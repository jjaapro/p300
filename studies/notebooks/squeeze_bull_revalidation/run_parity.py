#!/usr/bin/env python3
"""Step 2 — parity check P1..P4 and the data-migration caveats (README.md).

Reproduces the June-2026 numbers with the frozen rule functions before any
OOS number is looked at. Writes results/parity.json and results/parity_*.csv.
Read-only against prod.db (and, for one anchor, the 2026-07-22 backup).

  P1  OI flush bull-gated, phase3a window (fires <= 2026-02-06 17:30)      n=104, +0.280, MAR 2.18
  P2  OI flush on the table as of the ablation (rows <= 2026-06-05 19:01)   bull n=112, +0.285, MAR 2.05; pool n=416
      P2b -3% threshold at the phase-2 cut (17:04)                          n=221, bear/flat/bull 49/106/66
      P2c July-22 backup == today on the June span                          (no other row changes since June)
  P3  OI flush bull-gated, 2022-01-30 -> 2026-04-13 (caller's window)       n=104±3, +0.28±0.05
  P4  funding+CVD, 2020-01-01 -> 2026-04-13, June code path                 the 21-fire ledger

"Table as of June" = today's rows cut at the run time MINUS the 18 hourly
OI rows 2026-06-02 13:00 -> 2026-06-03 06:00 that the 2026-06-19 Binance
backfill added (see squeeze_bull_lib.june_backfill_rows; the June phase-1
JSON recorded 38,080 joined rows through 2026-06-05 15:00, today's tables
give 38,098). Numbers on today's table are reported alongside so the effect
of the migration on the June numbers is quantified per fire.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from squeeze_bull_lib import (  # noqa: E402
    ROOT, RESULTS, JUNE_REF, JUNE_FCD_LEDGER, JUNE_JSON_DIR, JUNE_OI_TABLE_END, JUNE_P3_END,
    JUNE_FCD_LAST_FIRE, JUNE_FCD_START, JUNE_FCD_END, FUNDING_CUTOVER,
    OOS_START, OI_SEAM_NOTE, FLUSH_THRESHOLD, PRICE_DIR_THRESHOLD, COOLDOWN_HOURS,
    OI_COST_BP, FCD_COST_BP, WINNING, OI_BACKFILL_REACH_START, OI_SEAM_FIRST_SNAPSHOT,
    ro_connect, to_ts, load_oi_frame, june_backfill_rows, oi_ledger, oi_stats, june_metrics,
    load_15m, load_funding, funding_8h_grid, fcd_ledger, load_json, dump_json,
)
import studies.notebooks.oi_flush.phase2_backtest as _p2mod  # noqa: E402  (for the -3% anchor)

JUNE_PHASE2_CUT = pd.Timestamp('2026-06-05 17:04:40', tz='UTC')   # phase2 JSON generated_at
JUNE_PHASE1_NBARS = 38080                                          # phase1 JSON n_bars (rows <= 2026-06-05 15:00)
JUNE_PHASE1_END = pd.Timestamp('2026-06-05 15:00:00', tz='UTC')
BACKUP_0722 = ROOT / 'data' / 'backups' / 'prod-20260722.db'
JUNE_REF_P2B = {'n': 221, 'bear': 49, 'flat': 106, 'bull': 66, 'mean_R': 0.005, 'maxDD': -16.49,
                'IS_n': 181, 'OOS_n': 40, 'OOS_meanR': 0.069, 'bull_mean_R': 0.331, 'bull_MAR': 1.51}


# ─── Data-migration caveats ─────────────────────────────────────────────────

def data_quality() -> dict:
    con = ro_connect()
    oi = pd.read_sql('SELECT timestamp, oi_open, oi_high, oi_low, oi_close '
                     'FROM cd_open_interest ORDER BY timestamp', con)
    fr = pd.read_sql('SELECT timestamp, fr_close FROM cd_funding_rate ORDER BY timestamp', con)
    px = pd.read_sql('SELECT timestamp FROM cd_futures_ohlcv ORDER BY timestamp', con)
    m15 = pd.read_sql('SELECT timestamp FROM cd_futures_15m ORDER BY timestamp', con)
    con.close()
    for d in (oi, fr, px, m15):
        d['ts'] = to_ts(d['timestamp'])
    out: dict = {}

    # OI: seam between CoinDesk OHLC rows and Binance point snapshots
    flat = ((oi['oi_open'] == oi['oi_close']) & (oi['oi_high'] == oi['oi_close'])
            & (oi['oi_low'] == oi['oi_close']))
    last_ohlc = int(np.flatnonzero(~flat.values).max())
    seam_i = last_ohlc + 1
    chg = oi['oi_close'].pct_change()
    y2026 = oi['ts'] >= pd.Timestamp('2026-01-01', tz='UTC')
    stale = flat & (oi['oi_close'] == oi['oi_close'].shift(1))
    seam = {
        'note': OI_SEAM_NOTE,
        'last_ohlc_row_utc': str(oi['ts'].iloc[last_ohlc]),
        'first_snapshot_row_utc': str(oi['ts'].iloc[seam_i]) if seam_i < len(oi) else None,
        'snapshot_rows_after_seam': int(len(oi) - seam_i),
        'all_rows_after_seam_are_snapshots': bool(flat.iloc[seam_i:].all()) if seam_i < len(oi) else None,
        'seam_1h_change_pct': round(float(chg.iloc[seam_i]) * 100, 4) if seam_i < len(oi) else None,
        'median_abs_1h_change_pct_2026': round(float(chg[y2026].abs().median()) * 100, 4),
        'p99_abs_1h_change_pct_2026': round(float(chg[y2026].abs().quantile(0.99)) * 100, 4),
        'snapshot_rows_before_seam_total': int((flat & (oi['ts'] < OI_SEAM_FIRST_SNAPSHOT)).sum()),
        'snapshot_rows_before_seam_stale_coindesk': int((stale & (oi['ts'] < OI_BACKFILL_REACH_START)).sum()),
        'snapshot_rows_in_backfill_reach': [str(t) for t in oi.loc[flat & (oi['ts'] >= OI_BACKFILL_REACH_START)
                                                                  & (oi['ts'] < OI_SEAM_FIRST_SNAPSHOT), 'ts']],
    }
    out['oi_seam'] = seam

    def continuity(ts: pd.Series, start: pd.Timestamp, freq: str) -> dict:
        s = ts[ts >= start]
        if s.empty:
            return {'rows': 0}
        grid = pd.date_range(s.min(), s.max(), freq=freq, tz='UTC')
        present = pd.DatetimeIndex(s)
        missing = grid.difference(present)
        off_grid = present.difference(grid)
        gaps = []
        if len(missing):
            m = missing.to_series()
            step = pd.Timedelta(freq)
            run_start = m.iloc[0]
            prev = m.iloc[0]
            for t in m.iloc[1:]:
                if t - prev != step:
                    gaps.append((str(run_start), str(prev)))
                    run_start = t
                prev = t
            gaps.append((str(run_start), str(prev)))
        return {'rows': int(len(s)), 'first': str(s.min()), 'last': str(s.max()),
                'expected_on_grid': int(len(grid)), 'missing': int(len(missing)),
                'off_grid_rows': int(len(off_grid)), 'gap_runs': gaps[:25],
                'duplicates': int(present.duplicated().sum())}

    out['oi_continuity_oos'] = continuity(oi['ts'], OOS_START, '1h')
    out['oi_continuity_full'] = continuity(oi['ts'], oi['ts'].min(), '1h')
    out['oi_nan_close'] = int(oi['oi_close'].isna().sum())
    out['px_hourly_continuity_oos'] = continuity(px['ts'], OOS_START, '1h')
    out['px_hourly_continuity_since_2022'] = continuity(px['ts'], pd.Timestamp('2022-01-30', tz='UTC'), '1h')
    out['px_15m_continuity_oos'] = continuity(m15['ts'], OOS_START, '15min')

    # funding cadence
    pre = fr[fr['ts'] < FUNDING_CUTOVER]
    post = fr[fr['ts'] >= FUNDING_CUTOVER]
    pre30 = pre[pre['ts'] >= FUNDING_CUTOVER - pd.Timedelta(days=30)]
    post30 = post[post['ts'] < FUNDING_CUTOVER + pd.Timedelta(days=30)]
    hours_post = post['ts'].dt.hour.value_counts().sort_index()
    on_grid_post = post['ts'].dt.hour.isin([0, 8, 16]) & (post['ts'].dt.minute == 0)
    on_hour_pre = (pre['ts'].dt.minute == 0) & (pre['ts'].dt.second == 0)
    out['funding_cadence'] = {
        'cutover_utc': str(FUNDING_CUTOVER),
        'rows_pre': int(len(pre)), 'rows_post': int(len(post)),
        'rows_per_day_last30d_pre': round(len(pre30) / 30, 2),
        'rows_per_day_first30d_post': round(len(post30) / 30, 2),
        'post_hour_of_day_counts': {int(k): int(v) for k, v in hours_post.items()},
        'post_rows_off_8h_grid': int((~on_grid_post).sum()),
        'pre_rows_off_the_hour': int((~on_hour_pre).sum()),
        'pre_rows_on_8h_grid': int(((pre['ts'].dt.hour.isin([0, 8, 16])) & (pre['ts'].dt.minute == 0)).sum()),
        'pre_last30d_mean_abs': float(pre30['fr_close'].abs().mean()),
        'post_first30d_mean_abs': float(post30['fr_close'].abs().mean()),
        'pre_last30d_std': float(pre30['fr_close'].std()),
        'post_first30d_std': float(post30['fr_close'].std()),
        'last_row_utc': str(fr['ts'].max()),
    }
    return out


def backup_equality() -> dict:
    """P2c: are today's hourly rows on the June span identical to the 2026-07-22 backup?"""
    if not BACKUP_0722.exists():
        return {'available': False}
    start_s = int(pd.Timestamp('2022-01-30 06:00', tz='UTC').timestamp())
    end_s = int(JUNE_OI_TABLE_END.timestamp())
    res = {'available': True, 'backup': str(BACKUP_0722)}
    for tbl, col in (('cd_open_interest', 'oi_close'), ('cd_futures_ohlcv', 'close')):
        frames = {}
        for name, p in (('today', ro_connect), ('backup', None)):
            con = p() if p else sqlite3.connect(f'file:{BACKUP_0722.as_posix()}?mode=ro', uri=True)
            d = pd.read_sql(f'SELECT timestamp, {col} FROM {tbl} WHERE timestamp >= ? AND timestamp <= ? '
                            'ORDER BY timestamp', con, params=(start_s, end_s))
            con.close()
            frames[name] = d.set_index('timestamp')[col]
        a, b = frames['today'], frames['backup']
        common = a.index.intersection(b.index)
        res[tbl] = {'rows_today': int(len(a)), 'rows_backup': int(len(b)),
                    'only_today': int(len(a.index.difference(b.index))),
                    'only_backup': int(len(b.index.difference(a.index))),
                    'max_abs_value_diff_common': float((a.loc[common] - b.loc[common]).abs().max()) if len(common) else None}
    res['identical'] = all(res[t]['only_today'] == 0 and res[t]['only_backup'] == 0
                           and (res[t]['max_abs_value_diff_common'] or 0.0) == 0.0
                           for t in ('cd_open_interest', 'cd_futures_ohlcv'))
    return res


# ─── Parity helpers ─────────────────────────────────────────────────────────

def compare(got: dict, ref: dict, keys_tol: dict) -> dict:
    """keys_tol: key -> absolute tolerance (0 = exact)."""
    rows = {}
    ok = True
    for k, tol in keys_tol.items():
        g = got.get(k)
        r = ref.get(k)
        if g is None or r is None:
            rows[k] = {'got': g, 'ref': r, 'pass': False}
            ok = False
            continue
        p = abs(float(g) - float(r)) <= tol
        rows[k] = {'got': g, 'ref': r, 'tol': tol, 'pass': bool(p)}
        ok = ok and p
    return {'pass': ok, 'fields': rows}


def fire_diff(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    sa = a.set_index('ts')['r_outcome']
    sb = b.set_index('ts')['r_outcome']
    common = sa.index.intersection(sb.index)
    return {'n_a': int(len(sa)), 'n_b': int(len(sb)), 'common': int(len(common)),
            'only_a': [str(t) for t in sa.index.difference(sb.index)],
            'only_b': [str(t) for t in sb.index.difference(sa.index)],
            'max_abs_dR_common': float((sa.loc[common] - sb.loc[common]).abs().max()) if len(common) else None}


def main() -> None:
    print('=' * 100)
    print('S-SqueezeBull re-validation — step 2: parity + data-migration caveats')
    print('=' * 100)
    print(f'frozen: FLUSH_THRESHOLD={FLUSH_THRESHOLD} PRICE_DIR={PRICE_DIR_THRESHOLD} '
          f'cooldown={COOLDOWN_HOURS} rows, cost={OI_COST_BP}bp | fCVD WINNING={WINNING}, cost={FCD_COST_BP}bp')

    out: dict = {'data_quality': data_quality()}
    dq = out['data_quality']
    print('\n--- data-migration caveats')
    s = dq['oi_seam']
    print(f"  OI seam: last OHLC row {s['last_ohlc_row_utc']}, first snapshot row {s['first_snapshot_row_utc']}, "
          f"seam 1h change {s['seam_1h_change_pct']}% (median |1h| 2026 {s['median_abs_1h_change_pct_2026']}%, "
          f"p99 {s['p99_abs_1h_change_pct_2026']}%)")
    print(f"  OI snapshot rows before the seam: {s['snapshot_rows_before_seam_total']} "
          f"({s['snapshot_rows_before_seam_stale_coindesk']} stale-CoinDesk repeats in 2022/2024; "
          f"{len(s['snapshot_rows_in_backfill_reach'])} Binance backfill rows "
          f"{s['snapshot_rows_in_backfill_reach'][0] if s['snapshot_rows_in_backfill_reach'] else ''} -> "
          f"{s['snapshot_rows_in_backfill_reach'][-1] if s['snapshot_rows_in_backfill_reach'] else ''})")
    print(f"  OI hourly continuity in OOS: rows={dq['oi_continuity_oos']['rows']} missing={dq['oi_continuity_oos']['missing']} "
          f"off-grid={dq['oi_continuity_oos']['off_grid_rows']} dups={dq['oi_continuity_oos']['duplicates']}; "
          f"full table missing hours={dq['oi_continuity_full']['missing']}")
    print(f"  px hourly continuity: OOS missing={dq['px_hourly_continuity_oos']['missing']}, since 2022 missing="
          f"{dq['px_hourly_continuity_since_2022']['missing']}; 15m OOS missing={dq['px_15m_continuity_oos']['missing']}")
    fc = dq['funding_cadence']
    print(f"  funding: rows/day last 30d pre-cutover {fc['rows_per_day_last30d_pre']}, first 30d post "
          f"{fc['rows_per_day_first30d_post']}; post rows off the 8h grid {fc['post_rows_off_8h_grid']}; "
          f"post hour counts {fc['post_hour_of_day_counts']}")
    print(f"  funding level: mean|fr| pre30 {fc['pre_last30d_mean_abs']:.3e} post30 {fc['post_first30d_mean_abs']:.3e}; "
          f"std pre30 {fc['pre_last30d_std']:.3e} post30 {fc['post_first30d_std']:.3e}")

    # ── the June table: today's rows cut at the run time minus the backfilled hours ──
    fills = june_backfill_rows()
    df_june = load_oi_frame(table_end=JUNE_OI_TABLE_END, drop_rows=fills)
    df_today_cut = load_oi_frame(table_end=JUNE_OI_TABLE_END)
    df_now = load_oi_frame()
    n_p1 = int((df_june.index <= JUNE_PHASE1_END).sum())
    out['june_table_reconstruction'] = {
        'backfill_rows_removed': int(len(fills)),
        'backfill_rows': [str(t) for t in fills],
        'rows_through_2026_06_05_15h_reconstructed': n_p1,
        'rows_through_2026_06_05_15h_today': int((df_today_cut.index <= JUNE_PHASE1_END).sum()),
        'june_phase1_n_bars': JUNE_PHASE1_NBARS,
        'row_count_matches_phase1': bool(n_p1 == JUNE_PHASE1_NBARS),
        'rows_june_cut': int(len(df_june)), 'rows_today_cut': int(len(df_today_cut)), 'rows_today_full': int(len(df_now)),
        'today_last_row': str(df_now.index.max()),
    }
    print(f"\n--- June table reconstruction: removed {len(fills)} backfilled OI hours "
          f"({fills.min() if len(fills) else '-'} -> {fills.max() if len(fills) else '-'}); rows through 2026-06-05 15:00: "
          f"{n_p1} (June phase-1 n_bars {JUNE_PHASE1_NBARS}, today {out['june_table_reconstruction']['rows_through_2026_06_05_15h_today']}) "
          f"-> matches={n_p1 == JUNE_PHASE1_NBARS}")
    out['P2c_backup_equality'] = backup_equality()
    be = out['P2c_backup_equality']
    print(f"  P2c July-22 backup identical to today on the June span: {be.get('identical')} "
          f"({ {k: v for k, v in be.items() if k.startswith('cd_')} })")

    led_june = oi_ledger(df_june)
    led_today_cut = oi_ledger(df_today_cut)
    led_now = oi_ledger(df_now)
    led_june.to_csv(RESULTS / 'parity_oi_flush_ledger_june_table.csv', index=False)
    led_now.to_csv(RESULTS / 'parity_oi_flush_ledger_today_table.csv', index=False)

    # P2 — ablation (stats() from June, IS_END 2024-12-31) on the reconstructed June table
    p2_pool = oi_stats(led_june, 'fixed_020_pool')
    p2_bull = oi_stats(led_june[led_june['regime'] == 'bull_30d'], 'fixed_020_bull')
    t_pool = oi_stats(led_today_cut, 'fixed_020_pool_today')
    t_bull = oi_stats(led_today_cut[led_today_cut['regime'] == 'bull_30d'], 'fixed_020_bull_today')
    d_all = fire_diff(led_today_cut, led_june)
    d_bull = fire_diff(led_today_cut[led_today_cut['regime'] == 'bull_30d'], led_june[led_june['regime'] == 'bull_30d'])
    out['P2'] = {
        'got_pool': p2_pool, 'got_bull': p2_bull,
        'today_table_pool': t_pool, 'today_table_bull': t_bull,
        'ref_bull': JUNE_REF['P2_ablation_fixed_020_bull'], 'ref_pool': JUNE_REF['P2_ablation_fixed_020_pool'],
        'check_bull': compare(p2_bull, JUNE_REF['P2_ablation_fixed_020_bull'],
                              {'n': 0, 'mean_R': 0.005, 'MAR': 0.02, 'maxDD': 0.01,
                               'OOS_n': 0, 'OOS_meanR': 0.005, 'cum_R': 0.02, 'WR': 0.002}),
        'check_pool': compare(p2_pool, JUNE_REF['P2_ablation_fixed_020_pool'],
                              {'n': 0, 'mean_R': 0.005, 'OOS_n': 0, 'OOS_meanR': 0.005, 'maxDD': 0.01}),
        'migration_delta_today_vs_june': {'pooled': d_all, 'bull_gated': d_bull},
    }
    out['P2']['pass'] = out['P2']['check_bull']['pass'] and out['P2']['check_pool']['pass']
    r = JUNE_REF['P2_ablation_fixed_020_bull']
    print(f"\n--- P2 ablation table (rows <= {JUNE_OI_TABLE_END}, reconstructed June rows):")
    print(f"  bull  got n={p2_bull['n']} meanR={p2_bull['mean_R']:+.3f} MAR={p2_bull['MAR']:.2f} maxDD={p2_bull['maxDD']:+.2f} "
          f"annR={p2_bull['annual_R']:+.1f} OOS(2025->)={p2_bull['OOS_meanR']:+.3f}({p2_bull['OOS_n']}) cumR={p2_bull['cum_R']:+.2f} WR={p2_bull['WR']:.3f}")
    print(f"  bull  ref n={r['n']} meanR={r['mean_R']:+.3f} MAR={r['MAR']:.2f} maxDD={r['maxDD']:+.2f} annR={r['annual_R']:+.1f} "
          f"OOS={r['OOS_meanR']:+.3f}({r['OOS_n']}) cumR={r['cum_R']:+.2f} WR={r['WR']:.3f}")
    rp = JUNE_REF['P2_ablation_fixed_020_pool']
    print(f"  pool  got n={p2_pool['n']} meanR={p2_pool['mean_R']:+.3f} maxDD={p2_pool['maxDD']:+.2f} OOS={p2_pool['OOS_meanR']:+.3f}({p2_pool['OOS_n']})"
          f"   ref n={rp['n']} meanR={rp['mean_R']:+.3f} maxDD={rp['maxDD']:+.2f} OOS={rp['OOS_meanR']:+.3f}({rp['OOS_n']})")
    print(f"  today's table (with the 18 backfilled hours): pool n={t_pool['n']} meanR={t_pool['mean_R']:+.3f} OOS={t_pool['OOS_meanR']:+.3f}({t_pool['OOS_n']}); "
          f"bull n={t_bull['n']} meanR={t_bull['mean_R']:+.3f} MAR={t_bull['MAR']:.2f}")
    print(f"  migration delta (today vs June rows): pooled fires only-today={d_all['only_a']} only-June={d_all['only_b']} "
          f"max|dR| common={d_all['max_abs_dR_common']}; bull-gated only-today={d_bull['only_a']} only-June={d_bull['only_b']} "
          f"max|dR|={d_bull['max_abs_dR_common']}")
    print(f"  P2 pass: {out['P2']['pass']}")

    # P2b — the -3% phase-2 anchor at the phase-2 cut (module constant patched in-process, restored after)
    df_p2b = load_oi_frame(table_end=JUNE_PHASE2_CUT, drop_rows=fills)
    df_p2b_today = load_oi_frame(table_end=JUNE_PHASE2_CUT)
    _p2mod.FLUSH_THRESHOLD = -0.03
    try:
        led3 = oi_ledger(df_p2b)
        led3_today = oi_ledger(df_p2b_today)
    finally:
        _p2mod.FLUSH_THRESHOLD = FLUSH_THRESHOLD
    s3 = oi_stats(led3, 'fixed_030_pool')
    s3b = oi_stats(led3[led3['regime'] == 'bull_30d'], 'fixed_030_bull')
    got3 = {'n': s3['n'], 'bear': int((led3['regime'] == 'bear_30d').sum()), 'flat': int((led3['regime'] == 'flat_30d').sum()),
            'bull': int((led3['regime'] == 'bull_30d').sum()), 'mean_R': s3['mean_R'], 'maxDD': s3['maxDD'],
            'IS_n': s3['IS_n'], 'OOS_n': s3['OOS_n'], 'OOS_meanR': s3['OOS_meanR'],
            'bull_mean_R': s3b['mean_R'], 'bull_MAR': s3b['MAR']}
    out['P2b'] = {'got': got3, 'ref': JUNE_REF_P2B,
                  'check': compare(got3, JUNE_REF_P2B, {'n': 0, 'bear': 0, 'flat': 0, 'bull': 0, 'mean_R': 0.005,
                                                        'maxDD': 0.01, 'IS_n': 0, 'OOS_n': 0, 'OOS_meanR': 0.005,
                                                        'bull_mean_R': 0.005, 'bull_MAR': 0.02}),
                  'today_table_n': int(len(led3_today)),
                  'today_table_same_fires': bool(set(led3_today['ts']) == set(led3['ts']))}
    out['P2b']['pass'] = out['P2b']['check']['pass']
    print(f"\n--- P2b -3% anchor at the phase-2 cut ({JUNE_PHASE2_CUT}): got {got3} | ref {JUNE_REF_P2B} "
          f"| pass={out['P2b']['pass']} | today's table gives the same {len(led3_today)} fires: {out['P2b']['today_table_same_fires']}")

    # P1 — phase3a window (bull fires <= last June fCVD fire), compute_metrics verbatim
    p1_set = led_june[(led_june['regime'] == 'bull_30d') & (led_june['ts'] <= JUNE_FCD_LAST_FIRE)]
    p1 = june_metrics(p1_set, 'phase3a_bull')
    p1_now = june_metrics(led_now[(led_now['regime'] == 'bull_30d') & (led_now['ts'] <= JUNE_FCD_LAST_FIRE)],
                          'phase3a_bull_today_table')
    out['P1'] = {'got': p1, 'got_today_table': p1_now, 'ref': JUNE_REF['P1_phase3a_bull'],
                 'check': compare(p1, JUNE_REF['P1_phase3a_bull'],
                                  {'n': 0, 'meanR': 0.005, 'MAR': 0.02, 'annual_R': 0.05})}
    out['P1']['pass'] = out['P1']['check']['pass']
    print(f"\n--- P1 phase3a window (bull fires <= {JUNE_FCD_LAST_FIRE}):")
    print(f"  got n={p1['n']} meanR={p1['meanR']:+.3f} MAR={p1['MAR']:.2f} annR={p1['annual_R']:+.2f} maxDD={p1['maxDD']:+.2f}"
          f"   | ref n=104 meanR=+0.280 MAR=2.18 annR=+8.10   | today's table n={p1_now['n']} meanR={p1_now['meanR']:+.3f} MAR={p1_now['MAR']:.2f}")
    print(f"  P1 pass: {out['P1']['pass']}")

    # P3 — caller's window on today's table
    p3_set = led_now[(led_now['regime'] == 'bull_30d') & (led_now['ts'] <= JUNE_P3_END)]
    p3 = june_metrics(p3_set, 'caller_window_bull')
    gap = p3_set[p3_set['ts'] > JUNE_FCD_LAST_FIRE]
    ref3 = JUNE_REF['P3_caller']
    within = (abs(p3['n'] - ref3['n']) <= ref3['n_tol']) and (abs(p3['meanR'] - ref3['mean_R']) <= ref3['mean_R_tol'])
    p1_now_set = led_now[(led_now['regime'] == 'bull_30d') & (led_now['ts'] <= JUNE_FCD_LAST_FIRE)]
    explained = out['P1']['pass'] and (p3['n'] == len(p1_now_set) + len(gap)) and (len(p1_now_set) == p1['n'])
    out['P3'] = {
        'got': p3, 'ref': ref3, 'within_tolerance': bool(within),
        'fires_after_2026_02_06_in_window': gap[['ts', 'r_outcome', 'exit_kind', 'ret_30d']].assign(
            ts=lambda d: d['ts'].astype(str)).to_dict('records'),
        'excess_explained_by_gap_fires': bool(explained),
        'pass': bool(within or explained),
    }
    print(f"\n--- P3 caller window (bull fires <= {JUNE_P3_END}, today's table):")
    print(f"  got n={p3['n']} meanR={p3['meanR']:+.3f} MAR={p3['MAR']:.2f} (ref 104±3, +0.28±0.05) within={within}; "
          f"fires after 2026-02-06 17:30: {len(gap)}; P3 pass: {out['P3']['pass']}")
    for _, g in gap.iterrows():
        print(f"     gap fire {g['ts']}  R={g['r_outcome']:+.3f} {g['exit_kind']} ret30={g['ret_30d']:+.3f}")

    # P4 — funding+CVD June code path on the June window
    start_ts = int(pd.Timestamp(JUNE_FCD_START, tz='UTC').timestamp())
    end_ts = int(pd.Timestamp(JUNE_FCD_END, tz='UTC').timestamp())
    df15 = load_15m(start_ts, end_ts)
    fh = load_funding(start_ts, end_ts)
    led_fcd = fcd_ledger(df15, fh, 'A')
    led_fcd.to_csv(RESULTS / 'parity_fcd_ledger_june_window.csv', index=False)
    ref_led = pd.DataFrame(JUNE_FCD_LEDGER, columns=['ts', 'r_ref'])
    ref_led['ts'] = pd.to_datetime(ref_led['ts'], utc=True)
    jpath = JUNE_JSON_DIR / 'funding_cvd_phase2_robustness.json'
    json_note = 'June JSON not present; embedded copy used'
    if jpath.exists():
        j = load_json(jpath)
        jl = pd.DataFrame([(x['ts'], x['r_outcome']) for x in j['ledger']], columns=['ts', 'r_json'])
        jl['ts'] = pd.to_datetime(jl['ts'], utc=True)
        mm = ref_led.merge(jl, on='ts', how='outer')
        json_note = (f"June JSON present: {len(jl)} fires; embedded copy matches JSON: "
                     f"{bool(len(mm) == 21 and (mm['r_ref'] - mm['r_json']).abs().max() < 1e-9)}")
    got = led_fcd[['ts', 'r_outcome', 'exit_kind', 'funding_z', 'cvd_z']]
    m = ref_led.merge(got, on='ts', how='outer', indicator=True)
    both = m[m['_merge'] == 'both']
    max_dr = float((both['r_ref'] - both['r_outcome']).abs().max()) if len(both) else None
    p4_pass = bool(len(m) == 21 and (m['_merge'] == 'both').all() and max_dr is not None and max_dr <= 1e-6)
    out['P4'] = {
        'got_n': int(len(led_fcd)), 'got_meanR': float(led_fcd['r_outcome'].mean()) if len(led_fcd) else None,
        'ref_n': 21, 'ref_meanR': JUNE_REF['P4_fcd']['mean_R'],
        'matched': int(len(both)), 'max_abs_dR': max_dr,
        'only_in_ref': [str(t) for t in m[m['_merge'] == 'left_only']['ts']],
        'only_in_got': [str(t) for t in m[m['_merge'] == 'right_only']['ts']],
        'json_crosscheck': json_note,
        'window': [JUNE_FCD_START, JUNE_FCD_END], 'bars_15m': int(len(df15)), 'funding_rows': int(len(fh)),
        'pass': p4_pass,
    }
    print(f"\n--- P4 funding+CVD June path ({JUNE_FCD_START} -> {JUNE_FCD_END}, {len(df15)} bars, {len(fh)} funding rows):")
    print(f"  got n={len(led_fcd)} meanR={out['P4']['got_meanR']:+.4f} | ref n=21 meanR=+1.0898 | matched {len(both)}/21, "
          f"max|dR|={max_dr} | only_in_ref={out['P4']['only_in_ref']} only_in_got={out['P4']['only_in_got']}")
    print(f"  {json_note}")
    print(f"  P4 pass: {p4_pass}")

    # P4b (informational) — handling B on the same window
    led_fcd_b = fcd_ledger(df15, funding_8h_grid(fh), 'B')
    led_fcd_b.to_csv(RESULTS / 'parity_fcd_ledger_june_window_handlingB.csv', index=False)
    mb = ref_led.merge(led_fcd_b[['ts', 'r_outcome']], on='ts', how='outer', indicator=True)
    out['P4b_handling_B_informational'] = {
        'n': int(len(led_fcd_b)), 'meanR': float(led_fcd_b['r_outcome'].mean()) if len(led_fcd_b) else None,
        'same_ts_as_june': int((mb['_merge'] == 'both').sum()),
        'only_in_june': [str(t) for t in mb[mb['_merge'] == 'left_only']['ts']],
        'only_in_B': [str(t) for t in mb[mb['_merge'] == 'right_only']['ts']],
        'funding_rows_on_8h_grid': int(len(funding_8h_grid(fh))),
    }
    print(f"\n--- P4b (informational) handling B on the June window: n={len(led_fcd_b)} "
          f"meanR={out['P4b_handling_B_informational']['meanR']:+.3f}; same-ts as June {out['P4b_handling_B_informational']['same_ts_as_june']}/21")

    out['all_pass'] = bool(out['P1']['pass'] and out['P2']['pass'] and out['P2b']['pass']
                           and out['P3']['pass'] and out['P4']['pass'])
    out['env'] = {'pandas': pd.__version__, 'numpy': np.__version__, 'python': sys.version.split()[0]}
    dump_json(out, RESULTS / 'parity.json')
    print('\n' + '=' * 100)
    print(f"PARITY: P1={out['P1']['pass']} P2={out['P2']['pass']} P2b={out['P2b']['pass']} P3={out['P3']['pass']} "
          f"P4={out['P4']['pass']}  -> all_pass={out['all_pass']}")
    print(f"wrote {RESULTS / 'parity.json'}")


if __name__ == '__main__':
    main()

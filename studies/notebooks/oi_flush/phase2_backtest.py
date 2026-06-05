"""Phase 2 of the OI flush study: proper backtest with regime check.

Phase 1 found a 60.5% WR / +0.555% mean-return signal at the -3% OI drop
threshold (long-flush only — short-flush continues, not reverses).

Phase 2:
  1. Build full trade backtest with fixed stop/target/TIF
  2. Sweep stop_pct in {0.5, 1.0, 1.5, 2.0}, target_pct in {0.5, 1.0, 1.5,
     2.0, 3.0}, tif_hours in {12, 24, 48, 72}
  3. Per (stop, target, tif): compute n, mean R, WR, maxDD, MAR, annual R
  4. Identify best by MAR (with min-trade threshold)
  5. Regime split (bear / flat / bull via BTC ret_30d) on the best combo
  6. Bootstrap maxDD CI

Decision: if any combo has mean R > +0.2 AND MAR > 1.5 AND positive in
all three regimes AND OOS positive, proceed to sleeve design. Otherwise
report findings and stop.
"""
from __future__ import annotations

import itertools
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve()
while not (ROOT / 'data' / 'databases' / 'prod.db').exists():
    if ROOT == ROOT.parent:
        raise RuntimeError('locate prod.db')
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))
DB = ROOT / 'data' / 'databases' / 'prod.db'
OUT_DIR = ROOT / 'studies' / 'material' / 'chento' / 'validation'

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

FLUSH_THRESHOLD = -0.02           # -2% OI drop in 4h. Updated 2026-06-05
                                  # per threshold_ablation.py: -2% bull-gated
                                  # MAR 2.05 > -3% MAR 1.51 + 2× OOS samples.
                                  # See memory/feedback_normalize_vs_absolute_thresholds.md
PRICE_DIR_THRESHOLD = -0.005      # -0.5% price drop to qualify as long-flush
COOLDOWN_HOURS = 24
COST_BP = 18.0
IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')

STOPS_PCT = (0.005, 0.01, 0.015, 0.02)
TARGETS_PCT = (0.005, 0.01, 0.015, 0.02, 0.03)
TIFS_H = (12, 24, 48, 72)


def load_data() -> pd.DataFrame:
    con = sqlite3.connect(str(DB))
    oi = pd.read_sql("SELECT timestamp, oi_close FROM cd_open_interest "
                      "ORDER BY timestamp", con)
    px = pd.read_sql("SELECT timestamp, open, high, low, close "
                      "FROM cd_futures_ohlcv ORDER BY timestamp", con)
    con.close()
    oi['ts'] = pd.to_datetime(oi['timestamp'], unit='s', utc=True).dt.as_unit('ns')
    px['ts'] = pd.to_datetime(px['timestamp'], unit='s', utc=True).dt.as_unit('ns')
    df = px.set_index('ts').drop(columns='timestamp').join(
        oi.set_index('ts').drop(columns='timestamp'), how='inner')
    df['oi_chg_4h'] = df['oi_close'].pct_change(4)
    df['px_chg_4h'] = df['close'].pct_change(4)
    # BTC 30d return for regime
    daily = df['close'].resample('1D').last()
    df['ret_30d'] = daily.pct_change(30).reindex(df.index, method='ffill')
    return df


def identify_long_flush_events(df: pd.DataFrame) -> list[int]:
    mask = ((df['oi_chg_4h'] <= FLUSH_THRESHOLD)
            & (df['px_chg_4h'] <= PRICE_DIR_THRESHOLD))
    idxs = np.flatnonzero(mask.values)
    kept = []
    last = -10**9
    for i in idxs:
        if i - last < COOLDOWN_HOURS:
            continue
        kept.append(i)
        last = i
    return kept


def replay(highs, lows, closes, *,
            entry_idx: int, stop_pct: float, target_pct: float,
            tif_bars: int) -> dict | None:
    entry = closes[entry_idx]
    stop = entry * (1.0 - stop_pct)        # long-only: stop below entry
    target = entry * (1.0 + target_pct)
    risk = entry - stop
    if risk <= 0:
        return None
    cost_R = (COST_BP / 10000.0) * (entry / risk)

    n = len(highs)
    end = min(entry_idx + tif_bars + 1, n)
    last_close = entry
    r_out = None
    exit_kind = None
    for j in range(entry_idx + 1, end):
        bh = float(highs[j]); bl = float(lows[j]); bc = float(closes[j])
        last_close = bc
        if bl <= stop:
            r_out = (stop - entry) / risk - cost_R
            exit_kind = 'stop'; break
        if bh >= target:
            r_out = (target - entry) / risk - cost_R
            exit_kind = 'target'; break
    if r_out is None:
        r_out = (last_close - entry) / risk - cost_R
        exit_kind = 'tif'
    return {'r_outcome': r_out, 'exit_kind': exit_kind}


def stats(rep: pd.DataFrame, label: str) -> dict:
    if rep.empty:
        return {'label': label, 'n': 0}
    rep = rep.sort_values('ts').reset_index(drop=True)
    cum = rep['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    is_set = rep[rep['ts'] <= IS_END]
    oos_set = rep[rep['ts'] > IS_END]
    span_y = ((rep['ts'].max() - rep['ts'].min()).total_seconds()
                / (365.25 * 86400))
    annual_R = float(rep['r_outcome'].mean()) * len(rep) / max(span_y, 0.05)
    return {
        'label': label, 'n': int(len(rep)),
        'n_per_yr': round(len(rep) / max(span_y, 0.05), 1),
        'mean_R': round(float(rep['r_outcome'].mean()), 3),
        'WR': round(float((rep['r_outcome'] > 0).mean()), 3),
        'cum_R': round(float(cum[-1]), 2),
        'maxDD': round(float(dd.min()), 2),
        'annual_R': round(annual_R, 1),
        'MAR': round(annual_R / abs(float(dd.min())), 2)
                if dd.min() < 0 else 0,
        'IS_meanR': round(float(is_set['r_outcome'].mean()) if len(is_set) else 0, 3),
        'IS_n': int(len(is_set)),
        'OOS_meanR': round(float(oos_set['r_outcome'].mean()) if len(oos_set) else 0, 3),
        'OOS_n': int(len(oos_set)),
        'stops_pct': round(float((rep['exit_kind'] == 'stop').mean()), 2),
        'tgts_pct': round(float((rep['exit_kind'] == 'target').mean()), 2),
        'tif_pct': round(float((rep['exit_kind'] == 'tif').mean()), 2),
    }


def show(s):
    if s.get('n', 0) == 0:
        print(f'  {s["label"]:<32s} empty'); return
    print(f'  {s["label"]:<32s} n={s["n"]:>3d} '
          f'({s["n_per_yr"]:>4.1f}/yr)  meanR={s["mean_R"]:+.3f}  '
          f'WR={s["WR"]:.0%}  maxDD={s["maxDD"]:+5.2f}  '
          f'annR={s["annual_R"]:>+6.1f}  MAR={s["MAR"]:>5.2f}  '
          f'OOS={s["OOS_meanR"]:+.3f}({s["OOS_n"]})  '
          f'st/tg/tif={s["stops_pct"]:.0%}/{s["tgts_pct"]:.0%}/{s["tif_pct"]:.0%}')


def main():
    print('Loading data...')
    df = load_data()
    print(f'  {len(df)} hourly bars, span {df.index.min()} -> {df.index.max()}')

    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    ret30 = df['ret_30d'].values
    ts_arr = df.index

    print('Identifying long-flush events...')
    event_idxs = identify_long_flush_events(df)
    print(f'  {len(event_idxs)} long-flush events')

    # Build per-event base records
    base_records = []
    for i in event_idxs:
        regime = ('bear_30d' if ret30[i] < -0.10
                    else 'bull_30d' if ret30[i] > 0.10
                    else 'flat_30d')
        base_records.append({
            'ts': ts_arr[i], 'idx': i, 'regime': regime,
            'ret_30d': float(ret30[i]) if not np.isnan(ret30[i]) else None,
        })

    # ─── Sweep ────────────────────────────────────────────────────────
    print(f'\nSweeping {len(STOPS_PCT)*len(TARGETS_PCT)*len(TIFS_H)} combos...')
    all_combos = []
    for stop_pct, target_pct, tif_h in itertools.product(STOPS_PCT, TARGETS_PCT, TIFS_H):
        rows = []
        for base in base_records:
            r = replay(highs, lows, closes,
                        entry_idx=base['idx'],
                        stop_pct=stop_pct, target_pct=target_pct,
                        tif_bars=tif_h)
            if r is not None:
                rows.append({**base, **r})
        rep = pd.DataFrame(rows)
        s = stats(rep, f's{stop_pct*100:.1f}_t{target_pct*100:.1f}_tif{tif_h}h')
        s['stop_pct'] = stop_pct; s['target_pct'] = target_pct
        s['tif_h'] = tif_h
        all_combos.append(s)

    # ─── Top combos by MAR (n>=80, OOS positive) ───────────────────────
    print('\n' + '=' * 110)
    print('=== TOP COMBOS BY MAR (n>=80, OOS_meanR > 0) ===')
    print('=' * 110)
    cands = [c for c in all_combos
               if c.get('n', 0) >= 80
               and c.get('OOS_meanR', 0) > 0]
    cands.sort(key=lambda c: c['MAR'], reverse=True)
    print(f'\n  {len(cands)} candidates clear the gate')
    print(f'\n  Top 10 by MAR:')
    for s in cands[:10]:
        show(s)

    if not cands:
        print('\n  No combos clear the gate. Top 10 by mean_R:')
        all_combos.sort(key=lambda c: c.get('mean_R', -99), reverse=True)
        for s in all_combos[:10]:
            show(s)
        return

    best = cands[0]
    print(f'\n  Best by MAR: {best["label"]} '
          f'(stop={best["stop_pct"]*100:.1f}%, '
          f'target={best["target_pct"]*100:.1f}%, '
          f'tif={best["tif_h"]}h)')

    # ─── Re-run best combo to get per-trade ledger for regime analysis
    print('\n' + '=' * 110)
    print(f'=== BEST COMBO: regime breakdown ===')
    print('=' * 110)
    best_rows = []
    for base in base_records:
        r = replay(highs, lows, closes,
                    entry_idx=base['idx'],
                    stop_pct=best['stop_pct'],
                    target_pct=best['target_pct'],
                    tif_bars=best['tif_h'])
        if r is not None:
            best_rows.append({**base, **r})
    rep_best = pd.DataFrame(best_rows)

    print(f'\n  By regime:')
    for regime in ('bear_30d', 'flat_30d', 'bull_30d'):
        show(stats(rep_best[rep_best['regime'] == regime], regime))

    # ─── Bootstrap maxDD + meanR CI ─────────────────────────────────────
    print('\n' + '=' * 110)
    print('=== BOOTSTRAP CI (1000 resamples on best combo) ===')
    print('=' * 110)
    r = rep_best['r_outcome'].values
    rng = np.random.default_rng(42)
    means = []; maxDDs = []
    for _ in range(1000):
        s = rng.choice(r, len(r), replace=True)
        c = np.cumsum(s); p = np.maximum.accumulate(c)
        means.append(s.mean()); maxDDs.append((c-p).min())
    print(f'\n  meanR  p05={np.percentile(means, 5):+.3f}  '
           f'p50={np.percentile(means, 50):+.3f}  '
           f'p95={np.percentile(means, 95):+.3f}')
    print(f'  maxDD  p05={np.percentile(maxDDs, 5):+.2f}  '
           f'p95={np.percentile(maxDDs, 95):+.2f}')
    print(f'  P(meanR > 0):    {(np.array(means) > 0).mean():.1%}')
    print(f'  P(meanR > +0.1): {(np.array(means) > 0.1).mean():.1%}')
    print(f'  P(meanR > +0.2): {(np.array(means) > 0.2).mean():.1%}')

    # ─── Verdict ──────────────────────────────────────────────────────
    print('\n' + '=' * 110)
    print('=== VERDICT ===')
    print('=' * 110)
    print(f'\n  Best combo: {best["label"]}')
    print(f'    mean R: {best["mean_R"]:+.3f}')
    print(f'    WR: {best["WR"]:.0%}')
    print(f'    annual: {best["annual_R"]:+.1f}R/yr')
    print(f'    MAR: {best["MAR"]:.2f}')
    print(f'    OOS R: {best["OOS_meanR"]:+.3f} (n={best["OOS_n"]})')
    bear = stats(rep_best[rep_best['regime'] == 'bear_30d'], 'bear')
    flat = stats(rep_best[rep_best['regime'] == 'flat_30d'], 'flat')
    bull = stats(rep_best[rep_best['regime'] == 'bull_30d'], 'bull')
    all_regimes_positive = (bear.get('mean_R', 0) > 0
                              and flat.get('mean_R', 0) > 0
                              and bull.get('mean_R', 0) > 0)
    if (best['mean_R'] > 0.2 and best['MAR'] > 1.5
            and best['OOS_meanR'] > 0 and all_regimes_positive):
        print(f'\n  PASS: signal is robust across regimes. Proceed to sleeve design.')
    elif best['mean_R'] > 0 and best['OOS_meanR'] > 0:
        print(f'\n  MARGINAL: signal real but modest. Consider regime gating, '
               f'higher OI threshold, or longer TIF.')
    else:
        print(f'\n  FAIL: signal does not survive proper backtest.')

    out_path = OUT_DIR / 'oi_flush_phase2_backtest.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'n_events': len(event_idxs),
            'best_combo': best,
            'top_10_by_MAR': cands[:10],
            'regime_breakdown': {
                'bear_30d': bear, 'flat_30d': flat, 'bull_30d': bull,
            },
            'bootstrap': {
                'meanR_p05': float(np.percentile(means, 5)),
                'meanR_p50': float(np.percentile(means, 50)),
                'meanR_p95': float(np.percentile(means, 95)),
                'maxDD_p05': float(np.percentile(maxDDs, 5)),
                'maxDD_p95': float(np.percentile(maxDDs, 95)),
                'pos_frac': float((np.array(means) > 0).mean()),
            },
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

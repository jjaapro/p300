#!/usr/bin/env python3
"""Step 3 — the OOS window (2026-04-14 -> last full UTC day) and the
informational tables (README.md). Frozen rules; read-only against prod.db.

Writes results/full_oi_flush_ledger.csv, results/oos_oi_flush_ledger.csv,
results/full_funding_cvd_ledger.csv, results/oos_funding_cvd_ledger.csv,
results/oos_regime.json, results/per_year.csv, results/etf_split.csv,
results/oos.json.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from squeeze_bull_lib import (  # noqa: E402
    RESULTS, OOS_START, FUNDING_CUTOVER, CUTOVER_MIXED_END, ETF_SPLIT, FULL_SAMPLE_START,
    JUNE_OI_TABLE_END, JUNE_FCD_START, BULL_THRESHOLD, OI_TIF_H, FCD_TIF_BARS,
    load_px_hourly, last_full_day, load_oi_frame, oi_ledger, load_15m, load_funding,
    funding_8h_grid, fcd_ledger, regime_of, classify, basic, dump_json,
)


def regime_distribution(px: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                        col: str = 'ret_30d') -> dict:
    w = px.loc[(px.index >= start) & (px.index <= end), col].dropna()
    if w.empty:
        return {'hours': 0}
    cls = w.map(classify)
    share = cls.value_counts(normalize=True)
    bull = w[w > BULL_THRESHOLD]
    # bull spans (consecutive days with any bull hour)
    spans = []
    if len(bull):
        days = pd.DatetimeIndex(sorted(set(bull.index.floor('D'))))
        s0 = days[0]
        prev = days[0]
        for d in days[1:]:
            if (d - prev).days > 1:
                spans.append((str(s0.date()), str(prev.date())))
                s0 = d
            prev = d
        spans.append((str(s0.date()), str(prev.date())))
    return {
        'hours': int(len(w)),
        'share_bull_gt_10pct': round(float(share.get('bull_30d', 0.0)), 4),
        'share_bear_lt_m10pct': round(float(share.get('bear_30d', 0.0)), 4),
        'share_flat': round(float(share.get('flat_30d', 0.0)), 4),
        'bull_hours': int(len(bull)), 'bull_days': round(len(bull) / 24, 1),
        'ret_30d_min': round(float(w.min()), 4), 'ret_30d_max': round(float(w.max()), 4),
        'ret_30d_max_at': str(w.idxmax()), 'ret_30d_min_at': str(w.idxmin()),
        'ret_30d_median': round(float(w.median()), 4),
        'bull_spans': spans,
    }


def per_year(led_oi: pd.DataFrame, led_a: pd.DataFrame, led_b: pd.DataFrame) -> pd.DataFrame:
    years = sorted(set(led_oi['ts'].dt.year) | set(led_a['ts'].dt.year) | set(led_b['ts'].dt.year))
    rows = []
    for y in years:
        o = led_oi[led_oi['ts'].dt.year == y]
        ob = o[o['regime'] == 'bull_30d']
        a = led_a[led_a['ts'].dt.year == y]
        b = led_b[led_b['ts'].dt.year == y]

        def m(d):
            return (int(len(d)), round(float(d['r_outcome'].mean()), 3) if len(d) else None,
                    round(float(d['r_outcome'].sum()), 2) if len(d) else None,
                    round(float((d['r_outcome'] > 0).mean()), 2) if len(d) else None)
        mo, mob, ma, mb = m(o), m(ob), m(a), m(b)
        rows.append({'year': y,
                     'oi_pool_n': mo[0], 'oi_pool_meanR': mo[1], 'oi_pool_sumR': mo[2],
                     'oi_bull_n': mob[0], 'oi_bull_meanR': mob[1], 'oi_bull_sumR': mob[2], 'oi_bull_WR': mob[3],
                     'fcdA_n': ma[0], 'fcdA_meanR': ma[1], 'fcdA_sumR': ma[2],
                     'fcdB_n': mb[0], 'fcdB_meanR': mb[1], 'fcdB_sumR': mb[2]})
    return pd.DataFrame(rows)


def etf_split(led_oi: pd.DataFrame, led_a: pd.DataFrame, led_b: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, sel in (('pre_ETF (<2024-01-11)', lambda d: d[d['ts'] < ETF_SPLIT]),
                      ('post_ETF (>=2024-01-11)', lambda d: d[d['ts'] >= ETF_SPLIT])):
        o = sel(led_oi)
        ob = o[o['regime'] == 'bull_30d']
        for sleeve, d in (('OI_pooled', o), ('OI_bull_gated', ob),
                          ('fCVD_A', sel(led_a)), ('fCVD_B', sel(led_b))):
            s = basic(d)
            rows.append({'period': name, 'sleeve': sleeve, 'n': s['n'], 'mean_R': s['mean_R'],
                         'sum_R': s['sum_R'], 'WR': s['WR'], 'maxDD': s['maxDD']})
    return pd.DataFrame(rows)


def main() -> None:
    print('=' * 100)
    print('S-SqueezeBull re-validation — step 3: OOS window + informational tables')
    print('=' * 100)
    px = load_px_hourly()
    lfd = last_full_day(px)
    w_end = lfd + pd.Timedelta(hours=23, minutes=59, seconds=59)
    table_end = px.index.max()
    print(f'cd_futures_ohlcv last row {table_end}; last full UTC day {lfd.date()}; OOS = [{OOS_START}, {w_end}]')

    # ── Rule 1: OI flush (full table as stored) ──
    df = load_oi_frame()
    led = oi_ledger(df)
    led.to_csv(RESULTS / 'full_oi_flush_ledger.csv', index=False)
    oos = led[(led['ts'] >= OOS_START) & (led['ts'] <= w_end)].copy()
    oos.to_csv(RESULTS / 'oos_oi_flush_ledger.csv', index=False)
    res = oos[oos['resolved']]
    unres = oos[~oos['resolved']]
    bull = res[res['regime'] == 'bull_30d']
    bull_bo = res[res['regime_backonly'] == 'bull_30d']
    print(f'\n--- Rule 1 OI flush: full-sample fires {len(led)} ({df.index.min()} -> {df.index.max()}); '
          f'OOS fires {len(oos)} (resolved {len(res)}, unresolved {len(unres)})')
    oi_oos = {
        'window': [str(OOS_START), str(w_end)], 'last_full_day': str(lfd.date()),
        'fires_pooled': int(len(oos)), 'resolved_pooled': int(len(res)), 'unresolved': int(len(unres)),
        'bull_gated_resolved': basic(bull),
        'pooled_resolved': basic(res),
        'by_regime_resolved': {r: basic(res[res['regime'] == r]) for r in ('bear_30d', 'flat_30d', 'bull_30d')},
        'bull_gated_backonly_regime_resolved': basic(bull_bo),
        'bull_gated_fires': oos[oos['regime'] == 'bull_30d'][['ts', 'r_outcome', 'exit_kind', 'ret_30d', 'oi_chg_4h',
                                                             'px_chg_4h', 'window_span_h', 'resolved']]
            .assign(ts=lambda d: d['ts'].astype(str)).to_dict('records'),
        'unresolved_fires': unres[['ts', 'regime', 'r_outcome', 'exit_kind']]
            .assign(ts=lambda d: d['ts'].astype(str)).to_dict('records'),
        'fires_with_window_span_gt_4h': int((oos['window_span_h'] > 4.0).sum()),
        'fires_after_oi_seam_2026_06_10': int((oos['ts'] >= pd.Timestamp('2026-06-10 08:00', tz='UTC')).sum()),
    }
    print(f"  bull-gated resolved: {oi_oos['bull_gated_resolved']}")
    print(f"  pooled resolved:     {oi_oos['pooled_resolved']}")
    for r, s in oi_oos['by_regime_resolved'].items():
        print(f"    {r:<9s} n={s['n']:>3d} meanR={s['mean_R']} sumR={s['sum_R']} WR={s['WR']}")
    print(f"  bull-gated with backward-only regime: {oi_oos['bull_gated_backonly_regime_resolved']}")
    print(f"  fires with 4-row window > 4h: {oi_oos['fires_with_window_span_gt_4h']}; "
          f"fires after the OI seam: {oi_oos['fires_after_oi_seam_2026_06_10']}")
    for f in oi_oos['bull_gated_fires']:
        print(f"    bull fire {f['ts']}  R={f['r_outcome']:+.3f} {f['exit_kind']:<6s} ret30={f['ret_30d']:+.3f} "
              f"oi4h={f['oi_chg_4h']*100:+.2f}% px4h={f['px_chg_4h']*100:+.2f}% resolved={f['resolved']}")

    # ── Rule 2: funding + CVD (A as stored, B 8h-consistent), full history ──
    start_ts = int(pd.Timestamp(JUNE_FCD_START, tz='UTC').timestamp())
    end_ts = int(table_end.timestamp())
    df15 = load_15m(start_ts, end_ts)
    fh = load_funding(start_ts, end_ts)
    led_a = fcd_ledger(df15, fh, 'A')
    led_b = fcd_ledger(df15, funding_8h_grid(fh), 'B')
    for d in (led_a, led_b):
        d['ret_30d'] = regime_of(px, d['ts']).values
        d['regime'] = d['ret_30d'].map(classify)
    full_fcd = pd.concat([led_a, led_b], ignore_index=True)
    full_fcd.to_csv(RESULTS / 'full_funding_cvd_ledger.csv', index=False)
    oos_fcd = full_fcd[(full_fcd['ts'] >= OOS_START) & (full_fcd['ts'] <= w_end)].copy()
    oos_fcd.to_csv(RESULTS / 'oos_funding_cvd_ledger.csv', index=False)
    print(f'\n--- Rule 2 funding+CVD: 15m bars {len(df15)} ({df15.index.min()} -> {df15.index.max()}); '
          f'funding rows A={len(fh)} B={len(funding_8h_grid(fh))}')
    fcd_oos = {}
    for h, d in (('A', led_a), ('B', led_b)):
        o = d[(d['ts'] >= OOS_START) & (d['ts'] <= w_end)]
        pre = d[d['ts'] < FUNDING_CUTOVER]
        fcd_oos[h] = {
            'full_n': int(len(d)), 'full_meanR': round(float(d['r_outcome'].mean()), 4) if len(d) else None,
            'pre_cutover_n': int(len(pre)),
            'oos_fires': o[['ts', 'r_outcome', 'exit_kind', 'funding_z', 'cvd_z', 'ret_30d', 'regime',
                            'resolved', 'cutover_mixed']].assign(ts=lambda x: x['ts'].astype(str)).to_dict('records'),
            'oos_resolved': basic(o[o['resolved']]),
            'oos_n_cutover_mixed': int(o['cutover_mixed'].sum()),
            'fires_between_cutover_and_oos_start': d[(d['ts'] >= FUNDING_CUTOVER) & (d['ts'] < OOS_START)][['ts', 'r_outcome']]
                .assign(ts=lambda x: x['ts'].astype(str)).to_dict('records'),
        }
        print(f"  handling {h}: full n={len(d)} meanR={fcd_oos[h]['full_meanR']}; pre-cutover n={len(pre)}; "
              f"OOS fires {len(o)} (resolved {int(o['resolved'].sum())}, cutover-mixed {int(o['cutover_mixed'].sum())}): "
              f"{fcd_oos[h]['oos_resolved']}")
        for f in fcd_oos[h]['oos_fires']:
            print(f"    fire {f['ts']}  R={f['r_outcome']:+.3f} {f['exit_kind']:<6s} fz={f['funding_z']:+.2f} "
                  f"cz={f['cvd_z']:+.2f} ret30={f['ret_30d']:+.3f} resolved={f['resolved']} mixed={f['cutover_mixed']}")

    # ── OOS regime distribution ──
    reg = {
        'june_construction': regime_distribution(px, OOS_START, w_end, 'ret_30d'),
        'backward_only': regime_distribution(px, OOS_START, w_end, 'ret_30d_backonly'),
    }
    rj = reg['june_construction']
    reg['bull_gate_never_open'] = bool(rj.get('bull_hours', 0) == 0)
    dump_json(reg, RESULTS / 'oos_regime.json')
    print(f"\n--- OOS regime (BTC ret_30d, June construction): hours={rj['hours']} bull>{BULL_THRESHOLD:.0%} share="
          f"{rj.get('share_bull_gt_10pct')} ({rj.get('bull_days')} days) bear share={rj.get('share_bear_lt_m10pct')} "
          f"flat={rj.get('share_flat')}; ret_30d range [{rj.get('ret_30d_min')}, {rj.get('ret_30d_max')}] max at {rj.get('ret_30d_max_at')}")
    print(f"  bull spans: {rj.get('bull_spans')}")
    if reg['bull_gate_never_open']:
        print('  NOTE: BTC 30d return was never > +10% in the OOS window -> the bull gate makes n = 0 by construction.')

    # ── historical firing rates for the INCONCLUSIVE projection ──
    june_led = led[led['ts'] <= JUNE_OI_TABLE_END]
    june_bull = june_led[june_led['regime'] == 'bull_30d']
    june_span_y = (JUNE_OI_TABLE_END - df.index.min()).total_seconds() / (365.25 * 86400)
    june_bull_hours = int((df.loc[df.index <= JUNE_OI_TABLE_END, 'ret_30d'] > BULL_THRESHOLD).sum())
    oos_span_days = (w_end - OOS_START).total_seconds() / 86400
    rates = {
        'june_window': [str(df.index.min()), str(JUNE_OI_TABLE_END)],
        'june_span_years': round(june_span_y, 3),
        'june_bull_fires': int(len(june_bull)),
        'june_bull_fires_per_year': round(len(june_bull) / june_span_y, 2),
        'june_bull_days': round(june_bull_hours / 24, 1),
        'june_bull_fires_per_bull_day': round(len(june_bull) / max(june_bull_hours / 24, 1e-9), 4),
        'june_share_bull_hours': round(june_bull_hours / int((df.index <= JUNE_OI_TABLE_END).sum()), 4),
        'oos_span_days': round(oos_span_days, 1),
        'oos_bull_days': rj.get('bull_days', 0),
        'oos_bull_fires_resolved': int(len(bull)),
        'oos_pooled_fires_per_year': round(len(res) / (oos_span_days / 365.25), 2),
    }
    print(f"\n--- firing rates: June bull-gated {rates['june_bull_fires']} fires / {rates['june_span_years']}y = "
          f"{rates['june_bull_fires_per_year']}/yr; per bull day {rates['june_bull_fires_per_bull_day']} "
          f"({rates['june_bull_days']} bull days, {rates['june_share_bull_hours']:.0%} of hours); "
          f"OOS: {rates['oos_span_days']} days, {rates['oos_bull_days']} bull days, {rates['oos_bull_fires_resolved']} bull fires, "
          f"pooled {rates['oos_pooled_fires_per_year']}/yr")

    # ── per-year + ETF split (full sample; OI from 2022-01-30, fCVD from 2020) ──
    py = per_year(led, led_a, led_b)
    py.to_csv(RESULTS / 'per_year.csv', index=False)
    es = etf_split(led[led['ts'] >= FULL_SAMPLE_START], led_a[led_a['ts'] >= FULL_SAMPLE_START],
                   led_b[led_b['ts'] >= FULL_SAMPLE_START])
    es.to_csv(RESULTS / 'etf_split.csv', index=False)
    pd.set_option('display.width', 220)
    print('\n--- per-year (full sample; OI pooled / OI bull-gated / fCVD A / fCVD B):')
    print(py.to_string(index=False))
    print('\n--- pre-ETF vs post-ETF (2024-01-11), full sample from 2022-01-30:')
    print(es.to_string(index=False))

    out = {
        'oos_window': [str(OOS_START), str(w_end)], 'last_full_day': str(lfd.date()),
        'table_end_hourly': str(table_end), 'table_end_15m': str(df15.index.max()),
        'oi_flush': oi_oos, 'funding_cvd': fcd_oos, 'regime': reg, 'rates': rates,
        'unresolved_cutoffs': {'oi_flush_needs_bars_through': str(w_end + pd.Timedelta(hours=OI_TIF_H)),
                               'fcd_needs_bars_through': str(w_end + pd.Timedelta(minutes=15 * FCD_TIF_BARS))},
    }
    dump_json(out, RESULTS / 'oos.json')
    print(f"\nwrote {RESULTS / 'oos.json'} (+ ledgers, per_year.csv, etf_split.csv, oos_regime.json)")


if __name__ == '__main__':
    main()

"""OI flush threshold ablation: is -3% fixed cutoff overfitting?

Same question as the TRIPLE_V3 regime-threshold ablation, applied to the
OI flush sleeve's `FLUSH_THRESHOLD = -0.03`. The signal is `oi_chg_4h`
(4h % change in OI). Hypothesis: the absolute -3% might encode structural
liquidation-cascade significance, OR it might be lucky calibration of a
property that actually scales with regime.

Test setup (mirrors validation_regime_threshold_ablation.py architecture):
  - Hold Phase 2 best (stop=2%, target=3%, TIF=48h) constant
  - Vary the trigger threshold across 3 families:
      * fixed %       — {-2.0, -2.5, -3.0, -3.5, -4.0, -5.0}
      * rolling z     — oi_chg_4h vs trailing {30d, 60d, 90d} mean/std,
                        z < {-1.5, -2.0, -2.5, -3.0}
      * rolling pct   — oi_chg_4h vs trailing {30d, 60d, 90d} distribution,
                        bottom {5%, 10%, 15%, 20%}
  - Always require PRICE_DIR_THRESHOLD (price down ≤ -0.5%) — same as
    production. Just varying the OI gate.
  - Report POOLED and BULL_GATED (BTC ret_30d > +10%) separately, since
    the bull-gated subset is the actual proposed edge per [[oi-flush-findings]].

Decision rule: replace -3% fixed with a normalized variant ONLY IF the
normalized variant has bull-gated MAR ≥ fixed_30 MAR AND ≥3 different
lookbacks give consistent results (robustness across lookback windows).
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

from studies.notebooks.oi_flush.phase2_backtest import (
    load_data, replay, stats, show,
    PRICE_DIR_THRESHOLD, COOLDOWN_HOURS, IS_END,
)

# Phase 2 best combo
STOP_PCT = 0.02
TARGET_PCT = 0.03
TIF_H = 48
BULL_REGIME_THRESHOLD = 0.10   # BTC ret_30d > +10%


# ─── Threshold variants ────────────────────────────────────────────────────


def compute_rolling_stats(oi_chg_4h: pd.Series,
                           lookback_hours: int) -> pd.DataFrame:
    """Rolling mean/std/percentile-rank of oi_chg_4h."""
    min_periods = max(50, lookback_hours // 4)
    mu = oi_chg_4h.rolling(lookback_hours, min_periods=min_periods).mean()
    sigma = oi_chg_4h.rolling(lookback_hours, min_periods=min_periods).std()
    z = (oi_chg_4h - mu) / sigma

    def _pct_rank(x):
        if len(x) < min_periods:
            return np.nan
        return float((x <= x.iloc[-1]).sum()) / len(x)

    pct = oi_chg_4h.rolling(lookback_hours,
                              min_periods=min_periods).apply(_pct_rank,
                                                              raw=False)
    return pd.DataFrame({'oi_chg': oi_chg_4h, 'mu': mu, 'sigma': sigma,
                          'z': z, 'pct': pct})


def identify_events_for_variant(df: pd.DataFrame, *,
                                 variant: str, **kwargs) -> list[int]:
    """Identify long-flush events using the specified threshold variant.
    Always requires price drop (PRICE_DIR_THRESHOLD)."""
    if variant == 'fixed':
        oi_mask = df['oi_chg_4h'] <= kwargs['threshold']
    elif variant == 'z':
        oi_mask = df[f'z_{kwargs["lookback"]}'] <= kwargs['threshold']
    elif variant == 'pct':
        # Bottom-tail: pct rank <= threshold (e.g. <= 0.10 means bottom 10%)
        oi_mask = df[f'pct_{kwargs["lookback"]}'] <= kwargs['threshold']
    else:
        raise ValueError(f'Unknown variant: {variant}')
    price_mask = df['px_chg_4h'] <= PRICE_DIR_THRESHOLD
    mask = oi_mask & price_mask
    idxs = np.flatnonzero(mask.values)
    kept = []
    last = -10**9
    for i in idxs:
        if i - last < COOLDOWN_HOURS:
            continue
        kept.append(i)
        last = i
    return kept


def replay_and_stats(df: pd.DataFrame, event_idxs: list[int], *,
                      label: str) -> tuple[dict, dict]:
    """Replay all events with fixed stop/target/TIF; return pooled and
    bull-gated stats."""
    highs = df['high'].values; lows = df['low'].values
    closes = df['close'].values; ret30 = df['ret_30d'].values
    ts_arr = df.index

    rows = []
    for i in event_idxs:
        r = replay(highs, lows, closes,
                    entry_idx=i, stop_pct=STOP_PCT,
                    target_pct=TARGET_PCT, tif_bars=TIF_H)
        if r is None:
            continue
        regime = ('bear_30d' if ret30[i] < -0.10
                    else 'bull_30d' if ret30[i] > BULL_REGIME_THRESHOLD
                    else 'flat_30d')
        rows.append({'ts': ts_arr[i], 'regime': regime,
                       'ret_30d': float(ret30[i]) if not np.isnan(ret30[i]) else None,
                       **r})
    rep = pd.DataFrame(rows)

    s_pool = stats(rep, label) if not rep.empty else {'label': label, 'n': 0}
    s_bull = stats(rep[rep['regime'] == 'bull_30d'] if not rep.empty else rep,
                     f'{label}_bull') if not rep.empty else {'label': f'{label}_bull', 'n': 0}
    return s_pool, s_bull


# ─── Main ──────────────────────────────────────────────────────────────────


def main():
    print('Loading data...')
    df = load_data()
    print(f'  {len(df)} hourly bars, span {df.index.min()} -> {df.index.max()}')

    # Compute rolling stats for z and pct families at 30/60/90d lookbacks
    print('\nComputing rolling z/pct stats for oi_chg_4h (30/60/90d lookbacks)...')
    for lookback_days in (30, 60, 90):
        lb_h = lookback_days * 24
        s = compute_rolling_stats(df['oi_chg_4h'], lookback_hours=lb_h)
        df[f'z_{lookback_days}d'] = s['z']
        df[f'pct_{lookback_days}d'] = s['pct']
    print('  done.')

    # Variants
    variants = []
    # Fixed % thresholds
    for thresh in (-0.020, -0.025, -0.030, -0.035, -0.040, -0.050):
        variants.append((f'fixed_{int(abs(thresh*1000)):03d}', 'fixed',
                          {'threshold': thresh}))
    # Z thresholds × lookbacks (negative — we want LOW z scores = unusual drops)
    for lb in (30, 60, 90):
        for thresh in (-1.5, -2.0, -2.5, -3.0):
            variants.append((f'z_{lb}d_{int(abs(thresh*10)):02d}', 'z',
                              {'threshold': thresh, 'lookback': f'{lb}d'}))
    # Percentile bottom-tail × lookbacks
    for lb in (30, 60, 90):
        for thresh in (0.05, 0.10, 0.15, 0.20):
            variants.append((f'pct_{lb}d_{int(thresh*100):02d}', 'pct',
                              {'threshold': thresh, 'lookback': f'{lb}d'}))

    print(f'\nEvaluating {len(variants)} threshold variants '
           f'(stop={STOP_PCT*100:.1f}%, target={TARGET_PCT*100:.1f}%, '
           f'TIF={TIF_H}h)...')
    print(f'\n{"label":<15s} {"pool n":>7s} {"pool R":>7s} {"pool MAR":>9s} '
           f'{"bull n":>7s} {"bull R":>7s} {"bull MAR":>9s} {"bull OOS(n)":>14s}')
    print('-' * 92)

    results = {}
    for label, variant, kwargs in variants:
        event_idxs = identify_events_for_variant(df, variant=variant, **kwargs)
        s_pool, s_bull = replay_and_stats(df, event_idxs, label=label)
        results[label] = {'pool': s_pool, 'bull': s_bull,
                            'n_events': len(event_idxs)}
        n_p = s_pool.get('n', 0); n_b = s_bull.get('n', 0)
        mr_p = s_pool.get('mean_R', 0); mr_b = s_bull.get('mean_R', 0)
        mar_p = s_pool.get('MAR', 0); mar_b = s_bull.get('MAR', 0)
        oos_b = s_bull.get('OOS_meanR', 0); oos_n = s_bull.get('OOS_n', 0)
        print(f'{label:<15s} {n_p:>7d} {mr_p:>+7.3f} {mar_p:>9.2f} '
               f'{n_b:>7d} {mr_b:>+7.3f} {mar_b:>9.2f} '
               f'{oos_b:>+8.3f}({oos_n:>2d})')

    # ─── Decision analysis ─────────────────────────────────────────────────
    print('\n' + '=' * 92)
    print('=== DECISION ANALYSIS: vs fixed_030 (current production) ===')
    print('=' * 92)
    base = results.get('fixed_030', {}).get('bull', {})
    if base.get('n', 0) == 0:
        print('  ERROR: fixed_030 bull baseline empty')
        return

    print(f'\n  Production baseline (fixed_030 bull-gated):')
    print(f'    n={base["n"]}  meanR={base["mean_R"]:+.3f}  '
           f'maxDD={base["maxDD"]:+.2f}  MAR={base["MAR"]:.2f}  '
           f'annual={base["annual_R"]:+.1f}R')
    print(f'    OOS_meanR={base["OOS_meanR"]:+.3f}(n={base["OOS_n"]})')

    # Pareto-better variants (bull-gated MAR ≥ baseline AND OOS ≥ baseline OOS)
    print('\n  Variants with bull-gated MAR ≥ fixed_030 baseline AND OOS_meanR ≥ baseline:')
    pareto = []
    for k, v in results.items():
        if k == 'fixed_030':
            continue
        b = v.get('bull', {})
        if b.get('n', 0) == 0:
            continue
        if b['MAR'] >= base['MAR'] and b['OOS_meanR'] >= base['OOS_meanR']:
            pareto.append((k, b))
    pareto.sort(key=lambda x: x[1]['MAR'], reverse=True)
    if not pareto:
        print('  NONE — fixed_030 is on the Pareto frontier for bull-gated MAR + OOS.')
    else:
        for k, b in pareto[:10]:
            lift = (b['MAR'] - base['MAR']) / base['MAR'] * 100
            print(f'    {k:<15s} MAR={b["MAR"]:.2f} ({lift:+.0f}%)  '
                   f'meanR={b["mean_R"]:+.3f}  '
                   f'OOS={b["OOS_meanR"]:+.3f}(n={b["OOS_n"]})  '
                   f'n={b["n"]}')

    # Best per family
    print('\n  Best per family (bull-gated MAR):')
    for prefix in ('fixed', 'z', 'pct'):
        family = [(k, v['bull']) for k, v in results.items()
                    if k.startswith(prefix) and v['bull'].get('n', 0) >= 20]
        if not family:
            continue
        family.sort(key=lambda x: x[1].get('MAR', 0), reverse=True)
        best_k, best_v = family[0]
        print(f'    {prefix}: {best_k:<15s} MAR={best_v["MAR"]:.2f}  '
               f'meanR={best_v["mean_R"]:+.3f}  '
               f'OOS={best_v["OOS_meanR"]:+.3f}(n={best_v["OOS_n"]})  '
               f'n={best_v["n"]}')

    # Robustness across lookbacks
    print('\n  Z-score robustness across lookbacks (same σ, different lookback):')
    for thresh_int in (15, 20, 25, 30):
        line = [f'    z=-{thresh_int/10:.1f}σ: ']
        for lb in (30, 60, 90):
            k = f'z_{lb}d_{thresh_int:02d}'
            b = results.get(k, {}).get('bull', {})
            if b.get('n', 0) > 0:
                line.append(f'  {lb}d: MAR={b["MAR"]:.2f} OOS={b["OOS_meanR"]:+.2f}(n={b["OOS_n"]})')
        print(''.join(line))

    print('\n  Percentile robustness across lookbacks (same %, different lookback):')
    for thresh_pct in (5, 10, 15, 20):
        line = [f'    pct={thresh_pct:>2d}%: ']
        for lb in (30, 60, 90):
            k = f'pct_{lb}d_{thresh_pct:02d}'
            b = results.get(k, {}).get('bull', {})
            if b.get('n', 0) > 0:
                line.append(f'  {lb}d: MAR={b["MAR"]:.2f} OOS={b["OOS_meanR"]:+.2f}(n={b["OOS_n"]})')
        print(''.join(line))

    # ─── Write ─────────────────────────────────────────────────────────────
    out_path = OUT_DIR / 'oi_flush_threshold_ablation.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('OI flush threshold ablation: tests fixed % vs rolling '
                     'z-score vs rolling percentile thresholds for the '
                     'flush trigger. Hold stop=2%, target=3%, TIF=48h '
                     '(Phase 2 best). Apply PRICE_DIR < -0.5% always. '
                     'Report POOLED and BULL_GATED (BTC ret_30d > +10%).'),
            'config': {
                'stop_pct': STOP_PCT, 'target_pct': TARGET_PCT,
                'tif_h': TIF_H, 'bull_threshold': BULL_REGIME_THRESHOLD,
            },
            'baseline_label': 'fixed_030',
            'results': results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

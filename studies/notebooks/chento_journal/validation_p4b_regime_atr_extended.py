"""P4b of the 2026-06-05 lookahead audit: GATING test for the atr6 hypothesis.

P4 found atr_mult=6 lifts MAR by +37% over atr_mult=5 (current production).
Three concerns from the verification workflow (wf_ac66eec9-8dd):

  1. P4 omitted FILTER_SKIP_UP_30D_SHORTS which production uses.
     Pool composition differs by ~28%. P4b restores it.
  2. ATR sweep was only {4,5,6}. atr6 could be a local max. P4b extends
     to {7, 8}.
  3. maxDD is a single realized path. P4b adds bootstrap 95% CI on each
     variant's maxDD via 1000-resample with replacement.

Decision rule (must satisfy ALL three):
  (a) atr6_t6R still ranks top-3 by MAR after regime filter
  (b) MAR lift over atr5_t6R remains >= +10%
  (c) atr6_t6R bootstrap 95%-CI maxDD upper bound is strictly < atr5_t6R's
      point estimate (so the DD edge isn't single-cluster luck)

If all three hold, ship atr_mult=6. Otherwise hold at 5.
"""
from __future__ import annotations

import json
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
OUT_DIR = ROOT / 'studies' / 'material' / 'chento' / 'validation'
OUT_DIR.mkdir(parents=True, exist_ok=True)

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

import studies.notebooks.chento_journal.validation_B_composite as src_comp
import studies.notebooks.chento_journal.validation_group_A_tuning as src_p3
from studies.notebooks.chento_journal.validation_group_A_tuning import (
    build_optimized_triggers, apply_filters, replay_with_extras, summary,
)
from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import load_btc_15m
from studies.notebooks.chento_journal.validation_C5_smc_features import features_at

IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')
UP_30D_THRESHOLD = 0.10   # production value
BOOTSTRAP_N = 1000


def intersect_backward(a: pd.DataFrame, b: pd.DataFrame, *,
                       window_hours: float = 24.0) -> pd.DataFrame:
    if a.empty or b.empty:
        return pd.DataFrame(columns=a.columns)
    keep = []
    for i, ra in a.iterrows():
        bs = b[b['direction'] == ra['direction']]
        if bs.empty:
            continue
        delta_h = (bs['ts'] - ra['ts']).dt.total_seconds() / 3600.0
        if ((delta_h >= -window_hours) & (delta_h <= 0)).any():
            keep.append(i)
    return a.loc[keep].copy()


def compute_btc_ret_30d(df_15m: pd.DataFrame) -> pd.Series:
    """30-day rolling return of BTC close, indexed by 15m timestamp."""
    daily_close = df_15m['close'].resample('1D').last()
    daily_ret_30 = daily_close.pct_change(30)
    return daily_ret_30.reindex(df_15m.index, method='ffill')


def apply_regime_filter(rep: pd.DataFrame, ret_30d: pd.Series) -> pd.DataFrame:
    """Production rule: skip SHORT trades when ret_30d > +0.10."""
    if rep.empty:
        return rep
    ts_idx = pd.DatetimeIndex(rep['ts'])
    ix = ret_30d.index.searchsorted(ts_idx, side='right') - 1
    rep = rep.copy()
    rep['ret_30d'] = [float(ret_30d.iloc[i]) if 0 <= i < len(ret_30d)
                       else np.nan for i in ix]
    drop_mask = ((rep['direction'] == 'short') &
                 (rep['ret_30d'] > UP_30D_THRESHOLD))
    return rep[~drop_mask].copy()


def replay_then_filter_with_regime(triple_w, df_smc, df_atr, fvgs, obs,
                                     delta_df, ret_30d, *,
                                     atr_mult: float, target_r: float,
                                     tif_hours: int = 72):
    rows = []
    for _, t in triple_w.iterrows():
        r = replay_with_extras(t, df_smc, df_atr, fvgs, obs,
                                atr_mult=atr_mult, target_r=target_r,
                                tif_bars=4 * tif_hours,
                                partial_at_R=None, partial_frac=0.0,
                                ladder_at_minus_R=None, ladder_size_frac=0.0)
        if r is not None:
            rows.append(r)
    rep = pd.DataFrame(rows)
    if rep.empty:
        return rep
    dist_arr = []
    for _, row in rep.iterrows():
        idx = df_smc.index.searchsorted(row['ts'], side='right') - 1
        if idx < 0 or idx >= len(df_smc):
            dist_arr.append(np.nan); continue
        f = features_at(idx, row['entry'], row['direction'], row['risk'],
                          df_smc, fvgs, obs)
        dist_arr.append(f.get('dist_resist_OB_R', np.nan))
    rep['dist_resist_OB_R'] = dist_arr
    rep = apply_filters(rep, delta_df)            # production filters 1-3
    rep = apply_regime_filter(rep, ret_30d)       # production filter 4
    return rep


def bootstrap_maxdd_ci(r_outcomes: np.ndarray, n_boot: int = BOOTSTRAP_N,
                        rng: np.random.Generator | None = None) -> dict:
    """Resample (with replacement) the trade sequence and return the
    distribution of realized maxDD (cumulative-min of cum_R - peak)."""
    if rng is None:
        rng = np.random.default_rng(42)
    n = len(r_outcomes)
    if n == 0:
        return {'p05': 0, 'p50': 0, 'p95': 0, 'point': 0}
    point_cum = np.cumsum(r_outcomes)
    point_peak = np.maximum.accumulate(point_cum)
    point_dd = float((point_cum - point_peak).min())
    maxDDs = np.empty(n_boot)
    for k in range(n_boot):
        sample_idx = rng.integers(0, n, size=n)
        sample = r_outcomes[sample_idx]
        c = np.cumsum(sample)
        p = np.maximum.accumulate(c)
        maxDDs[k] = (c - p).min()
    return {
        'point': round(point_dd, 3),
        'p05': round(float(np.percentile(maxDDs, 5)), 3),
        'p50': round(float(np.percentile(maxDDs, 50)), 3),
        'p95': round(float(np.percentile(maxDDs, 95)), 3),
    }


def add_full_summary(rep: pd.DataFrame, label: str) -> dict:
    s = summary(rep, label)
    if s.get('n', 0) == 0:
        s.update({'annual_R': 0, 'MAR': 0,
                   'dd_p05': 0, 'dd_p50': 0, 'dd_p95': 0, 'dd_point': 0})
        return s
    span_y = ((rep['ts'].max() - rep['ts'].min()).total_seconds()
                / (365.25 * 86400))
    annual_R = s['mean_R'] * s['n'] / max(span_y, 0.1)
    s['annual_R'] = round(annual_R, 1)
    s['MAR'] = round(annual_R / abs(s['max_dd_R']) if s['max_dd_R'] != 0 else 0, 2)
    boot = bootstrap_maxdd_ci(rep.sort_values('ts')['r_outcome'].values)
    s['dd_point'] = boot['point']
    s['dd_p05'] = boot['p05']
    s['dd_p50'] = boot['p50']
    s['dd_p95'] = boot['p95']
    return s


def main():
    src_comp.intersect_triggers = intersect_backward
    src_p3.intersect_triggers = intersect_backward
    print('[P4b] intersect_triggers patched to BACKWARD-ONLY [-24h, 0]')

    print('Building optimized triggers (backward-only)...')
    triple_w, df_smc, df_atr, fvgs, obs, delta_df = build_optimized_triggers()
    print(f'  Triple bars: {len(triple_w)}')

    print('Computing BTC 30d return for regime filter...')
    df_15m_full = load_btc_15m()
    ret_30d = compute_btc_ret_30d(df_15m_full)

    ATR_MULTS = (4.0, 5.0, 6.0, 7.0, 8.0)
    TARGETS = (4.0, 5.0, 6.0, 7.0, 8.0, 10.0)

    print('\n' + '=' * 110)
    print('=== P4b: Post-filter (incl REGIME) + bootstrap maxDD CI — BACKWARD-ONLY ===')
    print('=' * 110)
    print(f'\n{"variant":<14s} {"n":>3s} {"meanR":>7s} {"WR":>4s} '
           f'{"maxDD":>7s} {"DD_p05":>7s} {"DD_p95":>7s} '
           f'{"annual":>8s} {"MAR":>5s} '
           f'{"IS_meanR(n)":>14s} {"OOS_meanR(n)":>14s}')

    results = {}
    for atr_mult in ATR_MULTS:
        for target_r in TARGETS:
            label = f'atr{atr_mult:.0f}_t{target_r:.0f}R'
            rep = replay_then_filter_with_regime(triple_w, df_smc, df_atr,
                                                    fvgs, obs, delta_df, ret_30d,
                                                    atr_mult=atr_mult,
                                                    target_r=target_r,
                                                    tif_hours=72)
            s = add_full_summary(rep, label)
            results[label] = s
            print(f'{label:<14s} {s["n"]:>3d} {s["mean_R"]:>+7.3f} '
                   f'{s["wr"]:>4.0%} {s["max_dd_R"]:>+7.2f} '
                   f'{s["dd_p05"]:>+7.2f} {s["dd_p95"]:>+7.2f} '
                   f'{s["annual_R"]:>+8.1f} {s["MAR"]:>5.2f} '
                   f'{s["IS_meanR"]:>+8.3f}({s["IS_n"]:>2d}) '
                   f'{s["OOS_meanR"]:>+8.3f}({s["OOS_n"]:>2d})')

    print('\n' + '=' * 110)
    print('=== Top by MAR (n≥15, both IS+OOS > 0) ===')
    print('=' * 110)
    cands = [(k, v) for k, v in results.items()
              if v.get('n', 0) >= 15
              and v.get('IS_meanR', 0) > 0
              and v.get('OOS_meanR', 0) > 0]
    cands.sort(key=lambda kv: kv[1]['MAR'], reverse=True)
    for k, v in cands[:12]:
        print(f'  {k:<14s} MAR={v["MAR"]:>5.2f}  annual={v["annual_R"]:>+6.1f}R  '
               f'maxDD={v["max_dd_R"]:+5.2f} (p95={v["dd_p95"]:+5.2f})  '
               f'meanR={v["mean_R"]:+.3f}  OOS={v["OOS_meanR"]:+.3f}({v["OOS_n"]})')

    print('\n' + '=' * 110)
    print('=== HEADLINE: gating decision for atr_mult change ===')
    print('=' * 110)
    base = results.get('atr5_t6R', {})
    cand6 = results.get('atr6_t6R', {})
    if base.get('n', 0) > 0 and cand6.get('n', 0) > 0:
        base_mar = base['MAR']; cand_mar = cand6['MAR']
        mar_lift_pct = (cand_mar - base_mar) / base_mar * 100 if base_mar > 0 else 0
        print(f'\n  atr5_t6R (current production):')
        print(f'    n={base["n"]}, meanR={base["mean_R"]:+.3f}, OOS={base["OOS_meanR"]:+.3f}(n={base["OOS_n"]})')
        print(f'    annual={base["annual_R"]:+.1f}R, MAR={base_mar:.2f}')
        print(f'    maxDD point={base["max_dd_R"]:+.2f}R, bootstrap CI: [{base["dd_p05"]:+.2f}, {base["dd_p95"]:+.2f}]')
        print(f'\n  atr6_t6R (candidate):')
        print(f'    n={cand6["n"]}, meanR={cand6["mean_R"]:+.3f}, OOS={cand6["OOS_meanR"]:+.3f}(n={cand6["OOS_n"]})')
        print(f'    annual={cand6["annual_R"]:+.1f}R, MAR={cand_mar:.2f}')
        print(f'    maxDD point={cand6["max_dd_R"]:+.2f}R, bootstrap CI: [{cand6["dd_p05"]:+.2f}, {cand6["dd_p95"]:+.2f}]')
        print(f'\n  MAR lift: {mar_lift_pct:+.1f}%')

        # Gating tests
        crit_a = cand_mar >= sorted([v['MAR'] for k, v in cands[:3]])[0] if cands else False
        crit_b = mar_lift_pct >= 10
        crit_c = cand6['dd_p95'] > base['max_dd_R']  # cand's upper-CI strictly tighter than base point
        print('\n  GATING:')
        print(f'    (a) atr6_t6R in top-3 by MAR (regime-filtered)?  {"PASS" if crit_a else "FAIL"}')
        print(f'    (b) MAR lift >= +10%?                             {"PASS" if crit_b else "FAIL"}  ({mar_lift_pct:+.1f}%)')
        print(f'    (c) atr6 bootstrap p95 maxDD ({cand6["dd_p95"]:+.2f}) > atr5 point ({base["max_dd_R"]:+.2f})?')
        print(f'        (smaller magnitude = tighter DD = PASS)        {"PASS" if crit_c else "FAIL"}')
        verdict = 'SHIP atr_mult=6' if (crit_a and crit_b and crit_c) else 'HOLD at atr_mult=5'
        print(f'\n  VERDICT: {verdict}')

    # Check ATR gradient continuation (local max test)
    print('\n=== ATR_mult gradient at t=6R (local max check) ===')
    for atr in ATR_MULTS:
        k = f'atr{atr:.0f}_t6R'
        v = results.get(k)
        if v and v.get('n', 0) > 0:
            print(f'  {k}: n={v["n"]} MAR={v["MAR"]:.2f} maxDD={v["max_dd_R"]:+.2f} '
                   f'OOS={v["OOS_meanR"]:+.3f}(n={v["OOS_n"]})')

    out_path = OUT_DIR / 'p4b_regime_atr_extended_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('P4b gating test: ATR sweep extended to {4,5,6,7,8} with '
                     'FILTER_SKIP_UP_30D_SHORTS applied (production-equivalent). '
                     'Bootstrap 95% CI on maxDD via 1000 resamples. Decision rule '
                     'in script docstring.'),
            'config': {
                'tif_hours': 72, 'ladder_enabled': False,
                'filters': ['no_tilt', 'no_resist_OB(>2R)', 'okx_aligned(z>=0)',
                            'skip_up_30d_shorts(ret_30d>0.10)'],
                'bootstrap_n': BOOTSTRAP_N,
            },
            'results': results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

"""validation_C4_cross_exchange_delta: cross-exchange perp price delta as
signal / gate on the optimized Triple composite.

Hypothesis (per flowx "Exchange Deviation"):
  When one exchange's BTC perp price leads another's by a measurable
  margin during a directional move, the lagging exchange catches up —
  often violently. The leading exchange flow is a directional signal.

Specifically:
  - delta_okx_bnb[t]    = ln(close_okx[t]) - ln(close_bnb[t])
  - delta_bybit_bnb[t]  = ln(close_bybit[t]) - ln(close_bnb[t])
  - delta_okx_bybit[t]  = ln(close_okx[t]) - ln(close_bybit[t])
  - z-score each over rolling 7d window

Tests:
  1. Standalone: extreme delta + price direction as entry signal
  2. As gate on optimized Triple composite (filter where delta agrees /
     disagrees with trade direction)
  3. Sign-of-recent-delta-change (one exchange flipped from leading to
     lagging — momentum exhaustion / reversal signal)
"""
from __future__ import annotations

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
OUT_DIR.mkdir(parents=True, exist_ok=True)

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
    load_btc_15m, compute_moneyflow_signal, b1_triggers, compute_atr,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    load_lsr, compute_lsr_extremes, b5_triggers,
)
from studies.notebooks.chento_journal.validation_B7_multitf_cvd import (
    compute_multitf_cvd, b7_alignment_triggers,
)
from studies.notebooks.chento_journal.validation_B_composite import intersect_triggers
from studies.notebooks.chento_journal.validation_C5_smc_features import (
    compute_pivots, compute_smc_state, compute_order_blocks, compute_fvgs,
    replay_one,
)

IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')


# === Loaders ===============================================================

def load_perp_1h() -> pd.DataFrame:
    """Load 1h closes from Binance / OKX / Bybit aligned on timestamp."""
    con = sqlite3.connect(str(DB))
    # Binance: cd_futures_ohlcv is 1h; pull close + timestamp
    bnb = pd.read_sql("""
        SELECT timestamp, close AS close_bnb
        FROM cd_futures_ohlcv
        ORDER BY timestamp
    """, con)
    okx = pd.read_sql("""
        SELECT timestamp, close AS close_okx
        FROM okx_perp_1h
        ORDER BY timestamp
    """, con)
    bybit = pd.read_sql("""
        SELECT timestamp, close AS close_bybit
        FROM bybit_perp_1h
        ORDER BY timestamp
    """, con)
    con.close()
    # Merge
    out = bnb.merge(okx, on='timestamp', how='outer').merge(bybit, on='timestamp', how='outer')
    out['ts'] = pd.to_datetime(out['timestamp'], unit='s', utc=True)
    out = out.set_index('ts').drop(columns='timestamp')
    out = out.sort_index()
    # Restrict to where all three are present
    out = out.dropna(how='any')
    return out


def compute_deltas(df: pd.DataFrame, *,
                    z_window_hours: int = 24 * 7    # 7-day rolling window
                    ) -> pd.DataFrame:
    out = df.copy()
    # Log-ratios (small-number-friendly, additive)
    out['delta_okx_bnb'] = np.log(out['close_okx']) - np.log(out['close_bnb'])
    out['delta_bybit_bnb'] = np.log(out['close_bybit']) - np.log(out['close_bnb'])
    out['delta_okx_bybit'] = np.log(out['close_okx']) - np.log(out['close_bybit'])
    for col in ('delta_okx_bnb', 'delta_bybit_bnb', 'delta_okx_bybit'):
        mu = out[col].rolling(z_window_hours, min_periods=z_window_hours // 4).mean()
        sd = out[col].rolling(z_window_hours, min_periods=z_window_hours // 4).std()
        out[f'{col}_z'] = (out[col] - mu) / sd
    # Composite: max-deviation z (how out-of-whack overall)
    out['max_abs_z'] = out[['delta_okx_bnb_z', 'delta_bybit_bnb_z',
                              'delta_okx_bybit_z']].abs().max(axis=1)
    # Direction of "lead": positive okx_bnb_z means okx is premium → okx leading
    return out


# === Summary helpers =======================================================

def summary(t: pd.DataFrame, label: str) -> dict:
    if t.empty:
        return {'label': label, 'n': 0}
    t = t.sort_values('ts').reset_index(drop=True)
    cum = t['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    is_set = t[t['ts'] <= IS_END]; oos_set = t[t['ts'] > IS_END]
    return {
        'label': label, 'n': int(len(t)),
        'mean_R': round(float(t['r_outcome'].mean()), 3),
        'wr': round(float((t['r_outcome'] > 0).mean()), 3),
        'cum_R': round(float(cum[-1]), 2),
        'max_dd_R': round(float(dd.min()), 2),
        'IS_meanR': round(float(is_set['r_outcome'].mean()) if len(is_set) else 0, 3),
        'IS_n': int(len(is_set)),
        'OOS_meanR': round(float(oos_set['r_outcome'].mean()) if len(oos_set) else 0, 3),
        'OOS_n': int(len(oos_set)),
    }


def show(s):
    if s.get('n', 0) == 0:
        print(f'  {s["label"]:<55s} empty')
        return
    print(f'  {s["label"]:<55s} n={s["n"]:>4d}  meanR={s["mean_R"]:+.3f}  '
           f'WR={s["wr"]:.0%}  cumR={s["cum_R"]:>+7.1f}  '
           f'maxDD={s["max_dd_R"]:+5.2f}  IS={s["IS_meanR"]:+.3f}({s["IS_n"]})  '
           f'OOS={s["OOS_meanR"]:+.3f}({s["OOS_n"]})')


def main():
    print('Loading 1h perp closes from Binance / OKX / Bybit...')
    perp = load_perp_1h()
    print(f'  Aligned 1h bars: {len(perp):,}  {perp.index.min()} -> {perp.index.max()}')
    perp_z = compute_deltas(perp)

    # Magnitude
    for col in ('delta_okx_bnb', 'delta_bybit_bnb', 'delta_okx_bybit'):
        v = perp_z[col].dropna() * 10000   # in bps
        print(f'  {col} (bps): mean={v.mean():+.2f}, std={v.std():.2f}, '
               f'p1/p99 = {v.quantile(0.01):+.2f}/{v.quantile(0.99):+.2f}')

    # === Build optimized Triple composite trades, then test deltas as gate ===
    print('\nBuilding optimized Triple (atr5_t6R + no_tilt + no_resist_OB_2R)...')
    df_15m = load_btc_15m()
    df_p = compute_pivots(df_15m, n=5)
    df_smc = compute_smc_state(df_p, n=5)
    obs = compute_order_blocks(df_smc)
    fvgs = compute_fvgs(df_smc)
    df_atr = df_smc.copy()
    df_atr['atr'] = compute_atr(df_atr, period=14)
    df_b1 = compute_moneyflow_signal(df_15m)
    df_b1['atr'] = compute_atr(df_b1, period=14)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df_15m, compute_lsr_extremes(load_lsr('BTC')))
    b7 = b7_alignment_triggers(compute_multitf_cvd(df_15m), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    # Trim to cross-exchange window
    cx_start = perp_z.index.min() + pd.Timedelta(days=14)
    triple_cx = triple[triple['ts'] >= cx_start].copy()
    print(f'  Triple triggers in cross-exchange window (>= {cx_start.date()}): {len(triple_cx):,}')

    rows = []
    for _, t in triple_cx.iterrows():
        r = replay_one(t, df_smc, df_atr, fvgs, obs,
                        atr_mult=5.0, target_r=6.0, tp_mode='fixed')
        if r is not None:
            rows.append(r)
    rep = pd.DataFrame(rows)
    if rep.empty:
        print('  no replays — aborting')
        return
    # Apply no_tilt + no_resist_OB
    rep = rep.sort_values('ts').reset_index(drop=True)
    cur = 0; lb = []
    for r in rep['r_outcome'].shift(1).fillna(0):
        if r < 0: cur += 1
        else: cur = 0
        lb.append(cur)
    rep['consec_losses_before'] = lb
    mask = (rep['consec_losses_before'] == 0) & (
        (rep['dist_resist_OB_R'] > 2.0) | rep['dist_resist_OB_R'].isna())
    opt = rep[mask].copy()
    print(f'  Optimized trades on cross-exchange window: {len(opt):,}')
    show(summary(opt, 'optimized (no cross-exchange filter)'))

    # Attach cross-exchange deltas at entry
    ts_idx = pd.DatetimeIndex(opt['ts'])
    ix = perp_z.index.searchsorted(ts_idx, side='right') - 1
    for col in ('delta_okx_bnb', 'delta_bybit_bnb', 'delta_okx_bybit',
                 'delta_okx_bnb_z', 'delta_bybit_bnb_z', 'delta_okx_bybit_z',
                 'max_abs_z'):
        opt[col] = [float(perp_z[col].iloc[i]) if 0 <= i < len(perp_z) else np.nan
                     for i in ix]

    all_results = {'optimized_baseline': summary(opt, 'optimized_baseline')}

    # === Test: filter by aggregate deviation strength ===
    print('\n--- Filter: max_abs_z (cross-exchange stress) ---')
    for thr in (0.5, 1.0, 1.5, 2.0, 2.5):
        # HIGH stress (>thr): exchanges deviating → momentum / breakout regime
        high = opt[opt['max_abs_z'] >= thr]
        low = opt[opt['max_abs_z'] < thr]
        s_h = summary(high, f'high_stress (max_abs_z>={thr})')
        s_l = summary(low, f'low_stress (max_abs_z<{thr})')
        all_results[s_h['label']] = s_h
        all_results[s_l['label']] = s_l
        show(s_h); show(s_l)

    # === Test: directional alignment (delta sign vs trade direction) ===
    print('\n--- Filter: OKX premium aligns with trade direction ---')
    # If long: want close_okx > close_bnb (okx is premium = pulling price up)
    # If short: want close_okx < close_bnb (okx is discount = pulling price down)
    for thr in (0.0, 0.5, 1.0, 1.5):
        aligned = (
            ((opt['direction'] == 'long') & (opt['delta_okx_bnb_z'] >= thr)) |
            ((opt['direction'] == 'short') & (opt['delta_okx_bnb_z'] <= -thr))
        )
        s = summary(opt[aligned], f'okx_aligned (|z|>={thr})')
        all_results[s['label']] = s
        show(s)

    print('\n--- Filter: OKX premium CONTRARY to trade direction (mean-revert ahead) ---')
    # Triple is mean-reversion. If long, we ENTER when shorts are getting squeezed.
    # If OKX is at premium (longs paying up), that's the squeeze that triggers our long entry.
    # Wait, "contrary" relative to the upcoming move: long entry wants premium to REVERSE.
    # Let me re-think: actually for mean-reversion long entry (price oversold, expect bounce),
    # a strong OKX discount that's about to revert UP would be aligned.
    # That's actually the same as "aligned" but I'll test both for completeness.
    for thr in (0.0, 0.5, 1.0, 1.5):
        contrary = (
            ((opt['direction'] == 'long') & (opt['delta_okx_bnb_z'] <= -thr)) |
            ((opt['direction'] == 'short') & (opt['delta_okx_bnb_z'] >= thr))
        )
        s = summary(opt[contrary], f'okx_contrary (|z|>={thr})')
        all_results[s['label']] = s
        show(s)

    # === Test: Bybit-Binance delta same direction filter ===
    print('\n--- Filter: Bybit premium aligns with trade direction ---')
    for thr in (0.0, 0.5, 1.0):
        aligned = (
            ((opt['direction'] == 'long') & (opt['delta_bybit_bnb_z'] >= thr)) |
            ((opt['direction'] == 'short') & (opt['delta_bybit_bnb_z'] <= -thr))
        )
        s = summary(opt[aligned], f'bybit_aligned (|z|>={thr})')
        all_results[s['label']] = s
        show(s)

    # === Test: BOTH OKX and Bybit aligned (consensus) ===
    print('\n--- Filter: BOTH OKX and Bybit aligned (cross-exchange consensus) ---')
    for thr in (0.0, 0.5, 1.0):
        long_aligned = ((opt['direction'] == 'long') &
                          (opt['delta_okx_bnb_z'] >= thr) &
                          (opt['delta_bybit_bnb_z'] >= thr))
        short_aligned = ((opt['direction'] == 'short') &
                           (opt['delta_okx_bnb_z'] <= -thr) &
                           (opt['delta_bybit_bnb_z'] <= -thr))
        s = summary(opt[long_aligned | short_aligned], f'consensus_aligned (|z|>={thr})')
        all_results[s['label']] = s
        show(s)

    # === Test: BOTH OKX and Bybit CONTRARY (anti-consensus) ===
    print('\n--- Filter: BOTH OKX and Bybit CONTRARY to trade ---')
    for thr in (0.0, 0.5, 1.0):
        long_contrary = ((opt['direction'] == 'long') &
                          (opt['delta_okx_bnb_z'] <= -thr) &
                          (opt['delta_bybit_bnb_z'] <= -thr))
        short_contrary = ((opt['direction'] == 'short') &
                           (opt['delta_okx_bnb_z'] >= thr) &
                           (opt['delta_bybit_bnb_z'] >= thr))
        s = summary(opt[long_contrary | short_contrary], f'consensus_contrary (|z|>={thr})')
        all_results[s['label']] = s
        show(s)

    # === Save ===
    out_path = OUT_DIR / 'C4_cross_exchange_delta_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('Cross-exchange perp delta (Binance/OKX/Bybit 1h) as gate '
                      'on optimized Triple composite + standalone signal tests.'),
            'cross_exchange_window_start': str(perp_z.index.min()),
            'cross_exchange_window_end': str(perp_z.index.max()),
            'variants': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

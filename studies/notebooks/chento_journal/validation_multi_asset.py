"""validation_multi_asset: apply the optimized Triple composite stack to
ETH and OP, compare to BTC.

The optimized stack (per [[chento-triple-optimized-config]]):
  Trigger: B1 money-flow divergence ∩ B5 LSR extremes ∩ B7 multi-TF CVD align
  Math:    atr5_t6R fixed
  Gates:   no_tilt + no_resist_OB_within_2R + okx_aligned (delta z >= 0 long
            / <= 0 short)

Tests whether the edge generalizes beyond BTC, or whether the optimizations
(particularly the OKX gate) are BTC-specific.

Data dependencies per asset:
  - cd_futures_{asset}_15m  (or cd_futures_15m for BTC) — perp 15m with taker split
  - ca_long_short_ratio asset = {BTC|ETH|OP} — for B5
  - okx_perp_{asset}_1h — for OKX gate
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
    compute_moneyflow_signal, b1_triggers, compute_atr,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    compute_lsr_extremes, b5_triggers,
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

# Per-asset table mapping
ASSET_CONFIG = {
    'BTC': {
        'futures_15m': 'cd_futures_15m',
        'okx_1h': 'okx_perp_1h',
        'lsr_asset': 'BTC',
    },
    'ETH': {
        'futures_15m': 'cd_futures_eth_15m',
        'okx_1h': 'okx_perp_eth_1h',
        'lsr_asset': 'ETH',
    },
    'OP': {
        'futures_15m': 'cd_futures_op_15m',
        'okx_1h': 'okx_perp_op_1h',
        'lsr_asset': 'OP',
    },
}


# === Loaders ===============================================================

def load_perp_15m(asset: str) -> pd.DataFrame:
    table = ASSET_CONFIG[asset]['futures_15m']
    con = sqlite3.connect(str(DB))
    df = pd.read_sql(f"""
        SELECT timestamp, open, high, low, close, volume, quote_volume,
               volume_buy, quote_volume_buy, volume_sell, quote_volume_sell
        FROM {table}
        ORDER BY timestamp
    """, con)
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    df = df.set_index('ts').drop(columns='timestamp')
    df = df[~df.index.duplicated(keep='last')]
    return df


def load_lsr_asset(asset: str) -> pd.DataFrame:
    con = sqlite3.connect(str(DB))
    df = pd.read_sql("""
        SELECT timestamp, ratio, long_pct, short_pct
        FROM ca_long_short_ratio
        WHERE asset = ?
        ORDER BY timestamp
    """, con, params=[asset])
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    df = df.set_index('ts').drop(columns='timestamp')
    return df


def load_okx_close_asset(asset: str) -> pd.Series:
    table = ASSET_CONFIG[asset]['okx_1h']
    con = sqlite3.connect(str(DB))
    df = pd.read_sql(f"SELECT timestamp, close FROM {table} ORDER BY timestamp", con)
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    return df.set_index('ts')['close']


def derive_binance_1h_close(df_15m: pd.DataFrame) -> pd.Series:
    """Resample 15m perp close to 1h close (causal: take the close of the
    last 15m bar within each hour)."""
    h = df_15m['close'].resample('1h').last()
    return h


# === OKX gate computation ==================================================

def compute_okx_delta_z(close_bnb_1h: pd.Series, close_okx_1h: pd.Series, *,
                          window_hours: int = 24 * 7) -> pd.DataFrame:
    df = pd.DataFrame({'close_bnb': close_bnb_1h, 'close_okx': close_okx_1h}).dropna()
    df['delta'] = np.log(df['close_okx']) - np.log(df['close_bnb'])
    mu = df['delta'].rolling(window_hours, min_periods=window_hours // 4).mean()
    sd = df['delta'].rolling(window_hours, min_periods=window_hours // 4).std()
    df['delta_z'] = (df['delta'] - mu) / sd
    return df


# === Summary ==============================================================

def summary(t: pd.DataFrame, label: str) -> dict:
    if t.empty:
        return {'label': label, 'n': 0}
    t = t.sort_values('ts').reset_index(drop=True)
    cum = t['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    is_set = t[t['ts'] <= IS_END]; oos_set = t[t['ts'] > IS_END]
    span_y = (t['ts'].max() - t['ts'].min()).total_seconds() / (365.25 * 86400)
    return {
        'label': label, 'n': int(len(t)),
        'per_yr': round(len(t) / max(span_y, 0.1), 1),
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
    print(f'  {s["label"]:<55s} n={s["n"]:>4d} ({s.get("per_yr",0):>5.1f}/yr)  '
           f'R={s["mean_R"]:+.3f}  WR={s["wr"]:.0%}  cumR={s["cum_R"]:>+7.1f}  '
           f'maxDD={s["max_dd_R"]:+5.2f}  IS={s["IS_meanR"]:+.3f}({s["IS_n"]})  '
           f'OOS={s["OOS_meanR"]:+.3f}({s["OOS_n"]})')


# === Per-asset run ==========================================================

def run_asset(asset: str) -> dict:
    print(f'\n{"="*80}')
    print(f'=== {asset} ===')
    print(f'{"="*80}')

    try:
        df_15m = load_perp_15m(asset)
    except Exception as e:
        print(f'  load_perp_15m failed: {e}')
        return {'asset': asset, 'error': str(e)}
    print(f'  perp 15m: {len(df_15m):,} bars  {df_15m.index.min()} -> {df_15m.index.max()}')

    # Build B1
    df_b1 = compute_moneyflow_signal(df_15m)
    df_b1['atr'] = compute_atr(df_b1, period=14)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    print(f'  B1 triggers (money flow div): {len(b1):,}')

    # B5
    try:
        lsr_df = load_lsr_asset(ASSET_CONFIG[asset]['lsr_asset'])
        print(f'  LSR rows: {len(lsr_df):,}  ({lsr_df.index.min()} -> {lsr_df.index.max()})')
        lsr_z = compute_lsr_extremes(lsr_df)
        b5 = b5_triggers(df_15m, lsr_z)
        print(f'  B5 triggers (LSR extremes): {len(b5):,}')
    except Exception as e:
        print(f'  B5 unavailable for {asset}: {e}')
        b5 = pd.DataFrame()

    # B7
    multitf = compute_multitf_cvd(df_15m)
    b7 = b7_alignment_triggers(multitf, z_threshold=2.0)
    print(f'  B7 triggers (multi-TF CVD): {len(b7):,}')

    # Triple
    if not b5.empty:
        triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    else:
        # If no LSR, fall back to B1 ∩ B7 (degraded composite)
        triple = intersect_triggers(b1, b7)
    print(f'  Triple triggers: {len(triple):,}')

    if triple.empty:
        return {'asset': asset, 'n_triple': 0}

    # SMC + replay
    df_p = compute_pivots(df_15m, n=5)
    df_smc = compute_smc_state(df_p, n=5)
    obs = compute_order_blocks(df_smc)
    fvgs = compute_fvgs(df_smc)
    df_atr = df_smc.copy()
    df_atr['atr'] = compute_atr(df_atr, period=14)

    rows = []
    for _, t in triple.iterrows():
        r = replay_one(t, df_smc, df_atr, fvgs, obs,
                        atr_mult=5.0, target_r=6.0, tp_mode='fixed')
        if r is not None:
            rows.append(r)
    rep = pd.DataFrame(rows)
    if rep.empty:
        return {'asset': asset, 'n_triple': len(triple), 'n_replay': 0}

    # Apply no_tilt + no_resist_OB
    rep = rep.sort_values('ts').reset_index(drop=True)
    cur = 0; lb = []
    for r in rep['r_outcome'].shift(1).fillna(0):
        if r < 0: cur += 1
        else: cur = 0
        lb.append(cur)
    rep['consec_losses_before'] = lb
    mask_filt = (rep['consec_losses_before'] == 0) & (
        (rep['dist_resist_OB_R'] > 2.0) | rep['dist_resist_OB_R'].isna())
    opt = rep[mask_filt].copy()

    # Baselines per asset
    results = {}
    show(summary(rep, f'{asset} baseline atr5_t6R'))
    results['baseline_atr5_t6R'] = summary(rep, 'baseline_atr5_t6R')
    show(summary(opt, f'{asset} + no_tilt + no_resist_OB'))
    results['filt_no_tilt_no_resist_OB'] = summary(opt, 'filt_no_tilt_no_resist_OB')

    # OKX gate (if available)
    try:
        okx_close = load_okx_close_asset(asset)
        print(f'  OKX 1h close: {len(okx_close):,}  ({okx_close.index.min()} -> {okx_close.index.max()})')
        binance_1h_close = derive_binance_1h_close(df_15m)
        delta_df = compute_okx_delta_z(binance_1h_close, okx_close)

        ts_idx = pd.DatetimeIndex(opt['ts'])
        ix = delta_df.index.searchsorted(ts_idx, side='right') - 1
        opt['okx_delta_z'] = [float(delta_df['delta_z'].iloc[i])
                                if 0 <= i < len(delta_df) else np.nan
                                for i in ix]

        for thr in (0.0, 0.5, 1.0):
            aligned = (
                ((opt['direction'] == 'long') & (opt['okx_delta_z'] >= thr)) |
                ((opt['direction'] == 'short') & (opt['okx_delta_z'] <= -thr))
            )
            sub = opt[aligned]
            s = summary(sub, f'{asset} + okx_aligned (z>={thr})')
            results[f'okx_aligned_z{thr}'] = summary(sub, f'okx_aligned_z{thr}')
            show(s)
    except Exception as e:
        print(f'  OKX gate unavailable for {asset}: {e}')

    return {'asset': asset, 'n_triple': len(triple), 'n_replay': len(rep),
            'n_filt': len(opt), 'results': results}


# === Main ==================================================================

def main():
    all_results = {}
    for asset in ('BTC', 'ETH', 'OP'):
        all_results[asset] = run_asset(asset)

    # === Summary table ===
    print(f'\n{"="*80}')
    print('=== Summary: optimized stack across assets ===')
    print(f'{"="*80}')
    print(f'\n{"asset":<6s} {"variant":<35s} {"n":>4s} {"/yr":>5s} {"meanR":>7s} {"WR":>5s} {"cumR":>8s} {"maxDD":>7s} {"IS":>9s} {"OOS":>9s}')
    for asset, res in all_results.items():
        if 'results' not in res: continue
        for label, s in res['results'].items():
            if s['n'] == 0: continue
            print(f'{asset:<6s} {label:<35s} {s["n"]:>4d} {s.get("per_yr",0):>5.1f} '
                   f'{s["mean_R"]:>+7.3f} {s["wr"]:>5.0%} {s["cum_R"]:>+8.1f} '
                   f'{s["max_dd_R"]:>+7.2f} {s["IS_meanR"]:>+9.3f} {s["OOS_meanR"]:>+9.3f}')

    out_path = OUT_DIR / 'multi_asset_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('Multi-asset cross-validation of optimized Triple stack. '
                      'Per-asset: B1+B5+B7 composite -> atr5_t6R -> no_tilt + '
                      'no_resist_OB_2R -> okx_aligned (z>=0).'),
            'all_results': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

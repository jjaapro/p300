"""validation_B4_coinalyze: rerun B4 squeeze-direction signal on the
multi-year ca_liquidations dataset (Coinalyze).

ca_liquidations spans 2021-01 onwards (vs cd_liquidations' 87 days).
Lets us properly validate whether the +0.87R per trade finding from the
3-month cd_liquidations sample holds over 5 years.

Also re-runs the composite B1∩B5∩B7∩B4 to see if adding squeeze direction
to the triple pushes per-trade R higher.
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

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
    load_btc_15m, compute_atr, compute_moneyflow_signal, b1_triggers,
    measure_r_outcomes, summarize_triggers, chento_coverage,
)
from studies.notebooks.chento_journal.validation_B4_squeeze_direction import (
    compute_squeeze_signal,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    load_lsr, compute_lsr_extremes, b5_triggers,
)
from studies.notebooks.chento_journal.validation_B7_multitf_cvd import (
    compute_multitf_cvd, b7_alignment_triggers,
)
from studies.notebooks.chento_journal.validation_B_composite import intersect_triggers


def load_ca_liquidations(asset: str = 'BTC') -> pd.DataFrame:
    """Load Coinalyze liquidations for `asset`."""
    con = sqlite3.connect(str(DB))
    df = pd.read_sql(
        "SELECT timestamp, long_qty, short_qty FROM ca_liquidations "
        "WHERE asset = ? ORDER BY timestamp", con, params=(asset,))
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    df = df.set_index('ts').drop(columns='timestamp')
    df = df[~df.index.duplicated(keep='last')]
    # Map to cd_liquidations column names so compute_squeeze_signal works
    df['long_quote_quantity'] = df['long_qty']  # ca returns base, but z-score is scale-invariant
    df['short_quote_quantity'] = df['short_qty']
    return df


def b4_ca_triggers(df_15m: pd.DataFrame, liq_signal: pd.DataFrame, *,
                    z_threshold: float = 2.0,
                    cooldown_bars: int = 4 * 6,
                    atr_mult_stop: float = 2.0,
                    target_r: float = 2.0,
                    ) -> pd.DataFrame:
    """B4 trigger logic using ca_liquidations signal columns."""
    df = df_15m.copy()
    df['atr'] = compute_atr(df, period=14)
    df['long_liq_z'] = liq_signal['long_liq_z'].reindex(df.index, method='ffill', limit=4)
    df['short_liq_z'] = liq_signal['short_liq_z'].reindex(df.index, method='ffill', limit=4)

    short_mask = df['long_liq_z'] > z_threshold
    long_mask = df['short_liq_z'] > z_threshold

    rows = []
    last_trigger_idx = -10**9
    for i in range(len(df)):
        if i - last_trigger_idx < cooldown_bars:
            continue
        if pd.isna(df['atr'].iloc[i]):
            continue
        if pd.isna(df['long_liq_z'].iloc[i]) and pd.isna(df['short_liq_z'].iloc[i]):
            continue
        entry = float(df['close'].iloc[i])
        atr_val = float(df['atr'].iloc[i])
        risk = atr_val * atr_mult_stop
        if risk <= 0: continue
        ts = df.index[i]
        if bool(short_mask.iloc[i]) if not pd.isna(short_mask.iloc[i]) else False:
            rows.append({'ts': ts, 'direction': 'short', 'entry': entry,
                         'stop': entry + risk, 'target': entry - risk * target_r,
                         'long_liq_z': float(df['long_liq_z'].iloc[i]),
                         'atr': atr_val})
            last_trigger_idx = i
        elif bool(long_mask.iloc[i]) if not pd.isna(long_mask.iloc[i]) else False:
            rows.append({'ts': ts, 'direction': 'long', 'entry': entry,
                         'stop': entry - risk, 'target': entry + risk * target_r,
                         'short_liq_z': float(df['short_liq_z'].iloc[i]),
                         'atr': atr_val})
            last_trigger_idx = i
    return pd.DataFrame(rows)


def main():
    print(f'DB: {DB}')

    print('\nLoading Coinalyze liquidations...')
    ca_liq = load_ca_liquidations('BTC')
    print(f'  ca_liquidations BTC: {len(ca_liq):,} rows '
          f'{ca_liq.index.min()} -> {ca_liq.index.max()}')
    span_y = (ca_liq.index.max() - ca_liq.index.min()).total_seconds() / (365.25*86400)
    print(f'  span: {span_y:.2f} years')

    if len(ca_liq) < 1000:
        print('Not enough Coinalyze data — backfill may still be running.')
        return

    print('\nLoading BTC 15m + computing squeeze signal...')
    df = load_btc_15m()
    liq_sig = compute_squeeze_signal(ca_liq)
    valid = liq_sig['long_liq_z'].dropna()
    print(f'  long_liq_z range: [{valid.min():.1f}, {valid.max():.1f}]  std={valid.std():.2f}')

    # === B4 alone on full multi-year ===
    print('\n=== B4 alone (multi-year, ca_liquidations) ===')
    for z in (1.5, 2.0, 2.5, 3.0):
        label = f'B4ca_z{z}'
        trigs = b4_ca_triggers(df, liq_sig, z_threshold=z)
        if trigs.empty:
            print(f'  {label}: 0 triggers'); continue
        trigs_r = measure_r_outcomes(trigs, df)
        s = summarize_triggers(trigs_r, label=label)
        cov = chento_coverage(trigs_r, asset='BTCUSDT')
        print(f'  {label}: n={s["n"]:>4d} /yr={s["trades_per_year"]:>5.1f}  '
               f'meanR={s["mean_R"]:+.3f} WR={s["win_rate"]:.0%}  '
               f'precision={cov.get("trigger_to_chento_loose_rate",0):.1%}  '
               f'coverage={cov.get("chento_to_trigger_loose_rate",0):.1%}  '
               f'annual={s["implied_annual_pct"]:+.1f}%')
        # OOS split
        is_set = trigs_r[trigs_r['ts'] <= pd.Timestamp('2024-12-31', tz='UTC')]
        oos_set = trigs_r[trigs_r['ts'] >= pd.Timestamp('2025-01-01', tz='UTC')]
        is_s = summarize_triggers(is_set, label='IS')
        oos_s = summarize_triggers(oos_set, label='OOS')
        print(f'      IS:  n={is_s.get("n",0):>4d} meanR={is_s.get("mean_R",0):+.3f} WR={is_s.get("win_rate",0):.0%}')
        print(f'      OOS: n={oos_s.get("n",0):>4d} meanR={oos_s.get("mean_R",0):+.3f} WR={oos_s.get("win_rate",0):.0%}')

    # === Composite with B4ca as 4th signal ===
    print('\n=== Quad composite: B1 ∩ B5 ∩ B7-align ∩ B4ca ===')
    df_b1 = compute_moneyflow_signal(df)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    lsr = load_lsr('BTC')
    lsr_sig = compute_lsr_extremes(lsr)
    b5 = b5_triggers(df, lsr_sig)
    df_b7 = compute_multitf_cvd(df)
    b7_align = b7_alignment_triggers(df_b7, z_threshold=2.0)
    b4ca = b4_ca_triggers(df, liq_sig, z_threshold=2.0)

    c_b1_b5 = intersect_triggers(b1, b5)
    c_triple = intersect_triggers(c_b1_b5, b7_align)
    c_quad = intersect_triggers(c_triple, b4ca)

    print(f'  Triple B1∩B5∩B7align: {len(c_triple)} triggers')
    print(f'  Quad +B4ca: {len(c_quad)} triggers')

    if not c_quad.empty:
        trigs_r = measure_r_outcomes(c_quad, df)
        s = summarize_triggers(trigs_r, label='B1∩B5∩B7align∩B4ca')
        cov = chento_coverage(trigs_r, asset='BTCUSDT')
        print(f'  Quad: n={s["n"]:>4d} /yr={s["trades_per_year"]:>5.1f}  '
               f'meanR={s["mean_R"]:+.3f} WR={s["win_rate"]:.0%}  '
               f'precision={cov.get("trigger_to_chento_loose_rate",0):.1%}  '
               f'coverage={cov.get("chento_to_trigger_loose_rate",0):.1%}  '
               f'annual={s["implied_annual_pct"]:+.1f}%')
        is_set = trigs_r[trigs_r['ts'] <= pd.Timestamp('2024-12-31', tz='UTC')]
        oos_set = trigs_r[trigs_r['ts'] >= pd.Timestamp('2025-01-01', tz='UTC')]
        is_s = summarize_triggers(is_set, label='IS')
        oos_s = summarize_triggers(oos_set, label='OOS')
        print(f'    IS:  n={is_s.get("n",0):>4d} meanR={is_s.get("mean_R",0):+.3f} WR={is_s.get("win_rate",0):.0%}')
        print(f'    OOS: n={oos_s.get("n",0):>4d} meanR={oos_s.get("mean_R",0):+.3f} WR={oos_s.get("win_rate",0):.0%}')

    print('\nWrote (in script output above)')


if __name__ == '__main__':
    main()

"""Phase 1 of the OI accumulation + flush study (plan joyful-singing-leaf.md,
2026-06-05).

Thesis (causal mechanism):
  When BTC open interest builds rapidly (positioning crowded with leveraged
  longs OR shorts), a flush event (rapid OI drop, typically driven by
  liquidation cascade) marks the end of one-sided positioning. After the
  flush, price tends to mean-revert in the OPPOSITE direction of the
  flush (longs flushed in price drop -> bounce UP; shorts flushed in
  price rise -> fade DOWN).

Method:
  1. Load cd_open_interest hourly + cd_futures_ohlcv hourly (4.3y window).
  2. Compute rolling OI % change at N-hour windows {2h, 4h, 6h, 12h}.
  3. Identify flush events: oi_change_4h <= threshold (-2%, -3%, -5%).
  4. Classify flush direction:
       - "long_flush": flush coincides with price drop (price_change in
         flush window <= -0.5%)
       - "short_flush": flush + price rise (price_change >= +0.5%)
  5. Forward-walk: from flush detection, measure return at horizons
     {4h, 24h, 72h, 168h}.
  6. Compare to baseline: random bars in same window (no flush).

Decision: if forward-return mean over horizon is materially better than
baseline (e.g., >2x baseline magnitude with correct sign), the signal
exists and Phase 2 (trade design + backtest) is justified.

Usage:
  python -m studies.notebooks.oi_flush.research
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

FLUSH_THRESHOLDS = (-0.02, -0.03, -0.05)   # OI change at 4h window
PRICE_DIR_THRESHOLD = 0.005                  # 0.5% to classify direction
FORWARD_HORIZONS_H = (4, 24, 72, 168)
COOLDOWN_HOURS = 24                          # don't double-count same flush


def load_oi_and_price() -> pd.DataFrame:
    con = sqlite3.connect(str(DB))
    oi = pd.read_sql("""
        SELECT timestamp, oi_close, oi_value_close
        FROM cd_open_interest ORDER BY timestamp
    """, con)
    px = pd.read_sql("""
        SELECT timestamp, open, high, low, close
        FROM cd_futures_ohlcv ORDER BY timestamp
    """, con)
    con.close()
    oi['ts'] = pd.to_datetime(oi['timestamp'], unit='s', utc=True).dt.as_unit('ns')
    px['ts'] = pd.to_datetime(px['timestamp'], unit='s', utc=True).dt.as_unit('ns')
    oi = oi.set_index('ts').drop(columns='timestamp')
    px = px.set_index('ts').drop(columns='timestamp')
    # Inner-join on hourly index
    df = px.join(oi, how='inner')
    return df


def main():
    print('Loading hourly OI + BTC OHLC...')
    df = load_oi_and_price()
    print(f'  {len(df)} hourly bars, '
           f'span {df.index.min()} -> {df.index.max()}')

    closes = df['close'].values
    oi = df['oi_close'].values
    n = len(df)

    # Compute rolling pct change over various windows
    print('Computing rolling OI + price pct changes...')
    for h in (2, 4, 6, 12, 24, 72):
        df[f'oi_chg_{h}h'] = df['oi_close'].pct_change(h)
        df[f'px_chg_{h}h'] = df['close'].pct_change(h)

    # Forward returns
    print('Computing forward returns at each horizon...')
    for h in FORWARD_HORIZONS_H:
        df[f'fwd_ret_{h}h'] = df['close'].shift(-h) / df['close'] - 1

    # ─── Sweep flush thresholds ────────────────────────────────────────
    print('\n' + '=' * 110)
    print('=== FLUSH EVENT ANALYSIS ===')
    print('=' * 110)

    all_results = {}
    for thresh in FLUSH_THRESHOLDS:
        print(f'\n--- Flush threshold: oi_chg_4h <= {thresh*100:.1f}% ---')
        flush_mask = df['oi_chg_4h'] <= thresh
        # Apply cooldown to avoid double-counting same flush
        flush_idx = np.flatnonzero(flush_mask.values)
        kept = []
        last = -10**9
        for i in flush_idx:
            if i - last < COOLDOWN_HOURS:
                continue
            kept.append(i)
            last = i
        events_df = df.iloc[kept].copy()
        events_df['_kept_idx'] = kept

        # Classify by direction
        long_flush_mask = events_df['px_chg_4h'] <= -PRICE_DIR_THRESHOLD
        short_flush_mask = events_df['px_chg_4h'] >= PRICE_DIR_THRESHOLD
        long_flushes = events_df[long_flush_mask]
        short_flushes = events_df[short_flush_mask]
        neutral_flushes = events_df[~long_flush_mask & ~short_flush_mask]
        print(f'  Events: {len(events_df)} '
               f'(long_flush: {len(long_flushes)}, '
               f'short_flush: {len(short_flushes)}, '
               f'neutral: {len(neutral_flushes)})')

        # Baseline: ALL hourly bars (not just flush)
        baseline = df[df[f'oi_chg_4h'].notna()]

        print(f'\n  {"horizon":<8s} {"event":<14s} {"n":>5s} '
               f'{"mean_ret%":>10s} {"WR":>6s} {"sharpe-like":>12s} '
               f'{"vs_baseline_uplift":>20s}')

        thresh_results = {}
        for h in FORWARD_HORIZONS_H:
            col = f'fwd_ret_{h}h'
            base_mean = baseline[col].mean()
            base_std = baseline[col].std()
            base_sharpe = base_mean / base_std if base_std > 0 else 0

            for label, events in (('long_flush', long_flushes),
                                    ('short_flush', short_flushes),
                                    ('neutral', neutral_flushes)):
                if len(events) == 0:
                    continue
                ev_rets = events[col].dropna()
                if len(ev_rets) < 5:
                    continue
                ev_mean = ev_rets.mean()
                ev_std = ev_rets.std()
                ev_sharpe = ev_mean / ev_std if ev_std > 0 else 0
                # For long_flush, we expect BOUNCE UP (positive return)
                # For short_flush, we expect FADE DOWN (negative return)
                # WR: long_flush wins if fwd_ret > 0; short_flush wins if < 0
                if label == 'long_flush':
                    wr = (ev_rets > 0).mean()
                elif label == 'short_flush':
                    wr = (ev_rets < 0).mean()
                else:
                    wr = (ev_rets > 0).mean()
                uplift_pp = (ev_mean - base_mean) * 100
                print(f'  {h}h{"":<5s} {label:<14s} {len(ev_rets):>5d} '
                      f'{ev_mean*100:>+9.3f}% {wr*100:>5.1f}% '
                      f'{ev_sharpe:>+11.3f} '
                      f'{uplift_pp:>+15.3f}pp')
                thresh_results[f'{h}h_{label}'] = {
                    'n': int(len(ev_rets)),
                    'mean_ret_pct': round(float(ev_mean)*100, 4),
                    'wr_pct': round(float(wr)*100, 1),
                    'sharpe_like': round(float(ev_sharpe), 3),
                    'uplift_pp_vs_baseline': round(float(uplift_pp), 3),
                }
            print()
        all_results[f'thresh_{int(thresh*100)}pct'] = thresh_results

    # ─── Verdict ──────────────────────────────────────────────────────
    print('\n' + '=' * 110)
    print('=== VERDICT ===')
    print('=' * 110)
    # Look at the strongest signal: 24h horizon at -3% threshold
    key = 'thresh_-3pct'
    if key in all_results:
        long_24h = all_results[key].get('24h_long_flush', {})
        short_24h = all_results[key].get('24h_short_flush', {})
        long_mean = long_24h.get('mean_ret_pct', 0)
        short_mean = short_24h.get('mean_ret_pct', 0)
        print(f'\n  At threshold -3%, 24h horizon:')
        print(f'    long_flush (expect bounce UP): '
               f'mean_ret {long_mean:+.3f}%, '
               f'WR {long_24h.get("wr_pct", 0):.1f}%, n={long_24h.get("n", 0)}')
        print(f'    short_flush (expect fade DOWN): '
               f'mean_ret {short_mean:+.3f}%, '
               f'WR {short_24h.get("wr_pct", 0):.1f}%, n={short_24h.get("n", 0)}')
        # For long_flush thesis to work, mean_ret should be POSITIVE
        # For short_flush thesis to work, mean_ret should be NEGATIVE
        long_works = long_mean > 0.5 and long_24h.get('wr_pct', 0) > 55
        short_works = short_mean < -0.5 and short_24h.get('wr_pct', 0) > 55
        if long_works and short_works:
            print(f'\n  PASS: Both directions show mean-reversion bounce. '
                  f'Proceed to Phase 2 (trade design).')
        elif long_works:
            print(f'\n  PARTIAL: Only long_flush bounce works. Design '
                  f'long-only sleeve.')
        elif short_works:
            print(f'\n  PARTIAL: Only short_flush fade works. Design '
                  f'short-only sleeve.')
        else:
            print(f'\n  WEAK / FAIL: Signal too weak or wrong sign. '
                  f'Consider longer accumulation requirements or different '
                  f'threshold.')

    out_path = OUT_DIR / 'oi_flush_phase1_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'asset': 'BTC',
            'window': [str(df.index.min()), str(df.index.max())],
            'n_bars': int(n),
            'flush_thresholds': list(FLUSH_THRESHOLDS),
            'price_dir_threshold': PRICE_DIR_THRESHOLD,
            'forward_horizons_h': list(FORWARD_HORIZONS_H),
            'all_results': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

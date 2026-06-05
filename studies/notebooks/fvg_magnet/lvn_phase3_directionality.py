"""Phase 3 of the LVN study: directionality of LVN traversal.

Phase 2 established that LVN traversal time is 55% faster than HVN dwell
time. But "fast" isn't "directional" — if price entering an LVN is equally
likely to exit at the opposite edge (continuation) or the entry edge
(reversal), the speed is real but un-profitable.

This script tests: GIVEN an LVN entry event, what's the probability the
exit is at the OPPOSITE edge (continuation) vs the ENTRY edge (reversal)?

Method:
  1. Build rolling 7d VP + identify LVN zones (same as Phase 2).
  2. For each LVN zone identified at rebalance time t_R, walk forward
     looking for ENTRY events — a bar where prev bar was OUTSIDE the zone
     and current bar's close is INSIDE.
  3. From each entry event, walk forward until EXIT — a bar fully outside.
  4. Classify entry edge (top / bottom) and exit edge (top / bottom).
  5. Continuation = entry edge != exit edge.
  6. Aggregate continuation rate; if >60%, LVN gives a directional edge
     and Phase 4 (trade design) is justified.

Decision:
  - continuation rate >= 60% AND symmetric (both directions work) AND
    statistically robust (n large): directional signal real, design trade
  - continuation rate ~50%: speed real but un-profitable; close hypothesis
  - asymmetric (one direction works, other doesn't): partial signal,
    investigate which regime
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

VP_WINDOW_DAYS = 7
VP_WINDOW_BARS = VP_WINDOW_DAYS * 96
N_BINS = 50
REBALANCE_BARS = 96
LVN_PERCENTILE = 25
MAX_LOOKAHEAD_BARS = 672   # 7d max — same as Phase 2 horizon


def load_btc_15m() -> pd.DataFrame:
    con = sqlite3.connect(str(DB))
    df = pd.read_sql("""
        SELECT timestamp, open, high, low, close, volume
        FROM cd_futures_15m
        ORDER BY timestamp
    """, con)
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True).dt.as_unit('ns')
    return df.set_index('ts').drop(columns='timestamp')


def compute_vp_at(typ: np.ndarray, vol: np.ndarray,
                   idx_end: int) -> np.ndarray | None:
    start = idx_end - VP_WINDOW_BARS
    if start < 0:
        return None
    prices = typ[start:idx_end]
    vols = vol[start:idx_end]
    if np.isnan(prices).any() or vols.sum() <= 0:
        return None
    lo, hi = float(prices.min()), float(prices.max())
    if hi <= lo:
        return None
    bin_edges = np.linspace(lo, hi, N_BINS + 1)
    bin_idx = np.clip(((prices - lo) / (hi - lo) * N_BINS).astype(int),
                        0, N_BINS - 1)
    bin_vols = np.zeros(N_BINS)
    for k in range(len(prices)):
        bin_vols[bin_idx[k]] += vols[k]
    return bin_vols, bin_edges


def identify_lvn_zones(bin_vols: np.ndarray,
                        bin_edges: np.ndarray) -> list[tuple[float, float]]:
    threshold = np.percentile(bin_vols, LVN_PERCENTILE)
    mask = bin_vols <= threshold
    zones = []
    i = 0
    while i < N_BINS:
        if mask[i]:
            j = i
            while j < N_BINS and mask[j]:
                j += 1
            zones.append((float(bin_edges[i]), float(bin_edges[j])))
            i = j
        else:
            i += 1
    return zones


def main():
    print('Loading BTC 15m OHLC...')
    df = load_btc_15m()
    print(f'  {len(df)} bars, span {df.index.min()} -> {df.index.max()}')

    typ = ((df['high'] + df['low']) / 2).values
    vol = df['volume'].values
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    n_bars = len(df)

    print('Identifying LVN zones at each rebalance point...')
    rebalance_zones: list[tuple[int, list[tuple[float, float]]]] = []
    for idx_end in range(VP_WINDOW_BARS, n_bars, REBALANCE_BARS):
        vp = compute_vp_at(typ, vol, idx_end)
        if vp is None:
            continue
        bin_vols, bin_edges = vp
        zones = identify_lvn_zones(bin_vols, bin_edges)
        rebalance_zones.append((idx_end, zones))
    print(f'  {len(rebalance_zones)} rebalance points')

    # For each rebalance point's zones, walk forward to find entry events.
    # Stop at next rebalance (zones change there).
    print('\nWalking forward to find LVN entry/exit events...')
    events = []
    for ri, (idx_R, zones) in enumerate(rebalance_zones):
        next_R = (rebalance_zones[ri + 1][0]
                   if ri + 1 < len(rebalance_zones) else n_bars)
        # Limit lookahead — we want zone identity stable during traversal
        end_walk = min(idx_R + REBALANCE_BARS, next_R, n_bars - 1)
        for zone_low, zone_high in zones:
            # Skip degenerate zones (zero width)
            if zone_high <= zone_low:
                continue
            # State tracker: where was the previous bar?
            for i in range(idx_R + 1, end_walk):
                # Was previous bar OUTSIDE zone, and current bar's close INSIDE?
                prev_close = closes[i - 1]
                curr_close = closes[i]
                if zone_low <= prev_close <= zone_high:
                    continue  # was already inside
                if not (zone_low <= curr_close <= zone_high):
                    continue  # still outside
                # Entry event: prev was outside, current is inside
                if prev_close < zone_low:
                    entry_edge = 'bottom'  # came from below
                else:
                    entry_edge = 'top'  # came from above

                # Walk until exit
                exit_edge = None
                exit_bars = None
                exit_distance_pct = None
                for j in range(i + 1, min(i + MAX_LOOKAHEAD_BARS + 1, n_bars)):
                    if lows[j] > zone_high:
                        exit_edge = 'top'
                        exit_bars = j - i
                        exit_distance_pct = (closes[j] - zone_high) / closes[j]
                        break
                    if highs[j] < zone_low:
                        exit_edge = 'bottom'
                        exit_bars = j - i
                        exit_distance_pct = (zone_low - closes[j]) / closes[j]
                        break
                if exit_edge is None:
                    continue  # didn't exit in window
                events.append({
                    'idx_entry': i,
                    'zone_low': zone_low, 'zone_high': zone_high,
                    'zone_width_pct': (zone_high - zone_low) / closes[i],
                    'entry_edge': entry_edge,
                    'exit_edge': exit_edge,
                    'exit_bars': exit_bars,
                    'exit_distance_pct': exit_distance_pct,
                    'continuation': entry_edge != exit_edge,
                })

    rep = pd.DataFrame(events)
    print(f'  Total entry/exit events: {len(rep)}')

    # ─── Headline ─────────────────────────────────────────────────────────
    print('\n' + '=' * 95)
    print('=== HEADLINE: Continuation rate (exit at opposite edge of entry) ===')
    print('=' * 95)
    cont_rate = rep['continuation'].mean()
    print(f'\n  Total events: {len(rep)}')
    print(f'  Continuation rate: {cont_rate*100:.2f}%')
    print(f'  Reversal rate: {(1-cont_rate)*100:.2f}%')

    # ─── By entry direction ───────────────────────────────────────────────
    print('\n=== BY ENTRY DIRECTION ===')
    print(f'\n  {"entry":<8s} {"n":>6s} {"cont%":>8s} {"rev%":>8s} '
          f'{"avg_cont_bars":>14s} {"avg_rev_bars":>14s}')
    by_dir = {}
    for d in ('bottom', 'top'):
        sub = rep[rep['entry_edge'] == d]
        if len(sub) == 0:
            continue
        cont = sub['continuation'].mean()
        avg_cont = sub[sub['continuation']]['exit_bars'].mean()
        avg_rev = sub[~sub['continuation']]['exit_bars'].mean()
        print(f'  {d:<8s} {len(sub):>6d} {cont*100:>7.2f}% '
              f'{(1-cont)*100:>7.2f}% {avg_cont:>13.1f} {avg_rev:>13.1f}')
        by_dir[d] = {
            'n': int(len(sub)),
            'cont_rate_pct': round(float(cont)*100, 2),
            'avg_cont_bars': round(float(avg_cont), 1)
                              if not np.isnan(avg_cont) else None,
            'avg_rev_bars': round(float(avg_rev), 1)
                             if not np.isnan(avg_rev) else None,
        }

    # ─── Continuation distance distribution ─────────────────────────────
    print('\n=== EXIT-DISTANCE-PAST-ZONE distribution (pct of price) ===')
    cont_events = rep[rep['continuation']]
    rev_events = rep[~rep['continuation']]
    print(f'\n  Continuation events (n={len(cont_events)}):')
    for q in (0.25, 0.50, 0.75, 0.90):
        v = cont_events['exit_distance_pct'].quantile(q) * 100
        print(f'    p{int(q*100):>2d}: {v:.3f}%')
    print(f'  Reversal events (n={len(rev_events)}):')
    for q in (0.25, 0.50, 0.75, 0.90):
        v = rev_events['exit_distance_pct'].quantile(q) * 100
        print(f'    p{int(q*100):>2d}: {v:.3f}%')

    # ─── By zone width ───────────────────────────────────────────────────
    print('\n=== BY ZONE WIDTH ===')
    print(f'\n  {"width bucket":<14s} {"n":>6s} {"cont%":>8s}')
    width_buckets = [
        ('<0.5%', 0, 0.005),
        ('0.5-1%', 0.005, 0.01),
        ('1-2%', 0.01, 0.02),
        ('2-5%', 0.02, 0.05),
        ('5%+', 0.05, np.inf),
    ]
    for label, lo, hi in width_buckets:
        sub = rep[(rep['zone_width_pct'] >= lo) & (rep['zone_width_pct'] < hi)]
        if len(sub) == 0:
            continue
        cont = sub['continuation'].mean()
        print(f'  {label:<14s} {len(sub):>6d} {cont*100:>7.2f}%')

    # ─── Verdict ──────────────────────────────────────────────────────────
    print('\n' + '=' * 95)
    print('=== VERDICT ===')
    print('=' * 95)
    is_directional = cont_rate >= 0.60
    is_symmetric = (by_dir.get('bottom', {}).get('cont_rate_pct', 0) >= 55
                     and by_dir.get('top', {}).get('cont_rate_pct', 0) >= 55)
    print(f'\n  Continuation rate: {cont_rate*100:.2f}% '
           f'({"directional" if is_directional else "not directional"})')
    print(f'  Symmetric: {is_symmetric}')
    if is_directional and is_symmetric:
        print(f'\n  CONCLUSION: LVN traversal IS directional + symmetric — '
              f'real edge. Proceed to Phase 4 (trade design).')
    elif is_directional and not is_symmetric:
        print(f'\n  CONCLUSION: LVN traversal is directional in ONE direction only. '
              f'Partial signal — design asymmetric entries.')
    elif cont_rate >= 0.50:
        print(f'\n  CONCLUSION: LVN traversal is mildly directional but '
              f'not enough to overcome cost. Speed real, profit weak.')
    else:
        print(f'\n  CONCLUSION: LVN traversal REVERSES more than continues. '
              f'Price tends to bounce back into entry edge — different setup '
              f'(fade the LVN entry).')

    out_path = OUT_DIR / 'lvn_phase3_directionality_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'asset': 'BTC', 'cadence': '15m',
            'n_events': int(len(rep)),
            'continuation_rate_pct': round(float(cont_rate)*100, 2),
            'by_entry_direction': by_dir,
            'continuation_distance_p50_pct': round(
                float(cont_events['exit_distance_pct'].quantile(0.5)) * 100, 3),
            'reversal_distance_p50_pct': round(
                float(rev_events['exit_distance_pct'].quantile(0.5)) * 100, 3),
            'is_directional': bool(is_directional),
            'is_symmetric': bool(is_symmetric),
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

"""Phase 2 of the magnet study: Volume Profile Low-Volume Nodes (LVN).

Two testable hypotheses about LVNs:

  Test A (LVN as MAGNET): For each newly identified LVN zone, does price
    reach (touch) the zone within N bars at a rate above a control level
    at the same distance? This is the "fair value attraction" claim —
    weak prior support.

  Test B (LVN as TRANSITION ZONE): When price is INSIDE an LVN zone, does
    it exit faster than when price is inside an HVN zone of comparable
    width? This is the proper Auction Market Theory claim — "no resting
    volume = no resistance = fast traverse".

Approach:
  1. Daily rebalance: every 24h compute rolling 7d volume profile (96 bins).
  2. Identify LVN bins (volume <= 25th percentile) and HVN bins (volume
     >= 75th percentile). Group contiguous bins into zones.
  3. Test A: for each new LVN zone at rebalance time t, time-to-first-touch
     vs random-level control at same distance from close.
  4. Test B: for every bar where price is inside an LVN zone, count bars
     until exit; same for HVN zones. Compare medians.

Decision: if either test shows >1.5x effect (faster exit for LVN, or
higher touch rate vs control), the mechanism is real and worth designing
a trade signal around.
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
VP_WINDOW_BARS = VP_WINDOW_DAYS * 96  # 15m
N_BINS = 50
REBALANCE_BARS = 96  # every 24h
LVN_PERCENTILE = 25
HVN_PERCENTILE = 75

HORIZONS = {
    '1h':   4,
    '4h':   16,
    '24h':  96,
    '72h':  288,
    '168h': 672,
}


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
                   idx_end: int) -> tuple[np.ndarray, np.ndarray, float, float] | None:
    """Compute volume profile for bars [idx_end - VP_WINDOW_BARS, idx_end).
    Returns (bin_volumes, bin_edges, lo, hi) or None if invalid."""
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
    return bin_vols, bin_edges, lo, hi


def identify_zones(bin_vols: np.ndarray, bin_edges: np.ndarray, *,
                    is_lvn: bool) -> list[tuple[float, float]]:
    """Return list of (zone_low, zone_high) for contiguous low-volume
    (or high-volume) bin runs."""
    pct = LVN_PERCENTILE if is_lvn else HVN_PERCENTILE
    threshold = np.percentile(bin_vols, pct)
    mask = (bin_vols <= threshold) if is_lvn else (bin_vols >= threshold)
    zones = []
    i = 0
    while i < N_BINS:
        if mask[i]:
            j = i
            while j < N_BINS and mask[j]:
                j += 1
            zone_low = float(bin_edges[i])
            zone_high = float(bin_edges[j])
            zones.append((zone_low, zone_high))
            i = j
        else:
            i += 1
    return zones


def measure_touch(highs: np.ndarray, lows: np.ndarray, *,
                   start_idx: int, target_low: float, target_high: float,
                   max_lookahead: int) -> int | None:
    """Forward-walk and return bars-to-touch the zone (bar enters
    [target_low, target_high]). None if never touched in window."""
    n = len(highs)
    end = min(start_idx + max_lookahead + 1, n)
    for j in range(start_idx + 1, end):
        if highs[j] >= target_low and lows[j] <= target_high:
            return j - start_idx
    return None


def measure_exit(highs: np.ndarray, lows: np.ndarray, *,
                  start_idx: int, zone_low: float, zone_high: float,
                  max_lookahead: int) -> int | None:
    """Forward-walk and return bars-until-bar-fully-outside-zone (low > zone_high
    OR high < zone_low). None if still inside at end of window."""
    n = len(highs)
    end = min(start_idx + max_lookahead + 1, n)
    for j in range(start_idx + 1, end):
        if lows[j] > zone_high or highs[j] < zone_low:
            return j - start_idx
    return None


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
    max_horizon = max(HORIZONS.values())

    # ─── Build rebalanced LVN + HVN zone series ─────────────────────────
    print(f'Computing rolling VP every {REBALANCE_BARS} bars '
          f'(every {REBALANCE_BARS//4}h) over {VP_WINDOW_DAYS}d window...')
    lvn_zones_at: list[tuple[int, list[tuple[float, float]]]] = []  # (idx, [(lo, hi), ...])
    hvn_zones_at: list[tuple[int, list[tuple[float, float]]]] = []
    for idx_end in range(VP_WINDOW_BARS, n_bars, REBALANCE_BARS):
        vp = compute_vp_at(typ, vol, idx_end)
        if vp is None:
            continue
        bin_vols, bin_edges, _, _ = vp
        lvn = identify_zones(bin_vols, bin_edges, is_lvn=True)
        hvn = identify_zones(bin_vols, bin_edges, is_lvn=False)
        lvn_zones_at.append((idx_end, lvn))
        hvn_zones_at.append((idx_end, hvn))
    print(f'  {len(lvn_zones_at)} rebalance points')
    n_lvn_total = sum(len(z) for _, z in lvn_zones_at)
    n_hvn_total = sum(len(z) for _, z in hvn_zones_at)
    print(f'  Total LVN zones identified: {n_lvn_total}, HVN zones: {n_hvn_total}')

    # ─── Test A: LVN as MAGNET ───────────────────────────────────────────
    print('\n' + '=' * 95)
    print('=== TEST A: LVN MAGNET (time-to-touch vs control) ===')
    print('=' * 95)
    lvn_records = []
    for idx_end, zones in lvn_zones_at:
        if idx_end + max_horizon >= n_bars:
            continue
        close_at = closes[idx_end]
        for zone_low, zone_high in zones:
            if zone_low <= close_at <= zone_high:
                continue  # price already inside; not a "magnet" event
            # direction + edge
            if zone_high < close_at:
                edge = zone_high
                distance_pct = (close_at - edge) / close_at
                direction = 'below'
            else:
                edge = zone_low
                distance_pct = (edge - close_at) / close_at
                direction = 'above'
            # LVN touch
            bars_touch_lvn = measure_touch(
                highs, lows, start_idx=idx_end,
                target_low=zone_low, target_high=zone_high,
                max_lookahead=max_horizon)
            # Control: opposite-side point level at same distance_pct
            if direction == 'below':
                ctrl_level = close_at * (1.0 + distance_pct)
                ctrl_dir = 'above'
            else:
                ctrl_level = close_at * (1.0 - distance_pct)
                ctrl_dir = 'below'
            # Point-level touch (any bar high >= level for above; low <= level for below)
            n = len(highs)
            end = min(idx_end + max_horizon + 1, n)
            bars_touch_ctrl = None
            for j in range(idx_end + 1, end):
                if ctrl_dir == 'above' and highs[j] >= ctrl_level:
                    bars_touch_ctrl = j - idx_end; break
                if ctrl_dir == 'below' and lows[j] <= ctrl_level:
                    bars_touch_ctrl = j - idx_end; break
            lvn_records.append({
                'idx': idx_end, 'direction': direction,
                'distance_pct': distance_pct,
                'zone_width_pct': (zone_high - zone_low) / close_at,
                'bars_to_touch_lvn': bars_touch_lvn,
                'bars_to_touch_ctrl': bars_touch_ctrl,
            })
    rep_a = pd.DataFrame(lvn_records)
    print(f'\n  LVN events (price OUTSIDE zone): {len(rep_a)}')
    print(f'  Distance distribution (pct of price):')
    for q in (0.10, 0.25, 0.50, 0.75, 0.90):
        v = rep_a['distance_pct'].quantile(q) * 100
        print(f'    p{int(q*100):>2d}: {v:.3f}%')
    print(f'\n  Touch rates per horizon:')
    print(f'  {"horizon":<8s} {"LVN_rate":>10s} {"ctrl_rate":>11s} {"uplift":>8s} {"delta_pp":>10s}')
    test_a_summary = {}
    for name, h_bars in HORIZONS.items():
        lvn_rate = ((rep_a['bars_to_touch_lvn'].notna())
                     & (rep_a['bars_to_touch_lvn'] <= h_bars)).mean()
        ctrl_rate = ((rep_a['bars_to_touch_ctrl'].notna())
                      & (rep_a['bars_to_touch_ctrl'] <= h_bars)).mean()
        uplift = lvn_rate / ctrl_rate if ctrl_rate > 0 else 0
        delta_pp = (lvn_rate - ctrl_rate) * 100
        print(f'  {name:<8s} {lvn_rate*100:>9.2f}% {ctrl_rate*100:>10.2f}% '
              f'{uplift:>7.2f}x {delta_pp:>+8.2f}')
        test_a_summary[name] = {
            'lvn_rate_pct': round(lvn_rate*100, 2),
            'ctrl_rate_pct': round(ctrl_rate*100, 2),
            'uplift': round(uplift, 2),
            'delta_pp': round(delta_pp, 2),
        }

    # ─── Test B: LVN as TRANSITION ZONE ─────────────────────────────────
    print('\n' + '=' * 95)
    print('=== TEST B: LVN TRANSITION ZONE (bars-to-exit, LVN vs HVN) ===')
    print('=' * 95)

    # For each bar in the time series, find the most recent rebalance point;
    # check if price is inside an LVN or HVN zone there. If yes AND the same
    # zone holds for the bar (price hasn't moved out yet), measure bars-to-exit.

    # Build a fast lookup: for each bar idx, which is the most recent
    # rebalance idx?
    rebalance_idxs = np.array([r[0] for r in lvn_zones_at])
    lvn_idx_to_zones = dict(lvn_zones_at)
    hvn_idx_to_zones = dict(hvn_zones_at)

    lvn_exit_records = []
    hvn_exit_records = []
    for i in range(VP_WINDOW_BARS, n_bars - max_horizon):
        # Most recent rebalance at or before i
        pos = np.searchsorted(rebalance_idxs, i, side='right') - 1
        if pos < 0:
            continue
        rebal_idx = int(rebalance_idxs[pos])
        bar_low = lows[i]; bar_high = highs[i]; bar_close = closes[i]
        # LVN entry: bar_close inside an LVN zone
        for zone_low, zone_high in lvn_idx_to_zones[rebal_idx]:
            if zone_low <= bar_close <= zone_high:
                exit_bars = measure_exit(
                    highs, lows, start_idx=i,
                    zone_low=zone_low, zone_high=zone_high,
                    max_lookahead=max_horizon)
                if exit_bars is not None:
                    lvn_exit_records.append({
                        'zone_width_pct': (zone_high - zone_low) / bar_close,
                        'exit_bars': exit_bars,
                    })
                break  # only one zone matters
        for zone_low, zone_high in hvn_idx_to_zones[rebal_idx]:
            if zone_low <= bar_close <= zone_high:
                exit_bars = measure_exit(
                    highs, lows, start_idx=i,
                    zone_low=zone_low, zone_high=zone_high,
                    max_lookahead=max_horizon)
                if exit_bars is not None:
                    hvn_exit_records.append({
                        'zone_width_pct': (zone_high - zone_low) / bar_close,
                        'exit_bars': exit_bars,
                    })
                break

    rep_lvn = pd.DataFrame(lvn_exit_records)
    rep_hvn = pd.DataFrame(hvn_exit_records)
    print(f'\n  Inside-LVN bar-events: {len(rep_lvn)}')
    print(f'  Inside-HVN bar-events: {len(rep_hvn)}')
    print(f'\n  Zone width distribution (pct of price):')
    for label, rep in (('LVN', rep_lvn), ('HVN', rep_hvn)):
        print(f'    {label} median width: {rep["zone_width_pct"].median()*100:.3f}%')

    print(f'\n  Bars-to-exit distribution:')
    print(f'  {"quantile":<10s} {"LVN":>8s} {"HVN":>8s} {"ratio (LVN/HVN)":>17s}')
    test_b_summary = {}
    for q in (0.10, 0.25, 0.50, 0.75, 0.90):
        lvn_q = rep_lvn['exit_bars'].quantile(q)
        hvn_q = rep_hvn['exit_bars'].quantile(q)
        ratio = lvn_q / hvn_q if hvn_q > 0 else 0
        print(f'  p{int(q*100):>2d}        {lvn_q:>8.1f} {hvn_q:>8.1f} {ratio:>15.2f}')
        test_b_summary[f'p{int(q*100)}'] = {
            'lvn_bars': float(lvn_q),
            'hvn_bars': float(hvn_q),
            'ratio': round(float(ratio), 2),
        }

    # Width-normalized comparison: compare LVN vs HVN where zone widths
    # are similar
    print(f'\n  Width-matched comparison (LVN width <= median HVN width):')
    hvn_med_width = rep_hvn['zone_width_pct'].median()
    lvn_narrow = rep_lvn[rep_lvn['zone_width_pct'] <= hvn_med_width]
    if len(lvn_narrow) > 100 and len(rep_hvn) > 100:
        print(f'    LVN n (narrow): {len(lvn_narrow)}, HVN n: {len(rep_hvn)}')
        for q in (0.25, 0.50, 0.75):
            lvn_q = lvn_narrow['exit_bars'].quantile(q)
            hvn_q = rep_hvn['exit_bars'].quantile(q)
            ratio = lvn_q / hvn_q if hvn_q > 0 else 0
            print(f'    p{int(q*100):>2d}: LVN={lvn_q:.1f} HVN={hvn_q:.1f} ratio={ratio:.2f}')

    # Verdict
    print('\n' + '=' * 95)
    print('=== VERDICT ===')
    print('=' * 95)
    avg_uplift_a = np.mean([v['uplift'] for v in test_a_summary.values()
                              if v['uplift'] > 0])
    lvn_median = rep_lvn['exit_bars'].median()
    hvn_median = rep_hvn['exit_bars'].median()
    transition_ratio = lvn_median / hvn_median if hvn_median > 0 else 0
    print(f'\n  Test A (LVN MAGNET): avg uplift {avg_uplift_a:.2f}x across horizons')
    print(f'  Test B (LVN TRANSITION): median exit ratio LVN/HVN = {transition_ratio:.2f}')
    print(f'    LVN median time-in-zone: {lvn_median:.1f} bars '
           f'({lvn_median * 15:.0f} min)')
    print(f'    HVN median time-in-zone: {hvn_median:.1f} bars '
           f'({hvn_median * 15:.0f} min)')
    if transition_ratio < 0.67:
        print(f'\n  CONCLUSION: Test B is POSITIVE — LVN transit time is '
              f'{(1-transition_ratio)*100:.0f}% faster than HVN. '
              f'Real microstructure effect.')
    elif transition_ratio < 0.9:
        print(f'\n  CONCLUSION: Test B is WEAKLY POSITIVE ({transition_ratio:.2f}x). '
              f'LVNs are mildly faster than HVNs.')
    else:
        print(f'\n  CONCLUSION: Test B is NULL ({transition_ratio:.2f}x). '
              f'LVN traversal speed not different from HVN.')

    out_path = OUT_DIR / 'lvn_magnet_phase2_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'asset': 'BTC', 'cadence': '15m',
            'window_utc': [str(df.index.min()), str(df.index.max())],
            'vp_window_days': VP_WINDOW_DAYS,
            'n_bins': N_BINS,
            'rebalance_hours': REBALANCE_BARS // 4,
            'lvn_pct_threshold': LVN_PERCENTILE,
            'hvn_pct_threshold': HVN_PERCENTILE,
            'test_a_magnet': test_a_summary,
            'test_b_transition': test_b_summary,
            'avg_uplift_a': round(float(avg_uplift_a), 3),
            'transition_ratio_b': round(float(transition_ratio), 3),
            'n_lvn_events': int(len(rep_lvn)),
            'n_hvn_events': int(len(rep_hvn)),
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

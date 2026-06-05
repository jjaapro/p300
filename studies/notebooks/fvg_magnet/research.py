"""Phase 1 of the FVG magnet study (plan joyful-singing-leaf.md, 2026-06-05).

Question: do prices gravitate toward Fair Value Gaps at a rate above
chance? If yes, the magnet hypothesis from ICT/SMC literature is real and
could drive entry signals.

Method:
  1. Load BTC 15m OHLC over the available 5+ year history.
  2. Detect all FVGs via the existing compute_fvgs() (causal, 3-bar pattern).
  3. For each FVG, measure:
       - distance_pct = |gap_edge - close_at_creation| / close_at_creation
       - bars_to_touch = idx_filled - idx_created (None if unfilled)
  4. Build a CONTROL: for each FVG at idx_created, pick a random level at
     the same distance_pct in the SAME direction. Forward-walk and measure
     whether that random level was touched within the same horizon.
  5. Compare fill/touch rates per horizon (1h, 4h, 24h, 72h, 168h).

Output: per-horizon FVG vs control touch rate, with uplift ratio. Also
breaks down by direction (bull/bear) and distance bucket.

Decision: if FVG fill rate is materially above control (uplift > 1.5x) at
some horizon AND statistically robust (large n), the magnet effect is real
and worth designing trades around. Otherwise close the hypothesis.

Usage:
  python -m studies.notebooks.fvg_magnet.research
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

from studies.notebooks.chento_journal.validation_C5_smc_features import (
    compute_fvgs as _compute_fvgs_upstream,
)


def compute_fvgs_fixed(df: pd.DataFrame) -> list[dict]:
    """Wrap upstream compute_fvgs and RECOMPUTE idx_filled with the correct
    causal sweep (starts at idx_created + 1, not idx_created).

    The upstream function has a bug: the fill loop starts at the creation
    bar, and for a bull FVG zone_high == bar_i.low so `bar_i.low <= zone_high`
    is True at j == idx_created — every FVG is marked filled by its own
    creation bar. That makes any forward-fill study return 100% / 0 bars.
    """
    fvgs = _compute_fvgs_upstream(df)
    highs = df['high'].values
    lows = df['low'].values
    n = len(df)
    for f in fvgs:
        f['idx_filled'] = None  # discard upstream's buggy value
        zl, zh = f['zone_low'], f['zone_high']
        d = f['direction']
        # Causal fill sweep starts the bar AFTER the FVG completes
        for j in range(f['idx_created'] + 1, n):
            if d == 'bull' and lows[j] <= zh:
                f['idx_filled'] = j
                break
            if d == 'bear' and highs[j] >= zl:
                f['idx_filled'] = j
                break
    return fvgs


compute_fvgs = compute_fvgs_fixed  # used by main()

# Horizons in 15m bars
HORIZONS = {
    '1h':   4,
    '4h':   16,
    '24h':  96,
    '72h':  288,
    '168h': 672,
}

# Distance buckets in pct
DISTANCE_BUCKETS = [
    ('0-0.25%', 0.0,    0.0025),
    ('0.25-0.5%', 0.0025, 0.005),
    ('0.5-1%', 0.005, 0.01),
    ('1-2%', 0.01, 0.02),
    ('2-5%', 0.02, 0.05),
    ('5%+',  0.05, np.inf),
]


def load_btc_15m() -> pd.DataFrame:
    con = sqlite3.connect(str(DB))
    df = pd.read_sql("""
        SELECT timestamp, open, high, low, close
        FROM cd_futures_15m
        ORDER BY timestamp
    """, con)
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True).dt.as_unit('ns')
    return df.set_index('ts').drop(columns='timestamp')


def measure_control_touch(highs: np.ndarray, lows: np.ndarray, *,
                            idx_created: int, target_level: float,
                            direction: str, max_lookahead: int) -> int | None:
    """Forward-walk from idx_created+1 up to max_lookahead bars and return
    bars-to-touch. direction='bull' means target is BELOW (price drops to
    touch); direction='bear' means target is ABOVE (price rises to touch).
    Returns None if not touched within window."""
    n = len(highs)
    end = min(idx_created + max_lookahead + 1, n)
    for j in range(idx_created + 1, end):
        if direction == 'bull' and lows[j] <= target_level:
            return j - idx_created
        if direction == 'bear' and highs[j] >= target_level:
            return j - idx_created
    return None


def main():
    print('Loading BTC 15m OHLC...')
    df = load_btc_15m()
    print(f'  {len(df)} bars, span {df.index.min()} -> {df.index.max()}')

    print('Detecting FVGs...')
    fvgs = compute_fvgs(df)
    n_bull = sum(1 for f in fvgs if f['direction'] == 'bull')
    n_bear = sum(1 for f in fvgs if f['direction'] == 'bear')
    print(f'  Total FVGs: {len(fvgs)}  (bull: {n_bull}, bear: {n_bear})')

    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    n_bars = len(df)
    max_horizon = max(HORIZONS.values())

    # Build per-FVG record with distance + bars_to_fill
    print('Computing distance + fill-time per FVG (filtering to those with '
           f'>={max_horizon} bars of forward data)...')
    rng = np.random.default_rng(42)
    records = []
    for f in fvgs:
        idx = f['idx_created']
        if idx + max_horizon >= n_bars:
            continue  # not enough forward data
        close_at = closes[idx]
        # Gap EDGE that's first touched
        if f['direction'] == 'bull':
            gap_edge = f['zone_high']   # bull FVG below; price comes DOWN to zone_high
            distance_pct = (close_at - gap_edge) / close_at
        else:
            gap_edge = f['zone_low']    # bear FVG above; price rises to zone_low
            distance_pct = (gap_edge - close_at) / close_at
        if distance_pct <= 0 or not np.isfinite(distance_pct):
            continue
        # FVG bars_to_fill from already-tracked idx_filled
        bars_to_fill = (f['idx_filled'] - idx) if f['idx_filled'] is not None else None
        if bars_to_fill is not None and bars_to_fill > max_horizon:
            bars_to_fill = None  # treat as not-filled-within-window for our horizons
        # Control: random distance from a uniform distribution that has the
        # same MEDIAN as FVGs (will compute after; for now record distance only)
        records.append({
            'idx': idx,
            'direction': f['direction'],
            'gap_edge': gap_edge,
            'distance_pct': distance_pct,
            'bars_to_fill': bars_to_fill,
        })

    rep = pd.DataFrame(records)
    print(f'  FVGs with sufficient forward data: {len(rep)}')

    # Distance distribution
    print('\nFVG distance_pct distribution (pct of price):')
    for q in (0.10, 0.25, 0.50, 0.75, 0.90):
        v = rep['distance_pct'].quantile(q) * 100
        print(f'  p{int(q*100):>2d}: {v:.3f}%')

    # Control: for each FVG, pick a random level at the SAME distance_pct
    # in the SAME direction. This isolates the "magnet" effect by holding
    # distance + direction constant; only the question of whether THIS
    # specific price level (the FVG edge) is special remains.
    #
    # Construction: take the FVG's distance_pct + direction; the random
    # control is the SAME PRICE LEVEL as the FVG edge. Wait — that's the
    # FVG itself. To create a real control, we need a DIFFERENT random
    # level at the same distance.
    #
    # The right control: at idx_created, pick a random fraction f in
    # [0.5, 1.5] of the FVG's distance_pct, then build the control level
    # at close_at +/- f * distance_pct (in same direction as FVG).
    # This gives a "fuzzy" control near the same distance — not exact, but
    # close enough that the comparison is "is THIS distance + direction
    # special at the FVG edge specifically?".
    #
    # Simpler control: distance_pct + direction held exactly equal; the
    # control level is on the OPPOSITE side at same distance (so a bull
    # FVG at -d% has a control at +d% on the up-side). This compares the
    # asymmetry between "gap I just left below me" and "no gap above me at
    # same distance". If FVGs are TRULY magnetic, the FVG side gets touched
    # more often than the no-FVG side.
    #
    # We'll use this opposite-side control.

    print('\nMeasuring control: opposite-side level at same distance_pct...')
    # bull FVG: target is BELOW close (price drops to touch); control is
    #            ABOVE close at same distance (price rises to touch)
    # bear FVG: target is ABOVE close; control is BELOW close at same dist
    rep['bars_to_touch_control'] = None
    for i, row in rep.iterrows():
        idx = int(row['idx'])
        d = float(row['distance_pct'])
        close_at = closes[idx]
        if row['direction'] == 'bull':
            # FVG below; control above (same distance up)
            control_level = close_at * (1.0 + d)
            control_dir = 'bear'  # price needs to RISE to touch it
        else:
            # bear FVG above; control below
            control_level = close_at * (1.0 - d)
            control_dir = 'bull'  # price needs to DROP to touch it
        bars = measure_control_touch(
            highs, lows, idx_created=idx, target_level=control_level,
            direction=control_dir, max_lookahead=max_horizon)
        rep.at[i, 'bars_to_touch_control'] = bars

    # Compute touch rates per horizon
    print('\n' + '=' * 95)
    print('=== Touch rates by horizon: FVG vs opposite-side control ===')
    print('=' * 95)
    print(f'\n  Horizon  N_FVG  FVG_fill%  ctrl_touch%   uplift   delta(pp)')
    headline = {}
    for name, h_bars in HORIZONS.items():
        fvg_filled_within = ((rep['bars_to_fill'].notna())
                              & (rep['bars_to_fill'] <= h_bars))
        ctrl_filled_within = ((rep['bars_to_touch_control'].notna())
                               & (rep['bars_to_touch_control'] <= h_bars))
        n = len(rep)
        fvg_rate = fvg_filled_within.mean()
        ctrl_rate = ctrl_filled_within.mean()
        uplift = fvg_rate / ctrl_rate if ctrl_rate > 0 else np.inf
        delta_pp = (fvg_rate - ctrl_rate) * 100
        print(f'  {name:<7s}  {n:>5d}  {fvg_rate*100:>7.2f}%  '
              f'{ctrl_rate*100:>9.2f}%  {uplift:>5.2f}x  {delta_pp:>+8.2f}')
        headline[name] = {
            'n': int(n),
            'fvg_rate_pct': round(fvg_rate*100, 2),
            'ctrl_rate_pct': round(ctrl_rate*100, 2),
            'uplift_ratio': round(uplift, 2),
            'delta_pp': round(delta_pp, 2),
        }

    # Direction breakdown
    print('\n=== Direction breakdown (bull vs bear FVGs) ===')
    for dirn in ('bull', 'bear'):
        sub = rep[rep['direction'] == dirn]
        if len(sub) == 0:
            continue
        print(f'\n  {dirn.upper()} FVGs (n={len(sub)}):')
        for name, h_bars in HORIZONS.items():
            fvg_rate = ((sub['bars_to_fill'].notna())
                         & (sub['bars_to_fill'] <= h_bars)).mean() * 100
            ctrl_rate = ((sub['bars_to_touch_control'].notna())
                          & (sub['bars_to_touch_control'] <= h_bars)).mean() * 100
            print(f'    {name:<7s}  FVG={fvg_rate:>6.2f}%  '
                  f'ctrl={ctrl_rate:>6.2f}%  '
                  f'uplift={fvg_rate/ctrl_rate if ctrl_rate > 0 else 0:.2f}x')

    # Distance bucket breakdown
    print('\n=== Distance bucket breakdown (uplift at 24h horizon) ===')
    print(f'\n  {"bucket":<12s} {"n":>5s} {"FVG_fill%":>10s} {"ctrl_touch%":>12s} {"uplift":>8s}')
    h24 = HORIZONS['24h']
    bucket_summary = {}
    for label, lo, hi in DISTANCE_BUCKETS:
        sub = rep[(rep['distance_pct'] >= lo) & (rep['distance_pct'] < hi)]
        if len(sub) == 0:
            continue
        fvg_rate = ((sub['bars_to_fill'].notna())
                     & (sub['bars_to_fill'] <= h24)).mean() * 100
        ctrl_rate = ((sub['bars_to_touch_control'].notna())
                      & (sub['bars_to_touch_control'] <= h24)).mean() * 100
        uplift = fvg_rate / ctrl_rate if ctrl_rate > 0 else 0
        print(f'  {label:<12s} {len(sub):>5d} {fvg_rate:>9.2f}% {ctrl_rate:>11.2f}% {uplift:>7.2f}x')
        bucket_summary[label] = {
            'n': int(len(sub)), 'fvg_rate_pct': round(fvg_rate, 2),
            'ctrl_rate_pct': round(ctrl_rate, 2), 'uplift': round(uplift, 2),
        }

    # Time-to-fill summary
    print('\n=== Time-to-touch distribution ===')
    fvg_filled = rep['bars_to_fill'].dropna()
    ctrl_filled = pd.Series(rep['bars_to_touch_control'].dropna().tolist())
    print(f'\n  {"metric":<25s} {"FVG":>10s} {"control":>10s}')
    for q in (0.10, 0.25, 0.50, 0.75, 0.90):
        fvg_q = fvg_filled.quantile(q) if len(fvg_filled) > 0 else None
        ctrl_q = ctrl_filled.quantile(q) if len(ctrl_filled) > 0 else None
        print(f'  bars-to-touch p{int(q*100):>2d}: '
              f'{fvg_q if fvg_q is not None else "n/a":>10}  '
              f'{ctrl_q if ctrl_q is not None else "n/a":>10}')

    # Verdict
    print('\n' + '=' * 95)
    print('=== VERDICT ===')
    print('=' * 95)
    avg_uplift = np.mean([v['uplift_ratio'] for v in headline.values()
                            if np.isfinite(v['uplift_ratio'])])
    best_horizon = max(headline.keys(), key=lambda k: headline[k]['uplift_ratio']
                         if np.isfinite(headline[k]['uplift_ratio']) else 0)
    best_uplift = headline[best_horizon]['uplift_ratio']
    print(f'\n  Average uplift across horizons: {avg_uplift:.2f}x')
    print(f'  Best uplift: {best_uplift:.2f}x at horizon {best_horizon}')
    if avg_uplift >= 1.5:
        print(f'\n  CONCLUSION: FVG magnet effect is REAL (avg uplift >= 1.5x). '
              f'Worth designing trades around. Move to Phase 2: signal design.')
    elif avg_uplift >= 1.1:
        print(f'\n  CONCLUSION: FVG magnet effect is WEAK but present '
              f'({avg_uplift:.2f}x average). Worth deeper analysis before '
              f'designing trades.')
    else:
        print(f'\n  CONCLUSION: FVG magnet effect is ABSENT '
              f'({avg_uplift:.2f}x average). FVGs are no more magnetic than '
              f'random levels at the same distance. Close hypothesis.')

    out_path = OUT_DIR / 'fvg_magnet_phase1_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'asset': 'BTC', 'cadence': '15m',
            'window_utc': [str(df.index.min()), str(df.index.max())],
            'n_bars': int(n_bars),
            'n_fvgs_total': len(fvgs),
            'n_fvgs_with_forward_data': int(len(rep)),
            'horizon_summary': headline,
            'distance_bucket_24h': bucket_summary,
            'avg_uplift': round(avg_uplift, 3),
            'best_horizon': best_horizon,
            'best_uplift': round(best_uplift, 3),
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

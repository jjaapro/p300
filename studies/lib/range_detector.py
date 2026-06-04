"""Range detection for chento's Rule 2 (time-in-range) and Rule 3 (midrange
avoidance / edge-only entries).

Chento's verbatim rules (2026-05-22 YT live):
  Rule 2: "The more time spent in a specific range, the stronger the
           resistance becomes."
  Rule 3: "Don't trade in midrange."

This module provides:
  detect_active_range(df, ts, ...) -> dict | None
      Returns the active range bracket containing ts, with high/low/start_ts/
      duration_bars, or None if no range.

  price_position_in_range(price, range_dict) -> float [0..1]
      0 = at range low, 1 = at range high.

  time_in_range_bars(range_dict, ts) -> int
      How many bars the range has held by `ts`.

Range definition:
  A range is identified when the rolling [high, low] over `window_bars`
  satisfies range_pct <= max_range_pct (e.g., price moved <8% within the
  window). The range is "active" if `ts` is inside the bracket.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def detect_active_range(df: pd.DataFrame, ts: pd.Timestamp, *,
                         window_bars: int = 4 * 24 * 3,  # 3 days of 15m
                         max_range_pct: float = 0.06,    # 6% max range to qualify
                         high_col: str = 'high',
                         low_col: str = 'low') -> dict | None:
    """Find the longest backward-looking window ending at `ts` where the
    high-low range satisfies max_range_pct. Returns the range bracket.

    Returns dict:
      range_high, range_low, range_pct, start_ts, end_ts, duration_bars
    or None if no qualifying range.
    """
    if df.empty:
        return None
    if ts not in df.index:
        pos = df.index.searchsorted(ts, side='right') - 1
    else:
        pos = df.index.get_loc(ts)
    if pos < 20:  # need minimal history
        return None

    # Walk backward from ts, find the longest window where range <= threshold
    best = None
    for lookback in (window_bars, window_bars * 2, window_bars // 2):
        start = max(0, pos - lookback)
        win = df.iloc[start:pos + 1]
        if len(win) < 10:
            continue
        rh = float(win[high_col].max())
        rl = float(win[low_col].min())
        rng_pct = (rh - rl) / rl if rl > 0 else 1.0
        if rng_pct <= max_range_pct:
            duration = len(win)
            if best is None or duration > best['duration_bars']:
                best = {
                    'range_high': rh, 'range_low': rl,
                    'range_pct': rng_pct,
                    'start_ts': df.index[start],
                    'end_ts': df.index[pos],
                    'duration_bars': duration,
                }
    return best


def price_position_in_range(price: float, range_dict: dict) -> float:
    """Return 0..1 position within the range (0=low, 1=high)."""
    if range_dict is None:
        return float('nan')
    rh, rl = range_dict['range_high'], range_dict['range_low']
    if rh <= rl:
        return float('nan')
    return float((price - rl) / (rh - rl))


def time_in_range_bars(range_dict: dict) -> int:
    """How many bars the range has held."""
    if range_dict is None:
        return 0
    return int(range_dict['duration_bars'])


def classify_position(price_pos: float, *,
                      lower_edge: float = 0.25,
                      upper_edge: float = 0.75) -> str:
    """Classify price position as 'bottom_edge', 'midrange', or 'top_edge'."""
    if np.isnan(price_pos):
        return 'unknown'
    if price_pos <= lower_edge:
        return 'bottom_edge'
    if price_pos >= upper_edge:
        return 'top_edge'
    return 'midrange'


if __name__ == '__main__':
    # Self-test on BTC 15m
    import sqlite3
    from strategies.support import db as _db
    con = sqlite3.connect(str(_db.PROD_DB))
    df = pd.read_sql("SELECT timestamp, open, high, low, close FROM cd_futures_15m "
                      "ORDER BY timestamp", con)
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    df = df.set_index('ts').drop(columns='timestamp')
    print(f'Loaded {len(df):,} bars')
    sample_ts = df.index[-1000]
    r = detect_active_range(df, sample_ts)
    if r:
        price = df.loc[sample_ts, 'close']
        pos = price_position_in_range(price, r)
        zone = classify_position(pos)
        print(f'At {sample_ts}:')
        print(f'  range: [{r["range_low"]:.0f}, {r["range_high"]:.0f}] ({r["range_pct"]*100:.1f}%)')
        print(f'  duration: {r["duration_bars"]} bars ({r["duration_bars"]/4:.0f}h)')
        print(f'  price: {price:.0f}  pos={pos:.2f}  zone={zone}')
    else:
        print(f'At {sample_ts}: NO RANGE')

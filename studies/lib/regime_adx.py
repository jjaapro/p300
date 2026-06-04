"""ADX-based regime classifier (long / short / range / None) per timestamp.

Mirrors S-003's signal logic (strategies/sleeves/adx/signal.py) but extracted
as a pure function with no DB / no live state, so validation notebooks and
multi-asset screening can use the same regime definition without going through
the S-003 sleeve.

Definition:
  adx_period = 14, adx_high = 25, adx_low = 20, ema_period = 50

  regime[t] = 'long'  if  adx[t] >= adx_high  AND  close[t] > ema[t]
  regime[t] = 'short' if  adx[t] >= adx_high  AND  close[t] < ema[t]
  regime[t] = 'range' if  adx[t] <  adx_low
  regime[t] = None    if  adx_low <= adx[t] < adx_high  (no-mans-land)

The 'range' label is distinct from None: 'range' is an active read ("market
is compressed, no directional bias"), None is a non-decision ("ADX is in the
gap zone, regime undetermined").
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

# When run as a script (python studies/lib/regime_adx.py), the repo root is
# not on sys.path. Resolve it relative to this file so the strategies import
# works in both contexts.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from strategies.support.indicators import adx as _adx_fn  # noqa: E402

Regime = Literal['long', 'short', 'range']


def _ema(values: pd.Series, period: int) -> pd.Series:
    """Wilder-style EMA seeded with SMA of first ``period`` values."""
    out = pd.Series(np.nan, index=values.index, dtype=float)
    if len(values) < period:
        return out
    sma = values.iloc[:period].mean()
    out.iloc[period - 1] = sma
    k = 2.0 / (period + 1)
    for i in range(period, len(values)):
        out.iloc[i] = values.iloc[i] * k + out.iloc[i - 1] * (1 - k)
    return out


def classify_regime(df: pd.DataFrame, *,
                    adx_period: int = 14,
                    adx_high: float = 25.0,
                    adx_low: float = 20.0,
                    ema_period: int = 50,
                    high_col: str = 'high',
                    low_col: str = 'low',
                    close_col: str = 'close') -> pd.Series:
    """Return per-bar regime label series indexed by df.index.

    df: OHLC frame; high/low/close column names overridable.
    Values: 'long' | 'short' | 'range' | None.

    NaN until warmup completes (2*adx_period bars + ema_period).
    """
    if df.empty:
        return pd.Series([], dtype='object', index=df.index)

    candles = [
        {'high': float(h), 'low': float(l), 'close': float(c)}
        for h, l, c in zip(df[high_col], df[low_col], df[close_col])
    ]
    adx_vals = _adx_fn(candles, adx_period)
    ema_vals = _ema(df[close_col], ema_period)

    out = pd.Series([None] * len(df), index=df.index, dtype='object')
    closes = df[close_col].values
    for i in range(len(df)):
        a = adx_vals[i]
        e = ema_vals.iloc[i] if i < len(ema_vals) else float('nan')
        if math.isnan(a) or (isinstance(e, float) and math.isnan(e)):
            continue
        if a >= adx_high:
            out.iloc[i] = 'long' if closes[i] > e else 'short'
        elif a < adx_low:
            out.iloc[i] = 'range'
        # else: in the 20<=adx<25 gap zone — leave None
    return out


def regime_at(regime_series: pd.Series, ts: pd.Timestamp) -> Regime | None:
    """Look up regime at or just before `ts` (ffill semantics)."""
    if regime_series.empty:
        return None
    pos = regime_series.index.searchsorted(ts, side='right') - 1
    if pos < 0:
        return None
    val = regime_series.iloc[pos]
    return val if isinstance(val, str) else None


def classify_regime_daily_for_intraday(intraday_df: pd.DataFrame,
                                       daily_df: pd.DataFrame,
                                       **kwargs) -> pd.Series:
    """Convenience: compute regime on `daily_df` then forward-fill onto
    `intraday_df`'s index. Daily regime is what S-003 uses.

    `intraday_df.index` and `daily_df.index` both tz-aware UTC.
    """
    daily_regime = classify_regime(daily_df, **kwargs)
    # Reindex onto intraday and ffill (each bar inherits the daily regime
    # that was known at end of that UTC day's open)
    return daily_regime.reindex(intraday_df.index, method='ffill')


if __name__ == '__main__':
    # Quick self-test on BTC daily from prod.db
    import sqlite3
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from strategies.support import db as _db

    con = sqlite3.connect(str(_db.PROD_DB))
    df = pd.read_sql(
        "SELECT open_time, open, high, low, close, volume FROM btc_1m "
        "ORDER BY open_time", con)
    con.close()
    df['ts'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    df = df.set_index('ts').drop(columns='open_time')
    df.columns = ['open', 'high', 'low', 'close', 'volume']
    # Resample to daily
    daily = df.resample('1D').agg(
        open=('open', 'first'), high=('high', 'max'),
        low=('low', 'min'),     close=('close', 'last'),
        volume=('volume', 'sum')).dropna()
    print(f'Daily bars: {len(daily)}  range {daily.index.min()} -> {daily.index.max()}')
    regime = classify_regime(daily)
    counts = regime.value_counts(dropna=False)
    print(f'\nRegime label counts:')
    print(counts)
    print(f'\nLast 30 daily regime labels:')
    print(regime.tail(30))

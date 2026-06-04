"""Starter setup library for the screener.

Each setup is a deterministic function:
    setup_fn(df_daily, df_1h, **params) -> pd.DataFrame
        columns: ts, entry, stop, target, setup_id, [conf]

Setups MUST only use data up to and including ts (the runner enforces this
by sub-slicing the input frame per row when needed; setup authors should
respect it by not using lookahead operations like resample().shift(-1)).

Forward-return + R-outcome measurement is the runner's job, not the setup's.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# === Common helpers ========================================================

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI on close series."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR on OHLC frame with high/low/close columns."""
    h, l, c = df['high'], df['low'], df['close']
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False).mean()


def _slope(s: pd.Series, period: int) -> pd.Series:
    """Per-bar log-return slope over `period` bars (simple approximation)."""
    return (s / s.shift(period)).apply(np.log) / period


# === S1 — Oversold bounce (mean-reversion) =================================

def s1_oversold_bounce(df_daily: pd.DataFrame, df_1h: pd.DataFrame,
                       *,
                       drawdown_window: int = 7,
                       drawdown_threshold: float = -0.20,
                       rsi_period: int = 14,
                       rsi_threshold: float = 30,
                       vol_mult: float = 1.5,
                       vol_lookback: int = 20,
                       target_r: float = 3.0,
                       ) -> pd.DataFrame:
    """7d return < -20% AND RSI < 30 AND today's close > today's open AND
    today's volume > 1.5x 20d-mean.
    """
    if len(df_daily) < max(drawdown_window, rsi_period, vol_lookback) + 5:
        return pd.DataFrame(columns=['ts', 'entry', 'stop', 'target', 'setup_id'])
    close = df_daily['close']
    open_ = df_daily['open']
    high = df_daily['high']
    low = df_daily['low']
    vol = df_daily['volume']

    nday_ret = close.pct_change(drawdown_window)
    rsi = _rsi(close, rsi_period)
    bullish_candle = close > open_
    vol_spike = vol > vol_mult * vol.rolling(vol_lookback).mean()
    atr = _atr(df_daily, 14)

    mask = (nday_ret <= drawdown_threshold) & (rsi <= rsi_threshold) \
           & bullish_candle & vol_spike

    rows = []
    for ts, hit in mask.items():
        if not hit:
            continue
        entry = float(close.loc[ts])
        stop = float(low.loc[ts] - atr.loc[ts]) if not np.isnan(atr.loc[ts]) else float(low.loc[ts])
        risk = entry - stop
        target = entry + target_r * risk if risk > 0 else np.nan
        rows.append({
            'ts': ts, 'entry': entry, 'stop': stop, 'target': target,
            'setup_id': 'S1_oversold_bounce',
        })
    return pd.DataFrame(rows)


# === S2 — Bull flag breakout (momentum continuation) =======================

def s2_bull_flag_breakout(df_daily: pd.DataFrame, df_1h: pd.DataFrame,
                          *,
                          prior_trend_window: int = 20,
                          prior_trend_min_pct: float = 0.30,
                          flag_min_bars: int = 5,
                          flag_max_bars: int = 10,
                          flag_max_range_pct: float = 0.08,
                          vol_mult: float = 1.5,
                          target_r: float = 2.0,
                          ) -> pd.DataFrame:
    """20d trend up > 30%, last 5-10d consolidation (range < 8%, no new highs),
    trigger on close > 10d high with above-avg volume.
    """
    if len(df_daily) < prior_trend_window + flag_max_bars + 5:
        return pd.DataFrame(columns=['ts', 'entry', 'stop', 'target', 'setup_id'])
    close = df_daily['close']
    vol = df_daily['volume']
    rows = []

    for i in range(prior_trend_window + flag_max_bars, len(df_daily)):
        ts = df_daily.index[i]
        bar = df_daily.iloc[i]
        # 20d trend ending flag_min_bars ago
        ret_20d = (close.iloc[i - flag_min_bars] / close.iloc[i - flag_min_bars - prior_trend_window]) - 1
        if ret_20d < prior_trend_min_pct:
            continue
        # Flag = last N bars: max range pct
        for flag_len in range(flag_min_bars, flag_max_bars + 1):
            flag_slice = df_daily.iloc[i - flag_len:i]
            f_max, f_min = float(flag_slice['high'].max()), float(flag_slice['low'].min())
            f_range = (f_max - f_min) / f_min if f_min > 0 else 1.0
            if f_range > flag_max_range_pct:
                continue
            # No new HH within flag
            if float(bar['high']) <= f_max:
                continue
            # Volume spike on breakout day
            avg_v = flag_slice['volume'].mean()
            if avg_v <= 0 or bar['volume'] < vol_mult * avg_v:
                continue
            entry = float(bar['close'])
            stop = f_min
            risk = entry - stop
            target = entry + target_r * risk if risk > 0 else np.nan
            rows.append({
                'ts': ts, 'entry': entry, 'stop': stop, 'target': target,
                'setup_id': 'S2_bull_flag_breakout',
            })
            break  # only fire once per ts
    return pd.DataFrame(rows)


# === S4 — Funding-flush long ===============================================
#
# Original spec calls for funding rate + OI data. We don't have per-coin
# funding/OI in screener tables — only OHLCV. Approximate the flush via:
#   1d return < -8% AND high-volume capitulation bar (vol > 2x 20d mean)
#   AND close in upper 50% of bar range (capitulation reversal)
# Replace with true funding-flush logic once per-coin funding is ingested.

def s4_funding_flush_long_proxy(df_daily: pd.DataFrame, df_1h: pd.DataFrame,
                                *,
                                flush_threshold: float = -0.08,
                                vol_mult: float = 2.0,
                                vol_lookback: int = 20,
                                close_in_upper_pct: float = 0.5,
                                target_r: float = 2.0,
                                ) -> pd.DataFrame:
    """Approximated funding-flush long: large red bar with capitulation volume
    and close in upper half of bar (rejection of lows).
    """
    if len(df_daily) < vol_lookback + 5:
        return pd.DataFrame(columns=['ts', 'entry', 'stop', 'target', 'setup_id'])
    close = df_daily['close']
    open_ = df_daily['open']
    high = df_daily['high']
    low = df_daily['low']
    vol = df_daily['volume']

    ret_1d = close.pct_change(1)
    vol_spike = vol > vol_mult * vol.rolling(vol_lookback).mean()
    bar_range = (high - low).replace(0, np.nan)
    close_pos = (close - low) / bar_range  # 0 = at low, 1 = at high
    upper_close = close_pos >= close_in_upper_pct
    mask = (ret_1d <= flush_threshold) & vol_spike & upper_close

    rows = []
    for ts, hit in mask.items():
        if not hit:
            continue
        entry = float(close.loc[ts])
        stop = float(low.loc[ts])
        risk = entry - stop
        target = entry + target_r * risk if risk > 0 else np.nan
        rows.append({
            'ts': ts, 'entry': entry, 'stop': stop, 'target': target,
            'setup_id': 'S4_funding_flush_long_proxy',
        })
    return pd.DataFrame(rows)


# === S10 — Trend-aligned pullback (chento-style on wide universe) ==========
#
# Daily ADX > 25 AND close > EMA(50) (uptrend regime).
# Entry on first daily bar where price pulls back to within 2% of EMA(21)
# AND closes higher than open (reversal candle).
#
# We approximate ADX without the full Wilder calc — use realized vol as a
# trend-strength proxy (high vol AND positive ema slope = trending). Quick
# heuristic; a real run would use the full ADX from indicators.adx.

def s10_trend_aligned_pullback(df_daily: pd.DataFrame, df_1h: pd.DataFrame,
                               *,
                               trend_ema: int = 50,
                               pullback_ema: int = 21,
                               proximity_pct: float = 0.02,
                               slope_lookback: int = 10,
                               min_slope: float = 0.001,
                               target_r: float = 2.0,
                               ) -> pd.DataFrame:
    if len(df_daily) < trend_ema + slope_lookback + 5:
        return pd.DataFrame(columns=['ts', 'entry', 'stop', 'target', 'setup_id'])
    close = df_daily['close']
    open_ = df_daily['open']
    low = df_daily['low']

    ema50 = _ema(close, trend_ema)
    ema21 = _ema(close, pullback_ema)
    ema50_slope = _slope(ema50, slope_lookback)

    uptrend = (close > ema50) & (ema50_slope >= min_slope)
    pullback_close = (close - ema21).abs() / ema21 <= proximity_pct
    reversal = close > open_

    mask = uptrend & pullback_close & reversal

    rows = []
    for ts, hit in mask.items():
        if not hit:
            continue
        entry = float(close.loc[ts])
        stop = float(low.loc[ts])
        risk = entry - stop
        target = entry + target_r * risk if risk > 0 else np.nan
        rows.append({
            'ts': ts, 'entry': entry, 'stop': stop, 'target': target,
            'setup_id': 'S10_trend_aligned_pullback',
        })
    return pd.DataFrame(rows)


# === Registry of starter setups ============================================

STARTER_SETUPS = {
    'S1_oversold_bounce':        s1_oversold_bounce,
    'S2_bull_flag_breakout':     s2_bull_flag_breakout,
    'S4_funding_flush_long_proxy': s4_funding_flush_long_proxy,
    'S10_trend_aligned_pullback': s10_trend_aligned_pullback,
}

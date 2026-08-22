#!/usr/bin/env python3
"""Scanner-study engine: cross-sectional event detection + mechanical backtest
over the 171-symbol Binance futures universe (scanner_ohlcv.db, 15m bars,
2024-01 -> now).

Two templates from the Paladin study's validated behaviour:

  A sweep_fade_short — a fresh N-day high printed within the last 6h and price
    has rejected back below the pre-break reference level on a red bar. Short.
  B rs_dump_long — BTC down over 24h, the alt outperforming BTC by a margin,
    on a level-holding (non-capitulating) bar. Long.

Mechanics (both): enter next bar open (taker), stop = k x ATR14(1h), fixed-R
target, TIF cap, conservative ambiguity (a bar spanning stop and target counts
as the stop). Costs are taken per side in bp and converted to R using each
trade's own risk distance — small stops make fees expensive, exactly the
LVN-study failure mode we must not repeat.

Everything reads bars strictly before the signal bar's close; entries are on
the NEXT bar's open. No lookahead.
"""
from __future__ import annotations

import os
import sqlite3

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, 'scanner_ohlcv.db')

BARS_PER_DAY = 96          # 15m
SWEEP_WINDOW = 24          # "within the last 6h" = 24 x 15m bars

# Round-trip cost model, per side, in basis points of price.
FEE_BP = 5.0               # Binance USDT-M taker
SLIP_BP = 4.0              # conservative for top-150 liquidity


def load_symbol(symbol: str) -> pd.DataFrame | None:
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    try:
        df = pd.read_sql(
            'SELECT open_time_ms, open, high, low, close, volume, quote_volume '
            'FROM klines_15m WHERE symbol=? ORDER BY open_time_ms',
            con, params=(symbol,))
    finally:
        con.close()
    if len(df) < 30 * BARS_PER_DAY:
        return None
    df.index = pd.to_datetime(df.pop('open_time_ms'), unit='ms', utc=True)
    return df


def list_symbols() -> list[str]:
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    try:
        return [r[0] for r in con.execute(
            'SELECT DISTINCT symbol FROM klines_15m ORDER BY symbol')]
    finally:
        con.close()


def add_features(df: pd.DataFrame, btc_close_1h: pd.Series | None) -> pd.DataFrame:
    """Per-bar features, all computed from data available at that bar's close."""
    out = df.copy()
    h1 = df['close'].resample('1h', label='left', closed='left').last().dropna()
    hi1 = df['high'].resample('1h', label='left', closed='left').max().dropna()
    lo1 = df['low'].resample('1h', label='left', closed='left').min().dropna()
    tr = pd.concat([hi1 - lo1, (hi1 - h1.shift()).abs(),
                    (lo1 - h1.shift()).abs()], axis=1).max(axis=1)
    atr1h = tr.ewm(alpha=1 / 14, adjust=False).mean()
    # value known only once the 1h bar closes -> shift one 1h bar, ffill onto 15m
    out['atr1h'] = atr1h.shift(1).reindex(out.index, method='ffill')

    # rolling 30d median of daily quote volume (liquidity gate), known at bar
    dv = df['quote_volume'].rolling(BARS_PER_DAY).sum()
    out['qv_24h'] = dv
    out['qv_30d_med'] = dv.rolling(30 * BARS_PER_DAY).median()

    out['ret_24h'] = df['close'].pct_change(BARS_PER_DAY)
    if btc_close_1h is not None:
        btc = btc_close_1h.reindex(out.index, method='ffill')
        out['btc_ret_24h'] = btc.pct_change(BARS_PER_DAY)
        out['btc_ret_30d'] = btc.pct_change(30 * BARS_PER_DAY)
        out['rs_24h'] = out['ret_24h'] - out['btc_ret_24h']
    return out


def sweep_fade_short_events(df: pd.DataFrame, n_days: int) -> pd.Series:
    """True on bars where: a fresh n-day high printed in the last 6h AND the
    bar closes red back below the pre-break reference high."""
    w = n_days * BARS_PER_DAY
    ref_high = df['high'].shift(SWEEP_WINDOW).rolling(w).max()   # before the 6h window
    recent_high = df['high'].rolling(SWEEP_WINDOW).max()
    broke = recent_high > ref_high
    rejected = (df['close'] < ref_high) & (df['close'] < df['open'])
    return (broke & rejected).fillna(False)


def sweep_fade_short_events_v2(df: pd.DataFrame, n_days: int,
                               ext_atr: float = 2.0,
                               min_ret_24h: float = 0.05) -> pd.Series:
    """Refined fade (pre-registered 2026-08-23, NOT grid-mined): only extended
    pumps — close above the 24h EMA by ext_atr x ATR1h AND 24h return >=
    min_ret_24h — and only the FIRST rejection bar of a sweep episode."""
    base = sweep_fade_short_events(df, n_days)
    ema24 = df['close'].ewm(span=BARS_PER_DAY, adjust=False).mean()
    extended = df['close'] > ema24 + ext_atr * df['atr1h']
    pumped = df['ret_24h'] >= min_ret_24h
    ev = base & extended & pumped
    first = ev & ~ev.shift(1, fill_value=False).rolling(SWEEP_WINDOW).max().astype(bool)
    return first.fillna(False)


def rs_dump_long_events(df: pd.DataFrame, btc_thresh: float, rs_thresh: float) -> pd.Series:
    """True on bars where BTC is down >= btc_thresh over 24h while this alt
    outperforms BTC by >= rs_thresh, and the bar itself is not capitulating
    (close above the last 6h low region)."""
    holding = df['close'] > df['low'].rolling(SWEEP_WINDOW).min() * 1.001
    return ((df['btc_ret_24h'] <= -btc_thresh)
            & (df['rs_24h'] >= rs_thresh) & holding).fillna(False)


def backtest_events(df: pd.DataFrame, events: pd.Series, side: int,
                    atr_mult: float, target_r: float, tif_bars: int,
                    min_qv_30d: float, regime_gate: str | None,
                    cooldown_bars: int = BARS_PER_DAY) -> list[dict]:
    """Mechanical replay of every event. side=-1 short, +1 long."""
    o = df['open'].to_numpy(); h = df['high'].to_numpy()
    lo = df['low'].to_numpy(); c = df['close'].to_numpy()
    atr = df['atr1h'].to_numpy()
    qv = df['qv_30d_med'].to_numpy()
    btc30 = df['btc_ret_30d'].to_numpy() if 'btc_ret_30d' in df else np.full(len(df), np.nan)
    idx = df.index
    ev = np.flatnonzero(events.to_numpy())
    trades: list[dict] = []
    next_ok = -1
    cost_r_side = (FEE_BP + SLIP_BP) / 1e4     # price fraction per side

    for i in ev:
        if i <= next_ok or i + 2 >= len(o):
            continue
        if not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        if not np.isfinite(qv[i]) or qv[i] < min_qv_30d:
            continue
        if regime_gate == 'skip_up30d' and (np.isfinite(btc30[i]) and btc30[i] > 0.10):
            continue
        entry = o[i + 1]
        risk = atr_mult * atr[i]
        stop = entry - side * risk
        target = entry + side * risk * target_r
        exit_px, reason = None, 'timeout'
        end = min(i + 1 + tif_bars, len(o) - 1)
        for j in range(i + 1, end + 1):
            hit_stop = (lo[j] <= stop) if side > 0 else (h[j] >= stop)
            hit_tp = (h[j] >= target) if side > 0 else (lo[j] <= target)
            if hit_stop:                       # conservative: stop wins the bar
                exit_px, reason, jx = stop, 'stop', j
                break
            if hit_tp:
                exit_px, reason, jx = target, 'target', j
                break
        else:
            exit_px, jx = c[end], end
        gross_r = (exit_px - entry) * side / risk
        cost_r = cost_r_side * 2 * entry / risk     # both sides, in R units
        trades.append({'time': idx[i + 1], 'entry': entry, 'risk_pct': risk / entry * 100,
                       'exit_reason': reason, 'gross_r': gross_r,
                       'net_r': gross_r - cost_r, 'cost_r': cost_r,
                       'hold_bars': jx - i})
        next_ok = i + cooldown_bars
    return trades

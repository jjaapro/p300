"""Pure helpers for S-106 CHENTO_LIMIT_BID.

Stateless functions for swing-base detection, multi-timeframe bias
computation, and confluence scoring. No I/O, no DB access — testable
in isolation, importable from notebooks for backtesting.

The shape of these functions is intentionally identical to the helpers in
[studies/notebooks/swing_base_limit_bid/discovery.ipynb] so the live sleeve
can be validated against the same backtest code.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import numpy as np
import pandas as pd

from .config import (
    BASE_WINDOW_HOURS, BASE_CLUSTER_PCT, BASE_CLUSTER_BAND_PCT,
    BASE_EXPANSION_PCT, BASE_EXPANSION_DAYS,
    BASIS_BP_MAX, FUNDING_MAX, OI_FLUSH_PCT,
    MTF_DEFS, MTF_NET_ACCEPT, MTF_NET_REJECT, MTF_CAPITULATION_SIG,
    TRIGGER_HOUR_MIN, TRIGGER_HOUR_MAX, TRIGGER_WEEKDAYS,
)


# ─── Swing-base detection ──────────────────────────────────────────────────

def detect_active_base(df_15m: pd.DataFrame, now_ts: pd.Timestamp,
                       window_hours: int = BASE_WINDOW_HOURS,
                       cluster_pct: float = BASE_CLUSTER_PCT,
                       cluster_band_pct: float = BASE_CLUSTER_BAND_PCT,
                       expansion_pct: float = BASE_EXPANSION_PCT,
                       expansion_days: int = BASE_EXPANSION_DAYS,
                       ) -> dict | None:
    """Return the most recent base whose forward expansion has confirmed
    AND that still sits within reach of current price (i.e. price hasn't
    run away from it).

    Returns ``{'base_low', 'base_start_ts', 'base_end_ts', 'confirm_ts',
    'cluster_frac', 'fwd_high'}`` or ``None`` if no qualifying base.

    df_15m: indexed by tz-aware timestamp, columns include 'spot_l', 'spot_h'.
    now_ts: the current bar's timestamp (tz-aware).
    """
    bars_per_hr = 4
    W = window_hours * bars_per_hr
    K_bars = expansion_days * 24 * bars_per_hr

    # Snap now_ts down to the index
    if now_ts not in df_15m.index:
        # Use the most recent bar at or before now_ts
        idx_pos = df_15m.index.searchsorted(now_ts, side='right') - 1
    else:
        idx_pos = df_15m.index.get_loc(now_ts)
    if idx_pos < W:
        return None

    # Walk backwards from idx_pos looking for the most recent confirmed base
    # whose `confirm_ts` is <= now_ts. We scan a bounded window — 7 days back
    # at hourly cadence — because anything older than that isn't "approaching".
    max_lookback = min(idx_pos, 7 * 24 * bars_per_hr)
    for k_off in range(0, max_lookback, bars_per_hr):
        confirm_pos = idx_pos - k_off
        if confirm_pos - W - K_bars < 0:
            break
        # For this confirm position, the base window ended K_bars before confirm
        base_end_pos = confirm_pos - K_bars
        base_start_pos = base_end_pos - W
        if base_start_pos < 0:
            continue
        win_lo = df_15m['spot_l'].iloc[base_start_pos:base_end_pos].values
        b_low = float(win_lo.min())
        within = float((win_lo <= b_low * (1 + cluster_band_pct)).mean())
        if within < cluster_pct:
            continue
        # Forward expansion check
        fwd_hi_window = df_15m['spot_h'].iloc[base_end_pos:base_end_pos + K_bars]
        if len(fwd_hi_window) == 0:
            continue
        fwd_high = float(fwd_hi_window.max())
        if fwd_high < b_low * (1 + expansion_pct):
            continue
        # Confirm timestamp = first bar where expansion was reached
        hit_mask = fwd_hi_window >= b_low * (1 + expansion_pct)
        if not hit_mask.any():
            continue
        confirm_pos_actual = base_end_pos + int(hit_mask.values.argmax())
        if confirm_pos_actual > idx_pos:
            continue
        return {
            'base_low': b_low,
            'base_start_ts': df_15m.index[base_start_pos],
            'base_end_ts': df_15m.index[base_end_pos - 1],
            'confirm_ts': df_15m.index[confirm_pos_actual],
            'cluster_frac': within,
            'fwd_high': fwd_high,
        }
    return None


def is_approaching_base(current_price: float, base_low: float,
                        approach_band_pct: float) -> bool:
    """True if current price is within approach_band_pct above base_low."""
    if base_low <= 0:
        return False
    upper_bound = base_low * (1 + approach_band_pct)
    # Must be above the base (no point limit-bidding below it) but within band
    return base_low < current_price <= upper_bound


# ─── Confluence scoring ────────────────────────────────────────────────────

def score_base_window(window_df: pd.DataFrame) -> dict:
    """Score the 4 confluence legs on a base window. Each leg is binary;
    returns ``{'basis', 'funding', 'oi_flush', 'absorption', 'conf_score',
    'basis_bp_mean', 'funding_mean', 'oi_drawdown_pct', 'spot_cvd_sum'}``.

    Window must contain columns: 'basis_bp', 'funding', 'oi', 'spot_cvd'.
    """
    if len(window_df) == 0:
        return {'basis': 0, 'funding': 0, 'oi_flush': 0, 'absorption': 0,
                'conf_score': 0, 'basis_bp_mean': float('nan'),
                'funding_mean': float('nan'), 'oi_drawdown_pct': float('nan'),
                'spot_cvd_sum': float('nan')}
    basis_mean = float(window_df['basis_bp'].mean())
    fund_mean = float(window_df['funding'].mean())
    oi_start = float(window_df['oi'].iloc[0])
    oi_min = float(window_df['oi'].min())
    oi_dd = (oi_min - oi_start) / max(oi_start, 1e-9)
    spot_cvd_sum = float(window_df['spot_cvd'].sum())

    basis_ok = int(basis_mean <= BASIS_BP_MAX)
    fund_ok = int(fund_mean < FUNDING_MAX)
    oi_ok = int(oi_dd <= -OI_FLUSH_PCT)
    absorb_ok = int(spot_cvd_sum > 0)

    return {
        'basis': basis_ok,
        'funding': fund_ok,
        'oi_flush': oi_ok,
        'absorption': absorb_ok,
        'conf_score': basis_ok + fund_ok + oi_ok + absorb_ok,
        'basis_bp_mean': basis_mean,
        'funding_mean': fund_mean,
        'oi_drawdown_pct': oi_dd,
        'spot_cvd_sum': spot_cvd_sum,
    }


# ─── Multi-timeframe bias ─────────────────────────────────────────────────

def compute_tf_bias_series(ohlcv_df: pd.DataFrame, period: int, slope: int,
                            ) -> pd.Series:
    """Given an OHLCV frame (index = tz-aware, column 'c' = close), return
    a Series of bias labels ('+' / '-' / '0') by bar.

    Bias = '+' if close > SMA AND SMA[now] > SMA[now-slope] (uptrend confirm)
           '-' if close < SMA AND SMA[now] < SMA[now-slope] (downtrend confirm)
           '0' otherwise (inflecting / chop)
    """
    sma = ohlcv_df['c'].rolling(period).mean()
    sma_then = sma.shift(slope)
    up = (ohlcv_df['c'] > sma) & (sma > sma_then)
    dn = (ohlcv_df['c'] < sma) & (sma < sma_then)
    out = pd.Series(np.where(up, '+', np.where(dn, '-', '0')),
                    index=ohlcv_df.index, dtype=object)
    return out


def mtf_signature_at(ts: pd.Timestamp, tf_bias_map: dict[str, pd.Series],
                     ) -> tuple[str, int]:
    """Look up the bias on each TF as of ``ts`` and return (signature, net).

    tf_bias_map: ``{tf_label: bias_series}`` for ['M','W','D','H4','H1'].
    Each series is computed by compute_tf_bias_series on resampled OHLCV.

    Uses pandas index.asof so the most-recent closed bar on each TF is
    used regardless of the TF's bar frequency.
    """
    chars = []
    for label in ['M', 'W', 'D', 'H4', 'H1']:
        series = tf_bias_map.get(label)
        if series is None or len(series) == 0:
            chars.append('?')
            continue
        asof = series.index.asof(ts)
        if pd.isna(asof):
            chars.append('?')
            continue
        v = series.loc[asof]
        chars.append(v if isinstance(v, str) else '?')
    sig = ''.join(chars)
    net = sig.count('+') - sig.count('-')
    return sig, net


def passes_mtf_gate(sig: str, net: int) -> bool:
    """True if the (sig, net) pair sits in an accepted cell.

    Hard rejects: cells with positive expectancy = negative (per discovery).
    Specifically `mtf_net in {+3, +4, +5}` — buying the dip when *every*
    timeframe is already bullish is a losing trade on average over 2022-2026.

    Accepts:
    - sig == MTF_CAPITULATION_SIG (special case: bearish HTF + bullish LTF)
    - net in MTF_NET_ACCEPT (default {-3, -2, +1, +2})
    """
    if net in MTF_NET_REJECT:
        return False
    if sig == MTF_CAPITULATION_SIG:
        return True
    if net in MTF_NET_ACCEPT:
        return True
    return False


# ─── Time-of-day / day-of-week gate ────────────────────────────────────────

def passes_time_gate(now_utc: datetime) -> bool:
    """True if now_utc is in the NY-overlap window and a tradeable weekday."""
    if now_utc.weekday() not in TRIGGER_WEEKDAYS:
        return False
    h = now_utc.hour
    return TRIGGER_HOUR_MIN <= h <= TRIGGER_HOUR_MAX


# ─── Tier exit state machine (v2) ─────────────────────────────────────────

def evaluate_tier_transitions(state: dict, bar_high: float, bar_low: float,
                              entry: float, stop_initial: float,
                              t1_r: float, t2_r: float, trail_pct: float,
                              ) -> dict:
    """Given a trade's current state + the latest bar's high/low, return the
    actions to take + the new state dict.

    state keys: {'t1_done', 't2_done', 'trail_armed', 'high_water', 'active_stop'}

    Returns:
        {
            'actions': list of {'kind': 't1'|'t2'|'trail_exit'|'stop_exit',
                                 'price': float},
            'new_state': updated state dict,
        }

    Order of evaluation (conservative — assumes worst-case-first):
        1. Stop hit (any active_stop including trail)
        2. T1 if not done
        3. T2 if t1 done and t2 not done
        4. Trail stop update if armed

    Pure function — no I/O, no DB access.
    """
    state = dict(state or {})
    state.setdefault('t1_done', False)
    state.setdefault('t2_done', False)
    state.setdefault('trail_armed', False)
    state.setdefault('high_water', entry)
    state.setdefault('active_stop', stop_initial)

    actions = []
    risk = entry - stop_initial
    if risk <= 0:
        return {'actions': actions, 'new_state': state}

    # Update high_water on the way up
    state['high_water'] = max(float(state['high_water']), float(bar_high))

    # If trail is armed, ratchet the active_stop up
    if state['trail_armed']:
        new_trail_stop = state['high_water'] * (1.0 - trail_pct)
        if new_trail_stop > state['active_stop']:
            state['active_stop'] = new_trail_stop

    # 1. Stop hit (initial or trailed)
    if bar_low <= state['active_stop']:
        actions.append({'kind': 'stop_exit', 'price': state['active_stop']})
        return {'actions': actions, 'new_state': state}

    t1_price = entry + t1_r * risk
    t2_price = entry + t2_r * risk

    # 2. Tier 1
    if not state['t1_done'] and bar_high >= t1_price:
        actions.append({'kind': 't1', 'price': t1_price})
        state['t1_done'] = True
        state['trail_armed'] = True
        # Re-ratchet on T1 hit (might already be the case)
        new_trail_stop = state['high_water'] * (1.0 - trail_pct)
        if new_trail_stop > state['active_stop']:
            state['active_stop'] = new_trail_stop

    # 3. Tier 2 — only after T1 has fired this bar or earlier
    if state['t1_done'] and not state['t2_done'] and bar_high >= t2_price:
        actions.append({'kind': 't2', 'price': t2_price})
        state['t2_done'] = True

    return {'actions': actions, 'new_state': state}


# ─── 15m boundary detection (matches short_squeeze.math) ────────────────────

def is_15m_boundary(now_utc: datetime) -> bool:
    """True at the start of a fresh 15m bar (minute mod 15 == 0).

    Live ticks fire every minute; we only want to do real work once per
    15m bar close.
    """
    return now_utc.minute % 15 == 0


def utc_date_of(ts: datetime) -> str:
    """ISO-date string in UTC. Used as a cache key."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).date().isoformat()

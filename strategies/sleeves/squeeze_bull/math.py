"""S-107 SQUEEZE_BULL — pure signal maths, no I/O.

Split out from signal.py so the parity test can drive these functions with
the study's own hourly frame and assert the production port reproduces the
research ledger exactly (the repo's standing rule for ports).

Every function takes plain sequences and returns plain values; nothing here
touches the database, the clock, or the trade ledger.
"""
from __future__ import annotations

from .config import (
    BULL_THRESHOLD, COOLDOWN_HOURS, FLUSH_THRESHOLD, PRICE_DIR_THRESHOLD,
    REGIME_SHIFT_DAYS, STOP_PCT, TARGET_PCT,
)


def pct_change_n(values, n: int, i: int) -> float | None:
    """(values[i] / values[i-n]) - 1, or None when out of range or the base
    is not positive. Matches pandas .pct_change(n) at position i."""
    if i < n or i >= len(values):
        return None
    base = values[i - n]
    if base is None or base <= 0 or values[i] is None:
        return None
    return values[i] / base - 1.0


def classify_regime(ret_30d: float | None) -> str:
    """June classification, kept verbatim including the NaN branch: a missing
    30-day return is `flat_30d`, never bull, so it can never open a trade."""
    if ret_30d is None:
        return "flat_30d"
    if ret_30d < -BULL_THRESHOLD:
        return "bear_30d"
    if ret_30d > BULL_THRESHOLD:
        return "bull_30d"
    return "flat_30d"


def backward_only_ret_30d(daily_closes: dict, on_date) -> float | None:
    """The causal 30-day return for any hour of `on_date`.

    `daily_closes` maps date -> that UTC day's last hourly close. The value
    is built from days strictly before `on_date`:

        r30(d) = close[d - 1] / close[d - 1 - 30] - 1

    i.e. pandas' `daily.pct_change(30).shift(1)` reindexed onto the hourly
    grid. REGIME_SHIFT_DAYS is what makes it causal; with a shift of 0 an
    early-morning bar reads a close from later the same day.
    """
    from datetime import timedelta

    ref = on_date - timedelta(days=REGIME_SHIFT_DAYS)
    prior = ref - timedelta(days=30)
    a, b = daily_closes.get(ref), daily_closes.get(prior)
    if a is None or b is None or b <= 0:
        return None
    return a / b - 1.0


def is_flush(oi_chg_4h: float | None, px_chg_4h: float | None) -> bool:
    """The trigger itself, before the regime gate and the cooldown."""
    if oi_chg_4h is None or px_chg_4h is None:
        return False
    return oi_chg_4h <= FLUSH_THRESHOLD and px_chg_4h <= PRICE_DIR_THRESHOLD


def kept_flush_indices(oi_closes, closes) -> list[int]:
    """Indices of flush events that survive the 24-bar cooldown.

    Verbatim port of `identify_long_flush_events` in
    studies/notebooks/oi_flush/phase2_backtest.py, and the reason this is a
    sequence function rather than a per-bar predicate: **the cooldown is
    applied to flush EVENTS, before any regime filter.** A bear-regime flush
    the sleeve will never trade still silences the next 24 bars. Deciding
    the cooldown from the last *trade* instead would let a bull flush fire
    inside the shadow of an untraded one, and the ledger would drift from
    the research.

    The kept set depends on the flush history, so callers must pass a window
    long enough for the chain to be determined. Any 24-bar gap between
    flushes resets it, and flushes are rare (423 in 4.6 years), so the
    sleeve's 45-day window is far more than enough.
    """
    kept: list[int] = []
    last = None
    for i in range(len(closes)):
        if not is_flush(pct_change_n(oi_closes, 4, i), pct_change_n(closes, 4, i)):
            continue
        if last is not None and (i - last) < COOLDOWN_HOURS:
            continue
        kept.append(i)
        last = i
    return kept


def bracket(entry_price: float) -> tuple[float, float, float]:
    """(stop, target, risk_per_unit) for a long at `entry_price`."""
    stop = entry_price * (1.0 - STOP_PCT)
    target = entry_price * (1.0 + TARGET_PCT)
    return stop, target, entry_price - stop


def replay_bracket(highs, lows, closes, *, entry_idx: int, tif_bars: int,
                   cost_bp: float) -> dict | None:
    """Research replay, ported verbatim from
    studies/notebooks/oi_flush/phase2_backtest.py:replay.

    Used only by the parity test and by backtests — the live sleeve prices
    exits off the current price, not off bars. Stop is checked before target
    within a bar.
    """
    entry = closes[entry_idx]
    stop, target, risk = bracket(entry)
    if risk <= 0:
        return None
    cost_r = (cost_bp / 10000.0) * (entry / risk)

    end = min(entry_idx + tif_bars + 1, len(highs))
    last_close = entry
    for j in range(entry_idx + 1, end):
        last_close = float(closes[j])
        if float(lows[j]) <= stop:
            return {"r_outcome": (stop - entry) / risk - cost_r, "exit_kind": "stop"}
        if float(highs[j]) >= target:
            return {"r_outcome": (target - entry) / risk - cost_r, "exit_kind": "target"}
    return {"r_outcome": (last_close - entry) / risk - cost_r, "exit_kind": "tif"}

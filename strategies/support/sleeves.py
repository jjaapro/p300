"""strategies.support.sleeves — small runtime helpers shared across sleeve dispatchers.

This module is the home for cross-sleeve runtime utilities that have proven
worth de-duplicating empirically. It is **not** a base class for sleeves —
the variance in dispatcher bodies (THU_BEAR's day-of-week + hour gates,
ADX's compression-cycle state machine, CARRY's multi-day funding eval,
FOMC's scheduled-event matching) doesn't fit cleanly under a single
template-method abstraction without leaky workarounds. Targeted helper
extraction wins on ROI vs blast radius.

Currently exposes:
  live_pnl_pct(direction, entry, current)   — direction-aware PnL math
  is_sl_hit(direction, entry, current, threshold) — wraps live_pnl_pct
                                                     with the threshold check

Other shared concerns (sizing math, variant capital lookup, INSERT) already
live in ``strategies.trades.open_shadow_trade``; close mechanics live in
``strategies.trades.close_perp_trade`` / ``close_carry_trade``.
"""
from __future__ import annotations


def live_pnl_pct(direction: str, entry_price: float, current_price: float) -> float:
    """Direction-aware live PnL as PERCENT of notional.

    LONG  → positive when ``current > entry``
    SHORT → positive when ``current < entry``

    The sign convention here is the canonical one across the bot: a SL is hit
    when this returns a value <= ``-sl_threshold_pct`` (where threshold is
    positive). See ``is_sl_hit`` for the threshold-aware check.
    """
    if direction.upper() == "LONG":
        return (current_price - entry_price) / entry_price * 100.0
    return (entry_price - current_price) / entry_price * 100.0


def is_sl_hit(direction: str, entry_price: float, current_price: float,
              sl_threshold_pct: float) -> tuple[bool, float]:
    """Return ``(hit, live_pnl_pct)`` for a single position.

    ``sl_threshold_pct`` is positive (e.g. ``5.0`` means a 5% adverse move
    triggers). The SL fires when the position's live PnL falls to
    ``-sl_threshold_pct`` or worse.

    Sleeve dispatchers call this inside their own SL-sweep loop because the
    loop shape varies per sleeve (multi-asset dict vs flat single-asset list,
    different action-dict schemas, different logging tags). The PnL math
    itself, however, is identical and worth centralizing.
    """
    pnl = live_pnl_pct(direction, entry_price, current_price)
    return pnl <= -sl_threshold_pct, pnl

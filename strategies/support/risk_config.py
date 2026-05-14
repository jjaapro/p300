"""Risk-control config switches.

SL semantics:
  - "price_move" (default): stop_loss_pct is compared against the raw price
    change percent. At k=5x, a 10% stop triggers only after BTC moves 10% —
    by which point the trade has already lost 10% of notional = 50% of the
    margin committed = 7.5% of total capital. This is the historical behaviour.

    WARNING: for leveraged strategies this means effective margin-loss at SL
    trigger = stop_loss_pct × leverage. Set P300_STOP_SEMANTICS=margin to
    cap per-trade ROE drawdown instead.

  - "margin": stop_loss_pct is compared against the trade's PnL as a
    percentage of committed margin (pre-leverage allocation). At k=5x, a
    10% margin-loss stop triggers at a 2% price move. This caps the ROE
    drawdown per trade regardless of leverage.

Why price_move is the default (deliberate, tracked decision — see
AUDIT_2026_05_05.md):
  All historical sleeve win-rates and DD numbers in PORTFOLIO.md were
  produced with price_move semantics. Flipping the default would silently
  change every variant's expected behaviour in live trading and invalidate
  the calibration of stop_loss_pct values that were tuned per-sleeve in
  upstream research. Operators who want leverage-adjusted ROE caps should
  opt in via P300_STOP_SEMANTICS=margin and re-tune the per-sleeve pcts.

Switch via env var at runtime (so a single code path supports both without
rebuilding). Default unchanged from the original implementation.

Usage in a SL branch:
    from strategies.support.risk_config import effective_price_move_sl_pct
    sl_thresh = effective_price_move_sl_pct(stop_loss_pct, leverage)
    if live_pnl_pct <= -sl_thresh:
        ...close...
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("dashboard.risk_config")

_WARNED_PRICE_MOVE = False


def sl_semantic() -> str:
    """Return 'price_move' (default) or 'margin'."""
    global _WARNED_PRICE_MOVE
    val = (os.environ.get("P300_STOP_SEMANTICS", "price_move") or "price_move").strip().lower()
    if val in ("margin", "margin_loss", "notional", "notional_loss"):
        return "margin"
    if not _WARNED_PRICE_MOVE:
        log.warning("SL semantic is 'price_move' (leverage-unaware). "
                    "Set P300_STOP_SEMANTICS=margin for leverage-adjusted stops.")
        _WARNED_PRICE_MOVE = True
    return "price_move"


def effective_price_move_sl_pct(configured_pct: float, leverage: float) -> float:
    """Convert the configured SL (in whichever semantic) to a price-move
    threshold suitable for comparison against `(current - entry)/entry * 100`
    (sign already adjusted for direction).

    - 'price_move': returns configured_pct as-is.
    - 'margin':     returns configured_pct / leverage — so a configured
                    margin-loss X% triggers when price moves X/leverage %.
                    At leverage == 0 we defensively fall back to configured.
    """
    if sl_semantic() == "margin" and leverage and leverage > 0:
        return float(configured_pct) / float(leverage)
    return float(configured_pct)

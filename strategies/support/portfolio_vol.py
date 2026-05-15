"""Portfolio-level vol-target scalar — P2.4c.

Two implementations of "today's vol-target leverage":

  - **Legacy (J+-only, BTC-vol-rank).** ``today_inputs()["lev"]``
    exposes a regime-conditional scalar (computed by
    :mod:`strategies.support.voltarget`) based on the J+ family's
    recent 1x-equivalent BTC returns. Only the six J+ sub-sleeves
    consume it. Tactical sleeves don't vol-target at all.

  - **Portfolio-vol (P2.4c).** Compute realized vol from the variant's
    NAV (the trades ledger's daily-return sum, all sleeves combined),
    scalar = ``target_vol / realized_vol``, clamped to ``[FLOOR, CAP]``.
    Applied uniformly to every sleeve. This is the proper way to
    target a portfolio-level risk number: correlation < 1 between
    sleeves means combined vol < sum of individual vols, so per-sleeve
    vol-targeting double-counts the diversification benefit.

The behavior switch is the variant's
``spec.allocator_notes.use_portfolio_vol`` flag (default False, so
today's J+-only behavior is preserved). When True,
:func:`compute_portfolio_vol_scalar` produces the scalar and
:func:`current_vol_scalar` returns it for every sleeve. When False,
legacy semantics apply.

Tactical-sleeve consumption of the scalar is a separate opt-in (each
sleeve adds a `leverage *= _effective_vol_scalar` line). For the
initial activation pass, the flag can be flipped on without sleeve
edits — only J+ sleeves (already wired to read
``_effective_vol_scalar``) will see the new number; tactical sleeves
continue ignoring the field. That gives one full paper week to verify
the new scalar's behaviour on J+ before extending to tactical.
"""
from __future__ import annotations

import math
import statistics
from datetime import timedelta
from typing import Optional


# J+ family — these sleeves use the vol-target scalar today.
_JPLUS_SLEEVES = frozenset({
    "JPLUS_R4_BTC", "JPLUS_R4_ETH", "JPLUS_R4_BTC_V2", "JPLUS_R4_ETH_V2",
    "JPLUS_EMA_BTC", "JPLUS_ETH_DAILY",
})

# Portfolio-vol calibration constants.
TARGET_VOL_ANNUAL_DEFAULT = 0.30   # 30% annualized — matches J+ regime caps
LEV_FLOOR = 0.5                    # never below half-leverage
LEV_CAP = 3.0                      # never above the J+ family's per-regime cap
WINDOW_DAYS = 30                   # rolling window for realized vol
MIN_OBS_FOR_VOL = 10               # below this, fall back to legacy scalar


def compute_portfolio_vol_scalar(
    variant_id: str,
    capital_usdt: float,
    target_vol_annual: float = TARGET_VOL_ANNUAL_DEFAULT,
    window_days: int = WINDOW_DAYS,
) -> Optional[float]:
    """Compute a portfolio-vol scalar from the variant's realized NAV.

    Reads the last ``window_days`` of daily realized returns from the
    trades ledger via :func:`strategies.support.strategy_health.trades_daily_returns`.
    Annualized stdev × √365 -> ``target_vol_annual / realized_vol``,
    clamped to ``[LEV_FLOOR, LEV_CAP]``.

    Returns ``None`` when there's not enough data to estimate (fewer
    than :data:`MIN_OBS_FOR_VOL` non-zero observations, or realized
    vol of zero). The caller should fall back to the legacy J+ scalar
    or to ``None`` for tactical sleeves.
    """
    from strategies.support import clock
    from strategies.support.strategy_health import trades_daily_returns

    today = clock.now_utc().date()
    start = today - timedelta(days=window_days + 1)
    daily = trades_daily_returns(
        variant_id, start.isoformat(), today.isoformat(),
        capital_usdt, zero_fill=True,
    )
    if len(daily) < MIN_OBS_FOR_VOL:
        return None
    # daily is list of (date_iso, return_pct). Convert to fractions.
    rets = [r / 100.0 for _, r in daily]
    if len(rets) < 2:
        return None
    sd = statistics.pstdev(rets)
    if sd <= 0 or math.isnan(sd):
        return None
    realized_vol_ann = sd * math.sqrt(365)
    if realized_vol_ann <= 0:
        return None
    scalar = target_vol_annual / realized_vol_ann
    return max(LEV_FLOOR, min(LEV_CAP, scalar))


def current_vol_scalar(strategy_id: str,
                       variant: Optional[dict] = None) -> Optional[float]:
    """Today's vol-target leverage scalar for ``strategy_id``.

    When the variant's ``spec.allocator_notes.use_portfolio_vol`` is
    True (and we can compute it), returns the portfolio-vol scalar for
    EVERY sleeve. Otherwise falls back to the legacy J+-only behaviour:
    ``today_inputs()['lev']`` for J+ family, ``None`` for tactical.

    The ``variant`` argument is the orchestrator's variant dict (with
    its spec); when called without it (legacy callers, tests), only
    the legacy path is taken.
    """
    # Portfolio-vol path (opt-in via spec flag).
    if variant is not None:
        spec = variant.get("spec") or {}
        notes = spec.get("allocator_notes") or {}
        if notes.get("use_portfolio_vol"):
            capital = float(variant.get("capital_usdt") or 10000)
            target = float(notes.get("target_vol_annual",
                                       TARGET_VOL_ANNUAL_DEFAULT))
            scalar = compute_portfolio_vol_scalar(
                variant["id"], capital, target_vol_annual=target,
            )
            if scalar is not None:
                return scalar
            # Fall through to legacy if no NAV history yet.

    # Legacy path: J+ family sleeves only.
    if strategy_id not in _JPLUS_SLEEVES:
        return None
    from strategies.support import jplus_inputs
    ti = jplus_inputs.today_inputs()
    if ti is None:
        return None
    return float(ti["lev"])

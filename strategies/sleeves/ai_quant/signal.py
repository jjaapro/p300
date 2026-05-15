"""AI_QUANT sleeve — live paper service.

Fires once per UTC day at 00:05–00:15. Every minute the orchestrator
calls try_fire_for_variant; we short-circuit cheaply on the four gates
(kill-switch / time-window / per-day-already-fired / daily-cost-cap)
before any LLM call. On the one tick of the day that passes all four,
we build the context bundle, render a baseline chart, run the
Anthropic tool-use loop, persist the decision row, and reconcile the
existing AI_QUANT position with the new directional view.

Reconciliation matrix (effective_direction ← FLAT if conviction<30):

  Current pos | Effective | Action
  ------------|-----------|-----------------------------------------------
  none        | FLAT      | noop
  none        | LONG      | open LONG (allocation = weight × conv/100)
  none        | SHORT     | open SHORT (allocation = weight × conv/100)
  LONG        | FLAT      | close existing
  LONG        | LONG      | held (no scaling in v1)
  LONG        | SHORT     | flipped: close existing, then open SHORT
  SHORT       | FLAT/LONG/SHORT | mirror of LONG cases

This is a paper-ONLY service. Trades route through trades.open_paper_trade
and trades.close_perp_trade — no exchange orders ever. Sized identically
to the algorithmic sleeves so PnL is comparable.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

from strategies import trades
from strategies.support import clock, price_feed
from . import (
    chart,
    context as ctx_mod,
    decision as decision_mod,
    journal,
)
from .config import (
    SLEEVE_NAME, DEFAULT_ASSET,
    ENTRY_WINDOW_HOURS_UTC, ENTRY_WINDOW_START_MIN, ENTRY_WINDOW_END_MIN,
    DEFAULT_DAILY_COST_CAP_USD, MIN_CONVICTION_FOR_TRADE,
    MAX_DEFERS_PER_DAY, DEFER_LATEST_HOUR, DEFER_LATEST_MIN,
)

log = logging.getLogger("p300.ai_quant_service")


# ─── Gates ──────────────────────────────────────────────────────────────────

def _kill_switch_on() -> bool:
    """AI_QUANT_ENABLED must be 'true' (case-insensitive). Defaults to OFF."""
    return os.environ.get("AI_QUANT_ENABLED", "").strip().lower() == "true"


def _in_entry_window(now=None) -> bool:
    now = now or clock.now_utc()
    return (
        now.hour == ENTRY_WINDOW_HOURS_UTC
        and ENTRY_WINDOW_START_MIN <= now.minute <= ENTRY_WINDOW_END_MIN
    )


def _daily_cost_cap_usd() -> float:
    """Read the daily API-cost cap (USD) from the env, with sanity bounds.

    Default is $5/day. A typo like "50" instead of "5.0" would have sailed
    through pre-2026-05-14 as $50/day (10× the intent); bound to a [0.01,
    50] range to catch that class of mistake while still allowing a
    deliberate ramp toward the 5% sleeve allocation."""
    raw = os.environ.get("AI_QUANT_DAILY_COST_CAP_USD")
    if not raw:
        return DEFAULT_DAILY_COST_CAP_USD
    try:
        v = float(raw)
    except ValueError:
        log.warning(f"[ai_quant] AI_QUANT_DAILY_COST_CAP_USD={raw!r} not "
                    f"parseable — using default ${DEFAULT_DAILY_COST_CAP_USD}/day")
        return DEFAULT_DAILY_COST_CAP_USD
    if not 0.01 <= v <= 50.0:
        log.warning(f"[ai_quant] AI_QUANT_DAILY_COST_CAP_USD={v} outside "
                    f"sane range [0.01, 50] — using default "
                    f"${DEFAULT_DAILY_COST_CAP_USD}/day (suspected typo)")
        return DEFAULT_DAILY_COST_CAP_USD
    return v


def _compute_defer_until_utc(now: datetime, retry_in_hours: float) -> int:
    """Convert the model's relative retry_in_hours to an absolute unix-ts,
    clamped so the deferred call still lands on today's UTC date (≤ 23:55).
    Past 23:55 the runtime would lose the deferred slot to the next day's
    00:05 entry window, so we cap aggressively rather than spill over.

    Logs a warning when the clamp fires — the model is told the defer
    fired but the realized defer is shorter than requested, which can
    mislead retrospective review (and any future decision_history that
    surfaces the model's prior-decision retry expectations)."""
    target = now + timedelta(hours=retry_in_hours)
    end_of_day = now.replace(
        hour=DEFER_LATEST_HOUR, minute=DEFER_LATEST_MIN,
        second=0, microsecond=0,
    )
    if target > end_of_day:
        clamped_hours = (end_of_day - now).total_seconds() / 3600.0
        log.warning(
            f"[ai_quant] defer clamped: model asked retry_in_hours="
            f"{retry_in_hours:.2f}, capped to {clamped_hours:.2f}h "
            f"(≤ 23:55 UTC same day)"
        )
        target = end_of_day
    return int(target.timestamp())


# ─── Sizing helpers ─────────────────────────────────────────────────────────

def _resolve_leverage(sleeve_cfg: dict) -> float:
    """orchestrator._resolve_sleeve_leverage adds _effective_leverage onto
    sleeve_cfg before dispatch; fall back to params.leverage if not present."""
    eff = sleeve_cfg.get("_effective_leverage")
    if eff is not None:
        return float(eff)
    return float((sleeve_cfg.get("params") or {}).get("leverage", 1.0))


def _allocation_pct_for(conviction: int, weight_pct: float) -> float:
    """Conviction-scaled allocation, capped at weight_pct."""
    a = weight_pct * (max(0, min(100, int(conviction))) / 100.0)
    return min(a, weight_pct)


def _effective_direction(payload: dict) -> str:
    """Apply the conviction-floor rule: FLAT if direction is FLAT or
    conviction < MIN_CONVICTION_FOR_TRADE. Returns LONG / SHORT / FLAT."""
    direction = (payload.get("direction") or "").upper()
    if direction == "FLAT":
        return "FLAT"
    try:
        conv = int(payload.get("conviction_0_100") or 0)
    except (TypeError, ValueError):
        conv = 0
    if conv < MIN_CONVICTION_FOR_TRADE:
        return "FLAT"
    if direction in ("LONG", "SHORT"):
        return direction
    return "FLAT"


# ─── Reconciliation ─────────────────────────────────────────────────────────

def _reconcile(
    *,
    variant: dict,
    sleeve_cfg: dict,
    asset: str,
    current_open: list[dict],
    decision_payload: dict,
    live_price: float | None,
) -> tuple[str, dict]:
    """Apply the decision against the current position. Returns
    (trade_action_string, debug_dict). May open and/or close paper trades
    as a side effect.

    trade_action shapes (consumed by journal & verification):
      "noop"
      "held"
      "opened:SJ-1234"
      "closed:SJ-1234"
      "flipped:SJ-old->SJ-new"
      "skipped:no_price"
    """
    # AI_QUANT: weight_pct is the *cap* — conviction (0-100) scales the
    # actual allocation inside it via _allocation_pct_for. The migration to
    # _effective_weight_pct just swaps the source of the cap; conviction
    # scaling stays unchanged.
    weight_pct = float(sleeve_cfg.get("_effective_weight_pct",
                                        sleeve_cfg.get("weight_pct", 0.0)))
    leverage = _resolve_leverage(sleeve_cfg)
    eff_direction = _effective_direction(decision_payload)
    conviction = int(decision_payload.get("conviction_0_100") or 0)

    open_trade = current_open[0] if current_open else None
    current_dir = (open_trade["direction"] if open_trade else None)

    # ── Case: no open position ──
    if open_trade is None:
        if eff_direction == "FLAT":
            return "noop", {"reason": "flat_decision_no_position"}
        if live_price is None:
            return "skipped:no_price", {"reason": "live_price_unavailable"}
        alloc = _allocation_pct_for(conviction, weight_pct)
        # P2.4e: AI_QUANT can go LONG or SHORT (LLM-discretionary). Skip if
        # another sleeve already has an opposite-direction perp open on
        # this asset — first-come-first-served. CARRY's perp SHORT is
        # excluded inside conflict_resolver (delta-neutral collateral).
        from strategies.support import conflict_resolver
        opposing = conflict_resolver.detect_opposing_open(
            variant["id"], asset, eff_direction)
        if opposing is not None:
            return "skipped:directional_conflict", {
                "reason": "directional_conflict",
                "intended_direction": eff_direction,
                "conflicting_trade_id": opposing["id"],
                "conflicting_strategy": opposing["strategy"],
                "conflicting_direction": opposing["direction"],
            }
        # P2.4d: AI_QUANT is the first sleeve to opt into the margin-headroom
        # cap (lowest-priority sleeve — additive 2%, default-OFF, naturally
        # yields when the variant's notional pool is tight). Skip the open
        # if the candidate notional would push the variant over its
        # gross_notional_target_x.
        capital = float(variant.get("capital_usdt") or 10000)
        candidate_notional = capital * (alloc / 100.0) * leverage
        from strategies.support import margin_headroom
        ok, reason = margin_headroom.can_open(variant, candidate_notional)
        if not ok:
            return "skipped:margin_constrained", {
                "reason": reason,
                "alloc_pct_intended": alloc,
                "candidate_notional_usdt": candidate_notional,
            }
        tid = trades.open_paper_trade(
            variant=variant,
            sleeve_name=SLEEVE_NAME,
            asset=asset,
            direction=eff_direction,
            entry_price=live_price,
            allocation_pct=alloc,
            leverage=leverage,
            reason={
                "sleeve": SLEEVE_NAME,
                "ai_decision": _summarize_payload(decision_payload),
                "allocation_pct_used": alloc,
                "weight_pct_max": weight_pct,
            },
            scheduled_exit_dt=None,
        )
        return f"opened:{tid}", {
            "trade_id": tid, "alloc_pct": alloc,
            "entry_price": live_price, "leverage": leverage,
        }

    # ── Case: open position exists ──
    if eff_direction == "FLAT":
        if live_price is None:
            return "skipped:no_price", {"reason": "live_price_unavailable_close"}
        trades.close_perp_trade(
            trade_id=open_trade["id"], exit_price=live_price,
            reason="ai_quant_flat", sleeve_name=SLEEVE_NAME,
        )
        return f"closed:{open_trade['id']}", {
            "closed_trade_id": open_trade["id"], "exit_price": live_price,
        }

    if eff_direction == current_dir:
        return "held", {
            "trade_id": open_trade["id"], "direction": current_dir,
            "entry_price": open_trade.get("avg_entry_price") or open_trade.get("entry_price"),
        }

    # Direction flipped: close existing, open opposite
    if live_price is None:
        return "skipped:no_price", {"reason": "live_price_unavailable_flip"}
    trades.close_perp_trade(
        trade_id=open_trade["id"], exit_price=live_price,
        reason="ai_quant_flip", sleeve_name=SLEEVE_NAME,
    )
    alloc = _allocation_pct_for(conviction, weight_pct)
    # P2.4e: after the close, the AI_QUANT position is gone but other
    # sleeves may have an opposite-direction open on this asset. Abort
    # the new opposite open if so — leaves the variant FLAT on this
    # asset for AI_QUANT until the next tick.
    from strategies.support import conflict_resolver
    opposing = conflict_resolver.detect_opposing_open(
        variant["id"], asset, eff_direction)
    if opposing is not None:
        return (
            f"closed:{open_trade['id']}",
            {
                "closed_trade_id": open_trade["id"],
                "flip_aborted": "directional_conflict",
                "intended_direction": eff_direction,
                "conflicting_trade_id": opposing["id"],
                "conflicting_strategy": opposing["strategy"],
                "conflicting_direction": opposing["direction"],
            },
        )
    # P2.4d: same margin-headroom check as the fresh-entry path. Note
    # the prior position is already closed at this point, so the
    # candidate adds back into the variant's pool without netting
    # against itself.
    capital = float(variant.get("capital_usdt") or 10000)
    candidate_notional = capital * (alloc / 100.0) * leverage
    from strategies.support import margin_headroom
    ok, reason = margin_headroom.can_open(variant, candidate_notional)
    if not ok:
        return (
            f"closed:{open_trade['id']}",  # flip aborted at open step
            {
                "closed_trade_id": open_trade["id"],
                "flip_aborted": "margin_constrained",
                "reason": reason,
                "alloc_pct_intended": alloc,
                "candidate_notional_usdt": candidate_notional,
            },
        )
    new_tid = trades.open_paper_trade(
        variant=variant,
        sleeve_name=SLEEVE_NAME,
        asset=asset,
        direction=eff_direction,
        entry_price=live_price,
        allocation_pct=alloc,
        leverage=leverage,
        reason={
            "sleeve": SLEEVE_NAME,
            "ai_decision": _summarize_payload(decision_payload),
            "flipped_from_trade": open_trade["id"],
            "allocation_pct_used": alloc,
        },
        scheduled_exit_dt=None,
    )
    return (
        f"flipped:{open_trade['id']}->{new_tid}",
        {"closed_trade_id": open_trade["id"], "new_trade_id": new_tid,
         "alloc_pct": alloc, "entry_price": live_price, "leverage": leverage},
    )


def _summarize_payload(payload: dict) -> dict:
    """Compact decision summary for the trade's `reason` notes column."""
    return {
        "direction": payload.get("direction"),
        "conviction": payload.get("conviction_0_100"),
        "horizon_days": payload.get("time_horizon_days"),
        "drivers": (payload.get("key_drivers") or [])[:3],
    }


# ─── Entry point ───────────────────────────────────────────────────────────

def try_fire_for_variant(variant: dict, sleeve_cfg: dict) -> dict:
    """Variant-engine dispatch entry point. Returns a status dict.

    Status values:
      disabled            — kill switch off
      off_window          — outside the 00:05–00:15 UTC entry window
      already_fired_today — idempotency: today's row already exists
      cost_capped         — daily cost cap exceeded
      decision_error      — LLM call failed; ERROR row written
      decided             — decision recorded; trade_action shows what we did
    """
    variant_id = variant["id"]
    asset = (sleeve_cfg.get("params") or {}).get("asset") or DEFAULT_ASSET

    if not _kill_switch_on():
        return {"status": "disabled"}

    # Defer-aware idempotency: if today's latest row is an active defer,
    # block; if it's an expired defer, allow re-fire and bypass the entry
    # window check; if it's a real LONG/SHORT/FLAT/DEFER-chain-exhausted
    # decision, the standard once-per-day rule applies.
    today_row = journal.get_today_decision(variant_id)
    bypass_entry_window = False
    if today_row is not None:
        if today_row.get("decided") == "DEFER":
            defer_until = today_row.get("defer_until_utc")
            now_ts = clock.now_ts()
            if defer_until is not None and now_ts < int(defer_until):
                return {
                    "status": "deferred_waiting",
                    "until_utc": int(defer_until),
                    "waiting_for": today_row.get("confidence_caveats"),
                }
            # Defer expired — proceed without re-checking the entry window.
            bypass_entry_window = True
        else:
            return {"status": "already_fired_today"}

    if not bypass_entry_window and not _in_entry_window():
        return {"status": "off_window"}

    cap = _daily_cost_cap_usd()
    spent = journal.get_today_cost_usd(variant_id)
    if spent >= cap:
        log.warning(f"AI_QUANT cost cap hit for {variant_id}: "
                     f"${spent:.4f} >= ${cap:.4f}")
        return {"status": "cost_capped", "spent_usd": spent, "cap_usd": cap}

    # Heavy lifting from here on.
    current_open = trades.get_open_trades(variant_id, SLEEVE_NAME, asset)
    open_overlay = [{
        "direction": t["direction"],
        "entry_price": t.get("avg_entry_price") or t.get("entry_price"),
        "entry_dt": t.get("actual_entry_time") or t.get("entry_time"),
    } for t in current_open]

    try:
        context_bundle = ctx_mod.build_context(variant_id, asset)
    except Exception as e:  # noqa: BLE001
        log.exception(f"AI_QUANT context build failed for {variant_id}")
        # Persist a synthetic ERROR row so idempotency triggers next tick
        _persist_error(
            variant_id=variant_id, asset=asset,
            error=f"context_build: {type(e).__name__}: {e}",
            context_bundle=None, trade_action="error",
        )
        return {"status": "decision_error", "error": "context_build"}

    try:
        baseline_png = chart.render_chart(
            asset=asset, timeframe="1d", lookback_bars=90,
            indicators=None, open_positions=open_overlay or None,
        )
    except Exception as e:  # noqa: BLE001
        log.exception(f"AI_QUANT chart render failed for {variant_id}")
        _persist_error(
            variant_id=variant_id, asset=asset,
            error=f"chart_render: {type(e).__name__}: {e}",
            context_bundle=context_bundle, trade_action="error",
        )
        return {"status": "decision_error", "error": "chart_render"}

    defers_today = journal.count_today_defers(variant_id)
    allow_defer = defers_today < MAX_DEFERS_PER_DAY

    result = decision_mod.run_decision(
        variant_id=variant_id,
        asset=asset,
        open_positions=open_overlay or None,
        client=sleeve_cfg.get("_anthropic_client"),  # tests inject; production None
        include_server_tools=sleeve_cfg.get("_include_server_tools", True),
        allow_defer=allow_defer,
        context_bundle=context_bundle,
        baseline_chart_png=baseline_png,
    )

    if result.deferred is not None:
        defer_until = _compute_defer_until_utc(
            clock.now_utc(),
            float(result.deferred.get("retry_in_hours", 1.0)),
        )
        journal.save_decision(
            variant_id=variant_id, asset=asset,
            decision_result=result, context_bundle=context_bundle,
            trade_action="deferred",
            defer_until_utc=defer_until,
        )
        return {
            "status": "deferred",
            "asset": asset,
            "waiting_for": result.deferred.get("waiting_for"),
            "retry_at_utc": defer_until,
            "defers_today": defers_today + 1,
            "max_defers_per_day": MAX_DEFERS_PER_DAY,
            "turns": result.turns,
        }

    if result.decision is None:
        journal.save_decision(
            variant_id=variant_id, asset=asset,
            decision_result=result, context_bundle=context_bundle,
            trade_action="error",
        )
        return {"status": "decision_error", "error": result.error}

    live_price = price_feed.get_current_price(asset)
    trade_action, debug = _reconcile(
        variant=variant, sleeve_cfg=sleeve_cfg, asset=asset,
        current_open=current_open, decision_payload=result.decision,
        live_price=live_price,
    )
    journal.save_decision(
        variant_id=variant_id, asset=asset,
        decision_result=result, context_bundle=context_bundle,
        trade_action=trade_action,
    )
    return {
        "status": "decided",
        "asset": asset,
        "decision": result.decision["direction"],
        "conviction": result.decision["conviction_0_100"],
        "horizon_days": result.decision.get("time_horizon_days"),
        "trade_action": trade_action,
        "turns": result.turns,
        **debug,
    }


def _persist_error(
    *, variant_id: str, asset: str, error: str,
    context_bundle: dict | None, trade_action: str,
) -> None:
    """Synthetic ERROR row when we never reached the API call. Mirrors what
    journal.save_decision writes for a failed run_decision return."""
    fake = decision_mod.DecisionResult(
        decision=None, error=error, turns=0, tool_calls=[],
        usage={}, cost_usd=0.0, model_id=os.environ.get("AI_QUANT_MODEL", ""),
    )
    journal.save_decision(
        variant_id=variant_id, asset=asset, decision_result=fake,
        context_bundle=context_bundle, trade_action=trade_action,
    )

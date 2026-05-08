"""AI_QUANT sleeve — live shadow service.

Fires once per UTC day at 00:05–00:15. Every minute the variant_engine
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

This is a SHADOW-ONLY service. Trades route through trades.open_shadow_trade
and trades.close_perp_trade — no exchange orders ever. Sized identically
to the algorithmic sleeves so PnL is comparable.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from services import clock, price_feed, trades
from services.ai_quant import (
    chart,
    context as ctx_mod,
    decision as decision_mod,
    journal,
)

log = logging.getLogger("p300.ai_quant_service")

SLEEVE_NAME = "AI_QUANT"
DEFAULT_ASSET = "BTC"
ENTRY_WINDOW_HOURS_UTC = 0          # window opens at 00:00 UTC
ENTRY_WINDOW_START_MIN = 5          # 00:05
ENTRY_WINDOW_END_MIN = 15           # 00:15 (inclusive)
DEFAULT_DAILY_COST_CAP_USD = 5.0
MIN_CONVICTION_FOR_TRADE = 30


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
    raw = os.environ.get("AI_QUANT_DAILY_COST_CAP_USD")
    if not raw:
        return DEFAULT_DAILY_COST_CAP_USD
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_DAILY_COST_CAP_USD


# ─── Sizing helpers ─────────────────────────────────────────────────────────

def _resolve_leverage(sleeve_cfg: dict) -> float:
    """variant_engine._resolve_sleeve_leverage adds _effective_leverage onto
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
    (trade_action_string, debug_dict). May open and/or close shadow trades
    as a side effect.

    trade_action shapes (consumed by journal & verification):
      "noop"
      "held"
      "opened:SJ-1234"
      "closed:SJ-1234"
      "flipped:SJ-old->SJ-new"
      "skipped:no_price"
    """
    weight_pct = float(sleeve_cfg.get("weight_pct", 0.0))
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
        tid = trades.open_shadow_trade(
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
        return f"opened:{tid}", {"trade_id": tid, "alloc_pct": alloc}

    # ── Case: open position exists ──
    if eff_direction == "FLAT":
        if live_price is None:
            return "skipped:no_price", {"reason": "live_price_unavailable_close"}
        trades.close_perp_trade(
            trade_id=open_trade["id"], exit_price=live_price,
            reason="ai_quant_flat", sleeve_name=SLEEVE_NAME,
        )
        return f"closed:{open_trade['id']}", {"closed_trade_id": open_trade["id"]}

    if eff_direction == current_dir:
        return "held", {"trade_id": open_trade["id"], "direction": current_dir}

    # Direction flipped: close existing, open opposite
    if live_price is None:
        return "skipped:no_price", {"reason": "live_price_unavailable_flip"}
    trades.close_perp_trade(
        trade_id=open_trade["id"], exit_price=live_price,
        reason="ai_quant_flip", sleeve_name=SLEEVE_NAME,
    )
    alloc = _allocation_pct_for(conviction, weight_pct)
    new_tid = trades.open_shadow_trade(
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
         "alloc_pct": alloc},
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

    if not _in_entry_window():
        return {"status": "off_window"}

    if journal.get_today_decision(variant_id) is not None:
        return {"status": "already_fired_today"}

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

    result = decision_mod.run_decision(
        variant_id=variant_id,
        asset=asset,
        open_positions=open_overlay or None,
        client=sleeve_cfg.get("_anthropic_client"),  # tests inject; production None
        include_server_tools=sleeve_cfg.get("_include_server_tools", True),
        context_bundle=context_bundle,
        baseline_chart_png=baseline_png,
    )

    if result.decision is None:
        journal.save_decision(
            variant_id=variant_id, asset=asset,
            decision_result=result, context_bundle=context_bundle,
            trade_action="error",
        )
        return {"status": "decision_error", "error": result.error,
                "cost_usd": result.cost_usd}

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
        "decision": result.decision["direction"],
        "conviction": result.decision["conviction_0_100"],
        "trade_action": trade_action,
        "cost_usd": result.cost_usd,
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

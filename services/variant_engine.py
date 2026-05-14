"""
Variant engine — scheduler tick that drives shadow variants alongside the prod
portfolio.

Invariants:
  - The PRIMARY variant flows through execution_service (PAPER/LIVE, real sizing,
    real reconciliation). This engine does NOT touch primary execution.
  - SHADOW variants produce phantom trades only. They never call
    exchange_service; they never enter execution_service._execute_entry.
  - Each shadow trade is tagged with strategy_variant=<variant_id> and
    execution_mode='SHADOW'.

V1 scope: R4 window shadows (signal_overlay variants that mod R4 BTC / R4 ETH
entry hours and sizing multipliers). The existing jplus overlay (P-100 J+ 1.0)
is now just one row in the registry — the engine iterates all enabled shadow
signal_overlay variants and fires their windows.

Full-portfolio shadow continuous-sleeve replication (phantom EMA/ETH_SPOT/GOLD
positions per variant) is deferred. When a full_portfolio variant is enabled,
it still gets R4 shadow trades via its resolved spec, but continuous sleeves
reuse the parent variant's ledger.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from services import clock, db, trade_db, trades, variant_registry
from services.price_feed import _get_current_price

log = logging.getLogger("dashboard.variant_engine")


# ─── Spec resolution ─────────────────────────────────────────────────────────

def resolve_effective_spec(variant: dict) -> dict:
    """Return the full effective spec for a variant.

    full_portfolio: spec as-is.
    signal_overlay: parent spec merged with sleeve_modifiers.
    """
    spec = dict(variant.get("spec") or {})
    if variant["kind"] == "full_portfolio":
        return spec
    # signal_overlay — resolve parent
    parent_id = spec.get("parent_variant_id") or variant.get("parent_variant_id")
    if not parent_id:
        return spec  # orphan overlay — treat as full_portfolio
    parent = variant_registry.get_variant(parent_id)
    if parent is None:
        log.warning(f"[variant_engine] overlay {variant['id']} points to missing parent {parent_id}")
        return spec
    effective = dict(parent.get("spec") or {})
    # Stash modifiers for downstream use
    effective["_overlay_modifiers"] = spec.get("sleeve_modifiers") or {}
    effective["_parent_id"] = parent_id
    return effective


def r4_window_for(variant: dict, sleeve: str) -> dict | None:
    """Resolve the R4 entry window config for the given sleeve within this
    variant. Returns {entry_hour, exit_hour, entry_weekday, sizing_multiplier,
    disable_vol_target}, or None if this variant doesn't modify the sleeve
    (meaning it uses parent's window)."""
    spec = variant.get("spec") or {}
    if variant["kind"] == "signal_overlay":
        mods = spec.get("sleeve_modifiers") or {}
        cfg = mods.get(sleeve)
        if not cfg:
            return None
        return cfg
    # full_portfolio — R4 config lives at spec root
    prefix_map = {
        "R4_BTC": ("r4_btc_entry_hour", "r4_btc_exit_hour", None),
        "R4_ETH": ("r4_eth_entry_hour", "r4_eth_exit_hour", "r4_eth_entry_weekday"),
    }
    if sleeve not in prefix_map:
        return None
    eh, xh, wd = prefix_map[sleeve]
    if eh not in spec:
        return None
    out = {"entry_hour": spec[eh], "exit_hour": spec[xh]}
    if wd and wd in spec:
        out["entry_weekday"] = spec[wd]
    return out


# ─── Shadow tick ─────────────────────────────────────────────────────────────

def _is_r4_day(dt: datetime) -> bool:
    return 1 <= dt.day <= 14


def _is_r4_btc_day(dt: datetime) -> bool:
    return dt.weekday() in (0, 2) and _is_r4_day(dt)


def _is_r4_eth_day(dt: datetime) -> bool:
    return dt.weekday() == 2 and _is_r4_day(dt)


def _floor_to_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _shadow_trade_exists(variant_id: str, strategy: str, entry_iso: str,
                         asset: str) -> bool:
    """Check if a shadow trade already exists for (variant_id, strategy, entry_time, asset)."""
    import sqlite3
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        row = con.execute(
            "SELECT 1 FROM trades WHERE strategy_variant = ? AND strategy = ? "
            "AND asset = ? AND entry_time = ? LIMIT 1",
            (variant_id, strategy, asset, entry_iso),
        ).fetchone()
        return row is not None
    finally:
        con.close()


def _create_shadow_trade(
    *, variant: dict, asset: str, direction: str, strategy: str,
    allocation_pct: float, leverage: float,
    entry_time_iso: str, exit_time_iso: str,
    entry_price: float, reason: dict,
) -> str:
    """Insert a shadow open trade tagged with variant_id. Bypasses
    execution_service entirely — never touches exchange."""
    import json
    import sqlite3
    capital = float(variant.get("capital_usdt") or
                    trade_db.get_config("paper_account_usdt") or "10000")
    size_usdt = capital * (allocation_pct / 100.0) * leverage
    qty = size_usdt / entry_price if entry_price > 0 else 0
    now_iso = clock.now_iso()

    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT id FROM trades WHERE series='SJ' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            tid = "SJ-0001"
        else:
            num = int(row["id"].split("-")[1]) + 1
            tid = f"SJ-{num:04d}"

        con.execute("""
            INSERT INTO trades (id, series, asset, direction, strategy, regime,
                allocation_pct, leverage, entry_time, exit_time, status,
                execution_mode, strategy_variant, actual_entry_time,
                entry_price, size_usdt, qty, order_ids, notes)
            VALUES (?, 'SJ', ?, ?, ?, ?, ?, ?, ?, ?, 'open',
                    'SHADOW', ?, ?, ?, ?, ?, ?, ?)
        """, (tid, asset, direction, strategy, reason.get("regime", "unknown"),
              allocation_pct, leverage, entry_time_iso, exit_time_iso,
              variant["id"], now_iso, entry_price, size_usdt, qty,
              json.dumps([f"SHADOW-{tid}"]),
              json.dumps(reason, default=str)))
        con.commit()
    finally:
        con.close()
    return tid


def _close_due_shadows(now_utc: datetime) -> None:
    """Close any open shadow trade whose scheduled exit_time has passed.

    SCOPE: only trades belonging to ENABLED variants. Replay variants are
    registered with enabled=0 (see backtest_runner.ensure_replay_variant)
    so the live engine does not touch their phantom trades. Without this
    filter, the live tick sees a backtest's open shadow trade (with
    historical exit_time long past wall-clock now) and silently closes it
    at the live price — corrupting any backtest run while the live bot
    is up. This is the root cause of the SJ-1169/1506/1557/1562 leaks.
    """
    import sqlite3
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        opens = con.execute(
            "SELECT t.id, t.asset, t.strategy_variant, t.exit_time, t.entry_price, "
            "       t.qty, t.size_usdt, t.direction, t.strategy "
            "FROM trades t JOIN variants v ON t.strategy_variant = v.id "
            "WHERE t.execution_mode = 'SHADOW' AND t.status = 'open' "
            "  AND v.enabled = 1"
        ).fetchall()
    finally:
        con.close()

    for t in opens:
        exit_time = t["exit_time"]
        if not exit_time:
            continue
        try:
            exit_dt = datetime.fromisoformat(exit_time)
            if exit_dt.tzinfo is None:
                exit_dt = exit_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if now_utc < exit_dt:
            continue
        price = _get_current_price(t["asset"])
        if price is None:
            log.warning(f"[shadow] no exit price for {t['id']} {t['asset']} — skipping")
            continue
        trades.close_perp_trade(t["id"], price, "scheduled_exit",
                                sleeve_name=t["strategy"])


def _maybe_open_r4_window(variant: dict, sleeve: str, asset: str,
                          now_utc: datetime) -> None:
    """Open a shadow R4 window for (variant, sleeve, asset) if now is the
    entry minute."""
    if sleeve == "R4_BTC" and not _is_r4_btc_day(now_utc):
        return
    if sleeve == "R4_ETH":
        # ETH R4 lives on Wed. J+ overlay may entry on Tuesday 20:00 targeting
        # Wed exit. Determine by the window's entry_weekday if present.
        pass

    cfg = r4_window_for(variant, sleeve)
    if cfg is None:
        return

    entry_hour = cfg.get("entry_hour")
    exit_hour = cfg.get("exit_hour")
    entry_weekday = cfg.get("entry_weekday")  # None => same day
    sizing_mult = float(cfg.get("sizing_multiplier") or 1.0)

    if entry_hour is None or exit_hour is None:
        return
    if now_utc.hour != int(entry_hour):
        return

    # Validate day eligibility
    if sleeve == "R4_ETH":
        if entry_weekday is not None:
            if now_utc.weekday() != int(entry_weekday):
                return
            tomorrow = now_utc + timedelta(days=1)
            if not _is_r4_eth_day(tomorrow):
                return
        else:
            if not _is_r4_eth_day(now_utc):
                return

    entry_dt = _floor_to_hour(now_utc)
    if entry_weekday is not None and sleeve == "R4_ETH":
        exit_dt = (entry_dt + timedelta(days=1)).replace(hour=int(exit_hour))
    else:
        exit_dt = entry_dt.replace(hour=int(exit_hour))
        if exit_dt <= entry_dt:
            exit_dt += timedelta(days=1)

    strategy = "R4"
    entry_iso = entry_dt.isoformat()
    if _shadow_trade_exists(variant["id"], strategy, entry_iso, asset):
        return

    price = _get_current_price(asset)
    if price is None:
        log.warning(f"[shadow {variant['id']}] no {asset} price — skip R4 open")
        return

    # Base allocation: 15% (matches strong_bull R4 weight). Multiplied by sizing.
    base_alloc = 15.0
    alloc_pct = base_alloc * sizing_mult

    reason = {
        "variant_id": variant["id"],
        "variant_short_name": variant["short_name"],
        "sleeve": sleeve,
        "window": f"{entry_hour:02d}:00-{exit_hour:02d}:00 UTC",
        "sizing_multiplier": sizing_mult,
        "regime": "unknown",  # TODO: plumb regime if needed
    }
    tid = _create_shadow_trade(
        variant=variant, asset=asset, direction="LONG", strategy=strategy,
        allocation_pct=alloc_pct, leverage=1.0,
        entry_time_iso=entry_iso, exit_time_iso=exit_dt.isoformat(),
        entry_price=price, reason=reason,
    )
    log.info(f"[shadow {variant['id']}] opened {tid} {asset} R4 LONG @ {price:.2f} "
             f"(window {reason['window']}, sizing ×{sizing_mult})")


# ─── Composition dispatch ────────────────────────────────────────────────────

# Maps a strategy_id (from variant spec composition) to the live service that
# executes its shadow trades. Services expose try_fire_for_variant(variant,
# sleeve_cfg) -> status dict. Missing entries = sleeve is not yet live-wired
# (logged once per tick but doesn't crash the engine).
STRATEGY_DISPATCH: dict[str, Any] = {}


def _load_dispatch():
    """Lazy import services so a broken service module doesn't prevent module
    import. Called from tick()."""
    global STRATEGY_DISPATCH
    if STRATEGY_DISPATCH:
        return
    from strategies.sleeves.adx import signal as adx_sleeve
    from strategies.sleeves.thu_bear import signal as thu_bear_sleeve
    from strategies.sleeves.cpr import signal as cpr_sleeve
    from strategies.sleeves.pdo import signal as pdo_sleeve
    from strategies.sleeves.carry import signal as carry_sleeve
    from strategies.sleeves.fomc import signal as fomc_sleeve
    from strategies.sleeves.ai_quant import signal as ai_quant_sleeve
    from strategies.sleeves.r4 import signal as r4_sleeve
    from strategies.sleeves.ema import signal as ema_sleeve
    from strategies.sleeves.eth_daily import signal as eth_daily_sleeve
    STRATEGY_DISPATCH = {
        "S-003":           adx_sleeve.try_fire_for_variant,
        "S-096":           thu_bear_sleeve.try_fire_for_variant,
        "S-078":           carry_sleeve.try_fire_for_variant,
        "PDO-L-RF":        pdo_sleeve.try_fire_for_variant,
        "CPR":             cpr_sleeve.try_fire_for_variant,
        "FOMC":            fomc_sleeve.try_fire_for_variant,
        # Core J+ sub-sleeves — live entry handlers (Phases 1-3 of the
        # live-execution refactor). Each opens its own discrete trades
        # at the calendar/signal moment with the live price.
        "JPLUS_R4_BTC":    r4_sleeve.r4_btc_try_fire,
        "JPLUS_R4_ETH":    r4_sleeve.r4_eth_try_fire,
        "JPLUS_R4_BTC_V2": r4_sleeve.r4_btc_v2_try_fire,
        "JPLUS_R4_ETH_V2": r4_sleeve.r4_eth_v2_try_fire,
        "JPLUS_EMA_BTC":   ema_sleeve.ema_btc_try_fire,
        "JPLUS_ETH_DAILY": eth_daily_sleeve.eth_daily_try_fire,
        # AI_QUANT — discretionary LLM trader. Default-off via
        # AI_QUANT_ENABLED env so the dispatch is wired but no API
        # cost is incurred until the user explicitly opts in.
        "AI_QUANT":        ai_quant_sleeve.try_fire_for_variant,
    }


_warned_missing: set[tuple[str, str]] = set()


_SLEEVE_KEY_FOR_STRATEGY = {"S-003": "s003", "S-096": "s096", "S-078": "s078",
                             "PDO-L-RF": "pdo", "CPR": "cpr", "FOMC": "fomc",
                             "JPLUS_R4_BTC": "r4_btc",
                             "JPLUS_R4_ETH": "r4_eth",
                             "JPLUS_EMA_BTC": "ema_btc",
                             "JPLUS_ETH_DAILY": "eth_daily"}


def _resolve_sleeve_leverage(spec: dict, sleeve: dict) -> float:
    """Resolve the per-sleeve leverage multiplier.

    Priority (first non-None wins):
      1. sleeve['params']['leverage']   — per-composition-entry override
      2. spec['sleeve_leverages'][key]  — top-level map, key in {core,s003,s096,s078}
      3. 1.0 default

    Returns a float leverage to be multiplied into notional size at dispatch
    time. Services apply this to size_usdt and write it to trade.leverage.
    """
    params = sleeve.get("params") or {}
    lev = params.get("leverage")
    if lev is None:
        top = spec.get("sleeve_leverages") or {}
        key = _SLEEVE_KEY_FOR_STRATEGY.get(sleeve.get("strategy_id"))
        if key:
            lev = top.get(key)
    try:
        return float(lev) if lev is not None else 1.0
    except (TypeError, ValueError):
        return 1.0


def _tick_composition(variant: dict, now_utc: datetime) -> None:
    """Dispatch each sleeve in a full_portfolio composition spec to its live
    service. Portfolio-to-portfolio references (e.g., p100_jplus_v2_0 as
    the 60% core sleeve) are skipped here to avoid double-firing — they run
    in their own right as separate enabled variants.

    Per-sleeve leverage (for levered variants like Aggressive 2.0) is
    resolved here and injected into the sleeve_cfg as `_effective_leverage`
    — downstream services multiply it into size_usdt and write to
    trade.leverage."""
    _load_dispatch()
    spec = variant.get("spec") or {}
    composition = spec.get("composition") or []
    for sleeve in composition:
        portfolio_id = sleeve.get("portfolio_id")
        strategy_id = sleeve.get("strategy_id")
        if portfolio_id:
            # Core sleeve refs another variant — that variant runs independently
            # as its own shadow. Don't double-fire. Equity attribution to
            # P-200's composition is handled in the equity-series endpoint.
            continue
        if not strategy_id:
            continue
        # Resolve and inject per-sleeve leverage (non-destructive copy)
        sleeve_with_k = dict(sleeve)
        sleeve_with_k["_effective_leverage"] = _resolve_sleeve_leverage(spec, sleeve)
        sleeve = sleeve_with_k
        dispatcher = STRATEGY_DISPATCH.get(strategy_id)
        if dispatcher is None:
            key = (variant["id"], strategy_id)
            if key not in _warned_missing:
                log.warning(f"[{variant['id']}] no live service for {strategy_id} "
                            f"— sleeve skipped (deferred)")
                _warned_missing.add(key)
            continue
        try:
            result = dispatcher(variant, sleeve)
            if result and result.get("status") not in ("no_action", "not_thursday",
                                                       "already_fired_today", "warmup",
                                                       "deferred_waiting"):
                log.info(f"[{variant['id']}] {strategy_id} -> {result}")
        except Exception as e:
            log.exception(f"[{variant['id']}] {strategy_id} dispatch error: {e}")


def _check_liquidations_all_variants(now_utc) -> int:
    """Per-tick liquidation sweep across every active shadow variant.

    Reuses ``backtest_runner.check_liquidations_for_variant`` — the same
    function the research replay runs — so live SHADOW, ``run.py --mode
    sim``, and the backtest path all evaluate margin trajectories with
    identical logic. Each open SHADOW trade whose entry→now path would
    have breached maintenance margin gets force-closed via the sleeve's
    close function with reason ``forced_exit:liquidation``.

    Wrapped in try/except per-variant so a single misbehaving variant
    cannot abort the tick. Returns total trades force-closed across all
    variants this tick (logged as a warning if non-zero)."""
    total = 0
    shadows = variant_registry.get_active_shadows()
    for v in shadows:
        try:
            from backtest_runner import check_liquidations_for_variant
            total += check_liquidations_for_variant(v["id"], now_utc)
        except Exception as e:
            log.exception(f"[liq {v['id']}] check raised: {e}")
    if total > 0:
        log.warning(f"[liq] {total} trade(s) force-closed this tick")
    return total


def tick() -> None:
    """Scheduler entry point — runs every minute.

    1. Liquidation sweep — force-close any open SHADOW trade whose
       entry→now margin trajectory would have breached maintenance.
       Runs BEFORE scheduled-exit checks so a liquidated trade is not
       re-counted as a scheduled close.
    2. Close any shadow trades whose scheduled exit_time has passed.
    3. Tick the FOMC observer (records would-be FOMC decisions + P&L
       without opening shadow trades). Out-of-portfolio research feed.
    4. For each enabled shadow variant:
       - signal_overlay variants: evaluate their R4 window modifiers
       - full_portfolio with composition: dispatch each sleeve to its service
    """
    now_utc = clock.now_utc()
    _check_liquidations_all_variants(now_utc)
    _close_due_shadows(now_utc)

    # FOMC observer — runs unconditionally each tick, regardless of variants.
    try:
        from strategies.sleeves.fomc import signal as fomc_sleeve
        fomc_sleeve.tick_observer()
    except Exception as e:
        log.exception(f"[fomc-observer] tick error: {e}")

    shadows = variant_registry.get_active_shadows()
    for v in shadows:
        try:
            if v["kind"] == "signal_overlay":
                _maybe_open_r4_window(v, "R4_BTC", "BTC", now_utc)
                _maybe_open_r4_window(v, "R4_ETH", "ETH", now_utc)
            elif v["kind"] == "full_portfolio":
                spec = v.get("spec") or {}
                if "composition" in spec:
                    _tick_composition(v, now_utc)
                else:
                    # Full portfolio with own R4 windows (no composition)
                    _maybe_open_r4_window(v, "R4_BTC", "BTC", now_utc)
                    _maybe_open_r4_window(v, "R4_ETH", "ETH", now_utc)
        except Exception as e:
            log.exception(f"[shadow {v['id']}] tick error: {e}")


# ─── Read helpers for the UI / API ───────────────────────────────────────────

def get_variant_equity_series(variant_id: str) -> list[dict]:
    """Return cumulative equity curve for a variant.

    Source selection:
      - spec.equity_source == 'daily_returns' → compound from variant_daily_returns
        (legacy P-200-style variants whose trades are partial/seeded).
        Returns [] if the table doesn't exist (fresh DB).
      - default 'trades' → accumulate pnl_usdt across closed trades for this variant

    Starts equity from variant.capital_usdt (or paper_account_usdt fallback).
    Uses ``services.db.DASH_DB`` so a sim-mode DB redirection propagates here.
    """
    import sqlite3
    from services import db as _db_mod
    db_path = str(_db_mod.DASH_DB)
    v = variant_registry.get_variant(variant_id)
    if v is None:
        return []
    capital = float(v.get("capital_usdt") or trade_db.get_config("paper_account_usdt") or 10000)
    source = (v.get("spec") or {}).get("equity_source", "trades")

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    if source == "daily_returns":
        try:
            rows = con.execute(
                "SELECT date, return_1x_pct FROM variant_daily_returns "
                "WHERE variant_id = ? ORDER BY date ASC",
                (variant_id,),
            ).fetchall()
        except sqlite3.OperationalError as e:
            if "no such table" in str(e).lower():
                rows = []
            else:
                con.close()
                raise
        con.close()
        out = []
        equity = capital
        for r in rows:
            equity *= (1 + (r["return_1x_pct"] or 0) / 100.0)
            out.append({
                "time": f"{r['date']}T00:00:00+00:00",
                "equity_usdt": round(equity, 2),
                "return_pct": round((equity / capital - 1) * 100, 4),
            })
        return out

    # Default: trades-based (P-100 prod, P-100 J+ 2.0, etc.)
    rows = con.execute(
        "SELECT actual_exit_time, pnl_usdt FROM trades "
        "WHERE strategy_variant = ? AND status = 'closed' "
        "AND actual_exit_time IS NOT NULL AND pnl_usdt IS NOT NULL "
        "ORDER BY actual_exit_time ASC",
        (variant_id,),
    ).fetchall()
    con.close()

    out = []
    equity = capital
    for r in rows:
        pnl = r["pnl_usdt"] or 0
        equity += pnl
        out.append({
            "time": r["actual_exit_time"],
            "equity_usdt": round(equity, 2),
            "return_pct": round((equity / capital - 1) * 100, 4),
        })
    return out

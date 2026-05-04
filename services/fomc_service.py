"""FOMC sleeve — long BTC into FOMC announcements, conditional on regime + sentiment.

Trade window: enter at T-10h before announcement, exit at T+0.5h.
Announcement is 14:00 ET on the FOMC date (DST-aware).

Decision rule (derived from 52 historical events 2020-2026):

  Phase × Action      Win%    Mean
  ──────────────────  ─────   ─────
  peak_hold           100%    +1.69%   <- best historical, always trade
  hiking              83%     +1.69%
  zirp_hold           79%     +1.17%
  cutting             70%     +1.26%
  mid_hold            25%     -0.70%   <- always skip

  Cut -25bp (any)     20%     -0.84%   <- always skip (override)
  Hike >=+50bp        83%     +3.07%
  Emergency cut       67%     +0.28%

  F&G at FOMC         Win%    Mean
  ──────────────────  ─────   ─────
  Extreme Fear (<=25) 100%    +2.40%   <- override unlocks even in mid_hold
  Extreme Greed (>75) 40%     +1.18%   <- override skip even in good phase

Composite rule:
  HARD SKIP if expected_action == 'cut_25'
  HARD SKIP if F&G == 'extreme_greed'
  HARD TRADE if F&G == 'extreme_fear' AND phase != 'mid_hold'
  SKIP if phase == 'mid_hold' (default for current 2025-2026 environment)
  TRADE otherwise

Mode: OBSERVER ONLY for v1 — decisions and would-be P&L are logged to the
fomc_observer table, no shadow trades are opened. After 4-6 live FOMCs,
review the decision log and decide whether to promote to a real sleeve.

Promote path:
  1. Confirm observer's would-be returns match the conditional backtest.
  2. Reallocate the variant's 5% cash reserve into a 'FOMC' composition
     entry with strategy_id='FOMC'.
  3. Replace `record_observer_decision()` calls in tick() with the standard
     _create_shadow_trade flow.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from services import clock

log = logging.getLogger("p300.fomc")

REPO = Path(__file__).resolve().parent.parent
TRADER_DB = REPO / "data" / "trader.db"
DASH_DB = REPO / "data" / "dashboard.db"

ET = ZoneInfo("America/New_York")

# Trade window (minutes from FOMC announcement)
ENTRY_OFFSET_MIN = -10 * 60   # T-10h
EXIT_OFFSET_MIN = 30           # T+0.5h
# Tolerance for picking up the entry/exit minute; 60s is enough since variant_engine ticks every minute
WINDOW_TOL_MIN = 1


# ─── Schema ──────────────────────────────────────────────────────────────────

def init_schema() -> None:
    """Create fomc_observer table if missing. Idempotent."""
    con = sqlite3.connect(str(TRADER_DB))
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS fomc_observer (
                fomc_date TEXT PRIMARY KEY,
                announcement_utc TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                target_rate_pct REAL,
                phase TEXT,
                expected_action TEXT,
                expected_action_meta TEXT,
                fear_greed INTEGER,
                fear_greed_bucket TEXT,
                entry_planned_utc TEXT,
                exit_planned_utc TEXT,
                entry_price REAL,
                exit_price REAL,
                return_pct REAL,
                recorded_at TEXT NOT NULL
            )
        """)
        con.commit()
    finally:
        con.close()


# ─── Decision logic ──────────────────────────────────────────────────────────

def _remaining_2026_meetings_after(date_str: str) -> int:
    """Count FOMC dates in scheduled_events strictly AFTER `date_str`,
    within calendar year 2026."""
    con = sqlite3.connect(str(TRADER_DB))
    try:
        n = con.execute("""
            SELECT COUNT(*) FROM scheduled_events
            WHERE event_type='FOMC' AND date > ? AND date < '2027-01-01'
        """, (date_str,)).fetchone()[0]
        return int(n)
    finally:
        con.close()


def evaluate(fomc_date: str) -> dict:
    """Decide whether to trade this FOMC and produce a structured rationale.

    Returns dict with keys: decision ('trade'|'skip'), reason, phase,
    target_rate_pct, expected_action, fear_greed.
    """
    from services import fed_funds_service, sentiment_index_service, polymarket_service

    target_rate = fed_funds_service.get_target_rate(fomc_date)
    phase = fed_funds_service.classify_phase(fomc_date)
    fg = sentiment_index_service.get_value(fomc_date)
    fg_bucket = sentiment_index_service.bucket(fg)

    remaining = _remaining_2026_meetings_after(fomc_date)
    expected_action, ea_meta = polymarket_service.expected_action_for_meeting(
        fomc_date, remaining)

    decision = "trade"
    reasons: list[str] = []

    # Hard skips (override everything else)
    if expected_action == "cut_25":
        decision = "skip"
        reasons.append("expected_action=cut_25 (-25bp cuts: 20% historical win rate)")
    if fg_bucket == "extreme_greed":
        decision = "skip"
        reasons.append(f"fg={fg} extreme_greed (40% historical win rate at FOMC)")

    # Extreme fear override unlocks the trade unless mid_hold
    extreme_fear_override = (
        decision == "trade"
        and fg_bucket == "extreme_fear"
        and phase != "mid_hold"
    )

    # Phase skip — mid_hold has 25% historical win rate
    if decision == "trade" and phase == "mid_hold" and not extreme_fear_override:
        decision = "skip"
        reasons.append(f"phase=mid_hold (25% historical win rate)")

    if decision == "trade":
        if extreme_fear_override:
            reasons.append(f"fg={fg} extreme_fear at FOMC -> 8/8 historical wins")
        else:
            reasons.append(f"phase={phase}, fg_bucket={fg_bucket}, "
                            f"expected_action={expected_action}")

    return {
        "decision": decision,
        "reason": "; ".join(reasons),
        "target_rate_pct": target_rate,
        "phase": phase,
        "expected_action": expected_action,
        "expected_action_meta": ea_meta,
        "fear_greed": fg,
        "fear_greed_bucket": fg_bucket,
    }


# ─── Calendar lookup ─────────────────────────────────────────────────────────

def announcement_dt_utc(fomc_date: str) -> datetime:
    """14:00 ET on `fomc_date`, converted to UTC. DST-aware."""
    et_dt = datetime.fromisoformat(fomc_date).replace(hour=14, minute=0,
                                                        tzinfo=ET)
    return et_dt.astimezone(timezone.utc)


def next_fomc_date(now_utc: datetime, lookahead_days: int = 60) -> str | None:
    """Next FOMC date strictly >= now_utc.date(), within `lookahead_days`.
    None if no upcoming FOMC in the window."""
    today = now_utc.date()
    horizon = today + timedelta(days=lookahead_days)
    con = sqlite3.connect(str(TRADER_DB))
    try:
        row = con.execute("""
            SELECT date FROM scheduled_events
            WHERE event_type='FOMC' AND date >= ? AND date <= ?
            ORDER BY date LIMIT 1
        """, (today.isoformat(), horizon.isoformat())).fetchone()
        return row[0] if row else None
    finally:
        con.close()


# ─── Observer recording ──────────────────────────────────────────────────────

def _has_observer_record(fomc_date: str) -> bool:
    con = sqlite3.connect(str(TRADER_DB))
    try:
        return con.execute(
            "SELECT 1 FROM fomc_observer WHERE fomc_date = ?",
            (fomc_date,)).fetchone() is not None
    finally:
        con.close()


def _upsert_observer_decision(fomc_date: str, eval_result: dict,
                                announcement: datetime) -> None:
    """Idempotent insert of the decision row. P&L fields filled later."""
    init_schema()
    con = sqlite3.connect(str(TRADER_DB))
    try:
        entry_dt = announcement + timedelta(minutes=ENTRY_OFFSET_MIN)
        exit_dt = announcement + timedelta(minutes=EXIT_OFFSET_MIN)
        con.execute("""
            INSERT OR IGNORE INTO fomc_observer
            (fomc_date, announcement_utc, decision, reason, target_rate_pct,
             phase, expected_action, expected_action_meta,
             fear_greed, fear_greed_bucket,
             entry_planned_utc, exit_planned_utc, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (fomc_date, announcement.isoformat(), eval_result["decision"],
              eval_result["reason"], eval_result["target_rate_pct"],
              eval_result["phase"], eval_result["expected_action"],
              json.dumps(eval_result.get("expected_action_meta") or {}),
              eval_result["fear_greed"], eval_result["fear_greed_bucket"],
              entry_dt.isoformat(), exit_dt.isoformat(),
              clock.now_utc().isoformat()))
        con.commit()
    finally:
        con.close()


def _record_entry_price(fomc_date: str, price: float) -> None:
    con = sqlite3.connect(str(TRADER_DB))
    try:
        con.execute("UPDATE fomc_observer SET entry_price = ? "
                    "WHERE fomc_date = ? AND entry_price IS NULL",
                    (price, fomc_date))
        con.commit()
    finally:
        con.close()


def _record_exit_price(fomc_date: str, price: float) -> None:
    con = sqlite3.connect(str(TRADER_DB))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT entry_price FROM fomc_observer "
                           "WHERE fomc_date = ? AND exit_price IS NULL",
                           (fomc_date,)).fetchone()
        if row is None or row["entry_price"] is None:
            return
        ret_pct = (price / float(row["entry_price"]) - 1) * 100
        con.execute("UPDATE fomc_observer SET exit_price = ?, return_pct = ? "
                    "WHERE fomc_date = ?",
                    (price, ret_pct, fomc_date))
        con.commit()
    finally:
        con.close()


# ─── Tick (called by variant_engine each minute) ─────────────────────────────

def tick_observer() -> dict:
    """Observer tick — runs every minute alongside the variant engine.

    Three things it does:
      1. As soon as a future FOMC is within 11h, write the decision row
         (idempotent). This makes the planned action visible BEFORE the
         entry minute even if the bot starts mid-cycle.
      2. At T-10h ± WINDOW_TOL_MIN, if decision == 'trade' and entry_price
         is missing, snapshot the BTC price.
      3. At T+0.5h ± WINDOW_TOL_MIN, if entry_price is set and exit_price
         is missing, snapshot the BTC price and compute return_pct.

    Returns a small status dict for logging.
    """
    from services import price_feed

    now = clock.now_utc()
    fomc_date = next_fomc_date(now, lookahead_days=60)
    if fomc_date is None:
        return {"status": "no_upcoming_fomc"}

    announcement = announcement_dt_utc(fomc_date)
    minutes_to_ann = (announcement - now).total_seconds() / 60.0

    # Phase 1: pre-decide as soon as we're within 11h
    if minutes_to_ann <= 11 * 60 + 5 and not _has_observer_record(fomc_date):
        eval_result = evaluate(fomc_date)
        _upsert_observer_decision(fomc_date, eval_result, announcement)
        log.info(f"[fomc-observer] {fomc_date} decision={eval_result['decision']} "
                 f"reason={eval_result['reason']}")

    # Phase 2: entry-price snapshot at T-10h
    target_entry = announcement + timedelta(minutes=ENTRY_OFFSET_MIN)
    if abs((now - target_entry).total_seconds()) <= WINDOW_TOL_MIN * 60:
        con = sqlite3.connect(str(TRADER_DB))
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT decision, entry_price FROM fomc_observer WHERE fomc_date = ?",
                (fomc_date,)).fetchone()
        finally:
            con.close()
        if row and row["decision"] == "trade" and row["entry_price"] is None:
            price = price_feed.get_current_price("BTC")
            if price is not None:
                _record_entry_price(fomc_date, price)
                log.info(f"[fomc-observer] {fomc_date} entry @ {price:.2f}")
                return {"status": "entry_recorded", "fomc_date": fomc_date,
                        "entry_price": price}

    # Phase 3: exit-price snapshot at T+0.5h
    target_exit = announcement + timedelta(minutes=EXIT_OFFSET_MIN)
    if abs((now - target_exit).total_seconds()) <= WINDOW_TOL_MIN * 60:
        price = price_feed.get_current_price("BTC")
        if price is not None:
            _record_exit_price(fomc_date, price)
            log.info(f"[fomc-observer] {fomc_date} exit @ {price:.2f}")
            return {"status": "exit_recorded", "fomc_date": fomc_date,
                    "exit_price": price}

    return {"status": "ok", "fomc_date": fomc_date,
            "minutes_to_announcement": round(minutes_to_ann, 1)}


# ─── Reporting ───────────────────────────────────────────────────────────────

# ─── Trade-mode dispatcher (used by variant_engine + backtest_runner) ────────

# Round-trip taker fee estimate (5bp entry + 5bp exit).
COST_BP_RT = 10.0


def _open_fomc_long(variant: dict, asset: str, entry_price: float,
                     allocation_pct: float, leverage: float,
                     reason: dict, exit_iso: str) -> str:
    """Mirror the standard tactical-sleeve open path. Writes to dashboard.db
    'trades' table tagged with the variant_id, strategy='FOMC', execution_mode
    ='SHADOW'. Idempotency is the caller's responsibility."""
    import json
    import sqlite3
    from services import trade_db
    capital = float(variant.get("capital_usdt") or
                    trade_db.get_config("paper_account_usdt") or 10000)
    size_usdt = capital * (allocation_pct / 100.0) * leverage
    qty = size_usdt / entry_price if entry_price > 0 else 0
    now_iso = clock.now_utc().isoformat()
    con = sqlite3.connect(str(DASH_DB))
    try:
        row = con.execute(
            "SELECT id FROM trades WHERE series='SJ' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            tid = "SJ-0001"
        else:
            tid = f"SJ-{int(row[0].split('-')[1]) + 1:04d}"
        con.execute("""
            INSERT INTO trades (id, series, asset, direction, strategy, regime,
                allocation_pct, leverage, entry_time, exit_time, status,
                execution_mode, strategy_variant, actual_entry_time,
                entry_price, size_usdt, qty, order_ids, notes)
            VALUES (?, 'SJ', ?, 'LONG', 'FOMC', ?, ?, ?, ?, ?, 'open',
                    'SHADOW', ?, ?, ?, ?, ?, ?, ?)
        """, (tid, asset, reason.get("phase", "unknown"), allocation_pct,
              leverage, now_iso, exit_iso, variant["id"], now_iso,
              entry_price, size_usdt, qty,
              json.dumps([f"SHADOW-{tid}"]),
              json.dumps(reason, default=str)))
        con.commit()
    finally:
        con.close()
    return tid


def _close_fomc_shadow(trade_id: str, exit_price: float, reason: str) -> None:
    """Sleeve close — delegates to services.trades.close_perp_trade."""
    from services.trades import close_perp_trade
    close_perp_trade(trade_id, exit_price, reason, sleeve_name="FOMC",
                     cost_bp_rt=COST_BP_RT, apply_funding=True)


def _has_fomc_trade(variant_id: str, fomc_date: str) -> bool:
    """True if a FOMC trade for this (variant, fomc_date) already exists."""
    import sqlite3
    con = sqlite3.connect(str(DASH_DB))
    try:
        # Idempotent on entry_time (which we set to T-10h; a second open
        # at the same minute would be a duplicate).
        target_entry = announcement_dt_utc(fomc_date) + timedelta(minutes=ENTRY_OFFSET_MIN)
        row = con.execute(
            "SELECT 1 FROM trades WHERE strategy_variant=? AND strategy='FOMC' "
            "AND entry_time=? LIMIT 1",
            (variant_id, target_entry.isoformat())).fetchone()
        return row is not None
    finally:
        con.close()


def _sweep_stuck_opens(variant_id: str) -> int:
    """Defense-in-depth: close any FOMC trade for this variant whose
    scheduled exit_time has passed. Runs at the start of every dispatcher
    tick so a missed close_due_for_variant pass (e.g. a transient stale
    price) gets caught on the very next minute, not at end-of-window.
    Returns number closed."""
    import sqlite3
    from services.price_feed import get_current_price
    now = clock.now_utc()
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, asset, exit_time FROM trades "
            "WHERE strategy_variant=? AND strategy='FOMC' "
            "  AND status='open' AND execution_mode='SHADOW'",
            (variant_id,)).fetchall()
    finally:
        con.close()
    n = 0
    for r in rows:
        try:
            exit_dt = datetime.fromisoformat(r["exit_time"])
        except (TypeError, ValueError):
            continue
        if exit_dt.tzinfo is None:
            exit_dt = exit_dt.replace(tzinfo=timezone.utc)
        if now < exit_dt:
            continue
        price = get_current_price(r["asset"])
        if price is None:
            continue
        try:
            _close_fomc_shadow(r["id"], price, "self_sweep_past_exit")
            n += 1
            log.info(f"[fomc {variant_id}] self-sweep closed {r['id']} "
                     f"{r['asset']} @ {price:.2f}")
        except Exception:
            log.exception(f"[fomc {variant_id}] self-sweep close failed for {r['id']}")
    return n


def try_fire_for_variant(variant: dict, sleeve_cfg: dict) -> dict:
    """Sleeve interface — called once per tick by variant_engine and the
    replay runner. Trades BTC long T-10h to T+0.5h on FOMC days that pass
    the regime filter.

    Idempotency: one FOMC trade per (variant_id, fomc_date), keyed on the
    entry_time being the announcement T-10h timestamp.

    Returns a status dict with the same shape as other sleeves.
    """
    from services.price_feed import get_current_price

    now = clock.now_utc()
    # Always sweep stuck opens first — independent of next_fomc_date so a
    # post-event tick can still close trades from the just-finished meeting.
    swept = _sweep_stuck_opens(variant["id"])

    fomc_date = next_fomc_date(now, lookahead_days=2)
    if fomc_date is None:
        return {"status": "no_upcoming_fomc", "swept": swept}

    announcement = announcement_dt_utc(fomc_date)
    target_entry = announcement + timedelta(minutes=ENTRY_OFFSET_MIN)
    target_exit = announcement + timedelta(minutes=EXIT_OFFSET_MIN)

    # Always keep the observer row in sync (idempotent insert) so the
    # decision log is populated even if we ultimately skip.
    if not _has_observer_record(fomc_date):
        eval_result = evaluate(fomc_date)
        _upsert_observer_decision(fomc_date, eval_result, announcement)
    else:
        # cached on first call this meeting; cheap reread
        import sqlite3
        from pathlib import Path
        con = sqlite3.connect(str(REPO / "data" / "trader.db"))
        con.row_factory = sqlite3.Row
        try:
            r = con.execute(
                "SELECT decision, phase, fear_greed_bucket, expected_action "
                "FROM fomc_observer WHERE fomc_date=?", (fomc_date,)).fetchone()
        finally:
            con.close()
        eval_result = {
            "decision": r["decision"], "phase": r["phase"],
            "fear_greed_bucket": r["fear_greed_bucket"],
            "expected_action": r["expected_action"],
        }

    if eval_result["decision"] != "trade":
        return {"status": "skip", "fomc_date": fomc_date,
                "reason": eval_result.get("reason", "see_observer_table")}

    # Open at the FIRST tick where now >= target_entry (and before target_exit).
    # Idempotency keeps subsequent ticks within the entry window from
    # opening duplicates.
    if not (target_entry <= now < target_exit):
        return {"status": "outside_window", "fomc_date": fomc_date}
    if _has_fomc_trade(variant["id"], fomc_date):
        return {"status": "already_open", "fomc_date": fomc_date}

    asset = "BTC"
    price = get_current_price(asset)
    if price is None:
        log.warning(f"[fomc {variant['id']}] no {asset} price at "
                    f"{now.isoformat()} — skip entry")
        return {"status": "no_price", "fomc_date": fomc_date}

    alloc_pct = float(sleeve_cfg.get("weight_pct", 0.0))
    leverage = float(sleeve_cfg.get("_effective_leverage", 1.0))
    reason = {
        "trigger": "FOMC_long",
        "variant_id": variant["id"],
        "sleeve": "FOMC",
        "fomc_date": fomc_date,
        "phase": eval_result.get("phase"),
        "fear_greed_bucket": eval_result.get("fear_greed_bucket"),
        "expected_action": eval_result.get("expected_action"),
        "target_entry_utc": target_entry.isoformat(),
        "target_exit_utc": target_exit.isoformat(),
    }
    tid = _open_fomc_long(variant, asset, price, alloc_pct, leverage,
                           reason, target_exit.isoformat())
    # also record the entry price into the observer table for audit
    _record_entry_price(fomc_date, price)
    log.info(f"[fomc {variant['id']}] opened {tid} BTC LONG @ {price:.2f} "
             f"(fomc={fomc_date}, alloc={alloc_pct}%, k={leverage}x, "
             f"phase={eval_result.get('phase')})")
    return {"status": "opened", "trade_id": tid, "fomc_date": fomc_date,
            "entry_price": price}


def get_decisions(limit: int = 20) -> list[dict]:
    """Return recent observer rows, newest first. For health checks / dashboards."""
    init_schema()
    con = sqlite3.connect(str(TRADER_DB))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM fomc_observer ORDER BY fomc_date DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()

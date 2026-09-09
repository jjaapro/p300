"""Standalone runner for the R4 calendar family — one process, four windows,
one variant (`bot_r4_v1`).

Windows (UTC, week 1-2 of the month = day <= 14):
  JPLUS_R4_BTC     Mon 06:00 -> 18:00
  JPLUS_R4_ETH     Tue 20:00 -> Wed 20:00 (Wed must be day <= 14)
  JPLUS_R4_BTC_V2  Wed+Fri 04:00 -> 14:00
  JPLUS_R4_ETH_V2  Wed+Fri 04:00 -> 14:00

The sleeve package is imported unchanged: each variant's decide function
owns the calendar, idempotency-per-day, regime sign (bear => no trade) and
the validated inner_lev x vol_lev stack. The bot owns what a single-variant
ledger has to own: the per-variant weight, the co-fire budget, the
late-entry guard (a fire more than LATE_ENTRY_MAX_S after the window opens
is a logged miss, never a cold fill) and the stale-input policy.

Prerequisite: `python feed.py` running (this bot never fetches).

Usage:
  python bots/r4/runner.py                 # live loop, 60s ticks
  python bots/r4/runner.py --once          # single tick and exit
  python bots/r4/runner.py --once --db data/databases/sim_r4.db
      --sim-now 2026-08-03T06:01:00+00:00  # dry run on a DB copy
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import botlib  # noqa: E402
from bots.r4 import windows as r4cal  # noqa: E402
from bots.r4 import config as botcfg  # noqa: E402

log = logging.getLogger("bot.r4")

_stop = threading.Event()

# Statuses that mean "nothing to do right now" (logged at DEBUG unless
# --verbose). Everything the tick can report except opened / blocked / error.
_IDLE_STATUSES = {"no_action", "missed_window", "budget_exhausted"}

# (strategy, UTC day) pairs already reported as missed: one WARNING + one
# diag record per window, not one per tick.
_missed: set[tuple[str, str]] = set()


def _signal_handler(signum, frame):
    log.info(f"signal {signum} received — stopping after current tick")
    _stop.set()


def _sleeve():
    from strategies.sleeves.timing_anomalies.internal.r4 import signal as r4
    return r4


def deciders() -> dict:
    r4 = _sleeve()
    from strategies.sleeves.timing_anomalies.internal.r4.config import (
        STRATEGY_R4_BTC, STRATEGY_R4_BTC_V2, STRATEGY_R4_ETH, STRATEGY_R4_ETH_V2,
    )
    return {
        STRATEGY_R4_BTC: r4.r4_btc_decide,
        STRATEGY_R4_ETH: r4.r4_eth_decide,
        STRATEGY_R4_BTC_V2: r4.r4_btc_v2_decide,
        STRATEGY_R4_ETH_V2: r4.r4_eth_v2_decide,
    }


def _diag(record: dict) -> None:
    """One JSONL line per event. `utc_date` + `counters` are the keys the
    dashboard's diag_today() sums per day (it ignores lines without them)."""
    ts = record.get("ts")
    record.setdefault("utc_date", str(ts)[:10] if ts else None)
    record.setdefault("counters", {record.get("event", "event"): 1})
    try:
        botcfg.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(botcfg.DIAG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError as e:
        log.warning(f"diag write failed: {e!r}")


def size_intent(intent, capital: float, open_gross: float, *, late_s: float):
    """Per-variant weight x the sleeve's capped stacked leverage, scaled down
    to the remaining co-fire budget. Returns (resized_intent | None, info).

    open_paper_trade sizes capital x alloc% x leverage, so alloc is pinned to
    100 x weight and the (possibly budget-scaled) notional expressed through
    leverage; the sleeve's own leverage is kept when the budget does not bind.
    """
    strategy = intent.reason["_strategy"]
    weight = float(botcfg.VARIANT_WEIGHT[strategy])
    lev = min(float(intent.leverage), botcfg.LEV_CAP)
    notional = capital * weight * lev
    budget = botcfg.GROSS_MAX_X * capital - open_gross
    capped = notional > budget
    if capped:
        notional = max(budget, 0.0)
    info = {"strategy": strategy, "weight": weight, "sleeve_lev": float(intent.leverage),
            "lev": lev, "notional": notional, "budget": budget, "capped": capped}
    if notional < botcfg.MIN_NOTIONAL_USDT:
        return None, info
    reason = dict(intent.reason or {})
    reason.update({"bot_weight": weight, "entry_latency_s": round(late_s),
                   "gross_before_usdt": round(open_gross, 2),
                   "budget_capped": capped})
    resized = dataclasses.replace(
        intent, allocation_pct=100.0 * weight,
        leverage=notional / (capital * weight), reason=reason)
    return resized, info


def _stop_sweep(variant_id: str) -> list[str]:
    """Optional intraday stop (only when STOP_LOSS_PCT is set). Closes at the
    current price, which is what a real stop would do at tick granularity."""
    if botcfg.STOP_LOSS_PCT is None:
        return []
    from strategies import trades
    from strategies.support import db
    from strategies.support.price_feed import get_current_price
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        opens = con.execute(
            "SELECT id, asset, strategy, entry_price FROM trades "
            "WHERE strategy_variant=? AND execution_mode='paper' AND status='open'",
            (variant_id,)).fetchall()
    finally:
        con.close()
    closed = []
    for tid, asset, strategy, entry in opens:
        price = get_current_price(asset)
        if price is None or entry is None:
            continue
        if price <= float(entry) * (1.0 - botcfg.STOP_LOSS_PCT):
            trades.close_perp_trade(tid, price, "stop_loss", sleeve_name=strategy)
            closed.append(tid)
            log.warning(f"STOP {tid} {strategy} @ {price:,.2f} (entry {entry:,.2f})")
    return closed


def tick(variant: dict, sleeve_cfg: dict) -> dict:
    from strategies.support import clock

    stale_mgmt = botlib.stale_tables(botcfg.MGMT_TABLES)
    if stale_mgmt:
        return {"status": "stale_mgmt_inputs", "stale": stale_mgmt,
                "hb_status": "degraded", "evaluated": False,
                "hb_note": f"mgmt tables stale: {sorted(stale_mgmt)}"}

    r4 = _sleeve()
    capital = float(variant["capital_usdt"])
    now = clock.now_utc()
    today = now.date().isoformat()
    detail: dict[str, str] = {}
    opened: list[str] = []
    missed: list[str] = []
    blocked: dict = {}
    exhausted: list[str] = []
    evaluated = False

    for strategy, decide in deciders().items():
        if not botcfg.ENABLED.get(strategy, False):
            continue
        intents, status = decide(variant, sleeve_cfg)
        st = status.get("status", "?")
        detail[strategy] = st
        if st != "no_inputs":
            evaluated = True
        if not intents:
            continue
        intent = intents[0]
        open_dt = r4cal.window_open_for(strategy, intent.scheduled_exit_dt)
        late_s = (now - open_dt).total_seconds()
        if late_s > botcfg.LATE_ENTRY_MAX_S:
            detail[strategy] = "missed_window"
            missed.append(strategy)
            key = (strategy, today)
            if key not in _missed:
                _missed.add(key)
                log.warning(f"MISSED WINDOW {strategy} {today}: now is "
                            f"+{late_s / 60:.0f} min after open (grace "
                            f"{botcfg.LATE_ENTRY_MAX_S}s) — not entering")
                _diag({"ts": now.isoformat(), "event": "missed_window",
                       "strategy": strategy, "late_s": round(late_s)})
            continue
        stale_entry = botlib.stale_tables(botcfg.ENTRY_TABLES)
        if stale_entry:
            log.warning(f"ENTRY BLOCKED {strategy} — stale entry tables: {stale_entry}")
            detail[strategy] = "entry_blocked_stale_inputs"
            blocked = stale_entry
            continue
        open_gross = botlib.open_gross_usdt(variant["id"])
        resized, info = size_intent(intent, capital, open_gross, late_s=late_s)
        if resized is None:
            detail[strategy] = "budget_exhausted"
            exhausted.append(strategy)
            log.warning(f"BUDGET EXHAUSTED {strategy}: remaining "
                        f"${info['budget']:,.0f} < min ${botcfg.MIN_NOTIONAL_USDT:,.0f} "
                        f"(open gross ${open_gross:,.0f})")
            _diag({"ts": now.isoformat(), "event": "budget_exhausted",
                   "strategy": strategy, **info})
            continue
        res = r4._r4_execute(variant, sleeve_cfg, resized)
        detail[strategy] = "opened"
        opened.append(res.get("trade_id"))
        log.info(f"OPENED {res.get('trade_id')} {strategy} notional="
                 f"${info['notional']:,.0f} (weight {info['weight']:.2f}, "
                 f"lev {info['lev']:.2f}x, capped={info['capped']}, "
                 f"latency {late_s:.0f}s)")
        _diag({"ts": now.isoformat(), "event": "opened", "trade_id": res.get("trade_id"),
               "late_s": round(late_s), **info})

    out = {"status": "no_action", "detail": detail, "hb_status": "ok", "hb_note": "",
           "evaluated": evaluated}
    if opened:
        out.update(status="opened", opened=opened, signal=True)
    elif blocked:
        out.update(status="entry_blocked_stale_inputs", hb_status="degraded",
                   hb_note=f"entry tables stale: {sorted(blocked)}")
    elif missed:
        out.update(status="missed_window",
                   hb_note="missed_window " + ",".join(missed) + f" {today}")
    elif exhausted:
        out.update(status="budget_exhausted")

    closed = botlib.close_due_trades(variant["id"])
    if closed:
        out["backstop_closed"] = closed
    stopped = _stop_sweep(variant["id"])
    if stopped:
        out["stopped"] = stopped
    return out


def _point_at_db(path: Path) -> None:
    """Dry-run only: redirect every DB constant at a copy, refusing prod.db."""
    from strategies.support import db, trade_db
    target = path.resolve()
    if target == db.PROD_DB.resolve():
        raise SystemExit(f"--db {path} is the live prod.db; dry runs need a copy")
    if not target.exists():
        raise SystemExit(f"--db {path} does not exist")
    db.PROD_DB = db.DASH_DB = db.TRADER_DB = target
    trade_db.DB_PATH = target
    log.warning(f"DRY RUN against {target}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="R4 calendar standalone bot")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=botcfg.TICK_SECONDS)
    ap.add_argument("--verbose", action="store_true",
                    help="Log idle tick statuses at INFO instead of DEBUG.")
    ap.add_argument("--db", type=Path, default=None,
                    help="Dry run against a COPY of prod.db (requires --once).")
    ap.add_argument("--sim-now", default=None,
                    help="ISO UTC timestamp to simulate (requires --once).")
    args = ap.parse_args(argv)
    if (args.db or args.sim_now) and not args.once:
        ap.error("--db / --sim-now are only valid with --once")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

    from strategies.support.env import load_env_file
    load_env_file()
    from strategies.support import clock
    if args.db:
        _point_at_db(args.db)
    if args.sim_now:
        dt = datetime.fromisoformat(args.sim_now)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        clock.set_simulated_now(dt)
        log.warning(f"SIMULATED CLOCK {dt.isoformat()}")

    botlib.ensure_wal()
    botlib.init_heartbeat_schema()
    from strategies.support import trade_db
    trade_db.init_db()

    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name=botcfg.SHORT_NAME,
        capital_usdt=botcfg.CAPITAL_USDT, bot_name=botcfg.BOT_NAME)
    enabled = [k for k, v in botcfg.ENABLED.items() if v]
    log.info(f"variant {variant['id']} capital=${variant['capital_usdt']:,.0f} "
             f"weight={botcfg.VARIANT_WEIGHT[enabled[0]]:.2f}/variant "
             f"gross_cap={botcfg.GROSS_MAX_X}x grace={botcfg.LATE_ENTRY_MAX_S}s "
             f"stop={botcfg.STOP_LOSS_PCT} enabled={enabled} "
             f"open_trades={botlib.count_open_trades(variant['id'])}")
    for w in r4cal.next_windows(clock.now_utc(), 4):
        log.info(f"next window {w['strategy']} {w['open_utc'].isoformat()} -> "
                 f"{w['close_utc'].isoformat()}")

    # No _effective_* keys: the sleeve decides with its own inputs, the bot
    # re-sizes the Intent (see size_intent).
    sleeve_cfg = {"priority": 100}

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    log.info(f"loop starting (interval={args.interval}s)")
    while not _stop.is_set():
        t0 = time.time()
        hb_status, hb_note, evaluated, signalled = "ok", "", False, False
        try:
            out = tick(variant, sleeve_cfg)
            hb_status = out.get("hb_status", "ok")
            hb_note = out.get("hb_note", "")
            evaluated = bool(out.get("evaluated"))
            signalled = bool(out.get("signal"))
            msg = (f"tick {out['status']} {out.get('detail', '')} "
                   f"({(time.time() - t0) * 1000:.0f}ms)")
            if out["status"] in _IDLE_STATUSES and not args.verbose:
                log.debug(msg)
            else:
                log.info(msg)
        except Exception as e:
            hb_status, hb_note = "error", repr(e)
            log.exception(f"tick error: {e}")
        try:
            now_iso = clock.now_utc().isoformat()
            botlib.heartbeat(
                botcfg.BOT_NAME, status=hb_status, note=hb_note,
                interval_s=args.interval,
                last_eval_utc=now_iso if evaluated else None,
                last_signal_utc=now_iso if signalled else None,
                open_trades=botlib.count_open_trades(variant["id"]))
        except Exception as e:
            log.warning(f"heartbeat write failed: {e!r}")
        if args.once:
            return 0
        for _ in range(args.interval):
            if _stop.is_set():
                break
            time.sleep(1)
    log.info("shutdown complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())

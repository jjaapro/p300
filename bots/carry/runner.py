"""Standalone runner for S-078 Carry — bot #4 of the extraction plan.

Delta-neutral funding harvest: spot-long + perp-short at equal notional
while 7d average funding is positive; exit on a 3-day negative streak
(sleeve side-effect). Market-neutral — diversifies the long-heavy fleet.
Replaces the stranded legacy CARRY position closed on 2026-07-22.

Sizing is FIXED-NOTIONAL (capital × CARRY_NOTIONAL_X): there is no stop,
so fixed-R doesn't apply; risk is basis/funding, not price.

Prerequisite: `python feed.py` running.

Usage:
  python bots/carry/runner.py           # live loop, 60s ticks
  python bots/carry/runner.py --once    # single tick and exit
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import signal
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import botlib  # noqa: E402
from bots.carry import config as botcfg  # noqa: E402

log = logging.getLogger("bot.carry")

_stop = threading.Event()

_IDLE_STATUSES = {"no_action", "warmup"}
_NOT_EVALUATED_STATUSES = {"warmup"}


def _signal_handler(signum, frame):
    log.info(f"signal {signum} received — stopping after current tick")
    _stop.set()


def size_intent(intent, capital: float):
    """Fixed-notional: alloc 100% × leverage CARRY_NOTIONAL_X →
    size_usdt = capital × CARRY_NOTIONAL_X."""
    resized = dataclasses.replace(intent, allocation_pct=100.0,
                                  leverage=botcfg.CARRY_NOTIONAL_X)
    return resized, {"notional": capital * botcfg.CARRY_NOTIONAL_X}


def tick(variant: dict, sleeve_cfg: dict) -> dict:
    from strategies.sleeves.carry import signal as sleeve

    stale_mgmt = botlib.stale_tables(botcfg.MGMT_TABLES)
    if stale_mgmt:
        return {"status": "stale_mgmt_inputs", "stale": stale_mgmt,
                "hb_status": "degraded",
                "hb_note": f"mgmt tables stale: {sorted(stale_mgmt)}"}

    intents, status = sleeve.try_decide_for_variant(variant, sleeve_cfg)
    st = status.get("status", "?")
    out = {"status": st, "detail": status, "hb_status": "ok", "hb_note": "",
           "evaluated": st not in _NOT_EVALUATED_STATUSES}

    for intent in intents:
        resized, info = size_intent(intent, float(variant["capital_usdt"]))
        res = sleeve.execute_for_variant(variant, sleeve_cfg, resized)
        log.info(f"OPENED {res.get('trade_id')} delta-neutral "
                 f"notional=${info['notional']:,.0f} "
                 f"(7d FR {res.get('fr_7d_avg_pct')}%)")
        out["opened"] = res.get("trade_id")
        out["signal"] = True

    closed = botlib.close_due_trades(variant["id"])
    if closed:
        out["backstop_closed"] = closed
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Carry S-078 standalone bot")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=botcfg.TICK_SECONDS)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

    from strategies.support.env import load_env_file
    load_env_file()

    botlib.ensure_wal()
    botlib.init_heartbeat_schema()
    from strategies.support import trade_db
    trade_db.init_db()

    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name=botcfg.SHORT_NAME,
        capital_usdt=botcfg.CAPITAL_USDT, bot_name=botcfg.BOT_NAME)
    log.info(f"variant {variant['id']} capital=${variant['capital_usdt']:,.0f} "
             f"notional={botcfg.CARRY_NOTIONAL_X}x "
             f"open_trades={botlib.count_open_trades(variant['id'])}")

    sleeve_cfg = {"weight_pct": 100.0, "_effective_leverage": 1.0,
                  "priority": 100}

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    log.info(f"loop starting (interval={args.interval}s)")
    from strategies.support import clock
    while not _stop.is_set():
        t0 = time.time()
        hb_status, hb_note, evaluated, signalled = "ok", "", False, False
        try:
            out = tick(variant, sleeve_cfg)
            hb_status = out.get("hb_status", "ok")
            hb_note = out.get("hb_note", "")
            evaluated = bool(out.get("evaluated"))
            signalled = bool(out.get("signal"))
            msg = f"tick {out['status']} ({(time.time() - t0) * 1000:.0f}ms)"
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

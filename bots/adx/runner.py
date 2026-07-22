"""Standalone runner for S-003 ADX — bot #3 of the extraction plan, shipping
the Tier-2 calibration (symmetric trend filter + ATR×4 trail + funding veto;
maxDD −27→−15%, MAR 1.78→3.09 on the 2018→2026-06 study).

Same skeleton as bots/chento_v3 and bots/short_squeeze: one process, one
strategy, one variant (`bot_adx_v1`); the sleeve package is imported
unchanged. Entry decisions are daily (sleeve-idempotent per UTC day); the
60s tick prices the fixed-SL and ATR-trail sweeps against live btc_1m.

Prerequisite: `python feed.py` running (this bot never fetches).

Usage:
  python bots/adx/runner.py           # live loop, 60s ticks
  python bots/adx/runner.py --once    # single tick and exit
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import botlib  # noqa: E402
from bots.adx import config as botcfg  # noqa: E402

log = logging.getLogger("bot.adx")

_stop = threading.Event()

_IDLE_STATUSES = {"no_action", "already_fired_today", "warmup",
                  "trend_filter_block", "funding_veto_block"}

# warmup = no decision could be made; everything else at this cadence
# means today's daily evaluation happened.
_NOT_EVALUATED_STATUSES = {"warmup"}


def _signal_handler(signum, frame):
    log.info(f"signal {signum} received — stopping after current tick")
    _stop.set()


def size_intent(intent, capital: float):
    """Fixed-R sizing (botlib.size_intent_fixed_r): 2% risk over the
    sleeve's effective initial stop (min of 10% SL and 4×ATR seed)."""
    return botlib.size_intent_fixed_r(
        intent, capital, risk_pct=botcfg.RISK_PCT,
        notional_max_x=botcfg.NOTIONAL_MAX_X)


def tick(variant: dict, sleeve_cfg: dict) -> dict:
    from strategies.sleeves.adx import signal as sleeve

    stale_mgmt = botlib.stale_tables(botcfg.MGMT_TABLES)
    if stale_mgmt:
        return {"status": "stale_mgmt_inputs", "stale": stale_mgmt,
                "hb_status": "degraded",
                "hb_note": f"mgmt tables stale: {sorted(stale_mgmt)}"}

    intents, status = sleeve.try_decide_for_variant(variant, sleeve_cfg)
    st = status.get("status", "?")
    out = {"status": st, "detail": status, "hb_status": "ok", "hb_note": "",
           "evaluated": st not in _NOT_EVALUATED_STATUSES}

    if intents:
        stale_entry = botlib.stale_tables(botcfg.ENTRY_TABLES)
        if stale_entry:
            log.warning(f"ENTRY BLOCKED — stale entry tables: {stale_entry}")
            out.update(status="entry_blocked_stale_inputs",
                       hb_status="degraded",
                       hb_note=f"entry tables stale: {sorted(stale_entry)}")
        else:
            for intent in intents:
                resized, info = size_intent(intent, float(variant["capital_usdt"]))
                res = sleeve.execute_for_variant(variant, sleeve_cfg, resized)
                log.info(f"OPENED {res.get('trade_id')} {resized.direction} "
                         f"notional=${info['notional']:,.0f} "
                         f"(stop_pct={info['stop_pct']:.2%}, "
                         f"at_cap={info['at_cap']})")
                out["opened"] = res.get("trade_id")
                out["signal"] = True

    closed = botlib.close_due_trades(variant["id"])
    if closed:
        out["backstop_closed"] = closed
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ADX S-003 T2 standalone bot")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=botcfg.TICK_SECONDS)
    ap.add_argument("--verbose", action="store_true",
                    help="Log idle tick statuses at INFO instead of DEBUG.")
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
             f"risk={botcfg.RISK_PCT}%/trade cap={botcfg.NOTIONAL_MAX_X}x "
             f"open_trades={botlib.count_open_trades(variant['id'])}")

    # The sleeve reads weight/leverage (overwritten by size_intent) plus the
    # per-variant stop-loss param from sleeve_cfg.params.
    sleeve_cfg = {"weight_pct": 100.0, "_effective_leverage": 1.0,
                  "priority": 100,
                  "params": {"stop_loss_pct": botcfg.STOP_LOSS_PCT}}

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    log.info(f"loop starting (interval={args.interval}s, T2+veto active)")
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

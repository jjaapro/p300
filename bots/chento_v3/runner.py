"""Standalone runner for CHENTO_TRIPLE_V3 — bot #1 of the extraction plan.

One process, one strategy, one variant (`bot_chento_v3_v1`). Imports the
existing sleeve package unchanged; there is no orchestrator, reconcile,
pooling, or margin sim here. What the runner adds around the sleeve:

  - fixed-R sizing        notional = capital × RISK_PCT% / stop_pct, from
                          the stop the sleeve itself computed (5×ATR)
  - stale-input refusal   stale mgmt tables → skip tick (degraded);
                          stale entry tables → sweep runs, intents dropped
  - scheduled-exit backstop  botlib.close_due_trades, behind the sleeve's
                          own stop/target/TIF bar-walking sweep
  - heartbeat             every tick, for monitor.py
  - diagnostics           CHENTO_V3_DIAG permanently on (OKX-lockout lesson)

Prerequisite: `python feed.py` running (this bot never fetches).

Usage:
  python bots/chento_v3/runner.py           # live loop, 60s ticks
  python bots/chento_v3/runner.py --once    # single tick and exit
  python bots/chento_v3/runner.py --verbose # log idle statuses too
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from bots.chento_v3 import config as botcfg  # noqa: E402

# Diagnostics env must be set BEFORE the sleeve module is imported — the
# sleeve reads CHENTO_V3_DIAG / CHENTO_V3_DIAG_PATH at import time.
os.environ.setdefault("CHENTO_V3_DIAG", "1")
os.environ.setdefault("CHENTO_V3_DIAG_PATH", botcfg.DIAG_PATH)

import botlib  # noqa: E402

log = logging.getLogger("bot.chento_v3")

_stop = threading.Event()

# Sleeve statuses that are steady-state noise at INFO level.
_IDLE_STATUSES = {"not_at_15m_boundary", "already_evaluated", "bar_not_ready",
                  "no_triple", "cooldown", "insufficient_data",
                  "open_position_wait"}

# Statuses that do NOT count as a completed boundary evaluation for the
# heartbeat's last_eval_utc (the monitor's silent-bot check keys off it).
_NOT_EVALUATED_STATUSES = {"not_at_15m_boundary", "bar_not_ready"}


def _signal_handler(signum, frame):
    log.info(f"signal {signum} received — stopping after current tick")
    _stop.set()


def _last_closed_was_loss(variant_id: str) -> bool:
    """True if this variant's most recently closed paper trade lost. Basis of
    the ETH leg's half-after-loss tilt policy (overlay study 2026-08-23) —
    the sleeve's FILTER_NO_TILT skip is disabled there in favour of this."""
    import sqlite3

    from strategies.support import db
    con = sqlite3.connect(f"file:{db.PROD_DB}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT entry_price, exit_price, direction FROM trades "
            "WHERE strategy_variant=? AND status='closed' "
            "ORDER BY exit_time DESC LIMIT 1", (variant_id,)).fetchone()
    finally:
        con.close()
    if not row or row[0] is None or row[1] is None:
        return False
    sign = 1 if str(row[2]).upper() == "LONG" else -1
    return (row[1] - row[0]) * sign < 0


def size_intent(intent, capital: float, risk_scale: float = 1.0):
    """Fixed-R sizing (botlib.size_intent_fixed_r) with this bot's params:
    RISK_PCT% (optionally tilt-scaled) over the sleeve's own 5×ATR stop,
    notional ≤ NOTIONAL_MAX_X × capital."""
    return botlib.size_intent_fixed_r(
        intent, capital, risk_pct=botcfg.RISK_PCT * risk_scale,
        notional_max_x=botcfg.NOTIONAL_MAX_X)


def tick(variant: dict, sleeve_cfg: dict) -> dict:
    """One bot tick. Returns a status dict for logging/heartbeat."""
    from strategies.sleeves import chento_triple_v3 as sleeve

    stale_mgmt = botlib.stale_tables(botcfg.MGMT_TABLES)
    if stale_mgmt:
        return {"status": "stale_mgmt_inputs", "stale": stale_mgmt,
                "hb_status": "degraded",
                "hb_note": f"mgmt tables stale: {sorted(stale_mgmt)}"}

    intents, status = sleeve.try_decide_for_variant(variant, sleeve_cfg)
    st = status.get("status", "?")
    out = {"status": st, "detail": status, "hb_status": "ok", "hb_note": "",
           # "evaluated" = a real 15m-boundary evaluation happened, so the
           # monitor's silent-bot check measures signal evals, not loop life
           "evaluated": st not in _NOT_EVALUATED_STATUSES}

    if intents:
        stale_entry = botlib.stale_tables(botcfg.ENTRY_TABLES)
        if stale_entry:
            # Sweep already ran (position management is safe); refusing the
            # ENTRY is the loud version of what stale data used to do
            # silently via NaN gates.
            log.warning(f"ENTRY BLOCKED — stale entry tables: {stale_entry}")
            out.update(status="entry_blocked_stale_inputs",
                       hb_status="degraded",
                       hb_note=f"entry tables stale: {sorted(stale_entry)}")
        else:
            risk_scale = 1.0
            if getattr(botcfg, "TILT_HALF_AFTER_LOSS", False) \
                    and _last_closed_was_loss(variant["id"]):
                risk_scale = 0.5
                log.info("tilt policy: half risk (last closed trade lost)")
            for intent in intents:
                resized, info = size_intent(intent, float(variant["capital_usdt"]),
                                            risk_scale)
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
    ap = argparse.ArgumentParser(description="Chento Triple v3 standalone bot")
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

    Path(botcfg.DIAG_PATH).parent.mkdir(parents=True, exist_ok=True)
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

    # The sleeve reads only these keys from sleeve_cfg; alloc/lev are
    # placeholders that size_intent() overwrites on every Intent.
    sleeve_cfg = {"weight_pct": 100.0, "_effective_leverage": 1.0,
                  "priority": 100}

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    log.info(f"loop starting (interval={args.interval}s, diag on)")
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
            msg = (f"tick {out['status']} ({(time.time() - t0) * 1000:.0f}ms)")
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


def run(cfg, argv: list[str] | None = None) -> int:
    """Entry point for per-asset wrapper bots (bots/chento_v3_eth): swap in
    their config module, then run the shared loop. The wrapper must set
    CHENTO_V3_ASSET (and diag env) BEFORE importing this module."""
    global botcfg
    botcfg = cfg
    return main(argv)


if __name__ == "__main__":
    sys.exit(main())

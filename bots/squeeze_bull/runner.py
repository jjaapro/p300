"""Standalone runner for SQUEEZE_BULL (S-107) — long BTC perp after a
forced-deleveraging flush, bull regime only.

Same skeleton as bots/short_squeeze: one process, one strategy, one variant
(`bot_squeeze_bull_v1`); the sleeve package is imported unchanged.

Bot-side responsibilities:
  - fixed-R sizing        1% risk over the sleeve's 2% stop (= 0.5x notional),
                          with a 3x notional cap that should never bind
  - stale-input refusal   stale btc_1m -> skip the tick entirely (the sweep
                          prices exits off the live 1m feed); stale
                          cd_open_interest / cd_futures_ohlcv -> the sweep
                          still runs, new entries are refused loudly
  - heartbeat, diag, scheduled-exit backstop

Why the stale policy matters here specifically: the entry reads a 30-day
return derived from cd_open_interest, a live-read table with roughly 30 days
of retention and a 3-hour freshness contract. Trading a stale positioning
feed would silently re-fire on an old flush.

Prerequisite: `python feed.py` running (this bot never fetches).

Usage:
  python bots/squeeze_bull/runner.py            # live loop, 60s ticks
  python bots/squeeze_bull/runner.py --once     # single tick and exit
  python bots/squeeze_bull/runner.py --once --verbose
"""
from __future__ import annotations

import argparse
import json
import logging
import signal as _signal
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from bots.squeeze_bull import config as botcfg  # noqa: E402

import botlib  # noqa: E402

log = logging.getLogger("bot.squeeze_bull")

_stop = threading.Event()

_IDLE_STATUSES = {"already_evaluated_this_hour", "position_open", "cooldown",
                  "no_flush", "regime_not_bull", "insufficient_bars", "no_bars"}


def _signal_handler(signum, frame):
    log.info(f"signal {signum} received — stopping after current tick")
    _stop.set()


def _diag(record: dict) -> None:
    """Append one JSON line per evaluated bar. Permanently on: this sleeve has
    never paper-traded and the gate counters are the only way to tell 'no
    flush happened' from 'the bot is broken'."""
    try:
        botcfg.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(botcfg.DIAG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception as e:  # noqa: BLE001 — diagnostics must never stop trading
        log.debug(f"diag write failed: {e!r}")


def size_intent(intent, capital: float):
    """Fixed-R sizing: 1% risk over the sleeve's 2% stop, notional <= 3x."""
    return botlib.size_intent_fixed_r(
        intent, capital, risk_pct=botcfg.RISK_PCT,
        notional_max_x=botcfg.NOTIONAL_MAX_X)


def tick(variant: dict, sleeve_cfg: dict) -> dict:
    from strategies.sleeves.squeeze_bull import signal as sleeve
    from strategies.support import clock

    stale_mgmt = botlib.stale_tables(botcfg.MGMT_TABLES)
    if stale_mgmt:
        return {"status": "stale_mgmt_inputs", "stale": sorted(stale_mgmt),
                "hb_status": "degraded",
                "hb_note": f"mgmt tables stale: {sorted(stale_mgmt)}"}

    intents, status = sleeve.try_decide_for_variant(variant, sleeve_cfg)
    st = status.get("status", "?")
    out = {"status": st, "detail": status, "hb_status": "ok", "hb_note": "",
           "evaluated": st != "already_evaluated_this_hour"}

    if out["evaluated"] and st not in ("no_bars", "insufficient_bars"):
        _diag({"utc": clock.now_utc().isoformat(), "status": st,
               **{k: status.get(k) for k in
                  ("bar_iso", "oi_chg_4h", "px_chg_4h",
                   "ret_30d_backonly", "regime", "swept")}})

    if intents:
        stale_entry = botlib.stale_tables(botcfg.ENTRY_TABLES)
        if stale_entry:
            log.warning(f"ENTRY BLOCKED — stale entry tables: {sorted(stale_entry)}")
            out.update(status="entry_blocked_stale_inputs", hb_status="degraded",
                       hb_note=f"entry tables stale: {sorted(stale_entry)}")
        else:
            for intent in intents:
                resized, info = size_intent(intent, float(variant["capital_usdt"]))
                res = sleeve.execute_for_variant(variant, sleeve_cfg, resized)
                log.info(f"OPENED {res.get('trade_id')} LONG "
                         f"notional=${info['notional']:,.0f} "
                         f"(stop_pct={info['stop_pct']:.2%}, at_cap={info['at_cap']})")
                out["opened"] = res.get("trade_id")
                out["signal"] = True

    closed = botlib.close_due_trades(variant["id"])
    if closed:
        out["backstop_closed"] = closed
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Squeeze Bull standalone bot")
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

    botcfg.LOGS_DIR.mkdir(parents=True, exist_ok=True)
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

    sleeve_cfg = {"weight_pct": 100.0, "_effective_leverage": 1.0,
                  "priority": 100}

    _signal.signal(_signal.SIGINT, _signal_handler)
    _signal.signal(_signal.SIGTERM, _signal_handler)

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
            msg = f"tick {out['status']} ({(time.time() - t0) * 1000:.0f}ms)"
            if out["status"] in _IDLE_STATUSES and not args.verbose:
                log.debug(msg)
            else:
                log.info(msg)
        except Exception as e:  # noqa: BLE001
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
        except Exception as e:  # noqa: BLE001
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
    raise SystemExit(main())

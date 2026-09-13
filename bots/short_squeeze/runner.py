"""Standalone runner for SHORT_SQUEEZE (S-105) — bot #2 of the extraction
plan, and the sleeve's FIRST-EVER paper deployment (it was dispatch-
registered but never composed into P-300).

Same skeleton as bots/chento_v3: one process, one strategy, and since
2026-09-12 TWO paper variants on the same signals (`bots/short_squeeze/config.py`
VARIANTS): the shipped `bot_short_squeeze_v1` with the swept-low stop and 3R
target, and `bot_short_squeeze_nostop_v1` with the 6h time stop only. The
sleeve package is imported unchanged; the exit policy is passed per variant as the
`use_stop` keyword. Bot-side additions:

  - fixed-R sizing        1% risk over the sleeve's swept-low stop, with a
                          3× notional cap that binds often by design; the
                          no-stop variant runs a fixed 1x of capital
  - stale-input refusal   stale btc_1m → skip tick (the sweep prices exits
                          off the live 1m feed); stale signal tables →
                          sweep runs, new entries refused loudly
  - scheduled-exit backstop, heartbeat, SSQ_DIAG permanently on (counted
    once per tick, by the first variant)

Prerequisite: `python feed.py` running (this bot never fetches).

Usage:
  python bots/short_squeeze/runner.py           # live loop, 60s ticks
  python bots/short_squeeze/runner.py --once    # single tick and exit
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from bots.short_squeeze import config as botcfg  # noqa: E402

# Diag env must land BEFORE the sleeve module import (read at import time).
os.environ.setdefault("SSQ_DIAG", "1")
os.environ.setdefault("SSQ_DIAG_PATH", botcfg.DIAG_PATH)

import botlib  # noqa: E402

log = logging.getLogger("bot.short_squeeze")

_stop = threading.Event()

_IDLE_STATUSES = {"not_15m_boundary", "position_open", "cooldown",
                  "no_distribution", "macro_not_ready", "macro_not_short",
                  "no_latest_bar", "out_of_session", "no_prior_low",
                  "no_sweep", "perp_pct_too_high", "divergence_pct_too_low",
                  "close_in_range_too_low"}


def _signal_handler(signum, frame):
    log.info(f"signal {signum} received — stopping after current tick")
    _stop.set()


def size_intent(intent, capital: float, *, use_stop: bool = True):
    """Fixed-R sizing (botlib.size_intent_fixed_r) with this bot's params:
    1% risk over the swept-low stop, notional ≤ 3× capital (binds often).

    The no-stop variant has no stop to size from and runs a fixed
    NOSTOP_NOTIONAL_X of capital; `stop_pct` in the info dict is then the
    reference swept-low distance the R accounting uses."""
    if use_stop:
        return botlib.size_intent_fixed_r(
            intent, capital, risk_pct=botcfg.RISK_PCT,
            notional_max_x=botcfg.NOTIONAL_MAX_X)
    reason = intent.reason or {}
    entry = float(reason["_entry_price"])
    ref_stop = float(reason["_reference_stop_price"])
    notional = capital * botcfg.NOSTOP_NOTIONAL_X
    resized = dataclasses.replace(intent, allocation_pct=100.0,
                                  leverage=notional / capital)
    return resized, {"stop_pct": abs(entry - ref_stop) / entry,
                     "notional": notional, "at_cap": False}


def tick(variant: dict, *, use_stop: bool = True,
         count_diag: bool = True) -> dict:
    """One tick for one variant. `use_stop` selects the exit policy and
    `count_diag` whether this call counts the per-day gate diagnostics (once
    per tick: both variants see the same bar)."""
    from bots.short_squeeze.strategy import signal as sleeve

    stale_mgmt = botlib.stale_tables(botcfg.MGMT_TABLES)
    if stale_mgmt:
        return {"status": "stale_mgmt_inputs", "stale": stale_mgmt,
                "hb_status": "degraded",
                "hb_note": f"mgmt tables stale: {sorted(stale_mgmt)}"}

    # Sizing is the runner's job (size_intent overwrites both), so the sleeve
    # gets the placeholders the cfg dict used to carry.
    intents, status = sleeve.decide(variant, weight_pct=100.0, leverage=1.0,
                                    use_stop=use_stop, count_diag=count_diag)
    st = status.get("status", "?")
    out = {"status": st, "detail": status, "hb_status": "ok", "hb_note": "",
           "evaluated": st != "not_15m_boundary"}

    if intents:
        stale_entry = botlib.stale_tables(botcfg.ENTRY_TABLES)
        if stale_entry:
            log.warning(f"ENTRY BLOCKED — stale entry tables: {stale_entry}")
            out.update(status="entry_blocked_stale_inputs",
                       hb_status="degraded",
                       hb_note=f"entry tables stale: {sorted(stale_entry)}")
        else:
            for intent in intents:
                resized, info = size_intent(intent, float(variant["capital_usdt"]),
                                            use_stop=use_stop)
                res = sleeve.execute(variant, resized)
                log.info(f"OPENED {res.get('trade_id')} {resized.direction} "
                         f"[{variant['id']}] notional=${info['notional']:,.0f} "
                         f"(stop_pct={info['stop_pct']:.2%}, "
                         f"{'stop+target' if use_stop else 'NO stop, time only'}, "
                         f"at_cap={info['at_cap']})")
                out["opened"] = res.get("trade_id")
                out["signal"] = True

    closed = botlib.close_due_trades(variant["id"])
    if closed:
        out["backstop_closed"] = closed
    return out


def tick_all(variants: list[dict]) -> dict:
    """One tick over every variant, in config order. `variants` items are
    {"row": <variants table row>, "use_stop": bool}. Returns what the
    heartbeat needs (worst status, joined notes, any evaluated / signalled,
    total open trades) plus the per-variant tick results; `status` is the
    first variant's, which drives the idle-vs-info log decision."""
    per: dict[str, dict] = {}
    for k, v in enumerate(variants):
        per[v["row"]["id"]] = tick(v["row"], use_stop=bool(v["use_stop"]),
                                   count_diag=(k == 0))
    outs = list(per.values())
    rank = {"ok": 0, "degraded": 1, "error": 2}
    hb_status = max((o.get("hb_status", "ok") for o in outs), key=lambda s: rank.get(s, 2))
    return {
        "status": outs[0]["status"] if outs else "no_variants",
        "per_variant": per,
        "hb_status": hb_status,
        "hb_note": "; ".join(o["hb_note"] for o in outs if o.get("hb_note")),
        "evaluated": any(o.get("evaluated") for o in outs),
        "signal": any(o.get("signal") for o in outs),
        "opened": [o["opened"] for o in outs if o.get("opened")],
        "open_trades": sum(botlib.count_open_trades(v["row"]["id"]) for v in variants),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Short Squeeze standalone bot")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=botcfg.TICK_SECONDS)
    ap.add_argument("--verbose", action="store_true",
                    help="Log idle tick statuses at INFO instead of DEBUG.")
    botlib.add_dry_run_flags(ap)
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

    from strategies.support.env import load_env_file
    load_env_file()
    # Before the sleeve is imported: it resolves SSQ_DIAG_PATH at import.
    botlib.apply_dry_run_flags(ap, args, botcfg)

    Path(botcfg.DIAG_PATH).parent.mkdir(parents=True, exist_ok=True)
    botlib.ensure_wal()
    botlib.init_heartbeat_schema()
    from strategies.support import trade_db
    trade_db.init_db()

    variants = []
    for v in botcfg.VARIANTS:
        row = botlib.ensure_bot_variant(
            v["id"], short_name=v["short_name"],
            capital_usdt=botcfg.CAPITAL_USDT, bot_name=botcfg.BOT_NAME)
        variants.append({"row": row, "use_stop": v["use_stop"]})
        sizing = (f"risk={botcfg.RISK_PCT}%/trade cap={botcfg.NOTIONAL_MAX_X}x"
                  if v["use_stop"] else
                  f"NO stop, notional={botcfg.NOSTOP_NOTIONAL_X:.2f}x")
        log.info(f"variant {row['id']} capital=${row['capital_usdt']:,.0f} "
                 f"{sizing} open_trades={botlib.count_open_trades(row['id'])}")

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    log.info(f"loop starting (interval={args.interval}s, diag on, "
             f"{len(variants)} variants)")
    from strategies.support import clock
    while not _stop.is_set():
        t0 = time.time()
        hb_status, hb_note, evaluated, signalled, open_n = "ok", "", False, False, None
        try:
            out = tick_all(variants)
            hb_status = out["hb_status"]
            hb_note = out["hb_note"]
            evaluated = bool(out["evaluated"])
            signalled = bool(out["signal"])
            open_n = out["open_trades"]
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
                open_trades=open_n)
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

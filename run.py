"""P-300 Aggressive 2.0 1.0 -- standalone paper-trading bot.

Runs the variant engine on a 60-second loop. Each tick dispatches all 7
P-300 sleeves for the SHADOW variant:

  - JPLUS-CORE (50%) -- Core J+ daily-return engine. Computes yesterday's
    return via jplus.simulate() and persists to variant_daily_returns
    (source='live_computed', idempotent per UTC day).
  - S-003 ADX (15%), S-078 Carry (8%), S-096 V4 Thu Bear (6%),
    PDO-L-RF (11%), CPR (5%), FOMC (5%) -- tactical sleeves. Open/close
    phantom trades in the `trades` table tagged execution_mode='SHADOW'
    and strategy_variant='p300_aggressive_v2_v1_0'.

No real orders are placed on any exchange.

Prerequisites (one-shot bootstrap):
  python bootstrap.py           # build data/trader.db from scratch
                                # (reads COINALYZE_API_KEY from .env)
  python register_p300.py       # register variant + seed daily returns

Daily operation:
  python run.py --feed          # single-process: bot + data feed, with
                                # automatic startup gap-fix so the DB
                                # self-heals every restart. Recommended
                                # for unattended paper trading.
  python run.py                 # bot only, no data feed (assumes you're
                                # running `python binance_feed.py` separately)
  python run.py --once          # one tick and exit (smoke test)
  python run.py --feed --skip-gap-fix
                                # skip the startup gap pass (fast restart)

Inspect state:
  python health.py              # all 8 invariants, exit code reflects state
  sqlite3 data/dashboard.db "SELECT id, asset, strategy, direction, status, \\
    pnl_pct FROM trades WHERE strategy_variant='p300_aggressive_v2_v1_0' \\
    ORDER BY id DESC LIMIT 20"
  sqlite3 data/dashboard.db "SELECT date, return_1x_pct, regime \\
    FROM variant_daily_returns WHERE variant_id='p300_aggressive_v2_v1_0' \\
    AND source='live_computed' ORDER BY date DESC LIMIT 10"
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from services import trade_db, variant_engine, variant_registry  # noqa: E402

log = logging.getLogger("p300")

VARIANT_ID = "p300_aggressive_v2_v1_0"

_stop = threading.Event()


def _signal_handler(signum, frame):
    log.info(f"signal {signum} received — stopping after current tick")
    _stop.set()


def _feed_thread(interval: int) -> None:
    """Background thread that refreshes Binance data each cycle."""
    import binance_feed  # local import so run.py works even if feed is absent
    log.info(f"binance_feed thread starting (interval={interval}s)")
    while not _stop.is_set():
        try:
            r = binance_feed.refresh_all()
            summary = ", ".join(f"{k}:{v}" for k, v in r.items())
            log.info(f"[feed] {summary}")
        except Exception as e:
            log.exception(f"[feed] tick failed: {e}")
        # Sleep in 1s increments so Ctrl-C returns promptly
        for _ in range(interval):
            if _stop.is_set():
                return
            time.sleep(1)


def _ensure_variant_registered() -> None:
    v = variant_registry.get_variant(VARIANT_ID)
    if v is None:
        log.error(
            f"Variant {VARIANT_ID} not registered. Run `python register_p300.py` first."
        )
        sys.exit(2)
    if not v["enabled"]:
        log.error(f"Variant {VARIANT_ID} is disabled — cannot tick.")
        sys.exit(2)
    if v["status"] != "SHADOW":
        log.warning(f"Variant {VARIANT_ID} status = {v['status']} (expected SHADOW)")
    log.info(f"Variant {VARIANT_ID} OK: {v['short_name']} ({v['status']})")


def _print_open_trades() -> None:
    """Show every open shadow trade across enabled variants at startup so
    the operator can see what positions the bot is inheriting (e.g. from a
    prior session). Reads dashboard.db directly — keeps run.py self-
    contained without a service-layer dependency."""
    import sqlite3
    from pathlib import Path
    db = Path(__file__).resolve().parent / "data" / "dashboard.db"
    if not db.exists():
        log.info("=== open shadow trades: dashboard.db missing ===")
        return
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT t.id, t.strategy_variant, t.strategy, t.asset, t.direction,
               t.entry_price, t.size_usdt, t.leverage,
               t.actual_entry_time, t.exit_time
        FROM trades t JOIN variants v ON t.strategy_variant = v.id
        WHERE t.execution_mode='SHADOW' AND t.status='open'
          AND v.enabled=1
        ORDER BY t.actual_entry_time
    """).fetchall()
    con.close()

    if not rows:
        log.info("=== open shadow trades: none ===")
        return
    log.info(f"=== open shadow trades: {len(rows)} ===")
    for r in rows:
        entry = (r["actual_entry_time"] or "")[:16].replace("T", " ")
        exit_due = (r["exit_time"] or "")[:16].replace("T", " ")
        size = float(r["size_usdt"] or 0)
        ep = float(r["entry_price"] or 0)
        lev = float(r["leverage"] or 1)
        log.info(f"  {r['id']:<8} {r['strategy']:<10} {r['asset']:<4} "
                 f"{r['direction']:<5} ${size:>7,.0f} @ ${ep:>9,.2f}  "
                 f"k={lev:.1f}x  entered={entry}  exit_due={exit_due}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P-300 headless paper-trading bot")
    ap.add_argument("--interval", type=int, default=60,
                    help="Tick interval seconds (default 60)")
    ap.add_argument("--feed", action="store_true",
                    help="Also run binance_feed in a background thread")
    ap.add_argument("--once", action="store_true",
                    help="Run one tick and exit (for testing)")
    ap.add_argument("--skip-gap-fix", action="store_true",
                    help="Skip the startup gap-detection pass when --feed is "
                         "set. Default with --feed is to heal any holes in "
                         "the kline + funding tables before the live loop.")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    # init schemas (idempotent)
    trade_db.init_db()
    variant_registry.init_schema()
    _ensure_variant_registered()
    _print_open_trades()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    if args.feed and not args.once:
        # Self-heal any data gaps before starting the live loop. First run
        # on a sparse DB can take ~20 min; subsequent runs are sub-second.
        if not args.skip_gap_fix:
            log.info("=== startup gap fix ===")
            import binance_feed
            res = binance_feed.fix_all_gaps()
            nonzero = {k: v for k, v in res.items() if v}
            if nonzero:
                summary = ", ".join(f"{k}:+{v:,}" for k, v in nonzero.items())
                log.info(f"gap fix complete -- {summary}")
            else:
                log.info("gap fix complete -- no gaps")
        t = threading.Thread(target=_feed_thread, args=(args.interval,),
                              daemon=True)
        t.start()

    log.info(f"scheduler loop starting (interval={args.interval}s, "
             f"feed={'on' if args.feed else 'off'})")
    while not _stop.is_set():
        t0 = time.time()
        now = datetime.now(timezone.utc)
        try:
            variant_engine.tick()
            log.info(f"tick ok ({(time.time() - t0) * 1000:.0f}ms @ {now.isoformat()})")
        except Exception as e:
            log.exception(f"tick error: {e}")
        if args.once:
            return 0
        # Sleep in 1s increments for prompt Ctrl-C
        for _ in range(args.interval):
            if _stop.is_set():
                break
            time.sleep(1)
    log.info("shutdown complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())

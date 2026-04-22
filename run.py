"""P-300 Aggressive 2.0 1.0 — standalone paper-trading bot.

Runs the variant engine on a 60-second loop. Each tick dispatches the 5 live
tactical sleeves (S-003 ADX, S-078 Carry, S-096 V4 Thu Bear, PDO-L-RF, CPR)
for the P-300 shadow variant. Shadow trades are written to data/dashboard.db
(execution_mode='SHADOW', strategy_variant='p300_aggressive_v2_v1_0'). No
orders are placed on any exchange.

The 50% core J+ MLgate anchor is seeded from backtest daily returns at
registration time (see register_p300.py) — it is NOT live-traded here.

Prerequisites (one-shot bootstrap):
  python seed_data.py           # copy tables from trader.db to data/trader.db
  python register_p300.py       # register variant + seed daily returns
  python binance_feed.py --once # fetch fresh bars (optional — seed is enough)

Daily operation:
  python run.py                 # foreground loop, Ctrl-C to stop
  python run.py --feed          # also run binance_feed in the same process

Inspect state:
  python -c "from services import variant_registry as r; \\
             print(r.get_variant('p300_aggressive_v2_v1_0')['status'])"
  sqlite3 data/dashboard.db "SELECT id, asset, strategy, direction, status, \\
    pnl_pct FROM trades WHERE strategy_variant='p300_aggressive_v2_v1_0' \\
    ORDER BY id DESC LIMIT 20"
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P-300 headless paper-trading bot")
    ap.add_argument("--interval", type=int, default=60,
                    help="Tick interval seconds (default 60)")
    ap.add_argument("--feed", action="store_true",
                    help="Also run binance_feed in a background thread")
    ap.add_argument("--once", action="store_true",
                    help="Run one tick and exit (for testing)")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    # init schemas (idempotent)
    trade_db.init_db()
    variant_registry.init_schema()
    _ensure_variant_registered()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    if args.feed and not args.once:
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

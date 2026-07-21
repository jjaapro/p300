"""Standalone market-data feed daemon.

Extracted from bot.py's in-process feed thread (2026-07 bot-extraction plan):
with multiple single-strategy bots running, exactly ONE process may own the
fetchers — this one. Bots are read-only consumers of prod.db market tables.

Covers every live-read table: Binance klines (1m/15m/1h spot+perp), funding,
LSR, open interest, okx_perp_1h (hourly throttle), plus the daily externals
(Fear&Greed, fed funds, Polymarket). Heals gaps at startup via
fix_all_gaps(). Writes a `feed` heartbeat row each cycle so monitor.py can
alert if this process dies (the 2026-06/07 outage ran 23 days unnoticed and
nearly cost the ~30d-retention LSR/OI history — never again).

Usage:
  python feed.py                # gap-fix, then refresh every 60s
  python feed.py --once         # single refresh cycle and exit
  python feed.py --skip-gap-fix # fast restart, no startup heal
  python feed.py --verbose      # per-cycle table counts

Do NOT run bot.py's legacy loop at the same time — it starts its own feed
thread and would double-fetch.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

import botlib  # noqa: E402

log = logging.getLogger("feed")

_stop = threading.Event()


def _signal_handler(signum, frame):
    log.info(f"signal {signum} received — stopping after current cycle")
    _stop.set()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P-300 market-data feed daemon")
    ap.add_argument("--interval", type=int, default=60,
                    help="Refresh cycle seconds (default 60).")
    ap.add_argument("--once", action="store_true",
                    help="One refresh cycle and exit.")
    ap.add_argument("--skip-gap-fix", action="store_true",
                    help="Skip the startup gap-heal pass.")
    ap.add_argument("--verbose", action="store_true",
                    help="Log per-table fetch counts every cycle.")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

    from strategies.support.env import load_env_file
    load_env_file()

    botlib.ensure_wal()
    botlib.init_heartbeat_schema()

    from data.sources import binance

    if not args.skip_gap_fix and not args.once:
        log.info("=== startup gap fix ===")
        res = binance.fix_all_gaps()
        nonzero = {k: v for k, v in res.items() if v}
        log.info("gap fix complete -- " + (
            ", ".join(f"{k}:+{v:,}" for k, v in nonzero.items())
            if nonzero else "no gaps"))

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    log.info(f"feed loop starting (interval={args.interval}s)")
    while not _stop.is_set():
        t0 = time.time()
        status, note = "ok", ""
        try:
            r = binance.refresh_all()
            failed = sorted(k for k, v in r.items() if v == -1)
            if failed:
                status, note = "degraded", f"failed: {','.join(failed)}"
                log.warning(f"cycle degraded — {note}")
            if args.verbose:
                log.info("[feed] " + ", ".join(f"{k}:{v}" for k, v in r.items()))
        except Exception as e:
            status, note = "error", repr(e)
            log.exception(f"cycle failed: {e}")
        try:
            botlib.heartbeat("feed", status=status, note=note,
                             interval_s=args.interval)
        except Exception as e:
            log.warning(f"heartbeat write failed: {e!r}")
        if args.once:
            log.info(f"single cycle done ({time.time() - t0:.1f}s)")
            return 0
        for _ in range(args.interval):
            if _stop.is_set():
                break
            time.sleep(1)
    log.info("shutdown complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())

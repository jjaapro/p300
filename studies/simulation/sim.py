"""Deterministic-clock simulator for the orchestrator.

Replaces the former ``run.py --mode sim`` path. The bot's trading logic
is unchanged — only the data source (``strategies.support.db.TRADER_DB``
and ``DASH_DB`` are redirected at startup) and the clock differ. The
clock-advance primitive lives in ``strategies.support.sim_loop`` and is
shared with ``backtest_runner.py`` so both paths walk the same calendar
through the same per-tick callback.

Build a sliced sim trader.db with
``studies.simulation.build_sim_trader_db`` before running. The P-300
variant is auto-registered in the sim dashboard.db on startup via
``strategies.p300_spec.register`` — no separate registration step.

Usage:
  python studies/simulation/sim.py \\
      --start 2024-01-01 --end 2024-12-31 \\
      --trader-db data/trader_sim.db \\
      --dash-db /tmp/sim_dash.db

  python studies/simulation/sim.py \\
      --start 2024-06-03T05:59 --end 2024-06-03T18:00 \\
      --trader-db data/trader_sim.db \\
      --dash-db /tmp/sim_dash.db \\
      --sim-tick-seconds 60   # default; lower = finer granularity
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

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from strategies import orchestrator
from strategies.support import sim_loop, trade_db, variant_registry  # noqa: E402

log = logging.getLogger("p300.sim")

VARIANT_ID = "p300_aggressive_v2_v1_0"

_stop = threading.Event()


def _signal_handler(signum, frame):
    log.info(f"signal {signum} received — stopping after current tick")
    _stop.set()


def _parse_iso_utc(s: str) -> datetime:
    """Parse an ISO-8601 datetime, defaulting to UTC if naive. Accepts
    'YYYY-MM-DD' (treated as 00:00 UTC) and full ISO timestamps."""
    s = s.strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        s = s + "T00:00:00+00:00"
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ensure_variant_registered() -> None:
    """Auto-register the P-300 variant in the sim dashboard.db. Idempotent."""
    from strategies import p300_spec
    if p300_spec.register(quiet=True):
        log.info(f"Variant {VARIANT_ID} auto-registered in sim dashboard.db.")
    v = variant_registry.get_variant(VARIANT_ID)
    if v is None:
        log.error(f"Variant {VARIANT_ID} registration failed in sim DB.")
        sys.exit(2)
    if not v["enabled"]:
        log.error(f"Variant {VARIANT_ID} is disabled in sim DB — cannot tick.")
        sys.exit(2)
    log.info(f"Variant {VARIANT_ID} OK: {v['short_name']} ({v['status']})")


def _print_health_report() -> None:
    """One-shot strategy-health snapshot. Same code path as the live bot's
    startup banner; wrapped in try/except so a metric-side bug can't mask
    sim results."""
    try:
        from strategies.support.strategy_health import build_report, format_report
        report = build_report(VARIANT_ID)
        for line in format_report(report).splitlines():
            log.info(line)
    except Exception as e:
        log.warning(f"health report skipped: {e!r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Deterministic sim of the P-300 orchestrator against "
                    "isolated DBs.",
    )
    ap.add_argument("--start", required=True, type=str,
                    help="Sim start (UTC). 'YYYY-MM-DD' or full ISO-8601.")
    ap.add_argument("--end", required=True, type=str,
                    help="Sim end (UTC, inclusive).")
    ap.add_argument("--trader-db", required=True, type=str,
                    help="Path to the sim trader.db (market data source). "
                         "Build with studies/simulation/build_sim_trader_db.py.")
    ap.add_argument("--dash-db", required=True, type=str,
                    help="Path to the sim dashboard.db (variant + trade ledger "
                         "destination). Auto-registered on startup if absent.")
    ap.add_argument("--sim-tick-seconds", type=int, default=60,
                    help="Simulated-clock advance per tick in seconds "
                         "(default 60). Lower = finer granularity.")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    # DB redirect (must run BEFORE init_db / variant lookup). Module-attribute
    # mutation of strategies.support.db propagates everywhere because every
    # consumer reads db.DASH_DB / db.TRADER_DB at call time, not import time.
    from strategies.support import db as _db_mod
    trader_path = Path(args.trader_db).resolve()
    dash_path = Path(args.dash_db).resolve()
    # Isolation guard: sim must NEVER read or write the live consolidated DB.
    # The original design (per BACKLOG / sim docstring) routes sim against
    # an isolated sliced DB built with build_sim_trader_db.py and an
    # ephemeral sim dashboard.db. Passing prod.db here would silently mix
    # sim trade rows into the live ledger and is almost certainly a typo.
    prod_path = _db_mod.PROD_DB.resolve()
    for label, path in (("--trader-db", trader_path), ("--dash-db", dash_path)):
        if path == prod_path:
            log.error(
                f"{label}={path} points at the live prod.db. Sim must run "
                f"against isolated DBs only — build a sliced trader.db via "
                f"studies/simulation/build_sim_trader_db.py and use a "
                f"throwaway dash-db path (e.g. /tmp/sim_dash.db). Refusing "
                f"to proceed."
            )
            return 2
    _db_mod.TRADER_DB = trader_path
    _db_mod.DASH_DB = dash_path
    log.info(
        f"=== sim mode === redirected strategies.support.db.TRADER_DB="
        f"{_db_mod.TRADER_DB} strategies.support.db.DASH_DB={_db_mod.DASH_DB}"
    )

    from strategies.support import clock as _clock
    start = _parse_iso_utc(args.start)
    end = _parse_iso_utc(args.end)
    _clock.set_simulated_now(start)

    # Load .env so ANTHROPIC_API_KEY, COINALYZE_API_KEY etc. are available
    # to sleeves the same way as in live mode.
    from strategies.support.env import load_env_file
    load_env_file()

    trade_db.init_db()
    variant_registry.init_schema()
    _ensure_variant_registered()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    log.info(
        f"=== sim loop starting === start={start.isoformat()} "
        f"end={end.isoformat()} step={args.sim_tick_seconds}s"
    )

    def _tick(cur):
        try:
            orchestrator.tick()
        except Exception as e:
            log.exception(f"sim tick error at {cur.isoformat()}: {e}")

    t_wall = time.time()
    n_ticks = sim_loop.run_sim(start, end, args.sim_tick_seconds, _tick,
                                stop_event=_stop)
    elapsed = time.time() - t_wall
    log.info(
        f"=== sim loop complete === ticks={n_ticks} "
        f"wall_time={elapsed:.1f}s ({n_ticks / max(elapsed, 1e-9):.0f} ticks/s)"
    )
    log.info("=== sim post-run health report ===")
    _print_health_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())

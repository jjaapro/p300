"""P-300 Aggressive 2.0 — standalone paper-trading bot.

Runs the orchestrator on a 60-second loop. Each tick dispatches the
variant's top-level sleeves; sleeves emit phantom trades into the
``trades`` table tagged ``execution_mode='paper'`` and
``strategy_variant='p300_aggressive_v2_v1_0'``. Realized PnL is the
sum of the trade ledger; there is no parallel theoretical-PnL track.

Top-level sleeves dispatched on each tick (orchestrator.STRATEGY_DISPATCH):
  S-003 ADX, S-078 Carry, JPLUS_EMA_BTC, JPLUS_ETH_DAILY,
  SHORT_SQUEEZE, AI_QUANT, TIMING_ANOMALIES.

TIMING_ANOMALIES is a meta-sleeve that fans out to 8 calendar/clock
substrategies (FOMC, THU_BEAR, PDO_L_RF, CPR, R4_BTC, R4_ETH,
R4_BTC_V2, R4_ETH_V2) whose code lives under
``strategies/sleeves/timing_anomalies/internal/``. Trades these
emit carry their substrategy's own ``strategy`` tag (e.g. ``FOMC``,
``THU_BEAR``) so attribution stays at the substrategy level.

AI_QUANT is default-OFF via the ``AI_QUANT_ENABLED`` env var.

No real orders are placed on any exchange.

The data feed (``data.sources.binance``) always runs in a background
thread so the bot is a single-process unit. Console noise from idle
sleeves (``no_signal`` / ``not_thursday`` / ``tick ok`` / ``[feed]`` /
etc.) is filtered out by default; pass ``--verbose`` to see everything.

Prerequisites (one-shot bootstrap):
  python bootstrap.py           # build data/prod.db from scratch
                                # (reads COINALYZE_API_KEY from .env)

The P-300 variant is auto-registered on first bot startup via
``strategies.p300_spec.register`` — no separate registration step.

Daily operation:
  python bot.py                 # bot + binance_feed, with startup gap-fix
  python bot.py --once          # one tick and exit (smoke test)
  python bot.py --skip-gap-fix  # skip the startup gap pass (fast restart)
  python bot.py --verbose       # show every log line (no noise filter)

Sim mode (research-side, separate entry point):
  python studies/simulation/sim.py \\
      --start 2024-01-01 --end 2024-12-31 \\
      --trader-db data/trader_sim.db \\
      --dash-db /tmp/sim_dash.db

Inspect state:
  python health.py              # all 8 invariants, exit code reflects state
  sqlite3 data/dashboard.db "SELECT id, asset, strategy, direction, status, \\
    pnl_pct FROM trades WHERE strategy_variant='p300_aggressive_v2_v1_0' \\
    ORDER BY id DESC LIMIT 20"
"""
from __future__ import annotations

import argparse
import logging
import re
import signal
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from strategies import orchestrator, p300_spec
from strategies.support import trade_db, variant_registry  # noqa: E402

log = logging.getLogger("p300")

VARIANT_ID = "p300_aggressive_v2_v1_0"

_stop = threading.Event()


# ─── Console noise filter ─────────────────────────────────────────────────────

# Patterns suppressed by default. Each is a regex matched against the formatted
# log message. Goal: hide idle/heartbeat lines that fire every tick in steady
# state so the operator sees only events that actually changed state. Pass
# ``--verbose`` to disable filtering (e.g. when debugging a sleeve).
_NOISE_PATTERNS: tuple[str, ...] = (
    r"already_recorded",         # JPLUS-CORE idempotent re-checks (1439/day)
    r"no_signal",                # tactical sleeve: no entry condition met
    r"not_thursday",             # S-096 V4: only fires on Thursdays
    r"'no_gap'",                 # PDO: no gap-up condition today
    r"'open_waiting'",           # sleeve has an open trade, waiting for exit
    r"\[feed\]",                 # binance_feed thread per-tick refresh summary
    r"tick ok",                  # orchestrator 60s heartbeat
    r"S-096.*'status': 'ok', 'actions': \[\]",  # S-096 idle (no entry/exit)
    r"no_upcoming_fomc",         # FOMC dispatcher: no FOMC within 2-day window
    r"not_tuesday",
    r"no_position_needed",
    r"in_sync",
    r"awaiting_fresh_cross",
    r"not_calendar_day",
    r"new rows",
    r"new headlines",
    r"off_window",
    r"before_open_window",
    r"already_open",
    r"after_close_window",
    r"no_action",
    r"not_wk_1_2",
    r"'status': 'not_at_15m_boundary'",
)


class _NoiseFilter(logging.Filter):
    """Drop log records whose message matches any pattern in _NOISE_PATTERNS."""

    def __init__(self) -> None:
        super().__init__()
        self._regex = re.compile("|".join(_NOISE_PATTERNS))

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        # Drop only INFO; always pass WARNING / ERROR through.
        if record.levelno >= logging.WARNING:
            return True
        return self._regex.search(record.getMessage()) is None


# ─── Lifecycle ────────────────────────────────────────────────────────────────

def _signal_handler(signum, frame):
    log.info(f"signal {signum} received — stopping after current tick")
    _stop.set()


def _feed_thread(interval: int) -> None:
    """Background thread that refreshes Binance data each cycle."""
    from data.sources import binance as binance_feed
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
    """Auto-register the P-300 variant on startup. Idempotent — no-op if
    the row already exists. Replaces the pre-2026-05-16 workflow that
    required `python register_p300.py` as a separate step."""
    if p300_spec.register(quiet=True):
        log.info(f"Variant {VARIANT_ID} auto-registered.")
    v = variant_registry.get_variant(VARIANT_ID)
    if v is None:
        log.error(f"Variant {VARIANT_ID} registration failed.")
        sys.exit(2)
    if not v["enabled"]:
        log.error(f"Variant {VARIANT_ID} is disabled — cannot tick.")
        sys.exit(2)
    if v["status"] != "paper":
        log.warning(f"Variant {VARIANT_ID} status = {v['status']} (expected paper)")
    log.info(f"Variant {VARIANT_ID} OK: {v['short_name']} ({v['status']})")


def _print_open_trades() -> None:
    """Show every open paper trade across enabled variants at startup so
    the operator can see what positions the bot is inheriting (e.g. from a
    prior session). Reads via ``strategies.support.db.DASH_DB`` so a sim-mode
    redirection of that constant is honoured here too."""
    import sqlite3
    from strategies.support import db as _db_mod
    db_path = _db_mod.DASH_DB
    if not db_path.exists():
        log.info("=== open paper trades: dashboard.db missing ===")
        return
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT t.id, t.strategy_variant, t.strategy, t.asset, t.direction,
               t.entry_price, t.size_usdt, t.leverage,
               t.actual_entry_time, t.exit_time
        FROM trades t JOIN variants v ON t.strategy_variant = v.id
        WHERE t.execution_mode='paper' AND t.status='open'
          AND v.enabled=1
        ORDER BY t.actual_entry_time
    """).fetchall()
    con.close()

    if not rows:
        log.info("=== open paper trades: none ===")
        return
    log.info(f"=== open paper trades: {len(rows)} ===")
    for r in rows:
        entry = (r["actual_entry_time"] or "")[:16].replace("T", " ")
        exit_due = (r["exit_time"] or "")[:16].replace("T", " ")
        size = float(r["size_usdt"] or 0)
        ep = float(r["entry_price"] or 0)
        lev = float(r["leverage"] or 1)
        log.info(f"  {r['id']:<8} {r['strategy']:<10} {r['asset']:<4} "
                 f"{r['direction']:<5} ${size:>7,.0f} @ ${ep:>9,.2f}  "
                 f"k={lev:.1f}x  entered={entry}  exit_due={exit_due}")


def _print_health_report() -> None:
    """Print a one-shot strategy-health snapshot for the live variant on
    startup. Pulls portfolio metrics (Sharpe / WR / MDD / total return)
    across YTD/90D/30D plus per-sleeve breakdown (N / WR / total $ /
    expectancy / profit factor / avg hold / Sharpe / MDD).

    Wrapped in try/except so any metric-side bug can't block the bot's
    main loop from coming up. The report prints once at startup only."""
    try:
        from strategies.support.strategy_health import build_report, format_report
        report = build_report(VARIANT_ID)
        for line in format_report(report).splitlines():
            log.info(line)
    except Exception as e:
        log.warning(f"health report skipped: {e!r}")


def _print_today_inputs_snapshot() -> None:
    """One-shot today_inputs() snapshot — regime, vol-target leverage,
    R4 gate state, EMA-position, and the per-cell weights computed for
    today. Gives the operator a sanity check on startup ("what regime
    are we in, are R4 substrategies going to fire today, what's the
    vol-target leverage").

    Cell weights are split across two emission paths:
      - r4_btc / r4_eth / r4_btc_v2 / r4_eth_v2 are TIMING_ANOMALIES
        substrategies, fired through the meta-sleeve dispatcher.
      - ema_btc and eth_daily are top-level continuous sleeves.
    """
    try:
        from strategies.support import jplus_inputs as core_sim
        ti = core_sim.today_inputs()
        if ti is None:
            log.warning("today_inputs snapshot: simulator returned None "
                        "(insufficient warmup data?)")
            return
        weights = ti.get("weights", {}) or {}
        log.info(
            f"=== today_inputs ({ti.get('date', '?')}) === "
            f"mode={ti.get('mode')}  vol_lev={float(ti.get('lev', 0)):.2f}x  "
            f"gate={'FIRED' if ti.get('gated') else 'open'}  "
            f"ema_p={int(ti.get('ema_p', 0)):+d}"
        )
        log.info(
            "  TIMING_ANOMALIES R4 weights: "
            f"r4_btc={weights.get('r4_btc', 0):.3f}  "
            f"r4_eth={weights.get('r4_eth', 0):.3f}  "
            f"r4_btc_v2={weights.get('r4_btc_v2', 0):.3f}  "
            f"r4_eth_v2={weights.get('r4_eth_v2', 0):.3f}"
        )
        log.info(
            "  continuous sleeve weights: "
            f"ema_btc={weights.get('ema_btc', 0):.3f}  "
            f"eth_daily={weights.get('eth_daily', 0):.3f}"
        )
    except Exception as e:
        log.warning(f"today_inputs snapshot skipped: {e!r}")


def _run_live_loop(args, log) -> int:
    """Wall-clock-driven live loop. Runs until _stop is set or, with
    --once, after a single tick."""
    from strategies.support import clock as _clock
    if not args.once:
        if not args.skip_gap_fix:
            log.info("=== startup gap fix ===")
            from data.sources import binance as binance_feed
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

    log.info(f"scheduler loop starting (interval={args.interval}s)")
    while not _stop.is_set():
        t0 = time.time()
        now = _clock.now_utc()
        try:
            orchestrator.tick()
            log.info(f"tick ok ({(time.time() - t0) * 1000:.0f}ms @ {now.isoformat()})")
        except Exception as e:
            log.exception(f"tick error: {e}")
        if args.once:
            return 0
        for _ in range(args.interval):
            if _stop.is_set():
                break
            time.sleep(1)
    log.info("shutdown complete")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="P-300 headless paper-trading bot",
        epilog=(
            "For research replays use studies/simulation/sim.py (deterministic "
            "clock, isolated DBs) or backtest_runner.py (variant replay)."
        ),
    )
    ap.add_argument("--interval", type=int, default=60,
                    help="Live tick interval seconds (default 60).")
    ap.add_argument("--once", action="store_true",
                    help="Run one tick and exit (smoke test). Skips the data "
                         "feed thread and gap-fix.")
    ap.add_argument("--skip-gap-fix", action="store_true",
                    help="Skip the startup gap-detection pass. Default behavior "
                         "is to heal any holes in the kline + funding tables "
                         "before the live loop starts.")
    ap.add_argument("--verbose", action="store_true",
                    help="Show every log line. Default filters idle/heartbeat "
                         "noise (no_signal / tick ok / [feed] / etc.) so the "
                         "console only shows state changes.")
    args = ap.parse_args(argv)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s"
    ))
    if not args.verbose:
        handler.addFilter(_NoiseFilter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    # Load .env so ANTHROPIC_API_KEY, COINALYZE_API_KEY etc. are available
    # to sleeves and fetchers without requiring a shell export. Existing env
    # values are preserved (an explicit `export` still wins).
    from strategies.support.env import load_env_file
    load_env_file()

    # init schemas (idempotent)
    trade_db.init_db()
    variant_registry.init_schema()
    _ensure_variant_registered()
    _print_open_trades()
    _print_today_inputs_snapshot()
    _print_health_report()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    return _run_live_loop(args, log)


if __name__ == "__main__":
    sys.exit(main())

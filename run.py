"""P-300 Aggressive 2.0 1.0 -- standalone paper-trading bot.

Runs the variant engine on a 60-second loop. Each tick dispatches all
sleeves of the SHADOW variant; sleeves emit phantom trades into the
``trades`` table tagged ``execution_mode='SHADOW'`` and
``strategy_variant='p300_aggressive_v2_v1_0'``. Realized PnL is the
sum of the trade ledger; there is no parallel theoretical-PnL track.

Tactical sleeves (50%):
  S-003 ADX, S-078 Carry, S-096 V4 Thu Bear, PDO-L-RF, CPR, FOMC.

Core J+ sub-sleeves (50%, sized per-tick from
``strategies.support.jplus_inputs.today_inputs()``):
  JPLUS_R4_BTC, JPLUS_R4_ETH, JPLUS_R4_BTC_V2, JPLUS_R4_ETH_V2,
  JPLUS_EMA_BTC, JPLUS_ETH_DAILY.

AI_QUANT (additive 2%, default-OFF via ``AI_QUANT_ENABLED`` env var).

No real orders are placed on any exchange.

Two operating modes:

  python run.py                       # LIVE (default): wall-clock loop
                                      # against data/trader.db + data/dashboard.db
  python run.py --mode sim \\
      --start 2024-01-01 --end 2024-12-31 \\
      --trader-db data/trader_sim.db \\
      --dash-db /tmp/sim_dash.db      # SIM: deterministic loop, same
                                      # dispatch logic, separate DBs.
                                      # Build trader-sim with
                                      # studies/simulation/build_sim_trader_db.py.

Prerequisites (one-shot bootstrap):
  python bootstrap.py           # build data/trader.db from scratch
                                # (reads COINALYZE_API_KEY from .env)
  python register_p300.py       # register variant in data/dashboard.db

Daily operation:
  python run.py --feed          # single-process: bot + data feed, with
                                # automatic startup gap-fix so the DB
                                # self-heals every restart. Recommended
                                # for unattended paper trading.
  python run.py                 # bot only, no data feed (assumes you're
                                # running `python binance_feed.py` separately)
  python run.py --once          # one tick and exit (smoke test, live only)
  python run.py --feed --skip-gap-fix
                                # skip the startup gap pass (fast restart)

Inspect state:
  python health.py              # all 8 invariants, exit code reflects state
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from strategies import orchestrator
from strategies.support import trade_db, variant_registry  # noqa: E402

log = logging.getLogger("p300")

VARIANT_ID = "p300_aggressive_v2_v1_0"

_stop = threading.Event()


def _signal_handler(signum, frame):
    log.info(f"signal {signum} received — stopping after current tick")
    _stop.set()


def _feed_thread(interval: int) -> None:
    """Background thread that refreshes Binance data each cycle."""
    from data.sources import binance as binance_feed  # local import so run.py works even if feed is absent
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
    prior session). Reads via ``strategies.support.db.DASH_DB`` so a sim-mode
    redirection of that constant is honoured here too."""
    import sqlite3
    from strategies.support import db as _db_mod
    db_path = _db_mod.DASH_DB
    if not db_path.exists():
        log.info("=== open shadow trades: dashboard.db missing ===")
        return
    con = sqlite3.connect(str(db_path))
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
    """One-shot Core J+ today_inputs() snapshot — regime, vol-target
    leverage, R4 gate state, EMA-position, and the six sub-sleeve
    weights. Replaces the per-day attribution log line that the
    deleted jplus_service used to emit; gives the operator the same
    sanity check on startup ("what regime are we in, are R4 sleeves
    going to fire today, what's the vol-target leverage")."""
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
            "  sub-sleeve weights: "
            f"r4_btc={weights.get('r4_btc', 0):.3f}  "
            f"r4_eth={weights.get('r4_eth', 0):.3f}  "
            f"r4_btc_v2={weights.get('r4_btc_v2', 0):.3f}  "
            f"r4_eth_v2={weights.get('r4_eth_v2', 0):.3f}  "
            f"ema_btc={weights.get('ema_btc', 0):.3f}  "
            f"eth_daily={weights.get('eth_daily', 0):.3f}"
        )
    except Exception as e:
        log.warning(f"today_inputs snapshot skipped: {e!r}")


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


def _run_live_loop(args, log) -> int:
    """Wall-clock-driven live loop. Runs until _stop is set or, with
    --once, after a single tick."""
    from strategies.support import clock as _clock
    if args.feed and not args.once:
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

    log.info(f"scheduler loop starting (interval={args.interval}s, "
             f"feed={'on' if args.feed else 'off'})")
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


def _run_sim_loop(args, log) -> int:
    """Deterministic sim loop: advance the simulated clock by
    --sim-tick-seconds per tick from --start to --end (inclusive). No
    wall-clock sleep; runs as fast as the dispatch can. The bot's
    trading logic is identical to live mode; only the data source
    (strategies.support.db.{TRADER,DASH}_DB redirected at startup) and clock
    differ. The loop primitive lives in strategies.support.sim_loop so
    backtest_runner.py can reuse the same clock-advance logic with
    its own per-tick callback."""
    from strategies.support import sim_loop
    start = _parse_iso_utc(args.start)
    end = _parse_iso_utc(args.end)
    log.info(
        f"=== sim loop starting === start={start.isoformat()} "
        f"end={end.isoformat()} step={args.sim_tick_seconds}s "
        f"trader_db={args.trader_db} dash_db={args.dash_db}"
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P-300 headless paper-trading bot")
    ap.add_argument("--mode", choices=("live", "sim"), default="live",
                    help="Operating mode. 'live' (default): wall-clock loop "
                         "against the real DBs. 'sim': deterministic loop "
                         "against the DBs given by --trader-db/--dash-db, "
                         "advancing a simulated clock from --start to --end.")
    ap.add_argument("--interval", type=int, default=60,
                    help="Live tick interval seconds (default 60). Ignored "
                         "in --mode sim (use --sim-tick-seconds).")
    ap.add_argument("--feed", action="store_true",
                    help="Also run binance_feed in a background thread. "
                         "Incompatible with --mode sim.")
    ap.add_argument("--once", action="store_true",
                    help="Run one tick and exit (for live-mode smoke test).")
    ap.add_argument("--skip-gap-fix", action="store_true",
                    help="Skip the startup gap-detection pass when --feed is "
                         "set. Default with --feed is to heal any holes in "
                         "the kline + funding tables before the live loop.")
    # ── sim-mode args ─────────────────────────────────────────────────
    ap.add_argument("--start", type=str, default=None,
                    help="Sim start (UTC). Required with --mode sim. "
                         "Accepts 'YYYY-MM-DD' or full ISO-8601.")
    ap.add_argument("--end", type=str, default=None,
                    help="Sim end (UTC, inclusive). Required with --mode sim.")
    ap.add_argument("--trader-db", type=str, default=None,
                    help="Path to the sim trader.db (market data source). "
                         "Required with --mode sim. Build with "
                         "studies/simulation/build_sim_trader_db.py.")
    ap.add_argument("--dash-db", type=str, default=None,
                    help="Path to the sim dashboard.db (variant + trade ledger "
                         "destination). Required with --mode sim. Pre-register "
                         "the variant via "
                         "`python register_p300.py --dash-db <path>`.")
    ap.add_argument("--sim-tick-seconds", type=int, default=60,
                    help="Simulated-clock advance per tick in seconds "
                         "(default 60). Lower = finer granularity.")
    args = ap.parse_args(argv)

    # ── Validate mode-specific arg combos ─────────────────────────────
    if args.mode == "sim":
        missing = [
            f for f, v in (
                ("--start", args.start), ("--end", args.end),
                ("--trader-db", args.trader_db), ("--dash-db", args.dash_db),
            ) if not v
        ]
        if missing:
            ap.error(f"--mode sim requires: {', '.join(missing)}")
        if args.feed:
            ap.error("--feed is incompatible with --mode sim "
                     "(sim runs against pre-populated DBs, no live API)")
        if args.once:
            ap.error("--once is a live-mode smoke test; use --start/--end "
                     "with --mode sim")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    # ── Sim-mode DB redirect (must run BEFORE init_db / variant lookup) ──
    # Module-attribute mutation of strategies.support.db propagates everywhere
    # because every consumer reads ``db.DASH_DB`` / ``db.TRADER_DB`` at
    # call time, not import time. Confirmed by audit: no
    # ``from strategies.support.db import DASH_DB`` patterns exist.
    if args.mode == "sim":
        from strategies.support import db as _db_mod
        _db_mod.TRADER_DB = Path(args.trader_db).resolve()
        _db_mod.DASH_DB = Path(args.dash_db).resolve()
        log.info(
            f"=== sim mode === redirected strategies.support.db.TRADER_DB="
            f"{_db_mod.TRADER_DB} strategies.support.db.DASH_DB={_db_mod.DASH_DB}"
        )
        from strategies.support import clock as _clock
        _clock.set_simulated_now(_parse_iso_utc(args.start))

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
    if args.mode == "live":
        _print_health_report()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    if args.mode == "sim":
        return _run_sim_loop(args, log)
    return _run_live_loop(args, log)


if __name__ == "__main__":
    sys.exit(main())

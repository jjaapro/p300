"""Backtest runner for the P-300 tactical sleeves (5 live sleeves, 45% of
intended capital — Core J+ MLgate NOT included; see register_p300.py).

Replays each sleeve's LIVE dispatch code against historical market data from
trader.db, using services/clock.py to present a simulated "now" to each
module so DB queries never see future bars. No signal logic is reimplemented;
the strategy code under test is literally the same code that runs live.

Usage:
  python backtest_runner.py --start 2023-01-01 --end 2026-04-15
  python backtest_runner.py --start 2021-07-01 --end 2026-04-15 --interval-hours 1
  python backtest_runner.py --reset       # purge prior replay data first

Output:
  - Closed trades in dashboard.db tagged strategy_variant='p300..._replay'
    (NEVER contaminates the live variant's data)
  - Daily NAV series in variant_daily_returns with source='replay'
  - Console report: total / annualized / Sharpe / MDD / trade count / per-sleeve PnL
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sqlite3
import sys
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from services import clock, trade_db, variant_engine, variant_registry  # noqa: E402
from services.price_feed import _get_current_price  # noqa: E402
from services import db

log = logging.getLogger("p300.backtest")

LIVE_VARIANT_ID = "p300_aggressive_v2_v1_0"
REPLAY_VARIANT_ID_PREFIX = "p300_aggressive_v2_v1_0__replay"

# Set by main() based on --with-fomc; ensure_replay_variant() reads it.
# Single-element list so closures can mutate without `nonlocal` gymnastics.
WITH_FOMC: list[bool] = [False]

# Strategy ids to skip during replay (-x flag). Use to drop slow sleeves
# that don't affect a comparison run (e.g. JPLUS-CORE, which contributes
# to variant_daily_returns rather than trades-based NAV).
SKIP_STRATEGIES: set[str] = set()


def replay_variant_id(tag: str | None) -> str:
    """Build the replay variant id, optionally suffixed by --tag so multiple
    runs (e.g. A vs B) can coexist in the DB side-by-side for comparison."""
    if tag:
        return f"{REPLAY_VARIANT_ID_PREFIX}_{tag}"
    return REPLAY_VARIANT_ID_PREFIX


# ─── Replay variant setup ─────────────────────────────────────────────────────

def ensure_replay_variant(variant_id: str, reset: bool = False) -> dict:
    """Clone live variant's spec under a replay id. Purge prior replay data
    when reset=True. Returns the replay variant dict."""
    live = variant_registry.get_variant(LIVE_VARIANT_ID)
    if live is None:
        raise SystemExit(f"Live variant {LIVE_VARIANT_ID} not registered — "
                         f"run `python register_p300.py` first.")

    con = sqlite3.connect(str(db.DASH_DB))
    cur = con.cursor()

    if reset:
        log.info(f"Purging prior replay data for {variant_id}...")
        cur.execute("DELETE FROM trades WHERE strategy_variant = ?",
                    (variant_id,))
        cur.execute("DELETE FROM variant_daily_returns WHERE variant_id = ?",
                    (variant_id,))
        cur.execute("DELETE FROM variant_events WHERE variant_id = ?",
                    (variant_id,))
        cur.execute("DELETE FROM variants WHERE id = ?",
                    (variant_id,))
        con.commit()

    existing = cur.execute("SELECT id FROM variants WHERE id = ?",
                            (variant_id,)).fetchone()
    if existing is None:
        spec = dict(live["spec"])
        # If --with-fomc is requested, inject the FOMC sleeve into composition.
        # Also flag this on the spec so downstream tooling knows the run isn't
        # apples-to-apples with the live variant.
        if WITH_FOMC[0]:
            comp = list(spec.get("composition") or [])
            # default: 5% allocation (replacing the 5% cash reserve), k=5x
            comp.append({
                "strategy_id": "FOMC", "weight_pct": 5.0,
                "params": {"leverage": 5.0},
                "note": "FOMC long T-10h -> T+0.5h, regime+sentiment filtered.",
            })
            spec["composition"] = comp
            sl = dict(spec.get("sleeve_leverages") or {})
            sl["fomc"] = 5.0
            spec["sleeve_leverages"] = sl
            spec["_fomc_sleeve"] = "added by --with-fomc"
        spec_json = json.dumps(spec, indent=2)
        now_iso = datetime.now(timezone.utc).isoformat()
        cur.execute("""
            INSERT INTO variants (
                id, short_name, long_name, kind, parent_variant_id, version, status,
                is_primary, capital_usdt, color, spec_json, notes, superseded_by,
                reconcile_against, enabled, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            variant_id,
            f"P-300 Tactical-Only REPLAY {variant_id.split('__replay', 1)[-1] or ''}".strip(),
            "Historical replay of P-300 Tactical-Only 1.0 — same sleeve code, "
            "simulated clock. Core J+ MLgate NOT included. Variant weights "
            "sum to 45% of intended capital.",
            "full_portfolio", None, "1.0-replay", "SHADOW",
            0, float(live.get("capital_usdt") or 10000), "#8b0000",
            spec_json,
            "Created by backtest_runner.py. Trades here come from historical "
            "replay of live dispatch code under services/clock.py simulated-now.",
            None, None,
            0,  # enabled=0 — replay variant should NEVER tick in live mode
            now_iso,
        ))
        con.commit()
        log.info(f"Registered replay variant {variant_id} (disabled — "
                 f"will only be driven by this runner).")
    con.close()
    return variant_registry.get_variant(variant_id)


# ─── Scheduled-exit close, variant-scoped ─────────────────────────────────────

def _load_close_fn(strategy: str):
    """Return the sleeve-specific close function (which applies fees/funding).
    Falls back to the engine's simple close for unknown strategies."""
    if strategy == "ADX":
        from services.adx_service import _close_adx_shadow
        return _close_adx_shadow
    if strategy == "CARRY":
        from services.carry_service import _close_carry_shadow
        return _close_carry_shadow
    if strategy == "THU_BEAR":
        from services.thu_bear_service import _close_thu_bear_shadow
        return _close_thu_bear_shadow
    if strategy == "PDO_RETOUCH":
        from services.pdo_retouch_service import _close_pdo_shadow
        return _close_pdo_shadow
    if strategy == "CPR":
        from services.cpr_service import _close_cpr_shadow
        return _close_cpr_shadow
    if strategy == "FOMC":
        from services.fomc_service import _close_fomc_shadow
        return _close_fomc_shadow
    return None


def mark_remaining_at_end(variant_id: str) -> int:
    """Force-close any trades still open at end of backtest, at the current
    clock's price. Uses each sleeve's own close (fees + funding applied).
    Returns count closed."""
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    opens = con.execute("""
        SELECT id, asset, strategy FROM trades
        WHERE strategy_variant = ? AND execution_mode = 'SHADOW'
          AND status = 'open'
    """, (variant_id,)).fetchall()
    con.close()
    n = 0
    for t in opens:
        price = _get_current_price(t["asset"])
        if price is None:
            log.warning(f"[end-mark] no price for {t['asset']} at clock "
                        f"{clock.now_iso()} — {t['id']} left open")
            continue
        close_fn = _load_close_fn(t["strategy"])
        if close_fn is not None:
            close_fn(t["id"], price, "end_of_backtest_window")
            n += 1
    return n


def close_due_for_variant(variant_id: str, now_utc: datetime) -> int:
    """Close any open shadow trade for this variant whose scheduled exit_time
    has passed. Dispatches to the sleeve-specific close (so fees/funding are
    applied); falls back to a simple mark-to-close if no sleeve owns the
    strategy. Returns count closed."""
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    opens = con.execute("""
        SELECT id, asset, strategy, exit_time FROM trades
        WHERE strategy_variant = ? AND execution_mode = 'SHADOW'
          AND status = 'open'
    """, (variant_id,)).fetchall()
    con.close()

    n = 0
    for t in opens:
        exit_time = t["exit_time"]
        if not exit_time:
            continue
        try:
            exit_dt = datetime.fromisoformat(exit_time)
        except ValueError:
            continue
        if exit_dt.tzinfo is None:
            exit_dt = exit_dt.replace(tzinfo=timezone.utc)
        if now_utc < exit_dt:
            continue
        price = _get_current_price(t["asset"])
        if price is None:
            # Stale price — skip THIS tick but log so a persistent stale
            # condition (which would leave the trade stuck open) is visible.
            age_h = (now_utc - exit_dt).total_seconds() / 3600
            if age_h > 1:
                log.warning(f"[close_due] {t['id']} {t['strategy']} {t['asset']} "
                            f"past exit_time by {age_h:.1f}h but no price available "
                            f"at clock={now_utc.isoformat()}")
            continue
        close_fn = _load_close_fn(t["strategy"])
        if close_fn is None:
            log.warning(f"[close_due] no close_fn for strategy {t['strategy']!r} "
                        f"({t['id']}) — trade will leak past exit_time")
            continue
        try:
            close_fn(t["id"], price, "scheduled_exit")
            n += 1
        except Exception:
            log.exception(f"[close_due] close_fn raised for {t['id']} "
                          f"{t['strategy']} — trade may leak past exit_time")
    return n


# ─── Dispatch composition (variant-scoped) ────────────────────────────────────

def tick_replay_variant(variant: dict) -> None:
    """Dispatch all sleeves in the replay variant's composition for the
    current simulated clock. Mirrors variant_engine._tick_composition but
    scoped to a single variant so we don't accidentally tick live data."""
    variant_engine._load_dispatch()
    spec = variant.get("spec") or {}
    composition = spec.get("composition") or []
    for sleeve in composition:
        if sleeve.get("portfolio_id"):
            continue
        strategy_id = sleeve.get("strategy_id")
        if not strategy_id:
            continue
        if strategy_id in SKIP_STRATEGIES:
            continue
        dispatcher = variant_engine.STRATEGY_DISPATCH.get(strategy_id)
        if dispatcher is None:
            continue
        sleeve_with_k = dict(sleeve)
        sleeve_with_k["_effective_leverage"] = \
            variant_engine._resolve_sleeve_leverage(spec, sleeve)
        try:
            dispatcher(variant, sleeve_with_k)
        except Exception:
            log.exception(f"{strategy_id} dispatch error at {clock.now_iso()}")


# ─── NAV + metrics ────────────────────────────────────────────────────────────

def build_daily_nav(variant_id: str, capital: float, start: datetime,
                    end: datetime) -> list[dict]:
    """Aggregate closed-trade PnL by UTC date, compound into NAV curve."""
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT actual_exit_time, pnl_usdt, strategy
        FROM trades
        WHERE strategy_variant = ? AND status = 'closed'
          AND actual_exit_time IS NOT NULL AND pnl_usdt IS NOT NULL
        ORDER BY actual_exit_time
    """, (variant_id,)).fetchall()
    con.close()
    from collections import defaultdict
    daily_pnl: dict[str, float] = defaultdict(float)
    for r in rows:
        d = r["actual_exit_time"][:10]
        daily_pnl[d] += float(r["pnl_usdt"] or 0.0)
    # Emit one row per calendar day in [start, end] — even zero-PnL days,
    # so Sharpe / MDD are well-defined over the full window.
    out = []
    equity = capital
    d = start.date()
    end_d = end.date()
    while d <= end_d:
        key = d.isoformat()
        pnl = daily_pnl.get(key, 0.0)
        equity += pnl
        ret_pct = (pnl / (equity - pnl) * 100) if (equity - pnl) > 0 else 0.0
        out.append({"date": key, "equity_usdt": equity,
                    "daily_pnl": pnl, "return_pct": ret_pct})
        d = d + timedelta(days=1)
    return out


def write_replay_daily_returns(variant_id: str, nav_rows: list[dict]) -> None:
    """Persist the daily NAV series as variant_daily_returns rows (source='replay')."""
    con = sqlite3.connect(str(db.DASH_DB))
    now_iso = datetime.now(timezone.utc).isoformat()
    con.execute("DELETE FROM variant_daily_returns WHERE variant_id = ?",
                (variant_id,))
    rows = [(variant_id, r["date"], r["return_pct"], "replay", None, now_iso)
            for r in nav_rows]
    con.executemany("""
        INSERT INTO variant_daily_returns
        (variant_id, date, return_1x_pct, source, regime, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, rows)
    con.commit()
    con.close()


def compute_metrics(nav_rows: list[dict], capital: float) -> dict:
    if not nav_rows:
        return {}
    rets = [r["return_pct"] / 100.0 for r in nav_rows]
    final_equity = nav_rows[-1]["equity_usdt"]
    total_return = (final_equity / capital) - 1
    n_days = len(nav_rows)
    years = n_days / 365.25
    cagr = (final_equity / capital) ** (1 / years) - 1 if years > 0 and final_equity > 0 else float("nan")
    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        sd = math.sqrt(var)
        sharpe = (mean / sd) * math.sqrt(365) if sd > 0 else float("nan")
    else:
        sharpe = float("nan")
    # MDD on equity curve
    peak = capital
    mdd = 0.0
    for r in nav_rows:
        peak = max(peak, r["equity_usdt"])
        dd = (r["equity_usdt"] / peak) - 1 if peak > 0 else 0
        mdd = min(mdd, dd)
    return {
        "final_equity": final_equity,
        "total_return_pct": total_return * 100,
        "cagr_pct": cagr * 100 if not math.isnan(cagr) else float("nan"),
        "sharpe_daily_ann": sharpe,
        "mdd_pct": mdd * 100,
        "n_days": n_days,
    }


def per_sleeve_pnl(variant_id: str) -> dict[str, dict]:
    con = sqlite3.connect(str(db.DASH_DB))
    rows = con.execute("""
        SELECT strategy,
               COUNT(*) AS n,
               SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) AS wins,
               COALESCE(SUM(pnl_usdt), 0) AS total_pnl,
               COALESCE(AVG(pnl_pct), 0) AS avg_pct
        FROM trades
        WHERE strategy_variant = ? AND status = 'closed'
        GROUP BY strategy
        ORDER BY total_pnl DESC
    """, (variant_id,)).fetchall()
    con.close()
    out = {}
    for strat, n, wins, total, avg in rows:
        out[strat] = {"trades": n, "win_rate_pct": (wins / n * 100) if n else 0,
                      "total_pnl_usdt": total, "avg_pnl_pct": avg}
    return out


# ─── Main loop ────────────────────────────────────────────────────────────────

def run(start: datetime, end: datetime, interval_hours: int,
        reset: bool, tag: str | None = None,
        progress_every_days: int = 30) -> None:
    variant_id = replay_variant_id(tag)
    variant = ensure_replay_variant(variant_id, reset=reset)
    capital = float(variant.get("capital_usdt") or 10000)
    log.info(f"Replay variant: {variant['id']} | capital: ${capital:,.0f}")
    log.info(f"Window: {start.isoformat()} → {end.isoformat()} "
             f"| tick interval: {interval_hours}h")

    total_ticks = int(((end - start).total_seconds() // 3600) // interval_hours) + 1
    log.info(f"Total ticks: {total_ticks:,}")

    t0 = _time.time()
    last_progress = t0
    n_closed_scheduled = 0
    tick_count = 0
    cur = start
    while cur <= end:
        clock.set_simulated_now(cur)
        n_closed_scheduled += close_due_for_variant(variant["id"], cur)
        tick_replay_variant(variant)
        tick_count += 1
        # Progress log every 120 ticks (~5 days of simulated time at hourly
        # cadence), OR every 10 wall-seconds, whichever comes first.
        if tick_count % 120 == 0 or (_time.time() - last_progress) > 10:
            now_r = _time.time()
            elapsed = now_r - t0
            pct = tick_count / total_ticks
            eta = elapsed * (1 - pct) / pct if pct > 0 else 0
            rate = tick_count / elapsed if elapsed > 0 else 0
            log.info(f"[{cur.date()} {cur.hour:02d}h] tick {tick_count:,}/{total_ticks:,} "
                     f"({pct*100:.1f}%) {rate:.0f} t/s eta={eta:.0f}s")
            last_progress = now_r
        cur = cur + timedelta(hours=interval_hours)

    # Mark any trades still open at end-of-window at end-of-window PRICES,
    # via each sleeve's own close (so fees + funding are applied). We do NOT
    # advance the clock past `end` — that would read future prices.
    clock.set_simulated_now(end)
    n_final = mark_remaining_at_end(variant["id"])
    clock.set_simulated_now(None)

    elapsed = _time.time() - t0
    log.info(f"Replay complete. {tick_count:,} ticks in {elapsed:.1f}s "
             f"({tick_count/elapsed:.0f} ticks/s). "
             f"Scheduled-exit closes: {n_closed_scheduled:,} during window, "
             f"{n_final} marked-to-end-of-window (open at final tick, "
             f"closed at end clock price).")

    # Build NAV, persist, report
    nav = build_daily_nav(variant["id"], capital, start, end)
    write_replay_daily_returns(variant["id"], nav)
    metrics = compute_metrics(nav, capital)
    sleeves = per_sleeve_pnl(variant["id"])

    print("\n" + "=" * 72)
    print(f"  P-300 TACTICAL-ONLY REPLAY — {start.date()} to {end.date()}")
    print(f"  (5 live sleeves, 45% intended capital; Core J+ MLgate NOT included)")
    print("=" * 72)
    print(f"  Starting capital:   ${capital:>14,.2f}")
    if metrics:
        print(f"  Final equity:       ${metrics['final_equity']:>14,.2f}")
        print(f"  Total return:       {metrics['total_return_pct']:>14,.2f}%")
        print(f"  CAGR:               {metrics['cagr_pct']:>14,.2f}%")
        print(f"  Sharpe (daily ann): {metrics['sharpe_daily_ann']:>14.2f}")
        print(f"  Max drawdown:       {metrics['mdd_pct']:>14,.2f}%")
        print(f"  Sample days:        {metrics['n_days']:>14,}")
    print()
    print("  Per-sleeve attribution:")
    print(f"  {'strategy':<14} {'trades':>7} {'win%':>6} {'total $':>14} {'avg %':>9}")
    for strat, d in sleeves.items():
        print(f"  {strat:<14} {d['trades']:>7} {d['win_rate_pct']:>6.1f} "
              f"{d['total_pnl_usdt']:>14,.2f} {d['avg_pnl_pct']:>9,.2f}")
    print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True, type=_parse_ts,
                    help="ISO date or datetime (UTC), e.g. 2023-01-01")
    ap.add_argument("--end", required=True, type=_parse_ts,
                    help="ISO date or datetime (UTC)")
    ap.add_argument("--interval-hours", type=int, default=1,
                    help="Tick interval in hours (default 1)")
    ap.add_argument("--reset", action="store_true",
                    help="Purge prior replay trades + daily returns + variant row (for this tag)")
    ap.add_argument("--tag", type=str, default=None,
                    help="Suffix appended to the replay variant id so multiple "
                         "runs coexist (e.g. --tag A, --tag B). Default: none.")
    ap.add_argument("--with-fomc", action="store_true",
                    help="Inject the FOMC sleeve into the replay spec (5%% "
                         "allocation, k=5x) so the backtest measures the "
                         "tactical stack PLUS FOMC. Use --tag to label runs.")
    ap.add_argument("--skip", action="append", default=[],
                    help="Skip a strategy id during replay (repeatable). "
                         "E.g. --skip JPLUS-CORE drops the daily-return "
                         "sleeve, which is the slowest dispatch. Trades-based "
                         "NAV is unaffected by skipping JPLUS-CORE.")
    args = ap.parse_args(argv)
    WITH_FOMC[0] = bool(args.with_fomc)
    SKIP_STRATEGIES.update(args.skip)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    trade_db.init_db()
    variant_registry.init_schema()

    run(args.start, args.end, args.interval_hours, args.reset, tag=args.tag)
    return 0


def _parse_ts(s: str) -> datetime:
    """Accept 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS', assume UTC."""
    s = s.strip()
    if "T" in s:
        dt = datetime.fromisoformat(s)
    else:
        dt = datetime.fromisoformat(s + "T00:00:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


if __name__ == "__main__":
    sys.exit(main())

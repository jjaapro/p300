"""Backtest runner for the P-300 portfolio.

Replays each sleeve's LIVE dispatch code against historical market data
from trader.db, using services/clock.py to present a simulated "now" to
each module so DB queries never see future bars. No signal logic is
reimplemented — the strategy code under test is literally the same code
that runs live. The clock-advance loop is shared with run.py --mode sim
via services.sim_loop.

Usage:
  python backtest_runner.py --start 2023-01-01 --end 2026-04-15
  python backtest_runner.py --start 2021-07-01 --end 2026-04-15 --interval-hours 1
  python backtest_runner.py --reset       # purge prior replay data first

Output:
  - Closed trades in dashboard.db tagged strategy_variant='p300..._replay'
    (NEVER contaminates the live variant's data).
  - NAV is computed from the trade ledger via
    services.strategy_health.trades_daily_returns. No variant_daily_returns
    rows are written — Phase 5 made reporting tools trades-based.
  - Console report: total / annualized / Sharpe / MDD / trade count / per-sleeve PnL.
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

from services import clock, sim_loop, trade_db, variant_engine, variant_registry  # noqa: E402
from services.price_feed import _get_current_price  # noqa: E402
from services import db, strategy_health

log = logging.getLogger("p300.backtest")

LIVE_VARIANT_ID = "p300_aggressive_v2_v1_0"
REPLAY_VARIANT_ID_PREFIX = "p300_aggressive_v2_v1_0__replay"

# Set by main() based on --with-fomc; ensure_replay_variant() reads it.
# Single-element list so closures can mutate without `nonlocal` gymnastics.
WITH_FOMC: list[bool] = [False]

# Strategy ids to skip during replay (--skip flag). Use to isolate a
# sleeve's contribution in A/B comparison runs.
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
        from strategies.sleeves.adx.signal import _close_adx_shadow
        return _close_adx_shadow
    if strategy == "CARRY":
        from strategies.sleeves.carry.signal import _close_carry_shadow
        return _close_carry_shadow
    if strategy == "THU_BEAR":
        from strategies.sleeves.thu_bear.signal import _close_thu_bear_shadow
        return _close_thu_bear_shadow
    if strategy == "PDO_RETOUCH":
        from strategies.sleeves.pdo.signal import _close_pdo_shadow
        return _close_pdo_shadow
    if strategy == "CPR":
        from strategies.sleeves.cpr.signal import _close_cpr_shadow
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


def check_liquidations_for_variant(variant_id: str, now_utc: datetime) -> int:
    """Walk open shadow trades for this variant; for any leveraged trade
    whose margin trajectory would have breached maintenance margin between
    its entry and now_utc, force-close it via the sleeve's close function
    at the liquidation price/time.

    Tags forced-closes via the sleeve's close `reason` argument with
    'forced_exit:liquidation' so they're identifiable in the trades table.

    Always-on in backtest mode (per project decision). Trades at leverage
    <= 1 are skipped inside the adapter (no liquidation possible).
    Returns count force-closed.
    """
    from services.margin_check import check_liquidations_for_variant as _check_liq

    events = _check_liq(variant_id, now_utc)
    if not events:
        return 0
    n = 0
    for trade, liq in events:
        close_fn = _load_close_fn(trade["strategy"])
        if close_fn is None:
            log.warning(f"[liq] no close_fn for {trade['strategy']!r} "
                        f"({trade['id']}) — liquidation event ignored")
            continue
        # Set the simulated clock to the liquidation time so the close
        # function records actual_exit_time correctly.
        prior = clock._simulated_now if hasattr(clock, '_simulated_now') else None
        clock.set_simulated_now(liq.liq_time)
        try:
            close_fn(trade["id"], liq.liq_price, "forced_exit:liquidation")
            n += 1
            log.warning(f"[liq] {trade['id']} {trade['strategy']} {trade['asset']} "
                        f"force-closed at {liq.liq_time.isoformat()} "
                        f"px={liq.liq_price:.2f} ({liq.reason})")
        except Exception:
            log.exception(f"[liq] close_fn raised for {trade['id']} during "
                          f"liquidation force-close — trade may be inconsistent")
        finally:
            # Restore prior clock; the main loop will set it back per-tick anyway.
            clock.set_simulated_now(prior)
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
        # Skip non-deterministic sleeves on historical replay. The
        # AI_QUANT sleeve carries params.deterministic=False because the
        # LLM produces different decisions each run; including it in a
        # backtest would make the replay irreproducible. Forward paper
        # is the only honest evaluation for those sleeves.
        if (sleeve.get("params") or {}).get("deterministic") is False:
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
    """Calendar-complete daily NAV series from the trades ledger.

    Uses the canonical trades-based realized-PnL path
    (services.strategy_health.trades_daily_returns). Equity is rebuilt
    here as ``capital + cumulative daily PnL`` so the per-row equity_usdt
    matches the user's mental model of "starting bankroll plus what the
    bot earned by date d." Empty days are zero-filled."""
    daily = strategy_health.trades_daily_returns(
        variant_id, start.date().isoformat(), end.date().isoformat(),
        capital, zero_fill=True,
    )
    out: list[dict] = []
    equity = capital
    for date_iso, ret_pct in daily:
        # ret_pct from trades_daily_returns is (sum closed pnl_usdt that
        # day / capital × 100). Convert back to dollars and accumulate.
        pnl = ret_pct / 100.0 * capital
        equity += pnl
        out.append({"date": date_iso, "equity_usdt": equity,
                    "daily_pnl": pnl, "return_pct": ret_pct})
    return out


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

    # Counters are mutable so the per-tick closure can update them in place
    # without nonlocal gymnastics — sim_loop.run_sim doesn't return tick state.
    counters = {"liquidated": 0, "closed_scheduled": 0}
    t0 = _time.time()
    last_progress = [t0]  # mutable for closure

    def _tick(cur: datetime) -> None:
        # Liquidation checks run BEFORE scheduled-exit checks so a liquidated
        # trade can't be re-counted as a scheduled close. Always-on per
        # project decision (v1 of the margin/liquidation simulator).
        counters["liquidated"] += check_liquidations_for_variant(
            variant["id"], cur)
        counters["closed_scheduled"] += close_due_for_variant(
            variant["id"], cur)
        tick_replay_variant(variant)
        # Progress log every 120 ticks OR every 10 wall-seconds.
        elapsed_now = _time.time() - t0
        # Rough tick count from elapsed sim-time (avoids needing to thread
        # tick_count through the closure).
        sim_secs = (cur - start).total_seconds()
        approx_ticks = int(sim_secs // (interval_hours * 3600)) + 1
        if approx_ticks % 120 == 0 or (_time.time() - last_progress[0]) > 10:
            pct = approx_ticks / total_ticks
            eta = elapsed_now * (1 - pct) / pct if pct > 0 else 0
            rate = approx_ticks / elapsed_now if elapsed_now > 0 else 0
            log.info(f"[{cur.date()} {cur.hour:02d}h] "
                     f"tick {approx_ticks:,}/{total_ticks:,} "
                     f"({pct*100:.1f}%) {rate:.0f} t/s eta={eta:.0f}s")
            last_progress[0] = _time.time()

    tick_count = sim_loop.run_sim(start, end, interval_hours * 3600, _tick)

    # Mark any trades still open at end-of-window at end-of-window PRICES,
    # via each sleeve's own close (so fees + funding are applied). We do NOT
    # advance the clock past `end` — that would read future prices.
    clock.set_simulated_now(end)
    n_final = mark_remaining_at_end(variant["id"])
    clock.set_simulated_now(None)

    elapsed = _time.time() - t0
    log.info(f"Replay complete. {tick_count:,} ticks in {elapsed:.1f}s "
             f"({tick_count/elapsed:.0f} ticks/s). "
             f"Scheduled-exit closes: {counters['closed_scheduled']:,} "
             f"during window, {counters['liquidated']:,} liquidations, "
             f"{n_final} marked-to-end-of-window (open at final tick, "
             f"closed at end clock price).")

    # Build NAV from realized trades and report. No variant_daily_returns
    # write — Phase 5 made reporting tools trades-based, so VDR rows for
    # replay variants are no longer read by anything.
    nav = build_daily_nav(variant["id"], capital, start, end)
    metrics = compute_metrics(nav, capital)
    sleeves = per_sleeve_pnl(variant["id"])

    print("\n" + "=" * 72)
    print(f"  P-300 REPLAY — {start.date()} to {end.date()}")
    print(f"  (tactical + Core J+ sub-sleeves; AI_QUANT skipped — non-deterministic)")
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
                         "E.g. --skip CPR drops the contrarian-positioning-"
                         "reversal sleeve from this run. Useful for A/B "
                         "comparisons that isolate one sleeve's contribution.")
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

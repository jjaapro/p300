"""Phase 0 invariant tests for the live/sim refactor.

These tests anchor the live bot's current state in `data/dashboard.db`
so each later refactor phase (delete daily-return accrual, delete
catchup, migrate reporting tools, etc.) is regression-checkable. See
`~/.claude/plans/let-s-now-plan-this-staged-lantern.md` for the full
phase plan.

Three invariants:

1. **Trade counts only grow.** For each (strategy_variant, strategy)
   tuple in TRADE_COUNT_BASELINE, the current count must be >= baseline.
   This catches the V2-bug class — a sleeve silently disabled by stale
   variant config or by accidentally being removed from STRATEGY_DISPATCH.

2. **PnL aggregation paths agree.** Sum of closed `trades.pnl_usdt` for
   the live variant equals what `strategy_health._trades_daily_returns`
   produces over the same window (after un-percentifying via capital).
   If either path drifts, the equality breaks.

3. **`today_inputs()` smoke.** Returns a dict with all six sub-sleeve
   weight keys. Catches simulator import / warmup regressions that
   would silently make jplus_live handlers fail with `no_inputs`.

If a later phase intentionally changes one of these, update the
baseline in the same commit so the change is visible in review.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services import db


LIVE_VARIANT = "p300_aggressive_v2_v1_0"

# Baseline captured 2026-05-10. Tests assert current counts >= these
# floors; organic growth is allowed, shrinkage is a regression.
TRADE_COUNT_BASELINE = {
    (LIVE_VARIANT, "CPR"):              4,
    (LIVE_VARIANT, "JPLUS_R4_BTC"):     5,
    (LIVE_VARIANT, "JPLUS_R4_BTC_V2"):  1,
    (LIVE_VARIANT, "JPLUS_R4_ETH"):     2,
    (LIVE_VARIANT, "JPLUS_R4_ETH_V2"):  1,
    (LIVE_VARIANT, "PDO_RETOUCH"):      1,
}

EXPECTED_WEIGHT_KEYS = {
    "r4_btc", "r4_eth", "r4_btc_v2", "r4_eth_v2", "ema_btc", "eth_daily",
}


@pytest.fixture(scope="module")
def live_db_ro():
    """Read-only connection to data/dashboard.db. Skip the whole module
    if the live DB doesn't exist (e.g. a CI runner without bootstrapped
    data). The path resolves dynamically so a future Phase 1 sim-mode
    redirection of services.db.DASH_DB also redirects this fixture —
    useful when the same suite is run against a sim DB."""
    p = Path(db.DASH_DB)
    if not p.exists():
        pytest.skip(f"dashboard.db not found at {p}; phase 0 baseline skipped")
    uri = f"file:{p.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    yield con
    con.close()


# ─── Invariant 1: trade-count baseline ─────────────────────────────────────

def test_trade_counts_meet_or_exceed_baseline(live_db_ro):
    rows = live_db_ro.execute(
        "SELECT strategy_variant, strategy, COUNT(*) AS n "
        "FROM trades GROUP BY strategy_variant, strategy"
    ).fetchall()
    current = {(r["strategy_variant"], r["strategy"]): r["n"] for r in rows}
    failures = []
    for key, expected_min in TRADE_COUNT_BASELINE.items():
        got = current.get(key, 0)
        if got < expected_min:
            failures.append(
                f"  {key[0]} / {key[1]}: baseline {expected_min}, got {got}"
            )
    assert not failures, (
        "Trade-count regression detected (a sleeve may be silently "
        "disabled or trades deleted):\n" + "\n".join(failures)
    )


def test_baseline_strategies_still_present(live_db_ro):
    """Every (variant, strategy) tuple in the baseline still appears in
    the trades table. Catches the case where every row of a sleeve was
    deleted (count would drop to 0 and disappear from GROUP BY)."""
    rows = live_db_ro.execute(
        "SELECT DISTINCT strategy_variant, strategy FROM trades"
    ).fetchall()
    present = {(r["strategy_variant"], r["strategy"]) for r in rows}
    missing = [key for key in TRADE_COUNT_BASELINE if key not in present]
    assert not missing, (
        "Baseline (variant, strategy) tuples not found in trades:\n  "
        + "\n  ".join(f"{v} / {s}" for v, s in missing)
    )


# ─── Invariant 2: PnL aggregation consistency ──────────────────────────────

def test_closed_pnl_sum_matches_strategy_health(live_db_ro):
    """The trade ledger's sum of closed pnl_usdt for the live variant
    must equal what strategy_health._trades_daily_returns produces over
    the same window (after un-percentifying via capital). If either path
    drifts, the equality breaks — this anchors that PnL aggregation
    stays consistent across the refactor."""
    cap_row = live_db_ro.execute(
        "SELECT capital_usdt FROM variants WHERE id=?",
        (LIVE_VARIANT,),
    ).fetchone()
    if cap_row is None:
        pytest.skip(f"live variant {LIVE_VARIANT} not registered")
    capital = float(cap_row["capital_usdt"]) or 10000.0

    range_row = live_db_ro.execute(
        "SELECT MIN(date(actual_exit_time)) AS lo, "
        "       MAX(date(actual_exit_time)) AS hi "
        "FROM trades WHERE strategy_variant=? AND status='closed'",
        (LIVE_VARIANT,),
    ).fetchone()
    lo, hi = range_row["lo"], range_row["hi"]
    if lo is None or hi is None:
        pytest.skip("no closed trades on live variant yet")

    direct_pnl = float(live_db_ro.execute(
        "SELECT COALESCE(SUM(pnl_usdt), 0) FROM trades "
        "WHERE strategy_variant=? AND status='closed' "
        "  AND date(actual_exit_time) >= ? "
        "  AND date(actual_exit_time) <= ?",
        (LIVE_VARIANT, lo, hi),
    ).fetchone()[0])

    from services import strategy_health
    rets_pct = strategy_health._trades_daily_returns(
        LIVE_VARIANT, lo, hi, capital
    )
    health_pnl = sum(r * capital / 100.0 for r in rets_pct)

    assert abs(direct_pnl - health_pnl) < 1e-6, (
        f"PnL aggregation mismatch over [{lo}, {hi}]:\n"
        f"  direct sum(pnl_usdt) = {direct_pnl:.6f}\n"
        f"  strategy_health     = {health_pnl:.6f}\n"
        f"  diff                = {direct_pnl - health_pnl:.6f}"
    )


# ─── Invariant 3: today_inputs() smoke ─────────────────────────────────────

def test_today_inputs_returns_complete_weights():
    """today_inputs() must return a dict whose 'weights' key has all six
    sub-sleeve weight keys. Catches simulator-import or warmup regressions
    that would silently make jplus_live handlers exit with no_inputs."""
    from jplus import simulate as core_sim
    ti = core_sim.today_inputs()
    assert ti is not None, (
        "today_inputs() returned None — simulator warmup failed (likely "
        "insufficient data history for regime/EMA/vol-target inputs)"
    )
    assert "weights" in ti, (
        f"today_inputs() missing 'weights' key. Got keys: {sorted(ti.keys())}"
    )
    got_keys = set(ti["weights"].keys())
    missing = EXPECTED_WEIGHT_KEYS - got_keys
    assert not missing, (
        f"today_inputs().weights missing expected keys: {sorted(missing)}\n"
        f"  got: {sorted(got_keys)}\n"
        f"  expected superset of: {sorted(EXPECTED_WEIGHT_KEYS)}"
    )

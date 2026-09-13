"""Look-ahead safety across multiple clock positions.

The contract: if we run a signal evaluator at clock=T1 and again at
clock=T2 (T2 > T1), the per-date outputs for every date <= min(T1, T2)
MUST be bit-identical. If they differ, the code is peeking at future data.

This is the single most important integration test in the repo — it
certifies that our backtest can be trusted to represent what would have
been known at each point in time.

Uses the real data/trader.db so the test exercises the actual data
loaders (not synthetic fixtures). It's slow-ish (~15s) but runs once.

Coverage:
  - jplus.simulate (Core J+)
  - bots.adx.strategy.signal._current_signal (S-003 ADX)
  - regime_classifier.classify_regime
  - bots.carry.strategy.signal._load_recent_daily_funding (S-078 Carry)

Four more arms covered CPR, PDO, THU_BEAR and FOMC until 2026-09-13. Those
sleeves were archived and the arms went with them, to
studies/material/archive/tests/test_lookahead.py — still runnable, on demand,
by whoever picks up the open PDO/CPR/THU_BEAR re-validations. They are out of
the default suite because pytest.ini sets `testpaths = tests`, and because the
archive is explicitly unsupported: a main-suite test must not go red when
unmaintained code drifts.

Running-bot coverage, as of 2026-09-13: squeeze_bull and short_squeeze live
in tests/test_bot_lookahead.py; r4's sizing engine is covered here, both by
the jplus arm and by the today_inputs() arms at the bottom of this file.
chento_v3 is the one running bot still uncovered — its loaders have no upper
clock bound (BACKLOG 7b), so a correct boundary assertion would be red today.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from strategies.support import clock
from studies.jplus_analytic import simulate


def assert_no_data_after_clock(dates, clk, what: str) -> None:
    """The other half of the contract: a run must not SEE past its own clock.

    Comparing two clock positions on their common dates proves the past is
    not revised. It does NOT prove the clock is respected — if a loader
    ignores the clock entirely both runs return the whole table, every
    common-date comparison is trivially equal, and the test passes green
    while the bot trades on tomorrow's candles. Two of the four original
    arms here had exactly that hole (2026-09-13).

    `dates` is an iterable of "YYYY-MM-DD" strings or dates; `clk` the
    simulated datetime the run used.
    """
    if not dates:
        raise AssertionError(f"{what}: no data at all — test would be vacuous")
    cutoff = clk.date().isoformat()
    after = sorted(str(d) for d in dates if str(d) > cutoff)
    assert not after, (
        f"{what}: LOOK-AHEAD — {len(after)} date(s) after the clock {cutoff}, "
        f"first 5 {after[:5]}. The loader is not clock-bounded.")


@pytest.mark.slow
@pytest.mark.parametrize("early_clock,late_clock", [
    (datetime(2023, 6, 1, tzinfo=timezone.utc), datetime(2024, 6, 1, tzinfo=timezone.utc)),
    (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2025, 1, 1, tzinfo=timezone.utc)),
])
def test_common_dates_identical_across_clocks(early_clock, late_clock):
    """The R4 sizing engine, both halves of the contract.

    This is r4's coverage as well as Core J+'s: `simulate.simulate` is a thin
    wrapper over `jplus_inputs._run_decision_loop`, the same walk that
    `today_inputs()` uses to size every live R4 fire.

    This arm is the AGREEMENT half only. The boundary half cannot be asserted
    on this output at all: `simulate` filters `k < clock_date`
    (simulate.py:47-48), and since 2026-09-13 `_run_decision_loop` drops that
    date too, so any boundary check downstream of either is true by
    construction and would mask an unbounded loader underneath. It is
    asserted on the loaders directly in test_jplus_loaders_are_clock_bounded.
    """
    from strategies.support import jplus_inputs

    def run(clk):
        clock.set_simulated_now(clk)
        jplus_inputs._invalidate_today_inputs_cache()
        raw, _state = jplus_inputs._run_decision_loop()
        # No boundary assertion here, deliberately. There was one, on the raw
        # loop keys, and it went decorative on 2026-09-13 the moment the 7a fix
        # landed: _run_decision_loop now drops every date >= the clock's, so
        # its keys can never reach past the clock no matter what the loaders
        # return. The drill proved it — pushing load_btc_hourly's bound 100
        # days into the future left it green. The same masking the note above
        # warns about for simulate(), one layer down. The boundary half lives
        # on the loaders themselves now: test_jplus_loaders_are_clock_bounded.
        cutoff = clk.date().isoformat()
        return {k: v for k, v in raw.items()
                if k < cutoff and k >= "2022-01-01"}

    early, late = run(early_clock), run(late_clock)
    clock.set_simulated_now(None)
    jplus_inputs._invalidate_today_inputs_cache()

    common = sorted(set(early) & set(late))
    assert len(common) > 30, "need enough common dates to be meaningful"

    diffs = []
    for d in common:
        a = early[d]
        b = late[d]
        # Compare every decision-relevant field
        for k in ("return_pct", "mode", "lev", "r1x_pct", "gated", "ema_p"):
            if isinstance(a[k], float):
                if abs(a[k] - b[k]) > 1e-9:
                    diffs.append((d, k, a[k], b[k]))
            else:
                if a[k] != b[k]:
                    diffs.append((d, k, a[k], b[k]))
    assert not diffs, f"first 5 look-ahead divergences: {diffs[:5]}"


@pytest.mark.slow
@pytest.mark.parametrize("clk", [
    datetime(2024, 11, 5, 20, 0, tzinfo=timezone.utc),   # R4_ETH entry minute
    datetime(2023, 6, 1, 4, 0, tzinfo=timezone.utc),
])
def test_jplus_loaders_are_clock_bounded(clk):
    """The boundary half for r4's sizing engine, asserted where the bound
    actually lives — on the five loaders `_run_decision_loop` reads.

    It cannot be asserted downstream (see the agreement arm above): both
    `simulate` and, since the 7a fix, `_run_decision_loop` drop dates at or
    after the clock, which hides an unbounded loader. And hiding is not the
    same as neutralising — `ema_pos` and the four R4 return maps are built
    from the HOURLY series, which no date filter touches.

    Hourly loaders are checked at TIMESTAMP granularity, not date: a date
    check would accept a 21:00 bar on a 20:00 clock. The daily loaders may
    include the clock's own date — that partial bar is what they are
    documented to return, and _run_decision_loop is what drops it.

    Proven to catch: load_btc_hourly's bound pushed 100 days past the clock.
    ~7 s per clock: the loaders read full history back to 2019, even though
    it skips the decision-loop walk.
    """
    from data import loaders

    now_s = int(clk.timestamp())
    clock.set_simulated_now(clk)
    try:
        btc_h = loaders.load_btc_hourly()
        eth_h = loaders.load_eth_hourly()
        btc_d = loaders.load_btc_daily()
        eth_d = loaders.load_eth_daily()
        ls = loaders.load_ls_ratio_btc()
    finally:
        clock.set_simulated_now(None)

    assert btc_h, "load_btc_hourly returned nothing — the arm would be vacuous"
    newest_btc = max(r[0] for r in btc_h)
    assert newest_btc <= now_s, (
        f"load_btc_hourly: LOOK-AHEAD — newest bar "
        f"{datetime.fromtimestamp(newest_btc, tz=timezone.utc)} is after the "
        f"clock {clk}. It feeds ema_pos and btc_d unfiltered.")

    assert eth_h, "load_eth_hourly returned nothing — the arm would be vacuous"
    newest_eth = max(datetime.strptime(d, "%Y-%m-%d").replace(
        hour=h, tzinfo=timezone.utc) for d, h in eth_h)
    assert newest_eth <= clk, (
        f"load_eth_hourly: LOOK-AHEAD — newest bar {newest_eth} is after the "
        f"clock {clk}. It feeds the R4_ETH return maps unfiltered.")

    for what, series in (("load_btc_daily", btc_d), ("load_eth_daily", eth_d),
                         ("load_ls_ratio_btc", ls)):
        assert_no_data_after_clock(series, clk, what)


@pytest.mark.slow
def test_replay_is_deterministic():
    """Same clock, same call → byte-identical output. No hidden state,
    no randomness."""
    clock.set_simulated_now(datetime(2025, 1, 1, tzinfo=timezone.utc))
    a = simulate.simulate(start_date="2023-01-01", end_date="2024-12-31")
    b = simulate.simulate(start_date="2023-01-01", end_date="2024-12-31")
    clock.set_simulated_now(None)
    assert a == b


def test_simulate_on_tiny_window_doesnt_crash():
    """Defensive: requesting a window too small for signals to compute
    shouldn't crash; it returns whatever was valid."""
    clock.set_simulated_now(datetime(2022, 2, 15, tzinfo=timezone.utc))
    out = simulate.simulate(start_date="2022-02-01", end_date="2022-02-10")
    clock.set_simulated_now(None)
    # May be empty if warmup isn't complete; just verify no crash and
    # return type contract holds.
    for d, rec in out.items():
        assert "return_pct" in rec
        assert "mode" in rec
        assert "lev" in rec
        assert "gated" in rec
        assert "ema_p" in rec


# ─── ADX signal look-ahead ──────────────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.parametrize("early_clock,late_clock", [
    (datetime(2023, 6, 1, tzinfo=timezone.utc), datetime(2024, 6, 1, tzinfo=timezone.utc)),
    (datetime(2024, 6, 1, tzinfo=timezone.utc), datetime(2025, 6, 1, tzinfo=timezone.utc)),
])
def test_adx_candle_loader_no_lookahead(early_clock, late_clock):
    """ADX's daily candles for a given date must not change when the clock
    advances. This pins the LOADER, which is where ADX's look-ahead risk
    actually lives.

    Named test_adx_signal_no_lookahead until 2026-09-13, and it called
    `_current_signal` at both clocks — but never compared the two results.
    Proven decorative: making `_current_signal` return None unconditionally
    left this test green. The dead calls are gone rather than being turned
    into an assertion, because a signal-level assertion here would be
    vacuous either way:

    `_current_signal(candles)` is a pure function of the list it is handed
    and reports the state of its LAST bar, so there are no later bars inside
    it to peek at — it cannot look ahead by construction. Feed it equal
    inputs and you get equal outputs; that is determinism, already covered by
    test_replay_is_deterministic, not clock-invariance.

    Note the loader's window START moves with the clock
    (`since_ts = upper_ts - days_back * 86400`, signal.py:59-61), so the two
    runs cover different spans. Only their intersection is comparable, which
    is what the zip below walks.
    """
    from bots.adx.strategy.signal import _load_btc_daily_candles

    clock.set_simulated_now(early_clock)
    early_candles = _load_btc_daily_candles(limit_days=400)
    early_dates = {c["dt"] for c in early_candles}

    clock.set_simulated_now(late_clock)
    late_candles = _load_btc_daily_candles(limit_days=400)
    clock.set_simulated_now(None)

    # HALF 1 — boundary. The early run must not have seen past its own clock.
    # Added 2026-09-13. Without it the test is blind to the most likely
    # look-ahead bug of all: a loader that ignores the clock entirely. Both
    # runs then return the whole table, every common-date comparison is
    # trivially equal, and the test passes. Verified by mutation — neutering
    # the `timestamp <= ?` bound left the agreement half green.
    assert_no_data_after_clock(early_dates, early_clock, "ADX daily candles")
    assert_no_data_after_clock({c["dt"] for c in late_candles}, late_clock,
                               "ADX daily candles")

    # HALF 2 — agreement. A date the early run DID see must not be revised
    # when the clock advances.
    common_candles_early = [c for c in early_candles if c["dt"] in {lc["dt"] for lc in late_candles}]
    common_candles_late = [c for c in late_candles if c["dt"] in early_dates]
    assert len(common_candles_early) > 50, "need enough common bars"
    for ce, cl in zip(common_candles_early, common_candles_late):
        assert ce["dt"] == cl["dt"]
        for k in ("open", "high", "low", "close"):
            assert abs(ce[k] - cl[k]) < 1e-6, \
                f"ADX candle mismatch at {ce['dt']} {k}: {ce[k]} vs {cl[k]}"


# ─── regime_classifier look-ahead ───────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.parametrize("early_clock,late_clock", [
    (datetime(2023, 6, 1, tzinfo=timezone.utc), datetime(2024, 6, 1, tzinfo=timezone.utc)),
    (datetime(2024, 6, 1, tzinfo=timezone.utc), datetime(2025, 6, 1, tzinfo=timezone.utc)),
])
def test_regime_classifier_no_lookahead(early_clock, late_clock):
    """regime_classifier.classify_regime must produce identical labels at two
    different clock positions for all common dates."""
    from strategies.support.regime_tactical import classify_regime

    clock.set_simulated_now(early_clock)
    early = {r["date"]: r for r in classify_regime("BTC")}

    clock.set_simulated_now(late_clock)
    late = {r["date"]: r for r in classify_regime("BTC")}
    clock.set_simulated_now(None)

    assert_no_data_after_clock(early, early_clock, "regime classify_regime")
    assert_no_data_after_clock(late, late_clock, "regime classify_regime")

    common = sorted(set(early) & set(late))
    assert len(common) > 100, "need enough common dates"

    diffs = []
    for d in common:
        e, l = early[d], late[d]
        if e["label"] != l["label"]:
            diffs.append((d, "label", e["label"], l["label"]))
        for k in ("close", "ma", "slope_pct", "rv_ann", "rv_pct"):
            ev, lv = e[k], l[k]
            if ev is None and lv is None:
                continue
            if ev is None or lv is None or abs(ev - lv) > 1e-6:
                diffs.append((d, k, ev, lv))
    assert not diffs, f"regime look-ahead divergences (first 5): {diffs[:5]}"


# ─── Carry funding-load look-ahead ──────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.parametrize("early_clock,late_clock", [
    (datetime(2024, 6, 1, tzinfo=timezone.utc), datetime(2025, 6, 1, tzinfo=timezone.utc)),
])
def test_carry_funding_no_lookahead(early_clock, late_clock):
    """Carry _load_recent_daily_funding must produce identical bars on common
    dates at two different clock positions. Uses days=400 to force overlap
    (default 30d would give zero overlap a year apart)."""
    from bots.carry.strategy.signal import _load_recent_daily_funding

    clock.set_simulated_now(early_clock)
    early = {r["date"]: r for r in _load_recent_daily_funding(days=400)}

    clock.set_simulated_now(late_clock)
    late = {r["date"]: r for r in _load_recent_daily_funding(days=400)}
    clock.set_simulated_now(None)

    assert_no_data_after_clock(early, early_clock, "carry daily funding")
    assert_no_data_after_clock(late, late_clock, "carry daily funding")

    common = sorted(set(early) & set(late))
    assert len(common) > 30, "need enough common funding days"

    diffs = []
    for d in common:
        for k in ("daily_funding_pct", "spot_close", "perp_close"):
            if abs(early[d][k] - late[d][k]) > 1e-9:
                diffs.append((d, k, early[d][k], late[d][k]))
    assert not diffs, f"carry funding look-ahead divergences (first 5): {diffs[:5]}"


# ─── R4 live sizing: today_inputs() must not read today's partial bar ───────
#
# BACKLOG 7a, fixed 2026-09-13. today_inputs() projects "today" from the tail
# of _run_decision_loop's state, and until then that state included the
# clock's own UTC date as a PARTIAL daily bar. Four live reads saw it — the
# regime (det_i), the R4 gate, vol-target leverage and the LS circuit breaker
# — so r4's sizing changed through the day and was frozen by whichever call
# the date-keyed cache saw first, i.e. by when the process last restarted.
# It also bypassed the bear-regime kill switch on 2026-03-03 and 2026-03-31.
#
# These are the only coverage today_inputs() has: tests/test_golden_r4.py
# stubs it out entirely.

@pytest.mark.slow
# Two hours, not five: measured against the bug, 0/4/12/23 all PASS on the
# broken code — only 20:00 read a different regime. One hour before the
# divergence plus the hour that diverged is what actually has teeth; the
# other three cost ~24s of decision-loop walks and caught nothing.
@pytest.mark.parametrize("hour", [4, 20])
def test_today_inputs_is_invariant_within_the_utc_day(hour):
    """Same UTC day, any hour: same answer. The docstring has promised this
    since the function was written; it was false until 2026-09-13.

    2024-11-05 is a real R4_ETH entry day on which the bug moved the answer
    from uncertain / lev 2.0 / w 0.148 at 04:00 to mild_bull / lev 2.5 / w
    0.130 at 20:00 — and R4_ETH's window opens at 20:00.
    """
    from strategies.support import jplus_inputs
    clock.set_simulated_now(datetime(2024, 11, 5, hour, tzinfo=timezone.utc))
    jplus_inputs._invalidate_today_inputs_cache()   # date-keyed: flush per hour
    try:
        ti = jplus_inputs.today_inputs()
    finally:
        clock.set_simulated_now(None)
        jplus_inputs._invalidate_today_inputs_cache()
    assert ti["date"] == "2024-11-05"
    assert (ti["mode"], ti["lev"], ti["gated"]) == ("uncertain", 2.0, False), (
        f"today_inputs() at {hour:02d}:00 returned {ti['mode']}/{ti['lev']}/"
        f"{ti['gated']} — live r4 sizing is reading today's partial bar again")
    assert ti["weights"]["r4_eth"] == pytest.approx(0.14814814814814814)


@pytest.mark.slow
@pytest.mark.parametrize("when", [
    datetime(2026, 3, 3, 20, 1, tzinfo=timezone.utc),
    datetime(2026, 3, 31, 20, 1, tzinfo=timezone.utc),
])
def test_bear_kill_switch_is_not_bypassed_by_the_partial_bar(when):
    """The safety consequence, pinned on the two real R4_ETH entry minutes
    where it happened. The partial bar read 'uncertain' (weight 0.148 — the
    bot fires); yesterday's complete close reads 'bear' (weight 0 — the kill
    switch at bots/r4/strategy/signal.py:93 refuses). The kill switch itself
    was intact in code and defeated in practice."""
    from strategies.support import jplus_inputs
    clock.set_simulated_now(when)
    jplus_inputs._invalidate_today_inputs_cache()
    try:
        ti = jplus_inputs.today_inputs()
    finally:
        clock.set_simulated_now(None)
        jplus_inputs._invalidate_today_inputs_cache()
    assert ti["mode"] == "bear", (
        f"{when:%Y-%m-%d %H:%M}: regime {ti['mode']!r}, expected 'bear' — the "
        f"bear kill switch is being bypassed")
    assert ti["weights"]["r4_eth"] == 0.0


@pytest.mark.slow
# 2026-03-03 dropped from this list: it fails here too, but the kill-switch
# arm above already pins that exact minute. 2026-04-01 stays because it is the
# one case only this arm catches — a GATE flip, which a det_i-only fix leaves
# in place and which oversized the V2 entry 2.5x.
@pytest.mark.parametrize("when", [
    datetime(2024, 11, 5, 20, 1, tzinfo=timezone.utc),
    datetime(2026, 4, 1, 4, 1, tzinfo=timezone.utc),
])
def test_today_inputs_equals_the_research_row(when):
    """The contract, rather than a value: what the live bot sizes with on day D
    must equal the row the research loop records for D.

    Stronger than the hard-coded arms above because it cannot pass by a value
    happening to sit at a regime cap. It is also exactly the premise of the
    AUDIT_2026_05_13 weights correction at jplus_inputs.py:206-225 ("at Tue
    20:00, today_inputs()'s latest complete daily close is Monday") — which
    was false in live until this fix. 2026-04-01 04:01 is the V2 entry a
    det_i-only fix would have oversized 2.5x through the gate leak.
    """
    from datetime import timedelta
    from strategies.support import jplus_inputs

    clock.set_simulated_now(when)
    jplus_inputs._invalidate_today_inputs_cache()
    try:
        live = jplus_inputs.today_inputs()
    finally:
        clock.set_simulated_now(None)

    later = when + timedelta(days=3)
    clock.set_simulated_now(later)
    jplus_inputs._invalidate_today_inputs_cache()
    try:
        out, _state = jplus_inputs._run_decision_loop()
    finally:
        clock.set_simulated_now(None)
        jplus_inputs._invalidate_today_inputs_cache()

    day = when.date().isoformat()
    assert day in out, f"research loop has no row for {day}"
    row = out[day]
    assert (live["mode"], live["gated"]) == (row["mode"], row["gated"]), (
        f"{day}: live sizes with {live['mode']}/gated={live['gated']} but the "
        f"research row says {row['mode']}/gated={row['gated']}")
    assert live["lev"] == pytest.approx(row["lev"]), (
        f"{day}: live lev {live['lev']} != research lev {row['lev']}")

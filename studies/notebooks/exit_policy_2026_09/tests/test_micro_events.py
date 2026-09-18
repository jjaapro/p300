"""Synthetic fixtures for microstructure stage 1 (PREREGISTRATION_MICROSTRUCTURE.md sections 3.1, 5, 6, 8).

A flat 1-minute market at 100 (high 100.1, low 99.9) with all features zero; each test changes only the minutes and
features that matter. Minute indices are absolute; the trade enters at I0.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import micro_data as D  # noqa: E402
import micro_lib as M  # noqa: E402

N = 4_000
I0 = 2_000
T0 = 1_700_006_400


def series(n: int = N) -> M.Series:
    return M.Series("BTC", T0, np.full(n, 100.0), np.full(n, 100.1), np.full(n, 99.9), np.full(n, 100.0),
                    np.zeros(n), np.zeros(n), np.zeros(n), np.ones(n, dtype=bool))


def bar(ser: M.Series, i: int, o: float, h: float, lo: float, c: float) -> None:
    ser.open[i], ser.high[i], ser.low[i], ser.close[i] = o, h, lo, c


# --- frozen numbers --------------------------------------------------------------------------------

def test_frozen_constants_match_the_preregistration():
    assert (M.FLOW_WINDOW, M.NORM_WINDOW, M.NORM_MIN_PRESENT, M.Z_EXTREME) == (5, 10_080, 5_040, 3.0)
    assert (M.LEVEL_LOOKBACK, M.LEVEL_MIN_PRESENT, M.LEVEL_MIN_DISTANCE) == (1_440, 1_000, 0.001)
    assert (M.BREAK_MINUTES, M.AT_LEVEL_BAND, M.BOOK_WINDOW_MIN_PRESENT) == (15, 0.0025, 3)
    assert M.BOOK_ELIGIBLE_FROM == int(datetime(2023, 1, 8, tzinfo=timezone.utc).timestamp())
    assert M.HORIZON_H == {"chento": 72, "squeeze_bull": 48, "short_squeeze": 6}
    assert [M.window_minutes(p) for p in M.POPULATIONS] == [216, 144, 18]
    assert (M.BIN_R, M.MIN_CONTROLS, M.MIN_EVENT_TRADES, M.SIGN_CONTROL_MIN) == (0.25, 3, 30, 10)
    assert (M.EQUIVALENCE_R, M.ALPHA, M.BLOCK_DAYS, M.N_BOOT, M.SEED) == (0.10, 0.05, 30, 10_000, 42)
    assert M.PRIMARY == ("E1_against", "E2_rejection", "E3_against", "E4_at_level")


# --- features --------------------------------------------------------------------------------------

def test_trailing_z_uses_only_the_lagged_window():
    rng = np.random.default_rng(1)
    x = rng.normal(size=40)
    z = M.trailing_z(x, lag=2, window=4, min_present=4)
    b = 20
    ref = x[b - 5:b - 1]                                   # b - lag - window + 1 ... b - lag
    assert z[b] == pytest.approx((x[b] - ref.mean()) / ref.std())
    y = x.copy()
    y[b - 1] += 50.0                                       # inside the lag gap: not used
    y[b - 6] += 50.0                                       # before the window: not used
    assert M.trailing_z(y, lag=2, window=4, min_present=4)[b] == pytest.approx(z[b])
    assert np.isnan(z[:5]).all() and np.isfinite(z[5])     # the first full reference window is x[0 ... 3]


def test_flow_delta_sums_five_minutes_and_price_change_spans_them():
    n = 60
    rng = np.random.default_rng(2)
    vol = rng.uniform(1, 2, n)
    tb = vol * rng.uniform(0, 1, n)
    op, cl = rng.uniform(99, 101, n), rng.uniform(99, 101, n)
    z, dp = M.flow_features(op, cl, vol, tb, window=5, norm_window=10, min_present=10)
    d = 2 * tb - vol
    Dsum = np.array([d[b - 4:b + 1].sum() if b >= 4 else np.nan for b in range(n)])
    b = 40
    ref = Dsum[b - 14:b - 4]                               # D at b - 14 ... b - 5
    assert z[b] == pytest.approx((Dsum[b] - ref.mean()) / ref.std())
    assert dp[b] == pytest.approx(cl[b] - op[b - 4])
    vol2 = vol.copy()
    vol2[b - 2] = 0.0                                      # a dead minute inside the window
    z2, _ = M.flow_features(op, cl, vol2, tb, window=5, norm_window=10, min_present=10)
    assert np.isnan(z2[b])


def test_book_imbalance_needs_three_of_five_minutes():
    n = 60
    rng = np.random.default_rng(3)
    bid, ask = rng.uniform(1, 2, n), rng.uniform(1, 2, n)
    zq = M.book_features(bid, ask, window=5, min_in_window=3, norm_window=10, min_present=10)
    q = (bid - ask) / (bid + ask)
    Q = np.array([q[b - 4:b + 1].mean() if b >= 4 else np.nan for b in range(n)])
    b = 40
    ref = Q[b - 14:b - 4]
    assert zq[b] == pytest.approx((Q[b] - ref.mean()) / ref.std())
    bid2 = bid.copy()
    bid2[b - 1] = bid2[b - 2] = np.nan                     # 3 of 5 present: still defined
    assert np.isfinite(M.book_features(bid2, ask, window=5, min_in_window=3, norm_window=10, min_present=8)[b])
    bid2[b - 3] = np.nan                                   # 2 of 5 present: missing
    assert np.isnan(M.book_features(bid2, ask, window=5, min_in_window=3, norm_window=10, min_present=8)[b])


def test_book_minute_keeps_the_last_snapshot_inside_the_minute():
    t0, n = 1_000_020, 5
    ts = np.array([t0 + 125, t0 + 61, t0 + 95, t0 + 240, t0 + 400])     # unsorted; minute 1 twice; one out of range
    bid = np.array([3.0, 1.0, 2.0, 4.0, 9.0])
    ask = bid + 10
    ob, oa, cnt = np.full(n, np.nan), np.full(n, np.nan), np.zeros(n, dtype=np.uint16)
    D.last_in_minute(ts, bid, ask, t0, n, ob, oa, cnt)
    assert np.isnan(ob[0]) and ob[1] == 2.0 and oa[1] == 12.0 and ob[2] == 3.0 and ob[4] == 4.0 and np.isnan(ob[3])
    assert cnt.tolist() == [0, 2, 1, 0, 1]


# --- walker ----------------------------------------------------------------------------------------

def test_long_stop_at_level_gap_at_open_and_stop_before_target():
    s = series()
    bar(s, I0 + 30, 100.0, 100.0, 98.5, 99.0)
    assert M.walk(s, I0, 1, 99.0, 103.0, 6) == (I0 + 30, "stop", 99.0)
    s = series()
    bar(s, I0 + 30, 98.0, 98.2, 97.8, 98.0)
    assert M.walk(s, I0, 1, 99.0, 103.0, 6) == (I0 + 30, "stop", 98.0)
    s = series()
    bar(s, I0 + 30, 100.0, 103.5, 98.5, 100.0)
    assert M.walk(s, I0, 1, 99.0, 103.0, 6) == (I0 + 30, "stop", 99.0)
    s = series()
    bar(s, I0 + 30, 100.0, 103.5, 99.5, 103.0)
    assert M.walk(s, I0, 1, 99.0, 103.0, 6) == (I0 + 30, "target", 103.0)


def test_time_exit_skips_missing_minutes_and_ignores_touches_at_the_horizon():
    s = series()
    ih = I0 + 6 * 60
    bar(s, ih, np.nan, np.nan, np.nan, np.nan)
    bar(s, ih + 1, 100.4, 104.0, 98.0, 100.5)             # opens at the time exit; its touches do not count
    assert M.walk(s, I0, 1, 99.0, 103.0, 6) == (ih + 1, "time", 100.4)


def test_short_mirrors_stop_and_target():
    s = series()
    bar(s, I0 + 10, 100.0, 101.5, 99.5, 101.0)
    assert M.walk(s, I0, -1, 101.0, 97.0, 6) == (I0 + 10, "stop", 101.0)
    s = series()
    bar(s, I0 + 10, 100.0, 100.2, 96.5, 97.0)
    assert M.walk(s, I0, -1, 101.0, 97.0, 6) == (I0 + 10, "target", 97.0)


# --- events ----------------------------------------------------------------------------------------

def first(ser, kind, s=1, x=I0 + 300, level=None):
    return M.first_true(M.window_masks(ser, I0, x, s, level)[kind])


def test_absorption_against_and_supportive_signs():
    s = series()
    s.z_flow[I0 + 10], s.dp5[I0 + 10] = 3.2, 0.0          # buying, price did not rise
    s.z_flow[I0 + 20], s.dp5[I0 + 20] = 3.2, 0.3          # buying that moved price: not absorption
    s.z_flow[I0 + 30], s.dp5[I0 + 30] = -3.0, 0.1         # selling that did not push price down
    assert first(s, "E1_against", 1) == 10
    assert first(s, "E1_supportive", 1) == 30
    assert first(s, "E1_against", -1) == 30               # for a short, selling absorbed is against
    assert first(s, "E1_supportive", -1) == 10


def test_window_must_lie_inside_the_trade_and_before_the_exit():
    s = series()
    s.z_flow[I0 + 3], s.dp5[I0 + 3] = 4.0, -0.1           # window b-4 starts before entry
    assert first(s, "E1_against") == -1
    s.z_flow[I0 + 4], s.dp5[I0 + 4] = 4.0, -0.1
    assert first(s, "E1_against") == 4
    s = series()
    s.z_flow[I0 + 50], s.dp5[I0 + 50] = 4.0, -0.1
    assert first(s, "E1_against", x=I0 + 51) == 50        # open after minute 50
    assert first(s, "E1_against", x=I0 + 50) == -1        # exits in minute 50
    s.close[I0 + 50] = np.nan
    assert first(s, "E1_against", x=I0 + 51) == -1        # no mark, no event


def test_book_tilt_signs():
    s = series()
    s.zq[I0 + 12] = -3.0
    s.zq[I0 + 40] = 3.1
    assert first(s, "E3_against", 1) == 12 and first(s, "E3_supportive", 1) == 40
    assert first(s, "E3_against", -1) == 40 and first(s, "E3_supportive", -1) == 12


def test_level_is_the_prior_24h_extreme_and_must_be_beyond_entry():
    s = series()
    s.high[I0 - 1440] = 100.5
    s.high[I0] = 120.0                                    # the entry minute itself is not before entry
    s.high[I0 - 1441] = 130.0                             # older than 24 h
    assert M.level_of(s, I0, 1) == 100.5
    assert M.level_valid(100.11, 100.0, 1) and not M.level_valid(100.09, 100.0, 1)
    assert not M.level_valid(99.0, 100.0, 1)                                  # below entry is not overhead
    s.low[I0 - 5] = 99.0
    assert M.level_of(s, I0, -1) == 99.0 and M.level_valid(99.89, 100.0, -1) and not M.level_valid(99.91, 100.0, -1)
    s.high[I0 - 1440:I0 - 439] = np.nan                   # 1,001 missing: 439 present
    assert np.isnan(M.level_of(s, I0, 1))


def test_rejection_at_the_wick_minute_or_the_first_close_back_inside():
    s = series()
    bar(s, I0 + 20, 100.0, 101.2, 99.9, 100.8)            # break of 101, close back below in the same minute
    assert M.break_events(s, I0, I0 + 300, 1, 101.0) == (I0 + 20, -1)
    s = series()
    for k in range(3):
        bar(s, I0 + 20 + k, 101.0, 101.5, 100.9, 101.2)
    bar(s, I0 + 23, 101.2, 101.3, 100.5, 100.9)
    assert M.break_events(s, I0, I0 + 300, 1, 101.0) == (I0 + 23, -1)


def test_acceptance_after_fifteen_closes_beyond_and_nothing_when_the_trade_exits_first():
    s = series()
    for k in range(15):
        bar(s, I0 + 20 + k, 101.0, 101.5, 100.9, 101.0 if k == 3 else 101.2)   # a close AT the level is not back inside
    assert M.break_events(s, I0, I0 + 300, 1, 101.0) == (-1, I0 + 34)
    assert M.break_events(s, I0, I0 + 30, 1, 101.0) == (-1, -1)
    s.close[I0 + 34] = np.nan                             # no mark at the acceptance minute
    assert M.break_events(s, I0, I0 + 300, 1, 101.0) == (-1, -1)


def test_only_the_first_break_counts_and_short_mirrors():
    s = series()
    for k in range(15):
        bar(s, I0 + 20 + k, 101.0, 101.5, 100.9, 101.2)
    bar(s, I0 + 60, 101.0, 101.8, 100.0, 100.5)           # a later failed break is not an event
    assert M.break_events(s, I0, I0 + 300, 1, 101.0) == (-1, I0 + 34)
    s = series()
    bar(s, I0 + 7, 100.0, 100.0, 98.8, 99.2)
    assert M.break_events(s, I0, I0 + 300, -1, 99.0) == (I0 + 7, -1)


def test_absorption_at_the_level_needs_the_band_and_a_valid_level():
    s = series()
    s.z_flow[I0 + 10], s.dp5[I0 + 10] = 3.5, -0.1
    s.close[I0 + 10] = 100.0
    assert first(s, "E4_at_level", level=100.2) == 10     # 0.2 % away
    assert first(s, "E4_at_level", level=100.3) == -1     # 0.3 % away
    assert first(s, "E4_at_level", level=None) == -1


def test_ineligible_trades_have_no_events():
    s = series()
    s.zq[I0 + 12] = -3.5
    tr = pd.DataFrame([{"tid": "a", "asset": "BTC", "s": 1, "i0": I0, "x": I0 + 100, "entry_ts": M.BOOK_ELIGIBLE_FROM - 60,
                        "level": np.nan, "level_valid": False}])
    ev = M.events_population(tr, {"BTC": s})
    assert ev["E3_against_first"].iat[0] == -1 and ev["E2_rejection_first"].iat[0] == -1


# --- continuation value and placebo --------------------------------------------------------------------

def make_trades(specs):
    rows = []
    for k, sp in enumerate(specs):
        rows.append({"tid": f"t{k}", "asset": "BTC", "direction": "long" if sp.get("s", 1) > 0 else "short",
                     "s": sp.get("s", 1), "i0": sp["i0"], "x": sp["i0"] + sp["len"], "entry_ts": T0 + 60 * sp["i0"],
                     "entry_day": "2024-01-01", "level_valid": True, "E1_against_first": sp.get("first", -1)})
    return pd.DataFrame(rows)


def grids_from(marks: list[np.ndarray], cvs: list[np.ndarray]) -> M.Grids:
    T, E = len(marks), max(len(m) for m in marks)
    open_ = np.zeros((T, E), dtype=bool)
    mark, cv = np.full((T, E), np.nan), np.full((T, E), np.nan)
    for r, (m, c) in enumerate(zip(marks, cvs)):
        open_[r, :len(m)], mark[r, :len(m)], cv[r, :len(c)] = True, m, c
    bin_ = np.where(open_, np.floor(np.nan_to_num(mark) / M.BIN_R), -(10 ** 9)).astype(np.int64)
    return M.Grids(open_, bin_, mark, cv, cv.copy())


def test_placebo_exclusions_and_per_control_averaging():
    L = 40
    specs = [{"i0": 0, "len": L, "first": 0 + 10},        # event trade: elapsed 10
             {"i0": 100, "len": L},                       # good control
             {"i0": 200, "len": L},                       # good control, two matching minutes
             {"i0": 300, "len": L},                       # good control
             {"i0": 20, "len": L},                        # overlaps the event trade in calendar time
             {"i0": 400, "len": L, "s": -1},              # other direction
             {"i0": 500, "len": L, "first": 500 + 9},     # its own event at elapsed 9: at risk only before it
             {"i0": 600, "len": L}]                       # every mark in another bin
    tr = make_trades(specs)
    base = np.full(L, 0.1)
    marks = [base.copy() for _ in specs]
    cvs = [np.zeros(L) for _ in specs]
    marks[0][10], cvs[0][10] = 0.1, -0.5
    for r in (1, 2, 3, 4, 5, 6):
        marks[r][:] = 5.0                                 # far bin everywhere ...
    marks[1][10], cvs[1][10] = 0.2, 1.0                   # ... except the matching minutes
    marks[2][9], cvs[2][9] = 0.15, 0.0
    marks[2][11], cvs[2][11] = 0.2, 2.0
    marks[3][12], cvs[3][12] = 0.0, 3.0
    marks[3][13], cvs[3][13] = 0.1, 3.0                   # outside the window of 2
    marks[4][10], cvs[4][10] = 0.1, 99.0
    marks[5][10], cvs[5][10] = 0.1, 99.0
    marks[6][9], cvs[6][9] = 0.1, 99.0                    # at elapsed 9 its event has happened
    marks[7][:] = 5.0
    out = M.match(tr, grids_from(marks, cvs), "E1_against", window=2).set_index("tid")
    row = out.loc["t0"]
    assert row["controls"] == 3 and row["bin"] == 0 and row["elapsed_min"] == 10
    assert row["cv"] == -0.5
    assert row["placebo"] == pytest.approx((1.0 + (0.0 + 2.0) / 2 + 3.0) / 3)
    # time-only ignores bins: t6 joins through elapsed 8 (before its event) and t7 through minutes 8-12
    assert row["controls_time_only"] == 5


def test_placebo_needs_three_controls():
    L = 20
    tr = make_trades([{"i0": 0, "len": L, "first": 5}, {"i0": 100, "len": L}, {"i0": 200, "len": L}])
    g = grids_from([np.full(L, 0.1)] * 3, [np.zeros(L)] * 3)
    row = M.match(tr, g, "E1_against", window=3).iloc[0]
    assert row["controls"] == 2 and np.isnan(row["placebo"])


def test_grids_mark_bin_and_continuation_value():
    s = series()
    s.close[I0:I0 + 3] = [100.0, 99.9, 101.0]
    tr = pd.DataFrame([{"tid": "a", "asset": "BTC", "s": 1, "i0": I0, "x": I0 + 3, "entry": 100.0, "risk": 2.0,
                        "exit_price": 102.0, "exit_price_notime": 96.0}])
    g = M.build_grids(tr, {"BTC": s}, with_cv=True)
    assert g.mark[0, :3] == pytest.approx([0.0, -0.05, 0.5])
    assert g.bin_[0, :3].tolist() == [0, -1, 2]
    assert g.cv[0, :3] == pytest.approx([1.0, 1.05, 0.5]) and g.cv_notime[0, 2] == pytest.approx(-2.5)


# --- statistics ------------------------------------------------------------------------------------

def test_one_sided_p_and_holm():
    boot = np.array([-0.4, -0.3, -0.2, -0.2, -0.1])
    # centred draws boot - (-0.24) = [-0.16, -0.06, 0.04, 0.04, 0.14]; at or below -0.24: none
    assert M.p_less(-0.24, boot) == pytest.approx(1 / 6)
    assert M.p_less(0.3, boot + 0.54) == pytest.approx(6 / 6)
    adj = M.holm_adjust({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj == pytest.approx({"a": 0.03, "c": 0.06, "b": 0.06})


def test_summarize_gives_the_extra_trade_to_the_earlier_half():
    rows = pd.DataFrame({"entry_ts": [3, 1, 2], "entry_day": ["2024-01-03", "2024-01-01", "2024-01-02"],
                         "delta": [3.0, 1.0, 2.0]})
    axis = M.day_axis(rows["entry_day"])
    r = M.summarize(rows, "delta", axis, M.block_indices(len(axis), n_boot=50))
    assert r["n"] == 3 and r["first_half_n"] == 2 and r["first_half"] == pytest.approx(1.5) and r["second_half"] == 3.0


def test_classification_rules():
    neg = {"n": 40, "mean": -0.3, "ci95": [-0.5, -0.1], "first_half": -0.2, "second_half": -0.4}
    assert M.classify(neg, True, 0.01, {"n": 25, "mean": 0.1}, "E1_against") == "INFORMATIVE"
    assert M.classify(neg, True, 0.01, {"n": 25, "mean": -0.5}, "E1_against") == "UNDETERMINED"
    assert M.classify(neg, True, 0.01, {"n": 5, "mean": 0.1}, "E1_against") == "INFORMATIVE, sign control unavailable"
    assert M.classify(neg, True, 0.01, None, "E4_at_level") == "INFORMATIVE"
    assert M.classify(neg, True, 0.20, None, "E4_at_level") == "UNDETERMINED"
    assert M.classify({**neg, "second_half": 0.1}, True, 0.01, None, "E4_at_level") == "UNDETERMINED"
    assert M.classify({**neg, "ci95": [-0.09, 0.08]}, True, 0.5, None, "E4_at_level") == "NO INFORMATION >= 0.10 R"
    assert M.classify({**neg, "ci95": [0.02, 0.5]}, True, 0.9, None, "E4_at_level") == "CONTRARY"
    assert M.classify(neg, False, None, None, "E4_at_level") == "DESCRIPTIVE"

"""Fixtures for the spot-vs-perp events, placebos and labels (PREREGISTRATION_SPOT_PERP.md sections 5, 6, 8, P9).

A flat synthetic market with every input zero; each test changes only the minutes and inputs that matter. Every
boundary asserts both the included and the excluded twin, so a fixture cannot freeze the wrong side of a rule.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import anatomy_lib as A  # noqa: E402
import micro_lib as M  # noqa: E402
import spotperp_lib as L  # noqa: E402

N = 400
T0 = 1_640_995_200          # 2022-01-03 00:00 UTC, a Monday
GATE = L.GATE_MIN           # 60


def panel(n: int = N, **over) -> L.Panel:
    """A flat market: price 100 everywhere, every input zero, every window finite."""
    flat = lambda v=0.0: np.full(n, float(v))  # noqa: E731
    ser = M.Series("BTC", T0, flat(100.0), flat(100.1), flat(99.9), flat(100.0), flat(), flat(), flat(),
                   np.ones(n, dtype=bool))
    p = L.Panel(asset="BTC", t0_s=T0, n=n, ser=ser, dP=flat(), DP60=flat(), dS=flat(), DS60=flat(), zS=flat(),
                prem=flat(), dprem=flat(), pt=flat(), vr=flat(1.0),
                session=np.zeros(n, dtype=np.int8), weekend=np.zeros(n, dtype=bool),
                oi=flat(), doi=flat(), ot=flat(), DSplus60=flat())
    for k, v in over.items():
        setattr(p, k, v)
    return p


def trades(rows: list[dict]) -> pd.DataFrame:
    out = []
    for i, r in enumerate(rows):
        s = r.get("s", 1)
        entry, risk = 100.0, 1.0
        out.append({"pop": "chento", "tid": r.get("tid", f"T{i}"), "asset": "BTC",
                    "direction": "long" if s > 0 else "short", "s": s,
                    "entry_ts": r.get("entry_ts", T0 + 60 * r.get("i0", 0) + r.get("day_offset", 0) * 86400),
                    "entry": entry, "risk": risk, "stop": entry - s * risk, "target": entry + s * 6 * risk,
                    "i0": r.get("i0", 0), "x": r["x"], "exit_price": r.get("exit_price", 100.0),
                    "exit_price_notime": r.get("exit_price", 100.0), "horizon_min": r.get("horizon", N)})
    df = pd.DataFrame(out)
    df["entry_day"] = [M.utc_day(t) for t in df["entry_ts"]]
    return df


def events_of(p: L.Panel, tr: pd.DataFrame, with_cv: bool = False):
    g = L.build_grids(tr, {"BTC": p}, with_cv=with_cv)
    return L.first_events(tr, g), g


def first_of(kind: str, p: L.Panel, tr: pd.DataFrame) -> int:
    ev, _ = events_of(p, tr)
    return int(ev[f"{kind}_first"].iat[0])


def rising(n: int, at: list[int]) -> np.ndarray:
    """Highs that set a new running maximum exactly at the given minutes."""
    h = np.full(n, 100.0)
    for j, m in enumerate(at):
        h[m:] = 100.0 + 0.1 * (j + 1)
    return h


# --- frozen constants -------------------------------------------------------------------------------

def test_frozen_constants_match_the_preregistration():
    assert (L.GATE_MIN, L.FLOW_PRESENT, L.FRESH_MAX) == (60, 50, 60)
    assert (L.GATE_MIN, L.FLOW_PRESENT) == (A.FLOW_MIN, A.FLOW_MIN_PRESENT)
    assert L.FRESH_MAX == A.STALL_EDGES[1]
    assert L.ERA_DAYS == 365 and L.OI_LAG_S == A.METRIC_LAG_S == 300
    assert (L.Z_EXTREME, L.MIN_CONTROLS, L.MIN_EVENT_TRADES, L.MIN_LINE_TRADES) == (3.0, 3, 30, 10)
    assert (L.F3_PT, L.F3_OT, L.F3_VR) == (0.0012, 0.0008, 1.35)
    assert L.IN_PROFIT_R == 1.0 and A.SPIKE == 0.02          # +1 R of squeeze_bull's own R
    assert L.FAMILY_CANDIDATES == ("F1_against", "F2_perp_led")
    assert L.DECISION_CONTROL == {"F1_against": "F1_spot_confirmed", "F2_perp_led": "F2_spot_confirmed"}


def test_sessions_are_read_from_the_bot_config_text():
    assert L.SESSION_EDGES == {"asia": (0, 7), "london": (7, 14), "ny": (14, 21)}
    assert L.SESSION_NAMES == ("asia", "london", "ny", "remainder")
    assert list(L.session_bucket(np.array([0, 6, 7, 13, 14, 20, 21, 23]))) == [0, 0, 1, 1, 2, 2, 3, 3]


# --- running extremes -------------------------------------------------------------------------------

def test_running_extreme_is_strict_and_never_the_first_minute():
    x = L.running_extreme(np.array([100.0, 100.0, 101.0, 101.0, 102.0]))
    assert list(x) == [False, False, True, False, True]          # entry minute excluded, a tie is not an extreme


def test_running_extreme_ignores_missing_minutes():
    x = L.running_extreme(np.array([100.0, np.nan, 101.0, np.nan, 100.5]))
    assert list(x) == [False, False, True, False, False]         # a NaN bar is never an extreme


def test_stall_counts_minutes_since_the_last_extreme_and_is_nan_before_the_first():
    stall = L.stall_from_extreme(np.array([False, False, True, False, False, True]))
    assert np.isnan(stall[0]) and np.isnan(stall[1])
    assert list(stall[2:]) == [0.0, 1.0, 2.0, 0.0]


def test_a_short_takes_its_extremes_on_the_lows():
    p = panel()
    p.ser.low[:] = 100.0
    p.ser.low[GATE + 5:] = 99.0
    tr = trades([{"x": N, "s": -1}])
    _, g = events_of(p, tr)
    assert g.X[0, GATE + 5] and not g.X[0, GATE + 4]


# --- F1 ---------------------------------------------------------------------------------------------

def _f1_panel(minute: int, dprem: float, dp: float, ds: float, n: int = N) -> L.Panel:
    p = panel(n)
    p.ser.high[:] = 100.0
    p.ser.high[minute:] = 101.0                                  # a new running extreme at `minute`
    p.dprem[minute], p.DP60[minute], p.DS60[minute] = dprem, dp, ds
    return p


@pytest.mark.parametrize("dprem,dp,ds,kind", [
    (1.0, 1.0, -1.0, "F1_against"),
    (1.0, 1.0, 0.0, "F1_against"),                               # the spot leg is inclusive at zero
    (1.0, 1.0, 1.0, "F1_spot_confirmed"),
    (-1.0, -1.0, 1.0, "F1_spot_led"),
])
def test_f1_legs_pick_exactly_one_kind_for_a_long(dprem, dp, ds, kind):
    m = GATE + 3
    p = _f1_panel(m, dprem, dp, ds)
    tr = trades([{"x": N}])
    ev, _ = events_of(p, tr)
    fired = [k for k in ("F1_against", "F1_spot_confirmed", "F1_spot_led") if ev[f"{k}_first"].iat[0] == m]
    assert fired == [kind]


def test_f1_mirrors_every_sign_for_a_short():
    m = GATE + 3
    p = panel()
    p.ser.low[:] = 100.0
    p.ser.low[m:] = 99.0
    p.dprem[m], p.DP60[m], p.DS60[m] = -1.0, -1.0, 1.0           # s = -1: every leg flips
    tr = trades([{"x": N, "s": -1}])
    assert first_of("F1_against", p, tr) == m


def test_a_premium_change_of_exactly_zero_is_neither_event_nor_control():
    m = GATE + 3
    p = _f1_panel(m, 0.0, 1.0, -1.0)
    tr = trades([{"x": N}])
    ev, _ = events_of(p, tr)
    assert ev["F1_against_first"].iat[0] == -1 and ev["F1_spot_confirmed_first"].iat[0] == -1


def test_a_minute_is_never_both_the_event_and_its_decision_control():
    m = GATE + 3
    for ds in (-1.0, 1.0):
        p = _f1_panel(m, 1.0, 1.0, ds)
        tr = trades([{"x": N}])
        ev, _ = events_of(p, tr)
        assert (ev["F1_against_first"].iat[0] == m) != (ev["F1_spot_confirmed_first"].iat[0] == m)


def test_the_shared_gate_excludes_a_perfect_minute_before_sixty_and_admits_it_at_sixty():
    assert first_of("F1_against", _f1_panel(GATE - 1, 1.0, 1.0, -1.0), trades([{"x": N}])) == -1
    assert first_of("F1_against", _f1_panel(GATE, 1.0, 1.0, -1.0), trades([{"x": N}])) == GATE


def test_only_the_first_event_of_a_kind_is_used():
    p = panel()
    p.ser.high[:] = 100.0
    for j, m in enumerate((GATE + 2, GATE + 9)):
        p.ser.high[m:] = 100.0 + 0.1 * (j + 1)
        p.dprem[m], p.DP60[m], p.DS60[m] = 1.0, 1.0, -1.0
    tr = trades([{"x": N}])
    ev, _ = events_of(p, tr)
    assert ev["F1_against_first"].iat[0] == GATE + 2 and ev["F1_against_minutes"].iat[0] == 2


def test_a_missing_input_gives_no_event_of_that_family_but_leaves_the_other_family_alone():
    m = GATE + 3
    p = _f1_panel(m, 1.0, 1.0, -1.0)
    p.zP[m], p.zS[m] = 4.0, -1.0                                  # F2 would fire at the same minute
    p.dprem[m] = np.nan                                           # the F1 family's input is missing
    tr = trades([{"x": N}])
    ev, _ = events_of(p, tr)
    assert ev["F1_against_first"].iat[0] == -1
    assert ev["F2_perp_led_first"].iat[0] == m


def test_f1_in_profit_gates_at_exactly_one_r():
    m = GATE + 3
    for close, fires in ((101.0, True), (100.999, False)):
        p = _f1_panel(m, 1.0, 1.0, -1.0)
        p.ser.close[m] = close                                    # mark = (close - 100) / 1 R
        tr = trades([{"x": N}])
        assert (first_of("F1_in_profit", p, tr) == m) is fires


# --- F2 ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("zp,zs,fires", [(3.0, 0.0, True), (3.0, 0.01, False), (2.99, 0.0, False), (4.0, -1.0, True)])
def test_f2_boundaries_are_inclusive_where_the_document_says_so(zp, zs, fires):
    m = GATE + 3
    p = panel()
    p.ser.high[:] = 100.0
    p.ser.high[m:] = 101.0
    p.zP[m], p.zS[m] = zp, zs
    tr = trades([{"x": N}])
    assert (first_of("F2_perp_led", p, tr) == m) is fires


def test_the_f2_mirror_fires_on_an_extreme_against_the_trade():
    m = GATE + 3
    p = panel()
    p.ser.low[:] = 100.0
    p.ser.low[m:] = 99.0                                          # a new running low inside a long
    p.zP[m], p.zS[m] = -4.0, 1.0                                  # the perp-led pattern with every sign flipped
    tr = trades([{"x": N}])
    ev, _ = events_of(p, tr)
    assert ev["F2_mirror_first"].iat[0] == m and ev["F2_perp_led_first"].iat[0] == -1


# --- composition and open interest ------------------------------------------------------------------

def test_the_composition_sum_is_missing_only_when_a_venue_has_no_row():
    n = 200
    dS = np.zeros(n)
    fd = {"volume": np.full(n, 2.0), "taker_buy_volume": np.full(n, 1.0), "open": np.full(n, 100.0)}
    fd["volume"][10] = 0.0                                        # carried but dead: contributes zero
    fd["open"][11] = np.nan                                       # no archive row: the sum is missing
    fd["volume"][11] = np.nan
    raw = {"t0_s": T0, "close": np.full(n, 100.0), "open": np.full(n, 100.0), "high": np.full(n, 100.1),
           "low": np.full(n, 99.9), "volume": np.full(n, 2.0), "taker_buy_volume": np.full(n, 1.0),
           "bid": np.full(n, 1.0), "ask": np.full(n, 1.0), "book_t0_s": T0}
    spot = {"open": np.full(n, 100.0), "close": np.full(n, 100.0), "volume": np.full(n, 2.0),
            "taker_buy_volume": np.full(n, 1.0)}
    p = L.build_panel("BTC", raw, spot, np.zeros(n), fdusd=fd)
    d = L.minute_delta(fd["volume"], fd["taker_buy_volume"])
    assert np.isnan(d[10]) and np.isnan(d[11])                    # the raw FDUSD delta is missing for both
    assert p.dSplus[10] == 0.0                                    # carried but dead: contributes zero
    assert np.isnan(p.dSplus[11])                                 # no archive row: the sum is missing
    assert p.dSplus[9] == 0.0 and dS.sum() == 0.0


def test_open_interest_uses_a_snapshot_only_after_its_publication_lag():
    oi = np.arange(20, dtype=float)
    out = L.oi_minutes(oi, T0, T0, 60)
    # the minute closing at T0 + 60 s can use the slot stamped T0 - 300 s at the earliest, so nothing yet
    assert np.isnan(out[0])
    assert out[5] == 0.0                                          # the minute closing at T0 + 360 s uses slot 0
    assert out[10] == 1.0


# --- placebos ---------------------------------------------------------------------------------------

SPAN = 300                                                        # each trade occupies its own 300-minute window


def _pair(event_minute: int, control_minutes: list[int], control_extreme: bool = True,
          control_has_prior_event: bool = False, entry_ts: list[int] | None = None
          ) -> tuple[pd.DataFrame, L.Grids, L.Panel]:
    """One event trade and three control trades, all long, on windows that do not overlap in calendar time."""
    n = (SPAN + 10) * 5
    p = panel(n)
    p.ser.high[:] = 100.0
    p.ser.high[event_minute:] = 101.0
    p.dprem[event_minute], p.DP60[event_minute], p.DS60[event_minute] = 1.0, 1.0, -1.0
    rows = [{"i0": 0, "x": SPAN, "tid": "E"}]
    for c in range(3):                                            # a gap so the intervals do not even touch
        start = (SPAN + 10) * (c + 1)
        rows.append({"i0": start, "x": start + SPAN, "tid": f"C{c}"})
    if entry_ts is not None:
        for row, ts in zip(rows, entry_ts):
            row["entry_ts"] = ts
    tr = trades(rows)
    g = L.build_grids(tr, {"BTC": p}, with_cv=True)
    for r in range(1, 4):                                         # shape the controls by hand
        g.X[r, :] = False
        g.kind["F1_against"][r, :] = False
        g.stall[r, :] = 99.0
        g.kind["F1_any_extreme"][r, :] = False
        for m in control_minutes:
            g.X[r, m] = control_extreme
            g.kind["F1_any_extreme"][r, m] = control_extreme and g.gate[r, m] and g.open_[r, m]
            g.stall[r, m] = 0.0 if control_extreme else 99.0
        if control_has_prior_event:
            g.kind["F1_against"][r, control_minutes[0] - 1] = True
    ev = L.first_events(tr, g)
    return ev, g, p


def _controls(ev, g, spec, kind="F1_against") -> int:
    rows = L.match(ev, g, kind, 216, spec)
    return int(rows["controls"].iat[0]) if len(rows) else 0


def test_a_control_minute_that_is_not_an_extreme_is_excluded_under_the_rungs_and_kept_under_s1():
    e = GATE + 5
    ev, g, _ = _pair(e, [e], control_extreme=False)
    assert _controls(ev, g, L.RUNGS[0]) == 0
    assert _controls(ev, g, L.SECONDARY[0]) == 3                  # S1 drops the extreme match


def test_an_extreme_control_minute_is_kept_under_rung_one():
    e = GATE + 5
    ev, g, _ = _pair(e, [e])
    assert _controls(ev, g, L.RUNGS[0]) == 3


def test_rung_two_keeps_a_control_at_stall_fifty_nine_and_drops_it_at_sixty():
    e = GATE + 5
    for stall, kept in ((59.0, 3), (60.0, 0)):
        ev, g, _ = _pair(e, [e], control_extreme=False)
        for r in range(1, 4):
            g.stall[r, e] = stall
        assert _controls(ev, g, L.RUNGS[1]) == kept


def test_the_gate_applies_to_control_minutes_too():
    """A control minute before the kind's window fits is excluded even when every input is finite."""
    e = GATE + 2
    for minute, kept in ((GATE - 1, 0), (GATE + 1, 3)):
        ev, g, _ = _pair(e, [minute])
        for r in range(1, 4):
            g.ok["f1"][r, :] = False                              # the only candidate minute is `minute`
            g.ok["f1"][r, minute] = True
            g.known["f1"][r, :] = True
        assert _controls(ev, g, L.RUNGS[0]) == kept


def test_a_control_with_a_prior_event_is_excluded_from_the_level_pool_and_kept_in_the_shared_pool():
    e = GATE + 5
    ev, g, _ = _pair(e, [e], control_has_prior_event=True)
    assert _controls(ev, g, L.RUNGS[0]) == 0
    assert _controls(ev, g, L.shared_spec(L.RUNGS[0])) == 3


def test_an_unknown_status_control_is_excluded_only_when_the_gap_is_at_or_after_the_gate():
    e = GATE + 5
    for minute, kept in ((GATE + 1, 0), (GATE - 10, 3)):
        ev, g, _ = _pair(e, [e])
        for r in range(1, 4):
            g.X[r, minute] = True
            g.ok["f1"][r, minute] = False                         # an extreme whose inputs are missing
            g.known["f1"][r, :] = ~np.maximum.accumulate(g.X[r] & g.gate[r] & ~g.ok["f1"][r])
        assert _controls(ev, g, L.RUNGS[0]) == kept


def test_the_era_window_is_inclusive_at_three_hundred_and_sixty_five_days():
    e = GATE + 5
    for offset, kept in ((364, 3), (366, 0)):
        stamps = [T0] + [T0 + offset * 86400 - c * 3600 for c in range(3)]
        ev, g, _ = _pair(e, [e], entry_ts=stamps)
        assert _controls(ev, g, L.RUNGS[0]) == kept
        assert _controls(ev, g, L.RUNGS[2]) == 3                  # rung 3 pools all years


def test_r_vol_keeps_only_controls_on_the_same_side_of_one():
    e = GATE + 5
    ev, g, _ = _pair(e, [e])
    g.vr[0, e] = 1.5
    for r in range(1, 4):
        g.vr[r, e] = 1.2
    assert _controls(ev, g, L.robustness_specs(L.RUNGS[0])[0]) == 3
    for r in range(1, 4):
        g.vr[r, e] = 0.8
    assert _controls(ev, g, L.robustness_specs(L.RUNGS[0])[0]) == 0


def test_r_session_keeps_only_controls_in_the_same_bucket_and_day_type():
    e = GATE + 5
    ev, g, _ = _pair(e, [e])
    assert _controls(ev, g, L.robustness_specs(L.RUNGS[0])[1]) == 3
    for r in range(1, 4):
        g.session[r, e] = 2
    assert _controls(ev, g, L.robustness_specs(L.RUNGS[0])[1]) == 0
    for r in range(1, 4):
        g.session[r, e] = 0
        g.weekend[r, e] = True
    assert _controls(ev, g, L.robustness_specs(L.RUNGS[0])[1]) == 0


def test_f1_any_extreme_has_no_controls_under_rung_one_by_construction():
    e = GATE + 5
    ev, g, _ = _pair(e, [e])
    assert _controls(ev, g, L.RUNGS[0], kind="F1_any_extreme") == 0
    assert _controls(ev, g, L.SECONDARY[0], kind="F1_any_extreme") == 3


def test_fewer_than_three_controls_drops_the_event_trade():
    e = GATE + 5
    ev, g, _ = _pair(e, [e])
    for r in (2, 3):
        g.X[r, e] = False
    rows = L.match(ev, g, "F1_against", 216, L.RUNGS[0])
    assert rows["controls"].iat[0] == 1 and not np.isfinite(rows["placebo"].iat[0])
    assert L.included(rows) == 0


# --- labels -----------------------------------------------------------------------------------------

def _line(mean: float, n: int = 40, halves=(-0.2, -0.2)) -> dict:
    return {"n": n, "mean": mean, "ci95": [mean - 0.5, mean + 0.5],
            "first_half": halves[0], "second_half": halves[1]}


def test_informative_needs_every_condition():
    good = dict(test=_line(-0.4), in_family=True, holm_p=0.01, control=_line(-0.1),
                shared_test=_line(-0.4), shared_control=_line(-0.1), rvol=_line(-0.3), rsession=_line(-0.3))
    assert L.classify(**good) == "INFORMATIVE"
    assert L.classify(**{**good, "holm_p": 0.2}) == "UNDETERMINED"
    assert L.classify(**{**good, "test": _line(-0.4, halves=(-0.2, 0.1))}) == "UNDETERMINED"
    assert L.classify(**{**good, "rvol": _line(0.3)}) == "UNDETERMINED"
    assert L.classify(**{**good, "shared_control": _line(-0.9)}) == "UNDETERMINED"
    assert L.classify(**{**good, "in_family": False}) == "DESCRIPTIVE"


def test_a_thin_control_or_robustness_line_never_promotes():
    good = dict(test=_line(-0.4), in_family=True, holm_p=0.01, control=_line(-0.1),
                shared_test=_line(-0.4), shared_control=_line(-0.1), rvol=_line(-0.3), rsession=_line(-0.3))
    assert L.classify(**{**good, "control": _line(-0.1, n=9)}) == "INFORMATIVE, sign control unavailable"
    assert L.classify(**{**good, "rsession": _line(-0.3, n=9)}) == "INFORMATIVE, robustness unavailable"
    for label in ("INFORMATIVE, sign control unavailable", "INFORMATIVE, robustness unavailable"):
        assert label != "INFORMATIVE"                             # only the exact string may promote


def test_the_other_labels_follow_stage_one():
    assert L.classify(test=_line(0.0) | {"ci95": [-0.05, 0.05]}, in_family=True, holm_p=0.9, control=None,
                      shared_test=None, shared_control=None, rvol=None, rsession=None) == "NO INFORMATION >= 0.10 R"
    assert L.classify(test=_line(0.6) | {"ci95": [0.2, 1.0]}, in_family=True, holm_p=0.9, control=None,
                      shared_test=None, shared_control=None, rvol=None, rsession=None) == "CONTRARY"

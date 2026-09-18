"""Fixtures for stage R (PREREGISTRATION_RANGE_WICK.md sections 5, 6, 8, P5).

A flat synthetic perpetual market (price 100, volume 1) starting 2022-09-01, with a DVOL row of 50 on every day;
each test changes only the minutes and rows that matter. Every boundary asserts both the included and the excluded
twin, so a fixture cannot freeze the wrong side of a rule.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import micro_lib as M  # noqa: E402
import rangewick_lib as L  # noqa: E402

T0 = 1_661_990_400          # 2022-09-01 00:00 UTC, a Thursday; DVOL_FROM (2022-09-08) is day 7 of this grid
DAY = 1440
N_DAYS = 40
N = N_DAYS * DAY
IMP50 = 0.5 / math.sqrt(365)


def day_str(k: int) -> str:
    return M.utc_day(T0 + k * 86400)


def raw(n: int = N, price: float = 100.0) -> dict:
    return {"open": np.full(n, price), "high": np.full(n, price + 0.1), "low": np.full(n, price - 0.1),
            "close": np.full(n, price), "volume": np.ones(n), "taker_buy_volume": np.full(n, 0.5),
            "t0_s": T0, "book_t0_s": T0, "bid": np.full(n, np.nan), "ask": np.full(n, np.nan)}


def dvol_df(closes: dict) -> pd.DataFrame:
    rows = [{"asset": "BTC", "day": d, "open": c, "high": c, "low": c, "close": c} for d, c in closes.items()]
    return pd.DataFrame(rows, columns=["asset", "day", "open", "high", "low", "close"])


DEFAULT_DVOL = {day_str(k): 50.0 for k in range(N_DAYS)}


def panel(r: dict | None = None, dv: pd.DataFrame | None = None) -> L.Panel:
    return L.build_panel("BTC", r if r is not None else raw(), dv if dv is not None else dvol_df(DEFAULT_DVOL))


def alternate_day_closes(r: dict, pct: float = 0.01) -> np.ndarray:
    """The last minute of each day closes at 100 (1 +/- pct), alternating: a realised daily vol of about 2 pct."""
    C = np.array([100.0 * (1 + pct * (-1) ** k) for k in range(N_DAYS)])
    for k in range(N_DAYS):
        r["close"][(k + 1) * DAY - 1] = C[k]
    return C


def rv_of(C: np.ndarray, k: int) -> float:
    lr = np.log(C[1:] / C[:-1])
    return float(np.std(lr[k - L.RV_DAYS - 1:k - 1], ddof=1))


def bar(r: dict, j: int, o: float, h: float, lo: float, c: float) -> None:
    """Write a 15-minute bar with OHLC (o, h, lo, c) at bar index j."""
    a, b = j * 15, j * 15 + 15
    r["open"][a:b], r["close"][a:b] = o, c
    r["high"][a:b], r["low"][a:b] = max(o, c), min(o, c)
    r["high"][a + 7], r["low"][a + 8] = h, lo


def trades(rows: list[dict]) -> pd.DataFrame:
    out = []
    for i, rr in enumerate(rows):
        s, entry, risk = rr.get("s", 1), rr.get("entry", 100.0), rr.get("risk", 1.0)
        out.append({"pop": "chento", "tid": rr.get("tid", f"T{i}"), "asset": "BTC",
                    "direction": "long" if s > 0 else "short", "s": s,
                    "entry_ts": rr.get("entry_ts", T0 + 60 * rr["i0"] + rr.get("day_offset", 0) * 86400),
                    "entry": entry, "risk": risk, "stop": entry - s * risk,
                    "target": rr.get("target", entry + s * 6 * risk), "i0": rr["i0"], "x": rr["x"],
                    "kind": rr.get("kind", "time"), "exit_price": rr.get("exit_price", entry),
                    "exit_price_notime": rr.get("exit_price", entry), "level": rr.get("level", float("nan")),
                    "level_valid": rr.get("level_valid", False), "horizon_min": rr.get("horizon", 4320)})
    df = pd.DataFrame(out)
    df["entry_day"] = [M.utc_day(t) for t in df["entry_ts"]]
    return df


def events_of(p: L.Panel, tr: pd.DataFrame, with_cv: bool = False):
    g = L.build_grids(tr, {"BTC": p}, with_cv=with_cv)
    return L.first_events(tr, g), g


def first_of(kind: str, p: L.Panel, tr: pd.DataFrame, row: int = 0) -> int:
    ev, _ = events_of(p, tr)
    return int(ev[f"{kind}_first"].iat[row])


# --- constants ---------------------------------------------------------------------------------------

def test_frozen_constants():
    assert (L.GATE_MIN, L.IMP_MULT, L.ANNUAL_DAYS, L.RV_DAYS, L.P_MIN_R, L.WICK_FRAC, L.TOL, L.BAR_MIN,
            L.DVOL_FROM, L.FRESH_MAX, L.ERA_DAYS, L.MIN_CONTROLS, L.MIN_EVENT_TRADES, L.MIN_LINE_TRADES) == \
        (60, 1.0, 365, 30, 0.3, 0.4, 0.0015, 15, "2022-09-08", 60, 365, 3, 30, 10)
    assert L.FAMILY_CANDIDATES == ("I1_implied", "J_wick")
    assert L.DECISION_CONTROL == {"I1_implied": "I1_realised", "J_wick": "J_nolevel"}
    assert [r.name for r in L.RUNGS] == ["rung1", "rung2", "rung3"]
    assert (L.RUNGS[0].extreme, L.RUNGS[1].extreme, L.RUNGS[2].extreme, L.RUNGS[2].era) == ("fresh", "X", "fresh", False)


# --- the per-day scales (section 5) ------------------------------------------------------------------

def test_imp_uses_the_prior_days_close_and_starts_at_dvol_from():
    dv = dict(DEFAULT_DVOL)
    dv[day_str(7)] = 60.0                                   # 2022-09-08's own close
    p = panel(dv=dvol_df(dv))
    assert np.isnan(p.imp[7 * DAY - 1])                     # 09-07: before DVOL_FROM
    assert p.imp[7 * DAY] == pytest.approx(IMP50)           # 09-08 reads 09-07's close, 50
    assert p.imp[8 * DAY - 1] == pytest.approx(IMP50)       # the last minute of 09-08 still does
    assert p.imp[8 * DAY] == pytest.approx(0.6 / math.sqrt(365))   # 09-09 reads 09-08's close, 60
    dv2 = dict(DEFAULT_DVOL)
    del dv2[day_str(9)]                                     # a missing row: no IMP on the day after it
    p2 = panel(dv=dvol_df(dv2))
    assert np.isnan(p2.imp[10 * DAY]) and np.isfinite(p2.imp[11 * DAY]) and np.isfinite(p2.imp[9 * DAY])


def test_rv_from_31_day_closes_and_missing_with_one_absent():
    r = raw()
    C = alternate_day_closes(r)
    p = panel(r)
    assert p.rv[35 * DAY] == pytest.approx(rv_of(C, 35))
    assert p.rv[35 * DAY] > 0.019
    assert np.isnan(p.rv[30 * DAY]) and np.isfinite(p.rv[31 * DAY])      # day 31 is the first with 31 prior closes
    r2 = raw()
    alternate_day_closes(r2)
    r2["volume"][3 * DAY:4 * DAY] = 0.0                                      # day 3 has no present minute
    p2 = panel(r2)
    assert np.isnan(p2.rv[34 * DAY]) and np.isfinite(p2.rv[35 * DAY])
    assert np.isnan(L.day_closes(p2.ser.close, N)[3])


def test_dvol_facts_count_missing_days_and_bad_closes():
    dv = dict(DEFAULT_DVOL)
    del dv[day_str(5)]
    dv[day_str(6)] = 0.0
    f = L.dvol_facts(dvol_df(dv))["BTC"]
    assert f["rows"] == N_DAYS - 1 and f["missing_days_inside_span"] == 1 and f["missing_days"] == [day_str(5)]
    assert f["bad_closes"] == 1 and f["first_day"] == day_str(0) and f["last_day"] == day_str(N_DAYS - 1)


# --- I: move vs implied range ------------------------------------------------------------------------

def test_i1_at_the_implied_move_and_just_below_both_directions():
    i0 = 32 * DAY
    for s in (1, -1):
        r = raw()
        alternate_day_closes(r, pct=0.03)                   # RV about 6 %, above IMP, so the realised control is silent
        r["close"][i0 + 100] = 100.0 * (1 + s * IMP50) + s * 1e-9
        r["close"][i0 + 101] = 100.0 * (1 + s * (IMP50 - 1e-4))
        p = panel(r)
        ev, _ = events_of(p, trades([{"i0": i0, "x": i0 + 300, "s": s}]))
        assert int(ev["I1_implied_first"].iat[0]) == i0 + 100
        assert int(ev["I1_implied_minutes"].iat[0]) == 1               # the just-below twin is not an event
        assert int(ev["I1_realised_first"].iat[0]) == -1
        assert int(ev["I1_implied_only_first"].iat[0]) == i0 + 100


def test_i1_realised_control_and_implied_only():
    i0 = 32 * DAY
    r = raw()
    C = alternate_day_closes(r)                             # RV about 2 %, below IMP (2.6 %)
    rv = rv_of(C, 32)
    assert rv < IMP50
    r["close"][i0 + 100] = 100.0 * (1 + rv + 1e-6)         # crosses the realised scale only
    r["close"][i0 + 200] = 100.0 * (1 + IMP50 + 1e-6)      # crosses both
    p = panel(r)
    ev, _ = events_of(p, trades([{"i0": i0, "x": i0 + 300}]))
    assert int(ev["I1_realised_first"].iat[0]) == i0 + 100
    assert int(ev["I1_implied_first"].iat[0]) == i0 + 200
    assert int(ev["I1_implied_only_first"].iat[0]) == -1   # where implied is crossed, realised is too


def test_i2_reads_the_move_from_the_days_open():
    i0 = 32 * DAY + 30
    r = raw()
    alternate_day_closes(r)
    r["close"][i0 + 100] = 100.0 * (1 + IMP50 + 1e-6)      # dv from the day's open 100; mv from entry 101 is smaller
    p = panel(r)
    ev, _ = events_of(p, trades([{"i0": i0, "x": i0 + 300, "entry": 101.0}]))
    assert int(ev["I2_day_first"].iat[0]) == i0 + 100
    assert int(ev["I2_day_realised_first"].iat[0]) == i0 + 100
    assert int(ev["I1_implied_first"].iat[0]) == -1
    assert p.day_open[i0 + 100] == 100.0


def test_no_i_event_before_dvol_from_and_the_shared_gate():
    r = raw()
    alternate_day_closes(r, pct=0.03)
    i0 = 5 * DAY
    r["close"][i0 + 100] = 100.0 * (1 + IMP50 + 1e-6)
    p = panel(r)
    assert first_of("I1_implied", p, trades([{"i0": i0, "x": i0 + 300}])) == -1     # IMP missing before 09-08
    r = raw()
    alternate_day_closes(r, pct=0.03)
    i0 = 32 * DAY
    r["close"][i0 + 59] = r["close"][i0 + 60] = 100.0 * (1 + IMP50 + 1e-6)
    p = panel(r)
    ev, _ = events_of(p, trades([{"i0": i0, "x": i0 + 300}]))
    assert int(ev["I1_implied_first"].iat[0]) == i0 + 60 and int(ev["I1_implied_minutes"].iat[0]) == 1


# --- bars and J ---------------------------------------------------------------------------------------

def test_bars_form_from_present_minutes_and_a_dead_bar_is_missing():
    r = raw()
    r["volume"][75:80] = 0                                   # bar 5 (75-89): its first five minutes dead
    r["open"][80], r["high"][83], r["low"][85], r["close"][89] = 100.5, 102.0, 98.0, 101.0
    r["volume"][90:105] = 0                                  # bar 6 wholly dead
    p = panel(r)
    B = p.bars["15"]
    assert B.end[89] and not B.end[88] and B.start[89] == 75 and B.start[75] == 75
    assert (B.open[89], B.high[89], B.low[89], B.close[89]) == (100.5, 102.0, 98.0, 101.0)
    assert B.present[75] and B.present[89]
    assert not B.present[95] and B.end[104] and np.isnan(B.close[104])
    B1 = p.bars["1"]
    assert B1.end.all() and B1.start[89] == 89 and not B1.present[95] and B1.present[89]


def test_wick_needs_the_rejection_shape_and_the_arming_at_their_boundaries():
    # long from 100, risk 1; bar 5 (minutes 75-89) reaches the trade's own target 101, a level: range 2.0, so the
    # shape needs a shadow of 0.8 -> a close of 100.2 is a rejection, 100.21 is not (the level held: J_accept)
    for close, expect in ((100.2, "J_wick"), (100.21, "J_accept")):
        r = raw()
        bar(r, 5, 100.0, 101.0, 99.0, close)
        ev, _ = events_of(panel(r), trades([{"i0": 0, "x": 300, "target": 101.0}]))
        for k in ("J_wick", "J_accept", "J_nolevel"):
            assert int(ev[f"{k}_first"].iat[0]) == (89 if k == expect else -1), (close, k)
    # arming: the bar's extreme at +0.3 R arms, +0.29 R does not (the target is the level, the shape holds)
    for ext, armed in ((100.3, True), (100.29, False)):
        r = raw()
        bar(r, 5, 99.5, ext, 99.0, 99.7)
        ev, _ = events_of(panel(r), trades([{"i0": 0, "x": 300, "target": ext}]))
        assert int(ev["J_wick_first"].iat[0]) == (89 if armed else -1)
        assert int(ev["J_shape_first"].iat[0]) == (89 if armed else -1)


def test_level_hit_near_within_tol_just_outside_traded_through_and_held():
    # entry 100 -> the round grid is 100, 110, 120 ... and TOL is 0.15
    cases = [(109.9, 100.2, "J_wick"),        # within 0.15 of 110, rejection shape
             (109.8, 100.2, "J_nolevel"),     # 0.2 away and not through: the shape alone
             (110.5, 100.2, "J_wick"),        # through 110 and closed back on the entry side
             (110.1, 110.05, "J_accept"),     # at the level, no rejection shape: the level held
             (110.5, 110.3, None)]            # through and held, no shape: nothing
    for ext, close, expect in cases:
        r = raw()
        bar(r, 5, 100.0, ext, 99.9, close)
        ev, g = events_of(panel(r), trades([{"i0": 0, "x": 300, "target": 200.0}]))
        for k in ("J_wick", "J_nolevel", "J_accept"):
            assert int(ev[f"{k}_first"].iat[0]) == (89 if k == expect else -1), (ext, close, k)
        assert 110.0 in g.levels[0] and 100.0 in g.levels[0] and 200.0 in g.levels[0]


def test_short_mirror_reads_the_low_and_the_lower_shadow():
    r = raw()
    bar(r, 5, 100.0, 100.1, 90.1, 99.8)                     # near 90 on the grid; lower shadow 9.7 of range 10
    p = panel(r)
    ev, _ = events_of(p, trades([{"i0": 0, "x": 300, "s": -1, "target": 50.0}]))
    assert int(ev["J_wick_first"].iat[0]) == 89
    assert first_of("J_shape", p, trades([{"i0": 0, "x": 300, "target": 200.0}])) == -1   # a long: +0.1 R, unarmed


def test_a_bar_crossing_the_exit_is_never_an_event_and_the_first_event_is_kept():
    r = raw()
    bar(r, 5, 100.0, 101.0, 99.0, 100.2)
    bar(r, 8, 100.0, 101.0, 99.0, 100.2)
    p = panel(r)
    assert first_of("J_wick", p, trades([{"i0": 0, "x": 85, "target": 101.0}])) == -1   # bar 5 ends at 89 >= x
    assert first_of("J_wick", p, trades([{"i0": 0, "x": 90, "target": 101.0}])) == 89
    ev, _ = events_of(p, trades([{"i0": 0, "x": 300, "target": 101.0}]))
    assert int(ev["J_wick_first"].iat[0]) == 89 and int(ev["J_wick_minutes"].iat[0]) == 2


def test_the_24h_extreme_is_a_level_only_when_valid():
    r = raw()
    bar(r, 5, 100.0, 103.0, 99.0, 100.2)                    # 103: no grid level, target far
    p = panel(r)
    assert first_of("J_wick", p, trades([{"i0": 0, "x": 300, "target": 200.0, "level": 103.0,
                                          "level_valid": True}])) == 89
    assert first_of("J_wick", p, trades([{"i0": 0, "x": 300, "target": 200.0, "level": 103.0,
                                          "level_valid": False}])) == -1
    assert first_of("J_nolevel", p, trades([{"i0": 0, "x": 300, "target": 200.0, "level": 103.0,
                                             "level_valid": False}])) == 89


def test_grid_step_and_candidate_levels():
    assert (L.grid_step(78_000), L.grid_step(3_000), L.grid_step(950)) == (1000, 100, 10)
    lv = L.candidate_levels(100.0, 1, 106.0, 100.1, True, 99.0, 125.0)
    assert lv.tolist() == [100.0, 100.1, 106.0, 110.0, 120.0, 130.0, 140.0]
    lv = L.candidate_levels(100.0, -1, 94.0, 99.9, True, 85.0, 101.0)
    assert lv.tolist() == [80.0, 90.0, 94.0, 99.9, 100.0]
    assert L.candidate_levels(100.0, 1, 106.0, float("nan"), False, float("nan"), float("nan")).tolist() == [106.0]


def test_the_1_minute_arm_reads_each_minute_as_a_bar():
    r = raw()
    m = 100
    r["open"][m], r["high"][m], r["low"][m], r["close"][m] = 100.0, 101.0, 99.0, 100.2
    ev, _ = events_of(panel(r), trades([{"i0": 0, "x": 300, "target": 101.0}]))
    assert int(ev["J_wick_1m_first"].iat[0]) == 100
    assert int(ev["J_wick_first"].iat[0]) == 104            # the 15-minute bar 6 (90-104) carries the same shape


# --- the placebo (section 6) -------------------------------------------------------------------------

def _pop_for_match(control_highs_at: list, spacing: int = 405) -> tuple:
    """One event trade E (a J_wick at minute 89 on its own target) and controls, each with a new running high at
    the given minutes of its own life (fresh for the next 60 minutes), on bar-aligned entries."""
    r = raw()
    bar(r, 5, 100.0, 101.0, 99.0, 100.2)
    rows = [{"i0": 0, "x": 300, "target": 101.0, "tid": "E"}]
    for k, at in enumerate(control_highs_at):
        i0c = spacing * (k + 1)
        for j, m in enumerate(at):
            r["high"][i0c + m:i0c + 300] = 100.1 + 0.02 * (j + 1)
        rows.append({"i0": i0c, "x": i0c + 300, "target": 101.0, "tid": f"C{k}"})
    return r, rows


def test_rung1_admits_stall_59_excludes_60_rung2_needs_a_strict_extreme_S1_needs_neither():
    r, rows = _pop_for_match([[40]])
    ev, g = events_of(panel(r), trades(rows), with_cv=True)
    assert int(ev["J_wick_first"].iat[0]) == 89
    r1 = L.match(ev, g, "J_wick", 216, L.RUNGS[0])
    assert r1["control_minutes"].iat[0] == 40               # minutes 60..99: stall 20..59; stall 60 at minute 100 is out
    assert L.match(ev, g, "J_wick", 216, L.RUNGS[1])["control_minutes"].iat[0] == 0   # the extreme at 40 is before the gate
    assert L.match(ev, g, "J_wick", 216, L.SECONDARY[0])["control_minutes"].iat[0] == 240   # every gated bin-0 minute
    r, rows = _pop_for_match([[70]])
    ev, g = events_of(panel(r), trades(rows), with_cv=True)
    assert L.match(ev, g, "J_wick", 216, L.RUNGS[1])["control_minutes"].iat[0] == 1     # the strict extreme itself
    assert L.match(ev, g, "J_wick", 216, L.RUNGS[0])["control_minutes"].iat[0] == 60


def test_era_direction_overlap_bin_and_the_control_floor():
    def controls_under(rung, **over):
        r, rows = _pop_for_match([[70], [70], [70]])
        for k, v in over.items():
            rows[1][k] = v
        if "bin_close" in over:
            r["close"][rows[1]["i0"]:rows[1]["x"]] = over["bin_close"]
        ev, g = events_of(panel(r), trades(rows), with_cv=True)
        out = L.match(ev, g, "J_wick", 216, rung)
        return int(out["controls"].iat[0]), L.included(out)
    assert controls_under(L.RUNGS[0]) == (3, 1)
    assert controls_under(L.RUNGS[0], day_offset=364) == (3, 1)
    assert controls_under(L.RUNGS[0], day_offset=366) == (2, 0)          # era: out under rung 1
    assert controls_under(L.RUNGS[2], day_offset=366) == (3, 1)          # rung 3: all years
    assert controls_under(L.RUNGS[0], s=-1) == (2, 0)                    # the other direction
    assert controls_under(L.RUNGS[0], i0=200, x=500) == (2, 0)           # overlapping the event trade
    assert controls_under(L.RUNGS[0], bin_close=100.3) == (2, 0)         # another profit bin
    assert controls_under(L.SECONDARY[2], bin_close=100.3) == (3, 1)     # S3: time only, no bin


def test_the_shared_pool_admits_a_control_with_a_prior_event():
    i0 = 32 * DAY
    r = raw()
    alternate_day_closes(r)
    r["close"][i0 + 100] = 100.0 * (1 + IMP50 + 1e-6)      # E crosses IMP at its minute 100 (mark 0.26 R at risk 10)
    r["high"][i0 + 100:i0 + 300] = 103.0
    rows = [{"i0": i0, "x": i0 + 300, "risk": 10.0, "tid": "E"}]
    for k in range(3):
        i0c = i0 + 400 * (k + 1)
        r["close"][i0c:i0c + 300] = 100.3                     # bin 1 at risk 1, like E's 0.26 R
        r["high"][i0c + 90:i0c + 300] = 100.5                 # fresh 90..149
        rows.append({"i0": i0c, "x": i0c + 300, "tid": f"C{k}"})
    c0 = i0 + 400
    r["close"][c0 + 60] = 100.0 * (1 + IMP50 + 1e-6)        # C0's own crossing at its gate minute: a prior event
    ev, g = events_of(panel(r), trades(rows), with_cv=True)
    assert int(ev["I1_implied_first"].iat[0]) == i0 + 100 and int(ev["I1_implied_first"].iat[1]) == c0 + 60
    assert int(L.match(ev, g, "I1_implied", 216, L.RUNGS[0])["controls"].iat[0]) == 2
    assert int(L.match(ev, g, "I1_implied", 216, L.shared_spec(L.RUNGS[0]))["controls"].iat[0]) == 3


def test_unknown_status_excludes_a_control_but_not_from_the_shared_pool():
    r, rows = _pop_for_match([[80], [80], [80]])
    c0 = rows[1]["i0"]
    r["volume"][c0 + 60:c0 + 75] = 0                         # C0's bar 4 (its first gated bar) is missing
    ev, g = events_of(panel(r), trades(rows), with_cv=True)
    assert int(L.match(ev, g, "J_wick", 216, L.RUNGS[0])["controls"].iat[0]) == 2
    assert int(L.match(ev, g, "J_wick", 216, L.shared_spec(L.RUNGS[0]))["controls"].iat[0]) == 3
    r, rows = _pop_for_match([[80], [80], [80]])
    r["volume"][c0 + 30:c0 + 45] = 0                         # a missing bar before the gate says nothing
    ev, g = events_of(panel(r), trades(rows), with_cv=True)
    assert int(L.match(ev, g, "J_wick", 216, L.RUNGS[0])["controls"].iat[0]) == 3


def test_rvol_and_rsession_keep_only_the_matched_controls():
    i0 = 32 * DAY                                            # 2022-10-03 00:00 UTC, a Monday
    r = raw()
    bar(r, i0 // 15 + 5, 100.0, 101.0, 99.0, 100.2)          # E: J_wick at its minute 89, 01:29 UTC, Asia, weekday
    rows = [{"i0": i0, "x": i0 + 300, "target": 101.0, "tid": "E"}]
    starts = {"C_asia_next_day": i0 + DAY, "C_london": i0 + 480, "C_saturday": i0 + 5 * DAY}
    for tid, i0c in starts.items():
        r["high"][i0c + 70:i0c + 300] = 100.12               # fresh 70..129
        rows.append({"i0": i0c, "x": i0c + 300, "target": 101.0, "tid": tid})
    r["volume"][starts["C_london"] - 60:starts["C_london"] + 300] = 0.5   # VR below 1 on the London control
    ev, g = events_of(panel(r), trades(rows), with_cv=True)
    rung = L.RUNGS[0]
    base = L.match(ev, g, "J_wick", 216, rung)
    assert int(base["controls"].iat[0]) == 3
    rvol, rsess = L.robustness_specs(rung)
    v = L.match(ev, g, "J_wick", 216, rvol)
    assert int(v["controls"].iat[0]) == 2 and v["control_tids"].iat[0] == "C_asia_next_day;C_saturday"
    s = L.match(ev, g, "J_wick", 216, rsess)
    assert int(s["controls"].iat[0]) == 1 and s["control_tids"].iat[0] == "C_asia_next_day"


def test_placebo_and_delta_come_from_the_controls_continuation_values():
    r, rows = _pop_for_match([[70], [70], [70]])
    rows[0]["exit_price"] = 99.0                             # E stops out: shipped R -1; its CV at minute 89 is -1.2
    for k in (1, 2, 3):
        rows[k]["exit_price"] = 102.0                        # each control ends +2 R: CV +2 at every bin-0 minute
    ev, g = events_of(panel(r), trades(rows), with_cv=True)
    out = L.match(ev, g, "J_wick", 216, L.RUNGS[0])
    assert out["cv"].iat[0] == pytest.approx(-1.2) and out["placebo"].iat[0] == pytest.approx(2.0)
    assert out["delta"].iat[0] == pytest.approx(-3.2)


def test_overlay_pairs_the_event_exit_against_the_shipped_exit():
    r, rows = _pop_for_match([[70]])
    rows[0]["exit_price"], rows[0]["kind"] = 99.0, "stop"
    ev, g = events_of(panel(r), trades(rows), with_cv=True)
    o = L.overlay_rows(ev, g, "J_wick")
    assert o["event"].tolist() == [True, False]
    assert o["diff"].to_numpy() == pytest.approx([1.2, 0.0])   # exit at the wick close 100.2 instead of the stop
    axis = M.day_axis(ev["entry_day"])
    summ = L.summarize_overlay(o, axis, M.block_indices(len(axis), n_boot=50))
    assert summ["n"] == 2 and summ["n_event"] == 1 and summ["event_trades_shipped_stop"] == 1
    assert summ["mean_diff_R"] == pytest.approx(0.6)


# --- the verdict (section 8) -------------------------------------------------------------------------

def test_verdict_promotes_only_with_an_agreeing_eth_line():
    import rangewick_run as R

    def t(cls, n=40, mean=-0.3):
        return {"classification": cls, "decision_rung": "rung1",
                "lines": {"rung1": {"n": n, "mean": mean, "first_half": mean, "second_half": mean}}}

    family = ["chento_BTC:J_wick", "squeeze_bull:J_wick"]
    good = {"J_wick": {"subset_included": 12, "subset_mean_delta": -0.2}}
    tests = {"chento_BTC:J_wick": t("UNDETERMINED"), "squeeze_bull:J_wick": t("INFORMATIVE"),
             "chento_ETH:J_wick": t("DESCRIPTIVE")}
    assert R.stage_verdict(tests, family, good)["verdict"] == "PROMOTED: J_wick"
    tests["chento_ETH:J_wick"] = t("DESCRIPTIVE", n=29)
    v = R.stage_verdict(tests, family, good)
    assert v["verdict"] == "NONE PROMOTED" and v["per_kind"]["J_wick"]["replication"] == "unavailable"
    tests["chento_ETH:J_wick"] = t("DESCRIPTIVE")
    v = R.stage_verdict(tests, family, {"J_wick": {"subset_included": 9, "subset_mean_delta": -0.2}})
    assert v["per_kind"]["J_wick"]["replication"] == "replication unavailable (non-overlap subset)"
    tests["chento_ETH:J_wick"] = t("DESCRIPTIVE", mean=0.1)
    assert R.stage_verdict(tests, family, good)["per_kind"]["J_wick"]["replication"] == "disagrees"
    tests["chento_ETH:J_wick"] = t("DESCRIPTIVE")
    tests["squeeze_bull:J_wick"] = t("INFORMATIVE, robustness unavailable")
    v = R.stage_verdict(tests, family, good)
    assert v["verdict"] == "NONE PROMOTED" and v["per_kind"]["J_wick"]["replication"] == "not evaluated"

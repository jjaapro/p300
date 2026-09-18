"""Synthetic fixtures for the chento exit-policy walker (PREREGISTRATION_CHENTO.md sections 3-7).

Every market is flat at 100 with a 1R risk of 1.0 (stop 99 / target 106 for a long) unless a test changes a
bar. The bot's math.py is loaded straight from its file, so no sleeve package, clock or database is imported.

    venv\\Scripts\\python.exe -m pytest studies/notebooks/exit_policy_2026_09/tests -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import chento_lib as C  # noqa: E402

_spec = importlib.util.spec_from_file_location("chento_math_fixture", C.ROOT / "bots" / "chento_v3" / "strategy" / "math.py")
ctm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ctm)

T = 1_700_000_100 - (1_700_000_100 % 900)      # a 15m bar open
H = 3600
N_BARS = 4 * 24 * 40                           # 40 days of 15m bars


def market(bars: dict | None = None, missing=(), against_long=(), against_short=(), opposite_short=(),
           funding=(), n=N_BARS) -> C.Market:
    ts = T + np.arange(n) * 900
    high = np.full(n, 100.2)
    low = np.full(n, 99.8)
    close = np.full(n, 100.0)
    for offset, (h, lo, c) in (bars or {}).items():
        i = offset // 900
        high[i], low[i], close[i] = h, lo, c
    keep = np.ones(n, dtype=bool)
    for offset in missing:
        keep[offset // 900] = False
    frame = pd.DataFrame({"timestamp": ts[keep], "high": high[keep], "low": low[keep], "close": close[keep]})
    b = C.L.Bars.from_frame(frame)
    cvd = np.zeros(len(b.ts))
    vel = np.zeros(len(b.ts))
    for offset in against_long:                 # cvd_z high with flat price: against a long
        cvd[b.pos(T + offset)] = 2.5
    for offset in against_short:
        cvd[b.pos(T + offset)] = -2.5
    fs = np.array([T + o for o, _ in funding], dtype=np.int64)
    fr = np.array([r for _, r in funding], dtype=float)
    opp = {"long": np.array(sorted(T + o for o in opposite_short), dtype=np.int64), "short": np.array([], dtype=np.int64)}
    return C.Market("SYN", b, cvd, vel, opp, fs, fr)


def walk(mkt, arm="A0", direction="long", k=2.0, funding=True, entry=100.0, risk=1.0):
    return C.walk(ctm, mkt, T, direction, entry, risk, C.BY_ID[arm], k, cost_bp=10.0, with_funding=funding)


COST = 10 / 10000 * 100 / 1.0      # 0.1R at entry 100, risk 1


# --- time exits, stops and targets ------------------------------------------------------------

def test_a0_exits_at_the_close_of_the_bar_opening_72h_after_the_trigger():
    e = walk(market({72 * H: (100.3, 99.9, 100.25)}))
    assert e.kind == "time" and e.exit_bar_ts == T + 72 * H and e.exit_price == pytest.approx(100.25)
    assert e.R_price == pytest.approx(0.25 - COST)


def test_time_exit_bar_is_not_checked_for_a_stop():
    e = walk(market({72 * H: (100.0, 98.0, 99.5)}))       # the bot exits at this bar's close first
    assert e.kind == "time" and e.exit_price == pytest.approx(99.5)


def test_stop_is_checked_before_target_in_the_same_bar():
    e = walk(market({5 * H: (107.0, 98.5, 100.0)}))
    assert e.kind == "stop" and e.exit_price == pytest.approx(99.0) and e.R_price == pytest.approx(-1 - COST)


def test_target_fills_at_the_level():
    e = walk(market({10 * H: (106.5, 100.0, 106.2)}))
    assert e.kind == "target" and e.R_price == pytest.approx(6 - COST)


def test_grid_values_and_a2_168():
    assert walk(market(), arm="A2_6").exit_bar_ts == T + 6 * H
    assert walk(market(), arm="A2_168").exit_bar_ts == T + 168 * H


def test_a1_holds_past_72h_and_censors_at_720h():
    e = walk(market({100 * H: (106.1, 100.0, 106.0)}), arm="A1")
    assert e.kind == "target" and e.exit_bar_ts == T + 100 * H
    c = walk(market(), arm="A1")
    assert c.kind == "censored_horizon" and c.exit_bar_ts == T + 720 * H


def test_data_end_censors_arms_without_a_time_exit():
    e = walk(market(n=4 * 24 * 10), arm="A1")
    assert e.kind == "censored_data_end" and e.exit_bar_ts == T + (4 * 24 * 10 - 1) * 900


def test_missing_bars_are_skipped_and_counted():
    e = walk(market({3 * H: (100.2, 98.9, 99.0)}, missing=[3 * H]))
    assert e.kind == "time" and e.missing == 1


# --- condition exits ---------------------------------------------------------------------------

def test_x1_exits_at_the_first_absorption_against_even_in_profit():
    mkt = market({20 * H: (101.0, 100.5, 100.8)}, against_long=[20 * H, 30 * H])
    e = walk(mkt, arm="X1")
    assert e.kind == "event" and e.exit_bar_ts == T + 20 * H and e.exit_price == pytest.approx(100.8)


def test_x2_ignores_absorption_while_winning_and_exits_when_losing():
    mkt = market({20 * H: (101.0, 100.5, 100.8), 30 * H: (100.0, 99.5, 99.6)}, against_long=[20 * H, 30 * H])
    e = walk(mkt, arm="X2")
    assert e.kind == "event" and e.exit_bar_ts == T + 30 * H and e.exit_price == pytest.approx(99.6)


def test_absorption_threshold_k_applies():
    mkt = market(against_long=[20 * H])                  # cvd_z = 2.5
    assert walk(mkt, arm="X1", k=3.0).kind == "censored_horizon"
    assert walk(mkt, arm="X1", k=2.0).kind == "event"


def test_absorption_in_the_trade_direction_does_not_exit():
    mkt = market(against_short=[20 * H])                 # cvd_z = -2.5 supports a long
    assert walk(mkt, arm="X1").kind == "censored_horizon"


def test_x3_exits_on_the_opposite_trigger_but_not_on_the_entry_bar():
    mkt = market(opposite_short=[0, 40 * H])
    e = walk(mkt, arm="X3")
    assert e.kind == "event" and e.exit_bar_ts == T + 40 * H


def test_stop_or_target_beats_an_event_in_the_same_bar():
    mkt = market({20 * H: (100.2, 98.0, 99.5)}, against_long=[20 * H])
    assert walk(mkt, arm="X1").kind == "stop"


def test_short_position_mirrors():
    mkt = market({10 * H: (100.0, 93.9, 94.0)})
    e = walk(mkt, direction="short")
    assert e.kind == "target" and e.R_price == pytest.approx(6 - COST)


# --- funding ---------------------------------------------------------------------------------------

def test_funding_sign_mark_and_boundaries():
    # settlements: at entry (not charged), during the hold (charged), at the time-exit close (not charged)
    f = [(900, 0.001), (8 * H, 0.001), (72 * H + 900, 0.001)]
    e = walk(market(funding=f))
    assert e.funding_R == pytest.approx(-0.001 * 100.0 / 1.0)
    s = walk(market(funding=f), direction="short")
    assert s.funding_R == pytest.approx(+0.001 * 100.0)


def test_funding_at_the_open_of_a_stop_bar_is_charged():
    e = walk(market({16 * H: (100.2, 98.5, 99.0)}, funding=[(16 * H, 0.002)]))
    assert e.kind == "stop" and e.funding_R == pytest.approx(-0.002 * 100.0)
    assert e.net_R == pytest.approx(e.R_price + e.funding_R)


# --- causality and statistics ---------------------------------------------------------------------------

def test_future_bars_do_not_change_completed_exits():
    full = market({30 * H: (100.2, 98.0, 99.0), 200 * H: (107, 99.5, 106.5)})
    short = market({30 * H: (100.2, 98.0, 99.0)}, n=4 * 24 * 5)
    for arm in ("A0", "A1", "X1", "X2", "X3"):
        a, b = walk(full, arm=arm), walk(short, arm=arm)
        assert (a.kind, a.exit_bar_ts, a.exit_price) == (b.kind, b.exit_bar_ts, b.exit_price)


def test_step0_first_fire_and_k_rule():
    mkt = market(against_long=[10 * H])
    h, cens = C.first_fire_hours(mkt, T, "long", 2.0)
    assert h == pytest.approx(10.0) and not cens
    assert C.first_fire_hours(mkt, T, "long", 3.0) == (720.0, True)


def test_holm_and_bootstrap_p():
    adj = C.holm_adjust({"a": 0.01, "b": 0.02, "c": 0.04})
    assert adj == {"a": 0.03, "b": 0.04, "c": 0.04}
    assert C.p_one_sided(1.0, np.array([0.5, 1.5, 2.5, 3.0])) == pytest.approx(3 / 5)
    idx = C.block_indices(T=10, block=4, n_boot=3, seed=1)
    d = np.diff(idx, axis=1) % 10
    assert np.all((d == 1) | (np.arange(1, 10) % 4 == 0)[None, :])

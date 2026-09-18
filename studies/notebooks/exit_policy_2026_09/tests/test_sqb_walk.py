"""Synthetic fixtures for the squeeze_bull exit walker (PREREGISTRATION_SQUEEZE_BULL.md section 3).

A flat 1-minute market at 100 (high 100.1, low 99.9), entry 100 at the trigger bar's close, so R = 2.0 and the
7 bp cost is 0.035 R. Each test changes only the minutes that matter.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sqb_lib as S  # noqa: E402

T0 = 1_700_006_400                     # a UTC midnight, seconds
BAR = T0 + 3600 * 5                    # trigger bar open; entry at BAR + 1 h
ENTRY_TS = BAR + 3600
N = 60 * 24 * 40
COST = 0.035


def path(minutes: dict | None = None, missing=(), funding=(), n=N) -> S.Path1m:
    o = np.full(n, 100.0)
    h = np.full(n, 100.1)
    lo = np.full(n, 99.9)
    c = np.full(n, 100.0)
    for offset_min, (mo, mh, ml, mc) in (minutes or {}).items():
        i = (ENTRY_TS - T0) // 60 + offset_min
        o[i], h[i], lo[i], c[i] = mo, mh, ml, mc
    for offset_min in missing:
        i = (ENTRY_TS - T0) // 60 + offset_min
        o[i] = h[i] = lo[i] = c[i] = np.nan
    fs = np.array([ENTRY_TS + s for s, _ in funding], dtype=np.int64)
    fr = np.array([r for _, r in funding], dtype=float)
    return S.Path1m(T0 * 1000, o, h, lo, c, fs, fr)


FIRE = SimpleNamespace(entry=100.0, entry_ts=ENTRY_TS, bar_ts=BAR, entry_day="2023-11-15")


def walk(p, arm="S0", flush=()):
    return S.walk(p, FIRE, S.BY_ID[arm], np.array(sorted(flush), dtype=np.int64))


def test_time_exit_at_the_open_of_the_minute_48h_after_entry():
    r = walk(path({48 * 60: (100.5, 100.6, 100.4, 100.5)}))
    assert r["kind"] == "time" and r["exit_s"] == ENTRY_TS + 48 * 3600 and r["exit_price"] == pytest.approx(100.5)
    assert r["R_price"] == pytest.approx(0.5 / 2.0 - COST)


def test_stop_at_level_and_gap_at_open():
    assert walk(path({90: (100.0, 100.0, 97.5, 98.5)}))["exit_price"] == pytest.approx(98.0)
    g = walk(path({90: (97.0, 97.2, 96.8, 97.0)}))
    assert g["kind"] == "stop" and g["exit_price"] == pytest.approx(97.0)


def test_target_at_level_and_stop_first_in_the_same_minute():
    t = walk(path({200: (100.0, 103.5, 100.0, 103.0)}))
    assert t["kind"] == "target" and t["R_price"] == pytest.approx(1.5 - COST)
    both = walk(path({200: (100.0, 103.5, 97.0, 100.0)}))
    assert both["kind"] == "stop"


def test_no_time_arm_holds_past_48h_and_censors_at_720h():
    r = walk(path({60 * 60: (100.0, 103.2, 100.0, 103.0)}), arm="S_NOTIME")
    assert r["kind"] == "target" and r["hours_held"] == pytest.approx(60.0)
    assert walk(path(), arm="S_NOTIME")["kind"] == "censored_horizon"


def test_wider_target_and_no_target():
    p = path({200: (100.0, 103.5, 100.0, 103.0)})
    assert walk(p, arm="S_TGT4")["kind"] == "time"
    assert walk(p, arm="S_NOTGT")["kind"] == "time"


def test_twin_has_no_stop_and_the_no_time_twin_has_a_catastrophe_stop():
    deep = path({90: (100.0, 100.0, 95.0, 95.5)})
    assert walk(deep, arm="N0")["kind"] == "time"
    crash = path({90: (100.0, 100.0, 89.0, 90.0)})
    c = walk(crash, arm="N_NOTIME")
    assert c["kind"] == "catastrophe" and c["exit_price"] == pytest.approx(90.0)


def test_flush_resumed_needs_a_window_wholly_after_the_trigger_bar():
    early = walk(path(), arm="C_REFLUSH", flush=[BAR + 3 * 3600])
    assert early["kind"] == "censored_horizon"
    r = walk(path(), arm="C_REFLUSH", flush=[BAR + 3 * 3600, BAR + 4 * 3600])
    assert r["kind"] == "event" and r["exit_s"] == BAR + 5 * 3600


def test_funding_boundaries():
    p = path({48 * 60 - 1: (100.0, 100.0, 100.0, 100.0)}, funding=[(0, 0.001), (8 * 3600, 0.001), (48 * 3600, 0.001)])
    r = walk(p)                                    # entry-instant and exit-instant settlements not charged
    assert r["funding_R"] == pytest.approx(-0.001 * 100.0 / 2.0)
    s = walk(path({8 * 60: (100.0, 100.0, 97.0, 98.0)}, funding=[(8 * 60 * 60 // 60 * 60, 0.002)]))
    assert s["kind"] == "stop" and s["funding_R"] == pytest.approx(-0.002 * 100.0 / 2.0)


def test_missing_time_exit_minute_moves_to_the_next_tradable_one():
    r = walk(path(missing=[48 * 60, 48 * 60 + 1]))
    assert r["exit_s"] == ENTRY_TS + 48 * 3600 + 120


def test_data_end_censors():
    r = walk(path(n=(ENTRY_TS - T0) // 60 + 10 * 60), arm="S_NOTIME")
    assert r["kind"] == "censored_data_end"


def test_future_minutes_do_not_change_completed_exits():
    full = path({120: (100.0, 100.0, 97.0, 98.0), 3000: (100.0, 104.0, 100.0, 103.0)})
    n_short = (ENTRY_TS - T0) // 60 + 600
    short = path({120: (100.0, 100.0, 97.0, 98.0)}, n=n_short)
    cut_s = T0 + n_short * 60
    compared = 0
    for arm in ("S0", "S_NOTIME", "C_REFLUSH", "N0", "N_NOTIME"):
        a, b = walk(full, arm), walk(short, arm)
        if a["exit_s"] < cut_s:                                  # completed before the cut
            assert (a["kind"], a["exit_s"], a["exit_price"]) == (b["kind"], b["exit_s"], b["exit_price"])
            compared += 1
        else:
            assert b["kind"] == "censored_data_end"
    assert compared == 3                                         # the three stop arms; both twins outlive the cut


def test_single_open_sequence_skips_overlaps_and_keeps_a_fire_at_the_prior_exit():
    fires = pd.DataFrame({"bar_ts": [BAR, BAR + 3600, BAR + 7200], "entry": [100.0, 100.0, 100.0]})
    walks = pd.DataFrame({"arm": ["S0"] * 3, "bar_ts": [BAR, BAR + 3600, BAR + 7200],
                          "exit_s": [ENTRY_TS + 7200, ENTRY_TS + 9000, ENTRY_TS + 20000], "net_R": [1.0, 1.0, -1.0]})
    seq = S.single_open_sequence(path(), fires, walks, "S0")
    assert seq["trades"] == 2 and seq["total_return_pct"] == pytest.approx(0.0)

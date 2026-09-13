"""The OKX gate re-validation's decision code, pinned before it judges any real outcome.

studies/notebooks/okx_gate_revalidation/README.md is frozen (plus Addendum 1). Its verdict is
final once verdict.json is written, and an INVALID burns the single permitted rerun — so every
clause, every INVALID path and every refusal is driven here on synthetic data first. Nothing
in this file reads the study snapshot or prod.db; every file it writes is under tmp_path.

Characterization pins gates, not arithmetic: each fixture asserts the intermediate clause
values it was built to produce before it asserts the verdict, so a mis-tuned fixture cannot
pass vacuously, and several twins differ from a passing fixture in exactly one clause.
"""
from __future__ import annotations

import itertools
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "studies" / "notebooks" / "okx_gate_revalidation"
for _p in (str(ROOT), str(STUDY)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import okxlib as L  # noqa: E402
import outcomes as O  # noqa: E402
import same_hour_report as S  # noqa: E402
from bots.chento_v3.strategy import math as ctm  # noqa: E402,F401  (okxlib._math needs it loaded)
from studies.lib.validation import benchmark, gates, metrics  # noqa: E402

UTC = timezone.utc
FLOAT_TOKEN = re.compile(r"\d+\.\d+")


def _ts(*a) -> int:
    return int(datetime(*a, tzinfo=UTC).timestamp())


# ─── frozen values ─────────────────────────────────────────────────────────────

def test_frozen_epochs_and_day_axis():
    assert L.SNAPSHOT_CUTOFF == _ts(2026, 9, 12)
    assert L.WINDOW_START == _ts(2021, 4, 1) and L.WINDOW_END == _ts(2026, 9, 8)
    assert L.P1_CUTOFF == _ts(2026, 7, 20, 23, 59, 59)
    assert len(L.DAY_AXIS) == 1987 and L.DAY_AXIS[0] == "2021-04-01" and L.DAY_AXIS[-1] == "2026-09-08"


def test_frozen_constants_equal_the_bot_config_at_freeze():
    """The study pins the values it froze on 2026-09-13; this documents that they were the
    bot's. If the bot later changes one, the study keeps its frozen value."""
    from bots.chento_v3 import config as botcfg
    from bots.chento_v3.strategy import config as c
    assert (L.COST_BP, L.ATR_STOP_MULT, L.TARGET_R, L.TIF_HOURS) == \
        (c.COST_BP_RT, c.ATR_STOP_MULT, c.TARGET_R, c.TIF_HOURS)
    assert (L.SMC_OB_WITHIN_R, L.UP_30D_THRESHOLD, L.OKX_ALIGN_Z_MIN, L.OKX_WINDOW_HOURS, L.COOLDOWN_HOURS) == \
        (c.SMC_OB_WITHIN_R, c.UP_30D_THRESHOLD, c.OKX_ALIGN_Z_MIN, c.OKX_DELTA_WINDOW_HOURS, c.COOLDOWN_HOURS)
    assert L.LADDER_KW == dict(ladder_enabled=c.LADDER_ENABLED, ladder_adv_trigger_R=c.LADDER_ADV_TRIGGER_R,
                               ladder_size_frac=c.LADDER_T1_SIZE_FRAC, ladder_post_stop_R=c.LADDER_POST_STOP_R)
    assert (L.RISK_PCT, L.NOTIONAL_MAX_X) == (botcfg.RISK_PCT, botcfg.NOTIONAL_MAX_X)


def test_library_contract_pins():
    assert (gates.BLOCKED_EXPECTANCY_MAX_BP, gates.SHARPE_UPLIFT_MIN, gates.SIGN_STABILITY_MIN) == (-5.0, 0.2, 2 / 3)
    assert gates.FOLD_PRESETS["event"] == (730, 365, 365)
    assert metrics.max_drawdown([1.0, -2.0, 1.0]) == 2.0          # takes PER-TRADE R
    assert benchmark.compounded_max_drawdown([np.nan, -0.5]) == 0.5  # drops NaN: callers must zero-fill
    x = np.zeros((50, 2))
    x[:, 1] = 1.0
    x[::3, 0] = 1.0
    b = benchmark.paired_block_boot_diff(x, x.copy(), L.ratio_stat, block=L.BLOCK, n_iter=200, seed=L.SEED, qs=L.QS)
    assert b["ci"] == [0.0, 0.0, 0.0] and b["n_iter"] == 200


# ─── verdict combiner and clauses ──────────────────────────────────────────────

def test_combiner_truth_table_is_total_and_ordered():
    for bits in itertools.product([False, True], repeat=8):
        inv, K1, K2, K3, K4, Ra, Rb, D = bits
        out = L.combine(["x"] if inv else [], K1, K2, K3, K4, Ra, Rb, D)
        want = ("INVALID" if inv else "KEEP" if (K1 and K2 and K3 and K4)
                else "RETIRE" if (Ra and Rb and D) else "INCONCLUSIVE")
        assert out["outcome"] == want
        assert out["failed_keep_clauses"] == [n for n, v in zip(("K1", "K2", "K3", "K4"), (K1, K2, K3, K4)) if not v]
        assert out["failed_retire_clauses"] == [n for n, v in zip(("R_a", "R_b", "D"), (Ra, Rb, D)) if not v]


@pytest.mark.parametrize("pool,btc,eth,want", [
    (1, 1, 1, True), (1, 1, -1, False), (0, 1, -1, False), (0, 1, 1, True), (1, 0, -1, False),
    (1, 0, 1, True), (-1, -1, -1, True), (0, 0, 0, True), (-1, 1, 1, False), (float("nan"), 1, 1, False),
])
def test_direction_clause(pool, btc, eth, want):
    assert L.direction_clause(pool, btc, eth) is want


def test_retire_clause_boundaries():
    B_pos = {"ci": [0.01, 0.5, 1.0]}
    B_zero = {"ci": [0.0, 0.5, 1.0]}
    assert L.retire_clauses({"ci": [-0.1, 0.1, 0.3]}, B_pos) == {"R_a": True, "R_b": True, "anti_discriminating": False}
    assert L.retire_clauses({"ci": [0.0, 0.1, 0.2]}, B_zero)["R_b"] is True      # inclusive at 0
    assert L.retire_clauses({"ci": [0.0, 0.1, 0.2]}, B_zero)["R_a"] is False     # strict at 0
    anti = L.retire_clauses({"ci": [-0.5, -0.3, -0.1]}, B_pos)
    assert anti["R_b"] is False and anti["anti_discriminating"] is True


def test_required_statement_matrix():
    assert L.required_statement("RETIRE", True, True) == "does_not_bear_on_lookahead"
    assert L.required_statement("INCONCLUSIVE", True, True) == "does_not_bear_on_lookahead"
    assert L.required_statement("KEEP", True, True) == "pipeline_red_flag"
    for v in ("KEEP", "RETIRE", "INCONCLUSIVE"):
        assert L.required_statement(v, True, False) is None
        assert L.required_statement(v, False, True) == "control_not_evaluable"


# ─── membership, POWER, P2, P1 ─────────────────────────────────────────────────

def test_filters_follow_the_bot():
    assert L.filter2_passes(float("inf")) and L.filter2_passes(2.0000001)
    assert not L.filter2_passes(2.0) and not L.filter2_passes(-1.0)
    assert not L.filter4_skips("short", 0.10) and L.filter4_skips("short", 0.1000001)
    assert not L.filter4_skips("short", float("nan")) and not L.filter4_skips("long", 0.5)
    for z in (float("nan"), -1e-12, 0.0, -0.0, 1e-12, 2.0, -2.0):
        for d in ("long", "short"):
            assert L.okx_pass(z, d) == ctm.okx_aligned(z, d, 0.0)


def test_membership_frame():
    f = pd.DataFrame({
        "t": [1, 2, 3, 4, 5, 6], "direction": ["long", "long", "short", "short", "long", "short"],
        "risk": [10.0, float("nan"), 10.0, 10.0, 0.0, 10.0], "dist_R": [float("inf"), 5.0, 2.0, 3.0, 5.0, 3.0],
        "ret_30d": [0.5, 0.0, 0.0, 0.2, 0.0, float("nan")], "z_R1": [0.1, 0.1, -0.1, -0.1, 0.1, float("nan")],
        "z_R2": [-0.1, 0.1, -0.1, -0.1, 0.1, -0.2]})
    m = L.membership(f)
    assert list(m["atr_drop"]) == [False, True, False, False, True, False]
    assert list(m["in_off"]) == [True, False, False, False, False, True]
    assert list(m["in_on_R1"]) == [True, False, False, False, False, False]   # NaN z blocks
    assert list(m["in_on_R2"]) == [False, False, False, False, False, True]


def test_power_and_fidelity_boundaries():
    def counts(pk, pb, ek, eb):
        return {"BTC": {"n_K": pk, "n_B": pb}, "ETH": {"n_K": ek, "n_B": eb},
                "pooled": {"n_K": pk + ek, "n_B": pb + eb}}
    assert L.power_passes(counts(20, 20, 10, 10))
    assert L.power_passes(counts(30, 30, 10, 10))
    assert not L.power_passes(counts(30, 30, 10, 9))       # pooled n_B 39 passes; only ETH fails
    assert not L.power_passes(counts(19, 20, 10, 10))       # pooled n_K 29
    assert L.pooled_fidelity({"BTC": {"p2_numerator": 8, "p2_denominator": 10},
                              "ETH": {"p2_numerator": 0, "p2_denominator": 0}}) >= L.P2_MIN
    assert L.pooled_fidelity({"BTC": {"p2_numerator": 7999, "p2_denominator": 10000},
                              "ETH": {"p2_numerator": 0, "p2_denominator": 0}}) < L.P2_MIN


def test_p1_comparator():
    base = pd.DataFrame({"ts": [100, 200, 300], "direction": ["long", "short", "long"],
                         "entry": [1.0, 2.0, 3.0], "stop": [0.5, 2.5, 2.5], "target": [4.0, -1.0, 6.0]})
    assert L.p1_compare(base, base.copy())["pass"]
    near = base.copy()
    near.loc[1, "entry"] = 2.0 * (1 + 5e-10)
    assert L.p1_compare(near, base)["pass"]
    far = base.copy()
    far.loc[1, "entry"] = 2.0 * (1 + 2e-9)
    r = L.p1_compare(far, base)
    assert not r["pass"] and r["first_mismatch_index"] == 1
    swapped = base.iloc[[1, 0, 2]].reset_index(drop=True)
    r = L.p1_compare(swapped, base)
    assert r["set_ok"] and not r["order_ok"] and not r["pass"]
    assert not L.p1_compare(base.iloc[:2], base)["pass"]
    assert not L.p1_compare(base.iloc[:0], base.iloc[:0])["pass"]      # nothing compared is not a pass


# ─── the §2.4 walker ───────────────────────────────────────────────────────────

T0 = _ts(2024, 3, 1, 10, 0)


def _bars(n=400, price=100.0, overrides=None, drop=()):
    ts = T0 + L.BAR_S * np.arange(n)
    high = np.full(n, price + 0.5)
    low = np.full(n, price - 0.5)
    close = np.full(n, price)
    for i, (h, lo, c) in (overrides or {}).items():
        high[i], low[i], close[i] = h, lo, c
    keep = np.array([i not in set(drop) for i in range(n)])
    return L.Bars(ts[keep], high[keep], low[keep], close[keep])


def test_walker_stop_wins_a_spanning_bar_long_and_short():
    b = _bars(overrides={5: (200.0, 30.0, 100.0)})         # touches both levels, both directions
    for d in ("long", "short"):
        w = L.walk_trade(b, T0, d, 100.0, 10.0, 10.0)
        assert w.kind == "stop" and w.exit_bar_ts == T0 + 5 * L.BAR_S
        assert w.R == pytest.approx(-1.0 - w.cost_R)


def test_walker_target_and_cost():
    b = _bars(overrides={7: (170.0, 99.0, 150.0)})
    w = L.walk_trade(b, T0, "long", 100.0, 10.0, 10.0)
    assert w.kind == "target" and w.exit_price == 160.0
    assert w.cost_R == 10 / 10000 * 100.0 / 10.0
    assert w.R == pytest.approx(6.0 - w.cost_R)
    assert L.walk_trade(b, T0, "long", 100.0, 10.0, 18.0).cost_R == 18 / 10000 * 100.0 / 10.0


def test_walker_tif_exits_at_close_of_bar_opening_t_plus_72h_without_stepping_it():
    i_tif = 72 * 4
    b = _bars(overrides={i_tif: (100.5, 50.0, 103.0)})        # stop-crossing wick only in the TIF bar
    w = L.walk_trade(b, T0, "long", 100.0, 10.0, 10.0)
    assert w.kind == "tif" and w.exit_bar_ts == T0 + 72 * 3600 and w.exit_close_ts == T0 + 72 * 3600 + 900
    assert w.R == pytest.approx(0.3 - w.cost_R)
    b2 = _bars(overrides={i_tif - 1: (100.5, 50.0, 103.0)})   # the last stepped bar is t+71h45m
    assert L.walk_trade(b2, T0, "long", 100.0, 10.0, 10.0).kind == "stop"


def test_walker_missing_bars_and_flag():
    w4 = L.walk_trade(_bars(drop=range(10, 14)), T0, "long", 100.0, 10.0, 10.0)
    w5 = L.walk_trade(_bars(drop=range(10, 15)), T0, "long", 100.0, 10.0, 10.0)
    assert (w4.missing, w4.flagged) == (4, False) and (w5.missing, w5.flagged) == (5, True)
    assert w5.kind == "tif"


def test_walker_missing_tif_bar_uses_first_later_bar_and_no_bar_is_nan():
    b = _bars(overrides={72 * 4 + 2: (100.5, 99.5, 107.0)}, drop=(72 * 4, 72 * 4 + 1))
    w = L.walk_trade(b, T0, "long", 100.0, 10.0, 10.0)
    assert w.kind == "tif" and w.exit_bar_ts == T0 + 72 * 3600 + 2 * 900 and w.exit_price == 107.0
    w2 = L.walk_trade(_bars(n=200), T0, "long", 100.0, 10.0, 10.0)
    assert w2.kind == "no_exit_bar" and math.isnan(w2.R)


# ─── P3 (Addendum A1) ──────────────────────────────────────────────────────────

def _ledger_row(reason, *, lag_s=30.0, exit_after_s=None, exit_price=None, stop=90.0, direction="LONG"):
    t = T0
    fill = t + 900 + lag_s
    notes = {"bar_ts": datetime.fromtimestamp(t, tz=UTC).isoformat(), "_entry_price": 100.0, "_risk": 10.0,
             "_stop_price": stop, "_target_price": 160.0,
             "_time_stop_iso": datetime.fromtimestamp(fill + 72 * 3600, tz=UTC).isoformat(),
             "_filter_diag": {"okx_delta_z": 0.5}}
    text = json.dumps(notes) + f"\nCHENTO_TRIPLE_V3_EXIT: {reason}; fees=10bp RT, slip=0bp RT"
    exit_ts = fill + 72 * 3600 if exit_after_s is None else exit_after_s
    return {"id": "SJ-1", "direction": direction, "notes": text, "exit_price": exit_price,
            "actual_exit_time": datetime.fromtimestamp(exit_ts, tz=UTC).isoformat()}


def test_parse_ledger_notes_reads_json_and_suffix():
    obj, reason = L.parse_ledger_notes(_ledger_row("tif_expiry")["notes"])
    assert obj["_risk"] == 10.0 and reason == "tif_expiry"


@pytest.mark.parametrize("reason", ["tif_expiry", "scheduled_exit"])
def test_p3_tif_is_checked_against_the_schedule_within_180s(reason):
    ok = L.p3_check_trade(_bars(), _ledger_row(reason, lag_s=179.0))
    assert ok["pass"] and ok["reference"] == "_time_stop_iso" and ok["dt_s"] == -179.0
    late = L.p3_check_trade(_bars(), _ledger_row(reason, lag_s=180.5))
    assert not late["pass"] and late["kind_match"]
    # the live exit time is NOT the reference for TIF: an hour-late actual exit still passes
    assert L.p3_check_trade(_bars(), _ledger_row(reason, lag_s=30.0, exit_after_s=T0 + 80 * 3600))["pass"]


def test_p3_stop_checks_time_and_price_and_kind():
    b = _bars(overrides={5: (100.5, 85.0, 95.0)})
    close_ts = T0 + 6 * 900
    assert L.p3_check_trade(b, _ledger_row("stop_hit", exit_after_s=close_ts + 179, exit_price=90.0))["pass"]
    assert not L.p3_check_trade(b, _ledger_row("stop_hit", exit_after_s=close_ts + 181, exit_price=90.0))["pass"]
    bad_px = L.p3_check_trade(b, _ledger_row("stop_hit", exit_after_s=close_ts + 10, exit_price=90.0 * (1 + 1e-6)))
    assert bad_px["time_ok"] and not bad_px["price_ok"] and not bad_px["pass"]
    wrong_kind = L.p3_check_trade(b, _ledger_row("tif_expiry"))
    assert not wrong_kind["kind_match"] and not wrong_kind["pass"]


# ─── series, entry days, ordering ──────────────────────────────────────────────

def test_entry_day_of_a_2345_trigger_is_the_next_day():
    tr = L.add_entry_fields(pd.DataFrame({"t": [_ts(2023, 5, 1, 23, 45), _ts(2023, 5, 1, 23, 30)]}))
    assert list(tr["entry_day"]) == ["2023-05-02", "2023-05-01"]


def test_btc_before_eth_on_equal_entry_ts():
    tr = pd.DataFrame({"asset": ["ETH", "BTC", "BTC"], "t": [T0, T0, T0 - 900], "direction": ["long"] * 3})
    out = L.order_trades(L.add_entry_fields(tr))
    assert list(out["asset"]) == ["BTC", "BTC", "ETH"] and list(out["t"]) == [T0 - 900, T0, T0]


def test_gate_series_units_and_periods_per_year():
    tr = pd.DataFrame({"R": [1.5, -0.5], "in_on_R1": [True, False], "entry_day": ["2022-01-01", "2022-01-02"]})
    on, off, days, ppy = L.gate_series(tr)
    assert list(off) == [3.0, -1.0] and on[0] == 3.0 and math.isnan(on[1])
    assert ppy == 2 / (1987 / 365.25)


def test_mtm_daily_returns_conserve_the_trade():
    b = _bars(n=400, overrides={i: (104.5, 103.5, 104.0) for i in range(60, 400)})
    w = L.walk_trade(b, T0, "long", 100.0, 10.0, 10.0)
    rec = {"asset": "BTC", "t": T0, "direction": "long", "entry": 100.0, "risk": 10.0, "R": w.R,
           "cost_R": w.cost_R, "exit_bar_ts": w.exit_bar_ts,
           "entry_day": datetime.fromtimestamp(T0 + 900, tz=UTC).date().isoformat()}
    out = L.mtm_daily_returns([rec], {"BTC": b})
    assert not np.isnan(out).any() and len(out) == 4
    assert out.sum() == pytest.approx(0.02 * w.R)


# ─── decide(): end-to-end synthetic verdicts ───────────────────────────────────

def _frame(kept_R, blocked_R, *, days_span=(0, 1986), same_day_blocked=None):
    """Interleave kept and blocked trades per asset over the day axis; R given per asset as
    {asset: array} for the kept and the blocked sets."""
    rows = []
    for a in L.ASSETS:
        k, bl = np.asarray(kept_R[a], float), np.asarray(blocked_R[a], float)
        n = len(k) + len(bl)
        days = np.linspace(days_span[0], days_span[1] - 1, n).astype(int)
        flags = ["K" if i % 2 == 0 and i // 2 < len(k) else "B" for i in range(n)]
        ki = bi = 0
        for i, day in enumerate(days):
            t = L.WINDOW_START + int(day) * 86400 + 4 * 3600 + (ASSET_OFFSET[a])
            if flags[i] == "K" and ki < len(k):
                r, kept = k[ki], True
                ki += 1
            else:
                r, kept = bl[bi], False
                bi += 1
            if not kept and same_day_blocked is not None:
                t = L.WINDOW_START + same_day_blocked * 86400 + (bi % 90) * 900 + ASSET_OFFSET[a]
            rows.append({"asset": a, "t": t, "direction": "long" if i % 3 else "short", "entry": 100.0,
                         "risk": 10.0, "R": r, "in_on_R1": kept, "in_on_R2": kept, "z_R2": 0.5,
                         "exit_bar_ts": t + 3600})
    return L.order_trades(L.add_entry_fields(pd.DataFrame(rows)))


ASSET_OFFSET = {"BTC": 0, "ETH": 900}


def _keep_fixture(n=100, seed=1):
    rng = np.random.default_rng(seed)
    return ({a: 3.0 + 0.5 * rng.standard_normal(n) for a in L.ASSETS},
            {a: -1.0 + 0.2 * rng.standard_normal(n) for a in L.ASSETS})


def test_decide_keep():
    k, b = _keep_fixture()
    res = L.decide(_frame(k, b))
    m = res["gate_metrics"]["metrics"]
    assert m["n_folds"] >= 3 and m["blocked_expectancy_bp"] <= -5 and m["oos_sharpe_uplift"] >= 0.2 * math.sqrt(53)
    assert m["sign_stability"] == 1.0
    assert res["clauses"]["K1"] and res["clauses"]["K2"] and res["clauses"]["K3"] and res["clauses"]["K4"]
    assert res["decision"]["outcome"] == "KEEP"


def test_decide_keep_twin_where_only_R2_membership_differs_is_not_keep():
    k, b = _keep_fixture()
    tr = _frame(k, b)
    tr["in_on_R2"] = ~tr["in_on_R1"].astype(bool)          # R2 keeps what R1 blocks
    res = L.decide(tr)
    c = res["clauses"]
    assert c["K1"] and c["K2"] and c["K4"] and not c["K3"]
    assert res["decision"]["outcome"] != "KEEP"


def test_decide_k1_uses_grid_53_binary_without_reality_check(monkeypatch):
    seen = {}
    real = gates.promotion_verdict

    def spy(m, grid_size, kind="modulator", reality_check=False):
        seen.update(grid_size=grid_size, kind=kind, reality_check=reality_check, ppy=m["periods_per_year"])
        return real(m, grid_size, kind=kind, reality_check=reality_check)

    monkeypatch.setattr(gates, "promotion_verdict", spy)
    k, b = _keep_fixture(n=60)
    tr = _frame(k, b)
    L.decide(tr)
    assert seen == {"grid_size": 53, "kind": "binary", "reality_check": False, "ppy": len(tr) / (1987 / 365.25)}


def _shifted(seed, shift, n=150):
    rng = np.random.default_rng(seed)
    base = 0.8 + 2.0 * rng.standard_normal(n)
    return base + shift, base


def test_decide_retire():
    kb, bb = _shifted(11, 0.05)
    ke, be = _shifted(12, 0.05)
    res = L.decide(_frame({"BTC": kb, "ETH": ke}, {"BTC": bb, "ETH": be}))
    c = res["clauses"]
    assert res["delta"]["BTC"] == pytest.approx(0.05) and res["delta"]["ETH"] == pytest.approx(0.05)
    assert c["R_a"] and c["R_b"] and c["D"] and not c["K1"]
    assert res["decision"]["outcome"] == "RETIRE"


def test_decide_assets_disagreeing_in_sign_is_inconclusive_not_retire():
    kb, bb = _shifted(11, 0.05)
    ke, be = _shifted(12, -0.05)
    res = L.decide(_frame({"BTC": kb, "ETH": ke}, {"BTC": bb, "ETH": be}))
    c = res["clauses"]
    assert c["R_a"] and c["R_b"] and not c["D"]
    assert res["decision"]["outcome"] == "INCONCLUSIVE" and "D" in res["decision"]["failed_retire_clauses"]


def test_decide_anti_discriminating_is_inconclusive_and_flagged():
    rng = np.random.default_rng(5)
    k = {a: -1.0 + 0.1 * rng.standard_normal(80) for a in L.ASSETS}
    b = {a: 1.0 + 0.1 * rng.standard_normal(80) for a in L.ASSETS}
    res = L.decide(_frame(k, b))
    assert res["anti_discriminating"] and not res["clauses"]["R_b"]
    assert res["clauses"]["R_a"] and res["boot_B"]["ci"][0] > 0.5        # boot_B is the BLOCKED arm
    assert res["decision"]["outcome"] == "INCONCLUSIVE"


def test_decide_invalid_on_non_finite_R():
    k, b = _keep_fixture(n=40)
    tr = _frame(k, b)
    tr.loc[3, "R"] = float("nan")
    assert L.decide(tr)["decision"] == L.combine(["non_finite_R"], *[False] * 7)


def test_decide_invalid_on_degenerate_R2_arm():
    k, b = _keep_fixture(n=40)
    tr = _frame(k, b)
    tr["in_on_R2"] = False
    assert L.decide(tr)["decision"]["invalid_checks"] == ["degenerate_R2_arm"]
    tr = _frame(k, b)
    tr["z_R2"] = float("nan")
    assert "R2_z_all_non_finite" in L.decide(tr)["decision"]["invalid_checks"]


def test_decide_invalid_when_folds_are_degenerate():
    k, b = _keep_fixture(n=40)
    res = L.decide(_frame(k, b, days_span=(0, 800)))
    assert res["decision"]["outcome"] == "INVALID"
    assert "gate_metrics_n_folds_lt_3" in res["decision"]["invalid_checks"]


def test_decide_invalid_on_non_finite_bootstrap_quantile():
    k, b = _keep_fixture(n=40)
    res = L.decide(_frame(k, b, same_day_blocked=1500))
    assert res["decision"]["outcome"] == "INVALID"
    assert "non_finite_bootstrap_quantile" in res["decision"]["invalid_checks"]


# ─── guards ────────────────────────────────────────────────────────────────────

def test_connect_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", sqlite3.connect)
    monkeypatch.setattr(sqlite3, "_okx_real_connect", sqlite3.connect, raising=False)
    ok, ro_only, other = tmp_path / "ok.db", tmp_path / "ro.db", tmp_path / "other.db"
    for p in (ok, ro_only, other):
        sqlite3.connect(str(p)).close()
    L.install_connect_guard(allowed=[ok], allowed_ro=[ro_only])
    sqlite3.connect(str(ok)).close()
    sqlite3.connect(f"file:{ro_only.as_posix()}?mode=ro", uri=True).close()
    with pytest.raises(PermissionError):
        sqlite3.connect(str(ro_only))
    with pytest.raises(PermissionError):
        sqlite3.connect(str(other))
    with pytest.raises(PermissionError):
        sqlite3.connect(str(L.PROD_DB))


def _run(args, cwd=ROOT, env_extra=None):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    env.update(env_extra or {})
    return subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True, env=env, timeout=600)


def test_sleeve_guard_refuses_a_late_or_diag_on_import():
    code = ("import sys; sys.path.insert(0, r'%s'); sys.path.insert(0, r'%s'); import os, okxlib as L\n"
            "os.environ['CHENTO_V3_DIAG'] = '1'\n"
            "try:\n    L.assert_no_sleeve_yet()\nexcept L.Refusal:\n    print('refused-diag')\n"
            "os.environ['CHENTO_V3_DIAG'] = '0'\nimport bots.chento_v3.strategy\n"
            "try:\n    L.assert_no_sleeve_yet()\nexcept L.Refusal:\n    print('refused-late')\n") % (ROOT, STUDY)
    out = _run(["-c", code]).stdout
    assert "refused-diag" in out and "refused-late" in out


# ─── CLIs on a tiny synthetic snapshot ─────────────────────────────────────────

def _source_db(path: Path):
    con = sqlite3.connect(str(path))
    for t in ("cd_futures_15m", "cd_futures_eth_15m"):
        con.execute(f"CREATE TABLE {t} (timestamp INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL)")
    for t in ("okx_perp_1h", "okx_perp_eth_1h"):
        con.execute(f"CREATE TABLE {t} (timestamp INTEGER PRIMARY KEY, close REAL)")
    con.execute("CREATE TABLE ca_long_short_ratio (asset TEXT, timestamp INTEGER, ratio REAL, long_pct REAL, "
                "short_pct REAL, UNIQUE(asset, timestamp))")
    con.execute("CREATE TABLE trades (id TEXT PRIMARY KEY, strategy_variant TEXT, direction TEXT, status TEXT, "
                "actual_entry_time TEXT, actual_exit_time TEXT, exit_price REAL, notes TEXT, pnl_usdt REAL)")
    cut = L.SNAPSHOT_CUTOFF
    for t in ("cd_futures_15m", "cd_futures_eth_15m"):
        con.executemany(f"INSERT INTO {t} VALUES (?, 100, 100.5, 99.5, 100)",
                        [(cut - 900 * 3,), (cut - 900,), (cut,), (cut + 900,)])
    for t in ("okx_perp_1h", "okx_perp_eth_1h"):
        con.executemany(f"INSERT INTO {t} VALUES (?, 100)", [(cut - 3600,), (cut,)])
    con.executemany("INSERT INTO ca_long_short_ratio VALUES (?, ?, 1, 0.5, 0.5)",
                    [("BTC", cut - 86400), ("ETH", cut - 86400), ("OP", cut - 86400), ("BTC", cut)])
    row = _ledger_row("tif_expiry")
    con.executemany("INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, 123.0)", [
        ("SJ-1", "bot_chento_v3_v1", "LONG", "closed", "2026-08-21T06:15:25+00:00", row["actual_exit_time"], 101.0, row["notes"]),
        ("SJ-2", "bot_chento_v3_v1", "LONG", "open", "2026-08-22T06:15:25+00:00", None, None, row["notes"]),
        ("SJ-3", "bot_adx_v1", "LONG", "closed", "2026-08-21T06:15:25+00:00", row["actual_exit_time"], 1.0, row["notes"]),
        ("SJ-4", "bot_chento_v3_eth", "SHORT", "closed", "2026-09-12T00:00:01+00:00", row["actual_exit_time"], 1.0, row["notes"]),
    ])
    con.commit()
    con.close()


def _diag_files(tmp: Path):
    near = [b for b in L.P0_BARS if b["ledger"] is None]
    paths = {}
    for asset in L.ASSETS:
        p = tmp / f"diag_{asset}.jsonl"
        recs = [{"ts": b["t"], "direction": "long" if asset == "BTC" else "short", "reason": "okx_misaligned",
                 "okx_delta_z": b["z"]} for b in near if b["asset"] == asset]
        p.write_text(json.dumps({"utc_date": "x", "near_misses": recs}) + "\n", encoding="utf-8")
        paths[asset] = p
    return paths


def test_snapshot_cli_copies_below_cutoff_once(tmp_path):
    src, dest, res = tmp_path / "src.db", tmp_path / "snap" / "snapshot.db", tmp_path / "results"
    _source_db(src)
    diag = _diag_files(tmp_path)
    args = [str(STUDY / "snapshot.py"), "--source", str(src), "--dest", str(dest), "--results-dir", str(res),
            "--diag-btc", str(diag["BTC"]), "--diag-eth", str(diag["ETH"])]
    first = _run(args)
    assert first.returncode == 0, first.stdout + first.stderr
    meta = json.loads((res / "snapshot.json").read_text(encoding="utf-8"))
    assert meta["file_sha256"] == L.file_sha256(dest) and not os.access(dest, os.W_OK)
    con = sqlite3.connect(f"file:{dest.as_posix()}?mode=ro", uri=True)
    try:
        assert con.execute("SELECT MAX(timestamp) FROM cd_futures_15m").fetchone()[0] == L.SNAPSHOT_CUTOFF - 900
        assert con.execute("SELECT MAX(timestamp) FROM okx_perp_eth_1h").fetchone()[0] == L.SNAPSHOT_CUTOFF - 3600
        assert sorted(r[0] for r in con.execute("SELECT DISTINCT asset FROM ca_long_short_ratio")) == ["BTC", "ETH"]
        assert [r[0] for r in con.execute("SELECT id FROM p3_ledger")] == ["SJ-1"]
        cols = [r[1] for r in con.execute("PRAGMA table_info(p3_ledger)")]
        assert "pnl_usdt" not in cols
        assert con.execute("SELECT COUNT(*) FROM p0_bars").fetchone()[0] == 3
    finally:
        con.close()
    before = (res / "snapshot.json").read_bytes()
    second = _run(args)
    assert second.returncode == 2 and "REFUSED" in second.stdout
    assert (res / "snapshot.json").read_bytes() == before


def _study_fixture(tmp_path, *, parity_pass=True):
    """A tiny snapshot plus complete step 2-4 artefacts; no real data."""
    snap, res = tmp_path / "snapshot.db", tmp_path / "results"
    res.mkdir()
    con = sqlite3.connect(str(snap))
    for t in ("cd_futures_15m", "cd_futures_eth_15m"):
        con.execute(f"CREATE TABLE {t} (timestamp INTEGER PRIMARY KEY, high REAL, low REAL, close REAL)")
    con.executemany("INSERT INTO cd_futures_15m VALUES (?, 100.5, 99.5, 100.0)",
                    [(T0 + 900 * i,) for i in range(400)])
    con.execute("CREATE TABLE p3_ledger (id TEXT PRIMARY KEY, strategy_variant TEXT, direction TEXT, "
                "actual_entry_time TEXT, actual_exit_time TEXT, exit_price REAL, notes TEXT)")
    row = _ledger_row("tif_expiry")
    con.executemany("INSERT INTO p3_ledger VALUES (?, 'bot_chento_v3_v1', 'LONG', 'x', ?, 100.0, ?)",
                    [(i, row["actual_exit_time"], row["notes"]) for i in L.P3_IDS])
    con.commit()
    con.close()
    sha = L.file_sha256(snap)
    L.write_json_atomic(res / "snapshot.json", {"file_sha256": sha})
    L.write_json_atomic(res / "parity.json", {"pass": parity_pass,
                                              "provenance": L.provenance(sha, STUDY / "parity_check.py")})
    pools = {}
    for a in L.ASSETS:
        n = 6
        f = pd.DataFrame({"t": [L.iso(T0 + 86400 * i) for i in range(n)], "direction": ["long"] * n,
                          "entry": 100.0, "atr": 2.0, "risk": 10.0, "dist_R": float("inf"), "ret_30d": 0.0,
                          "z_R1": [0.5, -0.5] * 3, "z_R2": [0.5, -0.5] * 3, "anchor": True, "anchor_eod": True,
                          "okx_newest_R1": "", "okx_newest_R2": ""})
        f["t_epoch"] = [L.parse_ts(x) for x in f["t"]]
        m = L.membership(f.assign(t=f["t_epoch"]))
        for c in ("atr_drop", "in_off", "in_on_R1", "in_on_R2"):
            f[c] = m[c]
        f.drop(columns="t_epoch").to_csv(res / f"features_{a}.csv", index=False)
        f[["t", "direction"]].to_csv(res / f"pool_{a}.csv", index=False)
        off = m[m["in_off"]]
        L.write_json_atomic(res / f"fidelity_{a}.json", {
            "p2_numerator": n, "p2_denominator": n, "precision": {},
            "power_R1": {"n_off": int(len(off)), "n_K": int(off["in_on_R1"].sum()),
                         "n_B": int((~off["in_on_R1"]).sum())},
            "provenance": L.provenance(sha, STUDY / "bot_features.py", {f"pool_{a}.csv": res / f"pool_{a}.csv"}),
            "outputs_sha256": {f"features_{a}.csv": L.file_sha256(res / f"features_{a}.csv")}})
    L.write_json_atomic(res / "pool_parity.json", {
        "pass": True, "provenance": L.provenance(sha, STUDY / "gen_pool.py"),
        "outputs_sha256": {f"pool_{a}.csv": L.file_sha256(res / f"pool_{a}.csv") for a in L.ASSETS}})
    return snap, res


def _outcomes(snap, res, *extra):
    return _run([str(STUDY / "outcomes.py"), "--snapshot", str(snap), "--results-dir", str(res), *extra])


def test_outcomes_refuses_on_missing_artefact_or_hash_mismatch_and_writes_nothing(tmp_path):
    snap, res = _study_fixture(tmp_path)
    (res / "parity.json").unlink()
    listing = sorted(p.name for p in res.iterdir())
    r = _outcomes(snap, res, "--preconditions-only")
    assert r.returncode == 2 and "REFUSED" in r.stdout and sorted(p.name for p in res.iterdir()) == listing
    L.write_json_atomic(res / "parity.json", {"pass": True})
    L.write_json_atomic(res / "snapshot.json", {"file_sha256": "0" * 64})
    listing = sorted(p.name for p in res.iterdir())
    r = _outcomes(snap, res, "--preconditions-only")
    assert r.returncode == 2 and sorted(p.name for p in res.iterdir()) == listing


def test_outcomes_precondition_failure_is_invalid_and_prints_no_number(tmp_path):
    snap, res = _study_fixture(tmp_path, parity_pass=False)
    r = _outcomes(snap, res, "--preconditions-only")
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stdout.strip() == "INVALID" and not FLOAT_TOKEN.search(r.stdout)
    inv = json.loads((res / "invalid.json").read_text(encoding="utf-8"))
    assert inv["stage"] == "preconditions" and inv["failed_checks"] == ["P0", "POWER"]
    pre = json.loads((res / "preconditions.json").read_text(encoding="utf-8"))
    assert pre["P3"] is True and pre["P1"] is True
    assert not any((res / n).exists() for n in ("trades_BTC.csv", "trades_ETH.csv", "report_pre.json",
                                                  "n_trials.json", "verdict.json"))
    again = _outcomes(snap, res, "--preconditions-only")
    assert again.returncode == 2 and "rerun" in again.stdout


def test_outcomes_refuses_once_a_verdict_exists(tmp_path):
    snap, res = _study_fixture(tmp_path)
    L.write_json_atomic(res / "verdict.json", {"outcome": "RETIRE"})
    r = _outcomes(snap, res, "--preconditions-only")
    assert r.returncode == 2 and "final" in r.stdout
    assert not (res / "preconditions.json").exists()


def test_same_hour_report_refuses_without_a_verdict(tmp_path):
    snap, res = _study_fixture(tmp_path)
    L.write_json_atomic(res / "invalid.json", {"stage": "preconditions"})
    r = _run([str(STUDY / "same_hour_report.py"), "--snapshot", str(snap), "--results-dir", str(res)])
    assert r.returncode == 2 and not (res / "report_post.json").exists()


# ─── added after the pre-run code review ───────────────────────────────────────

def test_frozen_decision_constants():
    assert (L.N_TRIALS, L.BLOCK, L.N_ITER, L.SEED, L.QS) == (53, 30, 10000, 42, (0.05, 0.5, 0.95))
    assert (L.K3_B_MAX, L.P2_MIN, L.POWER_POOLED_MIN, L.POWER_ASSET_MIN) == (-0.025, 0.80, 30, 10)
    assert (L.P3_TOL_S, L.P3_PRICE_REL, L.P0_TOL) == (180.0, 1e-9, 1e-9)
    assert L.P3_IDS == ("SJ-4243", "SJ-4244", "SJ-4245", "SJ-4246", "SJ-4248", "SJ-4249")


def test_k3_clause_boundaries():
    assert L.k3_clause(0.1, -0.025) and not L.k3_clause(0.1, -0.0249)
    assert not L.k3_clause(-0.03, -0.025)                                  # K must beat B
    assert not L.k3_clause(float("nan"), -1.0) and not L.k3_clause(0.1, float("nan"))


def test_each_degenerate_gate_metrics_check():
    good = {"oos_sharpe_uplift": 1.0, "sign_stability": 1.0, "blocked_expectancy_bp": -10.0, "n_folds": 3}
    assert L.degenerate(good) == []
    for k in ("oos_sharpe_uplift", "sign_stability", "blocked_expectancy_bp"):
        assert L.degenerate({**good, k: float("nan")}) == [k]
    assert L.degenerate({**good, "n_folds": 2}) == ["n_folds_lt_3"]


def test_bootstraps_use_the_frozen_construction(monkeypatch):
    calls = []

    def spy(a, b, stat, **kw):
        calls.append((np.asarray(a), np.asarray(b), kw))
        return {"point": 0.0, "ci": [0.0, 0.0, 0.0], "p_gt_0": 0.0, "block": kw["block"], "n_iter": kw["n_iter"]}

    monkeypatch.setattr(benchmark, "paired_block_boot_diff", spy)
    L.bootstraps(["2021-04-01", "2021-04-01", "2026-09-08"], [1.0, 2.0, -1.0], [True, False, False])
    assert len(calls) == 2
    for a, b, kw in calls:
        assert a.shape == b.shape == (1987, 2)
        assert kw == dict(block=30, n_iter=10000, seed=42, qs=(0.05, 0.5, 0.95))
    (K, B, _), (B2, Z, _) = calls
    assert K[0].tolist() == [1.0, 1.0] and B[0].tolist() == [2.0, 1.0] and B[-1].tolist() == [-1.0, 1.0]
    assert (B2 == B).all() and (Z[:, 0] == 0).all() and (Z[:, 1] == 1).all()   # boot_B = blocked vs zero


def test_path_nonfinite_prices_counts_only_the_walk_path():
    b = _bars()
    assert L.path_nonfinite_prices(b, T0) == 0
    b.high[5] = np.nan
    b.close[72 * 4] = np.nan          # the TIF exit bar is on the path
    b.low[72 * 4 + 1] = np.nan        # the bar after it is not
    assert L.path_nonfinite_prices(b, T0) == 2


def test_mtm_marks_each_day_at_the_2345_close():
    b = _bars(n=400, overrides={55: (102.5, 101.5, 102.0), 151: (105.5, 104.5, 105.0),
                                288: (103.5, 102.5, 103.0)})
    w = L.walk_trade(b, T0, "long", 100.0, 10.0, 10.0)
    assert w.kind == "tif"
    rec = {"asset": "BTC", "t": T0, "direction": "long", "entry": 100.0, "risk": 10.0, "R": w.R,
           "cost_R": w.cost_R, "exit_bar_ts": w.exit_bar_ts, "entry_day": "2024-03-01"}
    out = L.mtm_daily_returns([rec], {"BTC": b})
    assert out == pytest.approx(0.02 * np.array([0.2, 0.3, -0.5, 0.3 - w.cost_R]))


def test_trade_close_drawdown_is_in_exit_order():
    tr = pd.DataFrame({"R": [-2.0, 2.0, -2.0], "exit_bar_ts": [200, 100, 300]})
    assert L.arm_stats(tr)["maxdd_R_trade_close"] == 4.0        # entry order would give 2.0
    assert metrics.max_drawdown(tr["R"].to_numpy()) == 2.0


def test_report_formulas():
    from statistics import NormalDist
    RK, RB = np.array([1.0, 2.0, 4.0]), np.array([0.0, -1.0, 1.0, 2.0])
    want = (NormalDist().inv_cdf(1 - 0.05 / 53) + NormalDist().inv_cdf(0.8)) * \
        math.sqrt(np.var(RK, ddof=1) / 3 + np.var(RB, ddof=1) / 4)
    assert L.mde(RK, RB) == pytest.approx(want)
    assert L.cap_binding(pd.DataFrame({"risk": [0.66, 0.67], "entry": [100.0, 100.0]})) == 1
    tr = pd.DataFrame({"R": [1.0, 2.0, -1.0], "entry_day": ["2021-04-01", "2021-04-01", "2021-04-03"]})
    s = np.zeros(1987)
    s[0], s[2] = 3.0, -1.0
    assert L.daily_sharpe_by_entry_day(tr) == metrics.daily_sharpe(s, 365.0)


def test_control_arm_flags():
    kb, bb = _shifted(11, 0.05)
    ke, be = _shifted(12, 0.05)
    tr = _frame({"BTC": kb, "ETH": ke}, {"BTC": bb, "ETH": be})
    sign = np.where(tr["direction"] == "long", 1.0, -1.0)
    tr["z_SH"] = np.where(tr["in_on_R1"], sign, -sign)            # same membership as R1
    same = S.control(tr, {"outcome": "RETIRE"})
    assert same["meets_full_retire_clause"] and same["required_statement"] == "does_not_bear_on_lookahead"
    assert not same["red_flag_causal_gap_exceeds_same_hour"]
    tr["z_SH"] = -tr["z_SH"]                                        # SH keeps what R1 blocks
    flipped = S.control(tr, {"outcome": "KEEP"})
    assert flipped["red_flag_causal_gap_exceeds_same_hour"] and flipped["delta_K_minus_B"]["pooled"] < 0


def test_record_phase_b_exception_is_invalid_with_no_message(tmp_path, capsys):
    def boom():
        raise ValueError("leak 1.2345")

    assert O.record_phase_b(tmp_path, {"phase": "B"}, boom) == 1
    assert capsys.readouterr().out.strip() == "INVALID"
    text = (tmp_path / "invalid.json").read_text(encoding="utf-8")
    assert "ValueError" in text and "1.2345" not in text and "leak" not in text
    assert (tmp_path / O.PHASE_B_MARKER).exists()
    assert not any((tmp_path / n).exists() for n in O.OUTCOME_FILES)


def test_record_phase_b_writes_the_verdict_last(tmp_path, monkeypatch, capsys):
    k, b = _keep_fixture(n=40)
    tr = _frame(k, b)
    dec = L.decide(tr)
    order = []
    real = L.write_json_atomic
    monkeypatch.setattr(L, "write_json_atomic", lambda p, o: (order.append(Path(p).name), real(p, o))[1])
    assert O.record_phase_b(tmp_path, {"phase": "B"}, lambda: (dec, tr, {"x": 1})) == 0
    assert order[0] == O.PHASE_B_MARKER and order[-1] == "verdict.json"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([*O.OUTCOME_FILES, O.PHASE_B_MARKER])


def test_record_phase_b_cleans_partial_outputs_on_a_late_interrupt(tmp_path, monkeypatch, capsys):
    k, b = _keep_fixture(n=40)
    tr = _frame(k, b)
    dec = L.decide(tr)
    real = L.write_json_atomic

    def failing(p, o):
        if Path(p).name == "verdict.json":
            raise KeyboardInterrupt
        return real(p, o)

    monkeypatch.setattr(L, "write_json_atomic", failing)
    assert O.record_phase_b(tmp_path, {"phase": "B"}, lambda: (dec, tr, {"x": 1})) == 1
    assert not any((tmp_path / n).exists() for n in O.OUTCOME_FILES)
    assert "KeyboardInterrupt" in (tmp_path / "invalid.json").read_text(encoding="utf-8")


def _listing(d):
    return sorted((p.name, p.stat().st_size) for p in d.iterdir())


def test_outcomes_phase_b_refuses_outside_the_study_results_dir(tmp_path):
    snap, res = _study_fixture(tmp_path)
    before = _listing(res)
    r = _outcomes(snap, res)
    assert r.returncode == 2 and "results/" in r.stdout and _listing(res) == before


def test_outcomes_refuses_tampered_artefacts_and_stale_outcome_files(tmp_path):
    snap, res = _study_fixture(tmp_path)
    f = res / "features_BTC.csv"
    f.write_text(f.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    r = _outcomes(snap, res, "--preconditions-only")
    assert r.returncode == 2 and "recorded" in r.stdout
    (tmp_path / "b").mkdir()
    snap2, res2 = _study_fixture(tmp_path / "b")
    (res2 / "trades_BTC.csv").write_text("x\n", encoding="utf-8")
    r2 = _outcomes(snap2, res2, "--preconditions-only")
    assert r2.returncode == 2 and "stale" in r2.stdout and not (res2 / "preconditions.json").exists()


def test_outcomes_refuses_membership_that_differs_from_its_recomputation(tmp_path):
    snap, res = _study_fixture(tmp_path)
    df = pd.read_csv(res / "features_BTC.csv")
    df.loc[0, "in_on_R1"] = not bool(df.loc[0, "in_on_R1"])
    df.to_csv(res / "features_BTC.csv", index=False)
    fid = json.loads((res / "fidelity_BTC.json").read_text(encoding="utf-8"))
    fid["outputs_sha256"]["features_BTC.csv"] = L.file_sha256(res / "features_BTC.csv")
    L.write_json_atomic(res / "fidelity_BTC.json", fid)
    r = _outcomes(snap, res, "--preconditions-only")
    assert r.returncode == 2 and "membership" in r.stdout


def test_outcomes_rerun_needs_a_committed_addendum_and_a_second_invalid_ends_the_study(tmp_path):
    snap, res = _study_fixture(tmp_path, parity_pass=False)
    assert _outcomes(snap, res, "--preconditions-only").returncode == 1
    assert _outcomes(snap, res, "--preconditions-only", "--rerun-addendum", "0" * 40).returncode == 2
    head = L.git("rev-parse", "HEAD")
    second = _outcomes(snap, res, "--preconditions-only", "--rerun-addendum", head)
    assert second.returncode == 1 and (res / "invalid_run1.json").exists() and (res / "invalid.json").exists()
    third = _outcomes(snap, res, "--preconditions-only", "--rerun-addendum", head)
    assert third.returncode == 2 and "second INVALID" in third.stdout

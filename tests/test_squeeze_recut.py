"""The squeeze twins' pre-registered re-cut rules.

n_paired is 0 for both sleeves today, so none of these clauses has ever been
evaluated against real data. That is exactly why they are tested now: when the
20th paired fire lands, the verdict must not depend on decision code written
under pressure, and the thresholds must not be quietly re-read.

Each test below drives `recut_lib.decide` with a synthetic ledger built to sit
on one side of one clause from docs/calibration/*.md.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
RECUT = ROOT / "studies" / "notebooks" / "squeeze_recut"
for _p in (str(ROOT), str(RECUT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

recut_lib = pytest.importorskip("recut_lib")

AS_OF = datetime(2026, 12, 1, tzinfo=timezone.utc)
BAR0 = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())


def _live(cfg, pairs, *, worst=None):
    """`pairs` is a list of (r_stop, r_nostop). One closed trade per side per
    bar, which is what a paired fire is."""
    rows = []
    for i, (rs, rn) in enumerate(pairs):
        bar = BAR0 + i * cfg.bar_step * 10
        for side, r in (("stop", rs), ("nostop", rn)):
            rows.append(dict(
                id=f"SJ-{i}{side[0]}", side=side, still_open=False,
                variant=(cfg.stop_id if side == "stop" else cfg.nostop_id),
                bar_ts=bar, r_live_net=r, risk_pct=0.02, size_usdt=5000.0,
                entry_time=datetime.fromtimestamp(bar, tz=timezone.utc).isoformat(),
                exit_time=(datetime.fromtimestamp(bar, tz=timezone.utc)
                           + timedelta(hours=1)).isoformat(),
                exit_reason="time_stop", backstop_exit=False,
                legacy_ref_stop=False, pnl_usdt=r * 100.0))
    if worst is not None:
        rows.append(dict(
            id="SJ-worst", side="nostop", still_open=False,
            variant=cfg.nostop_id, bar_ts=BAR0 - cfg.bar_step,
            r_live_net=worst, risk_pct=0.02, size_usdt=5000.0,
            entry_time="2026-09-30T00:00:00+00:00",
            exit_time="2026-09-30T06:00:00+00:00",
            exit_reason="time_stop", backstop_exit=False,
            legacy_ref_stop=False, pnl_usdt=worst * 100.0))
    return pd.DataFrame(rows)


def _union(live):
    if live.empty or "bar_ts" not in live.columns:
        return pd.DataFrame()
    bars = sorted(set(live.bar_ts))
    out = []
    for b in bars:
        sides = set(live[live.bar_ts == b].side)
        out.append(dict(
            bar_ts=b,
            bar_iso=datetime.fromtimestamp(b, tz=timezone.utc).isoformat(),
            taken_stop="stop" in sides, taken_nostop="nostop" in sides,
            klass=("BOTH" if sides == {"stop", "nostop"} else
                   "STOP_ONLY" if sides == {"stop"} else "NOSTOP_ONLY"),
            stop_block=None, nostop_block=None))
    return pd.DataFrame(out)


def _decide(key, pairs, *, n_gate=20, div=None, worst=None):
    cfg = recut_lib.SLEEVES[key]
    live = _live(cfg, pairs, worst=worst)
    return recut_lib.decide(cfg, live, _union(live),
                            div if div is not None else pd.DataFrame(),
                            AS_OF, n_gate)


def _clause(v, name):
    return next(c for c in v.clauses if c.name == name)


# ── the gate ───────────────────────────────────────────────────────────────

def test_below_the_gate_is_not_due_and_no_threshold_is_evaluated():
    """The thresholds are fixed in advance and 'are not to be edited when a
    fire disappoints' — nor read early, which would be the same thing."""
    v = _decide("squeeze_bull", [(0.1, -9.0)] * 19)
    assert v.outcome == "NOT_DUE"
    assert v.n_paired == 19
    # A catastrophic no-stop record must NOT produce a verdict before n = 20.
    assert [c.name for c in v.clauses] == ["D4_divergence"]
    assert "20 more needed" not in v.notes[0]
    assert "1 more" in v.notes[0] or "19 paired" in v.notes[0]


def test_gate_is_reached_at_exactly_n_gate():
    v = _decide("squeeze_bull", [(0.1, 0.3)] * 20)
    assert v.n_paired == 20
    assert v.outcome != "NOT_DUE"


# ── the clauses ────────────────────────────────────────────────────────────

def test_healthy_pair_continues():
    v = _decide("squeeze_bull", [(0.25, 0.30)] * 20)
    assert v.outcome == "CONTINUE"
    assert all(c.verdict in ("PASS", "n/a") for c in v.clauses)


def test_d1_nostop_mean_at_or_below_zero_disables():
    """'DISABLE the no-stop variant if its live mean R <= 0'."""
    v = _decide("squeeze_bull", [(0.3, 0.5)] * 10 + [(0.3, -0.5)] * 10)
    assert _clause(v, "D1_nostop_mean").verdict == "FAIL"
    assert v.outcome == "DISABLE_NOSTOP"


def test_d2_paired_gap_more_than_a_tenth_below_the_stop_variant_disables():
    """'or its paired mean R is more than 0.10 R below the stop variant's'."""
    v = _decide("squeeze_bull", [(0.50, 0.30)] * 20)      # gap -0.20
    assert _clause(v, "D2_paired_gap").verdict == "FAIL"
    assert v.outcome == "DISABLE_NOSTOP"


def test_d2_is_inclusive_at_exactly_a_tenth():
    """'more than 0.10 R below' — exactly 0.10 below is not a breach."""
    v = _decide("squeeze_bull", [(0.40, 0.30)] * 20)
    assert _clause(v, "D2_paired_gap").verdict == "PASS"


def test_d3_worst_trade_floor_disables():
    """squeeze_bull: 'or any single live trade prints below -6 R'."""
    v = _decide("squeeze_bull", [(0.3, 0.5)] * 20, worst=-6.5)
    assert _clause(v, "D3_worst_trade").verdict == "FAIL"
    assert v.outcome == "DISABLE_NOSTOP"


def test_d3_floor_differs_per_sleeve():
    """short_squeeze's floor is -10 R, not -6: its replay worst is -7.8."""
    assert recut_lib.SLEEVES["short_squeeze"].worst_trade_floor == -10.0
    assert recut_lib.SLEEVES["squeeze_bull"].worst_trade_floor == -6.0
    v = _decide("short_squeeze", [(0.3, 0.5)] * 20, worst=-7.8)
    assert _clause(v, "D3_worst_trade").verdict == "PASS"


def test_d4_divergence_fires_at_any_n_including_zero():
    """'Any time: DISABLE a variant whose live record diverges from the
    sleeve's own replay of the same fires by > 0.05 R on any trade.' This is
    the one clause not gated on the paired count."""
    div = pd.DataFrame([dict(id="SJ-1", side="nostop", bar_ts=BAR0,
                             r_live=0.1, r_replay=0.3, diff_R=-0.2,
                             funding_R=0.0, backstop_cost_R=0.0,
                             residual_R=-0.2, live_exit="time_stop",
                             replay_exit="tif", exit_matches=True)])
    v = _decide("squeeze_bull", [], div=div)
    assert _clause(v, "D4_divergence").verdict == "FAIL"
    assert v.outcome == "DISABLE_NOSTOP"
    assert v.n_paired == 0


def test_d4_reads_the_residual_not_the_raw_difference():
    """A difference fully explained by funding is not an execution fault."""
    div = pd.DataFrame([dict(id="SJ-1", side="nostop", bar_ts=BAR0,
                             r_live=0.1, r_replay=0.3, diff_R=-0.2,
                             funding_R=-0.2, backstop_cost_R=0.0,
                             residual_R=-0.005, live_exit="time_stop",
                             replay_exit="tif", exit_matches=True)])
    v = _decide("squeeze_bull", [], div=div)
    assert _clause(v, "D4_divergence").verdict == "PASS"
    assert v.outcome == "NOT_DUE"


# ── n = 30 only ────────────────────────────────────────────────────────────

def test_dsr_clause_only_exists_at_n_30():
    assert not any(c.name == "D5_dsr"
                   for c in _decide("squeeze_bull", [(0.2, 0.3)] * 20).clauses)
    assert any(c.name == "D5_dsr"
               for c in _decide("squeeze_bull", [(0.2, 0.3)] * 30,
                                n_gate=30).clauses)


def test_dsr_below_half_disables_at_n_30():
    """'plus a deflated Sharpe at a trial count of at least 30. DISABLE if
    DSR < 0.50.' A noisy series with a mean barely above zero should not
    survive 30 trials of selection."""
    pairs = [(0.2, 3.0), (0.2, -2.9)] * 15
    v = _decide("squeeze_bull", pairs, n_gate=30)
    assert _clause(v, "D5_dsr").verdict == "FAIL"
    assert v.outcome == "DISABLE_NOSTOP"
    assert recut_lib.N_TRIALS >= 30


def test_promotion_needs_a_full_tenth_of_an_r():
    """'Make the no-stop variant the fleet default ... only if its paired mean
    R is >= the stop variant's + 0.10 R'."""
    short = _decide("squeeze_bull", [(0.20, 0.29)] * 30, n_gate=30)
    assert _clause(short, "D6_promote").verdict == "FAIL"
    assert short.outcome == "CONTINUE"

    enough = _decide("squeeze_bull", [(0.20, 0.32)] * 30, n_gate=30)
    assert _clause(enough, "D6_promote").verdict == "PASS"
    assert enough.outcome == "CONTINUE_AND_CONSIDER_PROMOTION"


def test_short_squeeze_retires_when_both_variants_are_negative_at_n_30():
    """'If BOTH variants have mean R <= 0 at n = 30, retire the sleeve — the
    execution study's verdict stands.' squeeze_bull has no such clause."""
    v = _decide("short_squeeze", [(-0.2, -0.1)] * 30, n_gate=30)
    assert _clause(v, "D7_retire_sleeve").verdict == "FAIL"
    assert v.outcome == "RETIRE_SLEEVE"

    sb = _decide("squeeze_bull", [(-0.2, -0.1)] * 30, n_gate=30)
    assert not any(c.name == "D7_retire_sleeve" for c in sb.clauses)
    assert sb.outcome == "DISABLE_NOSTOP"       # D1 still fires


# ── plumbing the clauses depend on ─────────────────────────────────────────

def test_parse_notes_splits_the_json_head_from_the_appended_exit_text():
    raw = ('{"sleeve": "SQUEEZE_BULL", "bar_ts": 1789146000}\n'
           'SQUEEZE_BULL_EXIT: take_profit; pnl=+123.45')
    blob, reason = recut_lib.parse_notes(raw)
    assert blob["bar_ts"] == 1789146000
    assert reason == "take_profit"


def test_parse_notes_survives_a_notes_column_that_is_not_json():
    blob, reason = recut_lib.parse_notes("not json at all")
    assert blob == {} and reason is None
    assert recut_lib.parse_notes(None) == ({}, None)


def test_paired_frame_counts_only_bars_both_variants_closed():
    cfg = recut_lib.SLEEVES["squeeze_bull"]
    live = _live(cfg, [(0.1, 0.2), (0.3, 0.4)])
    # A third bar the stop variant took alone must not become a pair.
    live = pd.concat([live, pd.DataFrame([dict(
        id="SJ-solo", side="stop", still_open=False, variant=cfg.stop_id,
        bar_ts=BAR0 + 999, r_live_net=0.9, risk_pct=0.02, size_usdt=5000.0,
        entry_time="2026-10-05T00:00:00+00:00",
        exit_time="2026-10-05T06:00:00+00:00", exit_reason="time_stop",
        backstop_exit=False, legacy_ref_stop=False, pnl_usdt=90.0)])],
        ignore_index=True)
    assert len(recut_lib.paired_frame(live, _union(live))) == 2


def test_an_open_trade_is_not_a_paired_fire():
    cfg = recut_lib.SLEEVES["squeeze_bull"]
    live = _live(cfg, [(0.1, 0.2)])
    live.loc[live.side == "nostop", "still_open"] = True
    assert len(recut_lib.paired_frame(live, _union(live))) == 0


def test_sleeve_config_matches_the_calibration_logs():
    """Cheap guard against a config drift that would silently change which
    policy the replay compares."""
    sb = recut_lib.SLEEVES["squeeze_bull"]
    assert (sb.stop_policy, sb.nostop_policy) == ("P0_shipped", "P1b_target_only")
    assert (sb.tif_h, sb.bar_step, sb.bar_key) == (48, 3600, "bar_ts")
    ss = recut_lib.SLEEVES["short_squeeze"]
    assert (ss.stop_policy, ss.nostop_policy) == ("P0_shipped", "P1_time_only")
    assert (ss.tif_h, ss.bar_step, ss.bar_key) == (6, 900, "bar_ts_utc")
    assert ss.retire_sleeve_if_both_negative and not sb.retire_sleeve_if_both_negative

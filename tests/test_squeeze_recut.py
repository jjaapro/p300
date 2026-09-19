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
                             funding_R=0.0, booked_cost_R=0.0,
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
                             funding_R=-0.2, booked_cost_R=0.0,
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


# ── booked cost (changed 2026-09-14, README "Changed 2026-09-14") ──────────

def _ledger(tmp_path):
    """The columns load_live reads, and trade_adjustments as prod had it until
    BACKLOG 4.5: only UNIQUE(trade_id, seq), so duplicate CLOSE rows can exist."""
    import sqlite3
    con = sqlite3.connect(str(tmp_path / "ledger.db"))
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE trades (
        id TEXT PRIMARY KEY, strategy_variant TEXT, strategy TEXT, status TEXT,
        actual_entry_time TEXT, actual_exit_time TEXT, entry_price REAL,
        exit_price REAL, size_usdt REAL, current_size_usdt REAL, leverage REAL,
        pnl_usdt REAL, pnl_pct REAL, unique_key TEXT, notes TEXT)""")
    con.execute("""CREATE TABLE trade_adjustments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id TEXT, seq INTEGER,
        event_type TEXT, fee_usdt REAL, UNIQUE(trade_id, seq))""")
    return con


def test_booked_cost_comes_from_the_close_adjustment(tmp_path):
    """fee_usdt / notional, exactly — the notes round to whole bp. One row per
    trade even with a duplicate CLOSE (the first by seq). The notes suffix only
    when there is no CLOSE row, summing fee and slippage. Nothing for an open
    trade."""
    import json
    cfg = recut_lib.SLEEVES["squeeze_bull"]
    con = _ledger(tmp_path)
    head = json.dumps({"bar_ts": BAR0, "_reference_stop_price": 49_000.0})
    for tid, status, suffix in (
            ("SJ-exact", "closed", "\nSQUEEZE_BULL_EXIT: time_stop; fees=7bp RT, slip=0bp RT"),
            ("SJ-dup", "closed", "\nSQUEEZE_BULL_EXIT: take_profit; fees=7bp RT, slip=0bp RT"),
            ("SJ-legacy", "closed",
             "\nSQUEEZE_BULL_EXIT: scheduled_exit; fees=10bp RT, slip=5bp RT, funding=-0.086%"),
            ("SJ-open", "open", "")):
        con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (tid, cfg.stop_id, cfg.sleeve, status, "2026-10-01T00:00:00+00:00",
                     None, 50_000.0, None, 5_000.0, 5_000.0, 0.5, 1.0, 0.02,
                     f"k-{tid}", head + suffix))
    con.executemany(
        "INSERT INTO trade_adjustments (trade_id, seq, event_type, fee_usdt) VALUES (?,?,?,?)",
        [("SJ-exact", 1, "OPEN", 0.0), ("SJ-exact", 2, "CLOSE", 3.6),
         ("SJ-dup", 1, "OPEN", 0.0), ("SJ-dup", 2, "CLOSE", 3.5),
         ("SJ-dup", 3, "CLOSE", 9.0),
         ("SJ-legacy", 1, "OPEN", 0.0), ("SJ-open", 1, "OPEN", 0.0)])

    live = recut_lib.load_live(cfg, AS_OF, con=con)
    booked = dict(zip(live.id, live.booked_bp))

    assert sorted(live.id) == ["SJ-dup", "SJ-exact", "SJ-legacy", "SJ-open"]
    assert booked["SJ-exact"] == pytest.approx(7.2)     # not the notes' 7
    assert booked["SJ-dup"] == pytest.approx(7.0)       # seq 2, not seq 3's 18
    assert booked["SJ-legacy"] == pytest.approx(15.0)   # 10 + 5 from the notes
    assert pd.isna(booked["SJ-open"])
    assert pd.isna(recut_lib.booked_cost_bp(None, 5_000.0, "no suffix"))


def test_divergence_nets_booked_cost_on_every_closed_trade(monkeypatch):
    """Live books 10 bp on SHORT_SQUEEZE, the replay nets the E6 measured
    9.32 bp. That gap is in every closed trade, whatever closed it: a time stop
    and a backstop close that booked the same 10 bp get the same cost term, so
    the residual is zero on both. Until 2026-09-14 the time stop got no term
    (residual -0.034 R) and the backstop close got a hard-coded 15 bp one
    (-0.284 R), which alone trips D4's 0.05 R."""
    from strategies.support import funding
    monkeypatch.setattr(funding, "accrued_pct", lambda *a: 0.0)
    cfg = recut_lib.SLEEVES["short_squeeze"]
    risk = 0.002
    term = -((10.0 - recut_lib.sl.cost_bp("SHORT_SQUEEZE")) / 1e4) / risk
    live = pd.DataFrame([dict(
        id=f"SJ-{reason}", side="stop", still_open=False, bar_ts=BAR0 + i * 900,
        r_live_net=0.5 + term, risk_pct=risk, booked_bp=10.0,
        entry_time="2026-10-01T00:00:00+00:00", exit_time="2026-10-01T06:00:00+00:00",
        exit_reason=reason, backstop_exit=(reason == "scheduled_exit"))
        for i, reason in enumerate(("time_stop", "scheduled_exit"))])
    rep = pd.DataFrame([dict(bar_ts=BAR0 + i * 900, side="stop", r_net=0.5, kind="tif")
                        for i in range(2)])

    div = recut_lib.divergence(cfg, live, rep)

    assert term == pytest.approx(-0.034, abs=5e-4)
    assert list(div.booked_cost_R) == pytest.approx([term, term])
    assert list(div.residual_R) == pytest.approx([0.0, 0.0], abs=1e-12)
    assert list(div.backstop_exit) == [False, True]
    v = recut_lib.decide(cfg, live, _union(live), div, AS_OF, 20)
    assert _clause(v, "D4_divergence").verdict == "PASS"


def test_divergence_falls_back_to_the_old_backstop_cost_only_when_nothing_was_booked(
        monkeypatch):
    """A scheduled_exit close with no CLOSE row and no notes suffix can only
    be a pre-2026-09-14 backstop close, which booked 15 bp. Any other close
    with nothing recoverable gets no cost term."""
    from strategies.support import funding
    monkeypatch.setattr(funding, "accrued_pct", lambda *a: 0.0)
    cfg = recut_lib.SLEEVES["squeeze_bull"]
    live = pd.DataFrame([dict(
        id=f"SJ-{reason}", side="stop", still_open=False, bar_ts=BAR0 + i * 3600,
        r_live_net=0.3, risk_pct=0.02, booked_bp=float("nan"),
        entry_time="2026-10-01T00:00:00+00:00", exit_time="2026-10-02T00:00:00+00:00",
        exit_reason=reason, backstop_exit=(reason == "scheduled_exit"))
        for i, reason in enumerate(("time_stop", "scheduled_exit"))])
    rep = pd.DataFrame([dict(bar_ts=BAR0 + i * 3600, side="stop", r_net=0.3, kind="tif")
                        for i in range(2)])

    div = recut_lib.divergence(cfg, live, rep)

    assert list(div.booked_cost_R) == pytest.approx(
        [0.0, -((15.0 - recut_lib.sl.cost_bp("SQUEEZE_BULL")) / 1e4) / 0.02])


def test_divergence_nets_the_funding_a_long_paid_across_a_settlement(tmp_path, monkeypatch):
    """Real funding lookup, not a stub: a SHORT_SQUEEZE hold 06:00 -> 12:00
    crosses the 08:00 settlement at 0.01 %, which live booked as -0.01 % of
    notional. funding_R nets it, so a trade whose only differences are that
    funding and the booked cost has a zero residual. Until 2026-09-14 the
    lookup passed +1 for the direction, which raised inside accrued_pct:
    funding_R was NaN and the residual kept -0.033 R of pure funding."""
    import sqlite3

    from strategies.support import db as _db_mod
    ledger = tmp_path / "funding.db"
    con = sqlite3.connect(str(ledger))
    con.execute("CREATE TABLE cd_funding_rate (timestamp INTEGER, fr_close REAL)")
    settle = int(datetime(2026, 10, 1, 8, tzinfo=timezone.utc).timestamp())
    con.executemany("INSERT INTO cd_funding_rate VALUES (?, ?)",
                    [(settle - 28_800, 0.0005), (settle, 0.0001), (settle + 28_800, 0.0005)])
    con.commit()
    con.close()
    monkeypatch.setattr(_db_mod, "TRADER_DB", ledger)

    cfg = recut_lib.SLEEVES["short_squeeze"]
    risk = 0.003
    funding_r = -0.0001 / risk
    term = -((10.0 - recut_lib.sl.cost_bp("SHORT_SQUEEZE")) / 1e4) / risk
    live = pd.DataFrame([dict(
        id="SJ-funded", side="stop", still_open=False, bar_ts=BAR0,
        r_live_net=0.5 + term + funding_r, risk_pct=risk, booked_bp=10.0,
        entry_time="2026-10-01T06:00:00+00:00", exit_time="2026-10-01T12:00:00+00:00",
        exit_reason="time_stop", backstop_exit=False)])
    rep = pd.DataFrame([dict(bar_ts=BAR0, side="stop", r_net=0.5, kind="tif")])

    div = recut_lib.divergence(cfg, live, rep)

    assert funding_r == pytest.approx(-0.0333, abs=5e-5)
    assert div.funding_R.iloc[0] == pytest.approx(funding_r)
    assert div.booked_cost_R.iloc[0] == pytest.approx(term)
    assert div.residual_R.iloc[0] == pytest.approx(0.0, abs=1e-12)


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


# ── 2026-09-19: the voided trade, D4's label, quote timing ─────────────────

def _ledger_db(tmp_path, rows):
    """A prod-shaped trades / trade_adjustments pair with the columns load_live reads."""
    import sqlite3
    path = tmp_path / "prod.db"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE trades (id TEXT PRIMARY KEY, strategy_variant TEXT, strategy TEXT, status TEXT, "
                "actual_entry_time TEXT, actual_exit_time TEXT, entry_price REAL, exit_price REAL, size_usdt REAL, "
                "current_size_usdt REAL, leverage REAL, pnl_usdt REAL, pnl_pct REAL, unique_key TEXT, notes TEXT)")
    con.execute("CREATE TABLE trade_adjustments (trade_id TEXT, seq INTEGER, event_type TEXT, fee_usdt REAL)")
    for r in rows:
        con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
    con.commit()
    con.row_factory = sqlite3.Row
    return con


def test_voided_trades_are_struck_from_the_live_ledger(tmp_path):
    """SJ-4250 fired only on the mis-stamped open-interest table (item 30) and was voided by the operator
    (decision 8, 2026-09-19): it leaves the live ledger before anything is counted, and the report names it."""
    cfg = recut_lib.SLEEVES["squeeze_bull"]
    notes = ('{"trigger": "squeeze_bull_oi_flush", "bar_ts": 1789146000, "_stop_price": 75944.022}'
             "\nSQUEEZE_BULL_EXIT: time_stop; fees=7bp RT, slip=0bp RT, funding=-0.032%")
    con = _ledger_db(tmp_path, [
        ("SJ-4250", cfg.stop_id, "SQUEEZE_BULL", "closed", "2026-09-11T18:00:31+00:00",
         "2026-09-13T17:00:20+00:00", 77493.9, 77276.14, 5000.0, 5000.0, 0.5, -19.16, -0.38, "k1", notes),
        ("SJ-9001", cfg.stop_id, "SQUEEZE_BULL", "closed", "2026-10-01T18:00:31+00:00",
         "2026-10-03T17:00:20+00:00", 80000.0, 80800.0, 5000.0, 5000.0, 0.5, 45.0, 0.9, "k2",
         notes.replace("1789146000", "1790874000"))])
    live = recut_lib.load_live(cfg, AS_OF, con=con)
    assert list(live.id) == ["SJ-9001"]
    assert live.attrs["voided"] == {"SJ-4250": recut_lib.VOIDED_TRADES["squeeze_bull"]["SJ-4250"]}
    assert "item 30" in live.attrs["voided"]["SJ-4250"]
    assert recut_lib.VOIDED_TRADES["short_squeeze"] == {}


def test_d4_names_the_variant_that_diverged():
    """Before 2026-09-19 every D4 trip read DISABLE_NOSTOP, even SJ-4250's, a stop-variant trade."""
    def div_for(*sides):
        return pd.DataFrame([dict(id=f"SJ-{s}", side=s, bar_ts=BAR0, r_live=0.1, r_replay=0.3, diff_R=-0.2,
                                  funding_R=0.0, booked_cost_R=0.0, residual_R=-0.2, live_exit="time_stop",
                                  replay_exit="tif", exit_matches=True) for s in sides])
    assert _decide("squeeze_bull", [], div=div_for("stop")).outcome == "DISABLE_STOP"
    assert _decide("squeeze_bull", [], div=div_for("nostop")).outcome == "DISABLE_NOSTOP"
    assert _decide("squeeze_bull", [], div=div_for("stop", "nostop")).outcome == "DISABLE_BOTH"
    # at the gate too, and D4 outranks a promotion but not the other DISABLE clauses
    v = _decide("squeeze_bull", [(0.5, 0.6)] * 20, div=div_for("stop"))
    assert _clause(v, "D4_divergence").verdict == "FAIL" and v.outcome == "DISABLE_STOP"
    v = _decide("squeeze_bull", [(0.5, -0.1)] * 20, div=div_for("stop"))
    assert v.outcome == "DISABLE_NOSTOP"                       # D1 fails as well: the no-stop variant goes


def _quote_pair(gap_bars: float, live_exit: str = "time_stop", replay_kind: str = "tif"):
    """One closed stop-variant trade whose only difference from its replay is the 60 s poll: live entered 0.1
    above the replay's fill and exited 0.2 above its exit (a long, 2 % stop, entry 100)."""
    cfg = recut_lib.SLEEVES["squeeze_bull"]
    risk = 0.02
    booked_bp = 7.0
    price_r_live = (101.0 - 100.0) / (100.0 * risk)
    r_live_net = price_r_live - (booked_bp / 1e4) / risk
    price_r_rep = (100.8 - 99.9) / (99.9 * risk)
    r_rep_net = price_r_rep - (recut_lib.sl.cost_bp(cfg.sleeve) / 1e4) / risk
    exit_ts = BAR0 + 48 * 3600
    live = pd.DataFrame([dict(
        id="SJ-1", side="stop", still_open=False, bar_ts=BAR0, r_live_net=r_live_net, risk_pct=risk,
        entry_price=100.0, exit_price=101.0, size_usdt=5000.0, booked_bp=booked_bp,
        entry_time=datetime.fromtimestamp(BAR0 + 3600, tz=timezone.utc).isoformat(),
        exit_time=datetime.fromtimestamp(exit_ts, tz=timezone.utc).isoformat(),
        exit_reason=live_exit, backstop_exit=False)])
    rep = pd.DataFrame([dict(bar_ts=BAR0, side="stop", r_net=r_rep_net, kind=replay_kind,
                             fill_spot=99.9, exit_spot=100.8,
                             exit_ts=int(exit_ts - gap_bars * cfg.bar_step))])
    return cfg, live, rep


def test_divergence_nets_quote_timing_when_the_exits_match_within_two_bars(monkeypatch):
    from strategies.support import funding
    monkeypatch.setattr(funding, "accrued_pct", lambda *a: 0.0)
    cfg, live, rep = _quote_pair(gap_bars=1)
    div = recut_lib.divergence(cfg, live, rep)
    row = div.iloc[0]
    assert row.quote_drift_R == pytest.approx(((101.0 - 100.8) - (100.0 - 99.9)) / (100.0 * 0.02))   # +0.05 R
    assert row.exit_gap_bars == pytest.approx(1.0)
    assert abs(row.residual_R) < 0.002                          # the poll explained the whole difference
    assert _clause(recut_lib.decide(cfg, live, _union(live), div, AS_OF, 20), "D4_divergence").verdict == "PASS"


def test_quote_timing_is_not_netted_across_a_different_exit_or_a_late_exit(monkeypatch):
    from strategies.support import funding
    monkeypatch.setattr(funding, "accrued_pct", lambda *a: 0.0)
    cfg, live, rep = _quote_pair(gap_bars=3)                   # exits three bars apart: a fault, not the poll
    row = recut_lib.divergence(cfg, live, rep).iloc[0]
    assert row.quote_drift_R == 0.0 and row.exit_gap_bars == pytest.approx(3.0)
    assert abs(row.residual_R - 0.05) < 0.002
    cfg, live, rep = _quote_pair(gap_bars=0, live_exit="stop_hit", replay_kind="tif")
    row = recut_lib.divergence(cfg, live, rep).iloc[0]
    assert row.quote_drift_R == 0.0 and not row.exit_matches
    cfg, live, rep = _quote_pair(gap_bars=1)
    rep = rep.drop(columns=["exit_ts", "fill_spot", "exit_spot"])   # a replay without prices: no netting, no error
    row = recut_lib.divergence(cfg, live, rep).iloc[0]
    assert row.quote_drift_R == 0.0 and row.exit_gap_bars is None


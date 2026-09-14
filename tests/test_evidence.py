"""strategies/support/evidence.py — the results warnings (BACKLOG item 16).

Display only: LOSING_MONEY (red) when a variant's cumulative net P&L is below
zero at any n, BELOW_RESEARCH (amber) when its mean since `comparable_from`
is under the 10th percentile of resampled research means. These tests pin the
gates, not just the happy path: each rule has a twin on the other side of its
boundary, and the de-duplication is pinned on the shape of the August
doubled-fleet rows it exists for.

Every ledger here is a tmp sqlite file; nothing reads or writes prod.db.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import health
from strategies.support import evidence

REPO = Path(__file__).resolve().parents[1]

_COLS = ("id, asset, direction, strategy, strategy_variant, entry_time, "
         "exit_time, actual_entry_time, actual_exit_time, pnl_usdt, status, "
         "execution_mode")


def _ledger(tmp_path, rows, name="prod.db"):
    """rows: dicts with id, variant, pnl and optional strategy, direction,
    entry, exit, status, actual_entry, actual_exit."""
    p = tmp_path / name
    con = sqlite3.connect(str(p))
    con.execute(
        "CREATE TABLE trades (id TEXT PRIMARY KEY, asset TEXT, direction TEXT,"
        " strategy TEXT, strategy_variant TEXT, entry_time TEXT,"
        " exit_time TEXT, actual_entry_time TEXT, actual_exit_time TEXT,"
        " pnl_usdt REAL, status TEXT, execution_mode TEXT)")
    for i, r in enumerate(rows):
        # distinct default entry minutes, so defaults never look like doubles
        entry = r.get("entry", f"2026-09-20T{10 + i // 60:02d}:{i % 60:02d}:00+00:00")
        exit_ = r.get("exit", "2026-09-21T10:00:00+00:00")
        con.execute(
            f"INSERT INTO trades ({_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["id"], "BTC", r.get("direction", "LONG"),
             r.get("strategy", "S"), r["variant"], entry,
             r.get("scheduled_exit", exit_), r.get("actual_entry", entry),
             r.get("actual_exit", exit_), r["pnl"],
             r.get("status", "closed"), "paper"))
    con.commit()
    con.close()
    return p


def _ro(p, row_factory=False):
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    if row_factory:
        con.row_factory = sqlite3.Row
    return con


def _evaluate(p, **kw):
    con = _ro(p)
    try:
        return evidence.evaluate(con, **kw)
    finally:
        con.close()


def _codes(res, variant=None):
    """Red and amber codes (info lines left out)."""
    return [w["code"] for w in res["warnings"] if w["severity"] != "info"
            and (variant is None or w["variant"] == variant)]


def _row(res, variant):
    return next(r for r in res["variants"] if r["variant"] == variant)


def _baseline(values, since="2026-01-01T00:00:00+00:00"):
    return {"unit": "pct_of_capital", "n": len(values), "values": list(values),
            "comparable_from": since}


def _iso(day, hour=10, minute=0, second=0):
    return f"2026-09-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}+00:00"


SB = "bot_squeeze_bull_v1"
SB_NOSTOP = "bot_squeeze_bull_nostop_v1"
CHENTO = "bot_chento_v3_v1"
ADX = "bot_adx_v1"


# ─── scope ────────────────────────────────────────────────────────────────────

def test_scope_is_derived_from_bot_configs_like_health():
    scope, failures = evidence.live_variants()
    assert failures == []
    by_variant = {s["variant"]: s for s in scope}
    # health.py's own derivation, over health.BOT_CONFIGS
    import importlib
    want = []
    for bot in health.BOT_CONFIGS:
        cfg = importlib.import_module(f"bots.{bot}.config")
        want += [v["id"] for v in getattr(cfg, "VARIANTS", [])] or [cfg.VARIANT_ID]
    assert [s["variant"] for s in scope] == want
    assert by_variant[SB]["bot"] == by_variant[SB_NOSTOP]["bot"] == "squeeze_bull"
    assert by_variant["bot_short_squeeze_nostop_v1"]["bot"] == "short_squeeze"
    assert "p300_aggressive_v2_v1_0" not in by_variant
    assert all(s["capital_usdt"] == 10_000.0 for s in scope)


def test_scope_follows_a_config_change(monkeypatch):
    import bots.squeeze_bull.config as sb
    monkeypatch.setattr(sb, "VARIANTS", list(sb.VARIANTS) + [
        {"id": "bot_squeeze_bull_extra_v1", "short_name": "x", "use_stop": True}])
    scope, _ = evidence.live_variants()
    assert "bot_squeeze_bull_extra_v1" in {s["variant"] for s in scope}


def test_a_config_that_fails_to_import_is_a_visible_line(tmp_path):
    p = _ledger(tmp_path, [])
    res = _evaluate(p, bots=["adx", "no_such_bot"], baselines={})
    bad = [w for w in res["warnings"] if w["code"] == "EVIDENCE_UNAVAILABLE"]
    assert len(bad) == 1 and "no_such_bot" in bad[0]["text"]
    assert bad[0]["severity"] == "amber"
    assert [r["variant"] for r in res["variants"]] == [ADX]    # the rest still runs


def test_every_live_variant_has_a_readable_research_baseline():
    """Format check on the shipped bots/<bot>/research_baseline.json files."""
    scope, _ = evidence.live_variants()
    for bot in dict.fromkeys(s["bot"] for s in scope):
        entries = evidence.load_baseline(bot)
        for s in (s for s in scope if s["bot"] == bot):
            entry = entries[s["variant"]]
            assert entry["unit"] == "pct_of_capital"
            assert entry["n"] == len(entry["values"])


# ─── LOSING_MONEY (red) ───────────────────────────────────────────────────────

def test_red_at_n1_and_it_clears_when_the_total_is_back_at_zero(tmp_path):
    p = _ledger(tmp_path, [{"id": "SJ-4250", "variant": SB, "pnl": -19.16,
                            "strategy": "SQUEEZE_BULL"}])
    res = _evaluate(p, baselines={})
    red = [w for w in res["warnings"] if w["severity"] == "red"]
    assert [w["code"] for w in red] == ["LOSING_MONEY"]
    assert red[0]["variant"] == SB and red[0]["bot"] == "squeeze_bull"
    assert "-19.16 USDT" in red[0]["text"]
    assert "1 closed trade" in red[0]["text"]
    assert "too few trades to conclude anything" in red[0]["text"]
    assert _row(res, SB)["flags"] == ["LOSING_MONEY"]

    p2 = _ledger(tmp_path, [
        {"id": "SJ-4250", "variant": SB, "pnl": -19.16},
        {"id": "SJ-4260", "variant": SB, "pnl": 19.16,
         "exit": _iso(22)}], name="later.db")
    res = _evaluate(p2, baselines={})
    assert "LOSING_MONEY" not in _codes(res)                 # 0.00 clears
    assert _row(res, SB)["flags"] == []


@pytest.mark.parametrize("n, few", [(4, True), (5, False)])
def test_red_drops_the_too_few_words_from_five_trades(tmp_path, n, few):
    rows = [{"id": f"SJ-{i}", "variant": SB, "pnl": -1.0, "exit": _iso(10 + i)}
            for i in range(n)]
    res = _evaluate(_ledger(tmp_path, rows), baselines={})
    red = next(w for w in res["warnings"] if w["code"] == "LOSING_MONEY")
    assert f"{n} closed trades" in red["text"]
    assert ("too few trades to conclude anything" in red["text"]) is few


def test_open_trades_do_not_count(tmp_path):
    p = _ledger(tmp_path, [
        {"id": "SJ-1", "variant": SB, "pnl": 5.0},
        {"id": "SJ-2", "variant": SB, "pnl": -50.0, "status": "open"}])
    res = _evaluate(p, baselines={})
    assert _row(res, SB)["n"] == 1
    assert "LOSING_MONEY" not in _codes(res)


def test_squeeze_variants_are_judged_separately(tmp_path):
    """Both run the same signals with different exits. Pooled per bot this
    ledger is +20 and silent; per variant the stop variant is losing."""
    p = _ledger(tmp_path, [
        {"id": "SJ-1", "variant": SB, "pnl": -30.0},
        {"id": "SJ-2", "variant": SB_NOSTOP, "pnl": 50.0}])
    res = _evaluate(p, baselines={})
    assert _codes(res, SB) == ["LOSING_MONEY"]
    assert "LOSING_MONEY" not in _codes(res, SB_NOSTOP)
    assert _row(res, SB)["total_usdt"] == -30.0
    assert _row(res, SB_NOSTOP)["total_usdt"] == 50.0


def test_legacy_p300_variant_is_not_in_scope(tmp_path):
    p = _ledger(tmp_path, [{"id": "SJ-1", "variant": "p300_aggressive_v2_v1_0",
                            "pnl": -500.0}])
    res = _evaluate(p, baselines={})
    assert "p300_aggressive_v2_v1_0" not in {r["variant"] for r in res["variants"]}
    assert "LOSING_MONEY" not in _codes(res)


# ─── de-duplication ───────────────────────────────────────────────────────────

# bot_chento_v3_v1 on 2026-08-21..24, the doubled-fleet incident: every signal
# booked twice by two processes, entries seconds apart in the same minute.
AUGUST = [
    ("SJ-4243", "2026-08-21T06:15:25.500991+00:00", "2026-08-24T06:15:28.280631+00:00", 231.80226780070436),
    ("SJ-4244", "2026-08-21T06:15:58.170967+00:00", "2026-08-24T06:16:28.381514+00:00", 227.690158927827),
    ("SJ-4245", "2026-08-21T19:45:12.442313+00:00", "2026-08-24T19:45:20.968861+00:00", 154.00727969033431),
    ("SJ-4246", "2026-08-21T19:45:45.415128+00:00", "2026-08-24T19:46:21.043488+00:00", 152.9352385959613),
    ("SJ-4248", "2026-08-22T04:00:13.719775+00:00", "2026-08-22T05:15:17.881384+00:00", -215.5910070941614),
    ("SJ-4249", "2026-08-22T04:00:42.155888+00:00", "2026-08-22T05:15:17.901383+00:00", -215.58897880684933),
]


def _august_rows():
    return [{"id": i, "variant": CHENTO, "pnl": pnl, "entry": e, "exit": x,
             "strategy": "CHENTO_TRIPLE_V3"} for i, e, x, pnl in AUGUST]


def test_the_three_august_pairs_count_once_each(tmp_path):
    res = _evaluate(_ledger(tmp_path, _august_rows()), baselines={})
    row = _row(res, CHENTO)
    assert row["n"] == 3
    assert row["duplicates_ignored"] == 3
    assert [c["id"] for c in row["last_closes"]] == ["SJ-4248", "SJ-4243", "SJ-4245"]
    assert row["total_usdt"] == pytest.approx(231.80 + 154.01 - 215.59, abs=0.01)
    dup = next(w for w in res["warnings"] if w["code"] == "DUPLICATES_IGNORED")
    assert dup["severity"] == "info" and "3 duplicate rows ignored" in dup["text"]


def test_a_doubled_loss_does_not_flip_a_winning_variant_red(tmp_path):
    """Counted twice, the loss makes this ledger -50; counted once it is +50."""
    p = _ledger(tmp_path, [
        {"id": "SJ-10", "variant": CHENTO, "pnl": -100.0,
         "entry": _iso(1, 4, 0, 13), "exit": _iso(1, 5)},
        {"id": "SJ-11", "variant": CHENTO, "pnl": -100.0,
         "entry": _iso(1, 4, 0, 42), "exit": _iso(1, 5, 0, 1)},
        {"id": "SJ-12", "variant": CHENTO, "pnl": 150.0,
         "entry": _iso(2), "exit": _iso(3)}])
    res = _evaluate(p, baselines={})
    assert "LOSING_MONEY" not in _codes(res, CHENTO)
    assert _row(res, CHENTO)["total_usdt"] == 50.0


def test_dedupe_needs_same_minute_strategy_and_direction(tmp_path):
    base = {"variant": CHENTO, "pnl": 1.0}
    p = _ledger(tmp_path, [
        {**base, "id": "SJ-1", "entry": _iso(1, 4, 0, 59)},
        {**base, "id": "SJ-2", "entry": _iso(1, 4, 1, 0)},            # next minute
        {**base, "id": "SJ-3", "entry": _iso(1, 4, 0, 30), "direction": "SHORT"},
        {**base, "id": "SJ-4", "entry": _iso(1, 4, 0, 30), "strategy": "OTHER"},
    ])
    assert _row(_evaluate(p, baselines={}), CHENTO)["duplicates_ignored"] == 0


def test_dedupe_keeps_the_numerically_lowest_id(tmp_path):
    p = _ledger(tmp_path, [
        {"id": "SJ-1000", "variant": CHENTO, "pnl": -7.0, "entry": _iso(1, 4, 0, 1)},
        {"id": "SJ-999", "variant": CHENTO, "pnl": 3.0, "entry": _iso(1, 4, 0, 40)}])
    row = _row(_evaluate(p, baselines={}), CHENTO)
    assert [c["id"] for c in row["last_closes"]] == ["SJ-999"]


def test_todays_live_ledger_shape_gives_exactly_one_red(tmp_path):
    """The 2026-09-14 bot ledger: the August chento pairs, SJ-4250 on
    squeeze_bull, and the two open placeholder trades."""
    rows = _august_rows() + [
        {"id": "SJ-4250", "variant": SB, "pnl": -19.161338139904906,
         "strategy": "SQUEEZE_BULL", "entry": "2026-09-11T18:00:31.499362+00:00",
         "exit": "2026-09-13T17:00:20.244820+00:00"},
        {"id": "SJ-4242", "variant": "bot_carry_v1", "pnl": None, "status": "open"},
        {"id": "SJ-4247", "variant": ADX, "pnl": None, "status": "open"}]
    res = _evaluate(_ledger(tmp_path, rows))                 # the shipped baselines
    reds = [w for w in res["warnings"] if w["severity"] == "red"]
    assert [(w["code"], w["variant"]) for w in reds] == [("LOSING_MONEY", SB)]
    assert not [w for w in res["warnings"] if w["severity"] == "amber"]
    assert _row(res, CHENTO)["n"] == 3 and _row(res, CHENTO)["total_usdt"] > 0
    info = {(w["code"], w["variant"]) for w in res["warnings"] if w["severity"] == "info"}
    assert ("RESEARCH_SAMPLE_SMALL", "bot_carry_v1") in info


# ─── BELOW_RESEARCH (amber) ───────────────────────────────────────────────────

# Five draws from {-2, +3}: one win gives a mean of exactly -1.0, and P(0 or 1
# win) = 18.75% straddles the 10th percentile while P(0 wins) = 3.1% is below
# it, so the threshold is exactly -1.0 % of capital (a -100 USDT trade).
KNOWN = [-2.0, 3.0] * 20


def test_known_threshold():
    assert evidence.research_threshold(ADX, 5, tuple(KNOWN)) == -1.0


def test_threshold_is_the_10th_percentile_of_10000_resampled_means():
    """KNOWN's threshold is -1.0 at the 5th, 10th and 15th percentile alike, so
    it cannot pin the percentile. Means of 400 draws from {-1, +1} are close to
    Normal(0, 0.05) on a 0.005 lattice: the 10th percentile is -0.064 (-0.06 on
    the lattice), the 5th -0.082, the 15th -0.052."""
    assert (evidence.BOOTSTRAP_PERCENTILE, evidence.BOOTSTRAP_DRAWS) == (10, 10_000)
    thr = evidence.research_threshold(ADX, 400, tuple([-1.0, 1.0] * 50))
    assert thr == pytest.approx(-0.064, abs=0.005)


def _adx_trades(pnls, day0=10):
    return [{"id": f"SJ-{100 + i}", "variant": ADX, "pnl": pnl,
             "entry": _iso(day0 + i), "exit": _iso(day0 + i, 20)}
            for i, pnl in enumerate(pnls)]


def test_amber_boundary(tmp_path):
    at = _evaluate(_ledger(tmp_path, _adx_trades([-100.0] * 5), "at.db"),
                   bots=["adx"], baselines={ADX: _baseline(KNOWN)})
    assert "BELOW_RESEARCH" not in _codes(at)                # equal is not below
    assert _row(at, ADX)["comparable"]["threshold_pct"] == -1.0

    below = _evaluate(_ledger(tmp_path, _adx_trades([-100.0] * 4 + [-101.0]), "below.db"),
                      bots=["adx"], baselines={ADX: _baseline(KNOWN)})
    amber = [w for w in below["warnings"] if w["code"] == "BELOW_RESEARCH"]
    assert len(amber) == 1 and amber[0]["severity"] == "amber"
    t = amber[0]["text"]
    for part in ("live mean -1.002%", "research +0.500%", "threshold -1.000%",
                 "win rate 0% vs 50%", "n=5",
                 "research is in-sample; this is a prompt to look, not a verdict"):
        assert part in t, part
    assert _row(below, ADX)["flags"] == ["LOSING_MONEY", "BELOW_RESEARCH"]


def test_amber_needs_five_live_trades(tmp_path):
    res = _evaluate(_ledger(tmp_path, _adx_trades([-900.0] * 4)),
                    bots=["adx"], baselines={ADX: _baseline(KNOWN)})
    assert "BELOW_RESEARCH" not in _codes(res)
    assert "from n=5" in _row(res, ADX)["note"]


def test_the_amber_sample_is_de_duplicated(tmp_path):
    """Four trades plus a same-minute double: n=4, too few for the test. Counted
    raw, the double would reach n=5 and fire amber early."""
    rows = _adx_trades([-900.0] * 4) + [
        {"id": "SJ-199", "variant": ADX, "pnl": -900.0,
         "entry": _iso(10, 10, 0, 30), "exit": _iso(10, 20, 0, 5)}]
    res = _evaluate(_ledger(tmp_path, rows), bots=["adx"],
                    baselines={ADX: _baseline(KNOWN)})
    assert _row(res, ADX)["comparable"]["n"] == 4
    assert "BELOW_RESEARCH" not in _codes(res)


def test_the_threshold_is_drawn_at_the_live_n(tmp_path):
    """Ten trades at -0.6%: under the n=10 threshold (-0.5: P(<=2 wins of 10)
    is 5.5%, P(<=3) 17.2%) but above the n=5 one (-1.0)."""
    res = _evaluate(_ledger(tmp_path, _adx_trades([-60.0] * 10)), bots=["adx"],
                    baselines={ADX: _baseline(KNOWN)})
    assert _row(res, ADX)["comparable"]["threshold_pct"] == -0.5
    assert "BELOW_RESEARCH" in _codes(res)


def test_results_are_a_percentage_of_the_configs_capital(tmp_path, monkeypatch):
    import bots.adx.config as adx_config
    monkeypatch.setattr(adx_config, "CAPITAL_USDT", 5_000.0)
    # -100 USDT is -2% of 5,000 (it would be exactly the -1.0% threshold at 10,000)
    res = _evaluate(_ledger(tmp_path, _adx_trades([-100.0] * 5)), bots=["adx"],
                    baselines={ADX: _baseline(KNOWN)})
    row = _row(res, ADX)
    assert row["capital_usdt"] == 5_000.0
    assert row["last_closes"][-1]["pct"] == -2.0
    assert row["comparable"]["mean_pct"] == -2.0
    assert "BELOW_RESEARCH" in _codes(res)


def test_comparable_from_filters_the_amber_sample(tmp_path):
    since = _iso(15)
    # five dreadful trades before the cut, five fine ones from it (the first
    # exactly AT the cut, which counts)
    rows = _adx_trades([-900.0] * 5, day0=8) + _adx_trades([50.0] * 5, day0=15)
    for i, r in enumerate(rows):
        r["id"] = f"SJ-{200 + i}"
    res = _evaluate(_ledger(tmp_path, rows), bots=["adx"],
                    baselines={ADX: _baseline(KNOWN, since=since)})
    assert "BELOW_RESEARCH" not in _codes(res)
    assert _row(res, ADX)["comparable"]["n"] == 5
    assert "LOSING_MONEY" in _codes(res)             # red still sees every trade

    # the twin: the same trades, cut moved back so the bad ones count
    res = _evaluate(_ledger(tmp_path, rows, "early.db"), bots=["adx"],
                    baselines={ADX: _baseline(KNOWN, since=_iso(1))})
    assert "BELOW_RESEARCH" in _codes(res)


def test_research_sample_below_ten_is_info_not_amber(tmp_path):
    res = _evaluate(_ledger(tmp_path, _adx_trades([-900.0] * 6)), bots=["adx"],
                    baselines={ADX: _baseline([-2.0, 3.0] * 4 + [1.0])})
    assert "BELOW_RESEARCH" not in _codes(res)
    info = next(w for w in res["warnings"] if w["code"] == "RESEARCH_SAMPLE_SMALL")
    assert info["severity"] == "info"
    assert "research sample too small for a below-research test (n=9)" in info["text"]


def test_research_sample_of_exactly_ten_is_tested(tmp_path):
    res = _evaluate(_ledger(tmp_path, _adx_trades([-900.0] * 5)), bots=["adx"],
                    baselines={ADX: _baseline([-2.0, 3.0] * 5)})
    assert "BELOW_RESEARCH" in _codes(res)
    assert "RESEARCH_SAMPLE_SMALL" not in {w["code"] for w in res["warnings"]}


def test_carry_research_sample_is_too_small_today(tmp_path):
    res = _evaluate(_ledger(tmp_path, []), bots=["carry"])
    info = next(w for w in res["warnings"] if w["code"] == "RESEARCH_SAMPLE_SMALL")
    assert "(n=1)" in info["text"]


def test_missing_baseline_is_info(tmp_path):
    res = _evaluate(_ledger(tmp_path, _adx_trades([-900.0] * 6)), bots=["adx"],
                    baselines={})
    assert "BELOW_RESEARCH" not in _codes(res)
    w = next(w for w in res["warnings"] if w["code"] == "NO_RESEARCH_BASELINE")
    assert w["severity"] == "info" and "no research baseline" in w["text"]


def test_malformed_baseline_file_is_a_visible_line(tmp_path, monkeypatch):
    fake = tmp_path / "repo"
    (fake / "bots" / "adx").mkdir(parents=True)
    (fake / "bots" / "adx" / evidence.BASELINE_FILE).write_text("{not json",
                                                                encoding="utf-8")
    monkeypatch.setattr(evidence, "REPO", fake)
    res = _evaluate(_ledger(tmp_path, []), bots=["adx"])
    w = next(w for w in res["warnings"] if w["code"] == "EVIDENCE_UNAVAILABLE")
    assert "adx research baseline" in w["text"]


@pytest.mark.parametrize("content", ['{"variants": []}', '{"variants": 5}', "directory"])
def test_a_bad_baseline_costs_only_its_own_bot(tmp_path, monkeypatch, content):
    """A wrong-shaped (or unreadable) adx baseline must not hide squeeze_bull's
    red line along with it."""
    fake = tmp_path / "repo"
    path = fake / "bots" / "adx" / evidence.BASELINE_FILE
    if content == "directory":                     # exists, but read_text raises OSError
        path.mkdir(parents=True)
    else:
        path.parent.mkdir(parents=True)
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(evidence, "REPO", fake)
    p = _ledger(tmp_path, [{"id": "SJ-4250", "variant": SB, "pnl": -19.16}])
    res = _evaluate(p, bots=["adx", "squeeze_bull"])
    assert _codes(res, SB) == ["LOSING_MONEY"]
    bad = [w for w in res["warnings"] if w["code"] == "EVIDENCE_UNAVAILABLE"]
    assert len(bad) == 1 and "adx research baseline" in bad[0]["text"]


def test_threshold_is_the_same_in_every_process():
    """Seeded from sha256(variant, n), never Python's salted hash(): the
    monitor, the dashboard and tomorrow's run must all draw the same line."""
    # 40 distinct values: means of 7 draws rarely tie, so the 10th percentile
    # moves with the seed (a lattice of repeated values would hide a bad seed)
    values = tuple(round(math.sin(i * 1.7) * 2.1, 6) for i in range(40))
    here = evidence.research_threshold("bot_x_v1", 7, values)
    code = ("import sys; sys.dont_write_bytecode = True; "
            f"sys.path.insert(0, {str(REPO)!r}); "
            "from strategies.support import evidence; "
            f"print(repr(evidence.research_threshold('bot_x_v1', 7, {values!r})))")
    for seed in ("1", "2"):
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, check=True, timeout=120,
                             env=dict(os.environ, PYTHONHASHSEED=seed,
                                      PYTHONDONTWRITEBYTECODE="1"))
        assert out.stdout.strip() == repr(here)
    assert evidence.research_threshold("bot_x_v1", 8, values) != here


def test_threshold_is_cached_per_variant_and_n():
    values = tuple(float(i % 5 - 2) for i in range(30))
    evidence.research_threshold.cache_clear()
    evidence.research_threshold("bot_y_v1", 6, values)
    evidence.research_threshold("bot_y_v1", 6, values)
    assert evidence.research_threshold.cache_info().hits == 1


# ─── ledger reading ───────────────────────────────────────────────────────────

def test_closes_are_chronological_by_actual_exit(tmp_path):
    p = _ledger(tmp_path, [
        {"id": "SJ-1", "variant": SB, "pnl": 1.0, "entry": _iso(1), "exit": _iso(9)},
        {"id": "SJ-2", "variant": SB, "pnl": 2.0, "entry": _iso(2), "exit": _iso(3)},
        {"id": "SJ-3", "variant": SB, "pnl": 3.0, "entry": _iso(3),
         "actual_exit": None, "scheduled_exit": _iso(5)},    # falls back to exit_time
        {"id": "SJ-4", "variant": SB, "pnl": 4.0, "entry": _iso(4), "exit": _iso(4)},
    ])
    row = _row(_evaluate(p, baselines={}), SB)
    assert [c["id"] for c in row["last_closes"]] == ["SJ-4", "SJ-3", "SJ-1"]
    assert row["n"] == 4


def test_loader_accepts_a_readonly_connection_with_or_without_row_factory(tmp_path):
    p = _ledger(tmp_path, [{"id": "SJ-1", "variant": SB, "pnl": -2.0}])
    for rf in (False, True):
        con = _ro(p, row_factory=rf)
        try:
            res = evidence.evaluate(con, baselines={})
        finally:
            con.close()
        assert _codes(res, SB)[0] == "LOSING_MONEY"


def test_a_ledger_without_actual_entry_time_is_a_visible_line(tmp_path):
    p = tmp_path / "old.db"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE trades (id TEXT, strategy_variant TEXT, strategy TEXT,"
                " direction TEXT, entry_time TEXT, exit_time TEXT,"
                " actual_exit_time TEXT, pnl_usdt REAL, status TEXT)")
    con.commit()
    con.close()
    res = _evaluate(p, baselines={})
    assert res["variants"] == []
    w = next(w for w in res["warnings"] if w["code"] == "EVIDENCE_UNAVAILABLE")
    assert "actual_entry_time" in w["text"]


def test_results_are_json_serialisable(tmp_path):
    res = _evaluate(_ledger(tmp_path, _august_rows()))
    json.dumps(res)

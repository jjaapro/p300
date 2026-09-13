"""Byte-equivalence parity: ADX sleeve Tier-2 calibration vs the research
that validated it (studies/notebooks/adx_study, 2026-06-26 findings).

Layers:
  1. indicators.atr (new shared fn) vs harness.atr_series — byte-identical.
  2. Harness T2 config reproduces the findings-table golden numbers on the
     study's data window (end 2026-06-26): n=27, WR 56%, PF 6.89,
     ret +2483%, maxDD −15.1% — pins that today's harness+data still
     produce the studied result.
  3. Sleeve `_current_signal` (symmetric filter ON) agrees with the harness
     T2 entry list bar-by-bar: fires the same 27 entries, and blocks the
     counter-trend shorts the asymmetric baseline used to take.
  4. Funding-veto z mirrors the research zscore at the two dates the
     findings document: 2025-10-05 (z≈1.53, the vetoed ATH long) and
     2024-11-09 (z≈0.41, the kept breakout long).

Read-only against live prod.db; skipped when absent.
"""
from __future__ import annotations

import math

import pytest

from strategies.support import db as _db_mod

pytestmark = pytest.mark.skipif(
    not _db_mod.PROD_DB.exists() or _db_mod.PROD_DB.stat().st_size == 0,
    reason="real prod.db not available")

# The study ran ON 2026-06-26, whose forming daily bar was excluded by the
# loader — so the research data ends at the 2026-06-25 candle.
STUDY_END = "2026-06-25"


@pytest.fixture(scope="module")
def candles():
    from studies.notebooks.adx_study.harness import load_btc_daily
    c = load_btc_daily(end_date=STUDY_END)
    assert len(c) > 3000
    return c


def _short_filter_e150(ctx):
    """Verbatim semantics of experiments.short_filter_e150 (that module runs
    its whole sweep at import, so the 3-line gate is re-stated here using the
    harness-provided ctx['trend_ema'], which IS the EMA150 series)."""
    if ctx["new_dir"] == "short":
        te = ctx["trend_ema"]
        return te is not None and not math.isnan(te) and ctx["close"] < te
    return True


@pytest.fixture(scope="module")
def t2_result(candles):
    from studies.notebooks.adx_study.harness import run
    return run(candles, "2018-01-01", entry_gate=_short_filter_e150,
               exit_mode="adx_or_atr", atr_mult=4.0)


def test_atr_matches_research(candles):
    from strategies.support.indicators import atr as sleeve_atr
    from studies.notebooks.adx_study.harness import atr_series
    a = sleeve_atr(candles, 14)
    b = atr_series(candles, 14)
    assert len(a) == len(b)
    for i, (x, y) in enumerate(zip(a, b)):
        if math.isnan(x) or math.isnan(y):
            assert math.isnan(x) and math.isnan(y), i
        else:
            assert x == pytest.approx(y, rel=1e-12), i


def test_harness_t2_reproduces_findings_table(t2_result):
    m = t2_result
    assert m["n"] == 27
    assert m["wr"] == pytest.approx(55.6, abs=0.1)          # "56%" rounded
    assert m["pf"] == pytest.approx(6.89, abs=0.01)
    assert m["ret_pct"] == pytest.approx(2483, abs=2)
    assert m["max_dd"] == pytest.approx(-15.1, abs=0.1)


def test_sleeve_signal_matches_t2_entries(candles, t2_result):
    """The sleeve state machine (symmetric filter ON) must fire exactly
    where the harness T2 run entered, evaluated bar-by-bar on slices."""
    from bots.adx.strategy import signal as adx_sig
    from bots.adx.strategy.config import SYMMETRIC_TREND_FILTER

    assert SYMMETRIC_TREND_FILTER, "production flag must be ON for T2"

    by_dt = {c["dt"]: i for i, c in enumerate(candles)}
    for t in t2_result["trades"]:
        i = by_dt[t["entry_dt"]]
        sig = adx_sig._current_signal(candles[: i + 1])
        assert sig is not None, t["entry_dt"]
        assert sig["entry_sig"] == t["dir"], (
            f"{t['entry_dt']}: sleeve={sig['entry_sig']} harness={t['dir']}")


def test_sleeve_blocks_countertrend_shorts(candles, t2_result):
    """Shorts the asymmetric baseline took above EMA150 must now be blocked
    (the funding-harvest shorts T2 deliberately forfeits)."""
    from bots.adx.strategy import signal as adx_sig
    from studies.notebooks.adx_study.harness import run

    base = run(candles, "2018-01-01")           # asymmetric live baseline
    t2_entries = {t["entry_dt"] for t in t2_result["trades"]}
    dropped_shorts = [t for t in base["trades"]
                      if t["dir"] == "short" and t["entry_dt"] not in t2_entries]
    assert len(dropped_shorts) >= 5             # study: 7 filtered shorts

    by_dt = {c["dt"]: i for i, c in enumerate(candles)}
    for t in dropped_shorts:
        i = by_dt[t["entry_dt"]]
        sig = adx_sig._current_signal(candles[: i + 1])
        assert sig is not None and sig["entry_sig"] is None, t["entry_dt"]
        assert sig["entry_blocked_by_trend"], t["entry_dt"]


def test_funding_veto_z_matches_findings(candles):
    """findings.md addendum 2: Oct-5-2025 funding z=1.53 (vetoed),
    Nov-9-2024 z=0.41 (kept)."""
    from bots.adx.strategy import signal as adx_sig
    from bots.adx.strategy.config import FUNDING_VETO_Z

    by_dt = {c["dt"]: i for i, c in enumerate(candles)}

    z_ath = adx_sig._funding_z(candles[: by_dt["2025-10-05"] + 1])
    assert z_ath == pytest.approx(1.53, abs=0.02)
    assert z_ath > FUNDING_VETO_Z               # the target case is vetoed

    z_breakout = adx_sig._funding_z(candles[: by_dt["2024-11-09"] + 1])
    assert z_breakout == pytest.approx(0.41, abs=0.02)
    assert z_breakout < FUNDING_VETO_Z          # the healthy breakout passes


def test_harness_funding_option_charges_settlements(candles, t2_result):
    """`with_funding=True` (added 2026-09-12) keeps the same trades and lowers
    the return by the settlement funding the longs paid — the whole
    live-vs-research gap found by adx_robustness_2026_09. The default stays
    off so the findings table above still reproduces."""
    from studies.notebooks.adx_study.harness import run

    assert t2_result["with_funding"] is False
    m = run(candles, "2018-01-01", entry_gate=_short_filter_e150,
            exit_mode="adx_or_atr", atr_mult=4.0, with_funding=True)
    assert m["with_funding"] is True
    assert m["n"] == t2_result["n"]
    assert ([t["entry_dt"] for t in m["trades"]]
            == [t["entry_dt"] for t in t2_result["trades"]])
    # funding data starts 2019-09: earlier trades are charged nothing
    assert all(t["funding_pct"] == 0.0 for t in m["trades"] if t["exit_dt"] < "2019-09")
    longs = [t for t in m["trades"] if t["dir"] == "long" and t["entry_dt"] >= "2019-09"]
    assert longs
    assert sum(t["funding_pct"] for t in longs) < 0
    assert m["funding_pct_sum"] == pytest.approx(sum(t["funding_pct"] for t in m["trades"]))
    assert m["ret_pct"] < t2_result["ret_pct"]
    for a, b in zip(m["trades"], t2_result["trades"]):
        assert a["net_pct"] == pytest.approx(b["net_pct"] + a["funding_pct"])

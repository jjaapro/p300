"""End-to-end smoke run of every stage on a synthetic random-walk market (slow; opt-in).

    set ORB_SMOKE=1 && venv\\Scripts\\python.exe -m pytest studies/notebooks/orb_study/tests/test_run_smoke.py -q -s

The panel is a geometric random walk (5 bp per minute, open = previous close) with constant
funding, spanning the real panel dates, so every stage runs on the real calendars without touching
real prices. Two things are checked: the pipeline completes and writes every artefact and freeze,
and the engine has no built-in bias — P0's gross expectancy on a random walk is within three
standard errors of zero.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytestmark = pytest.mark.skipif(os.environ.get("ORB_SMOKE") != "1", reason="slow; set ORB_SMOKE=1")


def synthetic_market():
    import orb_data
    import orb_engine as eng
    n = orb_data.n_minutes()
    rng = np.random.default_rng(2026)
    steps = rng.normal(0.0, 5e-4, n)
    close = 10_000.0 * np.exp(np.cumsum(steps))
    open_ = np.concatenate([[10_000.0], close[:-1]])
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, 2e-4, n)))
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 2e-4, n)))
    vol = 1.0 + rng.exponential(1.0, n)
    t0 = orb_data.ms(orb_data.PANEL_START)
    panel = {"t0_ms": t0, "open": open_, "high": high, "low": low, "close": close, "volume": vol,
             "quote_volume": vol * close}
    slots = np.arange(0, n, 480)
    funding = {"time_ms": t0 + slots * 60_000, "rate": np.full(len(slots), 1e-4)}
    return eng.Market.from_panel("SYNTH", panel, funding, 0.01, 0.01, None)


def test_all_stages_on_a_random_walk(tmp_path, monkeypatch):
    import orb_metrics as met
    import orb_run
    mkt = synthetic_market()
    monkeypatch.setattr(orb_run, "load_market", lambda symbol: mkt)
    monkeypatch.setattr(orb_run, "STUDY", tmp_path)
    monkeypatch.setattr(orb_run, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(orb_run, "RUN", tmp_path / "results" / "run_v1")
    monkeypatch.setattr(orb_run, "verify_f0", lambda: None)
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "freeze_F0.json").write_text("{}")

    dev = orb_run.stage_development()
    assert (tmp_path / "results" / "freeze_F1.json").exists()
    assert len(dev["stepm"]["t_stats"]) == 46
    p0 = pd.read_csv(tmp_path / "results" / "run_v1" / "development" / "SYNTH" / "P0.csv.gz")
    gross = met.trades(p0)["gross_bp"]
    se = gross.std(ddof=1) / np.sqrt(len(gross))
    print(f"\nP0 on a random walk: n={len(gross)} gross mean {gross.mean():+.2f} bp, se {se:.2f}")
    assert abs(gross.mean()) < 3 * se

    val = orb_run.stage_validation()
    assert (tmp_path / "results" / "freeze_F2.json").exists()
    assert val["continuation"]["decision"] in ("open", "closed")
    verdict = orb_run.stage_lockbox()
    body = json.loads((tmp_path / "results" / "freeze_verdict.json").read_text())
    assert set(body["verdicts"]) == set(json.loads((tmp_path / "results" / "freeze_F1.json").read_text())["candidates"])
    print("synthetic verdicts:", verdict["verdicts"], "| lockbox:", verdict["lockbox"])
    with pytest.raises(SystemExit):
        orb_run.stage_development()                     # a freeze is never overwritten


def test_two_candidates_and_an_opened_lockbox(tmp_path, monkeypatch):
    """Exercise the code paths a random walk never reaches: a selected challenger and an open lockbox."""
    import orb_run
    mkt = synthetic_market()
    monkeypatch.setattr(orb_run, "load_market", lambda symbol: mkt)
    monkeypatch.setattr(orb_run, "STUDY", tmp_path)
    monkeypatch.setattr(orb_run, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(orb_run, "RUN", tmp_path / "results" / "run_v1")
    monkeypatch.setattr(orb_run, "verify_f0", lambda: None)
    real_select = orb_run.select_challenger

    def forced(summary, yearly, p0_sharpe):
        out = real_select(summary, yearly, p0_sharpe)
        out.update(challenger="X_TRAIL_NOTIME", reason="forced by smoke test")
        return out

    monkeypatch.setattr(orb_run, "select_challenger", forced)
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "freeze_F0.json").write_text("{}")
    dev = orb_run.stage_development()
    assert dev["candidates"] == ["P0", "X_TRAIL_NOTIME"] and set(dev["dsr"]) == {"P0", "X_TRAIL_NOTIME"}
    orb_run.stage_validation()
    f2_path = tmp_path / "results" / "freeze_F2.json"
    f2 = json.loads(f2_path.read_text())
    f2["lockbox"] = "open"
    f2_path.write_text(json.dumps(f2))
    verdict = orb_run.stage_lockbox()
    assert verdict["lockbox"] == "opened" and len(verdict["p_values"]) == 6
    assert set(verdict["holm_rejected"]) == set(verdict["p_values"])
    print("\nforced-open verdicts:", verdict["verdicts"])

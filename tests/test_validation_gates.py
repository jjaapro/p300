"""studies.lib.validation.gates -- GATE_VALIDATION.md protocol as code."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from studies.lib.validation import gates


def _dates(n: int, start: date = date(2020, 1, 1), step: int = 1) -> list[str]:
    return [(start + timedelta(days=i * step)).isoformat() for i in range(n)]


def test_presets_match_gate_validation_doc():
    assert gates.FOLD_PRESETS == {
        "daily": (365, 182, 182),
        "event": (730, 365, 365),
        "quarterly": (1460, 730, 730),
    }


def test_folds_do_not_overlap_and_cover_series():
    dates = _dates(4 * 365)
    fit, oos, step = gates.FOLD_PRESETS["daily"]
    folds = gates.walk_forward_folds(dates, fit, oos, step)
    assert len(folds) >= 5
    seen: list[int] = []
    for f in folds:
        assert f["fit_idx"] and f["oos_idx"]
        assert max(f["fit_idx"]) < min(f["oos_idx"])          # fit strictly before OOS
        assert f["fit_end"] == f["oos_start"]
        assert dates[f["fit_idx"][0]] == f["fit_start"]
        assert dates[f["oos_idx"][0]] == f["oos_start"]
        seen.extend(f["oos_idx"])
    assert len(seen) == len(set(seen))                         # no OOS overlap
    assert seen == list(range(folds[0]["oos_idx"][0], len(dates)))   # contiguous to the end
    assert folds[0]["fit_start"] == "2020-01-01"
    assert folds[0]["fit_end"] == "2020-12-31"
    assert folds[0]["oos_end"] == "2021-07-01"
    assert folds[0]["oos_idx"][0] == 365
    assert folds[1]["fit_start"] == "2020-07-01"


def test_folds_on_sparse_event_series():
    dates = _dates(4 * 52, step=7)                # weekly fires over ~4 years
    fit, oos, step = gates.FOLD_PRESETS["event"]
    folds = gates.walk_forward_folds(dates, fit, oos, step)
    assert len(folds) == 2
    for f in folds:
        assert all(f["fit_start"] <= dates[i] < f["fit_end"] for i in f["fit_idx"])
        assert all(f["oos_start"] <= dates[i] < f["oos_end"] for i in f["oos_idx"])
    with pytest.raises(ValueError):
        gates.walk_forward_folds(dates[::-1], fit, oos, step)
    with pytest.raises(ValueError):
        gates.walk_forward_folds(dates, 0, oos, step)
    assert gates.walk_forward_folds([], fit, oos, step) == []


def test_stitched_oos_length_is_sum_of_oos_lengths():
    dates = _dates(1500)
    folds = gates.walk_forward_folds(dates, 365, 182, 182)
    vals = np.arange(1500, dtype=float)
    st = gates.stitched_oos(folds, vals)
    assert len(st) == sum(len(f["oos_idx"]) for f in folds)
    assert list(st) == sorted(st)                             # time order
    assert st[0] == folds[0]["oos_idx"][0]
    assert gates.stitched_oos_indices(folds)[-1] == 1499
    with pytest.raises(IndexError):
        gates.stitched_oos(folds, vals[:100])


def test_promotion_verdict_passes_clear_uplift_and_fails_null():
    rng = np.random.default_rng(1)
    n = 1500
    dates = _dates(n)
    off = 0.02 + 1.0 * rng.standard_normal(n)      # % per fire, ungated
    on = off + 0.5                                  # modulator adds 50bp per fire
    folds = gates.walk_forward_folds(dates, *gates.FOLD_PRESETS["daily"])

    m = gates.gate_metrics(on, off, folds, periods_per_year=365)
    assert m["expectancy_uplift_bp"] == pytest.approx(50.0)
    assert m["n_on"] == m["n_off"] == len(gates.stitched_oos(folds, off))
    assert m["oos_sharpe_uplift"] > 0.2 and m["sign_stability"] == 1.0
    assert m["n_folds"] == len(folds) and m["n_blocked"] == 0
    v = gates.promotion_verdict(m, grid_size=1, kind="modulator")
    assert v["pass"] and all(c[2] for c in v["criteria"].values())
    assert set(v["criteria"]) == {"expectancy_uplift_bp", "oos_sharpe_uplift_deflated", "sign_stability"}

    null = gates.gate_metrics(off, off, folds)
    vn = gates.promotion_verdict(null, grid_size=12)
    assert not vn["pass"]
    assert vn["criteria"]["expectancy_uplift_bp"][0] == pytest.approx(0.0)
    assert vn["criteria"]["sign_stability"][0] == 0.0

    # deflation by sqrt(|grid|) unless a reality check was run
    v16 = gates.promotion_verdict(m, grid_size=16)
    assert v16["criteria"]["oos_sharpe_uplift_deflated"][0] == pytest.approx(m["oos_sharpe_uplift"] / 4)
    vrc = gates.promotion_verdict(m, grid_size=16, reality_check=True)
    assert vrc["criteria"]["oos_sharpe_uplift_deflated"][0] == pytest.approx(m["oos_sharpe_uplift"])
    with pytest.raises(ValueError):
        gates.promotion_verdict(m, grid_size=1, kind="other")


def test_binary_gate_uses_blocked_expectancy():
    rng = np.random.default_rng(2)
    n = 1500
    dates = _dates(n)
    off = 0.02 + 1.0 * rng.standard_normal(n)
    on = off.copy()
    on[off < -1.0] = np.nan                         # oracle block of the bad tail
    folds = gates.walk_forward_folds(dates, *gates.FOLD_PRESETS["daily"])
    m = gates.gate_metrics(on, off, folds)
    assert m["n_blocked"] > 0 and m["n_on"] == m["n_off"] - m["n_blocked"]
    assert m["blocked_expectancy_bp"] < -100.0
    assert m["expectancy_uplift_bp"] > 5.0
    v = gates.promotion_verdict(m, grid_size=4, kind="binary")
    assert v["pass"]
    assert v["criteria"]["blocked_expectancy_bp"][2]
    # a modulator verdict on a series with no blocked fires cannot use that criterion
    vm = gates.promotion_verdict(gates.gate_metrics(off, off, folds), grid_size=4, kind="binary")
    assert not vm["criteria"]["blocked_expectancy_bp"][2]


def test_ledger_persists_and_totals(tmp_path):
    p = tmp_path / "ledger" / "n_trials.json"
    led = gates.n_trials_ledger(p)
    assert led.total() == 0 and led.families() == []
    led.add("vol_gate", 12, "window x pctile grid")
    led.add("vol_gate", 3, "extra windows")
    led.add("fomc", 7, "phase set")
    assert led.total("vol_gate") == 15
    assert led.total("fomc") == 7
    assert led.total() == 22
    assert led.total("nope") == 0
    reloaded = gates.n_trials_ledger(p)                # fresh object reads the JSON
    assert reloaded.total("vol_gate") == 15
    assert reloaded.families() == ["fomc", "vol_gate"]
    assert [e["tag"] for e in reloaded.entries("vol_gate")] == ["window x pctile grid", "extra windows"]
    assert all("ts" in e for e in reloaded.entries())
    with pytest.raises(ValueError):
        led.add("", 1)

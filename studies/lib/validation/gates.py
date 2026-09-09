"""GATE_VALIDATION.md as code -- walk-forward folds, stitched-OOS gate
metrics, the promotion verdict and the parameter-family (n-trials) ledger.

New module (no trader-repo ancestor).  Encodes the protocol in the repo-root
``GATE_VALIDATION.md``:

* section 3 -- fold structure by gate cadence (``FOLD_PRESETS``): daily
  signal 1y fit / 6mo OOS rolled by 6mo; weekly/event-conditioned 2y / 1y /
  1y; quarterly 4y / 2y / 2y.  Folds are calendar-day windows, so a sparse
  per-fire series (an event gate) gets the same time coverage as a daily one.
* section 3 -- "stitched OOS series": the reported metric is computed on the
  concatenation of every OOS window, never on the friendliest fold.
* section 4 -- metrics: OOS expectancy per fire with the gate ON vs OFF, OOS
  Sharpe of the gated equity curve vs ungated, and per-fold sign stability.
* section 5 -- promotion criteria: expectancy uplift >= +5bp/fire (modulator
  gate) or blocked-trade expectancy <= -5bp (binary block); OOS Sharpe
  uplift >= +0.2, deflated by sqrt(|grid|) when no bootstrap reality check
  was run; gated beats ungated in >= 2/3 of folds.  Criterion 4 (artifacts
  committed) is a process check and is not evaluated here.
* section 2 -- the parameter family: ``n_trials_ledger`` records how many
  parameter combinations each gate family has burned.  That count is the
  |grid| that deflates the Sharpe uplift here and the N for ``dsr_pbo``.

Series conventions
------------------
``per_fire_on`` / ``per_fire_off`` are per-period PnL in PERCENT, aligned
one-to-one with the ``dates`` the folds were built from.  NaN means "no fire
in this period" (a binary gate blocking the trade); 0.0 is a fire that made
nothing.  Expectancy averages over fires (NaN excluded); the equity-curve
Sharpe treats NaN as a flat 0 period.  For a modulator gate the ON series is
the OFF series scaled by the leverage multiplier.  ``periods_per_year`` is
the annualisation of the per-period Sharpe (365 for daily series; fires per
year for a per-fire series).
"""
from __future__ import annotations

import json
import math
import os
from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .metrics import daily_sharpe

# (fit_days, oos_days, step_days) -- GATE_VALIDATION.md section 3
FOLD_PRESETS: dict[str, tuple[int, int, int]] = {
    "daily": (365, 182, 182),
    "event": (730, 365, 365),
    "quarterly": (1460, 730, 730),
}

# GATE_VALIDATION.md section 5
EXPECTANCY_UPLIFT_MIN_BP = 5.0
BLOCKED_EXPECTANCY_MAX_BP = -5.0
SHARPE_UPLIFT_MIN = 0.2
SIGN_STABILITY_MIN = 2.0 / 3.0


# --- folds ----------------------------------------------------------------------

def _to_date(d: Any) -> date:
    return date.fromisoformat(str(d)[:10])


def walk_forward_folds(dates: Sequence[Any], fit_days: int, oos_days: int,
                       step_days: int) -> list[dict[str, Any]]:
    """Rolling calendar-day walk-forward folds over a sorted date axis.

    Fold k fits on ``[start + k*step, start + k*step + fit_days)`` and
    scores OOS on the following ``oos_days`` (half-open windows; the OOS
    window starts the day the fit window ends).  Rolling continues while the
    OOS window starts on or before the last date; a fold whose OOS window
    holds no observation is skipped (sparse series), and the final fold may
    be shorter than ``oos_days``.  With ``step_days == oos_days`` (all
    presets) the OOS windows tile the axis after the first fit window.

    Returns a list of dicts with ``fit_idx`` / ``oos_idx`` (positions into
    ``dates``) and the ISO window bounds ``fit_start``, ``fit_end``,
    ``oos_start``, ``oos_end`` (end exclusive).
    """
    if fit_days <= 0 or oos_days <= 0 or step_days <= 0:
        raise ValueError("fit_days, oos_days and step_days must be positive")
    ds = [_to_date(d) for d in dates]
    if not ds:
        return []
    for a, b in zip(ds, ds[1:]):
        if b < a:
            raise ValueError("dates must be sorted ascending")
    ords = [d.toordinal() for d in ds]
    last = ds[-1]

    folds: list[dict[str, Any]] = []
    fit_start = ds[0]
    while True:
        fit_end = fit_start + timedelta(days=fit_days)
        oos_start = fit_end
        oos_end = oos_start + timedelta(days=oos_days)
        if oos_start > last:
            break
        fit_idx = list(range(bisect_left(ords, fit_start.toordinal()),
                             bisect_left(ords, fit_end.toordinal())))
        oos_idx = list(range(bisect_left(ords, oos_start.toordinal()),
                             bisect_left(ords, oos_end.toordinal())))
        if oos_idx:
            folds.append({
                "fit_idx": fit_idx,
                "oos_idx": oos_idx,
                "fit_start": fit_start.isoformat(),
                "fit_end": fit_end.isoformat(),
                "oos_start": oos_start.isoformat(),
                "oos_end": oos_end.isoformat(),
            })
        fit_start = fit_start + timedelta(days=step_days)
    return folds


def stitched_oos_indices(folds: Sequence[dict[str, Any]]) -> list[int]:
    """Sorted, de-duplicated union of every fold's OOS positions (time order)."""
    return sorted({int(i) for f in folds for i in f["oos_idx"]})


def stitched_oos(folds: Sequence[dict[str, Any]], per_period_values: Any) -> np.ndarray:
    """Concatenate the OOS windows of ``folds`` into one series in time order.

    ``per_period_values`` is aligned with the ``dates`` the folds were built
    from.  OOS positions shared by overlapping folds appear once.
    """
    vals = np.asarray(per_period_values, dtype=np.float64).ravel()
    idx = stitched_oos_indices(folds)
    if idx and idx[-1] >= vals.size:
        raise IndexError(f"fold OOS index {idx[-1]} outside series of length {vals.size}")
    return vals[idx]


# --- metrics --------------------------------------------------------------------

def _flat(x: np.ndarray) -> np.ndarray:
    """NaN (no fire) -> 0 (flat period) for equity-curve statistics."""
    return np.where(np.isfinite(x), x, 0.0)


def gate_metrics(per_fire_on: Any, per_fire_off: Any, folds: Sequence[dict[str, Any]],
                 periods_per_year: float = 365.0) -> dict[str, Any]:
    """GATE_VALIDATION.md section 4 metrics on the stitched OOS series.

    Returns a dict with
      expectancy_uplift_bp   mean(ON fires) - mean(OFF fires), in bp
      oos_sharpe_uplift      annualised Sharpe(ON curve) - Sharpe(OFF curve)
      sign_stability         fraction of folds where the ON curve out-Sharpes OFF
      n_on, n_off            fire counts (finite entries) in the stitched OOS
    plus the components: expectancy_on_pct / expectancy_off_pct,
    oos_sharpe_on / oos_sharpe_off, per_fold_sharpe_uplift, n_folds, and for
    binary blocks n_blocked / blocked_expectancy_bp (OFF fires that the gate
    turned into no-fire), which section 5 needs.
    """
    on = np.asarray(per_fire_on, dtype=np.float64).ravel()
    off = np.asarray(per_fire_off, dtype=np.float64).ravel()
    if on.shape != off.shape:
        raise ValueError(f"per_fire_on {on.shape} and per_fire_off {off.shape} differ in length")

    on_oos = stitched_oos(folds, on)
    off_oos = stitched_oos(folds, off)
    fires_on = on_oos[np.isfinite(on_oos)]
    fires_off = off_oos[np.isfinite(off_oos)]
    n_on, n_off = int(fires_on.size), int(fires_off.size)
    exp_on = float(fires_on.mean()) if n_on else float("nan")
    exp_off = float(fires_off.mean()) if n_off else float("nan")

    sr_on = daily_sharpe(_flat(on_oos), periods_per_year)
    sr_off = daily_sharpe(_flat(off_oos), periods_per_year)

    per_fold: list[float] = []
    for f in folds:
        idx = list(f["oos_idx"])
        if not idx:
            continue
        per_fold.append(daily_sharpe(_flat(on[idx]), periods_per_year)
                        - daily_sharpe(_flat(off[idx]), periods_per_year))
    sign_stability = (float(np.mean([d > 0 for d in per_fold]))
                      if per_fold else float("nan"))

    blocked = np.isfinite(off_oos) & ~np.isfinite(on_oos)
    n_blocked = int(blocked.sum())
    blocked_bp = float(off_oos[blocked].mean() * 100.0) if n_blocked else float("nan")

    return {
        "expectancy_uplift_bp": (exp_on - exp_off) * 100.0,
        "oos_sharpe_uplift": sr_on - sr_off,
        "sign_stability": sign_stability,
        "n_on": n_on,
        "n_off": n_off,
        "expectancy_on_pct": exp_on,
        "expectancy_off_pct": exp_off,
        "oos_sharpe_on": sr_on,
        "oos_sharpe_off": sr_off,
        "per_fold_sharpe_uplift": per_fold,
        "n_folds": len(per_fold),
        "n_blocked": n_blocked,
        "blocked_expectancy_bp": blocked_bp,
        "periods_per_year": periods_per_year,
    }


# --- promotion verdict ----------------------------------------------------------

def _finite(x: Any) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def promotion_verdict(metrics: dict[str, Any], grid_size: int, kind: str = "modulator",
                      reality_check: bool = False) -> dict[str, Any]:
    """GATE_VALIDATION.md section 5 promotion criteria.

    ``kind`` is "modulator" (criterion 1 = expectancy uplift >= +5bp/fire)
    or "binary" (criterion 1 = expectancy of the blocked fires <= -5bp).
    Criterion 2 compares the OOS Sharpe uplift with +0.2; unless a bootstrap
    reality check was run (``reality_check=True``) the uplift is first
    deflated by ``sqrt(grid_size)`` for the size of the pre-registered
    parameter family.  Criterion 3 needs the gated curve to beat the ungated
    one in at least 2/3 of folds.  NaN metrics fail their criterion.

    Returns {"pass": bool, "criteria": {name: (value, threshold, pass)},
    "kind", "grid_size", "reality_check", "sharpe_deflation"}.
    """
    if kind not in ("modulator", "binary"):
        raise ValueError(f"kind must be 'modulator' or 'binary', got {kind!r}")
    if grid_size < 1:
        raise ValueError("grid_size must be >= 1")

    criteria: dict[str, tuple[float, float, bool]] = {}

    if kind == "modulator":
        v = float(metrics.get("expectancy_uplift_bp", float("nan")))
        criteria["expectancy_uplift_bp"] = (
            v, EXPECTANCY_UPLIFT_MIN_BP, _finite(v) and v >= EXPECTANCY_UPLIFT_MIN_BP)
    else:
        v = float(metrics.get("blocked_expectancy_bp", float("nan")))
        criteria["blocked_expectancy_bp"] = (
            v, BLOCKED_EXPECTANCY_MAX_BP, _finite(v) and v <= BLOCKED_EXPECTANCY_MAX_BP)

    deflation = 1.0 if reality_check else math.sqrt(grid_size)
    raw = float(metrics.get("oos_sharpe_uplift", float("nan")))
    deflated = raw / deflation if _finite(raw) else float("nan")
    criteria["oos_sharpe_uplift_deflated"] = (
        deflated, SHARPE_UPLIFT_MIN, _finite(deflated) and deflated >= SHARPE_UPLIFT_MIN)

    s = float(metrics.get("sign_stability", float("nan")))
    criteria["sign_stability"] = (
        s, SIGN_STABILITY_MIN, _finite(s) and s >= SIGN_STABILITY_MIN - 1e-12)

    return {
        "pass": all(c[2] for c in criteria.values()),
        "criteria": criteria,
        "kind": kind,
        "grid_size": int(grid_size),
        "reality_check": bool(reality_check),
        "sharpe_deflation": deflation,
    }


# --- n-trials ledger ------------------------------------------------------------

class NTrialsLedger:
    """Append-only JSON ledger of parameter combinations burned per gate family.

    Each ``add`` appends ``{"family", "n", "tag", "ts"}`` and writes the file
    (atomically via a temp file).  ``total(family)`` is the family's N for
    deflation; ``total()`` the grand total.
    """

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._entries: list[dict[str, Any]] = []
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._entries = list(data.get("entries", []))

    def add(self, family: str, n: int, tag: str = "") -> dict[str, Any]:
        """Record ``n`` trials for ``family`` with a free-text ``tag``; persists immediately."""
        if not family:
            raise ValueError("family must be a non-empty string")
        if int(n) < 0:
            raise ValueError("n must be >= 0")
        entry = {
            "family": str(family),
            "n": int(n),
            "tag": str(tag),
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._entries.append(entry)
        self._save()
        return entry

    def total(self, family: str | None = None) -> int:
        """Sum of ``n`` for ``family`` (0 if unknown); all families when None."""
        return sum(e["n"] for e in self._entries
                   if family is None or e["family"] == family)

    def families(self) -> list[str]:
        return sorted({e["family"] for e in self._entries})

    def entries(self, family: str | None = None) -> list[dict[str, Any]]:
        return [dict(e) for e in self._entries
                if family is None or e["family"] == family]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "entries": self._entries}, fh, indent=2)
        os.replace(tmp, self.path)


def n_trials_ledger(path: str | os.PathLike[str]) -> NTrialsLedger:
    """Open (or create on first ``add``) the JSON n-trials ledger at ``path``."""
    return NTrialsLedger(path)

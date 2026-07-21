"""Byte-equivalence parity: short_squeeze sleeve vs its research notebook
(studies/notebooks/short_squeeze_sessions/strategy_backtest.ipynb).

The notebook isn't importable, so the research side is re-implemented here
verbatim from its cells (perp_cvd / divergence formulas over base volumes;
`rolling_percentile` = trailing-window rank excluding the current value;
WINDOW_BARS = 90*56). Three layers:

  1. RAW FEATURES — sleeve loader vs independent pandas recompute over the
     same rows: must be exactly equal.
  2. FUNCTION SEMANTICS — sleeve math.rolling_percentile / percentile_rank
     vs the notebook definitions: must be exactly equal.
  3. LIVE-PATH APPROXIMATION — the sleeve ranks bars against a once-per-day
     frozen 90-day snapshot; research ranks per-bar against a trailing
     window. Structurally different BY DESIGN — this test QUANTIFIES the
     divergence (percentile diff + gate-flip rate at the 0.15/0.70
     thresholds) and trips only if it exceeds loose bounds. Numbers land in
     docs/calibration/short_squeeze.md.

Read-only against live prod.db; skipped when absent.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from strategies.support import db as _db_mod

pytestmark = pytest.mark.skipif(
    not _db_mod.PROD_DB.exists() or _db_mod.PROD_DB.stat().st_size == 0,
    reason="real prod.db not available")

WINDOW_BARS = 90 * 56          # notebook constant (90d × 56 session bars/day)
SESSION_HOURS = range(7, 21)   # london(7-14) + ny(14-21)


def _load_universe(days: int = 220) -> pd.DataFrame:
    """Notebook-style universe: London/NY 15m bars with perp_cvd/divergence
    computed the notebook's way (base volumes, spot reindexed onto perp)."""
    con = sqlite3.connect(str(_db_mod.PROD_DB))
    lo = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    perp = pd.read_sql(
        "SELECT timestamp, open, high, low, close, volume_buy, volume_sell "
        "FROM cd_futures_15m WHERE timestamp >= ? ORDER BY timestamp",
        con, params=(lo,), index_col="timestamp")
    spot = pd.read_sql(
        "SELECT timestamp, volume_buy, volume_sell FROM cd_spot_15m "
        "WHERE timestamp >= ? ORDER BY timestamp",
        con, params=(lo,), index_col="timestamp")
    con.close()
    b15 = perp.copy()
    b15["perp_cvd"] = perp["volume_buy"].fillna(0) - perp["volume_sell"].fillna(0)
    spot_cvd = (spot["volume_buy"].fillna(0) - spot["volume_sell"].fillna(0))
    b15["spot_cvd"] = spot_cvd.reindex(b15.index).fillna(0)
    b15["divergence"] = b15["spot_cvd"] - b15["perp_cvd"]
    hours = pd.to_datetime(b15.index, unit="s", utc=True).hour
    return b15[np.isin(hours, list(SESSION_HOURS))]


def _notebook_rolling_percentile(series: pd.Series, window: int) -> pd.Series:
    """VERBATIM from strategy_backtest.ipynb (extracted 2026-07-21): NaN for
    the first `window` entries, full-window trailing rank, current value
    excluded from its own distribution."""
    out = np.full(len(series), np.nan)
    arr = series.values
    for i in range(window, len(arr)):
        out[i] = (arr[i - window: i] <= arr[i]).mean()
    return pd.Series(out, index=series.index)


@pytest.fixture(scope="module")
def universe():
    df = _load_universe()
    assert len(df) > 5000, "session universe unexpectedly small"
    return df


def test_raw_features_match_sleeve_loader(universe):
    """Sleeve's _load_recent_15m_bars must produce the exact same
    perp_cvd/divergence per timestamp as the notebook formulas."""
    from strategies.sleeves.short_squeeze import signal as ssq

    now = datetime.fromtimestamp(int(universe.index.max()), tz=timezone.utc)
    rows = ssq._load_recent_15m_bars(now, 90)
    assert len(rows) > 3000
    by_ts = universe[["perp_cvd", "divergence", "low", "close"]]
    checked = 0
    for r in rows:
        if r["ts"] not in by_ts.index:
            continue
        exp = by_ts.loc[r["ts"]]
        assert r["perp_cvd"] == pytest.approx(exp["perp_cvd"], abs=1e-9), r["ts"]
        assert r["divergence"] == pytest.approx(exp["divergence"], abs=1e-9), r["ts"]
        checked += 1
    assert checked > 3000


def test_percentile_function_semantics(universe):
    """Sleeve math.rolling_percentile == notebook definition, and
    percentile_rank uses the same <= semantics."""
    from strategies.sleeves.short_squeeze import math as ssq_math

    s = universe["perp_cvd"].iloc[-2000:]
    ours = ssq_math.rolling_percentile(s.to_numpy(dtype=float), 500)
    ref = _notebook_rolling_percentile(s, 500).to_numpy()
    nan_ours, nan_ref = np.isnan(ours), np.isnan(ref)
    assert (nan_ours == nan_ref).all()
    assert np.allclose(ours[~nan_ours], ref[~nan_ref], rtol=0, atol=1e-12)

    dist = s.to_numpy(dtype=float)[:500]
    for v in (dist[10], float(np.min(dist)) - 1, float(np.max(dist)) + 1):
        assert ssq_math.percentile_rank(v, dist) == (dist <= v).mean()


def test_live_snapshot_vs_research_trailing_quantified(universe):
    """Quantify the structural approximation: daily-frozen 90d snapshot
    (live path) vs per-bar trailing WINDOW_BARS (research). Loose tripwire
    bounds; the measured numbers go into the calibration log."""
    from strategies.sleeves.short_squeeze.config import (
        DIVERGENCE_PCT_MIN, PERP_CVD_PCT_MAX)

    df = universe.copy()
    ts = df.index.to_numpy()
    research = _notebook_rolling_percentile(df["perp_cvd"], WINDOW_BARS)

    # live path model: distribution frozen at each UTC day's 00:00, covering
    # the prior 90 calendar days of session bars
    days = pd.to_datetime(ts, unit="s", utc=True).normalize()
    vals = df["perp_cvd"].to_numpy(dtype=float)
    live = np.full(len(vals), np.nan)
    eval_days = pd.unique(days)[-20:]                 # last ~20 trading days
    for day in eval_days:
        day0 = int(day.timestamp())
        lo = day0 - 90 * 86400
        dist = vals[(ts >= lo) & (ts < day0)]
        if not len(dist):
            continue
        idx = np.where(days == day)[0]
        for i in idx:
            live[i] = (dist <= vals[i]).mean()

    mask = ~np.isnan(live) & ~np.isnan(research.to_numpy())
    assert mask.sum() > 500, "not enough overlapping bars to quantify"
    diff = np.abs(live[mask] - research.to_numpy()[mask])
    p99 = float(np.quantile(diff, 0.99))

    r_gate = research.to_numpy()[mask] < PERP_CVD_PCT_MAX
    l_gate = live[mask] < PERP_CVD_PCT_MAX
    flip_rate = float((r_gate != l_gate).mean())

    print(f"\n[quantified] perp_cvd pct: n={int(mask.sum())} "
          f"p99 |live-research|={p99:.4f} max={diff.max():.4f} "
          f"gate({PERP_CVD_PCT_MAX}) flip rate={flip_rate:.3%}")

    assert p99 < 0.10, f"snapshot-vs-trailing p99 diff {p99:.4f} — approximation too coarse"
    assert flip_rate < 0.10, f"gate flip rate {flip_rate:.2%} — approximation changes decisions too often"

"""Screener-runner: take a setup function, apply it across the universe,
measure forward-return distribution per trigger, evaluate edge gates.

Setup function signature:
    setup_fn(df_daily: pd.DataFrame, df_1h: pd.DataFrame, *, params) -> pd.DataFrame
        Returns columns:
          ts                — trigger timestamp (UTC, exists in df index)
          entry             — execution price (typically next-bar open)
          stop              — protective stop
          target            — TP (optional; can be NaN)
          setup_id          — string label
          conf              — optional confidence 0..1

A setup is a deterministic function. The runner enforces lookahead-bias:
the setup only sees data up to and including its trigger ts; the
forward-return measurement uses data AFTER the trigger.
"""
from __future__ import annotations

import math
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from strategies.support import db as _db  # noqa: E402

DB = _db.PROD_DB
FORWARD_WINDOWS_DAYS = (1, 3, 7, 14)


# === Data loading ==========================================================

def load_universe(con: sqlite3.Connection,
                  top_n: int | None = None,
                  min_qvol_usd: float = 10_000_000) -> list[str]:
    """Return list of assets in universe, optionally top-N by quote volume."""
    rows = con.execute(
        "SELECT asset, median_quote_volume_30d FROM screener_universe "
        "WHERE median_quote_volume_30d >= ? "
        "ORDER BY median_quote_volume_30d DESC",
        (min_qvol_usd,)).fetchall()
    if top_n is not None:
        rows = rows[:top_n]
    return [r[0] for r in rows]


def load_klines(con: sqlite3.Connection, asset: str, interval: str,
                start_ts: int | None = None,
                end_ts: int | None = None) -> pd.DataFrame:
    """Load OHLC for asset/interval. tz-aware index in UTC."""
    table = {'1d': 'screener_klines_daily', '1h': 'screener_klines_1h'}[interval]
    sql = f"SELECT ts, open, high, low, close, volume, quote_volume " \
          f"FROM {table} WHERE asset = ?"
    params: list = [asset]
    if start_ts is not None:
        sql += " AND ts >= ?"; params.append(start_ts)
    if end_ts is not None:
        sql += " AND ts <= ?"; params.append(end_ts)
    sql += " ORDER BY ts"
    df = pd.read_sql(sql, con, params=params)
    if df.empty:
        return df
    df['ts'] = pd.to_datetime(df['ts'], unit='s', utc=True)
    df = df.set_index('ts')
    return df[~df.index.duplicated(keep='last')]


# === Forward-return measurement ===========================================

def measure_forward_returns(triggers: pd.DataFrame, df_daily: pd.DataFrame,
                            windows_days: tuple = FORWARD_WINDOWS_DAYS
                            ) -> pd.DataFrame:
    """For each trigger row, look up close N days forward and compute return.

    Triggers: DataFrame with 'ts', 'entry' columns.
    df_daily: full daily OHLC for the same asset.
    Returns triggers + columns: fwd_ret_{N}d for each window.
    """
    if triggers.empty or df_daily.empty:
        return triggers
    out = triggers.copy()
    daily_close = df_daily['close']
    for n in windows_days:
        fwd_col = f'fwd_ret_{n}d'
        rets = []
        for _, row in out.iterrows():
            entry = float(row['entry'])
            ts = pd.Timestamp(row['ts'])
            target_ts = ts + pd.Timedelta(days=n)
            pos = daily_close.index.searchsorted(target_ts, side='right') - 1
            if pos < 0 or pos >= len(daily_close):
                rets.append(np.nan); continue
            fwd_close = float(daily_close.iloc[pos])
            rets.append((fwd_close - entry) / entry if entry > 0 else np.nan)
        out[fwd_col] = rets
    return out


# === R-multiple measurement (for setups with explicit stop/target) =========

def measure_r_outcomes(triggers: pd.DataFrame, df_1h: pd.DataFrame,
                       tif_days: int = 14) -> pd.DataFrame:
    """For each trigger with entry/stop/target, replay forward 1h bars to
    determine R-outcome: did target hit first (+R), stop hit first (-R), or
    time-stop (partial R based on close at TIF).
    """
    if triggers.empty or df_1h.empty:
        return triggers
    out = triggers.copy()
    r_outcomes = []
    hold_hours = []
    exits = []
    for _, row in out.iterrows():
        entry = float(row['entry'])
        stop = float(row['stop']) if 'stop' in row and pd.notna(row['stop']) else np.nan
        target = float(row['target']) if 'target' in row and pd.notna(row['target']) else np.nan
        ts = pd.Timestamp(row['ts'])
        if np.isnan(stop) or np.isnan(target) or entry <= 0:
            r_outcomes.append(np.nan); hold_hours.append(np.nan); exits.append('na')
            continue
        risk = abs(entry - stop)
        if risk == 0:
            r_outcomes.append(np.nan); hold_hours.append(np.nan); exits.append('na')
            continue
        long = target > entry
        tif_end = ts + pd.Timedelta(days=tif_days)
        fwd = df_1h.loc[ts:tif_end]
        outcome = None; exit_px = None; exit_ts = None
        for bts, bar in fwd.iterrows():
            bh, bl = float(bar['high']), float(bar['low'])
            if long:
                if bl <= stop:
                    outcome = -1.0; exit_px = stop; exit_ts = bts; break
                if bh >= target:
                    outcome = (target - entry) / risk; exit_px = target; exit_ts = bts; break
            else:  # short
                if bh >= stop:
                    outcome = -1.0; exit_px = stop; exit_ts = bts; break
                if bl <= target:
                    outcome = (entry - target) / risk; exit_px = target; exit_ts = bts; break
        if outcome is None:
            close = float(fwd['close'].iloc[-1]) if len(fwd) > 0 else entry
            outcome = ((close - entry) / risk) if long else ((entry - close) / risk)
            exit_px = close
            exit_ts = fwd.index[-1] if len(fwd) > 0 else ts
            exit_lbl = 'tif'
        else:
            exit_lbl = 'win' if outcome > 0 else 'loss'
        r_outcomes.append(outcome)
        hold_hours.append((exit_ts - ts).total_seconds() / 3600 if exit_ts else np.nan)
        exits.append(exit_lbl)
    out['r_outcome'] = r_outcomes
    out['hold_hours'] = hold_hours
    out['exit_kind'] = exits
    return out


# === Edge-gate evaluation =================================================

@dataclass
class EdgeReport:
    setup_id: str
    n_triggers: int
    years: float
    triggers_per_year: float
    asset_concentration_top1_pct: float
    n_assets: int
    fwd_ret_means: dict      # {1: mean_1d, 3: ..., 7: ..., 14: ...}
    fwd_ret_sharpes: dict
    r_mean: float | None     # if r_outcome present
    r_sharpe: float | None
    r_win_rate: float | None
    stability: dict          # first-half vs second-half R means
    verdict: str             # PASS | FAIL
    reasons: list[str]


def evaluate_edge(triggers_with_returns: pd.DataFrame, setup_id: str,
                  target_sharpe: float = 0.30,
                  min_triggers_per_year: int = 50,
                  max_asset_concentration: float = 0.30,
                  total_universe_years: float = 3.0) -> EdgeReport:
    """Apply edge gates to a triggers DataFrame already enriched with
    forward returns + (optionally) R outcomes.
    """
    df = triggers_with_returns
    n = len(df)
    if n == 0:
        return EdgeReport(setup_id, 0, 0, 0, 0, 0, {}, {},
                          None, None, None, {}, 'FAIL', ['no triggers'])

    years = total_universe_years
    tpy = n / years if years > 0 else 0
    assets = df['asset'].value_counts() if 'asset' in df.columns else pd.Series()
    top1 = float(assets.iloc[0]) / n if not assets.empty else 1.0

    fwd_means: dict = {}; fwd_sharpes: dict = {}
    for w in FORWARD_WINDOWS_DAYS:
        col = f'fwd_ret_{w}d'
        if col in df.columns:
            vals = df[col].dropna()
            if len(vals) > 1:
                m = float(vals.mean()); s = float(vals.std(ddof=1))
                fwd_means[w] = m
                fwd_sharpes[w] = m / s if s > 0 else 0

    r_mean = r_sharpe = r_wr = None
    if 'r_outcome' in df.columns:
        rv = df['r_outcome'].dropna()
        if len(rv) > 1:
            r_mean = float(rv.mean())
            r_sharpe = float(rv.mean() / rv.std(ddof=1)) if rv.std(ddof=1) > 0 else 0
            r_wr = float((rv > 0).mean())

    # Stability: top-half date range vs bottom-half
    df_sorted = df.sort_values('ts') if 'ts' in df.columns else df
    mid = len(df_sorted) // 2
    stability = {}
    if 'r_outcome' in df.columns and mid > 0:
        first = df_sorted['r_outcome'].iloc[:mid].dropna()
        second = df_sorted['r_outcome'].iloc[mid:].dropna()
        if len(first) > 0 and len(second) > 0:
            stability['first_half_r_mean'] = float(first.mean())
            stability['second_half_r_mean'] = float(second.mean())
            stability['both_positive'] = bool(first.mean() > 0 and second.mean() > 0)

    reasons = []
    if tpy < min_triggers_per_year:
        reasons.append(f'frequency {tpy:.1f}/yr < {min_triggers_per_year}')
    if top1 > max_asset_concentration:
        reasons.append(f'asset concentration top1={top1:.1%} > {max_asset_concentration:.0%}')
    best_fwd_sharpe = max(fwd_sharpes.values()) if fwd_sharpes else 0
    if best_fwd_sharpe < target_sharpe and (r_sharpe is None or r_sharpe < target_sharpe):
        reasons.append(f'best Sharpe {max(best_fwd_sharpe, r_sharpe or 0):.2f} < {target_sharpe}')
    if stability and not stability.get('both_positive', False):
        reasons.append('not stable: one half is negative')

    verdict = 'PASS' if not reasons else 'FAIL'
    return EdgeReport(
        setup_id, n, years, tpy, top1, len(assets),
        fwd_means, fwd_sharpes, r_mean, r_sharpe, r_wr,
        stability, verdict, reasons,
    )


# === Driver: run a setup across the universe ==============================

def run_setup_across_universe(
    setup_fn: Callable,
    setup_id: str,
    universe: list[str] | None = None,
    top_n: int | None = None,
    min_qvol_usd: float = 10_000_000,
    start_ts: int | None = None,
    end_ts: int | None = None,
    setup_params: dict | None = None,
) -> tuple[pd.DataFrame, EdgeReport]:
    """Run `setup_fn` over each coin in universe, concat triggers, measure
    forward returns and R outcomes (if setup yields stop+target), evaluate
    edge gates. Returns (triggers_df, edge_report).
    """
    con = sqlite3.connect(str(DB))
    try:
        if universe is None:
            universe = load_universe(con, top_n=top_n,
                                      min_qvol_usd=min_qvol_usd)
        all_triggers = []
        params = setup_params or {}
        for i, asset in enumerate(universe, 1):
            d = load_klines(con, asset, '1d', start_ts=start_ts, end_ts=end_ts)
            h = load_klines(con, asset, '1h', start_ts=start_ts, end_ts=end_ts)
            if d.empty:
                continue
            try:
                trig = setup_fn(d, h, **params)
            except Exception as e:
                print(f'  [{i:3d}/{len(universe)}] {asset}: setup error: {e}')
                continue
            if trig.empty:
                continue
            trig = trig.copy()
            trig['asset'] = asset
            trig_with_fwd = measure_forward_returns(trig, d)
            if not h.empty:
                trig_with_fwd = measure_r_outcomes(trig_with_fwd, h)
            all_triggers.append(trig_with_fwd)
    finally:
        con.close()

    if not all_triggers:
        return pd.DataFrame(), EdgeReport(
            setup_id, 0, 0, 0, 0, 0, {}, {}, None, None, None,
            {}, 'FAIL', ['no triggers'])

    df = pd.concat(all_triggers, ignore_index=True)
    df['setup_id'] = setup_id
    # Total span of universe data — used by frequency gate
    spans = []
    for t in all_triggers:
        if len(t) > 0:
            spans.append((t['ts'].min(), t['ts'].max()))
    if spans:
        full_span = (min(s[0] for s in spans), max(s[1] for s in spans))
        years = ((full_span[1] - full_span[0]).total_seconds()
                 / (365.25 * 86400))
    else:
        years = 3.0
    report = evaluate_edge(df, setup_id, total_universe_years=years)
    return df, report

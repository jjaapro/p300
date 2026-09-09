"""Shared helpers for the Track V4 validation audit.

Read-only. Owns:
  * the BTC daily close series (from cd_futures_ohlcv, read-only URI)
  * the alpha-vs-BTC regression with Newey-West standard errors
  * the statistic bundle applied identically to Part A and Part B series
  * JSON-safe serialisation

Nothing here writes to prod.db or to anything under strategies/ or bots/.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from strategies.support import db  # noqa: E402
from studies.lib.validation import (  # noqa: E402
    bootstrap,
    cpcv,
    dd_duration,
    dsr_pbo,
    fundamental_law,
    haircut,
    metrics,
)

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)

# Pre-registered constants (README §2)
PERIODS_PER_YEAR = 365.0
BOOT_SEED = 42
BOOT_ITER_IID = 10_000
BOOT_ITER_BLOCK = 5_000
BLOCK_LEN = 20
CPCV_MIN_ROWS = 200
NW_LAG = 5
ALPHA_T_THRESHOLD = 2.0
BETA_R2_THRESHOLD = 0.20
MIN_TRADES_FOR_VERDICT = 20
MIN_DAILY_ROWS_FOR_VERDICT = 60
TRUST_CUTOFF = "2026-05-16"  # memory/project_paper_trade_loss_recovery_2026_06_05.md


def ro_connect() -> sqlite3.Connection:
    """Read-only connection to prod.db (standing rule)."""
    return sqlite3.connect("file:" + str(db.PROD_DB) + "?mode=ro", uri=True)


# ── BTC daily benchmark ──────────────────────────────────────────────────────

def btc_daily_returns() -> dict[str, float]:
    """{date_iso: simple daily return} for BTC from cd_futures_ohlcv (hourly
    perp). Last close of each UTC day; the still-forming current day is
    dropped, mirroring adx_study/harness.load_btc_daily."""
    con = ro_connect()
    try:
        rows = con.execute(
            "SELECT timestamp, close FROM cd_futures_ohlcv "
            "WHERE close IS NOT NULL AND close > 0 ORDER BY timestamp"
        ).fetchall()
    finally:
        con.close()
    last_close: dict[str, float] = {}
    for ts, close in rows:
        d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        last_close[d] = float(close)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    last_close.pop(today, None)
    days = sorted(last_close)
    out: dict[str, float] = {}
    for i in range(1, len(days)):
        prev, cur = last_close[days[i - 1]], last_close[days[i]]
        if prev > 0:
            out[days[i]] = cur / prev - 1.0
    return out


# ── Newey-West OLS ───────────────────────────────────────────────────────────

def _normal_sf(x: float) -> float:
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def alpha_vs_btc(dates: Sequence[str], rets_pct: Sequence[float],
                 btc: dict[str, float], lag: int = NW_LAG) -> dict[str, Any]:
    """OLS of the series' daily returns (in PERCENT) on BTC daily returns
    (converted to percent), with Newey-West(lag) standard errors.

    Returns annualised alpha in percentage points, beta, R^2, the alpha
    t-statistic and its two-sided normal p-value.
    """
    pairs = [(float(r), btc[d] * 100.0)
             for d, r in zip(dates, rets_pct) if d in btc]
    n = len(pairs)
    if n < 10:
        return {"n_obs": n, "status": "insufficient overlap (<10 days)"}
    y = np.array([p[0] for p in pairs], dtype=float)
    x = np.array([p[1] for p in pairs], dtype=float)
    X = np.column_stack([np.ones(n), x])
    beta_hat, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta_hat
    ss_res = float((resid ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    XtX_inv = np.linalg.pinv(X.T @ X)
    # Newey-West meat
    u = X * resid[:, None]
    S = u.T @ u
    L = min(lag, n - 1)
    for l in range(1, L + 1):
        w = 1.0 - l / (L + 1.0)
        G = u[l:].T @ u[:-l]
        S = S + w * (G + G.T)
    cov = XtX_inv @ S @ XtX_inv
    se = np.sqrt(np.maximum(np.diag(cov), 0.0))
    alpha_d, beta = float(beta_hat[0]), float(beta_hat[1])
    t_alpha = alpha_d / se[0] if se[0] > 0 else float("nan")
    t_beta = beta / se[1] if se[1] > 0 else float("nan")
    p_alpha = (2.0 * _normal_sf(abs(t_alpha))
               if math.isfinite(t_alpha) else float("nan"))
    return {
        "n_obs": n,
        "alpha_daily_pct": alpha_d,
        "alpha_annual_pct": alpha_d * PERIODS_PER_YEAR,
        "beta": beta,
        "r_squared": r2,
        "t_alpha_nw": t_alpha,
        "p_alpha_nw": p_alpha,
        "t_beta_nw": t_beta,
        "nw_lag": L,
        "status": "ok",
    }


# ── The statistic bundle ─────────────────────────────────────────────────────

def zero_fill(sparse: dict[str, float], start: str, end: str
              ) -> list[tuple[str, float]]:
    """Calendar-complete daily series between start and end inclusive."""
    d0 = datetime.strptime(start, "%Y-%m-%d")
    d1 = datetime.strptime(end, "%Y-%m-%d")
    out = []
    cur = d0
    while cur <= d1:
        iso = cur.strftime("%Y-%m-%d")
        out.append((iso, sparse.get(iso, 0.0)))
        cur += timedelta(days=1)
    return out


def audit_series(label: str,
                 trade_dates: Sequence[str],
                 trade_rets_pct: Sequence[float],
                 daily: Sequence[tuple[str, float]],
                 n_trials: int,
                 btc: dict[str, float],
                 *,
                 unit: str = "% of capital",
                 note: str = "") -> dict[str, Any]:
    """Every pre-registered statistic for one series.

    trade_rets_pct: per-trade returns in the series' own unit (percent of
                    capital for paper, R for chento, net percent for ADX).
    daily:          zero-filled (date, daily return) in the same unit.
    """
    n_tr = len(trade_rets_pct)
    dvals = [v for _, v in daily]
    ddates = [d for d, _ in daily]
    n_days = len(dvals)
    span_days = 0
    if trade_dates:
        d0 = datetime.strptime(min(trade_dates), "%Y-%m-%d")
        d1 = datetime.strptime(max(trade_dates), "%Y-%m-%d")
        span_days = (d1 - d0).days + 1

    out: dict[str, Any] = {
        "series": label,
        "unit": unit,
        "note": note,
        "n_trades": n_tr,
        "n_daily_rows": n_days,
        "first_trade": min(trade_dates) if trade_dates else None,
        "last_trade": max(trade_dates) if trade_dates else None,
        "span_days": span_days,
        "n_trials": n_trials,
    }
    if n_tr == 0:
        out["status"] = "no closed trades — every statistic undefined"
        out["verdict_clause"] = "C1"
        out["verdict"] = "too few observations to say"
        return out
    out["status"] = "ok"

    arr = np.asarray(trade_rets_pct, dtype=float)
    out["mean_per_trade"] = float(arr.mean())
    out["median_per_trade"] = float(np.median(arr))
    out["sd_per_trade"] = float(arr.std(ddof=1)) if n_tr > 1 else float("nan")
    out["total"] = float(arr.sum())
    out["win_rate_pct"] = float((arr > 0).mean() * 100.0)
    bets_per_year = (n_tr * 365.0 / span_days) if span_days > 0 else float("nan")
    out["bets_per_year"] = bets_per_year

    # per-trade Sharpe (raw, per-trade units) and annualised at realised breadth
    out["sharpe_per_trade"] = (
        float(arr.mean() / arr.std(ddof=1)) if n_tr > 1 and arr.std(ddof=1) > 0
        else float("nan"))
    out["sharpe_per_trade_ann"] = (
        metrics.trade_sharpe(list(arr), bets_per_year)
        if math.isfinite(bets_per_year) else float("nan"))

    # daily Sharpe, drawdown
    out["sharpe_daily_ann"] = metrics.daily_sharpe(dvals, PERIODS_PER_YEAR)
    out["max_drawdown_units"] = metrics.max_drawdown(dvals)

    # drawdown durations, on the compounding equity curve from trades
    try:
        curve = dd_duration.equity_curve_from_trades(
            [(d, r / 100.0) for d, r in zip(trade_dates, arr)])
        dds = dd_duration.drawdown_durations(curve)
        if dds:
            durs = [d["duration_days"] for d in dds]
            depths = [d["depth_pct"] for d in dds]
            out["dd_n"] = len(dds)
            out["dd_max_duration_days"] = max(durs)
            out["dd_median_duration_days"] = sorted(durs)[len(durs) // 2]
            out["dd_max_depth_pct"] = max(depths)
            out["dd_open_at_end"] = not dds[-1]["recovered"]
        else:
            out["dd_n"] = 0
    except Exception as e:  # noqa: BLE001 - report, do not hide
        out["dd_error"] = f"{type(e).__name__}: {e}"

    # iid bootstrap on the per-trade Sharpe
    bs = bootstrap.bootstrap_sharpe(list(arr), n_iter=BOOT_ITER_IID,
                                    seed=BOOT_SEED,
                                    n_per_year=max(bets_per_year, 1e-9)
                                    if math.isfinite(bets_per_year) else 12)
    if bs:
        out["boot_trade_sr_point"] = bs["sr_point"]
        out["boot_trade_sr_p05"] = bs["sr_p05"]
        out["boot_trade_sr_p50"] = bs["sr_p50"]
        out["boot_trade_sr_p95"] = bs["sr_p95"]
    else:
        out["boot_trade_sr_point"] = None

    # circular-block bootstrap on the daily series
    bb = bootstrap.block_bootstrap_sharpe(dvals, block=BLOCK_LEN,
                                          n_iter=BOOT_ITER_BLOCK,
                                          seed=BOOT_SEED,
                                          periods_per_year=PERIODS_PER_YEAR)
    if bb:
        out["block_daily_sr_point"] = bb["sr_point"]
        out["block_daily_sr_p05"] = bb["sr_p05"]
        out["block_daily_sr_p50"] = bb["sr_p50"]
        out["block_daily_sr_p95"] = bb["sr_p95"]
        out["block_daily_p_sr_gt_0"] = bb["p_sr_gt_0"]
    else:
        out["block_daily_sr_point"] = None

    # Deflated Sharpe — on the per-trade series (the unit the strategy bets in)
    d_tr = dsr_pbo.dsr_from_returns(list(arr), n_trials,
                                    periods_per_year=bets_per_year
                                    if math.isfinite(bets_per_year) else None)
    if d_tr:
        out["dsr_trade"] = d_tr["dsr"]
        out["dsr_trade_z"] = d_tr["dsr_z"]
        out["dsr_trade_sr_expected"] = d_tr["sr_expected"]
        out["dsr_trade_skew"] = d_tr["skew"]
        out["dsr_trade_kurt"] = d_tr["kurt"]
    else:
        out["dsr_trade"] = None
    # ...and on the daily series, for comparability with the daily Sharpe
    d_d = dsr_pbo.dsr_from_returns(dvals, n_trials,
                                   periods_per_year=PERIODS_PER_YEAR)
    out["dsr_daily"] = d_d["dsr"] if d_d else None
    out["dsr_daily_z"] = d_d["dsr_z"] if d_d else None

    # Harvey-Liu haircut on the annualised daily Sharpe
    sr_ann = out["sharpe_daily_ann"]
    if sr_ann and math.isfinite(sr_ann) and n_days >= 2:
        hc = haircut.haircut_triple(sr_ann, n_days, n_trials,
                                    freq_per_year=PERIODS_PER_YEAR)
        out["haircut_p_observed"] = hc["p_observed"]
        for m in ("bonferroni", "holm", "bhy"):
            out[f"haircut_{m}_sharpe"] = hc[m]["sharpe_adj"]
            out[f"haircut_{m}_pct"] = hc[m]["haircut_pct"]
    else:
        out["haircut_holm_sharpe"] = None

    # Fundamental Law
    if math.isfinite(bets_per_year) and bets_per_year > 0:
        fl = fundamental_law.breadth_verdict(sr_ann, bets_per_year)
        out["fl_ic_required"] = fl["ic_required"]
        out["fl_verdict"] = fl["verdict"]
        out["fl_memo_verdict"] = fl["memo_verdict"]
    else:
        out["fl_ic_required"] = None

    # CPCV — only where the daily series is long enough (pre-registered)
    if n_days >= CPCV_MIN_ROWS:
        try:
            cp = cpcv.cpcv_score(list(daily),
                                 lambda r: metrics.daily_sharpe(r, PERIODS_PER_YEAR),
                                 n_groups=10, k_test=2, embargo_days=5)
            out["cpcv_n_splits"] = cp["n_splits"]
            out["cpcv_train_mean"] = cp["train_mean"]
            out["cpcv_test_mean"] = cp["test_mean"]
            out["cpcv_decay_ratio"] = cp["decay_ratio"]
            out["cpcv_mean_drop"] = cp["mean_drop"]
        except Exception as e:  # noqa: BLE001
            out["cpcv_error"] = f"{type(e).__name__}: {e}"
    else:
        out["cpcv_n_splits"] = "insufficient"
        out["cpcv_decay_ratio"] = "insufficient"

    # Alpha vs BTC
    av = alpha_vs_btc(ddates, dvals, btc)
    for k, v in av.items():
        out[f"alpha_{k}"] = v

    return out


def assign_verdict(row: dict[str, Any]) -> dict[str, Any]:
    """Apply the pre-registered classification clauses C1-C8 (README §5)."""
    fired: list[str] = []
    n_tr = row.get("n_trades", 0)
    n_days = row.get("n_daily_rows", 0)

    c1 = (n_tr < MIN_TRADES_FOR_VERDICT) or (n_days < MIN_DAILY_ROWS_FOR_VERDICT)
    dsr = row.get("dsr_trade")
    p05 = row.get("boot_trade_sr_p05")
    c2 = (dsr is not None and dsr >= 0.95 and p05 is not None and p05 > 0)
    c3 = (not c1) and (not c2)

    t_a = row.get("alpha_t_alpha_nw")
    r2 = row.get("alpha_r_squared")
    c4 = (t_a is not None and r2 is not None
          and isinstance(t_a, float) and isinstance(r2, float)
          and math.isfinite(t_a) and math.isfinite(r2)
          and abs(t_a) < ALPHA_T_THRESHOLD and r2 >= BETA_R2_THRESHOLD)

    hc = row.get("haircut_holm_sharpe")
    c5 = hc is not None and isinstance(hc, float) and hc > 0
    ic = row.get("fl_ic_required")
    c6 = ic is not None and isinstance(ic, float) and math.isfinite(ic) and abs(ic) > 0.7
    dec = row.get("cpcv_decay_ratio")
    c7 = isinstance(dec, float) and dec < 0.5
    pbo = row.get("pbo")
    c8 = isinstance(pbo, float) and pbo > 0.5

    if c1:
        verdict = "too few observations to say"
        fired.append("C1")
    elif c4:
        verdict = "indistinguishable from beta"
        fired.append("C4")
    elif c2:
        verdict = "survives deflation"
        fired.append("C2")
    else:
        verdict = "does not survive deflation"
        fired.append("C3")
    if c4 and not c1:
        if "C4" not in fired:
            fired.append("C4")
    if c2:
        if "C2" not in fired:
            fired.append("C2")
    if c3 and "C3" not in fired:
        fired.append("C3")

    flags = []
    if c5:
        flags.append("survives Holm at this N")
        fired.append("C5")
    if c6:
        flags.append("breadth-implausible (required IC > 0.7)")
        fired.append("C6")
    if c7:
        flags.append("degrades out of fold")
        fired.append("C7")
    if c8:
        flags.append("overfit-prone on the reconstructible axis")
        fired.append("C8")

    row["verdict"] = verdict
    row["clauses_fired"] = "+".join(fired)
    row["flags"] = "; ".join(flags) if flags else ""
    return row


def jsonable(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, (np.floating, np.integer)):
        o = o.item()
    if isinstance(o, float):
        if math.isnan(o):
            return None
        if math.isinf(o):
            return "inf" if o > 0 else "-inf"
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    return o


def write_json(name: str, obj: Any) -> Path:
    p = RESULTS / name
    p.write_text(json.dumps(jsonable(obj), indent=2), encoding="utf-8")
    return p


def write_csv(name: str, rows: Sequence[dict[str, Any]],
              columns: Sequence[str] | None = None) -> Path:
    import csv
    p = RESULTS / name
    if not rows:
        p.write_text("", encoding="utf-8")
        return p
    if columns is None:
        seen: list[str] = []
        for r in rows:
            for k in r:
                if k not in seen:
                    seen.append(k)
        columns = seen
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(columns), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: jsonable(r.get(k)) for k in columns})
    return p

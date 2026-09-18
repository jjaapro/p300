"""Costs, calendar returns, summaries and block-bootstrap inference for the ORB study.

Units. A ledger row carries gross_bp (price only), funding_bp (cash to the position) and
the fill/exit prices, all in basis points of entry notional at 1x equity. A cost scenario
is a per-leg charge in bp; the exit leg scales with exit/fill because the position's
notional has moved. net_bp = gross_bp + funding_bp - c * (1 + exit/fill); the "gross"
scenario is the zero-cost, zero-funding price signal.

Calendar returns are one value per UTC day over the whole block, zero on days without a
position (365-day annualisation). A trade entered and closed on one UTC day contributes
net_bp / 1e4 to that day. A multi-day position is marked to market at each 00:00 UTC
with a fixed quantity, and each day's P&L is divided by the equity at that day's start.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from orb_engine import DAY_MIN, Market

COSTS = {
    "gross": None,
    "rt5": 2.5,
    "rt10": 5.0,          # also the taker fee floor: 2 x 5.0 bp
    "rt20": 10.0,
    "rt30": 15.0,
    "db": 5.8,            # decision-bearing: 5.0 fee + 0.3 half-spread + 0.5 slippage, per leg
    "db_nonfee_x2": 6.6,  # non-fee friction doubled
    "db_plus5": 8.3,      # +5 bp per round trip
    "db_plus10": 10.8,    # +10 bp per round trip
}


def trades(ledger: pd.DataFrame) -> pd.DataFrame:
    return ledger[ledger["status"] == "trade"].copy() if len(ledger) else ledger


def net_bp(tr: pd.DataFrame, scenario: str) -> pd.Series:
    c = COSTS[scenario]
    if c is None:
        return tr["gross_bp"].astype(float)
    return tr["gross_bp"] + tr["funding_bp"] - c * (1.0 + tr["exit_px"] / tr["fill_px"])


def _utc_date(mkt: Market, idx: int) -> str:
    return pd.Timestamp(mkt.time_ms(idx), unit="ms", tz="UTC").date().isoformat()


def _funding_events(mkt: Market, side: int, fill_idx: int, exit_idx: int, fill_px: float):
    lo = np.searchsorted(mkt.funding_idx, fill_idx, side="left")
    hi = np.searchsorted(mkt.funding_idx, exit_idx, side="right")
    for f, rate in zip(mkt.funding_idx[lo:hi], mkt.funding_rate[lo:hi]):
        g = int(f)
        while g > fill_idx and not mkt.ok(g):
            g -= 1
        mark = mkt.open[g] if mkt.ok(g) else fill_px
        yield int(f), -side * rate * (mark / fill_px)          # fraction of entry notional


def trade_day_returns(tr: dict, mkt: Market, scenario: str) -> list[tuple[str, float]]:
    """(UTC date, return on that day's starting equity) for one trade."""
    c = COSTS[scenario]
    side, f, fpx, e, epx = int(tr["side"]), int(tr["fill_idx"]), float(tr["fill_px"]), int(tr["exit_idx"]), float(tr["exit_px"])
    if _utc_date(mkt, f) == _utc_date(mkt, e):
        net = tr["gross_bp"] if c is None else tr["gross_bp"] + tr["funding_bp"] - c * (1 + epx / fpx)
        return [(_utc_date(mkt, f), float(net) / 1e4)]
    assert mkt.t0_ms % (DAY_MIN * 60_000) == 0, "panel must start at a UTC midnight"
    boundaries = list(range((f // DAY_MIN + 1) * DAY_MIN, e + 1, DAY_MIN))   # 00:00 UTC minutes in (f, e]
    events = [] if c is None else list(_funding_events(mkt, side, f, e, fpx))
    out, equity, prev_px, seg_start = [], 1.0, fpx, f
    for k, b in enumerate([*boundaries, None]):
        last = b is None
        if last:
            px = epx
        else:                                   # mark at the close of the minute ending at midnight
            g = b - 1
            while g > seg_start and not mkt.ok(g):
                g -= 1
            px = mkt.close[g] if mkt.ok(g) else prev_px
        pnl = side * (px - prev_px) / fpx
        if c is not None:
            pnl -= c / 1e4 if k == 0 else 0.0
            pnl -= c / 1e4 * epx / fpx if last else 0.0
            pnl += sum(v for i, v in events if seg_start <= i < (e + 1 if last else b))
        out.append((_utc_date(mkt, seg_start), pnl / equity))
        equity += pnl
        prev_px, seg_start = px, (e if last else b)
    return out


def daily_returns(ledger: pd.DataFrame, mkt: Market, start: str, end: str, scenario: str) -> pd.Series:
    days = pd.date_range(start, end, freq="D").strftime("%Y-%m-%d")
    r = pd.Series(0.0, index=days)
    for tr in trades(ledger).to_dict("records"):
        for day, ret in trade_day_returns(tr, mkt, scenario):
            if day in r.index:
                r[day] = (1 + r[day]) * (1 + ret) - 1
    return r


def per_day_trade_sums(ledger: pd.DataFrame, mkt: Market, start: str, end: str, scenario: str):
    """Sum of net bp and count of trades by UTC entry date, zero-filled over the block."""
    days = pd.date_range(start, end, freq="D").strftime("%Y-%m-%d")
    tr = trades(ledger)
    sums = pd.Series(0.0, index=days)
    counts = pd.Series(0.0, index=days)
    if len(tr):
        d = [_utc_date(mkt, int(i)) for i in tr["fill_idx"]]
        g = pd.DataFrame({"d": d, "net": net_bp(tr, scenario).to_numpy()}).groupby("d")["net"]
        sums.loc[g.sum().index.intersection(days)] = g.sum()
        counts.loc[g.count().index.intersection(days)] = g.count()
    return sums, counts


# --- summaries --------------------------------------------------------------------------------

def max_drawdown(daily: pd.Series) -> float:
    nav = np.concatenate([[1.0], np.cumprod(1 + daily.to_numpy())])
    peak = np.maximum.accumulate(nav)
    return float((nav / peak - 1).min())


def summarize(ledger: pd.DataFrame, mkt: Market, start: str, end: str, scenario: str) -> dict:
    tr = trades(ledger)
    daily = daily_returns(ledger, mkt, start, end, scenario)
    n_days = len(daily)
    out = {"scenario": scenario, "sessions": int(len(ledger)), "trades": int(len(tr)), "days": n_days}
    if len(tr) == 0:
        return out
    net = net_bp(tr, scenario)
    wins, losses = net[net > 0], net[net <= 0]
    nav_growth = float(np.prod(1 + daily.to_numpy()))
    ann = nav_growth ** (365 / n_days) - 1 if nav_growth > 0 else -1.0
    sd = daily.std(ddof=1)
    mdd = max_drawdown(daily)
    top = net.sort_values(ascending=False)
    total = net.sum()
    out.update({
        "mean_bp": float(net.mean()), "median_bp": float(net.median()), "sd_bp": float(net.std(ddof=1)),
        "win_rate": float((net > 0).mean()),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else math.inf,
        "payoff": float(wins.mean() / -losses.mean()) if len(wins) and losses.mean() < 0 else math.nan,
        "mean_gross_bp": float(tr["gross_bp"].mean()), "mean_funding_bp": float(tr["funding_bp"].mean()),
        "mean_risk_bp": float(tr["risk_bp"].mean()), "median_risk_bp": float(tr["risk_bp"].median()),
        "mean_R": float((net / tr["risk_bp"].where(tr["risk_bp"] > 0)).mean()),
        "total_return": nav_growth - 1, "ann_return": ann,
        "sharpe_daily_ann": float(daily.mean() / sd * math.sqrt(365)) if sd > 0 else math.nan,
        "max_drawdown": mdd, "mar": ann / -mdd if mdd < 0 else math.nan,
        "exposure_days": float((daily != 0).mean()),
        "long_trades": int((tr["side"] > 0).sum()), "short_trades": int((tr["side"] < 0).sum()),
        "long_mean_bp": float(net[tr["side"] > 0].mean()) if (tr["side"] > 0).any() else math.nan,
        "short_mean_bp": float(net[tr["side"] < 0].mean()) if (tr["side"] < 0).any() else math.nan,
        "best1_share": float(top.iloc[:1].sum() / total) if total != 0 else math.nan,
        "best5_share": float(top.iloc[:5].sum() / total) if total != 0 else math.nan,
        "best10_share": float(top.iloc[:10].sum() / total) if total != 0 else math.nan,
        "mean_bars_held": float(tr["bars_held"].mean()),
        "exit_reasons": tr["exit_reason"].value_counts().to_dict(),
        "flagged_trades": int((tr["flags"].fillna("") != "").sum()),
    })
    return out


# --- block bootstrap ------------------------------------------------------------------------------

def block_indices(T: int, block: int = 20, n_boot: int = 10_000, seed: int = 42) -> np.ndarray:
    """Circular block bootstrap index matrix (n_boot, T); reuse it jointly across series."""
    rng = np.random.default_rng(seed)
    n_blocks = -(-T // block)
    starts = rng.integers(0, T, size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % T
    return idx.reshape(n_boot, -1)[:, :T].astype(np.int32)


def boot_mean(x: np.ndarray, idx: np.ndarray) -> np.ndarray:
    return x[idx].mean(axis=1)


def boot_ratio(sums: np.ndarray, counts: np.ndarray, idx: np.ndarray) -> np.ndarray:
    c = counts[idx].sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(c > 0, sums[idx].sum(axis=1) / c, np.nan)


def interval(boot: np.ndarray, q=(0.025, 0.5, 0.975)) -> list[float]:
    b = boot[np.isfinite(boot)]
    return [float(v) for v in np.quantile(b, q)] if len(b) else [math.nan] * len(q)


def p_greater_than_zero(observed: float, boot: np.ndarray) -> float:
    """One-sided p-value for H0: statistic <= 0, from the recentred bootstrap distribution."""
    b = boot[np.isfinite(boot)]
    return float((1 + np.sum(b - observed >= observed)) / (len(b) + 1))


def expectancy_inference(ledger: pd.DataFrame, mkt: Market, start: str, end: str, scenario: str,
                         idx: np.ndarray) -> dict:
    sums, counts = per_day_trade_sums(ledger, mkt, start, end, scenario)
    s, c = sums.to_numpy(), counts.to_numpy()
    obs = float(s.sum() / c.sum()) if c.sum() else math.nan
    boot = boot_ratio(s, c, idx)
    daily = daily_returns(ledger, mkt, start, end, scenario).to_numpy()
    dboot = boot_mean(daily, idx)
    return {"mean_bp": obs, "mean_bp_ci95": interval(boot), "p_mean_bp_gt0": p_greater_than_zero(obs, boot),
            "undefined_draws": int(np.isnan(boot).sum()),
            "daily_mean": float(daily.mean()), "daily_mean_ci95": interval(dboot),
            "p_daily_mean_gt0": p_greater_than_zero(float(daily.mean()), dboot)}


def holm(pvalues: dict[str, float], alpha: float = 0.05) -> dict[str, bool]:
    order = sorted(pvalues, key=pvalues.get)
    m = len(order)
    rejected, still = {}, True
    for i, k in enumerate(order):
        still = still and pvalues[k] <= alpha / (m - i)
        rejected[k] = still
    return rejected

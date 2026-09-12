"""Shared code for the sizing-style study. Reuses the execution study's events, cached 1 m paths, walker and
statistics (read-only); re-implements margin_sim's cross-margin liquidation rule with exit times."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EXEC = ROOT / "studies" / "notebooks" / "execution_2026_09"
for p in (str(ROOT), str(EXEC)):
    if p not in sys.path:
        sys.path.insert(0, p)
import exec_lib as ex  # noqa: E402

bv = ex.bv
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)

CAPITAL = 10_000.0
MM_PCT = 0.005              # strategies/support/margin_sim.SimParams.mm_pct
LIQ_FEE_PCT = 0.005         # SimParams.liquidation_fee_pct
SIZING = {"CHENTO_BTC": (2.0, 3.0), "CHENTO_ETH": (2.0, 3.0), "SHORT_SQUEEZE": (1.0, 3.0), "SQUEEZE_BULL": (1.0, 3.0)}
POLICIES = ("P0_shipped", "P1_time_only", "P1b_target_only", "P2_catastrophe_3x")
CAT_MULT = 3.0
_E6 = None


def cost_bp(sleeve: str) -> float:
    """Execution study E6: measured taker round trip per sleeve (bp)."""
    global _E6
    if _E6 is None:
        _E6 = ex.jload("e6_recost.json")
    return float(_E6["sleeves"][sleeve]["models"]["M1_measured_taker"]["mean_bp_per_trade"])


def jdump(obj, name: str) -> Path:
    import json
    p = RESULTS / name
    p.write_text(json.dumps(bv._jsonable(obj), indent=1, default=str), encoding="utf-8")
    return p


def jload(name: str):
    import json
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def policy_levels(ev: ex.Event, fill_venue: float, policy: str) -> tuple[float, float, float]:
    stop, target, risk = ev.levels_for_fill(fill_venue, 1.0)
    d, inf = ev.direction, float("inf")
    no_stop = -inf if d > 0 else inf
    no_target = inf if d > 0 else -inf
    if policy == "P0_shipped":
        return stop, target, risk
    if policy == "P1_time_only":
        return no_stop, no_target, risk
    if policy == "P1b_target_only":
        return no_stop, target, risk
    if policy == "P2_catastrophe_3x":
        return fill_venue - d * CAT_MULT * risk, target, risk
    raise ValueError(policy)


def notional_x(sleeve: str, risk_pct: float) -> float:
    r, cap = SIZING[sleeve]
    return float(min(r / 100.0 / risk_pct, cap))


def run_policy(sleeve: str, ev: ex.Event, path: ex.PricePath, cx: dict, policy: str) -> dict:
    """One trade under one exit policy at the shipped sizing. R in shipped-R units; pnl in $ on CAPITAL."""
    d = ev.direction
    ratio = cx["venue"] / cx["paper"]
    f = cx["paper"] * ratio                       # = signal price (venue terms)
    stop, target, risk = policy_levels(ev, f, policy)
    w = ex.walk(path, cx["i0"], cx["i1"], d, stop, target)
    exit_venue = w["price"] * ratio if w["kind"] == "tif" else w["price"]
    risk_pct = risk / f
    nx = notional_x(sleeve, risk_pct)
    notional = nx * CAPITAL
    pnl_frac = d * (exit_venue / f - 1.0)
    cost_usd = notional * cost_bp(sleeve) / 1e4
    # adverse excursion inside the hold (fraction of fill), for the S3 safety clause
    seg = path.l[cx["i0"]:w["idx"] + 1] if d > 0 else path.h[cx["i0"]:w["idx"] + 1]
    mae = float(np.max(d * (1.0 - seg / cx["paper"]))) if seg.size else 0.0
    return dict(sleeve=sleeve, policy=policy, ts=ev.signal_ts, d=d, kind=w["kind"], r=ex.trade_r(d, f, exit_venue, risk),
                pnl_frac=pnl_frac, risk_pct=risk_pct, notional_x=nx, notional_usd=notional,
                pnl_usd=pnl_frac * notional - cost_usd, cost_usd=cost_usd, i_fill=cx["i0"], i_exit=w["idx"],
                fill_spot=cx["paper"], exit_spot=(exit_venue / ratio), mae=mae, asset=ev.asset)


def daily_mtm_pnl(trades: list[dict], path: ex.PricePath) -> pd.Series:
    """Mark-to-market $ P&L per UTC day from the 1 m path (marks at day-end closes, exit price on the exit day)."""
    acc: dict[int, float] = {}
    for t in trades:
        i0, i1, d, notional, prev = t["i_fill"], t["i_exit"], t["d"], t["notional_usd"], t["fill_spot"]
        t1 = int(path.ts[i1])
        day = int(path.ts[i0]) // 86400
        while True:
            day_end = (day + 1) * 86400
            if day_end > t1:
                mark, last = t["exit_spot"], True
            else:
                j = path.idx_gt(day_end - path.step) - 1
                mark, last = float(path.c[j]), False
            acc[day] = acc.get(day, 0.0) + notional * d * (mark / prev - 1.0)
            if last:
                acc[day] -= t["cost_usd"]
                break
            prev, day = mark, day + 1
    if not acc:
        return pd.Series(dtype=float)
    days = np.arange(min(acc), max(acc) + 1)
    return pd.Series([acc.get(int(k), 0.0) for k in days], index=days)


def curve_from_daily(pnl: pd.Series, capital: float = CAPITAL) -> dict:
    if pnl.empty:
        return dict(maxdd_pct=float("nan"), total_pct=float("nan"), mar=float("nan"), years=0.0)
    eq = capital + pnl.cumsum().to_numpy()
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak * 100.0
    years = max(len(pnl) / 365.25, 0.05)
    total_pct = (eq[-1] / capital - 1.0) * 100.0
    mdd = float(dd.min())
    return dict(maxdd_pct=mdd, total_pct=total_pct, pct_per_year=total_pct / years,
                mar=float((total_pct / years) / abs(mdd)) if mdd < 0 else float("inf"), years=years,
                sharpe=float(bv.sharpe_of(pnl.to_numpy() / capital)))


def policy_summary(trades: list[dict], path: ex.PricePath) -> dict:
    r = np.array([t["r"] for t in trades]); ts = np.array([t["ts"] for t in trades])
    pnl_pct = np.array([t["pnl_usd"] for t in trades]) / CAPITAL * 100.0
    s = ex.summarize_r(r, ts)
    b = ex.block_boot_mean(pnl_pct, ex.day_groups(ts))
    curve = curve_from_daily(daily_mtm_pnl(trades, path))
    h2 = ex.halves(ts)
    return dict(n=len(trades), mean_r=s["mean_r"], r_ci90=s["ci90"], win=s["win"], worst_r=float(r.min()),
                sum_r=s["sum_r"], first_half_r=s["first_half_mean"], second_half_r=s["second_half_mean"],
                mean_pnl_pct=b["mean"], pnl_pct_ci90=b["ci90"],
                first_half_pnl_pct=float(pnl_pct[~h2].mean()), second_half_pnl_pct=float(pnl_pct[h2].mean()),
                kinds=pd.Series([t["kind"] for t in trades]).value_counts().to_dict(),
                mean_notional_x=float(np.mean([t["notional_usd"] for t in trades]) / CAPITAL), **curve)


def liquidation_walk(trades_by_asset: dict[str, list[dict]], capital: float, mm: float = MM_PCT) -> dict:
    """Minute-by-minute cross-margin account walk: equity = capital + realised + unrealised at the adverse
    extreme; maintenance = mm x open notional. Grid = the BTC 1 m path; ETH trades are mapped by timestamp."""
    base = ex.load_path("btc_1m")
    n = len(base.ts)
    unreal = np.zeros(n); notional = np.zeros(n); realised_step = np.zeros(n)
    worst_mae = 0.0
    for asset, trades in trades_by_asset.items():
        p = ex.path_for(asset, "1m")
        for t in trades:
            i0, i1, d = t["i_fill"], t["i_exit"], t["d"]
            adv = p.l[i0:i1 + 1] if d > 0 else p.h[i0:i1 + 1]
            u = t["notional_usd"] * d * (adv / t["fill_spot"] - 1.0)
            worst_mae = max(worst_mae, t["mae"])
            if p is base:
                unreal[i0:i1 + 1] += u; notional[i0:i1 + 1] += t["notional_usd"]
                x_idx = min(i1 + 1, n - 1)
            else:
                idx = base.idx_ge(p.ts[i0]) + np.arange(i1 + 1 - i0)
                idx = idx[idx < n]
                np.add.at(unreal, idx, u[:len(idx)]); np.add.at(notional, idx, t["notional_usd"])
                x_idx = min(int(idx[-1]) + 1, n - 1) if len(idx) else n - 1
            realised_step[x_idx] += t["pnl_usd"]
    realised = np.cumsum(realised_step)
    equity = capital + realised + unreal
    open_ = notional > 0
    breach = open_ & (equity < mm * notional)
    episodes = int(np.sum(breach[1:] & ~breach[:-1]) + (1 if breach.size and breach[0] else 0))
    with np.errstate(divide="ignore", invalid="ignore"):
        dist = np.where(open_, (equity - mm * notional) / np.where(open_, notional, 1.0), np.nan)
        gross = np.where(open_, notional / np.maximum(equity, 1e-9), np.nan)
    k = int(np.nanargmin(dist)) if open_.any() else -1
    return dict(breach_minutes=int(breach.sum()), episodes=episodes, min_distance=float(np.nanmin(dist)) if open_.any() else float("nan"),
                at=ex.iso(base.ts[k]) if k >= 0 else None, max_gross_x=float(np.nanmax(gross)) if open_.any() else 0.0,
                min_equity=float(equity[open_].min()) if open_.any() else capital, worst_mae=worst_mae,
                final_equity=float(capital + realised[-1]), open_minutes=int(open_.sum()))

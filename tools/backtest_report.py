"""Deeper analysis of a completed replay run.

Prints:
  - Yearly / monthly return breakdown
  - Longest drawdown period
  - Per-sleeve trade distribution (N trades, win rate, mean/median PnL %,
    worst/best trade, avg hold time)
  - Direction breakdown (LONG vs SHORT) per sleeve
  - Monthly trade count heatmap

Usage:
  python backtest_report.py
  python backtest_report.py --variant p300_aggressive_v2_v1_0__replay
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# tools/ scripts run from repo root via `python tools/backtest_report.py ...`
# Add the repo root to sys.path so `from services import db` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db  # noqa: E402

DEFAULT_VARIANT = "p300_aggressive_v2_v1_0__replay"


def load_variant_capital(variant_id: str) -> float:
    con = sqlite3.connect(str(db.DASH_DB))
    row = con.execute("SELECT capital_usdt FROM variants WHERE id=?",
                      (variant_id,)).fetchone()
    con.close()
    return float(row[0]) if row and row[0] else 10000.0


def load_nav_series(variant_id: str) -> list[tuple[str, float, float]]:
    """[(date, return_pct, equity)] — equity compounded from return_pct."""
    con = sqlite3.connect(str(db.DASH_DB))
    rows = con.execute("""
        SELECT date, return_1x_pct FROM variant_daily_returns
        WHERE variant_id = ? AND source = 'replay'
        ORDER BY date
    """, (variant_id,)).fetchall()
    con.close()
    # Our return_1x_pct is daily PnL as % of PREVIOUS equity — rebuild equity
    capital = load_variant_capital(variant_id)
    out = []
    eq = capital
    for d, ret_pct in rows:
        eq = eq * (1 + (ret_pct or 0) / 100.0)
        out.append((d, ret_pct or 0, eq))
    return out


def load_trades(variant_id: str) -> list[dict]:
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT * FROM trades
        WHERE strategy_variant = ? AND status = 'closed'
        ORDER BY actual_entry_time
    """, (variant_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def yearly_breakdown(nav: list[tuple[str, float, float]], capital: float) -> dict:
    """{'2024': {start_eq, end_eq, return_pct, days, mdd_pct}}"""
    by_year = defaultdict(list)
    for d, r, eq in nav:
        by_year[d[:4]].append((d, r, eq))
    out = {}
    running_eq = capital
    for year in sorted(by_year):
        series = by_year[year]
        start_eq = running_eq
        end_eq = series[-1][2]
        ret_pct = (end_eq / start_eq - 1) * 100
        peak = start_eq
        mdd = 0.0
        for d, r, eq in series:
            peak = max(peak, eq)
            dd = (eq / peak - 1) * 100 if peak > 0 else 0
            mdd = min(mdd, dd)
        out[year] = {
            "start_eq": start_eq, "end_eq": end_eq,
            "return_pct": ret_pct, "days": len(series), "mdd_pct": mdd,
        }
        running_eq = end_eq
    return out


def longest_drawdown(nav: list[tuple[str, float, float]]) -> dict:
    """Return {'start', 'end', 'duration_days', 'depth_pct'} of the deepest DD."""
    if not nav:
        return {}
    peak = nav[0][2]
    peak_date = nav[0][0]
    worst = {"depth_pct": 0.0, "start": "", "bottom": "", "end": "", "duration": 0}
    dd_start = None
    dd_start_date = None
    for d, r, eq in nav:
        if eq >= peak:
            peak = eq
            peak_date = d
            dd_start = None
        else:
            if dd_start is None:
                dd_start = peak
                dd_start_date = peak_date
            depth = (eq / dd_start - 1) * 100
            if depth < worst["depth_pct"]:
                worst = {"depth_pct": depth, "start": dd_start_date,
                         "bottom": d, "end": "", "duration": 0}
    # Recovery end = first bar where equity >= previous peak after worst bottom
    if worst["bottom"]:
        peak_before = None
        for d, r, eq in nav:
            if d == worst["start"]:
                peak_before = eq
                break
        if peak_before is not None:
            for d, r, eq in nav:
                if d > worst["bottom"] and eq >= peak_before:
                    worst["end"] = d
                    break
        if worst["end"]:
            dstart = datetime.fromisoformat(worst["start"])
            dend = datetime.fromisoformat(worst["end"])
            worst["duration"] = (dend - dstart).days
        else:
            dstart = datetime.fromisoformat(worst["start"])
            dend = datetime.fromisoformat(nav[-1][0])
            worst["duration"] = (dend - dstart).days
            worst["end"] = "(still underwater at end)"
    return worst


def sleeve_distribution(trades: list[dict]) -> dict[str, dict]:
    """Per-sleeve trade distribution stats."""
    import statistics
    by = defaultdict(list)
    for t in trades:
        by[t["strategy"]].append(t)
    out = {}
    for strat, ts in by.items():
        pnls = [float(t.get("pnl_pct") or 0) for t in ts]
        pnls_usdt = [float(t.get("pnl_usdt") or 0) for t in ts]
        wins = sum(1 for p in pnls_usdt if p > 0)
        # Hold time
        holds_h = []
        for t in ts:
            try:
                e = datetime.fromisoformat(t["actual_entry_time"])
                x = datetime.fromisoformat(t["actual_exit_time"])
                holds_h.append((x - e).total_seconds() / 3600.0)
            except (TypeError, ValueError):
                pass
        longs = sum(1 for t in ts if t["direction"] == "LONG")
        shorts = sum(1 for t in ts if t["direction"] == "SHORT")
        out[strat] = {
            "n": len(ts),
            "win_rate_pct": (wins / len(ts) * 100) if ts else 0,
            "mean_pct": statistics.mean(pnls) if pnls else 0,
            "median_pct": statistics.median(pnls) if pnls else 0,
            "best_pct": max(pnls) if pnls else 0,
            "worst_pct": min(pnls) if pnls else 0,
            "total_usdt": sum(pnls_usdt),
            "avg_hold_hours": statistics.mean(holds_h) if holds_h else 0,
            "longs": longs, "shorts": shorts,
        }
    return out


def monthly_heatmap(nav: list[tuple[str, float, float]]) -> dict:
    """{year: {month: return_pct}}"""
    by_month = defaultdict(list)
    for d, r, eq in nav:
        by_month[d[:7]].append((d, r, eq))
    out = defaultdict(dict)
    # Need starting equity for each month
    prev_end = None
    for ym in sorted(by_month):
        year, month = ym.split("-")
        series = by_month[ym]
        start_eq = prev_end if prev_end is not None else series[0][2] / (1 + series[0][1] / 100)
        end_eq = series[-1][2]
        ret = (end_eq / start_eq - 1) * 100 if start_eq > 0 else 0
        out[year][int(month)] = ret
        prev_end = end_eq
    return dict(out)


def print_report(variant_id: str) -> None:
    capital = load_variant_capital(variant_id)
    nav = load_nav_series(variant_id)
    trades = load_trades(variant_id)

    if not nav:
        print(f"No NAV data for {variant_id} — run backtest_runner.py first.")
        return

    print("=" * 80)
    print(f"  Deep replay report — {variant_id}")
    print(f"  Window: {nav[0][0]} to {nav[-1][0]}  |  {len(nav)} days  |  {len(trades)} trades")
    print(f"  Start capital: ${capital:,.0f}  |  Final equity: ${nav[-1][2]:,.2f}")
    total_return = (nav[-1][2] / capital - 1) * 100
    print(f"  Total return:  {total_return:+.2f}%")
    print("=" * 80)

    print("\n  ── Yearly breakdown ──")
    print(f"  {'year':<6} {'days':>5} {'start $':>12} {'end $':>12} {'ret %':>9} {'mdd %':>9}")
    yearly = yearly_breakdown(nav, capital)
    for y, d in yearly.items():
        print(f"  {y:<6} {d['days']:>5} {d['start_eq']:>12,.2f} "
              f"{d['end_eq']:>12,.2f} {d['return_pct']:>9,.2f} {d['mdd_pct']:>9,.2f}")

    print("\n  ── Deepest drawdown ──")
    worst = longest_drawdown(nav)
    if worst:
        print(f"    start:    {worst['start']}")
        print(f"    bottom:   {worst['bottom']}  ({worst['depth_pct']:+.2f}%)")
        print(f"    end:      {worst['end']}")
        print(f"    duration: {worst['duration']} days")

    print("\n  ── Per-sleeve distribution ──")
    header = f"  {'strategy':<14} {'n':>5} {'L/S':>6} {'win%':>6} {'mean%':>8} {'med%':>8} {'best%':>8} {'worst%':>8} {'total $':>12} {'avg h':>7}"
    print(header)
    sd = sleeve_distribution(trades)
    for strat, d in sorted(sd.items(), key=lambda kv: -kv[1]["total_usdt"]):
        ls = f"{d['longs']}/{d['shorts']}"
        print(f"  {strat:<14} {d['n']:>5} {ls:>6} {d['win_rate_pct']:>6.1f} "
              f"{d['mean_pct']:>8.2f} {d['median_pct']:>8.2f} "
              f"{d['best_pct']:>8.2f} {d['worst_pct']:>8.2f} "
              f"{d['total_usdt']:>12,.2f} {d['avg_hold_hours']:>7,.1f}")

    print("\n  ── Monthly returns heatmap (%) ──")
    heat = monthly_heatmap(nav)
    print(f"  {'year':<6} " + " ".join(f"{m:>7}" for m in
                                        ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]) + "    YTD")
    for year in sorted(heat):
        months = heat[year]
        row = f"  {year:<6} "
        ytd = 1.0
        for m in range(1, 13):
            v = months.get(m)
            if v is None:
                row += f"{'-':>7} "
            else:
                row += f"{v:>+7.2f} "
                ytd *= (1 + v / 100)
        row += f"  {(ytd - 1) * 100:>+7.2f}"
        print(row)

    print("=" * 80)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default=DEFAULT_VARIANT)
    args = ap.parse_args()
    print_report(args.variant)


if __name__ == "__main__":
    main()

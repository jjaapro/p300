"""Post-backtest comparison: with-FOMC vs without-FOMC, derived from a
single replay run.

Logic: take the replay variant tagged 'with_fomc', read all closed trades,
build two equity curves:
  A) full set (every closed trade, including FOMC)
  B) excluding strategy='FOMC' (synthesizes the without-FOMC counterfactual)

Then report metrics for each + the FOMC-attributable delta.

This avoids running the same window twice — every other sleeve fires
deterministically given the simulated clock, so removing only FOMC trades
from PnL aggregation gives the exact counterfactual.

Usage:  python compare_with_fomc.py --tag with_fomc
"""
from __future__ import annotations

import argparse
import math
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent
DASH_DB = REPO / "data" / "dashboard.db"


def load_trades(variant_id: str) -> list[dict]:
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT id, asset, strategy, direction, actual_entry_time,
               actual_exit_time, entry_price, exit_price, size_usdt,
               pnl_usdt, pnl_pct, allocation_pct, leverage, notes
        FROM trades
        WHERE strategy_variant = ? AND status = 'closed'
        ORDER BY actual_exit_time
    """, (variant_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def daily_nav(trades: list[dict], capital: float, start: date, end: date,
              exclude_strategies: set[str] | None = None) -> list[dict]:
    """Build per-day equity curve from closed trades."""
    exclude = exclude_strategies or set()
    daily_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        if t["strategy"] in exclude:
            continue
        if not t["actual_exit_time"]:
            continue
        d = t["actual_exit_time"][:10]
        daily_pnl[d] += float(t["pnl_usdt"] or 0)
    rows = []
    equity = capital
    d = start
    while d <= end:
        pnl = daily_pnl.get(d.isoformat(), 0.0)
        equity += pnl
        ret_pct = (pnl / (equity - pnl) * 100) if (equity - pnl) > 0 else 0.0
        rows.append({"date": d.isoformat(), "equity": equity, "pnl": pnl,
                      "return_pct": ret_pct})
        d = d + timedelta(days=1)
    return rows


def metrics(nav: list[dict], capital: float) -> dict:
    if not nav:
        return {}
    rets = [r["return_pct"] / 100 for r in nav]
    final = nav[-1]["equity"]
    n = len(nav); years = n / 365.25
    cagr = (final / capital) ** (1 / years) - 1 if years > 0 and final > 0 else float("nan")
    if len(rets) > 1:
        m = sum(rets) / len(rets)
        v = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
        sd = math.sqrt(v)
        sharpe = (m / sd) * math.sqrt(365) if sd > 0 else float("nan")
    else:
        sharpe = float("nan")
    peak = capital; mdd = 0.0
    for r in nav:
        peak = max(peak, r["equity"])
        dd = (r["equity"] / peak) - 1 if peak > 0 else 0
        mdd = min(mdd, dd)
    return {"final": final, "total_return": (final / capital - 1) * 100,
            "cagr": cagr * 100 if not math.isnan(cagr) else float("nan"),
            "sharpe": sharpe, "mdd": mdd * 100, "n_days": n}


def per_sleeve(trades: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    by_strat: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_strat[t["strategy"]].append(t)
    for strat, items in by_strat.items():
        n = len(items)
        wins = sum(1 for t in items if (t["pnl_usdt"] or 0) > 0)
        total = sum(t["pnl_usdt"] or 0 for t in items)
        avg_pct = sum(t["pnl_pct"] or 0 for t in items) / n if n else 0
        out[strat] = {"n": n, "win_rate": (wins / n * 100) if n else 0,
                       "total_pnl": total, "avg_pct": avg_pct}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", required=True,
                    help="Replay variant tag (e.g. 'with_fomc'). The full "
                         "variant id is p300_aggressive_v2_v1_0__replay_<tag>.")
    args = ap.parse_args(argv)

    variant_id = f"p300_aggressive_v2_v1_0__replay_{args.tag}"
    con = sqlite3.connect(str(DASH_DB))
    v = con.execute("SELECT capital_usdt FROM variants WHERE id=?",
                     (variant_id,)).fetchone()
    con.close()
    if v is None:
        raise SystemExit(f"Variant {variant_id} not found.")
    capital = float(v[0])

    trades = load_trades(variant_id)
    if not trades:
        raise SystemExit(f"No closed trades for {variant_id}.")
    start = date.fromisoformat(min(t["actual_entry_time"][:10] for t in trades))
    end = date.fromisoformat(max(t["actual_exit_time"][:10] for t in trades))

    nav_full = daily_nav(trades, capital, start, end)
    nav_no_fomc = daily_nav(trades, capital, start, end, exclude_strategies={"FOMC"})
    m_full = metrics(nav_full, capital)
    m_no_fomc = metrics(nav_no_fomc, capital)
    sleeves = per_sleeve(trades)

    print("=" * 78)
    print(f"  P-300 TACTICAL STACK — REPLAY  ({start} -> {end})")
    print(f"  variant_id: {variant_id}")
    print(f"  starting capital: ${capital:,.2f}")
    print(f"  NOTE: JPLUS-CORE (50% of intended portfolio) NOT included in trades NAV.")
    print("=" * 78)
    print()
    print(f"  {'metric':<25}  {'WITH FOMC':>14}  {'WITHOUT FOMC':>14}  {'DELTA':>10}")
    print(f"  {'-'*25}  {'-'*14}  {'-'*14}  {'-'*10}")
    if m_full and m_no_fomc:
        for label, key, fmt in [
            ("Final equity",  "final",        "${:>13,.2f}"),
            ("Total return",  "total_return", "{:>13.2f}%"),
            ("CAGR",          "cagr",         "{:>13.2f}%"),
            ("Sharpe (ann)",  "sharpe",       "{:>14.2f}"),
            ("Max drawdown",  "mdd",          "{:>13.2f}%"),
        ]:
            a = m_full.get(key); b = m_no_fomc.get(key)
            if isinstance(a, float) and isinstance(b, float):
                if "%" in fmt:
                    delta = f"{a-b:+9.2f}%"
                elif "$" in fmt:
                    delta = f"${a-b:+9,.2f}"
                else:
                    delta = f"{a-b:+9.2f}"
                print(f"  {label:<25}  {fmt.format(a):>14}  {fmt.format(b):>14}  {delta:>10}")
    print()
    print("  Per-sleeve attribution (closed trades):")
    print(f"  {'strategy':<14} {'n':>5} {'win%':>6} {'total $':>14} {'avg %':>9}")
    for strat in sorted(sleeves, key=lambda s: -sleeves[s]["total_pnl"]):
        d = sleeves[strat]
        print(f"  {strat:<14} {d['n']:>5} {d['win_rate']:>6.1f} "
              f"{d['total_pnl']:>14,.2f} {d['avg_pct']:>9.2f}")
    print("=" * 78)

    # Drill-down on FOMC trades
    fomc_trades = [t for t in trades if t["strategy"] == "FOMC"]
    if fomc_trades:
        print()
        print(f"  FOMC trades ({len(fomc_trades)} total):")
        print(f"  {'entry':<19} {'exit':<19} {'entry$':>9} {'exit$':>9} {'pnl%':>7}")
        for t in fomc_trades:
            print(f"  {t['actual_entry_time'][:19]:<19} "
                  f"{t['actual_exit_time'][:19]:<19} "
                  f"${t['entry_price']:>8,.0f} ${t['exit_price']:>8,.0f} "
                  f"{t['pnl_pct'] or 0:>+6.2f}%")


if __name__ == "__main__":
    main()

"""Leverage sensitivity for the FOMC sleeve, run against any replay tag.

Reads the strategy_variant's FOMC trades, scales each trade's PnL by an
alternative leverage multiplier (since pnl_pct is per-size_usdt and size
scales linearly with leverage), then computes:
  - Total portfolio % impact at each leverage level
  - Max drawdown contribution from FOMC alone (sleeve-level)
  - Worst losing streak in capital terms
  - Per-trade win rate (unchanged by leverage; just re-checks)

Optionally runs a Monte Carlo bootstrap on the realized trade distribution
to project worst-case DD at each leverage level.

Usage:  python fomc_leverage_sensitivity.py --tag with_fomc_v3 [--mc 10000]
"""
from __future__ import annotations

import argparse
import random
import sqlite3
from pathlib import Path
from strategies.support import db

REPO = Path(__file__).resolve().parent.parent
def load_trades(variant_id: str) -> list[dict]:
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT actual_entry_time, actual_exit_time, entry_price, exit_price,
               size_usdt, pnl_usdt, pnl_pct
        FROM trades
        WHERE strategy_variant=? AND strategy='FOMC' AND status='closed'
        ORDER BY actual_entry_time
    """, (variant_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def sleeve_metrics_at_leverage(spot_moves: list[float], alloc: float,
                                  leverage: float, cost_bp_rt: float = 10.0) -> dict:
    """Compute portfolio-% return per trade at a hypothetical leverage,
    then DD/streak metrics on the cumulative capital curve."""
    pnl_pcts = []
    for sm in spot_moves:
        port_pct = sm * alloc * leverage * 100 \
                   - (cost_bp_rt / 10000) * alloc * leverage * 100
        pnl_pcts.append(port_pct)
    eq = 1.0; peak = 1.0; max_dd = 0.0
    streak = 0.0; max_streak = 0.0
    for r in pnl_pcts:
        eq *= (1 + r/100)
        peak = max(peak, eq)
        dd = (eq/peak - 1) * 100
        max_dd = min(max_dd, dd)
        if r < 0:
            streak += r
            max_streak = min(max_streak, streak)
        else:
            streak = 0
    wins = sum(1 for r in pnl_pcts if r > 0)
    return {
        "n": len(pnl_pcts),
        "wins": wins,
        "win_rate": wins / len(pnl_pcts) * 100 if pnl_pcts else 0,
        "total_return": (eq - 1) * 100,
        "mean_pct": sum(pnl_pcts) / len(pnl_pcts) if pnl_pcts else 0,
        "best": max(pnl_pcts) if pnl_pcts else 0,
        "worst": min(pnl_pcts) if pnl_pcts else 0,
        "max_dd": max_dd,
        "max_loss_streak": max_streak,
    }


def monte_carlo(spot_moves: list[float], alloc: float, leverage: float,
                  n_runs: int = 10000, seed: int = 42) -> dict:
    """Bootstrap (with replacement) from the realized spot moves to project
    distribution of cumulative return + max DD over the same number of
    events. Useful for understanding how lucky/unlucky the realized path was."""
    if not spot_moves:
        return {}
    random.seed(seed)
    n = len(spot_moves)
    cost = 10.0 / 10000 * alloc * leverage * 100
    totals = []; max_dds = []
    for _ in range(n_runs):
        eq = 1.0; peak = 1.0; max_dd = 0.0
        for _ in range(n):
            sm = random.choice(spot_moves)
            port_pct = sm * alloc * leverage * 100 - cost
            eq *= (1 + port_pct/100)
            peak = max(peak, eq)
            dd = (eq/peak - 1) * 100
            max_dd = min(max_dd, dd)
        totals.append((eq - 1) * 100)
        max_dds.append(max_dd)
    totals.sort(); max_dds.sort()
    return {
        "p5_total": totals[int(0.05*n_runs)],
        "p50_total": totals[int(0.50*n_runs)],
        "p95_total": totals[int(0.95*n_runs)],
        "p5_dd": max_dds[int(0.05*n_runs)],
        "p50_dd": max_dds[int(0.50*n_runs)],
        "p95_dd": max_dds[int(0.95*n_runs)],
        "worst_dd": max_dds[0],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--alloc", type=float, default=5.0,
                    help="FOMC allocation %% of capital (default 5.0)")
    ap.add_argument("--mc", type=int, default=0,
                    help="Monte Carlo runs (0 = skip MC). 10000 is good.")
    args = ap.parse_args(argv)

    variant_id = f"p300_aggressive_v2_v1_0__replay_{args.tag}"
    trades = load_trades(variant_id)
    if not trades:
        raise SystemExit(f"No FOMC trades for {variant_id}")
    spot_moves = [(t["exit_price"] / t["entry_price"] - 1) for t in trades]

    print(f"FOMC sleeve realized trades for {variant_id}:")
    print(f"  n={len(trades)}, wins={sum(1 for s in spot_moves if s>0)}, "
          f"mean spot move = {sum(spot_moves)/len(spot_moves)*100:+.3f}%")
    print()
    print(f"=== LEVERAGE SENSITIVITY  (alloc={args.alloc}% of capital) ===")
    print(f"{'k':<5}{'CumRet':>9}{'PerTrade':>10}{'Best':>8}{'Worst':>8}"
          f"{'MaxDD':>8}{'MaxLossStreak':>15}{'WinRate':>9}")
    for lev in [1, 2, 3, 5, 7.5, 10, 12.5, 15, 20, 25, 30]:
        m = sleeve_metrics_at_leverage(spot_moves, args.alloc/100, lev)
        print(f"  {lev:<3}x {m['total_return']:>+7.2f}% {m['mean_pct']:>+8.3f}%"
              f" {m['best']:>+6.2f}% {m['worst']:>+6.2f}%"
              f" {m['max_dd']:>+6.2f}% {m['max_loss_streak']:>+12.2f}%"
              f" {m['win_rate']:>7.0f}%")

    if args.mc > 0:
        print()
        print(f"=== MONTE CARLO ({args.mc} runs, sampling with replacement) ===")
        print(f"  Sleeve P&L distribution if future cohort matches realized stats")
        print(f"  Hold size: alloc={args.alloc}% × leverage")
        print(f"{'k':<5}{'P5_total':>10}{'P50':>9}{'P95':>9}"
              f"{'P5_DD':>9}{'P50_DD':>9}{'P95_DD':>9}{'WorstDD':>10}")
        for lev in [5, 10, 15, 20, 25, 30]:
            mc = monte_carlo(spot_moves, args.alloc/100, lev, args.mc)
            print(f"  {lev:<3}x {mc['p5_total']:>+7.1f}% {mc['p50_total']:>+7.1f}%"
                  f" {mc['p95_total']:>+7.1f}% {mc['p5_dd']:>+7.2f}%"
                  f" {mc['p50_dd']:>+7.2f}% {mc['p95_dd']:>+7.2f}%"
                  f" {mc['worst_dd']:>+8.2f}%")


if __name__ == "__main__":
    main()

"""Drill-down report on FOMC trades from a backtest replay.

Shows: per-trade decision, phase, F&G bucket, entry/exit prices, P&L,
versus the historical baseline (52-event mean +1.10% per traded event,
73% win rate at T-10h -> T+0.5h).

Also computes:
  - Cumulative FOMC P&L curve across the replay window
  - Per-phase win-rate breakdown for trades the sleeve actually took
  - Compare actual replay results to ex-ante expected from the rule
    (peak_hold expects 100%, hiking expects 83%, etc.)
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import date
from pathlib import Path
from strategies.support import db

REPO = Path(__file__).resolve().parent.parent
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", required=True)
    args = ap.parse_args(argv)

    variant_id = f"p300_aggressive_v2_v1_0__replay_{args.tag}"

    # Pull FOMC trades + observer rows + join on fomc_date inferred from notes
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    trades = con.execute("""
        SELECT id, asset, actual_entry_time, actual_exit_time,
               entry_price, exit_price, size_usdt, leverage,
               pnl_usdt, pnl_pct, notes
        FROM trades
        WHERE strategy_variant=? AND strategy='FOMC' AND status='closed'
        ORDER BY actual_entry_time
    """, (variant_id,)).fetchall()
    con.close()

    if not trades:
        print(f"No FOMC trades for variant {variant_id}.")
        return

    # Pull observer rows for cross-reference
    con = sqlite3.connect(str(db.TRADER_DB))
    con.row_factory = sqlite3.Row
    obs = {r["fomc_date"]: dict(r) for r in con.execute(
        "SELECT * FROM fomc_observer").fetchall()}
    con.close()

    print("=" * 92)
    print(f"  FOMC SLEEVE — DRILL-DOWN  (variant: {variant_id})")
    print("=" * 92)
    print(f"  {'fomc':<11} {'phase':<11} {'fg':>3} {'fg_b':<6} "
          f"{'expected':<10} "
          f"{'entry$':>8} {'exit$':>8} "
          f"{'spot%':>6} {'pnl%':>7} {'$pnl':>9}")
    print("  " + "-" * 88)

    cum_pnl = 0.0
    by_phase: dict[str, list[float]] = {}
    for t in trades:
        # Derive fomc_date from notes JSON
        import json
        notes_obj = {}
        if t["notes"]:
            try:
                notes_obj = json.loads(t["notes"].split("\n")[0])
            except (ValueError, json.JSONDecodeError):
                pass
        fomc_date = notes_obj.get("fomc_date", t["actual_entry_time"][:10])
        ob = obs.get(fomc_date) or {}
        phase = (ob.get("phase") or notes_obj.get("phase") or "?")[:10]
        fg = ob.get("fear_greed")
        fg_b = (ob.get("fear_greed_bucket") or "?").replace("_", "")[:5]
        ea = (ob.get("expected_action") or notes_obj.get("expected_action") or "?")[:9]

        spot_pct = (t["exit_price"] / t["entry_price"] - 1) * 100
        pnl_pct = t["pnl_pct"] or 0
        pnl_usdt = t["pnl_usdt"] or 0
        cum_pnl += pnl_usdt
        by_phase.setdefault(phase, []).append(pnl_pct)

        print(f"  {fomc_date:<11} {phase:<11} {(fg or '?'):>3} {fg_b:<6} "
              f"{ea:<10} "
              f"${t['entry_price']:>7,.0f} ${t['exit_price']:>7,.0f} "
              f"{spot_pct:+5.2f}% {pnl_pct:+6.2f}% {pnl_usdt:+8,.2f}")

    print("  " + "-" * 88)
    n = len(trades)
    wins = sum(1 for t in trades if (t["pnl_usdt"] or 0) > 0)
    avg_pct = sum(t["pnl_pct"] or 0 for t in trades) / n
    print(f"  Totals: n={n}  wins={wins} ({wins/n*100:.0f}%)  "
          f"avg_pnl_pct={avg_pct:+.2f}%  cumulative ${cum_pnl:+,.2f}")
    print()
    print("  By phase (observed in this replay):")
    print(f"  {'phase':<14} {'n':>4} {'win%':>6} {'mean_pct':>9}")
    for phase in sorted(by_phase):
        rs = by_phase[phase]
        w = sum(1 for r in rs if r > 0)
        print(f"  {phase:<14} {len(rs):>4} {w/len(rs)*100:>5.0f}% {sum(rs)/len(rs):+8.2f}%")
    print("=" * 92)


if __name__ == "__main__":
    main()

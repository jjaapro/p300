"""Equity curve + max-drawdown comparison of entry mechanics on the edge signal.

Reads the per-signal R produced by experiment_ladder.py and builds a
fixed-1R-risk equity curve per entry arm (trades summed in time order), then
reports cum R, max drawdown (R), and return/DD.

IMPORTANT scope note: this uses the EXPERIMENT exit model (structural stop just
beyond the dwellblock / 3R target / 72h TIF) on the 83 edge-signal triggers that
fall in the 5s window — NOT the chento_triple_v3 production exit model. So
'market' is the closest proxy to the CURRENT prod entry (signal-price fill), and
the market->shallow delta is the RELATIVE effect of the entry change. A faithful
prod comparison requires replaying the prod exit model (separate step).

Run: python studies/notebooks/dwell_block/equity_compare.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
r = pd.read_csv(HERE / "ladder_results.csv").sort_values("ts").reset_index(drop=True)

ARMS = ["market", "shallow0.2", "deep_single", "deep_ladder", "cascade"]
rows = []
curves = {}
for a in ARMS:
    s = r[a].dropna().to_numpy()
    cum = np.cumsum(s)
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    curves[a] = (r.loc[r[a].notna(), "ts"].to_numpy(), cum)
    rows.append({
        "arm": a, "n": len(s),
        "cumR": round(float(cum[-1]), 2),
        "meanR": round(float(s.mean()), 3),
        "maxDD_R": round(float(dd.min()), 2),
        "ret_over_DD": round(float(cum[-1] / abs(dd.min())), 2) if dd.min() < 0 else None,
    })

stats = pd.DataFrame(rows)
stats.to_csv(HERE / "equity_stats.csv", index=False)
with (HERE / "equity_stats.txt").open("w", encoding="utf-8") as fh:
    fh.write(stats.to_string(index=False))
    mk = stats[stats.arm == "market"].iloc[0]
    sh = stats[stats.arm == "shallow0.2"].iloc[0]
    fh.write(f"\n\nmarket -> shallow0.2:  cumR {mk.cumR:+.1f} -> {sh.cumR:+.1f}  "
             f"({(sh.cumR/mk.cumR-1)*100:+.0f}%);  "
             f"maxDD {mk.maxDD_R:+.1f} -> {sh.maxDD_R:+.1f} R;  "
             f"ret/DD {mk.ret_over_DD} -> {sh.ret_over_DD}")

# equity curves figure
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 5))
    style = {"market": ("#888", "-"), "shallow0.2": ("#2ca02c", "-"),
             "deep_single": ("#d62728", "--"), "deep_ladder": ("#ff7f0e", ":"),
             "cascade": ("#9467bd", ":")}
    for a in ARMS:
        ts, cum = curves[a]
        c, ls = style[a]
        ax.plot(pd.to_datetime(ts), cum, ls, color=c, lw=2 if a in ("market", "shallow0.2") else 1.2,
                label=f"{a}  (cumR {cum[-1]:+.0f}, maxDD {stats[stats.arm==a].iloc[0].maxDD_R:+.0f})")
    ax.set_ylabel("cumulative R (fixed 1R risk/trade)")
    ax.set_title("Entry-mechanic equity curves on the edge signal (n=83, experiment exit model)\n"
                 "shallow pullback: higher return AND shallower drawdown than market", fontsize=10)
    ax.legend(fontsize=8); ax.grid(alpha=0.15); ax.axhline(0, color="#999", lw=0.6)
    fig.tight_layout(); fig.savefig(HERE / "equity_curves.png", dpi=120)
except Exception as e:  # noqa: BLE001
    print("plot skipped:", e)

print(stats.to_string(index=False))

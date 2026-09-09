"""S-080 grid harness — port of trader `backtest_grid_realistic.py` with p300
costs and the pre-registered metrics (README.md). Read-only on prod.db.

Run:  venv\\Scripts\\python studies/notebooks/grid_study/grid_harness.py [--bars 1m|5m]
Writes results/grid_daily_<cell>.csv and results/grid_summary.csv.
"""
from __future__ import annotations

import argparse
import math
import random
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[2]))
from strategies.support import db  # noqa: E402

RESULTS = _HERE / "results"
START = "2020-01-01"
HALF_SPLIT = "2023-05-01"
CELLS = [  # (label, grid_pct, fee_grid_side, fee_close_side)
    ("A_maker_0.2", 0.2, 0.0002, 0.00075),
    ("A_maker_0.5", 0.5, 0.0002, 0.00075),
    ("B_taker_0.2", 0.2, 0.00075, 0.00075),
    ("B_taker_0.5", 0.5, 0.00075, 0.00075),
]
N_GRIDS = 10
FILL_RATE = 0.50
SLIPPAGE_PCT = 0.02


def load_bars(resolution: str) -> list[tuple[int, float, float, float]]:
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        d = pd.read_sql(
            "SELECT open_time, high, low, close FROM btc_1m WHERE open_time >= ? "
            "ORDER BY open_time", con,
            params=(int(datetime.fromisoformat(START + "T00:00:00+00:00").timestamp() * 1000),))
    finally:
        con.close()
    d["ts"] = pd.to_datetime(d.open_time, unit="ms", utc=True)
    d = d.set_index("ts").drop(columns="open_time")
    if resolution == "5m":
        d = d.resample("5min").agg(high=("high", "max"), low=("low", "min"),
                                   close=("close", "last")).dropna()
    return [(int(t.timestamp()), float(h), float(l), float(c))
            for t, h, l, c in zip(d.index, d.high.values, d.low.values, d.close.values)]


class GridBot:
    """Long-only limit ladder: buy a level on touch from above, sell one
    spacing up. Equal lot per level. Fees as fractions per side."""

    def __init__(self, mid, grid_pct, n_grids, capital, slippage_pct, fill_rate,
                 fee_grid, fee_close, seed=42):
        self.grid_pct, self.n_grids, self.capital = grid_pct, n_grids, capital
        self.slip = slippage_pct / 100.0
        self.fill_rate, self.fee_grid, self.fee_close = fill_rate, fee_grid, fee_close
        self.rng = random.Random(seed)
        self.mid = mid
        self.spacing = mid * grid_pct / 100.0
        self.levels = [{"price": mid + i * self.spacing, "has_inv": False, "bp": 0.0}
                       for i in range(-n_grids, n_grids + 1)]
        self.pos_size = capital / (2 * n_grids)
        self.realized = 0.0
        self.cycles = 0
        self.missed = 0

    def process_bar(self, h, l):
        pnl = 0.0
        cyc = 0
        for lv in self.levels:
            p = lv["price"]
            if not lv["has_inv"] and l <= p <= h:
                if self.rng.random() > self.fill_rate:
                    self.missed += 1
                    continue
                lv["has_inv"] = True
                lv["bp"] = p * (1 + self.slip)
            elif lv["has_inv"]:
                sell_target = lv["bp"] + self.spacing
                sell_price = sell_target * (1 - self.slip)
                if sell_price <= h:
                    if self.rng.random() > self.fill_rate:
                        self.missed += 1
                        continue
                    units = self.pos_size / lv["bp"]
                    profit = units * (sell_price - lv["bp"])
                    fees = units * (lv["bp"] + sell_price) * self.fee_grid
                    pnl += (profit - fees) / self.capital
                    cyc += 1
                    lv["has_inv"] = False
                    lv["bp"] = 0.0
        self.realized += pnl
        self.cycles += cyc
        return pnl, cyc

    def unrealized(self, price):
        unr = 0.0
        for lv in self.levels:
            if lv["has_inv"]:
                unr += (self.pos_size / lv["bp"]) * (price - lv["bp"])
        return unr / self.capital

    def close_all(self, price):
        pnl = 0.0
        for lv in self.levels:
            if lv["has_inv"]:
                units = self.pos_size / lv["bp"]
                sell_price = price * (1 - self.slip)
                pnl += (units * (sell_price - lv["bp"])
                        - units * (lv["bp"] + sell_price) * self.fee_close) / self.capital
                lv["has_inv"] = False
        self.realized += pnl
        return pnl


def simulate(bars, grid_pct, fee_grid, fee_close) -> tuple[pd.DataFrame, int, int]:
    bot = GridBot(bars[0][3], grid_pct, N_GRIDS, 1.0, SLIPPAGE_PCT, FILL_RATE, fee_grid, fee_close)
    daily = defaultdict(lambda: {"realized": 0.0, "cycles": 0, "unrealized": 0.0})
    recenters = 0
    for ts, h, l, c in bars:
        if not (h > 0 and l > 0 and c > 0):
            continue
        d = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        r, k = bot.process_bar(h, l)
        daily[d]["realized"] += r
        daily[d]["cycles"] += k
        daily[d]["unrealized"] = bot.unrealized(c)
        if abs(c - bot.mid) > N_GRIDS * bot.spacing * 2:
            daily[d]["realized"] += bot.close_all(c)
            daily[d]["unrealized"] = 0.0
            recenters += 1
            bot = GridBot(c, grid_pct, N_GRIDS, 1.0, SLIPPAGE_PCT, FILL_RATE, fee_grid, fee_close,
                          seed=bot.rng.randint(0, 99999))
    last = bars[-1]
    d = datetime.fromtimestamp(last[0], tz=timezone.utc).date().isoformat()
    daily[d]["realized"] += bot.close_all(last[3])
    daily[d]["unrealized"] = 0.0
    df = pd.DataFrame.from_dict(daily, orient="index").sort_index()
    df.index = pd.to_datetime(df.index)
    equity = df.realized.cumsum() + df.unrealized       # % of capital, mark-to-market
    df["equity"] = equity
    df["ret"] = equity.diff().fillna(equity.iloc[0])    # daily mark-to-market return
    return df, int(df.cycles.sum()), recenters   # total over every grid instance, not just the last


def metrics(df: pd.DataFrame, cycles: int, recenters: int, label: str) -> dict:
    def sharpe(x):
        return float(x.mean() / x.std(ddof=1) * math.sqrt(365)) if len(x) > 30 and x.std(ddof=1) > 0 else float("nan")
    h1 = df.ret[df.index < HALF_SPLIT]
    h2 = df.ret[df.index >= HALF_SPLIT]
    eq = df.equity
    dd = (eq.cummax() - eq).max()
    years = (df.index[-1] - df.index[0]).days / 365.25
    ann = float(eq.iloc[-1]) / years
    per_year = df.ret.groupby(df.index.year).sum().round(4).to_dict()
    return {"cell": label, "n_days": len(df), "total_pct": float(eq.iloc[-1]) * 100,
            "ann_pct": ann * 100, "max_dd_pct": float(dd) * 100,
            "mar": (ann / dd) if dd > 0 else float("inf"),
            "sharpe_h1": sharpe(h1), "sharpe_h2": sharpe(h2), "sharpe_full": sharpe(df.ret),
            "cycles_per_year": cycles / years, "recenters": recenters,
            "per_year_pct": {k: v * 100 for k, v in per_year.items()}}


def verdict(rows: list[dict]) -> str:
    a = [r for r in rows if r["cell"].startswith("A_")]
    def passes(r):
        return r["sharpe_h1"] > 0 and r["sharpe_h2"] > 0 and r["mar"] >= 0.5
    a_pass = [r["cell"] for r in a if passes(r)]
    if not a_pass:
        return "KILL — no maker-cost cell has Sharpe > 0 in both halves AND MAR ≥ 0.5"
    b = [r for r in rows if r["cell"].startswith("B_") and passes(r)]
    if not b:
        return f"PARTIAL — maker cells {a_pass} pass but no taker cell does; not sleeve-worthy without maker execution"
    return f"SURVIVES — {a_pass} (maker) and {[r['cell'] for r in b]} (taker) pass; open a sleeve-design discussion"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", default="1m", choices=["1m", "5m"])
    args = ap.parse_args(argv)
    RESULTS.mkdir(exist_ok=True)
    bars = load_bars(args.bars)
    print(f"{len(bars):,} {args.bars} bars, {datetime.fromtimestamp(bars[0][0], tz=timezone.utc).date()} "
          f"-> {datetime.fromtimestamp(bars[-1][0], tz=timezone.utc).date()}")
    rows = []
    for label, grid_pct, fee_grid, fee_close in CELLS:
        df, cycles, recenters = simulate(bars, grid_pct, fee_grid, fee_close)
        df.to_csv(RESULTS / f"grid_daily_{label}.csv")
        m = metrics(df, cycles, recenters, label)
        rows.append(m)
        print(f"{label:12s} total {m['total_pct']:+7.1f}%  ann {m['ann_pct']:+6.1f}%  "
              f"maxDD {m['max_dd_pct']:5.1f}%  MAR {m['mar']:5.2f}  "
              f"Sharpe h1 {m['sharpe_h1']:+.2f} h2 {m['sharpe_h2']:+.2f}  "
              f"cycles/yr {m['cycles_per_year']:,.0f}  recenters {m['recenters']}")
        print("    per year:", {k: round(v, 1) for k, v in m["per_year_pct"].items()})
    pd.DataFrame(rows).drop(columns="per_year_pct").to_csv(RESULTS / "grid_summary.csv", index=False)
    print("\nPre-registered verdict:", verdict(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

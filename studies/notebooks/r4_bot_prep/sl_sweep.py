"""R4 bot prep — stop-loss grid and late-entry cost curve on 1m bars.

Pre-registered in README.md (2026-09-06). Read-only against prod.db.
Run:  python studies/notebooks/r4_bot_prep/sl_sweep.py
Writes results/sl_sweep.csv and results/late_entry.csv next to this file.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
sys.path.insert(0, str(_REPO))
from strategies.support import db  # noqa: E402

RESULTS = _HERE / "results"
COST_BP_RT = 15.0
STOP_GRID = (0.02, 0.03, 0.05, None)
LATENESS_MIN = (0, 5, 10, 15, 30, 60)
POST_ETF = pd.Timestamp("2024-01-11", tz="UTC")
START = pd.Timestamp("2020-01-01", tz="UTC")

# strategy -> (asset, weekday set, entry hour, hold hours, key_next_day)
WINDOWS = {
    "JPLUS_R4_BTC":    ("BTC", {0},    6,  12, False),
    "JPLUS_R4_ETH":    ("ETH", {1},    20, 24, True),
    "JPLUS_R4_BTC_V2": ("BTC", {2, 4}, 4,  10, False),
    "JPLUS_R4_ETH_V2": ("ETH", {2, 4}, 4,  10, False),
}


def load_1m(table: str) -> pd.DataFrame:
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        d = pd.read_sql(
            f"SELECT open_time, open, high, low, close FROM {table} "
            f"WHERE open_time >= ? ORDER BY open_time",
            con, params=(int(START.timestamp() * 1000),))
    finally:
        con.close()
    d["ts"] = pd.to_datetime(d.open_time, unit="ms", utc=True)
    return d.set_index("ts").drop(columns="open_time")


def window_days(asset_df: pd.DataFrame, weekdays: set[int], key_next_day: bool):
    """Yield entry-day dates (UTC) that satisfy the calendar predicate."""
    for day in pd.date_range(asset_df.index[0].floor("D"), asset_df.index[-1].floor("D"),
                             freq="D", tz="UTC"):
        if day.weekday() not in weekdays:
            continue
        dom = (day + timedelta(days=1)).day if key_next_day else day.day
        if dom > 14:
            continue
        yield day


def simulate(df: pd.DataFrame, entry_ts, exit_ts, stop_pct):
    """Return (gross_return, stopped) for one fire, or None if bars are missing."""
    try:
        e_bar = df.loc[entry_ts]
        x_bar = df.loc[exit_ts]
    except KeyError:
        return None
    entry = float(e_bar["open"])
    exit_px = float(x_bar["open"])
    if entry <= 0 or exit_px <= 0:
        return None
    if stop_pct is None:
        return (exit_px / entry - 1.0, False)
    stop_px = entry * (1.0 - stop_pct)
    path = df.loc[entry_ts:exit_ts - timedelta(minutes=1), "low"]
    if (path <= stop_px).any():
        return (stop_px / entry - 1.0, True)
    return (exit_px / entry - 1.0, False)


def run() -> tuple[pd.DataFrame, pd.DataFrame]:
    bars = {"BTC": load_1m("btc_1m"), "ETH": load_1m("eth_1m")}
    sl_rows, late_rows = [], []
    for strat, (asset, wds, eh, hold, key_next) in WINDOWS.items():
        df = bars[asset]
        for day in window_days(df, wds, key_next):
            entry0 = day + timedelta(hours=eh)
            exit_ts = entry0 + timedelta(hours=hold)
            era = "post_etf" if day >= POST_ETF else "pre_etf"
            for sl in STOP_GRID:
                r = simulate(df, entry0, exit_ts, sl)
                if r is None:
                    continue
                sl_rows.append(dict(strategy=strat, era=era, day=day.date(),
                                    stop=("none" if sl is None else f"{sl:.0%}"),
                                    gross_bp=r[0] * 1e4, net_bp=r[0] * 1e4 - COST_BP_RT,
                                    stopped=r[1]))
            for late in LATENESS_MIN:
                r = simulate(df, entry0 + timedelta(minutes=late), exit_ts, None)
                if r is None:
                    continue
                late_rows.append(dict(strategy=strat, era=era, day=day.date(),
                                      late_min=late, net_bp=r[0] * 1e4 - COST_BP_RT))
    return pd.DataFrame(sl_rows), pd.DataFrame(late_rows)


def summarize(sl: pd.DataFrame, late: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    def _t(x):
        return x.mean() / x.std(ddof=1) * np.sqrt(len(x)) if len(x) > 1 and x.std(ddof=1) > 0 else np.nan
    g = (sl.groupby(["strategy", "era", "stop"])
           .agg(n=("net_bp", "size"), mean_bp=("net_bp", "mean"), worst_bp=("net_bp", "min"),
                hit=("net_bp", lambda x: (x > 0).mean()), cum_bp=("net_bp", "sum"),
                t=("net_bp", _t), stop_rate=("stopped", "mean"))
           .reset_index())
    base = g[g.stop == "none"].set_index(["strategy", "era"])
    g["d_mean_vs_none_bp"] = g.apply(
        lambda r: r.mean_bp - base.loc[(r.strategy, r.era), "mean_bp"], axis=1)
    g["worst_shrink_pct"] = g.apply(
        lambda r: (1 - r.worst_bp / base.loc[(r.strategy, r.era), "worst_bp"]) * 100
        if base.loc[(r.strategy, r.era), "worst_bp"] < 0 else np.nan, axis=1)
    lg = (late.groupby(["strategy", "era", "late_min"])
              .agg(n=("net_bp", "size"), mean_bp=("net_bp", "mean"), t=("net_bp", _t))
              .reset_index())
    base_l = lg[lg.late_min == 0].set_index(["strategy", "era"])
    lg["loss_vs_0_bp"] = lg.apply(
        lambda r: base_l.loc[(r.strategy, r.era), "mean_bp"] - r.mean_bp, axis=1)
    return g, lg


def decide(g: pd.DataFrame, lg: pd.DataFrame) -> dict:
    """Apply the pre-registered rules verbatim."""
    verdict = {}
    passing = []
    for stop in ("5%", "3%", "2%"):
        ok = True
        for strat in WINDOWS:
            for era in ("pre_etf", "post_etf"):
                row = g[(g.strategy == strat) & (g.era == era) & (g.stop == stop)]
                n_post = g[(g.strategy == strat) & (g.era == "post_etf") & (g.stop == "none")].n
                if era == "post_etf" and (n_post.empty or int(n_post.iloc[0]) < 30):
                    continue  # informational only
                if row.empty or row.d_mean_vs_none_bp.iloc[0] < -5.0 or row.worst_shrink_pct.iloc[0] < 30.0:
                    ok = False
        if ok:
            passing.append(stop)
    verdict["stop_loss_pct"] = passing[0] if passing else None  # widest first
    post = lg[lg.era == "post_etf"]
    best = 0
    for late in (5, 10, 15):
        if all((post[(post.strategy == s) & (post.late_min == late)].loss_vs_0_bp <= 5.0).all()
               for s in WINDOWS):
            best = late
    verdict["late_entry_max_s"] = max(5, best) * 60 if best else 300
    return verdict


def main() -> int:
    RESULTS.mkdir(exist_ok=True)
    sl, late = run()
    g, lg = summarize(sl, late)
    g.to_csv(RESULTS / "sl_sweep.csv", index=False)
    lg.to_csv(RESULTS / "late_entry.csv", index=False)
    sl.to_csv(RESULTS / "sl_fires.csv", index=False)
    pd.set_option("display.width", 200)
    print("=== Test 1: stop-loss grid (net bp, 15bp RT) ===")
    print(g.round(1).to_string(index=False))
    print("\n=== Test 2: late-entry curve (net bp) ===")
    print(lg.round(1).to_string(index=False))
    v = decide(g, lg)
    print("\n=== Pre-registered decision ===")
    print(f"STOP_LOSS_PCT = {v['stop_loss_pct']}")
    print(f"LATE_ENTRY_MAX_S = {v['late_entry_max_s']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

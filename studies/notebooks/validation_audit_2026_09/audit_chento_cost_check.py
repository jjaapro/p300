"""Track V4 — reconciliation of the chento headline number.

NOT part of the pre-registered statistic set; added while running Part B after
the CSV pool and the overlay study's published mean R disagreed by a factor of
3.5. Recorded as an addition in findings.md.

What differs (both read from the same on-disk trade file):

  results_backonly/trades_*.csv `r_outcome`
      produced by chento_journal/validation_C5_smc_features.replay_one:
      TIF_BARS = 4*24 = 96 x 15m = **24 hours**, and
      cost_R = (COST_BP=18 / 10000) * (entry / risk) SUBTRACTED.

  overlay_study/run_overlays.py `replay(bars, t, None)` (the 'base' variant
  whose numbers the study publishes)
      TIF_H = 72 hours, and **no transaction cost at all**.

So "production expectancy ~+0.8R/trade" is a 72h-TIF, ZERO-COST number, while
the file it is computed from carries a 24h-TIF, cost-inclusive column. This
script reproduces the published number, then charges the source pool's own
cost model to it.

Only the BASE exit variant is replayed — one already-published configuration,
not a sweep. Read-only on prod.db.

Run:  python studies/notebooks/validation_audit_2026_09/audit_chento_cost_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
for p in (str(_HERE), str(_REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

from audit_common import ro_connect, write_json  # noqa: E402

TABLES = {"BTC": "cd_futures_15m", "ETH": "cd_futures_eth_15m"}
TIF_H = 72          # overlay_study/run_overlays.py::TIF_H
COST_BP = 18.0      # chento_journal/validation_C5_smc_features.COST_BP
OVERLAY = _REPO / "studies" / "notebooks" / "overlay_study" / "results_backonly"


def load_bars(asset: str) -> pd.DataFrame:
    con = ro_connect()
    try:
        df = pd.read_sql(
            f"SELECT timestamp, open, high, low, close FROM {TABLES[asset]} "
            "ORDER BY timestamp", con)
    finally:
        con.close()
    df.index = pd.to_datetime(df.pop("timestamp"), unit="s", utc=True)
    return df[~df.index.duplicated(keep="last")]


def replay_base(bars: pd.DataFrame, t) -> float:
    """Byte-for-byte the overlay engine's base branch (wick_pmin=None):
    stop wins a spanning bar, then target, else mark at the TIF close."""
    sign = 1 if t.direction == "long" else -1
    entry, stop, target = t.entry, t.stop, t.target
    risk = abs(t.entry - t.stop)
    w = bars[(bars.index > t.ts) & (bars.index <= t.ts + pd.Timedelta(hours=TIF_H))]
    if not len(w):
        return np.nan
    for _, b in w.iterrows():
        if (b["low"] <= stop) if sign > 0 else (b["high"] >= stop):
            return -1.0
        if (b["high"] >= target) if sign > 0 else (b["low"] <= target):
            return (target - entry) * sign / risk
    return (w.iloc[-1]["close"] - entry) * sign / risk


def tilt_skip(rs):
    return [0.0 if i > 0 and rs[i - 1] < 0 else 1.0 for i in range(len(rs))]


def tilt_half(rs):
    return [0.5 if i > 0 and rs[i - 1] < 0 else 1.0 for i in range(len(rs))]


def summarize(rs, sizes, label):
    eff = np.array([r * s for r, s in zip(rs, sizes)])
    traded = eff[np.array(sizes) > 0]
    cum = np.cumsum(eff)
    dd = float((cum - np.maximum.accumulate(cum)).min()) if len(cum) else 0.0
    return {
        "label": label,
        "n": len(rs),
        "traded_n": int((np.array(sizes) > 0).sum()),
        "mean_r": float(traded.mean()) if len(traded) else float("nan"),
        "total_r": float(eff.sum()),
        "max_dd_r": dd,
        "mar_like": float(eff.sum() / abs(dd)) if dd < 0 else float("inf"),
        "wr_pct": float((traded > 0).mean() * 100) if len(traded) else float("nan"),
    }


def main() -> int:
    print("=" * 78)
    print("chento headline reconciliation — TIF and cost")
    print("=" * 78)
    out = {}
    published = {  # results_backonly/overlay_summary.csv, base|tilt=*|H=off
        ("BTC", "none"): 0.8001439689433522,
        ("BTC", "skip_after_loss"): None,
        ("ETH", "none"): None,
    }
    for asset in ("BTC", "ETH"):
        t = pd.read_csv(OVERLAY / f"trades_{asset}.csv", parse_dates=["ts"])
        aligned = (((t.direction == "long") & (t.okx_delta_z >= 0))
                   | ((t.direction == "short") & (t.okx_delta_z <= 0)))
        t = t[aligned].reset_index(drop=True)
        bars = load_bars(asset)
        print(f"\n--- {asset}: {len(t)} OKX-aligned trades, "
              f"{len(bars):,} 15m bars ---")

        r72_nocost = np.array([replay_base(bars, row) for row in t.itertuples()])
        keep = np.isfinite(r72_nocost)
        r72_nocost = r72_nocost[keep]
        tk = t[keep].reset_index(drop=True)
        # the source pool's own cost model, applied to the 72h replay
        cost_r = (COST_BP / 10000.0) * (tk.entry.values / tk.risk.values)
        r72_cost = r72_nocost - cost_r
        r24_cost = tk.r_outcome.values     # the CSV column, TIF 24h, cost in

        print(f"  cost_R per trade: mean {cost_r.mean():.4f}R  "
              f"median {np.median(cost_r):.4f}R  max {cost_r.max():.4f}R")
        rows = []
        for tag, rs in (("TIF 72h, NO cost (= the published engine)", r72_nocost),
                        ("TIF 72h, 18bp cost charged", r72_cost),
                        ("TIF 24h, 18bp cost (the CSV r_outcome column)", r24_cost)):
            rs = list(rs)
            for pol, sz in (("none", [1.0] * len(rs)),
                            ("skip_after_loss", tilt_skip(rs)),
                            ("half_after_loss", tilt_half(rs))):
                rows.append(summarize(rs, sz, f"{tag} | tilt={pol}"))
        for r in rows:
            print(f"    {r['label']:<58} n={r['traded_n']:>3} "
                  f"meanR={r['mean_r']:+.3f} totalR={r['total_r']:+7.1f} "
                  f"maxDD={r['max_dd_r']:+6.1f} MAR={r['mar_like']:5.1f} "
                  f"WR={r['wr_pct']:.0f}%")
        out[asset] = {
            "n_okx_aligned": int(len(tk)),
            "cost_r_mean": float(cost_r.mean()),
            "cost_r_median": float(np.median(cost_r)),
            "cost_r_max": float(cost_r.max()),
            "variants": rows,
        }
        # hand the production-faithful per-trade series to audit_backtests.py
        pd.DataFrame({
            "ts": tk.ts, "date": tk.ts.dt.strftime("%Y-%m-%d"),
            "direction": tk.direction, "okx_delta_z": tk.okx_delta_z,
            "r72_nocost": r72_nocost, "cost_r": cost_r, "r72_cost": r72_cost,
            "r24_cost_csv": r24_cost,
        }).to_csv(_HERE / "results" / f"chento_r72_{asset}.csv", index=False)
        print(f"  wrote results/chento_r72_{asset}.csv")
        pub = published.get((asset, "none"))
        if pub is not None:
            got = rows[0]["mean_r"]
            print(f"  reproduction check vs overlay_summary.csv "
                  f"base|tilt=none|H=off: published {pub:.4f}  reproduced "
                  f"{got:.4f}  diff {got - pub:+.4f}")
            out[asset]["published_mean_r_base_none"] = pub
            out[asset]["reproduced_mean_r_base_none"] = got
            out[asset]["reproduction_diff"] = got - pub

    print(f"\nwrote {write_json('chento_cost_reconciliation.json', out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

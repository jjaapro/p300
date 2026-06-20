"""How does chento ACTUALLY enter relative to a dwellblock?

For each of his extracted BTC trades, detect the dwellblock structure as-of his
entry time (causal, on cd_futures_15m perp — the instrument + TF his charts use)
and measure where his real entry price (entry_first) sits relative to the
nearest zone. Answers: does he bid ABOVE the zone (shallow/momentum), AT the
zone (support bid), or BELOW it (deep dip)? And on our depth axis (0=market-ref,
1=zone POC), how deep does he bid — vs the ~20% peak the A/B/C sweep found?

Robust metrics (no reference-price assumption):
  in_zone rate         — % of entries that land inside a detected zone
  poc_dist_atr         — signed (entry-POC)/ATR; + = above POC (shallow for long)
  side classification  — above_zone / in_zone / below_zone

Bridge to the sweep (needs a 'from' price — uses the pre-entry swing extreme):
  bid_depth = (P_ref - entry)/(P_ref - POC)   [long]   0=at swing, 1=at POC

Run: python studies/notebooks/dwell_block/analyze_chento_bids.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve()
ROOT = HERE
while not (ROOT / "data" / "databases" / "prod.db").exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))
from studies.notebooks.dwell_block.dwellblock import detect_dwellblocks  # noqa: E402

DB = ROOT / "data" / "databases" / "prod.db"
TRADES = ROOT / "studies" / "material" / "chento" / "scan_aggregated" / "trades.jsonl"
LOOKBACK = 192          # 48h of 15m for the time-at-price window
EDGE_ATR = 0.10         # tolerance band for "inside" vs above/below the zone


def load_15m() -> pd.DataFrame:
    con = sqlite3.connect(str(DB))
    df = pd.read_sql("SELECT timestamp, open, high, low, close, volume "
                     "FROM cd_futures_15m ORDER BY timestamp", con)
    con.close()
    df["dt"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    return df.set_index("dt")


def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def main():
    rows = [json.loads(l) for l in TRADES.read_text(encoding="utf-8").splitlines() if l.strip()]
    trades = pd.DataFrame(rows)
    trades = trades[trades["asset"] == "BTCUSDT"].copy()
    trades["ts"] = pd.to_datetime(trades["first_ts"], utc=True, errors="coerce")
    trades = trades.dropna(subset=["ts", "entry_first"])
    trades = trades[trades["entry_first"] > 0]
    print(f"{len(trades)} BTCUSDT trades with entry+ts")

    df = load_15m()
    atr = atr_series(df)

    recs = []
    for _, t in trades.iterrows():
        ts, entry, direction = t["ts"], float(t["entry_first"]), t["direction"]
        win = df.loc[:ts]
        if len(win) < LOOKBACK:
            continue
        bars = win.iloc[-LOOKBACK:]
        a = float(atr.asof(ts)) if not pd.isna(atr.asof(ts)) else np.nan
        if not np.isfinite(a) or a <= 0:
            continue
        zones = detect_dwellblocks(bars, top_k=6)
        if not zones:
            recs.append({"ts": ts, "dir": direction, "era": ts.year, "has_zone": False})
            continue
        # nearest zone to his actual entry (containing it if any)
        inside = [z for z in zones if z.lo <= entry <= z.hi]
        zone = (min(inside, key=lambda z: abs(z.poc - entry)) if inside
                else min(zones, key=lambda z: abs(z.poc - entry)))
        poc_dist_atr = (entry - zone.poc) / a            # signed; +above POC
        if direction == "short":
            poc_dist_atr = -poc_dist_atr                 # +above-POC -> +"early" for both sides
        in_zone = zone.lo <= entry <= zone.hi
        # side relative to the zone, oriented so + = shallower/earlier than POC
        if entry > zone.hi + EDGE_ATR * a:
            side = "above_zone" if direction == "long" else "below_zone"
        elif entry < zone.lo - EDGE_ATR * a:
            side = "below_zone" if direction == "long" else "above_zone"
        else:
            side = "in_zone"
        # orient: "shallow" = entered before reaching the zone POC in trade dir
        shallow = (entry > zone.poc) if direction == "long" else (entry < zone.poc)

        # bridge depth vs pre-entry swing extreme
        if direction == "long":
            p_ref = float(bars["high"].max()); denom = p_ref - zone.poc
            depth = (p_ref - entry) / denom if denom > 0 else np.nan
        else:
            p_ref = float(bars["low"].min()); denom = zone.poc - p_ref
            depth = (entry - p_ref) / denom if denom > 0 else np.nan

        recs.append({"ts": ts, "dir": direction, "era": ts.year, "has_zone": True,
                     "in_zone": in_zone, "poc_dist_atr": poc_dist_atr,
                     "side": side, "shallow_of_poc": shallow, "bid_depth": depth})

    r = pd.DataFrame(recs)
    rz = r[r["has_zone"]]
    print(f"\ndetected a zone near entry for {len(rz)}/{len(r)} trades "
          f"({len(rz)/len(r):.0%})")
    print(f"entry landed INSIDE a dwellblock: {rz['in_zone'].mean():.0%}")
    print(f"entered SHALLOWER than POC (before the zone heart): "
          f"{rz['shallow_of_poc'].mean():.0%}")
    print("\nside vs nearest zone (oriented to trade direction):")
    print(rz["side"].value_counts().to_string())
    print(f"\npoc_dist_atr (signed, + = shallower than POC):")
    print(f"  median {rz['poc_dist_atr'].median():+.2f} ATR   "
          f"mean {rz['poc_dist_atr'].mean():+.2f}   "
          f"IQR [{rz['poc_dist_atr'].quantile(.25):+.2f}, {rz['poc_dist_atr'].quantile(.75):+.2f}]")
    d = rz["bid_depth"].dropna()
    d = d[(d > -2) & (d < 3)]
    print(f"\nbid_depth vs swing (0=swing extreme, 1=zone POC; sweep peak was ~0.2):")
    print(f"  median {d.median():.2f}   mean {d.mean():.2f}   "
          f"IQR [{d.quantile(.25):.2f}, {d.quantile(.75):.2f}]  (n={len(d)})")

    print("\nby era:")
    for era, g in rz.groupby("era"):
        dd = g["bid_depth"].dropna(); dd = dd[(dd > -2) & (dd < 3)]
        print(f"  {era}: n={len(g):>3}  in_zone={g['in_zone'].mean():.0%}  "
              f"shallow={g['shallow_of_poc'].mean():.0%}  "
              f"med_poc_dist={g['poc_dist_atr'].median():+.2f}ATR  "
              f"med_depth={dd.median():.2f}")

    out = ROOT / "studies" / "notebooks" / "dwell_block" / "chento_bid_positions.csv"
    rz.to_csv(out, index=False)
    print(f"\nwrote {out}")
    return rz


if __name__ == "__main__":
    main()

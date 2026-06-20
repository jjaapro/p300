"""Test 2 — RANGE-FORMATION hedge: when a range forms, bid the local bottom
(long limit at range_low) and offer the local high (short limit at range_high).
Profit from oscillation; if the range breaks, the leg entered at the favorable
extreme is in profit so you "still win something".

This is a STANDALONE strategy (its signal = range-formation events, not Triple
triggers), so it's evaluated on its own expectancy — the B13 conclusion was that
range-fishing belongs in a SEPARATE sleeve, and this tests a sharper version of it
(entries at the extremes rather than mid-range).

Per leg: risk = BUF_ATR×ATR beyond the range edge (the breakout stop); reward = the
range width. R is normalised to that breakout risk, so a leg that round-trips the
range is +width/buf R and a broken leg is −1R. Realistic 18bp round-trip cost is
applied per leg (tight-stop range scalps pay a lot in R-relative cost — that's the
point to surface).

Writes results to files. Run: python studies/notebooks/hedge_tests/range_hedge.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE
while not (ROOT / "data" / "databases" / "prod.db").exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))
from studies.lib.range_detector import detect_active_range  # noqa: E402

DB = ROOT / "data" / "databases" / "prod.db"
COST_RT = 0.0018
BUF_ATR = 1.0            # breakout stop = this many ATR beyond the range edge
MIN_DUR = 48             # range must have held >= 12h (48 15m bars) to qualify
MAXHOLD = 4 * 24 * 7     # 7 days max to resolve both legs
STRIDE = 12              # scan for a fresh range every 3h
TP_FRAC = 0.90           # take profit at 90% across the range (chento takes early)


def _atr(df, period=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def simulate(hi, lo, cl, pos, rl, rh, atr):
    """Dual-leg range hedge from bar `pos`. Returns (combined_R, tag, resolve_pos)."""
    buf = BUF_ATR * atr
    if buf <= 0 or rh <= rl:
        return None
    width = rh - rl
    long_tp = rl + TP_FRAC * width; long_stop = rl - buf
    short_tp = rh - TP_FRAC * width; short_stop = rh + buf
    cost_long = COST_RT * (rl / buf)
    cost_short = COST_RT * (rh / buf)

    lfill = sfill = False
    lr = sr = None
    n = len(cl)
    end = min(pos + 1 + MAXHOLD, n)
    last = pos
    for j in range(pos + 1, end):
        last = j
        bh, bl = float(hi[j]), float(lo[j])
        # long leg
        if lr is None:
            if not lfill and bl <= rl:
                lfill = True
            if lfill:
                if bl <= long_stop:
                    lr = -1.0 - cost_long
                elif bh >= long_tp:
                    lr = (long_tp - rl) / buf - cost_long
        # short leg
        if sr is None:
            if not sfill and bh >= rh:
                sfill = True
            if sfill:
                if bh >= short_stop:
                    sr = -1.0 - cost_short
                elif bl <= short_tp:
                    sr = (rh - short_tp) / buf - cost_short
        if lr is not None and sr is not None:
            break
    # mark unresolved-but-filled legs to last close; unfilled legs = 0
    cend = float(cl[last])
    if lr is None:
        lr = ((cend - rl) / buf - cost_long) if lfill else 0.0
    if sr is None:
        sr = ((rh - cend) / buf - cost_short) if sfill else 0.0
    tag = (("L+" if lr > 0 else "L-") if lfill else "L0") + (("S+" if sr > 0 else "S-") if sfill else "S0")
    return lr + sr, tag, last


def main():
    con = sqlite3.connect(str(DB))
    df = pd.read_sql("SELECT timestamp, open, high, low, close FROM cd_futures_15m "
                     "ORDER BY timestamp", con)
    con.close()
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = df.set_index("ts").drop(columns="timestamp")
    atr = _atr(df)
    hi = df["high"].to_numpy(); lo = df["low"].to_numpy(); cl = df["close"].to_numpy()
    atr_arr = atr.to_numpy(); idxs = df.index
    print(f"Loaded {len(df):,} 15m bars; scanning for range-formation events...")

    rows = []
    cooldown_pos = 0
    for pos in range(200, len(df) - 10, STRIDE):
        if pos < cooldown_pos:
            continue
        ts = idxs[pos]
        r = detect_active_range(df, ts)
        if not r or r["duration_bars"] < MIN_DUR:
            continue
        a = float(atr_arr[pos])
        if not np.isfinite(a) or a <= 0:
            continue
        res = simulate(hi, lo, cl, pos, r["range_low"], r["range_high"], a)
        if res is None:
            continue
        comb, tag, resolve_pos = res
        rows.append({"ts": ts, "comb_R": comb, "tag": tag,
                     "range_pct": r["range_pct"], "dur_bars": r["duration_bars"]})
        cooldown_pos = resolve_pos + STRIDE   # don't re-open until this hedge resolves

    rep = pd.DataFrame(rows)
    r = rep["comb_R"].to_numpy()
    cum = np.cumsum(r); dd = cum - np.maximum.accumulate(cum); mdd = float(dd.min()) if len(r) else 0
    out = {"n_events": int(len(rep)), "meanR": round(float(r.mean()), 3) if len(r) else 0,
           "WR": round(float((r > 0).mean()), 3) if len(r) else 0,
           "cumR": round(float(cum[-1]), 1) if len(r) else 0,
           "maxDD_R": round(mdd, 1), "MAR": round(float(cum[-1] / abs(mdd)), 2) if mdd < 0 else None,
           "tag_counts": rep["tag"].value_counts().to_dict() if len(rep) else {},
           "params": {"BUF_ATR": BUF_ATR, "MIN_DUR": MIN_DUR, "TP_FRAC": TP_FRAC,
                      "max_range_pct": 0.06}}
    (HERE / "range_hedge_results.json").write_text(json.dumps(out, indent=2, default=str))
    txt = (f"range-formation hedge (standalone)\n"
           f"  n_events={out['n_events']}  meanR={out['meanR']:+.3f}  WR={out['WR']:.0%}  "
           f"cumR={out['cumR']:+.1f}  maxDD={out['maxDD_R']:+.1f}R  MAR={out['MAR']}\n"
           f"  outcome mix: {out['tag_counts']}")
    (HERE / "range_hedge_results.txt").write_text(txt)
    print(txt)

    if len(rep):
        try:
            import matplotlib; matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(9, 5))
            ax.plot(pd.to_datetime(rep.sort_values("ts")["ts"]), np.cumsum(
                rep.sort_values("ts")["comb_R"]), lw=1.8, color="#1f77b4")
            ax.set_ylabel("cumulative R"); ax.axhline(0, color="#999", lw=0.6)
            ax.set_title(f"Range-formation hedge (standalone, n={out['n_events']}, "
                         f"meanR {out['meanR']:+.2f}, MAR {out['MAR']})", fontsize=10)
            ax.grid(alpha=0.15)
            fig.tight_layout(); fig.savefig(HERE / "range_hedge_equity.png", dpi=120)
        except Exception as e:  # noqa: BLE001
            print("plot skipped:", e)


if __name__ == "__main__":
    main()

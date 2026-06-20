"""(2a) Prod-faithful comparison: does the shallow dwellblock-pullback entry
improve the SHIPPED chento_triple_v3 sleeve's drawdown?

Reproduces the production backtest exactly — `replay_with_mae(enable_ladder=False)`
(entry = trigger-bar close, risk = 5*ATR, target 6R, 72h TIF) + `apply_filters`
(no-tilt + resist-OB>2R + OKX-aligned) on the B1n B5n B7 trigger set — then swaps
ONLY the entry for a shallow ~20% limit pullback toward the nearest dwellblock
(with a 24h fill window). Everything else identical, so the delta is the entry.

Arms:
  prod_market       current sleeve: market fill at the trigger bar close
  shallow_fallback  shallow limit where a zone exists+fills in 24h, else market
                    (SAME trade set as prod -> clean apples-to-apples max-DD)
  shallow_skip      shallow limit, skip the signal if it never fills (fewer trades)

Writes prod_compare_results.json/.txt + prod_compare_equity.png (stdout-independent).
Run: python studies/notebooks/dwell_block/prod_compare.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE
while not (ROOT / "data" / "databases" / "prod.db").exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))
from studies.notebooks.dwell_block.dwellblock import detect_dwellblocks  # noqa: E402
from studies.notebooks.chento_journal.validation_liquidation_and_C6 import (  # noqa: E402
    build_optimized, replay_with_mae, apply_filters, COST_BP)

ATR_MULT = 5.0; TARGET_R = 6.0; TIF_BARS = 4 * 72       # prod exit model
LOOKBACK = 192; DEPTH = 0.20                            # dwellblock detect + pullback depth
FILL_TIF_BARS = 96                                      # 24h of 15m to fill the limit
MAX_DIST_ATR = 6.0; MIN_DIST_ATR = 0.20


def replay_shifted(trig, df_smc, df_atr, *, fallback_market: bool):
    """Prod replay (no ladder) but entry = shallow limit toward nearest zone."""
    direction = trig["direction"]; ts = trig["ts"]
    idx = df_smc.index.searchsorted(ts, side="right") - 1
    if idx < 0 or idx >= len(df_smc):
        return None
    atr = float(df_atr["atr"].iloc[idx])
    if pd.isna(atr) or atr <= 0:
        return None
    P = float(df_smc["close"].iloc[idx])
    risk = atr * ATR_MULT
    if risk <= 0:
        return None
    sgn = 1 if direction == "long" else -1

    bars = df_smc.iloc[max(0, idx - LOOKBACK):idx + 1][["high", "low"]].copy()
    bars["volume"] = 0.0
    zones = detect_dwellblocks(bars, top_k=6)
    cands = [z for z in zones if (z.poc < P if direction == "long" else z.poc > P)]
    zone = (max(cands, key=lambda z: z.poc) if direction == "long" and cands else
            min(cands, key=lambda z: z.poc) if cands else None)
    dist = abs(P - zone.poc) if zone else np.inf
    usable = zone is not None and MIN_DIST_ATR * atr <= dist <= MAX_DIST_ATR * atr
    shallow = P - sgn * DEPTH * dist if usable else P

    high = df_smc["high"].to_numpy(); low = df_smc["low"].to_numpy()
    closes = df_smc["close"].to_numpy(); nbar = len(closes)

    if usable:
        fill_idx = None
        for j in range(idx + 1, min(idx + 1 + FILL_TIF_BARS, nbar)):
            if (low[j] <= shallow) if direction == "long" else (high[j] >= shallow):
                fill_idx = j; break
        if fill_idx is None:
            if not fallback_market:
                return {"missed": True, "ts": ts, "direction": direction}
            entry, start = P, idx + 1
        else:
            entry, start = shallow, fill_idx + 1
    else:
        entry, start = P, idx + 1

    stop = entry - risk if direction == "long" else entry + risk
    target = entry + risk * TARGET_R if direction == "long" else entry - risk * TARGET_R
    cost_R = (COST_BP / 10000.0) * (entry / risk)
    end = min(start + TIF_BARS, nbar)
    outcome, kind, last_close = None, None, entry
    for j in range(start, end):
        bh, bl = high[j], low[j]; last_close = closes[j]
        if direction == "long":
            if bl <= stop:
                outcome, kind = (stop - entry) / risk - cost_R, "stop"; break
            if bh >= target:
                outcome, kind = (target - entry) / risk - cost_R, "target"; break
        else:
            if bh >= stop:
                outcome, kind = (entry - stop) / risk - cost_R, "stop"; break
            if bl <= target:
                outcome, kind = (entry - target) / risk - cost_R, "target"; break
    if outcome is None:
        outcome = ((last_close - entry) if direction == "long" else (entry - last_close)) / risk - cost_R
        kind = "tif"
    return {"ts": ts, "direction": direction, "entry": entry, "risk": risk,
            "r_outcome": outcome, "exit_kind": kind, "filled": True}


def stats(rep, label):
    t = rep.sort_values("ts"); r = t["r_outcome"].to_numpy()
    cum = np.cumsum(r); peak = np.maximum.accumulate(cum); dd = cum - peak
    maxdd = float(dd.min())
    return {"label": label, "n": int(len(r)), "meanR": round(float(r.mean()), 3),
            "WR": round(float((r > 0).mean()), 3), "cumR": round(float(cum[-1]), 1),
            "maxDD_R": round(maxdd, 1),
            "MAR": round(float(cum[-1] / abs(maxdd)), 2) if maxdd < 0 else None}, (t["ts"].to_numpy(), cum)


def main():
    print("Building prod triggers + data (heavy)...")
    triple_w, df_smc, df_atr, fvgs, obs, delta_df = build_optimized()
    print(f"  {len(triple_w)} triple triggers")

    def run(builder, label, fallback=None):
        rows = []
        for _, t in triple_w.iterrows():
            r = (replay_with_mae(t, df_smc, df_atr, fvgs, obs, enable_ladder=False)
                 if builder == "market" else
                 replay_shifted(t, df_smc, df_atr, fallback_market=fallback))
            if r is not None and r.get("r_outcome") is not None:
                rows.append(r)
        rep = pd.DataFrame(rows)
        rep = apply_filters(rep, delta_df, df_smc, fvgs, obs)
        return stats(rep, label)

    results = [
        run("market", "prod_market"),
        run("shifted", "shallow_fallback", fallback=True),
        run("shifted", "shallow_skip", fallback=False),
    ]
    table = [s for s, _ in results]
    out = {"params": {"atr_mult": ATR_MULT, "target_r": TARGET_R, "depth": DEPTH,
                       "fill_tif_h": FILL_TIF_BARS / 4}, "arms": table}
    (HERE / "prod_compare_results.json").write_text(json.dumps(out, indent=2))
    lines = [f'{"arm":<18}{"n":>5}{"meanR":>8}{"WR":>6}{"cumR":>9}{"maxDD_R":>9}{"MAR":>7}']
    for s in table:
        lines.append(f'{s["label"]:<18}{s["n"]:>5}{s["meanR"]:>+8.3f}{s["WR"]:>6.0%}'
                     f'{s["cumR"]:>+9.1f}{s["maxDD_R"]:>+9.1f}{(s["MAR"] or 0):>7.2f}')
    txt = "\n".join(lines)
    (HERE / "prod_compare_results.txt").write_text(txt)
    print(txt)

    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9, 5))
        cols = {"prod_market": "#888", "shallow_fallback": "#2ca02c", "shallow_skip": "#1f77b4"}
        for s, (ts, cum) in results:
            ax.plot(pd.to_datetime(ts), cum, color=cols[s["label"]], lw=1.8,
                    label=f'{s["label"]}  cumR {s["cumR"]:+.0f}, maxDD {s["maxDD_R"]:+.0f}, MAR {s["MAR"]}')
        ax.set_ylabel("cumulative R"); ax.axhline(0, color="#999", lw=0.6)
        ax.set_title("Prod sleeve exit model: market vs shallow dwellblock entry", fontsize=11)
        ax.legend(fontsize=8); ax.grid(alpha=0.15)
        fig.tight_layout(); fig.savefig(HERE / "prod_compare_equity.png", dpi=120)
    except Exception as e:  # noqa: BLE001
        print("plot skipped:", e)


if __name__ == "__main__":
    main()

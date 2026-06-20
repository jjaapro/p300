"""Test 1 — REACTIVE hedge: enter single-direction, and if price goes the wrong
side of entry, open an opposite (hedge) leg instead of taking the stop; unwind
the hedge when price reverses, then ride the original leg.

Tested against the SHIPPED chento_triple_v3 backtest (prod-faithful: build_optimized
triggers + replay_with_mae(enable_ladder=False), 5×ATR stop / 6R target / 72h, +
apply_filters). Baseline = current single-direction sleeve.

Model (long; short mirrors):
  - hedge fires when adverse excursion reaches `hedge_at_R` (must be < 1R, i.e. inside
    the 5×ATR stop) -> open full short at that price. P&L is now FROZEN at -hedge_at_R
    until we unwind (a static hedge == a stop at that level).
  - unwind the short when price bounces `unwind_R` off its post-hedge extreme -> bank the
    short P&L, re-expose the long (original stop/target re-armed). The long is now deep
    underwater and only wins on a full recovery to the 6R target.
  - if never unwound by TIF: close both, loss capped at -hedge_at_R (the "insurance" payoff).

Sweeps hedge_at_R × unwind_R. Writes results to files (stdout capture is flaky here).
Run: python studies/notebooks/hedge_tests/reactive_hedge.py
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
from studies.notebooks.chento_journal.validation_liquidation_and_C6 import (  # noqa: E402
    build_optimized, replay_with_mae, apply_filters, COST_BP)

ATR_MULT = 5.0; TARGET_R = 6.0; TIF_BARS = 4 * 72
HEDGE_AT = [0.3, 0.5, 0.7]      # adverse-R at which the hedge fires (< 1R = inside the stop)
UNWIND_R = [0.3, 0.5, 1.0]      # bounce off the post-hedge extreme that closes the hedge


def replay_reactive(ts, direction, hi, lo, cl, index, atr_arr, *, hedge_at_R, unwind_R):
    idx = index.searchsorted(ts, side="right") - 1
    n = len(cl)
    if idx < 0 or idx >= n:
        return None
    atr = float(atr_arr[idx])
    if not np.isfinite(atr) or atr <= 0:
        return None
    entry = float(cl[idx]); risk = atr * ATR_MULT
    if risk <= 0:
        return None
    long = direction == "long"
    stop = entry - risk if long else entry + risk
    target = entry + risk * TARGET_R if long else entry - risk * TARGET_R
    htp = entry - hedge_at_R * risk if long else entry + hedge_at_R * risk
    cost_R = (COST_BP / 10000.0) * (entry / risk)
    start, end = idx + 1, min(idx + 1 + TIF_BARS, n)

    hedged = unwound = False
    hp = ext = None
    short_r = 0.0
    outcome = kind = None
    last = entry
    for j in range(start, end):
        bh, bl, last = float(hi[j]), float(lo[j]), float(cl[j])
        if not hedged:
            if long:
                if bh >= target:
                    outcome, kind = (target - entry) / risk - cost_R, "target"; break
                if bl <= htp:
                    hedged, hp, ext = True, htp, bl
                elif bl <= stop:
                    outcome, kind = (stop - entry) / risk - cost_R, "stop"; break
            else:
                if bl <= target:
                    outcome, kind = (entry - target) / risk - cost_R, "target"; break
                if bh >= htp:
                    hedged, hp, ext = True, htp, bh
                elif bh >= stop:
                    outcome, kind = (entry - stop) / risk - cost_R, "stop"; break
        elif not unwound:
            if long:
                ext = min(ext, bl); up = ext + unwind_R * risk
                if bh >= up:
                    short_r, unwound = (hp - up) / risk - cost_R, True
            else:
                ext = max(ext, bh); up = ext - unwind_R * risk
                if bl <= up:
                    short_r, unwound = (up - hp) / risk - cost_R, True
        else:
            if long:
                if bl <= stop:
                    outcome, kind = (stop - entry) / risk - cost_R, "stop2"; break
                if bh >= target:
                    outcome, kind = (target - entry) / risk - cost_R, "target"; break
            else:
                if bh >= stop:
                    outcome, kind = (entry - stop) / risk - cost_R, "stop2"; break
                if bl <= target:
                    outcome, kind = (entry - target) / risk - cost_R, "target"; break

    if outcome is None:
        if hedged and not unwound:
            outcome = ((hp - entry) if long else (entry - hp)) / risk - cost_R
            kind = "hedged_tif"
        else:
            outcome = ((last - entry) if long else (entry - last)) / risk - cost_R
            kind = "tif"
    return {"ts": ts, "direction": direction, "entry": entry, "risk": risk,
            "r_outcome": outcome + short_r, "exit_kind": kind,
            "hedged": hedged, "unwound": unwound}


def stats(rep, label):
    t = rep.sort_values("ts"); r = t["r_outcome"].to_numpy()
    cum = np.cumsum(r); dd = cum - np.maximum.accumulate(cum); mdd = float(dd.min())
    return {"label": label, "n": int(len(r)), "meanR": round(float(r.mean()), 3),
            "WR": round(float((r > 0).mean()), 3), "cumR": round(float(cum[-1]), 1),
            "maxDD_R": round(mdd, 1),
            "MAR": round(float(cum[-1] / abs(mdd)), 2) if mdd < 0 else None}, (t["ts"].to_numpy(), cum)


def main():
    print("Building prod triggers + data (heavy)...")
    triple_w, df_smc, df_atr, fvgs, obs, delta_df = build_optimized()
    print(f"  {len(triple_w)} triggers")
    hi = df_smc["high"].to_numpy(); lo = df_smc["low"].to_numpy(); cl = df_smc["close"].to_numpy()
    index = df_smc.index; atr_arr = df_atr["atr"].to_numpy()

    base = pd.DataFrame([r for r in (replay_with_mae(t, df_smc, df_atr, fvgs, obs, enable_ladder=False)
                                     for _, t in triple_w.iterrows()) if r])
    results = [stats(apply_filters(base, delta_df, df_smc, fvgs, obs), "baseline_single")]

    for X in HEDGE_AT:
        for Y in UNWIND_R:
            rows = [replay_reactive(t["ts"], t["direction"], hi, lo, cl, index, atr_arr,
                                    hedge_at_R=X, unwind_R=Y) for _, t in triple_w.iterrows()]
            rep = pd.DataFrame([r for r in rows if r])
            results.append(stats(apply_filters(rep, delta_df, df_smc, fvgs, obs),
                                 f"hedge@{X}_unwind{Y}"))

    table = [s for s, _ in results]
    (HERE / "reactive_hedge_results.json").write_text(json.dumps(table, indent=2))
    lines = [f'{"variant":<22}{"n":>5}{"meanR":>8}{"WR":>6}{"cumR":>9}{"maxDD_R":>9}{"MAR":>7}']
    for s in table:
        lines.append(f'{s["label"]:<22}{s["n"]:>5}{s["meanR"]:>+8.3f}{s["WR"]:>6.0%}'
                     f'{s["cumR"]:>+9.1f}{s["maxDD_R"]:>+9.1f}{(s["MAR"] or 0):>7.2f}')
    (HERE / "reactive_hedge_results.txt").write_text("\n".join(lines))
    print("\n".join(lines))

    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        best = max((s for s in table[1:]), key=lambda s: s["MAR"] or -9)
        fig, ax = plt.subplots(figsize=(9, 5))
        for s, (ts, cum) in results:
            if s["label"] in ("baseline_single", best["label"]):
                ax.plot(pd.to_datetime(ts), cum, lw=1.8,
                        label=f'{s["label"]}  cumR {s["cumR"]:+.0f} maxDD {s["maxDD_R"]:+.0f} MAR {s["MAR"]}')
        ax.set_ylabel("cumulative R"); ax.axhline(0, color="#999", lw=0.6)
        ax.set_title("Reactive hedge vs single-direction (prod exit model)\nbaseline vs best-MAR hedge variant", fontsize=10)
        ax.legend(fontsize=9); ax.grid(alpha=0.15)
        fig.tight_layout(); fig.savefig(HERE / "reactive_hedge_equity.png", dpi=120)
    except Exception as e:  # noqa: BLE001
        print("plot skipped:", e)


if __name__ == "__main__":
    main()

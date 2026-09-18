"""Post-report exploratory control for the squeeze_bull arm (not pre-registered; labelled as such).

Question: how much of the fires' forward move after entry is simply the bull regime's drift? For every hourly bar in
the sleeve's own causal bull regime (backward-only 30-day return > +10%), the forward return at the same horizons as the
report, from the bar's close, on the snapshot's hourly closes. Mean over all such hours in the fires' span and a 30-day
block interval on the day axis.

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\sqb_explore.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sqb_lib as S  # noqa: E402

HORIZONS_H = (24, 48, 72, 96, 168)


def main() -> None:
    if not (S.RESULTS / "report.json").exists():
        raise SystemExit("report.json missing: exploratory control only after the report")
    m = S.sleeve_math()
    with np.load(S.HOURLY) as z:
        ts, close = z["ts"].astype(np.int64), z["close"].astype(float)
    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date() for t in ts]
    daily = {}
    for d, c in zip(days, close):
        daily[d] = c                                  # last hourly close of each UTC day
    fires = S.load_fires()
    lo, hi = int(fires["bar_ts"].min()), int(fires["bar_ts"].max())
    pos = {int(t): i for i, t in enumerate(ts)}
    rows = []
    for i, (t, d) in enumerate(zip(ts, days)):
        if not (lo <= t <= hi) or m.classify_regime(m.backward_only_ret_30d(daily, d)) != "bull_30d":
            continue
        rec = {"day": d.isoformat()}
        for h in HORIZONS_H:
            j = pos.get(int(t) + h * 3600)
            rec[h] = (close[j] / close[i] - 1) * 100 if j is not None else np.nan
        rows.append(rec)
    df = pd.DataFrame(rows)
    axis = S.day_axis(fires)
    idx = S.block_indices(len(axis))
    out = {"bull_hours": int(len(df)), "note": "exploratory, not pre-registered", "by_horizon_h": {}}
    for h in HORIZONS_H:
        sub = df.dropna(subset=[h])
        sub = sub[sub["day"].isin(set(axis))]
        r = S.paired(sub["day"].reset_index(drop=True), sub[h].to_numpy(), axis, idx)
        out["by_horizon_h"][str(h)] = {"mean_pct": r["mean_d"], "ci95": r["ci95"], "hours": int(len(sub))}
    rep = __import__("json").loads((S.RESULTS / "report.json").read_text())
    for h in HORIZONS_H:
        out["by_horizon_h"][str(h)]["fires_mean_pct"] = rep["forward_moves_pct"][str(h)]["mean_pct"]
    S.write_json(S.RESULTS / "exploratory_regime_drift.json", out)
    for h, v in out["by_horizon_h"].items():
        print(f"{h:>4} h  bull-regime hours {v['mean_pct']:+.2f}% [{v['ci95'][0]:+.2f}, {v['ci95'][1]:+.2f}]  fires {v['fires_mean_pct']:+.2f}%")


if __name__ == "__main__":
    main()

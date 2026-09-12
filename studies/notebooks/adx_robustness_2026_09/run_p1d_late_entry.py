"""P1(d) — what a late entry (h hours after the day boundary) does to the same trades."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import adx_lib as al

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")

ex = al.ex
DELAYS_H = (0, 1, 2, 6, 12, 23)


def main() -> None:
    path = ex.load_path("btc_1m")
    candles = al.daily_candles(0)
    out = {"env": ex.env_info(), "by_delay": {}}
    base = al.live_walk(candles, path)
    base_net = float(np.mean([t["net_pct"] for t in base]))
    # (i) same trades, shifted entry price, same exits
    rows = []
    for t in base:
        d = 1 if t["dir"] == "long" else -1
        boundary = t["entry_ts"] - 60
        for h in DELAYS_H:
            tt = boundary + h * 3600
            j = path.idx_ge(tt)
            if j >= len(path.ts) or path.ts[j] != tt:
                continue
            if tt >= t["exit_ts"]:
                rows.append(dict(delay_h=h, entry_ts=t["entry_ts"], missed=True, net_pct=np.nan)); continue
            px = float(path.c[j])
            gross = d * (t["exit_px"] / px - 1) * 100
            rows.append(dict(delay_h=h, entry_ts=t["entry_ts"], missed=False, net_pct=gross - al.COST_BP_RT / 100 - (-t["funding_pct"])))
    df = pd.DataFrame(rows)
    for h in DELAYS_H:
        s = df[df.delay_h == h]
        out["by_delay"][f"h{h}"] = dict(n=int(len(s)), missed=int(s.missed.sum()), mean_net_pct=float(s.net_pct.mean()),
                                        delta_vs_h0=float(s.net_pct.mean() - df[df.delay_h == 0].net_pct.mean()))
    # (ii) the full machine re-run with the entry tick delayed (stops and trail then anchor on the late fill)
    out["machine_with_delay"] = {}
    for h in DELAYS_H:
        tr = al.live_walk(candles, path, entry_delay_s=h * 3600)
        st = al.curve_stats(al.mtm_returns(tr, path)); ls = al.ledger_stats(tr)
        out["machine_with_delay"][f"h{h}"] = dict(n=ls["n"], mean_net_pct=ls["mean_net_pct"], **st)
    tab = pd.DataFrame(out["by_delay"]).T
    print("same trades, late entry price:\n", tab.round(3).to_string())
    print("\nmachine re-run with delayed entry tick:\n", pd.DataFrame(out["machine_with_delay"]).T.round(3).to_string())
    d6 = out["by_delay"]["h6"]["delta_vs_h0"]
    out["decision"] = dict(delta_6h_pp=d6, within_half_pp=bool(abs(d6) <= 0.5),
                           verdict="current behaviour fine; document the assumption" if abs(d6) <= 0.5 else "propose a grace window (hours)")
    print("decision:", out["decision"])
    al.jdump(out, "p1d_late_entry.json")


if __name__ == "__main__":
    main()

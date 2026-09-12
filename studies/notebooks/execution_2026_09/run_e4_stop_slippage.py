"""E4 — stop-market slippage under stop_path semantics (gap-through fills at the open) (README §E4)."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import exec_lib as ex

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")


def year_range_threshold(path: ex.PricePath) -> dict[int, float]:
    yrs = pd.to_datetime(path.ts, unit="s").year
    rng = (path.h - path.l) / path.c * 1e4
    return {int(y): float(np.quantile(rng[yrs == y], 0.95)) for y in np.unique(yrs)}


def run(sleeve: str, res: str) -> dict:
    events = ex.load_events(sleeve)
    path = ex.path_for(events[0].asset, res)
    thr = year_range_threshold(path) if res == "1m" else None
    rows = []
    for ev in events:
        cx = ex.context(ev, path)
        if cx is None:
            continue
        mt = ex.market_trade(ev, path, cx)
        if mt["kind"] != "stop":
            continue
        j, d, stop = mt["idx"], ev.direction, mt["stop"]
        fill_path = mt["price_pathsem"]                     # min/max(open, stop): gap-through at the open
        poll = float(path.c[j])                             # a polling bot closes at the price it sees next
        rows.append(dict(ts=ev.signal_ts, stop=stop, fill_pathsem=fill_path, poll=poll,
                         slip_pathsem_bp=d * (stop - fill_path) / stop * 1e4, slip_poll_bp=d * (stop - poll) / stop * 1e4,
                         gap_through=bool(d * (path.o[j] - stop) < 0), range_bp=float((path.h[j] - path.l[j]) / path.c[j] * 1e4),
                         stressed=bool(thr and (path.h[j] - path.l[j]) / path.c[j] * 1e4 >= thr[pd.Timestamp(int(path.ts[j]), unit="s").year]),
                         stop_dist_bp=float(mt["risk"] / mt["fill"] * 1e4), r=mt["r"]))
    if not rows:
        return dict(n_stops=0)
    df = pd.DataFrame(rows)
    g = ex.day_groups(df.ts.to_numpy())
    a, b = ex.block_boot_mean(df.slip_pathsem_bp.to_numpy(), g), ex.block_boot_mean(df.slip_poll_bp.to_numpy(), g)
    out = dict(n_stops=int(len(df)), share_gap_through=float(df.gap_through.mean()),
               slip_pathsem_bp=dict(mean=a["mean"], ci90=a["ci90"], median=float(df.slip_pathsem_bp.median()), p90=float(df.slip_pathsem_bp.quantile(0.9)),
                                    max=float(df.slip_pathsem_bp.max())),
               slip_poll_bp=dict(mean=b["mean"], ci90=b["ci90"], median=float(df.slip_poll_bp.median()), p90=float(df.slip_poll_bp.quantile(0.9))),
               median_stop_dist_bp=float(df.stop_dist_bp.median()),
               slip_pathsem_in_R=float((df.slip_pathsem_bp / df.stop_dist_bp).mean()),
               slip_poll_in_R=float((df.slip_poll_bp / df.stop_dist_bp).mean()))
    if res == "1m":
        out["stressed"] = dict(n=int(df.stressed.sum()), mean_slip_pathsem_bp=float(df.loc[df.stressed, "slip_pathsem_bp"].mean()) if df.stressed.any() else float("nan"),
                               calm_mean_slip_pathsem_bp=float(df.loc[~df.stressed, "slip_pathsem_bp"].mean()))
    return out


def adx_sl() -> dict:
    p1 = ex.load_path("btc_1m")
    rows = []
    for a in ex.load_adx():
        if a["reason"] != "SL":
            continue
        i0, i1 = p1.idx_ge(a["signal_ts"]), p1.idx_gt(a["exit_ts"] + 86400)
        if i0 >= i1:
            continue
        d, sl = a["direction"], a["sl_price"]
        w = ex.walk(p1, i0, i1, d, sl, float("inf") if d > 0 else 0.0)
        if w["kind"] != "stop":
            rows.append(dict(ts=a["signal_ts"], found=False)); continue
        j = w["idx"]
        rows.append(dict(ts=a["signal_ts"], found=True, hit=ex.iso(p1.ts[j]), ledger_exit=ex.iso(a["exit_ts"] - 86400),
                         slip_pathsem_bp=d * (sl - w["price_pathsem"]) / sl * 1e4, slip_poll_bp=d * (sl - p1.c[j]) / sl * 1e4,
                         gap_through=bool(d * (p1.o[j] - sl) < 0)))
    df = pd.DataFrame(rows)
    f = df[df.found == True]  # noqa: E712
    return dict(n_sl=int(len(df)), n_located=int(len(f)), mean_slip_pathsem_bp=float(f.slip_pathsem_bp.mean()),
                max_slip_pathsem_bp=float(f.slip_pathsem_bp.max()), mean_slip_poll_bp=float(f.slip_poll_bp.mean()),
                share_gap_through=float(f.gap_through.mean()), rows=f.to_dict("records"))


def main() -> None:
    out = {"env": ex.env_info(), "sleeves": {}, "adx": adx_sl()}
    for sl in ex.WALK_SLEEVES:
        out["sleeves"][sl] = {"1m": run(sl, "1m")}
        if ex.load_events(sl)[0].asset == "BTC":
            out["sleeves"][sl]["5s"] = run(sl, "5s")
        for res, d in out["sleeves"][sl].items():
            print(sl, res, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items() if not isinstance(v, dict)},
                  "pathsem:", {k: (round(v, 2) if isinstance(v, float) else [round(x, 2) for x in v]) for k, v in d.get("slip_pathsem_bp", {}).items()},
                  "poll:", {k: (round(v, 2) if isinstance(v, float) else [round(x, 2) for x in v]) for k, v in d.get("slip_poll_bp", {}).items()},
                  "stressed:", d.get("stressed"))
    print("ADX SL:", {k: v for k, v in out["adx"].items() if k != "rows"})
    ex.jdump(out, "e4_stop_slippage.json")


if __name__ == "__main__":
    main()

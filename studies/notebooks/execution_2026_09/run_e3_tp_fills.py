"""E3 — take-profit fills: touch vs trade-through, and the cost of a market-at-touch exit (README §E3)."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import exec_lib as ex

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")

TICK = ex.TICK["spot"]


def run(sleeve: str, res: str) -> dict:
    events = ex.load_events(sleeve)
    path = ex.path_for(events[0].asset, res)
    rows = []
    for ev in events:
        cx = ex.context(ev, path)
        if cx is None:
            continue
        mt = ex.market_trade(ev, path, cx)
        if mt["kind"] != "target":
            continue
        j, d, tgt = mt["idx"], ev.direction, mt["target"]
        ext = float(path.h[j]) if d > 0 else float(path.l[j])
        through_tick = d * (ext - tgt) >= TICK
        through_1bp = d * (ext - tgt) >= tgt * 1e-4
        # the polling bot exits at the price it sees on its next tick: the touch bar's close (60 s tick)
        poll = float(path.c[j])
        poll_cost_bp = d * (tgt - poll) / tgt * 1e4
        # a resting TP that did NOT fill (touch only): the trade continues with the same levels
        alt = ex.walk(path, j + 1, cx["i1"], d, mt["stop"], tgt) if j + 1 < cx["i1"] else dict(kind="none", price=np.nan)
        r_alt = ex.trade_r(d, mt["fill"], alt["price"], mt["risk"]) if alt["kind"] != "none" else np.nan
        rows.append(dict(ts=ev.signal_ts, through_tick=through_tick, through_1bp=through_1bp, poll_cost_bp=poll_cost_bp,
                         r_target=mt["r"], r_if_unfilled=r_alt, alt_kind=alt["kind"],
                         range_bp=float((path.h[j] - path.l[j]) / path.c[j] * 1e4)))
    if not rows:
        return dict(n_target_hits=0)
    df = pd.DataFrame(rows)
    touch_only = ~df.through_1bp
    loss = (df.r_target - df.r_if_unfilled)[touch_only & df.r_if_unfilled.notna()]
    g = ex.day_groups(df.ts.to_numpy())
    pc = ex.block_boot_mean(df.poll_cost_bp.to_numpy(), g)
    out = dict(n_target_hits=int(len(df)), share_through_tick=float(df.through_tick.mean()),
               share_through_1bp=float(df.through_1bp.mean()),
               expected_loss_r_per_target_if_resting=float(loss.mean() * touch_only.mean()) if len(loss) else 0.0,
               mean_loss_r_touch_only=float(loss.mean()) if len(loss) else float("nan"),
               poll_exit_cost_bp=dict(mean=pc["mean"], ci90=pc["ci90"], median=float(df.poll_cost_bp.median())),
               unfilled_alt_kinds=df.loc[touch_only, "alt_kind"].value_counts().to_dict())
    return out


def main() -> None:
    out = {"env": ex.env_info(), "tick_usdt": TICK, "sleeves": {}, "decision": {}}
    for sl in ex.WALK_SLEEVES:
        out["sleeves"][sl] = {"1m": run(sl, "1m")}
        if ex.load_events(sl)[0].asset == "BTC":
            out["sleeves"][sl]["5s"] = run(sl, "5s")
        r1 = out["sleeves"][sl]["1m"]
        out["decision"][sl] = dict(resting_tp=bool(r1.get("share_through_tick", 0) >= 0.90),
                                   share_through_tick_1m=r1.get("share_through_tick"))
        print(sl, {res: {k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items() if k != "unfilled_alt_kinds"}
                   for res, d in out["sleeves"][sl].items()})
        print("   decision:", out["decision"][sl])
    ex.jdump(out, "e3_tp_fills.json")


if __name__ == "__main__":
    main()

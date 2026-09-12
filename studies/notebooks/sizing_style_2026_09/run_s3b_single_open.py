"""S3b — POST-HOC sensitivity (not pre-registered): SHORT_SQUEEZE with the bot's single-open semantics.

The research pool lets a second trigger open while a trade is still held (4 h cooldown vs 6 h TIF), which the
S3 walk counts at 6x gross; the live bot holds one position per variant. This re-runs S1/S3 for SHORT_SQUEEZE
after dropping any trade that starts while the previous one is still open. Reported as a sensitivity only."""
from __future__ import annotations

import sys

import pandas as pd

import sizing_lib as sl

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")

ex = sl.ex


def single_open(t: pd.DataFrame, path: ex.PricePath) -> pd.DataFrame:
    t = t.sort_values("ts")
    keep, last_exit = [], -1
    for r in t.itertuples():
        if r.i_fill <= last_exit:
            continue
        keep.append(r.Index); last_exit = r.i_exit
    return t.loc[keep]


def main() -> None:
    df = pd.read_csv(sl.RESULTS / "s1_trades.csv")
    path = ex.load_path("btc_1m")
    out = {"env": ex.env_info(), "note": "post-hoc sensitivity, not pre-registered", "SHORT_SQUEEZE": {}}
    for policy in sl.POLICIES:
        t = single_open(df[(df.sleeve == "SHORT_SQUEEZE") & (df.policy == policy)], path)
        trades = t.to_dict("records")
        summ = sl.policy_summary(trades, path)
        walk = sl.liquidation_walk({"BTC": trades}, sl.CAPITAL)
        out["SHORT_SQUEEZE"][policy] = dict(n=len(trades), dropped=int((df[(df.sleeve == "SHORT_SQUEEZE") & (df.policy == policy)]).shape[0] - len(trades)),
                                            mean_r=summ["mean_r"], first_half_r=summ["first_half_r"], second_half_r=summ["second_half_r"],
                                            mar=summ["mar"], maxdd_pct=summ["maxdd_pct"], pct_per_year=summ["pct_per_year"],
                                            episodes=walk["episodes"], min_distance=walk["min_distance"], worst_mae=walk["worst_mae"],
                                            max_gross_x=walk["max_gross_x"], safety_2x=bool(walk["min_distance"] >= 2 * walk["worst_mae"]))
        print(policy, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in out["SHORT_SQUEEZE"][policy].items()})
    sl.jdump(out, "s3b_single_open.json")


if __name__ == "__main__":
    main()

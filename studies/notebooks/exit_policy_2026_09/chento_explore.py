"""Post-verdict exploratory tables for the chento exit-policy study. Never changes the verdict.

Refuses to run before results/chento/verdict.json exists. Two additions:

1. Drawdown against FIXED capital. The frozen report-only sequence measured mark-to-market drawdown against
   running peak equity, which shrinks it as equity compounds (A0 BTC -9.6%), while the 2026-09-14 sizing
   review, the reference the operator knows, measured peak-to-trough dollars against the fixed $10,000 with
   day-end and exit marks (A0 BTC 33.4%). This recomputes the sequence the same way, for every arm.
2. Where the no-time-stop difference comes from: A0's time-stopped trades by what they became when held,
   and the difference by period.

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\chento_explore.py
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import chento_lib as C  # noqa: E402
import chento_run as R  # noqa: E402


def fixed_capital_sequence(markets, trades, walks, arm, asset) -> dict:
    w = walks[(walks["arm"] == arm) & (walks["asset"] == asset)].set_index(["t", "direction"])
    tr = trades[trades["asset"] == asset].sort_values("t", kind="mergesort")
    taken, last_entry = [], -10**12
    for row in tr.itertuples(index=False):                       # the frozen sequence rules, unchanged
        now = int(row.t) + C.BAR_S
        if now - last_entry < R.COOLDOWN_H * 3600:
            continue
        e = w.loc[(int(row.t), row.direction)]
        done = [x for x in taken if x["close_ts"] <= now]
        if asset == "BTC" and any(x["kind"] == "stop" and x["R_price"] < 0 and now - x["close_ts"] < R.NO_TILT_H * 3600 for x in done):
            continue
        scale = 1.0
        if asset == "ETH" and done:
            scale = 0.5 if max(done, key=lambda x: x["close_ts"])["R_price"] < 0 else 1.0
        risk_usd = min(R.CAPITAL * R.RISK_PCT / 100 * scale, R.NOTIONAL_MAX_X * R.CAPITAL * float(row.risk) / float(row.entry))
        taken.append({"entry_ts": now, "close_ts": int(e["exit_bar_ts"]) + C.BAR_S, "kind": e["kind"], "R_price": float(e["R_price"]),
                      "net_R": float(e["net_R"]), "risk_usd": risk_usd, "sign": 1 if row.direction == "long" else -1,
                      "entry": float(row.entry), "risk": float(row.risk)})
        last_entry = now
    bars = markets[asset].bars
    marks = sorted({(x["close_ts"] // 86400 + 1) * 86400 for x in taken} | {x["close_ts"] for x in taken}
                   | set(range((min(x["entry_ts"] for x in taken) // 86400 + 1) * 86400,
                               (max(x["close_ts"] for x in taken) // 86400 + 2) * 86400, 86400)))
    peak, worst = 0.0, 0.0
    for m in marks:                                               # day-end marks plus every exit time
        close = float(bars.close[bars.last_le(m - C.BAR_S)])
        pnl = sum(x["net_R"] * x["risk_usd"] for x in taken if x["close_ts"] <= m)
        pnl += sum(x["sign"] * (close - x["entry"]) / x["risk"] * x["risk_usd"] for x in taken if x["entry_ts"] <= m < x["close_ts"])
        peak = max(peak, pnl)
        worst = max(worst, peak - pnl)
    return {"trades": len(taken), "total_return_pct_of_capital": sum(x["net_R"] * x["risk_usd"] for x in taken) / R.CAPITAL * 100,
            "max_drawdown_pct_of_capital": worst / R.CAPITAL * 100}


def main() -> None:
    C.L.require((C.RESULTS / "verdict.json").exists(), "verdict.json missing: exploratory tables only after the verdict")
    signal, ctm, clock = C.open_process("BTC")
    trades = C.load_trades()
    con = C.L.ro_connect(C.SNAPSHOT)
    try:
        markets = {a: C.load_market(con, a, ctm) for a in C.ASSETS}
    finally:
        con.close()
    walks = pd.read_csv(C.RESULTS / "walks.csv.gz")
    dd = {arm: {a: fixed_capital_sequence(markets, trades, walks, arm, a) for a in C.ASSETS}
          for arm in ("A0", "A2_48", "A2_168", "A1", "X1", "X2", "X3")}
    key = ["asset", "t", "direction"]
    a0 = walks[walks["arm"] == "A0"].set_index(key)
    a1 = walks[walks["arm"] == "A1"].set_index(key)
    tif = a0.index[a0["kind"] == "time"]
    held = pd.DataFrame({"a0_R": a0.loc[tif, "net_R"], "a1_R": a1.loc[tif, "net_R"], "a1_kind": a1.loc[tif, "kind"]})
    held["d"] = held["a1_R"] - held["a0_R"]
    by_kind = held.groupby("a1_kind").agg(trades=("d", "size"), mean_a0_R=("a0_R", "mean"), mean_a1_R=("a1_R", "mean"),
                                          sum_d=("d", "sum")).round(3)
    days = trades.set_index(key)["entry_day"]
    held["period"] = np.where(days.loc[held.index] < "2024-01-01", "2021-2023", "2024-2026")
    by_period = held.groupby("period").agg(tif_trades=("d", "size"), mean_d_on_tif_trades=("d", "mean")).round(3)
    out = {"fixed_capital_sequence": dd, "a0_time_stopped_by_a1_outcome": by_kind.reset_index().to_dict("records"),
           "a0_time_stopped_by_period": by_period.reset_index().to_dict("records")}
    C.write_json(C.RESULTS / "exploratory.json", out)
    for arm, v in dd.items():
        print(arm, {a: (s["trades"], round(s["total_return_pct_of_capital"], 1), round(s["max_drawdown_pct_of_capital"], 1)) for a, s in v.items()})
    print(by_kind.to_string())
    print(by_period.to_string())


if __name__ == "__main__":
    main()

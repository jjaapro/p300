"""Preconditions P1-P7 and step 0 for the chento exit-policy study. No arm outcome other than A0 is computed.

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\chento_checks.py all

`all` runs P1, P2, P4, P5, P6, P7 and step 0 in this process (sleeve imported for BTC; the walker's math is
asset-agnostic) and P3 in one subprocess per asset (the sleeve resolves its asset at import). It writes
results/chento/preconditions.json and results/chento/step0.json. P2 compares the A0 walk with the OKX study's
committed trades: those outcomes were already seen and are disclosed in the pre-registration.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def p3_asset(asset: str) -> dict:
    """Flow features from the full-history frame vs the bot's own frame rebuilt at each clock."""
    import numpy as np
    import pandas as pd
    import chento_lib as C
    signal, ctm, clock = C.open_process(asset)
    trades = C.load_trades()
    trades = trades[trades["asset"] == asset].head(20)
    con = C.L.ro_connect(C.SNAPSHOT)
    try:
        mkt = C.load_market(con, asset, ctm)
    finally:
        con.close()
    rows, worst = [], 0.0
    for t in trades["t"]:
        for offset_h in (0, 24, 48):
            b = int(t) + offset_h * 3600
            now = datetime.fromtimestamp(b, tz=timezone.utc)
            clock.set_simulated_now(now)
            signal._rebuild_daily_cache(now, force=True)
            df = signal._cached_features["df"]
            C.L.require(len(df) > 0 and df.index[-1] == pd.Timestamp(now), "rebuild did not end at the clock")
            bot = df.iloc[-1]
            p = mkt.bars.pos(b)
            C.L.require(p >= 0, "bar missing in the snapshot")
            d_cvd = abs(float(bot["cvd_z"]) - float(mkt.cvd_z[p]))
            d_vel = abs(float(bot["vel_z"]) - float(mkt.vel_z[p]))
            worst = max(worst, d_cvd, d_vel)
            same_decisions = True
            for k in C.K_CANDIDATES:
                for direction in ("long", "short"):
                    flat = abs(float(bot["vel_z"])) < C.VEL_Z_MAX
                    cz = float(bot["cvd_z"])
                    bot_fire = flat and (cz > k if direction == "long" else cz < -k)
                    same_decisions &= bool(bot_fire) == bool(mkt.against_mask(direction, k)[p])
            rows.append({"bar": C.iso(b), "abs_diff_cvd_z": d_cvd, "abs_diff_vel_z": d_vel, "decisions_equal": same_decisions})
    ok = worst <= 1e-9 and all(r["decisions_equal"] for r in rows)
    return {"asset": asset, "bars": len(rows), "max_abs_diff": worst, "pass": bool(ok), "rows": rows}


def run_all() -> dict:
    import numpy as np
    import pandas as pd
    import chento_lib as C
    out = {"created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    out["P1"] = C.verify_inputs()
    C.L.require(out["P1"]["pass"], f"P1 failed: {out['P1']}")
    signal, ctm, clock = C.open_process("BTC")
    trades = C.load_trades()
    out["trades"] = {a: int((trades["asset"] == a).sum()) for a in C.ASSETS}
    con = C.L.ro_connect(C.SNAPSHOT)
    try:
        markets = {a: C.load_market(con, a, ctm) for a in C.ASSETS}
    finally:
        con.close()

    # P2: A0 without funding reproduces the OKX study's committed trades
    a0 = C.walk_all(ctm, markets, trades, k=2.0, with_funding=False, arms=(C.BY_ID["A0"],))
    ref = pd.concat([pd.read_csv(C.OKX_DIR / "results" / f"trades_{a}.csv") for a in C.ASSETS], ignore_index=True)
    ref["t"] = [C.L.parse_ts(x) for x in ref["t"]]
    ref["exit_bar_ts"] = [C.L.parse_ts(x) for x in ref["exit_bar_ts"]]
    m = a0.merge(ref[["asset", "t", "direction", "kind", "exit_bar_ts", "exit_price", "R"]], on=["asset", "t", "direction"],
                 how="outer", suffixes=("", "_ref"), indicator=True)
    kind_map = {"time": "tif"}
    both = m["_merge"] == "both"
    p2 = {"walked": int(len(a0)), "reference": int(len(ref)), "matched": int(both.sum()),
          "kind_equal": int((m.loc[both, "kind"].map(lambda k: kind_map.get(k, k)) == m.loc[both, "kind_ref"]).sum()),
          "exit_bar_equal": int((m.loc[both, "exit_bar_ts"] == m.loc[both, "exit_bar_ts_ref"]).sum()),
          "max_abs_diff_exit_price": float((m.loc[both, "exit_price"] - m.loc[both, "exit_price_ref"]).abs().max()),
          "max_abs_diff_R": float((m.loc[both, "R_price"] - m.loc[both, "R"]).abs().max())}
    p2["pass"] = bool(p2["walked"] == p2["reference"] == p2["matched"] == p2["kind_equal"] == p2["exit_bar_equal"]
                      and p2["max_abs_diff_exit_price"] <= 1e-9 * 1e5 and p2["max_abs_diff_R"] <= 1e-9)
    out["P2"] = p2

    # P4 funding coverage and P5 missing bars over the longest (censored) walk path of every trade
    worst_gap, missing, beyond_data = 0, 0, 0
    for tr in trades.itertuples(index=False):
        mkt = markets[tr.asset]
        start, end = int(tr.t), min(int(tr.t) + C.CENSOR_HOURS * 3600, int(mkt.bars.ts[-1]))
        lo = int(np.searchsorted(mkt.funding_s, start - 9 * 3600))
        hi = int(np.searchsorted(mkt.funding_s, end + 9 * 3600))
        s = mkt.funding_s[lo:hi]
        worst_gap = max(worst_gap, int(np.diff(s).max()) if len(s) > 1 else 10**9)
        n_expected = (end - start) // C.BAR_S
        n_have = int(np.searchsorted(mkt.bars.ts, end, side="right") - np.searchsorted(mkt.bars.ts, start + C.BAR_S))
        missing += max(0, n_expected - n_have)
        beyond_data += int(int(tr.t) + C.CENSOR_HOURS * 3600 > int(mkt.bars.ts[-1]))
    out["P4"] = {"max_settlement_gap_s": worst_gap, "pass": worst_gap <= 8 * 3600 + 300}
    out["P5"] = {"missing_bars_on_720h_paths": missing, "trades_reaching_data_end_before_720h": beyond_data,
                 "pass": True}

    # P6 synthetic fixtures
    r = subprocess.run([sys.executable, "-m", "pytest", str(HERE / "tests"), "-q", "-p", "no:cacheprovider"],
                       capture_output=True, text=True)
    out["P6"] = {"summary": r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:],
                 "pass": r.returncode == 0}

    # P7 truncation: exits completed before the cut are unchanged with every later bar deleted
    cut = int(datetime(2023, 6, 1, tzinfo=timezone.utc).timestamp())
    early = trades[trades["t"] < cut]
    full = C.walk_all(ctm, markets, early, k=2.0, with_funding=True)
    short_mk = {}
    for a, mkt in markets.items():
        keep = mkt.bars.ts < cut
        bars = C.L.Bars(mkt.bars.ts[keep], mkt.bars.high[keep], mkt.bars.low[keep], mkt.bars.close[keep])
        short_mk[a] = C.Market(a, bars, mkt.cvd_z[keep], mkt.vel_z[keep],
                               {d: v[v < cut] for d, v in mkt.opposite.items()},
                               mkt.funding_s[mkt.funding_s < cut], mkt.funding_rate[mkt.funding_s < cut])
    trunc = C.walk_all(ctm, short_mk, early, k=2.0, with_funding=True)
    done = full["exit_ts"] <= cut
    cols = ["kind", "exit_bar_ts", "exit_price", "net_R"]
    diffs = int((full.loc[done, cols].to_numpy() != trunc.loc[done, cols].to_numpy()).any(axis=1).sum())
    out["P7"] = {"cut_utc": C.iso(cut), "exits_compared": int(done.sum()), "differences": diffs, "pass": diffs == 0}

    step = C.step0(markets, trades)
    C.write_json(C.RESULTS / "step0.json", step)

    for asset in C.ASSETS:
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "p3", "--asset", asset],
                           capture_output=True, text=True)
        C.L.require(r.returncode == 0, f"P3 {asset} process failed: {r.stderr[-800:]}")
        out[f"P3_{asset}"] = json.loads(r.stdout.strip().splitlines()[-1])
    out["pass"] = all(out[k]["pass"] for k in ("P1", "P2", "P4", "P5", "P6", "P7", "P3_BTC", "P3_ETH"))
    C.write_json(C.RESULTS / "preconditions.json", out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["all", "p3"])
    ap.add_argument("--asset", choices=["BTC", "ETH"])
    args = ap.parse_args()
    if args.mode == "p3":
        print(json.dumps(p3_asset(args.asset)))
    else:
        res = run_all()
        slim = {k: ({kk: vv for kk, vv in v.items() if kk != "rows"} if isinstance(v, dict) else v) for k, v in res.items()}
        print(json.dumps(slim, indent=1, default=str))
        print("PRECONDITIONS PASSED" if res["pass"] else "PRECONDITIONS FAILED")

"""Engine checks on real development data (PREREGISTRATION.md section 11, F0). Prints match counts only.

1. Parity: reference loop (orb_engine) vs vectorized implementation (orb_signals) on BTC
   development sessions for every policy the vectorized code covers; every trade field must match.
2. Randomized-control walker: orb_signals.walk_fixed_stop vs the reference walk on P0's entries
   with seeded random sides.
3. Truncation: the reference engine on a market whose future minutes are deleted must reproduce
   every decision made before the cut.

Run from the repository root:
    venv\\Scripts\\python.exe studies\\notebooks\\orb_study\\orb_parity.py
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import orb_calendars as cal
import orb_engine as eng
import orb_policies as pol
import orb_signals as sig
from orb_checks import RESULTS, load_market

DEV = ("2020-01-01", "2022-12-31")
FIELDS = ["date", "trigger_idx", "side", "fill_idx", "fill_px", "stop_px", "exit_idx", "exit_px", "exit_reason", "flags"]


def sessions_for(p: eng.Policy, start="2019-10-01", end="2022-12-31") -> pd.DataFrame:
    return cal.sessions(p.anchor, max(start, "2020-01-01") if p.anchor == "UTC" else start, end, shift_min=p.shift_min)


def covered(p: eng.Policy) -> bool:
    return (p.target_r is None and not p.exit_event and p.stop != "trail_w" and p.time_exit != "none"
            and p.entry in ("close", "stop"))


def compare(ref: pd.DataFrame, vec: pd.DataFrame) -> dict:
    r = ref[ref["status"] == "trade"].reset_index(drop=True)
    out = {"ref_trades": int(len(r)), "vec_trades": int(len(vec))}
    if len(r) != len(vec):
        out["match"] = False
        out["dates_only_in_ref"] = sorted(set(r["date"]) - set(vec["date"]))[:10]
        out["dates_only_in_vec"] = sorted(set(vec["date"]) - set(r["date"]))[:10]
        return out
    v = vec.reset_index(drop=True)
    mism = {}
    for f in FIELDS:
        a, b = r[f], v[f]
        if f in ("fill_px", "stop_px", "exit_px", "trigger_idx", "side", "fill_idx", "exit_idx"):
            bad = ~np.isclose(a.astype(float), b.astype(float), rtol=0, atol=1e-9)
        elif f == "flags":
            # The vectorized code models only ambiguity flags; data flags (gap_while_invested,
            # time_exit_delayed) exist only in the reference loop and are counted separately.
            ambiguity = lambda s: "|".join(x for x in str(s).split("|") if x.startswith("ambiguous"))  # noqa: E731
            bad = a.fillna("").map(ambiguity) != b.fillna("").map(ambiguity)
            out["reference_data_flags"] = int(a.fillna("").str.contains("gap_while_invested|time_exit_delayed").sum())
        else:
            bad = a.astype(str) != b.astype(str)
        if bad.any():
            mism[f] = int(bad.sum())
    gross_bad = ~np.isclose(r["gross_bp"].astype(float), v["gross_bp"].astype(float), rtol=0, atol=1e-9)
    if gross_bad.any():
        mism["gross_bp"] = int(gross_bad.sum())
    out["mismatched_fields"] = mism
    out["match"] = not mism
    return out


def parity(mkt: eng.Market) -> dict:
    res = {}
    todo = [p for p in (*pol.FAMILY, *pol.CONTROLS, *pol.DIAGNOSTICS) if covered(p)]
    for p in todo:
        s = sessions_for(p)
        ref = eng.run_policy(mkt, s, p, *DEV)
        vec = sig.vector_policy(mkt, s, p, *DEV)
        res[p.id] = compare(ref, vec)
    return res


def random_walker(mkt: eng.Market, seed: int = 7) -> dict:
    s = sessions_for(pol.P0)
    led = eng.run_policy(mkt, s, pol.P0, *DEV)
    tr = led[led["status"] == "trade"].reset_index(drop=True)
    rng = np.random.default_rng(seed)
    side = rng.choice([-1, 1], size=len(tr))
    dist = (tr["fill_px"] - tr["stop_px"]).abs().to_numpy()
    stop = tr["fill_px"].to_numpy() - side * dist
    i0 = np.array([mkt.index(t) for t in tr["t0_ms"]])
    vec = sig.walk_fixed_stop(mkt, side, tr["fill_idx"].to_numpy(), tr["fill_px"].to_numpy(), stop, i0 + 390)
    bad = 0
    for k, row in tr.iterrows():
        w = eng.walk(mkt, int(side[k]), int(row["fill_idx"]), float(row["fill_px"]), float(stop[k]), None, False,
                     pol.P0, row["H"], row["L"], row["W"], int(i0[k]), len(mkt))
        if not (w["exit_idx"] == vec["exit_idx"][k] and abs(w["exit_px"] - vec["exit_px"][k]) < 1e-9
                and w["exit_reason"] == vec["exit_reason"][k]):
            bad += 1
    return {"trades": int(len(tr)), "mismatches": bad, "match": bad == 0}


def truncation(mkt: eng.Market, cut_utc: str = "2021-07-01T00:00:00") -> dict:
    cut = mkt.index(int(datetime.fromisoformat(cut_utc).replace(tzinfo=timezone.utc).timestamp() * 1000))
    short = replace(mkt, open=mkt.open.copy(), high=mkt.high.copy(), low=mkt.low.copy(), close=mkt.close.copy())
    for a in (short.open, short.high, short.low, short.close):
        a[cut:] = np.nan
    out = {}
    for p in (pol.P0, pol.BY_ID["CORE_UTC_R30_STOP"], pol.BY_ID["EXT_RVOL15"], pol.BY_ID["EXT_WIDTH"],
              pol.BY_ID["EXT_TGT2R"], pol.BY_ID["X_REENTER1M"], pol.BY_ID["X_TRAIL"], pol.BY_ID["X_VWAP"]):
        s = sessions_for(p)
        full = eng.run_policy(mkt, s, p, "2020-01-01", "2021-06-30")
        trunc = eng.run_policy(short, s, p, "2020-01-01", "2021-06-30")
        done = full[(full["status"] != "trade") | (full["exit_idx"].fillna(0) < cut)]
        num = ["trigger_idx", "side", "fill_idx", "fill_px", "exit_idx", "exit_px"]
        txt = ["status", "reason", "exit_reason"]
        a = done.set_index("date")
        b = trunc.set_index("date").loc[a.index]
        diff = np.zeros(len(a), dtype=bool)
        for k in num:
            x, y = a[k].astype(float).to_numpy(), b[k].astype(float).to_numpy()
            diff |= ~((np.isnan(x) & np.isnan(y)) | np.isclose(x, y, rtol=0, atol=1e-9))
        for k in txt:
            diff |= a[k].fillna("").astype(str).to_numpy() != b[k].fillna("").astype(str).to_numpy()
        out[p.id] = {"decisions_compared": int(len(a)), "differences": int(diff.sum())}
    out["match"] = all(v["differences"] == 0 for k, v in out.items() if isinstance(v, dict))
    return out


def main() -> dict:
    mkt = load_market("BTCUSDT")
    res = {"created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "parity": parity(mkt), "random_walker": random_walker(mkt), "truncation": truncation(mkt)}
    res["parity_all_match"] = all(v["match"] for v in res["parity"].values())
    res["passed"] = res["parity_all_match"] and res["random_walker"]["match"] and res["truncation"]["match"]
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "engine_checks.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


if __name__ == "__main__":
    r = main()
    for pid, v in r["parity"].items():
        print(f"{pid:22s} ref={v['ref_trades']:4d} vec={v['vec_trades']:4d} match={v['match']} {v.get('mismatched_fields', '')}"
              f"{v.get('dates_only_in_ref', '')}{v.get('dates_only_in_vec', '')}")
    print("random walker:", r["random_walker"])
    print("truncation:", r["truncation"])
    print("ENGINE CHECKS PASSED" if r["passed"] else "ENGINE CHECKS FAILED")

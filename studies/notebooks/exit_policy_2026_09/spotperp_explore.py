"""Spot-vs-perp stage: the two reported tables computed AFTER the verdict, and exploratory only.

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\spotperp_explore.py

Both are required outputs of PREREGISTRATION_SPOT_PERP.md (the target-minute channel of section 5 and section 6's
covariate balance) that the precondition code did not compute, so they are produced here, after `verdict.json`, and
they decide nothing. `spotperp_lib.match` gained one optional argument (`collect`) for the second table; the frozen
behaviour is unchanged and the fixtures still pass, which the freeze check in `outcomes` no longer guards because the
outcome run has already happened. Writes `results/spot_perp/exploratory_reported_tables.json`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import micro_lib as M  # noqa: E402
import spotperp_lib as L  # noqa: E402
import spotperp_run as R  # noqa: E402

KINDS = ("F1_against", "F1_spot_confirmed")


def target_minute_channel(subpops: dict, panels: dict) -> dict:
    """Trades whose first pattern minute is the exit minute itself, by exit kind (section 5).

    The event rule needs `b < x`, so a trade whose first perp-led extreme is the minute that fills its target has no
    event and joins the control pool instead. This counts how often that happens, which is the channel's size.
    """
    out = {}
    for name, tr in subpops.items():
        rows = {k: {"target": 0, "stop": 0, "time": 0, "other": 0} for k in KINDS}
        totals = {k: 0 for k in KINDS}
        for t in tr.itertuples(index=False):
            p = panels[t.asset]
            x = int(t.x)
            if x >= p.n or (x - t.i0) < L.GATE_MIN:
                continue
            s = t.s
            up = p.ser.high[t.i0:x + 1] if s > 0 else -p.ser.low[t.i0:x + 1]
            X = L.running_extreme(up)
            if not X[-1]:                                          # the exit minute is not a new extreme
                continue
            legs_ok = (np.isfinite(p.DP60[x]) and np.isfinite(p.DS60[x]) and np.isfinite(p.dprem[x]))
            if not legs_ok:
                continue
            prem, dp, ds = s * p.dprem[x], s * p.DP60[x], s * p.DS60[x]
            fired = {"F1_against": prem > 0 and dp > 0 and ds <= 0,
                     "F1_spot_confirmed": prem > 0 and dp > 0 and ds > 0}
            for kind, hit in fired.items():
                if not hit:
                    continue
                totals[kind] += 1
                if getattr(t, f"{kind}_first", -1) == -1:          # no in-trade event: the exit minute is the first
                    rows[kind][t.kind if t.kind in rows[kind] else "other"] += 1
        out[name] = {"first_pattern_minute_is_exit": rows, "pattern_holds_at_exit_minute": totals}
    return out


def covariate_balance(events: dict, grids: dict, f0: dict) -> dict:
    """Event minutes against their contributing control minutes, under each test's decision rung (section 6)."""
    out = {}
    for name, tr in events.items():
        g, w = grids[name], R.window_of(name)
        for kind in KINDS:
            rung_name = R.rung_for(name, kind, f0)
            spec = next((r for r in L.RUNGS if r.name == rung_name), L.RUNGS[0])
            rows, pooled = L.match(tr, g, kind, w, spec, collect=True)
            if not len(rows) or not len(pooled["vr"]):
                continue
            ev = rows[rows["controls"] >= L.MIN_CONTROLS]
            if not len(ev):                                        # no included event trade: nothing to balance
                continue
            out[f"{name}:{kind}"] = {
                "rung": rung_name, "event_trades": int(len(ev)), "control_minutes": int(len(pooled["vr"])),
                "median_vr": {"event": float(np.nanmedian(ev["vr"])), "control": float(np.nanmedian(pooled["vr"]))},
                "median_abs_pt": {"event": float(np.nanmedian(np.abs(
                    [g.pt[i, int(e)] for i, e in zip(ev.index, ev["elapsed_min"])]))),
                    "control": float(np.nanmedian(np.abs(pooled["pt"])))},
                "ordinal_of_extreme": {
                    "event": [float(np.percentile(ev["ord"], q)) for q in (25, 50, 75)],
                    "control": [float(np.percentile(pooled["ord"], q)) for q in (25, 50, 75)]},
                "session_share": {
                    "event": {n: round(float((ev["session"] == i).mean()), 3) for i, n in enumerate(L.SESSION_NAMES)},
                    "control": {n: round(float((pooled["session"] == i).mean()), 3)
                                for i, n in enumerate(L.SESSION_NAMES)}},
                "weekend_share": {"event": round(float(ev["weekend"].mean()), 3),
                                  "control": round(float(pooled["weekend"].mean()), 3)},
            }
    return out


def main() -> dict:
    subpops, _, _ = R.load_subpops()
    _, panels = R.load_panels()
    events, grids = {}, {}
    for name, tr in subpops.items():
        g = L.build_grids(tr, panels, with_cv=True)
        events[name] = L.first_events(tr, g)
        grids[name] = g
    f0 = json.loads((R.RESULTS / "freeze_F0.json").read_text())
    out = {"created_utc": R.now_utc(), "exploratory": True,
           "after_verdict_created_utc": json.loads((R.RESULTS / "verdict.json").read_text())["created_utc"],
           "target_minute_channel": target_minute_channel(events, panels),
           "covariate_balance": covariate_balance(events, grids, f0)}
    M.write_json(R.RESULTS / "exploratory_reported_tables.json", out)
    print(json.dumps(out["target_minute_channel"], indent=1))
    for k, v in out["covariate_balance"].items():
        print(k, "vr", v["median_vr"], "ord", v["ordinal_of_extreme"])
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    main()

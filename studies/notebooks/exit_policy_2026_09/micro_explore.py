"""Microstructure stage 1 — EXPLORATORY, after the verdict (not pre-registered; the verdict does not use it).

The outcome run showed every chento event and sign control positive in the first half and negative in the second. The
frozen placebo draws controls from every year, so a period in which holding paid more for all trades can show up as
"information" in whichever events cluster in it. This control repeats the frozen matching with one extra condition:
controls must enter within +-365 days of the event trade. It calls the frozen `micro_lib.match` on each event trade
together with its era pool, so nothing else about the matching changes.

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\micro_explore.py
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import micro_lib as M  # noqa: E402
import micro_run as R  # noqa: E402

ERA_DAYS = 365


def era_matched_rows(tr: pd.DataFrame, series: dict, kind: str, pop: str) -> pd.DataFrame:
    """The frozen match for each event trade, run on that trade plus the trades entering within +-ERA_DAYS of it."""
    first = tr[f"{kind}_first"].to_numpy(np.int64)
    events = np.flatnonzero(M.eligible(tr, kind) & (first >= 0))
    out = []
    for i in events:
        near = np.abs(tr["entry_ts"].to_numpy() - tr["entry_ts"].iat[i]) <= ERA_DAYS * 86400
        sub = tr[near].reset_index(drop=True)
        grids = M.build_grids(sub, series, with_cv=True)
        rows = M.match(sub, grids, kind, M.window_minutes(pop))
        out.append(rows[rows["tid"] == tr["tid"].iat[i]])
    rows = pd.concat(out, ignore_index=True)
    rows["delta"] = rows["cv"] - rows["placebo"]
    return rows


def main() -> dict:
    _, series, pops = R.load_all()
    frozen = json.loads((M.RESULTS / "report.json").read_text())
    result = {"created_utc": R.now_utc(), "exploratory": True, "era_days": ERA_DAYS,
              "after_verdict_created_utc": json.loads((M.RESULTS / "verdict.json").read_text())["created_utc"],
              "tests": {}}
    for pop in ("chento", "squeeze_bull"):
        tr = pops[pop]
        axis = M.day_axis(tr["entry_day"])
        idx = M.block_indices(len(axis))
        for kind in M.KINDS:
            if not frozen["tests"][f"{pop}:{kind}"]["delta"].get("n"):
                continue
            rows = era_matched_rows(tr, series, kind, pop)
            s = M.summarize(rows, "delta", axis, idx)
            inc = rows[np.isfinite(rows["placebo"])]
            s["mean_cv_at_event"] = float(inc["cv"].mean()) if len(inc) else float("nan")
            s["mean_placebo"] = float(inc["placebo"].mean()) if len(inc) else float("nan")
            s["frozen_all_years_mean"] = frozen["tests"][f"{pop}:{kind}"]["delta"]["mean"]
            result["tests"][f"{pop}:{kind}"] = s
            print(f"{pop}:{kind}: n={s['n']} era-matched {s.get('mean', float('nan')):+.3f} "
                  f"(all years {s['frozen_all_years_mean']:+.3f})", flush=True)
    M.write_json(M.RESULTS / "exploratory_era_matched.json", result)
    return result


if __name__ == "__main__":
    main()

"""Re-cut of 2026-09-19: what the corrected open-interest table changed.

BACKLOG item 30 re-stamped cd_open_interest's Binance-era rows (2026-06-10 09:00
onward) from start-of-hour to close-of-hour — the convention this study's June
rules were written on. This compares the pipeline's results on the corrected
table against the committed 2026-09-08 results (snapshotted before the re-run)
at the fire level, so the table's effect and the OOS window's ten-day extension
(09-07 -> 09-17) are reported separately.

    python recut_oi_table_2026_09_19.py --before <dir with the 09-08 results>

Writes results/recut_2026_09_19.json. Read-only otherwise.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
SEAM = pd.Timestamp("2026-06-10 09:00", tz="UTC")     # first re-stamped row
OLD_WINDOW_END = pd.Timestamp("2026-09-07 23:59:59", tz="UTC")


def ledger(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts").sort_index()


def decision(summary: dict) -> dict:
    """The fields the BUILD rule reads, pulled the same way from either run."""
    a = summary.get("a_oos_bull_gated", summary.get("clause_a", {}))
    b = summary.get("b_full_sample_combined_MAR", summary.get("clause_b", {}))
    return {"verdict": summary.get("verdict"), "reason": summary.get("reason"),
            "oos_window": summary.get("oos_window") or summary.get("window"),
            "a": a, "b": b}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--before", type=Path, required=True,
                    help="directory holding the 2026-09-08 results files")
    args = ap.parse_args(argv)

    out: dict = {"seam": str(SEAM), "old_window_end": str(OLD_WINDOW_END)}
    for name in ("oos_oi_flush_ledger.csv", "full_oi_flush_ledger.csv"):
        old, new = ledger(args.before / name), ledger(RESULTS / name)
        cols = [c for c in ("r_outcome", "regime", "resolved") if c in old.columns and c in new.columns]
        both = old.index.intersection(new.index)
        removed = old.index.difference(new.index)
        added = new.index.difference(old.index)
        changed = [ts for ts in both
                   if "r_outcome" in cols and abs(float(old.loc[ts, "r_outcome"]) - float(new.loc[ts, "r_outcome"])) > 1e-9]
        rep = {
            "old_fires": int(len(old)), "new_fires": int(len(new)),
            "unchanged": int(len(both) - len(changed)),
            "changed_r": [{"ts": str(ts), "old": float(old.loc[ts, "r_outcome"]),
                           "new": float(new.loc[ts, "r_outcome"])} for ts in changed],
            "removed": [{"ts": str(ts), **{c: (old.loc[ts, c].item() if hasattr(old.loc[ts, c], "item") else old.loc[ts, c]) for c in cols}}
                        for ts in removed],
            "added": [{"ts": str(ts), **{c: (new.loc[ts, c].item() if hasattr(new.loc[ts, c], "item") else new.loc[ts, c]) for c in cols},
                       "beyond_old_window": bool(ts > OLD_WINDOW_END)} for ts in added],
        }
        # Anything before the seam that moved is NOT the table's doing.
        rep["pre_seam_differences"] = [d["ts"] for d in rep["changed_r"] + rep["removed"] + rep["added"]
                                       if pd.Timestamp(d["ts"]) < SEAM]
        out[name] = rep
        print(f"\n{name}: {rep['old_fires']} -> {rep['new_fires']} fires; "
              f"{rep['unchanged']} unchanged, {len(changed)} changed R, "
              f"{len(removed)} removed, {len(added)} added "
              f"({sum(a['beyond_old_window'] for a in rep['added'])} of them past the old window end)")
        for d in rep["changed_r"]:
            print(f"   changed  {d['ts']}  R {d['old']:+.3f} -> {d['new']:+.3f}")
        for d in rep["removed"]:
            print(f"   removed  {d['ts']}  " + "  ".join(f"{k}={v}" for k, v in d.items() if k != "ts"))
        for d in rep["added"]:
            print(f"   added    {d['ts']}  " + "  ".join(f"{k}={v}" for k, v in d.items() if k != "ts"))
        if rep["pre_seam_differences"]:
            print(f"   WARNING: {len(rep['pre_seam_differences'])} differences before the seam — "
                  f"not attributable to the re-stamp; investigate")

    old_s = json.loads((args.before / "summary.json").read_text(encoding="utf-8"))
    new_s = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    out["decision_before"], out["decision_after"] = decision(old_s), decision(new_s)
    print("\nDECISION 2026-09-08 run:", json.dumps(out["decision_before"], indent=1)[:900])
    print("\nDECISION corrected table:", json.dumps(out["decision_after"], indent=1)[:900])
    (RESULTS / "recut_2026_09_19.json").write_text(json.dumps(out, indent=2, default=str) + "\n",
                                                   encoding="utf-8")
    print(f"\nwrote {RESULTS / 'recut_2026_09_19.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

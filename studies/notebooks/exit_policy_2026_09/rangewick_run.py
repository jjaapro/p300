"""Stage R (move vs implied range, rejection-wick exit): preconditions, the freeze, and the one outcome run.

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\rangewick_run.py checks
    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\rangewick_run.py freeze0
    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\rangewick_run.py outcomes

`checks` exports the DVOL table (read-only), then computes counts only and never a continuation value at an event
(PREREGISTRATION_RANGE_WICK.md section 9). `freeze0` refuses unless the checks passed and never overwrites a freeze.
`outcomes` refuses unless every frozen hash still holds and never reruns. Results land in results/range_wick/.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import micro_lib as M  # noqa: E402
import micro_run as MR  # noqa: E402
import rangewick_lib as L  # noqa: E402

RESULTS = L.RESULTS
PROD_DB = M.ROOT / "data" / "databases" / "prod.db"
HORIZON_MIN = {p: h * 60 for p, h in M.HORIZON_H.items()}
FROZEN = ["PREREGISTRATION_RANGE_WICK.md", "rangewick_lib.py", "rangewick_run.py",
          "tests/test_rangewick_events.py", "results/range_wick/preconditions.json",
          "results/range_wick/dvol_daily.csv"]
IMPORTED = {"results/microstructure/freeze_F0.json": ("micro_lib.py", "micro_run.py"),
            "results/spot_perp/freeze_F0.json": ("spotperp_lib.py",)}
PRECONDITIONS = ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- inputs -----------------------------------------------------------------------------------------

def export_dvol(now: datetime | None = None) -> pd.DataFrame:
    """deribit_dvol_daily, read-only, the partial current UTC day dropped, written to results/range_wick/."""
    today = (now or datetime.now(timezone.utc)).date().isoformat()
    con = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query("SELECT asset, timestamp, open, high, low, close FROM deribit_dvol_daily "
                               "ORDER BY asset, timestamp", con)
    finally:
        con.close()
    df["day"] = [M.utc_day(int(t)) for t in df["timestamp"]]
    df = df[df["day"] < today][["asset", "day", "open", "high", "low", "close"]].reset_index(drop=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    df.to_csv(L.DVOL_CSV, index=False)
    return df


def load_panels(dvol: pd.DataFrame, cut: dict | None = None) -> tuple[dict, dict]:
    raws = {a: M.load_raw(a) for a in MR.ASSETS}
    panels = {a: L.build_panel(a, raws[a], dvol, n_minutes=(cut or {}).get(a)) for a in MR.ASSETS}
    return raws, panels


def load_subpops():
    """Stage 1's three populations, walked by its walker, with chento split by asset (the spot-vs-perp split)."""
    _, series, pops = MR.load_all()
    out = {}
    for pop, tr in pops.items():
        tr = tr.copy()
        tr["horizon_min"] = HORIZON_MIN[pop]
        if pop == "chento":
            for asset in ("BTC", "ETH"):
                out[f"chento_{asset}"] = tr[tr["asset"] == asset].reset_index(drop=True)
        else:
            out[pop] = tr.reset_index(drop=True)
    return out, series, pops


def prepare(subpops: dict, panels: dict, with_cv: bool = False) -> tuple[dict, dict]:
    events, grids = {}, {}
    for name, tr in subpops.items():
        g = L.build_grids(tr, panels, with_cv=with_cv)
        events[name] = L.first_events(tr, g)
        grids[name] = g
    return events, grids


def window_of(subpop: str) -> int:
    return M.window_minutes(L.POP_OF[subpop])


def exit_ts(tr: pd.DataFrame) -> np.ndarray:
    return tr["entry_ts"].to_numpy(np.int64) + (tr["x"].to_numpy(np.int64) - tr["i0"].to_numpy(np.int64)) * 60


# --- preconditions ----------------------------------------------------------------------------------

def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(M.ROOT), capture_output=True, text=True).stdout.strip()


SQB_LEDGER_REL = "studies/notebooks/squeeze_bull_revalidation/results/full_oi_flush_ledger.csv"


def p1_inputs(dvol_sha: str) -> dict:
    """Amendment A1: the imported libraries are recorded as they stand at HEAD (git-clean), the squeeze_bull ledger
    is the corrected-open-interest re-cut (git-clean, its commit recorded), and every other stage 1 input must
    still match stage 1's frozen hash."""
    hashes = MR.input_hashes()
    stage1 = json.loads((HERE / "results" / "microstructure" / "freeze_F0.json").read_text())
    out = {"imported": {}, "perp_panels": {}, "stage1_inputs": {}}
    for freeze_rel, files in IMPORTED.items():
        for rel in files:
            path = f"studies/notebooks/exit_policy_2026_09/{rel}"
            out["imported"][rel] = {"sha256": M.sha256(HERE / rel),
                                    "git_clean": _git("status", "--porcelain", "--", path) == "",
                                    "head_blob": _git("rev-parse", f"HEAD:{path}"), "earlier_freeze": freeze_rel}
    for k, v in hashes.items():
        out["stage1_inputs"][k] = {"match": v == stage1["inputs_sha256"].get(k)}
    out["stage1_inputs"]["squeeze_bull_ledger"] = {
        "match": hashes["squeeze_bull_ledger"] == stage1["inputs_sha256"]["squeeze_bull_ledger"],
        "sha256_now": hashes["squeeze_bull_ledger"],
        "stage1_sha256": stage1["inputs_sha256"]["squeeze_bull_ledger"],
        "git_clean": _git("status", "--porcelain", "--", SQB_LEDGER_REL) == "",
        "head_blob": _git("rev-parse", f"HEAD:{SQB_LEDGER_REL}"),
        "last_commit": _git("log", "-1", "--format=%h %ci %s", "--", SQB_LEDGER_REL)}
    for a in MR.ASSETS:
        meta = json.loads((M.ORB_CACHE / f"{M.SYMBOL[a]}_perp_1m.meta.json").read_text())
        out["perp_panels"][a] = {"match": hashes[f"perp_1m_{a}"] == meta["file_sha256"],
                                 "logical_sha256": meta["logical_sha256"]}
    out["micro_trades_sha256"] = M.sha256(HERE / "results" / "microstructure" / "trades.csv.gz")
    out["dvol_export_sha256"] = dvol_sha
    out["head"] = _git("rev-parse", "--short", "HEAD")
    out["pass"] = bool(all(v["match"] for k, v in out["stage1_inputs"].items() if k != "squeeze_bull_ledger")
                       and out["stage1_inputs"]["squeeze_bull_ledger"]["git_clean"]
                       and all(v["git_clean"] for v in out["imported"].values())
                       and all(v["match"] for v in out["perp_panels"].values()))
    return out


def p2_populations(subpops: dict, pops: dict) -> dict:
    """Amendment A1: squeeze_bull is the corrected-open-interest re-cut, so stage 1's S0 identity is reported, not
    required, and the symmetric difference to stage 1's set is listed; chento and short_squeeze must be stage 1's."""
    m2 = MR.m2_identity(pops)
    counts = {k: int(len(v)) for k, v in subpops.items()}
    directions = {k: v["direction"].value_counts().to_dict() for k, v in subpops.items()}
    i_eligible = {k: int(L.eligible(v, "I1_implied").sum()) for k, v in subpops.items()}
    saved = pd.read_csv(HERE / "results" / "microstructure" / "trades.csv.gz")
    old = set(saved[saved["pop"] == "squeeze_bull"]["tid"])
    new = set(subpops["squeeze_bull"]["tid"])
    sqb = {"stage1_trades": len(old), "trades_now": len(new), "shared": len(old & new),
           "only_in_stage1": sorted(old - new), "only_now": sorted(new - old)}
    ok = counts["chento_BTC"] == 208 and counts["chento_ETH"] == 184 and counts["short_squeeze"] == 71
    chento_ok = bool(m2["chento"]["equals_A0"]) and bool(m2["short_squeeze"]["all_london_ny"]) \
        and m2["short_squeeze"]["distinct_triggers"] == 71
    return {"stage1_m2": m2, "counts": counts, "directions": directions, "i_eligible": i_eligible,
            "squeeze_bull_vs_stage1": sqb, "pass": bool(chento_ok and ok)}


def p3_walker(subpops: dict) -> dict:
    """Every walked trade that stage 1 also walked equals its saved row; the trades the re-cut added are listed,
    not compared (amendment A1)."""
    saved = pd.read_csv(HERE / "results" / "microstructure" / "trades.csv.gz").set_index("tid")
    mine = pd.concat(subpops.values(), ignore_index=True).set_index("tid")
    shared = saved.index.intersection(mine.index)
    diffs = {}
    for col in ("i0", "x", "kind", "x_notime", "kind_notime", "level_valid"):
        a, b = mine.loc[shared, col], saved.loc[shared, col]
        diffs[col] = int((a.astype(str) != b.astype(str)).sum())
    for col in ("exit_price", "exit_price_notime", "level"):
        a, b = mine.loc[shared, col].to_numpy(float), saved.loc[shared, col].to_numpy(float)
        both_nan = np.isnan(a) & np.isnan(b)
        diffs[col] = int(((np.abs(a - b) > 1e-9 * np.abs(b)) & ~both_nan).sum())
    return {"trades_now": int(len(mine)), "stage1_trades": int(len(saved)), "compared": int(len(shared)),
            "not_in_stage1": sorted(mine.index.difference(saved.index)), "differences": diffs,
            "pass": bool(len(shared) >= len(saved) - 5 and not any(diffs.values()))}


def p4_dvol(dvol: pd.DataFrame) -> dict:
    facts = L.dvol_facts(dvol)
    ok = all(a in facts for a in MR.ASSETS) and all(v["bad_closes"] == 0 for v in facts.values())
    return {"facts": facts, "first_eligible_day": L.DVOL_FROM, "pass": bool(ok)}


def p5_fixtures() -> dict:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        str(HERE / "tests" / "test_rangewick_events.py")], capture_output=True, text=True,
                       cwd=str(M.ROOT))
    tail = (r.stdout or "").strip().splitlines()[-3:]
    return {"returncode": r.returncode, "tail": tail, "pass": r.returncode == 0}


def _masks_upto(p: L.Panel, tr, upto: int) -> dict:
    m, ok, _, armed = L.trade_masks(p, tr.i0, upto + 1, tr.s, tr.entry, tr.risk, tr.target,
                                    float(tr.level), bool(tr.level_valid))
    out = {k: v for k, v in m.items()}
    out.update({f"ok_{f}": v for f, v in ok.items()})
    out["armed"] = armed
    out["imp"], out["rv"], out["day_open"] = p.imp[tr.i0:upto + 1], p.rv[tr.i0:upto + 1], p.day_open[tr.i0:upto + 1]
    return out


def p6_causality(subpops: dict, dvol: pd.DataFrame, panels: dict, raws: dict) -> dict:
    """Truncate the panel and the DVOL rows at a cut inside a trade; nothing at or before the cut may change."""
    rng = np.random.default_rng(M.SEED)
    out = {"cuts": [], "pass": True}
    allt = pd.concat(subpops.values(), ignore_index=True)
    for asset in MR.ASSETS:
        pool = allt[allt["asset"] == asset]
        for k in rng.choice(len(pool), size=5, replace=False):
            tr = pool.iloc[int(k)]
            c = int(tr.i0 + (tr.x - tr.i0) // 2)
            day_c = M.utc_day(raws[asset]["t0_s"] + 60 * c)
            dvol_cut = dvol[dvol["day"] < day_c]
            cut_panel = L.build_panel(asset, raws[asset], dvol_cut, n_minutes=c + 1)
            open_trades = pool[(pool["i0"] <= c) & (pool["x"] > c)]
            changed = []
            for o in open_trades.itertuples(index=False):
                full = _masks_upto(panels[asset], o, c)
                cut = _masks_upto(cut_panel, o, c)
                for key in full:
                    a, b = np.asarray(full[key]), np.asarray(cut[key])
                    same = np.array_equal(a, b) if a.dtype == bool else np.array_equal(np.isnan(a), np.isnan(b)) \
                        and np.allclose(np.nan_to_num(a), np.nan_to_num(b))
                    if not same:
                        changed.append((o.tid, key))
            rec = {"asset": asset, "tid": tr.tid, "cut_minute": c, "cut_day": day_c,
                   "open_trades": int(len(open_trades)), "changed": changed[:10]}
            out["cuts"].append(rec)
            if changed:
                out["pass"] = False
    return out


def p7_coverage(events: dict, grids: dict) -> dict:
    out = {"per_subpop": {}, "descriptive": {}, "pass": True}
    for name, tr in events.items():
        g = grids[name]
        base = g.open_ & g.gate
        el_i = L.eligible(tr, "I1_implied")
        cov_i = float((base[el_i] & g.ok["I"][el_i]).sum() / max(1, base[el_i].sum())) if el_i.any() else float("nan")
        cov_j = float((base & g.ok["J"]).sum() / max(1, base.sum()))
        by_year = {}
        years = tr["entry_day"].str[:4].to_numpy()
        for y in sorted(set(years)):
            rows = (years == y) & el_i
            if rows.any():
                by_year[y] = float((base[rows] & g.ok["I"][rows]).sum() / max(1, base[rows].sum()))
        out["per_subpop"][name] = {"coverage_I": cov_i, "coverage_J_bars": cov_j, "coverage_I_by_year": by_year}
        out["descriptive"][name] = {"I": bool(not np.isfinite(cov_i) or cov_i < L.COVERAGE_MIN),
                                    "J": bool(cov_j < L.COVERAGE_MIN)}
    return out


def exit_minute_channel(subpops: dict, panels: dict) -> dict:
    """Trades whose first pattern minute with b <= x is the exit minute x, by kind and exit kind (section 6)."""
    out = {}
    for name, tr in subpops.items():
        counts = {k: {"target": 0, "stop": 0, "time": 0, "other": 0} for k in L.KINDS}
        for t in tr.itertuples(index=False):
            p = panels[t.asset]
            if t.x >= p.n:
                continue
            m, _, _, _ = L.trade_masks(p, t.i0, t.x + 1, t.s, t.entry, t.risk, t.target, float(t.level), bool(t.level_valid))
            n = t.x + 1 - t.i0
            keep = (np.arange(n) >= L.GATE_MIN) & np.isfinite(p.ser.close[t.i0:t.x + 1])
            for k in L.KINDS:
                mk = m[k] & keep
                if mk.any() and int(np.argmax(mk)) == n - 1:
                    counts[k][t.kind if t.kind in counts[k] else "other"] += 1
        out[name] = counts
    return out


def covariate_balance(tr: pd.DataFrame, g: L.Grids, kind: str, window: int, spec: L.Spec) -> dict:
    rows, pooled = L.match(tr, g, kind, window, spec, collect=True)
    if not len(rows) or not len(pooled["vr"]):
        return {"n": int(len(rows))}
    q = lambda v: [float(np.nanquantile(v, x)) for x in (0.25, 0.5, 0.75)]  # noqa: E731
    return {"n": int(len(rows)),
            "event": {"vr_median": float(np.nanmedian(rows["vr"])), "mark_median": float(rows["mark_R"].median()),
                      "ord_q": q(rows["ord"].to_numpy(float)),
                      "session_share": rows["session"].value_counts(normalize=True).to_dict(),
                      "weekend_share": float(rows["weekend"].mean())},
            "controls": {"vr_median": float(np.nanmedian(pooled["vr"])), "mark_median": float(np.nanmedian(pooled["mark"])),
                         "ord_q": q(pooled["ord"].astype(float)),
                         "session_share": pd.Series(pooled["session"]).value_counts(normalize=True).to_dict(),
                         "weekend_share": float(np.mean(pooled["weekend"]))}}


def overlap_shares(events: dict) -> dict:
    """ETH event trades of a kind overlapping a chento-BTC event trade of the kind; squeeze_bull's likewise."""
    out = {}
    btc = events["chento_BTC"]
    for kind in L.FAMILY_CANDIDATES:
        b = btc[btc[f"{kind}_first"] >= 0]
        b_lo, b_hi = b["entry_ts"].to_numpy(np.int64), exit_ts(b)
        for other in ("chento_ETH", "squeeze_bull"):
            e = events[other]
            e = e[e[f"{kind}_first"] >= 0]
            lo, hi = e["entry_ts"].to_numpy(np.int64), exit_ts(e)
            overl = np.array([bool(((b_lo <= h) & (l <= b_hi)).any()) for l, h in zip(lo, hi)], dtype=bool)
            out[f"{other}:{kind}"] = {"event_trades": int(len(e)), "overlapping_btc_event_trade": int(overl.sum()),
                                      "non_overlap_tids": [t for t, o in zip(e["tid"], overl) if not o]}
    return out


def p8_counts(events: dict, grids: dict, subpops: dict, panels: dict) -> dict:
    out = {"per_subpop": {}, "family": [], "decision_rungs": {}}
    for name, tr in events.items():
        g, w = grids[name], window_of(name)
        rows = {}
        for kind in L.KINDS:
            el = L.eligible(tr, kind)
            first = tr[f"{kind}_first"].to_numpy(np.int64)
            has = el & (first >= 0)
            entry = {"eligible_trades": int(el.sum()), "trades_with_event": int(has.sum()),
                     "event_share": float(has.sum() / max(1, el.sum()))}
            if has.any():
                elapsed = (first[has] - tr["i0"].to_numpy(np.int64)[has]) / 60.0
                entry["median_elapsed_hours"] = float(np.median(elapsed))
                entry["event_minutes_total"] = int(tr[f"{kind}_minutes"].to_numpy()[has].sum())
            counts = {}
            for spec in L.RUNGS + L.SECONDARY:
                r = L.match(tr, g, kind, w, spec)
                counts[spec.name] = L.included(r)
                if spec.name == "rung1" and len(r):
                    entry["dropped_for_lack_of_controls_rung1"] = int((r["controls"] < L.MIN_CONTROLS).sum())
                    entry["mean_distinct_controls_rung1"] = float(r["controls"].mean())
            rung = L.decision_rung(counts)
            entry["included"] = counts
            entry["decision_rung"] = rung
            if rung:
                rs = next(r for r in L.RUNGS if r.name == rung)
                for spec in L.robustness_specs(rs) + (L.shared_spec(rs),):
                    entry["included"][spec.name] = L.included(L.match(tr, g, kind, w, spec))
                if kind in L.FAMILY_CANDIDATES or kind in L.DECISION_CONTROL.values():
                    entry["balance"] = covariate_balance(tr, g, kind, w, rs)
            rows[kind] = entry
            if kind in L.FAMILY_CANDIDATES and name in L.FAMILY_SUBPOPS:
                out["decision_rungs"][f"{name}:{kind}"] = rung
        # base rates
        el_i = L.eligible(tr, "I1_implied")
        armed = g.armed
        shape = g.kind["J_shape"]
        wick_at_level = g.kind["J_wick"]
        rows["_base_rates"] = {
            "share_eligible_trades_crossing_IMP": float((tr["I1_implied_minutes"].to_numpy()[el_i] > 0).mean()) if el_i.any() else None,
            "share_eligible_trades_crossing_RV": float((tr["I1_realised_minutes"].to_numpy()[el_i] > 0).mean()) if el_i.any() else None,
            "armed_bars": int(armed.sum()), "armed_bars_with_rejection_shape": int(shape.sum()),
            "share_armed_with_shape": float(shape.sum() / max(1, armed.sum())),
            "share_shape_at_level": float(wick_at_level.sum() / max(1, shape.sum())),
            "mean_candidate_levels_per_trade": float(np.mean([len(x) for x in g.levels])) if g.levels else None}
        # the power line: one random open minute per trade, never an event minute by construction of the draw
        rng = np.random.default_rng(M.SEED)
        cvs = []
        for i in range(len(tr)):
            open_idx = np.flatnonzero(g.open_[i] & g.gate[i])
            if len(open_idx):
                cvs.append(g.cv[i, rng.choice(open_idx)])
        sd = float(np.std(cvs, ddof=1)) if len(cvs) > 2 else float("nan")
        rows["_power"] = {"sd_cv_random_minute": sd,
                          "mde_R": {k: float(M.POWER_Z * sd / np.sqrt(v["trades_with_event"]))
                                    for k, v in rows.items() if not k.startswith("_") and v.get("trades_with_event")}}
        out["per_subpop"][name] = rows
    out["exit_minute_channel"] = exit_minute_channel(subpops, panels)
    out["overlap"] = overlap_shares(events)
    return out


def checks() -> dict:
    dvol = export_dvol()
    dvol_sha = M.sha256(L.DVOL_CSV)
    subpops, series, pops = load_subpops()
    raws, panels = load_panels(dvol)
    events, grids = prepare(subpops, panels, with_cv=True)      # cv only for the power line's random minutes
    out = {"created_utc": now_utc(), "P1": p1_inputs(dvol_sha), "P2": p2_populations(subpops, pops),
           "P3": p3_walker(subpops), "P4": p4_dvol(dvol), "P5": p5_fixtures(),
           "P6": p6_causality(subpops, dvol, panels, raws), "P7": p7_coverage(events, grids),
           "P8": p8_counts(events, grids, subpops, panels)}
    desc = out["P7"]["descriptive"]
    family = [f"{n}:{k}" for n in L.FAMILY_SUBPOPS for k in L.FAMILY_CANDIDATES
              if out["P8"]["decision_rungs"].get(f"{n}:{k}") and not desc[n][L.KIND_FAMILY[k]]]
    out["P8"]["family"] = family
    out["P8"]["pass"] = True
    out["pass"] = all(out[k]["pass"] for k in PRECONDITIONS)
    M.write_json(RESULTS / "preconditions.json", out)
    for k in PRECONDITIONS:
        print(f"{k}: {'pass' if out[k]['pass'] else 'FAIL'}", flush=True)
    print("family:", family, flush=True)
    print("decision rungs:", out["P8"]["decision_rungs"], flush=True)
    return out


# --- freeze -----------------------------------------------------------------------------------------

def freeze0() -> Path:
    pre = json.loads((RESULTS / "preconditions.json").read_text())
    if not pre["pass"]:
        raise SystemExit("preconditions did not pass; nothing is frozen")
    target = RESULTS / "freeze_F0.json"
    if target.exists():
        raise SystemExit("freeze_F0.json exists; a freeze is never overwritten")
    M.write_json(target, {
        "freeze": "F0", "created_utc": now_utc(),
        "note": "Before any continuation value at an event. The checks computed event minutes, placebo control "
                "counts, base rates and the power line's random-minute sd only. No threshold was changed after "
                "reading them.",
        "family": pre["P8"]["family"], "decision_rungs": pre["P8"]["decision_rungs"],
        "descriptive": pre["P7"]["descriptive"],
        "files": {f: M.sha256(HERE / f) for f in FROZEN},
        "imported_freezes": {rel: M.sha256(HERE / rel) for rel in IMPORTED},
        "stage1_inputs_sha256": MR.input_hashes(),
    })
    print(f"frozen: {target}", flush=True)
    return target


# --- outcomes ---------------------------------------------------------------------------------------

def rung_for(subpop: str, kind: str, f0: dict) -> str:
    """A control, reported kind or replication line is read under the rung of the event test it accompanies."""
    anchor = "J_wick" if L.KIND_FAMILY[kind] in ("J", "J1") else "I1_implied"
    if subpop == L.REPLICATION_SUBPOP:
        for candidate in L.FAMILY_SUBPOPS:
            rung = f0["decision_rungs"].get(f"{candidate}:{anchor}")
            if rung:
                return rung
    return f0["decision_rungs"].get(f"{subpop}:{anchor}") or "rung1"


def summarize_rows(rows: pd.DataFrame, axis: list, idx: np.ndarray) -> dict:
    out = M.summarize(rows, "delta", axis, idx) if len(rows) else {"n": 0}
    if out.get("n"):
        inc = rows[np.isfinite(rows["placebo"])]
        out["mean_cv_at_event"] = float(inc["cv"].mean())
        out["mean_placebo"] = float(inc["placebo"].mean())
        out["by_direction"] = {d: {"n": int(len(g)), "mean": float(g["delta"].mean())}
                               for d, g in inc.groupby("direction")}
        terc = pd.qcut(inc["elapsed_min"].rank(method="first"), 3, labels=["early", "mid", "late"]) if len(inc) >= 3 else None
        if terc is not None:
            out["by_elapsed_tercile"] = {str(t): {"n": int(len(g)), "mean": float(g["delta"].mean())}
                                         for t, g in inc.groupby(terc, observed=True)}
        out["by_bin"] = {int(b): {"n": int(len(g)), "mean": float(g["delta"].mean())} for b, g in inc.groupby("bin")}
        out["by_session"] = {int(b): {"n": int(len(g)), "mean": float(g["delta"].mean())}
                             for b, g in inc.groupby("session")}
    return out


def eth_non_overlap(tests: dict, events: dict) -> dict:
    """Section 8: the ETH event trades of a kind whose interval overlaps no chento-BTC event trade of the kind."""
    out = {}
    btc, eth = events["chento_BTC"], events[L.REPLICATION_SUBPOP]
    for kind in L.FAMILY_CANDIDATES:
        b = btc[btc[f"{kind}_first"] >= 0]
        b_lo, b_hi = b["entry_ts"].to_numpy(np.int64), exit_ts(b)
        e = eth[eth[f"{kind}_first"] >= 0]
        lo, hi = e["entry_ts"].to_numpy(np.int64), exit_ts(e)
        overl = np.array([bool(((b_lo <= h) & (l <= b_hi)).any()) for l, h in zip(lo, hi)], dtype=bool)
        subset = set(e["tid"][~overl])
        t = tests.get(f"{L.REPLICATION_SUBPOP}:{kind}", {})
        rows = t.get("rows", {}).get(t.get("decision_rung") or "rung1")
        rec = {"eth_event_trades": int(len(e)), "overlapping": int(overl.sum()), "subset": int(len(subset))}
        if rows is not None and len(rows):
            sub = rows[rows["tid"].isin(subset) & np.isfinite(rows["placebo"])]
            rec["subset_included"] = int(len(sub))
            rec["subset_mean_delta"] = float(sub["delta"].mean()) if len(sub) else None
        else:
            rec["subset_included"] = 0
            rec["subset_mean_delta"] = None
        out[kind] = rec
    return out


def stage_verdict(tests: dict, family: list, subsets: dict) -> dict:
    """Section 8: a kind is promoted only with an INFORMATIVE family test and an agreeing chento-ETH line."""
    out = {"created_utc": now_utc(), "promoted": [], "per_kind": {},
           "classifications": {k: t["classification"] for k, t in tests.items() if k in family}}
    for kind in L.FAMILY_CANDIDATES:
        informative = [k for k in family if k.endswith(f":{kind}") and tests[k]["classification"] == "INFORMATIVE"]
        note = {"informative_family_tests": informative}
        eth_key = f"{L.REPLICATION_SUBPOP}:{kind}"
        if informative:
            rung = tests[informative[0]]["decision_rung"]
            eth = tests.get(eth_key, {}).get("lines", {}).get(rung, {"n": 0})
            note["eth_line"] = {"rung": rung, **{k: eth.get(k) for k in ("n", "mean", "first_half", "second_half")}}
            sub = subsets.get(kind, {})
            note["eth_non_overlap_subset"] = sub
            line_ok = (eth.get("n", 0) >= L.MIN_EVENT_TRADES and eth.get("mean", 0) < 0
                       and eth.get("first_half", 0) < 0 and eth.get("second_half", 0) < 0)
            subset_ok = (sub.get("subset_included", 0) >= L.MIN_LINE_TRADES
                         and (sub.get("subset_mean_delta") or 0) < 0)
            if eth.get("n", 0) < L.MIN_EVENT_TRADES:
                note["replication"] = "unavailable"
            elif sub.get("subset_included", 0) < L.MIN_LINE_TRADES:
                note["replication"] = "replication unavailable (non-overlap subset)"
            else:
                note["replication"] = "agrees" if (line_ok and subset_ok) else "disagrees"
            if line_ok and subset_ok:
                out["promoted"].append(kind)
        else:
            note["replication"] = "not evaluated"
        out["per_kind"][kind] = note
    out["verdict"] = ("PROMOTED: " + ", ".join(out["promoted"])) if out["promoted"] else "NONE PROMOTED"
    out["permits"] = "nothing in production; a promotion permits only a separate stage 2 pre-registration"
    return out


def outcomes() -> dict:
    f0 = json.loads((RESULTS / "freeze_F0.json").read_text())
    changed = [f for f in FROZEN if M.sha256(HERE / f) != f0["files"][f]]
    if changed:
        raise SystemExit(f"changed since F0: {changed}")
    if (RESULTS / "report.json").exists():
        raise SystemExit("report.json exists; the outcome run already happened")

    dvol = L.load_dvol()
    subpops, _, _ = load_subpops()
    _, panels = load_panels(dvol)
    events, grids = prepare(subpops, panels, with_cv=True)

    tests, all_rows, overlays = {}, [], {}
    for name, tr in events.items():
        g, w = grids[name], window_of(name)
        axis = M.day_axis(tr["entry_day"])
        idx = M.block_indices(len(axis))
        for kind in L.KINDS:
            rung_name = rung_for(name, kind, f0)
            rung = next((r for r in L.RUNGS if r.name == rung_name), L.RUNGS[0])
            entry = {"decision_rung": rung_name, "lines": {}}
            specs = list(L.RUNGS) + list(L.SECONDARY) + list(L.robustness_specs(rung)) + [L.shared_spec(rung)]
            keep_rows = {}
            for spec in specs:
                rows = L.match(tr, g, kind, w, spec)
                keep_rows[spec.name] = rows
                entry["lines"][spec.name] = summarize_rows(rows, axis, idx)
                if spec.name == rung_name and len(rows):
                    rows = rows.copy()
                    rows.insert(0, "kind", kind)
                    rows.insert(0, "subpop", name)
                    all_rows.append(rows)
            entry["rows"] = keep_rows
            tests[f"{name}:{kind}"] = entry
        for kind in L.OVERLAY_KINDS:
            overlays[f"{name}:{kind}"] = L.summarize_overlay(L.overlay_rows(tr, g, kind), axis, idx)

    family = f0["family"]
    p_family = {k: tests[k]["lines"][tests[k]["decision_rung"]]["p_one_sided_less"] for k in family
                if tests[k]["lines"].get(tests[k]["decision_rung"], {}).get("n")}
    holm = M.holm_adjust(p_family) if p_family else {}
    for key, t in tests.items():
        name, kind = key.split(":", 1)
        rung_name = t["decision_rung"] or "rung1"
        line = t["lines"].get(rung_name, {"n": 0})
        ctrl_kind = L.DECISION_CONTROL.get(kind) or L.REPORTED_CONTROL.get(kind)
        ctrl = tests.get(f"{name}:{ctrl_kind}", {}).get("lines", {}).get(rung_name) if ctrl_kind else None
        shared = t["lines"].get(f"shared_{rung_name}")
        shared_ctrl = (tests.get(f"{name}:{ctrl_kind}", {}).get("lines", {}).get(f"shared_{rung_name}")
                       if ctrl_kind else None)
        rvol, rsess = t["lines"].get(f"rvol_{rung_name}"), t["lines"].get(f"rsession_{rung_name}")
        t["holm_p"] = holm.get(key)
        t["classification"] = L.classify(line, key in family, t["holm_p"], ctrl, shared, shared_ctrl, rvol, rsess)
        if ctrl_kind and shared and shared_ctrl and shared.get("n") and shared_ctrl.get("n"):
            t["shared_difference"] = L.boot_difference(t["rows"][f"shared_{rung_name}"],
                                                       tests[f"{name}:{ctrl_kind}"]["rows"][f"shared_{rung_name}"])

    subsets = eth_non_overlap(tests, events)
    verdict = stage_verdict(tests, family, subsets)
    for t in tests.values():
        t.pop("rows", None)
    events_df = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()
    events_df.to_csv(RESULTS / "events.csv.gz", index=False)
    cols = ["subpop", "tid", "asset", "direction", "entry_ts", "entry_day", "entry", "risk", "i0", "x", "kind",
            "exit_price"] + [f"{k}_first" for k in L.KINDS] + [f"{k}_minutes" for k in L.KINDS]
    pd.concat([tr.assign(subpop=n)[cols] for n, tr in events.items()], ignore_index=True).to_csv(
        RESULTS / "trades.csv.gz", index=False)
    M.write_json(RESULTS / "report.json", {"created_utc": now_utc(), "f0_created_utc": f0["created_utc"],
                                           "family": family, "holm_p": holm, "tests": tests,
                                           "overlay": overlays, "eth_non_overlap": subsets})
    M.write_json(RESULTS / "verdict.json", verdict)
    print(json.dumps(verdict, indent=1)[:4000], flush=True)
    return verdict


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["checks", "freeze0", "outcomes"])
    args = ap.parse_args()
    {"checks": checks, "freeze0": freeze0, "outcomes": outcomes}[args.stage]()

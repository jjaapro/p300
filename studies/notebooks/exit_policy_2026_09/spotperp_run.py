"""Spot-vs-perp stage: preconditions, the freeze, and the one outcome run.

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\spotperp_run.py checks
    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\spotperp_run.py freeze0
    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\spotperp_run.py outcomes

`checks` computes counts only and never a continuation value at an event (PREREGISTRATION_SPOT_PERP.md section 9).
`freeze0` refuses unless the checks passed and never overwrites a freeze. `outcomes` refuses unless every frozen hash
still holds and never reruns. Results land in results/spot_perp/.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import micro_lib as M  # noqa: E402
import micro_run as MR  # noqa: E402
import spotperp_data as SD  # noqa: E402
import spotperp_lib as L  # noqa: E402

RESULTS = L.RESULTS
CACHE = L.CACHE
RAW = SD.RAW
HORIZON_MIN = {p: h * 60 for p, h in M.HORIZON_H.items()}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- inputs -----------------------------------------------------------------------------------------

def load_panels(cut: dict | None = None) -> tuple[dict, dict]:
    """The per-minute panels of both assets. `cut` truncates them for the causality check (P10)."""
    raws = {a: M.load_raw(a) for a in ("BTC", "ETH")}
    panels = {}
    for asset in ("BTC", "ETH"):
        sym = L.SYMBOL[asset]
        spot = _npz(CACHE / f"{sym}_spot_1m_panel.npz")
        prem = _npz(CACHE / f"{sym}_premium_1m_panel.npz")["close"]
        oi_5m = m_t0 = fdusd = None
        if asset == "BTC":
            with np.load(CACHE / "BTCUSDT_metrics_5m.npz") as z:
                oi_5m, m_t0 = z["oi"].astype(float), int(z["t0_s"][0])
            fdusd = _npz(CACHE / "BTCFDUSD_spot_1m_panel.npz")
        n = cut.get(asset) if cut else None
        panels[asset] = L.build_panel(asset, raws[asset], spot, prem, oi_5m, m_t0, fdusd,
                                      n_minutes=n, cut_close_s=(raws[asset]["t0_s"] + 60 * n if n else None))
    return raws, panels


def _npz(path: Path) -> dict:
    with np.load(path) as z:
        return {k: z[k].astype(float) if k != "t0_s" else z[k] for k in z.files}


def load_subpops():
    """Stage 1's three populations, walked by its walker, with chento split by asset."""
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


def prepare(subpops: dict, panels: dict) -> tuple[dict, dict]:
    """First events and grids (no continuation values) for every subpopulation."""
    events, grids = {}, {}
    for name, tr in subpops.items():
        g = L.build_grids(tr, panels, with_cv=False)
        events[name] = L.first_events(tr, g)
        grids[name] = g
    return events, grids


def window_of(subpop: str) -> int:
    return M.window_minutes(L.POP_OF[subpop])


# --- counts (P11) -----------------------------------------------------------------------------------

def count_table(events: dict, grids: dict) -> dict:
    """Event and control counts per subpopulation, kind and placebo. No continuation value is computed."""
    out = {}
    for name, tr in events.items():
        w = window_of(name)
        g = grids[name]
        rows = {}
        for kind in L.KINDS:
            el = L.eligible(tr, kind)
            first = tr[f"{kind}_first"].to_numpy(np.int64)
            has = el & (first >= 0)
            entry = {"eligible_trades": int(el.sum()), "trades_with_event": int(has.sum())}
            if has.any():
                elapsed = (first[has] - tr["i0"].to_numpy(np.int64)[has]) / 60.0
                entry["median_elapsed_hours"] = float(np.median(elapsed))
                entry["event_minutes_total"] = int(tr[f"{kind}_minutes"].to_numpy()[has].sum())
            for spec in L.RUNGS + L.SECONDARY:
                entry[f"included_{spec.name}"] = L.included(L.match(tr, g, kind, w, spec))
            rows[kind] = entry
        out[name] = rows
    return out


# --- stages -----------------------------------------------------------------------------------------

def smoke() -> dict:
    """Counts only, for reading before the preconditions are written: can any candidate test decide anything?"""
    subpops, _, _ = load_subpops()
    _, panels = load_panels()
    events, grids = prepare(subpops, panels)
    kinds = ("F1_against", "F1_spot_confirmed", "F2_perp_led", "F2_spot_confirmed")
    summary = {}
    for name, tr in events.items():
        g, w = grids[name], window_of(name)
        line = {"trades": int(len(tr))}
        for kind in kinds:
            first = tr[f"{kind}_first"].to_numpy(np.int64)
            has = L.eligible(tr, kind) & (first >= 0)
            entry = {"event_trades": int(has.sum())}
            if has.sum():
                specs = list(L.RUNGS) + list(L.robustness_specs(L.RUNGS[0])) + [L.shared_spec(L.RUNGS[0])]
                for spec in specs:
                    entry[spec.name] = L.included(L.match(tr, g, kind, w, spec))
            line[kind] = entry
        summary[name] = line
        print(name, json.dumps(line), flush=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "smoke_counts.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


# --- preconditions ----------------------------------------------------------------------------------

CONCURRENT = ["cache/BTCUSDT_spot_1m.npz", "cache/ETHUSDT_spot_1m.npz", "cache/BTCUSDT_metrics_5m_full.npz",
              "cache/ETHUSDT_metrics_5m_full.npz", "data/raw/spot_1m/manifest.json"]
PANELS = ["BTCUSDT_spot_1m_panel", "ETHUSDT_spot_1m_panel", "BTCFDUSD_spot_1m_panel",
          "BTCUSDT_premium_1m_panel", "ETHUSDT_premium_1m_panel"]


def p1_inputs() -> dict:
    """Hashes: stage 1's inputs and frozen files, this stage's archive and panels, the concurrent study's files."""
    stage1 = json.loads((HERE / "results" / "microstructure" / "freeze_F0.json").read_text())
    out = {"stage1_inputs_match": MR.input_hashes() == stage1["inputs_sha256"], "stage1_files": {}}
    for rel, want in stage1["files"].items():
        got = M.sha256(M.ROOT / rel)
        out["stage1_files"][rel] = {"match": got == want}
    manifest = json.loads((RAW / "manifest.json").read_text())
    bad = [e["file"] for e in manifest["files"]
           if SD.AD._sha256((HERE / e["file"]).read_bytes()) != e["sha256"]]
    out["archive"] = {"files": len(manifest["files"]), "hash_mismatches": bad,
                      "manifest_sha256": M.sha256(RAW / "manifest.json"),
                      "format_scan_sha256": M.sha256(RAW / "format_scan.json")}
    out["panels"] = {}
    for name in PANELS:
        meta = json.loads((CACHE / f"{name}.meta.json").read_text())
        out["panels"][name] = {"logical_sha256": meta["logical_sha256"], "file_sha256": meta["file_sha256"],
                               "file_sha256_now": SD.AD._sha256((CACHE / f"{name}.npz").read_bytes())}
        out["panels"][name]["match"] = out["panels"][name]["file_sha256"] == out["panels"][name]["file_sha256_now"]
    oi_meta = json.loads((CACHE / "BTCUSDT_metrics_5m.meta.json").read_text())
    out["oi_cache"] = {"logical_sha256": oi_meta["logical_sha256"],
                       "match": oi_meta["file_sha256"] == SD.AD._sha256((CACHE / "BTCUSDT_metrics_5m.npz").read_bytes())}
    out["micro_trades_sha256"] = M.sha256(HERE / "results" / "microstructure" / "trades.csv.gz")
    out["concurrent_study_present_not_read"] = {
        rel: (SD.AD._sha256((HERE / rel).read_bytes()) if (HERE / rel).exists() else None) for rel in CONCURRENT}
    out["pass"] = (out["stage1_inputs_match"] and not bad and all(v["match"] for v in out["stage1_files"].values())
                   and all(v["match"] for v in out["panels"].values()) and out["oi_cache"]["match"])
    return out


def p2_populations(subpops: dict, pops: dict) -> dict:
    m2 = MR.m2_identity(pops)
    counts = {k: int(len(v)) for k, v in subpops.items()}
    directions = {k: v["direction"].value_counts().to_dict() for k, v in subpops.items()}
    ok = counts["chento_BTC"] == 208 and counts["chento_ETH"] == 184 and counts["squeeze_bull"] == 122 \
        and counts["short_squeeze"] == 71
    return {"stage1_m2": m2, "counts": counts, "directions": directions, "pass": bool(m2["pass"] and ok)}


def p3_venue(pops: dict, series: dict) -> dict:
    return MR.m3_venue(pops, series)


def p4_walker(subpops: dict) -> dict:
    """Every walked trade equals stage 1's saved row."""
    saved = pd.read_csv(HERE / "results" / "microstructure" / "trades.csv.gz").set_index("tid")
    mine = pd.concat(subpops.values(), ignore_index=True).set_index("tid")
    shared = [c for c in ("i0", "x", "kind", "x_notime", "kind_notime", "level_valid") if c in saved]
    diffs = {}
    for col in shared:
        a, b = mine[col].reindex(saved.index), saved[col]
        bad = a.astype(str) != b.astype(str)
        diffs[col] = int(bad.sum())
    for col in ("exit_price", "exit_price_notime"):
        a, b = mine[col].reindex(saved.index).to_numpy(float), saved[col].to_numpy(float)
        diffs[col] = int((np.abs(a - b) > 1e-9 * np.abs(b)).sum())
    return {"trades": int(len(saved)), "differences": diffs, "pass": bool(len(mine) == len(saved)
                                                                         and not any(diffs.values()))}


def _lag_table(a: np.ndarray, b: np.ndarray, lags=range(-3, 4)) -> dict:
    """Correlation of `a` against `b` shifted by each lag, on the minutes finite in both."""
    out = {}
    for lag in lags:
        x = a[max(0, -lag):len(a) - max(0, lag)]
        y = b[max(0, lag):len(b) - max(0, -lag)]
        ok = np.isfinite(x) & np.isfinite(y)
        out[str(lag)] = float(np.corrcoef(x[ok], y[ok])[0, 1]) if ok.sum() > 100 else None
    return out


DECIDED_FROM_YEAR = 2021          # the earliest year any population can read (amendment A26)


def p5_alignment(panels: dict, raws: dict) -> dict:
    """Spot against perp and the premium against the perp-over-spot ratio, both on differences (A26).

    Levels must not be used: two autocorrelated level series correlate about 0.92 at every lag, so the peak is noise.
    The decision covers the asset-years the populations can read, 2021 onward; 2020 is reported.
    """
    out, ok = {}, True
    for asset, p in panels.items():
        sym = L.SYMBOL[asset]
        spot_close = _npz(CACHE / f"{sym}_spot_1m_panel.npz")["close"][:p.n]
        perp_close = p.ser.close
        with np.errstate(invalid="ignore", divide="ignore"):
            rs = np.diff(np.log(spot_close), prepend=np.nan)
            rp = np.diff(np.log(perp_close), prepend=np.nan)
            ratio = perp_close / spot_close - 1.0
        d_prem, d_ratio = np.diff(p.prem, prepend=np.nan), np.diff(ratio, prepend=np.nan)
        for year, (lo, hi) in SD.year_slices(p.t0_s, p.n).items():
            lines = {"returns": _lag_table(rs[lo:hi], rp[lo:hi]),
                     "premium": _lag_table(d_prem[lo:hi], d_ratio[lo:hi]),
                     "premium_levels_reported": _lag_table(p.prem[lo:hi], ratio[lo:hi])}
            for label, table in lines.items():
                vals = {int(k): v for k, v in table.items() if v is not None}
                if not vals:
                    continue
                peak = max(vals, key=lambda k: vals[k])
                decided = label != "premium_levels_reported" and int(year) >= DECIDED_FROM_YEAR
                out[f"{asset}:{year}:{label}"] = {"peak_lag": peak, "at_0": vals.get(0), "decided": decided,
                                                 "table": table}
                if decided:
                    ok = ok and peak == 0 and (label != "premium" or vals.get(0, -1) > 0)
    return {"lags": out, "pass": bool(ok), "decided_from_year": DECIDED_FROM_YEAR,
            "note": "A26: the premium half is computed on differences; levels are reported only; 2020 is reported "
                    "only, because no population reads it"}


def p6_price_sanity(panels: dict) -> dict:
    """Report only: how far spot and perp can be apart, per asset and year, in bp."""
    out = {}
    for asset, p in panels.items():
        for sym in ([L.SYMBOL[asset]] + (["BTCFDUSD"] if asset == "BTC" else [])):
            close = _npz(CACHE / f"{sym}_spot_1m_panel.npz")["close"][:p.n]
            with np.errstate(invalid="ignore", divide="ignore"):
                d = np.abs(close / p.ser.close - 1.0) * 1e4
            for year, (lo, hi) in SD.year_slices(p.t0_s, p.n).items():
                seg = d[lo:hi][np.isfinite(d[lo:hi])]
                if len(seg):
                    out[f"{sym}:{year}"] = {"median_bp": float(np.median(seg)), "p99_bp": float(np.quantile(seg, 0.99)),
                                            "max_bp": float(seg.max()), "minutes": int(len(seg))}
    return {"per_asset_year": out, "pass": True, "note": "report only, no threshold; alignment is decided by P5"}


def p7_build_facts() -> dict:
    out, ok = {}, True
    for name in PANELS:
        meta = json.loads((CACHE / f"{name}.meta.json").read_text())
        entry = {k: meta[k] for k in ("rows_read", "files", "totals", "minutes_present", "minutes_missing",
                                      "unit_switch_first_us_file", "by_year")}
        entry["minutes_dead"] = meta.get("minutes_dead")
        entry["identity_failures"] = meta["totals"]["identity_failures"]
        entry["identity_failure_rows"] = meta["identity_failure_rows"]
        entry["grid_matches_perp"] = meta["grid"]["t0_s"] == SD.T0_S and meta["grid"]["minutes"] == SD.N_MINUTES
        if "premium_overlap_vs_anatomy_cache" in meta:
            entry["premium_overlap"] = meta["premium_overlap_vs_anatomy_cache"]
            ok = ok and meta["premium_overlap_vs_anatomy_cache"]["identical"]
        if "first_present_utc" in meta:
            entry["first_present_utc"] = meta["first_present_utc"]
            entry["all_nan_before"] = meta["all_nan_before_first_present"]
            ok = ok and meta["all_nan_before_first_present"] and meta["first_present_utc"] == SD.FDUSD_FIRST_UTC
        ok = ok and entry["grid_matches_perp"] and meta["totals"]["conflicting_duplicates"] == 0
        out[name] = entry
    return {"panels": out, "pass": bool(ok)}


def p8_coverage(events: dict, grids: dict) -> dict:
    out, testable = {}, {}
    for name, tr in events.items():
        g = grids[name]
        denom = g.open_ & g.gate
        line = {}
        for family in ("f1", "f2", "comp", "oi"):
            total = int(denom.sum())
            have = int((denom & g.ok[family]).sum())
            line[family] = {"minutes": total, "with_inputs": have,
                            "share": round(have / total, 4) if total else None}
        out[name] = line
        testable[name] = {f: bool(line[f]["share"] is not None and line[f]["share"] >= M.BOOK_COVERAGE_MIN)
                          for f in ("f1", "f2")}
    return {"per_subpop": out, "testable": testable, "pass": True,
            "note": "coverage below 0.90 makes that kind DESCRIPTIVE; it does not stop the stage"}


def p9_fixtures() -> dict:
    import subprocess
    out = {}
    for name in ("test_spotperp_events.py", "test_spotperp_data.py", "test_micro_events.py", "test_anatomy.py"):
        r = subprocess.run([sys.executable, "-m", "pytest", str(HERE / "tests" / name), "-q"],
                           capture_output=True, text=True, cwd=str(M.ROOT))
        out[name] = {"returncode": r.returncode, "tail": r.stdout.strip().splitlines()[-1] if r.stdout else ""}
    return {"suites": out, "pass": all(v["returncode"] == 0 for v in out.values())}


def p10_causality(subpops: dict, panels: dict, raws: dict) -> dict:
    """Masking every minute after a cut must leave every input at or before the cut unchanged."""
    rng = np.random.default_rng(M.SEED)
    out, ok = {}, True
    fields = ("DP60", "DS60", "zS", "dprem", "pt", "vr", "doi", "ot", "DSplus60")
    for asset in ("BTC", "ETH"):
        pool = pd.concat([tr for tr in subpops.values()], ignore_index=True)
        pool = pool[pool["asset"] == asset]
        picks = rng.choice(len(pool), size=min(5, len(pool)), replace=False)
        for pick in picks:
            row = pool.iloc[int(pick)]
            cut = int(row["i0"] + (row["x"] - row["i0"]) // 2)
            _, cut_panels = load_panels({asset: cut + 1})
            full, part = panels[asset], cut_panels[asset]
            diffs = {}
            for f in fields:
                a, b = getattr(full, f), getattr(part, f)
                if a is None or b is None:
                    continue
                a, b = a[:cut + 1], b[:cut + 1]
                diffs[f] = int((~((a == b) | (np.isnan(a) & np.isnan(b)))).sum())
            zp = int((~((full.zP[:cut + 1] == part.zP[:cut + 1])
                        | (np.isnan(full.zP[:cut + 1]) & np.isnan(part.zP[:cut + 1])))).sum())
            diffs["zP"] = zp
            out[f"{asset}:{cut}"] = {"cut_utc": SD.iso(full.t0_s + 60 * cut), "differences": diffs}
            ok = ok and not any(diffs.values())
    return {"cuts": out, "pass": bool(ok)}


def p11_counts(events: dict, grids: dict) -> dict:
    """Every count the family is fixed on. No continuation value is computed here."""
    specs = list(L.RUNGS) + list(L.SECONDARY)
    per, family = {}, []
    for name, tr in events.items():
        g, w = grids[name], window_of(name)
        rows = {}
        for kind in L.KINDS:
            el = L.eligible(tr, kind)
            first = tr[f"{kind}_first"].to_numpy(np.int64)
            has = el & (first >= 0)
            entry = {"eligible_trades": int(el.sum()), "trades_with_event": int(has.sum()),
                     "event_share": round(float(has.sum()) / max(1, int(el.sum())), 4)}
            if has.any():
                elapsed = (first[has] - tr["i0"].to_numpy(np.int64)[has]) / 60.0
                entry["median_elapsed_hours"] = float(np.median(elapsed))
                entry["median_elapsed_share_of_horizon"] = float(np.median(elapsed * 60 / tr["horizon_min"].iat[0]))
            use = list(specs)
            if kind in L.FAMILY_CANDIDATES or kind in L.DECISION_CONTROL.values():
                use += list(L.robustness_specs(L.RUNGS[0])) + [L.shared_spec(r) for r in L.RUNGS]
            for spec in use:
                m = L.match(tr, g, kind, w, spec)
                entry[f"included_{spec.name}"] = L.included(m)
                if spec.name == "rung1" and len(m):
                    entry["dropped_for_controls"] = int((m["controls"] < L.MIN_CONTROLS).sum())
                    entry["mean_controls"] = float(m["controls"].mean())
            entry["decision_rung"] = L.decision_rung({s.name: entry.get(f"included_{s.name}", 0) for s in L.RUNGS})
            rows[kind] = entry
        per[name] = rows
    for sub in L.FAMILY_SUBPOPS:
        for kind in L.FAMILY_CANDIDATES:
            if per[sub][kind]["decision_rung"]:
                family.append(f"{sub}:{kind}")
    return {"per_subpop": per, "family": family, "pass": True}


def checks() -> dict:
    subpops, series, pops = load_subpops()
    raws, panels = load_panels()
    events, grids = prepare(subpops, panels)
    out = {"created_utc": now_utc(), "P1": p1_inputs(), "P2": p2_populations(subpops, pops),
           "P3": p3_venue(pops, series), "P4": p4_walker(subpops), "P5": p5_alignment(panels, raws),
           "P6": p6_price_sanity(panels), "P7": p7_build_facts(), "P8": p8_coverage(events, grids),
           "P9": p9_fixtures(), "P10": p10_causality(subpops, panels, raws),
           "P11": p11_counts(events, grids)}
    out["pass"] = all(out[k]["pass"] for k in ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10", "P11"))
    RESULTS.mkdir(parents=True, exist_ok=True)
    M.write_json(RESULTS / "preconditions.json", out)
    for k in ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10", "P11"):
        print(f"{k}: {'pass' if out[k]['pass'] else 'FAIL'}", flush=True)
    print("family:", out["P11"]["family"], flush=True)
    return out


# --- freeze -----------------------------------------------------------------------------------------

FROZEN = ["PREREGISTRATION_SPOT_PERP.md", "spotperp_data.py", "spotperp_lib.py", "spotperp_run.py",
          "spotperp_download.py", "tests/test_spotperp_events.py", "tests/test_spotperp_data.py",
          "results/spot_perp/preconditions.json"]
FROZEN_CACHE = [f"cache/{n}.meta.json" for n in PANELS]


def freeze0() -> Path:
    pre = json.loads((RESULTS / "preconditions.json").read_text())
    if not pre["pass"]:
        raise SystemExit("preconditions did not pass; nothing is frozen")
    target = RESULTS / "freeze_F0.json"
    if target.exists():
        raise SystemExit("freeze_F0.json exists; a freeze is never overwritten")
    stage1 = json.loads((HERE / "results" / "microstructure" / "freeze_F0.json").read_text())
    M.write_json(target, {
        "freeze": "F0", "created_utc": now_utc(),
        "note": "Before any continuation value at an event. The checks computed event minutes and placebo control "
                "counts only; a counts-only smoke pass (results/spot_perp/smoke_counts.json) was read first and its "
                "numbers are P11's. No threshold was changed after reading them.",
        "family": pre["P11"]["family"],
        "decision_rungs": {f"{s}:{k}": pre["P11"]["per_subpop"][s][k]["decision_rung"]
                           for s in L.SUBPOPS for k in L.FAMILY_CANDIDATES},
        "files": {f: M.sha256(HERE / f) for f in FROZEN + FROZEN_CACHE},
        "stage1_inputs_sha256": MR.input_hashes(),
        "stage1_freeze_files": stage1["files"],
        "panels": {n: json.loads((CACHE / f"{n}.meta.json").read_text())["logical_sha256"] for n in PANELS},
    })
    print(f"frozen: {target}", flush=True)
    return target


# --- outcomes ---------------------------------------------------------------------------------------

def rung_for(subpop: str, kind: str, f0: dict) -> str:
    """Section 6: a control, sub-mask or replication line is read under the rung of the event test it accompanies."""
    anchor = "F2_perp_led" if kind.startswith("F2") else "F1_against"
    if kind in L.FAMILY_CANDIDATES:
        anchor = kind
    if subpop == L.REPLICATION_SUBPOP:                            # the ETH line follows the BTC test of its kind
        for candidate in L.FAMILY_SUBPOPS:
            rung = f0["decision_rungs"].get(f"{candidate}:{anchor}")
            if rung:
                return rung
    return f0["decision_rungs"].get(f"{subpop}:{anchor}") or "rung1"


def exit_ts(tr: pd.DataFrame) -> np.ndarray:
    return tr["entry_ts"].to_numpy(np.int64) + (tr["x"].to_numpy(np.int64) - tr["i0"].to_numpy(np.int64)) * 60


def summarize_rows(rows: pd.DataFrame, axis: list, idx: np.ndarray) -> dict:
    out = M.summarize(rows, "delta", axis, idx) if len(rows) else {"n": 0}
    if out.get("n"):
        inc = rows[np.isfinite(rows["placebo"])]
        out["mean_cv_at_event"] = float(inc["cv"].mean())
        out["mean_placebo"] = float(inc["placebo"].mean())
    return out


def outcomes() -> dict:
    f0 = json.loads((RESULTS / "freeze_F0.json").read_text())
    changed = [f for f in FROZEN + FROZEN_CACHE if M.sha256(HERE / f) != f0["files"][f]]
    if changed:
        raise SystemExit(f"changed since F0: {changed}")
    if (RESULTS / "report.json").exists():
        raise SystemExit("report.json exists; the outcome run already happened")

    subpops, _, _ = load_subpops()
    _, panels = load_panels()
    events = {}
    grids = {}
    for name, tr in subpops.items():
        g = L.build_grids(tr, panels, with_cv=True)
        events[name] = L.first_events(tr, g)
        grids[name] = g

    tests, all_rows = {}, []
    for name, tr in events.items():
        g, w = grids[name], window_of(name)
        axis = M.day_axis(tr["entry_day"])
        idx = M.block_indices(len(axis))
        for kind in L.KINDS:
            rung_name = rung_for(name, kind, f0)                  # every line uses its event test's rung
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

    # the family, Holm and the labels
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
    verdict = stage_verdict(tests, events, family, subsets)
    for t in tests.values():
        t.pop("rows", None)
    events_df = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()
    events_df.to_csv(RESULTS / "events.csv.gz", index=False)
    cols = ["subpop", "tid", "asset", "direction", "entry_ts", "entry_day", "entry", "risk", "i0", "x", "kind",
            "exit_price"] + [f"{k}_first" for k in L.KINDS] + [f"{k}_minutes" for k in L.KINDS]
    pd.concat([tr.assign(subpop=n)[cols] for n, tr in events.items()], ignore_index=True).to_csv(
        RESULTS / "trades.csv.gz", index=False)
    M.write_json(RESULTS / "report.json", {"created_utc": now_utc(), "f0_created_utc": f0["created_utc"],
                                           "family": family, "holm_p": holm, "tests": tests})
    M.write_json(RESULTS / "verdict.json", verdict)
    print(json.dumps(verdict, indent=1)[:4000], flush=True)
    return verdict


def eth_non_overlap(tests: dict, events: dict) -> dict:
    """Per kind: the chento-ETH event trades whose interval shares no minute with a chento-BTC event trade's."""
    out = {}
    btc, eth = events["chento_BTC"], events["chento_ETH"]
    btc_lo, btc_hi = btc["entry_ts"].to_numpy(np.int64), exit_ts(btc)
    eth_lo, eth_hi = eth["entry_ts"].to_numpy(np.int64), exit_ts(eth)
    for kind in L.FAMILY_CANDIDATES:
        has_btc = btc[f"{kind}_first"].to_numpy(np.int64) >= 0
        lo, hi = btc_lo[has_btc], btc_hi[has_btc]
        overlaps = {}
        for j, tid in enumerate(eth["tid"]):
            overlaps[tid] = bool(np.any((lo <= eth_hi[j]) & (eth_lo[j] <= hi)))
        key = f"chento_ETH:{kind}"
        rung = tests.get(key, {}).get("decision_rung") or "rung1"
        rows = tests.get(key, {}).get("rows", {}).get(rung)
        entry = {"btc_event_trades": int(has_btc.sum()),
                 "eth_event_trades_overlapping": int(sum(overlaps.get(t, False) for t in
                                                         (rows["tid"] if rows is not None and len(rows) else [])))}
        if rows is not None and len(rows):
            sub = rows[[not overlaps.get(t, False) for t in rows["tid"]]]
            inc = sub[np.isfinite(sub["placebo"])] if len(sub) else sub
            entry["subset_included"] = int(len(inc))
            entry["subset_mean_delta"] = float(inc["delta"].mean()) if len(inc) else None
        out[kind] = entry
    return out


def stage_verdict(tests: dict, events: dict, family: list, subsets: dict) -> dict:
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


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["smoke", "checks", "freeze0", "outcomes"])
    args = ap.parse_args()
    {"smoke": smoke, "checks": checks, "freeze0": freeze0, "outcomes": outcomes}[args.stage]()

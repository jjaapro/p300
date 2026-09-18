"""Microstructure stage 1: preconditions, freeze, one outcome run (PREREGISTRATION_MICROSTRUCTURE.md).

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\micro_run.py checks
    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\micro_run.py freeze0
    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\micro_run.py outcomes

`checks` never computes a continuation value at an event: its placebo counts use grids without exit prices, and its
power line samples one random open minute per trade. `compute_outcomes` is pure (series, populations, family) so the
notebook can recompute it and compare with the saved report.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import zipfile  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import micro_data as D  # noqa: E402
import micro_lib as M  # noqa: E402

ASSETS = ("BTC", "ETH")
INPUTS = {
    **{f"perp_1m_{a}": M.ORB_CACHE / f"{M.SYMBOL[a]}_perp_1m.npz" for a in ASSETS},
    **{f"book_1m_{a}": M.BOOK_CACHE / f"{M.SYMBOL[a]}_book1pct_1m.npz" for a in ASSETS},
    "book_manifest": M.BOOK_MANIFEST,
    **{f"chento_features_{a}": M.CHENTO_FEATURES[a] for a in ASSETS},
    "squeeze_bull_ledger": M.SQB_LEDGER,
    "short_squeeze_replay": M.SS_REPLAY,
    "chento_A0_walks": M.CHENTO_A0_WALKS,
    "squeeze_bull_walks": M.SQB_WALKS,
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def input_hashes() -> dict:
    return {k: M.sha256(p) for k, p in INPUTS.items()}


def load_all() -> tuple[dict, dict, dict]:
    raws = {a: M.load_raw(a) for a in ASSETS}
    assert raws["BTC"]["t0_s"] == raws["ETH"]["t0_s"], "both panels must share one minute grid"
    series = {a: M.build_series(a, raws[a]) for a in ASSETS}
    pops = {}
    for p in M.POPULATIONS:
        tr = M.walk_population(M.load_population(p), series)
        pops[p] = M.events_population(tr, series)
    return raws, series, pops


# --- preconditions ------------------------------------------------------------------------------------

def m1_inputs(hashes: dict) -> dict:
    out = {"inputs_sha256": hashes}
    for a in ASSETS:
        meta = json.loads((M.ORB_CACHE / f"{M.SYMBOL[a]}_perp_1m.meta.json").read_text())
        out[f"perp_1m_{a}_matches_meta"] = hashes[f"perp_1m_{a}"] == meta["file_sha256"]
        out[f"perp_1m_{a}_logical_sha256"] = meta["logical_sha256"]
        bmeta = json.loads((M.BOOK_CACHE / f"{M.SYMBOL[a]}_book1pct_1m.meta.json").read_text())
        out[f"book_1m_{a}_matches_meta"] = hashes[f"book_1m_{a}"] == bmeta["file_sha256"]
        out[f"book_1m_{a}_logical_sha256"] = bmeta["logical_sha256"]
        out[f"chento_features_{a}_frozen"] = hashes[f"chento_features_{a}"] == M.CHENTO_FEATURES_SHA256[a]
    out["squeeze_bull_ledger_frozen"] = hashes["squeeze_bull_ledger"] == M.SQB_LEDGER_SHA256
    rel = M.SS_REPLAY.relative_to(M.ROOT).as_posix()
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=M.ROOT, capture_output=True).returncode == 0
    clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], cwd=M.ROOT).returncode == 0
    out["short_squeeze_replay_committed_unchanged"] = tracked and clean
    manifest = json.loads(M.BOOK_MANIFEST.read_text())
    parsed = [e for e in manifest["files"] if e["status"] != "missing_in_archive"]
    bad = [e["file"] for e in parsed if not e["checksum_ok"] or M.sha256(M.HERE / e["file"]) != e["sha256"]]
    out["book_zips"] = {"verified": len(parsed) - len(bad), "failed": bad,
                        "missing_in_archive": [(e["symbol"], e["day"]) for e in manifest["files"]
                                               if e["status"] == "missing_in_archive"]}
    out["book_build_spot_check"] = book_spot_check(manifest)
    flags = [v for k, v in out.items() if isinstance(v, bool)]
    out["pass"] = all(flags) and not bad and out["book_build_spot_check"]["mismatches"] == 0
    return out


def book_spot_check(manifest: dict, per_asset: int = 5) -> dict:
    """Re-parse sample days independently (pandas groupby, last snapshot per minute) and compare with the built arrays."""
    rng = np.random.default_rng(M.SEED)
    days, mismatches = [], 0
    for a in ASSETS:
        sym = M.SYMBOL[a]
        with np.load(M.BOOK_CACHE / f"{sym}_book1pct_1m.npz") as z:
            t0, bid, ask = int(z["t0_s"][0]), z["bid_notional_1pct"], z["ask_notional_1pct"]
        files = [e for e in manifest["files"] if e["symbol"] == sym and e["status"] != "missing_in_archive"]
        for k in rng.choice(len(files), size=per_asset, replace=False):
            e = files[int(k)]
            with zipfile.ZipFile(M.HERE / e["file"]) as zf:
                f = pd.read_csv(io.BytesIO(zf.read(zf.namelist()[0])))
            f = f[np.isclose(f["percentage"].astype(float).abs(), 1.0)].copy()
            f["ts_s"] = [int(pd.Timestamp(x, tz="UTC").timestamp()) for x in f["timestamp"]]
            f["side"] = np.where(f["percentage"].astype(float) < 0, "bid", "ask")
            w = f.pivot_table(index="ts_s", columns="side", values="notional", aggfunc="last").dropna().sort_index()
            w["minute"] = (w.index.to_numpy() - t0) // 60
            last = w.groupby("minute").last()
            day0 = (int(pd.Timestamp(e["day"], tz="UTC").timestamp()) - t0) // 60
            mins = np.arange(day0, day0 + 1440)
            want_b = last["bid"].reindex(mins).to_numpy()
            want_a = last["ask"].reindex(mins).to_numpy()
            bad = int((~np.isclose(bid[mins], want_b, rtol=0, atol=0, equal_nan=True)).sum()
                      + (~np.isclose(ask[mins], want_a, rtol=0, atol=0, equal_nan=True)).sum())
            mismatches += bad
            days.append({"symbol": sym, "day": e["day"], "mismatched_values": bad})
    return {"days": days, "mismatches": mismatches}


def m2_identity(pops: dict) -> dict:
    a0 = pd.read_csv(M.CHENTO_A0_WALKS)
    a0 = a0[a0["arm"] == "A0"]
    want = set(zip(a0["asset"], a0["t"].astype(int), a0["direction"]))
    ch = pops["chento"]
    have = set(zip(ch["asset"], ch["signal_ts"].astype(int), ch["direction"]))
    sb = pd.read_csv(M.SQB_WALKS)
    s0 = set(sb.loc[sb["arm"] == "S0", "bar_ts"].astype(int))
    ss = pops["short_squeeze"]
    hours = pd.to_datetime(ss["signal_ts"], unit="s", utc=True).dt.hour
    out = {"chento": {"trades": len(ch), "equals_A0": have == want, "A0_rows": len(want)},
           "squeeze_bull": {"trades": len(pops["squeeze_bull"]),
                            "equals_S0": set(pops["squeeze_bull"]["signal_ts"].astype(int)) == s0},
           "short_squeeze": {"trades": len(ss), "distinct_triggers": int(ss["signal_ts"].nunique()),
                             "all_london_ny": bool(((hours >= 7) & (hours < 21)).all())}}
    out["pass"] = (out["chento"]["equals_A0"] and len(ch) == 392 and out["squeeze_bull"]["equals_S0"]
                   and out["squeeze_bull"]["trades"] == 122 and len(ss) == 71 and out["short_squeeze"]["distinct_triggers"] == 71
                   and out["short_squeeze"]["all_london_ny"])
    return out


def m3_venue(pops: dict, series: dict) -> dict:
    out = {}
    for p, tr in pops.items():
        rel = [abs(series[t.asset].close[t.i0 - 1] - t.entry) / t.entry for t in tr.itertuples(index=False)]
        out[p] = {"trades": len(rel), "max_rel_diff": float(np.max(rel))}
    out["pass"] = all(v["max_rel_diff"] <= 1e-9 for v in out.values())
    return out


def m4_walker(pops: dict, series: dict) -> dict:
    sb = pd.read_csv(M.SQB_WALKS)
    s0 = sb[sb["arm"] == "S0"].set_index("bar_ts")
    tr = pops["squeeze_bull"]
    same = 0
    for t in tr.itertuples(index=False):
        r = s0.loc[int(t.signal_ts)]
        same += (t.kind == r["kind"] and series["BTC"].time_s(t.x) == int(r["exit_s"])
                 and abs(t.exit_price - float(r["exit_price"])) <= 1e-9 * t.entry)
    a0 = pd.read_csv(M.CHENTO_A0_WALKS)
    a0 = a0[a0["arm"] == "A0"].set_index(["asset", "t", "direction"])
    ch = pops["chento"]
    kind_15m = np.array([a0.loc[(t.asset, int(t.signal_ts), t.direction), "kind"] for t in ch.itertuples(index=False)])
    differ = ch["kind"].to_numpy() != kind_15m
    ch_diff = pd.DataFrame({"tid": ch["tid"].to_numpy()[differ], "kind_1m": ch["kind"].to_numpy()[differ],
                            "kind_15m": kind_15m[differ]})
    ss = pops["short_squeeze"]
    ss_diff = ss.loc[ss["kind"] != ss["replay_exit_reason"], ["tid", "kind", "replay_exit_reason"]]
    out = {"squeeze_bull_equal_to_S0": {"equal": int(same), "trades": len(tr)},
           "chento_kind_agreement": {"share": 1 - len(ch_diff) / len(ch), "differences": ch_diff.to_dict("records"),
                                     "mix_1m": ch["kind"].value_counts().to_dict()},
           "short_squeeze_kind_agreement": {"share": 1 - len(ss_diff) / len(ss), "differences": ss_diff.to_dict("records"),
                                            "mix_1m": ss["kind"].value_counts().to_dict()}}
    out["pass"] = (same == len(tr) and out["chento_kind_agreement"]["share"] >= 0.90
                   and out["short_squeeze_kind_agreement"]["share"] >= 0.80)
    return out


def m5_fixtures() -> dict:
    r = subprocess.run([sys.executable, "-m", "pytest", str(HERE / "tests" / "test_micro_events.py"), "-q",
                        "-p", "no:cacheprovider"], capture_output=True, text=True)
    return {"summary": r.stdout.strip().splitlines()[-1], "pass": r.returncode == 0}


def m6_book_coverage(pops: dict, series: dict) -> dict:
    out = {"by_asset_year": {}}
    for a in ASSETS:
        meta = json.loads((M.BOOK_CACHE / f"{M.SYMBOL[a]}_book1pct_1m.meta.json").read_text())
        out["by_asset_year"][a] = meta["minutes_with_snapshot_by_year"]
    out["in_trade"] = {}
    for p, tr in pops.items():
        el = tr[M.eligible(tr, "E3_against")]
        present = sum(int(series[t.asset].book_present[t.i0:t.x].sum()) for t in el.itertuples(index=False))
        total = int((el["x"] - el["i0"]).sum())
        share = present / total if total else float("nan")
        out["in_trade"][p] = {"eligible_trades": len(el), "minutes": total, "share_with_snapshot": share,
                              "e3_testable": bool(total and share >= M.BOOK_COVERAGE_MIN)}
    out["pass"] = True                                      # coverage below the bar makes E3 descriptive, not a failure
    return out


def m7_causality(raws: dict, series: dict, pops: dict, per_asset: int = 5) -> dict:
    rng = np.random.default_rng(M.SEED)
    cuts = []
    for a in ASSETS:
        trades = pd.concat([tr[tr["asset"] == a] for tr in pops.values()], ignore_index=True)
        trades = trades[trades["entry_ts"] >= M.BOOK_ELIGIBLE_FROM].reset_index(drop=True)
        for k in rng.choice(len(trades), size=per_asset, replace=False):
            t = trades.iloc[int(k)]
            cut = int(t["i0"] + (t["x"] - t["i0"]) // 2)
            short = M.build_series(a, raws[a], n_minutes=cut + 1)
            full = series[a]
            feat_diff = {name: int((~np.isclose(getattr(full, name)[:cut + 1], getattr(short, name), rtol=0, atol=0,
                                                equal_nan=True)).sum()) for name in ("z_flow", "dp5", "zq")}
            live = pd.concat([tr[(tr["asset"] == a) & (tr["i0"] <= cut) & (tr["x"] > cut)] for tr in pops.values()])
            event_diff = 0
            for u in live.itertuples(index=False):
                L_full, L_short = M.level_of(full, u.i0, u.s), M.level_of(short, u.i0, u.s)
                valid = M.level_valid(L_full, u.entry, u.s)
                level = L_full if valid else None
                mf = M.window_masks(full, u.i0, cut + 1, u.s, level)
                ms = M.window_masks(short, u.i0, cut + 1, u.s, level)
                event_diff += int(not np.array_equal([L_full], [L_short], equal_nan=True))
                event_diff += sum(int(not np.array_equal(mf[k2], ms[k2])) for k2 in mf)
                if valid:
                    event_diff += int(M.break_events(full, u.i0, cut + 1, u.s, L_full)
                                      != M.break_events(short, u.i0, cut + 1, u.s, L_short))
            cuts.append({"asset": a, "cut_minute": cut, "cut_utc": datetime.fromtimestamp(full.time_s(cut), tz=timezone.utc)
                         .isoformat(), "open_trades_at_cut": len(live), "feature_differences": feat_diff,
                         "event_differences": event_diff})
    ok = all(sum(c["feature_differences"].values()) == 0 and c["event_differences"] == 0 for c in cuts)
    return {"cuts": cuts, "pass": ok}


def random_minute_cv_sd(tr: pd.DataFrame, series: dict) -> float:
    """sd of the continuation value at one random open minute per trade (seed 42): the power line only."""
    rng = np.random.default_rng(M.SEED)
    values = []
    for t in tr.itertuples(index=False):
        c = series[t.asset].close[t.i0:t.x]
        open_minutes = np.flatnonzero(np.isfinite(c))
        if len(open_minutes) == 0:                          # stopped out inside its entry minute
            continue
        m = int(rng.choice(open_minutes))
        values.append(t.s * (t.exit_price - c[m]) / t.risk)
    return float(np.std(values, ddof=1))


def m8_counts(pops: dict, series: dict, coverage: dict) -> dict:
    out, family = {}, []
    for p, tr in pops.items():
        grids = M.build_grids(tr, series, with_cv=False)
        sd = random_minute_cv_sd(tr, series)
        per = {"cv_sd_random_minute": sd, "tests": {}}
        for kind in M.KINDS:
            el = M.eligible(tr, kind)
            first = tr[f"{kind}_first"].to_numpy(np.int64)
            has = el & (first >= 0)
            elapsed_h = (first[has] - tr["i0"].to_numpy()[has]) / 60
            in_trade = int((tr["x"] - tr["i0"]).to_numpy()[el].sum())
            rows = M.match(tr, grids, kind, M.window_minutes(p))
            included = int((rows["controls"] >= M.MIN_CONTROLS).sum()) if len(rows) else 0
            per["tests"][kind] = {
                "eligible_trades": int(el.sum()), "trades_with_event": int(has.sum()),
                "share_with_event": float(has.sum() / el.sum()) if el.sum() else float("nan"),
                "median_elapsed_hours": float(np.median(elapsed_h)) if len(elapsed_h) else float("nan"),
                "median_elapsed_share_of_horizon": float(np.median(elapsed_h) / M.HORIZON_H[p]) if len(elapsed_h) else float("nan"),
                "event_minutes_per_24h_in_trade": float(tr[f"{kind}_minutes"].to_numpy()[el].sum() / in_trade * 1440) if in_trade else float("nan"),
                "included_with_min_controls": included,
                "included_time_only": int((rows["controls_time_only"] >= M.MIN_CONTROLS).sum()) if len(rows) else 0,
                "mde80_R": float(M.POWER_Z * sd / np.sqrt(included)) if included else float("nan")}
            if kind in M.PRIMARY:
                testable = included >= M.MIN_EVENT_TRADES and (not kind.startswith("E3") or coverage["in_trade"][p]["e3_testable"])
                per["tests"][kind]["in_family"] = bool(testable)
                if testable:
                    family.append(f"{p}:{kind}")
        out[p] = per
    return {"populations": out, "family": family, "pass": True}


def checks() -> dict:
    hashes = input_hashes()
    raws, series, pops = load_all()
    out = {"created_utc": now_utc(), "M1": m1_inputs(hashes), "M2": m2_identity(pops), "M3": m3_venue(pops, series),
           "M4": m4_walker(pops, series), "M5": m5_fixtures(), "M6": m6_book_coverage(pops, series)}
    out["M7"] = m7_causality(raws, series, pops)
    out["M8"] = m8_counts(pops, series, out["M6"])
    out["pass"] = all(out[m]["pass"] for m in ("M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8"))
    M.write_json(M.RESULTS / "preconditions.json", out)
    return out


# --- freeze and outcomes ------------------------------------------------------------------------------

FROZEN_FILES = ["PREREGISTRATION_MICROSTRUCTURE.md", "micro_data.py", "micro_lib.py", "micro_run.py",
                "tests/test_micro_events.py", "results/microstructure/preconditions.json",
                "cache/BTCUSDT_book1pct_1m.meta.json", "cache/ETHUSDT_book1pct_1m.meta.json"]


def freeze0() -> Path:
    pre = json.loads((M.RESULTS / "preconditions.json").read_text())
    if not pre["pass"]:
        raise SystemExit("preconditions did not pass; nothing is frozen")
    target = M.RESULTS / "freeze_F0.json"
    if target.exists():
        raise SystemExit("freeze_F0.json exists; a freeze is never overwritten")
    M.write_json(target, {
        "freeze": "F0", "created_utc": now_utc(),
        "note": "Before any continuation value at an event. The checks computed event minutes, placebo control counts "
                "(no exit prices) and the sd of the continuation value at one random open minute per trade. After the "
                "first checks run, a scratch diagnostic confirmed the near-zero short_squeeze counts are not a bug: on "
                "all BTC minutes |z| >= 3 holds in 0.90 % / 0.94 % (buy / sell side), E1 against (long) in 0.018 %, "
                "E1 supportive (long) in 0.027 %, zq <= -3 in 0.35 %, zq >= 3 in 0.26 %; inside short_squeeze trades "
                "(7,930 minutes) the median trade's largest |z| is 1.74. No threshold was changed.",
        "family": pre["M8"]["family"], "inputs_sha256": input_hashes(),
        "files": {(HERE / f).resolve().relative_to(M.ROOT).as_posix(): M.sha256(HERE / f) for f in FROZEN_FILES}})
    return target


def compute_outcomes(series: dict, pops: dict, family: list[str]) -> tuple[pd.DataFrame, dict]:
    all_rows, tests = [], {}
    for p, tr in pops.items():
        grids = M.build_grids(tr, series, with_cv=True)
        axis = M.day_axis(tr["entry_day"])
        idx = M.block_indices(len(axis))
        for kind in M.KINDS:
            rows = M.match(tr, grids, kind, M.window_minutes(p))
            if len(rows):
                rows["delta"] = rows["cv"] - rows["placebo"]
                rows["delta_notime"] = rows["cv_notime"] - rows["placebo_notime"]
                rows["delta_time_only"] = rows["cv"] - rows["placebo_time_only"]
            rows.insert(0, "kind", kind)
            rows.insert(0, "pop", p)
            all_rows.append(rows)
            inc = rows[np.isfinite(rows["placebo"])] if len(rows) else rows
            t = {"primary": kind in M.PRIMARY, "in_family": f"{p}:{kind}" in family,
                 "delta": M.summarize(rows, "delta", axis, idx) if len(rows) else {"n": 0},
                 "mean_cv_at_event": float(inc["cv"].mean()) if len(inc) else float("nan"),
                 "mean_placebo": float(inc["placebo"].mean()) if len(inc) else float("nan"),
                 "secondary_time_only": M.summarize(rows, "delta_time_only", axis, idx) if len(rows) else {"n": 0},
                 "secondary_no_time_exit": M.summarize(rows, "delta_notime", axis, idx) if len(rows) else {"n": 0}}
            if p == "chento" and len(inc):
                t["chento_by_asset_direction"] = {f"{a}_{d}": {"n": int(len(g)), "mean_delta": float(g["delta"].mean())}
                                                  for (a, d), g in inc.groupby(["asset", "direction"])}
            tests[f"{p}:{kind}"] = t
    p_family = {k: tests[k]["delta"]["p_one_sided_less"] for k in family}
    holm = M.holm_adjust(p_family) if p_family else {}
    promoted = []
    for key, t in tests.items():
        p, kind = key.split(":")
        if not t["primary"]:
            continue
        control = tests[f"{p}:{M.SIGN_CONTROL[kind]}"]["delta"] if kind in M.SIGN_CONTROL else None
        t["holm_p"] = holm.get(key)
        t["classification"] = M.classify(t["delta"], t["in_family"], t["holm_p"], control, kind) \
            if t["delta"]["n"] else "DESCRIPTIVE"
        if t["classification"].startswith("INFORMATIVE"):
            promoted.append(key)
    events = pd.concat(all_rows, ignore_index=True)
    return events, {"tests": tests, "family": family, "holm_p": holm, "promoted": promoted}


def outcomes() -> dict:
    f0 = json.loads((M.RESULTS / "freeze_F0.json").read_text())
    changed = [f for f, h in f0["files"].items() if M.sha256(M.ROOT / f) != h]
    if changed or input_hashes() != f0["inputs_sha256"]:
        raise SystemExit(f"changed since F0: {changed or 'inputs'}")
    if (M.RESULTS / "report.json").exists():
        raise SystemExit("report.json exists; the outcome run already happened")
    pre = json.loads((M.RESULTS / "preconditions.json").read_text())
    _, series, pops = load_all()
    events, report = compute_outcomes(series, pops, f0["family"])
    for key, t in report["tests"].items():                  # the counts must be the ones the family was fixed on
        p, kind = key.split(":")
        want = pre["M8"]["populations"][p]["tests"][kind]["included_with_min_controls"]
        if t["delta"]["n"] != want:
            raise SystemExit(f"{key}: {t['delta']['n']} included trades, preconditions counted {want}")
    events.to_csv(M.RESULTS / "events.csv.gz", index=False)
    trade_cols = ["pop", "tid", "asset", "direction", "entry_ts", "entry_day", "entry", "risk", "stop", "target", "i0",
                  "x", "kind", "exit_price", "x_notime", "kind_notime", "exit_price_notime", "level", "level_valid"] + \
                 [f"{k}_first" for k in M.KINDS] + [f"{k}_minutes" for k in M.KINDS]
    pd.concat([tr[trade_cols] for tr in pops.values()], ignore_index=True).to_csv(M.RESULTS / "trades.csv.gz", index=False)
    M.write_json(M.RESULTS / "report.json", {"created_utc": now_utc(), "f0_created_utc": f0["created_utc"], **report,
                                             "events_sha256": M.sha256(M.RESULTS / "events.csv.gz"),
                                             "trades_sha256": M.sha256(M.RESULTS / "trades.csv.gz")})
    verdict = {"created_utc": now_utc(), "f0_created_utc": f0["created_utc"],
               "verdict": "PROMOTED: " + ", ".join(report["promoted"]) if report["promoted"] else "NONE PROMOTED",
               "classifications": {k: t["classification"] for k, t in report["tests"].items() if t["primary"]},
               "family": report["family"], "holm_p": report["holm_p"],
               "permits": "nothing in production; a promotion permits only a separate stage 2 pre-registration"}
    M.write_json(M.RESULTS / "verdict.json", verdict)
    return verdict


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["checks", "freeze0", "outcomes"])
    stage = ap.parse_args().stage
    res = {"checks": checks, "freeze0": freeze0, "outcomes": outcomes}[stage]()
    print(json.dumps(res if isinstance(res, dict) else str(res), indent=1, default=str)[:6000])

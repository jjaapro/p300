"""ORB study stages, in the only order the pre-registration allows.

    venv\\Scripts\\python.exe studies\\notebooks\\orb_study\\orb_run.py freeze0
    venv\\Scripts\\python.exe studies\\notebooks\\orb_study\\orb_run.py development   # writes F1
    venv\\Scripts\\python.exe studies\\notebooks\\orb_study\\orb_run.py validation    # writes F2
    venv\\Scripts\\python.exe studies\\notebooks\\orb_study\\orb_run.py lockbox       # writes the verdict

Each stage refuses to run unless the previous freeze exists and every file hashed into F0 is
unchanged, and refuses to overwrite its own freeze. Ledgers go to results/run_v1/<block>/<symbol>/,
summaries to results/run_v1/<block>/, and every evaluation is appended to trial_ledger.csv.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import orb_calendars as cal
import orb_controls as ctl
import orb_engine as eng
import orb_metrics as met
import orb_policies as pol
from orb_checks import RESULTS, load_market

STUDY = Path(__file__).resolve().parent
sys.path.insert(0, str(STUDY.parents[2]))                    # repository root, for studies.lib.validation
from studies.lib.validation.dsr_pbo import dsr_from_returns  # noqa: E402
from studies.lib.validation.stepm import stepm  # noqa: E402

RUN = RESULTS / "run_v1"
BLOCKS = {"development": ("2020-01-01", "2022-12-31"), "validation": ("2023-01-01", "2024-12-31"),
          "lockbox": ("2025-01-01", "2026-09-13")}
HURDLE_BP = 2.0
N_BOOT, BLOCK_DAYS, SEED = 10_000, 20, 42
SUMMARY_SCENARIOS = ("gross", "rt5", "rt10", "rt20", "rt30", "db", "db_nonfee_x2", "db_plus5", "db_plus10")


# --- freezes -------------------------------------------------------------------------------

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_files() -> list[Path]:
    files = [STUDY / n for n in ("PREREGISTRATION.md", "TEST_PLAN.md", "RESEARCH.md")]
    files += sorted(STUDY.glob("orb_*.py")) + sorted((STUDY / "tests").glob("test_*.py"))
    files += sorted((STUDY / "configs").glob("*")) + [STUDY / "data" / "raw" / "binance_um" / "manifest.json"]
    files += sorted((STUDY / "cache").glob("*.meta.json"))
    files += [RESULTS / "data_gate.json", RESULTS / "engine_checks.json"]
    return files


def _rel(p: Path) -> str:
    return p.relative_to(STUDY).as_posix()


def _write_freeze(name: str, payload: dict) -> Path:
    path = RESULTS / f"freeze_{name}.json"
    if path.exists():
        raise SystemExit(f"{path.name} already exists; a freeze is never overwritten")
    body = {"freeze": name, "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), **payload}
    path.write_text(json.dumps(body, indent=1, default=str), encoding="utf-8")
    return path


def _read_freeze(name: str) -> dict:
    path = RESULTS / f"freeze_{name}.json"
    if not path.exists():
        raise SystemExit(f"{path.name} missing: run the previous stage first")
    return json.loads(path.read_text(encoding="utf-8"))


def verify_f0() -> None:
    f0 = _read_freeze("F0")
    changed = [f for f, h in f0["files"].items() if not (STUDY / f).exists() or _sha(STUDY / f) != h]
    if changed:
        raise SystemExit(f"files changed since F0 (record an amendment first): {changed}")


def freeze0() -> Path:
    gate = json.loads((RESULTS / "data_gate.json").read_text())
    checks = json.loads((RESULTS / "engine_checks.json").read_text())
    if not (gate["gate_passed"] and checks["passed"]):
        raise SystemExit("data gate or engine checks did not pass; nothing is frozen")
    return _write_freeze("F0", {
        "note": "Before any ORB outcome. Rules, code, calendars, data manifest and checks.",
        "policy_registry_sha256": pol.registry_sha256(),
        "panel_logical_sha256": {s: json.loads((STUDY / "cache" / f"{s}_perp_1m.meta.json").read_text())["logical_sha256"]
                                 for s in ("BTCUSDT", "ETHUSDT")},
        "files": {_rel(f): _sha(f) for f in frozen_files()}})


# --- running policies -------------------------------------------------------------------------

def _trial(policy: eng.Policy, symbol: str, block: str, use: str) -> dict:
    return {"run_id": "run_v1", "policy_id": policy.id,
            "config_sha256": hashlib.sha256(json.dumps(asdict(policy), sort_keys=True).encode()).hexdigest()[:16],
            "symbol": symbol, "block": block, "cost_model": "db (+ scenarios)", "selection_use": use,
            "outcome_access_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def run_policies(mkt: eng.Market, block: str, policies: list[eng.Policy], use: str) -> dict[str, pd.DataFrame]:
    start, end = BLOCKS[block]
    out, trials = {}, []
    folder = RUN / block / mkt.symbol
    folder.mkdir(parents=True, exist_ok=True)
    for p in policies:
        led = eng.run_policy(mkt, cal.sessions(p.anchor, "2020-01-01", end, shift_min=p.shift_min), p, start, end)
        if "exit_reason" in led:
            censored = (led["status"] == "trade") & (led["exit_reason"] == "censored_block_end")
            led.loc[censored, "status"] = "censored"         # excluded from confirmatory statistics
        led.to_csv(folder / f"{p.id}.csv.gz", index=False)
        out[p.id] = led
        trials.append(_trial(p, mkt.symbol, block, use))
    path = STUDY / "trial_ledger.csv"
    pd.DataFrame(trials).to_csv(path, mode="a", header=not path.exists(), index=False)
    return out


def summary_table(ledgers: dict[str, pd.DataFrame], mkt: eng.Market, block: str) -> pd.DataFrame:
    start, end = BLOCKS[block]
    rows = []
    for pid, led in ledgers.items():
        for sc in SUMMARY_SCENARIOS:
            s = met.summarize(led, mkt, start, end, sc)
            s["exit_reasons"] = json.dumps(s.get("exit_reasons", {}))
            rows.append({"policy": pid, "censored": int((led["status"] == "censored").sum()), **s})
    return pd.DataFrame(rows)


def period_expectancy(led: pd.DataFrame, freq: str, scenario: str = "db") -> pd.Series:
    tr = met.trades(led)
    if not len(tr):
        return pd.Series(dtype=float)
    d = pd.to_datetime(tr["date"])
    key = d.dt.year.astype(str) if freq == "year" else d.dt.year.astype(str) + "-H" + ((d.dt.month > 6) + 1).astype(str)
    return met.net_bp(tr, scenario).groupby(key.to_numpy()).mean()


def daily_matrix(ledgers: dict[str, pd.DataFrame], ids: list[str], mkt: eng.Market, block: str, scenario="db") -> pd.DataFrame:
    start, end = BLOCKS[block]
    return pd.DataFrame({pid: met.daily_returns(ledgers[pid], mkt, start, end, scenario) for pid in ids})


def sharpe(daily: pd.Series) -> float:
    sd = daily.std(ddof=1)
    return float(daily.mean() / sd * math.sqrt(365)) if sd > 0 else float("nan")


def break_even_leg_bp(led: pd.DataFrame) -> float:
    tr = met.trades(led)
    if not len(tr):
        return float("nan")
    legs = (1 + tr["exit_px"] / tr["fill_px"]).mean()
    return float((tr["gross_bp"] + tr["funding_bp"]).mean() / legs)


# --- development ----------------------------------------------------------------------------------

def select_challenger(summary: pd.DataFrame, yearly: dict[str, pd.Series], p0_sharpe: float) -> dict:
    db = summary[summary["scenario"] == "db"].set_index("policy")
    rows = []
    for p in pol.FAMILY[1:]:
        s = db.loc[p.id]
        years_pos = int((yearly[p.id] > 0).sum()) if len(yearly[p.id]) else 0
        eligible = (s["trades"] >= 100) and (s.get("mean_bp", float("nan")) > 0) and years_pos >= 2
        rows.append({"policy": p.id, "trades": int(s["trades"]), "mean_bp": s.get("mean_bp"),
                     "sharpe": s.get("sharpe_daily_ann"), "years_positive": years_pos,
                     "changed_components": pol.changed_components(p), "eligible": bool(eligible)})
    table = pd.DataFrame(rows)
    el = table[table["eligible"] & table["sharpe"].notna()]
    if not len(el):
        return {"challenger": None, "reason": "no eligible policy", "table": table}
    best = el["sharpe"].max()
    tied = el[el["sharpe"] >= best - 0.02].sort_values(["changed_components", "policy"])
    pick = tied.iloc[0]
    if not pick["sharpe"] > p0_sharpe:
        return {"challenger": None, "reason": f"best eligible {pick['policy']} Sharpe {pick['sharpe']:.3f} "
                                              f"does not beat P0 {p0_sharpe:.3f}", "table": table}
    return {"challenger": pick["policy"], "reason": "highest eligible development net daily Sharpe", "table": table}


def stage_development() -> dict:
    verify_f0()
    if (RESULTS / "freeze_F1.json").exists():
        raise SystemExit("freeze_F1.json exists; development selection already happened")
    block = "development"
    start, end = BLOCKS[block]
    out_dir = RUN / block
    mkt = load_market("BTCUSDT")
    ledgers = run_policies(mkt, block, pol.FAMILY, "selection")
    ledgers |= run_policies(mkt, block, pol.CONTROLS, "control")
    ledgers |= run_policies(mkt, block, pol.DIAGNOSTICS, "diagnostic")
    summary = summary_table(ledgers, mkt, block)
    summary.to_csv(out_dir / "summary_BTCUSDT.csv", index=False)
    fam_ids = [p.id for p in pol.FAMILY]
    daily = daily_matrix(ledgers, fam_ids + [p.id for p in pol.CONTROLS], mkt, block)
    daily.to_csv(out_dir / "daily_db_BTCUSDT.csv")
    yearly = {pid: period_expectancy(ledgers[pid], "year") for pid in fam_ids}
    halfyear = {pid: period_expectancy(ledgers[pid], "half") for pid in fam_ids}
    pd.DataFrame(yearly).T.to_csv(out_dir / "yearly_db_BTCUSDT.csv")
    pd.DataFrame(halfyear).T.to_csv(out_dir / "halfyear_db_BTCUSDT.csv")

    p0_sharpe = sharpe(daily["P0"])
    sel = select_challenger(summary, yearly, p0_sharpe)
    sel["table"].to_csv(out_dir / "selection_table.csv", index=False)
    candidates = ["P0"] + ([sel["challenger"]] if sel["challenger"] else [])

    # draft folds, for transparency only
    folds = {pid: {"2022H1": sharpe(daily.loc["2022-01-01":"2022-06-30", pid]),
                   "2022H2": sharpe(daily.loc["2022-07-01":"2022-12-31", pid])} for pid in fam_ids}

    idx = met.block_indices(len(daily), BLOCK_DAYS, N_BOOT, SEED)
    inference = {pid: met.expectancy_inference(ledgers[pid], mkt, start, end, "db", idx) for pid in fam_ids}
    sm = stepm(daily[fam_ids].to_numpy().T, alpha=0.05, n_bootstrap=N_BOOT, block_length=BLOCK_DAYS, seed=SEED)
    stepm_out = {"rejected": sorted(fam_ids[i] for i in sm["rejected"]),
                 "t_stats": dict(zip(fam_ids, map(float, sm["t_stats"]))),
                 "final_critical_value": sm["iterations"][-1]["critical_value"]}
    sr_obs = daily[fam_ids].mean() / daily[fam_ids].std(ddof=1)
    sr_var = float(sr_obs.var(ddof=1))
    dsr = {c: {n: dsr_from_returns(daily[c].to_numpy(), n, 365, sr_var) for n in (46, 100, 200)} for c in candidates}

    rnd_dir = ctl.random_direction(mkt, met.trades(ledgers["P0"]), pol.RANDOM_CONTROLS["CTL_RANDOM_DIRECTION"], pol.RANDOM_SEED_BASE)
    rnd_time = ctl.random_time(mkt, met.trades(ledgers["P0"]), pol.RANDOM_CONTROLS["CTL_RANDOM_TIME"], pol.RANDOM_SEED_BASE)
    rnd_dir.to_csv(out_dir / "random_direction.csv", index=False)
    rnd_time.to_csv(out_dir / "random_time.csv", index=False)
    p0_mean = float(met.net_bp(met.trades(ledgers["P0"]), "db").mean())
    p0_gross = float(met.trades(ledgers["P0"])["gross_bp"].mean())
    random_summary = {name: {"p0_mean_bp_db": p0_mean, "share_seeds_at_or_above_p0_db": float((df["mean_bp_db"] >= p0_mean).mean()),
                             "seed_mean_bp_db_quantiles": met.interval(df["mean_bp_db"].to_numpy(), (0.05, 0.5, 0.95)),
                             "p0_mean_bp_gross": p0_gross, "share_seeds_at_or_above_p0_gross": float((df["mean_bp_gross"] >= p0_gross).mean())}
                      for name, df in (("random_direction", rnd_dir), ("random_time", rnd_time))}

    paired = {}
    for c in candidates:
        diff = (daily[c] - daily["CTL_MOMENTUM"]).to_numpy()
        boot = met.boot_mean(diff, idx)
        paired[f"{c}_minus_momentum"] = {"mean_daily_diff": float(diff.mean()), "ci95": met.interval(boot),
                                          "p_gt0": met.p_greater_than_zero(float(diff.mean()), boot)}
    p0_se = float(np.nanstd(met.boot_ratio(*[a.to_numpy() for a in met.per_day_trade_sums(ledgers["P0"], mkt, start, end, "db")], idx), ddof=1))
    n_dev = int(summary[(summary.policy == "P0") & (summary.scenario == "db")]["trades"].iloc[0])
    z = 1.6448536269514722 + 0.8416212335729143
    mde = {"p0_dev_se_bp": p0_se, "dev_trades": n_dev,
           "validation_mde80_bp": z * p0_se * math.sqrt(n_dev / (n_dev * 2 / 3)),
           "lockbox_mde80_bp": z * p0_se * math.sqrt(n_dev / (n_dev * (1 + 256 / 365) / 3))}

    report = {"block": block, "p0_sharpe": p0_sharpe, "selection": {k: v for k, v in sel.items() if k != "table"},
              "candidates": candidates, "inference": inference, "stepm": stepm_out, "dsr": dsr,
              "random_controls": random_summary, "paired_vs_momentum": paired, "draft_folds": folds, "mde": mde,
              "break_even_leg_bp": {pid: break_even_leg_bp(ledgers[pid]) for pid in [*fam_ids, *[p.id for p in pol.CONTROLS]]}}
    (out_dir / "development_report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    _write_freeze("F1", {"note": "After development, before any validation outcome.", "candidates": candidates,
                         "challenger_policy": asdict(pol.BY_ID[sel["challenger"]]) if sel["challenger"] else None,
                         "selection_reason": sel["reason"],
                         "result_files": {_rel(f): _sha(f) for f in sorted(out_dir.glob("*")) if f.is_file()}})
    return report


# --- validation -----------------------------------------------------------------------------------

def stage_validation() -> dict:
    verify_f0()
    f1 = _read_freeze("F1")
    if (RESULTS / "freeze_F2.json").exists():
        raise SystemExit("freeze_F2.json exists; validation already happened")
    block = "validation"
    start, end = BLOCKS[block]
    out_dir = RUN / block
    cands = [pol.BY_ID[c] for c in f1["candidates"]]
    report = {"block": block, "candidates": f1["candidates"], "assets": {}}
    for sym in ("BTCUSDT", "ETHUSDT"):
        mkt = load_market(sym)
        ledgers = run_policies(mkt, block, cands, "confirmatory" if sym == "BTCUSDT" else "transfer")
        ledgers |= run_policies(mkt, block, [pol.BY_ID["CTL_MOMENTUM"]], "control")
        ledgers |= run_policies(mkt, block, pol.DIAGNOSTICS, "diagnostic")
        summary_table(ledgers, mkt, block).to_csv(out_dir / f"summary_{sym}.csv", index=False)
        daily = daily_matrix(ledgers, list(ledgers), mkt, block)
        daily.to_csv(out_dir / f"daily_db_{sym}.csv")
        idx = met.block_indices(len(daily), BLOCK_DAYS, N_BOOT, SEED)
        per = {}
        for c in f1["candidates"]:
            led = ledgers[c]
            inf = met.expectancy_inference(led, mkt, start, end, "db", idx)
            half = period_expectancy(led, "half")
            plus5 = float(met.net_bp(met.trades(led), "db_plus5").mean()) if len(met.trades(led)) else float("nan")
            clauses = {"checks_passed": True, "mean_bp_gt0": inf["mean_bp"] > 0, "daily_mean_gt0": inf["daily_mean"] > 0,
                       "halfyears_positive_ge3": int((half > 0).sum()) >= 3, "db_plus5_ge0": plus5 >= 0}
            per[c] = {"inference": inf, "halfyears": half.to_dict(), "db_plus5_mean_bp": plus5,
                      "trades": int(len(met.trades(led))), "clauses": clauses, "passes": all(clauses.values())}
        report["assets"][sym] = per
    btc = report["assets"]["BTCUSDT"]
    decision = "open" if any(v["passes"] for v in btc.values()) else "closed"
    report["continuation"] = {"rule": "at least one BTC candidate passes all clauses", "decision": decision}
    (out_dir / "validation_report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    _write_freeze("F2", {"note": "After validation, before any lockbox outcome.", "candidates": f1["candidates"],
                         "lockbox": decision, "btc_clauses": {c: v["clauses"] for c, v in btc.items()},
                         "result_files": {_rel(f): _sha(f) for f in sorted(out_dir.glob("*")) if f.is_file()}})
    return report


# --- lockbox and verdict ---------------------------------------------------------------------------

def _no_edge_or_inconclusive(ci: list[float]) -> str:
    return "NO_ECONOMIC_EDGE_DEMONSTRATED" if ci[2] < HURDLE_BP else "INCONCLUSIVE"


def stage_lockbox() -> dict:
    verify_f0()
    f2 = _read_freeze("F2")
    if (RESULTS / "freeze_verdict.json").exists():
        raise SystemExit("freeze_verdict.json exists; the lockbox was already evaluated")
    val = json.loads((RUN / "validation" / "validation_report.json").read_text())
    verdicts = {}
    if f2["lockbox"] != "open":
        for c in f2["candidates"]:
            ci = val["assets"]["BTCUSDT"][c]["inference"]["mean_bp_ci95"]
            verdicts[c] = [_no_edge_or_inconclusive(ci), "PRICE_SIGNAL_ONLY"]
        body = {"lockbox": "closed", "verdicts": verdicts, "basis": "validation block (lockbox stayed closed)"}
        _write_freeze("verdict", body)
        return body
    block = "lockbox"
    start, end = BLOCKS[block]
    out_dir = RUN / block
    cands = [pol.BY_ID[c] for c in f2["candidates"]]
    assets, pvals, stats = {}, {}, {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        mkt = load_market(sym)
        ledgers = run_policies(mkt, block, cands, "confirmatory")
        ledgers |= run_policies(mkt, block, [pol.BY_ID["CTL_MOMENTUM"]], "control")
        ledgers |= run_policies(mkt, block, pol.DIAGNOSTICS, "diagnostic")
        summary_table(ledgers, mkt, block).to_csv(out_dir / f"summary_{sym}.csv", index=False)
        daily = daily_matrix(ledgers, list(ledgers), mkt, block)
        daily.to_csv(out_dir / f"daily_db_{sym}.csv")
        idx = met.block_indices(len(daily), BLOCK_DAYS, N_BOOT, SEED)
        for c in f2["candidates"]:
            inf = met.expectancy_inference(ledgers[c], mkt, start, end, "db", idx)
            tr = met.trades(ledgers[c])
            inf["db_plus5_mean_bp"] = float(met.net_bp(tr, "db_plus5").mean()) if len(tr) else float("nan")
            inf["trades"] = int(len(tr))
            stats[f"{sym}_{c}"] = inf
            pvals[f"{sym}_{c}_expectancy"] = inf["p_mean_bp_gt0"]
            if sym == "BTCUSDT":
                diff = (daily[c] - daily["CTL_MOMENTUM"]).to_numpy()
                boot = met.boot_mean(diff, idx)
                stats[f"{sym}_{c}_uplift"] = {"mean_daily_diff": float(diff.mean()), "ci95": met.interval(boot),
                                              "p_gt0": met.p_greater_than_zero(float(diff.mean()), boot)}
                pvals[f"{sym}_{c}_uplift"] = stats[f"{sym}_{c}_uplift"]["p_gt0"]
    rejected = met.holm(pvals, 0.05)
    for c in f2["candidates"]:
        b = stats[f"BTCUSDT_{c}"]
        e = stats[f"ETHUSDT_{c}"]
        halves_ok = f2["btc_clauses"][c]["halfyears_positive_ge3"]
        candidate = (b["mean_bp"] >= HURDLE_BP and b["daily_mean"] > 0 and rejected[f"BTCUSDT_{c}_expectancy"]
                     and b["db_plus5_mean_bp"] >= 0 and halves_ok)
        if candidate:
            v = ["HISTORICAL_CANDIDATE"]
            if rejected[f"BTCUSDT_{c}_uplift"]:
                v.append("BREAKOUT_INCREMENT_SUPPORTED")
            if e["mean_bp"] > 0 and rejected[f"ETHUSDT_{c}_expectancy"]:
                v.append("TRANSFER_SUPPORTED")
        else:
            v = [_no_edge_or_inconclusive(b["mean_bp_ci95"])]
        verdicts[c] = v + ["PRICE_SIGNAL_ONLY"]
    body = {"lockbox": "opened", "stats": stats, "p_values": pvals, "holm_rejected": rejected, "verdicts": verdicts,
            "result_files": {_rel(f): _sha(f) for f in sorted(out_dir.glob("*")) if f.is_file()}}
    _write_freeze("verdict", body)
    return body


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["freeze0", "development", "validation", "lockbox"])
    stage = ap.parse_args().stage
    result = {"freeze0": freeze0, "development": stage_development, "validation": stage_validation,
              "lockbox": stage_lockbox}[stage]()
    print(json.dumps(result if isinstance(result, dict) else str(result), indent=1, default=str)[:4000])

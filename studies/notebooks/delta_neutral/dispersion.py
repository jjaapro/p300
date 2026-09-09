"""Sub-study (i) — cross-venue funding dispersion.

Frozen rules live in README.md. Nothing under strategies/** or bots/** is
modified; prod.db is opened mode=ro.

Run:  python studies/notebooks/delta_neutral/dispersion.py
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from strategies.support import db  # noqa: E402
from studies.lib.validation import bootstrap, dsr_pbo, metrics  # noqa: E402
import carry_sizing  # noqa: E402  (same folder — the Carry benchmark for clause I-2)

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)

SETTLEMENT = 8 * 3600
PER_YEAR = 3 * 365                      # 1095 settlements/year
SEED = 42
BP = 1e-4
THRESHOLDS_BP = (1.0, 2.0, 3.0, 5.0, 10.0)
PRIMARY_THRESHOLD_BP = 3.0
CYCLE_COST = 20 * BP                    # 4 legs x 5bp, per full open-and-close cycle
N_TRIALS = 20
POWER_MIN_SETTLEMENTS = 365
CUTOVER_TS = int(datetime(2026, 4, 13, tzinfo=timezone.utc).timestamp())


def ro_connect() -> sqlite3.Connection:
    return sqlite3.connect("file:" + str(db.PROD_DB) + "?mode=ro", uri=True)


def _d(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _dt(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


# ── venue funding loaders (all on the 8h settlement grid) ────────────────────

def load_venues(con: sqlite3.Connection, asset: str) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = {}
    if asset == "BTC":
        bin_tbl, okx_inst, bybit_sym = "cd_funding_rate", "BTC-USDT-SWAP", "BTCUSDT"
    else:
        bin_tbl, okx_inst, bybit_sym = "cd_funding_rate_eth", "ETH-USDT-SWAP", "ETHUSDT"
    out["binance"] = {int(t): float(v) for t, v in con.execute(
        f"SELECT timestamp, fr_close FROM {bin_tbl} "
        f"WHERE fr_close IS NOT NULL AND timestamp % ? = 0", (SETTLEMENT,))}
    out["okx"] = {int(t): float(v) for t, v in con.execute(
        "SELECT timestamp, funding_rate FROM okx_funding "
        "WHERE inst_id = ? AND funding_rate IS NOT NULL AND timestamp % ? = 0",
        (okx_inst, SETTLEMENT))}
    out["bybit"] = {int(t): float(v) for t, v in con.execute(
        "SELECT timestamp, funding_rate FROM bybit_funding "
        "WHERE symbol = ? AND funding_rate IS NOT NULL AND timestamp % ? = 0",
        (bybit_sym, SETTLEMENT))}
    return out


def build_panel(venues: dict[str, dict[int, float]],
                names: tuple[str, ...]) -> tuple[list[int], np.ndarray]:
    """Settlements where EVERY venue in `names` has a rate. Returns (ts, matrix)
    with matrix[i, j] = rate of venue names[j] at ts[i]."""
    common = set(venues[names[0]])
    for n in names[1:]:
        common &= set(venues[n])
    ts = sorted(common)
    mat = np.array([[venues[n][t] for n in names] for t in ts], dtype=float)
    return ts, mat


# ── dispersion description ───────────────────────────────────────────────────

def describe(ts: list[int], mat: np.ndarray, names: tuple[str, ...]) -> dict:
    hi = mat.max(axis=1)
    lo = mat.min(axis=1)
    spread = hi - lo
    s_bp = spread / BP
    qs = [50, 75, 90, 95, 99, 99.9]
    pcts = {f"p{q}": float(np.percentile(s_bp, q)) for q in qs}
    exceed = {f"gt_{int(t)}bp": float((s_bp > t).mean()) for t in THRESHOLDS_BP}
    n_exc = {f"n_gt_{int(t)}bp": int((s_bp > t).sum()) for t in THRESHOLDS_BP}

    # concentration of the 3bp exceedance
    mask = s_bp > PRIMARY_THRESHOLD_BP
    by_day: dict[str, int] = {}
    for t, m in zip(ts, mask):
        if m:
            by_day[_d(t)] = by_day.get(_d(t), 0) + 1
    all_days = sorted({_d(t) for t in ts})
    ranked = sorted(by_day.values(), reverse=True)
    total = sum(ranked)
    top5_days = sum(ranked[:5]) / total if total else float("nan")
    k = max(1, int(round(0.05 * len(all_days))))
    top5pct_days = sum(ranked[:k]) / total if total else float("nan")
    stress = bool(total and top5pct_days >= 0.50)

    argmax = mat.argmax(axis=1)
    argmin = mat.argmin(axis=1)
    max_share = {names[j]: float((argmax == j).mean()) for j in range(len(names))}
    min_share = {names[j]: float((argmin == j).mean()) for j in range(len(names))}
    pair = [(int(a), int(b)) for a, b in zip(argmin, argmax)]
    flips = sum(1 for a, b in zip(pair, pair[1:]) if a != b)
    # same restricted to exceedance settlements
    epair = [p for p, m in zip(pair, mask) if m]
    eflips = sum(1 for a, b in zip(epair, epair[1:]) if a != b)

    return {"n": len(ts), "first": _dt(ts[0]), "last": _dt(ts[-1]),
            "span_days": (ts[-1] - ts[0]) / 86400.0,
            "mean_spread_bp": float(s_bp.mean()), "sd_spread_bp": float(s_bp.std(ddof=1)),
            "max_spread_bp": float(s_bp.max()), **pcts, **exceed, **n_exc,
            "n_exceed_days_3bp": len(by_day), "n_calendar_days": len(all_days),
            "top5_days_share_of_3bp_exceedances": top5_days,
            "top5pct_days_share_of_3bp_exceedances": top5pct_days,
            "stress_concentrated": stress,
            "max_venue_share": max_share, "min_venue_share": min_share,
            "pair_flip_rate_all": flips / max(len(pair) - 1, 1),
            "pair_flip_rate_on_exceedance": eflips / max(len(epair) - 1, 1),
            "mean_abs_rate_bp": float(np.abs(mat).mean() / BP)}


# ── strategy replay ──────────────────────────────────────────────────────────

def replay(ts: list[int], mat: np.ndarray, names: tuple[str, ...],
           thr_bp: float, causal: bool, cost_mode: str) -> dict:
    """Per-settlement net return on notional (decimal), one entry per interval
    (ts[i] -> ts[i+1]).

    causal=True  : decide from the ts[i] cross-section, collect the ts[i+1] rates
                   (the tradeable version).
    causal=False : collect the ts[i] spread itself (NON-TRADEABLE upper bound).

    cost_mode 'pair_change' : CYCLE_COST charged whenever the held pair changes
                              (incl. OFF->ON and ON->OFF)  [pre-registered]
              'every'       : CYCLE_COST charged on every ON interval
              'zero'        : no cost
    """
    thr = thr_bp * BP
    hi = mat.max(axis=1); lo = mat.min(axis=1)
    spread = hi - lo
    amax = mat.argmax(axis=1); amin = mat.argmin(axis=1)

    net = np.zeros(len(ts) - 1)
    gross = np.zeros(len(ts) - 1)
    on = np.zeros(len(ts) - 1, dtype=bool)
    held: tuple[int, int] | None = None
    n_cost_events = 0
    n_pair_changes_on = 0
    prev_on_pair: tuple[int, int] | None = None

    for i in range(len(ts) - 1):
        contiguous = (ts[i + 1] - ts[i]) == SETTLEMENT
        want = (spread[i] > thr) and contiguous
        pair = (int(amin[i]), int(amax[i])) if want else None
        if pair != held:
            if cost_mode != "zero" and not (pair is None and held is None):
                net[i] -= CYCLE_COST
                n_cost_events += 1
        elif cost_mode == "every" and pair is not None:
            net[i] -= CYCLE_COST
            n_cost_events += 1
        if pair is not None:
            if causal:
                g = mat[i + 1, pair[1]] - mat[i + 1, pair[0]]
            else:
                g = spread[i]
            gross[i] = g
            net[i] += g
            on[i] = True
            if prev_on_pair is not None and prev_on_pair != pair:
                n_pair_changes_on += 1
            prev_on_pair = pair
        held = pair

    n_on = int(on.sum())
    return {"net": net, "gross": gross, "on": on, "ts": ts[:-1],
            "n_intervals": len(net), "n_on": n_on,
            "on_rate": n_on / max(len(net), 1),
            "n_cost_events": n_cost_events,
            "rotation_rate_on": n_pair_changes_on / max(n_on - 1, 1),
            "mean_gross_on_bp": float(gross[on].mean() / BP) if n_on else float("nan"),
            "mean_net_bp": float(net.mean() / BP),
            "ann_return_pct": float(net.mean() * PER_YEAR * 100.0),
            "total_pct": float(net.sum() * 100.0)}


def boot_mean_ci(x: np.ndarray, n_iter: int = 10000, seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed)
    n = x.size
    if n < 2:
        return {"p05": float("nan"), "p50": float("nan"), "p95": float("nan"),
                "p_gt_0": float("nan"), "point": float("nan")}
    means = np.empty(n_iter)
    for i in range(n_iter):
        means[i] = x[rng.integers(0, n, n)].mean()
    p05, p50, p95 = (float(q) for q in np.quantile(means, (0.05, 0.5, 0.95)))
    return {"point": float(x.mean()), "p05": p05, "p50": p50, "p95": p95,
            "p_gt_0": float((means > 0).mean()), "n_iter": n_iter}


# ── basis-leakage diagnostic ─────────────────────────────────────────────────

def basis_leakage(con: sqlite3.Connection, ts: list[int],
                  tbl_a: str, bar_a: int, tbl_b: str, bar_b: int) -> dict:
    """sd of log(P_b/P_a) change over each 8h holding interval, in bp.

    This is the price P&L the 'delta-neutral' assumption throws away: a pair
    long on venue A and short on venue B earns -(dlog P_b - dlog P_a).

    Bars are stamped at their OPEN, so the price AT instant t is the close of
    the bar stamped t - bar_seconds. Passing the bar length per table keeps a
    15m series and a 1h series aligned to the same instant (using `close` at
    the same stamp for both would compare 16:15 against 17:00).
    """
    def series(tbl, bar):
        return {int(t) + bar: float(c) for t, c in con.execute(
            f"SELECT timestamp, close FROM {tbl} WHERE close IS NOT NULL")}
    A, B = series(tbl_a, bar_a), series(tbl_b, bar_b)
    rel = []
    used = []
    for t in ts:
        if t in A and t in B and A[t] > 0 and B[t] > 0:
            rel.append(math.log(B[t] / A[t]))
            used.append(t)
    if len(rel) < 3:
        return {"n": len(rel), "note": "insufficient overlapping price rows"}
    r = np.array(rel)
    d = np.diff(r)
    keep = np.array([(used[i + 1] - used[i]) == SETTLEMENT for i in range(len(used) - 1)])
    d = d[keep]
    if d.size < 3:
        return {"n": int(d.size), "note": "insufficient contiguous intervals"}
    return {"n": int(d.size), "first": _dt(used[0]), "last": _dt(used[-1]),
            "sd_bp_per_8h": float(d.std(ddof=1) / BP),
            "mean_abs_bp_per_8h": float(np.abs(d).mean() / BP),
            "p95_abs_bp_per_8h": float(np.percentile(np.abs(d), 95) / BP),
            "mean_level_bp": float(r.mean() / BP)}


# ── main ─────────────────────────────────────────────────────────────────────

ARMS = (
    ("P",     "BTC", ("binance", "okx", "bybit")),
    ("P-ETH", "ETH", ("binance", "okx", "bybit")),
    ("S",     "BTC", ("binance", "bybit")),
    ("S-ETH", "ETH", ("binance", "bybit")),
)


def main() -> int:
    con = ro_connect()
    panels = {}
    descs = {}
    for arm, asset, names in ARMS:
        venues = load_venues(con, asset)
        ts, mat = build_panel(venues, names)
        panels[arm] = (ts, mat, names, asset)
        descs[arm] = describe(ts, mat, names)
        print(f"{arm:6s} {asset} {names} -> {len(ts)} settlements "
              f"{_dt(ts[0])} .. {_dt(ts[-1])}")

    # ---- Carry benchmark over each arm's window (clause I-2) --------------
    print("\ncomputing the Carry benchmark from the sub-study (iii) replay ...")
    bench = {}
    for arm in panels:
        ts, *_ = panels[arm]
        bench[arm] = carry_sizing.carry_benchmark(ts[0], ts[-1])
        print(f"  {arm}: sleeve-faithful Carry ann "
              f"{bench[arm]['sleeve_faithful']['ann_return_pct']:.2f}% "
              f"({bench[arm]['sleeve_faithful']['n_days']} days), raw funding ann "
              f"{bench[arm]['raw_funding_ann_pct']:.2f}%")

    # ---- strategy replays --------------------------------------------------
    rows = []
    detail = {}
    for arm, (ts, mat, names, asset) in panels.items():
        for thr in THRESHOLDS_BP:
            for causal in (True, False):
                for cost in ("pair_change", "every", "zero"):
                    r = replay(ts, mat, names, thr, causal, cost)
                    row = {"arm": arm, "asset": asset,
                           "venues": "+".join(names), "threshold_bp": thr,
                           "causal": causal, "cost_mode": cost,
                           "n_intervals": r["n_intervals"], "n_on": r["n_on"],
                           "on_rate": r["on_rate"],
                           "rotation_rate_on": r["rotation_rate_on"],
                           "n_cost_events": r["n_cost_events"],
                           "mean_gross_on_bp": r["mean_gross_on_bp"],
                           "mean_net_bp": r["mean_net_bp"],
                           "ann_return_pct": r["ann_return_pct"],
                           "total_pct": r["total_pct"],
                           "carry_bench_ann_pct":
                               bench[arm]["sleeve_faithful"]["ann_return_pct"]}
                    rows.append(row)
                    if (thr == PRIMARY_THRESHOLD_BP and causal
                            and cost == "pair_change"):
                        detail[arm] = (r, row)

    # ---- clause evaluation on the primary cell of each arm -----------------
    clauses = {}
    for arm, (r, row) in detail.items():
        net = r["net"]
        ci = boot_mean_ci(net)
        d1 = dsr_pbo.dsr_from_returns(net, 1, periods_per_year=PER_YEAR)
        d20 = dsr_pbo.dsr_from_returns(net, N_TRIALS, periods_per_year=PER_YEAR)
        bb = bootstrap.block_bootstrap_sharpe(net, block=9, n_iter=5000, seed=SEED,
                                              periods_per_year=PER_YEAR)
        b = bench[arm]["sleeve_faithful"]["ann_return_pct"]
        clauses[arm] = {
            "I-0": {"desc": f"< {POWER_MIN_SETTLEMENTS} usable settlements",
                    "value": r["n_intervals"], "threshold": POWER_MIN_SETTLEMENTS,
                    "fired": r["n_intervals"] < POWER_MIN_SETTLEMENTS},
            "I-1": {"desc": "net annualised return < 5%",
                    "value": r["ann_return_pct"], "threshold": 5.0,
                    "fired": r["ann_return_pct"] < 5.0},
            "I-2": {"desc": "net annualised return < realised Carry benchmark",
                    "value": r["ann_return_pct"], "threshold": b,
                    "fired": r["ann_return_pct"] < b},
            "I-3": {"desc": "90% iid-bootstrap CI of mean per-settlement net includes 0",
                    "value": [ci["p05"] / BP, ci["p95"] / BP], "threshold": 0.0,
                    "fired": ci["p05"] <= 0.0 <= ci["p95"]},
            "I-4": {"desc": "DSR at n_trials=20 < 0.95",
                    "value": (d20["dsr"] if d20 else None), "threshold": 0.95,
                    "fired": (d20 is None or d20["dsr"] < 0.95)},
            "_bootstrap_mean_bp": {k: (v / BP if k in ("point", "p05", "p50", "p95")
                                       else v) for k, v in ci.items()},
            "_block_bootstrap_sharpe": bb,
            "_dsr_n1": d1, "_dsr_n20": d20,
            "_sharpe_ann": float(metrics.daily_sharpe(net, PER_YEAR)),
            "_max_dd_pct": float(metrics.max_drawdown(net * 100.0)),
        }

    # ---- era split on arm S (Binance predicted-rate proxy pre-cutover) -----
    era = {}
    for arm in ("S", "S-ETH"):
        ts, mat, names, asset = panels[arm]
        for label, lo, hi in (("pre_cutover", 0, CUTOVER_TS),
                              ("post_cutover", CUTOVER_TS, 10 ** 11)):
            idx = [i for i, t in enumerate(ts) if lo <= t < hi]
            if len(idx) < 10:
                continue
            sub_ts = [ts[i] for i in idx]
            sub_mat = mat[idx]
            r = replay(sub_ts, sub_mat, names, PRIMARY_THRESHOLD_BP, True, "pair_change")
            era[f"{arm}_{label}"] = {
                "n_intervals": r["n_intervals"], "n_on": r["n_on"],
                "first": _dt(sub_ts[0]), "last": _dt(sub_ts[-1]),
                "ann_return_pct": r["ann_return_pct"],
                "mean_spread_bp": float(((sub_mat.max(axis=1)
                                          - sub_mat.min(axis=1)) / BP).mean()),
                "exceed_3bp_rate": float((((sub_mat.max(axis=1)
                                            - sub_mat.min(axis=1)) / BP)
                                          > PRIMARY_THRESHOLD_BP).mean()),
            }

    # ---- basis leakage -----------------------------------------------------
    H, Q = 3600, 900
    leak = {}
    ts_p = panels["P"][0]
    leak["BTC binance(cd_futures_ohlcv 1h) vs okx(okx_perp_1h) — 3-venue window"] = \
        basis_leakage(con, ts_p, "cd_futures_ohlcv", H, "okx_perp_1h", H)
    ts_s = panels["S"][0]
    leak["BTC binance vs bybit(bybit_perp_1h) — 2-venue window (bybit prices end 2026-05-26)"] = \
        basis_leakage(con, ts_s, "cd_futures_ohlcv", H, "bybit_perp_1h", H)
    ts_pe = panels["P-ETH"][0]
    leak["ETH binance(cd_futures_eth_15m) vs okx(okx_perp_eth_1h)"] = \
        basis_leakage(con, ts_pe, "cd_futures_eth_15m", Q, "okx_perp_eth_1h", H)
    leak["ETH binance vs bybit(bybit_perp_eth_1h)"] = \
        basis_leakage(con, panels["S-ETH"][0], "cd_futures_eth_15m", Q,
                      "bybit_perp_eth_1h", H)

    # ---- pairwise venue differences (provenance cross-check) --------------
    pairwise = {}
    for arm, (ts, mat, names, asset) in panels.items():
        if len(names) < 3:
            continue
        for a in range(len(names)):
            for b in range(a + 1, len(names)):
                d = np.abs(mat[:, a] - mat[:, b]) / BP
                pairwise[f"{arm}:{names[a]}-{names[b]}"] = {
                    "mean_abs_bp": float(d.mean()), "p95_abs_bp": float(np.percentile(d, 95)),
                    "max_abs_bp": float(d.max()), "n_gt_3bp": int((d > 3).sum())}
    # the same pair over the post-cutover era of arm S, where every leg is a
    # realised settlement, vs the pre-cutover era where Binance is a proxy
    ts_s2, mat_s2, names_s2, _ = panels["S"]
    for label, lo, hi in (("pre_cutover", 0, CUTOVER_TS),
                          ("post_cutover", CUTOVER_TS, 10 ** 11)):
        idx = [i for i, t in enumerate(ts_s2) if lo <= t < hi]
        if not idx:
            continue
        d = np.abs(mat_s2[idx, 0] - mat_s2[idx, 1]) / BP
        pairwise[f"S:binance-bybit:{label}"] = {
            "n": len(idx), "mean_abs_bp": float(d.mean()),
            "p95_abs_bp": float(np.percentile(d, 95)),
            "max_abs_bp": float(d.max()), "n_gt_3bp": int((d > 3).sum())}
    con.close()

    # ---- verdict -----------------------------------------------------------
    def arm_verdict(a: str) -> str:
        c = clauses[a]
        if c["I-1"]["fired"] or c["I-2"]["fired"]:
            return "KILL"
        if c["I-3"]["fired"] or c["I-4"]["fired"]:
            return "KILL (not distinguishable from noise / fails deflation)"
        return "PASS"

    verdicts = {a: arm_verdict(a) for a in clauses}
    p_under = clauses["P"]["I-0"]["fired"]
    s_powered = not clauses["S"]["I-0"]["fired"]
    if s_powered and verdicts["S"].startswith("KILL"):
        overall = "KILL (from arm S, the adequately-powered 2-venue arm)"
    elif s_powered:
        overall = "INCONCLUSIVE — arm S positive, 3-venue rule unproven"
    else:
        overall = "INCONCLUSIVE — no adequately-powered arm"
    if p_under:
        overall += "; arm P INCONCLUSIVE on power"

    out = {"overall_verdict": overall, "arm_verdicts": verdicts,
           "clauses": clauses, "dispersion": descs, "carry_benchmark": bench,
           "era_split_arm_S": era, "basis_leakage": leak,
           "pairwise_venue_diff": pairwise,
           "settings": {"thresholds_bp": list(THRESHOLDS_BP),
                        "primary_threshold_bp": PRIMARY_THRESHOLD_BP,
                        "cycle_cost_bp": CYCLE_COST / BP,
                        "settlements_per_year": PER_YEAR,
                        "n_trials": N_TRIALS,
                        "power_min_settlements": POWER_MIN_SETTLEMENTS}}
    (RESULTS / "dispersion_summary.json").write_text(json.dumps(out, indent=2, default=str))

    with open(RESULTS / "dispersion_strategy_grid.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    with open(RESULTS / "dispersion_distribution.csv", "w", newline="") as f:
        keys = sorted({k for d in descs.values() for k in d
                       if not isinstance(d[k], dict)})
        w = csv.writer(f); w.writerow(["arm"] + keys)
        for a, d in descs.items():
            w.writerow([a] + [d.get(k) for k in keys])

    # per-settlement spread series for the two BTC arms (notebook plots)
    for arm in ("P", "S"):
        ts, mat, names, _ = panels[arm]
        sp = (mat.max(axis=1) - mat.min(axis=1)) / BP
        with open(RESULTS / f"dispersion_series_{arm}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "utc"] + [f"rate_bp_{n}" for n in names]
                       + ["spread_bp", "argmin", "argmax"])
            amin = mat.argmin(axis=1); amax = mat.argmax(axis=1)
            for i, t in enumerate(ts):
                w.writerow([t, _dt(t)] + [f"{mat[i, j] / BP:.4f}" for j in range(len(names))]
                           + [f"{sp[i]:.4f}", names[amin[i]], names[amax[i]]])

    print(f"\nOVERALL (i): {overall}")
    for a in clauses:
        print(f"  arm {a}: {verdicts[a]}")
        for k in ("I-0", "I-1", "I-2", "I-3", "I-4"):
            c = clauses[a][k]
            print(f"    {k}: {c['desc']} -> value={c['value']} "
                  f"thr={c['threshold']} fired={c['fired']}")
    print(f"wrote {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

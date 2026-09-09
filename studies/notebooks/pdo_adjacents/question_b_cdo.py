"""QUESTION (b) -- the CDO (current-day-open) retouch variant.

Rules are frozen in README.md section 2.  Nothing here is swept: every
threshold is inherited from the PDO sleeve's own config (2.0% excursion,
0.10% touch tolerance, 24h TIF capped at next-day 01:00 UTC).  The only two
numbers with no PDO ancestor are the 1.00% stop (which exists solely to give
"mean R" a denominator) and the 2.00% = 2R target.

Read-only on prod.db.  Writes only into ./results/.
"""
from __future__ import annotations

import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from studies.notebooks.pdo_adjacents import common as C  # noqa: E402
from studies.lib.validation import bootstrap, dsr_pbo, gates, metrics  # noqa: E402

RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)

EXCURSION_PCT = 2.00     # = PDO GAP_THRESHOLD_PCT
TOUCH_TOL_PCT = 0.10     # = PDO TOUCH_TOL_PCT
STOP_PCT = 1.00          # 1R
TARGET_R = 2.00
TIF_HOURS = 24
COST_R = C.COST_BP_RT / 100.0 / STOP_PCT   # 18bp / 1.00% = 0.18 R
N_TRIALS = 2
N_ITER = 10000
SEED = 42


# --- engine ------------------------------------------------------------------

def run_cdo(asset: str, bars: "C.AssetBars", end_ts: int) -> pd.DataFrame:
    m_ts = bars.minutes["ts"].to_numpy()
    m_high = bars.minutes["high"].to_numpy()
    m_low = bars.minutes["low"].to_numpy()

    h_open_ts = bars.h_index * C.HOUR          # bar OPEN timestamps
    n_h = len(h_open_ts)

    trades = []
    open_until = -1          # position blocks entries until this ts
    day_ord_prev = None
    cdo = None
    cdo_ts = None
    run_hi = -np.inf
    run_lo = np.inf
    fired_today = False

    for i in range(n_h):
        t_open = int(h_open_ts[i])
        t_close = t_open + C.HOUR
        if t_close > end_ts:
            break
        day_ord = t_open // C.DAY
        if day_ord != day_ord_prev:
            day_ord_prev = day_ord
            first = bars.day_first(day_ord)
            cdo = None if first is None else first[0]
            cdo_ts = None if first is None else first[1]
            run_hi, run_lo = -np.inf, np.inf
            fired_today = False
        if cdo is None or cdo <= 0:
            continue
        lo = float(bars.h_low[i]); hi = float(bars.h_high[i])
        cl = float(bars.h_close[i])
        run_hi = max(run_hi, hi)
        run_lo = min(run_lo, lo)
        up_exc = (run_hi / cdo - 1.0) * 100.0
        dn_exc = (1.0 - run_lo / cdo) * 100.0
        armed_up = up_exc >= EXCURSION_PCT
        armed_dn = dn_exc >= EXCURSION_PCT
        if not (armed_up or armed_dn):
            continue
        if fired_today or t_close <= open_until:
            continue
        band_hi = cdo * (1 + TOUCH_TOL_PCT / 100)
        band_lo = cdo * (1 - TOUCH_TOL_PCT / 100)
        if not (lo <= band_hi and hi >= band_lo):
            continue
        # direction = larger armed excursion
        if armed_up and armed_dn:
            if up_exc == dn_exc:
                continue                       # impossible tie -> skip, recorded by absence
            direction = "LONG" if up_exc > dn_exc else "SHORT"
        else:
            direction = "LONG" if armed_up else "SHORT"

        entry = cl
        entry_ts = t_close
        day_start = int(day_ord) * C.DAY
        deadline = min(entry_ts + TIF_HOURS * C.HOUR, day_start + C.DAY + C.HOUR)
        if deadline <= entry_ts:
            continue

        if direction == "LONG":
            stop_px = entry * (1 - STOP_PCT / 100)
            tgt_px = entry * (1 + TARGET_R * STOP_PCT / 100)
        else:
            stop_px = entry * (1 + STOP_PCT / 100)
            tgt_px = entry * (1 - TARGET_R * STOP_PCT / 100)

        a = int(np.searchsorted(m_ts, entry_ts, side="left"))
        b = int(np.searchsorted(m_ts, deadline, side="left"))
        exit_type, exit_ts, exit_px = None, None, None
        ambiguous = False
        if b > a:
            if direction == "LONG":
                s_hit = m_low[a:b] <= stop_px
                t_hit = m_high[a:b] >= tgt_px
            else:
                s_hit = m_high[a:b] >= stop_px
                t_hit = m_low[a:b] <= tgt_px
            i_s = int(np.argmax(s_hit)) if s_hit.any() else None
            i_t = int(np.argmax(t_hit)) if t_hit.any() else None
            ambiguous = (i_s is not None and i_t is not None and i_s == i_t)
            if i_s is not None and (i_t is None or i_s <= i_t):
                exit_type, exit_px = "stop", stop_px          # ties -> stop (conservative)
                exit_ts = int(m_ts[a + i_s])
            elif i_t is not None:
                exit_type, exit_px = "target", tgt_px
                exit_ts = int(m_ts[a + i_t])
        if exit_type is None:
            px = bars.hour_close_at(deadline)
            if px is None:
                continue
            exit_type, exit_px, exit_ts = "tif", float(px), deadline

        if direction == "LONG":
            move_pct = (exit_px / entry - 1.0) * 100.0
        else:
            move_pct = (entry / exit_px - 1.0) * 100.0
        r = move_pct / STOP_PCT - COST_R

        trades.append({
            "asset": asset, "direction": direction,
            "entry_ts": entry_ts, "entry_iso": C.iso(entry_ts),
            "entry_date": C.dstr(entry_ts),
            "exit_ts": exit_ts, "exit_iso": C.iso(exit_ts),
            "exit_type": exit_type,
            "hold_hours": (exit_ts - entry_ts) / C.HOUR,
            "cdo": cdo, "cdo_ts": cdo_ts,
            "up_exc_pct": up_exc, "dn_exc_pct": dn_exc,
            "entry_price": entry, "exit_price": exit_px,
            "stop_price": stop_px, "target_price": tgt_px,
            "move_pct": move_pct, "R": r,
            "first_bar_of_day": bool(t_open == day_start),
            "same_bar_stop_target": bool(ambiguous),
        })
        fired_today = True
        open_until = exit_ts

    return pd.DataFrame(trades)


# --- bootstrap of the MEAN R (same resamples the module's Sharpe bootstrap uses) ---

def mean_r_bootstrap(rs: list[float], n_iter: int = N_ITER, seed: int = SEED) -> dict:
    n = len(rs)
    rng = random.Random(seed)
    means, sharpes = [], []
    for _ in range(n_iter):
        sample = [rs[rng.randrange(n)] for _ in range(n)]
        m = sum(sample) / n
        var = sum((x - m) ** 2 for x in sample) / (n - 1)
        sd = math.sqrt(var)
        means.append(m)
        sharpes.append(m / sd if sd > 0 else 0.0)
    means.sort(); sharpes.sort()
    return {
        "n": n,
        "mean_point": sum(rs) / n,
        "mean_p05": means[int(0.05 * n_iter)],
        "mean_p50": means[int(0.50 * n_iter)],
        "mean_p95": means[int(0.95 * n_iter)],
        "p_mean_gt_0": sum(1 for x in means if x > 0) / n_iter,
        "sr_p05_replicated": sharpes[int(0.05 * n_iter)],
    }


def stats_block(df: pd.DataFrame) -> dict:
    s = C.summarize(df["R"].to_numpy() if len(df) else [])
    out = dict(s)
    if len(df):
        out["stop_pct_of_trades"] = float((df.exit_type == "stop").mean())
        out["target_pct_of_trades"] = float((df.exit_type == "target").mean())
        out["tif_pct_of_trades"] = float((df.exit_type == "tif").mean())
        out["long_share"] = float((df.direction == "LONG").mean())
    return out


def main() -> None:
    with C.ro_conn() as con:
        last_ts = int(con.execute(
            "SELECT MIN(m) FROM (SELECT MAX(open_time)/1000 AS m FROM btc_1m "
            "UNION ALL SELECT MAX(open_time)/1000 FROM eth_1m)").fetchone()[0])
    end_ts = last_ts - (last_ts % C.HOUR)
    start_ts = int(datetime.fromisoformat(C.STUDY_START)
                   .replace(tzinfo=timezone.utc).timestamp())

    frames = []
    for asset in ("BTC", "ETH"):
        bars = C.AssetBars(asset)
        tr = run_cdo(asset, bars, end_ts)
        tr = tr[tr.entry_ts >= start_ts]
        print(f"{asset}: {len(tr)} CDO trades")
        frames.append(tr)
    trades = pd.concat(frames, ignore_index=True).sort_values("entry_ts").reset_index(drop=True)
    trades.to_csv(RESULTS / "qb_trades.csv", index=False)

    R = trades["R"].tolist()
    dates = trades["entry_date"].tolist()

    # --- descriptive breakdowns ---------------------------------------------
    rows = []
    def add(label, sub):
        b = stats_block(sub)
        b["slice"] = label
        rows.append(b)
    add("pooled", trades)
    for a in ("BTC", "ETH"):
        add(f"asset={a}", trades[trades.asset == a])
    for d in ("LONG", "SHORT"):
        add(f"dir={d}", trades[trades.direction == d])
    add("pre_etf", trades[trades.entry_date < C.ETF_DATE])
    add("post_etf", trades[trades.entry_date >= C.ETF_DATE])
    for a in ("BTC", "ETH"):
        add(f"{a}_pre_etf", trades[(trades.asset == a) & (trades.entry_date < C.ETF_DATE)])
        add(f"{a}_post_etf", trades[(trades.asset == a) & (trades.entry_date >= C.ETF_DATE)])
    add("first_bar_of_day", trades[trades.first_bar_of_day])
    add("not_first_bar_of_day", trades[~trades.first_bar_of_day])
    for y in sorted({d[:4] for d in dates}):
        add(f"year={y}", trades[trades.entry_date.str.startswith(y)])
    summary = pd.DataFrame(rows)[
        ["slice", "n", "mean", "median", "sd", "win_rate", "sum", "sharpe",
         "min", "max", "stop_pct_of_trades", "target_pct_of_trades",
         "tif_pct_of_trades", "long_share"]]
    summary.to_csv(RESULTS / "qb_summary.csv", index=False)

    # --- CLAUSE B1: bootstrap CI on mean R ----------------------------------
    b1 = mean_r_bootstrap(R)
    mod_boot = bootstrap.bootstrap_sharpe(R, n_iter=N_ITER, seed=SEED,
                                          n_per_year=len(R) / ((end_ts - start_ts) / (365 * C.DAY)))
    B1 = bool(b1["mean_p05"] > 0)

    # --- CLAUSE B2: walk-forward folds --------------------------------------
    folds = gates.walk_forward_folds(dates, fit_days=730, oos_days=365, step_days=365)
    fold_rows = []
    for k, f in enumerate(folds, 1):
        vals = np.array([R[i] for i in f["oos_idx"]], dtype=float)
        span = (datetime.fromisoformat(f["oos_end"]) - datetime.fromisoformat(f["oos_start"])).days
        complete = (f["oos_end"] <= max(dates))
        fold_rows.append({
            "fold": k, "fit_start": f["fit_start"], "fit_end": f["fit_end"],
            "oos_start": f["oos_start"], "oos_end": f["oos_end"],
            "oos_window_days": span,
            "complete": bool(complete),
            "n": int(vals.size),
            "mean_R": float(vals.mean()) if vals.size else float("nan"),
            "sum_R": float(vals.sum()) if vals.size else 0.0,
            "positive": bool(vals.size and vals.mean() > 0),
        })
    folds_df = pd.DataFrame(fold_rows)
    folds_df.to_csv(RESULTS / "qb_folds.csv", index=False)
    complete_folds = folds_df[folds_df.complete].head(4)
    n_complete = int(len(complete_folds))
    n_pos = int(complete_folds["positive"].sum())
    B2_evaluable = n_complete >= 4
    B2 = bool(B2_evaluable and n_pos >= 3)

    # --- CLAUSE B3: DSR at N_TRIALS = 2 -------------------------------------
    per_year = len(R) / ((end_ts - start_ts) / (365 * C.DAY))
    d = dsr_pbo.dsr_from_returns(R, n_trials=N_TRIALS, periods_per_year=per_year)
    B3 = bool(d is not None and d["dsr"] > 0.95)

    enough = len(R) >= 30
    if not enough or not B2_evaluable:
        verdict = "INCONCLUSIVE"
    elif B1 and B2 and B3:
        verdict = "BUILD"
    else:
        verdict = "KILL"

    # supporting: same three statistics on the long-only sub-series
    long_only = trades[trades.direction == "LONG"]["R"].tolist()
    supp = {}
    if len(long_only) >= 30:
        lb = mean_r_bootstrap(long_only)
        ld = dsr_pbo.dsr_from_returns(long_only, n_trials=N_TRIALS)
        supp["long_only"] = {"n": len(long_only), "mean_R": lb["mean_point"],
                             "mean_p05": lb["mean_p05"],
                             "dsr": None if ld is None else ld["dsr"]}
    short_only = trades[trades.direction == "SHORT"]["R"].tolist()
    if len(short_only) >= 30:
        sb = mean_r_bootstrap(short_only)
        sd_ = dsr_pbo.dsr_from_returns(short_only, n_trials=N_TRIALS)
        supp["short_only"] = {"n": len(short_only), "mean_R": sb["mean_point"],
                              "mean_p05": sb["mean_p05"],
                              "dsr": None if sd_ is None else sd_["dsr"]}
    nf = trades[~trades.first_bar_of_day]["R"].tolist()
    if len(nf) >= 30:
        nb = mean_r_bootstrap(nf)
        nd = dsr_pbo.dsr_from_returns(nf, n_trials=N_TRIALS)
        supp["excluding_first_bar_of_day_POST_HOC"] = {
            "n": len(nf), "mean_R": nb["mean_point"], "mean_p05": nb["mean_p05"],
            "dsr": None if nd is None else nd["dsr"]}

    era = metrics.era_split(dates, R, cutoff=C.ETF_DATE)

    # robustness bounds (reported, not decision-bearing)
    n_amb = int(trades["same_bar_stop_target"].sum())
    # upper bound: resolve EVERY same-bar tie to the target instead of the stop
    r_opt = trades["R"].to_numpy().copy()
    amb = trades["same_bar_stop_target"].to_numpy()
    r_opt[amb] = TARGET_R - COST_R
    zero_cost_mean = float(np.mean(R)) + COST_R
    bounds = {
        "n_same_bar_stop_target_ties": n_amb,
        "mean_R_if_all_ties_were_targets": float(r_opt.mean()),
        "mean_R_at_zero_cost": zero_cost_mean,
        "flips_needed_to_reach_mean_R_zero":
            float(-np.sum(R) / (TARGET_R - COST_R - (-1.0 - COST_R))),
        "target_hit_rate": float((trades.exit_type == "target").mean()),
        "breakeven_target_hit_rate_at_18bp":
            float((1.0 + COST_R) / (TARGET_R + 1.0)),
    }

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "rules": {
            "excursion_pct": EXCURSION_PCT, "touch_tol_pct": TOUCH_TOL_PCT,
            "stop_pct_is_1R": STOP_PCT, "target_R": TARGET_R,
            "tif_hours": TIF_HOURS, "tif_cap": "day_start + 1d + 1h",
            "cost_R": COST_R, "cost_bp_rt": C.COST_BP_RT,
            "n_trials": N_TRIALS, "seed": SEED, "n_iter": N_ITER,
        },
        "n_trades": len(R),
        "mean_R": b1["mean_point"],
        "sum_R": float(np.sum(R)),
        "max_drawdown_R": float(metrics.max_drawdown(np.array(R))),
        "era_mean_R": {k: (float(np.mean(v)) if v else float("nan"))
                       for k, v in era.items()},
        "era_n": {k: len(v) for k, v in era.items()},
        "clauses": {
            "B1_mean_R_p05": b1["mean_p05"],
            "B1_mean_R_point": b1["mean_point"],
            "B1_mean_R_p95": b1["mean_p95"],
            "B1_p_mean_gt_0": b1["p_mean_gt_0"],
            "B1_module_sharpe_p05": None if mod_boot is None else mod_boot["sr_p05"],
            "B1_module_sharpe_point": None if mod_boot is None else mod_boot["sr_point"],
            "B1_replication_matches_module": (
                mod_boot is not None
                and abs(mod_boot["sr_p05"] - b1["sr_p05_replicated"]) < 1e-12),
            "B1_fired": B1,
            "B2_complete_folds": n_complete,
            "B2_positive_folds": n_pos,
            "B2_required": "3 of 4",
            "B2_fired": B2,
            "B3_dsr": None if d is None else d["dsr"],
            "B3_sr_per_trade": None if d is None else d["sr_per_obs"],
            "B3_sr_expected_max": None if d is None else d["sr_expected"],
            "B3_required": 0.95,
            "B3_fired": B3,
        },
        "verdict": verdict,
        "supporting": supp,
        "robustness_bounds": bounds,
    }
    (RESULTS / "qb_clauses.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out["clauses"], indent=2, default=str))
    print("verdict:", verdict)
    print(summary.to_string(index=False))
    print(folds_df.to_string(index=False))
    print(json.dumps(supp, indent=2, default=str))
    print(json.dumps(bounds, indent=2, default=str))


if __name__ == "__main__":
    main()

"""S-VRP runner — parity gate, deflated Sharpe, OOS accrual and the DVOL−RV
monitor for the pre-registered spec in README.md. Read-only on prod.db.

Run:  venv\\Scripts\\python studies/notebooks/vrp_study/run_vrp.py
Writes results/vrp_expiries.csv, results/vrp_summary.json, results/vrp_dvol_premium.csv.

The expiry loop is the trader repo's research/probe_vrp_strike_sensitivity.py
loop (±10% cell) on p300's tables; the P&L engine is studies/lib/options.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parents[2]
sys.path.insert(0, str(ROOT))
from studies.lib.options import chain, pnl_engine  # noqa: E402

RESULTS = _HERE / "results"
ASSET = "BTC"
STRIKE_OFFSET = 0.10
ENTRY_DAYS = 21
HEDGE_DAYS = 7
MIN_STRIKES = 5
TERMINAL = "mark"   # "mark": expiry-day USD mark (pre-registered, trader parity); "intrinsic": payoff from expiry-day spot (sensitivity only)
TRAIN =("2024-11-01", "2025-09-30")
TEST = ("2025-10-01", "2026-04-30")
TRIAL_COUNTS = {"single spec": 1, "strikes×cadence": 42, "realistic (×entry timings)": 168,
                "aggressive (full sweep)": 672}
# Trader Phase-3 reference (research/probe_vrp_dsr.py, 2026-04-2x) and gate tolerances (README Step 1)
REFERENCE = {"n": 66, "mean_pct": 1.53, "worst_pct": -7.08, "sharpe_ann": 1.88, "dsr_168": 0.890}
TOL = {"n": 3, "mean_pct": 0.20, "worst_pct": 0.50, "sharpe_ann": 0.20}


# ---- deflated Sharpe, verbatim trader dsr_pbo_wednesday.deflated_sharpe (the parity reference) ----
def _normal_inv(p: float) -> float:
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
               (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)


def deflated_sharpe_trader(rs: list[float], n_trials: int) -> dict | None:
    n = len(rs)
    mean = sum(rs) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1))
    if sd == 0:
        return None
    m3 = sum((r - mean) ** 3 for r in rs) / n
    m4 = sum((r - mean) ** 4 for r in rs) / n
    skew, kurt = m3 / sd ** 3, m4 / sd ** 4
    sr = mean / sd
    var_sr = 1.0 / (n - 1)
    if n_trials <= 1:
        sr_expected = 0.0
    else:
        em = 0.5772156649
        z1 = _normal_inv(1 - 1.0 / n_trials)
        z2 = _normal_inv(1 - 1.0 / (n_trials * math.e))
        sr_expected = math.sqrt(var_sr) * ((1 - em) * z1 + em * z2)
    denom = math.sqrt(1 - skew * sr + ((kurt - 1) / 4.0) * sr * sr)
    if denom <= 0:
        return None
    z = (sr - sr_expected) * math.sqrt(n - 1) / denom
    return dict(sr_per_trade=sr, sr_expected=sr_expected, skew=skew, kurt=kurt, n=n,
                n_trials=n_trials, dsr=0.5 * (1 + math.erf(z / math.sqrt(2))), dsr_z=z)


def _toolkit_dsr():
    """studies.lib.validation.dsr_pbo when the package imports (cross-check only)."""
    try:
        from studies.lib.validation import dsr_pbo
        return dsr_pbo
    except Exception:
        return None


# ---- expiry loop ------------------------------------------------------------------------
def run_expiries(con: sqlite3.Connection, spot: dict[str, float],
                 settle: dict[str, float] | None = None) -> list[dict]:
    """settle: {date: 08:00 UTC price} used as the settlement level in intrinsic mode
    (falls back to the day close when missing)."""
    rows = []
    for expiry_ts, expiry_date, nc, np_ in chain.find_expiries(con, ASSET, MIN_STRIKES):
        expiry_dt = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)
        entry_date = (expiry_dt - timedelta(days=ENTRY_DAYS)).date().isoformat()
        base = {"expiry": expiry_date, "expiry_ts": expiry_ts, "n_calls": nc, "n_puts": np_,
                "entry": entry_date}
        near = chain.spot_near(spot, entry_date)
        if near is None:
            rows.append(dict(base, status="no_spot"))
            continue
        entry_date, s = near
        base.update(entry=entry_date, spot_entry=s)
        pair = chain.find_strangle_pair(con, ASSET, expiry_ts, entry_date, s, STRIKE_OFFSET, STRIKE_OFFSET,
                                        require_terminal=(TERMINAL == "mark"))
        if pair is None:
            rows.append(dict(base, status="no_pair"))
            continue
        kc, kp, ci, pi, cm, pm = pair
        base.update(strike_call=kc, strike_put=kp, call_inst=ci, put_inst=pi, call_mark=cm, put_mark=pm,
                    raw_premium_pct=(cm + pm) / s * 100)
        if TERMINAL == "mark":
            tc = chain.get_terminal_value(con, ci, expiry_ts)
            tp = chain.get_terminal_value(con, pi, expiry_ts)
            if tc is None or tp is None:
                rows.append(dict(base, status="no_terminal"))
                continue
            terminal = tc + tp
        else:
            s_x = settle.get(expiry_date) if settle else None
            if s_x is None:
                near_x = chain.spot_near(spot, expiry_date)
                if near_x is None:
                    rows.append(dict(base, status="no_terminal"))
                    continue
                s_x = near_x[1]
            terminal = max(s_x - kc, 0.0) + max(kp - s_x, 0.0)
            base["spot_expiry"] = s_x
        spot_used = spot
        if TERMINAL != "mark":
            spot_used = dict(spot)
            spot_used[expiry_date] = base["spot_expiry"]     # hedge closes at settlement, not the day close
        h = pnl_engine.hedged_short_pnl(
            spot_used, expiry_ts, entry_date, kc, kp, chain.load_marks(con, ci), chain.load_marks(con, pi),
            cm, pm, terminal, hedge_freq_days=HEDGE_DAYS)
        if h is None:
            rows.append(dict(base, status="no_iv", terminal=terminal))
            continue
        if TRAIN[0] <= expiry_date <= TRAIN[1]:
            split = "TRAIN"
        elif TEST[0] <= expiry_date <= TEST[1]:
            split = "TEST"
        elif expiry_date > TEST[1]:
            split = "OOS"
        else:
            split = "PRE"
        rows.append(dict(base, status="ok", split=split, terminal=terminal,
                         pnl_pct=h["pnl_usd"] / s * 100, naked_pnl_pct=h["naked_pnl_usd"] / s * 100,
                         hedge_pnl_pct=h["hedge_pnl_usd"] / s * 100,
                         hedge_costs_pct=h["hedge_costs_usd"] / s * 100,
                         iv_call=h["iv_call_entry"], iv_put=h["iv_put_entry"],
                         n_rebal=h["n_rebalances"], n_skip=h["n_skip"]))
    return rows


def stats(rs: list[float]) -> dict | None:
    n = len(rs)
    if n < 2:
        return {"n": n, "mean_pct": (sum(rs) / n if n else None)}
    m = sum(rs) / n
    sd = math.sqrt(sum((r - m) ** 2 for r in rs) / (n - 1))
    return {"n": n, "mean_pct": m, "sd_pct": sd, "sharpe_ann": (m / sd * math.sqrt(12)) if sd > 0 else 0.0,
            "win_pct": 100.0 * sum(r > 0 for r in rs) / n, "worst_pct": min(rs), "best_pct": max(rs),
            "median_pct": sorted(rs)[n // 2]}


def fmt(s: dict | None) -> str:
    if not s or s.get("n", 0) < 2:
        return f"n={s['n'] if s else 0}"
    return (f"n={s['n']:3d} mean {s['mean_pct']:+.3f}% sd {s['sd_pct']:.3f}% Sharpe_ann {s['sharpe_ann']:+.2f} "
            f"win {s['win_pct']:.1f}% worst {s['worst_pct']:+.2f}% best {s['best_pct']:+.2f}%")


# ---- DVOL − realised-vol monitor -----------------------------------------------------------
def dvol_premium(con: sqlite3.Connection, spot_all: dict[str, float]) -> tuple[dict, list[dict]]:
    dvol = {datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat(): float(c)
            for ts, c in con.execute(
                "SELECT timestamp, close FROM deribit_dvol_daily WHERE asset=? AND close IS NOT NULL "
                "ORDER BY timestamp", (ASSET,))}
    days = sorted(spot_all)
    lr = {days[i]: math.log(spot_all[days[i]] / spot_all[days[i - 1]]) for i in range(1, len(days))}
    idx = {d: i for i, d in enumerate(days)}

    def rv(from_i: int, to_i: int) -> float | None:
        xs = [lr[days[j]] for j in range(from_i, to_i) if days[j] in lr]
        if len(xs) < 20:
            return None
        m = sum(xs) / len(xs)
        return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1) * 365) * 100

    per_month: dict[str, list[float]] = defaultdict(list)
    per_year: dict[str, list[float]] = defaultdict(list)
    for d, v in dvol.items():
        i = idx.get(d)
        if i is None or i + 30 >= len(days):
            continue
        f = rv(i + 1, i + 31)
        if f is None:
            continue
        per_month[d[:7]].append(v - f)
        per_year[d[:4]].append(v - f)
    monthly = [{"month": k, "n": len(v), "dvol_minus_fwd_rv30": sum(v) / len(v)} for k, v in sorted(per_month.items())]
    yearly = {k: {"n": len(v), "mean": sum(v) / len(v), "share_positive": sum(x > 0 for x in v) / len(v)}
              for k, v in sorted(per_year.items())}
    last_d = max(dvol) if dvol else None
    trailing = rv(idx[days[-1]] - 30, idx[days[-1]] + 1) if days else None
    return {"yearly": yearly, "latest_date": last_d, "latest_dvol": dvol.get(last_d), "trailing_rv30": trailing,
            "n_dvol_days": len(dvol)}, monthly


def main() -> int:
    global TERMINAL
    ap = argparse.ArgumentParser()
    ap.add_argument("--terminal", choices=["mark", "intrinsic"], default="mark",
                    help="expiry payoff: mark = expiry-day USD mark (pre-registered); "
                         "intrinsic = from expiry-day spot, strikes need only an entry mark (sensitivity)")
    TERMINAL = ap.parse_args().terminal
    suffix = "" if TERMINAL == "mark" else "_intrinsic"
    RESULTS.mkdir(exist_ok=True)
    spot = chain.load_spot_daily(ASSET, since="2024-09-01")          # trader parity: same start
    spot_all = chain.load_spot_daily(ASSET, since="2022-08-01")      # for the DVOL monitor
    settle = chain.load_spot_at_hour(ASSET, 8, since="2024-09-01") if TERMINAL == "intrinsic" else None
    con = chain.connect_ro()
    try:
        rows = run_expiries(con, spot, settle)
        first_live = con.execute(
            "SELECT MIN(timestamp) FROM deribit_options_daily WHERE source='deribit'").fetchone()[0]
        prem, monthly = dvol_premium(con, spot_all)
        future = [r for r in chain.find_expiries(con, ASSET, MIN_STRIKES)
                  if r[1] > datetime.now(timezone.utc).date().isoformat()]
    finally:
        con.close()

    keys = sorted({k for r in rows for k in r}, key=lambda k: (k != "expiry", k))
    with open(RESULTS / f"vrp_expiries{suffix}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    ok = [r for r in rows if r["status"] == "ok"]
    by_split = {s: [r["pnl_pct"] for r in ok if r["split"] == s] for s in ("PRE", "TRAIN", "TEST", "OOS")}
    combined = by_split["TRAIN"] + by_split["TEST"]
    st = {s: stats(v) for s, v in by_split.items()}
    st["TRAIN+TEST"] = stats(combined)

    print(f"spec: short ±{STRIKE_OFFSET:.0%} strangle, T-{ENTRY_DAYS}, {HEDGE_DAYS}d hedge, "
          f"terminal={TERMINAL}, {pnl_engine.Costs()}")
    from collections import Counter
    print("expiry statuses:", dict(Counter(r["status"] for r in rows)))
    for s in ("PRE", "TRAIN", "TEST", "TRAIN+TEST", "OOS"):
        print(f"  {s:10s} {fmt(st[s])}")

    # parity gate
    c = st["TRAIN+TEST"]
    gate = {}
    if c and c["n"] >= 2:
        gate = {"n": abs(c["n"] - REFERENCE["n"]) <= TOL["n"],
                "mean_pct": abs(c["mean_pct"] - REFERENCE["mean_pct"]) <= TOL["mean_pct"],
                "worst_pct": abs(c["worst_pct"] - REFERENCE["worst_pct"]) <= TOL["worst_pct"],
                "sharpe_ann": abs(c["sharpe_ann"] - REFERENCE["sharpe_ann"]) <= TOL["sharpe_ann"]}
    parity = bool(gate) and all(gate.values())
    if TERMINAL == "mark":
        print(f"parity gate: {'PASS' if parity else 'FAIL'} {gate}  (reference {REFERENCE})")
    else:
        print("parity gate: not applicable (sensitivity run, terminal=intrinsic)")

    # deflated Sharpe on the combined seeded sample
    dsr_rows = []
    tk = _toolkit_dsr()
    if len(combined) >= 2:
        dec = [r / 100 for r in combined]
        for label, nt in TRIAL_COUNTS.items():
            d = deflated_sharpe_trader(dec, nt)
            t = tk.dsr_from_returns(dec, nt, periods_per_year=12) if tk else None
            dsr_rows.append({"scenario": label, "n_trials": nt, "dsr": d["dsr"] if d else None,
                             "dsr_z": d["dsr_z"] if d else None,
                             "dsr_toolkit": t["dsr"] if t else None})
        print("DSR (trader formula | toolkit):")
        for r in dsr_rows:
            tkv = f"{r['dsr_toolkit']:.4f}" if r["dsr_toolkit"] is not None else "n/a"
            print(f"  {r['scenario']:28s} N={r['n_trials']:4d}  DSR {r['dsr']:.4f} (z {r['dsr_z']:+.3f}) | {tkv}")
    dsr168 = next((r["dsr"] for r in dsr_rows if r["n_trials"] == 168), None)

    # per-year table
    per_year = defaultdict(list)
    for r in ok:
        per_year[r["expiry"][:4]].append(r["pnl_pct"])
    year_tbl = {y: stats(v) for y, v in sorted(per_year.items())}
    print("per expiry-year:")
    for y, s in year_tbl.items():
        print(f"  {y}: {fmt(s)}")

    # OOS accrual
    first_live_date = datetime.fromtimestamp(first_live, tz=timezone.utc).date().isoformat() if first_live else None
    evaluable = [r[1] for r in future
                 if first_live_date and (datetime.fromisoformat(r[1]) - timedelta(days=ENTRY_DAYS)).date().isoformat() >= first_live_date]
    oos = {"n_oos_expiries": len(by_split["OOS"]), "first_live_snapshot": first_live_date,
           "seed_last_mark_day": max((r["expiry"] for r in ok), default=None),
           "first_evaluable_expiry": evaluable[0] if evaluable else None,
           "n_future_expiries_listed": len(future)}
    print("OOS:", oos)

    # DVOL monitor
    print("DVOL − forward 30d realised vol, by year (vol points):")
    for y, v in prem["yearly"].items():
        print(f"  {y}: mean {v['mean']:+.1f}  positive {v['share_positive']:.0%}  n={v['n']}")
    print(f"  latest {prem['latest_date']}: DVOL {prem['latest_dvol']}, trailing RV30 "
          f"{prem['trailing_rv30'] and round(prem['trailing_rv30'], 1)}")
    with open(RESULTS / "vrp_dvol_premium.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["month", "n", "dvol_minus_fwd_rv30"])
        w.writeheader()
        w.writerows(monthly)

    # decision rule (README)
    kill = []
    if st["OOS"] and st["OOS"]["n"] >= 6 and st["OOS"]["mean_pct"] <= 0:
        kill.append("OOS mean ≤ 0 at n ≥ 6")
    if any(r["pnl_pct"] < -10 for r in ok):
        kill.append("an expiry < −10%")
    if dsr168 is not None and dsr168 < 0.90:
        kill.append(f"DSR@168 {dsr168:.3f} < 0.90")
    if TERMINAL != "mark":
        verdict = f"SENSITIVITY (terminal=intrinsic, not pre-registered) — kill clauses hit: {kill or 'none'}"
    elif not parity:
        verdict = "PARITY FAIL — port must be debugged before any verdict"
    elif kill:
        verdict = "KILL: " + "; ".join(kill)
    else:
        verdict = "SURVIVES the seeded-sample clauses; OOS clause not yet evaluable"
    print("verdict:", verdict)

    summary = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "spec": {"asset": ASSET, "strike_offset": STRIKE_OFFSET, "entry_days": ENTRY_DAYS,
                        "hedge_days": HEDGE_DAYS, "terminal": TERMINAL, "costs": pnl_engine.Costs().__dict__,
                        "train": TRAIN, "test": TEST},
               "statuses": dict(Counter(r["status"] for r in rows)),
               "stats": st, "parity": {"pass": parity, "checks": gate, "reference": REFERENCE, "tolerance": TOL},
               "dsr": dsr_rows, "dsr_168": dsr168, "per_year": year_tbl, "oos": oos,
               "dvol_premium": prem, "kill_clauses_hit": kill, "verdict": verdict}
    (RESULTS / f"vrp_summary{suffix}.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print("wrote", RESULTS / f"vrp_summary{suffix}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

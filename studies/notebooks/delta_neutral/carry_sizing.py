"""Sub-study (iii) — carry-crash sizing.

Replays the S-078 Carry sleeve's own decision code over the full Binance funding
history, with and without the pre-registered rule "cut position size 50% when the
trailing 60-day funding z-score exceeds its expanding 95th percentile".

Nothing under strategies/** or bots/** is modified. prod.db is opened mode=ro.
The sleeve's signal functions are IMPORTED, not reimplemented; the P&L constants
come from strategies/trades.py and bots/carry/config.py.

Run:  python studies/notebooks/delta_neutral/carry_sizing.py
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from strategies.support import db  # noqa: E402
from studies.lib.validation import bootstrap, dsr_pbo, metrics  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)

SETTLEMENT = 8 * 3600
SEED = 42

# ── sleeve / bot constants, imported from the live code ──────────────────────
from bots.carry.strategy.config import (  # noqa: E402
    FR_WINDOW_DAYS, FR_ENTRY_THRESHOLD, EXIT_NEG_DAYS,
)
from bots.carry.strategy.signal import _evaluate_today  # noqa: E402
from strategies.trades import CARRY_COST_PCT, CARRY_SLIPPAGE_PCT  # noqa: E402
from bots.carry.config import CAPITAL_USDT, CARRY_NOTIONAL_X  # noqa: E402

ROUND_TRIP_PCT = CARRY_COST_PCT + CARRY_SLIPPAGE_PCT      # 0.24 %
ONE_WAY_PCT = ROUND_TRIP_PCT / 2.0                         # 0.12 %
RESIZE_FRACTION = 0.5                                      # cut to 50 %
RESIZE_COST_PCT = ONE_WAY_PCT * RESIZE_FRACTION            # 0.06 % per transition

# Pre-registered primary cell + sensitivity grid.
PRIMARY_WINDOW = 60
PRIMARY_PCTILE = 95
GRID_WINDOWS = (30, 60, 90)
GRID_PCTILES = (90, 95, 99)
P95_WARMUP = 252          # z observations required before the percentile is defined


def ro_connect() -> sqlite3.Connection:
    return sqlite3.connect("file:" + str(db.PROD_DB) + "?mode=ro", uri=True)


def _utc_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _midnight_ts(date_iso: str) -> int:
    return int(datetime.strptime(date_iso, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp())


# ── 1. daily funding series, read-only, identical aggregation to funding.py ──

def load_settlements(con: sqlite3.Connection, table: str) -> list[tuple[int, float]]:
    """(timestamp, fr_close) for every on-grid settlement, oldest first.

    Same filter as strategies.support.funding: `timestamp % 28800 = 0` so each
    settlement is counted exactly once regardless of stored cadence (the
    pre-2026-04 rows are hourly bars; the post-cutover rows are 8-hourly).
    """
    rows = con.execute(
        f"SELECT timestamp, fr_close FROM {table} "
        f"WHERE fr_close IS NOT NULL AND timestamp % ? = 0 ORDER BY timestamp",
        (SETTLEMENT,),
    ).fetchall()
    return [(int(t), float(v)) for t, v in rows]


def daily_sums_pct_ro(settlements: list[tuple[int, float]]) -> dict[str, float]:
    """{date: sum-of-3-settlements * 100}, complete days only — the read-only
    twin of funding.daily_sums_pct(complete_only=True)."""
    agg: dict[str, list[float]] = {}
    for ts, v in settlements:
        agg.setdefault(_utc_date(ts), []).append(v)
    return {d: sum(vs) * 100.0 for d, vs in agg.items() if len(vs) >= 3}


def assert_byte_equivalent(settlements: list[tuple[int, float]],
                           mine: dict[str, float]) -> dict:
    """Verify our aggregation against the LIVE funding.daily_sums_pct.

    prod.db must not be opened read-write, and funding.py opens db.TRADER_DB
    with a plain rw connection, so the live function is pointed at a scratch
    copy holding exactly the rows we read. Any date mismatch aborts the run.
    """
    import tempfile
    from strategies.support import funding as live_funding

    tmpdir = Path(tempfile.mkdtemp(prefix="carry_equiv_"))
    tmp_db = tmpdir / "equiv.db"
    scon = sqlite3.connect(str(tmp_db))
    scon.execute("CREATE TABLE cd_funding_rate (timestamp INTEGER PRIMARY KEY, "
                 "fr_open REAL, fr_high REAL, fr_low REAL, fr_close REAL)")
    scon.executemany("INSERT INTO cd_funding_rate(timestamp, fr_close) VALUES (?,?)",
                     settlements)
    scon.commit()
    scon.close()

    saved = db.TRADER_DB
    try:
        db.TRADER_DB = tmp_db
        theirs = live_funding.daily_sums_pct(
            "BTC", settlements[0][0] - 1, settlements[-1][0] + 1,
            complete_only=True)
        # spot-check accrued_pct on three windows spanning the cadence cutover
        probes = []
        for frac in (0.10, 0.55, 0.97):
            i = int(len(settlements) * frac)
            a = datetime.fromtimestamp(settlements[i][0], timezone.utc)
            b = datetime.fromtimestamp(settlements[min(i + 30, len(settlements) - 1)][0],
                                       timezone.utc)
            live = live_funding.accrued_pct("BTC", a, b, "SHORT")
            ours = sum(v for ts, v in settlements
                       if a.timestamp() < ts <= b.timestamp()) * 100.0
            probes.append({"from": a.isoformat(), "to": b.isoformat(),
                           "live": live, "ours": ours, "abs_diff": abs(live - ours)})
    finally:
        db.TRADER_DB = saved

    if set(theirs) != set(mine):
        raise SystemExit(f"BYTE-EQUIVALENCE FAILED: date sets differ "
                         f"({len(theirs)} live vs {len(mine)} ours)")
    worst = max(abs(theirs[d] - mine[d]) for d in mine) if mine else 0.0
    if worst > 1e-12:
        raise SystemExit(f"BYTE-EQUIVALENCE FAILED: max |diff| = {worst:g}")
    worst_probe = max(p["abs_diff"] for p in probes)
    if worst_probe > 1e-12:
        raise SystemExit(f"accrued_pct equivalence FAILED: {worst_probe:g}")
    return {"n_days": len(mine), "max_abs_diff_daily_pct": worst,
            "accrued_pct_probes": probes}


def load_daily_close(con: sqlite3.Connection, table: str) -> dict[str, float]:
    """{date: last close of that UTC day} — the sleeve's own construction."""
    out: dict[str, float] = {}
    for ts, c in con.execute(
            f"SELECT timestamp, close FROM {table} WHERE close IS NOT NULL "
            f"ORDER BY timestamp"):
        out[_utc_date(int(ts))] = float(c)
    return out


# ── 2. the sizing rule (fully causal) ────────────────────────────────────────

def rule_multipliers(dates: list[str], funding: np.ndarray,
                     window: int, pctile: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Size multiplier per decision day D, using only information through D-1.

    z_d   = (f_d - mean(f[d-window+1..d])) / sd(same)          [trailing z]
    P_d   = expanding `pctile`-th percentile of {z_s : s <= d}, defined once
            P95_WARMUP z values exist.
    fires on day D iff z_{D-1} > P_{D-1}.

    Returns (multiplier per day, z per day, expanding percentile per day) —
    the latter two aligned to the day they are computed on (index d), not to
    the day they act on.
    """
    n = len(dates)
    z = np.full(n, np.nan)
    for i in range(window - 1, n):
        w = funding[i - window + 1: i + 1]
        sd = float(w.std(ddof=1))
        if sd > 0:
            z[i] = (funding[i] - float(w.mean())) / sd
    pct = np.full(n, np.nan)
    seen: list[float] = []
    for i in range(n):
        if math.isfinite(z[i]):
            seen.append(float(z[i]))
        if len(seen) >= P95_WARMUP:
            pct[i] = float(np.percentile(np.asarray(seen), pctile))
    mult = np.ones(n)
    for i in range(1, n):
        if math.isfinite(z[i - 1]) and math.isfinite(pct[i - 1]) and z[i - 1] > pct[i - 1]:
            mult[i] = RESIZE_FRACTION
    return mult, z, pct


# ── 3. sleeve-driven replay ──────────────────────────────────────────────────

def replay(records: list[dict], settlements: list[tuple[int, float]],
           mult_by_date: dict[str, float] | None,
           resize_cost: bool = True) -> dict:
    """Drive the sleeve's own state machine day by day.

    Decision instant for records[i] is midnight UTC of the day AFTER
    records[i]["date"] — the earliest moment the live sleeve, which excludes
    "today" from its window, can see records[i] as its last complete day.

    Funding accrues settlement by settlement over (entry_ts, exit_ts], exactly
    as strategies.support.funding.accrued_pct does, and each settlement is
    collected at that calendar day's size multiplier.
    """
    dates = [r["date"] for r in records]
    date_index = {d: i for i, d in enumerate(dates)}
    # decision day for record i (the day the sleeve acts on)
    decision_date = [
        (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        for d in dates
    ]
    decision_ts = [_midnight_ts(d) for d in decision_date]

    settle_by_ts = dict(settlements)
    settle_ts_sorted = [t for t, _ in settlements]

    daily_pnl: dict[str, float] = {}   # decision-calendar day -> % of capital
    trades: list[dict] = []

    open_trade: dict | None = None
    for i in range(FR_WINDOW_DAYS, len(records)):
        sig = _evaluate_today(records[: i + 1])
        if sig is None:
            continue
        d_ts = decision_ts[i]
        d_day = decision_date[i]

        if open_trade is not None and sig["exit_trigger"]:
            _accrue(open_trade, d_ts, settle_ts_sorted, settle_by_ts,
                    mult_by_date, daily_pnl, resize_cost)
            size_at_exit = open_trade["size"]
            daily_pnl[d_day] = daily_pnl.get(d_day, 0.0) - ONE_WAY_PCT * size_at_exit
            open_trade["exit_ts"] = d_ts
            open_trade["exit_date"] = d_day
            open_trade["exit_reason"] = f"neg_streak={sig['neg_streak_days']}d"
            open_trade["cost_pct"] = open_trade["cost_paid"] + ONE_WAY_PCT * size_at_exit
            open_trade["net_pct"] = open_trade["funding_pct"] - open_trade["cost_pct"]
            trades.append(open_trade)
            open_trade = None
            continue

        if open_trade is None and sig["entry_ok"] and not sig["exit_trigger"]:
            daily_pnl[d_day] = daily_pnl.get(d_day, 0.0) - ONE_WAY_PCT
            open_trade = {"entry_ts": d_ts, "entry_date": d_day,
                          "entry_price": sig["spot_close"],
                          "fr_7d_avg_pct": sig["fr_7d_avg_pct"],
                          "funding_pct": 0.0, "cost_paid": ONE_WAY_PCT,
                          "size": 1.0, "cursor": d_ts, "resizes": 0,
                          "days_at_half": 0, "days_open": 0}
            continue

        if open_trade is not None:
            _accrue(open_trade, d_ts, settle_ts_sorted, settle_by_ts,
                    mult_by_date, daily_pnl, resize_cost)

    # mark-to-date close of any still-open trade at the last decision instant
    if open_trade is not None:
        last_ts = decision_ts[len(records) - 1]
        _accrue(open_trade, last_ts, settle_ts_sorted, settle_by_ts,
                mult_by_date, daily_pnl, resize_cost)
        last_day = decision_date[len(records) - 1]
        daily_pnl[last_day] = daily_pnl.get(last_day, 0.0) - ONE_WAY_PCT * open_trade["size"]
        open_trade["exit_ts"] = last_ts
        open_trade["exit_date"] = last_day
        open_trade["exit_reason"] = "end_of_sample_mark"
        open_trade["cost_pct"] = open_trade["cost_paid"] + ONE_WAY_PCT * open_trade["size"]
        open_trade["net_pct"] = open_trade["funding_pct"] - open_trade["cost_pct"]
        trades.append(open_trade)

    first_day = decision_date[FR_WINDOW_DAYS]
    last_day = decision_date[len(records) - 1]
    all_days = _day_range(first_day, last_day)
    series = np.array([daily_pnl.get(d, 0.0) for d in all_days])
    return {"dates": all_days, "daily_pct": series, "trades": trades,
            "date_index": date_index}


def _accrue(trade: dict, upto_ts: int, settle_ts_sorted, settle_by_ts,
            mult_by_date, daily_pnl, resize_cost: bool) -> None:
    """Collect every settlement in (cursor, upto_ts] at that day's multiplier,
    charging a resize cost whenever the multiplier changes."""
    lo, hi = trade["cursor"], upto_ts
    if hi <= lo:
        return
    import bisect
    i0 = bisect.bisect_right(settle_ts_sorted, lo)
    i1 = bisect.bisect_right(settle_ts_sorted, hi)
    for k in range(i0, i1):
        ts = settle_ts_sorted[k]
        day = _utc_date(ts)
        m = 1.0 if mult_by_date is None else mult_by_date.get(day, 1.0)
        if m != trade["size"]:
            if resize_cost:
                cost = RESIZE_COST_PCT
                daily_pnl[day] = daily_pnl.get(day, 0.0) - cost
                trade["cost_paid"] += cost
            trade["size"] = m
            trade["resizes"] += 1
        pnl = settle_by_ts[ts] * 100.0 * m
        daily_pnl[day] = daily_pnl.get(day, 0.0) + pnl
        trade["funding_pct"] += pnl
    trade["cursor"] = hi


def _day_range(a: str, b: str) -> list[str]:
    d0 = datetime.strptime(a, "%Y-%m-%d")
    d1 = datetime.strptime(b, "%Y-%m-%d")
    return [(d0 + timedelta(days=k)).strftime("%Y-%m-%d")
            for k in range((d1 - d0).days + 1)]


# ── 4. metrics ───────────────────────────────────────────────────────────────

def arm_stats(daily_pct: np.ndarray) -> dict:
    r = np.asarray(daily_pct, dtype=float)
    ann_ret = float(r.mean() * 365.0)
    mdd = float(metrics.max_drawdown(r))
    sh = float(metrics.daily_sharpe(r, 365.0))
    return {"n_days": int(r.size), "total_pct": float(r.sum()),
            "ann_return_pct": ann_ret, "max_dd_pct": mdd,
            "mar": (ann_ret / mdd) if mdd > 0 else float("inf"),
            "sharpe_ann": sh, "mean_daily_pct": float(r.mean()),
            "sd_daily_pct": float(r.std(ddof=1)) if r.size > 1 else 0.0}


def paired_block_bootstrap_sharpe_diff(a: np.ndarray, b: np.ndarray,
                                       block: int = 20, n_iter: int = 5000,
                                       seed: int = SEED) -> dict:
    """Percentile CI of Sharpe(a) - Sharpe(b) under a shared circular-block
    resample (the pairing is what makes the difference CI meaningful)."""
    a = np.asarray(a, float); b = np.asarray(b, float)
    T = a.size
    rng = np.random.default_rng(seed)
    out = np.empty(n_iter)
    for i in range(n_iter):
        idx = bootstrap.circular_block_indices(T, T, block, rng)
        out[i] = metrics.daily_sharpe(a[idx], 365.0) - metrics.daily_sharpe(b[idx], 365.0)
    p05, p50, p95 = (float(q) for q in np.quantile(out, (0.05, 0.5, 0.95)))
    return {"diff_point": float(metrics.daily_sharpe(a, 365.0)
                                - metrics.daily_sharpe(b, 365.0)),
            "p05": p05, "p50": p50, "p95": p95,
            "p_gt_0": float((out > 0).mean()), "n_iter": n_iter, "block": block}


# ── 5. public entry point (dispersion.py imports this) ───────────────────────

_CACHE: dict | None = None


def build_carry_series() -> dict:
    """Baseline (no-rule) Carry replay. Cached so dispersion.py is cheap."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    con = ro_connect()
    try:
        settlements = load_settlements(con, "cd_funding_rate")
        funding_by_day = daily_sums_pct_ro(settlements)
        spot = load_daily_close(con, "cd_spot_binance")
        perp = load_daily_close(con, "cd_futures_ohlcv")
    finally:
        con.close()
    days = sorted(set(funding_by_day) & set(spot) & set(perp))
    records = [{"date": d, "daily_funding_pct": funding_by_day[d],
                "spot_close": spot[d], "perp_close": perp[d]} for d in days]
    base = replay(records, settlements, None, resize_cost=False)
    _CACHE = {"records": records, "settlements": settlements, "base": base,
              "funding_by_day": funding_by_day}
    return _CACHE


def carry_benchmark(start_ts: int, end_ts: int) -> dict:
    """Realised Carry over [start_ts, end_ts] — clause I-2's benchmark.

    (a) sleeve-faithful: the baseline replay's daily series restricted to the
        window, annualised additively.
    (b) context: raw Binance funding sum over the window, no filter, no cost.
    """
    c = build_carry_series()
    base = c["base"]
    a_day = _utc_date(start_ts)
    b_day = _utc_date(end_ts)
    mask = [(a_day <= d <= b_day) for d in base["dates"]]
    r = base["daily_pct"][np.array(mask)]
    st = arm_stats(r) if r.size else {"ann_return_pct": float("nan"), "n_days": 0}
    raw = sum(v for ts, v in c["settlements"] if start_ts <= ts <= end_ts) * 100.0
    n_days = max((end_ts - start_ts) / 86400.0, 1.0)
    return {"sleeve_faithful": st,
            "raw_funding_ann_pct": raw * 365.0 / n_days,
            "raw_funding_total_pct": raw,
            "window": [a_day, b_day], "window_days": n_days}


# ── 6. main ──────────────────────────────────────────────────────────────────

def main() -> int:
    import csv
    print("loading funding + price series (mode=ro) ...")
    c = build_carry_series()
    records, settlements = c["records"], c["settlements"]
    print(f"  {len(settlements)} on-grid settlements, {len(records)} usable days "
          f"({records[0]['date']} -> {records[-1]['date']})")

    print("byte-equivalence vs strategies.support.funding ...")
    eq = assert_byte_equivalent(settlements, c["funding_by_day"])
    print(f"  OK — {eq['n_days']} days, max |diff| = {eq['max_abs_diff_daily_pct']:g}; "
          f"accrued_pct probes max |diff| = "
          f"{max(p['abs_diff'] for p in eq['accrued_pct_probes']):g}")

    dates = [r["date"] for r in records]
    fseries = np.array([r["daily_funding_pct"] for r in records])

    base = c["base"]
    base_stats = arm_stats(base["daily_pct"])
    print(f"  baseline: {base_stats['n_days']} days, ann {base_stats['ann_return_pct']:.2f}%, "
          f"Sharpe {base_stats['sharpe_ann']:.3f}, MDD {base_stats['max_dd_pct']:.2f}%, "
          f"{len(base['trades'])} trades")

    # ---- grid -------------------------------------------------------------
    grid_rows = []
    primary = None
    for w in GRID_WINDOWS:
        for p in GRID_PCTILES:
            mult, z, pct = rule_multipliers(dates, fseries, w, p)
            mult_by_date = {d: float(m) for d, m in zip(dates, mult)}
            for rcost in (True, False):
                rep = replay(records, settlements, mult_by_date, resize_cost=rcost)
                st = arm_stats(rep["daily_pct"])
                # rule-eligible sub-window: from the first day the percentile exists
                elig_from = next((dates[i] for i in range(len(dates))
                                  if math.isfinite(pct[i])), None)
                if elig_from is not None:
                    m2 = np.array([d >= elig_from for d in rep["dates"]])
                    st_e = arm_stats(rep["daily_pct"][m2])
                    bs_e = arm_stats(base["daily_pct"][m2])
                else:
                    st_e = bs_e = {"sharpe_ann": float("nan"), "mar": float("nan"),
                                   "ann_return_pct": float("nan"),
                                   "max_dd_pct": float("nan"), "n_days": 0}
                # days the rule fires while a position is open
                open_days = _open_day_set(rep["trades"])
                fire_days = {d for d, m in mult_by_date.items() if m < 1.0}
                n_fire_open = len(fire_days & open_days)
                row = {"window": w, "pctile": p, "resize_cost": rcost,
                       "n_fire_days": len(fire_days), "n_fire_open_days": n_fire_open,
                       "sharpe": st["sharpe_ann"], "sharpe_base": base_stats["sharpe_ann"],
                       "sharpe_uplift": st["sharpe_ann"] - base_stats["sharpe_ann"],
                       "mar": st["mar"], "mar_base": base_stats["mar"],
                       "ann_return_pct": st["ann_return_pct"],
                       "ann_return_base_pct": base_stats["ann_return_pct"],
                       "max_dd_pct": st["max_dd_pct"],
                       "max_dd_base_pct": base_stats["max_dd_pct"],
                       "elig_from": elig_from,
                       "sharpe_elig": st_e["sharpe_ann"],
                       "sharpe_base_elig": bs_e["sharpe_ann"],
                       "sharpe_uplift_elig": st_e["sharpe_ann"] - bs_e["sharpe_ann"],
                       "mar_elig": st_e["mar"], "mar_base_elig": bs_e["mar"],
                       "n_days_elig": st_e["n_days"]}
                grid_rows.append(row)
                if w == PRIMARY_WINDOW and p == PRIMARY_PCTILE and rcost:
                    primary = {"row": row, "rep": rep, "mult": mult, "z": z,
                               "pct": pct, "elig_from": elig_from,
                               "stats_full": st, "stats_elig": st_e,
                               "base_elig": bs_e}
                if w == PRIMARY_WINDOW and p == PRIMARY_PCTILE and not rcost:
                    primary_nocost = row
    assert primary is not None

    # ---- bootstrap + DSR on the primary cell ------------------------------
    print("bootstrapping primary cell (60d, p95, resize cost on) ...")
    with_r = primary["rep"]["daily_pct"]
    diff_full = paired_block_bootstrap_sharpe_diff(with_r, base["daily_pct"])
    m2 = np.array([d >= primary["elig_from"] for d in primary["rep"]["dates"]])
    diff_elig = paired_block_bootstrap_sharpe_diff(with_r[m2], base["daily_pct"][m2])
    bs_with = bootstrap.block_bootstrap_sharpe(with_r, block=20, n_iter=5000,
                                               seed=SEED, periods_per_year=365)
    bs_base = bootstrap.block_bootstrap_sharpe(base["daily_pct"], block=20,
                                               n_iter=5000, seed=SEED,
                                               periods_per_year=365)
    dsr = {}
    for nt in (1, 9, 18):
        dsr[f"with_n{nt}"] = dsr_pbo.dsr_from_returns(with_r, nt, periods_per_year=365)
        dsr[f"base_n{nt}"] = dsr_pbo.dsr_from_returns(base["daily_pct"], nt,
                                                      periods_per_year=365)

    # ---- clause evaluation -------------------------------------------------
    row = primary["row"]
    clauses = {
        "III-0": {"desc": "rule fires on <30 days overlapping an open position",
                  "value": row["n_fire_open_days"], "threshold": 30,
                  "fired": row["n_fire_open_days"] < 30},
        "III-1": {"desc": "Sharpe uplift < 0.10 (full window)",
                  "value": row["sharpe_uplift"], "threshold": 0.10,
                  "fired": row["sharpe_uplift"] < 0.10},
        "III-1e": {"desc": "Sharpe uplift < 0.10 (rule-eligible window)",
                   "value": row["sharpe_uplift_elig"], "threshold": 0.10,
                   "fired": row["sharpe_uplift_elig"] < 0.10},
        "III-2": {"desc": "MAR not improved (full window)",
                  "value": row["mar"], "threshold": row["mar_base"],
                  "fired": row["mar"] <= row["mar_base"]},
        "III-2e": {"desc": "MAR not improved (rule-eligible window)",
                   "value": row["mar_elig"], "threshold": row["mar_base_elig"],
                   "fired": row["mar_elig"] <= row["mar_base_elig"]},
        "III-3": {"desc": "90% paired block-bootstrap CI of Sharpe diff includes 0",
                  "value": [diff_full["p05"], diff_full["p95"]], "threshold": 0.0,
                  "fired": diff_full["p05"] <= 0.0 <= diff_full["p95"]},
        "III-4": {"desc": "DSR of with-rule daily series at n_trials=9 < 0.95",
                  "value": dsr["with_n9"]["dsr"] if dsr["with_n9"] else None,
                  "threshold": 0.95,
                  "fired": (dsr["with_n9"] is None or dsr["with_n9"]["dsr"] < 0.95)},
    }
    if clauses["III-0"]["fired"]:
        verdict = "INCONCLUSIVE — clause III-0 (power): rule fires on too few open days"
    elif clauses["III-1"]["fired"] or clauses["III-2"]["fired"]:
        verdict = "KILL"
    else:
        verdict = "BUILD-CANDIDATE"

    out = {"verdict": verdict, "clauses": clauses,
           "byte_equivalence": eq,
           "constants": {"CARRY_COST_PCT": CARRY_COST_PCT,
                         "CARRY_SLIPPAGE_PCT": CARRY_SLIPPAGE_PCT,
                         "round_trip_pct": ROUND_TRIP_PCT,
                         "resize_cost_pct_per_transition": RESIZE_COST_PCT,
                         "CAPITAL_USDT": CAPITAL_USDT,
                         "CARRY_NOTIONAL_X": CARRY_NOTIONAL_X,
                         "FR_WINDOW_DAYS": FR_WINDOW_DAYS,
                         "FR_ENTRY_THRESHOLD": FR_ENTRY_THRESHOLD,
                         "EXIT_NEG_DAYS": EXIT_NEG_DAYS,
                         "P95_WARMUP": P95_WARMUP},
           "window": {"first_day": base["dates"][0], "last_day": base["dates"][-1],
                      "n_days": len(base["dates"]),
                      "rule_eligible_from": primary["elig_from"],
                      "n_days_eligible": row["n_days_elig"]},
           "baseline": base_stats,
           "with_rule": primary["stats_full"],
           "with_rule_elig": primary["stats_elig"],
           "baseline_elig": primary["base_elig"],
           "primary_no_resize_cost": primary_nocost,
           "bootstrap": {"sharpe_diff_full": diff_full,
                         "sharpe_diff_eligible": diff_elig,
                         "with_rule": bs_with, "baseline": bs_base},
           "dsr": {k: v for k, v in dsr.items()},
           "n_trades_baseline": len(base["trades"]),
           "n_trades_with_rule": len(primary["rep"]["trades"])}

    (RESULTS / "carry_summary.json").write_text(json.dumps(out, indent=2, default=str))

    with open(RESULTS / "carry_grid.csv", "w", newline="") as f:
        wtr = csv.DictWriter(f, fieldnames=list(grid_rows[0].keys()))
        wtr.writeheader(); wtr.writerows(grid_rows)

    with open(RESULTS / "carry_daily.csv", "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["date", "ret_base_pct", "ret_rule_pct", "size_mult"])
        mbd = {d: float(m) for d, m in zip(dates, primary["mult"])}
        for d, rb, rr in zip(base["dates"], base["daily_pct"], with_r):
            wtr.writerow([d, f"{rb:.8f}", f"{rr:.8f}", mbd.get(d, 1.0)])

    with open(RESULTS / "carry_trades.csv", "w", newline="") as f:
        keys = ["entry_date", "exit_date", "exit_reason", "entry_price",
                "fr_7d_avg_pct", "funding_pct", "cost_pct", "net_pct", "resizes"]
        wtr = csv.writer(f); wtr.writerow(["arm"] + keys)
        for arm, reps in (("base", base["trades"]),
                          ("rule", primary["rep"]["trades"])):
            for t in reps:
                wtr.writerow([arm] + [t.get(k) for k in keys])

    with open(RESULTS / "carry_zscore.csv", "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["date", "daily_funding_pct", "z60", "expanding_p95", "fires_next_day"])
        z, pct, mult = primary["z"], primary["pct"], primary["mult"]
        for i, d in enumerate(dates):
            fires = (i + 1 < len(dates) and mult[i + 1] < 1.0)
            wtr.writerow([d, f"{fseries[i]:.6f}",
                          "" if not math.isfinite(z[i]) else f"{z[i]:.4f}",
                          "" if not math.isfinite(pct[i]) else f"{pct[i]:.4f}",
                          int(fires)])

    print(f"\nVERDICT (iii): {verdict}")
    for k, v in clauses.items():
        print(f"  {k}: {v['desc']} -> value={v['value']} fired={v['fired']}")
    print(f"wrote {RESULTS}")
    return 0


def _open_day_set(trades: list[dict]) -> set[str]:
    out: set[str] = set()
    for t in trades:
        out |= set(_day_range(t["entry_date"], t["exit_date"]))
    return out


if __name__ == "__main__":
    sys.exit(main())

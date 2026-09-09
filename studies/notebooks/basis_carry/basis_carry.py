"""S-DN(ii) cash-and-carry basis study -- deterministic, read-only on prod.db.

Runs the frozen rules in README.md.  Writes every number findings.md quotes
into results/.  No writes to prod.db, nothing under strategies/ or bots/ is
touched or imported for mutation.

Run:  python studies/notebooks/basis_carry/basis_carry.py

Structure
---------
* clauses D1 / D2 / D3 -- the pre-registered data-validity gates, evaluated
  exactly as written in README.md section 5.  D3's outcome is reported as
  measured; it is NOT re-specified.
* the pre-registered PRIMARY: 8% trigger, CURRENT_QUARTER, BTC+ETH pooled.
* robustness runs added AFTER D3 was seen to fail, labelled as such:
    - primary_alt : identical frozen rules, but BOTH assets' perp hourly close
                    built from the 15m tables (the series a third source,
                    tv_btc_perp_1h, shows to be the correct one).
    - btc_only / eth_only splits of the pre-registered primary.
  These exist to bound the impact of the D3 failure.  They do not move any
  threshold and they do not change the pre-registered decision rule.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from strategies.support import db  # noqa: E402
from studies.lib.validation import bootstrap, dsr_pbo, metrics  # noqa: E402

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(exist_ok=True)

HOUR = 3600
DAY = 86400
SETTLE = 28800                      # 8h funding settlement grid, epoch-aligned
FEE_PER_LEG_SIDE = 0.0005           # 5 bp per leg per side -> 20 bp round trip
MIN_DTD = 7.0                       # days-to-delivery floor for entry candidacy
TRIGGERS = (0.08, 0.06, 0.05)       # 0.08 is the PRE-REGISTERED rule
PRIMARY_TRIGGER = 0.08
PRIMARY_SLOT = "CURRENT_QUARTER"
N_TRIALS = 12                       # 3 triggers x 2 assets x 2 slots
KILL_R_ANN = 0.05
BOOT_ITER = 10_000
SEED = 42

ASSETS = {
    "BTC": dict(pair="BTCUSDT", perp_1h="cd_futures_ohlcv", perp_15m="cd_futures_15m",
                funding="cd_funding_rate"),
    "ETH": dict(pair="ETHUSDT", perp_1h=None, perp_15m="cd_futures_eth_15m",
                funding="cd_funding_rate_eth"),
}


def uts(ts) -> str:
    if ts is None or (isinstance(ts, float) and not math.isfinite(ts)):
        return ""
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def connect() -> sqlite3.Connection:
    return sqlite3.connect("file:" + str(db.PROD_DB) + "?mode=ro", uri=True)


# --------------------------------------------------------------------------
# 4a. delivery calendar
# --------------------------------------------------------------------------

def last_friday(year: int, month: int) -> dt.date:
    d = dt.date(year, month, calendar.monthrange(year, month)[1])
    while d.weekday() != 4:      # Friday
        d -= dt.timedelta(days=1)
    return d


def delivery_calendar(y0: int = 2020, y1: int = 2027) -> list[int]:
    out = []
    for y in range(y0, y1 + 1):
        for m in (3, 6, 9, 12):
            d = last_friday(y, m)
            out.append(int(dt.datetime(d.year, d.month, d.day, 8, 0,
                                       tzinfo=dt.timezone.utc).timestamp()))
    return sorted(out)


CAL = delivery_calendar()
CAL_ARR = np.array(CAL, dtype="int64")


def delivery_for_array(ts: np.ndarray, slot: str) -> np.ndarray:
    """Vectorised: CURRENT_QUARTER -> first delivery >= ts, NEXT_QUARTER -> second."""
    k = 0 if slot == "CURRENT_QUARTER" else 1
    idx = np.searchsorted(CAL_ARR, ts, side="left") + k
    out = np.full(len(ts), -1, dtype="int64")
    ok = idx < len(CAL_ARR)
    out[ok] = CAL_ARR[idx[ok]]
    return out


# --------------------------------------------------------------------------
# clause D1 -- calendar vs the 4 ground-truth rows
# --------------------------------------------------------------------------

def clause_d1(con) -> dict:
    rows = con.execute(
        "SELECT symbol, pair, contract_type, delivery_ts, onboard_ts "
        "FROM binance_quarterly_contracts ORDER BY symbol").fetchall()
    calset = set(CAL)
    anchors, matched = [], 0
    for sym, pair, ct, dts, ots in rows:
        for kind, v in (("delivery_ts", dts), ("onboard_ts", ots)):
            ok = v in calset
            anchors.append(dict(symbol=sym, pair=pair, contract_type=ct, kind=kind,
                                ts=v, utc=uts(v), in_calendar=bool(ok)))
            matched += bool(ok)
    n_distinct = len({a["ts"] for a in anchors})
    n_distinct_ok = len({a["ts"] for a in anchors if a["in_calendar"]})
    return dict(n_contract_rows=len(rows), n_anchor_checks=len(anchors),
                n_anchor_matches=matched, n_distinct_timestamps=n_distinct,
                n_distinct_matched=n_distinct_ok,
                pass_=(n_distinct_ok == 4 and matched == len(anchors)),
                anchors=anchors)


# --------------------------------------------------------------------------
# clause D3 -- 15m -> 1h reconstruction convention
# --------------------------------------------------------------------------

def hourly_from_15m(df15: pd.DataFrame, offset: int) -> pd.Series:
    """close of the 15m bar stamped hour+offset, re-indexed by the hour."""
    sel = df15[(df15.index % HOUR) == (offset % HOUR)]
    s = sel["close"].copy()
    s.index = (s.index - offset).astype("int64")
    return s


def clause_d3(con) -> dict:
    btc1h = pd.read_sql_query("SELECT timestamp, close FROM cd_futures_ohlcv", con,
                              index_col="timestamp")["close"]
    btc15 = pd.read_sql_query("SELECT timestamp, close FROM cd_futures_15m", con,
                              index_col="timestamp")
    results = {}
    for name, off in (("open_stamped(+2700s)", 2700), ("close_stamped(+0s)", 0)):
        rec = hourly_from_15m(btc15, off)
        j = pd.concat([btc1h.rename("h"), rec.rename("r")], axis=1).dropna()
        exact = float(np.isclose(j["h"], j["r"], rtol=0, atol=1e-9).mean()) if len(j) else 0.0
        rel = ((j["h"] - j["r"]).abs() / j["h"]) if len(j) else pd.Series(dtype=float)
        results[name] = dict(overlap=int(len(j)), exact_frac=exact,
                             frac_rel_le_1e6=float((rel <= 1e-6).mean()) if len(j) else None,
                             max_abs_diff=float((j["h"] - j["r"]).abs().max()) if len(j) else None)
    best = max(results, key=lambda k: results[k]["exact_frac"])
    return dict(candidates=results, chosen=best,
                chosen_offset=2700 if best.startswith("open") else 0,
                exact_frac=results[best]["exact_frac"],
                threshold=0.999, pass_=results[best]["exact_frac"] >= 0.999)


def d3_tiebreak(con) -> dict:
    """Which of cd_futures_ohlcv / cd_futures_15m does an independent third
    source agree with on the mismatching hours?  Diagnostic only."""
    h = pd.read_sql_query("SELECT timestamp, close FROM cd_futures_ohlcv", con,
                          index_col="timestamp")["close"]
    m = pd.read_sql_query("SELECT timestamp, close FROM cd_futures_15m", con,
                          index_col="timestamp")
    r = hourly_from_15m(m, 2700)
    j = pd.concat([h.rename("h"), r.rename("r")], axis=1).dropna()
    try:
        tv = pd.read_sql_query("SELECT timestamp, close FROM tv_btc_perp_1h", con,
                               index_col="timestamp")["close"]
    except Exception as e:  # noqa: BLE001
        return dict(available=False, error=str(e))
    t = pd.concat([j, tv.rename("tv")], axis=1).dropna()
    bad = t[~np.isclose(t["h"], t["r"], rtol=0, atol=1e-9)]
    if not len(bad):
        return dict(available=True, overlap=int(len(t)), n_mismatch=0)
    dh = (bad["h"] - bad["tv"]).abs()
    dr = (bad["r"] - bad["tv"]).abs()
    return dict(available=True, overlap=int(len(t)), n_mismatch=int(len(bad)),
                n_1h_closer_to_tv=int((dh < dr).sum()),
                n_15m_closer_to_tv=int((dr < dh).sum()),
                median_abs_1h_minus_tv=float(dh.median()),
                median_abs_15m_minus_tv=float(dr.median()),
                verdict=("cd_futures_15m matches the third source; "
                         "cd_futures_ohlcv is the table carrying the error"
                         if (dr < dh).sum() > (dh < dr).sum() else
                         "cd_futures_ohlcv matches the third source"))


# --------------------------------------------------------------------------
# data assembly
# --------------------------------------------------------------------------

def perp_hourly(con, asset: str, offset: int, force_15m: bool = False) -> pd.Series:
    cfg = ASSETS[asset]
    if cfg["perp_1h"] and not force_15m:
        s = pd.read_sql_query(f"SELECT timestamp, close FROM {cfg['perp_1h']}", con,
                              index_col="timestamp")["close"]
        return s.sort_index()
    df15 = pd.read_sql_query(f"SELECT timestamp, close FROM {cfg['perp_15m']}", con,
                             index_col="timestamp")
    return hourly_from_15m(df15, offset).sort_index()


def quarterly(con, pair: str, slot: str) -> pd.Series:
    return pd.read_sql_query(
        "SELECT timestamp, close FROM binance_quarterly_1h "
        "WHERE pair=? AND contract_type=? ORDER BY timestamp",
        con, params=(pair, slot), index_col="timestamp")["close"].sort_index()


def build_basis(perp: pd.Series, qtr: pd.Series, slot: str) -> pd.DataFrame:
    df = pd.concat([perp.rename("perp"), qtr.rename("qtr")], axis=1).dropna()
    df = df[df["perp"] > 0]
    df["basis"] = (df["qtr"] - df["perp"]) / df["perp"]
    df["delivery"] = delivery_for_array(df.index.values.astype("int64"), slot)
    df = df[df["delivery"] > 0]
    df["dtd"] = (df["delivery"] - df.index.values) / DAY
    df = df[df["dtd"] > 0]
    df["basis_ann"] = df["basis"] * 365.0 / df["dtd"]
    return df


# --------------------------------------------------------------------------
# clause D2 -- empirical roll detection
# --------------------------------------------------------------------------

def clause_d2(bases: dict) -> dict:
    checks, per_asset = [], {}
    for asset in ("BTC", "ETH"):
        df = bases[(asset, "CURRENT_QUARTER")]
        idx = df.index.values.astype("int64")
        b = df["basis"].values
        lo, hi = idx.min(), idx.max()
        ok = tot = 0
        for d in CAL:
            if d - 72 * HOUR < lo or d + 72 * HOUR > hi:
                continue
            m = (idx >= d - 72 * HOUR) & (idx <= d + 72 * HOUR)
            w_ts, w_b = idx[m], b[m]
            if len(w_ts) < 20:
                continue
            jumps = np.abs(np.diff(w_b))
            j = int(np.argmax(jumps))
            jump_ts = int(w_ts[j + 1])
            hit = abs(jump_ts - d) <= 2 * HOUR
            tot += 1
            ok += hit
            checks.append(dict(asset=asset, rule_delivery=d, rule_utc=uts(d),
                               detected_jump_utc=uts(jump_ts),
                               offset_hours=(jump_ts - d) / HOUR,
                               jump_size=float(jumps[j]), confirmed=bool(hit)))
        per_asset[asset] = dict(rolls=tot, confirmed=ok, frac=(ok / tot) if tot else 0.0)
    worst = min(v["frac"] for v in per_asset.values())
    return dict(per_asset=per_asset, worst_frac=worst, threshold=0.90,
                pass_=worst >= 0.90, checks=checks)


# --------------------------------------------------------------------------
# funding
# --------------------------------------------------------------------------

def funding_series(con, asset: str) -> pd.Series:
    t = ASSETS[asset]["funding"]
    s = pd.read_sql_query(f"SELECT timestamp, fr_close FROM {t}", con,
                          index_col="timestamp")["fr_close"]
    s = s[(s.index % SETTLE) == 0]          # settlement grid only
    return s[~s.index.duplicated()].sort_index()


def funding_over(fund: pd.Series, perp: pd.Series, t0: int, t1: int):
    """sum(rate * perp_mark) over settlements in (t0, t1]. -> (usd, found, expected)."""
    first = ((t0 // SETTLE) + 1) * SETTLE
    grid = np.arange(first, t1 + 1, SETTLE, dtype="int64")
    tot, n_found = 0.0, 0
    for g in grid:
        r = fund.get(g)
        if r is None or not np.isfinite(r):
            continue
        mark = perp.get(g)
        if mark is None or not np.isfinite(mark):
            continue
        tot += float(r) * float(mark)
        n_found += 1
    return tot, n_found, len(grid)


# --------------------------------------------------------------------------
# trades
# --------------------------------------------------------------------------

def exit_table(cur_df: pd.DataFrame) -> dict:
    """delivery -> exit row (last hourly bar strictly before delivery).

    Built from the CURRENT_QUARTER panel, which is the same physical contract a
    NEXT_QUARTER position becomes after it rolls up a slot -- this is what lets
    a NEXT_QUARTER entry be held to its own delivery.
    """
    out = {}
    for delivery, cyc in cur_df.groupby("delivery", sort=True):
        ts = int(delivery) - HOUR
        if ts in cyc.index:
            out[int(delivery)] = (ts, cyc.loc[ts])
    return out


def build_trades(df: pd.DataFrame, exits: dict, fund: pd.Series, perp: pd.Series,
                 asset: str, slot: str, trigger: float) -> list[dict]:
    trades = []
    for delivery, cyc in df.groupby("delivery", sort=True):
        delivery = int(delivery)
        if delivery not in exits:
            continue                                  # no converged exit bar
        exit_ts, x = exits[delivery]
        cand = cyc[(cyc["dtd"] >= MIN_DTD) & (cyc.index < exit_ts)]
        if cand.empty:
            continue
        hit = cand[np.abs(cand["basis_ann"].values) >= trigger]
        if hit.empty:
            continue
        e = hit.iloc[0]
        entry_ts = int(hit.index[0])
        s = 1.0 if e["basis_ann"] > 0 else -1.0       # +1 = short qtr / long perp
        Q0, P0 = float(e["qtr"]), float(e["perp"])
        Q1, P1 = float(x["qtr"]), float(x["perp"])
        gross = s * ((Q0 - P0) - (Q1 - P1)) / P0
        fees = FEE_PER_LEG_SIDE * (Q0 + P0 + Q1 + P1) / P0
        fund_usd, n_f, n_e = funding_over(fund, perp, entry_ts, exit_ts)
        funding = -s * fund_usd / P0
        net = gross - fees + funding
        hold_days = (exit_ts - entry_ts) / DAY
        trades.append(dict(
            asset=asset, slot=slot, trigger=trigger, delivery=delivery,
            delivery_utc=uts(delivery), entry_ts=entry_ts, entry_utc=uts(entry_ts),
            exit_ts=exit_ts, exit_utc=uts(exit_ts),
            direction=("short_qtr_long_perp" if s > 0 else "long_qtr_short_perp"),
            hold_days=hold_days, entry_dtd=float(e["dtd"]),
            entry_basis=float(e["basis"]), entry_basis_ann=float(e["basis_ann"]),
            exit_basis=float(x["basis"]),
            Q0=Q0, P0=P0, Q1=Q1, P1=P1,
            gross=gross, fees=fees, funding=funding, net=net,
            net_ann=net * 365.0 / hold_days if hold_days > 0 else float("nan"),
            gross_ann=gross * 365.0 / hold_days if hold_days > 0 else float("nan"),
            funding_ann=funding * 365.0 / hold_days if hold_days > 0 else float("nan"),
            funding_settlements_found=n_f, funding_settlements_expected=n_e,
            funding_incomplete=bool(n_f < n_e)))
    return trades


def r_ann(trades) -> float:
    d = sum(t["hold_days"] for t in trades)
    return 365.0 * sum(t["net"] for t in trades) / d if d > 0 else float("nan")


def boot_r_ann(trades, n_iter=BOOT_ITER, seed=SEED) -> dict:
    if len(trades) < 2:
        return {}
    rng = np.random.default_rng(seed)
    nets = np.array([t["net"] for t in trades])
    days = np.array([t["hold_days"] for t in trades])
    n = len(nets)
    out = np.empty(n_iter)
    for i in range(n_iter):
        k = rng.integers(0, n, n)
        out[i] = 365.0 * nets[k].sum() / days[k].sum()
    out.sort()
    return dict(point=r_ann(trades), n_iter=n_iter,
                p2_5=float(np.percentile(out, 2.5)), p5=float(np.percentile(out, 5)),
                p50=float(np.percentile(out, 50)), p95=float(np.percentile(out, 95)),
                p97_5=float(np.percentile(out, 97.5)),
                p_above_kill=float((out >= KILL_R_ANN).mean()),
                p_above_zero=float((out > 0).mean()))


def summarise(trades, label: str) -> dict:
    res = dict(label=label, n_trades=len(trades))
    if not trades:
        return res
    nets = [t["net"] for t in trades]
    span_years = (max(t["exit_ts"] for t in trades)
                  - min(t["entry_ts"] for t in trades)) / (365 * DAY)
    tpy = len(trades) / span_years if span_years > 0 else 4.0
    total_days = sum(t["hold_days"] for t in trades)
    res.update(
        R_ann=r_ann(trades),
        gross_ann_pooled=365.0 * sum(t["gross"] for t in trades) / total_days,
        fees_ann_pooled=-365.0 * sum(t["fees"] for t in trades) / total_days,
        funding_ann_pooled=365.0 * sum(t["funding"] for t in trades) / total_days,
        mean_net_ann=float(np.mean([t["net_ann"] for t in trades])),
        median_net_ann=float(np.median([t["net_ann"] for t in trades])),
        mean_gross_ann=float(np.mean([t["gross_ann"] for t in trades])),
        mean_funding_ann=float(np.mean([t["funding_ann"] for t in trades])),
        mean_net=float(np.mean(nets)),
        sd_net=float(np.std(nets, ddof=1)) if len(nets) > 1 else None,
        win_rate=float(np.mean([n > 0 for n in nets])),
        total_hold_days=float(total_days),
        sum_gross=float(sum(t["gross"] for t in trades)),
        sum_fees=float(sum(t["fees"] for t in trades)),
        sum_funding=float(sum(t["funding"] for t in trades)),
        sum_net=float(sum(nets)),
        n_funding_incomplete=int(sum(t["funding_incomplete"] for t in trades)),
        trades_per_year=tpy, span_years=span_years,
        mean_entry_basis_ann=float(np.mean([t["entry_basis_ann"] for t in trades])),
        mean_exit_basis=float(np.mean([t["exit_basis"] for t in trades])),
        mean_hold_days=float(np.mean([t["hold_days"] for t in trades])),
        n_long_basis=sum(1 for t in trades if t["direction"].startswith("short_qtr")),
        n_reverse=sum(1 for t in trades if t["direction"].startswith("long_qtr")))
    res["bootstrap_R_ann"] = boot_r_ann(trades)
    if len(nets) >= 2:
        res["bootstrap_sharpe"] = bootstrap.bootstrap_sharpe(
            nets, n_iter=BOOT_ITER, threshold=0.0, seed=SEED, n_per_year=tpy)
        res["dsr"] = dsr_pbo.dsr_from_returns(nets, n_trials=N_TRIALS,
                                              periods_per_year=tpy)
        res["trade_sharpe_ann"] = metrics.trade_sharpe(nets, tpy)
    for name, sub in (("long_basis", [t for t in trades if t["direction"].startswith("short_qtr")]),
                      ("reverse", [t for t in trades if t["direction"].startswith("long_qtr")])):
        if sub:
            res[f"{name}_n"] = len(sub)
            res[f"{name}_R_ann"] = r_ann(sub)
            res[f"{name}_mean_gross_ann"] = float(np.mean([t["gross_ann"] for t in sub]))
            res[f"{name}_mean_funding_ann"] = float(np.mean([t["funding_ann"] for t in sub]))
    return res


# --------------------------------------------------------------------------
# one full pass over the 12-cell grid for a given perp-source mode
# --------------------------------------------------------------------------

def run_grid(con, offset: int, force_15m: bool, tag: str):
    perps, funds, bases = {}, {}, {}
    for asset in ("BTC", "ETH"):
        perps[asset] = perp_hourly(con, asset, offset, force_15m=force_15m)
        funds[asset] = funding_series(con, asset)
        for slot in ("CURRENT_QUARTER", "NEXT_QUARTER"):
            bases[(asset, slot)] = build_basis(
                perps[asset], quarterly(con, ASSETS[asset]["pair"], slot), slot)
    exits = {a: exit_table(bases[(a, "CURRENT_QUARTER")]) for a in ("BTC", "ETH")}

    trades, grid = [], []
    for asset in ("BTC", "ETH"):
        for slot in ("CURRENT_QUARTER", "NEXT_QUARTER"):
            df = bases[(asset, slot)]
            n_cycles = int(df["delivery"].nunique())
            n_closable = int(sum(1 for d in df["delivery"].unique() if int(d) in exits[asset]))
            for trig in TRIGGERS:
                tr = build_trades(df, exits[asset], funds[asset], perps[asset],
                                  asset, slot, trig)
                trades += tr
                row = dict(source=tag, asset=asset, slot=slot, trigger=trig,
                           cycles_available=n_cycles, cycles_closable=n_closable,
                           n_trades=len(tr))
                row.update({k: v for k, v in summarise(tr, "").items()
                            if k in ("R_ann", "gross_ann_pooled", "fees_ann_pooled",
                                     "funding_ann_pooled", "mean_net_ann", "win_rate",
                                     "total_hold_days", "n_long_basis", "n_reverse",
                                     "mean_hold_days", "n_funding_incomplete")})
                grid.append(row)
    return bases, perps, funds, trades, grid


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> None:
    con = connect()
    report: dict = {"generated_utc": uts(dt.datetime.now(dt.timezone.utc).timestamp()),
                    "prod_db": str(db.PROD_DB)}

    # ---- D1
    d1 = clause_d1(con)
    report["D1"] = {k: v for k, v in d1.items() if k != "anchors"}
    pd.DataFrame(d1["anchors"]).to_csv(OUT / "d1_calendar_anchors.csv", index=False)
    print(f"D1 calendar anchors: {d1['n_anchor_matches']}/{d1['n_anchor_checks']} checks, "
          f"{d1['n_distinct_matched']}/{d1['n_distinct_timestamps']} distinct -> pass={d1['pass_']}")

    # ---- D3 (+ third-source tiebreak, diagnostic only)
    d3 = clause_d3(con)
    d3["tiebreak_third_source"] = d3_tiebreak(con)
    report["D3"] = d3
    print(f"D3 15m->1h: chosen={d3['chosen']} exact_frac={d3['exact_frac']:.6f} "
          f"(threshold {d3['threshold']}) -> pass={d3['pass_']}")
    print(f"   tiebreak: {d3['tiebreak_third_source'].get('verdict')}")
    offset = d3["chosen_offset"]

    # ---- PRE-REGISTERED pass: BTC native 1h, ETH from 15m
    bases, perps, funds, trades, grid = run_grid(con, offset, force_15m=False, tag="preregistered")
    report["panels"] = {f"{a}-{s}": dict(hours=int(len(bases[(a, s)])),
                                         first=uts(bases[(a, s)].index.min()),
                                         last=uts(bases[(a, s)].index.max()))
                        for a in ("BTC", "ETH") for s in ("CURRENT_QUARTER", "NEXT_QUARTER")}
    for k, v in report["panels"].items():
        print(f"panel {k}: {v['hours']} hours {v['first']} .. {v['last']}")

    # ---- D2
    d2 = clause_d2(bases)
    report["D2"] = {k: v for k, v in d2.items() if k != "checks"}
    pd.DataFrame(d2["checks"]).to_csv(OUT / "d2_roll_detection.csv", index=False)
    print(f"D2 roll detection: BTC {d2['per_asset']['BTC']['confirmed']}/{d2['per_asset']['BTC']['rolls']}, "
          f"ETH {d2['per_asset']['ETH']['confirmed']}/{d2['per_asset']['ETH']['rolls']}, "
          f"worst={d2['worst_frac']:.4f} -> pass={d2['pass_']}")

    # ---- descriptive term structure
    desc = []
    for (asset, slot), df in bases.items():
        yr = pd.to_datetime(df.index, unit="s", utc=True).year
        for y in sorted(set(yr)):
            v = df["basis_ann"].values[yr == y]
            raw = df["basis"].values[yr == y]
            desc.append(dict(asset=asset, slot=slot, year=int(y), hours=int(len(v)),
                             basis_ann_mean=float(np.mean(v)),
                             basis_ann_median=float(np.median(v)),
                             basis_ann_sd=float(np.std(v, ddof=1)) if len(v) > 1 else float("nan"),
                             basis_ann_p05=float(np.percentile(v, 5)),
                             basis_ann_p95=float(np.percentile(v, 95)),
                             raw_basis_median=float(np.median(raw)),
                             frac_ge_8pct=float((v >= 0.08).mean()),
                             frac_ge_6pct=float((v >= 0.06).mean()),
                             frac_ge_5pct=float((v >= 0.05).mean()),
                             frac_le_m8pct=float((v <= -0.08).mean())))
    pd.DataFrame(desc).to_csv(OUT / "basis_term_structure_by_year.csv", index=False)
    print(f"term structure: {len(desc)} asset/slot/year rows written")

    # ---- ROBUSTNESS pass: both assets off the 15m tables (post-D3 diagnostic)
    _, _, _, trades_alt, grid_alt = run_grid(con, offset, force_15m=True, tag="all_15m")

    pd.DataFrame(trades).to_csv(OUT / "trades_all_cells.csv", index=False)
    pd.DataFrame(trades_alt).to_csv(OUT / "trades_all_cells_15m_source.csv", index=False)
    pd.DataFrame(grid + grid_alt).to_csv(OUT / "grid_12_cells.csv", index=False)
    print(f"grid: {len(grid)} pre-registered cells + {len(grid_alt)} robustness cells; "
          f"{len(trades)} / {len(trades_alt)} trades")

    # ---- PRIMARY (pre-registered) and its splits
    def pick(ts, trig=PRIMARY_TRIGGER, slot=PRIMARY_SLOT, asset=None):
        s = [t for t in ts if t["trigger"] == trig and t["slot"] == slot
             and (asset is None or t["asset"] == asset)]
        return sorted(s, key=lambda t: t["entry_ts"])

    prim = pick(trades)
    pd.DataFrame(prim).to_csv(OUT / "primary_trades.csv", index=False)
    report["primary"] = summarise(prim, "preregistered BTC+ETH CURRENT_QUARTER @8%")
    report["primary_btc_only"] = summarise(pick(trades, asset="BTC"), "BTC only")
    report["primary_eth_only"] = summarise(pick(trades, asset="ETH"), "ETH only")
    report["primary_alt_all_15m"] = summarise(pick(trades_alt),
                                              "robustness: both perps from 15m tables")

    # how many primary trade fills land on a BTC 1h/15m mismatching hour?
    h = pd.read_sql_query("SELECT timestamp, close FROM cd_futures_ohlcv", con,
                          index_col="timestamp")["close"]
    m15 = pd.read_sql_query("SELECT timestamp, close FROM cd_futures_15m", con,
                            index_col="timestamp")
    rec = hourly_from_15m(m15, 2700)
    jj = pd.concat([h.rename("h"), rec.rename("r")], axis=1).dropna()
    badset = set(jj.index[~np.isclose(jj["h"], jj["r"], rtol=0, atol=1e-9)].astype("int64"))
    btc_prim = pick(trades, asset="BTC")
    hit_fills = [(t["entry_utc"], t["exit_utc"]) for t in btc_prim
                 if t["entry_ts"] in badset or t["exit_ts"] in badset]
    report["d3_impact_on_primary"] = dict(
        btc_trades=len(btc_prim), n_fills_checked=2 * len(btc_prim),
        n_trades_touching_a_mismatched_hour=len(hit_fills), examples=hit_fills[:5])
    print(f"D3 impact: {len(hit_fills)}/{len(btc_prim)} BTC primary trades have a fill "
          f"on a mismatching hour")

    # ---- clause evaluation, exactly as pre-registered
    res = report["primary"]
    s1 = len(prim) >= 5
    if prim:
        k1 = res["R_ann"] < KILL_R_ANN
        lb = res["bootstrap_R_ann"]["p2_5"]
        lb5 = res["bootstrap_R_ann"]["p5"]
        k2 = lb < KILL_R_ANN
        dsr_v = res["dsr"]["dsr"] if res.get("dsr") else float("nan")
        k3 = not (dsr_v >= 0.95)
    else:
        k1 = k2 = k3 = None
        lb = lb5 = dsr_v = float("nan")

    gates_pass = d1["pass_"] and d2["pass_"] and d3["pass_"]
    if not gates_pass:
        failed = [n for n, p in (("D1", d1["pass_"]), ("D2", d2["pass_"]),
                                 ("D3", d3["pass_"])) if not p]
        verdict = f"INCONCLUSIVE (data) -- clause {'/'.join(failed)} failed"
    elif not s1:
        verdict = "INCONCLUSIVE (sample) -- clause S1 failed"
    elif k1:
        verdict = "CONCLUDED KILL per pre-registration"
    elif k2 or k3:
        verdict = "CONCLUDED KILL (not robust) per pre-registration"
    else:
        verdict = "CONCLUDED BUILD per pre-registration"

    report["clauses"] = dict(
        D1=dict(measured=f"{d1['n_distinct_matched']}/4 distinct anchors",
                threshold="4/4", fired=not d1["pass_"]),
        D2=dict(measured=d2["worst_frac"], threshold=0.90, fired=not d2["pass_"]),
        D3=dict(measured=d3["exact_frac"], threshold=0.999, fired=not d3["pass_"]),
        S1=dict(measured=len(prim), threshold=5, fired=not s1),
        K1=dict(measured=res.get("R_ann"), threshold=KILL_R_ANN, fired=k1),
        K2=dict(measured_p2_5=lb, measured_p5=lb5, threshold=KILL_R_ANN, fired=k2),
        K3=dict(measured=dsr_v, threshold=0.95, fired=k3))
    report["verdict_literal_prereg"] = verdict
    report["economics_verdict_ignoring_D3"] = (
        "CONCLUDED KILL" if (k1 or k2 or k3) else "not KILL")

    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print("\n=== LITERAL PRE-REGISTERED VERDICT:", verdict)
    for name, key in (("pre-registered primary", "primary"), ("BTC only", "primary_btc_only"),
                      ("ETH only", "primary_eth_only"),
                      ("robustness all-15m", "primary_alt_all_15m")):
        r = report[key]
        if r.get("n_trades"):
            print(f"  {name:24s} n={r['n_trades']:3d}  R_ann={r['R_ann']:+8.4%}  "
                  f"gross={r['gross_ann_pooled']:+7.4%}  fees={r['fees_ann_pooled']:+7.4%}  "
                  f"funding={r['funding_ann_pooled']:+8.4%}")
    print(f"  bootstrap 95% CI on primary R_ann: "
          f"[{lb:.4%}, {res['bootstrap_R_ann']['p97_5']:.4%}]  "
          f"P(R_ann>0)={res['bootstrap_R_ann']['p_above_zero']:.4f}  DSR={dsr_v:.3e}")
    print("results ->", OUT)


if __name__ == "__main__":
    main()

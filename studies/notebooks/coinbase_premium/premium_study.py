"""S-Coinbase-premium — analysis script.

Frozen rule, per studies/notebooks/coinbase_premium/README.md (pre-registration):

    premium = (coinbase_close - binance_close) / binance_close   (hourly, spot vs spot)
    z       = (premium - mean(prev 336 bars)) / popstd(prev 336 bars)
    fire    : z >= +2.0, cooldown 48 bars from the previous fire
    entry   : Binance close of the firing bar
    exit    : first of  low <= entry - 2*ATR14   (bars i+1..i+48)
              or        close of bar i+48
    cost    : 18 bp round trip
    R       : (net_return) / (2*ATR14 / entry)

Read-only on prod.db. Deterministic. Re-runnable.

Run:  PYTHONPATH=<repo root> python studies/notebooks/coinbase_premium/premium_study.py
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from strategies.support import db
from studies.lib.validation import bootstrap, dsr_pbo, metrics

# ── frozen parameters ────────────────────────────────────────────────────────
LOOKBACK = 336          # 14 days of hourly bars
Z_THRESHOLD = 2.0
HOLD_BARS = 48
COOLDOWN_BARS = 48
ATR_LEN = 14
SL_ATR_MULT = 2.0
COST_RT = 0.0018        # 18 bp round trip, research convention
WARMUP = LOOKBACK + ATR_LEN + 5   # 355
BOOT_ITERS = 10_000
BOOT_SEED = 42

# trial counts, argued in README §2
N_TRIALS = {"BTC": 12, "ETH": 1}
N_TRIALS_SENS = {"BTC": [1, 12, 24], "ETH": [1]}

ETF_CUTOFF = {"BTC": metrics.BTC_SPOT_ETF, "ETH": metrics.ETH_SPOT_ETF}

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
OUT.mkdir(exist_ok=True)


def uts(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d %H:%M")


# ── data loading ─────────────────────────────────────────────────────────────

def connect() -> sqlite3.Connection:
    return sqlite3.connect("file:" + str(db.PROD_DB) + "?mode=ro", uri=True)


def load_btc(con: sqlite3.Connection) -> pd.DataFrame:
    """Coinbase BTC 1h joined to Binance BTC spot 1h (cd_spot_binance)."""
    q = """
        SELECT cb.timestamp AS ts, cb.close AS cb_close,
               bn.open AS bn_open, bn.high AS bn_high,
               bn.low AS bn_low, bn.close AS bn_close
        FROM coinbase_spot_1h cb
        JOIN cd_spot_binance bn ON bn.timestamp = cb.timestamp
        WHERE cb.asset = 'BTC'
        ORDER BY cb.timestamp
    """
    return pd.read_sql_query(q, con)


def load_eth(con: sqlite3.Connection) -> pd.DataFrame:
    """Coinbase ETH 1h joined to Binance ETHUSDT SPOT 1h aggregated from eth_1m.

    Pre-registered aggregation: open = open of the :00 minute, high = max high,
    low = min low, close = close of the :59 minute.  An hour whose last minute
    bar is not :59 has no valid close and is dropped.
    """
    m = pd.read_sql_query(
        "SELECT open_time, open, high, low, close FROM eth_1m ORDER BY open_time", con)
    m["ts_s"] = m["open_time"] // 1000
    m["hour"] = (m["ts_s"] // 3600) * 3600

    g = m.groupby("hour", sort=True)
    agg = pd.DataFrame({
        "bn_high": g["high"].max(),
        "bn_low": g["low"].min(),
        "n_min": g["ts_s"].size(),
        "first_ts": g["ts_s"].min(),
        "last_ts": g["ts_s"].max(),
    })
    first_idx = g["ts_s"].idxmin()
    last_idx = g["ts_s"].idxmax()
    agg["bn_open"] = m.loc[first_idx, "open"].to_numpy()
    agg["bn_close"] = m.loc[last_idx, "close"].to_numpy()
    agg = agg.reset_index().rename(columns={"hour": "ts"})

    # frozen: close must come from the :59 minute
    valid = agg["last_ts"] == agg["ts"] + 3540
    dropped_no_close = int((~valid).sum())
    agg = agg[valid].copy()

    cb = pd.read_sql_query(
        "SELECT timestamp AS ts, close AS cb_close FROM coinbase_spot_1h "
        "WHERE asset='ETH' ORDER BY timestamp", con)

    df = cb.merge(agg[["ts", "bn_open", "bn_high", "bn_low", "bn_close", "n_min"]],
                  on="ts", how="inner")
    df.attrs["dropped_no_close"] = dropped_no_close
    return df


# ── features ─────────────────────────────────────────────────────────────────

def atr_rma(high, low, close, length=ATR_LEN):
    """Wilder RMA ATR, seeded with the mean of the first `length` true ranges."""
    n = len(high)
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    if n > 1:
        tr[1:] = np.maximum.reduce([high[1:] - low[1:],
                                    np.abs(high[1:] - close[:-1]),
                                    np.abs(low[1:] - close[:-1])])
    atr = np.zeros(n)
    if n >= length:
        atr[length - 1] = tr[:length].mean()
        for i in range(length, n):
            atr[i] = (atr[i - 1] * (length - 1) + tr[i]) / length
    return atr


def rolling_z(x: np.ndarray, lookback: int = LOOKBACK) -> np.ndarray:
    """z of x[i] against the `lookback` bars STRICTLY BEFORE i (population sd)."""
    n = len(x)
    out = np.full(n, np.nan)
    s = np.concatenate([[0.0], np.cumsum(x)])
    s2 = np.concatenate([[0.0], np.cumsum(x * x)])
    i = np.arange(lookback, n)
    wsum = s[i] - s[i - lookback]
    wsum2 = s2[i] - s2[i - lookback]
    mean = wsum / lookback
    var = wsum2 / lookback - mean * mean
    sd = np.sqrt(np.where(var > 0, var, np.nan))
    z = (x[i] - mean) / sd
    out[i] = np.where(np.isfinite(z), z, 0.0)
    return out


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["premium"] = (df["cb_close"] - df["bn_close"]) / df["bn_close"]
    df["z"] = rolling_z(df["premium"].to_numpy(float))
    df["atr"] = atr_rma(df["bn_high"].to_numpy(float),
                        df["bn_low"].to_numpy(float),
                        df["bn_close"].to_numpy(float))
    c = df["bn_close"].to_numpy(float)
    fwd = np.full(len(c), np.nan)
    if len(c) > HOLD_BARS:
        fwd[:-HOLD_BARS] = (c[HOLD_BARS:] - c[:-HOLD_BARS]) / c[:-HOLD_BARS]
    df["fwd48"] = fwd
    df["date"] = [uts(t)[:10] for t in df["ts"]]
    df["year"] = df["date"].str[:4]
    return df


# ── the backtest ─────────────────────────────────────────────────────────────

def simulate(df: pd.DataFrame, z_thr=Z_THRESHOLD, hold=HOLD_BARS,
             cooldown=COOLDOWN_BARS, cost=COST_RT, entry_next_open=False,
             premium_filter=None) -> pd.DataFrame:
    ts = df["ts"].to_numpy(np.int64)
    z = df["z"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    o = df["bn_open"].to_numpy(float)
    lo = df["bn_low"].to_numpy(float)
    c = df["bn_close"].to_numpy(float)
    prem = df["premium"].to_numpy(float)
    n = len(c)

    trades = []
    last_fire = -10 ** 9
    for i in range(WARMUP, n):
        if not np.isfinite(z[i]) or z[i] < z_thr:
            continue
        if i - last_fire < cooldown:
            continue
        if not (atr[i] > 0):
            continue
        if premium_filter is not None and abs(prem[i]) > premium_filter:
            continue

        if entry_next_open:
            if i + 1 >= n:
                continue
            e_idx = i + 1
            e_px = o[e_idx]
        else:
            e_idx = i
            e_px = c[i]
        if not (e_px > 0):
            continue
        sl = e_px - SL_ATR_MULT * atr[i]
        if sl >= e_px:
            continue
        if e_idx + hold >= n:      # not enough bars to complete the hold
            continue

        exit_idx, exit_px, reason = None, None, None
        for j in range(e_idx + 1, e_idx + hold + 1):
            if lo[j] <= sl:
                exit_idx, exit_px, reason = j, sl, "SL"
                break
        if exit_idx is None:
            exit_idx, exit_px, reason = e_idx + hold, c[e_idx + hold], "TIME"

        risk_frac = SL_ATR_MULT * atr[i] / e_px
        gross = (exit_px - e_px) / e_px
        net = gross - cost
        trades.append({
            "fire_ts": int(ts[i]), "fire_dt": uts(ts[i]),
            "entry_ts": int(ts[e_idx]), "entry_dt": uts(ts[e_idx]),
            "exit_ts": int(ts[exit_idx]), "exit_dt": uts(ts[exit_idx]),
            "date": uts(ts[e_idx])[:10], "year": uts(ts[e_idx])[:4],
            "z": float(z[i]), "premium_bp": float(prem[i] * 1e4),
            "entry_px": float(e_px), "exit_px": float(exit_px),
            "sl_px": float(sl), "atr": float(atr[i]),
            "risk_frac": float(risk_frac),
            "gross_ret": float(gross), "net_ret": float(net),
            "R": float(net / risk_frac),
            "reason": reason,
            "bars_held": int(exit_idx - e_idx),
            "hours_held": int((ts[exit_idx] - ts[e_idx]) // 3600),
        })
        last_fire = i
    return pd.DataFrame(trades)


# ── statistics ───────────────────────────────────────────────────────────────

def bootstrap_mean(vals, n_iter=BOOT_ITERS, seed=BOOT_SEED):
    """iid percentile bootstrap of the sample mean. Frozen: seed 42, 10k iters."""
    a = np.asarray(vals, float)
    a = a[np.isfinite(a)]
    if a.size < 2:
        return None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, a.size, size=(n_iter, a.size))
    boot = a[idx].mean(axis=1)
    q = np.quantile(boot, [0.025, 0.05, 0.50, 0.95, 0.975])
    return {
        "n": int(a.size),
        "mean_point": float(a.mean()),
        "ci95_lo": float(q[0]), "ci95_hi": float(q[4]),
        "ci90_lo": float(q[1]), "ci90_hi": float(q[3]),
        "boot_median": float(q[2]),
        "boot_mean": float(boot.mean()), "boot_sd": float(boot.std(ddof=0)),
        "p_gt_0": float((boot > 0).mean()),
        "n_iter": n_iter, "seed": seed,
    }


def spearman(a, b):
    s = pd.DataFrame({"a": a, "b": b}).dropna()
    if len(s) < 3:
        return float("nan")
    return float(s["a"].rank().corr(s["b"].rank()))


def pearson(a, b):
    s = pd.DataFrame({"a": a, "b": b}).dropna()
    if len(s) < 3:
        return float("nan")
    return float(s["a"].corr(s["b"]))


def trade_stats(tr: pd.DataFrame, asset: str) -> dict:
    if tr.empty:
        return {"asset": asset, "n": 0}
    R = tr["R"].to_numpy(float)
    net = tr["net_ret"].to_numpy(float)
    first, last = tr["date"].iloc[0], tr["date"].iloc[-1]
    days = max((datetime.strptime(last, "%Y-%m-%d")
                - datetime.strptime(first, "%Y-%m-%d")).days, 1)
    tpy = len(tr) / (days / 365.25)

    boot_R = bootstrap_mean(R)
    boot_ret = bootstrap_mean(net)
    bs = bootstrap.bootstrap_sharpe(list(R), n_iter=BOOT_ITERS, seed=BOOT_SEED,
                                    n_per_year=tpy)
    dsr = {str(k): dsr_pbo.dsr_from_returns(R, n_trials=k, periods_per_year=tpy)
           for k in N_TRIALS_SENS[asset]}

    eq = np.cumsum(R)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    mdd_R = float(np.max(peak - eq)) if len(eq) else 0.0

    return {
        "asset": asset,
        "n": int(len(tr)),
        "first": first, "last": last, "trades_per_year": float(tpy),
        "mean_R": float(R.mean()), "median_R": float(np.median(R)),
        "sd_R": float(R.std(ddof=1)) if len(R) > 1 else float("nan"),
        "total_R": float(R.sum()),
        "mean_net_ret_pct": float(net.mean() * 100),
        "total_net_ret_pct": float(net.sum() * 100),
        "win_rate": float((R > 0).mean()),
        "sl_rate": float((tr["reason"] == "SL").mean()),
        "mean_risk_frac_pct": float(tr["risk_frac"].mean() * 100),
        "max_dd_R": mdd_R,
        "bootstrap_mean_R": boot_R,
        "bootstrap_mean_net_ret": boot_ret,
        "bootstrap_sharpe_R": bs,
        "dsr_by_n_trials": dsr,
        "n_trials_primary": N_TRIALS[asset],
    }


# ── descriptives ─────────────────────────────────────────────────────────────

def premium_by_year(df: pd.DataFrame, asset: str) -> pd.DataFrame:
    g = df.groupby("year")["premium"]
    out = pd.DataFrame({
        "asset": asset,
        "n_bars": g.size(),
        "mean_bp": g.mean() * 1e4,
        "sd_bp": g.std(ddof=1) * 1e4,
        "p01_bp": g.quantile(0.01) * 1e4,
        "p50_bp": g.quantile(0.50) * 1e4,
        "p99_bp": g.quantile(0.99) * 1e4,
        "abs_gt_2pct": df.assign(f=df["premium"].abs() > 0.02).groupby("year")["f"].sum(),
    }).reset_index()
    return out


def z_decile_table(df: pd.DataFrame, asset: str) -> pd.DataFrame:
    d = df.loc[np.isfinite(df["z"]) & np.isfinite(df["fwd48"]),
               ["z", "fwd48", "atr", "bn_close"]].copy()
    d["risk_frac"] = SL_ATR_MULT * d["atr"] / d["bn_close"]
    d["R_equiv"] = (d["fwd48"] - COST_RT) / d["risk_frac"]
    d["decile"] = pd.qcut(d["z"], 10, labels=False, duplicates="drop")
    g = d.groupby("decile")
    out = pd.DataFrame({
        "asset": asset,
        "n": g.size(),
        "z_lo": g["z"].min(), "z_hi": g["z"].max(), "z_mid": g["z"].median(),
        "mean_fwd48_bp": g["fwd48"].mean() * 1e4,
        "median_fwd48_bp": g["fwd48"].median() * 1e4,
        "win_rate": g["fwd48"].apply(lambda s: float((s > 0).mean())),
        "mean_R_equiv": g["R_equiv"].mean(),
    }).reset_index()
    return out


def z_bucket_table(df: pd.DataFrame, asset: str) -> pd.DataFrame:
    edges = [-np.inf, -3, -2, -1, 0, 1, 2, 3, np.inf]
    labels = ["<-3", "-3..-2", "-2..-1", "-1..0", "0..1", "1..2", "2..3", ">=3"]
    d = df.loc[np.isfinite(df["z"]) & np.isfinite(df["fwd48"]), ["z", "fwd48"]].copy()
    d["bucket"] = pd.cut(d["z"], edges, labels=labels, right=False)
    g = d.groupby("bucket", observed=False)
    return pd.DataFrame({
        "asset": asset,
        "n": g.size(),
        "mean_fwd48_bp": g["fwd48"].mean() * 1e4,
        "win_rate": g["fwd48"].apply(lambda s: float((s > 0).mean()) if len(s) else np.nan),
    }).reset_index()


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    con = connect()
    raw = {"BTC": load_btc(con), "ETH": load_eth(con)}
    con.close()

    provenance = {}
    frames, trades, stats = {}, {}, {}
    year_rows, decile_rows, bucket_rows, trade_year_rows = [], [], [], []
    corr, eras, sens = {}, {}, {}

    for asset, df0 in raw.items():
        n_raw = len(df0)
        # drop the final, still-forming hour
        df0 = df0.iloc[:-1].copy()
        df = build_features(df0)
        frames[asset] = df
        provenance[asset] = {
            "rows_joined": int(n_raw),
            "rows_used": int(len(df)),
            "first": uts(df["ts"].iloc[0]), "last": uts(df["ts"].iloc[-1]),
            "dropped_final_forming_bar": 1,
            "eth_hours_dropped_no_59_close": int(df0.attrs.get("dropped_no_close", 0)),
            "bars_abs_premium_gt_2pct": int((df["premium"].abs() > 0.02).sum()),
            "calendar_hours_expected": int((df["ts"].iloc[-1] - df["ts"].iloc[0]) // 3600 + 1),
        }

        tr = simulate(df)
        trades[asset] = tr
        stats[asset] = trade_stats(tr, asset)
        tr.to_csv(OUT / f"trades_{asset}.csv", index=False)

        year_rows.append(premium_by_year(df, asset))
        decile_rows.append(z_decile_table(df, asset))
        bucket_rows.append(z_bucket_table(df, asset))

        # ── correlation, no threshold (red-flag check)
        ok = np.isfinite(df["z"]) & np.isfinite(df["fwd48"])
        sub = df.loc[ok]
        nonov = sub.iloc[::HOLD_BARS]
        corr[asset] = {
            "n_overlapping": int(len(sub)),
            "pearson_z_fwd48": pearson(sub["z"], sub["fwd48"]),
            "spearman_z_fwd48": spearman(sub["z"], sub["fwd48"]),
            "n_nonoverlapping": int(len(nonov)),
            "pearson_nonoverlapping": pearson(nonov["z"], nonov["fwd48"]),
            "spearman_nonoverlapping": spearman(nonov["z"], nonov["fwd48"]),
            "pearson_premium_fwd48": pearson(sub["premium"], sub["fwd48"]),
            "spearman_premium_fwd48": spearman(sub["premium"], sub["fwd48"]),
        }

        # ── era split (premium level, and per-trade R)
        cutoff = ETF_CUTOFF[asset]
        sp = metrics.era_split(df["date"].tolist(), df["premium"].tolist(), cutoff=cutoff)
        era = {"cutoff": cutoff,
               "premium": {k: {"n": len(v),
                               "mean_bp": float(np.mean(v) * 1e4) if v else None,
                               "sd_bp": float(np.std(v, ddof=1) * 1e4) if len(v) > 1 else None}
                           for k, v in sp.items()}}
        if not tr.empty:
            spR = metrics.era_split(tr["date"].tolist(), tr["R"].tolist(), cutoff=cutoff)
            era["trade_R"] = {}
            for k, v in spR.items():
                b = bootstrap_mean(v) if len(v) >= 2 else None
                era["trade_R"][k] = {
                    "n": len(v),
                    "mean_R": float(np.mean(v)) if v else None,
                    "total_R": float(np.sum(v)) if v else None,
                    "ci95_lo": b["ci95_lo"] if b else None,
                    "ci95_hi": b["ci95_hi"] if b else None,
                }
        eras[asset] = era

        # ── per-year trade table
        if not tr.empty:
            g = tr.groupby("year")["R"]
            ty = pd.DataFrame({"asset": asset, "n": g.size(),
                               "mean_R": g.mean(), "total_R": g.sum(),
                               "win_rate": g.apply(lambda s: float((s > 0).mean()))}).reset_index()
            ty["mean_net_ret_pct"] = tr.groupby("year")["net_ret"].mean().to_numpy() * 100
            trade_year_rows.append(ty)

        # ── sensitivities (reported, never used to choose the primary)
        s = {}
        t2 = simulate(df, premium_filter=0.02)
        s["premium_filter_2pct"] = {"n": len(t2),
                                    "mean_R": float(t2["R"].mean()) if len(t2) else None,
                                    "total_R": float(t2["R"].sum()) if len(t2) else None}
        t3 = simulate(df, entry_next_open=True)
        s["entry_next_open"] = {"n": len(t3),
                                "mean_R": float(t3["R"].mean()) if len(t3) else None,
                                "total_R": float(t3["R"].sum()) if len(t3) else None}
        t4 = simulate(df, cost=0.0)
        s["zero_cost"] = {"n": len(t4),
                          "mean_R": float(t4["R"].mean()) if len(t4) else None,
                          "total_R": float(t4["R"].sum()) if len(t4) else None}
        sens[asset] = s

    # ── decision clauses ─────────────────────────────────────────────────────
    clauses = {}

    eth_n = stats["ETH"]["n"]
    btc_n = stats["BTC"]["n"]
    clauses["C0"] = {
        "clause": "Evaluability: either asset produces < 2 fires",
        "btc_fires": btc_n, "eth_fires": eth_n,
        "fired": bool(btc_n < 2 or eth_n < 2),
    }

    if eth_n >= 2:
        b = stats["ETH"]["bootstrap_mean_R"]
        clauses["C1"] = {
            "clause": "ETH 95% bootstrap CI on mean R includes or lies below zero",
            "eth_n": eth_n,
            "eth_mean_R": stats["ETH"]["mean_R"],
            "ci95_lo": b["ci95_lo"], "ci95_hi": b["ci95_hi"],
            "ci90_lo": b["ci90_lo"], "ci90_hi": b["ci90_hi"],
            "p_mean_gt_0": b["p_gt_0"],
            "fired": bool(b["ci95_lo"] <= 0.0),
            "evaluable": True,
        }
    else:
        clauses["C1"] = {"clause": "ETH 95% bootstrap CI on mean R",
                         "evaluable": False, "fired": None}

    tb = trades["BTC"]
    tb25 = tb[tb["entry_dt"] >= "2025-01-01"] if not tb.empty else tb
    n25 = int(len(tb25))
    mean25 = float(tb25["R"].mean()) if n25 else None
    b25 = bootstrap_mean(tb25["R"]) if n25 >= 2 else None
    clauses["C2"] = {
        "clause": "BTC entries >= 2025-01-01: n >= 20 AND mean R <= 0",
        "btc_2025plus_n": n25,
        "btc_2025plus_mean_R": mean25,
        "btc_2025plus_total_R": float(tb25["R"].sum()) if n25 else None,
        "btc_2025plus_ci95": [b25["ci95_lo"], b25["ci95_hi"]] if b25 else None,
        "evaluable": bool(n25 >= 20),
        "fired": bool(n25 >= 20 and mean25 is not None and mean25 <= 0.0),
    }

    if clauses["C0"]["fired"]:
        verdict = "INCONCLUSIVE (data) — C0 fired"
    elif clauses["C1"]["fired"] or clauses["C2"]["fired"]:
        why = [k for k in ("C1", "C2") if clauses[k]["fired"]]
        verdict = "CONCLUDED KILL per pre-registration (" + ", ".join(why) + ")"
    else:
        verdict = "CONCLUDED BUILD per pre-registration"
    clauses["verdict"] = verdict

    # ── write artefacts ──────────────────────────────────────────────────────
    pd.concat(year_rows).to_csv(OUT / "premium_by_year.csv", index=False)
    pd.concat(decile_rows).to_csv(OUT / "z_decile_table.csv", index=False)
    pd.concat(bucket_rows).to_csv(OUT / "z_bucket_table.csv", index=False)
    if trade_year_rows:
        pd.concat(trade_year_rows).to_csv(OUT / "trades_by_year.csv", index=False)

    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2))
    (OUT / "headline.json").write_text(json.dumps(stats, indent=2, default=float))
    (OUT / "correlation.json").write_text(json.dumps(corr, indent=2, default=float))
    (OUT / "era_split.json").write_text(json.dumps(eras, indent=2, default=float))
    (OUT / "sensitivities.json").write_text(json.dumps(sens, indent=2, default=float))
    (OUT / "clauses.json").write_text(json.dumps(clauses, indent=2, default=float))

    # ── console summary ──────────────────────────────────────────────────────
    print("=" * 78)
    print("  S-Coinbase-premium — z>=+2.0, 48h hold, 2xATR14 stop, 18bp RT")
    print("=" * 78)
    for a in ("BTC", "ETH"):
        p = provenance[a]
        print(f"\n  {a}: {p['rows_used']:,} hourly bars  {p['first']} -> {p['last']}"
              f"  (|prem|>2%: {p['bars_abs_premium_gt_2pct']})")
        s = stats[a]
        if s["n"] == 0:
            print("    NO FIRES")
            continue
        b = s["bootstrap_mean_R"]
        print(f"    fires={s['n']}  mean R={s['mean_R']:+.4f}  total R={s['total_R']:+.1f}"
              f"  WR={s['win_rate']*100:.0f}%  SL={s['sl_rate']*100:.0f}%")
        print(f"    mean net ret/trade={s['mean_net_ret_pct']:+.4f}%  1R={s['mean_risk_frac_pct']:.2f}% notional")
        print(f"    bootstrap mean R 95% CI = [{b['ci95_lo']:+.4f}, {b['ci95_hi']:+.4f}]"
              f"   P(mean>0)={b['p_gt_0']:.3f}")
        bs = s["bootstrap_sharpe_R"]
        print(f"    per-trade Sharpe {bs['sr_point']:+.4f}  90% CI [{bs['sr_p05']:+.4f}, {bs['sr_p95']:+.4f}]")
        for k, d in s["dsr_by_n_trials"].items():
            if d:
                print(f"    DSR(n_trials={k}) = {d['dsr']:.4f}   (sr_expected {d['sr_expected']:+.4f})")
        print(f"    corr(z, fwd48h): pearson {corr[a]['pearson_z_fwd48']:+.4f}"
              f"  spearman {corr[a]['spearman_z_fwd48']:+.4f}"
              f"  | non-overlap pearson {corr[a]['pearson_nonoverlapping']:+.4f}")

    print("\n" + "-" * 78)
    for k in ("C0", "C1", "C2"):
        print(f"  {k}: fired={clauses[k]['fired']}  {clauses[k]['clause']}")
    print(f"\n  VERDICT: {verdict}")
    print("-" * 78)
    print(f"\n  artefacts -> {OUT}")


if __name__ == "__main__":
    main()

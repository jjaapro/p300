"""S-Coinbase-premium — analysis.

Runs the frozen rule in README.md (the PRE-REGISTRATION) on BTC and ETH.
Deterministic, read-only on prod.db. Writes every number findings.md quotes
into results/.

  python studies/notebooks/coinbase_premium/analysis.py

Rule (frozen, see README §4):
  premium = (coinbase_close - binance_close) / binance_close, hourly
  z       = 336h trailing z-score (window strictly before the current bar)
  fire    = z >= +2.0, one fire per 48h
  trade   = long at the firing bar's Binance close, stop at entry - 2*ATR14,
            time exit at +48 bars, 18 bp round trip
  R       = 2 * ATR14 (the stop distance)
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from strategies.support import db                                  # noqa: E402
from studies.lib.validation import bootstrap, dsr_pbo, metrics     # noqa: E402

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(parents=True, exist_ok=True)

# ── frozen parameters ────────────────────────────────────────────────────────
LOOKBACK_H = 336          # 14 days
Z_FIRE = 2.0
HOLD_H = 48
COOLDOWN_H = 48
ATR_LEN = 14
SL_ATR_MULT = 2.0
COST_RT = 0.0018          # 18 bp round trip, p300 research convention
WARMUP_PAD = 5
BOOT_ITERS = 10_000
BOOT_SEED = 42
ETF_CUTOFF = "2024-01-11"
C2_ERA_START = "2025-01-01"
C2_MIN_N = 20
N_TRIALS = {"BTC": 24, "ETH": 1}
N_TRIALS_ETH_CONSERVATIVE = 2


def utc(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


# ── data ─────────────────────────────────────────────────────────────────────
def connect() -> sqlite3.Connection:
    return sqlite3.connect("file:" + str(db.PROD_DB) + "?mode=ro", uri=True)


ETH_HOURLY_SQL = """
SELECT hb*3600 AS timestamp, open, high, low, close, nmin FROM (
  SELECT open_time/3600000 AS hb,
    FIRST_VALUE(open) OVER w AS open,
    MAX(high)         OVER w AS high,
    MIN(low)          OVER w AS low,
    LAST_VALUE(close) OVER w AS close,
    COUNT(*)          OVER w AS nmin,
    ROW_NUMBER() OVER (PARTITION BY open_time/3600000 ORDER BY open_time) rn
  FROM eth_1m
  WINDOW w AS (PARTITION BY open_time/3600000 ORDER BY open_time
               ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
) WHERE rn = 1 ORDER BY timestamp
"""


def binance_leg(con: sqlite3.Connection, asset: str) -> pd.DataFrame:
    """Binance SPOT hourly OHLC for the asset. BTC: cd_spot_binance (the table
    the task named). ETH: eth_1m (ETHUSDT spot 1m) aggregated to the hourly
    grid — prod.db has no ETH counterpart to cd_spot_binance, and every other
    ETH series is a perp. Pre-registered in README §3."""
    if asset == "BTC":
        d = pd.read_sql_query(
            "SELECT timestamp, open, high, low, close FROM cd_spot_binance "
            "ORDER BY timestamp", con)
        d["nmin"] = 60
        return d
    d = pd.read_sql_query(ETH_HOURLY_SQL, con)
    return d


def coinbase_leg(con: sqlite3.Connection, asset: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT timestamp, close FROM coinbase_spot_1h WHERE asset = ? "
        "ORDER BY timestamp", con, params=(asset,))


def build_panel(con: sqlite3.Connection, asset: str) -> pd.DataFrame:
    bn = binance_leg(con, asset).rename(
        columns={"open": "bn_o", "high": "bn_h", "low": "bn_l",
                 "close": "bn_c", "nmin": "bn_nmin"})
    cb = coinbase_leg(con, asset).rename(columns={"close": "cb_c"})
    p = cb.merge(bn, on="timestamp", how="inner").sort_values("timestamp")
    p = p.dropna(subset=["cb_c", "bn_c", "bn_h", "bn_l"]).reset_index(drop=True)
    p = p[(p["bn_c"] > 0) & (p["cb_c"] > 0)].reset_index(drop=True)
    p["dt"] = pd.to_datetime(p["timestamp"], unit="s", utc=True)
    p["premium"] = (p["cb_c"] - p["bn_c"]) / p["bn_c"]
    return p


# ── features ─────────────────────────────────────────────────────────────────
def trailing_z(x: np.ndarray, lookback: int) -> np.ndarray:
    """z of x[i] against the mean/population-sd of x[i-lookback : i] — the
    window is strictly BEFORE the current bar, so there is no lookahead."""
    n = len(x)
    out = np.full(n, np.nan)
    cs = np.concatenate([[0.0], np.cumsum(x)])
    cs2 = np.concatenate([[0.0], np.cumsum(x * x)])
    for i in range(lookback, n):
        s = cs[i] - cs[i - lookback]
        s2 = cs2[i] - cs2[i - lookback]
        m = s / lookback
        var = s2 / lookback - m * m
        out[i] = (x[i] - m) / math.sqrt(var) if var > 0 else 0.0
    return out


def atr_rma(h: np.ndarray, l: np.ndarray, c: np.ndarray, length: int) -> np.ndarray:
    """Wilder RMA ATR, seeded on the mean of the first `length` true ranges."""
    n = len(h)
    tr = np.zeros(n)
    tr[0] = h[0] - l[0]
    if n > 1:
        tr[1:] = np.maximum.reduce([h[1:] - l[1:],
                                    np.abs(h[1:] - c[:-1]),
                                    np.abs(l[1:] - c[:-1])])
    atr = np.zeros(n)
    if n >= length:
        atr[length - 1] = tr[:length].mean()
        for i in range(length, n):
            atr[i] = (atr[i - 1] * (length - 1) + tr[i]) / length
    return atr


# ── simulation ───────────────────────────────────────────────────────────────
def simulate(panel: pd.DataFrame) -> pd.DataFrame:
    """The frozen rule. Long at the firing bar's close, 2*ATR14 stop checked on
    hourly lows (filled at the stop price), time exit at +48 bars, 18 bp."""
    ts = panel["timestamp"].to_numpy()
    c = panel["bn_c"].to_numpy(float)
    h = panel["bn_h"].to_numpy(float)
    lo = panel["bn_l"].to_numpy(float)
    z = panel["z"].to_numpy(float)
    atr = panel["atr"].to_numpy(float)

    n = len(c)
    start = LOOKBACK_H + ATR_LEN + WARMUP_PAD
    rows = []
    last_entry_i = -10**9
    i = start
    while i < n:
        if np.isnan(z[i]) or z[i] < Z_FIRE:
            i += 1
            continue
        if i - last_entry_i < COOLDOWN_H:
            i += 1
            continue
        entry = c[i]
        a = atr[i]
        stop = entry - SL_ATR_MULT * a
        if a <= 0 or stop >= entry:
            i += 1
            continue
        R = SL_ATR_MULT * a
        exit_i, exit_px, reason = None, None, None
        for j in range(i + 1, min(i + HOLD_H + 1, n)):
            if lo[j] <= stop:
                exit_i, exit_px, reason = j, stop, "SL"
                break
            if j - i >= HOLD_H:
                exit_i, exit_px, reason = j, c[j], "TimeStop"
                break
        if exit_i is None:                       # truncated at the end of data
            i += 1
            continue
        gross = (exit_px - entry) / entry
        net = gross - COST_RT
        # DIAGNOSTIC ONLY (not the pre-registered rule, not a decision input):
        # the same fires held the full 48h with the stop switched off. Reported
        # in findings.md purely to attribute the result between signal and stop.
        j_full = i + HOLD_H
        nostop_net = ((c[j_full] - entry) / entry - COST_RT) if j_full < n else float("nan")
        rows.append(dict(
            entry_ts=int(ts[i]), entry_dt=utc(ts[i]),
            exit_ts=int(ts[exit_i]), exit_dt=utc(ts[exit_i]),
            year=int(utc(ts[i])[:4]),
            z=float(z[i]), premium_bp=float(panel["premium"].iloc[i] * 1e4),
            entry_px=float(entry), exit_px=float(exit_px), stop_px=float(stop),
            atr=float(a), R_unit=float(R),
            bars_held=int(exit_i - i), exit_reason=reason,
            ret_gross=float(gross), ret_net=float(net),
            trade_R=float((exit_px - entry) / R - (COST_RT * entry) / R),
            diag_nostop_net=float(nostop_net),
            diag_nostop_R=float(nostop_net * entry / R) if nostop_net == nostop_net else float("nan"),
        ))
        last_entry_i = i
        i += 1
    return pd.DataFrame(rows)


# ── stats ────────────────────────────────────────────────────────────────────
def boot_mean_ci(x: np.ndarray, n_iter: int = BOOT_ITERS,
                 seed: int = BOOT_SEED) -> dict:
    """iid percentile bootstrap of the MEAN (the clause-C1 statistic).
    Primary interval is 95% two-sided; 90% reported as a secondary number."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 2:
        return {}
    rng = np.random.default_rng(seed)
    means = x[rng.integers(0, n, size=(n_iter, n))].mean(axis=1)
    means.sort()
    return dict(
        n=int(n), point=float(x.mean()),
        ci95_lo=float(np.percentile(means, 2.5)),
        ci95_hi=float(np.percentile(means, 97.5)),
        ci90_lo=float(np.percentile(means, 5.0)),
        ci90_hi=float(np.percentile(means, 95.0)),
        boot_mean=float(means.mean()), boot_sd=float(means.std(ddof=0)),
        p_positive=float((means > 0).mean()), n_iter=n_iter, seed=seed,
    )


def leg_stats(tr: pd.DataFrame, label: str, n_trials: int) -> dict:
    r = tr["trade_R"].to_numpy(float)
    pct = tr["ret_net"].to_numpy(float)
    n = len(r)
    out = {"label": label, "n_trades": n, "n_trials": n_trials}
    if n == 0:
        return out
    per_year = n / max((tr["exit_ts"].max() - tr["entry_ts"].min()) / (365.25 * 86400), 1e-9)
    out.update(
        mean_R=float(r.mean()), median_R=float(np.median(r)), sd_R=float(r.std(ddof=1)) if n > 1 else float("nan"),
        sum_R=float(r.sum()),
        mean_pct=float(pct.mean()), sum_pct=float(pct.sum()),
        win_rate=float((r > 0).mean()),
        pf=float(r[r > 0].sum() / abs(r[r < 0].sum())) if (r < 0).any() else float("inf"),
        sr_per_trade=float(r.mean() / r.std(ddof=1)) if n > 1 and r.std(ddof=1) > 0 else float("nan"),
        max_dd_R=float(metrics.max_drawdown(r)),
        trades_per_year=float(per_year),
        sl_rate=float((tr["exit_reason"] == "SL").mean()),
        mean_bars_held=float(tr["bars_held"].mean()),
    )
    # DIAGNOSTIC ONLY — same fires, stop disabled. Not a decision input.
    ns = tr["diag_nostop_R"].dropna().to_numpy(float)
    nsp = tr["diag_nostop_net"].dropna().to_numpy(float)
    out["diag_nostop"] = dict(
        n=int(len(ns)),
        mean_R=float(ns.mean()) if len(ns) else float("nan"),
        mean_pct=float(nsp.mean()) if len(nsp) else float("nan"),
        win_rate=float((ns > 0).mean()) if len(ns) else float("nan"),
        note="NOT the pre-registered rule; attribution diagnostic only",
    )
    out["boot_mean_R"] = boot_mean_ci(r)
    out["boot_mean_pct"] = boot_mean_ci(pct)
    bs = bootstrap.bootstrap_sharpe(list(r), n_iter=BOOT_ITERS, seed=BOOT_SEED,
                                    n_per_year=per_year)
    out["boot_sharpe_R"] = bs
    d = dsr_pbo.dsr_from_returns(r, n_trials=n_trials)
    out["dsr"] = d
    if label.startswith("ETH"):
        out["dsr_conservative"] = dsr_pbo.dsr_from_returns(
            r, n_trials=N_TRIALS_ETH_CONSERVATIVE)
    # per-year
    py = []
    for y, g in tr.groupby("year"):
        rr = g["trade_R"].to_numpy(float)
        py.append(dict(year=int(y), n=len(rr), mean_R=float(rr.mean()),
                       sum_R=float(rr.sum()), win_rate=float((rr > 0).mean()),
                       mean_pct=float(g["ret_net"].mean())))
    out["per_year"] = py
    # era split at the spot-ETF approval
    es = metrics.era_split(list(tr["entry_dt"].str[:10]), list(r), cutoff=ETF_CUTOFF)
    esp = metrics.era_split(list(tr["entry_dt"].str[:10]), list(pct), cutoff=ETF_CUTOFF)
    for era in ("pre", "post"):
        v = np.asarray(es[f"{era}_etf"], dtype=float)   # era_split keys are pre_etf/post_etf
        vp = np.asarray(esp[f"{era}_etf"], dtype=float)
        out[f"era_{era}"] = dict(
            n=int(len(v)), mean_R=float(v.mean()) if len(v) else float("nan"),
            sum_R=float(v.sum()) if len(v) else 0.0,
            mean_pct=float(vp.mean()) if len(vp) else float("nan"),
            win_rate=float((v > 0).mean()) if len(v) else float("nan"),
            boot=boot_mean_ci(v) if len(v) >= 2 else {},
        )
    # C2 era: 2025-01-01 onward
    late = tr[tr["entry_dt"] >= C2_ERA_START]
    lr = late["trade_R"].to_numpy(float)
    out["era_2025plus"] = dict(
        n=int(len(lr)), mean_R=float(lr.mean()) if len(lr) else float("nan"),
        sum_R=float(lr.sum()) if len(lr) else 0.0,
        mean_pct=float(late["ret_net"].mean()) if len(lr) else float("nan"),
        win_rate=float((lr > 0).mean()) if len(lr) else float("nan"),
        boot=boot_mean_ci(lr) if len(lr) >= 2 else {},
    )
    return out


# ── descriptives ─────────────────────────────────────────────────────────────
def premium_by_year(panel: pd.DataFrame, asset: str) -> pd.DataFrame:
    g = panel.assign(year=panel["dt"].dt.year).groupby("year")["premium"]
    d = pd.DataFrame({
        "asset": asset,
        "year": g.mean().index,
        "n_hours": g.size().to_numpy(),
        "mean_bp": (g.mean() * 1e4).to_numpy(),
        "sd_bp": (g.std(ddof=1) * 1e4).to_numpy(),
        "median_bp": (g.median() * 1e4).to_numpy(),
        "p01_bp": (g.quantile(0.01) * 1e4).to_numpy(),
        "p99_bp": (g.quantile(0.99) * 1e4).to_numpy(),
        "pct_hours_positive": g.apply(lambda s: float((s > 0).mean() * 100)).to_numpy(),
    })
    return d.reset_index(drop=True)


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    return float(np.corrcoef(ra, rb)[0, 1])


def monotonicity(panel: pd.DataFrame, asset: str) -> tuple[dict, pd.DataFrame]:
    """z vs forward 48h return on EVERY bar, no threshold."""
    c = panel["bn_c"].to_numpy(float)
    z = panel["z"].to_numpy(float)
    n = len(c)
    fwd = np.full(n, np.nan)
    fwd[: n - HOLD_H] = c[HOLD_H:] / c[: n - HOLD_H] - 1.0
    m = ~np.isnan(z) & ~np.isnan(fwd)
    zz, ff = z[m], fwd[m]
    # Overlapping 48h windows inflate n ~48x; also report the honest
    # non-overlapping estimate (every 48th qualifying bar).
    zn, fn = zz[::HOLD_H], ff[::HOLD_H]
    summary = dict(
        asset=asset, n=int(m.sum()),
        pearson=float(np.corrcoef(zz, ff)[0, 1]),
        spearman=spearman(zz, ff),
        n_nonoverlap=int(len(zn)),
        pearson_nonoverlap=float(np.corrcoef(zn, fn)[0, 1]),
        spearman_nonoverlap=spearman(zn, fn),
        mean_fwd48_all_bp=float(ff.mean() * 1e4),
        mean_fwd48_z_ge_2_bp=float(ff[zz >= Z_FIRE].mean() * 1e4) if (zz >= Z_FIRE).any() else float("nan"),
        n_bars_z_ge_2=int((zz >= Z_FIRE).sum()),
    )
    # excess of the z>=2 bucket over unconditional drift, split at the spot ETF
    dtn = panel["dt"].dt.tz_localize(None).to_numpy()[m]
    cut = np.datetime64(ETF_CUTOFF)
    for era, sel in (("pre", dtn < cut), ("post", dtn >= cut)):
        fe, ze = ff[sel], zz[sel]
        hi = ze >= Z_FIRE
        summary[f"uncond_fwd48_{era}_bp"] = float(fe.mean() * 1e4) if len(fe) else float("nan")
        summary[f"z_ge_2_fwd48_{era}_bp"] = float(fe[hi].mean() * 1e4) if hi.any() else float("nan")
        summary[f"excess_{era}_bp"] = (summary[f"z_ge_2_fwd48_{era}_bp"]
                                       - summary[f"uncond_fwd48_{era}_bp"])
        summary[f"n_bars_z_ge_2_{era}"] = int(hi.sum())
    # decile table
    q = pd.qcut(zz, 10, labels=False, duplicates="drop")
    rows = []
    for d in sorted(set(q)):
        sel = q == d
        rows.append(dict(asset=asset, decile=int(d) + 1, n=int(sel.sum()),
                         z_lo=float(zz[sel].min()), z_hi=float(zz[sel].max()),
                         mean_fwd48_bp=float(ff[sel].mean() * 1e4),
                         median_fwd48_bp=float(np.median(ff[sel]) * 1e4),
                         pct_positive=float((ff[sel] > 0).mean() * 100)))
    return summary, pd.DataFrame(rows)


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    con = connect()
    report: dict = {"generated": datetime.now(timezone.utc).isoformat(),
                    "cost_rt_bp": COST_RT * 1e4, "rule": {
                        "lookback_h": LOOKBACK_H, "z_fire": Z_FIRE,
                        "hold_h": HOLD_H, "cooldown_h": COOLDOWN_H,
                        "atr_len": ATR_LEN, "sl_atr_mult": SL_ATR_MULT}}
    panels, all_trades, py_frames, deciles, mono = {}, [], [], [], []

    for asset in ("BTC", "ETH"):
        p = build_panel(con, asset)
        p["z"] = trailing_z(p["premium"].to_numpy(float), LOOKBACK_H)
        p["atr"] = atr_rma(p["bn_h"].to_numpy(float), p["bn_l"].to_numpy(float),
                           p["bn_c"].to_numpy(float), ATR_LEN)
        panels[asset] = p
        report[f"panel_{asset}"] = dict(
            rows=int(len(p)), first=utc(p["timestamp"].iloc[0]),
            last=utc(p["timestamp"].iloc[-1]),
            binance_leg="cd_spot_binance" if asset == "BTC" else "eth_1m -> 1h",
            short_hours=int((p["bn_nmin"] < 60).sum()),
        )
        py_frames.append(premium_by_year(p, asset))
        s, dec = monotonicity(p, asset)
        mono.append(s)
        deciles.append(dec)

        tr = simulate(p)
        tr.insert(0, "asset", asset)
        all_trades.append(tr)
        report[asset] = leg_stats(tr, asset, N_TRIALS[asset])
    con.close()

    trades = pd.concat(all_trades, ignore_index=True)
    trades.to_csv(OUT / "trades.csv", index=False)
    eq = trades.sort_values(["asset", "entry_ts"]).copy()
    eq["equity_R"] = eq.groupby("asset")["trade_R"].cumsum()
    eq[["asset", "entry_dt", "trade_R", "equity_R", "exit_reason"]].to_csv(
        OUT / "equity_curve.csv", index=False)
    pd.DataFrame([
        dict(asset=a, **{k: v for k, v in report[a]["per_year"][i].items()})
        for a in ("BTC", "ETH") for i in range(len(report[a]["per_year"]))
    ]).to_csv(OUT / "per_year.csv", index=False)
    pd.DataFrame([
        dict(asset=a, era=e, **{k: v for k, v in report[a][f"era_{e}"].items()
                                if k != "boot"})
        for a in ("BTC", "ETH") for e in ("pre", "post", "2025plus")
    ]).to_csv(OUT / "trade_era_split.csv", index=False)
    pd.concat(py_frames, ignore_index=True).to_csv(OUT / "premium_by_year.csv", index=False)
    pd.concat(deciles, ignore_index=True).to_csv(OUT / "z_decile_forward48h.csv", index=False)
    pd.DataFrame(mono).to_csv(OUT / "monotonicity.csv", index=False)

    # premium era decay (descriptive, both assets)
    era_rows = []
    for asset, p in panels.items():
        cut = pd.Timestamp(ETF_CUTOFF, tz="UTC")
        for era, sel in (("pre", p["dt"] < cut), ("post", p["dt"] >= cut)):
            s = p.loc[sel, "premium"]
            zz = p.loc[sel, "z"]
            era_rows.append(dict(asset=asset, era=era, n_hours=int(len(s)),
                                 mean_bp=float(s.mean() * 1e4),
                                 sd_bp=float(s.std(ddof=1) * 1e4),
                                 pct_hours_positive=float((s > 0).mean() * 100),
                                 n_bars_z_ge_2=int((zz >= Z_FIRE).sum()),
                                 pct_bars_z_ge_2=float((zz >= Z_FIRE).mean() * 100)))
    pd.DataFrame(era_rows).to_csv(OUT / "premium_era_decay.csv", index=False)

    # ── clause evaluation ────────────────────────────────────────────────────
    eth = report["ETH"]
    btc = report["BTC"]
    c1_ci = eth.get("boot_mean_R", {})
    c1_evaluable = bool(c1_ci)
    c1_fires = bool(c1_evaluable and c1_ci["ci95_lo"] <= 0)
    b2 = btc["era_2025plus"]
    c2_evaluable = b2["n"] >= C2_MIN_N
    c2_fires = bool(c2_evaluable and b2["mean_R"] <= 0)

    eth_dsr = (eth.get("dsr") or {}).get("dsr")
    support_ok = bool(eth_dsr is not None and eth_dsr > 0.95)

    if not c1_evaluable or not c2_evaluable:
        unevaluable = [c for c, ok in (("C1", c1_evaluable), ("C2", c2_evaluable)) if not ok]
    else:
        unevaluable = []

    if c1_fires or c2_fires:
        verdict = "CONCLUDED KILL per pre-registration"
    elif unevaluable:
        verdict = "INCONCLUSIVE — " + ", ".join(unevaluable) + " unevaluable"
    elif support_ok:
        verdict = "CONCLUDED BUILD per pre-registration"
    else:
        verdict = "CONCLUDED KILL per pre-registration"

    report["clauses"] = {
        "C1_eth_boot_ci_mean_R": dict(
            evaluable=c1_evaluable, fires=c1_fires,
            n=c1_ci.get("n"), point=c1_ci.get("point"),
            ci95_lo=c1_ci.get("ci95_lo"), ci95_hi=c1_ci.get("ci95_hi"),
            ci90_lo=c1_ci.get("ci90_lo"), ci90_hi=c1_ci.get("ci90_hi"),
            rule="KILL when ci95_lo <= 0"),
        "C2_btc_2025plus_mean_R": dict(
            evaluable=c2_evaluable, fires=c2_fires, n=b2["n"],
            mean_R=b2["mean_R"], min_n=C2_MIN_N,
            rule="KILL when n >= 20 and mean_R <= 0"),
        "support_eth_dsr": dict(dsr=eth_dsr, threshold=0.95, passes=support_ok,
                                n_trials=N_TRIALS["ETH"]),
    }
    report["verdict"] = verdict
    report["monotonicity"] = mono
    report["premium_era_decay"] = era_rows

    with open(OUT / "report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    # console
    print("=" * 78)
    print("S-Coinbase-premium")
    print("=" * 78)
    for a in ("BTC", "ETH"):
        p = report[f"panel_{a}"]
        print(f"panel {a}: {p['rows']:,} hourly bars {p['first']} -> {p['last']} "
              f"(binance leg: {p['binance_leg']}, short hours {p['short_hours']})")
    print()
    for a in ("BTC", "ETH"):
        s = report[a]
        if not s.get("n_trades"):
            print(f"{a}: NO TRADES")
            continue
        b = s["boot_mean_R"]
        print(f"{a}: n={s['n_trades']} meanR={s['mean_R']:+.4f} sumR={s['sum_R']:+.2f} "
              f"WR={s['win_rate']*100:.1f}% PF={s['pf']:.2f} SR/trade={s['sr_per_trade']:+.4f} "
              f"maxDD={s['max_dd_R']:.2f}R SLrate={s['sl_rate']*100:.0f}%")
        print(f"     boot mean R 95% CI [{b['ci95_lo']:+.4f}, {b['ci95_hi']:+.4f}] "
              f"90% [{b['ci90_lo']:+.4f}, {b['ci90_hi']:+.4f}] P(>0)={b['p_positive']:.3f}")
        print(f"     DSR(N={s['n_trials']})={(s['dsr'] or {}).get('dsr')}  "
              f"2025+: n={s['era_2025plus']['n']} meanR={s['era_2025plus']['mean_R']:+.4f}")
    print()
    print("C1 (ETH boot CI includes/below 0):", "FIRES" if c1_fires else "does not fire",
          "" if c1_evaluable else "[UNEVALUABLE]")
    print("C2 (BTC 2025+ meanR<=0, n>=20):  ", "FIRES" if c2_fires else "does not fire",
          "" if c2_evaluable else "[UNEVALUABLE]")
    print("VERDICT:", verdict)
    print("\nartefacts ->", OUT)


if __name__ == "__main__":
    main()

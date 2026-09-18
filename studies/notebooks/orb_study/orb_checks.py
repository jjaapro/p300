"""Data gate for the ORB study (PREREGISTRATION.md section 2). No strategy outcome is computed.

Run from the repository root:
    venv\\Scripts\\python.exe studies\\notebooks\\orb_study\\orb_checks.py

Checks, each written to results/data_gate.json:
  1. panel integrity: grid, duplicates, OHLC validity, outage-filler bars
  2. archive vs REST: 15-minute OHLC from our 1m panel against the local REST-fetched perp tables
     (cd_futures_15m, cd_futures_eth_15m) and 1-minute bars against screener_klines_1m
  3. trades vs bars: 1-minute OHLCV rebuilt from Binance aggTrades on three development days
  4. funding: 8-hour spacing, one settlement per slot, no gaps
  5. eligibility: sessions per anchor and year with every exclusion reason
prod.db is opened read-only (mode=ro, query_only).
"""
from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

import orb_calendars as cal
import orb_data
from orb_engine import Market, Policy, range_features

STUDY = Path(__file__).resolve().parent
ROOT = STUDY.parents[2]
PROD_DB = ROOT / "data" / "databases" / "prod.db"
RESULTS = STUDY / "results"
AGG_DAYS = ("2020-06-15", "2021-06-15", "2022-06-15")
TICK = {"BTCUSDT": (0.01, 0.10, "2022-02-15T00:00:00"), "ETHUSDT": (0.01, 0.01, None)}


def load_market(symbol: str) -> Market:
    before, after, change = TICK[symbol]
    return Market.from_panel(symbol, orb_data.load_panel(symbol), orb_data.load_funding(symbol), before, after, change)


def _ro_connect() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{PROD_DB.as_posix()}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    return con


def panel_integrity(symbol: str) -> dict:
    p = orb_data.load_panel(symbol)
    meta = json.loads((orb_data.CACHE / f"{symbol}_perp_1m.meta.json").read_text())
    o, h, lo, c, v = (p[k] for k in ("open", "high", "low", "close", "volume"))
    finite = np.isfinite(o) & np.isfinite(h) & np.isfinite(lo) & np.isfinite(c)
    bad = finite & ((lo > np.minimum(o, c)) | (h < np.maximum(o, c)) | (lo <= 0))
    dead = v == 0
    idx = np.flatnonzero(dead)
    runs = []
    if len(idx):
        starts = idx[np.r_[True, np.diff(idx) > 1]]
        ends = idx[np.r_[np.diff(idx) > 1, True]]
        runs = [{"start_utc": pd.Timestamp(p["t0_ms"] + int(s) * 60_000, unit="ms", tz="UTC").isoformat(),
                 "minutes": int(e - s + 1)} for s, e in zip(starts, ends)]
    per_file = pd.DataFrame(meta["files"])
    return {"minutes": meta["minutes"], "present": meta["present_minutes"], "duplicates": meta["duplicates"],
            "non_monotonic": int(per_file["non_monotonic"].sum()), "off_grid": int(per_file["off_grid"].sum()),
            "close_time_mismatch": int(per_file["close_time_not_open_plus_59999"].sum()),
            "timestamp_units": sorted(per_file["unit"].unique().tolist()),
            "non_finite": int((~finite).sum()), "invalid_ohlc": int(bad.sum()),
            "outage_minutes": int(dead.sum()), "outage_runs": runs,
            "logical_sha256": meta["logical_sha256"]}


def archive_vs_rest_15m(symbol: str) -> dict:
    table = {"BTCUSDT": "cd_futures_15m", "ETHUSDT": "cd_futures_eth_15m"}[symbol]
    p = orb_data.load_panel(symbol)
    n15 = len(p["close"]) // 15
    sl = slice(0, n15 * 15)
    agg = pd.DataFrame({
        "ts": (p["t0_ms"] // 1000 + np.arange(n15) * 900).astype(np.int64),
        "open": p["open"][sl].reshape(n15, 15)[:, 0], "high": p["high"][sl].reshape(n15, 15).max(axis=1),
        "low": p["low"][sl].reshape(n15, 15).min(axis=1), "close": p["close"][sl].reshape(n15, 15)[:, -1],
        "volume": p["volume"][sl].reshape(n15, 15).sum(axis=1)})
    with _ro_connect() as con:
        rest = pd.read_sql_query(f"SELECT timestamp AS ts, open, high, low, close, volume FROM {table} "
                                 f"WHERE timestamp >= ? AND timestamp < ?", con,
                                 params=(int(agg.ts.iloc[0]), int(agg.ts.iloc[-1]) + 900))
    m = agg.merge(rest, on="ts", suffixes=("_a", "_r"))
    rel = {k: (m[f"{k}_a"] - m[f"{k}_r"]).abs() / m[f"{k}_r"].abs().clip(lower=1e-12) for k in ("open", "high", "low", "close")}
    ohlc_diff = np.maximum.reduce([rel[k].to_numpy() for k in rel]) > 1e-9
    vol_rel = ((m["volume_a"] - m["volume_r"]).abs() / m["volume_r"].clip(lower=1e-12)).to_numpy()
    worst = m.loc[ohlc_diff].assign(max_rel=np.maximum.reduce([rel[k][ohlc_diff].to_numpy() for k in rel]) if ohlc_diff.any() else [])
    return {"table": table, "overlap_bars": int(len(m)), "archive_bars": int(len(agg)),
            "ohlc_disagree": int(ohlc_diff.sum()), "ohlc_agree_share": float(1 - ohlc_diff.mean()) if len(m) else float("nan"),
            "volume_rel_diff_p99": float(np.quantile(vol_rel, 0.99)) if len(m) else float("nan"),
            "volume_rel_diff_gt_1pct": int((vol_rel > 0.01).sum()),
            "disagreements": [{"utc": pd.Timestamp(int(r.ts), unit="s", tz="UTC").isoformat(), "max_rel": float(r.max_rel)}
                              for r in worst.nlargest(25, "max_rel").itertuples()] if len(worst) else []}


def archive_vs_rest_1m() -> dict:
    p = orb_data.load_panel("BTCUSDT")
    with _ro_connect() as con:
        rest = pd.read_sql_query("SELECT ts, open, high, low, close, volume FROM screener_klines_1m WHERE asset='BTCUSDT'", con)
    i = (rest["ts"].to_numpy(np.int64) * 1000 - p["t0_ms"]) // 60_000
    keep = (i >= 0) & (i < len(p["close"]))
    rest, i = rest[keep], i[keep]
    diff = np.zeros(len(rest), dtype=bool)
    for k in ("open", "high", "low", "close"):
        diff |= np.abs(p[k][i] - rest[k].to_numpy()) > 1e-9 * rest[k].to_numpy()
    vol_rel = np.abs(p["volume"][i] - rest["volume"].to_numpy()) / np.maximum(rest["volume"].to_numpy(), 1e-12)
    return {"table": "screener_klines_1m", "overlap_bars": int(len(rest)), "ohlc_disagree": int(diff.sum()),
            "volume_rel_diff_gt_1pct": int((vol_rel > 0.01).sum()),
            "first_utc": pd.Timestamp(int(rest["ts"].min()), unit="s", tz="UTC").isoformat() if len(rest) else None,
            "last_utc": pd.Timestamp(int(rest["ts"].max()), unit="s", tz="UTC").isoformat() if len(rest) else None}


def trades_vs_bars(day: str, symbol: str = "BTCUSDT") -> dict:
    url = f"https://data.binance.vision/data/futures/um/daily/aggTrades/{symbol}/{symbol}-aggTrades-{day}.zip"
    raw = urlopen(Request(url, headers={"User-Agent": "p300-orb-study/1.0"}), timeout=300).read()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        body = z.read(z.namelist()[0])
    header = 0 if not body[:1].isdigit() else None
    cols = ["agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id", "transact_time", "is_buyer_maker"]
    t = pd.read_csv(io.BytesIO(body), header=header, names=None if header == 0 else cols,
                    usecols=["price", "quantity", "transact_time"] if header == 0 else [1, 2, 5])
    t.columns = ["price", "quantity", "transact_time"]
    start = int(pd.Timestamp(f"{day} 13:00", tz="UTC").timestamp() * 1000)
    end = int(pd.Timestamp(f"{day} 21:30", tz="UTC").timestamp() * 1000)
    t = t[(t.transact_time >= start) & (t.transact_time < end)]
    t["minute"] = (t.transact_time // 60_000) * 60_000
    bars = t.groupby("minute").agg(open=("price", "first"), high=("price", "max"), low=("price", "min"),
                                   close=("price", "last"), volume=("quantity", "sum"))
    p = orb_data.load_panel(symbol)
    i = (bars.index.to_numpy(np.int64) - p["t0_ms"]) // 60_000
    same = {k: np.abs(p[k][i] - bars[k].to_numpy()) <= 1e-9 * bars[k].to_numpy() for k in ("open", "high", "low", "close")}
    diff = {k: int((~v).sum()) for k, v in same.items()}
    # Binance sets some bar opens to the previous bar's close rather than the first trade.
    open_is_prev_close = np.abs(p["open"][i] - p["close"][i - 1]) <= 1e-9 * p["close"][i - 1]
    hl_bp = np.maximum(np.abs(p["high"][i] - bars["high"].to_numpy()), np.abs(p["low"][i] - bars["low"].to_numpy())) \
        / bars["close"].to_numpy() * 1e4
    vol_rel = np.abs(p["volume"][i] - bars["volume"].to_numpy()) / bars["volume"].to_numpy()
    window_vol_rel = abs(p["volume"][i].sum() - bars["volume"].sum()) / bars["volume"].sum()
    return {"day": day, "symbol": symbol, "zip_bytes": len(raw), "trade_minutes": int(len(bars)),
            "expected_minutes": (end - start) // 60_000, "ohlc_disagree_by_field": diff,
            "open_unexplained": int((~same["open"] & ~open_is_prev_close).sum()),
            "high_low_disagree": int((~same["high"] | ~same["low"]).sum()),
            "high_low_max_diff_bp": float(hl_bp.max()),
            "volume_max_rel_diff": float(vol_rel.max()), "window_volume_rel_diff": float(window_vol_rel),
            "source": url}


def funding_check(symbol: str) -> dict:
    f = orb_data.load_funding(symbol)
    slots = np.round((f["time_ms"] - orb_data.ms(orb_data.PANEL_START)) / (8 * 3_600_000)).astype(int)
    expected = int((orb_data.ms(orb_data.CUTOFF) - orb_data.ms(orb_data.PANEL_START)) / (8 * 3_600_000))
    offs = f["time_ms"] - (orb_data.ms(orb_data.PANEL_START) + slots * 8 * 3_600_000)
    return {"rows": int(len(slots)), "expected_slots": expected, "unique_slots": int(len(np.unique(slots))),
            "missing_slots": int(expected - len(np.unique(slots))), "max_offset_ms": int(np.abs(offs).max()),
            "rate_min": float(f["rate"].min()), "rate_max": float(f["rate"].max()),
            "rate_mean_bp": float(f["rate"].mean() * 1e4), "from_rest": int((f["source"] == 1).sum())}


def eligibility(mkt: Market, anchor: str, start: str = "2020-01-01", end: str = "2026-09-13") -> pd.DataFrame:
    """Session counts by year: eligible, early close, and invalid by reason (15-minute range, P0 horizon)."""
    s = cal.sessions(anchor, start, end)
    full = s[~s["early_close"]].reset_index(drop=True)
    feats = range_features(mkt, full, Policy(id="elig", anchor=anchor))
    full = full.assign(year=full["date"].str[:4], valid=feats["valid"].to_numpy(),
                       reason=feats["invalid_reason"].fillna("ok").to_numpy())
    # outage minutes anywhere in the P0 horizon (anchor .. anchor+390) flag the session
    i0 = feats["i0"].to_numpy()
    dead = np.isnan(mkt.close)
    full["outage_in_horizon"] = [bool(dead[a:a + 391].any()) if 0 <= a < len(dead) else True for a in i0]
    table = full.pivot_table(index="year", columns="reason", values="date", aggfunc="count", fill_value=0)
    table["sessions"] = full.groupby("year").size()
    table["outage_in_horizon"] = full.groupby("year")["outage_in_horizon"].sum()
    table["early_close_excluded"] = s[s["early_close"]].assign(year=lambda d: d["date"].str[:4]).groupby("year").size()
    return table.fillna(0).astype(int)


def run_all() -> dict:
    out = {"created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for sym in orb_data.SYMBOLS:
        out[f"{sym}_panel"] = panel_integrity(sym)
        out[f"{sym}_rest15m"] = archive_vs_rest_15m(sym)
        out[f"{sym}_funding"] = funding_check(sym)
    out["BTCUSDT_rest1m"] = archive_vs_rest_1m()
    out["BTCUSDT_aggtrades"] = [trades_vs_bars(d) for d in AGG_DAYS]
    elig = {}
    for sym in orb_data.SYMBOLS:
        mkt = load_market(sym)
        for anchor in ("NY", "LDN", "UTC"):
            elig[f"{sym}_{anchor}"] = eligibility(mkt, anchor).reset_index().to_dict("records")
    out["eligibility"] = elig
    gate = {
        "grid_and_duplicates": all(out[f"{s}_panel"]["duplicates"] == 0 and out[f"{s}_panel"]["non_monotonic"] == 0
                                   and out[f"{s}_panel"]["off_grid"] == 0 for s in orb_data.SYMBOLS),
        "ohlc_valid": all(out[f"{s}_panel"]["invalid_ohlc"] == 0 and out[f"{s}_panel"]["non_finite"] == 0 for s in orb_data.SYMBOLS),
        "rest15m_agree_999": all(out[f"{s}_rest15m"]["ohlc_agree_share"] >= 0.999 for s in orb_data.SYMBOLS),
        # Amendment A1 (PREREGISTRATION.md section 2), made before any outcome: closes exact; every
        # open equal to the first trade or the previous close; highs/lows equal on >= 99% of minutes and
        # never more than 1 bp apart; window volume equal to 1e-6 (trades move between adjacent minutes).
        "aggtrades_consistent": all(
            d["ohlc_disagree_by_field"]["close"] == 0 and d["open_unexplained"] == 0
            and d["high_low_disagree"] <= 0.01 * d["trade_minutes"] and d["high_low_max_diff_bp"] <= 1.0
            and d["window_volume_rel_diff"] <= 1e-6 and d["trade_minutes"] == d["expected_minutes"]
            for d in out["BTCUSDT_aggtrades"]),
        "funding_complete": all(out[f"{s}_funding"]["missing_slots"] == 0 and out[f"{s}_funding"]["unique_slots"] == out[f"{s}_funding"]["rows"]
                                for s in orb_data.SYMBOLS),
    }
    out["gate"] = gate
    out["gate_passed"] = all(gate.values())
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "data_gate.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    res = run_all()
    print(json.dumps({k: v for k, v in res.items() if k not in ("eligibility",)}, indent=1, default=str)[:6000])
    print("GATE PASSED" if res["gate_passed"] else "GATE FAILED", res["gate"])

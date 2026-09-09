"""Supplementary data-quality diagnostic for the S-DN(ii) basis study.

POST-HOC. Written AFTER the pre-registered run, in response to an anomaly the
term-structure table surfaced: the 2021 NEXT_QUARTER panels show a ~-50%
"basis" that is economically impossible.  This script establishes the cause and
bounds its reach.  It changes NO rule and moves NO threshold; it only measures
how much of each panel is a repeated (zero-volume) quarterly print, and whether
any pre-registered PRIMARY fill landed on such a bar.

Read-only on prod.db.  Writes results/staleness_by_year.csv,
results/primary_fill_liquidity.csv and results/manual_trade_check.csv.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from strategies.support import db  # noqa: E402

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(exist_ok=True)
HOUR, DAY, SETTLE = 3600, 86400, 28800
FEE = 0.0005


def uts(ts) -> str:
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


con = sqlite3.connect("file:" + str(db.PROD_DB) + "?mode=ro", uri=True)

# ---------------------------------------------------------------- staleness
rows = []
for pair, asset in (("BTCUSDT", "BTC"), ("ETHUSDT", "ETH")):
    for slot in ("CURRENT_QUARTER", "NEXT_QUARTER"):
        q = pd.read_sql_query(
            "SELECT timestamp, close, volume FROM binance_quarterly_1h "
            "WHERE pair=? AND contract_type=? ORDER BY timestamp",
            con, params=(pair, slot), index_col="timestamp")
        yr = pd.to_datetime(q.index, unit="s", utc=True).year
        repeated = q["close"].diff().fillna(1.0) == 0.0   # print unchanged from prior hour
        for y in sorted(set(yr)):
            m = yr == y
            rows.append(dict(asset=asset, slot=slot, year=int(y), hours=int(m.sum()),
                             frac_zero_volume=float((q["volume"].values[m] == 0).mean()),
                             frac_repeated_close=float(repeated.values[m].mean()),
                             median_volume=float(np.median(q["volume"].values[m]))))
stale = pd.DataFrame(rows)
stale.to_csv(OUT / "staleness_by_year.csv", index=False)
print("staleness by asset/slot/year")
print(stale.to_string(index=False))

# ------------------------------------------- liquidity of the primary fills
prim = pd.read_csv(OUT / "primary_trades.csv")
liq = []
for _, t in prim.iterrows():
    pair = "BTCUSDT" if t["asset"] == "BTC" else "ETHUSDT"
    out = {"asset": t["asset"], "entry_utc": t["entry_utc"], "exit_utc": t["exit_utc"]}
    for tag, ts in (("entry", int(t["entry_ts"])), ("exit", int(t["exit_ts"]))):
        r = con.execute(
            "SELECT volume FROM binance_quarterly_1h WHERE pair=? AND contract_type=? "
            "AND timestamp=?", (pair, "CURRENT_QUARTER", ts)).fetchone()
        out[f"{tag}_qtr_volume"] = None if r is None else float(r[0])
    liq.append(out)
liq = pd.DataFrame(liq)
liq["zero_volume_fill"] = (liq["entry_qtr_volume"] == 0) | (liq["exit_qtr_volume"] == 0)
liq.to_csv(OUT / "primary_fill_liquidity.csv", index=False)
print(f"\nprimary fills on a zero-volume quarterly bar: "
      f"{int(liq['zero_volume_fill'].sum())} / {len(liq)} trades")
print(f"  min entry qtr volume = {liq['entry_qtr_volume'].min():.2f}, "
      f"min exit qtr volume = {liq['exit_qtr_volume'].min():.2f}")

# ---------------------------------- independent hand-check of 4 sample trades
# Recomputes net from raw SQL, no shared code with basis_carry.py.
FUND = {"BTC": "cd_funding_rate", "ETH": "cd_funding_rate_eth"}
checks = []
sample = prim.sort_values("entry_ts").iloc[[0, len(prim) // 3, 2 * len(prim) // 3, -1]]
for _, t in sample.iterrows():
    asset, pair = t["asset"], ("BTCUSDT" if t["asset"] == "BTC" else "ETHUSDT")
    e, x = int(t["entry_ts"]), int(t["exit_ts"])
    Q0 = con.execute("SELECT close FROM binance_quarterly_1h WHERE pair=? AND "
                     "contract_type='CURRENT_QUARTER' AND timestamp=?", (pair, e)).fetchone()[0]
    Q1 = con.execute("SELECT close FROM binance_quarterly_1h WHERE pair=? AND "
                     "contract_type='CURRENT_QUARTER' AND timestamp=?", (pair, x)).fetchone()[0]
    if asset == "BTC":
        P0 = con.execute("SELECT close FROM cd_futures_ohlcv WHERE timestamp=?", (e,)).fetchone()[0]
        P1 = con.execute("SELECT close FROM cd_futures_ohlcv WHERE timestamp=?", (x,)).fetchone()[0]
        perp_at = lambda g: con.execute(  # noqa: E731
            "SELECT close FROM cd_futures_ohlcv WHERE timestamp=?", (g,)).fetchone()
    else:
        P0 = con.execute("SELECT close FROM cd_futures_eth_15m WHERE timestamp=?", (e + 2700,)).fetchone()[0]
        P1 = con.execute("SELECT close FROM cd_futures_eth_15m WHERE timestamp=?", (x + 2700,)).fetchone()[0]
        perp_at = lambda g: con.execute(  # noqa: E731
            "SELECT close FROM cd_futures_eth_15m WHERE timestamp=?", (g + 2700,)).fetchone()
    s = 1.0 if t["entry_basis_ann"] > 0 else -1.0
    gross = s * ((Q0 - P0) - (Q1 - P1)) / P0
    fees = FEE * (Q0 + P0 + Q1 + P1) / P0
    fu = 0.0
    n_f = n_e = 0
    for g in range(((e // SETTLE) + 1) * SETTLE, x + 1, SETTLE):
        n_e += 1
        r = con.execute(f"SELECT fr_close FROM {FUND[asset]} WHERE timestamp=?", (g,)).fetchone()
        mk = perp_at(g)
        if r and mk and r[0] is not None and mk[0] is not None:
            fu += float(r[0]) * float(mk[0])
            n_f += 1
    funding = -s * fu / P0
    net = gross - fees + funding
    checks.append(dict(asset=asset, entry_utc=t["entry_utc"], exit_utc=t["exit_utc"],
                       script_net=float(t["net"]), manual_net=net,
                       abs_diff=abs(float(t["net"]) - net),
                       script_gross=float(t["gross"]), manual_gross=gross,
                       script_funding=float(t["funding"]), manual_funding=funding,
                       settlements_found=n_f, settlements_expected=n_e))
chk = pd.DataFrame(checks)
chk.to_csv(OUT / "manual_trade_check.csv", index=False)
print("\nindependent recomputation of 4 sample primary trades (raw SQL, no shared code):")
print(chk[["asset", "entry_utc", "script_net", "manual_net", "abs_diff",
           "settlements_found", "settlements_expected"]].to_string(index=False))
print(f"max |script - manual| net = {chk['abs_diff'].max():.3e}")

# --------------- descriptive basis restricted to the entry-eligible dtd >= 7d
CAL = []
import calendar as _cal
for y in range(2020, 2028):
    for mth in (3, 6, 9, 12):
        d = dt.date(y, mth, _cal.monthrange(y, mth)[1])
        while d.weekday() != 4:
            d -= dt.timedelta(days=1)
        CAL.append(int(dt.datetime(d.year, d.month, d.day, 8, 0, tzinfo=dt.timezone.utc).timestamp()))
CAL = np.array(sorted(CAL), dtype="int64")

rows = []
for pair, asset in (("BTCUSDT", "BTC"), ("ETHUSDT", "ETH")):
    if asset == "BTC":
        perp = pd.read_sql_query("SELECT timestamp, close FROM cd_futures_ohlcv", con,
                                 index_col="timestamp")["close"]
    else:
        m = pd.read_sql_query("SELECT timestamp, close FROM cd_futures_eth_15m", con,
                              index_col="timestamp")["close"]
        m = m[(m.index % HOUR) == 2700]
        m.index = m.index - 2700
        perp = m
    for slot, k in (("CURRENT_QUARTER", 0), ("NEXT_QUARTER", 1)):
        q = pd.read_sql_query("SELECT timestamp, close FROM binance_quarterly_1h "
                              "WHERE pair=? AND contract_type=? ORDER BY timestamp",
                              con, params=(pair, slot), index_col="timestamp")["close"]
        j = pd.concat([perp.rename("p"), q.rename("q")], axis=1).dropna()
        idx = j.index.values.astype("int64")
        dvy = CAL[np.clip(np.searchsorted(CAL, idx, side="left") + k, 0, len(CAL) - 1)]
        dtd = (dvy - idx) / DAY
        b = (j["q"].values - j["p"].values) / j["p"].values
        ann = b * 365.0 / np.where(dtd > 0, dtd, np.nan)
        keep = dtd >= 7.0
        yr = pd.to_datetime(idx, unit="s", utc=True).year.values
        for y in sorted(set(yr[keep])):
            m2 = keep & (yr == y)
            v = ann[m2]
            rows.append(dict(asset=asset, slot=slot, year=int(y), hours=int(m2.sum()),
                             basis_ann_mean=float(np.mean(v)), basis_ann_median=float(np.median(v)),
                             basis_ann_sd=float(np.std(v, ddof=1)),
                             basis_ann_p05=float(np.percentile(v, 5)),
                             basis_ann_p95=float(np.percentile(v, 95)),
                             frac_ge_8pct=float((v >= 0.08).mean()),
                             frac_ge_6pct=float((v >= 0.06).mean()),
                             frac_ge_5pct=float((v >= 0.05).mean()),
                             frac_le_m8pct=float((v <= -0.08).mean())))
d7 = pd.DataFrame(rows)
d7.to_csv(OUT / "basis_term_structure_dtd7.csv", index=False)
print("\nterm structure restricted to entry-eligible hours (dtd >= 7d):")
print(d7[d7.slot == "CURRENT_QUARTER"].to_string(index=False))
print("results ->", OUT)

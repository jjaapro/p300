"""Descriptive: the annualised perp funding curve vs the annualised quarterly basis.

Pre-registration section 6 item 5 requires a decomposition of the trade into
gross convergence / fees / funding.  This script gives the *population* version
of that decomposition, independent of any entry rule: at every 8h settlement,
compare the annualised funding rate the long-perp leg pays with the annualised
CURRENT_QUARTER basis the short-quarterly leg earns.  Their difference IS the
trade's gross carry before fees.  Descriptive only -- no thresholds, no rules.

Read-only.  Writes results/funding_vs_basis_by_year.csv.
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
from studies.lib.validation import bootstrap  # noqa: E402

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(exist_ok=True)
HOUR, DAY, SETTLE = 3600, 86400, 28800

con = sqlite3.connect("file:" + str(db.PROD_DB) + "?mode=ro", uri=True)

import calendar as _cal  # noqa: E402
CAL = []
for y in range(2020, 2028):
    for mth in (3, 6, 9, 12):
        d = dt.date(y, mth, _cal.monthrange(y, mth)[1])
        while d.weekday() != 4:
            d -= dt.timedelta(days=1)
        CAL.append(int(dt.datetime(d.year, d.month, d.day, 8, 0, tzinfo=dt.timezone.utc).timestamp()))
CAL = np.array(sorted(CAL), dtype="int64")

rows, panels = [], {}
for asset, pair, fund_tbl in (("BTC", "BTCUSDT", "cd_funding_rate"),
                              ("ETH", "ETHUSDT", "cd_funding_rate_eth")):
    if asset == "BTC":
        perp = pd.read_sql_query("SELECT timestamp, close FROM cd_futures_ohlcv", con,
                                 index_col="timestamp")["close"]
    else:
        m = pd.read_sql_query("SELECT timestamp, close FROM cd_futures_eth_15m", con,
                              index_col="timestamp")["close"]
        m = m[(m.index % HOUR) == 2700]
        m.index = m.index - 2700
        perp = m
    q = pd.read_sql_query("SELECT timestamp, close FROM binance_quarterly_1h WHERE pair=? "
                          "AND contract_type='CURRENT_QUARTER' ORDER BY timestamp",
                          con, params=(pair,), index_col="timestamp")["close"]
    f = pd.read_sql_query(f"SELECT timestamp, fr_close FROM {fund_tbl}", con,
                          index_col="timestamp")["fr_close"]
    f = f[(f.index % SETTLE) == 0]
    f = f[~f.index.duplicated()]

    j = pd.concat([perp.rename("p"), q.rename("q"), f.rename("fr")], axis=1).dropna()
    idx = j.index.values.astype("int64")
    dvy = CAL[np.clip(np.searchsorted(CAL, idx, side="left"), 0, len(CAL) - 1)]
    dtd = (dvy - idx) / DAY
    keep = dtd >= 7.0
    j = j[keep]
    dtd = dtd[keep]
    basis_ann = ((j["q"].values - j["p"].values) / j["p"].values) * 365.0 / dtd
    # 3 settlements per day -> annualised funding rate paid by the long-perp leg
    fund_ann = j["fr"].values * 3.0 * 365.0
    carry = basis_ann - fund_ann          # gross annualised edge before fees
    panels[asset] = pd.DataFrame(dict(basis_ann=basis_ann, fund_ann=fund_ann, carry=carry),
                                 index=j.index)
    yr = pd.to_datetime(j.index, unit="s", utc=True).year.values
    for y in sorted(set(yr)):
        m2 = yr == y
        rows.append(dict(asset=asset, year=int(y), settlements=int(m2.sum()),
                         basis_ann_mean=float(basis_ann[m2].mean()),
                         funding_ann_mean=float(fund_ann[m2].mean()),
                         carry_mean=float(carry[m2].mean()),
                         carry_median=float(np.median(carry[m2])),
                         frac_carry_positive=float((carry[m2] > 0).mean()),
                         frac_carry_gt_5pct=float((carry[m2] > 0.05).mean())))
    # same, conditioned on the pre-registered trigger state
    hot = basis_ann >= 0.08
    if hot.sum():
        rows.append(dict(asset=asset, year="ALL|basis>=8%", settlements=int(hot.sum()),
                         basis_ann_mean=float(basis_ann[hot].mean()),
                         funding_ann_mean=float(fund_ann[hot].mean()),
                         carry_mean=float(carry[hot].mean()),
                         carry_median=float(np.median(carry[hot])),
                         frac_carry_positive=float((carry[hot] > 0).mean()),
                         frac_carry_gt_5pct=float((carry[hot] > 0.05).mean())))
    rows.append(dict(asset=asset, year="ALL", settlements=int(len(carry)),
                     basis_ann_mean=float(basis_ann.mean()),
                     funding_ann_mean=float(fund_ann.mean()),
                     carry_mean=float(carry.mean()), carry_median=float(np.median(carry)),
                     frac_carry_positive=float((carry > 0).mean()),
                     frac_carry_gt_5pct=float((carry > 0.05).mean())))

out = pd.DataFrame(rows)
out.to_csv(OUT / "funding_vs_basis_by_year.csv", index=False)
pd.set_option("display.width", 220)
print(out.to_string(index=False))

# correlation of the two curves, and a block bootstrap CI on the mean carry
rows_ci: list[dict] = []
for asset, d in panels.items():
    r = float(np.corrcoef(d["basis_ann"], d["fund_ann"])[0, 1])
    hot = d[d["basis_ann"] >= 0.08]["carry"].values
    # circular-block bootstrap of the MEAN carry (blocks of 45 settlements = 15 days,
    # so the persistence of a funding/basis regime survives into the resamples)
    rng = np.random.default_rng(42)
    T = len(hot)
    boot = np.array([hot[bootstrap.circular_block_indices(T, T, 45, rng)].mean()
                     for _ in range(10_000)])
    lo, hi = np.quantile(boot, (0.025, 0.975))
    print(f"\n{asset}: corr(basis_ann, funding_ann) = {r:+.4f} over {len(d)} settlements")
    print(f"  mean carry while basis>=8%: {hot.mean():+.4%} (n={T} settlements); "
          f"block-bootstrap 95% CI [{lo:+.4%}, {hi:+.4%}]; P(mean carry > 0) = "
          f"{(boot > 0).mean():.4f}")
    rows_ci.append(dict(asset=asset, corr_basis_funding=r, n_settlements_hot=T,
                        mean_carry_hot=float(hot.mean()), ci_lo=float(lo), ci_hi=float(hi),
                        p_mean_carry_gt_0=float((boot > 0).mean())))
pd.DataFrame(rows_ci).to_csv(OUT / "funding_vs_basis_ci.csv", index=False)
print("results ->", OUT)

"""Diagnostic for clause D3 -- why does the 15m->1h reconstruction miss 1.2% of hours?

Read-only. Writes results/d3_diagnostic.csv + prints a summary. This is a
diagnostic of a FAILED pre-registered gate, not a re-specification of it.
"""
from __future__ import annotations

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
con = sqlite3.connect("file:" + str(db.PROD_DB) + "?mode=ro", uri=True)

h = pd.read_sql_query("SELECT timestamp, close FROM cd_futures_ohlcv", con,
                      index_col="timestamp")["close"]
m = pd.read_sql_query("SELECT timestamp, close FROM cd_futures_15m", con,
                      index_col="timestamp")["close"]
rec = m[(m.index % 3600) == 2700]
rec.index = rec.index - 2700
j = pd.concat([h.rename("h"), rec.rename("r")], axis=1).dropna()
d = (j["h"] - j["r"]).abs()
rel = d / j["h"]

exact = np.isclose(j["h"], j["r"], rtol=0, atol=1e-9)
print(f"overlap hours       : {len(j)}")
print(f"exact (atol 1e-9)   : {exact.mean():.6f}   n_mismatch={int((~exact).sum())}")
for tol in (1e-8, 1e-6, 1e-4, 1e-2, 0.1, 1.0, 10.0):
    print(f"  |diff| <= {tol:<8}: {(d <= tol).mean():.6f}")
for rt in (1e-9, 1e-8, 1e-7, 1e-6, 1e-5, 1e-4, 1e-3):
    print(f"  rel   <= {rt:<8}: {(rel <= rt).mean():.6f}")

bad = j[~exact].copy()
bad["diff"] = (bad["h"] - bad["r"])
bad["rel"] = bad["diff"].abs() / bad["h"]
bad["year"] = pd.to_datetime(bad.index, unit="s", utc=True).year
print("\nmismatches by year:")
print(bad.groupby("year").agg(n=("diff", "size"), med_rel=("rel", "median"),
                              max_rel=("rel", "max")).to_string())
tot = j.copy()
tot["year"] = pd.to_datetime(tot.index, unit="s", utc=True).year
print("\nexact-fraction by year:")
per = pd.DataFrame({"n": tot.groupby("year").size(),
                    "n_bad": bad.groupby("year").size()}).fillna(0)
per["exact_frac"] = 1 - per["n_bad"] / per["n"]
print(per.to_string())
per.to_csv(OUT / "d3_diagnostic.csv")

# does the mismatch matter at basis scale? basis is O(1e-2); a rel diff of
# 1e-6 shifts basis_ann by 365/dtd * 1e-6.
print(f"\nrel diff p50/p95/p99/max over ALL overlap hours: "
      f"{rel.median():.3e} / {rel.quantile(.95):.3e} / {rel.quantile(.99):.3e} / {rel.max():.3e}")
print("\nworst 10 mismatches:")
w = bad.reindex(bad["rel"].sort_values(ascending=False).index).head(10)
w.index = pd.to_datetime(w.index, unit="s", utc=True)
print(w.to_string())

# --- third-source tiebreak: which of the two tables is off on the bad hours?
try:
    tv = pd.read_sql_query("SELECT timestamp, close FROM tv_btc_perp_1h", con,
                           index_col="timestamp")["close"]
    t = pd.concat([j, tv.rename("tv")], axis=1).dropna()
    tb = t[~np.isclose(t["h"], t["r"], rtol=0, atol=1e-9)]
    print(f"\nTV tiebreak: overlap with tv_btc_perp_1h = {len(t)} hours, "
          f"{len(tb)} of them mismatching")
    if len(tb):
        dh = (tb["h"] - tb["tv"]).abs()
        dr = (tb["r"] - tb["tv"]).abs()
        print(f"  cd_futures_ohlcv closer to TV on {(dh < dr).sum()} hours; "
              f"cd_futures_15m closer on {(dr < dh).sum()}; tie {(dr == dh).sum()}")
        print(f"  median |1h - TV| = {dh.median():.4f}   "
              f"median |15m-derived - TV| = {dr.median():.4f}")
except Exception as e:  # noqa: BLE001
    print("\nTV tiebreak unavailable:", e)

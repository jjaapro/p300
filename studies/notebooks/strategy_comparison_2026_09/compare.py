"""Compare R4, SqueezeBull and PDO on one common basis.

Each study books outcomes in its own unit: R4 and PDO in basis points of the
position notional, SqueezeBull in R multiples against a 2% stop. Converting
everything to PERCENT OF POSITION NOTIONAL makes them comparable:

    R4, PDO      pct = net_bp / 100
    SqueezeBull  pct = r_outcome * 2.0        (1R = the 2% stop distance)

The equity curve is then "one unit of notional per trade, unlevered, trades in
entry order" for all three, so max drawdown is in percent of capital under an
identical sizing assumption. That is deliberately NOT any strategy's shipped
sizing (R4 actually fires at up to 1.5x capital per leg and allows three
concurrent legs); it is the only way to put three different books on one axis.
Read the shipped-sizing numbers from each study's own findings.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(r"C:\Source\Repos\p300")
N = ROOT / "studies" / "notebooks"


def metrics(name, dates, pct, unit_note=""):
    """pct: iterable of per-trade outcomes in % of notional, in entry order."""
    s = pd.Series(list(pct), index=pd.to_datetime(list(dates), utc=True)).sort_index()
    n = len(s)
    if n == 0:
        return None
    wins, losses = s[s > 0], s[s < 0]
    eq = s.cumsum()
    dd = (eq.cummax() - eq)
    span_y = (s.index[-1] - s.index[0]).days / 365.25
    ann = eq.iloc[-1] / span_y if span_y > 0 else float("nan")
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else float("inf")
    return {
        "strategy": name,
        "first": s.index[0].date().isoformat(),
        "last": s.index[-1].date().isoformat(),
        "years": round(span_y, 2),
        "n": n,
        "trades_yr": round(n / span_y, 1) if span_y > 0 else float("nan"),
        "win_n": int((s > 0).sum()),
        "win_pct": round(100 * (s > 0).mean(), 1),
        "profit_factor": round(pf, 2),
        "mean_pct": round(s.mean(), 3),
        "total_pct": round(eq.iloc[-1], 1),
        "ann_pct": round(ann, 1),
        "maxdd_pct": round(dd.max(), 1),
        "mar": round(ann / dd.max(), 2) if dd.max() > 0 else float("inf"),
        "note": unit_note,
    }


rows = []

# ---- R4: four calendar windows, no stop (the shipped config) --------------
r4 = pd.read_csv(N / "r4_bot_prep" / "results" / "sl_fires.csv")
r4 = r4[r4.stop == "none"].copy()
r4["pct"] = r4.net_bp / 100.0
r4 = r4.sort_values("day")
rows.append(metrics("R4 — all four windows", r4.day, r4.pct, "15 bp RT, no stop"))
rows.append(metrics("R4 — post-ETF only", r4[r4.era == "post_etf"].day,
                    r4[r4.era == "post_etf"].pct, "the era the bot trades"))
for strat, label in (("JPLUS_R4_BTC", "R4 — BTC Mon 06-18"),
                     ("JPLUS_R4_ETH", "R4 — ETH Tue 20-Wed 20"),
                     ("JPLUS_R4_BTC_V2", "R4 — BTC V2 Wed+Fri 04-14"),
                     ("JPLUS_R4_ETH_V2", "R4 — ETH V2 Wed+Fri 04-14")):
    g = r4[r4.strategy == strat]
    rows.append(metrics("  " + label, g.day, g.pct, ""))

# ---- SqueezeBull: bull-gated OI flush (the decision-bearing leg) ----------
sb = pd.read_csv(N / "squeeze_bull_revalidation" / "results" / "full_oi_flush_ledger.csv")
sb = sb[sb.resolved.astype(str).str.lower().isin(["true", "1"])].copy()
sb["pct"] = sb.r_outcome * 2.0
bull = sb[sb.regime.astype(str).str.contains("bull")]
rows.append(metrics("SqueezeBull — OI flush, bull-gated", bull.ts, bull.pct,
                    "18 bp RT, 2% stop / 3% target / 48h"))
rows.append(metrics("SqueezeBull — OI flush, all regimes", sb.ts, sb.pct,
                    "the ungated pool, for contrast"))
oos = pd.read_csv(N / "squeeze_bull_revalidation" / "results" / "oos_oi_flush_ledger.csv")
oosb = oos[oos.regime.astype(str).str.contains("bull")]
rows.append(metrics("  SqueezeBull — bull-gated, OOS only", oosb.ts, oosb.r_outcome * 2.0,
                    "2026-04-14 onward"))

# ---- PDO: shipped -10% regime threshold -----------------------------------
pdo = pd.read_csv(N / "pdo_adjacents" / "results" / "qa_trades.csv")
pdo = pdo[pdo.threshold == -10.0].copy()
pdo["pct"] = pdo.net_bp / 100.0
rows.append(metrics("PDO — shipped -10%, BTC+ETH", pdo.entry_iso, pdo.pct, "18 bp RT"))
for a in ("BTC", "ETH"):
    g = pdo[pdo.asset == a]
    rows.append(metrics(f"  PDO — {a}", g.entry_iso, g.pct, ""))

# ---- PDO question (b): the CDO retouch variant that was killed ------------
# stop is 1% below entry and target 2% above, so 1R = 1% of notional here
# (verified: a +2.0% move books R = 1.82 after the 18 bp cost = 0.18R).
qb = pd.read_csv(N / "pdo_adjacents" / "results" / "qb_trades.csv")
rows.append(metrics("PDO — CDO retouch (KILLED)", qb.entry_iso, qb["R"] * 1.0,
                    "18 bp RT, 1% stop / 2% target; shown for contrast"))

out = pd.DataFrame([r for r in rows if r])
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 30)
print(out.to_string(index=False))
out.to_csv(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "results" / "comparison.csv", index=False)

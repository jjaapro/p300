"""Do SqueezeBull (OI flush) and the live ShortSqueeze sleeve trade the same event?

ShortSqueeze S-105 requires an Asia-session SHORT-SQUEEZE SETUP on the fire's
UTC date: Asia OI up >= 0.5%, Asia mean funding < 0, Asia close below Asia open
(strategies/sleeves/short_squeeze/README.md, "Macro context").

SqueezeBull requires OI DOWN 2% over 4h with price down 0.5%, bull-gated.

If ShortSqueeze's macro gate is routinely satisfied on SqueezeBull fire days the
two are the same trade; if it is rarely satisfied they are different phases of
the OI cycle. Read-only.
"""
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, r"C:\Source\Repos\p300")
from strategies.support import db  # noqa: E402

con = sqlite3.connect(f"file:{db.PROD_DB}?mode=ro", uri=True)

oi = pd.read_sql("SELECT timestamp, oi_close FROM cd_open_interest ORDER BY timestamp", con)
px = pd.read_sql("SELECT timestamp, open, close FROM cd_futures_ohlcv ORDER BY timestamp", con)
fr = pd.read_sql("SELECT timestamp, fr_close FROM cd_funding_rate ORDER BY timestamp", con)
for d in (oi, px, fr):
    d["ts"] = pd.to_datetime(d.timestamp, unit="s", utc=True)
    d.set_index("ts", inplace=True)

# ---- ShortSqueeze macro gate, per UTC date -------------------------------
asia_oi, asia_px, asia_fr = oi[oi.index.hour < 7], px[px.index.hour < 7], fr[fr.index.hour < 7]
g = pd.DataFrame({
    "oi_first": asia_oi.oi_close.groupby(asia_oi.index.date).first(),
    "oi_last": asia_oi.oi_close.groupby(asia_oi.index.date).last(),
    "px_open": asia_px.open.groupby(asia_px.index.date).first(),
    "px_close": asia_px.close.groupby(asia_px.index.date).last(),
    "fr_mean": asia_fr.fr_close.groupby(asia_fr.index.date).mean(),
})
g["asia_oi_chg"] = g.oi_last / g.oi_first - 1
g["gate_oi"] = g.asia_oi_chg >= 0.005
g["gate_fr"] = g.fr_mean < 0
g["gate_px"] = g.px_close < g.px_open
g["ss_macro_gate"] = g.gate_oi & g.gate_fr & g.gate_px

# ---- SqueezeBull fires ----------------------------------------------------
sb = pd.read_csv(r"C:\Source\Repos\p300\studies\notebooks\squeeze_bull_revalidation"
                 r"\results\full_oi_flush_ledger.csv")
sb["ts"] = pd.to_datetime(sb.ts, utc=True)
sb = sb[sb.resolved.astype(str).str.lower().isin(["true", "1"])]
bull = sb[sb.regime.astype(str).str.contains("bull")].copy()
bull["date"] = bull.ts.dt.date

j = bull.join(g, on="date")
base = g.loc[g.index >= bull.date.min()]

print(f"SqueezeBull bull-gated fires: {len(j)}   ShortSqueeze macro gate met on "
      f"{int(j.ss_macro_gate.fillna(False).sum())} of them "
      f"({100*j.ss_macro_gate.fillna(False).mean():.1f}%)")
print(f"Base rate of the ShortSqueeze macro gate over the same span: "
      f"{100*base.ss_macro_gate.mean():.1f}% of days (n={len(base)})")
print()
print("individual gate legs on SqueezeBull fire days vs base rate:")
for leg, label in (("gate_oi", "Asia OI up >= 0.5%"), ("gate_fr", "Asia mean funding < 0"),
                   ("gate_px", "Asia closed below open")):
    print(f"  {label:26s} fires {100*j[leg].fillna(False).mean():5.1f}%   "
          f"base {100*base[leg].mean():5.1f}%")
print()
print(f"Asia OI change on SqueezeBull fire days: mean {100*j.asia_oi_chg.mean():+.2f}%  "
      f"median {100*j.asia_oi_chg.median():+.2f}%")
print(f"                        all days in span: mean {100*base.asia_oi_chg.mean():+.2f}%  "
      f"median {100*base.asia_oi_chg.median():+.2f}%")
print()
print("outcome split by whether the ShortSqueeze macro gate also held:")
for flag, sub in j.groupby(j.ss_macro_gate.fillna(False)):
    lbl = "gate ALSO held" if flag else "gate did NOT hold"
    w = sub.r_outcome[sub.r_outcome > 0]
    l = sub.r_outcome[sub.r_outcome < 0]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else float("inf")
    print(f"  {lbl:20s} n={len(sub):3d}  mean R {sub.r_outcome.mean():+.3f}  "
          f"win {100*(sub.r_outcome>0).mean():.0f}%  PF {pf:.2f}")

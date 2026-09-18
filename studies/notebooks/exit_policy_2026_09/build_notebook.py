#!/usr/bin/env python3
"""Assemble and execute the exit-policy review notebooks (system Python has nbformat/nbclient; cells run in the repo venv).

    C:/Python/Python313/python.exe studies/notebooks/exit_policy_2026_09/build_notebook.py [01] [02] [03] [04]
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import nbformat
from nbclient import NotebookClient

HERE = Path(__file__).resolve().parent
VENV_PY = HERE.parents[2] / "venv" / "Scripts" / "python.exe"

CHENTO_CELLS = [
    ("md", r'''
# 01 · Exit-policy study, chento arm

**Question (BACKLOG item 11 + research queue 2).** Is chento's 72 h time stop worth anything? Would it do better with no
time stop, a different one, or an exit that fires when the trade is contradicted? The user's hypothesis, on record
before any number: *"time stop overall feels a bad idea, it just means that we do not actually know if the trade was
valid or not."*

Frozen rules: [PREREGISTRATION_CHENTO.md](PREREGISTRATION_CHENTO.md). Write-up: [findings_chento.md](findings_chento.md).
This notebook recomputes every walk from the frozen snapshot, checks it against the saved outcome files, and then
shows the evidence. **Verdict: INCONCLUSIVE — keep 72 h; the hypothesis is not settled.**
'''),
    ("code", r'''
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
from IPython.display import display

STUDY = Path.cwd()
assert (STUDY / "PREREGISTRATION_CHENTO.md").exists(), "execute from studies/notebooks/exit_policy_2026_09"
sys.path.insert(0, str(STUDY)); sys.path.insert(0, str(STUDY.parent / "orb_study"))
import chento_lib as C, chento_run as R
from review_plots import AXIS, INK2, NEUTRAL, SERIES, style, zero_line
pd.set_option("display.width", 220); pd.set_option("display.max_columns", 30)
RES = C.RESULTS
F0 = R.verify_f0()
V, REP, EXP = (json.loads((RES / f).read_text()) for f in ("verdict.json", "report.json", "exploratory.json"))
PRE, STEP0 = (json.loads((RES / f).read_text()) for f in ("preconditions.json", "step0.json"))
print("F0", F0["created_utc"], "| k =", F0["k"], "| verdict", V["verdict"], "written", V["created_utc"])
'''),
    ("md", r'''
## 1. The run is reproducible

Every arm on every trade is walked again here, from the frozen snapshot through the bot's own `math.py`, and compared
with `walks.csv.gz` and the decision in `verdict.json`.
'''),
    ("code", r'''
signal, ctm, clock = C.open_process("BTC")
trades = C.load_trades()
con = C.L.ro_connect(C.SNAPSHOT)
markets = {a: C.load_market(con, a, ctm) for a in C.ASSETS}
con.close()
walks, report, decision = R.compute_outcomes(ctm, markets, trades, float(F0["k"]))
saved = pd.read_csv(RES / "walks.csv.gz")
cols = ["asset", "t", "direction", "arm", "kind", "exit_bar_ts"]
same_rows = walks[cols].astype(str).equals(saved[cols].astype(str))
same_R = np.allclose(walks["net_R"], saved["net_R"], rtol=0, atol=1e-9)
print("walks identical:", same_rows and same_R, "| verdict identical:", decision["verdict"] == V["verdict"])
print("trades:", trades.groupby("asset").size().to_dict())
'''),
    ("md", r'''
## 2. Preconditions and step 0 (before the freeze)

A0 reproduced the OKX study's 392 trades exactly; the flow features equal the bot's own frames; funding complete; no
missing bars; fixtures, truncation and smoke tests passed. Step 0 then chose the flow-reversal threshold from signal
timing alone: at k = 1 the signal fires within hours on nearly every trade, which would be a hidden short time stop.
'''),
    ("code", r'''
display(pd.DataFrame({k: {"pass": v["pass"]} | {kk: vv for kk, vv in v.items() if kk not in ("pass", "rows") and not isinstance(vv, (dict, list))}
                      for k, v in PRE.items() if isinstance(v, dict) and "pass" in v}).T)
pd.DataFrame(STEP0["by_k"]).T.round(3)
'''),
    ("md", r'''
## 3. Every arm against the shipped exit

Net R per trade (10 bp and actual funding), paired against A0 on identical entries. Intervals: 30-day block bootstrap of
entry days, 10,000 draws. The grey line is the pre-registered +0.10 R effect size.
'''),
    ("code", r'''
rows = []
for arm, s in REP["by_arm"].items():
    c = REP["candidates"].get(arm)
    rows.append({"arm": arm, "mean_net_R": s["mean_net_R"], "mean_d_vs_A0": c["mean_d"] if c else 0.0,
                 "ci95_lo": c["ci95"][0] if c else None, "ci95_hi": c["ci95"][2] if c else None,
                 "holm_p": V["clauses"][arm]["holm_p"] if c else None, "d_BTC": c["mean_d_BTC"] if c else None,
                 "d_ETH": c["mean_d_ETH"] if c else None, "d_first_half": c["mean_d_first_half"] if c else None,
                 "d_second_half": c["mean_d_second_half"] if c else None, "median_hours_held": s["median_hours_held"],
                 "exit_mix": s["exit_mix"]})
T = pd.DataFrame(rows).set_index("arm")
display(T.round(3))
arms = [a for a in C.CANDIDATES]
fig, ax = plt.subplots(figsize=(8.5, 3.8))
y = np.arange(len(arms))[::-1]
for yi, a in zip(y, arms):
    c = REP["candidates"][a]
    ax.hlines(yi, c["ci95"][0], c["ci95"][2], color=AXIS, linewidth=2)
    ax.scatter([c["mean_d"]], [yi], color=SERIES[0] if c["mean_d"] >= 0 else SERIES[1], s=46, zorder=3)
ax.axvline(0, color=AXIS, linewidth=1); ax.axvline(C.EFFECT_R, color=NEUTRAL, linewidth=1)
ax.set_yticks(y); ax.set_yticklabels(["6 h", "12 h", "24 h", "48 h", "168 h", "A1 no time stop", "X1 flow reversal",
                                      "X2 flow reversal, losing", "X3 opposite signal"], fontsize=8)
style(ax, grid_axis="x"); ax.set_xlabel("net R per trade minus the shipped 72 h exit (95% interval)")
ax.set_title("Chento: every exit arm against the 72 h time stop (392 trades)")
plt.tight_layout(); plt.show()
'''),
    ("md", r'''
## 4. The frozen decision

A candidate needs all four: effect ≥ +0.10 R, Holm-adjusted p ≤ 0.05, positive on both assets and in both halves, and
support from walk-forward re-selection. None passes; the walk-forward picks a different arm in each fold.
'''),
    ("code", r'''
display(pd.DataFrame(V["clauses"]).T)
print("walk-forward stitched out-of-sample mean difference:", round(REP["walk_forward"]["stitched_oos_mean_d"], 3))
display(pd.DataFrame(REP["walk_forward"]["folds"])[["oos_start", "oos_end", "selected", "oos_trades"]])
print("VERDICT:", V["verdict"], "| hypothesis 'time stop is harmful':", V["hypothesis_time_stop_harmful"])
'''),
    ("md", r'''
## 5. Why: the move keeps building for about a week, and holding paid only in 2021–2023

With no exit at all, the average move in the trade's favour grows until about 7 days, half of it by 72 h. The trades the
time stop closes were +1.40 R on average at 72 h; held, 57 reached the target and 77 were stopped out, and the net gain
per such trade was +1.00 R in 2021–2023 and −0.05 R in 2024–2026.
'''),
    ("code", r'''
fp = REP["forward_profile"]["by_horizon_h"]
h = np.array([int(k) for k in fp]); m = np.array([fp[k]["mean_R"] for k in fp])
lo = np.array([fp[k]["ci95"][0] for k in fp]); hi = np.array([fp[k]["ci95"][1] for k in fp])
fig, ax = plt.subplots(figsize=(8.5, 3.4))
ax.fill_between(h, lo, hi, color=SERIES[0], alpha=0.15, linewidth=0)
ax.plot(h, m, marker="o", color=SERIES[0])
ax.axvline(72, color=SERIES[1], linewidth=1)
ax.annotate("shipped time stop 72 h", xy=(72, ax.get_ylim()[1]), xytext=(4, -12), textcoords="offset points", fontsize=8, color=INK2)
ax.set_xscale("log"); ax.set_xticks(h); ax.set_xticklabels([f"{x} h" if x < 168 else f"{x // 24} d" for x in h], fontsize=8)
style(ax); zero_line(ax); ax.set_ylabel("mean move in the trade's favour, R")
ax.set_title("Chento: average move after entry with no exit (95% interval)")
plt.tight_layout(); plt.show()
display(pd.DataFrame(EXP["a0_time_stopped_by_a1_outcome"]))
pd.DataFrame(EXP["a0_time_stopped_by_period"])
'''),
    ("md", r'''
## 6. The price of holding longer: drawdown

The bots' own sequence (6 h cooldown, BTC 48 h skip after a losing stop, ETH half risk after a loss, 2% risk, 3× cap), with
drawdown against the fixed $10,000 as in the 2026-09-14 sizing review (A0 reproduces its 33% / 21%).
'''),
    ("code", r'''
seq = EXP["fixed_capital_sequence"]
labels = {"A0": "72 h (shipped)", "A2_48": "48 h", "A2_168": "168 h", "A1": "no time stop", "X1": "flow reversal",
          "X2": "flow reversal, losing", "X3": "opposite signal"}
fig, axes = plt.subplots(1, 2, figsize=(10, 3.8))
for ax, asset in zip(axes, ("BTC", "ETH")):
    for i, (arm, lab) in enumerate(labels.items()):
        s = seq[arm][asset]
        ax.scatter(s["max_drawdown_pct_of_capital"], s["total_return_pct_of_capital"], s=46,
                   color=SERIES[1] if arm == "A0" else SERIES[0], zorder=3)
        ax.annotate(lab, (s["max_drawdown_pct_of_capital"], s["total_return_pct_of_capital"]), xytext=(5, 3),
                    textcoords="offset points", fontsize=7, color=INK2)
    style(ax, grid_axis="both"); ax.set_title(asset, fontsize=10)
    ax.set_xlabel("max drawdown, % of $10,000"); ax.set_ylabel("total return, % of $10,000")
plt.tight_layout(); plt.show()
pd.DataFrame({arm: {f"{a} return %": round(seq[arm][a]["total_return_pct_of_capital"], 1) for a in ("BTC", "ETH")}
              | {f"{a} max DD %": round(seq[arm][a]["max_drawdown_pct_of_capital"], 1) for a in ("BTC", "ETH")} for arm in labels}).T
'''),
    ("md", r'''
## Conclusion

- **Keep 72 h.** Every shorter time stop is significantly worse, and no alternative passed the frozen rule.
- **No time stop** is ahead on average (+0.18 R per trade) but not reliably: all of it came from 2021–2023, and it costs
  much more drawdown (ETH 21% → 43%). Settling it needs data after 2026-09-11, for example a paper twin of chento without
  the time stop running beside the shipped bot, plus an exposure budget.
- **"Exit when shown wrong"** did not help: a flow reversal cuts winners, and the opposite chento signal almost never fires
  during a trade. The structural stop remains the only invalidation that does not damage the payoff.
'''),
]

SQB_CELLS = [
    ("md", r'''
# 02 · Exit-policy study, squeeze_bull arm (report-only)

**Question.** Is squeeze_bull's 48 h time stop — the exit that closed SJ-4250 — doing anything? How far do the bounces
run, would another target do better, does a "flush resumed" exit help, and what does the time stop do for the no-stop
twin?

Frozen rules: [PREREGISTRATION_SQUEEZE_BULL.md](PREREGISTRATION_SQUEEZE_BULL.md). Write-up:
[findings_squeeze_bull.md](findings_squeeze_bull.md). **Report-only: nothing may change before the n = 20 / 30 re-cuts.**
This notebook recomputes every walk on the Binance perpetual 1-minute path and checks it against the saved files.
'''),
    ("code", r'''
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
from IPython.display import display

STUDY = Path.cwd()
assert (STUDY / "PREREGISTRATION_SQUEEZE_BULL.md").exists(), "execute from studies/notebooks/exit_policy_2026_09"
sys.path.insert(0, str(STUDY)); sys.path.insert(0, str(STUDY.parent / "orb_study"))
import sqb_lib as S, sqb_run as RUN
from review_plots import AXIS, INK2, NEUTRAL, SERIES, style, zero_line
pd.set_option("display.width", 220); pd.set_option("display.max_columns", 30)
RES = S.RESULTS
F0, PRE, REP, DRIFT = (json.loads((RES / f).read_text()) for f in
                       ("freeze_F0.json", "preconditions.json", "report.json", "exploratory_regime_drift.json"))
changed = [f for f, h in F0["files"].items() if S.sha256(S.ROOT / f) != h]
print("F0", F0["created_utc"], "| frozen files unchanged:", not changed, "| report", REP["created_utc"])
fires = S.load_fires()
walks, report = RUN.compute_report(S.load_path(), fires, S.load_hourly_flush_bars()["flush_bar_ts"])
saved = pd.read_csv(RES / "walks.csv.gz")
cols = ["bar_ts", "arm", "kind", "exit_s"]
print("walks identical to the saved run:",
      walks[cols].astype(str).equals(saved[cols].astype(str)) and np.allclose(walks["net_R"], saved["net_R"], rtol=0, atol=1e-9))
pd.DataFrame({k: {"pass": v["pass"]} for k, v in PRE.items() if isinstance(v, dict) and "pass" in v}).T
'''),
    ("md", r'''
## 1. Every arm, and the paired differences

Net R per trade (R = 2 % of entry, 7 bp, actual funding) on all 122 fires. Incumbent arms are compared with the shipped
S0, twin arms with the shipped twin N0, and the twin with the incumbent. Intervals: 30-day block bootstrap of entry days.
'''),
    ("code", r'''
display(pd.DataFrame(REP["by_arm"]).T[["mean_net_R", "mean_R_no_funding", "mean_funding_R", "win_rate",
                                       "median_hours_held", "worst_R", "exit_mix"]].round(3))
P = pd.DataFrame({k: {"mean_d": v["mean_d"], "ci95_lo": v["ci95"][0], "ci95_hi": v["ci95"][1], "first_half": v["first_half"],
                      "second_half": v["second_half"], "fires_changed": v["fires_changed"]} for k, v in REP["paired"].items()}).T
display(P.round(3))
fig, ax = plt.subplots(figsize=(8.5, 4.2))
keys = list(REP["paired"])
y = np.arange(len(keys))[::-1]
for yi, k in zip(y, keys):
    v = REP["paired"][k]
    ax.hlines(yi, v["ci95"][0], v["ci95"][1], color=AXIS, linewidth=2)
    ax.scatter([v["mean_d"]], [yi], s=46, zorder=3, color=SERIES[0] if v["mean_d"] >= 0 else SERIES[1])
ax.axvline(0, color=AXIS, linewidth=1)
ax.set_yticks(y); ax.set_yticklabels([k.replace("_minus_", " − ") for k in keys], fontsize=8)
style(ax, grid_axis="x"); ax.set_xlabel("difference in net R per trade (95% interval)")
ax.set_title("squeeze_bull: exit arms against the shipped exits (122 fires)")
plt.tight_layout(); plt.show()
'''),
    ("md", r'''
## 2. The time stop, trade by trade

The 30 trades the shipped exit closes on its 48 h time stop, held to their stop or target instead.
'''),
    ("code", r'''
pd.Series(REP["s0_time_stopped_held_without_time_stop"]).to_frame("value")
'''),
    ("md", r'''
## 3. How far the bounces run, against the bull regime's own drift

Best and worst price after entry, the share of fires reaching each target before −2 %, and the average move after entry
compared with every hour of the same causal bull regime (an exploratory control added after the report).
'''),
    ("code", r'''
display(pd.DataFrame({k: v for k, v in REP["excursions"].items() if isinstance(v, dict)}).T.round(2))
display(pd.Series({k: v for k, v in REP["excursions"].items() if not isinstance(v, dict)}).round(3).to_frame("share of fires"))
h = [int(x) for x in DRIFT["by_horizon_h"]]
fires_m = [DRIFT["by_horizon_h"][str(x)]["fires_mean_pct"] for x in h]
bull_m = [DRIFT["by_horizon_h"][str(x)]["mean_pct"] for x in h]
fig, ax = plt.subplots(figsize=(8, 3.3))
ax.plot(h, fires_m, marker="o", color=SERIES[0], label="flush fires")
ax.plot(h, bull_m, marker="o", color=NEUTRAL, label="any bull-regime hour")
ax.axvline(48, color=SERIES[1], linewidth=1)
ax.set_xticks(h); ax.set_xticklabels([f"{x} h" for x in h], fontsize=8)
style(ax); zero_line(ax); ax.set_ylabel("mean move after entry, %"); ax.legend(loc="upper left")
ax.set_title("squeeze_bull: the bounce is mostly done by 48 h; after that it is regime drift")
plt.tight_layout(); plt.show()
'''),
    ("md", r'''
## 4. The bot's own sequence

One position at a time, $10,000, 1 % risk per trade; drawdown against the fixed capital.
'''),
    ("code", r'''
pd.DataFrame(REP["single_open_sequence"]).T.round(1)
'''),
    ("md", r'''
## Conclusion (report-only)

- The incumbent's 48 h time stop is a coin flip on the trades it closes: held, they split 15 targets and 15 stops for the
  same average. Removing it changes nothing on average and adds drawdown; 12 h is clearly worse.
- For the no-stop twin the time stop is the risk control: without it, 15 trades end at −10 % and drawdown more than doubles.
- Wider targets, no target and "flush resumed" are within noise of the shipped exit.
- The twin beats the incumbent by +0.21 R per trade on this clean paired test, but almost all of it is before mid-2024. The
  n = 20 / 30 live re-cut stays the decision point.
'''),
]


MICRO_CELLS = [
    ("md", r'''
# 03 · Microstructure stage 1: do absorption, rejection and order-book events carry exit information?

**Question.** After the chento and squeeze_bull arms the user asked: *"isn't time simply just statistical overfitting?
... I still feel there is something in absorption, rejection and liquidity that could help to define exits better."*
Before building exits from those ideas, this stage measures whether the events carry information at all. At the first
event inside a trade, is the rest of the trade worth less than at matched moments without the event?

Frozen rules: [PREREGISTRATION_MICROSTRUCTURE.md](PREREGISTRATION_MICROSTRUCTURE.md). Write-up:
[findings_microstructure.md](findings_microstructure.md). This notebook recomputes every walk, event, placebo and
statistic from the frozen code and the checksum-verified data, checks them against the saved run, and shows the evidence.
**Verdict: NONE PROMOTED.**
'''),
    ("code", r'''
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from IPython.display import display

STUDY = Path.cwd()
assert (STUDY / "PREREGISTRATION_MICROSTRUCTURE.md").exists(), "execute from studies/notebooks/exit_policy_2026_09"
sys.path.insert(0, str(STUDY)); sys.path.insert(0, str(STUDY.parent / "orb_study"))
import micro_lib as M, micro_run as R
from review_plots import AXIS, INK2, SERIES, style
pd.set_option("display.width", 220); pd.set_option("display.max_columns", 30)
RES = M.RESULTS
F0, PRE, REP, V, ERA = (json.loads((RES / f).read_text()) for f in
                        ("freeze_F0.json", "preconditions.json", "report.json", "verdict.json", "exploratory_era_matched.json"))
changed = [f for f, h in F0["files"].items() if M.sha256(M.ROOT / f) != h]
print("F0", F0["created_utc"], "| frozen files unchanged:", not changed, "| inputs unchanged:", R.input_hashes() == F0["inputs_sha256"])
print("verdict:", V["verdict"], "written", V["created_utc"], "| decision family:", ", ".join(F0["family"]))
'''),
    ("md", r'''
## 1. The run is reproducible

Every trade is walked again on the 1-minute perp path, every event found again, every placebo and statistic recomputed.
The result must equal `events.csv.gz` and the classifications in `verdict.json`.
'''),
    ("code", r'''
_, series, pops = R.load_all()
events, report = R.compute_outcomes(series, pops, F0["family"])
saved = pd.read_csv(RES / "events.csv.gz")
num = ["elapsed_min", "bin", "controls", "controls_time_only", "cv", "cv_notime", "placebo", "placebo_notime",
       "placebo_time_only", "delta"]
same = (events[["pop", "kind", "tid"]].astype(str).equals(saved[["pop", "kind", "tid"]].astype(str))
        and all(np.allclose(events[c].to_numpy(float), saved[c].to_numpy(float), rtol=0, atol=1e-9, equal_nan=True) for c in num))
same_class = {k: t["classification"] for k, t in report["tests"].items() if t["primary"]} == V["classifications"]
print("events identical:", same, "| classifications identical:", same_class, "| event rows:", len(events))
pd.DataFrame({p: {"trades": len(tr), "assets": ", ".join(sorted(tr["asset"].unique())), "first entry": tr["entry_day"].min(),
                  "last entry": tr["entry_day"].max(), "median minutes in trade": int((tr["x"] - tr["i0"]).median()),
                  "exit mix (1-minute walk)": tr["kind"].value_counts().to_dict()} for p, tr in pops.items()}).T
'''),
    ("md", r'''
## 2. Preconditions (all passed before the freeze)

- **Inputs and identity.** Inputs matched their hashes, and every book file matched its published checksum; five
  archive days do not exist and stay missing. The three populations are exactly the earlier phases' trades, and every
  entry equals the perp close.
- **Walker.** The 1-minute walker reproduces squeeze_bull's S0 walks exactly, chento's 15-minute exit kinds on all 392
  trades, and the short_squeeze replay (spot path) on 68 of 71.
- **Fixtures and coverage.** 23 fixtures pass. The book covers 97.6–99.2 % of in-trade minutes.
- **Causality.** Masking the future changed no feature or event at ten cut points.
'''),
    ("code", r'''
display(pd.DataFrame({m: {"pass": PRE[m]["pass"]} for m in ("M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8")}).T)
m4 = PRE["M4"]
print("walker: squeeze_bull S0", m4["squeeze_bull_equal_to_S0"], "| chento agreement", round(m4["chento_kind_agreement"]["share"], 3),
      "| short_squeeze agreement", round(m4["short_squeeze_kind_agreement"]["share"], 3))
display(pd.DataFrame(m4["short_squeeze_kind_agreement"]["differences"]))
display(pd.DataFrame(PRE["M6"]["in_trade"]).T)
pd.DataFrame(PRE["M7"]["cuts"]).drop(columns="cut_minute")
'''),
    ("md", r'''
## 3. How often the events happen inside trades

This decided what could be tested: a decision test needed 30 event trades, each with at least 3 matched controls. Only
chento's absorption, rejection and book tests reached it. short_squeeze trades last a median of 65 minutes and almost
never meet these events. squeeze_bull meets them on 4–21 % of trades. The events are rare by construction: absorbed
buying (E1 against a long) holds in about one BTC minute in 5,500.
'''),
    ("code", r'''
rows = []
for p, v in PRE["M8"]["populations"].items():
    for k, t in v["tests"].items():
        rows.append({"population": p, "event": k, "eligible": t["eligible_trades"], "with event": t["trades_with_event"],
                     "share": t["share_with_event"], "median hours to first": t["median_elapsed_hours"],
                     "event minutes per 24 h in trade": t["event_minutes_per_24h_in_trade"],
                     "included (>= 3 controls)": t["included_with_min_controls"], "MDE 80 % (R)": t["mde80_R"],
                     "decision family": t.get("in_family")})
C = pd.DataFrame(rows)
display(C.round(3))
kinds = list(M.KINDS)
fig, ax = plt.subplots(figsize=(9, 4.2))
width = 0.26
yk = np.arange(len(kinds))
for j, p in enumerate(M.POPULATIONS):
    share = [C[(C.population == p) & (C.event == k)]["share"].iat[0] for k in kinds]
    ax.barh(yk + (j - 1) * width, share, height=width - 0.04, color=SERIES[j], label=p)
ax.set_yticks(yk); ax.set_yticklabels([k.replace("_", " ") for k in kinds]); ax.invert_yaxis()
style(ax, grid_axis="x"); ax.set_xlabel("share of trades with the event before their exit")
ax.legend(loc="lower right"); ax.set_title("How often each event occurs inside a trade")
plt.tight_layout(); plt.show()
b = series["BTC"]
fin = np.isfinite(b.z_flow)
with np.errstate(invalid="ignore"):
    print("all BTC minutes: |z| >= 3 buy side %.2f %%, sell side %.2f %%; E1 against (long) %.3f %%; E1 supportive (long) %.3f %%" % (
        100 * (b.z_flow[fin] >= 3).mean(), 100 * (b.z_flow[fin] <= -3).mean(),
        100 * ((b.z_flow >= 3) & (b.dp5 <= 0))[fin].mean(), 100 * ((b.z_flow <= -3) & (b.dp5 >= 0))[fin].mean()))
'''),
    ("md", r'''
## 4. The test: the rest of the trade after the first event, against matched moments

Δ = continuation value at the first event minus the placebo. The continuation value is the move from that minute to the
shipped exit, in R. The placebo is the same quantity for other trades matched on four things: the same point in their
life (±5 % of the horizon), the same 0.25 R profit bin, the same direction, and no event yet and no calendar overlap.
Negative Δ means the event came before worse-than-matched continuation. Intervals: 30-day block bootstrap of entry
days. Filled markers are the pre-registered decision family, open markers are descriptive.
'''),
    ("code", r'''
def forest(ax, pop, title):
    keys = [k for k in M.KINDS if REP["tests"][f"{pop}:{k}"]["delta"].get("n")]
    y = np.arange(len(keys))[::-1]
    for yi, k in zip(y, keys):
        t = REP["tests"][f"{pop}:{k}"]; d = t["delta"]
        colour = SERIES[0] if k in M.PRIMARY else SERIES[1]
        ax.hlines(yi, d["ci95"][0], d["ci95"][1], color=AXIS, linewidth=2)
        ax.scatter([d["mean"]], [yi], s=48, zorder=3, facecolors=colour if t["in_family"] else "none",
                   edgecolors=colour, linewidths=1.5)
        ax.annotate(f"n = {d['n']}", (d["ci95"][1], yi), xytext=(4, -3), textcoords="offset points", fontsize=7, color=INK2)
    ax.axvline(0, color=INK2, linewidth=0.8)
    ax.set_yticks(y); ax.set_yticklabels([k.replace("_", " ") for k in keys])
    style(ax, grid_axis="x"); ax.set_xlabel("Δ, R per event trade (95 % interval)"); ax.set_title(title, fontsize=10)

fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4))
forest(axes[0], "chento", "chento: 392 trades")
forest(axes[1], "squeeze_bull", "squeeze_bull: 122 trades, all descriptive")
handles = [Line2D([], [], marker="o", linestyle="", color=SERIES[0], label="event against the position"),
           Line2D([], [], marker="o", linestyle="", color=SERIES[1], label="sign control (pattern with the position)")]
fig.legend(handles=handles, loc="lower center", ncol=2, bbox_to_anchor=(0.5, 0.0))
fig.suptitle("Continuation after the first event minus matched moments", fontsize=11, fontweight="bold")
plt.tight_layout(rect=(0, 0.07, 1, 1)); plt.show()
T = pd.DataFrame({k: {"n": t["delta"].get("n"), "delta": t["delta"].get("mean"),
                      "ci95_lo": t["delta"]["ci95"][0] if t["delta"].get("n") else None,
                      "ci95_hi": t["delta"]["ci95"][1] if t["delta"].get("n") else None,
                      "p_one_sided": t["delta"].get("p_one_sided_less"), "holm_p": t.get("holm_p"),
                      "first_half": t["delta"].get("first_half"), "second_half": t["delta"].get("second_half"),
                      "time-only placebo": t["secondary_time_only"].get("mean"),
                      "no-time-exit walk": t["secondary_no_time_exit"].get("mean"),
                      "classification": t.get("classification", "sign control")} for k, t in REP["tests"].items()}).T
T
'''),
    ("md", r'''
## 5. What holding after an event was worth, in absolute terms

For an exit, what matters is the continuation value itself. On chento, holding after every event still returned +0.5 to
+1.0 R per event trade. Exiting at the first absorption would have cost 0.24 R per trade overall, and at the first
rejection 0.36 R (price only; the exit leg is paid either way). On squeeze_bull, holding after absorption or rejection was
worth about zero, and so was holding after acceptance, the opposite of a rejection.
'''),
    ("code", r'''
rows = []
for p in ("chento", "squeeze_bull"):
    N = len(pops[p])
    for k in M.KINDS:
        e = events[(events["pop"] == p) & (events["kind"] == k)]
        if not len(e):
            continue
        rows.append({"population": p, "event": k, "event trades": len(e), "mean CV after the event": e["cv"].mean(),
                     "mean matched placebo": REP["tests"][f"{p}:{k}"]["mean_placebo"],
                     "exit at first event minus shipped, R per trade": -e["cv"].sum() / N})
A = pd.DataFrame(rows)
display(A.round(3))
fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.0))
for ax, p in zip(axes, ("chento", "squeeze_bull")):
    a = A[A.population == p].reset_index(drop=True)
    y = np.arange(len(a))[::-1]
    ax.scatter(a["mean CV after the event"], y, s=46, color=SERIES[0], zorder=3)
    ax.scatter(a["mean matched placebo"], y, s=46, facecolors="none", edgecolors=SERIES[2], linewidths=1.5, zorder=3)
    ax.axvline(0, color=INK2, linewidth=0.8)
    ax.set_yticks(y); ax.set_yticklabels([k.replace("_", " ") for k in a["event"]])
    style(ax, grid_axis="x"); ax.set_xlabel("mean continuation value, R"); ax.set_title(p, fontsize=10)
handles = [Line2D([], [], marker="o", linestyle="", color=SERIES[0], label="after the first event"),
           Line2D([], [], marker="o", linestyle="", markerfacecolor="none", markeredgecolor=SERIES[2], label="matched moments")]
fig.legend(handles=handles, loc="lower center", ncol=2, bbox_to_anchor=(0.5, 0.0))
fig.suptitle("What the rest of the trade was worth", fontsize=11, fontweight="bold")
plt.tight_layout(rect=(0, 0.07, 1, 1)); plt.show()
'''),
    ("md", r'''
## 6. Halves, years, and an exploratory era-matched control

Every chento event and every sign control was positive in the first half and negative in the second. The frozen placebo
draws controls from all years. A period in which holding paid more for every trade (2021–2023, where chento's
no-time-stop gain sat) therefore shows up in whichever events fell in it. An exploratory control added after the verdict
(`micro_explore.py`, not pre-registered) keeps only controls entering within ±365 days of the event trade. It removes
part of the swing, but no interval moves away from zero.
'''),
    ("code", r'''
rows = []
for k, s in ERA["tests"].items():
    f = REP["tests"][k]["delta"]
    rows.append({"test": k, "all years Δ": f["mean"], "halves, all years": f"{f['first_half']:+.2f} / {f['second_half']:+.2f}",
                 "era-matched Δ": s["mean"], "era 95 % lo": s["ci95"][0], "era 95 % hi": s["ci95"][1],
                 "halves, era-matched": f"{s['first_half']:+.2f} / {s['second_half']:+.2f}", "n, era-matched": s["n"]})
display(pd.DataFrame(rows).set_index("test").round(3))
pd.DataFrame({k.split(":")[1]: {y: v["mean"] for y, v in REP["tests"][k]["delta"]["by_year"].items()}
              for k in REP["tests"] if k.startswith("chento:") and REP["tests"][k]["delta"].get("n")}).round(2)
'''),
    ("md", r'''
## Conclusion

- **Nothing promoted.** On chento, the only population with enough events, none of three events carries information
  this sample can detect (Holm-adjusted p 0.69–0.83): absorption against the position, rejection of the prior-day
  extreme, and an order book tilted against the position. Their sign controls behave the same way, and holding after
  every event still paid.
- **short_squeeze almost never meets these events** (0–2 of 71 trades). Its trades resolve in about an hour, before an
  extreme flow or book reading appears, so exits keyed on them could not change it.
- **squeeze_bull meets them on 4–21 % of trades**, too few to test. Holding after them was worth about zero, but so was
  holding after their opposites.
- **What this does not rule out.** Information smaller than about 0.3–0.6 R per event trade on chento, and anything the
  public archive cannot see: sub-minute order flow, live depth, individual prints. By the pre-registration this closes
  the line for these events at this resolution.
'''),
]


ANATOMY_CELLS = [
    ("md", r'''
# 04 · squeeze_bull top anatomy (exploratory, stage A)

**The user's question.** *"What if we study what happened after squeeze bull got an entry. Assumption is that there is
spike up, like we just had. What happened just before the price action reversed? How does it look like and how does the
all previous similar events look like? Maybe there is no reverse at all, but something else that tells it's time to
exit?"*

Protocol: [PROTOCOL_TOP_ANATOMY.md](PROTOCOL_TOP_ANATOMY.md) (with Amendment A1). Write-up:
[findings_top_anatomy.md](findings_top_anatomy.md). **Exploratory:** every number here describes one discovery set of
122 fires; nothing is decided. Any exit idea is tested only on holdout data (ETH flushes, BTC non-bull flushes, live
fires) in a separate, pre-registered stage B.

The one trap: lining paths up on their highest point always shows a rise into it and a fall after it. The comparison
that matters is **the final top against the same fire's earlier new highs that paused and then kept going**, measured
with data known at that minute.
'''),
    ("code", r'''
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from IPython.display import display

STUDY = Path.cwd()
assert (STUDY / "PROTOCOL_TOP_ANATOMY.md").exists(), "execute from studies/notebooks/exit_policy_2026_09"
sys.path.insert(0, str(STUDY)); sys.path.insert(0, str(STUDY.parent / "orb_study"))
import anatomy_lib as A, micro_lib as ML
from review_plots import AXIS, INK2, MUTED, NEUTRAL, SERIES, style, zero_line
pd.set_option("display.width", 220); pd.set_option("display.max_columns", 40)
RES = A.RESULTS
MA, MA1 = (json.loads((RES / f).read_text()) for f in ("manifest_A.json", "manifest_A1.json"))
changed_A = [f for f, h in MA["files"].items() if ML.sha256(STUDY / f) != h]
changed_A1 = [f for f, h in MA1["files"].items() if ML.sha256(STUDY / f) != h]
print("stage A run", MA["created_utc"], "| files changed since:", changed_A, "(the protocol gained Amendment A1)")
print("amendment A1", MA1["created_utc"], "| files changed since:", changed_A1)
SUM, TVF, REP, SMAP, DC, CASE, PP = (json.loads((RES / f).read_text()) for f in
    ("summary.json", "top_vs_false.json", "repair.json", "state_map.json", "data_checks.json", "case_SJ-4250.json", "profiles_paired.json"))
SH = pd.read_csv(RES / "shapes.csv")
'''),
    ("md", r'''
## 1. The run is reproducible

Shapes, the top-versus-false-top table, the repair table and the state map are computed again from the library and
the checksum-verified data, then compared with the saved files.
'''),
    ("code", r'''
mkt = A.load_market()
fires = A.load_fires(mkt)
sh = A.shapes(mkt, fires)
_, tvf = A.top_vs_false(mkt, sh)
_, rep = A.repair(mkt, sh)
_, smap = A.state_map(mkt, fires)
same_shapes = sh[["fid", "shape", "S", "T", "C"]].astype(str).equals(SH[["fid", "shape", "S", "T", "C"]].astype(str))
close = lambda a, b: json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
print("shapes identical:", same_shapes, "| top vs false:", close(json.loads(json.dumps(tvf)), TVF),
      "| repair:", close(json.loads(json.dumps(rep)), REP), "| state map:", close(json.loads(json.dumps(smap)), SMAP))
'''),
    ("md", r'''
## 2. The case: SJ-4250

Entry 2026-09-11 18:00 at 77,493.9. The flush had taken price down 2.2 % from 79,208 over four hours. On the
convention the strategy was validated on (open interest at each bar's close), the open-interest drop was −1.8 %, short
of the −2 % trigger; section 8 explains why the bot fired.

- **Before the time stop.** Price sagged for almost three days, and the entry minute stayed its high. Open interest
  was fully back by 09-13 13:34, before the time stop closed the trade on 09-13 17:00.
- **The spike.** Price crossed +2 % on 09-14 18:19, got back to where the flush started at 18:46, paused once at 18:48,
  topped at 20:23 at +2.68 %, and fell 2 % by 09-15 00:20.
- **Positioning during the spike.** Open interest peaked at 2.0× the flushed amount at 06:15 on 09-14 and fell during
  the spike, to 0.5 at the top. The all-account long/short ratio went from +13 % to −20 % since entry: accounts sold into
  the rise. After the top both reversed.
- **Where it went.** Price reached 75,697 (−2.3 % from entry) on 09-15.
'''),
    ("code", r'''
cf = pd.read_csv(RES / "case_SJ-4250_features.csv.gz")
cf = cf[cf["rel_min"] >= -6 * 60]
h = cf["rel_min"] / 60
E = CASE["fire"]["entry"]; P0 = CASE["fire"]["P0"]
marks = {"time stop": (A.CASE_EXIT_TS - CASE["fire"]["entry_ts"]) / 3600, "spike +2 %": CASE["shape"]["S"] / 60,
         "top": CASE["shape"]["T"] / 60, "2 % reversal": CASE["shape"]["C"] / 60}
fig, axes = plt.subplots(5, 1, figsize=(11, 11), sharex=True, gridspec_kw={"height_ratios": [2.2, 1, 1, 1, 1]})
ax = axes[0]
ax.plot(h, cf["close"], color=SERIES[0], linewidth=1.2)
for level, lab, colour in ((E * 1.03, "target +3 %", SERIES[2]), (P0, "flush start (P0)", SERIES[3]), (E, "entry", INK2), (E * 0.98, "stop −2 %", SERIES[1])):
    ax.axhline(level, color=colour, linewidth=0.9, linestyle="--")
    ax.annotate(lab, xy=(h.iloc[0], level), xytext=(2, 3), textcoords="offset points", fontsize=7, color=INK2)
ax.set_ylabel("BTCUSDT perp")
ax.set_title("SJ-4250: from entry to the spike and its reversal (hours since entry)")
panels = [("OR", "OI repair (1 = flushed OI back)"), ("premium_bp", "premium index, bp"),
          ("account_lsr_chg_pct", "account long/short ratio, % since entry"), ("volume_ratio_60", "60-min volume ÷ 7-day normal")]
for ax, (col, lab) in zip(axes[1:], panels):
    ax.plot(h, cf[col], color=SERIES[0], linewidth=1.0)
    ax.set_ylabel(lab, fontsize=7)
for ax in axes:
    style(ax)
    for name, x in marks.items():
        ax.axvline(x, color=NEUTRAL if name == "time stop" else SERIES[1], linewidth=0.9)
for name, x in marks.items():
    axes[0].annotate(name, xy=(x, axes[0].get_ylim()[1]), xytext=(3, -10), textcoords="offset points", fontsize=7, color=INK2, rotation=90, va="top")
axes[1].axhline(1, color=AXIS, linewidth=0.8); zero_line(axes[2]); zero_line(axes[3]); axes[4].axhline(1, color=AXIS, linewidth=0.8)
axes[-1].set_xlabel("hours since entry")
plt.tight_layout(); plt.show()
pd.Series({k: CASE[k] for k in ("entry_utc", "time_stop_utc", "spike_start_utc", "top_utc", "reversal_utc", "top_price",
                                "top_runup_pct", "PR_at_top", "OR_at_top", "first_PR_ge_1_utc", "first_OR_ge_1_utc",
                                "P0_minus_entry_pct", "oi_flush_pct_archive", "panel_end_utc")}).to_frame("SJ-4250")
'''),
    ("md", r'''
## 3. All 122 fires: how the bounces went

**Shapes.** In the 7 days after entry, 103 fires spiked at least +2 % and then fell 2 % from their peak. 16 never
reached +2 %; 3 reached it and did not fall 2 %.

**Timing.** The spike came a median 18 h after entry. Once price crossed +2 %, the top came fast: a median 2 h later,
and within 12 minutes for a quarter of them. The top was at a median +3.2 %, 28 h after entry. 35 of the 103 tops came
after 48 h, where the time stop had already closed the trade. After the top, price fell a median 3.2 % within 24 h.
'''),
    ("code", r'''
sr = SH[SH["shape"] == "spike_reversal"]
display(SH["shape"].value_counts().to_frame("fires"))
display(sr[["hours_to_spike", "hours_spike_to_top", "hours_to_top", "top_runup_pct", "fall_24h_after_top_pct", "max_pct_7d"]]
        .describe(percentiles=[0.25, 0.5, 0.75]).T.round(2))
fig, axes = plt.subplots(1, 2, figsize=(11, 3.6))
axes[0].hist(sr["hours_to_top"], bins=np.arange(0, 170, 6), color=SERIES[0], edgecolor="white", linewidth=0.8)
axes[0].axvline(48, color=SERIES[1], linewidth=1.2)
axes[0].annotate("time stop 48 h", xy=(48, axes[0].get_ylim()[1]), xytext=(4, -12), textcoords="offset points", fontsize=8, color=INK2)
axes[0].set_xlabel("hours from entry to the top"); axes[0].set_ylabel("fires"); style(axes[0])
axes[1].hist(sr["top_runup_pct"], bins=np.arange(2, 13.5, 0.5), color=SERIES[0], edgecolor="white", linewidth=0.8)
axes[1].axvline(3, color=SERIES[2], linewidth=1.2)
axes[1].annotate("target +3 %", xy=(3, axes[1].get_ylim()[1]), xytext=(4, -12), textcoords="offset points", fontsize=8, color=INK2)
axes[1].set_xlabel("top, % above entry"); style(axes[1])
fig.suptitle("103 bounces that spiked and reversed", fontsize=11, fontweight="bold")
plt.tight_layout(); plt.show()
'''),
    ("md", r'''
## 4. Every top, aligned

Price in % of the top, from 24 h before to 24 h after. The thick line is the median. Small multiples below show every
fire; the dashed line is the entry price, the orange tick is where the 48 h time stop fell.
'''),
    ("code", r'''
G = pd.read_csv(RES / "gallery.csv")
M = np.load(RES / "gallery.npz")["close_pct_of_top"]
lags_h = (G["lags_from"].iat[0] + np.arange(M.shape[1]) * G["lag_step"].iat[0]) / 60
fig, ax = plt.subplots(figsize=(10, 3.8))
for row in M:
    ax.plot(lags_h, row, color=NEUTRAL, linewidth=0.4, alpha=0.5)
ax.plot(lags_h, np.nanmedian(M, axis=0), color=SERIES[0], linewidth=2)
ax.axvline(0, color=INK2, linewidth=0.8); style(ax); ax.set_ylim(-9, 1)
ax.set_xlabel("hours from the top"); ax.set_ylabel("% of the top")
ax.set_title("103 bounce tops: price around the top (median in blue)")
plt.tight_layout(); plt.show()
cols = 10
rows = int(np.ceil(len(G) / cols))
fig, axes = plt.subplots(rows, cols, figsize=(16, 1.25 * rows), sharex=True, sharey=True)
for k, ax in enumerate(axes.flat):
    if k >= len(G):
        ax.axis("off"); continue
    g = G.iloc[k]
    ax.plot(lags_h, M[k], color=SERIES[0], linewidth=0.7)
    ax.axhline(g["entry_pct_of_top"], color=INK2, linewidth=0.5, linestyle="--")
    ts_h = g["time_stop_rel_min"] / 60
    if -24 <= ts_h <= 24:
        ax.axvline(ts_h, color=SERIES[1], linewidth=0.8)
    ax.set_title(pd.Timestamp(int(g["fid"].split(":")[1]), unit="s").strftime("%Y-%m-%d"), fontsize=6, color=INK2)
    ax.set_ylim(-9, 1); ax.tick_params(labelsize=5, length=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
plt.tight_layout(); plt.show()
'''),
    ("md", r'''
## 5. Just before the top, against the new highs that kept going

At the top minute, the paired table below shows no difference in premium, taker flow, volume, order book or top-trader
positioning from the same fire's earlier paused highs (56 fires have both). What differs is what grows with price and
time: the top is higher, about 6 h later, with more open interest rebuilt, and with the all-account long/short ratio
further down. The overlay (Amendment A1, same 56 fires) shows the hours before: volume into the final top runs a little
lower, but no interval at 15–240 minutes before excludes zero. **Nothing visible in real time says "this one is the top."**
'''),
    ("code", r'''
T = pd.DataFrame(TVF).T[["fires", "median_at_top", "median_at_false_tops", "median_diff", "ci95_median", "share_top_higher"]]
display(T)
lags_h = np.array(PP["lags_min"]) / 60
show = [("volume_ratio_60", "60-min volume ÷ 7-day normal"), ("taker_buy_share_60", "taker buy share, 60 min"),
        ("premium_bp", "premium index, bp"), ("book_z", "order-book ±1 % imbalance z"),
        ("OR", "OI repair"), ("account_lsr_chg_pct", "account long/short, % since entry")]
fig, axes = plt.subplots(2, 3, figsize=(12, 6.2))
for ax, (col, lab) in zip(axes.flat, show):
    ax.plot(lags_h, PP[col]["top_median"], color=SERIES[0], linewidth=1.4)
    ax.plot(lags_h, PP[col]["false_median"], color=SERIES[1], linewidth=1.4)
    ax.axvline(0, color=INK2, linewidth=0.8); style(ax); ax.set_title(lab, fontsize=9); ax.set_xlim(-12, 12)
for ax in axes[1]:
    ax.set_xlabel("hours from the high")
handles = [Line2D([], [], color=SERIES[0], label="the final top"), Line2D([], [], color=SERIES[1], label="earlier highs that kept going")]
fig.legend(handles=handles, loc="lower center", ncol=2, bbox_to_anchor=(0.5, 0.0))
fig.suptitle("Median features around the final top and around earlier paused highs (same 56 fires)", fontsize=11, fontweight="bold")
plt.tight_layout(rect=(0, 0.06, 1, 1)); plt.show()
pd.DataFrame({col: {lag: f"{v['median_diff']:+.2f} ({v['ci95'][0]:+.2f}, {v['ci95'][1]:+.2f})" for lag, v in PP[col]["at_lags"].items()}
              for col, _ in show}).T.rename(columns=lambda c: f"{c} min")
'''),
    ("md", r'''
## 6. Is the top where the flush is repaired?

No. 87 % of tops went past the price the flush started from. The median top sat 2.3 flush-sizes above entry, and
bounce size barely depends on flush size. After price first got back to where the flush started, the next 24 h still
averaged +0.8 %. Open-interest repair does not mark the top either: at the top, a median half of the flushed open
interest was back, with a very wide spread.
'''),
    ("code", r'''
display(pd.DataFrame({k: v for k, v in REP.items() if isinstance(v, dict)}).T)
SH["flush_pct"] = (SH["P0"] / SH["entry"] - 1) * 100
s2 = SH[SH["shape"] == "spike_reversal"]
fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
pr_top = pd.read_csv(RES / "repair_per_fire.csv").dropna(subset=["PR_at_top"])["PR_at_top"]
axes[0].hist(pr_top.clip(upper=10), bins=np.arange(-1, 10.5, 0.5), color=SERIES[0], edgecolor="white", linewidth=0.8)
axes[0].axvline(1, color=SERIES[3], linewidth=1.2)
axes[0].annotate("flush start", xy=(1, axes[0].get_ylim()[1]), xytext=(4, -12), textcoords="offset points", fontsize=8, color=INK2)
axes[0].set_xlabel("price repair at the top (flush-sizes above entry, capped at 10)"); axes[0].set_ylabel("fires"); style(axes[0])
axes[1].scatter(s2["flush_pct"], s2["top_runup_pct"], s=18, color=SERIES[0])
axes[1].set_xlabel("flush size, % (price drop over the rule's 4 h)"); axes[1].set_ylabel("top, % above entry"); style(axes[1], grid_axis="both")
axes[1].set_title(f"rank correlation {s2['flush_pct'].rank().corr(s2['top_runup_pct'].rank()):.2f}", fontsize=9)
plt.tight_layout(); plt.show()
'''),
    ("md", r'''
## 7. "Something else that tells it's time": the state map

At every hour of the 7 days after each fire: the move over the next 24 h, by state. The grey line is the bull regime's
own 24 h drift (+0.21 %).
- **Stall.** While the bounce was still making new highs (last new high within an hour), the next 24 h averaged
  +0.51 %. After a day with no new high it averaged +0.12 %.
- **Price repair.** Inside the repair zone (up to 1.5 flush-sizes above entry) the next 24 h averaged +0.35 to +0.48 %.
  Beyond 1.5 it averaged +0.13 %.
- **OI repair.** No pattern.

In both cases the extra is gone, not reversed: the rest of the trade is worth about what any bull-regime hour is worth.
'''),
    ("code", r'''
fig, axes = plt.subplots(1, 3, figsize=(13, 3.8), sharey=True)
labels = {"PR": ["< 0", "0–0.5", "0.5–1", "1–1.5", "> 1.5"], "OR": ["< 0", "0–0.5", "0.5–1", "1–1.5", "> 1.5"],
          "stall_min": ["< 1 h", "1–6 h", "6–24 h", "> 24 h"]}
titles = {"PR": "price repair (flush-sizes above entry)", "OR": "OI repair", "stall_min": "time since the last new high"}
for ax, name in zip(axes, ("PR", "OR", "stall_min")):
    bins = SMAP[name]
    x = np.arange(len(bins))
    m = [b["mean_fwd24_pct"] for b in bins]
    lo = [b["ci95"][0] for b in bins]; hi = [b["ci95"][1] for b in bins]
    ax.vlines(x, lo, hi, color=AXIS, linewidth=2)
    ax.scatter(x, m, s=46, color=SERIES[0], zorder=3)
    ax.axhline(SMAP["bull_regime_drift_24h_pct"], color=NEUTRAL, linewidth=1.2)
    zero_line(ax)
    ax.set_xticks(x); ax.set_xticklabels(labels[name], fontsize=8); style(ax); ax.set_title(titles[name], fontsize=9)
axes[0].set_ylabel("mean move over the next 24 h, % (95 % interval)")
axes[2].annotate("bull-regime drift", xy=(len(SMAP["stall_min"]) - 1, SMAP["bull_regime_drift_24h_pct"]), xytext=(-60, 8),
                 textcoords="offset points", fontsize=8, color=INK2)
fig.suptitle("What the next 24 h were worth, by state (122 fires, hourly states over 7 days)", fontsize=11, fontweight="bold")
plt.tight_layout(); plt.show()
'''),
    ("md", r'''
## 8. Data checks, and a production data issue found on the way

The flush-start price matches the ledger exactly, and the archive's open-interest change matches the ledger's
(median difference 0.03 points). But the hourly open interest the live bots read changed meaning on **2026-06-10**, the
day the CoinDesk OI feed died and the Binance fetcher took over. Before, the value stamped at an hour was the open
interest at the end of that hour. Since then it is the open interest at the start of the hour, one bar staler than the
price bar it is joined to. squeeze_bull's live flush test has used that stale value since June, short_squeeze's Asia OI
change too. March–April 2022 match the archive at no lag. Reported only; nothing was changed.

**What it did to the signal.** The sleeve's own flush, cooldown and bull-gate code was run on the hourly snapshot twice:
once with the stored open interest, and once with the value at each bar's close.
- **Since the switch**, the stored series gives two bull fires: 2026-09-04 14:00 and 2026-09-11 17:00.
- **The bar-close series** gives one: 2026-09-04 13:00.
- **SJ-4250**, the trade that started this study, is a fire only because of the stale stamp. Its 4-hour open-interest
  change was −2.48 % on the stored values and −1.79 % at bar closes.
'''),
    ("code", r'''
print({k: v for k, v in DC.items() if k != "ledger_oi_timestamp_lag_by_month"})
lag = pd.DataFrame(DC["ledger_oi_timestamp_lag_by_month"]).T
display(lag[["best", "median_rel_diff_best", "stamp_minus_5m", "stamp_plus_55m", "stamp_plus_60m"]].iloc[::3])
import sqb_lib as S
from datetime import datetime, timezone
sm = S.sleeve_math()
with np.load(S.HOURLY) as z:
    hts, hclose, hoi = z["ts"].astype(np.int64), z["close"].astype(float), z["oi_close"].astype(float)
hdays = [datetime.fromtimestamp(int(x), tz=timezone.utc).date() for x in hts]
daily = {}
for d, c in zip(hdays, hclose):
    daily[d] = c
switch = int(datetime(2026, 6, 10, tzinfo=timezone.utc).timestamp())
bar_close_oi = np.where(hts >= switch - 3600, np.r_[hoi[1:], np.nan], hoi)
def bull_fires_since_switch(series):
    vals = [None if not np.isfinite(v) else float(v) for v in series]
    return [datetime.fromtimestamp(int(hts[k]), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            for k in sm.kept_flush_indices(vals, hclose.tolist())
            if hts[k] >= switch and sm.classify_regime(sm.backward_only_ret_30d(daily, hdays[k])) == "bull_30d"]
print("bull fires since 2026-06-10 | stored OI (what the bot reads):", bull_fires_since_switch(hoi),
      "| OI at each bar's close (research convention):", bull_fires_since_switch(bar_close_oi))
'''),
    ("md", r'''
## Conclusion (exploratory)

- **What happened before the reversal:** a fast push through +2 % to a new high, usually within about 2 hours, with a
  premium pop and a burst of taker buying. That is the same picture as at every earlier high that kept going. At this
  resolution, nothing in real time singles out the final top.
- **The repair levels are not the exit.** Price usually runs well past where the flush started, and bounces are about
  +3 % regardless of the flush's size.
- **What does change is state.** After a day without a new high, or once price is more than 1.5 flush-sizes above
  entry, the next 24 h are worth about the bull regime's drift, no longer the bounce's extra.
- **SJ-4250 would not have been saved** by any of these. It went 56 hours without a new high before its spike, so a
  stall exit would have left even earlier than the time stop. It should not have been a trade at all: it fired only
  because the live open-interest feed has carried each hour's opening value since 2026-06-10.
- **Stage B candidate:** "exit after 24 h without a new high" in place of the fixed 48 h, a time rule tied to the
  bounce's progress rather than to the clock. It would be tested on ETH flushes, BTC non-bull flushes and live fires, if
  the user wants to go on.
'''),
]


def build(cells: list, name: str, kernel: str) -> Path:
    nb = nbformat.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": kernel, "display_name": "p300 venv", "language": "python"}
    for kind, text in cells:
        text = text.strip("\n")
        nb.cells.append(nbformat.v4.new_markdown_cell(text) if kind == "md" else nbformat.v4.new_code_cell(text))
    NotebookClient(nb, timeout=1800, kernel_name=kernel, resources={"metadata": {"path": str(HERE)}}).execute()
    out = HERE / name
    nbformat.write(nb, out)
    return out


def main(which: list[str]) -> None:
    tmp = tempfile.mkdtemp(prefix="exitpol_kernel_")
    kd = Path(tmp) / "kernels" / "p300venv"
    kd.mkdir(parents=True)
    (kd / "kernel.json").write_text(json.dumps({"argv": [str(VENV_PY), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
                                                "display_name": "p300 venv", "language": "python"}), encoding="utf-8")
    os.environ["JUPYTER_PATH"] = tmp + os.pathsep + os.environ.get("JUPYTER_PATH", "")
    books = {"01": (CHENTO_CELLS, "01_chento_exit_policy.ipynb"), "02": (SQB_CELLS, "02_squeeze_bull_exit_policy.ipynb"),
             "03": (MICRO_CELLS, "03_microstructure_information.ipynb"),
             "04": (ANATOMY_CELLS, "04_squeeze_bull_top_anatomy.ipynb")}
    for key, (cells, name) in books.items():
        if not which or key in which:
            print("wrote", build(cells, name, "p300venv"))


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])

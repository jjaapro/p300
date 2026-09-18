"""Cell sources for the ORB review notebooks (executed by build_notebooks.py)."""
from __future__ import annotations

SETUP = r'''
import json, subprocess, sys
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
from IPython.display import display

STUDY = Path.cwd()
assert (STUDY / "PREREGISTRATION.md").exists(), "execute from studies/notebooks/orb_study"
sys.path.insert(0, str(STUDY))
import orb_calendars as cal, orb_checks, orb_data, orb_engine as eng, orb_metrics as met, orb_policies as pol
from review_plots import AXIS, INK2, MUTED, NEGATIVE, NEUTRAL, SERIES, style, zero_line
pd.set_option("display.width", 220); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 80)
RESULTS = STUDY / "results"
'''

# ---------------------------------------------------------------------------------------------
NB01 = [
    ("md", r'''
# 01 · Data and calendars

**ORB study, notebook 1 of 5.** Question: is the data good enough to test an opening-range breakout on
Binance BTCUSDT and ETHUSDT perpetuals at one-minute resolution, before any strategy outcome is computed?

Frozen inputs: [PREREGISTRATION.md](PREREGISTRATION.md) section 2 (data, gate, Amendment A1),
`data/raw/binance_um/manifest.json`, `configs/calendar_*.json`. The gate result recorded before the first
outcome is `results/data_gate.json` (hashed into `results/freeze_F0.json`). This notebook recomputes the
checks from the panels and compares them with that record; it never rewrites it.

Re-create the inputs from the repository root with `venv\Scripts\python.exe studies\notebooks\orb_study\orb_data.py download`
then `... orb_data.py build` (about 290 MB of zips, 181 MB of panels).
'''),
    ("code", SETUP + r'''
GATE = json.loads((RESULTS / "data_gate.json").read_text())
print("gate recorded", GATE["created_utc"], "| passed:", GATE["gate_passed"])
'''),
    ("md", r'''
## 1. What was downloaded

Every zip was checked against the SHA-256 in Binance's published `.CHECKSUM` file. Funding settlements after
the last monthly archive (2026-09-01 → cutoff) come from the public REST endpoint and are saved verbatim.
'''),
    ("code", r'''
manifest = json.loads((STUDY / "data/raw/binance_um/manifest.json").read_text())
files = pd.DataFrame(manifest["files"])
print(manifest["source"], "| panel", manifest["panel_start_utc"], "->", manifest["cutoff_utc_exclusive"], "(exclusive)")
files.groupby(["symbol", "kind"]).agg(files=("file", "size"), megabytes=("bytes", lambda b: round(b.sum() / 1e6, 1)),
                                      checksums_verified=("checksum_ok", lambda c: bool(c.dropna().all()) if c.notna().any() else None))
'''),
    ("md", r'''
## 2. Panel integrity

A dense UTC minute grid from 2020-01-01 to 2026-09-14 (exclusive): 3,525,120 minutes per asset. Bars are
open-stamped, so the row at minute *t* covers [*t*, *t*+60 s) and is known at *t*+60 s.
'''),
    ("code", r'''
rows = {}
for sym in orb_data.SYMBOLS:
    fresh = orb_checks.panel_integrity(sym)
    frozen = GATE[f"{sym}_panel"]
    rows[sym] = {k: v for k, v in fresh.items() if k not in ("outage_runs", "logical_sha256")}
    rows[sym]["matches_frozen_gate"] = fresh == frozen
pd.DataFrame(rows)
'''),
    ("md", r'''
Every minute is present, but not every minute traded. The archive fills exchange outages with flat,
zero-volume bars. Those are not prices anyone could trade, so the engine treats every zero-volume bar as
missing. Most sit in runs of 13–99 minutes (Binance maintenance or incidents); ETH also has 24 isolated
tradeless minutes in 2020, when the contract was thin.
'''),
    ("code", r'''
for sym in orb_data.SYMBOLS:
    runs = pd.DataFrame(GATE[f"{sym}_panel"]["outage_runs"])
    runs["year"] = runs.start_utc.str[:4]
    print(f"{sym}: {runs.minutes.sum()} tradeless minutes; runs longer than one minute:")
    display(runs[runs.minutes > 1].reset_index(drop=True))
    print("  isolated single tradeless minutes by year:", runs[runs.minutes == 1].groupby("year").size().to_dict())
'''),
    ("md", r'''
## 3. Archive against the REST API

Two independent retrieval paths from the same exchange: our 1-minute archive aggregated to 15 minutes against
the project's REST-fetched perpetual tables (`cd_futures_15m`, `cd_futures_eth_15m`), and 1-minute bars against
`screener_klines_1m`. prod.db is opened read-only. The feed keeps writing, so a recomputation can differ slightly
from the frozen record; both are shown.
'''),
    ("code", r'''
rows = []
for sym in orb_data.SYMBOLS:
    fresh, frozen = orb_checks.archive_vs_rest_15m(sym), GATE[f"{sym}_rest15m"]
    rows.append({"check": f"{sym} 15m vs {fresh['table']}", "overlap_bars": fresh["overlap_bars"],
                 "ohlc_disagree": fresh["ohlc_disagree"], "agree_share": round(fresh["ohlc_agree_share"], 6),
                 "frozen_ohlc_disagree": frozen["ohlc_disagree"], "volume_diff_gt_1pct": fresh["volume_rel_diff_gt_1pct"]})
fresh1, frozen1 = orb_checks.archive_vs_rest_1m(), GATE["BTCUSDT_rest1m"]
rows.append({"check": "BTCUSDT 1m vs screener_klines_1m", "overlap_bars": fresh1["overlap_bars"], "ohlc_disagree": fresh1["ohlc_disagree"],
             "agree_share": 1 - fresh1["ohlc_disagree"] / fresh1["overlap_bars"], "frozen_ohlc_disagree": frozen1["ohlc_disagree"],
             "volume_diff_gt_1pct": fresh1["volume_rel_diff_gt_1pct"]})
display(pd.DataFrame(rows))
print("Largest 15m disagreements (BTC) cluster on the outages:")
pd.DataFrame(GATE["BTCUSDT_rest15m"]["disagreements"][:10])
'''),
    ("md", r'''
## 4. Bars against trades (Amendment A1)

On three development days fixed by date rule (the 15th of June 2020, 2021 and 2022, 13:00–21:30 UTC) the
1-minute bars were rebuilt from Binance `aggTrades`. The first run failed the clause as originally written,
"exact agreement": Binance sets some bar opens to the **previous bar's close** instead of the first trade (every
mismatched open equals the previous close, one tick from a trade printed 5–105 ms after the boundary), and a
few boundary trades land in the adjacent minute. The archive matches the REST API exactly, so this is how the
exchange builds bars. The clause was amended **before any outcome** (PREREGISTRATION.md section 2). The frozen
results are shown; set `RECHECK = True` to download the three days again (about 130 MB).
'''),
    ("code", r'''
RECHECK = False
agg = [orb_checks.trades_vs_bars(d) for d in orb_checks.AGG_DAYS] if RECHECK else GATE["BTCUSDT_aggtrades"]
pd.DataFrame([{k: v for k, v in a.items() if k != "source"} for a in agg])
'''),
    ("md", r'''
## 5. Funding and tick size

Funding settles every 8 hours on both contracts throughout 2020–2026, with no missing settlement. The chart is
context for the exit arms that hold overnight (longs pay a positive rate).
'''),
    ("code", r'''
display(pd.DataFrame({s: GATE[f"{s}_funding"] for s in orb_data.SYMBOLS}))
fig, ax = plt.subplots(figsize=(9, 3.2))
for i, sym in enumerate(orb_data.SYMBOLS):
    f = orb_data.load_funding(sym)
    s = pd.Series(f["rate"] * 1e4, index=pd.to_datetime(f["time_ms"], unit="ms")).rolling(90).mean()
    ax.plot(s.index, s.values, color=SERIES[i], label=sym)
    ax.annotate(sym, xy=(s.index[-1], s.values[-1]), xytext=(4, 0), textcoords="offset points", fontsize=8, color=INK2, va="center")
style(ax); zero_line(ax)
ax.set_title("Funding rate, 30-day mean (bp per 8 hours)"); ax.set_ylabel("bp per settlement"); ax.legend(loc="upper left")
plt.tight_layout(); plt.show()
'''),
    ("code", r'''
p = orb_data.load_panel("BTCUSDT")
months = pd.to_datetime(p["t0_ms"] + np.arange(len(p["close"])) * 60_000, unit="ms").strftime("%Y-%m")
on_grid = np.abs(np.round(p["close"] * 10) - p["close"] * 10) < 1e-6
share = pd.Series(on_grid).groupby(months).mean()
print("BTCUSDT share of 1m closes on a 0.1 grid (tick 0.01 before, 0.10 from 2022-02-15):")
share.loc["2021-11":"2022-05"].round(3).to_frame("share_on_0.1_grid").T
'''),
    ("md", r'''
## 6. Calendars and eligible sessions

NY sessions are full-length NYSE days at 09:30 America/New_York; London sessions full-length LSE days at 08:00
Europe/London; the UTC anchor is every calendar day. `tests/test_calendars.py` checks the frozen calendars against
an independently compiled NYSE holiday and early-close list and the DST behaviour of the anchors.
'''),
    ("code", r'''
r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_calendars.py", "-q", "-p", "no:cacheprovider"],
                   capture_output=True, text=True, cwd=STUDY)
print(r.stdout.strip().splitlines()[-1])
ny = cal.sessions("NY", "2024-03-04", "2024-04-05")
ny["t0_utc"] = pd.to_datetime(ny.t0_ms, unit="ms").dt.strftime("%H:%M")
print("Around the 2024 DST changes (US 03-10, UK 03-31): the NY anchor moves in UTC; the gap to London is -4h between them")
ny[["date", "t0_utc", "ny_minus_ldn_hours"]].iloc[[3, 4, 5, 18, 19, 20]]
'''),
    ("code", r'''
for key, table in GATE["eligibility"].items():
    if key.startswith("BTCUSDT"):
        print(key)
        display(pd.DataFrame(table).set_index("year"))
'''),
    ("md", r'''
## Gate result

Every clause passed before the first outcome was computed. "ok" sessions have a complete 15-minute opening
range; `outage_in_horizon` counts sessions with tradeless minutes somewhere between the anchor and the session
exit, which the engine handles with its gap rules (and flags).
'''),
    ("code", r'''
pd.Series(GATE["gate"], name="passed").to_frame()
'''),
]

# ---------------------------------------------------------------------------------------------
NB02 = [
    ("md", r'''
# 02 · Reference engine

**ORB study, notebook 2 of 5.** Question: does the engine do exactly what the pre-registration says, bar by bar,
without looking ahead?

The rules are in [PREREGISTRATION.md](PREREGISTRATION.md) section 3 and the docstring of `orb_engine.py`. In one
paragraph: the opening range is the high H and low L of the first *m* one-minute bars after the anchor. A close
entry triggers on the first completed bar closing beyond H or L and fills at the next bar's open; a resting stop
entry fills at the level or a worse open. The protective stop is live from the fill; a bar opening beyond it exits
at that open, otherwise a touch exits at the stop. A bar that could order two events either way is booked the
pessimistic way and flagged. Time exits happen at the first tradable open at or after the exit minute.
'''),
    ("code", SETUP),
    ("md", r'''
## 1. Synthetic fixtures

Each test builds a flat market and changes only the bars that matter: breakouts, a close exactly on the boundary,
the deadline edge, latency, gaps through stops, stops in the entry minute, tick rounding, targets that touch but do
not trade through, both boundaries in one bar, missing and outage bars, funding at the boundaries, every exit
event, multi-day holds, and a truncation test.
'''),
    ("code", r'''
r = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "-rs"], capture_output=True, text=True, cwd=STUDY)
print(r.stdout[-1500:])
'''),
    ("md", r'''
## 2. Two independent implementations agree on real data

`orb_signals.py` recomputes ranges, triggers, fills and fixed-stop exits with array windows instead of a state
machine. On every BTC development session, for the 42 policies it covers, every trade field (dates, trigger,
side, fill index and price, stop, exit index and price, exit reason, ambiguity flags, gross bp) must be identical.
The randomized-control walker is checked against the reference walk, and the engine is re-run on a market whose
minutes after 2021-07-01 were deleted: every decision completed before the cut must be unchanged. Recomputed here
(about 30 seconds) and compared with the frozen record.
'''),
    ("code", r'''
import orb_parity
mkt = orb_checks.load_market("BTCUSDT")
fresh = {"parity": orb_parity.parity(mkt), "random_walker": orb_parity.random_walker(mkt), "truncation": orb_parity.truncation(mkt)}
frozen = json.loads((RESULTS / "engine_checks.json").read_text())
print("identical to frozen engine_checks.json:", {k: fresh[k] == frozen[k] for k in fresh})
par = pd.DataFrame(fresh["parity"]).T[["ref_trades", "vec_trades", "match"]]
print(f"parity: {int(par.match.sum())}/{len(par)} policies identical on every field")
display(par.T)
print("randomized-control walker:", fresh["random_walker"])
pd.DataFrame({k: v for k, v in fresh["truncation"].items() if isinstance(v, dict)}).T
'''),
    ("md", r'''
## 3. Three sessions by hand

The first three P0 trades of the development block, drawn from the minute panel. Check them against the ledger
rows printed under each chart: the shaded band is the 09:30–09:45 range, the trigger is the first close outside
it, the fill is the next bar's open, and the position ends at the opposite boundary or at 16:00.
'''),
    ("code", r'''
led = pd.read_csv(RESULTS / "run_v1/development/BTCUSDT/P0.csv.gz")
sample = led[led.status == "trade"].head(3)
fig, axes = plt.subplots(1, 3, figsize=(13, 3.6), sharey=False)
for ax, (_, t) in zip(axes, sample.iterrows()):
    i0 = mkt.index(int(t.t0_ms)); end = int(t.exit_idx) + 20
    x = (np.arange(i0, end) - i0)
    ax.plot(x, mkt.close[i0:end], color=SERIES[0], linewidth=1.1, label="1m close")
    ax.axvspan(0, 15, color=NEUTRAL, alpha=0.25, linewidth=0)
    ax.axhline(t.H, color=AXIS, linewidth=1); ax.axhline(t.L, color=AXIS, linewidth=1)
    k, f, e = int(t.trigger_idx) - i0, int(t.fill_idx) - i0, int(t.exit_idx) - i0
    ax.scatter([k], [mkt.close[int(t.trigger_idx)]], s=36, color=SERIES[3], zorder=3, label="trigger close")
    ax.scatter([f], [t.fill_px], s=36, color=SERIES[2], zorder=3, label="fill (next open)")
    ax.scatter([e], [t.exit_px], s=36, color=NEGATIVE if t.gross_bp < 0 else SERIES[5], zorder=3, label=f"exit ({t.exit_reason})")
    ax.axhline(t.stop_px, color=NEGATIVE, linewidth=0.9, alpha=0.6)
    style(ax); ax.set_title(f"{t.date}  {'long' if t.side > 0 else 'short'}", fontsize=10)
    ax.set_xlabel("minutes after 09:30 New York")
handles, names = axes[0].get_legend_handles_labels()
fig.legend(handles, [n.split(" (")[0] if n.startswith("exit") else n for n in names], ncol=4, loc="lower center", fontsize=8)
plt.tight_layout(rect=(0, 0.08, 1, 1)); plt.show()
cols = ["date", "H", "L", "trigger_idx", "side", "fill_utc", "fill_px", "stop_px", "exit_utc", "exit_px", "exit_reason", "gross_bp", "risk_bp"]
sample[cols]
'''),
    ("code", r'''
t = sample.iloc[0]
k = int(t.trigger_idx)
bars = pd.DataFrame({"utc": pd.to_datetime([mkt.time_ms(i) for i in range(k - 1, k + 3)], unit="ms"),
                     "open": mkt.open[k - 1:k + 3], "high": mkt.high[k - 1:k + 3], "low": mkt.low[k - 1:k + 3], "close": mkt.close[k - 1:k + 3]})
print(f"{t.date}: H={t.H}, L={t.L}. The trigger bar is the first to CLOSE above H; the fill is the next bar's OPEN ({t.fill_px}).")
bars
'''),
    ("md", r'''
## 4. No built-in bias

Two end-to-end runs of every stage on a synthetic random walk (`tests/test_run_smoke.py`, opt-in because each
takes about 80 seconds) confirmed that the pipeline completes, never overwrites a freeze, and handles a
challenger and an opened lockbox. On the random walk P0's gross expectancy was +2.7 bp with a standard error of
2.4 bp, and the clock-only controls are shown below: a strategy engine without an edge in its data shows none.
'''),
    ("code", r'''
mech = json.loads((RESULTS / "run_v1/exploratory/mechanism.json").read_text())
pd.DataFrame(mech["random_walk_engine_check"]).T
'''),
]

REPRO = r'''
def reproduce(symbol, block, pid):
    # Recompute one frozen ledger from the panel and compare it with the saved file.
    import orb_run
    a, b = orb_run.BLOCKS[block]
    mkt = orb_checks.load_market(symbol)
    p = pol.BY_ID[pid]
    fresh = eng.run_policy(mkt, cal.sessions(p.anchor, "2020-01-01", b, shift_min=p.shift_min), p, a, b)
    if "exit_reason" in fresh:
        fresh.loc[(fresh["status"] == "trade") & (fresh["exit_reason"] == "censored_block_end"), "status"] = "censored"
    saved = pd.read_csv(RESULTS / f"run_v1/{block}/{symbol}/{pid}.csv.gz")
    num = ["t0_ms", "H", "L", "trigger_idx", "side", "fill_idx", "fill_px", "stop_px", "exit_idx", "exit_px", "gross_bp", "funding_bp"]
    same = len(fresh) == len(saved) and all(np.allclose(fresh[c].astype(float), saved[c].astype(float), equal_nan=True, rtol=0, atol=1e-9) for c in num)
    return bool(same and fresh["status"].tolist() == saved["status"].tolist())
'''

# ---------------------------------------------------------------------------------------------
NB03 = [
    ("md", r'''
# 03 · Development: baseline, controls, variants and selection

**ORB study, notebook 3 of 5. BTC, 2020-01-01 → 2022-12-31.** Questions: does P0 have an edge before and after
costs; does the anchor, the entry or the exit matter; what does the breakout add over opening momentum and random
entries; and which one challenger, if any, does the frozen rule carry into validation?

Frozen inputs: `results/freeze_F0.json` (rules, code, data) and `results/freeze_F1.json` (the selection, written
before any validation outcome). Costs: `db` = 11.6 bp per round trip plus actual funding; `gross` = price only.
'''),
    ("code", SETUP + REPRO + r'''
import orb_run
orb_run.verify_f0()
F0, F1 = (json.loads((RESULTS / f"freeze_{n}.json").read_text()) for n in ("F0", "F1"))
print("F0", F0["created_utc"], "| F1", F1["created_utc"], "| candidates:", F1["candidates"], "|", F1["selection_reason"])
print("frozen files unchanged; ledgers reproduce from the panel:",
      {pid: reproduce("BTCUSDT", "development", pid) for pid in F1["candidates"]})
REP = json.loads((RESULTS / "run_v1/development/development_report.json").read_text())
S = pd.read_csv(RESULTS / "run_v1/development/summary_BTCUSDT.csv")
'''),
    ("md", r'''
## 1. P0 and the whole family, before and after costs

Gross is the price move in bp of entry notional; `net_bp` subtracts 5.8 bp per leg and adds realised funding. The
95% interval and p-value come from a 20-day circular block bootstrap of per-trade expectancy (10,000 draws, seed 42).
'''),
    ("code", r'''
g = S[S.scenario == "gross"].set_index("policy")[["trades", "mean_bp", "win_rate"]].rename(columns={"mean_bp": "gross_bp", "win_rate": "gross_win"})
d = S[S.scenario == "db"].set_index("policy")[["mean_bp", "win_rate", "profit_factor", "sharpe_daily_ann", "max_drawdown"]].rename(columns={"mean_bp": "net_bp"})
t = g.join(d)
t["ci95_lo"] = pd.Series({k: v["mean_bp_ci95"][0] for k, v in REP["inference"].items()})
t["ci95_hi"] = pd.Series({k: v["mean_bp_ci95"][2] for k, v in REP["inference"].items()})
t["p_gt0"] = pd.Series({k: v["p_mean_bp_gt0"] for k, v in REP["inference"].items()})
t["break_even_rt_bp"] = pd.Series(REP["break_even_leg_bp"]) * 2
t.round(2)
'''),
    ("md", r'''
## 2. The anchor matters more than the rule

Gross expectancy for every anchor and range length (close entries solid, resting-stop entries dotted). The grey
line is the decision-bearing round trip of 11.6 bp: a policy has to clear it just to break even.
'''),
    ("code", r'''
gross = S[S.scenario == "gross"].set_index("policy")["mean_bp"]
fig, ax = plt.subplots(figsize=(8.5, 3.6))
for i, anchor in enumerate(("NY", "LDN", "UTC")):
    for entry, ls in (("CLOSE", "-"), ("STOP", ":")):
        ids = ["P0" if (anchor, m, entry) == ("NY", 15, "CLOSE") else f"CORE_{anchor}_R{m}_{entry}" for m in (5, 15, 30, 60)]
        ax.plot([5, 15, 30, 60], [gross[k] for k in ids], ls, marker="o", color=SERIES[i], label=f"{anchor} {entry.lower()}")
    ax.annotate(anchor, xy=(60, gross[f"CORE_{anchor}_R60_CLOSE"]), xytext=(6, 0), textcoords="offset points", fontsize=8, color=INK2, va="center")
style(ax); zero_line(ax); zero_line(ax, 11.6, "round-trip cost 11.6 bp", color=NEUTRAL)
ax.set_xticks([5, 15, 30, 60]); ax.set_xlabel("opening range, minutes"); ax.set_ylabel("gross bp per trade")
ax.set_title("Development gross expectancy by anchor (BTC 2020-2022)")
ax.legend(ncol=6, loc="upper center", bbox_to_anchor=(0.5, -0.2), fontsize=7)
plt.tight_layout(); plt.show()
'''),
    ("md", r'''
## 3. Controls: what does the breakout itself add?

- **Opening momentum** enters at 09:45 in the range candle's direction without waiting for a break.
- **Clock long / short** enter at 09:45 in a fixed direction with the same stop rule.
- **Placebo** shifts the whole rule by ±1–2 hours on the same dates.
- **Random direction** keeps P0's entries and stop distance but flips a seeded coin for the side (200 seeds);
  **random time** also draws the entry minute (200 seeds).
'''),
    ("code", r'''
ctl_ids = ["P0", "CTL_MOMENTUM", "CTL_CLOCK_LONG", "CTL_CLOCK_SHORT", "CTL_FADE", "CTL_PLACEBO_M120", "CTL_PLACEBO_M60", "CTL_PLACEBO_P60", "CTL_PLACEBO_P120"]
display(t.loc[ctl_ids, ["trades", "gross_bp", "net_bp", "gross_win", "sharpe_daily_ann"]].round(2))
display(pd.DataFrame(REP["random_controls"]).T)
print("paired daily difference vs opening momentum (db):")
display(pd.DataFrame(REP["paired_vs_momentum"]).T)
rd, rt = pd.read_csv(RESULTS / "run_v1/development/random_direction.csv"), pd.read_csv(RESULTS / "run_v1/development/random_time.csv")
fig, axes = plt.subplots(1, 2, figsize=(10, 3.2), sharey=True)
for ax, df, name in zip(axes, (rd, rt), ("random direction", "random time and direction")):
    ax.hist(df.mean_bp_gross, bins=30, color=SERIES[0], alpha=0.85, edgecolor="white", linewidth=0.6)
    ax.axvline(t.loc["P0", "gross_bp"], color=SERIES[1], linewidth=2)
    ax.annotate("P0", xy=(t.loc["P0", "gross_bp"], ax.get_ylim()[1] * 0.9), xytext=(4, 0), textcoords="offset points", fontsize=8, color=INK2)
    style(ax); ax.set_title(f"{name}: 200 seeds", fontsize=10); ax.set_xlabel("mean gross bp per trade")
plt.tight_layout(); plt.show()
'''),
    ("md", r'''
## 4. Exits: in development every earlier exit made it worse

The exit-event addendum and the draft's target and 60-minute arms, each changing only P0's exit. Gross in blue,
after costs in orange. Only removing the 16:00 exit raised the gross in development; notebook 05 follows every arm
through 2026 on both assets.
'''),
    ("code", r'''
exit_ids = ["P0", "X_NOTIME", "EXT_TGT3R", "EXT_TGT2R", "EXT_TGT1R", "X_VWAP", "X_REENTER15M", "X_REENTER1M", "X_TRAIL", "X_TRAIL_NOTIME", "EXT_TX60", "EXT_STOPMID"]
labels = {"P0": "P0: opposite stop, 16:00 exit", "X_NOTIME": "no time exit (hold to stop, 7-day cap)", "EXT_TGT3R": "3R target",
          "EXT_TGT2R": "2R target", "EXT_TGT1R": "1R target", "X_VWAP": "exit on VWAP cross", "X_REENTER15M": "exit on 15m close back in range",
          "X_REENTER1M": "exit on 1m close back in range", "X_TRAIL": "trailing stop, one range width", "X_TRAIL_NOTIME": "trailing stop, no time exit",
          "EXT_TX60": "time exit 60 min after entry", "EXT_STOPMID": "stop at range midpoint"}
e = t.loc[exit_ids, ["trades", "gross_bp", "net_bp", "gross_win", "max_drawdown"]]
display(e.rename(index=labels).round(2))
fig, ax = plt.subplots(figsize=(8.5, 4.2))
yy = np.arange(len(exit_ids))[::-1]
ax.hlines(yy, 0, e.gross_bp, color=AXIS, linewidth=1)
ax.scatter(e.gross_bp, yy, color=SERIES[0], s=40, zorder=3, label="gross")
ax.scatter(e.net_bp, yy, color=SERIES[1], s=40, zorder=3, label="after costs (db)")
ax.set_yticks(yy); ax.set_yticklabels([labels[k] for k in exit_ids], fontsize=8)
style(ax, grid_axis="x"); ax.axvline(0, color=AXIS, linewidth=1)
ax.set_xlabel("bp per trade"); ax.set_title("Exit events on P0's entries (BTC 2020-2022)"); ax.legend(loc="lower right")
plt.tight_layout(); plt.show()
'''),
    ("md", r'''
## 5. Selection by the frozen rule

Eligible: at least 100 trades, positive net expectancy, positive in at least two of the three years. The highest net
daily Sharpe wins; ties within 0.02 go to fewer changed components. CORE_NY_R60_STOP had the highest Sharpe (0.565);
EXT_WIDTH (0.557) was within 0.02 with one changed component instead of two, so EXT_WIDTH is the challenger. Both
beat P0's 0.061.
'''),
    ("code", r'''
sel = pd.read_csv(RESULTS / "run_v1/development/selection_table.csv").sort_values("sharpe", ascending=False)
display(sel.head(10).round(3))
yearly = pd.read_csv(RESULTS / "run_v1/development/yearly_db_BTCUSDT.csv", index_col=0)
yearly.loc[["P0", "EXT_WIDTH", "CORE_NY_R60_STOP", "X_NOTIME"]].round(2)
'''),
    ("md", r'''
## 6. How much can 46 trials and three years say?

Romano–Wolf StepM over all 46 family return series rejects nothing (best t = 0.98 against a critical value of 2.61).
The deflated Sharpe ratio of the challenger is 0.004 at 46 trials. The minimum detectable effect at 80% power is about
17 bp per trade for the two-year validation block, eight times the +2 bp hurdle: validation can reject large effects
but cannot confirm small ones.
'''),
    ("code", r'''
print("StepM rejected:", REP["stepm"]["rejected"], "| final critical value", round(REP["stepm"]["final_critical_value"], 3))
display(pd.Series(REP["stepm"]["t_stats"]).sort_values(ascending=False).head(8).round(2).to_frame("t"))
display(pd.DataFrame({c: {n: (round(v["dsr"], 4) if v else None) for n, v in dd.items()} for c, dd in REP["dsr"].items()}).rename_axis("trials"))
pd.Series(REP["mde"]).round(2).to_frame("minimum detectable effect")
'''),
]

# ---------------------------------------------------------------------------------------------
NB04 = [
    ("md", r'''
# 04 · Validation and verdict

**ORB study, notebook 4 of 5.** Question: do the frozen candidates (P0 and EXT_WIDTH) hold up on 2023-01-01 →
2024-12-31, which the selection never saw? BTC decides; ETH is the transfer test.

Continuation rule (PREREGISTRATION.md section 8), for each BTC candidate: net expectancy and mean daily return both
positive, at least three of four half-years positive, and non-negative expectancy with +5 bp per round trip. If no
candidate passes, the lockbox (2025-01-01 → 2026-09-13) stays closed and the verdict is taken from validation.
'''),
    ("code", SETUP + REPRO + r'''
import orb_run
orb_run.verify_f0()
F2, V = (json.loads((RESULTS / f"freeze_{n}.json").read_text()) for n in ("F2", "verdict"))
print("F2", F2["created_utc"], "| lockbox:", F2["lockbox"], "| verdict freeze", V["created_utc"])
print("validation ledgers reproduce from the panel:",
      {f"{s} {c}": reproduce(s, "validation", c) for s in ("BTCUSDT", "ETHUSDT") for c in F2["candidates"]})
VR = json.loads((RESULTS / "run_v1/validation/validation_report.json").read_text())
'''),
    ("code", r'''
rows = []
for sym, per in VR["assets"].items():
    for c, v in per.items():
        i = v["inference"]
        rows.append({"asset": sym, "candidate": c, "trades": v["trades"], "net_bp": i["mean_bp"], "ci95_lo": i["mean_bp_ci95"][0],
                     "ci95_hi": i["mean_bp_ci95"][2], "p_gt0": i["p_mean_bp_gt0"], "daily_mean_bp": i["daily_mean"] * 1e4,
                     "plus5_bp": v["db_plus5_mean_bp"], **{f"half {k}": x for k, x in v["halfyears"].items()}, "passes_rule": v["passes"]})
pd.DataFrame(rows).round(2)
'''),
    ("md", r'''
## Half-year net expectancy, development into validation

The same frozen rules, one point per half-year, net of 11.6 bp costs and funding. The development block's positive
result rests on 2022; in validation every half-year is negative for P0.
'''),
    ("code", r'''
dev_h = pd.read_csv(RESULTS / "run_v1/development/halfyear_db_BTCUSDT.csv", index_col=0)
val = VR["assets"]["BTCUSDT"]
fig, ax = plt.subplots(figsize=(9, 3.4))
for i, c in enumerate(F2["candidates"]):
    s = pd.concat([dev_h.loc[c].dropna(), pd.Series(val[c]["halfyears"])])
    ax.plot(range(len(s)), s.values, marker="o", color=SERIES[i], label=c)
    ax.annotate(c, xy=(len(s) - 1, s.values[-1]), xytext=(6, 0), textcoords="offset points", fontsize=8, color=INK2, va="center")
ax.axvspan(5.5, len(s) - 0.5, color=NEUTRAL, alpha=0.18, linewidth=0)
ax.text(5.65, ax.get_ylim()[1], "validation", fontsize=8, color=INK2, va="top")
ax.set_xticks(range(len(s))); ax.set_xticklabels(s.index, fontsize=8)
style(ax); zero_line(ax); ax.set_ylabel("net bp per trade"); ax.set_title("BTC net expectancy by half-year"); ax.legend(loc="lower left")
plt.tight_layout(); plt.show()
'''),
    ("md", r'''
## Verdict (frozen)

Neither candidate passed a single performance clause, so the lockbox was **not opened**. The registered verdict for
both is **INCONCLUSIVE + PRICE_SIGNAL_ONLY**. "Inconclusive" only because the upper end of the 95% interval (P0
+2.8 bp, EXT_WIDTH +4.6 bp) still reaches the +2 bp hurdle, not because the evidence leans positive: the point
estimates are −6.8 and −10.8 bp per trade, and ETH is no better (−7.2 and −8.2). Under the pre-registration a
validation failure ends this confirmatory campaign; a revised ORB idea needs a new ID and data not yet seen.
'''),
    ("code", r'''
pd.DataFrame({c: {"verdict": " + ".join(v)} for c, v in V["verdicts"].items()}).T
'''),
]

# ---------------------------------------------------------------------------------------------
NB05 = [
    ("md", r'''
# 05 · Exploratory: exit events, mechanism and decay (post-verdict)

**ORB study, notebook 5 of 5. Exploratory, run after the verdict was frozen; nothing here changes it.** All 46
policies and the controls were run on every block for both assets (PREREGISTRATION.md section 10). Any pattern
picked out of these tables is a hypothesis for a new pre-registration with data not yet seen, not a finding: with
54 rules × 2 assets × 3 blocks, some cells will look good by chance.
'''),
    ("code", SETUP + r'''
X = RESULTS / "run_v1/exploratory"
A = pd.read_csv(X / "all_policies.csv")
Y = pd.read_csv(X / "by_year.csv")
MECH = json.loads((X / "mechanism.json").read_text())
DIAG = json.loads((X / "diagnostics.json").read_text())
EXC = pd.read_csv(X / "excursions_BTCUSDT_P0.csv")
BLOCKS = ["development", "validation", "lockbox"]
ORDER = [p.id for p in (*pol.FAMILY, *pol.CONTROLS)]

def pivot(sym, scenario="gross", value="mean_bp"):
    tab = A[(A.symbol == sym) & (A.scenario == scenario)].pivot_table(index="policy", columns="block", values=value)[BLOCKS]
    return tab.loc[ORDER]
'''),
    ("md", r'''
## 1. By year: BTC never cleared its costs outside 2022

P0's gross expectancy per calendar year. The grey line is the 11.6 bp round trip. Outside 2022, BTC's gross stayed
between −1.5 and +8.8 bp in every year and was lower after the US spot-ETF launch; ETH cleared the cost line in
2022, 2025 and 2026.
'''),
    ("code", r'''
fig, ax = plt.subplots(figsize=(8, 3.3))
for i, sym in enumerate(("BTCUSDT", "ETHUSDT")):
    s = Y[(Y.symbol == sym) & (Y.policy == "P0") & (Y.scenario == "gross")].set_index("year")["mean_bp"]
    ax.plot(s.index.astype(str), s.values, marker="o", color=SERIES[i], label=sym)
    ax.annotate(sym, xy=(len(s) - 1, s.values[-1]), xytext=(6, 0), textcoords="offset points", fontsize=8, color=INK2, va="center")
style(ax); zero_line(ax); zero_line(ax, 11.6, "round-trip cost", color=NEUTRAL)
ax.set_ylabel("gross bp per trade"); ax.set_title("P0 gross expectancy by year (2026 to 13 September)"); ax.legend(loc="upper left")
plt.tight_layout(); plt.show()
print("BTC P0 before / after the US spot-ETF launch:", {k: DIAG["BTCUSDT_P0"][k] for k in ("pre_etf", "post_etf")})
'''),
    ("md", r'''
## 2. Mechanism: most of the gross is not the breakout's direction

For each block: P0's gross, the median of 200 random-direction versions of the same trades (same entry, same stop
distance, coin-flip side) and the median of 200 random-time versions. On a random walk all three are about zero
(engine check at the end of this section). On real BTC and ETH the random versions earn a positive gross in every
block, so much of what the rule earns comes from its shape: a stop at the range edge held into the US session
profits from intraday momentum whichever way the position faces. The breakout's own contribution is the gap between
P0 and the random-direction median: positive in 2020–22, negative in 2023–24, mixed since.
'''),
    ("code", r'''
rows = []
for sym in ("BTCUSDT", "ETHUSDT"):
    for b in BLOCKS:
        m = MECH[f"{sym}_{b}"]
        rows.append({"asset": sym, "block": b, "P0": m["p0_gross_bp"], "random direction (median)": m["random_direction_gross_median_bp"],
                     "random time (median)": m["random_time_gross_median_bp"], "share of direction seeds >= P0": m["random_direction_share_ge_p0"]})
M = pd.DataFrame(rows)
display(M.round(2))
fig, axes = plt.subplots(1, 2, figsize=(10, 3.3), sharey=True)
for ax, sym in zip(axes, ("BTCUSDT", "ETHUSDT")):
    m = M[M.asset == sym]
    for i, col in enumerate(("P0", "random direction (median)", "random time (median)")):
        ax.plot(m.block, m[col], marker="o", color=SERIES[i], label=col)
    style(ax); zero_line(ax); ax.set_title(sym, fontsize=10)
axes[0].set_ylabel("gross bp per trade"); axes[0].legend(loc="upper right", fontsize=7)
plt.tight_layout(); plt.show()
print("random-walk engine check (gross bp, synthetic market):")
pd.DataFrame(MECH["random_walk_engine_check"]).T.round(2)
'''),
    ("md", r'''
## 3. Anchors across all blocks

Mean gross over the four range lengths and both entries, per anchor, and the placebo shifts of P0. The NY anchor had
the highest mean gross in five of six asset-blocks (the exception is BTC 2025–26, where every anchor was within a few
bp of zero); midnight UTC was negative in every block after 2022; and the placebo shifted two hours earlier (07:30
New York) was the lowest or second-lowest of the five shifts in every block.
'''),
    ("code", r'''
out = {}
for sym in ("BTCUSDT", "ETHUSDT"):
    g = pivot(sym)
    for anchor in ("NY", "LDN", "UTC"):
        ids = [i for i in g.index if i.startswith(f"CORE_{anchor}_")] + (["P0"] if anchor == "NY" else [])
        out[(sym, f"{anchor} (mean of 8 policies)")] = g.loc[ids].mean()
    for pid in ("CTL_PLACEBO_M120", "CTL_PLACEBO_M60", "P0", "CTL_PLACEBO_P60", "CTL_PLACEBO_P120"):
        out[(sym, pid)] = g.loc[pid]
pd.DataFrame(out).T.round(1)
'''),
    ("md", r'''
## 4. Exit events across every block

Each arm changes only P0's exit. Across the six asset-blocks (BTC and ETH, three periods each): fixed 1R/2R/3R
targets, the 60-minute exit and the trailing stop had a lower gross than P0 in all six; the VWAP-cross exit in five;
the close-back-inside-the-range exits (1-minute and 15-minute) and the midpoint stop in four, beating P0 only in the
two blocks where P0's own gross had almost vanished (BTC 2025–26, ETH 2023–24). None of these earlier exits was net
positive after costs in more than one block. Removing the 16:00 exit raised the gross in four of six (all three BTC
blocks and ETH 2025–26), at the price of a win rate near 10% and a large drawdown.
'''),
    ("code", r'''
arms = ["X_NOTIME", "EXT_TGT3R", "EXT_TGT2R", "EXT_TGT1R", "X_VWAP", "X_REENTER15M", "X_REENTER1M", "X_TRAIL", "X_TRAIL_NOTIME", "EXT_TX60", "EXT_STOPMID"]
G = A[A.scenario == "gross"].pivot_table(index=["symbol", "block"], columns="policy", values="mean_bp")
N = A[A.scenario == "db"].pivot_table(index=["symbol", "block"], columns="policy", values="mean_bp")
pd.DataFrame([{"exit arm": k, "blocks with gross above P0 (of 6)": int((G[k] > G["P0"]).sum()),
               "blocks net positive (of 6)": int((N[k] > 0).sum()), "mean gross minus P0, bp": (G[k] - G["P0"]).mean()} for k in arms]).round(2)
'''),
    ("code", r'''
exit_ids = ["P0", "X_NOTIME", "EXT_TGT3R", "EXT_TGT2R", "EXT_TGT1R", "X_VWAP", "X_REENTER15M", "X_REENTER1M", "X_TRAIL", "X_TRAIL_NOTIME", "EXT_TX60", "EXT_STOPMID"]
for sym in ("BTCUSDT", "ETHUSDT"):
    tab = pivot(sym).loc[exit_ids].add_suffix(" gross").join(pivot(sym, "db").loc[exit_ids].add_suffix(" net"))
    tab = tab.join(pivot(sym, "db", "win_rate").loc[exit_ids].add_suffix(" win")).join(pivot(sym, "db", "max_drawdown").loc[exit_ids].add_suffix(" maxDD"))
    print(sym); display(tab.round(2))
g = pivot("BTCUSDT").loc[exit_ids]
fig, axes = plt.subplots(1, 3, figsize=(12, 3.8), sharey=True)
yy = np.arange(len(exit_ids))[::-1]
for ax, b in zip(axes, BLOCKS):
    ax.hlines(yy, 0, g[b], color=AXIS, linewidth=1)
    ax.scatter(g[b], yy, color=[SERIES[1] if k == "P0" else SERIES[0] for k in exit_ids], s=36, zorder=3)
    ax.axvline(g.loc["P0", b], color=SERIES[1], linewidth=1, alpha=0.6)
    style(ax, grid_axis="x"); ax.axvline(0, color=AXIS, linewidth=1); ax.set_title(f"BTC {b}", fontsize=10); ax.set_xlabel("gross bp per trade")
axes[0].set_yticks(yy); axes[0].set_yticklabels(exit_ids, fontsize=8)
plt.tight_layout(); plt.show()
'''),
    ("md", r'''
## 5. What the stop and the 16:00 exit do to P0's trades (BTC, 2020–2026)

Stopped trades are decided early: their best moment comes a median 8 minutes after entry, and a quarter of them were
at least 1R in profit before the stop. Trades still open at 16:00 peaked a median three hours after entry and had given
back a median 80 bp by the exit. After the 16:00 exit, the next four hours show no consistent follow-through.
'''),
    ("code", r'''
EXC["block"] = np.where(EXC.date < "2023-01-01", "development", np.where(EXC.date < "2025-01-01", "validation", "lockbox"))
EXC["mfe_R"] = EXC.mfe_bp / EXC.risk_bp
display(EXC.groupby("exit_reason").agg(trades=("gross_bp", "size"), mean_gross_bp=("gross_bp", "mean"), median_mfe_bp=("mfe_bp", "median"),
                                       median_minutes_to_best=("minutes_to_mfe", "median"), median_gave_back_bp=("gave_back_bp", "median"),
                                       share_reaching_1R=("mfe_R", lambda r: (r >= 1).mean())).round(2))
EXC[EXC.exit_reason == "time"].groupby("block")[["move_after_4h_bp", "move_after_12h_bp", "move_after_24h_bp"]].agg(["mean", "median"]).round(1)
'''),
    ("md", r'''
## 6. Diagnostics for P0, the challenger and the no-time-exit arm

Long and short, before and after the US spot-ETF launch (2024-01-11), range-width and relative-volume terciles, the
share of all gross carried by the best 20 trades, the break-even round trip, and the relationship to simply holding
the perpetual.
'''),
    ("code", r'''
rows = []
for key, dd in DIAG.items():
    if "long" not in dd:
        continue
    rows.append({"series": key, "long_gross": dd["long"]["gross_bp"], "short_gross": dd["short"]["gross_bp"], "pre_etf_gross": dd["pre_etf"]["gross_bp"],
                 "post_etf_gross": dd["post_etf"]["gross_bp"], "narrow_width_gross": dd["width_terciles"]["low"]["gross_bp"],
                 "wide_width_gross": dd["width_terciles"]["high"]["gross_bp"], "low_relvol_gross": dd["relvol_terciles"]["low"]["gross_bp"],
                 "high_relvol_gross": dd["relvol_terciles"]["high"]["gross_bp"], "best20_share_of_gross": dd["best_trade_share_of_gross"]["20"],
                 "break_even_rt_bp": dd["break_even_rt_bp"], "beta_vs_hold": dd["beta_vs_calendar_hold"], "corr_vs_hold": dd["corr_vs_calendar_hold"]})
display(pd.DataFrame(rows).set_index("series").round(2))
print({k: v for k, v in DIAG.items() if "early_close" in k})
'''),
    ("md", r'''
## 7. Hypotheses this leaves (not findings)

- **US-session intraday momentum, not ORB.** The random controls, the anchor comparison and the placebo shifts point
  at the same thing: positions held from the US open into the US afternoon, with a stop at the opening range, earned
  gross returns whichever way they faced. That is a different hypothesis — intraday momentum around the US session —
  and would need its own mechanism statement, pre-registration and data not yet seen.
- **Costs are the wall.** BTC P0's break-even round trip is 7.5 bp, below the 10 bp taker-fee floor. Anything in this
  family needs maker entries or a much larger gross per trade.
- **For breakout payoffs, cut on the structural stop only.** Invalidation exits on the first close back inside the
  range and trailing stops removed the trades that pay; dropping the time exit raised gross but turned the rule into a
  trend-following position with a win rate near 10%. This agrees with the repository's earlier lessons (wider targets
  beat tight ones on the same stop; tight trailing stops hurt). It says nothing directly about mean-reversion trades
  such as squeeze_bull, whose exits are the exit-policy study's job.
'''),
]

NOTEBOOKS = {
    "01_data_and_calendars.ipynb": NB01,
    "02_reference_engine.ipynb": NB02,
    "03_development.ipynb": NB03,
    "04_validation_and_verdict.ipynb": NB04,
    "05_exploratory_exits_and_mechanism.ipynb": NB05,
}

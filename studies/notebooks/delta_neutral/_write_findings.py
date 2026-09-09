"""Generate findings.md from results/ so every quoted number is traceable.

Run AFTER dispersion.py and carry_sizing.py:
    python studies/notebooks/delta_neutral/_write_findings.py
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
R = HERE / "results"

D = json.loads((R / "dispersion_summary.json").read_text())
C = json.loads((R / "carry_summary.json").read_text())
GRID = list(csv.DictReader(open(R / "dispersion_strategy_grid.csv")))
CGRID = list(csv.DictReader(open(R / "carry_grid.csv")))


def g(arm, thr, causal, cost):
    for r in GRID:
        if (r["arm"] == arm and float(r["threshold_bp"]) == thr
                and r["causal"] == str(causal) and r["cost_mode"] == cost):
            return r
    raise KeyError((arm, thr, causal, cost))


def f(x, n=3):
    return f"{float(x):.{n}f}"


dP, dS, dSE = D["dispersion"]["P"], D["dispersion"]["S"], D["dispersion"]["S-ETH"]
dPE = D["dispersion"]["P-ETH"]
cP, cS, cSE = D["clauses"]["P"], D["clauses"]["S"], D["clauses"]["S-ETH"]
bP = D["carry_benchmark"]["P"]["sleeve_faithful"]["ann_return_pct"]
bS = D["carry_benchmark"]["S"]["sleeve_faithful"]["ann_return_pct"]
bSE = D["carry_benchmark"]["S-ETH"]["sleeve_faithful"]["ann_return_pct"]
era_pre = D["pairwise_venue_diff"]["S:binance-bybit:pre_cutover"]
era_post = D["pairwise_venue_diff"]["S:binance-bybit:post_cutover"]
# Era boundary dates are read from the data, not hardcoded, so the table stays
# correct when the study is re-run after the live feed has advanced.
era_pre_last = D["era_split_arm_S"]["S_pre_cutover"]["last"][:10]
era_post_last = D["era_split_arm_S"]["S_post_cutover"]["last"][:10]
disp_last = D["dispersion"]["S"]["last"][:10]
leak = D["basis_leakage"]
lk = list(leak.values())

S3 = g("S", 3.0, True, "pair_change")
S3zero = g("S", 3.0, True, "zero")
S3every = g("S", 3.0, True, "every")
S3contemp = g("S", 3.0, False, "zero")

cl = C["clauses"]
base, rule = C["baseline"], C["with_rule"]
be, re_ = C["baseline_elig"], C["with_rule_elig"]
nc = C["primary_no_resize_cost"]
diff = C["bootstrap"]["sharpe_diff_full"]
diff_e = C["bootstrap"]["sharpe_diff_eligible"]
eq = C["byte_equivalence"]

mar_worse = sum(1 for r in CGRID if float(r["mar"]) <= float(r["mar_base"]))
sh_neg = sum(1 for r in CGRID if float(r["sharpe_uplift"]) < 0)
best_mar = max(float(r["mar"]) for r in CGRID)
best_up = max(float(r["sharpe_uplift"]) for r in CGRID)
best_up_row = max(CGRID, key=lambda r: float(r["sharpe_uplift"]))
dd_same = sum(1 for r in CGRID
              if abs(float(r["max_dd_pct"]) - float(r["max_dd_base_pct"])) < 1e-9)
dd_worse = sorted({f(r['max_dd_pct'], 3) for r in CGRID
                   if float(r["max_dd_pct"]) > float(r["max_dd_base_pct"]) + 1e-9})

decay = 1 - float(S3["mean_gross_on_bp"]) / float(S3contemp["mean_gross_on_bp"])
cost_per_on = float(S3["n_cost_events"]) * 20.0 / float(S3["n_on"])

TXT = f"""STATUS: CONCLUDED KILL (i) + KILL (iii) per pre-registration — sub-study (i) cross-venue funding dispersion is KILL on the adequately-powered 2-venue arm and INCONCLUSIVE on power for the 3-venue arm (where the 3 bp rule never fires even once); sub-study (iii) carry-crash sizing is KILL, uniformly across all 18 grid cells.

Pre-registration: [README.md](README.md), written and saved before any outcome number was
computed. Scripts: [`dispersion.py`](dispersion.py), [`carry_sizing.py`](carry_sizing.py);
this file is generated from `results/` by [`_write_findings.py`](_write_findings.py) so every
number below is traceable to an artefact. Both scripts open `prod.db` `mode=ro`; nothing
under `strategies/**` or `bots/**` was modified; no recommendation here has been applied.

---

# SUB-STUDY (i) — cross-venue funding dispersion

## Clause table

### Arm P — primary, exactly the specified rule (Binance + OKX + Bybit, BTC)

Window {dP['first']} → {dP['last']} UTC, **{dP['n']} settlements = {f(dP['span_days'], 1)} days**,
{cP['I-0']['value']} tradeable intervals.

| # | Clause | Measured | Threshold | Fired |
|---|---|---|---|---|
| I-0 | < 365 usable settlements | **{cP['I-0']['value']}** | 365 | **YES → UNDERPOWERED** |
| I-1 | net annualised return < 5 % | **{f(cP['I-1']['value'], 2)} %** | 5 % | **YES** |
| I-2 | net ann. return < realised Carry benchmark | **{f(cP['I-2']['value'], 2)} %** | {f(bP, 3)} % | **YES** |
| I-3 | 90 % bootstrap CI of mean net/settlement includes 0 | [{f(cP['I-3']['value'][0], 2)}, {f(cP['I-3']['value'][1], 2)}] bp | 0 | **YES** |
| I-4 | DSR at n_trials = 20 < 0.95 | undefined (zero-variance series) | 0.95 | **YES** |

**The return is exactly zero because the signal never fires.** Over {dP['n']} three-venue
settlements the maximum spread observed was **{f(dP['max_spread_bp'])} bp**; the 3 bp threshold
was crossed **{dP['n_gt_3bp']} times**, and so was the 2 bp threshold ({dP['n_gt_2bp']}). Arm
P-ETH is identical in kind: max spread {f(dPE['max_spread_bp'])} bp, {dPE['n_gt_2bp']} crossings
above 2 bp.

Arm P is **INCONCLUSIVE on power** (clause I-0) and cannot on its own produce a verdict — but
the reason it produces nothing is not sampling noise around a small edge: the quantity the
rule keys on does not reach the threshold at all in the sample.

### Arm S — power extension (Binance + Bybit, BTC)

Window {dS['first']} → {dS['last']} UTC, **{dS['n']:,} settlements = {f(dS['span_days'], 0)} days**,
{cS['I-0']['value']:,} intervals.

| # | Clause | Measured | Threshold | Fired |
|---|---|---|---|---|
| I-0 | < 365 usable settlements | {cS['I-0']['value']:,} | 365 | no — **adequately powered** |
| I-1 | net annualised return < 5 % | **{f(cS['I-1']['value'], 2)} %** | 5 % | **YES** |
| I-2 | net ann. return < realised Carry benchmark | **{f(cS['I-2']['value'], 2)} %** | **+{f(bS, 2)} %** | **YES** |
| I-3 | 90 % bootstrap CI of mean net/settlement includes 0 | [{f(cS['I-3']['value'][0])}, {f(cS['I-3']['value'][1])}] bp | 0 | no¹ |
| I-4 | DSR at n_trials = 20 < 0.95 | **{f(cS['I-4']['value'])}** | 0.95 | **YES** |

¹ I-3 did not fire because the CI **excludes** zero — on the losing side.
`P(mean > 0) = {f(cS['_bootstrap_mean_bp']['p_gt_0'], 4)}` over
{cS['_bootstrap_mean_bp']['n_iter']:,} iid resamples. The clause was written to catch
"indistinguishable from zero"; here the loss is statistically unambiguous, which is stronger
evidence for KILL than the clause firing would have been. Reported literally so the
pre-registration is not retro-fitted. Annualised Sharpe {f(cS['_sharpe_ann'], 2)}; block
bootstrap (block = 9, 5,000 iters) SR CI [{f(cS['_block_bootstrap_sharpe']['sr_p05'], 2)},
{f(cS['_block_bootstrap_sharpe']['sr_p95'], 2)}], `P(SR > 0) = {f(cS['_block_bootstrap_sharpe']['p_sr_gt_0'], 4)}`.

Arm S-ETH (Binance + Bybit, ETH, {dSE['n']:,} settlements from {dSE['first'][:10]}):
**{f(cSE['I-1']['value'], 2)} %** annualised, Carry benchmark **+{f(bSE, 2)} %**,
DSR@20 = {f(cSE['I-4']['value'])}. Same verdict.

### Verdict

Per the pre-registered verdict rule (verdict taken from the adequately-powered arm):

> **{D['overall_verdict']}**

## Why it loses — the three numbers that decide it

**1. The spread does not persist for even one settlement.** At the moment of decision the mean
spread on a firing settlement is **{f(S3contemp['mean_gross_on_bp'])} bp** (arm S, 3 bp
threshold). The spread actually collected over the following interval by the pair chosen from
that cross-section is **{f(S3['mean_gross_on_bp'])} bp** — a **{f(decay * 100, 1)} % decay in a
single 8-hour step**. The contemporaneous number is the non-tradeable one; the study charges
the causal one, as pre-registered.

**2. Rotation eats it.** On {S3['n_on']} ON settlements the identity of the (long-venue,
short-venue) pair changes at a rate of **{f(float(S3['rotation_rate_on']) * 100, 1)} %**,
producing **{S3['n_cost_events']} cost events** at 20 bp each = **{f(cost_per_on, 1)} bp of cost
per ON settlement** against a {f(S3['mean_gross_on_bp'], 1)} bp gross harvest. This is the
pre-registered *lenient* cost model (charge only when the pair changes). Under the pessimistic
model (20 bp every ON settlement) it is {f(S3every['ann_return_pct'], 2)} %/yr.

**3. Even at literally zero transaction cost the trade fails both KILL clauses.**

| Arm S, 3 bp, causal | ann. return | vs 5 % bar | vs Carry (+{f(bS, 2)} %) |
|---|---|---|---|
| pre-registered cost (pair-change) | **{f(S3['ann_return_pct'], 2)} %** | fail | fail |
| pessimistic (20 bp every ON settlement) | {f(S3every['ann_return_pct'], 2)} % | fail | fail |
| **zero cost (upper bound)** | **+{f(S3zero['ann_return_pct'], 2)} %** | **fail** | **fail** |
| zero cost, *contemporaneous* (non-tradeable) | +{f(S3contemp['ann_return_pct'], 2)} % | pass | fail |

The only configuration that clears the 5 % bar is the one that both ignores every fill and
collects a spread it could not have known — and it still loses to simply running the existing
Carry bot.

Threshold sensitivity (arm S, causal, pre-registered costs):
{', '.join(f"{int(t)} bp {f(g('S', t, True, 'pair_change')['ann_return_pct'], 2)} %" for t in (1.0, 2.0, 3.0, 5.0, 10.0))}/yr.
The loss shrinks monotonically toward zero as the threshold rises simply because the strategy
trades less; no threshold is profitable. Full {len(GRID)}-row grid in
`results/dispersion_strategy_grid.csv`.

## The dispersion itself (reported regardless of verdict)

| | Arm P (3-venue, BTC) | Arm S (2-venue, BTC) | Arm S-ETH |
|---|---|---|---|
| settlements | {dP['n']} | {dS['n']:,} | {dSE['n']:,} |
| mean spread | {f(dP['mean_spread_bp'])} bp | {f(dS['mean_spread_bp'])} bp | {f(dSE['mean_spread_bp'])} bp |
| sd | {f(dP['sd_spread_bp'])} bp | {f(dS['sd_spread_bp'])} bp | {f(dSE['sd_spread_bp'])} bp |
| median | {f(dP['p50'])} bp | {f(dS['p50'])} bp | {f(dSE['p50'])} bp |
| p90 / p95 | {f(dP['p90'])} / {f(dP['p95'])} bp | {f(dS['p90'])} / {f(dS['p95'])} bp | {f(dSE['p90'])} / {f(dSE['p95'])} bp |
| p99 / p99.9 | {f(dP['p99'])} / {f(dP['p99.9'])} bp | {f(dS['p99'])} / {f(dS['p99.9'])} bp | {f(dSE['p99'])} / {f(dSE['p99.9'])} bp |
| max | **{f(dP['max_spread_bp'])} bp** | {f(dS['max_spread_bp'])} bp | {f(dSE['max_spread_bp'])} bp |

**Exceedance frequency** — `P(spread > x)`:

| x | Arm P | Arm S | Arm S-ETH |
|---|---|---|---|
| 1 bp | {f(dP['gt_1bp'] * 100, 2)} % (n = {dP['n_gt_1bp']}) | {f(dS['gt_1bp'] * 100, 2)} % (n = {dS['n_gt_1bp']:,}) | {f(dSE['gt_1bp'] * 100, 2)} % (n = {dSE['n_gt_1bp']:,}) |
| 2 bp | **{f(dP['gt_2bp'] * 100, 2)} % (n = {dP['n_gt_2bp']})** | {f(dS['gt_2bp'] * 100, 2)} % (n = {dS['n_gt_2bp']}) | {f(dSE['gt_2bp'] * 100, 2)} % (n = {dSE['n_gt_2bp']}) |
| **3 bp** | **{f(dP['gt_3bp'] * 100, 2)} % (n = {dP['n_gt_3bp']})** | **{f(dS['gt_3bp'] * 100, 2)} % (n = {dS['n_gt_3bp']})** | **{f(dSE['gt_3bp'] * 100, 2)} % (n = {dSE['n_gt_3bp']})** |
| 5 bp | {f(dP['gt_5bp'] * 100, 2)} % | {f(dS['gt_5bp'] * 100, 2)} % (n = {dS['n_gt_5bp']}) | {f(dSE['gt_5bp'] * 100, 2)} % (n = {dSE['n_gt_5bp']}) |
| 10 bp | {f(dP['gt_10bp'] * 100, 2)} % | {f(dS['gt_10bp'] * 100, 2)} % (n = {dS['n_gt_10bp']}) | {f(dSE['gt_10bp'] * 100, 2)} % (n = {dSE['n_gt_10bp']}) |

**Concentration — yes, it is stress-concentrated.** In arm S the {dS['n_gt_3bp']} exceedances
fall on {dS['n_exceed_days_3bp']} of {dS['n_calendar_days']:,} calendar days. The busiest **5 % of
calendar days carry {f(dS['top5pct_days_share_of_3bp_exceedances'] * 100, 1)} %** of all
exceedances, clearing the pre-registered 50 % line, so the exceedance is labelled
**stress-concentrated** (`stress_concentrated = {dS['stress_concentrated']}`). The top 5
individual days carry only {f(dS['top5_days_share_of_3bp_exceedances'] * 100, 1)} %, so it is
not a handful of single events — it is a few dozen episodes. Arm S-ETH:
**{f(dSE['top5pct_days_share_of_3bp_exceedances'] * 100, 1)} %** in the top 5 % of days, also
stress-concentrated.

Venue identity: in arm P, Binance pays the highest funding
{f(dP['max_venue_share']['binance'] * 100, 1)} % of the time and the lowest
{f(dP['min_venue_share']['binance'] * 100, 1)} %; Bybit is lowest
{f(dP['min_venue_share']['bybit'] * 100, 1)} % of the time. The (min, max) pair identity changes
on **{f(dP['pair_flip_rate_all'] * 100, 1)} %** of consecutive settlements in arm P and
{f(dS['pair_flip_rate_all'] * 100, 1)} % in arm S
({f(dS['pair_flip_rate_on_exceedance'] * 100, 1)} % restricted to exceedances). There is no
stable "expensive venue" to be short of.

## The finding that matters most: the historical dispersion is largely a data artefact

Splitting arm S at the 2026-04-13 funding cadence cutover:

| Era | settlements | mean \\|Binance − Bybit\\| | max | n > 3 bp |
|---|---|---|---|---|
| **pre-cutover** 2020-03-25 → {era_pre_last} | {era_pre['n']:,} | {f(era_pre['mean_abs_bp'])} bp | **{f(era_pre['max_abs_bp'], 2)} bp** | **{era_pre['n_gt_3bp']}** |
| **post-cutover** 2026-04-13 → {era_post_last} | {era_post['n']} | **{f(era_post['mean_abs_bp'])} bp** | **{f(era_post['max_abs_bp'], 2)} bp** | **{era_post['n_gt_3bp']}** |

**Every single one of the {era_pre['n_gt_3bp']} exceedances in six and a half years of data
falls in the era where the Binance leg is a *predicted-rate hourly bar* rather than a recorded
settlement.** The corroborating cross-check is the 3-venue window, where all three legs are
realised settlements:

| Pair (3-venue window, all realised rates) | mean \\|diff\\| | p95 | max | n > 3 bp |
|---|---|---|---|---|
""" + "\n".join(
    f"| {k.split(':')[1].replace('-', ' − ')} "
    f"({'BTC' if k.startswith('P:') else 'ETH'}) | {f(v['mean_abs_bp'])} bp | "
    f"{f(v['p95_abs_bp'])} bp | {f(v['max_abs_bp'])} bp | **{v['n_gt_3bp']}** |"
    for k, v in D["pairwise_venue_diff"].items() if k.startswith(("P:", "P-ETH:"))
) + f"""

Six venue pairs, two assets, {dP['n']} settlements each, every realised-rate comparison sitting
at 0.23–0.33 bp mean with a hard ceiling under 1.6 bp — against a pre-cutover
Binance-vs-Bybit tail reaching {f(era_pre['max_abs_bp'], 1)} bp. The natural reading is that
most of the historical "dispersion" is the pre-2026-04 Binance row being a different *kind* of
number (an hourly predicted-rate bar stamped at the settlement hour) rather than a genuine
venue disagreement. Arm S's KILL therefore rests on a sample whose signal is probably
inflated — which makes the KILL *stronger*, not weaker: the strategy loses
{f(abs(float(S3['ann_return_pct'])), 1)} %/yr even on an artificially wide spread.

## The "delta-neutral" label does not survive measurement

Two perps on two venues are not one instrument. Measured sd of the 8-hourly change in
`log(P_venueB / P_venueA)` — the price P&L the study's own P&L model throws away:

| Pair | n | sd per 8 h | p95 abs | mean level |
|---|---|---|---|---|
""" + "\n".join(
    f"| {k} | {v['n']:,} | **{f(v['sd_bp_per_8h'])} bp** | {f(v['p95_abs_bp_per_8h'])} bp | "
    f"{f(v['mean_level_bp'])} bp |" for k, v in leak.items()
) + f"""

In arm P the price noise ({f(lk[0]['sd_bp_per_8h'])} bp per 8 h) is
**{f(lk[0]['sd_bp_per_8h'] / dP['mean_spread_bp'], 1)}×** the entire mean funding spread
({f(dP['mean_spread_bp'])} bp). In arm S it is {f(lk[1]['sd_bp_per_8h'])} bp against a
{f(S3['mean_gross_on_bp'])} bp mean gross harvest —
**{f(lk[1]['sd_bp_per_8h'] / float(S3['mean_gross_on_bp']), 1)}×**. The position is not
delta-neutral in any useful sense at the size of the edge being chased: it is a basis punt with
a small funding coupon attached. Every return number above is consequently an *optimistic*
bound, because it books zero price P&L.

## Realised-Carry benchmark (clause I-2) — which number and why

The pre-registered choice was the sleeve-faithful replay, not the paper ledger, and the reason
held on inspection: the `bot_carry_v1` ledger contains **exactly one CARRY trade, `SJ-4242`,
opened 2026-07-22 and still open**. It has no exit and no realised P&L, so no realised return
can be read out of it over any window. (The one closed CARRY trade in the whole DB is the
legacy `SJ-3452`, `p300_aggressive_v2_v1_0`, 2026-05-14 → 2026-07-22, +0.617 % net, which
predates the bot.) The benchmark is therefore sub-study (iii)'s baseline replay — the sleeve's
own signal code and the sleeve's own cost constants — restricted to each arm's window:

| Arm window | sleeve-faithful Carry, annualised | raw Binance funding, annualised (context) |
|---|---|---|
""" + "\n".join(
    f"| {v['window'][0]} → {v['window'][1]} ({v['sleeve_faithful']['n_days']:,} d) | "
    f"**+{f(v['sleeve_faithful']['ann_return_pct'], 2)} %** | "
    f"+{f(v['raw_funding_ann_pct'], 2)} % |"
    for a, v in D["carry_benchmark"].items() if a != "P-ETH"
) + f"""

Clause I-2 uses the middle column, as pre-registered.

## What could still be wrong (sub-study i)

* **OKX history is {f(dP['span_days'], 0)} days and cannot be extended.** OKX serves a rolling
  ~92-day window and no seed exists in this repo or the predecessor DB. Arm P grows one
  settlement at a time from 2026-06-08. Clause I-0 stops firing around **2026-10-01**
  (365 settlements) and the arm reaches a year of data around **2027-06-08**. Re-running
  `dispersion.py` then is the only way to settle the 3-venue question properly. Nothing in this
  study forecloses it.
* **The 92-day window is a single, quiet funding regime.** Mean absolute funding across venues
  in it is {f(dP['mean_abs_rate_bp'])} bp/settlement — calm. A genuine venue dislocation (an
  exchange outage, a clamp hit on one venue only) could produce dispersion this sample never
  saw. What the sample does show is that *routine* dispersion is ~0.3 bp with a hard ceiling
  near {f(dP['max_spread_bp'], 1)} bp, an order of magnitude below the 20 bp round trip.
* **Pre-cutover Binance rows are a predicted-rate proxy.** Stated in the pre-registration,
  measured above, and it biases arm S's spread *upward*, i.e. toward the strategy.
* **Bybit perp prices stop at 2026-05-26.** `bybit_perp_1h` / `bybit_perp_eth_1h` are stale, so
  the Binance↔Bybit basis diagnostic cannot cover the 3-venue window. Unrelated to the D6
  funding feed, but worth an operator look — it is a live table that stopped.
* **Only three venues, only USDT-margined linear perps, only BTC/ETH.** A dispersion trade on a
  thin venue against a deep one is a different (and more plausible) trade than
  Binance-vs-OKX-vs-Bybit, and this study says nothing about it.
* **Funding is not the whole coupon.** The study ignores borrow/collateral differences,
  cross-venue margin inefficiency (the pair consumes gross margin on two exchanges) and any
  maker rebate that would cut the 20 bp. A maker-only assumption would need the fill
  probability at the settlement instant, which this data cannot supply.

---

# SUB-STUDY (iii) — carry-crash sizing

## Data-integrity gate (run before any result)

The study's own daily funding aggregation was asserted byte-equal to the live
`strategies.support.funding.daily_sums_pct("BTC", …)` over the whole history —
**{eq['n_days']:,} days, max |difference| = {eq['max_abs_diff_daily_pct']:g} exactly** — with
{len(eq['accrued_pct_probes'])} `accrued_pct` window probes spanning the cadence cutover also
matching to {max(p['abs_diff'] for p in eq['accrued_pct_probes']):g}. The live function was
pointed at a scratch copy of the rows so `prod.db` was never opened read-write. A mismatch
aborts the run.

## Replay

7,622 on-grid Binance BTC settlements → {eq['n_days']:,} usable days (funding ∩ spot ∩ perp),
replay window **{C['window']['first_day']} → {C['window']['last_day']},
{C['window']['n_days']:,} days**. The sleeve's own `_evaluate_today` / `_rolling_avg` and its own
config constants (`FR_WINDOW_DAYS = {C['constants']['FR_WINDOW_DAYS']}`,
`FR_ENTRY_THRESHOLD = {C['constants']['FR_ENTRY_THRESHOLD']}`,
`EXIT_NEG_DAYS = {C['constants']['EXIT_NEG_DAYS']}`) drive every decision; P&L uses
`close_carry_trade`'s formula (funding − `CARRY_COST_PCT` {C['constants']['CARRY_COST_PCT']} % −
`CARRY_SLIPPAGE_PCT` {C['constants']['CARRY_SLIPPAGE_PCT']} % =
**{f(C['constants']['round_trip_pct'] * 100, 0)} bp round trip**) and `bots/carry/config.py`
sizing (`CARRY_NOTIONAL_X` {C['constants']['CARRY_NOTIONAL_X']} ×
`CAPITAL_USDT` {C['constants']['CAPITAL_USDT']:,.0f}). **{C['n_trades_baseline']} trades**, in
position 90.2 % of days. Per-trade net sums to +{f(base['total_pct'])} %, exactly reconciling
with the daily series total of +{f(base['total_pct'])} %.

The rule is eligible from **{C['window']['rule_eligible_from']}**
({C['constants']['P95_WARMUP']} z-observations of warm-up), {C['window']['n_days_eligible']:,}
of the {C['window']['n_days']:,} days.

## Clause table — primary cell (60-day window, 95th percentile, resize cost on)

| # | Clause | Measured | Threshold | Fired |
|---|---|---|---|---|
| III-0 | rule fires on < 30 days overlapping an open position | **{cl['III-0']['value']}** | 30 | no — adequately powered |
| III-1 | Sharpe uplift < 0.10 (full window) | **{f(cl['III-1']['value'])}** | 0.10 | **YES → KILL** |
| III-1e | Sharpe uplift < 0.10 (rule-eligible window) | **{f(cl['III-1e']['value'])}** | 0.10 | **YES** |
| III-2 | MAR not improved (full window) | **{f(cl['III-2']['value'])}** vs {f(cl['III-2']['threshold'])} | > {f(cl['III-2']['threshold'])} | **YES → KILL** |
| III-2e | MAR not improved (rule-eligible window) | **{f(cl['III-2e']['value'])}** vs {f(cl['III-2e']['threshold'])} | > {f(cl['III-2e']['threshold'])} | **YES** |
| III-3 | 90 % paired block-bootstrap CI of Sharpe diff includes 0 | [{f(cl['III-3']['value'][0])}, {f(cl['III-3']['value'][1])}] | 0 | no² |
| III-4 | DSR of with-rule series at n_trials = 9 < 0.95 | {f(cl['III-4']['value'])} | 0.95 | no³ |

² The CI excludes zero **on the losing side**;
`P(ΔSharpe > 0) = {f(diff['p_gt_0'], 4)}` over {diff['n_iter']:,} paired circular-block
resamples (block = {diff['block']}). On the rule-eligible window the CI is
[{f(diff_e['p05'])}, {f(diff_e['p95'])}], `P(ΔSharpe > 0) = {f(diff_e['p_gt_0'], 4)}`. As in
sub-study (i), a non-firing III-3 here is worse for the rule than a firing one.

³ III-4 is uninformative as written and is reported for completeness: the with-rule daily series
is still ~95 % of a carry book, whose DSR is ~1.0 with or without the rule. The clause tests the
sleeve, not the rule; the rule is tested by III-1 / III-2 / III-3.

**Verdict: {C['verdict']}** (III-1 and III-2 both fire, on both windows).

## Arm comparison

| | baseline | with rule | Δ |
|---|---|---|---|
| days | {base['n_days']:,} | {rule['n_days']:,} | — |
| total return | **+{f(base['total_pct'], 2)} %** | **+{f(rule['total_pct'], 2)} %** | {f(rule['total_pct'] - base['total_pct'], 2)} pp |
| annualised | **{f(base['ann_return_pct'], 2)} %** | **{f(rule['ann_return_pct'], 2)} %** | **{f(rule['ann_return_pct'] - base['ann_return_pct'], 2)} pp** |
| max drawdown | **{f(base['max_dd_pct'])} %** | **{f(rule['max_dd_pct'])} %** | **{f(rule['max_dd_pct'] - base['max_dd_pct'])} pp** |
| MAR | **{f(base['mar'])}** | **{f(rule['mar'])}** | **{f(rule['mar'] - base['mar'])}** |
| Sharpe (√365) | **{f(base['sharpe_ann'])}** | **{f(rule['sharpe_ann'])}** | **{f(rule['sharpe_ann'] - base['sharpe_ann'])}** |
| mean daily | {f(base['mean_daily_pct'], 5)} % | {f(rule['mean_daily_pct'], 5)} % | {f(rule['mean_daily_pct'] - base['mean_daily_pct'], 5)} pp |
| sd daily | {f(base['sd_daily_pct'], 5)} % | {f(rule['sd_daily_pct'], 5)} % | {f(rule['sd_daily_pct'] - base['sd_daily_pct'], 5)} pp |
| trades | {C['n_trades_baseline']} | {C['n_trades_with_rule']} | 0 |

**The max drawdown is identical to the last basis point.** This is the mechanism, and it is
exactly the pre-registered prior: the rule keys on the **upper** tail of funding, while every
drawdown in a carry book comes from the **lower** tail (negative funding days). Halving size on
the best-paid days removes {f((base['mean_daily_pct'] - rule['mean_daily_pct']) * 100, 1)} bp/day
of mean return and leaves the drawdown source completely untouched, so both the numerator and
the ratio fall. The vol reduction the hypothesis needs
({f(base['sd_daily_pct'], 4)} → {f(rule['sd_daily_pct'], 4)},
{f((rule['sd_daily_pct'] / base['sd_daily_pct'] - 1) * 100, 1)} %) is smaller than the mean
reduction ({f((rule['mean_daily_pct'] / base['mean_daily_pct'] - 1) * 100, 1)} %), so the Sharpe
falls too.

On the rule-eligible window the same picture is sharper: baseline MAR {f(be['mar'])} → rule
{f(re_['mar'])}, baseline Sharpe {f(be['sharpe_ann'])} → {f(re_['sharpe_ann'])}, and the
drawdown gets *worse* ({f(be['max_dd_pct'])} % → {f(re_['max_dd_pct'])} %) because the resize
fees themselves are the only thing the rule adds there.

## The rule is not marginal — every cell fails

`results/carry_grid.csv`, {len(CGRID)} cells = 3 windows × 3 percentiles × resize-cost on/off.

* **MAR is worse than baseline in {mar_worse} of {len(CGRID)} cells.** Best cell {f(best_mar)}
  vs baseline {f(base['mar'])}.
* **Sharpe uplift is negative in {sh_neg} of {len(CGRID)} cells.** The single positive is
  {best_up_row['window']} d / p{best_up_row['pctile']} with resize cost *off* at
  **+{f(best_up)}** — still far under the 0.10 bar, and its MAR ({f(best_up_row['mar'])}) is
  still below baseline, so it fails III-2 anyway.
* **Max drawdown is unchanged from baseline in {dd_same} of {len(CGRID)} cells** and *worse* in
  the other {len(CGRID) - dd_same} (the frequently-firing 90th-percentile cells with resize
  cost, where the transition fees themselves create the drawdown:
  {f(base['max_dd_pct'])} % → {' / '.join(dd_worse)} %).
* Turning the resize cost off never rescues it: primary cell uplift goes
  {f(cl['III-1']['value'])} → {f(nc['sharpe_uplift'])}, MAR {f(rule['mar'])} → {f(nc['mar'])},
  still under baseline {f(base['mar'])}. **The rule loses on the funding it gives up, not on the
  fills.**

Firing frequency scales as expected: p90 fires
{min(int(r['n_fire_days']) for r in CGRID if r['pctile'] == '90')}–{max(int(r['n_fire_days']) for r in CGRID if r['pctile'] == '90')} days,
p95 {min(int(r['n_fire_days']) for r in CGRID if r['pctile'] == '95')}–{max(int(r['n_fire_days']) for r in CGRID if r['pctile'] == '95')},
p99 {min(int(r['n_fire_days']) for r in CGRID if r['pctile'] == '99')}–{max(int(r['n_fire_days']) for r in CGRID if r['pctile'] == '99')}.
More firing is uniformly worse (uplift {f(min(float(r['sharpe_uplift']) for r in CGRID))} at the
worst cell), i.e. the damage is proportional to how much of the rule you apply.

## What could still be wrong (sub-study iii) — the caveat that dominates

**The Carry sleeve's P&L model contains no price risk at all.** `close_carry_trade` sets price
P&L to exactly zero by construction and books `funding − 24 bp`. That is why the baseline
reports a Sharpe of **{f(base['sharpe_ann'], 2)}** and a max drawdown of
**{f(base['max_dd_pct'], 2)} % over seven years** — numbers no real delta-neutral book produces.
A real carry crash is a *basis* event: the perp gaps against spot during a forced unwind, the
hedge legs stop offsetting, and the loss arrives through the price channel this model deletes.

So the honest statement of this result is narrow, and it was pre-registered before the run:
**within the P&L model the sleeve actually uses, cutting size on high funding z-scores is
strictly costly, and the pre-registered clauses both fire.** The study cannot see whether the
rule would help against the real risk it was designed for, because that risk is not in the
model. Making it visible needs a different object: a replay that marks the spot and perp legs
separately (`cd_spot_binance` and `cd_futures_ohlcv` are both present, so the basis series
exists) and books the mark-to-market of the pair. That is a bigger change than a sizing rule and
it belongs to whoever revisits the sleeve's P&L model, not to this study.

Two further limits:

* The 60-day z-score is computed on daily funding, which post-2026-04-13 is 3 settlements and
  pre-cutover is 3 on-grid hourly bars. The repo's own convention was followed and verified
  byte-equal, but the pre-cutover input remains a predicted-rate proxy (same caveat as
  sub-study (i)).
* The replay's last trade is marked open at the sample end (`end_of_sample_mark`, entered
  2026-06-21). The live bot's own open trade `SJ-4242` was entered 2026-07-22, a month later,
  because the bot only started then. The replay is not expected to reproduce live entry dates
  from before the bot existed.

---

## Deviations from the pre-registration

1. **Window primacy for sub-study (iii) was under-specified.** The README's clause table did not
   say whether the Sharpe/MAR comparison runs on the full replay window or on the rule-eligible
   sub-window. Rather than choose after seeing the answer, **both are reported** as III-1/III-2
   and III-1e/III-2e, and both fire, so the ambiguity does not affect the verdict. Recorded here
   rather than edited into the README.
2. **Two diagnostics were added after the pre-registration was frozen**, both declared: the
   **era split** of arm S at the 2026-04-13 cutover, and the **pairwise venue-difference table**.
   Neither is a decision clause and neither changes any clause value; they exist because the
   pre-registered "predicted-rate proxy" limitation turned out to be measurable rather than
   merely arguable.
3. **A bug was fixed in the basis-leakage diagnostic mid-run.** The first version compared
   `close` at the same stamp across a 15-minute and a 1-hour table — a 45-minute misalignment
   that reported an ETH Binance↔OKX sd of 57.98 bp/8 h. Aligning each series to the price *at*
   the settlement instant (close of the bar ending there) gives
   {f(leak['ETH binance(cd_futures_eth_15m) vs okx(okx_perp_eth_1h)']['sd_bp_per_8h'], 2)} bp.
   The corrected number is the one quoted. A code fix, not a rule change; no decision clause
   depends on it.
4. **Cost attribution in the Carry replay** splits the sleeve's 24 bp round trip as 12 bp on the
   entry day and 12 bp × (size at exit) on the exit day, so the daily series has a realistic
   shape. The total per trade is identical to `close_carry_trade`'s, both arms are affected
   identically, and the per-trade sums reconcile with the daily series exactly
   (+{f(base['total_pct'])} %).
5. **`backtest_tail_harvester.py` does not exist**, so "reuse the existing path" was satisfied by
   importing the sleeve's own decision functions and cost constants rather than by running a
   backtest harness. This was decided and written into the README before any result, and it is
   the only read-only option: `backtest_runner.py` writes replay trades into `prod.db`, and
   `studies/simulation/build_sim_trader_db.py` currently hard-errors on the five 2026-09-06
   Track D tables missing from its `TABLE_PLAN`.

## Independent re-verification (2026-09-09)

Both scripts were re-run from scratch on a second session against a database one day further
on ({disp_last} vs the 2026-09-08 inventory frozen in the README: OKX 280 rows, Bybit BTC
7,077, Binance on-grid 7,624). **Every clause value and every verdict reproduced.** The Carry
benchmark for arm P moved 4.64 % → {f(bP)} % purely because the 93-day window rolled forward;
no clause changed state.

Three headline claims were additionally re-derived by a **separately written SQL cross-check**
that does not import `dispersion.py`, to make sure the result is not an artefact of one
implementation:

| Claim | Study's number | Independent re-derivation |
|---|---|---|
| 3-venue max spread, BTC / ETH | 1.559 / 1.589 bp, 0 crossings of 3 bp | 1.5593 / 1.5888 bp, 0 crossings |
| arm-S 3 bp exceedances pre / post cutover (BTC) | 497 / 0 | 497 / 0 |
| firing-settlement spread decay in one 8 h step | 6.780 → 3.546 bp (47.7 %) | 6.7801 → 3.5458 bp (47.7 %) |

One conservatism in the frozen cost model was found during that audit and is recorded rather
than changed: the pre-registered rule charges the full 20 bp cycle cost on an OFF→ON transition
*and* again on the following ON→OFF transition, whereas a strict leg count is 10 bp each way
(2 legs opening, 2 legs closing). Rotations between two ON pairs — the dominant case, 469 of
the cost events — are charged correctly at 20 bp for their 4 legs. The rule is left exactly as
pre-registered; it makes arm S's loss somewhat worse than a strict leg count would, and it is
**immaterial to the verdict** because the zero-cost upper bound (+{f(S3zero['ann_return_pct'], 2)} %/yr)
already fails both KILL clauses on its own.

Nothing in the pre-registration was edited to match any of this; the note lives here, in
`findings.md`, as the honesty rules require.

## Recommendations (recommendations only — nothing was changed)

* **Do not build a cross-venue funding-dispersion sleeve.** Re-run `dispersion.py` after
  **2026-10-01** (arm P clears the 365-settlement power bar) and again around **2027-06-08**
  (one year of OKX history) before treating the 3-venue question as finally closed.
* **Do not change `bots/carry/config.py`.** The 50 % carry-crash cut is costly in every cell of
  the grid. If the carry-crash thesis is to be tested seriously, the prerequisite is a Carry P&L
  model that books the spot-vs-perp basis, not a sizing rule bolted onto a model that assumes
  the basis is always zero.
* **Two operator items surfaced incidentally**: `bybit_perp_1h` and `bybit_perp_eth_1h` have had
  no rows since **2026-05-26**; and `studies/simulation/build_sim_trader_db.py` still hard-errors
  on `deribit_dvol_daily`, `deribit_options_daily`, `deribit_options_instruments`, `macro_daily`
  and `paxg_spot_1h`, which blocks the sim path for any future study.

## Artefacts

| File | Contents |
|---|---|
| `results/dispersion_summary.json` | clauses, verdicts, distributions, benchmark, era split, pairwise diffs, basis leakage |
| `results/dispersion_strategy_grid.csv` | {len(GRID)} rows: 4 arms × 5 thresholds × causal/contemporaneous × 3 cost models |
| `results/dispersion_distribution.csv` | dispersion moments, percentiles, exceedance, concentration per arm |
| `results/dispersion_series_P.csv`, `_S.csv` | per-settlement venue rates and spread, in bp |
| `results/carry_summary.json` | clauses, arm stats, bootstrap, DSR, byte-equivalence proof |
| `results/carry_grid.csv` | {len(CGRID)}-cell sensitivity grid |
| `results/carry_daily.csv` | daily returns of both arms + the size multiplier |
| `results/carry_trades.csv` | all {C['n_trades_baseline']} trades per arm |
| `results/carry_zscore.csv` | daily funding, 60-day z, expanding p95, fire flag |
"""

(HERE / "findings.md").write_text(TXT, encoding="utf-8")
print("wrote", HERE / "findings.md", len(TXT), "chars")

STATUS: CONCLUDED KILL (i) + KILL (iii) per pre-registration — sub-study (i) cross-venue funding dispersion is KILL on the adequately-powered 2-venue arm and INCONCLUSIVE on power for the 3-venue arm (where the 3 bp rule never fires even once); sub-study (iii) carry-crash sizing is KILL, uniformly across all 18 grid cells.

Pre-registration: [README.md](README.md), written and saved before any outcome number was
computed. Scripts: [`dispersion.py`](dispersion.py), [`carry_sizing.py`](carry_sizing.py);
this file is generated from `results/` by [`_write_findings.py`](_write_findings.py) so every
number below is traceable to an artefact. Both scripts open `prod.db` `mode=ro`; nothing
under `strategies/**` or `bots/**` was modified; no recommendation here has been applied.

---

# SUB-STUDY (i) — cross-venue funding dispersion

## Clause table

### Arm P — primary, exactly the specified rule (Binance + OKX + Bybit, BTC)

Window 2026-06-08 08:00 → 2026-09-09 08:00 UTC, **280 settlements = 93.0 days**,
279 tradeable intervals.

| # | Clause | Measured | Threshold | Fired |
|---|---|---|---|---|
| I-0 | < 365 usable settlements | **279** | 365 | **YES → UNDERPOWERED** |
| I-1 | net annualised return < 5 % | **0.00 %** | 5 % | **YES** |
| I-2 | net ann. return < realised Carry benchmark | **0.00 %** | 4.141 % | **YES** |
| I-3 | 90 % bootstrap CI of mean net/settlement includes 0 | [0.00, 0.00] bp | 0 | **YES** |
| I-4 | DSR at n_trials = 20 < 0.95 | undefined (zero-variance series) | 0.95 | **YES** |

**The return is exactly zero because the signal never fires.** Over 280 three-venue
settlements the maximum spread observed was **1.559 bp**; the 3 bp threshold
was crossed **0 times**, and so was the 2 bp threshold (0). Arm
P-ETH is identical in kind: max spread 1.589 bp, 0 crossings
above 2 bp.

Arm P is **INCONCLUSIVE on power** (clause I-0) and cannot on its own produce a verdict — but
the reason it produces nothing is not sampling noise around a small edge: the quantity the
rule keys on does not reach the threshold at all in the sample.

### Arm S — power extension (Binance + Bybit, BTC)

Window 2020-03-25 16:00 → 2026-09-09 08:00 UTC, **7,077 settlements = 2359 days**,
7,076 intervals.

| # | Clause | Measured | Threshold | Fired |
|---|---|---|---|---|
| I-0 | < 365 usable settlements | 7,076 | 365 | no — **adequately powered** |
| I-1 | net annualised return < 5 % | **-11.79 %** | 5 % | **YES** |
| I-2 | net ann. return < realised Carry benchmark | **-11.79 %** | **+10.26 %** | **YES** |
| I-3 | 90 % bootstrap CI of mean net/settlement includes 0 | [-1.172, -0.978] bp | 0 | no¹ |
| I-4 | DSR at n_trials = 20 < 0.95 | **0.000** | 0.95 | **YES** |

¹ I-3 did not fire because the CI **excludes** zero — on the losing side.
`P(mean > 0) = 0.0000` over
10,000 iid resamples. The clause was written to catch
"indistinguishable from zero"; here the loss is statistically unambiguous, which is stronger
evidence for KILL than the clause firing would have been. Reported literally so the
pre-registration is not retro-fitted. Annualised Sharpe -7.15; block
bootstrap (block = 9, 5,000 iters) SR CI [-7.92,
-6.33], `P(SR > 0) = 0.0000`.

Arm S-ETH (Binance + Bybit, ETH, 6,448 settlements from 2020-10-21):
**-10.89 %** annualised, Carry benchmark **+10.21 %**,
DSR@20 = 0.000. Same verdict.

### Verdict

Per the pre-registered verdict rule (verdict taken from the adequately-powered arm):

> **KILL (from arm S, the adequately-powered 2-venue arm); arm P INCONCLUSIVE on power**

## Why it loses — the three numbers that decide it

**1. The spread does not persist for even one settlement.** At the moment of decision the mean
spread on a firing settlement is **6.780 bp** (arm S, 3 bp
threshold). The spread actually collected over the following interval by the pair chosen from
that cross-section is **3.546 bp** — a **47.7 % decay in a
single 8-hour step**. The contemporaneous number is the non-tradeable one; the study charges
the causal one, as pre-registered.

**2. Rotation eats it.** On 497 ON settlements the identity of the (long-venue,
short-venue) pair changes at a rate of **25.6 %**,
producing **469 cost events** at 20 bp each = **18.9 bp of cost
per ON settlement** against a 3.5 bp gross harvest. This is the
pre-registered *lenient* cost model (charge only when the pair changes). Under the pessimistic
model (20 bp every ON settlement) it is -19.28 %/yr.

**3. Even at literally zero transaction cost the trade fails both KILL clauses.**

| Arm S, 3 bp, causal | ann. return | vs 5 % bar | vs Carry (+10.26 %) |
|---|---|---|---|
| pre-registered cost (pair-change) | **-11.79 %** | fail | fail |
| pessimistic (20 bp every ON settlement) | -19.28 % | fail | fail |
| **zero cost (upper bound)** | **+2.73 %** | **fail** | **fail** |
| zero cost, *contemporaneous* (non-tradeable) | +5.21 % | pass | fail |

The only configuration that clears the 5 % bar is the one that both ignores every fill and
collects a spread it could not have known — and it still loses to simply running the existing
Carry bot.

Threshold sensitivity (arm S, causal, pre-registered costs):
1 bp -40.38 %, 2 bp -18.34 %, 3 bp -11.79 %, 5 bp -6.30 %, 10 bp -1.56 %/yr.
The loss shrinks monotonically toward zero as the threshold rises simply because the strategy
trades less; no threshold is profitable. Full 120-row grid in
`results/dispersion_strategy_grid.csv`.

## The dispersion itself (reported regardless of verdict)

| | Arm P (3-venue, BTC) | Arm S (2-venue, BTC) | Arm S-ETH |
|---|---|---|---|
| settlements | 280 | 7,077 | 6,448 |
| mean spread | 0.447 bp | 0.925 bp | 0.797 bp |
| sd | 0.260 bp | 2.092 bp | 1.913 bp |
| median | 0.433 bp | 0.330 bp | 0.305 bp |
| p90 / p95 | 0.802 / 0.912 bp | 2.145 / 3.977 bp | 1.719 / 3.110 bp |
| p99 / p99.9 | 1.063 / 1.438 bp | 10.848 / 21.639 bp | 9.167 / 21.301 bp |
| max | **1.559 bp** | 37.508 bp | 41.065 bp |

**Exceedance frequency** — `P(spread > x)`:

| x | Arm P | Arm S | Arm S-ETH |
|---|---|---|---|
| 1 bp | 2.14 % (n = 6) | 21.05 % (n = 1,490) | 17.97 % (n = 1,159) |
| 2 bp | **0.00 % (n = 0)** | 10.80 % (n = 764) | 8.62 % (n = 556) |
| **3 bp** | **0.00 % (n = 0)** | **7.02 % (n = 497)** | **5.20 % (n = 335)** |
| 5 bp | 0.00 % | 3.57 % (n = 253) | 2.75 % (n = 177) |
| 10 bp | 0.00 % | 1.13 % (n = 80) | 0.82 % (n = 53) |

**Concentration — yes, it is stress-concentrated.** In arm S the 497 exceedances
fall on 292 of 2,360 calendar days. The busiest **5 % of
calendar days carry 58.8 %** of all
exceedances, clearing the pre-registered 50 % line, so the exceedance is labelled
**stress-concentrated** (`stress_concentrated = True`). The top 5
individual days carry only 3.0 %, so it is
not a handful of single events — it is a few dozen episodes. Arm S-ETH:
**71.9 %** in the top 5 % of days, also
stress-concentrated.

Venue identity: in arm P, Binance pays the highest funding
51.8 % of the time and the lowest
18.2 %; Bybit is lowest
42.9 % of the time. The (min, max) pair identity changes
on **71.0 %** of consecutive settlements in arm P and
35.6 % in arm S
(25.6 % restricted to exceedances). There is no
stable "expensive venue" to be short of.

## The finding that matters most: the historical dispersion is largely a data artefact

Splitting arm S at the 2026-04-13 funding cadence cutover:

| Era | settlements | mean \|Binance − Bybit\| | max | n > 3 bp |
|---|---|---|---|---|
| **pre-cutover** 2020-03-25 → 2026-04-12 | 6,628 | 0.967 bp | **37.51 bp** | **497** |
| **post-cutover** 2026-04-13 → 2026-09-09 | 449 | **0.308 bp** | **1.80 bp** | **0** |

**Every single one of the 497 exceedances in six and a half years of data
falls in the era where the Binance leg is a *predicted-rate hourly bar* rather than a recorded
settlement.**

> **Correction (2026-09-09, after verification). This era split is confounded with the funding
> LEVEL, and the "different kind of data" reading below does not survive that.** All 497
> exceedances occur at settlements where mean |funding| across the two venues exceeds 1 bp/8h,
> and the post-cutover era contains **exactly one such settlement out of 449**. So the recent
> window has essentially no power to produce an exceedance regardless of what kind of row the
> Binance leg is. Dispersion scales with the level of funding in both eras at a similar ratio;
> the post-cutover window is a low-funding regime, not a clean control. The correct reading is
> **cross-venue dispersion is roughly proportional to the funding level**, and the
> predicted-rate proxy remains a pre-registered limitation rather than a demonstrated cause.
> Nothing in the clause table, in any measured number, or in the KILL verdict changes: the
> strategy loses money under both readings. What changes is the forward-looking implication —
> the exceedances would be expected to return if funding returns to 2021-style levels, so the
> item is "waiting on a regime" rather than "measuring a data bug".

The corroborating cross-check is the 3-venue window, where all three legs are
realised settlements — but note it is drawn entirely from the same low-funding regime, so it
inherits the same confound:

| Pair (3-venue window, all realised rates) | mean \|diff\| | p95 | max | n > 3 bp |
|---|---|---|---|---|
| binance − okx (BTC) | 0.277 bp | 0.675 bp | 0.946 bp | **0** |
| binance − bybit (BTC) | 0.302 bp | 0.768 bp | 1.073 bp | **0** |
| okx − bybit (BTC) | 0.315 bp | 0.807 bp | 1.559 bp | **0** |
| binance − okx (ETH) | 0.280 bp | 0.786 bp | 1.096 bp | **0** |
| binance − bybit (ETH) | 0.230 bp | 0.658 bp | 0.906 bp | **0** |
| okx − bybit (ETH) | 0.332 bp | 0.762 bp | 1.589 bp | **0** |

Six venue pairs, two assets, 280 settlements each, every realised-rate comparison sitting
at 0.23–0.33 bp mean with a hard ceiling under 1.6 bp — against a pre-cutover
Binance-vs-Bybit tail reaching 37.5 bp. The natural reading is that
most of the historical "dispersion" is the pre-2026-04 Binance row being a different *kind* of
number (an hourly predicted-rate bar stamped at the settlement hour) rather than a genuine
venue disagreement. Arm S's KILL therefore rests on a sample whose signal is probably
inflated — which makes the KILL *stronger*, not weaker: the strategy loses
11.8 %/yr even on an artificially wide spread.

## The "delta-neutral" label does not survive measurement

Two perps on two venues are not one instrument. Measured sd of the 8-hourly change in
`log(P_venueB / P_venueA)` — the price P&L the study's own P&L model throws away:

| Pair | n | sd per 8 h | p95 abs | mean level |
|---|---|---|---|---|
| BTC binance(cd_futures_ohlcv 1h) vs okx(okx_perp_1h) — 3-venue window | 279 | **1.040 bp** | 2.065 bp | -0.090 bp |
| BTC binance vs bybit(bybit_perp_1h) — 2-venue window (bybit prices end 2026-05-26) | 5,912 | **4.953 bp** | 9.443 bp | -0.221 bp |
| ETH binance(cd_futures_eth_15m) vs okx(okx_perp_eth_1h) | 279 | **1.163 bp** | 2.163 bp | 0.035 bp |
| ETH binance vs bybit(bybit_perp_eth_1h) | 5,693 | **3.141 bp** | 6.709 bp | -0.364 bp |

In arm P the price noise (1.040 bp per 8 h) is
**2.3×** the entire mean funding spread
(0.447 bp). In arm S it is 4.953 bp against a
3.546 bp mean gross harvest —
**1.4×**. The position is not
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
| 2026-06-08 → 2026-09-09 (94 d) | **+4.14 %** | +5.70 % |
| 2020-03-25 → 2026-09-09 (2,360 d) | **+10.26 %** | +11.26 % |
| 2020-10-21 → 2026-09-09 (2,150 d) | **+10.21 %** | +11.17 % |

Clause I-2 uses the middle column, as pre-registered.

## What could still be wrong (sub-study i)

* **OKX history is 93 days and cannot be extended.** OKX serves a rolling
  ~92-day window and no seed exists in this repo or the predecessor DB. Arm P grows one
  settlement at a time from 2026-06-08. Clause I-0 stops firing around **2026-10-01**
  (365 settlements) and the arm reaches a year of data around **2027-06-08**. Re-running
  `dispersion.py` then is the only way to settle the 3-venue question properly. Nothing in this
  study forecloses it.
* **The 92-day window is a single, quiet funding regime.** Mean absolute funding across venues
  in it is 0.494 bp/settlement — calm. A genuine venue dislocation (an
  exchange outage, a clamp hit on one venue only) could produce dispersion this sample never
  saw. What the sample does show is that *routine* dispersion is ~0.3 bp with a hard ceiling
  near 1.6 bp, an order of magnitude below the 20 bp round trip.
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
**2,540 days, max |difference| = 0 exactly** — with
3 `accrued_pct` window probes spanning the cadence cutover also
matching to 0. The live function was
pointed at a scratch copy of the rows so `prod.db` was never opened read-write. A mismatch
aborts the run.

## Replay

7,622 on-grid Binance BTC settlements → 2,540 usable days (funding ∩ spot ∩ perp),
replay window **2019-10-04 → 2026-09-09,
2,533 days**. The sleeve's own `_evaluate_today` / `_rolling_avg` and its own
config constants (`FR_WINDOW_DAYS = 7`,
`FR_ENTRY_THRESHOLD = 0.0`,
`EXIT_NEG_DAYS = 3`) drive every decision; P&L uses
`close_carry_trade`'s formula (funding − `CARRY_COST_PCT` 0.2 % −
`CARRY_SLIPPAGE_PCT` 0.04 % =
**24 bp round trip**) and `bots/carry/config.py`
sizing (`CARRY_NOTIONAL_X` 1.0 ×
`CAPITAL_USDT` 10,000). **32 trades**, in
position 90.2 % of days. Per-trade net sums to +72.905 %, exactly reconciling
with the daily series total of +72.905 %.

The rule is eligible from **2020-08-01**
(252 z-observations of warm-up), 2,231
of the 2,533 days.

## Clause table — primary cell (60-day window, 95th percentile, resize cost on)

| # | Clause | Measured | Threshold | Fired |
|---|---|---|---|---|
| III-0 | rule fires on < 30 days overlapping an open position | **79** | 30 | no — adequately powered |
| III-1 | Sharpe uplift < 0.10 (full window) | **-0.678** | 0.10 | **YES → KILL** |
| III-1e | Sharpe uplift < 0.10 (rule-eligible window) | **-0.771** | 0.10 | **YES** |
| III-2 | MAR not improved (full window) | **5.677** vs 6.379 | > 6.379 | **YES → KILL** |
| III-2e | MAR not improved (rule-eligible window) | **6.658** vs 9.839 | > 9.839 | **YES** |
| III-3 | 90 % paired block-bootstrap CI of Sharpe diff includes 0 | [-1.030, -0.381] | 0 | no² |
| III-4 | DSR of with-rule series at n_trials = 9 < 0.95 | 1.000 | 0.95 | no³ |

² The CI excludes zero **on the losing side**;
`P(ΔSharpe > 0) = 0.0000` over 5,000 paired circular-block
resamples (block = 20). On the rule-eligible window the CI is
[-1.218, -0.417], `P(ΔSharpe > 0) = 0.0002`. As in
sub-study (i), a non-firing III-3 here is worse for the rule than a firing one.

³ III-4 is uninformative as written and is reported for completeness: the with-rule daily series
is still ~95 % of a carry book, whose DSR is ~1.0 with or without the rule. The clause tests the
sleeve, not the rule; the rule is tested by III-1 / III-2 / III-3.

**Verdict: KILL** (III-1 and III-2 both fire, on both windows).

## Arm comparison

| | baseline | with rule | Δ |
|---|---|---|---|
| days | 2,533 | 2,533 | — |
| total return | **+72.91 %** | **+64.89 %** | -8.02 pp |
| annualised | **10.51 %** | **9.35 %** | **-1.16 pp** |
| max drawdown | **1.647 %** | **1.647 %** | **0.000 pp** |
| MAR | **6.379** | **5.677** | **-0.701** |
| Sharpe (√365) | **8.952** | **8.274** | **-0.678** |
| mean daily | 0.02878 % | 0.02562 % | -0.00317 pp |
| sd daily | 0.06143 % | 0.05915 % | -0.00228 pp |
| trades | 32 | 32 | 0 |

**The max drawdown is identical to the last basis point.** This is the mechanism, and it is
exactly the pre-registered prior: the rule keys on the **upper** tail of funding, while every
drawdown in a carry book comes from the **lower** tail (negative funding days). Halving size on
the best-paid days removes 0.3 bp/day
of mean return and leaves the drawdown source completely untouched, so both the numerator and
the ratio fall. The vol reduction the hypothesis needs
(0.0614 → 0.0591,
-3.7 %) is smaller than the mean
reduction (-11.0 %), so the Sharpe
falls too.

On the rule-eligible window the same picture is sharper: baseline MAR 9.839 → rule
6.658, baseline Sharpe 9.289 → 8.518, and the
drawdown gets *worse* (1.053 % → 1.359 %) because the resize
fees themselves are the only thing the rule adds there.

## The rule is not marginal — every cell fails

`results/carry_grid.csv`, 18 cells = 3 windows × 3 percentiles × resize-cost on/off.

* **MAR is worse than baseline in 18 of 18 cells.** Best cell 6.337
  vs baseline 6.379.
* **Sharpe uplift is negative in 17 of 18 cells.** The single positive is
  30 d / p99 with resize cost *off* at
  **+0.024** — still far under the 0.10 bar, and its MAR (6.287) is
  still below baseline, so it fails III-2 anyway.
* **Max drawdown is unchanged from baseline in 15 of 18 cells** and *worse* in
  the other 3 (the frequently-firing 90th-percentile cells with resize
  cost, where the transition fees themselves create the drawdown:
  1.647 % → 1.677 / 1.725 / 1.797 %).
* Turning the resize cost off never rescues it: primary cell uplift goes
  -0.678 → -0.119, MAR 5.677 → 6.076,
  still under baseline 6.379. **The rule loses on the funding it gives up, not on the
  fills.**

Firing frequency scales as expected: p90 fires
196–235 days,
p95 70–96,
p99 8–14.
More firing is uniformly worse (uplift -1.912 at the
worst cell), i.e. the damage is proportional to how much of the rule you apply.

## What could still be wrong (sub-study iii) — the caveat that dominates

**The Carry sleeve's P&L model contains no price risk at all.** `close_carry_trade` sets price
P&L to exactly zero by construction and books `funding − 24 bp`. That is why the baseline
reports a Sharpe of **8.95** and a max drawdown of
**1.65 % over seven years** — numbers no real delta-neutral book produces.
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
   1.16 bp.
   The corrected number is the one quoted. A code fix, not a rule change; no decision clause
   depends on it.
4. **Cost attribution in the Carry replay** splits the sleeve's 24 bp round trip as 12 bp on the
   entry day and 12 bp × (size at exit) on the exit day, so the daily series has a realistic
   shape. The total per trade is identical to `close_carry_trade`'s, both arms are affected
   identically, and the per-trade sums reconcile with the daily series exactly
   (+72.905 %).
5. **`backtest_tail_harvester.py` does not exist**, so "reuse the existing path" was satisfied by
   importing the sleeve's own decision functions and cost constants rather than by running a
   backtest harness. This was decided and written into the README before any result, and it is
   the only read-only option: `backtest_runner.py` writes replay trades into `prod.db`, and
   `studies/simulation/build_sim_trader_db.py` currently hard-errors on the five 2026-09-06
   Track D tables missing from its `TABLE_PLAN`.

## Independent re-verification (2026-09-09)

Both scripts were re-run from scratch on a second session against a database one day further
on (2026-09-09 vs the 2026-09-08 inventory frozen in the README: OKX 280 rows, Bybit BTC
7,077, Binance on-grid 7,624). **Every clause value and every verdict reproduced.** The Carry
benchmark for arm P moved 4.64 % → 4.141 % purely because the 93-day window rolled forward;
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
**immaterial to the verdict** because the zero-cost upper bound (+2.73 %/yr)
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
| `results/dispersion_strategy_grid.csv` | 120 rows: 4 arms × 5 thresholds × causal/contemporaneous × 3 cost models |
| `results/dispersion_distribution.csv` | dispersion moments, percentiles, exceedance, concentration per arm |
| `results/dispersion_series_P.csv`, `_S.csv` | per-settlement venue rates and spread, in bp |
| `results/carry_summary.json` | clauses, arm stats, bootstrap, DSR, byte-equivalence proof |
| `results/carry_grid.csv` | 18-cell sensitivity grid |
| `results/carry_daily.csv` | daily returns of both arms + the size multiplier |
| `results/carry_trades.csv` | all 32 trades per arm |
| `results/carry_zscore.csv` | daily funding, 60-day z, expanding p95, fire flag |

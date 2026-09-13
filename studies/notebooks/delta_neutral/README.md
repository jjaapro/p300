# Delta-neutral funding studies — pre-registration (2026-09-08, before any outcome number)

Two funding studies share this folder because they share a data spine (perp funding
settlements on the 8-hourly grid) and a benchmark (what the existing S-078 Carry bot
already earns). They are otherwise independent and each carries its own decision rule.

* **Sub-study (i)** — cross-venue funding **dispersion**: is the spread between venues'
  funding rates on the same asset harvestable by a venue-pair delta-neutral perp trade?
* **Sub-study (iii)** — carry-crash **sizing**: does halving the Carry sleeve's size when
  the trailing 60-day funding z-score is extreme improve Carry's Sharpe and MAR?

Nothing under `strategies/**` or `bots/**` is modified. `prod.db` is opened
`mode=ro` throughout. Any recommendation to change `bots/carry/config.py` is a
recommendation only.

---

## 0. Data inventory (verified read-only 2026-09-08, before pre-registration was frozen)

Row counts and spans below were measured first, because the *length of the venue history*
is the binding design constraint on sub-study (i) and it must be pre-registered honestly
rather than discovered afterwards. These are inputs, not outcomes.

| Table | Key | Rows | First settlement (UTC) | Last (UTC) | Off-grid rows (`ts % 28800 != 0`) |
|---|---|---|---|---|---|
| `cd_funding_rate` (Binance BTC) | `timestamp` | 57,845 total / **7,622 on-grid** | 2019-09-25 08:00 | 2026-09-08 16:00 | 50,223 (pre-cutover hourly bars) |
| `cd_funding_rate_eth` (Binance ETH) | `timestamp` | 7,329 (all on-grid) | 2020-01-01 00:00 | 2026-09-08 16:00 | 0 |
| `okx_funding` `BTC-USDT-SWAP` | `(inst_id, timestamp)` | **278** | 2026-06-08 08:00 | 2026-09-08 16:00 | 0 |
| `okx_funding` `ETH-USDT-SWAP` | `(inst_id, timestamp)` | **278** | 2026-06-08 08:00 | 2026-09-08 16:00 | 0 |
| `bybit_funding` `BTCUSDT` | `(symbol, timestamp)` | **7,075** | 2020-03-25 16:00 | 2026-09-08 16:00 | 0 |
| `bybit_funding` `ETHUSDT` | `(symbol, timestamp)` | **6,446** | 2020-10-21 08:00 | 2026-09-08 16:00 | 0 |

Realised joins on the settlement grid (measured):

| Join | Settlements |
|---|---|
| Binance ∩ Bybit, BTC | 7,075 |
| Binance ∩ Bybit, ETH | 6,446 |
| Binance ∩ OKX ∩ Bybit, BTC | **278** |
| Binance ∩ OKX ∩ Bybit, ETH | **278** |

**The three-venue sample is 278 settlements = 92.3 days.** OKX serves only a rolling
~92-day window and no seed exists anywhere in the repo or the predecessor DB. This is
pre-registered as a hard power limit, handled by clause **I-0** below.

Price tables available for the basis-leakage diagnostic:
`cd_futures_ohlcv` (Binance BTC perp 1h, 2019-09-08 → 2026-09-08),
`okx_perp_1h` (2021-01-01 → 2026-09-08),
`bybit_perp_1h` (2021-01-01 → **2026-05-26 — feed stale, does not cover the OKX window**),
`okx_perp_eth_1h`, `bybit_perp_eth_1h` (same staleness), `cd_futures_eth_15m`.

### Cadence handling (2026-04-13 / 2026-04-25 cutover)

`strategies/support/funding.py` is the repo's single source of truth and filters
`WHERE timestamp % 28800 = 0` so each settlement is counted exactly once regardless of
whether the underlying rows are pre-cutover hourly bars or post-cutover 8-hourly
settlements. This study uses **the same filter and the same aggregation**, and asserts
byte-equivalence of its own daily series against `funding.daily_sums_pct("BTC", …)` on the
full overlap before any result is computed (memory: *verify byte-equivalence when
porting*). A mismatch on any date aborts the run.

Known residual: on the pre-cutover Binance rows the on-grid value is the *predicted*-rate
hourly bar stamped at the settlement hour, not an independently recorded realised
settlement. Bybit and OKX rows are realised rates. Sub-study (i)'s long-history arm
therefore compares a predicted-rate proxy against realised rates; this is recorded as a
limitation, not corrected, because correcting it would require a data source the repo
does not have.

---

## 1. SUB-STUDY (i) — cross-venue funding dispersion

### Hypothesis

Perp funding rates for the same asset differ across venues because each venue computes
funding from its own order book and its own premium-index/interest-rate formula and clamp.
If the dispersion is persistent (predictable from one settlement to the next) rather than
pure noise, a delta-neutral pair — long the perp on the venue paying the lowest (most
negative) funding, short the perp on the venue paying the highest — earns the spread for
zero net delta.

### Frozen rules

1. **Grid.** Decisions and settlements both live on the 8-hourly UTC grid
   (`timestamp % 28800 == 0`): 00:00, 08:00, 16:00.
2. **Venues.** Binance (`cd_funding_rate` BTC, `cd_funding_rate_eth` ETH),
   OKX (`okx_funding`, `BTC-USDT-SWAP` / `ETH-USDT-SWAP`),
   Bybit (`bybit_funding`, `BTCUSDT` / `ETHUSDT`). A settlement is usable only when every
   venue in the arm has a non-NULL rate at that exact timestamp.
3. **Signal.** At settlement `t`, `spread_t = max_v(rate_{v,t}) − min_v(rate_{v,t})`
   in decimal rate units. The position is ON for the interval `(t, t+1]` iff
   `spread_t > 3 bp = 0.0003`.
4. **Legs.** Long the perp at `argmin_v rate_{v,t}`, short the perp at `argmax_v rate_{v,t}`,
   equal and opposite notional `N` (so net delta ≈ 0 by construction).
5. **Causality — the decisive design choice.** The rate stamped at `t` is the rate that has
   *just settled*. A position opened after settlement `t` collects the rate stamped at
   `t+1`, which is unknown at `t`. The pre-registered, tradeable P&L is therefore

   `gross_{t→t+1} = rate_{H(t), t+1} − rate_{L(t), t+1}`

   where `H(t)`, `L(t)` are the venues chosen from the **`t`** cross-section. The
   contemporaneous version (`spread_t` itself) is reported **only** as a clearly labelled
   non-tradeable upper bound; it is not eligible to satisfy any decision clause.
6. **Costs.** Four legs at 5 bp each = **20 bp of notional per full open-and-close cycle**.
   Charging assumption, pre-registered: **the 20 bp is charged when the held leg-pair
   changes.** Concretely, holding the identity of `(L, H)` unchanged from one settlement to
   the next requires no fills, so no cost; a change of either leg, an OFF→ON transition, or
   an ON→OFF transition costs 20 bp once. Rationale: charging 20 bp every settlement would
   assume the book is torn down and rebuilt even when the optimal pair is identical, which
   is not how the position would be run. Two sensitivities are reported alongside:
   **(a) 20 bp every ON settlement** (pessimistic rotation) and **(b) 0 bp** (upper bound).
   The realised rotation rate (fraction of ON settlements whose leg-pair differs from the
   previous ON settlement) is reported so the reader can price any other assumption.
7. **Annualisation.** 3 settlements/day × 365 = **1095 settlements/year**. Net annualised
   return = `mean(net per-settlement return on notional N) × 1095`, additive (the position
   is notional-constant, not compounded).
8. **Basis leakage (assumption + measurement).** The strategy is treated as delta-neutral,
   i.e. the price P&L of long-perp-on-L versus short-perp-on-H is assumed zero. This is an
   idealisation: the two perps are different contracts and their prices diverge. It is
   measured, not assumed away — the study reports the distribution of
   `(P_H/P_L)` log-change over each 8-hour holding interval for the venue pairs where price
   data exists in the window, and states the resulting P&L standard deviation in bp so it
   can be compared against the funding spread being harvested. Because `bybit_perp_1h` is
   stale from 2026-05-26 this diagnostic is available for Binance↔OKX inside the 3-venue
   window and for Binance↔Bybit before 2026-05-26 only.

### Arms

| Arm | Venues | Asset | Settlements available |
|---|---|---|---|
| **P** (primary, exactly as specified) | Binance + OKX + Bybit | BTC | 278 |
| **P-ETH** | Binance + OKX + Bybit | ETH | 278 |
| **S** (secondary, power extension) | Binance + Bybit | BTC | 7,075 |
| **S-ETH** | Binance + Bybit | ETH | 6,446 |

Arm S applies the identical rule to a smaller venue set over the full common history. It
is a *different* hypothesis (2-venue dispersion, not 3-venue) and is declared as such; it
exists because arm P cannot carry statistical weight.

### Decision clauses (frozen)

| # | Clause | Fires ⇒ |
|---|---|---|
| **I-0** | Arm P has fewer than 365 usable settlements (≈ 1 year). | Arm P is declared **UNDERPOWERED**; its I-1/I-2 numbers are still reported but cannot on their own produce a BUILD verdict. |
| **I-1** | Net annualised return of the arm (causal, pre-registered cost model) **< 5 %**. | KILL for that arm. |
| **I-2** | Net annualised return of the arm **< the realised Carry benchmark** over the same window. | KILL for that arm. |
| **I-3** | 90 % iid-bootstrap CI of the arm's mean per-settlement net return **includes 0**. | Result not distinguishable from zero; supports KILL. |
| **I-4** | Deflated Sharpe (DSR) at `n_trials = 20` **< 0.95**. | Fails multiple-testing deflation; supports KILL. |

**Verdict rule for sub-study (i)** (frozen): the sub-study's verdict is taken from the
adequately-powered arm. If I-0 fires (arm P underpowered) and arm S is adequately powered
(≥ 365 settlements), the sub-study verdict is arm S's verdict, with arm P reported as
**INCONCLUSIVE on power** and its numbers quoted. If any KILL clause fires on arm S,
verdict = **KILL**. If no KILL clause fires on arm S but arm P is underpowered, the verdict
is **INCONCLUSIVE — arm S positive, 3-venue rule unproven**, which is explicitly *not* a
build recommendation.

### Dispersion description (reported regardless of verdict, not a decision clause)

* Full distribution of `spread_t` per arm: mean, sd, and the 50/75/90/95/99/99.9th
  percentiles, in bp.
* **Exceedance frequency**: `P(spread_t > 3 bp)`, plus the same at 1, 2, 5 and 10 bp.
* **Concentration**: the share of all exceedance settlements falling in the top 5 calendar
  days by exceedance count, and the Gini-style top-5 %-of-days share. Pre-registered label:
  the exceedance is called **stress-concentrated** if ≥ 50 % of exceedances fall inside
  ≤ 5 % of the calendar days in the window.
* Which venue sits at the max and at the min, and how often the identity flips.

### Realised-Carry benchmark (for clause I-2)

Two candidates exist and the choice is pre-registered here, before it is computed:

* The **paper ledger** for `bot_carry_v1` contains exactly **one** CARRY trade, `SJ-4242`,
  opened 2026-07-22 and **still open** (verified read-only). There is no closed trade in the
  bot's own ledger, so the ledger cannot yield a realised return over any window. The only
  closed CARRY trade in the whole DB is the legacy `SJ-3452` (`p300_aggressive_v2_v1_0`,
  2026-05-14 → 2026-07-22, +0.617 % net) which predates the bot.
* Therefore the benchmark is computed from **funding, via the Carry sleeve's own accounting**
  (`strategies.support.funding` sums + `strategies/trades.py::close_carry_trade` cost model),
  replayed by sub-study (iii)'s baseline. This is the same object the bot would have earned
  had it been running the whole window, and it is the honest comparator.

Both numbers are reported: (a) the sub-study (iii) baseline replay's net annualised return
restricted to the arm's window, and (b) the raw annualised Binance funding sum over that
window with no entry filter and no costs (a strictly easier bar). **Clause I-2 is evaluated
against (a)**, the sleeve-faithful figure; (b) is context.

### Trial count for deflation

Threshold ∈ {1, 2, 3, 5, 10} bp × asset ∈ {BTC, ETH} × venue set ∈ {3-venue, 2-venue}
= **20 trials**. DSR is reported at `n_trials` = 1 and 20; clause I-4 uses 20.

### Priors (stated before running)

* Funding dispersion across major venues is a well-known basis trade with real
  practitioner participation, so the *persistent* component should already be arbitraged to
  near the cost floor. 20 bp round trip against a 3 bp/settlement signal means the trade
  must hold ≥ 7 settlements (≈ 2.3 days) on an unchanged leg-pair just to break even on
  fills. My prior is that leg identity is *not* that persistent and the strategy loses on
  rotation cost.
* I expect `spread_t` to be small and heavily right-tailed: median well under 1 bp,
  exceedances of 3 bp concentrated in funding-clamp episodes (one venue hitting its cap
  while another does not) — i.e. stress-concentrated.
* I expect the contemporaneous (non-tradeable) version to look attractive and the causal
  version to be near zero — the gap between them is the point of the study.
* I expect the basis-leakage sd per 8h interval to be **larger** than the funding spread
  being harvested, which would make "delta-neutral" a poor description of the trade.
* Expected verdict: **KILL** on arm S, **INCONCLUSIVE on power** on arm P.

---

## 2. SUB-STUDY (iii) — carry-crash sizing

### Hypothesis

Carry crashes cluster after funding gets extreme: an unusually high funding rate marks
crowded leveraged longs, and the unwind that follows both collapses funding and (for a
spot-long/perp-short carry book) is where the basis risk actually bites. Cutting the Carry
sleeve's position size by 50 % when the trailing 60-day funding z-score is above its 95th
percentile should therefore raise risk-adjusted return even though it gives up gross carry.

### How Carry is replayed (existing path, not a re-implementation)

There is no `studies/` carry study and the calibration source named in
`bots/carry/strategy/README.md` (`backtest_tail_harvester.py`) **no longer exists in
the repo** (verified: no file, no reference outside the two doc strings). The two live
replay paths are `backtest_runner.py` and `studies/simulation/sim.py`; both **write** trades
into a database (`backtest_runner.py` writes replay rows straight into `prod.db`), which the
standing read-only rule forbids, and `studies/simulation/build_sim_trader_db.py` currently
hard-errors on five 2026-09-06 Track D tables that were never added to its `TABLE_PLAN`.

*(2026-09-13: all three of those files were deleted with the legacy orchestrator path. The
argument above is left as written because its conclusion is unchanged and now unconditional
— there is no replay harness to weigh against, so driving the sleeve's own decision code
under a frozen clock is simply the way. `studies/notebooks/pdo_adjacents/parity_check.py`
is the worked example.)*

The pre-registered path is therefore: **import and drive the sleeve's own decision code**,
with the P&L computed by the sleeve's own accounting constants, and *nothing re-implemented*:

* Day set and daily funding series: the exact aggregation of
  `strategies.support.funding.daily_sums_pct` (`ts % 28800 == 0`, `SUM(fr_close)` per UTC
  date, complete days only = 3 settlements, ×100 → percent), read `mode=ro`, and asserted
  byte-equal to the live function's output over the whole overlap before anything is scored.
* Signal state: `bots.carry.strategy.signal._evaluate_today` and `._rolling_avg`,
  imported and called directly, fed an expanding record window exactly as
  `_load_recent_daily_funding` builds it (days strictly before the decision day; spot and
  perp closes joined from `cd_spot_binance` / `cd_futures_ohlcv` so the day-set intersection
  matches the live function).
* State machine: the order in `signal.try_decide_for_variant` — exit sweep first (close all
  open carry when `exit_trigger`), then entry if flat ∧ `entry_ok` ∧ ¬`exit_trigger` ∧ no
  action already taken that day.
* P&L: `strategies/trades.py::close_carry_trade`'s formula,
  `net_pct = accrued_funding_pct(SHORT) − CARRY_COST_PCT(0.20) − CARRY_SLIPPAGE_PCT(0.04)`
  = funding minus **24 bp** round trip. Sizing per `bots/carry/config.py`:
  `CARRY_NOTIONAL_X = 1.0` on `CAPITAL_USDT = 10,000`, so notional = capital and
  `daily return on capital = daily funding %`.
* Decision clock: one decision per UTC day at 00:00, using complete days through D−1
  (the earliest instant at which the live sleeve, which excludes "today", could act on day
  D). Funding accrues on settlements in `(entry_ts, exit_ts]`, matching `accrued_pct`.

**Cost convention used here: the production one (10 bp fee + 5 bp slippage equivalent —
concretely CARRY's 20 bp fee + 4 bp slippage), not the 18 bp research default**, because
the object under test is the live sleeve's own P&L and the sleeve's constants are the
correct ones.

### Frozen rule under test

Let `f_d` = daily funding in percent (sum of the day's 3 settlements) on day `d`.

1. `mu_d`, `sd_d` = mean and sample sd of `f` over the trailing **60 days** ending at `d`
   (inclusive). `z_d = (f_d − mu_d) / sd_d`, undefined when `sd_d == 0` or fewer than 60
   observations exist.
2. `P95_d` = the **95th percentile of `{z_s : s ≤ d}`** — an expanding, strictly causal
   percentile. It is only defined once at least **252** `z` values exist (one trading year
   of warm-up); before that the rule never fires.
3. On decision day `D`, the rule **fires** iff `z_{D−1} > P95_{D−1}` (only D−1 information
   is used, matching the sleeve's own exclusion of "today").
4. When the rule fires on a day the position is open, that day's funding accrual is
   collected on **50 %** of base notional. When it does not, 100 %.
5. **Resize cost**: each transition of the size multiplier (1.0 → 0.5 or 0.5 → 1.0) trades
   half the notional one way. One-way synthetic cost is half the 24 bp round trip = 12 bp,
   applied to the 50 % being traded ⇒ **6 bp of base notional per transition**. A
   zero-resize-cost variant is reported as a sensitivity.
6. The rule changes size only. Entry and exit dates are unchanged — the sleeve's 7-day
   average > 0 entry and 3-day negative-streak exit are untouched, so both arms hold
   identical positions on identical days and differ only in size.

### Metrics

Daily net return on capital (percent), for the with-rule and without-rule arms:

* **Sharpe** = `metrics.daily_sharpe(daily_returns, periods_per_year=365)`.
* **MAR** = annualised return / max drawdown, with annualised return = `mean_daily × 365`
  and max drawdown = `metrics.max_drawdown(daily_returns)` (additive equity walk, the
  toolkit convention).
* Bootstrap: `bootstrap.block_bootstrap_sharpe(block=20, n_iter=5000, seed=42)` on each
  arm, and a paired block bootstrap of the **difference series** (`with − without`, same
  resampled index for both arms) for the CI on the uplift.
* `dsr_pbo.dsr_from_returns(daily, n_trials, periods_per_year=365)` at `n_trials` = 1, 9
  and 18.

### Decision clauses (frozen)

| # | Clause | Fires ⇒ |
|---|---|---|
| **III-0** | The rule fires on fewer than **30** distinct days that overlap an open Carry position. | **INCONCLUSIVE on power** for sub-study (iii); the clause is named in the verdict. |
| **III-1** | Sharpe uplift (`Sharpe_with − Sharpe_without`, annualised) **< 0.10**. | KILL. |
| **III-2** | MAR is **not improved** (`MAR_with ≤ MAR_without`). | KILL. |
| **III-3** | 90 % paired block-bootstrap CI of the Sharpe difference **includes 0**. | Uplift not distinguishable from noise; supports KILL. |
| **III-4** | DSR of the with-rule daily series at `n_trials = 9` **< 0.95**. | Supports KILL (reported for both arms). |

**Verdict rule for sub-study (iii)**: KILL if III-1 or III-2 fires; INCONCLUSIVE if III-0
fires; otherwise BUILD-CANDIDATE (a recommendation to change `bots/carry/config.py` sizing,
never an edit).

### Trial count for deflation

Window ∈ {30, 60, 90} days × percentile ∈ {90, 95, 99} = **9 trials** (the cut fraction is
fixed at 50 % by the task). The resize-cost on/off pair is a sensitivity on the same rule,
not a separate search, but DSR is also reported at `n_trials = 18` so the reader can take
the stricter reading. The 3×3 grid is run and reported in full so the primary cell can be
seen in context; the primary cell (60 d, p95) is the only one that decides.

### Priors (stated before running)

* The Carry sleeve's P&L is *funding minus a constant*. Its daily return series is almost
  entirely funding itself. Cutting size when funding is high therefore removes the
  **highest-earning days**, which mechanically cuts the mean. For the Sharpe to rise, the
  volatility reduction must outweigh that — and since carry's return volatility comes from
  funding volatility, halving on high-|z| days does cut vol. My prior is that Sharpe moves
  by less than ±0.1 and that the mean falls, so III-1 fires.
* MAR: drawdowns in this P&L model come from *negative* funding days, which are low-`z`,
  not high-`z`. A rule keyed to the **upper** tail should not touch the drawdown source at
  all, so I expect max drawdown roughly unchanged and annualised return lower ⇒ MAR worse
  ⇒ III-2 fires too.
* The genuine carry-crash mechanism (basis blow-out on the spot-vs-perp legs during an
  unwind) is **not in this P&L model at all** — the sleeve assumes price P&L is exactly
  zero. If the rule has value, this replay structurally cannot see it. That is the single
  most important caveat and it is pre-registered here, not discovered afterwards.
* Expected verdict: **KILL**, with the caveat above stated prominently.

---

## 3. Artefacts

* `dispersion.py` — sub-study (i): dispersion distribution, causal + contemporaneous
  strategy replay across all arms and thresholds, cost sensitivities, basis-leakage
  diagnostic, bootstrap + DSR. Writes `results/dispersion_*.{csv,json}`.
* `carry_sizing.py` — sub-study (iii): byte-equivalence assertion, sleeve-driven Carry
  replay, with/without rule, 3×3 sensitivity grid, bootstrap + DSR. Writes
  `results/carry_*.{csv,json}`.
* `results/` — every number quoted in `findings.md`.
* `findings.md` — STATUS line stating both verdicts, one clause table per sub-study.
* `build_notebook.py` — viewer notebook, run with `C:/Python/Python313/python.exe`.

Both scripts are deterministic (fixed seeds), re-runnable, and open `prod.db` `mode=ro`.

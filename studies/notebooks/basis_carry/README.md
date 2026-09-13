# S-DN(ii) — Cash-and-carry basis: perp vs Binance quarterly future

**PRE-REGISTRATION. Written and saved 2026-09-08 BEFORE any outcome number was computed.**
Nothing below was edited after results existed. Deviations, if any, are recorded in
`findings.md` with the original clause left visible here.

## 0. Why this folder is separate from `studies/notebooks/delta_neutral/`

The research roadmap files this study as **S-DN(ii)**, alongside the two funding
delta-neutral sub-studies. Those two — **(i)** cross-venue funding dispersion and
**(iii)** carry-crash sizing — are owned by another agent and live in
`studies/notebooks/delta_neutral/`. They share a data spine (the 8-hourly funding
settlement grid) that this study only *consumes*; this study's spine is instead the
new `binance_quarterly_1h` term-structure feed. To avoid two agents writing the same
folder, S-DN(ii) is here. `findings.md` cross-references both directions.

## 1. Hypothesis

When the Binance USDⓈ-M **quarterly delivery future** trades rich to the **perpetual**,
a quantity-neutral pair — short the quarterly, long the perp — held to the quarterly's
delivery captures the convergence of that spread to zero, because a delivery future
must settle to the index at 08:00 UTC on its delivery date while the perp tracks the
index continuously. The spread is a *contractual* convergence, not a statistical one.

The reason this may nonetheless be worthless in practice: a positive basis is the
same market state as positive perp funding. The long-perp leg pays funding for the
whole hold. **The null hypothesis of this study is that funding + fees eat the entire
convergence**, i.e. the trade is a well-known structural arbitrage that is already
priced so that the residual, after the funding you must pay to hold the hedge, is not
worth a new venue integration.

Symmetrically, when the quarterly trades **cheap** to the perp (backwardation) the
reverse pair — long quarterly, short perp — captures convergence *and* receives
funding when funding is positive. This is the leg with the better prior.

## 2. Execution reality (stated up front, not a result)

**p300 has no delivery-futures adapter.** `strategies/` and `bots/` trade the Binance
USDⓈ-M perpetual only. `binance_quarterly_1h` is a *data* feed shipped 2026-09-08
(feed D5); there is no order path, no position accounting for a contract that expires,
no roll logic, and no margin model for a two-instrument delivery pair. Acting on a
positive result here would be a **new venue integration**, not a new sleeve on the
existing rails. That raises the bar: the economics must justify building an order
path, not merely be positive.

This study's only job is to answer whether the economics clear that bar. It creates
**no sleeve and no bot** regardless of outcome (research workflow rule).

## 3. Data (verified read-only 2026-09-08, before this file was frozen)

`prod.db` opened `mode=ro` throughout. Row counts and spans measured, not assumed:

| Table | Key | Rows | First (UTC) | Last (UTC) |
|---|---|---|---|---|
| `binance_quarterly_1h` BTCUSDT CURRENT_QUARTER | (pair, contract_type, timestamp) | 49,042 | 2021-02-03 08:00 | 2026-09-08 17:00 |
| `binance_quarterly_1h` BTCUSDT NEXT_QUARTER | ″ | 40,621 | 2021-03-16 07:00 | 2026-09-08 17:00 |
| `binance_quarterly_1h` ETHUSDT CURRENT_QUARTER | ″ | 49,019 | 2021-02-04 07:00 | 2026-09-08 17:00 |
| `binance_quarterly_1h` ETHUSDT NEXT_QUARTER | ″ | 40,621 | 2021-03-16 07:00 | 2026-09-08 17:00 |
| `binance_quarterly_contracts` | symbol | **4** | onboard 2026-03-27 08:00 | delivery 2026-12-25 08:00 |
| `cd_futures_ohlcv` (BTC perp 1h) | timestamp | 61,369 | 2019-09-08 17:00 | 2026-09-08 17:00 |
| `cd_futures_eth_15m` (ETH perp 15m) | timestamp | 199,367 | 2021-01-01 00:00 | 2026-09-08 17:30 |
| `cd_funding_rate` (BTC) | timestamp | 57,845 | 2019-09-25 08:00 | 2026-09-08 16:00 |
| `cd_funding_rate_eth` (ETH) | timestamp | 7,329 | 2020-01-01 00:00 | 2026-09-08 16:00 |

Known structural limitations, from the D5 feed agent's handover and re-verified here:

* `binance_quarterly_contracts` holds **only currently-listed contracts** (Binance
  `exchangeInfo` drops delivered ones). It gives **4 ground-truth timestamps**
  and no history. The historical roll calendar is therefore *derived*, see §4.
* Both `NEXT_QUARTER` series have **structural listing holes** 2022-04-18→2022-06-20,
  2022-10-13→2022-12-23, 2022-12-30→2023-03-27, 2023-04-14→2023-06-25,
  2023-07-31→2023-08-18 (Binance listing decisions, recorded in
  `data/known_unfillable.json`). These are **structurally absent, never interpolated**.
  Both `CURRENT_QUARTER` series are gapless.
* BTC funding is stamped **hourly** before the 2026-04-13 cadence change and only at
  **8-hourly settlements** after it. ETH funding is 8-hourly throughout.

## 4. Frozen rules

### 4a. Delivery calendar (derived, then gated)

Binance USDⓈ-M quarterly contracts deliver at **08:00 UTC on the last Friday of
March / June / September / December**. The calendar is generated by that rule for
2021Q1..2026Q4. It is **not accepted on faith** — see clause **D1** and **D2**.

The rule's four generated 2026 timestamps (2026-03-27, 2026-06-26, 2026-09-25,
2026-12-25, all 08:00 UTC) are checked against the four rows actually in
`binance_quarterly_contracts` (two `delivery_ts`, two `onboard_ts`; a contract is
onboarded as NEXT_QUARTER at the previous delivery). This is a calendar fact about
the table's contents, computed before this file was frozen: **all four match**.
That is 4 anchors, not a validated 24-quarter history, hence clause D2.

* For `CURRENT_QUARTER` at hour *t*: delivery = the first calendar delivery ≥ *t*.
* For `NEXT_QUARTER` at hour *t*: delivery = the **second** calendar delivery ≥ *t*.

### 4b. Basis

```
basis(t)      = (quarterly_close(t) - perp_close(t)) / perp_close(t)
dtd(t)        = (delivery_ts - t) / 86400            # days to delivery, float
basis_ann(t)  = basis(t) * 365 / dtd(t)
```

Both closes are the 1h close at the same hour stamp. ETH perp hourly close is built
from `cd_futures_eth_15m`; the open-vs-close stamping convention is resolved by
reconstructing BTC's hourly close from `cd_futures_15m` and requiring an exact match
against `cd_futures_ohlcv` — see clause **D3**.

### 4c. Entry / exit

* Candidate hours require `dtd >= 7.0` days. Below 7 days the `365/dtd`
  annualisation amplifies a spread that is mostly microstructure; frozen at 7.
* **Entry**: the **first** hour of each (asset, contract slot, contract cycle) at
  which `basis_ann >= +0.08`. Direction: **short quarterly, long perp**, quantity
  neutral (1 unit of the underlying on each leg).
* **Reverse entry**: the first hour at which `basis_ann <= -0.08`. Direction:
  **long quarterly, short perp**.
* At most **one** position per (asset, slot, contract cycle). Whichever trigger fires
  first in a cycle claims it; no re-entry after exit within the same cycle.
* **Exit**: the last hourly bar strictly before `delivery_ts`, i.e. 07:00 UTC on the
  delivery date. We do **not** mark the settlement print; whatever basis remains at
  07:00 is left uncaptured, which is the conservative direction.
* A cycle whose exit bar is not in the data (the live 2026Q3 cycle) is **excluded**;
  no trade is counted that has not actually converged.

### 4d. P&L, per unit of entry perp notional `P0`

With `Q0,P0` the entry closes and `Q1,P1` the exit closes, quantity-neutral, sign
`s = +1` for the short-quarterly/long-perp direction and `s = -1` for the reverse:

```
gross    = s * ((Q0 - P0) - (Q1 - P1)) / P0
fees     = 0.0005 * (Q0 + P0 + Q1 + P1) / P0            # 5bp per leg per side
funding  = -s * sum_over_settlements( rate_k * P_k ) / P0
net      = gross - fees + funding
```

* `fees` is exactly 5 bp per leg per side (≈ 20 bp round trip), charged on the true
  notional of each of the four fills rather than an approximation.
* `funding` uses the **realised** Binance settlement rates at 00:00 / 08:00 / 16:00
  UTC, marked at the perp close of that hour, summed over settlements in
  `(entry_ts, exit_ts]`. Source `cd_funding_rate` (BTC) / `cd_funding_rate_eth` (ETH),
  the same 3-settlements-per-day convention `bots/carry/strategy` uses. The
  long-perp leg **pays** positive funding; the short-perp leg **receives** it.
* Per-trade annualised: `net_ann = net * 365 / hold_days`.
* If any settlement inside the window is missing from the funding table the trade is
  flagged `funding_incomplete` and the count is reported; such trades are **kept**
  (dropping them would bias toward the cheap-funding periods) and the missing
  settlement is charged as zero, which flatters the trade — reported as a bound.

### 4e. Primary decision statistic

The strategy is a **single capital slot**: capital is deployed only while a trade is
on. The decision number is the time-weighted annualised return on deployed capital,
pooled across BTC and ETH on the primary `CURRENT_QUARTER` slot:

```
R_ann = 365 * sum_i(net_i) / sum_i(hold_days_i)
```

Secondary, reported but not decisive: the simple mean of `net_ann_i`.

### 4f. Cost convention

**5 bp per leg per side = 20 bp per completed round trip**, as the task specifies for
this trade, *plus* realised funding. This is the task's stated convention and it is
close to production `strategies/trades.py` (10 bp fee + 5 bp slippage per side on one
leg); it is *cheaper* than the 18 bp/side research default, and that is deliberate —
delivery futures on Binance are maker-quotable and the whole trade is a resting
spread. Charging less than the research default makes the KILL harder to reach, i.e.
it is the direction that favours the hypothesis.

## 5. Decision clauses

Data-validity gates first. **If D1, D2 or D3 fails, the verdict is INCONCLUSIVE
(data) and the named clause is reported — the economics are not evaluated.**

| # | Clause | Fires when |
|---|---|---|
| **D1** | The rule-generated 2026 delivery calendar reproduces all 4 timestamps in `binance_quarterly_contracts` | < 4 of 4 match → INCONCLUSIVE (data) |
| **D2** | Roll dates are empirically confirmed in the price data: for each rule-generated delivery, the `CURRENT_QUARTER` basis series shows its largest jump of the surrounding ±72h window within ±2h of the rule timestamp | < 90% of in-sample rolls confirmed on either asset → INCONCLUSIVE (data) |
| **D3** | The ETH hourly perp close is reconstructible: the same 15m→1h rule applied to BTC's `cd_futures_15m` reproduces `cd_futures_ohlcv.close` exactly on ≥ 99.9% of overlapping hours | below that → INCONCLUSIVE (data) |
| **S1** | Sample sufficiency: the pre-registered 8% trigger produces at least **5** completed trades pooled across BTC+ETH CURRENT_QUARTER | fewer than 5 → INCONCLUSIVE (sample), no KILL/BUILD claim |
| **K1** | **PRIMARY KILL.** `R_ann` (§4e) after funding and fees **< 5%** | fires → KILL |
| **K2** | Robustness: the 95% percentile-bootstrap lower bound on `R_ann` (10,000 resamples of the completed trades, seed 42) is **< 5%** | fires → the point estimate is not defensible even if K1 misses |
| **K3** | Multiple-testing: the Deflated Sharpe Ratio of the per-trade net returns at `n_trials = 12` is **< 0.95** | fires → the Sharpe does not survive the trial count |

**BUILD** requires D1–D3 pass, S1 pass, and **none** of K1/K2/K3 fire.
**KILL** is returned when D1–D3 and S1 pass and K1 fires.
If K1 misses but K2 or K3 fires, the verdict is **KILL (not robust)** — a positive
point estimate that fails its own robustness clause does not justify a new venue
integration. This asymmetry is deliberate and is fixed here, before the numbers.

### Trial count for deflation

`n_trials = 12` = 3 triggers (8% / 6% / 5%) × 2 assets × 2 contract slots
(CURRENT_QUARTER / NEXT_QUARTER). Every number this study computes is inside that
grid, so 12 bounds the search, and the pre-registered rule is exactly one of the 12.

## 6. Reported for context only — NOT a selection menu

The task requires the term structure be described and the 5% and 6% triggers be
reported. These are **context, not candidates**. The decision is made on the 8%
trigger on `CURRENT_QUARTER` and on nothing else. If a lower trigger looks better,
that will be stated as an observation with its trial cost, and the verdict will
still be the 8% verdict. Specifically reported:

1. Distribution of `basis_ann` by calendar year and asset (mean, median, sd, p05,
   p95, fraction of hours ≥ 8%, ≥ 6%, ≥ 5%, ≤ −8%).
2. Trigger frequency: how many contract cycles out of the available ones fire.
3. The full trade table and `R_ann` at the 5% and 6% triggers.
4. The `NEXT_QUARTER` slot, with the 2022-04→2023-08 listing holes marked absent.
5. A decomposition of every trade into gross convergence, fees, funding — because
   the whole question is whether funding eats the convergence.

## 7. Priors (stated before the numbers)

* P(the 8% trigger fires at all on BTC CURRENT_QUARTER since 2021): ~0.9. 2021 had a
  famously rich term structure; 2024's ETF period had episodes too.
* P(gross convergence is positive on average): ~0.95. It is close to contractual.
* P(net of funding and fees clears 5% annualised on the pre-registered rule): **~0.2.**
  The literature and every practitioner account say the perp-vs-quarterly spread is
  the most heavily arbitraged relationship in crypto, and that the funding you pay on
  the long perp leg is the same signal that made the basis rich in the first place.
* P(the reverse/backwardation leg is the better one): ~0.6, but it will be rare.
* P(verdict is INCONCLUSIVE on a data clause): ~0.15, concentrated in D2 — if the
  continuous-series roll is not detectable, the annualisation denominator is unsafe.
* Expected outcome: **KILL**, with a gross convergence that is real and a net that is
  not worth a venue integration.

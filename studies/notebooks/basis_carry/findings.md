STATUS: INCONCLUSIVE — pre-registered data gate D3 fired (15m→1h exact-match 98.80% vs 99.9% required), so the literal pre-registration returns INCONCLUSIVE (data); the economics were computed anyway as a recorded deviation and are KILL by a wide margin (R_ann −7.14%/yr, bootstrap 95% CI [−12.22%, −2.58%], DSR 2.0e−08), and the D3 failure is shown below to be numerically incapable of changing that.

# S-DN(ii) — Cash-and-carry basis: Binance perp vs quarterly future

Pre-registration: [`README.md`](README.md), frozen **2026-09-08** before any outcome
number existed. Analysis completed **2026-09-09**. Nothing in `README.md` was edited
after results existed; the deviation below is recorded here, not there.

Sibling studies **S-DN(i)** (cross-venue funding dispersion) and **S-DN(iii)**
(carry-crash sizing) live in [`studies/notebooks/delta_neutral/`](../delta_neutral/)
and are owned by another agent. This folder is split off so two agents do not write
the same directory. The two share the 8-hourly funding settlement grid
(`cd_funding_rate` / `cd_funding_rate_eth`, `timestamp % 28800 == 0`, the
`strategies.support.funding` convention); this study consumes it and adds the
`binance_quarterly_1h` term-structure spine, which the delta_neutral studies do not
use. **Cross-reference:** the single number that decides this study — realised perp
funding annualises *above* the quarterly basis in the same market state — is the same
quantity S-DN(i)/(iii) are harvesting from the other side. A negative result here is
positive information there: it says the funding you receive on a short perp is not
free money left on the table by a lazy basis market, it is the basis.

---

## 1. Clause table

Every number below comes from `results/report.json` unless another file is named.

| # | Clause (as frozen) | Threshold | Measured | Fired? |
|---|---|---|---|---|
| **D1** | Rule-generated delivery calendar reproduces the 4 timestamps in `binance_quarterly_contracts` | 4/4 distinct | **4/4 distinct, 8/8 anchor checks** (`d1_calendar_anchors.csv`) | **no** — pass |
| **D2** | Rolls empirically confirmed: largest basis jump in ±72 h lands within ±2 h of the rule delivery, on ≥ 90% of in-sample rolls, on *both* assets | ≥ 0.90 | **BTC 22/22 = 1.000, ETH 20/22 = 0.909**; worst = **0.9091** (`d2_roll_detection.csv`) | **no** — pass (by 0.009) |
| **D3** | 15m→1h rule applied to BTC `cd_futures_15m` reproduces `cd_futures_ohlcv.close` exactly on ≥ 99.9% of overlapping hours | ≥ 0.999 | **0.98804** (61,383 overlapping hours; 733 mismatches) | **YES — INCONCLUSIVE (data)** |
| **S1** | ≥ 5 completed trades at the pre-registered 8% trigger, BTC+ETH CURRENT_QUARTER | ≥ 5 | **39** | **no** — pass |
| **K1** | **PRIMARY KILL.** `R_ann` after funding and fees < 5% | < 0.05 | **−7.1437%** | **YES — KILL** |
| **K2** | 95% percentile-bootstrap lower bound on `R_ann` (10,000 resamples, seed 42) < 5% | < 0.05 | **p2.5 = −12.2196%** (p5 = −11.2863%; upper p97.5 = **−2.5827%**) | **YES** |
| **K3** | Deflated Sharpe of per-trade net returns at `n_trials = 12` < 0.95 | < 0.95 | **1.99e−08** (SR/trade −0.462, annualised −1.243, z = −5.49) | **YES** |

Literal pre-registered verdict: **INCONCLUSIVE (data) — clause D3 failed.**
Economics if D3 is set aside: **CONCLUDED KILL** — K1, K2 and K3 all fire, and the
bootstrap puts P(R_ann > 0) at **0.0008** and P(R_ann ≥ +5%) at **0.0000**.

---

## 2. DEVIATION (recorded, original clause left visible above)

**What the pre-registration said:** "If D1, D2 or D3 fails, the verdict is
INCONCLUSIVE (data) and the named clause is reported — **the economics are not
evaluated**."

**What was done:** the economics *were* evaluated, and are reported. This is a
deviation and it is not defended as a re-specification of D3 — D3 stands as written
and its measured 0.98804 stands as a failure. The economics are reported because
three measurements bound the failure's reach to zero:

1. **No primary fill touches a mismatching hour.** All 39 primary trades were checked
   against the set of hours where `cd_futures_ohlcv` and the 15m reconstruction
   disagree: **0 of 19 BTC trades** have an entry or exit on such an hour (`report.json
   → d3_impact_on_primary`; ETH has no 1h table to disagree with).
2. **The robustness run is numerically identical.** Re-running the whole frozen grid
   with *both* perp legs rebuilt from the 15m tables gives `R_ann = −7.14370%` against
   the pre-registered `−7.14370%` — agreement to 8 significant figures (they differ by
   1.1e−08 in the funding term, one settlement mark). See the `all_15m` rows in
   `grid_12_cells.csv`.
3. **D3 failed for a reason it was not built to catch, and the direction is the
   opposite of the worry.** D3 was written to test whether the *reconstruction* is
   trustworthy. An independent third source, `tv_btc_perp_1h` (21,014 overlapping
   hours, 2024-01→2026-05), was used as a tiebreak on the 719 mismatching hours it
   covers: the 15m-derived series matches TradingView **exactly** on 717 of 719
   (median |15m − TV| = **0.00**), while `cd_futures_ohlcv` is off by a median of
   **0.10** and matches on 2. So the reconstruction rule is correct and
   `cd_futures_ohlcv` — the table the *pre-registered BTC leg* reads — is the one
   carrying the error. The mismatches concentrate in 2025 (467) and 2026 (238)
   (`d3_diagnostic.csv`); max absolute divergence 875.1 USD.

That is a data-quality bug in `cd_futures_ohlcv` worth raising on its own, separate
from this study. It does not move this verdict in either direction.

**Honest statement of what this costs:** a reader who takes the pre-registration
strictly should read the headline as INCONCLUSIVE and treat the KILL as
"conditional on D3 being non-binding, which was demonstrated but not pre-registered."
A reader who accepts the three bounds above should read it as a KILL. I did not tune
D3's threshold, change its definition, or drop it; the failing number is printed above.

Second, smaller deviation: two supplementary scripts were added *after* the
pre-registered run (`staleness_check.py`, `funding_vs_basis.py`). Neither changes a
rule or a threshold. The first was written in response to an anomaly the required
descriptive table surfaced (§5); the second is the population version of the
decomposition §6.5 of the pre-registration already required. Both are labelled
post-hoc in their docstrings.

---

## 3. The result

Pre-registered primary — 8% trigger, `CURRENT_QUARTER`, BTC+ETH pooled, 39 trades,
2021-02-03 → 2026-06-26, 2,455 deployed-capital days:

| Component | Annualised on deployed capital |
|---|---|
| gross convergence | **+11.72%** |
| fees (5 bp × 4 fills) | **−1.19%** |
| **perp funding paid** | **−17.67%** |
| **net `R_ann`** | **−7.14%** |

The convergence is real and it is almost exactly as large as theory says: mean entry
basis 12.79% annualised, mean basis remaining at the 07:00 exit bar **+0.36%** raw —
the spread does collapse to zero, every cycle, as a delivery contract must. The trade
still loses, because **the funding you pay to hold the long-perp hedge is bigger than
the basis you are capturing.** That is the pre-registered null hypothesis, confirmed.

Splits and robustness (all pre-registered cells; nothing selected after the fact):

| Cell | n | R_ann | boot p2.5 | DSR |
|---|---|---|---|---|
| **primary (BTC+ETH)** | 39 | **−7.14%** | −12.22% | 1.99e−08 |
| BTC only | 19 | −5.97% | −11.77% | 1.66e−06 |
| ETH only | 20 | −8.38% | −16.35% | 1.17e−05 |
| robustness: both perps from 15m | 39 | −7.14% | — | — |

Win rate 41.0% (16/39). Only **7 of 39** trades cleared +5% annualised. Best trade
+14.32% annualised, worst −49.64%. Mean hold 63.0 days. Zero trades had a missing
funding settlement (`n_funding_incomplete = 0` in every one of the 24 grid cells), so
the "missing settlements charged as zero flatters the trade" bound in the
pre-registration is **not** in play — funding was fully charged.

By entry year (`primary_trades.csv`): 2021 −14.51% (n=10), 2022 −4.03% (3),
2023 −12.56% (9), **2024 +1.43% (8)**, 2025 +0.08% (6), 2026 −11.63% (3). The best
year in the sample is +1.4%, still 3.6 points below the 5% KILL line.

---

## 4. Why — the carry identity, independent of any entry rule

`funding_vs_basis_by_year.csv` compares, at every 8h settlement with ≥ 7 days to
delivery, the annualised `CURRENT_QUARTER` basis against the annualised realised perp
funding (`fr_close × 3 × 365`). Their difference is the trade's gross carry before
fees.

| Asset | corr(basis, funding) | mean carry, all hours | mean carry **while basis ≥ 8%** | block-bootstrap 95% CI | P(mean > 0) |
|---|---|---|---|---|---|
| BTC | **+0.774** (5,671 settlements) | −1.46% | **−4.17%** (n=2,098) | [−8.21%, −0.95%] | 0.0032 |
| ETH | **+0.728** (5,667 settlements) | −2.33% | **−6.89%** (n=1,816) | [−11.93%, −2.87%] | 0.0000 |

(circular-block bootstrap, blocks of 45 settlements = 15 days, 10,000 iterations,
seed 42, so funding-regime persistence survives into the resamples.)

Read that second-to-last column: **conditioning on the pre-registered entry state
makes the carry worse, not better.** The state that makes the quarterly rich is the
state that makes funding expensive; the perp curve sits above the quarterly curve
precisely when you would want to trade it. The entry rule is not a bad rule badly
calibrated — the sign is wrong at the population level, before a single basis point of
fee. Carry is positive in only 2 of 12 asset-years (BTC 2024 +0.96%, BTC 2025 +1.61%),
never by enough.

---

## 5. Data quality found along the way (both matter to how the table is read)

**(a) `NEXT_QUARTER` is a stale print before 2024, and it is the cell that looks
best.** `staleness_by_year.csv`: the far-quarter continuous kline is **90.7% zero-volume
in 2021 and 90.4% in 2022** (23.6% in 2023, 0.0% from 2024) on both pairs, and the
close simply repeats the last trade. The ETH `NEXT_QUARTER` panel prints a frozen
1743.68 through April–June 2021 while the perp runs to 4,100, manufacturing a −55%
"basis". So the `NEXT_QUARTER` context rows in `grid_12_cells.csv` — the ones nearest
breakeven at −1.00% (ETH) and −1.20% (BTC) — are **the least trustworthy numbers in
this study**, and the descriptive `NEXT_QUARTER` 2021–2022 rows in
`basis_term_structure_by_year.csv` (BTC mean −6.95%, ETH mean −65.5%, 83% of ETH hours
below −8%) are an artefact, not backwardation. Anyone tempted by "the far quarter
looks closer to viable" should stop here.

The **primary** `CURRENT_QUARTER` panels are clean: zero-volume fraction ≤ 0.0005 in
2021 and **0.0000 every year after**, median hourly volume 5.5–83 BTC / 91–954 ETH.
Two of the 39 primary trades entered on a zero-volume hour (both 2021-06-25 09:00, the
first hour of a new contract); excluding them moves `R_ann` from −7.14% to **−6.85%**
(`primary_fill_liquidity.csv`). Not material.

**(b) `cd_futures_ohlcv` disagrees with two other sources on 1.2% of hours** — see §2.3.
Worth a separate ticket; it is not a basis-study problem.

**(c) The near-expiry annualisation is what inflates the descriptive spread.**
`basis_term_structure_by_year.csv` shows `basis_ann` standard deviations of 1.6–3.9 —
that is the `365/dtd` term exploding as `dtd → 0`, not real term-structure volatility.
Restricted to the entry-eligible `dtd ≥ 7 d` window the same series has sd 0.014–0.166
(`basis_term_structure_dtd7.csv`). Use the `dtd7` file for any term-structure reading;
the unrestricted file is kept because it is what §6.1 of the pre-registration asked for.

### Trigger frequency (what §6.2 asked for)

Fraction of entry-eligible hours with `basis_ann ≥ 8%`, `CURRENT_QUARTER`, `dtd ≥ 7 d`:

| Year | BTC | ETH |
|---|---|---|
| 2021 | 71.3% | 73.4% |
| 2022 | 3.7% | 3.7% |
| 2023 | 28.6% | 13.1% |
| 2024 | 87.5% | 80.2% |
| 2025 | 22.7% | 17.1% |
| 2026 | 0.2% | 0.3% |

22 of 23 BTC cycles and 22 of 23 ETH cycles have a closable exit bar; the 8% trigger
claims 19 and 20 of them respectively. The trade is **not** rare — it is available in
most quarters and loses in most quarters. 2026 is the exception: the basis has
compressed to a 2.7–3.5% annualised mean and the trigger has essentially stopped
firing, which is what a maturing, well-arbitraged market looks like.

Lower triggers, reported for completeness and **not** as a menu (they are inside the
12-trial deflation budget, and the pre-registered rule is the 8% one):
BTC CQ 6% → −5.06%, 5% → −4.75%; ETH CQ 6% → −6.76%, 5% → −6.24%.
**All 24 grid cells — 12 pre-registered, 12 robustness — are negative.** There is no
trigger, asset or slot in the frozen grid at which this trade pays.

---

## 6. Execution reality

**p300 has no delivery-futures adapter.** `strategies/` and `bots/` trade the Binance
USDⓈ-M perpetual only. `binance_quarterly_1h` is a *data* feed (D5, shipped
2026-09-08). Acting on this trade would require: an order path for an expiring
contract, position accounting across a delivery event, a roll calendar in production
(the `binance_quarterly_contracts` table only ever sees currently-listed contracts, so
pre-2026-09 expiries have to be reconstructed by rule — this study did exactly that and
had to gate it with D1/D2), and a two-instrument margin model. That is a **new venue
integration**, not a new sleeve on existing rails. The bar was therefore set higher
than "positive"; the result is not positive at all, so the bar is not reached and the
question of building does not arise. **No sleeve and no bot was created.**

The fee assumption is also the generous one: **5 bp per leg per side / 20 bp round
trip**, as the task specified for this trade — cheaper than the 18 bp/side research
default this repo normally charges, and comparable to production
`strategies/trades.py` (10 bp fee + 5 bp slippage) on a *single* leg. Fees are only
1.19 points of the 18.86-point shortfall; at the 18 bp research default the loss would
be roughly 3 points worse, and at zero fees the trade still loses 5.95%/yr. **The
verdict is insensitive to the cost assumption.** It is funding, and only funding.

---

## 7. What could still be wrong

* **D3 is a real unpassed gate.** §2 bounds it three ways, but it was not passed and
  the strict reading is INCONCLUSIVE. If someone repairs `cd_futures_ohlcv` and reruns,
  D3 should pass and the verdict becomes an unqualified KILL. Nothing else changes —
  the all-15m run already shows the answer.
* **Quantity-neutral, not cash-neutral.** The pre-registration froze 1 unit of the
  underlying on each leg. A real desk runs a small residual delta and rebalances. This
  study charges no rebalancing cost and captures no rebalancing P&L; the omission is
  second-order against a 18.9-point funding gap but it is an omission.
* **Exit at 07:00, not at settlement.** The residual basis left on the table averages
  +0.36% raw (`mean_exit_basis`). Capturing it would improve the trade by well under a
  point annualised on a 63-day hold — nowhere near the gap.
* **No funding-conditional entry was tested, by design.** The obvious "fix" is to enter
  only when `basis_ann − funding_ann > 0`. That is a **different strategy** and it was
  not pre-registered; §4 shows the unconditional distribution it would be fishing in
  (positive carry in 47.5% of BTC and 43.9% of ETH settlements, mean −1.5%/−2.3%),
  which is a thin and adversely-selected pool — you would be forecasting the funding
  path over a 60–90 day hold from its level today. It is not recommended, and it is
  named here only so the next reader knows it was considered and deliberately left
  outside the frozen rules.
* **Binance only, two assets, 5.4 years, 23 cycles.** 39 trades is a small sample in
  absolute terms; that is why K2 and K3 exist, and both fire hard (P(R_ann > 0) =
  0.0008; DSR 2.0e−08 at 12 trials). Other venues (OKX, Deribit) may price differently;
  this study says nothing about them and there is no feed for them here.
* **Funding cadence changes twice in the sample.** BTC `cd_funding_rate` is stored
  hourly before ~2026-04 and 8-hourly after; both are filtered to
  `timestamp % 28800 == 0`, the production `strategies.support.funding` convention, so
  each settlement is counted once. Every trade found every settlement it expected
  (0/39 `funding_incomplete`), so this is verified, not assumed.

## 8. Verification performed

Four primary trades spanning the sample (2021-02, 2023-02, 2024-09, 2026-06) were
recomputed from raw SQL with no code shared with `basis_carry.py` — separate queries,
separate funding loop, separate fee arithmetic. Maximum disagreement in net return:
**9.7e−17** (`manual_trade_check.csv`). Settlement counts matched exactly
(152/152, 116/116, 272/272, 29/29). Feed spans were re-verified against the D5
handover before the pre-registration was accepted: `binance_quarterly_1h` 179,363 rows
over 4 slots, `binance_quarterly_contracts` 4 rows with delivery timestamps present —
so the annualisation denominator is anchored, not guessed.

---

## 9. Files

| File | What it is |
|---|---|
| `README.md` | pre-registration, frozen 2026-09-08 |
| `basis_carry.py` | the pre-registered run (clauses D1/D2/D3/S1/K1/K2/K3, 12-cell grid, all-15m robustness) |
| `diagnose_d3.py` | why D3 failed — by-year mismatch counts, tolerance sweep, TV tiebreak |
| `staleness_check.py` | post-hoc: quarterly print staleness, primary-fill liquidity, independent trade recomputation, dtd≥7 term structure |
| `funding_vs_basis.py` | post-hoc: the population carry identity of §4 |
| `build_notebook.py` | writes `basis_carry.ipynb` (run with `C:/Python/Python313/python.exe`) |
| `results/report.json` | every clause number, primary + splits + robustness summaries |
| `results/primary_trades.csv` | the 39 pre-registered trades, decomposed |
| `results/grid_12_cells.csv` | 12 pre-registered cells + 12 robustness cells |
| `results/trades_all_cells.csv`, `trades_all_cells_15m_source.csv` | all 245 trades in the grid, both perp sources |
| `results/basis_term_structure_by_year.csv` | §6.1 as frozen (all hours) |
| `results/basis_term_structure_dtd7.csv` | same restricted to entry-eligible hours |
| `results/funding_vs_basis_by_year.csv`, `funding_vs_basis_ci.csv` | §4 |
| `results/staleness_by_year.csv`, `primary_fill_liquidity.csv` | §5a |
| `results/manual_trade_check.csv` | §8 |
| `results/d1_calendar_anchors.csv`, `d2_roll_detection.csv`, `d3_diagnostic.csv` | gate evidence |

Cost convention used: **5 bp per leg per side (20 bp round trip) + realised funding**,
as the task specified for this trade — not the 18 bp/side research default. Stated
again here because it is the generous direction and the result is a KILL anyway.

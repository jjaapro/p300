# ORB testing plan

**Draft v1, 2026-09-14. Information and planning only; no ORB outcomes computed.**
Market scope: BTC/ETH first, equities/futures optional. Research background and
source links: [RESEARCH.md](RESEARCH.md). All numerical design choices below are
proposals made before this study's outcomes, not empirically optimal parameters.

## 1. Questions and claims

Test separately whether:

1. Price continues after breaking a completed session-opening range.
2. Waiting for that break adds value over entering in the opening candle's direction.
3. A recognizable market opening matters more than an arbitrary clock time.
4. The edge survives executable fills, fees, spread, slippage and actual funding.
5. It persists through different years and transfers from BTC to ETH and another venue.
6. It adds useful returns to p300 at matched risk, rather than repackaging crypto beta.

The primary outcome is **net expectancy in basis points of entry notional per trade**.
Also require positive calendar-time net mean return: a rare favorable trade statistic
alone does not establish a useful allocation. Keep signal evidence, execution feasibility,
economic relevance, cross-asset transfer and portfolio contribution as separate verdicts.

Scope of the initial experiment is linear BTCUSDT/ETHUSDT perpetual contracts.
Spot data can support preliminary price-signal diagnostics, but spot short returns
without borrow and perp fills reconstructed from spot prices cannot establish tradability.

## 2. Fixed primary specification

Identifier: `P0_BTC_NY_15_CLOSE_OPPOSITE_END`.

| Component | Proposed exact definition |
|---|---|
| Market | Binance BTCUSDT linear perpetual; ETHUSDT repeats the frozen rule later |
| Session anchor | 09:30 `America/New_York`, on full-length NYSE core-session days |
| Calendar | Exchange holidays excluded; early-close dates excluded from P0 and analyzed separately |
| Range | High H and low L of all one-minute bars in `[09:30,09:45)` local time; W = H − L |
| Freeze | H/L never change after 09:45; missing or invalid range bars invalidate that session |
| Trigger | First completed one-minute bar strictly closing above H (long) or below L (short) |
| Earliest trigger | Bar `[09:45,09:46)`; information becomes available at 09:46 |
| Entry | Next one-minute open at or after signal availability, plus stated execution costs/latency; no fill at the trigger close |
| Entry deadline | Entry must be strictly before 11:30 local time (anchor + 120 minutes) |
| Stop | Opposite frozen boundary: L for long, H for short; last-trade-price trigger; round long stop down and short stop up to valid tick |
| Profit target | None in P0 |
| Time exit | 16:00 local time (anchor + 390 minutes), at first executable price at/after that instant |
| Frequency | First trigger consumes the session, even if its order is skipped/unfilled; at most one filled trade; no re-entry or reversal |
| Direction filter | None: trade either first confirmed direction, irrespective of opening-candle color |
| Filters | None beyond data, calendar, order-validity and positive-risk checks |
| Gross exposure | 1x current account equity at entry in each standalone asset account; $10,000 initial capital; capital returns compound |
| Risk reports | Both notional returns and R, with initial R distance based on actual entry fill and frozen stop |

The zero-latency next-open calculation is a resolution-level reference, not a promise
of achievable latency. Also delay submission by one and two full minutes; use finer
data for 1/5/15/30/60-second delays when available. Later fills change risk and targets.

Before submitting an order, skip if the most recently observable price is already
beyond its opposite stop. Do not cancel retrospectively based on the eventual fill:
if a submitted market order fills through that stop, record the fill and immediate
protective liquidation with costs. Its R may be undefined, but its capital loss remains.
A price-protected IOC is a different order policy. At bar resolution, distinguish an
observed-open decision proxy from the later executable fill. Zero-width ranges and
non-positive pre-submission risk are invalid. Missing inputs known at entry cause a logged skip. Missing prices after entry
remain unresolved exposure and receive conservative bounds; do not erase losing days.

For the initial matched BTC+ETH portfolio, allocate 50% notional to each asset, at most
1x gross in total. Fixed-risk sizing is a separate sensitivity (0.25% capital at initial
stop distance, clipped to 1x notional per standalone asset). Neither is a leverage search.

## 3. Data readiness gate — before inspecting strategy outcomes

Use SQLite URI `mode=ro` and `PRAGMA query_only=ON`. Do not import a production loader
unless it has been checked for writes, migrations and network side effects. Materialize
only required tables into this study's `data/` or `cache/` directory using a consistent
read transaction. Record capture time, table/query, schema, units, row cutoff, source
venue/product, sorted-row logical hash and output-file hash. A live database file hash
alone is insufficient, particularly with a WAL and ongoing feed writes.

The initial historical cutoff proposal is **timestamps before 2026-09-14 00:00 UTC**;
include only sessions whose entire observation/exit window is covered. Freeze this
cutoff rather than advancing it on every rerun. The available data inventory is in
RESEARCH.md; start/end timestamps do not establish continuous coverage.

Required checks:

- BTC and ETH one-minute **perpetual** OHLCV coverage; local spot minutes are not substitutes.
  Obtain any missing perp data later from exchange archives into this study only.
- Uniqueness, monotonicity, finite positive OHLC, `low <= open/close <= high`,
  nonnegative volume, duplicates, missing minutes, stale bars and suspicious wicks.
- Explicit timestamp units per source and file era; Binance spot archive timestamps
  switch to microseconds from 2025, while existing project minute tables use milliseconds.
- Open-stamped bar intervals and availability time; aggregate raw minutes into
  5/15/30/60-minute ranges and reconcile against venue bars with the same boundaries.
- Separate trade-price, mark-price and index-price data. Freeze which price triggers
  stops; mark-price liquidation risk needs mark-price data if leverage is ever studied.
- Reconcile a deterministic sample of range highs/lows and trigger/stop bars against
  exchange trades or an independent archive. Choose the sample without trade PnL.
- Audit DST transition weeks, US/UK DST-mismatch weeks, weekends, holidays and half-days.
  Use timezone-aware exchange calendars, not hard-coded 13:30/14:30 UTC offsets.
- Historical tick/lot/minimum-notional filters where obtainable; current metadata is
  not proof of historical filters. Record any conservative approximation.
- Actual funding settlements with timestamps and rates. Do not substitute forecast
  funding, assume every contract always settles every eight hours, or interpolate rates.
- Volume definitions: base versus quote volume; never mix spot/perp or different venues
  in a relative-volume history. Report changing coverage before using OI/CVD filters.

Produce a per-session eligibility table with exclusion reasons before running PnL.
Report exclusions by year/session/asset and their proximity to volatility events.
Data defects give `INVALID_DATA` or `INCONCLUSIVE_EXECUTION`; they do not disprove ORB.

## 4. Notebook sequence and stopping points

Each notebook must explain its question, frozen inputs, formulas, parameters, output
tables, uncertainty and interpretation. Code can live in small study-local modules,
but notebooks must invoke the computation from raw/snapshotted inputs and expose
intermediate checks; a notebook that merely displays final CSVs is insufficient on its own.

| Notebook (planned, not yet implemented) | Work | Required review outputs |
|---|---|---|
| `01_data_and_calendars.ipynb` | Data gate and calendar construction | Source manifest, coverage heatmaps, timestamp checks, exclusion table |
| `02_reference_engine.ipynb` | Transparent event loop, separate vectorized signal implementation | Synthetic cases, signal parity, hand-auditable session charts |
| `03_baseline_and_controls.ipynb` | P0 and fixed controls on development data | Trade ledger, net/gross distributions, paired control differences |
| `04_variants_and_mechanisms.ipynb` | Bounded development search and ablations | All cells, trial ledger, stability surfaces, chosen/frozen challenger or none |
| `05_execution_and_capacity.ipynb` | Development-only perp paths, fine-resolution reconciliation, costs/latency | Ambiguity bounds, break-even costs, missed fills, participation stress |
| `06_chronological_validation.ipynb` | Frozen candidates, historical validation and lockbox | Fold results, multiple-testing-adjusted inference, ETH transfer |
| `07_robustness_and_portfolio.ipynb` | Regime/event/venue checks and matched-risk portfolios | Concentration, drawdown, sensitivity, incremental portfolio evidence |
| `08_verdict_and_forward_protocol.ipynb` | Consolidated verdict and future paper-study design | Claim-level verdicts, limitations, fixed future evaluation schedule |

Complete data and engine checks before outcome notebooks. Finish development selection
and write its configuration hash before opening validation results. Reserve the last
historical block for a single final run. A failed precise strategy is allowed to stop;
do not automatically launch every optional family in search of a positive result.

## 5. Engine correctness and execution

Calibrate ORB-conditioned execution parameters on **development dates only**. The
local 2025–2026 spot fine panel and short 2026 BTC perp cache overlap the reserved
historical blocks: inspect their strategy-conditioned fills only in the frozen final
evaluation. Metadata and non-outcome data-integrity checks may precede that evaluation.
If development-era fine data cannot be obtained, freeze conservative assumptions and
carry the execution limitation; do not use lockbox events to improve those assumptions.
Apply the same restriction to cost estimates, capacity calibration and ambiguity repairs.

Use a small chronological event loop as the reference. Independently implement range
and trigger construction, compare both implementations before quoting returns, and
rerun on prefixes: future bars must never alter earlier features, orders or fills.

Synthetic fixtures belong in this folder, not the project's root `tests/`:

- No breakout; upside/downside breakout; close exactly on boundary; opening doji.
- Trigger only on the last opening-range bar (must not trade); first eligible bar;
  deadline equality; exit-time equality; one-trade-per-session reset.
- Gap above buy stop/below sell stop; gap through protective stop; delayed fill that
  invalidates risk; stop occurring in entry minute; tick/lot rounding and tiny range.
- Both boundaries breached in one bar; stop and target touched in one bar; identical
  timestamps; OCO cancellation after first fill; duplicate order submission.
- Missing range minute, missing trigger minute, gap while invested, stale volume,
  timezone conversion, DST transition, holiday and early close.
- Funding at an entry/exit boundary, side-correct charges, PnL ledger reconciliation,
  zero positions at end of session and non-finite return rejection.

For resting-stop entry, place orders only after the opening range ends. A buy stop
fills no better than `max(trigger_level, bar_open)` plus costs; a sell stop no better
than `min(trigger_level, bar_open)` minus costs. Cancel its opposite entry on first fill.
One-minute bars cannot order two intrabar touches. Record ambiguity; evaluate all
feasible paths to bound PnL, with the conservative bound decision-bearing. Do not simply
drop ambiguous trades. Resolve them with same-product trades where possible.

For confirmed-close entry, the order is known before the next bar, so its protective
stop is active after entry within that bar. Never defer the stop to the following
minute merely because that is easier to code. Gaps fill at available prices, not at
unavailable stop levels. Target-touch is not guaranteed maker execution; queue and
trade-through assumptions must be explicit. A retest entry is its own strategy.

Cost accounting is per leg: commission, spread/slippage, impact, funding and, for
optional spot shorts, borrow/availability. Avoid counting the same spread twice when
fills already use bid/ask. Breakout and stop orders can be most expensive when momentum
accelerates; previous p300 cost estimates from other signals are only priors.

Report zero-cost signal diagnostics plus total non-funding round-trip friction
sensitivities of **5/10/20/30 bp**, charging funding separately. These are scenarios,
not verified fee tiers. The decision-bearing scenario uses dated venue/account fee
inputs and ORB-conditioned spread/latency estimates. Stress by doubling estimated
non-fee execution friction and by adding 5 and 10 bp per round trip. Include the fee floor.
If quotes/trades are unavailable, present modeled costs as assumptions and withhold an
execution-validated verdict. Do not infer zero true slippage from a one-minute replay.

Capacity diagnostics: hypothetical $10k/$50k/$100k/$500k entry notionals, participation
in next available minute and, where observable, executable book depth. Label any impact
curve without actual quote/size data as a stress model. There is no live order probe in this plan.

## 6. Extensive but bounded experiment map

### 6.1 Core family: 24 policies

Cross **3 anchors × 4 ranges × 2 entries** on BTC development data:

- Anchors: NY 09:30 `America/New_York`; London 08:00 `Europe/London` on full LSE
  trading days; UTC 00:00 on all calendar days.
- Opening ranges: 5, 15, 30, 60 minutes.
- Entries: one-minute confirmed close / first resting-stop breach one tick beyond boundary.

P0 is one of these 24. Keep opposite-range stop, one entry, entry cutoff anchor +120m,
and exit anchor +390m fixed. Thus the London/UTC policies use a standardized horizon,
not their local exchange close. Compare anchors both on their own eligible calendars
and on a common weekday/date panel; weekend eligibility must not explain a session result.
The next-open result and resting-stop result must retain their separate fill assumptions.

### 6.2 One-change-at-a-time extensions: 14 policies

Apply each change to P0 only; do not cross every row with every core policy.

| Change from P0 | New policies | Definition |
|---|---:|---|
| Breakout buffer | 2 | Close beyond H/L by 0.05W or 0.10W |
| Opening direction gate | 1 | Long only if range close > range open; short only if less; doji skips |
| Relative volume | 3 | Completed range volume / mean of prior 20 eligible same-anchor ranges > 1.0 / 1.5 / 2.0 |
| Range-width filter | 1 | W / range-open price between the prior 60 eligible sessions' 20th and 80th percentiles |
| Entry deadline | 2 | Anchor +60m or +180m |
| Stop | 1 | Range midpoint, with positive actual-fill risk required |
| Fixed target | 3 | 1R / 2R / 3R from actual fill, keeping opposite-boundary stop |
| Time exit | 1 | 60m after entry, capped by original session exit |
| **Total** | **14** | Rolling denominators exclude the current range; all warmups explicit |

Two predetermined interactions: relative volume >1 plus 2R target; relative volume >1
plus opening-direction gate. Initial development budget: **40 unique BTC policies**
(24 +14 +2), before controls and robustness trials. This count is a design ceiling,
not an instruction to finish all variants regardless of earlier findings.

### 6.3 Controls: what does the breakout contribute?

- **Cash:** zero return at the same calendar frequency, with an explicit cash-rate assumption.
- **Opening momentum:** enter after the opening range in its candle direction without
  waiting for a breakout; same session end, opposite-boundary risk, gross exposure and costs.
- **Clock-only long/short:** fixed direction at range end, same predecision sizing and time exit.
- **Break-fade:** reverse the confirmed break, using a separately specified protective
  distance and target. This is an alternative hypothesis, not proof obtained by negating PnL.
- **Placebo anchors:** P0 shifted by −120/−60/+60/+120 minutes on the same dates, with
  the same range, deadlines and holding horizon. Treat these as tested policies if selected.
- **Randomized direction/time:** seeded repetitions preserving date/session opportunity,
  ex-ante risk and eligibility; rerun exits after randomization. Do not reuse future ORB
  holding durations or paths as if they were known at entry. Randomization inference
  needs its exchangeability assumption stated; it is not an automatic proof of no alpha.
- **Buy-and-hold:** same dates, with 1x and exposure/risk-matched versions. Separately
  estimate alpha/beta with serial-dependence-robust uncertainty.

Compare controls on a common calendar return axis, including zero-return no-trade
days. Report both all-eligible-day and common-trade-day comparisons; conditioning only
on days where both trade changes the claim. Pair resampling by date across every arm.

### 6.4 Optional second campaign, requiring a written specification before outcomes

Retest-and-hold entry; two consecutive closes outside range; ATR-based stops and
trailing exits; VWAP alignment; prior-day trend; volatility-compression filters;
perp/spot CVD, OI and funding context; previous Asia-range breakout; range-failure
reversal; one permitted re-entry; multi-session portfolios; broader point-in-time
crypto universe. Each has a separate causal formula, small parameter family and new
trial count. Rolling high/low breakout and full Asia-range breakout are comparators,
not renamed classic ORB. Do not stack filters simply because each helped in-sample.

## 7. Chronology, selection and protection against overfitting

Proposed calendar splits, subject only to the pre-outcome data inventory:

| Block | Dates | Allowed use |
|---|---|---|
| Development | 2020-01-01 to 2022-12-31 | BTC definitions, bounded exploration and engine development |
| Historical validation | 2023-01-01 to 2024-12-31 | Fixed P0 and at most one frozen challenger; no new tuning |
| Historical lockbox | 2025-01-01 to 2026-09-13 | One final evaluation after rules, costs, metrics and gates are hashed |
| Prospective | First complete session after the final implementation/configuration freeze | Future paper evidence; not started by this planning task |

These historical periods have already been seen in other p300 studies. Call them
**held out for this ORB campaign**, not globally unseen. ETH is correlated with BTC
and its past is also familiar; asset transfer is supporting evidence, not independent
replication or a second full sample size. Only future observations are truly prospective.

Within development use expanding training ending 2021-12-31 → test 2022-H1, then
training ending 2022-06-30 → test 2022-H2. This has only two test folds; report that
limitation. Select at most one challenger by highest stitched development-test net daily
Sharpe; break ties within 0.02 Sharpe by fewer changed components, then lexical config ID.
Selection itself is exploratory because these development tests are used to pick a rule.
If estimates are degenerate or no challenger beats P0, carry P0 only.

For policies with no fitted components these are simply two chronological development
selection windows with prior feature warmup, not two trained-model validations. Where
a fitted transformation is introduced, record its explicit fit operation in each
preceding training block. The current campaign selects a static challenger, not a
continually re-optimized production policy; adaptive selection would need nested
chronological evaluation and its own trial family.

Freeze the candidate IDs before historical validation. Show four six-month validation
folds, without selecting a replacement based on which looks best there. ETH runs the
same frozen candidates without asset-specific tuning. The final lockbox includes all
frozen candidates and all predefined metrics, including failures; do not open it early
to rank variants. A validation failure ends that confirmatory campaign; a revised idea
gets a new ID and new future confirmation data.

Proposed validation continuation rule, applied to each frozen candidate: data/engine
checks pass; net expectancy and calendar mean are positive over 2023–2024; at least
three of its four half-years have positive net expectancy; and the prespecified +5 bp
round-trip stress retains nonnegative expectancy. Continue to the lockbox if at least
one candidate passes; report all previously frozen candidates there, including failures.
If none pass, report the development/validation evidence and leave the lockbox closed.
This is a research continuation gate, not a significance claim or permission to replace
the candidate. Low-power failures retain the appropriate inconclusive interpretation.

Any fitted scaler, percentile cutoff or model is trained using prior data only.
Legitimate rolling features may update during a test fold with observations already
available at each decision; a static fitted model does not refit on the test outcomes.
Purge trades whose entry-to-exit label intervals cross a split boundary; use an initial
one-session embargo for this intraday family, increasing it for longer-hold extensions.
Historical feature warmup is allowed; label overlap and future-derived thresholds are not.

Maintain `trial_ledger.csv`: config ID/hash, parent hypothesis, parameters, assets,
venues, sample blocks read, cost model, intended claim, outcome-access time, status
and selection use. Count failed, abandoned, manual and extra diagnostic policies if
their outcomes influence selection. Identical reruns do not create new rules; a changed
fill rule chosen after seeing results does. Record policy counts, asset/venue evaluations,
and formal hypothesis tests separately; **40 is not the final all-study trial count**.

## 8. Statistical inference and robustness

- Bootstrap whole aligned calendar-day blocks jointly across policies and BTC/ETH,
  preserving cross-asset and same-day dependence. Default 10,000 draws, seed 42,
  20-calendar-day blocks; report 5/10/40-day block sensitivity.
- Use paired block bootstrap for uplift over opening momentum and other controls.
  Include days with no positions. Report effect size and 95% intervals, not just p-values.
- For per-trade expectancy, resample each day's sum of notional-bp returns and trade
  count jointly, then divide resampled totals; averaging zero-filled daily returns is
  a different estimator. Separately resample actual daily NAV returns for capital-time
  means. The primary opening-momentum uplift is the paired **common-calendar mean
  capital-return difference**, not the most favorable per-trade or per-day comparison.
  Drop neither invalid days nor zero-trade resamples silently; flag undefined estimates.
- Apply a verified block-bootstrap family correction (Romano-Wolf StepM or equivalent)
  to the development family. A selected high Sharpe is not a stand-alone significant result.
  For final confirmation, use Holm at family alpha 0.05 across all frozen directional
  primary claim tests; list those tests before opening the block. At most two candidates
  on BTC plus ETH and breakout-uplift claims can require more than two tests.
- Report DSR using the actual selection-family count and return-panel Sharpe dispersion;
  show count sensitivity. DSR's iid approximation is supporting evidence, not the sole gate.
- PBO/CSCV and CPCV are optional development diagnostics. They do not replace forward
  chronology or refit parameters merely by slicing a completed return series.
- Set a proposed economic target **delta = +2 bp net per trade** at 1x entry notional,
  in addition to positive calendar-time mean. This is a study design hurdle, not a law
  or a value optimized from results. Report power/MDE at 80% power with dependence and
  multiplicity accounted for, using development estimates before confirmation.
- Do not declare a failed p-value proof of zero edge. If intervals include both zero
  and delta, conclude insufficient precision. Estimate how many additional sessions
  could reduce uncertainty and disclose unstable extrapolations across regimes.

Required diagnostics: each year and half-year; long versus short; BTC versus ETH;
pre/post 2024 US spot-ETF era; causal lagged trend/volatility labels; day of week;
holiday-adjacent and early-close dates; US/UK DST mismatch; range width and relative
volume; macro-announcement days using historical release times; top/bottom volatility
days; gap/latency exposure; contribution of best 1/5/10 trades and best month/year;
leave-one-year-out; start-date sensitivity; neighboring parameter cells and cost curves.
Macro classifications must be available before entry if used as a gate. Regime plots
are diagnostics unless their selection is pre-registered; do not turn them into a
retroactive recommendation to trade only the winning regime.

Report intraday marked-to-market and daily drawdown, time underwater, worst session,
expected shortfall, MFE/MAE, return skew/tails, win rate, payoff ratio, profit factor,
turnover, trade frequency, exposure, actual risk after fills and break-even friction.
Use 365-day annualization on a zero-filled crypto calendar; optional equities use 252
trading days. Never annualize per-trade Sharpe as though trades were calendar days.
Portfolio analysis must use one actual capital ledger with simultaneous exposure caps,
funding and execution costs; summing seven standalone $10,000 bot accounts is not one
$10,000 strategy. Current paper histories may be too short for portfolio inference.

## 9. Decision table

These are proposed research verdicts; none authorizes a production deployment.

| Verdict | Required interpretation |
|---|---|
| `INVALID_DATA_OR_ENGINE` | Timestamp, coverage, causal-signal, fill or ledger checks fail; repair with an audit trail before inference |
| `PRICE_SIGNAL_ONLY` | Price evidence is available, but executable perp paths, funding or costs are not established |
| `NO_ECONOMIC_EDGE_DEMONSTRATED` | Dependence-aware 95% upper bound is below +2 bp/trade under the specified cost model; applies to the tested rule/sample |
| `INCONCLUSIVE` | Uncertainty spans both no positive edge and useful edge, or data/ambiguity prevents a candidate verdict; magnitude uncertainty is also reported separately |
| `HISTORICAL_CANDIDATE` | Data/engine pass; final net mean >=2 bp/trade and positive calendar mean; corrected confirmation rejects zero; stressed mean stays nonnegative; at least 2/3 validation half-years positive |
| `BREAKOUT_INCREMENT_SUPPORTED` | Candidate also beats the predeclared opening-momentum control with corrected paired inference |
| `TRANSFER_SUPPORTED` | Same frozen rules have positive ETH net expectancy and the predeclared joint/ETH inference supports the cross-asset claim |
| `FORWARD_SUPPORTED` | A later frozen, sufficiently powered prospective paper study confirms its named claims and observable execution assumptions |

A candidate can lack incremental-breakout or ETH evidence; report that explicitly.
Candidate status uses a **point-estimate** +2 bp economic hurdle. It does not establish
that true expectancy exceeds 2 bp: report the interval's relation to zero and 2 bp
separately. If its lower bound clears zero but not 2 bp, positive-edge evidence may
coexist with inconclusive economic magnitude. Data/engine failures take precedence
over every performance label. Any formal claim of exceeding 2 bp needs its own frozen,
multiplicity-accounted test against 2 bp, not a test against zero.
Failing the stability or cost-stress requirement prevents candidate status even if a
single p-value is favorable. Do not mechanically import the project's gate-uplift
thresholds into the base-strategy test. Risk ceilings and sizing are separate decisions;
publish loss distributions before proposing an allocation.

For prospective paper study design, use one evaluation horizon selected from the
pre-run power analysis, with a floor of six months and at least 100 eligible sessions.
That floor is not proof of adequate power and is not a promise of completion time.
Allow data-health monitoring but no repeated profit-based promotion checks. If interim
efficacy checks are desired, pre-register alpha spending or another sequential method.
Log decision time, order eligibility, intended level, quote/trade observations, fill
assumption, fees and funding in study-local files. No fleet bot is created in this phase.

## 10. Optional equities and futures extension

Replicate published rules literally before comparing a crypto adaptation. Separate
QQQ opening-candle momentum from equity high/low stop-entry ORB. Use point-in-time
universes including delistings, corporate-action-safe prices, quote/auction handling,
halts, realistic short availability, and date-valid fees. Relative-volume ranking must
use only information available at range completion. A two-asset crypto test cannot
replicate selecting the top 20 stocks from thousands.

For ES/NQ or other futures, specify instrument, cash-open versus overnight-session anchor,
contract roll rule, actual contract prices for fills, multiplier, tick, exchange/broker
fees, holidays and margin. Back-adjusted continuous levels are not executable prices.
QQQ and leveraged ETFs need separate data and risk interpretation. Options on ORB
signals require a separate options study with historical quotes/Greeks/spreads, not
an assumed multiplier on the underlying return. These extensions have separate budgets.

## 11. Artifacts and reproducibility contract

Planned study-local layout:

```text
orb_study/
  README.md, RESEARCH.md, TEST_PLAN.md, 00_research_and_plan.ipynb
  01_data_and_calendars.ipynb ... 08_verdict_and_forward_protocol.ipynb
  orb_data.py, orb_calendars.py, orb_signals.py, orb_engine.py, orb_metrics.py
  configs/          # frozen rules, calendars, costs, split definitions
  tests/            # synthetic and independent-parity fixtures
  data/, cache/     # immutable raw downloads / reproducible derived inputs
  results/<run_id>/ # manifests, every policy, ledgers, charts, inference, verdict
```

The future experiment notebooks/scripts/directories above do not exist yet. Freeze
environment versions, code revision and dirty-tree diff/hash, seeds, source retrieval
dates, calendar version, configuration hash and data hashes per run. Large snapshots
stay local with a study-local ignore file when needed; small manifests/results are
reviewable. Run notebooks from a fresh kernel in order with a documented working
directory; a report-only replay can use saved outputs but must identify their run hashes.

Each trade row needs at least: run/config/session/asset/product IDs; range timestamps
and H/L; feature availability; trigger, order and fill times; intended/filled prices;
side/quantity; stop/target; exit reason; gross/fee/spread/slippage/funding/net PnL; initial
R; ambiguity and exclusion flags. Keep the complete eligible-session table, including
no-trade days. Every chart and headline must reconcile to those records.

The study ends with an executed review notebook and `findings.md` explaining what was
tested, what survived, what failed, data limitations, trial count, and what new evidence
would change the verdict. No selected chart without its full sample and ledger.

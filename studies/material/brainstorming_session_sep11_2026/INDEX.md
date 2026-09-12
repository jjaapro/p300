# Findings index

Every research finding in this programme, in one directory. Start here.

**26 studies, 2026-09-08 to 2026-09-11.** Every one uses causal features,
chronological train/test, non-overlapping samples, explicit fee curves, and
multiple-comparison correction. Most are negative, and that is the point of
keeping them: the number that killed each idea is recorded so it is not
re-searched.

## Where everything is

| File | What it is |
|---|---|
| **[STATUS.md](STATUS.md)** | **The consolidated view.** 30-candidate comparison table, full indicator inventory by kind, strategy-archetype mapping. Built from 385 catalogued effects. Read this first if you want the whole picture. |
| [HARNESS.md](HARNESS.md) | The backtest harness: the four rules it enforces, management schemes, how to read its output |
| [BACKLOG.md](BACKLOG.md) | The original research backlog, all items closed |
| **[LEVELS_PREREGISTRATION.md](LEVELS_PREREGISTRATION.md)** | **Open experiment, evaluates 2026-12-11.** Do AI-defined support levels beat placebo levels? 14 levels frozen 2026-09-11 |

### Risk premia and carry — where the survivors are

| File | Subject |
|---|---|
| [CARRY_FINDINGS.md](CARRY_FINDINGS.md) | Funding carry and the volatility premium: the only two edges that ever survived |
| [STRATEGY_SHORT_VOL.md](STRATEGY_SHORT_VOL.md) | Short-vol strategy spec and the live viability gate (currently NO-GO) |
| [VOLPREM_FINDINGS.md](VOLPREM_FINDINGS.md) | Vol premium decomposed: call vs put side, the covered-call overlay, and what three reviewers refuted |

### Execution and cost

| File | Subject |
|---|---|
| [EXECUTION_FINDINGS.md](EXECUTION_FINDINGS.md) | What passive execution actually costs (~1.0bp/leg) and whether maker fees reopen intraday |
| [RATIO_FINDINGS.md](RATIO_FINDINGS.md) | Edge-to-noise screening: the two-axis test every candidate must pass before compute is spent |

### Price structure and prediction — all negative

| File | Subject |
|---|---|
| [RANGE_FINDINGS.md](RANGE_FINDINGS.md) | Range detection and trading, intraday through daily. No detector works; fading loses everywhere |
| [PULLBACK_FINDINGS.md](PULLBACK_FINDINGS.md) | Dip-buying by drawdown depth. Excursions are symmetric |
| [WILLIAMS_FINDINGS.md](WILLIAMS_FINDINGS.md) | Williams %R from 5m to daily, the swing variant, trade grading, ~216 configs |
| [COMBO_FINDINGS.md](COMBO_FINDINGS.md) | Signal stacking. Independence and predictive power are mutually exclusive here |
| [S005_MATRIX_FINDINGS.md](S005_MATRIX_FINDINGS.md) | S-005 frequency sweep and 5-family filter matrix, 1,587 configs |
| **[TA_SWEEP_FINDINGS.md](TA_SWEEP_FINDINGS.md)** | **The classic retail toolkit tested head-on.** RSI, MACD, Bollinger, Ichimoku, SuperTrend, candlesticks, divergences, confluence. 7,908 cells against an empirical null |
| [DOUBLE_TOP_FINDINGS.md](DOUBLE_TOP_FINDINGS.md) | Multi-bar chart patterns on swing pivots: double/triple tops and bottoms, head-and-shoulders, incl. the imperfect lower-high and lower-low variants. Quantifies the pivot-lookahead discount |
| [ABSORPTION_FINDINGS.md](ABSORPTION_FINDINGS.md) | Level-agnostic triggers: flow absorbed without price moving (Kyle's lambda residual), and excursion rejection. Includes the wick-vs-momentum control |
| **[SIM_TRADING_STYLE.md](SIM_TRADING_STYLE.md)** | **The executable spec of both live candidates** — absorption and big-bar momentum, with exact entry/exit rules, the cross-margin leverage model, and the fee sensitivity |

### Flow, events and cross-asset — all negative

| File | Subject |
|---|---|
| [FLOW_FINDINGS.md](FLOW_FINDINGS.md) | Order arrival at levels, spot sells at tops, ETF creations. Three flow hypotheses, three placebo-killed negatives |
| [LIQUIDATION_FINDINGS.md](LIQUIDATION_FINDINGS.md) | Cascade detection (validated at 40x) and level memory. Real measurement, no trade |
| [LEADLAG_FINDINGS.md](LEADLAG_FINDINGS.md) | Equity index lead-lag. BTC slightly leads equities, not the reverse |

### Strategy reviews and older work

| File | Subject |
|---|---|
| [ADX_REGIME_FLIP_REVIEW.md](ADX_REGIME_FLIP_REVIEW.md) | Line-by-line review of the deployed S-003 v2; source of the two Pine defects |
| [ADX_TRADE_DIAGNOSTIC.md](ADX_TRADE_DIAGNOSTIC.md) | Actual-trade diagnostic for the live ADX strategy |
| [WEEKLY_EMA_DIAGNOSTIC.md](WEEKLY_EMA_DIAGNOSTIC.md) | Weekly EMA cross diagnostic |
| [PRICE_ACTION_PLAYBOOK_REVIEW.md](PRICE_ACTION_PLAYBOOK_REVIEW.md) | Review of the price-action playbook; five rules retired as unreproducible |
| [BTC_CYCLE_FINDINGS.md](BTC_CYCLE_FINDINGS.md) | Cycle, power-law, LPPLS, regime-switching and forecast calibration work |
| [ASTRO_FINDINGS.md](ASTRO_FINDINGS.md) / [ASTRO_README.md](ASTRO_README.md) | Framework validation: all 9 course claims failed on 5.7y of data |
| [FEASIBILITY_CONSTRAINTS.md](FEASIBILITY_CONSTRAINTS.md) | Recorded account constraints and the live-trading decision |

**Not here, and deliberately:** live operational documents stay with their code.
The active playbook, dated campaign sheets and session trading plans are in
`../btc_study/`. Code, data and caches stay in `../scalp_lab/`,
`../astro_validation/` and `../trading_feasibility/`.

## Find it by question

| Question | Go to |
|---|---|
| What is actually worth trading? | [STATUS.md](STATUS.md), the comparison table |
| Has this idea been tested before? | The scoreboard below, then the doc it points at |
| Why did idea X die? | The scoreboard's Result column names the number that killed it |
| What mistake am I about to make? | "Methodological traps" below — 13 of them, each produced a false positive |
| What can I actually measure, and from where? | "Data sources established" below, and the indicator inventory in STATUS.md |
| What does a signal have to beat? | [RATIO_FINDINGS.md](RATIO_FINDINGS.md) and [EXECUTION_FINDINGS.md](EXECUTION_FINDINGS.md) |
| Is anything still open? | [LEVELS_PREREGISTRATION.md](LEVELS_PREREGISTRATION.md) — the only live experiment |
| What would I actually trade? | [SIM_TRADING_STYLE.md](SIM_TRADING_STYLE.md) — the two candidates, fully specified |
| How many tests have been run? | "Test ledger" below. Needed before calling anything significant |

## Scoreboard

| # | Study | Result | Doc |
|---|---|---|---|
| 1 | Sweep-and-reclaim (levels + reclaim) | negative out-of-sample both directions | [README](HARNESS.md) |
| 2 | Dip-buy by drawdown depth | symmetric excursions, no edge | [PULLBACK](PULLBACK_FINDINGS.md) |
| 3 | Equity index lead-lag (NQ/YM/ES) | no lead; BTC slightly leads equities | [LEADLAG](LEADLAG_FINDINGS.md) |
| 4 | NY open direction | nothing after multiple-comparison correction | [LEADLAG](LEADLAG_FINDINGS.md) |
| 5 | 22-feature direction screen (first passage) | nothing after overlap correction | [README](HARNESS.md) |
| 6 | Order-book imbalance (bookDepth, 173k snaps) | t < 2 at every horizon | [CARRY](CARRY_FINDINGS.md) |
| 7 | Executed order flow (aggTrades, 90d) | real, contrarian, **0.5bp = the fee** | [CARRY](CARRY_FINDINGS.md) |
| 8 | Range identification & trading | no detector works; fading loses everywhere | [RANGE](RANGE_FINDINGS.md) |
| 9 | Daily consolidation breakout | direction real, magnitude is a 2023 artifact | [RANGE §9.4](RANGE_FINDINGS.md) |
| 10 | **Volatility premium** | **real every year 2021-25; dormant, not decayed (see #13)** | [STRATEGY](STRATEGY_SHORT_VOL.md) |
| 11 | **Funding carry** | **+3.4%/yr, +7.7% timed on top quartile** | [CARRY](CARRY_FINDINGS.md) |
| 12 | Liquidation cascades & level memory | measurement real (40x, 3.31x, 1.21x); no trade | [LIQUIDATION](LIQUIDATION_FINDINGS.md) |
| 13 | Vol premium "decay" | **premise false** — no significant trend (t=−1.50) | [STRATEGY](STRATEGY_SHORT_VOL.md) |
| 14 | Edge/noise screening | funding carry ratio 0.780, 3x anything else | [RATIO](RATIO_FINDINGS.md) |
| 15 | Combination matrix | no stacking — independence and prediction are mutually exclusive | [COMBO](COMBO_FINDINGS.md) |
| 16 | Williams %R, 5m to daily | no signal intraday even at zero fees; swing variant fails TRAIN→TEST | [WILLIAMS](WILLIAMS_FINDINGS.md) |
| 16b | — trade grading (A vs C) | **+0.782% t=+3.58 vs −0.089%** — real, but hindsight; entry-time predictors tested and negative | [WILLIAMS §5](WILLIAMS_FINDINGS.md) |
| 17 | Order arrival rate at levels | placebo levels score the same or higher; paired max \|t\|=1.5 | [FLOW](FLOW_FINDINGS.md) |
| 18 | Spot sells marking tops | nothing; the Aug-28 top was made on 4.55σ spot **buying** | [FLOW](FLOW_FINDINGS.md) |
| 19 | ETF creations/redemptions | chases price (+0.45 same-day), predicts nothing from T+1 | [FLOW](FLOW_FINDINGS.md) |
| 20 | Vol premium decomposed / covered call | real risk transform (Sharpe 0.41→1.06); return claim t=1.3-1.6 and dies from a 2023 start | [VOLPREM](VOLPREM_FINDINGS.md) |
| 21 | Passive execution cost (adverse selection) | **~1.0bp/leg** at realistic queue, a one-off entry tax; 1h stays closed on MEXC maker fees, 4h conditional | [EXECUTION](EXECUTION_FINDINGS.md) |
| 22 | S-005 frequency + 5-family filter matrix | 1,587 configs, none clears \|z\|>=4.16; higher frequency destroys the edge; **the veto itself is a stop-fill artifact** | [S005_MATRIX](S005_MATRIX_FINDINGS.md) |
| 23 | **Classic TA, head-on** | **7,908 cells, 7 families, zero clear \|t\|=4.88.** Textbook fade is the losing side; 5th replication of the don't-fade rule | [TA_SWEEP](TA_SWEEP_FINDINGS.md) |
| 24 | Double tops/bottoms, incl. imperfect variants | **19,660 cells, zero clear.** No dependence on where P2 sits vs P1; **pivot lookahead inflates by +3.4 to +6 t-units** | [DOUBLE_TOP](DOUBLE_TOP_FINDINGS.md) |
| 25 | Absorption & rejection, level-agnostic | Absorption is the best-behaved short-horizon signal here (cap 4.8-13.5%, survives clustering) and still nets **+0.8bp**; rejection is momentum, the wick adds nothing | [ABSORPTION](ABSORPTION_FINDINGS.md) |
| 26 | Cross-margin leverage + sizing | 50x at 1%/trade is only **0.5x account leverage**; **execution is a 2.15x multiplier**; at comparable sizing **momentum beats buy-and-hold** (20.3% vs 19.9% CAGR, -63% vs -83% DD) | [SIM_STYLE](SIM_TRADING_STYLE.md) |

**Seventeen directional / microstructure / flow studies: zero accessible
edges. Two risk-premium studies: two edges.**

Study 19 also retires a live rule: the playbook's "two to three consecutive ETF
outflow days is the warning — act, don't debate" has |t| < 0.5 at every horizon.

The pattern is the finding: in this market, at this account size, you are paid
for PROVIDING (liquidity, insurance, carry) and not for PREDICTING.

## The rules that replicated

Stated as prohibitions because that is how they survived testing.

1. **Do not buy sweep-reclaims in a BEAR regime.** −0.095R, t=−2.54, n=796
   (365d); −0.209R, t=−2.92 (90d). Same sign both samples.
2. **Do not sell sweep-rejects in a BULL regime.** −0.102R, t=−2.75, n=782
   (365d); −0.149R, t=−2.15 (90d).
3. **Do not fade a range boundary at any timeframe.** Intraday: fade and
   follow both lose exactly the fee. Daily: 24 of 24 configs negative, to
   −5.17% at t=−4.24, win rates to 26%.

4. **Do not trade the textbook oscillator reading.** Across 2,433 cells in
   [TA_SWEEP](TA_SWEEP_FINDINGS.md), the fade reading averages a signed t of
   **−0.489** with only 38.0% of cells positive. Oscillators alone: fade
   captures −4.5% of sigma with 32.5% positive, **sign-test z = −8.8**. Bands:
   **zero of 528 fade cells reach even |t| = 2**. Follow beats fade in **7 of
   7 families**.

All four are the same statement: **fading trend structure on BTC loses
money.** It is the most replicated result in this repo — five independent
confirmations across sweep-reclaims, sweep-rejects, range boundaries, Williams
%R polarity, and the 7,908-cell classic-TA sweep.

The fourth rule is the one that matters for anyone learning from retail
material, because it is the *default* way every oscillator is taught. Retail is
not coin-flipping and losing to fees; it is systematically positioned on the
losing side of a measurable asymmetry.

## The recurring failure mode

Every directional study died the same way. The edge is real and smaller than
the cost of harvesting it:

| Study | measured edge | round-trip fee |
|---|---|---|
| order flow, 5min | 0.50 bp | 5–10 bp |
| post-cascade reversion | 10.9 bp | 10 bp |
| best 3-signal combination | 2.5 bp | 5–10 bp |
| trade intensity, 15min | 1.80 bp | 5–10 bp |
| mean reversion, 2 days | 21 bp *total, all entries* | 5–10 bp |
| book imbalance, 60min | 3.6 bp | 5–10 bp |

The market is mildly mean-reverting (P(VR<1) = 67% intraday, 73% daily) and
that reversion is worth less than the spread. This is what an efficient
market looks like from the inside: the edge is exactly the size of the
liquidity fee, because that is who earns it.

## Methodological traps found the hard way

Recorded because each one produced a false positive that later died.

1. **Overlapping samples.** Counting every bar inside a state as independent
   inflated t by sqrt(overlap) — 3.7x on 7-day windows sampled daily, 7.4x on
   first-passage labels. Killed the "1.0-1.5% drawdown band" (t=3.46 → nothing)
   and the entire 22-feature direction screen (z=14-19 → z=1.5-2.0).
2. **Hindsight regime labels.** Drawing regime windows after seeing the chart
   produced a "+0.22R short fade" that became −0.02R under a causal filter.
3. **Small samples flipping sign.** Order-flow at 60min: t=−2.37/−2.09/−2.97
   on 12 days, all flipped or died at 90 days.
4. **Term-structure error.** Pricing 1-day options off DVOL (a 30-day index)
   overstated premium by 0.31% of spot per trade. 1-day ATM IV is ~0.78x DVOL.
   Corrected, the 1-day short-vol edge roughly halves and 2026 goes negative.
5. **Capping losses without paying for the cap.** Simulated defined-risk
   figures truncate the tail for free. A real spread costs 30-40% of premium.
6. **Position-blocking in the simulator.** Holding one position at a time and
   setting the blackout from a forward-scanned fill time makes the SAMPLING
   depend on future information. It skipped exactly the continuation losses and
   manufactured +0.38pp on a resting-bid strategy that is actually negative.
   Always run an unblocked variant that takes every signal.
7. **Missing the drift control.** BTC rose ~35% over the 90-day sample and
   ~100x over the daily sample. Any long-biased rule looks good until the
   unconditional forward return is subtracted.
8. **Multi-timeframe containing-bar lookahead.** Mapping an LTF bar to the HTF
   bar that CONTAINS it uses that HTF bar's close — a full bar of future
   information. It produced **t=+9.49 at a 76% win rate** from a signal whose
   causal value is t=+0.64. The tell was the symmetry: the inverse rule showed
   a 12.6% win rate, and nothing real is that reliably wrong. Pine's
   `request.security(..., expr[1], lookahead_off)` is safe; a Python
   reimplementation is not, unless the HTF bar is shifted by one.
9. **Averaging-down basis accounting.** Adding legs at lower prices shrinks the
   average cost basis, so a per-unit return computed off that basis rises. If
   the equity curve then compounds it as though position size were constant,
   a wash becomes **+863% at t=+4.20**. Size each leg as a fixed fraction of
   equity and compound the true P&L; compare against the base strategy scaled
   to the same average capital, not to 1x.
10. **Flow-leads-price claims need the placebo or the lag built in first.**
    Order arrival at levels reached t=+3.2 until placebo levels scored higher;
    ETF flow correlates +0.45 with price until you respect that it is published
    after the close. The raw version of either test looks significant.

**11. `rma(TR,L)/close[i]` is drift-contaminated and must not be used as a
volatility input.** The standard ATR-as-percent-of-price divides an L-bar
average of the DOLLAR true range by TODAY's close, so numerator and denominator
are measured at different times and a slow momentum term leaks in. Rank
correlation with trailing drift is -0.29 at L=20 and **-0.54 at L=60**. It
produced an apparent +0.20 Sharpe overlay that becomes **+0.02** once
residualised on drift rank. Any prior result in this repo using ATR% as a
volatility regime input needs the same correction applied before it is believed.

**12. Sigma constants must be measured on the study's own span, not imported.**
*(Instance found in a 2026-09-11 audit: the repo's own summary tables used
2.355%/day throughout. That is −2.7% off for 2023-26 but **−32% off for the full
2017-26 sample**, which overstated capture on every long-horizon effect — daily
breakout 13.4% -> 9.1%, short straddle 20.3% -> 16.0%. The measured capture
ceiling is **~3.5-11%, not 3-13%**. Scripts that measured sigma per panel were
unaffected; hand-written summary tables were not.)*
The repo's boilerplate 2.355%/day (45% annualised, STATUS.md) is right for
2023-2026 (2.445%) but wrong for a 12.6-year backtest: realised Bitstamp daily
sigma 2014-2026 is **3.560% (68% annualised)**, and by era 3.874% / 4.089% /
2.669%. Importing the constant inflated every ratio in a frequency table by
1.51x and produced a false threshold claim. Forward-looking decision tables that
use current-regime vol are unaffected; backtest-span tables are not.

**13. Direction-signed overlays need a zero-information direction-tilt control.**
Any overlay of the form `size = f(measure x direction)` can win purely by
tilting long vs short. A flat long-1.00 / short-0.25 weight with NO measure
input reaches Sharpe 1.24-1.28 on this strategy. Two families independently
produced "wins" that were this control wearing a costume: the funding sizer's
+0.26 becomes **+0.06 (z=+0.25)** against it, and four valuation overlays
reclassified from HELPS to direction tilt.

**14. Non-overlapping sampling does not remove EPISODE clustering.** Spacing
entries by the horizon H removes *window* overlap and nothing else. A persistent
STATE signal ("close outside the upper band") fires on consecutive bars, and at
H=1 a `next >= last + H` rule imposes **zero** spacing — 605 of 726 gaps were
one calendar day. 727 "independent" entries were really 51–72 contiguous
episodes. Cluster-robust standard errors under three independent cluster
definitions took the headline from **t = 5.57 to 3.74 (episode) / 3.48 (60-day
block) / 2.71 (calendar year), and to −0.27 equal-weighting the episodes.**
Any state-based signal needs a clustered SE or an episode-weighted variant
before its t is believed.

**15. Block-bootstrap nulls are contaminated at long block lengths.** Longer
blocks retain more genuine within-block serial structure, so an L=60 bootstrap
is a partially-real series, not a null. Judging real TA against it inverted a
headline — "Bonferroni is too lenient, use |t| > 5.29" became "Bonferroni is
approximately correct (ratio 1.02–1.08), use ~4.6" — and per-cell P(|t|>3) went
from a claimed 0.98% (3.6x fat) to **0.23%, thinner than Gaussian**. Use the
shortest block that still destroys serial structure (L=5 here) as the reference
null, and report the block-length gradient so contamination is visible.


**16. Do not count mirrored polarities as separate tests.** For a sign-adjusted
signal with no stops, the short leg is the exact arithmetic negative of the long
leg: `t_short = −t_long`. Reporting both doubles the cell count with zero
information, inflates the apparent search size, and — if dispersion is computed
over the mirrored set — counts the polarity location shift as variance. That
accounted for **63% of a claimed "over-dispersed by 42%" result.**


**17. Pivot-based patterns need a stated, swept confirmation lag - and a
deliberate-lookahead control to prove it.** A swing high is only knowable k bars
after it prints. Entering at the pivot bar instead of pivot+k inflates the
apparent edge by **+3.4 to +6 t-units** and **+34.6 percentage points of sigma
capture**, lifting win rates from 52.6% to 71.1%. At k=3, zero of 464 pattern
cells clear their bar causally and **132 clear it with lookahead**. Worse, the
bias hides in the pivot *definition*: a non-strict right-hand comparison
(`ctr >= right.max()`) admits later equal-or-higher bars, and collapsing runs of
same-type pivots by keeping the more extreme one consults the future. That one
detail moved a placebo from t=+1.35 to **t=-0.13**. Always run the
hindsight variant alongside and publish the gap.


**18. Never benchmark a fixed-size strategy against a compounding one.**
Buy-and-hold reinvests every dollar. A simulation that holds position size
constant is handicapped on the way up and over-leveraged on the way down, and
the gap is large enough to invert a conclusion. In study 26 a fixed $100-margin
run showed momentum losing to buy-and-hold 2.29x vs 4.90x; the identical signal
sized proportionally **beat** it, 20.3% vs 19.9% CAGR at -63% vs -83%
drawdown. Match the sizing rule to the benchmark before comparing, and state
which rule is in force.


## Data sources established

| Source | What | Coverage |
|---|---|---|
| Binance REST klines | OHLCV + taker-buy volume | full history, all intervals |
| `data.binance.vision` **bookDepth** | depth at ±0.2/1/2/3/4/5% every 2.5s | 730+ days, to T−2 |
| `data.binance.vision` **aggTrades** | every trade, ms, aggressor side | 730+ days, ~740k rows/day |
| Deribit DVOL | 30-day implied vol index | back to 2021-03 |
| Deribit chain | full option surface, bid/ask/IV/greeks | live |
| Binance futures | funding, OI, L/S ratios | funding full, OI 30d REST |
| Yahoo Finance | NQ=F, YM=F, ES=F, indices | 60d intraday, years daily |

`bookTicker` (tick-by-tick best bid/ask) was **discontinued ~2024-03** and is
not available for recent dates. Kaggle L2 sets exist but are 12 days each —
too short to survive the corrections above.

## What is still untested

- **ETH/BTC relative value.** The last untested idea from the original list. A ratio is closer to stationary than either leg,
  so spread reversion may be large relative to costs. Naturally frequent.
- **Option term-structure spread.** Falls directly out of trap #4: 1-day IV at
  0.78x DVOL while 7-day is 1.01x. If persistent, a calendar spread trades the
  differential rather than a price move.
- **ETF flow — ANSWERED, negative.** See study 19.
- **Order flow at levels — ANSWERED, negative.** See study 17.
- **Crowding / consensus indicators.** PARTLY answered: positioning (crowd vs
  top-trader L/S, and their divergence) predicts nothing at any horizon from
  1h to 3d — the crowd is neither right nor wrong. What is untested is whether
  *indicator consensus* (many published signals agreeing) creates exploitable
  positioning. Given study #15, expect independence to be the obstacle again.
- **Maker-only execution.** The single lever that moves every result at once.
  Several −0.05% figures go to roughly zero at 0.02% fees. Untested because it
  is an infrastructure question, not a research one.

## Code

All code lives in `../scalp_lab/`.

```
data.py      klines fetch + cache, spot delta
micro.py     bookDepth + aggTrades from the Binance archive
signals.py   causal regimes, causal levels, sweep-reclaim setups
regimes.py   ADX, Efficiency Ratio, Choppiness, BB width, variance ratio, Hurst
anchors.py   PDH/PDL/PWH/PWL/PWO/PMO, session levels, value area
pullback.py  drawdown-from-rolling-high states and excursion stats
cascades.py  cascade detection (price move + OI drop), stop-level mapping
barrier.py   first-passage labelling and univariate feature screen
carry.py     funding history, DVOL history, realised vol
leadlag.py   cross-correlation, Granger, OLS
backtest.py  path simulation, management schemes, trade stats
study.py     parameter sweep, out-of-sample confirm, Bonferroni, fee curve
```

Runners are `run_*.py`, each executable as `python -m scalp_lab.run_<name>`.

## Housekeeping

`cache/` holds ~3.2 GB: 90 days of aggTrades (tick data), 1,177 days of 5m OI
metrics, 477 days of real liquidation snapshots, and kline caches. The
aggTrades bulk is the deletable part if not extending the order-flow work;
the metrics and liquidation caches are small and worth keeping.

37 runner scripts (`run_*.py`). Each is standalone and re-runnable; the
expensive ones cache to `cache/`.


## Data-loader bugs found 2026-09-10 (fixed in micro.py)

Both were silent and both would have produced a confident wrong answer.

1. `agg_trades` built its cache key as `agg_{symbol}_{day}.pkl` with **no
   market**, so a `market="spot"` request returned cached FUTURES data. Any
   spot-vs-perp comparison would have compared perp with itself. No existing
   runner called it with a non-default market, so prior findings are unaffected.
2. Binance **spot** aggTrades use MICROSECOND timestamps; futures use
   milliseconds. Joining them without normalising puts spot bars 1000x out.

## Consolidated view

[STATUS.md](STATUS.md) — 30-candidate comparison table, full indicator inventory
by kind with data-availability limits, and the strategy-archetype mapping. Built
from 385 catalogued effects.

## Test ledger

Kept so a Bonferroni correction can be applied honestly if anything is ever
selected out of this search. Running a large number of variants is not itself a
problem when they all come back empty — the correction matters at the moment a
winner is chosen.

| block | tests |
|---|---|
| studies 1-15 | see individual docs |
| Williams %R (study 16) | ~216 + 18 |
| order arrival at levels (17) | 28 |
| spot sells (18) | 16 |
| ETF flow (19) | 16 |
| vol premium decomposition (20) | 4 strikes x 6 years + 3 adversarial re-implementations |
| passive execution (21) | 3 independent implementations x 5 queue rungs x 5 distances x 5 resting times |
| S-005 matrix (22) | **1,587** across 5 families + 323 frequency cells + 33 reproduction |
| classic TA sweep (23) | **7,908** populated cells + ~3.6M null-calibration evaluations |
| chart patterns (24) | **19,660** de-duplicated causal cells + matched lookahead pairs |
| absorption & rejection (25) | 348 configs, 202 cells across 2 runs |
| leverage simulation (26) | 12 account paths x 2 fee modes x 3 concurrency limits |

## Pine fixes 2026-09-10

Both defects from `btc_study/ADX_REGIME_FLIP_REVIEW_2026-09-10.md` confirmed with
line numbers and fixed. Originals untouched; fixes are `*_fixed.pine`.

| file | bug 1 (stop absent on first held bar) | bug 2 (`cur_dir` desync) |
|---|---|---|
| `adx_v2.pine` | CONFIRMED, lines 114-121 | CONFIRMED |
| `adx_v3.pine` | CONFIRMED, lines 151-158 | CONFIRMED |
| `adx_emax_unified_v1.pine` | CONFIRMED, lines 144-147 (milder: its own knock-out fires, but at the bar CLOSE not the stop price) | n/a |

Mechanism: `process_orders_on_close=true` with recalculation-after-fill off means
`strategy.position_size` is still zero when the guarded `strategy.exit` block
runs on the entry bar, so the stop is first submitted a bar late and **the entire
first held bar is unprotected**. Fix submits the exit in the same script
execution as the entry via two bar-local flags; the original block is left
byte-identical so every later bar behaves exactly as before.

Verified by an independent reviewer through TradingView's `pine_check`:
**compiled, 0 errors, 0 warnings.**

**Expect the backtests to get WORSE.** New stop-outs appear that never existed,
and at 100%-of-equity compounding a single changed 2011-2013 outcome re-bases the
whole curve. Old and new headline numbers are not comparable; only the
trade-by-trade ledger is.


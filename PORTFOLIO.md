# P-300 Aggressive 2.0 — Portfolio Composition

What the bot trades, when each sleeve fires, what leverage it uses, and how the
pieces compose. All percentages are **fractions of total capital** unless stated
otherwise.

> Variant ID: `p300_aggressive_v2_v1_0` · Status: paper-only.

---

## 1. Overview

The bot dispatches **7 top-level sleeves** per minute through
[strategies/orchestrator.py](strategies/orchestrator.py). One of them
(`TIMING_ANOMALIES`) is a meta-sleeve that fans out internally to **8
calendar/clock substrategies** — so there are **14 distinct signal paths** in
total. All sleeves write to the same `trades` table
(`execution_mode='paper'`), tagged by substrategy name where applicable;
realized PnL is the trade-ledger sum (no parallel theoretical-PnL track since
the 2026-05-10 live/sim refactor).

All sleeves are **equal at the orchestrator level**. Each sleeve declares its
weight, leverage, and gating contract; per-tick allocation is dynamic per
`(regime, sleeve)` via [`strategies/support/allocation.py`](strategies/support/allocation.py).
There are no Core/Tactical tiers; the dispatch order in `spec.composition` is
first-come-first-served on the margin pool and conflict resolution.

> **Stop-loss semantics.** All per-sleeve `stop_loss_pct` values are
> interpreted as **price-move** percentages by default — a 10% stop at k=5×
> triggers when price moves 10%, after which the trade has lost 50% of margin.
> Set `P300_STOP_SEMANTICS=margin` to interpret the same numbers as margin-loss
> caps. See [strategies/support/risk_config.py](strategies/support/risk_config.py).

---

## 2. Sleeve roster

| Sleeve | Pre-lev alloc | Leverage | Asset | Direction | Hold |
|---|---|---|---|---|---|
| [S-003 ADX](strategies/sleeves/adx/signal.py) | 15% | 5× | BTC | LONG / SHORT | days–weeks |
| [S-078 Carry](strategies/sleeves/carry/signal.py) | 8% | 5× | BTC (delta-neutral) | n/a | days |
| [JPLUS_EMA_BTC](strategies/sleeves/ema/signal.py) | regime-keyed | vol-lev | BTC | LONG / SHORT | continuous |
| [JPLUS_ETH_DAILY](strategies/sleeves/eth_daily/signal.py) | regime-keyed | vol-lev | ETH | LONG | continuous in bull |
| [SHORT_SQUEEZE](strategies/sleeves/short_squeeze/signal.py) | regime-keyed | regime-keyed | BTC | LONG | scalp (15m signal, fixed R) |
| [AI_QUANT](strategies/sleeves/ai_quant/signal.py) *(experimental, default-OFF)* | 2% × conviction | 3× | BTC | LONG / SHORT / FLAT | LLM-discretionary |
| [**TIMING_ANOMALIES**](strategies/sleeves/timing_anomalies/) (meta) | sum-of-substrategies | per-sub | BTC + ETH | mixed | per-sub |
| &nbsp;&nbsp;&nbsp;↳ [FOMC](strategies/sleeves/timing_anomalies/internal/fomc/signal.py) | 5% | 10× | BTC | LONG | ~10.5h (FOMC days) |
| &nbsp;&nbsp;&nbsp;↳ [THU_BEAR](strategies/sleeves/timing_anomalies/internal/thu_bear/signal.py) (S-096 V4) | 6% (3% BTC + 3% ETH) | 5× | BTC + ETH | SHORT | 24h (Thu) |
| &nbsp;&nbsp;&nbsp;↳ [PDO_L_RF](strategies/sleeves/timing_anomalies/internal/pdo/signal.py) (S-102) | 9% (4.5/asset) | 1× | BTC + ETH | LONG | 24h |
| &nbsp;&nbsp;&nbsp;↳ [CPR](strategies/sleeves/timing_anomalies/internal/cpr/signal.py) (S-101) | 5% (2.5/asset) | 1× | BTC + ETH | LONG | ≤15 days |
| &nbsp;&nbsp;&nbsp;↳ [R4_BTC](strategies/sleeves/timing_anomalies/internal/r4/signal.py) | regime-keyed | 2.5× inner × vol-lev | BTC | LONG | 12h (Mon 06→18 UTC, wk1-2) |
| &nbsp;&nbsp;&nbsp;↳ [R4_ETH](strategies/sleeves/timing_anomalies/internal/r4/signal.py) | regime-keyed | 2.5× inner × vol-lev | ETH | LONG | 24h (Tue 20→Wed 20 UTC, wk1-2) |
| &nbsp;&nbsp;&nbsp;↳ [R4_BTC_V2](strategies/sleeves/timing_anomalies/internal/r4/signal.py) | regime-keyed | 2.5× inner × vol-lev | BTC | LONG | 10h (Wed/Fri 04→14 UTC, wk1-2) |
| &nbsp;&nbsp;&nbsp;↳ [R4_ETH_V2](strategies/sleeves/timing_anomalies/internal/r4/signal.py) | regime-keyed | 2.5× inner × vol-lev | ETH | LONG | 10h (Wed/Fri 04→14 UTC, wk1-2) |

Allocations come from [`strategies/support/allocation.py`](strategies/support/allocation.py)'s
`WEIGHT_TABLE`. Rows that don't vary by regime (S-003, S-078, AI_QUANT, and the
6 timing-substrategy rows mapped via `ALLOCATOR_KEY`) hold the same value
across all four regimes. The 6 J+ rows (R4 family + EMA_BTC + ETH_DAILY) carry
per-regime values rescaled at lookup time by `CORE_ALLOC_CAP = 0.50` — see §4.3.

---

## 3. Per-sleeve detail

Signal / Entry / Exit / Edge thesis / Caveat for each top-level sleeve.
TIMING_ANOMALIES's 8 substrategies live in §3.7.

### 3.1 S-003 ADX — Trend-flip on BTC

- **Signal**: 14-period ADX crosses 25 from prior compression (<20 in last 20 bars). Direction: LONG when close > EMA(50), SHORT when close < EMA(50). LONG-only trend filter: additionally requires close > EMA(150).
- **Entry**: at the crossover bar. Stops out at −2% spot (10% of size after k=5×).
- **Exit**: opposite ADX flip, OR stop loss, OR trend exhaustion.
- **Edge thesis**: catches medium-term trends in BTC; takes the loss when trend reverses.

### 3.2 S-078 Carry — Delta-neutral funding harvest

- **Signal**: 7-day average BTC perp funding > 0%. Entry opens spot-long + perp-short of equal notional → market-neutral.
- **Income**: collects funding payments every 8h while the perp side is short.
- **Exit**: 3 consecutive negative funding days, or scheduled time-stop.
- **Edge thesis**: structurally positive funding in bullish regimes is paid for free if you can hedge cheaply. P&L is dominated by funding accrual, not price moves.

### 3.3 JPLUS_EMA_BTC — Weekly EMA crossover position-flip

- **Signal**: EMA(5) vs EMA(21) on **weekly** BTC closes. LONG when EMA5 > EMA21, SHORT when EMA5 < EMA21. Position state (`ema_p` in the sizing pipeline): +1 LONG, −1 SHORT, 0 warmup.
- **Entry**: at the next weekly candle's open after a cross. Strict T+1, no same-bar entry.
- **Exit**: at the next weekly candle's open after the reverse cross. The sleeve is always in one of LONG / SHORT — no idle-to-cash state after the first crossover.
- **Daily contribution**: `ema_p × today's BTC daily return × regime_weight`. An EMA-LONG day with BTC up +2% adds +2% × regime_weight; an EMA-SHORT day adds −2% × regime_weight.
- **Cost model**: 10bp round-trip fee + 5bp slippage + funding accrual (since 2026-05-13 — pre-fix the sleeve incorrectly ran zero-funding on multi-week perp holds; ~4.5%/yr funding was previously invisible).
- **Edge thesis**: medium-term trend follower on BTC. Captures multi-week directional moves; pays the spread/fee on whipsaws.
- **Active in**: every regime (regime weights 0.30 in mild_bull / uncertain / bear, 0.50 in strong_bull).

### 3.4 JPLUS_ETH_DAILY — Passive ETH long, bull-regimes only

- **Signal**: none — not a discretionary signal sleeve. It's a "long ETH at regime-weighted size" position.
- **Entry**: the day the regime classifier flips into `strong_bull` or `mild_bull`.
- **Exit**: the day the regime classifier flips out of bull.
- **Daily contribution**: `ETH daily return × regime weight` (0.20 strong_bull, 0.10 mild_bull, 0 otherwise).
- **Cost model**: 10bp fee + 5bp slip + funding accrual (since 2026-05-13).
- **Edge thesis**: pure long-ETH-beta during bull regimes. ETH outperforms BTC on the way up; this gives the portfolio that exposure when conditions are constructive.

### 3.5 S-105 SHORT_SQUEEZE — Sweep + CVD-divergence long, intraday

- **Signal**: bar-level LONG trigger. All four conditions must agree on a 15m bar:
  1. Session gate — London or NY (07:00–21:00 UTC).
  2. Asia-grind macro — slow drift up overnight (criteria in [strategies/sleeves/short_squeeze/config.py](strategies/sleeves/short_squeeze/config.py)).
  3. Sweep — current bar takes out the prior session low.
  4. Perp/spot CVD divergence — spot CVD positive while perp CVD negative on the sweep bar.
- **Entry**: at the sweep bar close.
- **Exit**: fixed-R target, fixed-R stop, or session-end time-stop. No trailing.
- **Edge thesis**: forced short-covers at swept lows produce predictable squeezes when perp/spot order-flow disagrees. Mechanistically grounded — first such sleeve in the portfolio. See [studies/notebooks/short_squeeze_sessions/strategy_backtest.ipynb](studies/notebooks/short_squeeze_sessions/strategy_backtest.ipynb).

### 3.6 AI_QUANT — Discretionary LLM trader (experimental, default-OFF)

- **Status**: Phase-1 experiment (added 2026-05-08). Default-disabled via the `AI_QUANT_ENABLED` env var ([`.env.example`](.env.example) ships with `false`); when unset, [strategies/sleeves/ai_quant/signal.py:_kill_switch_on()](strategies/sleeves/ai_quant/signal.py) short-circuits to `status='disabled'` and no LLM call is made.
- **Allocation**: 2% of capital. Raise to 5% only after 60+ days of forward paper PnL net of API cost.
- **Leverage**: 3×. **Asset**: BTC perp. **Direction**: LONG / SHORT / FLAT, chosen daily by the model.
- **Signal**: an Anthropic Opus 4.7 tool-use loop runs once per UTC day in a 10-minute window (00:05–00:15 UTC). Every minute, four cheap gates are evaluated by the [orchestrator](strategies/orchestrator.py) before any LLM call: kill-switch / time-window / per-day-already-fired / daily-cost-cap. On the one tick that passes all four, the service builds a context bundle (regime, F&G, funding, recent volatility, open positions), renders a 90-bar daily chart, and runs the decision loop with server tools enabled.
- **Output schema**: the model returns `direction ∈ {LONG, SHORT, FLAT}`, `conviction_0_100`, `time_horizon_days`, and `key_drivers[]`. A **conviction floor** forces `conviction < 30` to FLAT regardless of the model's stated direction.
- **Sizing**: `allocation_pct = weight_pct × (conviction / 100)`, capped at the 2% weight. Conviction-50 LONG sizes to 1% at 3× leverage; conviction-100 LONG sizes to the full 2%.
- **Reconciliation**: each day's decision is reconciled against any open AI_QUANT position — open / hold / close / flip. No mid-day scaling in v1.
- **Exit**: no fixed time-stop. Held until the next day's decision flips them or sets FLAT, or until the configured 10% price-move stop fires.
- **Cost cap**: $5/day default API spend ceiling (`AI_QUANT_DAILY_COST_CAP_USD`); when exceeded, the gate returns `cost_capped` and no decision runs that day.
- **Audit trail**: every fire writes a row to the journal ([strategies/sleeves/ai_quant/journal.py](strategies/sleeves/ai_quant/journal.py)) with decision payload, tool calls, token usage, cost, and resulting trade action — including ERROR rows when context-build / chart-render / API fail. The journal also mirrors to human-readable markdown at `data/ai_quant_archive/{date}_{variant}_{asset}_{decided}_id{N}.md`.
- **Backtest behavior**: `params.deterministic=False` is consumed by [backtest_runner.py](backtest_runner.py) to **skip** AI_QUANT on historical replay — the LLM is non-deterministic. AI_QUANT contributes nothing to backtest figures.
- **Edge thesis**: a discretionary trader with broad context (macro, sentiment, microstructure, chart) may catch regime shifts that the rule-based sleeves are structurally blind to. Whether the model can beat its own API cost net of slippage is the open question.

### 3.7 TIMING_ANOMALIES — Meta-sleeve over 8 calendar/clock substrategies

A single orchestrator-level dispatcher that fans out per-tick to 8 substrategies
sharing one allocation budget. Substrategy code lives under
[strategies/sleeves/timing_anomalies/internal/{fomc, thu_bear, pdo, cpr, r4}/](strategies/sleeves/timing_anomalies/).
The meta-sleeve is the **sole** entry point — there is no per-substrategy
dispatcher at the orchestrator level. Each substrategy still tags its trades
with its own name (`FOMC`, `THU_BEAR`, etc.) in `trades.strategy`, so
per-substrategy attribution is preserved end-to-end.

Per-substrategy weight resolution: the meta-sleeve looks up the substrategy's
legacy `strategy_id` via `ALLOCATOR_KEY`
([strategies/sleeves/timing_anomalies/internal/__init__.py](strategies/sleeves/timing_anomalies/internal/__init__.py))
and calls `allocation.get_weight_pct(legacy_id, regime)`. Without this hop,
every substrategy would silently fall back to its static composition weight,
defeating regime-adaptive sizing.

#### 3.7.1 FOMC — Long into Fed announcement, regime-filtered

- **Signal**: only fires on FOMC dates (8/year, from `scheduled_events`).
- **Entry**: T−10h before announcement (08:00 UTC, or 09:00 UTC for EST meetings).
- **Exit**: T+0.5h after (when Powell starts speaking).
- **Filter rule** (combined regime + sentiment + Polymarket):
  - HARD SKIP if `expected_action == 'cut_25bp'` (historical 20% win rate).
  - HARD SKIP if F&G bucket == `extreme_greed` (40% win rate).
  - HARD TRADE if F&G == `extreme_fear` AND phase ≠ `mid_hold` (8/8 historical wins).
  - SKIP if `phase == 'mid_hold'` (25% win rate).
  - TRADE otherwise (peak_hold / hiking / zirp_hold / cutting in good context).
- **Inputs**:
  - **Phase**: from [data/sources/fed_funds.py](data/sources/fed_funds.py) — NY Fed XML, classified as `zirp_hold / hiking / peak_hold / cutting / mid_hold`.
  - **F&G**: from [data/sources/sentiment.py](data/sources/sentiment.py) — alternative.me daily Fear & Greed.
  - **Expected action**: from [data/sources/polymarket.py](data/sources/polymarket.py) — implied per-meeting cut probability from the "How many Fed rate cuts in 2026?" market.
- **Audit trail**: every FOMC date writes a row to `fomc_observer` in `data/databases/prod.db` with the decision + reason + inputs, even when the decision is SKIP.
- **Edge thesis**: short-window event trade. Drift up into the announcement, partial fade after. Filter weeds out the regimes where this fails.
- **Caveat**: filter was tuned on the same 52-event historical cohort the in-sample backtest is drawn from. Going-forward edge unproven.

#### 3.7.2 THU_BEAR (S-096 V4) — Calendar-driven Thursday short

- **Signal**: Thursdays only. V4 event filter — trade only if Thursday is within ±1 day of CPI or NFP, AND not within ±1 day of OPEX. Prior-day regime (from [regime_tactical.py](strategies/support/regime_tactical.py)) must be `bear_trend / sell_off / chop` (not `bull_trend`).
- **Entry**: Thursday 00:00 UTC. SHORT BTC + ETH equally.
- **Exit**: Friday 01:00 UTC, or stop-loss at −1% spot (5% margin at k=5×).
- **Edge thesis**: weekly Thursday selling pressure during macro-event-adjacent periods, conditioned on being already in a non-bull regime.
- **Caveat**: V4 event filter was derived post-hoc from V3's Thursday attribution — in-sample selection bias applies.

#### 3.7.3 PDO_L_RF (S-102) — Pullback Daily Open Retouch Long

- **Signal**: after a daily gap-down ≥ 2%, wait for the price to retouch the prior daily open (PDO). Regime must not be deeply bearish (`regime_threshold_pct: −10%` recent peak DD).
- **Entry**: at the PDO retouch.
- **Exit**: scheduled time-stop, or stop-loss.
- **BTC-LONG cross-sleeve cap**: PDO + CPR combined BTC-LONG allocation pre-leverage is capped at 15% by [strategies/support/risk_caps.py](strategies/support/risk_caps.py).
- **Edge thesis**: gap-fills are a known intraday phenomenon in crypto. Mean-reversion long after a down-gap.
- **Caveat**: parameters (gap %, regime threshold) were swept in upstream research without visible walk-forward CV — data-snooping exposure.

#### 3.7.4 CPR (S-101) — Contrarian Positioning Reversal

- **Signal**: all four conditions must agree:
  1. 3-day mean funding rate < 20-percentile of trailing window.
  2. LSR (long-short ratio) < 20-percentile of trailing window.
  3. Daily close > EMA(20).
  4. EMA(20) > EMA(50).
- **Setup logic**: persistent negative funding + crowd is short + price still in uptrend → expected short squeeze.
- **Entry**: at the next 1m bar after signal trigger.
- **Exits**: target at +2.93% (BB upper band), stop at −5%, or 15-day time-stop.
- **BTC-LONG cross-sleeve cap**: shared with PDO — see §3.7.3.
- **Edge thesis**: contrarian-position-with-trend setup. Theoretically high-quality but historically thin sample (12 BTC + 9 ETH events from upstream).

#### 3.7.5 R4 family — Calendar-window intraday longs

Four substrategies sharing the same machinery (fixed-window long, inner-leverage
2.5×, vol-percentile gate from [strategies/support/gate.py](strategies/support/gate.py)).
The vol gate fires when trailing 30-day BTC realized vol is in the top 25% of
the 365-day distribution — strictly T−1, no look-ahead. Fires ≈30% of days.

| Substrategy | Window | Asset | Entry → Exit |
|---|---|---|---|
| R4_BTC | Mon wk1-2 | BTC | 06:00 UTC → 18:00 UTC (12h) |
| R4_ETH | Tue wk1-2 (Wed day ≤ 14) | ETH | Tue 20:00 UTC → Wed 20:00 UTC (24h) |
| R4_BTC_V2 | Wed + Fri wk1-2 | BTC | 04:00 UTC → 14:00 UTC (10h) |
| R4_ETH_V2 | Wed + Fri wk1-2 | ETH | 04:00 UTC → 14:00 UTC (10h) |

Each fire is LONG. Per-trade return: `(price_at_exit − price_at_entry) /
price_at_entry − 10bp RT cost`. Inner leverage: **2.5×** normally, **1.0×**
when the vol gate fires.

**R4_BTC V1 history.** Was Mon+Wed before 2026-05-08; the calendar-window
study in [studies/notebooks/r4_study/](studies/notebooks/r4_study/) found
Wednesday responds better to a 04→14 UTC window than V1's 06→18, so Wed was
moved to R4_BTC_V2 along with Friday — historically the strongest single
weekday cell on BTC (NFP-anticipation effect). The same Wed+Fri 04→14 cell
extracts comparable signal on ETH (R4_ETH_V2).

**Edge thesis (R4 family).** Calendar-anchored intraday windows that have
been empirically positive across the post-Binance-perp + post-ETF eras. The
V2 grid-search (402 fires, t = +4.6 in-sample, +2.5 OOS walk-forward) is
defensible; post-ETF era is short (~2.3y), so live monitoring via
[strategies/support/strategy_health.py](strategies/support/strategy_health.py)
expectancy decay watches for regime change.

---

## 4. Sizing pipeline

The portfolio-wide sizing pipeline resolves a per-tick fraction-of-capital
notional for any sleeve that opts in. Three layers, applied in order:

```
                 ┌──────────────────────────────────────┐
                 │   regime classifier (T-1 inputs)     │
                 │   strong_bull / mild_bull /          │
                 │   uncertain / bear                   │
                 └──────────────┬───────────────────────┘
                                │
                                ▼
                 ┌──────────────────────────────────────┐
                 │   Layer 1 — regime weight            │
                 │   allocation.WEIGHT_TABLE[sleeve]    │
                 │   [regime]; rescaled by CORE_ALLOC_  │
                 │   CAP for the J+ sizing rows.        │
                 └──────────────┬───────────────────────┘
                                │
                                ▼
                 ┌──────────────────────────────────────┐
                 │   Layer 2 — inner leverage           │
                 │   R4 family only: 2.5× / 1.0× via    │
                 │   vol-percentile gate (gate.py).     │
                 │   Other sleeves: 1.0× (pass-through).│
                 └──────────────┬───────────────────────┘
                                │
                                ▼
                 ┌──────────────────────────────────────┐
                 │   Layer 3 — vol-target outer         │
                 │   leverage (voltarget.py)            │
                 │   floor 0.5×, regime-capped 1.5–3.0× │
                 └──────────────┬───────────────────────┘
                                │
                                ▼
              effective notional = capital × layer1 × layer2 × layer3
```

For sleeves whose `WEIGHT_TABLE` row is regime-independent (S-003, S-078, the
6 timing-substrategy rows, AI_QUANT), Layer 1 returns the same value across
all four regimes. Inner leverage is 1× for everything except the R4 family
(only consumers of the gate).

### 4.1 Regime classifiers

Two parallel classifiers run on the same BTC daily series; each sleeve consumes
whichever fits its trade thesis. Both are strictly T−1 (look-ahead safe).

**J+ classifier** ([strategies/support/regime_jplus.py](strategies/support/regime_jplus.py)) — 4 modes, consumed by `allocation.py` and the J+ sizing-pipeline rows:

| Mode | Trigger |
|---|---|
| **strong_bull** | close > EMA(50) AND close > EMA(20) AND m30 > 0 AND m7 > 0 |
| **mild_bull** | close > EMA(50) AND (m30 > 0 OR close > EMA(20)) |
| **bear** | close < EMA(50) AND m30 < 0 |
| **uncertain** | otherwise; or peak-DD > 5% while bullish; or LS circuit-breaker active |

Two overrides:
- **LS circuit breaker** — 7-day LSR delta < −15 forces `uncertain` for the next 7 calendar days.
- **Peak-DD override** — close > 5% off trailing peak demotes any bullish label to `uncertain`.

**Tactical classifier** ([strategies/support/regime_tactical.py](strategies/support/regime_tactical.py)) — 4 modes, consumed by THU_BEAR's V3 prev-day filter and PDO_L_RF's `regime_threshold_pct` skip:

| Mode | Trigger |
|---|---|
| **bull_trend** | 50d SMA 10-day slope > +0.5% of price, RV not extreme |
| **bear_trend** | 50d SMA 10-day slope < −0.5% of price, RV not extreme |
| **chop** | \|slope\| ≤ 0.5% (dead-band) |
| **sell_off** | RV percentile ≥ 75th AND close < 50d MA AND slope < 0 |

The two vocabularies coexist because they came from different research lines.
Unifying them is on the backlog but isn't load-bearing.

### 4.2 Per-regime allocation weights

Pulled at lookup time from [`allocation.get_weight_pct(strategy_id, regime)`](strategies/support/allocation.py)
and injected as `_effective_weight_pct` into every sleeve dispatch.

**J+ sizing-pipeline raw weights** (`REGIME_WEIGHTS_FULL` in [strategies/support/jplus_inputs.py](strategies/support/jplus_inputs.py)):

| Mode | EMA_BTC | ETH_DAILY | R4_BTC | R4_ETH | R4_BTC_V2 | R4_ETH_V2 | Raw sum |
|---|---|---|---|---|---|---|---|
| **strong_bull** | 0.50 | 0.20 | 0.15 | 0.15 | 0.075 | 0.075 | 1.15 |
| **mild_bull**   | 0.30 | 0.10 | 0.20 | 0.30 | 0.10  | 0.15  | 1.15 |
| **uncertain**   | 0.30 | 0.00 | 0.30 | 0.40 | 0.15  | 0.20  | 1.35 |
| **bear**        | 0.30 | 0.00 | 0.00 | 0.00 | 0.00  | 0.00  | 0.30 |

`CORE_ALLOC_CAP = 0.50` rescales every row at lookup time so the per-regime
sum across these 6 rows never exceeds 0.50. When raw sum ≤ 0.50 (bear regime),
weights pass through unchanged. When raw sum > 0.50, every entry is multiplied
by `0.50 / raw_sum` — relative weighting between rows is preserved.

**Capped weights** — what `today_inputs()` actually returns:

| Mode | EMA_BTC | ETH_DAILY | R4_BTC | R4_ETH | R4_BTC_V2 | R4_ETH_V2 | Sum |
|---|---|---|---|---|---|---|---|
| **strong_bull** | 0.217 | 0.087 | 0.065 | 0.065 | 0.033 | 0.033 | **0.500** |
| **mild_bull**   | 0.130 | 0.043 | 0.087 | 0.130 | 0.043 | 0.065 | **0.500** |
| **uncertain**   | 0.111 | 0.000 | 0.111 | 0.148 | 0.056 | 0.074 | **0.500** |
| **bear**        | 0.300 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | **0.300** |

The bot historically spent ~62% of time in `uncertain` and ~25% in `bear`, so
on most days R4_BTC and R4_ETH are doing the real work when they fire, and
EMA_BTC carries the rest.

**Non-J+ rows** (regime-independent in `WEIGHT_TABLE`):

| Sleeve | Weight (all regimes) |
|---|---|
| S-003 ADX | 0.15 |
| S-078 Carry | 0.08 |
| THU_BEAR (S-096) | 0.06 |
| PDO_L_RF | 0.09 |
| CPR | 0.05 |
| FOMC | 0.05 |
| AI_QUANT | 0.02 |

SHORT_SQUEEZE is not in `WEIGHT_TABLE` yet — it sizes from its own config
until regime-keyed weights are calibrated.

### 4.3 Inner-leverage gate (R4 family only)

The R4 family stacks an inner amplifier on top of its raw windowed return:

| State | Multiplier | Reason |
|---|---|---|
| Normal day | **2.5×** | R4 is a sized sleeve within the J+ sizing pipeline |
| **Vol-percentile gate fired** | **1.0×** | de-lever in high-vol regimes |

The gate ([strategies/support/gate.py](strategies/support/gate.py)) fires when
trailing 30-day BTC realized vol is in the top 25% of the 365-day distribution.
Strictly T−1; fires ≈29.7% of days in the historical window.

### 4.4 Vol-target outer leverage

After Layer 1 × Layer 2 produce a per-sleeve daily contribution, the strategy
return is multiplied by a vol-target outer leverage `lev`
([strategies/support/voltarget.py](strategies/support/voltarget.py)):

```
lev = clamp(LEV_FLOOR=0.5×,  regime_cap,  TARGET_VOL=50% / realized_vol_30d_ann)
```

Per-regime caps:

| Mode | Max leverage |
|---|---|
| strong_bull | 3.0× |
| mild_bull | 2.5× |
| uncertain | 2.0× |
| **bear** | **1.5×** |

Targets **50% annualized volatility** for the strategy. When realized vol is
HIGHER, leverage drops. When LOWER, leverage rises (capped by regime).
Floored at 0.5× — never zero.

### 4.5 Worked example — R4_ETH on a Tuesday

R4_ETH firing on a Tuesday in `uncertain` regime, gate not fired,
`lev = 2.0`. Note the regime weight is the **capped** value (0.148 = raw 0.40
× 0.50/1.35):

```
final_contrib_to_daily_return =
    raw_R4_ETH_return × inner_lev(2.5) × capped_weight(0.148) × vol_target(2.0)
  = raw × 0.74
```

So R4_ETH's effective leverage on that day is **0.74× of the raw spot move**,
applied to **~15% of capital** (the capped R4_ETH allocation in `uncertain`).

For a continuous sleeve like EMA_BTC, the inner-leverage layer is 1.0×, so
the math is `raw_return × capped_weight × vol_target` — same pipeline, one
fewer multiplier.

---

## 5. Cross-sleeve coordination

Sleeves own their own decision logic. Everything that has to look across
sleeves — sizing, gating, margin, directional conflict, signal pooling, trade
ordering — lives in [strategies/support/](strategies/support/) and is injected
into each sleeve's dispatch as `sleeve_cfg["_effective_*"]` fields by
[strategies/orchestrator.py](strategies/orchestrator.py).

### 5.1 Two-phase dispatch + reconcile

Every sleeve exposes two callables:

- `try_decide_for_variant(variant, sleeve_cfg) → (list[Intent], status_dict)` — reads inputs, evaluates signals, runs side-effect bookkeeping (SL sweeps, scheduled closes, daily rebalances, FLIPs), and returns the entry intents it would open if approved. **No fresh-open side-effects** at this phase.
- `execute_for_variant(variant, sleeve_cfg, intent) → status_dict` — opens the trade described by an `Intent` returned from the reconcile pass.

`Intent` ([strategies/support/dispatch.py](strategies/support/dispatch.py))
is a frozen dataclass: `asset`, `direction`, `allocation_pct`, `leverage`,
`conviction (0–100)`, `priority`, `reason` (dict persisted to `trades.notes`),
`scheduled_exit_dt`.

After every two-phase sleeve has returned intents,
`reconcile_intents()` runs once:

1. **Conviction-weighted signal pooling** (`_pool_concordant_allocations`). Same-(asset, direction) intents have their allocations redistributed so the *total* equals the conviction-weighted average alloc, not the sum. Two sleeves agreeing LONG BTC don't produce 2× exposure; they produce the conviction-weighted-avg sleeve's full size, split by share.
2. **Sort** by `(priority asc, −conviction desc)`. Sleeves declare `priority` in composition (default 100); ties break on conviction.
3. **Per-intent loop**, in priority order:
   - **Directional conflict check** — reject if a higher-priority intent already approved the opposite direction on this asset. Pre-seeded with `existing_directional_opens` from the DB so legacy positions count too.
   - **Margin headroom** — full size if fits; clamp + `approved_reduced` if it fits at ≥50% of intended; reject otherwise.
4. Returns parallel `ReconcileResult` list. The orchestrator calls each sleeve's `execute_for_variant(intent)` only for approved / approved_reduced / approved_pooled results; logs the rest.

CARRY's perp SHORT is excluded from both conflict and pool checks
(delta-neutral collateral). FLAT intents (close-existing) always pass through.

### 5.2 Gating framework

A gate decides whether a sleeve fires at all and, optionally, scales its
leverage. Each gate registers a `(strategy_id, regime, now_utc) → GateDecision(fire, leverage_mult, reason)`
callable in `GATE_REGISTRY` ([strategies/support/gating.py](strategies/support/gating.py)).
The orchestrator looks it up per sleeve per tick and injects the result as
`_effective_gate`.

| Gate | Type | What it does |
|---|---|---|
| R4 vol-gate | leverage modulator | BTC 30d realized vol > 75th-percentile (365d window) → R4 inner leverage 1.0× instead of 2.5× |
| THU_BEAR V4 | binary | CPI/NFP-adjacent Thursday + ex-OPEX → fire; otherwise block |
| FOMC composite | binary | Phase × F&G × Polymarket-cut-prob filter via the `fomc_observer` table |

Walk-forward CV protocol for adding/rebuilding a gate: [GATE_VALIDATION.md](GATE_VALIDATION.md).
The R4 vol-gate is calibrated on BTC vol percentile (not the R4 trade series), so it carries less in-sample selection-bias risk than V4 / FOMC, both of which were derived post-hoc.

### 5.3 Margin headroom

Tracks the variant's gross perp-notional vs cap and tells sleeves how much
room they have ([strategies/support/margin_headroom.py](strategies/support/margin_headroom.py)).
Cap defaults to `2.5 × capital_usdt` via `spec.allocator_notes.gross_notional_target_x`.
Sums `size_usdt` across open paper trades (already-leveraged notional from
`trades.open_paper_trade`) — no double-leveraging.

The reconcile pass (§5.1) enforces margin uniformly across all candidates
per tick. Sleeves themselves no longer call margin helpers inline.

### 5.4 Directional conflict resolver

Catches opposing-direction perp opens on the same asset within a variant —
e.g. ADX wants LONG BTC while THU_BEAR wants SHORT BTC on the same Thursday.
([strategies/support/conflict_resolver.py](strategies/support/conflict_resolver.py)).
Two surfaces:

- `detect_opposing_open(variant_id, asset, direction)` — returns the earliest open trade with opposite direction, or None. Used by the reconcile pass to seed its conflict state with positions opened before reconcile ran.
- `current_directional_opens(variant_id) → {asset: direction}` — one-shot snapshot for the reconcile seed and operator dashboards.

CARRY's perp SHORT is excluded by design (delta-neutral collateral). The
exclusion list is shared with `signal_aggregator.py` and `dispatch.py`'s
reconcile loop.

### 5.5 Signal aggregator (detect-only)

Read-side dual of conflict_resolver — surfaces same-asset, same-direction
stacks (e.g. ADX LONG BTC + AI_QUANT LONG BTC) for audit dashboards.
([strategies/support/signal_aggregator.py](strategies/support/signal_aggregator.py)).
**Does not pool** — that's the reconcile pass (§5.1). Detect-only today; UI
consumers may eventually act on its output.

### 5.6 Portfolio vol-target scalar (opt-in)

Per-tick scalar that re-targets the variant's gross exposure to a documented
annualized-vol budget (default 30%).
([strategies/support/portfolio_vol.py](strategies/support/portfolio_vol.py)).
Computed from realized NAV over a 30-day rolling window:
`scalar = target_vol_annual / realized_vol_annual`, clamped to
`[LEV_FLOOR=0.5, LEV_CAP=3.0]`. Falls back to None (no scaling) when the
variant has < 10 observations of NAV history.

Opt-in per variant via `spec.allocator_notes.use_portfolio_vol`. When True,
the orchestrator multiplies every sleeve's `_effective_leverage` by the
scalar at injection time; the J+ sizing-pipeline rows read
`_effective_vol_scalar` directly as their inner vol-target leverage (not
multiplied through `_effective_leverage`, to avoid double-counting).

---

## 6. Support module ↔ sleeve matrix

Which sleeves are touched by which support module, and how the relationship
flows. "Via orchestrator" means the orchestrator computes the value per-tick
and injects it as a `_effective_*` field into the sleeve's dispatch dict.
"Direct import" means the sleeve module itself imports the support module.

| Support module | Consumed by | Relationship |
|---|---|---|
| [`allocation.py`](strategies/support/allocation.py) | all sleeves | via orchestrator → `_effective_weight_pct` |
| [`gating.py`](strategies/support/gating.py) | R4 family, THU_BEAR, FOMC | via orchestrator → `_effective_gate` |
| [`gate.py`](strategies/support/gate.py) | R4 family (the vol-percentile gate) | via `gating.py` |
| [`voltarget.py`](strategies/support/voltarget.py) | J+ sizing-pipeline sleeves | via `jplus_inputs.today_inputs()` |
| [`portfolio_vol.py`](strategies/support/portfolio_vol.py) | all sleeves (opt-in per variant) | via orchestrator → multiplies `_effective_leverage` |
| [`margin_headroom.py`](strategies/support/margin_headroom.py) | reconcile pass (all sleeves) | via `dispatch.reconcile_intents()` |
| [`conflict_resolver.py`](strategies/support/conflict_resolver.py) | reconcile pass (directional sleeves; CARRY perp leg excluded) | via `dispatch.reconcile_intents()` |
| [`signal_aggregator.py`](strategies/support/signal_aggregator.py) | UI / audit (detect-only) | read-side; no dispatch dependency |
| [`dispatch.py`](strategies/support/dispatch.py) | every two-phase sleeve | `Intent` + `ReconcileResult` contract |
| [`regime_jplus.py`](strategies/support/regime_jplus.py) | J+ sizing pipeline + allocation | via `allocation.current_regime()` |
| [`regime_tactical.py`](strategies/support/regime_tactical.py) | THU_BEAR (V3 prev-day filter), PDO_L_RF (`regime_threshold_pct`) | direct import |
| [`jplus_inputs.py`](strategies/support/jplus_inputs.py) | R4 family + EMA_BTC + ETH_DAILY | `today_inputs()` snapshot (regime + lev + gate + ema_p + weights) |
| [`risk_caps.py`](strategies/support/risk_caps.py) | PDO_L_RF + CPR (cross-sleeve BTC-LONG cap) | direct import |
| [`funding.py`](strategies/support/funding.py) | CARRY (daily sum), CPR (daily mean) | direct import |
| [`price_feed.py`](strategies/support/price_feed.py) | every sleeve at entry/exit | direct import |
| [`trade_db.py`](strategies/support/trade_db.py) + [`strategies/trades.py`](strategies/trades.py) | every sleeve | direct — single writer for `trades` + `trade_adjustments` |
| [`margin_sim.py`](strategies/support/margin_sim.py) + [`margin_check.py`](strategies/support/margin_check.py) | always-on liquidation simulator | via orchestrator (per-tick check on all open trades) |
| [`strategy_health.py`](strategies/support/strategy_health.py) | UI / health.py / bot startup snapshot | read-side; no dispatch dependency |

---

## 7. External data feeds

Cached snapshots of upstream public APIs the bot polls daily.
[`data/sources/binance.py:_refresh_daily_external`](data/sources/binance.py)
runs once per UTC day; sim-mode-aware (no-op when `clock.is_simulated()`).

| Feed | Cache | Consumer |
|---|---|---|
| Crypto Fear & Greed | `prod.db:fear_greed_index` | FOMC composite gate, AI_QUANT context |
| Fed Funds target rate | [`data/archive/fed_funds_target_upper.json`](data/archive/) (parsed from `nyfed_rates.xml`) | FOMC phase classifier |
| Polymarket cut-probability | [`data/archive/polymarket_fed_2026.json`](data/archive/) | FOMC composite gate |
| Binance klines / funding | `prod.db` (btc_1m, eth_1m, cd_funding_rate, cd_spot_binance, ca_long_short_ratio) | every sleeve + regime classifiers |
| News headlines | `prod.db:news_headlines` | AI_QUANT context only |
| CoinDesk derivatives | `prod.db` (cd_open_interest, cd_liquidations, cd_dvol) | AI_QUANT context only |

---

## 8. Risk profile

Per-sleeve historical performance previously lived in this doc through
2026-05-13; the v6 backtest figures predated several material fixes (Jensen-gap
in compound-equity, slippage model, `CORE_ALLOC_CAP`, EMA/ETH_DAILY funding +
fee correction, liquidation simulator) and were removed. Run
[studies/notebooks/full_portfolio_report.ipynb](studies/notebooks/full_portfolio_report.ipynb)
against the live trade ledger for current numbers.

**Concurrent notional bands** (approximate; refresh when next backtest lands):

- Mean ≈80% of capital; P95 ≈120%; max ≈150%.
- The portfolio cross-margins. Half the time the bot has more than 100% notional in positions because multiple sleeves overlap.

**Per-sleeve time-occupancy** (approximate):

| Sleeve | Time in market | Why |
|---|---|---|
| CPR | ~100% | always-on contrarian; multi-asset overlap |
| CARRY | ~90% | always holding while funding regime is positive |
| ADX | ~50% | trend follower; in market roughly half the time |
| THU_BEAR | <10% | Thursdays only, event-filtered |
| PDO_L_RF | ~1% | rare gap-retouch setup |
| FOMC | <1% | 8 days/year × ~10.5h |

FOMC is **time-disjoint** from THU_BEAR (FOMC always Tue/Wed, THU_BEAR
Thursday) and effectively-disjoint from everything else. The R4 family is
calendar-windowed; concurrent firing peaks on Wednesdays (R4_ETH still open
from Tue + R4_BTC_V2 + R4_ETH_V2 firing 04:00–14:00 UTC). The V2 sleeves are
sized at half the V1 regime weights specifically to keep peak Wednesday
concurrent exposure comparable to the pre-V2 baseline.

---

## 9. Methodology caveats

1. **In-sample selection bias.** The Aggressive 2.0 family was chosen from
   {Conservative / Regime-dynamic / Kelly / Aggressive} based on backtest
   performance. Regime weights, R4 windows, and ETH weights were tuned on
   roughly the same era of data. Live forward performance will likely be lower
   than in-sample.

2. **R4 windows-grid selection (2026-05-08).** The live R4 config for
   `JPLUS_R4_BTC` (Mon wk1-2 06→18 UTC, was Mon+Wed) and the V2 sleeves
   (Wed+Fri wk1-2 04→14 UTC) were picked from a 7,500-config grid search over
   (asset × day × week × start-hour × end-hour) using "57 configs that were
   positive in every backtest year." Empirically defensible (t = +4.6
   in-sample over 402 fires; +2.5 OOS walk-forward per
   [studies/notebooks/r4_study/findings.md](studies/notebooks/r4_study/findings.md))
   but post-ETF era (2024-01 onward, ~2.3y) is too short for the post-ETF
   walk-forward to be conclusive. User's decision to run the live config and
   monitor expectancy via [strategy_health.py](strategies/support/strategy_health.py)
   is documented in
   `memory/feedback_r4_post_etf_ride_with_monitor.md`.

3. **Look-ahead protections are real and tested**
   ([tests/test_jplus_lookahead.py](tests/test_jplus_lookahead.py)). The
   upstream ML R4 gate had within-day look-ahead and was REPLACED by a
   rule-based gate. Not every input has been audited at the same depth.

4. **GOLD overlay is dropped.** Upstream P-100 J+ MLgate had a 15–55% GOLD
   allocation as crisis hedge. p300 has no `macro_daily` table and the asset
   isn't wired. The crypto side now stands at 1.0 weight.

5. **FOMC sleeve has only 11 in-sample backtest events** (added 2026-04-30).
   100% in-sample win rate. Bootstrap on the 52-event historical cohort
   (73% win) projects ~12% sleeve max DD at 10× leverage. Live edge unproven;
   first real-time test on the next 6+ FOMC events.

6. **CPR's historical sample is thin** (12 BTC + 9 ETH events upstream) and
   **PDO_L_RF's params** were selected via parameter sweeps without
   walk-forward CV — both carry data-snooping exposure.

7. **THU_BEAR V4 event filter** (CPI/NFP-adjacent, ex-OPEX) was derived
   post-hoc from V3's Thursday attribution. V4 backtest comparisons are
   in-sample; live paper is the first genuine OOS record.

8. **Sharpe / MDD numbers are not deflated for multiple-testing.** No
   bootstrap CI, no Monte Carlo, no White's reality check. Treat point
   estimates as suggestive only. **Sharpe is computed with risk-free rate = 0**
   — `(mean / sd) × √365` in
   [strategy_health.py](strategies/support/strategy_health.py),
   [backtest_runner.py](backtest_runner.py), and the report notebooks. At a
   4–5% Fed funds rate, that overstates Sharpe by ~0.5–0.7. The crypto
   convention is rf = 0; we follow it but note the gap.

9. **Daily-NAV MDD understates intraday DD at 5–10× leverage** in stress
   regimes. Factor this into any risk claim.

10. **Live BTC-LONG cap (skip-if-over) vs simulator cap (proportional
    down-scale) diverge by construction** — live NAV ≠ sim NAV even with
    identical signals.

11. **AI_QUANT is excluded from all backtest figures.** Non-deterministic
    (LLM outputs vary run-to-run) and skipped on historical replay via
    `params.deterministic=False`. Its edge — if any — will only be visible in
    forward paper PnL, evaluated *net of API cost* (capped at $5/day,
    ~$1,825/yr against a 2% sleeve).

12. **Execution-cost model — fees + slippage, modeled separately.**
    `strategies.trades.compute_perp_close` charges round-trip cost as
    `(cost_bp_rt + slippage_bp_rt)` against notional at close. Defaults
    (since 2026-05-13, audit-calibrated):
    - `DEFAULT_COST_BP_RT = 10.0` — Binance taker fee × 2 legs.
    - `DEFAULT_SLIPPAGE_BP_RT = 5.0` — conservative mid of the 5–10bp/RT
      retail bid-ask spread + market-impact band at $1k–$20k notional.
    - **FOMC override: `SLIPPAGE_BP_RT = 10.0`** — 10× leverage at the
      announcement bar, when BTC/USDT spread widens.
    - **CARRY: `CARRY_SLIPPAGE_PCT = 0.04`** on top of the 20bp fee — 4 fills
      × 1bp limit-style slip on the synthetic spot+perp position.
    - **JPLUS_EMA_BTC and JPLUS_ETH_DAILY** use the same default (10bp fee +
      5bp slip + funding accrual) on close. Pre-2026-05-13 these sleeves
      passed `cost_bp_rt=0.0, apply_funding=False` because ETH_DAILY's status
      was framed as "pending a spot-fee model" — but both trade the *perp*,
      so the zero-funding default was structurally wrong (multi-week ETH-LONG
      perp at 8h funding ~0.005% accrues to ~4.5%/yr).
    - **Pre-2026-05-13 paper PnL** was net of fees only. Forward numbers from
      2026-05-13 onward are net of fees + slippage; older backtest figures
      were not. Treat the step-down on 2026-05-13 as a methodology change,
      not a regime change.

---

## 10. Where to look in the code

**Entry points and orchestration**

| Concern | File |
|---|---|
| Variant registration + composition | [strategies/p300_spec.py](strategies/p300_spec.py) |
| Per-tick sleeve dispatch + reconcile | [strategies/orchestrator.py](strategies/orchestrator.py) |
| Bot entry point (live) | [bot.py](bot.py) |
| Sim entry point | [studies/simulation/sim.py](studies/simulation/sim.py) |
| Backtest replay engine | [backtest_runner.py](backtest_runner.py) |
| Build a sim trader.db | [studies/simulation/build_sim_trader_db.py](studies/simulation/build_sim_trader_db.py) |
| Health invariants | [health.py](health.py) |
| One-time data bootstrap | [bootstrap.py](bootstrap.py) |

**Sleeves**

| Sleeve | File |
|---|---|
| S-003 ADX | [strategies/sleeves/adx/](strategies/sleeves/adx/) |
| S-078 Carry | [strategies/sleeves/carry/](strategies/sleeves/carry/) |
| JPLUS_EMA_BTC | [strategies/sleeves/ema/](strategies/sleeves/ema/) |
| JPLUS_ETH_DAILY | [strategies/sleeves/eth_daily/](strategies/sleeves/eth_daily/) |
| SHORT_SQUEEZE | [strategies/sleeves/short_squeeze/](strategies/sleeves/short_squeeze/) |
| AI_QUANT | [strategies/sleeves/ai_quant/](strategies/sleeves/ai_quant/) |
| TIMING_ANOMALIES (meta) | [strategies/sleeves/timing_anomalies/](strategies/sleeves/timing_anomalies/) |
| &nbsp;&nbsp;↳ FOMC | [strategies/sleeves/timing_anomalies/internal/fomc/](strategies/sleeves/timing_anomalies/internal/fomc/) |
| &nbsp;&nbsp;↳ THU_BEAR | [strategies/sleeves/timing_anomalies/internal/thu_bear/](strategies/sleeves/timing_anomalies/internal/thu_bear/) |
| &nbsp;&nbsp;↳ PDO_L_RF | [strategies/sleeves/timing_anomalies/internal/pdo/](strategies/sleeves/timing_anomalies/internal/pdo/) |
| &nbsp;&nbsp;↳ CPR | [strategies/sleeves/timing_anomalies/internal/cpr/](strategies/sleeves/timing_anomalies/internal/cpr/) |
| &nbsp;&nbsp;↳ R4 family | [strategies/sleeves/timing_anomalies/internal/r4/](strategies/sleeves/timing_anomalies/internal/r4/) |

**Plumbing (see §6 for full ownership matrix)**

| Concern | File |
|---|---|
| Two-phase dispatch contract (Intent + reconcile) | [strategies/support/dispatch.py](strategies/support/dispatch.py) |
| Look-ahead-safe clock | [strategies/support/clock.py](strategies/support/clock.py) |
| Sim-mode tick primitive | [strategies/support/sim_loop.py](strategies/support/sim_loop.py) |
| Live price feed (1m spot, strict-`<`) | [strategies/support/price_feed.py](strategies/support/price_feed.py) |
| Realized PnL aggregation | [strategies/support/strategy_health.py](strategies/support/strategy_health.py) (`trades_daily_returns`) |
| Trade-row schema + standardized close log | [strategies/support/trade_db.py](strategies/support/trade_db.py) |
| Gate validation protocol | [GATE_VALIDATION.md](GATE_VALIDATION.md) |
| FOMC observer audit log | `data/databases/prod.db:fomc_observer` |

---

## 11. Live and sim modes

Live trading runs from [`bot.py`](bot.py). Sim mode runs from
[`studies/simulation/sim.py`](studies/simulation/sim.py). They share identical
dispatch logic — only the data source and clock differ:

| | LIVE | SIM |
|---|---|---|
| Clock | wall clock | simulated, advanced deterministically |
| Market data | `data/databases/prod.db` (kept fresh by `binance_feed`) | `--trader-db <path>` (built by `studies/simulation/build_sim_trader_db.py`) |
| Trade ledger | `data/databases/prod.db` | `--dash-db <path>` (separate file) |
| External APIs | NY Fed XML, Polymarket, F&G, news | all blocked — sim must be reproducible offline |
| Loop | wall-clock 60s tick | `strategies.support.sim_loop.run_sim` (no sleep) |

```
# Live (default; binance_feed runs in-process):
python bot.py

# Sim — build a sliced trader.db, then run the bot under a fake clock.
# The P-300 variant is auto-registered into the sim ledger DB on startup.
python studies/simulation/build_sim_trader_db.py \
    --start 2024-01-01 --end 2024-12-31 \
    --output data/trader_sim_2024.db
python studies/simulation/sim.py \
    --start 2024-01-01 --end 2024-12-31 \
    --trader-db data/trader_sim_2024.db \
    --dash-db /tmp/sim_dash.db \
    --sim-tick-seconds 60
```

The same `STRATEGY_DISPATCH` runs in both modes; the same 7 top-level sleeves
(TIMING_ANOMALIES fanning out to 8 substrategies) open trades to whichever
`--dash-db` path the orchestrator is pointed at. Sim mode produces a complete
trade ledger that the report notebooks
([studies/notebooks/full_portfolio_report.ipynb](studies/notebooks/full_portfolio_report.ipynb),
[studies/notebooks/backtest_report.ipynb](studies/notebooks/backtest_report.ipynb))
can summarize identically to a live run.

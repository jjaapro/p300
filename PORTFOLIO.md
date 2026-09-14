# P-300 Aggressive 2.0 — Portfolio Composition

What the fleet trades, when each strategy fires, what leverage it uses, and how
the pieces compose. All percentages are **fractions of capital** unless stated
otherwise.

> Status: paper-only. Nine bot variants across seven bot units — see §2.
> The single orchestrator variant `p300_aggressive_v2_v1_0` was disabled
> 2026-09-13 (its 27 closed trades are kept); the doc keeps its history, marked.

---

## 1. Overview

**A bot is a directory.** `bots/<name>/` holds `runner.py`, `config.py` and
`strategy/` (`signal.py`, `config.py`, and `math.py` where the sleeve has one),
and that is the only place a strategy's logic lives. There are **seven bot
units** — `chento_v3` (BTC), `chento_v3_eth`, `short_squeeze`, `adx`, `carry`,
`squeeze_bull`, `r4` — and **nine variants**: the two squeeze bots each carry a
no-stop paper twin inside the same process, and `r4` trades its ETH windows
only. Each runner owns its own tick loop, its own open-position rule (one at a
time for most; chento and r4 stack positions by design — §8) and its own
variant row.

`start_fleet.ps1` starts [feed.py](feed.py) + the seven runners + the dashboard
(`dashboard/server.py` on :8300); [health.py](health.py) checks the result.
[monitor.py](monitor.py) exists but is not running and has no scheduled task.

All bots write to the same `trades` table (`execution_mode='paper'`), tagged by
strategy name; realized PnL is the trade-ledger sum (no parallel
theoretical-PnL track since the 2026-05-10 live/sim refactor).

> **History (retired 2026-09-13).** Until then a single process (`bot.py`)
> dispatched 7 top-level sleeves per minute through `strategies/orchestrator.py`,
> one of them (`TIMING_ANOMALIES`) a meta-sleeve fanning out to 8
> calendar/clock substrategies — 14 signal paths under one variant, with dynamic
> per-`(regime, sleeve)` allocation from `strategies/support/allocation.py` and
> no Core/Tactical tiers. That path had been dormant since 2026-06-11 and was
> deleted by step 3 of the "bot = directory = strategy" refactor, along with
> `p300_spec.py`, `backtest_runner.py`, the simulation harness and five support
> modules. Nothing dispatches anything any more: a runner evaluates its own
> strategy and sizes it itself (§4).

> **Stop-loss semantics.** `stop_loss_pct` values are interpreted as
> **price-move** percentages by default — a 10% stop at k=5× triggers when
> price moves 10%, after which the trade has lost 50% of margin. Set
> `P300_STOP_SEMANTICS=margin` to interpret the same numbers as margin-loss
> caps. See [strategies/support/risk_config.py](strategies/support/risk_config.py).
> ADX is the only bot still carrying such a percentage (`STOP_LOSS_PCT = 10.0`).
> r4 sets it to `None` deliberately (§3.9), carry has no stop at all, and the
> rest stop at a price level their strategy computes.

---

## 2. Roster — what runs

Nine paper variants in seven processes. Each row's capital is its own
`CAPITAL_USDT = 10,000` (bot-level, not a share of one pool); sizing is
per-bot (§4), not a portfolio allocation.

| Bot unit | Variant ID | Strategy code | Sizing | Asset | Direction | Hold |
|---|---|---|---|---|---|---|
| [`adx`](bots/adx/) | `bot_adx_v1` | [S-003 ADX](bots/adx/strategy/signal.py) | fixed-R 2% / ≤3× notional | BTC | LONG / SHORT | days–weeks |
| [`carry`](bots/carry/) | `bot_carry_v1` | [S-078 Carry](bots/carry/strategy/signal.py) | fixed notional 1× capital | BTC (delta-neutral) | n/a | days |
| [`chento_v3`](bots/chento_v3/) | `bot_chento_v3_v1` | [CHENTO_TRIPLE_V3](bots/chento_v3/strategy/signal.py) | fixed-R 2% / ≤3× | BTC | LONG / SHORT | ~3 days (TIF=72h) |
| [`chento_v3_eth`](bots/chento_v3_eth/) | `bot_chento_v3_eth` | same sleeve, `CHENTO_V3_ASSET=ETH` | fixed-R 2% / ≤3×, half-R after a loss | ETH | LONG / SHORT | ~3 days (TIF=72h) |
| [`short_squeeze`](bots/short_squeeze/) | `bot_short_squeeze_v1` | [S-105 SHORT_SQUEEZE](bots/short_squeeze/strategy/signal.py) | fixed-R 1% / ≤3× (cap binds by design) | BTC | LONG | ≤6h (15m signal) |
| ↳ same process | `bot_short_squeeze_nostop_v1` | same signals, no stop / no target | fixed notional 1× capital | BTC | LONG | 6h time stop |
| [`squeeze_bull`](bots/squeeze_bull/) | `bot_squeeze_bull_v1` | [S-107 SQUEEZE_BULL](bots/squeeze_bull/strategy/signal.py) | fixed-R 1% over the 2% stop = 0.5× | BTC | LONG | ≤48h (hourly signal) |
| ↳ same process | `bot_squeeze_bull_nostop_v1` | same signals, no stop | fixed 0.5× (same notional) | BTC | LONG | +3% target or 48h |
| [`r4`](bots/r4/) | `bot_r4_v1` | [R4 calendar](bots/r4/strategy/signal.py) | 0.20/window × sleeve lev (cap 7.5×), gross ≤3× | ETH | LONG | per window, below |

`r4` is one variant evaluating four windows, of which **only the two ETH ones
are enabled** (`ENABLED` in [bots/r4/config.py](bots/r4/config.py), user
decision 2026-09-12 — the BTC windows stay wired and tested but off):

| Window | Enabled | Asset | Entry → Exit |
|---|---|---|---|
| `JPLUS_R4_BTC` | no | BTC | Mon wk1-2, 06:00 → 18:00 UTC (12h) |
| `JPLUS_R4_ETH` | **yes** | ETH | Tue 20:00 → Wed 20:00 UTC, wk1-2 (24h) |
| `JPLUS_R4_BTC_V2` | no | BTC | Wed + Fri wk1-2, 04:00 → 14:00 UTC (10h) |
| `JPLUS_R4_ETH_V2` | **yes** | ETH | Wed + Fri wk1-2, 04:00 → 14:00 UTC (10h) |

### 2.1 Archived sleeves (moved 2026-09-13, nothing runs them)

Eight sleeves that stopped being run were moved out of the live tree by step 4
of the refactor. They are importable for research parity checks and nothing
else; the contract is in
[studies/material/archive/README.md](studies/material/archive/README.md), and
[`tests/test_orchestrator_interface_gone.py`](tests/test_orchestrator_interface_gone.py)`::test_no_live_code_imports_the_archive`
fails the suite if a live module imports one — it parses imports rather than
grepping, because one of the three edges it caught (`jplus_inputs` importing
the EMA sleeve on the live r4 sizing path) would have failed *silently*: the
runner catches `ImportError` and writes a degraded heartbeat.

| Sleeve | Was | Now | Why it stopped |
|---|---|---|---|
| AI_QUANT | `strategies/sleeves/ai_quant/` | [studies/material/archive/ai_quant/](studies/material/archive/ai_quant/) | `AI_QUANT_ENABLED=false` since 2026-06 |
| JPLUS_EMA_BTC | `strategies/sleeves/ema/` | [studies/material/archive/ema/](studies/material/archive/ema/) | zero trades in the 2026-06-07 trade audit |
| JPLUS_ETH_DAILY | `strategies/sleeves/eth_daily/` | [studies/material/archive/eth_daily/](studies/material/archive/eth_daily/) | never traded live |
| THU_BEAR (S-096 V4) | `…/timing_anomalies/internal/thu_bear/` | [studies/material/archive/thu_bear/](studies/material/archive/thu_bear/) | out-of-sample question never settled |
| PDO_RETOUCH (S-102) | `…/timing_anomalies/internal/pdo/` | [studies/material/archive/pdo/](studies/material/archive/pdo/) | validated 2026-05-11, never promoted to a bot |
| CPR (S-101) | `…/timing_anomalies/internal/cpr/` | [studies/material/archive/cpr/](studies/material/archive/cpr/) | TradingView re-validation never completed |
| FOMC (S-103) | `…/timing_anomalies/internal/fomc/` | [studies/material/archive/fomc/](studies/material/archive/fomc/) | `mid_hold` is the standing decision, i.e. skip |
| chento_limit_bid | `strategies/sleeves/chento_limit_bid/` | [studies/material/archive/chento_limit_bid/](studies/material/archive/chento_limit_bid/) | superseded by `bots/chento_v3/` |

The `TIMING_ANOMALIES` meta-sleeve that dispatched four of them went with the
orchestrator; what it was is recorded in
[studies/material/archive/TIMING_ANOMALIES.md](studies/material/archive/TIMING_ANOMALIES.md).
The four R4 windows it also dispatched live on in `bots/r4/`.

**The ledger still carries their trades.** `prod.db` holds **12 closed trades
and zero open ones** under archived labels — AI_QUANT 4, CPR 4, PDO_RETOUCH 2,
THU_BEAR 2 — so the archived names survive in live code as *data, not
dispatch*: `KNOWN_SLEEVES` in
[strategy_health.py](strategies/support/strategy_health.py), the
`{"ADX", "THU_BEAR"}` stop path in [strategies/trades.py](strategies/trades.py),
`PDO_RETOUCH` in [equity.py](strategies/support/equity.py)'s `_NO_FUNDING`, and
`fomc_observer` / `ai_quant_decisions` in [botlib.py](botlib.py)'s
`STATE_TABLES`. Dropping any of them stops the reports accounting for those
closed trades.

---

## 3. Per-strategy detail

Signal / Entry / Exit / Edge thesis / Caveat. The running bots are §3.1, §3.2,
§3.5, §3.7, §3.9 and §3.10 — the last being the newest, shipped 2026-09-09.
§3.3, §3.4, §3.6 and §3.8 describe **archived** sleeves and are kept as history
— nothing dispatches them, and their code moved to
`studies/material/archive/` on 2026-09-13 (§2.1).

### 3.1 S-003 ADX — Trend-flip on BTC

- **Signal**: 14-period ADX crosses 25 from prior compression (<20 in last 20 bars). Direction: LONG when close > EMA(50), SHORT when close < EMA(50). Trend filter: entries additionally require close on the right side of EMA(150) — **symmetric since the 2026-07-22 Tier-2 calibration** (it was LONG-only from 2026-05-04).
- **Entry**: at the crossover bar, sized fixed-R at 2% of capital over the effective initial stop, capped at 3× notional. A LONG is vetoed when the 30-day funding z-score exceeds 1.5 ("don't long over-crowded leverage").
- **Exit**: opposite ADX flip, OR the 4×ATR(14) trailing stop (Tier-2), OR the 10% stop-loss.
- **Edge thesis**: catches medium-term trends in BTC; takes the loss when trend reverses.
- **Calibration log**: [docs/calibration/adx.md](docs/calibration/adx.md). Tier-2 moved the 2018→2026-06 backtest from maxDD −27.3% / MAR 1.78 to −15.1% / 3.09 (OOS MAR 3.16); each lever is independently disableable in [bots/adx/strategy/config.py](bots/adx/strategy/config.py).

### 3.2 S-078 Carry — Delta-neutral funding harvest

- **Signal**: 7-day average BTC perp funding > 0%. Entry opens spot-long + perp-short of equal notional → market-neutral.
- **Income**: collects funding payments every 8h while the perp side is short.
- **Exit**: trailing 30-day cumulative funding below −0.5 % of notional (since 2026-09-12; the 3-consecutive-negative-days exit cost 0.85 %/yr and never protected — [docs/calibration/carry.md](docs/calibration/carry.md)). No scheduled time-stop.
- **Edge thesis**: structurally positive funding in bullish regimes is paid for free if you can hedge cheaply. P&L is dominated by funding accrual, not price moves.

### 3.3 JPLUS_EMA_BTC — Weekly EMA crossover position-flip

> **ARCHIVED 2026-09-13** — zero trades in the 2026-06-07 trade audit; code at
> [studies/material/archive/ema/](studies/material/archive/ema/). Its position
> math survives in the live tree as
> [strategies/support/ema_position.py](strategies/support/ema_position.py),
> because `jplus_inputs.today_inputs()` still returns `ema_p` and the r4 bot
> reads that snapshot. The description below is what the sleeve did.

- **Signal**: EMA(5) vs EMA(21) on **weekly** BTC closes. LONG when EMA5 > EMA21, SHORT when EMA5 < EMA21. Position state (`ema_p` in the sizing pipeline): +1 LONG, −1 SHORT, 0 warmup.
- **Entry**: at the next weekly candle's open after a cross. Strict T+1, no same-bar entry.
- **Exit**: at the next weekly candle's open after the reverse cross. The sleeve is always in one of LONG / SHORT — no idle-to-cash state after the first crossover.
- **Daily contribution**: `ema_p × today's BTC daily return × regime_weight`. An EMA-LONG day with BTC up +2% adds +2% × regime_weight; an EMA-SHORT day adds −2% × regime_weight.
- **Cost model**: 10bp round-trip fee + 5bp slippage + funding accrual (since 2026-05-13 — pre-fix the sleeve incorrectly ran zero-funding on multi-week perp holds; ~4.5%/yr funding was previously invisible).
- **Edge thesis**: medium-term trend follower on BTC. Captures multi-week directional moves; pays the spread/fee on whipsaws.
- **Active in**: every regime (regime weights 0.30 in mild_bull / uncertain / bear, 0.50 in strong_bull).

### 3.4 JPLUS_ETH_DAILY — Passive ETH long, bull-regimes only

> **ARCHIVED 2026-09-13** — never traded live; code at
> [studies/material/archive/eth_daily/](studies/material/archive/eth_daily/).
> The description below is what the sleeve did.

- **Signal**: none — not a discretionary signal sleeve. It's a "long ETH at regime-weighted size" position.
- **Entry**: the day the regime classifier flips into `strong_bull` or `mild_bull`.
- **Exit**: the day the regime classifier flips out of bull.
- **Daily contribution**: `ETH daily return × regime weight` (0.20 strong_bull, 0.10 mild_bull, 0 otherwise).
- **Cost model**: 10bp fee + 5bp slip + funding accrual (since 2026-05-13).
- **Edge thesis**: pure long-ETH-beta during bull regimes. ETH outperforms BTC on the way up; this gives the portfolio that exposure when conditions are constructive.

### 3.5 S-105 SHORT_SQUEEZE — Sweep + CVD-divergence long, intraday

- **Signal**: bar-level LONG trigger. All four conditions must agree on a 15m bar:
  1. Session gate — London or NY (07:00–21:00 UTC).
  2. Asia-grind macro — slow drift up overnight (criteria in [bots/short_squeeze/strategy/config.py](bots/short_squeeze/strategy/config.py)).
  3. Sweep — current bar takes out the prior session low.
  4. Perp/spot CVD divergence — spot CVD positive while perp CVD negative on the sweep bar.
- **Entry**: at the sweep bar close, sized fixed-R at 1% of capital. The stop sits 10bp below the swept bar's low — often only basis points from entry — so the 3× notional cap binds **by design**, not by accident.
- **Exit**: 3R fixed target, the stop, or a 6h time stop. No trailing.
- **Second variant (2026-09-12)**: `bot_short_squeeze_nostop_v1` runs in the same process on the same signals with no stop and no target — 6h time stop only — at a fixed 1× of capital. Paired with the stop variant for re-cuts at n=20/30 ([docs/calibration/short_squeeze.md](docs/calibration/short_squeeze.md)); the no-stop style wins where the stop sits inside the noise, which is exactly this sleeve's shape.
- **Edge thesis**: forced short-covers at swept lows produce predictable squeezes when perp/spot order-flow disagrees. Mechanistically grounded — first such sleeve in the portfolio. See [studies/notebooks/short_squeeze_sessions/strategy_backtest.ipynb](studies/notebooks/short_squeeze_sessions/strategy_backtest.ipynb).
- **Caveat**: at its old 25bp cost model this sleeve booked −0.32 R/trade on a +0.48 R gross edge, so its paper record before 2026-09-12 is negative by construction (§9.12). Measured cost is 9.3bp and it now books 10; execution cost is still 47–60% of gross.

### 3.6 AI_QUANT — Discretionary LLM trader (experimental, default-OFF)

> **ARCHIVED 2026-09-13** — `AI_QUANT_ENABLED=false` since 2026-06 and never
> re-enabled; at 4,006 lines it was the largest sleeve in the tree. Code at
> [studies/material/archive/ai_quant/](studies/material/archive/ai_quant/); its
> 4 closed trades and its `ai_quant_decisions` journal rows stay in `prod.db`.
> The description below is what the sleeve did when it ran.

- **Status**: Phase-1 experiment (added 2026-05-08). Default-disabled via the `AI_QUANT_ENABLED` env var ([`.env.example`](.env.example) ships with `false`); when unset, [`ai_quant/signal.py:_kill_switch_on()`](studies/material/archive/ai_quant/signal.py) short-circuits to `status='disabled'` and no LLM call is made.
- **Allocation**: 2% of capital. Raise to 5% only after 60+ days of forward paper PnL net of API cost.
- **Leverage**: 3×. **Asset**: BTC perp. **Direction**: LONG / SHORT / FLAT, chosen daily by the model.
- **Signal**: an Anthropic Opus 4.7 tool-use loop runs once per UTC day in a 10-minute window (00:05–00:15 UTC). Every minute, four cheap gates were evaluated by the orchestrator before any LLM call: kill-switch / time-window / per-day-already-fired / daily-cost-cap. On the one tick that passes all four, the service builds a context bundle (regime, F&G, funding, recent volatility, open positions), renders a 90-bar daily chart, and runs the decision loop with server tools enabled.
- **Output schema**: the model returns `direction ∈ {LONG, SHORT, FLAT}`, `conviction_0_100`, `time_horizon_days`, and `key_drivers[]`. A **conviction floor** forces `conviction < 30` to FLAT regardless of the model's stated direction.
- **Sizing**: `allocation_pct = weight_pct × (conviction / 100)`, capped at the 2% weight. Conviction-50 LONG sizes to 1% at 3× leverage; conviction-100 LONG sizes to the full 2%.
- **Reconciliation**: each day's decision is reconciled against any open AI_QUANT position — open / hold / close / flip. No mid-day scaling in v1.
- **Exit**: no fixed time-stop. Held until the next day's decision flips them or sets FLAT, or until the configured 10% price-move stop fires.
- **Cost cap**: $5/day default API spend ceiling (`AI_QUANT_DAILY_COST_CAP_USD`); when exceeded, the gate returns `cost_capped` and no decision runs that day.
- **Audit trail**: every fire writes a row to the journal ([`ai_quant/journal.py`](studies/material/archive/ai_quant/journal.py)) with decision payload, tool calls, token usage, cost, and resulting trade action — including ERROR rows when context-build / chart-render / API fail. The journal also mirrors to human-readable markdown at `data/ai_quant_archive/{date}_{variant}_{asset}_{decided}_id{N}.md`.
- **Backtest behavior**: `params.deterministic=False` was consumed by `backtest_runner.py` (deleted 2026-09-13) to **skip** AI_QUANT on historical replay — the LLM is non-deterministic. AI_QUANT contributes nothing to backtest figures.
- **Edge thesis**: a discretionary trader with broad context (macro, sentiment, microstructure, chart) may catch regime shifts that the rule-based sleeves are structurally blind to. Whether the model can beat its own API cost net of slippage is the open question.

### 3.7 CHENTO_TRIPLE_V3 — Mean-reversion-into-extreme swing on BTC perp 15m

- **Signal**: Triple composite at the 15m bar — **all three** must agree on direction:
  1. **B1 money-flow divergence** — taker-CVD z-score (rolling 30d) crosses ±0.5 while price-velocity z-score stays within ±1.0 (volume builds without price following).
  2. **B5 LSR extremes** — Binance global long-account percentage hits p10 (oversold longs → LONG) or p90 (euphoric longs → SHORT) of the trailing 30 days.
  3. **B7 multi-TF CVD alignment** — CVD z-scores on 1h / 4h / 1d / 3d resamples all have the same sign with |z| ≥ 2.0.
- **Filter gates** (every active one must pass — three since 2026-09-14):
  1. `no_tilt` — skip for 48h after a stop-loss exit with negative R (TIF-expiry losses do not count; in memory, cleared on restart; not the overlay study's skip-after-any-losing-predecessor rule, BACKLOG 15). BTC leg only — ETH halves risk on the trade after a loss instead (ETH leg, below).
  2. `no_resist_OB_within_2R` — skip if a fresh unfilled opposite-direction Order Block sits within 2R of entry (causally-detected 5-bar pivot OB).
  3. ~~`okx_aligned`~~ — **RETIRED 2026-09-13, off since 2026-09-14** (`FILTER_OKX_ALIGNED = False`, both legs): the pre-registered causal re-validation returned RETIRE on 2026-09-13 ([findings](studies/notebooks/okx_gate_revalidation/findings.md)). The rule was: OKX-Binance perp price log-delta z-score (rolling 7d) must sign-match the trade direction. `math.okx_aligned` and the `OKX_*` constants stay; the sleeve still computes `okx_delta_z` into its feature frame each rebuild, but nothing records it and no decision reads it.
  4. `skip_up_30d_shorts` (asymmetric) — skip ONLY shorts when BTC 30d return > +10%. Longs still take.
- **Entry**: at the 15m bar close, sized fixed-R at 2% of capital over the sleeve's own stop, capped at 3× notional. (The H_B adaptive-sizing split was **not** shipped — it failed the backward-only Pareto test; see below.)
- **Math layer**: stop = entry ± 5×ATR(14, 15m), target = entry ± 6R fixed, TIF = 72 hours, 10bp RT cost scaled by stop distance (measured 2026-09-12; the research replays charged 18bp).
- **Execution — A4 ladder add: DISABLED in production** (`LADDER_ENABLED = False`, shipped 2026-06-05 on the P1 backward-only verdict — H_B was Pareto-best on anchors but failed once the lookahead was removed). The rule it would have run, kept for the record: on the bar where adverse excursion reaches −0.3R, add a second order at that price, sized by 7-day Volume-Profile classification of the original entry —
  - **Inside Value Area** (~34% of triggers): T3 sizing → 150% add. Worst-case combined loss ~3.3R (~4.4% NAV at 4% risk).
  - **Outside Value Area** (~66% of triggers): T1 sizing → 50% add. Worst-case combined loss ~2.0R (~2.5% NAV at 4% risk).
  - After a ladder fire the combined stop widens to −1.5R from original entry, with **strictly no further compounding** — a single-shot risk increase, not a martingale.
- **Cooldown**: 6 hours between triggers (was 4 before the B1-anchored trigger). With the OKX gate off the triple composite fires about 66 BTC / 62 ETH times a year (357 / 337 in the 2021-04..2026-09 study pool); filters 2 and 4 veto about 42% / 45% of those, leaving about 38 / 34 (the gated arm kept ~17 / ~15), and the cooldown plus BTC's 48h skip trim that to about 36 / 34 taken, so the cooldown rarely binds. It is also the only entry spacing: there is no single-open guard, so positions stack (§8).
- **ETH leg**: `bots/chento_v3_eth/` runs the same sleeve with `CHENTO_V3_ASSET=ETH` against the ETH tables. Two rules differ per asset: ETH turns **off** the skip-after-loss filter and halves risk on the trade after a loss instead (matched skip's MAR while keeping ~64% more income, overlay study 2026-08-23, on the OKX-gated trade set).
- **Backtest performance** (5.4y BTC, R-tracking framework, funding cost included): 106 trades / 20.2 per yr / mean R +4.13 / WR 82% / max DD −4.52R / MAR 18.4 / IS-OOS gap 0.05R. ETH-cross-validated at +2.9R mean / 78% WR. *(Gated-arm research figures: measured with the OKX gate on, partly on the same-hour look-ahead `okx_delta_z` — not the configuration running since 2026-09-14. The re-validation's report-only ungated arm at 10bp, funding not modelled, every trade taken, no tilt: BTC +0.731R mean over 208 trades, ETH +0.540R over 184.)*
- **Caveats**:
  - The 30d-return threshold (+10%) is calibrated on BTC volatility; for ETH or alts this needs re-tuning.
  - The sleeve fires sparsely, so long empty stretches in the paper ledger are the expected behaviour rather than evidence of a wiring fault. Per-day gate-kill diagnostics are permanently on (`DIAG_PATH` in [bots/chento_v3/config.py](bots/chento_v3/config.py)) — the OKX gate once locked the sleeve out for two months unseen.
  - *(Retired with the replay engine, 2026-09-13: the old caveat that backtest replay at 1h ticks missed 75% of 15m signals. The bot ticks at 60s and self-gates entries to 15m boundaries, so it does not have that problem.)*
- **Edge thesis**: at confluence-of-three-extremes points, BTC mean-reverts toward equilibrium with high probability. The asymmetric regime filter avoids the one regime (bull rally + short trigger) where the strategy structurally fails. The A4 ladder converts the inevitable adverse wicks into a sizing advantage rather than a cost. Validated against 5+ years of cleanly-separated IS/OOS data with extraordinary stability (OOS = IS within 5% on every key metric).
- **Provenance**: Triple composite emerged from validating 39 chento-stated rules and 5 dxFeed-trader hypotheses — see [studies/material/chento/validation/findings_decisions.md](studies/material/chento/validation/findings_decisions.md) for the per-rule audit and [bots/chento_v3/strategy/README.md](bots/chento_v3/strategy/README.md) for the consolidated finding-to-parameter trace.

### 3.8 TIMING_ANOMALIES — Meta-sleeve over 8 calendar/clock substrategies

> **RETIRED 2026-09-13**, with the orchestrator that was its only caller. What
> the dispatcher was is recorded in
> [studies/material/archive/TIMING_ANOMALIES.md](studies/material/archive/TIMING_ANOMALIES.md);
> four of its substrategies are archived (§3.8.1–3.8.4) and the four R4 windows
> live on in their own bot (§3.9). The description below is history.

A single orchestrator-level dispatcher that fanned out per-tick to 8
substrategies sharing one allocation budget. The meta-sleeve was the **sole**
entry point — there was no per-substrategy dispatcher at the orchestrator
level. Each substrategy tagged its trades with its own name (`FOMC`,
`THU_BEAR`, etc.) in `trades.strategy`, so per-substrategy attribution is
preserved end-to-end and survives the archive.

Per-substrategy weight resolution: the meta-sleeve looked up the substrategy's
legacy `strategy_id` via `ALLOCATOR_KEY` and called
`allocation.get_weight_pct(legacy_id, regime)`. Without that hop, every
substrategy would have silently fallen back to its static composition weight,
defeating regime-adaptive sizing.

#### 3.8.1 FOMC — Long into Fed announcement, regime-filtered

> Archived — [studies/material/archive/fomc/](studies/material/archive/fomc/).
> `mid_hold` is the standing decision in this rate environment, i.e. skip.

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

#### 3.8.2 THU_BEAR (S-096 V4) — Calendar-driven Thursday short

> Archived — [studies/material/archive/thu_bear/](studies/material/archive/thu_bear/).
> The out-of-sample question below was never settled. Its 2 closed trades still
> resolve through the stop path in [strategies/trades.py](strategies/trades.py).

- **Signal**: Thursdays only. V4 event filter — trade only if Thursday is within ±1 day of CPI or NFP, AND not within ±1 day of OPEX. Prior-day regime (from [regime_tactical.py](strategies/support/regime_tactical.py)) must be `bear_trend / sell_off / chop` (not `bull_trend`).
- **Entry**: Thursday 00:00 UTC. SHORT BTC + ETH equally.
- **Exit**: Friday 01:00 UTC, or stop-loss at −1% spot (5% margin at k=5×).
- **Edge thesis**: weekly Thursday selling pressure during macro-event-adjacent periods, conditioned on being already in a non-bull regime.
- **Caveat**: V4 event filter was derived post-hoc from V3's Thursday attribution — in-sample selection bias applies.

#### 3.8.3 PDO_L_RF (S-102) — Pullback Daily Open Retouch Long

> Archived — [studies/material/archive/pdo/](studies/material/archive/pdo/).
> Validated 2026-05-11 but never promoted to a bot. Its 2 closed
> `PDO_RETOUCH` trades keep the sleeve name in `_NO_FUNDING`
> ([equity.py](strategies/support/equity.py)).

- **Signal**: after a daily gap-down ≥ 2%, wait for the price to retouch the prior daily open (PDO). Regime must not be deeply bearish (`regime_threshold_pct: −10%` recent peak DD).
- **Entry**: at the PDO retouch.
- **Exit**: scheduled time-stop, or stop-loss.
- **BTC-LONG cross-sleeve cap**: PDO + CPR combined BTC-LONG allocation pre-leverage is capped at 15% by [strategies/support/risk_caps.py](strategies/support/risk_caps.py).
- **Edge thesis**: gap-fills are a known intraday phenomenon in crypto. Mean-reversion long after a down-gap.
- **Caveat**: parameters (gap %, regime threshold) were swept in upstream research without visible walk-forward CV — data-snooping exposure.

#### 3.8.4 CPR (S-101) — Contrarian Positioning Reversal

> Archived — [studies/material/archive/cpr/](studies/material/archive/cpr/).
> The TradingView re-validation was never completed. 4 closed trades.

- **Signal**: all four conditions must agree:
  1. 3-day mean funding rate < 20-percentile of trailing window.
  2. LSR (long-short ratio) < 20-percentile of trailing window.
  3. Daily close > EMA(20).
  4. EMA(20) > EMA(50).
- **Setup logic**: persistent negative funding + crowd is short + price still in uptrend → expected short squeeze.
- **Entry**: at the next 1m bar after signal trigger.
- **Exits**: target at +2.93% (BB upper band), stop at −5%, or 15-day time-stop.
- **BTC-LONG cross-sleeve cap**: shared with PDO — see §3.8.3.
- **Edge thesis**: contrarian-position-with-trend setup. Theoretically high-quality but historically thin sample (12 BTC + 9 ETH events from upstream).

#### 3.8.5 R4 family — moved

The four R4 windows were the only substrategies to survive the meta-sleeve.
They run as their own bot — see **§3.9**.

### 3.9 R4 — Calendar-window intraday longs (`bots/r4/`)

One variant, `bot_r4_v1`, evaluating four windows of which **only the two ETH
ones are enabled** (user decision 2026-09-12; the BTC pair is wired, tested and
`ENABLED = False`). All four share the same machinery: fixed-window long,
inner leverage 2.5×, vol-percentile gate from
[strategies/support/gate.py](strategies/support/gate.py). The vol gate fires
when trailing 30-day BTC realized vol is in the top 25% of the 365-day
distribution — strictly T−1, no look-ahead. Fires ≈30% of days.

| Window | Enabled | Asset | Entry → Exit | Post-ETF expectancy ([r4_study](studies/notebooks/r4_study/findings.md)) |
|---|---|---|---|---|
| JPLUS_R4_BTC | no | BTC | Mon wk1-2, 06:00 → 18:00 UTC (12h) | +0.83%/fire (n=55, t=2.7) |
| JPLUS_R4_ETH | **yes** | ETH | Tue 20:00 → Wed 20:00 UTC (Wed day ≤ 14, 24h) | +1.82%/fire (n=55, t=3.1) |
| JPLUS_R4_BTC_V2 | no | BTC | Wed + Fri wk1-2, 04:00 → 14:00 UTC (10h) | +0.48%/fire (n=111, t=3.0) |
| JPLUS_R4_ETH_V2 | **yes** | ETH | Wed + Fri wk1-2, 04:00 → 14:00 UTC (10h) | +0.56%/fire (n=111, t=2.8) |

Each fire is LONG. Inner leverage **2.5×** normally, **1.0×** when the vol gate
fires, times the vol-target leverage from `jplus_inputs.today_inputs()` (regime
caps 1.5–3.0×); a bear regime means no trade. Costs at close are the
`trades.py` defaults: 10bp fee + 5bp slippage + funding.

**Bot-level rules** ([bots/r4/config.py](bots/r4/config.py),
[docs/calibration/r4.md](docs/calibration/r4.md)):

- **Sizing** — capital × **0.20 per window** × the sleeve's stacked leverage capped at **7.5×**, so one fire is ≤1.5× capital and typically ~1×. Equal weight per window beat expectancy-weighting in the study.
- **Co-fire budget** — open R4 notional ≤ **3.0× capital**. Overlapping windows are by design (a Wednesday can hold ETH V1 still open plus ETH V2); a new fire is scaled *down* to the remaining budget, never skipped, unless the remainder is below $250.
- **Late-entry grace 300s** — a fire more than 5 minutes after the window opens is logged `missed_window` and never taken. The 2026-05-13 cold fills at +127 min were the only V2 losers.
- **No stop-loss.** The pre-registered sweep of 2/3/5% stops rejected every level: none kept expectancy within 5bp *and* cut the worst fire by ≥30% in both eras. The scheduled window close is the only exit. Worst single fires without a stop were −4.8% (BTC) to −10.2% (ETH pre-ETF) gross. Re-opening this is a new pre-registered study.

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

**Caveat — no mechanism.** R4 was held out of the fleet on 2026-09-09 despite
its backtest precisely because nobody can say *why* the windows work or when
they would decay. It rejoined on 2026-09-12 with only the era-stable ETH leg
enabled; the BTC windows are post-perp emergent and stay off. Acceptance needs
≈20 fires, i.e. 3–4 months.

### 3.10 S-107 SQUEEZE_BULL — Long the flush, bull regime only

- **Signal**: hourly bars, `cd_futures_ohlcv` joined to `cd_open_interest`. A bar triggers when open interest falls ≥2% over the trailing 4 hours **and** price falls ≥0.5% over the same 4 bars (a *long* flush, not shorts covering), the trailing 30-day return is above +10% (bull regime), and no kept flush event fired in the previous 24 bars.
- **The cooldown runs over flush EVENTS, not over trades.** A bear-regime flush the sleeve never trades still silences the next 24 bars — that is the research behaviour and the parity test enforces it.
- **Entry**: at the trigger bar's close. Fixed-R 1% of capital over the 2% stop, so notional is 0.5× capital and the 3× cap never binds.
- **Exit**: −2% stop, +3% target (1.5R gross), 48h time stop. Within a bar the stop is checked before the target.
- **Second variant (2026-09-12)**: `bot_squeeze_bull_nostop_v1` runs in the same process on the same signals with no stop, sized as if the 2% stop existed — both variants hold the same notional on every fire and their ledgers differ only by the exit. Re-cuts are paired at n=20/30.
- **Edge thesis**: forced deleveraging of longs overshoots; in a bull regime the flush is bought back. Ungated the pool is a coin flip (profit factor 1.00 over 423 fires); bull-gated it is 1.74.
- **The shipped gate is NOT the researched one.** The June study read the *current* day's daily close and forward-filled it onto the hourly grid, so a fire read a close up to 21h in its own future. Production sets `REGIME_SHIFT_DAYS = 1` — every fire reads a close at least 3h old. On the ten out-of-sample fires that scores **+0.246 R** against the peeking gate's +0.202. Quote the backward-only number.
- **Caveat**: the frozen rule says BUILD, but every margin is one fire wide — the n ≥ 10 floor is met with margin **zero**, mean R +0.2021 against a +0.10 floor, combined MAR 1.60 against 1.50. Deflated Sharpe on those ten fires is **0.72** even with no multiple-testing penalty, and the whole OOS record rests on one three-week bull run in spring 2026. The re-cut points at n=20 and n=30 were fixed in advance — [docs/calibration/squeeze_bull.md](docs/calibration/squeeze_bull.md), [studies/notebooks/squeeze_bull_revalidation/findings.md](studies/notebooks/squeeze_bull_revalidation/findings.md).

---

## 4. Sizing

**Each bot sizes its own fires against its own capital.** There is no
portfolio-wide allocator any more: `strategies/support/allocation.py` and its
`WEIGHT_TABLE` were deleted with the orchestrator on 2026-09-13. Two shapes
are in use.

**1. Fixed-R** — every bot except `carry` and `r4`, via
`botlib.size_intent_fixed_r` ([botlib.py](botlib.py)):

```
notional = capital × RISK_PCT% / stop_pct      (capped at NOTIONAL_MAX_X × capital)
```

The stop distance is the one the sleeve itself computed into the Intent
(`_entry_price` / `_stop_price`), so R-space results are the study's and only
dollars-per-R change. Per-bot values live in `bots/<name>/config.py`: ADX and
both chento legs risk 2%, the two squeeze bots 1%; all cap at 3× notional per
position (chento positions stack, so its gross is not capped — §8).
Fixed-R beat fixed-notional across the sizing study, and the 3× cap is a
structural guard, not a working dial — except on `short_squeeze`, whose stop is
often basis points wide, where it binds by design.

**2. Fixed notional** — `carry` runs 1× capital (delta-neutral, no stop to size
from), and the two no-stop paper twins run a fixed notional chosen to match
their stop-variant sibling (§3.5, §3.10).

**`r4` is the exception**, and the only remaining consumer of the J+ stack
below: capital × 0.20 per window × the sleeve's stacked leverage (inner gate ×
vol-target, capped at 7.5×), scaled down to a 3× gross co-fire budget — §3.9.

```
                 ┌──────────────────────────────────────┐
                 │   regime classifier (T-1 inputs)     │
                 │   strong_bull / mild_bull /          │
                 │   uncertain / bear                   │
                 └──────────────┬───────────────────────┘
                                │
                                ▼
                 ┌──────────────────────────────────────┐
                 │   inner leverage                     │
                 │   r4 only: 2.5× / 1.0× via the       │
                 │   vol-percentile gate (gate.py)      │
                 └──────────────┬───────────────────────┘
                                │
                                ▼
                 ┌──────────────────────────────────────┐
                 │   vol-target outer leverage          │
                 │   (voltarget.py)                     │
                 │   floor 0.5×, regime-capped 1.5–3.0× │
                 └──────────────┬───────────────────────┘
                                │
                                ▼
        r4 fire notional = capital × 0.20 × min(inner × voltarget, 7.5)
                           , clipped to the 3× gross budget
```

Both layers are assembled by `jplus_inputs.today_inputs()`
([strategies/support/jplus_inputs.py](strategies/support/jplus_inputs.py)),
which the r4 runner calls once per evaluation.

> **History (retired 2026-09-13).** The pipeline used to have a third layer
> above these two — a per-`(regime, sleeve)` weight from
> `allocation.WEIGHT_TABLE`, regime-independent for S-003 / S-078 / AI_QUANT /
> the timing substrategies and regime-keyed for the six J+ rows, rescaled at
> lookup time by `CORE_ALLOC_CAP = 0.50`. It bounded sleeves sharing one
> variant's capital; with one variant per bot there is nothing left to share.
>
> §4.2's weight tables are kept because `REGIME_WEIGHTS_FULL` and
> `CORE_ALLOC_CAP` still live in `jplus_inputs.py` and the r4 sleeve still
> reads them — but as a **gate, not a size**. A zero weight returns
> `regime_zero_weight` and no trade (this is what makes `bear` untradeable for
> r4); the magnitude is then thrown away and replaced by the bot's own 0.20 per
> window in `runner.size_intent`. The EMA_BTC and ETH_DAILY rows of those
> tables are read by nothing, their sleeves being archived.

### 4.1 Regime classifiers

Two parallel classifiers run on the same BTC daily series; each sleeve consumes
whichever fits its trade thesis. Both are strictly T−1 (look-ahead safe).

**J+ classifier** ([strategies/support/regime_jplus.py](strategies/support/regime_jplus.py)) — 4 modes, consumed by `jplus_inputs.today_inputs()`, i.e. by the r4 bot:

| Mode | Trigger |
|---|---|
| **strong_bull** | close > EMA(50) AND close > EMA(20) AND m30 > 0 AND m7 > 0 |
| **mild_bull** | close > EMA(50) AND (m30 > 0 OR close > EMA(20)) |
| **bear** | close < EMA(50) AND m30 < 0 |
| **uncertain** | otherwise; or peak-DD > 5% while bullish; or LS circuit-breaker active |

Two overrides:
- **LS circuit breaker** — 7-day LSR delta < −15 forces `uncertain` for the next 7 calendar days.
- **Peak-DD override** — close > 5% off trailing peak demotes any bullish label to `uncertain`.

**Tactical classifier** ([strategies/support/regime_tactical.py](strategies/support/regime_tactical.py)) — 4 modes. It fed THU_BEAR's V3 prev-day filter and PDO_L_RF's `regime_threshold_pct` skip; both were archived 2026-09-13, so **no running bot consumes it today**. The module stays in `strategies/support/` with its look-ahead tests ([tests/test_jplus_lookahead.py](tests/test_jplus_lookahead.py), [tests/test_regime_classifier.py](tests/test_regime_classifier.py)):

| Mode | Trigger |
|---|---|
| **bull_trend** | 50d SMA 10-day slope > +0.5% of price, RV not extreme |
| **bear_trend** | 50d SMA 10-day slope < −0.5% of price, RV not extreme |
| **chop** | \|slope\| ≤ 0.5% (dead-band) |
| **sell_off** | RV percentile ≥ 75th AND close < 50d MA AND slope < 0 |

The two vocabularies coexist because they came from different research lines.
Unifying them is on the backlog but isn't load-bearing.

### 4.2 Per-regime weights — now an r4 gate only

Read §4's history note before using these numbers. `today_inputs()` still
computes them, and the r4 sleeve still reads its own row to decide **whether**
to trade (`weight <= 0` ⇒ `regime_zero_weight`, no fire) — but the bot then
sizes at its own flat 0.20 per window. The EMA_BTC / ETH_DAILY columns are
read by nothing since those sleeves were archived.

**J+ raw weights** (`REGIME_WEIGHTS_FULL` in [strategies/support/jplus_inputs.py](strategies/support/jplus_inputs.py)):

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

The bot historically spent ~62% of time in `uncertain` and ~25% in `bear`.
Read down the R4_ETH and R4_ETH_V2 columns for what still matters: both are
zero in `bear`, which is the whole of the regime gate r4 now applies.

> **History (retired 2026-09-13).** The other sleeves drew regime-independent
> rows from `allocation.WEIGHT_TABLE` — S-003 ADX 0.15, S-078 Carry 0.08,
> THU_BEAR 0.06, PDO_L_RF 0.09, CPR 0.05, FOMC 0.05, AI_QUANT 0.02 — and
> SHORT_SQUEEZE was never added to it, sizing from its own config instead.
> That table is gone; every bot now sizes from its own config, which is what
> SHORT_SQUEEZE was already doing.

### 4.3 Inner-leverage gate (r4 only)

The r4 windows stack an inner amplifier on top of the raw windowed return:

| State | Multiplier | Reason |
|---|---|---|
| Normal day | **2.5×** | the sleeve's designed amplification |
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
`lev = 2.0`, nothing else open:

```
stacked_lev = inner_lev(2.5) × vol_target(2.0) = 5.0     (cap 7.5 not reached)
notional    = capital × bot_weight(0.20) × 5.0 = 1.0 × capital
```

So the fire is **1× capital of ETH perp**, or 1× of the raw spot move —
inside the 3× gross co-fire budget, leaving 2× for the Wednesday overlap with
R4_ETH_V2. The regime weight from §4.2 does not enter the arithmetic; it only
had to be non-zero for the fire to happen at all.

> **History (retired 2026-09-13).** The same fire under the orchestrator
> multiplied the raw return by `inner_lev(2.5) × capped_weight(0.148) ×
> vol_target(2.0) = 0.74×`, on ~15% of the shared variant's capital. The bot's
> flat 0.20 is deliberately larger: it is 20% of its *own* $10k, not a share
> of a pool eight sleeves were drawing on.

---

## 5. Cross-strategy coordination

> **Mostly retired 2026-09-13.** Cross-sleeve coordination existed because
> eight sleeves shared one variant's capital and one margin pool. Each bot now
> has its own variant, its own capital and its own position rules (§8), so there
> is nothing left to arbitrate between them: **no live process calls
> `reconcile_intents()`**, and the co-fire budget that *is* enforced is r4's
> own `GROSS_MAX_X` (§3.9), inside one bot. The modules described in §5.1 and
> §5.3–5.5 still exist and still pass their tests; `gating.py` and
> `portfolio_vol.py` were deleted outright. Read this section as the record of
> a mechanism, and check §6 for what any given module does today.

### 5.1 Two-phase decide/execute + the reconcile pass

The **two-phase split survives and is the live contract**: every strategy
module exposes `decide(...) → (list[Intent], status_dict)` — reads inputs,
evaluates signals, runs side-effect bookkeeping (stop sweeps, scheduled
closes), and returns entry intents with **no fresh-open side-effects** — and
`execute(variant, intent) → status_dict`, which opens the trade. The runner
sizes the Intent between the two calls (§4). r4 exposes one `decide_*` per
window.

`Intent` ([strategies/support/dispatch.py](strategies/support/dispatch.py))
is a frozen dataclass: `asset`, `direction`, `allocation_pct`, `leverage`,
`conviction (0–100)`, `priority`, `reason` (dict persisted to `trades.notes`),
`scheduled_exit_dt`.

What has **no live caller** is the reconcile pass. Under the orchestrator,
after every two-phase sleeve had returned intents, `reconcile_intents()` ran
once:

1. **Conviction-weighted signal pooling** (`_pool_concordant_allocations`). Same-(asset, direction) intents have their allocations redistributed so the *total* equals the conviction-weighted average alloc, not the sum. Two sleeves agreeing LONG BTC don't produce 2× exposure; they produce the conviction-weighted-avg sleeve's full size, split by share.
2. **Sort** by `(priority asc, −conviction desc)`. Sleeves declare `priority` in composition (default 100); ties break on conviction.
3. **Per-intent loop**, in priority order:
   - **Directional conflict check** — reject if a higher-priority intent already approved the opposite direction on this asset. Pre-seeded with `existing_directional_opens` from the DB so legacy positions count too.
   - **Margin headroom** — full size if fits; clamp + `approved_reduced` if it fits at ≥50% of intended; reject otherwise.
4. Returned a parallel `ReconcileResult` list. The orchestrator called each sleeve's `execute_for_variant(intent)` only for approved / approved_reduced / approved_pooled results, and logged the rest.

CARRY's perp SHORT was excluded from both conflict and pool checks
(delta-neutral collateral). FLAT intents (close-existing) always passed through.

### 5.2 Gating framework — deleted 2026-09-13

`strategies/support/gating.py` held a `GATE_REGISTRY` of
`(strategy_id, regime, now_utc) → GateDecision(fire, leverage_mult, reason)`
callables that the orchestrator looked up per sleeve per tick and injected as
`_effective_gate`. It registered three gates:

| Gate | Type | What it did | Today |
|---|---|---|---|
| R4 vol-gate | leverage modulator | BTC 30d realized vol > 75th-percentile (365d window) → R4 inner leverage 1.0× instead of 2.5× | **still live**, read directly from [gate.py](strategies/support/gate.py) via `jplus_inputs` (§4.3) |
| THU_BEAR V4 | binary | CPI/NFP-adjacent Thursday + ex-OPEX → fire; otherwise block | archived with the sleeve |
| FOMC composite | binary | Phase × F&G × Polymarket-cut-prob filter via the `fomc_observer` table | archived with the sleeve |

Walk-forward CV protocol for adding/rebuilding a gate: [GATE_VALIDATION.md](GATE_VALIDATION.md).
The R4 vol-gate is calibrated on BTC vol percentile (not the R4 trade series), so it carries less in-sample selection-bias risk than V4 / FOMC, both of which were derived post-hoc — which is a fair part of why it is the one that survived.

### 5.3 Margin headroom

Tracks a variant's gross perp-notional vs cap and tells a caller how much
room it has ([strategies/support/margin_headroom.py](strategies/support/margin_headroom.py)).
Sums `size_usdt` across open paper trades (already-leveraged notional from
`trades.open_paper_trade`) — no double-leveraging.

Its only caller was the reconcile pass, so **nothing calls it today**. The
per-bot equivalent that does run is `botlib.open_gross_usdt(variant_id)`,
which r4 uses for its co-fire budget. The old cap default came from
`spec.allocator_notes.gross_notional_target_x` (2.5× capital); `p300_spec.py`
is gone.

### 5.4 Directional conflict resolver

Catches opposing-direction perp opens on the same asset within a variant —
e.g. ADX wanting LONG BTC while THU_BEAR wanted SHORT BTC on the same Thursday.
([strategies/support/conflict_resolver.py](strategies/support/conflict_resolver.py)).
Cross-bot conflict is now structurally possible — chento can be short BTC while
ADX is long it — and is **not** resolved: the bots are separate books by
design. Two surfaces:

- `detect_opposing_open(variant_id, asset, direction)` — returns the earliest open trade with opposite direction, or None. Used by the reconcile pass to seed its conflict state with positions opened before reconcile ran.
- `current_directional_opens(variant_id) → {asset: direction}` — one-shot snapshot for the reconcile seed and operator dashboards.

CARRY's perp SHORT is excluded by design (delta-neutral collateral). The
exclusion list is shared with `signal_aggregator.py` and `dispatch.py`'s
reconcile loop.

### 5.5 Signal aggregator (detect-only)

Read-side dual of conflict_resolver — surfaces same-asset, same-direction
stacks (e.g. ADX LONG BTC + chento LONG BTC) for audit dashboards.
([strategies/support/signal_aggregator.py](strategies/support/signal_aggregator.py)).
**Does not pool** — that was the reconcile pass (§5.1). Detect-only, and no
live consumer today.

### 5.6 Portfolio vol-target scalar — deleted 2026-09-13

`strategies/support/portfolio_vol.py` was a per-tick scalar that re-targeted
one variant's gross exposure to an annualized-vol budget (default 30%),
computed from realized NAV over a 30-day rolling window
(`scalar = target_vol_annual / realized_vol_annual`, clamped to
`[0.5, 3.0]`, None below 10 NAV observations). It was opt-in per variant via
`spec.allocator_notes.use_portfolio_vol`. Vol targeting today is
`voltarget.py` inside the r4 stack (§4.4) and nothing else.

---

## 6. Support module ↔ bot matrix

`strategies/` holds **no strategy** since 2026-09-13: only
[trades.py](strategies/trades.py) (the ledger-write layer) and
[support/](strategies/support/) (shared live-path modules). Every relationship
below is a **direct import** — there is no injection layer any more.

**Live path — imported by a running bot:**

| Support module | Imported by | What for |
|---|---|---|
| [`clock.py`](strategies/support/clock.py) | every runner + every strategy | the only source of "now"; look-ahead-safe |
| [`db.py`](strategies/support/db.py) | everything | `prod.db` path + connection policy |
| [`env.py`](strategies/support/env.py) | every runner, `feed.py`, `monitor.py` | `load_env_file` |
| [`dispatch.py`](strategies/support/dispatch.py) | every strategy module | the `Intent` dataclass (§5.1) |
| [`price_feed.py`](strategies/support/price_feed.py) | every strategy at entry/exit | 1m spot price, strict-`<` |
| [`trade_db.py`](strategies/support/trade_db.py) + [`strategies/trades.py`](strategies/trades.py) | every bot | single writer for `trades` + `trade_adjustments` |
| [`trade_adjustments.py`](strategies/support/trade_adjustments.py) | `strategies/trades.py` | partial-fill / add bookkeeping |
| [`instance_guard.py`](strategies/support/instance_guard.py) | `botlib.py`, `strategies/trades.py` | one writer per variant |
| [`jplus_inputs.py`](strategies/support/jplus_inputs.py) | r4 | `today_inputs()` snapshot (regime + lev + gate + ema_p + weights) |
| [`regime_jplus.py`](strategies/support/regime_jplus.py) | `jplus_inputs.py`, `ema_position.py` | the 4-mode J+ classifier (§4.1) |
| [`gate.py`](strategies/support/gate.py) | `jplus_inputs.py` | R4 vol-percentile gate (§4.3) |
| [`voltarget.py`](strategies/support/voltarget.py) | `jplus_inputs.py` | vol-target outer leverage (§4.4) |
| [`r4_windows.py`](strategies/support/r4_windows.py) | `jplus_inputs.py` | R4 window predicates for the decision loop |
| [`ema_position.py`](strategies/support/ema_position.py) | `jplus_inputs.py` | `ema_p` — moved down from the archived EMA sleeve, 2026-09-13 |
| [`indicators.py`](strategies/support/indicators.py) | adx, `regime_jplus.py` | EMA / ADX / ATR |
| [`stop_path.py`](strategies/support/stop_path.py) | adx, `strategies/trades.py` | which level closed a trade; pinned by `tests/test_stop_resolver_inversion.py` |
| [`risk_config.py`](strategies/support/risk_config.py) | adx | price-move vs margin stop semantics |
| [`funding.py`](strategies/support/funding.py) | carry | daily funding sum — the signal *and* the P&L |
| [`equity.py`](strategies/support/equity.py) | `strategy_health.py` | NAV series; `_NO_FUNDING` keeps `PDO_RETOUCH` |
| [`ledger_coherence.py`](strategies/support/ledger_coherence.py) | `strategy_health.py` | ledger-vs-NAV invariant |
| [`strategy_health.py`](strategies/support/strategy_health.py) | dashboard, `health.py`, runner startup banner | read-side; the pre-registered instrument for the n=20/30 re-cuts |
| [`variant_registry.py`](strategies/support/variant_registry.py) | `botlib.py` | variant rows + `variant_events` |

**Present but with no live caller** — kept, tested, and documented here so
nobody mistakes them for wiring:

| Support module | Status |
|---|---|
| [`dispatch.reconcile_intents`](strategies/support/dispatch.py) | the reconcile pass (§5.1) — tests only |
| [`margin_headroom.py`](strategies/support/margin_headroom.py) | called only by the reconcile pass (§5.3) |
| [`conflict_resolver.py`](strategies/support/conflict_resolver.py) | called by the reconcile pass and `signal_aggregator.py` (§5.4) |
| [`signal_aggregator.py`](strategies/support/signal_aggregator.py) | detect-only, no consumer (§5.5) |
| [`risk_caps.py`](strategies/support/risk_caps.py) | the PDO+CPR BTC-LONG cap, reached via `margin_headroom.py` |
| [`regime_tactical.py`](strategies/support/regime_tactical.py) | its two consumers (THU_BEAR, PDO) are archived (§4.1) |
| [`margin_sim.py`](strategies/support/margin_sim.py) | liquidation simulator; was per-tick under the orchestrator, now tests only (`margin_check.py` was deleted 2026-09-13) |
| [`sleeves.py`](strategies/support/sleeves.py) | `live_pnl_pct` / `is_sl_hit` helpers |
| [`sim_loop.py`](strategies/support/sim_loop.py) | the sim tick primitive; the sim entry point was deleted 2026-09-13 (§11) |

---

## 7. External data feeds

Cached snapshots of upstream public APIs the bot polls daily.
[`data/sources/binance.py:_refresh_daily_external`](data/sources/binance.py)
runs once per UTC day; sim-mode-aware (no-op when `clock.is_simulated()`).

| Feed | Cache | Consumer |
|---|---|---|
| Crypto Fear & Greed | `prod.db:fear_greed_index` | dashboard; stamped onto trade rows by `trade_db.py`. *(Was the FOMC gate + AI_QUANT context — both archived.)* |
| Fed Funds target rate | [`data/archive/fed_funds_target_upper.json`](data/archive/) (parsed from `nyfed_rates.xml`) | *(archived FOMC phase classifier; still refreshed daily)* |
| Polymarket cut-probability | [`data/archive/polymarket_fed_2026.json`](data/archive/) | *(archived FOMC composite gate; still refreshed daily)* |
| Binance klines / funding | `prod.db` (btc_1m, eth_1m, cd_funding_rate, cd_spot_binance, ca_long_short_ratio) | every bot + the regime classifier |
| Open interest | `prod.db:cd_open_interest` | squeeze_bull (the flush trigger), short_squeeze (Asia-grind gate) |
| 15m futures/spot CVD | `prod.db` (cd_futures_15m, cd_futures_eth_15m, cd_spot_15m) | chento B1/B7, short_squeeze divergence |
| OKX perp | `prod.db` (okx_perp_1h, okx_perp_eth_1h) | chento loads it and computes `okx_delta_z` each rebuild, but no decision reads it, nothing records it, and it is no longer an entry table; the feed and its freshness contract stay. *(Was chento's cross-exchange alignment gate — retired 2026-09-13, off since 2026-09-14.)* |
| Macro daily / PAXG | `prod.db` (macro_daily, paxg_spot_1h) | research only (added 2026-09-06; the gold overlay was killed — §9.4) |
| News headlines | `prod.db:news_headlines` | *(was AI_QUANT context only; gated table, not live-read)* |
| CoinDesk derivatives | `prod.db` (cd_liquidations, cd_dvol) | *(gated tables; research only)* |

Which tables must be **fresh** for a given bot to act is that bot's
`MGMT_TABLES` / `ENTRY_TABLES` in `bots/<name>/config.py`: a stale mgmt table
skips the whole tick, a stale entry table still lets the position sweep run but
discards any new Intent.

---

## 8. Risk profile

Per-sleeve historical performance previously lived in this doc through
2026-05-13; the v6 backtest figures predated several material fixes (Jensen-gap
in compound-equity, slippage model, `CORE_ALLOC_CAP`, EMA/ETH_DAILY funding +
fee correction, liquidation simulator) and were removed. Run
[studies/notebooks/full_portfolio_report.ipynb](studies/notebooks/full_portfolio_report.ipynb)
against the live trade ledger for current numbers.

**Notional per bot.** Each variant has its own $10,000 and its own cap, so
there is no portfolio-level gross number any more — add the per-bot ceilings if
you want the fleet's worst case (chento's is per position, and its positions
stack):

| Bot | Ceiling per open position | Concurrency |
|---|---|---|
| adx, short_squeeze | 3× capital (`NOTIONAL_MAX_X`) | single-open guard — one position at a time |
| chento_v3, chento_v3_eth | 3× capital (`NOTIONAL_MAX_X`) | **no single-open guard — positions stack**, bounded only by the 6h cooldown and the 72h TIF (structural max 12 open per bot); no aggregate cap |
| squeeze_bull | 0.5× capital in practice (1% risk over a 2% stop); 3× cap never binds | single-open, per variant |
| carry | 1× capital, delta-neutral | one position |
| r4 | 1.5× capital per fire (0.20 × 7.5×) | **up to 3× gross across overlapping windows** |

**Overlapping R4 windows are by design** — the calendar anomalies genuinely
overlap, so a Wednesday in week 1-2 can hold R4_ETH (still open from Tuesday)
alongside R4_ETH_V2, and a third if the BTC V2 window is ever re-enabled.
Never serialise them; overlap risk is a sizing question, which is what
`GROSS_MAX_X`, the per-window weight and the 7.5× leverage cap answer.

**Stacked chento positions are by design too — but nothing budgets them.**
Each signal is its own trade; the live BTC bot already held 3 overlapping
signals on 2026-08-22 while the OKX gate was still on. The 2026-09-14 sizing
review replayed the re-validation's ungated trades in the bots' own sequence:
BTC peaks at 5 open positions, 10% of capital at risk, 8.53× gross notional and
a 33.4% mark-to-market max drawdown (gated: 23.2%); ETH peaks at 6 open, 12% at
risk, 4.39× gross and 20.9% (gated: 10.9%). Paper keeps `RISK_PCT` 2% and the
per-trade 3× cap. Before any real capital, a per-bot open-risk or gross budget
(or lower risk) goes in as its own pre-registered test.

**Per-strategy time-occupancy** (approximate):

| Strategy | Time in market | Why |
|---|---|---|
| CARRY | ~90% | always holding while funding regime is positive |
| ADX | ~50% | trend follower; in market roughly half the time |
| R4 (ETH pair) | calendar-windowed | Tue 20:00 → Wed 20:00 plus Wed/Fri 04:00–14:00, weeks 1-2 only |
| CHENTO_TRIPLE_V3 | low | ~36 BTC / ~34 ETH trades/yr with the OKX gate off (study rate; ~17 / ~15 gated), 72h TIF, positions can overlap |
| SQUEEZE_BULL | low | bull-regime flushes only, ≤48h |
| SHORT_SQUEEZE | low | London/NY sweeps only, ≤6h |

> **History.** The pre-2026-09-13 version of this table also carried
> CPR (~100%), THU_BEAR (<10%), PDO_L_RF (~1%) and FOMC (<1%), and noted that
> FOMC was time-disjoint from THU_BEAR (FOMC always Tue/Wed) and effectively
> disjoint from everything else. Those sleeves are archived. It also quoted
> whole-portfolio concurrent notional of mean ≈80% / P95 ≈120% / max ≈150% of
> capital under the single shared variant, and explained that the V2 R4 sleeves
> were sized at half the V1 regime weights to keep peak Wednesday exposure
> comparable to the pre-V2 baseline — the bot weights them equally instead
> (§3.9) and bounds the overlap with `GROSS_MAX_X`.

---

## 9. Methodology caveats

1. **In-sample selection bias.** The Aggressive 2.0 family was chosen from
   {Conservative / Regime-dynamic / Kelly / Aggressive} based on backtest
   performance. Regime weights, R4 windows, and ETH weights were tuned on
   roughly the same era of data. Live forward performance will likely be lower
   than in-sample. *(The variant-level portfolio this describes was retired
   2026-09-13; the selection bias baked into the surviving R4 windows is not.)*

2. **R4 windows-grid selection (2026-05-08).** The R4 configs for
   `JPLUS_R4_BTC` (Mon wk1-2 06→18 UTC, was Mon+Wed) and the V2 windows
   (Wed+Fri wk1-2 04→14 UTC) were picked from a 7,500-config grid search over
   (asset × day × week × start-hour × end-hour) using "57 configs that were
   positive in every backtest year." Empirically defensible (t = +4.6
   in-sample over 402 fires; +2.5 OOS walk-forward per
   [studies/notebooks/r4_study/findings.md](studies/notebooks/r4_study/findings.md))
   but post-ETF era (2024-01 onward, ~2.3y) is too short for the post-ETF
   walk-forward to be conclusive. User's decision to run the live config and
   monitor expectancy via [strategy_health.py](strategies/support/strategy_health.py)
   is documented in
   `memory/feedback_r4_post_etf_ride_with_monitor.md`. **Narrowed 2026-09-12**:
   only the two era-stable ETH windows are enabled, and the deeper objection —
   that nobody can state the mechanism — is §3.9's caveat.

3. **Look-ahead protections are real and tested**
   ([tests/test_jplus_lookahead.py](tests/test_jplus_lookahead.py)). The
   upstream ML R4 gate had within-day look-ahead and was REPLACED by a
   rule-based gate. Not every input has been audited at the same depth.

4. **GOLD overlay is dropped — now on evidence, not on missing data.** Upstream
   P-100 J+ MLgate had a 15–55% GOLD allocation as crisis hedge. The old reason
   for dropping it ("p300 has no `macro_daily` table and the asset isn't
   wired") is out of date: `macro_daily` and `paxg_spot_1h` were both added
   2026-09-06. The question was then studied properly and killed —
   [studies/notebooks/anchor_allocator_study/findings.md](studies/notebooks/anchor_allocator_study/findings.md)
   (2026-09-07, CONCLUDED KILL, all three pre-registered clauses fired):
   overflow-to-anchor as specified *lowers* full-period Sharpe by 0.23 and
   *deepens* max drawdown by 6.6pp over 2020-01→2026-09. A **static** gold
   sleeve with no overflow is the only positive cell (+0.39 Sharpe, −3.9pp MDD)
   — that is gold beta over this era, not the overflow mechanism. Routing idle
   capital to cash yield alone is worth +0.11 Sharpe at unchanged drawdown. The
   crypto side stands at 1.0 weight; no production change was made.

5. **FOMC sleeve has only 11 in-sample backtest events** (added 2026-04-30).
   100% in-sample win rate. Bootstrap on the 52-event historical cohort
   (73% win) projects ~12% sleeve max DD at 10× leverage. Live edge unproven.
   *(Archived 2026-09-13 with the test never run: `mid_hold` is the standing
   decision in this rate environment, i.e. skip.)*

6. **CPR's historical sample is thin** (12 BTC + 9 ETH events upstream) and
   **PDO_L_RF's params** were selected via parameter sweeps without
   walk-forward CV — both carry data-snooping exposure. *(Both archived
   2026-09-13; CPR's TradingView re-validation and PDO's re-check are two of
   the three open questions the archive stays importable for.)*

7. **THU_BEAR V4 event filter** (CPI/NFP-adjacent, ex-OPEX) was derived
   post-hoc from V3's Thursday attribution. V4 backtest comparisons are
   in-sample; live paper would have been the first genuine OOS record.
   *(Archived 2026-09-13 with 2 closed trades — the out-of-sample question is
   the third open one.)*

8. **Deflation and bootstrap CIs now exist — and nothing clears the bar.**
   Headline Sharpes here are still *undeflated point estimates*; the deflated
   ones are in
   [studies/notebooks/validation_audit_2026_09/findings.md](studies/notebooks/validation_audit_2026_09/findings.md)
   (2026-09-08), which ran the ported validation toolkit over p300's own record.
   Headlines: **no series in the audit reaches DSR ≥ 0.95** — the best is
   chento BTC+ETH combined at **0.876** (N=40 documented variants) / 0.744
   (N=120); chento BTC alone 0.726 / 0.550; ADX Tier-2 0.732 (N=17) / 0.496
   (N=40). Harvey-Liu Holm cuts ADX Tier-2's daily Sharpe 1.12 → 0.69 and
   chento BTC's 1.02 → 0. **The paper ledgers are statistically empty**: six
   bots have six closed trades between them, no single-strategy series exceeds
   13, and the entire 13-trade legacy R4 paper record sits inside the
   pre-2026-05-16 untrustworthy window (zero trustworthy R4 paper trades
   remain). Two measurement corrections fell out of the audit: chento's quoted
   **~+0.8R/trade is a zero-cost figure** (+0.69R BTC / +0.62R ETH once the
   source pool's own 18bp model is charged; *2026-09-14: every chento figure
   in this item, DSRs included, was measured on OKX-aligned pools, and that
   gate is retired — §3.7*), and the ADX study's "Sharpe 2.09"
   is a **per-trade t-statistic**, not an annualised Sharpe (that is 0.84
   baseline / 1.12 Tier-2). The audit imposed no KILL rules and changed no
   config. Still missing: White's reality check / SPA, and a full-grid PBO
   (only a partial 4-variant tilt-family PBO was reconstructible from disk).
   **Sharpe is computed with risk-free rate = 0**
   — `(mean / sd) × √365` in
   [strategy_health.py](strategies/support/strategy_health.py) and the report
   notebooks (it was the same in `backtest_runner.py`, deleted 2026-09-13). At
   a 4–5% Fed funds rate, that overstates Sharpe by ~0.5–0.7. The crypto
   convention is rf = 0; we follow it but note the gap.

9. **Daily-NAV MDD understates intraday DD at 5–10× leverage** in stress
   regimes. Factor this into any risk claim.

10. *(Retired 2026-09-13.)* **Live BTC-LONG cap (skip-if-over) vs simulator cap
    (proportional down-scale) diverged by construction** — live NAV ≠ sim NAV
    even with identical signals. Both the shared BTC-LONG cap and the simulator
    are gone (§5.3, §11), so the divergence has no live surface; the general
    lesson stands, that a cap expressed as *skip* and a cap expressed as
    *scale* are different strategies.

11. **AI_QUANT is excluded from all backtest figures.** Non-deterministic
    (LLM outputs vary run-to-run) and was skipped on historical replay via
    `params.deterministic=False`. Its edge — if any — would only have been
    visible in forward paper PnL, evaluated *net of API cost* (capped at
    $5/day, ~$1,825/yr against a 2% sleeve). *(Archived 2026-09-13 on 4 closed
    trades; the question was never answered.)*

12. **Execution-cost model — fees + slippage, modeled separately.**
    `strategies.trades.compute_perp_close` charges round-trip cost as
    `(cost_bp_rt + slippage_bp_rt)` against notional at close. Defaults
    (since 2026-05-13, audit-calibrated):
    - `DEFAULT_COST_BP_RT = 10.0` — Binance taker fee × 2 legs.
    - `DEFAULT_SLIPPAGE_BP_RT = 5.0` — conservative mid of the 5–10bp/RT
      retail bid-ask spread + market-impact band at $1k–$20k notional.
    - **FOMC override: `SLIPPAGE_BP_RT = 10.0`** — 10× leverage at the
      announcement bar, when BTC/USDT spread widens. *(Archived with the
      sleeve, 2026-09-13.)*
    - **CARRY: `CARRY_SLIPPAGE_PCT = 0.04`** on top of the 20bp fee — 4 fills
      × 1bp limit-style slip on the synthetic spot+perp position.
    - **JPLUS_EMA_BTC and JPLUS_ETH_DAILY** *(both archived 2026-09-13)* used
      the same default (10bp fee + 5bp slip + funding accrual) on close.
      Pre-2026-05-13 these sleeves
      passed `cost_bp_rt=0.0, apply_funding=False` because ETH_DAILY's status
      was framed as "pending a spot-fee model" — but both trade the *perp*,
      so the zero-funding default was structurally wrong (multi-week ETH-LONG
      perp at 8h funding ~0.005% accrues to ~4.5%/yr).
    - **Pre-2026-05-13 paper PnL** was net of fees only. Forward numbers from
      2026-05-13 onward are net of fees + slippage; older backtest figures
      were not. Treat the step-down on 2026-05-13 as a methodology change,
      not a regime change.
    - **Measured and adopted 2026-09-12**
      ([execution_2026_09](studies/notebooks/execution_2026_09/findings.md)):
      on seven years of 1 m bars the taker round trip is 9.6 bp (chento BTC),
      10.0 (chento ETH), 9.3 (SHORT_SQUEEZE), 6.7 (SQUEEZE_BULL) and 10.5 bp
      (ADX) — half-spread < 1 bp, decision-to-fill drift within ±3 bp, zero
      stop gap-throughs. The sleeves now book **chento 10 bp** (was 18),
      **SHORT_SQUEEZE 10** (was 10 + 15), **SQUEEZE_BULL 7** (was the 15 bp
      default), **ADX 10 + 1** (was 10 + 5); CARRY and the default constants
      above are unchanged. SHORT_SQUEEZE's old 25 bp was 0.80 R per trade on a
      +0.48 R gross edge, so its paper record before this date is negative by
      construction. Treat the step on 2026-09-12 as a methodology change, as
      on 2026-05-13; each sleeve's calibration log carries the provenance.

---

## 10. Where to look in the code

**Entry points**

| Concern | File |
|---|---|
| Start the fleet (feed + 7 runners + dashboard) | [start_fleet.ps1](start_fleet.ps1) |
| Market-data feed daemon (the only process that fetches) | [feed.py](feed.py) |
| Health invariants | [health.py](health.py) |
| Freshness + heartbeat + ghost-registry checks | [monitor.py](monitor.py) *(not scheduled; run by hand)* |
| Dashboard (`http://127.0.0.1:8300`) | `dashboard/server.py` |
| Shared bot runtime (heartbeats, variant rows, sizing, stale-table policy) | [botlib.py](botlib.py) |
| One-time data bootstrap | [bootstrap.py](bootstrap.py) |
| DB backup | [backup.py](backup.py) |

**Bots** — each directory holds `runner.py`, `config.py` and `strategy/`:

| Bot | Directory | Calibration log |
|---|---|---|
| S-003 ADX | [bots/adx/](bots/adx/) | [docs/calibration/adx.md](docs/calibration/adx.md) |
| S-078 Carry | [bots/carry/](bots/carry/) | [docs/calibration/carry.md](docs/calibration/carry.md) |
| CHENTO_TRIPLE_V3 (BTC) | [bots/chento_v3/](bots/chento_v3/) | [docs/calibration/chento_triple_v3.md](docs/calibration/chento_triple_v3.md) |
| CHENTO_TRIPLE_V3 (ETH) | [bots/chento_v3_eth/](bots/chento_v3_eth/) — runner + config only; imports `bots/chento_v3/strategy/` | same log |
| S-105 SHORT_SQUEEZE | [bots/short_squeeze/](bots/short_squeeze/) | [docs/calibration/short_squeeze.md](docs/calibration/short_squeeze.md) |
| S-107 SQUEEZE_BULL | [bots/squeeze_bull/](bots/squeeze_bull/) | [docs/calibration/squeeze_bull.md](docs/calibration/squeeze_bull.md) |
| R4 calendar | [bots/r4/](bots/r4/) (+ [bots/r4/windows.py](bots/r4/windows.py), the shared window predicates) | [docs/calibration/r4.md](docs/calibration/r4.md) |

**Archived strategies** — see §2.1 for the full table and the contract:
[studies/material/archive/](studies/material/archive/).

**Plumbing (see §6 for the full ownership matrix)**

| Concern | File |
|---|---|
| `Intent` contract (+ the retired reconcile pass) | [strategies/support/dispatch.py](strategies/support/dispatch.py) |
| Look-ahead-safe clock | [strategies/support/clock.py](strategies/support/clock.py) |
| Live price feed (1m spot, strict-`<`) | [strategies/support/price_feed.py](strategies/support/price_feed.py) |
| Ledger writes (open / close / adjust) | [strategies/trades.py](strategies/trades.py) |
| Realized PnL aggregation | [strategies/support/strategy_health.py](strategies/support/strategy_health.py) (`trades_daily_returns`) |
| Trade-row schema + standardized close log | [strategies/support/trade_db.py](strategies/support/trade_db.py) |
| Gate validation protocol | [GATE_VALIDATION.md](GATE_VALIDATION.md) |
| FOMC observer audit log (archived sleeve, live table; DDL in [bootstrap.py](bootstrap.py)'s `SCHEMAS`) | `data/databases/prod.db:fomc_observer` |
| Operating the fleet | [OPERATIONS.md](OPERATIONS.md) · roadmap: the top of [BACKLOG.md](BACKLOG.md) |

---

## 11. Running it

Every bot is one process reading `data/databases/prod.db`, which
[feed.py](feed.py) keeps fresh, and writing its trades back to the same file.
Nothing here places an order on an exchange: `execution_mode='paper'` is the
only mode that exists.

```powershell
# Everything that isn't already up: feed, the seven runners, the dashboard.
# The duplicate guard skips a unit that is already running.
.\start_fleet.ps1

.\start_fleet.ps1 -Status              # what is running right now
.\start_fleet.ps1 -Units adx,carry     # just two bots
.\start_fleet.ps1 -Monitor             # plus an hourly monitor.py console
```

```bash
python health.py                # invariants: schema, freshness, single-open (p300_% variants only — blind to every bot_*)
python monitor.py               # freshness + heartbeats + ghost registry
python bots/adx/runner.py --once --verbose    # one tick of one bot and exit
```

Every runner takes the same flags: `--once`, `--interval <seconds>`,
`--verbose`, and the dry-run pair `--db <copy-of-prod.db> --sim-now <iso-utc>`
(both require `--once`, and `--db` points the process at a **copy**, so a dry
run cannot touch the live ledger). Use that pair to replay a bot's decision at
a chosen timestamp without stopping the fleet.

> **History — sim mode, retired 2026-09-13.** A second entry point
> (`studies/simulation/sim.py`) ran the same dispatch logic under a simulated
> clock against a sliced `trader.db` built by
> `studies/simulation/build_sim_trader_db.py`, writing to a separate
> `--dash-db` ledger with all external APIs blocked so a run was reproducible
> offline. It went with the orchestrator it drove, together with
> `backtest_runner.py` and `tests/test_sim_mode.py` — the last of which had
> been copying the 1.5 GB `prod.db` once per test. `strategies/support/
> sim_loop.py` and `clock.set_simulated_now()` remain; the per-bot `--db /
> --sim-now` pair above is what replaced the workflow. The report notebooks
> ([studies/notebooks/full_portfolio_report.ipynb](studies/notebooks/full_portfolio_report.ipynb),
> [studies/notebooks/backtest_report.ipynb](studies/notebooks/backtest_report.ipynb))
> still read a trade ledger the same way — they now read the live one.

# P-300 Aggressive 2.0 — Portfolio Composition

A complete reference for what the bot trades, when each strategy fires, what
leverage it uses, and how the pieces compose. All percentages are **fractions
of total capital** unless stated otherwise.

> Variant ID: `p300_aggressive_v2_v1_0` · Status: paper-only.

---

## 1. Sleeve roster

The bot dispatches 13 sleeves per minute through
[strategies/orchestrator.py](strategies/orchestrator.py). All sleeves
write to the same `trades` table (`execution_mode='paper'`); realized
PnL is the trade-ledger sum (no parallel theoretical-PnL track since
the 2026-05-10 live/sim refactor).

| Sleeve | Pre-lev alloc | Leverage | Asset | Direction | Hold |
|---|---|---|---|---|---|
| [S-003 ADX](strategies/sleeves/adx/signal.py) | 15% | 5× | BTC | LONG/SHORT | days–weeks |
| [S-078 Carry](strategies/sleeves/carry/signal.py) | 8% | 5× | BTC (delta-neutral) | n/a | days |
| [S-096 V4 Thu Bear](strategies/sleeves/thu_bear/signal.py) | 6% (3% BTC + 3% ETH) | 5× | BTC + ETH | SHORT | 24h (Thu) |
| [S-102 PDO-L-RF](strategies/sleeves/pdo/signal.py) | 9% (4.5/asset) | 1× | BTC + ETH | LONG | 24h |
| [S-101 CPR](strategies/sleeves/cpr/signal.py) | 5% (2.5/asset) | 1× | BTC + ETH | LONG | ≤15 days |
| [S-103 FOMC](strategies/sleeves/fomc/signal.py) | 5% | 10× | BTC | LONG | ~10.5h (FOMC days) |
| [JPLUS_R4_BTC](strategies/sleeves/r4/signal.py) | regime-keyed | regime × vol-lev | BTC | LONG | 12h |
| [JPLUS_R4_ETH](strategies/sleeves/r4/signal.py) | regime-keyed | regime × vol-lev | ETH | LONG | 24h |
| [JPLUS_R4_BTC_V2](strategies/sleeves/r4/signal.py) | regime-keyed | regime × vol-lev | BTC | LONG | 10h |
| [JPLUS_R4_ETH_V2](strategies/sleeves/r4/signal.py) | regime-keyed | regime × vol-lev | ETH | LONG | 10h |
| [JPLUS_EMA_BTC](strategies/sleeves/ema/signal.py) | regime-keyed | vol-lev | BTC | LONG/SHORT | continuous |
| [JPLUS_ETH_DAILY](strategies/sleeves/eth_daily/signal.py) | regime-keyed | vol-lev | ETH | LONG | continuous in bull |
| [AI_QUANT](strategies/sleeves/ai_quant/signal.py) *(experimental)* | 2% × conviction | 3× | BTC | LONG/SHORT/FLAT | LLM-discretionary |

The 7 non-J+ rows use static regime-independent allocations from
[strategies/support/allocation.py](strategies/support/allocation.py)
(P2.4a) — same numbers historically lived in `register_p300.py`.
The 6 J+ rows pull regime-keyed weights from the same
WEIGHT_TABLE (matching the legacy `REGIME_WEIGHTS_FULL` from
[strategies/support/jplus_inputs.py](strategies/support/jplus_inputs.py))
with a `CORE_ALLOC_CAP=0.50` runtime scalar applied to the family.

Cross-sleeve coordination at trade-open time:

- **Margin headroom** ([strategies/support/margin_headroom.py](strategies/support/margin_headroom.py))
  — variant gross notional ≤ `gross_notional_target_x × capital`
  (default 2.5×). Sleeves skip with `margin_constrained` on overrun.
- **Conflict resolver** ([strategies/support/conflict_resolver.py](strategies/support/conflict_resolver.py))
  — a sleeve skips with `directional_conflict` when another sleeve
  already has an opposing perp open on the same asset. CARRY's
  delta-neutral SHORT leg is excluded.
- **Signal aggregator** ([strategies/support/signal_aggregator.py](strategies/support/signal_aggregator.py))
  — detection only today; sleeves consume in a later stage.

Sleeve order in `spec.composition` is the implicit dispatch
priority — first-come-first-served on the margin pool and on
conflict resolution. AI_QUANT is last (yields first).

> **Stop-loss semantics.** All `stop_loss_pct` values configured per sleeve
> are interpreted as **price-move** percentages by default — i.e. a 10% stop
> at k=5× triggers when price moves 10%, after which the trade has lost 50%
> of margin. This matches the calibration used in the historical backtests
> below. Set `P300_STOP_SEMANTICS=margin` to interpret the same numbers as
> margin-loss caps (10% margin-loss → 2% price move at k=5×); if you do,
> re-tune each sleeve's `stop_loss_pct` since the pcts were sized for the
> price-move semantic. See [strategies/support/risk_config.py](strategies/support/risk_config.py).

---

## 2. Per-sleeve detail

Tactical sleeves (regime-independent allocation today; §3 covers the
J+ family). Section numbering preserved from the pre-restructure
revision for backwards-compatible cross-references.

### 2.1 S-003 ADX — Trend-flip on BTC

- **Signal**: 14-period ADX crosses 25 from prior compression (<20 in last 20 bars). Direction: LONG when close > EMA(50), SHORT when close < EMA(50). LONG-only trend filter: LONG additionally requires close > EMA(150).
- **Entry**: at the crossover bar. Stops out at -2% spot (10% of size after k=5×).
- **Exit**: opposite ADX flip, OR stop loss, OR trend exhaustion.
- **Edge thesis**: catches medium-term trends in BTC; takes the loss when trend reverses.

### 2.2 S-078 Carry — Delta-neutral funding harvest

- **Signal**: 7-day average BTC perp funding > 0%. Entry opens spot-long + perp-short of equal notional → market-neutral.
- **Income**: collects funding payments every 8h while the perp side is short.
- **Exit**: 3 consecutive negative funding days, or scheduled time-stop.
- **Edge thesis**: structurally positive funding in bullish regimes is paid for free if you can hedge cheaply. P&L is dominated by funding accrual, not price moves.

### 2.3 S-096 V4 Thu Bear — Calendar-driven Thursday short

- **Signal**: Thursdays only. V4 filter: trade only if Thursday is within ±1 day of CPI or NFP, AND not within ±1 day of OPEX. Prior-day regime must be `bear_trend / sell_off / chop` (not `bull_trend`).
- **Entry**: Thursday 00:00 UTC. SHORT BTC + ETH equally.
- **Exit**: Friday 01:00 UTC, or stop-loss at -1% spot (5% margin at k=5×).
- **Edge thesis**: weekly Thursday selling pressure during macro-event-adjacent periods, conditioned on being already in a non-bull regime.
- **Caveat**: V4 event filter was derived post-hoc from V3's Thursday attribution — in-sample selection bias applies.

### 2.4 S-102 PDO-L-RF — Pullback Daily Open Retouch Long

- **Signal**: After a daily gap-down ≥ 2%, wait for the price to retouch the prior daily open (PDO). Regime must not be deeply bearish (`regime_threshold_pct: -10%` recent peak DD).
- **Entry**: at the PDO retouch.
- **Exit**: scheduled time-stop, or stop-loss.
- **Edge thesis**: gap-fills are a known intraday phenomenon in crypto. Mean-reversion long after a down-gap.
- **Caveat**: parameters (gap %, regime threshold) were swept in upstream research without visible walk-forward CV — data-snooping exposure.

### 2.5 S-101 CPR — Contrarian Positioning Reversal

- **Signal**: All four conditions must agree:
  1. 3-day mean funding rate < 20-percentile of trailing window
  2. LSR (long-short ratio) < 20-percentile of trailing window
  3. BTC daily close > EMA(20)
  4. EMA(20) > EMA(50)
- **Setup logic**: persistent negative funding + crowd is short + price still in uptrend → expected short squeeze.
- **Entry**: at the next 1m bar after signal trigger.
- **Exits**: target at +2.93% (BB upper band), stop at -5%, or 15-day time-stop.
- **Edge thesis**: contrarian-position-with-trend setup. Theoretically high-quality but historically thin sample (12 BTC + 9 ETH events from upstream).

### 2.6 S-103 FOMC — Long into Fed announcement, regime-filtered

- **Signal**: Only fires on FOMC dates (8/year, from `scheduled_events`).
- **Entry**: T-10h before announcement (08:00 UTC, or 09:00 UTC for EST meetings).
- **Exit**: T+0.5h after (when Powell starts speaking).
- **Filter rule** (combined regime + sentiment + Polymarket):
  - HARD SKIP if `expected_action == 'cut_25bp'` (historical 20% win rate)
  - HARD SKIP if F&G bucket == `extreme_greed` (40% win rate)
  - HARD TRADE if F&G == `extreme_fear` AND phase ≠ `mid_hold` (8/8 historical wins)
  - SKIP if `phase == 'mid_hold'` (25% win rate)
  - TRADE otherwise (peak_hold / hiking / zirp_hold / cutting in good context)
- **Inputs**:
  - **Phase**: from [data/sources/fed_funds.py](data/sources/fed_funds.py) — NY Fed XML, classified as `zirp_hold / hiking / peak_hold / cutting / mid_hold`.
  - **F&G**: from [data/sources/sentiment.py](data/sources/sentiment.py) — alternative.me daily Fear & Greed.
  - **Expected action**: from [data/sources/polymarket.py](data/sources/polymarket.py) — implied per-meeting cut probability from the "How many Fed rate cuts in 2026?" market.
- **Audit trail**: every FOMC date writes a row to `fomc_observer` in `data/databases/prod.db` with the decision + reason + inputs, even when the decision is SKIP.
- **Edge thesis**: short-window event trade. Drift up into the announcement, partial fade after. Filter weeds out the regimes where this fails.
- **Caveat**: filter was tuned on the same 52-event historical cohort the in-sample backtest is drawn from. Going-forward edge unproven.

### 2.7 AI_QUANT — Discretionary LLM trader (experimental, default-OFF)

- **Status**: Phase-1 experiment. Added 2026-05-08. Default-disabled via
  `AI_QUANT_ENABLED` env var ([`.env.example`](.env.example) ships with
  `false`); when unset, [strategies/sleeves/ai_quant/signal.py:_kill_switch_on()](strategies/sleeves/ai_quant/signal.py)
  short-circuits to `status='disabled'` and no LLM call is made.
- **Allocation**: 2% of capital, **inside** the 50% Tactical cap (since
  2026-05-12; PDO was trimmed from 11% to 9% to make room). Raise to 5%
  only after 60+ days of forward paper PnL net of API cost, which would
  require trimming another tactical sleeve to stay under the 50% cap.
- **Leverage**: 3×. **Asset**: BTC perp. **Direction**: LONG / SHORT / FLAT,
  chosen daily by the model.
- **Signal**: an Anthropic Opus 4.7 tool-use loop runs once per UTC day in a
  10-minute window (00:05–00:15 UTC). Every minute, four cheap gates are
  evaluated by [orchestrator](strategies/orchestrator.py) before any
  LLM call: kill-switch / time-window / per-day-already-fired /
  daily-cost-cap. On the one tick that passes all four, the service builds
  a context bundle (regime, F&G, funding, recent volatility, open
  positions, etc.), renders a 90-bar daily chart, and runs the decision
  loop with server tools enabled.
- **Output schema**: the model returns `direction ∈ {LONG, SHORT, FLAT}`,
  `conviction_0_100`, `time_horizon_days`, and `key_drivers[]`. The
  service applies a **conviction floor**: `conviction < 30` is forced to
  FLAT regardless of the model's stated direction.
- **Sizing**: `allocation_pct = weight_pct × (conviction / 100)`, capped at
  the 2% weight ([ai_quant_service.py:_allocation_pct_for](strategies/sleeves/ai_quant/signal.py)).
  So a conviction-50 LONG sizes to 1% of capital at 3× leverage; a
  conviction-100 LONG sizes to the full 2%.
- **Reconciliation**: each day's decision is reconciled against any open
  AI_QUANT position — open / hold / close / flip. No mid-day scaling in v1.
- **Exit logic**: there is no fixed time-stop. Positions are held until the
  next day's decision flips them or sets FLAT, or until the configured
  `stop_loss_pct=10.0` price-move stop fires.
- **Cost cap**: $5/day default API spend ceiling
  (`AI_QUANT_DAILY_COST_CAP_USD`); when exceeded, the gate returns
  `cost_capped` and no decision runs that day.
- **Audit trail**: every fire writes a row to the AI_QUANT journal
  ([strategies/sleeves/ai_quant/journal.py](strategies/sleeves/ai_quant/journal.py)) with the
  decision payload, tool calls, token usage, cost, and resulting trade
  action — including ERROR rows when context-build / chart-render / API
  fail, so idempotency triggers next tick. The journal also writes a
  human-browsable markdown mirror per row to
  `data/ai_quant_archive/{date}_{variant}_{asset}_{decided}_id{N}.md`
  ([strategies/sleeves/ai_quant/archive.py](strategies/sleeves/ai_quant/archive.py)) for
  decision-quality monitoring; regenerable from the DB via
  [strategies/sleeves/ai_quant/archive_rebuild.py](strategies/sleeves/ai_quant/archive_rebuild.py).
- **Backtest behavior**: `params.deterministic=False` is consumed by
  [backtest_runner.py](backtest_runner.py) to **skip** AI_QUANT on
  historical replay — the LLM is non-deterministic and replay would
  produce different decisions each run. AI_QUANT contributes nothing to
  the §6 in-sample backtest numbers.
- **Edge thesis**: a discretionary trader with broad context (macro,
  sentiment, microstructure, chart) may catch regime shifts that the
  rule-based sleeves are structurally blind to. Whether the model can
  beat its own API cost net of slippage is the open question this
  experiment exists to answer.

---

## 3. Core J+ Family — six regime-keyed sleeves

Six sub-sleeves derived from the upstream J+ engine, dispatched as
top-level entries in `STRATEGY_DISPATCH`. Each runs its own per-tick
handler:

| Sleeve | Allocation | Leverage | Asset | Direction | Holding period |
|---|---|---|---|---|---|
| [JPLUS_R4_BTC](strategies/sleeves/r4/signal.py) | **0–11.1%** (regime-varying, capped) | 2.5× inner × vol_lev → ~5× typ / 7.5× max | BTC | LONG | 12h (Mon 06→18 UTC, weeks 1-2) |
| [JPLUS_R4_ETH](strategies/sleeves/r4/signal.py) | **0–14.8%** (regime-varying, capped) | 2.5× inner × vol_lev → ~5× typ / 7.5× max | ETH | LONG | 24h (Tue 20→Wed 20 UTC, weeks 1-2) |
| [JPLUS_R4_BTC_V2](strategies/sleeves/r4/signal.py) | **0–5.6%** (regime-varying, capped) | 2.5× inner × vol_lev → ~5× typ / 7.5× max | BTC | LONG | 10h (Wed/Fri 04→14 UTC, weeks 1-2) |
| [JPLUS_R4_ETH_V2](strategies/sleeves/r4/signal.py) | **0–7.4%** (regime-varying, capped) | 2.5× inner × vol_lev → ~5× typ / 7.5× max | ETH | LONG | 10h (Wed/Fri 04→14 UTC, weeks 1-2) |
| [JPLUS_EMA_BTC](strategies/sleeves/ema/signal.py) | **11.1–30%** (regime-varying, capped) | vol_lev only: 0.5×–3× (regime-capped) | BTC | LONG / SHORT (weekly EMA flip) | continuous (open-ended; FLIP on weekly cross) |
| [JPLUS_ETH_DAILY](strategies/sleeves/eth_daily/signal.py) | **0–8.7%** (bull regimes only) | vol_lev only: 0.5×–3× (regime-capped) | ETH | LONG | continuous (opens on regime enter bull; closes on regime exit) |

The family sum is bounded by `CORE_ALLOC_CAP=0.50`
([strategies/support/allocation.py](strategies/support/allocation.py)
applies it at lookup time; the legacy `_cap_core_weights` in
[strategies/support/jplus_inputs.py](strategies/support/jplus_inputs.py)
is preserved for parity). Allocation ranges above are the **capped**
values across the four regimes (min = 0% in regimes where the
sub-sleeve is dormant; max = the highest regime's capped weight).
Per-regime breakdown is in §3.2.

**Leverage stacking** (R4 sub-sleeves):
- *Inner* — 2.5× ungated, 1.0× when the vol-percentile gate fires (high-vol regime, see §3.3).
- *Vol-target overlay* — 0.5× floor to a regime cap (1.5× bear / 2.0× uncertain / 2.5× mild_bull / 3.0× strong_bull). See [strategies/support/voltarget.py](strategies/support/voltarget.py).
- *Stacked* — inner × vol_lev. Typical 5×, max 7.5× (ungated + strong_bull regime), min 0.5× (gated + low realised vol).

EMA_BTC and ETH_DAILY have no inner R4 leverage — their effective k = vol_lev only.

The V1 R4_BTC sleeve was **Mon+Wed** before 2026-05-08; the calendar-window
study in [studies/notebooks/r4_study/](studies/notebooks/r4_study/) found that Wed responds better
to a 04→14 UTC window than the V1's 06→18, so Wednesday was moved to a new
sleeve (R4_BTC_V2) along with Friday — historically the strongest single
weekday cell on BTC (NFP-anticipation effect). The same Wed+Fri 04→14 cell
extracts comparable signal on ETH (R4_ETH_V2) per the cross-asset study.

Each handler:
- pulls today's regime mode, vol-target leverage, R4 gate, EMA position,
  and sub-sleeve weights from [`jplus_inputs.today_inputs()`](strategies/support/jplus_inputs.py)
  — which derives them strictly from data through yesterday's close;
- prices the entry from [`strategies.support.price_feed.get_current_price`](strategies/support/price_feed.py)
  (latest closed 1m bar — ~30s lag);
- writes the trade via [`strategies.trades.open_paper_trade`](strategies/trades.py),
  with `scheduled_exit_dt` set for the discrete-window sleeves and `None` for
  continuous positions;
- is idempotent per UTC day via the `trades` table and the
  `UNIQUE(trade_id, event_date, event_type)` constraint on the adjustment
  ledger, so re-ticks within the same day no-op.

For continuous sleeves (EMA_BTC, ETH_DAILY), the handler also emits SCALE
and LEVERAGE_ADJUST events on the first tick of each UTC day to bring the
position size to today's `weight × lev × capital` notional. EMA_BTC emits
a FLIP event when the weekly EMA cross changes the sign of `ema_p`.

**One writer per sleeve:** the live handlers above emit trades and
adjustments to the [trades](strategies/trades.py) and `trade_adjustments`
tables at the actual signal moment. That ledger is the single source of
truth for realized PnL — Core sub-sleeves and tactical sleeves both
write there uniformly. `SELECT * FROM trades WHERE status='open'`
returns the bot's complete current exposure.

Two earlier paths were removed in the 2026-05-10 live/sim refactor:
- The simulator-driven daily-return accrual (which wrote
  `variant_daily_returns` once per UTC day from the analytic formula)
  was a research artifact running parallel to the realized trade
  ledger. With the trade ledger as the canonical PnL, the parallel
  theoretical track only confused the operator about which number was
  real.
- The retroactive trade-emitter (startup gap-filler) backfilled trades
  for dates the bot had been offline. That has no analogue in real
  trading and silently masked sleeve-disablement bugs (the V2 sleeves
  silently fired only via catchup for two days because they were
  missing from the variant composition).

Today: the bot opens trades when each handler's signal fires; if the
bot is offline during a window, that trade is missed permanently — same
semantics as tactical sleeves. The analytic
[`studies.jplus_analytic.simulate()`](strategies/support/jplus_inputs.py) function remains
available as a **research-only** tool for offline analysis (regenerating
§6 numbers, parameter sweeps, walk-forward studies); no runtime path
calls it.

Cost migration: [strategies/sleeves/r4/  (sleeve folder)](strategies/sleeves/r4/  (sleeve folder)) emits gross window returns
(`COST_BP_RT = 0.0`); the 10bp R4 round-trip is charged at trade close.
[strategies/sleeves/ema/  (sleeve folder)](strategies/sleeves/ema/  (sleeve folder))'s `_COMMISSION` is `0.0`
explicitly (was a phantom constant pre-migration). Since 2026-05-13
EMA_BTC and ETH_DAILY live closes also charge the default cost (10bp
fee + 5bp slippage) and apply funding accrual — pre-2026-05-13 both
ran zero-fee + zero-funding, which was structurally wrong for the perp
positions they actually trade (multi-week ETH-LONG perp funding alone
~4.5%/yr was previously invisible).

```
                 ┌──────────────────────────────────────┐
                 │   regime classifier (T-1 inputs)     │
                 │   strong_bull / mild_bull /          │
                 │   uncertain / bear                   │
                 └──────────┬───────────────────────────┘
                            │
        ┌───────────────────┴───────────────────┐
        │  per-regime weighting (Layer 2)       │
        │  selects how much each sub-sleeve     │
        │  contributes today                    │
        └───┬─────────┬─────────┬──────────────┘
            │         │         │       │
       ┌────▼──┐  ┌──▼───┐  ┌──▼───┐  ┌▼─────────┐
       │ EMA   │  │ ETH  │  │ R4   │  │ R4       │
       │ (BTC) │  │ daily│  │ BTC  │  │ ETH      │
       │       │  │      │  │ ←2.5×│  │ ←2.5×    │ Layer 1
       │       │  │      │  │ when │  │ when     │ (R4 inner)
       │       │  │      │  │ no   │  │ no gate  │
       │       │  │      │  │ gate │  │          │
       └───┬───┘  └──┬───┘  └──┬───┘  └─┬────────┘
           │         │         │        │
           └────────►◄────────►◄────────┘
                  combined 1× daily return rl
                            │
                  ┌─────────▼─────────┐
                  │ vol-target (Layer 3)
                  │ 30d realized vol → daily lev
                  │ regime-capped 1.5×–3.0×
                  │ floored 0.5×
                  └─────────┬─────────┘
                            │
                  final daily return = rl × lev
```

### 3.1 Sub-sleeves

Four signal sources contribute to the daily 1× return. Each sub-sleeve's
*final* contribution to the day = (its return) × (regime weight, see §3.2)
× (any inner leverage, see §3.3) × (vol-target outer leverage, see §3.5).

| Sub-sleeve | Module | What it returns | Inner leverage |
|---|---|---|---|
| EMA(BTC) | [strategies/sleeves/ema/  (sleeve folder)](strategies/sleeves/ema/  (sleeve folder)) | position direction × BTC daily return | 1× |
| ETH daily | [data/loaders.py](data/loaders.py) | ETH's daily return | 1× |
| R4 BTC | [strategies/sleeves/r4/  (sleeve folder)](strategies/sleeves/r4/  (sleeve folder)) | Mon 06→18 UTC window return | **2.5× / 1×** (gated) |
| R4 ETH | [strategies/sleeves/r4/  (sleeve folder)](strategies/sleeves/r4/  (sleeve folder)) | 24h Tue 20→Wed 20 UTC window return | **2.5× / 1×** (gated) |
| R4 BTC V2 | [strategies/sleeves/r4/  (sleeve folder)](strategies/sleeves/r4/  (sleeve folder)) | Wed/Fri 04→14 UTC window return | **2.5× / 1×** (gated) |
| R4 ETH V2 | [strategies/sleeves/r4/  (sleeve folder)](strategies/sleeves/r4/  (sleeve folder)) | Wed/Fri 04→14 UTC window return | **2.5× / 1×** (gated) |

#### 3.1.1 EMA(BTC) — Weekly crossover position-flip

- **Signal**: EMA(5) vs EMA(21) on **weekly** BTC closes (BTC hourly aggregated to 168h buckets). LONG when EMA5 > EMA21, SHORT when EMA5 < EMA21.
- **Position state** (called `ema_p` in the simulator): `+1` while LONG, `-1` while SHORT, `0` during warmup.
- **Entry**: at the **next weekly candle's open** after a cross is detected. (No same-bar entry — strict T+1 to avoid look-ahead.)
- **Exit**: at the **next weekly candle's open** after the reverse cross. Effectively the sleeve is always in one of long / short / flat — there's no idle exit-to-cash state once the weekly EMAs have crossed.
- **Daily contribution** to Core: `ema_p × today's BTC daily return × regime weight`. So an EMA-LONG day with BTC up +2% contributes +2% × regime_weight; an EMA-SHORT day with BTC up +2% contributes -2% × regime_weight.
- **Cost model**: 0.1% round-trip commission charged at each weekly cross (in the simulator's pre-aggregation logic).
- **Edge thesis**: medium-term trend follower on BTC. Captures multi-week directional moves; pays the spread/fee on whipsaws.
- **Active in**: every regime (regime weights 0.30 in `mild_bull / uncertain / bear`, 0.50 in `strong_bull`).

#### 3.1.2 ETH daily — Passive ETH long, regime-gated

- **Signal**: none — this isn't a discretionary signal sleeve. It's a "long ETH at regime-weighted size" position.
- **Position**: long ETH spot. Held continuously while the regime is `strong_bull` or `mild_bull`; **idle** in `uncertain` and `bear` (regime weight = 0).
- **Entry**: the day the regime classifier flips into `strong_bull` or `mild_bull`. No T+1 delay (ETH daily return is already a closed-day measure).
- **Exit**: the day the regime classifier flips out of bull. (In practice — daily mark-to-market: each day's `er` is added with the appropriate weight.)
- **Daily contribution**: `ETH daily return × regime weight` (0.20 strong_bull, 0.10 mild_bull, 0 otherwise).
- **Cost model**: assumed zero — modeled as exposure in a notional sense, not discrete trades.
- **Edge thesis**: pure long-ETH-beta during bull regimes. ETH outperforms BTC on the way up; this gives the portfolio that exposure when conditions are constructive.
- **Active in**: `strong_bull` and `mild_bull` only.

#### 3.1.3 S-099 R4 BTC — Mon intraday long, weeks 1–2 only

- **Signal**: pure calendar trigger. Fires on Mondays whose date is **≤ 14 of the month** (first half only). Mon-only since 2026-05-08; was Mon+Wed before the V1/V2 split — Wednesdays moved to R4 BTC V2 at the era-stable 04→14 window. See [studies/notebooks/r4_study/findings.md](studies/notebooks/r4_study/findings.md).
- **Entry**: `06:00 UTC` open price of that day.
- **Exit**: `18:00 UTC` open price (i.e., end of the 12-hour window), same day.
- **Direction**: LONG always.
- **Return per fire**: `(price_at_18:00 − price_at_06:00) / price_at_06:00 − 10bp RT cost`.
- **Inner leverage**: **2.5×** normally, **1.0×** when the vol-percentile gate fires (§3.3).
- **Daily contribution**: `R4_BTC_return × inner_lev × regime_weight` (0.30 in `uncertain`, 0.20 in `mild_bull`, 0.15 in `strong_bull`, 0 in `bear`).
- **Cost model**: 10bp round-trip taker fee, baked into the windowed return.
- **Edge thesis**: post-Binance-perp / post-ETF emergent flow effect — Mon was −0.76%/trade pre-Binance and +0.83%/trade post-ETF. The strategy bets on the post-2024 regime continuing; per-sleeve health metrics in [strategies/support/strategy_health.py](strategies/support/strategy_health.py) trigger disable on expectancy decay. See [studies/notebooks/r4_study/findings.md](studies/notebooks/r4_study/findings.md).
- **Active in**: every non-`bear` regime.

#### 3.1.4 S-099 R4 ETH — Tue → Wed 24h long, weeks 1–2 only

- **Signal**: pure calendar trigger. Fires on **Tuesdays whose next-day (Wed) is ≤ 14 of the month**.
- **Entry**: `Tue 20:00 UTC` open price.
- **Exit**: `Wed 20:00 UTC` open price (24h hold, crossing the next calendar day).
- **Direction**: LONG always.
- **Return per fire**: `(price_at_Wed_20:00 − price_at_Tue_20:00) / price_at_Tue_20:00 − 10bp RT cost`.
- **Inner leverage**: **2.5×** normally, **1.0×** when the vol-percentile gate fires (§3.3).
- **Daily contribution**: keyed by Wed date — `R4_ETH_return × inner_lev × regime_weight` (0.40 in `uncertain`, 0.30 in `mild_bull`, 0.15 in `strong_bull`, 0 in `bear`).
- **Cost model**: 10bp round-trip taker fee.
- **Edge thesis**: an early-month Tue→Wed ETH window that's been the highest-alpha sub-sleeve in the J+ history. Empirically: 48 fires over 973 days, mean **+0.088%/day** (highest of all sub-sleeves), +116.5% compounded if run alone.
- **Active in**: every non-`bear` regime; weighted heaviest in `uncertain` (40%).
- **Caveat**: 48 events in 2.6 years is a thin sample. The +116% compounded standalone return is striking and demands skepticism — could be genuine alpha, could be a quirk of how ETH happened to behave on those exact dates in 2024–2025. Worth a longer-horizon sanity check.

#### 3.1.5 R4 BTC V2 — Wed + Fri intraday long, weeks 1–2 (added 2026-05-08)

- **Signal**: calendar trigger. Fires on Wednesdays and Fridays whose date is **≤ 14 of the month**.
- **Entry**: `04:00 UTC` open price.
- **Exit**: `14:00 UTC` open price (10-hour hold), same day.
- **Direction**: LONG always.
- **Return per fire**: `(price_at_14:00 − price_at_04:00) / price_at_04:00 − 10bp RT cost`.
- **Inner leverage**: **2.5×** normally, **1.0×** when the vol-percentile gate fires (§3.3).
- **Daily contribution**: `R4_BTC_V2_return × inner_lev × regime_weight` (0.15 in `uncertain`, 0.10 in `mild_bull`, 0.075 in `strong_bull`, 0 in `bear`) — half the V1 weight.
- **Edge thesis**: era-stable BTC alpha cell (positive in pre-Binance-perp, Binance-perp, and post-ETF eras). Likely captures NFP-anticipation (Friday wk1 cell is the strongest single-day cell on BTC) plus early-month Wed flow. Full-sample t=+4.6 across 402 fires per the [r4_study](studies/notebooks/r4_study/) grid search.
- **Active in**: every non-`bear` regime.

#### 3.1.6 R4 ETH V2 — Wed + Fri intraday long, weeks 1–2 (added 2026-05-08)

- Same calendar and window as R4 BTC V2 (Wed+Fri wk1-2 04→14 UTC), applied to ETH.
- **Daily contribution**: `R4_ETH_V2_return × inner_lev × regime_weight` (0.20 in `uncertain`, 0.15 in `mild_bull`, 0.075 in `strong_bull`, 0 in `bear`).
- **Edge thesis**: cross-asset bonus from the BTC study — the same Wed+Fri 04→14 window extracts comparable signal on ETH (+0.62% pre-ETH-ETF, +0.48% post-ETH-ETF per fire).
- **Active in**: every non-`bear` regime.

### S-095 3.2 Per-regime allocation weights ([strategies/support/jplus_inputs.py](strategies/support/jplus_inputs.py))

**Raw weights** (as stored in `REGIME_WEIGHTS_FULL`):

| Mode | EMA(BTC) | ETH daily | R4 ETH | R4 BTC | R4 BTC V2 | R4 ETH V2 | Raw sum | When this fires |
|---|---|---|---|---|---|---|---|---|
| **strong_bull** | 0.50 | 0.20 | 0.15 | 0.15 | 0.075 | 0.075 | 1.15 | full risk-on |
| **mild_bull** | 0.30 | 0.10 | 0.30 | 0.20 | 0.10  | 0.15  | 1.15 | partial risk-on, R4 emphasised |
| **uncertain** | 0.30 | 0.00 | 0.40 | 0.30 | 0.15  | 0.20  | 1.35 | calendar-driven only (R4 carries) |
| **bear** | 0.30 | 0.00 | 0.00 | 0.00 | 0.00  | 0.00  | 0.30 | EMA only, R4 idle |

> **CORE_ALLOC_CAP — applied at every read site since 2026-05-12.** Raw rows
> above are rescaled by `_cap_core_weights()` ([strategies/support/jplus_inputs.py](strategies/support/jplus_inputs.py))
> so the per-regime sum never exceeds **0.50** (the Core half of the
> 50/50 capital split). When raw sum ≤ 0.50 (bear regime), weights pass
> through unchanged. When raw sum > 0.50, every entry is multiplied by
> `0.50 / raw_sum` — relative weighting between sub-sleeves is preserved,
> but no single sub-sleeve's pre-leverage allocation can dominate the
> account. The capped weights `today_inputs()` actually returns are below.

**Capped weights** (what `today_inputs()` returns and what live trades size against):

| Mode | EMA(BTC) | ETH daily | R4 ETH | R4 BTC | R4 BTC V2 | R4 ETH V2 | Sum |
|---|---|---|---|---|---|---|---|
| **strong_bull** | 0.217 | 0.087 | 0.065 | 0.065 | 0.033 | 0.033 | **0.500** |
| **mild_bull** | 0.130 | 0.043 | 0.130 | 0.087 | 0.043 | 0.065 | **0.500** |
| **uncertain** | 0.111 | 0.000 | 0.148 | 0.111 | 0.056 | 0.074 | **0.500** |
| **bear** | 0.300 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | **0.300** |

**Total > 1.0 in three regimes**: as of the V2 sleeves being added (2026-05-08), Core total exposure can exceed 1.0 when multiple R4 sleeves fire concurrently. Peak concurrent exposure is on Wednesdays (R4 ETH V1 still open from Tue + R4 BTC V2 + R4 ETH V2 all firing 04:00-14:00 UTC) — about 75% of capital in `uncertain` regime. Vol-target leverage scales this further. The V2 sleeves are at half the V1 weights specifically to keep peak Wed concurrent exposure comparable to the pre-2026-05-08 baseline.

The bot spent 62% of the 2023-09 → 2026-04 window in `uncertain` and 25% in
`bear` — so for most days, R4 BTC and R4 ETH are doing the real work when
they fire, and EMA carries the rest.

### 3.3 Layer 1 — R4 inner multiplier ([strategies/support/jplus_inputs.py:26-27](strategies/support/jplus_inputs.py))

R4 BTC, R4 ETH, R4 BTC V2, R4 ETH V2 (and ONLY those four — not EMA, not ETH daily) get an inner
amplification on top of their raw windowed return:

| State | Multiplier | Why |
|---|---|---|
| Normal day | **2.5×** | R4 is a sized sleeve within the J+ portfolio |
| **Vol-percentile gate fired** | **1.0×** | de-lever in high-vol regimes |

The gate ([strategies/support/gate.py](strategies/support/gate.py)) fires when the trailing 30-day BTC realized vol is in the **top 25%** of the 365-day distribution — strictly using T-1 data, no look-ahead. Fired on **29.7% of days** in the v6 backtest window.

### 3.4 Layer 2 — regime weights

See table 3.2 above.

### 3.5 Layer 3 — vol-target outer leverage ([strategies/support/voltarget.py](strategies/support/voltarget.py))

After all sub-sleeves are weighted and summed (`rl`), the daily strategy return is multiplied by a vol-target leverage `lev`:

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

The simulator targets **50% annualised volatility** for the strategy. When realised vol is HIGHER than that, leverage drops. When realised vol is LOWER, leverage rises (capped by regime). Floored at 0.5× — never zero.

### 3.6 Effective leverage for a single R4 trade

Stacking the 3 layers on a representative day: R4 ETH firing on a Tuesday in `uncertain` regime, gate not fired, vol-target lev 2.0. Note the regime weight is the **capped** value (0.148 = raw 0.40 × 0.50/1.35), not the raw 0.40:

```
final_contrib_to_daily_return =
    raw_R4_ETH_return × R4_inner(2.5) × capped_weight(0.148) × vol_target(2.0)
  = raw × 0.74
```

So R4 ETH's effective leverage on that day is **0.74× of raw spot move**, applied to **~15% of capital** (the capped R4_ETH allocation in `uncertain`). Pre-cap (before 2026-05-12) this was 2.0× / 40%, which produced $20k LONG positions on $10k variant capital — the over-allocation that motivated the cap.

---

## 4. Cross-sleeve coordinators

Sleeves only own their own decision logic. Everything that has to look
across sleeves — regime, sizing, leverage, gating, margin, directional
conflict, signal pooling, trade ordering — lives in
[`strategies/support/`](strategies/support/) and is injected into each
sleeve's dispatch as `sleeve_cfg["_effective_*"]` fields by
[`strategies/orchestrator.py`](strategies/orchestrator.py). This
section walks the layers in the order they apply each tick.

### 4.1 Regime classifiers

Two parallel classifiers run on the same BTC daily series; each
sleeve consumes whichever fits its trade thesis. Both are strictly
T-1 (look-ahead-safe) and read from `cd_spot_binance` (BTC spot 1h
aggregated to daily) so they agree with TradingView's `BTCUSDT 1D`
feed within rounding.

**J+ classifier** ([strategies/support/regime_jplus.py](strategies/support/regime_jplus.py))
— 4 modes consumed by the allocation table and by every J+
sub-sleeve's weight resolution:

| Mode | Trigger |
|---|---|
| **strong_bull** | close > EMA(50) AND close > EMA(20) AND m30 > 0 AND m7 > 0 |
| **mild_bull** | close > EMA(50) AND (m30 > 0 OR close > EMA(20)) |
| **bear** | close < EMA(50) AND m30 < 0 |
| **uncertain** | otherwise; or peak-DD > 5% while bullish; or LS circuit-breaker active |

Two override rules layered on top:
- **LS circuit breaker** — 7-day LSR delta < −15 (long crowd unwinding)
  forces `uncertain` for the next 7 calendar days.
- **Peak-DD override** — close > 5% off trailing peak demotes any
  bullish label to `uncertain`.

**Tactical classifier** ([strategies/support/regime_tactical.py](strategies/support/regime_tactical.py))
— 4 modes consumed by THU_BEAR's V3 prev-day filter and PDO's
`regime_threshold_pct` skip:

| Mode | Trigger |
|---|---|
| **bull_trend** | 50d SMA 10-day slope > +0.5% of price, RV not extreme |
| **bear_trend** | 50d SMA 10-day slope < −0.5% of price, RV not extreme |
| **chop** | \|slope\| ≤ 0.5% (dead-band) |
| **sell_off** | RV percentile ≥ 75th AND close < 50d MA AND slope < 0 |

Why two vocabularies — the J+ family was ported from upstream P-100
research with its own regime taxonomy; the tactical sleeves came from
the trader-repo gate that uses RV-percentile + slope. Unifying them
is on the backlog as a follow-on to P2.4a but isn't load-bearing.

### 4.2 Allocation table ([strategies/support/allocation.py](strategies/support/allocation.py))

Single source of truth for `weight[sleeve][regime] → fraction-of-capital`.
The J+ regime mode is computed once per tick by
[`allocation.current_regime()`](strategies/support/allocation.py)
and injected as `_effective_weight_pct` into every sleeve dispatch.
Tactical sleeves were regime-independent until P2.4a shipped; their
rows hold the same constant across all four regimes (e.g. ADX = 15%
everywhere). J+ sub-sleeve rows mirror
[`jplus_inputs.REGIME_WEIGHTS_FULL`](strategies/support/jplus_inputs.py)
after the CORE_ALLOC_CAP scaling described in §3.4.

`CORE_ALLOC_CAP = 0.50` (enforced 2026-05-13, commit 43b9c45) caps
total Core gross at 50% of variant capital. Raw J+ weights sum to
1.10–1.35 in non-bear regimes; the cap scales every row by
`0.50 / sum(row)`. Pre-cap, R4_ETH sized 0.40 × 5x stacked = 200%
notional on $10k — the cap brings sizing in line with the documented
Core/Tactical 50/50 split.

### 4.3 Gating framework ([strategies/support/gating.py](strategies/support/gating.py))

A gate decides whether a sleeve fires at all and, optionally, scales
its leverage. Each gate registers a `(strategy_id, regime, now_utc) →
GateDecision(fire, leverage_mult, reason)` callable in
`GATE_REGISTRY`; the orchestrator looks it up per sleeve per tick and
injects the result as `_effective_gate`. Three gates registered:

| Gate | Type | What it does |
|---|---|---|
| R4 vol-gate | modulator | BTC 30d realized-vol > 75th-percentile (365d window) → R4 inner leverage 1.0× instead of 2.5× |
| THU_BEAR V4 | binary | CPI/NFP-adjacent Thursday + ex-OPEX → fire; otherwise block |
| FOMC composite | binary | Phase × F&G × Polymarket-cut-prob filter via [`fomc_observer`](strategies/sleeves/fomc/signal.py) table |

Walk-forward CV protocol for adding/rebuilding a gate:
[GATE_VALIDATION.md](GATE_VALIDATION.md). The R4 vol-gate is
calibrated on BTC vol percentile (not the R4 trade series), so it
carries less in-sample selection-bias risk than V4 / FOMC composite,
both of which were derived post-hoc from the same data they were
backtested on.

### 4.4 Portfolio vol-target scalar ([strategies/support/portfolio_vol.py](strategies/support/portfolio_vol.py))

Per-tick scalar that re-targets the variant's gross exposure to a
documented annualized-vol budget (default 30%). Computed from realized
NAV over a 30-day rolling window of the trades-ledger daily returns:
`scalar = target_vol_annual / realized_vol_annual`, clamped to
`[LEV_FLOOR=0.5, LEV_CAP=3.0]`. Falls back to None (no scaling) when
the variant has < 10 observations of NAV history.

The scalar is opt-in per variant via
`spec.allocator_notes.use_portfolio_vol`. When True, the orchestrator
multiplies every tactical sleeve's `_effective_leverage` by the
scalar at injection time; J+ sleeves read `_effective_vol_scalar`
directly as their inner vol-target leverage (not multiplied through
`_effective_leverage`, to avoid double-counting).

### 4.5 Margin headroom ([strategies/support/margin_headroom.py](strategies/support/margin_headroom.py))

Tracks the variant's gross perp-notional vs cap and tells sleeves how
much room they have. Cap defaults to `2.5 × capital_usdt` via
`spec.allocator_notes.gross_notional_target_x`. Sums `size_usdt`
across open paper trades (already-leveraged notional from
`trades.open_paper_trade`) — no double-leveraging.

Two policies, both available:
- **`can_open(variant, candidate_notional)` — skip policy.** Returns
  `(False, reason)` if `current_used + candidate > cap`. Tactical
  sleeves use this on fresh opens.
- **`clamp_to_headroom(variant, candidate_notional,
  min_reduce_fraction=0.5)` — reduce policy.** Returns
  `(clamped, "full"|"reduced"|"too_small"|"no_headroom", reason)`.
  Above the floor (≥50% of intended) the sleeve opens at the
  clamped size; below the floor it skips. AI_QUANT and the
  multi-asset sleeves (PDO, CPR, THU_BEAR) use this when fresh-opening.

Migrated sleeves (two-phase dispatch, §4.7) **don't call either of these directly**;
the reconcile pass enforces margin uniformly across all candidates
per tick.

### 4.6 Conflict resolver ([strategies/support/conflict_resolver.py](strategies/support/conflict_resolver.py))

Catches opposing-direction perp opens on the same asset within a
variant — e.g. ADX wants LONG BTC while THU_BEAR wants SHORT BTC on
the same Thursday. Two surfaces:
- `detect_opposing_open(variant_id, asset, direction)` — returns the
  earliest open trade with opposite direction, or None. Used by the
  reconcile pass to seed its conflict state with positions opened
  by legacy (non-two-phase) sleeves before reconcile ran.
- `current_directional_opens(variant_id) → {asset: direction}` —
  one-shot snapshot for the reconcile seed and operator dashboards.

CARRY's perp SHORT is excluded by design — it's delta-neutral
collateral against the spot leg, not a directional bet. The exclusion
list is the same one used by [signal_aggregator.py](strategies/support/signal_aggregator.py)
and by [dispatch.py](strategies/support/dispatch.py)'s reconcile loop.

### 4.7 Signal aggregator + reconcile pass

**Detect-only** ([strategies/support/signal_aggregator.py](strategies/support/signal_aggregator.py))
— the read-side dual of conflict-resolver. Surfaces same-asset,
same-direction stacks (e.g. ADX LONG BTC + AI_QUANT LONG BTC) for
audit / operator dashboards. Doesn't pool; that happens in reconcile.

**Active reconcile** ([strategies/support/dispatch.py](strategies/support/dispatch.py))
— the orchestrator collects `Intent` objects from every two-phase-
migrated sleeve, then runs `reconcile_intents()` once per tick:

1. **Conviction-weighted signal pooling** (`_pool_concordant_allocations`).
   Same-(asset, direction) intents have their allocations redistributed
   so the *total* equals the conviction-weighted average alloc rather
   than the sum: `new_alloc_i = (c_i / Σc) × Σ(c × a) / Σc`. Two
   sleeves agreeing LONG BTC don't produce 2× exposure; they produce
   the conviction-weighted-avg sleeve's full size, split by share.
2. **Sort** by `(priority asc, −conviction desc)`. Sleeves declare
   `priority` in composition (default 100); ties break on conviction.
3. **Per-intent loop**, in priority order:
   - **Directional conflict check** — reject if a higher-priority
     intent already approved the opposite direction on this asset.
     Pre-seeded with `existing_directional_opens` from the DB so
     legacy sleeves' positions count too.
   - **Margin headroom** — full size if fits; clamp + `approved_reduced`
     if it fits at ≥50% of intended; reject otherwise.
4. **Returns** parallel `ReconcileResult` list. The orchestrator
   calls each sleeve's `execute_for_variant(intent)` for approved /
   approved_reduced / approved_pooled results; logs the rest.

CARRY's perp SHORT is excluded from both conflict and pool checks
(delta-neutral collateral). FLAT intents (close-existing) always
pass through.

### 4.8 Two-phase dispatch contract ([strategies/support/dispatch.py](strategies/support/dispatch.py))

Each migrated sleeve exposes two callables:

- `try_decide_for_variant(variant, sleeve_cfg) → (list[Intent], status_dict)`
   reads inputs, evaluates signals, runs any side-effect bookkeeping
   (SL sweeps, scheduled closes, daily rebalances, FLIPs), and returns
   the entry intents it would open if approved. **No fresh-open
   side-effects** at this phase.
- `execute_for_variant(variant, sleeve_cfg, intent) → status_dict`
  opens the trade described by an `Intent` returned from reconcile.

All 13 dispatched sleeves are migrated as of 2026-05-16. The
`Intent` dataclass is frozen — `asset`, `direction`, `allocation_pct`,
`leverage`, `conviction (0-100)`, `priority`, `reason` (free-form
dict persisted to `trades.notes`), `scheduled_exit_dt`. The
orchestrator's reconcile pass operates on these uniformly across
sleeves; sleeves themselves no longer need to call conflict-resolver
or margin-headroom inline (the reconcile owns both).

### 4.9 External data feeds ([data/sources/](data/sources/))

Cached caches of upstream public APIs the bot polls daily. Refreshes
are throttled to once per UTC day by
[`binance.py:_refresh_daily_external`](data/sources/binance.py) and
are sim-mode-aware (no-op when `clock.is_simulated()`).

| Feed | Cache | Consumer |
|---|---|---|
| Crypto Fear & Greed | `prod.db:fear_greed_index` table (migrated from JSON 2026-05-16) | FOMC composite gate, AI_QUANT context |
| Fed Funds target rate | [`data/archive/fed_funds_target_upper.json`](data/archive/) (parsed from `nyfed_rates.xml`) | FOMC phase classifier |
| Polymarket cut-probability | [`data/archive/polymarket_fed_2026.json`](data/archive/) | FOMC composite gate |
| Binance klines / funding | `prod.db` (btc_1m, eth_1m, cd_funding_rate, cd_spot_binance, ca_long_short_ratio) | every sleeve + regime classifier |
| News headlines | `prod.db:news_headlines` | AI_QUANT context only |
| CoinDesk derivatives | `prod.db` (cd_open_interest, cd_liquidations, cd_dvol) | AI_QUANT context only |

Two CSVs / static caches survive as files rather than DB tables:
[`known_unfillable.json`](data/known_unfillable.json) is a
hand-curated gap-tracking config read by `health.py`; the
[`pdo_tv_validate_trades.csv`](data/archive/pdo_tv_validate_trades.csv)
is a research artifact from
[`studies/notebooks/pdo_tv_validate_dump.ipynb`](studies/notebooks/pdo_tv_validate_dump.ipynb).

---

## 5. Risk profile

### 5.1 Empirical concurrent notional (v6 backtest 2023-09 → 2026-04)

| Metric | Value |
|---|---|
| Mean | 81% of capital |
| P50 | 80% |
| P95 | 118% |
| P99 | 148% |
| Max | 153% |

The portfolio cross-margins. Half the time the bot has more than 100% notional
in positions because multiple sleeves overlap.

### 5.2 Per-sleeve time-occupancy

| Sleeve | Time in market | Why |
|---|---|---|
| CPR | 101% (multi-asset overlap) | always-on contrarian |
| CARRY | 91% | always holding while funding regime is positive |
| ADX | 54% | trend follower |
| THU_BEAR | 7% | Thursdays only |
| PDO-L-RF | 1% | rare gap-retouch setup |
| **FOMC** | **0.5%** | 8 days/year × ~10.5h |

### 5.3 Pairwise overlap (% of window where both have a position)

```
                 ADX     CARRY     CPR     FOMC    PDO     THU_BEAR
ADX           54.3%    49.4%    53.7%    0.24%    0.71%    3.75%
CARRY                  91.0%    91.7%    0.47%    1.16%    5.92%
CPR                              112.7%   0.52%    1.16%    6.51%
FOMC                                     0.52%    0.02%    0.00%
PDO                                              1.24%    0.15%
THU_BEAR                                                  13.4%
```

FOMC is **time-disjoint** from THU_BEAR (FOMC is always Tue/Wed, THU_BEAR is Thursday)
and effectively-disjoint from everything else (< 0.5% pairwise). Adding FOMC at
10× leverage raises mean concurrent notional by < 0.3% of capital.

> **Staleness note.** This matrix is the 2.66y in-sample baseline that
> feeds §6 and predates two compositional changes: the V2 R4 sleeves
> added 2026-05-08 (`JPLUS_R4_BTC_V2`, `JPLUS_R4_ETH_V2`) and AI_QUANT
> moved inside the 50% tactical cap on 2026-05-12. Re-running the
> overlap calculation against the current composition would shift the
> Core column (V2 sleeves correlate with V1 R4 by construction) and
> add an AI_QUANT row. Leaving the table as-is to keep §6's pairing
> consistent; treat it as the v6 baseline, not the current state.

---

## 6. Portfolio-level performance summary (v6 simulation, all-spot signals)

In-sample backtest, 973 days / 2.66 years (2023-09-01 → 2026-04-30).
This is the canonical baseline as of 2026-05-01 — all signal computations
read spot data (`cd_spot_binance`, `btc_1m`), aligned with TradingView's
default BTCUSDT 1D feed. Earlier v1–v5 numbers used perp data and are
superseded; do not compare side-by-side.

> **The numbers below cannot be regenerated cleanly from current code**
> ([AUDIT_2026_05_13](AUDIT_2026_05_13.md)). They predate every fix
> from the 2026-05-13 audit cluster, several of which materially affect
> the backtest output even against the same trade ledger:
> - **Jensen's-gap (compound-equity) defect** (commit `b2449f8`) —
>   pre-fix totals were inflated by ~nσ²/2; expect total return ~3–5pp
>   lower over the 2.66y run, CAGR slightly lower, MDD slightly *larger*
>   (compounding flatters drawdowns on positive drift).
> - **Slippage model** (`4261c48`) — every directional close now charges
>   +5bp/RT (10bp for FOMC); expect per-trade returns dragged 5–10bp.
> - **Core 50% cap** (`43b9c45`) — R4_ETH at 14.8% capped vs 40% raw etc.
>   Pre-cap Core was effectively running at ~1.35× the documented
>   exposure in `uncertain` regime; that gross was reflected in the §6
>   table and will shrink on re-run.
> - **EMA_BTC / ETH_DAILY funding + fees** (`5bf734f`) — both sleeves
>   now charge funding on multi-week perp holds (~4.5%/yr) plus fee +
>   slippage; previously zero on all three.
> - **Liquidation simulator in live + sim** (`477933f`) and **margin
>   haircut 0.95→0.50** (`b9cad06`) — high-leverage paper trades that
>   would have been wiped in reality now show up as forced closes.
>
> Treat the +485.7% / +121.1% / +294.8% / -15.7% figures below as
> historically-informative but **not exact, not currently reproducible,
> and overstated in the same direction as every fix above**. They will
> be regenerated in the next backtest refresh; until then, the *live*
> paper trade-ledger metrics (which now run on all the corrected
> models) are the ground truth for forward operation.

> The Core columns below are the analytic output of
> [`studies.jplus_analytic.simulate()`](strategies/support/jplus_inputs.py), which has been
> retained as a research-only tool after the 2026-05-10 live/sim
> refactor — no runtime path calls it. Tactical numbers come from the
> backtest_runner's realized trade ledger. Live operation (real-money or
> paper) tracks PnL purely from the trade ledger via
> [strategies/support/strategy_health.py:trades_daily_returns](strategies/support/strategy_health.py),
> so live numbers may diverge from these analytic values by the
> idealized-fill / discretization gap.

| Component | Final equity | Total return | CAGR | Max DD |
|---|---|---|---|---|
| Core J+ alone (100%-basis) | $58,573 | +485.7% | ~95% | -32.1% |
| Tactical alone (100%-basis) | $22,108 | +121.1% | 34.7% | -7.3% |
| **Combined P-300** (50% Core + 50% Tactical) | **$39,480** | **+294.8%** | **~66%** | **-15.7%** |

**Core sub-sleeve contribution** (each compounded as-if-standalone):
| Sub-sleeve | Compounded return alone | Days fired | Mean per active day |
|---|---|---|---|
| R4 ETH | +116.7% | 64 | +0.088% |
| R4 BTC | +62.0% | 128 | +0.052% |
| EMA(BTC) | +41.2% | 973 (every day) | +0.044% |
| ETH daily | +29.8% | 119 | +0.028% |

(Numbers don't sum to total because they each compound independently — the
real total combines them with vol-target leverage applied.)

**Regime mix over the window:**
- `uncertain`: 62.5% of days (R4 sleeves carry the load)
- `bear`: 25.3% (only EMA fires)
- `strong_bull`: 9.8%
- `mild_bull`: 2.5%
- Vol-target gate fired: 29.7% of days

**Tactical sleeve contribution** ($10k starting):
| Sleeve | Trades | Win% | Total $ |
|---|---|---|---|
| CARRY | 11 | 55% | $6,546 |
| ADX | **21** | 33% | **$3,508** |
| THU_BEAR | 68 | 63% | $1,371 |
| FOMC | 11 | 100% | $668 |
| PDO_RETOUCH | 43 | 40% | $9 |
| CPR | 23 | 74% | $6 |

**v6 vs v5 (perp signals) deltas**: combined NAV +$1,925, max DD improved by 1.7pp,
ADX gained one trade and +$538 (the 2026-04-27 long entry that perp ADX missed
because spot crossed 25 while perp peaked at 24.2). All other tactical sleeves
unchanged — only signals that read raw OHLC (ADX, regime classifier, JPLUS R4)
were affected by the data-source switch.

---

## 7. Methodology caveats

1. **In-sample selection bias**. The Aggressive 2.0 family was chosen
   from {Conservative / Regime-dynamic / Kelly / Aggressive} based on
   backtest performance. Core J+'s regime weights, R4 windows, and ETH
   weights were tuned on roughly the same era of data. Live forward
   performance will likely be lower than the in-sample +294.8% total
   (~66% CAGR).

   **R4 windows-grid selection (2026-05-08, separately).** The live R4
   config for `JPLUS_R4_BTC` (Mon wk1-2 06→18 UTC, was Mon+Wed) and the
   V2 sleeves (Wed+Fri wk1-2 04→14 UTC) were picked from a 7,500-config
   grid search over (asset × day × week × start-hour × end-hour) using
   the constraint "57 configs that were positive in every backtest
   year". The Wed-responds-better-to-04→14 + Wed+Fri-era-stability
   t-stats (+4.6 over 402 fires; +2.5 OOS walk-forward per
   [studies/notebooks/r4_study/findings.md](studies/notebooks/r4_study/findings.md)) are
   empirically defensible, but the post-ETF era (2024-01 onward, ~2.3y)
   is too short for the post-ETF-only walk-forward to be conclusive —
   the findings doc acknowledges this. The user's decision to run the
   live config and watch it via the strategy_health expectancy monitor
   is documented in `memory/feedback_r4_post_etf_ride_with_monitor.md`.
   Worth recording as a distinct selection-bias surface from the family
   choice above.

2. **Look-ahead protections** are real and tested ([tests/test_jplus_lookahead.py](tests/test_jplus_lookahead.py)) — the upstream ML R4 gate had
   within-day look-ahead and was REPLACED by a rule-based gate. But not
   every input has been audited at the same depth.

3. **GOLD overlay is dropped.** Upstream P-100 J+ MLgate had a 15–55%
   GOLD allocation as crisis hedge. p300 has no `macro_daily` table and
   the asset isn't wired. The crypto side now stands at 1.0 weight.

4. **FOMC sleeve (added 2026-04-30) has only 11 in-sample backtest events.**
   100% in-sample win rate. Bootstrap on the 52-event historical cohort
   (73% win) projects ~12% sleeve max DD at 10× leverage. Live edge
   unproven; first real-time test on the next 6+ FOMC events.

5. **CPR's historical sample is thin** (12 BTC + 9 ETH events upstream)
   and **PDO's params** were selected via parameter sweeps without
   walk-forward CV — both carry data-snooping exposure.

6. **S-096 V4 event filter** (CPI/NFP-adjacent, ex-OPEX) was derived
   post-hoc from V3's Thursday attribution. V4 backtest comparisons are
   in-sample; live paper is the first genuine OOS record.

7. **Sharpe / MDD numbers are not deflated for multiple-testing.** No
   bootstrap CI, no Monte Carlo, no White's reality check. Treat point
   estimates as suggestive only. **Sharpe is also computed with
   risk-free rate = 0** — `(mean / sd) × √365` in
   [strategies/support/strategy_health.py](strategies/support/strategy_health.py),
   [backtest_runner.py](backtest_runner.py),
   [studies/notebooks/full_portfolio_report.ipynb](studies/notebooks/full_portfolio_report.ipynb), and
   [studies/notebooks/tools_statistical_validation.ipynb](studies/notebooks/tools_statistical_validation.ipynb).
   At a 4–5% Fed funds rate, that overstates Sharpe by ~0.5–0.7
   depending on series volatility. The crypto convention is rf=0; we
   follow it but note the gap for cross-asset comparisons.

8. **Daily-NAV MDD understates intraday DD at 5–10× leverage** in stress
   regimes. Factor this into any risk claim — the -17.4% combined max DD
   is computed on daily closes, not intraday low-water marks.

9. **Live BTC-long cap (skip-if-over) vs simulator cap (proportional
   down-scale) diverge by construction** — live NAV ≠ sim NAV even with
   identical signals.

10. **AI_QUANT is excluded from all backtest figures in §6.** The sleeve
    is non-deterministic (LLM outputs vary run-to-run) and is skipped on
    historical replay via `params.deterministic=False`. Its edge — if any —
    will only be visible in forward paper PnL, and must be evaluated
    *net of API cost* (capped at $5/day, ~$1,825/yr against a 2% sleeve).

11. **Execution-cost model — fees + slippage, modeled separately.**
    `strategies.trades.compute_perp_close` charges round-trip cost as
    `(cost_bp_rt + slippage_bp_rt)` against notional at close. Defaults
    (since 2026-05-13, audit-calibrated):
    - `DEFAULT_COST_BP_RT = 10.0` — Binance taker fee × 2 legs.
    - `DEFAULT_SLIPPAGE_BP_RT = 5.0` — conservative mid of the
      audit-estimated 5–10bp/RT retail bid-ask spread + market-impact
      band at $1k–$20k notional.
    - **FOMC override: `SLIPPAGE_BP_RT = 10.0`** — 10× leverage at the
      announcement bar, when BTC/USDT spread widens.
    - **CARRY: `CARRY_SLIPPAGE_PCT = 0.04`** on top of the existing 20bp
      fee — 4 fills × 1bp limit-style slip on the synthetic spot+perp
      position. Lower than directional because CARRY entries are at the
      basis, not market.
    - **JPLUS_EMA_BTC and JPLUS_ETH_DAILY** now use the same default
      (10bp fee + 5bp slip + funding accrual) on close. Pre-2026-05-13
      these sleeves passed `cost_bp_rt=0.0, apply_funding=False` because
      ETH_DAILY's status was framed as "pending a spot-fee model" — but
      both trade the *perp*, so the zero-funding default was structurally
      wrong. Multi-week ETH-LONG perp at 8h funding ~0.005% accrues to
      ~4.5%/yr and was previously invisible to paper PnL.
    - **Pre-2026-05-13 paper PnL** was net of fees only. Forward
      numbers from 2026-05-13 onward are net of fees + slippage; the
      backtest figures in §6 (run before this commit) are not. Treat the
      step-down on 2026-05-13 as a methodology change, not a regime
      change.

---

## 8. Where to look in the code

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

**Sleeves (per-asset / per-event decision logic)**

| Concern | File |
|---|---|
| Tactical sleeves | [strategies/sleeves/](strategies/sleeves/) — adx, carry, thu_bear, pdo, cpr, fomc |
| Core J+ live handlers | [strategies/sleeves/r4/signal.py](strategies/sleeves/r4/signal.py) (R4 family), [strategies/sleeves/ema/signal.py](strategies/sleeves/ema/signal.py) (EMA_BTC), [strategies/sleeves/eth_daily/signal.py](strategies/sleeves/eth_daily/signal.py) |
| AI_QUANT discretionary trader | [strategies/sleeves/ai_quant/](strategies/sleeves/ai_quant/) — signal, decision, context, prompt, chart, journal, archive |
| Decision rule for FOMC | [strategies/sleeves/fomc/signal.py:evaluate()](strategies/sleeves/fomc/signal.py) |
| Two-phase dispatch contract (Intent + reconcile) | [strategies/support/dispatch.py](strategies/support/dispatch.py) |

**Cross-sleeve coordinators (§4)**

| Concern | File |
|---|---|
| J+ regime classifier (4 modes) | [strategies/support/regime_jplus.py](strategies/support/regime_jplus.py) |
| Tactical regime classifier (4 modes) | [strategies/support/regime_tactical.py](strategies/support/regime_tactical.py) |
| Per-tick allocation table | [strategies/support/allocation.py](strategies/support/allocation.py) |
| Gating framework (R4 vol-gate, V4, FOMC) | [strategies/support/gating.py](strategies/support/gating.py) |
| Portfolio vol-target scalar | [strategies/support/portfolio_vol.py](strategies/support/portfolio_vol.py) |
| Margin headroom (skip + reduce policies) | [strategies/support/margin_headroom.py](strategies/support/margin_headroom.py) |
| Directional conflict resolver | [strategies/support/conflict_resolver.py](strategies/support/conflict_resolver.py) |
| Concordant signal aggregator | [strategies/support/signal_aggregator.py](strategies/support/signal_aggregator.py) |
| Reconcile pass (pool + conflict + margin) | [strategies/support/dispatch.py:reconcile_intents](strategies/support/dispatch.py) |
| Gate validation protocol | [GATE_VALIDATION.md](GATE_VALIDATION.md) |
| Core J+ sizing inputs (regime/lev/gate/ema_p) | [strategies/support/jplus_inputs.py](strategies/support/jplus_inputs.py) (`today_inputs()`) |
| Core J+ analytic backtest (research-only) | [studies/jplus_analytic/simulate.py](studies/jplus_analytic/simulate.py) |
| Vol-target leverage (J+ family) | [strategies/support/voltarget.py](strategies/support/voltarget.py) |
| BTC-LONG cross-sleeve cap (PDO + CPR) | [strategies/support/risk_caps.py](strategies/support/risk_caps.py) |

**Plumbing**

| Concern | File |
|---|---|
| Look-ahead-safe clock | [strategies/support/clock.py](strategies/support/clock.py) |
| Sim-mode tick primitive | [strategies/support/sim_loop.py](strategies/support/sim_loop.py) |
| Live price feed (1m spot, strict-`<`) | [strategies/support/price_feed.py](strategies/support/price_feed.py) |
| Realized PnL aggregation | [strategies/support/strategy_health.py:trades_daily_returns](strategies/support/strategy_health.py) |
| Trade-row schema + standardized close log | [strategies/support/trade_db.py](strategies/support/trade_db.py) |
| AI_QUANT decision archive (markdown mirror) | [strategies/sleeves/ai_quant/archive.py](strategies/sleeves/ai_quant/archive.py) + [strategies/sleeves/ai_quant/archive_rebuild.py](strategies/sleeves/ai_quant/archive_rebuild.py) |
| External data fetchers (Binance, Coinalyze, NY Fed, F&G, Polymarket, news, CoinDesk) | [data/sources/](data/sources/) |
| FOMC observer audit log | `data/databases/prod.db:fomc_observer` |

---

## 9. Live and sim modes

Live trading runs from [`bot.py`](bot.py). Sim mode runs from
[`studies/simulation/sim.py`](studies/simulation/sim.py). They share
identical dispatch logic — only the data source and clock differ:

| | LIVE ([`bot.py`](bot.py)) | SIM ([`studies/simulation/sim.py`](studies/simulation/sim.py)) |
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

The same `STRATEGY_DISPATCH` runs in both modes; the same six J+ live
handlers and six tactical handlers open trades to whichever
`--dash-db` path the orchestrator is pointed at. Sim mode produces a complete
trade ledger that reporting tools (`studies/notebooks/full_portfolio_report.ipynb`,
`studies/notebooks/backtest_report.ipynb`) can summarize identically to a live run.

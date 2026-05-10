# P-300 Aggressive 2.0 — Portfolio Composition

A complete reference for what the bot trades, when each strategy fires, what
leverage it uses, and how the pieces compose. All percentages are **fractions
of total capital** unless stated otherwise.

> Variant ID: `p300_aggressive_v2_v1_0` · Status: SHADOW (paper-only)
> Last updated: 2026-05-10 (live/sim refactor — daily-return accrual + catchup deleted; sim mode added; analytic simulator now research-only).

---

## 1. Top-level allocation

| Block | Capital | Mechanism | What it writes |
|---|---|---|---|
| **Core J+ engine** | **50%** | daily-return accrual via `jplus.simulate()` | `variant_daily_returns` (one row/day, `source='live_computed'`) |
| **Tactical stack** | **50%** | discrete entries/exits in 6 sleeves | `trades` table (`execution_mode='SHADOW'`) |
| **AI_QUANT** (experimental) | **+2%** *additive, default-OFF* | daily LLM decision (Anthropic Opus 4.7) at 00:05–00:15 UTC | `trades` table (`execution_mode='SHADOW'`) |
| ~~Stable reserve~~ | 0% | (removed 2026-04-30 — FOMC absorbed the slot) | — |

The Core's daily return is computed once per day after midnight UTC. The
6 tactical sleeves each tick every minute, opening / closing phantom trades
based on their own signal logic. AI_QUANT is an additional Phase-1
experimental bucket layered on top — gated behind an env var and skipped on
historical replay (see §2.7).

---

## 2. The Tactical Stack — 6 sleeves, 50% of capital (+ AI_QUANT, additive)

Each sleeve has its own service module under [services/](services/) and is
dispatched per-minute by [services/variant_engine.py](services/variant_engine.py).

| Sleeve | Allocation | Leverage | Asset | Direction | Holding period |
|---|---|---|---|---|---|
| [S-003 ADX](services/adx_service.py) | **15%** | 5× | BTC | both | days–weeks |
| [S-078 Carry](services/carry_service.py) | **8%** | 5× | BTC (delta-neutral) | n/a | days |
| [S-096 V4 Thu Bear](services/thu_bear_service.py) | **6%** (3% BTC + 3% ETH) | 5× | BTC + ETH | SHORT | 24h (Thursdays) |
| [S-102 PDO-L-RF](services/pdo_retouch_service.py) | **11%** (5.5% BTC + 5.5% ETH) | 1× | BTC + ETH | LONG | 24h |
| [S-101 CPR](services/cpr_service.py) | **5%** (2.5% BTC + 2.5% ETH) | 1× | BTC + ETH | LONG | up to 15 days |
| [S-103 FOMC](services/fomc_service.py) | **5%** | 10× | BTC | LONG | ~10.5h (FOMC days only) |
| [AI_QUANT](services/ai_quant_service.py) *(experimental)* | **+2%** *additive, default-OFF* | 3× | BTC | LONG / SHORT / FLAT | LLM-discretionary (no fixed exit) |

**Tactical total: 50%** (matches Core's 50% so the portfolio is fully allocated).
**AI_QUANT** sits on top as a **Phase-1 experimental** bucket — its 2% is
*not* drawn from the 50% tactical allocation; gross exposure with AI_QUANT
enabled tops out at ~102% of capital before vol-target leverage and Core
overlap.

> **Stop-loss semantics.** All `stop_loss_pct` values configured per sleeve
> are interpreted as **price-move** percentages by default — i.e. a 10% stop
> at k=5× triggers when price moves 10%, after which the trade has lost 50%
> of margin. This matches the calibration used in the historical backtests
> below. Set `P300_STOP_SEMANTICS=margin` to interpret the same numbers as
> margin-loss caps (10% margin-loss → 2% price move at k=5×); if you do,
> re-tune each sleeve's `stop_loss_pct` since the pcts were sized for the
> price-move semantic. See [services/risk_config.py](services/risk_config.py).

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
  - **Phase**: from [services/fed_funds_service.py](services/fed_funds_service.py) — NY Fed XML, classified as `zirp_hold / hiking / peak_hold / cutting / mid_hold`.
  - **F&G**: from [services/sentiment_index_service.py](services/sentiment_index_service.py) — alternative.me daily Fear & Greed.
  - **Expected action**: from [services/polymarket_service.py](services/polymarket_service.py) — implied per-meeting cut probability from the "How many Fed rate cuts in 2026?" market.
- **Audit trail**: every FOMC date writes a row to `fomc_observer` in `data/trader.db` with the decision + reason + inputs, even when the decision is SKIP.
- **Edge thesis**: short-window event trade. Drift up into the announcement, partial fade after. Filter weeds out the regimes where this fails.
- **Caveat**: filter was tuned on the same 52-event historical cohort the in-sample backtest is drawn from. Going-forward edge unproven.

### 2.7 AI_QUANT — Discretionary LLM trader (experimental, default-OFF)

- **Status**: Phase-1 experiment. Added 2026-05-08. Default-disabled via
  `AI_QUANT_ENABLED` env var ([`.env.example`](.env.example) ships with
  `false`); when unset, [services/ai_quant_service.py:_kill_switch_on()](services/ai_quant_service.py)
  short-circuits to `status='disabled'` and no LLM call is made.
- **Allocation**: 2% of capital, additive on top of Core+Tactical (not part of
  the 50/50 split). Raise to 5% only after 60+ days of forward shadow PnL
  net of API cost.
- **Leverage**: 3×. **Asset**: BTC perp. **Direction**: LONG / SHORT / FLAT,
  chosen daily by the model.
- **Signal**: an Anthropic Opus 4.7 tool-use loop runs once per UTC day in a
  10-minute window (00:05–00:15 UTC). Every minute, four cheap gates are
  evaluated by [variant_engine](services/variant_engine.py) before any
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
  the 2% weight ([ai_quant_service.py:_allocation_pct_for](services/ai_quant_service.py)).
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
  ([services/ai_quant/journal.py](services/ai_quant/journal.py)) with the
  decision payload, tool calls, token usage, cost, and resulting trade
  action — including ERROR rows when context-build / chart-render / API
  fail, so idempotency triggers next tick. The journal also writes a
  human-browsable markdown mirror per row to
  `data/ai_quant_archive/{date}_{variant}_{asset}_{decided}_id{N}.md`
  ([services/ai_quant/archive.py](services/ai_quant/archive.py)) for
  decision-quality monitoring; regenerable from the DB via
  [tools/ai_quant_archive_rebuild.py](tools/ai_quant_archive_rebuild.py).
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

## 3. Core J+ Engine — 50% of capital

The Core is a composite strategy whose machinery lives in the [jplus/](jplus/)
package. Each of its six sub-sleeves is dispatched as a tactical-style
top-level entry in `STRATEGY_DISPATCH` and runs its own per-tick handler in
[services/jplus_live.py](services/jplus_live.py):

| Strategy ID | Asset | Live entry condition | Live exit condition |
|---|---|---|---|
| `JPLUS_R4_BTC` | BTC perp | **Mon** wk1-2, 06:00 UTC | scheduled 18:00 UTC same day |
| `JPLUS_R4_ETH` | ETH perp | Tue 20:00 UTC where next-day Wed.day ≤ 14 | scheduled Wed 20:00 UTC |
| `JPLUS_R4_BTC_V2` | BTC perp | **Wed/Fri** wk1-2, 04:00 UTC | scheduled 14:00 UTC same day |
| `JPLUS_R4_ETH_V2` | ETH perp | **Wed/Fri** wk1-2, 04:00 UTC | scheduled 14:00 UTC same day |
| `JPLUS_EMA_BTC` | BTC perp | first tick with `today_inputs.ema_p ≠ 0` | open-ended; FLIP on weekly cross |
| `JPLUS_ETH_DAILY` | ETH perp | first tick after regime enters strong_bull / mild_bull | first tick after regime exits bull |

The V1 R4_BTC sleeve was **Mon+Wed** before 2026-05-08; the calendar-window
study in [tools/r4_study/](tools/r4_study/) found that Wed responds better
to a 04→14 UTC window than the V1's 06→18, so Wednesday was moved to a new
sleeve (R4_BTC_V2) along with Friday — historically the strongest single
weekday cell on BTC (NFP-anticipation effect). The same Wed+Fri 04→14 cell
extracts comparable signal on ETH (R4_ETH_V2) per the cross-asset study.

Each handler:
- pulls today's regime mode, vol-target leverage, R4 gate, EMA position,
  and sub-sleeve weights from [`jplus.simulate.today_inputs()`](jplus/simulate.py)
  — which derives them strictly from data through yesterday's close;
- prices the entry from [`services.price_feed.get_current_price`](services/price_feed.py)
  (latest closed 1m bar — ~30s lag);
- writes the trade via [`services.trades.open_shadow_trade`](services/trades.py),
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
adjustments to the [trades](services/trades.py) and `trade_adjustments`
tables at the actual signal moment. That ledger is the single source of
truth for realized PnL — Core sub-sleeves and tactical sleeves both
write there uniformly. `SELECT * FROM trades WHERE status='open'`
returns the bot's complete current exposure.

Two earlier paths were removed in the 2026-05-10 live/sim refactor:
- The simulator-driven daily-return accrual (`services/jplus_service.py`,
  which wrote `variant_daily_returns` once per UTC day from the analytic
  formula) was a research artifact running parallel to the realized
  trade ledger. With the trade ledger as the canonical PnL, the parallel
  theoretical track only confused the operator about which number was real.
- The retroactive trade-emitter (`services/jplus_trade_emitter.py`,
  startup gap-filler) backfilled trades for dates the bot had been
  offline. That has no analogue in real trading and silently masked
  sleeve-disablement bugs (the V2 sleeves silently fired only via
  catchup for two days because they were missing from the variant
  composition).

Today: the bot opens trades when each handler's signal fires; if the
bot is offline during a window, that trade is missed permanently — same
semantics as tactical sleeves. The analytic
[`jplus.simulate.simulate()`](jplus/simulate.py) function remains
available as a **research-only** tool for offline analysis (regenerating
§6 numbers, parameter sweeps, walk-forward studies); no runtime path
calls it.

Cost migration: [jplus/r4.py](jplus/r4.py) emits gross window returns
(`COST_BP_RT = 0.0`); the 10bp R4 round-trip is charged at trade close.
[jplus/ema_sleeve.py](jplus/ema_sleeve.py)'s `_COMMISSION` is `0.0`
explicitly (was a phantom constant pre-migration). ETH_DAILY remains
zero-fee in the simulator and live handler pending a spot-fee model.

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
| EMA(BTC) | [jplus/ema_sleeve.py](jplus/ema_sleeve.py) | position direction × BTC daily return | 1× |
| ETH daily | [jplus/data.py](jplus/data.py) | ETH's daily return | 1× |
| R4 BTC | [jplus/r4.py](jplus/r4.py) | Mon 06→18 UTC window return | **2.5× / 1×** (gated) |
| R4 ETH | [jplus/r4.py](jplus/r4.py) | 24h Tue 20→Wed 20 UTC window return | **2.5× / 1×** (gated) |
| R4 BTC V2 | [jplus/r4.py](jplus/r4.py) | Wed/Fri 04→14 UTC window return | **2.5× / 1×** (gated) |
| R4 ETH V2 | [jplus/r4.py](jplus/r4.py) | Wed/Fri 04→14 UTC window return | **2.5× / 1×** (gated) |

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

- **Signal**: pure calendar trigger. Fires on Mondays whose date is **≤ 14 of the month** (first half only). Mon-only since 2026-05-08; was Mon+Wed before the V1/V2 split — Wednesdays moved to R4 BTC V2 at the era-stable 04→14 window. See [tools/r4_study/findings.md](tools/r4_study/findings.md).
- **Entry**: `06:00 UTC` open price of that day.
- **Exit**: `18:00 UTC` open price (i.e., end of the 12-hour window), same day.
- **Direction**: LONG always.
- **Return per fire**: `(price_at_18:00 − price_at_06:00) / price_at_06:00 − 10bp RT cost`.
- **Inner leverage**: **2.5×** normally, **1.0×** when the vol-percentile gate fires (§3.3).
- **Daily contribution**: `R4_BTC_return × inner_lev × regime_weight` (0.30 in `uncertain`, 0.20 in `mild_bull`, 0.15 in `strong_bull`, 0 in `bear`).
- **Cost model**: 10bp round-trip taker fee, baked into the windowed return.
- **Edge thesis**: post-Binance-perp / post-ETF emergent flow effect — Mon was −0.76%/trade pre-Binance and +0.83%/trade post-ETF. The strategy bets on the post-2024 regime continuing; per-sleeve health metrics in [services/strategy_health.py](services/strategy_health.py) trigger disable on expectancy decay. See [tools/r4_study/findings.md](tools/r4_study/findings.md).
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
- **Edge thesis**: era-stable BTC alpha cell (positive in pre-Binance-perp, Binance-perp, and post-ETF eras). Likely captures NFP-anticipation (Friday wk1 cell is the strongest single-day cell on BTC) plus early-month Wed flow. Full-sample t=+4.6 across 402 fires per the [r4_study](tools/r4_study/) grid search.
- **Active in**: every non-`bear` regime.

#### 3.1.6 R4 ETH V2 — Wed + Fri intraday long, weeks 1–2 (added 2026-05-08)

- Same calendar and window as R4 BTC V2 (Wed+Fri wk1-2 04→14 UTC), applied to ETH.
- **Daily contribution**: `R4_ETH_V2_return × inner_lev × regime_weight` (0.20 in `uncertain`, 0.15 in `mild_bull`, 0.075 in `strong_bull`, 0 in `bear`).
- **Edge thesis**: cross-asset bonus from the BTC study — the same Wed+Fri 04→14 window extracts comparable signal on ETH (+0.62% pre-ETH-ETF, +0.48% post-ETH-ETF per fire).
- **Active in**: every non-`bear` regime.

### S-095 3.2 Per-regime allocation weights ([jplus/simulate.py](jplus/simulate.py))

| Mode | EMA(BTC) | ETH daily | R4 ETH | R4 BTC | R4 BTC V2 | R4 ETH V2 | Sum | When this fires |
|---|---|---|---|---|---|---|---|---|
| **strong_bull** | 0.50 | 0.20 | 0.15 | 0.15 | 0.075 | 0.075 | 1.15 | full risk-on |
| **mild_bull** | 0.30 | 0.10 | 0.30 | 0.20 | 0.10  | 0.15  | 1.15 | partial risk-on, R4 emphasised |
| **uncertain** | 0.30 | 0.00 | 0.40 | 0.30 | 0.15  | 0.20  | 1.35 | calendar-driven only (R4 carries) |
| **bear** | 0.30 | 0.00 | 0.00 | 0.00 | 0.00  | 0.00  | 0.30 | EMA only, R4 idle |

**Total > 1.0 in three regimes**: as of the V2 sleeves being added (2026-05-08), Core total exposure can exceed 1.0 when multiple R4 sleeves fire concurrently. Peak concurrent exposure is on Wednesdays (R4 ETH V1 still open from Tue + R4 BTC V2 + R4 ETH V2 all firing 04:00-14:00 UTC) — about 75% of capital in `uncertain` regime. Vol-target leverage scales this further. The V2 sleeves are at half the V1 weights specifically to keep peak Wed concurrent exposure comparable to the pre-2026-05-08 baseline.

The bot spent 62% of the 2023-09 → 2026-04 window in `uncertain` and 25% in
`bear` — so for most days, R4 BTC and R4 ETH are doing the real work when
they fire, and EMA carries the rest.

### 3.3 Layer 1 — R4 inner multiplier ([jplus/simulate.py:26-27](jplus/simulate.py))

R4 BTC, R4 ETH, R4 BTC V2, R4 ETH V2 (and ONLY those four — not EMA, not ETH daily) get an inner
amplification on top of their raw windowed return:

| State | Multiplier | Why |
|---|---|---|
| Normal day | **2.5×** | R4 is a sized sleeve within the J+ portfolio |
| **Vol-percentile gate fired** | **1.0×** | de-lever in high-vol regimes |

The gate ([jplus/gate.py](jplus/gate.py)) fires when the trailing 30-day BTC realized vol is in the **top 25%** of the 365-day distribution — strictly using T-1 data, no look-ahead. Fired on **29.7% of days** in the v6 backtest window.

### 3.4 Layer 2 — regime weights

See table 3.2 above.

### 3.5 Layer 3 — vol-target outer leverage ([jplus/voltarget.py](jplus/voltarget.py))

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

Stacking the 3 layers on a representative day: R4 ETH firing on a Tuesday in `uncertain` regime, gate not fired, vol-target lev 2.0:

```
final_contrib_to_daily_return =
    raw_R4_ETH_return × R4_inner(2.5) × regime_weight(0.40) × vol_target(2.0)
  = raw × 2.0
```

So R4 ETH's effective leverage on that day is **2.0× of raw spot move**, applied to **20% of capital** (Core's 50% × the portion of `rl` that R4 ETH contributes that day, ~40%).

---

## 4. Regime classifier ([jplus/regime.py](jplus/regime.py))

Classifies each day into one of 4 modes using **only T-1 data** (look-ahead-safe).

| Mode | Trigger |
|---|---|
| **strong_bull** | close > EMA(50) AND close > EMA(20) AND m30 > 0 AND m7 > 0 |
| **mild_bull** | close > EMA(50) AND (m30 > 0 OR close > EMA(20)) |
| **bear** | close < EMA(50) AND m30 < 0 |
| **uncertain** | otherwise — OR peak-DD > 5% while bullish, OR LS circuit-breaker active |

Two override rules:
- **LS circuit breaker**: if 7-day LSR delta < -15 (long crowd unwinding), force `uncertain` for next 7 calendar days.
- **Peak-DD override**: if current close is > 5% off trailing peak AND mode would otherwise be bullish, demote to `uncertain`.

Inputs: BTC daily close, EMA(20), EMA(50), 30-day momentum, 7-day momentum, LSR snapshots, spot peak — all at T-1.

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

---

## 6. Portfolio-level performance summary (v6 simulation, all-spot signals)

In-sample backtest, 973 days / 2.66 years (2023-09-01 → 2026-04-30).
This is the canonical baseline as of 2026-05-01 — all signal computations
read spot data (`cd_spot_binance`, `btc_1m`), aligned with TradingView's
default BTCUSDT 1D feed. Earlier v1–v5 numbers used perp data and are
superseded; do not compare side-by-side.

> The Core columns below are the analytic output of
> [`jplus.simulate.simulate()`](jplus/simulate.py), which has been
> retained as a research-only tool after the 2026-05-10 live/sim
> refactor — no runtime path calls it. Tactical numbers come from the
> backtest_runner's realized trade ledger. Live operation (real-money or
> SHADOW) tracks PnL purely from the trade ledger via
> [services/strategy_health.py:trades_daily_returns](services/strategy_health.py),
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
   estimates as suggestive only.

8. **Daily-NAV MDD understates intraday DD at 5–10× leverage** in stress
   regimes. Factor this into any risk claim — the -17.4% combined max DD
   is computed on daily closes, not intraday low-water marks.

9. **Live BTC-long cap (skip-if-over) vs simulator cap (proportional
   down-scale) diverge by construction** — live NAV ≠ sim NAV even with
   identical signals.

10. **AI_QUANT is excluded from all backtest figures in §6.** The sleeve
    is non-deterministic (LLM outputs vary run-to-run) and is skipped on
    historical replay via `params.deterministic=False`. Its edge — if any —
    will only be visible in forward shadow PnL, and must be evaluated
    *net of API cost* (capped at $5/day, ~$1,825/yr against a 2% sleeve).

---

## 8. Where to look in the code

| Concern | File |
|---|---|
| Variant registration + weights | [register_p300.py](register_p300.py) |
| Sleeve dispatch + spec resolution | [services/variant_engine.py](services/variant_engine.py) |
| Per-tactical-sleeve services | [services/](services/) — adx, carry, cpr, fomc, pdo_retouch, thu_bear |
| Core J+ live handlers | [services/jplus_live.py](services/jplus_live.py) — r4_btc / r4_eth / r4_btc_v2 / r4_eth_v2 / ema_btc / eth_daily |
| Core J+ sizing inputs | [jplus.simulate.today_inputs()](jplus/simulate.py) — regime/lev/gate/ema_p/weights from T-1 data |
| Core J+ analytic backtest (research-only) | [jplus.simulate.simulate()](jplus/simulate.py) |
| AI_QUANT discretionary trader | [services/ai_quant_service.py](services/ai_quant_service.py) + [services/ai_quant/](services/ai_quant/) |
| Decision rule for FOMC | [services/fomc_service.py:evaluate()](services/fomc_service.py) |
| FOMC observer audit log | `data/trader.db:fomc_observer` |
| Look-ahead clock infrastructure | [services/clock.py](services/clock.py) |
| Sim-mode loop primitive | [services/sim_loop.py](services/sim_loop.py) |
| Build a sim trader.db | [tools/build_sim_trader_db.py](tools/build_sim_trader_db.py) |
| Price feed (1m spot, strict-`<`) | [services/price_feed.py](services/price_feed.py) |
| Realized PnL aggregation | [services/strategy_health.py:trades_daily_returns](services/strategy_health.py) |
| Standardized close log | [services/trade_db.py:format_close_summary()](services/trade_db.py) |
| Backtest replay engine | [backtest_runner.py](backtest_runner.py) |
| Per-decision AI_QUANT archive | [services/ai_quant/archive.py](services/ai_quant/archive.py) + [tools/ai_quant_archive_rebuild.py](tools/ai_quant_archive_rebuild.py) |
| Data-layer health checks | [health.py](health.py) |

---

## 9. Live and sim modes

The bot binary in [`run.py`](run.py) supports two modes that share
identical dispatch logic — only the data source and clock differ:

| | LIVE (default) | SIM |
|---|---|---|
| Clock | wall clock | simulated, advanced deterministically |
| Market data | `data/trader.db` (kept fresh by `binance_feed`) | `--trader-db <path>` (built by `tools/build_sim_trader_db.py`) |
| Trade ledger | `data/dashboard.db` | `--dash-db <path>` (separate file) |
| External APIs | NY Fed XML, Polymarket, F&G, news | all blocked — sim must be reproducible offline |
| Loop | wall-clock 60s tick | `services.sim_loop.run_sim` (no sleep) |

```
# Live (current default):
python run.py
python run.py --feed         # also runs binance_feed in a thread

# Sim — build a sliced trader.db, register the variant in a fresh
# dashboard sim DB, then run the bot under a fake clock:
python tools/build_sim_trader_db.py \
    --start 2024-01-01 --end 2024-12-31 \
    --output data/trader_sim_2024.db
python register_p300.py --dash-db /tmp/sim_dash.db
python run.py --mode sim \
    --start 2024-01-01 --end 2024-12-31 \
    --trader-db data/trader_sim_2024.db \
    --dash-db /tmp/sim_dash.db \
    --sim-tick-seconds 60
```

The same `STRATEGY_DISPATCH` runs in both modes; the same six J+ live
handlers and six tactical handlers open trades to whichever
`dashboard.db` they're pointed at. Sim mode produces a complete
trade ledger that reporting tools (`tools/full_portfolio_report.py`,
`tools/backtest_report.py`) can summarize identically to a live run.

# chento — strategy specification for bot replication

Translated from: 1,981 messages, ~35+ chart-era image extractions, +20 mobile-card extractions,
text mining of 46 long-form strategy posts. Companion to [strategy.md](strategy.md) (background)
and [findings.md](findings.md) (raw observations).

## TL;DR for the bot

We are modelling a discretionary trader whose framework converged into a
rule-based system by late 2025. **Most of the "magic" comes from his tool
stack and execution discipline, not from a single secret pattern.** The
edge is in:

1. Combining **8+ data streams** into a single decision (price action,
   liquidation map, CVD aggregate, money flow, probability model, SMC,
   time-of-day, multi-TF bias)
2. Strict **R:R filter** at the entry (no trade if < 1:3)
3. **Limit-bid at liquidation clusters with sweep + rejection confirmation**
4. **Multi-tier ladder scaling** instead of single-shot entries
5. **Dual-direction simultaneous positioning** (long + short hedge)
6. Aggressive but rule-bound **leverage variance** (13x → 200x)

## Tool stack inventory (8 streams identified)

| Stream | Tool | Use |
|---|---|---|
| 1 | TradingView (charting) | Multi-TF analysis, drawing zones, line projections |
| 2 | **Leviathan Liquidation Levels** (TV indicator) | Locate liquidation clusters above/below current price → entry/target |
| 3 | **aggr.trade** | 5-second aggregated tape across 16 markets — CVD + Liquidations + Volume + Price |
| 4 | Money Flow panel (Whales/Mid/Retail) | Order flow by participant size, divergence read |
| 5 | **Custom Probability Indicator** (TV) | Lunar + Calendar + MTF + Session → composite probability % |
| 6 | SMC indicator (Buy/Sell markers) | Order block / FVG identification with arrows |
| 7 | Multi-exchange position cards | Execution and PnL tracking (Bybit / LBank / Bitunix / MEXC) |
| 8 | Price action / candlestick reading | Confirmation candles (engulfing, wick, doji at level) |

## Setup decision tree (extracted)

### 1. Higher-timeframe bias (4H/1D/1W)
- Read Probability Indicator "MTF" status: STRONG BULL / NORMAL / STRONG BEAR
- Identify HTF order blocks (weekly/daily OBs)
- Mark "magnets" with emoji annotation: top magnet (supply target) + bottom magnet (demand target)

### 2. Lower-timeframe approach (15m / 5m)
- Wait for price to enter the relevant zone (premium or discount)
- Pull up Leviathan to see exact liquidation clusters within the zone
- Pre-stage limit order at the liquidation cluster price level
- Articulate the plan publicly with multi-leg zigzag projection

### 3. Entry trigger (5m / 30s / 5s)
- Wait for **liquidity sweep** (price wicks into the cluster, takes stops)
- Watch aggr.trade for cascading liquidation prints
- Confirm via **rejection candle** at the OB / cluster boundary
- Cross-check Money Flow: do whales agree with the direction?
- CVD divergence check: spot vs perp must support direction

### 4. Execution: limit order ladder
- Place 3-tier limit order ladder at:
  - Tier 1: current price (light)
  - Tier 2: -1% to -2% (more)
  - Tier 3: -2% to -4% (heaviest, at the magnet/cluster)
- Total position scales as price moves into the zone
- Example from journal: *"10% here, 10% 78.6, 10% 77.8"*

## Trade structure (the 1:3 R:R framework)

### Risk:reward gate
- **Minimum 1:3 R:R** — no trade otherwise
- Often achieves 4-5R+ via deep TP at next major structural level
- Example: 2025-12-07 short — entry 91,231 / SL 91,961 (+0.8%) / TP 87,400 (-4.2%) = R:R 5.25

### Stop placement
- Typically **0.4% to 0.8% from entry** on aggressive scalps (with 25-75x leverage)
- 2-4% from entry on swings (with 13-20x leverage)
- ALWAYS structural (above prior swing high for shorts, below prior low for longs)
- Hard rule: **stop can only tighten, never widen** (per Jan 2026 framework)

### Target placement
- Default 3R fixed (per framework)
- Often actual TP is at next major liquidation cluster (further than 3R)
- Examples observed: TP 70k from 77.4k entry (-9.5%), TP 43k from 58.4k (-26%), TP 107k from 90.9k (+17.7%)
- Magnetic target = the bottom/top of the zigzag projection

## Position management playbook

### Scaling in (validated patterns)
- **Multi-tier ladder**: 10-33% per tier at distinct price levels
- **Pre-stage**: post the plan with chart annotation BEFORE the trade fires
- **Conditional entry**: "will enter if X happens" (e.g. peak forms, OB rejects)

### Scaling out (validated patterns)
- First TP at **1R** (per Jan 2026 rules, partials allowed only after 1R)
- Default fixed TP at 3R for tier 2
- Trail or close on runner
- Partial 10-30% trim when conviction wavers ("Closed 30% will add it again at 68.8")

### Dual-direction hedging
- Run **long + short simultaneously** when conviction is mixed
- Counter-trend leg sized slightly LARGER than trend leg
- Example: Long 156 BTC + Short 337 BTC both running in profit, hedge-mode
- Confirmation levels defined for both: "80.4 long confirmation, 79.3 short confirmation"

### Drawdown management
- Hold through up to ~10% drawdown if conviction intact ("I hold till the damn end")
- Add margin to defend against liquidation
- Lower leverage in volatile regimes (20x → 13x mid-trade)
- Move SL to BE after profit shows (caption "sl be")

## Time-of-day / calendar filter

### Active windows (UTC)
- **12:00-13:00 UTC** — 1h before NY open (his "14:30 CET clockwork")
- **15:00-17:00 UTC** — NY morning session (peak posting times)
- **20:00 UTC** — NY close window
- Avoid Asia session (low conviction trades)

### Active days
- **Monday / Tuesday / Wednesday** primary (423/402/314 posts)
- Wednesday "midweek reversal" pattern
- Avoid Saturday (98 posts, lowest)

### Calendar events
- CPI release (mid-month, 12:30 UTC)
- FOMC (8x/year, 18:00 UTC announcement)
- NFP (first Friday, 12:30 UTC)
- New Moon / Full Moon (lunar cycle tracked in probability indicator)
- Bank holidays (low-liquidity range trading)

## Leverage profile (situational)

| Setup type | Leverage | Position size context |
|---|---|---|
| DCA-build long-term BTC | 3.5x | Multi-year, large capital |
| Standard swing | 13-20x | Default sizing |
| High-conviction scalp | 25-50x | After confluence signals align |
| Aggressive scalp | 75x | Big challenge accounts |
| Extreme play | 125-200x | Specific liquidation grab setups |

Risk per trade kept at 2-4% via SL distance even when leverage is high.

## Asset universe

Trading on:
- **BTCUSDT** primary (most journal entries)
- ETHUSDT secondary
- Top-10 liquid alts when structure is clean
- Bootstrap era was OP-dominant; mature era is BTC-dominant
- Multi-exchange: Bybit, LBank, Bitunix, MEXC (route depends on fees/liquidity)

## Bot-replication mapping

### What we can replicate today (with existing repo data)
- 15m-4H multi-timeframe bias from price + OHLCV ✓
- MTF cell map (`swing_base_limit_bid/discovery.ipynb`) — already validates the `+1/conf=3` and `--+++` patterns he uses ✓
- Funding + basis read (already in `cd_funding_rate`, `cd_futures_15m`) ✓
- Open Interest read (already in `cd_open_interest`) ✓
- Liquidations (when populated — currently sparse) ⚠️
- Limit-bid at swing low + N×ATR ✓ (this is the 75,485 setup)
- 1:3 R:R minimum filter ✓
- Multi-tier ladder scale-in (3-tier @ -1%/-2%/-4%) ✓ codeable

### What requires additional infrastructure
- **Liquidation map equivalent** — need to ingest historical liquidation data
  with price+size. Coinalyze API has it (free tier).
- **aggr.trade-style 5-second tape** — would need to aggregate Binance + Bybit
  + OKX trade streams in real-time. Significant infra work.
- **Probability indicator** — combines lunar/calendar/MTF/session. Buildable but
  requires Pine Script translation or custom Python.
- **Money Flow (whales vs retail)** — needs trade-size buckets per print.
  Achievable from Binance trade history (filter by USD value of each trade).

### What is NOT replicable
- Discretionary "conviction" calls
- Real-time pattern recognition on 5-second tape
- Multi-account live management (he runs Long + Short + DCA-build simultaneously)
- "I hold till the damn end" iron-hand drawdown tolerance

## Concrete next-step bot architecture

A first deployable sleeve based on this analysis would be:

```
chento_limit_bid_v1:
  market: BTCUSDT perp (Binance/Bybit)
  timeframe: 15m signal, 5m execution
  
  setup:
    - HTF (4H): not in STRONG BULL or STRONG BEAR (NORMAL only)
    - 15m: price approaching prior 36h swing low (within 1.2%)
    - Liquidation cluster below current price by < 1%
    - mtf_net ∈ {+1, +2} (from swing_base_limit_bid discovery)
    - conf_score >= 3 (basis ≤ -2bp, funding < 0, OI flush, spot CVD > 0)
    - time-of-day: 12:00-17:00 UTC (NY-overlap window)
    - day-of-week: Mon/Tue/Wed
  
  execution:
    - Limit-bid ladder: 33% at L1, 33% at L2, 33% at L3
      where L1 = swing_low * 1.005
            L2 = swing_low * 1.001  
            L3 = swing_low * 0.997 (below the swing low, sweep target)
    - SL = swing_low * 0.985 (1.5% below the low)
    - TP1 = entry + 3R (33% of position)
    - TP2 = entry + 6R (33% of position)
    - Runner = remaining 33%, trail 5% from high-water mark
    
  filters (any one disables trade):
    - R:R < 3 → no trade
    - Daily P&L < -8% → stop trading today
    - 2 stops hit today → stop trading
    - max open risk > 6% → no new positions

  scaling:
    - Position size scales up only after +20-25% equity growth
    - Account doubles → unit size doubles
```

This skeleton lands the deployable patterns from his framework. Subsequent
iterations would add:
- v2: short-side mirror
- v3: alt rotation (use OP, SOL, etc.)
- v4: dynamic leverage (lower in volatile regimes)
- v5: dual-direction hedge mode
- v6: probability indicator (lunar/calendar/MTF/session)

## Open questions — ANSWERED from extended reads (2026-05-20)

After 16 additional targeted reads (~50 total chart-era extractions), the
remaining open questions now have concrete answers.

### Limit-order management: the 3-strike rule

He treats limit orders that don't fill with a graduated escalation:

| Attempt | Behaviour | Evidence |
|---|---|---|
| **1st miss** (price runs without filling) | **Cancel** the limit | R28: *"One dwell block to early, got frontran abd 3% dump, cancel limit"* |
| **2nd miss** (re-staged limit also missed) | **Leave limit hanging**, don't chase | R29: *"frontran again 1300$ dollar move, leaving limits for now"* |
| **3rd miss** ("final try") | **Sideline for a while** | R37: *"So that didn't work, Final try, if this doesn't work I stay sidelined for a bit"* |

**Rule**: 3 strikes and out. He never chases — limits at specific prices or no trade.

### Limit ladder structure (live evidence from R31, R32)

Captured the actual order-book of a multi-tier short ladder:

| Order # | Price | Size (BTC) | Notes |
|---|---|---|---|
| Limit 1 | **77,000** | 37.43 | Tier 4 (highest) |
| Limit 2 | **76,600** | 55.50 | Tier 3 — biggest |
| Limit 3 | **76,200** | 44.40 | Tier 2 |
| Limit 4 | **75,900** | 38.08 | Tier 1 — added later |
| Limit 5 | **75,600** | 48.87 | Tier 0 (lowest) |
| TP order | **57,000** market | Entire position | Was 70,300 earlier — lowered as conviction grew |

So the ladder has **5 tiers across ~$1,400 range**, irregular sizing (37-55 BTC), and TP is dynamically adjusted (70,300 → 57,000 as conviction grew). Total planned position ≈ $13.6M.

### Drawdown defense: add margin, don't cut

R33 caption "Added some margin for liq" on a $27M short position with +$118k unrealized profit. He pushed liquidation from a closer level to $84,361 by adding margin rather than reducing the position. Position size stays constant; equity-at-risk grows.

**Implied rule**: When liq approaches within X% of mark, add margin. Don't reduce position unless invalidation level is breached.

### TP downward adjustment

R32: same trade's TP went from $70,300 → $57,000 (lowered by $13k = wider profit target as conviction grew). Per his framework "SL only tightens, never widens" — but **TP can adjust either direction** based on conviction. Lower TP for shorts = more bearish = more profit potential.

### DCA precision

R39: *"Missed my own dca by 2.5 dollars"* — his DCA limits are placed at specific decimal prices (not "around $X" but exact). When price comes within $2.50 of his level but doesn't tag, he NOTICES (and doesn't lower the limit to capture).

### Invalidation levels

R41: *"Lose 80.4 and we see 77 imo, lets see CPI"* — he names a **specific price level (80.4)** as the invalidation. If price closes below, short bias activates with $77k target. If holds, long bias continues. So:

**Invalidation = price-level break on the higher timeframe**, not a % from entry.

### Custom indicator stack (R11)

His charts show what appears to be a custom **risk/setup-quality dashboard**:

| Field | Value (one snapshot) |
|---|---|
| Weekend | NO (filter) |
| RVOL (relative volume) | 0.27 (currently LOW) |
| High RVOL | NO |
| Highest Vol | NO |
| Bullish Rej (bullish rejection flag) | NO |
| Bearish Rej (bearish rejection flag) | NO |
| HV + Rej (composite) | 2,492.4 / 675.9 |

These are **filter flags** he checks before entering. If RVOL is low and no rejection detected, the setup is weak. He waits for high-volume rejection candles at his level.

### Position-size discipline ("Baby long")

R17: "Baby long" with 30x leverage on $1.9k margin = only $57k position. So **leverage ≠ exposure**. He varies margin allocation to control true exposure even when nominal leverage is high. A 200x trade on $100 margin is just $20k position — small.

This means the "leverage profile" is not what matters; the **margin-as-%-of-equity** is the real risk metric.

## Final v1 sleeve spec — updated with answers

```yaml
chento_limit_bid_v1:
  market: BTCUSDT perp (Binance/Bybit)
  signal_TF: 15m / execution_TF: 5m

  setup:
    htf_bias: Probability indicator must be NORMAL or matching direction
    swing_base: price within 1.2% of prior 36h swing low (long) or high (short)
    liquidity_cluster: Leviathan liquidation cluster within 1% of base
    mtf_filter: mtf_net ∈ {+1, +2} for longs, {-5..-3} for shorts
    confluence: conf_score ≥ 3 (basis, funding, OI flush, spot CVD)
    time_window: 12:00-17:00 UTC (NY-overlap)
    day_of_week: Mon/Tue/Wed only
    rvol: not LOW (require RVOL ≥ 0.8 at signal bar)
    rejection_required: bullish/bearish rejection candle at the level
    
  ladder_execution:
    tier1: 20% at swing_low * 1.005  (limit above level)
    tier2: 20% at swing_low * 1.001  (limit at level edge)
    tier3: 25% at swing_low * 0.997  (limit below — sweep target)
    tier4: 20% at swing_low * 0.992  (deeper bid)
    tier5: 15% at swing_low * 0.985  (last bid — emergency)
  
  three_strike_rule:
    miss_1: cancel limit, wait for new signal
    miss_2: leave limits hanging, do NOT chase
    miss_3: sideline for 24h minimum

  trade_management:
    sl: swing_low * 0.978  (2.2% below the low — survives wicks)
    sl_only_tightens: true
    tp1: entry + 3R (close 33%)
    tp2: entry + 6R (close 33%)
    runner: remaining 34%, trail at high_water * 0.95
    tp_adjustment: allowed (lower TP for shorts / higher TP for longs as conviction grows)
    
  drawdown_defense:
    liq_approach_pct: 3   # if mark within 3% of liq
    action: add_margin     # not reduce_position
    additional_margin_pct: 25  # add 25% more margin
    cap_adds: 3            # max 3 add-margin events per trade
    
  invalidation:
    type: structural_level  # specific price, not % from entry
    source: prior_swing_high (for long) / prior_swing_low (for short)
    close_below_invalidates_long: true
    close_above_invalidates_short: true
    
  global_filters:
    r_r_min: 3
    daily_max_loss_pct: 8
    max_consecutive_losses: 2
    max_open_risk_pct: 6
    
  scaling_rules:
    grow_size_threshold: 20  # % equity growth before scaling up
    grow_size_factor: 2x     # then position size doubles
    shrink_size_trigger: drawdown_pct > 10  # halve size
```

## Multi-account architecture

The journal reveals he runs **at least 4 simultaneous accounts**:

| Account | Style | Leverage | Hold time | Purpose |
|---|---|---|---|---|
| **DCA-build** ($100k → $300-400k) | Multi-year hold | 3.5-5x | months | Long-term BTC long, $74k entry seen |
| **$200k → $2M challenge** | Position swing | 13-25x | days-weeks | Main rule-based account |
| **Bootstrap challenge** ($1k → $128k) | High-frequency scalp | 50-200x | min-hours | Live-stream content |
| **Inner Circle 50k+ group** | Mirror/signal | 13-30x | days | Mirror trades for paid group |

A v1 bot sleeve should map to **only the $200k→$2M challenge style**. Don't try to model bootstrap (200x leverage is uneconomic for a bot) or DCA-build (we don't have years).

## What we now know NOT to copy

Things to explicitly avoid in the bot, despite being part of his style:

1. **200x leverage scalps** — too close to liquidation, requires real-time tape reading
2. **Aggregator-tape-driven execution** (aggr.trade 5-sec decisions) — needs live infra he has, we don't
3. **Probability indicator without source** — proprietary tool, would need rebuilding
4. **Multi-account hedge mode** — increases complexity vs. expected return
5. **News-conditional trading ("lets see CPI")** — bot can defer trade through news but can't read news intelligently in real-time
6. **Discretionary "conviction" calls** — keep these as human-override layer

## Recommended bot build sequence

1. **chento_limit_bid_v1** (long-only, BTC, with the rules above) — 2-3 weeks
2. Backtest against 2024-2025 OHLCV in `swing_base_limit_bid/discovery.ipynb`
3. **chento_limit_bid_v2** (add short-side mirror) — 1 week
4. **Liquidation map ingestion** (Coinalyze API) — 1 week prerequisite
5. **chento_limit_bid_v3** (replace swing-low with Leviathan liquidation cluster) — 1 week
6. **Money Flow ingestion** (Binance trade-size buckets) — 1 week
7. **chento_limit_bid_v4** (add whale/retail filter) — 1 week
8. Walk-forward validation + paper trade for 30 days before any capital deployment

# chento — strategy reverse-engineering

Compiled from his explicit text posts in the Discord journal (June 2024 → May 2026).
Source: 46 text posts ≥300 chars in [../../material/chento/messages.jsonl](../../material/chento/messages.jsonl)
+ chart captions in the trade ledger.

## TL;DR

chento has run **22+ documented bootstrap challenges over 2.5+ years** (his own
count, March 2025). His strategy has **evolved dramatically** from
chaotic-but-effective bootstrap trading in 2024 → a codified rule-based framework
by late 2025. The current ($200k→$2M) framework is conservative position-trade,
the older bootstrap framework was high-leverage scalping. **Both worked** —
he uses different modes for different bankroll stages.

## Evolution timeline

### Phase A — Bootstrap (June 2024 → Nov 2024)

**Format:** 100→1k challenges. "100→1k Challenge" was his signature run.

**Self-description (June 10, 2024):**
> "I have been doing this for over 2 years now, with **19 successful runs and 20th time we lost**."

**Transparency policy he committed to:**
> "1) I post screenshot upon entry — 2) I post live updates — 3) I post screenshot of port — 4) I post upon closure. This means that if there is an SL you can see in the screenshot, entry seen in screenshot, and TP seen in screenshot."

**Mechanics observed in phase 1 data:**
- Default 20x leverage, ramps to 50x on high-conviction
- Cross-margin everywhere
- OPUSDT dominant (23 of 29 trades) — alt with high vol/dollar move
- Active DCA-up on winners (entry shifts 2.1948 → 2.2302)
- "Sl 20$" / "Risking 20$" — explicit dollar-risk discipline
- TPs at deep structural targets (TP 0.9 from 1.7 entry on OP shorts = −47%)
- Holds through drawdowns ("I hold till the damn end")

**Closure (Nov 7, 2024):**
> "End of an era. Hereby stopping the 100-1k challenge for good. We had fun, it was good but not bringing me what it used to bring."

### Phase B — Transition (Nov 2024 → Dec 2025)

Less structured public commentary. One key data point (Feb 2, 2025):
> "**8k to 18k and to 0k**, it happens, everyone goes through it... yesterday even ragequit and threw my phone to bits"

He blew up a challenge from $18k back to zero. This is the period where the
*next* framework hardens — the chaotic-but-fast bootstrap style gets retired.

By March 2025:
> "We've done this challenge **26 times of which I was successful 22 times**, 4 where after the original run."
>
> "**I know how to trade, I'm doing this for years and will look out and value the money as much as I can.** All questions will be fine and do my best to answer them correctly."

### Phase C — Codified framework (Dec 2025 → present)

**Strategy posts and explicit rules emerge.** Key documents:

#### The Coinflip Challenge rules (Dec 25, 2025)

| Rule | Value |
|---|---|
| Direction selection | Heads = Long, Tails = Short (RANDOMIZED!) |
| Move size & entry | Determined by him (structure, range, or level-based) |
| Risk:reward | **Fixed 1:3 RR** |
| Risk per trade | **2% min, 3% max** |
| Risk sizing | Based on confidence + mental state |
| Position sizing | Strictly from defined SL |
| Max losing trades/day | **2 — then STOP** |
| Revenge trading | Banned |
| SL | **Must be defined before entry, cannot be moved further away** |
| TP | Default 3R (fixed) |
| Manual early TP | Allowed ONLY with documented verbal reason on video |

This is a **structured mechanical system** — direction is randomized, only the entry mechanics (structure/range/level) are discretionary. He's testing whether his **edge is purely in execution and risk management**, not direction selection.

#### The $200k → $2M challenge rules (Jan 5, 2026)

| Element | Spec |
|---|---|
| Markets | **BTC, ETH, top-10 liquid alts** |
| Timeframes | **15m – 4H** |
| Style | "Structured intraday / swing" |
| R:R | **Minimum 1:3** |
| Risk per trade | 2% – 4% |
| Max open risk | 6% total |
| If R:R < 1:3 | **NO trade** |
| Adding to losers | **Banned** (explicit anti-DCA rule for big account) |
| Partial profits | **Allowed only after 1R** |
| Daily max loss | 8% — hitting = stop trading |
| Position size scale-up | Only after **+20-25% equity growth** |
| Milestones | 200k→300k→500k→750k→1.2M→2M |
| Win condition | **$2,000,000** |

**Critical contrast:** the $200k→$2M rules **explicitly forbid DCA into losers**, whereas his phase A bootstrap style was *built on* DCA-up management. Two different frameworks for two different account sizes/timeframes.

#### Dual-mode current execution (Feb 6, 2026)

He runs **two simultaneous approaches** in the current volatile market:

> "Current market is insanely volatile, meaning normal 20x will not only get stopped out it will get LIQUIDATED."
>
> **Mode 1 — "high leverage low margin sniping"**: less exposure but decent position sizes. Quick scalps.
>
> **Mode 2 — DCA scaling low leverage and large capital**: "100k account, goal for this is to hold years.... If we hit all entries, we go mid 40's and if below I will add margin and DCA more, building as low as 30's and average at 45 holding towards what I believe is the next top 140 region. Meaning a casual 3.5x with 8-12 btc... so 100 → 300/400k in 1-2 years"

So he keeps **two books simultaneously**:
- A scalper book on the leveraged challenge (1k→128k style)
- A DCA-build book on a $100k → $300-400k multi-year hold

## Setup framework — what he's actually looking at

### Visual TA stack (from chart screenshots in phase 1)

- **SMC indicator**: Buy / Sell markers with Smart Money Concepts framework
- **Multi-TF Order Blocks**: explicitly labeled "OB 4h" / "Daily OB" / "Weekly OB" stacked
- **Fair Value Gaps (FVG)**: pink zones for premium, green zones for discount
- **Fibonacci retracement**: 0.382 / 0.5 / 0.618 / 0.786 / 1.0 levels overlaid
- **Liquidity zones / sweeps**: he calls out "liquidations on long side" and "trapping volume"

### Quantitative tools (text posts 2025-Q4 onwards)

- **CVD perp vs CVD spot** — explicit divergence read:
  > "cvd perp's is moving up, cvd spot keeps aggressively selling, not good if it is a spot driven move constantly"
- **OI + liquidations** — "as soon as 88 is approached the oi and sells get triggered"
- **Money flow tool** (Whales > $1M / Mid $100k-$1M / Retail < $100k) — references in May 2026 commentary
- **Orderbook thinness** — "all small moves are stacked with liquidity, 200$ dump 10m liquidation on long side"

### Pattern-recognition language (his vocabulary)

- **"theory of 3"** — repetition of a pattern 3 times = high-conviction trade
- **"clockwork"** — repetitive time-based behavior (e.g. NY-open dump 1h before NY open)
- **"the milk"** — when MMs range-trap traders before next leg
- **"snap the daily"** — break a daily structural level
- **"liquidity grab"** — sweep above/below before the real move
- **"scam pump / firework down"** — pre-weekend manipulation patterns

### Higher-timeframe scenario builds

He explicitly states scenarios with probabilities (Feb 16, 2026):
> "1) conviction down with only 48% probability — 2) New Moon probability down 66.2% — 3) bank holiday and other factors probability up 60.5%"

So he's running a **weighted scenario tree** — multiple paths with assigned probabilities — rather than a single deterministic prediction.

## Entry & exit logic — best inference

### How he selects entries

1. **Higher-timeframe bias** — weekly/daily OB structure determines long vs short
2. **Multi-TF alignment** — looks for cases where lower TFs flip against the HTF (the "monthly bullish, daily bearish, hourly bullish" pattern user observed)
3. **Liquidity sweep** — wait for the sweep of an obvious stop cluster (e.g. above prior high / below prior low)
4. **CVD divergence** — perp vs spot CVD must support the direction (spot accumulation = buy, spot distribution = sell)
5. **Structural level** — entry at the prior OB / range edge / Fibonacci level

### How he sizes

- **Bootstrap phase**: 20-50x default, scales with conviction, dollar-risk capped ($20-50)
- **Current $200k→$2M**: 2-4% risk per trade, R-based sizing
- **DCA-build account**: low leverage (3.5x), large capital, multi-year hold
- **High-conviction one-offs**: up to 30% port (admitted to risking 800k of 2.7M port on one trade)

### How he exits

- **Default**: 3R fixed TP (per Jan 2026 framework)
- **Manual partial**: scale-out at 1R is allowed
- **Stop**: hard SL defined pre-entry, cannot be moved further away from current price (i.e. can only TIGHTEN, never WIDEN)
- **Drawdown tolerance**: holds through -10% drawdowns on conviction (the 08-14 BTC short was -10.65% before recovering)

### What's evolved across the 2 years

| Element | 2024 bootstrap | 2026 framework |
|---|---|---|
| DCA into losers | **YES (encouraged)** | **NO (banned)** |
| Leverage | 20-125x | 13-40x (lower) |
| Stop discipline | "Sl 20$" dollar-based | % equity-based, mandatory pre-entry |
| Risk tracking | Casual | Daily 8% / Per-trade 2-4% |
| Documentation | Discord-only | Discord + YouTube full video |
| Direction bias | Heavy short on BTC | Scenario-weighted |
| Account scaling | Same-trade DCA-up | Only after +20-25% equity |
| Asset universe | OP-dominant alt | BTC-dominant + ETH + top-10 |

## What text mining revealed (no image reads, 2026-05-20)

Run [analyze_text.py](analyze_text.py) for the full breakdown.

### Time-of-day (UTC)
- **Peak posting 15:00 UTC** (187 posts) and 17:00 UTC (179)
- Pre-NY 12:00 UTC: 145 — setting up before US open
- His "14:30 CET clockwork" reference = 12:30-13:30 UTC (high-activity slot)
- Sleep window: 03:00-09:00 UTC (~5-11 AM CET)

### Day-of-week
Mon/Tue/Wed dominant (423/402/314), Fri/Sat light. Matches his stated "midweek
reversal" obsession.

### SMC/CVD/OB vocabulary first appears 2025-Q4
Terms like "orderblock", "CVD", "NY open", "conviction" are **absent from his
text in 2024** — they appear only from 2025-Q4 onwards (when the codified
framework crystallized). The bootstrap-era charts had OB indicators visually,
but he didn't *name* the concepts in writing until phase C.

### Posting cadence reveals the offline period
- 2024-Q2/Q3: 1-2/day (bootstrap)
- 2025-Q2: 0.2/day; **2025-Q3: 0.1/day** ← effectively offline (the "$8k → $18k → $0k" blowup period)
- 2025-Q4: 4.4/day → **2026-Q1: 7.9/day** ← peak activity, $200k→$2M challenge launched

### He doesn't talk in R
Only 6 of 1,981 posts use "R" / "RR" language. He uses **% (154 posts)** and **$ (13)**.
When we backtest, R is OUR calculation from (entry-stop), not extracted from his text.

### Phase A SL was MENTAL, Phase C SL is PLATFORM-LEVEL
In phase-1 mobile-card extractions, the SL field was null in most cases —
he wrote "Sl manual" in captions. Consistent with "I hold till the damn end"
through a −10.65% drawdown that a hard platform stop would have closed.

The 2026 framework explicit reverses this: **SL Mandatory, cannot be moved
further away**. By Nov 2025 (Bitunix screenshot), the position card explicitly
shows "SL/TP (1/1)" indicator — both stop and target defined.

## What chart-era image reads revealed (5 images sampled 2026-05-20)

Sampled from `chart_era_queue.json` (108 total high-value chart-era images).

### Tight SLs on explicit-text trades

Example: 2025-12-07 BTCUSDT short — *"Shorted BTC here, tp 87400 sl 91961"*

| Field | Value | Distance |
|---|---|---|
| Entry | 91,230.80 | — |
| **SL** | 91,961.00 | **+0.80% above entry** |
| **TP** | 87,400.00 | −4.20% below entry |
| Implied R:R | — | **5.25** (well above his 1:3 minimum) |
| Leverage | **25x cross** | 20% margin risk per 0.8% adverse move |
| Position size | 9.665 BTC ≈ $881k | Big-account swing trade |

So **SLs are tight (sub-1%)** when explicitly set in the chart era. He pairs
that with deep TPs at major structural levels (multi-% away), giving him
the asymmetric R:R the framework requires.

### Pre-staging behavior — articulates plan BEFORE entry

2025-12-11 17:58 caption: *"my plan, will enter short if we peak some"*

He posts the chart with the projected zigzag path **before** the trade fires,
conditional on a price peak forming. The TradingView chart shows a multi-leg
projection: from current $89,757 → projected peak $91,500 → series of lower
highs / lower lows down to ~$83k target.

This pre-staging means **his community can replicate the trade BEFORE he
fires it.** It also means the entry trigger is a specific event (the peak
forming) rather than a passive level touch.

### Rejection confirmation, not blind level entries

2025-12-12 07:23 caption: *"Massive overshoot into the next OB, need to
observe here if it rejects, current PA surprised me slightly"*

He **doesn't enter on the OB touch alone** — he waits for rejection to
confirm. Specifically he says "need to observe here if it rejects" — i.e.
needs candlestick / price-action confirmation that the OB is holding.

This is a critical entry-trigger detail: **OB level + rejection candle**,
not just OB touch. Likely a bearish engulfing / wick / shooting-star on
the 5m or 15m at the OB top.

### Multi-leg zigzag projections — not single targets

The 2025-12-11 and 2025-12-12 charts both show 3-4 leg zigzag paths
projected down to the target. He's not predicting a straight-line move —
he expects bounces along the way.

This **explains his "scaling" behavior** (item #11, 2025-11-28 caption
*"Scaling short"*): each leg of the zigzag is a potential scale-in
opportunity. He builds the position across the entire projected path.

### 5m / 15m chart-era timeframe

All 3 chart-era reads used Bybit 5m or 15m as the chart timeframe.
Consistent with the $200k→$2M rules saying "15m – 4H". Lower TFs than
the bootstrap era's 4H analysis charts.

### Both 25x and 15x BTC shorts seen — leverage varies by setup
- 2025-12-07: 25x cross (Bybit-style card, $881k position)
- 2025-11-28: 15x cross (Bitunix card, $440k position)

The framework allows 2-4% risk per trade — at a tight 0.8% SL, 25x
leverage is ~20% margin per trade, which exceeds the 4% risk rule.
So either:
- The 25x trade was higher-conviction (the rules allow flexing within reason)
- Risk was capped via partial position size, not just leverage
- The 0.8% SL paired with 25x is effectively a 4% margin risk if he's
  only deploying 20% of total equity into the position

We need more position-card reads to confirm risk-sizing math.

## What we still don't know (next-session priority order)

| Priority | Question | What it needs |
|---|---|---|
| 1 | **Entry trigger details** — what candlestick pattern confirms "rejection"? | Read ~15 chart-era entries paying attention to the bar that fired the entry |
| 2 | **SL distance distribution** — is 0.8% typical or was that an outlier? | Extract SL field from ~30 chart-era cards; histogram entry-SL distance |
| 3 | **Risk-sizing math** — how does he reconcile 25x leverage with 4% max risk? | Cross-reference position size vs Free Margin (account equity) per card |
| 4 | **Asset rotation** — when does he pick BTC vs ETH vs alt? | Survey the chart-era set; tag each by asset |
| 5 | **Drawdown management** — when "hold" vs cut? | Follow lifecycle clusters that have known closing screenshots |

## Multi-session image-read plan

The chart-era queue has **108 high-value cluster-first images**. At ~3 minutes
per read this is ~5 hours of conversation budget. Realistic plan:

| Session | Scope | Output |
|---|---|---|
| ✓ Done | 5 chart-era reads | Entry-trigger insights above |
| Next | 15-20 chart-era reads | SL-distance distribution, multi-asset coverage |
| +1 | 15-20 chart-era reads | Asset-rotation logic, drawdown clusters |
| +2 | 15-20 chart-era reads | Setup-pattern catalog (rejection types, target sizing) |
| Final | Synthesis | Strategy spec ready for bot translation |

After ~70-80 high-value chart-era reads (covering ~75% of the queue) we
should have enough setup pattern coverage to draft a deployable spec.

## How this changes our research plan

The MTF cell-map work we did in [`swing_base_limit_bid/discovery.ipynb`](../swing_base_limit_bid/discovery.ipynb) was actually validated by his explicit text framework:
- He uses 15m-4H — same as our detector's resolution
- He targets 1:3 R:R minimum — same as our staged 3R T1
- He looks at multi-TF OB alignment — same as our `mtf_sig` signature
- He requires the higher-TF to be in his direction → strongly aligned with our finding that `mtf_net = +1` (long-side) and `mtf_net = -3..-5` (short-side) were the profitable cells

**The 75,485 trade we started with sits squarely in his current framework**:
- 15m – 4H timeframe ✓ (our detector ran on 15m bars)
- Limit-bid below current price ✓
- BTC (in-universe) ✓
- Structural level (prior weekly swing low + HVN edge) ✓
- 1:3 R:R achievable (75,485 entry, stop ~74,500, target ~78,000+) ✓
- Multi-TF alignment: `--+++` per our detector (M-W- D+ 4h+ 1h+ = capitulation-bounce signature) — the trader's stated framework: counter-trend lower-TF reversal in HTF bear

We've been on the right track — we just hadn't read the text evidence to confirm it.

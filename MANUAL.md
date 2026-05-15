# P-300 Manual Execution Guide

> **Can I run P-300 by hand without the bot?** Yes — but it's a real
> commitment. About **45 minutes a day plus 4-6 timed alerts**. Every
> calculation is simple arithmetic; the limiting factor is consistency,
> not complexity. The ML gate's absence (we replaced it with a simple
> volatility rule) is what makes this actually practical.

This manual is for someone who wants to execute the P-300 Aggressive 2.0
strategy on a real exchange (Binance, MEXC, etc.) without running the
software bot. Read [README.md](README.md) for strategy intent and [Core
J+ explanation](#) before this.

---

## Contents

1. [Feasibility & honest expectations](#1-feasibility--honest-expectations)
2. [Tools you need](#2-tools-you-need)
3. [The spreadsheet](#3-the-spreadsheet)
4. [Daily routine](#4-daily-routine)
5. [Intraday alerts schedule](#5-intraday-alerts-schedule)
6. [Per-sleeve decision rules](#6-per-sleeve-decision-rules)
7. [Position bookkeeping](#7-position-bookkeeping)
8. [Simplified "P-300 Lite"](#8-simplified-p-300-lite-for-the-time-constrained)
9. [Honest manual-vs-bot trade-offs](#9-manual-vs-bot-honest-trade-offs)
10. [Common errors & gotchas](#10-common-errors--gotchas)

---

## 1. Feasibility & honest expectations

| Aspect | Assessment |
|---|---|
| Daily time commitment | 30-45 min routine + 4-6 timed alerts on trading days |
| Required skills | Spreadsheet basics; TradingView basics; consistent execution |
| Hardest single task | Computing 30d realized vol percentile rank weekly. Spreadsheet handles it. |
| What ML gate would have required | Running a Python ML model daily — incompatible with "manual" |
| What our rule-based gate requires | One formula: `IS 30d-vol AT-OR-ABOVE 75th percentile of trailing 365d?` |
| Signals you can't easily replicate manually | None at full fidelity. Some lose intraday precision (PDO touch detection by hand is approximate; bot checks every hour). |

**Bottom line.** This is more like running a small farm than running a
machine — repetitive, precise, schedule-driven. If you can't commit to
the routine for 6+ months, **don't start manually** — the strategy's
edge is statistical and only manifests over hundreds of trading days.

---

## 2. Tools you need

| Tool | Purpose | Cost |
|---|---|---|
| **TradingView** (Free or Essential) | Daily/weekly charts, BTC/ETH OHLC, EMAs, ADX | Free works; alerts limited |
| **Coinglass** or **Coinalyze** | BTC/ETH funding rate history, long-short ratio | Free tier OK |
| **Spreadsheet** (Excel/Google Sheets) | All the calculations + bookkeeping | Free |
| **Phone alarms / Calendar** | Intraday entry/exit windows | Free |
| **Exchange UI** (Binance/MEXC) | Order entry, position tracking, fills | Free |
| **Economic calendar** (e.g. ForexFactory, TradingEconomics) | CPI, NFP, OPEX dates for V4 filter | Free |

**Critical:** all times in this manual are **UTC**. Bookmark a UTC
clock (e.g. timeanddate.com/worldclock/timezone/utc) on your phone.

---

## 3. The spreadsheet

Build one spreadsheet with the following sheets. The compute formulas
are all standard.

### Sheet 1: `Daily BTC` (one row per day)

| Column | Formula / source | Example |
|---|---|---|
| Date (UTC) | manual entry, ISO YYYY-MM-DD | 2026-04-15 |
| BTC close | TradingView daily close | 78,450 |
| BTC open | TradingView daily open | 77,200 |
| 1d return % | `=(close - prev_close) / prev_close * 100` | +1.62 |
| EMA(20) | `=A·close + (1-A)·prev_EMA20` where A=2/21 | 75,800 |
| EMA(50) | A=2/51 | 73,100 |
| 30d log-return std × √365 (= ann. vol %) | std of last 30 log-rets × √365 | 48.3 |
| 30d vol pct rank vs trailing 365d | `=COUNTIF(prior 365 vol, "<=" current vol) / 365` | 0.71 |
| Gate fired? | `=IF(rank ≥ 0.75, TRUE, FALSE)` (uses **YESTERDAY's** rank) | FALSE |
| 7d momentum | `=(close - close_7d_ago) / close_7d_ago` | +0.04 |
| 30d momentum | `=(close - close_30d_ago) / close_30d_ago` | +0.12 |
| Above EMA20? | `=close > EMA20` | TRUE |
| Above EMA50? | `=close > EMA50` | TRUE |
| Regime (raw) | nested IFs (see [§6.1](#61-the-regime-classifier)) | strong_bull |
| Regime (used today) | `=YESTERDAY's regime` (T-1 always) | strong_bull |

### Sheet 2: `Daily ETH` (one row per day)

Same as BTC but only need: date, ETH close, 1d return %.
The other indicators aren't used by P-300 for ETH.

### Sheet 3: `Daily Funding` (one row per day)

| Column | Formula / source | Example |
|---|---|---|
| Date | UTC | 2026-04-15 |
| BTC funding 00:00 | Coinglass / Binance | 0.0085% |
| BTC funding 08:00 | Coinglass | 0.0095% |
| BTC funding 16:00 | Coinglass | 0.0072% |
| BTC daily total | sum of 3 | 0.0252% |
| 7d avg | avg of last 7 daily totals | 0.0220% |
| ETH funding 00 / 08 / 16 | same |
| ETH daily total | sum |
| LS ratio (BTC) | Coinalyze "Long%" daily | 51.2 |
| LS shift 7d | `=current - 7d_ago` | -3.4 |

### Sheet 4: `Sleeve State` (one row per sleeve per day)

| Sleeve | Mode | Status | Entry $ | Size $ | Direction | Notes |
|---|---|---|---:|---:|---|---|
| EMA_BTC | LONG | open | 75,200 | $5,000 | +1 | weekly cross 2024-12 |
| ADX | — | flat | | | | last exit 2025-02-08 |
| CARRY | — | open | 78,450 | $4,000 | delta-neutral | entry 2026-04-10 |
| ... | | | | | | |

### Sheet 5: `Allocation Plan` (today's targets)

This is what tells you what to actually trade today. See
[§7](#7-position-bookkeeping).

### Sheet 6: `Trade Log` (every fill)

| Date | Time UTC | Sleeve | Asset | Side | Price | Size $ | Reason |
|---|---|---|---|---|---:|---:|---|
| 2026-04-15 | 06:00 | R4_BTC | BTC | BUY | 78,450 | 1,200 | Mon wk1 entry |
| 2026-04-15 | 18:00 | R4_BTC | BTC | SELL | 79,100 | 1,210 | scheduled exit |

This is your audit trail and your tax record.

---

## 4. Daily routine

### Morning (~20 min, before 06:00 UTC if you'll trade R4 BTC)

1. **Update Sheet 1 (BTC) with yesterday's close.** Roll forward EMAs, vol, percentile rank.
2. **Update Sheet 2 (ETH).**
3. **Update Sheet 3 (funding & LS) for yesterday.**
4. **Read off today's regime** from Sheet 1 (using YESTERDAY's row's
   regime — strict T−1 rule).
5. **Read off today's gate-fired status** from Sheet 1.
6. **Compute today's allocation table** (Sheet 5) using the per-regime
   weights from [§6.5](#65-per-regime-weights--combine-everything).
7. **Identify which sleeves fire today**:
   - Is today Mon of weeks 1-2? → R4 BTC V1 fires (06:00 entry, 18:00 exit)
   - Is today Tue of week 1-2 (next-day Wed wk1-2)? → R4 ETH V1 preps
   - Is today Wed or Fri of weeks 1-2? → R4 BTC V2 + R4 ETH V2 fire (04:00 entry, 14:00 exit)
   - Is today Thursday + V4 conditions met? → THU_BEAR fires
   - Are you holding open trades from prior days? → check stops/exits
8. **Cross-reference the economic calendar** for any CPI/NFP/OPEX that
   affects V4 today.

### Intraday (timed alarms — see [§5](#5-intraday-alerts-schedule))

Execute or close per the rules.

### Evening (~10 min, after daily close ~24:00 UTC)

1. **Record fills in Sheet 6.**
2. **Update Sheet 4 (Sleeve State)** for any opens/closes today.
3. **Check daily-cadence sleeves' exit signals** (ADX < 20, CARRY 3-day
   neg streak, CPR target/stop, EMA cross on weekly close if it's a
   week-end).
4. **Plan tomorrow's intraday alarms.**

### Weekly (~30 min, Sunday)

1. **Compute weekly OHLC for BTC** (Mon-Sun aggregation).
2. **Update EMA(5) and EMA(21) on the weekly series.**
3. **Check for cross-up / cross-down** — if either, **EMA_BTC sleeve
   flips position next Monday's open** (entry at start of new week).

---

## 5. Intraday alerts schedule

Set these as **phone alarms with sound**. UTC times.

| Alarm | When | Action |
|---|---|---|
| Mon 06:00 UTC | weeks 1-2 only (day-of-month ≤ 14) | R4 BTC V1 entry check + execute |
| Mon 18:00 UTC | same days | R4 BTC V1 scheduled exit |
| Tue 20:00 UTC | weeks 1-2 | R4 ETH V1 entry check + execute |
| Wed 20:00 UTC | weeks 1-2 | R4 ETH V1 scheduled exit |
| Wed+Fri 04:00 UTC | weeks 1-2 | R4 BTC V2 + R4 ETH V2 entry check + execute |
| Wed+Fri 14:00 UTC | same days | R4 BTC V2 + R4 ETH V2 scheduled exit |
| Thu 00:00 UTC | every Thursday | THU_BEAR entry check (regime + V4) |
| Fri 01:00 UTC | every Friday with open SHORTs | THU_BEAR scheduled exit (matches Pine `process_orders_on_close` fill) |
| Daily 23:00 UTC | every day | Stop-loss check on open positions |

### What "entry check" actually means

You don't open blindly at the alarm. You verify:

- For R4: regime is in one of {strong_bull, mild_bull, uncertain}, and
  you have allocation room.
- For THU_BEAR: yesterday's regime ∈ {bear, sell_off, chop} AND today
  is within ±1 day of CPI or NFP AND not within ±1 day of OPEX.

If conditions fail, don't trade. Log the skip in Sheet 6 with reason.

---

## 6. Per-sleeve decision rules

### 6.1 The regime classifier

Computed daily from BTC daily closes. Use **yesterday's** values to
classify today.

```
Inputs (all from yesterday's close):
  CLOSE     = yesterday's BTC daily close
  EMA20     = exponential moving avg (20 days) of daily closes
  EMA50     = exponential moving avg (50 days) of daily closes
  M30       = (CLOSE / close_30d_ago) - 1
  M7        = (CLOSE / close_7d_ago) - 1
  PEAK      = max BTC close from 2020-01-01 onward
  LS_now    = BTC long% today
  LS_7d_ago = BTC long% 7 days ago

Classify:
  IF (CLOSE > EMA50) AND (CLOSE > EMA20) AND (M30 > 0) AND (M7 > 0):
       → strong_bull
  ELIF (CLOSE > EMA50) AND (M30 > 0 OR CLOSE > EMA20):
       → mild_bull
  ELIF (CLOSE < EMA50) AND (M30 < 0):
       → bear
  ELSE:
       → uncertain

Override 1 (peak drawdown): if (PEAK − CLOSE) / PEAK > 5% AND classified
  as strong_bull or mild_bull, demote to uncertain.

Override 2 (LS circuit breaker): if (LS_now − LS_7d_ago) < −15, force
  "uncertain" for the next 7 calendar days.
```

In the spreadsheet, this is one nested-IF formula plus two override
columns.

### 6.2 The gate (replaces ML gate)

```
Inputs (all from yesterday's row):
  VOL_30D    = std(last 30 log-returns) × √365, in percent
  VOL_RANK   = (count of last 365 days where 30d vol ≤ VOL_30D) / 365

Gate fires (de-lever R4 sleeves 2.5× → 1×) if:
  VOL_RANK ≥ 0.75
```

When the gate fires, R4 BTC and R4 ETH trade at 1× size. Otherwise at
2.5× size. (See sizing in [§7](#7-position-bookkeeping).)

### 6.3 Vol-target leverage

Daily, after computing the unleveraged "1× return" for the strategy,
choose today's leverage multiplier:

```
Inputs:
  RECENT_1X     = list of last 30 days of unleveraged strategy returns (%)
  REALIZED_VOL  = std(RECENT_1X) × √365
  MODE          = today's regime

Per-regime caps:
  strong_bull → 3.0
  mild_bull   → 2.5
  uncertain   → 2.0
  bear        → 1.5

Leverage:
  IF len(RECENT_1X) < 30:
    LEV = min(1.0, cap)
  ELIF REALIZED_VOL ≤ 0:
    LEV = cap
  ELSE:
    LEV = MIN(cap, MAX(0.5, 50.0 / REALIZED_VOL))
```

In practice: when markets are calm (vol < 50%), you lever up toward the
cap. When vol > 100%, you de-lever toward the 0.5× floor.

This applies to the **whole strategy**, not per-sleeve. So the
sub-sleeves' weighted-sum daily return × LEV = today's portfolio return.

### 6.4 R4 BTC V1 (06:00 → 18:00 UTC, **Mon-only** wk1-2)

```
Conditions to fire today:
  1. Today's date.day ≤ 14  (week 1 or 2)
  2. Today's weekday = Monday
  3. Today's regime ∈ {strong_bull, mild_bull, uncertain}
     (in bear regime, R4 sleeves don't fire — see §6.5)

If all true:
  • At 06:00 UTC: BUY BTC at market (perp or spot — strategy is long-only)
  • At 18:00 UTC: SELL BTC at market
  • Sizing: see §7 — uses regime-conditional weight × today's LEV × gate factor

If gate fired today: size at 1×. If not: size at 2.5×.

Cost expectation: 10bp round-trip taker fees baked into the math.
```

### 6.5 R4 ETH V1 (Tue 20:00 → Wed 20:00 UTC, wk1-2)

```
Conditions:
  1. Today is Tuesday and tomorrow's date.day ≤ 14
  2. Today's regime allows ETH leg

Execution:
  • Tue 20:00 UTC: BUY ETH at market
  • Wed 20:00 UTC: SELL ETH at market
```

Same gate / leverage logic as R4 BTC V1.

### 6.5b R4 BTC V2 + R4 ETH V2 (Wed+Fri wk1-2 04→14 UTC, added 2026-05-08)

The Mon+Wed R4_BTC original was split: Wednesdays moved to a new
`R4_BTC_V2` window (04→14 UTC) along with Fridays — the
[r4_study](studies/notebooks/r4_study/findings.md) found the Wed+Fri 04→14 cell
positive across all eras (pre-Binance-perp, Binance-perp, post-ETF),
unlike the post-ETF-emergent Mon 06→18 V1 cell.

The same Wed+Fri 04→14 window applies to ETH as `R4_ETH_V2` —
cross-asset bonus from the BTC study.

```
R4 BTC V2 — Wed+Fri wk1-2 04→14 UTC:
  Conditions:
    1. Today's date.day ≤ 14
    2. Today is Wed or Fri
    3. Today's regime ∈ {strong_bull, mild_bull, uncertain}
  Execution:
    • 04:00 UTC: BUY BTC at market
    • 14:00 UTC: SELL BTC at market

R4 ETH V2 — same calendar/window, on ETH instead of BTC.
```

V2 sleeves use the same gate / leverage stack as V1 (see [§6.3](#63-vol-target-leverage)).
Per-regime weights for V2 are half the V1 weights (see §6.7) — the
intent is that adding V2 keeps peak Wed concurrent exposure
comparable to the pre-2026-05-08 baseline (when V1 fired on
Mon AND Wed).

### 6.6 EMA_BTC (weekly 5/21 crossover)

```
Process WEEKLY (e.g. Sunday evening, after the week's close):
  1. Aggregate BTC daily closes Mon-Sun into a weekly close.
  2. Compute EMA(5) and EMA(21) on weekly closes.
  3. Detect cross:
     - EMA(5) crosses ABOVE EMA(21) → enter LONG next Monday open
     - EMA(5) crosses BELOW EMA(21) → flip from LONG to SHORT next Monday open
     (or: from flat to SHORT for first cross-down)
  4. Hold position throughout the week.

Position contributes to portfolio: weight × position_sign × BTC daily return.
```

This is the "anchor" — it holds for weeks at a time. Most days you do
nothing on this sleeve.

### 6.7 Per-regime weights — combine everything

Each day, the **unleveraged 1× return** of Core J+ is the weighted sum
of contributions from six sub-sleeves:

**Raw weights** (as stored in `REGIME_WEIGHTS_FULL`):

| Regime | EMA_BTC | ETH_daily | R4_ETH | R4_BTC | R4_BTC_V2 | R4_ETH_V2 | Sum |
|---|---:|---:|---:|---:|---:|---:|---:|
| strong_bull | 0.50 | 0.20 | 0.15 | 0.15 | 0.075 | 0.075 | 1.15 |
| mild_bull   | 0.30 | 0.10 | 0.30 | 0.20 | 0.10  | 0.15  | 1.15 |
| uncertain   | 0.30 | 0.00 | 0.40 | 0.30 | 0.15  | 0.20  | 1.35 |
| bear        | 0.30 | 0.00 | 0.00 | 0.00 | 0.00  | 0.00  | 0.30 |

Where:
- `EMA_BTC contribution` = `ema_position × BTC_daily_return` (ema_position is +1 LONG / −1 SHORT / 0 flat from the weekly EMA(5)/EMA(21) cross)
- `ETH_daily contribution` = today's ETH daily close-to-close return
- `R4_ETH` = (Wed 20:00 open − Tue 20:00 open) / Tue 20:00 open − 10bp, only on R4_ETH V1 days
- `R4_BTC` = (18:00 open − 06:00 open) / 06:00 open − 10bp, only on R4_BTC V1 days (Mondays wk1-2)
- `R4_BTC_V2` = (14:00 open − 04:00 open) / 04:00 open − 10bp, only on Wed+Fri wk1-2
- `R4_ETH_V2` = same window as V2_BTC, on ETH
- All four R4 contributions are **multiplied by the gate factor** (2.5× normally; 1.0× when the vol-percentile gate has fired)

> **CORE_ALLOC_CAP = 0.50 (since 2026-05-12).** The raw rows above sum to
> >1.0 in every non-bear regime. To enforce the Core/Tactical 50/50 cap,
> `jplus.simulate._cap_core_weights()` rescales every row whose raw sum
> > 0.50 by `0.50 / raw_sum`, preserving relative weighting. After
> capping: strong_bull/mild_bull/uncertain all sum to **0.500**; bear is
> unchanged at 0.30. The capped values are what `today_inputs()`
> returns and what each Core sub-sleeve sizes its live trade against.

Then multiply the unleveraged weighted sum by today's `LEV` (from
[§6.3](#63-vol-target-leverage)). That's the **Core J+ daily return**.

### 6.8 Tactical sleeves

The 6 tactical sleeves work alongside Core J+, not inside it. Each is
its own discrete trade:

#### S-003 ADX (15% weight, k=5×)

```
Inputs:
  ADX(14) and EMA(50) on BTC daily candles

Entry signal (open trade):
  • ADX dropped below 20 in the last 20 days, AND
  • ADX today ≥ 25, AND
  • Direction = long if BTC close > EMA(50), short if below

Exit signals (close trade):
  • ADX < 20 (trend dies)
  • Direction flips (close + reverse)
  • Stop loss: 10% adverse price move from entry

Size: 15% × capital × 5× = 75% of capital notional, BTC perp.
```

#### S-078 Carry (8%, k=5×)

```
Inputs:
  Daily BTC funding rate (sum of 3 settlements)
  7-day rolling avg of daily funding

Entry: 7d avg > 0
Exit: 3 consecutive days of negative daily funding

Structure: long BTC spot + short BTC perp (delta-neutral).
P&L: short perp earns funding when rate > 0.

Size: 8% × capital × 5× = 40% notional each leg.
```

In manual practice, you can SKIP this sleeve if you don't want to
manage a hedged spot+perp pair. You'd lose ~13% per year of
contribution per our backtest. That's significant; consider keeping it.

#### S-096 V4 Thu Bear (6%, k=5×)

**NOTE:** Thu Bear uses a DIFFERENT regime classifier than Core J+ (§6.1).
The J+ classifier produces `strong_bull/mild_bull/uncertain/bear`. Thu Bear
uses `regime_classifier.py` which produces `bull_trend/bear_trend/chop/sell_off`
based on 50d SMA slope and volatility (not EMAs/momentum). You need a
second classification in your spreadsheet for this sleeve:

```
Thu Bear regime (separate from §6.1 J+ regime):
  SMA50_slope = (SMA50_today − SMA50_10d_ago) / close, normalized to %
  RV_rank     = 30d realized vol percentile vs trailing 365d

  IF |SMA50_slope| <= 0.5%:       → chop
  ELIF RV_rank >= 0.75 AND close < SMA50 AND slope < 0: → sell_off
  ELIF SMA50_slope > +0.5%:       → bull_trend
  ELIF SMA50_slope < -0.5%:       → bear_trend
```

```
Conditions to fire:
  1. Today is Thursday
  2. Yesterday's Thu Bear regime ∈ {bear_trend, sell_off, chop}
     (NOT the J+ regime from §6.1 — see classification above)
  3. Today is within ±1 day of a CPI or NFP release
     (look up at https://www.forexfactory.com/calendar)
  4. Today is NOT within ±1 day of an OPEX expiry (3rd Friday of month)

Execution:
  • 00:00 UTC Thursday: SHORT BTC perp + SHORT ETH perp (split alloc)
  • 01:00 UTC Friday: cover both (matches Pine reference; was 23:00 Thu prior to 2026-05-04)
  • Stop loss: 5% adverse move (price up 5% on the short side)

Size: 6% × capital × 5× = 30% notional, split 50/50 across BTC and ETH.
```

#### PDO-L-RF (9%, k=1×)

```
Conditions:
  1. BTC 30d trailing return ≥ -10% (regime filter)
  2. Today's open ≥ 102% of yesterday's open (gap up ≥ 2%)
  3. During the day, intraday price touches yesterday's open
     (within ±0.1% tolerance)

Execution:
  • Enter LONG when price first touches PDO level
  • Exit at min(end of UTC day, entry_time + 24h for BTC / 4h for ETH)

Size: 9% × capital × 1× = 9% notional, split BTC/ETH.
(Trimmed from 11% to 9% on 2026-05-12 to bring tactical total to the 50% cap.)

Manual challenge: detecting the touch live. You'd need price alerts
at the PDO level and check throughout the day. Easier: set a limit-buy
order at the PDO level, valid through end of day. If filled, place the
exit order timed for end of UTC day.
```

#### CPR (5%, k=1×)

```
Conditions (all must be true):
  1. Funding 3d avg ≤ 20th percentile of trailing 180d
  2. BTC long-short ratio ≤ 20th percentile of trailing 180d
  3. BTC close > EMA(20)
  4. EMA(20) > EMA(50)

Execution:
  • Enter LONG at next UTC-day open
  • Target: today's BB upper (Bollinger 20, 2σ) — fixed price level
  • Stop: 5% below entry
  • Time stop: 15 calendar days (close at market)

Size: 5% × capital × 1×.
```

CPR fires rarely (~5x per year). Most days do nothing on this sleeve.
The two percentile checks are the tedious part — your spreadsheet
should compute them automatically.

#### FOMC (5%, k=10×)

```
Conditions to fire:
  1. Today is an FOMC announcement day (8 per year — look up the Fed calendar)
  2. Filter passes:
     - SKIP if expected action is a 25bp cut (Polymarket/Fed Funds futures)
     - SKIP if Fear & Greed index is in "extreme greed" bucket
     - TRADE if Fear & Greed is "extreme fear" AND phase ≠ mid_hold
     - SKIP if Fed rate phase is "mid_hold"
     - TRADE otherwise

Execution:
  • Enter LONG BTC at T-10h before announcement (~08:00 UTC for 14:00 ET meetings)
  • Exit at T+0.5h after announcement (~14:30 ET / ~18:30 UTC)
  • Stop loss: 5% adverse move

Size: 5% × capital × 10× = 50% notional, BTC perp.
```

FOMC fires only 8 times per year. Check the Fed meeting calendar
in January and mark all 8 dates. The filter requires checking
Polymarket (or Fed Funds futures) for implied rate path AND the
Fear & Greed index the morning of. If you can't check both,
default to TRADE (the unfiltered backtest was still positive).

---

## 7. Position bookkeeping

The hardest non-signal part of manual P-300 is keeping track of how
much capital is in each sleeve and ensuring you don't double-up.

### Capital allocation example with $100,000

```
P-300 weights × today's leverage × today's gate

Starting: $100,000

Core J+ sub-sleeves (each emits its own discrete trades, sized
per-tick from today_inputs.weights × capital × inner_lev × LEV):
  • EMA_BTC                  = $100K × ema_btc_weight × LEV × position
  • ETH daily (regime-gated) = $100K × eth_daily_weight × LEV
  • R4 BTC V1   (Mon)        = $100K × r4_btc_weight    × inner_lev × LEV
  • R4 ETH V1   (Tue→Wed)    = $100K × r4_eth_weight    × inner_lev × LEV
  • R4 BTC V2   (Wed+Fri)    = $100K × r4_btc_v2_weight × inner_lev × LEV
  • R4 ETH V2   (Wed+Fri)    = $100K × r4_eth_v2_weight × inner_lev × LEV
  (inner_lev = 2.5 normally, 1.0 when vol-gate fires; weights from §6.7)

Tactical (50% total, inclusive of AI_QUANT):
  • S-003 ADX                = $100K × 0.15 × 5  = $75K notional   (BTC long or short)
  • S-078 Carry              = $100K × 0.08 × 5  = $40K each leg   (BTC spot/perp delta-neutral)
  • S-096 V4 Thu Bear        = $100K × 0.06 × 5  = $30K split 50/50 (BTC+ETH SHORT)
  • PDO-L-RF                 = $100K × 0.09 × 1  = $9K split 50/50 (BTC+ETH LONG)
  • CPR                      = $100K × 0.05 × 1  = $5K split 50/50 (BTC+ETH LONG)
  • FOMC                     = $100K × 0.05 × 10 = $50K notional   (BTC LONG, 8 days/year)
  • AI_QUANT (default-OFF)   = $100K × 0.02 × conviction/100 × 3 (BTC LONG/SHORT, daily)
                                                                  (inside the 50% cap since 2026-05-12)
```

The 2026-05-10 live/sim refactor made Core sub-sleeves operate exactly
like tactical sleeves — each one opens its own discrete trade at the
signal moment with the live (or simulated) market price. The
simulator-driven daily-return accrual (which used to replace per-trade
emission with a single "yesterday's return" row) was removed.

### Cross-sleeve BTC long cap

Important constraint: combined BTC LONG allocation across PDO + CPR
must not exceed **15% of capital pre-leverage**. PDO has 9% × half-on-
BTC = 4.5%; CPR has 5% × half-on-BTC = 2.5%. Combined 7% — under the
cap, so usually fine. But if the bot's already in CPR BTC and PDO
fires, manually verify you're not above 15% combined BTC exposure.

### When two sleeves want the same direction at the same time

This happens occasionally (e.g. PDO and CPR both LONG BTC). You hold
both as separate positions; they each have their own entry, stop,
target, and exit time.

### When two sleeves disagree (e.g., S-003 short while EMA_BTC long)

You hold both. They're separate sleeves with their own bookkeeping.
The portfolio's net BTC exposure can be small or zero in such cases —
that's by design (the Aggressive 2.0 mechanism is a multi-sleeve diversifier).

---

## 8. Simplified "P-300 Lite" for the time-constrained

If 45 minutes daily isn't realistic, here's the minimum-viable subset
that captures most of the strategy's character:

### Lite version: Core J+ only (50% capital)

Drop the 6 tactical sleeves entirely. Keep:
- EMA_BTC (weekly check, ~5 min on Sunday)
- R4 BTC V1 (Mon wk1-2 alarms — 06:00 / 18:00 UTC)
- R4 BTC V2 (Wed+Fri wk1-2 alarms — 04:00 / 14:00 UTC) — optional;
  if skipping, drop the 0.075-0.15 V2 weight from the regime table
- Regime classification (15 min daily updating spreadsheet)
- Gate (one column in spreadsheet)
- Vol-target leverage (one column)

You'd skip: R4 ETH (V1 and V2), ETH continuous (just don't include
the 0.10/0.20 ETH weight), THU_BEAR, ADX, CARRY, PDO, CPR, FOMC,
AI_QUANT.

**Time:** ~15-20 min daily routine + 2-6 alarms per week
(Mon 06/18 V1; optionally Wed+Fri 04/14 V2; weeks 1-2 only).

**Expected lite Sharpe:** lower than full P-300 (no diversification
from tactical), maybe 1.0-1.4 vs combined's 1.7. MDD likely ~−25% vs combined's −16%.

### Lite-er: Tactical-only

Drop Core J+ entirely. Run only S-003 ADX + S-078 Carry + S-096 V4 + PDO + CPR + FOMC.

**Time:** still ~30 min daily because there's a lot of bookkeeping. But
no R4 alarms (R4 lives in Core).

**Expected lite Sharpe:** about 1.18 (matches our A2 replay). MDD −7.8%.
This is actually **the lowest-MDD configuration** we measured.

### Lite-est: ADX + Carry + EMA_BTC

Three sleeves total. Daily-cadence each.

**Time:** ~10 min daily.

**Expected:** maybe Sharpe 0.8-1.0. Real but unverified — we haven't
backtested this exact subset.

---

## 9. Manual vs bot — honest trade-offs

| Aspect | Bot | Manual |
|---|---|---|
| Decision consistency | Always identical | Subject to mistakes, fatigue |
| Reaction speed | < 1 second | Minutes |
| Intraday touch detection (PDO) | Hourly precision | Approximate; relies on price alerts |
| Stop-loss enforcement | Tick-precise | Daily check at minimum |
| Sleep / vacation | Runs 24/7 | You miss whatever happens while away |
| Tax record | Auto-logged | Manual spreadsheet |
| Cost (operations) | Free; runs on a laptop | Your time |
| Complexity bus factor | Code is the source of truth | Your knowledge is the source of truth |

**My honest recommendation.** If you want to RUN this strategy for
income, run the bot. The bot's marginal cost is near-zero, and human
errors compound over time. If you want to LEARN this strategy or
validate it has real edge before committing capital, run it manually
for 1-3 months on paper (use a spreadsheet "paper portfolio") to
internalize the rhythms.

The manual is also useful as a **fallback procedure** if the bot fails
or you can't access the server. Keep this manual updated alongside the
bot's behavior so they don't diverge.

---

## 10. Common errors & gotchas

1. **Using today's vs yesterday's data for regime.** The classifier
   MUST use yesterday's close, not today's mid-day price. Spreadsheet
   formula must reference `yesterday_row`, not `today_row`. This is the
   #1 mistake — and it's the look-ahead bias we audited at length.

2. **Forgetting the gate multiplier.** R4 sizing depends on whether
   the gate fired. Gate-fired days are 1× size, not 2.5×. Easy to
   forget when manually entering orders.

3. **Trading R4 on day 15+.** Strict day ≤ 14 means days 1-14 only.
   Day 15 (week 3 Mon/Wed) is NOT a trade day.

4. **Forgetting the LS circuit breaker is for 7 calendar days.** Once
   triggered, force-uncertain for 7 days. Mark it on your calendar.

5. **Off-by-one on weekday checks.** UTC weekdays:
   - Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6.
   - R4 BTC V1 on Mon (0). R4 ETH V1 on Tue (1, with Wed exit).
   - R4 BTC V2 + R4 ETH V2 on Wed (2) and Fri (4).
   - THU_BEAR on Thu (3).

6. **Forgetting to drop today's still-forming daily candle.** The
   regime classifier uses YESTERDAY'S close (closed daily candle), not
   "current price right now." This matters — using today's still-
   forming close introduces look-ahead.

7. **Computing realized vol with simple returns instead of log returns.**
   Tiny difference at daily frequency but technically the formula uses
   `log(close_t / close_t-1)`, not `(close_t - close_t-1) / close_t-1`.

8. **Skipping the V4 OPEX exclusion.** V4 fails closed: if you can't
   confirm OPEX exclusion, DON'T trade THU_BEAR that day. OPEX dates
   are 3rd Fridays of each month for monthly options.

9. **Position double-up on direction-flip days.** If S-003 ADX
   reverses long → short, you're closing the long AND opening a short
   in the same action. Don't accidentally hold both simultaneously.

10. **Not logging trades.** Without Sheet 6 you have no audit trail,
    no tax record, and no way to debug if returns drift from
    expectations. Log everything, even skips ("PDO did not touch
    today, no fill").

---

## Appendix: minimum-viable spreadsheet structure

Here's a starter Excel/Google Sheets layout — copy this into your own:

**`Daily BTC` sheet (column-by-column):**

```
A: date (text, ISO)                ←  manual entry
B: BTC close                        ←  manual or =GOOGLEFINANCE
C: BTC open                         ←  manual or =GOOGLEFINANCE
D: 1d return %                      ←  =(B - prev B) / prev B * 100
E: log return                       ←  =LN(B / prev B)
F: EMA20                            ←  =B*2/21 + prev F * (1-2/21)  (seed first row at SMA(20))
G: EMA50                            ←  =B*2/51 + prev G * (1-2/51)
H: 30d ann vol %                    ←  =STDEV(E[T-29:T]) * SQRT(365) * 100
I: vol pct rank vs 365d             ←  =RANK.AVG(H, H[T-364:T-1]) / 365  (uses YESTERDAY's H)
J: gate fired                       ←  =I >= 0.75
K: M30                              ←  =B / B[T-30] - 1
L: M7                               ←  =B / B[T-7] - 1
M: above EMA20                      ←  =B > F
N: above EMA50                      ←  =B > G
O: regime raw                       ←  nested IF (see §6.1)
P: peak so far                      ←  =MAX(B[start:T])
Q: peak DD %                        ←  =(P - B) / P * 100
R: regime override (peak)           ←  =IF(Q > 5 AND O="strong_bull"|"mild_bull", "uncertain", O)
S: LS shift                         ←  funding sheet's value
T: regime override (LS CB)          ←  IF S < -15, set CB until T+7; carry forward
U: regime FINAL today               ←  =T-1's R (we use yesterday's classification)
V: leverage today                   ←  see §6.3
```

**Rinse & repeat structure for `Daily ETH` (smaller; only need close + return).**

**`Trade plan today` cell** prints something like:

```
2026-04-15 (Wed, day 15 → R4 wk3, NO R4 today)
Regime: mild_bull. Gate: NOT fired. Lev: 1.6×.
Sleeves to manage today:
  • EMA_BTC: HOLD long ($25K position)
  • S-003 ADX: HOLD long ($35K, SL at 71,500)
  • S-078 Carry: HOLD ($40K each leg, watch funding)
  • THU_BEAR: not Thursday
  • PDO: BTC 30d return = +4.2%, regime OK; check if today's open ≥ 102% of yesterday's open
  • CPR: percentile checks → no signal today
  • R4 BTC: NO (day = 15, week 3)
  • R4 ETH: NO (not Tuesday)
```

That's your morning briefing in one cell.

---

This manual is meant to be living documentation. If you start running
it manually and discover gotchas not listed here, add them. The bot's
code is in `strategies/` (sleeves under `strategies/sleeves/<name>/`,
shared math + state under `strategies/support/`) if you ever want to
verify a rule against the canonical implementation.

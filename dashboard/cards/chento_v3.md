# How Chento Triple v3 works

**One line:** BTC perp mean-reversion — enter when order-flow, positioning, and
multi-timeframe flow all say the crowd is leaning the wrong way, ride the snap-back
to a wide fixed target.

## What it trades
BTC Binance perp on 15m bars. Long and short. It checks every 15m bar close but
fires only ~10–20 times a year — silence is normal.

## Entry logic — the Triple composite
A trade needs **three independent signals agreeing on direction** inside a trailing
24h window, anchored on B1 (the freshest B1 bar is the entry bar):

- **B1 — money-flow divergence.** 30-day CVD z-score is stretched (|z| > 0.5) while
  1h price velocity is still quiet (|z| < 1.0): flow is moving, price hasn't reacted yet.
- **B5 — positioning extreme.** The long/short ratio sits in the bottom decile of its
  30-day range — the crowd is squeezed to one side.
- **B7 — multi-TF flow alignment.** CVD z-scores across 1h/4h/1d/3d all share the
  sign with median |z| > 2 — the flow pressure is broad, not one-timeframe noise.

A 6h cooldown separates triggers so one confluence can't emit duplicate trades.

## Filters (any one can veto the entry)
1. **No-tilt** — after a losing trade, skip the next signal (BTC leg only).
2. **No opposite order-block within 2R** (5-bar pivot SMC) — don't fade into a wall.
3. **OKX alignment** — the OKX–Binance perp delta z-score must agree with trade
   direction (cross-exchange confirmation).
4. **Regime, asymmetric** — skip **shorts** when BTC's 30d return is above +10%
   (don't short a running bull; longs unaffected).

## Exits & sizing
Initial stop **5×ATR(14)**, fixed target **6R**, time-in-force **72h** — whichever
comes first. Counter-intuitive but validated: the wide 6R target and the 72h hold
both beat tighter versions. Sizing is fixed-R: **2% of capital risked per trade**,
notional capped at 3× capital. Ladder adds are **disabled** (failed the
backward-only lookahead audit).

## What "no trade today" looks like
The diag counters below show which gate ate the day: `no_triple` (signals never
aligned), `b1_none`/`b5_none`/`b7_none` (a leg missing), `filter_*` (triple fired,
a veto killed it — those appear as near-misses), `cooldown_blocked`.

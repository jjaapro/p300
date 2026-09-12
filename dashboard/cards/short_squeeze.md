# How Short Squeeze works

**One line:** when Asia builds a crowded short and London/NY sweeps the low but
buyers absorb it, go long for the squeeze.

## What it trades
BTC, long only, 15m bars, **only during London/NY (07–21 UTC)**. Six gates must all
pass on the same bar:

1. **Session** — 07:00–21:00 UTC.
2. **Asia macro setup** — during the prior Asia session: open interest rose ≥ +0.5%,
   mean funding negative, Asia closed below its open. Shorts piled in and are
   underwater ⇒ squeeze fuel.
3. **Sweep** — the bar takes out the low of the prior 6h (24×15m bars).
4. **Flow percentiles** (rolling 90d, session-filtered distributions): perp CVD in
   the bottom 15% (aggressive perp selling) while spot−perp divergence is in the
   top 30% (spot is absorbing what perps dump).
5. **Reversal** — the bar closes off its low (close-in-range ≥ 0.10).
6. **Cooldown** — 4h since the last trigger.

## Exits & sizing
Two paper variants run in one process on the same signals since 2026-09-12:

- **`bot_short_squeeze_v1`** (shipped 2026-07-21): stop 10bp **below the swept
  low** (invalidation = the low actually breaking), target **3R**, time stop
  **6h**. Fixed-R **1% of capital**; the stop is so tight that the 3× notional
  cap **binds by design** (this replaces the old "20–100× leverage" idea from
  the research notes).
- **`bot_short_squeeze_nostop_v1`**: **no stop, no target, the 6h time stop
  only**, at a fixed **1× capital**. The sizing study found the bp-wide stop
  is inside the sweep's own noise (39 of 71 historical fires stopped out):
  without it the replay went from +0.48 R to +1.16 R per fire, with a −7.8 R
  worst trade instead of −1 R — hence the smaller notional. The two ledgers
  decide at 20 paired fires (`docs/calibration/short_squeeze.md`).

Costs booked: 10 bp per round trip, measured. Until 2026-09-12 the bot booked
25 bp, which on a bp-wide stop was 0.80 R per trade and made the paper record
negative by construction.

## Why it's usually silent
The Asia short-macro setup exists on only **~3.7% of days**; the longest historical
drought is 186 days. Long silences in positive-funding regimes are the designed
behavior — this sleeve is the regime-complement to CARRY (which earns exactly when
this one sleeps). The mirror short signal was tested and does not work; long-only
is deliberate.

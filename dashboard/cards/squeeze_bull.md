# How Squeeze Bull works

**One line:** when leveraged longs are force-closed in a hurry but the trend is
still up, buy the flush.

## What it trades
BTC, long only, hourly bars, any hour of the day. Four gates must all pass on
the same closed bar:

1. **OI flush** — open interest falls **≥ 2% in 4 hours**. That is not traders
   changing their minds; it is liquidation engines closing positions for them.
2. **Price confirms it is longs** — price falls ≥ 0.5% over the same 4 hours,
   so the deleveraging is longs being stopped out, not shorts covering.
3. **Bull regime** — the 30-day return is above **+10%**. This gate is doing
   almost all of the work: ungated, the same signal has a profit factor of
   exactly **1.00** over 423 fires. Gated, it is **1.74**.
4. **Cooldown** — 24 hours since the last flush event, counted over *all*
   flushes including the bear-regime ones the bot never trades.

## Why it should work
Liquidations are price-insensitive forced supply. They overshoot, because the
engine sells whatever the book will take. In an uptrend the bid comes back once
that forced supply is exhausted, so the flush is a discount rather than the
start of a new downtrend. The bull gate is what separates the two, and the
ungated profit factor of 1.00 is the evidence that without it there is no trade
at all.

## Exits & sizing
Stop **−2%**, target **+3%** (1.5 R gross), time stop **48h**. Within a bar the
stop is checked before the target. Fixed-R **1% of capital** over the 2% stop,
so notional is 0.5× capital and the 3× cap never binds.

## What "quiet" looks like
It fires roughly **25 times a year on average and zero times outside a bull
regime**, which was 28% of recent days. Weeks of silence in a flat or falling
tape are correct behaviour, not a broken bot. The diagnostics log every
evaluated hour with the gate that stopped it, so "no flush happened" is always
distinguishable from "the bot stopped evaluating".

## What to distrust
This is deployed to **collect evidence, not because the evidence is settled**.
The out-of-sample record that authorised it is ten fires, and every decision
margin is one observation wide: drop the single best fire and the mean falls
below its own floor. Eight of the ten came from one 19-day stretch. The
deflated Sharpe is 0.72 at one trial and 0.73 at an honest trial count.

The live gate is also **not** the researched one. The study's regime gate read
a daily close stamped up to 21 hours *after* the fire and cannot be
implemented; this bot shifts the daily series one day so it only ever reads
closed history. On the same ten fires that scores +0.246 R against the study's
+0.202, so the peek was not creating the edge — but the paper record measures
the shipped gate, not the published number.

Re-cut points at 20 and 30 fires are fixed in advance in
`docs/calibration/squeeze_bull.md`.

## Not included
The funding-plus-CVD leg of the same research is deliberately left out: two
out-of-sample fires, and it lowers combined MAR from 1.85 to 1.60.

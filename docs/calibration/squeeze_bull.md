# SQUEEZE_BULL (S-107) — calibration log

Long BTC perp after a forced-deleveraging flush, bull regime only.
Sleeve `strategies/sleeves/squeeze_bull/`, bot `bots/squeeze_bull/`,
variant `bot_squeeze_bull_v1`, $10,000 paper.

## Signal (frozen; changing any of it is a new pre-registered study)

Hourly bars, `cd_futures_ohlcv` inner-joined to `cd_open_interest` on
`timestamp`. A bar triggers when:

- `oi_close.pct_change(4) <= -0.02` — open interest deleverages 2% in 4h
- `close.pct_change(4) <= -0.005` — and price falls 0.5%, so it is a *long*
  flush rather than shorts covering
- the 30-day return is above **+10%** (bull regime)
- no kept flush event in the previous 24 bars

**The cooldown runs over flush EVENTS, not over trades.** A bear-regime flush
the sleeve never trades still silences the next 24 bars. This is the research
behaviour (`identify_long_flush_events`) and the parity test enforces it;
deciding the cooldown from the last trade would let a bull flush fire inside
the shadow of an untraded one and the live book would drift from the validated
one.

Execution: enter at the trigger bar's close, stop at −2%, target at +3%
(1.5 R gross), time stop at 48h. Within a bar the stop is checked before the
target. Bot sizes fixed-R at 1% of capital over the 2% stop, so notional is
0.5× capital and the 3× cap never binds.

## The regime gate is NOT the researched one — read this before comparing numbers

The June study computed the 30-day return from the **current** day's daily
close and forward-filled it onto the hourly grid. Every one of the 20
out-of-sample fires therefore read a close stamped 1 to 21 hours *after* the
fire. That construction cannot be implemented and is not what this sleeve does.

Production uses `REGIME_SHIFT_DAYS = 1`: the daily series is shifted one day,
so a fire reads a close at least three hours old. On the same ten
out-of-sample fires:

| gate | mean R | implementable |
|---|---|---|
| June (peeking) | +0.202 | no |
| **backward-only daily (shipped)** | **+0.246** | yes |
| intraday rolling 30d | +0.121 | yes |

Chosen 2026-09-09 by the user. The peek was not manufacturing the result — the
causal gate scores *higher* — but the third row shows the choice is not free,
and the paper record measures the shipped gate, not the study's +0.202.

## Evidence, stated honestly

`studies/notebooks/squeeze_bull_revalidation/findings.md`, verdict BUILD, with
all four parity checks passing byte-exactly. Full sample, bull-gated: 114
fires, profit factor 1.74, win rate 60.5%, mean +0.273 R.

Every margin in the out-of-sample decision is one observation wide:

| clause | floor | measured | margin |
|---|---|---|---|
| OOS fires | 10 | 10 | **0** |
| OOS mean R | +0.10 | +0.202 | +0.102 |
| combined MAR | 1.50 | 1.60 | +0.10 |

Drop the best single fire and the mean falls to +0.068, below the floor. Drop
two and it is negative. Eight of the ten fires are one 19-day episode in
April–May 2026. Six of the ten outcomes are the mechanical barrier payoffs, so
the clause is in substance "did 3 of 6 barrier-resolved trades hit +3% before
−2%". Deflated Sharpe is 0.72 at one trial, and the honest trial count is 30
threshold variants plus roughly 80 stop/target/TIF combinations, at which the
full sample sits at 0.73 and 0.59.

**This is deployed to collect evidence, not because the evidence is settled.**

## Pre-registered re-cut points (written 2026-09-09, before the fires exist)

Recorded now specifically because the original study's n ≥ 10 floor happened
to equal the sample available two days before its pre-registration was
written. These thresholds are fixed in advance and are not to be edited when a
fire disappoints.

- **At n = 20 OOS bull-gated fires** (research + live, on the shipped causal
  gate): re-run the study's `run_oos.py` and `run_combined.py`. CONTINUE if
  mean R ≥ +0.10 and combined MAR ≥ 1.5. DISABLE if mean R ≤ 0.
  Anything between is CONTINUE with a note, and no parameter changes.
- **At n = 30**: same test, plus a deflated Sharpe at a trial count of at
  least 30. DISABLE if DSR < 0.50.
- **Any time**: DISABLE if the live record diverges from the sleeve's own
  replay of the same fires by more than 0.05 R on any trade, which would mean
  an execution or data fault rather than an edge failure.
- Disable by setting the variant's `enabled = 0`; do not edit thresholds.

Expected clock: the rule fires about 0.24 times per bull-regime day and bull
days were 28% of the recent window, so roughly 25 fires a year. n = 20 is
about five months of similar conditions, and **zero** in a flat or bear tape.
Silence is correct behaviour, not breakage.

## Not included

The funding-plus-CVD leg of the June work is deliberately out. It has two
out-of-sample fires at mean −0.224 R and it *lowers* combined MAR from 1.85 to
1.60. Revisit only at ≥ 10 of its own OOS fires.

## Log

| date | change | why |
|---|---|---|
| 2026-09-09 | Sleeve + bot created; backward-only regime gate; OI-flush leg only; fixed-R 1% with a 3× cap; re-cut points above fixed in advance | User authorised paper deployment after `squeeze_bull_revalidation` returned BUILD. Deployed to accrue out-of-sample fires, with the statistical caveats above understood. |

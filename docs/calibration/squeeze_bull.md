# SQUEEZE_BULL (S-107) — calibration log

Long BTC perp after a forced-deleveraging flush, bull regime only.
Sleeve `bots/squeeze_bull/strategy/`, bot `bots/squeeze_bull/`,
two paper variants of $10,000 each in one process since 2026-09-12:
`bot_squeeze_bull_v1` (the shipped −2 % stop) and
`bot_squeeze_bull_nostop_v1` (no stop; see the 2026-09-12 section).

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

## 2026-09-12 — measured cost, and a second paper variant without the stop

**Cost.** The bot books **7 bp** per round trip (`PAPER_COST_BP_RT = 7.0`,
`PAPER_SLIPPAGE_BP_RT = 0.0`) instead of the 15 bp default it booked from
2026-09-09 to 2026-09-12. The sleeve's `COST_BP_RT = 18.0` is the research
replay convention and is unchanged, so the parity test still reproduces the
June ledger. Execution study `studies/notebooks/execution_2026_09/` (E6, the
sleeve's 122 historical fires on 1 m bars): all-in taker round trip 6.7 bp,
CI90 [4.5, 8.8] = 10 bp of fees and spread less a 2.6 bp *favourable*
decision-to-fill drift (the flush keeps falling for a minute after the
hourly close the bot books as its entry). Fee-only, drift ignored: 8 bp.
The pre-registered change rule (measured differs by > 3 bp and the CI
excludes the coded value) passed. Trades closed before 2026-09-12 carry
15 bp; treat it as a methodology change.

**Second variant `bot_squeeze_bull_nostop_v1`.** Same process, same signals,
same 0.5× notional (sized as if the 2 % stop existed); exits on the +3 %
target or the 48 h time stop only — no stop. Sizing study
`studies/notebooks/sizing_style_2026_09/` (S1 policy P1b, 122 fires
2022-01 → 2026-06 on 1 m bars, measured cost, shipped sizing):

| policy | mean R | win | worst trade | MTM maxDD | MAR | halves |
|---|---|---|---|---|---|---|
| stop −2 % (shipped) | +0.334 | 58 % | −1.00 R | −4.4 % | 1.86 | +0.256 / +0.412 |
| **no stop, target + 48 h (new variant)** | **+0.526** | 69 % | −4.17 R | **−3.3 %** | **4.09** | +0.544 / +0.507 |
| no stop, 48 h only | +0.566 | 65 % | −4.17 R | −4.0 % | 3.69 | +0.556 / +0.575 |

Every pre-registered clause passed (MAR and expectancy better in both
halves, drawdown not worse by > 5 pp, zero liquidation episodes with a 2×
safety factor on the worst move). The target-keeping form was chosen for
its lower drawdown and higher MAR. R for both variants is measured against
the 2 % reference distance (`_reference_stop_price` in the trade notes), so
the two ledgers compare directly. Each variant applies its own single-open
guard, so the no-stop variant, which holds longer, will skip some fires the
stop variant takes; the replay in the re-cut below covers the union.

**Pre-registered re-cut for the pair (written 2026-09-12, before any fire):**

- The incumbent's re-cut points above (n = 20 / 30) are unchanged and apply
  to it alone.
- At **n = 20 fires taken by both variants on the same bar**: replay both
  policies over the union of live fires with the sleeve's own walk. DISABLE
  the no-stop variant if its live mean R ≤ 0, or its paired mean R is more
  than 0.10 R below the stop variant's, or any single live trade prints
  below −6 R (the replay's worst is −4.17 R). Otherwise CONTINUE.
- At **n = 30**: the same, plus a deflated Sharpe ≥ 0.5 at a trial count of
  at least 30. Make the no-stop variant the fleet default (retire the stop
  variant) only if its paired mean R is ≥ the stop variant's + 0.10 R AND
  its MTM drawdown is not worse by > 5 pp.
- Any time: DISABLE a variant whose live record diverges from the sleeve's
  own replay of the same fires by > 0.05 R on any trade. Disable via
  `enabled = 0`; do not edit thresholds.

**The re-cut is executable**, written 2026-09-12 while `n_paired = 0` so the
code predates the data it judges: `studies/notebooks/squeeze_recut/`
(`python studies/notebooks/squeeze_recut/run_recut.py --sleeve squeeze_bull`).
Its README quotes the block above verbatim and `tests/test_squeeze_recut.py`
pins each clause, including that below the gate the thresholds are not
computed at all. Two readings had to be fixed in advance and are recorded
there: a pair requires both sides CLOSED (an open trade has no R, and counting
it could trigger the re-cut early), and the 0.05 R clause is read against the
**residual** after funding and booked cost, because the clause exists to catch
"an execution or data fault rather than an edge failure". Note that
`enabled = 0` alone does not stop a runner — the variant must also leave
`bots/squeeze_bull/config.py: VARIANTS`, then the bot restarts.

## Log

| date | change | why |
|---|---|---|
| 2026-09-14 | **The scheduled-exit backstop books this sleeve's own cost; the re-cut nets booked cost on every closed trade.** No parameter changed. `botlib.close_due_trades` now closes through `signal._close_paper` (`PAPER_COST_BP_RT` 7 bp, 0 slippage, funding) instead of the `trades.py` defaults (10 bp fee + 5 bp slippage + funding): 8 bp less, 0.04 R at the 2 % reference stop, on both variants. **No backstop close has ever happened on this sleeve**, so no ledger row changes (SJ-4250 closed on its own time stop at 7 bp). The backstop can only beat the sweep's time stop inside one tick (same clock, same price source); when it does, the label is still `scheduled_exit`, not `time_stop`. Also: a tick whose `decide()` raises now still runs the backstop and reports heartbeat `error`, and a due trade whose strategy has no closer is left open and named in the heartbeat without stopping the twin's tick. Any other error in one variant's tick (for example `execute()` refused while the process stands down as a duplicate instance) no longer skips the twin's tick either, though it still skips that variant's own backstop for the tick. **Re-cut (D4):** `recut_lib.divergence` now nets `booked_cost_R = −(booked_bp − 6.67) / 1e4 / risk_pct` on every closed trade, with `booked_bp` = the CLOSE adjustment's `fee_usdt` over notional; before, only `scheduled_exit` closes got a term, at a hard-coded 15 bp. The same change fixes `funding_R`, which came out NaN on every hold that crossed a funding settlement (every 48 h hold does; the lookup passed `+1` for the direction), leaving that funding in the residual. Every recorded re-cut run had D4 = n/a (`studies/notebooks/squeeze_recut/README.md`). Takes effect when squeeze_bull restarts. | BACKLOG 4.4 and 18; operator go-ahead 2026-09-14. Tests: `test_squeeze_bull_bot.py` backstop cost per variant (83.00 on +$100 of price P&L; the old backstop booked 75.00), raising decide, twin still swept after a refusal and after an entry error; `test_squeeze_recut.py` booked-cost tests. |
| 2026-09-13 | **Live time stop corrected 47 h → 48 h from entry.** No parameter changed (`TIF_HOURS` was always 48); the live schedule added it to the trigger bar's OPEN (`bar_ts`), but entry is that bar's CLOSE, so every live hold was 47 h. Now `entry + 48 h`, matching the research walker (`math.replay_bracket`) and the re-cut replay (`recut_lib.py:365-366`). Affects both variants. **Re-cut note:** SJ-4250 (closed on its time stop 2026-09-13, held 47.0 h) was booked under the old schedule, so its exit is structurally one hour early against its own replay; the n = 20 re-cut and the 0.05 R divergence rule must read it with that known offset rather than as a divergence. Goldens re-baselined: eight files, and the only change in each is its time-stop timestamps moving by exactly +3,600 s (verified field by field); the three no-fire goldens are byte-identical. | BACKLOG 12a, found by the item-11 time-stop census. User go-ahead 2026-09-13. Boundary tests (open at entry + 47 h 59 m, closed at + 48 h) and a drill mutation. Needs a squeeze_bull restart. |
| 2026-09-12 | Booked cost 15 → 7 bp (`PAPER_COST_BP_RT`); second paper variant `bot_squeeze_bull_nostop_v1` (no stop, +3 % target, 48 h, 0.5× fixed notional) in the same process; re-cut rule for the pair fixed above | `execution_2026_09` E6 (measured 6.7 bp [4.5, 8.8]); `sizing_style_2026_09` S1/S3 (BUILD-CANDIDATE on every pre-registered clause). User go-ahead 2026-09-12. |
| 2026-09-09 | Sleeve + bot created; backward-only regime gate; OI-flush leg only; fixed-R 1% with a 3× cap; re-cut points above fixed in advance | User authorised paper deployment after `squeeze_bull_revalidation` returned BUILD. Deployed to accrue out-of-sample fires, with the statistical caveats above understood. |

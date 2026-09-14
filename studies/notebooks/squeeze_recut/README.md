# Squeeze twin re-cut — pre-registration

**Written 2026-09-12, when `n_paired = 0` for both sleeves.** That is the
point: the decision code exists before the data it will judge, so the verdict
at n = 20 cannot be a function of what the fires turn out to look like.

SQUEEZE_BULL and SHORT_SQUEEZE each run a stop variant and a no-stop twin in
**one process on the same signals**, differing only in the exit. Each variant
applies its own single-open guard, so the longer-holding no-stop variant skips
some fires the stop variant takes. The comparison is therefore on the
**paired** subset — fires both variants took on the same bar — while the
replay covers the **union** of live fires.

    python studies/notebooks/squeeze_recut/run_recut.py --sleeve both
    python studies/notebooks/squeeze_recut/run_recut.py --sleeve squeeze_bull --n-gate 30

Read-only against `prod.db`. Exit code 1 on any DISABLE / RETIRE verdict.

## The rules, quoted verbatim from the calibration logs

Nothing in `recut_lib.py` may diverge from these. They were fixed before any
fire existed and are not to be edited when a fire disappoints.

### SQUEEZE_BULL — `docs/calibration/squeeze_bull.md`

> - The incumbent's re-cut points above (n = 20 / 30) are unchanged and apply
>   to it alone.
> - At **n = 20 fires taken by both variants on the same bar**: replay both
>   policies over the union of live fires with the sleeve's own walk. DISABLE
>   the no-stop variant if its live mean R ≤ 0, or its paired mean R is more
>   than 0.10 R below the stop variant's, or any single live trade prints
>   below −6 R (the replay's worst is −4.17 R). Otherwise CONTINUE.
> - At **n = 30**: the same, plus a deflated Sharpe ≥ 0.5 at a trial count of
>   at least 30. Make the no-stop variant the fleet default (retire the stop
>   variant) only if its paired mean R is ≥ the stop variant's + 0.10 R AND
>   its MTM drawdown is not worse by > 5 pp.
> - Any time: DISABLE a variant whose live record diverges from the sleeve's
>   own replay of the same fires by > 0.05 R on any trade. Disable via
>   `enabled = 0`; do not edit thresholds.

### SHORT_SQUEEZE — `docs/calibration/short_squeeze.md`

> - At **n = 20 fires taken by both variants on the same bar**: replay both
>   policies over the union of live fires with the sleeve's own walk. DISABLE
>   the no-stop variant if its live mean R ≤ 0, or its paired mean R is more
>   than 0.10 R below the stop variant's, or any single live trade prints
>   below −10 R (the replay's worst is −7.8 R). Otherwise CONTINUE.
> - At **n = 30**: the same, plus a deflated Sharpe ≥ 0.5 at a trial count of
>   at least 30. If BOTH variants have mean R ≤ 0 at n = 30, retire the
>   sleeve — the execution study's verdict stands. Make the no-stop variant
>   the fleet default only if its paired mean R is ≥ the stop variant's
>   + 0.10 R AND its MTM drawdown is not worse by > 5 pp.
> - Any time: DISABLE a variant whose live record diverges from the sleeve's
>   own replay of the same fires by > 0.05 R on any trade.

## Clause map

| id | clause | evaluated at | on FAIL |
|---|---|---|---|
| D1 | no-stop live mean R ≤ 0 | n ≥ gate | DISABLE no-stop |
| D2 | paired mean R more than 0.10 R below the stop variant | n ≥ gate | DISABLE no-stop |
| D3 | any single live trade below the floor (−6 R / −10 R) | n ≥ gate | DISABLE no-stop |
| D4 | live vs replay **residual** > 0.05 R on any trade | **any n, including 0** | DISABLE that variant |
| D5 | deflated Sharpe < 0.50 at 30 trials | n = 30 | DISABLE no-stop |
| D6 | paired mean R ≥ stop + 0.10 R (and MTM DD not worse by > 5 pp) | n = 30 | promote no-stop |
| D7 | both variants' mean R ≤ 0 (SHORT_SQUEEZE only) | n = 30 | RETIRE the sleeve |

Below the gate the verdict is `NOT_DUE` and **D1–D3 / D5–D7 are not computed
at all** — reading a threshold early is the same defect as editing it.
`tests/test_squeeze_recut.py` pins that, along with each clause's sign and the
two per-sleeve differences (the worst-trade floor, and D7).

## Decisions taken here, and why

**Trial count `N_TRIALS = 30`, fixed.** The squeeze_bull calibration log puts
the honest count at "30 threshold variants plus roughly 80 stop/target/TIF
combinations". The pre-registration says "at least 30", so 30 is the floor and
the kindest defensible number; it is recorded here rather than chosen when the
DSR is computed.

**D4 is read against the residual, not the raw difference.** The clause exists
to catch "an execution or data fault rather than an edge failure"
(squeeze_bull.md). A live-vs-replay difference that is fully explained by
funding, by the booked cost, or by the 60 s poll cadence is not a fault, so
`divergence()` decomposes the difference into `funding_R`, `booked_cost_R`
and a residual, and the clause fires on the residual. Both numbers are printed;
if the decomposition ever fails to explain a large raw difference, the residual
is what remains and the clause still fires.

**`booked_cost_R` is netted on every closed trade, not a rounding.** Live R is
net of the cost the ledger booked — the coded 7 bp (SQUEEZE_BULL) or 10 bp
(SHORT_SQUEEZE) — while the replay nets the E6 measured mean on every trade
(`sl.cost_bp`: 6.67 / 9.32 bp). On SHORT_SQUEEZE's tight stops that 0.68 bp
gap is 0.004–0.045 R per trade (median 0.020 R), up to 90% of D4's 0.05 R
budget. `booked_cost_R = −(booked_bp − sl.cost_bp) / 1e4 / risk_pct`, where
`booked_bp` is the trade's CLOSE adjustment `fee_usdt` over the notional it
closed (the first CLOSE row by `seq`, because prod's `trade_adjustments` can
hold duplicates, BACKLOG 4.5), falling back to the `fees=…bp RT, slip=…bp RT`
notes suffix (whole bp only) and, for a `scheduled_exit` close with neither,
to the 15 bp the old backstop booked. Any trade closed by `scheduled_exit` is
still flagged in the report: since 2026-09-14 that is a label and price-source
difference, not a cost one.

**Changed 2026-09-14: the cost term went from backstop-only to every closed
trade.** Until then `backstop_cost_R` was computed only for `scheduled_exit`
closes, at a hard-coded 15 bp, because `botlib.close_due_trades` booked the
trades.py defaults (15 bp + funding) instead of the sleeve's cost. The same
day the backstop was changed to close through each sleeve's own close
(BACKLOG 4.4), so the hard-coded 15 bp would have invented a cost on every
later backstop close — 0.04 R on SQUEEZE_BULL, 0.03–0.33 R on SHORT_SQUEEZE —
and the gap above was never netted at all. D4's own clause text already read
"residual, after funding and booked cost". **What had been judged under the
old term: nothing.** D4 is evaluated at every n, including 0, and all five
recorded runs (`results/recut_*_20260912T1555–1559Z.json`) show D4 = n/a: no
closed live trade had been decomposed (SJ-4250, the only live fire, was still
open then). `tests/test_squeeze_recut.py` pins the new term.

**Also fixed 2026-09-14: `funding_R` was NaN on every hold that crossed a
funding settlement.** `divergence()` passed the direction to
`funding.accrued_pct` as `+1` where it takes the string `"LONG"`, so the
lookup raised whenever a settlement row fell inside the hold, the broad
`except` turned `funding_R` into NaN, and the residual kept all the funding
live had booked. Every SQUEEZE_BULL hold (48 h) crosses settlements; on
SHORT_SQUEEZE's stops one 0.01 % settlement is 0.006–0.066 R of residual
(median 0.03 R, from the same E6 per-trade stop distances), past D4's 0.05 R
on its own at the tightest stops. Nothing was judged on it, for the same reason as
above: every recorded run had D4 = n/a. A test now runs the real lookup
against a funding table instead of a stub.

**A pair needs both sides CLOSED.** "Fires taken by both variants on the same
bar" is read as both having a resolved R. Counting an open trade would let the
gate be reached by trades that have not resolved — the one way the re-cut could
trigger early.

**A fire before a variant existed is not a skip.** The no-stop twins were
registered 2026-09-12, months after their stop siblings, so the union marks
those bars `variant_not_yet_live` rather than `unexplained`. `unexplained` is
an alarm — a fire neither variant took with no open position to explain it —
and the report prints it loudly.

**SHORT_SQUEEZE's reconstructed fires are approximate.** Its live gate reads
the newest funding settlement where the validated trigger history used a 7-row
mean (short_squeeze.md), so `reconstruct_fires` is labelled
`approx_research_window` for that sleeve. It affects only the NEITHER rows of
the union — never the paired set, which comes from the live ledger.

## Acting on a verdict

`enabled = 0` **does not stop a bot runner** — the runners iterate
`bots/<name>/config.py: VARIANTS` and never read the column. A DISABLE means:
set `enabled = 0`, remove the variant from `VARIANTS`, restart that bot. The
report prints this. Do not edit thresholds.

## Files

    README.md       this pre-registration
    recut_lib.py    all logic, no printing
    run_recut.py    CLI and report
    results/        recut_<sleeve>_<as_of>.json, union_*.csv, divergence_*.csv
    findings.md     written only when a re-cut actually runs

## Status

| date | n paired (SB / SS) | verdict |
|---|---|---|
| 2026-09-12 | 0 / 0 | NOT_DUE — script written and tested before any fire exists |
| 2026-09-14 | — (no run) | D4's cost term now nets the booked cost on every closed trade, and `funding_R` no longer comes out NaN on settlement-crossing holds (see "Changed 2026-09-14" and "Also fixed 2026-09-14" above); every earlier run had D4 = n/a |

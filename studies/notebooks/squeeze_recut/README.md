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
`divergence()` decomposes the difference into `funding_R`, `backstop_cost_R`
and a residual, and the clause fires on the residual. Both numbers are printed;
if the decomposition ever fails to explain a large raw difference, the residual
is what remains and the clause still fires.

**`backstop_cost_R` is a real term, not a rounding.** `botlib.close_due_trades`
closes overdue trades with no cost override, so it books the 15 bp default
rather than the sleeve's measured 7 bp (SQUEEZE_BULL) or 10 bp
(SHORT_SQUEEZE). It is a true fallback — the sleeve's own sweep runs first on
every tick — but when it fires it is worth 0.04 R on SQUEEZE_BULL and
0.07–0.5 R on SHORT_SQUEEZE, which alone would trip D4. Any trade closed by
`scheduled_exit` is flagged in the report. See BACKLOG.md item 4.4.

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

# Paladin study: validation review and forward addendum

Review date: 2026-09-14. Static source/notebook/saved-evidence review only.
No replay, data fetch, corpus extraction or database query was run.
This retrospective addendum preserves the original findings and their later corrections.

## Verdict and priority

**P0 for reuse of the positive wick-exit/automatable-alpha claim; preserve the no-sleeve decision.**
The corpus study provides useful behavior and reporting diagnostics, not proof of a trader's full performance or the universal absence of setup edge.
Future strategy testing is conditional on a narrowly specified new question, not a reason to recreate a Paladin sleeve.

## Artifacts reviewed

- `paladin_study.ipynb`, all zero-based cells 0–11, including saved textual tables and conclusions.
- `findings.md`, `paladin_data.py`, `fetch_ohlcv.py`, `venue_offset.py`, `run_h0.py`, `entry_context.py`.
- `resolve_and_exits.py`, `exit_wick_study.py`, `one_minute_check.py`, `entries_completion.py`, `entry_structure.py`.
- Imported `studies/material/paladin/analysis/load.py` replay/excursion functions and notebook result references.
- Raw message authenticity and all extraction decisions were not independently re-audited.

## Useful existing checks

- Cell 1 reports 217 positions and 173 backtestable cases rather than implying the entire corpus is tradable.
- Venue-offset diagnostics distinguish price types; follower next-bar market fills are compared with stated-price fills.
- The H0 grid includes 24/72/168/700-hour, break-even and follower-fill alternatives; the weak mechanical screen is retained.
- `entry_context.py:61` requires feature bars to have closed before the signal and matches controls by symbol/time of day.
- Unresolved positions and manual-exit MFE are examined explicitly; the findings do not rely solely on reported wins.
- BTC/ETH granularity checks report no H0 outcome flips in the 77-position subset; this is a useful limited sensitivity.
- Findings correctly preserve the decision not to build a sleeve despite later exit-rule interest.

## Specific defects and inference gaps

1. `exit_wick_study.py:57` builds round levels from future holding-window extrema; the scale derives from the future high at line 46.
   A future decimal-boundary crossing can change earlier levels/exits. This invalidates the claim of demonstrated causal wick alpha until replayed causally.
2. The wick variant chooses a close-triggered exit before an already-touched TP (`exit_wick_study.py:79` versus line 82).
   Whether the target is resting or conditional must be specified; same-bar fills, gap stops and close-trigger latency need executable ordering.
3. Wick tests are gross R with a median-risk cost approximation (`exit_wick_study.py:22`).
   Per-trade risk varies and funding depends on hold time; approximately +0.08R net is not a verified net ledger.
4. `run_h0.py:69` compares recorded execution only where reported R exists; those roughly 107 cases are selected toward disclosed outcomes.
   The full mechanical cohort and selected reported cohort do not isolate discretionary skill, and reported entries may be stale or retrospectively described.
5. `resolve_and_exits.py:56` applies planned stop/TP to unreported positions; that infers counterfactual mechanical outcomes, not actual hidden executions.
   Notebook cell 11 and findings use slightly different corrected counts; missing/implausible/no-data cases remain unresolved rather than proven losses.
6. Manual MFE includes the first stop-touch bar (`resolve_and_exits.py:94`), whose favorable extreme may occur after the stop.
   MFE available after a discretionary exit is an opportunity bound, not a realizable exit-policy return.
7. Matching 40 random dates per trade describes selection habits, not whether those features predict profitable trades.
   Shared symbols/dates and multiple H1–H15/structure/exit hypotheses require clustered inference and a research-history ledger.
8. `entries_completion.py:65` calls a reported-R minus passive-R identity alpha; selected disclosures and differing exit clocks remain confounded.
   Same-window comparisons alone do not identify beta versus timing/exit skill; `entries_completion.py:153` also assumes event timestamps are sorted.
9. `paladin_data.py:65` resamples slices without an explicit complete-bar count gate; partial first/last higher-timeframe bars need flagging.
   Plan fills may be unexecutable, current listings influence the fetch universe, and spot fallback is not a futures execution substitute.
10. Jul–Aug 2026 was already inspected within a short recovering-market sample before/alongside new hypotheses.
    Eight variants and 1m/5m/15m checks are sensitivities, not eight independent replications or a fresh untouched OOS.

## Prioritized conditional plan

### P0.1 — qualify positive inference before reuse

- Hypothesis: the fixed wick rule is causal and adds net value on the same eligible entries under executable order semantics.
- Freeze source/result/corpus hashes, original post timestamps, edit/forward timestamps and every known extraction exclusion.
- Define entry availability, follower latency, contract identity and whether TP is resting; log all unresolved message/price ambiguities.
- Set round-grid scale only from entry-time information; add future-data perturbation and decimal-crossing fixtures.
- Add stop/TP/wick same-bar fixtures, gap fills and incomplete-bar cases; use finer data or publish feasible ordering bounds.
- Compare gross and net R with per-fill spread/fees/slippage and actual holding-dependent funding.
- Acceptance: no future data changes past decisions and fixed quantities reconcile to a timestamped net cash-flow ledger.

### P1.2 — retain descriptive and predictive questions separately

- Reconcile the 217→173→reported-R cohort waterfall and differing unresolved counts in one new notebook.
- Provide missing-outcome bounds and matched-cohort results without calling simulated resolutions the trader's true win rate.
- Preserve the complete 12 H0 and 8 wick variants, granularity studies and exploratory feature families in the search count.
- Behavior controls should match symbol, side, time of day and local regime with calendar/symbol-clustered uncertainty.
- A fresh-high association is a hypothesis source; testing trade profitability requires fixed entries, exits and costs in a separate preregistered study.
- For transfer to project sleeves, choose one frozen wick rule versus the original exit on identical events before inspecting new outcomes.
- Also evaluate the causal full portfolio if earlier exits alter eligibility; align total capital, leverage, concurrency and idle cash.
- Use chronological forward data with purging for 72-hour holds; the already-read May–August corpus is development evidence.
- Use paired calendar blocks across symbols; report effective independent episodes and sensitivity to withholding the dominant market phase.
- Forward advancement requires lower 90% paired net improvement bound above zero under a predeclared tail-risk and turnover limit.
- If data/corpus uncertainty dominates, remain descriptive and retain no-build; failure to detect a feature is not proof that no such edge exists.

## Planned notebook outputs

- New `validation_review.ipynb`: corpus/venue/timestamp manifest, cohort and missing-outcome waterfall, count reconciliation.
- Causal wick fixtures, old-to-corrected paired outcomes, fill-order bounds and per-trade net-cost bridge.
- Descriptive feature effects with clustered intervals; separately labeled future predictive/transfer gate table if conditionally tested.

## Not applicable / not verified

Options pricing is not applicable. Actual private executions, source-message completeness, deleted/edit timing and live fills were not verified.
The granularity check applies to BTC/ETH only; it does not validate all alternative symbols or true follower execution.
No existing artifact was modified and no new study result or production recommendation is claimed.

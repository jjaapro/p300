# Attribution study: validation review and forward addendum

Review date: 2026-09-14. Static source/findings review only; no replay, database query or download.
This retrospective assessment preserves the original output and introduces a forward interpretation/validation plan.

## Verdict and priority

**P0 for reuse of the decomposition as proof of selection alpha or a go/no-go gate.**
The arithmetic identity can be a descriptive diagnostic, but the controls do not identify causal entry or exit skill.
No strategy rejection or promotion follows automatically from this review.

## Artifacts reviewed

- `findings.md`, `attribution.py`, and referenced historical/paper result artifacts.
- No notebook is present in this directory; future validation belongs in a new notebook here.
- The backward-only Chento input and related Paladin attribution methodology were inspected at their interfaces.
- The raw live/paper database was not queried and strategy/asset composition was not independently established.

## Useful parts of the existing analysis

- `attribution.py:81` makes the decomposition explicit rather than treating all directional gains as skill.
- Random-time and same-time passive-hold comparisons are distinct components.
- Findings acknowledge the very small seven-trade paper sample and the limits of transferring Paladin lessons.
- The backward-only Chento input avoids the original bidirectional trigger intersection at that input stage.
- Algebraic reconciliation is easy to check and can remain a descriptive notebook output.
- These strengths support diagnostic use after relabeling, not the stronger alpha inference.

## Claim-specific defects and limitations

1. `attribution.py:81` defines actual = random hold + same-time-minus-random + actual-minus-same-time.
   This equality is true by construction; it does not identify market beta, selection alpha or exit alpha without defensible counterfactuals.
2. Random controls draw across the entire available history (`attribution.py:95`), with only about 20 draws per trade.
   They are not matched to local regime, hour, volatility, liquidity or contemporaneous market exposure; changing the history changes the baseline.
3. `_hold_r` at line 65 uses a fixed no-stop 72-hour hold and first available close, regardless of the actual entry/exit duration.
   The actual replay uses stops/targets; the component difference therefore mixes entry, exit, exposure-time and risk-rule effects.
4. Near the data endpoint the passive window can truncate, and the dead `passive_r` function uses different semantics.
   The function names/docstrings do not by themselves establish matched timing or complete counterfactual paths.
5. `live_paper` selects no asset/symbol field, then uses BTCUSDT prices for the comparisons (`attribution.py:151`).
   If ETH or other assets are present, this is the wrong counterfactual; even a BTC-only sample needs explicit validation.
6. Open paper trades are marked at the current cutoff while the same-time control targets a fixed 72-hour horizon.
   Mixed open/closed cases, publication latency, funding, fees and actual position quantity are not consistently reconciled.
7. The historical replay and control R values are gross price outcomes.
   Stop distance rescales costs and beta exposure; variable R units are not an equal-capital portfolio or formal risk-factor attribution.
8. Shared market episodes, overlapping positions and reusing the same price history make controls/trades dependent.
   The chosen sources, fixed horizon and multiple descriptive comparisons also belong in selection history.
9. `findings.md` describes the decomposition as a practical selection-alpha workflow, exceeding what the counterfactuals support.
   Related Paladin failure to find tested features cannot establish that all remaining profit is exit skill or that entries are structureless.

## Prioritized forward plan

### P0.1 — clarify the estimand and prevent invalid reuse

- Hypothesis: each reported component is an exact, timestamp-consistent descriptive contrast under a declared control policy.
- Freeze source/result/input hashes; preserve old component names and values in an original-versus-reviewed bridge.
- Rename interpretations in a new notebook to random-time hold, same-time hold contrast and policy contrast unless stronger identification is supplied.
- Require asset, venue, direction, fixed units, signal observed-at, fill, exit, fees, funding and mark cutoff in every trade row.
- Separate open positions from completed trades and use equal known horizons; report censoring instead of silently shortening controls.
- Build hand-checkable long/short, non-BTC, partial horizon, fee/funding and same-entry-different-exit fixtures.
- Acceptance: components reconcile in dollars/R, every control matches the correct asset and information clock, and none is labeled causal alpha by identity alone.

### P1.2 — only if attribution will inform a strategy decision

- Freeze the exact question: incremental entry timing, exit policy, market beta or full-strategy value over passive exposure.
- For entry timing, use a fixed exit/risk policy for both actual and randomized events with local symbol/side/session/regime matches.
- For exit policy, use identical eligible entries and compare fixed policies under the same causal event and fill engine.
- For economic beta, build daily marked dollar returns and estimate lagged-factor exposure; do not substitute unmatched R holds for beta.
- Controls must share capital, gross exposure and known holding windows; include cash and exposure-matched passive alternatives.
- Use per-fill bid/ask/spread, slippage, fees and signed realized funding; enforce concurrent capital and margin limits.
- Register control construction, number of random draws, seed policy and one primary contrast before looking at new outcomes.
- Count the original sources/horizons/components and upstream strategy selection in the research ledger; additional control choices are additional analyses.
- Use chronological out-of-sample evaluation and untouched forward trades; the seven already-observed paper cases are descriptive only.
- Resample calendar blocks across all simultaneous trades and their paired controls; quantify Monte Carlo error separately from market-sample uncertainty.
- Forward decision evidence requires a lower 90% paired net contrast bound above zero and a predeclared economically meaningful effect size.
- A positive component with unstable matching or wide uncertainty remains INCONCLUSIVE; it cannot alone approve or kill a strategy.

## Planned notebook outputs

- New `validation_review.ipynb`: estimand dictionary, asset/timestamp coverage and exact decomposition cash-flow fixtures.
- Matched-versus-original control diagnostics, completed/open cohort bridge and beta-versus-policy decomposition clearly separated.
- Trial/control registry, calendar-block intervals, Monte Carlo convergence and a limited decision-use table.

## Not applicable / not verified

Options quote/settlement modeling is not part of the current analysis.
Actual live sample assets, execution details, independent alpha and corrected performance were not verified.
No existing result, strategy decision or production configuration was changed.

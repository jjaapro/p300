# Basis carry: validation review and forward addendum

Review date: 2026-09-14. Scope: static source, notebook and saved-result review only.
No notebook, replay, database query or data download was run for this review.
This is a retrospective assessment and a proposed forward addendum, not a replacement preregistration.
The frozen README and all existing findings/results remain unchanged.

## Verdict and priority

**P1 — preserve literal INCONCLUSIVE (D3), and the decision not to build.**
Negative modeled economics are useful screening evidence, conditional on their data/accounting assumptions.
Exact realized-funding, population-identity and confidence claims need qualification before reuse.
A new strategy search is not justified merely to obtain an unqualified KILL label.

## Artifacts reviewed

- `basis_carry.ipynb`, all zero-based cells 0–19, including saved textual outputs.
- `README.md`, `findings.md`, `basis_carry.py`, `diagnose_d3.py`, `staleness_check.py`, `funding_vs_basis.py`.
- Saved notebook tables cover `results/report.json`, primary/grid trades, D1/D2/D3 diagnostics, staleness and manual checks.
- Relevant funding conventions were compared with the neighboring delta-neutral source and documented lineage.

## What the existing evidence supports

- Cells 0–16 and `findings.md:44` retain the failed D3 gate and explicitly record that economics were nevertheless evaluated.
- Cell 17 reports 98.8042% exact reconstruction against the frozen 99.9% requirement; the failure was not silently relaxed.
- The third-source comparison favors 15-minute reconstruction on 717 of 719 disagreeing covered hours; coverage is limited.
- Cell 18 independently recomputes four trade cash flows to numerical precision using separate SQL/arithmetic.
- Cells 18–19 expose stale far-quarter prints, two zero-volume primary entries and the near-expiry annualization distortion.
- Saved primary economics are −7.14% annualized on summed deployed-notional days, with all 24 reported grid rows negative.
- These arithmetic/source sensitivities support an archived negative screen; they do not independently authenticate settlements or fills.

## Claim-specific gaps

1. `basis_carry.py:290` filters `fr_close` onto an eight-hour grid. This counts sampled rows once; it does not turn historical forecast bars into settled funding.
   `findings.md:262` calls complete counts verification of settlements, while `../delta_neutral/findings.md:148` explicitly identifies the pre-2026-04-13 proxy era.
   Four manual checks reuse that lineage, so exact realized-funding attribution remains unverified.
2. `basis_carry.py:350` selects the signal and enters at the same hourly close. Open-stamped closes are available one hour after their labels.
   `exit_table` uses delivery minus one hour but that bar's close can coincide with delivery; `findings.md:247` calls it a 07:00 exit.
   Funding `(entry_ts, exit_ts]`, mark timestamps, delivery mapping and days held therefore need one explicit economic clock.
3. `funding_vs_basis.py` and `findings.md:132` subtract one annualized contemporaneous rate from a term basis.
   This is a carry-state diagnostic, not an identity for subsequently realized multi-month funding or proof that every conditional strategy fails.
4. The hot-state bootstrap in `funding_vs_basis.py:112` filters observations before blocking; 45 retained observations need not mean 15 consecutive calendar days.
   `basis_carry.py:403` resamples trades independently despite shared BTC/ETH quarter shocks and overlapping holds.
5. `basis_carry.py:399` divides pooled net returns by summed holding days, not an executable portfolio's equity or margin allocation.
   Quantity neutrality, cross-leg basis risk, concurrent BTC/ETH allocation, margin calls and capital tied up on both legs are not modeled.
6. Zero-volume sensitivity does not establish spread, available size, synchronized two-leg execution or historical contract identity.
   Four current metadata anchors and a calendar rule cannot independently validate every historical onboard/delivery event.

## Conditional forward plan

### P1.1 — establish whether exact economics deserve reuse

- Hypothesis: on a verified settlement-and-price panel, the frozen 8% current-quarter rule still fails its original economic gates.
- Freeze source hashes, historical code revision, UTC cutoff, delivery-symbol mapping and an as-of availability dictionary before any replay.
- Separate predicted funding observations, published settled rates and actual cash-flow timestamps; validate samples across the cutover and stress periods.
- Resolve open/close timestamps with fixtures at 00:00, 08:00, 16:00, quarterly roll and delivery; charge only settlements held through economically.
- Reject unresolved mandatory data gaps instead of charging them zero; report proxy-era diagnostics separately if authoritative history is unavailable.
- First rerun only the unchanged primary for reconciliation, conditional on a future decision to revisit; retain D3's literal historical failure.
- Acceptance: cash-flow components reconcile to fixed quantities, all required timestamps are auditable, and no unknowable same-close fill is credited.

### P1.2 — only if a practical investment question remains

- Specify executable next-quote/two-leg entry, limit versus market behavior, partial fills, unwind failures, bid/ask, fees, slippage and delivery fees.
- Track marked equity and per-venue collateral with fixed units, funding cash flows, gross/net exposure and an explicit capital denominator.
- Controls: cash, verified spot/perp carry and the frozen basis rule at identical capital and dates; report long/reverse directions separately.
- Preserve the original 12-cell family plus 12 source sensitivities in the search ledger; count every later conditional-entry idea as a new study.
- Use quarter/calendar blocks that keep BTC and ETH together; show effective cycle count and sensitivity to block length rather than iid precision alone.
- Compare unchanged K1 ≥5%, K2 lower 95% bound ≥5% and K3 DSR ≥0.95 only after data gates and an honest selection chronology pass.
- A new forward claim additionally needs a held-out period declared before inspection and positive net advantage over the equal-capital carry control.
- Otherwise keep the no-build decision; repairing archival arithmetic does not require opening a broader search.

## Planned notebook outputs

- A new `validation_review.ipynb` here: source manifest, clock/settlement ledger, contract map and discrepancy table.
- Original-versus-corrected primary cash-flow bridge; price/funding/fee/capital decomposition and exposure-aware equity.
- Calendar-block uncertainty and a literal-original-versus-forward decision table; append limitations beside each claim.

## Not applicable / not verified

Options settlement is not applicable. Historical quotes, independent funding records, margin realizations and live execution were not verified.
Notebook outputs were read as saved evidence; their current reproducibility was not asserted or tested.
All proposed testing remains under `studies/notebooks/basis_carry`; no production recommendation is implemented by this document.

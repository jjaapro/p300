# Study validation audit and follow-up plans

**2026-09-14 — static review and planning only.** Testing quality is uneven: there are sound rejection/parity studies, incomplete evidence, and specific causality/accounting defects. This review does not certify the strategies or claim that every possible issue has been found.

Reviewed **51 existing non-ORB notebooks** and supporting study artifacts across **43 existing study directories**, including script-only directories and the top-level notebook families. Added **51 tailored review plans**. The [existing ORB plan](../orb_study/TEST_PLAN.md) remains unchanged.

## Start here

- [Cross-study findings and suggested work order](REVIEW_FINDINGS.md)
- [Shared validation protocol](VALIDATION_PROTOCOL.md)
- [Notebook version of this overview, findings and protocol](00_review_and_plan.ipynb)
- [Machine-readable coverage and findings](audit_index.json)
- [Original notebook inventory, hashes and execution metadata](notebook_inventory.json)

## Reading priorities

P0 means affected evidence needs correction or explicit bounds before reuse. P1 means decision-bearing follow-up is needed if pursued. P2 means conditional/descriptive/archive follow-up. These labels do not direct production changes and are not profitability verdicts.

The plans preserve old preregistrations and decisions. They document what was tested well, exact defects or missing evidence, proposed notebook stages, relevant controls and stopping rules. They are retrospective addenda; any future run must freeze its exact configuration and decision criteria before seeing new results.

## Complete study coverage

Paths in the notebook column are relative to `studies/notebooks/`. Script-only studies receive a notebook-based follow-up plan where applicable.

| Study / report family | Priority | Existing notebooks | Review and plan |
|---|---|---|---|
| adx_eth_2026_09 | P0 | `adx_eth_2026_09/adx_eth.ipynb` | [Review](../adx_eth_2026_09/VALIDATION_REVIEW_PLAN.md) |
| adx_robustness_2026_09 | P0 | `adx_robustness_2026_09/adx_robustness.ipynb` | [Review](../adx_robustness_2026_09/VALIDATION_REVIEW_PLAN.md) |
| adx_study | P0 | Script/report study | [Review](../adx_study/VALIDATION_REVIEW_PLAN.md) |
| anchor_allocator_study | P0 | `anchor_allocator_study/anchor_allocator.ipynb` | [Review](../anchor_allocator_study/VALIDATION_REVIEW_PLAN.md) |
| attribution | P0 | Script/report study | [Review](../attribution/VALIDATION_REVIEW_PLAN.md) |
| basis_carry | P1 | `basis_carry/basis_carry.ipynb` | [Review](../basis_carry/VALIDATION_REVIEW_PLAN.md) |
| bitstamp_adx_parity | P1 | `bitstamp_adx_backtest.ipynb` | [Review](standalone_plans/bitstamp_adx_parity.md) |
| bitstamp_thu_parity | P1 | `bitstamp_thu_bear_backtest.ipynb` | [Review](standalone_plans/bitstamp_thu_parity.md) |
| brainstorm_validation_2026_09 | P1 | `brainstorm_validation_2026_09/brainstorm_validation.ipynb` | [Review](../brainstorm_validation_2026_09/VALIDATION_REVIEW_PLAN.md) |
| calendar_cells | P2 | `calendar_cells/calendar_cells.ipynb` | [Review](../calendar_cells/VALIDATION_REVIEW_PLAN.md) |
| carry_exit_rule_2026_09 | P0 | `carry_exit_rule_2026_09/carry_exit_rule.ipynb` | [Review](../carry_exit_rule_2026_09/VALIDATION_REVIEW_PLAN.md) |
| chento_journal | P0 | `chento_journal/bootstrap_phase_analysis.ipynb`<br>`chento_journal/chento_limit_bid_v1_backtest.ipynb`<br>`chento_journal/chento_limit_bid_v3_backtest.ipynb`<br>`chento_journal/chento_vs_bot_comparison.ipynb` | [Review](../chento_journal/VALIDATION_REVIEW_PLAN.md) |
| coinbase_premium | P0 | `coinbase_premium/coinbase_premium.ipynb`<br>`coinbase_premium/coinbase_premium_run_A_2026_09_08.ipynb` | [Review](../coinbase_premium/VALIDATION_REVIEW_PLAN.md) |
| delta_neutral | P1 | `delta_neutral/delta_neutral.ipynb` | [Review](../delta_neutral/VALIDATION_REVIEW_PLAN.md) |
| dwell_block | P0 | `dwell_block/dwell_block_research.ipynb` | [Review](../dwell_block/VALIDATION_REVIEW_PLAN.md) |
| execution_2026_09 | P1 | `execution_2026_09/execution_study.ipynb` | [Review](../execution_2026_09/VALIDATION_REVIEW_PLAN.md) |
| fomc_reports | P1 | `compare_with_fomc.ipynb`<br>`fomc_backtest_drilldown.ipynb`<br>`fomc_leverage_sensitivity.ipynb` | [Review](standalone_plans/fomc_reports.md) |
| footprint_study | P2 | Script/report study | [Review](../footprint_study/VALIDATION_REVIEW_PLAN.md) |
| funding_cvd_divergence | P0 | Script/report study | [Review](../funding_cvd_divergence/VALIDATION_REVIEW_PLAN.md) |
| fvg_magnet | P2 | Script/report study | [Review](../fvg_magnet/VALIDATION_REVIEW_PLAN.md) |
| grid_study | P2 | `grid_study/grid_study.ipynb` | [Review](../grid_study/VALIDATION_REVIEW_PLAN.md) |
| hawkes_note_results | P2 | Script/report study | [Review](../hawkes_note_results/VALIDATION_REVIEW_PLAN.md) |
| hedge_tests | P2 | `hedge_tests/hedge_experiments.ipynb` | [Review](../hedge_tests/VALIDATION_REVIEW_PLAN.md) |
| legacy_portfolio_reports | P0 | `backtest_report.ipynb`<br>`full_portfolio_report.ipynb`<br>`portfolio_performance.ipynb` | [Review](standalone_plans/legacy_portfolio_reports.md) |
| lsr_b5_study | P1 | Script/report study | [Review](../lsr_b5_study/VALIDATION_REVIEW_PLAN.md) |
| macd_bottom_note | P2 | `macd_2w_post_ath_bottom.ipynb` | [Review](standalone_plans/macd_bottom_note.md) |
| oi_flush | P0 | Script/report study | [Review](../oi_flush/VALIDATION_REVIEW_PLAN.md) |
| okx_gate_revalidation | P1 | Script/report study | [Review](../okx_gate_revalidation/VALIDATION_REVIEW_PLAN.md) |
| overlay_study | P0 | `overlay_study/overlay_study.ipynb` | [Review](../overlay_study/VALIDATION_REVIEW_PLAN.md) |
| paladin_harvester | P2 | Script/report study | [Review](../paladin_harvester/VALIDATION_REVIEW_PLAN.md) |
| paladin_study | P0 | `paladin_study/paladin_study.ipynb` | [Review](../paladin_study/VALIDATION_REVIEW_PLAN.md) |
| pdo_adjacents | P0 | `pdo_adjacents/pdo_adjacents.ipynb` | [Review](../pdo_adjacents/VALIDATION_REVIEW_PLAN.md) |
| pdo_tv_parity | P0 | `pdo_tv_csv_parse.ipynb`<br>`pdo_tv_validate.ipynb`<br>`pdo_tv_validate_dump.ipynb`<br>`pdo_tv_validate_sweep.ipynb` | [Review](standalone_plans/pdo_tv_parity.md) |
| pool_study | P2 | Script/report study | [Review](../pool_study/VALIDATION_REVIEW_PLAN.md) |
| r4_bot_prep | P1 | `r4_bot_prep/r4_bot_prep.ipynb` | [Review](../r4_bot_prep/VALIDATION_REVIEW_PLAN.md) |
| r4_study | P0 | `r4_study/era_split.ipynb`<br>`r4_study/grid_search.ipynb`<br>`r4_study/sizing_study.ipynb`<br>`r4_study/walk_forward.ipynb`<br>`r4_study/year_breakdown.ipynb` | [Review](../r4_study/VALIDATION_REVIEW_PLAN.md) |
| range_sanity_2026_09 | P0 | `range_sanity_2026_09/range_sanity_2026_09.ipynb` | [Review](../range_sanity_2026_09/VALIDATION_REVIEW_PLAN.md) |
| rsi_divergence_note | P2 | `rsi_divergence_sketch.ipynb` | [Review](standalone_plans/rsi_divergence_note.md) |
| scanner_study | P0 | `scanner_study/scanner_study.ipynb` | [Review](../scanner_study/VALIDATION_REVIEW_PLAN.md) |
| screener | P0 | Script/report study | [Review](../screener/VALIDATION_REVIEW_PLAN.md) |
| short_squeeze_sessions | P0 | `short_squeeze_sessions/discovery.ipynb`<br>`short_squeeze_sessions/strategy_backtest.ipynb` | [Review](../short_squeeze_sessions/VALIDATION_REVIEW_PLAN.md) |
| sizing_style_2026_09 | P0 | `sizing_style_2026_09/sizing_style.ipynb` | [Review](../sizing_style_2026_09/VALIDATION_REVIEW_PLAN.md) |
| squeeze_bull_revalidation | P0 | `squeeze_bull_revalidation/squeeze_bull_revalidation.ipynb` | [Review](../squeeze_bull_revalidation/VALIDATION_REVIEW_PLAN.md) |
| squeeze_recut | P0 | Script/report study | [Review](../squeeze_recut/VALIDATION_REVIEW_PLAN.md) |
| statistical_validation_tools | P1 | `tools_statistical_validation.ipynb` | [Review](standalone_plans/statistical_validation_tools.md) |
| strategy_comparison_2026_09 | P2 | Script/report study | [Review](../strategy_comparison_2026_09/VALIDATION_REVIEW_PLAN.md) |
| swing_base_limit_bid | P0 | `swing_base_limit_bid/discovery.ipynb` | [Review](../swing_base_limit_bid/VALIDATION_REVIEW_PLAN.md) |
| trade_audit | P2 | Script/report study | [Review](../trade_audit/VALIDATION_REVIEW_PLAN.md) |
| validation_audit_2026_09 | P1 | `validation_audit_2026_09/validation_audit.ipynb` | [Review](../validation_audit_2026_09/VALIDATION_REVIEW_PLAN.md) |
| vrp_study | P0 | `vrp_study/vrp_study.ipynb` | [Review](../vrp_study/VALIDATION_REVIEW_PLAN.md) |
| whale_absorption | P2 | Script/report study | [Review](../whale_absorption/VALIDATION_REVIEW_PLAN.md) |

## Scope and verification limits

All 51 original notebooks have exactly one review mapping; all 43 existing non-ORB directories have a plan. All 52 original notebook files, including ORB, match the pre-review byte hashes. No historical notebooks or study code were executed, no databases were queried, and no original source/results were edited for this audit.

The review inspected source, Markdown and relevant saved text/result artifacts. It did not render every embedded chart, independently reproduce numeric performance, verify an external snapshot, or run every code path. 37 original notebooks with code have no saved execution counts; accompanying script runs may still be documented. These limits prevent a blanket claim that testing was done correctly or that everything has been considered.

## Rebuild this documentation

From the repository root, run the repository Python on `studies/notebooks/study_validation_audit_2026_09/write_root_plans.py`, then `build_audit_index.py`. These documentation scripts do not execute a strategy, import study libraries or open databases. The original inventory is a review snapshot; do not regenerate it to conceal changes. A new review should version its inventory separately.

Future empirical tests belong under each plan's stated `studies/notebooks/` path. The overview notebook contains Markdown only; it is a readable plan, not an executed backtest.

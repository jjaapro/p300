"""Write authored static-review plans; never execute a study or read a database."""
from pathlib import Path
import json

HERE = Path(__file__).resolve().parent
BASE = HERE.parent

# Findings refer to the source snapshot inventoried on 2026-09-14.
SPECS = [
    dict(study="legacy_portfolio_reports", title="Legacy portfolio and replay reports", priority="P0",
     notebooks=["backtest_report.ipynb", "full_portfolio_report.ipynb", "portfolio_performance.ipynb"],
     strength="These reports distinguish sleeve attribution and, in full_portfolio_report, explicitly reconstruct marked fixed-capital P&L additively and calculate Sharpe using opening NAV. Those corrections are useful; they do not make the three reports mutually equivalent.",
     findings=[
         "backtest_report cell 6 compounds the output of trades_daily_returns even though the helper's fixed-capital P&L convention is additive. Its exit-date-only series also excludes unrealized risk. Cell 9 starts its drawdown peak after the first reported return, so an initial loss can be missed.",
         "full_portfolio_report cell 11 constructs consecutive simple buy-and-hold returns, then passes them to equity_metrics (cell 9), which sums fixed-capital P&L percentages. The benchmark violates that function's input contract. Analytic example: prices 100 → 110 → 99 produce returns 0%, +10%, −10%; summing reports 0% while fixed-quantity buy-and-hold loses 1%.",
         "portfolio_performance cells 1/3 query whole-variant closed trades while displaying a fixed date window, and its calendar curves book P&L at exit. Cell 3's saved output has an empty tactical book and a zero combined portfolio beside nonzero analytic sleeves; this is not evidence of a validated zero-return portfolio.",
         "backtest_report and full_portfolio_report cell 2 depend on __file__; they are script conversions with commented main calls. Reproduction needs a real notebook entry point, explicit source snapshot and selected variant, not silent fallback capital or a mutable current ledger.",
     ],
     steps=[
         "00_report_contract.ipynb: freeze variant, as-of date, capital, contribution units, start/end boundaries and whether open positions belong in the report. Fail on an absent variant, empty required ledger or missing marks; reconcile the historical saved output before labeling it superseded.",
         "01_accounting_reconciliation.ipynb: use independent fixed-quantity and fixed-capital examples, initial-day loss, cross-year holdings, open positions, deposits, funding, partial exits and concurrent sleeves. Reconcile ending NAV to starting cash plus cashflows and marked inventory to declared monetary tolerance.",
         "02_report_comparison.ipynb: feed the same snapshot and date interval into corrected study-only versions of all three reports. Separate realized cashflow DD, daily marked DD and intraday bounds. Use direct quantity × price for buy-and-hold; align first investment time, idle capital and fees.",
         "03_report_decision.ipynb: export per-trade and per-day reconciliation differences plus before/after metric tables. Attribute every difference to semantics, source, dates or a defect; do not tune a strategy during this exercise.",
     ],
     acceptance="Complete when arithmetic fixtures and ledger/NAV/benchmark reconciliations pass, missing data fails explicitly, and the notebook reproduces from a fresh kernel. These are reporting tools: DSR, broad parameter searches and an alpha holdout are not prerequisites for reporting correctness. Any new portfolio superiority claim needs the separate portfolio plans."),

dict(study="fomc_reports", title="FOMC attribution, phase drilldown and leverage sensitivity", priority="P1",
     notebooks=["compare_with_fomc.ipynb", "fomc_backtest_drilldown.ipynb", "fomc_leverage_sensitivity.ipynb"],
     strength="The family separates event diagnostics, a with/without contribution view and leverage sensitivity. A seeded Monte Carlo and explicit cost convention improve repeatability. The source is useful exploratory scaffolding, not saved execution evidence in these notebooks.",
     findings=[
         "compare_with_fomc cells 4/7 remove FOMC rows from the realized book and book remaining P&L at exit. This estimates contribution within that executed ledger; it does not recompute free capital, skipped trades, position conflicts or marked risk of an executable portfolio without FOMC.",
         "fomc_backtest_drilldown cell 0 compares win rates with phase expectations, and cell 3 joins observer phase labels by FOMC date. The review cannot establish that historical phase labels/expectations were available before each trade or were independently calibrated. Latest labels are not an as-of signal history.",
         "fomc_leverage_sensitivity cell 3 does not load direction, while cell 6 calculates exit/entry−1. A long-only ledger prerequisite must be verified, or shorts are scored incorrectly. Closed-trade compounding and iid event resampling omit intrahold margin, liquidation, funding and clustered event shocks.",
         "The leverage table inspects eleven leverage settings. Its Monte Carlo uses the observed empirical tails and cannot establish safety at 10–30× or bound unobserved losses. These risk claims would be P0 before reuse. All three notebooks retain __file__ setup and commented main calls.",
     ],
     steps=[
         "00_fomc_lineage.ipynb: snapshot all scheduled announcements, changed dates/times, eligibility, skipped signals, historical phase/F&G inputs and their publication times. Freeze one existing rule and its full historical search lineage; record observed versus genuinely uninspected event periods.",
         "01_event_replay.ipynb: reproduce each eligible event at a causal fill time with direction, venue, fees, funding and intraday stop/exit handling. Reconcile logged signals and fills; show all missing/skipped/censored events rather than only closed winners and losers.",
         "02_event_validation.ipynb: use chronological event folds with training-only phase thresholds, event-level purging and joint BTC/ETH time clusters. Set a meaningful net effect and attainable precision before testing; use a simple same-time ungated event rule, exposure-matched random dates and cash as relevant controls. No fixed trade count is proof of power.",
         "03_fomc_portfolio_risk.ipynb: compare a full concurrent portfolio with/without FOMC using identical capital rules. Start with unit notional, then the existing intended leverage; treat additional leverage arms as sensitivity, not a winning leverage search. Replay mark-price margin, funding, spread/latency shocks and gap losses; compare cashflow attribution with executable incremental results.",
     ],
     acceptance="Attribution may be completed by reconciliation alone. An edge claim requires valid as-of data and engine plus a positive, sufficiently precise net incremental effect under the frozen rule. Insufficient events are inconclusive. No leverage or live-order decision follows from this retrospective plan."),

dict(study="pdo_tv_parity", title="PDO TradingView parity and CSV reconciliation", priority="P0",
     notebooks=["pdo_tv_csv_parse.ipynb", "pdo_tv_validate.ipynb", "pdo_tv_validate_dump.ipynb", "pdo_tv_validate_sweep.ipynb"],
     strength="The notebooks state the intended Pine parity question, disclose filters absent from that comparison, and provide per-trade dumps and bounded sensitivity sweeps. Reproducing another engine is a legitimate objective distinct from proving a tradable edge.",
     findings=[
         "pdo_tv_csv_parse cell 4 excludes Margin call slices and calls DayEnd/HoldLimit rows the real trades. Those cashflows are economically real parts of the exported position history. Removing them cannot establish reconciled net profitability or account drawdown; percent-return sums/compounding also require the actual sizing and partial-exit denominator.",
         "pdo_tv_validate cell 3 aggregates available minutes without enforcing complete hours; cell 6 uses the first observed hour as day open and previous observed day as PDO. Missing days/hours can change the intended calendar rule and make HOLD_BARS count observations rather than elapsed hours.",
         "The validation engines' end-open position is not marked, and drawdown follows closed-trade equity. Header slippage assumptions and actual fills must be reconciled. A static printed TradingView reference is not a matched, versioned export.",
         "The sweep's gap/tolerance/hold loops contain 20 evaluations and 18 unique parameter combinations on the same sample; that is parity sensitivity, not out-of-sample evidence for a selected winner. __file__, legacy database paths and hard-coded export locations obstruct fresh-kernel reproduction; the dump writes outside the preferred study output tree.",
     ],
     steps=[
         "00_pdo_parity_contract.ipynb: freeze Pine source/version, chart exchange, interval, chart timezone, bar magnifier/order processing, margin, commission, slippage, quantity and date range. Copy authorized inputs into a study-local manifest; never infer the timezone from machine locale.",
         "01_csv_cashflow_reconciliation.ipynb: preserve every export row and group entries, partial exits, margin calls, fees and open quantities by position/order identity. Reconcile quantities, realized dollars and account equity to the complete export; malformed/unmatched records must produce explicit failures.",
         "02_pdo_bar_and_engine_parity.ipynb: require complete calendar bars or a frozen exclusion rule; test midnight, missing prior day, 24-hour timeout, boundary touch, long/short arithmetic, gap, same-bar collision and open final positions. Compare exact signal, entry, exit and quantity records with declared tick/time tolerances.",
         "03_pdo_parity_verdict.ipynb: retain all 18 unique sweep configurations as diagnostic history, freeze one target configuration and explain unmatched trades. Save a corrected reconciliation table without deleting the original exports. Economic testing, if desired, belongs to a distinct future plan linked to pdo_adjacents/VALIDATION_REVIEW_PLAN.md.",
     ],
     acceptance="Pass only when the full export and study ledger reconcile, including margin calls/partial exits, with all exceptions explained and stable notebook execution. Matching a TradingView run with poor margin settings does not validate profitability. A parity-only exercise needs no DSR or arbitrary holdout."),

dict(study="bitstamp_adx_parity", title="Bitstamp ADX Pine and service parity", priority="P1",
     notebooks=["bitstamp_adx_backtest.ipynb"],
     strength="Cell 0 explicitly limits the notebook to signal-level parity and discloses spot data, costs and omitted funding. It compares current, TradingView-cross, stateful and service-style machines, which is useful for diagnosing semantic differences.",
     findings=[
         "Cell 11 opens at the daily close and then uses that same day's low/high in its worst-point MTM calculation. Those extrema predate the position. It tracks peaks only at those worst-point marks, so the reported MTM drawdown is not a complete NAV-path drawdown either. Do not reuse this risk metric without correction.",
         "Stops fill exactly at their daily level without a gap-through rule. The final position is appended as still_open with a cost-adjusted mark, then included beside completed trades; realized and marked statistics need separate contracts. Cell 13 entry-year cohorts are not calendar marked annual returns.",
         "Cell 2 requires __file__; cell 7's cache loader may fetch/write under data/. Service parity imports mutable current production implementations. The notebook has no saved execution counts establishing a fresh-kernel run.",
     ],
     steps=[
         "00_adx_parity_contract.ipynb: freeze price snapshot, Pine export, indicator warmup, process_orders_on_close setting, state reset/reversal rules and production dependency revision. Preserve intentionally buggy legacy variants as labeled diagnostic controls.",
         "01_adx_state_fixtures.ipynb: use independent short synthetic sequences for threshold equality, entry/reversal/exit, daily gap stop, same-bar collision, warmup and open terminal positions. Restrict risk marks to periods after entry and include initial capital.",
         "02_adx_parity_report.ipynb: reconcile signals and trade timestamps/prices at declared precision; separate executed trade metrics, open marks, entry cohorts and daily NAV. Write caches/results inside a new study-local run directory with no implicit download.",
         "If the question becomes economic robustness, reuse the current adx_robustness_2026_09 and adx_eth_2026_09 review plans after their engine blockers are resolved; do not use this spot parity notebook as a shortcut to perpetual leverage or funding conclusions.",
     ],
     acceptance="Signal parity can pass with exact frozen-engine agreement and correctly labeled scope. Economic or risk claims remain unsupported until execution and accounting are corrected. Out-of-sample strategy selection is not needed merely to verify Pine semantics."),

dict(study="bitstamp_thu_parity", title="Bitstamp Thursday-bear Pine parity", priority="P1",
     notebooks=["bitstamp_thu_bear_backtest.ipynb"],
     strength="Cell 0 clearly distinguishes Pine's day-of-month event approximation and Friday exit from the service's actual calendar and exit time. The prior Wednesday EMA in cell 14 is a causal daily feature for the stated hourly-close rule.",
     findings=[
         "Cells 13/14 use day-of-month NFP/CPI/OPEX proxies and Pine timing. These may be appropriate for parity but do not validate the actual event-filtered service. The distinction already documented in cell 0 must remain visible in any performance claim.",
         "Cell 14 fills daily-gap stops at the stop price and leaves final open positions outside the closed-trade statistics. Cell 16's MaxDD is closed-trade drawdown, not marked account risk.",
         "Cell 17 applies UTC+3 to a CSV comparison. The export's actual declared chart timezone must establish whether this is a fixed offset or a timezone with DST; the code does not establish it. Partial/missing exported rows and trade-number matches need explicit reconciliation.",
         "__file__, argparse-oriented orchestration, and cache fetch/write paths under data/ prevent treating the converted notebook as a clean, isolated executed study.",
     ],
     steps=[
         "00_thu_parity_inputs.ipynb: freeze the Pine source/chart/export, timezone, calendar approximation, exact bar-close convention, costs and snapshot. Record the separate service specification without merging its rules into the parity target.",
         "01_thu_state_and_calendar.ipynb: test Thursday boundary entry, Friday exit, Wednesday indicator availability, event-date edge cases, timezone transitions where applicable, missing bars, stop gaps and terminal exposure. Compare complete trade identities and prices against the export.",
         "02_thu_reconciled_report.ipynb: show realized and marked returns, monetary fee/quantity reconciliation, unexplained mismatches and fresh-kernel execution. Keep all new cache/output files local to the study.",
         "Only if service performance is the new question, register a separate actual-calendar replay with as-of announcement times, causal next fills, perpetual costs/funding, chronological validation and an unfiltered Thursday baseline. Count any chosen calendar/time variants in the historical search family.",
     ],
     acceptance="Accept exact parity only for the frozen Pine specification; it is not validation of the production event rules. Any missing chart metadata or unresolvable trade mismatch is inconclusive. No additional broad strategy sweep is needed for parity."),

dict(study="macd_bottom_note", title="MACD post-ATH bottom description", priority="P2",
     notebooks=["macd_2w_post_ath_bottom.ipynb"],
     strength="The notebook distinguishes cross proximity to a historical low from strict/ultimate subsequent lows and retains an open-cycle caveat. Those are useful descriptive questions about a small number of market cycles, not an executable exit rule.",
     findings=[
         "The title and narrative say 2W, but cell 1 sets BAR_DAYS=7 and the saved output in cell 2 confirms 7d bars. Any claim specifically about a tested two-week signal is unsupported by this saved run.",
         "Cell 2 reports interior gap bars and retains them. Cell 1's root search still expects jplus/. Dataset lineage, bucket completeness and the difference between displayed bar start and actionable bar close must be resolved before reproduction.",
         "Cells 5/6 examine proximity bands on the same eleven cycles. Cells 7/8 use future lows, next ATH/bear boundaries and future peak closes. These are retrospective labels; they cannot be entry features or realized exit profits. Shared/overlapping cycles and one unresolved cycle limit inferential precision.",
     ],
     steps=[
         "00_macd_description_reproduction.ipynb: freeze weekly versus biweekly intent explicitly, anchor, warmup, gap exclusion and bar-close timestamps. Reproduce the saved weekly result as historical description first; treat a biweekly version as an additional previously inspected choice.",
         "01_macd_cycle_uncertainty.ipynb: retain every qualifying cross and cycle, show censored labels, sensitivity to cycle definitions and the full band table, and report counts rather than headline percentages alone. Compare all bullish MACD crosses and simple time-matched descriptive controls without optimizing the band.",
         "If there is a new trading question, 02_macd_causal_policy.ipynb must freeze an attainable entry and non-clairvoyant exit, costs and sizing. Use chronological cycle-level validation and honest uncertainty about the few independent eras; BTC/ETH transfer is supporting evidence, not many independent cycles.",
     ],
     acceptance="Keep as a descriptive/archived study unless a new use case is chosen. Correct timeframe metadata and label timing before citation. A high share of retrospectively identified bottoms or a future-peak return is not an investable win rate; no profitability or leverage claim follows."),

dict(study="rsi_divergence_note", title="RSI divergence sketch", priority="P2",
     notebooks=["rsi_divergence_sketch.ipynb"],
     strength="Cell 0 candidly records failure in W1–W3, favorable W4 results and a parked conclusion. That is appropriate negative evidence for the tested rule; the later diagnostic ideas are visibly exploratory.",
     findings=[
         "Cell 1's window_mask includes each end date by adding a day, while the next window begins on the same date. Adjacent windows therefore overlap a boundary day. They are fixed-period comparisons, not a fitted walk-forward procedure.",
         "Cell 4 uses same-close information and fills, without a latency model. Its 10× liquidation proxy is based on hourly closes and a 1/leverage threshold, omitting intrabar marks, maintenance margin and funding. That cannot support liquidation safety. The RSI calculation's zero-loss case becomes NaN and needs a declared flat/rising-market convention.",
         "Cell 7 includes late crossings without a full future horizon in reach-probability denominators. Cell 8's suggested rolling gate arose after seeing W4 and future reach outcomes; future-gate decisions may use only already matured outcomes. The root finder and legacy database path also need modernization in a study copy.",
     ],
     steps=[
         "00_rsi_reproduction.ipynb: reproduce both failed directions with explicit data hash, RSI zero-loss convention, disjoint half-open windows and event counts. Correctly mark incomplete future reach labels and end-open trades; preserve the original parked decision.",
         "01_rsi_engine_bounds.ipynb, only if reused: compare causal next-open fills against the original same-close diagnostic, model actual long/short costs and perpetual funding, and bound intrahour stop/mark risk. Remove the close-only liquidation proxy from any economic conclusion.",
         "A new rolling gate is a separate candidate family. Freeze its matured-outcome horizon and update schedule, compare against ungated divergence and plain RSI rules, train only on preceding data, purge overlapping labels and use a genuinely later evaluation window. Do not keep searching W4 until a gate passes.",
     ],
     acceptance="No rerun is required to keep the idea parked. Before reuse, reconcile the stated defects and assess a prespecified net effect with dependence-aware uncertainty. Failure of this rule does not disprove every RSI strategy; W4 alone does not rescue it."),

dict(study="statistical_validation_tools", title="Paper-ledger statistical report", priority="P1",
     notebooks=["tools_statistical_validation.ipynb"],
     strength="The source uses zero-filled calendar dates, seeded bootstrap sampling, explicit annualization and additive fixed-capital CAGR/drawdown. It provides useful descriptive stability and correlation diagnostics with no intended database writes.",
     findings=[
         "Cells 4/5 use first-to-last closed-trade exit dates and realized P&L. Long holdings and open trades disappear from the risk path. A precise bootstrap of that cashflow series is not a confidence interval for marked trading risk.",
         "Cell 15 resamples individual days independently; BCa bias correction does not preserve holding-period or volatility dependence. It also has unhandled degenerate cases when the bias proportion reaches 0 or 1, and no explicit empty-input guard. Calibration should precede reuse as a general validator.",
         "Cell 21 labels correlation as daily log returns although cell 19 converts BTC to simple returns. Correlation thresholds are used to narrate BTC-beta dependence, but correlation alone is not beta, explained return or independence. Rolling windows overlap and are descriptive, not independent tests.",
         "The notebook is a converted script with __file__ and commented main, no saved execution record, and no family search correction or untouched evaluation. Those omissions are acceptable for a labeled ledger report but not a selected-strategy validation claim.",
     ],
     steps=[
         "00_statistical_input_contract.ipynb: freeze variant, snapshot, full observation window and fixed-capital versus opening-NAV returns. Show realized and marked paths separately, reconcile funding/fees/open inventory, and explicitly fail missing or nonfinite input.",
         "01_statistical_calibration.ipynb: test zero/constant series, one observation, isolated losses, skewed iid and dependent synthetic series, bootstrap edge probabilities and known equity curves. Measure empirical interval coverage across simulated datasets, not just agreement with the implementation.",
         "02_paper_uncertainty.ipynb: use paired time-block/stationary resampling with justified block lengths and an actual declared confidence level; show interval sensitivity, effective time coverage and nonpositive-equity handling. Estimate BTC beta and residual uncertainty if that is the question; keep rolling/year charts descriptive.",
         "For a selected strategy's edge claim, attach the strategy's historical trial family and future validation design. Do not invent N_TRIALS=1 merely because this reporting notebook makes one call.",
     ],
     acceptance="Report correctness requires accounting and estimator calibration, fresh-kernel execution and accurately labeled uncertainty. Economic significance additionally requires a valid design for the strategy being reported. Small live samples remain inconclusive even if a descriptive Sharpe is high."),

dict(study="execution_2026_09", title="Execution cost and fill-policy study", priority="P1", local=True,
     notebooks=["execution_2026_09/execution_study.ipynb"],
     strength="README and findings document E0 parity, spot/perpetual identity, 1-minute and 5-second sensitivity, fill-through alternatives, block intervals, small stress samples and an unrun E7 probe. The recorded decisions and subsequent authorized cost changes are historical facts; this audit does not reverse them.",
     findings=[
         "findings.md calls the modeled taker cost measured and concludes execution is the fee and almost nothing else. exec_lib.py fixes fees, spread/adverse-selection assumptions and zero impact; bars do not measure account fees, order-book queue, attainable maker status or actual fills. The caveats are more defensible than that headline.",
         "run_e4_stop_slippage.py:21–55 computes gap/stop slippage from the same OHLC stop walker. Zero modeled gaps means no opening-gap slippage in those sampled bars; it does not establish zero intrabar stop-market slippage or exact live fills. Polling tails and sparse stressed samples are material.",
         "E2/E3 touch/through models cannot establish that a resting order actually filled at a maker fee; marketable orders may take or be rejected under post-only. The narrow SHORT_SQUEEZE stop is particularly exposed to the documented spot/perpetual basis mismatch and sparse 5-second event sample.",
         "Entry lists are inherited from upstream studies. Parity with those lists validates reproduction, not their causal selection or engine correctness. The ADX stop-list check cannot detect omitted stop events absent from its source ledger. exec_lib.py:130–138 reuses existing caches unless force is set, without checking source hashes; cache and result lineage must be frozen for a current replay.",
     ],
     steps=[
         "00_execution_lineage_review.ipynb: map each sleeve's source ledger, code revision, asset/venue path, cache hash and cost convention; reconcile gross versus net R and distinguish original runs from later cost adoption. Resolve upstream engine issues before using a ledger as truth.",
         "01_execution_fill_bounds.ipynb: test causal order arrival, entry-bar stops, stop/target collisions, gaps, timeout, unfilled limits, maker rejection and cancellation races. Compare touch, through and conservative partial/no-fill bounds; retain all intended signals and skipped opportunity cost.",
         "02_execution_venue_costs.ipynb: separately evaluate true perpetual paths where available and matched spot-proxy sensitivity. Freeze dated account fee tiers when supplied, per-leg charges, spread, latency and stressed impact ranges. Report cost distributions and tail losses, not only mean drift near zero.",
         "03_execution_policy_validation.ipynb: use paired same-signal and time-block comparisons, both historical halves and a later frozen observation period. Require confidence/precision for net incremental policy benefit and trade retention, with actual uncertainty from the small stress cohorts.",
         "04_execution_observation_design.ipynb: specify passive quote/trade telemetry and, only as a separate later authorized task, any real-order probe. No orders, keys, purchases or live probes are part of this review. Paper price paths cannot settle queue or subsecond fill questions.",
     ],
     acceptance="The existing results can support modeled cost sensitivity within their data resolution. A claim about actual execution requires relevant observed fills/quotes and calibrated uncertainty. If venue/queue evidence is absent, retain explicit bounds and an inconclusive actual-fill verdict instead of declaring zero slippage."),

dict(study="brainstorm_validation_2026_09", title="Brainstorm claim replication", priority="P1", local=True,
     notebooks=["brainstorm_validation_2026_09/brainstorm_validation.ipynb"],
     strength="This is one of the stronger rejection studies: frozen claim-specific clauses, exact external-cache replication, historical/asset transfer, fees and turnover, explicit failed reproduction, cluster-aware statistics, and prior search counts. C0 separately compares fetched realized funding against the mixed production table; C2 uses the fetched history rather than assuming mod8h forecasts are settlements.",
     findings=[
         "The blanket N_TRIALS=1 label refers to the new replication tests, while selected hypotheses came from the larger documented search. Existing deflation calculations recognize that distinction; future summaries must not erase it or treat five claims plus all secondary analyses as a single unselected test.",
         "The notebook runs C0 then later scripts, while run_c0_data_parity.py:149–156 writes and prints gate values. A future orchestrator needs explicit required-gate enforcement and hash linkage, not execution order alone. Identical copied cache data is replication, not independent data.",
         "New historical years and correlated crypto assets test transportability; they are not automatically untouched chronological validation. C5's unreproduced H24 cell must remain unreproduced rather than being absorbed into a general claim that all arithmetic matched.",
         "C4's bar-phase and fill-model sensitivity supports sensitivity under those engines. It does not validate all ADX implementations or exact live stops; the newer ADX robustness harness has separate first-day-stop/accounting issues. Keep engine-specific conclusions and dependency revisions explicit.",
     ],
     steps=[
         "00_claim_reproduction_manifest.ipynb: tie each C0–C5 result to the external cache hash, local input hash, dependency commit, original search family and the exact frozen clause. Fail downstream execution on any required parity/data gate; preserve recorded deviations.",
         "01_replication_closure.ipynb: maintain one row per claim, separating exact parity, transfer evidence, costs and final decision. Resolve H24 provenance only if needed for reuse; do not search for an alternative H24 definition that recreates the headline.",
         "02_engine_sensitivity_review.ipynb, if C4 infrastructure is reused: test causal phase boundaries, entry-day stops, long/short return units, open positions and MTM reconciliation with independent fixtures before replay. Compare phases jointly and retain the original chosen UTC convention rather than optimizing it.",
         "Only new evidence or a changed economic question should reopen killed C1/C2/C3/C5 ideas. Register a new family with inherited trials, as-of data, net comparator and precision target; old failed tests remain in the record.",
     ],
     acceptance="Preserve the original rejection decisions and explicitly bounded C4 sensitivity findings. Completion is provenance/gate reconciliation and narrowly correct claims; rerunning broad searches to rescue killed ideas is unnecessary. Future adoption needs its own causal and economic validation."),

dict(study="validation_audit_2026_09", title="Earlier project validation audit", priority="P1", local=True,
     notebooks=["validation_audit_2026_09/validation_audit.ipynb"],
     strength="The notebook identifies itself as report-only, loads saved result files, reports that nothing clears the stated DSR standard, reconciles zero-cost/long-hold and costed/short-hold claims, and discloses partial PBO coverage and sparse paper evidence. Those disclosures remain valuable.",
     findings=[
         "Reproducing a loader's published point estimate (cells 8/10) does not establish its engine or source labels are correct. The current review finds upstream causal, funding, first-holding-day and MTM problems that can propagate into this audit's input vectors.",
         "Cells 13/14 disclose PBO only for the saved four tilt alternatives, omitting other searched branches whose matrices were unavailable. That is a partial-family diagnostic, not a complete search correction. Daily closed-trade or signal-return inputs must not be interpreted as a reconciled live account NAV.",
         "The closing narrative in cell 19 treats point estimates as real and breadth as the binding limitation. A cautious restatement is that saved point estimates reproduced under the tested definitions; data/engine validity and actual economic edge remain separate unresolved questions.",
         "Paper counts and deployment status in cell 5 are an as-of historical snapshot. They should not be displayed as current facts without a new explicit observation cutoff. A report-only notebook does not record execution of the source audit scripts in its own cells.",
     ],
     steps=[
         "00_audit_dependency_graph.ipynb: map every reported number to source study, input return definition, dependency revision, data hash and original validation clause. Mark affected descendants when a causal/data/accounting source is corrected.",
         "01_validation_helper_contracts.ipynb: verify actual helper behavior and units for DSR, PBO selection statistic/remainder, CPCV refitting/purge, bootstrap confidence levels and net equity. Use analytic or independent simulation checks appropriate to each metric before recalculation.",
         "02_audit_reconciliation.ipynb: preserve historical values, regenerate only affected inputs after source corrections, and show before/after plus full/partial family coverage. Missing search branches are an explicit uncertainty/lower bound, not silently N_TRIALS=1.",
         "03_audit_verdicts.ipynb: separate reproduction, validity, statistical precision, selection correction, portfolio impact and current paper evidence. Freeze any genuinely new evaluation period and observation rules before inspecting it.",
     ],
     acceptance="Complete when each claim has a traceable valid input and a conclusion no broader than its check. An old low DSR remains insufficient evidence, while a corrected point estimate does not itself establish an edge. No need to rerun unaffected rejected studies."),

dict(study="okx_gate_revalidation", title="OKX gate causal revalidation", priority="P1", local=True,
     notebooks=[],
     strength="The detailed README/addenda and findings preserve frozen source commits, a hashed external snapshot, causal and same-hour controls, parity gates, power/precision limits, a final RETIRE clause and explicit separation of discrimination from portfolio drawdown. This is a substantially specified study; a new generic search plan would weaken its discipline.",
     findings=[
         "findings.md:36–40 explicitly says the same-hour control also fails, so the final verdict does not isolate the look-ahead premise. Lines 84–108 clarify that RETIRE means discrimination was not demonstrated while profitable trades were blocked; it is not proof the gate is useless or that chento is statistically validated.",
         "The study is script/report based; no .ipynb exists here. Its external snapshot and result links are documented but this static review did not independently rerun or re-hash that snapshot. Preserve those limitations rather than asserting an independent reproduction.",
         "The report-only all-trades drawdown and 2.25× fire-rate increase imply a distinct sizing/concurrency question. Positive kept-minus-blocked R and row counts alone do not establish the executable portfolio impact of disabling the gate.",
         "findings.md:163–174 already records that the report-only production-sequence approximation used losing predecessors before they closed and differed from the bot's 48-hour rule. Those particular portfolio figures must not be reused; the final gate verdict did not depend on them. Lines 152–161 also disclose cross-checkout hash/line-ending issues and omitted funding.",
         "findings.md lists downstream studies using same-hour OKX values and forbids treating the causal re-test as their automatic recut. Each dependent overlay/attribution/filter requires its own as-of replay; new replacement gates inherit the documented search family.",
     ],
     steps=[
         "00_okx_verdict_reproduction.ipynb: create a report notebook that reads a pinned copy/export of the original snapshot and saved outputs, displays hashes and original clauses, and reproduces clause arithmetic without reopening the final decision or rewriting its preregistration.",
         "01_okx_dependency_review.ipynb: verify causal feature availability and compare R1/R2/same-hour controls using truncation tests. Enumerate all descendants and link each to its own review. Confirm any truncated/partial folds and actual information exposure in reports.",
         "02_okx_portfolio_impact.ipynb: for a separately frozen operational sizing question, replay OFF/gated arms jointly with actual sequencing, concurrent positions, capital caps, marked risk and event-time previous-outcome availability. Freeze risk limits before measuring; use paired calendar comparisons and tail stress.",
         "Keep the historical RETIRE outcome intact. Any replacement z threshold, venue or sizing gate is a new preregistered family with inherited trials and a new evaluation window, not an extension that can silently reverse the old verdict.",
     ],
     acceptance="Research reproduction is complete only with a verified manifest and exact clause table. Portfolio feasibility is a separate pass/inconclusive decision based on its frozen risk budget. No flag, bot, snapshot location or production schedule is changed by this audit."),

dict(study="range_sanity_2026_09", title="Range sanity and first-break fades", priority="P0", local=True,
     notebooks=["range_sanity_2026_09/range_sanity_2026_09.ipynb"],
     strength="The notebook explicitly calls these non-preregistered falsification/sanity checks and labels the maker arm as cost-only, with no fill simulation. Negative findings are useful for shelving the tested hypotheses without a larger search.",
     findings=[
         "b_basis_funding_asia.py's first-break fade outcome calculation approximates gross R as 2×target_hit−stop_hit, giving timeout trades zero gross return instead of marking their actual exit. It starts the stop/target walk strictly after the first breach bar, so an entry/fill followed by an adverse move within that bar is omitted.",
         "Session eligibility permits incomplete Asia/rest sessions rather than requiring the stated full windows. Quantify missing-time effects and whether the first observed breach was actually the first; do not silently replace unavailable bars with clean sessions.",
         "The funding path samples the historical mixed funding table at eight-hour timestamps. Pre-cutover forecasts are not established realized cashflows. Earlier minute-based saved outputs also need lineage comparison against the September 7 BTC repair before being reused.",
         "Cost-only maker discounts and exploratory LVN/profile alternatives do not demonstrate attainable fills or a fully defined tradable policy. Negative numbers reject the modeled variants, not every intraday range idea.",
     ],
     steps=[
         "00_range_reproduction.ipynb: preserve the old negative result and map every A/B/C arm, threshold, session, cost and source snapshot. Record actual session coverage, time labels and exclusions; distinguish spot features from any perpetual economics.",
         "01_range_path_fixtures.ipynb, before engine reuse: test breach-bar entry/stop/target ordering, gap fills, a timeout at a gain/loss, missing first breach, midnight and simultaneous alternatives. Mark actual timeout prices and report OHLC ambiguity bounds where ordering cannot be resolved.",
         "02_range_corrected_baseline.ipynb: run only the existing frozen arms after data/engine fixes, with realized settlement funding where applicable and consistent per-leg costs. Compare old/new trade lists and net R with paired time-block uncertainty. Do not add a grid to compensate for failure.",
         "A new LVN/range strategy would require its own training-only profile construction, event-time feature availability, executable fill rule, unfiltered/simple-fade controls, trial ledger and later validation period under the shared protocol.",
     ],
     acceptance="Keep rejected ideas archived. P0 applies to reuse of the current timeout/entry-bar engine and exact economic claims, not a requirement to revive this study. A corrected negative result closes the question for that specification; wide uncertainty is inconclusive."),

dict(study="hawkes_note_results", title="Hawkes event-clustering descriptive note", priority="P2", local=True,
     notebooks=[],
     strength="The parent hawkes_note.md explicitly defines descriptive gates and two distinct nulls, explains the stationarity/rate confound, records the binary-bin clause problem, labels the N3 addendum post hoc, and parks the idea. It correctly avoids Sharpe/PBO machinery for a non-return statistic.",
     findings=[
         "The note's binary rising-edge streams cannot satisfy its original hourly over-dispersion condition; the note discloses this and explains why the verdict does not change. Preserve that literal original clause and its recorded deviation, rather than retrospectively replacing the preregistration.",
         "addendum_slow_rate.py:9–13 uses a centered rate estimated from the same observations and calls residual clustering genuine self-excitation. The code/note can show a descriptive mismatch with that particular null; it cannot identify a unique excitation mechanism, a causal forecast or the benefit of a Hawkes model.",
         "The full-sample percentile thresholds and centered smoother are legitimate descriptive choices but would leak future information in prediction. Two hundred null replications provide limited precision for 99th-percentile thresholds, and multiple streams/bin widths/lags need simultaneous inference if a new formal discovery claim is made.",
         "The archived liquidity-feed span and funding-era restriction are historical inventory facts. The plan did not requery present coverage. A monotone ladder in this sample does not prove no future dataset or shorter kernel could ever yield a useful model.",
     ],
     steps=[
         "00_hawkes_description.ipynb: collect the existing note, result JSON and figures into a reproducible notebook with data/source hashes, exposure masks, event definitions, bin boundaries and original/deviated clauses. Preserve the PARK outcome.",
         "01_hawkes_null_calibration.ipynb, only if the statistic is reused: simulate homogeneous and inhomogeneous Poisson, renewal/refractory and known Hawkes processes, matching missing exposure and discretization. Verify false-positive rates, estimator bias, bin-edge sensitivity and coverage with enough replications for the declared tail level.",
         "A future predictive use is a new question: freeze trailing-only rate estimation and thresholds, compare held-out point-process likelihood/calibration against a simple time-varying-rate model, and use chronological event windows. Economic evaluation is required only if that forecast is translated into an execution/risk policy.",
     ],
     acceptance="Descriptive completion needs calibrated statistics and limited interpretation, not profitable returns or a DSR. Revisit only for a specified new data/method/use-case gate. More model complexity or a large Fano factor is not evidence of trading value."),

dict(study="trade_audit", title="Historical behavior compliance and paper/sim audit", priority="P2", local=True,
     notebooks=[],
     strength="findings.md separates historical paper and replay variants, identifies inconsistent P&L field units, explains configuration cutovers and reports missing sleeve coverage and tiny sample sizes. It flags specific late cold fills instead of treating the entire book as validated.",
     findings=[
         "The claim that 203/203 trades obey timing/asset/direction checks establishes those checks, not full strategy-logic compliance. It does not test indicator reconstruction, missing eligible signals, sizing, costs, duplicate orders, exit paths or idempotent restart behavior.",
         "The scripts contain hard-coded variant/database assumptions and direct sqlite3.connect(DB), without mode=ro enforcement. The June 7 enabled-variant convention and counts are an as-of snapshot; they are not a reliable current classifier for paper, simulated and real execution histories.",
         "Mean return / win-rate differences from different historical periods and n≤6 paper trades cannot establish matched paper-versus-simulator agreement, improvement or drift. Configuration history needs precise effective timestamps, and replay P&L percent fields have different denominators across sleeves.",
         "No notebook exists in this folder. Static findings do not show a reusable fresh-kernel reconciliation workflow, complete event coverage or a verified root cause for every anomaly.",
     ],
     steps=[
         "00_trade_audit_snapshot.ipynb: freeze ledger and configuration history at an explicit cutoff, use read-only access and select runs by stable identity/execution metadata. Normalize units and preserve open trades, partial fills and missing fields.",
         "01_behavior_event_replay.ipynb: reconstruct all eligible signals, including no-trade decisions, and compare decisions/fills/exits by event identity against the configuration active then. Test restart/cold start, delayed poll, duplicate event, missed window and changed weekday rules using deterministic fixtures.",
         "02_paper_sim_reconciliation.ipynb: run the same historical paper observation window through the frozen simulator, compare expected versus actual signal and fill times, prices, quantities, costs and reasons, and explain every unmatched event. Separate model error from operational deviation and deliberate configuration changes.",
         "03_trade_audit_closure.ipynb: publish anomaly IDs, expected/observed behavior, cause or unresolved status and evidence. A future performance-monitoring plan needs a predetermined observation window and drift/precision rules; never infer validation from six winners.",
     ],
     acceptance="Historical behavior findings may stay archived. A renewed audit passes only its explicitly checked behaviors with complete event reconciliation; unresolved/missing data is inconclusive. No alpha search or multiple-testing correction is needed merely to prove schedule/idempotency correctness."),
]


def main():
    index = []
    for spec in SPECS:
        local = spec.get("local", False)
        folder = BASE / spec["study"] if local else HERE / "standalone_plans"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / ("VALIDATION_REVIEW_PLAN.md" if local else spec["study"] + ".md")
        protocol = "../study_validation_audit_2026_09/VALIDATION_PROTOCOL.md" if local else "../VALIDATION_PROTOCOL.md"
        refs = "\n".join("- `" + n + "`" for n in spec["notebooks"]) or "- Script/report study; no existing notebook in this directory."
        lines = [f"# {spec['title']} — validation review and future plan", "",
                 "Date: 2026-09-14. Static review of current source, notebook text/saved outputs and supporting reports. No study executed; historical results were not independently reproduced. Notebook cell references are zero-based.", "",
                 f"**Priority: {spec['priority']}.** Scope: retrospective review plus proposed follow-up; this is not the original preregistration. Preserve existing source, frozen decisions and saved results. Priority describes prerequisites for reusing evidence, not an instruction to change production.", "",
                 "## Coverage", "", refs, "", "Paths above are relative to `studies/notebooks/`.", "",
                 "## What was done well", "", spec["strength"], "",
                 "## Findings and limits", ""]
        lines += [f"{i}. {text}" for i, text in enumerate(spec["findings"], 1)]
        lines += ["", "## Proposed notebook work", "",
                  "The names below are future artifacts, not completed tests. For top-level legacy notebooks, use `studies/notebooks/study_validation_audit_2026_09/followups/<family>/`; for a named study directory, use a new `validation_followup/` beneath that directory. Keep all caches, manifests and results alongside those future notebooks.", ""]
        lines += [f"{i}. {text}" for i, text in enumerate(spec["steps"], 1)]
        lines += ["", "## Frozen decisions and stopping rules", "", spec["acceptance"], "",
                  "Before the first follow-up run, freeze its exact primary question, data cutoff, inputs, costs, comparator, numerical tolerances/economic effect and any evaluation windows. Previously inspected results remain development evidence. Stop when a prerequisite fails; do not tune around it or rewrite historical clauses.", "",
                  "## Required evidence and applicability", "",
                  f"Apply the [shared validation protocol]({protocol}) where relevant. It specifies lineage, event-time causality, funding/fee semantics, accounting, selection correction, uncertainty, stress and decision labels. The tailored scope above takes precedence over a generic demand to run every statistical test.", "",
                  "- Save a source/data/environment manifest and complete fresh-kernel execution record for each actual follow-up.",
                  "- Save claim-to-result and before/after reconciliation tables, exclusions, unresolved issues and deviations.",
                  "- For a trading result, expose signal/trade/cashflow/calendar-NAV ledgers and actual parameter history; for a descriptive/parity/report result, expose its corresponding matched records and calibration checks.",
                  "- Mark unavailable data, unresolved ordering and inadequate precision as unevaluable/inconclusive. Keep hypothetical outcomes distinct from observed fills and original saved results.",
                  "- No empirical pass, repaired strategy, new trading performance or production change is claimed by this review.", ""]
        dest.write_text("\n".join(lines), encoding="utf-8")
        index.append(dict(study=spec["study"], review_path=dest.relative_to(BASE).as_posix(),
                          status="static_review_complete_followup_conditional" if spec["priority"] == "P2" else "static_review_complete_validation_needed",
                          priority=spec["priority"], key_findings=spec["findings"], notebooks_reviewed=spec["notebooks"],
                          scope_limitations="Static source/text-output review; no database query, notebook execution, independent historical reproduction or rendered-chart inspection."))
    (HERE / "root_review_index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(index)} root-owned review plans")


if __name__ == "__main__":
    main()

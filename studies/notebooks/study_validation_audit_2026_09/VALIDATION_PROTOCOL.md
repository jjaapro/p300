# Protocol for the next study validation round

Written 2026-09-14, after reviewing existing results. This is a retrospective audit and a prospective work plan, **not an original preregistration**. No strategy was run during this review. Each linked study plan narrows this protocol to its actual question; reporting tools, parity checks, descriptive statistics and rejected ideas do not need an alpha-search pipeline simply because they are notebooks.

## 1. What the review establishes

- **Static defect:** a specific source path conflicts with its stated semantics. Reproduction and materiality remain future work unless explicitly demonstrated analytically.
- **Evidence gap:** the available source/results do not establish the claim. This is not proof the strategy loses money.
- **Recorded result:** a historical finding or saved output, not a newly verified outcome.
- **P0:** correct or bound affected evidence before reusing it for the stated decision. This is not an instruction to change a running bot.
- **P1:** additional decision-bearing validation is needed if the study is pursued.
- **P2:** descriptive, archived, rejected or otherwise conditional follow-up. Preserve the recorded decision unless a distinct new question justifies revisiting it.

## 2. Freeze the question before the next result

For each actual follow-up, save a dated, versioned configuration before executing it. Record: hypothesis; primary estimand and comparator; instruments/venues; UTC coverage and session timezone; information already inspected; candidate count; training/selection rule; evaluation dates; data-quality gates; cost and fill rules; capital/risk budget; acceptance and rejection clauses; and notebook/output paths. Do not overwrite an old preregistration or move its thresholds after seeing results.

Use an append-only trial ledger with parent study, candidate ID, parameters, code/data hashes, execution timestamp, whether its result was inspected, and disposition. Count unsuccessful, abandoned, asset/session/window/exit/filter/size searches too. A single replication of a selected strategy is not a strategy family with one historical trial. Where the historical search count is unknowable, report a documented lower bound and conservative sensitivity; do not manufacture a precise count.

Separate these decisions: arithmetic reproduction, execution fidelity, economic edge, incremental portfolio value, and readiness for paper observation. Passing one does not imply the others. Existing deployment decisions and approvals are outside this review.

## 3. Reproducible notebook and data contract

Future tests and all new outputs stay under `studies/`, preferably `studies/notebooks/<study>/`. Use numbered notebooks for data/lineage, engine checks, replication, validation, robustness, and decision where those stages apply. A helper script may do expensive computation, but the notebook must expose its exact configuration, input/output hashes, run status and tables needed to review the conclusion.

Before running:

1. Use a fresh kernel and a repository-root finder that verifies expected directories; reject missing inputs. Do not rely on `__file__` in a notebook, stale variables or silently created SQLite files. Open source databases with `mode=ro`; do not invoke refreshing loaders implicitly.
2. Pin the source snapshot or consistent read export, source commit plus dirty-file hashes, environment, seeds, query, cutoff and output directory. Existing NPZ/CSV/JSON caches require matching lineage, not merely file existence. Keep original results alongside corrected results with explicit supersession links.
3. Record instrument, spot/perpetual distinction, price basis, timestamp units, bar start/end labels, timezone/DST, native interval, availability time, funding forecast/settlement distinction, fee units and contract multiplier.
4. Audit duplicates, gaps, zero/invalid prices, partial bars, missing fields, timestamp shifts, delistings and survivorship. Flag affected sessions; do not turn unavailable periods into flat returns or silently forward-fill tradable prices. Preserve excluded-event counts and reasons.
5. Save actual executed notebooks and machine-readable ledgers. Empty execution counts are an absence of notebook execution evidence, not proof a corresponding script never ran. A saved result also does not establish that it belongs to the current source/data version.

Project-specific lineage checks: the BTC minute repair documented in `docs/strategy_issue_validation_2026_09_07.md` may affect earlier minute-based results; compare hashes and affected intervals before deciding what to regenerate. It does not automatically invalidate every daily-price study. `data/sources/venue_funding.py` documents the funding lineage change around 2026-04-13; sampling old forecast rows at eight-hour timestamps does not turn them into realized settlements. The brainstorm C2 study's separately fetched settlement history must be distinguished from that mixed table.

## 4. Causality and execution

Construct an event clock: feature observation end → available timestamp → decision → order submission → eligible fill → exit → outcome available for later decisions. Joining on the label of an unfinished hourly/daily candle is insufficient. Test future-data truncation: changing data after a decision must not change its features, eligibility, sizing or already executed actions.

For trading studies, use deterministic synthetic paths before historical replay: entry followed by an immediate stop; stop/target in the same bar; gap through a level; reversal; delayed entry; timeout; missing bar; partial fill; cancellation/fill race; overlapping trades; funding at a boundary; and an open final trade. Exclude only the pre-entry portion of a bar, not an entire first holding day. State pessimistic/optimistic bounds when OHLC cannot order events.

Market fills require the first attainable price after availability plus calibrated spread, fees, latency and slippage. Limit touch or trade-through is a fill model, not proof of queue execution or maker status. Freeze skip, cancel and fallback behavior at decision time; do not wait for a failed limit's future path then backdate its fallback. Track unfilled signals and opportunity cost. Use the execution venue's path for actual risk; report spot proxies separately when perpetual paths are unavailable.

## 5. Accounting and risk

Reconcile each trade and aggregate equity to cashflows. For a linear fixed-quantity position, marked price P&L is signed quantity times change in price; quantity is entry notional divided by entry price. Do not multiply each day's percentage change by the original notional unless the strategy explicitly rebalances that notional daily. For a linear short, underlying gross return is `1 - exit / entry`.

Book fees when charged and realized funding at its actual settlement timestamps. Distinguish gross return, net return, R, return on notional, return on margin, and return on total account NAV. Fixed-capital P&L sums; current-NAV percentage returns compound. Include initial capital in drawdown peaks. Report marked daily NAV and intraday adverse risk where the claim requires it; trade-close drawdown is a separate statistic. Preserve open positions and censored observations explicitly.

Portfolio comparisons need concurrent positions, cash availability, notional/quantity caps, account or subaccount netting, margin, mark-price liquidation rules, funding and transaction costs. Removing rows from a realized book is contribution attribution; it does not establish the executable portfolio without that sleeve. Leverage is not justified by multiplying a compounded drawdown by a scalar. Stress correlated BTC/ETH losses, gaps, spread expansion, delayed exits, funding spikes, missing feeds and exchange downtime when relevant.

## 6. Statistical validation, proportionate to the question

For a future alpha claim, freeze chronological expanding or rolling training/evaluation windows after inventory. Fit thresholds, ranks, scaling, asset selection and overlays only on information available in training. Purge outcomes overlapping evaluation boundaries and use embargo based on actual lookback/holding/label overlap. Previously inspected historical years, another correlated crypto asset, or another feed of the same trades are robustness evidence, not automatically an untouched holdout. Reserve genuinely uninspected data where it exists; otherwise label historical revalidation honestly and schedule a future observation window.

Use all calendar days for portfolio risk and aligned paired resampling for strategy-minus-baseline comparisons. Cluster by time/event and jointly across correlated assets; choose block length from holding/dependence structure before evaluating, and report sensitivity at shorter and longer lengths. Default future interval reporting is 95% with at least 5,000 seeded replicates for ordinary bootstrap work, unless an existing frozen rule specifies otherwise. Small samples may require exact or simulation-based calibration and a larger replication count for tail probabilities. State actual quantiles; do not relabel historical 90% intervals as 95%.

Primary economic endpoints should include net expectancy, total/calendar return, marked drawdown, exposure/turnover, and paired incremental performance against the study's meaningful baseline. Sharpe, profit factor, hit rate, MAE/MFE and regime splits are supporting diagnostics. Report concentration by year, asset, direction and the largest winners; overlapping trades/windows are not independent observations.

Predeclare a minimum effect worth pursuing and assess minimum detectable effect / interval width using training or a justified planning distribution. Do not declare power merely from an arbitrary trade count. A wide interval crossing zero is **inconclusive**, not equivalence; demonstrate equivalence only with a predefined economically meaningful margin and appropriate interval/test. A rejection under a frozen cost model rejects that specification, not every version of a strategy idea.

Selection correction must include the actual family searched. Use DSR/SPA/Reality Check/PBO only when their inputs and null answer the question, with family-wise handling of primary claims. The existing helpers need caller-side verification: CPCV over a precomputed return vector is not refitting a learner; the PBO helper selects by mean returns and trims remainder; DSR units/trial count/kurtosis must match; bootstrap probability above zero is not a null p-value; some helpers default to 90% intervals. Inspect `studies/lib/validation` implementations before importing them into new decision logic. No DSR is needed to validate a CSV parser or a descriptive Fano factor.

## 7. Extensive testing without unlimited optimization

Sequence work: reconcile source/data → fix or bound engine defects → replicate the frozen baseline → validate the one primary question → bounded ablations → stress → decision. Stop when a prerequisite fails; do not compensate by expanding the grid. Record new ideas from robustness plots as a separate exploratory branch, never as confirmation of the same test.

Where applicable, bounded ablations isolate signal, filter, exit, cost, sizing and allocator contribution one at a time. Controls include no trade/cash, appropriate spot buy-and-hold, the unfiltered signal, a simple momentum/mean-reversion comparator, and time/exposure-matched randomized entries. Choose meaningful controls, not every possible baseline. Cross-venue and BTC/ETH transfer test transportability with separately reported results and common-period comparisons.

## 8. Required future deliverables and decisions

Each follow-up notebook ends with a claim-to-evidence table: claim, configuration, result artifact, uncertainty, passed/failed/unevaluable clause, deviations and permissible conclusion. Save signals including rejected ones, orders/fills when modeled, trades, settled cashflows, calendar NAV, exclusions, split membership, trial registry and environment/data manifests as applicable.

- **INVALID / UNEVALUABLE:** lineage, causality, engine, accounting or required-data prerequisite fails. Do not score promotion from that run.
- **REJECT / KEEP ARCHIVED:** the frozen economic or falsification clause fails with adequate evidence, or an existing no-build decision remains appropriate.
- **INCONCLUSIVE:** insufficient precision or required execution/market evidence. State what evidence would resolve it.
- **SUPPORTS THE LIMITED CLAIM:** all relevant frozen clauses pass. This is permission to make that research claim, not to place orders or change production.

For report/parity studies, completion is exact reconciliation within declared tolerances and fresh-kernel reproducibility, not a profitable result. For descriptive notes, completion is calibrated statistics and appropriately limited interpretation. Paper/live activity is a separate later task; this audit schedules no orders or external writes.

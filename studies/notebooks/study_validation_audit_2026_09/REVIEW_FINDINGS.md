# Cross-study findings

This is a static review dated 2026-09-14. It identifies source defects and evidence gaps; it does not supply corrected backtest results or an independent empirical validation. Read the individual plans for precise scope, references and applicability. Historical source, findings, preregistrations and saved notebooks are preserved.

## Answer to the audit question

Testing quality is uneven. Some recent studies have strong preregistration, parity, falsification and uncertainty reporting. Other notebooks are exploratory sketches or converted scripts, and several reusable engines contain specific causality or accounting defects. It would be incorrect to say that everything has been considered or that the whole collection is validated.

The most useful next step is to repair or bound shared evidence dependencies before doing more strategy searches. A large parameter grid cannot rescue an invalid event clock or equity calculation. Conversely, a parked idea need not be reopened just to obtain a more elaborate rejection.

## Highest-value corrections before affected evidence is reused

| Area | Static evidence / consequence | Follow-up |
|---|---|---|
| Shared marked P&L | `sizing_style_2026_09/sizing_lib.py:109` multiplies successive percentage moves by original notional. It does not conserve the P&L of a fixed-quantity holding; ADX imports this helper too. Funding is also booked at exit in the ADX adapter. | Reconcile quantity, cashflows and calendar NAV first; then regenerate only affected risk/return tables. |
| ADX first holding day | `adx_robustness_2026_09/adx_lib.py:141` continues the outer daily loop after opening. It skips that day's entire minute-stop walk, despite a comment referring only to the entry minute. | Synthetic immediate-stop and delayed-entry fixtures, corrected study-only replay, affected-trade and downstream BTC/ETH risk reconciliation. |
| Future information in decisions | Scanner hourly features, screener daily-entry timestamps, overlay/sizing-style previous-outcome sizing, Paladin level selection and dwell fallback contain identified availability/ordering problems. | Use the precise per-study references and future-data truncation tests; outcome data may enter later decisions only after it is observed. Preserve archived rejection decisions. |
| Legacy report arithmetic | `backtest_report` compounds fixed-capital contributions. `full_portfolio_report` passes consecutive buy-and-hold returns into an additive contribution function. | Independent accounting fixtures and full ledger/benchmark reconciliation before citing return or drawdown comparisons. |
| Funding provenance | Several studies count old `cd_funding_rate` forecast rows at eight-hour boundaries as settled cashflows. Their sum can be arithmetically reproducible while their economic meaning is wrong. | Verify lineage and settlement timestamps; distinguish the brainstorm C2 fetched settlement history from the mixed production table. |
| Execution inference | Spot OHLC stop/touch models support modeled sensitivity; they do not establish queue fills, maker eligibility, zero real slippage or perpetual liquidation safety. | Causal fill bounds, actual venue/account assumptions, tail stress and later observed execution evidence if needed. |
| Contradictory or incomplete decision evidence | Examples include a selected funding/CVD combination after a failed screening gate, an early dwell deployment suggestion superseded by later rejection, and incomplete recut inputs that can reach decision logic. | Reconcile original clause, current source and saved output; absent required inputs must remain unevaluable. Preserve superseded history visibly. |

Priorities are about evidence reuse, **not instructions to stop, deploy or modify bots**. Some listed issues were already acknowledged in later repository findings; the new plans connect them to the older evidence rather than presenting every item as a new discovery. Static inspection establishes the code-path issue; the size and direction of its effect on actual results remain unmeasured here.

## Work worth preserving

- **OKX gate revalidation** has a detailed frozen contract, causal and same-hour controls, explicit limits on inference, a literal final RETIRE decision and a separate portfolio-sizing question. Preserve that decision. Its later findings already disallow reuse of a report-only sequence approximation.
- **Brainstorm validation** distinguishes replication from transfer, reconstructs cost/turnover effects, accounts for earlier searches and records failures. Its rejected claims do not require another broad search.
- **Basis carry** preserves a failed data gate and literal INCONCLUSIVE outcome alongside negative modeled economics. Those are different statements; retain both.
- **Hawkes and RSI notes** disclose important descriptive/selection limits and park the ideas. Their plans are conditional and do not demand irrelevant alpha statistics.
- **Earlier validation audit** reports weak selection-adjusted evidence and incomplete family coverage instead of claiming a general statistical pass. Its source inputs still require the validity checks identified here.

## Common omissions and how the plans address them

1. **Reproducibility:** fresh-kernel entry points, read-only inputs, cache/source hashes and saved run manifests. Thirty-seven of the 51 original non-ORB notebooks contain code but no saved execution counts. That does not prove their accompanying scripts never ran; it means notebook execution is not demonstrated by those fields.
2. **Data and availability:** spot/perpetual identity, source cutovers, missing bars, partial sessions, prior BTC-minute repair, as-of feature publication and censored labels.
3. **Execution and capital:** causal entry/exit ordering, gap and ambiguous-bar handling, realized funding, actual quantity, open positions, concurrent capital and marked risk.
4. **Inference:** dependence, overlapping outcomes, the full search family, genuinely uninspected data, paired baselines, economically meaningful effect sizes and an explicit inconclusive state.
5. **Claim scope:** parity is not economic validation; a descriptive peak is not an attainable exit; cashflow attribution is not an executable counterfactual; a failed specification does not disprove an entire strategy class.

## Suggested execution order for a later testing task

1. Freeze the shared data/return contracts and audit helper behavior. Establish source/cache versions and independent arithmetic fixtures.
2. Correct and reconcile the shared marked-P&L function, ADX first-day walk and decision-time feature/outcome paths **if those results are to be reused**.
3. Reproduce affected fixed baselines and rebuild their dependent report tables. Record before/after trades, dollars and risk, with no new parameter selection.
4. Run each decision-bearing study's bounded validation and portfolio stress only after prerequisites pass.
5. Leave rejected/descriptive studies archived unless a new data source, mechanism or concrete use case triggers their conditional plan.

Each actual follow-up must freeze its exact configuration and numeric decision criteria before execution. This review schedules no backtests, changes no production code, and places no orders. The shared protocol and individual plans define the reviewable work to do next.

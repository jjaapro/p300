# Opening Range Breakout (ORB) research and testing plan

**Status: RESEARCH / DRAFT PLAN — 2026-09-14. No ORB backtest has been run.**

Scope agreed with the user: **BTC/ETH first; equities and index futures are optional extensions.**
All work for this study, including later notebooks, scripts, tests, downloaded data,
caches and results, belongs under this directory. Existing project data is read-only.

Start with:

- [Research and plan notebook](00_research_and_plan.ipynb): a Markdown-only
  research overview and standalone copy of the testing plan. It contains no executable cells or results.
- [Research review](RESEARCH.md): what ORB means, the evidence, source limitations,
  related project studies and data suitability.
- [Testing plan](TEST_PLAN.md): proposed rules, experiments, execution model,
  chronological validation, decision criteria and notebook deliverables.

The Markdown documents are the editable source of truth. Regenerate the review notebook
from them when changing the plan; do not maintain divergent specifications.

Regenerate from the repository root with:

```powershell
venv\Scripts\python.exe studies\notebooks\orb_study\build_review_notebook.py
```

The builder only reads these documents and writes their review notebook. It does not
access market data, import strategy code or execute experiments.

The initial proposal is a fixed BTC **New York 15-minute range**, followed by a
one-minute close outside the range and entry at the next executable price. ETH is a
transfer test. A separate resting-stop entry tests the traditional touch-break version.
Opening-candle momentum and range-break reversal are separate controls.

Research this turn comprised web-source review, inspection of repository code and
documents, and read-only data inventory. No trading signals, performance statistics,
parameter search, production change, data download or paper-trading run was performed.
Existing results quoted in RESEARCH.md are attributed to their original studies.

This is not yet a frozen pre-registration. The next implementation phase should
resolve the listed data and specification checks, record the final configuration and
hashes, then execute the numbered study notebooks. A change made after seeing an
outcome must be recorded as a new exploratory trial, not silently folded into the baseline.

```
p300/
├── bot.py                          # main loop; pure paper/live trading (no test mode); paper/live distinguished by trade tag; data feed always on
├── requirements.txt                # all required packages for venv
├── data/
│   ├── feed.py                     # check / fetch all used data every minute (1m is the lowest timeframe used)
│   ├── databases/
│   │   ├── prod.db                 # single DB for all paper + live state: market data, trades, observers (paper/live tag per trade)
│   │   ├── sim.db                  # simulation only; can be wiped or modified freely
│   ├── csvs/                       # temporary store for downloaded data (FOMC, etc.)
│   ├── archives/                   # per-sleeve persistent artifacts (e.g. ai_quant decision markdown)
├── strategies/
│   ├── orchestrator.py             # central control: allocation, regime weighting, gating, vol-target, conflict resolution
│   ├── trades.py                   # execution layer: open/close trades, cost + slippage + funding; called by orchestrator
│   ├── support/                    # shared services usable by any sleeve
│   │   ├── regime.py
│   │   ├── ml_gate.py
│   │   ├── voltarget.py
│   │   ├── ...
│   ├── sleeves/                    # one sub-folder per sleeve; uniform internal layout
│   │   ├── ai_quant/
│   │   │   ├── __init__.py
│   │   │   ├── signal.py           # entry/exit decision logic
│   │   │   ├── config.py           # params: allocation, leverage, stops, regime weights
│   │   │   ├── README.md           # what it does, edge thesis, caveats
│   │   │   ├── chart.py            # sleeve-specific extra (was tools/render_ai_chart.py)
│   │   │   ├── archive_rebuild.py  # sleeve-specific extra (was tools/ai_quant_archive_rebuild.py)
│   │   ├── pdo/                    # signal.py + config.py + README.md (same shape as ai_quant; no extras)
│   │   ├── r4/
│   │   ├── thu_bear/
│   │   ├── cpr/
│   │   ├── ...
├── tests/
│   ├── unit/                       # per-sleeve and per-support unit tests, mirroring code tree
│   ├── integrated/                 # spin up orchestrator + sleeves against fixtures; verify triggers, entries, exits
├── studies/
│   ├── notebooks/                  # jupyter notebooks for strategy studies (backtests, calibrations, validations)
│   ├── reports/                    # report generators (e.g. generate_ai_quant_report.py — was tools/ai_quant_preview.py)
│   ├── material/                   # books, research papers, reference documents
│   ├── simulation/
│   │   ├── sim.py                  # separate simulation bot with fake clock and other test-specific functionality
```

---

## Migration notes

### Decisions captured
- `bot.py` runs paper or live only — no `--test` mode. Distinction is a trade tag in `prod.db`, not a runtime flag.
- `strategies/trades.py` (execution layer) sits next to `orchestrator.py`; orchestrator drives it.
- `strategies/support/` holds shared services (regime, ml_gate, voltarget, margin sim, price feed, cost model) usable by any sleeve.
- No more Core/Tactical split — all sleeves are equal under the orchestrator. Regime weighting and ML gating apply to every sleeve.
- `tools/` directory is removed entirely:
  - Sleeve-specific tools → into the sleeve folder (e.g. `tools/ai_quant_archive_rebuild.py` → `strategies/sleeves/ai_quant/archive_rebuild.py`; `tools/render_ai_chart.py` → `strategies/sleeves/ai_quant/chart.py`).
  - Report generators → `studies/reports/` (e.g. `tools/ai_quant_preview.py` → `studies/reports/generate_ai_quant_report.py`).
  - Backtests / calibrations / validations → `studies/notebooks/` as `.ipynb` (literal conversion; lower priority — move scripts first, convert later).
  - `tools/build_sim_trader_db.py` → folds into `studies/simulation/sim.py` setup.
  - `tools/p300_run.ps1` → dropped; `bot.py` handles console output filtering itself.
- `SHADOW` terminology dropped everywhere; replaced by `paper` / `live` trade tag.
- **Uniform sleeve internal layout**: every sleeve has at minimum `signal.py` + `config.py` + `README.md` + `__init__.py`. Sleeve-specific extras (e.g. `chart.py`, `archive_rebuild.py`, sleeve-specific data fetchers) sit alongside. Unit tests live under `tests/unit/<sleeve>/`, not inside the sleeve folder.
- **`prod.db` is one SQLite file** holding everything paper+live: market data tables, `trades`, observers/journals. Today's `trader.db` + `dashboard.db` consolidate into it.
- **Orchestrator tick cadence: 1 minute.** Lowest timeframe used is 1m candles, so the loop wakes once per minute after the bar closes.

### Still open
- **`register_p300.py`** — variant concept likely collapses into orchestrator config; confirm during the orchestrator-skeleton commit.
- **DB consolidation** (`trader.db` + `dashboard.db` → `prod.db`) — schema migration; downstream commit, not Day 1 of the folder restructure.
- **Migration order** — proposed ~5 commits using `git mv` to preserve history: (1) introduce orchestrator skeleton, (2) collapse `services/` + `jplus/` into `strategies/sleeves/` + `support/`, (3) move `services/trades.py` → `strategies/trades.py`, (4) split `tools/` per the rules above, (5) `SHADOW` → `paper` rename. DB consolidation is a 6th step after the folder restructure stabilizes.
- **Doc cleanup** — PORTFOLIO.md, README.md, MANUAL.md, OPERATIONS.md will get a focused refactor at the END of the restructure. User flagged them as "messy and hard to read" (2026-05-14). All path links to `services/*` files in those docs are intentionally left stale during the move steps and fixed in one sweep at the end alongside the readability rewrite. AUDIT_*.md files are historical records and are left alone.

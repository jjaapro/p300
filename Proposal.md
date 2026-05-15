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

## Migration status (2026-05-14)

### Phase 1 — structural restructure (DONE)

21 commits since the pre-restructure checkpoint `1e2e2d0`. Everything
mechanical from the proposal is shipped:

| Step | Commit | Summary |
|---|---|---|
| Checkpoint | `1e2e2d0` | Proposal.md + BACKLOG.md captured |
| 1 — Scaffold | `c4b52c1` | Empty `strategies/`, `studies/`, `data/{archives,csvs,databases}`, `tests/{unit,integrated}` |
| 2 — ADX pilot | `ffce7b8` | First sleeve in the new layout |
| 3 — Tactical sleeves | `e8efe58`, `d2e9aee`, `9ee39ad`, `ebde133`, `a55145d` | THU_BEAR, CPR, PDO, CARRY, FOMC |
| 4 — AI_QUANT | `27dfbe9` | 15-file sleeve (incl. `chart_cli.py`, `archive_rebuild.py`) |
| 5 — J+ live handlers | `de0d6c0` | `services/jplus_live.py` split into `strategies/sleeves/{r4,ema,eth_daily}/` |
| 6a — J+ sleeve math | `c235f59` | `jplus/r4.py` + `jplus/ema_sleeve.py` → sleeve folders |
| 6b — J+ shared math | `e94638e` | `voltarget`, `gate`, `regime_jplus` → `strategies/support/` |
| 6c.1 — data loaders | `7097fb1` | `jplus/data.py` → `data/loaders.py` |
| 6c.2 — simulate split | `1630fbb` | `today_inputs` → `support/jplus_inputs.py`; `simulate()` → `studies/jplus_analytic/`; `jplus/` deleted |
| 6d — services utils | `03948bc` | 16 modules → `strategies/support/` |
| 6e — data fetchers | `2ef120b` | 5 services + `binance_feed.py` → `data/sources/` |
| 6f — tactical regime | `8762e1e` | `regime_classifier.py` → `support/regime_tactical.py` |
| 7 — trades.py | `7a22857` | `services/trades.py` → `strategies/trades.py` |
| 8 — tools/ split | `f7422c9` | 22 files redistributed across `studies/{notebooks,reports,simulation}/` |
| 9a — orchestrator rename | `0daba61` | `variant_engine.py` → `strategies/orchestrator.py`; `services/` deleted |

Tests passing across the touched suites (~384 at the last full sweep,
excluding one test that needs the optional `anthropic` package).

### Phase 2 — deferred work

See [BACKLOG.md](BACKLOG.md) for full scope, dependencies, and risk
notes on each item. Brief index:

| Item | Type | Risk | Why deferred |
|---|---|---|---|
| `check_liquidations_for_variant` extraction | Mechanical refactor | Low | Removes a `orchestrator → backtest_runner` layer inversion; small + isolated. |
| `.ipynb` conversion of `studies/notebooks/*.py` | Mechanical / cosmetic | None | User flagged as low priority during the proposal. |
| `run.py` → `bot.py` redesign | Mechanical + scope | Low | Drops `--mode sim` (splits to `studies/simulation/sim.py`), always-on feed, drops `tools/p300_run.ps1`. Wasn't part of "build the orchestrator". |
| Real orchestrator architecture | **Design + impl** | Medium | Cross-sleeve allocation, ML gating, portfolio vol target, margin enforcement, conflict resolution, signal aggregation. Multiple design questions; warrants its own focused effort. |
| `paper` → `paper` rename | Code + DB migration | Medium | Touches live paper-trade rows in `dashboard.db`. Needs careful migration order. |
| DB consolidation (`trader.db` + `dashboard.db` → `prod.db`) | DB migration | Medium | Hot DBs; needs schema design + migration script. |
| Doc sweep (PORTFOLIO / README / MANUAL / OPERATIONS) | Doc rewrite | Low | User flagged "messy and hard to read" — readability rewrite, not just path-fix. Last so all paths settle first. |

### Phase 1 decisions captured

- `bot.py` runs paper or live only — no `--test` mode. Distinction is a trade tag in `prod.db`, not a runtime flag.
- `strategies/trades.py` (execution layer) sits next to `orchestrator.py`; orchestrator drives it.
- `strategies/support/` holds shared services (regime, ml_gate, voltarget, margin sim, price feed, cost model) usable by any sleeve.
- No more Core/Tactical split — all sleeves are equal under the orchestrator. Regime weighting and ML gating apply to every sleeve.
- `tools/` directory removed entirely; redistributed by purpose.
- `paper` terminology to be dropped everywhere; replaced by `paper` / `live` trade tag. **(Code rewrite is phase 2.)**
- **Uniform sleeve internal layout**: every sleeve has at minimum `signal.py` + `config.py` + `README.md` + `__init__.py`. Sleeve-specific extras (e.g. `chart.py`, `archive_rebuild.py`, sleeve-specific data fetchers) sit alongside. Unit tests at `tests/unit/<sleeve>/`, not inside the sleeve folder.
- **`prod.db` is one SQLite file** holding everything paper+live: market data tables, `trades`, observers/journals. Today's `trader.db` + `dashboard.db` consolidate into it. **(Phase 2.)**
- **Orchestrator tick cadence: 1 minute.** Lowest timeframe used is 1m candles, so the loop wakes once per minute after the bar closes.

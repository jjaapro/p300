# Repository review — 2026-09-06

Reviewed the working tree at commit `4a4d83a4933b8e84700613dc04f7baf29749bbc8`.

Scope: standalone bot runners, shared trade persistence, Chento position management, price/data readers, simulation setup and data slicing, dashboard tests, and operational documentation. This is a targeted correctness review, not an exhaustive audit of every research notebook or trading strategy. Existing local changes were preserved; this review does not implement fixes.

Severity: **P1** = high priority, possible data loss or incorrect trade accounting/management; **P2** = functional or validation defect; **P3** = documentation defect.

## Findings

### 1. P1 — The simulation builder can delete its source database

**Location:** [studies/simulation/build_sim_trader_db.py](studies/simulation/build_sim_trader_db.py#L119), lines 119–130.

The builder resolves `--source` and `--output`, checks that the source exists, then unconditionally deletes an existing output. It never rejects the two paths identifying the same file. An accidental `--output data/databases/prod.db` with the default source can therefore delete the production database before the read-only source connection is opened. On Windows this depends on whether another process currently holds the file open.

**Verified:** With a disposable SQLite source and identical source/output arguments, the builder deleted the source and subsequently raised `sqlite3.OperationalError`. The original file no longer existed.

**Suggested fix:** Reject identical resolved paths, existing paths for which `samefile()` is true, and the production database as an output before any deletion. Write the slice to a temporary destination and replace an existing output only after successful validation. Add a regression check that the source remains byte-identical when a conflicting destination is rejected.

### 2. P1 — Cached forming candles can permanently hide Chento stop or target hits

**Location:** [strategies/sleeves/chento_triple_v3/signal.py](strategies/sleeves/chento_triple_v3/signal.py#L490), lines 490–507, 650–653, and 891–896.

The feature loader includes the forming 15-minute candle. `_bar_ohlc_for()` prefers its cached OHLC values without checking whether that candle was complete when cached. Position management runs **before** trigger evaluation can rebuild the cache, and then saves `last_walked_ts` as though the completed candle had been processed. A later refresh cannot recover the missed stop/target because that candle is no longer walked. The early cooldown return in trigger evaluation also prevents its refresh from being a reliable safeguard.

**Verified:** Opened a long at 100,000 with a 97,000 stop. The completed database candle had a 96,000 low; its cached partial version had a 99,000 low. The sweep took zero actions, kept the trade open, and advanced `last_walked_ts` past the stop-hit candle.

**Suggested fix:** Read confirmed final OHLC for position management, or only accept cached candles whose close preceded the cache snapshot. Do not advance walking progress using an unconfirmed partial candle. Cover a forming candle that crosses the stop after the cache is built.

### 3. P1 — Simulation redirection leaves production database consumers attached

**Location:** [studies/simulation/sim.py](studies/simulation/sim.py#L143), lines 143–160; [strategies/support/trade_db.py](strategies/support/trade_db.py#L28), lines 28–35; [strategies/sleeves/chento_triple_v3/signal.py](strategies/sleeves/chento_triple_v3/signal.py#L163), lines 163–176 and 189–217.

The simulator redirects `db.TRADER_DB` and `db.DASH_DB`, but `trade_db.DB_PATH` was captured from `db.PROD_DB` at import time. Consequently, `trade_db.init_db()` targets production rather than the requested simulation ledger. A fresh simulation ledger receives the variant schema but lacks the `trades` table. Meanwhile, Chento loaders and its OHLC fallback read `db.PROD_DB` directly, which is also left pointing at production. Simulated Chento decisions can therefore use data outside the supplied slice.

**Verified:** Using a temporary database as the fake production database, ran simulation initialization with a separate fresh ledger and stubbed the subsequent loop/report. The ledger had no `trades` table; both `trade_db.DB_PATH` and `db.PROD_DB` still identified the fake production file. No actual production database was used in this probe.

**Suggested fix:** Resolve ledger access from the active ledger path at call time and market access from the active market path. Audit remaining captured paths and direct `PROD_DB` reads. Test the actual initialization path with a fresh ledger and reject every connection to a sentinel production path; the existing acceptance test stubs out schema initialization and misses this defect.

### 4. P1 — Duplicate-signal protection changes on every execution attempt

**Location:** [strategies/trades.py](strategies/trades.py#L179), lines 179–185; [strategies/sleeves/chento_triple_v3/signal.py](strategies/sleeves/chento_triple_v3/signal.py#L900), lines 900–914.

`open_paper_trade()` derives its uniqueness key from `clock.now_iso()` unless an entry timestamp is supplied. Chento passes neither a stable entry timestamp nor a separate signal identifier, even though its Intent contains `bar_ts`. Two bot instances executing the same signal at different seconds, or a retry after a commit, receive different uniqueness keys. The in-memory evaluation/cooldown dictionaries do not coordinate separate processes or survive a restart.

**Verified:** Executing the exact same Chento Intent at timestamps one second apart created `SJ-0001` and `SJ-0002` in the same variant.

**Suggested fix:** Use a persistent logical signal key, such as variant + sleeve + asset + trigger candle, independently of the actual fill timestamp. Enforce it in the database and cover separate executions of the same signal at different wall-clock times. Persist any cooldown state required across restarts.

### 5. P1 — Sequential trade ID allocation races across fleet processes

**Location:** [strategies/trades.py](strategies/trades.py#L72), lines 72–81 and 189–239.

`_next_sj_id()` calculates `MAX(id) + 1` before the INSERT begins a write transaction. Holding a connection does not reserve that number: two connections can read the same maximum. SQLite serializes their writes, but the second INSERT then fails on `trades.id`. The exception handler only recovers collisions on `unique_key`. This also affects different bots opening unrelated signals in the shared ledger. A failed Chento entry may not be retried because its candle was already marked evaluated during phase one.

**Verified:** Synchronized two independent connections immediately after ID selection, using different variants and logical keys. One open succeeded; the other raised `UNIQUE constraint failed: trades.id`.

**Suggested fix:** Use database-assigned IDs/an atomic allocator, or acquire the write transaction before allocating the number. Keep allocation and insertion in the same transaction. Test concurrent opens for different signals, not just repeated identical keys.

### 6. P2 — The “last closed” price reader can return the forming minute

**Location:** [strategies/support/price_feed.py](strategies/support/price_feed.py#L74), lines 74–84; [data/sources/binance.py](data/sources/binance.py#L73), `fetch_spot_klines_1m()`.

The query selects `open_time < current_time`. At 10:00:30, that admits the candle opened at 10:00, which does not close until 10:01. The feed explicitly stores and subsequently updates forming candles. Live decisions can therefore consume provisional prices despite the closed-candle contract; replay at a sub-minute timestamp can consume a historical candle's future final close.

**Verified:** With a 09:59 candle closing at 100 and a 10:00 candle containing 999, a clock of 10:00:30 returned 999 rather than the last completed close of 100.

**Suggested fix:** Require the candle's close boundary to be at or before the clock, for example by comparing `open_time` against the current minute boundary. Test both exact-minute and mid-minute clocks with a forming candle present.

### 7. P2 — Simulation slices omit inputs required by current sleeves

**Location:** [studies/simulation/build_sim_trader_db.py](studies/simulation/build_sim_trader_db.py#L45), `TABLE_PLAN`, lines 45–64 and the copy loop at line 178.

The builder recreates every source table's schema, but copies rows only for tables in `TABLE_PLAN`. That list omits `cd_futures_15m`, `cd_spot_15m`, `okx_perp_1h`, and `fear_greed_index`, among newer asset-specific tables. Thus a slice can finish successfully while leaving required Short Squeeze, Chento, and FOMC inputs empty. Fixing database redirection alone does not make these sleeves reproducible from the slice.

**Verified:** Seeded one row in each of those four tables and `btc_1m`, then built a slice covering their timestamps. The output contained one `btc_1m` row and zero rows in each of the four omitted tables.

**Suggested fix:** Maintain a complete input manifest with timestamp units and warmup requirements for supported sleeves. Copy and validate those inputs, and fail clearly when required source data is unavailable. Include current 15-minute and ETH inputs in the slice integration checks.

### 8. P2 — A date-only simulation end excludes most of the final day

**Location:** [studies/simulation/sim.py](studies/simulation/sim.py#L56), lines 56–66 and 151–152; [strategies/support/sim_loop.py](strategies/support/sim_loop.py#L80), lines 80–89.

The README documents an inclusive date range, but the same parser converts both start and end dates to midnight. `--end 2024-12-31` therefore executes at most the midnight tick on December 31 and skips the remainder of that day. This loses final-day entries and scheduled exits and can leave misleadingly open positions in the results.

**Verified:** Parsing a date-only end produces `00:00:00+00:00`; the loop stops once the next tick exceeds that instant.

**Suggested fix:** Treat date-only end values as the whole calendar day, preferably through an exclusive next-midnight boundary, while preserving explicit timestamp semantics. Test an entry and exit later on the named final day.

### 9. P3 — README feed commands reference a removed entry point

**Location:** [README.md](README.md#L87), lines 87 and 116–118.

The bootstrap follow-up and standalone-feed instructions run `python binance_feed.py`, but that file does not exist. The maintained daemon is `feed.py`; historical fetch/backfill arguments belong to `data/sources/binance.py`. Following the documented commands fails before loading the application.

**Suggested fix:** Update normal feed commands to `python feed.py ...` and backfill examples to the actual data-source CLI. Check the quick-start commands against the current fleet architecture.

## Test results and additional validation issues

Ran the existing suite using the repository virtual environment:

```powershell
New-Item -ItemType Directory -Path .review_tmp -Force | Out-Null
.\venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -m 'not slow' --ignore=tests/test_sim_mode.py --ignore=tests/test_chento_parity.py --ignore=tests/test_chento_parity_eth.py --ignore=tests/test_adx_parity.py --ignore=tests/test_short_squeeze_parity.py --basetemp=.review_tmp/pytest_run --tb=short
```

**Result:** 1,015 passed, 3 failed, 18 deselected in 49.62 seconds. All three failures reproduced in a focused rerun. The five explicitly excluded modules cover real-data parity or production-database-copy simulation tests; they were not validated. This is not a full-suite pass.

### P2 — Two news tests depend on expired fixed dates

- [tests/test_news_fetcher.py](tests/test_news_fetcher.py#L293): `test_refresh_persists_correct_row_shape` inserts a May 8, 2026 article; refresh immediately prunes it, so unpacking the queried row fails with `TypeError`.
- [tests/test_news_fetcher.py](tests/test_news_fetcher.py#L368): `test_query_returns_newest_first_and_respects_limit` uses May 1, 2026 articles; the query gets zero rows instead of two.

The cause is the fixed fixtures interacting with the wall-clock **30-day retention** in [data/sources/news.py](data/sources/news.py#L225). These failures do not establish an ingestion defect. Freeze the relevant wall-clock sources in these tests or generate fixture dates relative to a controlled current time; setting the simulated trading clock alone disables refresh.

### P2 — Dashboard open-interest parity test fails

[tests/test_dashboard_market.py](tests/test_dashboard_market.py#L238), `test_basis_and_oi_match_chento_limit_bid`, checked 91 overlapping bars and found four dashboard OI gaps where the comparison allows at most one. The same failure occurred in isolation.

The relevant implementations are [dashboard/market.py](dashboard/market.py#L202), `_attach()`, and [strategies/sleeves/chento_limit_bid/signal.py](strategies/sleeves/chento_limit_bid/signal.py#L97), the limited forward-fill/reindex path. Reconcile their handling of missing bars and hourly gaps with an explicit common contract, then correct the implementation or assertion accordingly. The failure proves the parity check is currently red; it does not by itself establish which implementation has the intended semantics.

## Validation boundaries

The runtime reproductions used synthetic inputs and temporary databases, including a fake production path for the isolation probe. No bot/feed/monitor loop or exchange action was launched. Findings 1–7 were reproduced with these probes; finding 8 was checked through the parser and loop, and finding 9 against the repository paths. Live exchange behavior and strategy profitability were outside scope.

## Resolution — 2026-09-06 (Claude)

Every finding reproduced against the working tree and was fixed; each fix carries a regression test.

| # | Status | Fix |
|---|--------|-----|
| 1 | Fixed | `build_sim_trader_db.py` refuses an `--output` that is the source or the live prod.db before touching anything, builds into a `.building` sibling and swaps it in only after a complete copy. `tests/test_build_sim_trader_db.py` |
| 2 | Fixed | `chento_triple_v3`: a cached row is used only if the bar was final when the frame was built (`_bar_final_by`); live sweeps hold back `LIVE_BAR_SETTLE_S` (90 s) so the feed has overwritten the in-progress row. Replay path unchanged. `test_live_sweep_reads_final_candle_not_partial_cache` |
| 3 | Fixed | `trade_db` resolves the ledger from `db.DASH_DB` at call time (`DB_PATH` is now a test-only override); Chento and ADX market reads use `db.TRADER_DB`. The isolation-guard test no longer stubs schema init and asserts prod.db stays untouched. |
| 4 | Fixed | `open_paper_trade(signal_time_iso=...)`: Chento v3 and short_squeeze key idempotency on the trigger bar; Chento's cooldown is seeded from the ledger on first use so a restart cannot re-fire. `tests/test_trades_concurrency.py`, `test_execute_same_signal_twice_is_one_trade`, `test_cooldown_survives_restart` |
| 5 | Fixed | `open_paper_trade` opens `BEGIN IMMEDIATE` before the pre-check and ID mint, so allocation and INSERT share the write lock. `test_id_mint_holds_the_write_lock` |
| 6 | Fixed | `price_feed.get_current_price` admits a bar only when `open_time + 60s <= clock`. `tests/test_price_feed.py` |
| 7 | Fixed | `TABLE_PLAN` now names every live table (47) with a copy mode; an unplanned source table is an error, a missing required table is an error, a required table with zero rows in the window is a printed WARNING. `test_plan_covers_every_live_table` |
| 8 | Fixed | date-only `--end` means the last instant of that day (`_parse_iso_utc(..., end_of_day=True)`); explicit timestamps unchanged. `tests/test_sim_args.py` |
| 9 | Fixed | README / OPERATIONS / PORTFOLIO: `feed.py` for the daemon, `python -m data.sources.binance --backfill-klines` for backfills. Also replaced a non-ASCII arrow in that CLI's help text that crashed `--help` on a cp1252 console. |
| news tests | Fixed | fixture dates are now relative to the wall clock (30-day retention prune). |
| dashboard OI parity | Fixed (real defect) | Reproduced: the test fails in the first half of every hour. `market._attach` counted bars, seeded only the very first bar and went blank after the fixture's older entry-context cluster. Rewritten as a time-based carry (value valid for `carry × secs` after its stamp) — identical on contiguous data, correct across gaps. Probe over 24 h of fake clocks: 0 failures. |
| test_sim_mode | Fixed | stale `interval_hours=` keyword → `interval_seconds=3600` (pre-existing, unrelated to the findings). |

Validation: `pytest -m "not slow" --ignore=tests/test_sim_mode.py` → 1,052 passed, 0 failed (including the chento/ADX/short-squeeze parity modules the original review skipped); `tests/test_sim_mode.py` → 4 passed.

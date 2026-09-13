# p300 — a fleet of paper-trading bots

Seven bot processes, one market-data feed, one dashboard. Every bot writes
phantom trades to a local SQLite ledger and **never places an order on any
exchange**. The repo is self-contained: market data is pulled from public REST
APIs (Binance, OKX, Bybit, Coinbase, Deribit, Yahoo), the event calendar is
computed in-process, and the long-short-ratio history was seeded once from
Coinalyze.

> **Status 2026-09-13: the fleet runs, the evidence is still thin.** Nine paper
> variants across seven bot units; the ledgers hold far too few closed trades
> for a verdict on any of them. Current status and roadmap: the dated section at
> the top of [BACKLOG.md](BACKLOG.md). Validation protocol:
> [GATE_VALIDATION.md](GATE_VALIDATION.md); the honest-Sharpe audit of our own
> record is
> [studies/notebooks/validation_audit_2026_09/findings.md](studies/notebooks/validation_audit_2026_09/findings.md).

**A bot is a directory.** `bots/<name>/` holds `runner.py`, `config.py` and
`strategy/` — and that is the only place that bot's logic lives. There is no
orchestrator, no dispatch registry and no shared tick loop: each bot is its own
process, reads `prod.db`, and owns its variant rows. This replaced the
orchestrator path on 2026-09-13 (BACKLOG "bot = directory = strategy"); `bot.py`,
`strategies/orchestrator.py`, `p300_spec.py`, `backtest_runner.py` and the
simulation harness were deleted, and eight dormant sleeves moved to
[`studies/material/archive/`](studies/material/archive/README.md) — nothing that
runs may import them, enforced by
[tests/test_orchestrator_interface_gone.py](tests/test_orchestrator_interface_gone.py).

## The fleet

`start_fleet.ps1` launches these, feed first and dashboard last:

| Unit | Process | Variant id(s) | What it trades |
|---|---|---|---|
| feed | [feed.py](feed.py) | — | the only process that fetches; every bot reads `prod.db` |
| chento_v3 | [bots/chento_v3/runner.py](bots/chento_v3/runner.py) | `bot_chento_v3_v1` | Chento Triple v3 on BTC perp 15m — B1∩B5∩B7 composite, LONG/SHORT, TIF 72h |
| chento_v3_eth | [bots/chento_v3_eth/runner.py](bots/chento_v3_eth/runner.py) | `bot_chento_v3_eth` | same strategy, ETH leg (thin wrapper over the chento_v3 runner) |
| short_squeeze | [bots/short_squeeze/runner.py](bots/short_squeeze/runner.py) | `bot_short_squeeze_v1`, `bot_short_squeeze_nostop_v1` | S-105 sweep + perp/spot CVD divergence, BTC LONG |
| adx | [bots/adx/runner.py](bots/adx/runner.py) | `bot_adx_v1` | S-003 ADX trend flip on BTC, LONG/SHORT |
| carry | [bots/carry/runner.py](bots/carry/runner.py) | `bot_carry_v1` | S-078 delta-neutral funding harvest (spot long + perp short) |
| squeeze_bull | [bots/squeeze_bull/runner.py](bots/squeeze_bull/runner.py) | `bot_squeeze_bull_v1`, `bot_squeeze_bull_nostop_v1` | S-107 OI-flush bounce, BTC LONG |
| r4 | [bots/r4/runner.py](bots/r4/runner.py) | `bot_r4_v1` | R4 calendar windows; **ETH windows only** since 2026-09-12 (the BTC pair is wired but `ENABLED=False` in [bots/r4/config.py](bots/r4/config.py)) |
| dashboard | [dashboard/server.py](dashboard/server.py) | — | read-only UI on http://127.0.0.1:8300 |
| monitor | [monitor.py](monitor.py) | — | hourly freshness / heartbeat / silence checks — **optional unit, started only with `-Monitor`** |

Nine variants, seven bot units: the two squeeze bots each run a **no-stop paper
twin on the same signals in the same process** (added 2026-09-12), so their
ledgers differ only by the exit. Which variants a bot registers is its
`VARIANTS` / `VARIANT_ID` in `bots/<name>/config.py`.

Per-bot strategy detail — signals, sizing, leverage, edge thesis, caveats — is in
[PORTFOLIO.md](PORTFOLIO.md). Every dial change is logged in
[docs/calibration/](docs/calibration/) (one file per strategy — the two chento
bots share `chento_triple_v3.md`); the plain-language mechanism cards the
dashboard renders are in [dashboard/cards/](dashboard/cards/), one per bot.
`dashboard/botinfo.py` is what maps a bot to both.

## Start it

```powershell
.\start_fleet.ps1                  # everything that isn't already up
.\start_fleet.ps1 -Status          # what's running right now, then exit
.\start_fleet.ps1 -Units adx,carry # just two units
.\start_fleet.ps1 -Monitor         # also open the hourly monitor console
.\start_fleet.ps1 -DryRun          # print the commands and exit
```

One console per process, kept open after the process exits so a crash traceback
is never lost. A unit that is already running is **skipped, never started twice**
— the 2026-08-15..24 incident ran every bot doubled for nine days and chento
double-sized its signals. Run it as the same Windows user that owns the fleet,
or the process scan cannot read the fleet's command lines. If PowerShell refuses
to run the script: `powershell -ExecutionPolicy Bypass -File .\start_fleet.ps1`.

Other flags: `-SkipGapFix` (fast feed restart, no startup heal — do **not** use
after a long outage, the heal pass is what refills LSR/OI before the ~30d
upstream retention burns them) and `-ForceFeed` (only when the feed died less
than two minutes ago and its own stale-heartbeat guard refuses to start).

Each runner also stands alone:

```powershell
venv\Scripts\python.exe bots\adx\runner.py            # 60s ticks
venv\Scripts\python.exe bots\adx\runner.py --once     # single tick and exit
venv\Scripts\python.exe bots\adx\runner.py --verbose  # log idle tick statuses too
```

`--once` also unlocks the dry-run pair `--db <copy.db>` (run against a **copy**
of prod.db) and `--sim-now <iso>` (simulate a UTC timestamp). Every runner takes
the same five flags — `--once`, `--interval`, `--verbose`, `--db`, `--sim-now`.

## Check it

```powershell
venv\Scripts\python.exe health.py    # exit 0 = healthy; the checks and exit codes are in its docstring
venv\Scripts\python.exe monitor.py   # freshness + heartbeats + silence; exit 0 = green, 1 = alerts
venv\Scripts\python.exe monitor.py --deep   # plus an interior-gap scan (heavier; daily, not hourly)
venv\Scripts\python.exe feed.py --once      # one refresh cycle, to clear a stale table
```

`health.py` covers the databases, table freshness and continuity, regime/LSR
warmup depth, the single-open invariant, that every variant the bots are
configured to trade is registered and enabled, and that every entry point the
runners call still exists. `monitor.py` is the alerting counterpart (Telegram)
— but it is **not running and has no scheduled task on this machine**, so run
it by hand or with `-Monitor`. [backup.py](backup.py) is unscheduled too: the
newest full `prod.db` snapshot under `data/backups/` is from 2026-07-22, and
the LSR / open-interest history past the ~30d upstream retention is not
refetchable if the file is lost.

Reading the ledger:

```powershell
venv\Scripts\python.exe -m strategies.support.strategy_health --variant bot_chento_v3_v1

sqlite3 data/databases/prod.db "SELECT id, asset, strategy, direction, entry_price, size_usdt, actual_entry_time FROM trades WHERE strategy_variant LIKE 'bot_%' AND status='open' ORDER BY actual_entry_time DESC"

sqlite3 data/databases/prod.db "SELECT id, strategy_variant, asset, direction, pnl_pct, actual_exit_time FROM trades WHERE strategy_variant LIKE 'bot_%' AND status='closed' ORDER BY actual_exit_time DESC LIMIT 20"
```

Day-to-day runbook — troubleshooting, invariants, adding a bot, the dashboard's
panels: [OPERATIONS.md](OPERATIONS.md).

## Bootstrap (one time)

```bash
pip install -r requirements.txt

# Coinalyze free key (https://coinalyze.net/) — needed once, for the initial
# LSR history fetch. feed.py keeps the table fresh afterwards. PowerShell:
#   $env:COINALYZE_API_KEY = "..."      (or put it in the repo-root .env)
export COINALYZE_API_KEY=...

# Build data/databases/prod.db from scratch — calendar, LSR, klines, funding.
# Slow on first run (~30-60 min for 5y of 1m klines). Idempotent.
python bootstrap.py
#   --skip-klines      CSVs + funding only (~1 min)
#   --skip-coinalyze   no key yet
#   --since 2024-01-01 shorter history
```

Then `.\start_fleet.ps1` — each bot registers its own variant rows on first
start. To fill the deferred kline history later, in the background:

```bash
python -m data.sources.binance --backfill-klines --since 2020-01-01
```

`feed.py` self-heals at startup: it scans every cadence-based table for missing
rows and fetches each gap window. The first run on a sparse DB can take ~20
minutes; every subsequent run is sub-second.

**It heals gaps, not depth.** `--skip-klines` leaves a DB holding only the
rolling window the feed has since filled, and the startup gap-fix will fill
*interior* holes but will never extend history below `MIN(open_time)`. Only an
explicit `--backfill-klines --since <date>` does that. A study that quietly ran
on two years of data when it meant five is the failure this note exists to
prevent.

## Where the code lives

```
p300/
├── start_fleet.ps1                 # starts feed + 7 bots + dashboard, one console each
├── feed.py                         # the only fetcher; 60s cycle + startup gap heal
├── health.py                       # 9 invariant checks for live operation
├── monitor.py                      # freshness / heartbeat / silence alerts (unscheduled)
├── backup.py                       # VACUUM INTO snapshot of prod.db (unscheduled)
├── bootstrap.py                    # one-shot prod.db builder
├── botlib.py                       # the shared bot runtime: freshness contracts,
│                                   #   heartbeats, variant registration, sizing, dry-run flags
├── fetch_events.py                 # rebuilds scheduled_events (FOMC/CPI/NFP/OPEX)
├── fetch_coinalyze.py              # LSR history beyond Binance's 30d window
├── bots/<name>/                    # ONE BOT = ONE DIRECTORY
│   ├── runner.py                   #   the process: tick loop, heartbeat, ledger writes
│   ├── config.py                   #   what the BOT decides — variants, sizing, staleness policy
│   └── strategy/                   #   what the STRATEGY decides — signal.py, config.py, math.py
├── strategies/
│   ├── trades.py                   # the ledger-write layer (open / close / adjust)
│   └── support/                    # shared live-path modules: clock, price_feed, funding,
│                                   #   jplus_inputs, regime_jplus, voltarget, gate, ema_position,
│                                   #   trade_db, variant_registry, strategy_health,
│                                   #   ledger_coherence, db (path constants)
│                                   # also present, with NO live importer:
│                                   #   margin_sim, risk_caps, regime_tactical — see PORTFOLIO §6
├── dashboard/                      # read-only web UI (server, procscan, market, botinfo, cards)
├── data/
│   ├── databases/prod.db           # the one DB — market data + bot state
│   ├── sources/                    # external-data fetchers (binance, okx, bybit, coinbase,
│   │                               #   deribit, macro_yahoo, coindesk, fed_funds, …)
│   ├── backups/                    # backup.py snapshots
│   └── known_unfillable.json       # gaps verified to be source-side holes
├── docs/calibration/<strategy>.md  # the calibration log — every dial change, dated
├── studies/                        # research only; nothing here runs in the fleet
│   ├── notebooks/                  # per-study directories, each with a findings.md
│   ├── lib/                        # reusable research code, incl. lib/validation/
│   └── material/archive/           # 8 archived sleeves — importable, unsupported, never live
└── tests/                          # ~90 files; look-ahead canaries, golden guards, archive guard
```

`strategies/` no longer contains a single strategy. `strategies/support/` is the
shared live path only — anything strategy-specific belongs under its bot.

## Data tables

All tables live in `data/databases/prod.db`. `feed.py` is the only writer of the
**market-data** tables below; the bots write `trades`, `trade_adjustments`,
`bot_heartbeats`, `variants` and `variant_events`, and `fetch_events.py` builds
`scheduled_events`. Eight processes write this file concurrently, which is why
everything is WAL and read paths open `mode=ro`.

Freshness limits are `botlib.FRESHNESS_CONTRACTS`; each bot names the tables it
cannot trade without in its `MGMT_TABLES` (stale ⇒ skip the whole tick) and
`ENTRY_TABLES` (stale ⇒ manage positions, but open nothing new).

| Table | Source | Refresh | Read by |
|-------|--------|---------|---------|
| `btc_1m`, `eth_1m` | Binance spot klines | `feed.py` every 60s | `price_feed` — the mark price for every bot's position management; r4 window fills |
| `cd_futures_ohlcv` | Binance BTCUSDT perp 1h | `feed.py` every 60s | squeeze_bull, short_squeeze, carry (the perp leg of the basis) |
| `cd_spot_binance` | Binance BTCUSDT spot 1h | `feed.py` every 60s | adx (daily ADX/EMA), carry, r4 (regime + gate inputs) |
| `cd_futures_15m`, `cd_spot_15m` | Binance BTCUSDT perp/spot 15m with taker buy/sell split | `feed.py` every 60s | chento_v3, short_squeeze (CVD) |
| `cd_futures_eth_15m` | Binance ETHUSDT perp 15m | `feed.py` every 60s | chento_v3_eth |
| `okx_perp_1h`, `okx_perp_eth_1h` | OKX BTC/ETH-USDT-SWAP 1h | `feed.py`, hourly throttle | chento_v3 + chento_v3_eth cross-exchange gate |
| `cd_open_interest` | Binance native OI (~30d retention) | `feed.py` every 60s | squeeze_bull (flush), short_squeeze |
| `cd_funding_rate` | Binance BTC perp funding | `feed.py` every 60s | carry, adx, short_squeeze |
| `cd_funding_rate_eth` | Binance ETH perp funding | `feed.py` every 60s | `strategies.support.funding` / `equity` — funding accrual on the ETH legs |
| `ca_long_short_ratio` | Coinalyze (history) + Binance rolling 30d | `fetch_coinalyze.py` once + `feed.py` every 60s | chento_v3, r4 (regime), the J+ LS circuit breaker |
| `scheduled_events` | computed by `fetch_events.py` (FOMC/CPI hardcoded, NFP/OPEX rules) | annual: bump the FOMC/CPI lists, re-run | no bot reads it since the calendar sleeves were archived; kept fresh because `monitor.py` alerts below 60d of runway and the archived studies need it |
| `paxg_spot_1h` | Binance PAXGUSDT spot 1h (tokenised gold, since 2020-08) | `feed.py` every 60s; backfill `python -m data.sources.binance --backfill-paxg` | anchor-allocator study (GOLD leg) |
| `macro_daily` | Yahoo daily SPX/DXY/VIX/TNX/GOLD/IEF/TLT (since 2000) | `feed.py` once per UTC day via `data/sources/macro_yahoo.py`; seed `--seed-from`, deep pull `--backfill` | anchor-allocator study, regime context |
| `deribit_dvol_daily`, `deribit_options_instruments`, `deribit_options_daily` | Deribit public API: DVOL daily (since 2022-09), option instruments, liquid-subset book snapshots 00:05/08:05 UTC; history seeded from the trader-repo CoinDesk snapshot (2023-12→2026-04, `source='coindesk_seed'`) | `feed.py` via `data/sources/deribit.py` (self-throttled); CLI `--seed-from`, `--backfill-dvol`, `--snapshot` | VRP study; supersedes gated `cd_dvol` |
| `coinbase_spot_1h` | Coinbase Exchange BTC-USD/ETH-USD spot 1h (since 2020-01), one row per (asset, hour); history seeded from the trader-repo snapshot (→2026-04-14), live API onwards | `feed.py` via `data/sources/coinbase.py` (one pull per asset per UTC hour); CLI `--seed-from`, `--backfill [--since]` | Coinbase-premium study (US-venue spot basis vs `cd_spot_binance`) |
| `okx_funding`, `bybit_funding` | OKX (`BTC-USDT-SWAP`, `ETH-USDT-SWAP`) and Bybit (`BTCUSDT`, `ETHUSDT`) funding settlements on the 8h grid, epoch-second timestamps that join straight onto `cd_funding_rate` / `cd_funding_rate_eth`. OKX serves a rolling ~3 months only (from 2026-06-08); Bybit reaches back to 2020-03-25 (BTC) / 2020-10-21 (ETH). No predecessor seed exists — the OKX series grows forward from 2026-06-08 | `feed.py` via `data/sources/venue_funding.py` (one pull per instrument per UTC hour); CLI `--backfill [--since]` | funding-dispersion (delta-neutral) study |
| `binance_quarterly_1h` | Binance USDⓈ-M quarterly futures 1h, continuous-contract slots (`CURRENT_QUARTER`, `NEXT_QUARTER`) for BTCUSDT + ETHUSDT, one row per (pair, contract_type, hour). CURRENT_QUARTER runs unbroken from 2021-02-03 (BTC) / 2021-02-04 (ETH); NEXT_QUARTER from 2021-03-16 with five structural holes in 2022-04→2023-08 when Binance listed no far quarterly (recorded in `known_unfillable.json`). `series` is a virtual generated column (`pair-contract_type`) so `check_gaps` can group on one column | `feed.py` via `data/sources/binance_quarterly.py` (one pull per slot per UTC hour); CLI `--backfill [--since]` | perp-vs-quarterly basis study |
| `binance_quarterly_contracts` | The listed quarterly contracts from `/fapi/v1/exchangeInfo` (symbol, pair, contract_type, deliveryDate, onboardDate) with first/last-seen stamps — the roll calendar for the slots above. Only ever shows contracts listed *now*, so it accumulates forward from 2026-09-08 | `feed.py` via `data/sources/binance_quarterly.py` (once per UTC day); CLI `--contracts` | perp-vs-quarterly basis study (roll dating) |

Three read-path caveats on the 2026-09 feeds, verified 2026-09-08:

- **`deribit_options_daily` is mostly marks and nothing else.** 495,419 of its
  496,429 rows are the CoinDesk seed, and in those `mark_price`, `mark_iv`,
  `bid_price`, `ask_price`, `underlying_price` and `open_interest` are 100 %
  NULL — only `mark_price_usd` (and `volume`) survive the seed. Implied vol,
  open interest, spreads and the underlying exist for the 1,010 live rows
  only, i.e. from 2026-09-06 onward. Filter on the column you need, not on
  the row count.
- **The newest hourly row is the forming bar.** `coinbase_spot_1h` and
  `binance_quarterly_1h` both store the current, incomplete hour and overwrite
  it when the hour closes. Anything reading to `MAX(timestamp)` must drop the
  final bar, the same rule the rest of the read path already follows.
- **One malformed listing bar.** `binance_quarterly_1h` BTCUSDT
  CURRENT_QUARTER at 2021-02-03 08:00 (the series' first bar) violates the
  OHLC invariant at source: `high` 35,999.4 below `open` 36,054.1, `close`
  39,550.0 above `high`, on 4.48 of volume. Exclude it. No other row in the
  table breaks the invariant.

If `ca_long_short_ratio` shows a gap more than 30 days old, Binance can't reach
back that far — run `python fetch_coinalyze.py` to fill it. If
`scheduled_events` runs out of future rows, bump the FOMC/CPI lists in
`fetch_events.py` and re-run it.

## What this does NOT do

- **No live order placement.** All trades are paper — write-only to the `trades`
  table. There is no exchange-side execution path.
- **No backtest engine in this repo.** `bot.py`, `backtest_runner.py` and
  `studies/simulation/sim.py` were deleted on 2026-09-13; the replay/sim path
  they shared went with the orchestrator. Research runs in
  `studies/notebooks/`, one directory per study with a dated `findings.md`. A
  single bot can still be driven under a frozen clock with
  `runner.py --once --db <copy.db> --sim-now <iso>`.
- **No backtest equity seed.** Equity attribution starts from the first clean
  live paper fill; the older daily-returns panel was removed as compromised.
- **No verdicts yet.** In the 2026-09-08 validation audit no series reaches a
  deflated Sharpe of 0.95, and the paper ledgers were statistically empty —
  six closed trades across six bots. Read the caveats in
  [PORTFOLIO.md](PORTFOLIO.md) §9 before quoting a number from anywhere.

## Where to read more

| Question | Document |
|---|---|
| How do I run, watch and fix the fleet? | [OPERATIONS.md](OPERATIONS.md) |
| What does each bot actually trade, and at what size? | [PORTFOLIO.md](PORTFOLIO.md) |
| What is being worked on, held or killed right now? | [BACKLOG.md](BACKLOG.md), top section |
| How does a new gate or strategy prove itself? | [GATE_VALIDATION.md](GATE_VALIDATION.md) |
| Why is this bot's dial set to that number? | [docs/calibration/](docs/calibration/) |
| What did study X conclude? | `studies/notebooks/<study>/findings.md` |
| Why was a sleeve archived, and can it come back? | [studies/material/archive/README.md](studies/material/archive/README.md) |
| Can I trade this by hand? | [MANUAL.md](MANUAL.md) — written for the orchestrator-era sleeve set, not the current fleet |

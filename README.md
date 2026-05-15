# p300 — standalone P-300 paper-trading bot

Self-contained implementation of the **P-300 Aggressive 2.0 1.0** strategy.
Runs as a paper variant — writes phantom trades to a local sqlite DB and
never places orders on any exchange. The repo has no dependency on any
upstream `trader` research repo: market data is pulled from Binance public
REST, scheduled events are computed in-process, and long-short ratio
history comes from Coinalyze (free tier).

> **Status: paper validation in progress.** All prior upstream backtest
> numbers are treated as compromised and have been stripped. The earlier
> headline (Sharpe ≈ 1.73, bootstrap 95% CI [0.96, 2.44]) was computed
> from a `variant_daily_returns WHERE source='replay'` table that the
> 2026-05 trade-emitter migration stopped writing — it is **not
> reproducible from current code** and has been retracted pending
> regeneration on the realized trade ledger. The migrated tool
> (`python studies/notebooks/tools_statistical_validation.ipynb`) now runs against
> closed paper trades directly; paper accumulation is the only real
> OOS validation path, and the live-window Sharpe stabilises only after
> ~6+ months of realized data.

## What runs live here

Sleeves dispatched per-minute via `strategies/orchestrator.py`. All sleeves
emit real-time trades to the same `trades` table; realized PnL is the
trade-ledger sum (no parallel theoretical-return track):

- **Core J+ sub-sleeves (50%)** — six discrete-entry sleeves dispatched
  uniformly with the tactical stack:
  R4_BTC (Mon wk1-2 06:00→18:00 UTC), R4_ETH (Tue 20:00 → Wed 20:00 UTC
  wk1-2), R4_BTC_V2 / R4_ETH_V2 (Wed+Fri wk1-2 04:00→14:00 UTC),
  EMA_BTC (continuous, weekly EMA(5)/EMA(21) crossover), ETH_DAILY
  (continuous in bull regimes only). Sized per-tick from
  [`jplus.simulate.today_inputs()`](strategies/support/jplus_inputs.py) — regime-weighted
  sub-sleeve weights × inner R4 lev × vol-target lev, all from T-1 data.
- **Six tactical sleeves (50%)** — S-003 ADX, S-078 Carry, S-096 V4 Thu
  Bear, S-102 PDO-L-RF, S-101 CPR, S-103 FOMC. Discrete entries/exits in
  BTC and ETH.
- **AI_QUANT (2%, inside the tactical 50%, default-OFF)** — discretionary
  LLM trader using Anthropic Opus 4.7 once per UTC day. Gated behind
  `AI_QUANT_ENABLED` env var; paper-only like every other sleeve; skipped
  on historical replay. Per-decision markdown archive under
  `data/ai_quant_archive/`. (Was additive; folded into the 50% cap on
  2026-05-12 — PDO trimmed from 11% to 9% to make room.)

See [PORTFOLIO.md](PORTFOLIO.md) for the canonical per-sleeve reference
(signals, entries, exits, leverage stack, regime weights, edge thesis,
caveats). It stays in sync with [register_p300.py](register_p300.py); this
README is intentionally a thin pointer to avoid duplication drift.

The original ML gate was replaced with a deterministic vol-percentile rule
(see [strategies/support/gate.py](strategies/support/gate.py)) after we found the upstream gate's
features used same-day data and could not be reproduced without look-ahead.

The simulator-driven daily-return accrual and the offline-period catchup
emitter were removed in the 2026-05-10 live/sim refactor — Core sub-sleeves
now have the same operational shape as tactical sleeves
(if the bot is offline during a window, that trade is missed permanently).
The analytic [`jplus.simulate.simulate()`](strategies/support/jplus_inputs.py) function
remains as a research-only tool with no runtime caller.

## Bootstrap (one time)

```bash
# 1. Install dependencies (numpy only)
pip install -r requirements.txt

# 2. Get a free Coinalyze API key (https://coinalyze.net/) and export it.
#    Needed once for the initial LSR history fetch (~5 years).
export COINALYZE_API_KEY=...

# 3. Build data/prod.db from scratch — calendar, LS ratio, klines, funding.
#    Slow on first run (~30-60 min for 5y of 1m klines). Idempotent.
python bootstrap.py

# 4. Register the P-300 variant in data/prod.db
python register_p300.py
```

For a faster bootstrap that defers the slow kline backfill:

```bash
python bootstrap.py --skip-klines     # CSVs + funding only (~1 min)
# Run later, in the background, when convenient:
python binance_feed.py --backfill-klines --since 2020-01-01
```

For a no-Coinalyze bootstrap (CPR will be dormant for ~6 months while
binance_feed accumulates the rolling 30d LS window):

```bash
python bootstrap.py --skip-coinalyze
```

## Run

```bash
# Standalone loop — orchestrator + binance data feed in one process,
# 60s ticks, console noise filtered. Idle/heartbeat lines (no_signal,
# tick ok, [feed] etc.) are hidden by default; add --verbose to show
# every line.
python bot.py

# Smoke test (one tick and exit; skips the feed thread and gap-fix)
python bot.py --once

# Fast restart — skip the startup gap-fix pass
python bot.py --skip-gap-fix
```

If you'd rather drive the feed as a separate process:

```bash
python binance_feed.py                # gap-fix pass + loop every 60s
python binance_feed.py --once         # gap-fix pass + one tick + exit
python binance_feed.py --skip-gap-fix # skip the gap pass (faster restart)
```

`binance_feed.py` self-heals at startup: it scans every cadence-based
table (klines + funding) for missing rows, then fetches each gap window
from Binance. The first run on a sparse DB can take ~20 minutes; every
subsequent run is sub-second.

## Run in sim mode

Sim mode is the same bot binary running against a separate sim trader.db
and a separate sim dashboard.db, with a deterministic simulated clock —
no live API calls, no contamination of the live ledger. Identical
dispatch to live, so a sleeve that fires in sim is exactly the same
code path that fires in live.

```bash
# 1. Build a date-range slice of trader.db. The result is self-contained;
#    sim never reaches back to the source DB or the network.
python studies/simulation/build_sim_trader_db.py \
    --start 2024-01-01 --end 2024-12-31 \
    --output data/trader_sim_2024.db

# 2. Register the variant in a fresh dashboard sim DB.
python register_p300.py --dash-db /tmp/sim_dash.db

# 3. Run sim mode. Inclusive date range; --sim-tick-seconds advances
#    the simulated clock per tick (no wall-clock sleep).
python studies/simulation/sim.py \
    --start 2024-01-01 --end 2024-12-31 \
    --trader-db data/trader_sim_2024.db \
    --dash-db /tmp/sim_dash.db \
    --sim-tick-seconds 60

# 4. Report the sim run from the trade ledger:
python studies/notebooks/full_portfolio_report.ipynb  # or run the .ipynb
```

### Choosing `--sim-tick-seconds`

Each tick advances the simulated clock by `--sim-tick-seconds`. Lower
values give finer granularity; higher values run faster.

| `--sim-tick-seconds` | Behavior | When to use |
|---|---|---|
| **60** (default) | Same cadence live runs at. Tactical sleeves' "fire when in window" check sees every minute. | Operator-style sims where you want the result to look exactly like a live run over that window. |
| **3600** | One tick per hour. Calendar-driven sleeves (R4_BTC, R4_ETH, V2 sleeves, FOMC) still fire correctly because their entry hour is on a clean hour boundary. | Long backtests where you trust the dispatch logic and want the run to finish in minutes instead of hours. |
| **300** | 5 minutes. | A reasonable compromise — captures intra-hour tactical fires (e.g. CARRY's per-tick funding accrual) without paying the full 60s tick cost. |

A signal that fires at HH:00 hits at the same simulated tick under
all three settings — calendar sleeves are tick-granularity-invariant.
Tactical sleeves whose firing decisions depend on intra-hour state
(e.g. CARRY checking funding every tick) will see fewer or more
opportunities. Pick the granularity that matches what you're trying
to measure.

The sim/backtest_runner parity test
(`tests/test_sim_mode.py:test_sim_and_backtest_runner_produce_identical_jplus_trades`)
runs both at 1h ticks and verifies J+ sub-sleeves produce byte-identical
trades — proving calendar-anchored sleeves are granularity-invariant.

### Resuming an interrupted sim run

Sim mode is **idempotent per UTC day** because every sleeve checks
the trades table for an existing row before opening a new one
(``services.jplus_live._has_trade_for_day`` and the equivalent in
each tactical sleeve). So if a long sim run is interrupted — kill
signal, OOM, you closed the laptop — you can resume by re-launching
the same command with a `--start` at or before the last completed
date. Already-fired trades are no-ops on the re-tick.

```bash
# Original run, killed somewhere in mid-2024:
python studies/simulation/sim.py --start 2024-01-01 --end 2024-12-31 \
    --trader-db trader_sim.db --dash-db sim_dash.db

# Find the last completed UTC date in the sim ledger:
sqlite3 sim_dash.db \
    "SELECT MAX(date(actual_entry_time)) FROM trades \
     WHERE strategy_variant='p300_aggressive_v2_v1_0'"
# ⇒ 2024-07-13

# Resume — re-running 07-13 is safe (idempotent), continues from there:
python studies/simulation/sim.py --start 2024-07-13 --end 2024-12-31 \
    --trader-db trader_sim.db --dash-db sim_dash.db
```

The cost of starting a few days before the killpoint is just a few
hundred no-op tick-and-skip iterations — much cheaper than restarting
the whole sim from January.

### Which sim tool — `studies/simulation/sim.py` or `backtest_runner.py`?

Both drive the live bot under a fake clock via the same
[strategies/support/sim_loop.py](strategies/support/sim_loop.py)
primitive — but they differ in **where output lands** and **which
features they layer on top**:

|  | `studies/simulation/sim.py` | `backtest_runner.py` |
|---|---|---|
| Output ledger | separate `--dash-db` file | live `data/prod.db` (variant id suffixed `__replay[_<tag>]`) |
| Live data isolation | **complete** — separate prod.db | shares `data/prod.db` (read) + `data/prod.db` (writes to its own variant) |
| Liquidation simulator | YES (via `orchestrator.tick` — same `force_close_liquidations` path) | YES (`force_close_liquidations`) |
| Mark-to-end-of-window for trades open at end | NO | YES (`mark_remaining_at_end`) |
| Per-sleeve PnL summary | uses `strategy_health.build_report` | bespoke report block |
| `--reset` purges prior runs | NO (use a fresh `--dash-db`) | YES |
| `--tag` for parallel A/B runs | NO | YES |
| `--with-fomc` injects FOMC sleeve mid-run | NO | YES |
| `--skip <strategy>` excludes one sleeve | NO | YES |

**Pick `studies/simulation/sim.py`** when you want a clean *operator-style*
sim (does the bot work end-to-end on this date range?) with no risk
of touching the live ledger.

**Pick `backtest_runner.py`** when you want *research workflow* —
parameter sweeps, A/B comparisons, liquidation-aware long-window
backtests, or anything where keeping multiple result sets in one DB
helps.

There is no "third option": the two tools share their dispatch
(STRATEGY_DISPATCH) and clock primitive, so a sleeve that fires under
one fires identically under the other.

## Inspect state

```bash
# Variant metadata
python -c "from services import variant_registry as r; print(r.get_variant('p300_aggressive_v2_v1_0')['status'])"

# Open paper positions
sqlite3 data/prod.db "SELECT id, asset, strategy, direction, entry_price, size_usdt FROM trades WHERE strategy_variant='p300_aggressive_v2_v1_0' AND status='open'"

# Closed trades (newest first)
sqlite3 data/prod.db "SELECT id, asset, strategy, direction, pnl_pct, actual_exit_time FROM trades WHERE strategy_variant='p300_aggressive_v2_v1_0' AND status='closed' ORDER BY actual_exit_time DESC LIMIT 20"
```

## Architecture

```
p300/
├── bot.py                         # paper-trading entry point (60s tick, noise-filtered)
├── bootstrap.py                   # one-shot data/prod.db builder
├── register_p300.py               # registers the variant in prod.db
├── health.py                      # 8 invariant checks for live operation
├── fetch_events.py                # rebuilds scheduled_events (FOMC/CPI/NFP/OPEX)
├── fetch_coinalyze.py             # fetches ca_long_short_ratio history (Coinalyze)
├── backtest_runner.py             # clock-driven replay over a date window
├── data/
│   ├── prod.db                    # consolidated DB — market data + bot state
│   │                              #   (P2.6 unified trader.db + dashboard.db)
│   ├── known_unfillable.json      # gaps known to be unfillable
│   ├── ai_quant_archive/          # per-decision markdown mirror
│   └── ai_quant_preview/          # context-bundle preview snapshots
├── data/sources/                  # external-data fetchers
│   ├── binance.py                 # klines + funding refresh + gap-fix
│   ├── fed_funds.py               # NY Fed XML + rate-cycle phase classifier
│   ├── sentiment.py               # Fear & Greed index (alternative.me)
│   ├── polymarket.py              # Polymarket-implied rate expectations
│   ├── coindesk.py                # CoinDesk funding / OI / vol / liquidations
│   └── news.py                    # news headlines (AI_QUANT context)
├── strategies/                    # ALL strategy code (live + math + state)
│   ├── orchestrator.py            # per-tick scheduler; injects _effective_*
│   │                              #   (P2.4 — weight / leverage / gate / vol scalar)
│   ├── trades.py                  # paper-trade open/close persistence
│   ├── sleeves/                   # one folder per sleeve
│   │   ├── adx/                   # S-003 ADX
│   │   ├── carry/                 # S-078 delta-neutral carry
│   │   ├── thu_bear/              # S-096 V4 Thursday-bear
│   │   ├── pdo/                   # PDO-L-RF
│   │   ├── cpr/                   # CPR
│   │   ├── fomc/                  # FOMC long T-10h → T+0.5h
│   │   ├── ai_quant/              # discretionary LLM trader (default-off)
│   │   ├── r4/                    # JPLUS_R4_BTC / _ETH / _BTC_V2 / _ETH_V2
│   │   ├── ema/                   # JPLUS_EMA_BTC
│   │   └── eth_daily/             # JPLUS_ETH_DAILY
│   └── support/                   # shared math + state services
│       ├── db.py                  # path constant (PROD_DB; TRADER_DB/DASH_DB alias it)
│       ├── allocation.py          # per-(sleeve, regime) WEIGHT_TABLE (P2.4a)
│       ├── gating.py              # GateDecision + GATE_REGISTRY (P2.4b)
│       ├── portfolio_vol.py       # current_vol_scalar (P2.4c)
│       ├── clock.py               # injectable clock for deterministic replay
│       ├── sim_loop.py            # bot.py-shared sim primitive
│       ├── price_feed.py          # last-close reader with staleness guard
│       ├── indicators.py          # pure EMA / ADX math (no I/O)
│       ├── voltarget.py           # vol-target leverage (J+ today)
│       ├── gate.py                # R4 vol-percentile gate math
│       ├── jplus_inputs.py        # today_inputs() — regime/lev/gate/ema/weights
│       ├── regime_jplus.py        # J+ 4-state classifier
│       ├── regime_tactical.py     # tactical regime (bull/bear/chop/sell_off)
│       ├── margin_check.py        # liquidation orchestration + math adapter
│       ├── margin_sim.py          # margin / liquidation simulator
│       ├── risk_caps.py           # cross-sleeve BTC-long cap
│       ├── risk_config.py         # SL semantic (price-move vs margin-loss)
│       ├── strategy_health.py     # realized-PnL aggregation
│       ├── trade_db.py            # trade log schema
│       ├── variant_registry.py    # variant CRUD
│       ├── sleeves.py             # strategy_id constants
│       └── env.py                 # stdlib .env loader
├── studies/                       # research-only code
│   ├── simulation/                # sim.py (sim entry point), migrate_*, build_sim_trader_db.py
│   ├── notebooks/                 # .ipynb research (per-sleeve backtests, PDO TV validation, R4 study)
│   ├── reports/                   # ad-hoc report generators
│   └── jplus_analytic/            # offline simulate() — preserved for research
├── tests/                         # >670 tests incl. look-ahead canary, sim e2e, parity
└── requirements.txt               # numpy + (optional) anthropic for AI_QUANT
```

## Data the services need

| Table | Source | Refresh | Used by |
|-------|--------|---------|---------|
| `btc_1m`, `eth_1m` | Binance spot klines | `binance_feed.py` every 60s | pdo, cpr, price_feed (ETH) |
| `cd_futures_ohlcv` | Binance BTCUSDT perp 1h | `binance_feed.py` every 60s | adx, carry, regime_classifier, price_feed (BTC) |
| `cd_spot_binance` | Binance BTCUSDT spot 1h | `binance_feed.py` every 60s | carry |
| `cd_funding_rate` | Binance BTC perp funding | `binance_feed.py` every 60s | services.funding (BTC: carry, cpr, adx exit, fomc exit) |
| `cd_funding_rate_eth` | Binance ETH perp funding | `binance_feed.py` every 60s | services.funding (ETH: thu_bear, fomc exit) |
| `ca_long_short_ratio` | Coinalyze (history) + Binance rolling 30d | `fetch_coinalyze.py` once + `binance_feed.py` every 60s | cpr, regime LS circuit breaker |
| `scheduled_events` | computed by `fetch_events.py` (FOMC/CPI hardcoded, NFP/OPEX rules) | annual: bump FOMC/CPI lists, re-run | S-096 V4 filter, regime no-FOMC rule |

If `ca_long_short_ratio` shows a gap >30 days old, Binance can't reach back
that far — run `python fetch_coinalyze.py` to fill it. If `scheduled_events`
is empty or out-of-date, S-096 V4 **fails closed** (skips the Thursday
rather than degrading to an unconditional V1 short); re-run
`python fetch_events.py` to repopulate.

## Known methodology caveats

These are structural properties of the sleeves themselves, independent of
whether any specific backtest is trusted:

- **S-096 V4 event filter is in-sample.** It was derived post-hoc from V3's
  Thursday attribution using the same CPI/NFP/OPEX calendar it now gates on.
  Any backtest that reuses that calendar will outperform V3 by construction.
  Only a genuine live OOS record separates signal from curve-fit.
- **CPR sample is extremely thin** (n=12 BTC + n=9 ETH in upstream research).
  Neither prior work nor our future replay will have statistical power;
  live accumulation is the only real validation path.
- **PDO regime threshold and gap/tolerance params were selected via sweeps**
  without visible walk-forward CV — data-snooping exposure carries into any
  future backtest.
- **Aggressive 2.0 was selected from a 4-tier family** (Conservative /
  Regime-dynamic / Kelly / Aggressive) **by backtest performance** — there
  is family-level selection bias baked into the pick before we even evaluate it.
- **`btc_1m` historical depth depends on whether you ran the kline backfill.**
  `bootstrap.py --skip-klines` produces a DB with only the rolling window
  binance_feed has filled; full 5y of 1m bars requires either the
  `bootstrap.py` kline backfill or `binance_feed.py --backfill-klines
  --since 2020-01-01`. `binance_feed.py`'s startup gap-fix will also
  fill internal gaps incrementally on every restart — but won't extend
  history below `MIN(open_time)` without an explicit `--since`. PDO
  hourly-touch and CPR intraday aggregation are coarser before the
  backfill catches up.
- **Live BTC-long cap uses skip-if-over**; any future simulator-style
  proportional scale-down will diverge by construction.

## Caveats / what this extraction does NOT do

- **No live order placement.** Extending to real MEXC execution would require
  wiring through an `execution_service` that the stripped `variant_engine`
  doesn't have — this bot only writes phantom trades.
- **No dashboard.** Introspection is via sqlite queries (examples above) and
  `python studies/notebooks/backtest_report.ipynb --variant <id>` for replay metrics.
- **No prior-backtest equity seed.** Any daily-returns panel present in older
  versions of this repo has been removed as compromised. Equity attribution
  begins from the first clean replay or live paper fill.
- **`ca_long_short_ratio` history beyond Binance's 30d window** must come from
  Coinalyze. After bootstrap, the rolling 30d refresh from Binance is enough
  to keep things current; the ~5y of pre-bootstrap history relies on the
  initial Coinalyze fetch.

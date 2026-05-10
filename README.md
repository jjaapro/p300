# p300 — standalone P-300 paper-trading bot

Self-contained implementation of the **P-300 Aggressive 2.0 1.0** strategy.
Runs as a SHADOW variant — writes phantom trades to a local sqlite DB and
never places orders on any exchange. The repo has no dependency on any
upstream `trader` research repo: market data is pulled from Binance public
REST, scheduled events are computed in-process, and long-short ratio
history comes from Coinalyze (free tier).

> **Status: SHADOW validation in progress.** All prior upstream backtest
> numbers are treated as compromised and have been stripped. A clean replay
> over 2021-07 → 2026-04 with no look-ahead produced Sharpe ≈ 1.73 with a
> bootstrap 95% CI of [0.96, 2.44] (`python tools/tools_statistical_validation.py`).
> Live SHADOW accumulation is the only real OOS validation path.

## What runs live here

Sleeves dispatched per-minute via `services/variant_engine.py`. All sleeves
emit real-time trades to the same `trades` table; realized PnL is the
trade-ledger sum (no parallel theoretical-return track):

- **Core J+ sub-sleeves (50%)** — six discrete-entry sleeves dispatched
  uniformly with the tactical stack:
  R4_BTC (Mon wk1-2 06:00→18:00 UTC), R4_ETH (Tue 20:00 → Wed 20:00 UTC
  wk1-2), R4_BTC_V2 / R4_ETH_V2 (Wed+Fri wk1-2 04:00→14:00 UTC),
  EMA_BTC (continuous, weekly EMA(5)/EMA(21) crossover), ETH_DAILY
  (continuous in bull regimes only). Sized per-tick from
  [`jplus.simulate.today_inputs()`](jplus/simulate.py) — regime-weighted
  sub-sleeve weights × inner R4 lev × vol-target lev, all from T-1 data.
- **Six tactical sleeves (50%)** — S-003 ADX, S-078 Carry, S-096 V4 Thu
  Bear, S-102 PDO-L-RF, S-101 CPR, S-103 FOMC. Discrete entries/exits in
  BTC and ETH.
- **AI_QUANT (additive 2%, default-OFF)** — discretionary LLM trader using
  Anthropic Opus 4.7 once per UTC day. Gated behind `AI_QUANT_ENABLED` env
  var; shadow-only like every other sleeve; skipped on historical replay.
  Per-decision markdown archive under `data/ai_quant_archive/`.

See [PORTFOLIO.md](PORTFOLIO.md) for the canonical per-sleeve reference
(signals, entries, exits, leverage stack, regime weights, edge thesis,
caveats). It stays in sync with [register_p300.py](register_p300.py); this
README is intentionally a thin pointer to avoid duplication drift.

The original ML gate was replaced with a deterministic vol-percentile rule
(see [jplus/gate.py](jplus/gate.py)) after we found the upstream gate's
features used same-day data and could not be reproduced without look-ahead.

The simulator-driven daily-return accrual and the offline-period catchup
emitter were removed in the 2026-05-10 live/sim refactor — Core sub-sleeves
now have the same operational shape as tactical sleeves
(if the bot is offline during a window, that trade is missed permanently).
The analytic [`jplus.simulate.simulate()`](jplus/simulate.py) function
remains as a research-only tool with no runtime caller.

## Bootstrap (one time)

```bash
# 1. Install dependencies (numpy only)
pip install -r requirements.txt

# 2. Get a free Coinalyze API key (https://coinalyze.net/) and export it.
#    Needed once for the initial LSR history fetch (~5 years).
export COINALYZE_API_KEY=...

# 3. Build data/trader.db from scratch — calendar, LS ratio, klines, funding.
#    Slow on first run (~30-60 min for 5y of 1m klines). Idempotent.
python bootstrap.py

# 4. Register the P-300 variant in data/dashboard.db
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
# Standalone loop (ticks every 60s)
python run.py

# Loop + refresh Binance data in the same process
python run.py --feed

# Smoke test (one tick and exit)
python run.py --once
```

Keep the Binance data feed running in a second terminal if you prefer a
separate process:

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
python tools/build_sim_trader_db.py \
    --start 2024-01-01 --end 2024-12-31 \
    --output data/trader_sim_2024.db

# 2. Register the variant in a fresh dashboard sim DB.
python register_p300.py --dash-db /tmp/sim_dash.db

# 3. Run the bot in sim mode. Inclusive date range; sim-tick-seconds
#    advances the simulated clock per tick (no wall-clock sleep).
python run.py --mode sim \
    --start 2024-01-01 --end 2024-12-31 \
    --trader-db data/trader_sim_2024.db \
    --dash-db /tmp/sim_dash.db \
    --sim-tick-seconds 60

# 4. Report the sim run from the trade ledger:
python tools/full_portfolio_report.py --variant p300_aggressive_v2_v1_0 \
    --capital 10000   # reads /tmp/sim_dash.db if env var still set
```

### Which sim tool — `run.py --mode sim` or `backtest_runner.py`?

Both drive the live bot under a fake clock via the same
[services/sim_loop.run_sim](services/sim_loop.py) primitive — but they
differ in **where output lands** and **which features they layer on
top**:

|  | `run.py --mode sim` | `backtest_runner.py` |
|---|---|---|
| Output ledger | separate `--dash-db` file | live `data/dashboard.db` (variant id suffixed `__replay[_<tag>]`) |
| Live data isolation | **complete** — separate trader.db + dashboard.db | shares `data/trader.db` (read) + `data/dashboard.db` (writes to its own variant) |
| Liquidation simulator | NO (tactical-style closes only) | YES (`check_liquidations_for_variant`) |
| Mark-to-end-of-window for trades open at end | NO | YES (`mark_remaining_at_end`) |
| Per-sleeve PnL summary | uses `strategy_health.build_report` | bespoke report block |
| `--reset` purges prior runs | NO (use a fresh `--dash-db`) | YES |
| `--tag` for parallel A/B runs | NO | YES |
| `--with-fomc` injects FOMC sleeve mid-run | NO | YES |
| `--skip <strategy>` excludes one sleeve | NO | YES |

**Pick `run.py --mode sim`** when you want a clean *operator-style*
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
sqlite3 data/dashboard.db "SELECT id, asset, strategy, direction, entry_price, size_usdt FROM trades WHERE strategy_variant='p300_aggressive_v2_v1_0' AND status='open'"

# Closed trades (newest first)
sqlite3 data/dashboard.db "SELECT id, asset, strategy, direction, pnl_pct, actual_exit_time FROM trades WHERE strategy_variant='p300_aggressive_v2_v1_0' AND status='closed' ORDER BY actual_exit_time DESC LIMIT 20"
```

## Architecture

```
p300/
├── run.py                         # main loop (foreground)
├── bootstrap.py                   # one-shot data/trader.db builder (no upstream deps)
├── binance_feed.py                # keeps Binance tables fresh + on-demand backfills
├── fetch_events.py                # rebuilds scheduled_events (FOMC/CPI/NFP/OPEX)
├── fetch_coinalyze.py             # fetches ca_long_short_ratio history (Coinalyze)
├── register_p300.py               # registers the variant in dashboard.db
├── regime_classifier.py           # BTC regime gate (used by S-096 V3/V4)
├── health.py                      # 8 invariant checks for live operation
├── backtest_runner.py             # clock-driven hourly replay over a date window
├── tools/
│   ├── backtest_report.py          # per-variant deep metrics report (trade ledger)
│   ├── bitstamp_adx_backtest.py    # Pine S-003 ADX validator (vs TV BITSTAMP)
│   ├── bitstamp_thu_bear_backtest.py # Pine S-096 THU_BEAR validator
│   ├── compare_with_fomc.py        # A/B comparison helper
│   ├── fomc_backtest_drilldown.py  # FOMC trade-by-trade attribution
│   ├── fomc_leverage_sensitivity.py# FOMC leverage sweep
│   ├── full_portfolio_report.py    # equity / Sharpe / CAGR + buy-and-hold compare
│   ├── p300_run.ps1                # PowerShell launcher script
│   ├── tools_statistical_validation.py # bootstrap Sharpe CI + rolling/year breakdown
│   ├── build_sim_trader_db.py      # date-slice trader.db → sim trader.db
│   └── ai_quant_archive_rebuild.py # rebuild data/ai_quant_archive/ .md files from DB
├── requirements.txt               # numpy only
├── data/
│   ├── trader.db                  # market data (built by bootstrap; live-refreshed)
│   ├── dashboard.db               # variant registry + trade log
│   └── known_unfillable.json      # gaps known to be unfillable (exchange downtime etc.)
├── jplus/                         # Core J+ sizing inputs + analytic backtester
│   ├── data.py                    # clock-bounded loaders
│   ├── regime.py                  # 4-state classifier + LS circuit breaker
│   ├── r4.py                      # R4 BTC + R4 ETH intraday windows (offline)
│   ├── ema_sleeve.py              # weekly EMA(5/21) crossover for BTC anchor
│   ├── voltarget.py               # vol-target leverage with per-regime caps
│   ├── gate.py                    # rule-based 30d-vol percentile gate (T-1)
│   └── simulate.py                # today_inputs() (live sizing) + simulate() (research)
├── services/
│   ├── variant_engine.py          # scheduler tick + dispatch
│   ├── variant_registry.py        # variant CRUD + schema
│   ├── clock.py                   # injectable clock for deterministic replay
│   ├── db.py                      # DB path constants (TRADER_DB, DASHBOARD_DB)
│   ├── sleeves.py                 # sleeve name constants + dispatch table
│   ├── trades.py                  # trade open/close persistence
│   ├── trade_db.py                # trade log schema + runtime config
│   ├── price_feed.py              # last-close reader with staleness guard
│   ├── funding.py                 # funding accrual + daily sums/means (BTC + ETH)
│   ├── indicators.py              # pure EMA + ADX math (no I/O); used by services + validators + jplus
│   ├── risk_caps.py               # cross-sleeve BTC-long cap enforcement
│   ├── risk_config.py             # SL semantic (price-move vs margin-loss)
│   ├── adx_service.py             # S-003 ADX live dispatcher
│   ├── adx_service.pine           # Pine Script reference for S-003 ADX
│   ├── carry_service.py           # S-078 carry live dispatcher
│   ├── thu_bear_service.py        # S-096 V3/V4 Thu bear live dispatcher
│   ├── thu_bear_service.pine      # Pine Script reference for S-096 Thu Bear
│   ├── pdo_retouch_service.py     # PDO-L-RF live dispatcher
│   ├── pdo_retouch_service.pine   # Pine Script reference for PDO
│   ├── cpr_service.py             # CPR live dispatcher
│   ├── fomc_service.py            # FOMC live dispatcher (regime + sentiment filtered)
│   ├── fed_funds_service.py       # Fed rate-cycle phase classifier
│   ├── sentiment_index_service.py # Fear & Greed index bucketing
│   ├── polymarket_service.py      # Polymarket-implied rate expectations
│   ├── jplus_live.py              # Core J+ sub-sleeve live dispatchers (R4/EMA/ETH_DAILY)
│   ├── ai_quant_service.py        # AI_QUANT live dispatcher (gates + reconciliation)
│   ├── ai_quant/                  # AI_QUANT internals: context, chart, prompt,
│   │                              #   decision loop, journal, archive (.md mirror)
│   ├── strategy_health.py         # realized-PnL aggregation (trades_daily_returns)
│   └── sim_loop.py                # deterministic clock-advance loop primitive
└── tests/                         # 172 tests including look-ahead canary
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
  `python tools/backtest_report.py --variant <id>` for replay metrics.
- **No prior-backtest equity seed.** Any daily-returns panel present in older
  versions of this repo has been removed as compromised. Equity attribution
  begins from the first clean replay or live SHADOW fill.
- **`ca_long_short_ratio` history beyond Binance's 30d window** must come from
  Coinalyze. After bootstrap, the rolling 30d refresh from Binance is enough
  to keep things current; the ~5y of pre-bootstrap history relies on the
  initial Coinalyze fetch.

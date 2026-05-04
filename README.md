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
> bootstrap 95% CI of [0.96, 2.44] (`python tools_statistical_validation.py`).
> Live SHADOW accumulation is the only real OOS validation path.

## What runs live here

7 sleeves dispatched per-minute via `services/variant_engine.py`. Weights
are pre-leverage fractions of total capital:

- **50% JPLUS-CORE** — Core J+ daily-return anchor (EMA weekly cross + R4 BTC
  + R4 ETH, vol-target leverage, rule-based vol-percentile gate). Computed
  daily, persisted to `variant_daily_returns`.
- **15% S-003 ADX** — BTC regime-flip long/short at k=5x
- **8% S-078 Carry** — BTC delta-neutral funding harvest at k=5x
- **6% S-096 V4 Thu Bear** — BTC+ETH Thursday short, CPI/NFP-adjacent, at k=5x
- **11% PDO-L-RF** — gap-open retouch long (BTC+ETH) at k=1x
- **5% CPR** — contrarian positioning reversal (BTC+ETH) at k=1x
- **5% FOMC** — BTC long T-10h to T+0.5h on FOMC days, at k=10x. Filtered by
  rate-environment phase (skip mid-cycle holds + expected -25bp cuts), F&G
  bucket (extreme greed → skip; extreme fear unlocks otherwise-marginal
  setups), and Polymarket cuts-2026 implied probability. See
  [services/fomc_service.py](services/fomc_service.py).

The original ML gate was replaced with a deterministic vol-percentile rule
(see [jplus/gate.py](jplus/gate.py)) after we found the upstream gate's
features used same-day data and could not be reproduced without look-ahead.

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
├── health.py                      # 7 invariant checks for live operation
├── backtest_runner.py             # clock-driven hourly replay over a date window
├── combine_replay.py              # combines tactical replay + Core J+ daily returns
├── backtest_report.py             # per-variant deep metrics report
├── tools_statistical_validation.py# bootstrap Sharpe CI + rolling/year breakdown
├── requirements.txt               # numpy only
├── data/
│   ├── trader.db                  # market data (built by bootstrap; live-refreshed)
│   └── dashboard.db               # variant registry + trade log
├── jplus/                         # Core J+ daily-return engine (50% anchor)
│   ├── data.py                    # clock-bounded loaders
│   ├── regime.py                  # 4-state classifier + LS circuit breaker
│   ├── r4.py                      # R4 BTC + R4 ETH intraday windows
│   ├── ema_sleeve.py              # weekly EMA(5/21) crossover for BTC anchor
│   ├── voltarget.py               # vol-target leverage with per-regime caps
│   ├── gate.py                    # rule-based 30d-vol percentile gate (T-1)
│   └── simulate.py                # daily orchestrator → variant_daily_returns
├── services/
│   ├── variant_engine.py          # scheduler tick + dispatch
│   ├── variant_registry.py        # variant CRUD + schema
│   ├── clock.py                   # injectable clock for deterministic replay
│   ├── trade_db.py                # trade log schema + runtime config
│   ├── price_feed.py              # last-close reader with staleness guard
│   ├── funding.py                 # funding accrual + daily sums/means (BTC + ETH)
│   ├── risk_caps.py               # cross-sleeve BTC-long cap enforcement
│   ├── risk_config.py             # SL semantic (price-move vs margin-loss)
│   ├── adx_service.py             # S-003 ADX live dispatcher
│   ├── carry_service.py           # S-078 carry live dispatcher
│   ├── thu_bear_service.py        # S-096 V3/V4 Thu bear live dispatcher
│   ├── pdo_retouch_service.py     # PDO-L-RF live dispatcher
│   ├── cpr_service.py             # CPR live dispatcher
│   └── jplus_service.py           # JPLUS-CORE daily-return dispatcher
└── tests/                         # 73 tests including look-ahead canary
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
  `python backtest_report.py --variant <id>` for replay metrics.
- **No prior-backtest equity seed.** Any daily-returns panel present in older
  versions of this repo has been removed as compromised. Equity attribution
  begins from the first clean replay or live SHADOW fill.
- **`ca_long_short_ratio` history beyond Binance's 30d window** must come from
  Coinalyze. After bootstrap, the rolling 30d refresh from Binance is enough
  to keep things current; the ~5y of pre-bootstrap history relies on the
  initial Coinalyze fetch.

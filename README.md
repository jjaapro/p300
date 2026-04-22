# p300 — standalone P-300 Aggressive 2.0 1.0 paper-trading bot

Headless extraction of the P-300 strategy from the `trader` research repo. Runs
as a SHADOW variant — it writes phantom trades to a local sqlite DB and never
places orders on any exchange.

## What it runs

P-300 Aggressive 2.0 1.0 is a six-sleeve composition:
- **50% Core J+ MLgate** — seeded from backtest daily returns (not live-traded here)
- **15% S-003 ADX** — BTC regime-flip long/short at k=5x
- **8% S-078 Carry** — BTC delta-neutral funding harvest at k=5x
- **6% S-096 V4 Thu Bear** — BTC+ETH Thursday short, CPI/NFP-adjacent, at k=5x
- **11% PDO-L-RF** — gap-open retouch long (BTC+ETH) at k=1x
- **5% CPR** — contrarian positioning reversal (BTC+ETH) at k=1x
- 5% cash reserve (implicit; composition sums to 95%)

Backtest: Sh 7.21, CAGR +211.8%, MDD 18.8%. Honest-live expectation (0.55-0.65
discount): Sh 4.0-5.0, CAGR 120-180%, MDD 25-30%.

## Bootstrap (one time)

```bash
# 1. Install the one Python dep
pip install -r requirements.txt

# 2. Seed market data tables from the upstream trader repo
python seed_data.py

# 3. Register the P-300 variant in data/dashboard.db + seed daily returns
python register_p300.py
```

If the upstream `trader` repo isn't on the default path, pass `--source`:

```bash
python seed_data.py --source /path/to/trader.db
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
python binance_feed.py           # loop every 60s
python binance_feed.py --once    # fetch latest and exit
```

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
├── binance_feed.py                # keeps 4 Binance tables fresh via public REST
├── seed_data.py                   # one-shot pull of 7 tables from upstream trader.db
├── register_p300.py               # registers variant + seeds backtest daily returns
├── regime_classifier.py           # BTC regime gate (used by S-096 V3/V4)
├── requirements.txt               # numpy only
├── .env.example                   # MEXC keys (unused in shadow mode)
├── data/
│   ├── trader.db                  # market data (seeded; binance_feed keeps fresh)
│   ├── dashboard.db               # variant registry + trade log
│   ├── p300_registry.csv          # sleeve metadata (for register_p300)
│   ├── p300_daily_pnl.csv         # backtest daily panel (for register_p300)
│   └── jplus_static25x_mlgate022_daily.csv  # core anchor daily returns (for register_p300)
└── services/
    ├── variant_engine.py          # scheduler tick + dispatch
    ├── variant_registry.py        # variant CRUD + schema
    ├── trade_db.py                # trade log schema + runtime config
    ├── price_feed.py              # last-close reader (BTC/ETH)
    ├── adx_service.py             # S-003 ADX live dispatcher
    ├── carry_service.py           # S-078 carry live dispatcher
    ├── thu_bear_service.py        # S-096 V3/V4 Thu bear live dispatcher
    ├── pdo_retouch_service.py     # PDO-L-RF live dispatcher
    └── cpr_service.py             # CPR live dispatcher
```

## Data the services need

| Table | Source | Refresh | Used by |
|-------|--------|---------|---------|
| `btc_1m`, `eth_1m` | Binance spot klines | `binance_feed.py` every 60s | pdo, cpr, price_feed (ETH) |
| `cd_futures_ohlcv` | Binance BTCUSDT perp 1h | `binance_feed.py` every 60s | adx, carry, regime_classifier, price_feed (BTC) |
| `cd_funding_rate` | Binance perp funding | `binance_feed.py` every 60s | carry, cpr |
| `cd_spot_binance` | CoinDesk Binance spot 1h | seeded; re-run `seed_data.py` periodically | carry |
| `ca_long_short_ratio` | Coinalyze (or Binance `/futures/data/globalLongShortAccountRatio`) | seeded; re-run `seed_data.py` periodically | cpr |
| `scheduled_events` | static CPI/NFP/OPEX calendar | seeded; re-populate when calendar extends | S-096 V4 filter |

If `ca_long_short_ratio` or `scheduled_events` go stale, CPR may return
`pctile_window_too_thin` and S-096 V4 may skip Thursdays — neither breaks
the bot, they just produce `no_action`.

## Caveats / what this extraction does NOT do

- **No live order placement.** Extending to real MEXC execution would require
  wiring through an `execution_service` that the stripped `variant_engine`
  doesn't have — this bot only writes phantom trades.
- **No dashboard.** Introspection is via sqlite queries (examples above).
- **Core 50% anchor is seeded, not live.** The backtest daily-return panel
  (`jplus_static25x_mlgate022_daily.csv`) only runs to 2026-04-13. After that
  date, the core contributes 0 to equity — re-seed if you want it to keep
  compounding.
- **`ca_long_short_ratio` is not auto-refreshed** from Binance. Re-run
  `seed_data.py` from a fresh upstream trader.db when CPR starts returning
  `pctile_window_too_thin`.

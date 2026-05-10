# P-300 Operations Runbook

> **Read [README.md](README.md) first** for strategy description and
> architecture. This file is for operators running the bot.

## TL;DR

| What | Command |
|---|---|
| One-time setup | `export COINALYZE_API_KEY=...` → `python bootstrap.py` → `python register_p300.py` |
| Start live loop | `python run.py --feed` |
| Single tick (test) | `python run.py --once` |
| Health check | `python health.py` |
| Run replay (research) | `python backtest_runner.py --start 2021-07-01 --end 2026-04-15 --reset --tag YOUR_TAG` |
| Run sim mode (operator) | `python run.py --mode sim --start <iso> --end <iso> --trader-db <sim.db> --dash-db <sim_dash.db>` |
| Build sim trader.db | `python tools/build_sim_trader_db.py --start <iso> --end <iso> --output <sim.db>` |
| Deep metrics report | `python tools/backtest_report.py --variant <variant_id>` |
| Full-portfolio report | `python tools/full_portfolio_report.py --variant <variant_id>` |
| Unit + integration tests | `python -m pytest tests/` |

See [README.md §"Run in sim mode"](README.md) for the full sim-mode
recipe and the "which sim tool" decision matrix.

---

## 1. First-time bootstrap

```bash
pip install -r requirements.txt

# 1. Get a free Coinalyze API key (https://coinalyze.net/) and export it.
#    Needed only for the initial LSR history fetch (~5 years from 2021-01-01).
#    After that, binance_feed keeps the table fresh on its own.
export COINALYZE_API_KEY=...

# 2. Build data/trader.db from scratch:
#    - rebuild scheduled_events from fetch_events.py (calendar)
#    - fetch ca_long_short_ratio history from Coinalyze
#    - backfill funding rate (BTC + ETH) from Binance
#    - backfill klines (1m + 1h) from Binance — slow, ~30-60 min for 5 years
python bootstrap.py
# Faster variants:
#   python bootstrap.py --skip-klines      # skip the slow part
#   python bootstrap.py --skip-coinalyze   # if you don't have a key yet
#   python bootstrap.py --since 2024-01-01 # shorter history

# 3. Register the variant in dashboard.db
python register_p300.py

# 4. Sanity
python health.py
python run.py --once          # single tick; should complete in <30s
python -m pytest tests/ -q    # ~518 tests should pass (some slow sim
                              # tests run end-to-end against data/trader.db
                              # — they skip if the DB is missing)
```

## 2. Live operation

```bash
# Start the main loop with data feed in the same process
python run.py --feed

# Or split into two processes
python binance_feed.py &
python run.py
```

`binance_feed.py` runs a **gap-detection pass at startup** that scans every
cadence-based table (klines + funding) for missing rows and fetches them
from Binance. The first startup after a sparse bootstrap can take ~20 min
(e.g. filling 3M missing minutes for `btc_1m`); every subsequent startup
is sub-second because there's nothing to fix. Pass `--skip-gap-fix` to
opt out for a fast restart.

The bot ticks every 60s. On each tick:

- `variant_engine.tick()` closes due phantom trades, then dispatches each
  sleeve's `try_fire_for_variant()`.
- All sleeves open / close phantom trades in the `trades` table, tagged
  with `strategy_variant='p300_aggressive_v2_v1_0'` and
  `execution_mode='SHADOW'`. Realized PnL is the sum of the trade
  ledger; there is no parallel theoretical-PnL track.
- 6 tactical sleeves: S-003 ADX, S-078 Carry, S-096 V4 Thu Bear,
  PDO-L-RF, CPR, S-103 FOMC.
- 6 Core J+ sub-sleeves (live since the 2026-05-10 refactor):
  JPLUS_R4_BTC (Mon wk1-2 06→18 UTC), JPLUS_R4_ETH (Tue→Wed wk1-2 20→20),
  JPLUS_R4_BTC_V2 / JPLUS_R4_ETH_V2 (Wed+Fri wk1-2 04→14 UTC),
  JPLUS_EMA_BTC (continuous, weekly EMA cross), JPLUS_ETH_DAILY
  (continuous in bull regimes only). Sized per-tick from
  `jplus.simulate.today_inputs()`.
- AI_QUANT (additive 2%, default-OFF via `AI_QUANT_ENABLED` env) — daily
  Anthropic Opus 4.7 decision at 00:05–00:15 UTC.
- FOMC sleeve fires only on FOMC days (8/yr) and writes a decision-row
  audit trail to `fomc_observer` regardless of trade decision.
- A broken sleeve logs the exception but does **not** kill the loop
  (per-sleeve try/except in [services/variant_engine.py](services/variant_engine.py)).

## 3. Observing state

**Variant metadata:**
```bash
python -c "from services import variant_registry as r; \
  v = r.get_variant('p300_aggressive_v2_v1_0'); \
  print(v['short_name'], v['status'], v['enabled'])"
```

**Open phantom trades:**
```bash
sqlite3 data/dashboard.db "
  SELECT id, asset, strategy, direction, entry_price, size_usdt, actual_entry_time
  FROM trades
  WHERE strategy_variant='p300_aggressive_v2_v1_0' AND status='open'
  ORDER BY actual_entry_time DESC"
```

**Recent closed trades (last 20):**
```bash
sqlite3 data/dashboard.db "
  SELECT id, strategy, asset, direction, pnl_pct, actual_exit_time
  FROM trades
  WHERE strategy_variant='p300_aggressive_v2_v1_0' AND status='closed'
  ORDER BY actual_exit_time DESC LIMIT 20"
```

**Daily realized PnL (last 14 days):**
```bash
sqlite3 data/dashboard.db "
  SELECT date(actual_exit_time) AS d, ROUND(SUM(pnl_usdt), 2) AS pnl,
         COUNT(*) AS n_closed
  FROM trades
  WHERE strategy_variant='p300_aggressive_v2_v1_0' AND status='closed'
    AND date(actual_exit_time) >= date('now', '-14 days')
  GROUP BY d ORDER BY d DESC"
```

(The simulator-driven daily-return accrual that previously wrote to
`variant_daily_returns.source='live_computed'` was removed in the
2026-05-10 live/sim refactor. The trade ledger is the canonical source
of realized PnL — see
`services/strategy_health.trades_daily_returns()` for the
programmatic version of the query above.)

## 4. Troubleshooting

### `health.py` reports stale tables
Run `python binance_feed.py --once` to refresh klines + funding. If
`ca_long_short_ratio` shows a gap more than 30 days old, Binance's rolling
30d window can't reach back that far — run `python fetch_coinalyze.py`
(needs `COINALYZE_API_KEY`) to fill the gap from 2021-01-01 onward.

### `health.py` reports multi-open invariant violated
Something is writing multiple open trades per (variant, sleeve, asset).
This invariant was added in Phase 2; if violated, there's a bug. Query
the offenders:
```sql
SELECT strategy_variant, strategy, asset, COUNT(*)
FROM trades WHERE status='open'
GROUP BY strategy_variant, strategy, asset
HAVING COUNT(*) > 1;
```
Investigate the specific sleeve, then close the duplicates (manually
via `UPDATE trades SET status='closed'` — they're phantom, no exchange
action needed).

### `today_inputs` returns None / J+ sub-sleeves don't fire
`jplus.simulate.today_inputs()` returns None when there isn't enough
warmup data (regime classifier needs ~50 daily closes; vol-percentile
gate needs 365 days of BTC daily history). On a cold DB or one whose
`btc_1m` / `cd_spot_binance` table is more than 1 day stale, J+
sub-sleeves exit with `status='no_inputs'`. Run
`python binance_feed.py --once` to refresh, then next tick will succeed.

### Tests fail after a code change
```bash
python -m pytest tests/ -v                  # verbose, see which tests fail
python -m pytest tests/test_jplus_lookahead.py -v  # the look-ahead canary
```
The look-ahead tests are the single most important regression guard. If
they start failing, revert the change that caused it.

### Live loop crashes repeatedly
```bash
python run.py --once 2>&1 | tee /tmp/p300_once.log
```
The crash message should point at the offending sleeve or service.
Per-sleeve errors are already isolated — a whole-loop crash means the
variant_engine itself or the variant lookup failed, typically due to a
corrupted dashboard.db variant row. Recovery:
```bash
python register_p300.py    # re-registers idempotently
python health.py           # confirm fresh registration
```

## 5. Backtest workflows

Two ways to drive the live bot under a fake clock — see
[README.md §"Which sim tool"](README.md) for the decision matrix. In
short: `run.py --mode sim` for clean operator-style sims (separate DB
file, no live-DB risk), `backtest_runner.py` for research workflow
(`--tag` for parallel A/B, liquidation simulator, mark-to-end).

### Research replay with `backtest_runner.py`

```bash
python backtest_runner.py --start 2021-07-01 --end 2026-04-15 --reset --tag A
```

`--tag A` suffixes the replay variant id so multiple runs coexist in
the live `data/dashboard.db`. Results live under
`p300_aggressive_v2_v1_0__replay_A`. The replay variant is registered
with `enabled=0` so the live engine never touches it; only
`backtest_runner` ticks it.

After the run, report it:
```bash
python tools/backtest_report.py --variant p300_aggressive_v2_v1_0__replay_A
python tools/full_portfolio_report.py --variant p300_aggressive_v2_v1_0__replay_A
```

Both tools derive equity curves from the trade ledger via
`services.strategy_health.trades_daily_returns` — no
`variant_daily_returns` involvement.

### Operator sim with `run.py --mode sim`

```bash
# Build a slice of trader.db for the desired window
python tools/build_sim_trader_db.py --start 2024-01-01 --end 2024-12-31 \
    --output data/trader_sim_2024.db

# Register the variant in a fresh sim ledger DB
python register_p300.py --dash-db /tmp/sim_dash.db

# Run the bot under a simulated clock — no contact with the live DBs
python run.py --mode sim --start 2024-01-01 --end 2024-12-31 \
    --trader-db data/trader_sim_2024.db --dash-db /tmp/sim_dash.db \
    --sim-tick-seconds 60
```

Sim mode is idempotent per UTC day — a re-run over the same window is
a no-op. To resume an interrupted run, restart with `--start` at or
before the last completed UTC date.

### Switch SL semantic to margin-loss

```bash
P300_STOP_SEMANTICS=margin python backtest_runner.py --start ... --tag B
```

`price_move` (default) gave better results than `margin` in our
2021-07 to 2026-04 replay — but the knob is there for experimentation.

## 6. Variant IDs in `dashboard.db`

```
p300_aggressive_v2_v1_0              LIVE variant (what run.py ticks)
p300_aggressive_v2_v1_0__replay_<X>  research replays from backtest_runner --tag X
                                     (enabled=0; only backtest_runner ticks them)
```

Historical legacy variants from earlier eras (`__core`, `__C`,
`__core_v5`, `__combined_v6`, etc.) may still be present from before
the 2026-05-10 live/sim refactor. They're enabled=0 so the live engine
ignores them; safe to leave or delete as you prefer.

Delete unused replay variants with:
```sql
DELETE FROM variants            WHERE id = '<variant_id>';
DELETE FROM variant_events      WHERE variant_id = '<variant_id>';
DELETE FROM trades              WHERE strategy_variant = '<variant_id>';
-- variant_daily_returns is no longer auto-created on fresh DBs
-- (Phase 7 of the refactor); the DELETE below is harmless if absent:
DELETE FROM variant_daily_returns WHERE variant_id = '<variant_id>';
```

## 7. Critical invariants — if broken, investigate

1. **Single open trade per (variant, sleeve, asset).** Enforced in the
   sleeve services (see `_has_trade_for_day()` helpers and the
   "single-open invariant" inline comments). Violation indicates a
   regression in the sleeve logic.

2. **No look-ahead.** Every DB read goes through `services.clock` so the
   simulated clock can be moved without any module reading future data.
   Verified by `tests/test_jplus_lookahead.py` — bit-identical output at
   different clock positions.

3. **Idempotent registration.** Re-running `register_p300.py` deletes
   and reinserts — never duplicates. Safe to run any time. `--dash-db`
   flag lets you target a sim DB.

4. **Sim/live dispatch parity.** `run.py --mode sim` and
   `backtest_runner.py` produce byte-identical J+ sub-sleeve trades
   for the same window at the same tick cadence. Verified by
   `tests/test_sim_mode.py::test_sim_and_backtest_runner_produce_identical_jplus_trades`.

5. **Sim never hits the network.** When `clock.is_simulated()` is True,
   every external-API refresh function early-returns its no-op value.
   Verified by `tests/test_sim_network_isolation.py`.

6. **Test suite green.** `python -m pytest tests/` = 518 passing
   (some sim tests skip if `data/trader.db` / `data/dashboard.db` are
   absent, e.g. on a fresh CI checkout). If counts drop, don't deploy.

## 8. Contacts / knowledge

Everything we know is in-repo:
- Strategy description and caveats: [README.md](README.md)
- Core J+ port details: [jplus/__init__.py](jplus/__init__.py)
- Variant spec rationale: [register_p300.py](register_p300.py) header
- Look-ahead audit + fix history: [tests/test_jplus_lookahead.py](tests/test_jplus_lookahead.py)
- Backtest results: run `tools/backtest_report.py` against any replay variant

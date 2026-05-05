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
| Run replay | `python backtest_runner.py --start 2021-07-01 --end 2026-04-15 --reset --tag YOUR_TAG` |
| Full-P-300 combined replay | After tactical replay: `python tools/combine_replay.py --tac-variant <tac_id>` |
| Deep metrics report | `python tools/backtest_report.py --variant <variant_id>` |
| Unit + integration tests | `python -m pytest tests/` |

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
python -m pytest tests/ -q    # 172 tests should pass
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
- JPLUS-CORE computes yesterday's daily return via `jplus.simulate()` and
  persists to `variant_daily_returns` (source='live_computed'). Idempotent
  per UTC day.
- The 6 tactical sleeves (S-003, S-078, S-096 V4, PDO-L-RF, CPR, FOMC) open /
  close phantom trades in the `trades` table, tagged with
  `strategy_variant='p300_aggressive_v2_v1_0'` and `execution_mode='SHADOW'`.
- FOMC sleeve fires only on FOMC days (8/yr) and writes a decision-row
  audit trail to `fomc_observer` regardless of trade decision.
- A broken sleeve logs the exception but does **not** kill the loop
  (per-sleeve try/except in [services/variant_engine.py:409](services/variant_engine.py#L409)).

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

**Core J+ daily returns (last 10):**
```bash
sqlite3 data/dashboard.db "
  SELECT date, return_1x_pct, regime
  FROM variant_daily_returns
  WHERE variant_id='p300_aggressive_v2_v1_0' AND source='live_computed'
  ORDER BY date DESC LIMIT 10"
```

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

### `JPLUS-CORE` status = `not_ready`
The simulator refuses to emit a return for the clock date itself (it's
incomplete). If `not_ready` persists, the underlying BTC daily data is
more than 1 day stale. Run `python binance_feed.py --once` to refresh,
then next tick will succeed.

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

### Run the tactical stack over a window

```bash
python backtest_runner.py --start 2021-07-01 --end 2026-04-15 --reset --tag A
```

`--tag A` suffixes the replay variant id so multiple runs coexist in the
DB. Results live under `p300_aggressive_v2_v1_0__replay_A`.

### Run tactical + Core combined

```bash
# 1. Run tactical replay (tag A2 is the canonical "post-all-fixes" baseline)
python backtest_runner.py --start 2021-07-01 --end 2026-04-15 --reset --tag A2

# 2. Combine with Core (computed on the fly from jplus.simulate)
python tools/combine_replay.py --tac-variant p300_aggressive_v2_v1_0__replay_A2 \
    --tag-combined C --start 2021-07-01 --end 2026-04-15

# 3. Deep report
python tools/backtest_report.py --variant p300_aggressive_v2_v1_0__C
```

### Switch SL semantic to margin-loss

```bash
P300_STOP_SEMANTICS=margin python backtest_runner.py --start ... --tag B
```

`price_move` (default) gave better results than `margin` in our 2021-07
to 2026-04 replay — but the knob is there for experimentation.

## 6. Variant IDs registered in `dashboard.db`

```
p300_aggressive_v2_v1_0              LIVE variant (what run.py ticks)
p300_aggressive_v2_v1_0__replay_A    replay, price-move SL, ETH funding=0
p300_aggressive_v2_v1_0__replay_A2   replay, price-move SL, ETH funding correct ← tactical baseline
p300_aggressive_v2_v1_0__replay_B    replay, margin-loss SL, ETH funding=0
p300_aggressive_v2_v1_0__core        Core J+ standalone (50%, not weighted)
p300_aggressive_v2_v1_0__C           Full P-300 combined (50% Core + 45% Tactical + 5% cash)
```

Delete unused replay variants with:
```sql
DELETE FROM variants WHERE id = '<variant_id>';
DELETE FROM variant_daily_returns WHERE variant_id = '<variant_id>';
DELETE FROM variant_events WHERE variant_id = '<variant_id>';
DELETE FROM trades WHERE strategy_variant = '<variant_id>';
```

## 7. Critical invariants — if broken, investigate

1. **Single open trade per (variant, sleeve, asset).** Enforced in the
   sleeve services (see `_get_open_*_trades()` helpers and the
   "single-open invariant" inline comments). Violation indicates a
   regression in the tactical sleeve logic.

2. **No look-ahead.** Every DB read goes through `services.clock` so the
   simulated clock can be moved without any module reading future data.
   Verified by `tests/test_jplus_lookahead.py` — bit-identical output at
   different clock positions.

3. **Idempotent registration.** Re-running `register_p300.py` deletes
   and reinserts — never duplicates. Safe to run any time.

4. **Daily cadence for JPLUS-CORE.** The Core writes ONE daily-return
   row per UTC day per variant, checked at ingest. Multiple rows for
   the same day mean `_already_computed_today` is broken.

5. **Test suite green.** `python -m pytest tests/` = 172 passing. If this
   drops, don't deploy.

## 8. Contacts / knowledge

Everything we know is in-repo:
- Strategy description and caveats: [README.md](README.md)
- Core J+ port details: [jplus/__init__.py](jplus/__init__.py)
- Variant spec rationale: [register_p300.py](register_p300.py) header
- Look-ahead audit + fix history: [tests/test_jplus_lookahead.py](tests/test_jplus_lookahead.py)
- Backtest results: run `tools/backtest_report.py` against any replay variant

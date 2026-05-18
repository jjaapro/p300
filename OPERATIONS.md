# P-300 Operations Runbook

> **Read [README.md](README.md) first** for strategy description and
> architecture. This file is for operators running the bot.

## TL;DR

| What | Command |
|---|---|
| One-time setup | `export COINALYZE_API_KEY=...` → `python bootstrap.py` |
| Start live loop | `python bot.py` |
| Single tick (test) | `python bot.py --once` |
| Health check | `python health.py` |
| Run replay (research) | `python backtest_runner.py --start 2021-07-01 --end 2026-04-15 --reset --tag YOUR_TAG` |
| Run sim mode (operator) | `python studies/simulation/sim.py --start <iso> --end <iso> --trader-db <sim.db> --dash-db <sim_dash.db>` |
| Build sim trader.db | `python studies/simulation/build_sim_trader_db.py --start <iso> --end <iso> --output <sim.db>` |
| Deep metrics report | `studies/notebooks/backtest_report.ipynb` |
| Full-portfolio report | `studies/notebooks/full_portfolio_report.ipynb` |
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

# 2. Build data/databases/prod.db from scratch:
#    - rebuild scheduled_events from fetch_events.py (calendar)
#    - fetch ca_long_short_ratio history from Coinalyze
#    - backfill funding rate (BTC + ETH) from Binance
#    - backfill klines (1m + 1h) from Binance — slow, ~30-60 min for 5 years
python bootstrap.py
# Faster variants:
#   python bootstrap.py --skip-klines      # skip the slow part
#   python bootstrap.py --skip-coinalyze   # if you don't have a key yet
#   python bootstrap.py --since 2024-01-01 # shorter history

# 3. Sanity (P-300 variant auto-registers on first bot.py run)
python health.py
python bot.py --once          # single tick; should complete in <30s
python -m pytest tests/ -q    # ~490 tests should pass (some slow sim
                              # tests run end-to-end against data/databases/prod.db
                              # — they skip if the DB is missing)
```

## 2. Live operation

```bash
# Start the main loop with data feed in the same process (default)
python bot.py
```

Console noise from idle/heartbeat lines (no_signal, tick ok, [feed]…)
is filtered out by default. Pass `--verbose` to disable the filter
when you're debugging a sleeve.

`binance_feed.py` runs a **gap-detection pass at startup** that scans every
cadence-based table (klines + funding) for missing rows and fetches them
from Binance. The first startup after a sparse bootstrap can take ~20 min
(e.g. filling 3M missing minutes for `btc_1m`); every subsequent startup
is sub-second because there's nothing to fix. Pass `--skip-gap-fix` to
opt out for a fast restart.

The bot ticks every 60s. On each tick:

- `orchestrator.tick()` runs the liquidation sweep, closes paper trades
  whose scheduled exit_time has passed, then dispatches each sleeve's
  `try_fire_for_variant()`.
- Each dispatch carries `sleeve_cfg["_effective_*"]` fields the
  orchestrator computed once per tick: regime-aware allocation
  (P2.4a), sleeve gate decision (P2.4b), portfolio vol scalar
  (P2.4c), and remaining margin headroom (P2.4d). Every sleeve also
  re-queries `margin_headroom.can_open` + `conflict_resolver.detect_opposing_open`
  at trade-open so the second sleeve to fire in a tick sees the first's
  just-opened position.
- All sleeves open / close paper trades in the `trades` table, tagged
  `strategy_variant='p300_aggressive_v2_v1_0'`, `execution_mode='paper'`.
  Realized PnL is the trade-ledger sum; no parallel theoretical track.
- 7 top-level sleeves dispatch every tick; one of them (TIMING_ANOMALIES)
  fans out to 8 calendar/clock substrategies internally (FOMC, THU_BEAR,
  PDO_L_RF, CPR, R4_BTC/ETH/V2). 14 distinct signal paths total — see
  [README.md](README.md) for the full table. FOMC writes a decision-row
  audit trail to `fomc_observer` every FOMC day regardless of whether
  it trades.
- AI_QUANT (default-OFF via `AI_QUANT_ENABLED` env) — at most one
  Anthropic decision per UTC day, 00:05–00:15 UTC window.
- A broken sleeve logs the exception but does **not** kill the loop
  (per-sleeve try/except in [strategies/orchestrator.py](strategies/orchestrator.py)).

## 3. Observing state

**Variant metadata:**
```bash
python -c "from strategies.support import variant_registry as r; \
  v = r.get_variant('p300_aggressive_v2_v1_0'); \
  print(v['short_name'], v['status'], v['enabled'])"
```

**Cross-sleeve coordination snapshot** (gross / cap / headroom,
directional conflicts, concordant stacks):
```bash
python -c "from strategies.support.strategy_health import build_report, format_report; \
  print(format_report(build_report('p300_aggressive_v2_v1_0')))"
```
Same content appears in the bot's startup banner.

**Open phantom trades:**
```bash
sqlite3 data/databases/prod.db "
  SELECT id, asset, strategy, direction, entry_price, size_usdt, actual_entry_time
  FROM trades
  WHERE strategy_variant='p300_aggressive_v2_v1_0' AND status='open'
  ORDER BY actual_entry_time DESC"
```

**Recent closed trades (last 20):**
```bash
sqlite3 data/databases/prod.db "
  SELECT id, strategy, asset, direction, pnl_pct, actual_exit_time
  FROM trades
  WHERE strategy_variant='p300_aggressive_v2_v1_0' AND status='closed'
  ORDER BY actual_exit_time DESC LIMIT 20"
```

**Daily realized PnL (last 14 days):**
```bash
sqlite3 data/databases/prod.db "
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
`strategies.support.strategy_health.trades_daily_returns()` for the
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
`strategies.support.jplus_inputs.today_inputs()` returns None when
there isn't enough warmup data (regime classifier needs ~50 daily
closes; vol-percentile gate needs 365 days of BTC daily history). On a
cold DB or one whose `btc_1m` / `cd_spot_binance` table is more than 1
day stale, J+ sub-sleeves exit with `status='no_inputs'`. Run
`python binance_feed.py --once` to refresh, then next tick will succeed.

### Sleeves report `margin_constrained` / `directional_conflict`
Expected behaviour from the P2.4 coordination layer — see the
cross-sleeve snapshot above for the variant's current gross / cap /
open conflicts. `margin_constrained` fires when the variant's open
notional + the candidate notional would exceed
`gross_notional_target_x × capital`. `directional_conflict` fires when
an earlier-dispatched sleeve already has an opposing-direction perp
open on the same asset. Both are safety rails, not bugs — a
persistent flow of these statuses for one sleeve hints at miscalibrated
allocation or contradictory signals, not a code defect.

### Tests fail after a code change
```bash
python -m pytest tests/ -v                  # verbose, see which tests fail
python -m pytest tests/test_jplus_lookahead.py -v  # the look-ahead canary
```
The look-ahead tests are the single most important regression guard. If
they start failing, revert the change that caused it.

### Live loop crashes repeatedly
```bash
python bot.py --once 2>&1 | tee /tmp/p300_once.log
```
The crash message should point at the offending sleeve or service.
Per-sleeve errors are already isolated — a whole-loop crash means the
orchestrator itself or the variant lookup failed, typically due to a
corrupted prod.db variant row. Recovery:
```bash
sqlite3 data/databases/prod.db "DELETE FROM variants WHERE id='p300_aggressive_v2_v1_0'"
python bot.py --once       # re-registers via strategies.p300_spec.register
python health.py           # confirm fresh registration
```

## 5. Backtest workflows

Two ways to drive the live bot under a fake clock — see
[README.md §"Which sim tool"](README.md) for the decision matrix. In
short: `studies/simulation/sim.py` for clean operator-style sims
(separate DB file, no live-DB risk), `backtest_runner.py` for research
workflow (`--tag` for parallel A/B, mark-to-end). Both modes — and
live — run the liquidation simulator since 2026-05-13.

### Research replay with `backtest_runner.py`

```bash
python backtest_runner.py --start 2021-07-01 --end 2026-04-15 --reset --tag A
```

`--tag A` suffixes the replay variant id so multiple runs coexist in
the live `data/databases/prod.db`. Results live under
`p300_aggressive_v2_v1_0__replay_A`. The replay variant is registered
with `enabled=0` so the live engine never touches it; only
`backtest_runner` ticks it.

After the run, report it via the notebooks under `studies/notebooks/`
(`backtest_report.ipynb`, `full_portfolio_report.ipynb`). Both derive
equity curves from the trade ledger via
`strategies.support.strategy_health.trades_daily_returns` — no
`variant_daily_returns` involvement.

### Operator sim with `studies/simulation/sim.py`

```bash
# Build a slice of trader.db for the desired window
python studies/simulation/build_sim_trader_db.py --start 2024-01-01 --end 2024-12-31 \
    --output data/trader_sim_2024.db

# Run the bot under a simulated clock — no contact with the live DBs.
# The P-300 variant is auto-registered into the sim ledger DB on startup.
python studies/simulation/sim.py --start 2024-01-01 --end 2024-12-31 \
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

## 6. Variant IDs in `prod.db`

```
p300_aggressive_v2_v1_0              LIVE variant (what bot.py ticks)
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

2. **No look-ahead.** Every DB read goes through `strategies.support.clock`
   so the simulated clock can be moved without any module reading future
   data. Verified by `tests/test_jplus_lookahead.py` — bit-identical
   output at different clock positions.

3. **Idempotent registration.** `strategies.p300_spec.register` is a
   no-op when the variant row already exists. Called automatically on
   bot.py / sim.py startup; manual invocation is not needed.

4. **Sim/live dispatch parity.** `studies/simulation/sim.py` and
   `backtest_runner.py` produce byte-identical J+ sub-sleeve trades
   for the same window at the same tick cadence. Verified by
   `tests/test_sim_mode.py::test_sim_and_backtest_runner_produce_identical_jplus_trades`.

5. **Sim never hits the network.** When `clock.is_simulated()` is True,
   every external-API refresh function early-returns its no-op value.
   Verified by `tests/test_sim_network_isolation.py`.

6. **Test suite green.** `python -m pytest tests/` — 720+ passing as of
   2026-05-15 (some sim tests skip if `data/databases/prod.db` is absent, e.g.
   on a fresh CI checkout). If counts drop after a code change,
   don't deploy.

## 8. Contacts / knowledge

Everything we know is in-repo:
- Strategy description and caveats: [README.md](README.md)
- Core J+ port details: [strategies/support/jplus_inputs.py](strategies/support/jplus_inputs.py) (today_inputs) and [studies/jplus_analytic/](studies/jplus_analytic/) (offline simulate)
- Variant spec rationale: [strategies/p300_spec.py](strategies/p300_spec.py) header + build_spec
- Look-ahead audit + fix history: [tests/test_jplus_lookahead.py](tests/test_jplus_lookahead.py)
- Backtest results: run `studies/notebooks/backtest_report.ipynb` against any replay variant

# P-300 Operations Runbook

> **Read [README.md](README.md) first** for strategy description and
> architecture. This file is for operators running the fleet.

> **2026-09-13 — what this runbook now describes.** A bot is a directory:
> `bots/<name>/` holds `runner.py`, `config.py` and `strategy/`, and that is
> the only place its logic lives. The legacy orchestrator path (`bot.py`,
> `strategies/orchestrator.py`, `strategies/p300_spec.py`,
> `backtest_runner.py`, `studies/simulation/sim.py`) was deleted in the
> "bot = directory = strategy" refactor. Procedures that depended on it are
> marked **retired 2026-09-13** with their real modern equivalent, or with a
> plain statement that the capability went with the legacy path. Nothing in
> this file should be a command that fails at 3am.

## TL;DR

| What | Command |
|---|---|
| One-time setup | `export COINALYZE_API_KEY=...` → `python bootstrap.py` |
| Start the fleet (feed + 7 bots + dashboard) | `.\start_fleet.ps1` — see §10 |
| What is running right now | `.\start_fleet.ps1 -Status` |
| Health check | `python health.py` |
| Freshness / heartbeat / silence alerts | scheduled task `\p300\monitor-hourly` (+ `monitor-daily-deep`) once `ops\register_tasks.ps1` is run; ad hoc `python monitor.py` — see §11 |
| Dashboard | `python dashboard/server.py` → http://127.0.0.1:8300 |
| Dry-run one bot on a DB copy | `python bots/<name>/runner.py --once --db <copy.db> --sim-now <iso>` |
| Per-variant metrics report | `python -m strategies.support.strategy_health --variant bot_adx_v1` |
| Daily prod.db backup | scheduled task `\p300\backup-daily` (2 local copies; needs ~6.5 GB free on C:) once `ops\register_tasks.ps1` is run; ad hoc `python backup.py --keep-daily 2 --keep-weekly 0` — see §11 |
| Results warnings (LOSING red / BELOW RESEARCH amber) | dashboard fleet tiles + `monitor.py` warnings tier — display only, the operator decides; see §11 |
| Unit + integration tests | `python -m pytest tests/` |

Replay and sim tooling is gone — see §5.

---

## 1. First-time bootstrap

```bash
pip install -r requirements.txt

# 1. Get a free Coinalyze API key (https://coinalyze.net/) and export it.
#    Needed only for the initial LSR history fetch (~5 years from 2021-01-01).
#    After that, feed.py keeps the table fresh on its own.
export COINALYZE_API_KEY=...

# 2. Build data/databases/prod.db from scratch:
#    - create every table in bootstrap.py's SCHEMAS dict
#    - rebuild scheduled_events from fetch_events.py (calendar)
#    - fetch ca_long_short_ratio history from Coinalyze
#    - backfill funding rate (BTC + ETH) from Binance
#    - backfill klines (1m + 1h) from Binance — slow, ~30-60 min for 5 years
#    - fetch the daily macro/sentiment externals (fear & greed, fed funds,
#      Polymarket)
python bootstrap.py
# Faster variants:
#   python bootstrap.py --skip-klines      # skip the slow part
#   python bootstrap.py --skip-coinalyze   # if you don't have a key yet
#   python bootstrap.py --skip-binance     # calendar + LSR only
#   python bootstrap.py --since 2024-01-01 # shorter history

# 3. Sanity. Each bot registers its own variants row on first start
#    (botlib.ensure_bot_variant), so health.py's registration check is
#    expected to fail until the fleet has been started once.
python health.py                              # data + schema + entry points
python bots/adx/runner.py --once              # one tick of one bot; <30s
python -m pytest tests/ -q                    # 1128 tests as of 2026-09-13
```

`bootstrap.py` prints the same two next steps when it finishes
(`python health.py`, then `.\start_fleet.ps1`).

## 2. Live operation

The fleet is `feed.py` + seven bot runners + the dashboard, each in its own
process, all started by [`start_fleet.ps1`](start_fleet.ps1) (§10):

```powershell
.\start_fleet.ps1              # everything that isn't already up
.\start_fleet.ps1 -Status      # what's running right now
.\start_fleet.ps1 -Units adx,carry
```

The script refuses to start a unit that is already running — that duplicate
guard is the fix for the 2026-08-15..24 incident, when every bot ran doubled
for nine days after a manual double-start and chento double-sized its signals.
On Windows a **healthy** unit is TWO `python.exe` processes with the same
command line (venv shim parent + real interpreter child); the scan collapses
that pair, so never count raw command lines.

> **Retired 2026-09-13:** `python bot.py` — the single-process orchestrator
> loop that ran every sleeve and its own feed thread. It was dormant from
> 2026-06-11 and was deleted with the refactor. `start_fleet.ps1` and
> `dashboard/procscan.py` still *look* for a running `bot.py` and flag it as a
> fault, because a process from an old checkout would double-fetch against
> `feed.py`.

**Verbosity.** Idle tick statuses are logged at DEBUG, so a quiet console is a
working bot. Pass `--verbose` to a runner to log them at INFO when you are
debugging a strategy; `feed.py --verbose` logs per-table fetch counts every
cycle.

**Feed startup heal.** `feed.py` runs a **gap-detection pass at startup** that
scans every cadence-based table (klines + funding) for missing rows and fetches
them from Binance. The first startup after a sparse bootstrap can take ~20 min
(e.g. filling 3M missing minutes for `btc_1m`); every subsequent startup is
sub-second because there's nothing to fix. Pass `--skip-gap-fix` (or
`.\start_fleet.ps1 -SkipGapFix`) to opt out for a fast restart — but **not**
after a long outage: the heal pass is what refills LSR/OI before the ~30d
upstream retention burns them.

Each bot ticks on its own interval (`--interval`, default `botcfg.TICK_SECONDS`).
On each tick a runner:

- checks its `MGMT_TABLES` freshness against `botlib.FRESHNESS_CONTRACTS`, and
  skips the whole tick with `status='stale_mgmt_inputs'` if any is stale — a
  bot that cannot see the market does not manage positions on guesswork;
- calls its strategy's `decide()`, which both evaluates entries **and** sweeps
  the exits it owns (stop path, target, time stop, ADX flip, carry's 30-day
  cumulative exit);
- if `decide()` produced an Intent, re-checks `ENTRY_TABLES`; stale ones block
  the entry only (`status='entry_blocked_stale_inputs'`) while the sweep still
  runs;
- sizes the Intent itself, never the strategy: fixed-R via
  `botlib.size_intent_fixed_r` for `adx`, `chento_v3` (and its ETH wrapper),
  `short_squeeze` and `squeeze_bull`; a flat `CARRY_NOTIONAL_X` for `carry`;
  per-window weight × capped leverage against a gross co-fire budget for `r4`.
  Then it calls `execute()`;
- runs `botlib.close_due_trades()` as the scheduled-exit backstop behind the
  sleeve's own sweep. Since 2026-09-14 the runner hands it each strategy's own
  close function, so a backstop close books the sleeve's cost, slippage and
  funding (CARRY delta-neutral; ADX through its stop resolver, which can move
  the close to an earlier stop or to the due minute). It closes at the tick's
  quote with the label `scheduled_exit`. `r4` has no close of its own: the
  backstop is its exit and books the `trades.py` defaults, 10 bp + 5 bp +
  funding. The backstop still runs on a tick where `decide()` raised: no
  further entries that tick (`r4` keeps a window it opened earlier in the same
  tick), heartbeat `error`, note = the exception. It does not run on a
  `stale_mgmt_inputs` tick (on purpose; heartbeat `degraded`; `r4` checks
  btc_1m and eth_1m together, so a stale btc_1m also holds back an ETH close).
  Since 2026-09-19 it also runs when anything after `decide()` raises (the
  entry-table check, sizing, `execute()`, including `DuplicateInstanceError`
  while standing down; for `r4` also a sleeve that fails to load): status
  `entry_error`, heartbeat `error`, note = the exception, and in the
  two-variant bots the other variant still ticks. A due trade whose strategy has no closer,
  or whose price read or close raises, stays open and is named in the heartbeat
  note with status `error`; the other due trades still close, and the other
  variant still ticks;
- writes its `bot_heartbeats` row (`last_tick_utc`, `last_eval_utc`,
  `last_signal_utc`, `open_trades`, status, note);
- counts the tick in `bot_tick_daily` (one counter per day, bot, variant and
  status — every tick) and, when it is worth a row, appends it to `bot_ticks`
  with the sleeve's decision detail as JSON: the first tick after a start,
  every status change, every non-ok heartbeat, every fire and every backstop
  close; idle ticks repeating the previous status are counted, not stored
  (`botlib.record_tick`, since 2026-09-19 — before that the heartbeat row was
  the only trace of a tick, and nothing counted fires).

All bots open / close paper trades in the `trades` table with
`execution_mode='paper'`, each tagged with its own `strategy_variant`
(§6). Realized PnL is the trade-ledger sum; no parallel theoretical track.

A bot that crashes takes down only itself. Its console stays open with the
traceback (`cmd /k`), `monitor.py` raises `DEAD PROCESS`, and the dashboard
fleet tile goes red — the other six keep trading.

**Duplicate-instance stand-down (2026-09-12).** If a second process writes the
same heartbeat name, `botlib.heartbeat` forces `status='error'` with a
`DUPLICATE INSTANCE` note and calls `instance_guard.stand_down()`. That process
then **refuses new entries** but keeps managing exits — deliberately
asymmetric, see [strategies/support/instance_guard.py](strategies/support/instance_guard.py).
The exception: on a tick where a standing-down process gets a signal, `execute()` raises
`DuplicateInstanceError` before the scheduled-exit backstop runs, so that one tick skips the
backstop (and, in the two-variant bots, the other variant); the sleeve's own sweep inside
`decide()` has already run (BACKLOG 23).
Kill one of the two; the survivor clears the flag on its next heartbeat.

## 3. Observing state

The nine paper variants are listed in §6. Substitute the one you care about.

**Variant metadata:**
```bash
python -c "from strategies.support import variant_registry as r; \
  v = r.get_variant('bot_adx_v1'); \
  print(v['short_name'], v['status'], v['enabled'])"
```

**Per-variant metrics report** (YTD/90D/30D portfolio + per-sleeve stats,
ledger coherence, gross/cap/headroom):
```bash
python -m strategies.support.strategy_health --variant bot_adx_v1
```
Same content appears in each bot's startup banner. The cross-sleeve section is
scoped to one variant = one bot; it is a *report*, not a gate — the
orchestrator-era coordination layer that used to block trades is gone (§4).

The SQL below opens prod.db **read-only** (`?mode=ro`). Use that form while
the fleet is up: the plain path opens read-write and can take a lock.

**Open paper trades:**
```bash
sqlite3 "file:data/databases/prod.db?mode=ro" "
  SELECT id, strategy_variant, asset, strategy, direction, entry_price,
         size_usdt, actual_entry_time
  FROM trades
  WHERE strategy_variant LIKE 'bot_%' AND status='open'
  ORDER BY actual_entry_time DESC"
```

**Recent closed trades (last 20):**
```bash
sqlite3 "file:data/databases/prod.db?mode=ro" "
  SELECT id, strategy_variant, strategy, asset, direction, pnl_pct,
         actual_exit_time
  FROM trades
  WHERE strategy_variant LIKE 'bot_%' AND status='closed'
  ORDER BY actual_exit_time DESC LIMIT 20"
```

**Daily realized PnL (last 14 days):**
```bash
sqlite3 "file:data/databases/prod.db?mode=ro" "
  SELECT date(actual_exit_time) AS d, ROUND(SUM(pnl_usdt), 2) AS pnl,
         COUNT(*) AS n_closed
  FROM trades
  WHERE strategy_variant LIKE 'bot_%' AND status='closed'
    AND date(actual_exit_time) >= date('now', '-14 days')
  GROUP BY d ORDER BY d DESC"
```

**Heartbeats (liveness, last evaluation, open count):**
```bash
sqlite3 "file:data/databases/prod.db?mode=ro" "
  SELECT name, last_tick_utc, last_eval_utc, open_trades, status, note
  FROM bot_heartbeats ORDER BY name"
```

**Fires — the count behind every "revisit at n OOS fires" rule:**
```bash
sqlite3 "file:data/databases/prod.db?mode=ro" "
  SELECT bot, variant, status, SUM(n) AS n
  FROM bot_tick_daily WHERE day >= '2026-09-19'
  GROUP BY bot, variant, status ORDER BY bot, variant, n DESC"
```
A fire is a `decided` tick (`r4`: `opened`); `entry_blocked_stale_inputs` and
`entry_error` are fires that produced no trade. Each fire's gate values are in
`bot_ticks.detail` (`SELECT tick_utc, variant, detail FROM bot_ticks WHERE
status='decided'`). Python: `botlib.tick_summary(since_day="2026-09-19")`.

(The simulator-driven daily-return accrual that previously wrote to
`variant_daily_returns.source='live_computed'` was removed in the
2026-05-10 live/sim refactor. The trade ledger is the canonical source
of realized PnL — see
`strategies.support.strategy_health.trades_daily_returns()` for the
programmatic version of the query above.)

## 4. Troubleshooting

### A bot / the feed is down
```powershell
.\start_fleet.ps1 -Status      # ground-truth process scan
.\start_fleet.ps1              # restarts only what is down
```
The consoles stay open after a process exits, so read the traceback in the
dead unit's window before restarting it.

### `feed.py` exits with code 3
Its single-instance guard saw a `feed` heartbeat less than two intervals old
and refused to start a second fetcher. If the old feed really is dead (confirm
with `-Status`), restart with `.\start_fleet.ps1 -ForceFeed`. The process scan
in the script still refuses to start a second feed regardless of that switch.

### `health.py` reports stale tables
Run `python feed.py --once` to refresh klines + funding. If
`ca_long_short_ratio` shows a gap more than 30 days old, Binance's rolling
30d window can't reach back that far — run `python fetch_coinalyze.py`
(needs `COINALYZE_API_KEY`) to fill the gap from 2021-01-01 onward.

For the one-shot historical backfills, the CLI lives in `data/sources/binance.py`
(there is no `binance_feed.py` file — health.py's fix hints still name the old
script):
```bash
python -m data.sources.binance --backfill-klines --since 2024-01-01
python -m data.sources.binance --backfill-funding --since 2024-01-01
python -m data.sources.binance --backfill-klines-15m
```
Never run `python -m data.sources.binance` **without** a `--backfill-*` flag
while `feed.py` is up: it starts a second fetch loop.

### Interior gaps (holes in the middle, which MAX(ts) cannot see)
```bash
python -m data.check_gaps          # read-only report, safe while the fleet runs
python -m data.check_gaps -v       # every gap, not just the first 5
python monitor.py --deep           # the same scan, as alerts
```

### `health.py` reports multi-open invariant violated
Something is writing multiple open trades per (variant, strategy, asset).
Query the offenders:
```sql
SELECT strategy_variant, strategy, asset, COUNT(*)
FROM trades WHERE status='open'
GROUP BY strategy_variant, strategy, asset
HAVING COUNT(*) > 1;
```
> **Caveat, 2026-09-13:** `health.py::check_single_open_invariant` filters on
> `strategy_variant LIKE 'p300_%'`, i.e. the *legacy* variant only. It does not
> cover the `bot_*` variants the fleet actually trades. Run the SQL above by
> hand (without the LIKE) until that check is repointed.

> **Stacked chento rows are not duplicates (2026-09-14).** `bot_chento_v3_v1`
> and `bot_chento_v3_eth` have no single-open guard: each signal is its own
> trade, spaced by the 6h cooldown and closed by the 72h TIF at the latest (at
> most 12 open per bot), so the SQL above lists them whenever signals overlap.
> Do **not** close those. The real duplicate is two rows with the **same**
> trigger bar for one variant — how the 2026-08-15..24 double-start booked each
> of three chento signals twice. (Closing a trade appends a plain-text `CHENTO_TRIPLE_V3_EXIT: ...` line after the JSON in `notes`, so `json_extract`
> fails on these rows; the query slices the string instead.)
> ```sql
> SELECT strategy_variant,
>        substr(notes, instr(notes, '"bar_ts": "') + 11, 25) AS bar_ts,
>        COUNT(*)
> FROM trades WHERE strategy='CHENTO_TRIPLE_V3' AND status='open'
> GROUP BY strategy_variant, bar_ts
> HAVING COUNT(*) > 1;
> ```

Investigate the specific bot, then close the duplicates — for chento, only the
extra same-bar rows (manually
via `UPDATE trades SET status='closed'` — they're paper, no exchange
action needed).

### `today_inputs` returns None / `r4` doesn't fire
`strategies.support.jplus_inputs.today_inputs()` returns None when there isn't
enough warmup data (regime classifier needs ~50 daily closes; vol-percentile
gate needs 365 days of BTC daily history). `r4` is the only remaining live
consumer — it sizes every window off it — and exits with `status='no_inputs'`
on a cold DB or one whose `btc_1m` / `cd_spot_binance` table is more than 1 day
stale. Run `python feed.py --once` to refresh; the next tick will succeed.

### A bot reports a skip status instead of trading
These are the statuses a runner writes when it deliberately does not act. All
are normal operation, not defects:

| status | meaning |
|---|---|
| `stale_mgmt_inputs` | management tables stale — whole tick skipped, heartbeat `degraded` |
| `entry_blocked_stale_inputs` | signal fired but entry tables stale — sweep still ran, entry discarded |
| `no_inputs` | `today_inputs()` had insufficient warmup (r4) |
| `regime_zero_weight` | the regime weights table gives this window zero weight (r4's bear kill switch) |
| `budget_exhausted` | r4's co-fire budget (`GROSS_MAX_X × capital`) left less than `MIN_NOTIONAL_USDT` |
| `missed_window` | the window opened more than `LATE_ENTRY_MAX_S` ago — no cold fills |

> **Retired 2026-09-13:** `margin_constrained` and `directional_conflict`. Those
> came from the orchestrator's P2.4 coordination layer, which allocated across
> sleeves and vetoed opposing positions inside one variant. Each bot now owns
> one variant and sizes itself, so nothing produces those statuses. The
> `margin_headroom` / `conflict_resolver` / `signal_aggregator` modules survive
> as read-only reporting for `strategy_health` (§3); they gate nothing.

### Tests fail after a code change
```bash
python -m pytest tests/ -v                          # verbose, see what fails
python -m pytest tests/test_jplus_lookahead.py -v   # the look-ahead canary
python -m pytest tests/test_orchestrator_interface_gone.py -v  # archive/legacy guard
python -m pytest tests/test_golden_adx.py -v                   # one bot's goldens
```
The look-ahead tests are the single most important regression guard. If
they start failing, revert the change that caused it. `test_orchestrator_interface_gone.py`
is the standing guard that nothing live re-imports the archived sleeves or
re-grows an orchestrator adapter.

### A bot crashes repeatedly
```bash
python bots/<name>/runner.py --once --verbose
```
That is one tick against the live prod.db. To reproduce **without** touching
the live ledger, dry-run against a copy — this also redirects each sleeve's
diagnostics JSONL, which the dashboard reads:
```bash
# .backup, never a file copy: eight processes write prod.db every 60s and it
# is in WAL, so a byte copy taken without its -wal is not a consistent snapshot.
sqlite3 data/databases/prod.db ".backup data/prod_copy.db"
python bots/<name>/runner.py --once --db data/prod_copy.db --sim-now 2026-09-13T14:00:00Z
```
`--db` refuses the live `prod.db` outright, and `--db` / `--sim-now` are only
valid together with `--once`.

If the crash is a missing variants row, delete it and let the bot re-register
on its next start (`botlib.ensure_bot_variant` is idempotent):
```bash
sqlite3 data/databases/prod.db "DELETE FROM variants WHERE id='bot_adx_v1'"
python bots/adx/runner.py --once     # re-registers
python health.py                     # confirm fresh registration
```

## 5. Backtest and sim workflows — retired 2026-09-13

**The replay/sim capability went with the legacy path.** `backtest_runner.py`,
`studies/simulation/sim.py` and `studies/simulation/build_sim_trader_db.py`
drove `orchestrator.tick()` under a fake clock, and all three were deleted with
the orchestrator. There is no drop-in replacement: nothing today replays the
fleet over a date window. Do not go looking for one.

What exists instead:

- **One tick at a chosen timestamp, against a DB copy** — the dry-run flags on
  every runner (`--once --db <copy.db> --sim-now <iso>`, §4). This is the
  operator-facing "does this bot behave at time T" tool.
- **Golden and parity tests** — `tests/test_golden_*.py` pin each bot's decision
  on a fixture DB; `tests/test_*_parity.py` pin bot output against the research
  implementation. These are what now guarantee a strategy still does what it
  was calibrated to do.
- **Research** — `studies/notebooks/`. `backtest_report.ipynb` and
  `full_portfolio_report.ipynb` report a variant out of the trade ledger
  (`--variant <id>`); both derive equity curves via
  `strategies.support.strategy_health.trades_daily_returns` /
  `strategies.support.equity.marked_daily_returns`. Their headers still
  describe the replay-variant era they were written for.

### `P300_STOP_SEMANTICS` still exists

```bash
P300_STOP_SEMANTICS=margin python bots/adx/runner.py --once
```
`price_move` (default) gave better results than `margin` in the 2021-07 to
2026-04 replay. `adx` is the only bot whose stop it changes — it divides the
configured stop by leverage, and `stop_path` reads the resulting threshold back
off the trade notes on every close check
([strategies/support/risk_config.py](strategies/support/risk_config.py)).
Both semantics are pinned by `tests/test_golden_adx.py`.

## 6. Variant IDs in `prod.db`

Nine live paper variants, one per bot except the two squeeze bots, which each
carry a no-stop twin in the same process (same signals, different exit/sizing):

```
bot_chento_v3_v1              Chento Triple v3, BTC
bot_chento_v3_eth             Chento Triple v3, ETH
bot_short_squeeze_v1          Short Squeeze (stop 10bp under swept low, 3R, 6h)
bot_short_squeeze_nostop_v1   Short Squeeze, no stop, 6h time stop only
bot_adx_v1                    ADX S-003 T2
bot_carry_v1                  Carry S-078 (delta-neutral funding harvest)
bot_squeeze_bull_v1           Squeeze Bull / OI flush (-2% stop, +3%, 48h)
bot_squeeze_bull_nostop_v1    Squeeze Bull, no stop, +3%, 48h
bot_r4_v1                     R4 calendar — ETH windows only since 2026-09-12
```

Each is registered on bot startup by `botlib.ensure_bot_variant` and is that
bot's ledger scope today, its exchange sub-account at go-live. The paired
no-stop twins have re-cut points fixed in advance at n=20 and n=30.

**Legacy rows.** `p300_aggressive_v2_v1_0` was the orchestrator's variant. It
has been dormant since 2026-06-11 and was set `enabled = 0` on 2026-09-13 with
a `variant_events` row; its 27 closed trades are kept deliberately as the
legacy paper series. Older era rows (`__core`, `__C`, `__core_v5`,
`__combined_v6`, …) are also `enabled=0`. Nothing dispatches any of them —
safe to leave or delete as you prefer.

Delete an unused variant with:
```sql
DELETE FROM variants            WHERE id = '<variant_id>';
DELETE FROM variant_events      WHERE variant_id = '<variant_id>';
DELETE FROM trades              WHERE strategy_variant = '<variant_id>';
-- variant_daily_returns is no longer auto-created on fresh DBs
-- (Phase 7 of the refactor); the DELETE below is harmless if absent:
DELETE FROM variant_daily_returns WHERE variant_id = '<variant_id>';
```

Replay variants (`%__replay%`) have their own archive-then-delete tool, which
refuses to run without a fresh backup and is dry-run by default:
```bash
python studies/simulation/archive_replay_variants.py           # report
python studies/simulation/archive_replay_variants.py --apply   # do it
```

## 7. Critical invariants — if broken, investigate

1. **Single open trade per (variant, strategy, asset).** Enforced in each bot's
   strategy module before it emits an Intent — `_get_open_trades()` /
   `_has_trade_for_day()` queries scoped to `(strategy_variant, strategy,
   status='open', execution_mode='paper')`. Violation indicates a regression in
   the strategy logic. See the §4 caveat about health.py's check being scoped
   to the legacy variant prefix.
   Note `r4` is an exception by design: its windows genuinely overlap, so up
   to three concurrent R4 positions are intended — `_has_trade_for_day()` is
   per-window, not per-bot, and the overlap is bounded by `GROSS_MAX_X`, never
   by serialising windows.
   The chento bots (`chento_v3`, `chento_v3_eth`) are the other exception by
   design: they have **no** single-open guard, so positions from separate
   signals stack, bounded only by the 6h entry cooldown and the 72h TIF (at
   most 12 open per bot) — with no aggregate exposure cap. A violation there is
   two rows with the same trigger bar for one variant (§4 query), not two open
   rows.

2. **No look-ahead.** Every DB read goes through `strategies.support.clock`
   so the simulated clock can be moved without any module reading future
   data. Verified by `tests/test_jplus_lookahead.py` — bit-identical
   output at different clock positions, covering `jplus.simulate`, the ADX
   signal, the regime classifier and Carry's funding loader. The four arms that
   covered CPR, PDO, THU_BEAR and FOMC moved with those sleeves to
   `studies/material/archive/tests/test_lookahead.py` on 2026-09-13. Porting
   the contract to the bots that still have none is an open backlog item.

3. **Idempotent registration.** `botlib.ensure_bot_variant` is a no-op when the
   variants row already exists; it only backfills `enabled` when NULL and
   follows a changed `SHORT_NAME`. Called automatically on every runner start;
   manual invocation is not needed.
   *(Was `strategies.p300_spec.register` — deleted 2026-09-13.)*

4. **Bot / research parity.** Each bot's strategy module produces the same
   decisions as the research implementation it was ported from, asserted at
   known timestamps rather than by eye: `tests/test_chento_parity.py`,
   `test_chento_parity_eth.py`, `test_adx_parity.py`,
   `test_short_squeeze_parity.py`, `test_squeeze_bull_parity.py`, plus the
   `tests/test_golden_*.py` fixtures.
   *(Replaces the old sim/backtest_runner dispatch-parity invariant, whose test
   `tests/test_sim_mode.py` was deleted with the sim harness on 2026-09-13.)*

5. **A simulated clock never hits the network.** When `clock.is_simulated()` is
   True, every external-API refresh function early-returns its no-op value.
   This is what makes `--sim-now` dry runs safe. Verified by
   `tests/test_sim_network_isolation.py`.

6. **Nothing live imports the archive.** No module under `bots/`, `strategies/`,
   `dashboard/`, `data/`, nor `botlib.py` / `feed.py` / `monitor.py` /
   `bootstrap.py` / `backup.py` / `health.py` may import
   `studies/material/archive/`. Enforced by
   `tests/test_orchestrator_interface_gone.py::test_no_live_code_imports_the_archive`,
   which parses imports rather than grepping. (The archive's own README names
   this guard `tests/test_archive_is_not_live.py`; that file does not exist —
   the assertion lives in the module above.) This is not hygiene: one of the
   three edges that existed on archive day would have failed *silently* —
   `jplus_inputs` importing the EMA sleeve on the live r4 sizing path, where
   the runner catches ImportError and writes a degraded heartbeat, i.e. a bot
   that reports healthy and evaluates nothing. That sizing code now lives at
   [strategies/support/ema_position.py](strategies/support/ema_position.py).

7. **Test suite green.** `python -m pytest tests/` — 1128 passing as of
   2026-09-13, with nothing skipped and nothing deselected. If counts drop
   after a code change, don't deploy.

## 8. Contacts / knowledge

Everything we know is in-repo:
- Strategy description and caveats: [README.md](README.md)
- Roadmap / current project status: the dated status section at the top of
  [BACKLOG.md](BACKLOG.md)
- Per-bot calibration history (the authoritative change log for any dial):
  [docs/calibration/](docs/calibration/) — `adx.md`, `carry.md`,
  `chento_triple_v3.md` (both chento bots), `r4.md`, `short_squeeze.md`,
  `squeeze_bull.md`
- Per-bot mechanism prose, as rendered on the dashboard:
  [dashboard/cards/](dashboard/cards/)
- What each bot decides (capital, risk, caps, freshness tables):
  `bots/<name>/config.py`; strategy parameters: `bots/<name>/strategy/config.py`;
  how the strategy works, in its own words: `bots/<name>/strategy/README.md`
- Core J+ port details: [strategies/support/jplus_inputs.py](strategies/support/jplus_inputs.py) (today_inputs) and [studies/jplus_analytic/](studies/jplus_analytic/) (offline simulate)
- Look-ahead audit + fix history: [tests/test_jplus_lookahead.py](tests/test_jplus_lookahead.py)
- Archived sleeves and the rules for touching them:
  [studies/material/archive/README.md](studies/material/archive/README.md)

## 9. Dashboard (2026-08-24)

Read-only local web UI over the live fleet:

    python dashboard/server.py            # http://127.0.0.1:8300  (--open launches browser)
    python dashboard/server.py --port 8400

Panels: fleet liveness with ground-truth duplicate detection (psutil scan;
one green instance per unit — note a HEALTHY bot is two python.exe
processes, venv shim + real interpreter with identical command line,
collapsed by dashboard/procscan.py — never count raw cmdline matches),
data-feed freshness vs botlib contracts, a live alert strip mirroring
monitor.py's checks, a positioning strip (daily L/S account ratio vs its
prior year, the decile's historical 20d forward stats, CPR's positioning
conditions, the regime J+ LS circuit breaker), the BTC/ETH trade chart
(entry dots; hover shows planned TP / SL / timed stop; click pins the
levels and the full decision data) with flow panes underneath — perp/spot
CVD + spot−perp divergence, ΔOI% with the CVD×ΔOI quadrant label, funding
+ basis — plus a 4h/24h tape read, short_squeeze's live percentile gauges,
a 24h delta-by-price profile, and per-bot strategy explainers with an
annotated picture of the latest entry. The flow/positioning panels are
descriptive context ("what the bots see"), not signals: the gauges are
computed with the strategies' own SQL and constants and pinned to them by
tests/test_dashboard_market.py (dashboard/market.py docstring).

Strictly read-only: prod.db is opened mode=ro with PRAGMA query_only — the
dashboard cannot write the ledger. Safe to run alongside the fleet; safe
to start twice (the second bind just fails; exit code 2). Run it as the bots'
Windows user so the process scan can read their command lines.
`start_fleet.ps1` starts it last, after the feed and the bots.
Details: dashboard/server.py docstring.
Fleet tiles also carry display-only results badges (red LOSING, amber BELOW RESEARCH;
`strategies/support/evidence.py`) that never replace the liveness state. The alert strip
includes the scheduled jobs' status (MONITOR_*, DEEP_STALE, INTERIOR_GAPS as of the last deep
scan, BACKUP_*, DISK_LOW), and the footer shows the last monitor / deep / backup times (§11).
Info lines (HELD, results notes) no longer turn the banner amber; it reads `· N info`. After
removing a variant from a bot config, restart the dashboard too: it keeps the configs it
imported.
Theme: the header button cycles auto (light 07–19 local time, dark
otherwise) → light → dark; the choice is remembered by the browser, and
`/?theme=light|dark` forces one for a session. Both palettes are validated
separately (dataviz six checks); the entry-context PNG is rendered per
theme (`?theme=` on /api/entry_chart, cached as `SJ-n-light.png`).

## 10. Fleet units and adding a bot (2026-09-06, revised 2026-09-13)

The operated fleet is what `start_fleet.ps1` launches (feed first, dashboard last):

| unit | script | what it is |
|---|---|---|
| feed | feed.py | the only process that fetches; every bot reads prod.db |
| collector | collector.py | public liquidation / depth / Hyperliquid recorder → data/databases/microstructure.db; research only, no bot reads it; not in backup.py. Its tile has no eval limit (tick / instance state only); a run with `--db` or `--no-heartbeat` shows MISSING by design. `-ForceCollector` mirrors `-ForceFeed`. depth_1s buckets are exact only within `bid_reach_pct` / `ask_reach_pct` (the 1000-level snapshot range, ~0.1–0.5 % from mid); beyond it they are lower bounds, and NaN means nothing known there — see data/sources/micro/store.py |
| chento_v3 | bots/chento_v3/runner.py | Chento Triple v3, BTC |
| chento_v3_eth | bots/chento_v3_eth/runner.py | Chento Triple v3, ETH |
| short_squeeze | bots/short_squeeze/runner.py | S-105 sweep + CVD-divergence long; + no-stop twin |
| adx | bots/adx/runner.py | S-003 ADX regime flip |
| carry | bots/carry/runner.py | S-078 delta-neutral funding harvest |
| r4 | bots/r4/runner.py | R4 calendar family; ETH windows only since 2026-09-12 (BTC windows wired but disabled in `bots/r4/config.py`; added 2026-09-06, held 09-09) |
| squeeze_bull | bots/squeeze_bull/runner.py | S-107 OI-flush long; + no-stop twin |
| dashboard | dashboard/server.py | read-only UI on :8300 |
| monitor | monitor.py | hourly checks: normally the `\p300\monitor-hourly` scheduled task (`ops\register_tasks.ps1`); `-Monitor` opens a fallback console that duplicates it (see §11) |

Seven bot units, nine variants. A bot is a **directory**: `bots/<name>/` holds
`runner.py`, `config.py` and `strategy/` (`README.md`, `signal.py`, `config.py`,
and `math.py` where the strategy has feature maths of its own), and that is the
only place its logic lives. `strategies/` holds no strategy — only `trades.py`
(the ledger-write layer) and `support/` (shared live-path modules).

`chento_v3_eth` is the one exception: it has no `strategy/` of its own. Its
runner is a thin wrapper that sets `CHENTO_V3_ASSET=ETH` and the ETH
diagnostics path *before* importing `bots/chento_v3/`, then runs the shared
loop — so a change to the chento strategy changes both bots.

> **Retired 2026-09-13:** `bot.py` and `strategies/orchestrator.py` are deleted,
> not merely "not units". A `bot.py` from an older checkout would double-fetch
> against feed.py, so the fleet script and the dashboard still flag one as a
> fault if they see it.

Adding a bot means touching exactly five places, in one commit:

1. `bots/<name>/{__init__,config,runner}.py` **and `bots/<name>/strategy/`** —
   copy the skeleton of `bots/adx/` (`bots/r4/` for a multi-window bot). Bot
   decisions (capital, risk %, notional cap, tick seconds, freshness tables) go
   in `bots/<name>/config.py`; strategy parameters go in
   `bots/<name>/strategy/config.py`.
2. `start_fleet.ps1` — the `$Units` default array and the `$Fleet` entry.
3. `dashboard/procscan.py` `UNIT_SCRIPTS` — the argv token the process scan matches.
4. `monitor.py` `BOT_EXPECTATIONS[name]` — max seconds between signal evaluations before
   the bot counts as silent (a missing entry means silence is never alerted).
5. `dashboard/botinfo.py` `BOTS[name]` + its `params()` branch, plus
   `docs/calibration/<x>.md` (the filename named in that entry — both chento
   bots share `chento_triple_v3.md`) and `dashboard/cards/<name>.md`
   (`tests/test_dashboard_botinfo.py` fails if either file is missing).

Also register the bot's strategy entry points in `health.py`'s
`BOT_ENTRYPOINTS` and its config module in `BOT_CONFIGS`, or health.py will
police neither.

Every table the new bot reads must already be in `botlib.FRESHNESS_CONTRACTS`; list the
management tables in the bot's `MGMT_TABLES` and the entry-only tables in `ENTRY_TABLES`.

## 11. Monitoring, scheduled jobs and backups (2026-09-14)

Nothing starts the **fleet** at boot (§10). The monitor and the backup run as
Task Scheduler tasks once `ops\register_tasks.ps1` has been run. The script was
written on 2026-09-14 and only dry-run, so check with `Get-ScheduledTask
-TaskPath '\p300\'` before assuming the tasks exist:

| task (`\p300\`) | runs (`venv\Scripts\pythonw.exe`, in the repo) | when | limit | records |
|---|---|---|---|---|
| `monitor-hourly` | `monitor.py --quiet` | every hour at :07 | 10 min | `data\diagnostics\monitor_last.json`, `monitor.log` |
| `monitor-daily-deep` | `monitor.py --deep --quiet` | daily 09:10 local | 20 min | `monitor_last_deep.json` (and `monitor_last.json`), `monitor.log` |
| `backup-daily` | `backup.py --keep-daily 2 --keep-weekly 0` | daily 04:40 local | 120 min | `data\backups\prod-YYYYMMDD.db`, `backup_last.json`, `backup.log` |

All three run as the current user **only while that user is logged on**
(LogonType Interactive: no stored password, no elevation, RunLevel Limited).
They may start and keep running on battery, run once to catch up after a missed
start, and never overlap themselves. `pythonw.exe` opens no window and throws
stdout away, so the status files and the rotating logs (`monitor.log`,
`backup.log`, 1 MB × 5) are the record. Start times are stored as local time,
so they follow DST.

```powershell
pwsh -File ops\register_tasks.ps1 -DryRun      # show what would be registered
pwsh -File ops\register_tasks.ps1              # register (re-runnable) and print a verification table
Start-ScheduledTask -TaskPath '\p300\' -TaskName monitor-hourly       # run one now
Get-ScheduledTaskInfo -TaskPath '\p300\' -TaskName monitor-hourly | Format-List LastRunTime, LastTaskResult, NextRunTime
pwsh -File ops\register_tasks.ps1 -Uninstall   # remove the tasks; files under data\ are kept
```

**Reading `LastTaskResult`:**
- `0`: the monitor is green, or the backup verified.
- `1`: the monitor raised alerts, or the backup's snapshot, check or prune failed. Existing copies are kept.
- `2`: the monitor **crashed**, or the backup refused to start because prod.db is missing or free disk is short. For a monitor crash, read the traceback in `monitor_last.json` → `error` and in `monitor.log`.
- `267009`: running. `267011`: has never run.

**Alerts go to the dashboard only.** The Telegram code in `monitor.py` is kept
but unconfigured (there are no `TELEGRAM_*` keys in `.env`). When those keys
are set it pushes every non-green run; check the wiring with
`python monitor.py --test-alert`. The dashboard reads the three status files on
every 5 s poll:

| code | severity | meaning |
|---|---|---|
| `MONITOR_NEVER_RUN` | amber | no `monitor_last.json`: the tasks were never registered |
| `MONITOR_STALE` | red | the last monitor run is older than 2h15m: the task is disabled or failing, the machine slept, or you were logged off |
| `MONITOR_ERROR` | red | the last monitor or deep run crashed (exit 2), or a status file is unreadable |
| `DEEP_STALE` | amber | no deep gap scan in 30h |
| `INTERIOR_GAPS` | amber | replayed from the last deep scan, marked "(as of HH:MMZ)" |
| `BACKUP_FAILED` | amber | the last backup run did not end `ok` |
| `BACKUP_STALE` | amber over 30h, red over 72h | the newest good copy is too old |
| `DISK_LOW` | amber under 5 GB, red under 2 GB | free space on prod.db's drive, computed live |

The dashboard footer reads `monitor HH:MMZ · deep HH:MMZ · backup HH:MMZ`.
`monitor.py` also raises four alerts of its own:
- `PROD_DB_UNREADABLE`: it opens prod.db read-only and never creates it.
- `DISK_LOW`.
- `BACKUP_STALE`: the first run raises it, because the newest copy is from 2026-07-22.
- `DEEP_SCAN_STALE`, on hourly runs only.

**microstructure.db (the collector's database) is not backed up.** It grows
~170–230 MB/day (~6 GB/month; depth_1s ~75, hl_asset_ctx ~39, hl_positions
40–65, hl_accounts ~9, liquidations 10–40) and lives on the same drive as
prod.db. The collector guards the drive itself: below 5 GB free it stops
recording depth_1s (heartbeat `degraded`, `depth_paused_low_disk` and
`dropped_depth_low_disk` in `data\diagnostics\collector_last.json`) and resumes
above 8 GB; every other table keeps writing. At ~24 GB free on 2026-09-18 that
is roughly three months of runway unless space is freed (the 8.6 GB Claude VM
bundle noted under disk space later in this section) or the database is moved to another drive with
`collector.py --db <path>` (which also turns the heartbeat off — see §10).

A logged-off or sleeping machine gets no monitoring. When you come back, the
dashboard shows `MONITOR_STALE` in red, which is the honest signal.
`.\start_fleet.ps1 -Monitor` still opens an hourly console. Treat it as a
fallback for when the tasks are not registered: while they are, it only
duplicates `monitor-hourly`.

**Results warnings** (`strategies/support/evidence.py`) are **display only**.
They are computed per live variant, meaning the ids in each
`bots/<bot>/config.py`. Each closed trade counts as a % of that config's
`CAPITAL_USDT`:
- **LOSING (red)**: the variant's cumulative net P&L is below zero, at any
  number of trades. Under 5 trades the line says "too few trades to conclude
  anything". It clears once the total is back at or above zero.
- **BELOW RESEARCH (amber)**: needs at least 5 live trades since the variant's
  `comparable_from`. It fires when the live mean is below the 10th percentile
  of 10,000 resampled research means from `bots/<bot>/research_baseline.json`.
  The research is in-sample, so treat it as a prompt to look, not a verdict. A
  research sample under 10 trades gets an info line instead (carry today).
- Rows with the same variant, strategy and direction, entered in the same UTC
  minute, count once. These are the rows the 2026-08 doubled fleet booked twice
  (SJ-4243/44, 4245/46, 4248/49).

The warnings appear in four places:
- a badge on the fleet tile, which never replaces the liveness state;
- rows on the tile with n, net total and the last closes;
- lines in the alert strip;
- `monitor.py`'s `~~` warnings tier, recorded in `monitor_last.json` and never
  part of the exit code.

**Nothing is disabled automatically; the operator decides.** To disable a
variant, remove it from its bot's config (`VARIANTS`) and restart that unit,
then the dashboard unit: the dashboard keeps the bot configs it imported at
start, so until it restarts it still shows the variant and its badge. The
hourly monitor starts fresh each run and drops it on its own.
Setting `variants.enabled = 0` alone does not stop a runner.

**Backups.** How `backup.py` takes a copy:
1. It takes a WAL-safe `VACUUM INTO` snapshot, over a read-only connection, to
   `data/backups/prod-<YYYYMMDD>.db.partial`.
2. It checks the snapshot with `quick_check`, or with `integrity_check` under
   `--verify-full`.
3. Only then does it rename the snapshot to `prod-<YYYYMMDD>.db`.

So a killed or failed run never leaves a truncated copy, and a same-day rerun
replaces the earlier copy only once the new one verifies.

Retention:
- The task keeps **2 local copies** (`--keep-daily 2 --keep-weekly 0`). A
  hand-run `python backup.py` still uses the old defaults of 7 daily + 4
  Sunday copies, which C: cannot hold, so pass the same flags.
- Copies dated **before 2026-09-14 are never auto-pruned**
  (`backup.PRUNE_FROM`). `prod-20260722.db` and any older copy stay until the
  operator deletes them.
- Files not named `prod-YYYYMMDD.db` are never touched.

The script refuses to start with less than 2× prod.db free (about 3.3 GB for
the 1.65 GB prod.db of 2026-09-14), and a copy takes about 1.6 GB. Two task
copies are already on disk when the third day's run starts, so C: needs about
**6.5 GB free before the first run**, on top of the protected
`prod-20260722.db`. With less, the run either ends `low_disk` (BACKUP_FAILED
on the dashboard) or passes the guard and leaves C: below the 2 GB `DISK_LOW`
critical line while the feed is writing. On 2026-09-14 C: had about 3.3 GB
free until two runaway 5 GB Claude task logs were deleted (12.7 GB free after);
the Claude desktop app's VM bundle (~8.6 GB under `%LOCALAPPDATA%\Packages\Claude_*`)
is the largest grower to watch. If there is not room, keep the task off after registering it:
`Disable-ScheduledTask -TaskPath '\p300\' -TaskName backup-daily`, then
`Enable-ScheduledTask` with the same arguments once C: has the room.
Re-running `register_tasks.ps1` registers it enabled again.

Backups matter more than klines suggest: LSR, open-interest and liquidation history beyond the
upstream ~30-day retention is **irreplaceable**. Local retention does not
survive a disk failure, so occasionally copy the newest file off-machine.

**Restoring a backup.** Stop every process that opens prod.db, the dashboard
included. Confirm `prod.db-wal` is absent or 0 bytes, delete `prod.db-wal` and
`prod.db-shm`, then copy the backup over `prod.db`. A stale WAL left beside
the copy would be replayed onto it. Restore from a copy dated 2026-09-14 or
later. `prod-20260722.db` holds 8 duplicate (trade, date, event type)
adjustment groups, all on replay-variant trades. Since the
`uix_adj_trade_date_type` index (BACKLOG 4.5), every bot refuses to start on
that file and lists the groups. If it is the only copy, run
`studies/simulation/archive_replay_variants.py --apply` on it first
(BACKLOG 29).

**Calendar runway.** `scheduled_events` is static and populated years ahead
(currently to 2027-12-31). `monitor.py` alerts when less than 60 days of future
events remain; extend the `FOMC_DECISIONS` / `CPI_DATES` lists in
`fetch_events.py` and re-run it. Note `fetch_events.py` takes no arguments and
**wipes and repopulates** the table from its hardcoded lists:
```bash
python fetch_events.py
```

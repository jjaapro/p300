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
| Freshness / heartbeat / silence alerts | `python monitor.py` — **not scheduled**, see §11 |
| Dashboard | `python dashboard/server.py` → http://127.0.0.1:8300 |
| Dry-run one bot on a DB copy | `python bots/<name>/runner.py --once --db <copy.db> --sim-now <iso>` |
| Per-variant metrics report | `python -m strategies.support.strategy_health --variant bot_adx_v1` |
| Daily prod.db backup | `python backup.py` — **not scheduled**, see §11 |
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
  sleeve's own sweep;
- writes its `bot_heartbeats` row (`last_tick_utc`, `last_eval_utc`,
  `last_signal_utc`, `open_trades`, status, note).

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

Investigate the specific bot, then close the duplicates (manually
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
   Note `r4` is the exception by design: its windows genuinely overlap, so up
   to three concurrent R4 positions are intended — `_has_trade_for_day()` is
   per-window, not per-bot, and the overlap is bounded by `GROSS_MAX_X`, never
   by serialising windows.

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
| chento_v3 | bots/chento_v3/runner.py | Chento Triple v3, BTC |
| chento_v3_eth | bots/chento_v3_eth/runner.py | Chento Triple v3, ETH |
| short_squeeze | bots/short_squeeze/runner.py | S-105 sweep + CVD-divergence long; + no-stop twin |
| adx | bots/adx/runner.py | S-003 ADX regime flip |
| carry | bots/carry/runner.py | S-078 delta-neutral funding harvest |
| r4 | bots/r4/runner.py | R4 calendar family; ETH windows only since 2026-09-12 (BTC windows wired but disabled in `bots/r4/config.py`; added 2026-09-06, held 09-09) |
| squeeze_bull | bots/squeeze_bull/runner.py | S-107 OI-flush long; + no-stop twin |
| dashboard | dashboard/server.py | read-only UI on :8300 |
| monitor | monitor.py | hourly checks (optional unit, `-Monitor`; see §11) |

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

## 11. What is NOT automated (2026-09-13)

There is **no Task Scheduler entry for anything in this repo** on this machine —
verified 2026-09-13. Nothing starts the fleet at boot, and the jobs below run
only when an operator runs them:

| job | command | intended cadence | status |
|---|---|---|---|
| monitor | `python monitor.py` | hourly | **not running, not scheduled** |
| deep gap scan | `python monitor.py --deep` | daily | not scheduled |
| daily backup | `python backup.py` | daily | **not scheduled** |

`monitor.py`'s own docstring says "run it ad hoc or hourly via Task Scheduler"
and `backup.py`'s says "schedule daily via Task Scheduler" — that is the
intent, not the state. Until they are scheduled, the freshness / heartbeat /
silence / retention-burn alerts fire only when you run them, and the
dashboard's alert strip (which recomputes the same checks live, whenever the
page is open) is the only always-on substitute.

The stopgap while that remains true:
```powershell
.\start_fleet.ps1 -Monitor     # opens a console running monitor.py once an hour
```
That console lives only as long as it is open — it is a stopgap, not a schedule.

**Telegram alerts.** `monitor.py` pushes every non-green run to Telegram when
`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` are set in `.env`. `--summary` also
pushes an all-green daily status, so silence itself signals breakage. Verify the
wiring with `python monitor.py --test-alert`. Send failures are logged, never
fatal.

**Backups.** `backup.py` takes a WAL-safe `VACUUM INTO` snapshot to
`data/backups/prod-<YYYYMMDD>.db`, keeps the last 7 daily + last 4 Sunday
copies, and verifies the copy (`--verify-full` for a full `integrity_check`
instead of `quick_check`). This matters more than klines suggest: LSR,
open-interest and liquidation history beyond the upstream ~30-day retention is
**irreplaceable**. Retention here does not survive a disk failure — occasionally
copy the newest file off-machine.

**Calendar runway.** `scheduled_events` is static and populated years ahead
(currently to 2027-12-31). `monitor.py` alerts when less than 60 days of future
events remain; extend the `FOMC_DECISIONS` / `CPI_DATES` lists in
`fetch_events.py` and re-run it. Note `fetch_events.py` takes no arguments and
**wipes and repopulates** the table from its hardcoded lists:
```bash
python fetch_events.py
```

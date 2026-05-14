# Backlog — pending topics

Topics flagged for future work but not yet started. Newer entries at top
unless an explicit ordering matters. Each entry should be self-contained
enough that a fresh reader (or future-you with no memory of the
discussion) can pick it up.

---

# Phase 2 — restructure follow-ups

The 2026-05-14 structural restructure (see [Proposal.md](Proposal.md))
shipped everything mechanical: directories, file moves, import rewrites,
test green. The items in this section are what was deliberately deferred
from that effort and now need their own focused work.

Items are roughly ordered by dependency: independent / cheap items first,
design-heavy items in the middle, doc sweep last because it depends on
everything else settling.

## P2.1 — Extract `check_liquidations_for_variant` to `support/`

**Captured:** 2026-05-14 (deferred step 6g).
**Status:** ready to pick up; small + isolated.

### Motivation

`strategies/orchestrator.py:436` imports `check_liquidations_for_variant`
from `backtest_runner.py`. That's a live module depending on a
research module — a layer inversion. The function is the orchestration
wrapper that walks open shadow trades, calls
`strategies.support.margin_check.check_liquidations_for_variant` (the
math), then per-event calls the sleeve's close_fn. The math is already
in `support/`; the orchestration wrapper belongs there too.

### Scope

- Move `check_liquidations_for_variant` (~40 lines) and its helper
  `_load_close_fn` (~20 lines) from `backtest_runner.py` to
  `strategies/support/margin_check.py`.
- Rename to avoid the name clash with the existing
  `support/margin_check.check_liquidations_for_variant` (which is the
  math); call the orchestration wrapper something like
  `force_close_liquidations(variant_id, now_utc)`.
- Update `strategies/orchestrator.py:436` to import from `support/`.
- Update `backtest_runner.py:mark_remaining_at_end` (also uses
  `_load_close_fn`) to import from `support/`.
- No behavior change; all existing tests should pass unmodified.

### Dependencies

None — independent of all other phase-2 items.

### Risk

Low. Pure refactor; the function bodies don't change.

---

## P2.2 — Notebook conversion of `studies/notebooks/*.py`

**Captured:** 2026-05-14 (deferred during step 8).
**Status:** lowest-priority cleanup; user explicitly said "later".

### Motivation

During step 8 the research scripts moved from `tools/` to
`studies/notebooks/` but stayed as `.py` files. User direction:
"literal conversion to .ipynb" eventually, but "scripts can be first
moved to the new directory and then converted when we get there"
(2026-05-14).

### Scope

12 `.py` files to convert (and verify they still produce expected output):

```
studies/notebooks/bitstamp_adx_backtest.py
studies/notebooks/bitstamp_thu_bear_backtest.py
studies/notebooks/compare_with_fomc.py
studies/notebooks/fomc_backtest_drilldown.py
studies/notebooks/fomc_leverage_sensitivity.py
studies/notebooks/pdo_tv_validate.py
studies/notebooks/pdo_tv_validate_sweep.py
studies/notebooks/pdo_tv_validate_dump.py
studies/notebooks/pdo_tv_csv_parse.py
studies/notebooks/backtest_report.py
studies/notebooks/tools_statistical_validation.py
studies/notebooks/full_portfolio_report.py
studies/notebooks/r4_study/{r4_lib,era_split,walk_forward,grid_search,year_breakdown,sizing_study}.py
```

Each conversion: structure into cells, add markdown headers, drop the
`if __name__ == '__main__'` boilerplate where possible.

### Dependencies

None.

### Risk

None — purely cosmetic. Scripts still run as `.py` until converted.

---

## P2.3 — `run.py` → `bot.py` redesign

**Captured:** 2026-05-14.
**Status:** mechanical move + small redesign; touches the bot's entry point.

### Motivation

The proposal calls for `bot.py` as the single entry point for paper/live
trading, with no `--test` or `--mode sim` flags. Sim mode splits out
into a separate `studies/simulation/sim.py`. After `bot.py` exists, the
operator wrapper `tools/p300_run.ps1` can be dropped (user's stated
condition: "would not be needed if bot.py is properly implemented and
offers a way to filter console output").

### Scope

- `git mv run.py bot.py`.
- Drop `--mode sim` from the argparse. Sim-mode setup (clock injection,
  DB redirects, network isolation) moves to a new
  `studies/simulation/sim.py` that imports the orchestrator + sleeves
  and drives them with a fake clock. The closure-based sim primitive in
  `strategies/support/sim_loop.py` stays where it is.
- Decide whether `--once` (current smoke test) and `--feed` (current
  data-feed thread) survive or change. User wanted "data feed always
  on" — that argues `--feed` becomes the default (no flag).
- Update `register_p300.py`, `health.py`, README/MANUAL/OPERATIONS to
  reference `bot.py`.
- Drop `tools/p300_run.ps1` (and the now-empty `tools/` directory).
- Update `.claude/settings.json` allowlist entries that reference
  `run.py` / `tools/p300_run.ps1`.

### Dependencies

None — independent of orchestrator architecture, can ship at any time.

### Risk

Low-medium. Bot operator runs this every day; the renamed entry point
needs a smooth transition. Worth a single dry-run after the move to
confirm the bot starts up and ticks.

---

## P2.4 — Real orchestrator architecture

**Captured:** 2026-05-14 (the original "step 9b" from the migration plan).
**Status:** design + implementation. Largest single piece of phase 2.

### Motivation

`strategies/orchestrator.py` today is the renamed `variant_engine` — a
scheduler + ledger that calls each sleeve's `try_fire_for_variant` once
per tick. The conversation that produced [Proposal.md](Proposal.md)
identified six things a real orchestrator should own that today's code
does NOT:

1. **Cross-sleeve regime-weighted allocation.** Today each sleeve has a
   fixed `weight_pct` in `register_p300.py`. Only the J+ family has
   dynamic regime-based weights (via `REGIME_WEIGHTS_FULL` in
   `strategies/support/jplus_inputs.py`). Goal: `weight[sleeve][regime]`
   matrix applied uniformly to every sleeve.

2. **ML / rule-based gating framework.** Today THU_BEAR has its V4
   filter (CPI/NFP-adjacent, ex-OPEX), FOMC has its composite filter
   (phase × F&G × Polymarket), R4 has its vol-percentile gate — each
   hand-tuned per sleeve. Goal: a shared gating framework with proper
   walk-forward CV. Each sleeve registers a gate; orchestrator applies.

3. **Portfolio-level vol targeting.** Today only the J+ bundle vol-
   targets (`jplus.voltarget`). Per-sleeve vol-targeting would
   double-count because portfolio vol < sum of individual vols
   (correlation < 1). Goal: one portfolio vol target applied to all
   sleeves' combined exposure.

4. **Margin / risk budget enforcement.** Today the bot can run > 100%
   notional (mean concurrent 81%, P99 148%). No sleeve yields if margin
   tightens. Goal: orchestrator tracks margin headroom and reduces /
   defers lower-priority sleeves when constrained.

5. **Conflict resolution.** Today S-003 LONG BTC + S-096 SHORT BTC on
   the same Thursday run independently and net at the exchange (or
   worse, double-pay funding). Goal: orchestrator nets before opening.

6. **Signal aggregation.** Today multiple sleeves agreeing on direction
   open independent positions; their conviction isn't pooled. Goal:
   aggregate concordant signals into a conviction-weighted exposure.

### Scope

Each of the six above is its own design question. The work is multi-
commit and probably multi-session. A reasonable sub-decomposition:

- **P2.4a** — Extract per-sleeve allocation from `register_p300.py`
  composition into a single `weight[sleeve][regime]` table owned by the
  orchestrator. Sleeves read their weight from the orchestrator at tick
  time instead of from `sleeve_cfg.weight_pct`. The J+ family already
  reads dynamic weights via `today_inputs()` — generalize that pattern.
- **P2.4b** — Define a gating interface; refactor THU_BEAR V4 / FOMC
  composite / R4 vol-gate to register against it; document expected
  walk-forward CV protocol for new gates.
- **P2.4c** — Portfolio vol-target: replace the J+-only vol-target with
  a portfolio-level scalar applied to every sleeve's notional.
- **P2.4d** — Margin headroom check; deferral policy.
- **P2.4e** — Cross-sleeve conflict resolver.
- **P2.4f** — Signal aggregator.

### Dependencies

- Independent of [P2.5 / P2.6](#p25--shadow--paper-rename) (those touch
  the data layer; this touches orchestration).
- Should land before the SHADOW rename so the new orchestrator doesn't
  inherit the `execution_mode='SHADOW'` literal.
- P2.4a (allocation) is a precondition for P2.4d (margin enforcement)
  and P2.4f (signal aggregation) — both depend on a single source of
  truth for per-sleeve sizing.

### Risk

Medium. Each sub-feature changes how the bot sizes / opens positions.
Forward paper trade results would diverge from the pre-redesign series.
Worth a parity test (orchestrator outputs identical sizing for a fixed
regime / fixed inputs to today's logic, where the redesign hasn't
changed semantics).

---

## P2.5 — `SHADOW` → `paper` rename across code + DB

**Captured:** 2026-05-14.
**Status:** ready when the operator is OK pausing live paper trading
for the migration window (or running the migration online).

### Motivation

User directive 2026-05-14:
> "We should also drop the name 'shadow'; we have paper trades and we
>  have live trades. Only separation for these is available connection
>  to the exchange and we haven't even implemented that yet."

The `execution_mode='SHADOW'` literal exists in code (~50 sites) and
in `data/dashboard.db` (every trade row).

### Scope

**Code rewrites:**
- `strategies/trades.py` — `open_shadow_trade`, `close_perp_trade`,
  every internal `execution_mode='SHADOW'` literal.
- Each sleeve's `signal.py` — references like
  `from strategies.trades import open_shadow_trade`.
- `strategies/orchestrator.py` — the active-shadow-variants iteration
  (`get_active_shadows`).
- `strategies/support/variant_registry.py` — `status='SHADOW'` literal
  in variant rows.
- `strategies/support/trade_db.py` — schema default + comment.
- `strategies/support/strategy_health.py` — filter on `execution_mode`.
- `backtest_runner.py` — replay variants are SHADOW-status today.
- All tests that hard-code `SHADOW`.

**DB migration (one-shot, idempotent):**
- `UPDATE trades SET execution_mode='paper' WHERE execution_mode='SHADOW';`
- `UPDATE variants SET status='paper' WHERE status='SHADOW';`
- Same on `data/dashboard.db` (live) and any sim DBs.

**Test fixtures:** any test that builds a trade row with
`execution_mode='SHADOW'` updates to `'paper'`.

**Naming decision:** confirm enum values are `paper` and `live` (two
values, mutually exclusive). The trade row gets one or the other; live
means "connected to an exchange that filled the order".

### Dependencies

- After P2.4 (real orchestrator) ideally, so the new code doesn't ship
  with `SHADOW` literals and then immediately get rewritten.
- Before P2.6 (DB consolidation) so the renamed values land in the
  single consolidated DB rather than getting migrated twice.

### Risk

Medium. The DB migration must be coordinated with bot uptime —
running the UPDATE while a tick is mid-write is unsafe. Standard
approach: stop bot, run migration, restart.

---

## P2.6 — DB consolidation (`trader.db` + `dashboard.db` → `prod.db`)

**Captured:** 2026-05-14.
**Status:** scope clear, needs a migration plan.

### Motivation

Per Proposal.md and the decisions captured: one SQLite file for all
paper+live state. Today there are two:

- `data/trader.db` — market data (BTC/ETH klines, funding rates, LSR,
  scheduled events, cached external feeds).
- `data/dashboard.db` — bot state (variants, trades, trade_adjustments,
  ai_quant_decisions, fomc_observer).

The split is historical and adds friction (two paths to monkeypatch in
tests, two DBs to back up, two schemas to keep in sync). One DB is
simpler.

### Scope

- Schema design: namespace tables if any names collide. From inspection
  none do today, but verify.
- Migration script: `ATTACH DATABASE` both source DBs, copy tables to
  `prod.db`, verify row counts.
- Update `strategies/support/db.py` to expose a single `PROD_DB` (or
  keep `TRADER_DB` + `DASH_DB` as aliases pointing at the same file —
  decide).
- Decide on the sim equivalent: `data/databases/sim.db` per the
  proposal. `tools/build_sim_trader_db.py` already moved to
  `studies/simulation/build_sim_trader_db.py`; it builds a sliced
  trader.db today and needs to build a sliced prod.db instead.
- Update tests with DB-path fixtures (~10+ test files use either
  `TRADER_DB` or `DASH_DB` monkeypatches).
- Update `bootstrap.py` to create one DB instead of two.

### Dependencies

- After P2.5 (SHADOW rename) so we migrate once with clean enum values.
- Independent of P2.4 (orchestrator).

### Risk

Medium. Hot DBs. Same precondition as P2.5 — bot must be stopped
during the migration window. Backup before running.

---

## P2.7 — End-of-restructure doc sweep

**Captured:** 2026-05-14 (memory `project_doc_cleanup_planned`).
**Status:** waiting for everything else to settle. **Last in phase 2.**

### Motivation

User direction 2026-05-14:
> "Let's refactor PORTFOLIO.md, README.md, MANUAL.md, OPERATIONS.md at
>  the end. They are all kind of messy and hard to read."

Two concerns: (a) all internal `services/*`, `tools/*`, `jplus/*` path
references in those docs are stale after the restructure; (b) the docs
were already hard to read before the move. A path-fix pass alone isn't
enough — a readability rewrite is needed.

### Scope

For each of `PORTFOLIO.md`, `README.md`, `MANUAL.md`, `OPERATIONS.md`:
- Update every path link to the new location.
- Drop content that referred to dropped pieces (`SHADOW` terminology
  after P2.5, `services/` after the restructure, `jplus/` after step
  6c.2, `tools/` after step 8, `--mode sim` after P2.3).
- Re-organize sections for the reader to find what they need quickly
  (the current docs grew through audit cycles and add-ons; structure
  is layered chronologically rather than by topic).

`AUDIT_*.md` files are historical records — leave them alone.

### Dependencies

After everything else in phase 2 — paths and terminology need to be
final before the doc rewrite.

### Risk

Low. Doc-only.

---

# Earlier backlog entries

## AI_QUANT — let the model see its prior decisions

**Captured:** 2026-05-12
**Status:** planned, not started.

### Motivation

The current daily prompt at [strategies/sleeves/ai_quant/prompt.py:191-197](strategies/sleeves/ai_quant/prompt.py#L191-L197)
literally tells the model:

> exit_conditions = "Your checklist for TOMORROW's daily review — the
> runtime does NOT monitor these intra-day."

But tomorrow's model never sees yesterday's `exit_conditions`. Today's
call writes "exit if BTC closes below $78k", and the next day's call
re-decides from scratch with no awareness that the condition was ever
set. This is a documented loop that the codebase never closed.

Closing it gives the model: (a) the ability to honor exit conditions it
already committed to, (b) self-calibration via post-hoc P&L on its own
closed trades.

### Existing infrastructure

- Every decision lands in `ai_quant_decisions` ([data/dashboard.db](data/dashboard.db))
  with `exit_conditions`, `time_horizon_days`, `defer_until_utc`,
  `key_drivers_json`, `rationale_md`, `trade_action`, `confidence_caveats`.
- Markdown mirror under [data/ai_quant_archive/](data/ai_quant_archive/)
  via [strategies/sleeves/ai_quant/archive.py](strategies/sleeves/ai_quant/archive.py).
- [`get_recent_decisions`](strategies/sleeves/ai_quant/journal.py#L273) exists in
  the journal module with a docstring stating *"Future use: feeding
  decision history into the context bundle"*. Currently only used in tests.
- AI_QUANT trades land in the `trades` table with `strategy='AI_QUANT'`,
  with `entry_price`, `exit_price`, `pnl_pct`, `pnl_usdt`, `status`.
  **No FK** from `trades` back to the decision row that spawned it.

### Plan

Split into two milestones, with #1 strictly before #3 because #3 depends
on accumulated trade history that doesn't exist yet.

---

### Milestone 1 — Decision history (carryover commitments)

**Goal:** today's model sees what its prior self committed to, so it can
honor or rescind those commitments. Avoid anchoring bias by excluding
the prose rationale.

**Files to change:**

- [strategies/sleeves/ai_quant/journal.py](strategies/sleeves/ai_quant/journal.py) — extend
  `get_recent_decisions` SELECT to also pull `exit_conditions`,
  `time_horizon_days`, `defer_until_utc`, `confidence_caveats`. Keep
  `rationale_md` excluded (anchoring risk).

- [strategies/sleeves/ai_quant/context.py](strategies/sleeves/ai_quant/context.py) — new
  `_decision_history_section(variant_id, asset)`:
  - Pull last 7 days OR while
    `decision_utc + time_horizon_days * 86400 >= now`, whichever covers more.
  - Per row: `date`, `decided`, `conviction`, `time_horizon_days`,
    `trade_action`, `exit_conditions`, `confidence_caveats`, and a
    derived `status_now` ∈ `{open, closed, expired_horizon,
    superseded, deferred_active, deferred_expired}`.
  - `superseded` = there's a later same-day or later-day decision that
    replaced this one — keeps the section focused on still-relevant rows.
  - Cap ~7 entries; exclude `ERROR` rows.

- [build_context](strategies/sleeves/ai_quant/context.py#L612) — register as
  `decision_history` between `portfolio` and `data_freshness`.

- [strategies/sleeves/ai_quant/prompt.py](strategies/sleeves/ai_quant/prompt.py) — add a
  paragraph after the exit-conditions explanation:
  > **Carryover check.** `bundle.decision_history` carries the
  > exit_conditions and time_horizon you set on prior open positions.
  > Before deciding, explicitly evaluate each `status_now=open` row's
  > exit_conditions against today's bundle. If any fire, your decision
  > today should be FLAT. If none fire and the position is still within
  > its time_horizon, your default is to keep the call; argue explicitly
  > if you're rescinding. Do not defer to past rationale you can't see —
  > the section omits rationale_md on purpose to keep you re-deriving
  > the why from data each day.

- [tests/test_ai_quant_context.py](tests/test_ai_quant_context.py) —
  - Add `decision_history` to the expected-sections set.
  - Seed 3 decisions with mixed statuses; assert `status_now` is computed
    correctly for each.
  - Negative test: assert `rationale_md` is NOT present in any history row
    (the design constraint).

**Open question:**
- Include `confidence_caveats` or not? Argument for: caveats are "what
  would flip your view" — directly useful for today's check. Argument
  against: same anchoring concern as rationale. Current plan: include.

**Scope:** ~150 LOC + test. Self-contained, no schema changes.

---

### Milestone 2 — Post-hoc P&L (self-calibration)

Two phases because the decision→trade linkage is fuzzy today.

#### Phase 2a — instrument the link (one-time)

The clean version needs a stable join between `ai_quant_decisions` rows
and `trades` rows.

- Add column `ai_quant_decision_id INTEGER` to `trades` (migration in
  `strategies/support/trade_db.py::init_db` + `ALTER TABLE` for existing DBs, same
  pattern as the `defer_until_utc` migration on `ai_quant_decisions`).
- Find where AI_QUANT trades get emitted from a decision — start at
  [strategies/sleeves/ai_quant/decision.py](strategies/sleeves/ai_quant/decision.py) and
  search for `open_trade` / `trades.open_trade` callers. Pass the
  `decision_id` returned by `journal.save_decision` through to the new
  column.
- One-shot backfill tool: for existing AI_QUANT trade rows, fuzzy-match
  `(strategy='AI_QUANT', strategy_variant, asset)` against decisions by
  `actual_entry_time ≈ decision_utc` within ±2 min. Safe because
  AI_QUANT fires once per UTC day.

**Open question:** confirm exactly where the trade-emit call happens
before sequencing — we haven't traced it end-to-end.

**Scope:** small column + emitter wiring + ~50 LOC backfill tool.

#### Phase 2b — track-record section

- New `strategies/sleeves/ai_quant/track_record.py`:
  - `recent_closed_trades(variant_id, limit=15)` — JOIN `trades` ⋈
    `ai_quant_decisions` on `ai_quant_decision_id`, status='closed'.
  - `summary_stats(rows)` — N, win_rate, avg_win_pct, avg_loss_pct,
    expectancy_pct, max_dd_recent, current_streak (signed).
  - `by_conviction_bucket(rows)` — conviction `[30-50, 50-70, 70-100]` →
    (N, win_rate, expectancy). Surface only buckets with N≥3.
  - `by_direction(rows)` — LONG vs SHORT separately.

- [strategies/sleeves/ai_quant/context.py](strategies/sleeves/ai_quant/context.py) —
  `_track_record_section()` returning:
  ```
  {n_closed, last_15: [...], summary, by_conviction, by_direction,
   data_quality_note}
  ```
  `data_quality_note` carries an explicit caveat like *"N=4 closed
  trades — interpret as anecdotal, not statistically meaningful"* when N<10.

- [prompt.py](strategies/sleeves/ai_quant/prompt.py) — add calibration paragraph:
  > **Calibration check.** `bundle.track_record` shows your closed-trade
  > performance. Treat it as a sanity check, not a strategy input —
  > small samples are noisy. The valuable thing: if your win-rate at
  > conviction >70 is materially below 60%, you are overconfident; bring
  > today's conviction down a notch unless you can name what's
  > different. If a recent losing streak is concentrated in one
  > direction (e.g. 4 of last 5 SHORTs lost), be more skeptical of that
  > direction today.

- Register section in `build_context()`.

**Open questions:**
- Bucket boundaries `[30-50, 50-70, 70-100]` are guesses pre-data —
  revisit after ~50 decisions to see actual distribution.
- Time-decay weighting (recent trades weigh more) — defer until data
  shows it matters.

**Scope:** ~250 LOC + tests.

---

### Suggested sequencing

1. **Ship M1 first** — small, self-contained, closes the documented loop.
2. **Ship Phase 2a (instrumentation) next** — small standalone change.
   No prompt change yet. Lets clean trade-decision linkage accumulate.
3. **Ship Phase 2b** once N≥10 closed AI_QUANT trades exist. Without
   enough data, the section is just noise to the model.

Total ~500 LOC + a wait period of weeks between 2a and 2b for trade
history to accrue.

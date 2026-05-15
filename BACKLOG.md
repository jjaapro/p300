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

## P2.1 — Extract `check_liquidations_for_variant` to `support/` ✅

**Captured:** 2026-05-14 (deferred step 6g).
**Status:** completed 2026-05-14. The orchestration wrapper now lives in
`strategies/support/margin_check.py` as `force_close_liquidations(variant_id,
now_utc)`; the pre-existing math function keeps the
`check_liquidations_for_variant` name. `_load_close_fn` moved alongside it.
`backtest_runner.py` (three call sites) and `strategies/orchestrator.py` (one
call site) import from the new location. The layer inversion is gone and
all 530 tests pass.

### Motivation

`strategies/orchestrator.py:436` imports `check_liquidations_for_variant`
from `backtest_runner.py`. That's a live module depending on a
research module — a layer inversion. The function is the orchestration
wrapper that walks open paper trades, calls
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

## P2.2 — Notebook conversion of `studies/notebooks/*.py` ✅

**Captured:** 2026-05-14 (deferred during step 8).
**Status:** completed 2026-05-14. 17 scripts converted to `.ipynb`
(12 in `studies/notebooks/`, 5 in `studies/notebooks/r4_study/`).
`r4_study/r4_lib.py` kept as a `.py` library module — it's imported by
the other r4_study notebooks (`from r4_lib import …`) and converting it
would break those imports.

Conversion was done programmatically via `c:/tmp/py_to_ipynb.py`
(a one-shot ast-based splitter) using this cell-break heuristic:
module docstring → leading markdown cell; banner comments
(`# ─── label ───`) → markdown headers starting a new section; each
top-level `def` / `class` → its own code cell; `if __name__ ==
'__main__':` blocks dropped, replaced by a trailing `# main()` cell
the user can edit. Each generated notebook was validated by
ast-parsing its concatenated code cells (0 syntax errors across 17
files). Sleeve READMEs (adx / thu_bear / pdo) and one stale comment in
`strategies/support/indicators.py` were updated to point at the new
`.ipynb` paths.

### Dependencies

None.

### Risk

None — purely cosmetic. Scripts still run as `.py` until converted.

---

## P2.3 — `run.py` → `bot.py` redesign ✅

**Captured:** 2026-05-14.
**Status:** completed 2026-05-14. `run.py` renamed to `bot.py`; sim mode
moved to `studies/simulation/sim.py` (separate entry point, same
`sim_loop.run_sim(orchestrator.tick)` path so dispatch parity holds).
`--mode sim` flag and its argparse cluster removed from bot.py. Data
feed is now always-on in-process (the `--feed` flag is gone). Bot console
filters idle/heartbeat lines (`no_signal` / `tick ok` / `[feed]` etc.)
via a built-in `_NoiseFilter`; pass `--verbose` to disable. `--once` and
`--skip-gap-fix` survive. `tools/p300_run.ps1` and the empty `tools/`
directory are gone — the noise filter inside bot.py subsumes the
PowerShell wrapper's role. Docstring references across
`strategies/orchestrator.py`, `strategies/support/{env,strategy_health,
variant_registry,sim_loop}.py`, `backtest_runner.py`, `register_p300.py`,
and `studies/simulation/build_sim_trader_db.py` updated. README,
OPERATIONS, PORTFOLIO command examples and tables updated to point at
`bot.py` / `studies/simulation/sim.py`. `.claude/settings.json` allowlist
entries also updated. `tests/test_sim_mode.py` path fix lands here too
(the pre-existing P2.1-era failure — references to `tools/build_sim_trader_db.py`
— is now fixed as a side effect).

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
  Detailed design in [P2.4a design notes](#p24a-design-notes-2026-05-14)
  below.
- **P2.4b** — Define a gating interface; refactor THU_BEAR V4 / FOMC
  composite / R4 vol-gate to register against it; document expected
  walk-forward CV protocol for new gates.
  *2026-05-15: `strategies/support/gating.py` holds the `GateDecision`
  dataclass + `GATE_REGISTRY`. Five sleeves migrated — R4_BTC / R4_ETH
  / R4_BTC_V2 / R4_ETH_V2 (vol-gate; `leverage_mult` modulator) and
  S-096 / THU_BEAR (V4 event filter; binary `fire`). Orchestrator
  injects `_effective_gate` per dispatch. Pending:*
    - *FOMC composite — entangled with the sleeve's observer table +
      Phase 1/2 caching; deserves its own focused commit so the
      evaluate() call isn't run blindly per tick.*
    - *Walk-forward CV protocol for new gates — unwritten. Each gate
      should have a documented out-of-sample sharpe / expectancy
      benchmark before live-promotion.*
- **P2.4c** — Portfolio vol-target: replace the J+-only vol-target with
  a portfolio-level scalar applied to every sleeve's notional.
  *2026-05-15: orchestrator-injection plumbing shipped (parity-
  preserving). `strategies/support/portfolio_vol.py:current_vol_scalar`
  returns `today_inputs()["lev"]` for J+ sleeves (matching today's
  math) and None for tactical (today's behavior — no vol-targeting).
  Orchestrator + backtest_runner inject `_effective_vol_scalar` per
  dispatch. 6 J+ sleeves now read `_effective_vol_scalar` with
  `ti["lev"]` fallback for direct test callers. 21 parity tests
  anchor the contract. The actual portfolio-vol math (replacing the
  BTC-only J+ scalar with a true portfolio-vol estimate, applied to
  every sleeve) is a separate follow-up commit — at that point
  tactical sleeves opt into consuming `_effective_vol_scalar` and the
  semantics change. The dispatch wiring above does not.*
- **P2.4d** — Margin headroom check; deferral policy.
  *2026-05-15: scaffold + first opt-in shipped.
  `strategies/support/margin_headroom.py` exposes
  `current_gross_notional_usdt(variant_id)` (sums `size_usdt` across
  open paper trades — note: `size_usdt` is already the leveraged
  notional, see `trades.open_paper_trade`),
  `gross_cap_usdt(variant)` (reads
  `spec.allocator_notes.gross_notional_target_x`, default 2.5×
  capital), `headroom_usdt(variant)`, and
  `can_open(variant, candidate_notional_usdt) -> (bool, reason)`.
  Orchestrator + backtest_runner inject
  `_effective_margin_headroom_usdt` into every dispatched sleeve_cfg.
  All 13 dispatched sleeves have opted in by 2026-05-15: tactical
  (AI_QUANT, ADX, CPR, PDO, THU_BEAR, FOMC, CARRY) check on
  trade-open inside `try_fire_for_variant`; J+ family
  (R4_BTC / R4_ETH / R4_BTC_V2 / R4_ETH_V2 / EMA_BTC / ETH_DAILY)
  check on the fresh-open path (flip / scale on the two continuous
  sleeves don't grow gross net; close is no-op for the cap). Per-asset
  loop sleeves (CPR / PDO / THU_BEAR) cascade correctly across
  BTC -> ETH within one tick because `can_open` re-reads the DB each
  call, so the second asset's candidate sees the first asset's
  just-opened row. Status returned on overrun is `margin_constrained`
  (or `btc_cap_block` for the older PDO+CPR cap path).
  Pending: (b) proportional reduce policy instead of skip;
  (c) explicit sleeve priority (today: spec.composition iteration
  order).*
- **P2.4e** — Cross-sleeve conflict resolver.
  *2026-05-15: detection + first opt-ins shipped.
  `strategies/support/conflict_resolver.py` exposes
  `detect_opposing_open(variant_id, asset, direction)` — returns the
  earliest opposite-direction open paper trade on the same asset, or
  None — and `summarize_conflicts(variant_id)` for one-shot operator
  surveys. CARRY's delta-neutral perp SHORT is excluded from
  conflict detection (it's collateral, not a directional bet). 16
  module tests anchor the SQL filtering (status / variant / asset /
  direction / neutral exclusion / multi-asset). Two sleeves now opt in:
    - **AI_QUANT** — fresh open AND flip path. Conflict check runs
      BEFORE margin check (conflict = correctness, margin = sizing).
      Returns `skipped:directional_conflict` on fresh,
      `flip_aborted=directional_conflict` on flip. 4 new tests cover
      both paths + the CARRY-as-neutral case + concordant-direction
      (LONG vs LONG) passes through unaffected.
    - **THU_BEAR (S-096)** — per-asset loop checks for an opposing
      LONG on each (BTC/ETH) before opening its SHORT. ADX dispatches
      before THU_BEAR in composition order, so on a Thursday where
      both signals fire, ADX wins the slot and THU_BEAR yields with
      `directional_conflict`.
  Pending: more sleeves opt in (ADX could too — LONG vs an existing
  SHORT — but the dispatch order makes that rare); the Stage 2 goal
  (priority-based two-phase reconciliation with conviction comparison
  instead of first-come-first-served) shares the two-phase dispatch
  refactor with P2.4f Stage 2.*
- **P2.4f** — Signal aggregator.
  *2026-05-15: detection layer shipped (dual of P2.4e).
  `strategies/support/signal_aggregator.py` exposes
  `detect_concordant_opens(variant_id, asset, direction)` — every open
  paper trade matching the candidate's direction, sorted by entry time
  — and `summarize_concordant(variant_id)` — every (asset, direction)
  bucket with N>=2 stacked positions plus their summed notional + alloc.
  CARRY excluded (delta-neutral, same as P2.4e). 15 tests anchor the
  filtering + summary math. No orchestrator wiring yet; sleeves /
  operator dashboards consume directly. Stage 2 (pool concordant
  signals into one conviction-weighted exposure before opening)
  shares the two-phase-dispatch dependency with P2.4e Stage 2.*

### P2.4a status (2026-05-14)

**Complete.** ✅ All 13 sleeves migrated. `strategies/support/allocation.py`
holds the full WEIGHT_TABLE; orchestrator (`_tick_composition`) and
backtest runner (`tick_replay_variant`) classify regime once per tick
via `allocation.current_regime()` and inject `_effective_weight_pct`
into every sleeve dispatch alongside `_effective_leverage`. Every
sleeve's `try_fire_for_variant` reads `_effective_weight_pct` with a
fallback specific to that sleeve's history:

- **Tactical sleeves** (ADX, CARRY, THU_BEAR, PDO, CPR, FOMC,
  AI_QUANT) fall back to the static composition ``weight_pct``.
- **J+ sleeves** (R4_BTC, R4_ETH, R4_BTC_V2, R4_ETH_V2, EMA_BTC,
  ETH_DAILY) fall back to ``ti["weights"][short_key]`` — the legacy
  source the allocation table mirrors — so direct test callers that
  pass empty ``sleeve_cfg`` still work without changes.

84 parity tests in `tests/test_allocation_parity.py` anchor the
contract end-to-end: per-sleeve × per-regime table values match
register_p300 constants (tactical) and `_cap_core_weights` output
(J+); resolver fallback paths (regime=None, unknown sleeve, unknown
regime, no static weight) behave correctly; and the J+ orchestrator-
injection path matches the legacy `ti["weights"]` path.

### P2.4a design notes (2026-05-14)

**Today's allocation surface — two parallel code paths.**

1. *Tactical sleeves* (S-003 / S-078 / S-096 / PDO / CPR / FOMC /
   AI_QUANT) read `sleeve_cfg["weight_pct"]` at dispatch time. The
   numbers are static constants in `register_p300.py` composition:
   `{15, 8, 6, 9, 5, 5, 2}`. Regime-independent.
2. *J+ sub-sleeves* (six entries: `JPLUS_R4_BTC` / `_ETH` / `_V2_BTC` /
   `_V2_ETH` / `EMA_BTC` / `ETH_DAILY`) keep `weight_pct=0` placeholder
   and instead pull regime-weighted sizing from
   `strategies.support.jplus_inputs.today_inputs()` at trade-open time.
   The actual table is `REGIME_WEIGHTS_FULL` in `jplus_inputs.py`
   keyed by `strong_bull` / `mild_bull` / `uncertain` / `bear`.

There are **two regime vocabularies in the repo**:

- `regime_jplus.classify_day` — used by J+ family. Modes:
  `strong_bull` / `mild_bull` / `uncertain` / `bear`.
- `regime_tactical.classify_regime` — used by tactical gating logic
  (e.g. PDO's regime threshold, THU_BEAR's V4 filter). Modes:
  `bull_trend` / `bear_trend` / `chop` / `sell_off`.

This split is the first thing the design has to resolve.

**Proposed shape.**

```
strategies/support/allocation.py
  REGIME_VOCAB: {"strong_bull", "mild_bull", "uncertain", "bear"}
  # Single source of truth, reused from regime_jplus. Tactical
  # classifier stays where it is for sleeve-internal gates; the
  # allocator only needs ONE regime label per tick.

  WEIGHT_TABLE: dict[strategy_id, dict[regime, float]]
    "S-003":           {strong_bull: 0.15, mild_bull: 0.15, uncertain: 0.15, bear: 0.15},
    "S-078":           {... 0.08 across all regimes ...},
    "S-096":           {... 0.06 across all regimes ...},
    "PDO-L-RF":        {... 0.09 across all regimes ...},
    "CPR":             {... 0.05 across all regimes ...},
    "FOMC":            {... 0.05 across all regimes ...},
    "AI_QUANT":        {... 0.02 across all regimes ...},
    "JPLUS_R4_BTC":    {strong_bull: 0.15 × scale, mild_bull: 0.20 × scale, ...},
    "JPLUS_R4_ETH":    {...},
    ...
    # J+ rows are CORE_ALLOC_CAP-scaled (already done by
    # _cap_core_weights today; the table caches the scaled values
    # so the orchestrator doesn't re-run the cap each tick).

  def current_regime(now_utc: datetime | None = None) -> str:
      # Wraps regime_jplus.classify_day for today's date.

  def get_weight_pct(strategy_id: str, regime: str | None = None) -> float:
      # regime=None -> look up current_regime(). Returns the pre-leverage
      # allocation fraction as a percent (e.g. 0.15 -> 15.0). Returns
      # 0.0 for unknown strategy_id with a one-shot warning.
```

**Orchestrator integration.**

`strategies.orchestrator._tick_composition` already injects
`_effective_leverage` into each `sleeve_cfg` before dispatch
(see `_resolve_sleeve_leverage`). Add a parallel
`_effective_weight_pct` injection:

```python
def _resolve_sleeve_weight(spec, sleeve, regime) -> float:
    from strategies.support import allocation
    sid = sleeve.get("strategy_id")
    if sid:
        w = allocation.get_weight_pct(sid, regime)
        if w is not None:
            return w
    return float(sleeve.get("weight_pct", 0.0))   # static fallback
```

In `_tick_composition`, compute `regime = allocation.current_regime()`
once per tick and pass it into the resolver. Each sleeve_cfg copy gets
both `_effective_leverage` and `_effective_weight_pct`.

**Sleeve migration (one at a time).**

Each tactical sleeve changes:

```python
- alloc_pct = float(sleeve_cfg.get("weight_pct", 0.0))
+ alloc_pct = float(
+     sleeve_cfg.get("_effective_weight_pct",
+                    sleeve_cfg.get("weight_pct", 0.0)))
```

The fallback to `weight_pct` keeps unmigrated sleeves working during
the rollout, and keeps unit tests that build sleeve_cfg dicts manually
green without touching them.

Migration order (mirrors structural-restructure rhythm):

1. ADX (pilot, smallest blast radius)
2. CARRY, THU_BEAR, PDO, CPR, FOMC (5 tactical, one commit each)
3. AI_QUANT (special: conviction-scales INSIDE the weight cap; the
   refactor here is purely "swap source of the cap", logic unchanged)
4. J+ sub-sleeves last. Today's J+ handlers pull weight from
   `today_inputs()` directly. The migration here is to have them call
   `allocation.get_weight_pct()` which delegates back to the cached
   `today_inputs()` table — so the math is identical; only the routing
   changes.

**Parity contract.**

A new test `tests/test_allocation_parity.py` asserts: for each of the
4 regimes × 13 sleeves, `allocation.get_weight_pct(sleeve, regime)`
matches what the current dispatch resolves to with the same inputs.
Built before any sleeve is migrated; stays green across the whole
rollout.

For tactical sleeves the assertion is trivial (weight is regime-
independent). For J+ sleeves the assertion runs `today_inputs()` with
a fixed `now_utc` per regime and compares the resulting weight ×
inner-R4-lev to `get_weight_pct() × _resolve_sleeve_leverage()`.

**Decisions (user, 2026-05-14):**

- **Tactical sleeves stay regime-independent for P2.4a.** Per-regime
  tactical tuning is deferred — "we can do this later at the end of
  this refactoring or whenever it is proper." Keep today's constants
  on each row.
- **`CORE_ALLOC_CAP` via runtime pass (option b).** The cap stays as
  a runtime scaling pass inside `allocation.get_weight_pct`. The
  WEIGHT_TABLE rows hold raw values; the cap is applied at lookup
  time. Cap can be tuned without rewriting the table.
- **No Core/Tactical split — drop the 50/50 cap policy entirely.**
  User direction: "All sleeves... truly are no different from each
  other. I wouldn't split 50/50 anything. I would use orchestrator
  to define what strategies have highest chance of profiting in
  different environments (regime, volume, etc.) and adjust allocation
  dynamically before entry." No `TACTICAL_ALLOC_CAP` constant is
  added. `CORE_ALLOC_CAP=0.50` survives only as a transitional
  safety on the J+ family while migration is in flight; orchestrator
  takes over allocation end-to-end in later sub-tasks (P2.4c–f and
  follow-ups). See memory [[feedback_no_core_tactical_tiers]].
- **`register_p300.py` is on the way out.** End state: orchestrator
  owns sleeve enumeration + allocation + variant registration; the
  standalone register script disappears entirely (consolidation per
  lean-tooling preference). For P2.4a specifically: `weight_pct` in
  composition becomes informational. We keep it during migration as
  a pre-migration sanity check (the parity test reads it), but it
  has no behavioral effect once a sleeve switches to
  `_effective_weight_pct`. The script itself doesn't go away in
  P2.4a; that consolidation happens after the orchestrator owns
  everything register_p300 currently sets up.

**Risk assessment.**

Low-medium. Behavior-preservation parity tests are cheap and concrete
(specific numeric assertions per regime). The change touches every
sleeve's dispatch, but each sleeve's edit is one line. The biggest
risk is mis-classifying which regime applies at tick time — current
classifier is "today as of last full day" (J+ pattern), not "right
now", so an off-by-one regime selection at the day boundary needs an
explicit test.

### Dependencies

- Independent of [P2.5 / P2.6](#p25--paper--paper-rename) (those touch
  the data layer; this touches orchestration).
- Should land before the paper rename so the new orchestrator doesn't
  inherit the `execution_mode='paper'` literal.
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

## P2.5 — `SHADOW` → `paper` rename across code + DB ✅

**Captured:** 2026-05-14.
**Status:** completed 2026-05-15. Code rename done in one mechanical
pass — `'SHADOW'` literals across ~30 files swapped for `'paper'`,
identifiers renamed (`open_shadow_trade` → `open_paper_trade`,
`_close_X_shadow` → `_close_X_paper`, `_close_due_paper_trades`,
`get_active_paper_variants`, etc.), test names updated, docs/comments
updated where prose still made sense ("paper trades" survives as
phrasing). DB migration script at
`studies/simulation/migrate_shadow_to_paper.py` — idempotent, dry-run
mode supported. Applied to `data/dashboard.db` (3,451 trades + 41
variants migrated; backup at `data/dashboard.db.bak_pre_paper_rename`).
AUDIT_*.md files left frozen with the old terminology by design.

### Notes

Original motivation captured 2026-05-14: drop the name "shadow"; the
only real distinction is whether the bot has an exchange connection
(paper vs live). Memory:
[[feedback_naming_paper_not_shadow]].

Mechanically the rename split into:
- Code-side: `'SHADOW'` literals → `'paper'`; identifier renames
  (`open_paper_trade`, `_close_X_paper`, `_close_due_paper_trades`,
  `get_active_paper_variants`, `_create_paper_trade`,
  `_paper_trade_exists`); test-fn names; docstring / comment wording.
  Ran via a one-shot `c:/tmp/rename_shadow_to_paper.py` script
  (regex + word-boundary identifier swap; AUDIT_*.md skipped).
- DB-side: `studies/simulation/migrate_shadow_to_paper.py` runs
  `UPDATE trades` + `UPDATE variants` for the two tables that hold
  the enum. Idempotent, dry-run supported. Backup before running.

The naming decision is confirmed: `paper` and `live` are the two
enum values; the trade row gets one or the other.

---

## P2.6 — DB consolidation (`trader.db` + `dashboard.db` → `prod.db`) ✅

**Captured:** 2026-05-14.
**Status:** completed 2026-05-15.

Schema audit confirmed no table-name collisions between the two
sources (13 trader-side tables + 7 dash-side tables, all distinct).
Migration script `studies/simulation/migrate_to_prod_db.py` uses
`ATTACH DATABASE` to copy every table into `data/prod.db` with row-
count verification + index recreation. Idempotent, dry-run mode
supported. Applied locally: 6,897,394 trader rows + 37,964 dash rows
+ 9 indexes recreated; row counts match between source and prod.

`strategies/support/db.py` now exposes `PROD_DB = data/prod.db`;
`TRADER_DB` and `DASH_DB` are kept as aliases pointing at the same
file so existing read sites (~26 modules) and test monkeypatches
work without touching them.

`bootstrap.py` updated to create `data/prod.db` (instead of
trader.db); `studies/simulation/build_sim_trader_db.py` default
source now points at `prod.db`; `tests/test_sim_mode.py`
`LIVE_TRADER_DB` / `LIVE_DASH_DB` constants both resolve to
`prod.db`. Source files (`data/trader.db`,
`data/dashboard.db`) remain on disk as backups (renamed
`.bak_pre_prod_consol`); they can be deleted once forward operation
on `prod.db` is verified over a paper-trading cycle.

Sim equivalent of prod.db: tests build temporary sliced DBs via
`tmp_path` fixtures and monkeypatch `db.{TRADER,DASH}_DB`; that path
works unchanged since both aliases land on the same constant.

Tests: full suite passing against prod.db (same count as before; no
behavioral change).

---

## P2.7 — End-of-restructure doc sweep

**Captured:** 2026-05-14 (memory `project_doc_cleanup_planned`).
**Status:** 2026-05-15 — path-fix pass shipped (Stage 1). README's
architecture tree rewritten to reflect the post-restructure layout
(strategies/sleeves/, strategies/support/, data/sources/, studies/);
~127 stale `services/*` / `jplus/*` / `tools/*` / `trader.db` /
`dashboard.db` / `run.py` / `--mode sim` references replaced across
README, OPERATIONS, PORTFOLIO, MANUAL. Final stale `dashboard.db`
prose mentions updated to `prod.db` or `--dash-db` flag references.
Stage 2 (readability rewrite — "messy and hard to read" per user)
remains outstanding; defer until Phase 2 design-heavy items
(P2.4d/e/f) settle so the docs describe a stable surface.

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
- Drop content that referred to dropped pieces (`paper` terminology
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

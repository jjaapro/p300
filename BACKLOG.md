# Backlog — pending topics

Topics flagged for future work but not yet started. Newer entries at top
unless an explicit ordering matters. Each entry should be self-contained
enough that a fresh reader (or future-you with no memory of the
discussion) can pick it up.

---

## AI_QUANT — let the model see its prior decisions

**Captured:** 2026-05-12
**Status:** planned, not started.

### Motivation

The current daily prompt at [services/ai_quant/prompt.py:191-197](services/ai_quant/prompt.py#L191-L197)
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

- Every decision lands in `ai_quant_decisions` ([dashboard.db](data/dashboard.db))
  with `exit_conditions`, `time_horizon_days`, `defer_until_utc`,
  `key_drivers_json`, `rationale_md`, `trade_action`, `confidence_caveats`.
- Markdown mirror under [data/ai_quant_archive/](data/ai_quant_archive/)
  via [services/ai_quant/archive.py](services/ai_quant/archive.py).
- [`get_recent_decisions`](services/ai_quant/journal.py#L273) exists in
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

- [services/ai_quant/journal.py](services/ai_quant/journal.py) — extend
  `get_recent_decisions` SELECT to also pull `exit_conditions`,
  `time_horizon_days`, `defer_until_utc`, `confidence_caveats`. Keep
  `rationale_md` excluded (anchoring risk).

- [services/ai_quant/context.py](services/ai_quant/context.py) — new
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

- [build_context](services/ai_quant/context.py#L612) — register as
  `decision_history` between `portfolio` and `data_freshness`.

- [services/ai_quant/prompt.py](services/ai_quant/prompt.py) — add a
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
  `services/trade_db.py::init_db` + `ALTER TABLE` for existing DBs, same
  pattern as the `defer_until_utc` migration on `ai_quant_decisions`).
- Find where AI_QUANT trades get emitted from a decision — start at
  [services/ai_quant/decision.py](services/ai_quant/decision.py) and
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

- New `services/ai_quant/track_record.py`:
  - `recent_closed_trades(variant_id, limit=15)` — JOIN `trades` ⋈
    `ai_quant_decisions` on `ai_quant_decision_id`, status='closed'.
  - `summary_stats(rows)` — N, win_rate, avg_win_pct, avg_loss_pct,
    expectancy_pct, max_dd_recent, current_streak (signed).
  - `by_conviction_bucket(rows)` — conviction `[30-50, 50-70, 70-100]` →
    (N, win_rate, expectancy). Surface only buckets with N≥3.
  - `by_direction(rows)` — LONG vs SHORT separately.

- [services/ai_quant/context.py](services/ai_quant/context.py) —
  `_track_record_section()` returning:
  ```
  {n_closed, last_15: [...], summary, by_conviction, by_direction,
   data_quality_note}
  ```
  `data_quality_note` carries an explicit caveat like *"N=4 closed
  trades — interpret as anecdotal, not statistically meaningful"* when N<10.

- [prompt.py](services/ai_quant/prompt.py) — add calibration paragraph:
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

---

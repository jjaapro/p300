# Backlog — pending topics

Topics flagged for future work but not yet started. Newer entries at top
unless an explicit ordering matters. Each entry should be self-contained
enough that a fresh reader (or future-you with no memory of the
discussion) can pick it up.

---

## Project status and roadmap — 2026-09-12

**This section is the roadmap.** It is updated in the same commit as anything that ships,
is held, is killed or is decided (user request 2026-09-12: the roadmap must reflect the
current project state). Add a new dated block above the previous one; the topic entries
further down stay as they are.

### Where we are

- **What runs:** the paper fleet started by `start_fleet.ps1` — `feed.py` plus seven bot
  units (`chento_v3` BTC, `chento_v3_eth`, `short_squeeze`, `adx`, `carry`, `squeeze_bull`,
  `r4`) and the dashboard; nine bot variants (the two squeeze bots each carry a no-stop
  twin; `r4` is one variant with its ETH windows only). The legacy orchestrator path
  (`bot.py`, variant `p300_aggressive_v2_v1_0`: EMA_BTC, ETH_DAILY, THU_BEAR, PDO, CPR,
  FOMC, AI_QUANT) has been dormant since 2026-06-11; its row is still `enabled = 1` but
  nothing dispatches it.
- **Paper evidence is thin:** 36 paper trades in total, 9 by the bots — ADX 1 open, CARRY
  1 open, SQUEEZE_BULL 1 open, chento BTC 6 rows that are 3 signals double-booked during the
  2026-08-15..24 doubled-fleet incident, chento ETH 0 (three short fires, all filtered),
  SHORT_SQUEEZE 0, r4 0. Nothing clears an honest DSR
  ([validation_audit_2026_09](studies/notebooks/validation_audit_2026_09/findings.md)); the
  constraint is breadth of evidence, not edge.
- **Shipped 2026-09-12, morning** (commits `5df9772` / `2e9d596` / `5418bbb`):
  measured execution costs (chento 10 bp, SHORT_SQUEEZE 10, SQUEEZE_BULL 7, ADX 11), CARRY's
  trailing-30-day cumulative-funding exit, no-stop paper twins for SQUEEZE_BULL (0.5×
  notional) and SHORT_SQUEEZE (1×) with re-cuts fixed in advance at 20 / 30 paired fires,
  pool-plan decisions D8 (ADX + CARRY in one account) and F-EXEC (execution-layer
  requirements), `GATE_VALIDATION.md` §8.
- **Shipped 2026-09-12, afternoon** (`d4d811c` R4, `6451699` roadmap and docs, `eb4f2e2` data):
  R4 runs its ETH windows only and is back in the fleet defaults
  with the 2026-09-09 hold lifted for that pair ([docs/calibration/r4.md](docs/calibration/r4.md));
  `botlib.ensure_bot_variant` refreshes a bot's label from config; pool-plan D9 (no-stop
  sleeves are paired with something stable or isolated); this section; the README and
  OPERATIONS status pointers.
- **Shipped 2026-09-12, evening — the paper ledger's integrity guards.** Roadmap steps 4.2
  and 4.3, both done. The per-bar idempotency key was NOT missing: the column, the partial
  UNIQUE index, the pre-check and the race fallback shipped 2026-06-05, but
  `open_paper_trade`'s `signal_time_iso` was optional with a silent wall-clock fallback and
  **four of seven bots fell through it**. SHORT_SQUEEZE read `reason["bar_ts"]` from a dict
  that only ever carried `bar_ts_utc`, so it was unprotected from its 2026-07-21 deployment
  onward while a comment claimed otherwise; ADX, CARRY and R4 passed no key at all. Each now
  keys on the granularity its own in-process guard already enforced (trigger bar; UTC day;
  UTC day per window), so no trade that was permitted before is refused now — the guard just
  moved from check-then-insert, which two processes can both pass, to the DB's UNIQUE index,
  which they cannot. SQUEEZE_BULL's unguarded `str(reason.get("bar_ts"))` was a latent
  landmine: an absent bar would key the literal `"None"` and block that variant's second open
  forever. The fallback now logs. `ledger_coherence`'s duplicate detector grouped on the
  exact `entry_time` and so returned **zero** groups against the August incident it exists
  for (the paired fills are 33 s / 33 s / 28 s apart); re-bucketed to the minute it returns
  exactly those three pairs, each flagged `distinct_keys = 2`, which is the signature of two
  processes rather than one retry. `botlib.heartbeat` has returned False on a detected
  duplicate instance since August and **every runner discarded it**; it now sets a
  stand-down that refuses entries at `open_paper_trade` — exits keep running in both
  processes on purpose, since a double close is idempotent but unmanaged positions are not.
  `KNOWN_SLEEVES` += CHENTO_TRIPLE_V3 / SHORT_SQUEEZE / SQUEEZE_BULL. Suite 1332 → 1347.
- **The fleet was restarted 2026-09-12 17:11–17:12** and r4 has been started; all seven bots,
  the feed and the dashboard are up. That restart predates the evening commit above, so the
  running processes do **not** carry the integrity guards — they need another restart.
  Two live-fleet defects found during that check and still open: `monitor.py` is not running
  and has no scheduled task (nothing is watching the LSR / OI retention burn-down), and the
  `start_fleet.ps1` feed console is a zombie — its `feed.py` exited at launch and the live
  feed was started 44 s later from a separate shell with `--force-start`, so console-based
  triage currently lies about which feed is alive.
- **Active plan documents:** [bot_extraction_plan.md](studies/material/plans/bot_extraction_plan.md)
  (the architecture in force: one bot = one variant = one future sub-account; supersedes
  pool-plan phases B / D / F), [pool_restructure_implementation_plan.md](studies/material/plans/pool_restructure_implementation_plan.md)
  (design record, decisions D1–D9, dated status block at its top),
  [multi_asset_chento_plan.md](studies/material/plans/multi_asset_chento_plan.md) (Phase B
  as written — one runner, per-asset variant ids — is not what shipped), this file.

### Next, in order

1. **Operator.** Restart all seven bots so the integrity guards load — the 17:11 restart
   predates them. Start the hourly monitor (`.\start_fleet.ps1 -Units monitor`, or
   `-Monitor`): it is not running and has no scheduled task, so nothing is watching the
   LSR / OI retention burn-down. Close the zombie "p300 feed" console and decide whether the
   live `--force-start` feed should be relaunched under `start_fleet.ps1` so the duplicate
   guard applies to it. `python monitor.py --deep` the next day.
2. **Paper evidence — waiting, not work.** First fires of the no-stop twins and of r4 ETH;
   weekly `strategy_health` per bot. The paired re-cut script now exists —
   `studies/notebooks/squeeze_recut/`, written 2026-09-12 while `n_paired = 0` so the
   decision code predates the data it judges; `run_recut.py --sleeve both` reports NOT_DUE
   with 0 / 20 today and exits non-zero on any DISABLE verdict.
3. **Research queue — pre-registered notebooks, user picks the order.**
   1. Shelf re-cost under the no-stop style at measured per-leg costs (R4 windows,
      post-cascade reversion, absorption, footprint C3, PDO), then fleet-level compounding
      through the liquidation walk. Pass bar: net ≥ 2× the measured round trip, both halves.
   2. Condition-only exits (inverse signal, regime flip, flow reversal, OI rebuild, funding
      normalisation) on chento, SQUEEZE_BULL, SHORT_SQUEEZE and ADX at measured cost with
      mark-to-market drawdown; fixed-R on current equity against an exposure-matched
      buy-and-hold with start-date sensitivity (`studies/lib/validation/benchmark.py`).
   3. ETH/BTC through the two-axis screen: the regime-conditional spread (strong_bull days
      only; +113 bp/day, t 3.4 since the ETH ETF, but one bull episode; unconditional
      correlation 0.83 at every timeframe, no lead-lag, ratio ≈ random walk) and hedged
      expressions of existing signals. ETH_DAILY is the dormant expression; a
      `bots/eth_regime` extraction is the path if it clears.
   4. Second assets where the data exists and nothing is studied: SHORT_SQUEEZE on ETH,
      CARRY on ETH. Closed: ADX on ETH (KILL). Blocked on data: SQUEEZE_BULL on ETH (no ETH
      open-interest feed). Alts: the 150-symbol screener tables are daily / 1 h only and
      that feed stopped 2026-05-23.
   5. R4 target-exit sweep, the one untested exit refinement; no entry conditioner exists
      (2026-09-12 check, 11 cells, largest |t| 0.9).
4. **Code, each needs a go-ahead before the first commit.**
   1. **Bot = directory = strategy** (direction agreed 2026-09-12; topic entry below):
      strip the orchestrator interface from the running sleeves, move each under its bot
      as its strategy module, retire `bot.py` / the orchestrator / sim mode, archive the
      eight dormant sleeves. Absorbs the chento BTC + ETH single-variant fold (multi-asset
      plan Phase B), the `chento_limit_bid` archival (pool-plan A7) and the legacy variant
      row. Starts after step 1: the file moves happen between a fleet stop and a restart.
      **Step 1 was re-planned 2026-09-12** — see "Step 1 re-planned" in the topic entry
      below. Its original gate does not work (five of the six named parity tests never touch
      the surface being changed), and the obvious edit silently zeroes research replay. The
      replacement is expand-contract behind a golden-record net, ~23 commits in four phases,
      with the first live strategy-module edit needing its own go-ahead.
   2. ~~A DB-level per-bar unique key for paper trades~~ — **done 2026-09-12**; the real gap
      was the optional `signal_time_iso` and four bots falling through it, see the evening
      entry above.
   3. ~~`strategy_health.KNOWN_SLEEVES` += CHENTO_TRIPLE_V3, SHORT_SQUEEZE, SQUEEZE_BULL~~
      (pool-plan A6) — **done 2026-09-12**.
   4. **`botlib.close_due_trades` books the wrong cost.** The scheduled-exit backstop calls
      `close_perp_trade` with no cost overrides, so it charges the 15 bp default while the
      sleeves charge their measured 7 bp (SQUEEZE_BULL) and 10 bp (SHORT_SQUEEZE). It is a
      true fallback — the sleeve's own sweep runs first on every tick — but when it does
      fire it mis-prices by 8 bp, which is 0.04 R on SQUEEZE_BULL and 0.07–0.5 R on
      SHORT_SQUEEZE, against a pre-registered "DISABLE if live diverges from replay by
      > 0.05 R" tripwire. Needs a per-sleeve cost passthrough and a calibration-log entry in
      both docs, because it changes booked P&L.
   5. **`trade_adjustments` is missing `UNIQUE(trade_id, event_date, event_type)` in prod.**
      `strategies/support/trade_db.py` declares it and `record_adjustment` relies on it, but
      the 2026-05-18 PK rebuild dropped it and `CREATE TABLE IF NOT EXISTS` cannot retrofit —
      the documented failure mode in the `feedback-table-pk-required` memory, recurring. Only
      `UNIQUE (trade_id, seq)` survives, and `seq` is computed from the current max, so a
      sequential retry gets `seq + 1` and lands a duplicate event. Needs a table rebuild
      between a fleet stop and a restart, after a backup.
5. **Decisions waiting on the user.** E7 live quoting probe (API key, ≤ $50, two weeks,
   only if maker entries are wanted); SHORT_SQUEEZE's fate at the n = 30 re-cut (both
   variants ≤ 0 → retire); pool-plan D1–D7 (unchanged since June) and the D9 recommendation
   (Standard is the no-stop / experimental account, no new sub-accounts); whether the ETH
   bull-regime tilt gets its study.

### Concluded since 2026-09-01 (verdict; `findings.md` in each folder under `studies/notebooks/`)

`brainstorm_validation_2026_09` all five external claims KILL, engine claims confirmed ·
`execution_2026_09` coded costs 2–3× too high, SHORT_SQUEEZE retire-pending → answered by
its no-stop twin · `sizing_style_2026_09` SQUEEZE_BULL no-stop BUILD → shipped as the twin,
fixed-R beats compounding beats fixed notional · `carry_exit_rule_2026_09` CUM-30D shipped ·
`adx_robustness_2026_09` the live-vs-research gap is funding on longs → D8 ·
`adx_eth_2026_09` KILL · `validation_audit_2026_09` nothing clears DSR 0.95, two published
numbers corrected · `squeeze_bull_revalidation` BUILD → shipped 2026-09-09 · `r4_bot_prep`
no stop, 300 s grace · `range_sanity_2026_09`, `calendar_cells`, `coinbase_premium`,
`delta_neutral` KILL · `basis_carry` INCONCLUSIVE (data gate) · `vrp_study` do not advance ·
`anchor_allocator_study` KILL · `lsr_b5_study` no change. The 2026-09-06 `REPO_REVIEW.md`
findings were all addressed on 2026-09-07
([docs/strategy_issue_validation_2026_09_07.md](docs/strategy_issue_validation_2026_09_07.md):
218,908 minute rows repaired, minute stop paths, daily marked equity).

### Documents known to be stale

The full readability sweep of README / PORTFOLIO / MANUAL / OPERATIONS is still deferred
(memory `project-doc-cleanup-planned`). On 2026-09-12 README and OPERATIONS got a
one-paragraph status pointer to the fleet; their bodies still describe the dormant `bot.py`
loop, and MANUAL.md describes the pre-fleet manual J+ routine. PORTFOLIO.md §2 lists sleeves
of the dormant orchestrator path as if composed. Pool-plan Phase E predates the shipped
SQUEEZE_BULL (OI-flush only; Rule B deferred).

---

## Bot = directory = strategy — retire the orchestrator layer

**Captured:** 2026-09-12. **Status:** planned, direction agreed by the user ("why do we
need the sleeves if we have bots? I like the maintainability of the bots"); starts after
the 2026-09-12 batch is committed and the bots restarted; the first file move needs a
go-ahead (production code).

**Why.** The bot runners are the maintainable unit: `bots/<name>/config.py` holds only what
the operator controls (variants, capital, risk, caps, grace, stale policy) and the seven
runners total ~1,400 lines. The strategy module must stay separate from the runner — the
parity tests feed the same candles to it that the research harness saw and require the
same fires; one sleeve serves two bots (chento BTC / ETH), two variants (the squeeze
twins) and four windows (R4) — but the orchestrator-era framework around the sleeves is
dead weight: 42 `_effective_*` injections across 15 sleeve files, `try_fire_for_variant`
wrappers, `priority` / `conviction` arguments no bot uses, a dispatch registry keeping
eight dormant sleeves alive, and ~1,800 lines of dormant loop code (`bot.py`,
`strategies/orchestrator.py`, `backtest_runner.py`, `studies/simulation/sim.py`). That second
system is what makes "sleeves" read as overhead.

**Target shape.** `bots/<name>/{config,runner,strategy}.py` (+ `windows.py` where a bot
needs it): one directory = one bot = one strategy. `strategy.py` exposes `decide(...)`,
`execute(...)` and `sweep(...)` with plain arguments — no `sleeve_cfg` dict, no
`_effective_*`, flags such as `use_stop` / `count_diag` passed explicitly. Calibrated
parameters keep `docs/calibration/<name>.md`. Shared support (`strategies/support/`,
`strategies/trades.py`, `botlib.py`) is unchanged. Chento becomes one strategy module with
the asset as a call-time parameter (today `CHENTO_V3_ASSET` is read at import, which is why
BTC and ETH are two processes), so one runner can carry both assets under one cross-asset
cap — the multi-asset plan's Phase B as written.

**Steps, each its own commit with the suite green:**

1. ~~Strip the orchestrator interface from the six running strategy modules~~ — **DONE
   2026-09-13** after being re-planned; see the section below. The original one-line step
   was unsafe (its gate did not work, and the obvious edit silently broke research replay),
   so it became four phases and 20 commits. All six sleeves now expose plain-keyword
   `decide()`/`execute()`, all seven bots call them, and no runner carries a `sleeve_cfg`.
   **Step 2 — moving each module under its bot — is next, and is the one that needs the
   fleet stopped.**
2. `git mv` each module under its bot and re-point the importers (~50 files: the tests
   above, `studies/notebooks/adx_study/harness.py`, `adx_robustness_2026_09/adx_lib.py`,
   the chento_journal validations, `strategy_comparison_2026_09/squeeze_overlap.py`,
   `dashboard/botinfo.py` and `dashboard/market.py`, `strategies/support/{stop_path,
   margin_check,indicators,funding}.py`). Fleet stopped for this step, restarted from the
   new paths after it.
3. Retire the legacy path: `bot.py`, `strategies/orchestrator.py`, `strategies/p300_spec.py`,
   `backtest_runner.py`, `studies/simulation/sim.py` and its DB builder, the
   `TIMING_ANOMALIES` meta-sleeve, the allocation / weight tables, `tests/test_sim_mode.py`
   (the 1.5 GB-per-test copy); set the legacy variant row `p300_aggressive_v2_v1_0` to
   `enabled = 0` with a note. Each bot's `--once --db <copy> --sim-now` dry run is the
   simulator; research replays use the study harnesses.
4. Archive the eight dormant sleeves (EMA_BTC, ETH_DAILY, THU_BEAR, PDO, CPR, FOMC,
   AI_QUANT, chento_limit_bid) under `studies/material/archive/` — git keeps the history;
   any of them returns only through a study and a bot of its own. The pending PDO / CPR
   re-validation and THU_BEAR OOS questions stay open as research items, archiving does not
   answer them.
5. Docs: README, OPERATIONS, PORTFOLIO §2, dashboard cards, calibration-log paths — this is
   where the deferred doc cleanup happens (memory `project-doc-cleanup-planned`).

**Gates:** parity tests byte-equal before and after every step; the full suite green;
fleet restarted from the new paths with fresh heartbeats; one definition per rule (no
duplicated strategy logic, grep-verified); `git status` clean of stray copies.

---

## Step 1 re-planned — stripping the orchestrator interface safely

**Written 2026-09-12** after an audit of the original one-line step. Supersedes step 1
above; steps 2–5 are unchanged.

### Why the original step 1 was unsafe

**Its gate does not work.** Five of the six named parity tests never call
`try_decide_for_variant` / `execute_for_variant` and never pass a `sleeve_cfg` — they
exercise math, loaders and private helpers only. Just `test_carry_exit_rule` touches the
surface being changed, and `tests/test_allocation_parity.py:136-158`, named after the ADX
sleeve, re-states the sleeve's expression inline and asserts on its own copy. An
arbitrarily broken decide/execute path keeps all six green.

**The obvious edit breaks research replay silently.** `try_fire_for_variant` is not dead:
`backtest_runner.tick_replay_variant` resolves sleeves *only* through
`orchestrator.STRATEGY_DISPATCH` — the `try_fire` wrappers — and never consults
`STRATEGY_TWO_PHASE_DISPATCH`. On a miss it does `if dispatcher is None: continue` with no
log (`backtest_runner.py:257-259`), so deleting a wrapper turns every historical replay of
that sleeve into a zero-trade run that still prints a full report and exits 0. The
orchestrator is the opposite — it prefers two-phase and warns on a miss.

**And a broken sleeve import does not stop a bot.** Every runner catches it, writes
heartbeat `status='error'` and keeps ticking while evaluating nothing and — the part that
matters — **sweeping and closing nothing**. That would strand SJ-4242 (CARRY, open since
2026-07-22), SJ-4247 (ADX) and SJ-4250 (SQUEEZE_BULL).

### The approach: expand-contract, never a red commit boundary

The plain-argument `decide()` / `execute()` becomes the *real* implementation inside each
module; the old `(variant, sleeve_cfg)` entry points shrink to three-line cfg-unpacking
adapters; nothing is deleted until nothing references it. No running bot's call path
changes until a deliberate, one-bot-at-a-time repoint. Two of three independent judges
preferred this over characterize-first and over retiring the consumers first; the safety
net from characterize-first is grafted in as phase B, which is what makes the strip
gateable at all.

### Preconditions

- Record the baseline: full suite count, `python health.py` exit code, and a read-only
  snapshot of SJ-4242 / SJ-4247 / SJ-4250. Note that SJ-4250 carries a real scheduled exit
  and will legitimately close during this work; the other two carry the 2099-12-31 sentinel
  and must stay open unless their own sleeve rule closes them.
- **Copy prod.db with `sqlite3 .backup`, never `shutil.copy`** — eight processes write every
  60 s and prod.db is in WAL, so a plain copy without its `-wal` is not a consistent
  snapshot. **A fresh copy for every dry run**: the per-bar idempotency keys shipped
  2026-09-12 mean a second `--once --db <same copy>` writes no row, which would make a
  before/after diff pass on two empty results.
- **Pin one FIRING `--sim-now` anchor per bot** and require the baseline row at that anchor
  to be non-empty. A bot whose baseline is empty is *blocked*, not passing — short_squeeze
  has never fired, R4's next enabled window is 2026-10-02, and chento fires ~36×/yr, so an
  unpinned anchor compares nothing to nothing.
- Fix the target signatures in writing first, since the surface adapter encodes them.
  They must include `use_stop` — **short_squeeze and squeeze_bull each drive TWO live
  variants** (the no-stop twins) through one `tick_all` loop, so `use_stop` (and
  `count_diag` for short_squeeze) is threaded per variant into `decide()`. Getting that
  wrong silently flips the sizing branch and the `_stop_price` written into the reason blob.
- If `P300_STOP_SEMANTICS` cannot be read from the live process environments, **assume
  margin semantics are live** and keep ADX's leverage argument.
- Leave `p300_aggressive_v2_v1_0.enabled = 1`. `tests/test_sim_mode.py` copies prod.db
  verbatim and the sim path selects `enabled = 1`; disabling it silently zeroes that side.
  The disable belongs to step 3.
- Go-ahead needed before phase C (the first live strategy-module edit).

### Phase A — make it safe to work (fleet keeps running) ✅ DONE 2026-09-12

Commits `9d80dd0` (step 1), `4b59b29` (step 2), `80ad875` (step 3). Fleet
untouched: all eight heartbeats stayed `ok` on their original pids. Suite
1366 → 1398, `health.py` exits 0. Two real defects surfaced while doing it —
`--with-fomc` had always produced a run *without* FOMC and reported success,
and the step-3 dry-run gate caught both a leaked diagnostics write and a
NameError I had just introduced in the r4 runner.

1. **Make every dispatch miss loud.** In `backtest_runner.py:257-259` keep the existing
   `STRATEGY_DISPATCH` resolution and replace only `continue` with a `raise` naming the
   `strategy_id`. **Do not "mirror the orchestrator" here** — `STRATEGY_TWO_PHASE_DISPATCH`
   holds `(decide_fn, execute_fn)` tuples and the block one would copy is the
   collect-into-`_pending_intents`-for-reconcile shape, but `tick_replay_variant` has no
   reconcile pass, so a literal mirror collects intents and never executes them: the same
   silent zero-trade replay this step exists to prevent. Add the same validation once in
   `run()`, and **make it honour `SKIP_STRATEGIES` and `params.deterministic is False`** or
   `--skip` and AI_QUANT abort the run. Log the swallowed `except Exception: return None` in
   `timing_anomalies/internal/__init__.py`. New `tests/test_dispatch_registry.py`.
2. **Repoint `health.py`** off the dead composition onto the bot fleet: assert the nine
   `bot_*` variants are registered and that each runner's actual entry points exist. Prove
   it bites by renaming one in a scratch worktree and confirming a non-zero exit.
3. **Port `--db` / `--sim-now`** to the five runners that lack them. Done as
   `botlib.point_at_db_copy` / `add_dry_run_flags` / `apply_dry_run_flags` — one definition,
   six call sites. It redirects three layers, not one: the DB constants, the sleeve-level
   `CHENTO_V3_DIAG_PATH` / `SSQ_DIAG_PATH` env vars (resolved at sleeve import, hence called
   from `main()`), **and** the runner-level `botcfg.DIAG_PATH` / `LOGS_DIR` that squeeze_bull
   and r4 append to directly — the env redirect alone left a live JSONL being written, which
   the gate caught.

### Phase B — build a net that can actually fail (fleet keeps running) ✅ DONE 2026-09-12

Commits `6f9c907` (scaffolding + r4), `756e91f` (carry, squeeze_bull),
`65bae70` (adx), `5bbc906` (chento both assets, short_squeeze). **68 golden
documents across all six modules; the drill catches 14 of 14 mutations.**
Suite 1398 → 1464, fleet untouched.

Run it before and after every phase-C commit:

    python tests/fixtures/build_sleeve_fixtures.py     # verify fixture hashes
    python tests/fixtures/mutation_drill.py            # must be 14/14, tree clean

The drill found **six goldens that were decorative**, and the pattern held
every time: the arithmetic was covered, the *calibrated gates* were not —
R4's weights fallback, squeeze_bull's bull-regime gate and −2 % flush
threshold, ADX's symmetric trend filter and funding veto, short_squeeze's
macro gate. Each would have let a strip delete a calibrated rule with a green
suite. The load-bearing one: **hardcoding ADX's `leverage = 1.0`, the exact
edit the strip makes, now fails two goldens** including the round-trip through
`stop_path` that SJ-4247's live close path uses.

Three scaffolding defects worth remembering, all found by running rather than
reading: the surface adapter defaulted `weight_pct=100.0` for every sleeve,
which injects `_effective_weight_pct` and **silently overrode R4's
bear-regime kill switch** (its bot passes no weight at all, so the fallback
arm is the live path — hence the `AS_BOT` sentinel); `reset_module_state` for
chento targeted the package rather than `chento_triple_v3.signal`, so it
reset nothing; and the normalizer could not serialize numpy scalars.

4. Golden scaffolding: a live-DB kill-switch, a fixture builder with hash-pinned bars, an
   output normalizer, and a surface adapter (`tests/_sleeve_surface.py`) that is the single
   place the call shape is written down — this is what lets the goldens survive the
   signature change they are guarding. The diag-env half of the guard must be **module-level
   code, not an autouse fixture**: autouse runs after the test module's imports, and the
   sleeves read their diag flags at import.
5. **Goldens tier 1** — r4 (four windows), carry, squeeze_bull. Include a `use_stop=False`
   case so the live no-stop twins are inside the net.
6. **Goldens tier 2** — adx, chento BTC/ETH, short_squeeze, plus the stop-path consumer.
   Build the `P300_STOP_SEMANTICS=margin` ADX case **on the same firing fixture** as the
   default case, or its assertion never runs and the step-7 drill reports a false green.
7. **Mutation drill.** Deliberately break each sleeve and confirm the golden goes red. Any
   mutation that stays green means that golden is decorative — fix the golden, not the rule.
8. Fix the live defect the drill exposes: a raising `decide()` must not be able to skip the
   scheduled-exit close. (The same trap exists in the other five runners via an early
   `stale_mgmt_inputs` return — its own commit, not folded into this refactor.)

### Phase C — shim each module, then repoint each bot (go-ahead required)

9–14. **One module per commit, no restart.** ✅ **DONE 2026-09-12** — commits `cdf7dbf`
(squeeze_bull), `2890ebc` (carry), `3f18770` (chento), `0f7c3f0` (short_squeeze), `e1b286b`
(adx), `cb13cb5` (r4). Every sleeve now has a plain-keyword `decide()`/`execute()` as its
real implementation with the old entry points as pure-passthrough adapters; both surfaces
stay live. `tests/_sleeve_surface.py` no longer builds a `sleeve_cfg` for anything — the
only file under tests/ that knew the old shape has forgotten it.

**Every commit shows zero `tests/goldens/` churn**, which is the mechanical form of the
gate: a strip commit that touched a golden would be changing behaviour, not shape. Drill
14 → 23 mutations, still 0 missed. Suite 1464 → 1529. `tests/test_sleeve_adapter_equivalence.py`
(65 tests) pins the unpacking itself, since that is the one place the two surfaces could
diverge, and it enforces that each sleeve's defaults stay its OWN — `weight_pct` falls back
to 0.0, not 100.0, and ADX's `stop_loss_pct` to 10.0, not 0.0.

Three sleeve-specific facts that the shims had to preserve and now have tests:
**R4's `weight_pct=None` means ABSENT, not zero** — the bot passes no weight, so the
fallback arms (weights table incl. the bear-regime zero, gated inner leverage, vol leverage)
are the live path, and substituting 0.0 would disable the regime kill switch invisibly until
October. Its gate arm is literally `R4_INNER_LEV_UNGATED * gate.leverage_mult` — UNGATED,
which reads like a typo and is not. **ADX's leverage is load-bearing**, feeding
`effective_price_move_sl_pct` → the persisted `sl_semantic_price_thresh_pct` → `stop_path`
on SJ-4247's live close path; inert at the default semantic, which is what made hardcoding
it look safe. **`_r4_execute` keeps its private name**, because `bots/r4/runner.py` calls it
by exactly that.

15–20. **Repoint the runners, one bot per commit, one restart each.** ✅ **DONE 2026-09-12**
— commits `f78624c` (squeeze_bull), `db44055` (carry), `d6da44a` (chento, both units),
`f3b1b92` (short_squeeze), `60d5a7c` (adx + r4). **`grep sleeve_cfg bots/*/runner.py` now
returns nothing**: the fleet no longer speaks the orchestrator's dict.

Gate per bot, via `tests/fixtures/repoint_baseline.py`: a `--once --db <fresh copy>
--sim-now <firing anchor>` run before and after, requiring a **byte-identical and non-empty**
trade row, then a fresh `ok` heartbeat. Every one passed. The non-empty half earned its keep
on chento ETH — the BTC anchor produces no ETH fire, so its gate would have been
nothing-vs-nothing; its own anchor (2026-08-02T00:00:05Z) fires **SHORT** where BTC fires
LONG, proving a genuinely distinct path.

All seven bots restarted, each gated individually. The three open positions — SJ-4242,
SJ-4247, SJ-4250 — are tracked across every restart, and the running processes finally carry
the whole day's work, having started nine commits behind.

What kept catching things was `test_entry_points_match_what_the_runners_actually_call` (phase
A step 2): it went red on the very first repoint because `health.BOT_ENTRYPOINTS` still named
the legacy entry points, and again on each subsequent one. Roughly a dozen test stubs also
had to be repointed — each was a `monkeypatch` naming an old entry point, or a fake whose
`(v, cfg)` signature no longer matched the keyword call. Those are the only test edits in the
phase; **no golden changed in any of the six commits.**

### Phase D — contract ✅ DONE 2026-09-13

Commits `cd0d787` (21), `2138481` (22), `fea2975` (23).

21. **Centralized the cfg→kwargs translation** in `strategies/support/cfg_adapter.py`, on
    the orchestrator's side of the boundary — the cfg dict is its vocabulary, not the
    sleeves'. `STRATEGY_DISPATCH` / `STRATEGY_TWO_PHASE_DISPATCH` are now built from
    closures over each sleeve's plain `decide()`/`execute()`. The unpackers are written out
    one per sleeve rather than driven from a table, because their differences are the point.
    `fire_entry` takes `merge_status` because the old wrappers did **not** agree on their
    return shape — only an orchestrator log line reads it, but reproducing it kept the
    switch a true no-op.
22. **Repointed the last callers.** Exactly one was production: r4's four resolvers in
    `timing_anomalies/internal/__init__.py`. `test_jplus_live`'s shim was rebuilt from the
    plain deciders so its 28 call sites survived the deletion untouched.
23. **Deleted ~400 lines** of adapters and wrappers. `tests/test_orchestrator_interface_gone.py`
    replaces the phase-C equivalence file and asserts the inverse: legacy names absent, plain
    surface present, no runner carrying a `sleeve_cfg`, no migrated sleeve reading an
    `_effective_*` key. README's `--with-fomc` row and its "a sleeve that fires under one
    fires identically under the other" claim are corrected.

**Two things worth remembering.** The first deletion pass took carry's `decide()` and
`execute()` with it — the regex ran to end-of-file and carry is the one sleeve whose legacy
block sat *before* its implementation; caught by a char count 4× the others. And the drill
found a real hole rather than a stale one: with the per-sleeve equivalence tests
self-skipping, the remaining coverage checked that a flag KEY was present but never that its
VALUE was forwarded, so a translation that always returned `use_stop=True` passed. Fixed
with explicit value assertions.

Final: suite 1578 passed / 54 skipped (the equivalence parametrisations retiring
themselves), drill 22 mutations 0 missed, `health.py` exits 0, fleet untouched.

### What this does not do

No module moves (that is step 2 and it needs the fleet stopped); `sweep()` is not split out
of `decide()`, because the sweep's closes change what the entry check sees — so the
three-function target is two-thirds delivered; `priority` survives as a plain keyword until
the orchestrator goes in step 3; chento's asset stays import-time; nothing is retired.

### Residual risks

The goldens enshrine today's behaviour, bugs included — characterization tests cannot tell a
correct rule from a wrong one. The mutation drill proves the net is not vacuous, not that it
is complete. **R4 has a ~20-day blind window**: it has never opened a trade and its next
enabled fire is 2026-10-02, so a regression has no live output to diff until October. And a
passing short_squeeze golden is evidence the strip preserved behaviour, not evidence the
sleeve works — it has never fired in production and is pending a retirement decision.

---

## Execution layer and pending re-cuts from the 2026-09 execution / sizing studies

**Captured:** 2026-09-12. **Status:** open — the paper-side changes shipped
the same day (cost constants, CARRY exit, two no-stop paper variants; see
each `docs/calibration/*.md`); these are the parts that wait on something.

1. **Live execution layer requirements** (pool plan Phase F, item F-EXEC):
   taker entries, resting reduce-only take-profit limits, exchange-resident
   stop-market orders instead of 60 s polling, and a fills record with
   intended vs realised price per leg. Source:
   [studies/notebooks/execution_2026_09/findings.md](studies/notebooks/execution_2026_09/findings.md).
2. **E7 live quoting probe** (designed in that study's README, not run):
   needs an API key with trade permission, ≤ $50, two weeks, explicit
   go-ahead. Only worth running if maker entries are wanted — the on-disk
   answer is that they are worth ~3 bp at near-certain fills.
3. **Re-cuts fixed in advance**: SQUEEZE_BULL and SHORT_SQUEEZE stop vs
   no-stop variants at 20 and 30 paired fires
   ([docs/calibration/squeeze_bull.md](docs/calibration/squeeze_bull.md),
   [docs/calibration/short_squeeze.md](docs/calibration/short_squeeze.md)).
   A re-cut script that replays both policies over the union of live fires
   with each sleeve's own walk does not exist yet; write it before n = 20.
4. **ADX + CARRY in one cross-margin account** (pool plan D8) — a design
   constraint for the restructure, nothing to do now.
5. **Research harness hygiene**: `adx_study/harness.run(with_funding=True)`
   exists now; any harness-vs-live comparison must use it, and the other
   research replays (chento overlay, squeeze) should charge the measured
   per-leg costs from `execution_2026_09` rather than their coded lumps
   when next re-run.

---

## Consolidate timing-anomaly sleeves under a single bucket ✅

**Captured:** 2026-05-18.
**Status:** ✅ shipped 2026-05-18 (three commits: meta-sleeve scaffolding,
p300_spec cutover, physical relocation under
[strategies/sleeves/timing_anomalies/internal/](strategies/sleeves/timing_anomalies/internal/)).

The 8 calendar/clock substrategies (FOMC, THU_BEAR, PDO_L_RF, CPR,
R4_BTC, R4_ETH, R4_BTC_V2, R4_ETH_V2) now dispatch only via
`TIMING_ANOMALIES` — no orchestrator-level shim. EMA_BTC and ETH_DAILY
stayed at the top level (EMA explicitly so it can serve as a regime
gate; ETH_DAILY because it's continuous, not date-driven). See
`project_timing_anomalies_sleeve` in Claude Code's auto-memory for the
operating contract.

Original motivation (kept for context):
The existing sleeves (FOMC, R4, EMA crossover, PDO, CPR, day-of-week
windows) are all *statistical timing edges* — positive expectancy in
specific calendar/clock windows with no microstructure mechanism. A
portfolio of statistical timing edges is fragile to regime change; a
portfolio of mechanistically-grounded setups is more defensible. Future
research effort biases toward microstructure (short-squeeze etc.). The
single timing bucket shares one allocation budget instead of competing
for capital and config attention.

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
  dataclass + `GATE_REGISTRY`. All three originally-scoped gates
  registered:*
    - *R4 vol-gate — wraps `today_inputs()['gated']`. Modulator
      (`leverage_mult` ∈ {0.4, 1.0}, `fire=True` always). Registered
      for the four R4 sleeves; each consumes `_effective_gate.leverage_mult`
      directly.*
    - *V4 event filter — wraps `_v4_passes`. Binary block (`fire=False`
      for OPEX-adjacent or no-event-adjacency Thursdays). Registered
      for S-096; THU_BEAR consumes `_effective_gate.fire`.*
    - *FOMC composite — reads the `fomc_observer` table for the
      cached `evaluate()` result on the next FOMC date. Cheap on
      non-FOMC ticks (calendar short-circuit). The FOMC sleeve does
      not yet consume the gate — its own Phase 1/2 decision logic
      stays inline — but operator dashboards / `strategy_health`'s
      cross-sleeve snapshot can now read the same decision via the
      gate, so the registered surface is uniform across all three
      gates.*
  *Walk-forward CV protocol for new gates — shipped 2026-05-16 in
  [GATE_VALIDATION.md](GATE_VALIDATION.md). Five-step protocol:
  signature → pre-register parameter grid → walk-forward fold
  structure → metrics (expectancy / Sharpe / hit-rate, deflated by
  search budget) → promotion criteria (Sharpe uplift ≥ 0.2 deflated,
  per-fold sign stability ≥ 2/3). Documents the in-sample status of
  the three existing gates (R4 vol-gate validated-ish, V4 + FOMC
  composite both in-sample by construction) and the artifacts every
  new gate's PR must include (study notebook under
  studies/notebooks/gates/, docstring uplift figure, BACKLOG entry).
  Linked from strategies/support/gating.py module docstring.*
- **P2.4c** — Portfolio vol-target: replace the J+-only vol-target with
  a portfolio-level scalar applied to every sleeve's notional.
  *2026-05-15: math shipped + opt-in switch.
  `strategies/support/portfolio_vol.py:compute_portfolio_vol_scalar`
  reads the variant's realized NAV from the trades ledger (rolling
  30-day window, 10-obs minimum) and returns
  ``target_vol_annual / realized_vol`` clamped to ``[0.5, 3.0]``.
  Default target 30% annualized — matches the J+ family's regime
  caps. The orchestrator's `current_vol_scalar(strategy_id, variant)`
  reads the variant's ``spec.allocator_notes.use_portfolio_vol``
  flag: when True it returns the portfolio scalar for EVERY sleeve;
  when False (default) it returns legacy J+-only scalar / None for
  tactical. The opt-in flag lets the operator activate the new math
  on a paper-trading variant without disturbing the live variant; one
  paper week of J+ behaviour under the new scalar before extending to
  tactical. Tactical-sleeve consumption of `_effective_vol_scalar`
  (i.e. `leverage *= scalar`) is the remaining piece — J+ already
  reads the field. 29 tests (21 legacy + 8 new math/opt-in).*
  *2026-05-16: tactical consumption shipped. Done at orchestrator-
  injection time (NOT per-sleeve): `_tick_composition` multiplies
  `_effective_leverage` by `_effective_vol_scalar` when the scalar
  is non-None and the sleeve isn't in the J+ family. Tactical sleeves
  (ADX, CARRY, THU_BEAR, PDO, CPR, FOMC, AI_QUANT) consume
  `_effective_leverage` transparently — zero per-sleeve code changes.
  J+ family is bypassed because it reads `_effective_vol_scalar`
  directly as its leverage (replaces `ti["lev"]`) and would
  double-count if the orchestrator also scaled `_effective_leverage`.
  3 new tests in test_portfolio_vol.py: tactical scaling, J+ bypass,
  scalar-None no-op.*
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
  (b) Proportional-reduce policy shipped 2026-05-15:
  `margin_headroom.clamp_to_headroom(variant, candidate, min_reduce_fraction=0.5)`
  returns `(clamped, status, reason)` with status in
  ``{full, reduced, too_small, no_headroom}``. AI_QUANT is the first
  sleeve to consume it — at the fresh-open path, a partial-headroom
  case (>=50% of intended candidate fits) opens at the reduced size
  rather than skipping; below the floor it still skips. Other sleeves
  continue using `can_open` (skip-policy); the operator opts each in
  by replacing `can_open` with `clamp_to_headroom` per sleeve. AI_QUANT
  was the natural pilot because its conviction scaling already varies
  size trade-by-trade.
  (c) Explicit sleeve priority shipped 2026-05-16: each composition
  entry may set `priority` (float; lower = higher priority — first
  crack at margin pool / conflict slot). Orchestrator + backtest_runner
  stable-sort the composition list by priority at the top of
  `_tick_composition` / `tick_replay_variant`. Default priority is 100
  for entries without an explicit field, so stable sort preserves
  today's registration order. 5 tests in
  tests/test_orchestrator_priority.py anchor the sort: unset preserves
  registration order; lower-priority entries dispatch earlier; mixed
  explicit / default sorts correctly; ties preserve input order; float
  priorities supported for fine-grained tie-breakers.
  The scale-adjustment check (P2.4d (d) in the earlier note)
  shipped 2026-05-15: EMA_BTC and ETH_DAILY's daily-rebalance CASE 4
  now checks `can_open` against `(desired_qty - cur_qty) * price`
  before calling `trades.apply_scale`; scale-DOWN is always allowed,
  scale-UP yields with `actions.append("scale_up_margin_constrained")`
  when the delta would push the variant over cap. Total qty stays at
  cur_qty for that tick; the next daily rebalance will retry.*
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

  *2026-05-16 — Two-phase dispatch scaffold shipped.
  `strategies/support/dispatch.py` ships the `Intent` dataclass
  (frozen; asset / direction / allocation_pct / leverage / conviction /
  priority / reason / scheduled_exit_dt) and the documented
  two-phase contract — sleeves implement
  ``try_decide_for_variant(variant, sleeve_cfg) -> Intent | None``
  alongside the existing ``try_fire_for_variant``, and the
  orchestrator's reconcile pass collects intents across sleeves
  before any of them open. No sleeve has migrated yet — AI_QUANT
  is the natural pilot (its LLM decision is already separate from
  the trade open) and other sleeves follow as time permits. Migration
  is incremental: the orchestrator falls back to legacy
  ``try_fire_for_variant`` when a sleeve doesn't expose decide().*

  *2026-05-16 — `reconcile_intents()` pure function shipped.
  Takes a list of ``(strategy_id, Intent)`` collected from migrated
  sleeves + the variant's current gross / cap / capital. Returns a
  parallel list of `ReconcileResult` (approved / approved_reduced /
  rejected_directional_conflict / rejected_margin). Pass logic:
  sort by (priority, -conviction), then for each intent: directional
  conflict against earlier-approved on the same asset; margin headroom
  with reduce policy (50% floor); CARRY's neutral SHORT excluded from
  conflict checks. The function is pure — no DB writes — so it's
  fully unit-testable. 11 tests cover empty input, single approve,
  priority order, conviction tie-break, margin reject / reduce / floor,
  subsequent-intent consumption, CARRY neutral, FLAT passthrough,
  concordant stack approval. The orchestrator routing (call decide()
  on migrated sleeves, run reconcile_intents, call execute() on
  approved intents) is the next sub-commit; depends on AI_QUANT's
  decide()/execute() implementation.*

  *2026-05-16 — `register_p300.py` retired. Moved `build_spec` +
  idempotent `register` to `strategies/p300_spec.py`. bot.py and
  studies/simulation/sim.py call `p300_spec.register(quiet=True)` on
  startup — auto-registers on first run, no-op when the variant row
  already exists. Operator workflow simplifies from
  `bootstrap.py → register_p300.py → bot.py` to `bootstrap.py → bot.py`.
  test_ai_quant_e2e.py updated to import from new location;
  test_allocation_parity.py comment references still point at the
  module (now p300_spec). Text references in health.py, bootstrap.py,
  backtest_runner.py, README, OPERATIONS, PORTFOLIO updated. The
  `weight_pct` field in each composition entry stays (parity tests
  + operator docs read it) but is informational — the live allocator
  is `strategies.support.allocation` (P2.4a).*

  *2026-05-16 — R4 family migrated to two-phase. ALL 13 dispatched
  sleeves now on two-phase. R4_BTC / R4_ETH / R4_BTC_V2 / R4_ETH_V2:
  pure calendar-bounded entries, no maintenance side-effects (scheduled
  exit via `orchestrator._close_due_paper_trades`). Shared `_r4_decide`
  + `_r4_execute` helpers; per-variant decide functions handle the
  calendar / weekday / hour gates and delegate to the shared decide.
  Inline margin_headroom.can_open REMOVED — reconcile owns it across
  all 4 R4 sleeves. Single registration loop in orchestrator
  iterates all 4 R4 (sid, decide_fn) pairs. Registration test extended
  to all 4 JPLUS_R4_* sleeves.*

  *2026-05-16 — EMA_BTC (JPLUS_EMA_BTC) migrated to two-phase. Ninth
  sleeve. Adds the FLIP case to the J+ pattern: weekly EMA cross
  rotates the existing position's direction via atomic `apply_flip`
  (qty-preserving, no gross-notional growth). FLIP stays as a
  side-effect in decide() — reconcile only sees fresh opens. The
  signal (weekly cross) IS the triggered entry/exit pair: the old
  direction's exit signal is the same cross that triggers the new
  direction's entry. Inline margin_headroom.can_open on fresh-open
  removed; CASE 4 scale-up keeps inline margin (existing-position
  size adjustment, not a fresh open).*

  *2026-05-16 — ETH_DAILY (JPLUS_ETH_DAILY) migrated to two-phase.
  Eighth sleeve. First J+ family migration. User insight: there's no
  such thing as a "continuous sleeve" — every position has a triggered
  entry and a triggered exit. The position can hold for months, but
  something started it. Under that framing, J+ sleeves fit the same
  two-phase pattern as tactical: CASE 2 (regime turning bullish from
  flat) is the entry → Intent. CASE 3 (regime exit) and CASE 4 (daily
  size rebalance) are side-effects in decide() — they don't re-decide
  whether the position should exist, they just maintain its size or
  close it on the exit signal. Inline margin_headroom.can_open on
  fresh-open removed — reconcile owns it. CASE 4 scale-up keeps its
  inline margin check because reconcile only sees fresh opens, not
  size adjustments. Tests anchor the entry / close / rebalance paths
  unchanged. Registration test extended to JPLUS_ETH_DAILY.*

  *2026-05-16 — CARRY (S-078) migrated to two-phase. Seventh sleeve.
  All tactical sleeves now on two-phase. Side-effect in decide(): exit
  sweep on 3-day negative-funding-streak. Entry path emits a single
  Intent on entry conditions (no open, entry_ok, no exit_trigger,
  daily idempotency clear). Inline margin_headroom.can_open removed
  — reconcile owns it. CARRY's perp leg is in
  `dispatch._NEUTRAL_STRATEGIES`, so reconcile exempts it from
  directional-conflict checks (the SHORT is delta-neutral collateral,
  not a directional bet) while still enforcing margin headroom (the
  perp notional consumes real gross budget). Registration test
  extended to S-078.*

  *2026-05-16 — FOMC migrated to two-phase. Sixth sleeve. Single-asset
  BTC; side-effect in decide() is the stuck-open self-sweep (closes
  any trade past its scheduled exit_time, defense-in-depth against a
  missed close_due_for_variant pass). Entry path emits at most one
  Intent on FOMC ticks in [target_entry, target_exit) when the observer
  decision is `trade` and no trade exists for this fomc_date.
  `_record_entry_price` (observer-table audit write) moved to execute()
  so it only fires on the approved Intent. Inline margin_headroom.can_open
  REMOVED — reconcile owns it (matters at FOMC's 10× leverage). 
  Registration test extended to FOMC.*

  *2026-05-16 — CPR migrated to two-phase. Fifth sleeve. Same pattern
  as PDO: side-effects in decide() are stop/target/time-stop closes;
  entry path emits per-asset Intents; inline margin-headroom
  `clamp_to_headroom` removed (reconcile owns it). BTC cross-sleeve cap
  stays inline. Registration test extended to CPR.*

  *2026-05-16 — PDO-L-RF migrated to two-phase. Fourth sleeve on
  two-phase. Side-effects in decide(): hold-window exits per asset
  (BTC=24h, ETH=4h). Inline `margin_headroom.clamp_to_headroom` removed
  — reconcile handles partial-fit via `approved_reduced` (same math,
  same cascade behavior across BTC + ETH because reconcile's loop
  tracks `approved_notional` cumulatively). Cross-sleeve BTC LONG cap
  (`risk_caps.btc_long_cap_allows`) stays inline as a pre-Intent gate
  — separate per-asset cap not yet owned by reconcile. Registration
  test extended to PDO-L-RF.*

  *2026-05-16 — Contract bump + THU_BEAR (S-096) migrated to two-phase.
  Third sleeve on two-phase. `try_decide_for_variant` contract bumped
  from `Intent | None` to `list[Intent]` so multi-asset sleeves emit
  one intent per asset on the same tick (BTC + ETH SHORT on Thursday
  00:xx). AI_QUANT and ADX updated to wrap their single Intent in a
  list. Orchestrator's two-phase loop flattens lists; result-to-execute
  mapping switched from dict-by-sleeve-id to per-sleeve FIFO queue
  (reconcile's stable sort preserves input order within a sleeve, so
  pairing is deterministic). THU_BEAR's `try_fire_for_variant` becomes
  a wrapper running decide() then executing each Intent. Side-effects
  inside decide(): SL sweep on every open trade, Friday-EXIT_HOUR
  scheduled close. Inline conflict_resolver + margin_headroom removed
  from THU_BEAR — reconcile owns them. 1 new orchestrator test
  exercises the multi-intent fan-out (BTC + ETH both approved, 2
  execute calls). Registration test extended to S-096.*

  *2026-05-16 — ADX (S-003) migrated to two-phase + reconcile seeded
  from legacy DB opens. Second sleeve on two-phase after AI_QUANT.
  `try_fire_for_variant` becomes a thin wrapper calling
  `try_decide_for_variant` → `execute_for_variant`. Side-effects in
  decide() (always run, not subject to reconcile): SL sweep, exit-signal
  close on ADX < 20, direction-flip close on signal reversal. Returns
  an `Intent` when a fresh signal fires AND open-trade count is zero
  post-flip; otherwise returns `(None, status)`. Inline conflict_resolver
  + margin_headroom opt-ins removed — reconcile owns those now.
  To prevent regression against legacy sleeves whose DB opens land
  BEFORE the reconcile pass, `reconcile_intents` gained an
  `existing_directional_opens: dict[asset, direction]` arg; orchestrator
  builds it via the new
  `conflict_resolver.current_directional_opens(variant_id)` helper.
  Migrated sleeves' intents now collide against legacy DB positions
  uniformly. 3 new dispatch tests + 6 new conflict_resolver tests
  + ADX added to the existing two-phase registration test. Full fast
  suite 789 tests green (was 780; +9 net).*

  *2026-05-16 — Signal pooling (P2.4f Stage 2) shipped in reconcile_intents.
  `_pool_concordant_allocations` runs as the first step of the reconcile
  pass and redistributes allocations among same-(asset, direction) intents
  via conviction-weighted averaging: `cw_avg = Σ(c_i × a_i) / Σ(c_i)`,
  each intent gets `share_i × cw_avg`. Sum invariant: total pooled alloc
  equals cw_avg, not the sum — so two LONG BTC sleeves agreeing on
  direction produce conviction-weighted-avg exposure rather than 2×.
  Each sleeve keeps its own leverage / priority / conviction; only alloc
  changes. Excluded: FLAT direction, CARRY (delta-neutral, same as
  conflict-resolver), singletons. Zero-conviction fallback uses equal
  weighting. New status `approved_pooled` distinguishes pooled approvals
  from plain ``approved``; ``approved_reduced`` still wins the label
  when margin clamp also fires for the same intent. 13 new tests in
  test_dispatch_intent.py cover the redistribution math, exclusions,
  fallbacks, multi-asset bucket independence, and the pool-then-margin
  interaction. Existing concordant test rewritten to assert pooled
  semantics. Total 26 dispatch tests green.*

  *2026-05-16 — AI_QUANT pilot migration + orchestrator routing shipped.
  `strategies/sleeves/ai_quant/signal.py` now exposes
  `try_decide_for_variant(variant, sleeve_cfg) -> (Intent | None, dict)`
  and `execute_for_variant(variant, sleeve_cfg, intent) -> dict`; the
  legacy `try_fire_for_variant` is preserved as a thin wrapper
  (decide → execute) so backtest_runner + any direct callers are
  unchanged. Decide runs every gate (kill switch / defer-aware
  idempotency / entry window / cost cap), builds the context bundle,
  calls Anthropic, and packs the result into an `Intent` (asset /
  direction / allocation_pct / leverage / conviction / priority /
  reason). Execute does a fresh DB read of current_open and calls
  the existing `_reconcile()` to write the trade + journal row.
  Orchestrator adds parallel `STRATEGY_TWO_PHASE_DISPATCH` dict;
  `_load_dispatch` registers AI_QUANT into it when both `decide` +
  `execute` are exported. `_tick_composition` collects pending
  intents from migrated sleeves into a list, runs `reconcile_intents()`
  on the full set, then calls `execute_fn` on each approved /
  approved_reduced result. Legacy sleeves continue using
  `try_fire_for_variant` in the same loop. 4 new integration tests
  in `tests/test_orchestrator_two_phase.py` cover: registry
  registration, decide=None skips execute, approved Intent triggers
  execute with correct args, reconcile-rejected Intent skips execute.
  Full fast suite 768 tests green.*

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

## P2.7 — End-of-restructure doc sweep ✅

**Captured:** 2026-05-14 (memory `project_doc_cleanup_planned`).
**Status:** 2026-05-15 — Stage 1 (path-fix) and Stage 2 (readability)
both shipped across the four operator-facing docs. Each doc got its
own focused commit; structural drift the path-fix script couldn't
salvage (orphaned headings, bad substitutions, the dropped
Core/Tactical 50/50 framing, etc.) was hand-rewritten.

  - README (7a26449): new "What runs live here" 13-row table replaces
    the Core/Tactical prose split; dropped the long retraction of the
    upstream Sharpe number; added cross-sleeve coordination paragraph.
  - OPERATIONS (13a13cb): "Live operation" section rewritten around
    the orchestrator-injected `_effective_*` fields; new
    `margin_constrained` / `directional_conflict` troubleshooting note;
    test-count bump.
  - PORTFOLIO (2a69dce): §1 top-level allocation rewritten as a single
    13-row roster; §3 renamed "Core J+ Family"; brace-expansion paths
    and stranded substitutions cleaned.
  - MANUAL (5260329): CORE_ALLOC_CAP framing clarified; one stale
    `jplus.simulate._cap_core_weights` path fixed.

Audits show 0 stale `services/` / `jplus/` / `variant_engine` /
`SHADOW` / `--mode sim` / `data/trader.db` / `data/dashboard.db` /
`jplus.simulate` references in any of the four docs. The
architecture trees + canonical per-sleeve references match the
post-restructure layout.

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

### Milestone 1 — Decision history (carryover commitments) ✅

**Status:** shipped 2026-05-16. The carryover-commitments loop is
closed: today's call sees its prior `exit_conditions` /
`time_horizon_days` / `confidence_caveats` and a derived `status_now`
(open / closed / expired_horizon / superseded / deferred_active /
deferred_expired). `rationale_md` is excluded by design (anchoring
risk — the model must re-derive the WHY from current data). Window:
last 7 days OR while any decision is still within its declared
`time_horizon_days`. ERROR rows excluded. Cap 7 entries. 8 new tests
cover the status_now matrix + caps + horizon-window retention + the
no-rationale-leak invariant.

**Goal (historical):** today's model sees what its prior self
committed to, so it can honor or rescind those commitments. Avoid
anchoring bias by excluding the prose rationale.

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

#### Phase 2a — instrument the link (one-time) ✅

**Status:** shipped 2026-05-16. The decision↔trade join now exists:
- `trades.ai_quant_decision_id INTEGER` column added in
  `strategies/support/trade_db.py:init_db` (idempotent ALTER on existing
  DBs).
- `execute_for_variant` in `strategies/sleeves/ai_quant/signal.py`
  parses the `trade_action` string (`opened:SJ-X` /
  `flipped:SJ-old->SJ-new`), extracts the spawned trade id, and writes
  the decision id back via a small `_tag_trade_with_decision` helper.
  Failure of the tagging UPDATE is best-effort (logged, not raised) —
  the trade and the journal row are already durable; only the join is
  missing, which the backfill tool can recover.
- One-shot backfill in
  `studies/simulation/backfill_ai_quant_decision_id.py` fuzzy-matches
  legacy rows by `(variant_id, asset, time-within-±2min)`. Dry-run by
  default; `--apply` commits. Idempotent: skips already-linked rows.
- 12 new tests: 4 unit tests on `_spawned_trade_id` parsing, 3
  integration tests on the wiring (opened tag / flipped tag-new-only /
  FLAT noop doesn't tag), 8 tests on the backfill tool (match /
  apply / no-match / ambiguous / cross-variant exclusion / unparseable
  time / idempotency).

**Goal (historical):** the clean version needs a stable join between
`ai_quant_decisions` rows and `trades` rows.

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

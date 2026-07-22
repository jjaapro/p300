# Bot extraction plan — Chento Triple v3 (#1) + Short Squeeze (#2)

> **Created:** 2026-07-21, from a dependency-mapping pass over both sleeves plus a
> data-freshness audit of `prod.db`.
> **Direction:** single-strategy bots, each an independent process with its own
> (future) account, sharing one data platform. Supersedes phases B/D/F of
> [pool_restructure_implementation_plan.md](pool_restructure_implementation_plan.md)
> (pool primitives / pool paper validation / sub-account wiring); absorbs its A2
> (replay cleanup), A4 (calibration logs), A6 (health coverage → monitor). The
> pool plan file is left untouched as a record; this doc is the active plan.
> **Independence = runtime + account + deploy** — NOT git. Both bots import the
> existing sleeve packages in place. Physical file moves and repo splits are
> optional later steps, not prerequisites.
> **Posture:** extract, don't rebuild. Smallest change that produces paper
> evidence again.

---

## Status — implemented 2026-07-21 (same day)

Everything below through M2 shipped and tested the day the plan was written.
As-built deltas from the text below: the platform library is **`botlib.py` at
repo root** (a top-level `platform/` package would shadow the stdlib module);
SS diagnostics are env-gated `SSQ_DIAG` mirroring `CHENTO_V3_DIAG`; prod.db
switched to WAL for multi-process safety.

- **Heal**: done 2026-07-21 morning — klines/funding/LSR/OI continuous through
  now (incl. a targeted fetch for OI's residual 55h hole), okx_perp_1h rebuilt
  2021→now. **The ~07-28 retention deadline is defused.**
- **P0.1–P0.3**: `feed.py` (with hourly OKX refresh wired into
  `refresh_all()`), `botlib.py`, `monitor.py` — live-tested; 7 tests.
- **M1**: `bots/chento_v3/` running; 5 bot tests + 4 research-parity tests.
  Parity found + fixed one real drift (B5 `min_periods`, warmup-only).
  Calibration log started.
- **M2**: `bots/short_squeeze/` running; 5 bot tests + 3 parity tests.
  Live-path percentile approximation quantified: p99 Δ=0.0026, gate flips
  0.000% — negligible. README claim fixed; calibration log started.
- **Operator to-do**: keep `python feed.py` + both runners as always-on
  processes (Task Scheduler), `python monitor.py` hourly.

**2026-07-22 round two** (fleet-hardening plan, commits 2931557..eab273e):
P0 chento live boundary-eval lockout fixed (replay-gate: entries 8/8
byte-identical; `__replay_p0gate` = new shipped-config baseline) · P1
backup.py + Telegram alerts + stranded legacy positions closed + feed
guard · P2 replay archive (51 variants / 28,205 rows out of live) · P3
ADX Tier-2+veto shipped + `bots/adx/` · P4 `bots/carry/` (first tick
opened a delta-neutral position). Fleet = 4 bots. Operator: restart the
chento runner (pre-fix code), start adx/carry runners, schedule
backup.py daily + monitor.py hourly, Telegram tokens in .env.

## 0. Verified ground (2026-07-21) — why Phase 0 is urgent

| # | Finding | Evidence |
|---|---|---|
| G1 | **Feed dead since 2026-06-28 06:30** — every Binance-fed table stops there (23-day hole) | freshness query over prod.db |
| G2 | **LSR + OI have a hard heal deadline ≈ 2026-07-28.** Binance serves only ~30d of `ca_long_short_ratio` / `cd_open_interest` history (`binance.py:260-268`, `:847-849`). Restarting the feed before ~07-28 fully heals the hole; after that it is permanent. LSR feeds Chento B5; OI feeds Short Squeeze's Asia gate | binance.py comments + table max-ts |
| G3 | **Chento was gate-locked its entire paper life.** `okx_perp_1h` has NO live writer (absent from `refresh_all()` and from `check_gaps.py`; only manual backfills — last row 2026-05-26). Stale table → `okx_delta_z=NaN` → `okx_aligned()` False (`math.py:453`) → Filter 3 blocks every candidate (`signal.py:330-333`), and `FILTER_OKX_ALIGNED=True` in prod config. Chento's zero live trades are a plumbing defect, not market conditions | dependency map + config.py:62 |
| G4 | Chento live diagnostics were never on — diag JSONL contains only backtest-era runs (last write 06-04), so G3 was invisible | data/diagnostics/chento_v3_diag.jsonl |
| G5 | Short Squeeze is dispatch-registered but **not in the P-300 composition** — never paper-traded anywhere | p300_spec.py grep; PORTFOLIO.md:373 |
| G6 | Short Squeeze exit is a **6h time-stop** in code (`config.TIME_STOP_HOURS=6`; enforced `signal.py:299-309`). README correct; **PORTFOLIO.md:111 "session-end" is wrong** | dependency map |
| G7 | **Neither sleeve's signal path has a parity test.** Chento: no test file at all (math parity targets live in `studies/notebooks/chento_journal/validation_*.py`). Short Squeeze: tests cover `math.py` on synthetic data only; `signal.py` untested; docstring overstates coverage | dependency maps |
| G8 | Short Squeeze README data claim wrong: says CVD split "added 2026-05-18" (`README.md:79-80`) — real taker buy/sell exists since 2019 and its own backtest starts 2022 | dependency map |

Lessons G1–G4 encode the two structural requirements of this plan: **every table a
bot reads must have a live writer with a freshness contract**, and **gate
diagnostics + heartbeats must run in live** so a 100%-blocking gate is a visible
alert, not a filtered log line.

---

## Phase 0 — Platform (shared; prerequisite for both bots; run P0.1 immediately)

### P0.1 Standalone feed daemon — `feed.py` (repo root)
The feed leaves bot.py and becomes its own always-on process. Two+ bots must
never each run fetchers (double-writes, rate limits).

- Extract the loop from `bot.py:_feed_thread` / `_run_live_loop`: startup
  `binance.fix_all_gaps()`, then `binance.refresh_all()` every 60s, same noise
  filtering, SIGINT-clean.
- **Add OKX to the live cycle** (fixes G3 structurally): new
  `okx_perp.refresh_latest()` (incremental from `MAX(timestamp)`, same upsert as
  backfill) called from `refresh_all()` with an hourly throttle (same pattern as
  `_refresh_daily_external`). Backfill the 05-26→now hole once (OKX serves deep
  history). Optional: same for `bybit_perp_1h` (no sleeve reads it live today —
  decide when something needs it).
- Add `okx_perp_1h` to `data/check_gaps.py` expected-cadence spec (3600s).
- LSR warmup note: after healing, B5 needs its 30d percentile window — the first
  refresh pulls the full retention window, so B5 is whole again immediately.
- **Run this the day it exists** (G2 deadline). Interim if the daemon isn't ready
  same-day: `python bot.py --once` does NOT run the feed; a one-shot
  `python -c "from data.sources import binance; binance.fix_all_gaps(); binance.refresh_all()"`
  heals klines/funding/LSR/OI today. OKX hole via `okx_perp.py` CLI backfill.

### P0.2 Heartbeat convention — `bot_heartbeats` table (prod.db)
Each bot (and the feed daemon) upserts one row per tick:
`(name, last_tick_utc, last_eval_utc, last_signal_utc, open_trades, status, note)`.
`status` ∈ {ok, degraded, error}; `degraded` covers e.g. stale-input refusal (M1.2).
~30 lines in a new `platform/botlib.py` (also home of the shared runner helpers below).

### P0.3 Read-only monitor — `monitor.py` (repo root, Task-Scheduler hourly)
Checks and prints/exit-codes, never trades:
1. **Table freshness vs contract:** cd_futures_15m/cd_spot_15m ≤ 30min;
   btc_1m ≤ 10min; okx_perp_1h ≤ 3h; cd_open_interest ≤ 2h;
   ca_long_short_ratio ≤ 26h; cd_funding_rate ≤ 9h; cd_futures_ohlcv ≤ 2h.
2. **Heartbeats:** any bot/feed row older than 3× its tick interval → alert.
3. **Silence vs expectation:** per bot, days since last signal *evaluation* and
   last trade vs the bot's configured expected cadence (Chento: evals every 15m,
   trades ~8/yr; SS: evals every 15m in London/NY, trade cadence from V2.1 below).
4. **Open-trade sanity:** open rows older than their TIF/time-stop → alert
   (backstop-of-the-backstop).
This monitor would have caught G1 (day one), G3 (freshness), G4 (silence), and
the original five-week idle incident.

*Gate G-P0: all tables inside contract; monitor green; feed daemon running as a
scheduled/auto-restarting process.*

---

## Phase 1 — Bot #1: `bots/chento_v3/`

Sleeve facts the design leans on (from the dependency map): `math.py` is pure
numpy/pandas; `signal.py` needs only `clock`, `db`, `dispatch.Intent`,
`price_feed`, `trades.{open_paper_trade,close_perp_trade}`; exits
(5×ATR stop / 6R target / 72h TIF) are fully self-enforced by the sleeve's
bar-walking sweep against real 15m OHLC, with trade state in `trades.notes`
JSON; the sleeve consumes only `_effective_weight_pct`, `_effective_leverage`,
`priority` from injected config; decision-time network calls: none.

### M1.1 Runner — `bots/chento_v3/runner.py` (+ `config.py`)
- Own variant row: `bot_chento_v3_v1` (kind `single_bot`, status `paper`,
  own `capital_usdt` from bot config). Registered idempotently at startup
  (mini version of the p300_spec pattern; no composition — one strategy).
- Loop every 60s (cheap; sleeve self-gates entry evals to 15m boundaries):
  1. heartbeat tick;
  2. `try_decide_for_variant(variant, sleeve_cfg)` — sweep side-effects run here
     (stop/target/TIF closes);
  3. if Intent: apply the bot's sizing policy (below), then
     `execute_for_variant`;
  4. scheduled-exit backstop: close any open row past `exit_time` (≈20 lines,
     ports `orchestrator._close_due_paper_trades` semantics for this variant only).
- `CHENTO_V3_DIAG=1` always on in live; diag path under `bots/chento_v3/logs/`.
  (G4 fix — gate-kill counters become inspectable per day.)
- No reconcile, no pooling, no margin sim, no allocation table, no regime
  injection. Liquidation sweep intentionally dropped: at the sizing below,
  paper liquidation is unreachable; revisit at go-live with real account margin.

### M1.2 Stale-input refusal (G3 fix, bot-side belt to P0's suspenders)
Before each eval: if `okx_perp_1h` max-ts > 3h old, or `ca_long_short_ratio`
max-ts > 26h, or `cd_futures_15m` > 45min — skip the eval, set heartbeat
`degraded` with the offending table. A gate that silently blocks becomes a
loud refusal.

### M1.3 Sizing — fixed-R, no sleeve change
The Intent's `reason` dict carries the computed stop; runner derives
`stop_pct = |entry − stop| / entry` and sizes
`notional = capital × RISK_PCT / stop_pct` (default **RISK_PCT = 2%**, the
Premier-tier draft), capped at `NOTIONAL_MAX_X = 3×` capital. Rebuild the frozen
Intent via `dataclasses.replace(intent, allocation_pct=100.0,
leverage=notional/capital)` so `open_paper_trade`'s
`size = capital × alloc/100 × lev` lands exactly on the target notional.
R-space evidence (mean R, MAR) is sizing-independent, so comparability with the
backtest is preserved. Config-switchable back to legacy `weight×lev` if wanted.

### M1.4 Verification (definition of done)
1. **Parity test** (G7 fix): `tests/bots/test_chento_parity.py` — adapt the
   `chento_journal/validation_*.py` targets: assert B1/B5/B7/ATR/OKX-z feature
   values from the sleeve loaders at ≥5 known historical timestamps equal the
   research values (byte-equivalence rule).
2. **Forced-fire integration test:** temp DB, synthetic bar path → one full
   open → stop/target/TIF close cycle through the runner (not the orchestrator).
3. **Live liveness:** heartbeat green; diag JSONL advancing daily with non-zero
   `bars_at_boundary`; monitor green ≥ 3 consecutive days.
4. Natural cadence is ~8 trades/yr — "running & evaluating correctly" is the
   DoD, not "trade this week". Diag counters are the aliveness proof.
- Calibration log started: `docs/calibration/chento_triple_v3.md` (absorbs A4),
  first entry = this extraction + sizing policy.

---

## Phase 2 — Bot #2: `bots/short_squeeze/`

Sleeve facts: reads `cd_futures_15m`+`cd_spot_15m` (perp/spot CVD divergence),
`cd_futures_ohlcv`+`cd_open_interest`+`cd_funding_rate` (Asia macro gate),
`btc_1m` via price_feed; exits = 10bp-below-swept-low stop, 3R target, 6h
time-stop, enforced in the sleeve's sweep **against current price** (not
bar-walked) → the 60s runner cadence matters for exit fidelity; entry evals
self-gate to 15m boundaries in London/NY sessions; LONG-only; conviction
hardcoded 100; costs 10bp + 15bp slippage, funding applied.

### M2.1 Validation debt first (it has never traded — G5, G7, G8)
1. **Byte-equivalence spot-check:** new
   `tests/bots/test_short_squeeze_parity.py` — recompute perp-CVD percentile,
   divergence percentile, close-in-range, and the Asia summary via the sleeve
   loaders at ~10 trigger timestamps taken from
   `studies/notebooks/short_squeeze_sessions/strategy_backtest.ipynb`, assert
   equality with notebook values against historical prod.db rows. Blocking:
   the sleeve does not go live until this is green.
2. **Extract expected fire-rate** from the notebook (N triggers / period) →
   monitor expectation (alert if silent > 4× median trigger gap).
3. **Fix `README.md:79-80`** (CVD availability claim — G8). Note
   PORTFOLIO.md:111 ("session-end") for the doc sweep; don't fix piecemeal.
4. `is_long_macro` stays computed-but-unused — recorded as future symmetric-
   detector research (per adx_study Addendum 2), NOT wired in this extraction.

### M2.2 Runner — same skeleton as M1.1 via `platform/botlib.py`
- Variant `bot_short_squeeze_v1`; 60s loop (exit sweep fidelity); heartbeat;
  stale-input refusal on cd_futures_15m/cd_spot_15m (45min), cd_open_interest
  (2h), cd_funding_rate (9h), cd_futures_ohlcv (2h); scheduled-exit backstop.
- Diag: add a per-day gate-counter JSONL to the sleeve mirroring Chento's
  pattern (small `signal.py` addition, env-gated `SSQ_DIAG=1`) — the sleeve is
  unproven; we want the same visibility Chento now has.

### M2.3 Sizing — fixed-R with a hard notional cap (replaces README's 20–100×)
`notional = capital × RISK_PCT / stop_pct`, **RISK_PCT = 1%** default. The
sweep-low stop can sit only bp from entry → tiny `stop_pct` → runaway notional;
therefore hard cap `NOTIONAL_MAX_X = 3×` capital and log `sized_at_cap` when it
binds (expect it to bind often; that's by design — it converts "20–100×
leverage" into "risk 1%, never exceed 3× notional").

### M2.4 Definition of done
Parity test green → runner live → **first natural trigger paper-trades a full
open→close cycle** with costs/funding applied correctly (inspected by hand) →
monitor green with its fire-rate expectation armed. Calibration log
`docs/calibration/short_squeeze.md` started.

---

## Phase 3 — Decommission decisions (explicitly NOT now)
- Legacy `bot.py` + orchestrator + coordination layer: left in place, not run.
  Deletion is a separate decision after both bots have run ≥ a few weeks clean.
- Replay-variant archive (pool plan A2/D6) still worth doing for DB hygiene —
  unchanged scope, any time.
- Physical moves (`strategies/sleeves/X` → `bots/X/strategy/`, repo split,
  per-bot ledger DB split via a `db.LEDGER_DB` constant): optional, only after
  Phase 1+2 prove out. None of them block evidence generation.
- Go-live seam (per-bot sub-account, execution adapter, Binance sub-account
  availability/ToS check): out of scope; the variant-per-bot boundary is the
  future account boundary.

## Sequencing
P0.1 heal-now (deadline ~07-28) → P0.2/P0.3 → M1 (Chento) → M2 (Short Squeeze).
M2.1's parity work can start parallel to M1 (notebook + historical data only).

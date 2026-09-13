# OKX cross-exchange gate — causal re-validation — PRE-REGISTRATION

**Written and frozen: 2026-09-13, before any outcome number was computed.**

> ## ADDENDUM 1 — 2026-09-13, committed BEFORE any execution of §9 steps 1–6
>
> The body below (frozen at `19cd10a`) is not edited. Where this addendum and the body
> disagree, this addendum governs, and every item says which frozen text it replaces. It was
> written after a pre-run implementation review, from code, ledger timestamps and pool counts.
> **No R, PnL, kept/blocked split or gate statistic had been computed when it was written.**
> User decisions (2026-09-13): A1 as "check against the schedule"; A2–A6 approved together.
>
> ### A1 — P3 is CHANGED (a change to a frozen check, not a clarification)
>
> **Why.** The body's P3 tolerance (60 s against `actual_exit_time`) was measured on the three
> original ledger trades only (§2.4 table, §7.11). P3 as frozen also covers their
> doubled-fleet twins, which were never measured. A pre-run review, using ledger timestamps
> only, found that both TIF twins fail it with certainty: SJ-4244 by 88.4 s and SJ-4246 by
> 81.0 s, because the twins entered 58.2 s and 45.4 s after the bar close, against 25.5 s
> and 12.4 s for the originals. §4.4 forbids a repair from changing a threshold, so the body
> would have returned INVALID twice and ended with no verdict and the gate left on — the
> §7.12 escape hatch that decision 2 exists to close. The author of the body set that
> tolerance; the error is the author's.
>
> **Replaces the P3 bullet of §4.4:**
> - exit KIND must match exactly (stop / target / TIF; `tif_expiry` and `scheduled_exit` both
>   map to TIF);
> - **TIF exits:** the walker's exit-bar CLOSE time must be within **180 s** of the trade's own
>   recorded `_time_stop_iso` (its schedule), not of `actual_exit_time`;
> - **stop and target exits:** the walker's exit-bar close must be within **180 s** of
>   `actual_exit_time`, and the exit price within 1e-9 relative;
> - TIF exit price stays report-only, written only after `verdict.json` exists.
>
> **Why 180 s and not 60 s.** Against the schedule, the TIF gaps are each trade's entry lag:
> 25.3, 57.9, 12.2 and 45.2 s. A 60 s tolerance would pass by 2.1 s — a number fitted to six
> observations after seeing them, which is how the original error happened. 180 s is taken
> from the code instead: `LIVE_EVAL_GRACE_S = 180` (`bots/chento_v3/strategy/signal.py:68`)
> is the window in which live entries are evaluated after each 15m boundary, so entry lag,
> and with it the schedule, is bounded by it by construction. It also covers the stop side
> (`LIVE_BAR_SETTLE_S = 90`, :74, plus a 60 s tick). It still catches a one-bar walker error
> (900 s) five times over. Measured on all six today: TIF 25.3 / 57.9 / 12.2 / 45.2 s against
> the schedule; stops 17.9 / 17.9 s against the ledger exit, prices exact.
>
> ### A2 — When "the run" starts (§4.4 "any exception in steps 1–5")
>
> - Executions before the **run commit** are development runs, not the study run: steps 1–4,
>   `outcomes.py` phase A (preconditions only), the synthetic tests and the shadow run.
> - **The study run is the first execution of `outcomes.py` phase B — the phase that computes
>   R — at the run commit.** The run commit is named in `findings.md`.
> - A precondition failure (P0, P1, P2, P3, POWER) seen in a development run on correct code is
>   recorded and counts as the first INVALID — **unless** its diagnosed cause is an
>   implementation defect, fixed without changing any frozen value, with that diagnosis
>   committed before phase B. Either way it is written down; a development run can never
>   quietly become an undeclared repair.
>
> ### A3 — Operator slips are not INVALID
>
> A missing or stale precondition artefact, a snapshot or provenance hash mismatch, or a
> refusal guard firing writes nothing and is **not** INVALID. INVALID is only (a) a
> precondition that was actually evaluated and failed, or (b) an exception once phase B has
> started on verified inputs.
>
> ### A4 — Write order (reconciles §4.4, §4.5 and §9)
>
> `outcomes.py` phase B computes the verdict **and every §4.5 report item** in memory, then
> writes the outcome files, then writes `verdict.json` **last**, by atomic rename. Any exception
> before that rename — report-only computations included — is INVALID, exactly as §4.4 says.
> "Once `verdict.json` is written, the verdict is final" is unchanged.
>
> ### A5 — Added INVALID conditions (pipeline defects that would otherwise read as gate-off)
>
> Added to the §4.4 step-1 list. Each would otherwise fail a clause silently and fall through
> to RETIRE or INCONCLUSIVE, switching the gate off because of a bug:
> - any non-finite R in the OFF arm (`gates.gate_metrics` would silently treat it as a
>   no-fire, `gates.py:141-143`, `:164-165`);
> - an empty or degenerate R2 arm: `n_K_R2 == 0` or `n_B_R2 == 0`, or R2 z non-finite on every
>   trade. The R2 NaN-z count is reported before phase B.
>
> Also pinned here, so no later case is undefined:
> - **TIF exit bar missing:** exit at the close of the first existing 15m bar with
>   `bar_ts ≥ t + 72h`. (0 missing bars measured in all four price tables over
>   [2021-01-01, 2026-09-12), so moot on this snapshot; it matters for the re-cut.)
> - **Same-hour control, non-finite bootstrap quantile:** treated as a control run failure
>   (§4.5: one repair, then "control not evaluable (run failure)"), never as a silent pass or
>   fail of the required statement.
>
> ### A6 — The snapshot is taken ONCE, and its hash is committed before phase B
>
> The body froze the row cutoff (`< 2026-09-12 00:00 UTC`) but not the moment the snapshot is
> captured. `ca_long_short_ratio` rows inside the trailing 30 days are still being rewritten
> by the feed, so the capture time can change HOLDOUT anchors and pool membership. Step 1
> therefore runs exactly once; `results/snapshot.json` (file sha256, per-table logical sha256,
> row counts, capture time) is committed in the run commit; and that same file is used for
> the development runs, the study run, any rerun and the §6 re-cut. The snapshot file itself
> stays outside the repository.
>
> ### Report-only formulas, fixed now (cannot move the verdict; declared to remove any
> appearance of choosing them after seeing numbers)
>
> - MAR-like = total R / trade-close max drawdown in R; trade-close drawdown cumulates R in
>   **exit** order.
> - Per-trade Sharpe = mean / sd (ddof = 1). Daily Sharpe on the 1,987-day axis, zero on empty
>   days, periods_per_year = 365.
> - DSR (`dsr_from_returns`) on per-trade R in entry order, at N = 1, 27, 53.
> - Cap-binding trade: `risk / entry < 0.02 / 3`.
> - MDE = (z₁₋₀.₀₅/₅₃ + z₀.₈₀) × sqrt(s_K²/n_K + s_B²/n_B), at power 0.80.
> - CI half-width = (q₀.₉₅ − q₀.₀₅) / 2.
> - Mark-to-market daily axis: first entry day to last exit day, zero-filled
>   (`benchmark.compounded_max_drawdown` drops non-finite values, `benchmark.py:159`).
> - Tilt portfolios per asset; pooled = the entry-ordered concatenation of the two sized series.
>   `run_overlays.tilt_sizes` reacts to the IMMEDIATELY preceding trade only
>   (`run_overlays.py:127-128`).
>
> ### Looks made before this addendum (disclosure, extending §7.11)
>
> While planning the implementation: a probe snapshot reproduced all six P0 z values
> (|diff| ≤ 4.4e-16) and P0c; the walker was run on the six closed ledger trades for exit
> kind and exit timing (booleans and time deltas; stop prices matched); pool sizes before any
> filter were counted (357 BTC, 337 ETH in the window — not §4.6's 209/187, which are
> post-research-filter counts; §4.6's power figures were computed on the smaller numbers);
> and the six ledger exit-reason strings were read. **No R, PnL, kept/blocked split or gate
> statistic was computed.**


### Decisions recorded (user, 2026-09-13 — final, encoded in the rules below)

1. **RETIRE is discrimination-based, not a breadth policy.** RETIRE only if the causal gate
   fails to discriminate: the blocked set is profitable **and** the paired block-bootstrap
   90 % CI of (kept − blocked) mean R includes 0 (§4.3). A frozen **same-hour control arm**
   (C4's information set, same pool, same engine) is run after the verdict; it is
   non-decision-bearing. If it would also meet the RETIRE clause, `findings.md` must state
   that the verdict does not bear on the look-ahead premise (§4.5).
2. **INCONCLUSIVE switches the gate off.** Chosen by the user against the recommendation.
   Under INCONCLUSIVE the recommendation is `FILTER_OKX_ALIGNED = False` on both chento bots,
   executed as its own commit with golden re-baseline and restart (§6). **KEEP is the only
   verdict that leaves the gate on, and §4.6 rates KEEP near-unreachable, so gate-off is the
   most likely end state of this study.**
3. **Scope is pooled BTC+ETH, with a per-asset direction clause.** A pooled KEEP or RETIRE
   requires that neither asset shows the opposite sign on the discrimination statistic; if
   the assets disagree in sign the verdict is INCONCLUSIVE (clause D, §4.2–4.3).
4. **Drawdown is report-only.** The verdict tests expectancy and discrimination, **not** C4's
   −25 % max-drawdown claim. A gate that cuts drawdown while blocking profitable trades can
   be retired by this study (§1, §4). Mark-to-market drawdown is reported
   (`GATE_VALIDATION.md` §8.4).
5. **New outcome INVALID** (required by decision 2, so a pipeline failure is never read as
   INCONCLUSIVE). INVALID = any parity/fidelity/power precondition fails (P0, P1, P2, P3, POWER, or degenerate gate_metrics)
   or the study cannot run to completion. INVALID permits no production change and no
   verdict. It permits exactly one repair-and-rerun; the repair is a dated addendum at the
   top of this README, committed before the rerun computes any outcome. A second INVALID
   ends the study with no verdict and no change. **Evaluation order is total: INVALID →
   KEEP → RETIRE → INCONCLUSIVE** (§4.4).

---

BACKLOG item 8 (`BACKLOG.md:524-534`). This study re-tests chento_v3's filter 3, the
OKX-Binance perp delta z-score alignment gate (`FILTER_OKX_ALIGNED = True`,
`OKX_DELTA_WINDOW_HOURS = 168`, `OKX_ALIGN_Z_MIN = 0.0`,
`bots/chento_v3/strategy/config.py:89-91`). The test uses the information the running bot
can actually see.

Nothing under `bots/**` or `strategies/**` changes inside this study. `prod.db` is opened
`?mode=ro` only. The running fleet (feed, 7 bots, dashboard; open positions SJ-4242, SJ-4247
and SJ-4250) is never touched. Any production change that follows from the verdict is its
own commit, made after `findings.md` is committed (§6).

**What was measured before freezing, and nothing else:**
- table spans (§0.1);
- the `okx_delta_z` value at the six bars where the live bots recorded one (§0.3). These are
  feature values;
- row counts and trigger timestamps of `overlay_study/results_backonly/trades_{BTC,ETH}.csv`
  read with `usecols=['ts']` only (§4.6, §6). The `r_outcome`, `exit_kind` and
  `okx_delta_z` columns were not read.

No trade outcome, R, win rate, blocked/kept split or gate statistic of any kind was computed
for this pre-registration.

---

## 0. Data, costs and conventions

| Item | Frozen value |
|---|---|
| Database | `data/databases/prod.db`, read `?mode=ro`. The first script copies the input tables (rows with `timestamp < 2026-09-12 00:00:00 UTC`) into a **scratch snapshot** outside the repo and records row counts, max timestamps and a logical content hash. Every later script reads only the snapshot. The snapshot is needed because Binance overwrites the trailing ~30 days of LSR (`studies/notebooks/lsr_b5_study/README.md`), so a re-run must not see different history. |
| BTC inputs | `cd_futures_15m`, `okx_perp_1h`, `ca_long_short_ratio` with `asset='BTC'` |
| ETH inputs | `cd_futures_eth_15m`, `okx_perp_eth_1h`, `ca_long_short_ratio` with `asset='ETH'` |
| Timestamps | Bar OPEN, UTC seconds, on every table (a row stamped T is the bar [T, T+period); `signal.py:231`) |
| Sample window | Trigger bar open `t` in **[2021-04-01 00:00, 2026-09-08 00:00) UTC**, both assets. The start leaves the bot's 90-day frame a full history (OKX, ETH 15m and LSR all begin 2021-01-01). The end leaves a full 72 h 45 m path inside the snapshot. |
| Entry time / entry day | `entry_ts = t + 15m` (the trigger bar's close). `entry_day = UTC date of entry_ts`. Used for the fold axis, the bootstrap clusters and every per-day series. |
| Cost | **10 bp round trip**: `COST_BP_RT = 10.0` (`bots/chento_v3/strategy/config.py:40`, measured in `execution_2026_09` E6; `docs/calibration/chento_triple_v3.md`, row 2026-09-12). Converted exactly as the bot does: `cost_R = 10/10000 × entry/risk` (`signal.py:650`). 18 bp is reported as a secondary line only. |
| Funding | Not modelled. The sleeve closes with `apply_funding=False` (`signal.py:493-497`). Recorded as a known bound in §7. |
| Unit | **R.** 1R = initial risk = `ATR_STOP_MULT (5) × ATR(14)` on 15m bars, from the bot's own frame (`signal.py:903`). The bot sizes fixed-R at `RISK_PCT = 2.0` (`bots/chento_v3/config.py:21`), so 1R = 200 bp of bot capital and −5 bp of capital = **−0.025R**. The 3× notional cap (`:22`) is ignored in R accounting; the number of trades where it would bind is reported. Ladder off (`LADDER_ENABLED = False`). |
| RNG / bootstrap | Every bootstrap: `seed = 42`, `n_iter = 10000`. Decision-bearing bootstraps: `benchmark.paired_block_boot_diff` with **`block = 30` calendar days** on the day axis of §4.3. Scripts are deterministic. |
| Toolkit | `studies.lib.validation.gates` (`walk_forward_folds`, `gate_metrics`, `promotion_verdict`, `n_trials_ledger`), `benchmark.paired_block_boot_diff` (`benchmark.py:81-101`), `benchmark.compounded_max_drawdown` (`:154-164`), `metrics.max_drawdown` (`metrics.py:113`), `metrics.daily_sharpe`, `dsr_pbo.dsr_from_returns` |

### 0.1 Data inventory (measured read-only 2026-09-13)

| Table | First bar (UTC) | Last bar (UTC) | Rows |
|---|---|---|---|
| `cd_futures_15m` | 2019-09-08 17:45 | 2026-09-13 16:30 | 245,948 |
| `cd_futures_eth_15m` | 2021-01-01 00:00 | 2026-09-13 16:30 | 199,843 |
| `okx_perp_1h` | 2021-01-01 00:00 | 2026-09-13 15:00 | 49,960 |
| `okx_perp_eth_1h` | 2021-01-01 00:00 | 2026-09-13 15:00 | 49,960 |
| `ca_long_short_ratio` BTC / ETH | 2021-01-01 | 2026-09-13 | 2,074 / 2,074 |
| `bybit_perp_1h` (C4 only, not used here) | 2021-01-01 00:00 | 2026-05-26 00:00 | 47,305 |

`okx_perp_1h` has no write-time column. When the live feed actually received an OKX hour
cannot be recovered from the database (see §0.3.1 item 6).

### 0.2 How `validation_C4` paired the bars (the defect under test)

`studies/notebooks/chento_journal/validation_C4_cross_exchange_delta.py`:

- **Loader (:66-93).** Binance close comes from `cd_futures_ohlcv` (1h klines, :70-74).
  It is outer-merged on the identical timestamp with `okx_perp_1h` and `bybit_perp_1h`
  (:87), then `dropna(how='any')` (:92). Each row is hour H on all three venues, **each a
  complete bar closing at H+1h**.
- **z-score (:96-113).** `ln(okx) − ln(bnb)`, row-count `rolling(168, min_periods=42)` over
  the three-venue intersection.
- **Attach (:205-211).** `searchsorted(opt['ts'], side='right') − 1` picks hour
  `floor_hour(t)`.
  - `t` is the 15m trigger bar OPEN (`validation_B1_moneyflow_divergence.py:72, :149`).
  - Entry is that bar's close (`validation_C5_smc_features.py:404, :437`), so the decision
    is at `t + 15m`.
  - The attached z contains prices **45 / 30 / 15 / 0 minutes after the decision** for bars
    opening at :00 / :15 / :30 / :45.
  - Against the R1 information set (§0.3.2) it is **one hour newer on every bar**.
- **Everything else in C4's configuration.**
  - Pool: bidirectional `intersect_triggers` (:174), so ±24h forward selection.
  - Replay: `replay_one` without `tif_bars` (:182-183), i.e. TIF 24h (`C5:79`) at 18 bp
    (`C5:78`).
  - no_tilt computed on the ungated sequence before the gate (:192-200).
  - IS_END 2024-12-31 (:61).
  - The published table (baseline n=229, `okx_aligned z≥0` n=114, +1.25R vs +1.35R,
    OOS +1.13 vs +1.51, maxDD −3.37R vs −2.54R) belongs to that configuration.
- **The same one-hour-ahead attach was copied into:**
  - `validation_multi_asset.py:266`
  - `validation_group_A_tuning.py:283`
  - `validation_liquidation_and_C6.py:245`
  - `validation_a4_eth_funding_a8.py:305`
  - `overlay_study/gen_trades.py:83-87`, and therefore the `okx_delta_z` column of
    `overlay_study/results_backonly/trades_*.csv`
- **Consumers of that column that applied the same-hour gate as a fixed z ≥ 0 filter:**
  `overlay_study/run_overlays.py:163-165`, `attribution/attribution.py:135-136`,
  `lsr_b5_study/score_variants.py:50-51`, and the threshold re-filter in
  `validation_audit_2026_09/audit_backtests.py:425-452`.

BACKLOG 7b measured that the gate's sign differs from the same-hour value on ~31 % of bars
(`BACKLOG.md:513, :530`).

### 0.3 The live information set (frozen definition)

The live bot is described in §0.3.1 and the study's definitions in §0.3.2.

#### 0.3.1 What the bot does (HEAD `7b23183`; `signal.py` last changed in `f01894f`, shipped by `8d9986c`)

1. `_load_okx_1h` (`signal.py:228-257`) reads OKX closes with
   `timestamp >= now − 30d AND timestamp <= now − 3600` (:246).
2. The Binance 1h close is `df_15m['close'].resample('1h').last()` (:325) of the bot's
   90-day 15m frame, bounded `<= now` (:191-198).
3. `math.compute_okx_delta_z` (`math.py:444-451`) inner-joins the two series (`dropna`),
   takes `ln(okx) − ln(bnb)` and a row-count `rolling(168, min_periods=42)` z.
4. The z series is forward-filled onto the 15m index (`signal.py:328`).
5. `_filter_passes` reads it at the evaluated bar (:420-425) and calls
   `okx_aligned(z, dir, 0.0)` (`math.py:454-460`). The rule is **long iff z ≥ 0, short iff
   z ≤ 0; NaN ⇒ blocked** (:456).
6. **Feed lag.** Rows reach the table through `_refresh_hourly_okx`
   (`data/sources/binance.py:1091-1106`), called on every feed cycle from `refresh_all()`
   (:966; loop in `feed.py:106-131`). The throttle is an **elapsed-time** throttle, not an
   hour-aligned one: it refreshes only when ≥ 3300 s have passed since the previous refresh
   (:1096-1098; the stamp is set at :1105). Refreshes therefore fall every ~55–56 minutes at a
   phase that drifts through the clock hour, and **each closed OKX hour lands 0–56 min after
   its close**. BTC and ETH refresh in the same tick (:1100, :1102). Only confirmed candles
   are written (`data/sources/okx_perp.py:181-183`).
   - **Measured once (2026-09-13):** the OKX bar stamped 16:00 (closing 17:00) landed
     ~9.5 min after its close.
   - **Uniform-lag model (an estimate, not a measurement).** With lag ~ U(0, 56 min) and the
     live decision at `now ≈ t + 15m + 5s`, hour `floor_hour(t) − 1h` is admitted by the
     bound but is visible only if its lag ≤ (minute(t) + 15) min. Live then sees
     `floor_hour(t) − 2h` (the R2 set) on **~73 % of :00 bars, ~46 % of :15, ~20 % of :30
     and 0 % of :45** (41/56, 26/56, 11/56, 0).

**Measured at every bar where live recorded a z** (feature values recomputed read-only with
`math.py` on 2026-09-13; three ledger fires plus the three `okx_misaligned` near-misses in
`bots/chento_v3*/logs/diag.jsonl`):

| Bar (15m open, UTC) | Asset | Source | Live `okx_delta_z` (stored float) | Newest joined hour reproducing it (≤ 1e-9) |
|---|---|---|---|---|
| 2026-08-21 06:00 | BTC | SJ-4243 | 1.4609786480063527 | 05:00 |
| 2026-08-21 19:30 | BTC | SJ-4245 | 0.14373400566189817 | 18:00 |
| 2026-08-22 03:45 | BTC | SJ-4248 | 1.675149227184317 | 02:00 |
| 2026-08-22 09:45 | BTC | diag near-miss | −0.5391446553064457 | 08:00 |
| 2026-09-02 02:15 | ETH | diag near-miss | 1.9303023139624131 | 01:00 |
| 2026-09-02 23:45 | ETH | diag near-miss | 0.45577527404023227 | 22:00 |

All six equal the value at hour `floor_hour(t) − 1h`: five with |diff| = 0.0, one with
|diff| = 4.4e-16 (float noise). At the three :45 bars, `now − 3600` alone would have
admitted hour H, but the feed had not written it (at SJ-4248, hour 03:00 gives
0.9437063377980122). None of the six equals `floor_hour(t) − 2h`.

**This is not reassurance about the feed lag.** Three of the six are :45 bars, where the
uniform-lag model also predicts `floor_hour(t) − 1h` with certainty. Under that model the
probability that all six show `floor_hour(t) − 1h` is ≈ 15/56 × 45/56 × 30/56 ≈ 0.12. Six
observations neither confirm nor exclude a material share of live decisions on the R2 set.

#### 0.3.2 Definitions used in this study

**R1 — primary information set.** For a trigger on the 15m bar that opens at `t`:
`okx_delta_z(t)` is the value the bot's own replay path produces at sim clock `t`
(`signal.py:763-796`). That is `df.loc[t, 'okx_delta_z']` after
`clock.set_simulated_now(t); signal._rebuild_daily_cache(t, force=True)`, which equals
z at the newest joined hour `h` with `h ≤ t − 3600`, i.e. `floor_hour(t) − 1h`. R1 is what
live sees when the OKX hour has landed within (minute(t) + 15) min of its close.

**R2 — robustness arm (feed lag).** The same rebuild, with `signal._load_okx_1h` wrapped
**in memory** to call the original with `now − 1h`, so the OKX bound is `t − 7200`. R2 is
what live sees when the hour has not yet landed (§0.3.1 item 6). R2 can only downgrade a
KEEP (§4.2). It can never produce one.

**Same-hour (C4-style) — control arm, post-verdict.** z from `math.compute_okx_delta_z` on the
full-history Binance 15m resample and OKX series, read at hour `floor_hour(t)` (the
`gen_trades.py:83-87` attach). The same-hour z FEATURE is computed in step 3 for the
sign-disagreement rate only; the same-hour arm's OUTCOMES are computed only after
`verdict.json` exists (§4.5).

### 0.4 Parity gate (P0) — must pass before any outcome is computed

The outcome script refuses to run unless `results/parity.json` has `"pass": true`.

| # | Check | Tolerance |
|---|---|---|
| **P0a** | In a BTC process on the snapshot, R1 `okx_delta_z` at bars 2026-08-21 06:00, 2026-08-21 19:30 and 2026-08-22 03:45 equals the live ledger values in `trades.notes._filter_diag.okx_delta_z` for **SJ-4243 1.4609786480063527, SJ-4245 0.14373400566189817, SJ-4248 1.675149227184317**. `okx_aligned(z, 'long', 0.0)` is True at all three, as live traded all three LONG. | `abs(z − ledger) ≤ 1e-9` (not bit-exact: one read-only reconstruction differed by 4.4e-16); gate decision identical |
| **P0b** | Same check for the BTC near-miss (2026-08-22 09:45, −0.5391446553064457) and, in an ETH process, the two ETH near-misses (2026-09-02 02:15 1.9303023139624131; 2026-09-02 23:45 0.45577527404023227). The gate decision must be BLOCK at all three. | `≤ 1e-9`; decision identical |
| **P0c** | The newest OKX hour inside the R1 frame is `floor_hour(t) − 1h`, and inside the R2 frame `floor_hour(t) − 2h`, at all six bars. | exact (integer hour) |
| **P0d** | Process hygiene, asserted in code in **every** process of §9 (steps 2–6) before and after import: (i) `CHENTO_V3_DIAG` is `"0"` before import and `signal._DIAG_ENABLED is False`, since the diag path is resolved at import (`signal.py:81-86`) and would otherwise append to a running bot's JSONL; (ii) `db.PROD_DB`, `db.DASH_DB` and `db.TRADER_DB` point at the snapshot before `bots.chento_v3.strategy` is imported, **and every module-level research DB constant is repointed to the snapshot and asserted before its first use** (list below); (iii) `'strategies.support.trade_db' not in sys.modules` and `'strategies.support.variant_registry' not in sys.modules` (BACKLOG 10; `a942cf1` removed their import-time DDL, the assertion stays); (iv) no call to `decide`, `execute` or `_sweep_open_positions`. | exact |

**P0d(ii) research DB constants** (each `DB = ROOT / 'data' / 'databases' / 'prod.db'`, read at
call time as a module global, so setting the module attribute after import and before the
first call is sufficient; the script asserts `Path(mod.DB).resolve() == snapshot` for all five
before calling any research function):

| Module | Line | Why it matters |
|---|---|---|
| `studies/notebooks/chento_journal/validation_multi_asset.py` | :35 | `load_perp_15m` / `load_lsr_asset` / `load_okx_close_asset` open `sqlite3.connect(str(DB))` **read-write** (:83, :98, :113) |
| `studies/notebooks/chento_journal/validation_B1_moneyflow_divergence.py` | :50 | read-write connect at :64 |
| `studies/notebooks/chento_journal/validation_B5_lsr_extremes.py` | :35 | read-write connect at :48 |
| `studies/notebooks/chento_journal/validation_group_A_tuning.py` | :33 | imported by `validation_group_A_tuning_backonly.py:30` |
| `studies/notebooks/overlay_study/run_overlays.py` | :32 | imported by `outcomes.py` for `tilt_sizes` only; `:46`, `:57` open `?mode=ro` |

Import side effects, noted and accepted: `validation_multi_asset.py:36-37`,
`validation_B1_moneyflow_divergence.py:51-52`, `validation_group_A_tuning.py:34-35` and
`validation_C5_smc_features.py:61-62` call `OUT_DIR.mkdir(parents=True, exist_ok=True)` on
`studies/material/chento/validation`, which exists, so the call is a no-op.
`run_overlays.py:35` reads `sys.argv` into an unused `OUT` path at import and `:36`
reconfigures stdout; neither writes. **`gen_pool.py` does not import
`overlay_study/gen_trades.py`** (its `gen()` writes `trades_{asset}.csv` including
`r_outcome` to `gt.OUT` at :91, and `:46-47` mkdirs `overlay_study/results`); it imports the
research functions directly from the modules that `gen_trades.py:27-44` imports.

**If any P0 check fails, the outcome is INVALID (§4.4) and no outcome is computed.**

---

## 1. The question

**Q-KEEP (yes/no).** On the backward-only chento Triple pool, with every other production
filter applied identically, and with `okx_delta_z` on the R1 information set, does the OKX
alignment gate pass the repo's binary-gate promotion test (`GATE_VALIDATION.md` Step 5,
`gates.promotion_verdict(kind="binary")`) at `N_TRIALS = 53`, keep better trades than it
blocks pooled and on each asset separately, and still block losing trades under R2?

**Q-RETIRE (yes/no), asked only if Q-KEEP is no.** On the same pool over the full window,
does the gate fail to discriminate — are the trades it blocks demonstrably profitable while
the paired block-bootstrap 90 % CI of (kept − blocked) mean R includes 0, with both assets
agreeing in sign with the pooled difference?

The outcomes are resolved by the total evaluation order of §4.4 (INVALID → KEEP → RETIRE →
INCONCLUSIVE), not by a claim that the clauses are mutually exclusive.

**What the verdict does NOT test (decision 4).** The verdict tests expectancy and
discrimination only. It does **not** test C4's −25 % max-drawdown claim. A gate that cuts
drawdown while blocking profitable trades **can be retired by this study**. Drawdown,
including mark-to-market drawdown, is report-only (§4.5).

**Hypothesis under test (C4's claim, restated causally).** When OKX trades at a premium to
Binance, relative to its 7-day norm, in the direction of the trade, cross-venue flow has
follow-through. The trades the gate removes should therefore be the bad tail. If the effect
lives in the 15–45 minutes of future prices that C4's same-hour bar contained, the causal
gate will not separate the two groups.

---

## 2. Frozen rules

### 2.1 Trigger pool (research, backward-only)

The steps of `studies/notebooks/overlay_study/gen_trades.py::gen` (:52-80), with
`intersect_triggers` replaced by `validation_group_A_tuning_backonly.intersect_backward`
(window [−24h, 0], :33-46), exactly as `gen_trades_backonly.py:22` does, re-stated inline in
`gen_pool.py` (§0.4 P0d(ii)):

- `B1 = b1_triggers(compute_moneyflow_signal(df_15m), cvd_threshold=0.5, velocity_max=1.0)`
- `B5 = b5_triggers(df_15m, compute_lsr_extremes(load_lsr_asset(asset)))` (30 rows;
  PERIOD_START stamps, `lsr_b5_study/findings.md:37`)
- `B7 = b7_alignment_triggers(compute_multitf_cvd(df_15m), z_threshold=2.0)`
- `triple = intersect_backward(intersect_backward(B1, B5), B7)`

**Only `(t, direction)` is taken from research.** Triggers outside the §0 window are
dropped.

**P1 — pool parity** (the `lsr_b5_study` precedent). The same pipeline, continued through
research `replay_one(atr_mult=5.0, target_r=6.0, tp_mode='fixed')` and the research
`no_resist_OB` filter, must reproduce `overlay_study/results_backonly/trades_{asset}.csv`
on `(ts, direction, entry, stop, target)` for `ts ≤ 2026-07-20 23:59:59`: `ts` and
`direction` identical as sets and in order; `entry`, `stop`, `target` equal within
`abs diff ≤ 1e-9 × value`. The comparison reads the CSVs with
`usecols=['ts','direction','entry','stop','target']` and `float_precision="round_trip"`; the
in-memory `r_outcome` and `exit_kind` columns are dropped before anything is written or
printed. **If P1 fails, the outcome is INVALID.**

### 2.2 Per-trigger features — the bot's own code, one process per asset

For each pool trigger `t`:

1. `clock.set_simulated_now(t)` and `signal._rebuild_daily_cache(t, force=True)`.
2. Assert `df.index[-1] == t`.
3. From `df.loc[t]`: `close` (entry), `atr`, `ret_30d`, `okx_delta_z` (R1),
   `triple_long_anchor` and `triple_short_anchor`.
4. `risk = 5 × atr`. A NaN or non-positive ATR drops the trigger from both arms (counted).
5. `dist_R = math.nearest_resist_ob_distance_R(entry, dir, risk, signal._cached_obs, idx)`.
6. Rebuild under the R2 wrapper and read `okx_delta_z` (R2) only.

**P2 — pool fidelity (decision-bearing precondition).**
- **Denominator, frozen:** ALL pool triggers `(t, direction)` in the §0 window, per asset,
  **before** filter 2 (`dist_R`) and filter 4 (`ret_30d` shorts) and before the ATR drop of
  step 4.
- **Numerator:** those at which the bot's own frame sets `triple_{direction}_anchor = True`
  at `t`.
- Computed and written to `results/fidelity.json` before any outcome.
- **If pooled fidelity < 0.80, the outcome is INVALID.** Below that, the pool is not the
  bot's trigger stream closely enough for a verdict about the bot.

**Precision — report-only.** For each UTC day D in the window, rebuild once at clock
`D 23:45` and collect every bar in `[D 00:00, D 23:45]` with `triple_long_anchor` or
`triple_short_anchor` True. Per asset, sort these anchor bars by time and apply the bot's own
6 h cooldown (`COOLDOWN_HOURS = 6`, `config.py:133`; direction-agnostic, as
`signal.py:737-745` is keyed by variant): drop any anchor bar less than 6 h after the previous
retained one. Precision = share of retained anchor bars whose `(t, direction)` is in the pool.
Two approximations are stated, not fixed: the bot's cooldown runs from entries (after
filters), not from anchors; and anchors are read from an end-of-day frame, not a per-bar
frame. The script reports how many pool triggers have a different anchor value in the
end-of-day frame than in their per-trigger frame.

Also written before outcomes: the R1-vs-same-hour and R1-vs-R2 sign-disagreement rates on the
pool triggers (feature-level), and the POWER counts of §4.2.

### 2.3 Arms

- **OFF arm (ungated).** Triggers passing the bot's filter 2, `dist_R > SMC_OB_WITHIN_R (2.0)`
  (`signal.py:413-417`), and filter 4: shorts skipped when
  `ret_30d > UP_30D_THRESHOLD (0.10)` (`signal.py:427-429`, `config.py:104-105`), using the
  asset's own `ret_30d` from the frame. Filter 1 (tilt) is **not** applied at pool level
  (§4.5).
- **ON arm (gated).** OFF-arm triggers with `okx_aligned(z_R1, dir, 0.0) == True`.
- **Blocked set B = OFF \ ON**, including NaN z; the NaN count is reported. **Kept set K = ON.**

No other z threshold is computed anywhere in this study.

### 2.4 Outcome engine — the bot's walker

For each OFF-arm trade:

- Build the bot's state dict: `entry_price`, `risk`, `stop_price = entry ∓ risk`,
  `target_price = entry ± 6R`, ladder fields as `signal.py` writes them.
- Walk snapshot 15m bars `b = t+15m, t+30m, …`.
- **TIF — declared convention.** The live bot schedules `time_stop = now + 72h`
  (`signal.py:930`) and exits at the close of the first walked bar with `bar_ts ≥ time_stop`
  (:658). Two conventions exist:
  - **As read from the code** (superseded by the measurement below): a 5 s decision lag gives
    `time_stop = t + 72h15m5s`, and a 15m-bar reading of `signal.py:658` would exit on the bar
    opening `t + 72h30m`; the replay path (`signal.py:763-796`, `now = t`) exits on the bar
    opening `t + 72h`.
  - **Frozen: exit bar opens `t + 72h`, exit at its close** (close time `t + 72h15m` =
    `entry_ts + 72h`), in **both arms and in the same-hour arm**:
    `R = sign × (close − entry)/risk − cost_R`.
  - **Correction made before freezing (2026-09-13), measured against the live ledger.** An
    earlier draft of this section froze the bar opening `t + 72h30m` and called it the live
    convention. The ledger says otherwise. The live sweep runs on the live feed every tick and
    exits at the first tick at or after `time_stop`, not at a 15m bar close:

    | trade | kind | ledger exit | this convention's exit close | abs Δ |
    |---|---|---|---|---|
    | SJ-4243 | TIF | 2026-08-24 06:15:28 | 06:15:00 | 28 s |
    | SJ-4245 | TIF | 2026-08-24 19:45:20 | 19:45:00 | 21 s |
    | SJ-4248 | stop | 2026-08-22 05:15:17 | 05:15:00 | 18 s |

    The `t + 72h30m` convention exits ~30 minutes after live on every TIF trade. It could not
    change membership (identical across arms), but it shifts the level of R on TIF exits, and
    R-a — "the blocked set is profitable" — is a level test. P3 (§4.4) now checks this.
  - TIF exit PRICE cannot match live to the cent: live prices a tick, this study a 15m close
    (measured 77,380.3 vs live 77,459.91 and 78,838.2 vs 78,880.0, both ≤ 0.11 %). Stop and
    target exits price at the level itself and match exactly (SJ-4248: 76,594.80559574306 in
    both).
- Before that bar, call `math.evaluate_position_step(state, bar_high, bar_low, bar_close,
  ladder_enabled=False, cost_R=cost_R)`. Stop is checked before target within a bar
  (`math.py:561-575`).
- Missing bar rows are skipped and counted. A trade with more than 4 consecutive missing
  bars is flagged, but kept in both arms.

R is identical for a trade in both arms. The arms differ only in membership.

---

## 3. Periods

| Label | Definition | Role |
|---|---|---|
| FULL | t in [2021-04-01, 2026-09-08) | KEEP clauses K2–K4, D; RETIRE clause; the fold axis |
| Walk-forward folds | `gates.walk_forward_folds(entry_days, *FOLD_PRESETS["event"])` = fit 730 d / OOS 365 d / step 365 d on sorted pooled entry days; all folds the function returns, including a trailing partial one (`gates.py:98-117`) | KEEP clause K1 (stitched OOS) |
| C4-IS / C4-OOS | ≤ 2024-12-31 / 2025-01-01 → 2026-05-26 | report-only, comparability with the memory table |
| HOLDOUT | t ≥ 2026-05-27 | report-only in the main study; the window of the §6 re-cut |

**HOLDOUT, described exactly:** unseen at the C4 selection (C4 saw 2021-01 → 2026-05-26);
its **same-hour** outcomes **were** seen in the overlay study, the validation audit and the
attribution layer, 2026-08 → 2026-09 (`results_backonly` regenerated in `b3be67b`,
2026-09-01; consumers listed in §0.2). Its causal (R1) outcomes are first computed by this
study's report.

**Stated plainly:** FULL is **in-sample for the choice of the gate.** C4 selected `z ≥ 0`
after seeing 2021-01 → 2026-05-26. The folds provide stability evidence, not out-of-sample
evidence for that selection. This study answers whether a published effect survives removal
of its look-ahead. It cannot confirm the selection.

---

## 4. Metrics and the decision rule (fixed now)

**Scope of the verdict (decision 4).** Every decision-bearing clause below is about
expectancy (mean R) and discrimination (kept vs blocked). None is about drawdown. C4's
−25 % max-drawdown claim is not tested; a gate that reduces drawdown while blocking
profitable trades can be retired.

### 4.1 Series

The pooled series is BTC and ETH OFF-arm trades sorted by `(entry_ts, asset)`, BTC before ETH
on ties.

- `per_fire_off[i] = 2.0 × R_i`, in percent of capital (fixed-R at 2 %).
- `per_fire_on[i] = per_fire_off[i]` if trade i is kept, else NaN.
- `periods_per_year = n_OFF / (FULL span in days / 365.25)`, with FULL span in days = 1987
  (the §4.3 day axis, 2021-04-01..2026-09-08 inclusive).
- `m = gates.gate_metrics(per_fire_on, per_fire_off, folds, periods_per_year)`.
- **Discrimination statistic:** `Δ = mean_R(K) − mean_R(B)`, on FULL, R1, 10 bp. `Δ_pool` on
  the pooled series, `Δ_BTC` and `Δ_ETH` per asset.
- **Direction clause D (decision 3):** `sign(Δ_BTC) = sign(Δ_pool)` **and**
  `sign(Δ_ETH) = sign(Δ_pool)`, with sign strictly by `> 0` / `< 0` and a difference of exactly
  0.0 matching either sign. D implies the two assets agree in sign; if they disagree, D fails.

### 4.2 KEEP — all must hold

| Clause | Statement |
|---|---|
| **K1** | `gates.promotion_verdict(m, grid_size=53, kind="binary", reality_check=False)["pass"]` is True. That is `GATE_VALIDATION.md` Step 5 unmodified (`gates.py:217-265`): stitched-OOS blocked-trade expectancy ≤ −5 bp (= −0.025R), stitched-OOS Sharpe uplift / √53 ≥ +0.2 (raw ≥ 1.456), and the gated curve beating the ungated curve in ≥ 2/3 of folds. |
| **K2** | Per asset (FULL): `Δ_BTC > 0` **and** `Δ_ETH > 0` |
| **K3** | R2 robustness (FULL, pooled): `mean_R(B_R2) ≤ −0.025R` **and** `mean_R(K_R2) > mean_R(B_R2)` |
| **K4** | `Δ_pool > 0` and **D** holds (K2 with K4 implies D; D is stated so decision 3 is explicit) |

### 4.3 RETIRE — all must hold

Bootstrap construction, frozen (`GATE_VALIDATION.md` §8.7):
- **Day axis:** every UTC calendar date from 2021-04-01 to 2026-09-08 inclusive (the
  `entry_day` range of FULL), `T = 1987`, empty days included.
- **Clusters = UTC entry day.** BTC and ETH trades with the same `entry_day` sit in the same
  row, so they always move together in a block.
- `K_day[d] = [Σ R over kept trades with entry_day d, count]`,
  `B_day[d] = [Σ R over blocked trades, count]`, `ZERO_day[d] = [0.0, 1.0]`; arrays of shape
  `(T, 2)`.
- `ratio(x) = x[:, 0].sum() / x[:, 1].sum()` (the mean R of the resampled trades).
- `boot_diff = benchmark.paired_block_boot_diff(K_day, B_day, ratio, block=30, n_iter=10000,
  seed=42, qs=(0.05, 0.5, 0.95))` — the same block indices for K and B in every draw.
- `boot_B = benchmark.paired_block_boot_diff(B_day, ZERO_day, ratio, block=30, n_iter=10000,
  seed=42, qs=(0.05, 0.5, 0.95))` — the same `T`, `block`, `n_iter` and `seed`, hence the
  same resampled days as `boot_diff`; its quantiles are those of `mean_R(B)`.
- A non-finite value in either `ci` is a run failure (INVALID), not a verdict.

| Clause | Statement (FULL, pooled, R1, 10 bp) |
|---|---|
| **R-a — blocked set profitable** | `boot_B["ci"][0] > 0`: the day-block bootstrap 5th percentile of `mean_R(B)` is above zero. (Replaces the draft's iid bootstrap.) |
| **R-b — no discrimination** | The 90 % CI `[boot_diff["ci"][0], boot_diff["ci"][2]]` includes 0: `ci[0] ≤ 0 ≤ ci[2]`. |
| **D** | The direction clause of §4.1 holds. |

**X1 is report-only.** The raw point comparison `mean_R(K) ≤ mean_R(B)` has no interval and
would fire on about half of all studies of a gate with no effect. Under decision 1 it cannot
retire. Its content is carried by R-b; `Δ_pool` is reported.

**Edge case, encoded exactly as decided.** If the CI lies entirely below 0 (the gate keeps
significantly *worse* trades than it blocks), R-b fails, so the verdict is not RETIRE; it
falls to INCONCLUSIVE, which under decision 2 switches the gate off. `findings.md` must name
this case "anti-discriminating" if it occurs.

### 4.4 Evaluation order — explicit and total

`outcomes.py` evaluates in this order and stops at the first that holds. Exactly one outcome
results.

**Once `verdict.json` is written, the verdict is final.** INVALID can be declared only by the
mechanical checks in step 1, and only before `verdict.json` is written. A defect found
afterwards is recorded in `findings.md` as a threat; it neither voids the verdict nor consumes
the rerun, and the §6 consequence of the verdict proceeds. Without this rule a verdict someone
dislikes could be turned into a rerun by "discovering" a bug after seeing it.

1. **INVALID** if any of:
   - P0 fails (§0.4);
   - P1 fails (§2.1);
   - P2: pooled fidelity < 0.80 (§2.2);
   - POWER fails: pooled `n_K < 30` or `n_B < 30`, or for either asset `n_K < 10` or
     `n_B < 10` (FULL, R1; computed from gate membership before any outcome);
   - **P3 — walker parity against the live ledger.** Before any study outcome, the §2.4 walker
     replays every closed `bot_chento_v3_v1` / `bot_chento_v3_eth` ledger trade opened before
     2026-09-12 (SJ-4243, SJ-4245, SJ-4248 and their doubled-fleet twins SJ-4244/4246/4249)
     from its recorded `_entry_price`, `_risk`, `_stop_price`, `_target_price` and `bar_ts`.
     It must reproduce, for every one: the exit KIND (stop / target / TIF) exactly; the exit
     bar's CLOSE time within 60 s of `actual_exit_time`; and for stop and target exits the
     exit price within 1e-9 relative. TIF exit price is report-only (a live tick vs a 15m
     close). The tolerances come from a measurement, not a choice: max observed Δ 28 s, stop
     price exact. These are already-closed live paper trades — not a kept-vs-blocked
     statistic — and they are asserted in memory without printing R;
   - **gate_metrics degenerate:** any of `oos_sharpe_uplift`, `sign_stability`,
     `blocked_expectancy_bp` non-finite, or `n_folds < 3`. `promotion_verdict` fails a NaN
     criterion silently (`gates.py:227`, `_finite`); without this, a broken fold alignment
     would fail K1 quietly and fall through to RETIRE or INCONCLUSIVE — switching the gate off
     because of a pipeline defect;
   - the study cannot run to completion: any exception in steps 1–5 of §9, or a non-finite
     bootstrap quantile (§4.3).

   P0, P1, P2 and POWER are computed from features, and P3 from already-closed live ledger
   trades (not the kept-vs-blocked split); all five are checked before any study outcome is
   computed. The degenerate-gate_metrics check necessarily uses outcome-derived metrics, so it
   runs in memory AFTER the outcomes are computed and BEFORE `verdict.json` is written, and
   prints nothing; a failure there writes only `invalid.json`. `outcomes.py` holds every outcome value in memory and writes
   `trades_{asset}.csv`, `verdict.json` and `report_pre.json` only after the verdict is
   decided; on an exception it writes `results/invalid.json` with the exception type and
   code location only (no local values) and prints no outcome number.
2. **KEEP** if K1, K2, K3 and K4 hold.
3. **RETIRE** if R-a, R-b and D hold.
4. **INCONCLUSIVE** otherwise. `verdict.json` names every KEEP and RETIRE clause that failed.

**The precedence rule replaces any mutual-exclusivity argument.** The clauses are not
exclusive by construction: K1 is scored on the stitched OOS folds, R-a and R-b on FULL, so a
KEEP and a RETIRE clause set can in principle both hold. When they do, the earlier outcome in
the order wins.

**Simpson's paradox, resolved by the same order.** Because BTC and ETH differ in mean R and
may differ in block rate, pooled and per-asset signs can disagree:
- both `Δ_BTC > 0` and `Δ_ETH > 0` but `Δ_pool ≤ 0`: KEEP fails (K4), RETIRE fails (D) →
  INCONCLUSIVE;
- both `Δ_BTC < 0` and `Δ_ETH < 0` but `Δ_pool > 0` with R-a and R-b holding: KEEP fails (K2),
  RETIRE fails (D) → INCONCLUSIVE.
Neither a per-asset nor a pooled reading is allowed to override the other.

**INVALID — what follows.**
- No verdict, no production change, no memory or calibration-log change. The gate stays as it
  is.
- Exactly **one** repair-and-rerun is permitted. The repair is a dated addendum at the top of
  this README stating the failed check, the diagnosed cause and the code change, and
  asserting that no frozen value of §0–§5 (window, cost, filters, thresholds, clauses,
  `N_TRIALS`, block length, resample count, seed) changes. The addendum and the code change
  are committed **before** the rerun computes any outcome.
- A failure that cannot be repaired without changing a frozen value (for example POWER
  failing on correct counts) cannot be repaired; the rerun will return INVALID again.
- **A second INVALID ends the study with no verdict and no change.**

### 4.5 Report-only (never decision-bearing, never able to change a verdict)

**Written before `verdict.json` is released (same run, `report_pre.json`):**
- per asset and pooled: `n_OFF`, `n_K`, `n_B`, NaN-z count, ATR drops; mean, median, win rate,
  total R and trade-close maxDD R (`metrics.max_drawdown` on cumulative per-trade R) for OFF,
  K and B;
- X1 (`Δ_pool`, `Δ_BTC`, `Δ_ETH` as point values) and the full `boot_diff` / `boot_B` dicts;
- MAR-like, per-trade Sharpe, and annualised daily Sharpe (`metrics.daily_sharpe` on R summed
  by entry day);
- `dsr_from_returns` of the K arm at n_trials 1, 27 and 53, and of the OFF arm at 1;
- long/short split; exit mix (stop / target / TIF);
- C4-IS, C4-OOS and HOLDOUT tables;
- the full R2 table; the 18 bp line; the count of cap-binding trades;
- the **realised minimum detectable effect** for K − B at the Bonferroni level
  (α = 0.05/53, one-sided), and the realised half-width of the `boot_diff` 90 % CI;
- precision (§2.2).

**Mark-to-market drawdown (`GATE_VALIDATION.md` §8.4).** For OFF, ON (R1) and both tilt
portfolios below: a daily equity series where each open trade is marked at the close of the
last 15m bar of each UTC day it is open (and at its exit bar on its exit day), contributing
`0.02 × size × ΔR_marked` of initial capital that day, cost charged on the exit day. Reported
as `benchmark.compounded_max_drawdown(daily_returns)` alongside the trade-close
`metrics.max_drawdown`.

**Production-sequence portfolio — an approximation.** Tilt applied after the gate in the ON
arm and after the filters in the OFF arm, via
`run_overlays.tilt_sizes(rs, ts, policy)` (`overlay_study/run_overlays.py:118-139`), with `rs`
the arm's R in entry order, `ts` its `entry_ts` as a `pd.DatetimeIndex`, and
`policy = 'skip_after_loss'` for BTC, `'half_after_loss'` for ETH. Reports total R,
trade-close maxDD R, mark-to-market DD and MAR, ON vs OFF. It is labelled an approximation
because `tilt_sizes` zeroes (or halves) the next trade after **any** negative R, including
trades it itself skipped (`:125-128`), whereas production differs:
- BTC skips entries for 48 h only after a **stop_hit** loss (`signal.py:398-410`, stamp set at
  `:686-690`); TIF losses do not trigger it;
- ETH halves risk on the trade after the last **closed** trade lost
  (`bots/chento_v3/runner.py:67, :126-129`; `bots/chento_v3_eth/config.py:26-30`).
This is the causal counterpart of the memory's "−25 % maxDD" and cannot change the verdict.

**Same-hour control arm — written only after `verdict.json` exists** (`same_hour_report.py`
refuses to run otherwise; `results/report_post.json`). Frozen now:
- C4's information set (§0.3.2 same-hour z), on the same OFF arm (same pool, same filters 2 and
  4 from the bot frame, same trades, same R from §2.4), gate `okx_aligned(z_SH, dir, 0.0)`.
- The same statistics as §4.1–4.3: `Δ`, D, `boot_diff`, `boot_B` with `block=30`,
  `n_iter=10000`, `seed=42`; the same POWER thresholds; K1 at `grid_size=53` for reference.
- It isolates how much of the published effect was the information set.
- **Required statement** — applies when the causal verdict is RETIRE or INCONCLUSIVE. If the
  causal verdict is KEEP and the same-hour arm meets the full RETIRE clause, `findings.md`
  instead states that the control fails to discriminate on FULL while the causal gate passed
  the OOS promotion test, and flags it as a §8 pipeline red flag. Otherwise, if the same-hour
  arm would **also** meet the full RETIRE clause (R-a, R-b, D, with POWER met), `findings.md`
  must state: *"The same-hour control also fails
  to discriminate, so this verdict does not bear on the look-ahead premise: the gate does not
  discriminate on this pool and engine even with C4's information set."*
- If the same-hour arm fails POWER, `findings.md` says the control is not evaluable.
- A crash in this step does not touch the verdict. It gets at most ONE repair (a dated
  addendum; no frozen value changed). If it fails again, `findings.md` records "control not
  evaluable (run failure)" and proceeds. The §6 production change never waits on this step
  beyond that single repair — an unbounded wait here would be a de facto KEEP.

### 4.6 Power, stated before computing

- C4's own peeking discrimination was `okx_aligned z≥0` +1.35R vs `okx_contrary` +1.15R, a
  0.20R gap on ~115 trades each, at 24h/18 bp.
- The backward-only pool has 209 BTC and 187 ETH triggers with `t` in the §0 window (row
  counts of `results_backonly/trades_*.csv`, before filter 4). With the gate removing about
  half, `n_K ≈ n_B ≈ 150–200` pooled.
- For a 6R-target / 1R-stop payoff the per-trade sd is of order 2R (an assumption, not a
  measurement). The standard error of K − B is then about 0.2R, and the 90 % CI half-width
  about 0.33R before the widening from day blocks.
- **R-b is expected to hold even if C4's effect were fully causal:** at a true 0.20R gap the
  chance the CI excludes 0 is about 26 % (iid normal approximation), lower with blocks.
- R-a holds whenever the blocked trades' mean R is above roughly 0.25–0.35R; the chento pool's
  mean R is positive.
- D is close to a coin flip when the true gap is near zero, because the per-asset signs are
  then noise.
- At `N_TRIALS = 53`, K1's Sharpe criterion needs a raw stitched-OOS Sharpe uplift of about
  1.46, on top of K2, K3 and K4.

**KEEP is therefore near-unreachable at this sample even if C4's effect were real.** That is
the declared consequence of 53 trials on a few hundred trades, not a defect to be repaired
after seeing the result. **Under decision 2 it means gate-off is the most likely end state of
this study** (§6, §8). The realised MDE and CI half-width are reported (§4.5).

---

## 5. Trial count

| Source | Trials |
|---|---|
| C4 (BTC), `validation_C4…py:217-291`: max_abs_z high/low × 5 thresholds (10), okx_aligned × 4, okx_contrary × 4, bybit_aligned × 3, consensus_aligned × 3, consensus_contrary × 3 | 27 |
| `validation_multi_asset.py:271-279`: okx_aligned z ∈ {0, 0.5, 1.0} × {BTC, ETH, OP} | 9 |
| `validation_audit_2026_09/audit_backtests.py:436`: flat-max OKX z ∈ {−0.5, −0.25, 0, 0.25, 0.5, 1.0} × {BTC, ETH} | 12 |
| `validation_confidence_leverage.py:189-205`: `feature_edge_table`, `okx_delta_abs_z` split into quartiles Q1–Q4 with mean R per cell | 4 |
| This study: one decision cell (R1, z ≥ 0, pooled) | 1 |
| **N_TRIALS** | **53** |

- Summed, not multiplied: the searches were sequential (the `validation_audit_2026_09`
  convention).
- **53 is a floor.** Known uncounted looks: the memory's claim that tighter thresholds
  compound was read off C4's table; the fixed z ≥ 0 re-uses in `run_overlays.py:163-165`,
  `attribution.py:135-136` and `score_variants.py:50-51` are the same cell as C4's z ≥ 0 and
  are not counted again.
- **N enters only K1** (`grid_size`, the √N Sharpe deflation) and the report-only DSR lines.
  R-a, R-b and D do not use N. A different N can move a KEEP, never a RETIRE.
- R2, per-asset, same-hour, portfolio and re-cut cells cannot upgrade a verdict, so they are
  not counted.
- The study appends family `chento_okx_gate`, n=1, to `results/n_trials.json`
  (`gates.n_trials_ledger`) once, in the run that reaches a verdict.
- DSR is reported at 1, 27 and 53.

---

## 6. What this study changes, and what each outcome permits

**Not changed inside this study, under any outcome:**
- `bots/chento_v3/strategy/config.py:89-91`;
- the loaders in `signal.py`;
- `bots/chento_v3_eth/*`;
- the feed's OKX throttle;
- goldens and tests;
- the running processes (no start, stop or restart);
- the published C4 / overlay / audit outputs, which are not regenerated in place.

All study output goes under `studies/notebooks/okx_gate_revalidation/results/`. The roadmap
section of `BACKLOG.md` is updated in the same commit as the verdict (or the INVALID record).

**The plain consequence of decision 2.** RETIRE and INCONCLUSIVE both switch the gate off.
**KEEP is the only verdict that leaves the gate on, and §4.6 rates KEEP near-unreachable, so
gate-off is the most likely end state of this study.** That includes a gate that
discriminates in FULL but fails the deflated K1, and a gate whose per-asset signs disagree.
Only INVALID leaves the gate on without a verdict, and it permits one rerun.

**KEEP permits:**
- removing the "UNVERIFIED" label from memory `project_cross_exchange_okx_gate` and the
  calibration log, replacing −25 % / +34 % with this study's causal numbers (drawdown
  labelled report-only).

It does **not** permit any threshold, window or venue change, and it does not lift the
strategy-level caveat (nothing clears DSR 0.95).

**RETIRE and INCONCLUSIVE both lead to `FILTER_OKX_ALIGNED = False` on both chento bots.**
The direction is decided (2026-09-13). The change is one commit, made after `findings.md` is
committed, containing:
- `FILTER_OKX_ALIGNED = False` (`bots/chento_v3/strategy/config.py:89`; the ETH leg resolves
  the same module, `bots/chento_v3_eth/runner.py:24-28`);
- a calibration-log row in `docs/calibration/chento_triple_v3.md` naming the verdict;
- a golden re-baseline (`chento_btc_okx_blocked` loses its meaning);
- a roadmap update;
- a restart of both bots.

The fire rate would roughly double, so a sizing and concurrency review under `RISK_PCT 2 %`
and the 3× cap is a precondition of that commit's timing, not of its direction. The operator's
go-ahead governs when the commit and restart happen. **The go-ahead sets the date only; it
cannot withhold the change.** If the commit has not landed within 30 days of `findings.md`,
the roadmap records it as overdue. An indefinitely deferred gate-off commit would be a de facto
KEEP.

Both also record, without re-cutting, that every downstream chento study scored on the
same-hour `okx_delta_z` (§0.2 list) needs its own decision. Neither permits a replacement
gate: tighter z, a different window, Bybit, deliberately lagged z, or a size modulator for
misaligned trades. Each needs its own pre-registration at `N_TRIALS ≥ 54`. The memory label
becomes "RETIRED (causal re-test)" or "SWITCHED OFF — INCONCLUSIVE (causal re-test)"; the
−25 % / +34 % figures may not be cited in either case.

**INCONCLUSIVE additionally schedules one HOLDOUT re-cut, record-only.**
- **Gate state during accrual:** OFF on both bots from the change commit onward (ON between the
  verdict and that commit). The re-cut replays snapshot data, not the live ledger, so the live
  gate state does not affect it.
- **What it can do:** relabel INCONCLUSIVE as RETIRE if the §4.3 RETIRE clause holds on the
  re-cut window. It changes no configuration (the gate is already off) and **can never switch
  the gate back on**; reinstating the gate needs a new pre-registration.
- **Snapshot rule:** the re-cut database is a byte copy of the frozen snapshot (hash checked
  against `results/snapshot.json`), so every row with `timestamp < 2026-09-12 00:00` — and
  therefore every trigger with `t < 2026-09-08` and its full path — is reused unchanged. Rows
  from `prod.db` (`?mode=ro`) with `2026-09-12 00:00 ≤ timestamp < C` are appended by INSERT
  only; no existing row is replaced. `C` = 00:00 UTC of the check day.
- **Window:** `t` in [2026-05-27 00:00, C − 4 days). The ~24 triggers of
  [2026-05-27, 2026-09-08) (15 BTC + 9 ETH in the research pool before filter 4) are included,
  and their causal outcomes will already have been seen in this study's HOLDOUT table; this is
  disclosed, not corrected.
- **Trigger count:** on the 1st UTC day of each month from 2026-11-01, run §9 steps 1–3 only
  (snapshot append, pool, features; no outcome is computed) and count pooled OFF-arm triggers
  (filters 2 and 4 applied, NaN z included) in the window. The re-cut runs at the **first**
  check where the count is **≥ 60**, with `C` of that check frozen; no later extension. If 60
  is not reached by the 2028-09-01 check, the re-cut is abandoned and the label stays.
  (Estimate from pool row counts, not an outcome: 396 pooled triggers in 5.4 years ≈ 70 a
  year before filter 4, ~24 already in the window, so around 2027-03.)
- **Preconditions (re-cut):** P0 on the six bars; the frozen `results/pool_{asset}.csv` rows
  with `t < 2026-09-08` reproduced exactly; P2 fidelity ≥ 0.80 on the window's triggers;
  POWER pooled `n_K ≥ 20`, `n_B ≥ 20`, per asset `n_K ≥ 5`, `n_B ≥ 5`. Any failure: the
  label stays and no rerun is permitted.
- **Statistics:** R-a, R-b and D exactly as §4.3, with the day axis = UTC dates from 2026-05-27
  to the last entry day of the window inclusive, `block = 30`, `n_iter = 10000`, `seed = 42`.
- **K1 is not evaluable on the re-cut:** the window is shorter than the 730-day fit window,
  so `walk_forward_folds` returns no fold (`gates.py:98-117`), `gate_metrics` yields NaN and
  K1 fails. KEEP is not reachable from the re-cut, and `N_TRIALS` plays no role in it (the
  draft's "N_TRIALS = 50" is removed).

**INVALID permits nothing** beyond the one repair-and-rerun of §4.4.

**Separate OPS recommendation, whatever the outcome (not part of this study; its own go-ahead
and commit).** Align the OKX refresh to HH:01 instead of the elapsed-time throttle
(`data/sources/binance.py:1096-1098`). With every closed hour landing ~1 min after its close,
live at `now = t + 15m + 5s` sees `floor_hour(t) − 1h` on every bar, so the live information set
becomes R1 exactly and the R1/R2 mixture of §0.3.1 item 6 disappears. It matters for any
future use of `okx_delta_z` even if the gate is switched off.

---

## 7. Known threats

1. **Trigger-pool look-ahead.**
   - The primary pool does not inherit research's ±24h forward selection
     (`validation_B_composite.py:66` `abs(delta_h) <= 24`); it uses `intersect_backward`
     (`validation_group_A_tuning_backonly.py:44`). C4's own pool did inherit it (C4:174).
   - Residual non-look-ahead mismatches: research's B1-level 6h cooldown
     (`b1_triggers :119, :139`) versus the bot's cooldown from its own entries
     (`signal.py:743`); research trigger-list intersection versus the bot's per-bar windows
     (`math.py:252`). Measured by P2 (INVALID below 0.80) and by precision (report-only).
2. **The gate changes the production sequence.** A blocked trigger starts no cooldown, and
   skip-after-loss depends on the taken sequence. The K/B partition at tilt=none approximates
   the production counterfactual. The sequence portfolio is report-only and itself an
   approximation (§4.5).
3. **Small n.** C4 had 114 gated BTC trades. The backward-only pools are smaller than the
   bidirectional ones (the calibration log's backward-only ETH pool is n=73 gated). KEEP is
   expected to be unreachable (§4.6). R-b holds on noise when the true gate effect is zero or
   small, and holds most of the time even at C4's 0.20R. That is the consequence of decision 1
   at this sample: a gate whose discrimination cannot be distinguished from zero while it
   blocks profitable trades is retired. It is not a breadth policy; decision 1 rejected that
   framing.
4. **Multiple comparisons across z thresholds.** Only `z ≥ 0` is computed. Nothing is swept,
   and no threshold may be proposed from this study. `N_TRIALS = 53` counts the looks already
   taken and is a floor; it enters only K1.
5. **Selection is in-sample.** C4 saw 2021-01 → 2026-05-26; HOLDOUT was unseen at the selection
   but its same-hour outcomes were seen later (§3).
6. **Live information-set mixture (feed lag).** The OKX refresh is an elapsed-time throttle
   (`binance.py:1096-1098`), not hour-aligned, so each closed hour lands 0–56 min late
   (measured once: ~9.5 min). Under the uniform-lag model live decides on the R2 set on ~73 %
   of :00, ~46 % of :15, ~20 % of :30 and 0 % of :45 bars — roughly a third of decisions if
   trigger minutes are uniform. The database cannot show the real share (no write timestamp).
   The six live values matching R1 do not settle it (§0.3.1: probability ≈ 0.12 under the
   model). R1 is primary because it is the bot's own replay path and the modal live set; R2
   enters K3 and can only downgrade a KEEP. RETIRE is evaluated on R1 only; a gate that fails
   to discriminate on the fresher R1 set is not expected to discriminate on the staler R2 set.
   The OPS recommendation of §6 removes the mixture going forward.
7. **Data mutation.** LSR trailing-30-day overwrites and table backfills are handled by the
   snapshot. HOLDOUT LSR rows may differ from what live saw.
8. **Not modelled.** Funding on 72h holds is a cost on longs, and K and B may differ in
   long/short mix, which is reported. The notional cap and concurrent-position risk are not in
   R. Mark-to-market drawdown is computed but report-only (§4.5).
9. **Binance-side construction differs from C4** (1h klines, Bybit intersection). The study
   compares against its own same-hour arm, never against C4's numbers.
10. **Operational hazards.** Diag leaking into running bots' JSONL (P0d-i); read-write connects
    in research loaders pointed at `prod.db` (P0d-ii); ledger-module import (P0d-iii);
    `gen_trades.gen` overwriting committed files (not imported, §0.4); the ~0.2 s per frame
    rebuild is wall-time only.
11. **The authors are not blind.** The authors of this pre-registration have already seen the
    backward-only pool's outcomes and its **same-hour** gated split (overlay study, validation
    audit, attribution layer, LSR B5 study, 2026-08 → 2026-09; §0.2). The same-hour control
    arm's result is therefore largely predictable in advance, and so is the sign of the pool's
    mean R, which R-a depends on. What has not been seen is the R1 split. The rules above were
    written with that knowledge; a reader should weigh them accordingly.

    **One further look, made while setting P3 (2026-09-13).** To choose P3's tolerances from a
    measurement rather than a guess, the authors read the live ledger exits of SJ-4243, SJ-4245
    and SJ-4248 and ran the §2.4 walker on them: exit kind, exit time and exit price (§2.4
    table). Those three bars are IN the BTC pool, fall in HOLDOUT (t ≥ 2026-05-27), and all
    three pass the causal gate — so they are three members of the kept set K. Their outcomes
    were already recorded in the ledger before this document, and no kept-vs-blocked statistic
    was computed; but it means three K trades' exits were seen before freezing, and a reader
    should count that. They are 3 of the pooled trades; they cannot by themselves move a
    30-day-block bootstrap over 1,987 days, but they are not unseen.
12. **INVALID as an escape hatch.** A double INVALID leaves the gate on without a verdict.
    Mitigations: P0, P1, P2 and POWER are computed from features, and P3 from closed ledger trades, before any outcome exists; degenerate gate_metrics is checked in memory before verdict.json; a
    repair may not change any frozen value; outcome values are never written or printed by a
    failing run.

---

## 8. Priors (written before computing)

- **P(P0 passes) ≈ 0.95.** The six live values were already reproduced with `math.py`
  (five bit-exact, one at 4.4e-16). The remaining risk is float noise from the 30-day window
  start inside the replay rebuild, which the 1e-9 tolerance absorbs.
- **P(INVALID on the first run) ≈ 0.20**, mostly P1 exact reproduction and P2 fidelity.
- **Conditional on a valid run: P(KEEP) ≈ 0.03, P(RETIRE) ≈ 0.45, P(INCONCLUSIVE) ≈ 0.52.**
  - (i) Mechanism: a cross-venue lead measured at hourly closes is a minutes-scale effect,
    and C4's same-hour z contained up to 45 minutes of prices after entry. The causal z is
    15 to 60 minutes stale at the decision, up to 120 minutes on the R2 share.
  - (ii) Even with the peek, aligned-vs-contrary was only 0.20R, inside the expected CI.
  - (iii) A 31 % sign disagreement makes the causal gate a noisy copy of a weak selector.
  - (iv) The chento pool's mean R is positive, so R-a is expected to hold.
  - (v) With a near-zero true gap, D fails about half the time; that, not R-a or R-b, is the
    most likely reason a gate-off outcome is labelled INCONCLUSIVE rather than RETIRE.
- **Gate-off is the most likely end state: ≈ 0.97 of valid runs, ≈ 0.9 overall including the
  permitted rerun.** Under decision 2, KEEP is the only verdict that leaves the gate on and it
  is near-unreachable. This is stated plainly so that nobody reads a gate-off result as news.
- **P(same-hour control also meets RETIRE) ≈ 0.6**, given (ii) and §7.11; if it does, the
  verdict does not bear on the look-ahead premise and `findings.md` says so.
- If the causal K − B gap exceeds the same-hour gap (§4.5), treat it as a red flag for a
  pipeline error, not as good news.

---

## 9. Files and run order

| # | File | Output | Gate |
|---|---|---|---|
| 1 | `snapshot.py` | scratch snapshot DB + `results/snapshot.json` | — |
| 2 | `gen_pool.py` | `results/pool_{BTC,ETH}.csv` (t, direction only) + `results/pool_parity.json` (P1) | asserts P0d, incl. the five research `DB` constants |
| 3 | `bot_features.py --asset {BTC,ETH}` (one process per asset) | `results/features_{asset}.csv` (entry, atr, risk, dist_R, ret_30d, z_R1, z_R2, anchors) + `results/fidelity.json` (P2, precision, sign-disagreement rates) | refuses without step 2; asserts P0d |
| 4 | `parity_check.py` | `results/parity.json` (P0a-d) | asserts P0d |
| 5 | `outcomes.py` | first `results/preconditions.json` (P0, P1, P2, P3, POWER; no outcome), then the in-memory degenerate-gate_metrics check; then, only if all pass, `results/trades_{asset}.csv`, `results/verdict.json`, `results/report_pre.json`, `results/n_trials.json`; on any failure `results/invalid.json` and no outcome file | evaluation order §4.4; asserts P0d |
| 6 | `same_hour_report.py` | `results/report_post.json` (control arm, required statement flag) | refuses unless `verdict.json` exists |
| 7 | `findings.md` | verdict, clause-to-number table, control-arm statement, discussion | appended after step 6 |
| — | `recut.py` (INCONCLUSIVE only) | `results/recut/…` per §6 | monthly count via steps 1–3 only |

This README is never edited after commit. Any later change — including the one repair
permitted after an INVALID — is a dated addendum at the top that does not alter the body.

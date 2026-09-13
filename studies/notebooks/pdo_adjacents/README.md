# S-PDO-adjacents — PRE-REGISTRATION

**Written and frozen: 2026-09-08, before any outcome number was computed.**

> **Addendum 2026-09-13 — paths only, nothing else.** The body below is frozen and
> unaltered. The PDO sleeve moved from
> `strategies/sleeves/timing_anomalies/internal/pdo/` to
> `studies/material/archive/pdo/` when the eight dormant sleeves were archived; read
> every path below with that substitution. The code itself is byte-identical, and
> `parity_check.py` still exits 0 against it (4,224 timestamps, re-run on the archive
> commit) — which is why the archive was kept importable rather than dropped to a git
> tag. See [studies/material/archive/README.md](../../material/archive/README.md).
> The open question (b) — PDO's re-validation — is unaffected and still open.

Two independent questions about the neighbourhood of the live PDO sleeve
(`strategies/sleeves/timing_anomalies/internal/pdo/`).

- **(a)** Is the live regime threshold of **−10 %** better or worse than the
  predecessor repo's **−7 %**?
- **(b)** Does a **CDO retouch** variant (current-day-open retouch, the
  intraday sibling of PDO's prior-day-open retouch) have a tradeable edge?

Neither question may cause a change to production code inside this study.
`strategies/**` and `bots/**` are read-only here. Any recommendation to edit
`strategies/sleeves/timing_anomalies/internal/pdo/config.py` is a
recommendation only.

---

## 0. Data, costs and conventions (shared by both questions)

| Item | Frozen value |
|---|---|
| Database | `data/databases/prod.db`, opened **read-only** (`?mode=ro` URI) |
| BTC / ETH price | `btc_1m`, `eth_1m` (1-minute bars, `open_time` in ms, UTC). Coverage starts 2020-01-01. |
| BTC regime feed | `cd_spot_binance` (1-hour BINANCE:BTCUSDT spot, `timestamp` = bar OPEN in seconds) — the exact table the live sleeve reads. |
| Hourly bars | Aggregated from the 1-minute tables exactly as the sleeve does: `low = MIN(low)`, `high = MAX(high)`, `close =` close of the last 1-minute bar of the hour. |
| Study window | 2020-01-01 00:00 UTC → 2026-09-08 (end of available 1m data). |
| ETF era cut | `2024-01-11` (pre-ETF = strictly before, post-ETF = on/after). Reported for both questions in addition to pooled. |
| Round-trip cost | **18 bp** (repo research convention). The live sleeve charges 10 bp + funding; 10 bp is reported alongside as a secondary number, never as the decision number. |
| Funding | Not modelled (same simplification the live sleeve makes; see the sleeve README caveat). Recorded as a known bound on both answers. |
| RNG | Every bootstrap uses `seed=42`, `n_iter=10000`. Scripts are deterministic and re-runnable. |

### Parity requirement (repo standing rule for ports)

`signal.py` computes its features against a live clock and writes trades to
the DB, so its *dispatch* function cannot be replayed here. Its *feature*
functions can. The study therefore:

1. Vectorises the three feature functions
   (`_load_today_open_and_pdo`, `_get_hourly_bar_for_today`,
   `_btc_30d_return_pct`) into numpy arrays.
2. Re-runs the **sleeve's own functions** under a frozen clock
   (`clock.set_simulated_now(T)`) and asserts **exact float equality**
   (`==`, not `isclose`) against the vectorised arrays for
   `pdo`, `today_open`, `gap_pct`, hourly `low`/`high`/`close`, and
   `btc_30d_pct`.
3. Parity timestamps = **every hourly timestamp at which the vectorised
   model fires an entry under either threshold**, plus a deterministic
   pseudo-random sample of **2,000 additional hourly timestamps** spread
   across the whole history, per asset.
4. **If any parity assertion fails, both questions are reported
   INCONCLUSIVE.** No result is reported off an unverified re-implementation.

The sleeve opens `db.TRADER_DB` in read-write mode. To honour the read-only
rule the study monkeypatches the `sqlite3` handle *inside the imported sleeve
module object* so its `connect()` returns a `?mode=ro` connection. The sleeve
source file is not touched.

Only the **trade-management loop** (one trade per day, entry at the touching
bar's close, scheduled exit) is copied into the study, because the sleeve's
version of it writes to the trades table. Its rules are transcribed from
`signal.pine` + `signal.py` and listed in full in §1.2.

---

## 1. QUESTION (a) — regime threshold −10 % vs −7 %

### 1.1 Hypothesis

The live config gates entries on `btc_30d_return_pct >= REGIME_THRESHOLD_PCT`
with `REGIME_THRESHOLD_PCT = -10.0`. The predecessor repo used `-7.0`.
`-7.0` is the **stricter** filter: it blocks every setup whose BTC 30-day
return sits in the half-open band **[−10 %, −7 %)**. Those blocked trades are
the only thing that can differ between the two configurations; everything
else in the two backtests is identical by construction.

H_a: trades taken in the [−10 %, −7 %) band have materially negative
expectancy, so removing them lifts mean per-trade outcome by ≥ 5 bp on both
assets out-of-sample.

### 1.2 Frozen signal and execution rules (identical for both thresholds)

Straight from `config.py` / `signal.py` / `signal.pine`:

- `PDO` = open of the first 1-minute bar of the **previous** UTC day.
- `CDO` (`today_open`) = open of the first 1-minute bar of the **bar day**.
- `gap_pct = (CDO − PDO) / PDO × 100`; setup requires `gap_pct >= 2.0`.
- Regime: `btc_30d_pct >= threshold`, where `btc_30d_pct` is whatever the
  sleeve's `_btc_30d_return_pct()` returns at the evaluation hour. Threshold
  is the only thing varied: **−10.0** vs **−7.0**.
- Touch: the just-closed hourly bar satisfies
  `low <= PDO × 1.001` **and** `high >= PDO × 0.999`
  (`TOUCH_TOL_PCT = 0.10`).
- The "bar day" of the hourly bar closing at `T` is the UTC date of `T − 1h`
  (`_bar_day_start`). Setup-day D can therefore fire from `D 01:00` UTC
  through `D+1 00:00` UTC inclusive.
- **One entry per (asset, bar day)**, at the **first** qualifying bar; no new
  entry while a position is open (Pine `strategy.position_size == 0`).
- Entry price = close of the touching hourly bar (Pine
  `process_orders_on_close = true`).
- Exit at `min(entry_time + hold_hours, bar_day_start + 1 day + 1 hour)`,
  priced at the close of that hourly bar. `hold_hours` = **24 (BTC)**,
  **4 (ETH)** from `HOLD_BARS_BY_ASSET`.
- **No stop loss** (the sleeve has none).
- Direction: **LONG only**.
- Cost: 18 bp round trip subtracted from every trade.

Outcome per trade, in basis points:
`bp = (exit_price / entry_price − 1) × 10000 − 18`.

Because the cost is a constant per trade, the *difference* between the two
thresholds is invariant to the cost level — the 5 bp clause below cannot be
manufactured by the cost choice.

### 1.3 Periods

| Label | Definition |
|---|---|
| OOS | entry date **≥ 2026-04-01** (through 2026-09-08) — the decision window |
| IS | entry date **< 2026-04-01** (from 2020-01-01) — reported so the reader sees whether the change would have hurt historically |
| pre-ETF | entry date < 2024-01-11 |
| post-ETF | entry date ≥ 2024-01-11 |
| pooled | full 2020-01-01 → 2026-09-08 |

### 1.4 Decision clauses (pre-registered)

Let `m7(P, A)` and `m10(P, A)` be the mean per-trade outcome in bp over
period `P` for asset `A` under thresholds −7 and −10.

| Clause | Statement | Fires ⇒ |
|---|---|---|
| **A1** | `m7(OOS, BTC) − m10(OOS, BTC) >= +5.0` bp **AND** `m7(OOS, ETH) − m10(OOS, ETH) >= +5.0` bp | necessary for adoption |
| **A2** | `m7(IS, BTC) − m10(IS, BTC) >= 0.0` bp **AND** `m7(IS, ETH) − m10(IS, ETH) >= 0.0` bp ("in-sample result is not worse") | necessary for adoption |
| **A-VERDICT** | **A1 AND A2** ⇒ recommend the −7 % constant. **Anything else** ⇒ recommend keeping −10 %. | — |

Supporting numbers reported but **not** decision-bearing: trade counts,
median, win rate, Sharpe, max drawdown, DSR at `N_TRIALS=2`, and the outcome
of the **discriminating trades** (the ones in the [−10 %, −7 %) band) on
their own, per era.

**Power / degenerate cases, decided in advance:**

- If **zero** discriminating trades fall in the OOS window, then
  `m7(OOS) ≡ m10(OOS)`, the difference is exactly 0 bp, A1 does **not** fire,
  and the pre-registered default stands: **keep −10 %**. This is a valid
  verdict, not an INCONCLUSIVE — but the findings must say plainly that the
  OOS window carried no discriminating evidence.
- If any parity assertion in §0 fails, the verdict is INCONCLUSIVE.
- The OOS window is ~5 months. At the sleeve's historical fire rate
  (~15/yr BTC, ~20/yr ETH filtered) that is roughly 6 BTC and 8 ETH trades in
  total, of which only the subset inside the [−10 %, −7 %) regime band can
  discriminate. **This study is expected to be underpowered on the OOS
  clause and that is exactly why the pre-registered default is "keep".**

### 1.5 Trial count

Two thresholds are compared. `N_TRIALS = 2` for any deflation reported under
question (a). No other parameter is varied; gap, tolerance and hold are
frozen at the live values.

### 1.6 Priors (written before computing)

- P(verdict = keep −10 %) ≈ **0.80**. The Pine reference's own tooltip says
  *"−10 % is pre-committed; −7 % appears stronger in-sample audit but is
  post-hoc"* — i.e. the −7 % number is already flagged upstream as a
  post-hoc selection. A post-hoc threshold that survives its own in-sample
  audit is the textbook case that fails out-of-sample.
- P(the OOS window contains ≥ 4 discriminating trades) ≈ **0.25**. BTC 30-day
  returns spend little time in a 3-point-wide band, and a gap-up setup day is
  anti-correlated with a −10 %-ish regime.
- Expected magnitude of any real difference: **< 5 bp**, i.e. below the
  adoption bar even if the sign favours −7 %.

---

## 2. QUESTION (b) — the CDO retouch variant

### 2.1 What it is

PDO trades the retouch of *yesterday's* open after a gap away from it. The
CDO variant trades the retouch of **today's own open** after an intraday
excursion away from it. Same mechanism (a session anchor acting as
support/resistance), one timeframe tighter, and — unlike PDO — it is
naturally two-sided.

No predecessor spec for this exists anywhere in this repo (`grep -i cdo`
finds only the `CDO` plotting variable in `signal.pine` and its mirror in the
sleeve docstring). **The definition below is therefore mine, and it is frozen
here before any outcome is computed.** It is deliberately a direct structural
translation of PDO — every threshold that has a PDO counterpart is set to the
PDO value so that nothing is tuned.

### 2.2 Exact definition (frozen)

**Level.** `CDO` = the open of the first 1-minute bar of the current UTC day,
for the asset being traded. Identical construction to the sleeve's
`today_open`.

**Excursion (the "gap" analogue).** Working forward through the day's hourly
bars, track
`up_exc = max(high) / CDO − 1` and `dn_exc = 1 − min(low) / CDO`
over every hourly bar from the day's first bar up to and including the bar
being evaluated. A direction becomes **armed** when its excursion first
reaches **2.0 %** — the same number as `GAP_THRESHOLD_PCT`.

**Trigger.** On the close of an hourly bar of the same UTC day, when
- at least one direction is armed, **and**
- that bar's range contains the CDO band:
  `low <= CDO × 1.001` and `high >= CDO × 0.999`
  (`TOUCH_TOL_PCT = 0.10`, the PDO value), **and**
- no CDO trade has yet been taken for this (asset, UTC day), **and**
- no CDO position is currently open.

**Direction.** The armed direction with the **larger excursion at the trigger
bar** wins. Up-excursion armed ⇒ **LONG** (CDO as support, the exact analogue
of PDO's long-after-gap-up). Down-excursion armed ⇒ **SHORT** (CDO as
resistance). Ties (both armed and exactly equal) are impossible in floating
point and are recorded as skipped if they occur.

**Entry.** Market at the close of the trigger hourly bar. One trade per
(asset, UTC day).

**Stop.** Fixed **1.00 %** adverse from the entry price
(long: `entry × 0.99`; short: `entry × 1.01`). Checked on **1-minute** bars
inside the hold window. **This 1.00 % is the definition of 1R** — it exists
so that "mean R" in the pass rule has a denominator. PDO itself has no stop;
that is noted as a structural difference, not a tuned parameter.

**Target.** Fixed **2.00 %** favourable from the entry price = **+2.0R**
(long: `entry × 1.02`; short: `entry × 0.98`), also checked on 1-minute bars.

**Same-minute ambiguity.** If a single 1-minute bar's range contains both the
stop and the target, the **stop is assumed to fill first** (conservative).

**Time in force.** `min(entry_time + 24h, day_start + 1 day + 1 hour)` —
structurally identical to PDO's exit rule with `hold_hours = 24`, priced at
the close of that hourly bar. Whichever of stop / target / TIF comes first
ends the trade.

**Regime filter.** **None.** Adding one would be a third dial and would
inflate the trial count; question (a) is where the regime threshold is
examined.

**Cost.** 18 bp round trip = **0.18 R** (since 1R = 1.00 %), subtracted from
every trade regardless of exit type.

**R accounting.**
`R = signed_price_move_pct / 1.00 − 0.18`, where `signed_price_move_pct` is
`(exit/entry − 1) × 100` for longs and `(entry/exit − 1) × 100` for shorts.
So a clean stop is −1.18 R, a clean target is +1.82 R, and a TIF exit is
whatever the bar closes at, minus 0.18.

**Assets.** BTC and ETH, 2020-01-01 → 2026-09-08.

### 2.3 Decision clauses (pre-registered)

The primary series is the **pooled** BTC+ETH trade list (both directions),
sorted by entry time. All three clauses must fire.

| Clause | Statement | Tool |
|---|---|---|
| **B1** | The bootstrap **5th percentile** (one-sided 95 % lower bound, `n_iter=10000`, `seed=42`) of **mean R** is **strictly > 0**. | `studies.lib.validation.bootstrap` — `bootstrap_sharpe` gives the matching per-trade-Sharpe interval (per resample, `SR > 0 ⟺ mean > 0`, so the sign test is identical); the mean-R percentile itself is computed with the same iid percentile resampling, same seed. |
| **B2** | **≥ 3 of the 4 complete walk-forward folds** have positive mean R on their OOS window. Folds = `gates.walk_forward_folds(entry_dates, fit_days=730, oos_days=365, step_days=365)` (the `FOLD_PRESETS["event"]` preset). Folds are numbered in time order; the clause is evaluated on the **first four folds whose OOS window is a complete 365 days**. Any trailing partial fold is reported for information only and is excluded from the count. | `studies.lib.validation.gates` |
| **B3** | `dsr_from_returns(R, n_trials=2)["dsr"] > 0.95`. | `studies.lib.validation.dsr_pbo` |
| **B-VERDICT** | **B1 AND B2 AND B3** ⇒ recommend building. **Any clause failing ⇒ KILL.** | — |

Degenerate cases decided in advance: if the pooled trade count is < 30, or
fewer than 4 complete folds exist, B2 is unevaluable and the verdict is
**INCONCLUSIVE** naming that clause.

Reported but not decision-bearing: per-asset and per-direction breakdowns,
pre-ETF / post-ETF / pooled eras, win rate, exit-type mix (stop / target /
TIF), Sharpe, max drawdown in R, and the same three statistics on the
long-only sub-series so the reader can see whether the short leg is what
kills it.

### 2.4 Trial count

`N_TRIALS = 2`, as instructed: exactly two configurations are evaluated —
BTC and ETH — with every parameter frozen at its PDO counterpart. **No
parameter sweep is run.** If any sweep were run later, the trial count for
any subsequent decision would have to grow accordingly.

### 2.5 Priors (written before computing)

- P(KILL) ≈ **0.85**. This is the expected outcome and I am saying so before
  looking. Reasons: (i) the intraday open is a far weaker anchor than the
  prior-day open — it has no overnight positioning built around it and is not
  a level any exchange or index publishes; (ii) a 2 % intraday excursion
  followed by a full retrace to the open is, on crypto perps, mostly just
  volatility, and the 18 bp cost eats a meaningful slice of a 1R = 1 % risk
  unit; (iii) the short leg has no analogue in the validated PDO work, which
  is long-only, and the repo's own history (`feedback_tight_tsl…`,
  `project_lsr_b5_study`) is that symmetric extensions of a validated long
  edge usually fail; (iv) three simultaneous clauses, one of which is a DSR
  above 0.95, is a high bar for a signal with no prior evidence at all.
- Most likely failure clause: **B3** (DSR), then **B2** (fold stability).
- P(B1 fires on its own) ≈ 0.3 — a mildly positive mean R is plausible;
  surviving all three is not.

---

## 3. Files

| File | Purpose |
|---|---|
| `README.md` | this pre-registration |
| `common.py` | shared read-only loaders + vectorised sleeve features |
| `parity_check.py` | asserts byte-equality vs the live sleeve → `results/parity.json` |
| `question_a_regime.py` | PDO backtest at both thresholds → `results/qa_*.csv/json` |
| `question_b_cdo.py` | CDO retouch backtest + the three gates → `results/qb_*.csv/json` |
| `build_notebook.py` | writes the viewer notebook (run with `C:/Python/Python313/python.exe`) |
| `findings.md` | verdicts, clause-to-number tables, honest discussion |

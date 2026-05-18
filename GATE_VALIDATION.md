# Gate validation protocol

This document specifies how a new (or rebuilt) sleeve gate proves itself
before being switched on in the live bot. It exists because two of the
three gates already in production are flagged in BACKLOG and code
comments as having **in-sample selection bias** — they were tuned on
the same series they were tested against. The protocol below is what
should have happened, and what *will* happen for any future gate.

The audience is the person adding the next gate (or auditing an existing
one). The protocol is intentionally small — five steps, no framework.

---

## 1. Why walk-forward at all

A single in-sample backtest answers "did this filter help on the data
I built it from?" — almost always yes, often spuriously. Two failure
modes the codebase has documented evidence of:

- **THU_BEAR V4 event filter** ([strategies/sleeves/timing_anomalies/internal/thu_bear/signal.py:20-25](strategies/sleeves/timing_anomalies/internal/thu_bear/signal.py#L20-L25)):
  derived post-hoc from E4 event-purged CPCV attribution of V3's
  Thursdays. Any backtest that reuses the same CPI/NFP/OPEX series the
  filter was *picked from* outperforms V3 by construction. Until live
  paper accumulates real out-of-sample Thursdays, the V4 vs V3 delta
  in the in-sample replay is informative about curve-fit risk, not
  expected live performance.

- **FOMC composite** ([strategies/sleeves/timing_anomalies/internal/fomc/signal.py](strategies/sleeves/timing_anomalies/internal/fomc/signal.py),
  see [BACKLOG.md](BACKLOG.md) P-300 caveats): "tuned on the same
  52-event historical cohort that informs the live decision rule —
  in-sample selection bias applies."

Walk-forward CV is the cheapest mitigation: it produces an
out-of-sample track record by construction, even when you build the
gate from history. The fold structure forces the rule to generalise
across regimes the picker can't see.

---

## 2. The three existing gates — current evidence

| Gate | Source | What it does | OOS evidence today |
|---|---|---|---|
| R4 vol-gate | `jplus_inputs._gate_for_today` | BTC realized-vol percentile → halve R4 inner leverage | Calibrated on BTC vol pctile from 2019+; not picked from R4 trades. Lower selection-bias risk; still no formal walk-forward record. |
| THU_BEAR V4 | `thu_bear.signal._v4_passes` | CPI/NFP-adjacent + ex-OPEX → binary block | **In-sample.** Picked from V3 Thursday attribution. |
| FOMC composite | `fomc.signal.evaluate` | Phase × F&G × Polymarket → binary block | **In-sample.** Tuned on the same 52-event cohort. |

The R4 vol-gate is the closest to "validated"; the other two have
known in-sample provenance and are running live as deliberate paper
experiments with documented caveats. The point of this protocol is
that *future* gates should not enter live with this status.

---

## 3. The protocol — five steps

### Step 1 — define the gate's signature

A gate maps `(strategy_id, regime, now_utc) -> GateDecision`
(see [strategies/support/gating.py](strategies/support/gating.py)).
Before any data work, write down:

- **What is gated.** A binary block (`fire=False` skips the entry
  entirely) or a modulator (`leverage_mult < 1` shrinks the sleeve's
  size).
- **What the gate reads.** Be explicit about the input data source
  *and the cutoff convention* — does the gate read T-1 data (always
  safe) or live T data (verify no look-ahead via `clock.now_utc()`).
- **The decision rule** in pseudocode. One paragraph. If you can't
  write the rule before fitting parameters, you're fitting noise.

### Step 2 — pre-register the parameter family

Before touching the data, name every parameter the rule will sweep
over. Examples from existing gates:

- R4 vol-gate: `(window_days, pctile_threshold)` — two knobs.
- V4: `(include_event_types, window_days, exclude_event_types)` — three.
- FOMC composite: `(phase_set, fg_bucket_set, polymarket_threshold)` — three.

A grid of N parameters scanned over the same series multiplies the
multiple-testing bias by ~|grid|. Document the grid size with the
gate. This is what gets compared against the OOS Sharpe at promotion
time (see Step 5).

### Step 3 — walk-forward fold structure

Pick the fold structure based on the gate's natural cadence, not on
a default:

| Gate cadence | Recommended fold |
|---|---|
| Daily signal (R4 vol-gate, EMA cross) | 1-year fitting → 6-month OOS, rolled forward by 6 months |
| Weekly/event-conditioned (V4, FOMC) | 2-year fitting → 1-year OOS, rolled by 1 year |
| Quarterly (regime-allocator family) | 4-year fitting → 2-year OOS, rolled by 2 years |

Two rules to enforce:

- **No fold-boundary leakage.** When the OOS window starts, any
  feature the gate consumes that is computed over a rolling window
  must use only data inside the fitting window. Easiest enforcement:
  recompute features inside each fold's fitting half, not from the
  global series.
- **Stitched OOS series.** The reported metric is computed on the
  concatenation of every OOS half, NOT on the most recent OOS fold
  alone. Cherry-picking the fold that happens to be a friendly regime
  is the easiest way to fool yourself and others.

### Step 4 — metrics

Three numbers per gate:

- **OOS expectancy** in % per fire: `mean(per_fire_pnl_pct)` on the
  stitched OOS series, with the gate ON vs OFF. A gate that doesn't
  raise expectancy is dead weight even if its Sharpe looks fine
  (filtering trades out always reduces variance).
- **OOS Sharpe (annualised)** on the gated equity curve. Compare to
  the ungated baseline. Report both; the gain is the gate's
  contribution.
- **OOS hit-rate.** Specifically the bottom-quartile of trades in
  the ungated series — the gate's job is to remove the bad tail, so
  measure how much of that tail it actually removes.

Optional but recommended:

- **White's reality check** (or any bootstrap-based deflation): with
  N parameter combinations swept, the reported Sharpe should be
  deflated by the search-budget. If you can't run White's, at least
  report `|grid|` so the reader can mentally deflate.
- **Stability across folds.** Variance of the per-fold Sharpe — a
  gate that's strongly positive in folds 1-3 and strongly negative
  in folds 4-6 is fitting regime, not edge.

### Step 5 — promotion criteria

A gate is eligible for live promotion when **all** of:

1. **OOS expectancy uplift ≥ +5bp/fire** vs ungated (modulator gates)
   or **OOS expectancy of blocked trades ≤ -5bp** (binary blocks —
   the gate is removing real losing trades).
2. **OOS Sharpe uplift ≥ +0.2** vs ungated baseline, deflated by
   `|grid|` if a bootstrap reality check wasn't run.
3. **Per-fold Sharpe sign stability**: the gated curve beats ungated
   in at least 2/3 of folds.
4. The artifacts in Step 6 are committed.

A gate that fails any of these is not blocked from existing — it can
run as a *paper experiment* with the in-sample caveat documented in
its docstring (this is the state V4 and FOMC composite are in today).
But it does not get its caveat dropped from the variant spec until
live paper accumulates enough out-of-sample evidence on its own.

---

## 6. Artifacts to produce

When a gate is added or rebuilt, the PR must include:

- **A study notebook** under `studies/notebooks/gates/<gate_name>.ipynb`
  with the walk-forward run. Cells: data load, fold definition,
  per-fold fit + OOS scoring, metric table, equity-curve plot
  (gated vs ungated), per-fold Sharpe bar chart.
- **The fold definition file** (a small JSON or top-of-notebook
  constant) so a reviewer can re-run with different folds and see
  whether the result is stable.
- **A docstring update** on the gate's host function pointing at the
  notebook and stating the headline metric (OOS Sharpe and expectancy
  uplift). The docstring is the single source of truth — operators
  read it; if the notebook lives on but the gate's docstring doesn't
  cite OOS evidence, it counts as in-sample.
- **An entry in [BACKLOG.md](BACKLOG.md) under P2.4b** moving the
  gate from "in-sample" → "OOS-validated" in the evidence table
  in §2.

---

## 7. Worked example: hypothetical "vol-regime allocator"

To make the protocol concrete, imagine adding a gate that toggles the
J+ family between two leverage profiles based on a longer-horizon vol
regime indicator.

- **Signature** (Step 1): modulator. Reads BTC daily realized vol
  over a 90d window at the previous UTC midnight (T-1 strict).
  Returns `leverage_mult ∈ {0.5, 1.0}` based on whether the 90d vol
  is above or below its 5-year rolling 60th percentile.
- **Parameter family** (Step 2): `(window_days, pctile_threshold)`
  grid = `{60, 90, 120} × {50, 60, 70, 80}` = 12 combinations.
- **Fold structure** (Step 3): daily cadence → 1y fitting / 6mo OOS,
  rolled by 6mo. Over 2019-01 → 2026-04 that's ~13 OOS folds.
- **Metrics** (Step 4): on stitched OOS, compute J+ family Sharpe
  with `leverage_mult` applied vs ungated. Report expectancy uplift
  per R4 fire, Sharpe uplift, and per-fold Sharpe spread.
- **Promotion** (Step 5): if Sharpe uplift ≥ 0.2 deflated by √12 ≈
  +0.058 minimum raw Sharpe gain per knob, AND the gain is positive
  in 9/13 folds, the gate gets registered in
  [strategies/support/gating.py](strategies/support/gating.py) and
  consumed by the R4 handlers via `_effective_gate.leverage_mult`.

The point of the worked example is to make the per-step work small
and concrete. A gate that resists this much structure is a gate that
wasn't ready for live anyway.

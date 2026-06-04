# Notebook 1 (Group A) — interim summary

> **Mode: research / experiment, NOT ship.** Findings here inform what to study next, not what to put in the live sleeve. Every "BETTER" verdict is a research observation that needs cross-validation, regime-stratification, and out-of-sample testing before any production change.

Source scripts:
- [validation_A_sleeve_tuning.py](validation_A_sleeve_tuning.py) — baseline + A2 + A3 + A5 + A6 + A7
- [validation_A4_bounded_ladder.py](validation_A4_bounded_ladder.py) — A4 with 3 rung/stop variants
- [validation_A9_A10_composite.py](validation_A9_A10_composite.py) — A9 + A10 + composites C1/C2/C3
- [validation_A1_regime_gated.py](validation_A1_regime_gated.py) — **running**; results to be appended when short-entry collection completes

Output JSONs in [studies/material/chento/validation/](../../material/chento/validation/).

Data: 6.1y of BTC+ETH+OP (155 long entries baseline). Cost model: 10 bps fee + 8 bps slippage RT.

## Baseline v2 long

| Metric | Value |
|---|---|
| n trades | 155 (25.4/yr) |
| mean R per trade | **+0.104** |
| WR | 39% |
| T1 hit | 50% |
| T2 hit | 25% |
| max-DD-p10 per trade | **-1.36R** |
| hold p75 | 94h (≈4 days) |
| Implied annual @ 2% NAV risk | **+5%** |

## Single-knob experiments

| # | Variant | Δ R/trade | Annual % | Notes |
|---|---|---|---|---|
| A2 | TIF=21d (baseline) | 0 | +5% | Tightening fails: 1d=-0.111, 3d=-0.070, 7d=-0.016. 14d marginally beats 21d. |
| A3 | Move stop to BE at T1 | -0.071 | +2% | WR up 39%→50% but kills T2 (25%→18%); worse net R. |
| A5 | Hour/weekday heatmap | n/a | n/a | Too sparse — time gate funnels all fires to one hour. Need wider gate for useful analysis. |
| A6 | Hard leverage cap (alts ≤20x, majors ≤30x) | policy | — | Pure risk policy, not a backtest. RUNE blowup = 100% of extracted losses. |
| A7 | T1 close 25% (vs 33%) | +0.022 | +7% | Leaves more runner for T2/trail. |
| A9 | Dynamic T2 (3R→2R if MFE≥1.5R at T1) | +0.009 | +6% | Fast-T2 fires on only 3% of trades — too rare to matter standalone. |
| A10 mtf_net=-3 | Filter to mtf_net=-3 only | +0.057 | +5% | n=62. Contrarian read: entries when multi-TF is mostly bearish hit harder. |
| A10 mtf_net=+1 | Filter to mtf_net=+1 only | -0.038 | +2% | n=93. Mostly-bullish multi-TF is WORSE for long-side swing-base entries. |
| A10 capitulation `--+++` | Filter to capitulation sig | n/a | — | **0 entries in current cache** — the strict gates eliminate capitulation cases. Worth re-running with looser confluence. |

## A4 bounded ladder-add — the major single finding

| Variant | rung1 | rung2 | hard stop | mean R | WR | T1 | T2 | max-DD-p10 | Annual |
|---|---|---|---|---|---|---|---|---|---|
| Baseline | none | none | -1.0R | +0.104 | 39% | 50% | 25% | -1.36R | +5% |
| **A4-default** | **-0.75R** | **-1.0R** | **-1.5R** | **+0.411** | **54%** | **63%** | **32%** | **-1.76R** | **+23%** |
| A4-tighter | -0.5R | -0.75R | -1.25R | +0.355 | 49% | 58% | 29% | -1.58R | +20% |
| A4-wider | -1.0R | -1.5R | -2.0R | +0.201 | 56% | 66% | 34% | -2.24R | +11% |

**Math check:** With 1.0× original + 0.5× @ rung1 + 0.5× @ rung2 (2.0× total notional) and hard combined-stop, max loss when both rungs fill ≈ 2.1× original-R (default variant). This is the trade-off — we accept 2.1× max single-trade loss for the WR/expectancy uplift.

**A4-default rung-fill rates:** rung1 fires 66%, rung2 fires 56% (so 56% of trades fill both rungs). Hard stop fires 36% of the time — those are exactly the "tail events" the unbounded martingale would expose us to.

## Composites — stacking the wins

| # | Composition | mean R | WR | T1 | T2 | max-DD-p10 | Annual |
|---|---|---|---|---|---|---|---|
| Baseline | v2 default | +0.104 | 39% | 50% | 25% | -1.36R | +5% |
| C1 | A4-default + A7 (25% trim) | +0.457 | 51% | 63% | 32% | -1.76R | +26% |
| **C2** | **A4 + A7 + A9** | **+0.482** | **51%** | **63%** | **36%** | **-1.76R** | **+28%** |
| C3 | A10 cell -3 + A4 + A7 + A9 | +0.705 | 50% | 61% | 40% | -1.73R | +24% |

**C2 is the headline composite:**
- **5× mean R** vs baseline (+0.104 → +0.482)
- **5.6× implied annual** (+5% → +28%) at the same trade frequency
- WR +12 points, T1 hit rate +13 points, T2 hit rate +11 points
- max-DD-p10 worsens by 0.4R — accepted cost
- Hard stop fires 36% of trades — caps the unbounded martingale tail

**C3 narrows to the best MTF cell** (mtf_net=-3): per-trade R climbs to +0.705 (7× baseline) but trade frequency drops from 25.4/yr to 15.4/yr, so implied annual is roughly the same (+24% vs C2's +28%). Quality-vs-quantity trade.

## Connection to the size-doubling observation

The user's 2026-05-23 live observation — chento doubling $1M → $2M underwater for a 25% TP on a small bounce — is the **unbounded** version of what A4 codifies as a **bounded** experiment. Our experiment confirms the *intuition* (averaging-down catches more bounces, materially) while keeping a hard combined stop that the unbounded version explicitly abandons. The 2.1× max-loss multiplier is the price we pay for the +0.40R/trade uplift.

Whether chento's edge is *largely* this bounded version + selection of high-base-rate setups (in which case we may have captured most of it in C2) or whether his unbounded version + signal-stream features (money-flow, liq cascades) adds materially more — we cannot tell from Group A alone. Notebook 2 (Group B signals) is the next instrument that can answer this.

## What's still pending

- **A1 regime-gated direction** — running in background. Will append A1a/A1b/A1c results when short-entry collection completes (ETA ~60min from start).
- **A8 trim/DCA cycling** — re-entry within +0.25R of entry after T1 trim. Mechanically distinct from A4 (which adds at entry-time on the way down); A8 cycles after the trade is open. Deferred for next session.
- **A1 + A4 + A7 + A9 stack** — combine regime gating with the bounded-ladder composite once A1 results land.
- **A10 capitulation `--+++` subset** — currently 0 entries in cache because the strict v2 gates eliminate capitulation cases. Worth re-running with `CONF_SCORE_MIN=1` to see whether capitulation alone carries edge.

## Honest caveats

1. **Sample size:** 155 trades over 6.1y is thin for statistical claims. BTC alone has only 22 trades. Bootstrap-resampling confidence intervals would help but aren't computed yet.
2. **In-sample / out-of-sample:** every variant was tested on the full historical period. We haven't held out a validation set. The +28% annual on C2 could be flattered by overfit on the parameter grid.
3. **Multi-asset confound:** 86% of entries come from ETH/OP, which bypass v2's confluence scoring. The high per-trade R on the composites may concentrate on ETH/OP rather than BTC — we haven't broken results down by asset.
4. **Cost model:** 18 bps RT is conservative for liquid majors but may underprice slippage during the rung-fill cascades.
5. **Stop-placement asymmetry:** the v2 stop sits at base_low × (1 - STOP_OFFSET_PCT). Our A4 ladder rungs and hard stop sit at fractions of R below entry, NOT at structural levels. A future refinement: place the hard stop at a structural level (e.g., 1 ATR below the ladder-2 fill level) rather than a fixed R-multiple.

These caveats matter precisely because the findings *look* good — and that's exactly when discipline about validation is most needed.

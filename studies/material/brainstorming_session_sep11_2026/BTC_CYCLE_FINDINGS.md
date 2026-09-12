# BTC Findings — verified (post red-team)

Spot $62,361 · ATH $126,219 (2025-10-06) · drawdown −50.6% · today 2026-06-19.
Every headline number below was independently re-derived from the raw CSVs by separate
agents and reproduced within <1%; corrections from the red-team are folded in.

## Bias (medium-term): BEARISH / markdown — conditional on regime persisting
- Below bull-market support band: 20W SMA $70,878 · 21W EMA $74,509 (price −16%).
- Sitting **on the 200-week SMA** ($62,251; +0.2%) — the classic cycle-floor test.
- Recent drift negative (−0.16%/day full-sample; −0.7%/day last 30d).
- BUT long-term value building: Mayer 0.81 (<1 = cheap); ~**1.1σ below power-law fair value**.
- Caveat: the bearish read is conditional ("if the current decline regime persists"); a true
  zero-drift random walk centers at spot.

## Do we still follow BTC cycles? Yes — diminishing amplitude.
- Halving→top: 2013 ≈370d, 2017 ≈510d, 2021 ≈539d, 2025 ≈529d (510–540d is a 2-cycle rule).
- Top→bottom: ~12–14 months (365–638d). Modern cycles ≈365d.
- Drawdowns shrinking: −85% (’13–15) → −84% (’17–18) → −78% (’21–22). 3-point trend only.
- Power-law fit (b=5.57, R²=0.96) is strong; the time/exponential log-reg ($535k) is rejected.
- Caveat: ETF flows + macro now dominate magnitude; halving-relative timing does NOT itself
  prove "elongation." Use cycles for direction/timing, flows/macro for magnitude.

## End-July 2026: ~$60–62k, slight downside skew (most defensible output)
Ensemble (fat-tailed MC + block bootstrap), 42-day horizon, vol ann ≈40%, Student-t df≈4.
| drift assumption | median | P(break $59,110) | P(<$52k) | P(>$67k) |
|---|---|---|---|---|
| true zero-drift (martingale) | $61,813 | 37% | 10% | 27% |
| mild bear (90d drift) | $59,518 | 48% | 15% | 18% |
| **ensemble (blend)** | **~$60,500** | **~40%** | **~13%** | **~24%** |
| strong bear (30d pace) — stress | $45,979 | 97% | 83% | 0% |

P25–P75 ≈ **$54.5–66.4k**; P10–P90 ≈ $49–72k. Tail-probability CIs are wide (effective
independent 42-day windows ≈8). Pivots: **$59,110** (break = lower leg) · **$67k** (reclaim = relief).

## Cycle bottom: TIMING robust, MAGNITUDE wide
- **Timing: Q4 2026 most likely** (top + ~365d ≈ Oct 2026); full window Oct-2026 → mid-2027.
- **Magnitude (verified mixture):** median **~$45k**, P25–P75 **$37–53k**, modal ~$40k, P5–P95 $26–60k.
- Model triangulation for an Oct-2026 bottom:
  - Power-law undershoot (2018 −0.9σ … 2022 −1.26σ … 2015 −1.9σ) → $40–79k.
  - 200W SMA $66k × corrected undercut 0.60–0.80× → $40–53k (on-line $66k).
  - Drawdown −65%…−78% → $28–44k (shallow ETF-era end favored by the trend).
- Magnitude is genuinely uncertain (anchors span $16k–$79k); timing (Q4-2026) is the firmer call.

## Confidence-building pass (scripts 09–10 + implied vol)
- **Walk-forward calibration backtest** (215 daily + 290 weekly out-of-sample 42-day/6-week forecasts):
  the raw zero-drift engine was MISCALIBRATED — 90% interval covered only 75–82% (too narrow),
  PIT mean 0.34 in the bear window (realized landed below median ~2/3 of the time).
- **Fix:** inflate vol ×1.2 and include trailing drift → 90% coverage 0.88–0.93, PIT ~0.49 (calibrated).
- **CALIBRATED end-July:** median **$59.5k**, P25–P75 $53.5–66.2k, P5–P95 $45.8–77.4k,
  P(break $59,110)=**48%**, P(<$52k)=20%, P(>$67k)=22%. (Modestly more bearish + wider than first pass.)
- **Orthogonal vol convergence:** market 30-day implied vol BVIV=**44.6%** ≈ realized 43% ≈ calibrated 47%.
  Implied-vol forecast (mild-bear) median $59.6k / P(break)=48% — matches the calibrated engine.
- **Weekly block-bootstrap is well-calibrated** (KS p=0.13); daily bootstrap is NOT (effective n≈8) → trust weekly, downweight daily (confirms red-team).
- Net: confidence UP on method (now validated), headline nudged to median ~$59.5k and P(break $59k)≈45–48% (coin-flip).

## Tier-3 models (scripts 11–14) + master consensus
- **LPPLS (Sornette):** bubble fit on 2023→2025 run-up gives critical time **tc = 2025-10-06 = the exact ATH** (ω=8.6, R²=0.96, valid) → Oct-2025 was a genuine log-periodic bubble peak. Anti-bubble fit on the decline is degenerate (no clean imminent-bottom signal) → bottom not imminent.
- **Regime-switching (Markov, weekly):** 2 states = high-vol (~70% ann) vs calm (~20%). **Currently 71% in the high-vol regime**, expected ~4 weeks → turbulence through end-July. Directional drift unreliable (secular-uptrend bias).
- **Macro:** BTC~SPX corr +0.36 (beta ~1.8) but **SPX +15%/13wk while BTC −50%** → drawdown is **crypto-internal, not macro-driven**. DXY +1.3% = mild headwind. Equities a latent tailwind if BTC re-couples.
- **GARCH(1,1)-t:** persistence 0.94, ν=4.65; 42-day vol forecast **43%** ann, long-run 45%.
- **Volatility convergence (5 independent methods): 43 / 43 / 45 / 45 / 47%** → ~45% — high confidence.
- **END-JULY committee consensus (6 methods): median ~$61.4k, P(break $59,110)=41%, P(<$52k)=15%, P(>$67k)=26%.** Calibrated (most rigorous, more bearish): ~$59.5k / 48%. Reconciled: ~$60k, P(break)≈40–45%.

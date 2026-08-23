# paladin_harvester — CONCLUDED NEGATIVE (2026-08-23)

*The mechanical extract of Paladin's trade: round-1000 bounce entry, 4–5×ATR(1h) stop,
rejection-wick booking (armed ≥0.3R), TIF 72h, 24h cooldown, 18bp costs. Pre-registered
grid (2 stops × trend-gate on/off × long/short/both), BTC `cd_futures_15m` 2020-01→2026-08,
IS ≤2024-12-31< OOS. Kill criteria fixed before the run: net < +0.05R or MAR < 2.*

## Verdict: killed by its own pre-registered criteria

- **IS best**: `k4_gated_long` +0.033R net (gross +0.090), MAR 0.85 — under both floors.
- **OOS: every variant net-negative** (−0.03 to −0.13R); long-side gross collapses to
  +0.02–0.03R, shorts gross-negative throughout.
- The Paladin signature reproduces mechanically — 60–68% WR, 51–67% of exits via wick
  booking, ~3% stops — but small books minus full stops minus costs nets below zero
  without his entry judgment.

## What this settles

The wick-exit's +0.12R on *his* 173 positions does not transfer to mechanical
level-bounce entries: his live selection (and/or the specific May–Aug 2026 regime) was
carrying the difference. Combined with the completed due diligence on his entries — no
affinity vs matched controls across round levels, PDH/PDL, sweeps, day-range position,
EMA200, 4h timing, RS/BTC context, **volume-profile HVN/LVN, fib retracements, and
liquidity-shelf touch density** (`entry_structure.py`; the only signal was inverted:
his losses sat at 2× more-touched shelves than his wins) — **the Paladin thread is
closed for strategy extraction.** Six mechanical extracts, six measured negatives
(H0 setups, scanner sweep-fade, RS-dump, DCA, chento wick-overlay, harvester).

What genuinely transfers survives in already-shipped form: the risk-discipline skeleton
(fixed-R, hard stops, bounded adds, position caps — convergent with ours), the corrected
~70% WR benchmark for what "good discretionary" actually is, and the wick-exit as a
possible overlay **only** for future small-target (~1:1) sleeves, tested per sleeve.

## Files

`harvester.py` (pre-registered spec in docstring), `results/harvester_summary.csv`,
per-variant trade CSVs. Entry-structure due diligence: `../paladin_study/entry_structure.py`
+ `results/entry_structure*.csv`.

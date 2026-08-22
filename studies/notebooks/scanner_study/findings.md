# Scanner study — findings (CONCLUDED NEGATIVE, 2026-08-23)

*Data: 171-symbol Binance USDT-M futures universe (screener_universe ∪ Paladin symbols),
15m bars 2024-01 → 2026-08 (10.75M bars, study-local `scanner_ohlcv.db`). Engine:
`scanner_lib.py` (no-lookahead features, next-bar-open entry, conservative stop-first
ambiguity, 18bp round-trip taker cost converted to R per trade's own risk). Walk-forward:
IS 2024-01→2025-08, OOS 2025-09→2026-08. Liquidity gate $10M 30d-median daily volume.*

## Verdict

**Neither Paladin-derived template survives mechanical transfer. Do not build this sleeve.**

| template | gross R/trade | net R/trade (18bp) | verdict |
|---|---|---|---|
| A sweep-fade short (v1, broad) | +0.03 to +0.06, consistent across 2024/25/26 | −0.05 IS / +0.01 best-OOS | edge real but **below the cost floor** |
| A2 refined (extended-pump-only, pre-registered filters) | ~0.00 | −0.03 to −0.09 | selectivity does NOT concentrate the edge — extended pumps continue, not revert |
| B RS-dump long | **−0.14 to −0.26 gross** | −0.19 to −0.35 | decisively negative; kill |

Supporting detail:

- **A's gross edge is diffuse, tiny, and cost-dominated.** The broad detector (any fresh
  N-day-high sweep + red rejection bar) is gross-positive every year (+0.026/+0.056/+0.036),
  so it is not one regime's noise — but median cost is 0.05R (2.5×ATR stop) to 0.09R
  (1.5×ATR stop), and no variant clears it out-of-sample beyond +0.012R. Risk-adjusted it
  is nowhere near shippable: best MAR-like (total R / max R-drawdown) ≈ 0.4 vs 2–18 for
  our live sleeves.
- **The pre-registered refinement falsified the concentration hypothesis.** Filters taken
  from priors, not grid-mined (close > 24h EMA + 2×ATR, 24h return ≥ +5%, first rejection
  per episode, 2R targets per the wider-TP memory): trade count fell 40/day → 4/day as
  intended, but gross expectancy fell to ~0. The tiny edge lives in the *unselective* mass
  of minor sweeps, i.e. it is microstructure residue, not a fadeable-pump effect.
- **B is the same lesson the Paladin data already taught.** RS-during-dump predicted his
  *selection*, not his *outcome*; bought mechanically it is buying alts right before beta
  catches up: gross −0.15 to −0.26R at every parameterization, both splits.
- **Regime gate helps but cannot rescue.** `skip_up30d` (no shorts when BTC 30d > +10%)
  improves every A variant by ~0.005–0.01R — directionally consistent with the chento
  regime finding — but that is an order of magnitude too small here.

## How this closes the Paladin loop

The Paladin study showed his *setups* carried no edge (H0 ≈ 0R mechanical) and his results
came from discretionary exits + accounting. The one behaviour that looked automatable —
shorts clustering on fresh-high sweeps — turns out to describe **where he clicks, not why
he wins**: transplanted to a mechanical scanner it cannot pay retail costs. Combined with
H11 (his manual exits dodge 30% of stops), the conclusion is that the win rate was
manufactured at the exit and in the ledger, full stop. There is nothing to port.

## What survives this study

- **Infrastructure**: `scanner_ohlcv.db` (171 symbols × 32 months × 15m, resumable
  fetcher) + `scanner_lib.py` (vectorized cross-sectional event engine with honest cost
  model). Any future cross-sectional idea starts from here at near-zero setup cost.
- **The frequency problem stands unsolved.** A scanner-style sleeve remains the right
  *shape* for faster validation (this study processed ~66k simulated trades over 32
  months in minutes), but it needs a signal with a causal story — per the Q2 portfolio
  direction memo, microstructure-with-causal-story beats statistical-timing patterns.
  Candidates NOT tested here and still open: funding-extreme fades, OI-flush events
  cross-sectionally (the validated BTC OI-flush long generalized to the universe), and
  listing/delisting flows.
- **Negative results that save future time**: sweep-fade shorts and RS-dump longs are now
  both measured dead at retail costs on 2.6 years × 171 symbols. Do not revisit without a
  materially different cost basis (maker-only execution) or signal.

## Files

- `run_study.py` / `run_study_v2.py` — v1 grid (24 A + 4 B variants) and pre-registered A2
- `results/variant_summary.csv`, `results/variant_summary_v2.csv`, per-variant trade CSVs
- `scanner_study.ipynb` — presentation notebook
- `fetch_universe.py` — resumable universe backfill (DB gitignored, regenerate on demand)

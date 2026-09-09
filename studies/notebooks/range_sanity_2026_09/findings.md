# Range-strategy sanity checks — 2026-09-06

**Question.** Funds sit idle while BTC ranges. Is there an intraday range strategy (minimal hold,
small % moves, high leverage, tight stops) that we have not studied and that has a real prior?

**Method.** Not a pre-registered study. Three scripts (`a_`, `b_`, `c_`), BTC only, `prod.db`
read-only, 2020→2026-09 on 1m/15m bars. Costs: taker 18bp RT (research convention) vs a
maker-blended model (entry 2bp maker, TP 2bp maker, stop/TIF 9bp taker) vs zero. Regime = previous
day's ADX label from `studies/lib/regime_adx.py` (no lookahead).

## Results

### A. Is there intraday mean reversion inside the ADX "range" regime? Barely.

| bars | regime | lag-1 autocorr | variance ratio |
|---|---|---|---|
| 15m | range | −0.023 | VR(4) 0.96 |
| 1h | range | −0.008 | VR(4) 0.96 |
| 4h | range | +0.014 | VR(6) 1.06 |
| 1h | long (trend) | −0.037 | VR(4) 0.93 |
| 4h | long (trend) | −0.057 | VR(6) 0.95 |

Intraday mean reversion is *weaker* in the ADX-range regime than during trends (pullbacks revert
more than range noise does). The raw statistical material for a band-fade/grid is nearly absent.

### A2. Naive Bollinger(20,2) fade, 1h, stop 1×ATR(14), target SMA20, TIF 24 bars

Gross R ≈ 0 in every regime (range −0.006, long 0.000, short −0.062, gap +0.027; n≈800–1000 each).
Net: −0.41R (taker) / −0.20R (maker-blended) in the range regime. Median stop ≈ 53bp, so even
4bp of cost is 0.08R. **Dead before costs.**

### B1. Perp-spot basis scalp — market is too efficient now

Basis deviation from its 24h median: sd 1.7bp (2022) → 0.9bp (2026), half-life 14–30 min;
bars with |deviation| > 15bp: 41 in 2023, 2 in 2025, 0 in 2026. Two legs × maker 2bp already
exceeds the typical deviation. **Nothing to harvest.**

### B2. Drift around 8h funding settlement (00/08/16 UTC)

No bucket of funding rate shows a significant pre- or post-settlement drift (all |t| < 2 over
2020→now and 2024→now; e.g. funding > +0.06%: pre-60min +8bp, t=1.2, n=246). **No timing anomaly.**

### B3. Asia range (00–07 UTC) first-break fade, stop 0.25×width beyond, target Asia mid

Break happens on 96% of days; price closes back inside the Asia range 99% of the time (the first
break is usually a 1-minute poke), but reaches the mid only ~60% and the 0.25-width stop hits first
64–68%. Gross R −0.02 to −0.13 per regime; 2025→now range regime −0.16. Median Asia width
108–196bp → stop ≈ 27–49bp → taker cost 0.4–0.7R. **Dead before costs.**

### C. LVN fade (Phase 4 geometry) — cheaper execution does not rescue it

| config | n | gross R (zero cost) | maker-blended | taker 18bp |
|---|---|---|---|---|
| 1–2% width, pen≥30%, target +25%w, 24h | 221 | +0.060 | −0.098 | −0.275 |
| 1–2% width, target +50%w | 221 | +0.028 | −0.139 | −0.307 |
| 1–2% width, pen≥50% | 98 | +0.062 | −0.185 | −0.420 |
| 2–5% width, 72h | 27 | −0.224 | −0.295 | −0.369 |
| 0.5–1% width | 869 | −0.169 | −0.570 | −0.937 |

The Phase 3 descriptive finding (60–70% reversal) never translated into tradeable geometry: the
best gross is +0.06R. The 2026-06-05 note that "pre-cost +0.3–0.5R would survive maker execution"
was an estimate, not a measured backtest; the measured gross is 5–8× smaller. Only the flat_30d
slice of the 1–2% config is positive at maker-blended cost (+0.09R, n=47) — too thin to act on.

## Interpretation

1. Across this repo's own studies (whale absorption, footprint, scanner sweep-fade, dwell-block,
   LVN) plus these checks (band fade, Asia-range fade, basis, funding timing), **every fast
   small-move signal on BTC has gross edge between −0.2R and +0.15R**. Cost is not the only
   problem; the signal isn't there at intraday scale. Coarse structural signals (chento B1/B7,
   short-squeeze, OI flush, funding+CVD) are the ones that carried edge, and they hold 1–3 days.
2. A maker-execution framework lowers the cost bar from ~0.2–0.9R to ~0.05–0.3R per trade at
   these stop widths, but nothing measured so far clears even the lower bar. Build it when the
   go-live execution adapter is built (same seam), not as a research prerequisite.
3. "Range" as defined by ADX < 20 is 13–40% of days per year (40% in 2025, 20% so far in 2026)
   and carries *less* intraday mean reversion than trend days. A range detector is not the
   missing piece.

See the memory note `project_range_strategy_sanity_2026_09` for the portfolio-level conclusion.

# Overlay study — multi-asset chento + wick-exit + half-risk tag + post-loss throttle

*2026-08-23. Substrate: chento Triple composite regenerated for BTC (203 OKX-aligned
trades, 2021-01→2026-08) and ETH (143, 2021-01→2026-05, full composite — ETH LSR exists)
via the `validation_multi_asset` pipeline, `no_resist_OB` applied, `no_tilt` deliberately
NOT applied so post-loss policies could be tested as overlays. All exit variants
re-replayed bar-by-bar on the 15m futures tables (stop-first ambiguity, TIF 72h) so
variants differ only in the policy under test.*

**Caveat inherited from the source**: the Triple trigger pool carries the research
intersect lookahead, so absolute R values are optimistic (production ceiling ~50–70% of
research R per the lookahead audit). Every policy shares the same entries, so **relative**
comparisons are the finding. IS/OOS split 2024-12-31; all headline variants OOS-positive.

## 1. Multi-asset chento: YES — and the diversification is real

| portfolio (base exits, no overlays) | n | total R | maxDD R | MAR-like |
|---|---|---|---|---|
| BTC alone | 203 | 317 | −7.9 | 40 |
| ETH alone | 143 | 236 | −4.0 | 59 |
| BTC+ETH combined (common window) | 333 | 554 | **−8.0** | **69** |

Combined drawdown equals BTC's alone while total R rises 75% — the two streams don't
crash together. ETH re-confirms the 2026-05 validation on a fresh regeneration (+1.65R
mean, 59% WR). **OP stays excluded**: no LSR rows → degraded B1∩B7 composite, a different
strategy. Trade flow roughly +70% vs BTC-only (~60/yr combined at these gates) — this is
the honest path to "steadier flow" from this sleeve family, not new coins beyond what the
composite's data supports.

Production requirements (deferred, Phase-2-style): live writers + freshness contracts for
`cd_futures_eth_15m` and `okx_perp_eth_1h` (both frozen 2026-05-26; ETH LSR is already
live), then a chento_v3 ETH variant through the standard byte-equivalence port gate.

## 2. Rejection-wick exit on chento: NEGATIVE — it amputates the tail

Every wick variant (armed at +0.5R to +3R) reduces both total R and MAR versus the same
tilt policy with base exits, on both assets. Best wick variant on BTC: MAR 50 vs 97 for
the same-tilt base. The chento composite's expectancy lives in its 6R right tail; booking
at the first rejection level cuts exactly that. **The wick exit is a small-target-plan
tool (Paladin's ~1:1 world, where it took +0.003→+0.122R) and does not transfer to a
6R-target mean-reversion sleeve.** Overlays are strategy-shaped, not universal.

## 3. Binary half-risk tag (weekend | pre-CPI/FOMC/NFP 24h | against-30d-trend): NO as specified

Tag rate came out at 51–57% of all trades — far too broad (Paladin's was a targeted
minority tag). It halves good trades indiscriminately: combined skip+H MAR 141 vs 130 for
skip alone is the single flattering cell, but it costs 29% of total R (294 vs 414), and H
degrades every other policy pairing. Verdict: reject at this specification. A future
version must fire on ≤~15% of trades (event-window-only, say) to be worth retesting.

## 4. Post-loss policies: the real decision, and it's asset-dependent

| policy (combined portfolio) | traded | total R | maxDD R | MAR-like |
|---|---|---|---|---|
| none | 333 | 554 | −8.0 | 69 |
| **skip after loss** (shipped `no_tilt`) | 196 | 414 | −3.2 | **130** |
| **half after loss** (Paladin-style) | 333 | 484 | −4.5 | 108 |
| half after 2 stops in 7d | 333 | 542 | −6.0 | 90 |

- The shipped **skip-after-loss remains the MAR champion** — the 2026-05 no_tilt finding
  survives an independent re-derivation.
- **Half-after-loss is a legitimate middle path**: keeps 87% of the income stream (vs
  skip's 75%) at 44% less drawdown than no policy. On ETH it actually *beats* skip
  (MAR 69 vs 40 — skipping after losses discards too many good ETH trades). If the goal
  is steady flow rather than max MAR, half-after-loss is the better throttle, and a
  per-asset policy (BTC skip / ETH half) is defensible.
- The literal Paladin rule (half after 2 stops in 7d) is the weakest of the three — his
  own anti-tilt instinct, tested properly, wants to be *more* aggressive, not less.

## Bottom line

Multi-asset chento (BTC+ETH) is the one change here that clearly advances the steady-income
goal. Keep base exits and no_tilt on BTC; consider half-after-loss for the ETH leg. Drop
the wick exit and the half-risk tag for this sleeve family. Next gate before any prod work:
re-run this comparison on the backward-only trigger variant (the production-faithful pool)
to confirm the tilt-policy ordering survives without the research lookahead.

## Files

`gen_trades.py` (trade regeneration), `run_overlays.py` (engine + grid),
`results/trades_{BTC,ETH}.csv`, `results/overlay_summary.csv`, `overlay_study.ipynb`.

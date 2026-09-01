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

## Backward-only confirmation (2026-08-23, `results_backonly/`)

Re-ran the full grid on the production-faithful pool (`intersect_triggers` patched to
[-24h, 0] per the P3 audit pattern; BTC 101 / ETH 73 OKX-aligned trades). As expected the
lookahead haircut is real: combined baseline mean R 1.66 → **0.81**, WR 59% → 42%.

**Every structural conclusion survives:**

- **Tilt ordering identical in all three scopes**: skip (MAR 15.7 combined) >
  half-after-loss (14.5) > half-after-2-stops (12.7) > none (11.8). On ETH skip and half
  are statistically tied (6.7 vs 6.7) with half keeping 64% more income — the per-asset
  policy (BTC skip / ETH half-or-skip) stands.
- **Multi-asset holds and strengthens**: combined 133.6R vs BTC-alone 77.8R (+72%) at
  *lower* drawdown (−11.4 vs −13.0); MAR doubles (11.8 vs 6.0).
- **Wick-exit still rejected**: below same-config base exits throughout.
- **Half-risk tag is unstable across pools** (hurt bidirectional, helped backward-only —
  e.g. skip+H MAR 23.7 vs 15.7 combined). Inconsistent direction on a 54%-fire-rate tag =
  no reliable evidence; stays rejected pending a ≤15%-fire-rate respecification.

OOS (2025+) totals are positive in all headline variants but thin on ETH (data ends
2026-05). Production expectancy to underwrite any go-decision: **~+0.8R/trade, ~42% WR,
~30 trades/yr/asset** at these gates — not the research-pool numbers.

## Files

`gen_trades.py` (trade regeneration), `run_overlays.py` (engine + grid),
`results/trades_{BTC,ETH}.csv`, `results/overlay_summary.csv`, `overlay_study.ipynb`.

## Re-check on complete ETH data (2026-09-01, `results_backonly/` regenerated)

The 2026-08-23 backward-only pool was cut while `cd_futures_eth_15m` still ended
2026-05-26 (ETH feed dead until revived that day, `5d9c4b3`); 5,376 15m + 1,344 OKX
ETH rows were backfilled since (proven in `studies/notebooks/lsr_b5_study/results/
parity_explained.md`). `gen_trades_backonly.py` + `run_overlays.py results_backonly`
rerun on today's tables: BTC pool identical (211 trades; small metric shifts come
from the last trades' 72h windows completing), ETH pool 182 → 191 trades, OKX-aligned
73 → 77. ETH OOS is no longer thin (base OOS total 0.4R → 4.1R).

| conclusion | 08-23 | 09-01 (complete ETH) | status |
|---|---|---|---|
| multi-asset: combined vs BTC-alone (base) | 133.6R vs 77.8R (+72 %), DD −11.4 vs −13.0, MAR 11.8 vs 6.0 | 134.6R vs 80.8R (+67 %), DD −11.4 vs −11.2, MAR 11.8 vs 7.2 | **holds** (income +67 %, MAR +64 %; the "lower drawdown" part is now a tie) |
| tilt ordering, combined | skip 15.7 > half 14.5 > 2-stops 12.7 > none 11.8 | skip 15.7 > half 14.6 > 2-stops 12.9 > none 11.8 | **identical** |
| tilt on ETH | skip 6.7 ≈ half 6.7 (tied), half +64 % income | **skip 8.4 > half 7.5**; half +47 % income (41.5 vs 28.2R) at DD −5.5 vs −3.4; OOS skip 8.1R vs half 6.1R | **changed**: no longer a tie — skip leads on MAR and OOS, half on total R |
| wick exit | below base throughout | ETH base 6.0 vs wick p0.5/p1/p2/p3 = 1.3 / 3.6 / 4.5 / 3.7 | **still rejected** |
| half-risk tag | unstable across pools; rejected | still helps the backward-only pool (ETH skip+H 8.6, half+H 9.4; combined skip+H 23.8) | unchanged verdict (the instability is vs the bidirectional pool) |

Consequence: the per-asset tilt (BTC skip / ETH half-after-loss, shipped in
`strategies/sleeves/chento_triple_v3/config.py`) was justified by an ETH tie that the
complete data does not reproduce. Both remain defensible — half-after-loss for
steady flow (+47 % ETH income), skip-after-loss for MAR and OOS — but it is now a
trade-off, not a free lunch. Decision left to the operator; no config change made here.

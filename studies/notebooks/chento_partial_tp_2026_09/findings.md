STATUS: CONCLUDED 2026-09-19 — frozen verdict **KEEP_6R**. Partial take-profits, including chento's own trim
arithmetic, lower chento's risk-adjusted record on both assets and in both halves. No production change; the
shipped single 6R target stays. The "something better" the roadmap's Exits constraint asks for was not found in
this form.

# Chento partial take-profits 2026-09 — findings

**One-line answer.** Trimming winners cuts drawdown and cuts return more. On 392 identical entries the shipped
exit makes +0.634 R per trade at a −18.3 R maximum drawdown (MAR 2.54); chento's ladder — 25 / 20 / 10 / 20 % of
the running position at 1 / 2 / 3 / 4R — makes +0.435 R at −15.2 R (MAR 2.09), a paired cost of −0.20 R per trade
with a 95 % interval of −0.33 to −0.08. Win rate rises from 42 % to 46 % while the money falls. A random-level
placebo says the levels are not the problem: even the median random ladder sits below the shipped exit. Any early
trim is wrong on a signal whose edge is in the winners that run.

Pre-registration: [README.md](README.md), frozen in `results/freeze_F0.json` (2026-09-18 22:19:36 UTC) before any
ladder outcome; outcome run 22:19–22:22 UTC; verdict `results/verdict.json`. Executed notebook:
`chento_partial_tp.ipynb`.

## 1. What was tested

- **Entries and walker:** the exit-policy study's, unchanged — 208 BTC and 184 ETH chento trades, 2021-04 →
  2026-09, every trade taken, 10 bp, actual Binance funding, on the frozen OKX-study snapshot.
- **Gates:** the shipped exit (A0) reproduced by the exit-policy walker matched the committed
  `exit_policy_2026_09/results/chento/walks.csv.gz` on all 392 trades to 4e-16; the engine with no ladder equals
  A0 exactly. A0's exits: 194 stops, 159 time exits at 72 h, **39 targets** (10 %).
- **Engine:** a ladder never changes the remainder's stop, target or time exit, so each trade is A0's exit plus the
  first bar at which the running maximum favourable excursion crosses each level (fills at the level, stop before
  target on the exit bar). Total traded notional is unchanged, so total cost is unchanged; funding per leg.
- **Arms:** P1 chento's ladder (43.2 % rides); P2 50 % at 3R (his default); P3 50 % at 1R (his first take-profit).
  Placebo per arm: 500 ladders with the same fractions at random levels in (0.5R, 5.5R).

## 2. Results (net R per trade, 10 bp and actual funding)

| scope | arm | mean R | cum R | win rate | max DD (R) | **MAR** | DD at 2 % risk |
|---|---|---|---|---|---|---|---|
| pooled (392) | **A0 shipped** | **+0.634** | 248.3 | 42.1 % | −18.27 | **2.54** | −36.5 % |
| | P1 chento ladder | +0.435 | 170.5 | 46.4 % | −15.24 | 2.09 | −30.5 % |
| | P2 50 % at 3R | +0.517 | 202.7 | 43.6 % | −17.94 | 2.11 | −35.9 % |
| | P3 50 % at 1R | +0.398 | 156.1 | 46.9 % | −15.25 | 1.91 | −30.5 % |
| BTC (208) | A0 | +0.725 | 150.8 | 45.2 % | −14.13 | **2.00** | −28.3 % |
| | P1 | +0.508 | 105.6 | 51.0 % | −10.58 | 1.87 | −21.2 % |
| ETH (184) | A0 | +0.530 | 97.6 | 38.6 % | −12.83 | **1.50** | −25.7 % |
| | P1 | +0.353 | 64.9 | 41.3 % | −9.91 | 1.30 | −19.8 % |
| first half (181, to 2023-12-19) | A0 / P1 | +1.044 / +0.707 | 189.0 / 128.0 | | −9.68 / −7.53 | **7.43** / 6.47 | |
| second half (211) | A0 / P1 | +0.281 / +0.201 | 59.3 / 42.5 | | −18.27 / −15.24 | **1.24** / 1.07 | |

**Paired against A0** (30-day block bootstrap, 10,000 draws):

| arm | mean d | 95 % interval | BTC | ETH | first half | second half | trades changed |
|---|---|---|---|---|---|---|---|
| P1 | **−0.198** | [−0.329, −0.077] | −0.217 | −0.177 | −0.337 | −0.080 | 228 |
| P2 | −0.116 | [−0.200, −0.038] | −0.134 | −0.096 | −0.188 | −0.055 | 101 |
| P3 | −0.235 | [−0.390, −0.089] | −0.250 | −0.218 | −0.412 | −0.084 | 228 |

Every interval excludes zero on the negative side. The cost is largest in the first half, where chento's edge was
largest (+1.04 R per trade) — trimming hurts most exactly when the trades run furthest.

**Fills.** 58 % of trades touch +1R at some point (median 11 hours after entry), 37 % touch +2R, 26 % +3R, 19 % +4R,
10 % reach 6R. The ladder closes 24 % of the position early on average. So the picture is: most trades show a
paper profit of +1R early, and the shipped policy still earns more by holding through it, because the trades that
go on to 6R pay for the ones that give +1R back — the "wider target wins" finding from the calibration work, seen
from the exit side.

**Placebo.** P1 sits at the 35th percentile of random-level ladders with the same fractions (P2 30th, P3 7th).
The placebo's *median* MAR is 2.15 (P1's family) and 2.25 (single trims) — both below A0's 2.54, and its 95th
percentile (2.31 / 2.43) is below A0 too. No ladder of these fractions at any levels drawn beat the shipped
exit. The levels are not what is wrong; trimming is.

## 3. Decision (rule fixed in README.md §6 before the run)

| clause | P1 | P2 | P3 |
|---|---|---|---|
| D1 MAR ≥ 1.10 × A0 on BTC and ETH | no (1.87 / 1.30 vs 2.00 / 1.50) | no | no |
| D2 MAR ≥ A0 in both halves | no | no | no |
| D3 mean d ≥ −0.10 and CI low ≥ −0.30 | no (−0.20; −0.33) | no (−0.12 passes the point, −0.20 the bound; D1 fails) | no |

No candidate passes D1 → **KEEP_6R**.

## 4. What this settles and what it does not

- **Settled for chento:** partial take-profits at fixed R-levels — chento's ladder, his 3R default, his 1R first
  take-profit, and any random-level ladder of the same fractions — lower the risk-adjusted record. The Exits
  constraint stands unmet on this arm: the shipped 6R target is not to be replaced by trims. Together with the
  exit-policy chento arm (no time-stop change, no invalidation exit found) this is the third exit family tested
  on these entries without a winner.
- **Chento the trader is not contradicted.** His trims sit on discretionary trades at 20–45× with liquidation as
  the real stop (journal `material_2026_09_19_comment_and_tv_chart.md`); taking profit early is how a
  full-margin account survives to trade again. Our sleeve is fixed-R at 2 % with a structural stop, and on it the
  same arithmetic gives money back.
- **Not tested, declared:** break-even stop moves after a partial (another dial, not on record as his rule);
  re-entries; trims on the squeeze sleeves (different signal, different target — the roadmap said they would
  inherit only if this held); the counter-short comparator (needs a causal resistance trigger; with the partial-
  close leg dead it reduces to "do nothing vs counter-short", and the counter-short carries the regime table's
  negative prior).
- **Method note.** The engine is exact for ladders that leave the remainder's exit alone, which is what made a
  500-draw placebo per arm cheap. It does not model adverse fills at resting levels or the extra order count;
  both would make trims look worse, not better.

# Footprint study — pre-registered design (2026-08-23, before any data)

Question: does price-level signed delta (footprint) add value where bar-level delta
already works — specifically as (A) a confirmation filter that concentrates the
sweep-fade short's diffuse gross edge above the cost floor, and (B) target/interest
maps (unfinished auctions / naked POCs)?

Priors stated up front: magnet-family tests are 0-for-2 in this repo (FVG falsified,
LVN null); aggTrades-derived signals are 0-for-2 (whale-absorption, chento Rule 1).
The one motivated hypothesis is Test A, because the scanner study measured a real
gross edge (+0.03–0.06R across 2024/25/26) that only costs killed.

## Test A — sweep-bar imbalance as fade confirmation (the motivated test)

Events: BTC/ETH fresh-high sweep-fades (scanner_lib definition, N ∈ {1,3,7} pooled,
deduped, $10M liquidity gate), 2024-01 → 2026-08, replayed with the scanner engine
(2.5×ATR stop, 1R/1.5R targets, 48h TIF, 18bp costs).

Footprint per event bar (and the bar before): trades bucketed into 20 price bins;
signed delta per bin from is_buyer_maker.

Pre-registered confirmation flags (only these — no mining):
  C1  top-zone delta: net delta in the top 25% of the sweep bar's range < 0
      (sellers absorbed the high)
  C2  top-zone sell share > 60% of top-zone volume
  C3  C1 on (event bar + prior bar) combined

Success criterion: a confirmed subset with net expectancy > 0 AND ≥ +0.05R above
the unconfirmed subset, holding in both halves of the window. KILL if no flag
produces that split. No parameter changes after seeing results.

## Test B — unfinished auctions / naked POCs as magnets (low prior, gated)

Run ONLY if Test A survives, on a continuous 90-day BTC window: daily footprints,
unfinished-auction highs/lows (delta-imbalanced extremes) and naked POCs as levels;
forward touch/time-to-touch vs matched random levels at the same distance (the FVG/LVN
control template). KILL at uplift < 1.1× control.

## Data

Binance Vision daily aggTrades zips (tick-level price/qty/side), downloaded per
event day, parsed to per-event footprints, raw zips deleted — nothing heavy is kept
or committed. Study-local only; prod untouched.

---

# RESULTS (2026-08-24) — CONCLUDED NEGATIVE per pre-registration

All 1,357 events got footprints (100% coverage). Verdict table: `results/test_a_results.csv`.

- **All 6 flag × target combos: KILL.** No confirmed subset achieved net > 0; the
  +0.05R edge floor held in both halves for zero flags.
- **C2 (top-zone sell share) is actively harmful** — confirmed subsets underperform
  everywhere (−0.04 to −0.05R edge). High sell share at the highs marks continuation,
  not absorption.
- **C1 (one-bar top-zone delta): noise** — edge flips sign between halves.
- **C3 (two-bar sustained top-zone selling) is a real but insufficient signal**: the
  only flag with positive confirmed GROSS in both halves (+0.10/+0.05 at 1R,
  +0.09/+0.07 at 1.5R) and a consistent gross separation vs unconfirmed (~+0.08–0.13R).
  It is directionally exactly the absorption story — and it still cannot pay 18bp
  costs on BTC/ETH's zero-gross event pool (confirmed net −0.02 to −0.08R), and its
  H2 edge missed the floor. Killed per pre-registration; no parameter changes.

## Delta-family scoreboard after this study

Bar-level CVD: 3-for-3 (chento B1/B7, short_squeeze). aggTrades-derived: **0-for-3**
(whale-absorption, chento Rule 1, footprint confirmation).

## The one justified continuation (not run, needs its own pre-registration)

C3 on the **alt universe**, where the sweep-fade gross edge actually lived
(+0.03–0.06R): if sustained top-zone selling adds the same ~+0.1R gross separation
there, confirmed alts could clear the cost floor. That is a materially bigger data
job (~10× the Vision files across ~50 alt symbols) with a genuinely uncertain payoff —
run only with explicit appetite for it.

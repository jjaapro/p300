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

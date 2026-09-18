# Exit-policy study, liquidation map — PRE-REGISTRATION v1.0 (DRAFT, under review)

**Written 2026-09-17, before any map was built on any real data.** Follows the exhaustion brainstorm
(`studies/material/exhaustion_signals_2026_09/BRAINSTORM.md`, candidate B) and the user's go-ahead ("proceed according
to your recommendation"). **Nothing here permits a production change.** A positive result permits one thing: a
separate pre-registration of an exit arm that uses the level or event exactly as defined here.

## 1. Question and mechanism

Leveraged perpetual positions carry a price at which the exchange force-closes them. Positions opened during a move
cluster their liquidation prices a fixed fraction away from their entry, so a map of where recently opened positions
would be liquidated can be estimated from public data: open-interest changes tell how much was opened, taker flow tells
which side was the aggressor, and leverage sets the distance. The mechanism claims two things:

- **Magnet.** Price is drawn to dense clusters, because liquidating them is profitable for whoever pushes price there
  and because the forced orders themselves carry price into the cluster.
- **Turn.** Once a cluster is consumed, the forced flow that carried price there is gone, and price tends to turn.

For an exit this would mean: the nearest dense cluster in the trade's direction is a take-profit level, and a burst of
estimated liquidations in the trade's favour marks the end of the forced flow. Both are tested against placebos that hold
everything except the map's information fixed.

## 2. Inputs (frozen)

| Item | Frozen value |
|---|---|
| Open interest | Binance USD-M `metrics` daily archive, 5-minute snapshots (`liqmap_data.py`, checksum-verified): BTCUSDT 2020-09-01 → 2026-09-14, ETHUSDT 2021-12-01 → 2026-09-14 (`cache/<SYMBOL>_metrics_5m_full.npz`, logical sha256 in each meta). A snapshot stamped T is usable from **T + 5 minutes** (the REST feed publishes the same value stamped T + 5 min) |
| Price and flow | Binance perpetual 1-minute panels from the ORB study (`orb_study/cache/<SYMBOL>_perp_1m.npz`): open, high, low, close, volume, quote_volume, taker_buy_volume; zero-volume minutes are missing |
| Actual liquidations (validation only) | `C:/Source/Repos/trader/data/trader.db` `ca_liquidations`: Coinalyze's daily liquidation history for the **Binance USDT perpetual itself** (`BTCUSDT_PERP.A`, `ETHUSDT_PERP.A`; `l` → long_usd = liquidated longs, `s` → short_usd, converted to USD), 2022-02-28 → 2026-04-24 (read-only; sha256 of the file recorded in F0) |
| Trade populations | squeeze_bull's 122 bull fires (S0 walks, the squeeze_bull arm); chento's 208 BTC and 184 ETH gate-off entries (A0 walks, the chento arm); both re-walked on the 1-minute perp path by the microstructure stage's walker (`micro_lib.walk_population`), which reproduced those arms |
| Discovery / holdout | **BTC is the discovery set** (squeeze_bull, chento BTC, BTC hourly states). **ETH is the holdout** (chento ETH entries from 2022-01-01, 176 of 184; ETH hourly states from 2022-01-01). Nothing about ETH is computed, plotted or counted before the BTC verdict is written; the ETH run is one command run afterwards |
| Secondary population (reported) | squeeze_bull's 301 flat/bear fires, walked with the S0 exits |

Not in the map: liquidations themselves (no minute history exists), options, other venues. prod.db is not read.

## 3. The map (frozen model)

Per asset, on 5-minute bins `b` aligned to the archive stamps. From the 1-minute panel: `vol_b`, `tb_b` (taker buy
volume), `qv_b` (quote volume), `high_b`, `low_b`, `VWAP_b = qv_b / vol_b` (present minutes only; a bin with no present
minute is skipped for additions and removals).

1. **Open interest change** `ΔOI_b = OI_b − OI_{b−1}` (base units). Skipped when either snapshot is missing.
2. **Additions** when `ΔOI_b > 0`. Aggressor share `s_b = tb_b / vol_b` (0.5 when `vol_b = 0`). New longs
   `ΔOI_b × s_b`, new shorts `ΔOI_b × (1 − s_b)`, both entered at `VWAP_b`. Each is spread over leverage tiers
   L ∈ {10, 25, 50, 100} with weights **(0.40, 0.30, 0.20, 0.10)**. Liquidation price with the venue's lowest-tier
   maintenance margin `mmr = 0.004`: long `VWAP_b × (1 − 1/L + mmr)`, short `VWAP_b × (1 + 1/L − mmr)`.
3. **Price grid.** Mass lives in buckets of 10 bp on a geometric grid: bucket `k = floor(ln(price) / ln(1.001))`,
   level price of a bucket = `1.001^(k + 0.5)`. Two arrays, long mass and short mass, each a ring of **30 daily
   layers** (a UTC-day rollover drops the oldest layer: positions older than 30 days leave the map).
4. **Removal by price traversal**, applied to each bin before its additions, using the bin's own range: long mass
   in buckets whose level is **≥ `low_b`** is liquidated and removed; short mass in buckets whose level is **≤
   `high_b`** is removed. The removed amounts are the bin's **estimated liquidations** `E_long_b`, `E_short_b` (base),
   and `E × VWAP_b` in USD.
5. **Removal by open-interest decrease.** When `ΔOI_b < 0`: `D = −ΔOI_b`, `R = max(0, D − (E_long_b + E_short_b))`;
   remove `R` proportionally from all remaining mass, both sides in proportion to their totals (voluntary closes).
6. **Warm-up.** Nothing is read from the map before 30 days after the archive's first snapshot.

**Causality.** The map "as of minute m" is the state after the last bin whose stamp is ≤ m − 5 minutes. Features at
minute m use that state and the price at m.

**Provenance of the numbers.** The tiers 10 / 25 / 50 / 100 and their weights are a judgment written before any count,
modelled on what public heat-map products use; 30 days is the top anatomy's positioning window; 10 bp is the
microstructure stage's bucket; `mmr = 0.004` is Binance's lowest BTC/ETH maintenance-margin tier. No value was tried
and discarded. Section 10 reports alternatives after the verdict, without using them for it.

## 4. Features at a state (minute m, price `P` = close of m)

- **Cluster above** (short liquidations, the take-profit side for a long): among buckets with level in `(P, 1.05 P]`,
  the bucket with the largest short mass; `d_up` = its level / P − 1; `share_up` = its mass / total short mass in the
  band. Undefined when the band holds no short mass.
- **Cluster below** (long liquidations): mirror on `[0.95 P, P)`, long mass; `d_dn`, `share_dn`.
- **Burst in favour of a long:** `E_short` summed over the last 3 usable bins (15 minutes), compared with the
  **99th percentile of the same 15-minute sum over the trailing 30 days** (per asset, computed on the whole history
  once, causally); `burst_short = 1` when at or above it. `burst_long` mirrors.

## 5. Tests

All statistics: 30-day circular block bootstrap by day, 10,000 draws, seed 42 (the squeeze_bull arm's `block_indices`);
95 % percentile intervals; one-sided p as in the microstructure stage. Halves: earlier / later half of the included
observations by time.

### Q1 — levels (hourly states, discovery BTC 2020-10-01 → 2026-09-13)

At every hour's :00 minute close with a usable map and a defined cluster above: **actual level** `A = P (1 + d_up)`,
**placebo level** `B = P (1 + d′)` where `d′` is another eligible state's `d_up` (a permutation of the `d_up` values
across all eligible states, seed 42, so the distance distribution is identical by construction). Outcomes, same for A
and B:

- **touch**: the high reaches the level within 24 hours;
- **turn**: given a touch at minute t, within the next 240 minutes the low reaches `level × 0.99` before the high
  reaches `level × 1.01` (1 % back before 1 % through).

Statistics: `Δtouch = mean(touch_A) − mean(touch_B)` and `Δturn = mean(turn_A | touch_A) − mean(turn_B | touch_B)`.
The same for the cluster below with signs mirrored. Four primary numbers: **up-touch, up-turn, down-touch,
down-turn**. Positive means the map's level attracts price (touch) or turns it (turn) more than a level at the same
distance chosen without the map.

### Q2 — exit information (trades)

For each population and each event, the microstructure stage's statistic: `Δ = CV(event) − placebo`, the continuation
value from the first event minute to the shipped exit, minus matched moments of other trades (same elapsed window,
0.25 R bin, direction, no event yet, no calendar overlap; `micro_lib.match`, window 5 % of the horizon, ≥ 3 controls).

- **EV1 cluster reached.** The cluster above (for a long; below for a short) at the **entry minute's** map; event = the
  first open minute whose high (long) / low (short) reaches its level. **Level placebo:** the same with `d′`
  permuted across the population's trades of the same direction (seed 42). An event kind is INFORMATIVE only if it
  beats both its matched placebo and its level placebo (`Δ_actual < Δ_level-placebo`).
- **EV2 burst in favour.** First open minute with `burst_short = 1` for a long (`burst_long = 1` for a short), at
  least 5 minutes after the bin stamp. **Sign control:** the burst against the position.

Populations: squeeze_bull S0 (122), chento BTC A0 (208). A test enters the decision family only with **≥ 30 included
event trades**, counted in the preconditions.

### Family and decision

One Holm family over the primary tests: Q1's four numbers and every Q2 test that meets the count. Per test:

| Classification | Rule |
|---|---|
| **INFORMATIVE** | Holm-adjusted p < 0.05; both halves in the claimed direction; and the placebo comparison of its kind holds (Q2 EV1: `Δ_actual < Δ_level-placebo`; Q2 EV2: `Δ_favour < Δ_against`) |
| **NO INFORMATION** | 95 % interval inside ±0.10 R (Q2) or inside ±2 percentage points (Q1) |
| **CONTRARY** | interval on the wrong side of zero |
| **UNDETERMINED** | otherwise |
| **DESCRIPTIVE** | below the count |

**Holdout rule (written now):** an INFORMATIVE BTC test is **CONFIRMED** only if the same test on ETH has the same
sign with its own 95 % interval excluding zero (Q1) or the same sign of the point estimate with at least 20 included
trades (Q2 on chento ETH). The ETH run happens once, after the BTC verdict file is written, and its numbers are appended
to the verdict.

**Verdict:** the list of CONFIRMED tests, or **NONE CONFIRMED**. A confirmed EV1 permits a stage-B pre-registration of
"take profit at the entry-time cluster"; a confirmed EV2 permits "exit on a liquidation burst in favour"; a confirmed
Q1 without a confirmed Q2 permits nothing for these strategies (the levels matter but not inside these trades).

## 6. Preconditions (before F0; any failure stops the study)

| # | Check |
|---|---|
| L1 | Inputs match their hashes: both metrics archives (every zip against its CHECKSUM; built arrays' logical hashes), both 1-minute panels (file sha256 against their meta), the chento features files and the squeeze_bull ledger (the earlier arms' frozen hashes), `trader.db` (file sha256 recorded) |
| L2 | Populations equal the earlier arms' trades (squeeze_bull 122 = S0 rows; chento 392 = A0 rows; every entry equals the perp close before the entry minute) |
| L3 | Fixtures pass (`tests/test_liqmap.py`): additions with the tier split; traversal removal on both sides with the exact ≥ / ≤ rule; proportional removal net of traversal; the 30-day ring; the 5-minute causal lag; bucket arithmetic; cluster search and the undefined case; touch and turn outcomes incl. the 240-minute cut-off; placebo permutation; the burst threshold; the event rules inside trades |
| L4 | Causality on the real series: masking every bin after a cut leaves the map, the features and the events at or before the cut unchanged (5 cuts per asset) |
| L5 | **Map validity (reported, not a gate):** daily `Σ E_long × VWAP` and `Σ E_short × VWAP` (USD) against `ca_liquidations` BTC long_usd / short_usd, 2022-02-28 → 2026-04-24: Spearman ρ for each, and ρ of the daily long share. Recorded in the preconditions; **no dial is changed because of it**. If either ρ < 0.2 the verdict carries a caveat that the model does not track measured liquidations |
| L6 | Counts, before any outcome: eligible hourly states; per population and event, trades with the event, included trades (≥ 3 controls); the family; median elapsed at first event; and the power line (MDE at 80 % from the sd of the continuation value at one random open minute per trade, seed 42) |
| L7 | Coverage: bins skipped for missing OI or missing price, by year |

Nothing that uses an outcome (touch, turn, continuation value) is computed before F0. The Q1 touch/turn labels and the
Q2 continuation values are computed only in the outcome run.

## 7. What is reported (secondary, never decided)

- Per test: mean and interval, halves, by year; Q2's absolute continuation values and mean placebo; the time-only
  placebo; the no-time-exit walk.
- Q1 by tercile of `share_up` / `share_dn` (does a denser cluster do more?); by distance bin.
- The secondary population (squeeze_bull flat/bear fires), same statistics.
- Descriptive: the estimated liquidation series (daily USD, both sides) and its correlation with the actual daily
  series over time; how often the cluster above sits inside the +3 % target of squeeze_bull; the distribution of
  `d_up` at entry.
- **Sensitivity of the map (after the verdict, reported):** tier weights uniform (0.25 each) and tiers {20, 50, 100};
  search band 3 % and 10 %; bucket 20 bp; the 1-minute open interest of `trader.db` `oi_minutes` (BTC, 2022-06 →
  2026-04) instead of the 5-minute archive. Each rerun of Q1 and Q2 on BTC is labelled as such; none changes the
  verdict.

## 8. Information already seen (disclosure)

- The microstructure stage (absorption, rejection, order book: no exit information) and the top anatomy (no
  real-time top signature; extra return fades after a 24 h stall; tops a median +3.2 % above entry).
- The trade populations' shipped outcomes and every earlier squeeze_bull and chento number.
- The 2026-09-11 defect: prod.db's open interest has carried each hour's opening value since 2026-06-10. This study
  reads the archive, which is stamped correctly, so it is unaffected; SJ-4250 is not in any population.
- The daily `ca_liquidations` totals were seen only as a row count and date range. No map has been built, no cluster
  counted, no burst threshold computed, no touch/turn or continuation value looked at.
- The user's report of a trader timing tops and bottoms (no tools, instrument or times known).

## 9. Freeze

F0 after the preconditions and before any outcome: this file, `liqmap_lib.py`, `liqmap_run.py`, `liqmap_data.py`,
`tests/test_liqmap.py`, `preconditions.json`, the data hashes, written to `results/liqmap/freeze_F0.json` with a UTC
timestamp. Then one BTC outcome run writes `report.json` and `verdict.json`; then one ETH holdout run appends
`holdout_eth.json` and the CONFIRMED list to the verdict. Neither reruns. A bug found afterwards is fixed by a dated
amendment below, and both results are reported. Manifests, not commits, unless the user asks.

## 10. Amendments

None yet.

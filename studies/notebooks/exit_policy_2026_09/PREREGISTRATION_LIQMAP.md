# Exit-policy study, liquidation map — PRE-REGISTRATION v1.1

**Written 2026-09-17 (v1.0 draft), revised 2026-09-18 after a four-lens adversarial review (§10), before any map was
built on any real data.** Follows the exhaustion brainstorm (`studies/material/exhaustion_signals_2026_09/BRAINSTORM.md`,
candidate B) and the user's go-ahead ("proceed according to your recommendation"). **Nothing here permits a production
change.** A positive result permits one thing: a separate pre-registration of an exit arm that uses the level or event
exactly as defined here.

## 1. Question, mechanism and the control

Leveraged perpetual positions carry a price at which the exchange force-closes them. Positions opened during a move
cluster their liquidation prices a fixed fraction away from their entry, so a map of where recently opened positions
would be liquidated can be estimated from public data: open-interest changes tell how much was opened, taker flow
tells which side was the aggressor, and leverage sets the distance. The mechanism claims:

- **Magnet.** Price is drawn to dense clusters, because liquidating them is profitable for whoever pushes price there
  and because the forced orders themselves carry price into the cluster.
- **Turn.** Once a cluster is consumed, the forced flow that carried price there is gone, and price tends to turn.
- **Forced flow ends.** A burst of estimated liquidations in a trade's favour marks the end of the forced flow that
  was helping it.

For an exit this would mean: the nearest dense cluster in the trade's direction is a take-profit level, and a burst of
estimated liquidations in the trade's favour is a time to leave.

**The control.** Every cluster the map produces sits in territory price has not visited since the mass was added
(that is what traversal removal does), at a distance that depends on recent volatility, and its touch and turn rates
depend on both. A placebo level chosen without the map's information must share that geometry, or it measures the
geometry instead of the information. So the control is **a second map built by the identical rules on the identical
price path, with the open-interest and taker information removed** (a constant unit of mass every bin, half long, half
short; §3.7). Its clusters and its bursts are what the model would say if it knew only the price path. Every test below
compares the actual map with this control map at the same state, paired.

## 2. Inputs (frozen)

| Item | Frozen value |
|---|---|
| Open interest | Binance USD-M `metrics` daily archive, 5-minute snapshots (`liqmap_data.py`, every zip checksum-verified): BTCUSDT 2020-09-01 → 2026-09-14, ETHUSDT 2021-12-01 → 2026-09-14 (`cache/<SYMBOL>_metrics_5m_full.npz`, logical sha256 in each meta). `sum_open_interest`, base units |
| Price and flow | Binance perpetual 1-minute panels from the ORB study (`orb_study/cache/<SYMBOL>_perp_1m.npz`, 2020-01-01 → 2026-09-14 exclusive): open, high, low, close, volume, quote_volume, taker_buy_volume; zero-volume minutes are missing. Bars are labelled by open time; minute m is known at m + 60 s (the microstructure stage's convention) |
| Actual liquidations (validation only) | `C:/Source/Repos/trader/data/trader.db` `ca_liquidations`: Coinalyze's daily liquidation history for the **Binance USDT perpetual itself** (`BTCUSDT_PERP.A`, `ETHUSDT_PERP.A`; `l` → long_usd = liquidated longs, `s` → short_usd, USD), 2022-02-28 → 2026-04-24. Read-only; file sha256 recorded in F0 |
| 1-minute open interest (sensitivity only) | the same file's `oi_minutes` (CoinDesk 1-minute OI of the Binance BTCUSDT perpetual, 2022-06-01 → 2026-04-05; BTC only) |
| Trade populations | squeeze_bull's 122 bull fires (S0 walks, the squeeze_bull arm); chento's 208 BTC and 184 ETH gate-off entries (A0 walks, the chento arm); both as re-walked on the 1-minute perp path by the microstructure stage (`results/microstructure/trades.csv.gz`: i0, x, exit prices; no new walk) |
| Decision, report-only, replication | **Decision population for Q2: chento BTC** (208). **squeeze_bull S0 is report-only** in this study (no holdout of its own exists; its exits are report-only by standing decision until its n = 20 / 30 re-cuts). **ETH is a replication**, not an independent holdout: chento ETH entries from 2022-01-01 (176 of 184) and ETH hourly states from 2022-01-30, on a correlated asset over the same period. Before F0, ETH is touched only by L1 (hashes), L2 (population identity) and L7 (archive coverage from the sidecar); no ETH map, feature, cluster, threshold, event, count or outcome exists before the BTC verdict is written |
| Secondary population (reported) | squeeze_bull's 301 flat/bear fires, walked with the S0 exits |

Not in the map: liquidations themselves (no minute history exists), options, other venues. prod.db is not read.

**Alignment convention (frozen, not measured).** The archive snapshot stamped T is taken as the open interest at
T + 5 min: the REST feed labels the same value T + 5 (observed 2026-09-14), and the hourly bar-close series matched
archive stamp H + 55 from 2024-03-04. So **the bin stamped T holds the five 1-minute bars opening at T, T + 1, …,
T + 4** (closing at T + 5), `ΔOI_b = OI_T − OI_{T−5}` is the change over those minutes, and the bin is usable from
T + 5. Before 2024-03-04 the hourly series matched stamp H + 60 instead (a one-day step seen during review, L8), so
for that era the frozen convention prices additions five minutes late. One convention is kept for the whole span; the
shifted alignment is a §7 sensitivity. Under either reading every bar a bin uses closes at or before the minute whose
features read it, so the choice affects placement, not causality.

## 3. The map (frozen model)

Per asset, on the archive's dense 5-minute grid: bin b has stamp `T_b = t0_arch + 300 b` (BTCUSDT t0_arch 2020-09-01
00:00 UTC, ETHUSDT 2021-12-01 00:00 UTC; both whole multiples of 300 s after the panel's 2020-01-01 t0, asserted in
L1). From the 1-minute panel, over the present minutes among the bars opening `T_b … T_b + 4`: `vol_b`, `tb_b` (taker
buy volume), `qv_b` (quote volume), `high_b`, `low_b`, `VWAP_b = qv_b / vol_b`. A bin with no present minute has no
price quantities.

1. **Open-interest change.** A snapshot is usable only if finite and **> 0** (the archive carries zero-valued rows:
   BTC 473 slots in 264 runs, ETH 208 in 67, longest run 580 minutes; they are missing, not a close and re-open).
   `ΔOI_b = OI_b − OI_{b−1}` is defined only when both are usable. A bin with undefined `ΔOI_b` skips steps 2 and 5;
   the change across the gap is dropped, not spanned. Step 4 runs on every bin with at least one present minute.
2. **Additions** when `ΔOI_b > 0` and the bin has price quantities. Aggressor share `s_b = tb_b / vol_b` (`vol_b > 0`
   on every bin that reaches this step; no default share exists). New longs `ΔOI_b × s_b`, new shorts
   `ΔOI_b × (1 − s_b)`, both entered at `VWAP_b`: the aggressor's side of each new contract only (each unit of ΔOI
   opens one long and one short; the passive side is assumed unleveraged or hedged and carries no liquidation mass —
   the judgment public heat-map products make). Each is spread over leverage tiers L ∈ {10, 25, 50, 100} with
   weights **(0.40, 0.30, 0.20, 0.10)**. Liquidation price with maintenance margin `mmr = 0.004`: long
   `VWAP_b × (1 − 1/L + mmr)`, short `VWAP_b × (1 + 1/L − mmr)`. L is effective leverage (isolated-margin formula,
   no fees); a cross-margined position with lower effective leverage appears at a farther tier. Mass added in bin b is
   exposed to traversal from bin b + 1 (`ΔOI_b` is the difference of two snapshots, so a position opened and liquidated
   inside the bin never appears).
3. **Price grid and ring.** Mass lives in buckets of 10 bp on a geometric grid: bucket `k = floor(ln(price) /
   ln(1.001))`, level price of a bucket `1.001^(k + 0.5)`. The index is absolute; arrays cover $100 to $1,000,000
   (k = 4,607 … 13,822), the same for both assets; a price outside the range is an error, never clipped. Two arrays,
   long mass and short mass, each a ring of **30 daily layers** holding the current UTC day and the 29 preceding days:
   at the first bin whose stamp falls on a new UTC day, before that bin's removals and additions, the layer the new day
   maps to is cleared, so positions added more than 29 UTC days ago are gone. A bucket's mass is the sum over its
   layers; removals and features read layer sums.
4. **Removal by price traversal**, applied to each bin before its additions, using the bin's own range: long mass in
   buckets whose level is **≥ `low_b`** is liquidated and removed; short mass in buckets whose level is **≤ `high_b`**
   is removed. The removed amounts are the bin's **estimated liquidations** `E_long_b`, `E_short_b` (base), and
   `E × VWAP_b` in USD. A bin with price but undefined `ΔOI` still runs this step.
5. **Removal by open-interest decrease.** When `ΔOI_b < 0`: `D = −ΔOI_b`, `R = max(0, D − (E_long_b + E_short_b))`,
   clamped to the mass present (`R := min(R, total remaining mass, both sides)`); remove `R` proportionally from all
   remaining mass, both sides in proportion to their totals. Mass never goes below zero; when the clamp binds the
   surplus is recorded per bin (L7). Positions that left through the ring are not treated as closes; a later decline
   that in reality closes them is charged to the remaining younger mass. Openings are counted net and liquidations
   gross: `E` is removed in full and never re-added as the gross openings that offset it, so the map's total runs below
   the open interest inside its window by the liquidations it has estimated. That is a stated bias toward
   under-counting, not a dial.
6. **Warm-up.** Nothing is read from either map before 30 days after the archive's first snapshot.
7. **The control map.** The same construction, code path and inputs with the information removed: at every bin that
   has price quantities and a defined `ΔOI_b` (the same skips as the actual map), one unit of mass is added at
   `VWAP_b`, half long and half short (`ΔOI_b` replaced by 1, `s_b` by 0.5, the addition no longer conditional on
   `ΔOI_b > 0`), spread over the same tiers, weights, mmr, grid and ring; step 4 applies unchanged and gives
   `E⁰_long_b`, `E⁰_short_b`; step 5 does not apply. Its scale is arbitrary and cancels in every comparison.

**Minute conventions and causality.** Minute m is the bar opening at m; its close, high and low are known at m + 60 s.
**The map as of minute m is the state after the last bin whose stamp is ≤ m − 5 min**, whose bars close at or before
m. Features at minute m use that state and `P = close_m`. Traversal removal therefore lags price by five minutes as
well as open interest; no per-minute traversal is applied. Every rule below holds for both maps.

**Provenance of the numbers.** The tiers 10 / 25 / 50 / 100 and their weights are a judgment written before any count;
the tiers match the leverage lines of Coinglass's liquidation map, the kind of product chento's stream 2 (Leviathan's
TradingView Liquidation Levels) resembles; Leviathan's script is closed-source, and this model departs from both
products in five places, all its own (side by the bin's taker share; every positive ΔOI enters with no size
threshold; fixed tier weights; removal on open-interest decrease; 30-day expiry), so a negative verdict is about this
model. `mmr = 0.004` is BTCUSDT's tier-1 maintenance-margin rate; ETHUSDT's tier-1 rate could not be retrieved, so the
BTC value is used for both assets and held constant over 2020–2026 (each 0.001 moves every level by one bucket). The
30-day ring is a judgment made before any count: one month of positioning memory, the same horizon as the burst
baseline (§4), the bootstrap block (§5) and squeeze_bull's regime gate. 10 bp is the microstructure stage's bucket.
No value was tried and discarded; §7 reports alternatives after the verdict, without using them for it.

## 4. Features at a state

At minute m with price `P = close_m` and the map as of m (actual and control alike):

- **Cluster above** (short liquidations, the take-profit side for a long): among buckets with level in `(P, 1.05 P]`,
  the bucket with the largest short mass (ties: the nearest to P); `d_up` = its level / P − 1; `share_up` = its mass /
  total short mass in the band. **Defined** only when the band holds short mass and `share_up` is at or above the
  **top-tercile cut of `share_up` over the trailing 30 days of hourly :00 states with short mass in the band** (per
  map and asset, computed causally on the whole history once; undefined until 30 days of such states exist).
- **Cluster below** (long liquidations): mirror on `[0.95 P, P)`, long mass; `d_dn`, `share_dn`.
- **Burst.** `S_b = E_short_{b−2} + E_short_{b−1} + E_short_b` (three consecutive bins, 15 minutes; missing when any
  of the three has no price quantities). Threshold `θ_b` = the 99th percentile (linear interpolation over defined
  values) of `S` at bins `b − 8642 … b − 3`: the 30 days (8,640 bins) ending before the first bin `S_b` uses, so
  neither `S_b` nor the two sums sharing bins with it enter its own reference; only bins after the warm-up count;
  at least 4,320 of the 8,640 defined, else `θ_b` is missing. One rolling pass per asset (the microstructure stage's
  `trailing_z` form, lag 3, window 8,640). `burst_short_b = 1` when `S_b ≥ θ_b`; `burst_long` mirrors on `E_long`.
  First defined 60 days after the archive's first snapshot at the earliest (BTC 2020-10-31, ETH 2022-01-30). At minute
  m the flag is that of the map as of m. **`burst⁰_short`, `burst⁰_long`**: the identical rule on the control map's
  `E⁰`; by construction its base rate equals the actual burst's.

**Provenance.** 5 % band: the narrowest band that holds the 25×, 50× and 100× tiers of positions opened at P
(liquidation 3.6 %, 1.6 %, 0.6 % away) and excludes the 10× tier (9.6 %). Top tercile: §7's cut; 30 days: the ring's
horizon. 99th percentile: the percentile form of the microstructure stage's conventional extreme (z ≥ 3), used because
15-minute liquidation sums are heavy-tailed; base rate about one bin in a hundred, up to a few bursts per day per side
before clustering, so EV2 is expected to fire inside most 48–72 h trades. 8,640 is 30 days in bins; 4,320 is half, as
the microstructure stage's 5,040 of 10,080. 3 bins / 15 minutes is one bar of chento's signal timeframe. No value was
tried and discarded.

## 5. Tests

Every test compares the actual map with the control map at the same states, paired. All statistics: 30-day circular
block bootstrap by UTC day, 10,000 draws, seed 42 (the squeeze_bull arm's `block_indices`); each draw is Σ / Σ over
the drawn days; 95 % percentile intervals. **Each test has a claimed sign `c`**: Q1 claims Δ > 0 (`c = +1`: the map's
level attracts or turns price more than the control's); Q2 claims Δ < 0 (`c = −1`: holding after the event is worth
less). Every Δ, interval, half and year is reported in its natural sign; the decision quantities are taken on `c·Δ`:
one-sided p = `(1 + #{draws: c·(boot − Δ̄) ≥ c·Δ̄}) / (draws + 1)` (`micro_lib.p_less` for Q2, the chento arm's
`p_one_sided` for Q1), "both halves have the claimed sign", and Holm over these directional p values. Halves: the
earlier / later half of the included observations by time (states for Q1, event trades for Q2; the extra one to the
earlier half). By year = by UTC day's year.

### Q1 — levels (hourly states, discovery BTC 2020-10-31 → 2026-09-12 19:00)

**Eligible state:** an hourly :00 bar m, after both maps' clusters can be defined, at which **both** the actual map's
cluster and the control map's cluster on the tested side are defined, and whose whole outcome window lies inside the
1-minute panel (m + 1,680 minutes ≤ the panel's last minute; states past that are excluded, not scored; counted in L6).
Levels: `A` = the actual cluster's level, `B` = the control cluster's level (each map's own `d`).

**Outcomes** (identical rule for A and B; windows by clock; missing minutes skipped; for the cluster above):
- **touch**: the first present minute t in m + 1 … m + 1440 with `high_t ≥ level` (minute m itself never counts, its
  high may already exceed the level); no such minute → touch = 0 and turn is undefined for that state and level.
- **turn**, given a touch: if `high_t ≥ 1.01 × level`, price went through within the touching minute and turn = 0.
  Otherwise scan present minutes t + 1 … t + 240 in order and stop at the first minute where `low ≤ 0.99 × level` or
  `high ≥ 1.01 × level`: turn = 1 if `low ≤ 0.99 × level` and `high < 1.01 × level` there, else 0 (a minute meeting
  both is scored through). No such minute within 240 minutes: turn = 0 (a stall is not a turn).
- Cluster below: mirror (touch `low_t ≤ level`; through `low_t ≤ 0.99 × level` in the touching minute; then
  `high ≥ 1.01 × level` before `low ≤ 0.99 × level`).

**Four primary numbers**, all paired within the state:
- **up-touch**: per eligible state `δ = touch_A − touch_B`; Δ = Σ δ / Σ states.
- **up-turn**: on eligible states where **both** A and B were touched, `δ = turn_A − turn_B`; Δ = Σ δ / Σ such states.
  Only a comparison inside the same state and the same 24 h window separates "price turns where the cluster is"
  from "the map's levels get touched more"; the residual asymmetry (in a both-touched pair the nearer level is
  mechanically more likely to have been gone through) is symmetric under the null and is reported by sign and size of
  `d_A − d_B` in §7.
- **down-touch, down-turn**: mirrors on the cluster below.
Day assignment: the state's UTC day. Halves and years by state time.

### Q2 — exit information (trades)

**Entry state** for a trade: the map as of minute i0 − 1 (the state after the last bin stamped ≤ i0 − 6 min, one bin
staler than strictly available, so that a bin published at the entry open is never used) with `P = entry`
(= close of i0 − 1, L2). The event search starts at i0. **Kinds** (each with its own first-event column):
- **EV1** — the actual map's cluster in the trade's direction at the entry state; event = the first open minute b
  (i0 ≤ b < x, the walker's open set) with `high_b ≥ level` (long) / `low_b ≤ level` (short); b = i0 is allowed.
- **EV1_ctrl** — the same with the control map's cluster at the same entry state.
- **EV2** — burst in favour: the first open minute m ≥ i0 whose map as of m has `burst_short = 1` for a long
  (`burst_long` for a short) and every bar the three bins of that sum use opens at or after i0 (a burst formed in
  pre-entry bins never fires at entry).
- **EV2_ctrl** — the same with `burst⁰` in favour.
- **EV2_against** — the sign control: the actual burst against the position (`burst_long` for a long), same rule.

**Eligibility** (a trade that is ineligible for a kind is neither an event trade nor a control of that kind, as
`micro_lib.eligible`): EV1 / EV1_ctrl require the respective cluster to be defined at the entry state; EV2 / EV2_ctrl /
EV2_against require the respective threshold to be defined at the entry minute (BTC entries from 2020-10-31; on ETH
the two entries of 2022-01-25 are out of the EV2 kinds). Inside an eligible trade, a minute with a missing burst has
no event and the trade stays at risk as a control on that minute.

**Statistic per kind** (the microstructure stage's, with one change): `Δ_i = CV_i(e_i) − P_i` where CV is the
price-only continuation value from the first event minute to the shipped exit (in the strategy's R) and `P_i` is the
mean over matched controls: trades of the same population and direction, eligible for the kind, with no event of that
kind yet, at elapsed minutes within ±W of `e_i` (W = 5 % of the horizon: 216 min chento, 144 squeeze_bull), in the
same 0.25 R profit bin, not overlapping `[entry_i, exit_i]` in calendar time, **and entering within ±365 days of trade
i** (the era pool; the microstructure stage's exploratory control, the only value tried, made primary because the
all-years pool carried a period effect on these same chento trades — disclosed in §8). At least 3 contributing controls,
else the trade is dropped from the kind (counted). `micro_lib.py` is frozen in the microstructure F0 and is not
edited: `liqmap_lib` carries its own `eligible_liq`, a copy `match_liq` with the era window and per-kind eligibility,
and `classify_liq` with the rules of this section (regression fixtures in L3 pin the copy to the frozen behaviour).
`Δ̄` per kind: mean over its included trades, entry-day bootstrap, one-sided p (`c = −1`), halves, years.

**Placebo comparisons** (paired, on trades included in both kinds): `δ_i = Δ_i(EV1) − Δ_i(EV1_ctrl)`, and
`δ_i = Δ_i(EV2) − Δ_i(EV2_ctrl)`; `δ̄` with the same bootstrap and one-sided p for `δ̄ < 0`. EV2 additionally keeps the
sign control as a point comparison, `Δ̄(EV2) < Δ̄(EV2_against)` (the microstructure stage's form). A comparator needs
at least 10 included trades (the stage's `SIGN_CONTROL_MIN`); below that the test cannot be INFORMATIVE and is labelled
UNDETERMINED (comparator unavailable).

**Populations.** Decision: chento BTC A0. Report-only, same statistics: squeeze_bull S0 (a squeeze_bull test meeting
every INFORMATIVE condition is listed as **CANDIDATE (squeeze_bull, unreplicated)** and permits nothing here), the
squeeze_bull flat/bear fires. Replication: chento ETH (§5, replication rule).

### Family, classification and verdict

**One Holm family**: Q1's four numbers plus each chento BTC EV1 / EV2 test with **≥ 30 included event trades**
(counted in L6). Holm is step-down, so Q1's high-power rejections do not raise Q2's thresholds; one family controls the
error rate over one verdict list. Equivalence bands: touch ±2 pp, turn ±5 pp, Q2 ±0.10 R (a turn lift below 5 pp on a
40–50 % base rate is within the size of what the paired design still cannot remove; 2 pp and 0.10 R are the stage's).

Per test, evaluated **in this order**:

| Order | Classification | Rule |
|---|---|---|
| 1 | **DESCRIPTIVE** | below the count |
| 2 | **NO INFORMATION** | the 95 % interval lies inside the equivalence band |
| 3 | **INFORMATIVE** | Holm-adjusted p (claimed direction) < 0.05; both halves have the claimed sign; the placebo comparison of its kind holds (Q1: the test is itself paired against the control map; Q2 EV1: `δ̄` one-sided p < 0.05; Q2 EV2: `δ̄` one-sided p < 0.05 **and** `Δ̄(EV2) < Δ̄(EV2_against)`); comparators have ≥ 10 included trades; and the interval is not inside the band |
| 4 | **CONTRARY** | the 95 % interval lies wholly on the side opposite the claimed sign |
| 5 | **UNDETERMINED** | otherwise (incl. "comparator unavailable") |

`liqmap_lib.classify_liq` takes the claimed sign and the band as arguments and evaluates in this order; it does not
reuse `micro_lib.classify`'s fixed direction or order (fixtures in L3 pin both twins).

**Replication rule (ETH, written now).** An INFORMATIVE BTC test is **REPLICATED** only if the same test on ETH,
computed identically with ETH's own inputs (ETH map and control map, ETH thresholds and tercile cuts, ETH hourly
states from 2022-01-30, chento ETH entries from 2022-01-01, controls from chento ETH only, ETH's own day axis and
bootstrap; no Holm on ETH, one replication per INFORMATIVE test) shows: **Q1**, the claimed sign, ETH's own 95 %
interval excluding zero and not inside the band; **Q2**, at least 20 included event trades, the claimed sign,
one-sided p < 0.10 on ETH's own bootstrap (the conventional one-sided replication level, chosen now because ETH's
counts are a fraction of BTC's), and the placebo comparison of its kind with the claimed sign and one-sided p < 0.10.
Because about half of the chento ETH trades overlap a chento BTC trade in calendar time (92 of 176 under the 72 h
horizon, 7 sharing the entry minute), the ETH run also reports each Q2 test on the subset of ETH event trades whose
walked window overlaps no BTC event trade of the same kind (sign, Δ̄, interval; reported, not decided), and ETH's own
MDE at 80 %, so a failure from power is distinguishable from a contradiction; either reads NOT REPLICATED.

**Verdict:** the list of REPLICATED tests, or **NONE REPLICATED**, plus any CANDIDATE (squeeze_bull). For chento: a
replicated EV1 permits a stage-B pre-registration of "take profit at the entry-time cluster"; a replicated EV2 permits
"exit on a liquidation burst in favour"; replicated Q1 without a replicated Q2 permits nothing for these strategies
(the levels matter but not inside these trades). A CANDIDATE permits only its inclusion, as defined here, in the
separate ETH long-flush pre-registration that squeeze_bull's exits need (PROTOCOL_TOP_ANATOMY §8.1; this study neither
builds nor reads that fire list).

**Provenance of §5's numbers (chosen here, before any count).** 24 h touch horizon: the top anatomy's state horizon
and the microstructure stage's level lookback. 240 min / 1 % turn, the ±2 pp and ±5 pp bands, K-free paired
comparisons, 20 included ETH trades and p < 0.10 for replication: judgment, written as such. The statistic's numbers
(0.25 R bin, window 5 % of horizon, ≥ 3 controls, 30 trades, ±0.10 R, 10 for comparators, ±365 days) are inherited
unchanged from the microstructure stage and its exploratory control. No value was tried and discarded.

## 6. Preconditions (before F0; any failure stops the study)

| # | Check |
|---|---|
| L1 | Inputs match their hashes: both metrics archives (every zip against its CHECKSUM; built arrays' logical hashes), both 1-minute panels (file sha256 against their meta), the chento features files and the squeeze_bull ledger (the earlier arms' frozen hashes), the microstructure stage's `trades.csv.gz`, `trader.db` (file sha256 recorded); both archive t0 are whole multiples of 300 s after the panel t0 |
| L2 | Populations equal the earlier arms' trades (squeeze_bull 122 = S0 rows; chento 392 = A0 rows; every entry equals the perp close before the entry minute); the ETH count entering on or after 2022-01-01 (176) |
| L3 | Fixtures pass (`tests/test_liqmap.py`): bin-to-bar mapping (bin T uses exactly the bars opening T … T + 4; the state as of m = T + 5 includes bin T and as of T + 4 does not); additions with the tier split, priced at those bars' VWAP; a bin with no present minute adds and removes nothing; zero-valued and NaN snapshot runs (no addition, no proportional removal, traversal still runs on bins with price); traversal removal on both sides with the exact ≥ / ≤ rule, mass added in bin b untouched by bin b's range and removed by bin b + 1's; an up-bin with E > 0 adds exactly ΔOI and lowers the total by E; a down-bin with E > D removes E and adds nothing; the R clamp (empty map, after a rollover, clamp does not touch E); the ring rollover and cleared layer; layer-summed cluster with the tie rule; bucket arithmetic at the grid edges; the control map equals the actual map when ΔOI is constant and tb = vol / 2, and ignores scrambled OI and taker series; cluster search, the density cut and the undefined cases; the burst window (a spike at b changes none of θ_b … θ_{b+2} and changes θ_{b+3}; a spike before the window changes nothing; fewer than 4,320 defined gives a missing θ; a missing E in any of the three bins gives a missing S); the entry state uses the map as of i0 − 1 and P = close(i0 − 1) (masking every panel minute ≥ i0 leaves every EV1 level unchanged); the EV2 in-trade window (a burst whose bins straddle entry does not fire at i0; the first burst with all bars inside the trade does); touch and turn (bar m above the level does not touch; touch on the first ≥ minute; through in the touching minute; back before through; through before back; both in one minute → 0; no resolution in 240 minutes → 0; a missing minute skipped; the last eligible state m + 1,680 = the panel's last minute and m + 1,681 excluded; the mirrors); the paired turn (both touched; per-state difference; the d_A = d_B zero case); `match_liq` reproduces the microstructure fixtures `test_placebo_exclusions_and_per_control_averaging` and `test_placebo_needs_three_controls`, the era window (365 d kept, 365 d + 1 min dropped, a later control kept), per-kind eligibility and per-kind "no event yet"; the claimed sign (a positive Q1 Δ gives p < 0.05 and no CONTRARY under c = +1 and CONTRARY under c = −1; the Q2 mirror); the classification order (Holm p < 0.05, halves in direction, interval inside the band → NO INFORMATION; interval (+2.4, +4.0) pp → INFORMATIVE; comparator below 10 → UNDETERMINED); the paired δ̄ bootstrap and one-sided p; the L5 residual computation and the EV2 demotion rule |
| L4 | Causality on the real BTC series: masking every price minute ≥ c and every snapshot with stamp > c − 5 min leaves both maps, every feature, both thresholds, the tercile cuts and every event at or before minute c unchanged (5 cuts at minutes inside bins). L4 checks causality only; the alignment convention is fixed by §2 and reported by L8 |
| L5 | **Map validity (BTC; fixes the family).** Day bucket: a bin stamped T belongs to the UTC day of T. Daily `Σ E_long × VWAP_b` and `Σ E_short × VWAP_b` (USD) against `ca_liquidations` BTC long_usd / short_usd, 2022-02-28 → 2026-04-24, after the warm-up. **Day-convention pin first**, recorded before any ρ: neither the fetch script nor the API defines whether a row's t is the start or the end of its day, so it is pinned by reading the rows stamped 2025-10-09 / 10 / 11 and 2024-08-04 / 05 / 06 (the 2025-10-10 ~21:00 UTC market-wide liquidation and the 2024-08-05 long flush must dominate their neighbours on the long side); the spike's stamp decides; if the two checks disagree the study stops before F0. Then, per side: (i) raw Spearman ρ; (ii) range-controlled ρ = ρ of the residuals of log(estimate) and log(actual) each regressed on the day's log(high / low) from the panel (days with a zero estimate or actual dropped and counted); (iii) ρ of the daily long share; all at day lags −1 / 0 / +1 (the caveat reads lag 0 only; a neighbour exceeding lag 0 by more than 0.20 on both sides is recorded as a convention anomaly). **Benchmark:** the same three ρ for a map-free naive series, long_naive = `(open_d − low_d) / open_d × OI_d`, short_naive = `(high_d − open_d) / open_d × OI_d` (day OHL from the panel, OI_d the day's first usable snapshot). **Rule:** an EV2 test is DESCRIPTIVE (removed from the family here, before any outcome) when the raw ρ at lag 0 is below 0.2 on any side its trades use (squeeze_bull: short side; chento: both). **Caveat:** the verdict states "the model does not track measured liquidations beyond a volatility proxy" when the map's long-share ρ is below 0.2 or does not exceed the naive series' long-share ρ. Q1 and EV1 are not gated by L5 (it validates amounts, not placement). No dial changes because of L5 |
| L6 | **Counts, before any outcome (BTC).** Eligible hourly states per side and year, states dropped at the panel end, states with one map's cluster undefined; deciles of `share_up` / `share_dn`, of `d_up` / `d_dn` and of band mass at states with mass in the band, per map; the share of states with a defined cluster by year; the distribution of `s_b` over addition bins. Per population and kind (EV1, EV1_ctrl, EV2, EV2_ctrl, EV2_against): eligible trades, trades with the event, included trades (≥ 3 controls, era pool; the all-years count beside it), median elapsed at first event, events per 24 h in trade, the share of trades with `burst = 1` at the entry state, and the overlap of actual and control event minutes; the family with each test's `c`; the Q2 power line MDE(80 %, one-sided 5 %) = 2.49 × sd / √n with sd the sd of the continuation value at one random open minute per trade (seed 42, `micro_run.random_minute_cv_sd`) and n the test's included count. Overlap shares between chento ETH and chento BTC event windows are counted from entry / exit times only |
| L7 | **Coverage, per asset (from the sidecars and the map pass on BTC):** bins skipped for missing or zero OI, bins with no present minute, by year; base units of ΔOI dropped across unusable spans by year; the ten largest single-bin |ΔOI| / OI with dates (the 2021-05-22 04:20–05:40 BTC glitch, 26,168 → 2,772 → 28,197, is disclosed here); clamp count and surplus by year |
| L8 | **Alignment (BTC, reported, not a gate):** for every archive stamp T, the relative difference between archive OI and `oi_minutes.oi_close` at rows T − l for l ∈ {−600 … +600 s} in 60 s steps; per calendar month the lag with the smallest median and that median. Known before F0 (seen during review): the best lag steps on 2024-03-04. The frozen convention is kept for the whole span |

**What is computed before F0.** Both BTC maps, every feature, both thresholds, the tercile cuts, L5's correlations (they
use E, not trade outcomes), and, for the family counts, each trade's first event minute of every kind (which includes
whether and when the actual and control cluster levels are reached inside the trade). **Not computed before F0:** any
Q1 touch or turn label on hourly states, any continuation value at an event, any matched placebo value, any paired δ.

## 7. What is reported (secondary, never decided)

- Per test: Δ̄ with interval, halves, by year; Q2's absolute continuation values and mean placebo; the all-years
  control pool beside the era pool; the time-only placebo; the no-time-exit walk.
- Q1: the unpaired turn rates (`Σ turn·touch / Σ touch` per map, ratio-of-sums per draw); Δ by distance bin and by
  sign and size of `d_A − d_B`; Q1 and Q2 EV1 on all states / trades with any mass in the band (no density cut) and by
  tercile of share; a stratified permutation placebo (`d′` permuted within calendar month for Q1, within direction ×
  entry year for EV1, seed 42) beside the control-map result, so the size of the geometry effect is visible; the joint
  distribution of `(d_A, d_B)`; the Q1 power line = 2.49 × the bootstrap standard error of each Δ, and a negative
  control (two independent permutation label sets, seeds 42 and 43) whose Δ must sit inside its own interval around 0.
- Q2: EV1 and EV2 balance tables of actual vs control events by elapsed tercile, 0.25 R bin and entry year; the share
  of actual and control EV2 event minutes that set a new running high of the trade; Δ against a 15-minute signed-return
  extreme (`close_m / close_{m−15} − 1` at or above its trailing 30-day 99th percentile in the trade's favour) as a
  cruder move placebo; whether the cluster above at entry sits inside squeeze_bull's +3 % target; the distribution of
  `d_up` at entry.
- The secondary population (squeeze_bull flat/bear fires), same statistics.
- Descriptive: the estimated liquidation series (daily USD, both sides), its raw and range-controlled correlation with
  the actual daily series by year, the naive series beside it; the map's total mass as a share of open interest over
  time and the share of a cluster's mass older than 7 days.
- **Sensitivity of the map (BTC only, after the verdict, each rerun labelled; ETH is never run under a variant; no
  variant result permits a stage-B pre-registration):** tier weights uniform and tiers {20, 50, 100}; the passive side
  mapped at L = 10; `mmr = 0.0065`; search band 3 % and 10 %; bucket 20 bp; ring 7 and 90 days (warm-up equal to the
  ring, burst baseline held at 30 days); the shifted alignment (bin T = bars T − 5 … T − 1); the side-agnostic map
  (`s_b ≡ 0.5`); a product-style map (side by the bin's price-change sign, additions only when `ΔOI_b` exceeds its
  trailing 30-day 95th percentile, traversal removal only); burst threshold 95th and 99.9th percentile; turn 0.5 % and
  2 %, 120 and 480 minutes; touch horizon 12 h and 48 h; and the **1-minute open interest variant**: `oi_minutes`
  read as OI at instant t + l_month (L8's best lag per month; months with best median relative difference above 1e-4
  or a lag differing from both neighbours excluded and counted), 60 s bins (`ΔOI = OI(τ) − OI(τ − 60)`, the minute
  opening at τ − 60), usable from τ + 5 min, warm-up 30 days from 2022-06-01, burst over 15 one-minute bins against
  the same trailing-30-day 99th percentile, everything else as frozen, shown beside the frozen map re-scored on the
  variant's span (2022-07-01 → 2026-04-05).

## 8. Information already seen (disclosure)

- The microstructure stage (absorption, rejection, order book: no exit information; its era-matched exploratory
  control gave E1 −0.14, E2 −0.16, E3 +0.13, sign controls +0.01 / +0.39 / +0.15, no interval away from zero, on
  the same chento trades reused here; every event was positive in 2021–2023 and negative in 2024–2026 under the
  all-years pool, which is why the era pool is primary here), the top anatomy (no real-time top signature; extra return
  fades after a 24 h stall; tops a median +3.2 % above entry) and the spot-versus-perp stage (NONE PROMOTED,
  2026-09-18, run in this folder by a parallel session).
- The trade populations' shipped outcomes and every earlier squeeze_bull and chento number.
- The 2026-06-10 defect: prod.db's open interest has carried each hour's opening value since then. This study reads
  the archive, which is stamped as §2 says, so it is unaffected; SJ-4250 is not in any population.
- Before this document was finalised, `liqmap_data.py` downloaded both metrics archives (manifest 2026-09-17 21:05
  UTC: 3,954 zips, 0 days missing) and built the arrays; only the sidecars were read (slots present by year;
  BTC 75,255 identical duplicate rows dropped, 0 conflicting; ETH 0). During review, without any map: the count of
  zero-valued snapshots (above), the archive-versus-`oi_minutes` lag by month (the 2024-03-04 step) and the share
  of chento ETH trades overlapping BTC trades were measured. The daily `ca_liquidations` totals were seen only as a
  row count and date range; six of them will be read for L5's pin. No map has been built, no cluster counted, no
  burst threshold computed, no touch / turn or continuation value looked at.
- The user's report of a trader timing tops and bottoms (no tools, instrument or times known).

## 9. Freeze and code path

`liqmap_run.py` has four stages. **`checks`** runs L1–L8 as scoped above and writes `results/liqmap/preconditions.json`;
it constructs no ETH map. **`freeze0`** writes `results/liqmap/freeze_F0.json` (this file, `liqmap_lib.py`,
`liqmap_run.py`, `liqmap_data.py`, `tests/test_liqmap.py`, `preconditions.json`, the data hashes, a UTC timestamp) and
refuses if it exists. **`outcomes`** is BTC-only, refuses to run unless `freeze_F0.json` exists and `verdict.json`
does not, and writes `report.json` and `verdict.json`. **`holdout`** is the only entry point that constructs an ETH
map; it refuses to start unless `verdict.json` exists and `holdout_eth.json` does not; it records the sha256 of
`verdict.json` as found, then runs on ETH: map and control map, 5 causality cuts (a failure is recorded and stops the
run, no test is REPLICATED), ETH's L5 counterpart (reported), counts, thresholds, outcomes and the replication tests,
all written to `holdout_eth.json`; finally it appends the ETH numbers and the REPLICATED list to `verdict.json` under
a `holdout` key, leaving the BTC fields unchanged. Neither `outcomes` nor `holdout` reruns. A bug found afterwards is
fixed by a dated amendment below, and both results are reported. Manifests, not commits, unless the user asks.

## 10. Amendments and review record

- **v1.0 → v1.1 (2026-09-18, before any map was built).** The v1.0 draft is kept as
  `results/liqmap/PREREGISTRATION_LIQMAP_v1.0_draft_as_reviewed.md`. A four-lens adversarial review (causality,
  placebo validity, mechanism, feasibility; 65 findings, 57 confirmed after verification) changed: the placebo from a
  permuted distance to the control map (§1, §3.7, §5), because a permuted level does not share the map's unvisited
  geometry or its volatility dependence; the Q2 control pool to the era pool; turn to a paired statistic; the bin
  alignment, the entry state, the touch / turn and burst windows, the ring, the clamp and the zero-snapshot rule to
  explicit definitions; the classification to an ordered rule with a claimed sign and equivalence bands; ETH from
  "holdout" to "replication" with p < 0.10; squeeze_bull to report-only with a CANDIDATE label; L5 to a family-fixing
  precondition with a benchmark and a day-convention pin; and added L8 and the disclosures above. No number was chosen
  on an outcome; none had been computed.

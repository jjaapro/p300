# Exit-policy study, spot-led vs perp-led extremes (item F): PRE-REGISTRATION

**v1.1, written 2026-09-17 and amended 2026-09-18, before any event was computed on any trade; frozen at F0 by
`spotperp_run.py freeze0`.**
An information test in the form of microstructure stage 1 (`PREREGISTRATION_MICROSTRUCTURE.md`, frozen 2026-09-15): it
asks whether a new running high inside a long (a new running low inside a short) that is carried by perpetual takers
and a rising premium while spot takers lag says anything about what the rest of the trade is worth. **Nothing here
permits a production change.** A positive result permits one thing: a separate stage 2 pre-registration of exit arms
that use exactly these event definitions.

The prompt is BACKLOG research item 2 and row F of `studies/material/exhaustion_signals_2026_09/BRAINSTORM.md`
(2026-09-15): *"exit a long when a new high is carried by perp taker buying and a rising premium while spot buying
lags"*, and the user's request of 2026-09-17 to run it as an exit-information test on squeeze_bull and chento entries
with matched placebo highs and ETH as the holdout. This version follows three independent candidate designs judged by
two reviewers (2026-09-17), one draft (v0.1), and three adversarial reviews of that draft (lenses: mechanism and
confounds; statistics and placebos; causality and lookahead) that raised 33 issues. The amendments those reviews
produced were decided on reading, before any count, and are integrated where they belong; section 12.1 is the record.
The designs' winner is the 60-minute sign-only form (F1 below) with the 5-minute burst form (F2) beside it and the
external site's parameterisation (F3) reported only.

Status: every number below has its provenance stated (a convention, a prior frozen value, or the mechanism's own
definition); no number was chosen by looking at an outcome; nothing is open. No count on any trade population has been
made. The only computation so far is on downloaded archive files, disclosed in section 11. Nothing under `strategies/`,
`bots/`, the feeds or `prod.db` is read or touched by this stage.

## 1. Why an information test before exit arms

Stage 1's argument holds unchanged. An exit arm's result mixes what the trigger knows with the mechanical effect of
leaving early, and on trades with positive drift leaving early costs R whatever triggers it (chento's shorter time
stops lost 0.13 to 0.57 R; its flow-reversal exit X1 lost 0.24 R while firing on 141 of 392 trades). The continuation
value at the first event, compared with the continuation value of other trades at the same point in their life, in the
same profit bin and direction, with no event yet, is zero when the event carries no information, whatever the exit
rule and the strategy's drift. An exit signal pays no extra leg, so a small edge that cannot pay a round trip as an
entry can still be worth having as an exit.

Two things are already known and shape what is new here. Stage 1 found that a perp taker-flow extreme against the
position, a rejection of the prior 24 h extreme, and an order-book tilt say nothing detectable on chento. The
squeeze_bull top anatomy (stage A) found the premium index **level** and the perp 60-minute taker buy **share** at the
final top identical to the same fire's earlier highs that kept going. So a perp-only flow reading at a high, and a
premium level at a high, are known nulls. The content of item F that has never been measured is (a) **which venue
carried the extreme**, spot or perpetual, and (b) the premium **change** into it. No spot 1-minute series has been
aligned to any trade in this repository; the only spot-versus-perp quantity in production is short_squeeze's 15-minute
CVD divergence at swept lows, an entry.

## 2. Mechanisms (what each event claims)

A new running high inside a long is made by one of two kinds of buyer. Cash (spot) buyers pay in full: no funding
bill, no liquidation price, no forced exit, so demand carried by them tends to persist. Leveraged perpetual buyers
lifting offers faster than arbitrageurs can lean against them push the perpetual rich to its index: the premium rises
as the high prints, the marginal buyer is a levered long paying up, whose position must be financed at the next
settlement, sits above a stop or a liquidation price, and is the first to be unwound when the push stalls. When that
coincides with spot takers being net sellers or flat over the same push, the high is borrowed demand: the perpetual is
leading the index rather than following spot, and cash-and-carry arbitrage (sell the rich perp, buy spot) has not yet
arrived to absorb it. The mirror for shorts is a new running low with the premium falling (the perp cheap to index,
levered shorts pressing) while spot selling lags: a leveraged flush, which is the forced flow whose end squeeze_bull
buys, so a short should be worth less from there.

**Which leg discriminates.** Two of F1's three legs are close to automatic at a perp-made extreme. The premium index is
the perpetual's mark over Binance's multi-exchange spot index, and its numerator is the perp's own impact price, so a
new running high printed on the perp path is, within the hour, most often also a rise in the premium and a positive
perp taker delta over that hour: `s·ΔPrem > 0` and `s·D^P > 0` are expected to hold at most perp-path extremes
whatever spot did. The discriminating leg is `s·D^S ≤ 0`. F1 is therefore read as "the perp rich to the multi-exchange
index while Binance spot takers are net sellers or flat". The premium's index is multi-exchange while `D^S` is Binance
BTCUSDT (ETHUSDT) spot only, so the two venue readings are not the same cross-section of the market; the killed
Coinbase-versus-Binance premium entry (section 11) concerned the same cross-venue structure. P11 reports the
conditional base rate of each leg at in-trade extremes so the reader can see how automatic the two near-mechanical
legs are.

**The open-interest prior.** The repository's OI-flush study (`studies/notebooks/oi_flush`, 2026-06-05) found that a
short flush, price up with open interest down, is continuation, not reversal: leveraged shorts unwinding fuel the rise.
On that prior, at a new running high inside a long, `ΔOI ≤ 0` (`F1_oi_down`, short covering) reads as continuation and
`ΔOI > 0` (`F1_oi_up`, fresh longs opening into the extreme) as the borrowed-demand reading the mechanism describes.
The open-interest split is expected to separate two readings rather than confirm one, and neither half decides
anything. The open-interest leg assumes zero publication latency (section 5), which is unmeasured.

**Controls, and a departure from stage 1.** Stage 1 paired each event with its `s → −s` mirror, the same pattern
pointing with the position. Here that construction is kept as a reported control but not as the decision control, and
the departure is deliberate: at a perp-made extreme the premium and perp legs are near-automatic, so a mirror that
flips every leg mostly changes which trades are at an extreme at all, and its base rate is not comparable to the
event's. The decision control is instead **spot-confirmed**: the same extreme with the same premium and perp legs,
differing only on the spot leg (`s·D^S > 0`, spot takers net buying with the position). It holds the near-mechanical
legs fixed, isolates the one discriminating leg, and has a base rate comparable to the event's. Two reported controls
sit beside it: the **venue-reversal** control (spot-led: the extreme carried by spot takers with the premium moving the
other way and perp takers not net aggressors, the cash-and-carry state) and the **`s → −s` mirror** (the perp-led
pattern at a new running extreme against the trade, every sign flipped, including the extreme itself computed with
`−s`, with its own extreme-matched placebo on `X(−s)` minutes). If the spot-confirmed control scores the same as its
event, the spot leg adds nothing and the pattern is "a fresh perp-path extreme with activity"; section 8 builds that
into the decision.

| Event | Claim | Against the position (tested) | Decision control (spot-confirmed) | Reported controls |
|---|---|---|---|---|
| **F1 perp-led extreme, hour form** | over the hour into a new running extreme, perp takers were net aggressors in the trade's direction, spot takers were not, and the premium moved the perp's way: the extreme was bought (sold) with leverage against cash supply | long: new running high since entry, premium higher than 60 min earlier, perp 60-min taker delta > 0, spot 60-min taker delta ≤ 0; short: new running low, premium lower, perp delta < 0, spot delta ≥ 0 | the same extreme, premium and perp legs; spot 60-min taker delta > 0 in the trade's direction (cash confirms the push) | **F1 spot-led** (venue reversal: premium moved the other way, spot takers net aggressors, perp takers not; the arbitrage state); **F1 mirror** (`s → −s`: the perp-led pattern at a new running extreme against the trade) |
| **F2 perp-led extreme, burst form** | the five minutes that print the new extreme are an extreme burst of perp aggression (≥ 3 sd of the perp's own trailing week) while spot aggression is at or below its own trailing mean: who bought the minute that made the high | long: new running high, perp 5-min taker delta z ≥ +3, spot 5-min z ≤ 0; short mirrored | the same extreme and perp burst; spot z > 0 | **F2 spot-led** (spot z ≥ +3 with perp z ≤ 0); **F2 mirror** (`s → −s`) |
| **F3 site state** (reported, never decided) | the external site's "leveraged blow-off": price trend up, open interest up, futures CVD up while spot CVD not up, volume above normal, over ≥ 14 candles | new running extreme with the site's four numbers pinned as in section 5, BTC entries from 2022 only (needs the 5-minute OI archive) | the same price, open-interest, volume and perp legs; spot delta > 0 | **F3 spot-led** (venue legs reversed: spot delta > 0, perp delta ≤ 0) |

Reported decompositions, never decided: for F1, the premium leg alone (`F1_prem_only`), the venue leg alone
(`F1_flow_only`), the extreme alone (`F1_any_extreme`), the open-interest split of F1 on BTC entries from 2022
(`F1_oi_up`, `F1_oi_down`, above), F1 at spike-level highs (`F1_in_profit`: F1 against at a minute whose mark is at or
above +1 R, the anatomy's `SPIKE`, so the findings can say whether the venue split at spike-level highs differs from
the first push), and the composition line `F1_against_usdt_fdusd` (spot flow summed over BTCUSDT and BTCFDUSD, section
5); for F2, the perp burst regardless of spot (`F2_perp_extreme`). `F1_any_extreme` is the gate itself: under an
extreme-matched placebo every control minute is an extreme and so has had the event, leaving zero controls by
construction, so it is evaluated only under the era + bin placebo without extreme match (S1) and the all-years
no-extreme form (S2); the at-a-high effect is estimated as F1 against under rung 1 minus F1 against under S1. F3 at 60
minutes: a new running high already implies a rise over the hour, and the open-interest leg is expected to bind rarely
too, so F3 will mostly reduce to F1's flow legs plus the volume ratio. That is an expectation, not a measured claim:
P11 reports each F3 leg's base rate separately at in-trade `X` minutes, and the findings read F3 in the light of which
legs actually bound. One parameterisation, no sweep. If a decomposition scores the same as the full event, the other
leg adds nothing.

**The two no-prior-event `Δ̄` are not independent evidence.** Because the premium and perpetual legs are close to
automatic at a perpetual-made extreme, the eligible extremes of a family split into against-type and
spot-confirmed-type, so each kind's placebo is built mostly from the other kind's minutes and the two `Δ̄` come out as
near mirror images whatever the truth. The level statistic keeps that placebo; the comparison that decides between them
is computed against one shared pool (section 6), and the findings must not read the two mirrored numbers as two
results.

**What the spot-confirmed control is, mechanically.** At a rising premium some of the spot taker buying is the
arbitrageurs' hedge leg: sell the rich perpetual, lift spot offers. So spot-confirmed minutes mix genuine cash demand
with arbitrage hedging, and the two cannot be told apart at 1-minute klines. A positive result therefore reads as
"before the arbitrage arrives" as much as "borrowed demand", and the findings must say so.

## 3. Populations and the one price path (frozen)

The three populations, entries, stops, targets, horizons and R are stage 1's, unchanged and hash-checked
(`micro_lib.load_population`; chento features BTC `c1762d57…`, ETH `83663d81…`; squeeze_bull ledger `d3793c4e…`;
short_squeeze replay `5e9fd545…`). One change of framing, per the user's request: **chento is split by asset** into a
decision population and a replication set.

| Population | Trades | Role | Entry | Stop / target / time exit | R |
|---|---|---|---|---|---|
| **chento-BTC** | the 208 BTC gate-off entries (`features_BTC.csv`, `in_off == True`; long and short; direction counts recorded at P2) | **decision (family)** | signal bar open + 15 min | stop 1 R, target 6 R, **72 h** | the features `risk` (ATR-based) |
| **chento-ETH** | the 184 ETH gate-off entries (`features_ETH.csv`) | **replication set** (pre-registered, not in the Holm family; identical frozen rule on ETHUSDT spot, perp and premium; its agreement is required for any promotion, section 8) | same | same | same |
| **squeeze_bull** | the 122 bull fires (`full_oi_flush_ledger.csv`, `regime_backonly == "bull_30d"`), long, BTC | **decision (family)**; its agreement is reported, never sufficient for a promotion (section 8) | trigger hour open + 1 h | −2 %, +3 %, **48 h** | 2 % of entry |
| **short_squeeze** | the 71 replay triggers, long, BTC | descriptive | trigger 15 m bar open + 15 min | replay stop (trigger low × 0.999), 3 R, **6 h** | entry − stop |

Splitting chento costs power: stage 1's pooled MDE was 0.30 to 0.64 R, and chento-BTC alone widens it by about
`√(392 / 208)`, a factor of 1.37. That is accepted because the user named ETH as the holdout and because the ETH line is the only thing resembling
independent evidence on trades now in their fourth use. Placebo controls are drawn **within each population**: a
chento-BTC event is matched only to chento-BTC trades, a chento-ETH event only to chento-ETH trades (stage 1 let the two
assets serve as controls for each other; here they do not). chento-BTC and squeeze_bull share one asset, the same
arrays and overlapping calendar time, which is why their agreement cannot stand in for ETH's (section 8); P11 reports
how often their event trades overlap.

**Price path, all populations:** Binance USD-M perpetual 1-minute bars from the ORB panels
(`orb_study/cache/BTCUSDT_perp_1m.npz`, logical `8220cf02…`, file `b085372e…`; `ETHUSDT_perp_1m.npz`, logical
`1a13a3f4…`, file `77edf3f6…`), 2020-01-01 00:00 UTC to 2026-09-14 exclusive, 3,525,120 minutes, no missing rows. A
minute with zero volume is dead: no fill, no mark, no event. Marks, the walker, the running extreme and the continuation
value all live on this path; the spot series is used for flow only, never for price.

**Walker:** stage 1's (`micro_lib.walk`, section 3.1 of that document), unchanged: stop before target within a minute,
gap-open beyond the stop fills at the open, time exit at the open of the first non-missing minute at or after entry +
H; the trade is open after minute `b` exactly when `b < x`. P4 requires it to reproduce stage 1's saved walks trade by
trade. **Secondary walk (reported, not decided):** no time exit, censored at 720 h (`CENSOR_H`).

**Out of scope:** the no-stop twins, ADX, CARRY and R4, as in stage 1. The 301 BTC flat- and bear-regime flushes and
every ETH flush stay untouched: they are the top anatomy's pre-committed stage B holdouts (`PROTOCOL_TOP_ANATOMY.md`
section 8). Nothing here identifies a flush on ETH, applies a stall rule, or conditions on open interest or a regime on
ETH.

## 4. Data added for this stage

- **Binance spot 1-minute klines**, BTCUSDT and ETHUSDT: monthly zips 2020-01 to 2026-08 and daily zips 2026-09-01 to
  2026-09-13 from `data.binance.vision/data/spot/{monthly,daily}/klines/<SYMBOL>/1m/`, 93 files per symbol, each
  verified against its published `.CHECKSUM` at download (`spotperp_download.py`, manifest
  `data/raw/spotperp/manifest.json`, retrieved 2026-09-17 20:20:09 UTC, 372 files all `downloaded`; the manifest's
  `sha256` is the archive's own CHECKSUM value). When the BTCFDUSD files below were added the manifest was rewritten
  (`retrieved_utc` 2026-09-17 20:59:40 UTC, 422 entries): the 372 earlier files were re-verified against the archive's
  CHECKSUM (status `present`) and were not downloaded again. Fields used: `volume` and `taker_buy_volume` (base units,
  the same units as the perp panel's) for flow, `close` for the alignment and sanity checks only.
- **Binance spot 1-minute klines, BTCFDUSD** (composition line only, section 5): kind `spot_1m`, 37 monthly files
  2023-08 through 2026-08 and 13 daily files 2026-09-01 to 2026-09-13, 50 files, same script (`SYMBOL_SPECS`), same
  CHECKSUM verification, same manifest (entries `symbol == "BTCFDUSD"`, status `downloaded`); the archive has no
  2023-07 file (404; the pair listed in 2023-08). The download was started 2026-09-17 before the freeze and is
  disclosed in section 11. Cache `cache/BTCFDUSD_spot_1m_panel.npz`; the pair's first archive row is 2023-08-04 08:00
  UTC, so every minute before it is NaN by construction and the first finite hour-form sum is 2023-08-04 09:00 UTC.
  **Composition:**
  from 2023-08 Binance's zero-fee BTC/FDUSD pair (and later BTC/USDC) took a large share of Binance spot BTC volume, so
  BTCUSDT spot taker flow is a partial slice of Binance spot from then, through 2024 in particular, where squeeze_bull
  fires cluster. BTCUSDC and ETHFDUSD exist on the archive and are **not** downloaded or used; the pair has no premium
  index of its own.
- **Premium index 1-minute klines** (`futures/um/{monthly,daily}/premiumIndexKlines/<SYMBOL>/1m/`), both symbols, same
  span, same manifest; only `close` (a fraction; 1e-4 = 1 bp) is read. The premium is the perpetual's mark over
  Binance's multi-exchange spot index. The top anatomy's cache `cache/BTCUSDT_premium_1m.npz` (2022-01-01 onward,
  logical `70325b92…`, file `6845168b…`) is **not overwritten**: the new panels are written under a new name and P7
  asserts the overlap identical over `[2022-01-01 00:00, 2026-09-14 00:00)`; the anatomy cache's extra day
  (2026-09-14) lies outside the new panel and is recorded as such.
- **5-minute open interest**, BTCUSDT only, the existing `cache/BTCUSDT_metrics_5m.npz` (2022-01-01 onward, logical
  `d062488f…`, file `907350e0…`), used only by F3 and the F1 open-interest split. This stage reads no ETHUSDT metrics:
  those kinds are reported, not decided, and an ETH build would serve nothing that decides. The 2022-01-01 start is a
  **provenance choice, not a data limit**: a fuller BTCUSDT open-interest cache from 2020-09 exists in this folder
  (below), and the anatomy cache is used instead because its hash is already frozen in that stage's manifest. The
  2022-01-01 01:05 UTC eligibility date follows from it.
- **A concurrent study in the same folder, neither written, run nor read by this stage.** The user runs a
  liquidation-map study here (`PREREGISTRATION_LIQMAP.md`). Its files, seen 2026-09-17 20:56 to 21:22 UTC:
  `spot_data.py` with a second download of BTCUSDT and ETHUSDT spot 1-minute zips into `data/raw/spot_1m/` (188 files,
  including the 2026-09-14 daily file, its own manifest 20:57:53 UTC) and its builds `cache/BTCUSDT_spot_1m.npz`
  (20:58:22 UTC) and `cache/ETHUSDT_spot_1m.npz` (20:58:48 UTC) on a **3,526,560-minute grid** keyed `t0_ms` with a
  2026-09-15 cutoff, which is not this stage's grid; `liqmap_data.py` with `data/raw/metrics/` and
  `cache/BTCUSDT_metrics_5m_full.npz` (2020-09-01 onward) and `cache/ETHUSDT_metrics_5m_full.npz` (2021-12-01 onward),
  built 21:05 UTC; and `coinalyze_doc.html`. This stage's panels therefore carry the **`_panel` suffix** so no name
  collides, and P1 records these files' hashes, if they are present when the checks run, as "present, not read".
- **Not used:** bookDepth (the reused series builder still loads the book arrays for its `zq` field; no F kind reads
  them), funding, BTCUSDC, ETHFDUSD, the concurrent study's `spot_data.py`, `liqmap_data.py`, `data/raw/spot_1m/`,
  `data/raw/metrics/`, `cache/<SYMBOL>_spot_1m.npz` and `cache/<SYMBOL>_metrics_5m_full.npz`, `prod.db` (its `btc_1m` /
  `eth_1m` are spot too and are not opened). No prod.db access anywhere in this stage.

**Build (`spotperp_data.py build`, the ORB builder's pattern):** every zip is re-hashed against the manifest before
parsing; the header row is detected per file by the first byte (`anatomy_data._read_zip_csv`); the timestamp unit is
detected per file (`open_time > 10**14` means microseconds, `orb_data.build_klines`; the spot archive stamps
microseconds from its 2025-01 files, the premium archive milliseconds throughout); rows are placed on the perp grid by
`open_time` (`t0_s = 1577836800`, `n = 3,525,120`); off-grid, outside-panel and duplicate rows are counted and dropped
(last row wins, as the ORB build); conflicting duplicates are counted and P7 requires zero. `close_time − open_time` is
**checked per row and recorded**, never asserted: it equals 59,999 ms (millisecond era) or 59,999,999 µs (microsecond
era) on every row but the 16 listed in missing-data rule 2, and a violating row is counted, listed in the sidecar and
**kept like any other row** (P7). Outputs `cache/<SYMBOL>_spot_1m_panel.npz` for BTCUSDT, ETHUSDT and BTCFDUSD
(`t0_s, open, high, low, close, volume, quote_volume, taker_buy_volume, taker_buy_quote_volume`) and
`cache/<SYMBOL>_premium_1m_panel.npz` (`t0_s, close`) for BTCUSDT and ETHUSDT, each with a `.meta.json` sidecar
(per-file facts, minutes present by year, missing and dead runs, the listed identity failures, logical sha256 via
`micro_data._logical_hash`, file sha256, and the perp grid facts it is aligned to).

**Missing versus dead, per venue, never filled:** a spot minute the archive does not carry is NaN in every field; a
carried spot minute with `volume == 0` keeps its row and is dead (`dead = ~(volume > 0)`, micro_lib's rule applied to
the spot arrays separately). A premium minute the archive does not carry is NaN. Neither is zero-filled or carried
forward: a zero fill would make "spot lags" true exactly when spot data is absent and would manufacture perp-led
events. The perp path's deadness and the spot's are independent; a feature that needs both venues is missing when
either side is. **The composition line is the one exception, and only on the FDUSD leg:** a carried FDUSD minute with
`volume == 0` contributes a taker delta of **zero**, because zero taker flow on that pair is a true value, not an
absence; the summed USDT + FDUSD minute is missing only when **either venue has no archive row**, never partial. Without
this the line would discard BTCUSDT's own flow at every dead FDUSD minute, which is 13,903 minutes of 2023 and 21,523
of 2026, exactly the years it exists to inform.

## 5. Event definitions

`s = +1` long, `−1` short. Minute `b` is the bar opening at `b`, known at its close; spot bar, perp bar and premium
close at minute `b` are all known at `b + 60 s`. Every event of every kind requires: the trade is open after `b`
(`b < x`), `close^P_b` is not missing, **`b − 60 ≥ i0`** (one gate for every kind, so the running maximum behind every
extreme has at least an hour of history and the kinds are comparable), and every input the kind needs is finite at
`b`. Only the **first** event of each kind in a trade is used; later minutes of the same kind are counted
(`*_minutes`) but not used. Superscript `P` is the perpetual panel, `S` the spot panel; both sit on one minute grid.

**The target-minute channel.** `micro_lib.walk` exits at the minute whose high (long) or low (short) reaches the
target, so the target-filling minute is `x` itself and is never an event minute (`b < x` fails there). A trade whose
first perp-led extreme is the minute that fills the target therefore has no event, and its earlier extremes serve as
controls for other trades: a bias against finding information of exactly the tested kind, because the extreme that
ends the trade at its best price is the one the definition cannot see. Two things follow. P11 counts, per population,
kind and control, the trades whose first pattern minute with `b ≤ x` (all legs and `X_b` evaluated as usual, the
open-after condition relaxed to `b ≤ x`, `close^P_x` finite) is the exit minute `x`, split by the walker's exit kind
(target, stop, time), together with the ratio of target-minute shares of a kind against its spot-confirmed control,
each relative to that kind's in-trade `X` base rate. The channel is carried by those counts and not by a second
continuation value: section 6 explains why a value measured at the exit minute is not one.

### Per-minute quantities (per asset and venue, computed once on the whole panel)

- Taker delta: `d^v_b = 2 × taker_buy_volume^v_b − volume^v_b` for `v ∈ {P, S}`, base units; missing when that
  venue's minute is dead or absent (stage 1's `flow_features` form, applied to each venue separately).
- Premium: `prem_b`, the premium index kline close at `b` (fraction); missing where the archive has no row.
- **New running extreme** `X_b`, on the perp path, from entry: long, `high^P_b` present and
  `high^P_b > max{ high^P_m : i0 ≤ m < b, m present }`; short, `low^P_b` present and
  `low^P_b < min{ low^P_m : i0 ≤ m < b, m present }`. Strict; missing minutes are ignored in the running max; the
  entry minute `i0` is never an extreme (no prior minute; `anatomy_lib.new_running_high` returns True at index 0 because
  its prior is −∞, and that element is forced False here); a tie with the prior extreme is not a new extreme. `X(−s)`
  is the same quantity in the opposite direction (for a long, a new running low since entry); the mirror controls use
  it.
- **Stall** `stall_b`: minutes since the last new running extreme in the trade's direction over `[i0, b]`
  (`anatomy_lib.stall_minutes` on the trade's own perp highs, or negated lows for a short, with the entry minute forced
  not an extreme; NaN before the first extreme after entry, and a NaN stall is never "fresh"). `X_b` true means
  `stall_b = 0`. **Fresh:** `stall_b < 60`, the state map's first bin (`STALL_EDGES[1] = 60`).
- **Ordinal of the extreme** `ord_b`: the number of new running extremes in `[i0, b)` (the count of earlier pushes; a
  balance covariate, never a leg).
- **Price trend** `PT_b = close^P_b / close^P_{b−60} − 1` (every asset); **volume ratio** `VR_b` =
  `anatomy_lib.volume_ratio` on perp volume (mean 1-minute volume over `b − 59 … b` with ≥ 50 present, divided by the
  mean over the 10,080 minutes before the window with ≥ 5,040 present). Both are F3 legs on BTC and balance or
  robustness covariates everywhere.
- **Session bucket** of `b`: Asia 00-07, London 07-14, NY 14-21 UTC (`bots/short_squeeze/strategy/config.py`
  `SESSIONS`, read only), plus the 21-24 UTC remainder as a fourth bucket added here; **day type:** weekday or weekend
  (Saturday, Sunday) by the UTC calendar day of `b`.

### F1: perp-led extreme, hour form (tested)

- `D^v_b` = sum of `d^v_m` over the **present** minutes `m` in `b − 59 … b` on venue `v`, missing when fewer than 50 of
  the 60 are present on that venue (the anatomy's `taker_share` window and floor, applied to the delta).
- `ΔPrem_b = prem_b − prem_{b−60}`, missing when either is missing.
- Window inside the trade: `b − 60 ≥ i0` (the premium reference minute `b − 60` is the earliest minute used; this is
  also the gate every kind shares).
- **F1 against:** `X_b` and `s·ΔPrem_b > 0` and `s·D^P_b > 0` and `s·D^S_b ≤ 0`.
  Long: a new running high, premium higher than 60 minutes earlier, perp takers net buyers over the hour, spot takers
  net sellers or flat. Short: a new running low, premium lower, perp takers net sellers, spot takers net buyers or flat.
- **F1 spot-confirmed (decision control):** `X_b` and `s·ΔPrem_b > 0` and `s·D^P_b > 0` and `s·D^S_b > 0`. Only the
  spot leg differs from the event; the two are mutually exclusive at a minute.
- **F1 spot-led (reported control, venue reversal):** `X_b` and `s·ΔPrem_b < 0` and `s·D^S_b > 0` and `s·D^P_b ≤ 0`.
- **F1 mirror (reported control, `s → −s`):** `X(−s)_b` and `−s·ΔPrem_b > 0` and `−s·D^P_b > 0` and `−s·D^S_b ≤ 0`:
  the perp-led pattern at a new running extreme against the trade. Its placebo controls are matched on `X(−s)`
  minutes (a control minute must itself be a new running extreme against the control trade) under the same rung.
- A minute with `ΔPrem_b = 0` exactly is neither the event nor a control. A trade can have several of these kinds at
  different minutes, one, or none.
- Reported sub-masks on the same eligibility: `F1_prem_only`: `X_b` and `s·ΔPrem_b > 0`; `F1_flow_only`: `X_b` and
  `s·D^P_b > 0` and `s·D^S_b ≤ 0`; `F1_any_extreme`: `X_b`; `F1_in_profit`: F1 against and `M_b ≥ +1 R` (the mark at
  `b`, section 6, at or above the anatomy's `SPIKE`; inclusive).
- Reported open-interest split, BTC entries with `i0 ≥ 2022-01-01 01:05 UTC` only (the metrics cache's first stamp plus
  the 60-minute window plus the 5-minute lag; the E3 `BOOK_ELIGIBLE_FROM` pattern): `oi(m)` is the 5-minute archive
  snapshot with the latest stamp `T ≤ close_m − 300 s`, at most 6 slots back, else missing (`anatomy_lib.metric_minutes`);
  `ΔOI_b = oi(b) − oi(b − 60)`; `F1_oi_up` = F1 against and `ΔOI_b > 0`; `F1_oi_down` = F1 against and `ΔOI_b ≤ 0`.
  Open interest is unsigned, so the leg is the same for both directions. A missing `ΔOI_b` skips `b` for these two
  kinds only; F1 itself is unaffected. **Latency caveat:** the 300 s lag is `anatomy_data`'s convention (a snapshot
  stamped `T` is used from `T + 5 min`); the archive's real publication delay is unmeasured, so every OI-conditioned
  number carries that caveat (sections 10 and 11).
- Reported composition line, BTC entries only (`F1_against_usdt_fdusd`): the per-minute spot delta is
  `d^{S+}_m = d^{S,USDT}_m + d^{S,FDUSD}_m`, missing when either venue's minute is missing (never partial);
  `D^{S+}_b` is the 60-minute sum with the same 50-of-60 floor on the summed series; the event is F1 against with
  `D^{S+}` in place of `D^S`, the same sign-only leg, the same builder, with its spot-confirmed control
  (`s·D^{S+}_b > 0`). No minute before 2023-08-01 has a finite `D^{S+}`, so the line covers BTC entries from then; ETH
  is unchanged; BTCUSDC is not included. It decides nothing.

### F2: perp-led extreme, burst form (tested)

- `D5^v_b = d^v_{b−4} + … + d^v_b` (missing if any of the five is missing on venue `v`);
  `z^v_b = (D5^v_b − mean) / sd` with mean and sd over `D5^v` at minutes `b − 10084 … b − 5`, at least 5,040 present,
  else missing. Each venue is normalised against **itself**, so the venues' different scales never enter; `z^P` is
  exactly stage 1's `z_flow` (`micro_lib.flow_features`, `trailing_z`).
- Window inside the trade: F2 keeps its 5-minute flow window and 7-day normalisation, but its events are eligible only
  from `b − 60 ≥ i0`, the gate every kind shares (the 5-minute window alone would fit from `b − 4 ≥ i0`; that earlier
  eligibility is not used).
- **F2 perp-led:** `X_b` and `s·z^P_b ≥ 3` and `s·z^S_b ≤ 0`.
  Long: a new running high whose five minutes are an extreme burst of perp taker buying while the spot 5-minute taker
  delta is at or below its own trailing mean. Short mirrored on lows with selling.
- **F2 spot-confirmed (decision control):** `X_b` and `s·z^P_b ≥ 3` and `s·z^S_b > 0`.
- **F2 spot-led (reported control, venue reversal):** `X_b` and `s·z^S_b ≥ 3` and `s·z^P_b ≤ 0`.
- **F2 mirror (reported control, `s → −s`):** `X(−s)_b` and `−s·z^P_b ≥ 3` and `−s·z^S_b ≤ 0`, placebo on `X(−s)`
  minutes.
- Reported reference: `F2_perp_extreme`: `X_b` and `s·z^P_b ≥ 3` (any spot state). It contains F2 perp-led; the
  contrast between the two is what the spot conjunct adds over a perp burst at a new extreme, which the anatomy already
  found uninformative at squeeze_bull tops.
- Stage 1's `dp5` (no price progress) is not used: here price progresses by definition.

### F3: the external site's state (reported, never decided)

The site (surveyed 2026-09-17, no backtest behind it) gives: price trend > +0.12 %, OI trend > +0.08 %, futures CVD up
while spot CVD not up, volume ratio ≥ 1.35, lookback ≥ 14 candles, candle size unstated. Pinned here as one named
parameterisation, every pin the study's own and named as such:

- Window 60 minutes (shared with F1; 60 one-minute candles satisfy "≥ 14 candles"); BTC entries with
  `i0 ≥ 2022-01-01 01:05 UTC` only (open interest); no premium leg, because the site's state has none.
- `PT_b` and `VR_b` as above; `OT_b = oi(b) / oi(b − 60) − 1`.
- **F3:** `X_b` and `s·PT_b > 0.0012` and `OT_b > 0.0008` and `VR_b ≥ 1.35` and `s·D^P_b > 0` and `s·D^S_b ≤ 0`.
  A missing `PT`, `OT`, `VR`, `D^P` or `D^S` at `b` skips `b` for this kind.
- **F3 spot-confirmed (control, reported with it):** the same price, open-interest, volume and perp legs with
  `s·D^S_b > 0`.
- **F3 spot-led (reported control):** `X_b` and `s·PT_b > 0.0012` and `OT_b > 0.0008` and `VR_b ≥ 1.35` and
  `s·D^S_b > 0` and `s·D^P_b ≤ 0`.
- At a new running high the price leg and the open-interest leg are expected to bind rarely (section 2); P11 reports
  the base rate of each F3 leg separately at in-trade `X` minutes. One parameterisation, no sweep.

### Controls of the reported sub-masks

Every reported kind is paired with a control built by this stage's rule (hold every non-spot leg, flip only the spot
leg to the position's side), computed identically and reported beside it: `F1_flow_only` with `X_b` and `s·D^P_b > 0`
and `s·D^S_b > 0`; `F1_in_profit` with F1 spot-confirmed and `M_b ≥ +1 R`; `F1_oi_up` with F1 spot-confirmed and
`ΔOI_b > 0`, `F1_oi_down` with F1 spot-confirmed and `ΔOI_b ≤ 0` (the open-interest leg is unsigned and is not
mirrored; only the directional spot leg flips); `F1_against_usdt_fdusd` with its spot-confirmed form. Kinds with no
spot leg keep a one-leg venue swap as their reported control: `F1_prem_only` with `X_b` and `s·ΔPrem_b < 0`;
`F2_perp_extreme` with `X_b` and `s·z^S_b ≥ 3`. `F1_any_extreme` is the gate itself and has no direction to mirror;
its comparison is rung 1 against S1 (section 6), which is the size of the at-a-high effect.

### Missing-data rules (all kinds)

1. A perp minute with zero volume is dead: no mark, no fill, no event, no control minute (stage 1).
2. A spot minute absent from the archive or with zero volume is missing for spot: `d^S` is NaN there. Format facts
   from the scan of the downloaded files (`data/raw/spotperp/format_scan.json`, per-file counts, nothing joined to any
   trade): spot minutes missing inside file spans, both symbols, 2020: 1,252 (ETH 1,253), 2021: 993, 2023: 80, other
   years 0; zero-volume spot rows 2020: 50 (ETH 49), 2021: 89, 2023: 72, other years 0; no header row in any spot file;
   microsecond stamps in all 33 files per symbol from 2025-01; no duplicate or off-grid rows. **BTCFDUSD**, from the
   same scan (it covers all 422 files): first archive row **2023-08-04 08:00 UTC**, no minute absent inside a file's
   span, zero-volume rows 13,903 in 2023 (13,502 of them in 2023-08 alone) and 21,523 in 2026 and none in 2024 or 2025,
   no header row, 17 millisecond-era and 33 microsecond-era files, no duplicate or off-grid rows.
   **The `close_time − open_time` identity**, also computed by that scan: it holds on every premium and BTCFDUSD row and
   fails on **16 spot rows**, one in each of BTCUSDT and ETHUSDT 2020-02 (2020-02-19 11:35 UTC), 2020-03 (03-04 09:21),
   2020-12 (12-21 14:09 BTC and 14:08 ETH, a row whose close time precedes its open time), 2021-02 (02-11 03:40),
   2021-04 (04-25 04:00), 2021-08 (08-13 01:59), 2021-12 (12-24 04:59) and 2023-03 (03-24 12:39). Each is the truncated
   last bar before an exchange outage and sits at the edge of a gap; three of them carry zero volume and are dead by
   this rule anyway. **Handling, stated before the build:** such a row is placed by its `open_time` and kept like every
   other row, and the count and the rows are recorded per file in the sidecar. No row is dropped for it.
3. A premium minute absent from the archive is NaN; `ΔPrem` is NaN at both ends of a gap. Format facts, both symbols:
   minutes missing inside file spans 2020: 135, 2021: 5,760 (the 2021-07 file, four whole days), 2022: 1,490 (ETH
   1,487), 2023: 1,454, 2024: 2, 2025: 0, 2026: 1,440 (the 2026-06 file); header rows absent through 2022-01, mixed in
   2022-02 to 2022-05, present from 2022-06; all milliseconds. The scan counts gaps inside each file's own span, so a
   gap at a file edge shows as a shorter span; the builder's by-year presence on the full grid (P7) is the count that
   stands.
4. `D^v_b` needs ≥ 50 present minutes of 60 on venue `v` (F1); `D5^v_b` needs all five (F2); `z^v_b` needs ≥ 5,040
   reference values and `sd > 0`.
5. A minute where any input of a kind is missing is an event minute of no kind in that family (F1 against, F1
   spot-confirmed, F1 spot-led, the F1 sub-masks and `F1_in_profit` share one eligibility; the F1 mirror shares the same
   inputs on `X(−s)` minutes; F2 perp-led, F2 spot-confirmed, F2 spot-led, F2 mirror and `F2_perp_extreme` share
   another), so an event and its controls are counted on the same minutes.
6. Control minutes (section 6) must satisfy the same eligibility as event minutes, so "no prior event" on a control is
   real and not an artefact of missing spot or premium data; and a control's status must be known at every extreme up
   to the minute it contributes (the unknown-status rule, section 6). That rule covers only extremes **at or after the
   shared gate** (`elapsed ≥ 60`): before the gate no event of any kind can occur, so a NaN input there says nothing
   about the control's event history and must not exclude it.
7. `X_b` needs `high^P_b` (long) or `low^P_b` (short) present and at least one present perp minute in `[i0, b)`. The
   shared gate does not guarantee the latter on its own, since sixty consecutive dead perp minutes are possible; the
   guarantee comes from the gate **together with** the kind's finite-input requirement (`D^P` needs 50 present of the
   60 minutes `b−59 … b`, `D5^P` all five), and the implementation adds an explicit finite-prior-minute guard so the
   rule does not rest on that argument.
8. Coverage precondition (P8, the M6 analogue): if fewer than 90 % of a population's in-trade minutes at or after
   `i0 + 60` (the shared gate's denominator, every kind) have the F1 inputs finite, that population's F1 tests are
   DESCRIPTIVE; likewise for F2 with its inputs. No per-trade rule beyond the minute-level rules. No date rule for F1 or
   F2: spot and premium exist for the whole panel, so chento's 2021 trades are in.
9. The composition line's `d^{S+}` is missing when either the BTCUSDT or the BTCFDUSD minute is missing, never partial
   (section 4).

### Provenance of the numbers (chosen here, before any count)

- **60 minutes and 50 present** (F1, F3, and the gate `b − 60 ≥ i0` shared by every kind): `anatomy_lib.FLOW_MIN = 60`,
  `FLOW_MIN_PRESENT = 50`, the top anatomy's frozen flow window (`PROTOCOL_TOP_ANATOMY.md` section 2, C1 and C2,
  2026-09-15). The perp-only reading at this window is already known null at squeeze_bull tops, which is what isolates
  the venue split as the new content.
- **0** as the threshold on `ΔPrem`, `D^P`, `D^S` (F1) and on `z^S` (F2): the mechanism's own definition (premium
  rising or not, a venue's takers net buying or not, spot at or below its own trailing mean). No free number; sign-only
  legs are unit-free, so the venues' base-volume scale difference does not enter. The spot-confirmed control flips that
  one sign and adds no number.
- **5 minutes, 10,080 / 5,040, z ≥ 3** (F2): stage 1's frozen `FLOW_WINDOW`, `NORM_WINDOW`, `NORM_MIN_PRESENT`,
  `Z_EXTREME` (`micro_lib.py`, `PREREGISTRATION_MICROSTRUCTURE.md` section 5), not re-chosen.
- **Strict new running extreme over `[i0, b)`**: `PROTOCOL_TOP_ANATOMY.md` section 4 ("running max M(m)") and
  `anatomy_lib.new_running_high`, with the entry minute excluded and dead minutes ignored.
- **First event only, open after `b`**: stage 1 section 5's rule.
- **+1 R** (`F1_in_profit`): `anatomy_lib.SPIKE = 0.02`, the anatomy's frozen spike level, +1 R in squeeze_bull's own
  R; applied as +1 R of each population's own R.
- **Stall < 60 minutes** (rung 2): `anatomy_lib.STALL_EDGES = (0, 60, 360, 1440, ∞)`, first bin, the state map's
  "fresh" state; `stall_minutes` is the anatomy's function.
- **Session buckets** (R-session, cuts): `bots/short_squeeze/strategy/config.py` `SESSIONS = {"asia": (0, 7),
  "london": (7, 14), "ny": (14, 21)}`, read only, plus the 21-24 UTC complement; weekday / weekend is the UTC calendar.
- **VR on the same side of 1** (R-vol): 1 is the ratio's own neutral point (the recent hour equals its 7-day
  baseline); no number chosen.
- **10** included control trades for a sign control to count: `micro_lib.SIGN_CONTROL_MIN`. **30** included event
  trades for a test to enter the family, for a rung to be the decision rung, and for the ETH replication line:
  `micro_lib.MIN_EVENT_TRADES`. **3** contributing controls: `MIN_CONTROLS`. **±365 days**: `micro_explore.ERA_DAYS`.
  **W = 5 % of the horizon**: `WINDOW_SHARE`. **0.25 R bins**: `BIN_R`. **0.10 R**: `EQUIVALENCE_R`. **0.05**: `ALPHA`.
  **30-day blocks, 10,000 draws, seed 42**: `BLOCK_DAYS`, `N_BOOT`, `SEED`. **0.90**: `BOOK_COVERAGE_MIN`. **2.49**:
  `POWER_Z`. **5 cuts per asset**: stage 1's M7. All inherited, none re-chosen.
- **Open-interest lag 300 s, at most 6 slots back; eligibility from 2022-01-01 01:05 UTC**: `anatomy_lib.METRIC_LAG_S`,
  `METRIC_MAX_SLOTS_BACK`; `cache/BTCUSDT_metrics_5m.meta.json` `t0_utc` 2022-01-01 plus 65 minutes.
- **10,080 / 5,040 volume baseline** (`VR`): `anatomy_lib.VOL_BASE_MIN`, `VOL_BASE_MIN_PRESENT` (the 7-day
  convention shared with stage 1's normalisation window).
- **0.0012, 0.0008, 1.35, 60 candles** (F3 only): the external site's Filter 2 as quoted in the task, with the
  study's own reading of "≥ 14 candles" as 60 one-minute candles. One parameterisation, never swept, never decided.
- **59,999 ms / 59,999,999 µs** (P7): the archive's own kline row identity (`close_time = open_time + 1 minute − 1
  unit`); no choice.
- **`open_time > 10**14` means microseconds**: `orb_data.build_klines`, the ORB build's detection (10^14 ms is the year
  5138, so the test is unambiguous).
- **2023-08-01** (BTCFDUSD): the archive's first monthly file for the pair (2023-07 is 404); a data fact.
- No value in this section was tried and discarded; no count of any kind on any trade preceded it.

### Disclosed alternatives not chosen (so no forking path is hidden)

A 60-minute taker-buy **share** per venue with a threshold on `share^P − share^S` (coarser, needs a second number, and
the perp-only share is known null). Z-scored legs at `z ≥ 3` on both venues in the hour form (would gut the family).
The premium **level** at the extreme (known null). A relative venue leg ("spot share below perp share", true about
half the time absent any effect). A 5-minute z form with `D^S` summed over 60 minutes (mixed windows). The site's
"≥ 14 candles" read as 14 × 5 = 70 minutes (a third window; 60 shares F1's). A "no higher high in the next 30 minutes"
pause condition (the anatomy's false-top label, which is look-ahead). Pooling chento's two assets in the family with a
per-asset sign clause (halves the MDE; not chosen because the user named ETH as the holdout; section 12.1). Making F2 a
reported kind only rather than a family candidate (section 12.1). Rejected at amendment, on reading: the draft's
**count ladder** (dropping the extreme match when the extreme-matched count fell below 30; replaced by the placebo
rungs of section 6, which keep the extreme match in every decision placebo); the **venue-reversal control as the
decision control** (stage 1's mirror construction; kept as a reported control, section 2); **chento-BTC plus
squeeze_bull as a promotion path** without ETH (same asset, same arrays, overlapping calendar time; section 8); **a
threshold on P6** (the draft's 1 % bound, the one number without a prior frozen value; P6 is now report-only and P5's
number-free lag-0 test decides alignment); F2's own 5-minute eligibility (`b − 4 ≥ i0`, replaced by the shared gate).

## 6. Statistic: continuation value against matched placebo extremes

For trade `j` open after minute `m` (elapsed `e = m − i0_j`): mark `M_j(e) = s·(close^P_m − entry) / R` and
**continuation value** `CV_j(e) = s·(exit_price − close^P_m) / R`, price only, to the shipped exit. Funding is ignored
and the exit leg costs the same either way. Profit bin `k = floor(M / 0.25)`.

For event kind `F`, event trade `i` with first event at elapsed `e_i` and bin `k_i`:

- **At-risk controls:** trades `j ≠ i` in the same population and test, **same direction**, whose interval
  `[entry_j, exit_j]` does not overlap `[entry_i, exit_i]` in calendar time, and, under rungs 1 and 2, entering within
  **±365 days** of trade `i`'s entry (`micro_explore.ERA_DAYS`, the exploratory control stage 1 added after its verdict
  because the all-years pool put chento's 2021-23 versus 2024-26 period effect into every event; promoted here on that
  lesson, the value inherited, not re-chosen; the disclosure of what had been seen when this was decided is in section
  11).
- A control contributes its minutes `e′` with `|e′ − e_i| ≤ W` (`W` = 5 % of the horizon: 216 min chento, 144
  squeeze_bull, 18 short_squeeze) where it is open, its close is present, **the kind's inputs are finite at `e′`**, it
  has had no `F` event at or before `e′`, its bin equals `k_i`, the rung's extreme condition holds at `e′` (below), and
  **its status is known at every extreme up to `e′`**: every minute `m ≤ e′` inside the control trade at which `X_m`
  is true has the kind's inputs finite (otherwise "no prior event" cannot be asserted; such a control is excluded for
  unknown status, counted at P11 beside dropped-for-lack-of-controls).
- **The extreme match, kept in every decision placebo:** under rungs 1 and 3, `X_{e′}` is true on the control: the
  control minute is itself a new running extreme of the control trade in its direction (for the mirror controls, of
  `X(−s)`). This removes the mechanical component of being at the trade's best price (a running extreme is by
  construction preceded by a rise and followed on average by a give-back, and the anatomy's state map shows fresh highs
  precede better 24 h returns on squeeze_bull), so that `Δ` measures the venue split, not the state "at a high". Under
  rung 2 the condition is the fresh-extreme form, `stall_{e′} < 60` (the control's last new running extreme in its
  direction fewer than 60 minutes before `e′`; rung 1's minutes are a subset).
- `c_j` = mean `CV_j` over its contributing minutes; **placebo** `P_i` = mean of `c_j` over contributing controls. At
  least **3 contributing controls**, otherwise trade `i` is dropped from the test (counted).
- `Δ_i = CV_i(e_i) − P_i`. The test statistic is the mean `Δ̄` over included event trades.

**Placebo rungs (decision placebos; decided at P11 on counts only, before any continuation value):**

| Rung | Control minute must be | Era pool |
|---|---|---|
| **1 (primary)** | a new running extreme (`X_{e′}`) in the same bin | ±365 days |
| **2** | fresh: `stall_{e′} < 60`, in the same bin | ±365 days |
| **3** | a new running extreme (`X_{e′}`) in the same bin | all years |

The first rung, in this order, under which a candidate test has **at least 30 included event trades** is its decision
placebo; if none reaches 30 the test is DESCRIPTIVE. `Δ̄` is reported under **all three rungs** for every test,
whichever one decides. A test's spot-confirmed control, its reported controls, and the ETH replication line on the same
kind use the same rung as the event test they accompany, whatever their own counts. The rungs replace the draft's count
ladder: no decision placebo drops the extreme match.

**The shared pool, for the control contrast only.** Section 2 says `s·ΔPrem > 0` and `s·D^P > 0` are close to automatic
at a perpetual-made extreme, so the eligible `X` minutes of a kind's family partition into against-type and
spot-confirmed-type. Under the no-prior-event rule above, the event's placebo is then built from spot-confirmed-type
extremes and the control's placebo from against-type extremes, which makes the two `Δ̄` near mirror images of each other
by construction: `Δ̄(spot-confirmed) ≈ −Δ̄(against)`, modulated only by the share of extremes that are neither. The
inequality "`Δ̄` against < `Δ̄` of the control" would then be close to implied by `Δ̄ < 0` and would add no independent
protection, and a difference interval built from the two would be about twice the primary and would exclude zero more
easily. Stage 1 had no such structure because its events held on about 0.02 % of minutes.

So the two statistics are separated. **The level statistic `Δ` keeps the no-prior-event placebo** exactly as defined
above, rungs 1 to 3, with the unknown-status rule. **The control contrast** (the INFORMATIVE condition and the reported
difference interval) is computed against **one shared pool** used by both the event and its spot-confirmed control:
control minutes are eligible `X` minutes on other trades of the same population, same direction, non-overlapping,
within the rung's era window, in the same bin, at or after the gate, with the kind family's inputs finite and the
rung's extreme condition satisfied, **whatever their legs and whatever their prior-event history** (no no-prior-event
filter and no unknown-status filter, because no event history is required of them). Against a common reference the two
subgroups are genuinely comparable and the difference interval is meaningful. P11 reports both pools' counts per test,
and section 2 records that the two no-prior-event `Δ̄` are near mirror images and are not independent evidence.

**Robustness placebos (required for INFORMATIVE; each is the decision rung plus one extra sign-only constraint, applied
separately and reported for every test):**

- **R-vol:** the control minute's `VR_{e′}` lies on the same side of 1 as the event minute's `VR_{e_i}` (`≥ 1` with
  `≥ 1`, `< 1` with `< 1`). An event minute with a missing `VR` is dropped from the R-vol line (counted); a control
  minute with a missing `VR` does not contribute under it.
- **R-session:** the control minute lies in the same session bucket (Asia, London, NY, remainder) and the same day
  type (weekday, weekend) as the event minute.

INFORMATIVE additionally requires `Δ̄ < 0` under R-vol and under R-session (sign only; section 8), **and each line needs
at least 10 included event trades** (`micro_lib.SIGN_CONTROL_MIN`, the same floor and the same provenance as the
control's) for its sign to mean anything. A line below 10 is *unavailable*, and the label becomes "INFORMATIVE,
robustness unavailable", which stays in the table and never counts toward PROMOTED.

**These constraints thin the control pool, and that is expected rather than a surprise to be tuned away.** R-session
keeps one session bucket of four and one day type of two, so roughly one contributing control minute in six to eight.
R-vol keeps about half. Rung 1 already needs a control minute that is itself a new running extreme, in the same 0.25 R
bin, within ±W of the event's elapsed minute. And because section 2 expects this kind on a large share of `X` minutes,
the no-prior-event condition shrinks the era pool with every earlier extreme a candidate control has had. An INFORMATIVE
verdict may therefore be unreachable on these populations. If it is, the preconditions say so **in counts, before any
continuation value**, and the stage reports that it could not decide. No threshold is relaxed to make it reachable.

**Covariate balance (a required P11-style output, no outcomes):** at event minutes versus their contributing control
minutes under the decision rung, per test: median `VR`, median `|PT|`, session-bucket and day-type shares, and the
ordinal of the extreme `ord` with median and quartiles. The findings must state the gaps, and if the ordinal gap is
large, say that the contrast is partly "first push versus sustained trend".

**The target-minute channel is measured in counts, not in a second `Δ`.** Section 5 describes the channel: the event is
defined at a new running extreme, which is exactly the kind of minute that fills a target, and a trade whose first
pattern minute is its exit minute has no event and joins the control pool with its earlier, higher-continuation
extremes. The draft answered it with a statistic `Δ_x` that moved the event to the exit minute. **That statistic is
withdrawn, and no version of it gates INFORMATIVE**, because the quantity it forms is not a continuation value: at the
exit minute the exit has already happened before the close it would be measured from. A time exit fills at `open_x`, so
`s·(exit − close^P_x)/R` is negative precisely when the minute closes beyond its open, which a new running extreme in
that minute makes likely; a target fill gives the intra-minute give-back after the touch; a stop fill gives the
recovery. Three mechanically different quantities, one of them biased toward the sign that would pass the gate.

What is kept is the **size of the channel, in counts and before any continuation value** (P11): per population, kind
and control, the trades whose first pattern minute with `b ≤ x` is the exit minute `x`, split by the walker's exit kind
(target, stop, time); and the ratio of target-minute shares, the kind against its spot-confirmed control, each relative
to that kind's in-trade `X` base rate. The findings must state the size and direction of the channel, which biases `Δ`
negative for the tested kind and more so for bursty perpetual-led pushes, beside any negative `Δ̄`.

**Secondary placebos (reported, never decided):** **S1** era + bin without extreme match (the only placebo under which
`F1_any_extreme` has controls, with S2); **S2** all-years without extreme match (stage 1's
exact form); **S3** time-only (no bin, the decision rung's era pool and extreme match); **S4** `Δ` with continuation
values from the no-time-exit walk, same event and control minutes; **S5** the decision rung with the overlap exclusion
computed on the outcome-free interval `[entry, entry + H]` for both trades instead of `[entry, exit]`. The difference
between rung 1 and S1 is the size of the at-a-high effect; between rung 1 and rung 3 the era effect.

**Inference:** stage 1's, unchanged. `Δ_i` assigned to trade `i`'s entry day; day axis from the population's first to
last entry day; circular block bootstrap, 30-day blocks, 10,000 draws, seed 42 (`micro_lib.block_indices`); each draw
Σ Δ / Σ count over the drawn days, draws with no trades dropped; 95 % percentile interval; one-sided `p` for `Δ̄ < 0`:
`(1 + #{draws: boot − Δ̄ ≤ Δ̄}) / (draws + 1)`. **Halves:** included event trades in entry order, earlier half (the extra
trade when odd) and later half. Also reported, not decided: the same block bootstrap of
`Δ̄(against) − Δ̄(spot-confirmed control)` **computed on the shared pool**, on their union of entry days, because the
classification's control comparison is a bare inequality. The same machinery gives every reported control, every rung,
R-vol, R-session, the shared-pool lines, S1 to S5 and the replication line their own `Δ̄`, interval, halves and years.

A control minute must also satisfy the **shared gate** `e′ ≥ 60` under every placebo, like every event minute: before
the gate no kind is defined, so such a minute is never eligible, whatever its inputs.

## 7. Tests

**Primary candidates (4):** {chento-BTC, squeeze_bull} × {**F1 against, F2 perp-led**}.
**Decision controls (not in the family):** the same populations × {F1 spot-confirmed, F2 spot-confirmed}, computed
identically under the accompanying test's rung.
**Reported controls:** the same populations × {F1 spot-led, F2 spot-led, F1 mirror, F2 mirror}.
**Replication set (not in the family):** chento-ETH × {F1 against, F2 perp-led} with their spot-confirmed and reported
controls, the identical frozen rule on ETHUSDT spot, perp and premium; `Δ̄`, interval, halves and the non-overlap subset
reported under the same rung as the chento-BTC test of the same kind, entering the decision only through the stage
verdict in section 8.
**Descriptive:** short_squeeze × every kind (median trade 65 minutes: the 60-minute gate rarely fits, and stage 1
found the 5-minute extreme near-absent).
**Reported only, on every population where defined:** F3 with its controls, the F1 sub-masks (`F1_prem_only`,
`F1_flow_only`, `F1_any_extreme`, `F1_in_profit`), the open-interest split, the composition line
`F1_against_usdt_fdusd`, `F2_perp_extreme`, each with its control.

A primary candidate is **in the family** only if it has **at least 30 included event trades** under its decision rung
(section 6) and its population passes the coverage bar for that kind (P8). P11 counts this before any continuation
value is computed. The family is then fixed: at most four Holm tests. F3 is never in the family. The two hour-form
designs reviewed on 2026-09-17 defined the same event; it is counted once, as F1. F2 is a family candidate on the same
terms; a rare kind drops to DESCRIPTIVE without diluting Holm.

## 8. Decision per test

| Classification | Rule |
|---|---|
| **INFORMATIVE** | in the family; **Holm-adjusted p < 0.05** over the family; **both halves Δ̄ < 0**; **Δ̄ against < Δ̄ of its spot-confirmed control on the shared pool** (section 6), the control having at least 10 included trades there; and **Δ̄ < 0 under R-vol and under R-session**, each with at least 10 included event trades. The last three conditions are sign-only |
| **INFORMATIVE, sign control unavailable** | every condition above holds except that the spot-confirmed control has fewer than 10 included trades. Stays in the table; **never counts toward PROMOTED** |
| **INFORMATIVE, robustness unavailable** | every condition above holds except that R-vol or R-session has fewer than 10 included event trades, so its sign means nothing. Stays in the table; **never counts toward PROMOTED** |
| **NO INFORMATION ≥ 0.10 R** | in the family, 95 % interval inside (−0.10, +0.10) R |
| **CONTRARY** | in the family, 95 % interval above 0: the event comes before *better* continuation than matched extremes |
| **UNDETERMINED** | in the family, none of the above |
| **DESCRIPTIVE** | fewer than 30 included event trades under every rung, or below the coverage bar: numbers shown, no classification |

The classification is stage 1's `classify` with this stage's control mapping and the three added sign conditions
(`spotperp_lib.classify_sp`; `micro_lib.classify` treats a kind it does not know as needing no sign control, so it is
not called directly). `classify_sp` and the verdict code test the **exact string** `INFORMATIVE`; `micro_run.py`'s
`classification.startswith("INFORMATIVE")` promotion test is not reused, so neither "INFORMATIVE, sign control
unavailable" nor "INFORMATIVE, robustness unavailable" can promote.

**Stage verdict.** A kind (F1 or F2) is **PROMOTED** only if both hold:

- (a) at least one family test on it carries the exact label INFORMATIVE; and
- (b) the chento-ETH replication line on the same kind agrees: at least 30 included ETH event trades under the rung
  defined below, `Δ̄ < 0`, both ETH halves `< 0`, and `Δ̄ < 0` on the non-overlap subset defined below (all sign only).

**The rung the ETH line is read under** is the decision rung of the chento-BTC test of the same kind when that test is
in the family; when chento-BTC is DESCRIPTIVE and squeeze_bull carries the INFORMATIVE label, it is the decision rung
of that squeeze_bull test. The line is computed under all three rungs so either reading is available, and P11 records
which rung each line is read under.

**The non-overlap subset, defined exactly.** A chento-BTC *event trade of the kind* is any trade with a first event of
that kind, taken before any control filter. Intervals are `[entry_ts, the time of minute x]` from the shipped walk, and
overlap is any shared minute. The subset is the ETH event trades of that kind whose interval overlaps no such BTC
interval. It must contain **at least 10 included ETH event trades** (`SIGN_CONTROL_MIN`); below that the line is
"replication unavailable (non-overlap subset)" and the kind is not promoted. P11 reports the subset size beside the
overlap share. The subset exists because chento fires on both assets from one signal system, so overlapping entries are
the expected case and a handful of non-overlapping trades must not decide a promotion by a coin flip.

squeeze_bull's agreement is reported as a third line and is never sufficient: chento-BTC plus squeeze_bull without ETH
is **not** a promotion (same asset, same arrays, overlapping calendar time; P11 reports the overlap shares). Fewer than
30 included ETH event trades under the rung is "replication unavailable": no promotion this stage for that kind. This
is BRAINSTORM section 6's rule ("hold in both halves ... and replicate on a second asset or strategy") made operational
with the second asset required. Otherwise **NONE PROMOTED**. R is each strategy's own R. No pooling across populations.
A squeeze_bull result concentrated in 2023-H2 and 2024 is read with the spot composition in mind (section 4): BTCUSDT
spot taker flow is a partial slice of Binance spot there, and the composition line and P11's per-year base rates are
shown beside it.

**What a promotion permits:** a separate stage 2 pre-registration of exit arms on the promoted population(s) using the
event **exactly as defined here**: exit at the first event, or at the first event while losing, paired against the
shipped exit, with drawdown, and with the spot-confirmed control run as a control arm. That stage 2 pre-registration
must, before any arm runs, name the live source and a measured lag for each of its three inputs (perp 1-minute taker
split, spot 1-minute taker split, premium index kline); `prod.db` has none of the three today. It must book exits at
the open of `b + 1`, with the `b`-close continuation value shown beside it as the information bound. Stage 2 reuses
these trades, so its paired result is not independent confirmation; any production proposal would also need forward
paper evidence and the user's go-ahead. **NONE PROMOTED** closes this line for these events at this resolution
(1-minute klines, Binance spot only); it does not test a multi-venue tape (brainstorm item G) or individual prints.

## 9. Preconditions (before F0; any failure stops the stage)

| # | Check |
|---|---|
| P1 | **Inputs and hashes.** ORB panels match their meta (logical and file sha256, the values of stage 1's M1); chento features, squeeze_bull ledger, short_squeeze replay, chento A0 walks and squeeze_bull S0 walks match `freeze_F0.json`; the stage 1 frozen files this stage imports (`micro_lib.py`, `micro_run.py`, `micro_data.py`) match `freeze_F0.json`; every spot (BTCUSDT, ETHUSDT, BTCFDUSD) and premium zip re-hashed equals the manifest's sha256 (the archive's CHECKSUM value); `format_scan.json` and `results/microstructure/trades.csv.gz` hashed and recorded; the built spot and premium arrays' logical and file sha256 recorded from their sidecars; the metrics cache matches its meta |
| P2 | **Population identity.** Stage 1's M2 rerun unchanged (392 = A0 set, 122 = S0 set, 71 distinct triggers in London / NY hours), then the split: chento-BTC 208 and chento-ETH 184 by `asset`, with long / short counts per asset recorded (never stated in any prior file) |
| P3 | **Venue identity.** Every entry equals the close of the minute before its entry minute on the perp path (relative difference ≤ 1e-9), as M3 |
| P4 | **Walker identity.** For every one of the 585 trades, `i0`, `x`, `kind`, `exit_price`, `x_notime`, `kind_notime`, `exit_price_notime`, `level` and `level_valid` equal stage 1's saved `results/microstructure/trades.csv.gz` row with the same `tid` (exit price ≤ 1e-9 relative; the rest exact). Differences listed; any difference fails |
| P5 | **Spot-vs-perp timestamp alignment (decides).** Per asset and calendar year, and separately for the millisecond-era and microsecond-era spot files, the cross-correlation of 1-minute log returns of spot close and perp close over lags −3 … +3 minutes peaks at lag 0 (number-free; a mis-detected unit or a one-minute shift moves the peak). Any window containing lags ±1 detects a one-minute shift; −3 … +3 is a convenience width and only the peak at lag 0 decides. The premium is tested the same way and on **differences**, not levels: the correlation of `Δprem_b` with `Δ(close^P_b / close^S_b − 1)` peaks at lag 0 and is positive. **Levels must not be used here**: two highly autocorrelated level series correlate about 0.92 at every lag from −3 to +3, so the peak is decided by noise rather than by alignment. This was measured, not assumed, and amendment A26 records it. BTCFDUSD against the perp likewise, per year from 2023. **Scope of the decision:** the asset-years any population can read, which is 2021 onward (the earliest minute any window reaches is 2021-04-16 23:15 UTC, an hour before chento's first BTC entry). 2020 is computed and reported, never decided on. Computed on the panels, never on trades. A peak off lag 0 inside the decided scope fails |
| P6 | **Spot/perp price sanity (report-only, no threshold).** Per asset and year, on minutes live on both venues: median, 99th percentile and maximum of `|close^S / close^P − 1|`, in bp, for BTCUSDT, ETHUSDT and BTCFDUSD. Flagged for reading before the freeze; nothing is gated on it (the draft's 1 % bound was the one number without a prior frozen value and is withdrawn; alignment is decided by P5) |
| P7 | **Build facts.** From the sidecars: minutes present, missing and dead by year on each venue (BTCUSDT, ETHUSDT, BTCFDUSD) and for the premium; missing runs listed; conflicting duplicates **0**; off-grid and outside-panel rows counted; header and unit recorded per file, and the spot unit switch confirmed at 2025-01; the `close_time − open_time` identity **counted and listed per file, never asserted** (the 16 spot rows of missing-data rule 2 are the expected result; each is kept); the BTCUSDT premium panel identical to `cache/BTCUSDT_premium_1m.npz` over `[2022-01-01 00:00, 2026-09-14 00:00)`, the anatomy cache's extra day 2026-09-14 recorded as outside the new panel; BTCFDUSD NaN before its first archive row 2023-08-04 08:00 UTC; all new caches assert `t0_s` and length equal to the perp panel's |
| P8 | **Coverage.** Share of in-trade minutes at or after `i0 + 60` (the shared gate's denominator, every kind) per population (chento-BTC, chento-ETH, squeeze_bull, short_squeeze) and per asset-year with the F1 inputs finite (`D^P`, `D^S`, `ΔPrem`), and separately with the F2 inputs finite (`z^P`, `z^S`). Below 0.90 (`BOOK_COVERAGE_MIN`, reused) the population's tests of that kind are DESCRIPTIVE; short_squeeze is descriptive by section 7 regardless |
| P9 | **Fixtures pass** (`tests/test_spotperp_events.py`, stage 1's fixture style, one twin per gate, values asserted): frozen constants pinned; `X` strict, entry minute excluded, NaN bar never an extreme, dead minutes ignored in the running max, short mirror on lows, `X(−s)` for the mirror; F1 signs for both directions and `ΔPrem = 0` neither; the 50-of-60 floor on each venue separately; F2 at the inclusive boundaries `z^P = 3`, `z^S = 0` (event) and just inside (no event), short mirror; the spot-confirmed controls of F1 and F2 (spot leg `> 0`; a minute is never both event and control); the `s → −s` mirror of F1 and F2 on a new running extreme against the trade; a missing spot or premium input at an otherwise perfect minute gives no event of any kind in that family; the shared gate `b − 60 ≥ i0` for F1, F2 and F3 (a perfect F2 minute at `b − i0 = 59` is not an event, at 60 it is); first-event selection; spot NaN never zero-filled (a synthetic absent spot minute yields no F1 or F2 event); `F1_in_profit`'s gate at mark exactly +1 R (event) and just below (none); the composition line's summed `D^{S+}` with one missing FDUSD minute (the summed minute is missing, never partial); `match_sp`: a control minute that is not an extreme is excluded under rungs 1 and 3 and included under S1; under rung 2 a control minute at stall 59 is included and at stall 60 excluded; a control minute before the gate fits (elapsed < 60) is excluded under every placebo even with finite inputs; a control with a NaN-input new extreme before `e′` is excluded (unknown status) and with a finite one included; `F1_any_extreme` has zero controls under rung 1 (asserted zero); the shared pool admits a control with a prior event of the kind and one that is itself an event minute, while the no-prior-event pool excludes both, and on a synthetic population whose every `X` minute is against-type or spot-confirmed-type the two no-prior-event `Δ̄` come out as mirror images while the shared-pool contrast does not; R-vol (a control on the other side of 1 excluded, same side included) and R-session (other bucket or other day type excluded); a control entering 366 days away excluded, 364 included under rungs 1 and 2, included under rung 3; a control with a prior event, the other direction, an overlapping interval, another bin, and fewer than 3 controls, as stage 1; S5's outcome-free overlap interval; a control minute with a missing input excluded; the open-interest lag and eligibility date; `match_sp` under S2 equals `micro_lib.match` row for row on a synthetic population; `classify_sp` uses this stage's control map, requires the three sign conditions, and returns the exact string `INFORMATIVE` only when all hold, with each condition failing alone producing its own label; the verdict code promotes neither "INFORMATIVE, sign control unavailable" nor "INFORMATIVE, robustness unavailable", refuses a squeeze_bull-only promotion, and treats an ETH line below 30 included trades or a non-overlap subset below 10 as replication unavailable. The builder's own fixtures are `tests/test_spotperp_data.py` (19 of them, run and passing 2026-09-18 before the build): header and no header, ms and µs, mixed units fail, off-grid and outside-panel dropped and counted, identical duplicate dropped and counted, conflicting duplicate fails the build within a file and across files, zero-volume row kept and counted dead, absent minute NaN, a row violating the `close_time − open_time` identity counted, listed and **kept**, a manifest hash mismatch fails, the premium panel keeps only `close`, BTCFDUSD's first present minute and all-NaN prefix, and the year-slice and missing-run helpers |
| P10 | **Causality on the real series.** Per asset, 5 cuts at mid-trade of random trades (seed 42; every trade eligible, 2021 trades included; stage 1's M7 restricted to book-eligible trades). At each cut: perp and spot minutes after the cut set to NaN in every field; premium minutes after the cut NaN; open-interest slots with archive stamp `T` such that `T + 300 s` is later than the close of the cut minute NaN; then `D^P`, `D^S`, `z^P`, `z^S`, `ΔPrem`, `ΔOI`, `PT`, `OT`, `VR`, `X`, `X(−s)` and every F1, F2 and F3 mask, event, control and sub-mask at or before the cut must be unchanged for every open trade |
| P11 | **Counts, no outcomes.** Per candidate test, decision control, reported control, replication line and reported kind: eligible trades, event trades, included trades under rung 1, rung 2, rung 3, S1 and S2, under R-vol and R-session at the decision rung (each against the floor of 10), and in the shared pool of section 6 for the event and its spot-confirmed control; the decision rung per test (first rung with ≥ 30), the rung each replication and control line is read under, and the family; event share; median elapsed at first event; events per 24 h in trade; dropped-for-lack-of-controls and excluded-for-unknown-status, each as a count and a share of event trades (no threshold); the mean number of distinct controls per event and the share of controls used by more than one event; the exit-minute counts of section 5 (first pattern minute is `x`, by exit kind); the covariate-balance table of section 6; the overlap shares of section 8 (ETH event trades overlapping a chento-BTC event trade of the same kind; squeeze_bull event trades overlapping chento-BTC event trades; chento-BTC first-event minutes inside an open squeeze_bull trade, and vice versa); base rates: unconditional rates per asset-year and direction of every mask's **leg conjunction with `X` omitted** (`X` is defined only inside a trade, so there is no unconditional form of it), and X-conditioned rates on in-trade `X` minutes at or after the gate of each population, including `P(s·ΔPrem > 0 | X)`, `P(s·D^P > 0 | X)`, `P(s·D^S ≤ 0 | X)`, the share of perp bursts (`s·z^P ≥ 3`) with `s·z^S ≤ 0`, each F3 leg separately, and per asset-year the base rate of `s·D^S ≤ 0` at in-trade `X` minutes for USDT-only and for USDT + FDUSD, so an empty family is explained rather than re-tuned; and the power line MDE(80 %, one-sided 5 %) ≈ 2.49 × sd(CV at one random open minute per trade, seed 42) / √n, with the sd recomputed per population (chento-BTC and chento-ETH separately; stage 1's pooled chento 1.78 R, squeeze_bull 0.86 R, short_squeeze 1.33 R are the references). No continuation value at an event is computed |

## 10. What is reported (secondary, never decided)

Per test, decision control, reported control, replication line and reported kind: `Δ̄` with interval, halves, and by
year; P11's exit-minute counts and target-minute share ratios beside `Δ̄`, with the channel's direction stated; mean
`CV` at the event and mean placebo as absolute values; `Δ̄` under all three rungs, under R-vol and R-session (each with
its included count against the floor of 10), and under S1 to S5; the shared-pool `Δ̄` of the event and its
spot-confirmed control and the bootstrap interval of their difference; the covariate-balance table with its gaps
stated; for the F1 sub-masks,
`F1_in_profit` and `F2_perp_extreme`, the contrast with the full event (`F1_in_profit` against F1 against says whether
the venue split at spike-level highs differs from the first push); for F1's open-interest split and F3, the numbers on
BTC 2022+ entries only, every open-interest-conditioned number marked with the latency caveat of section 5; for the
composition line, `Δ̄` per year shown beside P11's per-year base rate of `s·D^S ≤ 0` (USDT-only and USDT + FDUSD).
Cuts of `Δ`: per direction (chento), per asset (the replication line beside the decision line), per elapsed tercile
and per profit bin at the event (the first event is expected early, on the first push after the gate fits, so the test
asks about mild early information rather than tops), per session bucket and day type of the event minute (Binance spot
is more active in US hours and perps in Asia, so "perp-led" partly means "Asian-session extreme"; R-session is the
matched form of this cut), and the share of F1 windows crossing a funding settlement (00, 08, 16 UTC). Which rung each
test used. Base rates and coverage from P8 and P11. The exit-at-first-event cost per trade over the whole population
(price only), as stage 1's findings did.

Three statements the findings must make about the intervals. The **NO INFORMATION ≥ 0.10 R** label is unreachable at
these n: the half-width is about `1.96 × sd / √n`, which with stage 1's reference continuation-value sds (0.86 to
1.78 R, P11) and `n` between 30 and 200 is about **0.12 to 0.64 R**, so a null result will read UNDETERMINED, not NO INFORMATION,
and the findings say so rather than call it evidence of absence. The intervals are optimistic because placebos share controls (one control trade serves many
events): the findings report per test the mean number of distinct controls and the share of controls used by more than
one event, from P11. And the reported lines are many: the findings count the intervals reported and state that
roughly one in twenty is expected to exclude zero by chance, so a single reported line excluding zero is not a finding.

## 11. Information already seen (disclosure)

- **Everything stage 1 disclosed** (`PREREGISTRATION_MICROSTRUCTURE.md` section 11): the shipped outcomes of all three
  populations, chento's X1 / X2 flow-reversal arms, the squeeze_bull "flush resumed" arm, and the prior microstructure
  entries. **Stage 1's own results** on these same trades (`findings_microstructure.md`): chento E1 −0.07 (−0.66,
  +0.56), E2 −0.20 (−0.68, +0.32), E3 +0.22 (−0.52, +0.93), sign controls −0.07 / +0.33 / +0.13, halves positive in
  2021-23 and negative in 2024-26 for every event; era-matched exploratory E1 −0.14, E2 −0.16, E3 +0.13, with included
  counts falling from 119 to 98 and 215 to 193 (E3 44). E1 supportive (extreme buying with the position while price
  holds) already scored −0.07 on chento, and F1 is a subset of "buying with the position at a high"; a result near zero
  is the expected outcome and would say the venue split adds nothing.
- **The era match was promoted after seeing those numbers.** Making the ±365-day match primary (rungs 1 and 2) was
  decided after reading stage 1's era-matched `Δ̄` on the same chento trades (E1 −0.14, E2 −0.16, E3 +0.13, n 98 / 193 /
  44), which cannot be un-seen. The all-years no-extreme placebo S2 (stage 1's exact form) and rung 3 (all years with
  the extreme match) are reported so the size of that choice is visible. The events here are different kinds, but the
  trades and the period effect are the same.
- **squeeze_bull top anatomy, stage A** (`findings_top_anatomy.md`, `results/top_anatomy/`): on 56 of the same 122
  fires, premium index at the final top versus earlier paused highs +0.7 bp (−0.9, +2.5), 60-minute perp taker buy
  share +0.003 (−0.005, +0.012), volume ratio −0.21 (−0.51, +0.20). **Amendment A1** overlaid premium, taker share and
  volume ratio at 240, 120, 60, 30 and 15 minutes before the high on those fires: one marginal interval among 60
  (premium at −120 minutes, +0.60 (+0.006, +1.42)), and the plotted profiles (`results/top_anatomy/profiles_paired.json`)
  showed the premium rising into the final top from about −2.1 bp at −15 minutes to +1.65 bp at the high, and into the
  earlier paused highs from about −2.4 bp to +0.23 bp: both rise, the final top by more. So **squeeze_bull is not fully blind to a premium-change
  measure**: F1's 60-minute window is taken from the flow-window convention, not from that overlay, but the findings
  must say this. The state map showed fresh highs (last new high under 1 h ago) precede better next-24 h returns
  (+0.51 % against +0.12 % after a day's stall), which is why the extreme-matched placebo is primary and why rung 2's
  "fresh" bin is the state map's own first bin. chento and short_squeeze have never been examined with premium or spot
  data.
- **Open interest.** The OI-flush prior (section 2; `studies/notebooks/oi_flush`, 2026-06-05): a short flush (price up,
  OI down) is continuation, so the open-interest split is expected to separate two readings, not confirm one. The
  open-interest leg assumes zero publication latency (`anatomy_data`'s 300 s convention equals the Binance REST stamp;
  the real delay is unmeasured, `findings_top_anatomy.md` section 9), so every OI-conditioned number is caveated.
- **Spot composition.** From 2023-08 Binance's zero-fee BTC/FDUSD pair (and later BTC/USDC) took a large share of
  Binance spot BTC volume, so BTCUSDT spot taker flow is a partial slice through 2024, where squeeze_bull fires cluster.
  This was known when the composition line was added; the line is reported only and decides nothing.
- **Entry-side results on the same ingredients, none inside trades:** short_squeeze's live gate (spot minus perp
  15-minute CVD percentile divergence at swept lows, long only, n = 70, +0.40 R) works; its mirror at swept highs was
  rejected (n = 336, −0.22 R); the Coinbase-versus-Binance spot premium entry was killed (BTC +0.155 R, post-ETF
  negative; the same cross-venue structure as F1's premium leg); funding plus "spot" CVD divergence (n = 21, +1.09 R) is
  deferred and its spot leg was in fact perp; chento Rule 1 (whale CVD divergence) and whale absorption are dead;
  footprint confirmation is dead. None of these bears on an exit at a running extreme, and none of their thresholds is
  used here.
- **Not measured anywhere before this document:** no spot 1-minute series aligned to any trade; no spot-minus-perp
  taker quantity at 1-minute resolution; the premium change into a high as a statistic; continuation value at a new
  running extreme inside any population; any ETH premium series; any BTCFDUSD series at all.
- **Done for this stage before this document, and its limits:** HEAD checks on the archive; the download of all 372
  BTCUSDT and ETHUSDT zips with CHECKSUM verification (manifest 2026-09-17 20:20:09 UTC); a format scan of those
  downloaded files recording per-file row counts, header presence, timestamp unit, missing minutes inside the file
  span, zero-volume rows, duplicates and column count (`format_scan.json`); and the BTCFDUSD download (50 zips, kind
  `spot_1m`, 2023-08 monthly through 2026-08 and daily 2026-09-01 to 13), started 2026-09-17 before the freeze with the
  same script, CHECKSUM verification and manifest (its entries status `downloaded`; the manifest rewritten at 20:59:40
  UTC with the 372 earlier files re-verified as `present`). The scan was then rerun over all 422 files and extended
  with the `close_time − open_time` identity per file (missing-data rule 2 carries its result). **The panels were then
  built** (`spotperp_data.py build`, 2026-09-18, 93 s): the five `_panel` caches and their sidecars, whose facts P7
  checks. So spot, FDUSD and premium values **have** been placed on the minute grid, which the build is; **no value of
  any of them has been joined to a trade, to a trade's minute, or summarised on any trade or day**, and no event,
  feature or count of this stage's kinds exists. Everything computed so far is per-file and per-year panel bookkeeping.
  F0 hashes the built arrays, and no minute of them is read against any trade before it.
- **The concurrent liquidation-map study** in this folder (section 4) built its own spot caches on a different grid at
  20:58 UTC on 2026-09-17 and its own open-interest caches at 21:05 UTC. This stage neither wrote, ran nor reads any of
  them, and nothing indicates any of them was joined to a trade; they are listed here because they sit in the same
  `cache/` directory and P1 records their hashes as "present, not read".
- **External source:** the "leveraged blow-off" state comes from an unvalidated site surveyed 2026-09-17 with no
  backtest behind it. It is one candidate parameterisation (F3), reported only, never evidence.

## 12. Freeze

F0 after the preconditions and before any continuation value at an event: this file, `spotperp_data.py`,
`spotperp_lib.py`, `spotperp_run.py`, `spotperp_download.py`, `tests/test_spotperp_events.py`,
`tests/test_spotperp_data.py`, `results/spot_perp/preconditions.json`, the five sidecars of this stage's panels and no
others (`cache/{BTCUSDT,ETHUSDT,BTCFDUSD}_spot_1m_panel.meta.json`,
`cache/{BTCUSDT,ETHUSDT}_premium_1m_panel.meta.json`), `data/raw/spotperp/manifest.json` and `format_scan.json`, and
the hashes of every stage 1 frozen file this stage imports, written to `results/spot_perp/freeze_F0.json` with a UTC
timestamp by `spotperp_run.py freeze0`. Stage 1's frozen files (`micro_lib.py`, `micro_run.py`, `micro_data.py`,
`tests/test_micro_events.py`) are imported and wrapped, never edited: their `freeze_F0.json` hashes and notebook 03's
re-check must still hold. Then one outcome run writes `events.csv.gz`, `trades.csv.gz`, `report.json` and
`verdict.json` under `results/spot_perp/`. Neither step reruns. A bug found afterwards is fixed by a dated amendment
below, and both results are reported. Manifests, not commits, unless the user asks.

### 12.1 Design review record (nothing open)

Three independent candidate designs (2026-09-17), judged by two reviewers; one draft (v0.1, 2026-09-17); three
adversarial reviews of the draft (lenses: mechanism and confounds; statistics and placebos; causality and lookahead)
raising 33 issues; one amendment set applied 2026-09-17, every amendment decided on reading before any count on any
trade. The draft's four open points were resolved as follows: (1) population framing: chento-BTC in the family and
chento-ETH as the replication set, as written (the pooled-chento alternative is in section 5's disclosed alternatives);
(2) F2 is a family candidate under the ≥ 30 rule; (3) the stage verdict requires the chento-ETH line on every path,
so no promotion rests on BTC-only trades in their fourth use (section 8); (4) P6 is number-free and report-only, P5
decides (section 9). The other amendments, in the sections where they act: the spot-confirmed decision control and the
two reported controls (sections 2, 5, 7); the target-minute channel, its P11 count and `Δ_x` (sections 5, 6, 8, 9);
the placebo rungs in place of the count ladder, and R-vol and R-session (section 6); the unknown-status rule (section
6); the shared `b − 60 ≥ i0` gate (section 5); `F1_any_extreme`'s placebos (sections 2, 6); the open-interest controls,
latency caveat and flush prior (sections 2, 5, 10, 11); the F3 leg statement (sections 2, 5); the spot composition and
the BTCFDUSD line (sections 4, 5, 8, 11); the premium-leg reading (section 2); the era-match disclosure (section 11);
P8's denominator, P5 to P7, P10 and P11 as rewritten (section 9); the interval statements and `F1_in_profit` (section
10); the stage 2 requirements (section 8); and S1 to S5 (section 6).

**Round 2, 2026-09-18.** An application audit, a fresh adversarial review and an implementability map of v1.0 raised 2
blockers, 5 major and 6 minor issues and 9 internal inconsistencies. Every one was decided on reading, before any count
on any trade, and applied here. The substantive changes: the concurrent liquidation-map study in this folder is
disclosed and this stage's panels take the `_panel` suffix so no cache name collides (sections 4, 11, 12); the
`close_time − open_time` identity is recorded rather than asserted, with the 16 known spot rows listed and kept, because
asserting it would have stopped the stage on a fact already in the scan (sections 4, 5, 9); the control contrast moves
to a shared pool, because under the no-prior-event placebo an event and its spot-confirmed control are near mirror
images by construction and their inequality would have added no protection (sections 2, 6, 8); `Δ_x` is withdrawn
entirely and the target-minute channel is carried by counts, because a continuation value cannot be measured from a
close the exit already preceded (sections 5, 6, 8, 10); R-vol and R-session gain the same floor of 10 included trades
as the control, with the structural thinning stated in words so an unreachable INFORMATIVE is explained rather than
tuned away (sections 6, 8); the FDUSD leg of the composition line treats a carried zero-volume minute as zero flow
rather than as missing (sections 4, 5); the ETH non-overlap subset, its floor and the rung each line is read under are
defined exactly (section 8); and the remaining minors and inconsistencies were fixed in place. The build ran after
this round and before F0; its facts are in section 11 and its checks in P7.

### 12.2 Amendment A26, 2026-09-18: the premium alignment check (made after seeing it fail)

**What happened, in order.** The preconditions were run on the built panels. Ten of eleven passed. P5 failed on 4 of
its 28 lines, all of them premium lines: BTC 2020 peaked at lag −1, BTC 2023 at +2, ETH 2020 at −1, ETH 2023 at −3.
This amendment was written after reading that result, which cannot be un-seen, so both the original form's result and
the amended form's result are reported.

**Why the original form could not work.** P5 as written correlated the premium *level* with the *level* of
`close^P / close^S − 1`. Both are strongly autocorrelated, so every lag from −3 to +3 scores about 0.92 and the peak is
noise: on BTC 2020 the seven lags were 0.916, 0.927, 0.956, 0.929, 0.918, 0.910, 0.907. The check therefore could not
tell an aligned series from a shifted one in either direction. One of the round-1 reviewers predicted exactly this. The
spot-versus-perp half of P5, which is computed on returns, was decisive in every asset-year: on BTC 2020 lag 0 scored
0.974 against about 0.00 to 0.02 at every other lag, and all 14 returns lines peak at lag 0.

**The amendment.** The premium half is computed on **differences**, like the returns half. Nothing else about P5
changes and no event definition, threshold, population or statistic is touched: P5 is a data-integrity check on
timestamp alignment.

**What the amended form shows.** It resolves both 2023 lines (BTC and ETH now peak at lag 0). It does **not** resolve
2020: on differences, BTC 2020 peaks at lag −1 (0.484 there against −0.130 at lag 0) and ETH 2020 likewise (0.400
against −0.133). That is a real one-minute offset in the 2020 premium archive, not a defect of the test, and it is
recorded rather than repaired. Every asset-year from 2021 on peaks at lag 0: BTC 0.189, 0.103, 0.125, 0.325, 0.284,
0.226 and ETH 0.439, 0.040, 0.094, 0.279, 0.184, 0.178, each above its next-best lag.

**Why 2020 does not stop the stage.** No population reads 2020. The earliest entry of any population is chento-BTC's
2021-04-17 00:15 UTC; the earliest minute any F1 window can reach is 60 minutes before it, 2021-04-16 23:15 UTC, and
the earliest an F2 normalisation window can reach is about 2021-04-10. The decision scope is therefore the asset-years
the populations can read, 2021 onward, and 2020 is reported. Had a population reached into 2020, the offset would have
had to be understood first.

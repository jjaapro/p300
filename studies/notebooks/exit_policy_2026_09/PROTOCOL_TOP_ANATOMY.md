# Exit-policy study, squeeze_bull top anatomy — PROTOCOL v1.0 (EXPLORATORY, stage A)

**Written 2026-09-15, before any feature below was computed on any fire's path after entry.** Stage A is exploratory:
it describes, it does not decide. Any exit rule it suggests is tested only in stage B, on data stage A never touches
(section 8). squeeze_bull stays report-only until its n = 20 / 30 re-cuts.

The user's question (2026-09-15, after the microstructure stage): *"What if we study what happened after squeeze bull
got an entry. Assumption is that there is spike up, like we just had. What happened just before the price action
reversed? How does it look like and how does the all previous similar events look like? Maybe there is no reverse at
all, but something else that tells it's time to exit?"*

The case that prompted it: SJ-4250 entered 2026-09-11 18:00 UTC at 77,493.9 and closed on its 48 h time stop at
77,276.14 on 09-13 17:00. Price then spiked to 79,570.9 (+2.68 %) on 09-14 20:15 and fell back to about −0.9 % by
09-15 08:13.

## 1. The trap this protocol is built around

Aligning paths on their highest point always shows a rise into it and a fall after it, whatever the data. Features
that follow volatility (volume, flow extremes) also cluster at any extreme. The question that matters for an exit is
narrower. At a new high that pauses, is anything visible in real time that tells the final top from the pauses that
kept going? Every feature here is measured causally at the minute in question, and the comparison is **the top against
the same fire's earlier paused highs ("false tops")**.

## 2. Mechanism first: what "the bounce is over" could mean

squeeze_bull buys after a long flush: open interest falls ≥ 2 % and price ≥ 0.5 % over four hourly bars. The bounce is
the repair of that dislocation. Four readings of "over", each with its measure:

| Family | Reading | Measure (causal at minute m) |
|---|---|---|
| **A. Repair** | the dislocation is undone | **A1 price repair** `PR = (close − E) / (P0 − E)`: 0 at entry, 1 at the price the flush started from. **A2 OI repair** `OR = (OI − OI_E) / (OI_0 − OI_E)`: 1 when the open interest the flush destroyed is back. Neither has a free number: both are set by the rule's own four-bar window |
| **B. Crowding** | longs are paying up again | B1 premium index (perp over index, bp); B2 top-trader position long/short ratio, % change since entry; B3 all-account long/short ratio, % change since entry |
| **C. Exhaustion** | buying stops moving price | C1 taker buy share of volume over 60 min; C2 60-min volume ÷ (60 × mean 1-minute volume over the trailing 7 days); C3 order-book ±1 % imbalance z (2023+, the microstructure stage's feature) |
| **D. Stall** | momentum is gone, no reversal needed | D1 minutes since the last new running high; D2 hours since entry |

`E` entry (trigger bar close); `P0` the close of hourly bar i−4 (the rule's base); `OI_E`, `OI_0` the archive OI
snapshots stamped at entry and at bar i−4's close.

## 3. Data

- **Price and flow:** Binance BTCUSDT perp 1-minute panel (ORB study cache; zero-volume minutes missing).
- **Open interest and long/short ratios:** Binance `metrics` daily archive, 5-minute snapshots, 2022-01-01 → 2026-09-14,
  checksum-verified. Rows can be unsorted and duplicated; duplicates are identical and dropped. **A snapshot stamped T
  is used from T + 5 min.**
- **Premium index:** Binance `premiumIndexKlines` 1-minute archive, same span.
- **Book:** the microstructure stage's ±1 % arrays.
- **The case, SJ-4250:** the same sources through 2026-09-14 from the archive, then Binance public REST for
  2026-09-15 (1-minute klines, premium index, 5-minute OI and ratios). No book after 09-14.
- prod.db is read only, for SJ-4250's trade row.

## 4. Fires and the shape of each bounce

**Discovery set:** the 122 bull fires of the squeeze_bull arm (the ledger's `regime_backonly == "bull_30d"`, trigger
bars 2022-03-25 → 2026-09-04). **Case:** SJ-4250. Horizon: 7 days after the entry minute `i0`, regardless of stop or
time stop, because the question is what the market did.

- **Spike start S:** first minute with high ≥ E × 1.02 (+1 R).
- **Running max M(m):** highest high from `i0` through m.
- **Reversal C:** first minute at or after S with low ≤ 0.98 × M(m), a 1 R fall from the peak.
- **Top T:** the first minute in `[i0, C]` whose high equals M(C).
- **Classes:** *spike then reversal* (S and C within 7 days), *spike, no reversal* (S, no C), *no spike* (no S).
- **False tops:** minutes m in `[S, T)` that set a new running high with no higher high in the next 30 minutes.

## 5. What stage A reports

1. **Shapes.** Class counts. Hours entry → S, S → T and entry → T; run-up at T; how many tops came after 48 h (where
   the time stop had already closed the trade, as with SJ-4250); depth of the fall in the 24 h after T.
2. **Where tops sit against repair.** PR and OR at T. The share of fires reaching PR ≥ 1 and OR ≥ 1 within 7 days, and
   what price did after first reaching each: move to +24 h, and the share of those moments below the eventual top.
3. **Top against false tops.** Every feature at T minus the mean over that fire's false tops, for fires with at least
   one false top. Median difference with a fire-bootstrap 95 % interval (10,000 draws, seed 42). Median profiles from
   12 h before to 12 h after, T and false tops overlaid. **Around 11 features are compared on one discovery set: expect
   one or two intervals to exclude zero by chance.**
4. **State map (the "no reversal" question).** At every hour from entry to 7 days, the state (PR, OR, D1, D2, premium)
   and the move to +24 h. Mean move by bin of PR, OR and D1, with fire-bootstrap intervals, beside the bull regime's own
   +24 h drift from the squeeze_bull arm's exploratory control.
5. **The case.** SJ-4250 from the flush to 09-15: price with the P0 and target levels, OR, premium, ratios, flow and
   book, marking entry, time stop, spike, top and reversal.
6. **Gallery.** Every *spike then reversal* fire aligned on its top (±24 h), price in % of the top, with the P0 level and
   the time stop marked.
7. **Data checks.** P0 / E against the ledger's `px_chg_4h`. Archive OI change over the rule's window against the
   ledger's `oi_chg_4h`. The lag at which the ledger's hourly `oi_close` matches archive OI, by month: a timestamp
   convention change was seen on three sample days before this protocol, and is reported, not fixed.

## 6. Picking candidates (end of stage A)

At most **three** exit cues go forward, each written as a complete rule. Preference order: a mechanism reading from
section 2; **no free number** (A1 or A2 as defined), else exactly one, taken from stage A's descriptive tables and named
as fitted; and firing on at least 30 % of discovery fires. If nothing meets these, stage A says so and stage B does not run.

## 7. What stage A may not do

- No path, feature or outcome of the holdout fires (section 8) is computed, plotted or counted; not even their fire lists.
- No threshold of the shipped squeeze_bull rule is judged on this data.
- Nothing in production changes.

## 8. Stage B, pre-committed now

A separate pre-registration, written after stage A and before any holdout computation, will test the candidates on:

1. **ETH long flushes** (primary). The sleeve's own `is_flush` and 24-bar event cooldown on ETHUSDT perp hourly closes
   and archive OI, with ETH's own backward-only 30-day return > +10 % as the bull gate, 2021-12 → 2026-09.
2. **BTC flat- and bear-regime flushes** (secondary). The ledger's other 301 fires; the regime differs, so a failure
   here is read with that in mind.
3. **Live squeeze_bull fires** as they accrue.

Each candidate is measured as net R paired against the shipped exits (S0 and the no-stop twin N0) with the squeeze_bull
arm's accounting, plus the continuation-value information test. The decision rule is written there, before any number.

## Amendment A1 (2026-09-15, after reading the first stage A run, before any stage B work)

The first run (`results/top_anatomy/manifest_A.json`, 16:31:29 UTC) overlaid the median profile of all 103 tops on the
median profile of false tops. The false tops come from the 56 fires that have them, so the overlay compared different
fires. The overlay is recomputed on the same 56 fires by `anatomy_amend_a1.py` (manifest `manifest_A1.json`):

- the median top profile and false-top profile on those fires;
- the per-fire difference (top minus false-top mean) at every lag, with its median and a fire-bootstrap 95 % interval
  (10,000 draws, seed 42) at 240, 120, 60, 30 and 15 minutes before the high, and at the high.

The profiles prompted this: volume approaching the final top looked lower, and the premium less negative. It is
descriptive like the rest of stage A, and it adds comparisons. The first run's outputs are kept unchanged.

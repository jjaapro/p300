# Exit-policy study, stage R: move vs implied range (I) and the rejection-wick exit (J): PRE-REGISTRATION

**v1.0, written 2026-09-19 before any event was computed on any trade; frozen at F0 by `rangewick_run.py freeze0`.**
An information test in the form of the spot-vs-perp stage (`PREREGISTRATION_SPOT_PERP.md`, frozen 2026-09-18) and of
microstructure stage 1 before it: it asks whether two moments inside a trade — the first minute at which the trade's
favourable move exceeds one option-implied daily move, and the first 15-minute bar that rejects from a level while the
trade is in profit — say anything about what the rest of the trade is worth. **Nothing here permits a production
change.** A positive result permits one thing: a separate stage 2 pre-registration of exit arms that use exactly these
event definitions.

The prompt is the roadmap's step 4 (`BACKLOG.md` §0 and §3 item 2, 2026-09-19): *"the two free tests the exhaustion
brainstorm left, as exit-information tests with placebos: move vs implied range (…a magnitude test has no
level-distance confound, and its placebo is the same rule with the implied range replaced by trailing realised
volatility) and the rejection-wick exit (Paladin's, +0.12 R on his entries, never tried on our sleeves; its levels
must be causal)"*, rows I and J of `studies/material/exhaustion_signals_2026_09/BRAINSTORM.md`. They are the last of
that family worth running; everything else in the brainstorm is dead or blocked.

Status: every number below has its provenance stated (a convention, a prior frozen value, the mechanism's own
definition, or a value from the Paladin study that is the prior being tested); no number was chosen by looking at an
outcome; nothing is open. No count on any trade population has been made. Section 11 discloses what was seen while
scoping. Nothing under `strategies/`, `bots/` or the feeds is read or touched; `prod.db` is opened read-only once, for
the DVOL table (section 4).

## 1. Why an information test before exit arms

The argument of the earlier stages holds unchanged. An exit arm's result mixes what the trigger knows with the
mechanical effect of leaving early, and on trades with positive drift leaving early costs R whatever triggers it
(chento's shorter time stops lost 0.13 to 0.57 R). The continuation value at the first event, compared with the
continuation value of other trades at the same point in their life, in the same profit bin and direction, with no
event yet, is zero when the event carries no information, whatever the exit rule and the strategy's drift.

What is already known and shapes what is new here:

- **Stage 1** found that a rejection of the prior 24 h extreme (E2, a break of the level followed by a close back
  inside it within 15 minutes, any profit) says nothing detectable on chento (Holm p 0.69–0.83). Stage R's J differs
  in three ways that are the content of Paladin's rule: it is **armed only in profit** (≥ 0.3 R unrealised at the
  bar's extreme), it reads the **bar's shape** (a rejection shadow of at least 0.4 of the bar's range), and its level
  set includes **round numbers and the trade's own target**, not only the 24 h extreme.
- **The Paladin study** (`studies/notebooks/paladin_study/exit_wick_study.py`, addendum 2026-08-23) found the
  mechanical translation of his manual exit worth **+0.122 R per trade gross, 81 % win rate** on his 173 positions
  (best variant P_min 0.3, wick 0.4; positive in all 8 variants and both halves; 42 confirmed stop-dodges). That
  result is an **exit-arm** number on **his** entries; it has never been measured on our sleeves, and the
  study-validation audit flagged his levels as future-built. Section 5 notes that his round grid was too fine for his
  own tolerance, which matters for reading his result (section 11).
- **The squeeze_bull top anatomy** (stage A) found the bounce's extra return fades after 24 h without a new high and
  once price is past 1.5 flush-sizes; tops sit about +3.2 % above the flush. The implied daily move of BTC at a DVOL
  of 50 is about 2.6 %, so "past one implied day" and "past 1.5 flush-sizes" are neighbours; I is the causal,
  option-priced form of that observation.
- **DVOL state signals as entries** were rejected (the VRP study, the calendar and regime cells); as an exit scale the
  implied move has never been tested.

## 2. Mechanisms (what each event claims)

**I — move vs implied range.** The Deribit DVOL index is the market's price of a day's range: an annualised implied
volatility from the option surface, whose one-day scaling `DVOL / √365` is the move the market expects over the next
UTC day. A trade whose favourable move from entry has already consumed one implied day has travelled the distance the
option market was pricing for a day of movement; what remains is more likely give-back than continuation, because the
demand or supply that made the move has met the range the market expected and the marginal participant is now a
mean-reverter (a delta hedger, an arbitrageur, a profit-taker). The claim is about the **scale**: the implied move,
not the realised one. The decision control is therefore the same rule with the implied daily move replaced by the
trailing realised daily volatility (`RV`, section 5): if the two score the same, the option market's number adds
nothing over "the move is large by recent standards" and the event is a profit-bin effect the placebo already
matches. The reported day form (I2) is the brainstorm's other reading — the move from the UTC day's open rather than
from the entry — and says whether it is the day's range or the trade's own excursion that matters.

**J — rejection wick at a level.** Paladin's exit trigger: a bar whose favourable extreme reaches a level the trader
watches (a round number, a published target, a prior extreme) and closes back on the entry side with a long rejection
shadow is supply met at the level; the trade books there because the level held. The claim has two legs: the **level**
(supply sits at round numbers and at prior extremes) and the **shape** (the rejection shadow is the visible trace of
that supply). The decision control holds the arming and the shape and flips the level leg: the same rejection shadow
away from any level (`J_nolevel`). If the two score the same, the level adds nothing and the event is "a bar that
gave back its range while in profit", which is the state the profit bin and the freshness match already describe. The
reported acceptance control (`J_accept`, the level reached without the rejection shape) is the continuation reading;
the reported shape-only form (`J_shape`) is what Paladin's rule effectively tested (section 5).

| Kind | Claim | Event (tested) | Decision control | Reported |
|---|---|---|---|---|
| **I1** move vs implied range | the trade has consumed one implied day of range | first minute with `mv_b ≥ IMP_b` | `I1_realised`: `mv_b ≥ RV_b` | `I1_implied_only` (implied crossed, realised not yet: the minutes where the option scale is the smaller one); `I2_day` and `I2_day_realised` (the move from the UTC day's open) |
| **J** rejection wick at a level | supply met at a level while in profit | first 15-minute bar, armed at ≥ 0.3 R, at a causal level, with a rejection shadow ≥ 0.4 of its range | `J_nolevel`: the same armed rejection shape with no level hit | `J_accept` (level hit, no rejection shape); `J_shape` (armed rejection shape, level ignored); `J_wick_1m` and `J_nolevel_1m` (the same rule on 1-minute bars, Paladin's 1 m arm) |

## 3. Populations and the one price path (frozen)

The four subpopulations, entries, stops, targets, horizons and R are the spot-vs-perp stage's, which are stage 1's,
unchanged and hash-checked (`micro_lib.load_population`; chento features BTC `c1762d57…`, ETH `83663d81…`;
squeeze_bull ledger `d3793c4e…`; short_squeeze replay `5e9fd545…`).

| Population | Trades | Role | Entry | Stop / target / time exit | R |
|---|---|---|---|---|---|
| **chento-BTC** | the 208 BTC gate-off entries (long and short) | **decision (family)** | signal bar open + 15 min | stop 1 R, target 6 R, **72 h** | the features' `risk` |
| **chento-ETH** | the 184 ETH gate-off entries | **replication set** (identical frozen rule on ETHUSDT; its agreement is required for any promotion) | same | same | same |
| **squeeze_bull** | the 122 bull fires, long, BTC | **decision (family)**; its agreement is reported, never sufficient | trigger hour open + 1 h | −2 %, +3 %, **48 h** | 2 % of entry |
| **short_squeeze** | the 71 replay triggers, long, BTC | descriptive | trigger 15 m bar open + 15 min | replay stop, 3 R, **6 h** | entry − stop |

**Amendment A1 (2026-09-19, before F0; section 12).** The squeeze_bull population is the
**corrected-open-interest re-cut** of `full_oi_flush_ledger.csv` (commit `599e8b6`, sha256 `4010efdd…`), not stage
1's frozen ledger (`d3793c4e…`): item 30 re-stamped the open-interest table on 2026-09-18 and the revalidation study
was re-run on it the same night. The two sets share 121 of 122 fires; stage 1's `BTC:1788530400` (2026-09-11 17:00,
the bar that fired only on the mis-stamped table) is replaced by `BTC:1788526800` (16:00). Testing on the set known
to contain a phantom fire was the alternative and was not taken. The chento and short_squeeze populations are stage
1's, unchanged.

**Eligibility for the I family:** entries at or after **2022-09-08 00:00 UTC**, the first UTC day whose prior day has a
completed row in `deribit_dvol_daily` (its first row is 2022-09-07). Every J kind is eligible on every trade. The
I-eligible counts are recorded at P2 (the expected values, seen while scoping, are in section 11).

Placebo controls are drawn **within each subpopulation**, as in the spot-vs-perp stage (a chento-BTC event is matched
only to chento-BTC trades). **Price path:** the ORB study's Binance USD-M perpetual 1-minute panels
(`orb_study/cache/BTCUSDT_perp_1m.npz`, `ETHUSDT_perp_1m.npz`, 2020-01-01 00:00 UTC to 2026-09-14 exclusive,
3,525,120 minutes, no missing rows; hashes checked at P1). A minute with zero volume is dead: no fill, no mark, no
event. **Walker:** stage 1's (`micro_lib.walk`), unchanged; P3 requires it to reproduce stage 1's saved walks trade by
trade. **Secondary walk (reported, not decided):** no time exit, censored at 720 h.

**Out of scope:** the no-stop twins, ADX, CARRY and R4; the 301 BTC flat- and bear-regime flushes and every ETH flush
(the top anatomy's pre-committed stage B holdouts) stay untouched.

## 4. Data added for this stage

- **`deribit_dvol_daily`** from `prod.db`, opened read-only once by `rangewick_run.py checks` and exported to
  `results/range_wick/dvol_daily.csv` (`asset, day, open, high, low, close`), which is hashed into the freeze and is
  the only DVOL input the outcome run reads. Rows are stamped at the UTC day open; the row of the current UTC day is
  partial and is dropped at export. Facts recorded at P4: rows per asset, first and last day, days missing inside the
  span, rows with a non-positive or non-finite close. The coverage plan (2026-09-18) measured the series clean from
  2022-09-07 with no gaps; a missing day yields no I event on the following day (missing-data rule 3) and is not
  filled.
- **Nothing else.** No spot, premium, open-interest or book array is read. The stage 1 series builder still loads the
  book cache for its `zq` field; no kind reads it.

## 5. Event definitions

`s = +1` long, `−1` short. Minute `b` is the bar opening at `b`, known at its close. Every event of every kind
requires: the trade is open after `b` (`b < x`), `close^P_b` is not missing, **`b − 60 ≥ i0`** (the shared gate, so the
freshness and extreme quantities have an hour of history and the kinds are comparable), and every input the kind needs
is finite at `b`. Only the **first** event of each kind in a trade is used; later minutes of the same kind are counted
(`*_minutes`) but not used.

### Per-minute quantities (per asset, computed once on the whole panel)

- **UTC day** `D(b)` of minute `b`; the grid starts at 2020-01-01 00:00 UTC, so day `k` is minutes `[1440k, 1440k +
  1440)`.
- **Implied daily move** `IMP_b = DVOL_close(D(b) − 1) / 100 / √365`, the prior UTC day's DVOL close (final at
  `D(b)` 00:00) scaled to one day; missing when that row is absent or `D(b)` precedes 2022-09-08. Using the day of the
  minute rather than the day of the entry keeps the scale current through a 72 h trade; both are causal.
- **Realised daily volatility** `RV_b`: the sample standard deviation (ddof 1) of the 30 daily log returns
  `ln(C_d / C_{d−1})` for `d = D(b) − 30 … D(b) − 1`, where `C_d` is the close of the last present minute of UTC day
  `d`; missing when any of the 31 day closes `C_{D(b)−31} … C_{D(b)−1}` is absent (a day with no present minute).
  Unscaled: a daily standard deviation, the same unit as `IMP_b`.
- **Move from entry** `mv_b = s · (close_b − entry) / entry`.
- **Move from the day's open** `dv_b = s · (close_b − O_{D(b)}) / O_{D(b)}`, where `O_D` is the open of the first
  present minute of day `D`; missing when the day has none.
- **15-minute bars:** minute `b` belongs to the bar `⌊b / 15⌋` (the grid's minute 0 is 00:00 UTC, so bars are the
  exchange's 15-minute bars). A bar's open is the open of its first present minute, its high and low the max and min
  over its present minutes, its close the close of its last present minute; a bar with no present minute is missing.
  The bar is **known at the close of its last grid minute**, which is the event minute `b` of every J kind (so the
  rule "open after `b`" and the gate apply to that minute). A bar is used only when it lies **entirely inside the
  trade**: its first grid minute is at or after `i0` (the entry bar, which the entry cuts, is never an event bar) and
  its last grid minute `b < x`.
- **Unrealised R at the bar's extreme** `u = s · (ext − entry) / risk`, `ext` = bar high (long) or bar low (short).
- **Rejection shadow** = `high − max(open, close)` (long) or `min(open, close) − low` (short); **range** = `high − low`.
- **Causal levels** of a trade, fixed at entry: (i) the **round grid** with step `10^(⌊log10(entry)⌋ − 1)` (BTC at
  10,000–99,999 → every 1,000; ETH at 1,000–9,999 → every 100; ETH below 1,000 → every 10), every multiple; (ii) the
  trade's **own target** (chento entry ± 6 R, squeeze_bull +3 %, short_squeeze's replay target); (iii) the **prior
  24 h extreme** at entry, stage 1's `level_of` (the highest high, for a short the lowest low, over the 1,440 minutes
  before `i0` with at least 1,000 present) when `level_valid` (at least 0.1 % beyond entry). A level is a
  **candidate** when it lies at or beyond entry in the trade's direction within tolerance: `s · (L − entry) ≥ −TOL ·
  entry`, `TOL = 0.0015`.
- **Level hit** at a bar: some candidate level `L` is **near** (`|ext − L| ≤ TOL · entry`) or **traded through**
  (`s · (ext − L) ≥ 0` and `s · (close − L) < 0`: the bar reached beyond `L` and closed back on the entry side).
- **New running extreme** `X_b`, **stall** and the **ordinal** of the extreme: the spot-vs-perp stage's definitions on
  the perp path (`spotperp_lib.running_extreme`, `stall_from_extreme`; strict, entry minute excluded, dead minutes
  ignored). **Fresh:** `stall_b < 60`.
- **Volume ratio** `VR_b` (`anatomy_lib.volume_ratio` on perp volume), **session bucket** and **day type** of `b`: as
  in the spot-vs-perp stage (R-vol and R-session covariates, never legs).

### I: move vs implied range

- Inputs: `IMP_b` and `RV_b` finite (one eligibility for the whole family, so the event and its control are counted on
  the same minutes; `I2` additionally needs `dv_b` finite).
- **I1_implied (tested):** `mv_b ≥ 1.0 × IMP_b`. The favourable move from entry has reached one implied daily move.
- **I1_realised (decision control):** `mv_b ≥ 1.0 × RV_b`. The same rule on the realised scale.
- **I1_implied_only (reported):** `I1_implied` and not `I1_realised` at the same minute (the implied move is the
  smaller scale there, so the option market is pricing less range than recently realised).
- **I2_day (reported):** `dv_b ≥ IMP_b`; **I2_day_realised (reported control):** `dv_b ≥ RV_b`.
- The multiplier is 1.0: one implied day, the mechanism's own number; no sweep.

### J: rejection wick at a level

On 15-minute bars (the tested form) and, reported, on 1-minute bars (each minute is its own bar; the same conditions,
the same levels, the same arming):

- Inputs: the bar present with `range > 0` (a bar with `range = 0` cannot have a shadow and is neither event nor
  control; it is counted).
- **Armed:** `u ≥ 0.3` (P_min, Paladin's best variant).
- **Rejection shape:** `shadow ≥ 0.4 × range` (WICK_FRAC, Paladin's best variant).
- **J_wick (tested):** armed, level hit, rejection shape.
- **J_nolevel (decision control):** armed, no level hit, rejection shape.
- **J_accept (reported control):** armed, level hit, no rejection shape (the level reached and held onto: the
  continuation reading).
- **J_shape (reported):** armed, rejection shape, level ignored (`J_wick ∪ J_nolevel`).
- **J_wick_1m, J_nolevel_1m (reported):** the same on 1-minute bars.
- A bar can be `J_wick` or `J_nolevel`, never both; `J_accept` and `J_wick` are exclusive.

### Missing-data rules

1. A perp minute with zero volume is dead: no mark, no fill, no event, no control minute (stage 1).
2. A 15-minute bar with no present minute is missing; a bar with some present minutes is formed from them. A
   control's J status is known at minute `e′` only when every bar ending at or before `e′` inside the control trade,
   at or after the gate, is present (the unknown-status rule of section 6 applied to bars).
3. `IMP_b` is missing where the prior day's DVOL row is absent; `RV_b` where any of its 31 day closes is absent; a
   minute with either missing is an event minute of no I kind and no I control minute, and a control's I status is
   known at `e′` only when every extreme at or before `e′` at or after the gate had both finite.
4. `dv_b` is missing when the day has no present minute before `b`.
5. A level set is computed once per trade from the entry and the trade's own slice of the panel (the grid spans the
   trade's price range); a trade without a valid 24 h extreme has the grid and its target only.
6. Every threshold (`mv ≥ IMP`, `u ≥ 0.3`, `shadow ≥ 0.4 × range`) is evaluated with a tolerance of one part in 10⁹
   (10⁻¹² absolute on the fractional moves) in the event's favour, so a bar exactly at a boundary is an event and
   floating-point rounding never decides one.

### Provenance of the numbers (chosen here, before any count)

- **P_min 0.3 R, WICK_FRAC 0.4, TOL 0.15 %, 15-minute bars, the 1-minute arm, the round grid, the published target as
  a level, "through and close back" as a hit:** Paladin's pre-registered mechanical translation and its best variant
  (`exit_wick_study.py`, 2026-08-23) — the prior under test, not re-chosen. **One departure, stated:** his grid step
  `10^(⌊log10 p⌋ − 2)` puts BTC levels every 100 dollars, which at 0.13 % spacing is inside his ±0.15 % tolerance
  everywhere, so his level condition was vacuous on BTC (and near it on ETH, every 10 dollars at 0.3 % spacing) and
  his result is the shape's. The step here is ten times coarser so that "at a level" is a condition, and `J_shape`
  reports the vacuous form beside it.
- **The prior 24 h extreme as a level:** stage 1's `level_of`, `LEVEL_LOOKBACK 1440`, `LEVEL_MIN_PRESENT 1000`,
  `LEVEL_MIN_DISTANCE 0.001`, inherited.
- **1.0 × the implied daily move:** the mechanism's own number. **`/ √365`:** the one-day scaling of an annualised
  volatility, the convention DVOL is quoted in. **The prior day's close:** the last final value before the day.
- **30 days** for `RV`: the repository's regime window (`ret_30d`, the chento regime filter, the anatomy's
  `ret_30d_backonly`), the only trailing window in use; not re-chosen. **31 day closes required:** the definition.
- **2022-09-08:** the first UTC day with a completed prior row in `deribit_dvol_daily` (first row 2022-09-07, a data
  fact).
- **Shared constants, inherited from the spot-vs-perp stage and stage 1, none re-chosen:** the 60-minute gate; strict
  running extremes; fresh = stall < 60 (`anatomy_lib.STALL_EDGES`); ±365 days (`micro_explore.ERA_DAYS`); W = 5 % of
  the horizon (216 min chento, 144 squeeze_bull, 18 short_squeeze); 0.25 R bins; 3 contributing controls; 30 included
  event trades for the family and the decision rung; 10 for a control or robustness line; 0.10 R equivalence; α 0.05;
  30-day blocks, 10,000 draws, seed 42; the session buckets (`bots/short_squeeze/strategy/config.py` `SESSIONS`, read
  as text) plus the 21–24 UTC remainder; VR on the same side of 1; 5 causality cuts per asset, seed 42; 0.90 coverage.
- No value in this section was tried and discarded; no count of any kind on any trade preceded it.

### Disclosed alternatives not chosen

The elapsed-time-scaled implied move (`IMP · √(e / 1440)`: at the gate it is a fifth of a day and trips on most
trades within hours; the brainstorm's wording is the daily range). A multiplier sweep (0.5, 1.0, 1.5, 2.0). An
ATR-scaled move (the sleeves' own R is already ATR-based for chento, so the profit bin covers it). The implied move
of the entry day held fixed through the trade. Paladin's fine grid as the tested level set (reported as `J_shape`).
Hourly or 4-hour bars for J (his study used 5, 15 and 1 minute). The exit-arm overlay as the decision (reported only,
section 8). Pooling chento's assets. A "level within the next N minutes" look-ahead as the false-top label.

## 6. Statistic: continuation value against matched placebo minutes

For trade `j` open after minute `m` (elapsed `e = m − i0_j`): mark `M_j(e) = s · (close_m − entry) / R` and
**continuation value** `CV_j(e) = s · (exit_price − close_m) / R`, price only, to the shipped exit. Profit bin
`k = ⌊M / 0.25⌋`. For kind `F`, event trade `i` with first event at elapsed `e_i` and bin `k_i`:

- **At-risk controls:** trades `j ≠ i` in the same subpopulation, same direction, whose interval `[entry_j, exit_j]`
  does not overlap `[entry_i, exit_i]` in calendar time, and, under rungs 1 and 2, entering within ±365 days of trade
  `i`'s entry.
- A control contributes its minutes `e′` with `|e′ − e_i| ≤ W` where it is open, at or after the gate, its close
  present, **the kind family's inputs finite at `e′`**, it has had no `F` event at or before `e′`, its bin equals
  `k_i`, the rung's freshness condition holds at `e′`, and **its status is known** at every extreme up to `e′`
  (missing-data rules 2 and 3).
- **The freshness match, kept in every decision placebo.** An I event happens as price pushes through a threshold and
  a J event within a bar that set a new extreme, so both are fresh-extreme moments; a control minute must also be
  fresh (`stall_{e′} < 60`) under rungs 1 and 3, and a strict new running extreme (`X_{e′}`) under rung 2. This
  removes the mechanical component of being at or near the trade's best price, so that `Δ` measures the event's
  content, not the state "at a high".
- `c_j` = mean `CV_j` over its contributing minutes; **placebo** `P_i` = mean of `c_j` over contributing controls; at
  least **3 contributing controls**, otherwise trade `i` is dropped from the test (counted).
- `Δ_i = CV_i(e_i) − P_i`; the test statistic is the mean `Δ̄` over included event trades.

**Placebo rungs (decision placebos; decided at P8 on counts only, before any continuation value):**

| Rung | Control minute must be | Era pool |
|---|---|---|
| **1 (primary)** | fresh (`stall < 60`), same bin | ±365 days |
| **2** | a new running extreme (`X`), same bin | ±365 days |
| **3** | fresh, same bin | all years |

The first rung, in this order, under which a candidate test has **at least 30 included event trades** is its decision
placebo; if none reaches 30 the test is DESCRIPTIVE. `Δ̄` is reported under all three rungs for every test. A test's
decision control, its reported controls and the ETH replication line use the same rung as the event test they
accompany.

**The shared pool, for the control contrast only.** As in the spot-vs-perp stage, the control contrast (the
INFORMATIVE condition and the reported difference interval) is computed against **one shared pool** used by both the
event and its decision control: control minutes on other trades of the subpopulation, same direction, non-overlapping,
within the rung's era window, same bin, at or after the gate, the family's inputs finite, the rung's freshness
condition satisfied, **whatever their legs and their prior-event history**. The level statistic `Δ` keeps the
no-prior-event placebo.

**Robustness placebos (required for INFORMATIVE; the decision rung plus one sign-only constraint each):** **R-vol**
(the control minute's `VR` on the same side of 1 as the event minute's; an event minute with a missing `VR` is dropped
from the line, counted) and **R-session** (same session bucket and day type). Each line needs at least 10 included
event trades; below that it is *unavailable* and the label becomes "INFORMATIVE, robustness unavailable", which never
counts toward PROMOTED.

**Secondary placebos (reported, never decided):** **S1** era + bin without the freshness match; **S2** all years
without it (stage 1's exact form); **S3** time-only (the decision rung's era and freshness, no bin); **S4** `Δ` with
continuation values from the no-time-exit walk. The difference between rung 1 and S1 is the size of the at-a-high
effect; between rung 1 and rung 3 the era effect.

**The target-minute channel.** `micro_lib.walk` exits at the minute whose high (long) or low (short) reaches the
target, so the target-filling minute is `x` itself and never an event minute. For I1 on squeeze_bull this matters
structurally: its target (+3 %) is about one implied daily move at a DVOL near 55, so the crossing and the target
fill will often be the same minute, and such a trade has no event and serves as a control. As in the spot-vs-perp
stage the channel is measured in counts, not in a second `Δ` (P8): per subpopulation and kind, the trades whose first
pattern minute with `b ≤ x` is the exit minute, by the walker's exit kind. The findings must state its size and
direction beside any negative `Δ̄`.

**Inference:** unchanged. `Δ_i` assigned to trade `i`'s entry day; circular block bootstrap, 30-day blocks, 10,000
draws, seed 42 (`micro_lib.block_indices`); 95 % percentile interval; one-sided `p` for `Δ̄ < 0`; halves by entry
order; Holm over the family. Also reported: the block bootstrap of `Δ̄(event) − Δ̄(decision control)` on the shared
pool.

**Covariate balance (required, no outcomes):** at event minutes versus their contributing control minutes under the
decision rung, per test: median `VR`, median mark, session and day-type shares, the ordinal of the extreme with
quartiles.

## 7. Tests

**Primary candidates (4):** {chento-BTC, squeeze_bull} × {**I1_implied, J_wick**}.
**Decision controls (not in the family):** the same subpopulations × {I1_realised, J_nolevel}, under the accompanying
test's rung.
**Reported controls and kinds:** J_accept, J_shape, J_wick_1m, J_nolevel_1m, I1_implied_only, I2_day,
I2_day_realised, on every subpopulation where defined.
**Replication set (not in the family):** chento-ETH × {I1_implied, J_wick} with their controls, the identical frozen
rule on ETHUSDT; read under the rung of the chento-BTC test of the same kind (or squeeze_bull's when chento-BTC is
DESCRIPTIVE and squeeze_bull carries the label), entering the decision only through the stage verdict.
**Descriptive:** short_squeeze × every kind (median trade 65 minutes: the 60-minute gate rarely fits and a 15-minute
bar rarely lies inside).

A primary candidate is **in the family** only if it has **at least 30 included event trades** under its decision rung
and its subpopulation passes the coverage bar for that family (P7). P8 counts this before any continuation value is
computed. The family is then fixed: at most four Holm tests.

## 8. Decision per test

The spot-vs-perp stage's classification, verbatim (`spotperp_lib.classify`, reused with this stage's control map):

| Classification | Rule |
|---|---|
| **INFORMATIVE** | in the family; **Holm-adjusted p < 0.05** over the family; **both halves Δ̄ < 0**; **Δ̄ of the event < Δ̄ of its decision control on the shared pool**, the control having at least 10 included trades there; and **Δ̄ < 0 under R-vol and under R-session**, each with at least 10 included event trades |
| **INFORMATIVE, sign control unavailable** | every condition above except that the decision control has fewer than 10 included trades. Never counts toward PROMOTED |
| **INFORMATIVE, robustness unavailable** | every condition above except that R-vol or R-session has fewer than 10 included event trades. Never counts toward PROMOTED |
| **NO INFORMATION ≥ 0.10 R** | in the family, 95 % interval inside (−0.10, +0.10) R |
| **CONTRARY** | in the family, 95 % interval above 0 |
| **UNDETERMINED** | in the family, none of the above |
| **DESCRIPTIVE** | fewer than 30 included event trades under every rung, or below the coverage bar |

**Stage verdict.** A kind (I1 or J) is **PROMOTED** only if (a) at least one family test on it carries the exact label
INFORMATIVE and (b) the chento-ETH replication line on the same kind agrees: at least 30 included ETH event trades
under the rung, `Δ̄ < 0`, both ETH halves `< 0`, and `Δ̄ < 0` on the **non-overlap subset** (ETH event trades whose
interval overlaps no chento-BTC event trade of the kind; at least 10 included, else "replication unavailable
(non-overlap subset)"). squeeze_bull's agreement is reported and never sufficient. Otherwise **NONE PROMOTED**, which
closes rows I and J of the brainstorm at this resolution (1-minute perpetual klines, daily DVOL, 15-minute bars).

**What a promotion permits:** a separate stage 2 pre-registration of exit arms on the promoted subpopulation(s) using
the event exactly as defined here, booking exits at the open of `b + 1`, with the `b`-close continuation value beside
it as the information bound; it must name the live source and lag of each input (DVOL daily is in `prod.db` with a
freshness contract; the 15-minute bars come from `btc_1m` / `eth_1m`). Stage 2 reuses these trades, so its paired
result is not independent confirmation; any production proposal would also need forward paper evidence and the
user's go-ahead.

**The exit-arm overlay, reported and never decided.** Because both rows of the brainstorm were phrased as exits and
the Paladin prior is an exit-arm number, the outcome run also reports, per subpopulation, for I1_implied and J_wick:
the paired per-trade difference in R between "exit at the close of the first event minute, otherwise the shipped
exit" and the shipped exit (price only, every trade of the subpopulation, non-event trades contributing zero), its
block-bootstrap interval, the number of event trades whose shipped exit was a stop (Paladin's "stop-dodge" count) and
whose shipped exit was the target (winners cut). It is reported because an arm mixes information with the mechanical
effect of leaving early; it decides nothing, and a positive overlay with a null `Δ̄` is read as the drift of the
matched placebo, not as an exit.

## 9. Preconditions (before F0; any failure stops the stage)

| # | Check |
|---|---|
| P1 | **Inputs and hashes** (as amended by A1). ORB panels match their meta (logical and file sha256); chento features, the short_squeeze replay, the book caches and the stage 1 walks match stage 1's frozen hashes; the squeeze_bull ledger is git-clean at HEAD with its hash, blob and last commit recorded; the imported libraries (`micro_lib.py`, `micro_run.py`, `spotperp_lib.py`) are git-clean at HEAD with their hashes and blobs recorded — identity with their earlier freezes is **not** required, because those freezes hashed working-tree bytes that no committed version reproduces (`micro_lib.py` equals stage 1's frozen hash once CRLF line endings are normalised; `spotperp_lib.py` differs from its freeze at every commit that contains it, an unrecorded pre-commit difference); `results/microstructure/trades.csv.gz` hashed; the DVOL export written and hashed |
| P2 | **Population identity** (as amended by A1). chento 392 = stage 1's A0 set, split chento-BTC 208 and chento-ETH 184; 71 distinct short_squeeze triggers in London / NY hours; squeeze_bull 122 from the corrected ledger with its symmetric difference to stage 1's set listed; I-eligible counts per subpopulation recorded |
| P3 | **Walker identity** (as amended by A1). For every trade stage 1 also walked (584 of 585), `i0`, `x`, `kind`, `exit_price`, `x_notime`, `kind_notime`, `exit_price_notime`, `level`, `level_valid` equal stage 1's saved row (exit price ≤ 1e-9 relative; the rest exact); the trade the re-cut added is listed, not compared |
| P4 | **DVOL facts.** Per asset: rows, first and last day, days missing inside the span (reported; missing days give no event, rule 3), non-positive or non-finite closes (**must be 0**), the partial current day dropped; the export's sha256 |
| P5 | **Fixtures pass** (`tests/test_rangewick_events.py`; one twin per gate, values asserted): `IMP` uses the prior day's close (the last minute of a day and the first of the next read different rows), and is missing before 2022-09-08 and on the day after a missing row; `RV` from 31 closes, missing with one absent; I1 at `mv = IMP` (event) and just below (none), short mirror, the realised control, `I1_implied_only`, `I2` from the day's open; the gate at elapsed 59 (none) and 60 (event); 15-minute bars from partial minutes, a missing bar, the entry bar excluded, a bar crossing `x` excluded; J at shadow exactly 0.4 of range (event) and just below (none), arming at 0.3 R (event) and just below (none), a level within TOL (hit), just outside (no hit → `J_nolevel`), traded through and closed back (hit), through and held (`J_accept` only when no rejection shape), the short mirror, the grid step by entry magnitude, candidate levels at or beyond entry only, the target and the 24 h extreme as levels, the 1-minute arm; first-event selection; the placebo: rung 1 admits stall 59 and excludes 60, rung 2 requires `X`, S1 admits neither condition; era 364 in / 366 out; other direction, overlapping interval, other bin, fewer than 3 controls excluded; a control with a prior event excluded from the level placebo and admitted to the shared pool; unknown status (a missing bar, a missing `IMP` at an earlier extreme) excludes a control; R-vol and R-session; the exit-arm overlay's paired difference on a two-trade population; the verdict refuses a squeeze_bull-only promotion, an ETH line below 30 and a subset below 10 |
| P6 | **Causality on the real series.** Per asset, 5 cuts at mid-trade of random trades (seed 42). At each cut minute `c`: perp minutes after `c` NaN in every field; DVOL rows for days at or after `D(c)` removed (the prior day's close stays, as it is known at `D(c)` 00:00); then `IMP`, `RV`, `dv`, the 15-minute bars, every mask, event and first event at or before `c` unchanged for every open trade |
| P7 | **Coverage.** Share of in-trade minutes at or after `i0 + 60` of I-eligible trades with `IMP` and `RV` finite, per subpopulation and asset-year; share of in-trade 15-minute bars present; below 0.90 the subpopulation's tests of that family are DESCRIPTIVE |
| P8 | **Counts, no outcomes.** Per candidate test, decision control, reported kind and replication line: eligible trades, event trades, included trades under rungs 1–3, S1, S2, R-vol, R-session and the shared pool; the decision rung and the family; median elapsed at the first event; the exit-minute channel counts; dropped-for-lack-of-controls and excluded-for-unknown-status; distinct controls per event and the share used by more than one event; the covariate-balance table; the overlap shares (ETH event trades overlapping chento-BTC event trades of the kind; squeeze_bull's overlapping chento-BTC's); base rates: the share of eligible trades that ever cross `IMP` and `RV`, the share of armed 15-minute bars with the rejection shape, and of those the share at a level, per asset-year; the power line MDE(80 %, one-sided 5 %) ≈ 2.49 × sd(CV at one random open minute per trade, seed 42) / √n per subpopulation. No continuation value at an event is computed |

## 10. What is reported (secondary, never decided)

Per test, control, reported kind and replication line: `Δ̄` with interval, halves and by year; mean `CV` at the event
and mean placebo; `Δ̄` under all three rungs, R-vol, R-session and S1–S4; the shared-pool `Δ̄` of the event and its
decision control with the bootstrap interval of their difference; the covariate-balance table; the exit-minute
channel; the exit-arm overlay of section 8; cuts of `Δ` by direction (chento), by elapsed tercile, by profit bin and
by session. Three statements the findings must make: the NO INFORMATION label is unreachable at these n (half-width
about 0.12 to 0.64 R), so a null reads UNDETERMINED; the intervals are optimistic because controls are shared; and of
the many reported lines about one in twenty is expected to exclude zero by chance.

## 11. Information already seen (disclosure)

Seen while scoping on 2026-09-19, before this document, none of it an outcome: the four population sizes and the
I-eligible counts from the population files' timestamps (chento-BTC 164 of 208 and chento-ETH 154 of 184 entries at
or after 2022-09-08; squeeze_bull 111 of 122 bull fires at or after 2022-09-07); the `deribit_dvol_daily` table's
shape (2,946 rows, BTC from 2022-09-07, ETH to the current day), its column names and three latest ETH rows; the
Paladin study's wick-exit result and code, from which the grid-spacing observation of section 5 was reasoned (not
computed on any trade); the spot-vs-perp stage's counts and results (its `events.csv.gz` was not opened); the
squeeze_bull top anatomy's state map and SJ-4250 case; the exit-policy findings of stages 1, 2, S1, A, F and B; the
median stop distance of the chento populations (BTC 2.1 %, ETH 2.9 % of entry) and squeeze_bull's fixed 2 %, from
which section 1's "one implied day is about +1.2 R on chento and near the target on squeeze_bull" was reasoned. No
event minute, no continuation value and no count of any kind on any trade has been computed.

## 12. Amendments

**A1 — 2026-09-19, after the first `checks` run and before F0; no continuation value at an event was computed.** The
first precondition run failed P1, P2, P3 and P6.

- *P1–P3.* The squeeze_bull ledger no longer matched stage 1's frozen hash: it was re-cut on the corrected
  open-interest table the night before (section 3, above). Three imported libraries no longer matched their earlier
  freezes for the reasons P1 now records (line endings; an unrecorded pre-commit difference in `spotperp_lib.py`).
  P1–P3 were re-scoped as written above: git-clean at HEAD with hashes recorded, the population difference listed,
  walker identity on the shared trades. Nothing about an event, a rule or a threshold changed.
- *P6.* The J family's per-minute input mask marked a minute by whether the 15-minute bar **containing** it had a
  present minute, which reads minutes after the cut inside the cut's own bar — a look-ahead in a control-eligibility
  mask, never in an event (events sit at bar ends and read only their own bar). Fixed before F0: a J status rests on
  the bars that have ended at or before the minute, so every minute's inputs count as present and a missing bar is
  caught at its end minute by the unknown-status rule. The fixture for the missing-bar exclusion is unchanged and
  passes. Missing-data rule 6 (the 10⁻⁹ boundary tolerance) was added before the first run, when the fixtures showed
  floating-point rounding deciding a boundary case.
- *Seen from that run, all counts and no outcome:* per kind and subpopulation the event counts and the included
  counts under every rung (chento-BTC I1 80 events, 29 included under rung 1, 25 under rung 2, 49 under rung 3, so
  the pre-registered ladder decides it under rung 3; chento-BTC J_wick 152 / 107 under rung 1; squeeze_bull J_wick
  70 / 51; squeeze_bull I1 15 events, 7 of them at the target minute, DESCRIPTIVE; chento-ETH I1 77 / 23 / 40 and
  J_wick 118 / 77; short_squeeze no I event and 14 J events), the R-vol and R-session counts (chento-BTC I1 R-session
  11, one above the floor), the shared-pool counts, the base rates (27 % of armed bars carry the rejection shape, 17–
  29 % of those at a level; half of the eligible chento trades cross one implied day, 14 % of squeeze_bull's) and the
  power line (MDE 0.54 R for chento-BTC I1, 0.39 R for J_wick, 0.26 R for squeeze_bull J_wick). No threshold, rung
  order, kind, control or population was changed on reading them; the family is what the ladder gives.

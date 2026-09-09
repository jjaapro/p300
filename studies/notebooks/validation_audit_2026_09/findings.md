STATUS: CONCLUDED AUDIT per pre-registration

*Track V4, 2026-09-08. Pre-registration: [README.md](README.md), written and
saved before any outcome number was computed. No KILL rules; nothing was
switched off, resized or recommended for disabling. Production code was read
and imported, never modified.*

---

## 1. The honest-Sharpe table

"Raw Sharpe" is the annualised daily Sharpe (rf = 0, 365 d/yr - the repo
convention). "Boot CI" is the 5-95 percentile interval; for backtest rows it is
the circular-block bootstrap on the daily series (block 20, 5,000 iterations),
for paper rows the iid bootstrap on the **per-trade** Sharpe, because a 3-to-108
row daily series cannot support a block bootstrap. "Haircut" is Harvey-Liu
**Holm**. Alpha is annualised, from a Newey-West(5) OLS on BTC daily returns.

### Part A - paper ledgers (N_TRIALS = 1)

| # | series | n | daily rows | raw Sharpe | boot 5-95 (per-trade SR) | DSR @ N=1 | haircut @ N=1 | alpha vs BTC (t) | verdict |
|---|---|---|---|---|---|---|---|---|---|
| A1 | `bot_adx_v1` | 0 | 0 | - | - | - | - | - | **too few observations to say** |
| A2 | `bot_carry_v1` | 0 | 0 | - | - | - | - | - | **too few observations to say** |
| A3 | `bot_chento_v3_v1` | 6 | 3 | +3.52 | -0.379 ... +4.628 | 0.710 | +3.52 | unevaluable (<10 overlap days) | **too few observations to say** |
| A4 | `bot_chento_v3_eth` | 0 | 0 | - | - | - | - | - | **too few observations to say** |
| A5 | `bot_short_squeeze_v1` | 0 | 0 | - | - | - | - | - | **too few observations to say** |
| A6 | `bot_r4_v1` *(unregistered)* | 0 | 0 | - | - | - | - | - | **too few observations to say** |
| A7 | legacy R4 paper (`JPLUS_R4_*`) | 13 | 38 | +2.42 | -0.356 ... +0.573 | 0.828 | +2.42 | +77.8 %/yr (t = 0.67) | **too few observations to say** |
| A7 | ...restricted to >= 2026-05-16 | **0** | 0 | - | - | - | - | - | **too few observations to say** |
| A8 | legacy variant, all sleeves *(context row, not a strategy)* | 27 | 108 | +2.13 | -0.119 ... +0.440 | **0.944** | +2.13 | +54.1 %/yr (t = 1.16) | **does not survive deflation** |
| A8 | ...restricted to >= 2026-05-16 | 9 | 67 | +2.03 | -0.703 ... +0.637 | 0.882 | +2.03 | +39.6 %/yr (t = 0.86) | **too few observations to say** |

At N = 1 the Holm adjustment factor is log(1) = 0, so the haircut Sharpe equals
the raw Sharpe by construction. That column carries no information in Part A;
it is shown only so the table is uniform.

### Part B - backtests

Chento rows are the **production-faithful** series: the overlay engine's BASE
replay (TIF 72 h) with the source pool's own 18 bp-scaled cost charged. See §3 -
the published numbers charge no cost at all.

| # | series | n | daily rows | raw Sharpe | block boot 5-95 | DSR @ cons. N | DSR @ aggr. N | haircut cons. / aggr. | alpha vs BTC (t) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| B1 | chento BTC, no tilt | 101 | 2032 | +1.024 | +0.399 ... +1.549 | 0.726 (N=40) | 0.550 (N=120) | +0.207 / **-0.655** | +12.5 R/yr (t = 2.20) | **does not survive deflation** |
| B1s | chento BTC, **SHIPPED** skip-after-loss | 39 | 2032 | +0.748 | +0.173 ... +1.170 | 0.461 | 0.284 | 0.000 / 0.000 | +6.7 R/yr (t = 1.79) | **does not survive deflation** |
| B2 | chento ETH, no tilt | 77 | 1995 | +0.912 | +0.258 ... +1.452 | 0.546 | 0.359 | -0.175 / 0.000 | +8.9 R/yr (t = 2.09) | **does not survive deflation** |
| B2s | chento ETH, **SHIPPED** half-after-loss | 77 | 1995 | +0.906 | +0.295 ... +1.400 | 0.518 | 0.324 | -0.203 / 0.000 | +6.8 R/yr (t = 2.08) | **does not survive deflation** |
| B3 | chento COMBINED, shipped per-asset tilt | 115 | 2012 | +1.135 | +0.607 ... +1.578 | **0.876** | **0.744** | +0.433 / +0.039 | +13.8 R/yr (t = 2.69) | **does not survive deflation** |
| B4 | ADX baseline | 35 | 3094 | +0.839 | +0.245 ... +1.420 | 0.624 (N=17) | 0.368 (N=40) | +0.397 / +0.189 | +30.7 %/yr (t = 1.66) | **does not survive deflation** |
| B5 | ADX **SHIPPED** Tier-2 | 28 | 3094 | +1.121 | +0.526 ... +1.686 | 0.732 | 0.496 | +0.808 / +0.692 | +34.3 %/yr (t = 2.47) | **does not survive deflation** |

> **Correction (2026-09-09, post-verification).** The five chento cells in the alpha
> column above were hand-typed into the generator and were low by a factor of ~2.5. They
> now read from `results/part_b_backtest_audit.csv:alpha_alpha_annual_pct`, the same
> Newey-West(5) figure the column header describes. The old values were +5.0 / +2.6 /
> +3.6 / +2.7 / +5.4 R/yr. No verdict changes (the alpha column feeds clause C4, which
> fired nowhere either way) and PORTFOLIO.md never quoted them. **Caveat now stated
> explicitly:** C4 has no power on the chento rows, because that daily series is 95%
> zeros and books each trade's R on its ENTRY date while the price action happens over
> the following 72 h, so a same-day regressor cannot see it (R2 = 1.8e-05 to 1.5e-03).
> A direction-signed, holding-window-aligned regression gives BTC no-tilt R2 = 0.336,
> t = 2.28. Read C4 as unevaluable for B1-B3, not as a clean negative.


A negative haircut Sharpe (B1 at N=120, B2/B2s at N=40) means the corrected
p-value passed 0.5 - read it as **zero**, not as a negative edge.

Chento alpha is in **R per year**, not percent - the series' unit is R.

**Not one row in this study reaches DSR >= 0.95.** The best number in the whole
audit is chento COMBINED at the conservative trial count: **0.876**.

---

## 2. Clause table - every pre-registered clause, its number, whether it fired

| clause | rule | measured | fired? |
|---|---|---|---|
| **C0** | any paper series with >= 20 closed trades? | max = **27** (A8) | **did not fire** - but see note below |
| **C1** | n < 20 or daily rows < 60 | A1-A7 (n <= 13), A7-post (0), A8-post (9) | **fired on 9 of 10 Part-A rows**; fired on **0** Part-B rows |
| **C2** | DSR >= 0.95 **and** boot p05 > 0 | max DSR = 0.944 (A8, N=1) / 0.876 (B3, N=40) | **fired nowhere** |
| **C3** | not C1 and not C2 | A8; B1, B1s, B2, B2s, B3, B4, B5 | **fired on 8 rows** |
| **C4** | abs(t_alpha) < 2.0 **and** R2 >= 0.20 | max R2 = **0.0146** (B5) | **fired nowhere** |
| **C5** | Holm haircut Sharpe > 0 | at conservative N: A3, A7, A8, A8-post (trivially, N=1), B1, B3, B4, B5. At aggressive N: B3 (+0.039), B4 (+0.189), B5 (+0.692) only | fired as listed |
| **C6** | required IC > 0.7 | max = **0.613** (B5, "elevated") | **fired nowhere** |
| **C7** | daily rows >= 200 and CPCV decay < 0.5 | B1s = **0.479** | **fired on B1s only**; Part A all `insufficient` (max 108 rows) |
| **C8** | partial-family PBO > 0.5 | BTC **0.139**, ETH **0.119** | **fired nowhere** |

**Note on C0.** C0 as written asks about "any paper series", and A8 - the legacy
variant's 27 trades across six different sleeves - clears the bar. But A8 is a
mixed-sleeve aggregate that this study included as a *context row*, explicitly
"not a strategy" (README §1). **No single-strategy paper series has more than 13
closed trades.** The clause did not fire on a technicality; the sentence it was
written to test is still true: p300's forward paper record cannot support a
statistical claim about any individual sleeve as of 2026-09-08.

---

## 3. Two things the audit found that were not on the list

### 3.1 The chento headline expectancy is a ZERO-COST number

While reconciling Part B I could not match the study's published mean R from the
file it reads. The two disagree by a factor of 3.5, and the reason is a genuine
inconsistency inside the overlay study - not an error in this audit:

| where | TIF | transaction cost | BTC mean R (OKX-aligned, no tilt) |
|---|---|---|---|
| `results_backonly/trades_BTC.csv` column `r_outcome` (written by `chento_journal/validation_C5_smc_features.replay_one`) | `TIF_BARS = 4*24` = **24 h** | **18 bp charged**, `cost_R = 0.0018 * entry/risk` | **+0.225 R** |
| `overlay_study/run_overlays.py::replay(..., None)` - the engine whose numbers the study publishes | `TIF_H` = **72 h** | **none at all** | **+0.800 R** |
| the same 72 h replay with the source pool's own 18 bp model charged | 72 h | 18 bp | **+0.685 R** |

`audit_chento_cost_check.py` reproduces the published figure exactly -
**+0.8001 vs +0.8001, difference +0.0000** - then charges the cost. Numbers:

* BTC: +0.800 R -> **+0.685 R** (mean cost 0.115 R/trade, median 0.096 R, max 0.401 R)
* ETH: +0.712 R -> **+0.622 R** (mean cost 0.090 R/trade)

So `overlay_study/findings.md`'s "Production expectancy to underwrite any
go-decision: **~+0.8R/trade**" and `docs/calibration/chento_triple_v3.md`'s ETH
"~+0.7R region" are pre-cost. Post-cost they are **~+0.69 R (BTC)** and
**~+0.62 R (ETH)** - a 14 % reduction, not a reversal. Every relative
conclusion in the overlay study (tilt ordering, multi-asset diversification,
wick-exit rejection) is unaffected: all variants share the same cost.

The audit's Part-B chento rows use the **cost-charged** series throughout. The
deflation is insensitive to which one you pick - no version clears 0.95:

| series | mean R | SR/trade | DSR @ 40 | DSR @ 120 |
|---|---|---|---|---|
| BTC skip, r72 no cost (published) | +1.000 | +0.359 | 0.538 | 0.348 |
| BTC skip, r72 **with cost** (audit primary) | +0.963 | +0.342 | 0.461 | 0.284 |
| BTC skip, r24 cost (the CSV column) | +0.443 | +0.232 | 0.201 | 0.092 |
| ETH half, r72 no cost (published) | +0.539 | +0.287 | 0.654 | 0.452 |
| ETH half, r72 **with cost** | +0.477 | +0.255 | 0.518 | 0.324 |
| ETH half, r24 cost | +0.142 | +0.117 | 0.091 | 0.036 |

### 3.2 ADX's "Sharpe 2.09" is a t-statistic, and its edge over holding BTC is not established

`adx_study/harness.py::_metrics` computes
`sharpe = mean(rets) / pstdev(rets) * sqrt(len(rets))`. That is a **per-trade
t-statistic**, not an annualised Sharpe - there is no time in it. The
`findings.md` line "Sharpe 2.09" and the calibration doc are quoting that field.
The annualised daily Sharpe of the same book is **+0.84 (baseline)** and
**+1.12 (Tier-2)**.

The audit reproduces the study's published table **exactly** when run to the
study's own end date (2026-06-25), which validates the read-only candle loader:

| | published | reproduced |
|---|---|---|
| baseline | n=34, +2769 %, -27.3 %, MAR 1.78 | **n=34, +2769 %, -27.3 %, MAR 1.78** |
| Tier-2 | n=27, +2483 %, -15.1 %, MAR 3.09 | **n=27, +2483 %, -15.1 %, MAR 3.09** |

*(The audit rows in §1 run to 2026-09-07 instead - baseline n=35, MAR 1.69;
Tier-2 n=28, MAR 2.95. The difference is 2.5 months of extra data, not a
discrepancy.)*

The pre-registered beta test (C4) says ADX is **not** explained by beta: R2 =
0.015, t_alpha = 2.47. **That test is weak here and I do not want it read as a
clean result.** A strategy that is flat 64 % of days and short 15 % of them can
have ~zero unconditional daily beta while its whole P&L is still "long BTC in a
bull market". So I added the comparison that actually decides it:

| | baseline | Tier-2 |
|---|---|---|
| time in market | 47.8 % of 3094 days | **36.2 %** (long 21.4 %, short 14.9 %) |
| strategy daily Sharpe | +0.839 | **+1.121** |
| BTC buy-and-hold, identical window | +0.730 | +0.730 |
| buy-and-hold DSR @ N=1 | **0.982** | 0.982 |
| vol-matched excess Sharpe | +0.087 | +0.296 |
| **SR(strategy) - SR(buy-and-hold)** | +0.097 | **+0.375** |
| its block-bootstrap 5-95 | -0.658 ... +0.896 | **-0.400 ... +1.178** |
| P(difference > 0) | 0.573 | **0.781** |

**Simply holding BTC over 2018-2026 is itself statistically significant
(DSR 0.982 at N=1) and it was never subjected to a variant search.** ADX Tier-2
beats it by +0.375 Sharpe, but the interval on that difference comfortably
contains zero: on 28 trades in 8.5 years you cannot establish that this sleeve
beats levered buy-and-hold. That is the substance of "explained by beta" for a
long-biased trend follower, even though the pre-registered R2-based clause did
not fire.

One number in the exposure table is a **construction identity and must not be
read as a finding**: on long-only days the regression gives beta = 0.995,
R2 = 0.9998. The mark-to-market series is *defined* as direction x BTC daily
return, so on long days it must equal BTC minus cost. It confirms the series is
built correctly and it confirms that ADX has no security-selection alpha -
100 % of its claim is timing - but it is not evidence of anything else.

---

## 4. Supporting results

### Flat-max

| axis | chosen | peak | verdict |
|---|---|---|---|
| chento BTC post-loss size multiplier {0, 0.5, 1} | 0.0 (skip) | 0.0 | **OK** |
| chento ETH post-loss size multiplier | 0.5 (half) | 0.0 | **OK** |
| chento BTC OKX delta-z gate threshold {-0.5 ... +1.0} | 0.0 | +0.25 | **OK** (surface 2.7 / 4.2 / **4.1** / 5.0 / 2.3 / 3.2) |
| chento ETH OKX delta-z gate threshold | 0.0 | +0.5 | **SHARP_PEAK** (surface 1.4 / 1.8 / **1.8** / 3.6 / 4.7 / -0.5) |
| ADX ATR-trail multiplier {2.5 ... 5.0} | 4.0 | 5.0 | **OK** (surface 0.80 / 1.59 / 3.31 / **2.95** / 3.40 / 3.55) |

The shipped value is never the peak and never a sharp peak *at the shipped
setting* - the ETH SHARP_PEAK verdict describes the surface's shape (a spike at
z = +0.5 that the shipped z = 0 does not sit on), so the shipped choice is
conservative rather than over-fitted. The ADX ATR surface rises monotonically
above 3.5, which is the benign shape.

### CPCV (event-purged, 10 groups, k=2, embargo 5 d) - run only where daily rows >= 200

| series | rows | decay ratio |
|---|---|---|
| B1 chento BTC no tilt | 2032 | 0.860 |
| B1s chento BTC shipped | 2032 | **0.479** <- C7 fires |
| B2 chento ETH no tilt | 1995 | 0.832 |
| B2s chento ETH shipped | 1995 | 0.838 |
| B3 chento combined | 2012 | 0.945 |
| B4 ADX baseline | 3094 | 0.994 |
| B5 ADX Tier-2 | 3094 | 1.011 |
| **every Part-A row** | <= 108 | **insufficient** |

(Full train/test means are in `results/part_b_backtest_audit.csv`.)

The one C7 hit is the shipped BTC tilt: skipping after every loss leaves 39 of
101 trades, and that thinned series loses more than half its Sharpe out of fold.
The un-tilted pool does not (0.860). This is consistent with §3.1's DSR table -
the tilt improves MAR but costs statistical confidence, because its whole
mechanism is discarding sample.

### Fundamental Law (required IC at realised breadth)

Every row lands in the "plausible" or "elevated" band; none is suspicious or
impossible. Highest is ADX Tier-2 at **IC 0.613** ("elevated") on 3.3 bets/year
- unsurprising, since a 3-trades-a-year strategy needs a large per-bet edge to
justify any Sharpe at all. Chento rows sit at IC 0.24-0.28 on 7-21 bets/year
("plausible"). Nothing here contradicts the reported Sharpes.

### PBO via CSCV - **PARTIAL**

Full 40-variant PBO is **skipped**, as pre-registered: `overlay_summary.csv`
holds aggregate metrics only, the 5 wick-exit variants need a bar-by-bar replay
and the H-tag needs the events table, and re-running either would be the
parameter sweep this study forbids. The 4-variant tilt family is pure
post-processing of the on-disk outcome sequence, so it was run and labelled
partial: **BTC PBO 0.139, ETH PBO 0.119** over 252 CSCV combinations. With only
4 columns, CSCV is weak; treat this as "no evidence of tilt-axis overfitting",
not as the study's PBO. ADX PBO is **skipped entirely** - `experiments.py`
prints to stdout and saves no per-variant series.

---

## 5. Deviations from the pre-registration

Declared in advance (README §4): six bot variants but five registered; minimal
read-only loader; mean R unavailable in Part A; flat-max n/a in Part A; ADX
funding veto not reproduced; full PBO skipped. All held. **Original clauses are
left visible throughout.**

Discovered while running, and recorded here rather than by editing the
pre-registration:

1. **Chento primary series changed from the CSV column to a cost-charged 72 h
   replay.** README §2.5 said the CSV `r_outcome` "already carries the study's
   cost model" and that no further cost would be applied. That was correct
   about the CSV but wrong about which series the study publishes - they are
   different series (§3.1). Reporting the CSV column alone would have audited a
   24 h-TIF pool nobody ships. All three variants are reported; the conclusion
   is identical for all three.
2. **ADX daily series is mark-to-market, not exit-date-attributed.** README
   §2.2 specified an exit-date-attributed daily series (Part A's convention).
   For a strategy holding for months, that gives 28 non-zero days in 3094 and
   cannot support a beta regression at all. Both are computed; the MTM series is
   primary and the exit-attributed Sharpe is reported alongside (+0.673
   baseline, +0.720 Tier-2, vs +0.839 / +1.121 MTM).
3. **Added: ADX buy-and-hold comparison and vol-matched excess** (§3.2). Not in
   the pre-registration. It adds no clause and changes no verdict; it exists
   because the pre-registered R2-based beta clause is too weak for a
   long-biased timing strategy. My first version of this statistic - raw excess
   over buy-and-hold - was wrong (it compares a 36 %-invested book with a
   100 %-invested one and reported -0.021); the vol-matched version and the
   paired bootstrap on the Sharpe difference replace it. Both are in the JSON.
4. **C5 was evaluated at the conservative N by the code**, not at the aggressive
   N as README §5 says. Both are reported in the clause table and in the CSV
   (`hc_cons_holm_sharpe`, `hc_aggr_holm_sharpe`); the automatic `flags` column
   reflects the conservative N.
5. **Flat-max axes.** README §2.6 named the tilt axis for chento. `flat_max_1d`
   requires numeric parameter values, so the categorical tilt policies were
   mapped to their post-loss size multiplier {0, 0.5, 1}, and the OKX-z gate
   threshold was added as a second, more informative axis (also pure
   re-filtering of on-disk trades).

---

## 6. What the numbers mean

**p300's paper record is not yet evidence.** Five bot variants are registered,
one (`bot_r4_v1`) is declared in `bots/r4/config.py` and has never registered
because the operator has not started the runner. Between them the six bots have
produced **six closed trades**, all from `bot_chento_v3_v1` across three days in
August. The 13-trade legacy R4 record - the only R4 paper record that exists -
lies **entirely** inside the window the repo's own memory marks as not fully
trustworthy: filtering to `>= 2026-05-16` leaves **zero** R4 paper trades. Every
Part-A bootstrap interval spans zero. Nothing here should be quoted as forward
validation of anything, in either direction: these numbers are equally
consistent with the sleeves working and with them not working.

**The backtests do not survive deflation at any documented trial count.** This
is the finding I did not expect (§7). It is not the same as saying the
strategies are dead:

* DSR is a *significance* statement, not a sign statement. Chento COMBINED at
  DSR 0.876 means "after penalising for 40 variants tried, there is a ~12 %
  chance a strategy this good arises from selection alone" - that is not
  nothing, it is just short of the 95 % bar the pre-registration set.
* Every chento bootstrap lower bound is **positive** (+0.17 to +0.61 block-daily,
  +0.09 to +0.16 per-trade), and every alpha t-statistic on the un-tilted pools
  clears 2. The point estimates are real; the sample is small relative to the
  search that produced them.
* The binding constraint is **breadth, not edge**. Chento gets 7-21 trades a
  year, ADX 3.3. `expected_max_sharpe(40, 1/100) ~ 0.21` per-trade - chento
  BTC's observed per-trade Sharpe is 0.27-0.34. The strategy has to clear a bar
  set by how many variants were tried, on a sample set by how rarely it fires.
  More years, or more assets, move this; more tuning does not.

**Multi-asset is the one lever that visibly helps the statistics.** COMBINED
(0.876 / 0.744) beats both legs individually at both trial counts, and it is the
only chento row whose aggressive-N haircut Sharpe stays positive. That
independently corroborates the overlay study's central claim from a direction it
did not test.

**The shipped post-loss tilt costs statistical confidence.** BTC skip-after-loss
raises MAR but drops DSR from 0.726 to 0.461 and is the only row where CPCV
decay fires (0.479). It is a drawdown-management choice, not an edge
improvement, and it should be described that way.

---

## 7. Scoring my own priors (README §6)

| prior | outcome |
|---|---|
| "C1 will fire on every Part-A row; C0 answers NO" | **Half right.** C1 fired on 9 of 10; C0 did not fire because the mixed-sleeve context row A8 has 27 trades. No single-strategy series exceeds 13. |
| "A7's split at 2026-05-16 will be the most damning number in Part A" | **Right, and worse than expected** - I guessed 4-6 trustworthy trades remain. **Zero** remain. |
| "Part A alpha R2 low from sparsity, not neutrality; C4 will not fire" | **Right** (R2 <= 0.0007 on Part A) and the sparsity reading holds. |
| "chento DSR survives at N=40, marginal at N=120 (~0.90-0.97)" | **Wrong.** 0.726 at N=40, 0.550 at N=120 for BTC; best row 0.876 / 0.744. I was roughly 0.2 too optimistic. |
| "Harvey-Liu Holm at N=120 cuts the Sharpe 40-60 %" | **Too optimistic for chento** (BTC 1.024 -> -0.655, i.e. 100 %), **right for ADX** (1.121 -> 0.692, a 38 % cut). |
| "COMBINED will be the strongest chento row" | **Right** - best DSR at both trial counts. |
| "harness 'Sharpe 2.09' is a per-trade t-statistic, not an annualised Sharpe" | **Right** (§3.2). |
| "ADX is the row where C4 fires - 50/50 between beta and t ~ 2.2" | **Wrong on the pre-registered test** (R2 0.015, t 2.47). But the buy-and-hold comparison I added delivers the prior's substance: the Sharpe edge over holding BTC is +0.375 with a CI of [-0.400, +1.178]. |
| "chento tilt flat-max FLAT-to-OK; ADX ATR OK not SHARP_PEAK" | **Right** on both. |
| "partial PBO ~ 0.2-0.5, roughly uninformative" | **Close** - 0.12-0.14, and still uninformative at 4 columns. |
| **"The backtests are the evidence; the paper ledgers are not evidence of anything."** | **Half wrong, and this is the headline.** The paper half is right. The backtest half is not: at the trial counts these studies actually documented, **neither backtest clears the 95 % deflation bar either.** |

---

## 8. What could still be wrong

* **The aggressive trial counts are my construction, not a record.** Neither
  study wrote down how many configurations were tried. Chento N=120 sums five
  documented searches (README §3); ADX N=40 adds an undocumented veto sweep to
  17 on-disk variants. Both could be low - undocumented exploratory runs leave
  no trace, and the true N is almost certainly larger than what is on disk,
  which makes the DSR figures **optimistic**, not pessimistic. The conservative
  counts are the defensible floor.
* **Chento's underlying pool still carries the intersect lookahead** flagged in
  `memory/project_chento_v3_lookahead_unrecoverable.md`. The backward-only
  regeneration removes the +/-24 h intersect bias, which is why this audit uses
  `results_backonly/`, but the memory's "production ceiling 50-70 % of research
  R" caveat is a separate haircut this study does not apply on top.
* **Chento R is booked on the entry date** because the CSV carries no exit
  timestamp. TIF is 72 h, so the daily series is shifted by up to 3 days. That
  affects the CPCV fold boundaries and the daily-Sharpe autocorrelation
  slightly; it cannot move a DSR from 0.88 to 0.95.
* **ADX charges 10 bp round-trip and no funding** (harness `COST_BP_RT`), while
  the live sleeve charges 10 bp fee + 5 bp slippage + funding. The audited ADX
  Sharpe is therefore optimistic by ~5 bp/trade plus the funding stream - small
  at 3 trades/year, but in the wrong direction. The ADX study's own finding that
  funding *rescues* the counter-trend shorts means the sign of the funding term
  is not obvious; it is not modelled here either way.
* **The ADX buy-and-hold comparison uses additive daily returns**, so it is a
  risk-adjusted comparison, not a wealth comparison. ADX Tier-2's compounded
  return (+2353 %) far exceeds buy-and-hold's over the same window; the claim
  being tested is only whether its *Sharpe* advantage is distinguishable from
  zero. It is not, at n=28.
* **A8 is not a strategy.** It aggregates six sleeves under one variant, so its
  DSR of 0.944 describes a portfolio that no longer exists in this architecture
  (the bots replaced it in July 2026). It is a context row and should not be
  quoted as a result.
* **Part A capital is the registered $10,000 per variant.** Returns as "% of
  capital" therefore understate per-trade risk-taking; `pnl_pct` (% of notional)
  is in the CSV for anyone who wants the other view.
* **CPCV could not be run on any paper series** (max 108 daily rows against a
  200-row floor). That is a data limitation, not a result.

---

## 9. Recommendation

None, by design - this is an audit and the pre-registration forbids KILL rules.
Three things are worth the user's attention, all documentation-level:

1. `overlay_study/findings.md` and `docs/calibration/chento_triple_v3.md` quote
   a **pre-cost** expectancy. The post-cost figures are +0.69 R (BTC) and
   +0.62 R (ETH). Worth correcting in those files by whoever owns them.
2. `adx_study/findings.md`'s "Sharpe 2.09" is a per-trade t-statistic. The
   annualised daily Sharpe is 0.84 (baseline) / 1.12 (Tier-2).
3. `PORTFOLIO.md` §9 caveat 8 has been updated to point here (Part D of this
   task). Caveat 4 has been corrected - `macro_daily` and `paxg_spot_1h` both
   exist since 2026-09-06, and the gold/anchor question was studied and killed
   on 2026-09-07.

---

## 10. Files

| file | what it is |
|---|---|
| `README.md` | pre-registration, saved before any outcome number |
| `audit_paper_track.py` | Part A - paper ledgers, read-only `prod.db` |
| `audit_backtests.py` | Part B - chento + ADX |
| `audit_chento_cost_check.py` | the §3.1 reconciliation; also writes the per-trade series Part B consumes |
| `audit_common.py` | shared loaders, Newey-West regression, the statistic bundle, clause logic |
| `build_notebook.py` | writes `validation_audit.ipynb` (run with `C:/Python/Python313/python.exe`) |
| `results/part_a_paper_audit.csv` | every Part-A number in §1 |
| `results/part_a_series.json`, `results/part_a_c0.json` | full Part-A detail + the C0 record |
| `results/part_b_backtest_audit.csv` | every Part-B number in §1 |
| `results/part_b_series.json`, `results/part_b_extras.json` | flat-max, PBO, trial-count provenance, ADX reproduction + exposure decomposition |
| `results/chento_cost_reconciliation.json` | the §3.1 table |
| `results/chento_r72_{BTC,ETH}.csv` | per-trade R under all three cost/TIF conventions |

Re-run order: `audit_chento_cost_check.py` -> `audit_backtests.py`;
`audit_paper_track.py` is independent. All three are deterministic
(bootstrap seed 42) and read-only on `prod.db`.

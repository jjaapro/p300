# A1 — anchor / idle-capital allocator — findings (2026-09-07)

**STATUS: CONCLUDED KILL per pre-registration.** All three KILL clauses fire.
Overflow-to-anchor as specified (idle tactical capital → GOLD/EMA 40/60,
80/20 in drawdowns) *lowers* full-period Sharpe by 0.23 versus the fixed-weight
baseline and *deepens* max drawdown by 6.6 pp; with gold's return replaced by
cash the Sharpe uplift is −0.32. The only large improvement in the
decomposition is a **static** gold allocation with no overflow (+0.39 Sharpe,
−3.9 pp MDD), i.e. gold beta over 2020-26, not the overflow mechanism.
Routing idle capital to cash yield alone is worth +0.11 Sharpe at unchanged
drawdown. No production change.

Pre-registration: [README.md](README.md) (written before any computation).
Approximations: [results/panel_notes.md](results/panel_notes.md).
Notebook: [anchor_allocator.ipynb](anchor_allocator.ipynb).

## 1 Question and setup

Six single-strategy paper bots hold fixed capital that sits idle whenever a
sleeve has no position. The predecessor repo's P-300 design (Anchor 48 % /
Tactical 47 % / Reserve 5 %, "overflow to anchor") was replayed on p300's own
sleeves over 2020-01-01 → 2026-09-06 (2441 days) with the rules, caps,
costs and variants fixed in the README. Sleeve unit-return series come from
the production code paths (J+ decision loop, ADX harness in the T2
configuration, carry rule on `funding.daily_sums_pct`, chento backward-only
pool with the production filter stack); GOLD is PAXG (GC=F proxy before
2020-08-28); cash yields a flat 4 %/yr.

Idle capital is large in this panel: Σ active tactical caps averages 14.3 %
of NAV against a 47 % budget, so **32.7 % of NAV is idle on the average day**
(median 33 %, p10 23 %, p90 39 %, never zero — the proportional-scaling rule
for over-subscribed budgets was never exercised). Only 5.2 % of days have no
active sleeve at all, because the carry rule holds 91 % of days; the fleet's
"no open position on 46 % of days" figure refers to a different sleeve set,
so the relevant number here is the idle *amount*, not the no-position share.

## 2 Verdict — every clause with its measured number

Sharpe / MDD are the predecessor's `metrics()` (Sharpe = CAGR / annualised
daily vol, MDD on compounded NAV), full period, default rule (regime split,
gross cap 2×).

| clause (README) | threshold | measured | result |
|---|---|---|---|
| KILL if Sharpe uplift < +0.10 | +0.10 | ΔSharpe(overflow − baseline) = **−0.228** | FIRES |
| KILL if MDD worse by > 5 pp | +5 pp | ΔMDD = **+6.57 pp** (14.8 % → 21.4 %) | FIRES |
| KILL if the uplift disappears without gold | ΔSharpe_nogold < +0.10 | ΔSharpe(overflow_no_gold − baseline) = **−0.323** | FIRES |
| CONFIRM needs Sharpe uplift ≥ +0.20 | +0.20 | −0.228 | not met |
| CONFIRM needs MDD ≤ baseline + 3 pp | +3 pp | +6.57 pp | not met |

Verdict = KILL (any KILL clause suffices; all three fire).

## 3 Decomposition (full period 2020-01-01 → 2026-09-06)

| variant | Sharpe | CAGR | MDD | Calmar | 2022 | post-ETF Sharpe | mean idle % | mean anchor % | mean gross | ΔSharpe | ΔMDD pp |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `baseline` (EMA 18 + cash 30, idle earns 0) | 1.584 | 22.3 % | 14.8 % | 1.50 | +21.0 % | 1.726 | 32.7 | 48.0 | 0.34 | — | — |
| `baseline_gold` (gold rule, no overflow) | **1.970** | 26.3 % | **10.9 %** | 2.41 | +14.2 % | 2.376 | 32.7 | 48.0 | 0.55 | **+0.386** | **−3.9** |
| `overflow` (the hypothesis) | 1.356 | 29.9 % | 21.4 % | 1.40 | +23.9 % | 1.551 | 32.7 | 80.6 | 0.87 | −0.228 | +6.6 |
| `overflow_no_gold` (gold dollars → cash yield) | 1.261 | 25.8 % | 23.2 % | 1.11 | +34.6 % | 1.061 | 32.7 | 80.6 | 0.87 | −0.323 | +8.4 |
| `overflow_cash_only` (idle → cash yield) | 1.698 | 23.9 % | 14.5 % | 1.64 | +22.6 % | 1.858 | 32.7 | 80.7 | 0.34 | +0.114 | −0.3 |
| BTC buy-and-hold (reference) | 0.720 | 43.4 % | 76.6 % | 0.57 | −64.2 % | 0.480 | | | | | |

Sub-periods: 2022 Sharpe / MDD — baseline 1.59 / 5.6 %, baseline_gold
1.35 / 6.5 %, overflow 1.28 / 10.4 %, overflow_no_gold 1.98 / 8.1 %,
overflow_cash_only 1.71 / 5.5 %. Post-ETF (2024-01-11 →) CAGR / MDD —
baseline 20.8 % / 9.6 %, baseline_gold 28.6 % / 6.7 %, overflow 30.1 % /
15.6 %, overflow_no_gold 18.3 % / 17.9 %, overflow_cash_only 22.3 % / 8.8 %.

Per-year returns (%):

| | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 YTD |
|---|---|---|---|---|---|---|---|
| baseline | +43.9 | +22.6 | +21.0 | +7.6 | +30.3 | +21.7 | +5.5 |
| baseline_gold | +54.9 | +23.1 | +14.2 | +12.1 | +31.0 | +37.4 | +8.6 |
| overflow | +67.0 | +22.1 | +23.9 | +11.6 | +40.1 | +41.8 | +2.9 |
| overflow_no_gold | +52.6 | +28.7 | +34.6 | +9.6 | +32.3 | +19.2 | +1.4 |
| overflow_cash_only | +45.7 | +24.3 | +22.6 | +9.0 | +32.0 | +23.3 | +6.4 |
| BTC B&H | +302.0 | +59.8 | −64.2 | +155.6 | +121.3 | −6.3 | −8.3 |

## 4 Sensitivity (pre-listed N = 4, all `overflow` with a fixed split)

| cell | Sharpe | CAGR | MDD | Calmar | 2022 | post-ETF Sharpe | ΔSharpe | ΔMDD pp |
|---|---|---|---|---|---|---|---|---|
| split 40/60 fixed, gross 1.5× | 1.204 | 27.9 % | 26.9 % | 1.04 | +24.4 % | 1.612 | −0.380 | +12.0 |
| split 40/60 fixed, gross 2× | 1.204 | 27.9 % | 26.9 % | 1.04 | +24.4 % | 1.612 | −0.380 | +12.0 |
| split 80/20 fixed, gross 1.5× | 1.631 | 30.0 % | 16.4 % | 1.83 | +15.3 % | 2.115 | +0.047 | +1.6 |
| split 80/20 fixed, gross 2× | 1.631 | 30.0 % | 16.4 % | 1.83 | +15.3 % | 2.115 | +0.047 | +1.6 |

The gross cap never binds (max gross 1.11× NAV — the rules cannot reach 1.5×
with 1× unit series), so the two gross-cap cells are identical, as the README
anticipated. The split matters a lot: sending 80 % of the overflow to gold
instead of 40 % turns −0.38 into +0.05 — still short of the +0.10 KILL line.
Nothing in the sensitivity grid reverses the verdict.

## 5 Why it fails — attribution

**The overflow buys the wrong asset.** The rule routes 60 % of ~33 % idle NAV
into EMA 1W BTC (mean EMA weight 30.7 % vs 18 % in the baseline). As a unit
series EMA 1W BTC has Sharpe 0.34, 57 % annualised vol and a 70 % max
drawdown (2020 +45 %, 2021 −23 %, 2022 +86 %, 2023 −4 %, 2024 +75 %, 2025
−4 %, 2026 −1 %), and its daily returns are 0.90-correlated with the baseline
portfolio, which is already EMA-dominated. Adding more of it raises CAGR
(22 → 30 %) but raises vol more (14 → 22 %) and moves the worst drawdown from
the 2020 COVID window (−14.8 %) to the 2021 whipsaw (−21.4 %, 2021-04-15 →
2021-09-28, recovered 2022-06-16).

**Gold is the only diversifier in the anchor.** PAXG: unit Sharpe 0.90, 19 %
vol, correlation −0.01 with the baseline and −0.03 with EMA; +124 % over
2020-08 → 2026-09 (2024 +30 %, 2025 +65 %). `baseline_gold` (static rule,
mean gold weight 25 %) is the best line in the table: +0.39 Sharpe, MDD
−3.9 pp. Replacing gold's return with cash inside `overflow` costs 0.10
Sharpe (1.356 → 1.261) and, more tellingly, `overflow` sits 0.61 Sharpe *below*
`baseline_gold` — the overflow mechanism destroys value that the gold sleeve
alone creates.

**Cash yield on idle capital is a small clean gain.** `overflow_cash_only`
adds +1.6 %/yr of CAGR (cash contribution +16.8 pp vs +8.0 pp) at unchanged
vol and drawdown: +0.114 Sharpe, and the paired bootstrap interval for that
delta is [+0.105, +0.126]. This is simply the 4 %/yr assumption applied to
~33 % of NAV; it holds only while short rates are near that level.

**Turnover is a real but secondary drag.** The daily claim/release cycle
moves 7.0 % of NAV per day on average through GOLD/EMA (gold-regime flips on
165 days at ~25 % of NAV, tactical toggling ~5.7 % on ordinary days), costing
3.9 %/yr at 15 bp. Gross of turnover, `overflow` would score Sharpe 1.587
(≈ baseline 1.584) with MDD 19.9 % (+5.1 pp) — the KILL is structural, not a
cost artefact. `baseline_gold` pays 0.9 %/yr for its regime flips.

**Sleeve contributions** (Σ weight × unit return, pp of NAV): EMA +41.2 in
the baseline vs +74.3 in `overflow`; gold +32.4 in `baseline_gold` vs +49.3 in
`overflow`; tactical sleeves are identical across variants (ADX +28, R4 ETH
+20, R4 ETH v2 +11.5, eth_daily +10.7, R4 BTC v2 +7.7, carry +5.9, R4 BTC
+4.6, chento ETH +1.9, chento BTC +1.2). The net-BTC cap bound on 57 days in
the overflow variants (2 in the baseline) and freed 0.07 % of NAV on average
— immaterial.

**Diagnostics (not pre-registered, do not enter the verdict).** Paired
circular-block bootstrap (block 20 d, 2000 resamples) of ΔSharpe on the
predecessor's definition: overflow − baseline −0.23, 90 % interval
[−0.60, +0.14], P(Δ ≥ +0.10) = 7.6 %; overflow_no_gold − baseline −0.32
[−0.54, −0.09], P(Δ > 0) = 1.2 %; baseline_gold − baseline +0.39
[+0.05, +0.74], P(Δ ≥ +0.10) = 92 %; overflow − baseline_gold −0.61
[−0.86, −0.34]. A one-day claim lag (overflow uses yesterday's idle amount,
so a sleeve that fires today is funded before the anchor is sold) gives
overflow Sharpe 1.409 (−0.175) and MDD 22.3 % — the same picture.

## 6 What this means

- The P-300 overflow rule should not be ported. Idle capital deployed into a
  57 %-vol, 0.9-correlated trend sleeve is concentration, not diversification.
- If idle capital is to earn anything, the evidence points at low-vol,
  uncorrelated parking: cash yield (+0.11 Sharpe, mechanical) or gold
  (+0.39 for the static rule; +0.05 net / +0.27 gross of turnover for
  80/20 overflow). Gold's contribution is one 2020-26 path in which gold
  returned +124 %; a rule that leans on it should be pre-registered as a
  gold-anchor study with its own KILL clauses (a realistic PAXG spread, a
  lower rebalance frequency to kill the 3-4 %/yr turnover, and a test on a
  gold-flat sub-period such as 2021-22 where the sleeve returned −5 % / −1 %).
- The tactical budget is far too large for the sleeves' actual footprint:
  caps sum to 58 % but the active sum averaged 14 % and never exceeded 47 %.
  Any allocator redesign should start from measured occupancy, not from the
  bots' equal $10k capitals.

## 7 Limitations (honest list)

1. **chento 72 h spread.** Exit timestamps are not in the trade files, so
   each trade's fixed-notional return is spread evenly over [ts, ts + 72 h)
   by hours per UTC day; concurrent trades stack notional (max 3 BTC / 4 ETH,
   mean 1.1 / 1.3 on active days). The chento pool also carries the research
   intersect lookahead (production ceiling ~50-70 % of research R); it
   contributes only +3 pp in total, so the verdict does not depend on it.
2. **Gold proxy pre-2020-08-28.** 241 in-period days use GC=F daily closes
   (0 on non-trading days) before PAXG exists; PAXG trades weekends and its
   early liquidity was thin (14 PAXG days with |r| > 4 %, all matched by GC=F
   moves of similar sign except 2024-04-13, a Saturday, and 2026-06-11).
3. **Cash yield 4 %/yr flat** — too high for 2020-21, about right for
   2023-26; it flatters `baseline`, `overflow_cash_only` and every anchor cash
   slot equally, and is the whole `overflow_cash_only` effect.
4. **Static caps** mirror the bots' equal capitals and `PORTFOLIO.md`
   weights; they were not optimised and the over-subscription rule never
   triggered, so "proportional scaling" is untested here.
5. **Same-day claim** (the allocator knows today's activity set when setting
   today's weights) is optimistic for the overflow variants; the one-day-lag
   diagnostic changes ΔSharpe from −0.23 to −0.18 only.
6. **Daily-reset marking.** ADX trades are marked as direction × daily
   close-to-close with the harness exit price on the exit day; longs compound
   back to the harness return exactly (max deviation 4e-15), shorts differ
   from the fixed-notional harness convention (max 0.136 on the long 2022
   short) — the daily-reset convention is the right one for a daily
   allocator but the sleeve's standalone numbers differ from the harness.
7. **R4 unit series embed the 2.5×/1× inner leverage** (the sleeve as the
   bot trades it); fees are 15 bp × inner leverage per fire. ETH inputs were
   rebuilt full-period from `eth_1m` because the production loader bounds ETH
   to 3 years (parity with the loop on the overlap: max |diff| 3e-17).
8. **short_squeeze omitted** (no liftable trade-list function; active ≤ 4 %
   of days, 6 h holds). The ADX harness omits the funding-crowding LONG veto
   (one known trade). The J+ regime classifier has no LSR before 2021, as in
   production.
9. **Sharpe definition** is CAGR / vol with no risk-free rate (predecessor
   port). The 4 % cash assumption therefore adds directly to every variant's
   Sharpe through the cash slot; deltas between variants are what matter.
10. **Single history.** One 6.7-year path with one gold bull market and one
    BTC cycle; the bootstrap intervals above quantify sampling noise on this
    path only, not regime risk. The block bootstrap resamples days, so the
    allocator's path dependence (turnover, drawdown timing) is only
    approximately preserved.
11. **Rebalancing cost model** is 15 bp on |Δ weight| of GOLD/EMA per day
    with a daily reset to target weights; NAV-drift rebalancing and PAXG's
    real spread (wider than 15 bp on Binance in thin hours) are not modelled.

## 8 Files

- `README.md` — pre-registration (unchanged after the run).
- `build_panel.py` → `results/daily_panel.csv` (2441 × 22, no NaN),
  `results/panel_notes.md`.
- `simulator.py` — port of `trader/p300_simulator.py` (`allocate_day`,
  `simulate`, `metrics`, `per_year_returns`) with the panel registry,
  idle-capital metric, turnover cost, T−1 BTC 30d, direction-aware net-BTC cap.
- `run.py` → `results/summary.csv`, `results/per_year.csv`,
  `results/sleeve_contrib.csv`, `results/equity_<variant>.csv`,
  `results/verdict.json`, `results/bootstrap_delta.csv`.
- `build_notebook.py` (system Python + nbformat; `--execute` runs it on the
  venv kernel without installing a kernelspec) → `anchor_allocator.ipynb`
  (executed).

Run from the repo root: `venv\Scripts\python studies/notebooks/anchor_allocator_study/build_panel.py`
then `run.py`. prod.db read-only; nothing under `strategies/`, `bots/`,
`data/` or `botlib.py` touched.

---

## 8. Corrections after independent verification (2026-09-09)

An independent agent rebuilt the allocator from this study's README against
`results/daily_panel.csv` and reproduced the three KILL clauses exactly, so **the
KILL verdict stands**: the pre-registered overflow rule loses 0.23 Sharpe and adds
6.6 pp of drawdown. Four corrections apply to the *positive* findings, which are the
part a reader would act on.

### C1 — "+0.39 Sharpe from a static gold allocation" is 64 % not gold

`baseline_gold` does not only add gold. The pre-registered anchor-compression rule
(`simulator.py`) also cuts EMA 1W BTC from an 18 % target to a 13.78 % mean and cash
from 30 % to 9.09 %. EMA 1W BTC is a 57 %-vol sleeve correlated 0.903 with the book,
so removing it is doing most of the work:

| variant | Sharpe | ΔSharpe | ΔMDD |
|---|---|---|---|
| baseline | 1.5837 | — | — |
| baseline_gold (as reported) | 1.9702 | **+0.3865** | −3.93 pp |
| same weights, gold dollars earning the cash yield instead | 1.8320 | +0.2483 | −2.12 pp |
| gold added but EMA pinned at 18 % | 1.7457 | +0.1620 | −1.28 pp |
| no gold at all, EMA simply cut to 13.78 % | 1.7659 | +0.1822 | −2.95 pp |

**Attribution: +0.2483 weight compression + +0.1382 gold excess return.** Cutting EMA
1W BTC to 10 % with no gold whatsoever is worth +0.3848 on its own. The actionable
finding is therefore *"the book holds too much EMA 1W BTC"*, not *"add gold"* — and
that is a different, cheaper change to make.

### C2 — It is not a "static" allocation

The rule is BTC-30d-conditioned: it flips the gold weight between 15 % and 32.6 % of
NAV on 165 days and pays 0.93 %/yr of turnover for the flips. Against genuinely flat
gold weights the conditioning *subtracts*: flat 15 % gives +0.1446, flat 30 % +0.3048,
flat 40 % +0.4086, flat 55 % +0.5038, against the dynamic rule's +0.3865. Calling it
static invites implementing either the wrong thing or the right thing for the wrong
reason.

### C3 — No deflation was reported, and the claim does not survive it

The study offers only a paired circular-block bootstrap. Applying the repo's own
toolkit to the difference series `baseline_gold − baseline` (SR_ann 0.482, n = 2441):
DSR **0.8932** at n_trials = 1, **0.3539** at the study's own 11 variants, 0.2047 at
30. The gold claim does not clear the bar this repo applies everywhere else.

### C4 — The cash-yield uplift is an artefact of the flat 4 % assumption

Limitation 3 flags the flat 4 %/yr cash rate qualitatively but never prices it.
Substituting the realised year-by-year effective fed funds path (2020 0.36 % …
2023 5.02 % … 2026 3.80 %, mean 2.84 %/yr) drops the `overflow_cash_only` uplift from
**+0.1145 to +0.0802**, below this study's own +0.10 materiality line. The three KILL
clauses still fire under the realistic path.

### C5 — The overflow failure is destination-specific, not mechanism-specific

Extending the split continuum past the pre-registered N = 4 grid (post hoc, so it
changes no verdict): 40/60 gold/EMA gives ΔSharpe −0.3801, 60/40 −0.1652, 80/20
+0.0471, **90/10 +0.1416 with MDD −1.12 pp — no kill clause fires**, 100/0 +0.2211.
What fails is routing 60 % of idle NAV into a high-vol sleeve already correlated 0.90
with the book. Overflow *to an uncorrelated destination* is not falsified by this
study; it was simply not the rule that was pre-registered.

### Also corrected

`results/panel_notes.md` says the proportional-scaling rule was "exercised" on 0.0 %
of days, contradicting itself and `findings.md` §1, which correctly states it was
never exercised (the largest reachable cap sum is 0.46 against a 0.47 budget).

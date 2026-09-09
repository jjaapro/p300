# A1 — anchor / idle-capital allocator study — pre-registration

*Written 2026-09-06 BEFORE any study computation ran (only source-code reading
and data-coverage checks preceded this file). Nothing in this file changes
after `build_panel.py` / `run.py` have been executed; anything learned later
goes in `results/panel_notes.md` or `findings.md`.*

## Question

p300 runs six single-strategy paper bots with fixed capital each. Capital sits
idle whenever a sleeve has no position — over the last 90 days the fleet had
**no open position on 46 % of days**. The predecessor repo
(`C:\Source\Repos\trader\p300_simulator.py`) designed a three-bucket allocator
(Anchor persistent / Tactical claim-on-fire / Reserve cash) with an
"overflow to anchor" rule. This study quantifies, on p300's own sleeves,
whether overflow-to-anchor with **Anchor = EMA 1W BTC + GOLD (PAXG,
momentum-dynamic) + cash yield** beats the fixed-weight baseline.

This is a STUDY. Output is numbers + `findings.md`. No production change.

## Hypothesis (fixed)

Overflow-to-anchor raises full-period Sharpe by ≥ +0.20 versus the
fixed-weight baseline at max drawdown ≤ baseline + 3 pp.

## KILL rules (fixed)

KILL if:

- Sharpe uplift < +0.10, OR
- MDD worse by > 5 pp, OR
- the uplift disappears when GOLD is removed from the anchor (i.e. comes only
  from gold beta) — report an "anchor without gold" and an "overflow-only
  (anchor = cash)" decomposition.

## Fixed allocator rules (from the trader P-300 spec)

- Buckets: Anchor 48 % / Tactical 47 % / Reserve 5 % of NAV.
- GOLD weight 15 % of NAV when BTC 30d return > +5 % else 55 % (compress
  anchor proportionally to fit 48 %); EMA 1W 18 %; cash floor 8 %.
- Overflow = tactical budget − sum(active tactical caps), split 40/60
  GOLD/EMA when BTC 30d ≥ −10 % and 80/20 when BTC 30d < −10 %.
- When active caps exceed the budget, scale them proportionally.
- Max net long-BTC exposure 50 % NAV (scale BTC-long sleeves down).
- Gross ≤ 2× NAV.
- Sensitivity (pre-listed, N = 4 only): overflow split {40/60, 80/20 fixed}
  × gross cap {1.5×, 2×}. No other cells will be added after seeing results.

## Tactical caps (fixed, % of NAV)

| sleeve | cap |
|---|---|
| r4_btc | 6 |
| r4_eth | 6 |
| r4_btc_v2 | 4 |
| r4_eth_v2 | 4 |
| adx | 10 |
| carry | 8 |
| chento_btc | 8 |
| chento_eth | 6 |
| eth_daily | 6 |

Sum = 58 % > 47 %, so the proportional-scaling rule is exercised when many
sleeves are active at once. These caps mirror the bots' equal $10k capitals
and the sleeve weights in `PORTFOLIO.md` (S-003 ADX 0.15, S-078 Carry 0.08,
J+ regime weights); they are **not** an optimisation and will not be tuned.

## Baseline (fixed)

- **baseline**: the same tactical caps always reserved (idle tactical capital
  earns 0 %, as today); anchor = EMA 1W 18 % + cash 30 % (no gold); no
  overflow.
- **baseline + gold**: anchor with gold (dynamic 15/55 rule, compressed to
  48 %), no overflow — so the decomposition is clean.

## Costs (fixed)

- Sleeve return series are already net of their own trade costs (see
  "Sources" below — where a source is gross, the cost is added in the panel
  builder and documented in `results/panel_notes.md`).
- Rebalancing cost for weight changes = 15 bp on the absolute change in
  GOLD / EMA notional per day.

## Period (fixed)

2020-01-01 → latest full day (2026-09-05 at the time of writing). Also report
2022 alone (crisis) and post-ETF (2024-01-11 →).

## Decomposition variants (fixed set, run by `run.py`)

| variant | anchor composition | idle tactical capital goes to |
|---|---|---|
| `baseline` | EMA 18 % + cash 30 % | nothing (earns 0 %) |
| `baseline_gold` | GOLD dynamic + EMA 18 % + cash floor 8 %, compressed to 48 % | nothing (earns 0 %) |
| `overflow` (the hypothesis) | as `baseline_gold` | GOLD/EMA 40/60 (80/20 when BTC 30d < −10 %) |
| `overflow_no_gold` ("anchor without gold") | identical weights to `overflow`, but every GOLD dollar earns the cash yield instead of PAXG | GOLD share → cash yield, EMA share → EMA |
| `overflow_cash_only` ("overflow-only, anchor = cash") | as `baseline` (EMA 18 % + cash 30 %) | 100 % → cash yield |

Sensitivity cells (N = 4) are `overflow` with the split fixed at 40/60 or
80/20 (no regime switch) and the gross cap at 1.5× or 2×.

## Verdict procedure (mechanical, applied verbatim by `run.py`)

Let ΔSharpe = Sharpe(`overflow`) − Sharpe(`baseline`), ΔMDD = MDD(`overflow`)
− MDD(`baseline`) in percentage points (positive = worse), and ΔSharpe_nogold
= Sharpe(`overflow_no_gold`) − Sharpe(`baseline`), all on the full period.

1. **KILL** if ΔSharpe < +0.10, OR ΔMDD > +5 pp, OR ΔSharpe_nogold < +0.10
   (the uplift disappears when gold is removed).
2. **CONFIRMED** if not killed AND ΔSharpe ≥ +0.20 AND ΔMDD ≤ +3 pp.
3. **WEAK** otherwise (uplift in [+0.10, +0.20) or ΔMDD in (+3, +5] pp):
   not adopted, not killed — reported as such.

Sensitivity cells are reported but do not enter the verdict (the verdict is
on the pre-specified default rule: regime-dependent split, gross cap 2×).

## Interpretations fixed before running (points the spec leaves open)

1. **Sharpe / MDD / CAGR / Calmar** are the predecessor's `metrics()` ported
   verbatim: Sharpe = CAGR / annualised daily vol (365-day year, no risk-free
   subtraction), MDD on the compounded NAV of daily returns. The same
   function is applied to the sub-period slices.
2. **"Active" tactical sleeve** on a day = nonzero unit return that day (for
   chento / ADX / carry that is the held-position days), exactly as the
   predecessor's `allocate_day`. Same-day claim: the allocator knows which
   sleeves are active on day t when it sets day t's weights (R4 windows are
   calendar-known, EMA / carry / ADX / chento holds are known from the prior
   close; only the entry day of a new chento / ADX / carry trade is an
   intraday claim). This is optimistic for the overflow variants and is
   listed as a limitation.
3. **BTC 30d return** (gold rule, split rule, chento short-skip) is the
   compounded trailing 30-day spot return using data through **T−1**. The
   predecessor indexed it through T (a one-day look-ahead); that is fixed
   here.
4. **Anchor residual**: when GOLD is at 15 % the anchor sums to 41 % < 48 %;
   the 7 % residual is treated as cash at the cash yield (the 8 % is a
   *floor*). In `baseline` the 30 % cash is the whole non-EMA anchor.
5. **Reserve 5 %** earns 0 % in every variant (exchange margin buffer, as
   today). It cancels in every comparison.
6. **Cash yield** = 4 %/365 per day, flat, an assumption (T-bill-like
   2022-26 average; too high for 2020-21). Earned by anchor cash and, in the
   overflow variants, by overflow routed to cash. Never earned by idle
   tactical capital in the baselines.
7. **R4 unit series** are the J+ decision loop's `r4_*_pct`, which embed the
   sleeve's gate-dependent inner leverage (2.5× ungated / 1× gated) — that
   IS the sleeve as the bot trades it. The 15 bp per fire is charged on the
   traded notional, i.e. 15 bp × inner leverage; gross and net-BTC
   accounting use weight × inner leverage on fire days.
8. **Net-BTC cap** uses actual position direction per sleeve (EMA `ema_p`,
   ADX trade direction, chento net direction, R4 long, carry delta-neutral =
   0), not the predecessor's PnL-sign proxy.
9. **Gross cap**: gross = Σ |weight × notional multiplier| over invested
   sleeves (cash excluded). With 1× unit series and R4's 2.5× the gross of
   these rules cannot exceed ~1.3× NAV, so the cap is expected never to
   bind; the two gross-cap sensitivity cells will then be identical and
   reported as such.
10. **Turnover cost** is charged on |Δ weight| of GOLD and EMA between
    consecutive days (15 bp), separately from the EMA flip cost that lives in
    the EMA unit series; NAV-drift rebalancing is not modelled (daily reset
    to target weights).
11. **Sub-periods**: "2022 alone" = 2022-01-01 → 2022-12-31; "post-ETF" =
    2024-01-11 → end. Metrics are computed on the slice with the same
    function.
12. **short_squeeze**: included only if a self-contained trade-list function
    can be lifted from
    `studies/notebooks/short_squeeze_sessions/strategy_backtest.ipynb` in
    ≤ ~1 h; otherwise omitted and so stated in `panel_notes.md` (it is active
    on ≤ 4 % of days).
13. **chento trade lists** are the backward-only research pool
    (`overlay_study/results_backonly/trades_{BTC,ETH}.csv`) with the
    production filter stack applied as in `lsr_b5_study/score_variants.py`
    (OKX alignment; shorts skipped when BTC 30d > +10 %; BTC skip-after-loss,
    ETH half-after-loss). Return on notional per trade = r_outcome ×
    risk / entry, spread evenly over the days from `ts` to `ts + 72 h` (the
    sleeve's TIF) because exit timestamps are not in the file. Overlapping
    trades stack (each is one unit of notional).

## Priors (stated before running)

- Idle tactical capital averages roughly half the 47 % budget (fleet had no
  open position on 46 % of recent days), so the overflow variants carry
  ~20–25 % of NAV extra in EMA/GOLD on a typical day.
- GOLD 2020-08 → 2026 had a strong run (roughly +60–100 % with ~15 %
  annualised vol). Expected: `baseline_gold` alone shows a visible Sharpe
  uplift from gold beta, and a large share of the full `overflow` uplift is
  gold beta rather than the overflow mechanism. Prior that the
  gold-removed decomposition (`overflow_no_gold`) clears +0.10: ~40 %.
- EMA 1W BTC is a slow trend follower; adding it to idle days raises return
  but also vol and drawdown (2022 shorts help; 2021/2024 whipsaws hurt).
  Prior ΔMDD for `overflow` vs `baseline`: +2 to +6 pp, i.e. the MDD clause
  is genuinely at risk.
- `overflow_cash_only` should show a small, almost pure return uplift
  (≈ +1 %/yr on ~23 % idle) with slightly *higher* Sharpe and unchanged MDD.
- Overall prior: P(CONFIRMED) ≈ 25 %, P(WEAK) ≈ 30 %, P(KILL) ≈ 45 %, the
  most likely KILL path being the gold-decomposition clause or the MDD clause.
- The gross-cap sensitivity is expected to be a no-op; the 80/20-fixed split
  is expected to lower MDD and Sharpe both (more gold, less EMA).

## Sources (each verified in code before use; approximations logged in `results/panel_notes.md`)

- J+ family (`strategies.support.jplus_inputs._run_decision_loop()`): EMA
  position, regime mode, R4 gate, BTC daily return, R4 BTC windows. R4 windows
  are GROSS (`r4/math.py: COST_BP_RT = 0.0`), so 15 bp per fire is charged in
  the panel. ETH inputs in the loop are bounded to the last 3 years by
  `data/loaders.py`; the ETH daily and R4 ETH series are rebuilt full-period
  from `eth_1m` with the same aggregation and the same
  `r4.r4_eth_returns` / `r4.r4_eth_v2_returns` functions, with a parity
  assertion against the loop on the overlapping window.
- ADX: `studies/notebooks/adx_study/harness.run` in the production T2
  configuration (symmetric EMA150 filter, `adx_or_atr` exit, ATR×4) as
  `tests/test_adx_parity.py` calls it; trades marked daily on BTC daily
  closes with the sleeve's 10 bp RT cost at close. The daily marks must
  compound back to each trade's harness return (parity assertion).
- Chento: see interpretation 13; `r_outcome` is already net of 18 bp
  (`validation_C5_smc_features.replay_one`).
- Carry: sleeve rule from `strategies/sleeves/carry/{config,signal}.py` on
  `strategies.support.funding.daily_sums_pct("BTC", …)`; entry when the 7-day
  average daily funding > 0, exit after 3 consecutive negative days;
  0.20 % round trip split over entry and exit days.
- GOLD: `paxg_spot_1h` daily close-to-close from 2020-08-28; before that
  `macro_daily` symbol GOLD (GC=F) as a proxy, 0 return on non-trading days.
- btc_bh: BTC spot close-to-close from the J+ loop.

## Files

- `README.md` — this pre-registration.
- `build_panel.py` → `results/daily_panel.csv`, `results/panel_notes.md`.
- `simulator.py` — port of the predecessor's `allocate_day` / `simulate` /
  `metrics` / `per_year_returns` consuming the panel + an in-code registry.
- `run.py` → `results/summary.csv`, `results/equity_*.csv`, prints the verdict.
- `build_notebook.py` (system Python, nbformat) → `anchor_allocator.ipynb`.
- `findings.md` — opens with `STATUS: CONCLUDED <verdict> per pre-registration`.

Run from the repo root with `venv\Scripts\python`. prod.db is opened
read-only (`mode=ro`) by every query this study issues itself; the reused
library loaders open it with plain read-only SELECTs. Nothing under
`strategies/`, `bots/`, `data/`, or `botlib.py` is modified.

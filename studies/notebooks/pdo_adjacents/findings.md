STATUS: CONCLUDED KEEP -10% (question a) / CONCLUDED KILL (question b) per pre-registration

Study: `studies/notebooks/pdo_adjacents/`
Pre-registration: [README.md](README.md), written and saved before any outcome
number was computed.
Data: `data/databases/prod.db`, opened read-only. 2020-01-01 -> 2026-09-08
(58,625 hourly evaluation bars). Cost 18 bp round trip (10 bp reported
alongside where it changes nothing).

Nothing under `strategies/**` or `bots/**` was modified.

---

## 0. Parity gate (precondition for reporting anything)

The study's vectorised features were checked against the **live sleeve's own
functions** (`_load_today_open_and_pdo`, `_get_hourly_bar_for_today`,
`_btc_30d_return_pct`), driven under a frozen clock, requiring **exact float
equality** (`==`).

| Asset | Entry timestamps tested | Random timestamps | Total | Failures |
|---|---|---|---|---|
| BTC | 88 | 2,000 | 2,086 | **0** |
| ETH | 141 | 2,000 | 2,140 | **0** |

`results/parity.json` -> `"passed": true`. Fields compared: `pdo`,
`today_open`, `gap_pct`, bar-day string, hourly `low` / `high` / `close`,
`btc_30d_pct`, and the None/not-None state of each. The parity gate passed,
so the results below are reportable.

The study's frozen constants were also asserted equal to the live `config.py`
at run time (`GAP_THRESHOLD_PCT=2.0`, `TOUCH_TOL_PCT=0.10`,
`HOLD_BARS_BY_ASSET={'BTC':24,'ETH':4}`).

---

# QUESTION (a) - regime threshold -10% (live) vs -7% (predecessor)

## a.1 Clause-to-number table

| Clause | Requirement | Measured | Fired? |
|---|---|---|---|
| **A1** (OOS BTC) | `mean_bp(-7) - mean_bp(-10) >= +5.0` bp on OOS BTC | **+0.00 bp** (both configs: n=1, mean +26.86 bp) | **NO** |
| **A1** (OOS ETH) | same, OOS ETH | **+0.00 bp** (both configs: n=6, mean +14.47 bp) | **NO** |
| **A1 overall** | both assets >= +5 bp | 0.00 / 0.00 | **NO** |
| **A2** (IS BTC) | `mean_bp(-7) - mean_bp(-10) >= 0.0` bp in-sample BTC | **-2.01 bp** (-7: +33.40 bp n=84; -10: +35.41 bp n=87) | **NO** |
| **A2** (IS ETH) | same, in-sample ETH | **-4.00 bp** (-7: -5.30 bp n=123; -10: -1.30 bp n=132) | **NO** |
| **A2 overall** | both assets >= 0 bp | -2.01 / -4.00 | **NO** |
| **A-VERDICT** | adopt -7 only if A1 **and** A2 | neither fired | **KEEP -10%** |

**Recommendation: leave `REGIME_THRESHOLD_PCT = -10.0` in
`strategies/sleeves/timing_anomalies/internal/pdo/config.py` unchanged.**
This is a recommendation only; no production file was touched.

## a.2 Why A1 measured exactly zero - the OOS window carried no evidence

The two thresholds can only differ on setups whose BTC 30-day return sits in
the band **[-10%, -7%)**. Over the whole 2020-2026 sample there are **15**
such trades (BTC 3, ETH 12). In the OOS window 2026-04-01 -> 2026-09-08 there
are **zero**. Every OOS trade was taken in a comfortably non-bearish regime:

| Asset | Entry (UTC) | gap % | BTC 30d % | net bp @18bp |
|---|---|---|---|---|
| BTC | 2026-05-15 14:00 | 2.24 | +6.34 | +26.86 |
| ETH | 2026-04-18 12:00 | 3.02 | +8.70 | +27.39 |
| ETH | 2026-04-23 10:00 | 2.02 | +8.89 | -2.59 |
| ETH | 2026-04-27 06:00 | 2.15 | +17.25 | -22.26 |
| ETH | 2026-05-24 22:00 | 2.51 | -1.79 | +147.94 |
| ETH | 2026-08-03 07:00 | 2.19 | +0.20 | -34.44 |
| ETH | 2026-09-01 18:00 | 2.10 | +22.06 | -29.25 |

The minimum BTC 30-day return across all seven OOS entries is **-1.79%** -
nowhere near either threshold. Regime occupancy explains it: the OOS window
spent only **1.74%** of its hours in the [-10%, -7%) band, versus **5.64%**
in-sample (`results/qa_regime_occupancy.csv`), and a >=2% gap-up setup day is
anti-correlated with a mildly bearish 30-day tape anyway.

So A1 evaluates to a genuine, well-defined **0.00 bp** difference, not to an
error - but it is a **zero-information zero**. The pre-registration
anticipated exactly this ("if zero discriminating trades fall in the OOS
window ... the pre-registered default stands: keep -10%") and that clause is
what carried the verdict. **The OOS half of this question is underpowered and
the honest reading is "no OOS evidence either way".** The decision therefore
rests on A2, which *is* evaluable and *does* discriminate.

## a.3 The in-sample half - -7% is worse, on both assets, in both eras

`results/qa_summary.csv`. Mean per-trade net bp at 18 bp round trip:

| Period | BTC -10 | BTC -7 | delta | ETH -10 | ETH -7 | delta |
|---|---|---|---|---|---|---|
| OOS (>=2026-04-01) | +26.86 (n=1) | +26.86 (n=1) | **0.00** | +14.47 (n=6) | +14.47 (n=6) | **0.00** |
| IS (<2026-04-01) | +35.41 (n=87) | +33.40 (n=84) | **-2.01** | -1.30 (n=132) | -5.30 (n=123) | **-4.00** |
| pre-ETF | +30.71 (n=63) | +27.49 (n=61) | -3.23 | -7.33 (n=95) | -12.42 (n=88) | -5.09 |
| post-ETF | +46.91 (n=25) | +48.15 (n=24) | +1.24 | +14.22 (n=43) | +12.86 (n=41) | -1.36 |
| pooled | +35.31 (n=88) | +33.32 (n=85) | -1.99 | -0.62 (n=138) | -4.38 (n=129) | -3.77 |

-7% is worse on 7 of the 8 non-zero era/asset cells. The single cell where it
wins (BTC post-ETF, +1.24 bp on 24-25 trades) is far below the +5 bp bar and
is one trade's worth of noise.

Supporting statistics agree with the sign, all at `N_TRIALS=2`
(`results/qa_dsr.csv`, `results/qa_bootstrap.csv`):

| Asset | Config | n | per-trade Sharpe | DSR | bootstrap Sharpe p05 |
|---|---|---|---|---|---|
| BTC | -10 | 88 | +0.1645 | 0.850 | -0.006 |
| BTC | -7 | 85 | +0.1539 | 0.818 | -0.024 |
| ETH | -10 | 138 | -0.0035 | 0.287 | -0.142 |
| ETH | -7 | 129 | -0.0230 | 0.216 | -0.164 |

Note in passing that **neither** configuration clears DSR 0.95 on the
2020-2026 sample at N_TRIALS=2, and ETH's per-trade edge over this window is
indistinguishable from zero. That is a statement about the PDO sleeve itself,
not about the threshold question, and it is not what this study was
pre-registered to decide - but it is the more important number on the page and
should not be buried.

## a.4 The mechanism, and a finding that was not anticipated

-7% is **not** a strict subset of -10%. Blocking a setup frees the
one-trade-per-day slot and the no-overlap slot, so a *later* touch bar the same
day can fire instead - and by then the rolling 30-day return has often drifted
above -7%. From `results/qa_trades.csv`:

- BTC: 3 trades removed, 0 added. Removed trades mean **+91.8 bp** - i.e. on
  BTC the -7% filter deletes trades that were, in this sample, profitable.
- ETH: 12 trades removed, **3 added**. The 12 removed are mean -68.6 bp (so
  removing them helps), but the 3 substitutes are catastrophic:

| Substitute entry | BTC 30d % | net bp |
|---|---|---|
| 2021-09-24 08:00 (replaces 04:00, +23.9 bp) | -6.91 | **-859.7** |
| 2022-09-18 14:00 (replaces 10:00, +58.1 bp) | -6.72 | **-418.0** |
| 2025-02-19 00:00 (replaces 02-18 07:00, +102.2 bp) | -5.59 | -25.9 |

Net of both effects, ETH's mean falls by 4.00 bp. This substitution channel is
the reason a "stricter risk filter" made the in-sample result *worse* rather
than merely smaller, and it is a general warning for any threshold change in a
one-trade-per-day sleeve: **tightening a gate does not only remove trades, it
re-times the ones that remain.**

Execution-ordering check: the count of hours where an exit and a new entry
coincided is **0** in all four runs (`same_hour_exit_entry_counts`), so the one
place where Pine (`position_size == 0` at bar start) and production
(`try_decide_for_variant` closes then re-enters on the same tick) could differ
never arose. The result is invariant to that choice.

## a.5 What could still be wrong (question a)

- **The OOS clause is uninformative, not merely weak.** Zero discriminating
  trades means the OOS window tested nothing about the threshold. If BTC enters
  a -7% to -10% 30-day drawdown in the next months the question becomes
  answerable and should be re-run. The IS evidence is what is actually carrying
  this verdict.
- **In-sample evidence is small too**: 15 discriminating trades over 6.7 years,
  3 of them on BTC. A -2 bp / -4 bp mean difference on 85-138 trades is well
  inside noise; the honest claim is "no reason to change", not "-10% is proven
  better".
- **Funding is not modelled**, matching the live sleeve. A BTC 24 h hold
  crosses ~3 settlements; at ~5 bp each this is a +/-15 bp per-trade band that
  dwarfs the 2-4 bp differences above. It is common to both configurations so
  it cannot flip the *sign* of the comparison, but it does mean the absolute
  BTC +35 bp/trade number is optimistic.
- **Entry price proxy**: the backtest fills at the touching hourly bar's close;
  production fills at `_get_current_price()` at dispatch. Same convention as the
  Pine reference (`process_orders_on_close`), but live slippage against that
  print is real and unmodelled beyond the flat cost.
- **Pine vs production regime offset** (documented, not a defect): the sleeve's
  +/-1 h jitter window makes its 720-hour window end at the just-closed bar,
  where Pine's `close[1]/close[721]` ends one hour earlier. The study reproduces
  the **sleeve**, because that is what runs in production. A study that
  reproduced Pine instead would shift every regime number by one hour.
- The pre-2026-05-18 caveat on `prod.db` aggregates (PK migration) does not
  apply here: this study reads raw `btc_1m` / `eth_1m` / `cd_spot_binance` rows,
  not derived aggregates.

---

# QUESTION (b) - the CDO (current-day-open) retouch variant

Definition is frozen in README.md section 2.2: CDO = first 1-minute open of the
UTC day; arm a direction when the intraday excursion from CDO reaches 2.0%;
enter at the close of the first hourly bar that then contains CDO +/-0.10%;
long if the up-excursion is the larger one, short if the down one; 1.00% stop
(= 1R), 2.00% target (= +2R), TIF `min(entry+24h, day+1 01:00 UTC)`, 18 bp =
0.18 R round trip, one trade per asset per UTC day, no regime filter. BTC +
ETH, 2020-01-01 -> 2026-09-08.

## b.1 Clause-to-number table

| Clause | Requirement | Measured | Fired? |
|---|---|---|---|
| **B1** | bootstrap 5th percentile of mean R **> 0** (iid percentile, n_iter=10,000, seed=42) | **-0.2140 R** (point -0.1640 R, p95 -0.1144 R; P(mean > 0) = **0.0000**) | **NO** |
| **B2** | >= 3 of 4 complete walk-forward folds positive (`gates.walk_forward_folds`, fit 730 d / OOS 365 d / step 365 d) | **0 of 4** positive | **NO** |
| **B3** | `dsr_from_returns(R, n_trials=2)["dsr"] > 0.95` | **7.20e-09** | **NO** |
| **B-VERDICT** | all three required | 0 of 3 fired | **KILL** |

Sample: **1,855 trades** (BTC 725, ETH 1,130), mean **-0.164 R**, total
**-304.2 R**, max drawdown **308.9 R**. Cross-check: the module's
`bootstrap.bootstrap_sharpe` p05 (-0.1661) is reproduced bit-for-bit by the
study's own resampler (`B1_replication_matches_module: true`), so the mean-R
percentiles come from literally the same 10,000 resamples.

**This was the pre-registered expected outcome** (prior P(KILL) ~ 0.85, with B3
named as the most likely failure clause). All three failed, and by wide
margins.

## b.2 Walk-forward folds (`results/qb_folds.csv`)

| Fold | Fit window | OOS window | n | mean R | positive? |
|---|---|---|---|---|---|
| 1 | 2020-01-03 -> 2022-01-02 | 2022-01-02 -> 2023-01-02 | 347 | -0.110 | no |
| 2 | 2021-01-02 -> 2023-01-02 | 2023-01-02 -> 2024-01-02 | 135 | -0.247 | no |
| 3 | 2022-01-02 -> 2024-01-02 | 2024-01-02 -> 2025-01-01 | 234 | -0.139 | no |
| 4 | 2023-01-02 -> 2025-01-01 | 2025-01-01 -> 2026-01-01 | 245 | -0.096 | no |
| 5 (partial, excluded by pre-registration) | 2024-01-02 -> 2026-01-01 | 2026-01-01 -> 2026-09-08 | 133 | -0.040 | no |

## b.3 Every cut is negative (`results/qb_summary.csv`)

| Slice | n | mean R | win rate |
|---|---|---|---|
| pooled | 1,855 | -0.164 | 36.6% |
| BTC | 725 | -0.167 | 37.8% |
| ETH | 1,130 | -0.162 | 35.8% |
| LONG | 893 | -0.176 | 37.1% |
| SHORT | 962 | -0.153 | 36.2% |
| pre-ETF (<2024-01-11) | 1,254 | -0.196 | 34.8% |
| post-ETF (>=2024-01-11) | 601 | -0.098 | 40.3% |
| BTC pre-ETF / post-ETF | 527 / 198 | -0.219 / -0.027 | 34.9% / 45.5% |
| ETH pre-ETF / post-ETF | 727 / 403 | -0.179 / -0.132 | 34.8% / 37.7% |
| 2020 / 2021 / 2022 / 2023 / 2024 / 2025 / 2026 | 289 / 471 / 348 / 135 / 234 / 245 / 133 | -0.228 / -0.228 / -0.104 / -0.247 / -0.139 / -0.096 / -0.040 | - |

Long-only sub-series: n=893, mean -0.176 R, bootstrap p05 -0.248, DSR 6.0e-06.
Short-only: n=962, mean -0.153 R, p05 -0.221, DSR 4.1e-05. Neither leg is what
killed it; **both legs are dead**, so the "the short leg is dragging a real long
edge down" escape hatch is closed.

There is a monotone drift toward less-negative in later years (-0.228 in 2021
-> -0.040 in 2026). It never reaches zero, and reading a trend off seven annual
means of a series whose pooled DSR is 1e-09 would be exactly the mistake this
pre-registration exists to prevent.

## b.4 The mechanism of failure - no edge, not a negative edge

The exit mix is the whole story (`results/qb_clauses.json`):

| Exit | share |
|---|---|
| stop (-1.18 R) | 57.7% |
| target (+1.82 R) | 26.4% |
| TIF (mark-to-close) | 15.9% |

At a 2R target and 18 bp of cost the break-even target-hit rate is
`(1 + 0.18) / (2 + 1) =` **39.3%**. The realised rate is **26.4%**.

At **zero cost** the mean is **+0.016 R per trade**. That is the honest summary
of question (b): the CDO retouch is not an anti-signal, it is a **coin flip**,
and the 18 bp round trip on a 1%-risk unit (0.18 R, ~18% of the risk unit) is
what turns a coin flip into a -0.164 R bleed. Any variant of this idea has to
find its edge before it can argue about costs - and the gross number says there
is none to find at this cadence.

## b.5 What could still be wrong (question b)

- **The stop/target/TIF wrapper is mine, and it is only one wrapper.** PDO
  itself has no stop; a stop had to be invented so that "mean R" had a
  denominator. A different risk unit or a different target multiple would give
  different numbers. The gross +0.016 R and the 26.4% vs 39.3% gap are the
  wrapper-independent facts, and they are the ones that condemn it. Sweeping the
  wrapper would also raise the trial count above the pre-registered
  N_TRIALS = 2 and would need its own pre-registration.
- **Same-bar stop/target ambiguity is immaterial**: the pre-registered
  conservative rule (stop fills first) was invoked **0 times** in 1,855 trades -
  no 1-minute bar contained both a -1% and a +2% excursion from the entry.
  `mean_R_if_all_ties_were_targets` = -0.16398, identical to the headline. To
  move the mean to zero would take 101 stop->target flips.
- **Known definitional artifact in my own rule, reported not hidden**: the first
  hourly bar of a UTC day trivially contains CDO (the day's open is that bar's
  open), so a first-bar trigger is an "excursion", not a real retouch. 344 of
  1,855 trades (18.5%) are first-bar triggers. Excluding them is a **post-hoc**
  cut and is reported only as a sensitivity: n=1,511, mean -0.139 R, DSR
  2.7e-06. The verdict is unchanged. Any future CDO work should write the rule
  so the excursion must complete *before* the touching bar.
- **1-minute path resolution**: stops and targets are checked on 1-minute OHLC,
  so intra-minute sequencing is unknown. With zero same-bar ties this only
  affects which of two *different* minutes came first, which the bar ordering
  already settles.
- **No funding, no per-trade slippage beyond the flat 18 bp.** Both make the
  result more negative, not less.
- **Fills at hourly closes** on both entry and TIF exit. Stops and targets fill
  at their exact trigger price with no gap-through, which is *optimistic*; real
  stop fills on a 1% stop in crypto perps are worse than the printed level.

---

## Deviations from the pre-registration

**None.** Every clause was evaluated as written, on the periods, costs, tools
and trial counts specified in README.md, and the README was not edited after the
first outcome number was computed.

Three items are worth flagging as *additions* rather than deviations - they were
computed after the clause results and are labelled post-hoc wherever they
appear: the regime-occupancy table (a.2), the substitution analysis (a.4), and
the "excluding first bar of day" sensitivity (b.5). None of them enters a
decision clause.

## Recommendations (recommendations only - no production file was changed)

1. **Leave `REGIME_THRESHOLD_PCT = -10.0` as it is.** The predecessor's -7%
   fails both pre-registered clauses. Its post-hoc provenance is already flagged
   in `signal.pine`'s own tooltip, and this study finds nothing to overturn
   that.
2. **Re-run question (a) if BTC spends real time in a -7% to -10% 30-day
   drawdown**, which would give the OOS clause something to measure. The scripts
   here re-run unchanged.
3. **Do not build a CDO retouch sleeve.** It is a coin flip before costs.
4. Separately, and more important than either question: the pooled 2020-2026 PDO
   numbers in a.3 show **ETH at -0.62 bp per trade with a DSR of 0.29**. The
   sleeve's own README already flags "parameters were swept ... without visible
   walk-forward CV". A pre-registered walk-forward re-validation of the live PDO
   sleeve (not of a threshold inside it) looks like a better use of the next
   study slot than any further adjacency.

## Artefacts

Every number above comes from `results/`:

| File | Contents |
|---|---|
| `parity.json` | the byte-equality gate |
| `qa_trades.csv` | all 440 PDO trades (2 assets x 2 thresholds) |
| `qa_fires.csv` | all 1,302 gate-passing touch bars with their regime number |
| `qa_summary.csv` | mean/median/sd/win-rate per asset x period x threshold |
| `qa_clauses.json` | A1/A2 arithmetic, deltas, regime occupancy, verdict |
| `qa_band_trades.csv`, `qa_band_trade_list.csv` | the 15 discriminating trades |
| `qa_oos_trade_list.csv` | the 7 OOS trades |
| `qa_regime_occupancy.csv` | % of hours in each regime band per period |
| `qa_dsr.csv`, `qa_bootstrap.csv` | supporting DSR / bootstrap at N_TRIALS=2 |
| `qb_trades.csv` | all 1,855 CDO trades with exit type and R |
| `qb_summary.csv` | every reported slice |
| `qb_folds.csv` | the 5 walk-forward folds |
| `qb_clauses.json` | B1/B2/B3 numbers, robustness bounds, verdict |

Reproduce with the venv python from the repo root:

```
python studies/notebooks/pdo_adjacents/parity_check.py     # must exit 0
python studies/notebooks/pdo_adjacents/question_a_regime.py
python studies/notebooks/pdo_adjacents/question_b_cdo.py
C:/Python/Python313/python.exe studies/notebooks/pdo_adjacents/build_notebook.py
```

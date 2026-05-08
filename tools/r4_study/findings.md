# R4 calendar-window study — running findings

The R4 sub-sleeves (R4_BTC, R4_ETH) trade fixed-time windows on
specific weekdays in weeks 1-2 of the month. The upstream rationale
for the exact `(weekday, week-of-month, entry hour, exit hour)` tuple
was a data-mined sweep, not a structural argument. This directory
holds the validation work for that sweep — does the alpha generalize,
and is it era-dependent?

Re-run anything: each script reads `trader.db` directly and prints to
stdout. They're cheap; rerun whenever data updates.

## Scripts

- [`r4_lib.py`](r4_lib.py) — shared loaders + window-return functions
- [`era_split.py`](era_split.py) — pre-Binance-perp / Binance→ETF / post-ETF for BTC; pre/post-ETH-ETF for ETH
- [`walk_forward.py`](walk_forward.py) — train 2018-2022, evaluate OOS on 2023-2026
- [`grid_search.py`](grid_search.py) — full sweep, top-by-t-stat / top-by-min-year / positive-every-year
- [`year_breakdown.py`](year_breakdown.py) — year-by-year (half-year) for hand-picked configs
- [`sizing_study.py`](sizing_study.py) — equal-weight vs per-cell-expectancy sizing (D study)

## Findings (current)

### 1. The R4_BTC live baseline is post-Binance-perp emergent

`Mon+Wed wk1-2 06→18 UTC` (the live R4_BTC config):

| Era | n | mean/trade | t-stat |
|---|---|---|---|
| pre-Binance-fut (2018→2019-09-07) | 82 | **−0.42%** | **−0.99** |
| Binance-fut → ETF (2019-09-08 → 2024-01-10) | 210 | +0.39% | +2.16 |
| post-BTC-ETF (2024-01-11 → today) | 110 | **+0.77%** | **+3.43** |

The strategy literally did not work before the Binance perp market
matured (negative mean, t-stat below zero). It's a flow-driven /
positioning-driven phenomenon that depends on liquid futures and (post-
2024) ETF AP hedging. **Live decision: ride it while it works, monitor
expectancy via [services/strategy_health.py](../../services/strategy_health.py),
disable the sleeve if expectancy goes negative.**

### 2. Wed+Fri wk1-2 04→14 UTC is era-stable

A single configuration was strongly positive in every era:

| Era | n | mean/trade | t-stat |
|---|---|---|---|
| pre-Binance-fut | 82 | +0.53% | +1.86 |
| Binance-fut → ETF | 209 | +0.45% | +3.15 |
| post-BTC-ETF | 111 | +0.48% | +3.03 |

Walk-forward (train 2018-2022 → test 2023-2026) survives at OOS t=+2.50.
Grid search of ~7,500 configs found 57 that were positive in every full
year 2018-2025; this one tops them by t-stat (+4.23 over full sample).
Likely mechanism: NFP anticipation (Fri wk1 alone has the largest single-
cell t-stat) plus early-month flows.

### 3. wk1-2 vs all-days filter

Pre-ETF, "all-days" had a comparable per-trade mean and higher cumulative
P&L (more samples). Post-ETF, the wk1-2 filter became significantly
stronger per-trade (~2× advantage). Plausible mechanism: monthly
mutual-fund / payroll flows hit ETFs early in the month, and APs hedge
those into spot during the first two weeks.

### 4. R4_ETH live config is era-stable

`Tue→Wed wk1-2 20→20 UTC` (the live R4_ETH config):

| Era | n | mean/trade | t-stat |
|---|---|---|---|
| ETH pre-ETF (through 2024-07-22) | 110 | +1.17% | +3.03 |
| ETH post-ETF | 43 | **+1.73%** | +2.60 |

The Tuesday-uniqueness is real — shifting one weekday over (Wed→Thu
20→20) gives persistently negative returns. Doesn't depend on ETF
launch.

### 5. Cross-asset bonus

`Wed+Fri wk1-2 04→14` (the BTC top) also works on ETH (+0.62% pre-ETF,
+0.48% post-ETF). Same window/days extract similar signal across both
majors — supports a real macro flow mechanism rather than asset-
specific noise.

## Open items

- Walk-forward on the post-ETF era specifically (only 2.3y available;
  needs more time to be conclusive).
- Quantify how much of post-ETF Mon+Wed alpha is concentrated on days
  immediately following large ETF inflow days (would need ETF flow
  data — not currently in trader.db).
- Whether the "Friday wk1 NFP" cell remains positive when actual NFP
  surprise direction is conditioned out.

## Per-cell sizing study (added 2026-05-08, see `sizing_study.py`)

Tested whether reweighting the four R4 sleeves by their post-ETF
per-trade expectancy would have outperformed equal-weight
(REGIME_WEIGHTS_FULL × inner_lev × vol_lev). Sleeves and post-ETF
expectancies (n=55 to 111 fires each since 2024-01-11):

| Sleeve | n | mean/trade | t |
|---|---|---|---|
| R4_BTC (Mon 06→18) | 55 | +0.833% | +2.69 |
| R4_ETH (Tue→Wed 20→20) | 55 | +1.823% | +3.10 |
| R4_BTC_V2 (Wed+Fri 04→14) | 111 | +0.481% | +3.03 |
| R4_ETH_V2 (Wed+Fri 04→14) | 111 | +0.556% | +2.77 |

Reweighting toward the higher-edge cells (R4_ETH gets +0.118 weight
shift; R4_BTC, R4_BTC_V2, R4_ETH_V2 each give up some) produced:

| Window | Scheme | n_days | cum% | t | Sharpe (annualized) |
|---|---|---|---|---|---|
| post-ETF | EQUAL | 221 | +185.5% | +4.71 | **5.03** |
| post-ETF | EXPECT | 221 | +199.7% | +4.27 | 4.56 |
| pre-ETF (sanity) | EQUAL | 391 | +211.9% | +4.06 | **3.26** |
| pre-ETF (sanity) | EXPECT | 391 | +223.5% | +3.81 | 3.06 |

**Verdict: not worth implementing.** EXPECT achieves marginally higher
cumulative return (+14% on the post-ETF window) but consistently lower
Sharpe — concentrating in R4_ETH amplifies its variance more than its
mean. The sleeve ranking is consistent across eras (R4_ETH wins both),
which validates the V2 split itself, but a per-cell-edge sizing
overlay doesn't add risk-adjusted alpha. Stay with the symmetric V1/V2
weights.

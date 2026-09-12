# Actual ADX v2 trade-ledger diagnostic

Computed 2026-09-10 from [the exported live strategy report](../btc_study/out/adx_live_strategy_2026-09-10.json). INDEX:BTCUSD 1D; 49 closed trades. The current open long is excluded.

**This is descriptive reslicing of one existing backtest, not an independent backtest or an out-of-sample validation.** Cohorts include trades whose reported UTC entry timestamp is on/after January 1 of the named year. An earlier entry that exits after the cutoff is excluded.

Compound return = `100*(product(1+trade.tp.p)-1)` using exported net normalized trade returns. Long/short subsets are selected trades from the original long/short ledger, not separately rerun strategies or the user portfolio.

| Entry cohort | Side | Trades | Wins | Normalized compound | Mean trade | Median trade | Worst trade | Mean bar span (days) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2018+ | all | 27 | 15 | +2456.8% | +18.0% | +5.0% | -10.10% | 43.1 |
| 2018+ | long | 16 | 9 | +1197.5% | +25.0% | +6.5% | -10.09% | 46.5 |
| 2018+ | short | 11 | 6 | +97.1% | +7.8% | +3.6% | -10.10% | 38.1 |
| 2020+ | all | 22 | 13 | +1492.9% | +18.9% | +6.5% | -10.10% | 44.9 |
| 2020+ | long | 14 | 8 | +760.0% | +24.4% | +6.5% | -10.09% | 46.3 |
| 2020+ | short | 8 | 5 | +85.2% | +9.3% | +7.0% | -10.10% | 42.4 |
| 2024+ | all | 9 | 6 | +115.9% | +9.8% | +11.5% | -10.10% | 38.9 |
| 2024+ | long | 5 | 3 | +55.2% | +10.2% | +11.5% | -10.09% | 34.2 |
| 2024+ | short | 4 | 3 | +39.1% | +9.3% | +12.6% | -10.10% | 44.8 |

Worst trade is not portfolio maximum drawdown. Holding spans use daily bar indexes, so intrabar fill timing is not reconstructed. Live settings include 100% equity, shorts,0.05% commission per side, zero slippage/margins, on-close fills, no order-fill recalculation and no bar magnification.

## Initial-stop spotcheck

The code/settings imply that the initial stop is submitted at the next scheduled calculation after entry. The trade ledger cannot determine what happened inside every intervening bar. Five entries could be matched exactly to the older local INDEX daily.csv, and none crossed its 10% stop in the first following daily bar. This limited check does not establish that the delay is harmless elsewhere.

| Reported entry date | Side | Next day low | Next day high | Stop | Crossed? |
|---|---|---:|---:|---:|---|
| 2025-07-16 | long | 117500.50 | 121012.09 | 106830.51 | False |
| 2025-10-05 | long | 123144.47 | 126219.03 | 111163.92 | False |
| 2025-11-07 | short | 101489.41 | 103351.25 | 113629.77 | False |
| 2026-01-12 | short | 90948.09 | 96412.53 | 100322.75 | False |
| 2026-05-30 | short | 73316.72 | 74198.89 | 81182.34 | False |

The 2024+ cohort excludes the 2023-10-18 long that closed 2024-01-06 (+55.13%), because its entry was before 2024.

Source SHA256: `0eeacf67c0992068f4ee559cf8a254d87174912907bab68ef8f4cfd9f03efa42`.

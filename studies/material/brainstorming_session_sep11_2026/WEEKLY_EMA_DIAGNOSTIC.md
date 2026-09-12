# Completed-week BTC EMA5/EMA21 diagnostic

Calculated 2026-09-10 from an existing local Binance BTCUSDT daily cache; no network refresh.

Complete UTC weeks: 2017-12-11 through 2026-08-31. First evaluated week after 105-week warmup: 2019-12-16.

Strict EMA5 crossing above EMA21, both based on completed weekly closes. EMA recurrence starts at the first weekly close. Confirmation occurs the following Monday 00:00 UTC. All crosses after warmup are listed.

Forward return uses signal close as an event-study anchor. MAE means the worst subsequent weekly low relative to that anchor, clipped at zero; it is not peak-to-trough drawdown. The CSV/JSON separately report maximum weekly-close peak-to-trough drawdown. These are not executable strategy returns.

| Signal week starting | 4w return / MAE | 12w return / MAE | 26w return / MAE |
|---|---:|---:|---:|
| 2020-01-27 | -8.6% / -9.9% | -17.6% / -59.5% | +18.6% / -59.5% |
| 2020-05-04 | +11.7% / -6.0% | +26.9% / -6.0% | +77.4% / -6.0% |
| 2021-08-09 | -2.0% / -8.8% | +34.7% / -15.7% | -10.5% / -29.9% |
| 2023-01-16 | +6.9% / -6.0% | +33.5% / -13.9% | +32.5% / -13.9% |
| 2023-10-16 | +24.6% / -0.4% | +39.1% / -0.4% | +116.5% / -0.4% |
| 2024-08-19 | -1.0% / -18.2% | +39.9% / -18.2% | +49.9% / -18.2% |
| 2024-09-23 | +3.7% / -10.1% | +45.1% / -10.1% | +25.6% / -10.1% |
| 2025-04-21 | +16.3% / -1.0% | +25.1% / -1.0% | +22.2% / -1.0% |
| 2026-08-24 | pending / pending | pending / pending | pending / pending |

## Descriptive comparison

All eligible weekly anchors share the same starting date and full-horizon availability rule. Their horizons overlap heavily; this is not an independent control sample or a significance test.

| Horizon | Sample | n | Positive | Mean return | Median return | Median MAE |
|---|---|---:|---:|---:|---:|---:|
| 4w | crossovers | 8 | 5 | +6.4% | +5.3% | -7.4% |
| 4w | all_weekly_anchors | 347 | 190 | +4.3% | +1.2% | -7.7% |
| 12w | crossovers | 8 | 7 | +28.3% | +34.1% | -12.0% |
| 12w | all_weekly_anchors | 339 | 191 | +14.0% | +6.6% | -15.7% |
| 26w | crossovers | 8 | 7 | +41.5% | +29.0% | -12.0% |
| 26w | all_weekly_anchors | 325 | 213 | +37.5% | +20.4% | -20.2% |

## Limits

- Descriptive event study, no FOMC conditioning or causal inference
- Overlapping outcomes and market regimes; counts are not independent trials
- No costs, slippage, taxes, position exits or strategy optimization
- Binance is not the user's INDEX chart; initialization and feed may change cross timing
- Recent signals without a full forward horizon remain censored, not failures
- Weekly-low MAE is not peak-to-trough drawdown; weekly-close drawdown omits intrabar paths

Source hash: `5c20923ca9d7b6893a829314ac70663281fe59a46c72f74acbb65b3791f0dbba`.

Reproduce: `python btc_study/scripts/20_weekly_ema_cross_diagnostic.py`.

"""S-003 ADX signal parameters.

Calibration of the ADX signal itself. Per-variant operational params
(allocation %, leverage, stop-loss %) come from the variant registry /
sleeve_cfg, not from this file.
"""

# Indicator periods (canonical S-003 values, match backtest_adx_regime.py).
ADX_PERIOD = 14
ADX_LOW_THRESH = 20.0
ADX_HIGH_THRESH = 25.0
EMA_LEN = 50

# Trend filter EMA length. Set to 0 to disable.
# Filter rule (asymmetric — LONGs only, since 2026-05-04):
#   LONG  requires close > EMA(N) AND close > EMA(50);
#   SHORT has no trend filter (close < EMA(50) is the only direction gate).
# Bitstamp BTC/USD walk-forward 2018-2024 in-sample optimum tied at 150/160
# with the symmetric filter (+1181% with SL=12% vs +747% baseline). The
# 2026-05-04 funding-aware backtest_runner replay over 2023-09 → 2026-05
# showed the symmetric variant losing $1,121 vs no-filter because counter-
# trend SHORTs in bull markets earn perp funding AND often pay off on price
# (e.g. 2025-02-22 +15.93%, 2025-10-11 +29.63%, 2026-01-21 +20.04%, all
# net of funding). LONG-only filter keeps the bull-market whipsaw protection
# the symmetric variant was designed for (e.g. 2026-04-26 LONG at $78,660
# with close < EMA(150) $79,325).
TREND_EMA_LEN = 150

WARMUP_BARS = max(ADX_PERIOD * 3, EMA_LEN + 1, TREND_EMA_LEN + 1)

# Round-trip transaction cost (5bp each leg on BTC perps — taker estimate).
COST_BP_RT = 10.0

# ─── Tier-2 calibration (2026-07-22, from the 2026-06-26 adx_study) ─────────
# Combined effect on the 2018→2026-06 backtest: maxDD −27.3% → −15.1%,
# MAR 1.78 → 3.09 (OOS MAR 3.16). Cost: forfeits the counter-trend-short
# funding carry (~net-flat; S-078 CARRY harvests funding delta-neutrally).
# Each lever is independently disableable; provenance in
# docs/calibration/adx.md and studies/notebooks/adx_study/findings.md.

# T2a — symmetric trend filter: SHORT entries require close < EMA(TREND_EMA_LEN)
# (mirror of the LONG rule; replaces the 2026-05-04 asymmetric design).
SYMMETRIC_TREND_FILTER = True

# T2b — ATR trailing exit alongside the ADX<20 exit and the 10% SL
# (research: harness.run(exit_mode="adx_or_atr", atr_mult=4.0)).
# 0 disables.
ATR_TRAIL_MULT = 4.0
ATR_TRAIL_PERIOD = 14

# Funding-crowding LONG veto: skip LONG entries when the 30d funding
# z-score exceeds this ("don't long over-crowded leverage" — avoids the
# 2025-10-05 ATH long). z = (today − mean_30d) / pstdev_30d over daily-mean
# cd_funding_rate, current day included, ≥10 samples required (fail-open).
# None disables.
FUNDING_VETO_Z = 1.5
FUNDING_VETO_DAYS = 30

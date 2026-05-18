"""S-105 SHORT_SQUEEZE signal parameters.

Locked from the threshold sweep + walk-forward + sensitivity analysis in
studies/notebooks/short_squeeze_sessions/strategy_backtest.ipynb (2026-05-18).
Don't re-tune these without re-running the sensitivity check — the values
sit on a plateau, not a spike, and small perturbations are safe but the
re-optimization process is biased upward in small samples.
"""

# ─── Bar-level trigger thresholds (percentile, not absolute) ───────────────

# Bar's perp_cvd must be in the bottom X% of the rolling 90-day London/NY
# distribution. Below 0.15 = bottom 15% = strong perp selling.
PERP_CVD_PCT_MAX = 0.15

# Bar's (spot_cvd - perp_cvd) must be in the top Y% of the same distribution.
# Above 0.70 = top 30%. Sensitivity analysis showed [0.70, 0.80] are equivalent
# historically (perp_cvd and divergence are correlated, so the perp filter
# does all the work), so we use the looser end for forward robustness.
DIVERGENCE_PCT_MIN = 0.70

# Bar must close in the upper 90% of its own range (any reversal off the low).
CLOSE_IN_RANGE_MIN = 0.10

# Bar must take out the low of the prior LOOKBACK_BARS 15m bars (sweep).
# 24 = 6h window.
LOOKBACK_BARS = 24

# Cooldown between consecutive triggers (in 15m bars). 16 = 4h.
COOLDOWN_BARS = 16

# ─── Rolling percentile window ─────────────────────────────────────────────

# Trailing-window length for percentile estimation. 90 days × ~56 London/NY
# bars/day = ~5000 bars. Picked from a plateau in sensitivity analysis;
# shorter (30-60d) gives slightly lower avg R; longer (180-365d) gives
# fewer trades. 90d is the inflection.
WINDOW_DAYS = 90

# ─── Daily macro context ───────────────────────────────────────────────────

# Asia session must satisfy ALL THREE: OI rose by at least ASIA_OI_PCT_MIN
# during asia, mean funding negative, asia closed below asia open.
ASIA_OI_PCT_MIN = 0.005

# ─── Session windows (UTC, trader-aligned) ─────────────────────────────────

SESSIONS = {
    "asia":   (0, 7),
    "london": (7, 14),
    "ny":     (14, 21),
}

# ─── Trade management ──────────────────────────────────────────────────────

# Take-profit at fixed N × R, where R = entry - trigger_bar.low (long).
TP_R = 3.0

# Stop just below trigger_bar.low with a small slippage buffer.
STOP_BUFFER = 0.001   # 10 bp below the bar's low

# Time stop: close at market if neither stop nor target hits within N hours.
TIME_STOP_HOURS = 6

# ─── Execution costs ───────────────────────────────────────────────────────

# 5 bp entry + 5 bp exit on BTC perps (taker).
COST_BP_RT = 10.0

# Slippage budget. Higher than FOMC's 10 bp because high-leverage scalp
# entries at sweep lows tend to fill in fast tape; bumping to 15 bp to be
# conservative for the simulator.
SLIPPAGE_BP_RT = 15.0

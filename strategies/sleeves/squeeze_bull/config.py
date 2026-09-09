"""S-107 SQUEEZE_BULL — strategy parameters (frozen).

Every value here is the frozen June-2026 rule as re-validated by
`studies/notebooks/squeeze_bull_revalidation/` (2026-09-08, verdict BUILD).
Nothing was re-tuned for this port. Changing any of them is a new
pre-registered study, not an edit — see docs/calibration/squeeze_bull.md.
"""
from __future__ import annotations

# ── Trigger ──────────────────────────────────────────────────────────────
# Open interest deleverages hard while price falls: forced liquidation of
# longs. Both conditions are measured over the trailing 4 HOURLY bars.
FLUSH_THRESHOLD = -0.02        # oi_close.pct_change(4) <= -2%
PRICE_DIR_THRESHOLD = -0.005   # close.pct_change(4)   <= -0.5%
COOLDOWN_HOURS = 24            # a kept fire silences the next 24 hourly bars

# ── Regime gate ──────────────────────────────────────────────────────────
# Long-only, and only in a bull regime: the ungated pool is a coin flip
# (profit factor 1.00 over 423 fires), the bull-gated pool is 1.74.
#
# BACKWARD-ONLY construction (2026-09-09 decision). The June study computed
# the 30-day return from the daily close of the CURRENT day and forward-
# filled it onto the hourly grid, so an 06:00 fire read a close that had not
# happened yet — up to 21 h of look-ahead. Production shifts the daily series
# by one day, so every fire reads a close at least 3 h old. On the ten
# out-of-sample fires this scores +0.246 R against the peeking gate's +0.202.
BULL_THRESHOLD = 0.10          # ret_30d > +10% = bull_30d
REGIME_LOOKBACK_DAYS = 30
REGIME_SHIFT_DAYS = 1          # THE causal guarantee. Never set this to 0.

# ── Execution ────────────────────────────────────────────────────────────
STOP_PCT = 0.02                # stop at entry x (1 - 0.02)
TARGET_PCT = 0.03              # target at entry x (1 + 0.03) = 1.5 R
TIF_HOURS = 48                 # time stop
# Within one bar the stop is checked BEFORE the target (conservative for a
# long). In the full sample no bar ever touched both first, so this is
# defensive rather than load-bearing.

# ── Costs (research convention; the bot books production costs) ──────────
COST_BP_RT = 18.0              # what the study charged, for reference only

ASSET = "BTC"
DIRECTION = "LONG"
SLEEVE_NAME = "SQUEEZE_BULL"

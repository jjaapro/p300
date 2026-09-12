"""Bot-level config for the standalone R4 calendar bot (S-099 / S-001 family).

Strategy parameters (windows, weekday and day-of-month rules, inner leverage,
vol gate) live in strategies/sleeves/timing_anomalies/internal/r4/config.py
and are NOT duplicated here. This file only holds what the BOT decides:
which variants run, how a fire is sized inside one variant, the late-entry
grace, and the stale-input policy. Changes here belong in
docs/calibration/r4.md per the calibration-log rule.

Which windows run: the ETH pair only, since 2026-09-12 (user decision — the
BTC windows are wired and tested but disabled in ENABLED below).

Calibration provenance: studies/notebooks/r4_bot_prep/findings.md (2026-09-06)
— STOP_LOSS_PCT = None (no stop level passed the pre-registered rule) and
LATE_ENTRY_MAX_S = 300 (5-minute grace from the late-entry cost curve).
"""
from pathlib import Path

from strategies.sleeves.timing_anomalies.internal.r4.config import (
    STRATEGY_R4_BTC, STRATEGY_R4_BTC_V2, STRATEGY_R4_ETH, STRATEGY_R4_ETH_V2,
)

VARIANT_ID = "bot_r4_v1"
BOT_NAME = "r4"
SHORT_NAME = "Bot: R4 calendar (ETH windows)"

CAPITAL_USDT = 10_000.0

# Which of the four sleeve variants this bot evaluates. Flip a value (and log
# it in docs/calibration/r4.md) when the disable rule fires or the operator
# decides. 2026-09-12: only the ETH windows run — R4_ETH (Tue -> Wed) is the
# era-stable window (r4_study §4, ranked first in both eras) while the BTC
# windows are post-perp emergent (§1). The BTC pair stays wired; a False
# entry is never evaluated (runner.tick skips it).
ENABLED = {
    STRATEGY_R4_BTC: False,
    STRATEGY_R4_ETH: True,
    STRATEGY_R4_BTC_V2: False,
    STRATEGY_R4_ETH_V2: True,
}

# Equal base weight per variant (r4_study sizing verdict: equal-weight beats
# expectancy-weighting). A fire sizes at CAPITAL × weight × the sleeve's own
# inner_lev × vol_lev (capped at LEV_CAP), so a single fire is ≤ 0.20 × 7.5 =
# 1.5× capital and typically ~1× at the usual 5× stack.
VARIANT_WEIGHT = {k: 0.20 for k in ENABLED}
LEV_CAP = 7.5                 # sleeve max: inner 2.5 × H_CAPS strong_bull 3.0

# Co-fire budget: sum of open R4 notional ≤ GROSS_MAX_X × capital. Wednesdays
# in week 1-2 can hold two positions with the ETH pair (ETH V1 still open +
# ETH V2; three when the BTC V2 is enabled too); a new fire is scaled DOWN to
# the remaining budget, never skipped, unless the remainder is below
# MIN_NOTIONAL_USDT.
GROSS_MAX_X = 3.0
MIN_NOTIONAL_USDT = 250.0

# Late-entry guard: a fire more than this many seconds after the window opens
# is logged as missed_window and never taken (the 2026-05-13 cold fills at
# +127 min were the only V2 losers). Value from the late-entry cost curve.
LATE_ENTRY_MAX_S = 300

# Intraday stop-loss as a fraction (0.03 = 3%) or None. The pre-registered
# sweep rejected every level; the scheduled exit remains the only exit.
STOP_LOSS_PCT = None

TICK_SECONDS = 60

# Stale-input policy: the scheduled-exit backstop prices both assets every
# tick (mgmt); today_inputs() needs BTC daily bars + LSR for the regime /
# gate / vol-target stack (entry). btc_1m/eth_1m also feed the entry price.
MGMT_TABLES = ["btc_1m", "eth_1m"]
ENTRY_TABLES = ["cd_spot_binance", "ca_long_short_ratio"]

LOGS_DIR = Path(__file__).resolve().parent / "logs"
DIAG_PATH = LOGS_DIR / "diag.jsonl"

"""Bot-level config for the standalone Chento Triple v3 bot.

Strategy parameters stay in strategies/sleeves/chento_triple_v3/config.py —
this file only holds what the BOT decides: capital, risk sizing, cadence,
and which tables gate evaluation. Changes here belong in
docs/calibration/chento_triple_v3.md per the calibration-log rule.
"""
from pathlib import Path

VARIANT_ID = "bot_chento_v3_v1"
BOT_NAME = "chento_v3"
SHORT_NAME = "Bot: Chento Triple v3"

# Paper capital for this bot's variant — its future sub-account balance.
CAPITAL_USDT = 10_000.0

# Fixed-R sizing (Premier-tier draft from the pool fact sheet): risk this %
# of capital per trade; notional = capital × RISK_PCT% / stop_distance%.
# The sleeve's stop is 5×ATR(14) on 15m bars, so stop_pct is typically
# ~1.5-4% and notional lands well under the cap.
RISK_PCT = 2.0
NOTIONAL_MAX_X = 3.0          # hard ceiling: notional ≤ 3× capital

TICK_SECONDS = 60             # sleeve self-gates entries to 15m boundaries

# Stale-input policy (feedback_table_freshness_contract):
#   MGMT_TABLES stale  → skip the whole tick (can't even manage positions)
#   ENTRY_TABLES stale → still run the sweep, but discard any new Intent
MGMT_TABLES = ["cd_futures_15m", "btc_1m"]
ENTRY_TABLES = ["okx_perp_1h", "ca_long_short_ratio"]

# Per-day gate-kill diagnostics — permanently ON in live (the OKX lockout
# went unseen for two months because these were off).
DIAG_PATH = str(Path(__file__).resolve().parent / "logs" / "diag.jsonl")

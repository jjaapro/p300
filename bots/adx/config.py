"""Bot-level config for the standalone ADX (S-003) bot.

Strategy parameters (incl. the 2026-07-22 Tier-2 calibration: symmetric
trend filter, ATR×4 trail, funding veto) live in
strategies/sleeves/adx/config.py. Changes here belong in
docs/calibration/adx.md per the calibration-log rule.
"""
from pathlib import Path

VARIANT_ID = "bot_adx_v1"
BOT_NAME = "adx"
SHORT_NAME = "Bot: ADX S-003 T2"

CAPITAL_USDT = 10_000.0

# Fixed-R sizing over the sleeve's effective initial stop (the tighter of
# the 10% SL and the 4×ATR trail seed — typically ~4-10%, so notional lands
# around 0.2-0.5× capital; the cap rarely binds).
RISK_PCT = 2.0
NOTIONAL_MAX_X = 3.0

# 60s ticks: entry decisions are daily (idempotent per UTC day) but the
# fixed-SL + ATR-trail sweep prices against live btc_1m every tick.
TICK_SECONDS = 60

# Stale-input policy: sweep needs a live price + daily candles; the veto
# additionally reads funding.
MGMT_TABLES = ["btc_1m", "cd_spot_binance"]
ENTRY_TABLES = ["cd_funding_rate"]

# The sleeve's per-variant stop-loss param (legacy sleeve_cfg.params path).
STOP_LOSS_PCT = 10.0

LOGS_DIR = str(Path(__file__).resolve().parent / "logs")

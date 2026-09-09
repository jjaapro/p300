"""Bot-level config for the standalone Squeeze Bull bot.

Strategy parameters stay in strategies/sleeves/squeeze_bull/config.py — this
file holds only what the BOT decides. Changes here belong in
docs/calibration/squeeze_bull.md per the calibration-log rule.
"""
from pathlib import Path

VARIANT_ID = "bot_squeeze_bull_v1"
BOT_NAME = "squeeze_bull"
SHORT_NAME = "Bot: Squeeze Bull (OI flush)"

CAPITAL_USDT = 10_000.0

# Fixed-R sizing. The sleeve's stop is a flat 2% below entry, so 1% risk maps
# to a 0.5x notional and the 3x cap never binds — unlike short_squeeze, whose
# swept-low stop is often only basis points wide. The cap is kept anyway as a
# structural guard, not as a working dial.
RISK_PCT = 1.0
NOTIONAL_MAX_X = 3.0

# 60s ticks: the sleeve prices exits off the CURRENT price rather than by
# walking bars, so tick cadence bounds exit fidelity. Entry self-gates to one
# evaluation per closed hourly bar, because every input is hourly.
TICK_SECONDS = 60

# Stale-input policy. The exit sweep only needs a live price. Every signal
# input is entry-side, and cd_open_interest is a live-read table on a 3h
# freshness contract, so a stale positioning feed must block entries loudly
# rather than trade on a stale 30-day return.
MGMT_TABLES = ["btc_1m"]
ENTRY_TABLES = ["cd_open_interest", "cd_futures_ohlcv"]

LOGS_DIR = Path(__file__).resolve().parent / "logs"
DIAG_PATH = LOGS_DIR / "diag.jsonl"

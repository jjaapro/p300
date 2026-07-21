"""Bot-level config for the standalone Short Squeeze bot.

Strategy parameters stay in strategies/sleeves/short_squeeze/config.py —
this file only holds what the BOT decides. Changes here belong in
docs/calibration/short_squeeze.md per the calibration-log rule.
"""
from pathlib import Path

VARIANT_ID = "bot_short_squeeze_v1"
BOT_NAME = "short_squeeze"
SHORT_NAME = "Bot: Short Squeeze"

CAPITAL_USDT = 10_000.0

# Fixed-R sizing. The sleeve's stop is 10bp below the swept low — often
# only bp from entry — so the uncapped notional would explode; the 3× cap
# is expected to bind frequently BY DESIGN (this replaces the README's
# "leverage 20-100x" suggestion with "risk 1%, never exceed 3× notional").
RISK_PCT = 1.0
NOTIONAL_MAX_X = 3.0

# 60s ticks: the sleeve's exit sweep checks CURRENT price against
# stop/target (not bar-walked), so tick cadence directly bounds exit
# fidelity. Entry evals self-gate to 15m boundaries in London/NY.
TICK_SECONDS = 60

# Stale-input policy: the sweep only needs a live price; every signal
# input is entry-side.
MGMT_TABLES = ["btc_1m"]
ENTRY_TABLES = ["cd_futures_15m", "cd_spot_15m", "cd_futures_ohlcv",
                "cd_open_interest", "cd_funding_rate"]

# Per-day gate-counter diagnostics — permanently ON in live (the sleeve has
# never paper-traded; we want the same visibility Chento now has).
DIAG_PATH = str(Path(__file__).resolve().parent / "logs" / "diag.jsonl")

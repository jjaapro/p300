"""Bot-level config for the standalone Squeeze Bull bot.

Strategy parameters stay in bots/squeeze_bull/strategy/config.py — this
file holds only what the BOT decides. Changes here belong in
docs/calibration/squeeze_bull.md per the calibration-log rule.
"""
from pathlib import Path

from bots.squeeze_bull.strategy.config import STOP_PCT

BOT_NAME = "squeeze_bull"

CAPITAL_USDT = 10_000.0

# Two paper variants in one process, on the SAME signals; only the exit
# differs (docs/calibration/squeeze_bull.md, 2026-09-12):
#   bot_squeeze_bull_v1         -2% stop, +3% target, 48h   (shipped 2026-09-09)
#   bot_squeeze_bull_nostop_v1  no stop,  +3% target, 48h   (added 2026-09-12,
#                               sizing_style_2026_09 policy P1b)
# Each variant keeps its own ledger, single-open guard and re-cut record.
VARIANTS = [
    {"id": "bot_squeeze_bull_v1",
     "short_name": "Bot: Squeeze Bull (OI flush)", "use_stop": True},
    {"id": "bot_squeeze_bull_nostop_v1",
     "short_name": "Bot: Squeeze Bull, no stop (OI flush)", "use_stop": False},
]
# The incumbent, for the dashboard registry and anything single-variant.
VARIANT_ID = VARIANTS[0]["id"]
SHORT_NAME = VARIANTS[0]["short_name"]

# Fixed-R sizing. The sleeve's stop is a flat 2% below entry, so 1% risk maps
# to a 0.5x notional and the 3x cap never binds — unlike short_squeeze, whose
# swept-low stop is often only basis points wide. The cap is kept anyway as a
# structural guard, not as a working dial.
RISK_PCT = 1.0
NOTIONAL_MAX_X = 3.0

# The no-stop variant is sized as if the 2% stop existed — the same 1% / 2%
# = 0.5x capital — so both variants hold the same notional on every fire and
# their ledgers differ only by the exit. Its R is measured against the same
# 2% reference distance (`_reference_stop_price` in the trade notes).
NOSTOP_NOTIONAL_X = RISK_PCT / 100.0 / STOP_PCT

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

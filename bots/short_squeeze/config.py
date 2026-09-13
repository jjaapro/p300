"""Bot-level config for the standalone Short Squeeze bot.

Strategy parameters stay in bots/short_squeeze/strategy/config.py —
this file only holds what the BOT decides. Changes here belong in
docs/calibration/short_squeeze.md per the calibration-log rule.
"""
from pathlib import Path

BOT_NAME = "short_squeeze"

CAPITAL_USDT = 10_000.0

# Two paper variants in one process, on the SAME signals; only the exit and
# the sizing differ (docs/calibration/short_squeeze.md, 2026-09-12):
#   bot_short_squeeze_v1         stop 10bp under the swept low, 3R target,
#                                6h time stop; fixed-R 1% capped at 3x
#                                (shipped 2026-07-21)
#   bot_short_squeeze_nostop_v1  no stop, no target, 6h time stop only;
#                                fixed notional 1x capital (added 2026-09-12,
#                                sizing_style_2026_09 policy P1)
# Each variant keeps its own ledger, single-open guard and re-cut record.
VARIANTS = [
    {"id": "bot_short_squeeze_v1",
     "short_name": "Bot: Short Squeeze", "use_stop": True},
    {"id": "bot_short_squeeze_nostop_v1",
     "short_name": "Bot: Short Squeeze, no stop, 6h", "use_stop": False},
]
# The incumbent, for the dashboard registry and anything single-variant.
VARIANT_ID = VARIANTS[0]["id"]
SHORT_NAME = VARIANTS[0]["short_name"]

# Fixed-R sizing. The sleeve's stop is 10bp below the swept low — often
# only bp from entry — so the uncapped notional would explode; the 3× cap
# is expected to bind frequently BY DESIGN (this replaces the README's
# "leverage 20-100x" suggestion with "risk 1%, never exceed 3× notional").
RISK_PCT = 1.0
NOTIONAL_MAX_X = 3.0

# The no-stop variant has no stop distance to size from, and the 3x cap was
# only ever justified by a bp-wide stop that it does not have, so it runs a
# fixed 1x of capital: R outcomes are the study's, dollars are ~1/2.5 of the
# stop variant's, and so is the tail that moves from the stop to the account.
NOSTOP_NOTIONAL_X = 1.0

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

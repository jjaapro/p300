"""Bot-level config for the Chento Triple v3 ETH leg (multi-asset plan,
studies/material/plans/multi_asset_chento_plan.md, 2026-08-23).

Strategy parameters live in bots/chento_v3/strategy/config.py
and resolve per-asset from CHENTO_V3_ASSET (set by runner.py before the
sleeve is imported). Changes here belong in
docs/calibration/chento_triple_v3.md per the calibration-log rule.
"""
from pathlib import Path

VARIANT_ID = "bot_chento_v3_eth"
BOT_NAME = "chento_v3_eth"
SHORT_NAME = "Bot: Chento Triple v3 ETH"

CAPITAL_USDT = 10_000.0

RISK_PCT = 2.0
NOTIONAL_MAX_X = 3.0

TICK_SECONDS = 60

# Stale-input policy — the ETH twins of the BTC leg's tables.
MGMT_TABLES = ["cd_futures_eth_15m", "eth_1m"]
# okx_perp_eth_1h left 2026-09-14 with the OKX gate (see bots/chento_v3/config.py).
ENTRY_TABLES = ["ca_long_short_ratio"]

# Post-loss sizing — RETIRED 2026-09-19 (BACKLOG decision 15): ETH used to
# halve risk on the trade after a loss (the overlay study's rule differed from
# the bot's on 54 of 184 trades, and on the gate-off pool the effect is noise).
# The runner's code path is kept, flag off.
TILT_HALF_AFTER_LOSS = False

DIAG_PATH = str(Path(__file__).resolve().parent / "logs" / "diag.jsonl")

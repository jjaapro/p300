"""Bot-level config for the Chento Triple v3 ETH leg (multi-asset plan,
studies/material/plans/multi_asset_chento_plan.md, 2026-08-23).

Strategy parameters live in strategies/sleeves/chento_triple_v3/config.py
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
ENTRY_TABLES = ["okx_perp_eth_1h", "ca_long_short_ratio"]

# Per-asset tilt policy (overlay study + backward-only confirmation,
# 2026-08-23): ETH disables the sleeve's skip-after-loss (FILTER_NO_TILT is
# False when ASSET=ETH) and instead halves risk on the trade after a loss —
# on ETH this matched skip's MAR while keeping ~64% more income.
TILT_HALF_AFTER_LOSS = True

DIAG_PATH = str(Path(__file__).resolve().parent / "logs" / "diag.jsonl")

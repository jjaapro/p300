"""Bot-level config for the Carry S-078 ETH twin (paper, 2026-09-19).

Strategy parameters live in bots/carry/strategy/config.py and resolve the asset
from CARRY_ASSET (set by runner.py before the sleeve is imported). Changes here
belong in docs/calibration/carry.md per the calibration-log rule.
"""

VARIANT_ID = "bot_carry_eth_v1"
BOT_NAME = "carry_eth"
SHORT_NAME = "Bot: Carry S-078 ETH"

CAPITAL_USDT = 10_000.0

# Fixed-notional sizing, as the BTC bot: delta-neutral spot-long + perp-short,
# income = funding, no stop. Notional = capital × this.
CARRY_NOTIONAL_X = 1.0

TICK_SECONDS = 60

# The signal AND the P&L are funding (cd_funding_rate_eth); the spot close comes
# from eth_1m and the perp close from cd_futures_eth_15m (strategy config
# PRICE_SOURCES["ETH"]).
MGMT_TABLES = ["eth_1m", "cd_futures_eth_15m", "cd_funding_rate_eth"]
ENTRY_TABLES: list[str] = []          # nothing beyond mgmt

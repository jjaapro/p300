"""Bot-level config for the standalone Carry (S-078) bot.

Strategy parameters (entry threshold, exit streak) live in
strategies/sleeves/carry/config.py. Changes here belong in
docs/calibration/carry.md per the calibration-log rule.
"""

VARIANT_ID = "bot_carry_v1"
BOT_NAME = "carry"
SHORT_NAME = "Bot: Carry S-078"

CAPITAL_USDT = 10_000.0

# Fixed-notional sizing (delta-neutral: spot-long + perp-short, price P&L
# ≈ 0, income = funding). Fixed-R doesn't apply — there is no stop; the
# exit is the 3-day negative-funding streak. Notional = capital × this.
CARRY_NOTIONAL_X = 1.0

TICK_SECONDS = 60

# The signal AND the P&L are funding; price feed prices the entry/exit.
MGMT_TABLES = ["btc_1m", "cd_funding_rate"]
ENTRY_TABLES: list[str] = []          # nothing beyond mgmt

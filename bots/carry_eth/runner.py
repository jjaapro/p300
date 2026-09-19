"""Standalone runner for the Carry S-078 ETH twin.

Thin wrapper over bots/carry/runner.py: sets CARRY_ASSET BEFORE the sleeve is
imported (bots/carry/strategy/config.py reads it at import), swaps in this bot's
config, and runs the shared loop. One process, one asset, one variant
(`bot_carry_eth_v1`).

Usage:
  python bots/carry_eth/runner.py           # live loop, 60s ticks
  python bots/carry_eth/runner.py --once    # single tick and exit
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

os.environ["CARRY_ASSET"] = "ETH"

from bots.carry_eth import config as ethcfg  # noqa: E402
from bots.carry import runner as base  # noqa: E402  (after env is set)

if __name__ == "__main__":
    sys.exit(base.run(ethcfg))

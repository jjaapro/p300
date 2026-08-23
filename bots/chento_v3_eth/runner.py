"""Standalone runner for the CHENTO_TRIPLE_V3 ETH leg.

Thin wrapper over bots/chento_v3/runner.py: sets the asset + diagnostics
environment BEFORE the sleeve package is imported (the sleeve config resolves
its tables from CHENTO_V3_ASSET at import time), swaps in this bot's config,
and runs the shared loop. One process, one asset, one variant
(`bot_chento_v3_eth`).

Usage:
  python bots/chento_v3_eth/runner.py           # live loop, 60s ticks
  python bots/chento_v3_eth/runner.py --once    # single tick and exit
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from bots.chento_v3_eth import config as ethcfg  # noqa: E402

os.environ["CHENTO_V3_ASSET"] = "ETH"
os.environ["CHENTO_V3_DIAG"] = "1"
os.environ["CHENTO_V3_DIAG_PATH"] = ethcfg.DIAG_PATH

from bots.chento_v3 import runner as base  # noqa: E402  (after env is set)

if __name__ == "__main__":
    sys.exit(base.run(ethcfg))

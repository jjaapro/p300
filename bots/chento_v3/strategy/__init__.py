"""CHENTO_TRIPLE_V3 — third-generation chento-inspired sleeve.

Triple-composite mean-reversion-into-extreme on BTC perp 15m, with
adaptive A4 ladder sizing and asymmetric regime filter. See README.md
for the full strategy spec and findings provenance.
"""
from . import signal as signal_module
from . import math as math_module
from . import config as config_module

# Re-export the orchestrator entry points at package level for legacy paths
# The bot imports this PACKAGE, so the entry points are re-exported here.
from .signal import decide, execute

__all__ = ["signal_module", "math_module", "config_module",
           "decide", "execute"]

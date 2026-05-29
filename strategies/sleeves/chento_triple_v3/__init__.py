"""CHENTO_TRIPLE_V3 — third-generation chento-inspired sleeve.

Triple-composite mean-reversion-into-extreme on BTC perp 15m, with
adaptive A4 ladder sizing and asymmetric regime filter. See README.md
for the full strategy spec and findings provenance.
"""
from . import signal as signal_module
from . import math as math_module
from . import config as config_module

# Re-export the orchestrator entry points at package level for legacy paths
from .signal import (
    try_decide_for_variant,
    execute_for_variant,
    try_fire_for_variant,
)

__all__ = [
    "signal_module", "math_module", "config_module",
    "try_decide_for_variant", "execute_for_variant", "try_fire_for_variant",
]

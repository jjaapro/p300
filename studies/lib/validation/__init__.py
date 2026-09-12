"""Statistical validation toolkit -- numpy, pandas and stdlib only.

Ported from the trader repo's root modules (deflate.py, cpcv.py,
bootstrap_sharpe.py, harvey_liu_haircut.py, romano_wolf_stepm.py,
model_confidence_set.py, spa_test.py, crisis_alpha_gate.py, flat_max_test.py,
reverse_stress.py, dd_duration.py, kelly_sizing.py, sizing_risk.py,
alpha_halflife.py, triple_barrier.py, changepoint_detector.py) with every
DB / memo / CLI part stripped.  Each module's docstring names its source file
and the paper it implements.  ``fundamental_law`` and ``gates`` are new
(``gates`` is GATE_VALIDATION.md as code).

Usage::

    from studies.lib.validation import dsr_pbo, cpcv, gates
"""
from __future__ import annotations

from . import (  # noqa: F401
    alpha_halflife,
    benchmark,
    bootstrap,
    changepoint,
    cpcv,
    crisis_alpha,
    dd_duration,
    dsr_pbo,
    flat_max,
    fundamental_law,
    gates,
    haircut,
    kelly,
    mcs,
    metrics,
    reverse_stress,
    sizing_risk,
    spa,
    stepm,
    triple_barrier,
)

__all__ = [
    "alpha_halflife",
    "benchmark",
    "bootstrap",
    "changepoint",
    "cpcv",
    "crisis_alpha",
    "dd_duration",
    "dsr_pbo",
    "flat_max",
    "fundamental_law",
    "gates",
    "haircut",
    "kelly",
    "mcs",
    "metrics",
    "reverse_stress",
    "sizing_risk",
    "spa",
    "stepm",
    "triple_barrier",
]

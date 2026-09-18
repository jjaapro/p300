"""The ORB policy registry: every rule this study may evaluate, fixed before any outcome.

Families (PREREGISTRATION.md section 4):
    P0            the primary rule
    CORE   (23)   3 anchors x 4 range lengths x 2 entries, minus P0
    EXT    (14)   one change to P0 at a time
    INT    (2)    two predetermined interactions
    EXIT   (6)    exit-event addendum (2026-09-15): invalidation, trailing and no-time-exit arms
    ---------------------------------------------------------------
    46 policies in the selection family (P0 included)

CONTROLS and DIAGNOSTICS are never selectable: they answer "what does the breakout add",
"does the clock matter" and "what does execution delay cost".
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace

from orb_engine import Policy

P0 = Policy(id="P0")

CORE = [replace(P0, id=f"CORE_{a}_R{m}_{e.upper()}", anchor=a, range_min=m, entry=e)
        for a in ("NY", "LDN", "UTC") for m in (5, 15, 30, 60) for e in ("close", "stop")
        if not (a == "NY" and m == 15 and e == "close")]

EXT = [
    replace(P0, id="EXT_BUF05", buffer_w=0.05),
    replace(P0, id="EXT_BUF10", buffer_w=0.10),
    replace(P0, id="EXT_GATE", direction_gate=True),
    replace(P0, id="EXT_RVOL10", relvol_min=1.0),
    replace(P0, id="EXT_RVOL15", relvol_min=1.5),
    replace(P0, id="EXT_RVOL20", relvol_min=2.0),
    replace(P0, id="EXT_WIDTH", width_band=True),
    replace(P0, id="EXT_DL60", deadline_min=60),
    replace(P0, id="EXT_DL180", deadline_min=180),
    replace(P0, id="EXT_STOPMID", stop="mid"),
    replace(P0, id="EXT_TGT1R", target_r=1.0),
    replace(P0, id="EXT_TGT2R", target_r=2.0),
    replace(P0, id="EXT_TGT3R", target_r=3.0),
    replace(P0, id="EXT_TX60", time_exit="entry60"),
]

INT = [
    replace(P0, id="INT_RVOL10_TGT2R", relvol_min=1.0, target_r=2.0),
    replace(P0, id="INT_RVOL10_GATE", relvol_min=1.0, direction_gate=True),
]

EXIT = [
    replace(P0, id="X_REENTER1M", exit_event="reenter_1m"),
    replace(P0, id="X_REENTER15M", exit_event="reenter_15m"),
    replace(P0, id="X_NOTIME", time_exit="none"),
    replace(P0, id="X_TRAIL", stop="trail_w"),
    replace(P0, id="X_TRAIL_NOTIME", stop="trail_w", time_exit="none"),
    replace(P0, id="X_VWAP", exit_event="vwap"),
]

FAMILY = [P0, *CORE, *EXT, *INT, *EXIT]

CONTROLS = [
    replace(P0, id="CTL_MOMENTUM", entry="momentum"),
    replace(P0, id="CTL_CLOCK_LONG", entry="clock_long"),
    replace(P0, id="CTL_CLOCK_SHORT", entry="clock_short"),
    replace(P0, id="CTL_FADE", entry="fade"),
    replace(P0, id="CTL_PLACEBO_M120", shift_min=-120),
    replace(P0, id="CTL_PLACEBO_M60", shift_min=-60),
    replace(P0, id="CTL_PLACEBO_P60", shift_min=60),
    replace(P0, id="CTL_PLACEBO_P120", shift_min=120),
]

DIAGNOSTICS = [
    replace(P0, id="DIAG_P0_LAT1", latency_min=1),
    replace(P0, id="DIAG_P0_LAT2", latency_min=2),
]

BY_ID = {p.id: p for p in (*FAMILY, *CONTROLS, *DIAGNOSTICS)}

# Randomized controls are generated from P0's trades (orb_controls.py), not registry rows.
RANDOM_CONTROLS = {"CTL_RANDOM_DIRECTION": 200, "CTL_RANDOM_TIME": 200}
RANDOM_SEED_BASE = 42


def changed_components(p: Policy) -> int:
    """Fields that differ from P0 (id excluded); the selection tie-breaker."""
    a, b = asdict(p), asdict(P0)
    return sum(1 for k in a if k != "id" and a[k] != b[k])


def registry_json() -> str:
    body = {"family": [asdict(p) for p in FAMILY], "controls": [asdict(p) for p in CONTROLS],
            "diagnostics": [asdict(p) for p in DIAGNOSTICS], "random_controls": RANDOM_CONTROLS,
            "random_seed_base": RANDOM_SEED_BASE}
    return json.dumps(body, indent=1, sort_keys=True)


def registry_sha256() -> str:
    return hashlib.sha256(registry_json().encode()).hexdigest()


assert len(FAMILY) == 46 and len({p.id for p in FAMILY}) == 46

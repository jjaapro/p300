#!/usr/bin/env python3
"""Backward-only variant of gen_trades: patches intersect_triggers to the
production-faithful [-24h, 0] window (the P3 audit pattern) and writes to
results_backonly/. This is the pool the tilt-policy ordering must survive
before any prod decision — the bidirectional pool carries selection lookahead.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

import studies.notebooks.overlay_study.gen_trades as gt  # noqa: E402
from studies.notebooks.chento_journal.validation_group_A_tuning_backonly import (  # noqa: E402
    intersect_backward,
)

if __name__ == '__main__':
    gt.intersect_triggers = intersect_backward
    gt.OUT = HERE / 'results_backonly'
    gt.OUT.mkdir(exist_ok=True)
    print('intersect_triggers patched to BACKWARD-ONLY [-24h, 0]')
    for a in ('BTC', 'ETH'):
        gt.gen(a)

"""P2 of the 2026-06-05 lookahead audit: ATR_STOP_MULT × TARGET_R joint
sweep under BACKWARD-ONLY intersect_triggers.

Mirrors validation_target_sweep_5y.py but patches `intersect_triggers` to
backward-only `[-24h, 0]` (matching production CHENTO_TRIPLE_V3
compute_triple_windowed). Audit hypothesis: optimum drifts from
atr5/t6R toward atr4/t5R (or similar). Decision: if a different
(atr_mult, target_r) pair wins on OOS mean R AND MAR with adequate n,
update strategies/sleeves/chento_triple_v3/config.py.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve()
while not (ROOT / 'data' / 'databases' / 'prod.db').exists():
    if ROOT == ROOT.parent:
        raise RuntimeError('locate prod.db')
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / 'studies' / 'material' / 'chento' / 'validation'

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

import studies.notebooks.chento_journal.validation_B_composite as src_comp
import studies.notebooks.chento_journal.validation_target_sweep_5y as src_p2


def intersect_backward(a: pd.DataFrame, b: pd.DataFrame, *,
                       window_hours: float = 24.0) -> pd.DataFrame:
    """Backward-only intersect anchored on `a`'s timestamp."""
    if a.empty or b.empty:
        return pd.DataFrame(columns=a.columns)
    keep = []
    for i, ra in a.iterrows():
        bs = b[b['direction'] == ra['direction']]
        if bs.empty:
            continue
        delta_h = (bs['ts'] - ra['ts']).dt.total_seconds() / 3600.0
        if ((delta_h >= -window_hours) & (delta_h <= 0)).any():
            keep.append(i)
    return a.loc[keep].copy()


def main():
    src_comp.intersect_triggers = intersect_backward
    src_p2.intersect_triggers = intersect_backward
    print('[P2-backonly] intersect_triggers patched to BACKWARD-ONLY [-24h, 0]')

    baseline_path = OUT_DIR / 'target_sweep_5y_results.json'
    backup_path = OUT_DIR / '.target_sweep_5y_results.bidirectional.tmp'
    if baseline_path.exists():
        shutil.copyfile(baseline_path, backup_path)
        print(f'[P2-backonly] backed up baseline JSON to {backup_path.name}')

    try:
        src_p2.main()
    finally:
        produced_path = OUT_DIR / 'target_sweep_5y_results.json'
        target_path = OUT_DIR / 'target_sweep_5y_results_backonly.json'
        if produced_path.exists():
            shutil.move(produced_path, target_path)
            print(f'[P2-backonly] moved output to {target_path.name}')
        if backup_path.exists():
            shutil.move(backup_path, baseline_path)
            print(f'[P2-backonly] restored baseline JSON')


if __name__ == '__main__':
    main()

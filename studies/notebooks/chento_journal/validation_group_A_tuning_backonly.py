"""P3 of the 2026-06-05 lookahead audit: TIF sweep (and the broader
Group A tuning suite) under BACKWARD-ONLY intersect_triggers.

Mirrors validation_group_A_tuning.py but patches `intersect_triggers` to
backward-only `[-24h, 0]` (matching production CHENTO_TRIPLE_V3
compute_triple_windowed). Audit hypothesis: TIF optimum drifts from
72h toward 48h. Decision: if MAR-optimal TIF differs from 72h, update
TIF_HOURS in strategies/sleeves/chento_triple_v3/config.py.
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
import studies.notebooks.chento_journal.validation_group_A_tuning as src_p3


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
    src_p3.intersect_triggers = intersect_backward
    print('[P3-backonly] intersect_triggers patched to BACKWARD-ONLY [-24h, 0]')

    baseline_path = OUT_DIR / 'group_A_tuning_results.json'
    backup_path = OUT_DIR / '.group_A_tuning_results.bidirectional.tmp'
    if baseline_path.exists():
        shutil.copyfile(baseline_path, backup_path)
        print(f'[P3-backonly] backed up baseline JSON to {backup_path.name}')

    try:
        src_p3.main()
    finally:
        produced_path = OUT_DIR / 'group_A_tuning_results.json'
        target_path = OUT_DIR / 'group_A_tuning_results_backonly.json'
        if produced_path.exists():
            shutil.move(produced_path, target_path)
            print(f'[P3-backonly] moved output to {target_path.name}')
        if backup_path.exists():
            shutil.move(backup_path, baseline_path)
            print(f'[P3-backonly] restored baseline JSON')


if __name__ == '__main__':
    main()

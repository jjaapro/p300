"""Run the starter setup library across the 150-coin universe and report
edge per setup.

Usage:
    python studies/notebooks/screener/run_starter_setups.py [--setup S1] [--top-n 50]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve()
while not (_REPO_ROOT / 'data' / 'databases' / 'prod.db').exists():
    if _REPO_ROOT == _REPO_ROOT.parent:
        raise RuntimeError('locate prod.db')
    _REPO_ROOT = _REPO_ROOT.parent
sys.path.insert(0, str(_REPO_ROOT))

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

from studies.lib.screener_runner import run_setup_across_universe
from studies.lib.screener_setups import STARTER_SETUPS

OUT_DIR = _REPO_ROOT / 'studies' / 'material' / 'screener_results'
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--setup', action='append', default=None,
                    help='Setup ID to run (repeatable). Default: all starter setups.')
    ap.add_argument('--top-n', type=int, default=None,
                    help='Limit universe to top-N coins by 24h quote volume.')
    args = ap.parse_args()

    setup_ids = args.setup or list(STARTER_SETUPS.keys())

    summary = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'top_n_universe': args.top_n,
        'setups': {},
    }

    for sid in setup_ids:
        fn = STARTER_SETUPS.get(sid)
        if fn is None:
            print(f'ERROR: unknown setup {sid}'); continue
        print(f'\n===== {sid} =====')
        t0 = datetime.now()
        df, report = run_setup_across_universe(
            fn, sid, top_n=args.top_n)
        elapsed = (datetime.now() - t0).total_seconds()
        print(f'  triggers: {report.n_triggers}  '
               f'({report.triggers_per_year:.1f}/yr)  '
               f'across {report.n_assets} assets  '
               f'(top1 share: {report.asset_concentration_top1_pct:.1%})')
        print(f'  forward-return Sharpe:  '
              + '  '.join(f'{w}d={s:+.2f}' for w, s in report.fwd_ret_sharpes.items()))
        if report.r_mean is not None:
            print(f'  R outcome:  mean={report.r_mean:+.3f}  '
                   f'Sharpe={report.r_sharpe:+.2f}  WR={report.r_win_rate:.0%}')
        if report.stability:
            print(f'  stability:  '
                   f'first_half_R={report.stability.get("first_half_r_mean", 0):+.3f}  '
                   f'second_half_R={report.stability.get("second_half_r_mean", 0):+.3f}  '
                   f'both_positive={report.stability.get("both_positive", False)}')
        print(f'  VERDICT: {report.verdict}'
               + (f' — {", ".join(report.reasons)}' if report.reasons else ''))
        print(f'  ({elapsed:.1f}s)')

        # Persist per-setup ledger + report
        ledger_path = OUT_DIR / f'{sid}_triggers.jsonl'
        with ledger_path.open('w', encoding='utf-8') as fh:
            for rec in df.to_dict(orient='records'):
                rec = dict(rec)
                if 'ts' in rec and hasattr(rec['ts'], 'isoformat'):
                    rec['ts'] = rec['ts'].isoformat()
                fh.write(json.dumps(rec, default=str) + '\n')

        summary['setups'][sid] = asdict(report)

    summary_path = OUT_DIR / 'starter_setups_summary.json'
    with summary_path.open('w', encoding='utf-8') as fh:
        json.dump(summary, fh, indent=2, default=str)
    print(f'\nWrote summary -> {summary_path}')


if __name__ == '__main__':
    main()

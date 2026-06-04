"""validation_B1_diagnostic: isolate B1 triggers that ALIGN with chento's BTC
trades, compare R-outcomes to non-aligned (noise) triggers.

If aligned-subset R/WR is similar to his stats: entries are right, gap is
filtering precision. If aligned-subset R/WR matches the noise subset: our
EXIT logic is also wrong (hold duration / stop placement / target).

Plus: by-chento-trade analysis. For each chento trade, find the nearest
aligned B1 trigger, look at its R outcome, compare to chento's own outcome.
This tells us if we're entering the same setup but exiting it wrong.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve()
while not (ROOT / 'data' / 'databases' / 'prod.db').exists():
    if ROOT == ROOT.parent:
        raise RuntimeError('locate prod.db')
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

CHENTO_TRADES = ROOT / 'studies' / 'material' / 'chento' / 'scan_aggregated' / 'trades.jsonl'
OUT_DIR = ROOT / 'studies' / 'material' / 'chento' / 'validation'

# Reuse B1's signal + trigger + replay code
from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
    load_btc_15m, compute_moneyflow_signal, compute_atr,
    b1_triggers, measure_r_outcomes, summarize_triggers,
)


WINDOW_HOURS = 72  # same as loose alignment


def main():
    print('Loading data...')
    df = load_btc_15m()
    df_enr = compute_moneyflow_signal(df)
    print(f'  BTC 15m: {len(df):,} bars')

    # Use the loose B1 variant since it caught 100% of chento trades
    print('\nGenerating B1 loose triggers (cvd>±0.5, vel<1.0)...')
    trigs = b1_triggers(df_enr, cvd_threshold=0.5, velocity_max=1.0)
    print(f'  {len(trigs)} triggers')
    print('  Measuring R outcomes...')
    trigs = measure_r_outcomes(trigs, df_enr)

    # Load chento BTC trades
    print('\nLoading chento BTC trades...')
    rows = [json.loads(l) for l in CHENTO_TRADES.read_text(encoding='utf-8').splitlines() if l.strip()]
    chento = pd.DataFrame(rows)
    chento['ts'] = pd.to_datetime(chento['first_ts'], utc=True, errors='coerce')
    chento = chento.dropna(subset=['ts'])
    btc = chento[chento['asset'] == 'BTCUSDT'].copy()
    btc = btc[(btc['ts'] >= trigs['ts'].min()) & (btc['ts'] <= trigs['ts'].max())]
    print(f'  {len(btc)} chento BTC trades in trigger window')

    # === Classify each trigger as aligned-with-chento or not ============
    aligned_mask = np.zeros(len(trigs), dtype=bool)
    for ci, c in btc.iterrows():
        same_dir = trigs[trigs['direction'] == c['direction']].copy()
        if same_dir.empty:
            continue
        delta_h = (same_dir['ts'] - c['ts']).dt.total_seconds() / 3600.0
        hits = same_dir[abs(delta_h) <= WINDOW_HOURS].index
        aligned_mask[trigs.index.isin(hits)] = True

    trigs['aligned'] = aligned_mask
    aligned = trigs[trigs['aligned']].copy()
    noise = trigs[~trigs['aligned']].copy()
    print(f'\nTriggers aligned with at least one chento trade (same-dir, ±{WINDOW_HOURS}h): '
          f'{aligned_mask.sum()}/{len(trigs)} = {aligned_mask.mean():.1%}')

    # === Compare R/WR ====================================================
    print('\n=== R-outcome comparison: aligned-with-chento vs noise ===')
    for label, sub in (('ALIGNED', aligned), ('NOISE', noise), ('ALL', trigs)):
        if sub.empty:
            print(f'  {label}: empty')
            continue
        s = summarize_triggers(sub, label=label)
        r_vals = sub['r_outcome'].dropna()
        print(f'  {label:<8s}: n={s["n"]:<5d}  mean R={s["mean_R"]:+.3f}  '
               f'median={s["median_R"]:+.3f}  WR={s["win_rate"]:.0%}  '
               f'targets={s["exit_kinds"].get("target",0)}  '
               f'stops={s["exit_kinds"].get("stop",0)}  '
               f'tif={s["exit_kinds"].get("tif",0)}')
        # Direction breakdown
        for d in ('long', 'short'):
            ds = sub[sub['direction'] == d]
            if not ds.empty:
                rs = ds['r_outcome'].dropna()
                print(f'      {d}: n={len(ds)} mean R={rs.mean():+.3f} WR={(rs>0).mean():.0%}')

    # === Per-chento-trade view: for each chento trade, what is OUR closest
    # aligned trigger's outcome? Then compare to his outcome.
    print('\n=== Per-chento-trade outcome comparison ===')
    print(f'(For each chento trade, look at our closest aligned same-dir trigger)')
    paired_rows = []
    for ci, c in btc.iterrows():
        same_dir = aligned[aligned['direction'] == c['direction']].copy()
        if same_dir.empty:
            continue
        delta_h = (same_dir['ts'] - c['ts']).dt.total_seconds() / 3600.0
        in_window = same_dir[abs(delta_h) <= WINDOW_HOURS]
        if in_window.empty:
            continue
        # closest by abs time
        in_window = in_window.copy()
        in_window['abs_dh'] = abs((in_window['ts'] - c['ts']).dt.total_seconds() / 3600.0)
        closest = in_window.loc[in_window['abs_dh'].idxmin()]
        paired_rows.append({
            'chento_msg_id': c.get('first_msg_id'),
            'chento_ts': c['ts'].isoformat(),
            'chento_direction': c['direction'],
            'chento_entry': c.get('entry_first'),
            'chento_best_pnl_pct': c.get('best_pnl_pct'),
            'chento_worst_pnl_pct': c.get('worst_pnl_pct'),
            'chento_last_pnl_pct': c.get('last_pnl_pct'),
            'chento_n_snap': c.get('n_snapshots'),
            'chento_duration_h': c.get('duration_hours'),
            'bot_trigger_ts': closest['ts'].isoformat(),
            'bot_entry': closest['entry'],
            'bot_stop': closest['stop'],
            'bot_target': closest['target'],
            'bot_r_outcome': closest['r_outcome'],
            'bot_exit_kind': closest['exit_kind'],
            'bot_hold_h': closest['hold_hours'],
            'time_delta_h': float((closest['ts'] - c['ts']).total_seconds() / 3600.0),
        })
    paired = pd.DataFrame(paired_rows)
    print(f'  Paired chento <-> bot rows: {len(paired)}')

    if len(paired) > 0:
        bot_r = paired['bot_r_outcome'].dropna()
        print(f'\n  Bot R outcomes on chento-aligned trades:')
        print(f'    mean R: {bot_r.mean():+.3f}')
        print(f'    median R: {bot_r.median():+.3f}')
        print(f'    win rate (R>0): {(bot_r>0).mean():.1%}')
        print(f'    target hits: {(paired["bot_exit_kind"]=="target").sum()}')
        print(f'    stop hits:   {(paired["bot_exit_kind"]=="stop").sum()}')
        print(f'    tif:         {(paired["bot_exit_kind"]=="tif").sum()}')
        print(f'    median hold h: {paired["bot_hold_h"].median():.1f}')

        # Chento-side
        chento_outcomes = paired['chento_last_pnl_pct'].dropna()
        chento_best = paired['chento_best_pnl_pct'].dropna()
        print(f'\n  Chento outcomes on same trades:')
        print(f'    chento mean last_pnl_pct: {chento_outcomes.mean():+.2f}%')
        print(f'    chento mean best_pnl_pct (MFE): {chento_best.mean():+.2f}%')
        print(f'    chento win rate (last>0): {(chento_outcomes>0).mean():.1%}')
        print(f'    chento median duration_h: {paired["chento_duration_h"].dropna().median():.1f}h')

        # Categorization: where do we both win? Both lose? We lose he wins? etc.
        bw = (paired['bot_r_outcome'] > 0)
        cw = (paired['chento_last_pnl_pct'] > 0)
        valid = paired['bot_r_outcome'].notna() & paired['chento_last_pnl_pct'].notna()
        v = paired[valid]
        print(f'\n  Win/loss matrix (valid pairs n={len(v)}):')
        print(f'    bot WIN + chento WIN:   {((v["bot_r_outcome"]>0) & (v["chento_last_pnl_pct"]>0)).sum()}')
        print(f'    bot WIN + chento LOSS:  {((v["bot_r_outcome"]>0) & (v["chento_last_pnl_pct"]<=0)).sum()}')
        print(f'    bot LOSS + chento WIN:  {((v["bot_r_outcome"]<=0) & (v["chento_last_pnl_pct"]>0)).sum()}')
        print(f'    bot LOSS + chento LOSS: {((v["bot_r_outcome"]<=0) & (v["chento_last_pnl_pct"]<=0)).sum()}')

        # Trades where chento WON but we LOST — what happened?
        print('\n  Sample: chento WON but bot LOST (bot stopped out before chento\'s win):')
        bot_loss_chento_win = v[(v['bot_r_outcome'] < 0) & (v['chento_last_pnl_pct'] > 0)].head(10)
        for _, r in bot_loss_chento_win.iterrows():
            print(f'    {r["chento_ts"]} {r["chento_direction"]:<5s}  '
                   f'bot R={r["bot_r_outcome"]:+.2f} ({r["bot_exit_kind"]}, hold {r["bot_hold_h"]:.0f}h)  '
                   f'chento last={r["chento_last_pnl_pct"]:+.1f}% best={r["chento_best_pnl_pct"]:+.1f}% dur={r["chento_duration_h"]:.0f}h')

        # Save full pairs to JSONL
        pairs_out = OUT_DIR / 'B1_diagnostic_paired.jsonl'
        with pairs_out.open('w', encoding='utf-8') as fh:
            for r in paired.to_dict(orient='records'):
                fh.write(json.dumps(r, default=str) + '\n')
        print(f'\nWrote {pairs_out}')

    # === Summary JSON ===
    summary = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'window_hours': WINDOW_HOURS,
        'study_question': (
            'If B1 catches 100% of chento timestamps but per-trigger R is bad, '
            'is the issue (a) the 98% noise diluting good triggers, or (b) the '
            'aligned triggers themselves having bad R outcomes? '
            'If (a) -> filter problem. If (b) -> entry-AND-exit problem.'
        ),
        'n_triggers_total': int(len(trigs)),
        'n_aligned': int(aligned_mask.sum()),
        'aligned_rate': round(float(aligned_mask.mean()), 4),
        'aligned_summary': summarize_triggers(aligned, 'aligned') if not aligned.empty else {},
        'noise_summary': summarize_triggers(noise, 'noise') if not noise.empty else {},
        'all_summary': summarize_triggers(trigs, 'all'),
    }
    out_path = OUT_DIR / 'B1_diagnostic_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump(summary, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

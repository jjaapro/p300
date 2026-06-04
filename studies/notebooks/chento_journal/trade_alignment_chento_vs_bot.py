"""trade_alignment_chento_vs_bot: align his observed trades against our
backtest's triggered trades to understand the frequency + coverage gap.

For each of chento's lifecycles (from scan_aggregated/trades.jsonl):
  - Did our v2 baseline backtest fire on the same asset within ±N hours?
  - If yes: how do entry prices compare?
  - If no: why? (asset we don't trade / outside time gate / failed MTF gate / failed confluence / no swing-base detected)

Also reverse direction:
  - For each of our backtest's triggered trades: did chento have a documented
    trade in the same window? (Discovery rate from his side.)

Output: studies/material/chento/validation/chento_vs_bot_alignment.json + .md report
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

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
BOT_LONG_LEDGER = ROOT / 'studies' / 'material' / 'chento' / 'validation' / 'A_baseline_ledger.jsonl'
# Short entries are in the cache; we load both directly
CACHE_DIR = ROOT / 'studies' / 'material' / 'chento' / 'validation' / 'cache'
OUT_JSON = ROOT / 'studies' / 'material' / 'chento' / 'validation' / 'chento_vs_bot_alignment.json'
OUT_MD = ROOT / 'studies' / 'material' / 'chento' / 'validation' / 'chento_vs_bot_alignment.md'


# Normalize asset names: chento corpus uses 'BTCUSDT', our bot uses 'BTC'
ASSET_MAP = {
    'BTCUSDT': 'BTC', 'ETHUSDT': 'ETH', 'OPUSDT': 'OP',
    'SOLUSDT': 'SOL', 'RUNEUSDT': 'RUNE',
}

WINDOW_HOURS_TIGHT = 24
WINDOW_HOURS_LOOSE = 72


def _load_chento_trades() -> pd.DataFrame:
    rows = [json.loads(l) for l in CHENTO_TRADES.read_text(encoding='utf-8').splitlines() if l.strip()]
    df = pd.DataFrame(rows)
    df['ts'] = pd.to_datetime(df['first_ts'], utc=True, errors='coerce')
    df = df.dropna(subset=['ts'])
    df['asset_norm'] = df['asset'].map(ASSET_MAP).fillna(df['asset'])
    return df


def _load_bot_trades() -> pd.DataFrame:
    """Load combined long+short entries from cache. Both directions for dual-direction
    chento alignment (BTC+OP only per current ruleset)."""
    # Find current cache_key from a long-side filename
    long_files = sorted(CACHE_DIR.glob('entries_BTC_*.jsonl'))
    long_files = [p for p in long_files if '_SHORT_' not in p.name]
    if not long_files:
        raise RuntimeError(f'no entry cache found in {CACHE_DIR}')
    # Take the most recent (single cache_key in practice)
    cache_key = long_files[-1].stem.split('_')[-1]
    print(f'  using cache_key {cache_key}')

    all_rows = []
    for asset in ('BTC', 'OP'):
        # Long
        long_path = CACHE_DIR / f'entries_{asset}_{cache_key}.jsonl'
        if long_path.exists():
            rows = [json.loads(l) for l in long_path.read_text(encoding='utf-8').splitlines() if l.strip()]
            for r in rows:
                r['direction'] = 'long'
            all_rows.extend(rows)
        # Short
        short_path = CACHE_DIR / f'entries_{asset}_SHORT_{cache_key}.jsonl'
        if short_path.exists():
            rows = [json.loads(l) for l in short_path.read_text(encoding='utf-8').splitlines() if l.strip()]
            for r in rows:
                r['direction'] = 'short'
            all_rows.extend(rows)

    df = pd.DataFrame(all_rows)
    df['ts'] = pd.to_datetime(df['now_ts'], utc=True)
    return df


def _within_window(t_chento, bot_ts_array, hours: float):
    """Return mask of bot trades within ±hours of t_chento. Handles numpy
    datetime64 array via pandas Timestamp / Timedelta arithmetic."""
    import numpy as np
    if len(bot_ts_array) == 0:
        return np.array([], dtype=bool)
    bot_pd = pd.to_datetime(bot_ts_array, utc=True)
    delta_h = (bot_pd - t_chento).total_seconds() / 3600.0
    return abs(delta_h.values) <= hours


def main():
    print(f'Loading chento trades from {CHENTO_TRADES}')
    chento = _load_chento_trades()
    print(f'  {len(chento)} chento lifecycles')
    print(f'\nLoading bot trades from cache (dual-direction)...')
    bot = _load_bot_trades()
    print(f'  {len(bot)} bot entries total ({(bot["direction"]=="long").sum()} long + '
          f'{(bot["direction"]=="short").sum()} short)')

    print(f'\nChento by asset:')
    for a, n in chento['asset_norm'].value_counts().items():
        print(f'  {a}: {n}')
    print(f'\nBot by asset:')
    for a, n in bot['asset'].value_counts().items():
        print(f'  {a}: {n}')

    # === Asset overlap ====================================================
    # Restrict to the 2 assets we backtest under the new chento ruleset
    common_assets = ('BTC', 'OP')
    chento_common = chento[chento['asset_norm'].isin(common_assets)].copy()
    print(f'\nChento trades on common assets (BTC/ETH/OP): {len(chento_common)}')

    # === Time overlap (he traded ETH/OP in periods we have data) =========
    # Bot trade timestamps cover 2022-01 → 2026-05 (BTC) and 2020-01 → 2026-05 (ETH)
    # Chento trades cover 2024-06 → 2026-05
    # Restrict both sides to chento's window for the alignment
    chento_min_ts = chento['ts'].min()
    chento_max_ts = chento['ts'].max()
    bot_in_chento_window = bot[(bot['ts'] >= chento_min_ts)
                                & (bot['ts'] <= chento_max_ts)].copy()
    print(f'Bot trades in chento window ({chento_min_ts} -> {chento_max_ts}): '
          f'{len(bot_in_chento_window)}')
    chento_in_bot_window = chento_common[
        (chento_common['ts'] >= bot['ts'].min())
        & (chento_common['ts'] <= bot['ts'].max())].copy()
    print(f'Chento (BTC/ETH/OP) in bot window: {len(chento_in_bot_window)}')

    # === ALIGNMENT 1: For each chento trade, did the bot fire in the same window? ==
    print('\n=== Alignment 1: chento -> bot coverage ===')
    print(f'(tight = ±{WINDOW_HOURS_TIGHT}h, loose = ±{WINDOW_HOURS_LOOSE}h)')
    matched_tight = 0
    matched_loose = 0
    per_asset_total = Counter()
    per_asset_matched_tight = Counter()
    per_asset_matched_loose = Counter()
    miss_examples = []

    for _, chento_row in chento_in_bot_window.iterrows():
        asset = chento_row['asset_norm']
        ts = chento_row['ts']
        bot_same_asset = bot_in_chento_window[bot_in_chento_window['asset'] == asset]
        per_asset_total[asset] += 1
        tight_hits = _within_window(ts, bot_same_asset['ts'].values, WINDOW_HOURS_TIGHT)
        loose_hits = _within_window(ts, bot_same_asset['ts'].values, WINDOW_HOURS_LOOSE)
        if tight_hits.any():
            matched_tight += 1
            per_asset_matched_tight[asset] += 1
        if loose_hits.any():
            matched_loose += 1
            per_asset_matched_loose[asset] += 1
        elif len(miss_examples) < 20:
            miss_examples.append({
                'msg_id': chento_row.get('first_msg_id'),
                'asset': asset,
                'direction': chento_row['direction'],
                'ts': ts.isoformat(),
                'entry_first': chento_row['entry_first'],
                'last_pnl_pct': chento_row.get('last_pnl_pct'),
            })

    print(f'\nChento trades within ±{WINDOW_HOURS_TIGHT}h of a bot trigger: '
          f'{matched_tight}/{len(chento_in_bot_window)} = '
          f'{matched_tight/len(chento_in_bot_window):.1%}')
    print(f'Chento trades within ±{WINDOW_HOURS_LOOSE}h: '
          f'{matched_loose}/{len(chento_in_bot_window)} = '
          f'{matched_loose/len(chento_in_bot_window):.1%}')
    print('\nPer-asset coverage (tight / loose / total):')
    for asset in sorted(per_asset_total.keys()):
        tot = per_asset_total[asset]
        tight = per_asset_matched_tight[asset]
        loose = per_asset_matched_loose[asset]
        print(f'  {asset}: {tight}/{tot} tight ({tight/tot:.0%}), '
              f'{loose}/{tot} loose ({loose/tot:.0%})')

    # === ALIGNMENT 2: For each bot trade, did chento have one in the same window? =
    print('\n=== Alignment 2: bot -> chento confirmation rate ===')
    bot_matched_tight = 0
    bot_matched_loose = 0
    bot_per_asset_total = Counter()
    bot_per_asset_matched = Counter()
    for _, bot_row in bot_in_chento_window.iterrows():
        asset = bot_row['asset']
        ts = bot_row['ts']
        chento_same = chento_in_bot_window[chento_in_bot_window['asset_norm'] == asset]
        bot_per_asset_total[asset] += 1
        tight_hits = _within_window(ts, chento_same['ts'].values, WINDOW_HOURS_TIGHT)
        loose_hits = _within_window(ts, chento_same['ts'].values, WINDOW_HOURS_LOOSE)
        if tight_hits.any():
            bot_matched_tight += 1
            bot_per_asset_matched[asset] += 1
        if loose_hits.any():
            bot_matched_loose += 1

    print(f'\nBot trades with a chento trade within ±{WINDOW_HOURS_TIGHT}h: '
          f'{bot_matched_tight}/{len(bot_in_chento_window)} = '
          f'{bot_matched_tight/max(len(bot_in_chento_window),1):.1%}')
    print(f'Bot trades within ±{WINDOW_HOURS_LOOSE}h: '
          f'{bot_matched_loose}/{len(bot_in_chento_window)} = '
          f'{bot_matched_loose/max(len(bot_in_chento_window),1):.1%}')

    # === ALIGNMENT 3: same-direction match for overlapping trades ==========
    print('\n=== Alignment 3: same-direction match for overlapping trades ===')
    same_dir_tight = 0
    same_dir_loose = 0
    any_dir_loose = 0
    for _, chento_row in chento_in_bot_window.iterrows():
        asset = chento_row['asset_norm']
        ts = chento_row['ts']
        direction = chento_row['direction']
        bot_same_asset = bot_in_chento_window[bot_in_chento_window['asset'] == asset]
        bot_same_dir = bot_same_asset[bot_same_asset['direction'] == direction]
        if _within_window(ts, bot_same_dir['ts'].values, WINDOW_HOURS_TIGHT).any():
            same_dir_tight += 1
        if _within_window(ts, bot_same_dir['ts'].values, WINDOW_HOURS_LOOSE).any():
            same_dir_loose += 1
        if _within_window(ts, bot_same_asset['ts'].values, WINDOW_HOURS_LOOSE).any():
            any_dir_loose += 1
    n_chento = len(chento_in_bot_window)
    print(f'Same-asset + same-direction tight (±{WINDOW_HOURS_TIGHT}h): {same_dir_tight}/{n_chento} = {same_dir_tight/max(n_chento,1):.1%}')
    print(f'Same-asset + same-direction loose (±{WINDOW_HOURS_LOOSE}h): {same_dir_loose}/{n_chento} = {same_dir_loose/max(n_chento,1):.1%}')
    print(f'Same-asset (any direction) loose: {any_dir_loose}/{n_chento} = {any_dir_loose/max(n_chento,1):.1%}')

    # Per-direction breakdown
    print('\nBy chento direction:')
    for dirn in ('long', 'short'):
        sub = chento_in_bot_window[chento_in_bot_window['direction'] == dirn]
        if len(sub) == 0:
            continue
        same_tight = sum(_within_window(r['ts'],
            bot_in_chento_window[(bot_in_chento_window['asset']==r['asset_norm']) & (bot_in_chento_window['direction']==dirn)]['ts'].values,
            WINDOW_HOURS_TIGHT).any() for _, r in sub.iterrows())
        same_loose = sum(_within_window(r['ts'],
            bot_in_chento_window[(bot_in_chento_window['asset']==r['asset_norm']) & (bot_in_chento_window['direction']==dirn)]['ts'].values,
            WINDOW_HOURS_LOOSE).any() for _, r in sub.iterrows())
        print(f'  chento {dirn} (n={len(sub)}): same-dir tight={same_tight}/{len(sub)} ({same_tight/len(sub):.0%}),  loose={same_loose}/{len(sub)} ({same_loose/len(sub):.0%})')

    # === Diagnostic: why did we miss the chento trades we missed? ==========
    print('\n=== Why we missed: assets and direction breakdown ===')
    missed = chento_in_bot_window.copy()
    missed['was_matched_loose'] = False
    for idx, chento_row in missed.iterrows():
        bot_same = bot_in_chento_window[bot_in_chento_window['asset'] == chento_row['asset_norm']]
        loose_hits = _within_window(chento_row['ts'], bot_same['ts'].values,
                                      WINDOW_HOURS_LOOSE)
        if loose_hits.any():
            missed.loc[idx, 'was_matched_loose'] = True
    truly_missed = missed[~missed['was_matched_loose']].copy()
    print(f'Total chento trades we missed (loose window): {len(truly_missed)}')
    print(f'\nMisses by direction:')
    for d, n in truly_missed['direction'].value_counts().items():
        print(f'  {d}: {n}')
    print(f'\nMisses by asset:')
    for a, n in truly_missed['asset_norm'].value_counts().items():
        print(f'  {a}: {n}')

    # === Chento trades on assets we DON'T backtest ===============
    chento_unsupported = chento[~chento['asset_norm'].isin(common_assets)]
    print(f'\nChento trades on assets outside BTC/ETH/OP universe: '
          f'{len(chento_unsupported)}')
    for a, n in chento_unsupported['asset_norm'].value_counts().items():
        print(f'  {a}: {n}')

    # === Write outputs ====================================================
    result = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'window_tight_hours': WINDOW_HOURS_TIGHT,
        'window_loose_hours': WINDOW_HOURS_LOOSE,
        'common_assets': list(common_assets),
        'totals': {
            'chento_lifecycles_total': len(chento),
            'chento_on_common_assets': len(chento_common),
            'chento_in_bot_window': len(chento_in_bot_window),
            'bot_baseline_entries': len(bot),
            'bot_in_chento_window': len(bot_in_chento_window),
        },
        'chento_to_bot_coverage': {
            'matched_tight': matched_tight,
            'matched_loose': matched_loose,
            'rate_tight': round(matched_tight / max(len(chento_in_bot_window), 1), 3),
            'rate_loose': round(matched_loose / max(len(chento_in_bot_window), 1), 3),
            'per_asset_tight': {a: f'{per_asset_matched_tight[a]}/{per_asset_total[a]}'
                                  for a in per_asset_total},
            'per_asset_loose': {a: f'{per_asset_matched_loose[a]}/{per_asset_total[a]}'
                                  for a in per_asset_total},
        },
        'bot_to_chento_confirmation': {
            'matched_tight': bot_matched_tight,
            'matched_loose': bot_matched_loose,
            'rate_tight': round(bot_matched_tight / max(len(bot_in_chento_window), 1), 3),
            'rate_loose': round(bot_matched_loose / max(len(bot_in_chento_window), 1), 3),
        },
        'direction_breakdown_of_misses': {
            'short_misses_long_only_baseline': short_match_we_missed,
            'long_misses_other_reasons': int(truly_missed[truly_missed['direction']=='long'].shape[0]),
        },
        'chento_unsupported_assets': {
            a: int(n) for a, n in chento_unsupported['asset_norm'].value_counts().items()
        },
        'miss_examples_first_20': miss_examples,
    }
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
    print(f'\nWrote {OUT_JSON}')


if __name__ == '__main__':
    main()

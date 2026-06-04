"""Dedup pass on phase1_trades.jsonl: group screenshots into unique trade lifecycles."""
import json, sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')

P = Path('c:/Source/Repos/p300/studies/material/chento/phase1_trades.jsonl')
records = [json.loads(l) for l in P.read_text(encoding='utf-8').splitlines() if l.strip()]
print(f'total records: {len(records)}')

by_type = defaultdict(int)
for r in records: by_type[r.get('screenshot_type','?')] += 1
print('\nby screenshot_type:'); [print(f'  {k}: {v}') for k,v in sorted(by_type.items(), key=lambda kv:-kv[1])]

# Group by (asset, direction, entry_price) — same tuple = same underlying trade
trades = defaultdict(list)
for r in records:
    if r.get('screenshot_type') != 'exchange_position_card': continue
    key = (r.get('asset'), r.get('direction'), r.get('entry_price'))
    trades[key].append(r)
print(f'\n{len(trades)} unique (asset, direction, entry_price) tuples')

# For each, show: asset, direction, entry, leverage, first_ts, last_ts, n_screenshots
print('\n=== Unique trade lifecycles ===')
print(f'{"asset":10s} {"side":>5s} {"lev":>4s} {"entry":>10s}  {"first":>16s} {"last":>16s} {"n":>3s}  TP')
trades_sorted = sorted(trades.items(), key=lambda kv: kv[1][0]['ts'])
for key, scs in trades_sorted:
    asset, direction, entry = key
    scs.sort(key=lambda r: r['ts'])
    first_ts = scs[0]['ts'][:16]; last_ts = scs[-1]['ts'][:16]
    lev = scs[0].get('leverage', '?')
    tp = scs[-1].get('tp_price') or '-'
    n = len(scs)
    print(f'{asset:10s} {direction:>5s} {lev:>3}x {entry:>10}  {first_ts:>16s} {last_ts:>16s} {n:>3d}  TP={tp}')

# Trades by asset
asset_count = defaultdict(int)
for key in trades: asset_count[key[0]] += 1
print(f'\nby asset:')
for a, c in sorted(asset_count.items(), key=lambda kv: -kv[1]):
    print(f'  {a}: {c} unique trades')

# Leverage distribution
lev_count = defaultdict(int)
for key, scs in trades.items(): lev_count[scs[0].get('leverage','?')] += 1
print(f'\nleverage distribution (unique trades):')
for lev in sorted(lev_count.keys(), key=lambda k: int(k) if isinstance(k,int) else 99):
    print(f'  {lev}x: {lev_count[lev]}')

# Hold-duration distribution
print(f'\ntrade durations (screenshot timestamps):')
short = mid = long_ = 0
for key, scs in trades.items():
    if len(scs)<2: continue
    dur_h = (datetime.fromisoformat(scs[-1]['ts']) - datetime.fromisoformat(scs[0]['ts'])).total_seconds()/3600
    if dur_h < 4: short += 1
    elif dur_h < 48: mid += 1
    else: long_ += 1
print(f'  <4h:   {short}')
print(f'  4-48h: {mid}')
print(f'  >48h:  {long_}  <- position trades')

# Account-balance checkpoints
print(f'\n=== Account-balance checkpoints ===')
balances = [r for r in records if r.get('screenshot_type') == 'account_balance']
for b in sorted(balances, key=lambda r: r['ts']):
    print(f"  {b['ts'][:16]}  ${b.get('total_assets_usdt'):>8,.0f}  (futures ${b.get('futures_value_usdt'):>8,.0f})")

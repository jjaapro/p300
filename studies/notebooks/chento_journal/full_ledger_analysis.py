"""Combined analysis across all extractions to date.

Sources:
  phase1_trades.jsonl   — 2024-Q2/Q3 mobile-card extractions (29 unique trades)
  scan_extractions.jsonl — 2026-Q1 chart-era extractions (~15 trades + close logs)

Output: per-trade ledger with R-equivalent outcomes + realized PnL log.
"""
import json, sys
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path('c:/Source/Repos/p300')

phase1 = [json.loads(l) for l in (ROOT/'studies/material/chento/phase1_trades.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
scan = [json.loads(l) for l in (ROOT/'studies/material/chento/scan_extractions.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
all_records = phase1 + scan
print(f'phase1 records: {len(phase1)}')
print(f'scan records:   {len(scan)}')
print(f'total records:  {len(all_records)}\n')

# Categorize
types = Counter(r.get('screenshot_type') for r in all_records)
print('by screenshot_type:')
for t, n in types.most_common():
    print(f'  {t}: {n}')

# Realized PnL events (close logs)
realized = [r for r in all_records if 'realized_pnl_usdt' in r and r.get('screenshot_type') in
            ('order_history_line', 'order_history', 'stream_dashboard')]
realized.sort(key=lambda r: r['ts'])
print(f'\nRealized PnL events: {len(realized)}')
for r in realized:
    pnl = r.get('realized_pnl_usdt') or r.get('realized_pnl_usdt_total') or 0
    print(f"  {r['ts'][:16]}  ${pnl:>+10,.2f}  bal={r.get('account_balance_usdt') or 'n/a'}")

print()

# Position cards (live snapshots) — dedup to unique trades
cards = [r for r in all_records if r.get('screenshot_type') == 'exchange_position_card']
print(f'Position-card snapshots: {len(cards)}')
cards.sort(key=lambda r: r['ts'])

def same_trade(a, b):
    if a.get('asset') != b.get('asset'): return False
    if a.get('direction') != b.get('direction'): return False
    ea, eb = a.get('entry_price'), b.get('entry_price')
    if ea is None or eb is None: return False
    if abs(ea - eb) / ea > 0.005: return False
    if (datetime.fromisoformat(b['ts']) - datetime.fromisoformat(a['ts'])).total_seconds() > 14*86400: return False
    return True

# also filter cards to only those with entry_price
cards = [r for r in cards if r.get('entry_price') is not None]
print(f'cards with entry_price: {len(cards)}')

groups = []; cur = []
for r in cards:
    if cur and same_trade(cur[-1], r): cur.append(r)
    else:
        if cur: groups.append(cur)
        cur = [r]
if cur: groups.append(cur)
print(f'Unique trade lifecycles: {len(groups)}')

# Build per-trade ledger
ledger = []
for g in groups:
    first, last = g[0], g[-1]
    pnl_pct = last.get('unrealized_pnl_pct') or 0
    leverage = first.get('leverage', 20) or 20
    stop_pct = 2.0  # assumed
    # R = pnl_pct / (stop_pct * leverage)
    r_value = pnl_pct / (stop_pct * leverage)
    ledger.append({
        'ts': first['ts'][:10],
        'asset': first['asset'],
        'direction': first['direction'],
        'leverage': leverage,
        'entry': first['entry_price'],
        'last_pnl_pct': pnl_pct,
        'r_equiv': r_value,
        'tp_set': bool(last.get('tp_price') or first.get('tp_price')),
        'sl_set': bool(last.get('sl_price') or first.get('sl_price')),
        'n_snapshots': len(g),
    })

print(f'\n=== Per-trade ledger ({len(ledger)} unique trades) ===')
import pandas as pd
df = pd.DataFrame(ledger)
print(df.to_string(index=False))

# Stats
print(f'\n=== Stats (apparent, survivorship-biased) ===')
print(f'Win rate (R > 0):       {(df["r_equiv"] > 0).mean():.1%}')
print(f'Mean R per trade:       {df["r_equiv"].mean():+.3f}')
print(f'Median R per trade:     {df["r_equiv"].median():+.3f}')
print(f'Top 5 R values:         {sorted(df["r_equiv"], reverse=True)[:5]}')
print(f'Bottom 5 R values:      {sorted(df["r_equiv"])[:5]}')

# TP/SL discipline
tp_set_rate = df['tp_set'].mean()
sl_set_rate = df['sl_set'].mean()
print(f'\nTP set on platform:     {tp_set_rate:.1%}')
print(f'SL set on platform:     {sl_set_rate:.1%}')

# Leverage distribution
print(f'\nLeverage distribution:')
for lev, n in df['leverage'].value_counts().sort_index().items():
    print(f'  {lev}x: {n}')

# Realized PnL aggregate
total_realized = sum((r.get('realized_pnl_usdt') or r.get('realized_pnl_usdt_total') or 0) for r in realized)
print(f'\n=== Realized PnL aggregate ===')
print(f'Total realized in {len(realized)} close events: ${total_realized:,.2f}')
# Date range of realized events
if realized:
    rdates = [datetime.fromisoformat(r['ts']) for r in realized]
    span_days = (max(rdates) - min(rdates)).days
    if span_days > 0:
        print(f'Span: {span_days} days  ({total_realized/span_days:,.0f}/day avg)')
        print(f'Annualized realized rate: ${total_realized * 365 / span_days:,.0f}/year')

"""Aggregate scan_full.jsonl (890 records) into:
  1. Canonical trade ledger (group by trade_link.trade_signature, take final snapshot per trade)
  2. Per-fill realized PnL log (every Realized PnL field across order_history / order_details)
  3. Discretionary signal taxonomy (action_type + reason_keywords + market_state counts)
  4. WR three ways: per-fill, per-trade-lifecycle, per-era
  5. Exchange / leverage / direction distribution
  6. Codifiability assessment

Outputs: prints to stdout + writes:
  studies/material/chento/scan_aggregated/trades.jsonl
  studies/material/chento/scan_aggregated/realized_pnl_events.jsonl
  studies/material/chento/scan_aggregated/signal_taxonomy.json
  studies/material/chento/scan_aggregated/summary.md
"""
from __future__ import annotations

import json, sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict, Counter

sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path('c:/Source/Repos/p300')
SCAN = ROOT / 'studies' / 'material' / 'chento' / 'scan_full.jsonl'
OUT_DIR = ROOT / 'studies' / 'material' / 'chento' / 'scan_aggregated'
OUT_DIR.mkdir(parents=True, exist_ok=True)

records = [json.loads(l) for l in SCAN.read_text(encoding='utf-8').splitlines() if l.strip()]
print(f'loaded {len(records):,} records from {SCAN.name}')

# === 1. Canonical trade ledger ===
# Group by trade_link.trade_signature when available, else (asset, direction, round(entry_price, 0))
def trade_key(r: dict) -> str | None:
    tl = r.get('trade_link') or {}
    sig = tl.get('trade_signature')
    if sig:
        return sig
    pc = r.get('position_card') or {}
    asset = pc.get('asset')
    direction = pc.get('direction')
    entry = pc.get('entry_price')
    if asset and direction and entry:
        # round entry to nearest 100 USD to dedupe near-identical entries
        return f'{asset}_{direction}_{round(entry, -2):.0f}'
    return None

trades = defaultdict(list)
for r in records:
    if (r.get('screenshot_type') or '').startswith('exchange_position_card'):
        k = trade_key(r)
        if k:
            trades[k].append(r)

# For each trade, sort snapshots by ts; build a lifecycle record
ledger = []
for sig, snaps in trades.items():
    snaps.sort(key=lambda r: r['ts_utc'])
    first = snaps[0]; last = snaps[-1]
    pc_first = first.get('position_card') or {}
    pc_last  = last.get('position_card')  or {}
    # Compute lifecycle duration
    dur_h = (datetime.fromisoformat(last['ts_utc']) - datetime.fromisoformat(first['ts_utc'])).total_seconds() / 3600
    # Best PnL across snapshots
    pnls = [(s.get('position_card') or {}).get('unrealized_pnl_pct') for s in snaps]
    pnls = [p for p in pnls if p is not None]
    best_pnl = max(pnls) if pnls else None
    worst_pnl = min(pnls) if pnls else None
    last_pnl = pc_last.get('unrealized_pnl_pct')

    # Did the trade have a partial close / SL_BE move based on discretionary_signal blocks?
    actions = []
    for s in snaps:
        ds = s.get('discretionary_signal') or {}
        if ds.get('action_type'):
            actions.append({
                'ts': s['ts_utc'], 'type': ds['action_type'],
                'pct': ds.get('action_pct'),
                'reasons': ds.get('action_reason_keywords') or []
            })

    # Did it close (we don't always see the close fill)
    closed_card = last.get('screenshot_type') == 'exchange_position_card_closed'
    realized = pc_last.get('realized_pnl_usdt_closed') if closed_card else pc_last.get('realized_pnl_usdt')

    ledger.append({
        'trade_signature': sig,
        'first_ts': first['ts_utc'][:16],
        'last_ts':  last['ts_utc'][:16],
        'asset': pc_first.get('asset'),
        'direction': pc_first.get('direction'),
        'exchange': pc_first.get('exchange'),
        'leverage_first': pc_first.get('leverage'),
        'leverage_last':  pc_last.get('leverage'),
        'entry_first': pc_first.get('entry_price'),
        'entry_last':  pc_last.get('entry_price'),
        'margin_first': pc_first.get('margin_usdt'),
        'margin_last':  pc_last.get('margin_usdt'),
        'best_pnl_pct':  best_pnl,
        'worst_pnl_pct': worst_pnl,
        'last_pnl_pct':  last_pnl,
        'realized_on_close_usdt': realized,
        'n_snapshots': len(snaps),
        'duration_hours': round(dur_h, 1),
        'closed': closed_card,
        'actions': actions,
        'first_msg_id': first['message_id'],
    })

ledger.sort(key=lambda r: r['first_ts'])

print(f'\n=== Canonical trade ledger ===')
print(f'unique trade lifecycles: {len(ledger)}')
print(f'  with ≥2 snapshots: {sum(1 for r in ledger if r["n_snapshots"] >= 2)}')
print(f'  with explicit close: {sum(1 for r in ledger if r["closed"])}')
print(f'  with discretionary actions logged: {sum(1 for r in ledger if r["actions"])}')

# === 2. Realized PnL events ===
realized_events = []
for r in records:
    if r.get('screenshot_type') in ('order_history', 'order_details', 'exchange_position_card_closed'):
        # order_records list
        ors = r.get('order_records') or []
        for o in ors:
            pnl = o.get('realized_pnl_usdt')
            if pnl is not None:
                realized_events.append({
                    'ts_msg': r['ts_utc'][:16],
                    'fill_time': o.get('fill_time'),
                    'asset': o.get('asset'),
                    'exchange': o.get('exchange'),
                    'side': o.get('side'),
                    'price': o.get('price'),
                    'realized_pnl_usdt': pnl,
                    'fee_usdt': o.get('fee_usdt'),
                    'source_msg': r['message_id'],
                })
        # closed-position-card has realized in position_card
        pc = r.get('position_card') or {}
        rl = pc.get('realized_pnl_usdt_closed')
        if rl is not None:
            realized_events.append({
                'ts_msg': r['ts_utc'][:16],
                'fill_time': pc.get('close_time'),
                'asset': pc.get('asset'),
                'exchange': pc.get('exchange'),
                'side': f"Close {pc.get('direction','?').title()}",
                'price': pc.get('close_price'),
                'realized_pnl_usdt': rl,
                'fee_usdt': None,
                'source_msg': r['message_id'],
            })

print(f'\n=== Realized PnL events ===')
print(f'total events: {len(realized_events)}')
wins = [e for e in realized_events if (e['realized_pnl_usdt'] or 0) > 0]
losses = [e for e in realized_events if (e['realized_pnl_usdt'] or 0) < 0]
print(f'  wins: {len(wins)}   losses: {len(losses)}')
print(f'  per-fill win rate: {len(wins) / max(len(wins) + len(losses), 1):.1%}')
total = sum(e['realized_pnl_usdt'] or 0 for e in realized_events)
print(f'  total realized PnL across events: ${total:+,.2f}')
print(f'  sum wins: ${sum(e["realized_pnl_usdt"] for e in wins):+,.2f}')
print(f'  sum losses: ${sum(e["realized_pnl_usdt"] for e in losses):+,.2f}')

# Sample top winners and losers
realized_events.sort(key=lambda e: e['realized_pnl_usdt'] or 0, reverse=True)
def _fmt_event(e):
    ts = e.get('ts_msg') or '?'
    asset = e.get('asset') or '?'
    side = e.get('side') or '?'
    pnl = e.get('realized_pnl_usdt') or 0
    return f"  {ts:<19s}  {asset:<10s} {side:<18s} ${pnl:+12,.2f}"

print(f'\nTop 10 winning fills:')
for e in realized_events[:10]:
    print(_fmt_event(e))
print(f'\nTop 10 losing fills:')
for e in realized_events[-10:]:
    print(_fmt_event(e))

# === 3. Discretionary signal taxonomy ===
action_counts = Counter()
reason_keyword_counts = Counter()
action_by_market_state = defaultdict(Counter)
for r in records:
    ds = r.get('discretionary_signal') or {}
    at = ds.get('action_type')
    if not at: continue
    action_counts[at] += 1
    for kw in (ds.get('action_reason_keywords') or []):
        reason_keyword_counts[kw] += 1
    ms = ds.get('market_state_at_decision') or {}
    sess = ms.get('approx_session')
    if sess:
        action_by_market_state[at][f'session={sess}'] += 1
    sl = ms.get('structural_level_nearby')
    if sl:
        action_by_market_state[at][f'level={sl}'] += 1
    li = ms.get('liq_intensity_observed')
    if li:
        action_by_market_state[at][f'liq={li}'] += 1
    cvd = ms.get('cvd_state_observed')
    if cvd:
        action_by_market_state[at][f'cvd={cvd}'] += 1

print(f'\n=== Discretionary action taxonomy ===')
print(f'action_type counts:')
for at, n in action_counts.most_common():
    print(f'  {at:<25s}: {n}')

print(f'\ntop 30 reason keywords:')
for kw, n in reason_keyword_counts.most_common(30):
    print(f'  {kw:<35s}: {n}')

# === 4. WR three ways ===
print(f'\n=== WR three ways ===')
# Per-fill
print(f'1. Per-fill (every realized PnL > 0 = win):')
print(f'   {len(wins)}/{len(wins)+len(losses)} = {len(wins)/max(len(wins)+len(losses),1):.1%}')

# Per-trade lifecycle (use last snapshot PnL)
lifecycle_wins = sum(1 for t in ledger if (t['last_pnl_pct'] or 0) > 0)
lifecycle_total = sum(1 for t in ledger if t['last_pnl_pct'] is not None)
print(f'2. Per-trade-lifecycle (last snapshot PnL > 0):')
print(f'   {lifecycle_wins}/{lifecycle_total} = {lifecycle_wins/max(lifecycle_total,1):.1%}')

# Per-era
def era_of(ts: str) -> str:
    d = ts[:10]
    if d <= '2024-11-30': return 'bootstrap (Jun-Nov 2024)'
    if d <= '2025-03-31': return 'transition (Dec 2024 - Mar 2025)'
    if d <= '2025-11-30': return 'gap (Apr-Nov 2025)'
    return 'position-trading (Dec 2025 - May 2026)'

era_results = defaultdict(lambda: {'wins': 0, 'losses': 0, 'lifecycles_win': 0, 'lifecycles_total': 0})
for e in realized_events:
    era = era_of(e['ts_msg'])
    if (e['realized_pnl_usdt'] or 0) > 0:
        era_results[era]['wins'] += 1
    elif (e['realized_pnl_usdt'] or 0) < 0:
        era_results[era]['losses'] += 1
for t in ledger:
    if t['last_pnl_pct'] is None: continue
    era = era_of(t['first_ts'])
    era_results[era]['lifecycles_total'] += 1
    if t['last_pnl_pct'] > 0:
        era_results[era]['lifecycles_win'] += 1

print(f'\n3. Per-era:')
for era, s in era_results.items():
    pf = s['wins'] / max(s['wins'] + s['losses'], 1) * 100 if s['wins'] + s['losses'] else 0
    pl = s['lifecycles_win'] / max(s['lifecycles_total'], 1) * 100 if s['lifecycles_total'] else 0
    print(f'   {era}:')
    print(f'      per-fill WR:      {pf:.1f}% (n={s["wins"]+s["losses"]})')
    print(f'      per-lifecycle WR: {pl:.1f}% (n={s["lifecycles_total"]})')

# === 5. Exchange / leverage / direction ===
print(f'\n=== Exchange/leverage/direction distribution (position cards) ===')
ex_counts = Counter()
lev_counts = Counter()
dir_counts = Counter()
for r in records:
    if (r.get('screenshot_type') or '').startswith('exchange_position_card'):
        pc = r.get('position_card') or {}
        if pc.get('exchange'): ex_counts[pc['exchange']] += 1
        if pc.get('leverage'): lev_counts[pc['leverage']] += 1
        if pc.get('direction'): dir_counts[pc['direction']] += 1
print('exchanges:')
for k, v in ex_counts.most_common(): print(f'  {k}: {v}')
print('leverages (top 15):')
for k, v in sorted(lev_counts.items(), key=lambda kv: -kv[1])[:15]:
    print(f'  {k}x: {v}')
print('directions:')
for k, v in dir_counts.most_common(): print(f'  {k}: {v}')

# === 6. Save outputs ===
(OUT_DIR / 'trades.jsonl').write_text(
    '\n'.join(json.dumps(t, default=str) for t in ledger), encoding='utf-8')
(OUT_DIR / 'realized_pnl_events.jsonl').write_text(
    '\n'.join(json.dumps(e, default=str) for e in realized_events), encoding='utf-8')
taxonomy = {
    'action_counts': dict(action_counts),
    'reason_keyword_counts': dict(reason_keyword_counts.most_common(100)),
    'action_by_market_state': {k: dict(v) for k, v in action_by_market_state.items()},
}
(OUT_DIR / 'signal_taxonomy.json').write_text(
    json.dumps(taxonomy, indent=2), encoding='utf-8')

print(f'\nWrote outputs to {OUT_DIR}/')
print(f'  trades.jsonl  ({len(ledger)} lifecycles)')
print(f'  realized_pnl_events.jsonl ({len(realized_events)} fills)')
print(f'  signal_taxonomy.json')

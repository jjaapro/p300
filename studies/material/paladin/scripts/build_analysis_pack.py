#!/usr/bin/env python3
"""Build the analysis-ready pack: UTC timestamps, long-format actions, derived geometry.

Everything a backtest needs to join this trader's decisions onto OHLCV data.
"""
import json
import os
import re
from datetime import datetime, timezone, timedelta

import pandas as pd

SRC = '/tmp/paladin'
OUT = f'{SRC}/out/analysis'
os.makedirs(OUT, exist_ok=True)

POS = json.load(open(f'{SRC}/extract/positions_all.json', encoding='utf-8'))
EVS = json.load(open(f'{SRC}/extract/events_all.json', encoding='utf-8'))
IMGS = json.load(open(f'{SRC}/extract/merged.json', encoding='utf-8'))
RAW = sorted(json.load(open(f'{SRC}/scalpx_paladin.json', encoding='utf-8'))['messages'],
             key=lambda m: m['timestamp'])

# ---------------------------------------------------------------- time base --
# Every timestamp in the export is Europe/Helsinki (+03:00 throughout the window).
# Earlier files dropped the offset, so all of them are local. Rebuild from source.
TZ = timezone(timedelta(hours=3))
GUILD, CH = '1042855255089623100', '1501271173588193290'
MSG_UTC, MSG_ID = {}, {}
for i, m in enumerate(RAW, 1):
    dt = datetime.fromisoformat(m['timestamp'])
    MSG_UTC[i] = dt.astimezone(timezone.utc)
    MSG_ID[i] = m['id']
LOCAL_TO_MSG = {}
for i, m in enumerate(RAW, 1):
    LOCAL_TO_MSG.setdefault(m['timestamp'][:19].replace('T', ' '), i)


def to_utc(local_str, msgs=None):
    """Local 'YYYY-MM-DD HH:MM:SS' -> aware UTC datetime. Prefer an exact message match."""
    if not local_str:
        return None
    s = str(local_str)[:19].replace('T', ' ')
    if s in LOCAL_TO_MSG:
        return MSG_UTC[LOCAL_TO_MSG[s]]
    try:
        return datetime.strptime(s, '%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ).astimezone(timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(s[:10], '%Y-%m-%d').replace(tzinfo=TZ).astimezone(timezone.utc)
        except ValueError:
            return None


def iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ') if dt else None


def ms(dt):
    return int(dt.timestamp() * 1000) if dt else None


NONCRYPTO = {'GOLD': ('XAU', 'USD', 'metal'), 'XAU': ('XAU', 'USD', 'metal'),
             'OIL': ('WTI', 'USD', 'commodity')}
CONF = {'high': 3, 'medium': 2, 'low': 1}

# ------------------------------------------------------------ position rows --
seen_ids = {}
positions, actions, unresolved = [], [], []

for p in sorted(POS, key=lambda x: x['first_seen']):
    pid = p['position_id']
    seen_ids[pid] = seen_ids.get(pid, 0) + 1
    if seen_ids[pid] > 1:                      # two ids collided in the source
        pid = f'{pid}-{seen_ids[pid]}'

    sym = p['symbol']
    if sym in NONCRYPTO:
        base, quote, asset_class = NONCRYPTO[sym]
    else:
        base, quote, asset_class = sym[:-4], 'USDT', 'crypto'

    signal = to_utc(p['first_seen'])
    entry_t = to_utc(p.get('entry_time'))
    exit_t = to_utc(p.get('exit_time'))
    side = p.get('side')
    sgn = 1 if side == 'long' else (-1 if side == 'short' else None)

    entry_ref = p.get('avg_entry') if p.get('avg_entry') is not None else p.get('planned_entry')
    stop = p.get('planned_stop')
    tps = [t for t in (p.get('planned_take_profits') or []) if isinstance(t, (int, float))]
    tp1 = tps[0] if tps else None

    risk_abs = abs(entry_ref - stop) if (entry_ref and stop and entry_ref != stop) else None
    risk_pct = (risk_abs / entry_ref * 100) if (risk_abs and entry_ref) else None
    tp1_pct = (abs(tp1 - entry_ref) / entry_ref * 100) if (tp1 and entry_ref) else None
    planned_rr = (abs(tp1 - entry_ref) / risk_abs) if (tp1 and entry_ref and risk_abs) else None

    exit_px = p.get('exit_price')
    exit_r_calc = (((exit_px - entry_ref) * sgn) / risk_abs) if (exit_px and entry_ref and risk_abs and sgn) else None

    note = (p.get('planned_entry_note') or '') + ' ' + (p.get('risk_note') or '')
    entry_style = ('market' if re.search(r'\bCMP\b|current market|market price', note, re.I)
                   else ('limit' if re.search(r'\blimit\b|\bbid\b|wait', note, re.I) else None))

    # data-quality flags — the source contains his own decimal typos, recorded verbatim
    flags = []
    if risk_pct is not None and (risk_pct > 25 or risk_pct < 0.1):
        flags.append('stop_distance_implausible')
    if stop is not None and entry_ref and sgn:
        if (sgn == 1 and stop > entry_ref) or (sgn == -1 and stop < entry_ref):
            flags.append('stop_on_wrong_side')
    if tp1 is not None and entry_ref and sgn:
        if (sgn == 1 and tp1 < entry_ref) or (sgn == -1 and tp1 > entry_ref):
            flags.append('target_on_wrong_side')
    if p.get('confidence') == 'low':
        flags.append('low_confidence_reconstruction')

    backtestable = bool(asset_class == 'crypto' and signal and entry_ref and stop and sgn
                        and 'stop_distance_implausible' not in flags
                        and 'stop_on_wrong_side' not in flags)

    row = {
        'position_id': pid, 'symbol': sym, 'base': base, 'quote': quote, 'asset_class': asset_class,
        'side': side, 'side_sign': sgn,
        'signal_time_utc': iso(signal), 'signal_ms': ms(signal),
        'entry_time_utc': iso(entry_t), 'entry_ms': ms(entry_t),
        'exit_time_utc': iso(exit_t), 'exit_ms': ms(exit_t),
        'duration_hours': p.get('duration_hours'),
        'planned_entry': p.get('planned_entry'), 'avg_entry': p.get('avg_entry'),
        'entry_ref': entry_ref, 'entry_style': entry_style,
        'planned_stop': stop, 'planned_tp1': tp1,
        'planned_tps': '|'.join(str(t) for t in tps) or None,
        'n_planned_tps': len(tps),
        'planned_dca': '|'.join(str(d) for d in (p.get('planned_dca') or [])) or None,
        'risk_dist_abs': round(risk_abs, 10) if risk_abs else None,
        'risk_dist_pct': round(risk_pct, 4) if risk_pct else None,
        'tp1_dist_pct': round(tp1_pct, 4) if tp1_pct else None,
        'planned_rr': round(planned_rr, 3) if planned_rr else None,
        'leverage': p.get('leverage'), 'risk_note': p.get('risk_note'),
        'exit_price': exit_px, 'exit_type': p.get('exit_type'), 'outcome': p.get('outcome'),
        'r_stated': p.get('r_multiple'),
        'r_from_prices': round(exit_r_calc, 3) if exit_r_calc is not None else None,
        'roi_card_pct': p.get('roi_pct'),
        'n_dca_fills': len(p.get('dca_fills') or []), 'n_stop_moves': len(p.get('stop_moves') or []),
        'n_tp_changes': len(p.get('tp_changes') or []), 'n_partials': len(p.get('partials') or []),
        'still_open_at_end': bool(p.get('still_open_at_end')),
        'confidence': p.get('confidence'), 'confidence_rank': CONF.get(p.get('confidence')),
        'is_backtestable': backtestable,
        'data_quality_flags': '|'.join(flags) or None,
        'signal_hour_utc': signal.hour if signal else None,
        'signal_dow': signal.strftime('%a') if signal else None,
        'signal_date_utc': signal.strftime('%Y-%m-%d') if signal else None,
        'thesis': p.get('thesis'), 'indicators': '|'.join(p.get('indicators') or []) or None,
        'market_context': p.get('market_context'),
        'management_story': p.get('management_story'), 'notes': p.get('notes'),
        'first_msg': (p.get('msgs') or [None])[0], 'msgs': '|'.join(str(m) for m in (p.get('msgs') or [])),
        'first_msg_url': f"https://discord.com/channels/{GUILD}/{CH}/{MSG_ID[p['msgs'][0]]}" if p.get('msgs') else None,
    }
    positions.append(row)

    # ---------------------------------------------------------- action rows --
    def act(kind, t, price=None, pct=None, msg=None, note=None, seq=None):
        dt = to_utc(t) if isinstance(t, str) else t
        actions.append({'position_id': pid, 'symbol': sym, 'side': side, 'action': kind,
                        'time_utc': iso(dt), 'time_ms': ms(dt), 'price': price,
                        'portion_pct': pct, 'msg': msg, 'note': note,
                        'msg_url': f"https://discord.com/channels/{GUILD}/{CH}/{MSG_ID[msg]}" if msg in MSG_ID else None})

    act('signal', signal, p.get('planned_entry'), None, (p.get('msgs') or [None])[0], p.get('planned_entry_note'))
    if stop is not None:
        act('stop_set', signal, stop, None, (p.get('msgs') or [None])[0], 'published stop')
    for i, t in enumerate(tps, 1):
        act(f'target_set', signal, t, None, (p.get('msgs') or [None])[0], f'TP{i} as published')
    if entry_t:
        act('entry', entry_t, entry_ref, None, (p.get('msgs') or [None])[0], p.get('entry_style'))
    for f in (p.get('dca_fills') or []):
        act('dca_fill', f.get('time'), f.get('price'), f.get('portion_pct'), f.get('msg'), f.get('note'))
    for s in (p.get('stop_moves') or []):
        act('stop_move', s.get('time'), s.get('to'), None, s.get('msg'), s.get('type') or s.get('note'))
    for c in (p.get('tp_changes') or []):
        tl = [x for x in (c.get('to') or []) if isinstance(x, (int, float))]
        act('target_move', c.get('time'), tl[0] if tl else None, None, c.get('msg'),
            ('new targets ' + '|'.join(str(x) for x in tl)) if tl else c.get('note'))
    for x in (p.get('partials') or []):
        act('partial_close', x.get('time'), x.get('price'), x.get('portion_pct'), x.get('msg'), x.get('note'))
    if exit_t or exit_px is not None:
        act('exit', exit_t or exit_t, exit_px, 100, (p.get('msgs') or [None])[-1], p.get('exit_type'))

    if p['outcome'] == 'unknown':
        unresolved.append({'position_id': pid, 'symbol': sym, 'side': side,
                           'signal_time_utc': iso(signal), 'entry_ref': entry_ref,
                           'planned_stop': stop, 'planned_tp1': tp1,
                           'last_msg': (p.get('msgs') or [None])[-1],
                           'last_seen_utc': iso(MSG_UTC.get((p.get('msgs') or [None])[-1])),
                           'why': 'never resolved in the channel — resolve from OHLCV: whichever of stop/TP1 was touched first after signal_time_utc'})

# ------------------------------------------------------------- event rows ----
events = []
for e in EVS:
    dt = MSG_UTC.get(e['msg']) or to_utc(e['timestamp'])
    events.append({
        'msg': e['msg'], 'time_utc': iso(dt), 'time_ms': ms(dt),
        'event_type': e['event_type'], 'symbol': e.get('symbol'), 'side': e.get('side'),
        'entry_price': e.get('entry_price'), 'dca_price': e.get('dca_price'),
        'take_profits': '|'.join(str(x) for x in (e.get('take_profits') or [])) or None,
        'stop_loss': e.get('stop_loss'), 'portion_pct': e.get('portion_pct'),
        'leverage': e.get('leverage'), 'r_stated': e.get('r_multiple'), 'roi_pct': e.get('roi_pct'),
        'price_ref': e.get('price_ref'), 'risk_note': e.get('risk_note'),
        'rationale': e.get('rationale'),
        'indicators': '|'.join(e.get('indicators_mentioned') or []) or None,
        'market_context': e.get('market_context'), 'quote': e.get('quote'),
        'confidence': e.get('confidence'), 'confidence_rank': CONF.get(e.get('confidence')),
        'msg_url': f"https://discord.com/channels/{GUILD}/{CH}/{MSG_ID[e['msg']]}" if e['msg'] in MSG_ID else None,
    })

# ------------------------------------------- price observations (alignment) --
# Every price he quoted with a timestamp — use these to verify your OHLCV feed
# lines up with the venue he actually traded, before trusting any join.
obs = []
for e in EVS:
    if not e.get('symbol'):
        continue
    dt = MSG_UTC.get(e['msg'])
    for field in ('price_ref', 'entry_price'):
        v = e.get(field)
        if isinstance(v, (int, float)):
            obs.append({'time_utc': iso(dt), 'time_ms': ms(dt), 'symbol': e['symbol'],
                        'observed_price': v, 'field': field, 'event_type': e['event_type'],
                        'msg': e['msg'],
                        'note': 'price he referenced as live/current' if field == 'price_ref' else 'stated entry'})
for m in IMGS:
    for t in (m.get('trades') or []):
        if t.get('exit_price_type') in ('mark', 'last') and t.get('exit_price') and t.get('symbol_norm'):
            dt = to_utc(m['timestamp'])
            obs.append({'time_utc': iso(dt), 'time_ms': ms(dt), 'symbol': t['symbol_norm'],
                        'observed_price': t['exit_price'], 'field': 'card_mark_price',
                        'event_type': 'screenshot', 'msg': None,
                        'note': 'mark price on an exchange share card — the tightest timestamp/price pair available'})

# ------------------------------------------------------------------- write --
dfp = pd.DataFrame(positions)
dfa = pd.DataFrame(actions).sort_values(['time_ms', 'position_id'], na_position='last')
dfe = pd.DataFrame(events)
dfo = pd.DataFrame(obs).dropna(subset=['time_ms']).sort_values('time_ms')
dfu = pd.DataFrame(unresolved)

for name, df in [('positions', dfp), ('actions', dfa), ('events', dfe),
                 ('price_observations', dfo), ('unresolved_positions', dfu)]:
    df.to_csv(f'{OUT}/{name}.csv', index=False, encoding='utf-8')
    df.to_parquet(f'{OUT}/{name}.parquet', index=False)
    df.to_json(f'{OUT}/{name}.jsonl', orient='records', lines=True, force_ascii=False)
    print(f'{name:22s} {len(df):>5} rows  {len(df.columns):>3} cols')

print('\nbacktestable positions:', int(dfp.is_backtestable.sum()), 'of', len(dfp))
print('crypto symbols:', dfp[dfp.asset_class == 'crypto'].base.nunique())
print('signal_time span UTC:', dfp.signal_time_utc.min(), '->', dfp.signal_time_utc.max())
print('actions by type:\n', dfa.action.value_counts().to_string())
print('\nrisk_dist_pct describe:\n', dfp.risk_dist_pct.describe().round(2).to_string())
print('\nplanned_rr describe:\n', dfp.planned_rr.describe().round(2).to_string())
print('\nr_from_prices vs r_stated, where both exist:')
both = dfp.dropna(subset=['r_stated', 'r_from_prices'])
print(f'  n={len(both)}  mean abs diff={float((both.r_stated - both.r_from_prices).abs().mean()):.2f}')

"""Cluster Q2/Q3 messages by ≤6h adjacency. Each cluster ~= one trade lifecycle.

For each cluster, emit:
  - cluster_id (first message_id)
  - n_msgs (how many screenshots)
  - duration_hours
  - reads_needed: 1 (just entry) if duration < 6h, else 2 (entry + final), else 3 if > 24h
"""
import json, sys
from pathlib import Path
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path('c:/Source/Repos/p300')
QUEUE = ROOT/'studies/material/chento/phase1_queue.jsonl'
DONE_FILE = ROOT/'studies/material/chento/phase1_trades.jsonl'
ALL_MSGS = ROOT/'studies/material/chento/messages.jsonl'

# Re-build the full Q2/Q3 list (queue excludes already-done — for clustering we want everything)
records = [json.loads(l) for l in ALL_MSGS.read_text(encoding='utf-8').splitlines() if l.strip()]
q23 = []
for r in records:
    if r['author_id'] != '978925049945919499': continue
    if not r['attachment_urls']: continue
    dt = datetime.fromisoformat(r['ts_utc'])
    if datetime(2024,6,1).date() <= dt.date() <= datetime(2024,9,30).date():
        q23.append(r)
q23.sort(key=lambda r: r['ts_utc'])

# Cluster with 6h adjacency
GAP_HOURS = 6.0
clusters = []
current = []
prev_ts = None
for r in q23:
    ts = datetime.fromisoformat(r['ts_utc'])
    if prev_ts is None or (ts - prev_ts).total_seconds() / 3600 <= GAP_HOURS:
        current.append(r)
    else:
        clusters.append(current)
        current = [r]
    prev_ts = ts
if current:
    clusters.append(current)

# For each cluster, decide reads needed
total_reads = 0
small_clusters = mid_clusters = big_clusters = 0
for i, cl in enumerate(clusters):
    start = datetime.fromisoformat(cl[0]['ts_utc'])
    end = datetime.fromisoformat(cl[-1]['ts_utc'])
    duration_h = (end - start).total_seconds() / 3600
    n = len(cl)
    if duration_h < 6:
        reads = min(1, n); small_clusters += 1
    elif duration_h < 24:
        reads = min(2, n); mid_clusters += 1
    else:
        reads = min(3, n); big_clusters += 1
    total_reads += reads

# Done set
done = set()
for l in DONE_FILE.read_text(encoding='utf-8').splitlines():
    if l.strip(): done.add(json.loads(l)['message_id'])

# Effective reads needed = total_reads minus what's already done
already_in_done = sum(1 for cl in clusters for r in cl if r['message_id'] in done)
print(f'2024-Q2/Q3 messages with images: {len(q23)}')
print(f'clusters (≤{GAP_HOURS}h gap): {len(clusters)}')
print(f'  small (<6h):  {small_clusters}  → 1 read each')
print(f'  mid   (<24h): {mid_clusters}  → 2 reads each')
print(f'  big   (≥24h): {big_clusters}  → 3 reads each')
print(f'\ntotal reads suggested: {total_reads}')
print(f'already-done messages: {already_in_done} (some may overlap with suggested reads)')

# Print first 15 clusters with structure
print('\nfirst 15 clusters:')
for i, cl in enumerate(clusters[:15]):
    start = datetime.fromisoformat(cl[0]['ts_utc']).strftime('%Y-%m-%d %H:%M')
    end = datetime.fromisoformat(cl[-1]['ts_utc']).strftime('%H:%M')
    dur = (datetime.fromisoformat(cl[-1]['ts_utc']) - datetime.fromisoformat(cl[0]['ts_utc'])).total_seconds() / 3600
    done_in_cl = sum(1 for r in cl if r['message_id'] in done)
    print(f'  C{i:>2}  {start} -> {end}  ({dur:5.1f}h, n={len(cl):>2}, done={done_in_cl})  '
          f'first_text={cl[0]["text"][:60]!r}')

# Save cluster manifest
import json as _j
CL_FILE = ROOT/'studies/material/chento/phase1_clusters.json'
out_clusters = []
for i, cl in enumerate(clusters):
    out_clusters.append({
        'cluster_id': i,
        'first_msg_id': cl[0]['message_id'],
        'first_ts': cl[0]['ts_utc'],
        'last_ts': cl[-1]['ts_utc'],
        'duration_hours': (datetime.fromisoformat(cl[-1]['ts_utc']) - datetime.fromisoformat(cl[0]['ts_utc'])).total_seconds()/3600,
        'message_ids': [r['message_id'] for r in cl],
        'texts': [r['text'][:200] for r in cl],
        'already_done_in_cluster': [r['message_id'] for r in cl if r['message_id'] in done],
    })
CL_FILE.write_text(_j.dumps(out_clusters, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'\nwrote {CL_FILE}')

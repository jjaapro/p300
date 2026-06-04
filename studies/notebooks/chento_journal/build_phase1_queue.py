"""Build the full Q2/Q3 2024 extraction queue:
- All 156 chento messages with images in that window
- Mark which are already in phase1_trades.jsonl
- Output a queue file with local_path for each remaining image
"""
import json, sys
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path('c:/Source/Repos/p300')
MSGS = ROOT/'studies/material/chento/messages.jsonl'
IMG_DIR = ROOT/'studies/material/chento/images'
DONE_FILE = ROOT/'studies/material/chento/phase1_trades.jsonl'
QUEUE = ROOT/'studies/material/chento/phase1_queue.jsonl'

records = [json.loads(l) for l in MSGS.read_text(encoding='utf-8').splitlines() if l.strip()]
# All Q2/Q3 chento posts with images
q23 = []
for r in records:
    if r['author_id'] != '978925049945919499': continue
    if not r['attachment_urls']: continue
    dt = datetime.fromisoformat(r['ts_utc'])
    if datetime(2024,6,1).date() <= dt.date() <= datetime(2024,9,30).date():
        q23.append(r)
q23.sort(key=lambda r: r['ts_utc'])
print(f'2024-Q2/Q3 chento posts with images: {len(q23)}')

# Already done set
done = set()
for l in DONE_FILE.read_text(encoding='utf-8').splitlines():
    if not l.strip(): continue
    rec = json.loads(l)
    done.add(rec['message_id'])
print(f'already extracted: {len(done)}')

# Build queue with local paths
queue = []
missing_local = 0
for r in q23:
    if r['message_id'] in done: continue
    # find local file matching this message_id
    matches = list(IMG_DIR.glob(f'{r["message_id"]}_*'))
    if not matches:
        missing_local += 1
        continue
    # Use first attachment (idx=0)
    matches.sort()
    queue.append({
        'message_id': r['message_id'],
        'ts': r['ts_utc'],
        'text': r['text'][:300] if r['text'] else '',
        'attachment_count': len(r['attachment_urls']),
        'local_path': str(matches[0].relative_to(ROOT)),
    })
print(f'queued for extraction: {len(queue)}  (missing locally: {missing_local})')

# Save queue
with QUEUE.open('w', encoding='utf-8') as fh:
    for q in queue:
        fh.write(json.dumps(q, ensure_ascii=False) + '\n')
print(f'wrote {QUEUE}')

# Preview the first 10
print('\nfirst 10 in queue:')
for q in queue[:10]:
    print(f'  {q["ts"][:16]}  {q["message_id"]}  text={q["text"][:60]!r}')

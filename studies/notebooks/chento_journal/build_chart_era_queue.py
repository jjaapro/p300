"""Build a targeted queue of 'high-information' chart-era images.

Criteria (chart era = 2025-Q4 onwards):
- chento author
- has attachment
- in 2025-10-01 to present
- first-of-cluster (cluster = ≤6h gap)
- caption text hints at a setup (long/short/buy/sell/risk/tp/sl/entry keyword)
  OR caption is non-trivial (>50 chars excluding mentions)
"""
import json, re, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path('c:/Source/Repos/p300')
MSGS = ROOT/'studies/material/chento/messages.jsonl'
IMG_DIR = ROOT/'studies/material/chento/images'

records = [json.loads(l) for l in MSGS.read_text(encoding='utf-8').splitlines() if l.strip()]
chento_imgs = [r for r in records
               if r['author_id'] == '978925049945919499'
               and r['attachment_urls']
               and r['ts_utc'] >= '2025-10-01']
chento_imgs.sort(key=lambda r: r['ts_utc'])
print(f'chart-era (2025-10-01+) chento posts with images: {len(chento_imgs)}')

# Cluster with ≤6h adjacency
clusters = []; cur = []; prev = None
for r in chento_imgs:
    ts = datetime.fromisoformat(r['ts_utc'])
    if prev is None or (ts - prev).total_seconds()/3600 <= 6:
        cur.append(r)
    else:
        clusters.append(cur); cur = [r]
    prev = ts
if cur: clusters.append(cur)
print(f'chart-era clusters: {len(clusters)}')

# Take first-of-cluster, filter for informative caption OR include all (small)
setup_kw = re.compile(r'\b(long|short|buy|sell|sl|tp|stop|target|entry|filled|risk|trade|setup|range|level|sweep|liq|ob|orderblock|fvg|cvd|funding|bias|biases|drawdown|liquidation)\b', re.I)
def clean_text(t):
    return re.sub(r'@\w+', '', t).strip()

high_value = []
for ci, cl in enumerate(clusters):
    first = cl[0]
    text = clean_text(first['text'])
    # informative if has setup keyword OR length >40 chars (non-mention)
    if setup_kw.search(text) or len(text) >= 40:
        matches = list(IMG_DIR.glob(f'{first["message_id"]}_*'))
        if matches:
            high_value.append({
                'cluster_id': ci,
                'msg_id': first['message_id'],
                'ts': first['ts_utc'],
                'text': text[:200],
                'n_in_cluster': len(cl),
                'local_path': str(matches[0].relative_to(ROOT)),
            })

print(f'\nhigh-value cluster-first images: {len(high_value)}')
out = ROOT/'studies/material/chento/chart_era_queue.json'
out.write_text(json.dumps(high_value, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'wrote {out}')

# Preview first 30
print(f'\nfirst 30:')
for i, h in enumerate(high_value[:30]):
    print(f'{i:>2}. {h["ts"][:16]}  n={h["n_in_cluster"]:>2}  text={h["text"][:80]!r}')

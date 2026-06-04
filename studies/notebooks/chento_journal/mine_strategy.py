"""Mine chento's text corpus for strategy content.

Two outputs:
1. All text posts longer than 300 chars (strategy explanations / commentary)
2. Terminology frequency across the 2-year window (look for SMC/ICT vocab evolution)
"""
import json, re, sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

MSGS = Path('c:/Source/Repos/p300/studies/material/chento/messages.jsonl')
records = [json.loads(l) for l in MSGS.read_text(encoding='utf-8').splitlines() if l.strip()]
chento = [r for r in records if r['author_id'] == '978925049945919499']
print(f'chento messages: {len(chento):,}')

# 1. All long text posts (>= 300 chars)
print('\n' + '='*80)
print('PART 1 — Long text posts (≥300 chars) ordered chronologically')
print('='*80)
long_posts = [r for r in chento if len(r['text']) >= 300]
long_posts.sort(key=lambda r: r['ts_utc'])
print(f'{len(long_posts)} long posts\n')
for r in long_posts:
    text = r['text'].strip()
    print(f'\n--- [{r["ts_utc"][:16]}] msg={r["message_id"]} ({len(text)} chars) ---')
    print(text[:2000])
    if len(text) > 2000:
        print(f'...[truncated, {len(text)-2000} more chars]')

"""HTML → jsonl parser for the chento Discord export.

Run from project root:
    python studies/notebooks/chento_journal/parse_journal.py

Reads:  studies/material/ScalpX...chento.html
Writes: studies/material/chento/messages.jsonl
        studies/material/chento/images_index.csv

Idempotent — safe to re-run. JSON lines chosen over parquet so the file is
hand-inspectable and we don't need to install pyarrow.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[3]
SRC  = ROOT / 'studies' / 'material' / 'ScalpX - Exclusive Trading Community - Traders logs - 🐐｜chento [1249330346126872647].html'
OUT_DIR = ROOT / 'studies' / 'material' / 'chento'
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_MSGS = OUT_DIR / 'messages.jsonl'
OUT_IMGS = OUT_DIR / 'images_index.csv'


# DiscordChatExporter writes the full date as "Monday, 10 June 2024 11.24" on
# the first message of a group and "11.24" on continuations within the same
# minute. We always have at least the full date somewhere in the group, so
# we carry the last-seen full date forward.
FULL_TS_RE = re.compile(r'^[A-Za-z]+, (\d{1,2}) ([A-Za-z]+) (\d{4}) (\d{1,2})\.(\d{2})$')
MONTHS = {m: i+1 for i, m in enumerate(
    ['January','February','March','April','May','June','July','August',
     'September','October','November','December'])}


def parse_ts(title: str, fallback_date: tuple[int,int,int] | None) -> tuple[datetime, tuple[int,int,int]]:
    """Return (utc_dt, (y,m,d)). title is the chatlog__timestamp title attr.
    On short timestamps like '11.24' we need fallback_date.
    """
    m = FULL_TS_RE.match(title)
    if m:
        d, mon, y, hh, mm = m.groups()
        month_num = MONTHS[mon]
        dt = datetime(int(y), month_num, int(d), int(hh), int(mm), tzinfo=timezone.utc)
        return dt, (int(y), month_num, int(d))
    short = re.match(r'^(\d{1,2})\.(\d{2})$', title)
    if short and fallback_date is not None:
        y, mo, d = fallback_date
        hh, mm = short.groups()
        dt = datetime(y, mo, d, int(hh), int(mm), tzinfo=timezone.utc)
        return dt, fallback_date
    raise ValueError(f'unrecognised timestamp: {title!r} (fallback={fallback_date})')


def message_to_dict(container, last_full_date, last_author, last_author_id):
    """Convert one <div class="chatlog__message-container"> to a dict.

    Returns (record_dict, new_full_date, new_author, new_author_id) so the
    caller can carry state forward across continuation messages.
    """
    mid = container.get('data-message-id')
    if mid is None:
        m = re.match(r'chatlog__message-container-(\d+)', container.get('id', ''))
        mid = m.group(1) if m else None

    # author
    author_span = container.select_one('.chatlog__author')
    if author_span is not None:
        author = author_span.get_text(' ', strip=True)
        author_id = author_span.get('data-user-id') or last_author_id
        # The "title" attr often holds the discord-handle
        author_handle = author_span.get('title') or author
    else:
        author = last_author
        author_id = last_author_id
        author_handle = last_author

    # timestamp
    ts_span = container.select_one('.chatlog__timestamp')
    if ts_span is None:
        ts_span = container.select_one('.chatlog__short-timestamp')
    if ts_span is None:
        return None, last_full_date, last_author, last_author_id
    ts_title = ts_span.get('title') or ts_span.get_text(' ', strip=True)
    try:
        dt, new_full_date = parse_ts(ts_title, last_full_date)
    except ValueError:
        return None, last_full_date, last_author, last_author_id

    # text body — chatlog__content holds markdown
    text_plain = ''
    for content in container.select('.chatlog__content'):
        text_plain += content.get_text(' ', strip=True) + '\n'
    text_plain = text_plain.strip()

    # attachments
    attachments = []
    for a in container.select('.chatlog__attachment a'):
        href = a.get('href')
        if href:
            attachments.append(href)

    # also pull embed images and embed titles/descriptions (some posts use embeds)
    for embed in container.select('.chatlog__embed'):
        for em_img in embed.select('img'):
            src = em_img.get('src')
            if src and 'twemoji' not in src and 'twitter' not in src:
                attachments.append(src)

    # reactions: {name: count}
    reactions = {}
    for r in container.select('.chatlog__reactions .chatlog__reaction'):
        name = r.get('title') or '?'
        count_span = r.select_one('.chatlog__reaction-count')
        count = int(count_span.get_text(strip=True)) if count_span else 1
        reactions[name] = count

    # reply target
    reply_to = None
    reply_a = container.select_one('.chatlog__reply a')
    if reply_a is not None:
        href = reply_a.get('href', '')
        m = re.search(r'(\d{16,})', href)
        if m:
            reply_to = m.group(1)

    rec = {
        'message_id':    mid,
        'ts_utc':        dt.isoformat(),
        'author':        author,
        'author_handle': author_handle,
        'author_id':     author_id,
        'text':          text_plain,
        'attachment_urls': attachments,
        'reactions':     reactions,
        'reply_to':      reply_to,
        'is_continuation': container.select_one('.chatlog__short-timestamp') is not None,
    }
    return rec, new_full_date, author, author_id


def main() -> int:
    if not SRC.exists():
        print(f'source HTML not found: {SRC}', file=sys.stderr)
        return 2
    print(f'parsing {SRC.name} ({SRC.stat().st_size/1e6:.1f} MB)')
    html = SRC.read_text(encoding='utf-8', errors='replace')
    soup = BeautifulSoup(html, 'html.parser')

    containers = soup.select('.chatlog__message-container')
    print(f'found {len(containers):,} message containers')

    last_full_date = None
    last_author = None
    last_author_id = None
    records = []
    images = []  # (message_id, url)
    skipped = 0

    for c in containers:
        rec, last_full_date, last_author, last_author_id = message_to_dict(
            c, last_full_date, last_author, last_author_id)
        if rec is None:
            skipped += 1
            continue
        records.append(rec)
        for url in rec['attachment_urls']:
            images.append((rec['message_id'], url))

    print(f'parsed:  {len(records):,}  skipped: {skipped}')

    OUT_MSGS.write_text(
        '\n'.join(json.dumps(r, ensure_ascii=False) for r in records),
        encoding='utf-8'
    )
    print(f'wrote {OUT_MSGS}')

    with OUT_IMGS.open('w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['message_id', 'url'])
        w.writerows(images)
    print(f'wrote {OUT_IMGS}  ({len(images):,} image rows)')

    # Quick stats
    by_author = {}
    for r in records:
        by_author[r['author']] = by_author.get(r['author'], 0) + 1
    print('\ntop authors:')
    for a, n in sorted(by_author.items(), key=lambda kv: -kv[1])[:8]:
        print(f'  {a}: {n}')

    first = records[0]['ts_utc'] if records else '-'
    last  = records[-1]['ts_utc'] if records else '-'
    print(f'\ndate range: {first} → {last}')

    # Text-content summary
    has_text  = sum(1 for r in records if r['text'])
    has_img   = sum(1 for r in records if r['attachment_urls'])
    text_only = sum(1 for r in records if r['text'] and not r['attachment_urls'])
    img_only  = sum(1 for r in records if r['attachment_urls'] and not r['text'])
    both      = sum(1 for r in records if r['text'] and r['attachment_urls'])
    empty     = sum(1 for r in records if not r['text'] and not r['attachment_urls'])
    print(f'\ncontent breakdown:')
    print(f'  has text:        {has_text:,}')
    print(f'  has image(s):    {has_img:,}')
    print(f'  text-only:       {text_only:,}')
    print(f'  image-only:      {img_only:,}')
    print(f'  text + image:    {both:,}')
    print(f'  empty:           {empty:,}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""Download Discord CDN images locally before the URL tokens expire.

Discord attachment URLs include `ex=` (expiration unix timestamp) + signed
`hm=` hash; once `ex` is past, the URL 404s. We download eagerly so that
the analysis can run offline against local files.

Run:
    python studies/notebooks/chento_journal/download_images.py [--limit N]

Reads:  studies/material/chento/images_index.csv
Writes: studies/material/chento/images/{message_id}_{idx}.{ext}
        studies/material/chento/images_local.csv  (message_id, url, local_path, status)
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[3]
INDEX_CSV  = ROOT / 'studies' / 'material' / 'chento' / 'images_index.csv'
IMG_DIR    = ROOT / 'studies' / 'material' / 'chento' / 'images'
LOCAL_CSV  = ROOT / 'studies' / 'material' / 'chento' / 'images_local.csv'
IMG_DIR.mkdir(parents=True, exist_ok=True)

EXT_FROM_URL = lambda url: (url.split('?', 1)[0].rsplit('.', 1)[-1] or 'bin').lower()


def safe_name(url: str, message_id: str, idx: int) -> Path:
    ext = EXT_FROM_URL(url)
    if ext not in ('png','jpg','jpeg','gif','webp'):
        ext = 'png'
    # Hash the URL so re-runs are idempotent and same image isn't redownloaded.
    h = hashlib.md5(url.encode()).hexdigest()[:8]
    return IMG_DIR / f'{message_id}_{idx}_{h}.{ext}'


def download(url: str, dst: Path, retries: int = 2) -> tuple[bool, str]:
    if dst.exists() and dst.stat().st_size > 0:
        return True, 'cached'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            dst.write_bytes(data)
            return True, f'ok ({len(data)} bytes)'
        except HTTPError as e:
            if e.code == 404 or e.code == 403:
                return False, f'expired ({e.code})'
            if attempt == retries:
                return False, f'http {e.code}'
        except URLError as e:
            if attempt == retries:
                return False, f'urlerror {e}'
        except Exception as e:
            if attempt == retries:
                return False, f'err {type(e).__name__}: {e}'
        time.sleep(1)
    return False, 'unreachable'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=None,
                    help='Stop after N images (use small value to test URL validity)')
    args = ap.parse_args()

    if not INDEX_CSV.exists():
        print(f'index csv not found: {INDEX_CSV}', file=sys.stderr); return 2

    rows = []
    with INDEX_CSV.open(encoding='utf-8') as fh:
        r = csv.reader(fh)
        next(r)  # header
        for line in r:
            if len(line) >= 2:
                rows.append((line[0], line[1]))

    if args.limit:
        rows = rows[:args.limit]
    print(f'attempting {len(rows):,} images → {IMG_DIR}')

    # Re-load existing local manifest if any (so we can resume)
    local_rows = []
    ok = bad = cached = 0
    msg_idx_counter: dict[str,int] = {}
    for msg_id, url in rows:
        idx = msg_idx_counter.get(msg_id, 0)
        msg_idx_counter[msg_id] = idx + 1
        dst = safe_name(url, msg_id, idx)
        success, status = download(url, dst)
        if success and status == 'cached':
            cached += 1
        elif success:
            ok += 1
        else:
            bad += 1
        local_rows.append((msg_id, url, str(dst.relative_to(ROOT)), status))
        if (ok + bad + cached) % 25 == 0:
            print(f'  progress: ok={ok} cached={cached} bad={bad}')

    print(f'\nfinal: ok={ok}  cached={cached}  bad={bad}  total={len(rows)}')

    with LOCAL_CSV.open('w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['message_id','url','local_path','status'])
        w.writerows(local_rows)
    print(f'wrote {LOCAL_CSV}')

    # If any 'expired' come up, print a few examples
    expired = [r for r in local_rows if r[3].startswith('expired')]
    if expired:
        print(f'\n{len(expired)} URLs returned 403/404 (expired) — examples:')
        for r in expired[:3]:
            print(f'  {r[0]}: {r[1][:100]}...')

    return 0


if __name__ == '__main__':
    sys.exit(main())

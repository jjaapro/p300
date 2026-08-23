#!/usr/bin/env python3
"""Download every image/attachment referenced in a DiscordChatExporter JSON export.

Output:
  <out>/images/NNN_YYYY-MM-DD_HHMMSS_<original name>   (one file per attachment / embed image)
  <out>/manifest.csv                                    (one row per downloaded file, with message context)
"""
import csv
import json
import os
import re
import sys
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, unquote

import requests

SRC = sys.argv[1]
OUT = sys.argv[2]
IMG_DIR = os.path.join(OUT, "images")
os.makedirs(IMG_DIR, exist_ok=True)

with open(SRC, encoding="utf-8") as f:
    data = json.load(f)

guild_id = data["guild"]["id"]
channel_id = data["channel"]["id"]
messages = sorted(data["messages"], key=lambda m: m["timestamp"])


def sanitize(name: str) -> str:
    name = unquote(name)
    name = re.sub(r"[\\/:*?\"<>|\s]+", "_", name).strip("._")
    return name[:120] or "file"


def ts_parts(ts: str):
    # "2026-05-06T23:44:07.027+03:00" -> ("2026-05-06", "234407")
    m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})", ts)
    return m.group(1), m.group(2) + m.group(3) + m.group(4)


jobs = []  # dicts describing each file to fetch
idx = 0
for m in messages:
    date, hms = ts_parts(m["timestamp"])
    author = m["author"].get("nickname") or m["author"].get("name")
    base = {
        "message_id": m["id"],
        "timestamp": m["timestamp"],
        "author": author,
        "author_username": m["author"].get("name"),
        "message_link": f"https://discord.com/channels/{guild_id}/{channel_id}/{m['id']}",
        "content": m.get("content", ""),
        "is_pinned": m.get("isPinned", False),
        "reply_to": (m.get("reference") or {}).get("messageId", ""),
    }
    for a in m.get("attachments", []):
        idx += 1
        fname = f"{idx:03d}_{date}_{hms}_{sanitize(a['fileName'])}"
        jobs.append({**base, "index": idx, "kind": "attachment", "original_name": a["fileName"],
                     "urls": [a["url"]], "filename": fname, "expected_bytes": a.get("fileSizeBytes")})
    for ei, e in enumerate(m.get("embeds", [])):
        candidates = []
        for img in e.get("images", []) or []:
            candidates.append(("embed_image", img))
        if e.get("image") and not any(c[1].get("url") == e["image"].get("url") for c in candidates):
            candidates.append(("embed_image", e["image"]))
        if e.get("thumbnail"):
            candidates.append(("embed_thumbnail", e["thumbnail"]))
        if e.get("video") and e["video"].get("url"):
            candidates.append(("embed_video", e["video"]))
        for kind, img in candidates:
            url = img.get("url")
            if not url:
                continue
            idx += 1
            canon = img.get("canonicalUrl")
            # derive a readable name from the canonical/original url
            src_for_name = canon or url
            path = urlparse(src_for_name).path
            orig = os.path.basename(path) or "embed"
            # discord proxy urls encode the real url in the path: .../https/pbs.twimg.com/media/XXX.jpg%3Alarge
            pm = re.search(r"/https?/([^/]+)/(.+)$", urlparse(url).path)
            if pm and not canon:
                orig = os.path.basename(unquote(pm.group(2)))
            orig = re.sub(r"[:%].*$", "", orig)  # strip ":large" style suffixes
            if "." not in orig:
                orig += ".jpg"
            fname = f"{idx:03d}_{date}_{hms}_EMBED_{sanitize(orig)}"
            urls = [url]
            if canon:
                urls.append(canon)
            elif pm:
                urls.append("https://" + pm.group(1) + "/" + unquote(pm.group(2)).replace(":large", "?name=large"))
            jobs.append({**base, "index": idx, "kind": kind, "original_name": orig, "urls": urls,
                         "filename": fname, "expected_bytes": None,
                         "embed_title": e.get("title") or "", "embed_url": e.get("url") or ""})

print(f"{len(jobs)} files to fetch from {len(messages)} messages", flush=True)

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"})


def fetch(job):
    dest = os.path.join(IMG_DIR, job["filename"])
    last_err = ""
    for url in job["urls"]:
        for attempt in range(4):
            try:
                r = session.get(url, timeout=60)
                if r.status_code == 200 and r.content:
                    with open(dest, "wb") as f:
                        f.write(r.content)
                    job["status"] = "ok"
                    job["bytes"] = len(r.content)
                    job["content_type"] = r.headers.get("Content-Type", "")
                    job["sha256"] = hashlib.sha256(r.content).hexdigest()
                    job["used_url"] = url
                    return job
                last_err = f"HTTP {r.status_code}"
                if r.status_code in (403, 404, 410):
                    break  # no point retrying this url
            except Exception as ex:  # noqa
                last_err = repr(ex)
            time.sleep(1.5 * (attempt + 1))
    job["status"] = "FAILED: " + last_err
    job["bytes"] = 0
    job["content_type"] = ""
    job["sha256"] = ""
    job["used_url"] = ""
    return job


done = 0
with ThreadPoolExecutor(max_workers=8) as ex:
    futs = [ex.submit(fetch, j) for j in jobs]
    for fut in as_completed(futs):
        j = fut.result()
        done += 1
        if done % 25 == 0 or j["status"] != "ok":
            print(f"[{done}/{len(jobs)}] {j['filename']} -> {j['status']}", flush=True)

jobs.sort(key=lambda j: j["index"])
ok = sum(1 for j in jobs if j["status"] == "ok")
print(f"downloaded {ok}/{len(jobs)}", flush=True)

cols = ["index", "filename", "kind", "status", "timestamp", "author", "message_id", "message_link",
        "original_name", "bytes", "expected_bytes", "content_type", "is_pinned", "embed_title", "embed_url",
        "source_url", "content"]
with open(os.path.join(OUT, "manifest.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for j in jobs:
        j["source_url"] = j["used_url"] or j["urls"][0]
        w.writerow({c: j.get(c, "") for c in cols})

with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump([{k: v for k, v in j.items()} for j in jobs], f, ensure_ascii=False, indent=1)

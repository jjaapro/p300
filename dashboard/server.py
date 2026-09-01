"""p300 monitoring dashboard — read-only local web UI.

Usage:
  python dashboard/server.py                 # serve http://127.0.0.1:8300
  python dashboard/server.py --port 8400 --open

Panels: fleet liveness + duplicate detection (psutil ground truth with the
venv-shim collapse, dashboard/procscan.py), data-feed freshness grid, an
alert strip mirroring monitor.py's checks live, the trade chart with entry
markers (planned TP/SL/timed stop on hover) with flow panes underneath
(perp/spot CVD + divergence, OI + ΔOI quadrant label, funding + basis —
dashboard/market.py, descriptive "what the bots see" context, not a
signal), a daily positioning tile (L/S ratio 1y percentile, CPR gate,
regime circuit breaker), a 24h delta-by-price profile, and per-bot
strategy explainers with an annotated picture of the latest entry.

Why it exists: the 2026-08-15→24 incident — every bot + feed ran doubled
for 9 days (manual double-start; chento double-sized every signal:
SJ-4243/44, 4245/46, 4248/49) while monitor.py read all-green. Heartbeat
rows are name-keyed, so two instances look like one fresh row; only a
same-machine process scan sees the truth, and this dashboard polls that
scan every few seconds. Note that on Windows a HEALTHY bot is two
python.exe processes (venv shim parent + real interpreter child with the
identical command line) — procscan collapses that; do not "fix" it by
counting command lines.

Read-only guarantee: prod.db is opened mode=ro with PRAGMA query_only=1
(dashboard/queries.py); this process cannot write the ledger. Safe to run
alongside the fleet; starting it twice is harmless (second bind fails).
Run it as the same user as the bots so psutil can read their cmdlines.

Exit codes: 0 clean shutdown (Ctrl+C), 2 bind failure (port in use).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from dashboard import botinfo, market, queries  # noqa: E402
# dashboard.render is imported lazily on the first entry-chart request —
# it pulls in matplotlib/mplfinance, which server startup shouldn't pay for.

log = logging.getLogger("dashboard")

_TRADE_ID_RE = re.compile(r"^SJ-\d+$")
_CHART_THEMES = ("dark", "light")           # render._STYLES keys

STATIC_DIR = Path(__file__).resolve().parent / "static"

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".md": "text/markdown; charset=utf-8",
}

# path -> fn(params: dict[str, str]) -> dict (JSON-serializable)
API_ROUTES = {
    "/api/overview": lambda p: queries.overview(),
    "/api/feeds": lambda p: queries.feeds(),
    "/api/trades": lambda p: queries.trades(p.get("scope", "recent")),
    "/api/candles": lambda p: queries.candles(
        p.get("asset", "BTC"), p.get("tf", "1h"),
        int(p.get("bars", 0)), int(p.get("after", 0))),
    "/api/bots": lambda p: botinfo.summary(),
    "/api/flow": lambda p: market.flow(
        p.get("asset", "BTC"), p.get("tf", "1h"),
        int(p.get("bars", 0)), int(p.get("after", 0))),
    "/api/positioning": lambda p: market.positioning(p.get("asset", "BTC")),
    "/api/profile": lambda p: market.profile(
        p.get("asset", "BTC"), int(p.get("hours", 24)),
        int(p.get("buckets", 24))),
}


class _Handler(BaseHTTPRequestHandler):
    server_version = "p300dash"

    def log_message(self, fmt, *args):          # default is stderr per request
        log.debug("%s %s", self.address_string(), fmt % args)

    def do_GET(self):                            # noqa: N802 (http.server API)
        try:
            self._route()
        except (BrokenPipeError, ConnectionAbortedError,
                ConnectionResetError):
            pass                                 # client went away mid-write
        except Exception as e:                   # any handler bug -> 500 JSON
            log.exception(f"request failed: {self.path}")
            try:
                self._send_json({"error": repr(e)},
                                HTTPStatus.INTERNAL_SERVER_ERROR)
            except Exception:
                pass

    def _route(self):
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        params = dict(parse_qsl(parts.query))
        if path == "/":
            return self._send_file(STATIC_DIR / "index.html")
        if path in API_ROUTES:
            try:
                return self._send_json(API_ROUTES[path](params))
            except ValueError as e:               # bad query params -> 400
                return self._send_json({"error": str(e)},
                                       HTTPStatus.BAD_REQUEST)
        if path.startswith("/api/bots/"):
            name = path[len("/api/bots/"):]
            try:
                return self._send_json(botinfo.detail(name))
            except KeyError:
                return self._send_json({"error": f"unknown bot: {name}"},
                                       HTTPStatus.NOT_FOUND)
        if path.startswith("/api/entry_chart/"):
            tid = path[len("/api/entry_chart/"):].removesuffix(".png")
            if not _TRADE_ID_RE.match(tid):
                return self._send_json({"error": f"bad trade id: {tid}"},
                                       HTTPStatus.BAD_REQUEST)
            theme = params.get("theme", "dark")
            if theme not in _CHART_THEMES:
                return self._send_json({"error": f"bad theme: {theme}"},
                                       HTTPStatus.BAD_REQUEST)
            from dashboard import render     # lazy: matplotlib is heavy
            png = render.cached_entry_chart(tid, theme)
            if png is None:
                return self._send_json({"error": f"unknown trade: {tid}"},
                                       HTTPStatus.NOT_FOUND)
            return self._send_bytes(png, "image/png", "no-cache")
        if path.startswith("/static/"):
            return self._send_file(STATIC_DIR / path[len("/static/"):])
        self._send_json({"error": f"not found: {path}"}, HTTPStatus.NOT_FOUND)

    def _send_json(self, payload: dict, status: int = HTTPStatus.OK):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str,
                    cache: str = "no-cache"):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path):
        resolved = path.resolve()
        if not resolved.is_relative_to(STATIC_DIR):       # traversal guard
            return self._send_json({"error": "forbidden"},
                                   HTTPStatus.FORBIDDEN)
        if not resolved.is_file():
            return self._send_json({"error": f"no such file: {path.name}"},
                                   HTTPStatus.NOT_FOUND)
        cache = ("max-age=3600" if "vendor" in resolved.parts
                 else "no-cache")
        self._send_bytes(resolved.read_bytes(),
                         _MIME.get(resolved.suffix, "application/octet-stream"),
                         cache)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Read-only p300 monitoring dashboard")
    ap.add_argument("--port", type=int, default=8300)
    ap.add_argument("--host", default="127.0.0.1",
                    help="Bind address (default localhost-only).")
    ap.add_argument("--open", action="store_true",
                    help="Open the dashboard in the default browser.")
    ap.add_argument("--verbose", action="store_true",
                    help="Log every request.")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

    try:
        httpd = ThreadingHTTPServer((args.host, args.port), _Handler)
    except OSError as e:
        print(f"cannot bind {args.host}:{args.port} — {e} "
              f"(dashboard already running? use --port)")
        return 2
    httpd.daemon_threads = True

    url = f"http://{args.host}:{args.port}"
    log.info(f"dashboard serving {url} (read-only; Ctrl+C to stop)")
    if args.open:
        threading.Timer(0.3, webbrowser.open_new_tab, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("shutdown")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

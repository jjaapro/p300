"""dashboard/server.py — HTTP smoke over the synthetic prod.db fixture."""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from dashboard import render, server

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dashboard_fixture import build_fixture_db  # noqa: E402


@pytest.fixture
def api(tmp_path, monkeypatch):
    p = build_fixture_db(tmp_path / "prod.db")
    monkeypatch.setattr("strategies.support.db.PROD_DB", p)
    monkeypatch.setattr(render, "CACHE_DIR", tmp_path / "cache")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server._Handler)
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def test_all_json_endpoints_respond(api):
    for path, key in [("/api/overview", "fleet"), ("/api/feeds", "tables"),
                      ("/api/trades?scope=open", "trades"),
                      ("/api/candles?asset=BTC&tf=1h", "bars"),
                      ("/api/bots", "bots")]:
        status, ctype, body = _get(api + path)
        assert status == 200, path
        assert "json" in ctype
        assert key in json.loads(body), path


def test_index_and_static(api):
    status, ctype, body = _get(api + "/")
    assert status == 200 and "html" in ctype and b"p300 dashboard" in body
    status, _, _ = _get(api + "/static/app.js")
    assert status == 200


def test_bot_detail_and_unknown(api):
    status, _, body = _get(api + "/api/bots/adx")
    assert status == 200
    d = json.loads(body)
    assert d["params"] and d["card_md"]
    status, _, _ = _get(api + "/api/bots/nope")
    assert status == 404


def test_bad_params_are_400(api):
    assert _get(api + "/api/candles?asset=BTC&tf=5m")[0] == 400
    assert _get(api + "/api/trades?scope=weird")[0] == 400
    assert _get(api + "/api/entry_chart/evil.png")[0] == 400


def test_entry_chart_png_and_unknown(api):
    status, ctype, body = _get(api + "/api/entry_chart/SJ-3.png")
    assert status == 200 and ctype == "image/png"
    assert body[:8] == b"\x89PNG\r\n\x1a\n"
    assert _get(api + "/api/entry_chart/SJ-424242.png")[0] == 404


def test_db_unavailable_is_500_not_crash(api, tmp_path, monkeypatch):
    # feed/bots down + db missing must degrade to a JSON 500, never kill
    # the server; and it must recover as soon as the db is back.
    import strategies.support.db as dbmod
    good = dbmod.PROD_DB
    monkeypatch.setattr("strategies.support.db.PROD_DB",
                        tmp_path / "gone.db")
    status, ctype, body = _get(api + "/api/feeds")
    assert status == 500 and "json" in ctype and b"error" in body
    monkeypatch.setattr("strategies.support.db.PROD_DB", good)
    assert _get(api + "/api/feeds")[0] == 200


def test_traversal_blocked(api):
    # urllib normalizes "..", so issue the raw request by hand
    import http.client
    host = api.removeprefix("http://")
    hostname, port = host.split(":")
    con = http.client.HTTPConnection(hostname, int(port), timeout=10)
    con.request("GET", "/static/../server.py")
    resp = con.getresponse()
    body = resp.read()
    con.close()
    assert resp.status in (403, 404)
    assert b"ThreadingHTTPServer" not in body

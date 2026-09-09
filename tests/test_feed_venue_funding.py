"""Tests for the OKX + Bybit funding-history feed (Track D6, 2026-09-08).

Network is never touched — every fetcher takes an injected `http_get`.
Same shape as tests/test_feed_coinbase.py / tests/test_feeds_track_d.py.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from strategies.support import clock
from strategies.support import db as _db_mod
from data.sources import venue_funding as vf


NOW = datetime(2026, 9, 8, 17, 30, tzinfo=timezone.utc)
S = 28800                              # one 8h settlement interval
BASE = 1788883200                      # 2026-09-08 16:00 UTC


@pytest.fixture
def tmp_prod(tmp_path, monkeypatch):
    path = (tmp_path / "prod.db").resolve()
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, path)
    monkeypatch.setattr(vf, "REQUEST_GAP_S", 0)
    vf._done.clear()
    yield path
    vf._done.clear()
    clock.set_simulated_now(None)


def _okx_payload(n=3, base=BASE, inst="BTC-USDT-SWAP"):
    """Newest-first, string fields, as OKX returns it."""
    return {"code": "0", "msg": "", "data": [
        {"instId": inst, "instType": "SWAP", "method": "current_period",
         "formulaType": "withRate",
         "fundingRate": f"{0.0001 * (i + 1):.10f}",
         "realizedRate": f"{0.0002 * (i + 1):.10f}",
         "fundingTime": str((base - i * S) * 1000)}
        for i in range(n)]}


def _bybit_payload(n=3, base=BASE, symbol="BTCUSDT"):
    """Newest-first, string fields, as Bybit returns it."""
    return {"retCode": 0, "retMsg": "OK", "result": {"category": "linear", "list": [
        {"symbol": symbol,
         "fundingRate": f"{0.00005 * (i + 1):.8f}",
         "fundingRateTimestamp": str((base - i * S) * 1000)}
        for i in range(n)]}}


# ─── parsing ──────────────────────────────────────────────────────────────────

def test_okx_parse_converts_ms_to_seconds_and_sorts_ascending():
    rows = vf.fetch_okx("BTC-USDT-SWAP", http_get=lambda url: _okx_payload())
    assert [r[0] for r in rows] == [BASE - 2 * S, BASE - S, BASE]
    assert rows[-1] == (BASE, pytest.approx(0.0001), pytest.approx(0.0002))
    assert all(t % S == 0 for t, _, _ in rows)          # 8h settlement grid


def test_bybit_parse_converts_ms_to_seconds_and_sorts_ascending():
    rows = vf.fetch_bybit("BTCUSDT", http_get=lambda url: _bybit_payload())
    assert [r[0] for r in rows] == [BASE - 2 * S, BASE - S, BASE]
    assert rows[-1] == (BASE, pytest.approx(0.00005))


def test_parsers_drop_rows_with_missing_or_unparseable_fields():
    bad = _okx_payload(n=1)
    bad["data"] += [
        {"fundingRate": "", "realizedRate": "", "fundingTime": str((BASE - S) * 1000)},
        {"fundingRate": "0.0001", "fundingTime": None},
        {"fundingRate": "0.0001", "fundingTime": "not-a-number"},
    ]
    assert [r[0] for r in vf.fetch_okx("X", http_get=lambda url: bad)] == [BASE]

    bad_b = _bybit_payload(n=1)
    bad_b["result"]["list"] += [
        {"symbol": "BTCUSDT", "fundingRate": None,
         "fundingRateTimestamp": str((BASE - S) * 1000)},
        {"symbol": "BTCUSDT", "fundingRate": "0.0001"},
    ]
    assert [r[0] for r in vf.fetch_bybit("BTCUSDT", http_get=lambda url: bad_b)] == [BASE]


def test_okx_empty_realized_rate_becomes_null_not_zero():
    p = _okx_payload(n=1)
    p["data"][0]["realizedRate"] = ""
    rows = vf.fetch_okx("X", http_get=lambda url: p)
    assert rows[0][2] is None and rows[0][1] == pytest.approx(0.0001)


def test_error_bodies_raise_instead_of_writing_garbage():
    with pytest.raises(RuntimeError):
        vf.fetch_okx("X", http_get=lambda url: {"code": "51001", "msg": "instrument doesn't exist"})
    with pytest.raises(RuntimeError):
        vf.fetch_bybit("X", http_get=lambda url: {"retCode": 10001, "retMsg": "params error"})


def test_request_urls_carry_instrument_limit_and_paging_cursor():
    seen: list[str] = []
    vf.fetch_okx("ETH-USDT-SWAP", after_ms=BASE * 1000, limit=100,
                 http_get=lambda url: (seen.append(url), {"code": "0", "data": []})[1])
    assert "instId=ETH-USDT-SWAP" in seen[0]
    assert "limit=100" in seen[0]
    assert f"after={BASE * 1000}" in seen[0]

    seen.clear()
    vf.fetch_bybit("ETHUSDT", end_ms=BASE * 1000 - 1, limit=200,
                   http_get=lambda url: (seen.append(url), {"retCode": 0, "result": {"list": []}})[1])
    assert "category=linear" in seen[0] and "symbol=ETHUSDT" in seen[0]
    assert "limit=200" in seen[0] and f"endTime={BASE * 1000 - 1}" in seen[0]


def test_limits_are_clamped_to_the_api_maximums():
    seen: list[str] = []
    vf.fetch_okx("X", limit=5000,
                 http_get=lambda url: (seen.append(url), {"code": "0", "data": []})[1])
    assert f"limit={vf.OKX_MAX_LIMIT}" in seen[0]
    seen.clear()
    vf.fetch_bybit("X", limit=5000,
                   http_get=lambda url: (seen.append(url), {"retCode": 0, "result": {"list": []}})[1])
    assert f"limit={vf.BYBIT_MAX_LIMIT}" in seen[0]


# ─── refresh ──────────────────────────────────────────────────────────────────

def _both(url):
    return _bybit_payload() if "bybit" in url else _okx_payload()


def test_refresh_upserts_every_instrument_and_declares_pks(tmp_prod):
    out = vf.refresh(now=NOW, http_get=_both)
    assert out == {"okx_BTC-USDT-SWAP": 3, "okx_ETH-USDT-SWAP": 3,
                   "bybit_BTCUSDT": 3, "bybit_ETHUSDT": 3}
    con = sqlite3.connect(str(tmp_prod))
    assert con.execute("SELECT COUNT(*) FROM okx_funding").fetchone()[0] == 6
    assert con.execute("SELECT COUNT(*) FROM bybit_funding").fetchone()[0] == 6
    assert con.execute(
        "SELECT inst_id, timestamp, funding_rate, realized_rate FROM okx_funding "
        "WHERE inst_id='BTC-USDT-SWAP' ORDER BY timestamp DESC").fetchone() == (
            "BTC-USDT-SWAP", BASE, pytest.approx(0.0001), pytest.approx(0.0002))
    assert con.execute(
        "SELECT symbol, timestamp, funding_rate FROM bybit_funding "
        "WHERE symbol='ETHUSDT' ORDER BY timestamp DESC").fetchone() == (
            "ETHUSDT", BASE, pytest.approx(0.00005))
    for table, cols in (("okx_funding", ["inst_id", "timestamp"]),
                        ("bybit_funding", ["symbol", "timestamp"])):
        info = con.execute(f"PRAGMA table_info({table})").fetchall()
        pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
        idx = [r for r in con.execute(f"PRAGMA index_list({table})") if r[3] == "pk"]
        assert pk == cols, table
        assert len(idx) == 1, table
    con.close()
    assert vf.latest("okx", "BTC-USDT-SWAP") == (BASE, pytest.approx(0.0001))
    assert vf.span("bybit", "BTCUSDT") == (3, BASE - 2 * S, BASE)


def test_refresh_throttles_to_one_pull_per_instrument_per_hour(tmp_prod):
    calls: list[str] = []

    def fake(url):
        calls.append(url)
        return _both(url)

    assert len(vf.refresh(now=NOW, http_get=fake)) == 4
    assert len(calls) == 4
    assert vf.refresh(now=NOW.replace(minute=59), http_get=fake) == {}
    assert len(calls) == 4
    assert len(vf.refresh(now=NOW.replace(hour=18), http_get=fake)) == 4
    assert len(calls) == 8
    # --force ignores the throttle (what the CLI/manual path uses)
    assert len(vf.refresh(now=NOW.replace(hour=18), force=True, http_get=fake)) == 4
    assert len(calls) == 12


def test_refresh_isolates_a_failing_venue(tmp_prod):
    def fake(url):
        if "okx" in url:
            raise RuntimeError("okx 503")
        return _bybit_payload()

    out = vf.refresh(now=NOW, http_get=fake)
    assert out == {"okx_BTC-USDT-SWAP": -1, "okx_ETH-USDT-SWAP": -1,
                   "bybit_BTCUSDT": 3, "bybit_ETHUSDT": 3}
    con = sqlite3.connect(str(tmp_prod))
    assert con.execute("SELECT COUNT(*) FROM okx_funding").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM bybit_funding").fetchone()[0] == 6
    con.close()
    # failed items are not marked done, so the next tick retries them
    assert not any(k.startswith("okx:") for k in vf._done)
    assert len([k for k in vf._done if k.startswith("bybit:")]) == 2


def test_refresh_isolates_a_single_failing_instrument(tmp_prod):
    def fake(url):
        if "ETHUSDT" in url:
            raise RuntimeError("bybit symbol unavailable")
        return _both(url)

    out = vf.refresh(now=NOW, http_get=fake)
    assert out["bybit_ETHUSDT"] == -1
    assert out["bybit_BTCUSDT"] == 3 and out["okx_ETH-USDT-SWAP"] == 3
    con = sqlite3.connect(str(tmp_prod))
    assert con.execute("SELECT DISTINCT symbol FROM bybit_funding").fetchall() == [("BTCUSDT",)]
    con.close()


def test_refresh_noops_under_simulated_clock(tmp_prod):
    clock.set_simulated_now(NOW)
    assert vf.refresh(
        http_get=lambda url: (_ for _ in ()).throw(AssertionError(url))) == {}


def test_refresh_replaces_a_revised_settlement_rather_than_duplicating(tmp_prod):
    vf.refresh(now=NOW, http_get=_both)
    vf._done.clear()
    revised = {"code": "0", "data": [{"fundingRate": "0.009", "realizedRate": "0.008",
                                      "fundingTime": str(BASE * 1000)}]}
    vf.refresh(now=NOW.replace(hour=18),
               http_get=lambda url: revised if "okx" in url else _bybit_payload())
    con = sqlite3.connect(str(tmp_prod))
    rows = con.execute("SELECT funding_rate, realized_rate FROM okx_funding "
                       "WHERE inst_id='BTC-USDT-SWAP' AND timestamp=?", (BASE,)).fetchall()
    total = con.execute("SELECT COUNT(*) FROM okx_funding "
                        "WHERE inst_id='BTC-USDT-SWAP'").fetchone()[0]
    con.close()
    assert rows == [(pytest.approx(0.009), pytest.approx(0.008))]
    assert total == 3                       # replaced in place, not appended


# ─── backfill (CLI only) ──────────────────────────────────────────────────────

def test_refresh_never_calls_backfill():
    import inspect
    for fn in (vf.refresh, vf.refresh_okx, vf.refresh_bybit):
        assert "backfill" not in inspect.getsource(fn)


def test_backfill_pages_backwards_until_the_api_runs_out(tmp_prod, monkeypatch):
    # one instrument per venue keeps the page bookkeeping below readable
    monkeypatch.setattr(vf, "OKX_INSTRUMENTS", ("BTC-USDT-SWAP",))
    monkeypatch.setattr(vf, "BYBIT_SYMBOLS", ("BTCUSDT",))
    urls: list[str] = []

    def fake(url):
        urls.append(url)
        page = len([u for u in urls if ("okx" in u) == ("okx" in url)]) - 1
        if page >= 2:                       # third page is empty -> stop
            return ({"code": "0", "data": []} if "okx" in url
                    else {"retCode": 0, "result": {"list": []}})
        base = BASE - page * 3 * S
        return _okx_payload(3, base) if "okx" in url else _bybit_payload(3, base)

    out = vf.backfill(http_get=fake)
    assert out == {"okx_BTC-USDT-SWAP": 6, "bybit_BTCUSDT": 6}
    # paging cursors: OKX `after` = oldest row of the previous page,
    # Bybit `endTime` = oldest row - 1 ms
    okx_urls = [u for u in urls if "okx" in u]
    assert "after=" not in okx_urls[0]
    assert f"after={(BASE - 2 * S) * 1000}" in okx_urls[1]
    bybit_urls = [u for u in urls if "bybit" in u]
    assert "endTime=" not in bybit_urls[0]
    assert f"endTime={(BASE - 2 * S) * 1000 - 1}" in bybit_urls[1]
    con = sqlite3.connect(str(tmp_prod))
    assert con.execute("SELECT COUNT(*) FROM okx_funding").fetchone()[0] == 6
    assert con.execute("SELECT COUNT(*) FROM bybit_funding").fetchone()[0] == 6
    con.close()


def test_backfill_respects_the_since_floor(tmp_prod):
    floor = BASE - S
    out = vf.backfill(floor, http_get=_both)
    assert out == {"okx_BTC-USDT-SWAP": 2, "okx_ETH-USDT-SWAP": 2,
                   "bybit_BTCUSDT": 2, "bybit_ETHUSDT": 2}
    con = sqlite3.connect(str(tmp_prod))
    assert con.execute("SELECT MIN(timestamp) FROM okx_funding").fetchone()[0] == floor
    assert con.execute("SELECT MIN(timestamp) FROM bybit_funding").fetchone()[0] == floor
    con.close()


def test_backfill_isolates_a_failing_instrument(tmp_prod):
    def fake(url):
        if "BTC-USDT-SWAP" in url:
            raise RuntimeError("okx down")
        return _both(url)

    out = vf.backfill(BASE - S, http_get=fake)
    assert out["okx_BTC-USDT-SWAP"] == -1
    assert out["okx_ETH-USDT-SWAP"] == 2 and out["bybit_BTCUSDT"] == 2


# ─── wiring ───────────────────────────────────────────────────────────────────

def test_tables_are_registered_everywhere_the_repo_expects():
    import bootstrap
    import botlib
    from data import check_gaps
    from data.sources import binance

    for t in ("okx_funding", "bybit_funding"):
        assert t in botlib.FRESHNESS_CONTRACTS
        assert botlib.FRESHNESS_CONTRACTS[t] == ("timestamp", 1.0, 10 * 3600)
        assert t in bootstrap.SCHEMAS
        assert "PRIMARY KEY" in bootstrap.SCHEMAS[t]
    specs = {s.table: s for s in check_gaps.SPECS}
    assert specs["okx_funding"].cadence_seconds == S
    assert specs["okx_funding"].asset_col == "inst_id"
    assert specs["bybit_funding"].cadence_seconds == S
    assert specs["bybit_funding"].asset_col == "symbol"
    import inspect
    assert "venue_funding" in inspect.getsource(binance.refresh_all)


def test_bootstrap_ddl_matches_the_feed_module_ddl():
    """bootstrap.py and the feed must not drift — CREATE TABLE IF NOT EXISTS
    would silently keep whichever shape got there first."""
    import bootstrap
    norm = lambda s: " ".join(s.split())            # noqa: E731
    feed = {norm(d) for d in vf.DDL}
    assert norm(bootstrap.SCHEMAS["okx_funding"]) in feed
    assert norm(bootstrap.SCHEMAS["bybit_funding"]) in feed

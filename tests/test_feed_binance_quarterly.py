"""Tests for the Binance quarterly-futures 1h feed (Track D5, 2026-09-08).

Network is never touched — every fetcher takes an injected `http_get`.
Same shape as tests/test_feed_coinbase.py.
"""
from __future__ import annotations

import inspect
import re
import sqlite3
from datetime import datetime, timezone

import pytest

from strategies.support import clock
from strategies.support import db as _db_mod
from data.sources import binance_quarterly as bq


NOW = datetime(2026, 9, 8, 17, 30, tzinfo=timezone.utc)
H = 3600
BASE_TS = 1788886800                       # 2026-09-08 17:00 UTC


@pytest.fixture
def tmp_prod(tmp_path, monkeypatch):
    path = (tmp_path / "prod.db").resolve()
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, path)
    monkeypatch.setattr(bq, "REQUEST_GAP_S", 0)
    bq._done.clear()
    yield path
    bq._done.clear()
    clock.set_simulated_now(None)


def _kline(ts_s, o, h, l, c, v):
    """Binance wire shape: ms open time, string numbers, 12 fields."""
    return [ts_s * 1000, str(o), str(h), str(l), str(c), str(v),
            ts_s * 1000 + H * 1000 - 1, "58885.6", 114, "0.33", "26184.7", "0"]


def _payload(base_ts=BASE_TS, n=3):
    return [_kline(base_ts + i * H, 100 + i, 110 + i, 90 + i, 105 + i, 1.5 + i)
            for i in range(n)]


def _exchange_info():
    return {"symbols": [
        {"symbol": "BTCUSDT_260925", "pair": "BTCUSDT",
         "contractType": "CURRENT_QUARTER",
         "deliveryDate": 1790323200000, "onboardDate": 1774598400000},
        {"symbol": "BTCUSDT_261225", "pair": "BTCUSDT",
         "contractType": "NEXT_QUARTER",
         "deliveryDate": 1798185600000, "onboardDate": 1782460800000},
        # noise the filter must drop: the perp, and a pair we do not track
        {"symbol": "BTCUSDT", "pair": "BTCUSDT", "contractType": "PERPETUAL",
         "deliveryDate": 4133404800000, "onboardDate": 1569398400000},
        {"symbol": "SOLUSDT_260925", "pair": "SOLUSDT",
         "contractType": "CURRENT_QUARTER",
         "deliveryDate": 1790323200000, "onboardDate": 1774598400000},
    ]}


# ─── parsing ──────────────────────────────────────────────────────────────────

def test_parse_casts_strings_and_seconds_and_sorts_ascending():
    rows = bq.fetch_klines("BTCUSDT", "CURRENT_QUARTER",
                           http_get=lambda url: list(reversed(_payload())))
    assert [r[0] for r in rows] == [BASE_TS, BASE_TS + H, BASE_TS + 2 * H]
    # (timestamp, open, high, low, close, volume)
    assert rows[0] == (BASE_TS, 100.0, 110.0, 90.0, 105.0, 1.5)
    assert rows[2] == (BASE_TS + 2 * H, 102.0, 112.0, 92.0, 107.0, 3.5)


def test_parse_drops_off_hour_and_malformed_rows():
    payload = _payload(n=1) + [_kline(BASE_TS + 90, 1, 2, 1, 2, 3)] + [[BASE_TS + H, "1"]]
    rows = bq.fetch_klines("BTCUSDT", "CURRENT_QUARTER", http_get=lambda url: payload)
    assert [r[0] for r in rows] == [BASE_TS]


def test_request_url_carries_pair_contract_type_interval_and_ms_bounds():
    seen: list[str] = []
    bq.fetch_klines("ETHUSDT", "NEXT_QUARTER", start_ts=BASE_TS,
                    end_ts=BASE_TS + 2 * H, limit=1500,
                    http_get=lambda url: (seen.append(url), [])[1])
    assert "continuousKlines" in seen[0]
    assert "pair=BTCUSDT" not in seen[0]
    assert "pair=ETHUSDT" in seen[0]
    assert "contractType=NEXT_QUARTER" in seen[0]
    assert "interval=1h" in seen[0]
    assert "limit=1500" in seen[0]
    assert f"startTime={BASE_TS * 1000}" in seen[0]
    assert f"endTime={(BASE_TS + 2 * H) * 1000}" in seen[0]


def test_error_body_raises_instead_of_writing_garbage():
    with pytest.raises(RuntimeError):
        bq.fetch_klines("BTCUSDT", "CURRENT_QUARTER",
                        http_get=lambda url: {"code": -1121, "msg": "Invalid symbol."})


# ─── refresh ──────────────────────────────────────────────────────────────────

def test_refresh_upserts_every_slot_and_declares_pk(tmp_prod):
    def fake(url):
        return _exchange_info() if "exchangeInfo" in url else _payload()

    out = bq.refresh(now=NOW, http_get=fake)
    assert out == {"BTCUSDT-CURRENT_QUARTER": 3, "BTCUSDT-NEXT_QUARTER": 3,
                   "ETHUSDT-CURRENT_QUARTER": 3, "ETHUSDT-NEXT_QUARTER": 3,
                   "contracts": 2}
    con = sqlite3.connect(str(tmp_prod))
    assert con.execute("SELECT COUNT(*) FROM binance_quarterly_1h").fetchone()[0] == 12
    assert con.execute(
        "SELECT pair, contract_type, timestamp, open, high, low, close, volume "
        "FROM binance_quarterly_1h WHERE pair='ETHUSDT' AND contract_type='NEXT_QUARTER' "
        "ORDER BY timestamp").fetchone() == (
            "ETHUSDT", "NEXT_QUARTER", BASE_TS, 100.0, 110.0, 90.0, 105.0, 1.5)

    pk = [r[1] for r in con.execute("PRAGMA table_info(binance_quarterly_1h)") if r[5]]
    idx = [r for r in con.execute("PRAGMA index_list(binance_quarterly_1h)") if r[3] == "pk"]
    cpk = [r[1] for r in con.execute("PRAGMA table_info(binance_quarterly_contracts)") if r[5]]
    cidx = [r for r in con.execute("PRAGMA index_list(binance_quarterly_contracts)")
            if r[3] == "pk"]
    con.close()
    assert pk == ["pair", "contract_type", "timestamp"]
    assert len(idx) == 1
    assert cpk == ["symbol"]
    assert len(cidx) == 1
    assert bq.latest("BTCUSDT", "CURRENT_QUARTER") == (BASE_TS + 2 * H, 107.0)


def test_series_generated_column_is_the_check_gaps_group(tmp_prod):
    """check_gaps groups on a single column; `series` is what makes a spec
    possible for a table whose natural group is (pair, contract_type)."""
    bq.refresh(now=NOW, http_get=lambda url:
               _exchange_info() if "exchangeInfo" in url else _payload())
    con = sqlite3.connect(str(tmp_prod))
    assert [r[0] for r in con.execute(
        "SELECT DISTINCT series FROM binance_quarterly_1h ORDER BY series")] == [
        "BTCUSDT-CURRENT_QUARTER", "BTCUSDT-NEXT_QUARTER",
        "ETHUSDT-CURRENT_QUARTER", "ETHUSDT-NEXT_QUARTER"]
    assert [r[0] for r in con.execute(
        "SELECT timestamp FROM binance_quarterly_1h WHERE series=? ORDER BY timestamp",
        ("ETHUSDT-CURRENT_QUARTER",))] == [BASE_TS, BASE_TS + H, BASE_TS + 2 * H]
    con.close()


def test_contracts_filters_to_our_pairs_and_quarterly_types(tmp_prod):
    n = bq.fetch_contracts(now=NOW, http_get=lambda url: _exchange_info())
    assert n == 2
    con = sqlite3.connect(str(tmp_prod))
    rows = con.execute(
        "SELECT symbol, pair, contract_type, delivery_ts, onboard_ts, first_seen_ts, "
        "last_seen_ts FROM binance_quarterly_contracts ORDER BY symbol").fetchall()
    con.close()
    now_ts = int(NOW.timestamp())
    assert rows == [
        ("BTCUSDT_260925", "BTCUSDT", "CURRENT_QUARTER", 1790323200, 1774598400,
         now_ts, now_ts),
        ("BTCUSDT_261225", "BTCUSDT", "NEXT_QUARTER", 1798185600, 1782460800,
         now_ts, now_ts)]


def test_contracts_keeps_first_seen_and_advances_last_seen(tmp_prod):
    bq.fetch_contracts(now=NOW, http_get=lambda url: _exchange_info())
    later = NOW.replace(day=9)
    bq.fetch_contracts(now=later, http_get=lambda url: _exchange_info())
    con = sqlite3.connect(str(tmp_prod))
    row = con.execute("SELECT first_seen_ts, last_seen_ts FROM "
                      "binance_quarterly_contracts WHERE symbol='BTCUSDT_260925'").fetchone()
    n = con.execute("SELECT COUNT(*) FROM binance_quarterly_contracts").fetchone()[0]
    con.close()
    assert row == (int(NOW.timestamp()), int(later.timestamp()))
    assert n == 2


def test_refresh_throttles_klines_hourly_and_contracts_daily(tmp_prod):
    calls: list[str] = []

    def fake(url):
        calls.append(url)
        return _exchange_info() if "exchangeInfo" in url else _payload()

    assert len(bq.refresh(now=NOW, http_get=fake)) == 5
    assert len(calls) == 5                                   # 4 slots + exchangeInfo
    assert bq.refresh(now=NOW.replace(minute=59), http_get=fake) == {}
    assert len(calls) == 5
    # next hour, same day: klines only
    out = bq.refresh(now=NOW.replace(hour=18), http_get=fake)
    assert set(out) == {f"{p}-{c}" for p, c in bq.SERIES}
    assert len(calls) == 9
    # next day: klines + contracts again
    out = bq.refresh(now=NOW.replace(day=9, hour=1), http_get=fake)
    assert "contracts" in out
    assert len(calls) == 14


def test_refresh_isolates_a_failing_slot(tmp_prod):
    def fake(url):
        if "ETHUSDT" in url and "NEXT_QUARTER" in url:
            raise RuntimeError("binance 503")
        return _exchange_info() if "exchangeInfo" in url else _payload()

    out = bq.refresh(now=NOW, http_get=fake)
    assert out == {"BTCUSDT-CURRENT_QUARTER": 3, "BTCUSDT-NEXT_QUARTER": 3,
                   "ETHUSDT-CURRENT_QUARTER": 3, "ETHUSDT-NEXT_QUARTER": -1,
                   "contracts": 2}
    con = sqlite3.connect(str(tmp_prod))
    assert con.execute("SELECT COUNT(*) FROM binance_quarterly_1h").fetchone()[0] == 9
    con.close()
    # the failed slot is not marked done, so the next tick retries it
    assert "ETHUSDT-NEXT_QUARTER:2026-09-08:17" not in bq._done
    assert "BTCUSDT-NEXT_QUARTER:2026-09-08:17" in bq._done


def test_refresh_isolates_a_failing_exchange_info(tmp_prod):
    def fake(url):
        if "exchangeInfo" in url:
            raise RuntimeError("binance 418")
        return _payload()

    out = bq.refresh(now=NOW, http_get=fake)
    assert out["contracts"] == -1
    assert out["BTCUSDT-CURRENT_QUARTER"] == 3
    con = sqlite3.connect(str(tmp_prod))
    assert con.execute("SELECT COUNT(*) FROM binance_quarterly_1h").fetchone()[0] == 12
    con.close()


def test_refresh_noops_under_simulated_clock(tmp_prod):
    clock.set_simulated_now(NOW)
    assert bq.refresh(
        http_get=lambda url: (_ for _ in ()).throw(AssertionError(url))) == {}


def test_refresh_replaces_the_previously_partial_bar(tmp_prod):
    bq.refresh(now=NOW, http_get=lambda url:
               _exchange_info() if "exchangeInfo" in url
               else [_kline(BASE_TS, 1, 2, 1, 2, 0.1)])
    bq._done.clear()
    bq.refresh(now=NOW.replace(hour=18), http_get=lambda url:
               _exchange_info() if "exchangeInfo" in url
               else [_kline(BASE_TS, 1, 9, 1, 8, 42.0)])
    con = sqlite3.connect(str(tmp_prod))
    rows = con.execute("SELECT close, volume FROM binance_quarterly_1h "
                       "WHERE pair='BTCUSDT' AND contract_type='CURRENT_QUARTER'").fetchall()
    con.close()
    assert rows == [(8.0, 42.0)]


# ─── backfill (CLI only) ──────────────────────────────────────────────────────

def test_backfill_pages_forward_and_is_not_called_by_refresh(tmp_prod):
    assert "backfill" not in inspect.getsource(bq.refresh)
    assert "backfill" not in inspect.getsource(bq.refresh_series)

    starts: list[int] = []
    pages = {}

    def fake(url):
        start = int(re.search(r"startTime=(\d+)", url).group(1))
        starts.append(start)
        # one full page then one short page then empty
        key = url.split("pair=")[1].split("&")[0] + url.split("contractType=")[1][:4]
        pages[key] = pages.get(key, 0) + 1
        if pages[key] == 1:
            return _payload(start // 1000, n=3)
        return []

    now = datetime.fromtimestamp(BASE_TS + 10 * H, tz=timezone.utc)
    out = bq.backfill(BASE_TS, now=now, http_get=fake)
    assert out == {f"{p}-{c}": 3 for p, c in bq.SERIES}
    assert len(starts) == 8                       # 2 pages x 4 slots
    assert starts[0] == BASE_TS * 1000
    assert starts[1] == (BASE_TS + 3 * H) * 1000  # resumes after the last bar, no overlap


def test_backfill_default_start_resumes_after_last_stored_bar(tmp_prod):
    con = sqlite3.connect(str(tmp_prod))
    bq.ensure_schema(con)
    con.execute("INSERT INTO binance_quarterly_1h (pair, contract_type, timestamp, "
                "open, high, low, close, volume) VALUES "
                "('BTCUSDT', 'CURRENT_QUARTER', ?, 1, 2, 0, 1.5, 9)", (BASE_TS,))
    con.commit()
    con.close()
    starts: list[int] = []
    now = datetime.fromtimestamp(BASE_TS + 5 * H, tz=timezone.utc)
    bq.backfill(now=now, http_get=lambda url: (
        starts.append(int(re.search(r"startTime=(\d+)", url).group(1))), [])[1])
    assert starts[0] == (BASE_TS + H) * 1000                    # last bar + 1h
    assert starts[1] == bq.DEFAULT_BACKFILL_START * 1000         # untouched slot


def test_backfill_isolates_a_failing_slot(tmp_prod):
    seen = {}

    def fake(url):
        if "BTCUSDT" in url and "CURRENT_QUARTER" in url:
            raise RuntimeError("binance down")
        key = url.split("pair=")[1].split("&")[0] + url.split("contractType=")[1][:4]
        seen[key] = seen.get(key, 0) + 1
        return _payload(n=3) if seen[key] == 1 else []

    now = datetime.fromtimestamp(BASE_TS + 10 * H, tz=timezone.utc)
    out = bq.backfill(BASE_TS, now=now, http_get=fake)
    assert out["BTCUSDT-CURRENT_QUARTER"] == -1
    assert out["BTCUSDT-NEXT_QUARTER"] == 3
    assert out["ETHUSDT-CURRENT_QUARTER"] == 3


def test_backfill_stops_on_a_non_advancing_page(tmp_prod):
    """A source that keeps returning bars before the cursor must not spin."""
    calls = []
    bq.backfill(BASE_TS, now=datetime.fromtimestamp(BASE_TS + 10 * H, tz=timezone.utc),
                http_get=lambda url: (calls.append(url), [_kline(BASE_TS - 10 * H,
                                                                1, 2, 1, 2, 1)])[1])
    assert len(calls) == len(bq.SERIES)


# ─── wiring ───────────────────────────────────────────────────────────────────

def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def test_tables_are_registered_everywhere():
    import bootstrap
    import botlib
    from data import check_gaps
    from data.sources import binance

    for t in (bq.TABLE, bq.CONTRACTS_TABLE):
        assert t in bootstrap.SCHEMAS
        assert t in botlib.FRESHNESS_CONTRACTS
    assert _norm(bootstrap.SCHEMAS[bq.TABLE]) == _norm(bq.DDL_KLINES)
    assert _norm(bootstrap.SCHEMAS[bq.CONTRACTS_TABLE]) == _norm(bq.DDL_CONTRACTS)
    assert botlib.FRESHNESS_CONTRACTS[bq.TABLE][0] == "timestamp"
    assert botlib.FRESHNESS_CONTRACTS[bq.CONTRACTS_TABLE][0] == "last_seen_ts"
    spec = [s for s in check_gaps.SPECS if s.table == bq.TABLE]
    assert len(spec) == 1
    assert (spec[0].time_col, spec[0].asset_col, spec[0].cadence_seconds) == (
        "timestamp", "series", 3600)
    assert "binance_quarterly" in inspect.getsource(binance.refresh_all)

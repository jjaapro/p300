"""Cross-resolution detection and atomic, recoverable spot-minute repair."""
import hashlib
import io
import sqlite3
import zipfile
from datetime import datetime, timezone

import pytest

from data import repair_minute_candles as repair


def archive(month, rows):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(f"BTCUSDT-1m-{month}.csv", "\n".join(",".join(map(str, r)) for r in rows))
    return out.getvalue()


def kline(ts, *, scale=1, duration=60000):
    return [ts * scale, 100, 102, 99, 101, 2,
            (ts + duration) * scale - 1, 200, 5, 1, 100, 0]


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "prod.db"
    ts = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp()) * 1000
    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE btc_1m {repair.ROW_SCHEMA}")
    con.execute("CREATE TABLE cd_spot_15m (timestamp INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL, total_trades INTEGER)")
    # All values are valid OHLC, but the first row covers fifteen minutes.
    for opened in [ts, ts + 900000]:
        con.execute("INSERT INTO btc_1m VALUES (?,?,?,?,?,?,?)", (opened, 100, 110, 90, 105, 30, 100))
        con.execute("INSERT INTO cd_spot_15m VALUES (?,?,?,?,?,?,?)", (opened // 1000, 100, 110, 90, 105, 30, 100))
        con.execute("INSERT INTO btc_1m VALUES (?,?,?,?,?,?,?)", (opened + 60000, 101, 102, 100, 101, 1, 2))
    con.commit()
    con.close()
    return path, ts


def downloader(month, _):
    ts = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp()) * 1000
    payload = archive(month, [kline(ts), kline(ts + 900000)])
    return payload, "https://example.invalid/test", hashlib.sha256(payload).hexdigest()


def snapshot(path):
    con = repair.connect_readonly(path)
    try:
        return con.execute("SELECT * FROM btc_1m ORDER BY open_time").fetchall()
    finally:
        con.close()


def test_detects_valid_ohlc_with_wrong_resolution_and_health_fails(source):
    import health
    path, _ = source
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM btc_1m WHERE low>high OR close<low OR close>high").fetchone()[0] == 0
        assert len(repair.find_mislabeled_minutes(con)) == 2
        failures = []
        health.check_minute_resolution(con, failures)
    assert len(failures) == 1
    assert "fifteen-minute" in failures[0]


def test_first_minute_only_trading_is_not_corruption(source):
    path, ts = source
    with sqlite3.connect(path) as con:
        con.execute("UPDATE btc_1m SET volume=0 WHERE open_time%900000!=0")
        assert repair.find_mislabeled_minutes(con) == []


@pytest.mark.parametrize("year,scale", [(2024, 1), (2025, 1000)])
def test_parses_millisecond_and_microsecond_archives(year, scale):
    ts = int(datetime(year, 1, 15, tzinfo=timezone.utc).timestamp()) * 1000
    month = f"{year}-01"
    result = repair.parse_archive(archive(month, [kline(ts, scale=scale)]), month, {ts})
    assert result[ts] == (ts, 100, 102, 99, 101, 2, 5)


def test_archive_halt_candle_can_end_early_inside_its_minute(source):
    _, ts = source
    row = kline(ts)
    row[6] = ts + 32286
    assert repair.parse_archive(archive("2024-01", [row]), "2024-01", {ts})[ts][0] == ts


def test_unselected_archive_anomaly_does_not_enter_replacements(source):
    _, ts = source
    unrelated = kline(ts + 60000)
    unrelated[6] = ts - 1000
    rows = repair.parse_archive(archive("2024-01", [kline(ts), unrelated]), "2024-01", {ts})
    assert list(rows) == [ts]


@pytest.mark.parametrize("failure", ["duration", "micro_duration", "duplicate", "nan", "month", "missing", "alignment", "ohlc", "columns"])
def test_rejects_bad_archives(source, failure):
    _, ts = source
    rows = [kline(ts)]
    if failure == "duration": rows = [kline(ts, duration=900000)]
    if failure == "micro_duration": rows = [kline(ts, scale=1000, duration=900000)]
    if failure == "duplicate": rows *= 2
    if failure == "nan": rows[0][2] = "NaN"
    if failure == "month": rows = [kline(ts + 31 * 86400000)]
    if failure == "missing": rows = []
    if failure == "alignment": rows = [kline(ts + 1)]
    if failure == "ohlc": rows[0][2] = 95
    if failure == "columns": rows[0].pop()
    with pytest.raises(ValueError):
        repair.parse_archive(archive("2024-01", rows), "2024-01", {ts})


def test_prepare_apply_preserves_originals_unrelated_rows_and_is_idempotent(source, tmp_path):
    path, ts = source
    before = snapshot(path)
    stage = tmp_path / "recovery.db"
    assert repair.prepare_repair(path, stage, tmp_path / "cache", downloader=downloader) == 2
    assert snapshot(path) == before
    assert repair.apply_repair(path, stage) == 2
    after = snapshot(path)
    assert len(after) == len(before)
    assert after[1] == before[1] and after[3] == before[3]
    assert after[0][1:] == (100, 102, 99, 101, 2, 5)
    with sqlite3.connect(stage) as con:
        assert con.execute("SELECT * FROM originals ORDER BY open_time").fetchall() == [before[0], before[2]]
        assert con.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
    assert repair.apply_repair(path, stage) == 0
    assert repair.prepare_repair(path, tmp_path / "unused.db", tmp_path, downloader=downloader) == 0


def test_missing_replacement_never_changes_source(source, tmp_path):
    path, ts = source
    before = snapshot(path)
    def incomplete(month, _):
        return archive(month, [kline(ts)]), "url", "hash"
    with pytest.raises(ValueError, match="missing"):
        repair.prepare_repair(path, tmp_path / "stage.db", tmp_path, downloader=incomplete)
    assert snapshot(path) == before


def test_changed_original_refuses_entire_transaction(source, tmp_path):
    path, ts = source
    stage = tmp_path / "stage.db"
    repair.prepare_repair(path, stage, tmp_path, downloader=downloader)
    with sqlite3.connect(path) as con:
        con.execute("UPDATE btc_1m SET close=104 WHERE open_time=?", (ts + 900000,))
    before = snapshot(path)
    with pytest.raises(ValueError, match="changed"):
        repair.apply_repair(path, stage)
    assert snapshot(path) == before


def test_sql_failure_rolls_back_every_update(source, tmp_path):
    path, ts = source
    stage = tmp_path / "stage.db"
    repair.prepare_repair(path, stage, tmp_path, downloader=downloader)
    with sqlite3.connect(path) as con:
        con.execute(f"CREATE TRIGGER fail_second BEFORE UPDATE ON btc_1m WHEN OLD.open_time={ts + 900000} BEGIN SELECT RAISE(ABORT,'injected'); END")
    before = snapshot(path)
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        repair.apply_repair(path, stage)
    assert snapshot(path) == before


def test_different_database_and_existing_stage_refused(source, tmp_path):
    path, _ = source
    stage = tmp_path / "stage.db"
    repair.prepare_repair(path, stage, tmp_path, downloader=downloader)
    with pytest.raises(ValueError, match="new, separate"):
        repair.prepare_repair(path, stage, tmp_path, downloader=downloader)
    with pytest.raises(ValueError, match="different source"):
        repair.apply_repair(tmp_path / "other.db", stage)


def test_bad_checksum_never_cached(tmp_path, monkeypatch):
    filename = "BTCUSDT-1m-2024-01.zip"
    def fake_urlopen(url, **_):
        return io.BytesIO(("0" * 64 + "  " + filename).encode() if url.endswith("CHECKSUM") else b"bad archive")
    monkeypatch.setattr(repair, "urlopen", fake_urlopen)
    with pytest.raises(ValueError, match="SHA-256"):
        repair.download_archive("2024-01", tmp_path)
    assert not (tmp_path / filename).exists()


def test_absent_comparison_is_unavailable():
    with sqlite3.connect(":memory:") as con:
        with pytest.raises(repair.ComparisonUnavailable):
            repair.find_mislabeled_minutes(con)

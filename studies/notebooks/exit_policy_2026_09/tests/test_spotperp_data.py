"""Fixtures for the spot-vs-perp data build (PREREGISTRATION_SPOT_PERP.md section 4, precondition P7).

Synthetic archive zips on a ten-minute grid; each test changes only the quirk it is about. Every boundary asserts
both the included and the excluded twin, so a fixture cannot freeze the wrong side of a rule.
"""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import spotperp_data as SD  # noqa: E402

T0 = 1_577_836_800          # 2020-01-01 00:00 UTC, divisible by 60
N = 10


def row(minute: int, unit: str = "ms", price: float = 100.0, volume: float = 2.0,
        close_delta: int | None = None, taker: float = 1.0) -> str:
    """One archive row for the given minute index on the fixture grid."""
    mul = SD.UNIT_DIVISOR[unit]
    ot = (T0 + 60 * minute) * mul
    ct = ot + (SD.IDENTITY[unit] if close_delta is None else close_delta)
    return (f"{ot},{price},{price + 1},{price - 1},{price + 0.5},{volume},{ct},"
            f"{volume * price},7,{taker},{taker * price},0")


def make_zip(path: Path, rows: list[str], header: bool = False) -> None:
    text = (",".join(SD.AD.KLINE_COLUMNS) + "\n" if header else "") + "\n".join(rows) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(path.stem + ".csv", text)


def manifest_for(raw: Path, files: list[tuple[str, str, Path]]) -> dict:
    return {"files": [{"symbol": s, "kind": k, "file": f"{k}/{p.name}",
                       "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for s, k, p in files]}


def build(tmp_path: Path, rows: list[str], header: bool = False, symbol: str = "TESTUSDT",
          kind: str = "spot_1m", name: str = "TESTUSDT-1m-2020-01.zip", extra: list[tuple[str, list[str]]] = ()) -> tuple[dict, dict]:
    """Build one fixture panel; returns (arrays, sidecar)."""
    raw, cache = tmp_path / "raw", tmp_path / "cache"
    files = [(symbol, kind, raw / kind / name)]
    make_zip(files[0][2], rows, header)
    for extra_name, extra_rows in extra:
        p = raw / kind / extra_name
        make_zip(p, extra_rows)
        files.append((symbol, kind, p))
    man = manifest_for(raw, files)
    meta = SD.build_one(symbol, kind, man, t0_s=T0, n=N, cache=cache, raw=raw)
    with np.load(cache / f"{symbol}_{'spot' if kind == 'spot_1m' else 'premium'}_1m_panel.npz") as z:
        arrays = {k: z[k] for k in z.files}
    return arrays, meta


# --- frozen constants -------------------------------------------------------------------------------

def test_frozen_grid_constants():
    assert (SD.T0_S, SD.N_MINUTES) == (1_577_836_800, 3_525_120)
    assert SD.IDENTITY == {"ms": 59_999, "us": 59_999_999}
    assert SD.US_THRESHOLD == 10 ** 14
    assert SD.SPOT_FIELDS == ("open", "high", "low", "close", "volume", "quote_volume",
                              "taker_buy_volume", "taker_buy_quote_volume")


def test_perp_grid_facts_match_the_orb_panel():
    facts = SD.perp_grid_facts()
    assert facts["t0_s"] == SD.T0_S and facts["minutes"] == SD.N_MINUTES
    assert len(facts["perp_panel_logical_sha256"]) == 64


# --- format quirks ----------------------------------------------------------------------------------

def test_header_and_no_header_parse_identically(tmp_path):
    rows = [row(m) for m in range(3)]
    a, _ = build(tmp_path / "a", rows, header=False)
    b, meta_b = build(tmp_path / "b", rows, header=True)
    assert meta_b["per_file"][0]["header"] is True
    for field in SD.SPOT_FIELDS:
        assert np.array_equal(a[field], b[field], equal_nan=True)


def test_microsecond_and_millisecond_files_land_on_the_same_minutes(tmp_path):
    ms, meta_ms = build(tmp_path / "ms", [row(m, "ms") for m in range(3)])
    us, meta_us = build(tmp_path / "us", [row(m, "us") for m in range(3)])
    assert meta_ms["per_file"][0]["unit"] == "ms" and meta_us["per_file"][0]["unit"] == "us"
    assert meta_us["unit_switch_first_us_file"] is not None and meta_ms["unit_switch_first_us_file"] is None
    for field in SD.SPOT_FIELDS:
        assert np.array_equal(ms[field], us[field], equal_nan=True)


def test_a_file_mixing_units_fails(tmp_path):
    with pytest.raises(RuntimeError, match="mixed timestamp units"):
        build(tmp_path, [row(0, "ms"), row(1, "us")])


def test_off_grid_and_outside_panel_rows_are_dropped_and_counted(tmp_path):
    rows = [row(0), row(1).replace(f"{(T0 + 60) * 1000},", f"{(T0 + 60) * 1000 + 30_000},"), row(N + 5), row(-3)]
    arrays, meta = build(tmp_path, rows)
    assert meta["per_file"][0]["off_grid"] == 1
    assert meta["per_file"][0]["outside_panel"] == 2
    assert np.isfinite(arrays["open"][0]) and not np.isfinite(arrays["open"][1])
    assert meta["minutes_present"] == 1


def test_identical_duplicate_is_dropped_and_counted(tmp_path):
    arrays, meta = build(tmp_path, [row(0), row(1), row(1)])
    assert meta["per_file"][0]["duplicates_in_file"] == 1
    assert meta["totals"]["conflicting_duplicates"] == 0
    assert meta["minutes_present"] == 2


def test_conflicting_duplicate_in_one_file_fails_the_build(tmp_path):
    with pytest.raises(RuntimeError, match="conflicting duplicate"):
        build(tmp_path, [row(0), row(1, price=100.0), row(1, price=101.0)])


def test_conflicting_duplicate_across_files_fails_the_build(tmp_path):
    with pytest.raises(RuntimeError, match="conflicting duplicate"):
        build(tmp_path, [row(1, price=100.0)], extra=[("TESTUSDT-1m-2020-02.zip", [row(1, price=101.0)])])


def test_last_row_wins_within_a_file(tmp_path):
    """An identical-timestamp repeat with the same values is dropped; the survivor is the last row."""
    arrays, meta = build(tmp_path, [row(0), row(0)])
    assert meta["per_file"][0]["duplicates_in_file"] == 1
    assert arrays["open"][0] == 100.0


def test_a_carried_zero_volume_row_is_kept_and_counted_dead(tmp_path):
    arrays, meta = build(tmp_path, [row(0, volume=0.0, taker=0.0), row(1)])
    assert np.isfinite(arrays["open"][0]) and arrays["volume"][0] == 0.0
    assert meta["minutes_dead"] == 1 and meta["minutes_present"] == 2
    assert meta["per_file"][0]["zero_volume_rows"] == 1


def test_an_absent_minute_is_nan_and_never_filled(tmp_path):
    arrays, meta = build(tmp_path, [row(0), row(2)])
    assert not np.isfinite(arrays["open"][1])
    assert meta["minutes_missing"] == N - 2
    assert meta["missing_runs_longest"][0]["minutes"] == N - 3       # minutes 3 .. N-1
    assert any(r["minutes"] == 1 for r in meta["missing_runs_longest"])


def test_a_row_violating_the_close_time_identity_is_counted_listed_and_kept(tmp_path):
    arrays, meta = build(tmp_path, [row(0, close_delta=41_646), row(1)])
    assert meta["totals"]["identity_failures"] == 1
    listed = meta["identity_failure_rows"]
    assert len(listed) == 1 and listed[0]["close_minus_open_s"] == pytest.approx(41.646)
    assert np.isfinite(arrays["open"][0])                            # kept like any other row


def test_a_row_exactly_on_the_identity_is_not_counted(tmp_path):
    _, meta = build(tmp_path, [row(0), row(1)])
    assert meta["totals"]["identity_failures"] == 0


def test_a_zip_whose_hash_does_not_match_the_manifest_fails(tmp_path):
    raw, cache = tmp_path / "raw", tmp_path / "cache"
    p = raw / "spot_1m" / "TESTUSDT-1m-2020-01.zip"
    make_zip(p, [row(0)])
    man = manifest_for(raw, [("TESTUSDT", "spot_1m", p)])
    man["files"][0]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="sha256"):
        SD.build_one("TESTUSDT", "spot_1m", man, t0_s=T0, n=N, cache=cache, raw=raw)


# --- premium and BTCFDUSD ---------------------------------------------------------------------------

def test_a_premium_panel_keeps_only_close(tmp_path):
    arrays, meta = build(tmp_path, [row(0), row(1)], kind="premium_1m", name="TESTUSDT-1m-2020-01.zip")
    assert set(arrays) == {"t0_s", "close"}
    assert meta["arrays"] == ["close"] and "minutes_dead" not in meta
    assert arrays["close"][0] == 100.5


def test_btcfdusd_records_its_first_present_minute_and_all_nan_before(tmp_path):
    arrays, meta = build(tmp_path, [row(4), row(5)], symbol="BTCFDUSD", name="BTCFDUSD-1m-2023-08.zip")
    assert meta["first_present_utc"] == SD.iso(T0 + 60 * 4)
    assert meta["all_nan_before_first_present"] is True
    assert not np.isfinite(arrays["open"][:4]).any()


# --- grid helpers -----------------------------------------------------------------------------------

def test_year_slices_cover_the_grid_without_overlap():
    slices = SD.year_slices(SD.T0_S, SD.N_MINUTES)
    assert list(slices) == [str(y) for y in range(2020, 2027)]
    assert slices["2020"][0] == 0 and slices["2026"][1] == SD.N_MINUTES
    for a, b in zip(list(slices.values()), list(slices.values())[1:]):
        assert a[1] == b[0]


def test_runs_of_returns_maximal_runs_longest_first():
    mask = np.array([True, True, False, True, False, False, True, True, True])
    assert SD.runs_of(mask) == [(6, 3), (0, 2), (3, 1)]
    assert SD.runs_of(np.zeros(5, dtype=bool)) == []
    assert SD.runs_of(np.ones(4, dtype=bool)) == [(0, 4)]

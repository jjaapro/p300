"""dashboard/botinfo.py — registry completeness, live params, diag sums."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dashboard import botinfo


def test_params_build_for_every_bot():
    # doubles as the import-side-effect canary: every bot + sleeve config
    # must import cleanly with no env mutation.
    for bot in botinfo.BOTS:
        rows = botinfo.params(bot)
        assert rows, bot
        for r in rows:
            assert set(r) == {"group", "name", "value", "unit", "source"}


def test_registry_files_exist():
    for name, meta in botinfo.BOTS.items():
        assert (botinfo.CARDS_DIR / meta["card"]).is_file(), name
        assert (botinfo.CALIB_DIR / meta["calibration"]).is_file(), name


def test_eth_view_overrides():
    btc = {(p["group"], p["name"]): p["value"]
           for p in botinfo.params("chento_v3")}
    eth = {(p["group"], p["name"]): p["value"]
           for p in botinfo.params("chento_v3_eth")}
    assert "cd_futures_15m" in str(btc[("Data", "candles / OKX")])
    assert "cd_futures_eth_15m" in str(eth[("Data", "candles / OKX")])
    assert "skip" in str(btc[("Filters", "tilt policy")])
    assert "half" in str(eth[("Filters", "tilt policy")])


def test_unknown_bot_raises():
    with pytest.raises(ValueError):
        botinfo.params("nope")


def test_diag_today_sums_fragmented_days(tmp_path, monkeypatch):
    today = datetime.now(timezone.utc).date().isoformat()
    lines = [
        {"utc_date": "2026-08-20", "counters": {"eval_calls": 5}},
        {"utc_date": today, "counters": {"eval_calls": 15,
                                         "boundary_skipped": 12}},
        {"utc_date": today, "counters": {"eval_calls": 15, "b1_none": 1},
         "near_misses": [{"ts": "x", "reason": "okx"}]},
    ]
    p = tmp_path / "diag.jsonl"
    p.write_text("\n".join(json.dumps(ln) for ln in lines),
                 encoding="utf-8")
    monkeypatch.setitem(botinfo.BOTS["chento_v3"], "diag", p)
    d = botinfo.diag_today("chento_v3")
    assert d["date"] == today and d["is_today"]
    assert d["counters"]["eval_calls"] == 30      # fragments summed
    assert d["counters"]["b1_none"] == 1
    assert d["near_misses"] == [{"ts": "x", "reason": "okx"}]


def test_diag_missing_file_is_none(monkeypatch):
    monkeypatch.setitem(botinfo.BOTS["adx"], "diag",
                        Path("Z:/does/not/exist.jsonl"))
    assert botinfo.diag_today("adx") is None
    assert botinfo.diag_today("carry") is None    # registry has None

"""The Carry S-078 ETH twin (bots/carry_eth, 2026-09-19): the sleeve trades the configured asset, reads that
asset's funding and prices, the carry close books that asset's funding, and the twin is registered wherever the
fleet is enumerated. The rule itself is asset-agnostic and covered by tests/test_carry_exit_rule.py."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import botlib
import health
import monitor
from bots.carry.strategy import config as cfg
from bots.carry.strategy import signal as carry
from bots.carry_eth import config as ethcfg
from dashboard import botinfo, procscan, queries
from strategies import trades
from strategies.support import clock, funding
from strategies.support import db as _db_mod
from strategies.support import trade_db, variant_registry

NOW = datetime(2026, 3, 10, 0, 5, tzinfo=timezone.utc)
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = (tmp_path / "prod.db").resolve()
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, db_path)
    monkeypatch.setattr(trade_db, "DB_PATH", db_path)
    trade_db.init_db()
    variant_registry.init_schema()
    clock.set_simulated_now(NOW)
    monkeypatch.setattr(cfg, "ASSET", "ETH")
    variant = botlib.ensure_bot_variant(ethcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
                                        bot_name=ethcfg.BOT_NAME)
    yield db_path, variant
    clock.set_simulated_now(None)


def test_config_and_wrapper():
    assert cfg.ASSET == "BTC"                                   # this process never set CARRY_ASSET
    assert set(cfg.PRICE_SOURCES) == {"BTC", "ETH"}
    assert cfg.PRICE_SOURCES["ETH"]["spot"][0] == "eth_1m" and cfg.PRICE_SOURCES["ETH"]["perp"][0] == "cd_futures_eth_15m"
    assert cfg.PRICE_SOURCES["BTC"]["spot"][:3] == ("cd_spot_binance", "timestamp", 1)
    assert (ethcfg.VARIANT_ID, ethcfg.BOT_NAME, ethcfg.CARRY_NOTIONAL_X) == ("bot_carry_eth_v1", "carry_eth", 1.0)
    assert set(ethcfg.MGMT_TABLES) == {"eth_1m", "cd_futures_eth_15m", "cd_funding_rate_eth"}
    for t in ethcfg.MGMT_TABLES:
        assert t in botlib.FRESHNESS_CONTRACTS, t
    src = (REPO / "bots" / "carry_eth" / "runner.py").read_text(encoding="utf-8")
    assert 'os.environ["CARRY_ASSET"] = "ETH"' in src and "base.run(ethcfg)" in src
    assert src.index('os.environ["CARRY_ASSET"]') < src.index("from bots.carry import runner as base")


def test_loader_reads_eth_funding_spot_and_perp(env, monkeypatch):
    db_path, _ = env
    seen = {}

    def fake_sums(asset, since_ts, until_ts, *, complete_only=True):
        seen["asset"] = asset
        return {(NOW - timedelta(days=k)).strftime("%Y-%m-%d"): 0.01 for k in range(1, 5)}
    monkeypatch.setattr(funding, "daily_sums_pct", fake_sums)
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE eth_1m (open_time INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL)")
    con.execute("CREATE TABLE cd_futures_eth_15m (timestamp INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL)")
    for k in range(1, 5):
        day = (NOW - timedelta(days=k)).replace(hour=0, minute=0, second=0, microsecond=0)
        # the day's minutes: an early one, the last-ten window with the true last close, a 23:45 perp bar
        for minute, close in ((10, 3000.0 + k), (1435, 3100.0 + k), (1439, 3200.0 + k)):
            ts = int((day + timedelta(minutes=minute)).timestamp()) * 1000
            con.execute("INSERT INTO eth_1m VALUES (?,?,?,?,?,?)", (ts, close, close, close, close, 1.0))
        con.execute("INSERT INTO cd_futures_eth_15m VALUES (?,?,?,?,?,?)",
                    (int((day + timedelta(minutes=1425)).timestamp()), 1, 1, 1, 3300.0 + k, 1.0))
    con.commit()
    con.close()
    recs = carry._load_recent_daily_funding(days=10)
    assert seen["asset"] == "ETH"
    assert [r["date"] for r in recs] == [(NOW - timedelta(days=k)).strftime("%Y-%m-%d") for k in (4, 3, 2, 1)]
    assert recs[-1]["spot_close"] == 3201.0 and recs[-1]["perp_close"] == 3301.0     # the day's LAST minute, not 23:55's
    assert recs[-1]["daily_funding_pct"] == 0.01


def test_decide_opens_an_eth_trade(env, monkeypatch):
    db_path, variant = env
    from tests.test_carry_exit_rule import _records
    monkeypatch.setattr(carry, "_load_recent_daily_funding",
                        lambda days=30: _records([0.02] * 40, start="2026-01-29"))
    intents, status = carry.decide(variant, weight_pct=100.0, leverage=1.0)
    assert status["status"] == "decided" and len(intents) == 1 and intents[0].asset == "ETH"
    tid = carry.execute(variant, intents[0])["trade_id"]
    con = sqlite3.connect(str(db_path))
    asset, strategy, sv = con.execute("SELECT asset, strategy, strategy_variant FROM trades WHERE id=?", (tid,)).fetchone()
    con.close()
    assert (asset, strategy, sv) == ("ETH", "CARRY", "bot_carry_eth_v1")
    assert carry._get_open_carry_trades(variant["id"]) and carry._get_open_carry_trades(variant["id"])[0]["id"] == tid


def test_close_books_the_trades_own_assets_funding(env, monkeypatch):
    db_path, variant = env
    tid = trades.open_paper_trade(variant=variant, sleeve_name="CARRY", asset="ETH", direction="LONG",
                                  entry_price=3000.0, allocation_pct=100.0, leverage=1.0,
                                  reason={"trigger": "t"}, scheduled_exit_dt=None,
                                  entry_dt=NOW - timedelta(days=3), signal_time_iso="2026-03-07")
    seen = {}

    def fake_accrued(asset, a, b, direction):
        seen["asset"], seen["direction"] = asset, direction
        return 0.50
    monkeypatch.setattr(funding, "accrued_pct", fake_accrued)
    assert trades.close_carry_trade(tid, 3010.0, "test") is True
    assert seen == {"asset": "ETH", "direction": "SHORT"}
    con = sqlite3.connect(str(db_path))
    pnl, status = con.execute("SELECT pnl_usdt, status FROM trades WHERE id=?", (tid,)).fetchone()
    con.close()
    assert status == "closed" and pnl == pytest.approx(10_000.0 * (0.50 - 0.24) / 100.0)


def test_btc_process_is_unchanged(env, monkeypatch):
    """With the default asset the sleeve still reads BTC funding from the BTC hourly tables."""
    db_path, _ = env
    monkeypatch.setattr(cfg, "ASSET", "BTC")
    con = sqlite3.connect(str(db_path))
    for t in ("cd_spot_binance", "cd_futures_ohlcv"):
        con.execute(f"CREATE TABLE {t} (timestamp INTEGER PRIMARY KEY, close REAL)")
    day = (NOW - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    con.execute("INSERT INTO cd_spot_binance VALUES (?, ?)", (int((day + timedelta(hours=23)).timestamp()), 50_000.0))
    con.execute("INSERT INTO cd_futures_ohlcv VALUES (?, ?)", (int((day + timedelta(hours=23)).timestamp()), 50_010.0))
    con.commit()
    con.close()
    seen = {}

    def fake_sums(asset, since_ts, until_ts, *, complete_only=True):
        seen["asset"] = asset
        return {day.strftime("%Y-%m-%d"): 0.02}
    monkeypatch.setattr(funding, "daily_sums_pct", fake_sums)
    recs = carry._load_recent_daily_funding(days=3)
    assert seen["asset"] == "BTC"
    assert len(recs) == 1 and recs[0]["spot_close"] == 50_000.0 and recs[0]["perp_close"] == 50_010.0


def test_twin_is_registered_wherever_the_fleet_is_enumerated():
    assert "carry_eth" in health.BOT_CONFIGS and "carry_eth" not in health.BOT_ENTRYPOINTS
    assert procscan.UNIT_SCRIPTS["carry_eth"] == "bots/carry_eth/runner.py"
    assert monitor.BOT_EXPECTATIONS["carry_eth"] == monitor.BOT_EXPECTATIONS["carry"]
    assert "carry_eth" in queries.UNITS
    meta = botinfo.BOTS["carry_eth"]
    assert (meta["variant_id"], meta["asset"], meta["calibration"]) == ("bot_carry_eth_v1", "ETH", "carry.md")
    assert (botinfo.CARDS_DIR / meta["card"]).is_file()
    params = {(p["group"], p["name"]): p["value"] for p in botinfo.params("carry_eth")}
    assert "ETH" in str(params[("Data", "asset / funding table")])
    assert "BTC" in str({(p["group"], p["name"]): p["value"] for p in botinfo.params("carry")}[("Data", "asset / funding table")])
    fleet = (REPO / "start_fleet.ps1").read_text(encoding="utf-8")
    assert 'carry_eth     = @{ Script = "bots/carry_eth/runner.py"' in fleet and '"carry", "carry_eth"' in fleet
    baseline = (REPO / "bots" / "carry_eth" / "research_baseline.json").read_text(encoding="utf-8")
    assert '"bot_carry_eth_v1"' in baseline

"""Shared synthetic prod.db builder for the dashboard test modules.

Not a test module (no test_ prefix). Consumers add tests/ to sys.path and
import it directly — robust under any pytest importmode.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 8, 24, 18, 0, 0, tzinfo=timezone.utc)

# SJ-3's entry, aligned to a 15m boundary so the renderer's entry-bar dot
# lands on a real bar.
SJ3_ENTRY_TS = (int(NOW.timestamp()) // 900) * 900 - 78 * 900
SJ3_ENTRY_ISO = datetime.fromtimestamp(
    SJ3_ENTRY_TS, tz=timezone.utc).isoformat()


def iso_ago(age_s: float) -> str:
    return (NOW - timedelta(seconds=age_s)).isoformat()


def build_fixture_db(p):
    """Synthetic prod.db shared by the queries / render / api test modules."""
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE cd_futures_15m (timestamp INTEGER PRIMARY KEY,"
                " open REAL, high REAL, low REAL, close REAL, volume REAL)")
    con.execute("CREATE TABLE btc_1m (open_time INTEGER PRIMARY KEY,"
                " open REAL, high REAL, low REAL, close REAL)")
    # freshness ages are measured by botlib against the REAL clock, so these
    # two rows must be stamped relative to runtime, not the fixed NOW.
    rt = datetime.now(timezone.utc).timestamp()
    con.execute("INSERT INTO cd_futures_15m VALUES (?,1,1,1,1,1)",
                (int(rt - 300),))                         # fresh
    con.execute("INSERT INTO btc_1m VALUES (?,1,1,1,1)",
                (int((rt - 7200) * 1000),))               # stale (2h > 10m)
    # 15m candles around SJ-3's entry for the entry-context renderer
    # (covers SJ-2's window too).
    for i in range(-100, 46):
        t = SJ3_ENTRY_TS + i * 900
        px = 77000.0 + (i % 7) * 40
        con.execute("INSERT OR IGNORE INTO cd_futures_15m VALUES "
                    "(?,?,?,?,?,?)",
                    (t, px, px + 120, px - 130, px + 30, 5.0))
    con.execute("CREATE TABLE scheduled_events (date TEXT)")
    con.execute("INSERT INTO scheduled_events VALUES (?)",
                ((NOW + timedelta(days=200)).date().isoformat(),))
    con.execute("""CREATE TABLE trades (
        id TEXT PRIMARY KEY, asset TEXT, direction TEXT, strategy TEXT,
        strategy_variant TEXT, entry_time TEXT, exit_time TEXT,
        actual_exit_time TEXT, entry_price REAL, exit_price REAL,
        size_usdt REAL, qty REAL, leverage REAL, pnl_usdt REAL,
        pnl_pct REAL, status TEXT, execution_mode TEXT, notes TEXT)""")
    adx_notes = ('{"adx": 25.3, "ema50": 65000.0, "_stop_price": 70495.2, '
                 '"_atr_at_entry": 2124.3}')
    carry_notes = ('{"fr_7d_avg_pct": 0.01, '
                   '"structure": "long_spot_short_perp_delta_neutral"}')
    chento_notes = ('{"trigger": "chento_triple_v3", '
                    '"sleeve": "CHENTO_TRIPLE_V3", '
                    '"_stop_price": 75041.15, "_target_price": 89490.9, '
                    '"_risk": 2064.25, '
                    '"_time_stop_iso": "' + iso_ago(-600) + '", '
                    '"_filter_diag": {"no_tilt": "pass"}}')
    closed_notes = ('{"_stop_price": 1.0, "_risk": 2000.0}'
                    '\nCHENTO_TRIPLE_V3_EXIT: stop_hit; fees=18bp RT')
    rows = [
        # (id, asset, dir, strat, variant, entry, exit_sched, actual_exit,
        #  entry_px, exit_px, size, qty, lev, pnl, pnl_pct, status, mode, notes)
        ("SJ-1", "BTC", "LONG", "ADX", "bot_adx_v1", iso_ago(90000),
         iso_ago(3 * 3600), None, 78338.03, None, 15000.0, 0.19, 1.5,
         None, None, "open", "paper", adx_notes),         # overdue 3h > grace
        ("SJ-2", "BTC", "LONG", "CARRY", "bot_carry_v1", iso_ago(80000),
         "2099-12-31T00:00:00+00:00", None, 60000.0, None, 10000.0, 0.25,
         1.0, None, None, "open", "paper", carry_notes),  # sentinel
        ("SJ-3", "BTC", "LONG", "CHENTO_TRIPLE_V3", "bot_chento_v3_v1",
         SJ3_ENTRY_ISO, iso_ago(600), None, 77105.4, None, 20000.0, 0.26,
         2.0, None, None, "open", "paper", chento_notes),  # within grace
        ("SJ-4", "BTC", "SHORT", "CHENTO_TRIPLE_V3", "bot_chento_v3_v1",
         iso_ago(200000), iso_ago(9 * 3600), iso_ago(100000), 80000.0,
         82000.0, 40000.0, 0.5, 4.0, 2000.0, 5.0, "closed", "paper",
         closed_notes),
    ]
    con.executemany(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows)
    con.execute("CREATE TABLE variants (id TEXT PRIMARY KEY, spec_json TEXT)")
    con.executemany("INSERT INTO variants VALUES (?,?)", [
        ("bot_adx_v1", '{"bot": "adx"}'),
        ("bot_carry_v1", '{"bot": "carry"}'),
        ("bot_chento_v3_v1", '{"bot": "chento_v3"}'),
        ("p300_aggressive_v2_v1_0", "{}"),                # legacy: excluded
    ])
    con.execute("CREATE TABLE cd_futures_ohlcv (timestamp INTEGER PRIMARY "
                "KEY, open REAL, high REAL, low REAL, close REAL)")
    hour = int(rt // 3600) * 3600
    for i in range(30):                                   # 30 hourly bars back
        t = hour - i * 3600
        con.execute("INSERT INTO cd_futures_ohlcv VALUES (?,?,?,?,?)",
                    (t, 100.0 + i, 110.0 + i, 90.0 + i, 105.0 + i))
    con.execute("""CREATE TABLE bot_heartbeats (
        name TEXT PRIMARY KEY, last_tick_utc TEXT, last_eval_utc TEXT,
        last_signal_utc TEXT, open_trades INTEGER, interval_s INTEGER,
        status TEXT, note TEXT, pid INTEGER)""")
    con.commit()
    con.close()
    return p

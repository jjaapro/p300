"""Shared synthetic prod.db builder for the dashboard test modules.

Not a test module (no test_ prefix). Consumers add tests/ to sys.path and
import it directly — robust under any pytest importmode.

Two clocks: the trade rows and the entry-context candles hang off the fixed
NOW (render tests need stable pictures); everything freshness- or
window-related is stamped relative to the REAL clock `rt`, because botlib
measures ages against wall time and the flow/positioning endpoints window
from now. All synthetic series are deterministic functions of their index
so tests can assert exact values.
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

_TAKER_SCHEMA = ("timestamp INTEGER PRIMARY KEY, open REAL, high REAL, "
                 "low REAL, close REAL, volume REAL, volume_buy REAL, "
                 "volume_sell REAL")

# ─── deterministic generators (shared with the tests' expected values) ──────

def perp15(k: int) -> tuple[float, float, float]:
    """(price, taker_buy, taker_sell) of BTC 15m bar k (k=0 newest)."""
    return 80000.0 + (k % 9) * 30, 5.0 + (k % 4), 5.0 + (k % 3)


def spot15(k: int) -> tuple[float, float, float] | None:
    """Spot row for bar k, or None — every 10th bar is missing so the flow
    endpoint's incomplete-spot → null rule is exercised."""
    if k % 10 == 0:
        return None
    px, _, _ = perp15(k)
    return px - 20, 4.0 + (k % 5), 4.0 + (k % 2)


def eth15(k: int) -> tuple[float, float, float]:
    return 3000.0 + (k % 7) * 2, 3.0 + (k % 3), 3.0 + (k % 4)


def hourly_perp(i: int) -> tuple[float, float, float]:
    """(close, taker_buy, taker_sell) of BTC hourly bar i (i=0 newest):
    cvd = 10 − i flips sign after i=10."""
    return 105.0 + i, 100.0 + i, 90.0 + 2 * i


def hourly_spot_close(i: int) -> float:
    return 99.5 + i


def oi_close(i: int) -> float:
    return 1000.0 + 10 * i


def funding_rate(j: int) -> float:
    """Settlement j (j=0 newest): cycles 1e-4, 2e-4, 3e-4."""
    return 1e-4 * (1 + j % 3)


def lsr_ratio(asset: str, k: int) -> float:
    """Daily long/short ratio k days ago; 100-value cycle offset so today's
    value sits mid-distribution."""
    return 0.7 + ((k + 37) % 100) / 100 + (0.05 if asset == "ETH" else 0.0)


def daily_close(k: int) -> float:
    return 100.0 + 0.1 * k


def iso_ago(age_s: float) -> str:
    return (NOW - timedelta(seconds=age_s)).isoformat()


def build_fixture_db(p):
    """Synthetic prod.db shared by the queries / render / api / market test
    modules."""
    con = sqlite3.connect(str(p))
    rt = datetime.now(timezone.utc).timestamp()
    q15 = int(rt // 900) * 900
    hour = int(rt // 3600) * 3600
    q8 = int(rt // 28800) * 28800
    day0 = int(rt // 86400) * 86400

    for t in ("cd_futures_15m", "cd_spot_15m", "cd_futures_eth_15m",
              "cd_futures_ohlcv", "cd_spot_binance"):
        con.execute(f"CREATE TABLE {t} ({_TAKER_SCHEMA})")
    con.execute("CREATE TABLE btc_1m (open_time INTEGER PRIMARY KEY,"
                " open REAL, high REAL, low REAL, close REAL)")
    con.execute("INSERT INTO btc_1m VALUES (?,1,1,1,1)",
                (int((rt - 7200) * 1000),))               # stale (2h > 10m)

    # 15m BTC perp + spot: 104 bars over the last 26h (k=0 newest → fresh)
    for k in range(104):
        t = q15 - k * 900
        px, vb, vs = perp15(k)
        con.execute("INSERT INTO cd_futures_15m VALUES (?,?,?,?,?,?,?,?)",
                    (t, px, px + 60, px - 60, px + 10, vb + vs, vb, vs))
        s = spot15(k)
        if s is not None:
            sc, sb, ss = s
            con.execute("INSERT INTO cd_spot_15m VALUES (?,?,?,?,?,?,?,?)",
                        (t, sc, sc + 40, sc - 40, sc + 10, sb + ss, sb, ss))
    # 15m ETH perp: 120 bars over the last 30h
    for k in range(120):
        t = q15 - k * 900
        px, vb, vs = eth15(k)
        con.execute("INSERT INTO cd_futures_eth_15m VALUES (?,?,?,?,?,?,?,?)",
                    (t, px, px + 4, px - 4, px + 1, vb + vs, vb, vs))
    # 15m candles around SJ-3's entry for the entry-context renderer
    # (covers SJ-2's window too). No taker columns — old-style rows.
    for i in range(-100, 46):
        t = SJ3_ENTRY_TS + i * 900
        px = 77000.0 + (i % 7) * 40
        con.execute("INSERT OR IGNORE INTO cd_futures_15m "
                    "(timestamp, open, high, low, close, volume) "
                    "VALUES (?,?,?,?,?,?)",
                    (t, px, px + 120, px - 130, px + 30, 5.0))

    # hourly BTC perp + spot: 30 bars back from the current hour
    for i in range(30):
        t = hour - i * 3600
        c, vb, vs = hourly_perp(i)
        con.execute("INSERT INTO cd_futures_ohlcv VALUES (?,?,?,?,?,?,?,?)",
                    (t, 100.0 + i, 110.0 + i, 90.0 + i, c, vb + vs, vb, vs))
        sc = hourly_spot_close(i)
        con.execute("INSERT INTO cd_spot_binance VALUES (?,?,?,?,?,?,?,?)",
                    (t, 99.0 + i, 109.0 + i, 89.0 + i, sc, 95.0 + 2 * i,
                     50.0 + i, 45.0 + i))
    # daily 00:00 spot closes for the positioning scoreboard (k>=2 avoids
    # colliding with the hourly rows above)
    for k in range(2, 402):
        c = daily_close(k)
        con.execute("INSERT OR IGNORE INTO cd_spot_binance "
                    "(timestamp, open, high, low, close, volume) "
                    "VALUES (?,?,?,?,?,?)",
                    (day0 - k * 86400, c, c + 1, c - 1, c, 10.0))

    # open interest: hourly, i=5 missing (exercises the carry path)
    con.execute("CREATE TABLE cd_open_interest (timestamp INTEGER PRIMARY KEY,"
                " oi_open REAL, oi_high REAL, oi_low REAL, oi_close REAL,"
                " oi_value_open REAL, oi_value_high REAL, oi_value_low REAL,"
                " oi_value_close REAL)")
    for i in range(30):
        if i == 5:
            continue
        o = oi_close(i)
        con.execute("INSERT INTO cd_open_interest VALUES (?,?,?,?,?,?,?,?,?)",
                    (hour - i * 3600, o, o, o, o, o * 8e4, o * 8e4, o * 8e4,
                     o * 8e4))

    # funding: 30 settlements (10 days) + two hourly non-settlement rows
    # carrying an absurd 0.5 that the % 28800 filter must drop
    for t in ("cd_funding_rate", "cd_funding_rate_eth"):
        con.execute(f"CREATE TABLE {t} (timestamp INTEGER PRIMARY KEY,"
                    f" fr_open REAL, fr_high REAL, fr_low REAL, fr_close REAL)")
        for j in range(30):
            fr = funding_rate(j)
            con.execute(f"INSERT INTO {t} VALUES (?,?,?,?,?)",
                        (q8 - j * 28800, fr, fr, fr, fr))
        for off in (3600, 7200):
            con.execute(f"INSERT INTO {t} VALUES (?,?,?,?,?)",
                        (q8 - off, 0.5, 0.5, 0.5, 0.5))

    # long/short account ratio: daily, BTC + ETH, 400 rows ending today 00:00
    con.execute("CREATE TABLE ca_long_short_ratio (asset TEXT NOT NULL,"
                " timestamp INTEGER NOT NULL, ratio REAL, long_pct REAL,"
                " short_pct REAL, UNIQUE(asset, timestamp))")
    for asset in ("BTC", "ETH"):
        for k in range(400):
            r = lsr_ratio(asset, k)
            lp = r / (1 + r) * 100
            con.execute("INSERT INTO ca_long_short_ratio VALUES (?,?,?,?,?)",
                        (asset, day0 - k * 86400, r, lp, 100 - lp))

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
    con.execute("""CREATE TABLE bot_heartbeats (
        name TEXT PRIMARY KEY, last_tick_utc TEXT, last_eval_utc TEXT,
        last_signal_utc TEXT, open_trades INTEGER, interval_s INTEGER,
        status TEXT, note TEXT, pid INTEGER)""")
    con.commit()
    con.close()
    return p

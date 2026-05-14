"""FOMC sleeve decision logic — pure-function unit tests.

Mocks the four upstream services (fed_funds, sentiment, polymarket,
price_feed) so tests don't depend on the real DB or network. The
decision rule itself is what we're validating.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from strategies.sleeves.fomc import signal as fomc_service


# ─── Mock the upstream services via monkeypatch fixture ──────────────────────

@pytest.fixture
def patched_evaluate(monkeypatch):
    """Drop-in mocks for fed_funds_service, sentiment_index_service,
    polymarket_service. Each test sets the desired return values."""
    state = {
        "target_rate": 3.75,
        "phase": "mid_hold",
        "fg": 26,
        "expected_action": "hold",
        "ea_meta": {"reason": "test"},
    }

    def fake_target_rate(d): return state["target_rate"]
    def fake_phase(d): return state["phase"]
    def fake_get_value(d): return state["fg"]
    def fake_bucket(v):
        if v is None: return "unknown"
        if v <= 25: return "extreme_fear"
        if v <= 45: return "fear"
        if v <= 55: return "neutral"
        if v <= 75: return "greed"
        return "extreme_greed"
    def fake_expected(d, n): return state["expected_action"], state["ea_meta"]
    def fake_remaining(d): return 5  # arbitrary, not checked by rule

    from data.sources import fed_funds as fed_funds_service, sentiment as sentiment_index_service, polymarket as polymarket_service
    monkeypatch.setattr(fed_funds_service, "get_target_rate", fake_target_rate)
    monkeypatch.setattr(fed_funds_service, "classify_phase", fake_phase)
    monkeypatch.setattr(sentiment_index_service, "get_value", fake_get_value)
    monkeypatch.setattr(sentiment_index_service, "bucket", fake_bucket)
    monkeypatch.setattr(polymarket_service, "expected_action_for_meeting", fake_expected)
    monkeypatch.setattr(fomc_service, "_remaining_2026_meetings_after", fake_remaining)
    return state


# ─── Decision rule tests ─────────────────────────────────────────────────────

def test_skip_when_expected_action_is_cut_25(patched_evaluate):
    patched_evaluate.update(phase="peak_hold", fg=60, expected_action="cut_25")
    r = fomc_service.evaluate("2026-04-29")
    assert r["decision"] == "skip"
    assert "cut_25" in r["reason"]


def test_skip_when_extreme_greed(patched_evaluate):
    patched_evaluate.update(phase="peak_hold", fg=85, expected_action="hold")
    r = fomc_service.evaluate("2026-04-29")
    assert r["decision"] == "skip"
    assert "extreme_greed" in r["reason"]


def test_skip_when_mid_hold_and_no_extreme_fear(patched_evaluate):
    patched_evaluate.update(phase="mid_hold", fg=40, expected_action="hold")
    r = fomc_service.evaluate("2026-04-29")
    assert r["decision"] == "skip"
    assert "mid_hold" in r["reason"]


def test_extreme_fear_unlocks_trade_outside_mid_hold(patched_evaluate):
    """F&G <=25 + non-mid_hold phase = the 8/8 historical bucket."""
    patched_evaluate.update(phase="cutting", fg=20, expected_action="hold")
    r = fomc_service.evaluate("2026-04-29")
    assert r["decision"] == "trade"
    assert "extreme_fear" in r["reason"]


def test_extreme_fear_does_not_override_mid_hold(patched_evaluate):
    """Even with extreme fear, mid_hold is a hard skip — historical
    mid_hold cohort is so weak we don't take the inferred edge."""
    patched_evaluate.update(phase="mid_hold", fg=15, expected_action="hold")
    r = fomc_service.evaluate("2026-04-29")
    assert r["decision"] == "skip"
    assert "mid_hold" in r["reason"]


def test_extreme_fear_does_not_override_cut_25(patched_evaluate):
    """Cut_25 is the hardest skip; even extreme fear doesn't unlock it."""
    patched_evaluate.update(phase="hiking", fg=10, expected_action="cut_25")
    r = fomc_service.evaluate("2026-04-29")
    assert r["decision"] == "skip"
    assert "cut_25" in r["reason"]


def test_trade_in_peak_hold_with_neutral_fg(patched_evaluate):
    """The 8/8 backtest cell."""
    patched_evaluate.update(phase="peak_hold", fg=50, expected_action="hold")
    r = fomc_service.evaluate("2026-04-29")
    assert r["decision"] == "trade"
    assert "peak_hold" in r["reason"]


def test_trade_in_hiking_phase(patched_evaluate):
    patched_evaluate.update(phase="hiking", fg=60, expected_action="hold")
    r = fomc_service.evaluate("2026-04-29")
    assert r["decision"] == "trade"


def test_trade_in_zirp_hold(patched_evaluate):
    patched_evaluate.update(phase="zirp_hold", fg=50, expected_action="hold")
    r = fomc_service.evaluate("2026-04-29")
    assert r["decision"] == "trade"


def test_today_april_29_2026_skips(patched_evaluate):
    """Today's actual conditions: phase=mid_hold, F&G≈26 (Fear, not Extreme),
    expected=hold. Should skip."""
    patched_evaluate.update(phase="mid_hold", fg=26, expected_action="hold")
    r = fomc_service.evaluate("2026-04-29")
    assert r["decision"] == "skip"
    assert r["phase"] == "mid_hold"


# ─── announcement_dt_utc — DST handling ──────────────────────────────────────

def test_announcement_during_edt():
    """April 29 = EDT. 14:00 ET = 18:00 UTC."""
    dt = fomc_service.announcement_dt_utc("2026-04-29")
    assert dt == datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)


def test_announcement_during_est():
    """January 28 = EST. 14:00 ET = 19:00 UTC."""
    dt = fomc_service.announcement_dt_utc("2026-01-28")
    assert dt == datetime(2026, 1, 28, 19, 0, tzinfo=timezone.utc)


# ─── Phase classifier (uses real fed_funds_service) ──────────────────────────

def test_phase_classifier_known_dates():
    """Smoke test against the live fed_funds_target_upper.json. If this
    file isn't present the test skips rather than fails — the rule itself
    is exercised by the mocked tests above."""
    from pathlib import Path
    from data.sources import fed_funds as fed_funds_service

    json_path = Path(__file__).resolve().parent.parent / "data" / "fed_funds_target_upper.json"
    if not json_path.exists():
        pytest.skip("fed_funds_target_upper.json not present")

    fed_funds_service.invalidate_cache()
    # 2024-07-31: rate held at 5.50% since Jul 2023 — peak_hold
    assert fed_funds_service.classify_phase("2024-07-31") == "peak_hold"
    # 2022-09-21: aggressive hike cycle
    assert fed_funds_service.classify_phase("2022-09-21") == "hiking"
    # 2025-12-10: r_90d_ago=4.5%, r_now=4.0% (after Sep+Oct cuts) -> cutting
    assert fed_funds_service.classify_phase("2025-12-10") == "cutting"
    # 2025-09-17: rate still 4.5% AT trade-decision time (cut effective T+1).
    # Phase is mid_hold even though the meeting itself produced a cut. This
    # reflects what the sleeve actually sees when deciding to enter at T-10h.
    assert fed_funds_service.classify_phase("2025-09-17") == "mid_hold"
    # 2026-04-29: rate stuck at 3.75% since Dec 2025 — mid_hold
    assert fed_funds_service.classify_phase("2026-04-29") == "mid_hold"


# ─── F&G bucket boundary cases ───────────────────────────────────────────────

def test_fg_bucket_boundaries():
    from data.sources import sentiment as s
    assert s.bucket(None) == "unknown"
    assert s.bucket(0) == "extreme_fear"
    assert s.bucket(25) == "extreme_fear"
    assert s.bucket(26) == "fear"
    assert s.bucket(45) == "fear"
    assert s.bucket(46) == "neutral"
    assert s.bucket(55) == "neutral"
    assert s.bucket(56) == "greed"
    assert s.bucket(75) == "greed"
    assert s.bucket(76) == "extreme_greed"
    assert s.bucket(100) == "extreme_greed"


# ─── Self-sweep close (defense-in-depth) ────────────────────────────────────

def test_self_sweep_closes_past_exit_trade(tmp_path, monkeypatch):
    """If a FOMC trade has exit_time in the past relative to clock and is
    still 'open', _sweep_stuck_opens should close it on the next tick.
    This guards against the SJ-1179-class bug where close_due_for_variant
    silently skipped a trade and the position leaked to end-of-window."""
    import sqlite3
    from datetime import datetime, timezone
    from strategies.support import clock, price_feed
    from strategies.sleeves.fomc import signal as fomc_service

    # Build minimal trades table at tmp path via canonical init_db so
    # schema additions (current_qty, trade_adjustments, etc.) are picked up
    # automatically.
    db = tmp_path / "dashboard.db"
    from strategies.support import trade_db, db as _db_mod
    monkeypatch.setattr(trade_db, "DB_PATH", db)
    monkeypatch.setattr(_db_mod, "DASH_DB", db)
    trade_db.init_db()
    con = sqlite3.connect(str(db))
    con.execute("""
        INSERT INTO trades
        (id, series, asset, direction, strategy, regime, allocation_pct,
         leverage, entry_time, exit_time, status, execution_mode,
         strategy_variant, actual_entry_time, entry_price, size_usdt, qty,
         order_ids, notes,
         current_qty, current_leverage, current_size_usdt, realized_pnl_usdt)
        VALUES
        ('SJ-T001','SJ','BTC','LONG','FOMC','peak_hold',5.0,5.0,
         '2024-01-31T09:00:00+00:00','2024-01-31T19:30:00+00:00','open','SHADOW',
         'test_variant','2024-01-31T09:00:00+00:00',42000.0,
         2500.0,0.0595,'[]','{}',
         0.0595, 5.0, 2500.0, 0)
    """)
    con.commit()
    con.close()

    # Stub price + funding feeds (DASH_DB already monkeypatched above).

    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 43000.0)
    from strategies.support import funding
    monkeypatch.setattr(funding, "accrued_pct", lambda *a, **k: 0.0)

    # Set clock past exit_time, then sweep
    clock.set_simulated_now(datetime(2024, 1, 31, 20, 0, tzinfo=timezone.utc))
    n = fomc_service._sweep_stuck_opens("test_variant")
    clock.set_simulated_now(None)

    assert n == 1, "sweep should close exactly one stuck trade"

    # Verify the trade record
    con = sqlite3.connect(str(db))
    r = con.execute("SELECT status, exit_price, pnl_usdt FROM trades "
                    "WHERE id='SJ-T001'").fetchone()
    con.close()
    assert r[0] == "closed"
    assert r[1] == 43000.0
    # pnl = (43000 - 42000) × 0.0595 = 59.5 price PnL; fees(10bp) + slip(10bp)
    # on $2500 = $5.0 total cost; net ≈ 54.5 (funding stubbed to 0).
    assert r[2] is not None
    assert 50 < r[2] < 65


def test_self_sweep_no_op_before_exit_time(tmp_path, monkeypatch):
    """Sweep should NOT close a trade whose exit_time is still in the future."""
    import sqlite3
    from datetime import datetime, timezone
    from strategies.support import clock, price_feed
    from strategies.sleeves.fomc import signal as fomc_service

    db = tmp_path / "dashboard.db"
    from strategies.support import trade_db, db as _db_mod
    monkeypatch.setattr(trade_db, "DB_PATH", db)
    monkeypatch.setattr(_db_mod, "DASH_DB", db)
    trade_db.init_db()
    con = sqlite3.connect(str(db))
    con.execute("""
        INSERT INTO trades
        (id, series, asset, direction, strategy, regime, allocation_pct,
         leverage, entry_time, exit_time, status, execution_mode,
         strategy_variant, actual_entry_time, entry_price, size_usdt, qty,
         order_ids, notes,
         current_qty, current_leverage, current_size_usdt, realized_pnl_usdt)
        VALUES
        ('SJ-T002','SJ','BTC','LONG','FOMC','peak_hold',5.0,5.0,
         '2024-01-31T09:00:00+00:00','2024-01-31T19:30:00+00:00','open','SHADOW',
         'test_variant','2024-01-31T09:00:00+00:00',42000.0,
         2500.0,0.0595,'[]','{}',
         0.0595, 5.0, 2500.0, 0)
    """)
    con.commit()
    con.close()

    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 43000.0)

    # Clock at 12:00 UTC — well before exit_time (19:30 UTC)
    clock.set_simulated_now(datetime(2024, 1, 31, 12, 0, tzinfo=timezone.utc))
    n = fomc_service._sweep_stuck_opens("test_variant")
    clock.set_simulated_now(None)

    assert n == 0, "should not close trades whose exit_time is in the future"


# ─── variant_engine._close_due_shadows scoping ───────────────────────────────

def test_close_due_shadows_skips_disabled_variants(tmp_path, monkeypatch):
    """Regression test: variant_engine._close_due_shadows must only touch
    trades belonging to ENABLED variants. The live bot used to close replay
    backtest trades because they were SHADOW + status=open, regardless of
    the variant's enabled flag — corrupting backtest results."""
    import sqlite3
    from datetime import datetime, timezone
    from services import variant_engine
    from strategies.support import clock, price_feed

    # Build dashboard.db at tmp path with two variants and one trade each.
    # trade_db.init_db creates the trades schema (and trade_adjustments);
    # the variants table is hand-rolled because it lives in variant_registry.
    db = tmp_path / "dashboard.db"
    from strategies.support import trade_db, db as _db_mod
    monkeypatch.setattr(trade_db, "DB_PATH", db)
    monkeypatch.setattr(_db_mod, "DASH_DB", db)
    trade_db.init_db()
    con = sqlite3.connect(str(db))
    con.execute("""
        CREATE TABLE variants (
            id TEXT PRIMARY KEY, short_name TEXT, long_name TEXT, kind TEXT,
            parent_variant_id TEXT, version TEXT, status TEXT, is_primary INT,
            capital_usdt REAL, color TEXT, spec_json TEXT, notes TEXT,
            superseded_by TEXT, reconcile_against TEXT, enabled INT,
            created_at TEXT
        )
    """)
    # Live variant (enabled=1) + replay variant (enabled=0)
    con.execute("INSERT INTO variants (id, kind, capital_usdt, enabled) "
                "VALUES ('live_v', 'full_portfolio', 10000, 1)")
    con.execute("INSERT INTO variants (id, kind, capital_usdt, enabled) "
                "VALUES ('replay_v', 'full_portfolio', 10000, 0)")
    # One open trade per variant, both with exit_time in the past
    for tid, vid in [("SJ-LIVE", "live_v"), ("SJ-REPLAY", "replay_v")]:
        con.execute("""
            INSERT INTO trades (id, series, asset, direction, strategy,
                allocation_pct, leverage, entry_time, exit_time, status,
                execution_mode, strategy_variant, actual_entry_time,
                entry_price, size_usdt, qty, order_ids, notes,
                current_qty, current_leverage, current_size_usdt,
                realized_pnl_usdt)
            VALUES (?, 'SJ', 'BTC', 'LONG', 'TEST', 5.0, 1.0,
                    '2024-01-01T00:00:00+00:00', '2024-01-02T00:00:00+00:00',
                    'open', 'SHADOW', ?, '2024-01-01T00:00:00+00:00',
                    50000.0, 500.0, 0.01, '[]', '{}', 0.01, 1.0, 500.0, 0)
        """, (tid, vid))
    con.commit()
    con.close()

    import services.variant_engine as ve
    import strategies.trades as svc_trades

    closed_ids: list[str] = []
    def fake_close(trade_id, exit_price, reason, sleeve_name, **kwargs):
        closed_ids.append(trade_id)
    monkeypatch.setattr(svc_trades, "close_perp_trade", fake_close)
    monkeypatch.setattr(ve, "_get_current_price", lambda _a: 51000.0)

    ve._close_due_shadows(datetime(2026, 4, 29, tzinfo=timezone.utc))

    assert "SJ-LIVE" in closed_ids, "live variant trade should be closed"
    assert "SJ-REPLAY" not in closed_ids, \
        "replay variant trade must NOT be closed by live engine"

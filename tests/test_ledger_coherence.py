"""strategies.support.ledger_coherence — coherence audit of the trades /
trade_adjustments ledger.

Tests use a synthetic fixture with deliberate defects (duplicates, missing
events, disabled-variant recent opens) to confirm each detector catches
what it's supposed to and ignores what it shouldn't (pre-J+ artifacts).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from strategies.support import ledger_coherence as lc
from tests.test_trade_adjustments import old_shape_ddl


# ─── Synthetic-defect fixture ──────────────────────────────────────────────


@pytest.fixture
def synthetic_ledger_db(tmp_path, monkeypatch):
    """Tmp dashboard.db with hand-rolled defects so each coherence detector
    has something to find.

    Defects planted:
      - 1 duplicate group: 2 trades with same (variant, strategy, asset, entry_time)
      - 1 disabled-variant trade entered in the last 24h (replay leak)
      - 1 post-J+ open trade with NO OPEN event in adjustments
      - 1 post-J+ closed trade with NO CLOSE event in adjustments
      - 1 pre-J+ trade without events (should NOT be flagged — known artifact)
      - 1 stale open trade in enabled variant (entry > 30d ago)
      - 2 healthy trades (clean open + clean closed with events)
    """
    fixture_db = tmp_path / "dashboard.db"
    from strategies.support import trade_db, db as _db_mod
    monkeypatch.setattr(trade_db, "DB_PATH", fixture_db)
    monkeypatch.setattr(_db_mod, "DASH_DB", fixture_db)
    trade_db.init_db()

    con = sqlite3.connect(str(fixture_db))
    # variants table (minimal)
    con.execute("""
        CREATE TABLE IF NOT EXISTS variants (
            id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1
        )
    """)
    con.execute("INSERT INTO variants (id, enabled) VALUES ('live_v', 1)")
    con.execute("INSERT INTO variants (id, enabled) VALUES ('replay_v', 0)")

    now = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    pre_jplus = "2025-01-01T00:00:00+00:00"
    post_jplus = "2026-06-01T00:00:00+00:00"
    recent = (now - timedelta(hours=2)).isoformat()
    stale = (now - timedelta(days=45)).isoformat()

    def ins_trade(tid: str, variant: str, strategy: str, asset: str,
                   entry_time: str, status: str, created_at: str):
        con.execute("""
            INSERT INTO trades
            (id, series, asset, direction, strategy, allocation_pct, leverage,
             entry_time, exit_time, status, execution_mode, strategy_variant,
             actual_entry_time, actual_exit_time,
             entry_price, exit_price, size_usdt, qty,
             current_qty, current_leverage, current_size_usdt, realized_pnl_usdt,
             created_at)
            VALUES (?, 'SJ', ?, 'LONG', ?, 5.0, 1.0,
                    ?, '2099-12-31T00:00:00+00:00', ?, 'paper', ?,
                    ?, NULL,
                    100.0, 110.0, 1000.0, 10.0,
                    10.0, 1.0, 1000.0, 0.0, ?)
        """, (tid, asset, strategy, entry_time, status, variant,
              entry_time, created_at))

    def ins_adj(tid: str, seq: int, event_type: str, event_time: str):
        con.execute("""
            INSERT INTO trade_adjustments
            (trade_id, seq, event_type, event_time, event_date,
             qty_delta, qty_after, leverage_after, margin_delta_usdt,
             size_usdt_after, price, fee_usdt)
            VALUES (?, ?, ?, ?, ?, 10.0, 10.0, 1.0, 1000.0, 1000.0, 100.0, 0.0)
        """, (tid, seq, event_type, event_time, event_time[:10]))

    # 1. Duplicate group: 2 trades with same (variant, strategy, asset, entry_time)
    ins_trade("SJ-1001", "live_v", "ADX", "BTC", post_jplus, "open", post_jplus)
    ins_adj("SJ-1001", 0, "OPEN", post_jplus)
    ins_trade("SJ-1002", "live_v", "ADX", "BTC", post_jplus, "open", post_jplus)
    ins_adj("SJ-1002", 0, "OPEN", post_jplus)

    # 2. Disabled-variant recent open (replay leak signature)
    ins_trade("SJ-1003", "replay_v", "ADX", "ETH", recent, "open", recent)
    ins_adj("SJ-1003", 0, "OPEN", recent)  # OPEN event recorded normally;
    # the defect is that this trade SHOULDN'T HAVE BEEN EMITTED — the
    # disabled-variant detector flags it independently of event coherence

    # 3. Post-J+ open with NO OPEN event
    ins_trade("SJ-1004", "live_v", "CARRY", "BTC", post_jplus, "open", post_jplus)

    # 4. Post-J+ closed with NO CLOSE event (but has OPEN)
    ins_trade("SJ-1005", "live_v", "CARRY", "ETH", post_jplus, "closed", post_jplus)
    ins_adj("SJ-1005", 0, "OPEN", post_jplus)

    # 5. Pre-J+ trade without events — should NOT trigger any flag
    ins_trade("SJ-0001", "live_v", "THU_BEAR", "BTC", pre_jplus, "closed", pre_jplus)

    # 6. Stale open in enabled variant
    ins_trade("SJ-1006", "live_v", "THU_BEAR", "ETH", stale, "open", stale)
    ins_adj("SJ-1006", 0, "OPEN", stale)

    # 7. Healthy trades — should pass everything
    ins_trade("SJ-1007", "live_v", "FOMC", "BTC", post_jplus, "open",
               post_jplus)
    ins_adj("SJ-1007", 0, "OPEN", post_jplus)
    ins_trade("SJ-1008", "live_v", "FOMC", "ETH", post_jplus, "closed",
               post_jplus)
    ins_adj("SJ-1008", 0, "OPEN", post_jplus)
    ins_adj("SJ-1008", 1, "CLOSE", post_jplus)

    con.commit()
    con.close()
    return fixture_db, now


# ─── Detector tests ────────────────────────────────────────────────────────


def test_duplicate_groups_detected(synthetic_ledger_db):
    _, now = synthetic_ledger_db
    audit = lc.audit_ledger(as_of_dt=now)
    assert audit.n_duplicate_groups == 1
    # The sample should reference the SJ-1001/SJ-1002 duplicate
    assert audit.duplicate_samples[0]["strategy"] == "ADX"
    assert audit.duplicate_samples[0]["asset"] == "BTC"
    assert audit.duplicate_samples[0]["cnt"] == 2


def _mk_ledger(tmp_path, monkeypatch, rows):
    """Minimal ledger holding just `rows` of (tid, variant, strategy, asset,
    direction, entry_time, unique_key)."""
    fixture_db = tmp_path / "dupes.db"
    from strategies.support import db as _db_mod, trade_db
    monkeypatch.setattr(trade_db, "DB_PATH", fixture_db)
    monkeypatch.setattr(_db_mod, "DASH_DB", fixture_db)
    trade_db.init_db()
    con = sqlite3.connect(str(fixture_db))
    con.execute("CREATE TABLE IF NOT EXISTS variants "
                "(id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1)")
    con.execute("INSERT OR IGNORE INTO variants (id, enabled) VALUES ('live_v', 1)")
    for tid, variant, strategy, asset, direction, entry_time, ukey in rows:
        con.execute("""
            INSERT INTO trades
            (id, series, asset, direction, strategy, allocation_pct, leverage,
             entry_time, exit_time, status, execution_mode, strategy_variant,
             actual_entry_time, entry_price, size_usdt, qty, unique_key)
            VALUES (?, 'SJ', ?, ?, ?, 5.0, 1.0, ?,
                    '2099-12-31T00:00:00+00:00', 'open', 'paper', ?,
                    ?, 100.0, 1000.0, 10.0, ?)
        """, (tid, asset, direction, strategy, entry_time, variant,
              entry_time, ukey))
    con.commit()
    con.close()
    return fixture_db


def test_duplicate_detector_sees_fills_seconds_apart(tmp_path, monkeypatch):
    """The 2026-08-15..24 doubled-fleet signature: one signal, two processes,
    fills 33s apart, so two DIFFERENT unique_keys and nothing for the UNIQUE
    index to collapse. Grouping on the exact entry_time — what this detector
    did until 2026-09-12 — returns zero and the incident stays invisible.
    """
    _mk_ledger(tmp_path, monkeypatch, [
        ("SJ-9001", "live_v", "CHENTO_TRIPLE_V3", "BTC", "LONG",
         "2026-08-21T06:15:25.500991+00:00",
         "live_v|CHENTO_TRIPLE_V3|BTC|2026-08-21T06:15:25.500991+00:00"),
        ("SJ-9002", "live_v", "CHENTO_TRIPLE_V3", "BTC", "LONG",
         "2026-08-21T06:15:58.170967+00:00",
         "live_v|CHENTO_TRIPLE_V3|BTC|2026-08-21T06:15:58.170967+00:00"),
    ])
    audit = lc.audit_ledger()
    assert audit.n_duplicate_groups == 1
    sample = audit.duplicate_samples[0]
    assert sample["cnt"] == 2
    assert sample["entry_minute"] == "2026-08-21T06:15"
    # Two distinct keys on one logical trade IS the diagnosis: the rows were
    # written by two processes, not by one process retrying.
    assert sample["distinct_keys"] == 2
    assert sample["trade_ids"] == "SJ-9001,SJ-9002"


def test_duplicate_detector_ignores_distinct_signals_on_the_same_day(
        tmp_path, monkeypatch):
    """Guards the granularity from the other side: chento genuinely fired
    twice on 2026-08-21 (06:15 and 19:45). A day-level bucket would call that
    a duplicate; a minute-level bucket must not."""
    _mk_ledger(tmp_path, monkeypatch, [
        ("SJ-9003", "live_v", "CHENTO_TRIPLE_V3", "BTC", "LONG",
         "2026-08-21T06:15:25+00:00", "k1"),
        ("SJ-9004", "live_v", "CHENTO_TRIPLE_V3", "BTC", "LONG",
         "2026-08-21T19:45:03+00:00", "k2"),
    ])
    assert lc.audit_ledger().n_duplicate_groups == 0


def test_duplicate_detector_ignores_a_close_and_reverse(tmp_path, monkeypatch):
    """Direction is part of the key, so a sleeve that closes a LONG and opens
    a SHORT in the same minute is not a duplicate."""
    _mk_ledger(tmp_path, monkeypatch, [
        ("SJ-9005", "live_v", "ADX", "BTC", "LONG",
         "2026-08-21T06:15:25+00:00", "k1"),
        ("SJ-9006", "live_v", "ADX", "BTC", "SHORT",
         "2026-08-21T06:15:41+00:00", "k2"),
    ])
    assert lc.audit_ledger().n_duplicate_groups == 0


def test_disabled_variant_recent_opens_detected(synthetic_ledger_db):
    """SJ-1003 in replay_v opened 2h ago — should flag as the replay-leak class."""
    _, now = synthetic_ledger_db
    audit = lc.audit_ledger(as_of_dt=now)
    assert audit.n_disabled_variants_with_recent_opens == 1
    assert audit.recent_disabled_opens_sample[0]["id"] == "SJ-1003"


def test_post_jplus_missing_open_event_detected(synthetic_ledger_db):
    """SJ-1004 is post-J+ open with no OPEN event — should flag."""
    _, now = synthetic_ledger_db
    audit = lc.audit_ledger(as_of_dt=now)
    assert audit.n_post_jplus_open_missing_open_event == 1


def test_post_jplus_missing_close_event_detected(synthetic_ledger_db):
    """SJ-1005 is post-J+ closed with no CLOSE event — should flag."""
    _, now = synthetic_ledger_db
    audit = lc.audit_ledger(as_of_dt=now)
    assert audit.n_post_jplus_closed_missing_close_event == 1


def test_pre_jplus_trades_excluded(synthetic_ledger_db):
    """SJ-0001 is pre-J+ closed with no events. Should NOT be flagged
    (known artifact)."""
    _, now = synthetic_ledger_db
    audit = lc.audit_ledger(as_of_dt=now)
    # SJ-0001 has no CLOSE event AND no OPEN event; the post-J+ count
    # should NOT include it (we have SJ-1005 as the only post-J+
    # missing-CLOSE, so count == 1).
    assert audit.n_post_jplus_closed_missing_close_event == 1


def test_stale_open_detected(synthetic_ledger_db):
    """SJ-1006 entered 45d ago in enabled variant — should flag."""
    _, now = synthetic_ledger_db
    audit = lc.audit_ledger(as_of_dt=now)
    assert audit.n_open_trades_stale == 1


def test_informational_open_counts(synthetic_ledger_db):
    _, now = synthetic_ledger_db
    audit = lc.audit_ledger(as_of_dt=now)
    # Enabled opens: SJ-1001, SJ-1002, SJ-1004, SJ-1006, SJ-1007 = 5
    assert audit.n_open_trades_enabled == 5
    # Disabled opens: SJ-1003 = 1
    assert audit.n_open_trades_in_disabled_variants == 1


def test_variant_filter_scopes_per_variant_counts(synthetic_ledger_db):
    """When filter is applied, per-variant counts scope but system-wide
    counts (duplicates, replay-isolation) remain unscoped."""
    _, now = synthetic_ledger_db
    audit = lc.audit_ledger(variant_id_filter="live_v", as_of_dt=now)
    # Per-scope: 5 opens in live_v (SJ-1001/2/4/6/7)
    assert audit.n_open_trades_enabled == 5
    # System-wide unchanged
    assert audit.n_duplicate_groups == 1
    assert audit.n_disabled_variants_with_recent_opens == 1


def test_format_ledger_coherence_renders_status_prefixes(synthetic_ledger_db):
    _, now = synthetic_ledger_db
    audit = lc.audit_ledger(as_of_dt=now)
    rendered = lc.format_ledger_coherence(audit)
    assert "FAIL" in rendered  # at least one detector tripped
    assert "duplicate" in rendered.lower()
    assert "OK" in rendered  # something passed (e.g. seq gaps == 0)


def test_clean_db_returns_all_zeros(tmp_path, monkeypatch):
    """A fresh DB with no defects should produce an all-zero audit."""
    fixture_db = tmp_path / "clean.db"
    from strategies.support import trade_db, db as _db_mod
    monkeypatch.setattr(trade_db, "DB_PATH", fixture_db)
    monkeypatch.setattr(_db_mod, "DASH_DB", fixture_db)
    trade_db.init_db()
    audit = lc.audit_ledger()
    assert audit.n_duplicate_groups == 0
    assert audit.n_disabled_variants_with_recent_opens == 0
    assert audit.n_post_jplus_open_missing_open_event == 0
    assert audit.n_post_jplus_closed_missing_close_event == 0
    assert audit.n_duplicate_adjustment_events == 0


# ─── Duplicate adjustment events (2026-09-14) ──────────────────────────────


def _old_shape_ledger(tmp_path, monkeypatch, *, duplicate: bool,
                      close_event: bool = True, created_at: str | None = None):
    """trades + trade_adjustments exactly as the 2026-05-18 PK rebuild left
    them in prod, plus the unique_key column init_db() later added to trades.
    Built WITHOUT init_db(): that now creates the (trade_id, event_date,
    event_type) key, and a duplicate event can only be written where the key
    is missing. One bot-era trade, created_at NULL by default like every
    trade since the rebuild dropped that column's DEFAULT."""
    fixture_db = tmp_path / "old_shape.db"
    from strategies.support import db as _db_mod, trade_db
    monkeypatch.setattr(trade_db, "DB_PATH", fixture_db)
    monkeypatch.setattr(_db_mod, "DASH_DB", fixture_db)
    con = sqlite3.connect(str(fixture_db))
    for table in ("trades", "trade_adjustments"):
        for sql in old_shape_ddl(table):
            con.execute(sql)
    con.execute("ALTER TABLE trades ADD COLUMN unique_key TEXT")
    con.execute("""
        INSERT INTO trades
        (id, series, asset, direction, strategy, entry_time, status,
         execution_mode, strategy_variant, actual_entry_time, created_at)
        VALUES ('SJ-4247', 'SJ', 'BTC', 'LONG', 'CHENTO_TRIPLE_V3',
                '2026-08-22T06:15:00+00:00', 'closed', 'paper', 'live_v',
                '2026-08-22T06:15:00+00:00', ?)
    """, (created_at,))
    events = ["2026-08-22T06:15:00+00:00 OPEN"]
    if duplicate:
        events.append("2026-08-22T06:15:33+00:00 OPEN")   # the retry, at seq+1
    if close_event:
        events.append("2026-08-22T19:45:00+00:00 CLOSE")
    for seq, ev in enumerate(events):
        event_time, event_type = ev.split()
        con.execute("""
            INSERT INTO trade_adjustments
            (trade_id, seq, event_type, event_time, event_date)
            VALUES ('SJ-4247', ?, ?, ?, ?)
        """, (seq, event_type, event_time, event_time[:10]))
    con.commit()
    con.close()


def test_duplicate_adjustment_event_detected(tmp_path, monkeypatch):
    """A same-day retry that the missing key let through: a second OPEN for
    the same trade and UTC day at seq+1. The seqs stay contiguous, so the
    seq-gap detector cannot see it; only grouping on the key can."""
    _old_shape_ledger(tmp_path, monkeypatch, duplicate=True)
    audit = lc.audit_ledger()
    assert audit.n_duplicate_adjustment_events == 1
    sample = audit.duplicate_adjustment_event_samples[0]
    assert (sample["trade_id"], sample["event_date"], sample["event_type"],
            sample["cnt"]) == ("SJ-4247", "2026-08-22", "OPEN", 2)
    assert sorted(sample["seqs"].split(",")) == ["0", "1"]
    assert audit.n_trades_with_adjustment_seq_gaps == 0
    assert ("[FAIL] duplicate (trade_id,event_date,event_type) adjustment events: 1"
            in lc.format_ledger_coherence(audit))
    assert lc._main([]) == 1


def test_open_and_close_on_one_day_are_not_duplicate_events(tmp_path, monkeypatch):
    """Twin: the same trade's OPEN and CLOSE on one UTC day are two events."""
    _old_shape_ledger(tmp_path, monkeypatch, duplicate=False)
    audit = lc.audit_ledger()
    assert audit.n_duplicate_adjustment_events == 0
    assert audit.duplicate_adjustment_event_samples == ()
    assert lc._main([]) == 0


def test_trade_with_created_at_is_audited_for_a_missing_close(tmp_path, monkeypatch):
    """The twin below minus the NULL: proves the fixture's closed trade with
    no CLOSE event is a detectable defect, so the xfail is about created_at."""
    _old_shape_ledger(tmp_path, monkeypatch, duplicate=False, close_event=False,
                      created_at="2026-08-22 06:15:00")
    assert lc.audit_ledger().n_post_jplus_closed_missing_close_event == 1


def test_created_at_null_trade_is_audited_for_a_missing_close(tmp_path, monkeypatch):
    """BACKLOG item 20, fixed 2026-09-19: a trade whose created_at is NULL is
    keyed on its actual_entry_time instead, so the bot-era rows that carried
    NULL from the 2026-05-18 rebuild to the backfill are audited too."""
    _old_shape_ledger(tmp_path, monkeypatch, duplicate=False, close_event=False)
    assert lc.audit_ledger().n_post_jplus_closed_missing_close_event == 1


def test_cli_exit_code_zero_on_clean_db(tmp_path, monkeypatch, capsys):
    """CLI returns exit 0 when no FAIL-severity issues."""
    fixture_db = tmp_path / "clean.db"
    from strategies.support import trade_db, db as _db_mod
    monkeypatch.setattr(trade_db, "DB_PATH", fixture_db)
    monkeypatch.setattr(_db_mod, "DASH_DB", fixture_db)
    trade_db.init_db()
    assert lc._main([]) == 0


def test_cli_exit_code_one_on_failures(synthetic_ledger_db):
    """CLI returns exit 1 when any FAIL detector trips."""
    assert lc._main([]) == 1

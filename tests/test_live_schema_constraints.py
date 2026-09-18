"""Declared vs live schema constraints for the tables the bots write.

CREATE TABLE IF NOT EXISTS is a no-op on a table that exists, so a constraint
added to the DDL later, or dropped by a rebuild, never reaches prod.db and no
code path notices. The 2026-05-18 PK rebuild
(data/migrations/2026_05_18_add_table_pks.py) did exactly that: it dropped the
(trade_id, event_date, event_type) idempotency key from trade_adjustments,
the NOT NULLs and DEFAULTs from trades and a DEFAULT from ai_quant_decisions,
and nothing failed for four months.

The declared schema is built in tmp by the real init functions — never copied
DDL — and compared against prod.db opened read-only: unique column sets with
their partial flag, and per column the type, NOT NULL, DEFAULT and PK
position, plus foreign keys.

Known drift (BACKLOG item 19) is pinned EXACTLY per table, and that case
carries a strict xfail whose only accepted failure is KnownDrift, raised when
the live drift equals the pin. So a table with no drift passes; pinned drift
xfails; any other difference, on any table, is a plain AssertionError and
fails, including new drift on a table that already drifts; and fixed drift
XPASSes, which strict=True turns into a failure (drop the pin in the fix's
commit). tests/test_table_classification.py covers which tables exist.

trade_adjustments' unique keys are deliberately NOT pinned. Until
data/migrations/2026_09_14_trade_adjustments_unique_index.py --apply has run
on prod.db, that case FAILS: prod lacks uix_adj_trade_date_type, which is the
point. Apply the migration before running the full suite.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

import pytest

import botlib
from strategies.support import db as _db_mod
from strategies.support import trade_db, variant_registry
from tests.test_trade_adjustments import old_shape_ddl

REPO = Path(__file__).resolve().parents[1]
LIVE = REPO / "data" / "databases" / "prod.db"

DECLARED_TABLES = ("ai_quant_decisions", "bot_heartbeats", "bot_tick_daily",
                   "bot_ticks", "config",
                   "fear_greed_index", "trade_adjustments", "trades",
                   "variant_events", "variants")

EVENT_KEY = (("trade_id", "event_date", "event_type"), "full")
FIELDS = ("type", "notnull", "dflt_value", "pk")


def lost_not_null(*columns: str) -> dict:
    return {(c, "notnull"): (1, 0) for c in columns}


def lost_defaults(**defaults: str) -> dict:
    """Keyword per column, value = the declared DEFAULT as PRAGMA prints it."""
    return {(c, "dflt_value"): (d, None) for c, d in defaults.items()}


# ─── Known drift, pinned: {table: (exact drift, reason)} ────────────────────

KNOWN_KEY_DRIFT = {
    "variants": (
        {"missing": [(("id",), "full"), (("id",), "primary key")]},
        "BACKLOG item 19: live variants has no PRIMARY KEY(id). Its DDL is the "
        "bare CREATE TABLE ... AS SELECT shape (INT types, no NOT NULL, CHECK "
        "or DEFAULT) left by the 2026-05-15 P2.6 consolidation "
        "(studies/simulation/migrate_to_prod_db.py, removed in 574ea6e); the "
        "2026-05-18 PK rebuild did not include variants. Only the partial "
        "idx_variants_primary came back, via init_schema. register_variant's "
        "plain INSERT and botlib.ensure_bot_variant's check-then-insert rely "
        "on the key, so two simultaneous starts of one bot can register the "
        "same id twice."),
}

KNOWN_COLUMN_DRIFT = {
    "ai_quant_decisions": (
        lost_defaults(created_at="datetime('now')"),
        "BACKLOG item 19: the 2026-05-18 PK rebuild's DDL dropped the "
        "created_at DEFAULT. Archived sleeve, no live writer."),
    "trade_adjustments": (
        {**lost_defaults(qty_delta="0", margin_delta_usdt="0", fee_usdt="0",
                         realized_pnl_delta_usdt="0"),
         ("<foreign keys>", "list"): (
             [("trades", "trade_id", "id", "NO ACTION", "NO ACTION", "NONE")], [])},
        "BACKLOG item 19: the 2026-05-18 PK rebuild's DDL dropped DEFAULT 0 on "
        "qty_delta/margin_delta_usdt/fee_usdt/realized_pnl_delta_usdt and "
        "REFERENCES trades(id). Harmless today: record_adjustment supplies "
        "every column (0 NULLs live) and foreign keys are off on every ledger "
        "connection. Deliberately NOT restored by the 2026-09-14 "
        "uix_adj_trade_date_type migration, which adds the key only."),
    "trades": (
        {**lost_not_null("series", "asset", "direction", "strategy"),
         **lost_defaults(leverage="1.0", status="'pending'", venue="'MEXC'",
                         execution_mode="'PAPER'", strategy_variant="'prod'",
                         created_at="datetime('now')", realized_pnl_usdt="0")},
        "BACKLOG item 19: the 2026-05-18 PK rebuild's DDL dropped NOT NULL on "
        "series/asset/direction/strategy and the DEFAULTs on leverage/status/"
        "venue/execution_mode/strategy_variant/created_at/realized_pnl_usdt. "
        "open_paper_trade never writes created_at, so every trade since has "
        "created_at NULL, which blinds ledger_coherence (BACKLOG item 20)."),
    "variants": (
        {("id", "pk"): (1, 0),
         **lost_not_null("short_name", "kind", "version", "status",
                         "is_primary", "spec_json", "enabled", "created_at"),
         **lost_defaults(status="'paper'", is_primary="0", enabled="1",
                         created_at="datetime('now')"),
         ("is_primary", "type"): ("INTEGER", "INT"),
         ("enabled", "type"): ("INTEGER", "INT")},
        "BACKLOG item 19: the P2.6 CREATE TABLE ... AS SELECT shape (see the "
        "key case) has no PRIMARY KEY, NOT NULL or DEFAULTs, and INT for "
        "INTEGER; CHECK(kind) is gone too but PRAGMA cannot show it. "
        "botlib.ensure_bot_variant patches the enabled=NULL fallout, and all "
        "9 bot variants have created_at NULL (BACKLOG item 20)."),
}


# ─── Helpers ────────────────────────────────────────────────────────────────

def unique_keys(con: sqlite3.Connection, table: str) -> list[tuple]:
    """Every uniqueness guarantee on `table`, sorted: one (columns, partial)
    per unique index — named, inline UNIQUE or a PK autoindex — plus
    (columns, 'primary key') from table_info, which also covers an INTEGER
    PRIMARY KEY that has no index. A list, not a set, so two identical
    unique indexes count twice."""
    keys = []
    for _seq, name, unique, _origin, partial in con.execute(
            f"PRAGMA index_list('{table}')"):
        if unique:
            cols = tuple(r[2] for r in con.execute(f"PRAGMA index_info('{name}')"))
            keys.append((cols, "partial" if partial else "full"))
    pk = tuple(r[1] for r in sorted(con.execute(f"PRAGMA table_info('{table}')"),
                                    key=lambda r: r[5]) if r[5])
    if pk:
        keys.append((pk, "primary key"))
    return sorted(keys)


def key_drift(declared: sqlite3.Connection, live: sqlite3.Connection,
              table: str) -> dict:
    """{'missing': declared keys live lacks, 'extra': live keys not
    declared}, each entry present only when non-empty."""
    d, l = Counter(unique_keys(declared, table)), Counter(unique_keys(live, table))
    drift = {"missing": sorted((d - l).elements()),
             "extra": sorted((l - d).elements())}
    return {k: v for k, v in drift.items() if v}


def column_drift(declared: sqlite3.Connection, live: sqlite3.Connection,
                 table: str) -> dict:
    """{(column, field): (declared, live)} for every declared column whose
    type, NOT NULL, DEFAULT or PK position differs live; a column live lacks
    is (column, 'column'): ('present', None). Foreign keys differ as
    ('<foreign keys>', 'list'): (declared, live). Columns only live has are
    not drift. CHECK constraints are not visible through PRAGMA and are not
    compared."""
    def columns(con):
        return {r[1]: tuple(r[2:6]) for r in con.execute(f"PRAGMA table_info('{table}')")}

    def foreign_keys(con):
        return sorted(tuple(r[2:]) for r in con.execute(
            f"PRAGMA foreign_key_list('{table}')"))

    d, l = columns(declared), columns(live)
    drift = {}
    for col, shape in d.items():
        if col not in l:
            drift[(col, "column")] = ("present", None)
            continue
        for field, dv, lv in zip(FIELDS, shape, l[col]):
            if dv != lv:
                drift[(col, field)] = (dv, lv)
    if foreign_keys(declared) != foreign_keys(live):
        drift[("<foreign keys>", "list")] = (foreign_keys(declared), foreign_keys(live))
    return drift


class KnownDrift(Exception):
    """The live drift is exactly the pinned drift: the only failure the strict
    xfail marks accept."""


def check(table: str, drift: dict, known: dict) -> None:
    if table in known and drift == known[table][0]:
        raise KnownDrift(f"{table}: {known[table][1]}")
    assert not drift, f"{table} declared -> live drift: {drift}"


def _cases(known: dict) -> list:
    return [pytest.param(t, marks=pytest.mark.xfail(strict=True, raises=KnownDrift,
                                                    reason=known[t][1]))
            if t in known else t
            for t in DECLARED_TABLES]


# ─── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def declared(tmp_path_factory):
    """A fresh DB holding exactly what the init functions declare."""
    path = tmp_path_factory.mktemp("declared") / "declared.db"
    with pytest.MonkeyPatch.context() as mp:
        for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
            mp.setattr(_db_mod, name, path)
        mp.setattr(trade_db, "DB_PATH", path)
        trade_db.init_db()
        variant_registry.init_schema()
        botlib.init_heartbeat_schema()
    con = sqlite3.connect(str(path))
    yield con
    con.close()


@pytest.fixture(scope="module")
def live():
    if not LIVE.exists():
        pytest.skip("prod.db not present")
    con = sqlite3.connect(f"file:{LIVE}?mode=ro", uri=True)
    yield con
    con.close()


# ─── Live prod.db ───────────────────────────────────────────────────────────

def test_declared_tables_are_all_checked(declared):
    """A table added to an init function must be added here too."""
    tables = {r[0] for r in declared.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'")}
    assert tables == set(DECLARED_TABLES)


@pytest.mark.parametrize("table", _cases(KNOWN_KEY_DRIFT))
def test_live_prod_db_has_every_declared_unique_key(declared, live, table):
    check(table, key_drift(declared, live, table), KNOWN_KEY_DRIFT)


@pytest.mark.parametrize("table", _cases(KNOWN_COLUMN_DRIFT))
def test_live_prod_db_columns_match_declaration(declared, live, table):
    check(table, column_drift(declared, live, table), KNOWN_COLUMN_DRIFT)


# ─── Non-vacuity: the helpers see the drift they exist for ─────────────────

@pytest.fixture
def old_shape_ledger(tmp_path):
    """trades + trade_adjustments exactly as the 2026-05-18 PK rebuild left
    them."""
    path = tmp_path / "old.db"
    con = sqlite3.connect(str(path))
    for table in ("trades", "trade_adjustments"):
        for sql in old_shape_ddl(table):
            con.execute(sql)
    con.commit()
    con.close()
    return path


def test_helpers_see_the_2026_05_18_drift(declared, old_shape_ledger):
    """Runs everywhere, prod.db or not, so the live cases above cannot pass
    by comparing two blind helpers. The rebuild's DDL is what prod carries
    for both tables, so their column drift must equal the pins."""
    old = sqlite3.connect(str(old_shape_ledger))
    try:
        assert EVENT_KEY in unique_keys(declared, "trade_adjustments")
        assert key_drift(declared, old, "trade_adjustments") == {"missing": [EVENT_KEY]}
        assert (column_drift(declared, old, "trade_adjustments")
                == KNOWN_COLUMN_DRIFT["trade_adjustments"][0])
        # init_db() added unique_key to trades after the rebuild; prod has it.
        assert column_drift(declared, old, "trades") == {
            **KNOWN_COLUMN_DRIFT["trades"][0], ("unique_key", "column"): ("present", None)}
    finally:
        old.close()


def test_key_drift_sees_a_lost_partial_clause(declared, tmp_path, monkeypatch):
    """uix_trades_unique_key is partial on purpose (legacy NULL keys); a full
    index on the same column is a different key, not a match."""
    path = tmp_path / "full.db"
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, path)
    monkeypatch.setattr(trade_db, "DB_PATH", path)
    trade_db.init_db()
    con = sqlite3.connect(str(path))
    try:
        assert key_drift(declared, con, "trades") == {}
        con.execute("DROP INDEX uix_trades_unique_key")
        con.execute("CREATE UNIQUE INDEX uix_trades_unique_key ON trades(unique_key)")
        assert key_drift(declared, con, "trades") == {
            "missing": [(("unique_key",), "partial")],
            "extra": [(("unique_key",), "full")]}
    finally:
        con.close()


def test_key_drift_sees_a_unique_index_rebuilt_as_plain(declared, tmp_path, monkeypatch):
    """Only unique indexes are keys. A plain index under the key's name is the
    shape the 2026-09-14 migration refuses with exit 4, and CREATE ... IF NOT
    EXISTS in init_db would keep it, so the live case must not count it."""
    path = tmp_path / "plain.db"
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, path)
    monkeypatch.setattr(trade_db, "DB_PATH", path)
    trade_db.init_db()
    con = sqlite3.connect(str(path))
    try:
        assert key_drift(declared, con, "trade_adjustments") == {}
        con.execute("DROP INDEX uix_adj_trade_date_type")
        con.execute("CREATE INDEX uix_adj_trade_date_type "
                    "ON trade_adjustments(trade_id, event_date, event_type)")
        assert key_drift(declared, con, "trade_adjustments") == {"missing": [EVENT_KEY]}
    finally:
        con.close()


def test_check_accepts_only_the_exact_pinned_drift():
    """The strict xfail marks accept KnownDrift only, so check() must raise it
    for the pinned drift and nothing else."""
    pinned = KNOWN_COLUMN_DRIFT["trades"][0]
    with pytest.raises(KnownDrift):
        check("trades", dict(pinned), KNOWN_COLUMN_DRIFT)
    # Drift beyond the pin, on a field the pin does not mention, is new drift
    # even though the pinned drift is all still there.
    extra = {("entry_price", "type"): ("REAL", "TEXT")}
    assert not set(extra) & set(pinned)
    with pytest.raises(AssertionError):
        check("trades", {**pinned, **extra}, KNOWN_COLUMN_DRIFT)
    # So is a pinned field whose values changed.
    changed = lost_defaults(venue="'BINANCE'")
    assert set(changed) <= set(pinned)
    with pytest.raises(AssertionError):
        check("trades", {**pinned, **changed}, KNOWN_COLUMN_DRIFT)
    # Part of the drift fixed is a change too: update the pin with the fix.
    with pytest.raises(AssertionError):
        check("trades", dict(list(pinned.items())[1:]), KNOWN_COLUMN_DRIFT)
    # Any drift on an unpinned table fails; no drift passes.
    with pytest.raises(AssertionError):
        check("config", lost_defaults(updated_at="datetime('now')"), KNOWN_COLUMN_DRIFT)
    check("config", {}, KNOWN_COLUMN_DRIFT)


@pytest.mark.parametrize("known", [KNOWN_KEY_DRIFT, KNOWN_COLUMN_DRIFT],
                         ids=["keys", "columns"])
def test_pinned_cases_xfail_strictly_and_only_on_known_drift(known):
    """The exact-pin rule lives in the marks as much as in check(): without
    raises=KnownDrift any AssertionError, new drift included, would xfail,
    and without strict=True fixed drift would XPASS quietly. The live cases
    cannot show either (they need prod.db, and the trade_adjustments key case
    is red until the migration runs), so the marks are checked here."""
    pinned = set()
    for case in _cases(known):
        if isinstance(case, str):
            assert case not in known
            continue
        (table,) = case.values
        (mark,) = case.marks
        assert (mark.name, mark.kwargs.get("strict"), mark.kwargs.get("raises")) == (
            "xfail", True, KnownDrift)
        pinned.add(table)
    assert pinned == set(known)


def test_init_db_heals_the_key_but_not_the_columns(declared, old_shape_ledger,
                                                    monkeypatch):
    """The named index alone clears the key drift on the old shape, which is
    what the 2026-09-14 migration relies on for prod; the DEFAULT/REFERENCES
    and NOT NULL drift stays, which is why both tables keep their column
    pins."""
    path = old_shape_ledger
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, path)
    monkeypatch.setattr(trade_db, "DB_PATH", path)
    trade_db.init_db()
    healed = sqlite3.connect(str(path))
    try:
        for table in ("trades", "trade_adjustments"):
            assert key_drift(declared, healed, table) == {}
            assert column_drift(declared, healed, table) == KNOWN_COLUMN_DRIFT[table][0]
    finally:
        healed.close()

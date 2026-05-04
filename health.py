"""Health check for the P-300 bot.

Exits 0 if everything looks healthy, non-zero if something is wrong.
Designed to run on a cron / monitor — output is plain text, sections
clearly delimited, with PASS / WARN / FAIL tags you can grep for.

Checks:
  1. Both databases exist and are readable.
  2. Required trader.db tables are present and reasonably fresh.
  3. Required dashboard.db tables are present.
  4. Variant p300_aggressive_v2_v1_0 is registered and enabled.
  5. Every strategy_id in the variant spec is wired into STRATEGY_DISPATCH.
  6. Data coverage is sufficient for Core J+ regime classifier (≥ 80d of BTC
     daily closes from cd_futures_ohlcv).
  7. CPR warmup OK (≥ 210d of ca_long_short_ratio rows).
  8. Data continuity — no gaps in cadence-based tables (klines, funding, LSR).
  9. No anomalously-open tactical trades (more than 1 per (variant, sleeve,
     asset) is a single-open-invariant violation — the bug we fixed).

Non-zero exit codes:
  1  Database missing
  2  Schema mismatch (required table absent)
  3  Variant not registered
  4  Dispatch not wired
  5  Data too stale / insufficient
  6  Invariant violation (multi-open trades)
  7  Data continuity violation (gaps in time-series tables)
  99 Unknown error
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
TRADER_DB = REPO / "data" / "trader.db"
DASH_DB = REPO / "data" / "dashboard.db"
UNFILLABLE_PATH = REPO / "data" / "known_unfillable.json"
VARIANT_ID = "p300_aggressive_v2_v1_0"


class HealthError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


def _ok(tag: str, msg: str) -> None:
    print(f"  [PASS] {tag:<30} {msg}")


def _warn(tag: str, msg: str) -> None:
    print(f"  [WARN] {tag:<30} {msg}")


def _fail(tag: str, msg: str, code: int) -> None:
    print(f"  [FAIL] {tag:<30} {msg}")
    raise HealthError(code, f"{tag}: {msg}")


def _fail_soft(tag: str, msg: str, failures: list[str]) -> None:
    """FAIL line that records but doesn't raise — used by continuity check
    so the report shows every failing table, not just the first."""
    print(f"  [FAIL] {tag:<30} {msg}")
    failures.append(f"{tag}: {msg}")


def _human_dur(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        h, m = divmod(seconds, 3600)
        return f"{h}h" if m < 60 else f"{h}h {m // 60}m"
    d, rem = divmod(seconds, 86400)
    h = rem // 3600
    return f"{d}d" if h == 0 else f"{d}d {h}h"


def _fmt_ts_s(ts_s: int) -> str:
    return datetime.fromtimestamp(int(ts_s), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _fmt_ts_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(int(ts_ms) // 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _load_unfillable() -> dict[tuple[str, str], list[tuple[int, int]]]:
    """Read data/known_unfillable.json and return
    {(table, asset): [(start_ts, end_ts), ...]} for fast lookup.

    Each entry's window is INCLUSIVE in the table's native ts unit."""
    if not UNFILLABLE_PATH.exists():
        return {}
    import json
    data = json.loads(UNFILLABLE_PATH.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for entry in data.get("entries", []):
        key = (entry["table"], entry.get("asset", ""))
        out.setdefault(key, []).append(
            (int(entry["start_ts"]), int(entry["end_ts"]))
        )
    return out


def _gap_is_unfillable(gap_start: int, gap_end: int,
                        unfillable: list[tuple[int, int]]) -> bool:
    """True if (gap_start, gap_end) is fully covered by some unfillable window."""
    for u_start, u_end in unfillable:
        if gap_start >= u_start and gap_end <= u_end:
            return True
    return False


# ─── Individual checks ───────────────────────────────────────────────────────

def check_databases() -> None:
    print("\n=== Databases ===")
    for name, path in [("trader.db", TRADER_DB), ("dashboard.db", DASH_DB)]:
        if not path.exists():
            _fail(name, f"missing at {path}", 1)
        # Open/close to verify readable
        sqlite3.connect(str(path)).close()
        _ok(name, f"{path} ({path.stat().st_size/1_048_576:.1f} MB)")


def check_trader_tables() -> None:
    print("\n=== trader.db tables ===")
    # freshness_s is compared against age in SECONDS regardless of the
    # table's timestamp unit; the unit conversion happens below.
    required = [
        ("cd_futures_ohlcv",    "timestamp", "BTC perp hourly OHLCV",    86_400),      # 24h
        ("btc_1m",              "open_time", "BTC spot 1m (ms)",         86_400),      # 24h
        ("eth_1m",              "open_time", "ETH spot 1m (ms)",         86_400),      # 24h
        ("cd_funding_rate",     "timestamp", "BTC funding settlements",  86_400),      # 24h
        ("cd_funding_rate_eth", "timestamp", "ETH funding settlements",  86_400),      # 24h
        ("ca_long_short_ratio", "timestamp", "BTC long/short ratio",     86_400 * 2),  # 48h (refreshed by binance_feed)
        ("scheduled_events",    "date",      "static event calendar",    None),
    ]
    con = sqlite3.connect(str(TRADER_DB))
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    for name, ts_col, desc, freshness_s in required:
        if name not in tables:
            _fail(name, f"MISSING — {desc}", 2)
            continue
        try:
            max_ts = con.execute(f"SELECT MAX({ts_col}) FROM {name}").fetchone()[0]
        except sqlite3.OperationalError as e:
            _fail(name, f"query failed: {e}", 2)
            continue
        if max_ts is None:
            _fail(name, "table is EMPTY", 2)
            continue
        n = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        # Freshness check (skip if None — table is static)
        if freshness_s is None:
            _ok(name, f"{n:,} rows (static)")
            continue
        # ms vs s handling
        max_s = int(max_ts) // 1000 if ts_col == "open_time" else int(max_ts)
        age_s = int(datetime.now(timezone.utc).timestamp()) - max_s
        if age_s <= freshness_s:
            _ok(name, f"{n:,} rows, latest {age_s // 3600}h ago")
        else:
            _warn(name, f"{n:,} rows, STALE: latest {age_s // 3600}h ago "
                        f"(freshness threshold {freshness_s // 3600}h)")
    con.close()


def check_dashboard_tables() -> None:
    print("\n=== dashboard.db tables ===")
    required = ["variants", "variant_events", "variant_daily_returns", "trades", "config"]
    con = sqlite3.connect(str(DASH_DB))
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    for t in required:
        if t not in tables:
            _fail(t, "MISSING — run register_p300.py", 2)
        else:
            n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            _ok(t, f"{n:,} rows")
    con.close()


def check_variant_registration() -> None:
    print("\n=== variant registration ===")
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT id, short_name, status, enabled FROM variants WHERE id = ?",
        (VARIANT_ID,),
    ).fetchone()
    con.close()
    if row is None:
        _fail("variant", f"{VARIANT_ID} NOT REGISTERED — run register_p300.py", 3)
    if row["enabled"] != 1:
        _fail("variant", f"{VARIANT_ID} DISABLED", 3)
    _ok("variant", f"{VARIANT_ID} ({row['short_name']}, status={row['status']})")


def check_dispatch_wired() -> None:
    print("\n=== dispatch wiring ===")
    import json
    con = sqlite3.connect(str(DASH_DB))
    row = con.execute("SELECT spec_json FROM variants WHERE id = ?",
                      (VARIANT_ID,)).fetchone()
    con.close()
    if row is None:
        _fail("dispatch", "variant missing; can't check", 3)
    spec = json.loads(row[0])
    strategy_ids = [s["strategy_id"] for s in spec.get("composition", [])
                    if s.get("strategy_id")]
    from services import variant_engine
    variant_engine._load_dispatch()
    for sid in strategy_ids:
        if sid in variant_engine.STRATEGY_DISPATCH:
            _ok(sid, "dispatch wired")
        else:
            _fail(sid, "NOT in STRATEGY_DISPATCH — check services/variant_engine.py", 4)


def check_warmup_sufficiency() -> None:
    print("\n=== warmup data sufficiency ===")
    con = sqlite3.connect(str(TRADER_DB))
    # Core J+ needs ≥ 80 BTC daily candles
    daily_count = con.execute("""
        SELECT COUNT(DISTINCT date(timestamp, 'unixepoch')) FROM cd_futures_ohlcv
    """).fetchone()[0]
    if daily_count < 80:
        _fail("BTC daily bars", f"only {daily_count} days — need ≥ 80 for regime", 5)
    _ok("BTC daily bars", f"{daily_count} days — regime classifier ready")
    # CPR needs ≥ 210 LS-ratio days
    ls_count = con.execute("""
        SELECT COUNT(DISTINCT date(timestamp, 'unixepoch'))
        FROM ca_long_short_ratio WHERE asset='BTC'
    """).fetchone()[0]
    if ls_count < 210:
        _warn("LS ratio (BTC)", f"{ls_count} days — CPR warmup needs ≥ 210")
    else:
        _ok("LS ratio (BTC)", f"{ls_count} days — CPR ready")
    con.close()


def _largest_gap_seconds(con: sqlite3.Connection, table: str, ts_col: str,
                          where: str = "") -> tuple[int, int, int, int, int]:
    """Return (count, min_ts, max_ts, max_delta, second_max_delta).
    `where` is an optional WHERE clause body (no leading WHERE)."""
    where_clause = f"WHERE {where}" if where else ""
    row = con.execute(
        f"SELECT COUNT(*), MIN({ts_col}), MAX({ts_col}) "
        f"FROM {table} {where_clause}"
    ).fetchone()
    count, min_ts, max_ts = row
    if not count or min_ts is None:
        return 0, 0, 0, 0, 0
    max_delta = con.execute(f"""
        SELECT COALESCE(MAX(delta), 0) FROM (
            SELECT {ts_col} - LAG({ts_col}) OVER (ORDER BY {ts_col}) AS delta
            FROM {table} {where_clause}
        )
    """).fetchone()[0]
    return int(count), int(min_ts), int(max_ts), int(max_delta or 0), 0


def _gap_start(con: sqlite3.Connection, table: str, ts_col: str,
                target_delta: int, where: str = "") -> int | None:
    """Return the prev_ts where the largest gap begins."""
    where_clause = f"WHERE {where}" if where else ""
    row = con.execute(f"""
        WITH ordered AS (
            SELECT {ts_col} AS ts,
                   LAG({ts_col}) OVER (ORDER BY {ts_col}) AS prev
            FROM {table} {where_clause}
        )
        SELECT prev FROM ordered WHERE ts - prev = ? LIMIT 1
    """, (target_delta,)).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _check_seconds_table(con: sqlite3.Connection, label: str, table: str,
                          ts_col: str, cadence_s: int, max_gap_s: int,
                          fix_cmd: str, failures: list[str],
                          where: str = "",
                          unfillable: list[tuple[int, int]] | None = None) -> None:
    """Generic continuity check for a table whose ts column is in seconds.

    cadence_s   = expected cadence (used for `expected = (max-min)/cadence + 1`)
    max_gap_s   = largest tolerated delta between consecutive rows. FAIL if
                  any fillable delta exceeds this.
    unfillable  = list of (start_ts, end_ts) windows known to be source-side
                  holes. Gaps fully contained in any window are reported as
                  unfillable rather than counted as failures.
    """
    unfillable = unfillable or []
    where_clause = f"WHERE {where}" if where else ""
    count_row = con.execute(
        f"SELECT COUNT(*), MIN({ts_col}), MAX({ts_col}) FROM {table} {where_clause}"
    ).fetchone()
    count, min_ts, max_ts = count_row
    if not count or min_ts is None:
        _fail_soft(label, "EMPTY", failures)
        return
    expected = (int(max_ts) - int(min_ts)) // cadence_s + 1

    # Enumerate every gap so we can classify each as fillable / unfillable.
    gap_rows = con.execute(f"""
        WITH ordered AS (
            SELECT {ts_col} AS ts,
                   LAG({ts_col}) OVER (ORDER BY {ts_col}) AS prev
            FROM {table} {where_clause}
        )
        SELECT prev, ts FROM ordered WHERE ts - prev > ? ORDER BY prev
    """, (cadence_s,)).fetchall()

    fillable: list[tuple[int, int]] = []
    unfillable_rows = 0
    for prev, ts in gap_rows:
        gap_start = int(prev) + cadence_s
        gap_end = int(ts) - cadence_s
        if _gap_is_unfillable(gap_start, gap_end, unfillable):
            unfillable_rows += (gap_end - gap_start) // cadence_s + 1
        else:
            fillable.append((gap_start, gap_end))

    if not fillable:
        if unfillable_rows > 0:
            _ok(label, f"{count:,} rows, {unfillable_rows} unfillable "
                       f"(known source-side holes)")
        else:
            _ok(label, f"{count:,} rows, 0 gaps")
        return

    fillable_missing = sum((e - s) // cadence_s + 1 for s, e in fillable)
    largest = max(fillable, key=lambda g: g[1] - g[0])
    largest_dur = (largest[1] - largest[0]) + cadence_s
    if largest_dur > max_gap_s or fillable_missing > 0:
        gap_dur = _human_dur(largest_dur - cadence_s) if largest_dur > cadence_s else f"{largest_dur}s"
        gap_start_str = _fmt_ts_s(largest[0])
        extra = f" (+{unfillable_rows} unfillable)" if unfillable_rows else ""
        msg = (f"{count:,} rows / {expected:,} expected -- "
               f"{fillable_missing:,} missing{extra}, "
               f"largest {gap_dur} at {gap_start_str}  -> run {fix_cmd}")
        _fail_soft(label, msg, failures)
    else:
        _warn(label, f"{count:,} rows, {len(fillable)} small fillable gap(s)")


def _check_1m_table(con: sqlite3.Connection, label: str, table: str,
                     failures: list[str],
                     unfillable: list[tuple[int, int]] | None = None) -> None:
    """1-minute kline tables. Strict policy applied to FILLABLE gaps only:
       - Any single fillable gap > 5 min   -> FAIL
       - Cumulative fillable > 60 min      -> FAIL
       - Total fillable missing > 1% rows  -> FAIL
       - Otherwise WARN

    `unfillable` is a list of (start_s, end_s) windows in SECONDS (matches the
    JSON file). Internally we convert to ms to match the table's unit."""
    cadence_ms = 60_000
    unfillable_ms = [(s * 1000, e * 1000) for s, e in (unfillable or [])]

    count_row = con.execute(
        f"SELECT COUNT(*), MIN(open_time), MAX(open_time) FROM {table}"
    ).fetchone()
    count, min_ts, max_ts = count_row
    if not count or min_ts is None:
        _fail_soft(label, "EMPTY", failures)
        return
    expected = (int(max_ts) - int(min_ts)) // cadence_ms + 1

    gap_rows = con.execute(f"""
        WITH ordered AS (
            SELECT open_time AS ts,
                   LAG(open_time) OVER (ORDER BY open_time) AS prev
            FROM {table}
        )
        SELECT prev, ts FROM ordered WHERE ts - prev > ? ORDER BY prev
    """, (cadence_ms,)).fetchall()

    fillable: list[tuple[int, int]] = []
    unfillable_minutes = 0
    for prev, ts in gap_rows:
        gap_start = int(prev) + cadence_ms
        gap_end = int(ts) - cadence_ms
        if _gap_is_unfillable(gap_start, gap_end, unfillable_ms):
            unfillable_minutes += (gap_end - gap_start) // cadence_ms + 1
        else:
            fillable.append((gap_start, gap_end))

    if not fillable:
        if unfillable_minutes > 0:
            _ok(label, f"{count:,} rows, {unfillable_minutes} unfillable "
                       f"(known source-side holes)")
        else:
            _ok(label, f"{count:,} rows, 0 gaps")
        return

    fillable_missing = sum((e - s) // cadence_ms + 1 for s, e in fillable)
    largest = max(fillable, key=lambda g: g[1] - g[0])
    largest_gap_min = (largest[1] - largest[0]) // cadence_ms + 1
    pct_missing = (fillable_missing / expected * 100) if expected else 0
    gap_start_str = _fmt_ts_ms(largest[0])

    fail_reasons = []
    if largest_gap_min > 5:
        fail_reasons.append(f"single fillable gap {largest_gap_min}m > 5m")
    if fillable_missing > 60:
        fail_reasons.append(f"cumulative fillable {fillable_missing:,}m > 60m")
    if pct_missing > 1.0:
        fail_reasons.append(f"{pct_missing:.1f}% fillable missing > 1%")
    extra = f" (+{unfillable_minutes} unfillable)" if unfillable_minutes else ""
    summary = (f"{count:,} rows / {expected:,} expected -- "
               f"{fillable_missing:,} missing min{extra} "
               f"(largest {largest_gap_min}m at {gap_start_str})")
    if fail_reasons:
        _fail_soft(label, summary + "  -> run binance_feed.py --backfill-klines",
                    failures)
    else:
        _warn(label, summary + " -- likely exchange-side")


def _check_events_coverage(con: sqlite3.Connection, failures: list[str]) -> None:
    expected_types = {"FOMC", "CPI", "NFP", "OPEX_MONTHLY", "OPEX_QUARTERLY"}
    rows = con.execute(
        "SELECT event_type, COUNT(*), MIN(date), MAX(date) "
        "FROM scheduled_events GROUP BY event_type"
    ).fetchall()
    if not rows:
        _fail_soft("scheduled_events", "EMPTY -- run fetch_events.py", failures)
        return
    actual_types = {r[0] for r in rows}
    missing_types = expected_types - actual_types
    if missing_types:
        _fail_soft("scheduled_events",
                    f"missing event types {sorted(missing_types)} -- run fetch_events.py",
                    failures)
        return
    total = sum(r[1] for r in rows)
    overall = con.execute(
        "SELECT MIN(date), MAX(date) FROM scheduled_events"
    ).fetchone()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if overall[1] < today:
        _fail_soft("scheduled_events",
                    f"{total} rows but max date {overall[1]} is in the past -- "
                    f"calendar needs to be extended; bump FOMC_DECISIONS / "
                    f"CPI_DATES in fetch_events.py and re-run",
                    failures)
        return
    _ok("scheduled_events",
        f"{total} rows, all 5 event types present, range {overall[0]} -> {overall[1]}")


def check_data_continuity() -> None:
    """Verify no gaps within cadence-based tables. Reports every table
    individually so a single corrupted table doesn't mask others.
    Honors data/known_unfillable.json — gaps verified to be source-side
    holes (no provider has the data) are reported but don't cause FAIL."""
    print("\n=== data continuity ===")
    con = sqlite3.connect(str(TRADER_DB))
    failures: list[str] = []
    unfillable_map = _load_unfillable()

    # Hourly perpetual + spot — strict 1h cadence, FAIL on any gap.
    _check_seconds_table(con, "cd_futures_ohlcv (1h BTC perp)",
                          "cd_futures_ohlcv", "timestamp", 3600, 3600,
                          "binance_feed.py --backfill-klines", failures)
    _check_seconds_table(con, "cd_spot_binance (1h BTC spot)",
                          "cd_spot_binance", "timestamp", 3600, 3600,
                          "binance_feed.py --backfill-klines", failures)

    # Funding — BTC table is mixed-cadence (1h seeded + 8h fresh); use 8h
    # max-gap as the bar (deterministic 8h settlement schedule).
    _check_seconds_table(con, "cd_funding_rate (BTC, <=8h)",
                          "cd_funding_rate", "timestamp", 28800, 28800,
                          "binance_feed.py --backfill-funding", failures)
    _check_seconds_table(con, "cd_funding_rate_eth (8h)",
                          "cd_funding_rate_eth", "timestamp", 28800, 28800,
                          "binance_feed.py --backfill-funding", failures)

    # 1-minute klines -- strict policy on fillable gaps; honors unfillable list.
    _check_1m_table(con, "btc_1m", "btc_1m", failures,
                     unfillable=unfillable_map.get(("btc_1m", ""), []))
    _check_1m_table(con, "eth_1m", "eth_1m", failures,
                     unfillable=unfillable_map.get(("eth_1m", ""), []))

    # Long-short ratio -- daily per asset, strict. Honors known_unfillable.json
    # for source-side holes that Coinalyze doesn't serve.
    for asset in ("BTC", "ETH"):
        _check_seconds_table(con, f"ca_long_short_ratio {asset} (1d)",
                              "ca_long_short_ratio", "timestamp", 86400, 86400,
                              "fetch_coinalyze.py", failures,
                              where=f"asset='{asset}'",
                              unfillable=unfillable_map.get(
                                  ("ca_long_short_ratio", asset), []))

    # Static calendar — coverage check, not cadence.
    _check_events_coverage(con, failures)

    con.close()
    if failures:
        raise HealthError(7,
                          f"{len(failures)} continuity violation(s) -- see above")


def check_single_open_invariant() -> None:
    print("\n=== single-open invariant ===")
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    # Count open trades per (variant, strategy, asset)
    rows = con.execute("""
        SELECT strategy_variant, strategy, asset, COUNT(*) AS n
        FROM trades
        WHERE status = 'open' AND strategy_variant LIKE 'p300_%'
        GROUP BY strategy_variant, strategy, asset
        HAVING COUNT(*) > 1
    """).fetchall()
    con.close()
    if rows:
        for r in rows:
            _fail("invariant",
                  f"{r['n']} open {r['strategy']} trades for "
                  f"{r['strategy_variant']} / {r['asset']}", 6)
    _ok("single-open", "no (variant, sleeve, asset) has >1 open trade")


# ─── Entry ───────────────────────────────────────────────────────────────────

def main() -> int:
    print("P-300 health check")
    print(f"  time:  {datetime.now(timezone.utc).isoformat()}")
    print(f"  repo:  {REPO}")
    try:
        check_databases()
        check_trader_tables()
        check_dashboard_tables()
        check_variant_registration()
        check_dispatch_wired()
        check_warmup_sufficiency()
        check_data_continuity()
        check_single_open_invariant()
    except HealthError as e:
        print(f"\n{'=' * 60}\nHEALTH CHECK FAILED (exit {e.code}): {e}\n{'=' * 60}")
        return e.code
    except Exception as e:
        print(f"\n{'=' * 60}\nUNEXPECTED ERROR (exit 99): {e}\n{'=' * 60}")
        import traceback; traceback.print_exc()
        return 99
    print(f"\n{'=' * 60}\nALL CHECKS PASSED\n{'=' * 60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

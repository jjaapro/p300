"""Ledger coherence audit — detect state-corruption modes that the read-side
P&L view masks (duplicate trade rows, orphaned opens, missing ledger events,
replay-variant contamination).

All counts are ZERO in a healthy DB; any non-zero value is a flag worth
investigating, though some non-zero values are KNOWN ARTIFACTS (pre-J+
migration trades pre-dating the trade_adjustments ledger introduction on
2026-05-05). The audit honors a cutoff date so legacy rows don't generate
noise.

Used by:
  - strategies.support.strategy_health for inclusion in the health report
  - Ad-hoc audits via `python -m strategies.support.ledger_coherence`
  - Future: alerting layer that pages when any count exceeds expected
    baseline (e.g., n_duplicate_groups should NEVER exceed 0)

The Fix #4 of the 2026-06-05 architecture pass per joyful-singing-leaf.md.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from strategies.support import db


# Trades created before this date pre-date the J+ trade_adjustments ledger
# introduction (memory: core-jplus-trade-emitter, 2026-05-05). They have no
# OPEN/CLOSE events recorded but are NOT corruption — exclude from coherence
# checks that key on the adjustment ledger.
JPLUS_MIGRATION_CUTOFF_ISO = "2026-05-05T00:00:00+00:00"

# Stale-open threshold: enabled-variant open trades older than this without
# any adjustment activity are flagged as potentially orphaned.
STALE_OPEN_DAYS = 30


@dataclass(frozen=True)
class LedgerCoherence:
    """Point-in-time coherence audit of the trades / trade_adjustments ledger.

    All counts are ZERO in a healthy DB. Non-zero values are flags to
    investigate but not necessarily bugs — pre-J+ migration artifacts are
    excluded from coherence-keyed checks via JPLUS_MIGRATION_CUTOFF_ISO.
    """
    as_of: str

    # System-wide duplicate detection
    n_duplicate_groups: int                       # (variant, strategy, asset, entry_time) trios with >1 trade
    duplicate_samples: tuple[dict, ...] = ()      # up to 5 representative groups

    # Per-scope (variant filter if provided, else system-wide)
    variant_id_filter: str | None = None
    n_open_trades_enabled: int = 0                # open trades in enabled variants
    n_open_trades_stale: int = 0                  # open trades older than STALE_OPEN_DAYS
    n_open_trades_in_disabled_variants: int = 0   # enabled=0 variant residue (informational)

    # Adjustment-ledger coherence (post-J+-cutoff trades only)
    n_post_jplus_open_missing_open_event: int = 0
    n_post_jplus_closed_missing_close_event: int = 0
    n_trades_with_adjustment_seq_gaps: int = 0

    # Replay-variant isolation (the SJ-1169-class concern, fixed 2026-05-16)
    n_disabled_variants_with_recent_opens: int = 0  # opened a trade in last 7d (should be 0)
    recent_disabled_opens_sample: tuple[dict, ...] = ()


# ─── Audit core ────────────────────────────────────────────────────────────


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    return con


def _duplicate_groups(con: sqlite3.Connection,
                       variant_filter: str | None) -> tuple[int, tuple[dict, ...]]:
    where = ""
    params: tuple = ()
    if variant_filter:
        where = "WHERE strategy_variant = ?"
        params = (variant_filter,)
    rows = con.execute(f"""
        SELECT strategy_variant, strategy, asset, entry_time, COUNT(*) AS cnt
        FROM trades
        {where}
        GROUP BY strategy_variant, strategy, asset, entry_time
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC, entry_time DESC
    """, params).fetchall()
    samples = tuple(dict(r) for r in rows[:5])
    return len(rows), samples


def _open_trade_counts(con: sqlite3.Connection,
                        variant_filter: str | None,
                        as_of_dt: datetime) -> tuple[int, int, int]:
    """Return (n_open_enabled, n_open_stale, n_open_disabled)."""
    cutoff = (as_of_dt - timedelta(days=STALE_OPEN_DAYS)).isoformat()
    base_where = ["t.status = 'open'"]
    params: list = []
    if variant_filter:
        base_where.append("t.strategy_variant = ?")
        params.append(variant_filter)
    where_sql = " AND ".join(base_where)

    # Enabled-variant open trades: join with variants on the variant_id.
    # Variants table may or may not exist depending on bot state — guard.
    try:
        n_enabled = con.execute(f"""
            SELECT COUNT(*) FROM trades t
            JOIN variants v ON t.strategy_variant = v.id
            WHERE {where_sql} AND v.enabled = 1
        """, params).fetchone()[0]
        n_disabled = con.execute(f"""
            SELECT COUNT(*) FROM trades t
            JOIN variants v ON t.strategy_variant = v.id
            WHERE {where_sql} AND v.enabled = 0
        """, params).fetchone()[0]
        n_stale = con.execute(f"""
            SELECT COUNT(*) FROM trades t
            JOIN variants v ON t.strategy_variant = v.id
            WHERE {where_sql} AND v.enabled = 1 AND t.entry_time < ?
        """, params + [cutoff]).fetchone()[0]
    except sqlite3.OperationalError:
        # variants table missing; fall back to all-open count
        n_all = con.execute(f"SELECT COUNT(*) FROM trades t WHERE {where_sql}",
                              params).fetchone()[0]
        n_enabled = n_all; n_disabled = 0; n_stale = 0
    return n_enabled, n_stale, n_disabled


def _adjustment_coherence(con: sqlite3.Connection,
                           variant_filter: str | None) -> tuple[int, int, int]:
    """Return (n_open_missing_OPEN, n_closed_missing_CLOSE, n_seq_gaps)."""
    base_where = [f"t.created_at >= ?"]
    params: list = [JPLUS_MIGRATION_CUTOFF_ISO]
    if variant_filter:
        base_where.append("t.strategy_variant = ?")
        params.append(variant_filter)
    where_sql = " AND ".join(base_where)
    try:
        n_open_no_open = con.execute(f"""
            SELECT COUNT(*) FROM trades t
            LEFT JOIN (
                SELECT DISTINCT trade_id FROM trade_adjustments
                WHERE event_type = 'OPEN'
            ) a ON t.id = a.trade_id
            WHERE {where_sql} AND t.status = 'open' AND a.trade_id IS NULL
        """, params).fetchone()[0]
        n_closed_no_close = con.execute(f"""
            SELECT COUNT(*) FROM trades t
            LEFT JOIN (
                SELECT DISTINCT trade_id FROM trade_adjustments
                WHERE event_type = 'CLOSE'
            ) a ON t.id = a.trade_id
            WHERE {where_sql} AND t.status = 'closed' AND a.trade_id IS NULL
        """, params).fetchone()[0]
        # Seq-gap detection: per-trade max(seq) > count(*) means a gap
        n_seq_gaps = con.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT t.id, MAX(a.seq) AS max_seq, COUNT(a.seq) AS n_events
                FROM trades t
                JOIN trade_adjustments a ON t.id = a.trade_id
                WHERE {where_sql}
                GROUP BY t.id
                HAVING MAX(a.seq) != COUNT(a.seq) - 1  -- seq is 0-indexed; gap if max != count-1
            )
        """, params).fetchone()[0]
    except sqlite3.OperationalError:
        # trade_adjustments missing; cannot audit
        return 0, 0, 0
    return n_open_no_open, n_closed_no_close, n_seq_gaps


def _replay_isolation(con: sqlite3.Connection,
                       as_of_dt: datetime) -> tuple[int, tuple[dict, ...]]:
    """Detect disabled-variant opens in the last 7 days — these would
    indicate the enabled=1 filter regression."""
    cutoff = (as_of_dt - timedelta(days=7)).isoformat()
    try:
        rows = con.execute("""
            SELECT t.id, t.strategy_variant, t.strategy, t.asset, t.entry_time
            FROM trades t
            JOIN variants v ON t.strategy_variant = v.id
            WHERE v.enabled = 0 AND t.actual_entry_time >= ?
            ORDER BY t.actual_entry_time DESC
        """, (cutoff,)).fetchall()
    except sqlite3.OperationalError:
        return 0, ()
    return len(rows), tuple(dict(r) for r in rows[:5])


def audit_ledger(variant_id_filter: str | None = None,
                  as_of_dt: datetime | None = None) -> LedgerCoherence:
    """Run the full coherence audit. If variant_id_filter is given, per-scope
    counts are scoped to that variant; system-wide counts (duplicates,
    replay-isolation) are NOT scoped — they're always system-wide.
    """
    if as_of_dt is None:
        as_of_dt = datetime.now(timezone.utc)
    con = _con()
    try:
        n_dups, dup_samples = _duplicate_groups(con, variant_id_filter)
        n_open_en, n_open_st, n_open_dis = _open_trade_counts(
            con, variant_id_filter, as_of_dt)
        n_no_open, n_no_close, n_seq_gaps = _adjustment_coherence(
            con, variant_id_filter)
        n_replay_recent, replay_samples = _replay_isolation(con, as_of_dt)
    finally:
        con.close()
    return LedgerCoherence(
        as_of=as_of_dt.isoformat(),
        n_duplicate_groups=n_dups, duplicate_samples=dup_samples,
        variant_id_filter=variant_id_filter,
        n_open_trades_enabled=n_open_en,
        n_open_trades_stale=n_open_st,
        n_open_trades_in_disabled_variants=n_open_dis,
        n_post_jplus_open_missing_open_event=n_no_open,
        n_post_jplus_closed_missing_close_event=n_no_close,
        n_trades_with_adjustment_seq_gaps=n_seq_gaps,
        n_disabled_variants_with_recent_opens=n_replay_recent,
        recent_disabled_opens_sample=replay_samples,
    )


# ─── Formatter ─────────────────────────────────────────────────────────────


def format_ledger_coherence(lc: LedgerCoherence) -> str:
    """Render as a console-friendly block — one line per metric, with
    severity prefix (OK / WARN / FAIL)."""
    def status(count: int, threshold: int = 0,
               severity_above: str = "FAIL") -> str:
        if count <= threshold:
            return "OK   "
        return f"{severity_above}"

    scope = lc.variant_id_filter or "system-wide"
    lines = [
        "=" * 78,
        f"  Ledger Coherence  -  scope: {scope}",
        f"  As of {lc.as_of}",
        "=" * 78,
        "",
        f"  [{status(lc.n_duplicate_groups)}] duplicate (variant,strategy,asset,entry_time) groups: "
        f"{lc.n_duplicate_groups}",
        f"  [{status(lc.n_disabled_variants_with_recent_opens)}] disabled-variant opens in last 7d "
        f"(replay leak guard): {lc.n_disabled_variants_with_recent_opens}",
        f"  [{status(lc.n_post_jplus_open_missing_open_event)}] open trades missing OPEN event "
        f"(post-{JPLUS_MIGRATION_CUTOFF_ISO[:10]}): "
        f"{lc.n_post_jplus_open_missing_open_event}",
        f"  [{status(lc.n_post_jplus_closed_missing_close_event)}] closed trades missing CLOSE event "
        f"(post-{JPLUS_MIGRATION_CUTOFF_ISO[:10]}): "
        f"{lc.n_post_jplus_closed_missing_close_event}",
        f"  [{status(lc.n_trades_with_adjustment_seq_gaps)}] trades with adjustment seq gaps: "
        f"{lc.n_trades_with_adjustment_seq_gaps}",
        f"  [{status(lc.n_open_trades_stale, threshold=0, severity_above='WARN')}] "
        f"open trades > {STALE_OPEN_DAYS}d old in enabled variants: "
        f"{lc.n_open_trades_stale}",
        "",
        f"  Informational:",
        f"    open trades in enabled variants:  {lc.n_open_trades_enabled}",
        f"    open trades in disabled variants: {lc.n_open_trades_in_disabled_variants}",
    ]
    if lc.duplicate_samples:
        lines.append("")
        lines.append("  Duplicate samples:")
        for s in lc.duplicate_samples:
            lines.append(f"    {s}")
    if lc.recent_disabled_opens_sample:
        lines.append("")
        lines.append("  Recent disabled-variant opens:")
        for s in lc.recent_disabled_opens_sample:
            lines.append(f"    {s}")
    return "\n".join(lines)


# ─── CLI ───────────────────────────────────────────────────────────────────


def _main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--variant", default=None,
                    help="Scope per-variant metrics to a single variant ID")
    args = p.parse_args(argv)
    lc = audit_ledger(variant_id_filter=args.variant)
    print(format_ledger_coherence(lc))
    # Exit code: 0 if all FAIL-severity counts are 0, else 1
    fail = (lc.n_duplicate_groups + lc.n_disabled_variants_with_recent_opens
             + lc.n_post_jplus_open_missing_open_event
             + lc.n_post_jplus_closed_missing_close_event
             + lc.n_trades_with_adjustment_seq_gaps)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))

"""Backfill `ai_quant_decision_id` on legacy AI_QUANT trade rows.

AI_QUANT M2a (2026-05-16): the live emitter now sets
``trades.ai_quant_decision_id`` to the row id of the decision that
spawned each trade (see :func:`strategies.sleeves.ai_quant.signal._tag_trade_with_decision`).
This script populates that column on the AI_QUANT trade rows that
already existed before the migration shipped.

Matching rule (safe because AI_QUANT fires once per UTC day):

    For each trades row with strategy='AI_QUANT' and
    ai_quant_decision_id IS NULL, find the ai_quant_decisions row
    with the SAME (variant_id, asset) whose decision_utc is within
    ±2 minutes of actual_entry_time. If exactly one decision matches,
    fill the column. If zero or multiple match (e.g. defer + re-fire
    on the same minute), skip and log.

Idempotent: re-running the script never touches rows that already
have the column set. Dry-run by default; pass --apply to commit.

Usage:
  python studies/simulation/backfill_ai_quant_decision_id.py        # dry-run
  python studies/simulation/backfill_ai_quant_decision_id.py --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _parse_entry_time(s: str | None) -> int | None:
    """Parse an actual_entry_time ISO string to a unix-ts. Returns None
    on missing or malformed input — those rows are skipped."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def backfill(dash_db: str, apply: bool = False,
              tolerance_seconds: int = 120) -> dict:
    """Run the backfill on ``dash_db``. Returns counts."""
    con = sqlite3.connect(dash_db)
    con.row_factory = sqlite3.Row
    try:
        # All AI_QUANT trade rows missing the link.
        candidates = con.execute(
            "SELECT id, strategy_variant, asset, actual_entry_time "
            "FROM trades "
            "WHERE strategy='AI_QUANT' AND ai_quant_decision_id IS NULL "
            "ORDER BY actual_entry_time"
        ).fetchall()

        updates: list[tuple[int, str]] = []
        skipped_no_time = 0
        skipped_no_match = 0
        skipped_ambiguous = 0

        for r in candidates:
            entry_ts = _parse_entry_time(r["actual_entry_time"])
            if entry_ts is None:
                skipped_no_time += 1
                continue
            lo, hi = entry_ts - tolerance_seconds, entry_ts + tolerance_seconds
            matches = con.execute(
                "SELECT id FROM ai_quant_decisions "
                "WHERE variant_id=? AND asset=? "
                "  AND decision_utc BETWEEN ? AND ?",
                (r["strategy_variant"], r["asset"], lo, hi),
            ).fetchall()
            if not matches:
                skipped_no_match += 1
                continue
            if len(matches) > 1:
                skipped_ambiguous += 1
                continue
            updates.append((int(matches[0]["id"]), r["id"]))

        result = {
            "scanned": len(candidates),
            "matched": len(updates),
            "skipped_no_time": skipped_no_time,
            "skipped_no_match": skipped_no_match,
            "skipped_ambiguous": skipped_ambiguous,
            "applied": False,
        }

        if apply and updates:
            con.executemany(
                "UPDATE trades SET ai_quant_decision_id=? WHERE id=?",
                updates,
            )
            con.commit()
            result["applied"] = True

        return result
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dash-db", default=None,
                    help="Path to dashboard.db / prod.db. Defaults to "
                         "strategies.support.db.DASH_DB.")
    ap.add_argument("--apply", action="store_true",
                    help="Commit the UPDATEs. Default is dry-run.")
    ap.add_argument("--tolerance-seconds", type=int, default=120,
                    help="Window around actual_entry_time when matching "
                         "to decision_utc. Default ±120s.")
    args = ap.parse_args(argv)

    if args.dash_db is None:
        # Import here so the file remains runnable from `python <script>`.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        from strategies.support import db as _db
        args.dash_db = str(_db.DASH_DB)

    print(f"Backfilling AI_QUANT decision_id on {args.dash_db} "
          f"(tolerance ±{args.tolerance_seconds}s, "
          f"{'APPLY' if args.apply else 'DRY-RUN'})")
    result = backfill(args.dash_db, apply=args.apply,
                       tolerance_seconds=args.tolerance_seconds)
    print(f"  Scanned:           {result['scanned']:>5}")
    print(f"  Matched:           {result['matched']:>5}"
          f"  ({'applied' if result['applied'] else 'would update'})")
    print(f"  Skipped (no time): {result['skipped_no_time']:>5}")
    print(f"  Skipped (no match):{result['skipped_no_match']:>5}")
    print(f"  Skipped (ambiguous):{result['skipped_ambiguous']:>4}")
    if not args.apply:
        print("\nNo changes committed. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

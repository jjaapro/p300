"""Regenerate the AI_QUANT markdown archive from ``ai_quant_decisions``.

Use cases:
- Backfill the archive when the table already has rows from before the
  archive feature shipped.
- Re-render every file after changing the markdown template in
  ``strategies.sleeves.ai_quant.archive._render_markdown``.
- Rebuild after a clean checkout where ``data/ai_quant_archive/`` was
  not committed.

Usage:
    py strategies/sleeves/ai_quant/archive_rebuild.py             # all rows
    py strategies/sleeves/ai_quant/archive_rebuild.py --variant p300_aggressive_v2_v1_0
    py strategies/sleeves/ai_quant/archive_rebuild.py --since 2026-05-01
    py strategies/sleeves/ai_quant/archive_rebuild.py --dry-run

Existing files for the same row id are overwritten — the row id is in
the filename, so there's no risk of writing to a different decision's
file. The script is read-only against the DB.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

# Allow ``py strategies/sleeves/ai_quant/archive_rebuild.py`` from the repo root.
_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from services import db  # noqa: E402
from strategies.sleeves.ai_quant import archive  # noqa: E402


def _build_query(variant: str | None, since: str | None) -> tuple[str, list]:
    clauses, params = [], []
    if variant:
        clauses.append("variant_id = ?")
        params.append(variant)
    if since:
        clauses.append("decision_date >= ?")
        params.append(since)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return (
        "SELECT * FROM ai_quant_decisions"
        + where
        + " ORDER BY decision_utc ASC",
        params,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--variant", default=None,
                    help="restrict to one variant_id")
    p.add_argument("--since", default=None,
                    help="restrict to rows whose decision_date >= YYYY-MM-DD")
    p.add_argument("--dry-run", action="store_true",
                    help="list rows that would be rendered, don't write files")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("ai_quant_archive_rebuild")

    if not Path(db.DASH_DB).exists():
        log.error("dashboard.db not found at %s", db.DASH_DB)
        return 2

    sql, params = _build_query(args.variant, args.since)
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()

    if not rows:
        log.info("no rows match (variant=%s, since=%s)", args.variant, args.since)
        return 0

    log.info("rendering %d rows to %s", len(rows), archive._archive_dir())
    written = 0
    skipped = 0
    for r in rows:
        d = dict(r)
        if args.dry_run:
            log.info("[dry-run] would write row id=%s date=%s decided=%s",
                     d["id"], d["decision_date"], d["decided"])
            continue
        path = archive.write_archive_md(row_id=d["id"], row=d)
        if path is None:
            skipped += 1
            log.warning("skip row id=%s (write failed; see prior log)", d["id"])
        else:
            written += 1
            if args.verbose:
                log.debug("wrote %s", path)

    if not args.dry_run:
        log.info("done — wrote %d, skipped %d", written, skipped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

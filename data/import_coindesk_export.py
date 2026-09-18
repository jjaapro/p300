"""Import a saved CoinDesk liquidation-hours export into cd_liquidations.

Why this exists: the live fetcher walks backward from *now*, so once a gap is
older than the API's rolling window the feed can never close it — and since
CoinDesk moved this endpoint behind an API key the feed cannot reach it at all
(``_http_get`` sends no key; probed 2026-09-18, every call returns 401, at the
recent tail and deep in history alike). A page pulled by hand from
``/futures/v1/historical/liquidation/hours`` with an explicit ``to_ts`` still
can, and this replays that saved page into the table on the terms the feed
used to.

New hours are added with INSERT OR IGNORE. Hours already stored are compared
field by field, and a disagreement aborts the run because INSERT OR IGNORE
would otherwise hide it; ``--replace`` overwrites those rows instead, for the
case where the export is authoritative and the stored rows are known-bad.

The still-forming bar is always dropped, as the live fetcher drops it: an
export pulled with ``to_ts=now`` ends mid-hour, and a partial row would be
permanent, since INSERT OR IGNORE skips an hour once any row for it exists.

Note on the 2026-04-23 18:00 → 2026-06-01 15:00 all-zero stretch: those 934
rows are CoinDesk's own data, not a local defect. Re-exporting that window
returns the same zeros, with CLOSE_LONG_PRICE frozen at the last pre-outage
value throughout. They are not repairable from this source, so ``--replace``
is not the answer for them.

It deliberately reuses ``coindesk._map_liq_row`` rather than re-deriving the
column mapping, so an imported row is byte-identical to one the feed would
have written for the same hour.

``cd_liquidations`` carries no market or instrument column — the whole table is
implicitly binance BTC-USDT-VANILLA-PERPETUAL. An export of any other
instrument would therefore corrupt it silently, so the market, instrument and
unit are checked against the fetcher's own constants before anything is
written.

    python data/import_coindesk_export.py data/archive/1788192000.json --dry-run
    python data/import_coindesk_export.py data/archive/1788192000.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.sources import coindesk  # noqa: E402
from strategies.support import db  # noqa: E402

TABLE = "cd_liquidations"
BAR_SECONDS = 3600
# Generous, because the feed and seven bots write to prod.db continuously.
BUSY_TIMEOUT_SECONDS = 30.0


def _utc(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def load_rows(path: Path) -> list[dict]:
    """Read the export and refuse anything that is not a clean page of hourly
    liquidation bars for the instrument this table holds."""
    payload = json.loads(path.read_text())
    if payload.get("Err"):
        raise ValueError(f"export carries an API error: {payload['Err']}")
    rows = payload.get("Data") or []
    if not rows:
        raise ValueError("export has no Data rows")

    for field, expected in (
        ("MARKET", coindesk.FUTURES_MARKET),
        ("MAPPED_INSTRUMENT", coindesk.FUTURES_INSTRUMENT),
        ("UNIT", "HOUR"),
    ):
        seen = {r.get(field) for r in rows}
        if seen != {expected}:
            raise ValueError(
                f"{TABLE} holds {coindesk.FUTURES_MARKET} "
                f"{coindesk.FUTURES_INSTRUMENT} hourly bars only, but the export's "
                f"{field} is {sorted(seen)!r} (expected {expected!r})"
            )

    stamps = [int(r["TIMESTAMP"]) for r in rows]
    unaligned = [t for t in stamps if t % BAR_SECONDS]
    if unaligned:
        raise ValueError(f"{len(unaligned)} timestamps are not on the hour, "
                         f"first {_utc(unaligned[0])}")
    if len(set(stamps)) != len(stamps):
        raise ValueError(f"{len(stamps) - len(set(stamps))} duplicate timestamps")
    return rows


def compare_overlap(con: sqlite3.Connection, mapped: dict[int, tuple],
                    ) -> tuple[list[int], list[str]]:
    """Check the export against rows already stored for the same hours.

    Returns the disagreeing timestamps and one readable line per differing
    field. Agreement across the overlap is the evidence that the export is the
    same series the table already holds; a disagreement means it is not, and
    INSERT OR IGNORE would hide that.
    """
    columns = [c[1] for c in con.execute(f"PRAGMA table_info({TABLE})")]
    stored = {r[0]: r for r in con.execute(f"SELECT * FROM {TABLE}")}
    differing: list[int] = []
    problems: list[str] = []
    for ts in sorted(set(mapped) & set(stored)):
        lines: list[str] = []
        for name, new, old in zip(columns, mapped[ts], stored[ts]):
            if new is None or old is None:
                if new is not old:
                    lines.append(f"{_utc(ts)} {name}: export {new!r}, stored {old!r}")
            elif abs(float(new) - float(old)) > 1e-9 * max(1.0, abs(float(old))):
                lines.append(f"{_utc(ts)} {name}: export {new}, stored {old}")
        if lines:
            differing.append(ts)
            problems.extend(lines)
    return differing, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path, help="Saved CoinDesk JSON page.")
    ap.add_argument("--db", type=Path, default=None,
                    help=f"Target database (default {db.PROD_DB}).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be inserted, write nothing.")
    ap.add_argument("--replace", action="store_true",
                    help="Overwrite stored rows that disagree with the export. Use "
                         "only when the export is the authoritative series and the "
                         "stored rows are known-bad; a disagreement otherwise means "
                         "the two are not the same series and wants investigating.")
    args = ap.parse_args(argv)

    rows = load_rows(args.path)
    mapped = {r[0]: r for r in (coindesk._map_liq_row(x) for x in rows)}

    # Drop the still-forming bar, exactly as _paginate_backward does. An export
    # pulled with to_ts=now ends mid-hour, and storing that partial value would
    # be permanent: INSERT OR IGNORE skips the hour once a row exists, so the
    # completed bar could never replace it.
    forming = (int(time.time()) // BAR_SECONDS) * BAR_SECONDS
    partial = sorted(t for t in mapped if t >= forming)
    for t in partial:
        del mapped[t]
    if not mapped:
        print("Nothing to import — the export holds only the still-forming bar.")
        return 0

    stamps = sorted(mapped)
    missing = [t for t in range(stamps[0], stamps[-1] + 1, BAR_SECONDS)
               if t not in mapped]
    print(f"export      {args.path}")
    print(f"  {len(mapped)} hourly bars, {_utc(stamps[0])} -> {_utc(stamps[-1])} UTC")
    print(f"  internal gaps: {len(missing)}")
    if partial:
        print(f"  skipped {len(partial)} still-forming bar(s): "
              f"{', '.join(_utc(t) for t in partial)}")

    target = args.db or db.PROD_DB
    con = sqlite3.connect(str(target), timeout=BUSY_TIMEOUT_SECONDS)
    try:
        coindesk._ensure_schema(con)
        before = con.execute(
            f"SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM {TABLE}"
        ).fetchone()
        print(f"table       {TABLE} in {target}")
        if before[0]:
            print(f"  {before[0]} rows, {_utc(before[1])} -> {_utc(before[2])} UTC")
        else:
            print("  empty")

        held = {r[0] for r in con.execute(f"SELECT timestamp FROM {TABLE}")}
        differing, problems = compare_overlap(con, mapped)
        print(f"overlap     {len(set(mapped) & held)} hours already stored, "
              f"{len(differing)} disagree ({len(problems)} fields)")
        for line in problems[:10]:
            print(f"  {line}")
        if len(problems) > 10:
            print(f"  ... and {len(problems) - 10} more")
        if differing and not args.replace:
            print("\nABORTED: the export disagrees with stored rows. Re-check the "
                  "instrument and the source; if the export is authoritative and the "
                  "stored rows are known-bad, re-run with --replace.")
            return 1

        new = sorted(set(mapped) - held)
        if new:
            print(f"new         {len(new)} rows, {_utc(new[0])} -> {_utc(new[-1])} UTC")
        if differing:
            print(f"replacing   {len(differing)} stored rows, "
                  f"{_utc(differing[0])} -> {_utc(differing[-1])} UTC")
        if not new and not differing:
            print("\nNothing to do — every hour in the export is stored and agrees.")
            return 0

        if args.dry_run:
            print("\nDry run — nothing written.")
            return 0

        placeholders = ",".join(["?"] * len(next(iter(mapped.values()))))
        cur = con.cursor()
        inserted = 0
        for ts in new:
            cur.execute(
                f"INSERT OR IGNORE INTO {TABLE} VALUES ({placeholders})", mapped[ts])
            inserted += cur.rowcount
        for ts in differing:
            cur.execute(
                f"INSERT OR REPLACE INTO {TABLE} VALUES ({placeholders})", mapped[ts])
        con.commit()

        after = con.execute(
            f"SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM {TABLE}"
        ).fetchone()
        print(f"\ninserted    {inserted} rows")
        print(f"replaced    {len(differing)} rows")
        print(f"table now   {after[0]} rows, {_utc(after[1])} -> {_utc(after[2])} UTC")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

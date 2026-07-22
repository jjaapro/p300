"""Time-series gap report for prod.db.

Walks every known time-series table and reports missing buckets at the
table's natural cadence. Use after a migration or whenever you suspect
a fetcher has been silently dropping rows.

    python -m data.check_gaps           # full report
    python -m data.check_gaps -v        # show every gap, not just first 5

Cadences are listed explicitly per table. Where a cadence is None the
script infers it from the modal diff (only meaningful for tables whose
cadence is uniform throughout — funding rate is NOT one of those).

Pure read-only. Safe to run while bot.py is writing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass

from pathlib import Path

# Allow `python data/check_gaps.py` without PYTHONPATH (same bootstrap as
# data/sources/binance.py — script-dir is on sys.path, not repo root).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.support import db  # noqa: E402


@dataclass(frozen=True)
class Spec:
    table: str
    time_col: str
    asset_col: str | None
    cadence_seconds: int | None     # None = infer from modal diff
    time_unit_to_seconds: float = 1.0   # 0.001 if column is in ms


SPECS: tuple[Spec, ...] = (
    Spec("btc_1m",              "open_time",  None,    60,    0.001),
    Spec("eth_1m",              "open_time",  None,    60,    0.001),
    Spec("cd_futures_15m",      "timestamp",  None,    900),
    Spec("cd_spot_15m",         "timestamp",  None,    900),
    Spec("cd_futures_ohlcv",    "timestamp",  None,    3600),
    Spec("cd_spot_binance",     "timestamp",  None,    3600),
    Spec("cd_open_interest",    "timestamp",  None,    3600),
    Spec("okx_perp_1h",         "timestamp",  None,    3600),
    Spec("cd_liquidations",     "timestamp",  None,    3600),
    # Funding has a 2026-04-13 source cutover (1h CoinDesk-predicted -> 8h
    # Binance settlement). Leaving cadence=None tells the script to infer,
    # which surfaces the cutover instead of pretending the table is uniform.
    Spec("cd_funding_rate",     "timestamp",  None,    None),
    Spec("cd_funding_rate_eth", "timestamp",  None,    28800),
    Spec("cd_dvol",             "timestamp",  "asset", 86400),
    Spec("ca_long_short_ratio", "timestamp",  "asset", 86400),
)


def _fmt(ts_native: int, mult: float) -> str:
    return dt.datetime.fromtimestamp(ts_native * mult, dt.UTC).strftime("%Y-%m-%d %H:%M")


def _check_one(con: sqlite3.Connection, spec: Spec, verbose: bool) -> None:
    if spec.asset_col:
        assets = [r[0] for r in con.execute(
            f'SELECT DISTINCT "{spec.asset_col}" FROM "{spec.table}" '
            f'ORDER BY "{spec.asset_col}"'
        ).fetchall()]
    else:
        assets = [None]

    for asset in assets:
        where = f'WHERE "{spec.asset_col}"=?' if asset else ""
        params = (asset,) if asset else ()
        ts = [r[0] for r in con.execute(
            f'SELECT "{spec.time_col}" FROM "{spec.table}" {where} '
            f'ORDER BY "{spec.time_col}"', params
        ).fetchall()]
        if len(ts) < 2:
            continue
        diffs = [ts[i+1] - ts[i] for i in range(len(ts) - 1)]
        modal_native = Counter(diffs).most_common(1)[0][0]
        cad_native = (spec.cadence_seconds / spec.time_unit_to_seconds
                       if spec.cadence_seconds else modal_native)

        gaps = [(ts[i], ts[i+1], int((ts[i+1] - ts[i]) / cad_native) - 1)
                 for i in range(len(ts) - 1)
                 if (ts[i+1] - ts[i]) > cad_native]
        missing = sum(g[2] for g in gaps)
        expected = int((ts[-1] - ts[0]) / cad_native) + 1
        rng = f"{_fmt(ts[0], spec.time_unit_to_seconds)} -> {_fmt(ts[-1], spec.time_unit_to_seconds)}"
        label = f"{spec.table}[{asset}]" if asset else spec.table
        cad_s = int(cad_native * spec.time_unit_to_seconds)
        inferred = " (inferred)" if spec.cadence_seconds is None else ""
        print(f"{label:<32} cad={cad_s}s{inferred:<11} rows={len(ts):>8} "
              f"expected={expected:>8} missing={expected-len(ts):>6} "
              f"gaps={len(gaps):>4}  {rng}")
        if gaps:
            shown = gaps if verbose else gaps[:5]
            for a, b, n in shown:
                a_s = _fmt(a, spec.time_unit_to_seconds)
                b_s = _fmt(b, spec.time_unit_to_seconds)
                print(f"    {a_s}  ->  {b_s}   missing={n}")
            if not verbose and len(gaps) > 5:
                print(f"    ... ({len(gaps) - 5} more, use -v to see all)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-v", "--verbose", action="store_true",
                     help="Show every gap (default: first 5 per table).")
    args = ap.parse_args(argv)
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        for spec in SPECS:
            try:
                _check_one(con, spec, args.verbose)
            except sqlite3.OperationalError as e:
                print(f"{spec.table}: skipped ({e})", file=sys.stderr)
            print()
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

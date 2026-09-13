"""§9 step 1 — copy the study's inputs out of prod.db into a frozen snapshot, ONCE.

Addendum A6: this step runs exactly once; results/snapshot.json is committed in the run
commit and the same file serves the development runs, the study run, any rerun and the §6
re-cut. The snapshot lives outside the repository (okxlib.DEFAULT_SNAPSHOT).

Reads prod.db through a `?mode=ro` URI inside one read transaction, so every table comes
from the same WAL snapshot. Copies:
  - the five input tables (§0), rows with timestamp < 2026-09-12 00:00 UTC, with their DDL;
  - `p3_ledger`: the closed bot_chento_v3_v1 / bot_chento_v3_eth trades opened before
    2026-09-12 (§4.4 P3), minimal columns — no pnl;
  - `p0_bars`: the three okx_misaligned near-misses of §0.4 P0b, selected by the README's
    bars, from the bots' diag.jsonl files (their directions are not in the README).

Refuses (writes nothing) if the snapshot or results/snapshot.json already exists.

  python studies/notebooks/okx_gate_revalidation/snapshot.py
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import sqlite3  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

import okxlib as L  # noqa: E402

ORDER = {"ca_long_short_ratio": "asset, timestamp", "p3_ledger": "id", "p0_bars": "asset, ts"}
STEP = {"cd_futures_15m": 900, "cd_futures_eth_15m": 900, "okx_perp_1h": 3600,
        "okx_perp_eth_1h": 3600}
GAP_FROM = L.epoch(datetime(2021, 1, 1, tzinfo=timezone.utc))


def near_misses(diag_paths: list[Path]) -> list[tuple]:
    wanted = {(b["asset"], b["t"]): b for b in L.P0_BARS if b["ledger"] is None}
    found: dict[tuple, list] = {k: [] for k in wanted}
    for asset, path in diag_paths:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                if "okx_misaligned" not in line:
                    continue
                for nm in json.loads(line).get("near_misses", []):
                    key = (asset, nm.get("ts"))
                    if nm.get("reason") == "okx_misaligned" and key in found:
                        found[key].append(nm)
    rows = []
    for key, recs in found.items():
        uniq = {(r["direction"], r["okx_delta_z"]) for r in recs}
        L.require(len(uniq) == 1, "P0b near-miss record missing or ambiguous in diag.jsonl")
        direction, z = uniq.pop()
        rows.append((key[0], key[1], direction, float(z), "diag.jsonl"))
    return sorted(rows)


def git_head() -> dict:
    def run(*a):
        return subprocess.run(["git", *a], cwd=L.ROOT, capture_output=True, text=True).stdout.strip()
    return {"head": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


def table_stats(con: sqlite3.Connection, table: str) -> dict:
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    order = ORDER.get(table, "timestamp")
    h = hashlib.sha256()
    n = 0
    for row in con.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {order}"):
        h.update((repr(tuple(row)) + "\n").encode("utf-8"))
        n += 1
    out = {"rows": n, "columns": cols, "logical_sha256": h.hexdigest(),
           "ddl": con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                              (table,)).fetchone()[0],
           "indexes": [r[0] for r in con.execute(
               "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
               (table,))]}
    if "timestamp" in cols:
        out["min_ts"], out["max_ts"] = con.execute(
            f"SELECT MIN(timestamp), MAX(timestamp) FROM {table}").fetchone()
    if table in STEP:
        step = STEP[table]
        ts = [r[0] for r in con.execute(f"SELECT timestamp FROM {table} ORDER BY timestamp")]
        out["duplicate_ts"] = len(ts) - len(set(ts))
        out["off_grid_ts"] = sum(1 for t in ts if t % step)
        tail = [t for t in ts if t >= GAP_FROM]
        out["missing_bars_since_2021_01_01"] = sum((b - a) // step - 1 for a, b in zip(tail, tail[1:])
                                                   if b - a > step)
    if table == "ca_long_short_ratio":
        out["rows_by_asset"] = dict(con.execute(
            "SELECT asset, COUNT(*) FROM ca_long_short_ratio GROUP BY asset").fetchall())
        out["duplicate_asset_ts"] = con.execute(
            "SELECT COUNT(*) FROM (SELECT asset, timestamp FROM ca_long_short_ratio "
            "GROUP BY asset, timestamp HAVING COUNT(*) > 1)").fetchone()[0]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", type=Path, default=L.PROD_DB)
    ap.add_argument("--dest", type=Path, default=L.DEFAULT_SNAPSHOT)
    ap.add_argument("--results-dir", type=Path, default=L.RESULTS)
    ap.add_argument("--diag-btc", type=Path, default=L.ROOT / "bots" / "chento_v3" / "logs" / "diag.jsonl")
    ap.add_argument("--diag-eth", type=Path, default=L.ROOT / "bots" / "chento_v3_eth" / "logs" / "diag.jsonl")
    args = ap.parse_args(argv)
    dest = args.dest.resolve()
    partial = dest.with_name(dest.name + ".partial")
    meta_path = args.results_dir / "snapshot.json"
    try:
        L.require(not meta_path.exists(), "A6: results/snapshot.json exists — the snapshot is taken once")
        L.require(not dest.exists() and not partial.exists(), "A6: the snapshot file already exists")
        L.require(args.source.exists(), "source database missing")
    except L.Refusal as e:
        print(f"REFUSED: {e}")
        return 2

    L.install_connect_guard(allowed=[partial], allowed_ro=[args.source])
    p0_rows = near_misses([("BTC", args.diag_btc), ("ETH", args.diag_eth)])
    dest.parent.mkdir(parents=True, exist_ok=True)
    captured = datetime.now(timezone.utc).isoformat()
    src = sqlite3.connect(f"file:{args.source.resolve().as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(partial))
    try:
        dst.execute("PRAGMA journal_mode=DELETE")
        src.execute("BEGIN")        # one read snapshot for every SELECT below
        for table in L.INPUT_TABLES:
            ddl = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                              (table,)).fetchone()[0]
            dst.execute(ddl)
            for (idx_sql,) in src.execute("SELECT sql FROM sqlite_master WHERE type='index' "
                                          "AND tbl_name=? AND sql IS NOT NULL", (table,)).fetchall():
                dst.execute(idx_sql)
            cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
            where = "timestamp < ?"
            if table == "ca_long_short_ratio":
                where += " AND asset IN ('BTC', 'ETH')"
            rows = src.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE {where} ORDER BY rowid",
                               (L.SNAPSHOT_CUTOFF,)).fetchall()
            dst.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES "
                            f"({', '.join('?' * len(cols))})", rows)
        dst.execute("CREATE TABLE p3_ledger (id TEXT PRIMARY KEY, strategy_variant TEXT, "
                    "direction TEXT, actual_entry_time TEXT, actual_exit_time TEXT, "
                    "exit_price REAL, notes TEXT)")
        ledger = src.execute(
            "SELECT id, strategy_variant, direction, actual_entry_time, actual_exit_time, "
            "exit_price, notes FROM trades WHERE strategy_variant IN (?, ?) AND status = 'closed' "
            "AND actual_entry_time < ? ORDER BY id", (*L.P3_VARIANTS, L.P3_OPENED_BEFORE)).fetchall()
        dst.executemany("INSERT INTO p3_ledger VALUES (?, ?, ?, ?, ?, ?, ?)", ledger)
        dst.execute("CREATE TABLE p0_bars (asset TEXT, ts TEXT, direction TEXT, okx_delta_z REAL, "
                    "source TEXT, PRIMARY KEY (asset, ts))")
        dst.executemany("INSERT INTO p0_bars VALUES (?, ?, ?, ?, ?)", p0_rows)
        src.rollback()
        dst.commit()
    finally:
        src.close()
        dst.close()

    con = sqlite3.connect(str(partial))
    try:
        tables = {t: table_stats(con, t) for t in (*L.INPUT_TABLES, "p3_ledger", "p0_bars")}
    finally:
        con.close()
    os.replace(partial, dest)
    os.chmod(dest, stat.S_IREAD)          # read-only attribute: nothing can rewrite its bytes
    meta = {"captured_utc": captured, "source": str(args.source.resolve()),
            "snapshot_path": str(dest), "row_cutoff_epoch": L.SNAPSHOT_CUTOFF,
            "row_cutoff_utc": L.iso(L.SNAPSHOT_CUTOFF), "repo": git_head(),
            "file_sha256": L.file_sha256(dest), "file_bytes": dest.stat().st_size,
            "p3_ledger_ids": [r[0] for r in ledger], "tables": tables}
    L.write_json_atomic(meta_path, meta)
    print(f"snapshot written: {dest} ({meta['file_bytes']:,} bytes)")
    for t, s in tables.items():
        print(f"  {t}: {s['rows']:,} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())

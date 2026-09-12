"""Per-bot dry run at a FIRING anchor, for the phase-C repoint gate.

    python repoint_baseline.py <label> [bot ...]

Writes results/<label>/<bot>.json with the trade rows the bot produced. The
repoint gate is: baseline and after-repoint are byte-identical, AND non-empty.

Non-empty is the part that matters. short_squeeze has never fired in
production, r4's next enabled window is 2026-10-02 and chento fires ~36x/yr,
so at an unpinned anchor all three produce no row before and no row after and
the diff passes on nothing. Every anchor below is one where that bot's golden
proves it fires.

Each run gets a FRESH sqlite .backup copy with the ledger emptied: the copy
carries the three real open positions, and the per-bar idempotency keys
shipped 2026-09-12 mean a second run against the same copy writes nothing.
"""
import json
import pathlib
import shutil
import sqlite3
import subprocess
import sys

REPO = pathlib.Path(r"C:\Source\Repos\p300")
SC = pathlib.Path(__file__).resolve().parent
PY = REPO / "venv" / "Scripts" / "python.exe"
PROD = REPO / "data" / "databases" / "prod.db"

# (bot, --sim-now anchor). Each is a bar that bot's golden proves it fires on.
ANCHORS = {
    "squeeze_bull":  "2026-09-11T18:00:31+00:00",
    "carry":         "2026-07-22T13:54:00+00:00",
    "chento_v3":     "2026-08-21T19:30:05+00:00",
    "chento_v3_eth": "2026-08-02T00:00:05+00:00",  # fires SHORT, unlike BTC
    "short_squeeze": "2025-10-14T10:00:05+00:00",
    "adx":           "2026-08-22T00:00:05+00:00",
    # R4_ETH's window opens Tue 20:00 UTC; 2026-09-08 is a Tuesday. BTC
    # windows are ENABLED=False, so this is the only live-fire shape.
    "r4":            "2026-09-08T20:01:00+00:00",
}

COLS = ("strategy", "strategy_variant", "asset", "direction", "status",
        "entry_price", "exit_price", "size_usdt", "qty", "leverage",
        "allocation_pct", "exit_time", "pnl_usdt", "notes", "unique_key")


def fresh_copy(dest: pathlib.Path):
    if dest.exists():
        dest.unlink()
    src = sqlite3.connect(PROD.as_uri() + "?mode=ro", uri=True)
    dst = sqlite3.connect(str(dest))
    src.backup(dst)
    dst.close()
    src.close()
    # Empty the ledger: the copy carries SJ-4242/4247/4250, whose single-open
    # guards would otherwise refuse the entry we are trying to measure.
    con = sqlite3.connect(str(dest))
    con.execute("DELETE FROM trades")
    con.execute("DELETE FROM trade_adjustments")
    con.commit()
    con.close()


def rows(db: pathlib.Path):
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        out = [dict(r) for r in con.execute(
            f"SELECT {', '.join(COLS)} FROM trades ORDER BY strategy, asset")]
    finally:
        con.close()
    for r in out:
        # The notes blob is the reason dict decide() built; keep it, drop the
        # free-text tail which carries timestamps.
        head = (r["notes"] or "").split("\n")[0]
        try:
            r["notes"] = json.loads(head)
        except ValueError:
            r["notes"] = None
        # unique_key embeds the signal stamp (behaviour) after the variant id.
        uk = r["unique_key"]
        r["unique_key"] = uk.split("|", 1)[1] if uk and "|" in uk else uk
    return out


def main(argv):
    label = argv[0]
    bots = argv[1:] or list(ANCHORS)
    out_dir = SC / "repoint" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    work = SC / "repoint" / "_work"
    work.mkdir(parents=True, exist_ok=True)

    rc = 0
    for bot in bots:
        anchor = ANCHORS[bot]
        db = work / f"{bot}.db"
        fresh_copy(db)
        p = subprocess.run(
            [str(PY), f"bots/{bot}/runner.py", "--once", "--db", str(db),
             "--sim-now", anchor],
            cwd=str(REPO), capture_output=True, text=True, timeout=1800)
        got = rows(db)
        (out_dir / f"{bot}.json").write_text(
            json.dumps(got, indent=1, sort_keys=True, default=str),
            encoding="utf-8")
        mark = "OK " if (p.returncode == 0 and got) else "!! "
        if p.returncode != 0 or not got:
            rc = 1
        print(f"  [{mark}] {bot:<16} exit={p.returncode}  rows={len(got)}"
              + ("" if got else "   <-- EMPTY: anchor does not fire, BLOCKED"))
        if p.returncode != 0:
            print("      " + (p.stderr.strip().splitlines() or ["?"])[-1][:160])
        for r in got:
            print(f"      {r['strategy']:<20} {r['strategy_variant']:<30} "
                  f"{r['direction']} @ {r['entry_price']} "
                  f"size={r['size_usdt']} k={r['leverage']}")
        db.unlink(missing_ok=True)
        for extra in work.glob(f"{bot}.db-*"):
            extra.unlink(missing_ok=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

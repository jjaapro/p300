"""Carve the minimal market data each sleeve's golden needs out of prod.db.

    python tests/fixtures/build_sleeve_fixtures.py            # build + verify
    python tests/fixtures/build_sleeve_fixtures.py --rehash   # re-pin (rare)

The `.db` files are gitignored; `MANIFEST.json` commits their sha256. A
MISMATCH IS A FINDING, NOT A CHORE: it means the historical rows moved
underneath us, which is a data-integrity question, never a re-baseline. One
such case is already known — SJ-4243's notes record `_entry_price: 75256.8`
for the 2026-08-21T06:00 bar while prod.db now holds `close = 75255.8` for
that exact timestamp, because the feed upserted the bar after the fire.

Windows are derived from each loader's OWN constants with margin, because a
fixture one day too short returns plausible, wrong numbers in silence. Measured
instance: a 260-day ADX carve reproduced `adx` and `close` exactly but returned
`trend_ema` 68887.18 against production's 69332.66 — a 445-point error in
EMA(150) — because `_load_btc_daily_candles` asks for
`DAILY_LIMIT + WARMUP_BARS + 10` = 361 days and the EMA seed differs when the
series is truncated. Nothing warned.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

PROD = REPO / "data" / "databases" / "prod.db"
MANIFEST = HERE / "MANIFEST.json"
DAY = 86400


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp())


# anchor = the frozen clock the golden runs at; each table carries the window
# the sleeve's own loader asks for, plus margin.
SPECS = {
    "carry": {
        # _load_recent_daily_funding(days=EXIT_CUM_DAYS + 7) then (days + 2).
        "anchor": "2026-07-22T13:54:00",
        "tables": {
            "cd_spot_binance": 60, "cd_futures_ohlcv": 60,
            "cd_funding_rate": 60,
        },
    },
    "squeeze_bull": {
        # _load_hourly(lookback_days=45); the regime gate needs 30d of daily
        # closes inside that window.
        "anchor": "2026-09-11T18:00:31",
        "tables": {"cd_futures_ohlcv": 50, "cd_open_interest": 50},
    },
    "adx": {
        # _load_btc_daily_candles asks DAILY_LIMIT + WARMUP_BARS + 10 = 361d.
        # 372 gives margin; see the docstring for what too-short looks like.
        "anchor": "2026-08-22T00:00:05",
        "tables": {"cd_spot_binance": 372, "cd_funding_rate": 60},
    },
    "chento": {
        "anchor": "2026-08-21T06:15:00",
        # okx 31 -> 45 days on 2026-09-13 (BACKLOG 7b), a deliberate SPEC
        # change, not a re-baseline of moved rows. The OKX-gate golden's
        # anchor moved to 2026-07-16 06:30 because the old one only blocked
        # on future data; with a 31-day carve the OKX table starts 07-21 and
        # that anchor's okx_delta_z would be NaN — a fixture artifact, not a
        # gate. 45 days reaches back to 07-07. The loader reads 30 days.
        "tables": {"cd_futures_15m": 91, "cd_futures_eth_15m": 91,
                   "ca_long_short_ratio": 95, "okx_perp_1h": 45,
                   "okx_perp_eth_1h": 45},
    },
    "short_squeeze": {
        # No production fire has ever happened; this anchor is a DISCOVERED
        # historical trigger, not a ledger row. Its 90-day percentile
        # distribution is why the window is long.
        "anchor": "2025-10-14T10:00:00",
        "tables": {"cd_futures_15m": 95, "cd_spot_15m": 95,
                   "cd_futures_ohlcv": 5, "cd_open_interest": 5,
                   "cd_funding_rate": 5},
    },
}


def _carve(name: str, spec: dict) -> Path:
    dest = HERE / f"{name}.db"
    if dest.exists():
        dest.unlink()
    anchor = _ts(spec["anchor"])
    src = sqlite3.connect(PROD.as_uri() + "?mode=ro", uri=True)
    dst = sqlite3.connect(str(dest))
    try:
        for table, days in spec["tables"].items():
            ddl = src.execute(
                "SELECT sql FROM sqlite_master WHERE name = ? AND type='table'",
                (table,)).fetchone()
            if ddl is None or not ddl[0]:
                print(f"    ! {table} absent from prod.db — skipped")
                continue
            dst.execute(ddl[0])
            lo = anchor - days * DAY
            # +1 day of margin above the anchor: some loaders bound on the
            # forming bar rather than the closed one.
            rows = src.execute(
                f"SELECT * FROM {table} WHERE timestamp >= ? AND timestamp <= ? "
                f"ORDER BY timestamp", (lo, anchor + DAY)).fetchall()
            if rows:
                qs = ",".join("?" * len(rows[0]))
                dst.executemany(f"INSERT INTO {table} VALUES ({qs})", rows)
            print(f"    {table:<24} {len(rows):>7,} rows  ({days}d)")
        dst.commit()
        dst.execute("VACUUM")
        dst.commit()
    finally:
        dst.close()
        src.close()
    return dest


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rehash", action="store_true",
                    help="Re-pin MANIFEST.json. A mismatch is a data-integrity "
                         "finding — only re-pin when you know WHY it moved.")
    ap.add_argument("--only", action="append", default=[])
    args = ap.parse_args(argv)

    if not PROD.exists():
        print(f"prod.db not found at {PROD} — cannot build fixtures")
        return 2

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    names = args.only or list(SPECS)
    bad = []
    for name in names:
        print(f"  {name}:")
        dest = _carve(name, SPECS[name])
        digest = sha256(dest)
        size_mb = dest.stat().st_size / 1e6
        prev = (manifest.get(name) or {}).get("sha256")
        if prev is None or args.rehash:
            manifest[name] = {"sha256": digest, "size_bytes": dest.stat().st_size,
                              "anchor": SPECS[name]["anchor"],
                              "tables": SPECS[name]["tables"]}
            print(f"    pinned {digest[:16]}...  {size_mb:.2f} MB")
        elif prev != digest:
            bad.append(name)
            print(f"    !! HASH MISMATCH  pinned {prev[:16]}... got {digest[:16]}...")
            print(f"       The historical rows moved. Investigate BEFORE "
                  f"re-pinning — this is a data-integrity finding.")
        else:
            print(f"    ok {digest[:16]}...  {size_mb:.2f} MB")

    MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Combine Core J+ (regime-gated) + Tactical replays into a full P-300 NAV.

P-300 weights (per register_p300.py, updated 2026-04-30 when FOMC was added):
  50%  Core J+ (regime-gated, no GOLD — see jplus/__init__.py)
  50%  Tactical stack (6 sleeves: S-003/S-078/S-096 V4/PDO-L-RF/CPR/FOMC)
   0%  No cash reserve (FOMC absorbed the prior 5% slot)

Daily combination: P-300 return = 0.50 * core + 0.50 * tactical.

Inputs:
  - Tactical daily returns: already persisted by backtest_runner.py in
    variant_daily_returns with source='replay'. Passed in via --tac-variant.
  - Core daily returns: computed on-the-fly by jplus.simulate() over the
    same window. Written to a new variant id as source='replay'.

Outputs (all in dashboard.db):
  - <core_variant>:  Core-alone daily NAV (50% weight scaled up to 100%
    basis so it reads like a standalone portfolio). Tag "core".
  - <combined_variant>: P-300 full daily NAV. Tag "C" by default.

Usage:
  python combine_replay.py --tac-variant p300_aggressive_v2_v1_0__replay_A2 \
                            --tag C --start 2021-07-01 --end 2026-04-15
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from services import variant_registry  # noqa: E402
from jplus import simulate as core_sim  # noqa: E402
from services import db

LIVE_VARIANT_ID = "p300_aggressive_v2_v1_0"

W_CORE = 0.50
W_TACTICAL = 0.50
W_CASH = 0.00


def load_tactical_returns(variant_id: str) -> dict[str, float]:
    """Read {date_iso: return_1x_pct} from variant_daily_returns for the
    given tactical replay variant."""
    con = sqlite3.connect(str(db.DASH_DB))
    rows = con.execute(
        "SELECT date, return_1x_pct FROM variant_daily_returns "
        "WHERE variant_id = ? AND source = 'replay' ORDER BY date",
        (variant_id,),
    ).fetchall()
    con.close()
    return {d: float(r or 0) for d, r in rows}


def ensure_variant(variant_id: str, short_name: str, notes: str,
                   capital: float = 10_000.0) -> None:
    """Idempotent: create the variant row if missing."""
    con = sqlite3.connect(str(db.DASH_DB))
    cur = con.cursor()
    existing = cur.execute(
        "SELECT id FROM variants WHERE id = ?", (variant_id,),
    ).fetchone()
    if existing:
        cur.execute("DELETE FROM variant_daily_returns WHERE variant_id = ?",
                    (variant_id,))
        cur.execute("DELETE FROM variant_events WHERE variant_id = ?",
                    (variant_id,))
        cur.execute("DELETE FROM variants WHERE id = ?", (variant_id,))
        con.commit()
    now_iso = datetime.now(timezone.utc).isoformat()
    cur.execute("""
        INSERT INTO variants (
            id, short_name, long_name, kind, parent_variant_id, version, status,
            is_primary, capital_usdt, color, spec_json, notes, superseded_by,
            reconcile_against, enabled, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        variant_id, short_name, short_name,
        "full_portfolio", None, "1.0-replay", "SHADOW",
        0, capital, "#7c2d12",
        json.dumps({"equity_source": "daily_returns",
                    "notes": "Created by combine_replay.py — not a live variant"}),
        notes, None, None,
        0,  # enabled=0 — never tick in live mode
        now_iso,
    ))
    con.commit()
    con.close()


def write_daily_returns(variant_id: str, rows: list[tuple[str, float]]) -> None:
    """Persist daily returns to variant_daily_returns (source='replay')."""
    now_iso = datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(str(db.DASH_DB))
    rows_full = [(variant_id, d, r, "replay", None, now_iso)
                 for d, r in rows]
    con.executemany("""
        INSERT INTO variant_daily_returns
        (variant_id, date, return_1x_pct, source, regime, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, rows_full)
    con.commit()
    con.close()


def run(tac_variant: str, tag_core: str, tag_combined: str,
        start: str | None, end: str | None) -> None:
    print(f"Combining tactical={tac_variant} + Core J+ regime-gated port")
    print(f"Weights: core={W_CORE}, tactical={W_TACTICAL}, cash={W_CASH}")
    core_variant_id = f"{LIVE_VARIANT_ID}__{tag_core}"
    combined_variant_id = f"{LIVE_VARIANT_ID}__{tag_combined}"

    tac = load_tactical_returns(tac_variant)
    if not tac:
        raise SystemExit(f"No tactical returns found for {tac_variant} — "
                         f"run backtest_runner.py with that tag first.")
    print(f"Tactical: {len(tac)} days from {min(tac)} to {max(tac)}")

    core_map = core_sim.simulate(start_date=start, end_date=end)
    print(f"Core J+:  {len(core_map)} days from "
          f"{min(core_map) if core_map else '-'} to "
          f"{max(core_map) if core_map else '-'}")

    # Write Core-alone daily returns as its own variant for side-by-side reporting.
    core_rows = [(d, core_map[d]["return_pct"]) for d in sorted(core_map)]
    ensure_variant(
        core_variant_id,
        "Core J+ (regime-gated, no GOLD) STANDALONE",
        "Standalone Core replay. J+ static 2.5x with rule-based R4 gate "
        "(T-1 vol-percentile). No ML, no GOLD, no look-ahead possible by "
        "construction. Daily returns are the Core's own PnL — NOT weighted "
        "into P-300; use the combined variant for that.",
    )
    write_daily_returns(core_variant_id, core_rows)
    print(f"Wrote {len(core_rows)} rows to {core_variant_id}")

    # Combined P-300 = 0.50 * core + 0.45 * tactical (cash contributes 0)
    all_dates = sorted(set(core_map.keys()) | set(tac.keys()))
    if start:
        all_dates = [d for d in all_dates if d >= start]
    if end:
        all_dates = [d for d in all_dates if d <= end]
    combined_rows: list[tuple[str, float]] = []
    for d in all_dates:
        core_pct = core_map.get(d, {"return_pct": 0.0})["return_pct"] if d in core_map else 0.0
        tac_pct = tac.get(d, 0.0)
        combined_pct = W_CORE * core_pct + W_TACTICAL * tac_pct
        combined_rows.append((d, combined_pct))

    ensure_variant(
        combined_variant_id,
        "P-300 full replay (Core+Tactical, no GOLD)",
        f"Daily returns combined as 0.50*Core + 0.45*Tactical + 0.05*0. "
        f"Core = {core_variant_id}. Tactical = {tac_variant}. This is the "
        "closest honest replay of P-300 we can produce with current data "
        "(GOLD dropped; rule-based gate instead of ML gate).",
    )
    write_daily_returns(combined_variant_id, combined_rows)
    print(f"Wrote {len(combined_rows)} rows to {combined_variant_id}")
    print(f"Done. Use `python backtest_report.py --variant {combined_variant_id}` "
          f"to view deep metrics.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tac-variant", required=True,
                    help="Tactical replay variant id (e.g. "
                         "p300_aggressive_v2_v1_0__replay_A2).")
    ap.add_argument("--tag-core", default="core",
                    help="Suffix for the Core-alone variant id (default 'core').")
    ap.add_argument("--tag-combined", default="C",
                    help="Suffix for the combined P-300 variant id (default 'C').")
    ap.add_argument("--start", default=None,
                    help="Earliest date (ISO) to include. Default: tactical start.")
    ap.add_argument("--end", default=None,
                    help="Latest date (ISO) to include. Default: tactical end.")
    args = ap.parse_args(argv)
    run(args.tac_variant, args.tag_core, args.tag_combined, args.start, args.end)
    return 0


if __name__ == "__main__":
    sys.exit(main())

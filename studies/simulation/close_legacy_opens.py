"""One-shot: close the legacy variant's stranded open paper trades.

When the legacy orchestrator bot died (~2026-06-11) its open positions
stopped being managed — no stop sweeps, no exits. Leaving them "open"
accrues fictional P&L. This closes every open paper trade on the legacy
variant at the current price with reason ``legacy_shutdown``, ending the
legacy paper series honestly (user decision 2026-07-22).

Dry-run by default:
  python studies/simulation/close_legacy_opens.py           # show targets
  python studies/simulation/close_legacy_opens.py --apply   # close them

CARRY trades close via close_carry_trade (funding-based P&L, delta-neutral);
everything else via close_perp_trade. Idempotent: already-closed rows are
simply no longer selected.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from strategies import trades  # noqa: E402
from strategies.support import db  # noqa: E402
from strategies.support.price_feed import get_current_price  # noqa: E402

log = logging.getLogger("close_legacy_opens")

LEGACY_VARIANT = "p300_aggressive_v2_v1_0"
REASON = "legacy_shutdown"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="Actually close (default: dry-run print only).")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    con = sqlite3.connect(str(db.PROD_DB))
    con.row_factory = sqlite3.Row
    try:
        opens = con.execute(
            "SELECT id, strategy, asset, direction, entry_price, size_usdt, "
            "       actual_entry_time FROM trades "
            "WHERE strategy_variant = ? AND execution_mode = 'paper' "
            "  AND status = 'open' ORDER BY actual_entry_time",
            (LEGACY_VARIANT,)).fetchall()
    finally:
        con.close()

    if not opens:
        log.info("no open legacy trades — nothing to do")
        return 0

    log.info(f"{'would close' if not args.apply else 'closing'} "
             f"{len(opens)} legacy open trade(s):")
    failures = 0
    for t in opens:
        price = get_current_price(t["asset"])
        log.info(f"  {t['id']}  {t['strategy']:<8} {t['asset']} "
                 f"{t['direction']:<5} entry {t['entry_price']:>10,.2f} "
                 f"({t['actual_entry_time'][:10]})  exit @ {price}")
        if price is None:
            log.error(f"  !! no current price for {t['asset']} — is feed.py "
                      f"running? skipping {t['id']}")
            failures += 1
            continue
        if not args.apply:
            continue
        if t["strategy"] == "CARRY":
            trades.close_carry_trade(t["id"], price, REASON)
        else:
            trades.close_perp_trade(t["id"], price, REASON,
                                    sleeve_name=t["strategy"])

    if args.apply:
        con = sqlite3.connect(str(db.PROD_DB))
        con.row_factory = sqlite3.Row
        try:
            for t in opens:
                row = con.execute(
                    "SELECT status, exit_price, pnl_usdt, pnl_pct "
                    "FROM trades WHERE id=?", (t["id"],)).fetchone()
                log.info(f"  {t['id']} -> {row['status']}  "
                         f"exit={row['exit_price']}  "
                         f"pnl=${(row['pnl_usdt'] or 0):,.2f} "
                         f"({(row['pnl_pct'] or 0):+.2f}%)")
        finally:
            con.close()
    else:
        log.info("dry-run only — rerun with --apply to close")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

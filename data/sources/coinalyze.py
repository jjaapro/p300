"""Coinalyze liquidation feed — the live writer for ca_liquidations.

Why this exists: the fleet ran from 2026-05-24 to 2026-09-18 with no
liquidation series of any kind and nothing noticed. `cd_liquidations` was
CoinDesk-fed and that endpoint moved behind a paid API key (probed
2026-09-18: 401 on every unauthenticated call, at the recent tail and deep in
history alike), while `ca_liquidations` sat in `botlib.FROZEN_TABLES` as a
one-shot, where a stale age is deliberately not a failure. Neither table could
raise an alarm. This module gives the series a live writer so it can carry a
freshness contract like every other live-read table.

Two cadences, two tables, on purpose. Their timestamps collide at every UTC
midnight and neither table carries an interval column, so one table holding
both would silently overwrite an hour with a whole day:

  ca_liquidations        1-hour bars, rolling ~89-day source window
  ca_liquidations_daily  daily bars, history back to 2021-01-01

The window asymmetry is the whole point of refreshing hourly. Measured
2026-09-18 on BTCUSDT_PERP.A: 88 days back returns rows, 90 returns none,
while the daily series still answers 1,500 days back. So an hour not collected
within ~89 days is gone for good, and a day is not.

Cadence: `refresh()` runs every feed cycle and throttles itself to one pull
per (interval, UTC hour) for hourly and one per (interval, UTC day) for daily.
Each pull resumes from the last stored bar, so an outage shorter than the
source window heals itself — the same reasoning as coinbase.refresh().

The heavy lifting stays in `fetch_coinalyze.py` (API client, rate limiting,
gap detection, unfillable recording); this module is the throttle and the
feed-facing entry point, so there is one implementation, not two.

CLI:
  python data/sources/coinalyze.py --refresh
  python data/sources/coinalyze.py --refresh --force
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.support import clock  # noqa: E402

log = logging.getLogger("coinalyze")

ASSETS: tuple[str, ...] = ("BTC", "ETH")
# interval -> how often the feed may pull it. Hourly because an uncollected
# hour expires out of the source window; daily because a day does not.
INTERVALS: dict[str, str] = {"1hour": "hour", "daily": "day"}
# The feed's first tick of a new hour lands seconds after the boundary, before
# the venue has published the bar that just closed. Shifting the throttle
# clock back by this much makes the pull for hour H happen at H:02, when the
# H-1 bar exists. (Seen live 2026-09-18 21:00:49: the pull asked for a bar 49
# seconds old, got nothing, and recorded those 49 seconds as unfillable.)
PUBLISH_LAG = timedelta(minutes=2)

_done: set[str] = set()


def _bucket(now: datetime, cadence: str) -> str:
    """Throttle key: one pull per UTC hour, or per UTC day, on a clock shifted
    back by PUBLISH_LAG so each bucket opens once its last bar is published."""
    shifted = now - PUBLISH_LAG
    if cadence == "hour":
        return f"{shifted.date().isoformat()}:{shifted.hour}"
    return shifted.date().isoformat()


def refresh(*, now: datetime | None = None, force: bool = False,
            ) -> dict[str, int]:
    """Throttled entry point for the feed loop.

    Returns {interval: rows_written}, -1 for an interval that failed; keys are
    absent when nothing was due. Never raises: one bad interval must not take
    down the feed cycle, and a missing API key is a configuration state to log
    once, not an error to retry every minute.
    """
    now = now or clock.now_utc()
    if clock.is_simulated() and not force:
        return {}

    import fetch_coinalyze  # repo root; also loads .env on import

    try:
        fetch_coinalyze._api_key()
    except fetch_coinalyze.MissingApiKey:
        if "no-key" not in _done:
            log.warning("COINALYZE_API_KEY not set — liquidation feed idle")
            _done.add("no-key")
        return {}

    out: dict[str, int] = {}
    for interval, cadence in INTERVALS.items():
        key = f"{interval}:{_bucket(now, cadence)}"
        if not force and key in _done:
            continue
        try:
            written = fetch_coinalyze.fetch_liquidations(ASSETS,
                                                         interval=interval)
            out[interval] = sum(written.values())
            _done.add(key)
        except Exception as e:  # noqa: BLE001 — one interval must not kill the other
            log.warning(f"coinalyze {interval} refresh failed: {e}")
            out[interval] = -1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true",
                    help="Run one throttled refresh cycle.")
    ap.add_argument("--force", action="store_true",
                    help="Ignore the throttle (and sim mode).")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    if not args.refresh:
        ap.print_help()
        return 0
    for k, v in refresh(force=args.force).items():
        print(f"  {k:8} {v:>6} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

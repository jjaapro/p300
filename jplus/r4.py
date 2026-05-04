"""R4 intraday windows — direct port from upstream `backtest_variant_j`.

R4 BTC (J+ window):
  Entry: 06:00 UTC
  Exit:  18:00 UTC (same day)
  Fires: Mondays and Wednesdays, weeks 1-2 of month (day ≤ 14)
  Return: (close_bar_18 - open_bar_06) / open_bar_06  minus 10bp RT cost

Note on what "close_bar_18" means: the 18:00 bar COVERS 18:00-19:00 UTC;
its OPEN is the 18:00 price. Upstream uses `by_hour[exit_key][0]` (the
open of the exit hour) — that's the price at 18:00 UTC sharp, which is
the correct exit execution price for a strategy that closes at 18:00.
We keep the same convention.

R4 ETH (J+ window, shifted -4):
  Entry: Tue 20:00 UTC
  Exit:  Wed 20:00 UTC (24h later)
  Fires: Tuesdays whose next-day (Wed) falls in weeks 1-2 of month (day ≤ 14)
  Return: (open_bar_wed_20 - open_bar_tue_20) / open_bar_tue_20 - 10bp RT
  Keyed BY WED DATE (the "trade day" the simulator attributes to).

Both return dicts are keyed by date_iso and contain the net per-trade return
(decimal, not percent). Absent keys mean no trade that day.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


COST_BP_RT = 10.0  # 5bp each side, taker estimate


def r4_btc_returns(
    by_hour: dict[tuple[str, int], tuple[float, float]],
    cost_bp: float = COST_BP_RT,
) -> dict[str, float]:
    """R4 BTC 06:00 → 18:00 UTC on Mon+Wed wk1-2."""
    out: dict[str, float] = {}
    for (d, h), (o, _c) in by_hour.items():
        if h != 6:
            continue
        try:
            dt = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if dt.weekday() not in (0, 2) or not (1 <= dt.day <= 14):
            continue
        entry = o
        exit_key = (d, 18)
        if exit_key not in by_hour:
            continue
        exit_open = by_hour[exit_key][0]
        if entry <= 0:
            continue
        gross = (exit_open - entry) / entry
        net = gross - cost_bp / 10000.0
        out[d] = net
    return out


def r4_eth_returns(
    eth_by_hour: dict[tuple[str, int], tuple[float, float]],
    cost_bp: float = COST_BP_RT,
) -> dict[str, float]:
    """R4 ETH Tue 20:00 → Wed 20:00 UTC, keyed by Wed date (trade day)."""
    out: dict[str, float] = {}
    for (d, h) in list(eth_by_hour.keys()):
        if h != 20:
            continue
        try:
            dt = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if dt.weekday() != 1:  # must be Tuesday
            continue
        wed_dt = dt + timedelta(days=1)
        if wed_dt.weekday() != 2 or not (1 <= wed_dt.day <= 14):
            continue
        entry = eth_by_hour[(d, 20)][0]
        exit_key = (wed_dt.date().isoformat(), 20)
        if exit_key not in eth_by_hour:
            continue
        exit_open = eth_by_hour[exit_key][0]
        if entry <= 0:
            continue
        gross = (exit_open - entry) / entry
        net = gross - cost_bp / 10000.0
        out[wed_dt.date().isoformat()] = net
    return out

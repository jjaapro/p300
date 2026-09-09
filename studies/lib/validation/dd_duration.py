"""Kaminski drawdown-duration analysis.

Ported from the trader repo root module ``dd_duration.py``
(``drawdown_durations``, ``summarize_drawdowns``, ``equity_curve_from_trades``);
the ``crisis_alpha_gate.load_daily`` import and the R4 loaders are dropped.

Kaminski & Greyserman (2014), "Trend Following with Managed Futures", ch. 9:
"The real risk metric is time-under-water, not peak-to-trough magnitude;
expected drawdown duration of 12-24 months is normal and should not trigger
de-allocation."

This utility computes drawdown duration statistics from a per-trade or per-day
return stream. Used as a baseline for live monitoring -- a strategy in drawdown
should be compared against its own historical DD duration distribution before
panic-stopping.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Sequence


def drawdown_durations(equity_curve: Sequence[tuple[str, float]]) -> list[dict[str, Any]]:
    """
    equity_curve: list of (date_str, cumulative_return) tuples in chronological order.
    Returns list of dicts: {start, end, duration_days, depth_pct, recovered}
    Each entry is one drawdown -- from peak to next peak (or end of data if no recovery).
    """
    if len(equity_curve) < 2:
        return []
    peak_value = equity_curve[0][1]
    peak_date = equity_curve[0][0]
    drawdowns = []
    current_dd_start = None
    current_dd_max_depth = 0.0
    for date, value in equity_curve:
        if value >= peak_value:
            # New peak -- close current drawdown if any
            if current_dd_start is not None:
                d0 = datetime.strptime(current_dd_start, "%Y-%m-%d")
                d1 = datetime.strptime(date, "%Y-%m-%d")
                drawdowns.append({
                    "start": current_dd_start,
                    "end": date,
                    "duration_days": (d1 - d0).days,
                    "depth_pct": current_dd_max_depth * 100,
                    "recovered": True,
                })
                current_dd_start = None
                current_dd_max_depth = 0.0
            peak_value = value
            peak_date = date
        else:
            # In drawdown
            if current_dd_start is None:
                current_dd_start = peak_date
            depth = (peak_value - value) / max(abs(peak_value), 1e-9)
            if depth > current_dd_max_depth:
                current_dd_max_depth = depth
    # If still in DD at end
    if current_dd_start is not None:
        d0 = datetime.strptime(current_dd_start, "%Y-%m-%d")
        d1 = datetime.strptime(equity_curve[-1][0], "%Y-%m-%d")
        drawdowns.append({
            "start": current_dd_start,
            "end": equity_curve[-1][0],
            "duration_days": (d1 - d0).days,
            "depth_pct": current_dd_max_depth * 100,
            "recovered": False,
        })
    return drawdowns


def summarize_drawdowns(drawdowns: Sequence[dict[str, Any]], label: str) -> dict[str, Any] | None:
    """Print a drawdown summary (as the trader original did) and also return it.

    Returns None (after printing) when there are no drawdowns; otherwise a dict
    with n, max/median/avg duration in days, max/avg depth in %, and the three
    longest drawdown records.
    """
    if not drawdowns:
        print(f"\n{label}: no drawdowns recorded")
        return None
    durations = [d["duration_days"] for d in drawdowns]
    depths = [d["depth_pct"] for d in drawdowns]
    n = len(drawdowns)
    print(f"\n{label}")
    print(f"  Total drawdowns: {n}")
    print(f"  Max duration:    {max(durations)} days")
    print(f"  Median duration: {sorted(durations)[n // 2]} days")
    print(f"  Avg duration:    {sum(durations) / n:.1f} days")
    print(f"  Max depth:       {max(depths):.2f}%")
    print(f"  Avg depth:       {sum(depths) / n:.2f}%")
    # Show top 3 longest drawdowns
    top = sorted(drawdowns, key=lambda d: -d["duration_days"])[:3]
    print(f"  Longest drawdowns:")
    for d in top:
        rec = "(recovered)" if d["recovered"] else "(open)"
        print(f"    {d['start']} -> {d['end']}  {d['duration_days']:>4}d  "
              f"depth {d['depth_pct']:.2f}%  {rec}")
    return {
        "label": label,
        "n": n,
        "max_duration_days": max(durations),
        "median_duration_days": sorted(durations)[n // 2],
        "avg_duration_days": sum(durations) / n,
        "max_depth_pct": max(depths),
        "avg_depth_pct": sum(depths) / n,
        "longest": top,
    }


def equity_curve_from_trades(trades: Sequence[tuple[str, float]],
                             start_date: str | None = None) -> list[tuple[str, float]]:
    """Convert list of (date, pct_ret) into running cumulative-equity curve.
    Each trade adds (cumulative + return) so flat days between trades
    are inserted at the same equity level for monotone date axis.

    Returns daily equity curve covering the full date range so DD durations
    are measured in calendar days, not trade-count.  ``pct_ret`` is a
    decimal return (0.01 = +1%) compounded multiplicatively.
    """
    if not trades:
        return []
    trade_map = {d: r for d, r in trades}
    start = start_date or trades[0][0]
    end = trades[-1][0]
    d0 = datetime.strptime(start, "%Y-%m-%d")
    d1 = datetime.strptime(end, "%Y-%m-%d")
    days = (d1 - d0).days + 1

    eq = 1.0
    curve = []
    for k in range(days):
        d = (d0 + timedelta(days=k)).date().isoformat()
        if d in trade_map:
            eq *= (1 + trade_map[d])
        curve.append((d, eq))
    return curve

"""Kaminski crisis-alpha gate -- reusable validator.

Ported from the trader repo root module ``crisis_alpha_gate.py``
(``validate_crisis_alpha``, ``report_validation``, ``per_year_buy_and_hold``);
the sqlite loaders and the R4 demo are dropped.

Tests whether a strategy maintains a defensive return profile in crisis years
(years where buy-and-hold was significantly negative). A strategy passes if:
  1. Its per-year return is non-negative in every defined crisis year, AND
  2. It outperforms buy-and-hold in every defined crisis year.

The "crisis year" definition is data-driven: any year where B&H is below a
threshold (default -15%). For crypto, this typically captures 2018, 2022,
and could capture later years depending on the path.

Reference: Kaminski, K. & Greyserman, A. (2014). "Trend Following with
Managed Futures: The Search for Crisis Alpha." Wiley, ch. 9:
  "validate any trend strategy by simulating its performance across past equity
   crises ... require positive return in majority"

We adopt the stronger criterion (positive AND beats B&H) for crypto because
the lineup is single-asset and we don't have equity-style diversification.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence


# --- Validator ------------------------------------------------------------------

def validate_crisis_alpha(per_year_strategy: Mapping[str, float],
                          per_year_bh: Mapping[str, float],
                          crisis_threshold_pct: float = -15.0,
                          defensive_margin_pp: float = 5.0,
                          label: str = "") -> dict:
    """
    Three independent gate criteria evaluated per crisis year:

      ABSOLUTE  : strategy return >= 0 (Kaminski's idealized positive-in-crisis)
      DEFENSIVE : strategy return > B&H return (drawdown protection)
      DEFENSIVE+: strategy return > B&H return + defensive_margin_pp
                  (beats B&H by a meaningful margin)

    A strategy "passes" a criterion overall if it satisfies it in EVERY crisis year.

    per_year_strategy: dict {year_str: strategy_return_pct}
    per_year_bh:       dict {year_str: bh_return_pct}
    crisis_threshold_pct: year is "crisis" if bh return <= this (default -15%)
    defensive_margin_pp: defensive+ requires strategy beats B&H by this margin (default 5pp)
    """
    crisis_years = sorted(
        y for y, bh in per_year_bh.items() if bh <= crisis_threshold_pct
    )
    results = []
    n_abs = 0
    n_def = 0
    n_def_plus = 0
    for year in crisis_years:
        bh = per_year_bh[year]
        if year not in per_year_strategy:
            results.append({
                "year": year, "strategy_pct": None, "bh_pct": bh,
                "absolute": "MISSING", "defensive": "MISSING", "defensive_plus": "MISSING",
            })
            continue
        strat = per_year_strategy[year]
        out = strat - bh
        absolute = strat >= 0
        defensive = strat > bh
        defensive_plus = out >= defensive_margin_pp
        if absolute:
            n_abs += 1
        if defensive:
            n_def += 1
        if defensive_plus:
            n_def_plus += 1
        results.append({
            "year": year, "strategy_pct": strat, "bh_pct": bh, "outperformance": out,
            "absolute": "PASS" if absolute else "FAIL",
            "defensive": "PASS" if defensive else "FAIL",
            "defensive_plus": "PASS" if defensive_plus else "FAIL",
        })

    n = len(crisis_years)
    return {
        "label": label,
        "crisis_years": crisis_years, "n_crisis": n,
        "absolute_overall": "PASS" if (n > 0 and n_abs == n) else ("FAIL" if n > 0 else "N/A"),
        "defensive_overall": "PASS" if (n > 0 and n_def == n) else ("FAIL" if n > 0 else "N/A"),
        "defensive_plus_overall": "PASS" if (n > 0 and n_def_plus == n) else ("FAIL" if n > 0 else "N/A"),
        "n_absolute": n_abs, "n_defensive": n_def, "n_defensive_plus": n_def_plus,
        "results": results,
    }


def report_validation(v: dict) -> None:
    """Print the per-year table and overall verdicts of a ``validate_crisis_alpha`` result."""
    print(f"\n{v['label']}")
    print(f"  Crisis years (B&H <= -15%): {v['crisis_years'] or '(none)'}")
    if not v["crisis_years"]:
        print("  No crisis years in this dataset -- gate is N/A")
        return
    print(f"  {'Year':<6} {'Strategy':>11} {'B&H':>11} {'Outperf':>10}  "
          f"{'Abs':>5} {'Def':>5} {'Def+':>5}")
    for r in v["results"]:
        if r["strategy_pct"] is None:
            print(f"  {r['year']:<6} {'-':>11} {r['bh_pct']:>+10.1f}% "
                  f"{'-':>10}  MISSING")
            continue
        print(f"  {r['year']:<6} {r['strategy_pct']:>+10.2f}% {r['bh_pct']:>+10.2f}% "
              f"{r['outperformance']:>+9.2f}%  "
              f"{r['absolute']:>5} {r['defensive']:>5} {r['defensive_plus']:>5}")
    n = v["n_crisis"]
    print(f"  Overall: ABSOLUTE  {v['absolute_overall']:<4} ({v['n_absolute']}/{n})  |  "
          f"DEFENSIVE {v['defensive_overall']:<4} ({v['n_defensive']}/{n})  |  "
          f"DEFENSIVE+ {v['defensive_plus_overall']:<4} ({v['n_defensive_plus']}/{n})")


# --- Helper to compute per-year returns from daily bars --------------------------

def per_year_buy_and_hold(bars: Sequence[tuple[str, Mapping[str, Any]]]) -> dict[str, float]:
    """B&H per-year return as %. Uses first open and last close of each year.

    ``bars`` is a chronological list of ``(iso_date, {"open": o, "close": c, ...})``.
    """
    by_year: dict[str, list] = defaultdict(list)
    for date, b in bars:
        by_year[date[:4]].append((date, b))
    out = {}
    for year, day_bars in by_year.items():
        day_bars.sort(key=lambda x: x[0])
        first = day_bars[0][1]["open"]
        last = day_bars[-1][1]["close"]
        if first > 0:
            out[year] = (last - first) / first * 100
    return out

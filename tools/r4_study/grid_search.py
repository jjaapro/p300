"""Grid search across (weekday, week-of-month, entry hour, hold hours).

Reports the best configurations for the BTC R4 window study. For each
combination computes:
  - all-period stats (n, mean, t-stat, cum)
  - year-by-year mean — specifically, how many full years (2018-2025)
    were positive, and what was the worst-year mean

Three rankings are printed:
  1) Best by overall t-stat
  2) Best by min-year mean (worst-year-best — most year-stable)
  3) Configs that were positive in ALL 8 full years 2018-2025

Honesty caveat: this evaluates ~7,500 configurations. The strongest
in-sample number out of that many will look impressive even on random
data. Treat the leaderboard as hypothesis-generation, not a backtest
result. Pair with walk_forward.py for OOS sanity.

Run:
    python tools/r4_study/grid_search.py
"""
from __future__ import annotations

from collections import defaultdict
from itertools import product

from r4_lib import (
    WEEKDAY_NAMES, WEEK_FILTERS, load_btc_hourly,
    window_returns, stats, fmt_config,
)

ENTRY_HOURS = list(range(0, 24, 2))
HOLD_HOURS  = [6, 8, 10, 12, 14, 16]
WEEKDAY_OPTIONS = [
    {i} for i in range(7)
] + [
    {0, 2}, {2, 4}, {0, 4}, {1, 2}, {1, 4}, {0, 1, 2}, {2, 3, 4},
]
FULL_YEARS = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]


def yearly_split(rd):
    by_year: dict[int, list[float]] = defaultdict(list)
    for d, r in rd.items():
        by_year[int(d[:4])].append(r)
    return by_year


def robustness(by_year: dict[int, list[float]]):
    full = [(y, by_year[y]) for y in FULL_YEARS if y in by_year]
    if len(full) < 7:
        return None
    means = []
    for _, rs in full:
        if not rs:
            continue
        means.append(sum(rs) / len(rs))
    n_pos = sum(1 for m in means if m > 0)
    return {"n_full": len(full), "n_pos": n_pos, "min_year": min(means)}


def main():
    print("Loading BTC hourly...")
    btc = load_btc_hourly()
    print(f"  {len(btc)} bars")

    results = []
    for wds, (wf_name, wf_fn), entry, hold in product(
        WEEKDAY_OPTIONS, WEEK_FILTERS.items(), ENTRY_HOURS, HOLD_HOURS,
    ):
        rd = window_returns(btc, wds, wf_fn, entry, hold)
        s = stats(rd)
        if s is None or s["n"] < 30:
            continue
        rb = robustness(yearly_split(rd))
        if rb is None:
            continue
        results.append({
            "config": (frozenset(wds), wf_name, entry, hold),
            "overall": s,
            "robust": rb,
        })
    print(f"  {len(results)} configurations evaluated\n")

    def header():
        print(f"  {'config':<33s}  {'n':>4}  {'mean%':>7}  {'cum%':>7}  "
              f"{'t':>5}  {'pos_yr':>6}  {'min_yr%':>7}")

    def show(rows, n=15):
        for r in rows[:n]:
            wds, wf, entry, hold = r["config"]
            print(f"  {fmt_config(set(wds), wf, entry, hold):<33s}  "
                  f"{r['overall']['n']:>4d}  "
                  f"{r['overall']['mean']:>+7.3f}  "
                  f"{r['overall']['cum']:>+7.1f}  "
                  f"{r['overall']['t']:>+5.2f}  "
                  f"{r['robust']['n_pos']}/{r['robust']['n_full']:>4d}  "
                  f"{r['robust']['min_year'] * 100:>+7.3f}")

    print("=" * 76)
    print("TOP 15 by overall t-stat")
    print("=" * 76)
    header()
    show(sorted(results, key=lambda r: r["overall"]["t"], reverse=True))

    print(f"\n{'=' * 76}")
    print("TOP 15 by min-year mean (worst-year-best — most year-stable)")
    print("=" * 76)
    header()
    show(sorted(results,
                key=lambda r: r["robust"]["min_year"], reverse=True))

    all_pos = [r for r in results if r["robust"]["n_pos"] == 8]
    print(f"\n{'=' * 76}")
    print(f"Configurations positive in EVERY full year 2018-2025: {len(all_pos)}")
    print("=" * 76)
    if all_pos:
        all_pos.sort(key=lambda r: r["overall"]["t"], reverse=True)
        header()
        show(all_pos, n=30)


if __name__ == "__main__":
    main()

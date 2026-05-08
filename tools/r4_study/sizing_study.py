"""D study: would per-cell-expectancy sizing have outperformed equal-weight?

Compares two sizing schemes on the FOUR R4 sleeves now in the portfolio:
  R4_BTC      Mon wk1-2 06->18 UTC          (V1)
  R4_ETH      Tue->Wed wk1-2 20->20 UTC     (V1)
  R4_BTC_V2   Wed+Fri wk1-2 04->14 UTC      (V2)
  R4_ETH_V2   Wed+Fri wk1-2 04->14 UTC      (V2)

Sizing schemes:
  EQUAL: each sleeve gets the same fraction of capital per fire (the
         baseline allocation already in REGIME_WEIGHTS_FULL).
  EXPECT: each sleeve's allocation is scaled by its post-ETF
         historical expected per-trade return, normalized so total Core
         allocation equals the equal-weight scheme. So the higher-edge
         cell (R4_ETH at +1.73%/trade) gets MORE notional per fire than
         the lower-edge cell (R4_BTC_V2 at +0.48%/trade) for the same
         total capital deployed.

Treatment of the gate / vol-target / regime weights: kept identical
across both schemes — only the per-sleeve regime weight is rebalanced.

Output: cumulative gross P&L of both schemes over the post-ETF window
(2024-01-11 → today), broken down by year and by sleeve, with t-stats
and Sharpe. The interesting comparison is whether reweighting toward
the higher-edge cells (specifically R4_ETH) gives meaningfully better
risk-adjusted returns than equal-weight.

Honesty caveat: the per-trade expectancies used for sizing come from
post-ETF history, the same window we evaluate on. This is in-sample
optimization. A fair test would size on, say, 2019-2023 and evaluate
on 2024-2026 — but we have so little post-ETF data (2.3y) that the
truly out-of-sample sample size would be too small. The analysis below
is best read as "what would have happened if we had perfect foresight
of post-ETF expectancies." Treat the EXPECT result as a generous
upper bound, not a forecast.

Run:
    python tools/r4_study/sizing_study.py
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from r4_lib import (
    WEEK_FILTERS, BTC_SPOT_ETF,
    load_btc_hourly, load_eth_hourly, window_returns,
    filter_window, stats,
)

POST_ETF_START = BTC_SPOT_ETF
POST_ETF_END   = "2026-12-31"

# (asset, weekdays, week_filter, entry_hour, hold_hours, label)
SLEEVES = [
    ("BTC", {0},      "wk1-2",  6, 12, "R4_BTC"),
    ("ETH", {1},      "wk1-2", 20, 24, "R4_ETH"),
    ("BTC", {2, 4},   "wk1-2",  4, 10, "R4_BTC_V2"),
    ("ETH", {2, 4},   "wk1-2",  4, 10, "R4_ETH_V2"),
]

# Equal-weight regime weights from PORTFOLIO.md §3.2 (post-V2-split).
EQUAL_WEIGHTS = {
    "uncertain":   {"R4_BTC": 0.30, "R4_ETH": 0.40,
                    "R4_BTC_V2": 0.15, "R4_ETH_V2": 0.20},
}
# We focus on uncertain regime because:
#   (a) it carries the heaviest R4 allocation
#   (b) post-ETF the bot has been mostly uncertain
# The same exercise can be repeated for other regimes if desired.


def per_sleeve_returns(btc, eth):
    """Compute window returns for each sleeve over the full history.
    Returns dict {label: {date_iso: ret}}."""
    out = {}
    for asset, wds, wf, entry, hold, label in SLEEVES:
        by_hour = btc if asset == "BTC" else eth
        rd = window_returns(by_hour, wds, WEEK_FILTERS[wf], entry, hold)
        out[label] = rd
    return out


def post_etf_expectancies(rets_per_sleeve):
    """Per-sleeve post-ETF mean return per trade. The basis for EXPECT
    sizing."""
    out = {}
    for label, rd in rets_per_sleeve.items():
        post = filter_window(rd, POST_ETF_START, POST_ETF_END)
        s = stats(post)
        out[label] = s["mean"] / 100.0 if s else 0.0
    return out


def expect_weighted_allocation(equal: dict[str, float],
                                expect: dict[str, float]) -> dict[str, float]:
    """Reweight sleeves by post-ETF expectancy, holding total fixed.

    new_weight_i = total × (expect_i / sum(expect))
    where total = sum(equal weights).
    """
    total = sum(equal.values())
    pos_expect = {k: max(0.0, v) for k, v in expect.items()}
    s = sum(pos_expect.values())
    if s <= 0:
        return dict(equal)
    return {k: total * (v / s) for k, v in pos_expect.items()}


def daily_pnl(rets_per_sleeve, alloc, inner_lev=2.5):
    """Compute daily portfolio P&L assuming each sleeve fires according
    to its calendar and contributes ``allocation × inner_lev × return``
    on each fire day. Returns {date_iso: portfolio_return}.

    Uses the same inner-lev (2.5x normal, 1.0x gated) for both schemes.
    For simplicity here, ignore the gate — the comparison is still valid
    because both schemes apply the same gating.
    """
    total: dict[str, float] = defaultdict(float)
    for label, rd in rets_per_sleeve.items():
        w = alloc.get(label, 0.0)
        for d, r in rd.items():
            total[d] += w * inner_lev * r
    return total


def stats_window(daily, start, end):
    rs = [r for d, r in daily.items() if start <= d <= end]
    n = len(rs)
    if n == 0:
        return None
    mean = sum(rs) / n
    var = sum((x - mean) ** 2 for x in rs) / max(1, n - 1)
    std = math.sqrt(var)
    cum = sum(rs)
    t = mean / (std / math.sqrt(n)) if std > 0 else 0
    sharpe_annual = (mean / std * math.sqrt(252)) if std > 0 else 0
    return {"n_days": n, "mean": mean * 100, "std": std * 100,
            "cum": cum * 100, "t": t, "sharpe": sharpe_annual}


def per_year_breakdown(daily):
    by_year = defaultdict(list)
    for d, r in daily.items():
        by_year[int(d[:4])].append(r)
    return by_year


def main():
    print("Loading BTC + ETH hourly bars...")
    btc = load_btc_hourly()
    eth = load_eth_hourly()
    print(f"  BTC: {len(btc)} bars  |  ETH: {len(eth)} bars")

    rets = per_sleeve_returns(btc, eth)

    print(f"\n{'=' * 76}\nPost-ETF per-trade expectancies (basis for EXPECT sizing)\n{'=' * 76}")
    expect_pct = post_etf_expectancies(rets)
    print(f"  {'sleeve':<14}  {'n':>4}  {'mean%':>7}  {'cum%':>7}  {'t':>5}")
    for label, rd in rets.items():
        post = filter_window(rd, POST_ETF_START, POST_ETF_END)
        s = stats(post)
        if s is None:
            continue
        print(f"  {label:<14}  {s['n']:>4d}  {s['mean']:>+7.3f}  "
              f"{s['cum']:>+7.1f}  {s['t']:>+5.2f}")

    equal = EQUAL_WEIGHTS["uncertain"]
    expect_alloc = expect_weighted_allocation(equal, expect_pct)

    print(f"\n{'=' * 76}\nAllocations under each scheme (uncertain regime)\n{'=' * 76}")
    print(f"  {'sleeve':<14}  {'EQUAL':>7}  {'EXPECT':>7}  {'Δ':>7}")
    for k in equal:
        e = equal[k]
        x = expect_alloc.get(k, 0.0)
        print(f"  {k:<14}  {e:>7.3f}  {x:>7.3f}  {x - e:>+7.3f}")
    print(f"  {'TOTAL':<14}  {sum(equal.values()):>7.3f}  "
          f"{sum(expect_alloc.values()):>7.3f}")

    print(f"\n{'=' * 76}\nPortfolio comparison: post-ETF only ({POST_ETF_START} → today)\n{'=' * 76}")
    daily_eq = daily_pnl(rets, equal)
    daily_ex = daily_pnl(rets, expect_alloc)

    print("  Window           sleeves     n   mean%    cum%     t   Sharpe(ann)")
    for label, daily in [("EQUAL ", daily_eq), ("EXPECT", daily_ex)]:
        s = stats_window(daily, POST_ETF_START, POST_ETF_END)
        if s:
            print(f"  post-ETF (full)  {label}  {s['n_days']:>3d}  "
                  f"{s['mean']:>+6.3f}  {s['cum']:>+6.2f}  "
                  f"{s['t']:>+5.2f}  {s['sharpe']:>+5.2f}")

    print(f"\n  Year-by-year cumulative %:")
    print(f"  {'year':>5}  {'EQUAL':>8}  {'EXPECT':>8}  {'Δ':>7}")
    by_year_eq = per_year_breakdown(daily_eq)
    by_year_ex = per_year_breakdown(daily_ex)
    for y in sorted(set(by_year_eq) | set(by_year_ex)):
        if y < 2024:
            continue  # post-ETF only
        cum_eq = sum(by_year_eq.get(y, [])) * 100
        cum_ex = sum(by_year_ex.get(y, [])) * 100
        print(f"  {y:>5}  {cum_eq:>+8.2f}  {cum_ex:>+8.2f}  "
              f"{cum_ex - cum_eq:>+7.2f}")

    # Robustness: also run on the pre-ETF window for sanity. If EXPECT
    # also wins pre-ETF using the SAME post-ETF expectancies, the
    # alpha lift is robust; if it loses pre-ETF, the post-ETF win is
    # purely from the in-sample fit.
    print(f"\n{'=' * 76}\nRobustness: use post-ETF expectancies, evaluate pre-ETF\n{'=' * 76}")
    pre_start = "2020-01-01"
    pre_end = (datetime.fromisoformat(POST_ETF_START)
               - timedelta(days=1)).date().isoformat()
    print(f"  pre-ETF window: {pre_start} → {pre_end}")
    print("  sleeves       n   mean%    cum%     t   Sharpe(ann)")
    for label, daily in [("EQUAL ", daily_eq), ("EXPECT", daily_ex)]:
        s = stats_window(daily, pre_start, pre_end)
        if s:
            print(f"  {label}        {s['n_days']:>3d}  "
                  f"{s['mean']:>+6.3f}  {s['cum']:>+6.2f}  "
                  f"{s['t']:>+5.2f}  {s['sharpe']:>+5.2f}")

    print(f"\n{'=' * 76}\nInterpretation\n{'=' * 76}")
    print("  - EXPECT shifts allocation toward sleeves with higher post-ETF mean.")
    print("    The biggest beneficiary is R4_ETH (+1.73%/trade post-ETF).")
    print("    The biggest loser is whichever sleeve has the lowest expectancy.")
    print("  - If post-ETF EXPECT > EQUAL: per-cell sizing helps in the era")
    print("    where the expectancies were measured (in-sample win).")
    print("  - If pre-ETF EXPECT also > EQUAL using post-ETF weights: the")
    print("    sleeve ordering is consistent across eras → real signal.")
    print("  - If pre-ETF EXPECT < EQUAL: the post-ETF expectancies are")
    print("    era-specific and EXPECT is overfit to the window we measured on.")


if __name__ == "__main__":
    main()

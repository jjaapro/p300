"""Year-by-year breakdown for a hand-picked set of configs.

Useful for visually inspecting which years carry the alpha and where
losing years are concentrated. The half-year split (H1 vs H2) is
informative around the Binance-perp launch (mid-2019) and the spot-ETF
launch (Q1 2024).

Run:
    python studies/notebooks/r4_study/year_breakdown.py
"""
from __future__ import annotations

from collections import defaultdict

from r4_lib import (
    WEEK_FILTERS, BINANCE_FUT_LAUNCH, BTC_SPOT_ETF, ETH_SPOT_ETF,
    load_btc_hourly, load_eth_hourly, window_returns, stats,
)

CONFIGS_BTC = [
    ({0, 2}, "wk1-2", 6, 12, "Mon+Wed wk1-2 06->18 (LIVE BASELINE)"),
    ({2, 4}, "wk1-2", 4, 10, "Wed+Fri wk1-2 04->14 (TOP)"),
    ({4}, "wk1",      4, 12, "Fri wk1 04->16 (NFP cell)"),
    ({2}, "all",      4, 10, "Wed only all-days 04->14"),
]
CONFIGS_ETH = [
    ({1}, "wk1-2", 20, 24, "Tue->Wed wk1-2 20->20 (LIVE R4_ETH)"),
]


def era_label_btc(d):
    if d < BINANCE_FUT_LAUNCH:
        return "pre-Binance-fut"
    if d < BTC_SPOT_ETF:
        return "Binance-fut→ETF"
    return "post-BTC-ETF"


def era_label_eth(d):
    return "post-ETH-ETF" if d >= ETH_SPOT_ETF else "ETH pre-ETF"


def show_year_breakdown(by_hour, configs, era_fn):
    for wds, wf_name, entry, hold, label in configs:
        rd = window_returns(by_hour, wds, WEEK_FILTERS[wf_name], entry, hold)
        by_half: dict[tuple[int, str], list[tuple[str, float]]] = defaultdict(list)
        for d, r in rd.items():
            y = int(d[:4])
            half = "H1" if int(d[5:7]) <= 6 else "H2"
            by_half[(y, half)].append((d, r))

        print(f"\n  {label}:")
        print(f"  {'year':>5}  {'half':>4}  {'n':>4}  {'mean%':>7}  "
              f"{'wr%':>5}  {'cum%':>7}  {'t':>5}    era")
        for (y, half) in sorted(by_half.keys()):
            rs = by_half[(y, half)]
            if not rs:
                continue
            cell = {d: r for d, r in rs}
            s = stats(cell)
            era = era_fn(min(d for d, _ in rs))
            print(f"  {y:>5}  {half:>4}  {s['n']:>4d}  {s['mean']:>+7.3f}  "
                  f"{s['wr']:>5.1f}  {s['cum']:>+7.1f}  {s['t']:>+5.2f}   {era}")


def main():
    print("Loading BTC + ETH hourly bars...")
    btc = load_btc_hourly()
    eth = load_eth_hourly()
    print(f"  BTC: {len(btc)}  |  ETH: {len(eth)}")

    print(f"\n{'=' * 76}\nBTC year-by-year (half-year split)\n{'=' * 76}")
    show_year_breakdown(btc, CONFIGS_BTC, era_label_btc)

    print(f"\n{'=' * 76}\nETH year-by-year (half-year split)\n{'=' * 76}")
    show_year_breakdown(eth, CONFIGS_ETH, era_label_eth)


if __name__ == "__main__":
    main()

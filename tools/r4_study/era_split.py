"""Era split: pre-Binance-perp, Binance→ETF, post-spot-ETF.

For BTC the boundaries are:
  - 2018-01-01 .. 2019-09-07: pre-Binance-perp
  - 2019-09-08 .. 2024-01-10: Binance perps dominant, no spot ETF
  - 2024-01-11 .. today:      post-BTC-spot-ETF

For ETH we additionally split at the spot ETH ETF launch (2024-07-23).

Why this matters: an alpha that exists only in one era is regime-
dependent — the live decision is whether to ride it (and monitor for
decay) or stick to era-stable patterns. The R4_BTC live config
(Mon+Wed wk1-2 06->18) was -0.42%/trade pre-Binance and only became
profitable post-2019. Wed+Fri wk1-2 04->14 was already +0.53%/trade
in the pre-Binance era and is era-stable.

Run:
    python tools/r4_study/era_split.py
"""
from __future__ import annotations

from r4_lib import (
    WEEK_FILTERS, BINANCE_FUT_LAUNCH, BTC_SPOT_ETF, ETH_SPOT_ETF,
    load_btc_hourly, load_eth_hourly, window_returns, filter_window,
    stats, fmt_config,
)

# (start, end) inclusive
BTC_ERAS = [
    ("pre-Binance-fut", "2018-01-01",   "2019-09-07"),
    ("Binance-fut→ETF", "2019-09-08",   "2024-01-10"),
    ("post-BTC-ETF",    "2024-01-11",   "2026-12-31"),
]
ETH_ERAS = [
    ("ETH pre-ETF",  "2020-01-01", "2024-07-22"),
    ("ETH post-ETF", "2024-07-23", "2026-12-31"),
]

CONFIGS_BTC = [
    ({0, 2}, "wk1-2", 6, 12, "Mon+Wed wk1-2 06->18 (LIVE)"),
    ({2, 4}, "wk1-2", 4, 10, "Wed+Fri wk1-2 04->14 (TOP)"),
    ({0, 2}, "all",   6, 12, "Mon+Wed all-days 06->18"),
    ({0, 2}, "all",   4, 10, "Mon+Wed all-days 04->14"),
    ({2, 4}, "all",   4, 10, "Wed+Fri all-days 04->14"),
    ({2}, "all",      4, 10, "Wed only all-days 04->14"),
    ({4}, "wk1",      6, 12, "Fri wk1 06->18 (NFP cell)"),
]
CONFIGS_ETH = [
    ({1}, "wk1-2", 20, 24, "Tue->Wed wk1-2 20->20 (LIVE R4_ETH)"),
    ({1}, "all",   20, 24, "Tue->Wed all-days 20->20"),
    ({1, 4}, "wk1-2", 20, 24, "Tue/Fri wk1-2 20->+1d 20"),
    ({2}, "wk1-2", 20, 24, "Wed->Thu wk1-2 20->20 (control)"),
    ({2, 4}, "wk1-2", 4, 10, "Wed+Fri wk1-2 04->14 (BTC-style)"),
]


def show_eras(by_hour, configs, eras):
    print(f"  {'config':<35s}  ", end="")
    for label, _, _ in eras:
        print(f"{label:<27s}  ", end="")
    print()
    print(f"  {'':<35s}  ", end="")
    for _ in eras:
        print(f"{'n':>4} {'mean%':>6} {'cum%':>7} {'t':>5}    ", end="")
    print()
    for wds, wf, entry, hold, label in configs:
        rd = window_returns(by_hour, wds, WEEK_FILTERS[wf], entry, hold)
        print(f"  {label:<35s}  ", end="")
        for _, start, end in eras:
            s = stats(filter_window(rd, start, end))
            if s is None:
                print(f"{'-':>4} {'-':>6} {'-':>7} {'-':>5}    ", end="")
            else:
                print(f"{s['n']:>4d} {s['mean']:>+6.3f} {s['cum']:>+7.1f} "
                      f"{s['t']:>+5.2f}    ", end="")
        print()


def main():
    print("Loading BTC + ETH hourly bars...")
    btc = load_btc_hourly()
    eth = load_eth_hourly()
    print(f"  BTC: {len(btc)} bars  |  ETH: {len(eth)} bars")

    print(f"\n{'=' * 76}\nBTC: three structural eras\n{'=' * 76}")
    print(f"  pre-Binance-fut: 2018-01-01 → {BINANCE_FUT_LAUNCH} (Binance perp launch)")
    print(f"  Binance-fut→ETF: {BINANCE_FUT_LAUNCH} → {BTC_SPOT_ETF} (BTC spot ETF)")
    print(f"  post-BTC-ETF:    {BTC_SPOT_ETF} → today")
    print()
    show_eras(btc, CONFIGS_BTC, BTC_ERAS)

    print(f"\n{'=' * 76}\nETH: pre/post-ETH-spot-ETF\n{'=' * 76}")
    print(f"  ETH pre-ETF:  2020-01-01 → {ETH_SPOT_ETF}")
    print(f"  ETH post-ETF: {ETH_SPOT_ETF} → today")
    print()
    show_eras(eth, CONFIGS_ETH, ETH_ERAS)


if __name__ == "__main__":
    main()

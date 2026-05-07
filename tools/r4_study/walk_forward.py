"""Walk-forward: fit on TRAIN, evaluate on TEST.

Sweeps the full grid on TRAIN (2018-2022), reports the top-15 by
in-sample t-stat alongside their out-of-sample stats on TEST
(2023-2026). The OOS column is the honest answer to "did the in-sample
ranking generalize?"

Decay typical: 30-50% of in-sample mean is the rule of thumb. Configs
that retain a t-stat above ~+2 OOS are the surviving signals.

Run:
    python tools/r4_study/walk_forward.py
"""
from __future__ import annotations

from itertools import product

from r4_lib import (
    WEEK_FILTERS, load_btc_hourly, window_returns,
    filter_window, stats, fmt_config,
)

TRAIN_START = "2018-01-01"
TRAIN_END   = "2022-12-31"
TEST_START  = "2023-01-01"
TEST_END    = "2026-12-31"

ENTRY_HOURS = list(range(0, 24, 2))
HOLD_HOURS  = [6, 8, 10, 12, 14, 16]
WEEKDAY_OPTIONS = [
    {0}, {1}, {2}, {3}, {4}, {5}, {6},
    {0, 2}, {2, 4}, {0, 4}, {1, 2}, {1, 4},
    {0, 1, 2}, {2, 3, 4},
]


def main():
    print("Loading BTC hourly...")
    btc = load_btc_hourly()
    print(f"  {len(btc)} bars")

    candidates = []
    for wds, (wf_name, wf_fn), entry, hold in product(
        WEEKDAY_OPTIONS, WEEK_FILTERS.items(), ENTRY_HOURS, HOLD_HOURS,
    ):
        rd = window_returns(btc, wds, wf_fn, entry, hold)
        train = stats(filter_window(rd, TRAIN_START, TRAIN_END))
        if train is None or train["n"] < 30:
            continue
        candidates.append({
            "config": (frozenset(wds), wf_name, entry, hold),
            "rd_full": rd,
            "train": train,
        })
    candidates.sort(key=lambda c: c["train"]["t"], reverse=True)
    print(f"  {len(candidates)} configs evaluated on TRAIN")

    print(f"\n{'=' * 76}")
    print(f"WALK-FORWARD: train {TRAIN_START}..{TRAIN_END} → test {TEST_START}..{TEST_END}")
    print(f"{'=' * 76}\n")

    print("  Top 15 by TRAIN t-stat — OOS performance:")
    hdr = (f"  {'config':<33s}  {'TRAIN':<27s}  {'TEST (OOS)':<27s}")
    print(hdr)
    print(f"  {'':<33s}  {'n':>4} {'mean%':>6} {'cum%':>7} {'t':>5}     "
          f"{'n':>4} {'mean%':>6} {'cum%':>7} {'t':>5}")
    for c in candidates[:15]:
        wds, wf, entry, hold = c["config"]
        train = c["train"]
        test = stats(filter_window(c["rd_full"], TEST_START, TEST_END))
        print(f"  {fmt_config(set(wds), wf, entry, hold):<33s}  "
              f"{train['n']:>4d} {train['mean']:>+6.3f} {train['cum']:>+7.1f} "
              f"{train['t']:>+5.2f}    "
              f"{test['n']:>4d} {test['mean']:>+6.3f} {test['cum']:>+7.1f} "
              f"{test['t']:>+5.2f}")


if __name__ == "__main__":
    main()

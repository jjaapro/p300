"""ETH-vs-BTC on regime-on days — does the ETH_REGIME sleeve's thesis hold?

Question: the ETH_DAILY (ETH Regime) sleeve goes long ETH only while the
BTC-derived regime is strong_bull/mild_bull. Its entire justification is
"ETH outperforms BTC on the way up." This script tests that directly:

  On the days the sleeve is ON (regime ∈ {strong_bull, mild_bull}), does
  ETH actually beat BTC?

If yes  → holding both assets adds return; keep the two-asset Continuous pool.
If no   → ETH Regime is redundant correlated beta with negative alpha; size up
          BTC, or express ETH as a long-ETH/short-BTC ratio to isolate alpha.

Reuses the EXACT production pipeline: data.loaders + regime_jplus.classify_series,
close-to-close daily returns, regime[d] computed from T-1 data (look-ahead-safe).
Note: ETH daily is capped to ~3y by the eth_1m lookback in loaders, so the
common BTC∩ETH window is roughly the last 3 years.
"""
from __future__ import annotations

import math
import os
import sys

# Make the repo root importable regardless of CWD.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from data import loaders as data                       # noqa: E402
from strategies.support import regime_jplus as regime  # noqa: E402

BULL = ("strong_bull", "mild_bull")


def corr(xs, ys):
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    return cov / math.sqrt(vx * vy) if vx > 0 and vy > 0 else float("nan")


def stats(subset):
    n = len(subset)
    if n == 0:
        return None
    brs = [r[2] for r in subset]
    ers = [r[3] for r in subset]
    spreads = [e - b for b, e in zip(brs, ers)]
    cum_b = math.prod(1 + x for x in brs) - 1
    cum_e = math.prod(1 + x for x in ers) - 1
    cum_s = math.prod(1 + x for x in spreads) - 1  # long-ETH / short-BTC, gross
    mean_b, mean_e, mean_s = sum(brs) / n, sum(ers) / n, sum(spreads) / n
    wr = sum(1 for s in spreads if s > 0) / n
    std_s = math.sqrt(sum((s - mean_s) ** 2 for s in spreads) / (n - 1)) if n > 1 else 0.0
    t = mean_s / (std_s / math.sqrt(n)) if std_s > 0 else 0.0
    return dict(n=n, cum_b=cum_b, cum_e=cum_e, cum_s=cum_s,
                mean_b=mean_b, mean_e=mean_e, mean_s=mean_s,
                wr=wr, t=t, rho=corr(brs, ers))


def main():
    btc_d = data.load_btc_daily()
    eth_d = data.load_eth_daily()
    ls_d = data.load_ls_ratio_btc()

    dates = sorted(set(btc_d.keys()))
    bc = [btc_d[d]["c"] for d in dates]
    regime_map = regime.classify_series(dates, bc, ls_d)  # {date: mode}, from dates[1:]

    # Aligned daily rows where BOTH assets have a close on d and d-1.
    rows = []  # (date, mode, br, er)
    for i in range(1, len(dates)):
        d, pd = dates[i], dates[i - 1]
        if not (d in eth_d and pd in eth_d and d in btc_d and pd in btc_d):
            continue
        br = (btc_d[d]["c"] - btc_d[pd]["c"]) / btc_d[pd]["c"]
        er = (eth_d[d]["c"] - eth_d[pd]["c"]) / eth_d[pd]["c"]
        rows.append((d, regime_map.get(d, "uncertain"), br, er))

    if not rows:
        print("No overlapping BTC/ETH rows — check data.")
        return

    d0, d1 = rows[0][0], rows[-1][0]
    print(f"Sample: {d0} -> {d1}   common BTC/ETH days: {len(rows)}\n")

    # Regime distribution
    from collections import Counter
    dist = Counter(r[1] for r in rows)
    print("Regime-day distribution (sleeve ON = strong_bull + mild_bull):")
    for m in ("strong_bull", "mild_bull", "uncertain", "bear"):
        print(f"  {m:12s} {dist.get(m, 0):5d}")
    print()

    subsets = {
        "ALL days":        rows,
        "BULL (ON)":       [r for r in rows if r[1] in BULL],
        "  strong_bull":   [r for r in rows if r[1] == "strong_bull"],
        "  mild_bull":     [r for r in rows if r[1] == "mild_bull"],
        "OFF (unc+bear)":  [r for r in rows if r[1] not in BULL],
    }

    hdr = (f"{'subset':16s} {'n':>5s} {'cumBTC':>9s} {'cumETH':>9s} "
           f"{'cumE-B*':>9s} {'meanE-B':>9s} {'WR(E>B)':>8s} {'t-stat':>7s} {'corr':>6s}")
    print(hdr)
    print("-" * len(hdr))
    for name, sub in subsets.items():
        s = stats(sub)
        if s is None:
            print(f"{name:16s}  (empty)")
            continue
        print(f"{name:16s} {s['n']:5d} {s['cum_b']*100:8.1f}% {s['cum_e']*100:8.1f}% "
              f"{s['cum_s']*100:8.1f}% {s['mean_s']*100:8.3f}% {s['wr']*100:7.1f}% "
              f"{s['t']:7.2f} {s['rho']:6.2f}")
    print("\n* cumE-B = gross cumulative return of a daily-rebalanced "
          "long-ETH / short-BTC (the pure ETH-outperformance leg), no funding/fees.")

    # Context: ETH/BTC ratio drift over the window.
    r_start = eth_d[d0]["c"] / btc_d[d0]["c"]
    r_end = eth_d[d1]["c"] / btc_d[d1]["c"]
    print(f"\nETH/BTC ratio: {r_start:.5f} → {r_end:.5f}  "
          f"({(r_end/r_start - 1)*100:+.1f}% over the window)")


if __name__ == "__main__":
    main()

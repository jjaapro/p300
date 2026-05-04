"""Statistical validation suite for replay variants.

Produces, for a chosen variant id:
  1. Bootstrap Sharpe 95% confidence interval (N=1000 resamples with
     replacement of daily returns). Gives an honest range around the
     point-estimate Sharpe.
  2. Rolling 180-day Sharpe stability — is the edge stationary or
     concentrated in a few windows?
  3. Per-year Sharpe, CAGR, MDD decomposition.
  4. Correlation to BTC buy-and-hold daily returns — how much is BTC-beta?
  5. Worst / best 30-day windows.

Default target: p300_aggressive_v2_v1_0__C (full P-300 combined).

Output is plain text organized by section. Writes nothing to the DB.
"""
from __future__ import annotations

import argparse
import math
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
DASH_DB = REPO / "data" / "dashboard.db"
TRADER_DB = REPO / "data" / "trader.db"


def load_returns(variant_id: str) -> list[tuple[str, float]]:
    con = sqlite3.connect(str(DASH_DB))
    rows = con.execute(
        "SELECT date, return_1x_pct FROM variant_daily_returns "
        "WHERE variant_id = ? AND source = 'replay' ORDER BY date",
        (variant_id,),
    ).fetchall()
    con.close()
    return [(d, float(r or 0)) for d, r in rows]


def load_variant_capital(variant_id: str) -> float:
    con = sqlite3.connect(str(DASH_DB))
    row = con.execute(
        "SELECT capital_usdt FROM variants WHERE id = ?", (variant_id,),
    ).fetchone()
    con.close()
    return float(row[0]) if row and row[0] else 10_000.0


def load_btc_daily_returns() -> dict[str, float]:
    """BTC daily log-returns keyed by date."""
    con = sqlite3.connect(str(TRADER_DB))
    rows = con.execute(
        "SELECT timestamp, close FROM cd_futures_ohlcv ORDER BY timestamp"
    ).fetchall()
    con.close()
    by_day: dict[str, float] = {}
    for ts, c in rows:
        if c is None:
            continue
        d = datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
        by_day[d] = float(c)
    dates = sorted(by_day)
    out: dict[str, float] = {}
    for i in range(1, len(dates)):
        p0, p1 = by_day[dates[i - 1]], by_day[dates[i]]
        if p0 > 0 and p1 > 0:
            out[dates[i]] = math.log(p1 / p0)
    return out


# ─── Metric primitives ───────────────────────────────────────────────────────

def daily_ann_sharpe(rets: list[float]) -> float:
    """`rets` in percent (not fraction). Returns annualised Sharpe."""
    if len(rets) < 2:
        return float("nan")
    m = sum(rets) / len(rets)
    v = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    s = math.sqrt(v)
    return (m / s) * math.sqrt(365) if s > 0 else float("nan")


def cagr(rets: list[float]) -> float:
    """Compound growth annualised, assumes 365d/y."""
    if not rets:
        return float("nan")
    eq = 1.0
    for r in rets:
        eq *= (1 + r / 100)
    years = len(rets) / 365.25
    return eq ** (1 / years) - 1 if eq > 0 and years > 0 else float("nan")


def max_drawdown(rets: list[float]) -> float:
    """Max DD as fraction (negative)."""
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in rets:
        eq *= (1 + r / 100)
        if eq > peak:
            peak = eq
        dd = eq / peak - 1
        if dd < mdd:
            mdd = dd
    return mdd


def pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 2 or len(y) != n:
        return float("nan")
    mx = sum(x) / n; my = sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((a - my) ** 2 for a in y))
    if sx == 0 or sy == 0:
        return float("nan")
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    return cov / (sx * sy)


# ─── Validators ──────────────────────────────────────────────────────────────

def bootstrap_sharpe(rets: list[float], n_trials: int = 1000,
                     seed: int = 42) -> tuple[float, float, float, float]:
    """Return (point_sharpe, lower_95, upper_95, median_boot)."""
    point = daily_ann_sharpe(rets)
    rng = random.Random(seed)
    n = len(rets)
    sharpes = []
    for _ in range(n_trials):
        sample = [rets[rng.randint(0, n - 1)] for _ in range(n)]
        s = daily_ann_sharpe(sample)
        if not math.isnan(s):
            sharpes.append(s)
    sharpes.sort()
    if not sharpes:
        return point, float("nan"), float("nan"), float("nan")
    lo = sharpes[int(0.025 * len(sharpes))]
    hi = sharpes[int(0.975 * len(sharpes))]
    med = sharpes[len(sharpes) // 2]
    return point, lo, hi, med


def rolling_sharpe(rets: list[float], window: int = 180) -> list[float]:
    if len(rets) < window:
        return []
    out = []
    for i in range(window - 1, len(rets)):
        chunk = rets[i - window + 1: i + 1]
        out.append(daily_ann_sharpe(chunk))
    return out


def yearly_breakdown(rows: list[tuple[str, float]]) -> dict[str, dict]:
    by_year: dict[str, list[float]] = {}
    for d, r in rows:
        by_year.setdefault(d[:4], []).append(r)
    out: dict[str, dict] = {}
    for y, rets in sorted(by_year.items()):
        out[y] = {
            "n": len(rets),
            "sharpe": daily_ann_sharpe(rets),
            "cagr_pct": cagr(rets) * 100 if not math.isnan(cagr(rets)) else float("nan"),
            "mdd_pct": max_drawdown(rets) * 100,
            "worst_day_pct": min(rets),
            "best_day_pct": max(rets),
        }
    return out


def worst_and_best_30d(rows: list[tuple[str, float]], window: int = 30
                       ) -> tuple[tuple[str, float], tuple[str, float]]:
    if len(rows) < window:
        return ("", float("nan")), ("", float("nan"))
    best = ("", -1e18)
    worst = ("", 1e18)
    for i in range(window - 1, len(rows)):
        # Cumulative return of last `window` days
        chunk = [r for _, r in rows[i - window + 1: i + 1]]
        eq = 1.0
        for r in chunk:
            eq *= (1 + r / 100)
        ret = (eq - 1) * 100
        if ret > best[1]:
            best = (rows[i][0], ret)
        if ret < worst[1]:
            worst = (rows[i][0], ret)
    return worst, best


def correlation_vs_btc(rows: list[tuple[str, float]], btc: dict[str, float]) -> float:
    # Align on common dates
    paired = [(r, btc[d]) for d, r in rows if d in btc]
    if len(paired) < 30:
        return float("nan")
    strat = [p[0] / 100.0 for p in paired]  # percent → fraction
    btc_r = [p[1] for p in paired]           # already log-fraction
    return pearson(strat, btc_r)


# ─── Orchestrator ────────────────────────────────────────────────────────────

def run(variant_id: str) -> None:
    rows = load_returns(variant_id)
    if not rows:
        print(f"No replay returns found for {variant_id}")
        return
    rets = [r for _, r in rows]
    n = len(rets)
    capital = load_variant_capital(variant_id)

    # Rebuild equity curve for context
    eq = capital
    for r in rets:
        eq *= (1 + r / 100)
    total_ret = (eq / capital - 1) * 100

    print("=" * 78)
    print(f"  Statistical validation — {variant_id}")
    print(f"  window: {rows[0][0]} → {rows[-1][0]}  ({n} days, "
          f"{n / 365.25:.2f} years)")
    print(f"  start ${capital:,.0f}  →  end ${eq:,.2f}  ({total_ret:+.2f}%)")
    print("=" * 78)

    # 1. Bootstrap Sharpe
    print("\n── Bootstrap Sharpe 95% CI (N=1000, seed=42) ──")
    point, lo, hi, med = bootstrap_sharpe(rets)
    print(f"  point estimate:  {point:+.3f}")
    print(f"  95% CI:          [{lo:+.3f}, {hi:+.3f}]")
    print(f"  bootstrap median:{med:+.3f}")
    width = hi - lo
    print(f"  CI width:        {width:.3f}  "
          f"({'tight' if width < 1.0 else 'wide' if width > 2.0 else 'moderate'})")

    # 2. Yearly breakdown
    print("\n── Per-year breakdown ──")
    yb = yearly_breakdown(rows)
    print(f"  {'year':<6} {'n':>4} {'sharpe':>8} {'CAGR %':>9} {'MDD %':>8} "
          f"{'worst day %':>12} {'best day %':>12}")
    for y, d in yb.items():
        print(f"  {y:<6} {d['n']:>4} {d['sharpe']:>+8.2f} {d['cagr_pct']:>+8.2f}  "
              f"{d['mdd_pct']:>+7.2f}  {d['worst_day_pct']:>+11.2f}  "
              f"{d['best_day_pct']:>+11.2f}")

    # 3. Rolling 180d Sharpe
    print("\n── Rolling 180d Sharpe ──")
    rs = rolling_sharpe(rets, 180)
    if rs:
        print(f"  min:    {min(rs):+.2f}")
        print(f"  median: {sorted(rs)[len(rs)//2]:+.2f}")
        print(f"  max:    {max(rs):+.2f}")
        neg_windows = sum(1 for s in rs if s < 0)
        print(f"  negative-Sharpe windows: {neg_windows} / {len(rs)} "
              f"({neg_windows/len(rs)*100:.1f}%)")
    else:
        print("  (not enough data for 180d window)")

    # 4. Correlation to BTC
    print("\n── Correlation to BTC buy-and-hold ──")
    btc = load_btc_daily_returns()
    corr = correlation_vs_btc(rows, btc)
    print(f"  Pearson corr (daily log-returns): {corr:+.3f}")
    if math.isnan(corr):
        print("  (insufficient data)")
    else:
        beta_desc = ("essentially uncorrelated" if abs(corr) < 0.2
                     else "weakly correlated" if abs(corr) < 0.5
                     else "moderately correlated" if abs(corr) < 0.75
                     else "strongly correlated")
        print(f"  ({beta_desc} — "
              f"{'BTC beta drives most returns' if corr > 0.75 else 'some BTC exposure remains' if corr > 0.3 else 'largely independent of BTC moves'})")

    # 5. 30-day windows
    print("\n── Worst / best rolling 30-day windows ──")
    worst, best = worst_and_best_30d(rows)
    print(f"  worst 30d: {worst[0]}  {worst[1]:+.2f}%")
    print(f"  best  30d: {best[0]}   {best[1]:+.2f}%")

    print("\n" + "=" * 78)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variant", default="p300_aggressive_v2_v1_0__C",
                    help="Variant id to validate.")
    args = ap.parse_args()
    run(args.variant)
    return 0


if __name__ == "__main__":
    sys.exit(main())

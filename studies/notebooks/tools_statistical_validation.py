"""Statistical validation suite for a P-300 variant's realized SHADOW
trade ledger.

Produces, for a chosen variant id:
  1. Bootstrap Sharpe 95% confidence interval (N=1000 resamples with
     replacement of daily returns). Gives an honest range around the
     point-estimate Sharpe.
  2. Rolling 180-day Sharpe stability — is the edge stationary or
     concentrated in a few windows?
  3. Per-year Sharpe, CAGR, MDD decomposition.
  4. Correlation to BTC buy-and-hold daily returns — how much is BTC-beta?
  5. Worst / best 30-day windows.

Default target: p300_aggressive_v2_v1_0 (live SHADOW variant).

Output is plain text organized by section. Writes nothing to the DB.

Data source: closed trades in ``trades`` (via
``strategies.support.strategy_health.trades_daily_returns``). Pre-2026-05-13 this
tool read from ``variant_daily_returns WHERE source='replay'``, but
that table is no longer written by any production path after the
trade-emitter migration (Phase 3-5). See AUDIT_2026_05_13.
"""
from __future__ import annotations

import argparse
import math
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from strategies.support import db, strategy_health

REPO = Path(__file__).resolve().parents[2]


def load_variant_capital(variant_id: str) -> float:
    con = sqlite3.connect(str(db.DASH_DB))
    row = con.execute(
        "SELECT capital_usdt FROM variants WHERE id = ?", (variant_id,),
    ).fetchone()
    con.close()
    return float(row[0]) if row and row[0] else 10_000.0


def _variant_trade_window(variant_id: str) -> tuple[str, str] | None:
    """Earliest and latest closed-trade exit date for the variant. Returns
    None if no closed trades exist."""
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        row = con.execute(
            "SELECT MIN(date(actual_exit_time)), MAX(date(actual_exit_time)) "
            "FROM trades WHERE strategy_variant=? AND status='closed' "
            "  AND actual_exit_time IS NOT NULL",
            (variant_id,),
        ).fetchone()
    finally:
        con.close()
    if not row or row[0] is None or row[1] is None:
        return None
    return row[0], row[1]


def load_returns(variant_id: str) -> list[tuple[str, float]]:
    """Daily realized returns for the variant, percent, oldest-first.

    Aggregates closed-trade pnl_usdt by exit date and divides by the
    variant's capital — same math as
    ``strategies.support.strategy_health.trades_daily_returns``. The series is
    zero-filled across the calendar window so Sharpe / MDD / rolling
    stats see flat days, not gaps."""
    window = _variant_trade_window(variant_id)
    if window is None:
        return []
    start, end = window
    capital = load_variant_capital(variant_id)
    return strategy_health.trades_daily_returns(
        variant_id, start, end, capital, zero_fill=True,
    )


def load_btc_daily_returns() -> dict[str, float]:
    """BTC daily log-returns keyed by date.

    Switched 2026-05-13 from ``cd_futures_ohlcv`` (perp) to
    ``cd_spot_binance`` to match the v6 signal-data switch — perp and
    spot daily returns are close but not identical (basis drift adds
    ~10bp daily noise). See AUDIT_2026_05_13 Medium item."""
    con = sqlite3.connect(str(db.TRADER_DB))
    rows = con.execute(
        "SELECT timestamp, close FROM cd_spot_binance ORDER BY timestamp"
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
    """Compound annual growth rate, assumes 365d/y.

    Equity is built additively (``eq += r/100`` on unit capital — the
    returns are arithmetic-on-fixed-capital, see strategy_health
    rationale). CAGR itself remains a geometric annualisation of the
    final equity since that's what the term means."""
    if not rets:
        return float("nan")
    eq = 1.0
    for r in rets:
        eq += r / 100
    years = len(rets) / 365.25
    return eq ** (1 / years) - 1 if eq > 0 and years > 0 else float("nan")


def max_drawdown(rets: list[float]) -> float:
    """Max DD as fraction (negative). Additive equity walk — see cagr()."""
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in rets:
        eq += r / 100
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

def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Rational-approximation inverse normal CDF (Abramowitz & Stegun 26.2.23)."""
    if p <= 0:
        return -math.inf
    if p >= 1:
        return math.inf
    if p < 0.5:
        return -_norm_ppf(1 - p)
    t = math.sqrt(-2.0 * math.log(1 - p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t)


def bootstrap_sharpe(rets: list[float], n_trials: int = 1000,
                     seed: int = 42) -> tuple[float, float, float, float]:
    """Return (point_sharpe, lower_95, upper_95, median_boot).

    Uses BCa (bias-corrected and accelerated) bootstrap for the confidence
    interval, which corrects for skewness in the bootstrap distribution —
    important for fat-tailed crypto returns where the basic percentile
    method produces intervals that are too narrow on the downside.
    """
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
    med = sharpes[len(sharpes) // 2]

    # BCa bias-correction factor z0
    below = sum(1 for s in sharpes if s < point)
    z0 = _norm_ppf(below / len(sharpes)) if len(sharpes) > 0 else 0.0

    # BCa acceleration factor a (jackknife)
    jk = []
    for i in range(n):
        leave_one = rets[:i] + rets[i + 1:]
        s = daily_ann_sharpe(leave_one)
        if not math.isnan(s):
            jk.append(s)
    if len(jk) >= 2:
        jk_mean = sum(jk) / len(jk)
        num = sum((jk_mean - v) ** 3 for v in jk)
        den = sum((jk_mean - v) ** 2 for v in jk)
        a = num / (6.0 * den ** 1.5) if den > 0 else 0.0
    else:
        a = 0.0

    alpha_lo, alpha_hi = 0.025, 0.975
    z_lo = _norm_ppf(alpha_lo)
    z_hi = _norm_ppf(alpha_hi)
    adj_lo = _norm_cdf(z0 + (z0 + z_lo) / (1 - a * (z0 + z_lo)))
    adj_hi = _norm_cdf(z0 + (z0 + z_hi) / (1 - a * (z0 + z_hi)))
    idx_lo = max(0, min(len(sharpes) - 1, int(adj_lo * len(sharpes))))
    idx_hi = max(0, min(len(sharpes) - 1, int(adj_hi * len(sharpes))))

    return point, sharpes[idx_lo], sharpes[idx_hi], med


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
    """Find the worst / best rolling N-day window by summed return.
    Returns are arithmetic-on-fixed-capital, so window return is the sum
    of the daily returns in that window (not compound)."""
    if len(rows) < window:
        return ("", float("nan")), ("", float("nan"))
    best = ("", -1e18)
    worst = ("", 1e18)
    for i in range(window - 1, len(rows)):
        ret = sum(r for _, r in rows[i - window + 1: i + 1])
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
    strat = [p[0] / 100.0 for p in paired]              # percent → simple fraction
    btc_r = [math.exp(p[1]) - 1.0 for p in paired]     # log → simple fraction
    return pearson(strat, btc_r)


# ─── Orchestrator ────────────────────────────────────────────────────────────

def run(variant_id: str) -> None:
    rows = load_returns(variant_id)
    if not rows:
        print(f"No realized trades found for {variant_id}")
        return
    rets = [r for _, r in rows]
    n = len(rets)
    capital = load_variant_capital(variant_id)

    # Rebuild equity curve for context — additive on fixed capital
    # because each daily return is realized PnL / starting capital.
    eq = capital
    for r in rets:
        eq += capital * r / 100
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
    ap.add_argument("--variant", default="p300_aggressive_v2_v1_0",
                    help="Variant id to validate. Default: the live "
                         "SHADOW variant. Pre-2026-05-13 default was the "
                         "since-deprecated `__C` replay variant.")
    args = ap.parse_args()
    run(args.variant)
    return 0


if __name__ == "__main__":
    sys.exit(main())

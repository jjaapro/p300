"""Strategy health monitoring — windowed portfolio + per-sleeve metrics.

Computes Sharpe, win rate, and max drawdown over three windows (YTD, 30D,
90D) for the live variant, plus a per-sleeve breakdown of closed trades.
Pure read-side over `variant_daily_returns` and `trades`; no side effects.

Intended uses:
  - Standalone CLI: `python -m services.strategy_health --variant <id>`
  - Startup banner in run.py / dashboard widget
  - Future: trip a circuit-breaker when rolling Sharpe falls below a
    threshold (initiative #1 from the risk-management gap analysis).

Why three windows: YTD anchors against a fixed point so the metric isn't
pulled by old data; 30D catches recent drift fast; 90D is the smoothing
horizon between the two. All three are computed from the same data —
the function just slices differently.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from services import clock, db


# ─── Metric primitives ──────────────────────────────────────────────────────


def annualized_sharpe(rets_pct: list[float]) -> float | None:
    """Annualised Sharpe on a list of daily returns (in percent, not
    fraction). Crypto convention: 365 trading days/year. Returns None for
    series too short to estimate variance."""
    n = len(rets_pct)
    if n < 2:
        return None
    m = sum(rets_pct) / n
    v = sum((r - m) ** 2 for r in rets_pct) / (n - 1)
    s = math.sqrt(v)
    if s <= 0:
        return None
    return (m / s) * math.sqrt(365)


def max_drawdown_pct(rets_pct: list[float]) -> float | None:
    """Max peak-to-trough drawdown across the equity curve formed by
    compounding the daily returns. Returned as a NEGATIVE percent (e.g.
    -15.3 for a 15.3% drawdown). None if no data."""
    if not rets_pct:
        return None
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in rets_pct:
        eq *= (1 + r / 100.0)
        if eq > peak:
            peak = eq
        dd = (eq / peak - 1.0) * 100.0
        if dd < mdd:
            mdd = dd
    return mdd


def cumulative_return_pct(rets_pct: list[float]) -> float:
    """Compound the daily returns and return total return as a percent."""
    eq = 1.0
    for r in rets_pct:
        eq *= (1 + r / 100.0)
    return (eq - 1.0) * 100.0


def max_drawdown_from_pnl_curve(pnls_usdt: list[float],
                                 starting_capital: float) -> float | None:
    """Max DD across a sequential per-trade P&L stream, expressed as a
    percent of the running equity. ``starting_capital`` seeds equity
    pre-trade-1; subsequent trades' MDD is relative to the running peak."""
    if not pnls_usdt or starting_capital <= 0:
        return None
    eq = starting_capital
    peak = starting_capital
    mdd = 0.0
    for p in pnls_usdt:
        eq += p
        if eq > peak:
            peak = eq
        dd = (eq / peak - 1.0) * 100.0
        if dd < mdd:
            mdd = dd
    return mdd


# ─── Window resolution ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Window:
    """A named date window for metric reporting. ``start`` is inclusive,
    ``end`` is inclusive. Both ISO date strings."""
    name: str
    start: str
    end: str


def resolve_windows(end_date: date | None = None) -> list[Window]:
    """Return [YTD, 90D, 30D] windows ending at ``end_date`` (default:
    yesterday by clock). YTD starts on Jan 1 of end_date's year."""
    if end_date is None:
        end_date = (clock.now_utc() - timedelta(days=1)).date()
    end_iso = end_date.isoformat()
    return [
        Window("YTD", date(end_date.year, 1, 1).isoformat(), end_iso),
        Window("90D", (end_date - timedelta(days=89)).isoformat(), end_iso),
        Window("30D", (end_date - timedelta(days=29)).isoformat(), end_iso),
    ]


# ─── Portfolio metrics (variant_daily_returns) ──────────────────────────────


def _load_daily_returns(variant_id: str, start: str, end: str,
                        source: str = "live_computed") -> list[float]:
    """Fetch daily returns (percent) for the variant within [start, end]
    inclusive, oldest-first. ``source`` filters ``variant_daily_returns.source``
    — pass ``'live_computed'`` for the live variant (default) or ``'replay'``
    for backtest inspection."""
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        rows = con.execute(
            "SELECT return_1x_pct FROM variant_daily_returns "
            "WHERE variant_id=? AND source=? "
            "  AND date >= ? AND date <= ? ORDER BY date",
            (variant_id, source, start, end),
        ).fetchall()
    finally:
        con.close()
    return [float(r[0]) for r in rows if r[0] is not None]


# Default Core/tactical split for the P-300 portfolio (matches
# tools/combine_replay.py:W_CORE/W_TACTICAL and PORTFOLIO.md §1).
W_CORE = 0.50
W_TACTICAL = 0.50


def _combined_daily_returns(variant_id: str, start: str, end: str,
                             capital_usdt: float,
                             w_core: float = W_CORE) -> list[float]:
    """Combined live-portfolio daily returns (percent), oldest-first.

    The live variant's ``variant_daily_returns`` rows hold Core J+'s
    STANDALONE return (jplus_service writes ``sim_gross − JPLUS fees``
    with no portfolio scaling). The tactical sleeves' P&L is recorded
    only in the ``trades`` table. Neither source alone tells the
    operator how the actual variant is doing, so the health banner has
    to combine them at read time:

        combined[d] = w_core × core_return[d]
                    + (sum of non-JPLUS closed-trade pnl_usdt with
                       exit_date == d) / capital × 100

    JPLUS_* trades are excluded to avoid double-counting — Core's per-
    sub-sleeve P&L is already inside the VDR row via the gross-minus-
    fees derivation.

    Days present in only one source are still emitted with the missing
    side as 0 (e.g. a day with a tactical fill but no VDR row yet
    contributes only the tactical slice)."""
    # Core side: VDR live_computed rows
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        core_rows = con.execute(
            "SELECT date, return_1x_pct FROM variant_daily_returns "
            "WHERE variant_id=? AND source='live_computed' "
            "  AND date >= ? AND date <= ?",
            (variant_id, start, end),
        ).fetchall()
        # Tactical side: aggregate closed-trade pnl by exit date, excluding
        # the JPLUS_* prefix (those are Core's sub-sleeves, already in VDR).
        tactical_rows = con.execute(
            "SELECT date(actual_exit_time) AS d, "
            "       COALESCE(SUM(pnl_usdt), 0.0) AS pnl "
            "FROM trades "
            "WHERE strategy_variant=? AND status='closed' "
            "  AND strategy NOT LIKE 'JPLUS_%' "
            "  AND date(actual_exit_time) >= ? "
            "  AND date(actual_exit_time) <= ? "
            "GROUP BY d",
            (variant_id, start, end),
        ).fetchall()
    finally:
        con.close()

    core_map = {r[0]: float(r[1]) for r in core_rows if r[1] is not None}
    tac_map = {r[0]: float(r[1]) for r in tactical_rows
                if r[0] is not None and r[1] is not None}
    dates = sorted(set(core_map) | set(tac_map))
    out: list[float] = []
    for d in dates:
        core_pct = core_map.get(d, 0.0)
        tac_pnl = tac_map.get(d, 0.0)
        tac_pct = (tac_pnl / capital_usdt * 100.0) if capital_usdt > 0 else 0.0
        out.append(w_core * core_pct + tac_pct)
    return out


@dataclass(frozen=True)
class PortfolioMetrics:
    """Read-only snapshot of portfolio metrics for a single window."""
    window: str
    n_days: int
    total_return_pct: float
    sharpe: float | None
    win_rate_pct: float | None       # % of days with return > 0
    max_drawdown_pct: float | None


def portfolio_metrics(variant_id: str, window: Window,
                      source: str = "live_computed",
                      capital_usdt: float | None = None,
                      w_core: float = W_CORE) -> PortfolioMetrics:
    """Portfolio metrics for one window.

    For ``source='live_computed'`` with ``capital_usdt`` supplied, returns
    are computed as the COMBINED daily series (Core VDR × w_core +
    tactical trade P&L / capital × 100). This is what an operator
    actually wants — the variant's true daily return — because the
    live-variant VDR row alone is just Core standalone.

    For ``source='replay'`` (or live without capital), reads VDR rows
    directly and treats them as already-final returns (matches
    combine_replay's convention: ``__core_*`` variants store standalone
    Core, ``__combined_*`` variants store the 50/50 mix). Caller picks
    the right variant id."""
    if source == "live_computed" and capital_usdt is not None:
        rets = _combined_daily_returns(variant_id, window.start, window.end,
                                         capital_usdt, w_core=w_core)
    else:
        rets = _load_daily_returns(variant_id, window.start, window.end, source)
    n = len(rets)
    if n == 0:
        return PortfolioMetrics(window.name, 0, 0.0, None, None, None)
    n_up = sum(1 for r in rets if r > 0)
    return PortfolioMetrics(
        window=window.name, n_days=n,
        total_return_pct=cumulative_return_pct(rets),
        sharpe=annualized_sharpe(rets),
        win_rate_pct=(n_up / n * 100.0) if n > 0 else None,
        max_drawdown_pct=max_drawdown_pct(rets),
    )


# ─── Per-sleeve metrics (trades) ────────────────────────────────────────────


def _load_closed_trades(variant_id: str, strategy: str,
                         start: str, end: str
                         ) -> list[tuple[str, str, float]]:
    """Closed trades for (variant, strategy) whose ``actual_exit_time``
    falls in [start, end]. Returns [(entry_time_iso, exit_time_iso,
    pnl_usdt)] oldest-first."""
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        rows = con.execute(
            "SELECT actual_entry_time, actual_exit_time, pnl_usdt "
            "FROM trades "
            "WHERE strategy_variant=? AND strategy=? AND status='closed' "
            "  AND actual_exit_time >= ? AND actual_exit_time <= ? "
            "ORDER BY actual_exit_time",
            (variant_id, strategy, start, end + "T23:59:59+00:00"),
        ).fetchall()
    finally:
        con.close()
    return [(r[0], r[1], float(r[2] or 0.0)) for r in rows
            if r[0] is not None and r[1] is not None and r[2] is not None]


# Canonical sleeve list — shown in the per-sleeve table even when a sleeve
# has produced no trades in a window. Tactical sleeves first, then Core J+
# sub-sleeves (JPLUS_ prefix). If you add a sleeve, append here so it shows
# up in the bot startup banner.
KNOWN_SLEEVES = (
    "ADX", "CARRY", "THU_BEAR", "PDO_RETOUCH", "CPR", "FOMC",
    "JPLUS_EMA_BTC", "JPLUS_ETH_DAILY", "JPLUS_R4_BTC", "JPLUS_R4_ETH",
)


def _all_strategies_for_variant(variant_id: str) -> list[str]:
    """Union of ``KNOWN_SLEEVES`` and any other ``strategy`` values the
    variant has actually traded — preserves the canonical ordering
    (tactical first, JPLUS_* second), then appends anything unexpected at
    the end so it doesn't get hidden."""
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        rows = con.execute(
            "SELECT DISTINCT strategy FROM trades WHERE strategy_variant=?",
            (variant_id,),
        ).fetchall()
    finally:
        con.close()
    seen = {r[0] for r in rows if r[0]}
    out: list[str] = list(KNOWN_SLEEVES)
    for s in sorted(seen - set(KNOWN_SLEEVES)):
        out.append(s)
    return out


@dataclass(frozen=True)
class SleeveMetrics:
    sleeve: str
    window: str
    n_trades: int
    total_pnl_usdt: float
    win_rate_pct: float | None
    sharpe: float | None              # on per-trade pnl% (relative to capital)
    max_drawdown_pct: float | None    # on cumulative pnl curve
    expectancy_usdt: float | None     # mean $ per trade (= total / N)
    profit_factor: float | None       # sum_wins / |sum_losses|; None if no losses
    avg_hold_hours: float | None      # mean (exit - entry) in hours


def _hold_hours(entry_iso: str, exit_iso: str) -> float | None:
    try:
        entry_dt = datetime.fromisoformat(entry_iso)
        exit_dt = datetime.fromisoformat(exit_iso)
    except (TypeError, ValueError):
        return None
    if entry_dt.tzinfo is None:
        entry_dt = entry_dt.replace(tzinfo=timezone.utc)
    if exit_dt.tzinfo is None:
        exit_dt = exit_dt.replace(tzinfo=timezone.utc)
    return (exit_dt - entry_dt).total_seconds() / 3600.0


def sleeve_metrics(variant_id: str, strategy: str, window: Window,
                   capital_usdt: float) -> SleeveMetrics:
    trades = _load_closed_trades(variant_id, strategy, window.start, window.end)
    n = len(trades)
    if n == 0:
        return SleeveMetrics(strategy, window.name, 0, 0.0,
                              None, None, None, None, None, None)
    pnls_usdt = [t[2] for t in trades]
    total = sum(pnls_usdt)
    wins = [p for p in pnls_usdt if p > 0]
    losses = [p for p in pnls_usdt if p < 0]
    # Per-trade return as % of capital — gives a per-trade Sharpe-like
    # quality metric. Not strictly comparable to daily-Sharpe (different
    # n and different sampling), but useful as a relative ranking.
    pnls_pct = [p / capital_usdt * 100.0 for p in pnls_usdt]
    sum_losses = sum(losses)  # negative or zero
    if losses:
        profit_factor = sum(wins) / abs(sum_losses) if sum_losses != 0 else None
    else:
        # No losses recorded: PF is mathematically infinite. Surface as None
        # in the value and as a special "inf" string in the formatter.
        profit_factor = float("inf") if wins else None
    holds = [h for h in (_hold_hours(t[0], t[1]) for t in trades) if h is not None]
    avg_hold = sum(holds) / len(holds) if holds else None
    return SleeveMetrics(
        sleeve=strategy, window=window.name, n_trades=n,
        total_pnl_usdt=total,
        win_rate_pct=(len(wins) / n * 100.0),
        sharpe=annualized_sharpe(pnls_pct),
        max_drawdown_pct=max_drawdown_from_pnl_curve(pnls_usdt, capital_usdt),
        expectancy_usdt=total / n,
        profit_factor=profit_factor,
        avg_hold_hours=avg_hold,
    )


# ─── Top-level report ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class HealthReport:
    """Full health snapshot — portfolio across windows + per-sleeve breakdown."""
    variant_id: str
    capital_usdt: float
    as_of: str                                     # ISO datetime
    windows: list[Window]
    portfolio: list[PortfolioMetrics]              # one per window, same order
    sleeves: dict[str, list[SleeveMetrics]]        # {strategy: [per-window]}


def build_report(variant_id: str, capital_usdt: float | None = None,
                 end_date: date | None = None,
                 source: str = "live_computed") -> HealthReport:
    """Compute portfolio metrics across YTD/90D/30D and per-sleeve metrics
    for every strategy the variant has traded.

    ``source`` controls which ``variant_daily_returns`` rows feed the
    portfolio block. Default ``'live_computed'`` for the live bot;
    ``'replay'`` lets the same tool inspect a backtest variant."""
    if capital_usdt is None:
        from services import trade_db
        capital_usdt = float(trade_db.get_config("paper_account_usdt") or 10000)
    windows = resolve_windows(end_date=end_date)
    portfolio = [portfolio_metrics(variant_id, w, source=source,
                                     capital_usdt=capital_usdt)
                  for w in windows]
    sleeves: dict[str, list[SleeveMetrics]] = {}
    for s in _all_strategies_for_variant(variant_id):
        sleeves[s] = [sleeve_metrics(variant_id, s, w, capital_usdt)
                       for w in windows]
    return HealthReport(
        variant_id=variant_id, capital_usdt=capital_usdt,
        as_of=clock.now_iso(), windows=windows,
        portfolio=portfolio, sleeves=sleeves,
    )


# ─── Formatter ──────────────────────────────────────────────────────────────


def _fmt_pct(v: float | None, places: int = 2, sign: bool = True) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "  n/a "
    fmt = f"{{:+.{places}f}}%" if sign else f"{{:.{places}f}}%"
    return fmt.format(v)


def _fmt_num(v: float | None, places: int = 2, sign: bool = True) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return " n/a "
    fmt = f"{{:+.{places}f}}" if sign else f"{{:.{places}f}}"
    return fmt.format(v)


def format_report(report: HealthReport) -> str:
    """Render as a console-friendly ASCII table block."""
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(f"  P-300 Strategy Health  -  variant {report.variant_id}")
    lines.append(f"  As of {report.as_of}  -  capital ${report.capital_usdt:,.0f}")
    lines.append("=" * 78)
    lines.append("")

    # Portfolio block — for the live variant this is the COMBINED view
    # (Core × W_CORE + tactical P&L). For replay it's whatever the VDR
    # rows hold.
    win_names = [w.name for w in report.windows]
    lines.append(f"  Portfolio  (Core x{W_CORE:.2f}  +  Tactical pnl)")
    header = f"  {'metric':<22} | " + " | ".join(f"{n:>10}" for n in win_names)
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    rows = [
        ("Days in window", [f"{p.n_days:>10d}" for p in report.portfolio]),
        ("Total return",   [f"{_fmt_pct(p.total_return_pct):>10}"
                             for p in report.portfolio]),
        ("Sharpe (ann.)",  [f"{_fmt_num(p.sharpe):>10}"
                             for p in report.portfolio]),
        ("Win rate (days)", [f"{_fmt_pct(p.win_rate_pct, sign=False):>10}"
                              for p in report.portfolio]),
        ("Max drawdown",   [f"{_fmt_pct(p.max_drawdown_pct):>10}"
                             for p in report.portfolio]),
    ]
    for label, cells in rows:
        lines.append(f"  {label:<22} | " + " | ".join(cells))
    lines.append("")

    # Per-sleeve block — shown in the canonical sleeve order even for
    # sleeves that produced 0 trades in the window (so operators can see
    # which sleeves are quiet).
    if report.sleeves:
        lines.append("  Per-sleeve  (closed trades; Sharpe on per-trade %, "
                      "MDD on cumulative pnl)")
        # Preserve the order from build_report; do NOT alphabetize so that
        # tactical comes before JPLUS_*.
        sleeve_order = list(report.sleeves.keys())
        for window in report.windows:
            lines.append("")
            lines.append(f"  -- {window.name}  ({window.start} -> {window.end}) --")
            sub_header = (f"  {'sleeve':<18} | {'N':>3} | {'WR':>7} | "
                           f"{'total $':>10} | {'exp $':>9} | "
                           f"{'PF':>6} | {'avg h':>7} | "
                           f"{'sharpe':>7} | {'MDD':>8}")
            lines.append(sub_header)
            lines.append("  " + "-" * (len(sub_header) - 2))
            for sleeve in sleeve_order:
                metrics = next(m for m in report.sleeves[sleeve]
                                if m.window == window.name)
                if metrics.n_trades == 0:
                    cells = [
                        f"{sleeve:<18}", f"{0:>3d}",
                        f"{'n/a':>7}",
                        f"{'0.00':>10}",
                        f"{'n/a':>9}",
                        f"{'n/a':>6}",
                        f"{'n/a':>7}",
                        f"{'n/a':>7}",
                        f"{'n/a':>8}",
                    ]
                else:
                    cells = [
                        f"{sleeve:<18}",
                        f"{metrics.n_trades:>3d}",
                        f"{_fmt_pct(metrics.win_rate_pct, sign=False):>7}",
                        f"{metrics.total_pnl_usdt:>+10.2f}",
                        f"{_fmt_num_or_signed(metrics.expectancy_usdt):>9}",
                        f"{_fmt_pf(metrics.profit_factor):>6}",
                        f"{_fmt_hold(metrics.avg_hold_hours):>7}",
                        f"{_fmt_num(metrics.sharpe):>7}",
                        f"{_fmt_pct(metrics.max_drawdown_pct):>8}",
                    ]
                lines.append("  " + " | ".join(cells))
    lines.append("")
    lines.append("=" * 78)
    return "\n".join(lines)


def _fmt_num_or_signed(v: float | None, places: int = 2) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v:+.{places}f}"


def _fmt_pf(v: float | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    if math.isinf(v):
        return "inf"
    return f"{v:.2f}"


def _fmt_hold(hours: float | None) -> str:
    if hours is None or (isinstance(hours, float) and math.isnan(hours)):
        return "n/a"
    if hours < 24:
        return f"{hours:.1f}h"
    days = hours / 24.0
    return f"{days:.1f}d"


# ─── CLI ────────────────────────────────────────────────────────────────────

def _main() -> None:
    import argparse
    p = argparse.ArgumentParser(
        description="Print strategy health metrics for a P-300 variant.")
    p.add_argument("--variant", default="p300_aggressive_v2_v1_0",
                   help="Variant ID (default: live variant).")
    p.add_argument("--capital", type=float, default=None,
                   help="Override capital (default: paper_account_usdt config).")
    p.add_argument("--source", default="live_computed",
                   choices=("live_computed", "replay"),
                   help="variant_daily_returns.source filter for the "
                        "portfolio block. Pass 'replay' to inspect a "
                        "backtest variant.")
    args = p.parse_args()
    report = build_report(args.variant, capital_usdt=args.capital,
                           source=args.source)
    print(format_report(report))


if __name__ == "__main__":
    _main()

"""Full-portfolio report with buy-and-hold (BTC) comparison, derived
from the trade ledger.

Produces total P&L, max DD, total trades, profitable trades, profit
factor, CAGR, Sharpe — for one variant — with BTC buy-and-hold side-
by-side.

Reads daily returns straight from ``trades`` via
``strategies.support.strategy_health.trades_daily_returns`` (the canonical
realized-PnL path). Core sub-sleeves and tactical sleeves contribute
to one variant's trades table uniformly, so there is no longer a
"combined" variant — pass --variant whichever one you want to report.

Usage:
  python tools/full_portfolio_report.py \\
      --variant p300_aggressive_v2_v1_0 \\
      --capital 10000
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from strategies.support import db, strategy_health  # noqa: E402


def load_daily_returns(variant_id: str, capital: float
                        ) -> list[tuple[str, float]]:
    """Calendar-complete daily realized returns (percent) for `variant_id`,
    derived from closed trades. Window auto-spans the variant's first
    closing trade to its last."""
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        row = con.execute(
            "SELECT MIN(date(actual_exit_time)) AS lo, "
            "       MAX(date(actual_exit_time)) AS hi "
            "FROM trades WHERE strategy_variant=? AND status='closed'",
            (variant_id,),
        ).fetchone()
    finally:
        con.close()
    if not row or row[0] is None:
        return []
    return strategy_health.trades_daily_returns(
        variant_id, row[0], row[1], capital, zero_fill=True
    )


def load_trades(variant_id: str) -> list[dict]:
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT pnl_usdt, pnl_pct, strategy, direction, "
        "actual_entry_time, actual_exit_time "
        "FROM trades WHERE strategy_variant = ? AND status = 'closed' "
        "AND pnl_usdt IS NOT NULL ORDER BY actual_exit_time",
        (variant_id,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def load_btc_daily_closes(start_iso: str, end_iso: str) -> list[tuple[str, float]]:
    """Daily UTC close from cd_spot_binance (1h bars aggregated to daily)."""
    start_ts = int(datetime.fromisoformat(start_iso).replace(tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.fromisoformat(end_iso).replace(tzinfo=timezone.utc).timestamp()) + 86400
    con = sqlite3.connect(str(db.TRADER_DB))
    rows = con.execute(
        "SELECT timestamp, close FROM cd_spot_binance "
        "WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp",
        (start_ts, end_ts),
    ).fetchall()
    con.close()
    by_day: dict[str, float] = {}
    for ts, close in rows:
        d = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        by_day[d] = float(close)
    return sorted(by_day.items())


def accumulate_equity(capital: float,
                       rets_pct: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Build the equity curve by **summing** daily PnL on fixed capital.

    Each ``r`` is realized-PnL-as-%-of-starting-capital; the dollar PnL on
    day d is ``capital * r / 100``, and equity is the running sum. The
    pre-2026-05-13 implementation compounded these (``eq *= (1 + r/100)``)
    which applied Jensen's gap to non-compounding returns — over-reporting
    total return and shrinking MDD. See AUDIT_2026_05_13 and the matching
    helpers in ``strategies.support.strategy_health``."""
    out = []
    eq = capital
    for d, r in rets_pct:
        eq += capital * r / 100.0
        out.append((d, eq))
    return out


# Back-compat alias — the old name reads as a misnomer now but external
# callers may still reference it. Use ``accumulate_equity`` in new code.
compound_equity = accumulate_equity


def equity_metrics(capital: float,
                    daily_rets_pct: list[tuple[str, float]]) -> dict:
    """All portfolio-level metrics given a daily-return series.

    Returns are arithmetic-on-fixed-capital (``trades_daily_returns``-style):
    total return is the *sum* of daily returns, equity grows additively
    by ``capital * r / 100``, MDD walks that additive equity. CAGR is
    still geometric by definition — it asks "what constant compound rate
    would have produced this final equity?" — but it's computed from the
    additively-built final equity, so it's the answer to the right
    question."""
    if not daily_rets_pct:
        return {}
    eq_curve = accumulate_equity(capital, daily_rets_pct)
    final_eq = eq_curve[-1][1]
    total_pnl = final_eq - capital
    total_ret_pct = (final_eq / capital - 1) * 100
    n_days = len(daily_rets_pct)
    years = n_days / 365.25
    cagr = (final_eq / capital) ** (1 / years) - 1 if years > 0 and final_eq > 0 else float("nan")

    rets = [r / 100.0 for _, r in daily_rets_pct]
    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        sd = math.sqrt(var)
        sharpe = (mean / sd) * math.sqrt(365) if sd > 0 else float("nan")
    else:
        sharpe = float("nan")

    peak = capital
    mdd = 0.0
    for _, eq in eq_curve:
        peak = max(peak, eq)
        dd = (eq / peak - 1) if peak > 0 else 0
        mdd = min(mdd, dd)

    return {
        "final_equity": final_eq,
        "total_pnl": total_pnl,
        "total_return_pct": total_ret_pct,
        "cagr_pct": cagr * 100 if not math.isnan(cagr) else float("nan"),
        "sharpe_daily_ann": sharpe,
        "mdd_pct": mdd * 100,
        "n_days": n_days,
        "first_day": daily_rets_pct[0][0],
        "last_day": daily_rets_pct[-1][0],
    }


def trade_metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"total_trades": 0, "profitable_trades": 0,
                "win_rate_pct": 0.0, "profit_factor": float("nan"),
                "gross_wins_usdt": 0.0, "gross_losses_usdt": 0.0}
    wins = [float(t["pnl_usdt"]) for t in trades if float(t["pnl_usdt"]) > 0]
    losses = [float(t["pnl_usdt"]) for t in trades if float(t["pnl_usdt"]) <= 0]
    gross_wins = sum(wins)
    gross_losses = abs(sum(losses))
    pf = (gross_wins / gross_losses) if gross_losses > 0 else float("inf")
    return {
        "total_trades": len(trades),
        "profitable_trades": len(wins),
        "win_rate_pct": len(wins) / len(trades) * 100,
        "profit_factor": pf,
        "gross_wins_usdt": gross_wins,
        "gross_losses_usdt": -sum(losses),  # negative — losses sum
    }


def buyhold_metrics(capital: float,
                     btc_daily: list[tuple[str, float]]) -> dict:
    """Buy capital worth of BTC at first close, mark daily, sell at last close."""
    if len(btc_daily) < 2:
        return {}
    start_px = btc_daily[0][1]
    btc_qty = capital / start_px
    rets = []
    prev_eq = capital
    for d, px in btc_daily:
        eq = btc_qty * px
        ret_pct = (eq / prev_eq - 1) * 100 if prev_eq > 0 else 0
        rets.append((d, ret_pct))
        prev_eq = eq
    return equity_metrics(capital, rets)


def fmt_pct(v: float, signed: bool = True) -> str:
    if v != v:  # nan check
        return "      n/a"
    return f"{v:+.2f}%" if signed else f"{v:.2f}%"


def fmt_money(v: float) -> str:
    if v != v:
        return "       n/a"
    return f"${v:>14,.2f}"


def print_side_by_side(port: dict, port_trades: dict, bh: dict,
                        capital: float, label_port: str,
                        label_bh: str = "BUY & HOLD (BTC)") -> None:
    print()
    print("=" * 80)
    print(f"  Full-portfolio replay vs Buy & Hold")
    print(f"  Window: {port.get('first_day','-')} -> {port.get('last_day','-')} "
          f"({port.get('n_days', 0)} days)")
    print(f"  Starting capital: ${capital:,.2f}")
    print("=" * 80)
    w = 26
    print(f"  {'':<{w}} {label_port:>20}  {label_bh:>20}")
    print(f"  {'-'*w} {'-'*20}  {'-'*20}")
    print(f"  {'Final equity':<{w}} {fmt_money(port.get('final_equity', float('nan'))):>20}  "
          f"{fmt_money(bh.get('final_equity', float('nan'))):>20}")
    print(f"  {'Total P&L':<{w}} {fmt_money(port.get('total_pnl', float('nan'))):>20}  "
          f"{fmt_money(bh.get('total_pnl', float('nan'))):>20}")
    print(f"  {'Total return':<{w}} {fmt_pct(port.get('total_return_pct', float('nan'))):>20}  "
          f"{fmt_pct(bh.get('total_return_pct', float('nan'))):>20}")
    print(f"  {'CAGR':<{w}} {fmt_pct(port.get('cagr_pct', float('nan'))):>20}  "
          f"{fmt_pct(bh.get('cagr_pct', float('nan'))):>20}")
    print(f"  {'Max drawdown':<{w}} {fmt_pct(port.get('mdd_pct', float('nan'))):>20}  "
          f"{fmt_pct(bh.get('mdd_pct', float('nan'))):>20}")
    sharpe_p = port.get('sharpe_daily_ann', float('nan'))
    sharpe_b = bh.get('sharpe_daily_ann', float('nan'))
    print(f"  {'Sharpe (daily ann)':<{w}} {sharpe_p:>20.2f}  {sharpe_b:>20.2f}")
    print(f"  {'-'*w} {'-'*20}  {'-'*20}")
    print(f"  {'Total trades':<{w}} {port_trades.get('total_trades', 0):>20}  "
          f"{'n/a':>20}")
    pft = port_trades.get('profitable_trades', 0)
    n = port_trades.get('total_trades', 0)
    win_rate = port_trades.get('win_rate_pct', 0.0)
    print(f"  {'Profitable trades':<{w}} {f'{pft} ({win_rate:.1f}%)':>20}  "
          f"{'n/a':>20}")
    pf = port_trades.get('profit_factor', float('nan'))
    pf_str = "inf" if pf == float('inf') else f"{pf:.2f}"
    print(f"  {'Profit factor':<{w}} {pf_str:>20}  {'n/a':>20}")
    print(f"  {'Gross wins':<{w}} "
          f"{fmt_money(port_trades.get('gross_wins_usdt', 0.0)):>20}  {'n/a':>20}")
    print(f"  {'Gross losses':<{w}} "
          f"{fmt_money(-port_trades.get('gross_losses_usdt', 0.0)):>20}  {'n/a':>20}")
    print("=" * 80)
    print(f"  Outperformance vs B&H: "
          f"{(port.get('total_return_pct', 0) - bh.get('total_return_pct', 0)):+.2f}%  "
          f"(CAGR: {(port.get('cagr_pct', 0) - bh.get('cagr_pct', 0)):+.2f}%)")
    print("=" * 80)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", required=True,
                    help="Variant id whose trades to report.")
    ap.add_argument("--capital", type=float, default=10000.0,
                    help="Starting capital (default 10000).")
    args = ap.parse_args()

    daily = load_daily_returns(args.variant, args.capital)
    if not daily:
        raise SystemExit(
            f"No closed trades for variant {args.variant!r} — nothing to report."
        )
    port_metrics = equity_metrics(args.capital, daily)

    trades = load_trades(args.variant)
    tmetrics = trade_metrics(trades)

    btc = load_btc_daily_closes(daily[0][0], daily[-1][0])
    bh = buyhold_metrics(args.capital, btc)

    print_side_by_side(port_metrics, tmetrics, bh, args.capital,
                       label_port="PORTFOLIO")
    return 0


if __name__ == "__main__":
    sys.exit(main())

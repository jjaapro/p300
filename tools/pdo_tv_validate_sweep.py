"""Parameter sweep + diagnostics for the PDO_RETOUCH TV-fidelity probe.

Investigates the 121-vs-172 trade-count gap between our Python replication
(tools/pdo_tv_validate.py) and the user's TradingView run.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TRADER_DB = REPO / "data" / "trader.db"

HOLD_BARS = 24
COMMISSION_RT = 0.001

START_UTC = "2020-01-01T00:00:00+00:00"
END_UTC   = "2026-05-04T00:00:00+00:00"


def _load_1h_bars(start_ms: int, end_ms: int) -> list[dict]:
    con = sqlite3.connect(str(TRADER_DB))
    rows = con.execute(
        "SELECT open_time, open, high, low, close FROM btc_1m "
        "WHERE open_time >= ? AND open_time < ? ORDER BY open_time",
        (start_ms, end_ms),
    ).fetchall()
    con.close()
    bars: dict[int, dict] = {}
    for ot, o, h, l, c in rows:
        hour_start = (ot // 3_600_000) * 3_600_000
        b = bars.get(hour_start)
        if b is None:
            bars[hour_start] = {"hour_ms": hour_start, "open": o, "high": h,
                                "low": l, "close": c, "first_minute": ot}
        else:
            if ot < b["first_minute"]:
                b["open"] = o
                b["first_minute"] = ot
            if h > b["high"]:
                b["high"] = h
            if l < b["low"]:
                b["low"] = l
            b["close"] = c
    return sorted(bars.values(), key=lambda x: x["hour_ms"])


def _day_str(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _max_drawdown(equity: list[float]) -> float:
    peak = equity[0]
    mdd = 0.0
    for e in equity:
        if e > peak:
            peak = e
        dd = (peak - e) / peak
        if dd > mdd:
            mdd = dd
    return mdd


def backtest(bars: list[dict], daily_opens: dict[str, float],
             gap_pct: float, touch_tol: float, hold_bars: int) -> dict:
    pdo: float | None = None
    cdo: float | None = None
    cur_day: str | None = None
    tried_today = False
    pos_entry: float | None = None
    pos_entry_bar: int | None = None
    pos_entry_day: str | None = None

    equity = 1.0
    equity_curve = [1.0]
    trades: list[dict] = []
    setup_days = set()
    touch_days = set()

    for i, b in enumerate(bars):
        bday = _day_str(b["hour_ms"])
        new_day = bday != cur_day
        if new_day:
            pdo = cdo
            cdo = daily_opens.get(bday)
            cur_day = bday
            tried_today = False

        if pos_entry is not None:
            bars_held = i - pos_entry_bar
            close_reason = None
            if bars_held >= hold_bars:
                close_reason = "HoldLimit"
            elif new_day:
                close_reason = "DayEnd"
            if close_reason is not None:
                gross_ret = (b["close"] - pos_entry) / pos_entry
                net_ret = gross_ret - COMMISSION_RT
                equity *= (1 + net_ret)
                trades.append({"reason": close_reason, "net_ret": net_ret,
                               "entry_day": pos_entry_day, "exit_day": bday,
                               "bars_held": bars_held})
                pos_entry = None
                pos_entry_bar = None
                pos_entry_day = None

        equity_curve.append(equity)

        if pos_entry is not None or tried_today or pdo is None or cdo is None:
            continue
        if pdo <= 0:
            continue
        gap = (cdo - pdo) / pdo * 100
        if gap < gap_pct:
            continue
        setup_days.add(bday)
        pdo_hi = pdo * (1 + touch_tol)
        pdo_lo = pdo * (1 - touch_tol)
        if b["low"] <= pdo_hi and b["high"] >= pdo_lo:
            touch_days.add(bday)
            pos_entry = b["close"]
            pos_entry_bar = i
            pos_entry_day = bday
            tried_today = True

    n = len(trades)
    wins = [t for t in trades if t["net_ret"] > 0]
    losses = [t for t in trades if t["net_ret"] <= 0]
    gross_wins = sum(t["net_ret"] for t in wins)
    gross_losses = -sum(t["net_ret"] for t in losses)
    pf = gross_wins / gross_losses if gross_losses > 0 else float("inf")
    return {
        "trades": n,
        "setup_days": len(setup_days),
        "touch_days": len(touch_days),
        "wins": len(wins),
        "win_rate": len(wins) / n if n else 0.0,
        "total_ret": equity - 1.0,
        "mdd": _max_drawdown(equity_curve),
        "pf": pf,
        "avg_trade": sum(t["net_ret"] for t in trades) / n if n else 0.0,
    }


def run() -> None:
    start_ms = int(datetime.fromisoformat(START_UTC).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(END_UTC).timestamp() * 1000)
    bars = _load_1h_bars(start_ms, end_ms)
    daily_opens: dict[str, float] = {}
    for b in bars:
        d = _day_str(b["hour_ms"])
        if d not in daily_opens:
            daily_opens[d] = b["open"]
    print(f"Loaded {len(bars)} 1H bars, {len(daily_opens)} days")
    print()

    # Gap-threshold sweep at default tol=0.10%, hold=24
    print("=== Gap-threshold sweep (tol=0.10%, hold=24) ===")
    print(f"{'gap%':>6}  {'setup':>5}  {'touch':>5}  {'trades':>6}  {'win%':>5}  {'pnl%':>7}  {'mdd%':>6}  {'PF':>5}")
    for gap_pct in (1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5):
        r = backtest(bars, daily_opens, gap_pct, 0.001, 24)
        print(f"{gap_pct:>6.2f}  {r['setup_days']:>5}  {r['touch_days']:>5}  "
              f"{r['trades']:>6}  {r['win_rate']*100:>4.1f}  "
              f"{r['total_ret']*100:>+7.2f}  {r['mdd']*100:>6.2f}  {r['pf']:>5.3f}")
    print()

    # Touch-tolerance sweep at gap=2.0, hold=24
    print("=== Touch-tolerance sweep (gap=2.0%, hold=24) ===")
    print(f"{'tol%':>6}  {'setup':>5}  {'touch':>5}  {'trades':>6}  {'win%':>5}  {'pnl%':>7}  {'mdd%':>6}  {'PF':>5}")
    for tol_pct in (0.05, 0.10, 0.15, 0.20, 0.25, 0.50, 1.0):
        r = backtest(bars, daily_opens, 2.0, tol_pct/100, 24)
        print(f"{tol_pct:>6.2f}  {r['setup_days']:>5}  {r['touch_days']:>5}  "
              f"{r['trades']:>6}  {r['win_rate']*100:>4.1f}  "
              f"{r['total_ret']*100:>+7.2f}  {r['mdd']*100:>6.2f}  {r['pf']:>5.3f}")
    print()

    # Hold-bars sweep at gap=2.0, tol=0.10
    print("=== Hold-bars sweep (gap=2.0%, tol=0.10%) ===")
    print(f"{'hold':>6}  {'trades':>6}  {'win%':>5}  {'pnl%':>7}  {'mdd%':>6}  {'PF':>5}")
    for hold in (1, 4, 8, 12, 24, 48):
        r = backtest(bars, daily_opens, 2.0, 0.001, hold)
        print(f"{hold:>6}  {r['trades']:>6}  {r['win_rate']*100:>4.1f}  "
              f"{r['total_ret']*100:>+7.2f}  {r['mdd']*100:>6.2f}  {r['pf']:>5.3f}")
    print()

    print("=== TV reference (user, filter OFF, BTC 1H, 2020-01-01..2026-05-03) ===")
    print("Trades: 172  |  Wins: 47 (27.33%)  |  P&L: +24.05%  |  MDD: 17.12%  |  PF: 1.551")


if __name__ == "__main__":
    run()

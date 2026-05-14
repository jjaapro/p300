"""PDO_RETOUCH BTC TradingView fidelity probe.

Replicates strategies/sleeves/pdo/signal.pine for BTCUSDT 1H over a user-
specified date range. No regime filter (matches user TV run 2026-05-11).

Pine semantics replicated:
  - PDO = open of first 1m bar of prior UTC day
  - CDO = open of first 1m bar of bar_day
  - gap_pct = (CDO - PDO) / PDO * 100, require >= 2.0
  - 1H bars: low <= PDO*(1+tol) and high >= PDO*(1-tol) -> touch
  - Entry at close of touching 1H bar; one entry per day; first touch wins
  - Exit: barsHeld >= 24 (HoldLimit) takes priority over newDay (DayEnd)
  - Commission 0.05% per side (10 bp RT), slippage negligible (2 ticks BTC)
  - 100% equity each trade, compounded
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TRADER_DB = REPO / "data" / "trader.db"

GAP_PCT = 2.0
TOUCH_TOL = 0.001  # 0.10%
HOLD_BARS = 24
COMMISSION_RT = 0.001  # 0.05% * 2

START_UTC = "2020-01-01T00:00:00+00:00"
END_UTC   = "2026-05-04T00:00:00+00:00"  # inclusive through 2026-05-03


def _load_1h_bars(start_ms: int, end_ms: int) -> list[dict]:
    """Aggregate btc_1m to 1H bars [H, H+1) UTC. Skip any hour with no rows."""
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
            b["close"] = c  # last 1m wins (ordered by open_time)
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


def run() -> None:
    start_ms = int(datetime.fromisoformat(START_UTC).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(END_UTC).timestamp() * 1000)
    bars = _load_1h_bars(start_ms, end_ms)
    print(f"Loaded {len(bars)} 1H bars "
          f"({_day_str(bars[0]['hour_ms'])} -> {_day_str(bars[-1]['hour_ms'])})")

    # Daily opens table — first 1m bar's open per UTC day.
    daily_opens: dict[str, float] = {}
    for b in bars:
        d = _day_str(b["hour_ms"])
        if d not in daily_opens:
            daily_opens[d] = b["open"]

    # State
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

    for i, b in enumerate(bars):
        bday = _day_str(b["hour_ms"])
        new_day = bday != cur_day
        if new_day:
            pdo = cdo
            cdo = daily_opens.get(bday)
            cur_day = bday
            tried_today = False

        # Exit logic — runs on every bar after entry
        if pos_entry is not None:
            bars_held = i - pos_entry_bar
            close_reason = None
            if bars_held >= HOLD_BARS:
                close_reason = "HoldLimit"
            elif new_day:
                close_reason = "DayEnd"
            if close_reason is not None:
                gross_ret = (b["close"] - pos_entry) / pos_entry
                net_ret = gross_ret - COMMISSION_RT
                equity *= (1 + net_ret)
                trades.append({
                    "entry_day": pos_entry_day,
                    "exit_day": bday,
                    "entry_price": pos_entry,
                    "exit_price": b["close"],
                    "bars_held": bars_held,
                    "gross_ret": gross_ret,
                    "net_ret": net_ret,
                    "reason": close_reason,
                })
                pos_entry = None
                pos_entry_bar = None
                pos_entry_day = None

        equity_curve.append(equity)

        # Entry logic — only if flat and not yet tried today
        if pos_entry is not None or tried_today or pdo is None or cdo is None:
            continue
        if pdo <= 0:
            continue
        gap = (cdo - pdo) / pdo * 100
        if gap < GAP_PCT:
            continue
        pdo_hi = pdo * (1 + TOUCH_TOL)
        pdo_lo = pdo * (1 - TOUCH_TOL)
        if b["low"] <= pdo_hi and b["high"] >= pdo_lo:
            pos_entry = b["close"]
            pos_entry_bar = i
            pos_entry_day = bday
            tried_today = True

    # Stats
    n = len(trades)
    wins = [t for t in trades if t["net_ret"] > 0]
    losses = [t for t in trades if t["net_ret"] <= 0]
    win_rate = len(wins) / n if n else 0.0
    gross_wins = sum(t["net_ret"] for t in wins)
    gross_losses = -sum(t["net_ret"] for t in losses)
    pf = gross_wins / gross_losses if gross_losses > 0 else float("inf")
    total_ret = equity - 1.0
    mdd = _max_drawdown(equity_curve)
    avg_trade = sum(t["net_ret"] for t in trades) / n if n else 0.0

    print()
    print("=== PDO_RETOUCH Python backtest (filter OFF, BTC 1H) ===")
    print(f"Range:           {_day_str(bars[0]['hour_ms'])} -> {_day_str(bars[-1]['hour_ms'])}")
    print(f"Params:          gap>={GAP_PCT}%, tol={TOUCH_TOL*100}%, hold={HOLD_BARS}h, commRT={COMMISSION_RT*100}bp")
    print(f"Trades:          {n}")
    print(f"Wins / Losses:   {len(wins)} / {len(losses)}  (win rate {win_rate*100:.2f}%)")
    print(f"Total P&L:       {total_ret*100:+.2f}%")
    print(f"Max DD:          {mdd*100:.2f}%")
    print(f"Profit factor:   {pf:.3f}")
    print(f"Avg trade:       {avg_trade*100:+.4f}%")
    print()

    # Exit reason breakdown
    from collections import Counter
    rcount = Counter(t["reason"] for t in trades)
    print(f"Exit reasons:    {dict(rcount)}")

    print()
    print("=== TV reference (user, 2026-05-11) ===")
    print("Trades: 172  |  Profitable: 47  (win rate 27.33%)")
    print("Total P&L: +24.05%  |  Max DD: 17.12%  |  Profit factor: 1.551")


if __name__ == "__main__":
    run()

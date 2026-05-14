"""Dump per-trade entry/exit details for comparison against TV's List of Trades.

Mirrors tools/pdo_tv_validate.py params: gap=2.0%, tol=0.10%, hold=24, filter OFF.
Writes data/pdo_tv_validate_trades.csv with columns:
  trade_id, entry_dt_utc, entry_price, exit_dt_utc, exit_price, bars_held,
  gross_ret_pct, net_ret_pct, exit_reason
"""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TRADER_DB = REPO / "data" / "trader.db"
OUT_CSV = REPO / "data" / "pdo_tv_validate_trades.csv"

GAP_PCT = 2.0
TOUCH_TOL = 0.001
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


def _dt_str(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _day_str(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def run() -> None:
    start_ms = int(datetime.fromisoformat(START_UTC).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(END_UTC).timestamp() * 1000)
    bars = _load_1h_bars(start_ms, end_ms)
    daily_opens: dict[str, float] = {}
    for b in bars:
        d = _day_str(b["hour_ms"])
        if d not in daily_opens:
            daily_opens[d] = b["open"]

    pdo: float | None = None
    cdo: float | None = None
    cur_day: str | None = None
    tried_today = False
    pos_entry: float | None = None
    pos_entry_bar: int | None = None
    pos_entry_ts_ms: int | None = None
    trades: list[dict] = []

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
            if bars_held >= HOLD_BARS:
                close_reason = "HoldLimit"
            elif new_day:
                close_reason = "DayEnd"
            if close_reason is not None:
                gross_ret = (b["close"] - pos_entry) / pos_entry
                net_ret = gross_ret - COMMISSION_RT
                trades.append({
                    "entry_ts": pos_entry_ts_ms,
                    "entry_price": pos_entry,
                    "exit_ts": b["hour_ms"] + 3_600_000,  # exit at bar close = hour_ms + 1h
                    "exit_price": b["close"],
                    "bars_held": bars_held,
                    "gross_ret": gross_ret,
                    "net_ret": net_ret,
                    "reason": close_reason,
                })
                pos_entry = None
                pos_entry_bar = None
                pos_entry_ts_ms = None

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
            # entry processes at bar close = hour_ms + 1h
            pos_entry_ts_ms = b["hour_ms"] + 3_600_000
            tried_today = True

    with OUT_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["#", "entry_dt_utc", "entry_price", "exit_dt_utc",
                    "exit_price", "bars_held", "gross_ret_pct",
                    "net_ret_pct", "exit_reason"])
        for idx, t in enumerate(trades, 1):
            w.writerow([idx, _dt_str(t["entry_ts"]), f"{t['entry_price']:.2f}",
                        _dt_str(t["exit_ts"]), f"{t['exit_price']:.2f}",
                        t["bars_held"], f"{t['gross_ret']*100:+.3f}",
                        f"{t['net_ret']*100:+.3f}", t["reason"]])

    print(f"Wrote {len(trades)} trades to {OUT_CSV}")
    print()
    print("=== First 15 trades ===")
    print(f"{'#':>3}  {'entry_dt_utc':<16}  {'entry':>9}  {'exit_dt_utc':<16}  "
          f"{'exit':>9}  {'gross%':>7}  {'net%':>7}  {'reason':<10}")
    for idx, t in enumerate(trades[:15], 1):
        print(f"{idx:>3}  {_dt_str(t['entry_ts']):<16}  {t['entry_price']:>9.2f}  "
              f"{_dt_str(t['exit_ts']):<16}  {t['exit_price']:>9.2f}  "
              f"{t['gross_ret']*100:>+7.3f}  {t['net_ret']*100:>+7.3f}  {t['reason']:<10}")
    print()
    print("=== Last 5 trades ===")
    for idx, t in enumerate(trades[-5:], len(trades) - 4):
        print(f"{idx:>3}  {_dt_str(t['entry_ts']):<16}  {t['entry_price']:>9.2f}  "
              f"{_dt_str(t['exit_ts']):<16}  {t['exit_price']:>9.2f}  "
              f"{t['gross_ret']*100:>+7.3f}  {t['net_ret']*100:>+7.3f}  {t['reason']:<10}")


if __name__ == "__main__":
    run()

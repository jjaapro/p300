"""Parse TradingView's exported PDO_RETOUCH trade list and reconcile against
our Python backtest.

Hypothesis: TV's 172-trade count is actually 86 real entries, each split into a
tiny "Margin call" slice + a full "DayEnd" slice. Verify by:
  1. Counting unique entry timestamps
  2. Separating Margin-call slices from DayEnd slices
  3. Recomputing stats on DayEnd slices only
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

CSV_PATH = Path(r"C:\Users\TJ5\Downloads\PDO-L-RF_BINANCE_BTCUSDT_2026-05-11_fecc0.csv")
# Fallback to data/ if Downloads path doesn't exist
if not CSV_PATH.exists():
    CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "PDO-L-RF_BINANCE_BTCUSDT_2026-05-11_fecc0.csv"


def parse_csv(path: Path) -> list[dict]:
    """Each TV trade has 2 rows: an Entry row and an Exit row. Return one
    consolidated dict per trade # with both legs joined."""
    rows: dict[str, dict] = defaultdict(dict)
    with path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            tn = r["Trade #"]
            t = r["Type"]
            if t.startswith("Entry"):
                rows[tn]["entry_dt"] = r["Date and time"]
                rows[tn]["entry_price"] = float(r["Price USDT"])
                rows[tn]["entry_signal"] = r["Signal"]
                rows[tn]["size_qty"] = float(r["Size (qty)"])
                rows[tn]["size_value"] = float(r["Size (value)"])
            else:  # Exit
                rows[tn]["exit_dt"] = r["Date and time"]
                rows[tn]["exit_price"] = float(r["Price USDT"])
                rows[tn]["exit_signal"] = r["Signal"]
                rows[tn]["net_pnl_usdt"] = float(r["Net P&L USDT"])
                rows[tn]["net_pnl_pct"] = float(r["Net P&L %"])
            rows[tn]["trade_num"] = int(tn)
    return sorted(rows.values(), key=lambda x: x["trade_num"])


def main() -> None:
    trades = parse_csv(CSV_PATH)
    print(f"Loaded {len(trades)} TV trades from {CSV_PATH.name}")
    print()

    # Group by entry timestamp
    by_entry_ts: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_entry_ts[t["entry_dt"]].append(t)

    unique_entries = len(by_entry_ts)
    pair_counts = [len(v) for v in by_entry_ts.values()]
    pair_dist = {n: pair_counts.count(n) for n in set(pair_counts)}
    print(f"Unique entry timestamps:   {unique_entries}")
    print(f"Trades per entry ts dist:  {pair_dist}")
    print()

    # Split by exit signal
    margin_calls = [t for t in trades if t["exit_signal"] == "Margin call"]
    dayends = [t for t in trades if t["exit_signal"] == "DayEnd"]
    hold_limits = [t for t in trades if t["exit_signal"] == "HoldLimit"]
    others = [t for t in trades if t["exit_signal"] not in ("Margin call", "DayEnd", "HoldLimit")]
    print(f"Exit signal breakdown:")
    print(f"  Margin call: {len(margin_calls)}  (avg net pnl: "
          f"{sum(t['net_pnl_pct'] for t in margin_calls)/len(margin_calls):+.3f}%)")
    print(f"  DayEnd:      {len(dayends)}  (avg net pnl: "
          f"{sum(t['net_pnl_pct'] for t in dayends)/len(dayends):+.3f}%)")
    print(f"  HoldLimit:   {len(hold_limits)}" +
          (f"  (avg net pnl: {sum(t['net_pnl_pct'] for t in hold_limits)/len(hold_limits):+.3f}%)"
           if hold_limits else ""))
    if others:
        print(f"  Other:       {len(others)}  (signals: {set(t['exit_signal'] for t in others)})")
    print()

    # Verify margin-call size ratio
    if margin_calls:
        mc_sizes = [t["size_value"] for t in margin_calls]
        de_sizes = [t["size_value"] for t in dayends]
        print(f"Margin-call slice sizes:  min={min(mc_sizes):.2f}, "
              f"max={max(mc_sizes):.2f}, mean={sum(mc_sizes)/len(mc_sizes):.2f} USDT")
        print(f"DayEnd slice sizes:       min={min(de_sizes):.2f}, "
              f"max={max(de_sizes):.2f}, mean={sum(de_sizes)/len(de_sizes):.2f} USDT")
        # Are they paired by entry ts?
        paired = sum(1 for ts, lst in by_entry_ts.items()
                     if any(t["exit_signal"] == "Margin call" for t in lst)
                     and any(t["exit_signal"] == "DayEnd" for t in lst))
        print(f"Entries with BOTH margin-call AND DayEnd slices: {paired} / {unique_entries}")
        print()

    # Recompute win rate using DayEnd trades only (the "real" trades)
    real_trades = dayends + hold_limits
    real_wins = [t for t in real_trades if t["net_pnl_pct"] > 0]
    real_losses = [t for t in real_trades if t["net_pnl_pct"] <= 0]
    print("=== TV stats reconciled (DayEnd + HoldLimit only) ===")
    print(f"Real trades:     {len(real_trades)}")
    print(f"Real wins:       {len(real_wins)}  ({len(real_wins)/len(real_trades)*100:.2f}%)")
    print(f"Real losses:     {len(real_losses)}")

    # Profit factor on real trades (using net_pnl_pct as proxy — uniform sizing assumption)
    gross_win = sum(t["net_pnl_pct"] for t in real_wins)
    gross_loss = -sum(t["net_pnl_pct"] for t in real_losses)
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    print(f"PF (sum-of-pct): {pf:.3f}")

    # Approximate compounded return using net_pnl_pct as per-trade compound
    eq = 1.0
    for t in real_trades:
        eq *= (1 + t["net_pnl_pct"] / 100)
    print(f"Compounded:      {(eq - 1)*100:+.2f}%")

    print()
    print("=== TV summary stats (what user reported) ===")
    print("Total trades 172, profitable 47 (27.33%), P&L +24.05%, MDD 17.12%, PF 1.551")
    print()
    print("=== My Python backtest (filter OFF, gap 2.0%, tol 0.10%) ===")
    print("Trades 121, wins 66 (54.55%), P&L +62.19%, MDD 11.42%, PF 1.628")
    print()

    # Now compare entry timestamps to find which my trades TV missed
    print("=== Entry timestamp comparison ===")
    py_csv = Path(__file__).resolve().parents[2] / "data" / "pdo_tv_validate_trades.csv"
    py_entries: set[str] = set()
    with py_csv.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            py_entries.add(r["entry_dt_utc"])
    tv_entries: set[str] = set(by_entry_ts.keys())
    py_only = sorted(py_entries - tv_entries)
    tv_only = sorted(tv_entries - py_entries)
    common = py_entries & tv_entries
    print(f"Common entries (same timestamp): {len(common)}")
    print(f"Python-only (TV missed):         {len(py_only)}")
    print(f"TV-only (Python missed):         {len(tv_only)}")
    print()
    if py_only:
        print(f"First 5 Python-only entries (TV missed these):")
        for ts in py_only[:5]:
            print(f"  {ts}")
    if tv_only:
        print(f"\nFirst 5 TV-only entries (Python missed these):")
        for ts in tv_only[:5]:
            print(f"  {ts}")


if __name__ == "__main__":
    main()

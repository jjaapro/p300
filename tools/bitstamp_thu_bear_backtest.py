"""S-096 Thu Bear V4 backtest on Bitstamp BTCUSD 1H — Pine parity validator.

Replays strategies/sleeves/thu_bear/signal.pine against Bitstamp's official OHLC feed
(same data TradingView's BITSTAMP:BTCUSD chart shows). Compares its trade
list and equity curve against TV's strategy-tester export to verify that
Pine and the Python port produce identical results — bar-for-bar.

This script is a Pine port, NOT a wrapper around strategies/sleeves/thu_bear/signal.py.
The live service uses the FRED-driven `scheduled_events` calendar and exits
at 23:00 UTC; the Pine script uses dom-based CPI/NFP/OPEX approximation and
exits at Fri 00:00 UTC. To validate Pine parity we must replicate Pine.

Pine spec (mirrored exactly here):
  - Entry  : Thursday 00:00 UTC, dom in {1..7} (NFP) or {9..13} (CPI),
             AND Wednesday's daily close < EMA(50) on daily timeframe
             (request.security with lookahead_off semantics).
  - Exit   : Friday 00:00 UTC at that bar's close.
  - SL     : 5% hard stop from entry (intra-bar, fills at SL price).
  - Sizing : 100% of equity, compounded.
  - Costs  : 0.05% commission per leg (10bp RT).
  - Fills  : process_orders_on_close=true -> entry/exit at the signal bar's
             close (Pine convention).

Usage:
    python bitstamp_thu_bear_backtest.py
    python bitstamp_thu_bear_backtest.py --start 2024-01-01 --end 2026-05-04
    python bitstamp_thu_bear_backtest.py --refresh-1h
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Pine inputs (mirrored verbatim from strategies/sleeves/thu_bear/signal.pine)
REGIME_EMA_LEN = 50
SL_PCT = 5.0
ENTRY_HOUR_UTC = 0
EXIT_HOUR_UTC = 0  # Friday 00:00 UTC
COMMISSION_PCT_PER_LEG = 0.05  # 5bp each side


# ─── Bitstamp API + caches ───────────────────────────────────────────────────

API_URL = "https://www.bitstamp.net/api/v2/ohlc/{pair}/"


def _cache_path_1h(symbol: str) -> Path:
    return REPO / "data" / f"bitstamp_{symbol}_1h.json"


def _cache_path_daily(symbol: str) -> Path:
    return REPO / "data" / f"bitstamp_{symbol}_daily.json"


def _api_fetch(symbol: str, step: int, start_ts: int, end_ts: int) -> list[dict]:
    """Paginate Bitstamp's OHLC API across [start_ts, end_ts). 1000 bars/call."""
    bars: list[dict] = []
    cur = start_ts
    page_n = 0
    while cur < end_ts:
        chunk_end = min(cur + 1000 * step, end_ts)
        url = (f"{API_URL.format(pair=symbol)}"
               f"?step={step}&limit=1000&start={cur}&end={chunk_end}")
        req = urllib.request.Request(url, headers={"User-Agent": "p300-bot/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.load(r)
        except Exception as e:
            print(f"    [page {page_n}] fetch error at cur={cur}: {e!r} — retrying once")
            time.sleep(1.0)
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.load(r)
        page = data["data"]["ohlc"]
        page_n += 1
        if not page:
            print(f"    [page {page_n}] empty, advancing to {chunk_end}")
            cur = chunk_end + step
            continue
        for b in page:
            ts = int(b["timestamp"])
            bars.append({
                "ts": ts,
                "open":  float(b["open"]),
                "high":  float(b["high"]),
                "low":   float(b["low"]),
                "close": float(b["close"]),
            })
        last_ts = int(page[-1]["timestamp"])
        last_dt = datetime.fromtimestamp(last_ts, timezone.utc)
        print(f"    [page {page_n}] {len(page)} bars, last={last_dt:%Y-%m-%d %H:%M}")
        new_cur = last_ts + step
        # Bitstamp returns the most recent bars (ignoring `start`) when the
        # requested window extends past "now". If `cur` doesn't advance, the
        # API has nothing newer — break to avoid an infinite loop.
        if new_cur <= cur:
            print(f"    [page {page_n}] cur did not advance "
                  f"({cur} -> {new_cur}); end of available data, stopping.")
            break
        cur = new_cur
        time.sleep(0.2)
    by_ts = {b["ts"]: b for b in bars}
    return [by_ts[t] for t in sorted(by_ts)]


def load_1h(symbol: str, start_iso: str, end_iso: str,
            refresh: bool = False) -> list[dict]:
    cache = _cache_path_1h(symbol)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cached: list[dict] = json.loads(cache.read_text()) if (cache.exists() and not refresh) else []
    want_start = int(datetime.fromisoformat(start_iso).replace(tzinfo=timezone.utc).timestamp())
    want_end = int(datetime.fromisoformat(end_iso).replace(tzinfo=timezone.utc).timestamp()) + 86400
    if not cached:
        print(f"  Fetching 1H {symbol} {start_iso} -> {end_iso} (no cache)…")
        bars = _api_fetch(symbol, 3600, want_start, want_end)
        cache.write_text(json.dumps(bars))
        return [b for b in bars if want_start <= b["ts"] < want_end]
    cached_start, cached_end = cached[0]["ts"], cached[-1]["ts"] + 3600
    if want_start < cached_start:
        print(f"  1H head fill {start_iso} -> {datetime.fromtimestamp(cached_start, timezone.utc).date()}")
        cached = _api_fetch(symbol, 3600, want_start, cached_start) + cached
    if cached_end < want_end:
        print(f"  1H tail fill {datetime.fromtimestamp(cached_end, timezone.utc).date()} -> {end_iso}")
        cached.extend(_api_fetch(symbol, 3600, cached_end, want_end))
    by_ts = {b["ts"]: b for b in cached}
    cached = [by_ts[t] for t in sorted(by_ts)]
    cache.write_text(json.dumps(cached))
    return [b for b in cached if want_start <= b["ts"] < want_end]


def load_daily(symbol: str, start_iso: str, end_iso: str) -> list[dict]:
    """Read existing daily cache (the bitstamp_adx_backtest one). The cache stores
    {ts, dt, open, high, low, close} — we don't refresh here; the ADX
    backtester is the canonical writer for the daily file."""
    cache = _cache_path_daily(symbol)
    if not cache.exists():
        raise SystemExit(f"Daily cache missing: {cache}. Run "
                          f"bitstamp_adx_backtest.py first to populate it.")
    raw = json.loads(cache.read_text())
    s = int(datetime.fromisoformat(start_iso).replace(tzinfo=timezone.utc).timestamp())
    e = int(datetime.fromisoformat(end_iso).replace(tzinfo=timezone.utc).timestamp()) + 86400
    return [b for b in raw if s <= b["ts"] < e]


# ─── EMA on daily closes ─────────────────────────────────────────────────────

# Indicator math lives in strategies.support.indicators (single source of truth).
from strategies.support.indicators import ema as calc_ema  # noqa: F401


# ─── Strategy walk ───────────────────────────────────────────────────────────

def is_v4_eligible_thursday(dt: datetime) -> bool:
    """Pine: dom in {1..7} (NFP-adj) OR dom in {9..13} (CPI-adj),
    AND NOT dom in {14..22} (OPEX-adj)."""
    if dt.weekday() != 3:  # 3 = Thursday
        return False
    dom = dt.day
    nfp = 1 <= dom <= 7
    cpi = 9 <= dom <= 13
    opex = 14 <= dom <= 22
    return (nfp or cpi) and not opex


def run_strategy(bars_1h: list[dict], daily: list[dict],
                 start_iso: str, end_iso: str) -> tuple[list[dict], float]:
    """Walk 1H bars; emit short trades per Pine spec."""
    # Daily EMA(50) on closes; index by ISO date for O(1) lookup.
    daily_closes = [b["close"] for b in daily]
    ema = calc_ema(daily_closes, REGIME_EMA_LEN)
    daily_idx_by_date: dict[str, int] = {}
    for i, b in enumerate(daily):
        d = datetime.fromtimestamp(b["ts"], timezone.utc).strftime("%Y-%m-%d")
        daily_idx_by_date[d] = i

    def bear_regime_at(thu_dt: datetime) -> bool | None:
        """Pine `request.security("D", ..., lookahead_off)` semantics on the
        Thursday 00:00 UTC intraday bar: returns values from the most recently
        CLOSED daily bar — i.e. Wednesday's close vs EMA50-through-Wednesday."""
        wed = (thu_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        i = daily_idx_by_date.get(wed)
        if i is None or i < REGIME_EMA_LEN - 1:
            return None
        e = ema[i]
        c = daily[i]["close"]
        if e != e:  # nan
            return None
        return c < e

    start_ts = int(datetime.fromisoformat(start_iso).replace(tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.fromisoformat(end_iso).replace(tzinfo=timezone.utc).timestamp()) + 86400

    trades: list[dict] = []
    pos: dict | None = None
    equity = 1_000_000.0  # match TV report's $1M base for direct $-comparison
    starting_equity = equity

    for bar in bars_1h:
        if bar["ts"] < start_ts or bar["ts"] >= end_ts:
            continue
        dt = datetime.fromtimestamp(bar["ts"], timezone.utc)

        # 1) Intra-bar SL check (Pine strategy.exit stop=).
        if pos is not None:
            sl_price = pos["entry_price"] * (1.0 + SL_PCT / 100.0)
            if bar["high"] >= sl_price:
                gross_pct = (pos["entry_price"] - sl_price) / pos["entry_price"] * 100
                fee_pct = 2 * COMMISSION_PCT_PER_LEG
                net_pct = gross_pct - fee_pct
                pnl_usdt = pos["size_usdt"] * net_pct / 100.0
                equity += pnl_usdt
                trades.append({**pos, "exit_dt": dt, "exit_price": sl_price,
                               "reason": "SL", "gross_pct": gross_pct,
                               "net_pct": net_pct, "pnl_usdt": pnl_usdt,
                               "cum_equity": equity})
                pos = None

        # 2) Friday 00:00 UTC exit (Fri_exit).
        if (pos is not None and dt.weekday() == 4 and dt.hour == EXIT_HOUR_UTC):
            ex = bar["close"]
            gross_pct = (pos["entry_price"] - ex) / pos["entry_price"] * 100
            fee_pct = 2 * COMMISSION_PCT_PER_LEG
            net_pct = gross_pct - fee_pct
            pnl_usdt = pos["size_usdt"] * net_pct / 100.0
            equity += pnl_usdt
            trades.append({**pos, "exit_dt": dt, "exit_price": ex,
                           "reason": "Fri_exit", "gross_pct": gross_pct,
                           "net_pct": net_pct, "pnl_usdt": pnl_usdt,
                           "cum_equity": equity})
            pos = None

        # 3) Thursday 00:00 UTC entry — only if flat and V4-eligible + bear.
        if (pos is None and dt.hour == ENTRY_HOUR_UTC
                and is_v4_eligible_thursday(dt)):
            br = bear_regime_at(dt)
            if br:
                entry_price = bar["close"]  # process_orders_on_close
                size_usdt = equity  # 100% of equity
                qty = size_usdt / entry_price
                pos = {"entry_dt": dt, "entry_price": entry_price,
                       "size_usdt": size_usdt, "qty": qty}

    return trades, equity


# ─── Reporting + parity check vs TV CSV ──────────────────────────────────────

def report(trades: list[dict], final_equity: float, starting_equity: float,
           start_iso: str, end_iso: str) -> None:
    n = len(trades)
    if n == 0:
        print("No trades.")
        return
    wins = [t for t in trades if t["pnl_usdt"] > 0]
    losses = [t for t in trades if t["pnl_usdt"] <= 0]
    gross_w = sum(t["pnl_usdt"] for t in wins)
    gross_l = sum(abs(t["pnl_usdt"]) for t in losses)
    pf = gross_w / gross_l if gross_l > 0 else float("inf")

    eq_curve = [starting_equity]
    for t in trades:
        eq_curve.append(t["cum_equity"])
    peak = eq_curve[0]
    max_dd = 0.0
    for e in eq_curve:
        peak = max(peak, e)
        dd = (e - peak) / peak * 100
        max_dd = min(max_dd, dd)

    total_pct = (final_equity - starting_equity) / starting_equity * 100

    print()
    print("=" * 78)
    print(f" S-096 Thu Bear V4 — Bitstamp BTCUSD 1H ({start_iso} -> {end_iso})")
    print("=" * 78)
    print(f"  Starting equity:   ${starting_equity:>14,.2f}")
    print(f"  Final equity:      ${final_equity:>14,.2f}")
    print(f"  Total return:      {total_pct:>+14.2f}%")
    print(f"  Trades:            {n} ({len(wins)}W / {len(losses)}L)")
    print(f"  Win rate:          {len(wins)/n*100:.1f}%")
    print(f"  Profit factor:     {pf:.3f}")
    print(f"  Max drawdown:      {max_dd:.2f}%")
    print()
    print(f"  {'#':>3}  {'entry (UTC)':<19}  {'exit (UTC)':<19}  "
          f"{'entry_px':>10}  {'exit_px':>10}  {'reason':<10}  "
          f"{'pnl_usd':>13}  {'pnl%':>7}  {'cum_eq':>14}")
    for i, t in enumerate(trades, 1):
        print(f"  {i:>3}  {t['entry_dt'].strftime('%Y-%m-%d %H:%M'):<19}  "
              f"{t['exit_dt'].strftime('%Y-%m-%d %H:%M'):<19}  "
              f"{t['entry_price']:>10.0f}  {t['exit_price']:>10.0f}  "
              f"{t['reason']:<10}  {t['pnl_usdt']:>+13,.2f}  "
              f"{t['net_pct']:>+7.2f}  {t['cum_equity']:>14,.2f}")


def diff_against_tv_csv(trades: list[dict], csv_path: Path) -> None:
    """If the user supplies a TV strategy-tester CSV, compare trade-by-trade."""
    import csv
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    # TV CSV has paired (Exit, Entry) rows per trade #; build entry-price by trade.
    tv: dict[int, dict] = {}
    for r in rows:
        tn = int(r["Trade #"])
        slot = tv.setdefault(tn, {})
        if r["Type"] == "Entry short":
            slot["entry_dt"] = r["Date and time"]
            slot["entry_price"] = float(r["Price USD"])
        else:
            slot["exit_dt"] = r["Date and time"]
            slot["exit_price"] = float(r["Price USD"])
            slot["net_pct"] = float(r["Net P&L %"])
            slot["cum_pct"] = float(r["Cumulative P&L %"])

    print()
    print("=" * 78)
    print(f" Parity check vs TV CSV: {csv_path.name}")
    print("=" * 78)
    n_match = 0
    for i, t in enumerate(trades, 1):
        if i not in tv:
            print(f"  #{i}: NO TV row — Python has extra trade {t['entry_dt']}")
            continue
        v = tv[i]
        # CSV times are UTC+3 (chart-tz); convert to UTC for compare.
        tv_entry = (datetime.strptime(v["entry_dt"], "%Y-%m-%d %H:%M")
                    - timedelta(hours=3))
        tv_exit = (datetime.strptime(v["exit_dt"], "%Y-%m-%d %H:%M")
                   - timedelta(hours=3))
        py_entry = t["entry_dt"].replace(tzinfo=None)
        py_exit = t["exit_dt"].replace(tzinfo=None)
        date_ok = (py_entry == tv_entry and py_exit == tv_exit)
        ep_diff = t["entry_price"] - v["entry_price"]
        xp_diff = t["exit_price"] - v["exit_price"]
        pct_diff = t["net_pct"] - v["net_pct"]
        flag = "OK " if (date_ok and abs(ep_diff) < 1 and abs(xp_diff) < 1
                          and abs(pct_diff) < 0.02) else "DIFF"
        print(f"  #{i:>2} {flag} dates={'eq' if date_ok else 'DIFF'}  "
              f"entry d={ep_diff:+7.1f}  exit d={xp_diff:+7.1f}  "
              f"net%% d={pct_diff:+5.2f}  (py {t['net_pct']:+5.2f} vs tv {v['net_pct']:+5.2f})")
        if flag == "OK ":
            n_match += 1
    extra_tv = [k for k in tv if k > len(trades)]
    if extra_tv:
        print(f"  TV has extra trades: {extra_tv}")
    print(f"\n  {n_match}/{max(len(trades), len(tv))} trades match within tolerance.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end",   default="2026-05-04")
    ap.add_argument("--symbol", default="btcusd")
    ap.add_argument("--refresh-1h", action="store_true",
                    help="Force-refetch the 1H Bitstamp cache.")
    ap.add_argument("--tv-csv", type=Path, default=None,
                    help="Path to TV strategy-tester CSV for parity check.")
    args = ap.parse_args()

    # Daily window: warm up EMA50 well before strategy start.
    daily_start = (datetime.fromisoformat(args.start) - timedelta(days=400)).date().isoformat()

    print(f"Loading daily {args.symbol} {daily_start} -> {args.end}")
    daily = load_daily(args.symbol, daily_start, args.end)
    print(f"  Loaded {len(daily)} daily bars.")

    print(f"Loading 1H {args.symbol} {args.start} -> {args.end}")
    bars_1h = load_1h(args.symbol, args.start, args.end, refresh=args.refresh_1h)
    print(f"  Loaded {len(bars_1h)} 1H bars.")

    trades, final_eq = run_strategy(bars_1h, daily, args.start, args.end)
    report(trades, final_eq, 1_000_000.0, args.start, args.end)
    if args.tv_csv:
        diff_against_tv_csv(trades, args.tv_csv)


if __name__ == "__main__":
    main()

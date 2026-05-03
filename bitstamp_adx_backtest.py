"""S-003 ADX long-history backtest on Bitstamp daily OHLC.

Pulls daily candles directly from Bitstamp's public API:
    https://www.bitstamp.net/api/v2/ohlc/{pair}/?step=86400&limit=1000
This is the same data TradingView's BITSTAMP:{PAIR} 1D feed shows, so trade
lists generated here line up with TV's strategy tester output to within
the same threshold-precision boundary cases we see on Binance (≈1 trade
in 40 may shift by 1 day at the ADX 25 threshold).

Replaces the prior implementation that aggregated a Kaggle 1m CSV archive.
That archive was missing intraday price spikes — its 2018-03-18 daily high
came in at ~$8,091 vs Bitstamp's official $8,324, and its close at $7,473
vs the official $8,188. Those misses dropped Pine SL hits and shifted the
state machine, producing trade-by-trade drift vs TV. The Bitstamp API path
gives an exact (to within 1-day boundary cases) match.

Usage:
    python bitstamp_adx_backtest.py
    python bitstamp_adx_backtest.py --start 2018-01-01 --end 2026-05-03
    python bitstamp_adx_backtest.py --symbol ethusd --start 2018-01-01
    python bitstamp_adx_backtest.py --mode service     # drives the live
                                                       # services/adx_service
    python bitstamp_adx_backtest.py --refresh          # force-refetch cache

Cache: first run for a (symbol, end-date) combination calls the API to fill
data/bitstamp_{symbol}_daily.json; subsequent runs read from cache and only
top up missing tail bars. Use --refresh to force full re-fetch.

Cost model: 10bp RT taker (matches services/adx_service.COST_BP_RT). No
funding — this is spot.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

# S-003 canonical parameters — copied verbatim from services/adx_service.py
ADX_PERIOD = 14
ADX_LOW_THRESH = 20.0
ADX_HIGH_THRESH = 25.0
EMA_LEN = 50
WAS_LOW_LOOKBACK = 20
COST_BP_RT = 10.0  # 5bp each leg


# ─── Bitstamp API + local cache ──────────────────────────────────────────────

API_URL = "https://www.bitstamp.net/api/v2/ohlc/{pair}/"


def _cache_path(symbol: str) -> Path:
    return REPO / "data" / f"bitstamp_{symbol}_daily.json"


def _api_fetch_window(symbol: str, start_ts: int, end_ts: int) -> list[dict]:
    """Fetch [start_ts, end_ts) daily bars from Bitstamp.

    The API caps at 1000 bars per call AND returns the most-recent bars
    within the (start, end) window. So we paginate forward by stepping
    end-of-window in 1000-day chunks rather than incrementing start.
    """
    bars: list[dict] = []
    cur = start_ts
    while cur < end_ts:
        chunk_end = min(cur + 1000 * 86400, end_ts)
        url = (f"{API_URL.format(pair=symbol)}"
               f"?step=86400&limit=1000&start={cur}&end={chunk_end}")
        req = urllib.request.Request(url, headers={"User-Agent": "p300-bot/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        page = data["data"]["ohlc"]
        if not page:
            cur = chunk_end + 86400
            continue
        for b in page:
            ts = int(b["timestamp"])
            bars.append({
                "ts": ts,
                "dt": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"),
                "open":  float(b["open"]),
                "high":  float(b["high"]),
                "low":   float(b["low"]),
                "close": float(b["close"]),
            })
        last_ts = int(page[-1]["timestamp"])
        cur = last_ts + 86400
        time.sleep(0.2)  # polite throttle
    # de-dup on date (in case of overlap at chunk boundaries)
    by_dt: dict[str, dict] = {}
    for b in bars:
        by_dt[b["dt"]] = b
    return [by_dt[d] for d in sorted(by_dt)]


def load_bitstamp_daily(symbol: str, warmup_start: str, end_date: str,
                         refresh: bool = False) -> list[dict]:
    """Return daily candles in [warmup_start, end_date], using local cache
    when possible and topping up with the API for any missing tail bars."""
    cache = _cache_path(symbol)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cached: list[dict] = []
    if cache.exists() and not refresh:
        cached = json.loads(cache.read_text())

    want_start = int(datetime.fromisoformat(warmup_start)
                     .replace(tzinfo=timezone.utc).timestamp())
    want_end = int(datetime.fromisoformat(end_date)
                   .replace(tzinfo=timezone.utc).timestamp()) + 86400

    if not cached:
        print(f"  Fetching Bitstamp daily {symbol} {warmup_start} -> {end_date} "
              f"(no cache)...")
        bars = _api_fetch_window(symbol, want_start, want_end)
        cache.write_text(json.dumps(bars))
        return [b for b in bars if want_start <= b["ts"] < want_end]

    cached_start = cached[0]["ts"]
    cached_end_excl = cached[-1]["ts"] + 86400
    fetch_head = want_start < cached_start
    fetch_tail = cached_end_excl < want_end
    if fetch_head:
        print(f"  Filling head: {warmup_start} -> "
              f"{datetime.fromtimestamp(cached_start, timezone.utc).date()}")
        head = _api_fetch_window(symbol, want_start, cached_start)
        cached = head + cached
    if fetch_tail:
        print(f"  Filling tail: "
              f"{datetime.fromtimestamp(cached_end_excl, timezone.utc).date()} "
              f"-> {end_date}")
        tail = _api_fetch_window(symbol, cached_end_excl, want_end)
        cached.extend(tail)
    if fetch_head or fetch_tail:
        # de-dup before persisting
        by_dt = {b["dt"]: b for b in cached}
        cached = [by_dt[d] for d in sorted(by_dt)]
        cache.write_text(json.dumps(cached))
    return [b for b in cached if want_start <= b["ts"] < want_end]


# ─── Indicators (verbatim port of services/adx_service.py) ───────────────────

def calc_ema(prices: list[float], period: int) -> list[float]:
    out = [float("nan")] * len(prices)
    if len(prices) < period:
        return out
    seed = sum(prices[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    for i in range(period, len(prices)):
        out[i] = prices[i] * k + out[i - 1] * (1 - k)
    return out


def calc_adx(candles: list[dict], period: int) -> list[float]:
    n = len(candles)
    if n < period * 2 + 1:
        return [float("nan")] * n
    tr = [0.0] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        h, l = candles[i]["high"], candles[i]["low"]
        ph, pl, pc = candles[i - 1]["high"], candles[i - 1]["low"], candles[i - 1]["close"]
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))
        up = h - ph
        down = pl - l
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
    atr = [float("nan")] * n
    pdi = [float("nan")] * n
    mdi = [float("nan")] * n
    dx = [float("nan")] * n
    atr[period] = sum(tr[1: period + 1])
    pdm_sum = sum(plus_dm[1: period + 1])
    mdm_sum = sum(minus_dm[1: period + 1])
    for i in range(period + 1, n):
        atr[i] = atr[i - 1] - atr[i - 1] / period + tr[i]
        pdm_sum = pdm_sum - pdm_sum / period + plus_dm[i]
        mdm_sum = mdm_sum - mdm_sum / period + minus_dm[i]
        if atr[i] > 0:
            pdi[i] = 100 * pdm_sum / atr[i]
            mdi[i] = 100 * mdm_sum / atr[i]
            denom = pdi[i] + mdi[i]
            dx[i] = 100 * abs(pdi[i] - mdi[i]) / denom if denom > 0 else 0.0
    adx = [float("nan")] * n
    first = period * 2
    if first < n:
        window = [dx[i] for i in range(period + 1, first + 1) if not math.isnan(dx[i])]
        if window:
            adx[first] = sum(window) / len(window)
            for i in range(first + 1, n):
                if not math.isnan(dx[i]):
                    adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return adx


# ─── Strategy walk-forward ───────────────────────────────────────────────────

def run_strategy(candles: list[dict], start_date: str,
                 mode: str = "stateful",
                 sl_pct: float = 10.0) -> tuple[list[dict], float, float]:
    """Walk forward bar-by-bar.

    Entry/exit: Pine's S-003 uses process_orders_on_close=true → orders
    execute at the CLOSE of the signal bar. We replicate that.
    SL hits execute at the SL price (= entry × (1 ± sl_pct/100)) when the
    bar's low/high breaches it — same convention as Pine's strategy.exit().

    mode:
      "current"   — services/adx_service.py legacy: any ADX<20 in last 20
                    bars + adx[i]>=25. Buggy; kept for diagnostic only.
      "tv_cross"  — Pine-ish one-shot: adx[i-1]<25 AND adx[i]>=25.
      "stateful"  — Exact Pine S-003 logic (sticky was_low, consumed on
                    entry). DEFAULT — matches the live service post the
                    2026-05-01 calibration patch.
      "service"   — Drive services/adx_service._current_signal on a growing
                    window. End-to-end check that the live service produces
                    the same trade list as 'stateful'. O(n^2), slow.

    Returns (trades, total_strategy_return_pct, buy_and_hold_pct).
    """
    closes = [c["close"] for c in candles]
    adx = calc_adx(candles, ADX_PERIOD)
    ema = calc_ema(closes, EMA_LEN)

    trades: list[dict] = []
    pos: dict | None = None
    was_low_state = False  # for mode == "stateful"

    def close_pos(exit_dt: str, exit_price: float, reason: str) -> None:
        nonlocal pos
        ep = pos["entry_price"]
        if pos["dir"] == "long":
            gross_pct = (exit_price - ep) / ep * 100
        else:
            gross_pct = (ep - exit_price) / ep * 100
        net_pct = gross_pct - COST_BP_RT / 100.0
        trades.append({
            "dir": pos["dir"], "entry_dt": pos["entry_dt"],
            "entry_price": ep, "exit_dt": exit_dt, "exit_price": exit_price,
            "gross_pct": gross_pct, "net_pct": net_pct, "reason": reason,
        })
        pos = None

    for i, bar in enumerate(candles):
        # 1) Intrabar SL — Pine's strategy.exit(stop=...).
        if pos is not None and sl_pct > 0:
            ep = pos["entry_price"]
            if pos["dir"] == "long":
                sl_price = ep * (1.0 - sl_pct / 100.0)
                if bar["low"] <= sl_price:
                    close_pos(bar["dt"], sl_price, "SL")
            else:
                sl_price = ep * (1.0 + sl_pct / 100.0)
                if bar["high"] >= sl_price:
                    close_pos(bar["dt"], sl_price, "SL")

        if math.isnan(adx[i]) or math.isnan(ema[i]):
            continue
        if bar["dt"] < start_date:
            continue

        exit_sig = adx[i] < ADX_LOW_THRESH

        if mode == "current":
            entry_armed = any(
                not math.isnan(adx[j]) and adx[j] < ADX_LOW_THRESH
                for j in range(max(0, i - WAS_LOW_LOOKBACK), i + 1)
            ) and adx[i] >= ADX_HIGH_THRESH
        elif mode == "tv_cross":
            prev = adx[i - 1] if i > 0 else float("nan")
            entry_armed = (not math.isnan(prev) and prev < ADX_HIGH_THRESH
                           and adx[i] >= ADX_HIGH_THRESH)
        elif mode == "stateful":
            if exit_sig:
                was_low_state = True
            entry_armed = was_low_state and adx[i] >= ADX_HIGH_THRESH
        elif mode == "service":
            from services import adx_service
            sig = adx_service._current_signal(candles[: i + 1])
            entry_armed = bool(sig and sig["entry_sig"])
        else:
            raise ValueError(f"unknown mode {mode!r}")

        # 2) Pine ordering: exit FIRST, entry SECOND. Both at this bar's close.
        if pos is not None and exit_sig:
            close_pos(bar["dt"], bar["close"], "ADX<20")

        if entry_armed:
            new_dir = "long" if closes[i] > ema[i] else "short"
            if pos is not None and pos["dir"] != new_dir:
                close_pos(bar["dt"], bar["close"], "regime_flip")
            if pos is None:
                pos = {"dir": new_dir, "entry_dt": bar["dt"],
                       "entry_price": bar["close"]}
            if mode == "stateful":
                was_low_state = False  # consume

    if pos is not None:
        last = candles[-1]
        ep = pos["entry_price"]
        ex = last["close"]
        gross = (ex - ep) / ep * 100 if pos["dir"] == "long" else (ep - ex) / ep * 100
        trades.append({
            "dir": pos["dir"], "entry_dt": pos["entry_dt"], "entry_price": ep,
            "exit_dt": last["dt"] + " (open)", "exit_price": ex,
            "gross_pct": gross, "net_pct": gross - COST_BP_RT / 100.0,
            "reason": "open", "still_open": True,
        })

    capital = 1.0
    for t in trades:
        capital *= (1.0 + t["net_pct"] / 100.0)
    strat_ret_pct = (capital - 1.0) * 100

    bh_start = next((c["close"] for c in candles if c["dt"] >= start_date), None)
    bh_end = candles[-1]["close"] if candles else None
    bh_pct = ((bh_end - bh_start) / bh_start * 100) if bh_start and bh_end else 0.0

    return trades, strat_ret_pct, bh_pct


# ─── Reporting ───────────────────────────────────────────────────────────────

def report(trades: list[dict], strat_pct: float, bh_pct: float,
           symbol: str, start_date: str, end_date: str) -> None:
    n = len(trades)
    if n == 0:
        print("No trades.")
        return
    wins = [t for t in trades if t["net_pct"] > 0]
    losses = [t for t in trades if t["net_pct"] <= 0]
    win_rate = len(wins) / n * 100
    avg_win = sum(t["net_pct"] for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t["net_pct"] for t in losses) / len(losses) if losses else 0.0
    gross_w = sum(t["net_pct"] for t in wins)
    gross_l = sum(abs(t["net_pct"]) for t in losses)
    pf = gross_w / gross_l if gross_l > 0 else float("inf")
    best = max(trades, key=lambda t: t["net_pct"])
    worst = min(trades, key=lambda t: t["net_pct"])

    eq = 1.0
    peak = 1.0
    max_dd = 0.0
    for t in trades:
        eq *= (1.0 + t["net_pct"] / 100.0)
        peak = max(peak, eq)
        dd = (eq - peak) / peak * 100
        max_dd = min(max_dd, dd)

    years = ((datetime.fromisoformat(end_date) -
              datetime.fromisoformat(start_date)).days / 365.25)
    cagr_strat = ((1 + strat_pct / 100) ** (1 / years) - 1) * 100 if years > 0 else 0
    cagr_bh = ((1 + bh_pct / 100) ** (1 / years) - 1) * 100 if years > 0 else 0

    print()
    print("=" * 76)
    print(f" S-003 ADX backtest — Bitstamp {symbol.upper()} daily "
          f"({start_date} -> {end_date})")
    print("=" * 76)
    print(f" Trades:           {n}")
    print(f" Win rate:         {win_rate:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f" Avg win:          {avg_win:+.2f}%")
    print(f" Avg loss:         {avg_loss:+.2f}%")
    print(f" Profit factor:    {pf:.3f}")
    print(f" Best trade:       {best['net_pct']:+.2f}%  "
          f"({best['dir']} {best['entry_dt']} -> {best['exit_dt']})")
    print(f" Worst trade:      {worst['net_pct']:+.2f}%  "
          f"({worst['dir']} {worst['entry_dt']} -> {worst['exit_dt']})")
    print(f" Strategy net:     {strat_pct:+.1f}%  (compounded, after 10bp RT)")
    print(f" Strategy CAGR:    {cagr_strat:.2f}%")
    print(f" Buy-and-hold:     {bh_pct:+.1f}%")
    print(f" B&H CAGR:         {cagr_bh:.2f}%")
    print(f" Edge over B&H:    {strat_pct - bh_pct:+.1f} pp")
    print(f" Max DD (closed):  {max_dd:.1f}%")
    print()

    by_year: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_year[t["entry_dt"][:4]].append(t)
    print(" Per-year breakdown:")
    for y in sorted(by_year):
        ts = by_year[y]
        wr = sum(1 for t in ts if t["net_pct"] > 0) / len(ts) * 100
        cap = 1.0
        for t in ts:
            cap *= (1.0 + t["net_pct"] / 100.0)
        print(f"   {y}: {len(ts):>2} trades, {wr:>5.1f}% wins, "
              f"compounded {(cap - 1) * 100:+7.1f}%")
    print()

    print(" Trade list:")
    print(f"   {'#':>2}  {'dir':5}  {'entry':12}  {'exit':18}  "
          f"{'entry_px':>11}  {'exit_px':>11}  {'pnl':>8}")
    for i, t in enumerate(trades, 1):
        flag = " *" if t.get("still_open") else "  "
        print(f"   {i:>2}{flag}{t['dir']:>5}  {t['entry_dt']:12}  "
              f"{t['exit_dt']:18}  {t['entry_price']:>11.2f}  "
              f"{t['exit_price']:>11.2f}  {t['net_pct']:>+7.2f}%")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--symbol", default="btcusd",
                    help="Bitstamp pair (e.g. btcusd, ethusd). Default btcusd.")
    ap.add_argument("--start", default="2018-01-01",
                    help="Strategy start date (default 2018-01-01)")
    ap.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    help="Strategy end date inclusive (default today UTC)")
    ap.add_argument("--warmup-start", default=None,
                    help="Earliest date to fetch for ADX/EMA warmup. Defaults "
                         "to start - 18 months. Wilder ADX bootstrap is "
                         "sensitive to the seed window — short warmup can "
                         "shift early-period signal days vs TV (which gets "
                         "all available chart history). 18 months is enough "
                         "for the indicators to fully converge for any "
                         "strategy start >= 2014.")
    ap.add_argument("--mode", default="stateful",
                    choices=["current", "tv_cross", "stateful", "service"],
                    help="Signal logic. 'stateful' (default) is the Pine "
                         "S-003 reference matching the live service.")
    ap.add_argument("--sl-pct", type=float, default=10.0,
                    help="Hard stop-loss in %% (Pine S-003 = 10). 0 disables.")
    ap.add_argument("--refresh", action="store_true",
                    help="Force-refetch the Bitstamp cache (data/bitstamp_*"
                         "_daily.json). Use after extending --end past the "
                         "cached tail to also re-validate previously cached "
                         "bars.")
    args = ap.parse_args()

    warmup = args.warmup_start
    if warmup is None:
        # 18 months is comfortably more than Wilder ADX(14) + EMA(50) need to
        # converge. Tested vs TradingView: this default gives byte-for-byte
        # trade-list parity with TV for strategy_start >= 2018, save the
        # known boundary cases at the ADX 25 threshold.
        warmup = (datetime.fromisoformat(args.start) - timedelta(days=540)
                  ).date().isoformat()

    print(f"Loading Bitstamp daily {args.symbol} {warmup} -> {args.end}")
    candles = load_bitstamp_daily(args.symbol, warmup, args.end,
                                   refresh=args.refresh)
    if not candles:
        print("No candles loaded.")
        return
    print(f"  Loaded {len(candles)} daily bars (cache: "
          f"{_cache_path(args.symbol).name}).")
    print(f"  First: {candles[0]['dt']} O={candles[0]['open']:.2f} "
          f"C={candles[0]['close']:.2f}")
    print(f"  Last:  {candles[-1]['dt']} O={candles[-1]['open']:.2f} "
          f"C={candles[-1]['close']:.2f}")
    print(f"Mode: {args.mode}, SL: {args.sl_pct}%")

    trades, strat_pct, bh_pct = run_strategy(candles, args.start, args.mode,
                                              sl_pct=args.sl_pct)
    report(trades, strat_pct, bh_pct, args.symbol, args.start, args.end)


if __name__ == "__main__":
    main()

"""S-003 ADX research harness.

Single source of truth for the ADX improvement study. Loads BTC daily from
`cd_spot_binance` (the LIVE signal source — see services/adx_service
_load_btc_daily_candles) and reproduces the live state machine EXACTLY,
including the asymmetric EMA(150) trend filter, as the baseline.

Everything is modular so improvement experiments can swap in:
  - a different direction rule (close-vs-EMA50  vs  +DI/-DI)
  - extra entry gates (HTF alignment, vol, OI, cross-exchange)
  - a different exit (ADX<20  vs  ATR trailing / chandelier)

Indicator math comes from strategies.support.indicators (the same ema/adx
the live service uses), so signal-level parity is guaranteed by construction.

Run:  python studies/notebooks/adx_study/harness.py
"""
from __future__ import annotations

import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from strategies.support import db
from strategies.support.indicators import ema, adx

# ─── Canonical S-003 params (match strategies/sleeves/adx/config.py) ──────────
ADX_PERIOD = 14
ADX_LOW = 20.0
ADX_HIGH = 25.0
EMA_LEN = 50
TREND_EMA_LEN = 150
COST_BP_RT = 10.0   # 5bp each leg (spot-only; no funding here)


# ─── Data ────────────────────────────────────────────────────────────────────

def load_btc_daily(end_date: str | None = None) -> list[dict]:
    """Daily BTC candles from cd_spot_binance (hourly spot -> daily UTC).

    Mirrors services/adx_service._load_btc_daily_candles: drops the still-
    forming current day. Returns [{ts,dt,open,high,low,close}], newest last.
    """
    con = sqlite3.connect(str(db.PROD_DB))
    rows = con.execute(
        "SELECT timestamp, open, high, low, close FROM cd_spot_binance "
        "ORDER BY timestamp"
    ).fetchall()
    con.close()
    days = defaultdict(list)
    for ts, o, h, l, c in rows:
        if o is None or c is None or o <= 0 or c <= 0:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        days[dt].append((ts, o, h, l, c))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = []
    for d in sorted(days.keys()):
        if d == today:
            continue
        if end_date and d > end_date:
            continue
        bars = days[d]
        out.append({
            "ts": bars[0][0], "dt": d,
            "open": bars[0][1],
            "high": max(b[2] for b in bars),
            "low": min(b[3] for b in bars),
            "close": bars[-1][4],
        })
    return out


def di_series(candles: list[dict], period: int = ADX_PERIOD):
    """+DI / -DI (Wilder), aligned to candles. Same math as indicators.adx
    internals, exposed so direction experiments can use native DI."""
    n = len(candles)
    pdi = [float("nan")] * n
    mdi = [float("nan")] * n
    if n < period * 2 + 1:
        return pdi, mdi
    tr = [0.0] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        h, l = candles[i]["high"], candles[i]["low"]
        ph, pl, pc = candles[i-1]["high"], candles[i-1]["low"], candles[i-1]["close"]
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))
        up = h - ph
        down = pl - l
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
    atr = sum(tr[1:period+1])
    pdm = sum(plus_dm[1:period+1])
    mdm = sum(minus_dm[1:period+1])
    for i in range(period+1, n):
        atr = atr - atr/period + tr[i]
        pdm = pdm - pdm/period + plus_dm[i]
        mdm = mdm - mdm/period + minus_dm[i]
        if atr > 0:
            pdi[i] = 100 * pdm / atr
            mdi[i] = 100 * mdm / atr
    return pdi, mdi


def atr_series(candles: list[dict], period: int = 14):
    """Wilder ATR aligned to candles (NaN until warmup)."""
    n = len(candles)
    out = [float("nan")] * n
    if n < period + 1:
        return out
    tr = [0.0] * n
    for i in range(1, n):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))
    out[period] = sum(tr[1:period+1]) / period
    for i in range(period+1, n):
        out[i] = (out[i-1] * (period-1) + tr[i]) / period
    return out


# ─── Backtest engine ─────────────────────────────────────────────────────────

def run(candles: list[dict], start_date: str = "2018-01-01", *,
        sl_pct: float = 10.0,
        trend_ema_len: int = TREND_EMA_LEN,
        direction: str = "ema50",          # "ema50" | "di" | "di_and_ema50"
        allow_short: bool = True,
        entry_gate=None,                    # fn(ctx)->bool, True=allow entry
        exit_mode: str = "adx",             # "adx" | "atr_trail" | "adx_or_atr"
        atr_mult: float = 3.0,
        atr_period: int = 14,
        rearm_thresh: float | None = None,  # if set, was_low re-arms on adx<this (default = ADX_LOW)
        short_needs_deep: bool = False,     # if True, SHORT entries still require a deep (<ADX_LOW) arm
        verbose: bool = False) -> dict:
    """Faithful S-003 walk-forward with pluggable direction / gate / exit.

    Baseline (defaults) == live services/adx_service semantics:
      direction='ema50', trend_ema_len=150 (asymmetric LONG-only), exit='adx',
      sl_pct=10, allow_short=True.

    ctx passed to entry_gate has: i, dt, close, adx, pdi, mdi, ema50,
    trend_ema, new_dir, candles, atr.
    Returns dict with trades + metrics.
    """
    closes = [c["close"] for c in candles]
    a = adx(candles, ADX_PERIOD)
    e50 = ema(closes, EMA_LEN)
    te = ema(closes, trend_ema_len) if trend_ema_len > 0 else None
    pdi, mdi = di_series(candles, ADX_PERIOD)
    atr = atr_series(candles, atr_period)
    rearm = ADX_LOW if rearm_thresh is None else rearm_thresh

    trades: list[dict] = []
    pos = None
    was_low = False        # shallow arm (re-arms at `rearm`)
    was_low_deep = False   # deep arm (re-arms strictly at ADX_LOW)
    trail = None  # trailing stop price for atr exits

    def _dirof(i):
        if direction == "ema50":
            return "long" if closes[i] > e50[i] else "short"
        if direction == "di":
            return "long" if pdi[i] >= mdi[i] else "short"
        if direction == "di_and_ema50":
            # only take the trade if DI and EMA agree; else return None (skip)
            d_ema = "long" if closes[i] > e50[i] else "short"
            d_di = "long" if pdi[i] >= mdi[i] else "short"
            return d_ema if d_ema == d_di else None
        raise ValueError(direction)

    def close_pos(dt, px, reason):
        nonlocal pos, trail
        ep = pos["entry_price"]
        g = (px-ep)/ep*100 if pos["dir"] == "long" else (ep-px)/ep*100
        net = g - COST_BP_RT/100.0
        trades.append({**pos, "exit_dt": dt, "exit_price": px,
                       "gross_pct": g, "net_pct": net, "reason": reason})
        pos = None
        trail = None

    for i, bar in enumerate(candles):
        # 1) intrabar hard SL
        if pos is not None and sl_pct > 0:
            ep = pos["entry_price"]
            if pos["dir"] == "long":
                slp = ep*(1-sl_pct/100)
                if bar["low"] <= slp:
                    close_pos(bar["dt"], slp, "SL")
            else:
                slp = ep*(1+sl_pct/100)
                if bar["high"] >= slp:
                    close_pos(bar["dt"], slp, "SL")
        # 1b) ATR trailing stop (intrabar), if enabled and in pos
        if pos is not None and exit_mode in ("atr_trail", "adx_or_atr") \
                and not math.isnan(atr[i]):
            if pos["dir"] == "long":
                trail = max(trail, bar["close"] - atr_mult*atr[i]) if trail else \
                        (pos["entry_price"] - atr_mult*atr[i])
                if bar["low"] <= trail:
                    close_pos(bar["dt"], trail, "ATR_trail")
            else:
                cand = bar["close"] + atr_mult*atr[i]
                trail = min(trail, cand) if trail else (pos["entry_price"] + atr_mult*atr[i])
                if bar["high"] >= trail:
                    close_pos(bar["dt"], trail, "ATR_trail")

        if math.isnan(a[i]) or math.isnan(e50[i]):
            continue
        if bar["dt"] < start_date:
            # still update arm state during warmup window so it's correct
            if a[i] < rearm: was_low = True
            if a[i] < ADX_LOW: was_low_deep = True
            continue

        if a[i] < rearm: was_low = True
        if a[i] < ADX_LOW: was_low_deep = True
        exit_sig = a[i] < ADX_LOW
        entry_event = was_low and a[i] >= ADX_HIGH

        # exit first (ADX death) unless we're in pure atr_trail mode
        if pos is not None and exit_sig and exit_mode in ("adx", "adx_or_atr"):
            close_pos(bar["dt"], bar["close"], "ADX<20")

        if entry_event:
            was_low = False  # consume regardless of outcome (one attempt/cycle)
            deep_armed = was_low_deep
            was_low_deep = False
            new_dir = _dirof(i)
            blocked = new_dir is None
            # SHORT may require a deep (<20) arm even when longs use a shallow re-arm
            if not blocked and short_needs_deep and new_dir == "short" and not deep_armed:
                blocked = True
            # asymmetric trend filter (LONG only)
            if not blocked and te is not None and not math.isnan(te[i]):
                if new_dir == "long" and bar["close"] <= te[i]:
                    blocked = True
            if not blocked and new_dir == "short" and not allow_short:
                blocked = True
            # custom entry gate
            if not blocked and entry_gate is not None:
                ctx = dict(i=i, dt=bar["dt"], close=bar["close"], adx=a[i],
                           pdi=pdi[i], mdi=mdi[i], ema50=e50[i],
                           trend_ema=(te[i] if te is not None else None),
                           new_dir=new_dir, candles=candles, atr=atr[i])
                if not entry_gate(ctx):
                    blocked = True
            if not blocked:
                if pos is not None and pos["dir"] != new_dir:
                    close_pos(bar["dt"], bar["close"], "regime_flip")
                if pos is None:
                    pos = {"dir": new_dir, "entry_dt": bar["dt"],
                           "entry_price": bar["close"], "entry_adx": round(a[i],1)}
                    trail = None

    if pos is not None:
        last = candles[-1]
        close_pos(last["dt"]+" (open)", last["close"], "open")
        trades[-1]["still_open"] = True

    return _metrics(trades, candles, start_date)


def _metrics(trades, candles, start_date):
    n = len(trades)
    if n == 0:
        return {"trades": [], "n": 0, "ret_pct": 0, "wr": 0, "pf": 0,
                "max_dd": 0, "cagr": 0, "bh_pct": 0, "sharpe": 0}
    wins = [t for t in trades if t["net_pct"] > 0]
    losses = [t for t in trades if t["net_pct"] <= 0]
    gw = sum(t["net_pct"] for t in wins)
    gl = sum(abs(t["net_pct"]) for t in losses)
    eq = 1.0; peak = 1.0; mdd = 0.0
    rets = []
    for t in trades:
        eq *= (1+t["net_pct"]/100); peak = max(peak, eq)
        mdd = min(mdd, (eq-peak)/peak*100); rets.append(t["net_pct"]/100)
    ret_pct = (eq-1)*100
    import statistics
    sharpe = (statistics.mean(rets)/statistics.pstdev(rets)*math.sqrt(len(rets))
              if len(rets) > 1 and statistics.pstdev(rets) > 0 else 0)
    bh_s = next((c["close"] for c in candles if c["dt"] >= start_date), None)
    bh_e = candles[-1]["close"]
    bh = (bh_e-bh_s)/bh_s*100 if bh_s else 0
    end = candles[-1]["dt"][:10]
    yrs = (datetime.fromisoformat(end)-datetime.fromisoformat(start_date)).days/365.25
    cagr = ((1+ret_pct/100)**(1/yrs)-1)*100 if yrs > 0 and ret_pct > -100 else 0
    return {
        "trades": trades, "n": n,
        "wr": len(wins)/n*100,
        "avg_win": gw/len(wins) if wins else 0,
        "avg_loss": gl/len(losses)*-1 if losses else 0,
        "pf": gw/gl if gl > 0 else float("inf"),
        "ret_pct": ret_pct, "cagr": cagr, "bh_pct": bh,
        "max_dd": mdd, "sharpe": sharpe,
        "mar": (cagr/abs(mdd)) if mdd < 0 else float("inf"),
    }


def fmt(m, label=""):
    if m["n"] == 0:
        return f"{label:28} n=0"
    return (f"{label:28} n={m['n']:>3}  WR={m['wr']:4.0f}%  "
            f"PF={m['pf']:4.2f}  ret={m['ret_pct']:+8.0f}%  "
            f"CAGR={m['cagr']:+5.1f}%  maxDD={m['max_dd']:6.1f}%  "
            f"MAR={m['mar']:4.2f}  Sh={m['sharpe']:4.2f}")


if __name__ == "__main__":
    c = load_btc_daily()
    print(f"Loaded {len(c)} daily bars  {c[0]['dt']} -> {c[-1]['dt']}")
    base = run(c, "2018-01-01")
    print("\nBASELINE (live S-003 semantics: ema50 dir, EMA150 LONG filter, ADX exit, 10% SL)")
    print(fmt(base, "baseline"))
    print(f"\n{'#':>2} {'dir':5} {'entry':11} {'exit':18} {'eADX':>5} {'pnl':>8}  reason")
    for i, t in enumerate(base["trades"], 1):
        print(f"{i:>2} {t['dir']:5} {t['entry_dt']:11} {str(t['exit_dt']):18} "
              f"{t.get('entry_adx',0):>5} {t['net_pct']:>+7.2f}%  {t['reason']}")

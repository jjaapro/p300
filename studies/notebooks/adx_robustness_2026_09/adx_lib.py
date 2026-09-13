"""ADX robustness pack — shared code (read-only).

Signal machine = the shipped sleeve's state machine (bots/adx/strategy/signal.py:160-198, Tier-2:
symmetric EMA(150) gate, no funding veto), indicators from the adx_study harness. `live_walk` prices the
machine the way the live bot does: enter at the first 1 m close after the day boundary, stops checked minute
by minute with stop_path semantics (wick -> stop, gap -> open), a day's trail level valid from the next day,
trail seeded from the anchor close, 15 bp round trip, Binance settlement funding.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in (str(ROOT), str(ROOT / "studies" / "notebooks" / "execution_2026_09"), str(ROOT / "studies" / "notebooks" / "sizing_style_2026_09")):
    if p not in sys.path:
        sys.path.insert(0, p)
import exec_lib as ex  # noqa: E402
import sizing_lib as sz  # noqa: E402

bv = ex.bv
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)
hz = bv.import_adx_harness()

START = "2020-01-01"   # btc_1m begins 2020-01-01: the common window for harness vs live
SL_PCT = 10.0
ATR_MULT = 4.0
COST_BP_RT = 15.0
_FUND = None


def jdump(obj, name: str) -> Path:
    import json
    p = RESULTS / name
    p.write_text(json.dumps(bv._jsonable(obj), indent=1, default=str), encoding="utf-8")
    return p


def jload(name: str):
    import json
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def funding_rows():
    global _FUND
    if _FUND is None:
        _FUND = bv.fetch_binance_funding("BTCUSDT")
    return _FUND


def daily_candles(phase_hour: int) -> list[dict]:
    return bv.df_to_candles(bv.load_daily_btc(phase_hour))


def signals(candles: list[dict]) -> dict:
    """Per closed daily bar: entry_dir (+1/-1/0, Tier-2 symmetric gate, was_low consumed regardless),
    exit_sig (ADX < 20), atr, close, ts (bar open)."""
    closes = [c["close"] for c in candles]
    a = hz.adx(candles, hz.ADX_PERIOD)
    e50 = hz.ema(closes, hz.EMA_LEN)
    e150 = hz.ema(closes, hz.TREND_EMA_LEN)
    atr = hz.atr_series(candles, 14)
    n = len(candles)
    entry = np.zeros(n, dtype=int)
    exit_sig = np.zeros(n, dtype=bool)
    was_low = False
    for j in range(n):
        if np.isnan(a[j]) or np.isnan(e50[j]):
            continue
        if a[j] < hz.ADX_LOW:
            was_low = True
        if was_low and a[j] >= hz.ADX_HIGH:
            new_dir = 1 if closes[j] > e50[j] else -1
            blocked = False
            if not np.isnan(e150[j]):
                if new_dir > 0 and closes[j] <= e150[j]:
                    blocked = True
                if new_dir < 0 and closes[j] >= e150[j]:
                    blocked = True
            entry[j] = 0 if blocked else new_dir
            was_low = False
        exit_sig[j] = (not np.isnan(a[j])) and a[j] < hz.ADX_LOW
    return dict(ts=np.array([c["ts"] for c in candles], dtype=np.int64), close=np.array(closes), atr=np.asarray(atr, float),
                entry=entry, exit=exit_sig, dt=[c["dt"] for c in candles])


def live_walk(candles: list[dict], path: ex.PricePath, start: str = START, sl_pct: float = SL_PCT, atr_mult: float = ATR_MULT,
              cost_bp_rt: float = COST_BP_RT, with_funding: bool = True, entry_delay_s: int = 0,
              fund_rows: list | None = None) -> list[dict]:
    """The machine priced with live semantics. Returns closed trades. `fund_rows` overrides the BTC
    settlement series (e.g. ETHUSDT for the ETH study)."""
    sig = signals(candles)
    ts, close, atr, entry, exit_sig = sig["ts"], sig["close"], sig["atr"], sig["entry"], sig["exit"]
    n = len(ts)
    start_ts = int(pd.Timestamp(start, tz="UTC").timestamp())
    fund = (fund_rows if fund_rows is not None else funding_rows()) if with_funding else None
    trades: list[dict] = []
    pos = None   # dict(dir, entry_ts, entry_px, i_entry, sl, trail, anchor_i)

    def close_pos(exit_ts: int, exit_px: float, reason: str) -> None:
        nonlocal pos
        d = pos["dir"]
        gross = d * (exit_px / pos["entry_px"] - 1.0) * 100.0
        f = bv.funding_pct_between(fund, pos["entry_ts"], exit_ts) if fund else 0.0
        funding_paid = d * f                   # long pays positive funding, short receives it
        trades.append(dict(dir="long" if d > 0 else "short", entry_ts=pos["entry_ts"], entry_dt=ex.iso(pos["entry_ts"]),
                           entry_px=pos["entry_px"], exit_ts=exit_ts, exit_dt=ex.iso(exit_ts), exit_px=exit_px, reason=reason,
                           gross_pct=gross, funding_pct=-funding_paid, net_pct=gross - cost_bp_rt / 100.0 - funding_paid,
                           sl=pos["sl"], init_stop_pct=pos["init_stop_pct"], i_entry=pos["i_entry"], i_exit=path.idx_ge(exit_ts)))
        pos = None

    for i in range(n - 1):
        boundary = int(ts[i + 1])                 # bar i closes here; the bot acts at the first tick after it
        if boundary < start_ts:
            continue
        # 1) intrabar stops during the day that just ended? No: stops are checked on the day *after* the
        #    boundary, with levels valid from this boundary. Handled below after entries/exits at the boundary.
        # 2) signal actions at the boundary (bar i just closed)
        if pos is not None:
            j = path.idx_ge(boundary)
            if j < len(path.ts) and path.ts[j] == boundary:
                px = float(path.c[j])
                if exit_sig[i]:
                    close_pos(boundary + 60, px, "ADX<20")
                elif entry[i] != 0 and entry[i] != pos["dir"]:
                    close_pos(boundary + 60, px, "direction flip")
        if pos is None and entry[i] != 0:
            j = path.idx_ge(boundary + entry_delay_s)
            if j < len(path.ts) and path.ts[j] == boundary + entry_delay_s and not np.isnan(atr[i]):
                d = int(entry[i]); px = float(path.c[j])
                sl = px * (1 - d * sl_pct / 100.0) if sl_pct > 0 else (0.0 if d > 0 else float("inf"))
                seed = close[i] - d * atr_mult * atr[i] if atr_mult > 0 else (0.0 if d > 0 else float("inf"))
                lvl = max(sl, seed) if d > 0 else min(sl, seed)
                pos = dict(dir=d, entry_ts=boundary + entry_delay_s + 60, entry_px=px, i_entry=j, sl=sl, trail=seed,
                           init_stop_pct=abs(px - lvl) / px * 100.0)
                continue   # no stop check on the entry minute itself
        # 3) trail ratchet: bar i's level becomes valid from this boundary on
        if pos is not None and atr_mult > 0 and not np.isnan(atr[i]) and int(ts[i]) >= pos["entry_ts"] - 86400:
            lvl_i = close[i] - pos["dir"] * atr_mult * atr[i]
            pos["trail"] = max(pos["trail"], lvl_i) if pos["dir"] > 0 else min(pos["trail"], lvl_i)
        # 4) minute-by-minute stop check over the next day's bars with the levels valid now
        if pos is not None:
            d = pos["dir"]
            level = max(pos["sl"], pos["trail"]) if d > 0 else min(pos["sl"], pos["trail"])
            a0 = path.idx_ge(boundary)
            a1 = path.idx_ge(int(ts[i + 2])) if i + 2 < n else len(path.ts)
            if pos["i_entry"] >= a0:
                a0 = pos["i_entry"] + 1
            w = ex.walk(path, a0, a1, d, level, float("inf") if d > 0 else -float("inf"))
            if w["kind"] == "stop":
                reason = "SL" if (level == pos["sl"]) else "ATR_trail"
                close_pos(int(path.ts[w["idx"]]) + 60, w["price_pathsem"], reason)
    return trades


def mtm_returns(trades: list[dict], path: ex.PricePath, cost_bp_rt: float = COST_BP_RT) -> pd.Series:
    """Daily fractional returns of a 1x position (UTC-day marks), fees on entry/exit days, funding on exit day."""
    rows = []
    for t in trades:
        rows.append(dict(i_fill=t["i_entry"], i_exit=min(t["i_exit"], len(path.ts) - 1), d=1 if t["dir"] == "long" else -1,
                         notional_usd=1.0, fill_spot=t["entry_px"], exit_spot=t["exit_px"],
                         cost_usd=cost_bp_rt / 1e4 - t["funding_pct"] / 100.0))
    return sz.daily_mtm_pnl(rows, path)


def curve_stats(r: pd.Series) -> dict:
    x = r.to_numpy()
    eq = np.cumprod(1 + x); peak = np.maximum.accumulate(eq)
    mdd = float(((eq - peak) / peak).min() * 100)
    years = len(x) / 365.25
    cagr = float((eq[-1] ** (1 / years) - 1) * 100) if years > 0 else float("nan")
    return dict(cagr_pct=cagr, mtm_maxdd_pct=mdd, sharpe=float(bv.sharpe_of(x)), mar=float(cagr / abs(mdd)) if mdd < 0 else float("inf"),
                days=int(len(x)), total_pct=float((eq[-1] - 1) * 100))


def ledger_stats(trades: list[dict]) -> dict:
    net = np.array([t["net_pct"] for t in trades])
    return dict(n=len(trades), mean_net_pct=float(net.mean()), win=float((net > 0).mean()), worst_pct=float(net.min()),
                sum_net_pct=float(net.sum()), reasons=pd.Series([t["reason"] for t in trades]).value_counts().to_dict(),
                funding_pct_sum=float(sum(t["funding_pct"] for t in trades)),
                median_init_stop_pct=float(np.median([t["init_stop_pct"] for t in trades])))

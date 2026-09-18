"""ORB reference engine: one session at a time, one minute bar at a time.

Chronology of a session (UTC, bars open-stamped, a bar is known at its close):

    t0 ............ t0+m           opening range [t0, t0+m): H and L frozen at t0+m
    t0+m .......... deadline       entry window (fill must happen strictly before deadline)
    fill .......... exit           position; the protective stop is live from the fill

Fill rules (TEST_PLAN.md section 5, PREREGISTRATION.md section 3):

* close entry   - trigger = first completed bar closing beyond the boundary; market order
                  fills at the open of bar trigger+1+latency.
* stop entry    - resting buy stop at H+tick and sell stop at L-tick from t0+m (OCO); a buy
                  stop fills at max(level, bar open), a sell stop at min(level, bar open).
                  A bar touching both levels is ambiguous: both paths are walked and the
                  worse one is kept.
* stops         - bar opening beyond the stop exits at that open (gap), otherwise a touch
                  exits at the stop. In the fill bar a close entry filled at the open, so a
                  touch is unambiguous; an intrabar stop-entry fill followed by a touch is
                  ambiguous and booked as stopped.
* targets       - a resting limit at the target fills at the target when the bar trades
                  through it by a tick; stop and target in one bar books the stop.
* event exits   - decided on a completed bar, filled at the next open.
* time exits    - at the open of the first tradable bar at/after the exit minute.

Zero-volume bars are exchange-outage filler in the Binance archive; the Market treats
them as missing. Gross, funding and risk are returned in basis points of entry notional;
costs are applied afterwards (orb_metrics.py) so every cost scenario reuses one walk.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

MINUTE_MS = 60_000
DAY_MIN = 1440


# --- inputs ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Policy:
    id: str
    anchor: str = "NY"              # NY | LDN | UTC (sessions come from orb_calendars)
    shift_min: int = 0              # placebo shift of the anchor, minutes
    range_min: int = 15
    entry: str = "close"            # close | stop | momentum | clock_long | clock_short | fade
    buffer_w: float = 0.0           # close must clear the boundary by buffer_w * W
    direction_gate: bool = False    # watch only the boundary in the range candle's direction
    relvol_min: float | None = None  # range volume / mean of prior 20 valid ranges must exceed
    width_band: bool = False        # W/open inside [p20, p80] of prior 60 valid ranges
    deadline_min: int = 120         # fill strictly before t0 + deadline_min
    stop: str = "opposite"          # opposite | mid | trail_w
    target_r: float | None = None   # target at fill +/- target_r * initial risk
    time_exit: str = "session"      # session (t0 + session_min) | entry60 | none
    exit_event: str | None = None   # reenter_1m | reenter_15m | vwap
    latency_min: int = 0            # whole minutes between signal availability and submission
    session_min: int = 390
    censor_days: int = 7            # horizon for time_exit == "none"


@dataclass
class Market:
    symbol: str
    t0_ms: int
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    quote_volume: np.ndarray
    funding_idx: np.ndarray         # minute index of each funding settlement (sorted)
    funding_rate: np.ndarray
    tick_before: float
    tick_after: float
    tick_change_idx: int

    @classmethod
    def from_panel(cls, symbol: str, panel: dict, funding: dict, tick_before: float,
                   tick_after: float, tick_change_utc: str | None) -> "Market":
        t0 = int(panel["t0_ms"])
        dead = ~(panel["volume"] > 0)       # outage filler (and NaN) is not a tradable price
        cols = {}
        for k in ("open", "high", "low", "close"):
            a = panel[k].astype(np.float64).copy()
            a[dead] = np.nan
            cols[k] = a
        change = len(cols["close"]) if tick_change_utc is None else \
            (int(datetime.fromisoformat(tick_change_utc).replace(tzinfo=timezone.utc).timestamp() * 1000) - t0) // MINUTE_MS
        return cls(symbol=symbol, t0_ms=t0, **cols,
                   volume=np.where(dead, 0.0, panel["volume"]),
                   quote_volume=np.where(dead, 0.0, panel["quote_volume"]),
                   funding_idx=(funding["time_ms"] - t0) // MINUTE_MS,
                   funding_rate=funding["rate"].astype(np.float64),
                   tick_before=tick_before, tick_after=tick_after, tick_change_idx=int(change))

    def __len__(self) -> int:
        return len(self.close)

    def index(self, t_ms: int) -> int:
        return (int(t_ms) - self.t0_ms) // MINUTE_MS

    def time_ms(self, i: int) -> int:
        return self.t0_ms + int(i) * MINUTE_MS

    def ok(self, i: int) -> bool:
        return 0 <= i < len(self.close) and not math.isnan(self.close[i])

    def tick(self, i: int) -> float:
        return self.tick_before if i < self.tick_change_idx else self.tick_after


def floor_tick(px: float, tick: float) -> float:
    return math.floor(px / tick + 1e-9) * tick


def ceil_tick(px: float, tick: float) -> float:
    return math.ceil(px / tick - 1e-9) * tick


# --- range features (causal across sessions) -------------------------------------------

def range_features(mkt: Market, sessions: pd.DataFrame, policy: Policy) -> pd.DataFrame:
    """H, L, W and the rolling filter inputs for every session, using prior sessions only."""
    m = policy.range_min
    rows = []
    for s in sessions.itertuples(index=False):
        i0 = mkt.index(s.t0_ms)
        row = {"i0": i0, "valid": False, "invalid_reason": None}
        if i0 < 0 or i0 + policy.session_min >= len(mkt):
            row["invalid_reason"] = "outside_panel"
        else:
            sl = slice(i0, i0 + m)
            if np.isnan(mkt.close[sl]).any():
                row["invalid_reason"] = "missing_range_bar"
            else:
                H, L = float(mkt.high[sl].max()), float(mkt.low[sl].min())
                row.update(H=H, L=L, W=H - L, range_open=float(mkt.open[i0]),
                           range_close=float(mkt.close[i0 + m - 1]), range_volume=float(mkt.volume[sl].sum()))
                if H - L <= 0:
                    row["invalid_reason"] = "zero_width"
                else:
                    row["valid"] = True
        rows.append(row)
    f = pd.DataFrame(rows)
    for c in ("H", "L", "W", "range_open", "range_close", "range_volume"):
        if c not in f:
            f[c] = np.nan
    w_rel = (f["W"] / f["range_open"]).where(f["valid"])
    vol = f["range_volume"].where(f["valid"])
    # shift(1) over the valid-session sequence: the current range never enters its own baseline.
    valid_idx = f.index[f["valid"]]
    prior_vol = vol.loc[valid_idx].shift(1).rolling(20, min_periods=20).mean()
    prior_w = w_rel.loc[valid_idx].shift(1)
    f["relvol"] = np.nan
    f.loc[valid_idx, "relvol"] = vol.loc[valid_idx] / prior_vol
    f["w_rel"] = w_rel
    f["w_p20"] = np.nan
    f["w_p80"] = np.nan
    f.loc[valid_idx, "w_p20"] = prior_w.rolling(60, min_periods=60).quantile(0.20)
    f.loc[valid_idx, "w_p80"] = prior_w.rolling(60, min_periods=60).quantile(0.80)
    return f


# --- entries ------------------------------------------------------------------------------

def _close_trigger(mkt: Market, i0: int, H: float, L: float, W: float, p: Policy, gate: int):
    """First completed close beyond a watched boundary whose fill lands before the deadline."""
    lat = p.latency_min
    deadline = i0 + p.deadline_min
    for k in range(i0 + p.range_min, deadline - 1 - lat):
        if not mkt.ok(k):
            continue
        c = mkt.close[k]
        up = c > H + p.buffer_w * W and gate >= 0
        down = c < L - p.buffer_w * W and gate <= 0
        if up or down:
            return k, (1 if up else -1)
    return None, 0


def _stop_level(side: int, H: float, L: float, W: float, p: Policy, tick: float) -> float:
    if p.entry == "fade":
        return floor_tick(L - W, tick) if side > 0 else ceil_tick(H + W, tick)
    if p.stop == "mid":
        mid = (H + L) / 2
        return floor_tick(mid, tick) if side > 0 else ceil_tick(mid, tick)
    return floor_tick(L, tick) if side > 0 else ceil_tick(H, tick)   # opposite and trail_w start here


def _time_exit_index(i0: int, fill_idx: int, p: Policy) -> tuple[int, str]:
    session_end = i0 + p.session_min
    if p.time_exit == "session":
        return session_end, "time"
    if p.time_exit == "entry60":
        return min(fill_idx + 60, session_end), "time"
    return fill_idx + p.censor_days * DAY_MIN, "censored_horizon"


# --- position walk --------------------------------------------------------------------------

def walk(mkt: Market, side: int, fill_idx: int, fill_px: float, stop_px: float,
         target_px: float | None, intrabar_fill: bool, p: Policy, H: float, L: float, W: float,
         i0: int, end_idx: int) -> dict:
    """Walk one position bar by bar until an exit. Returns exit fields and flags."""
    time_idx, time_reason = _time_exit_index(i0, fill_idx, p)
    flags: list[str] = []
    stop = stop_px
    run_ext = fill_px
    pending_event = False
    mae = mfe = 0.0
    cum_qv = float(mkt.quote_volume[i0:fill_idx].sum())
    cum_v = float(mkt.volume[i0:fill_idx].sum())
    tick = mkt.tick(fill_idx)
    j = fill_idx
    last_ok = fill_idx
    while True:
        if j >= end_idx:
            return _exit(mkt.close[last_ok], last_ok, "censored_block_end", flags, mae, mfe)
        if j >= time_idx and j > fill_idx:
            if not mkt.ok(j):
                flags.append("time_exit_delayed")
                j += 1
                continue
            return _exit(mkt.open[j], j, time_reason, flags, mae, mfe)
        if not mkt.ok(j):
            if j > fill_idx:
                flags.append("gap_while_invested")
            j += 1
            continue
        o, h, lo, c = mkt.open[j], mkt.high[j], mkt.low[j], mkt.close[j]
        first = j == fill_idx
        if pending_event and not first:
            return _exit(o, j, f"event_{p.exit_event}", flags, mae, mfe)
        if not first and ((side > 0 and o <= stop) or (side < 0 and o >= stop)):
            mae = min(mae, side * (o - fill_px) / fill_px * 1e4)
            return _exit(o, j, "stop_gap", flags, mae, mfe)
        stop_hit = lo <= stop if side > 0 else h >= stop
        target_hit = target_px is not None and (h >= target_px + tick if side > 0 else lo <= target_px - tick)
        # excursions in bp of the fill, never beyond the price the position actually left at
        worst = max(lo, stop) if side > 0 else min(h, stop)
        best = h if side > 0 else lo
        if target_hit and not stop_hit:
            best = target_px
        mae = min(mae, side * (worst - fill_px) / fill_px * 1e4)
        mfe = max(mfe, side * (best - fill_px) / fill_px * 1e4)
        if stop_hit:
            if first and intrabar_fill:
                flags.append("ambiguous_fill_bar_stop")
            if target_hit:
                flags.append("ambiguous_stop_target")
            return _exit(stop, j, "stop", flags, mae, mfe)
        if target_hit:
            return _exit(target_px, j, "target", flags, mae, mfe)
        last_ok = j
        # end-of-bar updates: they act from the next bar on
        if p.stop == "trail_w":
            if side > 0:
                run_ext = max(run_ext, h)
                stop = max(stop, floor_tick(run_ext - W, tick))
            else:
                run_ext = min(run_ext, lo)
                stop = min(stop, ceil_tick(run_ext + W, tick))
        cum_qv += mkt.quote_volume[j]
        cum_v += mkt.volume[j]
        if p.exit_event == "reenter_1m":
            pending_event = c < H if side > 0 else c > L
        elif p.exit_event == "reenter_15m":
            if (j - i0 + 1) % 15 == 0:
                pending_event = c < H if side > 0 else c > L
        elif p.exit_event == "vwap" and cum_v > 0:
            vwap = cum_qv / cum_v
            pending_event = c < vwap if side > 0 else c > vwap
        j += 1


def _exit(px: float, idx: int, reason: str, flags: list[str], mae: float, mfe: float) -> dict:
    return {"exit_idx": int(idx), "exit_px": float(px), "exit_reason": reason,
            "flags": "|".join(dict.fromkeys(flags)), "mae_bp": mae, "mfe_bp": mfe}


def funding_bp(mkt: Market, side: int, fill_idx: int, exit_idx: int, fill_px: float) -> float:
    """Settlements whose minute lies in [fill, exit] inclusive, marked at that minute's price."""
    lo = np.searchsorted(mkt.funding_idx, fill_idx, side="left")
    hi = np.searchsorted(mkt.funding_idx, exit_idx, side="right")
    total = 0.0
    for f, rate in zip(mkt.funding_idx[lo:hi], mkt.funding_rate[lo:hi]):
        f = int(f)
        while f > fill_idx and not mkt.ok(f):
            f -= 1
        mark = mkt.open[f] if mkt.ok(f) else fill_px
        total += -side * rate * (mark / fill_px) * 1e4
    return total


# --- one session ------------------------------------------------------------------------------

def evaluate_session(mkt: Market, s, feat: dict, p: Policy, busy_until: int, end_idx: int,
                     rng: np.random.Generator | None = None) -> dict:
    row = {"date": s.date, "t0_ms": int(s.t0_ms), "policy": p.id, "symbol": mkt.symbol,
           "early_close": bool(s.early_close), "status": "no_trade", "reason": None}
    if not feat["valid"]:
        return {**row, "status": "invalid", "reason": feat["invalid_reason"]}
    i0 = int(feat["i0"])
    H, L, W = feat["H"], feat["L"], feat["W"]
    row.update(H=H, L=L, W=W, range_open=feat["range_open"], range_close=feat["range_close"],
               relvol=feat["relvol"], w_rel=feat["w_rel"])
    if i0 <= busy_until:
        return {**row, "reason": "position_open"}
    if p.relvol_min is not None:
        if not np.isfinite(feat["relvol"]):
            return {**row, "reason": "warmup_relvol"}
        if not feat["relvol"] > p.relvol_min:
            return {**row, "reason": "filter_relvol"}
    if p.width_band:
        if not np.isfinite(feat["w_p20"]):
            return {**row, "reason": "warmup_width"}
        if not (feat["w_p20"] <= feat["w_rel"] <= feat["w_p80"]):
            return {**row, "reason": "filter_width"}
    candle = int(np.sign(feat["range_close"] - feat["range_open"]))
    gate = 0
    if p.direction_gate or p.entry == "momentum":
        if candle == 0:
            return {**row, "reason": "doji"}
        gate = candle

    start = i0 + p.range_min
    deadline = i0 + p.deadline_min
    tick = mkt.tick(start)
    entries = []                                   # (side, trigger_idx, fill_idx, fill_px, intrabar)
    if p.entry in ("close", "fade"):
        k, side = _close_trigger(mkt, i0, H, L, W, p, gate)
        if k is None:
            return {**row, "reason": "no_trigger"}
        s_idx = k + 1 + p.latency_min
        row.update(trigger_idx=k, trigger_side=side)
        if p.entry == "fade":
            side = -side
        if not mkt.ok(s_idx):
            return {**row, "reason": "missing_fill_bar"}
        stop_px = _stop_level(side, H, L, W, p, tick)
        prev = s_idx - 1
        while prev > k and not mkt.ok(prev):
            prev -= 1
        last_seen = mkt.close[prev]
        if (side > 0 and last_seen <= stop_px) or (side < 0 and last_seen >= stop_px):
            return {**row, "reason": "beyond_stop_pre_submit"}
        entries.append((side, k, s_idx, float(mkt.open[s_idx]), False))
    elif p.entry == "stop":
        buy, sell = H + tick, L - tick
        for k in range(start, deadline):
            if not mkt.ok(k):
                continue
            up = mkt.high[k] >= buy and gate >= 0
            down = mkt.low[k] <= sell and gate <= 0
            if up:
                entries.append((1, k, k, float(max(buy, mkt.open[k])), mkt.open[k] < buy))
            if down:
                entries.append((-1, k, k, float(min(sell, mkt.open[k])), mkt.open[k] > sell))
            if entries:
                break
        if not entries:
            return {**row, "reason": "no_trigger"}
        row.update(trigger_idx=entries[0][1], trigger_side=entries[0][0] if len(entries) == 1 else 0)
    elif p.entry in ("momentum", "clock_long", "clock_short"):
        side = {"momentum": candle, "clock_long": 1, "clock_short": -1}[p.entry]
        if not mkt.ok(start):
            return {**row, "reason": "missing_fill_bar"}
        entries.append((side, start, start, float(mkt.open[start]), False))
        row.update(trigger_idx=start, trigger_side=side)
    else:
        raise ValueError(f"unknown entry {p.entry}")

    outcomes = []
    for side, k, f_idx, fill_px, intrabar in entries:
        stop_px = _stop_level(side, H, L, W, p, tick)
        risk = (fill_px - stop_px) * side
        if p.entry == "fade":
            target_px = (H + L) / 2
        elif p.target_r is not None and risk > 0:
            target_px = fill_px + side * p.target_r * risk
        else:
            target_px = None
        if risk <= 0:                              # filled through the stop: immediate liquidation
            ex = _exit(fill_px, f_idx, "fill_through_stop", [], 0.0, 0.0)
        else:
            ex = walk(mkt, side, f_idx, fill_px, stop_px, target_px, intrabar, p, H, L, W, i0, end_idx)
        gross = side * (ex["exit_px"] - fill_px) / fill_px * 1e4
        outcomes.append({"side": side, "fill_idx": f_idx, "fill_px": fill_px, "stop_px": stop_px,
                         "target_px": target_px, "risk_bp": risk / fill_px * 1e4, **ex, "gross_bp": gross})
    trade = min(outcomes, key=lambda o: o["gross_bp"])   # one outcome unless the entry bar was ambiguous
    if len(outcomes) > 1:
        trade["flags"] = "|".join(filter(None, [trade["flags"], "ambiguous_both_boundaries"]))
    trade["funding_bp"] = funding_bp(mkt, trade["side"], trade["fill_idx"], trade["exit_idx"], trade["fill_px"])
    trade["fill_utc"] = pd.Timestamp(mkt.time_ms(trade["fill_idx"]), unit="ms", tz="UTC")
    trade["exit_utc"] = pd.Timestamp(mkt.time_ms(trade["exit_idx"]), unit="ms", tz="UTC")
    trade["bars_held"] = trade["exit_idx"] - trade["fill_idx"]
    return {**row, "status": "trade", "reason": None, **trade}


def run_policy(mkt: Market, sessions: pd.DataFrame, p: Policy, block_start: str, block_end: str,
               include_early_close: bool = False) -> pd.DataFrame:
    """Every session of the block (inclusive ISO dates), in order, trades and non-trades alike.

    Range features use all sessions passed in (warmup before block_start is allowed);
    positions are censored at the end of block_end (UTC) and never carried into the next block.
    Early-close exchange sessions are dropped before the rolling baselines are built, so
    they neither trade nor enter a filter's history, unless include_early_close is set.
    """
    if not include_early_close:
        sessions = sessions[~sessions["early_close"]].reset_index(drop=True)
    feats = range_features(mkt, sessions, p)
    end_ms = int(datetime.fromisoformat(block_end).replace(tzinfo=timezone.utc).timestamp() * 1000) + DAY_MIN * MINUTE_MS
    end_idx = min(len(mkt), mkt.index(end_ms))
    busy_until = -1
    rows = []
    for k, s in enumerate(sessions.itertuples(index=False)):
        if not (block_start <= s.date <= block_end):
            continue
        row = evaluate_session(mkt, s, feats.iloc[k].to_dict(), p, busy_until, end_idx)
        if row["status"] == "trade":
            busy_until = row["exit_idx"]
        rows.append(row)
    return pd.DataFrame(rows)

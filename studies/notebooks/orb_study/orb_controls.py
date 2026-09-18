"""Randomized and buy-and-hold controls (PREREGISTRATION.md section 4).

Random direction: P0's own trade sessions and fills, a seeded coin-flip side, the stop at
P0's risk distance on that side, session time exit.
Random time: P0's trade sessions, fill at a seeded uniform minute in [anchor+15, anchor+120),
coin-flip side, stop one range width away rounded outward, session time exit.
Both walk with orb_signals.walk_fixed_stop, which orb_parity.py checks against the reference walk.

Buy-and-hold: the session hold (long from the first post-range open to the session exit, no
stop) on every eligible session, and the calendar perpetual long (daily close to close with
funding) used for beta.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import orb_engine as eng
import orb_metrics as met
import orb_signals as sig


def _funding(mkt: eng.Market, side: np.ndarray, fill_idx: np.ndarray, exit_idx: np.ndarray, fill_px: np.ndarray) -> np.ndarray:
    return np.array([eng.funding_bp(mkt, int(s), int(f), int(e), float(p))
                     for s, f, e, p in zip(side, fill_idx, exit_idx, fill_px)])


def _net(gross: np.ndarray, funding: np.ndarray, exit_px: np.ndarray, fill_px: np.ndarray, scenario: str) -> np.ndarray:
    c = met.COSTS[scenario]
    return gross if c is None else gross + funding - c * (1 + exit_px / fill_px)


def random_direction(mkt: eng.Market, p0_trades: pd.DataFrame, n_seeds: int, seed_base: int,
                     scenarios=("gross", "db")) -> pd.DataFrame:
    tr = p0_trades.reset_index(drop=True)
    fill_idx = tr["fill_idx"].to_numpy(np.int64)
    fill_px = tr["fill_px"].to_numpy(float)
    dist = (tr["fill_px"] - tr["stop_px"]).abs().to_numpy(float)
    i0 = np.array([mkt.index(t) for t in tr["t0_ms"]], dtype=np.int64)
    rows = []
    for k in range(n_seeds):
        rng = np.random.default_rng(seed_base + k)
        side = rng.choice(np.array([-1, 1]), size=len(tr))
        w = sig.walk_fixed_stop(mkt, side, fill_idx, fill_px, fill_px - side * dist, i0 + 390)
        fund = _funding(mkt, side, fill_idx, w["exit_idx"], fill_px)
        row = {"seed": seed_base + k, "trades": len(tr)}
        for sc in scenarios:
            row[f"mean_bp_{sc}"] = float(_net(w["gross_bp"], fund, w["exit_px"], fill_px, sc).mean())
        rows.append(row)
    return pd.DataFrame(rows)


def random_time(mkt: eng.Market, p0_trades: pd.DataFrame, n_seeds: int, seed_base: int,
                scenarios=("gross", "db")) -> pd.DataFrame:
    tr = p0_trades.reset_index(drop=True)
    i0 = np.array([mkt.index(t) for t in tr["t0_ms"]], dtype=np.int64)
    W = tr["W"].to_numpy(float)
    rows = []
    for k in range(n_seeds):
        rng = np.random.default_rng(seed_base + 10_000 + k)
        entry = i0 + rng.integers(15, 120, size=len(tr))
        side = rng.choice(np.array([-1, 1]), size=len(tr))
        ok = ~np.isnan(mkt.close[entry])
        e, s = entry[ok], side[ok]
        fill_px = mkt.open[e]
        tick = np.where(e < mkt.tick_change_idx, mkt.tick_before, mkt.tick_after)
        raw = fill_px - s * W[ok]
        stop = np.where(s > 0, np.floor(raw / tick + 1e-9) * tick, np.ceil(raw / tick - 1e-9) * tick)
        w = sig.walk_fixed_stop(mkt, s, e, fill_px, stop, i0[ok] + 390)
        fund = _funding(mkt, s, e, w["exit_idx"], fill_px)
        row = {"seed": seed_base + 10_000 + k, "trades": int(ok.sum())}
        for sc in scenarios:
            row[f"mean_bp_{sc}"] = float(_net(w["gross_bp"], fund, w["exit_px"], fill_px, sc).mean())
        rows.append(row)
    return pd.DataFrame(rows)


def session_hold(mkt: eng.Market, ledger_all_sessions: pd.DataFrame, scenario: str = "db") -> pd.Series:
    """Long from anchor+15 open to the session-exit open on every valid session; net bp by date."""
    valid = ledger_all_sessions[ledger_all_sessions["status"] != "invalid"]
    out = {}
    for r in valid.itertuples(index=False):
        i0 = mkt.index(r.t0_ms)
        a, b = i0 + 15, i0 + 390
        while b < len(mkt) and not mkt.ok(b):
            b += 1
        if not (mkt.ok(a) and mkt.ok(b)):
            continue
        gross = (mkt.open[b] - mkt.open[a]) / mkt.open[a] * 1e4
        fund = eng.funding_bp(mkt, 1, a, b, float(mkt.open[a]))
        c = met.COSTS[scenario]
        out[r.date] = gross if c is None else gross + fund - c * (1 + mkt.open[b] / mkt.open[a])
    return pd.Series(out, dtype=float)


def calendar_hold(mkt: eng.Market, start: str, end: str) -> pd.Series:
    """Daily return of a 1x perpetual long, close at 23:59 UTC to close at 23:59 UTC, funding included."""
    days = pd.date_range(start, end, freq="D")
    out = {}
    for d in days:
        e = mkt.index(int(d.timestamp() * 1000)) + 1439
        s = e - 1440
        while s > 0 and not mkt.ok(s):
            s -= 1
        while e > s and not mkt.ok(e):
            e -= 1
        if s < 0 or e >= len(mkt):
            continue
        fund = eng.funding_bp(mkt, 1, s + 1, e, float(mkt.close[s])) / 1e4
        out[d.strftime("%Y-%m-%d")] = mkt.close[e] / mkt.close[s] - 1 + fund
    return pd.Series(out, dtype=float)

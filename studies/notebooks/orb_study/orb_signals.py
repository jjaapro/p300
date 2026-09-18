"""Independent vectorized ORB implementation, used only to check orb_engine.py.

Written separately from the reference loop (array windows instead of a bar-by-bar state
machine) for the policies whose exits are a fixed stop plus a time exit: close or
resting-stop entries, opposite or midpoint stops, buffers, the direction gate, the
relative-volume and range-width filters, deadlines, latency and the session / entry+60
time exits. Targets, trailing stops, event exits and multi-day holds exist only in the
reference engine and are covered by its synthetic fixtures.

`walk_fixed_stop` is also the fast path for the randomized controls, which need hundreds
of seeded repetitions; it is checked against the reference walk on the same entries.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from orb_engine import Market, Policy

EXTRA = 30          # bars searched past the time-exit minute when that bar is missing


def _window(a: np.ndarray, idx: np.ndarray) -> np.ndarray:
    inside = (idx >= 0) & (idx < len(a))
    out = np.full(idx.shape, np.nan)
    out[inside] = a[idx[inside]]
    return out


def _ticks(mkt: Market, i: np.ndarray) -> np.ndarray:
    return np.where(i < mkt.tick_change_idx, mkt.tick_before, mkt.tick_after)


def _round_stop(px: np.ndarray, side: np.ndarray, tick: np.ndarray) -> np.ndarray:
    down = np.floor(px / tick + 1e-9) * tick
    up = np.ceil(px / tick - 1e-9) * tick
    return np.where(side > 0, down, up)


def walk_fixed_stop(mkt: Market, side: np.ndarray, fill_idx: np.ndarray, fill_px: np.ndarray,
                    stop_px: np.ndarray, time_idx: np.ndarray, intrabar: np.ndarray | None = None) -> dict:
    """First stop (gap at open, else touch) before time_idx; otherwise exit at the first
    tradable open at/after time_idx. Arrays are one row per trade."""
    n = len(side)
    side = np.asarray(side, dtype=np.int64)
    fill_idx = np.asarray(fill_idx, dtype=np.int64)
    time_idx = np.asarray(time_idx, dtype=np.int64)
    span = int((time_idx - fill_idx).max()) + EXTRA + 1
    cols = np.arange(span)
    idx = fill_idx[:, None] + cols[None, :]
    O, Hh, Ll, C = (_window(a, idx) for a in (mkt.open, mkt.high, mkt.low, mkt.close))
    ok = ~np.isnan(C)
    s = side[:, None]
    st = stop_px[:, None]
    before_time = idx < time_idx[:, None]
    gap = (cols[None, :] > 0) & ok & before_time & np.where(s > 0, O <= st, O >= st)
    touch = ok & before_time & np.where(s > 0, Ll <= st, Hh >= st)
    hit = gap | touch
    has = hit.any(axis=1)
    first = hit.argmax(axis=1)
    rows = np.arange(n)
    is_gap = gap[rows, first]
    after = ok & ~before_time
    t_first = after.argmax(axis=1)
    t_has = after.any(axis=1)
    exit_col = np.where(has, first, np.where(t_has, t_first, -1))
    exit_px = np.where(has, np.where(is_gap, O[rows, first], stop_px), O[rows, np.maximum(t_first, 0)])
    reason = np.where(has, np.where(is_gap, "stop_gap", "stop"), np.where(t_has, "time", "unresolved"))
    risk = side * (fill_px - stop_px)
    through = risk <= 0
    exit_col = np.where(through, 0, exit_col)
    exit_px = np.where(through, fill_px, exit_px)
    reason = np.where(through, "fill_through_stop", reason)
    exit_idx = fill_idx + exit_col
    gross = side * (exit_px - fill_px) / fill_px * 1e4
    ambiguous = np.zeros(n, dtype=bool) if intrabar is None else intrabar & has & (first == 0)
    return {"exit_idx": exit_idx, "exit_px": exit_px, "exit_reason": reason, "gross_bp": gross,
            "ambiguous_fill_bar_stop": ambiguous}


def vector_policy(mkt: Market, sessions: pd.DataFrame, p: Policy, block_start: str, block_end: str) -> pd.DataFrame:
    if p.target_r is not None or p.exit_event or p.stop == "trail_w" or p.time_exit == "none" \
            or p.entry not in ("close", "stop"):
        raise ValueError(f"{p.id}: not covered by the vectorized implementation")
    s = sessions[~sessions["early_close"]].reset_index(drop=True)
    i0 = ((s["t0_ms"].to_numpy(np.int64) - mkt.t0_ms) // 60_000).astype(np.int64)
    m, D, S = p.range_min, p.deadline_min, p.session_min
    inside = (i0 >= 0) & (i0 + S < len(mkt))
    rng_idx = i0[:, None] + np.arange(m)[None, :]
    rH, rL, rC, rO = (_window(a, rng_idx) for a in (mkt.high, mkt.low, mkt.close, mkt.open))
    rV = _window(mkt.volume, rng_idx)
    complete = inside & ~np.isnan(rC).any(axis=1)
    with np.errstate(invalid="ignore"):
        H = np.where(complete, np.nanmax(np.where(complete[:, None], rH, 0.0), axis=1), np.nan)
        L = np.where(complete, np.nanmin(np.where(complete[:, None], rL, 0.0), axis=1), np.nan)
    W = H - L
    valid = complete & (W > 0)
    r_open, r_close = rO[:, 0], rC[:, m - 1]
    r_vol = np.nansum(rV, axis=1)

    # rolling baselines over the sequence of valid sessions, current one excluded
    v = np.flatnonzero(valid)
    relvol = np.full(len(s), np.nan)
    w_lo = np.full(len(s), np.nan)
    w_hi = np.full(len(s), np.nan)
    if len(v) > 20:
        csum = np.concatenate([[0.0], np.cumsum(r_vol[v])])
        k = np.arange(20, len(v))
        relvol[v[k]] = r_vol[v[k]] / ((csum[k] - csum[k - 20]) / 20)
    w_rel = W / r_open
    if len(v) > 60:
        wins = np.lib.stride_tricks.sliding_window_view(w_rel[v], 60)[:-1]   # window ending before k
        w_lo[v[60:]] = np.quantile(wins, 0.20, axis=1)
        w_hi[v[60:]] = np.quantile(wins, 0.80, axis=1)

    in_block = ((s["date"] >= block_start) & (s["date"] <= block_end)).to_numpy()
    eligible = valid & in_block
    if p.relvol_min is not None:
        eligible &= np.isfinite(relvol) & (relvol > p.relvol_min)
    if p.width_band:
        eligible &= np.isfinite(w_lo) & (w_rel >= w_lo) & (w_rel <= w_hi)
    candle = np.sign(r_close - r_open)
    gate_up = np.ones(len(s), dtype=bool)
    gate_dn = np.ones(len(s), dtype=bool)
    if p.direction_gate:
        eligible &= candle != 0
        gate_up, gate_dn = candle > 0, candle < 0
    tick = _ticks(mkt, i0 + m)
    rows = np.arange(len(s))

    if p.entry == "close":
        lat = p.latency_min
        kcols = np.arange(m, D - 1 - lat)
        C = _window(mkt.close, i0[:, None] + kcols[None, :])
        up = (C > (H + p.buffer_w * W)[:, None]) & gate_up[:, None]
        dn = (C < (L - p.buffer_w * W)[:, None]) & gate_dn[:, None]
        trig = up | dn
        has = trig.any(axis=1) & eligible
        first = trig.argmax(axis=1)
        side = np.where(up[rows, first], 1, -1)
        k = i0 + kcols[first]
        fill_idx = k + 1 + lat
        fill_px = _window(mkt.open, fill_idx)
        missing_fill = np.isnan(_window(mkt.close, fill_idx))
        stop_raw = np.where(side > 0, L, H) if p.stop == "opposite" else (H + L) / 2
        stop_px = _round_stop(stop_raw, side, tick)
        # most recent completed close before submission (the trigger close when latency is 0)
        back = np.arange(lat + 1)
        prev_idx = fill_idx[:, None] - 1 - back[None, :]
        prev_c = _window(mkt.close, prev_idx)
        last_seen = prev_c[rows, np.argmax(~np.isnan(prev_c), axis=1)]
        beyond = np.where(side > 0, last_seen <= stop_px, last_seen >= stop_px)
        trade = has & ~missing_fill & ~beyond
        intrabar = np.zeros(len(s), dtype=bool)
        candidates = [(side, fill_idx, fill_px, stop_px, intrabar)]
        trigger_idx = k
    else:
        kcols = np.arange(m, D)
        kidx = i0[:, None] + kcols[None, :]
        Hk, Lk, Ok = _window(mkt.high, kidx), _window(mkt.low, kidx), _window(mkt.open, kidx)
        buy, sell = (H + tick), (L - tick)
        up = (Hk >= buy[:, None]) & gate_up[:, None]
        dn = (Lk <= sell[:, None]) & gate_dn[:, None]
        trig = up | dn
        has = trig.any(axis=1) & eligible
        first = trig.argmax(axis=1)
        fill_idx = i0 + kcols[first]
        o_first = Ok[rows, first]
        both = up[rows, first] & dn[rows, first]
        long_side, short_side = np.ones(len(s), dtype=int), -np.ones(len(s), dtype=int)
        long_c = (long_side, fill_idx, np.maximum(buy, o_first),
                  _round_stop(L if p.stop == "opposite" else (H + L) / 2, long_side, tick), o_first < buy)
        short_c = (short_side, fill_idx, np.minimum(sell, o_first),
                   _round_stop(H if p.stop == "opposite" else (H + L) / 2, short_side, tick), o_first > sell)
        candidates = [long_c, short_c]
        trade = has
        trigger_idx = fill_idx

    session_end = i0 + S
    results = []
    for cand_side, f_idx, f_px, st_px, intrabar in candidates:
        t_idx = session_end if p.time_exit == "session" else np.minimum(f_idx + 60, session_end)
        sel = np.flatnonzero(trade)
        w = walk_fixed_stop(mkt, cand_side[sel], f_idx[sel], f_px[sel], st_px[sel], t_idx[sel], intrabar[sel])
        full = {key: np.full(len(s), np.nan, dtype=object) for key in w}
        for key, val in w.items():
            full[key][sel] = val
        results.append((cand_side, f_idx, f_px, st_px, full))

    out_rows = []
    for r in np.flatnonzero(trade):
        if p.entry == "close":
            cside, cf, cpx, cst, res = results[0]
            chosen = (cside[r], cf[r], cpx[r], cst[r], {k2: v2[r] for k2, v2 in res.items()})
            flags = "ambiguous_fill_bar_stop" if chosen[4]["ambiguous_fill_bar_stop"] else ""
        else:
            opts = []
            if up[r, first[r]]:
                cs, cf, cpx, cst, res = results[0]
                opts.append((cs[r], cf[r], cpx[r], cst[r], {k2: v2[r] for k2, v2 in res.items()}))
            if dn[r, first[r]]:
                cs, cf, cpx, cst, res = results[1]
                opts.append((cs[r], cf[r], cpx[r], cst[r], {k2: v2[r] for k2, v2 in res.items()}))
            chosen = min(opts, key=lambda o: o[4]["gross_bp"])
            flags = "ambiguous_fill_bar_stop" if chosen[4]["ambiguous_fill_bar_stop"] else ""
            if both[r]:
                flags = "|".join(filter(None, [flags, "ambiguous_both_boundaries"]))
        side_r, fidx_r, fpx_r, st_r, res_r = chosen
        out_rows.append({"date": s.loc[r, "date"], "H": H[r], "L": L[r], "trigger_idx": int(trigger_idx[r]),
                         "side": int(side_r), "fill_idx": int(fidx_r), "fill_px": float(fpx_r),
                         "stop_px": float(st_r), "exit_idx": int(res_r["exit_idx"]),
                         "exit_px": float(res_r["exit_px"]), "exit_reason": res_r["exit_reason"],
                         "gross_bp": float(res_r["gross_bp"]), "flags": flags})
    return pd.DataFrame(out_rows)

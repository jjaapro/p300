"""Experiment (1): does a bounded DCA ladder rescue DEEP dwellblock bidding?

v2 showed chento bids DEEP (at/below the zone POC) while the naive single-entry
sweep said SHALLOW pays. The reconciliation hypothesis (from the user's
observation): he bids the zone deep, DCAs across it, takes a real structural
stop if the zone fails, then re-enters at the NEXT dwellblock down. So a deep
bid is an anchor for a bounded ladder with a hard stop — not a naked limit.

This module tests, on the TRIPLE_V3 edge signal, holding the structural stop +
3R target fixed:
    market        depth 0.0, single entry            (reference)
    shallow       depth 0.2, single entry            (the sweep's winner)
    deep_single   depth 1.0 at the POC, single entry (the sweep's loser)
    deep_ladder   POC + add at the zone low, hard stop just below the zone
    cascade       deep_ladder on zone1; if stopped, re-enter deep_ladder on the
                  next support zone down; up to MAX_ATTEMPTS (his 3-strike cap)

R is normalised to the INITIAL rung's risk, so a ladder amplifies BOTH ways:
adding near the stop costs little extra risk (rung sits just above the stop) but
catches the full bounce from a better average — exactly the DCA asymmetry.

Run: python studies/notebooks/dwell_block/experiment_ladder.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve()
ROOT = HERE
while not (ROOT / "data" / "databases" / "prod.db").exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))
from studies.notebooks.dwell_block.dwellblock import (  # noqa: E402
    load_btc_5s, resample, detect_dwellblocks)
from studies.notebooks.dwell_block.experiment_abc import _atr_15m, _dedupe  # noqa: E402
from studies.notebooks.chento_journal.validation_group_A_redo import (  # noqa: E402
    build_optimized_triggers)

EXEC_TF = "15min"; LOOKBACK = 192
ENTRY_TIF = 17_280; TRADE_TIF = 51_840      # 24h fill / 72h hold, in 5s bars
TARGET_R = 3.0; STOP_BUF_ATR = 0.25
SHALLOW_DEPTH = 0.20
MAX_DIST_ATR = 6.0; MIN_DIST_ATR = 0.20
COOLDOWN_H = 4; COST_BP = 10.0
MAX_ATTEMPTS = 3                            # his 3-strike cap for the cascade
BIG = 10 ** 9


def simulate(low, high, close, start_idx, direction, rungs, hard_stop, target,
             *, market_first, n):
    """One laddered attempt. rungs=[(price,size),...]; rungs[0] is the anchor.
    Returns dict with r (normalised to rung0 risk), kind, exit_idx, size."""
    r0_price = rungs[0][0]
    r0_risk = abs(r0_price - hard_stop)
    if r0_risk <= 0:
        return None
    if market_first:
        idx0 = start_idx
    else:
        e_end = min(start_idx + ENTRY_TIF, n - 1)
        seg = (low if direction == "long" else high)[start_idx + 1:e_end + 1]
        cond = (seg <= r0_price) if direction == "long" else (seg >= r0_price)
        if not cond.any():
            return {"filled": False}
        idx0 = start_idx + 1 + int(np.argmax(cond))

    t_end = min(idx0 + TRADE_TIF, n - 1)
    lo2, hi2 = low[idx0 + 1:t_end + 1], high[idx0 + 1:t_end + 1]
    if lo2.size == 0:
        return {"filled": False}

    def fi(c):
        return int(np.argmax(c)) if c.any() else BIG
    if direction == "long":
        stop_i, tgt_i = fi(lo2 <= hard_stop), fi(hi2 >= target)
    else:
        stop_i, tgt_i = fi(hi2 >= hard_stop), fi(lo2 <= target)
    exit_i = min(stop_i, tgt_i)

    fills = [rungs[0]]
    for rp, rs in rungs[1:]:
        ri = fi(lo2 <= rp) if direction == "long" else fi(hi2 >= rp)
        if ri <= exit_i and ri < BIG:
            fills.append((rp, rs))

    if stop_i == BIG and tgt_i == BIG:
        exit_px, kind, ei = float(close[t_end]), "tif", t_end - idx0
    elif stop_i <= tgt_i:
        exit_px, kind, ei = hard_stop, "stop", stop_i
    else:
        exit_px, kind, ei = target, "target", tgt_i
    pnl = sum(s * ((exit_px - p) if direction == "long" else (p - exit_px))
              for p, s in fills)
    cost = (COST_BP / 10_000.0) * sum(s * p for p, s in fills) / r0_risk
    return {"filled": True, "r": pnl / r0_risk - cost, "kind": kind,
            "size": sum(s for _, s in fills), "exit_global": idx0 + 1 + ei}


def main():
    print("Loading edge (triple) triggers + 5s spot...")
    trigs = build_optimized_triggers()[0].copy()
    trigs["ts"] = pd.to_datetime(trigs["ts"], utc=True)

    import gc, sqlite3
    gc.collect()
    # Lean chunked load: only ts/high/low/close (float32) for the fill sim, and
    # build the 15m frame for detection incrementally so we never hold a 6.3M-row
    # DataFrame alongside the 7y-of-features the trigger build already allocated.
    con = sqlite3.connect(str(ROOT / "data" / "databases" / "prod.db"))
    ts_parts, hi_parts, lo_parts, cl_parts = [], [], [], []
    b15: dict = {}
    for ch in pd.read_sql("SELECT timestamp, high, low, close FROM cd_spot_5s "
                          "ORDER BY timestamp", con, chunksize=1_000_000):
        t = ch["timestamp"].to_numpy(); h = ch["high"].to_numpy()
        l = ch["low"].to_numpy(); c = ch["close"].to_numpy()
        ts_parts.append(t.astype("int64")); hi_parts.append(h.astype("float32"))
        lo_parts.append(l.astype("float32")); cl_parts.append(c.astype("float32"))
        cdf = pd.DataFrame({"b": t // 900, "h": h, "l": l, "c": c}).groupby("b").agg(
            h=("h", "max"), l=("l", "min"), c=("c", "last"))
        for b, hh, ll, cc in zip(cdf.index.to_numpy(), cdf["h"].to_numpy(),
                                 cdf["l"].to_numpy(), cdf["c"].to_numpy()):
            if b in b15:
                e = b15[b]; e[0] = max(e[0], hh); e[1] = min(e[1], ll); e[2] = cc
            else:
                b15[b] = [hh, ll, cc]
    con.close()
    ts_arr = np.concatenate(ts_parts)
    low = np.concatenate(lo_parts); high = np.concatenate(hi_parts); close = np.concatenate(cl_parts)
    del ts_parts, hi_parts, lo_parts, cl_parts; gc.collect()
    n = len(close); t_min, t_max = int(ts_arr[0]), int(ts_arr[-1])
    buckets = sorted(b15)
    df15 = pd.DataFrame([b15[b] for b in buckets], columns=["high", "low", "close"])
    df15.index = pd.to_datetime(np.array(buckets) * 900, unit="s", utc=True)
    df15["volume"] = 0.0
    atr15 = _atr_15m(df15)
    del b15; gc.collect()

    need_back, need_fwd = LOOKBACK * 900, (ENTRY_TIF + TRADE_TIF) * 5
    tsec = trigs["ts"].apply(lambda x: int(x.timestamp()))
    trigs = trigs[(tsec >= t_min + need_back) & (tsec <= t_max - need_fwd)].copy()
    trigs = _dedupe(trigs, COOLDOWN_H)
    print(f"  {len(trigs)} triggers in window")

    rows = []; skip = {"no_zone": 0, "too_far": 0, "too_close": 0}
    for _, tr in trigs.iterrows():
        ts, direction = tr["ts"], tr["direction"]
        si = int(np.searchsorted(ts_arr, ts.value // 10**9, side="left"))
        if si >= n:
            continue
        P = float(close[si])
        a = float(atr15.asof(ts)) if not pd.isna(atr15.asof(ts)) else np.nan
        if not np.isfinite(a) or a <= 0:
            continue
        zones = detect_dwellblocks(df15.loc[:ts].iloc[-LOOKBACK:], top_k=8)
        # stack of support zones in the pullback direction (nearest first)
        if direction == "long":
            stack = sorted([z for z in zones if z.poc < P], key=lambda z: -z.poc)
        else:
            stack = sorted([z for z in zones if z.poc > P], key=lambda z: z.poc)
        if not stack:
            skip["no_zone"] += 1; continue
        z1 = stack[0]; dist = abs(P - z1.poc)
        if dist > MAX_DIST_ATR * a:
            skip["too_far"] += 1; continue
        if dist < MIN_DIST_ATR * a:
            skip["too_close"] += 1; continue

        sgn = 1 if direction == "long" else -1

        def stop_of(z):
            return z.lo - STOP_BUF_ATR * a if direction == "long" else z.hi + STOP_BUF_ATR * a

        def tgt_from(price, hard_stop):
            return price + sgn * TARGET_R * abs(price - hard_stop)

        rec = {"ts": ts, "dir": direction}
        # ── single-attempt arms on zone1 ──
        hs1 = stop_of(z1)
        edge1 = z1.lo if direction == "long" else z1.hi      # ladder add at the zone's far edge
        sh = P - sgn * SHALLOW_DEPTH * abs(P - z1.poc)
        arms = {
            "market":      ([(P, 1.0)],            True,  tgt_from(P, hs1)),
            "shallow0.2":  ([(sh, 1.0)],           False, tgt_from(sh, hs1)),
            "deep_single": ([(z1.poc, 1.0)],       False, tgt_from(z1.poc, hs1)),
            "deep_ladder": ([(z1.poc, 1.0), (edge1, 1.0)], False, tgt_from(z1.poc, hs1)),
        }
        for name, (rungs, mkt, target) in arms.items():
            res = simulate(low, high, close, si, direction, rungs, hs1, target,
                           market_first=mkt, n=n)
            if res:
                rec[name] = res.get("r") if res.get("filled") else None
                rec[name + "_fill"] = res.get("filled", False)

        # ── cascade: deep_ladder on zone1; if stopped, re-enter next zone down ──
        casc_r = 0.0; cur = si; attempts = 0; filled_any = False
        for z in stack[:MAX_ATTEMPTS]:
            d = abs(float(close[min(cur, n - 1)]) - z.poc)
            if d > MAX_DIST_ATR * a:
                break
            hs = stop_of(z)
            edge = z.lo if direction == "long" else z.hi
            res = simulate(low, high, close, cur, direction,
                           [(z.poc, 1.0), (edge, 1.0)], hs, tgt_from(z.poc, hs),
                           market_first=False, n=n)
            if not res or not res.get("filled"):
                break
            attempts += 1; filled_any = True; casc_r += res["r"]
            if res["kind"] == "stop":
                cur = res["exit_global"]          # re-enter after the stop
                continue
            break                                  # target/tif -> done
        rec["cascade"] = casc_r if filled_any else None
        rec["cascade_attempts"] = attempts
        rows.append(rec)

    r = pd.DataFrame(rows)
    print(f"\nskips: {skip}   usable signals: {len(r)}\n")
    print(f'{"arm":<14}{"n":>5}{"fill%":>8}{"meanR":>9}{"WR":>7}{"adverse%":>10}{"exp/sig":>9}')
    for arm in ["market", "shallow0.2", "deep_single", "deep_ladder", "cascade"]:
        if arm not in r:
            continue
        fillcol = arm + "_fill"
        filled = r[r[arm].notna()]
        fr = (r[fillcol].mean() if fillcol in r else filled.shape[0] / len(r))
        mr = filled[arm].mean()
        exp = fr * mr if np.isfinite(mr) else np.nan
        wr = (filled[arm] > 0).mean()
        adv = (filled[arm] <= 0).mean()
        print(f'{arm:<14}{len(filled):>5}{fr:>8.0%}{mr:>+9.3f}{wr:>7.0%}'
              f'{adv:>10.0%}{exp:>+9.3f}')
    casc = r[r["cascade"].notna()]
    print(f'\ncascade attempts: mean {casc["cascade_attempts"].mean():.2f}, '
          f'dist {casc["cascade_attempts"].value_counts().sort_index().to_dict()}')
    out = ROOT / "studies" / "notebooks" / "dwell_block" / "ladder_results.csv"
    r.to_csv(out, index=False)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

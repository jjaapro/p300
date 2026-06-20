"""A/B/C dwell-entry experiment — does staging the limit INTO a dwellblock beat
entering early or at market?

Holds the SIGNAL constant (B1 money-flow divergence triggers — the anchor of the
TRIPLE_V3 composite; full OKX/SMC filters are a later refinement) and varies only
the ENTRY mechanic. Everything is simulated in SPOT 5s terms (cd_spot_5s); the
trigger only contributes (timestamp, direction).

Three arms, monotonic in pullback depth toward the nearest dwellblock:
    C  market-on-touch  — enter now at the signal price        (0%   pullback)
    A  early limit      — bid a shallow pullback, "too early"  (40%  pullback)
    B  dwell limit      — bid the full pullback into the zone  (100% pullback, at POC)

Risk is structural and COMMON across arms: stop just beyond the dwellblock's far
edge (faithful to his "invalidation = a specific price"). Each arm's risk =
|entry - stop|, target = entry +/- TARGET_R * risk. A better entry (B) therefore
carries a tighter stop and reaches target sooner — which is exactly the edge the
tactic claims.

Fill model is bracketed from the 5s tape:
    touch    — a 5s bar's low<=limit (long): optimistic, overstates fills
    through  — a 5s bar's low< limit (long): price actually traded through

The headline number is per-signal expectancy = fill_rate * mean_R|filled (a
missed limit contributes 0). The dwell hypothesis predicts B beats A on
adverse-fill rate and R|filled; whether B beats C overall is the fill-rate tradeoff.

Run: python studies/notebooks/dwell_block/experiment_abc.py
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
from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (  # noqa: E402
    load_btc_15m, compute_moneyflow_signal, b1_triggers)
from studies.notebooks.chento_journal.validation_group_A_redo import (  # noqa: E402
    build_optimized_triggers)

SIGNAL = sys.argv[1] if len(sys.argv) > 1 else "triple"   # "triple" (edge) | "b1" (noise anchor)

# ─── knobs (v1 — tune in iteration) ─────────────────────────────────────────
EXEC_TF = "15min"          # TF the dwellblocks are detected on
LOOKBACK_BARS = 192        # 48h of 15m for the time-at-price window
ENTRY_TIF_BARS = 17_280    # 24h of 5s — how long the limit rests waiting to fill
TRADE_TIF_BARS = 51_840    # 72h of 5s — max hold after fill
TARGET_R = 3.0
STOP_BUF_ATR = 0.25        # stop sits this many ATR beyond the zone's far edge
DEPTHS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]   # pullback depth toward the zone:
                                           # 0.0 = market, 1.0 = bid the zone POC
MAX_DIST_ATR = 6.0         # skip if the nearest zone is further than this (can't bid it)
MIN_DIST_ATR = 0.20        # need the zone meaningfully away from price (else B==C)
COOLDOWN_H = 4             # dedupe clustered B1 triggers, per direction
COST_BP_RT = 10.0          # round-trip cost in bp


def _atr_15m(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    pc = bars["close"].shift(1)
    tr = pd.concat([bars["high"] - bars["low"],
                    (bars["high"] - pc).abs(),
                    (bars["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _dedupe(trigs: pd.DataFrame, hours: int) -> pd.DataFrame:
    keep, last = [], {}
    for _, r in trigs.sort_values("ts").iterrows():
        d = r["direction"]
        if d in last and (r["ts"] - last[d]).total_seconds() < hours * 3600:
            continue
        last[d] = r["ts"]
        keep.append(r)
    return pd.DataFrame(keep)


def simulate(low, high, close, start_idx, direction, entry, stop, target,
             *, is_market, limit, fill_mode, n):
    """Vectorised entry-fill + forward trade walk. Returns dict or None(no fill)."""
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    # ── entry fill ──
    if is_market:
        fill_idx = start_idx
    else:
        end = min(start_idx + ENTRY_TIF_BARS, n - 1)
        lo, hi = low[start_idx + 1:end + 1], high[start_idx + 1:end + 1]
        if direction == "long":
            cond = (lo <= limit) if fill_mode == "touch" else (lo < limit)
        else:
            cond = (hi >= limit) if fill_mode == "touch" else (hi > limit)
        if not cond.any():
            return {"filled": False}
        fill_idx = start_idx + 1 + int(np.argmax(cond))
    # ── forward trade walk ──
    end2 = min(fill_idx + TRADE_TIF_BARS, n - 1)
    lo, hi = low[fill_idx + 1:end2 + 1], high[fill_idx + 1:end2 + 1]
    if lo.size == 0:
        return {"filled": False}
    if direction == "long":
        s_hit, t_hit = lo <= stop, hi >= target
    else:
        s_hit, t_hit = hi >= stop, lo <= target
    si = int(np.argmax(s_hit)) if s_hit.any() else 10 ** 9
    ti = int(np.argmax(t_hit)) if t_hit.any() else 10 ** 9
    if si == 10 ** 9 and ti == 10 ** 9:
        exit_px, kind = float(close[end2]), "tif"
    elif si <= ti:                      # tie -> stop (conservative)
        exit_px, kind = stop, "stop"
    else:
        exit_px, kind = target, "target"
    r = ((exit_px - entry) if direction == "long" else (entry - exit_px)) / risk
    r -= (COST_BP_RT / 10_000.0) * (entry / risk)
    return {"filled": True, "r": r, "kind": kind}


def main():
    print(f"Loading triggers (signal={SIGNAL}) + 5s spot...")
    if SIGNAL == "triple":
        trigs = build_optimized_triggers()[0].copy()   # B1 n B5 n B7, OKX window
    else:
        df15p = compute_moneyflow_signal(load_btc_15m())
        trigs = b1_triggers(df15p, cvd_threshold=0.5, velocity_max=1.0)
    trigs["ts"] = pd.to_datetime(trigs["ts"], utc=True)
    print(f"  {len(trigs)} raw {SIGNAL} triggers")

    df5 = load_btc_5s()
    ts_arr = (df5["timestamp"].to_numpy() if "timestamp" in df5
              else (df5.index.view("int64") // 1_000_000_000))
    low = df5["low"].to_numpy(); high = df5["high"].to_numpy(); close = df5["close"].to_numpy()
    n = len(close)
    t_min, t_max = ts_arr[0], ts_arr[-1]

    # 15m spot frame for dwellblock detection + ATR
    df15 = resample(df5, EXEC_TF)
    atr15 = _atr_15m(df15)

    # keep triggers with full lookback behind + full forward window ahead
    need_back = LOOKBACK_BARS * 900
    need_fwd = (ENTRY_TIF_BARS + TRADE_TIF_BARS) * 5
    trig_sec = trigs["ts"].apply(lambda x: int(x.timestamp()))  # robust vs s/ns dtype
    trigs = trigs[(trig_sec >= t_min + need_back) &
                  (trig_sec <= t_max - need_fwd)].copy()
    trigs = _dedupe(trigs, COOLDOWN_H)
    print(f"  {len(trigs)} triggers in the 5s window (after {COOLDOWN_H}h dedupe)")

    rows = []
    skip = {"no_zone": 0, "too_far": 0, "too_close": 0}
    for _, tr in trigs.iterrows():
        ts = tr["ts"]; direction = tr["direction"]
        start_idx = int(np.searchsorted(ts_arr, ts.value // 10**9, side="left"))
        if start_idx >= n:
            continue
        P = float(close[start_idx])
        atr = float(atr15.asof(ts)) if not pd.isna(atr15.asof(ts)) else np.nan
        if not np.isfinite(atr) or atr <= 0:
            continue

        zones = detect_dwellblocks(df15.loc[:ts].iloc[-LOOKBACK_BARS:], top_k=6)
        # nearest zone in the pullback direction
        if direction == "long":
            cands = [z for z in zones if z.poc < P]
            zone = max(cands, key=lambda z: z.poc) if cands else None
        else:
            cands = [z for z in zones if z.poc > P]
            zone = min(cands, key=lambda z: z.poc) if cands else None
        if zone is None:
            skip["no_zone"] += 1; continue
        dist = abs(P - zone.poc)
        if dist > MAX_DIST_ATR * atr:
            skip["too_far"] += 1; continue
        if dist < MIN_DIST_ATR * atr:
            skip["too_close"] += 1; continue

        D = zone.poc
        stop = (zone.lo - STOP_BUF_ATR * atr if direction == "long"
                else zone.hi + STOP_BUF_ATR * atr)
        arms = {}
        for depth in DEPTHS:
            if depth == 0.0:
                arms["d0.0"] = (P, True, None)            # market
            else:
                e = (P - depth * (P - D) if direction == "long"
                     else P + depth * (D - P))            # pullback toward the zone
                arms[f"d{depth:.1f}"] = (e, False, e)

        for arm, (entry, is_mkt, limit) in arms.items():
            target = entry + TARGET_R * abs(entry - stop) * (1 if direction == "long" else -1)
            for fm in (["touch"] if is_mkt else ["touch", "through"]):
                res = simulate(low, high, close, start_idx, direction, entry, stop,
                               target, is_market=is_mkt, limit=limit, fill_mode=fm, n=n)
                if res is None:
                    continue
                rows.append({"arm": arm, "fill_mode": fm, "direction": direction,
                             **res})

    res = pd.DataFrame(rows)
    if res.empty:
        print(f"\nNo usable signals. skips: {skip}")
        return
    print(f"\nskips: {skip}   usable signals: "
          f"{len(res[(res['arm']=='d0.0')&(res['fill_mode']=='touch')])}\n")

    def stat(sub):
        f = sub[sub["filled"]]
        fr = len(f) / len(sub) if len(sub) else 0
        mr = f["r"].mean() if len(f) else np.nan
        return {"n": len(sub), "fill%": fr,
                "R|fill": mr, "WR|fill": (f["r"] > 0).mean() if len(f) else np.nan,
                "adverse%": (f["r"] <= 0).mean() if len(f) else np.nan,
                "exp/signal": fr * (mr if np.isfinite(mr) else 0)}

    print(f'{"depth":<14}{"n":>5}{"fill%":>8}{"R|fill":>9}'
          f'{"WR|fill":>9}{"adverse%":>10}{"exp/sig":>9}')
    for depth in DEPTHS:
        arm = f"d{depth:.1f}"
        sub = res[(res["arm"] == arm) & (res["fill_mode"] == "touch")]
        if sub.empty:
            continue
        s = stat(sub)
        tag = ("market" if depth == 0.0 else "dwell-zone" if depth == 1.0 else "")
        label = f"{depth*100:>3.0f}% {tag}"
        print(f'{label:<14}{s["n"]:>5}{s["fill%"]:>8.0%}'
              f'{s["R|fill"]:>+9.3f}{s["WR|fill"]:>9.0%}'
              f'{s["adverse%"]:>10.0%}{s["exp/signal"]:>+9.3f}')

    out = ROOT / "studies" / "notebooks" / "dwell_block" / "abc_results.csv"
    res.to_csv(out, index=False)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()

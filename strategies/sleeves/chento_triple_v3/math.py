"""CHENTO_TRIPLE_V3 — pure stateless detector + state-machine functions.

All functions are deterministic + side-effect-free; they take inputs (data
frames, parameters) and return outputs (booleans, dicts, dataframes). This
mirrors the validation scripts in studies/notebooks/chento_journal/ so that
backtest results carry over to live unchanged.

This module is the source of truth for the strategy math. signal.py wires
data feeds + tick orchestration; math.py does the computation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ─── ATR ───────────────────────────────────────────────────────────────────

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR — same as validation scripts."""
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


# ─── B1 money-flow CVD ─────────────────────────────────────────────────────

def compute_moneyflow_signal(df: pd.DataFrame, *,
                              cvd_window_bars: int,
                              velocity_window_bars: int) -> pd.DataFrame:
    """Add cvd_z and vel_z columns to df. Requires quote_volume_buy / sell."""
    out = df.copy()
    mf = out["quote_volume_buy"] - out["quote_volume_sell"]
    cvd_mu = mf.rolling(cvd_window_bars, min_periods=cvd_window_bars // 4).mean()
    cvd_sd = mf.rolling(cvd_window_bars, min_periods=cvd_window_bars // 4).std()
    out["cvd_z"] = (mf - cvd_mu) / cvd_sd
    vel = out["close"].pct_change(velocity_window_bars)
    vel_mu = vel.rolling(cvd_window_bars, min_periods=cvd_window_bars // 4).mean()
    vel_sd = vel.rolling(cvd_window_bars, min_periods=cvd_window_bars // 4).std()
    out["vel_z"] = (vel - vel_mu) / vel_sd
    return out


def b1_fires(cvd_z: float, vel_z: float, *,
              cvd_threshold: float, vel_max: float) -> str | None:
    """Return 'long' / 'short' / None depending on B1 fire."""
    if pd.isna(cvd_z) or pd.isna(vel_z):
        return None
    if cvd_z > cvd_threshold and vel_z < vel_max:
        return "short"     # whales buying but price not reacting → fade
    if cvd_z < -cvd_threshold and vel_z > -vel_max:
        return "long"      # whales selling but price not reacting → fade
    return None


# ─── B5 LSR extremes ───────────────────────────────────────────────────────

def compute_lsr_extremes(lsr: pd.DataFrame, *, rolling_days: int) -> pd.DataFrame:
    """Add p10 / p50 / p90 of long_pct over trailing window.

    Always adds the columns (NaN-filled if insufficient data) so downstream
    consumers can rely on the schema."""
    out = lsr.copy()
    # Default NaN columns (overwritten below if we have data)
    out["lp_p10"] = np.nan
    out["lp_p50"] = np.nan
    out["lp_p90"] = np.nan
    if len(out) < 30:
        return out
    span_days = max((out.index.max() - out.index.min()).days, 1)
    samples_per_day = max(1, int(round(len(out) / span_days)))
    window = rolling_days * samples_per_day
    min_p = max(8, window // 4)
    out["lp_p10"] = out["long_pct"].rolling(window, min_periods=min_p).quantile(0.10)
    out["lp_p50"] = out["long_pct"].rolling(window, min_periods=min_p).quantile(0.50)
    out["lp_p90"] = out["long_pct"].rolling(window, min_periods=min_p).quantile(0.90)
    return out


def b5_fires(long_pct: float, lp_p10: float, lp_p90: float) -> str | None:
    """Return 'long' / 'short' / None depending on LSR extreme."""
    if pd.isna(long_pct) or pd.isna(lp_p10) or pd.isna(lp_p90):
        return None
    if long_pct <= lp_p10:
        return "long"      # longs flushed → contrarian long
    if long_pct >= lp_p90:
        return "short"     # longs euphoric → contrarian short
    return None


# ─── B7 multi-TF CVD alignment ─────────────────────────────────────────────

def compute_multitf_cvd_z(df_15m: pd.DataFrame,
                            timeframes: tuple[str, ...]) -> pd.DataFrame:
    """Compute CVD z-scores for each TF, ffilled onto 15m index.
    `timeframes` are pandas-resample-compatible strings: ('1h', '4h', '1D', '3D')."""
    out = df_15m.copy()
    # Compute per-bar CVD on 15m (delta), then resample sum to each TF
    cvd_15m = out["quote_volume_buy"] - out["quote_volume_sell"]
    out["cvd_15m"] = cvd_15m
    for tf in timeframes:
        # Resample to TF, sum, compute rolling z-score over 30d worth of TF bars
        tf_lower = tf.lower().replace("d", "D")    # pandas wants 'D' uppercase
        cvd_tf = cvd_15m.resample(tf_lower).sum()
        # Pick window length: 30d worth of bars at this TF
        bars_per_day_tf = {"1h": 24, "4h": 6, "1D": 1, "3D": 1 / 3}.get(tf_lower, 1)
        win = max(30, int(30 * bars_per_day_tf))
        mu = cvd_tf.rolling(win, min_periods=win // 4).mean()
        sd = cvd_tf.rolling(win, min_periods=win // 4).std()
        z = (cvd_tf - mu) / sd
        # ffill onto 15m index
        out[f"cvd_z_{tf}"] = z.reindex(out.index, method="ffill")
    return out


def b7_alignment_fires(cvd_z_values: dict[str, float],
                        *, z_threshold: float) -> str | None:
    """All TFs aligned with |z| ≥ threshold → directional signal."""
    if any(pd.isna(v) for v in cvd_z_values.values()):
        return None
    signs = [np.sign(v) if abs(v) >= z_threshold else 0
             for v in cvd_z_values.values()]
    if all(s > 0 for s in signs):
        return "long"
    if all(s < 0 for s in signs):
        return "short"
    return None


# ─── Triple intersection ───────────────────────────────────────────────────

def triple_fires(b1_dir: str | None, b5_dir: str | None,
                  b7_dir: str | None) -> str | None:
    """All three must agree on direction."""
    if b1_dir is None or b5_dir is None or b7_dir is None:
        return None
    if b1_dir == b5_dir == b7_dir:
        return b1_dir
    return None


# ─── SMC pivots + Order Blocks ─────────────────────────────────────────────

def detect_pivots(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Confirmed-pivot detection (5-bar default). Pivots known at +n bars."""
    out = df.copy()
    h = df["high"].values
    l = df["low"].values
    is_ph = np.zeros(len(df), dtype=bool)
    is_pl = np.zeros(len(df), dtype=bool)
    for i in range(n, len(df) - n):
        if h[i] > h[i - n:i].max() and h[i] >= h[i + 1:i + n + 1].max():
            is_ph[i] = True
        if l[i] < l[i - n:i].min() and l[i] <= l[i + 1:i + n + 1].min():
            is_pl[i] = True
    out["piv_high"] = is_ph
    out["piv_low"] = is_pl
    return out


def detect_order_blocks(df_smc: pd.DataFrame, n: int = 5) -> list[dict]:
    """Causally detect Order Blocks from confirmed pivots + BOS events.

    Same logic as validation_C5_smc_features.py. Returns list of dicts:
        {idx_created, idx_origin, direction, zone_low, zone_high, idx_filled}
    """
    out = df_smc.copy()
    n_bars = len(out)
    h = out["high"].values
    l = out["low"].values
    o = out["open"].values
    c = out["close"].values
    ph = out["piv_high"].values
    pl = out["piv_low"].values
    c_arr = c

    trend = "undef"
    last_sh = np.nan
    last_sh_idx = -1
    last_sl = np.nan
    last_sl_idx = -1
    obs = []

    for i in range(n_bars):
        confirm_idx = i - n
        if confirm_idx >= 0:
            if ph[confirm_idx]:
                last_sh = h[confirm_idx]
                last_sh_idx = confirm_idx
            if pl[confirm_idx]:
                last_sl = l[confirm_idx]
                last_sl_idx = confirm_idx

        # BOS_up
        if not np.isnan(last_sh) and c_arr[i] > last_sh:
            leg_start = max(int(last_sl_idx), 0)
            best = -1
            for j in range(i - 1, leg_start - 1, -1):
                if c[j] < o[j]:
                    best = j
                    break
            if best >= 0:
                obs.append({
                    "idx_created": i, "idx_origin": best, "direction": "bull",
                    "zone_low": float(l[best]), "zone_high": float(h[best]),
                    "idx_filled": None,
                })
            trend = "up"
            last_sh = np.nan
        if not np.isnan(last_sl) and c_arr[i] < last_sl:
            leg_start = max(int(last_sh_idx), 0)
            best = -1
            for j in range(i - 1, leg_start - 1, -1):
                if c[j] > o[j]:
                    best = j
                    break
            if best >= 0:
                obs.append({
                    "idx_created": i, "idx_origin": best, "direction": "bear",
                    "zone_low": float(l[best]), "zone_high": float(h[best]),
                    "idx_filled": None,
                })
            trend = "down"
            last_sl = np.nan

    # Mark fills
    for ob in obs:
        for j in range(ob["idx_created"], n_bars):
            if ob["direction"] == "bull" and l[j] <= ob["zone_high"]:
                ob["idx_filled"] = j; break
            if ob["direction"] == "bear" and h[j] >= ob["zone_low"]:
                ob["idx_filled"] = j; break
    return obs


def nearest_resist_ob_distance_R(entry: float, direction: str, risk: float,
                                    obs: list[dict], idx_now: int) -> float:
    """Distance (in R units) to the NEAREST unfilled opposite-direction OB.
    Returns +inf if no qualifying OB."""
    unfilled = [ob for ob in obs
                if ob["idx_created"] <= idx_now
                and (ob["idx_filled"] is None or ob["idx_filled"] > idx_now)]
    if direction == "long":
        # Resistance OBs above for longs = bearish OBs
        above_bear = [ob for ob in unfilled
                      if ob["direction"] == "bear" and ob["zone_low"] > entry]
        if not above_bear:
            return float("inf")
        nearest = min(above_bear, key=lambda x: x["zone_low"])
        return (nearest["zone_low"] - entry) / risk
    # Short: resistance below = bullish OBs
    below_bull = [ob for ob in unfilled
                  if ob["direction"] == "bull" and ob["zone_high"] < entry]
    if not below_bull:
        return float("inf")
    nearest = max(below_bull, key=lambda x: x["zone_high"])
    return (entry - nearest["zone_high"]) / risk


# ─── OKX delta z ───────────────────────────────────────────────────────────

def compute_okx_delta_z(close_bnb: pd.Series, close_okx: pd.Series, *,
                          window_hours: int) -> pd.Series:
    """log-ratio delta z-score on rolling window. Both series must be 1h."""
    df = pd.DataFrame({"bnb": close_bnb, "okx": close_okx}).dropna()
    delta = np.log(df["okx"]) - np.log(df["bnb"])
    mu = delta.rolling(window_hours, min_periods=window_hours // 4).mean()
    sd = delta.rolling(window_hours, min_periods=window_hours // 4).std()
    return (delta - mu) / sd


def okx_aligned(delta_z_now: float, direction: str, min_z: float) -> bool:
    """For long: delta_z ≥ min_z. For short: delta_z ≤ -min_z (or just ≤ 0 if min=0)."""
    if pd.isna(delta_z_now):
        return False
    if direction == "long":
        return delta_z_now >= min_z
    return delta_z_now <= -min_z if min_z > 0 else delta_z_now <= 0


# ─── 30d return regime ─────────────────────────────────────────────────────

def compute_30d_return(close: pd.Series, *, days: int = 30) -> pd.Series:
    """Daily close → 30d pct return, ffilled onto whatever index."""
    daily = close.resample("1D").last()
    ret = daily.pct_change(days)
    return ret.reindex(close.index, method="ffill")


def is_up_30d(ret_30d_now: float, threshold: float) -> bool:
    if pd.isna(ret_30d_now):
        return False
    return ret_30d_now > threshold


# ─── C6 Volume Profile classifier ──────────────────────────────────────────

def compute_volume_profile_for_ts(df_recent: pd.DataFrame, *,
                                     n_bins: int = 50,
                                     value_area_pct: float = 0.70
                                     ) -> tuple[float, float, float]:
    """Compute (POC, VAH, VAL) from a window of 15m bars (typically last 7d).
    Uses bar typical price (HL2) weighted by volume."""
    if df_recent.empty:
        return float("nan"), float("nan"), float("nan")
    typ = (df_recent["high"] + df_recent["low"]) / 2
    vols = df_recent["volume"].values
    prices = typ.values
    if vols.sum() <= 0 or np.isnan(prices).any():
        return float("nan"), float("nan"), float("nan")
    lo, hi = float(prices.min()), float(prices.max())
    if hi <= lo:
        return float("nan"), float("nan"), float("nan")
    bin_edges = np.linspace(lo, hi, n_bins + 1)
    idx = np.clip(((prices - lo) / (hi - lo) * n_bins).astype(int), 0, n_bins - 1)
    bin_vol = np.zeros(n_bins)
    for k in range(len(prices)):
        bin_vol[idx[k]] += vols[k]
    poc_bin = int(np.argmax(bin_vol))
    poc_price = 0.5 * (bin_edges[poc_bin] + bin_edges[poc_bin + 1])
    total_vol = bin_vol.sum()
    target = value_area_pct * total_vol
    accumulated = bin_vol[poc_bin]
    lo_bin = hi_bin = poc_bin
    while accumulated < target and (lo_bin > 0 or hi_bin < n_bins - 1):
        below = bin_vol[lo_bin - 1] if lo_bin > 0 else -1
        above = bin_vol[hi_bin + 1] if hi_bin < n_bins - 1 else -1
        if below >= above and lo_bin > 0:
            lo_bin -= 1; accumulated += bin_vol[lo_bin]
        elif hi_bin < n_bins - 1:
            hi_bin += 1; accumulated += bin_vol[hi_bin]
        else:
            break
    val_price = bin_edges[lo_bin]
    vah_price = bin_edges[hi_bin + 1]
    return poc_price, vah_price, val_price


def is_inside_va(price: float, vah: float, val: float) -> bool:
    if pd.isna(vah) or pd.isna(val):
        return False
    return val <= price <= vah


# ─── A4 ladder state machine ───────────────────────────────────────────────

def evaluate_position_step(state: dict, *,
                             bar_high: float, bar_low: float,
                             bar_close: float, atr_now: float,
                             direction: str,
                             ladder_enabled: bool,
                             ladder_adv_trigger_R: float,
                             ladder_size_frac: float,
                             ladder_post_stop_R: float,
                             cost_R: float) -> dict:
    """Per-bar state machine. Returns dict with 'action' and updated 'state'.

    Action values:
        'stop_hit'      — close the entire position at stop_price
        'target_hit'    — close at target_price
        'ladder_fired'  — internal ladder add event (state updated, no exit)
        None            — no event

    state schema:
        direction        : 'long' | 'short'
        entry_price      : float
        risk             : float (initial = ATR_STOP_MULT × ATR_at_entry)
        stop_price       : current stop
        target_price     : fixed target (entry ± 6R)
        ladder_added     : bool
        ladder_entry     : price ladder filled at (or None)
        ladder_size_frac : 0.5 (T1) or 1.5 (T3)
    """
    entry = state["entry_price"]
    risk = state["risk"]
    stop = state["stop_price"]
    target = state["target_price"]

    if direction == "long":
        adv_R = (entry - bar_low) / risk
        # Stop / target check
        if bar_low <= stop:
            return {"action": "stop_hit", "exit_price": stop, "r_outcome": (stop - entry) / risk - cost_R}
        if bar_high >= target:
            return {"action": "target_hit", "exit_price": target,
                    "r_outcome": (target - entry) / risk - cost_R}
    else:
        adv_R = (bar_high - entry) / risk
        if bar_high >= stop:
            return {"action": "stop_hit", "exit_price": stop, "r_outcome": (entry - stop) / risk - cost_R}
        if bar_low <= target:
            return {"action": "target_hit", "exit_price": target,
                    "r_outcome": (entry - target) / risk - cost_R}

    # Ladder fire?
    if ladder_enabled and not state.get("ladder_added", False) and adv_R >= ladder_adv_trigger_R:
        if direction == "long":
            ladder_entry = entry - risk * ladder_adv_trigger_R
            new_stop = entry - risk * ladder_post_stop_R
        else:
            ladder_entry = entry + risk * ladder_adv_trigger_R
            new_stop = entry + risk * ladder_post_stop_R
        state["ladder_added"] = True
        state["ladder_entry"] = ladder_entry
        state["ladder_size_frac"] = ladder_size_frac
        state["stop_price"] = new_stop
        return {"action": "ladder_fired", "ladder_entry": ladder_entry, "new_stop": new_stop}

    return {"action": None}


def composite_r_outcome(state: dict, exit_price: float, *,
                          direction: str, cost_R: float) -> float:
    """Combined R of original + ladder leg at the exit price."""
    entry = state["entry_price"]
    risk = state["risk"]
    if direction == "long":
        main_r = (exit_price - entry) / risk - cost_R
    else:
        main_r = (entry - exit_price) / risk - cost_R
    main_size = 1.0
    ladder_r = 0.0
    ladder_size = 0.0
    if state.get("ladder_added", False):
        ladder_entry = state["ladder_entry"]
        ladder_size = state["ladder_size_frac"]
        if direction == "long":
            ladder_r = (exit_price - ladder_entry) / risk - cost_R
        else:
            ladder_r = (ladder_entry - exit_price) / risk - cost_R
    return main_size * main_r + ladder_size * ladder_r

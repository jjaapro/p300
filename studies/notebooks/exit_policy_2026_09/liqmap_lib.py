"""Liquidation-map study library: the map engine and the state features (sections 3 and 4), and the Q1 / Q2
outcomes, the era-matched placebo and the classification table (section 5).

`PREREGISTRATION_LIQMAP.md` v1.1 is the authority and every rule below cites its section. Nothing here reads real data
or writes results; `liqmap_run.py` owns the passes and `tests/test_liqmap.py` pins every rule on hand-built arrays.

The actual map and the control map (3.7) are one code path: `run_map(..., control=True)` replaces the open-interest and
taker information by constants and skips the open-interest-decrease removal, so no rule can drift between them.

`micro_lib` is frozen by the microstructure stage's F0 and is never edited: the grids, the per-control placebo mean,
the block bootstrap, Holm and the day axis are imported from it unchanged. Section 5 changes three of its functions, so
this module carries copies -- `eligible_liq`, `match_liq` and `classify_liq` -- pinned to their twins by fixtures.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import chento_lib as CL  # noqa: E402
import micro_lib as ML  # noqa: E402

# --- frozen constants (sections 2, 3, 4) -----------------------------------------------------------
BIN_S = 300                                   # section 2: the archive's dense 5-minute grid
MIN_S = 60
BIN_MINUTES = BIN_S // MIN_S
DAY_S = 86_400

TIERS = (10.0, 25.0, 50.0, 100.0)             # 3.2: leverage tiers and their fixed weights
TIER_W = (0.40, 0.30, 0.20, 0.10)
MMR = 0.004                                   # BTCUSDT tier-1 maintenance margin, held for both assets
LONG_MULT = tuple(1.0 - 1.0 / L + MMR for L in TIERS)
SHORT_MULT = tuple(1.0 + 1.0 / L - MMR for L in TIERS)

BUCKET = 1.001                                # 3.3: geometric 10 bp grid
LN_BUCKET = math.log(BUCKET)
PRICE_MIN, PRICE_MAX = 100.0, 1_000_000.0     # a price outside the range is an error, never clipped
RING_DAYS = 30                                # 3.3: the current UTC day and the 29 preceding days
WARMUP_DAYS = 30                              # 3.6
BAND = 0.05                                   # section 4: the cluster search band

BURST_LAG = 3                                 # section 4: theta over bins b - 8642 ... b - 3
BURST_WINDOW = 8_640
BURST_MIN_DEFINED = 4_320
BURST_PCT = 99.0
TERCILE_DAYS = 30                             # section 4: the trailing density cut
TERCILE_Q = 2.0 / 3.0

_SCALAR = (int, float, np.floating, np.integer)


# --- the price grid (3.3) --------------------------------------------------------------------------

def bucket_of(price):
    """3.3: bucket k = floor(ln(price) / ln(1.001)). Outside [100, 1e6] is an error, never a clip."""
    if isinstance(price, _SCALAR):
        p = float(price)
        if not (PRICE_MIN <= p <= PRICE_MAX):
            raise ValueError(f"price {p!r} outside the map grid [{PRICE_MIN}, {PRICE_MAX}] (3.3)")
        return int(math.floor(math.log(p) / LN_BUCKET))
    p = np.asarray(price, dtype=float)
    if p.size and (not np.all(np.isfinite(p)) or p.min() < PRICE_MIN or p.max() > PRICE_MAX):
        raise ValueError(f"price outside the map grid [{PRICE_MIN}, {PRICE_MAX}] (3.3)")
    return np.floor(np.log(p) / LN_BUCKET).astype(np.int64)


def bucket_level(k):
    """3.3: the level price of bucket k, 1.001 ** (k + 0.5)."""
    if isinstance(k, _SCALAR):
        return BUCKET ** (float(k) + 0.5)
    return np.power(BUCKET, np.asarray(k, dtype=float) + 0.5)


K_MIN = bucket_of(PRICE_MIN)                  # 4,607
K_MAX = bucket_of(PRICE_MAX)                  # 13,822
N_BUCKETS = K_MAX - K_MIN + 1                 # 9,216


def map_state_bin(t0_arch: int, ts_s: int) -> int:
    """Section 3: the map as of a minute is the state after the last bin stamped <= ts_s - 5 min. May be negative."""
    return (int(ts_s) - BIN_S - int(t0_arch)) // BIN_S


def _k_ge(price: float) -> int:
    """Smallest bucket with level >= price; exact against `bucket_level`, so the 3.4 rule holds at the boundary."""
    k = int(math.ceil(math.log(price) / LN_BUCKET - 0.5))
    k = min(max(k, K_MIN - 1), K_MAX + 1)
    while k <= K_MAX and bucket_level(k) < price:
        k += 1
    while k > K_MIN and bucket_level(k - 1) >= price:
        k -= 1
    return k


def _k_le(price: float) -> int:
    """Largest bucket with level <= price."""
    k = int(math.floor(math.log(price) / LN_BUCKET - 0.5))
    k = min(max(k, K_MIN - 1), K_MAX + 1)
    while k >= K_MIN and bucket_level(k) > price:
        k -= 1
    while k < K_MAX and bucket_level(k + 1) <= price:
        k += 1
    return k


def _k_gt(price: float) -> int:
    """Smallest bucket with level strictly > price (the open edge of the band above, section 4)."""
    k = _k_ge(price)
    return k + 1 if bucket_level(k) == price else k


def _k_lt(price: float) -> int:
    """Largest bucket with level strictly < price (the open edge of the band below)."""
    k = _k_le(price)
    return k - 1 if bucket_level(k) == price else k


# --- the 5-minute bin panel (sections 2, 3) --------------------------------------------------------

@dataclass
class BinPanel:
    """One asset on the archive's 5-minute grid. Bin b is stamped t0_s + 300 b and holds the bars opening at that
    stamp ... + 4 min (section 2, frozen alignment)."""
    symbol: str
    t0_s: int
    n: int
    oi: np.ndarray            # 3.1: the snapshot when finite and > 0, else nan (zero rows are missing)
    present: np.ndarray       # bool: at least one present minute among the five bars
    vol: np.ndarray           # nan where the bin has no present minute
    tb: np.ndarray
    qv: np.ndarray
    high: np.ndarray
    low: np.ndarray
    vwap: np.ndarray          # qv / vol


def _bin_view(a: np.ndarray, off: int, n: int, fill: float) -> np.ndarray:
    """The 1-minute array laid on the bin grid as (n, 5); `off` minutes of the panel precede bin 0."""
    buf = np.full(n * BIN_MINUTES, fill)
    src_lo, src_hi = max(0, off), min(len(a), off + n * BIN_MINUTES)
    if src_hi > src_lo:
        buf[src_lo - off:src_hi - off] = a[src_lo:src_hi]
    return buf.reshape(n, BIN_MINUTES)


def build_bin_panel(symbol: str, oi: np.ndarray, oi_t0_s: int, panel: dict, panel_t0_s: int) -> BinPanel:
    """Section 3: the per-bin price quantities over the present minutes of the bars opening T_b ... T_b + 4.

    A minute is present iff volume > 0 (the earlier stages' convention); a bin with no present minute has no price
    quantities (3.1, step 4 still runs only on bins that have them).
    """
    oi_t0_s, panel_t0_s = int(oi_t0_s), int(panel_t0_s)
    if (oi_t0_s - panel_t0_s) % BIN_S != 0:
        raise AssertionError(f"archive t0 {oi_t0_s} is not a whole number of bins after the panel t0 {panel_t0_s}")
    oi = np.asarray(oi, dtype=float)
    n = int(oi.size)
    off = (oi_t0_s - panel_t0_s) // MIN_S
    vol5 = _bin_view(np.asarray(panel["volume"], float), off, n, 0.0)
    pm = vol5 > 0
    cnt = pm.sum(axis=1)
    present = cnt > 0
    tb5 = _bin_view(np.asarray(panel["taker_buy_volume"], float), off, n, 0.0)
    qv5 = _bin_view(np.asarray(panel["quote_volume"], float), off, n, 0.0)
    hi5 = _bin_view(np.asarray(panel["high"], float), off, n, np.nan)
    lo5 = _bin_view(np.asarray(panel["low"], float), off, n, np.nan)
    miss = ~present
    vol = np.where(pm, vol5, 0.0).sum(axis=1)
    tb = np.where(pm, tb5, 0.0).sum(axis=1)
    qv = np.where(pm, qv5, 0.0).sum(axis=1)
    high = np.where(pm, hi5, -np.inf).max(axis=1)
    low = np.where(pm, lo5, np.inf).min(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        vwap = qv / vol
    for a in (vol, tb, qv, high, low, vwap):
        a[miss] = np.nan
    usable = np.isfinite(oi) & (oi > 0)                          # 3.1: zero-valued rows are missing
    return BinPanel(symbol, oi_t0_s, n, np.where(usable, oi, np.nan), present, vol, tb, qv, high, low, vwap)


# --- one forward pass over the map (section 3) -----------------------------------------------------

@dataclass
class MapRun:
    e_long: np.ndarray            # 3.4: base units removed by traversal, per bin (nan past an `n_bins` cut)
    e_short: np.ndarray
    clamp_surplus: np.ndarray     # 3.5: base units the clamp could not remove (L7)
    total_mass: np.ndarray        # long + short mass after the bin's steps (section 7, descriptive)
    d_up: np.ndarray              # section 4, per query; nan when the band holds no mass on that side
    share_up: np.ndarray
    mass_up: np.ndarray
    d_dn: np.ndarray
    share_dn: np.ndarray
    mass_dn: np.ndarray


def run_map(panel: BinPanel, q_bin: np.ndarray, q_price: np.ndarray, control: bool = False,
            warmup_bin: int | None = None, n_bins: int | None = None) -> MapRun:
    """One forward pass. Per bin, in order: ring rollover (3.3), removal by price traversal (3.4), removal by
    open-interest decrease (3.5, not in the control map), additions (3.2, or 3.7 when `control`), then every query
    stamped at that bin. Mass added in bin b is therefore exposed to traversal only from bin b + 1 (3.2).

    Layer order never enters a removal, so no order has to be chosen: traversal empties whole buckets across all 30
    layers at once (3.4 removes every unit whose level the bar reached), and the open-interest decrease is one
    factor applied to every layer and bucket alike (3.5 removes R in proportion to the mass present, and the
    per-side proportions make that factor 1 - R / total the same on both sides). A proportional rule is
    layer-order-free by construction.

    `warmup_bin` (3.6) and `n_bins` (the L4 causality cuts) both answer nan: queries before the warm-up and queries
    at or after the cut are never evaluated, and the per-bin arrays are nan past the cut.

    `q_bin` must be sorted ascending. A query price that is not finite answers nan (the caller filters missing
    bars); a finite price outside the grid is an error, as 3.3 says.
    """
    n = int(panel.n)
    n_eff = n if n_bins is None else int(max(0, min(n, n_bins)))
    qb = np.asarray(q_bin, dtype=np.int64).ravel()
    qp = np.asarray(q_price, dtype=float).ravel()
    if qb.size != qp.size:
        raise ValueError("q_bin and q_price must have the same length")
    if qb.size > 1 and bool(np.any(np.diff(qb) < 0)):
        raise ValueError("q_bin must be sorted ascending")
    wb = -(1 << 62) if warmup_bin is None else int(warmup_bin)

    e_long, e_short = np.full(n, np.nan), np.full(n, np.nan)
    clamp_surplus, total_mass = np.full(n, np.nan), np.full(n, np.nan)
    nq = int(qb.size)
    d_up, share_up, mass_up = np.full(nq, np.nan), np.full(nq, np.nan), np.full(nq, np.nan)
    d_dn, share_dn, mass_dn = np.full(nq, np.nan), np.full(nq, np.nan), np.full(nq, np.nan)

    d_oi = np.full(n, np.nan)                                    # 3.1: defined only when both snapshots are usable
    if n > 1:
        d_oi[1:] = panel.oi[1:] - panel.oi[:-1]                  # nan propagates across a gap; never spanned
    with np.errstate(invalid="ignore", divide="ignore"):
        s_bin = np.where(panel.vol > 0, panel.tb / panel.vol, np.nan)    # 3.2 aggressor share

    present = panel.present.tolist()                             # python scalars: the loop runs 635k times on BTC
    low_l, high_l, vwap_l = panel.low.tolist(), panel.high.tolist(), panel.vwap.tolist()
    d_oi_l, s_l = d_oi.tolist(), s_bin.tolist()

    ring = np.zeros((2, RING_DAYS, N_BUCKETS))                   # 3.3: [0] long mass, [1] short mass
    lo, hi = [N_BUCKETS, N_BUCKETS], [-1, -1]                    # the bucket range each side can hold mass in
    mass = [0.0, 0.0]                                            # the running side totals, resynced every rollover
    t0 = int(panel.t0_s)
    prev_day, layer, qi = None, 0, 0
    while qi < nq and qb[qi] < 0:                                # queries before the pass answer nan
        qi += 1

    for b in range(n_eff):
        day = (t0 + BIN_S * b) // DAY_S
        if day != prev_day:                                      # 3.3 rollover, before the bin's removals and adds
            layer = int(day % RING_DAYS)
            ring[:, layer, :] = 0.0                              # the day 30 days back leaves the ring
            col = ring.sum(axis=1)                               # daily resync of the pointers and the side totals
            for s in (0, 1):
                nz = np.flatnonzero(col[s] > 0.0)
                lo[s] = int(nz[0]) if nz.size else N_BUCKETS
                hi[s] = int(nz[-1]) if nz.size else -1
                mass[s] = float(col[s].sum())
            prev_day = day

        el = es = 0.0
        if present[b]:                                           # 3.4 traversal, on the bin's own range
            a = _k_ge(low_l[b]) - K_MIN                          # long mass at level >= low_b
            if a < lo[0]:
                a = lo[0]
            if a <= hi[0]:
                block = ring[0, :, a:hi[0] + 1]                  # every layer of those buckets goes at once
                el = float(block.sum())
                block[...] = 0.0
                hi[0] = a - 1
                mass[0] = max(0.0, mass[0] - el)
            z = _k_le(high_l[b]) - K_MIN                         # short mass at level <= high_b
            if z > hi[1]:
                z = hi[1]
            if z >= lo[1]:
                block = ring[1, :, lo[1]:z + 1]
                es = float(block.sum())
                block[...] = 0.0
                lo[1] = z + 1
                mass[1] = max(0.0, mass[1] - es)
        e_long[b] = el
        e_short[b] = es

        d = d_oi_l[b]
        defined = d == d                                         # nan check
        surplus = 0.0
        if not control and defined and d < 0.0:                  # 3.5, skipped for the control map
            r0 = -d - (el + es)
            if r0 > 0.0:
                m_tot = mass[0] + mass[1]
                r = r0 if r0 < m_tot else m_tot                  # clamped to the mass present, both sides
                surplus = r0 - r
                if r > 0.0:
                    f = 1.0 - r / m_tot                          # proportional: the same factor everywhere
                    for s in (0, 1):
                        if lo[s] <= hi[s]:
                            ring[s, :, lo[s]:hi[s] + 1] *= f     # one factor over every layer and bucket
                        mass[s] *= f
        clamp_surplus[b] = surplus

        if present[b] and defined:                               # 3.2 additions, 3.7 in the control map
            if control:
                add_l = add_s = 0.5                              # one unit a bin, half long half short
            elif d > 0.0:
                sb = s_l[b]
                add_l, add_s = d * sb, d * (1.0 - sb)
            else:
                add_l = add_s = 0.0
            if add_l > 0.0 or add_s > 0.0:
                v = vwap_l[b]
                if add_l > 0.0:
                    row = ring[0, layer]
                    for mult, w in zip(LONG_MULT, TIER_W):       # VWAP x (1 - 1/L + mmr)
                        k = bucket_of(v * mult) - K_MIN
                        row[k] += add_l * w
                        if k < lo[0]:
                            lo[0] = k
                        if k > hi[0]:
                            hi[0] = k
                    mass[0] += add_l
                if add_s > 0.0:
                    row = ring[1, layer]
                    for mult, w in zip(SHORT_MULT, TIER_W):      # VWAP x (1 + 1/L - mmr)
                        k = bucket_of(v * mult) - K_MIN
                        row[k] += add_s * w
                        if k < lo[1]:
                            lo[1] = k
                        if k > hi[1]:
                            hi[1] = k
                    mass[1] += add_s
        total_mass[b] = mass[0] + mass[1]

        while qi < nq and qb[qi] == b:                           # section 4, read after the bin's steps
            p = qp[qi]
            if b >= wb and 0.0 < p < math.inf:                   # a missing bar answers nan, it is not an error
                a1 = _k_gt(p) - K_MIN                            # cluster above: short mass, level in (P, 1.05 P]
                b1 = _k_le(p * (1.0 + BAND)) - K_MIN
                a1, b1 = max(a1, 0), min(b1, N_BUCKETS - 1)
                if a1 <= b1:
                    band = ring[1, :, a1:b1 + 1].sum(axis=0)     # 3.3: a bucket's mass is the sum over its layers
                    tb_sum = float(band.sum())
                    if tb_sum > 0.0:
                        j = int(np.argmax(band))                 # ties: np.argmax takes the first, nearest to P
                        mass_up[qi] = float(band[j])
                        share_up[qi] = float(band[j]) / tb_sum
                        d_up[qi] = bucket_level(a1 + j + K_MIN) / p - 1.0
                a2 = _k_ge(p * (1.0 - BAND)) - K_MIN             # cluster below: long mass, level in [0.95 P, P)
                b2 = _k_lt(p) - K_MIN
                a2, b2 = max(a2, 0), min(b2, N_BUCKETS - 1)
                if a2 <= b2:
                    band = ring[0, :, a2:b2 + 1].sum(axis=0)
                    tb_sum = float(band.sum())
                    if tb_sum > 0.0:
                        j = len(band) - 1 - int(np.argmax(band[::-1]))   # ties: the last, nearest to P
                        mass_dn[qi] = float(band[j])
                        share_dn[qi] = float(band[j]) / tb_sum
                        d_dn[qi] = bucket_level(a2 + j + K_MIN) / p - 1.0
            qi += 1

    return MapRun(e_long, e_short, clamp_surplus, total_mass, d_up, share_up, mass_up, d_dn, share_dn, mass_dn)


# --- the burst (section 4) -------------------------------------------------------------------------

def burst_series(e: np.ndarray, has_price: np.ndarray, warmup_bin: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Section 4: S_b = e[b-2] + e[b-1] + e[b] (missing when any of the three bins has no price quantities),
    theta_b = the 99th percentile (linear interpolation) of the defined S over bins b - 8642 ... b - 3 counting only
    bins at or after the warm-up, missing when fewer than 4,320 are defined, and the flag S >= theta.

    The lag-3 window is the microstructure stage's `trailing_z` form, so neither S_b nor the two sums sharing a bin
    with it enter its own reference. The flag is a bool: a missing S or theta is not a burst.
    """
    e = np.asarray(e, dtype=float)
    ok = np.asarray(has_price, dtype=bool)
    if e.shape != ok.shape:
        raise ValueError("e and has_price must have the same shape")
    x = np.where(ok, e, np.nan)
    n = int(e.size)
    S = np.full(n, np.nan)
    if n >= 3:
        S[2:] = x[2:] + x[1:-1] + x[:-2]                         # nan in any of the three bins propagates
    ref = S.copy()
    ref[:max(0, min(n, int(warmup_bin)))] = np.nan               # 3.6: only bins at or after the warm-up count
    theta = (pd.Series(ref).shift(BURST_LAG)
             .rolling(BURST_WINDOW, min_periods=BURST_MIN_DEFINED)
             .quantile(BURST_PCT / 100.0, interpolation="linear").to_numpy())
    with np.errstate(invalid="ignore"):
        flag = S >= theta
    return S, theta, flag


# --- the density cut (section 4) -------------------------------------------------------------------

def tercile_cut(state_ts: np.ndarray, share: np.ndarray, at_ts: np.ndarray) -> np.ndarray:
    """Section 4: the 2/3 quantile (linear interpolation) of `share` over the hourly :00 states with mass in the
    band whose stamps lie in [t - 30 d, t). Missing while t - 30 d precedes the first hourly state, and when the
    window holds none. `state_ts` ascending; `at_ts` any order.
    """
    st = np.asarray(state_ts, dtype=np.int64).ravel()
    sh = np.asarray(share, dtype=float).ravel()
    at = np.asarray(at_ts, dtype=np.int64).ravel()
    if st.shape != sh.shape:
        raise ValueError("state_ts and share must have the same shape")
    out = np.full(at.shape, np.nan)
    if st.size == 0 or at.size == 0:
        return out
    ok = np.isfinite(sh)                                          # states with mass in the band
    ts_ok, sh_ok = st[ok], sh[ok]
    first, win = int(st[0]), TERCILE_DAYS * DAY_S
    lo = np.searchsorted(ts_ok, at - win, side="left")
    hi = np.searchsorted(ts_ok, at, side="left")
    for i in range(at.size):
        if int(at[i]) - win < first or hi[i] <= lo[i]:
            continue
        out[i] = float(np.quantile(sh_ok[lo[i]:hi[i]], TERCILE_Q))
    return out


def cluster_defined(share: np.ndarray, cut: np.ndarray) -> np.ndarray:
    """Section 4: a cluster is defined when the band held mass and its share is at or above the trailing cut."""
    sh, c = np.asarray(share, dtype=float), np.asarray(cut, dtype=float)
    with np.errstate(invalid="ignore"):
        return np.isfinite(sh) & np.isfinite(c) & (sh >= c)


# --- frozen constants -------------------------------------------------------------------------------
TOUCH_MIN = 1_440              # 5 Q1: the touch window, 24 h by clock
TURN_MIN = 240                 # 5 Q1: the turn scan after the touching minute
TURN_BAND = 0.01               # 5 Q1: the 1 % band that decides turn against through
STATE_WINDOW = 1_680           # 5 Q1: TOUCH_MIN + TURN_MIN, the last minute an eligible state can need
BIN_R = 0.25                   # 5 Q2: the profit bin         | these six are inherited unchanged from
WINDOW_SHARE = 0.05            # 5 Q2: W = 216 / 144 minutes  | the microstructure stage (section 5's
MIN_CONTROLS = 3               # 5 Q2: contributing controls  | provenance paragraph); the constants
MIN_EVENT_TRADES = 30          # 5 family: the count          | fixture asserts each equals its
SIGN_CONTROL_MIN = 10          # 5 family: a comparator's floor   | micro_lib twin.
EQUIVALENCE_R = 0.10           # 5 family: the Q2 equivalence band
ERA_DAYS = 365                 # 5 Q2: the era pool, +-365 days around the event trade's entry
ALPHA = 0.05
POWER_Z = 2.49                 # L6: z(0.95) + z(0.80)
NO_EVENT = np.iinfo(np.int64).max

KINDS = ("EV1", "EV1_ctrl", "EV2", "EV2_ctrl", "EV2_against")           # 5 Q2
Q1_TESTS = ("up_touch", "up_turn", "down_touch", "down_turn")           # 5 Q1's four primary numbers
CLAIMED_SIGN = {"Q1": +1, "Q2": -1}                                     # 5: c per question
BANDS = {"touch": (-0.02, 0.02),                                        # 5 family: +-2 pp, Delta as a fraction
         "turn": (-0.05, 0.05),                                         # +-5 pp
         "R": (-EQUIVALENCE_R, EQUIVALENCE_R)}                          # +-0.10 R


# --- Q1 outcomes (section 5) ------------------------------------------------------------------------

def touch_turn(high: np.ndarray, low: np.ndarray, present: np.ndarray, m: int, level: float, side: int,
               touch_min: int = TOUCH_MIN, turn_min: int = TURN_MIN,
               band: float = TURN_BAND) -> tuple[int, int, int]:
    """Section 5 Q1's outcome rule at state `m` for one level. side +1 = the cluster above, -1 = the cluster below.

    Returns (touch, turn, t_touch). touch is the first present minute in m + 1 ... m + 1,440 at or beyond the level
    (minute m itself never counts, its own bar may already be beyond it). Given a touch: turn = 0 when the touching
    minute is already 1 % through; otherwise the first of the next 240 present minutes that is 1 % back (turn = 1) or
    1 % through (turn = 0) decides, a minute meeting both is scored through, and no resolution inside the 240 minutes
    is turn = 0 (a stall is not a turn). Without a touch: (0, -1, -1). Windows are by clock; missing minutes are
    skipped but still consume the window.
    """
    n = len(high)
    up, dn = level * (1.0 + band), level * (1.0 - band)
    lo_i, hi_i = m + 1, min(n, m + 1 + touch_min)
    if lo_i >= hi_i:
        return 0, -1, -1
    with np.errstate(invalid="ignore"):
        h, l, p = high[lo_i:hi_i], low[lo_i:hi_i], present[lo_i:hi_i]
        hit = p & ((h >= level) if side > 0 else (l <= level))
    j = ML.first_true(hit)
    if j < 0:
        return 0, -1, -1
    t = lo_i + j
    with np.errstate(invalid="ignore"):
        if bool((high[t] >= up) if side > 0 else (low[t] <= dn)):
            return 1, 0, t                                      # through inside the touching minute
        a, b = t + 1, min(n, t + 1 + turn_min)
        h, l, p = high[a:b], low[a:b], present[a:b]
        back = p & ((l <= dn) if side > 0 else (h >= up))
        through = p & ((h >= up) if side > 0 else (l <= dn))
    k = ML.first_true(back | through)
    if k < 0:
        return 1, 0, t                                          # no resolution within the 240 minutes
    return 1, int(bool(back[k]) and not bool(through[k])), t


def state_window_ok(m, n_minutes: int, window: int = STATE_WINDOW):
    """Section 5 Q1's eligible state: the whole outcome window lies inside the panel, m + 1,680 <= its last minute.

    `m` may be a scalar or an array; `n_minutes` is the panel's length, so its last minute is n_minutes - 1.
    """
    return np.asarray(m) + window <= n_minutes - 1


# --- Q2 event minutes (section 5) -------------------------------------------------------------------

def first_level_minute(high: np.ndarray, low: np.ndarray, present: np.ndarray, i0: int, x: int, level: float,
                       direction: int) -> int:
    """EV1 / EV1_ctrl: the first open minute i0 <= b < x with high_b >= level (long) / low_b <= level (short).

    b = i0 is allowed (section 5 Q2). NO_EVENT when there is none, when the level is missing, or when the trade has
    no open minute. `level` is the cluster's level at the entry state and is an argument here, so nothing inside the
    trade can move it.
    """
    if not np.isfinite(level) or x <= i0:
        return NO_EVENT
    with np.errstate(invalid="ignore"):
        h, l, p = high[i0:x], low[i0:x], present[i0:x]
        hit = p & ((h >= level) if direction > 0 else (l <= level))
    j = ML.first_true(hit)
    return NO_EVENT if j < 0 else int(i0 + j)


def state_bin_of(t0_arch: int, ts_s) -> np.ndarray:
    """Section 3: the last bin stamped <= ts_s - 5 min, over an array of minute stamps.

    The vectorised twin of the map half's scalar `map_state_bin`; the two must agree on every stamp.
    """
    return np.floor_divide(np.asarray(ts_s, dtype=np.int64) - BIN_S - int(t0_arch), BIN_S)


def first_burst_minute(flag: np.ndarray, present: np.ndarray, t0_arch: int, panel_t0_s: int, i0: int, x: int) -> int:
    """EV2 kinds: the first open minute m in [i0, x) whose map as of m carries the burst flag, with every bar the
    flag's three bins use opening at or after i0 (section 5 Q2: a burst formed in pre-entry bins never fires at entry).

    Open is the walker's open set, as `micro_lib.Grids.open_` defines it: the trade is open after the minute's close
    and that close is present. A flag on a minute without a close is no event there and the trade stays at risk on it.
    Minute m reads bin `state_bin_of(t0_arch, t(m))`; that bin's sum spans bins b - 2 ... b, whose earliest bar opens
    at t0_arch + 300 (b - 2). A missing flag (NaN, or a bin outside the series) is likewise no event. NO_EVENT when
    none.
    """
    on = np.nan_to_num(np.asarray(flag, dtype=float), nan=0.0) > 0
    p = np.asarray(present, dtype=bool)[int(i0):int(x)]
    if not len(p):
        return NO_EVENT
    m = np.arange(int(i0), int(i0) + len(p), dtype=np.int64)
    b = state_bin_of(t0_arch, panel_t0_s + MIN_S * m)
    ok = p & (b >= 0) & (b < len(on))
    ok &= t0_arch + BIN_S * (b - 2) >= panel_t0_s + MIN_S * int(i0)      # all three bins' bars inside the trade
    hit = np.zeros(len(m), dtype=bool)
    hit[ok] = on[b[ok]]
    j = ML.first_true(hit)
    return NO_EVENT if j < 0 else int(m[j])


# --- eligibility and the era-matched placebo (section 5 Q2) -----------------------------------------

def eligible_liq(trades: pd.DataFrame, kind: str) -> np.ndarray:
    """Section 5 Q2's per-kind eligibility, read from the `<kind>_eligible` column the map stage writes.

    EV1 / EV1_ctrl are eligible when the respective cluster is defined at the entry state; EV2 / EV2_ctrl /
    EV2_against when the respective burst threshold is defined at the entry minute. Both are map quantities, so the
    run script computes them and stores the answer per kind. A trade ineligible for a kind is neither an event trade
    nor a control of that kind, exactly as `micro_lib.eligible`.
    """
    col = f"{kind}_eligible"
    if col not in trades.columns:
        raise KeyError(f"{col} missing: section 5 Q2's eligibility for {kind} is written by the map stage")
    return trades[col].to_numpy(bool)


def match_liq(trades: pd.DataFrame, grids: ML.Grids, kind: str, window: int,
              era_days: int = ERA_DAYS) -> pd.DataFrame:
    """`micro_lib.match` with section 5 Q2's era window; same columns, same numbers when `era_days` is huge.

    One row per eligible trade with a `kind` event: its elapsed minute, profit bin, control count and (with CV grids)
    its continuation value and matched placebo. A control is another trade of the same population and direction,
    eligible for this kind, with no event of this kind yet, at an elapsed minute within +-`window` of the event's, in
    the same 0.25 R profit bin, not overlapping [entry_i, exit_i] in calendar time, and entering within +-`era_days`
    of trade i. `micro_lib._placebo` averages inside a control first and returns NaN below three controls.
    """
    el = eligible_liq(trades, kind)
    first_abs = trades[f"{kind}_first"].to_numpy(np.int64)
    i0 = trades["i0"].to_numpy(np.int64)
    x = trades["x"].to_numpy(np.int64)
    s = trades["s"].to_numpy(np.int64)
    entry = trades["entry_ts"].to_numpy(np.int64)
    has = (first_abs >= 0) & (first_abs < NO_EVENT)     # -1 (the frozen column) and NO_EVENT both mean no event
    first_e = np.where(has, first_abs - i0, NO_EVENT)
    E = grids.open_.shape[1]
    rows = []
    for i in np.flatnonzero(el & has):
        e = int(first_e[i])
        k = int(grids.bin_[i, e])
        cand = el & (np.arange(len(trades)) != i) & (s == s[i]) & ~((i0 <= x[i]) & (i0[i] <= x))
        cand = cand & (np.abs(entry - entry[i]) <= int(era_days) * 86_400)       # 5 Q2: the era pool
        cand = np.flatnonzero(cand)
        lo, hi = max(0, e - window), min(E, e + window + 1)
        elapsed = np.arange(lo, hi)[None, :]
        at_risk = grids.open_[cand, lo:hi] & (elapsed < first_e[cand][:, None])
        valid = at_risk & (grids.bin_[cand, lo:hi] == k)
        count, count_t = valid.sum(axis=1), at_risk.sum(axis=1)
        keep, keep_t = count > 0, count_t > 0
        row = {"tid": trades["tid"].iat[i], "entry_ts": int(trades["entry_ts"].iat[i]),
               "entry_day": trades["entry_day"].iat[i], "asset": trades["asset"].iat[i],
               "direction": trades["direction"].iat[i], "elapsed_min": e, "bin": k,
               "mark_R": float(grids.mark[i, e]), "controls": int(keep.sum()),
               "controls_time_only": int(keep_t.sum())}
        if grids.cv is not None:
            row["cv"] = float(grids.cv[i, e])
            row["cv_notime"] = float(grids.cv_notime[i, e])
            row["placebo"] = ML._placebo(grids.cv[cand, lo:hi], valid, count, keep)
            row["placebo_notime"] = ML._placebo(grids.cv_notime[cand, lo:hi], valid, count, keep)
            row["placebo_time_only"] = ML._placebo(grids.cv[cand, lo:hi], at_risk, count_t, keep_t)
        rows.append(row)
    cols = ["tid", "entry_ts", "entry_day", "asset", "direction", "elapsed_min", "bin", "mark_R", "controls",
            "controls_time_only"]
    return pd.DataFrame(rows, columns=cols + (["cv", "cv_notime", "placebo", "placebo_notime", "placebo_time_only"]
                                              if grids.cv is not None else []))


# --- statistics under a claimed sign (section 5) ----------------------------------------------------

def p_claimed(point: float, boot: np.ndarray, claimed_sign: int) -> float:
    """Section 5's one-sided p = (1 + #{draws: c (boot - mean) >= c mean}) / (draws + 1), dispatched on c.

    c = -1 is `micro_lib.p_less` (Q2, holding after the event is worth less); c = +1 is the chento arm's
    `chento_lib.p_one_sided` (Q1, the map's level attracts or turns price more than the control map's).
    """
    c = int(claimed_sign)
    if c < 0:
        return ML.p_less(point, boot)
    if c > 0:
        return CL.p_one_sided(point, boot)
    raise ValueError("claimed_sign must be +1 or -1")


def paired_stats(days, values: np.ndarray, axis: list[str], idx: np.ndarray, claimed_sign: int) -> dict:
    """Mean, 95 % interval, one-sided p under the claimed sign, halves and years of one paired quantity.

    `days` and `values` are one per included observation (a state for Q1, an event trade for Q2) and must already be
    in time order: the halves are the earlier and later half of that order, the extra observation going to the
    earlier half (section 5). Non-finite values are dropped, as `micro_lib.summarize` drops rows without a placebo.
    Each bootstrap draw is a sum over a sum over the drawn days (`micro_lib.boot_means`).
    """
    c = int(claimed_sign)
    d = np.asarray([str(x) for x in days], dtype=object)
    v = np.asarray(values, dtype=float)
    ok = np.isfinite(v)
    d, v = d[ok], v[ok]
    n = len(v)
    if n == 0:
        return {"n": 0, "claimed_sign": c}
    if n > 1 and not bool((d[1:] >= d[:-1]).all()):
        raise ValueError("paired_stats needs its observations in time order (the halves are by time)")
    boot = ML.boot_means(d, v, axis, idx)
    point = float(v.mean())
    half = -(-n // 2)
    return {"n": n, "mean": point, "claimed_sign": c,
            "ci95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))],
            "p_one_sided": p_claimed(point, boot, c),
            "first_half": float(v[:half].mean()), "second_half": float(v[half:].mean()) if n > half else float("nan"),
            "first_half_n": half, "second_half_n": n - half,
            "by_year": {k: {"n": int(len(g)), "mean": float(g.mean())}
                        for k, g in pd.Series(v).groupby(pd.Series(d).str[:4].to_numpy())}}


def classify_liq(test: dict, in_family: bool, holm_p: float | None, control: dict | None,
                 claimed_sign: int, band: tuple[float, float]) -> str:
    """Section 5's classification table, evaluated in its order, under the claimed sign `c` and its band.

    Order: DESCRIPTIVE (below the count), NO INFORMATION (the interval inside the band), INFORMATIVE, CONTRARY (the
    interval wholly on the side opposite c), UNDETERMINED. `micro_lib.classify` puts INFORMATIVE first and fixes the
    direction at c = -1, so the twins differ on exactly those two things; L3 pins both.

    `control` carries the comparators of the test's kind, in `paired_stats`'s shape:
      None                                     Q1 -- the test is itself paired against the control map, so the
                                               placebo comparison holds by construction.
      {"placebo": {"n", "p_one_sided"}}        Q2 EV1 -- the paired delta-bar against the kind's control-map twin.
      {"placebo": ..., "sign": {"n", "mean"}}  Q2 EV2 -- and the sign control, Delta-bar(EV2_against).
    A comparator with fewer than 10 included trades is unavailable and the test cannot be INFORMATIVE.
    """
    c = int(claimed_sign)
    if not in_family or not test.get("n"):
        return "DESCRIPTIVE"                                            # 1: below the count
    lo, hi = test["ci95"]
    if band[0] < lo and hi < band[1]:
        return "NO INFORMATION"                                         # 2: the interval inside the band
    unavailable = False
    halves = c * test["first_half"] > 0 and c * test["second_half"] > 0
    if holm_p is not None and holm_p < ALPHA and halves:                # 3: INFORMATIVE
        placebo_ok = sign_ok = True
        if control is not None:
            comp = control.get("placebo")
            if comp is None or comp.get("n", 0) < SIGN_CONTROL_MIN:
                unavailable = True
            else:
                placebo_ok = comp["p_one_sided"] < ALPHA
            if "sign" in control:
                sc = control["sign"]
                if sc is None or sc.get("n", 0) < SIGN_CONTROL_MIN:
                    unavailable = True
                else:
                    sign_ok = c * test["mean"] > c * sc["mean"]         # the test is further in c's direction
        if not unavailable and placebo_ok and sign_ok:
            return "INFORMATIVE"
    if c * (hi if c > 0 else lo) < 0:
        return "CONTRARY"                                               # 4: wholly opposite the claimed sign
    return "UNDETERMINED (comparator unavailable)" if unavailable else "UNDETERMINED"       # 5

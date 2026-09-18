"""Spot-vs-perp stage: per-minute inputs, event kinds, matched placebos and the statistics.

Every constant here is frozen by PREREGISTRATION_SPOT_PERP.md and carries its provenance in a comment; every one is
pinned by a fixture in tests/test_spotperp_events.py. Stage 1's `micro_lib` and the anatomy's `anatomy_lib` are
imported and reused, never edited: the walker, the grids' formulas, the placebo mean, the block bootstrap, Holm and
the summary come from `micro_lib` unchanged, and this module adds the venue split, the running-extreme match, the
era, freshness, volume and session constraints, and the shared pool.

Nothing here reads prod.db, any bot or any feed. `SESSIONS` is read from the text of the short_squeeze bot's config
with `ast.literal_eval`, so no bot code is imported.
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import anatomy_lib as A  # noqa: E402
import micro_lib as M  # noqa: E402
import spotperp_data as SD  # noqa: E402

ROOT = M.ROOT
CACHE = HERE / "cache"
RESULTS = HERE / "results" / "spot_perp"
SS_CONFIG = ROOT / "bots" / "short_squeeze" / "strategy" / "config.py"

# --- frozen constants (section 5's provenance paragraph) ---------------------------------------------
GATE_MIN = A.FLOW_MIN                      # 60, the shared window-inside-trade gate for every kind
FLOW_PRESENT = A.FLOW_MIN_PRESENT          # 50 of 60 minutes present per venue
FRESH_MAX = A.STALL_EDGES[1]               # 60, the state map's "fresh" bin, rung 2's condition
IN_PROFIT_R = 1.0                          # F1_in_profit's gate; A.SPIKE (2 %) is squeeze_bull's +1 R
ERA_DAYS = 365                             # micro_explore.ERA_DAYS
OI_LAG_S = A.METRIC_LAG_S                  # 300
OI_ELIGIBLE_FROM = int(datetime(2022, 1, 1, 1, 5, tzinfo=timezone.utc).timestamp())
COMP_ELIGIBLE_FROM = int(datetime(2023, 8, 4, 9, 0, tzinfo=timezone.utc).timestamp())
F3_PT, F3_OT, F3_VR = 0.0012, 0.0008, 1.35  # the external site's state, one parameterisation, reported only
Z_EXTREME = M.Z_EXTREME                     # 3.0
MIN_CONTROLS = M.MIN_CONTROLS               # 3
MIN_EVENT_TRADES = M.MIN_EVENT_TRADES       # 30
MIN_LINE_TRADES = M.SIGN_CONTROL_MIN        # 10, the floor for a control and for each robustness line
SYMBOL = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}

SUBPOPS = ("chento_BTC", "chento_ETH", "squeeze_bull", "short_squeeze")
POP_OF = {"chento_BTC": "chento", "chento_ETH": "chento",
          "squeeze_bull": "squeeze_bull", "short_squeeze": "short_squeeze"}
FAMILY_SUBPOPS = ("chento_BTC", "squeeze_bull")
REPLICATION_SUBPOP = "chento_ETH"
FAMILY_CANDIDATES = ("F1_against", "F2_perp_led")

# kind -> the family whose inputs it needs (shared eligibility, section 5 rule 5)
KIND_FAMILY = {
    "F1_against": "f1", "F1_spot_confirmed": "f1", "F1_spot_led": "f1", "F1_mirror": "f1",
    "F1_prem_only": "f1", "F1_prem_only_ctrl": "f1", "F1_flow_only": "f1", "F1_flow_only_ctrl": "f1",
    "F1_any_extreme": "f1", "F1_in_profit": "f1", "F1_in_profit_ctrl": "f1",
    "F2_perp_led": "f2", "F2_spot_confirmed": "f2", "F2_spot_led": "f2", "F2_mirror": "f2",
    "F2_perp_extreme": "f2", "F2_perp_extreme_ctrl": "f2",
    "F1_oi_up": "oi", "F1_oi_up_ctrl": "oi", "F1_oi_down": "oi", "F1_oi_down_ctrl": "oi",
    "F1_against_usdt_fdusd": "comp", "F1_against_usdt_fdusd_ctrl": "comp",
    "F3": "f3", "F3_spot_confirmed": "f3", "F3_spot_led": "f3",
}
KINDS = tuple(KIND_FAMILY)
MIRROR_KINDS = ("F1_mirror", "F2_mirror")                      # matched on extremes against the trade
DECISION_CONTROL = {"F1_against": "F1_spot_confirmed", "F2_perp_led": "F2_spot_confirmed"}
REPORTED_CONTROL = {
    "F1_prem_only": "F1_prem_only_ctrl", "F1_flow_only": "F1_flow_only_ctrl",
    "F1_in_profit": "F1_in_profit_ctrl", "F2_perp_extreme": "F2_perp_extreme_ctrl",
    "F1_oi_up": "F1_oi_up_ctrl", "F1_oi_down": "F1_oi_down_ctrl",
    "F1_against_usdt_fdusd": "F1_against_usdt_fdusd_ctrl", "F3": "F3_spot_confirmed",
}
OI_KINDS = ("F1_oi_up", "F1_oi_up_ctrl", "F1_oi_down", "F1_oi_down_ctrl")
COMP_KINDS = ("F1_against_usdt_fdusd", "F1_against_usdt_fdusd_ctrl")
F3_KINDS = ("F3", "F3_spot_confirmed", "F3_spot_led")
NO_EVENT = M.NO_EVENT


def sessions() -> dict[str, tuple[int, int]]:
    """The bot's SESSIONS literal, read from the file text; no bot code is imported."""
    tree = ast.parse(SS_CONFIG.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "SESSIONS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError(f"SESSIONS not found in {SS_CONFIG}")


SESSION_EDGES = sessions()                                     # asia 0-7, london 7-14, ny 14-21
SESSION_NAMES = tuple(SESSION_EDGES) + ("remainder",)          # the 21-24 UTC remainder is this stage's fourth bucket


def session_bucket(hour: np.ndarray) -> np.ndarray:
    out = np.full(len(hour), len(SESSION_NAMES) - 1, dtype=np.int8)
    for i, (lo, hi) in enumerate(SESSION_EDGES.values()):
        out[(hour >= lo) & (hour < hi)] = i
    return out


# --- per-minute inputs ------------------------------------------------------------------------------

@dataclass
class Panel:
    """Every per-minute quantity of one asset, on the perp panel's grid."""
    asset: str
    t0_s: int
    n: int
    ser: M.Series                 # stage 1's series: prices and z_flow (the perp z), built unchanged
    dP: np.ndarray
    DP60: np.ndarray
    dS: np.ndarray
    DS60: np.ndarray
    zS: np.ndarray
    prem: np.ndarray
    dprem: np.ndarray
    pt: np.ndarray
    vr: np.ndarray
    session: np.ndarray
    weekend: np.ndarray
    oi: np.ndarray | None = None
    doi: np.ndarray | None = None
    ot: np.ndarray | None = None
    dSplus: np.ndarray | None = None
    DSplus60: np.ndarray | None = None

    @property
    def zP(self) -> np.ndarray:
        return self.ser.z_flow


def minute_delta(volume: np.ndarray, taker_buy: np.ndarray) -> np.ndarray:
    """2 x taker buy - volume, missing on a dead or absent minute (stage 1's flow_features form)."""
    with np.errstate(invalid="ignore"):
        return np.where(volume > 0, 2.0 * taker_buy - volume, np.nan)


def present_window_sum(d: np.ndarray, window: int = GATE_MIN, min_present: int = FLOW_PRESENT) -> np.ndarray:
    """Sum of the present values over b - window + 1 ... b, missing below `min_present` present."""
    s = pd.Series(d)
    total = s.rolling(window, min_periods=1).sum().to_numpy()
    count = s.notna().rolling(window, min_periods=1).sum().to_numpy()
    out = np.where(count >= min_present, total, np.nan)
    out[:window - 1] = np.nan
    return out


def lag_diff(x: np.ndarray, lag: int = GATE_MIN) -> np.ndarray:
    out = np.full(len(x), np.nan)
    out[lag:] = x[lag:] - x[:-lag]
    return out


def lag_ratio(x: np.ndarray, lag: int = GATE_MIN) -> np.ndarray:
    out = np.full(len(x), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        out[lag:] = x[lag:] / x[:-lag] - 1.0
    out[~np.isfinite(out)] = np.nan
    return out


def oi_minutes(oi_5m: np.ndarray, m_t0_s: int, t0_s: int, n: int, cut_close_s: int | None = None) -> np.ndarray:
    """The 5-minute archive value usable at each minute's close: stamp T is used from T + 300 s, at most 6 slots back."""
    oi = oi_5m.copy()
    if cut_close_s is not None:                                  # P10: a slot published after the cut is unknown
        stamps = m_t0_s + 300 * np.arange(len(oi))
        oi[stamps + OI_LAG_S > cut_close_s] = np.nan
    out = np.full(n, np.nan)
    close_s = t0_s + 60 * np.arange(n) + 60                      # the minute's close
    k = (close_s - OI_LAG_S - m_t0_s) // 300
    for back in range(A.METRIC_MAX_SLOTS_BACK + 1):
        idx = k - back
        ok = (idx >= 0) & (idx < len(oi)) & ~np.isfinite(out)
        if not ok.any():
            break
        out[ok] = oi[idx[ok]]
    return out


def build_panel(asset: str, raw_perp: dict, spot: dict, prem: np.ndarray, oi_5m=None, m_t0_s=None,
                fdusd: dict | None = None, n_minutes: int | None = None, cut_close_s: int | None = None) -> Panel:
    """All per-minute inputs for one asset. `n_minutes` truncates every array for the causality check (P10)."""
    n = len(raw_perp["close"]) if n_minutes is None else int(n_minutes)
    ser = M.build_series(asset, raw_perp, n)                     # perp prices and z_flow, stage 1 unchanged
    t0_s = raw_perp["t0_s"]
    dP = minute_delta(raw_perp["volume"][:n], raw_perp["taker_buy_volume"][:n])
    dS = minute_delta(spot["volume"][:n], spot["taker_buy_volume"][:n])
    zS = M.flow_features(spot["open"][:n], spot["close"][:n], spot["volume"][:n], spot["taker_buy_volume"][:n])[0]
    p = prem[:n].copy()
    hour = ((t0_s // 3600) + np.arange(n) // 60) % 24
    weekday = ((t0_s // 86400 + 4) + np.arange(n) // 1440) % 7   # 1970-01-01 was a Thursday
    panel = Panel(asset=asset, t0_s=t0_s, n=n, ser=ser,
                  dP=dP, DP60=present_window_sum(dP), dS=dS, DS60=present_window_sum(dS), zS=zS,
                  prem=p, dprem=lag_diff(p), pt=lag_ratio(ser.close), vr=A.volume_ratio(raw_perp["volume"][:n]),
                  session=session_bucket(hour), weekend=weekday >= 5)
    if oi_5m is not None:
        panel.oi = oi_minutes(oi_5m, m_t0_s, t0_s, n, cut_close_s)
        panel.doi = lag_diff(panel.oi)
        panel.ot = lag_ratio(panel.oi)
    if fdusd is not None:
        d_f = minute_delta(fdusd["volume"][:n], fdusd["taker_buy_volume"][:n])
        absent = ~np.isfinite(fdusd["open"][:n])                 # no archive row: the sum is missing
        d_f = np.where(absent, np.nan, np.nan_to_num(d_f))       # a carried zero-volume minute contributes zero
        panel.dSplus = np.where(np.isnan(dS) | np.isnan(d_f), np.nan, dS + d_f)
        panel.DSplus60 = present_window_sum(panel.dSplus)
    return panel


# --- running extremes -------------------------------------------------------------------------------

def running_extreme(values: np.ndarray) -> np.ndarray:
    """New running maximum of `values` over the strictly earlier minutes; the first minute is never one."""
    if len(values) == 0:                                         # a trade that exits inside its entry minute
        return np.zeros(0, dtype=bool)
    out = A.new_running_high(values)
    out[0] = False                                               # A.new_running_high's prior at index 0 is -inf
    return out


def stall_from_extreme(x_mask: np.ndarray) -> np.ndarray:
    """Minutes since the last True; NaN before the first (the anatomy's definition, entry minute excluded)."""
    idx = np.where(x_mask, np.arange(len(x_mask)), -1)
    last = np.maximum.accumulate(idx)
    return np.where(last >= 0, np.arange(len(x_mask)) - last, np.nan).astype(float)


# --- per-trade grids --------------------------------------------------------------------------------

@dataclass
class Grids:
    """Rows are trades, columns elapsed minutes from the entry minute."""
    open_: np.ndarray
    mark: np.ndarray
    bin_: np.ndarray
    cv: np.ndarray | None
    cv_notime: np.ndarray | None
    X: np.ndarray
    Xneg: np.ndarray
    stall: np.ndarray
    stall_neg: np.ndarray
    ord_: np.ndarray
    vr: np.ndarray
    pt: np.ndarray
    session: np.ndarray
    weekend: np.ndarray
    gate: np.ndarray
    ok: dict[str, np.ndarray]
    known: dict[str, np.ndarray]
    known_neg: dict[str, np.ndarray]
    kind: dict[str, np.ndarray]


def _family_ok(p: Panel, sl: slice) -> dict[str, np.ndarray]:
    f1 = np.isfinite(p.DP60[sl]) & np.isfinite(p.DS60[sl]) & np.isfinite(p.dprem[sl])
    f2 = np.isfinite(p.zP[sl]) & np.isfinite(p.zS[sl])
    out = {"f1": f1, "f2": f2,
           "f3": (np.isfinite(p.pt[sl]) & np.isfinite(p.vr[sl]) & np.isfinite(p.DP60[sl]) & np.isfinite(p.DS60[sl])
                  & (np.isfinite(p.ot[sl]) if p.ot is not None else np.zeros(f1.shape, bool))),
           "oi": f1 & (np.isfinite(p.doi[sl]) if p.doi is not None else np.zeros(f1.shape, bool)),
           "comp": (np.isfinite(p.dprem[sl]) & np.isfinite(p.DP60[sl])
                    & (np.isfinite(p.DSplus60[sl]) if p.DSplus60 is not None else np.zeros(f1.shape, bool)))}
    return out


def kind_masks(p: Panel, sl: slice, s: int, X: np.ndarray, Xneg: np.ndarray, mark: np.ndarray,
               ok: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Every kind's mask over one trade's minutes, before the open and gate masks are applied."""
    dprem, DP, DS = s * p.dprem[sl], s * p.DP60[sl], s * p.DS60[sl]
    zP, zS = s * p.zP[sl], s * p.zS[sl]
    m = {}
    with np.errstate(invalid="ignore"):
        m["F1_against"] = X & (dprem > 0) & (DP > 0) & (DS <= 0)
        m["F1_spot_confirmed"] = X & (dprem > 0) & (DP > 0) & (DS > 0)
        m["F1_spot_led"] = X & (dprem < 0) & (DS > 0) & (DP <= 0)
        m["F1_prem_only"] = X & (dprem > 0)
        m["F1_prem_only_ctrl"] = X & (dprem < 0)
        m["F1_flow_only"] = X & (DP > 0) & (DS <= 0)
        m["F1_flow_only_ctrl"] = X & (DP > 0) & (DS > 0)
        m["F1_any_extreme"] = X.copy()
        m["F1_in_profit"] = m["F1_against"] & (mark >= IN_PROFIT_R)
        m["F1_in_profit_ctrl"] = m["F1_spot_confirmed"] & (mark >= IN_PROFIT_R)
        m["F2_perp_led"] = X & (zP >= Z_EXTREME) & (zS <= 0)
        m["F2_spot_confirmed"] = X & (zP >= Z_EXTREME) & (zS > 0)
        m["F2_spot_led"] = X & (zS >= Z_EXTREME) & (zP <= 0)
        m["F2_perp_extreme"] = X & (zP >= Z_EXTREME)
        m["F2_perp_extreme_ctrl"] = X & (zS >= Z_EXTREME)
        # the s -> -s mirror: the same pattern at a new running extreme against the trade
        dpremN, DPN, DSN = -dprem, -DP, -DS
        m["F1_mirror"] = Xneg & (dpremN > 0) & (DPN > 0) & (DSN <= 0)
        m["F2_mirror"] = Xneg & (-zP >= Z_EXTREME) & (-zS <= 0)
        if p.doi is not None:
            doi = p.doi[sl]
            m["F1_oi_up"] = m["F1_against"] & (doi > 0)
            m["F1_oi_up_ctrl"] = m["F1_spot_confirmed"] & (doi > 0)
            m["F1_oi_down"] = m["F1_against"] & (doi <= 0)
            m["F1_oi_down_ctrl"] = m["F1_spot_confirmed"] & (doi <= 0)
        if p.DSplus60 is not None:
            DSp = s * p.DSplus60[sl]
            m["F1_against_usdt_fdusd"] = X & (dprem > 0) & (DP > 0) & (DSp <= 0)
            m["F1_against_usdt_fdusd_ctrl"] = X & (dprem > 0) & (DP > 0) & (DSp > 0)
        if p.ot is not None:
            base = X & (s * p.pt[sl] > F3_PT) & (p.ot[sl] > F3_OT) & (p.vr[sl] >= F3_VR)
            m["F3"] = base & (DP > 0) & (DS <= 0)
            m["F3_spot_confirmed"] = base & (DP > 0) & (DS > 0)
            m["F3_spot_led"] = base & (DS > 0) & (DP <= 0)
    shape = X.shape
    for kind in KINDS:
        if kind not in m:
            m[kind] = np.zeros(shape, dtype=bool)
        m[kind] = m[kind] & ok[KIND_FAMILY[kind]]
    return m


def build_grids(trades: pd.DataFrame, panels: dict[str, Panel], with_cv: bool) -> Grids:
    lengths = (trades["x"] - trades["i0"]).to_numpy(np.int64)
    T, E = len(trades), int(lengths.max())
    z = lambda dtype=float, fill=np.nan: np.full((T, E), fill, dtype=dtype)  # noqa: E731
    g = Grids(open_=np.zeros((T, E), bool), mark=z(), bin_=np.full((T, E), -(10 ** 9), np.int64),
              cv=z() if with_cv else None, cv_notime=z() if with_cv else None,
              X=np.zeros((T, E), bool), Xneg=np.zeros((T, E), bool), stall=z(), stall_neg=z(),
              ord_=np.zeros((T, E), np.int32), vr=z(), pt=z(),
              session=np.full((T, E), -1, np.int8), weekend=np.zeros((T, E), bool),
              gate=np.zeros((T, E), bool),
              ok={f: np.zeros((T, E), bool) for f in ("f1", "f2", "f3", "oi", "comp")},
              known={f: np.zeros((T, E), bool) for f in ("f1", "f2", "f3", "oi", "comp")},
              known_neg={f: np.zeros((T, E), bool) for f in ("f1", "f2", "f3", "oi", "comp")},
              kind={k: np.zeros((T, E), bool) for k in KINDS})
    for r, tr in enumerate(trades.itertuples(index=False)):
        p = panels[tr.asset]
        sl = slice(tr.i0, tr.x)
        L = tr.x - tr.i0
        close = p.ser.close[sl]
        g.open_[r, :L] = np.isfinite(close)
        g.mark[r, :L] = tr.s * (close - tr.entry) / tr.risk
        if with_cv:
            g.cv[r, :L] = tr.s * (tr.exit_price - close) / tr.risk
            g.cv_notime[r, :L] = tr.s * (tr.exit_price_notime - close) / tr.risk
        up = p.ser.high[sl] if tr.s > 0 else -p.ser.low[sl]
        down = -p.ser.low[sl] if tr.s > 0 else p.ser.high[sl]
        X, Xneg = running_extreme(up), running_extreme(down)
        g.X[r, :L], g.Xneg[r, :L] = X, Xneg
        g.stall[r, :L], g.stall_neg[r, :L] = stall_from_extreme(X), stall_from_extreme(Xneg)
        g.ord_[r, :L] = np.concatenate([[0], np.cumsum(X)[:-1]]).astype(np.int32)
        g.vr[r, :L], g.pt[r, :L] = p.vr[sl], p.pt[sl]
        g.session[r, :L], g.weekend[r, :L] = p.session[sl], p.weekend[sl]
        g.gate[r, :L] = np.arange(L) >= GATE_MIN
        ok = _family_ok(p, sl)
        masks = kind_masks(p, sl, tr.s, X, Xneg, g.mark[r, :L], ok)
        keep = g.open_[r, :L] & g.gate[r, :L]
        for f, v in ok.items():
            g.ok[f][r, :L] = v
            # status is unknown from the first gated extreme whose inputs are missing (section 6); the mirror kinds
            # ask the same question of the extremes against the trade, so the two directions are tracked apart
            g.known[f][r, :L] = ~np.maximum.accumulate(X & g.gate[r, :L] & ~v)
            g.known_neg[f][r, :L] = ~np.maximum.accumulate(Xneg & g.gate[r, :L] & ~v)
        for k, v in masks.items():
            g.kind[k][r, :L] = v & keep
    with np.errstate(invalid="ignore"):
        g.bin_ = np.where(g.open_, np.floor(g.mark / M.BIN_R), -(10 ** 9)).astype(np.int64)
    return g


def first_events(trades: pd.DataFrame, g: Grids) -> pd.DataFrame:
    """`<kind>_first` (absolute minute, -1 when none) and `<kind>_minutes` per trade."""
    out = {}
    i0 = trades["i0"].to_numpy(np.int64)
    for kind in KINDS:
        el = eligible(trades, kind)
        mask = g.kind[kind] & el[:, None]
        any_ = mask.any(axis=1)
        first = np.where(any_, mask.argmax(axis=1) + i0, -1)
        out[f"{kind}_first"] = first
        out[f"{kind}_minutes"] = mask.sum(axis=1)
    return pd.concat([trades.reset_index(drop=True), pd.DataFrame(out)], axis=1)


def eligible(trades: pd.DataFrame, kind: str) -> np.ndarray:
    if kind in OI_KINDS or kind in F3_KINDS:
        return ((trades["asset"] == "BTC") & (trades["entry_ts"] >= OI_ELIGIBLE_FROM)).to_numpy()
    if kind in COMP_KINDS:
        return ((trades["asset"] == "BTC") & (trades["entry_ts"] >= COMP_ELIGIBLE_FROM)).to_numpy()
    return np.ones(len(trades), dtype=bool)


# --- placebos ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Spec:
    """One placebo. `shared` drops the no-prior-event and unknown-status filters (the control contrast)."""
    name: str
    extreme: str = "X"          # "X", "fresh" or "none"
    era: bool = True
    bin: bool = True
    rvol: bool = False
    rsession: bool = False
    overlap: str = "exit"       # "exit" or "horizon"
    cv: str = "shipped"         # "shipped" or "notime"
    shared: bool = False


RUNGS = (Spec("rung1"), Spec("rung2", extreme="fresh"), Spec("rung3", era=False))
SECONDARY = (Spec("S1", extreme="none"), Spec("S2", extreme="none", era=False),
             Spec("S3", bin=False), Spec("S4", cv="notime"), Spec("S5", overlap="horizon"))


def robustness_specs(rung: Spec) -> tuple[Spec, Spec]:
    return (replace(rung, name=f"rvol_{rung.name}", rvol=True),
            replace(rung, name=f"rsession_{rung.name}", rsession=True))


def shared_spec(rung: Spec) -> Spec:
    return replace(rung, name=f"shared_{rung.name}", shared=True)


def match(trades: pd.DataFrame, g: Grids, kind: str, window: int, spec: Spec, collect: bool = False):
    """One row per eligible trade with a `kind` event: its continuation value, its placebo and the control count."""
    el = eligible(trades, kind)
    first_abs = trades[f"{kind}_first"].to_numpy(np.int64)
    i0 = trades["i0"].to_numpy(np.int64)
    x = trades["x"].to_numpy(np.int64)
    s = trades["s"].to_numpy(np.int64)
    horizon = trades["horizon_min"].to_numpy(np.int64)
    first_e = np.where(first_abs >= 0, first_abs - i0, NO_EVENT)
    family = KIND_FAMILY[kind]
    extreme_grid = g.Xneg if kind in MIRROR_KINDS else g.X
    stall_grid = g.stall_neg if kind in MIRROR_KINDS else g.stall
    cv_grid = g.cv if spec.cv == "shipped" else g.cv_notime
    E = g.open_.shape[1]
    lo_i, hi_i = (i0, x) if spec.overlap == "exit" else (i0, i0 + horizon)
    rows, collected = [], []
    for i in np.flatnonzero(el & (first_abs >= 0)):
        e = int(first_e[i])
        k = int(g.bin_[i, e])
        cand = el & (np.arange(len(trades)) != i) & (s == s[i]) & ~((lo_i <= hi_i[i]) & (lo_i[i] <= hi_i))
        if spec.era:
            cand &= np.abs(trades["entry_ts"].to_numpy() - trades["entry_ts"].iat[i]) <= ERA_DAYS * 86400
        cand = np.flatnonzero(cand)
        lo, hi = max(0, e - window), min(E, e + window + 1)
        span = np.arange(lo, hi)[None, :]
        at_risk = g.open_[cand, lo:hi] & g.gate[cand, lo:hi] & g.ok[family][cand, lo:hi]
        if not spec.shared:
            known = g.known_neg if kind in MIRROR_KINDS else g.known
            at_risk &= (span < first_e[cand][:, None]) & known[family][cand, lo:hi]
        if spec.bin:
            at_risk &= g.bin_[cand, lo:hi] == k
        if spec.extreme == "X":
            at_risk &= extreme_grid[cand, lo:hi]
        elif spec.extreme == "fresh":
            with np.errstate(invalid="ignore"):
                at_risk &= stall_grid[cand, lo:hi] < FRESH_MAX
        if spec.rvol:
            side = g.vr[i, e] >= 1.0
            with np.errstate(invalid="ignore"):
                at_risk &= np.isfinite(g.vr[cand, lo:hi]) & ((g.vr[cand, lo:hi] >= 1.0) == side)
            if not np.isfinite(g.vr[i, e]):
                continue                                          # the event minute has no VR: dropped, counted
        if spec.rsession:
            at_risk &= (g.session[cand, lo:hi] == g.session[i, e]) & (g.weekend[cand, lo:hi] == g.weekend[i, e])
        count = at_risk.sum(axis=1)
        keep = count > 0
        row = {"tid": trades["tid"].iat[i], "entry_ts": int(trades["entry_ts"].iat[i]),
               "entry_day": trades["entry_day"].iat[i], "asset": trades["asset"].iat[i],
               "direction": trades["direction"].iat[i], "elapsed_min": e, "bin": k,
               "mark_R": float(g.mark[i, e]), "ord": int(g.ord_[i, e]), "vr": float(g.vr[i, e]),
               "session": int(g.session[i, e]), "weekend": bool(g.weekend[i, e]),
               "controls": int(keep.sum()), "control_minutes": int(count.sum())}
        if cv_grid is not None:
            row["cv"] = float(cv_grid[i, e])
            row["placebo"] = M._placebo(cv_grid[cand, lo:hi], at_risk, count, keep)
            row["control_tids"] = ";".join(trades["tid"].iat[j] for j in cand[keep])
        rows.append(row)
        if collect and keep.any():                                # the contributing control minutes' covariates
            rr, cc = np.nonzero(at_risk)
            collected.append({"vr": g.vr[cand[rr], lo + cc], "ord": g.ord_[cand[rr], lo + cc],
                              "pt": g.pt[cand[rr], lo + cc], "session": g.session[cand[rr], lo + cc],
                              "weekend": g.weekend[cand[rr], lo + cc]})
    cols = ["tid", "entry_ts", "entry_day", "asset", "direction", "elapsed_min", "bin", "mark_R", "ord", "vr",
            "session", "weekend", "controls", "control_minutes"]
    if cv_grid is not None:
        cols += ["cv", "placebo", "control_tids"]
    out = pd.DataFrame(rows, columns=cols)
    if "cv" in out:
        out["delta"] = out["cv"] - out["placebo"]
    if collect:
        pooled = {k: np.concatenate([c[k] for c in collected]) if collected else np.zeros(0)
                  for k in ("vr", "ord", "pt", "session", "weekend")}
        return out, pooled
    return out


def included(rows: pd.DataFrame) -> int:
    """Event trades with at least MIN_CONTROLS contributing controls. Computable before any continuation value:
    `M._placebo` returns NaN under exactly that condition, so this equals the count of finite placebos."""
    if not len(rows):
        return 0
    return int((rows["controls"] >= MIN_CONTROLS).sum())


def decision_rung(counts: dict[str, int]) -> str | None:
    for spec in RUNGS:
        if counts.get(spec.name, 0) >= MIN_EVENT_TRADES:
            return spec.name
    return None


# --- statistics -------------------------------------------------------------------------------------

def boot_difference(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    """Block bootstrap of mean(a.delta) - mean(b.delta) on the union of their entry days."""
    a, b = a[np.isfinite(a["delta"])], b[np.isfinite(b["delta"])]
    if not len(a) or not len(b):
        return {"n_a": len(a), "n_b": len(b)}
    axis = M.day_axis(pd.concat([a["entry_day"], b["entry_day"]]))
    idx = M.block_indices(len(axis))
    pos = {d: i for i, d in enumerate(axis)}

    def sums(rows):
        s, c = np.zeros(len(axis)), np.zeros(len(axis))
        for d, v in zip(rows["entry_day"], rows["delta"].to_numpy(float)):
            s[pos[d]] += v
            c[pos[d]] += 1
        return s, c

    sa, ca = sums(a)
    sb, cb = sums(b)
    with np.errstate(invalid="ignore", divide="ignore"):
        boot = sa[idx].sum(axis=1) / ca[idx].sum(axis=1) - sb[idx].sum(axis=1) / cb[idx].sum(axis=1)
    boot = boot[np.isfinite(boot)]
    point = float(a["delta"].mean() - b["delta"].mean())
    return {"n_a": int(len(a)), "n_b": int(len(b)), "mean": point,
            "ci95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))]}


def classify(test: dict, in_family: bool, holm_p: float | None, control: dict | None,
             shared_test: dict | None, shared_control: dict | None,
             rvol: dict | None, rsession: dict | None) -> str:
    """Section 8's table. The exact string INFORMATIVE is the only label that can promote."""
    if not in_family or not test.get("n"):
        return "DESCRIPTIVE"
    lo, hi = test["ci95"]
    halves_negative = test["first_half"] < 0 and test["second_half"] < 0
    if holm_p is not None and holm_p < M.ALPHA and halves_negative:
        rob_ok = all(r is not None and r.get("n", 0) >= MIN_LINE_TRADES and r["mean"] < 0
                     for r in (rvol, rsession))
        rob_available = all(r is not None and r.get("n", 0) >= MIN_LINE_TRADES for r in (rvol, rsession))
        ctrl_available = (control is not None and control.get("n", 0) >= MIN_LINE_TRADES
                          and shared_control is not None and shared_control.get("n", 0) >= MIN_LINE_TRADES
                          and shared_test is not None and shared_test.get("n", 0))
        if not rob_available:
            return "INFORMATIVE, robustness unavailable"
        if not ctrl_available:
            return "INFORMATIVE, sign control unavailable"
        if rob_ok and shared_test["mean"] < shared_control["mean"]:
            return "INFORMATIVE"
    if -M.EQUIVALENCE_R < lo and hi < M.EQUIVALENCE_R:
        return "NO INFORMATION >= 0.10 R"
    if lo > 0:
        return "CONTRARY"
    return "UNDETERMINED"

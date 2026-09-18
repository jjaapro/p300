"""Stage R of the exit-policy study: move vs implied range (I) and the rejection-wick exit (J).

PREREGISTRATION_RANGE_WICK.md. The populations, the price path and the walker are stage 1's (micro_lib); the running
extremes, the session buckets, the bootstrap of a difference and the classification are the spot-vs-perp stage's
(spotperp_lib); the per-minute inputs, the kinds, the grids and the matched placebo are rebuilt here over this stage's
kinds. Nothing here reads a bot, a feed or prod.db (the DVOL export is rangewick_run's).
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import anatomy_lib as A  # noqa: E402
import micro_lib as M  # noqa: E402
import spotperp_lib as SP  # noqa: E402

RESULTS = HERE / "results" / "range_wick"
DVOL_CSV = RESULTS / "dvol_daily.csv"

# --- frozen numbers (section 5) ---------------------------------------------------------------------
GATE_MIN = SP.GATE_MIN                      # 60, the shared gate
IMP_MULT = 1.0                              # one implied day, the mechanism's own number
ANNUAL_DAYS = 365                           # DVOL is an annualised volatility; one day is /sqrt(365)
RV_DAYS = 30                                # the repository's regime window (ret_30d)
P_MIN_R = 0.3                               # Paladin's best variant: armed at >= 0.3 R at the bar's extreme
WICK_FRAC = 0.4                             # Paladin's best variant: rejection shadow >= 0.4 of the range
TOL = 0.0015                                # Paladin's level tolerance, 0.15 % of entry
BAR_MIN = 15                                # the tested bar; the 1-minute arm is reported
DVOL_FROM = "2022-09-08"                    # first UTC day with a completed prior row in deribit_dvol_daily
DVOL_FROM_TS = int(datetime(2022, 9, 8, tzinfo=timezone.utc).timestamp())
EPS_REL, EPS_ABS = 1e-9, 1e-12              # a boundary case is an event; float rounding never decides one
FRESH_MAX = SP.FRESH_MAX                    # 60, stall < 60 is "fresh"
ERA_DAYS = SP.ERA_DAYS                      # 365
MIN_CONTROLS = M.MIN_CONTROLS               # 3
MIN_EVENT_TRADES = M.MIN_EVENT_TRADES       # 30
MIN_LINE_TRADES = M.SIGN_CONTROL_MIN        # 10
COVERAGE_MIN = M.BOOK_COVERAGE_MIN          # 0.90
NO_EVENT = M.NO_EVENT
DAY_MIN = 1440

SUBPOPS = SP.SUBPOPS
POP_OF = SP.POP_OF
FAMILY_SUBPOPS = SP.FAMILY_SUBPOPS          # chento_BTC, squeeze_bull
REPLICATION_SUBPOP = SP.REPLICATION_SUBPOP  # chento_ETH
FAMILY_CANDIDATES = ("I1_implied", "J_wick")
DECISION_CONTROL = {"I1_implied": "I1_realised", "J_wick": "J_nolevel"}
REPORTED_CONTROL = {"I1_implied_only": "I1_realised", "I2_day": "I2_day_realised", "J_shape": "J_nolevel",
                    "J_accept": "J_wick", "J_wick_1m": "J_nolevel_1m"}
KIND_FAMILY = {"I1_implied": "I", "I1_realised": "I", "I1_implied_only": "I", "I2_day": "I", "I2_day_realised": "I",
               "J_wick": "J", "J_nolevel": "J", "J_accept": "J", "J_shape": "J",
               "J_wick_1m": "J1", "J_nolevel_1m": "J1"}
KINDS = tuple(KIND_FAMILY)
FAMILIES = ("I", "J", "J1")
OVERLAY_KINDS = FAMILY_CANDIDATES


# --- DVOL and the per-day scales --------------------------------------------------------------------

def load_dvol(path: Path = DVOL_CSV) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"asset": str, "day": str})


def dvol_facts(df: pd.DataFrame) -> dict:
    """Rows, span, missing days inside the span and bad closes, per asset (P4)."""
    out = {}
    for asset, g in df.groupby("asset"):
        days = sorted(g["day"])
        d0, d1 = pd.Timestamp(days[0]), pd.Timestamp(days[-1])
        expected = {d.strftime("%Y-%m-%d") for d in pd.date_range(d0, d1, freq="D")}
        missing = sorted(expected - set(days))
        close = g["close"].to_numpy(float)
        out[asset] = {"rows": int(len(g)), "first_day": days[0], "last_day": days[-1],
                      "missing_days_inside_span": len(missing), "missing_days": missing[:50],
                      "bad_closes": int((~np.isfinite(close) | (close <= 0)).sum())}
    return out


def _day_number(t0_s: int) -> int:
    assert t0_s % 86400 == 0, "the panel must start at a UTC midnight"
    return t0_s // 86400


def implied_daily_move(dvol: pd.DataFrame, asset: str, t0_s: int, n: int) -> np.ndarray:
    """IMP per minute: the prior UTC day's DVOL close / 100 / sqrt(365); NaN before DVOL_FROM or after a gap."""
    closes = {d: float(c) for d, c in zip(dvol.loc[dvol["asset"] == asset, "day"],
                                           dvol.loc[dvol["asset"] == asset, "close"])}
    day0 = _day_number(t0_s)
    ndays = -(-n // DAY_MIN)
    per_day = np.full(ndays, np.nan)
    for k in range(ndays):
        day = M.utc_day((day0 + k) * 86400)
        if day < DVOL_FROM:
            continue
        c = closes.get(M.utc_day((day0 + k - 1) * 86400))
        if c is not None and np.isfinite(c) and c > 0:
            per_day[k] = c / 100.0 / math.sqrt(ANNUAL_DAYS)
    return np.repeat(per_day, DAY_MIN)[:n]


def day_closes(close: np.ndarray, n: int) -> np.ndarray:
    """The close of the last present minute of each UTC day; NaN for a day with none."""
    ndays = -(-n // DAY_MIN)
    padded = np.full(ndays * DAY_MIN, np.nan)
    padded[:n] = close[:n]
    r = padded.reshape(ndays, DAY_MIN)
    present = np.isfinite(r)
    has = present.any(axis=1)
    last = DAY_MIN - 1 - np.argmax(present[:, ::-1], axis=1)
    out = np.where(has, r[np.arange(ndays), last], np.nan)
    return out


def day_opens(open_: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    """The open of the first present minute of each UTC day, per minute; NaN for a day with none."""
    ndays = -(-n // DAY_MIN)
    po, pc = np.full(ndays * DAY_MIN, np.nan), np.full(ndays * DAY_MIN, np.nan)
    po[:n], pc[:n] = open_[:n], close[:n]
    ro, rc = po.reshape(ndays, DAY_MIN), pc.reshape(ndays, DAY_MIN)
    present = np.isfinite(rc)
    has = present.any(axis=1)
    first = np.argmax(present, axis=1)
    per_day = np.where(has, ro[np.arange(ndays), first], np.nan)
    return np.repeat(per_day, DAY_MIN)[:n]


def realised_daily_vol(close: np.ndarray, n: int, days: int = RV_DAYS) -> np.ndarray:
    """RV per minute for day D: sample sd (ddof 1) of the `days` daily log returns ending at D-1, which needs the
    days + 1 closes C[D-days-1] ... C[D-1] all present; NaN otherwise."""
    C = day_closes(close, n)
    ndays = len(C)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.log(C[1:] / C[:-1])                        # r[d-1] = ln(C[d] / C[d-1]) for d >= 1
    per_day = np.full(ndays, np.nan)
    for k in range(days + 1, ndays):                      # returns for d = k-days ... k-1 -> r[k-days-1 : k-1]
        seg = r[k - days - 1:k - 1]
        if len(seg) == days and np.all(np.isfinite(seg)):
            per_day[k] = float(np.std(seg, ddof=1))
    return np.repeat(per_day, DAY_MIN)[:n]


# --- bars -------------------------------------------------------------------------------------------

@dataclass
class Bars:
    """A bar view of the minute grid. At a bar's last grid minute: its open, high, low, close; NaN elsewhere.
    `end` marks those minutes; `present` (per minute) says the bar containing the minute has a present minute;
    `start` (per minute) is the bar's first grid minute."""
    width: int
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    end: np.ndarray
    present: np.ndarray
    start: np.ndarray


def build_bars(ser: M.Series, width: int) -> Bars:
    n = len(ser.close)
    if width == 1:
        present = np.isfinite(ser.close)
        return Bars(1, ser.open.copy(), ser.high.copy(), ser.low.copy(), ser.close.copy(),
                    np.ones(n, dtype=bool), present, np.arange(n))
    nb = n // width
    m = nb * width
    r = {k: getattr(ser, k)[:m].reshape(nb, width) for k in ("open", "high", "low", "close")}
    present_m = np.isfinite(r["close"])
    has = present_m.any(axis=1)
    first = np.argmax(present_m, axis=1)
    last = width - 1 - np.argmax(present_m[:, ::-1], axis=1)
    rows = np.arange(nb)
    with np.errstate(invalid="ignore"), np.testing.suppress_warnings() as sup:
        sup.filter(RuntimeWarning)
        bo = np.where(has, r["open"][rows, first], np.nan)
        bc = np.where(has, r["close"][rows, last], np.nan)
        bh = np.where(has, np.nanmax(np.where(present_m, r["high"], np.nan), axis=1), np.nan)
        bl = np.where(has, np.nanmin(np.where(present_m, r["low"], np.nan), axis=1), np.nan)
    out = {k: np.full(n, np.nan) for k in ("open", "high", "low", "close")}
    ends = np.arange(nb) * width + width - 1
    for k, v in (("open", bo), ("high", bh), ("low", bl), ("close", bc)):
        out[k][ends] = v
    end = np.zeros(n, dtype=bool)
    end[ends] = True
    present = np.zeros(n, dtype=bool)
    present[:m] = np.repeat(has, width)
    start = (np.arange(n) // width) * width
    return Bars(width, out["open"], out["high"], out["low"], out["close"], end, present, start)


# --- the panel --------------------------------------------------------------------------------------

@dataclass
class Panel:
    asset: str
    t0_s: int
    n: int
    ser: M.Series
    imp: np.ndarray
    rv: np.ndarray
    day_open: np.ndarray
    vr: np.ndarray
    session: np.ndarray
    weekend: np.ndarray
    bars: dict


def build_panel(asset: str, raw_perp: dict, dvol: pd.DataFrame, n_minutes: int | None = None) -> Panel:
    """All per-minute inputs for one asset. `n_minutes` truncates every array for the causality check (P6)."""
    n = len(raw_perp["close"]) if n_minutes is None else int(n_minutes)
    ser = M.build_series(asset, raw_perp, n)
    t0_s = raw_perp["t0_s"]
    hour = ((t0_s // 3600) + np.arange(n) // 60) % 24
    weekday = ((t0_s // 86400 + 4) + np.arange(n) // DAY_MIN) % 7   # 1970-01-01 was a Thursday
    return Panel(asset=asset, t0_s=t0_s, n=n, ser=ser,
                 imp=implied_daily_move(dvol, asset, t0_s, n), rv=realised_daily_vol(ser.close, n),
                 day_open=day_opens(ser.open, ser.close, n), vr=A.volume_ratio(raw_perp["volume"][:n]),
                 session=SP.session_bucket(hour), weekend=weekday >= 5,
                 bars={"15": build_bars(ser, BAR_MIN), "1": build_bars(ser, 1)})


# --- levels and kinds (section 5) --------------------------------------------------------------------

def grid_step(entry: float) -> float:
    return 10.0 ** (math.floor(math.log10(entry)) - 1)


def candidate_levels(entry: float, s: int, target: float, level24: float, level_valid: bool,
                     lo: float, hi: float) -> np.ndarray:
    """The round grid over the trade's price range, the trade's target and the valid 24 h extreme, kept where at or
    beyond entry in the trade's direction within TOL."""
    step = grid_step(entry)
    grid = np.zeros(0)
    if np.isfinite(lo) and np.isfinite(hi) and hi >= lo:
        grid = np.arange(math.floor(lo / step) * step, hi + 2 * step, step)
    extra = [target] + ([level24] if level_valid else [])
    levels = np.concatenate([grid, np.asarray(extra, dtype=float)])
    levels = levels[np.isfinite(levels)]
    return np.unique(levels[s * (levels - entry) >= -TOL * entry])


def level_hits(ext: np.ndarray, close: np.ndarray, s: int, entry: float, levels: np.ndarray) -> np.ndarray:
    """Per bar: some candidate level is near the bar's extreme or was traded through and closed back on."""
    if len(levels) == 0 or len(ext) == 0:
        return np.zeros(len(ext), dtype=bool)
    with np.errstate(invalid="ignore"):
        diff = ext[:, None] - levels[None, :]
        near = np.abs(diff) <= TOL * entry
        through = (s * diff >= 0) & (s * (close[:, None] - levels[None, :]) < 0)
    return (near | through).any(axis=1)


def trade_masks(p: Panel, i0: int, x: int, s: int, entry: float, risk: float, target: float,
                level24: float, level_valid: bool) -> tuple[dict, dict, np.ndarray, np.ndarray]:
    """Every kind's mask over one trade's minutes [i0, x) before the open and gate masks, the per-family input
    masks, the trade's candidate levels, and the armed 15-minute bars (a base-rate denominator, not a kind)."""
    sl = slice(i0, x)
    L = max(0, x - i0)
    ser = p.ser
    close = ser.close[sl]
    m: dict[str, np.ndarray] = {}
    ok: dict[str, np.ndarray] = {}
    armed15 = np.zeros(L, dtype=bool)
    with np.errstate(invalid="ignore"):
        mv = s * (close - entry) / entry
        dv = s * (close - p.day_open[sl]) / p.day_open[sl]
        imp, rv = p.imp[sl], p.rv[sl]
        okI = np.isfinite(imp) & np.isfinite(rv) & np.isfinite(close)
        m["I1_implied"] = okI & (mv >= IMP_MULT * imp - EPS_ABS)
        m["I1_realised"] = okI & (mv >= IMP_MULT * rv - EPS_ABS)
        m["I1_implied_only"] = m["I1_implied"] & ~m["I1_realised"]
        okD = okI & np.isfinite(dv)
        m["I2_day"] = okD & (dv >= IMP_MULT * imp - EPS_ABS)
        m["I2_day_realised"] = okD & (dv >= IMP_MULT * rv - EPS_ABS)
        ok["I"] = okI
        lo = float(np.nanmin(ser.low[sl])) if L and np.isfinite(ser.low[sl]).any() else float("nan")
        hi = float(np.nanmax(ser.high[sl])) if L and np.isfinite(ser.high[sl]).any() else float("nan")
        levels = candidate_levels(entry, s, target, level24, level_valid, lo, hi)
        for fam, key, sfx in (("J", "15", ""), ("J1", "1", "_1m")):
            B = p.bars[key]
            bo, bh, bl, bc = B.open[sl], B.high[sl], B.low[sl], B.close[sl]
            inside = B.end[sl] & (B.start[sl] >= i0)          # the bar ends here and started inside the trade
            rng = bh - bl
            okJ = inside & np.isfinite(bo) & np.isfinite(bc) & np.isfinite(rng) & (rng > 0)
            ext = bh if s > 0 else bl
            u = s * (ext - entry) / risk
            shadow = (bh - np.maximum(bo, bc)) if s > 0 else (np.minimum(bo, bc) - bl)
            armed = okJ & (u >= P_MIN_R - EPS_REL)
            wick = shadow >= WICK_FRAC * rng * (1 - EPS_REL)
            hit = level_hits(ext, bc, s, entry, levels)
            m["J_wick" + sfx] = armed & wick & hit
            m["J_nolevel" + sfx] = armed & wick & ~hit
            if fam == "J":
                m["J_accept"] = armed & hit & ~wick
                m["J_shape"] = armed & wick
                armed15 = armed
            # a J status at a minute rests on the bars that have ENDED at or before it, so every minute's inputs
            # count as present; a missing bar is caught at its end minute by the unknown-status rule (build_grids)
            ok[fam] = np.ones(L, dtype=bool)
    return m, ok, levels, armed15


# --- grids ------------------------------------------------------------------------------------------

@dataclass
class Grids:
    """Rows are trades, columns elapsed minutes from the entry minute."""
    open_: np.ndarray
    mark: np.ndarray
    bin_: np.ndarray
    cv: np.ndarray | None
    cv_notime: np.ndarray | None
    X: np.ndarray
    stall: np.ndarray
    ord_: np.ndarray
    vr: np.ndarray
    session: np.ndarray
    weekend: np.ndarray
    gate: np.ndarray
    armed: np.ndarray
    ok: dict
    known: dict
    kind: dict
    levels: list


def build_grids(trades: pd.DataFrame, panels: dict, with_cv: bool) -> Grids:
    lengths = (trades["x"] - trades["i0"]).to_numpy(np.int64)
    T, E = len(trades), int(lengths.max()) if len(trades) else 0
    z = lambda dtype=float, fill=np.nan: np.full((T, E), fill, dtype=dtype)  # noqa: E731
    g = Grids(open_=np.zeros((T, E), bool), mark=z(), bin_=np.full((T, E), -(10 ** 9), np.int64),
              cv=z() if with_cv else None, cv_notime=z() if with_cv else None,
              X=np.zeros((T, E), bool), stall=z(), ord_=np.zeros((T, E), np.int32), vr=z(),
              session=np.full((T, E), -1, np.int8), weekend=np.zeros((T, E), bool), gate=np.zeros((T, E), bool),
              armed=np.zeros((T, E), bool),
              ok={f: np.zeros((T, E), bool) for f in FAMILIES},
              known={f: np.zeros((T, E), bool) for f in FAMILIES},
              kind={k: np.zeros((T, E), bool) for k in KINDS}, levels=[])
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
        X = SP.running_extreme(up)
        g.X[r, :L] = X
        g.stall[r, :L] = SP.stall_from_extreme(X)
        g.ord_[r, :L] = np.concatenate([[0], np.cumsum(X)[:-1]]).astype(np.int32) if L else np.zeros(0, np.int32)
        g.vr[r, :L] = p.vr[sl]
        g.session[r, :L], g.weekend[r, :L] = p.session[sl], p.weekend[sl]
        g.gate[r, :L] = np.arange(L) >= GATE_MIN
        masks, ok, levels, armed = trade_masks(p, tr.i0, tr.x, tr.s, tr.entry, tr.risk, tr.target,
                                               float(tr.level), bool(tr.level_valid))
        g.levels.append(levels)
        keep = g.open_[r, :L] & g.gate[r, :L]
        g.armed[r, :L] = armed & keep
        ends = {"I": X, "J": p.bars["15"].end[sl], "J1": p.bars["1"].end[sl]}
        present = {"I": ok["I"], "J": p.bars["15"].present[sl], "J1": p.bars["1"].present[sl]}
        for f, v in ok.items():
            g.ok[f][r, :L] = v
            # status unknown from the first gated extreme (I) or gated bar end (J) whose inputs are missing
            g.known[f][r, :L] = (~np.maximum.accumulate(ends[f] & g.gate[r, :L] & ~present[f]) if L
                                 else np.zeros(0, bool))
        for k, v in masks.items():
            g.kind[k][r, :L] = v & keep
    with np.errstate(invalid="ignore"):
        g.bin_ = np.where(g.open_, np.floor(g.mark / M.BIN_R), -(10 ** 9)).astype(np.int64)
    return g


def eligible(trades: pd.DataFrame, kind: str) -> np.ndarray:
    if KIND_FAMILY[kind] == "I":
        return (trades["entry_ts"] >= DVOL_FROM_TS).to_numpy()
    return np.ones(len(trades), dtype=bool)


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


# --- placebos (section 6) ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Spec:
    name: str
    extreme: str = "fresh"      # "fresh", "X" or "none"
    era: bool = True
    bin: bool = True
    rvol: bool = False
    rsession: bool = False
    cv: str = "shipped"         # "shipped" or "notime"
    shared: bool = False


RUNGS = (Spec("rung1", extreme="fresh"), Spec("rung2", extreme="X"), Spec("rung3", extreme="fresh", era=False))
SECONDARY = (Spec("S1", extreme="none"), Spec("S2", extreme="none", era=False),
             Spec("S3", bin=False), Spec("S4", cv="notime"))


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
    first_e = np.where(first_abs >= 0, first_abs - i0, NO_EVENT)
    family = KIND_FAMILY[kind]
    cv_grid = g.cv if spec.cv == "shipped" else g.cv_notime
    E = g.open_.shape[1]
    rows, collected = [], []
    for i in np.flatnonzero(el & (first_abs >= 0)):
        e = int(first_e[i])
        k = int(g.bin_[i, e])
        cand = el & (np.arange(len(trades)) != i) & (s == s[i]) & ~((i0 <= x[i]) & (i0[i] <= x))
        if spec.era:
            cand &= np.abs(trades["entry_ts"].to_numpy() - trades["entry_ts"].iat[i]) <= ERA_DAYS * 86400
        cand = np.flatnonzero(cand)
        lo, hi = max(0, e - window), min(E, e + window + 1)
        span = np.arange(lo, hi)[None, :]
        at_risk = g.open_[cand, lo:hi] & g.gate[cand, lo:hi] & g.ok[family][cand, lo:hi]
        if not spec.shared:
            at_risk &= (span < first_e[cand][:, None]) & g.known[family][cand, lo:hi]
        if spec.bin:
            at_risk &= g.bin_[cand, lo:hi] == k
        if spec.extreme == "X":
            at_risk &= g.X[cand, lo:hi]
        elif spec.extreme == "fresh":
            with np.errstate(invalid="ignore"):
                at_risk &= g.stall[cand, lo:hi] < FRESH_MAX
        if spec.rvol:
            if not np.isfinite(g.vr[i, e]):
                continue                                          # the event minute has no VR: dropped, counted
            side = g.vr[i, e] >= 1.0
            with np.errstate(invalid="ignore"):
                at_risk &= np.isfinite(g.vr[cand, lo:hi]) & ((g.vr[cand, lo:hi] >= 1.0) == side)
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
        if collect and keep.any():
            rr, cc = np.nonzero(at_risk)
            collected.append({"vr": g.vr[cand[rr], lo + cc], "ord": g.ord_[cand[rr], lo + cc],
                              "mark": g.mark[cand[rr], lo + cc], "session": g.session[cand[rr], lo + cc],
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
                  for k in ("vr", "ord", "mark", "session", "weekend")}
        return out, pooled
    return out


def included(rows: pd.DataFrame) -> int:
    if not len(rows):
        return 0
    return int((rows["controls"] >= MIN_CONTROLS).sum())


def decision_rung(counts: dict) -> str | None:
    for spec in RUNGS:
        if counts.get(spec.name, 0) >= MIN_EVENT_TRADES:
            return spec.name
    return None


# --- the exit-arm overlay (section 8, reported) -----------------------------------------------------

def overlay_rows(trades: pd.DataFrame, g: Grids, kind: str) -> pd.DataFrame:
    """Per trade: the shipped R, the overlay's R (exit at the close of the first event minute, else the shipped
    exit) and their difference; a trade without an event contributes zero."""
    el = eligible(trades, kind)
    first_abs = trades[f"{kind}_first"].to_numpy(np.int64)
    i0 = trades["i0"].to_numpy(np.int64)
    s = trades["s"].to_numpy(float)
    shipped = s * (trades["exit_price"].to_numpy(float) - trades["entry"].to_numpy(float)) / trades["risk"].to_numpy(float)
    has = el & (first_abs >= 0)
    arm = shipped.copy()
    e = np.where(has, first_abs - i0, 0)
    arm[has] = g.mark[np.flatnonzero(has), e[has]]
    return pd.DataFrame({"tid": trades["tid"], "entry_ts": trades["entry_ts"], "entry_day": trades["entry_day"],
                         "event": has, "exit_kind": trades["kind"], "shipped_R": shipped, "arm_R": arm,
                         "diff": arm - shipped})


def summarize_overlay(rows: pd.DataFrame, axis: list, idx: np.ndarray) -> dict:
    if not len(rows):
        return {"n": 0}
    v = rows["diff"].to_numpy(float)
    boot = M.boot_means(rows["entry_day"], v, axis, idx)
    ev = rows[rows["event"]]
    return {"n": int(len(rows)), "n_event": int(len(ev)), "mean_diff_R": float(v.mean()),
            "ci95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))],
            "mean_shipped_R": float(rows["shipped_R"].mean()), "mean_arm_R": float(rows["arm_R"].mean()),
            "event_trades_shipped_stop": int((ev["exit_kind"] == "stop").sum()),
            "event_trades_shipped_target": int((ev["exit_kind"] == "target").sum()),
            "event_trades_shipped_time": int((ev["exit_kind"] == "time").sum()),
            "mean_arm_R_on_event_trades": float(ev["arm_R"].mean()) if len(ev) else float("nan"),
            "mean_shipped_R_on_event_trades": float(ev["shipped_R"].mean()) if len(ev) else float("nan")}


# --- statistics and labels: the spot-vs-perp stage's, reused ----------------------------------------

boot_difference = SP.boot_difference
classify = SP.classify

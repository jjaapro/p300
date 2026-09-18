"""Exit-policy study, chento arm: inputs, the multi-arm walker and the statistics.

Everything the pre-registration (PREREGISTRATION_CHENTO.md) freezes lives here as a named constant.
The walker generalises the OKX re-validation's frozen 72 h walker (`okxlib.walk_trade`, which reproduced
the live ledger's exits); precondition P2 requires this walker's A0 to reproduce that study's trades exactly.

Process hygiene is the OKX study's: diagnostics off before the sleeve import, every sqlite connect refused
except the read-only snapshot, and no decide / execute / sweep call.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OKX_DIR = ROOT / "studies" / "notebooks" / "okx_gate_revalidation"
ORB_CACHE = ROOT / "studies" / "notebooks" / "orb_study" / "cache"
RESULTS = HERE / "results" / "chento"
sys.path.insert(0, str(OKX_DIR))
sys.path.insert(0, str(ROOT))

import okxlib as L  # noqa: E402

# --- frozen inputs (section 2) ------------------------------------------------------------------
SNAPSHOT = L.DEFAULT_SNAPSHOT
SNAPSHOT_SHA256 = "f3decffe3e5d740bf2678a9f6db29997ace4731ea0655a25274e71aa56b75c81"
INPUT_SHA256 = {
    "features_BTC.csv": "c1762d577495b9b75a9fce3c97c227e1524e33b5cfdd5f1a43f55e946ba7f81c",
    "features_ETH.csv": "83663d81fca7c309b4eb8656c315fb1e5812f704d658d402a7c35f2e66dcc646",
    "pool_BTC.csv": "1063f2f501199d6c508d018b1045276ac4fb51b5cbb9052350e4e0db1d9587d2",
    "pool_ETH.csv": "15f41e5af9e3616743446929be6db177edc279b83bb787a604a0fdd0fe8d44e9",
    "trades_BTC.csv": "c8741298816498413e1111f951d7bdeb45802d961a7f02f3419517ac5aa9f1bf",
    "trades_ETH.csv": "9d9c936604d35edbe360a6dccba3e6945fffba9e58dd4c48a421db885f527ef6",
}
FUNDING_SYMBOL = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
ASSETS = ("BTC", "ETH")
BAR_S = 900
COST_BP = 10.0
COST_BP_SECONDARY = 18.0
TARGET_R = 6.0
CENSOR_HOURS = 720
VEL_Z_MAX = 1.0                 # bots/chento_v3/strategy/config.py B1_VEL_Z_MAX
CVD_WINDOW_BARS = 2880          # B1_CVD_WINDOW_BARS
VEL_WINDOW_BARS = 4             # B1_VEL_WINDOW_BARS
K_CANDIDATES = (1.0, 2.0, 3.0)
STEP0_MIN_MEDIAN_HOURS = 72.0
HALF_SPLIT_DAY = "2023-12-20"
DAY_AXIS = [(date(2021, 4, 1) + timedelta(days=i)).isoformat() for i in range(1987)]
BLOCK_DAYS, N_BOOT, SEED = 30, 10_000, 42
EFFECT_R = 0.10
ALPHA = 0.05


@dataclass(frozen=True)
class Arm:
    id: str
    tif_hours: int | None           # None: no time exit (censored at CENSOR_HOURS)
    event: str | None = None        # abs_any | abs_losing | opposite


ARMS = (Arm("A0", 72), Arm("A2_6", 6), Arm("A2_12", 12), Arm("A2_24", 24), Arm("A2_48", 48),
        Arm("A2_168", 168), Arm("A1", None), Arm("X1", None, "abs_any"), Arm("X2", None, "abs_losing"),
        Arm("X3", None, "opposite"))
CANDIDATES = tuple(a.id for a in ARMS if a.id != "A0")
PREFERENCE = ("A1", "X3", "X1", "X2", "A2_168", "A2_48", "A2_24", "A2_12", "A2_6")   # section 7 tie order
BY_ID = {a.id: a for a in ARMS}


# --- small helpers ----------------------------------------------------------------------------------

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_day(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()


def iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=L._jsonable), encoding="utf-8")
    tmp.replace(path)


# --- process setup -----------------------------------------------------------------------------------

def open_process(asset: str = "BTC", scratch: Path | None = None):
    """Diag off, connect guard on the snapshot only, sleeve imported for `asset`. Returns (signal, math, clock)."""
    import tempfile
    scratch = scratch or Path(tempfile.mkdtemp(prefix=f"exitpol_{asset}_"))
    L.set_process_env(asset, scratch)
    L.install_connect_guard(allowed=[SNAPSHOT])
    return L.import_sleeve(SNAPSHOT, asset)


def verify_inputs() -> dict:
    """P1: the snapshot and every OKX-study input file match their frozen hashes."""
    out = {"snapshot": sha256(SNAPSHOT) == SNAPSHOT_SHA256}
    for name, want in INPUT_SHA256.items():
        out[name] = sha256(OKX_DIR / "results" / name) == want
    out["pass"] = all(out.values())
    return out


# --- inputs ------------------------------------------------------------------------------------------

def load_trades() -> pd.DataFrame:
    """The OKX study's gate-off arm (requires the sleeve imported: membership uses the bot's math)."""
    frames = []
    for asset in ASSETS:
        f = pd.read_csv(OKX_DIR / "results" / f"features_{asset}.csv")
        f["t"] = [L.parse_ts(x) for x in f["t"]]
        f = L.membership(f)
        off = f[f["in_off"]].copy()
        off.insert(0, "asset", asset)
        frames.append(off[["asset", "t", "direction", "entry", "atr", "risk"]])
    tr = pd.concat(frames, ignore_index=True)
    tr["entry_ts"] = tr["t"].astype(np.int64) + BAR_S
    tr["entry_day"] = [utc_day(x) for x in tr["entry_ts"]]
    tr["asset_rank"] = tr["asset"].map({"BTC": 0, "ETH": 1})
    tr = tr.sort_values(["entry_ts", "asset_rank", "direction"], kind="mergesort").reset_index(drop=True)
    return tr.drop(columns="asset_rank")


@dataclass
class Market:
    asset: str
    bars: "L.Bars"
    cvd_z: np.ndarray
    vel_z: np.ndarray
    opposite: dict          # direction of the POSITION -> sorted int64 array of opposite trigger bar opens
    funding_s: np.ndarray   # settlement times, UTC seconds (sorted)
    funding_rate: np.ndarray

    def against_mask(self, direction: str, k: float) -> np.ndarray:
        """ABS_AGAINST(k) for every bar: the bot's B1 absorption rule pointed against the position."""
        key = (direction, float(k))
        cache = self.__dict__.setdefault("_masks", {})
        if key not in cache:
            with np.errstate(invalid="ignore"):
                flat = np.abs(self.vel_z) < VEL_Z_MAX
                cache[key] = flat & ((self.cvd_z > k) if direction == "long" else (self.cvd_z < -k))
        return cache[key]


def load_market(con, asset: str, ctm) -> Market:
    df = pd.read_sql(f"SELECT timestamp, open, high, low, close, volume, quote_volume, volume_buy, "
                     f"quote_volume_buy, volume_sell, quote_volume_sell FROM {L.PERP_15M[asset]} ORDER BY timestamp", con)
    bars = L.Bars.from_frame(df[["timestamp", "high", "low", "close"]])
    frame = df.set_index(pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.as_unit("ns")).drop(columns="timestamp")
    feats = ctm.compute_moneyflow_signal(frame, cvd_window_bars=CVD_WINDOW_BARS, velocity_window_bars=VEL_WINDOW_BARS)
    pool = pd.read_csv(OKX_DIR / "results" / f"pool_{asset}.csv")
    pool_t = np.array([L.parse_ts(x) for x in pool["t"]], dtype=np.int64)
    opposite = {"long": np.sort(pool_t[(pool["direction"] == "short").to_numpy()]),
                "short": np.sort(pool_t[(pool["direction"] == "long").to_numpy()])}
    with np.load(ORB_CACHE / f"{FUNDING_SYMBOL[asset]}_funding.npz") as z:
        fs = (z["time_ms"] // 1000).astype(np.int64)
        fr = z["rate"].astype(float)
    return Market(asset, bars, feats["cvd_z"].to_numpy(float), feats["vel_z"].to_numpy(float), opposite, fs, fr)


def abs_against(mkt: Market, p: int, direction: str, k: float) -> bool:
    return bool(mkt.against_mask(direction, k)[p])


# --- the walker (section 3) ----------------------------------------------------------------------

@dataclass
class Exit:
    kind: str                # stop | target | time | event | censored_horizon | censored_data_end
    exit_bar_ts: int
    exit_price: float
    exit_ts: int             # settlement cutoff time (see funding)
    R_price: float           # at cost_bp, no funding
    cost_R: float
    funding_R: float
    missing: int
    max_consecutive_missing: int

    @property
    def net_R(self) -> float:
        return self.R_price + self.funding_R


def funding_R(mkt: Market, direction: str, risk: float, entry_ts: int, cutoff: int, inclusive: bool) -> float:
    """Sum of -side x rate x mark / risk over settlements in (entry_ts, cutoff] or (entry_ts, cutoff)."""
    lo = int(np.searchsorted(mkt.funding_s, entry_ts, side="right"))
    hi = int(np.searchsorted(mkt.funding_s, cutoff, side="right" if inclusive else "left"))
    if hi <= lo:
        return 0.0
    sign = 1.0 if direction == "long" else -1.0
    total = 0.0
    for s, rate in zip(mkt.funding_s[lo:hi], mkt.funding_rate[lo:hi]):
        p = mkt.bars.last_le(int(s) - BAR_S)            # the bar ending at s
        mark = float(mkt.bars.close[p])
        total += -sign * rate * mark / risk
    return total


def walk(ctm, mkt: Market, t: int, direction: str, entry: float, risk: float, arm: Arm, k: float,
         cost_bp: float = COST_BP, with_funding: bool = True) -> Exit:
    bars = mkt.bars
    sign = 1 if direction == "long" else -1
    stop = entry - sign * risk
    target = entry + sign * TARGET_R * risk
    state = {"entry_price": entry, "risk": risk, "stop_price": stop, "target_price": target,
             "ladder_added": False, "ladder_entry": None,
             "ladder_size_frac": L.LADDER_KW["ladder_size_frac"], "atr_at_entry": risk / L.ATR_STOP_MULT}
    cost_R = (cost_bp / 10000.0) * (entry / risk)
    entry_ts = t + BAR_S
    tif_ts = None if arm.tif_hours is None else t + arm.tif_hours * 3600
    censor_ts = t + CENSOR_HOURS * 3600
    opp = mkt.opposite[direction]
    last_ts = int(bars.ts[-1])
    missing = consec = max_consec = 0

    def close_exit(kind: str, p: int) -> Exit:
        px = float(bars.close[p])
        close_ts = int(bars.ts[p]) + BAR_S
        f = funding_R(mkt, direction, risk, entry_ts, close_ts, inclusive=False) if with_funding else 0.0
        return Exit(kind, int(bars.ts[p]), px, close_ts, sign * (px - entry) / risk - cost_R, cost_R, f,
                    missing, max_consec)

    b = t + BAR_S
    while True:
        if tif_ts is not None and b >= tif_ts:
            p = bars.first_ge(tif_ts)
            if p >= 0:
                return close_exit("time", p)
            return close_exit("censored_data_end", len(bars.ts) - 1)
        if tif_ts is None and b >= censor_ts:
            p = bars.first_ge(censor_ts)
            if p >= 0:
                return close_exit("censored_horizon", p)
            return close_exit("censored_data_end", len(bars.ts) - 1)
        if b > last_ts:
            return close_exit("censored_data_end", len(bars.ts) - 1)
        p = bars.pos(b)
        if p < 0:
            missing += 1
            consec += 1
            max_consec = max(max_consec, consec)
            b += BAR_S
            continue
        consec = 0
        res = ctm.evaluate_position_step(
            state, bar_high=float(bars.high[p]), bar_low=float(bars.low[p]), bar_close=float(bars.close[p]),
            atr_now=state["atr_at_entry"], direction=direction, cost_R=cost_R, **L.LADDER_KW)
        if res["action"] in ("stop_hit", "target_hit"):
            f = funding_R(mkt, direction, risk, entry_ts, b, inclusive=True) if with_funding else 0.0
            return Exit("stop" if res["action"] == "stop_hit" else "target", b, float(res["exit_price"]), b,
                        float(res["r_outcome"]), cost_R, f, missing, max_consec)
        if arm.event is not None:
            if arm.event == "opposite":
                j = int(np.searchsorted(opp, b))
                fire = j < len(opp) and int(opp[j]) == b
            else:
                fire = abs_against(mkt, p, direction, k)
                if fire and arm.event == "abs_losing":
                    fire = sign * (float(bars.close[p]) - entry) < 0
            if fire:
                return close_exit("event", p)
        b += BAR_S


def walk_all(ctm, markets: dict, trades: pd.DataFrame, k: float, cost_bp: float = COST_BP,
             with_funding: bool = True, arms=ARMS) -> pd.DataFrame:
    rows = []
    for tr in trades.itertuples(index=False):
        mkt = markets[tr.asset]
        for arm in arms:
            e = walk(ctm, mkt, int(tr.t), tr.direction, float(tr.entry), float(tr.risk), arm, k, cost_bp, with_funding)
            rows.append({"asset": tr.asset, "t": int(tr.t), "direction": tr.direction, "entry_day": tr.entry_day,
                         "arm": arm.id, "kind": e.kind, "exit_bar_ts": e.exit_bar_ts, "exit_price": e.exit_price,
                         "exit_ts": e.exit_ts, "hours_held": (e.exit_ts - (int(tr.t) + BAR_S)) / 3600,
                         "R_price": e.R_price, "funding_R": e.funding_R, "net_R": e.net_R, "cost_R": e.cost_R,
                         "missing": e.missing, "max_consecutive_missing": e.max_consecutive_missing})
    return pd.DataFrame(rows)


# --- step 0 (section 5) ----------------------------------------------------------------------------

def _path(mkt: Market, t: int) -> tuple[int, int]:
    """Bar positions of a trade's walk path before censoring: bar opens in [t + 15m, t + 720 h)."""
    lo = int(np.searchsorted(mkt.bars.ts, t + BAR_S))
    hi = int(np.searchsorted(mkt.bars.ts, t + CENSOR_HOURS * 3600, side="left"))
    return lo, hi


def first_fire_hours(mkt: Market, t: int, direction: str, k: float) -> tuple[float, bool]:
    """Hours from entry close to the close of the first ABS_AGAINST(k) bar; (720, True) if censored."""
    lo, hi = _path(mkt, t)
    hit = np.flatnonzero(mkt.against_mask(direction, k)[lo:hi])
    if len(hit):
        return (int(mkt.bars.ts[lo + hit[0]]) - t) / 3600.0, False
    return float(CENSOR_HOURS), True


def step0(markets: dict, trades: pd.DataFrame) -> dict:
    out = {"k_values": list(K_CANDIDATES), "by_k": {}}
    for k in K_CANDIDATES:
        hours, cens, fires, n_bars = [], [], 0, 0
        for tr in trades.itertuples(index=False):
            mkt = markets[tr.asset]
            h, c = first_fire_hours(mkt, int(tr.t), tr.direction, k)
            hours.append(h)
            cens.append(c)
            lo, hi = _path(mkt, int(tr.t))
            fires += int(mkt.against_mask(tr.direction, k)[lo:hi].sum())
            n_bars += hi - lo
        h = np.asarray(hours)
        out["by_k"][str(k)] = {"median_hours": float(np.median(h)), "p25_hours": float(np.quantile(h, 0.25)),
                               "p75_hours": float(np.quantile(h, 0.75)), "share_within_24h": float((h <= 24).mean()),
                               "share_within_72h": float((h <= 72).mean()), "censored": int(sum(cens)),
                               "per_bar_fire_rate": fires / n_bars if n_bars else float("nan"), "trades": len(h)}
    chosen = next((k for k in K_CANDIDATES if out["by_k"][str(k)]["median_hours"] >= STEP0_MIN_MEDIAN_HOURS), None)
    out["rule"] = f"smallest k with median time to first fire >= {STEP0_MIN_MEDIAN_HOURS:.0f} h, else 3 (flagged)"
    out["k"] = float(chosen if chosen is not None else K_CANDIDATES[-1])
    out["flag_faster_than_time_stop"] = chosen is None
    return out


# --- statistics (sections 6-7) ---------------------------------------------------------------------

def block_indices(T: int = len(DAY_AXIS), block: int = BLOCK_DAYS, n_boot: int = N_BOOT, seed: int = SEED) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n_blocks = -(-T // block)
    starts = rng.integers(0, T, size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % T
    return idx.reshape(n_boot, -1)[:, :T].astype(np.int32)


def day_sums(days: pd.Series, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    pos = {d: i for i, d in enumerate(DAY_AXIS)}
    s = np.zeros(len(DAY_AXIS))
    c = np.zeros(len(DAY_AXIS))
    for d, v in zip(days, values):
        i = pos[d]
        s[i] += v
        c[i] += 1
    return s, c


def boot_mean(sums: np.ndarray, counts: np.ndarray, idx: np.ndarray) -> np.ndarray:
    return sums[idx].sum(axis=1) / counts[idx].sum(axis=1)


def p_one_sided(point: float, boot: np.ndarray) -> float:
    return float((1 + np.sum(boot - point >= point)) / (len(boot) + 1))


def holm_adjust(p: dict[str, float]) -> dict[str, float]:
    order = sorted(p, key=p.get)
    m = len(order)
    adj, running = {}, 0.0
    for i, key in enumerate(order):
        running = max(running, min(1.0, (m - i) * p[key]))
        adj[key] = running
    return adj

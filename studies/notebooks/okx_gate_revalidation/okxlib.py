"""Shared code for the OKX gate causal re-validation (README.md, frozen at 19cd10a, plus
Addendum 1 at d1d9808).

Two kinds of thing live here:

* process guards — the P0d hygiene every §9 step asserts, the sqlite connect guard and the
  snapshot hash check; and
* pure functions — membership, the §2.4 walker, P3, the clause arithmetic, the verdict
  combiner and the report formulas — so the tests can drive every branch on synthetic data.

Nothing here opens a database, writes a file or imports the sleeve at import time. Every
numeric constant below is a frozen value of the README; the section is named beside it.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import sqlite3
import statistics
import sys
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RESULTS = HERE / "results"
PROD_DB = ROOT / "data" / "databases" / "prod.db"
# Durable and outside the repository (Addendum A6): the rerun and the §6 re-cut need it.
DEFAULT_SNAPSHOT = ROOT.parent / "p300-study-snapshots" / "okx_gate_revalidation" / "snapshot.db"


def _utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


def epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


def ts_float(s) -> float:
    """ISO string / Timestamp / epoch -> epoch seconds (UTC), sub-second kept."""
    if isinstance(s, (int, float, np.integer, np.floating)):
        return float(s)
    ts = pd.Timestamp(s)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return float(ts.timestamp())


def parse_ts(s) -> int:
    """Bar-aligned timestamps -> integer epoch seconds; refuses a fractional value."""
    v = ts_float(s)
    if v != int(v):
        raise ValueError("not a whole-second timestamp")
    return int(v)


# ─── Frozen values ─────────────────────────────────────────────────────────────
SNAPSHOT_CUTOFF = epoch(_utc(2026, 9, 12))            # §0: rows with timestamp < this
WINDOW_START = epoch(_utc(2021, 4, 1))                # §0: trigger bar open t in [start, end)
WINDOW_END = epoch(_utc(2026, 9, 8))
P1_CUTOFF = epoch(_utc(2026, 7, 20, 23, 59, 59))      # §2.1: ts <= this
P3_OPENED_BEFORE = "2026-09-12"                       # §4.4 P3
C4_IS_END = epoch(_utc(2024, 12, 31, 23, 59, 59))     # §3
C4_OOS_END = epoch(_utc(2026, 5, 27))                 # §3: C4-OOS 2025-01-01 -> 2026-05-26
HOLDOUT_START = epoch(_utc(2026, 5, 27))              # §3
for _v, _want in ((SNAPSHOT_CUTOFF, 1789171200), (WINDOW_START, 1617235200),
                  (WINDOW_END, 1788825600), (P1_CUTOFF, 1784591999)):
    if _v != _want:                                   # two derivations must agree
        raise RuntimeError("frozen epoch constant mismatch")

DAY_AXIS = [(date(2021, 4, 1) + timedelta(days=i)).isoformat() for i in range(1987)]
if DAY_AXIS[-1] != "2026-09-08":                      # §4.3: T = 1987, inclusive
    raise RuntimeError("day axis mismatch")
FULL_SPAN_DAYS = 1987                                 # §4.1

ASSETS = ("BTC", "ETH")
ASSET_RANK = {"BTC": 0, "ETH": 1}                     # §4.1: BTC before ETH on ties
PERP_15M = {"BTC": "cd_futures_15m", "ETH": "cd_futures_eth_15m"}
OKX_1H = {"BTC": "okx_perp_1h", "ETH": "okx_perp_eth_1h"}
INPUT_TABLES = ("cd_futures_15m", "cd_futures_eth_15m", "okx_perp_1h", "okx_perp_eth_1h",
                "ca_long_short_ratio")

BAR_S = 900
COST_BP = 10.0                  # §0 (bots/chento_v3/strategy/config.py:40)
COST_BP_SECONDARY = 18.0        # §0, report-only
ATR_STOP_MULT = 5.0             # §0 / §2.2
TARGET_R = 6.0                  # §2.4
TIF_HOURS = 72                  # §2.4
RISK_PCT = 2.0                  # §0 / §4.1: per_fire = 2.0 x R
NOTIONAL_MAX_X = 3.0            # §0, cap-binding count only
SMC_OB_WITHIN_R = 2.0           # §2.3 filter 2
UP_30D_THRESHOLD = 0.10         # §2.3 filter 4
OKX_ALIGN_Z_MIN = 0.0           # §2.3
OKX_WINDOW_HOURS = 168          # §0.3.2
COOLDOWN_HOURS = 6              # §2.2 precision
LADDER_KW = dict(ladder_enabled=False, ladder_adv_trigger_R=0.3, ladder_size_frac=0.5,
                 ladder_post_stop_R=1.5)                              # §0 unit: ladder off
N_TRIALS = 53                   # §5
BLOCK, N_ITER, SEED, QS = 30, 10000, 42, (0.05, 0.5, 0.95)            # §0 / §4.3
P2_MIN = 0.80                   # §2.2
POWER_POOLED_MIN, POWER_ASSET_MIN = 30, 10                            # §4.4
K3_B_MAX = -0.025               # §4.2 K3
P0_TOL = 1e-9                   # §0.4
P3_TOL_S = 180.0                # Addendum A1
P3_PRICE_REL = 1e-9             # §4.4 / A1
TILT_POLICY = {"BTC": "skip_after_loss", "ETH": "half_after_loss"}  # §4.5
P3_VARIANTS = ("bot_chento_v3_v1", "bot_chento_v3_eth")
P3_IDS = ("SJ-4243", "SJ-4244", "SJ-4245", "SJ-4246", "SJ-4248", "SJ-4249")   # named in §4.4 P3
EXIT_KIND = {"stop_hit": "stop", "target_hit": "target", "tif_expiry": "tif",
             "scheduled_exit": "tif"}                                  # A1

# §0.4 P0a / P0b. Directions of the near-misses come from bots/chento_v3*/logs/diag.jsonl.
P0_BARS = (
    dict(asset="BTC", t="2026-08-21T06:00:00+00:00", z=1.4609786480063527, expect=True, ledger="SJ-4243"),
    dict(asset="BTC", t="2026-08-21T19:30:00+00:00", z=0.14373400566189817, expect=True, ledger="SJ-4245"),
    dict(asset="BTC", t="2026-08-22T03:45:00+00:00", z=1.675149227184317, expect=True, ledger="SJ-4248"),
    dict(asset="BTC", t="2026-08-22T09:45:00+00:00", z=-0.5391446553064457, expect=False, ledger=None),
    dict(asset="ETH", t="2026-09-02T02:15:00+00:00", z=1.9303023139624131, expect=False, ledger=None),
    dict(asset="ETH", t="2026-09-02T23:45:00+00:00", z=0.45577527404023227, expect=False, ledger=None),
)

FORBIDDEN_MODULES = ("strategies.support.trade_db", "strategies.support.variant_registry")
RESEARCH_DB_MODULES = (
    "studies.notebooks.chento_journal.validation_multi_asset",
    "studies.notebooks.chento_journal.validation_B1_moneyflow_divergence",
    "studies.notebooks.chento_journal.validation_B5_lsr_extremes",
    "studies.notebooks.chento_journal.validation_group_A_tuning",
    "studies.notebooks.chento_journal.validation_B4_squeeze_direction",
)


class Refusal(RuntimeError):
    """An operator slip or failed guard: writes nothing and is NOT INVALID (Addendum A3)."""


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Refusal(msg)


# ─── Files ─────────────────────────────────────────────────────────────────────

def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _jsonable(o):
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, (np.ndarray,)):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def write_json_atomic(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True, allow_nan=True, default=_jsonable)
        fh.write("\n")
    os.replace(tmp, path)


def read_json(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def git(*args: str) -> str:
    """git output; a failing git is a refusal, never an empty 'clean' answer."""
    import subprocess
    proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    require(proc.returncode == 0, "git command failed")
    return proc.stdout.strip()


def code_hashes(*paths: Path) -> dict:
    return {Path(p).name: file_sha256(Path(p)) for p in (HERE / "okxlib.py", *paths)}


def provenance(snapshot_sha: str, script: Path, inputs: dict[str, Path] | None = None) -> dict:
    """Recorded in every step 2-4 output so outcomes.py can refuse stale artefacts (A3)."""
    return {"snapshot_sha256": snapshot_sha, "head": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain")), "code_sha256": code_hashes(script),
            "inputs_sha256": {k: file_sha256(Path(v)) for k, v in (inputs or {}).items()}}


def check_provenance(prov: dict | None, snapshot_sha: str, script: Path, what: str,
                     inputs: dict[str, Path] | None = None) -> None:
    require(prov is not None, f"{what} carries no provenance — rerun its step")
    require(prov["snapshot_sha256"] == snapshot_sha, f"{what} was made from a different snapshot")
    require(prov["code_sha256"] == code_hashes(script), f"{what} was made by different code — rerun its step")
    for k, v in (inputs or {}).items():
        require(prov["inputs_sha256"].get(k) == file_sha256(Path(v)), f"{what} was made from a different {k}")


def exception_record(exc: BaseException) -> dict:
    """Type and code locations only — never str(exc), which can carry values (§4.4)."""
    frames = traceback.extract_tb(exc.__traceback__)
    return {"exception_type": type(exc).__name__,
            "locations": [[Path(f.filename).name, f.lineno, f.name] for f in frames]}


# ─── Guards (P0d) ──────────────────────────────────────────────────────────────

def _db_target(database, uri: bool) -> tuple[str, bool]:
    s = str(database)
    if s.startswith("file:"):
        body, _, query = s[5:].partition("?")
        ro = bool(uri) and "mode=ro" in query.split("&")
        return body, ro
    return s, False


def install_connect_guard(allowed: list[Path], allowed_ro: list[Path] = ()) -> None:
    """Refuse every sqlite connect except to `allowed` paths (any mode) and to `allowed_ro`
    paths as `file:...?mode=ro` URIs. Steps 2-6 allow only the snapshot; only snapshot.py
    may read prod.db, and only read-only."""
    real = getattr(sqlite3, "_okx_real_connect", sqlite3.connect)
    ok = {str(Path(p).resolve()).lower() for p in allowed}
    ok_ro = {str(Path(p).resolve()).lower() for p in allowed_ro}

    def guarded(database, *args, **kwargs):
        target, ro = _db_target(database, kwargs.get("uri", False))
        if target != ":memory:":
            key = str(Path(target).resolve()).lower()
            if not (key in ok or (ro and key in ok_ro)):
                raise PermissionError("sqlite connect refused by the study guard")
        return real(database, *args, **kwargs)

    sqlite3._okx_real_connect = real
    sqlite3.connect = guarded


def set_process_env(asset: str | None, scratch: Path) -> None:
    """P0d(i): diag off BEFORE any sleeve import, pointed at a file that must never appear."""
    sys.dont_write_bytecode = True
    os.environ["CHENTO_V3_DIAG"] = "0"
    os.environ["CHENTO_V3_DIAG_PATH"] = str(scratch / "nodiag.jsonl")
    if asset is not None:
        os.environ["CHENTO_V3_ASSET"] = asset


def assert_no_sleeve_yet() -> None:
    require(os.environ.get("CHENTO_V3_DIAG") == "0", "P0d(i): CHENTO_V3_DIAG must be '0'")
    for m in FORBIDDEN_MODULES:
        require(m not in sys.modules, "P0d(iii): ledger module imported")
    require("bots.chento_v3.strategy" not in sys.modules, "sleeve imported before repoint")


def repoint_support_db(snapshot: Path) -> None:
    """P0d(ii): strategies.support.db at the snapshot, in every process (sleeve or not)."""
    from strategies.support import db
    db.PROD_DB = db.DASH_DB = db.TRADER_DB = Path(snapshot).resolve()


def import_sleeve(snapshot: Path, asset: str):
    """P0d(i)-(iv) around the one import of the sleeve. Returns (signal, math, clock)."""
    assert_no_sleeve_yet()
    snap = Path(snapshot).resolve()
    repoint_support_db(snap)
    from strategies.support import clock, db
    import bots.chento_v3.strategy as pkg
    from bots.chento_v3.strategy import math as ctm
    from bots.chento_v3.strategy import signal
    require(signal._DIAG_ENABLED is False, "P0d(i): sleeve diagnostics enabled")
    require(signal.ASSET == asset, "sleeve asset differs from the process asset")
    require(signal.PERP_15M_TABLE == PERP_15M[asset] and signal.OKX_1H_TABLE == OKX_1H[asset],
            "sleeve tables differ from the asset")
    for p in (db.PROD_DB, db.DASH_DB, db.TRADER_DB):
        require(Path(p).resolve() == snap, "P0d(ii): db constant not on the snapshot")
    for m in FORBIDDEN_MODULES:
        require(m not in sys.modules, "P0d(iii): ledger module imported")

    def _forbidden(*_a, **_k):
        raise RuntimeError("P0d(iv): decide/execute/_sweep_open_positions are forbidden")

    signal.decide = signal.execute = signal._sweep_open_positions = _forbidden
    pkg.decide = pkg.execute = _forbidden
    return signal, ctm, clock


def assert_p0d_after(snapshot: Path, scratch: Path) -> None:
    from strategies.support import db
    snap = Path(snapshot).resolve()
    for p in (db.PROD_DB, db.DASH_DB, db.TRADER_DB):
        require(Path(p).resolve() == snap, "P0d(ii): db constant moved")
    for m in FORBIDDEN_MODULES:
        require(m not in sys.modules, "P0d(iii): ledger module imported")
    require(not (scratch / "nodiag.jsonl").exists(), "P0d(i): diag file appeared")
    sig = sys.modules.get("bots.chento_v3.strategy.signal")
    if sig is not None:
        require(sig._DIAG_ENABLED is False, "P0d(i): sleeve diagnostics enabled")
    assert_research_repointed(snapshot)


def repoint_research(snapshot: Path, extra: tuple[str, ...] = ()) -> None:
    """P0d(ii): the five README modules, the sixth (B4) and any extra, then assert all."""
    for name in RESEARCH_DB_MODULES + tuple(extra):
        mod = importlib.import_module(name)
        mod.DB = Path(snapshot).resolve()
    assert_research_repointed(snapshot)


def assert_research_repointed(snapshot: Path) -> None:
    snap = Path(snapshot).resolve()
    for name, mod in list(sys.modules.items()):
        if name.startswith("studies.notebooks.") and hasattr(mod, "DB"):
            require(Path(mod.DB).resolve() == snap, "P0d(ii): research DB constant not repointed")


def verify_snapshot(snapshot: Path, results_dir: Path) -> dict:
    meta_path = Path(results_dir) / "snapshot.json"
    require(meta_path.exists(), "results/snapshot.json missing (run snapshot.py once)")
    require(Path(snapshot).exists(), "snapshot file missing")
    meta = read_json(meta_path)
    require(file_sha256(Path(snapshot)) == meta["file_sha256"],
            "snapshot sha256 differs from results/snapshot.json")
    return meta


def ro_connect(snapshot: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(snapshot).resolve().as_posix()}?mode=ro", uri=True)


# ─── Membership (§2.3) and preconditions from features ─────────────────────────

def filter2_passes(dist_R: float) -> bool:
    """The bot blocks iff dist_R <= 2.0 (signal.py:414-416); +inf (no OB) passes. Coded as
    `not (dist_R <= 2.0)` so a NaN can never silently drop a trade."""
    return not (dist_R <= SMC_OB_WITHIN_R)


def filter4_skips(direction: str, ret_30d: float) -> bool:
    """Shorts skipped iff math.is_up_30d(ret_30d, 0.10): strictly > 0.10, NaN does not skip."""
    return direction == "short" and bool(_math().is_up_30d(ret_30d, UP_30D_THRESHOLD))


def okx_pass(z: float, direction: str) -> bool:
    """The bot's own math.okx_aligned(z, dir, 0.0): long iff z >= 0, short iff z <= 0, NaN blocks."""
    return bool(_math().okx_aligned(z, direction, OKX_ALIGN_Z_MIN))


def membership(f: pd.DataFrame) -> pd.DataFrame:
    """Adds atr_drop, in_off, in_on_R1, in_on_R2 to a features frame."""
    out = f.copy()
    risk_ok = np.isfinite(out["risk"].astype(float)) & (out["risk"].astype(float) > 0)
    out["atr_drop"] = ~risk_ok
    off = []
    for ok, d, dist, r30 in zip(risk_ok, out["direction"], out["dist_R"], out["ret_30d"]):
        off.append(bool(ok) and filter2_passes(float(dist)) and not filter4_skips(d, float(r30)))
    out["in_off"] = off
    out["in_on_R1"] = [o and okx_pass(float(z), d) for o, z, d in
                       zip(out["in_off"], out["z_R1"], out["direction"])]
    out["in_on_R2"] = [o and okx_pass(float(z), d) for o, z, d in
                       zip(out["in_off"], out["z_R2"], out["direction"])]
    return out


def power_counts(feats: dict[str, pd.DataFrame], on_col: str = "in_on_R1") -> dict:
    out = {}
    for a, f in feats.items():
        off = f[f["in_off"]]
        out[a] = {"n_off": int(len(off)), "n_K": int(off[on_col].sum()),
                  "n_B": int((~off[on_col].astype(bool)).sum())}
    out["pooled"] = {k: sum(out[a][k] for a in feats) for k in ("n_off", "n_K", "n_B")}
    return out


def power_passes(counts: dict, pooled_min: int = POWER_POOLED_MIN,
                 asset_min: int = POWER_ASSET_MIN) -> bool:
    p = counts["pooled"]
    if p["n_K"] < pooled_min or p["n_B"] < pooled_min:
        return False
    return all(counts[a]["n_K"] >= asset_min and counts[a]["n_B"] >= asset_min
               for a in counts if a != "pooled")


def pooled_fidelity(fid: dict[str, dict]) -> float:
    num = sum(fid[a]["p2_numerator"] for a in fid)
    den = sum(fid[a]["p2_denominator"] for a in fid)
    return num / den if den else float("nan")


# ─── P1 comparator (§2.1) ──────────────────────────────────────────────────────

def p1_compare(research: pd.DataFrame, csv: pd.DataFrame) -> dict:
    """Both frames: ts (epoch), direction, entry, stop, target, already cut at ts <= P1_CUTOFF."""
    r = research.reset_index(drop=True)
    c = csv.reset_index(drop=True)
    set_ok = set(zip(r["ts"].astype(int), r["direction"])) == set(zip(c["ts"].astype(int), c["direction"]))
    order_ok = (len(r) == len(c)
                and bool((r["ts"].astype(int).to_numpy() == c["ts"].astype(int).to_numpy()).all())
                and bool((r["direction"].to_numpy() == c["direction"].to_numpy()).all()))
    rel = {}
    price_ok = order_ok
    first_bad = None
    if order_ok:
        for col in ("entry", "stop", "target"):
            a = r[col].astype(float).to_numpy()
            b = c[col].astype(float).to_numpy()
            d = np.abs(a - b)
            okc = d <= 1e-9 * np.abs(b)
            rel[col] = float(np.max(d / np.abs(b))) if len(b) else 0.0
            if not okc.all():
                price_ok = False
                i = int(np.argmin(okc))
                first_bad = i if first_bad is None else min(first_bad, i)
    else:
        n = min(len(r), len(c))
        for i in range(n):
            if int(r["ts"].iloc[i]) != int(c["ts"].iloc[i]) or r["direction"].iloc[i] != c["direction"].iloc[i]:
                first_bad = i
                break
        if first_bad is None:
            first_bad = n
    nonempty = len(r) > 0 and len(c) > 0
    return {"pass": bool(nonempty and set_ok and order_ok and price_ok), "set_ok": bool(set_ok),
            "order_ok": bool(order_ok), "price_ok": bool(price_ok), "n_research": int(len(r)),
            "n_csv": int(len(c)), "first_mismatch_index": first_bad, "max_rel_diff": rel}


# ─── Bars and the §2.4 walker ──────────────────────────────────────────────────

@dataclass
class Bars:
    ts: np.ndarray      # int64 bar opens, strictly ascending
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray

    @classmethod
    def from_frame(cls, df: pd.DataFrame) -> "Bars":
        df = df.sort_values("timestamp")
        ts = df["timestamp"].astype(np.int64).to_numpy()
        if len(ts) > 1 and not (np.diff(ts) > 0).all():
            raise ValueError("bar timestamps must be strictly ascending")
        return cls(ts, df["high"].astype(float).to_numpy(), df["low"].astype(float).to_numpy(),
                   df["close"].astype(float).to_numpy())

    def pos(self, t: int) -> int:
        i = int(np.searchsorted(self.ts, t))
        return i if i < len(self.ts) and self.ts[i] == t else -1

    def first_ge(self, t: int) -> int:
        i = int(np.searchsorted(self.ts, t))
        return i if i < len(self.ts) else -1

    def last_le(self, t: int) -> int:
        i = int(np.searchsorted(self.ts, t, side="right")) - 1
        return i


def path_nonfinite_prices(bars: Bars, t: int) -> int:
    """Rows on a trade's walk path (t+15m through the first bar at or after t+72h) whose high,
    low or close is not finite. A NaN high or low never triggers a stop or target, so R would
    come out finite and wrong; outcomes.py refuses phase B if any exist."""
    lo = int(np.searchsorted(bars.ts, t + BAR_S))
    end = bars.first_ge(t + TIF_HOURS * 3600)
    hi = len(bars.ts) if end < 0 else end + 1
    sl = slice(lo, hi)
    ok = np.isfinite(bars.high[sl]) & np.isfinite(bars.low[sl]) & np.isfinite(bars.close[sl])
    return int((~ok).sum())


def load_bars(con: sqlite3.Connection, asset: str) -> Bars:
    df = pd.read_sql(f"SELECT timestamp, high, low, close FROM {PERP_15M[asset]} ORDER BY timestamp", con)
    return Bars.from_frame(df)


@dataclass
class Walk:
    kind: str               # 'stop' | 'target' | 'tif' | 'no_exit_bar'
    exit_bar_ts: int | None
    exit_price: float
    R: float
    cost_R: float
    missing: int
    max_consecutive_missing: int

    @property
    def flagged(self) -> bool:
        return self.max_consecutive_missing > 4

    @property
    def exit_close_ts(self) -> int | None:
        return None if self.exit_bar_ts is None else self.exit_bar_ts + BAR_S


def _math():
    mod = sys.modules.get("bots.chento_v3.strategy.math")
    require(mod is not None, "import the sleeve through import_sleeve() first")
    return mod


def walk_trade(bars: Bars, t: int, direction: str, entry: float, risk: float,
               cost_bp: float, *, stop_price: float | None = None,
               target_price: float | None = None) -> Walk:
    """§2.4: step bars t+15m .. t+71h45m through math.evaluate_position_step (stop before
    target); exit the rest at the close of the bar opening t+72h, or of the first existing
    bar at or after it (A5). Missing bars are skipped and counted."""
    ctm = _math()
    sign = 1 if direction == "long" else -1
    stop = entry - sign * risk if stop_price is None else stop_price
    target = entry + sign * TARGET_R * risk if target_price is None else target_price
    state = {"entry_price": entry, "risk": risk, "stop_price": stop, "target_price": target,
             "ladder_added": False, "ladder_entry": None,
             "ladder_size_frac": LADDER_KW["ladder_size_frac"], "atr_at_entry": risk / ATR_STOP_MULT}
    cost_R = (cost_bp / 10000.0) * (entry / risk)
    tif_ts = t + TIF_HOURS * 3600
    missing = consec = max_consec = 0
    b = t + BAR_S
    while b < tif_ts:
        p = bars.pos(b)
        if p < 0:
            missing += 1
            consec += 1
            max_consec = max(max_consec, consec)
            b += BAR_S
            continue
        consec = 0
        res = ctm.evaluate_position_step(
            state, bar_high=float(bars.high[p]), bar_low=float(bars.low[p]),
            bar_close=float(bars.close[p]), atr_now=state["atr_at_entry"], direction=direction,
            cost_R=cost_R, **LADDER_KW)
        if res["action"] in ("stop_hit", "target_hit"):
            kind = "stop" if res["action"] == "stop_hit" else "target"
            return Walk(kind, b, float(res["exit_price"]), float(res["r_outcome"]), cost_R,
                        missing, max_consec)
        b += BAR_S
    p = bars.first_ge(tif_ts)
    if p < 0:
        return Walk("no_exit_bar", None, float("nan"), float("nan"), cost_R, missing, max_consec)
    close = float(bars.close[p])
    R = sign * (close - entry) / risk - cost_R
    return Walk("tif", int(bars.ts[p]), close, R, cost_R, missing, max_consec)


# ─── P3 (§4.4 as changed by Addendum A1) ───────────────────────────────────────

def parse_ledger_notes(notes: str) -> tuple[dict, str | None]:
    """The ledger stores JSON followed by a plain-text exit suffix (strategies/trades.py)."""
    obj, end = json.JSONDecoder().raw_decode(notes)
    tail = notes[end:]
    reason = None
    if "_EXIT: " in tail:
        reason = tail.split("_EXIT: ", 1)[1].split(";", 1)[0].strip()
    return obj, reason


def p3_check_trade(bars: Bars, row: dict) -> dict:
    """One closed ledger trade. Returns booleans and a time delta only — no R, and no TIF
    exit price (report-only after verdict.json, A1)."""
    notes, reason = parse_ledger_notes(row["notes"])
    live_kind = EXIT_KIND.get(reason or "")
    t = parse_ts(notes["bar_ts"])
    direction = str(row["direction"]).lower()
    w = walk_trade(bars, t, direction, float(notes["_entry_price"]), float(notes["_risk"]),
                   COST_BP, stop_price=float(notes["_stop_price"]),
                   target_price=float(notes["_target_price"]))
    out = {"id": row["id"], "live_kind": live_kind, "walker_kind": w.kind,
           "kind_match": live_kind is not None and live_kind == w.kind}
    if w.exit_close_ts is None:
        out.update(time_ok=False, dt_s=None, price_ok=None, reference=None)
    elif w.kind == "tif":
        dt = w.exit_close_ts - ts_float(notes["_time_stop_iso"])
        out.update(time_ok=abs(dt) <= P3_TOL_S, dt_s=round(float(dt), 3), price_ok=None,
                   reference="_time_stop_iso")
    else:
        dt = w.exit_close_ts - ts_float(row["actual_exit_time"])
        px = row.get("exit_price")
        out.update(time_ok=abs(dt) <= P3_TOL_S, dt_s=round(float(dt), 3),
                   price_ok=px is not None and abs(w.exit_price - float(px)) <= P3_PRICE_REL * abs(float(px)),
                   reference="actual_exit_time")
    out["pass"] = bool(out["kind_match"] and out["time_ok"]
                       and (out["price_ok"] is None or out["price_ok"]))
    return out


# ─── Series, clauses, verdict (§4.1-§4.4) ──────────────────────────────────────

def order_trades(trades: pd.DataFrame) -> pd.DataFrame:
    """§4.1: sort by (entry_ts, asset) with BTC before ETH; direction breaks any remaining tie."""
    tr = trades.copy()
    tr["_rank"] = tr["asset"].map(ASSET_RANK)
    tr = tr.sort_values(["entry_ts", "_rank", "direction"], kind="mergesort")
    return tr.drop(columns="_rank").reset_index(drop=True)


def add_entry_fields(trades: pd.DataFrame) -> pd.DataFrame:
    tr = trades.copy()
    tr["entry_ts"] = tr["t"].astype(np.int64) + BAR_S
    tr["entry_day"] = [datetime.fromtimestamp(int(x), tz=timezone.utc).date().isoformat()
                       for x in tr["entry_ts"]]
    return tr


def mean_or_nan(x) -> float:
    x = np.asarray(x, dtype=float)
    return float(x.mean()) if x.size else float("nan")


def delta_KB(R, kept) -> float:
    R = np.asarray(R, dtype=float)
    kept = np.asarray(kept, dtype=bool)
    return mean_or_nan(R[kept]) - mean_or_nan(R[~kept])


def _agree(x: float, y: float) -> bool:
    return x == 0 or y == 0 or ((x > 0) == (y > 0))


def direction_clause(d_pool: float, d_btc: float, d_eth: float) -> bool:
    """§4.1 D: each asset's sign matches the pooled sign (0.0 matches either sign), and the
    assets agree with each other. Non-finite -> False."""
    if not all(math.isfinite(v) for v in (d_pool, d_btc, d_eth)):
        return False
    return _agree(d_btc, d_pool) and _agree(d_eth, d_pool) and _agree(d_btc, d_eth)


def day_arrays(entry_days, R, kept, axis=DAY_AXIS):
    """§4.3: (T, 2) arrays [sum R, count] of kept and blocked trades per entry day."""
    pos = {d: i for i, d in enumerate(axis)}
    T = len(axis)
    K = np.zeros((T, 2))
    B = np.zeros((T, 2))
    for d, r, k in zip(entry_days, np.asarray(R, float), np.asarray(kept, bool)):
        i = pos[d]
        arr = K if k else B
        arr[i, 0] += r
        arr[i, 1] += 1.0
    Z = np.zeros((T, 2))
    Z[:, 1] = 1.0
    return K, B, Z


def ratio_stat(x: np.ndarray) -> float:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.float64(x[:, 0].sum()) / np.float64(x[:, 1].sum())


def bootstraps(entry_days, R, kept, axis=DAY_AXIS) -> tuple[dict, dict]:
    from studies.lib.validation import benchmark
    K, B, Z = day_arrays(entry_days, R, kept, axis)
    with np.errstate(divide="ignore", invalid="ignore"):
        boot_diff = benchmark.paired_block_boot_diff(K, B, ratio_stat, block=BLOCK, n_iter=N_ITER,
                                                     seed=SEED, qs=QS)
        boot_B = benchmark.paired_block_boot_diff(B, Z, ratio_stat, block=BLOCK, n_iter=N_ITER,
                                                  seed=SEED, qs=QS)
    return boot_diff, boot_B


def retire_clauses(boot_diff: dict, boot_B: dict) -> dict:
    ci = boot_diff["ci"]
    return {"R_a": bool(boot_B["ci"][0] > 0),
            "R_b": bool(ci[0] <= 0 <= ci[2]),
            "anti_discriminating": bool(ci[2] < 0)}


def ci_finite(*boots: dict) -> bool:
    return all(math.isfinite(v) for b in boots for v in b["ci"])


def gate_series(tr: pd.DataFrame, R_col: str = "R", kept_col: str = "in_on_R1"):
    """§4.1 on an ordered pooled frame: per_fire_off, per_fire_on, entry_days, ppy."""
    off = RISK_PCT * tr[R_col].astype(float).to_numpy()
    on = np.where(tr[kept_col].astype(bool).to_numpy(), off, np.nan)
    ppy = len(tr) / (FULL_SPAN_DAYS / 365.25)
    return on, off, list(tr["entry_day"]), ppy


def gate_metrics_for(tr: pd.DataFrame, R_col: str = "R", kept_col: str = "in_on_R1") -> dict:
    from studies.lib.validation import gates
    on, off, days, ppy = gate_series(tr, R_col, kept_col)
    folds = gates.walk_forward_folds(days, *gates.FOLD_PRESETS["event"])
    m = gates.gate_metrics(on, off, folds, ppy)
    pv = gates.promotion_verdict(m, grid_size=N_TRIALS, kind="binary", reality_check=False)
    return {"metrics": m, "promotion": pv, "folds": [{k: f[k] for k in
            ("fit_start", "fit_end", "oos_start", "oos_end")} for f in folds]}


def degenerate(m: dict) -> list[str]:
    bad = [k for k in ("oos_sharpe_uplift", "sign_stability", "blocked_expectancy_bp")
           if not math.isfinite(float(m[k]))]
    if m["n_folds"] < 3:
        bad.append("n_folds_lt_3")
    return bad


def k3_clause(mean_K_R2: float, mean_B_R2: float) -> bool:
    """§4.2 K3: mean_R(B_R2) <= -0.025 and mean_R(K_R2) > mean_R(B_R2)."""
    return bool(mean_B_R2 <= K3_B_MAX and mean_K_R2 > mean_B_R2)


def combine(invalid: list[str], K1: bool, K2: bool, K3: bool, K4: bool,
            R_a: bool, R_b: bool, D: bool) -> dict:
    """§4.4: INVALID -> KEEP -> RETIRE -> INCONCLUSIVE; names every failed K and R clause."""
    failed_keep = [n for n, v in (("K1", K1), ("K2", K2), ("K3", K3), ("K4", K4)) if not v]
    failed_retire = [n for n, v in (("R_a", R_a), ("R_b", R_b), ("D", D)) if not v]
    if invalid:
        outcome = "INVALID"
    elif not failed_keep:
        outcome = "KEEP"
    elif not failed_retire:
        outcome = "RETIRE"
    else:
        outcome = "INCONCLUSIVE"
    return {"outcome": outcome, "failed_keep_clauses": failed_keep,
            "failed_retire_clauses": failed_retire, "invalid_checks": list(invalid)}


def decide(tr: pd.DataFrame) -> dict:
    """Every decision-bearing number on the ordered pooled OFF-arm frame `tr` (columns asset,
    t, direction, entry_ts, entry_day, R, in_on_R1, in_on_R2, z_R2). Pure; no I/O."""
    invalid: list[str] = []
    R = tr["R"].astype(float).to_numpy()
    if not np.isfinite(R).all():                                     # A5
        invalid.append("non_finite_R")
    kept = tr["in_on_R1"].astype(bool).to_numpy()
    kept2 = tr["in_on_R2"].astype(bool).to_numpy()
    if kept2.sum() == 0 or (~kept2).sum() == 0:                      # A5
        invalid.append("degenerate_R2_arm")
    if not np.isfinite(tr["z_R2"].astype(float).to_numpy()).any():   # A5
        invalid.append("R2_z_all_non_finite")
    if invalid:
        return {"decision": combine(invalid, False, False, False, False, False, False, False)}

    d_pool = delta_KB(R, kept)
    d_asset = {}
    for a in ASSETS:
        m = (tr["asset"] == a).to_numpy()
        d_asset[a] = delta_KB(R[m], kept[m])
    D = direction_clause(d_pool, d_asset["BTC"], d_asset["ETH"])

    gm = gate_metrics_for(tr)
    deg = degenerate(gm["metrics"])
    if deg:
        invalid.extend("gate_metrics_" + d for d in deg)

    boot_diff, boot_B = bootstraps(list(tr["entry_day"]), R, kept)
    if not ci_finite(boot_diff, boot_B):
        invalid.append("non_finite_bootstrap_quantile")

    mean_K2, mean_B2 = mean_or_nan(R[kept2]), mean_or_nan(R[~kept2])
    K1 = bool(gm["promotion"]["pass"])
    K2 = bool(d_asset["BTC"] > 0 and d_asset["ETH"] > 0)
    K3 = k3_clause(mean_K2, mean_B2)
    K4 = bool(d_pool > 0 and D)
    rc = retire_clauses(boot_diff, boot_B) if ci_finite(boot_diff, boot_B) else \
        {"R_a": False, "R_b": False, "anti_discriminating": False}
    decision = combine(invalid, K1, K2, K3, K4, rc["R_a"], rc["R_b"], D)
    return {
        "decision": decision,
        "clauses": {"K1": K1, "K2": K2, "K3": K3, "K4": K4, "R_a": rc["R_a"], "R_b": rc["R_b"],
                    "D": D},
        "anti_discriminating": rc["anti_discriminating"],
        "delta": {"pooled": d_pool, **d_asset},
        "K3_values": {"mean_R_K_R2": mean_K2, "mean_R_B_R2": mean_B2,
                      "n_K_R2": int(kept2.sum()), "n_B_R2": int((~kept2).sum())},
        "gate_metrics": gm,
        "boot_diff": boot_diff,
        "boot_B": boot_B,
    }


# ─── Report-only formulas (Addendum 1, "Report-only formulas, fixed now") ──────

def exit_order(tr: pd.DataFrame) -> np.ndarray:
    key = tr["exit_bar_ts"].astype(float).to_numpy()
    return np.lexsort((np.arange(len(tr)), key))


def arm_stats(tr: pd.DataFrame, R_col: str = "R") -> dict:
    from studies.lib.validation import metrics
    R = tr[R_col].astype(float).to_numpy()
    n = len(R)
    if n == 0:
        return {"n": 0}
    R_exit = R[exit_order(tr)]
    dd = metrics.max_drawdown(R_exit)
    sd = float(np.std(R, ddof=1)) if n > 1 else float("nan")
    return {"n": n, "mean": float(R.mean()), "median": float(np.median(R)),
            "win_rate": float((R > 0).mean()), "total": float(R.sum()),
            "maxdd_R_trade_close": dd, "mar_like": float(R.sum() / dd) if dd > 0 else float("inf"),
            "per_trade_sharpe": float(R.mean() / sd) if sd and sd > 0 else float("nan")}


def daily_sharpe_by_entry_day(tr: pd.DataFrame, R_col: str = "R") -> float:
    from studies.lib.validation import metrics
    pos = {d: i for i, d in enumerate(DAY_AXIS)}
    s = np.zeros(len(DAY_AXIS))
    for d, r in zip(tr["entry_day"], tr[R_col].astype(float)):
        s[pos[d]] += r
    return metrics.daily_sharpe(s, 365.0)


def mde(R_K, R_B, n_trials: int = N_TRIALS, power: float = 0.80) -> float:
    nd = statistics.NormalDist()
    R_K, R_B = np.asarray(R_K, float), np.asarray(R_B, float)
    se = math.sqrt(np.var(R_K, ddof=1) / len(R_K) + np.var(R_B, ddof=1) / len(R_B))
    return (nd.inv_cdf(1 - 0.05 / n_trials) + nd.inv_cdf(power)) * se


def cap_binding(tr: pd.DataFrame) -> int:
    return int(((tr["risk"].astype(float) / tr["entry"].astype(float))
                < (RISK_PCT / 100.0) / NOTIONAL_MAX_X).sum())


def mtm_daily_returns(trades: list[dict], bars: dict[str, Bars], sizes=None) -> np.ndarray:
    """§4.5 mark-to-market: each open trade marked at the close of the last 15m bar of each
    UTC day it is open, and at its exit bar on its exit day; 0.02 x size x dR of initial
    capital, cost on the exit day. Axis: first entry day .. last exit day, zero-filled."""
    if not trades:
        return np.zeros(0)
    sizes = np.ones(len(trades)) if sizes is None else np.asarray(sizes, float)
    entry_days = [date.fromisoformat(tr["entry_day"]) for tr in trades]
    exit_days = [datetime.fromtimestamp(int(tr["exit_bar_ts"]), tz=timezone.utc).date()
                 for tr in trades]
    d0, d1 = min(entry_days), max(exit_days)
    n = (d1 - d0).days + 1
    out = np.zeros(n)
    for tr, size, ed, xd in zip(trades, sizes, entry_days, exit_days):
        if size == 0:
            continue
        b = bars[tr["asset"]]
        sign = 1 if tr["direction"] == "long" else -1
        prev = 0.0
        d = ed
        while d <= xd:
            if d < xd:
                day_last = epoch(datetime(d.year, d.month, d.day, 23, 45, tzinfo=timezone.utc))
                p = b.last_le(day_last)
                mark = prev if p < 0 or b.ts[p] < tr["t"] + BAR_S else \
                    sign * (float(b.close[p]) - tr["entry"]) / tr["risk"]
                out[(d - d0).days] += (RISK_PCT / 100.0) * size * (mark - prev)
            else:
                mark = float(tr["R"]) + float(tr["cost_R"])
                out[(d - d0).days] += (RISK_PCT / 100.0) * size * (mark - prev - float(tr["cost_R"]))
            prev = mark
            d += timedelta(days=1)
    return out


# ─── Same-hour control (§4.5) ──────────────────────────────────────────────────

def required_statement(verdict: str, sh_power_ok: bool, sh_meets_retire: bool) -> str | None:
    """§4.5 flags for findings.md."""
    if not sh_power_ok:
        return "control_not_evaluable"
    if verdict == "KEEP":
        return "pipeline_red_flag" if sh_meets_retire else None
    if verdict in ("RETIRE", "INCONCLUSIVE") and sh_meets_retire:
        return "does_not_bear_on_lookahead"
    return None

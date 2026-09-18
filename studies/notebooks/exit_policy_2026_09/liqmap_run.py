"""Liquidation-map study: preconditions, freeze, one BTC outcome run, one ETH replication (PREREGISTRATION_LIQMAP.md).

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\liqmap_run.py checks
    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\liqmap_run.py freeze0
    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\liqmap_run.py outcomes
    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\liqmap_run.py holdout

`checks` builds only the BTC maps -- section 2: before F0, ETH is touched by L1 (hashes), L2 (population identity) and
L7 (archive coverage from the sidecar) alone -- and computes no touch, no turn, no continuation value and no placebo.
`holdout` is the only entry point that constructs an ETH map, and it refuses to start before a BTC verdict exists.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import json  # noqa: E402
import sqlite3  # noqa: E402
import subprocess  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import liqmap_data as LD  # noqa: E402
import liqmap_lib as L  # noqa: E402
import micro_data as MD  # noqa: E402
import micro_lib as M  # noqa: E402
import micro_run as MR  # noqa: E402

RESULTS = HERE / "results" / "liqmap"
CACHE = HERE / "cache"
TRADER_DB = Path("C:/Source/Repos/trader/data/trader.db")
ASSETS = ("BTC", "ETH")
SYMBOL = M.SYMBOL

# --- section 2 inputs -------------------------------------------------------------------------------

INPUTS = {
    **{f"metrics_5m_{a}": CACHE / f"{SYMBOL[a]}_metrics_5m_full.npz" for a in ASSETS},
    "metrics_manifest": LD.RAW / "manifest.json",
    **{f"perp_1m_{a}": M.ORB_CACHE / f"{SYMBOL[a]}_perp_1m.npz" for a in ASSETS},
    **{f"chento_features_{a}": M.CHENTO_FEATURES[a] for a in ASSETS},
    "squeeze_bull_ledger": M.SQB_LEDGER,
    "micro_trades": M.RESULTS / "trades.csv.gz",
    "trader_db": TRADER_DB,
}

# Populations (section 2). The decision population is chento BTC; squeeze_bull and the flat/bear fires are report-only.
POPULATIONS = ("chento", "squeeze_bull", "squeeze_bull_secondary")
HORIZON_H = {"chento": 72, "squeeze_bull": 48, "squeeze_bull_secondary": 48}
KINDS = L.KINDS                                     # the library owns the frozen names
FAMILY_KINDS = ("EV1", "EV2")                       # only these enter the Holm family (section 5)
Q1_TESTS = L.Q1_TESTS
DECISION_POP, DECISION_ASSET = "chento", "BTC"

L5_PROBE_DAYS = (("2025-10-10", ("2025-10-09", "2025-10-10", "2025-10-11")),
                 ("2024-08-05", ("2024-08-04", "2024-08-05", "2024-08-06")))
L5_RHO_MIN = 0.2
CA_FROM, CA_TO = "2022-02-28", "2026-04-24"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def day_of(ts_s: int) -> str:
    return datetime.fromtimestamp(int(ts_s), tz=timezone.utc).date().isoformat()


def input_hashes() -> dict:
    return {k: M.sha256(p) for k, p in INPUTS.items()}


# --- loading ----------------------------------------------------------------------------------------

def load_panels() -> dict:
    """The 1-minute perpetual panels, raw (the map needs volume and quote volume, which Series does not carry)."""
    out = {}
    for a in ASSETS:
        with np.load(M.ORB_CACHE / f"{SYMBOL[a]}_perp_1m.npz") as z:
            out[a] = {k: z[k].astype(float) for k in ("open", "high", "low", "close", "volume", "quote_volume",
                                                      "taker_buy_volume")}
            out[a]["t0_s"] = int(z["t0_ms"][0]) // 1000
    assert out["BTC"]["t0_s"] == out["ETH"]["t0_s"], "both panels must share one minute grid"
    return out


def load_archive(asset: str) -> tuple[np.ndarray, int, dict]:
    with np.load(CACHE / f"{SYMBOL[asset]}_metrics_5m_full.npz") as z:
        oi = z["oi"].astype(float)
        t0 = int(z["t0_s"][0])
    meta = json.loads((CACHE / f"{SYMBOL[asset]}_metrics_5m_full.meta.json").read_text())
    return oi, t0, meta


def present_close(panel: dict) -> np.ndarray:
    """A minute is present iff it traded; the earlier stages' convention (micro_lib.build_series)."""
    return np.where(panel["volume"] > 0, panel["close"], np.nan)


def load_populations() -> dict[str, pd.DataFrame]:
    """The microstructure stage's walked trades, unchanged (section 2: no new walk), plus the secondary fires."""
    tr = pd.read_csv(INPUTS["micro_trades"])
    tr["s"] = np.where(tr["direction"].to_numpy() == "long", 1, -1).astype(np.int64)
    lag = {"chento": 900, "squeeze_bull": 3600}                 # the entry lag each loader applied (micro_lib)
    tr["signal_ts"] = tr["entry_ts"] - tr["pop"].map(lag).fillna(0).astype(np.int64)
    pops = {p: tr[tr["pop"] == p].reset_index(drop=True) for p in ("chento", "squeeze_bull")}
    pops["squeeze_bull_secondary"] = walk_secondary()
    return pops


def walk_secondary() -> pd.DataFrame:
    """squeeze_bull's flat/bear fires walked with the S0 exits (section 2, reported never decided)."""
    d = pd.read_csv(M.SQB_LEDGER)
    rows = []
    for r in d[d["regime_backonly"] != "bull_30d"].itertuples(index=False):
        bar, entry = int(pd.Timestamp(r.ts).timestamp()), float(r.entry)
        rows.append({"pop": "squeeze_bull", "tid": f"BTC:{bar}", "asset": "BTC", "direction": "long", "s": 1,
                     "signal_ts": bar, "entry_ts": bar + 3600, "entry": entry, "risk": M.SQB_STOP * entry,
                     "stop": entry * (1 - M.SQB_STOP), "target": entry * (1 + M.SQB_TARGET)})
    trades = M._finish(rows)                                    # walked as squeeze_bull, which is what S0 exits means
    walked = M.walk_population(trades, {"BTC": M.load_series("BTC")})      # the frozen walker, unchanged
    walked["pop"] = "squeeze_bull_secondary"
    walked["s"] = walked["s"].astype(np.int64)
    return walked


# --- the map pass (sections 3 and 4) ------------------------------------------------------------------

def bin_panel(asset: str, panels: dict, oi: np.ndarray, t0_arch: int, n_minutes: int | None = None) -> L.BinPanel:
    p = panels[asset]
    if n_minutes is not None:                            # the L4 cuts: nothing at or after the cut minute exists
        p = {k: (v[:n_minutes] if isinstance(v, np.ndarray) else v) for k, v in p.items()}
    return L.build_bin_panel(SYMBOL[asset], oi, t0_arch, p, p["t0_s"])


def hourly_state_minutes(panel: dict, t0_arch: int) -> np.ndarray:
    """Hourly :00 minutes that trade, from 60 days after the archive's first snapshot, with a complete window."""
    t0_min = panel["t0_s"]
    n = len(panel["close"])
    first_ts = t0_arch + (L.WARMUP_DAYS + L.TERCILE_DAYS) * 86400
    first = max(0, -(-(first_ts - t0_min) // M.MIN_S))
    first += (-first) % 60                                # round up to the next :00 minute
    last = n - 1 - L.STATE_WINDOW                          # section 5 Q1: m + 1,680 <= the panel's last minute
    return np.arange(first, last + 1, 60, dtype=np.int64)


def map_queries(minutes: np.ndarray, close: np.ndarray, t0_arch: int, t0_min: int) -> tuple[np.ndarray, np.ndarray]:
    """(bin, price) per query minute; the map as of m is the state after the last bin stamped <= m - 5 min."""
    ts = t0_min + minutes * M.MIN_S
    return np.array([L.map_state_bin(t0_arch, int(t)) for t in ts], dtype=np.int64), close[minutes]


def run_both_maps(panel: L.BinPanel, q_bin: np.ndarray, q_price: np.ndarray, n_bins: int | None = None) -> dict:
    """The actual map and the control map (section 3.7) over one query set, plus the burst series of each."""
    warm = int(np.ceil(L.WARMUP_DAYS * 86400 / L.BIN_S))
    order = np.argsort(q_bin, kind="mergesort")
    out = {}
    for name, control in (("actual", False), ("control", True)):
        run = L.run_map(panel, q_bin[order], q_price[order], control=control, warmup_bin=warm, n_bins=n_bins)
        unsorted = {}
        for f in ("d_up", "share_up", "mass_up", "d_dn", "share_dn", "mass_dn"):
            a = np.full(len(q_bin), np.nan)
            a[order] = getattr(run, f)
            unsorted[f] = a
        has_price = np.isfinite(panel.vwap)
        burst = {side: L.burst_series(getattr(run, f"e_{side}"), has_price, warm) for side in ("long", "short")}
        out[name] = {"run": run, "q": unsorted, "burst": burst}
    return out


def apply_density_cut(states_ts: np.ndarray, share: np.ndarray, at_ts: np.ndarray, at_share: np.ndarray) -> dict:
    """Section 4's top-tercile cut over the trailing 30 days of hourly states, and the definedness it implies."""
    cut = L.tercile_cut(states_ts, share, at_ts)
    return {"cut": cut, "defined": L.cluster_defined(at_share, cut)}


# --- preconditions ------------------------------------------------------------------------------------

def l1_inputs(hashes: dict) -> dict:
    out = {"inputs_sha256": hashes, "trader_db_sha256": hashes["trader_db"]}
    manifest = json.loads(INPUTS["metrics_manifest"].read_text())
    served = [e for e in manifest["files"] if e["status"] != "missing_in_archive"]
    bad = [e["file"] for e in served if M.sha256(HERE / e["file"]) != e["sha256"]]
    out["metrics_zips"] = {"verified": len(served) - len(bad), "failed": bad,
                           "missing_in_archive": [(e["symbol"], e["day"]) for e in manifest["files"]
                                                  if e["status"] == "missing_in_archive"]}
    panels = load_panels()
    for a in ASSETS:
        oi, t0_arch, meta = load_archive(a)
        rebuilt = MD._logical_hash({k: v for k, v in np.load(CACHE / f"{SYMBOL[a]}_metrics_5m_full.npz").items()
                                    if k != "t0_s"})
        out[f"metrics_5m_{a}_logical_matches_meta"] = rebuilt == meta["logical_sha256"]
        out[f"metrics_5m_{a}_file_matches_meta"] = hashes[f"metrics_5m_{a}"] == meta["file_sha256"]
        pmeta = json.loads((M.ORB_CACHE / f"{SYMBOL[a]}_perp_1m.meta.json").read_text())
        out[f"perp_1m_{a}_matches_meta"] = hashes[f"perp_1m_{a}"] == pmeta["file_sha256"]
        out[f"archive_t0_on_the_minute_grid_{a}"] = (t0_arch - panels[a]["t0_s"]) % L.BIN_S == 0
        out[f"chento_features_{a}_frozen"] = hashes[f"chento_features_{a}"] == M.CHENTO_FEATURES_SHA256[a]
    out["squeeze_bull_ledger_frozen"] = hashes["squeeze_bull_ledger"] == M.SQB_LEDGER_SHA256
    f0 = json.loads((M.RESULTS / "freeze_F0.json").read_text())
    rel = M.RESULTS.joinpath("trades.csv.gz").resolve().relative_to(M.ROOT).as_posix()
    report = json.loads((M.RESULTS / "report.json").read_text())
    out["micro_trades_matches_its_report"] = hashes["micro_trades"] == report["trades_sha256"]
    out["micro_lib_unchanged_since_its_F0"] = all(
        M.sha256(M.ROOT / f) == h for f, h in f0["files"].items() if f.endswith(("micro_lib.py", "micro_run.py")))
    out["micro_trades_rel"] = rel
    flags = [v for k, v in out.items() if isinstance(v, bool)]
    out["pass"] = all(flags) and not bad
    return out


def l2_populations(pops: dict) -> dict:
    a0 = pd.read_csv(M.CHENTO_A0_WALKS)
    a0 = a0[a0["arm"] == "A0"]
    ch = pops["chento"]
    sb = pd.read_csv(M.SQB_WALKS)
    s0 = set(sb.loc[sb["arm"] == "S0", "bar_ts"].astype(int))
    panels = load_panels()
    worst = {}
    for p, tr in pops.items():                          # L2 names chento and squeeze_bull; the secondary is reported
        d = [abs(present_close(panels[t.asset])[t.i0 - 1] - t.entry) / t.entry for t in tr.itertuples(index=False)]
        worst[p] = {"max_rel_diff": float(np.max(d)), "above_1e-9": int(np.sum(np.array(d) > 1e-9))}
    entry_ok = all(worst[p]["max_rel_diff"] <= 1e-9 for p in ("chento", "squeeze_bull"))
    eth = ch[(ch["asset"] == "ETH") & (ch["entry_ts"] >= int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp()))]
    out = {"chento": {"trades": len(ch), "equals_A0": set(zip(ch["asset"], ch["signal_ts"].astype(int),
                                                              ch["direction"])) == set(zip(a0["asset"],
                                                                                           a0["t"].astype(int),
                                                                                           a0["direction"])),
                      "BTC": int((ch["asset"] == "BTC").sum()), "ETH": int((ch["asset"] == "ETH").sum()),
                      "ETH_from_2022_01_01": len(eth)},
           "squeeze_bull": {"trades": len(pops["squeeze_bull"]),
                            "equals_S0": set(pops["squeeze_bull"]["signal_ts"].astype(int)) == s0},
           "squeeze_bull_secondary": {"trades": len(pops["squeeze_bull_secondary"])},
           "entry_equals_previous_close": {"by_population": worst, "gated_on": ["chento", "squeeze_bull"],
                                           "note": "the secondary flat/bear fires carry the squeeze_bull ledger's own "
                                                   "entry price, which differs from the perp close on a few fires; "
                                                   "that population is reported, never decided (section 2)",
                                           "pass": bool(entry_ok)}}
    out["pass"] = bool(out["chento"]["equals_A0"] and out["chento"]["trades"] == 392
                       and out["chento"]["BTC"] == 208 and out["chento"]["ETH"] == 184
                       and out["chento"]["ETH_from_2022_01_01"] == 176
                       and out["squeeze_bull"]["equals_S0"] and out["squeeze_bull"]["trades"] == 122
                       and out["squeeze_bull_secondary"]["trades"] == 301 and entry_ok)
    return out


def l3_fixtures() -> dict:
    r = subprocess.run([sys.executable, "-m", "pytest", str(HERE / "tests" / "test_liqmap.py"), "-q",
                        "-p", "no:cacheprovider"], capture_output=True, text=True)
    return {"summary": r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-400:],
            "pass": r.returncode == 0}


def l7_coverage(panel: L.BinPanel, run: "L.MapRun", oi: np.ndarray, t0_arch: int, meta: dict) -> dict:
    """Section 6 L7: what the map could not use, per year, and the ten largest single-bin open-interest jumps."""
    years = np.array([day_of(t0_arch + L.BIN_S * b)[:4] for b in range(panel.n)])
    usable = np.isfinite(panel.oi)
    d_defined = np.zeros(panel.n, dtype=bool)
    d_defined[1:] = usable[1:] & usable[:-1]
    dropped = np.zeros(panel.n)
    dropped[1:] = np.where(d_defined[1:], 0.0, np.abs(np.nan_to_num(panel.oi[1:] - panel.oi[:-1])))
    out = {"by_year": {}}
    for y in sorted(set(years)):
        m = years == y
        out["by_year"][y] = {"bins": int(m.sum()), "no_snapshot": int((~usable[m]).sum()),
                             "no_present_minute": int((~panel.present[m]).sum()),
                             "delta_oi_undefined": int((~d_defined[m]).sum()),
                             "base_units_dropped": float(dropped[m].sum()),
                             "clamp_bins": int((run.clamp_surplus[m] > 0).sum()),
                             "clamp_surplus": float(run.clamp_surplus[m].sum())}
    d = np.full(panel.n, np.nan)
    d[1:] = np.where(d_defined[1:], panel.oi[1:] - panel.oi[:-1], np.nan)
    rel = np.abs(d) / panel.oi
    top = np.argsort(np.where(np.isfinite(rel), rel, -1))[::-1][:10]
    out["largest_jumps"] = [{"utc": datetime.fromtimestamp(t0_arch + L.BIN_S * int(b), tz=timezone.utc).isoformat(),
                             "delta_oi": float(d[b]), "oi": float(panel.oi[b]), "relative": float(rel[b])}
                            for b in top]
    out["pass"] = True                                    # L7 reports coverage; it is not a gate
    return out


def l7_sidecar(asset: str) -> dict:
    _, _, meta = load_archive(asset)
    return {k: meta[k] for k in ("slots", "slots_present", "slots_present_by_year", "days_missing_in_archive",
                                 "identical_duplicates_dropped", "conflicting_duplicate_rows")}


def l8_alignment(asset: str, oi: np.ndarray, t0_arch: int) -> dict:
    """Section 6 L8 (reported, not a gate): the archive stamp against CoinDesk 1-minute open interest, by month."""
    if asset != "BTC":
        return {"note": "oi_minutes is BTC only", "pass": True}
    con = sqlite3.connect(f"file:{TRADER_DB.as_posix()}?mode=ro&immutable=1", uri=True)
    con.execute("PRAGMA query_only = 1")
    m = pd.read_sql("select timestamp, oi_close from oi_minutes order by timestamp", con)
    con.close()
    minute = {int(t): float(v) for t, v in zip(m["timestamp"], m["oi_close"])}
    lags = list(range(-600, 601, 60))
    rows = []
    for b in range(len(oi)):
        v = oi[b]
        if not np.isfinite(v) or v <= 0:
            continue
        T = t0_arch + L.BIN_S * b
        for lag in lags:
            w = minute.get(T - lag)
            if w:
                rows.append((day_of(T)[:7], lag, abs(v - w) / w))
    if not rows:
        return {"note": "no overlap", "pass": True}
    df = pd.DataFrame(rows, columns=["month", "lag", "rel"])
    med = df.groupby(["month", "lag"])["rel"].median().reset_index()
    best = med.loc[med.groupby("month")["rel"].idxmin()]
    return {"by_month": {r.month: {"best_lag_s": int(r.lag), "median_rel": float(r.rel)}
                         for r in best.itertuples(index=False)},
            "note": "the frozen convention of section 2 is kept for the whole span; this row reports, it does not gate",
            "pass": True}


# --- L5, map validity --------------------------------------------------------------------------------

def read_ca_liquidations(asset: str) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{TRADER_DB.as_posix()}?mode=ro&immutable=1", uri=True)
    con.execute("PRAGMA query_only = 1")
    d = pd.read_sql("select timestamp, long_usd, short_usd from ca_liquidations where asset = ? order by timestamp",
                    con, params=(asset,))
    con.close()
    return d


def l5_day_convention(ca: pd.DataFrame) -> dict:
    """Pinned before any correlation: the two market-wide long flushes must dominate their neighbours (L5)."""
    stamp = {day_of(t): float(v) for t, v in zip(ca["timestamp"], ca["long_usd"])}
    probes = []
    for event, days in L5_PROBE_DAYS:
        vals = {d: stamp.get(d) for d in days}
        if any(v is None for v in vals.values()):
            probes.append({"event": event, "values": vals, "convention": None})
            continue
        top = max(vals, key=vals.get)
        prev = (datetime.fromisoformat(event).date() - timedelta(days=1)).isoformat()
        nxt = (datetime.fromisoformat(event).date() + timedelta(days=1)).isoformat()
        convention = {event: "day_start", nxt: "day_end", prev: None}[top]
        probes.append({"event": event, "values": vals, "argmax": top, "convention": convention})
    conventions = {p["convention"] for p in probes}
    return {"probes": probes, "convention": probes[0]["convention"] if len(conventions) == 1 else None,
            "agree": len(conventions) == 1 and None not in conventions}


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 3:
        return float("nan")
    return float(pd.Series(a[ok]).rank().corr(pd.Series(b[ok]).rank()))


def _residual(x: np.ndarray, r: np.ndarray) -> np.ndarray:
    """Residual of log(x) on log(range); the range control of L5 (ii)."""
    ok = np.isfinite(x) & (x > 0) & np.isfinite(r) & (r > 0)
    out = np.full(len(x), np.nan)
    lx, lr = np.log(x[ok]), np.log(r[ok])
    A = np.vstack([lr, np.ones(len(lr))]).T
    coef, *_ = np.linalg.lstsq(A, lx, rcond=None)
    out[ok] = lx - A @ coef
    return out


def l5_validity(panel: L.BinPanel, run: "L.MapRun", t0_arch: int, panels: dict, warm_bin: int) -> dict:
    """Section 6 L5: the estimated daily liquidation series against Coinalyze's, and against a map-free naive series."""
    ca = read_ca_liquidations("BTC")
    pin = l5_day_convention(ca)
    if not pin["agree"]:
        return {"day_convention": pin, "pass": False,
                "note": "the two probes disagree on the day convention; the study stops before F0 (L5)"}
    shift = 0 if pin["convention"] == "day_start" else -86400
    actual = pd.DataFrame({"day": [day_of(int(t) + shift) for t in ca["timestamp"]],
                           "actual_long": ca["long_usd"].to_numpy(float),
                           "actual_short": ca["short_usd"].to_numpy(float)})

    bins = np.arange(panel.n)
    usd_long = run.e_long * panel.vwap
    usd_short = run.e_short * panel.vwap
    keep = (bins >= warm_bin) & np.isfinite(panel.vwap)
    days = np.array([day_of(t0_arch + L.BIN_S * int(b)) for b in bins])
    est = pd.DataFrame({"day": days[keep], "est_long": np.nan_to_num(usd_long[keep]),
                        "est_short": np.nan_to_num(usd_short[keep])}).groupby("day", as_index=False).sum()

    p = panels["BTC"]
    t0_min, close = p["t0_s"], present_close(p)
    dmin = np.array([day_of(t0_min + i * M.MIN_S) for i in range(len(close))])
    ph = pd.DataFrame({"day": dmin, "high": np.where(p["volume"] > 0, p["high"], np.nan),
                       "low": np.where(p["volume"] > 0, p["low"], np.nan),
                       "open": np.where(p["volume"] > 0, p["open"], np.nan)})
    agg = ph.groupby("day").agg(high=("high", "max"), low=("low", "min"), open=("open", "first")).reset_index()
    oi_first = pd.DataFrame({"day": days, "oi": panel.oi}).dropna().groupby("day", as_index=False).first()
    agg = agg.merge(oi_first, on="day", how="left")
    agg["naive_long"] = (agg["open"] - agg["low"]) / agg["open"] * agg["oi"]
    agg["naive_short"] = (agg["high"] - agg["open"]) / agg["open"] * agg["oi"]
    agg["range"] = np.log(agg["high"] / agg["low"])

    d = est.merge(actual, on="day", how="inner").merge(agg, on="day", how="left")
    d = d[(d["day"] >= CA_FROM) & (d["day"] <= CA_TO)].reset_index(drop=True)

    def side_stats(est_col: str, act_col: str, naive_col: str) -> dict:
        e, a, nv, r = (d[c].to_numpy(float) for c in (est_col, act_col, naive_col, "range"))
        lags = {}
        for lag in (-1, 0, 1):
            ea = np.roll(e, lag).astype(float)
            ea[:max(0, lag)] = np.nan
            if lag < 0:
                ea[lag:] = np.nan
            lags[str(lag)] = spearman(ea, a)
        drop = int((~((e > 0) & (a > 0))).sum())
        return {"rho_raw_by_lag": lags, "rho_raw": lags["0"],
                "rho_range_controlled": spearman(_residual(e, r), _residual(a, r)),
                "dropped_zero_days": drop, "naive_rho_raw": spearman(nv, a),
                "naive_rho_range_controlled": spearman(_residual(nv, r), _residual(a, r))}

    out = {"day_convention": pin, "days": len(d),
           "lag_note": "lag = the days the estimate is shifted forward before it meets the actual day",
           "long": side_stats("est_long", "actual_long", "naive_long"),
           "short": side_stats("est_short", "actual_short", "naive_short")}
    with np.errstate(invalid="ignore", divide="ignore"):
        est_share = d["est_long"] / (d["est_long"] + d["est_short"])
        act_share = d["actual_long"] / (d["actual_long"] + d["actual_short"])
        naive_share = d["naive_long"] / (d["naive_long"] + d["naive_short"])
    out["long_share_rho"] = spearman(est_share.to_numpy(), act_share.to_numpy())
    out["naive_long_share_rho"] = spearman(naive_share.to_numpy(), act_share.to_numpy())
    for side in ("long", "short"):                        # L5: a NEIGHBOUR ABOVE lag 0 would mean the day is misaligned
        near = [out[side]["rho_raw_by_lag"][k] - out[side]["rho_raw"] for k in ("-1", "1")]
        out[side]["neighbour_exceeds_lag0_by_0.20"] = bool(max(near) > 0.20)
    out["convention_anomaly"] = bool(out["long"]["neighbour_exceeds_lag0_by_0.20"]
                                     and out["short"]["neighbour_exceeds_lag0_by_0.20"])
    out["ev2_descriptive_sides"] = [s for s in ("long", "short") if not (out[s]["rho_raw"] >= L5_RHO_MIN)]
    out["caveat_volatility_proxy"] = bool(not (out["long_share_rho"] >= L5_RHO_MIN)
                                          or not (out["long_share_rho"] > out["naive_long_share_rho"]))
    out["pass"] = True                                    # L5 fixes the family and writes a caveat; it is not a gate
    return out



# --- L4, causality ------------------------------------------------------------------------------------

def l4_causality(asset: str, panels: dict, oi: np.ndarray, t0_arch: int, minutes: np.ndarray,
                 maps: dict, n_cuts: int = 5) -> dict:
    """Section 6 L4: truncating the panel at a cut minute and the archive at the snapshot it may read changes nothing."""
    rng = np.random.default_rng(M.SEED)
    t0_min = panels[asset]["t0_s"]
    inside = minutes[(minutes > int(minutes.min())) & (minutes < int(minutes.max()))]
    picks = np.sort(rng.choice(inside, size=n_cuts, replace=False))
    close = present_close(panels[asset])
    cuts = []
    for c in picks:
        c = int(c)
        n_bins = L.map_state_bin(t0_arch, t0_min + c * M.MIN_S) + 1
        oi_cut = oi.copy()
        oi_cut[n_bins:] = np.nan                                   # no snapshot the state at c may not read
        short_panel = bin_panel(asset, panels, oi_cut, t0_arch, n_minutes=c + 1)
        keep = minutes <= c
        q_bin, q_price = map_queries(minutes[keep], close, t0_arch, t0_min)
        short = run_both_maps(short_panel, q_bin, q_price, n_bins=n_bins)
        diffs = {}
        for name in ("actual", "control"):
            full_q, short_q = maps[name]["q"], short[name]["q"]
            diffs[f"{name}_queries"] = sum(int((~np.isclose(full_q[f][keep], short_q[f], rtol=0, atol=0,
                                                            equal_nan=True)).sum())
                                           for f in ("d_up", "share_up", "d_dn", "share_dn"))
            d = 0
            for side in ("long", "short"):
                for a_full, a_short in zip(maps[name]["burst"][side], short[name]["burst"][side]):
                    d += int((~np.isclose(a_full[:n_bins], a_short[:n_bins], rtol=0, atol=0, equal_nan=True)).sum())
            for f in ("e_long", "e_short"):
                d += int((~np.isclose(getattr(maps[name]["run"], f)[:n_bins], getattr(short[name]["run"], f)[:n_bins],
                                      rtol=0, atol=0, equal_nan=True)).sum())
            diffs[f"{name}_series"] = d
        cuts.append({"cut_minute": c, "cut_utc": datetime.fromtimestamp(t0_min + c * M.MIN_S, tz=timezone.utc)
                     .isoformat(), "bins_kept": n_bins, "queries_at_or_before_cut": int(keep.sum()),
                     "differences": diffs})
    return {"cuts": cuts, "pass": all(sum(c["differences"].values()) == 0 for c in cuts)}


# --- states, entries and events -------------------------------------------------------------------------

def split_queries(maps: dict, n_states: int) -> tuple[dict, dict]:
    """The one pass carried the hourly states first and the trade entry states after them."""
    states = {n: {f: v[:n_states] for f, v in maps[n]["q"].items()} for n in ("actual", "control")}
    entries = {n: {f: v[n_states:] for f, v in maps[n]["q"].items()} for n in ("actual", "control")}
    return states, entries


def state_table(asset: str, panels: dict, minutes: np.ndarray, states: dict) -> pd.DataFrame:
    """One row per hourly :00 state with each map's cluster, after the section 4 density cut."""
    t0_min = panels[asset]["t0_s"]
    close = present_close(panels[asset])
    ts = t0_min + minutes * M.MIN_S
    df = pd.DataFrame({"minute": minutes, "ts": ts, "day": [day_of(t) for t in ts], "price": close[minutes]})
    has_price = np.isfinite(df["price"].to_numpy())
    for name in ("actual", "control"):
        q = states[name]
        for side, share, d in (("up", "share_up", "d_up"), ("dn", "share_dn", "d_dn")):
            cut = L.tercile_cut(ts, q[share], ts)
            df[f"{name}_{side}_d"] = q[d]
            df[f"{name}_{side}_share"] = q[share]
            df[f"{name}_{side}_cut"] = cut
            df[f"{name}_{side}_defined"] = L.cluster_defined(q[share], cut) & has_price
            df[f"{name}_{side}_level"] = df["price"].to_numpy() * (1.0 + q[d])
    return df


def entry_features(asset: str, panels: dict, trades: pd.DataFrame, t0_arch: int, state_ts: np.ndarray,
                   states: dict, entries: dict, maps: dict) -> pd.DataFrame:
    """Section 5's entry state per trade: the two clusters, their levels, and each burst threshold and flag."""
    t0_min = panels[asset]["t0_s"]
    ts = (trades["i0"].to_numpy(np.int64) - 1) * M.MIN_S + t0_min
    out = trades.copy()
    for name in ("actual", "control"):
        q = entries[name]
        for side, share, d in (("up", "share_up", "d_up"), ("dn", "share_dn", "d_dn")):
            cut = L.tercile_cut(state_ts, states[name][share], ts)
            out[f"{name}_{side}_defined"] = L.cluster_defined(q[share], cut)
            out[f"{name}_{side}_level"] = out["entry"].to_numpy() * (1.0 + q[d])
            out[f"{name}_{side}_d"] = q[d]
    bins = np.array([L.map_state_bin(t0_arch, int(t)) for t in ts], dtype=np.int64)
    for name in ("actual", "control"):
        for side in ("long", "short"):
            _, theta, flag = maps[name]["burst"][side]
            ok = (bins >= 0) & (bins < len(theta))
            th = np.full(len(bins), np.nan)
            fl = np.zeros(len(bins), dtype=bool)
            th[ok] = theta[bins[ok]]
            fl[ok] = flag[bins[ok]]
            out[f"{name}_theta_{side}"] = th
            out[f"{name}_burst_{side}_at_entry"] = fl
    return out


def trade_events(asset: str, panels: dict, trades: pd.DataFrame, t0_arch: int, maps: dict) -> pd.DataFrame:
    """First event minute of every section 5 kind, and its eligibility, for one BTC population."""
    p = panels[asset]
    t0_min = p["t0_s"]
    high = np.where(p["volume"] > 0, p["high"], np.nan)
    low = np.where(p["volume"] > 0, p["low"], np.nan)
    present = np.isfinite(present_close(p))
    out = trades.copy()
    ev = {k: np.full(len(trades), -1, dtype=np.int64) for k in KINDS}
    el = {k: np.zeros(len(trades), dtype=bool) for k in KINDS}
    for r, t in enumerate(trades.itertuples(index=False)):
        up = t.s > 0                                            # a long takes profit above, a short below
        side = "up" if up else "dn"
        favour = "short" if up else "long"                      # the forced flow that helps the position
        against = "long" if up else "short"
        for kind, name in (("EV1", "actual"), ("EV1_ctrl", "control")):
            el[kind][r] = bool(getattr(t, f"{name}_{side}_defined"))
            if el[kind][r]:
                level = float(getattr(t, f"{name}_{side}_level"))
                ev[kind][r] = L.first_level_minute(high, low, present, t.i0, t.x, level, t.s)
        for kind, name, sd in (("EV2", "actual", favour), ("EV2_ctrl", "control", favour),
                               ("EV2_against", "actual", against)):
            el[kind][r] = bool(np.isfinite(getattr(t, f"{name}_theta_{sd}")))
            if el[kind][r]:
                ev[kind][r] = L.first_burst_minute(maps[name]["burst"][sd][2], present, t0_arch, t0_min, t.i0, t.x)
    for kind in KINDS:
        first = np.where((ev[kind] == L.NO_EVENT) | ~el[kind], -1, ev[kind])
        out[f"{kind}_first"] = first
        out[f"{kind}_minutes"] = (first >= 0).astype(np.int64)   # a first-event kind: one event minute at most
        out[f"{kind}_eligible"] = el[kind]
    return out


# --- L6, counts before any outcome ----------------------------------------------------------------------

def _deciles(s) -> list[float]:
    v = np.asarray(s, dtype=float)
    v = v[np.isfinite(v)]
    return [float(x) for x in np.quantile(v, np.arange(0, 11) / 10)] if len(v) else []


def l6_counts(states: pd.DataFrame, events: dict[str, pd.DataFrame], series: dict, l5: dict) -> dict:
    """Section 6 L6: everything the family is fixed on, computed before any touch, turn or continuation value."""
    out = {"states": {"total_hourly_states": int(len(states)),
                      "states_without_a_price": int((~np.isfinite(states["price"])).sum())}}
    for side, label in (("up", "up"), ("dn", "down")):
        both = states[f"actual_{side}_defined"] & states[f"control_{side}_defined"]
        out["states"][label] = {
            "eligible": int(both.sum()),
            "actual_only": int((states[f"actual_{side}_defined"] & ~states[f"control_{side}_defined"]).sum()),
            "control_only": int((~states[f"actual_{side}_defined"] & states[f"control_{side}_defined"]).sum()),
            "by_year": {y: int(g.sum()) for y, g in both.groupby(states["day"].str[:4])},
            "d_deciles_actual": _deciles(states.loc[both, f"actual_{side}_d"]),
            "d_deciles_control": _deciles(states.loc[both, f"control_{side}_d"]),
            "share_deciles_actual": _deciles(states.loc[both, f"actual_{side}_share"]),
            "share_deciles_control": _deciles(states.loc[both, f"control_{side}_share"])}

    demoted = set(l5.get("ev2_descriptive_sides", []))
    out["l5_ev2_sides_below_rho"] = sorted(demoted)
    out["populations"], family = {}, list(Q1_TESTS)
    for p, ev in events.items():
        grids = M.build_grids(ev, series, with_cv=False)          # no exit price: the counts cannot see an outcome
        sd = MR.random_minute_cv_sd(ev, series)
        per = {"trades": len(ev), "cv_sd_random_minute": sd, "tests": {}}
        for kind in KINDS:
            el = ev[f"{kind}_eligible"].to_numpy(bool)
            first = ev[f"{kind}_first"].to_numpy(np.int64)
            has = el & (first >= 0)
            elapsed_h = (first[has] - ev["i0"].to_numpy()[has]) / 60
            in_trade = int((ev["x"] - ev["i0"]).to_numpy()[el].sum())
            rows = L.match_liq(ev, grids, kind, window_minutes(p))
            all_years = L.match_liq(ev, grids, kind, window_minutes(p), era_days=10 ** 6)
            included = int((rows["controls"] >= M.MIN_CONTROLS).sum()) if len(rows) else 0
            per["tests"][kind] = {
                "eligible_trades": int(el.sum()), "trades_with_event": int(has.sum()),
                "share_with_event": float(has.sum() / el.sum()) if el.sum() else float("nan"),
                "median_elapsed_min": float(np.median(first[has] - ev["i0"].to_numpy()[has])) if has.any() else float("nan"),
                "median_elapsed_share_of_horizon": float(np.median(elapsed_h) / HORIZON_H[p]) if len(elapsed_h) else float("nan"),
                "events_per_24h_in_trade": float(ev[f"{kind}_minutes"].to_numpy()[el].sum() / in_trade * 1440) if in_trade else float("nan"),
                "included_with_min_controls": included,
                "included_all_years_pool": int((all_years["controls"] >= M.MIN_CONTROLS).sum()) if len(all_years) else 0,
                "mde80_R": float(M.POWER_Z * sd / np.sqrt(included)) if included else float("nan")}
        for name, side in (("actual", "short"), ("actual", "long"), ("control", "short"), ("control", "long")):
            col = f"{name}_burst_{side}_at_entry"
            if col in ev:
                per[f"share_{col}"] = float(ev[col].mean())
        for a, b in (("EV1", "EV1_ctrl"), ("EV2", "EV2_ctrl")):
            fa, fb = ev[f"{a}_first"].to_numpy(np.int64), ev[f"{b}_first"].to_numpy(np.int64)
            both = (fa >= 0) & (fb >= 0)
            per[f"overlap_{a}_{b}"] = {"both_with_event": int(both.sum()),
                                       "same_minute": int((both & (fa == fb)).sum())}
        out["populations"][p] = per
        if p == DECISION_POP:
            for kind in FAMILY_KINDS:
                n = per["tests"][kind]["included_with_min_controls"]
                sides = {"long", "short"} if p == "chento" else {"short"}
                blocked = bool(kind == "EV2" and (sides & demoted))
                per["tests"][kind]["in_family"] = bool(n >= M.MIN_EVENT_TRADES and not blocked)
                per["tests"][kind]["l5_demoted"] = blocked
                if per["tests"][kind]["in_family"]:
                    family.append(f"{p}:{kind}")
    out["family"] = family
    out["pass"] = True
    return out


def window_minutes(pop: str) -> int:
    """Section 5's matching window: 5 % of the population's horizon (216 minutes chento, 144 squeeze_bull)."""
    return int(round(HORIZON_H[pop] * 60 * L.WINDOW_SHARE))


# --- stages ----------------------------------------------------------------------------------------------

def btc_state() -> dict:
    """Everything `checks` and `outcomes` share: the BTC maps, the hourly states, the entry states and the events."""
    panels = load_panels()
    oi, t0_arch, meta = load_archive("BTC")
    panel = bin_panel("BTC", panels, oi, t0_arch)
    pops = load_populations()
    btc = {p: tr[tr["asset"] == "BTC"].reset_index(drop=True) for p, tr in pops.items()}
    state_minutes = hourly_state_minutes(panels["BTC"], t0_arch)
    entry_minutes = np.concatenate([tr["i0"].to_numpy(np.int64) - 1 for tr in btc.values()])
    minutes = np.concatenate([state_minutes, entry_minutes])
    close = present_close(panels["BTC"])
    q_bin, q_price = map_queries(minutes, close, t0_arch, panels["BTC"]["t0_s"])
    maps = run_both_maps(panel, q_bin, q_price)
    st, en = split_queries(maps, len(state_minutes))
    states = state_table("BTC", panels, state_minutes, st)
    ts_states = states["ts"].to_numpy(np.int64)
    events, at = {}, 0
    for p, tr in btc.items():
        sl = {n: {f: v[at:at + len(tr)] for f, v in en[n].items()} for n in ("actual", "control")}
        feats = entry_features("BTC", panels, tr, t0_arch, ts_states, st, sl, maps)
        events[p] = trade_events("BTC", panels, feats, t0_arch, maps)
        at += len(tr)
    return {"panels": panels, "oi": oi, "t0_arch": t0_arch, "meta": meta, "panel": panel, "pops": pops, "btc": btc,
            "maps": maps, "state_minutes": state_minutes, "minutes": minutes, "states": states, "events": events,
            "series": {"BTC": M.load_series("BTC")}}


def checks() -> dict:
    hashes = input_hashes()
    out = {"created_utc": now_utc(), "L1": l1_inputs(hashes)}
    out["L2"] = l2_populations(load_populations())
    out["L3"] = l3_fixtures()
    s = btc_state()
    warm = int(np.ceil(L.WARMUP_DAYS * 86400 / L.BIN_S))
    out["L4"] = l4_causality("BTC", s["panels"], s["oi"], s["t0_arch"], s["minutes"], s["maps"])
    out["L5"] = l5_validity(s["panel"], s["maps"]["actual"]["run"], s["t0_arch"], s["panels"], warm)
    out["L6"] = l6_counts(s["states"], s["events"], s["series"], out["L5"])
    out["L7"] = {"BTC": l7_coverage(s["panel"], s["maps"]["actual"]["run"], s["oi"], s["t0_arch"], s["meta"]),
                 "BTC_sidecar": l7_sidecar("BTC"), "ETH_sidecar": l7_sidecar("ETH"), "pass": True}
    out["L8"] = l8_alignment("BTC", s["oi"], s["t0_arch"])
    out["pass"] = all(out[k]["pass"] for k in ("L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"))
    M.write_json(RESULTS / "preconditions.json", out)
    return out


FROZEN_FILES = ["PREREGISTRATION_LIQMAP.md", "liqmap_data.py", "liqmap_lib.py", "liqmap_run.py",
                "tests/test_liqmap.py", "results/liqmap/preconditions.json",
                "cache/BTCUSDT_metrics_5m_full.meta.json", "cache/ETHUSDT_metrics_5m_full.meta.json"]


def freeze0() -> Path:
    pre = json.loads((RESULTS / "preconditions.json").read_text())
    if not pre["pass"]:
        raise SystemExit("preconditions did not pass; nothing is frozen")
    target = RESULTS / "freeze_F0.json"
    if target.exists():
        raise SystemExit("freeze_F0.json exists; a freeze is never overwritten")
    M.write_json(target, {
        "freeze": "F0", "created_utc": now_utc(),
        "note": "Before any Q1 touch or turn label, any continuation value at an event, any matched placebo and any "
                "paired difference. The checks built both BTC maps, every feature, both thresholds, the tercile cuts, "
                "L5's correlations and each trade's first event minute of every kind (section 6).",
        "family": pre["L6"]["family"], "inputs_sha256": input_hashes(),
        "files": {(HERE / f).resolve().relative_to(M.ROOT).as_posix(): M.sha256(HERE / f) for f in FROZEN_FILES}})
    return target


# --- Q1, levels ------------------------------------------------------------------------------------------

def q1_outcomes(panels: dict, asset: str, states: pd.DataFrame) -> pd.DataFrame:
    """Touch and turn of each map's cluster, per eligible hourly state and side (section 5 Q1)."""
    p = panels[asset]
    high = np.where(p["volume"] > 0, p["high"], np.nan)
    low = np.where(p["volume"] > 0, p["low"], np.nan)
    present = np.isfinite(present_close(p))
    rows = []
    for side, sgn, label in (("up", 1, "up"), ("dn", -1, "down")):
        both = (states[f"actual_{side}_defined"] & states[f"control_{side}_defined"]).to_numpy(bool)
        sub = states[both]
        for st in sub.itertuples(index=False):
            m = int(st.minute)
            a = L.touch_turn(high, low, present, m, float(getattr(st, f"actual_{side}_level")), sgn)
            b = L.touch_turn(high, low, present, m, float(getattr(st, f"control_{side}_level")), sgn)
            rows.append({"side": label, "minute": m, "ts": int(st.ts), "day": st.day, "price": float(st.price),
                         "d_A": float(getattr(st, f"actual_{side}_d")), "d_B": float(getattr(st, f"control_{side}_d")),
                         "touch_A": a[0], "turn_A": a[1], "t_touch_A": a[2],
                         "touch_B": b[0], "turn_B": b[1], "t_touch_B": b[2]})
    return pd.DataFrame(rows)


def q1_tests(q1: pd.DataFrame, axis: list[str], idx: np.ndarray) -> dict:
    """The four paired primary numbers of Q1, each claiming Delta > 0."""
    out = {}
    for side, label in (("up", "up"), ("down", "down")):
        s = q1[q1["side"] == label]
        touch = s.assign(delta=s["touch_A"] - s["touch_B"])
        out[f"{label}_touch"] = {"n": len(touch), **L.paired_stats(touch["day"], touch["delta"].to_numpy(float),
                                                                  axis, idx, claimed_sign=+1),
                                 "rate_A": float(s["touch_A"].mean()), "rate_B": float(s["touch_B"].mean())}
        both = s[(s["touch_A"] == 1) & (s["touch_B"] == 1)]
        turn = both.assign(delta=both["turn_A"] - both["turn_B"])
        out[f"{label}_turn"] = {"n": len(turn), **L.paired_stats(turn["day"], turn["delta"].to_numpy(float),
                                                                axis, idx, claimed_sign=+1),
                                "rate_A": float(both["turn_A"].mean()) if len(both) else float("nan"),
                                "rate_B": float(both["turn_B"].mean()) if len(both) else float("nan"),
                                "unpaired_rate_A": float(s.loc[s["touch_A"] == 1, "turn_A"].mean()),
                                "unpaired_rate_B": float(s.loc[s["touch_B"] == 1, "turn_B"].mean()),
                                "mean_d_A_minus_d_B": float((both["d_A"].abs() - both["d_B"].abs()).mean())
                                if len(both) else float("nan")}
    return out


# --- Q2, exit information ---------------------------------------------------------------------------------

def q2_rows(events: dict, series: dict) -> tuple[pd.DataFrame, dict]:
    """One matched row per event trade and kind, with the continuation value and the era-matched placebo."""
    frames, per_pop = [], {}
    for p, ev in events.items():
        grids = M.build_grids(ev, series, with_cv=True)
        axis = M.day_axis(ev["entry_day"])
        idx = M.block_indices(len(axis))
        per_pop[p] = {"axis": axis, "idx": idx}
        for kind in KINDS:
            rows = L.match_liq(ev, grids, kind, window_minutes(p))
            if len(rows):
                rows["delta"] = rows["cv"] - rows["placebo"]
                rows["delta_notime"] = rows["cv_notime"] - rows["placebo_notime"]
                rows["delta_time_only"] = rows["cv"] - rows["placebo_time_only"]
            rows.insert(0, "kind", kind)
            rows.insert(0, "pop", p)
            frames.append(rows)
    return pd.concat(frames, ignore_index=True), per_pop


def paired_placebo(rows: pd.DataFrame, pop: str, a: str, b: str, axis: list[str], idx: np.ndarray) -> dict:
    """Section 5's paired placebo comparison: delta of the actual kind minus delta of its control-map twin."""
    ra = rows[(rows["pop"] == pop) & (rows["kind"] == a) & np.isfinite(rows["delta"])]
    rb = rows[(rows["pop"] == pop) & (rows["kind"] == b) & np.isfinite(rows["delta"])]
    j = ra.merge(rb[["tid", "delta"]], on="tid", suffixes=("", "_ctrl"))
    if not len(j):
        return {"n": 0}
    d = (j["delta"] - j["delta_ctrl"]).to_numpy(float)
    return {"n": len(j), **L.paired_stats(j["entry_day"], d, axis, idx, claimed_sign=-1)}


def q2_tests(rows: pd.DataFrame, per_pop: dict, events: dict) -> dict:
    tests = {}
    for p, meta in per_pop.items():
        axis, idx = meta["axis"], meta["idx"]
        for kind in KINDS:
            r = rows[(rows["pop"] == p) & (rows["kind"] == kind)]
            inc = r[np.isfinite(r["delta"])]
            t = {"delta": L.paired_stats(inc["entry_day"], inc["delta"].to_numpy(float), axis, idx, claimed_sign=-1)
                 if len(inc) else {"n": 0},
                 "mean_cv_at_event": float(inc["cv"].mean()) if len(inc) else float("nan"),
                 "mean_placebo": float(inc["placebo"].mean()) if len(inc) else float("nan"),
                 "secondary_time_only": L.paired_stats(inc["entry_day"], inc["delta_time_only"].to_numpy(float),
                                                       axis, idx, claimed_sign=-1) if len(inc) else {"n": 0},
                 "secondary_no_time_exit": L.paired_stats(inc["entry_day"], inc["delta_notime"].to_numpy(float),
                                                          axis, idx, claimed_sign=-1) if len(inc) else {"n": 0}}
            tests[f"{p}:{kind}"] = t
        for a, b in (("EV1", "EV1_ctrl"), ("EV2", "EV2_ctrl")):
            tests[f"{p}:{a}"]["placebo_comparison"] = paired_placebo(rows, p, a, b, axis, idx)
    return tests


# --- outcomes ---------------------------------------------------------------------------------------------

def _check_freeze() -> dict:
    f0 = json.loads((RESULTS / "freeze_F0.json").read_text())
    changed = [f for f, h in f0["files"].items() if M.sha256(M.ROOT / f) != h]
    if changed or input_hashes() != f0["inputs_sha256"]:
        raise SystemExit(f"changed since F0: {changed or 'inputs'}")
    return f0


def outcomes() -> dict:
    f0 = _check_freeze()
    if (RESULTS / "verdict.json").exists() or (RESULTS / "report.json").exists():
        raise SystemExit("the BTC outcome run already happened; it never reruns")
    pre = json.loads((RESULTS / "preconditions.json").read_text())
    s = btc_state()

    q1 = q1_outcomes(s["panels"], "BTC", s["states"])
    q1_axis = M.day_axis(q1["day"])
    q1_idx = M.block_indices(len(q1_axis))
    tests = {k: v for k, v in q1_tests(q1, q1_axis, q1_idx).items()}
    rows, per_pop = q2_rows(s["events"], s["series"])
    tests.update(q2_tests(rows, per_pop, s["events"]))

    for key, t in tests.items():
        if key.startswith(DECISION_POP + ":"):              # the counts the family was fixed on must be the ones seen
            kind = key.split(":")[1]
            want = pre["L6"]["populations"][DECISION_POP]["tests"][kind]["included_with_min_controls"]
            if t["delta"]["n"] != want:
                raise SystemExit(f"{key}: {t['delta']['n']} included trades, the preconditions counted {want}")

    family = f0["family"]
    p_family = {}
    for key in family:
        t = tests[key]
        p_family[key] = (t["p_one_sided"] if "p_one_sided" in t else t["delta"]["p_one_sided"])
    holm = M.holm_adjust(p_family) if p_family else {}

    for key, t in tests.items():
        q1_test = key in Q1_TESTS
        band = L.BANDS["touch"] if key.endswith("touch") else \
               L.BANDS["turn"] if key.endswith("turn") else L.BANDS["R"]
        stat = t if q1_test else t["delta"]
        control = None
        if not q1_test:
            kind = key.split(":")[1]
            control = {}
            if "placebo_comparison" in t:
                control["placebo"] = t["placebo_comparison"]
            if kind == "EV2":
                control["sign"] = tests[f"{key.split(':')[0]}:EV2_against"]["delta"]
            control = control or None
        t["holm_p"] = holm.get(key)
        t["in_family"] = key in family
        t["classification"] = L.classify_liq(stat, t["in_family"], t["holm_p"], control,
                                             claimed_sign=+1 if q1_test else -1, band=band)

    informative = [k for k, t in tests.items() if t["classification"].startswith("INFORMATIVE")]
    q1.to_csv(RESULTS / "q1_states.csv.gz", index=False)
    rows.to_csv(RESULTS / "q2_events.csv.gz", index=False)
    trade_cols = ["pop", "tid", "asset", "direction", "entry_ts", "entry_day", "entry", "risk", "i0", "x", "kind",
                  "exit_price"] + [f"{k}_first" for k in KINDS] + [f"{k}_eligible" for k in KINDS]
    pd.concat([ev[trade_cols] for ev in s["events"].values()], ignore_index=True) \
        .to_csv(RESULTS / "trades.csv.gz", index=False)
    M.write_json(RESULTS / "report.json", {"created_utc": now_utc(), "f0_created_utc": f0["created_utc"],
                                           "tests": tests, "family": family, "holm_p": holm,
                                           "informative": informative,
                                           "q1_states_sha256": M.sha256(RESULTS / "q1_states.csv.gz"),
                                           "q2_events_sha256": M.sha256(RESULTS / "q2_events.csv.gz")})
    verdict = {"created_utc": now_utc(), "f0_created_utc": f0["created_utc"],
               "classifications": {k: t["classification"] for k, t in tests.items()},
               "informative_btc": informative, "family": family, "holm_p": holm,
               "l5_caveat_volatility_proxy": pre["L5"].get("caveat_volatility_proxy"),
               "verdict": "pending the ETH replication" if informative else "NONE REPLICATED (nothing INFORMATIVE on BTC)",
               "permits": "nothing in production; a replicated test permits only a separate stage-B pre-registration"}
    M.write_json(RESULTS / "verdict.json", verdict)
    return verdict


# --- the ETH replication ------------------------------------------------------------------------------------

def holdout() -> dict:
    """The only entry point that constructs an ETH map (section 9)."""
    if not (RESULTS / "verdict.json").exists():
        raise SystemExit("no BTC verdict; the ETH map is never built before one exists")
    if (RESULTS / "holdout_eth.json").exists():
        raise SystemExit("holdout_eth.json exists; the replication never reruns")
    f0 = _check_freeze()
    verdict = json.loads((RESULTS / "verdict.json").read_text())
    verdict_sha = M.sha256(RESULTS / "verdict.json")

    panels = load_panels()
    oi, t0_arch, meta = load_archive("ETH")
    panel = bin_panel("ETH", panels, oi, t0_arch)
    pops = load_populations()
    eth = pops["chento"][(pops["chento"]["asset"] == "ETH")
                         & (pops["chento"]["entry_ts"] >= int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp()))] \
        .reset_index(drop=True)
    state_minutes = hourly_state_minutes(panels["ETH"], t0_arch)
    minutes = np.concatenate([state_minutes, eth["i0"].to_numpy(np.int64) - 1])
    close = present_close(panels["ETH"])
    q_bin, q_price = map_queries(minutes, close, t0_arch, panels["ETH"]["t0_s"])
    maps = run_both_maps(panel, q_bin, q_price)
    st, en = split_queries(maps, len(state_minutes))
    states = state_table("ETH", panels, state_minutes, st)
    feats = entry_features("ETH", panels, eth, t0_arch, states["ts"].to_numpy(np.int64), st, en, maps)
    events = {"chento": trade_events("ETH", panels, feats, t0_arch, maps)}
    series = {"ETH": M.load_series("ETH")}

    causality = l4_causality("ETH", panels, oi, t0_arch, minutes, maps)
    if not causality["pass"]:
        M.write_json(RESULTS / "holdout_eth.json", {"created_utc": now_utc(), "verdict_sha256": verdict_sha,
                                                    "L4_eth": causality, "replicated": [],
                                                    "note": "an ETH causality cut failed; the run stopped and nothing "
                                                            "is REPLICATED"})
        raise SystemExit("ETH causality cut failed; nothing is REPLICATED")

    l5 = {"note": "ETH's L5 counterpart is reported, never a gate", "pass": True}
    counts = l6_counts(states, events, series, l5)
    q1 = q1_outcomes(panels, "ETH", states)
    q1_axis = M.day_axis(q1["day"])
    q1_idx = M.block_indices(len(q1_axis))
    tests = dict(q1_tests(q1, q1_axis, q1_idx))
    rows, per_pop = q2_rows(events, series)
    tests.update(q2_tests(rows, per_pop, events))

    replicated = []
    for key in verdict["informative_btc"]:
        t = tests.get(key)
        if t is None:
            continue
        if key in Q1_TESTS:
            lo, hi = t["ci95"]
            blo, bhi = L.BANDS["touch"] if key.endswith("touch") else L.BANDS["turn"]
            ok = t["mean"] > 0 and lo > 0 and not (blo < lo and hi < bhi)
        else:
            d, pc = t["delta"], t.get("placebo_comparison", {"n": 0})
            ok = (d["n"] >= 20 and d["mean"] < 0 and d["p_one_sided"] < 0.10
                  and pc.get("n", 0) > 0 and pc["mean"] < 0 and pc["p_one_sided"] < 0.10)
        t["replicated"] = bool(ok)
        if ok:
            replicated.append(key)

    out = {"created_utc": now_utc(), "verdict_sha256": verdict_sha, "f0_created_utc": f0["created_utc"],
           "L4_eth": causality, "counts": counts, "tests": tests, "replicated": replicated,
           "note": "ETH is a replication on a correlated asset over the same period, not an independent holdout."}
    q1.to_csv(RESULTS / "q1_states_eth.csv.gz", index=False)
    rows.to_csv(RESULTS / "q2_events_eth.csv.gz", index=False)
    M.write_json(RESULTS / "holdout_eth.json", out)
    final = json.loads((RESULTS / "verdict.json").read_text())
    final["holdout"] = {"created_utc": out["created_utc"], "replicated": replicated,
                        "classifications_eth": {k: tests[k].get("replicated") for k in verdict["informative_btc"]
                                                if k in tests}}
    final["verdict"] = ("REPLICATED: " + ", ".join(replicated)) if replicated else "NONE REPLICATED"
    M.write_json(RESULTS / "verdict.json", final)
    return final


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["checks", "freeze0", "outcomes", "holdout"])
    stage = ap.parse_args().stage
    res = {"checks": checks, "freeze0": freeze0, "outcomes": outcomes, "holdout": holdout}[stage]()
    print(json.dumps(res if isinstance(res, dict) else str(res), indent=1, default=str)[:6000])

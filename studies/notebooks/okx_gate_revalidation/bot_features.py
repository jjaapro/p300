"""§9 step 3 — per-trigger features from the bot's own code, one process per asset (§2.2).

For each pool trigger t, at sim clock t: `_rebuild_daily_cache(t, force=True)`, assert the
frame ends at t, read close / atr / ret_30d / okx_delta_z (R1) / the triple anchors, compute
dist_R from the bot's order blocks; then rebuild with `_load_okx_1h` wrapped to call the
original with now − 1h and read okx_delta_z (R2) only (§0.3.2).

Also written before any outcome: P2 fidelity, precision (report-only), the R1-vs-R2 and
R1-vs-same-hour disagreement rates, and the POWER counts. The same-hour z goes to its own
file, features_sh_{asset}.csv, which outcomes.py never opens.

No prices after t enter a feature: every frame is bounded at the clock by the loaders.

  python studies/notebooks/okx_gate_revalidation/bot_features.py --asset BTC
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import tempfile  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

import okxlib as L  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--asset", required=True, choices=L.ASSETS)
    ap.add_argument("--snapshot", type=Path, default=L.DEFAULT_SNAPSHOT)
    ap.add_argument("--results-dir", type=Path, default=L.RESULTS)
    ap.add_argument("--max-days", type=int, default=None,
                    help="tests only: truncate the precision day loop")
    args = ap.parse_args(argv)
    asset = args.asset
    snapshot = args.snapshot.resolve()
    res = args.results_dir
    scratch = Path(tempfile.mkdtemp(prefix=f"okx_features_{asset}_"))
    try:
        meta = L.verify_snapshot(snapshot, res)
        L.require((res / "pool_parity.json").exists(), "results/pool_parity.json missing (run gen_pool.py)")
        L.require((res / f"pool_{asset}.csv").exists(), f"results/pool_{asset}.csv missing")
        pp = L.read_json(res / "pool_parity.json")
        L.check_provenance(pp.get("provenance"), meta["file_sha256"], HERE / "gen_pool.py", "pool_parity.json")
        L.require(pp["outputs_sha256"][f"pool_{asset}.csv"] == L.file_sha256(res / f"pool_{asset}.csv"),
                  f"pool_{asset}.csv differs from the one gen_pool.py recorded")
        L.set_process_env(asset, scratch)
        L.install_connect_guard(allowed=[snapshot])
        signal, ctm, clock = L.import_sleeve(snapshot, asset)
    except L.Refusal as e:
        print(f"REFUSED: {e}")
        return 2

    import numpy as np
    import pandas as pd

    pool = pd.read_csv(res / f"pool_{asset}.csv")
    pool["t"] = [L.parse_ts(x) for x in pool["t"]]
    pool = pool.sort_values("t", kind="mergesort").reset_index(drop=True)

    orig_load = signal._load_okx_1h
    newest: dict[str, int | None] = {}

    def loader_r1(now, days_back):
        s = orig_load(now, days_back)
        newest["v"] = None if s.empty else int(s.index.max().timestamp())
        return s

    def loader_r2(now, days_back):
        s = orig_load(now - timedelta(hours=1), days_back)
        newest["v"] = None if s.empty else int(s.index.max().timestamp())
        return s

    def rebuild(t: int, loader):
        now = datetime.fromtimestamp(t, tz=timezone.utc)
        clock.set_simulated_now(now)
        newest.clear()
        signal._load_okx_1h = loader
        try:
            signal._rebuild_daily_cache(now, force=True)
        finally:
            signal._load_okx_1h = orig_load
        df = signal._cached_features["df"]
        # _rebuild_daily_cache returns early on an empty frame WITHOUT replacing the cache,
        # which would silently serve the previous trigger's features.
        L.require(len(df) > 0 and df.index[-1] == pd.Timestamp(now), "rebuild did not end at the clock")
        return df, newest.get("v")

    rows = []
    for t, d in zip(pool["t"], pool["direction"]):
        ts = pd.Timestamp(t, unit="s", tz="UTC")
        df, new1 = rebuild(int(t), loader_r1)
        row = df.iloc[-1]
        entry = float(row["close"])
        atr = float(row["atr"])
        risk = atr * L.ATR_STOP_MULT
        if np.isfinite(risk) and risk > 0:
            dist = float(ctm.nearest_resist_ob_distance_R(entry, d, risk, signal._cached_obs, len(df) - 1))
        else:
            dist = float("nan")
        rec = {"t": int(t), "direction": d, "entry": entry, "atr": atr, "risk": risk, "dist_R": dist,
               "ret_30d": float(row["ret_30d"]), "z_R1": float(row["okx_delta_z"]),
               "anchor": bool(row[f"triple_{d}_anchor"]), "okx_newest_R1": new1}
        df2, new2 = rebuild(int(t), loader_r2)
        rec["z_R2"] = float(df2.iloc[-1]["okx_delta_z"])
        rec["okx_newest_R2"] = new2
        rows.append(rec)
        L.require(df2.index[-1] == ts, "R2 rebuild did not end at the clock")
    feats = L.membership(pd.DataFrame(rows))

    # Same-hour (C4-style) z: full-history Binance 1h resample and OKX, attached at the newest
    # joined hour <= t (overlay_study/gen_trades.py:83-87) — the control arm's feature.
    con = L.ro_connect(snapshot)
    try:
        b15 = pd.read_sql(f"SELECT timestamp, close FROM {L.PERP_15M[asset]} ORDER BY timestamp", con)
        okx = pd.read_sql(f"SELECT timestamp, close FROM {L.OKX_1H[asset]} ORDER BY timestamp", con)
    finally:
        con.close()
    b15.index = pd.to_datetime(b15.pop("timestamp"), unit="s", utc=True).dt.as_unit("ns")
    okx.index = pd.to_datetime(okx.pop("timestamp"), unit="s", utc=True).dt.as_unit("ns")
    z_sh = ctm.compute_okx_delta_z(b15["close"].resample("1h").last(), okx["close"],
                                   window_hours=L.OKX_WINDOW_HOURS)
    t_idx = pd.to_datetime(feats["t"], unit="s", utc=True).dt.as_unit("ns")
    ix = z_sh.index.searchsorted(pd.DatetimeIndex(t_idx), side="right") - 1
    sh = pd.DataFrame({"t": feats["t"], "direction": feats["direction"],
                       "z_SH": [float(z_sh.iloc[i]) if i >= 0 else float("nan") for i in ix],
                       "sh_hour": [int(z_sh.index[i].timestamp()) if i >= 0 else None for i in ix]})

    # Precision (report-only): end-of-day frames over every window day.
    days = []
    d0 = datetime.fromtimestamp(L.WINDOW_START, tz=timezone.utc)
    while int(d0.timestamp()) < L.WINDOW_END:
        days.append(d0)
        d0 += timedelta(days=1)
    if args.max_days is not None:
        days = days[: args.max_days]
    pool_keys = set(zip(feats["t"].astype(int), feats["direction"]))
    by_day: dict[str, list[tuple[int, str]]] = {}
    for t, d in pool_keys:
        by_day.setdefault(datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat(), []).append((t, d))
    anchors: list[tuple[int, str]] = []
    eod_anchor: dict[tuple[int, str], bool] = {}
    for day in days:
        eod = int(day.timestamp()) + 23 * 3600 + 45 * 60
        df, _ = rebuild(eod, loader_r1)
        part = df.loc[pd.Timestamp(day):]
        for ts_i, la, sa in zip(part.index, part["triple_long_anchor"], part["triple_short_anchor"]):
            if bool(la):
                anchors.append((int(ts_i.timestamp()), "long"))
            if bool(sa):
                anchors.append((int(ts_i.timestamp()), "short"))
        for t, d in by_day.get(day.date().isoformat(), []):
            eod_anchor[(t, d)] = bool(df.loc[pd.Timestamp(t, unit="s", tz="UTC"), f"triple_{d}_anchor"])
    anchors.sort()
    retained = []
    for t, d in anchors:
        if not retained or t - retained[-1][0] >= L.COOLDOWN_HOURS * 3600:
            retained.append((t, d))
    n_in_pool = sum(1 for k in retained if k in pool_keys)
    feats["anchor_eod"] = [eod_anchor.get((int(t), d)) for t, d in zip(feats["t"], feats["direction"])]

    # Feature-level disagreement rates and POWER counts (§2.2, §4.4).
    def disagreement(za, zb):
        dec = [L.okx_pass(a, d) != L.okx_pass(b, d) for a, b, d in zip(za, zb, feats["direction"])]
        both = [(a, b) for a, b in zip(za, zb) if np.isfinite(a) and np.isfinite(b)]
        sign = [np.sign(a) != np.sign(b) for a, b in both]
        return {"n": int(len(dec)), "gate_decision_rate": float(np.mean(dec)) if dec else float("nan"),
                "sign_rate_both_finite": float(np.mean(sign)) if sign else float("nan"),
                "n_both_finite": int(len(both)),
                "nan_a": int(np.sum(~np.isfinite(np.asarray(za, float)))),
                "nan_b": int(np.sum(~np.isfinite(np.asarray(zb, float))))}

    floor_h = (feats["t"].astype(int) // 3600) * 3600
    off = feats[feats["in_off"]]
    fid = {
        "asset": asset,
        "p2_denominator": int(len(feats)),
        "p2_numerator": int(feats["anchor"].sum()),
        "fidelity": float(feats["anchor"].mean()) if len(feats) else float("nan"),
        "precision": {"days": len(days), "anchor_bars": len(anchors), "retained_after_cooldown": len(retained),
                      "retained_in_pool": n_in_pool,
                      "precision": n_in_pool / len(retained) if retained else float("nan"),
                      "pool_triggers_eod_anchor_differs": int(sum(
                          1 for a, e in zip(feats["anchor"], feats["anchor_eod"]) if e is not None and a != e)),
                      "pool_triggers_without_eod_frame": int(sum(1 for e in feats["anchor_eod"] if e is None))},
        "atr_drops": int(feats["atr_drop"].sum()),
        "nan_z_R1_pool": int((~np.isfinite(feats["z_R1"].astype(float))).sum()),
        "nan_z_R2_pool": int((~np.isfinite(feats["z_R2"].astype(float))).sum()),
        "nan_z_R1_off": int((~np.isfinite(off["z_R1"].astype(float))).sum()),
        "nan_z_R2_off": int((~np.isfinite(off["z_R2"].astype(float))).sum()),
        "power_R1": {"n_off": int(len(off)), "n_K": int(off["in_on_R1"].sum()),
                     "n_B": int((~off["in_on_R1"].astype(bool)).sum())},
        "power_R2": {"n_K": int(off["in_on_R2"].sum()), "n_B": int((~off["in_on_R2"].astype(bool)).sum())},
        "disagreement_R1_vs_R2": disagreement(feats["z_R1"].astype(float), feats["z_R2"].astype(float)),
        "disagreement_R1_vs_same_hour": disagreement(feats["z_R1"].astype(float), sh["z_SH"].astype(float)),
        "okx_newest_R1_not_floor_minus_1h": int(sum(1 for n, f in zip(feats["okx_newest_R1"], floor_h)
                                                    if n != f - 3600)),
        "okx_newest_R2_not_floor_minus_2h": int(sum(1 for n, f in zip(feats["okx_newest_R2"], floor_h)
                                                    if n != f - 7200)),
        "same_hour_attach_not_floor": int(sum(1 for n, f in zip(sh["sh_hour"], floor_h) if n != f)),
    }

    L.assert_p0d_after(snapshot, scratch)
    out = feats.copy()
    out["t"] = [L.iso(x) for x in out["t"]]
    for c in ("okx_newest_R1", "okx_newest_R2"):
        out[c] = [L.iso(int(x)) if not pd.isna(x) else "" for x in out[c]]
    cols = ["t", "direction", "entry", "atr", "risk", "dist_R", "ret_30d", "z_R1", "z_R2", "anchor",
            "anchor_eod", "okx_newest_R1", "okx_newest_R2", "atr_drop", "in_off", "in_on_R1", "in_on_R2"]
    out[cols].to_csv(res / f"features_{asset}.csv", index=False, lineterminator="\n")
    sh_out = sh.copy()
    sh_out["t"] = [L.iso(x) for x in sh_out["t"]]
    sh_out["sh_hour"] = [L.iso(int(x)) if not pd.isna(x) else "" for x in sh_out["sh_hour"]]
    sh_out.to_csv(res / f"features_sh_{asset}.csv", index=False, lineterminator="\n")
    fid["provenance"] = L.provenance(meta["file_sha256"], Path(__file__),
                                     {f"pool_{asset}.csv": res / f"pool_{asset}.csv"})
    fid["outputs_sha256"] = {n: L.file_sha256(res / n) for n in (f"features_{asset}.csv",
                                                                f"features_sh_{asset}.csv")}
    L.write_json_atomic(res / f"fidelity_{asset}.json", fid)
    print(f"{asset}: pool {len(feats)}, fidelity {fid['fidelity']:.4f}, OFF {fid['power_R1']['n_off']}, "
          f"precision days {len(days)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

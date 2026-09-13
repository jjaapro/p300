"""§9 step 4 — P0: the R1 okx_delta_z reproduces the six live values (§0.4).

The asset is fixed at sleeve import, so the parent imports no sleeve and runs one child
process per asset. Each child rebuilds at its P0 bars under R1 and R2 and checks:
  P0a/P0b  |z_R1 − README value| <= 1e-9, the recorded source (ledger notes
           `_filter_diag.okx_delta_z`, or the diag near-miss copied into the snapshot) equals
           the README value, and okx_aligned(z_R1, dir, 0.0) gives the live decision;
  P0c      newest OKX hour in the frame = floor_hour(t) − 1h (R1) and − 2h (R2);
  P0d      process hygiene, asserted before and after.

  python studies/notebooks/okx_gate_revalidation/parity_check.py
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

import okxlib as L  # noqa: E402


def child(asset: str, snapshot: Path, results_dir: Path, out: Path) -> int:
    scratch = Path(tempfile.mkdtemp(prefix=f"okx_parity_{asset}_"))
    L.verify_snapshot(snapshot, results_dir)
    L.set_process_env(asset, scratch)
    L.install_connect_guard(allowed=[snapshot])
    signal, ctm, clock = L.import_sleeve(snapshot, asset)
    import pandas as pd

    con = L.ro_connect(snapshot)
    try:
        ledger = {r[0]: r for r in con.execute("SELECT id, direction, notes FROM p3_ledger")}
        near = {(r[0], r[1]): r for r in con.execute("SELECT asset, ts, direction, okx_delta_z FROM p0_bars")}
    finally:
        con.close()

    orig = signal._load_okx_1h
    newest = {}

    def make_loader(shift_h):
        def loader(now, days_back):
            s = orig(now - timedelta(hours=shift_h), days_back)
            newest["v"] = None if s.empty else int(s.index.max().timestamp())
            return s
        return loader

    results = []
    for bar in (b for b in L.P0_BARS if b["asset"] == asset):
        t = L.parse_ts(bar["t"])
        now = datetime.fromtimestamp(t, tz=timezone.utc)
        if bar["ledger"] is not None:
            _, direction, notes = ledger[bar["ledger"]]
            direction = direction.lower()
            recorded = float(L.parse_ledger_notes(notes)[0]["_filter_diag"]["okx_delta_z"])
        else:
            _, _, direction, recorded = near[(asset, bar["t"])]
        zs, news, ends = {}, {}, {}
        for label, shift in (("R1", 0), ("R2", 1)):
            clock.set_simulated_now(now)
            signal._load_okx_1h = make_loader(shift)
            newest.clear()
            try:
                signal._rebuild_daily_cache(now, force=True)
            finally:
                signal._load_okx_1h = orig
            df = signal._cached_features["df"]
            ends[label] = bool(len(df) > 0 and df.index[-1] == pd.Timestamp(now))
            zs[label] = float(df.iloc[-1]["okx_delta_z"])
            news[label] = newest.get("v")
        floor_h = (t // 3600) * 3600
        decision = bool(ctm.okx_aligned(zs["R1"], direction, L.OKX_ALIGN_Z_MIN))
        results.append({
            "asset": asset, "t": bar["t"], "direction": direction, "source": bar["ledger"] or "diag",
            "abs_diff_R1": abs(zs["R1"] - bar["z"]),
            "z_ok": abs(zs["R1"] - bar["z"]) <= L.P0_TOL,
            "recorded_equals_readme": recorded == bar["z"],
            "decision_pass": decision, "decision_ok": decision == bar["expect"],
            "newest_R1_ok": news["R1"] == floor_h - 3600, "newest_R2_ok": news["R2"] == floor_h - 7200,
            "frames_end_at_clock": ends["R1"] and ends["R2"],
        })
    L.assert_p0d_after(snapshot, scratch)
    L.write_json_atomic(out, {"asset": asset, "bars": results, "p0d_ok": True})
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", type=Path, default=L.DEFAULT_SNAPSHOT)
    ap.add_argument("--results-dir", type=Path, default=L.RESULTS)
    ap.add_argument("--child", choices=L.ASSETS)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--error-out", type=Path)
    args = ap.parse_args(argv)
    snapshot = args.snapshot.resolve()
    if args.child:
        try:
            return child(args.child, snapshot, args.results_dir, args.out)
        except BaseException as e:  # noqa: BLE001 — record type and location only
            L.write_json_atomic(args.error_out, L.exception_record(e))
            os._exit(3)
    try:
        meta = L.verify_snapshot(snapshot, args.results_dir)
    except L.Refusal as e:
        print(f"REFUSED: {e}")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="okx_parity_"))
    per_asset = {}
    for asset in L.ASSETS:
        out = tmp / f"{asset}.json"
        err = tmp / f"{asset}.error.json"
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", asset,
                               "--snapshot", str(snapshot), "--results-dir", str(args.results_dir),
                               "--out", str(out), "--error-out", str(err)], cwd=L.ROOT,
                              capture_output=True, text=True)
        if proc.returncode != 0 or not out.exists():
            # A crash is recorded, not silently re-runnable — and it is not an evaluated P0
            # failure, so it is not INVALID either (A3).
            record = L.read_json(err) if err.exists() else {"exception_type": "child_exit",
                                                            "returncode": proc.returncode}
            L.write_json_atomic(args.results_dir / "parity_error.json", {"asset": asset, **record})
            print(f"REFUSED: parity child for {asset} did not complete (results/parity_error.json)")
            return 2
        with open(out, "r", encoding="utf-8") as fh:
            per_asset[asset] = json.load(fh)
    bars = [b for a in L.ASSETS for b in per_asset[a]["bars"]]
    checks = ("z_ok", "recorded_equals_readme", "decision_ok", "newest_R1_ok", "newest_R2_ok",
              "frames_end_at_clock")
    ok = (len(bars) == len(L.P0_BARS) and all(per_asset[a]["p0d_ok"] for a in L.ASSETS)
          and all(b[c] for b in bars for c in checks))
    L.write_json_atomic(args.results_dir / "parity.json",
                        {"pass": bool(ok), "bars": bars, "checks": list(checks),
                         "tolerance": L.P0_TOL,
                         "provenance": L.provenance(meta["file_sha256"], Path(__file__))})
    print(f"P0 {'pass' if ok else 'FAIL'} ({len(bars)} bars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

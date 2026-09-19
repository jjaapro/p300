"""SQUEEZE_BULL on ETH: freeze, then the one outcome run (README.md is the pre-registration).

    python studies/notebooks/squeeze_bull_eth_2026_09/run_sqb_eth.py freeze0
    python studies/notebooks/squeeze_bull_eth_2026_09/run_sqb_eth.py outcomes
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sqb_eth_lib as L  # noqa: E402
import micro_lib as M  # noqa: E402  (write_json, sha256; on the path via ss_eth_lib's sys.path)

RESULTS = L.RESULTS
FROZEN = ["README.md", "sqb_eth_lib.py", "run_sqb_eth.py", "tests/test_sqb_eth.py"]
DATA_FILES = {"perp_BTC": L.SS.ORB_CACHE / "BTCUSDT_perp_1m.npz", "perp_ETH": L.SS.ORB_CACHE / "ETHUSDT_perp_1m.npz",
              "metrics_BTC": L.SS.EP_CACHE / "BTCUSDT_metrics_5m_full.npz",
              "metrics_ETH": L.SS.EP_CACHE / "ETHUSDT_metrics_5m_full.npz",
              "reference_ledger": L.REF_LEDGER,
              "june_engine": L.ROOT / "studies/notebooks/oi_flush/phase2_backtest.py",
              "revalidation_lib": L.ROOT / "studies/notebooks/squeeze_bull_revalidation/squeeze_bull_lib.py",
              "builders": L.ROOT / "studies/notebooks/short_squeeze_eth_2026_09/ss_eth_lib.py"}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def freeze0() -> None:
    target = RESULTS / "freeze_F0.json"
    if target.exists():
        raise SystemExit("freeze_F0.json exists; a freeze is never overwritten")
    RESULTS.mkdir(parents=True, exist_ok=True)
    M.write_json(target, {"freeze": "F0", "created_utc": now_utc(),
                          "note": "Before any ETH number. The README's engine, costs, gates and clauses are fixed here.",
                          "files": {f: M.sha256(HERE / f) for f in FROZEN},
                          "data": {k: M.sha256(p) for k, p in DATA_FILES.items()}})
    print("frozen:", target)


def outcomes() -> None:
    f0 = json.loads((RESULTS / "freeze_F0.json").read_text())
    changed = [f for f in FROZEN if M.sha256(HERE / f) != f0["files"][f]] + \
        [k for k, p in DATA_FILES.items() if M.sha256(p) != f0["data"][k]]
    if changed:
        raise SystemExit(f"changed since F0: {changed}")
    if (RESULTS / "report.json").exists():
        raise SystemExit("report.json exists; the outcome run already happened")
    out = {"created_utc": now_utc(), "f0_created_utc": f0["created_utc"],
           "constants": {"june_cost_bp": L.JUNE_COST_BP, "live_cost_bp": L.LIVE_COST_BP, "recost_R": L.RECOST_R,
                         "full_sample_start": str(L.FULL_SAMPLE_START), "oos_start": str(L.OOS_START)}}

    # P1: the builder on the BTC panels against the corrected-table reference ledger
    btc = L.hourly_frame("BTC")
    btc_led = L.ledger(btc)
    ref = L.load_reference()
    fid = L.fidelity(btc_led, ref)
    out["P1"] = fid
    btc_led.to_csv(RESULTS / "ledger_BTC_panel.csv", index=False)
    print("P1 fidelity:", {k: v for k, v in fid.items() if k not in ("only_panel", "only_ref")}, flush=True)
    btc_bull = L.bull_gated(btc_led)
    out["BTC_panel"] = {"frame": L.frame_facts(btc), "full_bull": L.stats(btc_bull), "oos_bull": L.stats(L.oos(btc_bull)),
                        "full_bull_10bp": L.stats(btc_bull, "r_10bp"), "pooled": L.stats(btc_led[btc_led["resolved"]])}
    ref_bull = ref[(ref["regime_backonly"] == L.BULL) & ref["resolved"] & (ref["ts"] >= L.FULL_SAMPLE_START)]
    out["BTC_reference"] = {"full_bull": L.stats(ref_bull), "oos_bull": L.stats(L.oos(ref_bull))}

    # ETH
    eth = L.hourly_frame("ETH")
    eth_led = L.ledger(eth)
    eth_led.to_csv(RESULTS / "ledger_ETH.csv", index=False)
    eth_bull = L.bull_gated(eth_led)
    full_s, oos_s = L.stats(eth_bull), L.stats(L.oos(eth_bull))
    out["ETH"] = {"frame": L.frame_facts(eth), "fires_all_regimes": int(len(eth_led)),
                  "by_regime": {k: int(v) for k, v in eth_led["regime_backonly"].value_counts().items()},
                  "full_bull": full_s, "oos_bull": oos_s, "full_bull_10bp": L.stats(eth_bull, "r_10bp"),
                  "oos_bull_10bp": L.stats(L.oos(eth_bull), "r_10bp"),
                  "pooled": L.stats(eth_led[eth_led["resolved"]]),
                  "by_regime_stats": {reg: L.stats(g[g["resolved"]]) for reg, g in eth_led.groupby("regime_backonly")},
                  "unresolved_bull": int(((eth_led["regime_backonly"] == L.BULL) & ~eth_led["resolved"]).sum())}
    proj = L.projection(eth_bull, eth, L.BUILD_MIN_N, oos_s.get("n", 0)) if oos_s.get("n", 0) < L.BUILD_MIN_N else None
    out["decision"] = L.decide(oos_s, full_s, fid, proj)
    M.write_json(RESULTS / "report.json", out)
    for label, s in (("ETH full bull", full_s), ("ETH OOS bull", oos_s), ("BTC panel full bull", out["BTC_panel"]["full_bull"]),
                     ("BTC reference full bull", out["BTC_reference"]["full_bull"])):
        if s.get("n"):
            print(f"{label:24s} n {s['n']:3d} mean {s['mean_R']:+.3f} WR {s['WR']:.2f} MAR {s['MAR']} halves "
                  f"{s['first_half_mean_R']:+.3f}/{s['second_half_mean_R'] if s['second_half_mean_R'] is None else round(s['second_half_mean_R'], 3)}", flush=True)
        else:
            print(f"{label:24s} n 0", flush=True)
    print("\nDECISION:", json.dumps(out["decision"], indent=1), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["freeze0", "outcomes"])
    args = ap.parse_args()
    {"freeze0": freeze0, "outcomes": outcomes}[args.stage]()

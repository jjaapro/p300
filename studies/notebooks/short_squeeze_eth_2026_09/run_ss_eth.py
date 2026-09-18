"""SHORT_SQUEEZE on ETH: freeze, then the one outcome run (README.md is the pre-registration).

    python studies/notebooks/short_squeeze_eth_2026_09/run_ss_eth.py freeze0
    python studies/notebooks/short_squeeze_eth_2026_09/run_ss_eth.py outcomes
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ss_eth_lib as L  # noqa: E402
import exec_lib as ex  # noqa: E402
import micro_lib as M  # noqa: E402

RESULTS = L.RESULTS
FROZEN = ["README.md", "ss_eth_lib.py", "run_ss_eth.py", "tests/test_ss_eth.py"]
DATA_FILES = {"perp_BTC": L.ORB_CACHE / "BTCUSDT_perp_1m.npz", "perp_ETH": L.ORB_CACHE / "ETHUSDT_perp_1m.npz",
              "spot_BTC": L.EP_CACHE / "BTCUSDT_spot_1m_panel.npz", "spot_ETH": L.EP_CACHE / "ETHUSDT_spot_1m_panel.npz",
              "metrics_BTC": L.EP_CACHE / "BTCUSDT_metrics_5m_full.npz", "metrics_ETH": L.EP_CACHE / "ETHUSDT_metrics_5m_full.npz",
              "path_eth_1m": ex.CACHE / "eth_1m.npz", "path_btc_1m": ex.CACHE / "btc_1m.npz",
              "engine": L.ROOT / "studies/notebooks/execution_2026_09/exec_lib.py"}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def freeze0() -> None:
    target = RESULTS / "freeze_F0.json"
    if target.exists():
        raise SystemExit("freeze_F0.json exists; a freeze is never overwritten")
    RESULTS.mkdir(parents=True, exist_ok=True)
    M.write_json(target, {"freeze": "F0", "created_utc": now_utc(),
                          "note": "Before any ETH number. The README's engine, costs, gates and bars are fixed here.",
                          "files": {f: M.sha256(HERE / f) for f in FROZEN},
                          "data": {k: M.sha256(p) for k, p in DATA_FILES.items()}})
    print("frozen:", target)


def run_asset(tables: dict, path: ex.PricePath, label: str) -> dict:
    b15, trig = L.frame(tables)
    gross = L.simulate(b15, trig, path, slip_bp=0.0)
    port = L.simulate(b15, trig, path, slip_bp=ex.SS_SLIPPAGE_BP)
    net = L.with_net(gross)
    net.to_csv(RESULTS / f"trades_{label}.csv", index=False)
    out = {"triggers": int(trig.sum()), "frame_span": [str(b15.index.min()), str(b15.index.max())],
           "gross": L.describe(net, "pnl_R"), "net": L.describe(net, "net_R"), "port_2bp": L.describe(port, "pnl_R"),
           "boot_net": L.block_bootstrap_mean(net, "net_R"), "boot_gross": L.block_bootstrap_mean(net, "pnl_R"),
           "cost_share_of_gross": (float(net["cost_R"].sum() / net["pnl_R"].sum()) if len(net) and net["pnl_R"].sum() > 0 else None)}
    g, n = out["gross"], out["net"]
    print(f"{label:12s} triggers {out['triggers']:3d} gross {g.get('mean_R', float('nan')):+.3f} R net {n.get('mean_R', float('nan')):+.3f} R "
          f"win {n.get('win_rate', float('nan')):.2f} PF {n.get('pf')} halves {n.get('first_half_mean_R')}/{n.get('second_half_mean_R')} "
          f"DSR {n.get('dsr')} MAR {n.get('mar')}", flush=True)
    return out, trig, net


def outcomes() -> None:
    f0 = json.loads((RESULTS / "freeze_F0.json").read_text())
    changed = [f for f in FROZEN if M.sha256(HERE / f) != f0["files"][f]] + \
        [k for k, p in DATA_FILES.items() if M.sha256(p) != f0["data"][k]]
    if changed:
        raise SystemExit(f"changed since F0: {changed}")
    if (RESULTS / "report.json").exists():
        raise SystemExit("report.json exists; the outcome run already happened")
    out = {"created_utc": now_utc(), "f0_created_utc": f0["created_utc"]}

    # P0: the injected-table engine equals the port on prod's BTC tables, and the port equals its E0 anchor
    prod = L.tables_from_prod()
    b15_ref, trig_ref = ex.short_squeeze_frame()
    b15_mine, trig_mine = L.frame(prod)
    same = bool(trig_ref.index.equals(trig_mine.index) and (trig_ref.to_numpy() == trig_mine.to_numpy()).all())
    btc_path = ex.path_for("BTC", "1m")
    port = L.simulate(b15_mine, trig_mine, btc_path, slip_bp=ex.SS_SLIPPAGE_BP)
    anchor = port[pd.to_datetime(port["trigger_ts"], utc=True) <= pd.Timestamp(L.E0_EXPECT["end"], tz="UTC")]
    r = anchor["pnl_R"].to_numpy(float)
    got = {"n": int(len(anchor)), "win": float((r > 0).mean()), "mean_r": float(r.mean()), "pf": L.profit_factor(r)}
    p0 = {"engine_equals_port": same, "anchor_got": got, "anchor_expected": L.E0_EXPECT,
          "anchor_pass": bool(got["n"] == L.E0_EXPECT["n"] and abs(got["win"] - L.E0_EXPECT["win"]) < 0.005
                              and abs(got["mean_r"] - L.E0_EXPECT["mean_r"]) < 0.01 and abs(got["pf"] - L.E0_EXPECT["pf"]) < 0.02)}
    p0["pass"] = bool(same and p0["anchor_pass"])
    out["P0"] = p0
    print("P0 engine == port:", same, "| anchor:", got, "->", "PASS" if p0["anchor_pass"] else "FAIL", flush=True)
    btc_prod, _, btc_prod_net = run_asset(prod, btc_path, "BTC_prod")

    # P1: the builder on the BTC panels reproduces the prod-table run
    btc_panel_tables = L.tables_from_panels("BTC")
    btc_panel, trig_panel, btc_panel_net = run_asset(btc_panel_tables, btc_path, "BTC_panel")
    fid = L.jaccard(trig_mine, trig_panel)
    shared = btc_prod_net.merge(btc_panel_net, on="trigger_ts", suffixes=("_prod", "_panel"))
    fid["shared_trades"] = int(len(shared))
    fid["max_abs_pnl_diff_R"] = float((shared["pnl_R_prod"] - shared["pnl_R_panel"]).abs().max()) if len(shared) else None
    fid["pnl_agree"] = bool(len(shared) and fid["max_abs_pnl_diff_R"] < 0.001)
    fid["pass"] = bool(fid["jaccard"] is not None and fid["jaccard"] >= L.JACCARD_MIN and fid["pnl_agree"])
    out["P1"] = fid
    print("P1 fidelity:", {k: v for k, v in fid.items() if k not in ("only_a", "only_b")}, flush=True)

    # ETH
    eth_tables = L.tables_from_panels("ETH")
    eth, trig_eth, eth_net = run_asset(eth_tables, ex.path_for("ETH", "1m"), "ETH_panel")
    out["BTC_prod"], out["BTC_panel"], out["ETH"] = btc_prod, btc_panel, eth
    out["decision"] = L.decide(eth["net"], eth["boot_net"], fid)
    M.write_json(RESULTS / "report.json", out)
    print("\nDECISION:", json.dumps(out["decision"], indent=1), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["freeze0", "outcomes"])
    args = ap.parse_args()
    {"freeze0": freeze0, "outcomes": outcomes}[args.stage]()

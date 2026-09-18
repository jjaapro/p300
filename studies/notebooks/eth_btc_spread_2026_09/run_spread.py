"""ETH/BTC regime spread: freeze, then the one outcome run (README.md is the pre-registration).

    python studies/notebooks/eth_btc_spread_2026_09/run_spread.py freeze0
    python studies/notebooks/eth_btc_spread_2026_09/run_spread.py outcomes
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
import spread_lib as L  # noqa: E402
import micro_lib as M  # noqa: E402

RESULTS = L.RESULTS
FROZEN = ["README.md", "spread_lib.py", "run_spread.py", "tests/test_spread.py"]
DATA_FILES = {"funding_BTCUSDT": L.FUNDING_CACHE / "binance_funding_BTCUSDT.json",
              "funding_ETHUSDT": L.FUNDING_CACHE / "binance_funding_ETHUSDT.json",
              "stage1_trades": L.STAGE1_TRADES,
              "perp_BTC": L.ROOT / "studies/notebooks/orb_study/cache/BTCUSDT_perp_1m.npz",
              "perp_ETH": L.ROOT / "studies/notebooks/orb_study/cache/ETHUSDT_perp_1m.npz",
              "classifier": L.ROOT / "strategies/support/regime_jplus.py"}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def freeze0() -> None:
    target = RESULTS / "freeze_F0.json"
    if target.exists():
        raise SystemExit("freeze_F0.json exists; a freeze is never overwritten")
    RESULTS.mkdir(parents=True, exist_ok=True)
    M.write_json(target, {"freeze": "F0", "created_utc": now_utc(),
                          "note": "Before any number. The README's rules, bars and placebo are fixed here.",
                          "files": {f: M.sha256(HERE / f) for f in FROZEN},
                          "data": {k: M.sha256(p) for k, p in DATA_FILES.items()}})
    print("frozen:", target)


def part_a() -> dict:
    eth, btc = L.load_daily("ETH"), L.load_daily("BTC")
    common = sorted(set(eth.index) & set(btc.index))
    eth, btc = eth.loc[common], btc.loc[common]
    days = list(common)
    ls_d = L.load_ls_ratio()
    modes = L.classify(days, [float(v) for v in btc["close"]], ls_d)
    years = len(days) / L.DAYS_PER_YEAR
    f_btc, f_eth = L.load_funding("BTCUSDT"), L.load_funding("ETHUSDT")
    dist = pd.Series([modes.get(d, "n/a") for d in days]).value_counts().to_dict()
    out = {"span": [days[0], days[-1]], "days": len(days), "years": years, "regime_days": dist,
           "ls_first_day": min(ls_d) if ls_d else None, "arms": {}}

    arms = {"spread_strong_bull": (("strong_bull",), (1.0, -1.0), L.PAIR_COST),
            "spread_mild_bull": (("mild_bull",), (1.0, -1.0), L.PAIR_COST),
            "spread_all_bull": (("strong_bull", "mild_bull"), (1.0, -1.0), L.PAIR_COST),
            "long_eth_strong_bull": (("strong_bull",), (1.0, 0.0), L.LEG_RT_COST),
            "long_btc_strong_bull": (("strong_bull",), (0.0, 1.0), L.LEG_RT_COST)}
    for name, (target, legs, cost) in arms.items():
        eps = L.episodes_from_modes(days, modes, target)
        ep, daily = L.run_episodes(days, eth, btc, eps, f_btc, f_eth, legs=legs, cost=cost)
        rec = L.summarize(ep, daily, years)
        rec["per_year"] = L.per_year(ep)
        rec["halves_pct"] = L.halves(ep)
        rec["split_2023_06_09_pct"] = L.split_at(ep)
        rec["bootstrap"] = L.bootstrap_net_ann(ep, years)
        if name == "spread_strong_bull":
            rec["placebo"] = L.placebo_test(days, eth, btc, eps, f_btc, f_eth, float(ep["net"].sum()))
            ep.to_csv(RESULTS / "episodes_strong_bull.csv", index=False)
            daily.rename("daily_net").to_csv(RESULTS / "daily_strong_bull.csv")
        out["arms"][name] = rec
        print(f"{name:24s} episodes {rec['episodes']:3d} held {rec['days_held']:4d}d net {rec['net_total_pct']:+7.2f}% "
              f"({rec['net_ann_pct']:+6.2f}%/yr) dd {rec['max_dd_pct']:5.2f}% MAR {rec['mar']} DSR {rec['dsr']}", flush=True)

    c = out["arms"]["spread_strong_bull"]
    ci = c["bootstrap"]["ci90_ann_pct"]
    a = bool(c["net_ann_pct"] >= L.MIN_NET_ANN_PCT and ci[0] is not None and ci[0] > 0)
    h = c["halves_pct"]
    b = bool(h["first"] is not None and h["first"] > 0 and h["second"] > 0)
    cc = bool(c["dsr"] is not None and c["dsr"] >= L.DSR_BAR)
    d = bool(c["placebo"]["p_placebo"] < L.PLACEBO_ALPHA)
    m_eth = out["arms"]["long_eth_strong_bull"]["mar"]
    e = bool(c["mar"] is not None and (m_eth is None or c["mar"] >= m_eth))
    failing = [k for k, v in (("a_net_and_ci", a), ("b_both_halves", b), ("c_dsr", cc), ("d_placebo", d),
                              ("e_mar_vs_long_eth", e)) if not v]
    out["decision"] = {"a_net_and_ci": a, "b_both_halves": b, "c_dsr": cc, "d_placebo": d, "e_mar_vs_long_eth": e,
                       "failing": failing, "verdict": "BUILD — propose a paper sleeve" if not failing else "KILL"}
    return out


def part_b() -> dict:
    trades = L.load_chento_trades()
    closes = {a: L.load_panel_close(a)[0] for a in ("BTC", "ETH")}
    h = L.hedge_trades(trades, closes)
    h.to_csv(RESULTS / "chento_hedged.csv.gz", index=False)
    summary = L.hedge_summary(h)
    decision = L.hedge_decision(summary)
    for asset, rec in summary.items():
        print(f"chento {asset}: unhedged mean {rec['unhedged']['mean_R']:+.3f} R MAR {rec['unhedged']['mar']} | "
              f"hedged mean {rec['hedged']['mean_R']:+.3f} R MAR {rec['hedged']['mar']} | paired {rec['paired_diff']['mean']:+.3f} "
              f"CI95 {rec['paired_diff']['ci95']} | beta {rec.get('beta_of_R_on_market_move')}", flush=True)
    return {"trades": int(len(h)), "summary": summary, "decision": decision}


def outcomes() -> None:
    f0 = json.loads((RESULTS / "freeze_F0.json").read_text())
    changed = [f for f in FROZEN if M.sha256(HERE / f) != f0["files"][f]] + \
        [k for k, p in DATA_FILES.items() if M.sha256(p) != f0["data"][k]]
    if changed:
        raise SystemExit(f"changed since F0: {changed}")
    if (RESULTS / "report.json").exists():
        raise SystemExit("report.json exists; the outcome run already happened")
    out = {"created_utc": now_utc(), "f0_created_utc": f0["created_utc"], "part_a": part_a(), "part_b": part_b()}
    M.write_json(RESULTS / "report.json", out)
    print("\nPart A decision:", json.dumps(out["part_a"]["decision"], indent=1))
    print("Part B decision:", json.dumps(out["part_b"]["decision"], indent=1))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["freeze0", "outcomes"])
    args = ap.parse_args()
    {"freeze0": freeze0, "outcomes": outcomes}[args.stage]()

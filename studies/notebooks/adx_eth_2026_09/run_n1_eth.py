"""N1 — the shipped ADX Tier-2 machine on ETH, unchanged, priced with live semantics (README)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in (str(ROOT), str(ROOT / "studies" / "notebooks" / "adx_robustness_2026_09")):
    if p not in sys.path:
        sys.path.insert(0, p)
import adx_lib as al  # noqa: E402

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")

ex, bv = al.ex, al.bv
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)


def eth_candles(phase: int) -> list[dict]:
    return bv.df_to_candles(bv.daily_from_1m_sql("eth_1m", phase))


def main() -> None:
    path = ex.load_path("eth_1m")
    fund = bv.fetch_binance_funding("ETHUSDT")
    out = {"env": ex.env_info(), "n_trials": 1, "funding_prints": len(fund)}
    rows, series = [], {}
    for ph in range(24):
        tr = al.live_walk(eth_candles(ph), path, fund_rows=fund)
        r = al.mtm_returns(tr, path)
        series[ph] = r
        st = al.curve_stats(r); ls = al.ledger_stats(tr)
        net = np.array([t["net_pct"] for t in tr])
        t_stat = float(net.mean() / net.std(ddof=1) * np.sqrt(len(net))) if len(net) > 2 else float("nan")
        rows.append(dict(phase_hour=ph, n=ls["n"], mean_net_pct=ls["mean_net_pct"], win=ls["win"], worst_pct=ls["worst_pct"], t_trade=t_stat, **st))
        print(f"phase {ph:2d}: n={ls['n']} mean {ls['mean_net_pct']:+.2f}% t {t_stat:.2f} CAGR {st['cagr_pct']:.1f}% MTM maxDD {st['mtm_maxdd_pct']:.1f}% Sharpe {st['sharpe']:.2f} MAR {st['mar']:.2f}")
        if ph == 0:
            led0 = tr
    ph_df = pd.DataFrame(rows)
    ph_df.to_csv(RESULTS / "n1_phases.csv", index=False)
    pd.DataFrame(led0).to_csv(RESULTS / "n1_eth_ledger_phase0.csv", index=False)
    out["phases"] = ph_df.to_dict("records")
    out["phase_ranges"] = {k: [float(ph_df[k].min()), float(ph_df[k].median()), float(ph_df[k].max())] for k in ("cagr_pct", "mtm_maxdd_pct", "sharpe", "mar", "n", "t_trade")}
    # live phase detail
    p0 = ph_df[ph_df.phase_hour == 0].iloc[0]
    net0 = np.array([t["net_pct"] for t in led0]); ets = np.array([t["entry_ts"] for t in led0])
    h2 = ex.halves(ets)
    longs = [t for t in led0 if t["dir"] == "long"]; shorts = [t for t in led0 if t["dir"] == "short"]
    out["live_phase"] = dict(n=int(p0.n), mean_net_pct=float(p0.mean_net_pct), t_trade=float(p0.t_trade), sharpe=float(p0.sharpe),
                             mtm_maxdd_pct=float(p0.mtm_maxdd_pct), cagr_pct=float(p0.cagr_pct), mar=float(p0.mar),
                             first_half_mean=float(net0[~h2].mean()), second_half_mean=float(net0[h2].mean()),
                             longs=dict(n=len(longs), mean_net_pct=float(np.mean([t["net_pct"] for t in longs])) if longs else float("nan")),
                             shorts=dict(n=len(shorts), mean_net_pct=float(np.mean([t["net_pct"] for t in shorts])) if shorts else float("nan")),
                             funding_pct_sum=float(sum(t["funding_pct"] for t in led0)),
                             span=[led0[0]["entry_dt"], led0[-1]["exit_dt"]] if led0 else None)
    d = bv.dsr(series[0].to_numpy(), 1)
    out["live_phase"]["dsr_n1"] = d["dsr"] if d else None
    # correlation with the BTC machine (phase 0, robustness pack) and the equal-weight pair
    btc = pd.read_csv(ROOT / "studies" / "notebooks" / "adx_robustness_2026_09" / "results" / "p1a_live_daily_returns.csv", index_col=0)["ret"]
    eth = series[0]
    idx = sorted(set(btc.index) | set(eth.index))
    B = btc.reindex(idx).fillna(0.0); E = eth.reindex(idx).fillna(0.0)
    both = (B != 0) | (E != 0)
    corr = float(np.corrcoef(B[both], E[both])[0, 1])
    pair = (B + E) / 2
    out["btc_eth"] = dict(corr_daily_returns=corr, corr_days=int(both.sum()),
                          btc_curve=al.curve_stats(B), eth_curve=al.curve_stats(E), pair_curve=al.curve_stats(pair),
                          share_days_both_in_market=float(((B != 0) & (E != 0)).mean()))
    print("live phase:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in out["live_phase"].items()})
    print("BTC/ETH:", out["btc_eth"])
    # decision
    lp = out["live_phase"]
    clauses = dict(sharpe_ge_0p7=bool(lp["sharpe"] >= 0.7), sharpe_ge_0p5_in_80pct_phases=bool((ph_df.sharpe >= 0.5).mean() >= 0.8),
                   maxdd_ge_m55=bool(lp["mtm_maxdd_pct"] >= -55.0), t_trade_ge_1p5=bool(lp["t_trade"] >= 1.5), corr_lt_0p7=bool(corr < 0.7))
    out["decision"] = dict(**clauses, share_phases_sharpe_ge_0p5=float((ph_df.sharpe >= 0.5).mean()),
                           verdict="BUILD-CANDIDATE" if all(clauses.values()) else "KILL")
    print("DECISION:", out["decision"])
    al.jdump.__globals__["RESULTS"]  # noqa: B018 (keep adx_lib import used)
    import json
    (RESULTS / "n1_eth.json").write_text(json.dumps(bv._jsonable(out), indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()

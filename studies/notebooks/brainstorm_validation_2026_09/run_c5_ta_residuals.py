"""C5 — the two residual classic-TA cells on unseen assets.  README §C5."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import bv_lib as bv

SQZ_H = (4, 12, 24)
RSI_H = (1, 4, 12, 24)
EXPECT_SQZ = {4: (142, 240.9, 4.77), 12: (90, 526.4, 4.77), 24: (46, 1008.4, 4.19)}   # n, bp, cluster-robust t
EXPECT_RSI_T = 3.78
N_SWEEP = 7908


def sqz(df: pd.DataFrame, H: int, min_n: int = 30):
    c = df["close"].to_numpy(float)
    fire_dn, _ = bv.bb_squeeze_signal(c, 0.20)
    return bv.event_study(fire_dn, -np.ones(len(c)), c, H, min_n=min_n, warm=130, sigma_mode="fwd")


def rsi_cell(df: pd.DataFrame, H: int, n: int = 14, lvl: float = 75.0, min_n: int = 25):
    c = df["close"].to_numpy(float)
    ev = bv.rsi_enter_ob(c, n, lvl)
    return bv.event_study(ev, np.ones(len(c)), c, H, min_n=min_n, warm=60, sigma_mode="fwd")


def pooled(res: dict[str, tuple]) -> dict:
    adj, wk, per = [], [], []
    for a, (r, ts) in res.items():
        if r is None:
            continue
        adj.append(r["adj"])
        wk.append(ts[r["idx"]] // (7 * 86400))
        per.append(dict(asset=a, n=r["n"], mean_bp=r["mean_bp"], t=r["t"]))
    if not adj:
        return dict(n=0, n_assets=0)
    x = np.concatenate(adj)
    wk = np.concatenate(wk)
    sd = x.std(ddof=1)
    t_wk, g = bv.cluster_t(x, wk)
    pa = pd.DataFrame(per)
    return dict(n=int(len(x)), n_assets=int(len(pa)), mean_bp=float(x.mean()) * 1e4,
                t_naive=float(x.mean() / (sd / math.sqrt(len(x)))) if sd > 0 else 0.0, t_week_cluster=t_wk, g_weeks=g,
                share_assets_positive=float((pa.mean_bp > 0).mean()), win=float((x > 0).mean() * 100))


def main() -> None:
    out = {"env": bv.env_info(), "n_trials": dict(bitstamp_selected_from=N_SWEEP, unseen=1)}
    bs, name = bv.bitstamp_pickle(86400)
    bsd = bv.bars_to_df(bs)
    # run_oscillators.load_daily() truncates Bitstamp to >= 2014-01-01 ("longest clean span");
    # run_bands_vol.load_bitstamp_daily() uses the full 2011-08 -> series.  Mirror both.
    bsd_2014 = bsd[bsd["dt"] >= "2014-01-01"].reset_index(drop=True)
    p1 = bv.load_daily_btc()
    eth = bv.daily_from_1m_sql("eth_1m")
    alts = bv.load_screener_daily()
    panels = {"bitstamp": bsd, "p300_btc": p1, "eth": eth}
    panels_rsi = {"bitstamp": bsd_2014, "p300_btc": p1, "eth": eth}
    out["panels"] = dict(bitstamp_squeeze=[bsd["dt"].iloc[0], bsd["dt"].iloc[-1]],
                         bitstamp_rsi=[bsd_2014["dt"].iloc[0], bsd_2014["dt"].iloc[-1]], pickle=name)
    rows = []

    # ── squeeze released downward, short ───────────────────────────────────
    for H in SQZ_H:
        for pn, df in panels.items():
            r = sqz(df, H, min_n=(30 if pn == "bitstamp" else 10))
            s = bv.summary(r, ("n", "mean_bp", "t", "tc", "g", "win", "capture")) or dict(n=0)
            rows.append(dict(cell="BBsqz20_fire_dn_short", H=H, panel=pn, **s))
            if pn == "bitstamp" and r is not None:
                rows[-1]["dsr_N7908"] = bv.dsr(r["adj"], N_SWEEP)["dsr"]
                rows[-1]["expected"] = str(EXPECT_SQZ[H])
        pool = pooled({a: (sqz(g, H, min_n=3), g["ts"].to_numpy(np.int64)) for a, g in alts.items()})
        rows.append(dict(cell="BBsqz20_fire_dn_short", H=H, panel="alts_pooled", n=pool["n"], mean_bp=pool.get("mean_bp"),
                         t=pool.get("t_naive"), tc=pool.get("t_week_cluster"), g=pool.get("g_weeks"), win=pool.get("win"),
                         share_assets_positive=pool.get("share_assets_positive"), n_assets=pool["n_assets"]))

    # ── RSI(14) crossing up through 75, long (+ plateau cells) ─────────────
    for H in RSI_H:
        for pn, df in panels_rsi.items():
            r = rsi_cell(df, H, min_n=(25 if pn == "bitstamp" else 10))
            s = bv.summary(r, ("n", "mean_bp", "t", "tc", "g", "win", "capture")) or dict(n=0)
            rows.append(dict(cell="RSI14_enter_OB75_long", H=H, panel=pn, **s))
            if pn == "bitstamp" and r is not None:
                rows[-1]["dsr_N7908"] = bv.dsr(r["adj"], N_SWEEP)["dsr"]
        pool = pooled({a: (rsi_cell(g, H, min_n=3), g["ts"].to_numpy(np.int64)) for a, g in alts.items()})
        rows.append(dict(cell="RSI14_enter_OB75_long", H=H, panel="alts_pooled", n=pool["n"], mean_bp=pool.get("mean_bp"),
                         t=pool.get("t_naive"), tc=pool.get("t_week_cluster"), g=pool.get("g_weeks"), win=pool.get("win"),
                         share_assets_positive=pool.get("share_assets_positive"), n_assets=pool["n_assets"]))
    plateau = []
    for n in (7, 14, 21):
        for lvl in (65, 70, 75):
            for H in (4, 24):
                for pn, df in (("bitstamp", bsd_2014), ("eth", eth)):
                    r = rsi_cell(df, H, n, lvl, min_n=10)
                    s = bv.summary(r, ("n", "mean_bp", "t", "tc")) or dict(n=0)
                    plateau.append(dict(rsi_len=n, lvl=lvl, H=H, panel=pn, **s))
                pool = pooled({a: (rsi_cell(g, H, n, lvl, min_n=3), g["ts"].to_numpy(np.int64)) for a, g in alts.items()})
                plateau.append(dict(rsi_len=n, lvl=lvl, H=H, panel="alts_pooled", n=pool["n"], mean_bp=pool.get("mean_bp"),
                                    t=pool.get("t_naive"), tc=pool.get("t_week_cluster")))
    # the squeeze H=24 row is quoted with n=46 / +1008 bp; neither Bitstamp nor the Binance caches give
    # that n -- record what each panel gives so the parity gap is visible
    sq24 = {}
    for nm in ("spot_BTCUSDT_1d_3200d.pkl", "spot_BTCUSDT_1d_3300d.pkl"):
        d = bv.bars_to_df(bv.scalp_pickle(nm))
        sq24[nm] = bv.summary(sqz(d, 24, min_n=10), ("n", "mean_bp", "t", "tc"))
    sq24["bitstamp_full"] = bv.summary(sqz(bsd, 24, min_n=10), ("n", "mean_bp", "t", "tc"))
    sq24["bitstamp_2014"] = bv.summary(sqz(bsd_2014, 24, min_n=10), ("n", "mean_bp", "t", "tc"))
    out["squeeze_H24_parity_search"] = sq24
    print("squeeze H=24 on every candidate panel (quoted: n=46, +1008.4 bp, cluster t 4.19):", sq24)
    cells = pd.DataFrame(rows)
    plat = pd.DataFrame(plateau)
    cells.to_csv(bv.RESULTS / "c5_cells.csv", index=False)
    plat.to_csv(bv.RESULTS / "c5_rsi_plateau.csv", index=False)
    print("cells (t = naive, tc = episode/week-clustered):")
    print(cells.round(2).to_string(index=False))
    print("\nRSI plateau (unseen panels):")
    print(plat.pivot_table(index=["rsi_len", "lvl", "H"], columns="panel", values="t").round(2).to_string())

    # ── decision per cell (primary horizon: the best Bitstamp H by t) ──────
    dec = {}
    for cell in ("BBsqz20_fire_dn_short", "RSI14_enter_OB75_long"):
        sub = cells[cells.cell == cell]
        bits = sub[sub.panel == "bitstamp"].dropna(subset=["t"])
        Hbest = int(bits.loc[bits.t.idxmax(), "H"]) if len(bits) else None
        e = sub[(sub.panel == "eth") & (sub.H == Hbest)]
        a = sub[(sub.panel == "alts_pooled") & (sub.H == Hbest)]
        eth_ok = bool(len(e) and e.iloc[0]["n"] > 0 and e.iloc[0]["mean_bp"] > 0 and e.iloc[0]["t"] >= 2.0)
        alt_ok = bool(len(a) and a.iloc[0]["n"] > 0 and a.iloc[0]["mean_bp"] > 0 and a.iloc[0]["tc"] >= 2.0
                      and a.iloc[0]["share_assets_positive"] >= 0.6)
        dec[cell] = dict(bitstamp_best_H=Hbest, bitstamp=bits[bits.H == Hbest].iloc[0].to_dict() if Hbest else None,
                         eth=e.iloc[0].to_dict() if len(e) else None, alts=a.iloc[0].to_dict() if len(a) else None,
                         eth_pass=eth_ok, alts_pass=alt_ok, verdict="KEEP-FOR-STUDY" if (eth_ok and alt_ok) else "KILL")
        print(f"\nDECISION {cell}: best Bitstamp H={Hbest} | ETH pass {eth_ok} | alts pass {alt_ok} -> {dec[cell]['verdict']}")
    out["cells"] = rows
    out["decision"] = dec
    bv.jdump(out, "c5_summary.json")


if __name__ == "__main__":
    main()

"""C1 — big-bar daily momentum: parity, independent data, unseen eras/assets,
deflation, and the "beats buy-and-hold" sizing claim.  See README §C1.
"""
from __future__ import annotations

import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import bv_lib as bv

W, Z, H = 60, 1.0, 7
CLAIM = dict(n=189, mean_bp=193.5, t=3.04, tc=3.99, win=55.6, capture=21.4)
N_TRIALS = {"N1": 1, "N24_control_family": 24, "N96_run2": 96, "N348_study": 348}
GRID_Z, GRID_H, GRID_W = (0.5, 1.0, 1.5, 2.0), (3, 5, 7, 10, 14), (30, 60, 120)


def cell(df: pd.DataFrame, W=W, z=Z, H=H, drift="full", min_n=20):
    mask, d = bv.mom_signal(df["open"], df["high"], df["low"], df["close"], W, z)
    return bv.event_study(mask, d, df["close"].to_numpy(), H, min_n=min_n, drift=drift)


def window(df: pd.DataFrame, a: str | None, b: str | None) -> pd.DataFrame:
    m = np.ones(len(df), bool)
    if a:
        m &= df["dt"].to_numpy() >= a
    if b:
        m &= df["dt"].to_numpy() < b
    return df[m].reset_index(drop=True)


def pooled(results: dict[str, dict]) -> dict:
    """Pool per-asset result dicts: naive t, week-of-entry-clustered t, asset-clustered t."""
    adj, wk, asset = [], [], []
    per_asset = []
    for a, (r, ts) in results.items():
        if r is None:
            continue
        adj.append(r["adj"])
        wk.append(ts[r["idx"]] // (7 * 86400))
        asset.append(np.full(r["n"], a))
        per_asset.append(dict(asset=a, n=r["n"], mean_bp=r["mean_bp"], t=r["t"], win=r["win"]))
    if not adj:
        return dict(n=0), pd.DataFrame()
    x = np.concatenate(adj)
    wk = np.concatenate(wk)
    asset = np.concatenate(asset)
    sd = x.std(ddof=1)
    t_naive = x.mean() / (sd / math.sqrt(len(x))) if sd > 0 else 0.0
    t_wk, g_wk = bv.cluster_t(x, wk)
    t_as, g_as = bv.cluster_t(x, asset)
    pa = pd.DataFrame(per_asset)
    return dict(n=int(len(x)), n_assets=int(len(pa)), mean_bp=float(x.mean()) * 1e4, t_naive=float(t_naive),
                t_week_cluster=t_wk, g_weeks=g_wk, t_asset_cluster=t_as, g_assets=g_as,
                share_assets_positive=float((pa["mean_bp"] > 0).mean()), win=float((x > 0).mean() * 100)), pa


def main() -> None:
    out = {"env": bv.env_info(), "rule": dict(W=W, z=Z, H=H, direction="sign(close-open)", claim=CLAIM),
           "n_trials": N_TRIALS}

    # ── 1. parity on their cache ────────────────────────────────────────────
    b1 = bv.bars_to_df(bv.scalp_pickle("spot_BTCUSDT_1d_3200d.pkl"))
    par = cell(b1)
    ok = par is not None and par["n"] == CLAIM["n"] and abs(par["mean_bp"] - CLAIM["mean_bp"]) < 1.0 \
        and abs(par["t"] - CLAIM["t"]) < 0.02
    out["parity"] = dict(data="spot_BTCUSDT_1d_3200d.pkl", span=[b1["dt"].iloc[0], b1["dt"].iloc[-1]],
                         got=bv.summary(par), expected=CLAIM, pass_=bool(ok))
    print("1. PARITY (their cache):", bv.summary(par), "| expected", CLAIM, "| PASS" if ok else "| FAIL")

    # ── 2. p300 daily (independent table) ──────────────────────────────────
    p1 = bv.load_daily_btc()
    r_p = cell(p1)
    ts_p = p1["ts"].to_numpy(np.int64)
    out["p300_btc"] = dict(span=[p1["dt"].iloc[0], p1["dt"].iloc[-1]], n_bars=len(p1), primary=bv.summary(r_p),
                           per_year=bv.per_year(r_p, ts_p).to_dict("records"), era=bv.era_split(r_p, ts_p),
                           drift_trailing=bv.summary(cell(p1, drift="trailing")),
                           drift_none=bv.summary(cell(p1, drift="none")))
    # truncation (lookahead) test: the kept entries on a prefix must be a prefix of the full run
    pre = cell(p1.iloc[:2000].reset_index(drop=True))
    full_idx = set(int(i) for i in r_p["idx"] if i < 2000 - H)
    pre_idx = set(int(i) for i in pre["idx"] if i < 2000 - H)
    out["p300_btc"]["truncation_test_pass"] = bool(full_idx == pre_idx)
    print("2. p300 BTC daily:", bv.summary(r_p), "| truncation", out["p300_btc"]["truncation_test_pass"])
    print("   per year:\n", bv.per_year(r_p, ts_p).to_string(index=False))
    print("   era:", out["p300_btc"]["era"], "\n   trailing-drift:", out["p300_btc"]["drift_trailing"],
          "\n   raw:", out["p300_btc"]["drift_none"])

    # ── 3. unseen era: Bitstamp 2012-01-01 .. 2017-08-16 ───────────────────
    bs, name = bv.bitstamp_pickle(86400)
    bsd = bv.bars_to_df(bs)
    oos_time = window(bsd, "2012-01-01", "2017-08-17")
    r_bs = cell(oos_time)
    eras = {}
    for a, b in (("2012-01-01", "2014-01-01"), ("2014-01-01", "2017-01-01"), ("2017-01-01", "2020-01-01"),
                 ("2020-01-01", "2023-01-01"), ("2023-01-01", "2027-01-01")):
        eras[f"{a[:4]}-{b[:4]}"] = bv.summary(cell(window(bsd, a, b), min_n=10))
    out["bitstamp"] = dict(pickle=name, oos_window=["2012-01-01", "2017-08-16"], oos=bv.summary(r_bs),
                           full=bv.summary(cell(bsd)), eras=eras)
    print("3. Bitstamp OOS-in-time 2012-2017:", bv.summary(r_bs))
    print("   Bitstamp eras:", eras)

    # ── 4. unseen assets: ETH, ETH perp, alt panel ─────────────────────────
    eth = bv.daily_from_1m_sql("eth_1m")
    r_eth = cell(eth)
    ethp = bv.daily_from_intraday(bv.load_15m("cd_futures_eth_15m"), 0, 900, min_rows=90)
    r_ethp = cell(ethp)
    alts = bv.load_screener_daily()
    alt_res = {a: (cell(g, min_n=5), g["ts"].to_numpy(np.int64)) for a, g in alts.items()}
    pool, pa = pooled(alt_res)
    out["eth"] = dict(spot_1m=bv.summary(r_eth), span=[eth["dt"].iloc[0], eth["dt"].iloc[-1]],
                      per_year=bv.per_year(r_eth, eth["ts"].to_numpy(np.int64)).to_dict("records"),
                      perp_15m=bv.summary(r_ethp))
    out["alts"] = pool
    pa.to_csv(bv.RESULTS / "c1_alts.csv", index=False)
    print("4. ETH spot daily:", bv.summary(r_eth), "\n   ETH perp daily:", bv.summary(r_ethp))
    print("   alt panel pooled:", pool)

    # ── 5. plateau grid on p300 BTC ────────────────────────────────────────
    rows = []
    for z in GRID_Z:
        for h in GRID_H:
            for w in GRID_W:
                r = cell(p1, W=w, z=z, H=h)
                rows.append(dict(z=z, H=h, W=w, **(bv.summary(r, ("n", "mean_bp", "t", "tc", "win", "capture")) or dict(n=0))))
    grid = pd.DataFrame(rows)
    grid.to_csv(bv.RESULTS / "c1_grid.csv", index=False)
    nbr = grid[(grid["z"] == Z) & (grid["W"] == W)]
    out["grid"] = dict(size=len(grid), share_t_gt_2=float((grid["t"] > 2).mean()), share_positive=float((grid["mean_bp"] > 0).mean()),
                       max_t=float(grid["t"].max()), neighbours_z1_W60=nbr[["H", "n", "mean_bp", "t", "tc"]].to_dict("records"),
                       rank_of_primary_by_t=int((grid["t"] > float(grid[(grid.z == Z) & (grid.H == H) & (grid.W == W)]["t"].iloc[0])).sum()) + 1)
    print("5. grid: %d cells, share t>2 = %.2f, share positive = %.2f, primary rank by t = %d/%d"
          % (len(grid), out["grid"]["share_t_gt_2"], out["grid"]["share_positive"], out["grid"]["rank_of_primary_by_t"], len(grid)))
    print(grid.pivot_table(index=["z", "W"], columns="H", values="t").round(2).to_string())

    # ── 6. deflation + bootstrap on the p300 primary series ────────────────
    out["dsr"] = {k: bv.dsr(r_p["adj"], n) for k, n in N_TRIALS.items()}
    out["boot"] = dict(iid=bv.boot_mean_ci(r_p["adj"], block=1), block3=bv.boot_mean_ci(r_p["adj"], block=3))
    print("6. DSR:", {k: round(v["dsr"], 3) for k, v in out["dsr"].items()},
          "| bootstrap mean bp CI90 %s" % [round(x * 1e4, 1) for x in out["boot"]["iid"]["ci"]])

    # ── 7. sizing claim: port of run_leverage_sim2 ─────────────────────────
    sims = {}
    for label, df in (("their_cache", b1), ("p300", p1)):
        c = df["close"].to_numpy(float)
        mask, d = bv.mom_signal(df["open"], df["high"], df["low"], df["close"], W, Z)
        yrs = len(c) / 365.25
        bh_daily = np.diff(c) / c[:-1]
        bh = dict(final=10_000 * c[-1] / c[0], cagr=(c[-1] / c[0]) ** (1 / yrs) - 1,
                  maxdd=float((c / np.maximum.accumulate(c) - 1).min() * 100))
        block = {}
        for frac, fch, fm in ((1.0, 1.0, "maker"), (1.0, 1.0, "taker"), (0.5, 1.0, "maker"),
                              (1.0, 3.0, "maker"), (1.0, 3.0, "taker")):
            r = bv.simulate_cross_margin(c, mask, d, H, fch, fm, frac)
            curve = r["curve"][np.isfinite(r["curve"])]
            sr = np.diff(curve) / np.maximum(curve[:-1], 1e-9)
            k = f"frac{frac:.1f}_{fm}_fund{fch:.0f}x"
            block[k] = dict(final=r["final"], cagr=(r["final"] / 10_000) ** (1 / yrs) - 1 if r["final"] > 0 else -1,
                            maxdd=r["maxdd"], trades=r["trades"], liq=r["liq"], mean_gross=r["mean_gross"],
                            mean_gross_when_in=r["mean_gross_when_in"], max_gross=r["max_gross"],
                            max_conc=r["max_conc"], time_in_market=r["time_in_market"],
                            sharpe=bv.sharpe_of(sr))
            if frac == 1.0 and fm == "maker" and fch == 1.0:
                # exposure-matched buy-and-hold and paired bootstrap of the CAGR / maxDD gap
                E = r["mean_gross"]
                bh_matched = bh_daily * E
                block[k]["bh_exposure_matched"] = dict(exposure=E, **bv.curve_stats(bh_matched, 365.25))
                block[k]["strategy_curve_stats"] = bv.curve_stats(sr, 365.25)
                block[k]["boot_cagr_vs_bh_1x"] = bv.paired_block_boot(sr, bh_daily, lambda x: bv.cagr_of(x, 365.25), block=30)
                block[k]["boot_maxdd_vs_bh_1x"] = bv.paired_block_boot(sr, bh_daily, bv.maxdd_of, block=30)
                block[k]["boot_cagr_vs_bh_matched"] = bv.paired_block_boot(sr, bh_matched, lambda x: bv.cagr_of(x, 365.25), block=30)
                # random-direction, frequency-matched control (50 seeds)
                finals = []
                for seed in range(50):
                    rng = np.random.default_rng(seed)
                    mr = rng.random(len(c)) < mask.mean()
                    dr = rng.choice([-1.0, 1.0], len(c))
                    finals.append(bv.simulate_cross_margin(c, mr, dr, H, fch, fm, frac)["final"])
                finals = np.array(finals)
                block[k]["random_control"] = dict(median_final=float(np.median(finals)), p90_final=float(np.quantile(finals, 0.9)),
                                                  share_above_strategy=float((finals >= r["final"]).mean()),
                                                  share_above_bh=float((finals >= bh["final"]).mean()))
                if label == "p300":
                    fig, ax = plt.subplots(figsize=(9, 4.5))
                    x = pd.to_datetime(df["ts"], unit="s", utc=True)
                    ax.plot(x[:len(curve)], curve / 10_000, label="MOM 100%/entry, <=3 conc, maker (their sizing)")
                    ax.plot(x, c / c[0], label="buy & hold 1x")
                    ax.plot(x[1:], np.cumprod(1 + bh_matched), label=f"buy & hold at {E:.2f}x (exposure-matched)")
                    ax.set_yscale("log")
                    ax.set_title("C1 sizing claim on p300 BTC daily")
                    ax.legend(fontsize=8)
                    fig.tight_layout()
                    fig.savefig(bv.RESULTS / "c1_equity.png", dpi=110)
                    plt.close(fig)
        sims[label] = dict(years=yrs, buy_and_hold=bh, runs=block)
        print(f"7. sizing [{label}] B&H final {bh['final']:,.0f} CAGR {bh['cagr']*100:.1f}% maxDD {bh['maxdd']:.1f}%")
        for k, v in block.items():
            print(f"   {k:28s} final {v['final']:>10,.0f} CAGR {v['cagr']*100:6.1f}% maxDD {v['maxdd']:6.1f}% "
                  f"trades {v['trades']} gross mean {v['mean_gross']:.2f}x (in-mkt {v['mean_gross_when_in']:.2f}x, "
                  f"max {v['max_gross']:.2f}x) TiM {v['time_in_market']:.2f}")
    out["sizing"] = dict(expected=dict(frac1_maker=dict(final=50_499, cagr=0.203, maxdd=-63.0),
                                       bh=dict(final=48_960, cagr=0.199, maxdd=-83.2)), sims=sims)

    # ── 8. decision ────────────────────────────────────────────────────────
    unseen = {"bitstamp_2012_2017": r_bs, "eth_spot": r_eth}
    unseen_pass = {k: bool(v is not None and v["mean_bp"] > 0 and v["t"] >= 2.0) for k, v in unseen.items()}
    unseen_pass["alts_week_clustered"] = bool(pool.get("n", 0) > 0 and pool["mean_bp"] > 0 and pool["t_week_cluster"] >= 2.0)
    n_pass = sum(unseen_pass.values())
    dsr348 = out["dsr"]["N348_study"]["dsr"] if out["dsr"]["N348_study"] else float("nan")
    a = out["parity"]["pass_"]
    b = n_pass >= 2
    cc = dsr348 >= 0.95
    beats = sims["p300"]["runs"]["frac1.0_maker_fund1x"]["boot_cagr_vs_bh_matched"]["ci"][0] > 0
    if not b:
        verdict = "KILL"
    elif a and b and cc:
        verdict = "CONFIRMED-SIGNAL" + (" + BEATS-HOLD" if beats else "")
    else:
        verdict = "INCONCLUSIVE"
    out["decision"] = dict(parity=a, unseen=unseen_pass, n_unseen_pass=n_pass, dsr348=dsr348, dsr_pass=cc,
                           beats_hold_exposure_matched_ci_above_0=bool(beats), verdict=verdict)
    print("8. DECISION:", out["decision"])
    bv.jdump(out, "c1_summary.json")
    bv.per_year(r_p, ts_p).to_csv(bv.RESULTS / "c1_per_year.csv", index=False)


if __name__ == "__main__":
    main()

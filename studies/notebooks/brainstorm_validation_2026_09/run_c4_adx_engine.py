"""C4 — claims about the ADX regime machine: parity of the S-005 engine claims,
bar-phase noise floor on p300's live machine, stop-fill semantics, short leg.
README §C4.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import bv_lib as bv

START, END = "2014-01-01", "2026-08-10"
STUDY = dict(norm_w=252, stop_mode="wick_close_fill", stop_anchor="regime")
LIVE = dict(norm_w=252, stop_mode="intrabar", stop_anchor="regime")
EXPECTED = {"study": {"S003": (1.02, 40.9, -58.8), "S005": (1.05, 41.4, -41.2)},
            "live": {"S003": (1.08, 43.6, -37.5), "S005": (1.01, 39.4, -41.4)}}
P300_START = "2018-01-01"
SHORT_W = (0.0, 0.25, 0.5, 0.75, 1.0)


def m3(m: dict) -> tuple[float, float, float]:
    return (round(m["sharpe"], 2), round(m["cagr"] * 100, 1), round(m["maxdd"] * 100, 1))


def p300_stats(trades: list[dict], candles: list[dict], start: str, **kw) -> dict:
    r = bv.ledger_to_daily_returns(trades, candles, **kw)
    i0 = next(i for i, c in enumerate(candles) if c["dt"] >= start)
    rr = r[i0:]
    cs = bv.curve_stats(rr, 365.25)
    closed = [t for t in trades if not t.get("still_open")]
    t_stat, n = bv.trade_t(closed)
    return dict(n_trades=len(closed), t_trade=t_stat, **cs, time_in_market=float((rr != 0).mean()))


def main() -> None:
    out = {"env": bv.env_info()}
    s005 = bv.import_s005()
    hz = bv.import_adx_harness()

    # ── 1. parity: their engine, their data ────────────────────────────────
    bars, name = bv.bitstamp_pickle(86400)
    par = {}
    for cfg_name, cfg in (("study", STUDY), ("live", LIVE)):
        par[cfg_name] = {}
        for lab, veto in (("S003", False), ("S005", True)):
            res = s005.run(bars, use_veto=veto, **cfg)
            m = s005.metrics(res, START, END)
            got = m3(m)
            exp = EXPECTED[cfg_name][lab]
            par[cfg_name][lab] = dict(got=got, expected=exp, n_trades=m["n_trades"],
                                      pass_=bool(abs(got[0] - exp[0]) <= 0.02 and abs(got[1] - exp[1]) <= 0.3 and abs(got[2] - exp[2]) <= 0.3))
            print(f"1. PARITY [{cfg_name:5s} fill] {lab}: Sh/CAGR/maxDD got {got} expected {exp} n={m['n_trades']} "
                  f"{'PASS' if par[cfg_name][lab]['pass_'] else 'FAIL'}")
    m_bh = s005.metrics(s005.run(bars, use_veto=True, **STUDY), START, END)
    par["buy_and_hold"] = dict(cagr=m_bh["bh_cagr"], sharpe=m_bh["bh_sharpe"], maxdd=m_bh["bh_maxdd"])
    par["veto_inverts_under_live_fill"] = bool(par["live"]["S003"]["got"][0] > par["live"]["S005"]["got"][0])
    out["parity"] = dict(data=name, span=[START, END], **par)
    print("   B&H same span: CAGR %.1f%% Sh %.2f maxDD %.1f%% | veto inverts under live fill: %s"
          % (m_bh["bh_cagr"] * 100, m_bh["bh_sharpe"], m_bh["bh_maxdd"] * 100, par["veto_inverts_under_live_fill"]))

    # ── 2a. their bar-phase test (6 phases from Bitstamp 4h) ───────────────
    b4, name4 = bv.bitstamp_pickle(14400)
    ph = []
    for off in range(6):
        d = bv.agg_bars(b4, 6, off)
        m = s005.metrics(s005.run(d, use_veto=True, fee_bp_rt=4.0, **STUDY), START, END)
        ph.append(dict(close_hour=((off + 6) * 4) % 24, sharpe=m["sharpe"], cagr=m["cagr"], maxdd=m["maxdd"], n=m["n_trades"]))
    phs = pd.DataFrame(ph)
    phs.to_csv(bv.RESULTS / "c4_phase_s005.csv", index=False)
    out["phase_s005"] = dict(data=name4, rows=ph, sharpe_range=[float(phs.sharpe.min()), float(phs.sharpe.max())],
                             expected_range=[0.85, 1.16])
    print("2a. their engine, 6 phases: Sharpe %.2f..%.2f (expected 0.85..1.16)\n" % (phs.sharpe.min(), phs.sharpe.max()), phs.round(3).to_string(index=False))

    # ── 2b. p300 live machine, 24 phases ───────────────────────────────────
    hourly = bv.load_hourly("cd_spot_binance", with_taker=False)
    rows = []
    ledgers = {}
    for phase in range(24):
        dfp = bv.daily_from_intraday(hourly, phase, 3600, min_rows=1, require_complete=False)
        candles = bv.df_to_candles(dfp)
        m = hz.run(candles, P300_START)
        st = p300_stats(m["trades"], candles, P300_START)
        rows.append(dict(phase_hour=phase, n=m["n"], wr=m["wr"], pf=m["pf"], ret_pct=m["ret_pct"], harness_maxdd=m["max_dd"],
                         harness_mar=m["mar"], t_trade=st["t_trade"], daily_sharpe=st["sharpe"], daily_cagr=st["cagr"] * 100,
                         daily_maxdd=st["maxdd"] * 100, daily_mar=st["mar"]))
        ledgers[phase] = (m, candles)
    pdf = pd.DataFrame(rows)
    pdf.to_csv(bv.RESULTS / "c4_phase_p300.csv", index=False)

    # Does the ADX study's Tier-1 (ATR x4 catastrophe stop) / Tier-2 (+ symmetric short<EMA150
    # filter) improvement survive the phase noise?  Paired per phase, same bars.
    tier_rows = []
    for phase, (m_base, candles) in ledgers.items():
        e150 = hz.ema([c["close"] for c in candles], 150)

        def short_gate(ctx, _e=e150):
            return ctx["new_dir"] != "short" or ctx["close"] < _e[ctx["i"]]
        m_t1 = hz.run(candles, P300_START, exit_mode="adx_or_atr", atr_mult=4.0)
        m_t2 = hz.run(candles, P300_START, entry_gate=short_gate, exit_mode="adx_or_atr", atr_mult=4.0)
        s_b, s_1, s_2 = (p300_stats(m["trades"], candles, P300_START) for m in (m_base, m_t1, m_t2))
        tier_rows.append(dict(phase_hour=phase, base_maxdd=m_base["max_dd"], t1_maxdd=m_t1["max_dd"], t2_maxdd=m_t2["max_dd"],
                              base_mar=m_base["mar"], t1_mar=m_t1["mar"], t2_mar=m_t2["mar"],
                              base_ret=m_base["ret_pct"], t1_ret=m_t1["ret_pct"], t2_ret=m_t2["ret_pct"],
                              base_mtm_dd=s_b["maxdd"] * 100, t1_mtm_dd=s_1["maxdd"] * 100, t2_mtm_dd=s_2["maxdd"] * 100,
                              base_sharpe=s_b["sharpe"], t1_sharpe=s_1["sharpe"], t2_sharpe=s_2["sharpe"],
                              n_base=m_base["n"], n_t2=m_t2["n"]))
    tdf = pd.DataFrame(tier_rows)
    tdf.to_csv(bv.RESULTS / "c4_tiers_by_phase.csv", index=False)
    out["tiers_by_phase"] = dict(
        rows=tier_rows,
        t1_improves_harness_maxdd_share=float((tdf.t1_maxdd > tdf.base_maxdd).mean()),
        t2_improves_harness_maxdd_share=float((tdf.t2_maxdd > tdf.base_maxdd).mean()),
        t1_improves_mar_share=float((tdf.t1_mar > tdf.base_mar).mean()),
        t2_improves_mar_share=float((tdf.t2_mar > tdf.base_mar).mean()),
        t2_improves_mtm_dd_share=float((tdf.t2_mtm_dd > tdf.base_mtm_dd).mean()),
        t2_improves_sharpe_share=float((tdf.t2_sharpe > tdf.base_sharpe).mean()),
        median_delta=dict(t1_maxdd=float((tdf.t1_maxdd - tdf.base_maxdd).median()), t2_maxdd=float((tdf.t2_maxdd - tdf.base_maxdd).median()),
                          t1_mar=float((tdf.t1_mar - tdf.base_mar).median()), t2_mar=float((tdf.t2_mar - tdf.base_mar).median()),
                          t2_mtm_dd=float((tdf.t2_mtm_dd - tdf.base_mtm_dd).median()), t2_sharpe=float((tdf.t2_sharpe - tdf.base_sharpe).median())))
    print("2c. Tier-1 / Tier-2 vs baseline across the 24 phases (paired):")
    print(tdf.round(2).to_string(index=False))
    print("   shares of phases improved:", {k: v for k, v in out["tiers_by_phase"].items() if k.endswith("share")},
          "| median deltas:", {k: round(v, 2) for k, v in out["tiers_by_phase"]["median_delta"].items()})
    rng = dict(daily_sharpe=[float(pdf.daily_sharpe.min()), float(pdf.daily_sharpe.max())],
               ret_pct=[float(pdf.ret_pct.min()), float(pdf.ret_pct.max())],
               harness_maxdd=[float(pdf.harness_maxdd.min()), float(pdf.harness_maxdd.max())],
               harness_mar=[float(pdf.harness_mar.min()), float(pdf.harness_mar.max())],
               n=[int(pdf.n.min()), int(pdf.n.max())], t_trade=[float(pdf.t_trade.min()), float(pdf.t_trade.max())])
    out["phase_p300"] = dict(rows=rows, ranges=rng, phase0=rows[0],
                             noise_floor_confirmed=bool(rng["daily_sharpe"][1] - rng["daily_sharpe"][0] >= 0.20))
    print("2b. p300 live machine, 24 phases (start %s):" % P300_START)
    print(pdf.round(2).to_string(index=False))
    print("   ranges:", {k: [round(x, 2) for x in v] for k, v in rng.items()}, "| noise floor >= 0.20 Sharpe:", out["phase_p300"]["noise_floor_confirmed"])

    # ── 3. stop-fill semantics + buy-and-hold on p300's machine (phase 0) ──
    m0, cand0 = ledgers[0]
    live = p300_stats(m0["trades"], cand0, P300_START)
    studyfill = p300_stats(bv.reprice_sl_at_close(m0["trades"], cand0), cand0, P300_START)
    closes = np.array([c["close"] for c in cand0])
    i0 = next(i for i, c in enumerate(cand0) if c["dt"] >= P300_START)
    bh_daily = closes[i0 + 1:] / closes[i0:-1] - 1.0
    bh = bv.curve_stats(bh_daily, 365.25)
    bh_matched = bv.curve_stats(bh_daily * live["time_in_market"], 365.25)
    out["stop_fill_p300"] = dict(live_fill=live, study_fill=studyfill, buy_and_hold=bh,
                                 buy_and_hold_exposure_matched=dict(exposure=live["time_in_market"], **bh_matched),
                                 adx_study_reference=dict(n=34, wr=50, pf=5.20, ret_pct=2769, maxdd=-27.3, mar=1.78, note="as of 2026-06-26"))
    print("3. p300 machine phase 0: live fill %s\n   study fill %s\n   B&H %s\n   B&H exposure-matched %s"
          % ({k: round(v, 3) for k, v in live.items()}, {k: round(v, 3) for k, v in studyfill.items()},
             {k: round(v, 3) for k, v in bh.items()}, {k: round(v, 3) for k, v in bh_matched.items()}))
    pd.DataFrame(m0["trades"]).to_csv(bv.RESULTS / "c4_ledger_p300.csv", index=False)

    # ── 4. short leg with funding, weight sweep, drift controls, DSR ───────
    fr = bv.fetch_binance_funding()
    fund = {}
    shorts = []
    for t in m0["trades"]:
        if t["dir"] != "short":
            continue
        dt_idx = {c["dt"]: i for i, c in enumerate(cand0)}
        ts0 = cand0[dt_idx[t["entry_dt"]]]["ts"] + 86400
        ts1 = cand0[dt_idx[str(t["exit_dt"]).replace(" (open)", "")]]["ts"] + 86400
        fpct = bv.funding_pct_between(fr, ts0, ts1)
        fund[t["entry_dt"]] = fpct
        shorts.append(dict(entry_dt=t["entry_dt"], exit_dt=str(t["exit_dt"]), days=(ts1 - ts0) // 86400, price_net_pct=t["net_pct"],
                           funding_pct=fpct, total_pct=t["net_pct"] + fpct, reason=t["reason"], still_open=bool(t.get("still_open"))))
    sdf = pd.DataFrame(shorts)
    sdf.to_csv(bv.RESULTS / "c4_shortleg.csv", index=False)
    longs = [t for t in m0["trades"] if t["dir"] == "long" and not t.get("still_open")]
    closed_shorts = sdf[~sdf.still_open]
    sweep = []
    for w in SHORT_W:
        st = p300_stats(m0["trades"], cand0, P300_START, short_weight=w, funding_pct=fund)
        st_px = p300_stats(m0["trades"], cand0, P300_START, short_weight=w)
        sweep.append(dict(short_weight=w, cagr=st["cagr"] * 100, maxdd=st["maxdd"] * 100, mar=st["mar"], sharpe=st["sharpe"],
                          cagr_price_only=st_px["cagr"] * 100, maxdd_price_only=st_px["maxdd"] * 100, mar_price_only=st_px["mar"]))
    swp = pd.DataFrame(sweep)
    boot_short = bv.boot_mean_ci(closed_shorts["total_pct"].to_numpy(), block=1)
    boot_short_px = bv.boot_mean_ci(closed_shorts["price_net_pct"].to_numpy(), block=1)
    bears = closed_shorts[closed_shorts.entry_dt.str[:4].isin(["2018", "2022"])]
    # ETH machine (drift control in asset)
    eth = bv.daily_from_1m_sql("eth_1m")
    cand_e = bv.df_to_candles(eth)
    me = hz.run(cand_e, "2021-01-01")
    e_short = [t["net_pct"] for t in me["trades"] if t["dir"] == "short" and not t.get("still_open")]
    e_long = [t["net_pct"] for t in me["trades"] if t["dir"] == "long" and not t.get("still_open")]
    daily_live = bv.ledger_to_daily_returns(m0["trades"], cand0)[i0:]
    out["short_leg"] = dict(
        n_short_closed=int(len(closed_shorts)), n_long_closed=len(longs),
        short_mean_price_pct=float(closed_shorts.price_net_pct.mean()), short_mean_funding_pct=float(closed_shorts.funding_pct.mean()),
        short_mean_total_pct=float(closed_shorts.total_pct.mean()), short_sum_total_pct=float(closed_shorts.total_pct.sum()),
        long_mean_pct=float(np.mean([t["net_pct"] for t in longs])), long_sum_pct=float(np.sum([t["net_pct"] for t in longs])),
        boot_short_total=boot_short, boot_short_price=boot_short_px,
        bear_years_2018_2022=dict(n=int(len(bears)), mean_total_pct=float(bears.total_pct.mean()) if len(bears) else float("nan"),
                                  mean_price_pct=float(bears.price_net_pct.mean()) if len(bears) else float("nan")),
        eth_machine=dict(n_short=len(e_short), short_mean_pct=float(np.mean(e_short)) if e_short else float("nan"),
                         short_sum_pct=float(np.sum(e_short)) if e_short else float("nan"),
                         n_long=len(e_long), long_mean_pct=float(np.mean(e_long)) if e_long else float("nan"), ret_pct=me["ret_pct"], maxdd=me["max_dd"]),
        weight_sweep=sweep, best_mar_weight=float(swp.loc[swp.mar.idxmax(), "short_weight"]),
        dsr_daily_curve={"N1": bv.dsr(daily_live, 1), "N17_adx_study_sweep": bv.dsr(daily_live, 17)},
        no_short_edge_demonstrated=bool(boot_short["ci"][0] <= 0 <= boot_short["ci"][2]))
    print("4. short leg (p300 machine): n=%d closed shorts, mean price %+.2f%%, funding %+.2f%%, total %+.2f%% (CI90 %s); longs n=%d mean %+.2f%%"
          % (len(closed_shorts), closed_shorts.price_net_pct.mean(), closed_shorts.funding_pct.mean(), closed_shorts.total_pct.mean(),
             [round(x, 2) for x in boot_short["ci"]], len(longs), np.mean([t["net_pct"] for t in longs])))
    print(sdf.round(2).to_string(index=False))
    print("   weight sweep:\n", swp.round(3).to_string(index=False))
    print("   bear years 2018/2022 shorts:", out["short_leg"]["bear_years_2018_2022"], "| ETH machine:", out["short_leg"]["eth_machine"])
    print("   DSR daily curve:", {k: round(v["dsr"], 3) for k, v in out["short_leg"]["dsr_daily_curve"].items()})

    out["decision"] = dict(
        parity_pass=all(par[c][l]["pass_"] for c in ("study", "live") for l in ("S003", "S005")),
        veto_inverts_under_live_fill=par["veto_inverts_under_live_fill"],
        their_phase_range=out["phase_s005"]["sharpe_range"],
        p300_phase_sharpe_range=rng["daily_sharpe"], noise_floor_confirmed=out["phase_p300"]["noise_floor_confirmed"],
        no_short_edge_demonstrated=out["short_leg"]["no_short_edge_demonstrated"], best_mar_short_weight=out["short_leg"]["best_mar_weight"])
    print("5. DECISION:", out["decision"])
    bv.jdump(out, "c4_summary.json")


if __name__ == "__main__":
    main()

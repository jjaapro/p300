"""E0 — facts and parity gates (README §E0)."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import exec_lib as ex

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")

CHENTO_EXPECT = {"BTC": dict(nocost=0.800, cost=0.685), "ETH": dict(nocost=0.712, cost=0.622)}
SS_EXPECT = dict(n=70, win=0.443, mean_r=0.40, pf=1.65, end="2026-05-18")


def replay_15m(bars: pd.DataFrame, t: ex.Event) -> float:
    """Byte-for-byte the overlay engine's base branch on 15 m bars."""
    sign = t.direction
    entry, stop, target, risk = t.signal_price, t.stop, t.target, t.risk
    ts = pd.Timestamp(t.extra["ts_open"], unit="s", tz="UTC")
    w = bars[(bars.index > ts) & (bars.index <= ts + pd.Timedelta(hours=72))]
    if not len(w):
        return np.nan
    for _, b in w.iterrows():
        if (b["low"] <= stop) if sign > 0 else (b["high"] >= stop):
            return -1.0
        if (b["high"] >= target) if sign > 0 else (b["low"] <= target):
            return (target - entry) * sign / risk
    return (w.iloc[-1]["close"] - entry) * sign / risk


def main() -> None:
    out = {"env": ex.env_info(), "cache": ex.build_cache(), "fees_bp": ex.FEES_BP, "as_bp": ex.AS_BP,
           "coded_cost_bp": ex.CODED_COST_BP}

    # ── (a) what is btc_1m? ────────────────────────────────────────────────
    m1 = ex.load_path("btc_1m")
    spot_h = ex.sql_df("SELECT timestamp, close FROM cd_spot_binance WHERE timestamp >= 1577836800 ORDER BY timestamp")
    perp_h = ex.sql_df("SELECT timestamp, close FROM cd_futures_ohlcv WHERE timestamp >= 1577836800 ORDER BY timestamp")
    # 1m bar opening at hh:59 closes the hour; compare its close with the hourly close (both stamp the open)
    hour_ts = spot_h.timestamp.to_numpy(np.int64)
    idx = np.searchsorted(m1.ts, hour_ts + 3540)
    ok = (idx < len(m1.ts)) & (m1.ts[np.minimum(idx, len(m1.ts) - 1)] == hour_ts + 3540)
    m1c = m1.c[idx[ok]]
    sp = spot_h.close.to_numpy()[ok]
    pp = perp_h.set_index("timestamp").close.reindex(hour_ts[ok]).to_numpy()
    d_spot = np.abs(m1c / sp - 1) * 1e4
    d_perp = np.abs(m1c / pp - 1) * 1e4
    out["btc_1m_identity"] = dict(n_hours=int(ok.sum()), median_abs_bp_vs_spot=float(np.nanmedian(d_spot)),
                                  median_abs_bp_vs_perp=float(np.nanmedian(d_perp)),
                                  share_exact_vs_spot=float(np.mean(d_spot < 0.01)),
                                  verdict="SPOT" if np.nanmedian(d_spot) < np.nanmedian(d_perp) else "PERP")
    print("(a) btc_1m identity:", out["btc_1m_identity"])

    # ── (b) spot vs perp basis on 15 m closes ──────────────────────────────
    s15 = ex.sql_df("SELECT timestamp, close FROM cd_spot_15m ORDER BY timestamp").set_index("timestamp").close
    p15 = ex.sql_df("SELECT timestamp, close FROM cd_futures_15m ORDER BY timestamp").set_index("timestamp").close
    j = pd.concat([s15.rename("spot"), p15.rename("perp")], axis=1, join="inner")
    basis = (j.perp / j.spot - 1) * 1e4
    five = basis[(j.index >= 1749340800) & (j.index < 1780963200)]
    yr = basis.groupby(pd.to_datetime(basis.index, unit="s").year)
    out["basis_bp"] = dict(
        full=dict(n=int(len(basis)), mean=float(basis.mean()), sd=float(basis.std()), p95_abs=float(basis.abs().quantile(0.95)),
                  span=[ex.iso(j.index[0]), ex.iso(j.index[-1])]),
        five_s_year=dict(n=int(len(five)), mean=float(five.mean()), sd=float(five.std()), p95_abs=float(five.abs().quantile(0.95))),
        by_year={int(k): dict(mean=float(v.mean()), sd=float(v.std())) for k, v in yr},
        # 15 m change of the basis = the re-basing error of a level held for one bar
        d15_sd=float(basis.diff().std()), d15_p95_abs=float(basis.diff().abs().quantile(0.95)))
    print("(b) basis bp (perp/spot-1):", {k: v for k, v in out["basis_bp"].items() if k != "by_year"})

    # ── (c) parity gates ───────────────────────────────────────────────────
    gates = {}
    for asset, table in (("BTC", "cd_futures_15m"), ("ETH", "cd_futures_eth_15m")):
        ev = ex.load_chento(asset)
        bars = ex.sql_df(f"SELECT timestamp, open, high, low, close FROM {table} ORDER BY timestamp")
        bars.index = pd.to_datetime(bars.pop("timestamp"), unit="s", utc=True)
        bars = bars[~bars.index.duplicated(keep="last")]
        r = np.array([replay_15m(bars, t) for t in ev])
        cost = np.array([(18.0 / 1e4) * (t.signal_price / t.risk) for t in ev])
        keep = ~np.isnan(r)
        m_no, m_c = float(r[keep].mean()), float((r - cost)[keep].mean())
        exp = CHENTO_EXPECT[asset]
        gates[f"CHENTO_{asset}"] = dict(n=int(keep.sum()), mean_r_nocost=m_no, mean_r_18bp=m_c, expected=exp,
                                        pass_=bool(abs(m_no - exp["nocost"]) < 0.001 and abs(m_c - exp["cost"]) < 0.001))
        print(f"(c) CHENTO_{asset}: n={keep.sum()} no-cost {m_no:.4f} (exp {exp['nocost']}) | 18bp {m_c:.4f} (exp {exp['cost']}) ->",
              "PASS" if gates[f"CHENTO_{asset}"]["pass_"] else "FAIL")
        # paper price check: the spot 1 m close before the signal vs the perp 15 m close the ledger books
        path = ex.path_for(asset, "1m")
        pp_ = np.array([path.last_close_before(t.signal_ts) for t in ev])
        sig = np.array([t.signal_price for t in ev])
        gates[f"CHENTO_{asset}"]["spot_1m_vs_perp_close_bp"] = dict(median_abs=float(np.nanmedian(np.abs(pp_ / sig - 1) * 1e4)),
                                                                     p95_abs=float(np.nanquantile(np.abs(pp_ / sig - 1) * 1e4, 0.95)))

    ss_events, b15, trig = ex.load_short_squeeze()
    sim = ex.short_squeeze_notebook_simulate(b15, trig, m1)
    sub = sim[sim.trigger_ts <= pd.Timestamp(SS_EXPECT["end"], tz="UTC")]
    wins = sub.loc[sub.pnl_R > 0, "pnl_R"].sum(); losses = -sub.loc[sub.pnl_R < 0, "pnl_R"].sum()
    got = dict(n=int(len(sub)), win=float((sub.pnl_R > 0).mean()), mean_r=float(sub.pnl_R.mean()),
               pf=float(wins / losses) if losses > 0 else float("inf"), n_all=int(len(sim)),
               triggers_all=int(trig.sum()), span=[str(sim.trigger_ts.min()), str(sim.trigger_ts.max())])
    gates["SHORT_SQUEEZE"] = dict(got=got, expected=SS_EXPECT,
                                  pass_=bool(got["n"] == SS_EXPECT["n"] and abs(got["mean_r"] - SS_EXPECT["mean_r"]) < 0.01))
    print("(c) SHORT_SQUEEZE port:", got, "->", "PASS" if gates["SHORT_SQUEEZE"]["pass_"] else "FAIL")
    sim.to_csv(ex.RESULTS / "e0_short_squeeze_notebook_replay.csv", index=False)

    sb = ex.load_squeeze_bull(bull_only=True)
    hb = ex.sql_df("SELECT timestamp, open, high, low, close FROM cd_futures_ohlcv ORDER BY timestamp")
    hb = hb.drop_duplicates("timestamp").set_index("timestamp")
    hts = hb.index.to_numpy(np.int64); H, L, C = hb.high.to_numpy(), hb.low.to_numpy(), hb.close.to_numpy()
    diffs = []
    for t in sb:
        i = int(np.searchsorted(hts, t.extra["ts_open"]))
        if i >= len(hts) or hts[i] != t.extra["ts_open"]:
            diffs.append(np.nan); continue
        entry = C[i]; stop = entry * 0.98; target = entry * 1.03; risk = entry - stop
        cost_R = (18.0 / 1e4) * (entry / risk)
        r_out = None; last_close = entry
        for jj in range(i + 1, min(i + 49, len(hts))):
            last_close = C[jj]
            if L[jj] <= stop:
                r_out = (stop - entry) / risk - cost_R; break
            if H[jj] >= target:
                r_out = (target - entry) / risk - cost_R; break
        if r_out is None:
            r_out = (last_close - entry) / risk - cost_R
        diffs.append(abs(r_out - t.extra["r_csv"]))
    diffs = np.array(diffs)
    gates["SQUEEZE_BULL"] = dict(n=int(len(sb)), n_matched=int(np.isfinite(diffs).sum()), max_abs_diff=float(np.nanmax(diffs)),
                                 pass_=bool(np.nanmax(diffs) < 1e-6 and np.isfinite(diffs).all()))
    print("(c) SQUEEZE_BULL ledger parity:", gates["SQUEEZE_BULL"])

    adx = ex.load_adx()
    gates["ADX"] = dict(n_closed=len(adx), reasons=pd.Series([a["reason"] for a in adx]).value_counts().to_dict(),
                        mean_net_pct=float(np.mean([a["net_pct"] for a in adx])))
    print("(c) ADX ledger:", gates["ADX"])
    out["gates"] = gates
    out["event_counts"] = {"CHENTO_BTC": len(ex.load_chento("BTC")), "CHENTO_ETH": len(ex.load_chento("ETH")),
                           "SHORT_SQUEEZE": len(ss_events), "SQUEEZE_BULL": len(sb), "ADX": len(adx)}
    print("event counts:", out["event_counts"])
    ex.jdump(out, "e0_facts.json")


if __name__ == "__main__":
    main()

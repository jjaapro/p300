"""C0 — data parity gate.

Do the brainstorm's Binance-REST caches agree with p300's tables where they
overlap?  Is the funding series the same object in every source?  Everything
read-only; results -> results/c0_data_parity.json.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import bv_lib as bv


def rel(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        d = np.abs(a - b) / np.where(np.abs(b) > 0, np.abs(b), np.nan)
    return d


def stat(d):
    d = d[np.isfinite(d)]
    if d.size == 0:
        return dict(n=0)
    return dict(n=int(d.size), max=float(d.max()), mean=float(d.mean()),
                p99=float(np.quantile(d, 0.99)), share_gt_1pct=float((d > 0.01).mean()),
                share_gt_0p1pct=float((d > 0.001).mean()))


def main() -> None:
    out = {"env": bv.env_info()}

    # 1. brainstorm BTC daily cache vs p300 daily from cd_spot_binance hourly
    b1 = bv.bars_to_df(bv.scalp_pickle("spot_BTCUSDT_1d_3200d.pkl"))
    p1 = bv.load_daily_btc()
    m = b1.merge(p1, on="dt", suffixes=("_bs", "_p3"))
    st = {c: stat(rel(m[c + "_bs"], m[c + "_p3"])) for c in ("open", "high", "low", "close", "volume", "volume_buy")}
    out["btc_daily_cache_vs_p300"] = dict(
        n_cache=len(b1), n_p300=len(p1), n_overlap=len(m),
        cache_span=[b1["dt"].iloc[0], b1["dt"].iloc[-1]], p300_span=[p1["dt"].iloc[0], p1["dt"].iloc[-1]],
        only_in_cache=sorted(set(b1["dt"]) - set(p1["dt"]))[:20],
        only_in_p300_within_cache_span=sorted(d for d in set(p1["dt"]) - set(b1["dt"])
                                              if b1["dt"].iloc[0] <= d <= b1["dt"].iloc[-1])[:20],
        stats=st, pass_close=bool(st["close"]["max"] < 1e-3))
    print("1. BTC daily: cache %d rows, p300 %d rows, overlap %d | close max rel diff %.2e | volume p99 %.2e"
          % (len(b1), len(p1), len(m), st["close"]["max"], st["volume"]["p99"]))

    # 2. brainstorm 5m cache -> 15m vs cd_spot_15m
    b5 = bv.bars_to_df(bv.scalp_pickle("spot_BTCUSDT_5m_1177d.pkl"))
    b5["ts15"] = b5["ts"] // 900 * 900
    a15 = b5.groupby("ts15").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                 close=("close", "last"), volume=("volume", "sum"),
                                 volume_buy=("volume_buy", "sum"), n=("ts", "size"))
    a15 = a15[a15["n"] == 3]
    s15 = bv.load_15m("cd_spot_15m").set_index("ts")
    j = a15.join(s15, lsuffix="_bs", rsuffix="_p3", how="inner")
    st5 = {c: stat(rel(j[c + "_bs"], j[c + "_p3"])) for c in ("close", "high", "low", "volume", "volume_buy")}
    out["btc_5m_cache_vs_cd_spot_15m"] = dict(
        n_cache_5m=len(b5), n_cache_15m=len(a15), n_overlap=len(j),
        cache_span=[bv.iso(b5["ts"].iloc[0]), bv.iso(b5["ts"].iloc[-1])], stats=st5,
        pass_close=bool(st5["close"]["max"] < 1e-3),
        pass_taker=bool(st5["volume_buy"]["share_gt_1pct"] < 0.001))
    print("2. BTC 5m cache -> 15m vs cd_spot_15m: overlap %d | close max %.2e | volume share>1%% %.4f | taker share>1%% %.4f"
          % (len(j), st5["close"]["max"], st5["volume"]["share_gt_1pct"], st5["volume_buy"]["share_gt_1pct"]))

    # 3. funding: fresh Binance fetch vs their pickle vs prod.db vs bybit
    fr = bv.fetch_binance_funding()
    frd = dict(fr)
    pk = bv.scalp_pickle("funding_BTCUSDT_full.pkl")
    pkd = {int(round(t / 3600000.0)) * 3600000: r for t, r in pk}
    common = sorted(set(pkd) & set(frd))
    dif = np.array([abs(pkd[t] - frd[t]) for t in common]) if common else np.array([])
    prod_post = {int(t) * 1000: r for t, r in bv.q(
        "SELECT timestamp, fr_close FROM cd_funding_rate WHERE timestamp >= strftime('%s','2026-04-13') "
        "AND timestamp % 28800 = 0 AND fr_close IS NOT NULL")}
    prod_pre = {int(t) * 1000: r for t, r in bv.q(
        "SELECT timestamp, fr_close FROM cd_funding_rate WHERE timestamp < strftime('%s','2026-04-13') "
        "AND timestamp % 28800 = 0 AND fr_close IS NOT NULL")}
    byb = {int(t) * 1000: r for t, r in bv.q(
        "SELECT timestamp, funding_rate FROM bybit_funding WHERE symbol='BTCUSDT'")}

    def compare(d):
        c = sorted(set(d) & set(frd))
        if not c:
            return dict(n=0)
        x = np.array([d[t] for t in c])
        y = np.array([frd[t] for t in c])
        return dict(n=len(c), corr=float(np.corrcoef(x, y)[0, 1]), max_abs_diff=float(np.abs(x - y).max()),
                    share_equal_1e8=float((np.abs(x - y) < 1e-8).mean()),
                    mean_this=float(x.mean()), mean_binance=float(y.mean()))

    out["funding"] = dict(
        binance_fetch=dict(n=len(fr), span=[bv.iso(fr[0][0] // 1000), bv.iso(fr[-1][0] // 1000)]),
        pickle_vs_fetch=dict(n_common=len(common), max_abs_diff=float(dif.max()) if dif.size else None,
                             n_pickle=len(pk), pass_=bool(dif.size and dif.max() < 1e-7)),
        prod_post_cutover_vs_binance=compare(prod_post),
        prod_pre_cutover_settlement_rows_vs_binance=compare(prod_pre),
        bybit_vs_binance=compare(byb))
    print("3. funding: fetch %d prints %s..%s | pickle common %d max diff %s | prod post-cutover equal-share %.3f | "
          "prod PRE-cutover 08h rows equal-share %.3f corr %.2f | bybit corr %.2f"
          % (len(fr), out["funding"]["binance_fetch"]["span"][0], out["funding"]["binance_fetch"]["span"][1],
             len(common), out["funding"]["pickle_vs_fetch"]["max_abs_diff"],
             out["funding"]["prod_post_cutover_vs_binance"].get("share_equal_1e8", float("nan")),
             out["funding"]["prod_pre_cutover_settlement_rows_vs_binance"].get("share_equal_1e8", float("nan")),
             out["funding"]["prod_pre_cutover_settlement_rows_vs_binance"].get("corr", float("nan")),
             out["funding"]["bybit_vs_binance"].get("corr", float("nan"))))

    # 4. Bitstamp: their pickle vs studies/material json
    bs, name = bv.bitstamp_pickle(86400)
    bsd = bv.bars_to_df(bs)
    js = bv.load_bitstamp_json()
    mb = bsd.merge(js, on="dt", suffixes=("_bs", "_js"))
    stb = {c: stat(rel(mb[c + "_bs"], mb[c + "_js"])) for c in ("open", "high", "low", "close")}
    b4, name4 = bv.bitstamp_pickle(14400)
    out["bitstamp"] = dict(pickle=name, n_pickle=len(bsd), span_pickle=[bsd["dt"].iloc[0], bsd["dt"].iloc[-1]],
                           n_json=len(js), span_json=[js["dt"].iloc[0], js["dt"].iloc[-1]], n_overlap=len(mb),
                           stats=stb, pass_close=bool(stb["close"]["max"] < 1e-6),
                           pickle_4h=name4, n_4h=len(b4), span_4h=[bv.iso(b4[0][0] // 1000), bv.iso(b4[-1][0] // 1000)])
    print("4. Bitstamp: pickle %d rows (%s..%s) vs json %d rows, overlap %d, close max rel diff %.2e; 4h pickle %d rows"
          % (len(bsd), bsd["dt"].iloc[0], bsd["dt"].iloc[-1], len(js), len(mb), stb["close"]["max"], len(b4)))

    # 5. p300 internal: daily from hourly vs daily from btc_1m
    p1m = bv.daily_from_1m_sql("btc_1m")
    mi = p1.merge(p1m, on="dt", suffixes=("_h", "_m"))
    sti = {c: stat(rel(mi[c + "_h"], mi[c + "_m"])) for c in ("open", "high", "low", "close")}
    out["p300_hourly_vs_1m_daily"] = dict(n_overlap=len(mi), stats=sti, pass_close=bool(sti["close"]["max"] < 1e-3))
    print("5. p300 daily (hourly) vs daily (btc_1m): overlap %d, close max rel diff %.2e" % (len(mi), sti["close"]["max"]))

    # 6. other panels' coverage
    eth = bv.daily_from_1m_sql("eth_1m")
    alts = bv.load_screener_daily()
    p5 = bv.load_5m_from_5s()
    out["coverage"] = dict(eth_daily=dict(n=len(eth), span=[eth["dt"].iloc[0], eth["dt"].iloc[-1]]),
                           alts=dict(n_assets=len(alts), assets=sorted(alts),
                                     min_days=int(min(len(v) for v in alts.values())),
                                     max_days=int(max(len(v) for v in alts.values()))),
                           spot_5m_from_5s=dict(n=len(p5), span=[bv.iso(p5["ts"].iloc[0]), bv.iso(p5["ts"].iloc[-1])]))
    print("6. ETH daily %d rows (%s..%s); alt panel %d assets (%d..%d days); 5m-from-5s %d bars (%s..%s)"
          % (len(eth), eth["dt"].iloc[0], eth["dt"].iloc[-1], len(alts),
             out["coverage"]["alts"]["min_days"], out["coverage"]["alts"]["max_days"],
             len(p5), out["coverage"]["spot_5m_from_5s"]["span"][0], out["coverage"]["spot_5m_from_5s"]["span"][1]))

    # gate = the bulk agrees: fewer than 0.1 % of overlapping bars differ by more than 0.1 % on the
    # close (the max is reported separately; a partial last day or a known outage row is not a data fault)
    def bulk_ok(s):
        return bool(s["n"] > 0 and s["share_gt_0p1pct"] < 1e-3)
    out["gate"] = dict(
        btc_daily=bulk_ok(st["close"]),
        btc_5m=bulk_ok(st5["close"]) and bulk_ok(st5["volume_buy"]),
        funding=bool(out["funding"]["pickle_vs_fetch"]["pass_"] and len(fr) > 7000),
        bitstamp=bulk_ok(stb["close"]),
        p300_internal=bulk_ok(sti["close"]))
    print("GATE:", out["gate"])
    bv.jdump(out, "c0_data_parity.json")


if __name__ == "__main__":
    main()

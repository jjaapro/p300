"""C3 — absorption (fade the absorbed aggressor), 2 h hold.  README §C3."""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import bv_lib as bv

H5 = [12, 24, 36, 48, 72, 96, 144, 192, 288, 432, 576]
LAB5 = {12: "1h", 24: "2h", 36: "3h", 48: "4h", 72: "6h", 96: "8h", 144: "12h", 192: "16h", 288: "1d", 432: "1.5d", 576: "2d"}
H15 = [4, 8, 12, 16, 24, 32, 64, 96]
LAB15 = {4: "1h", 8: "2h", 12: "3h", 16: "4h", 24: "6h", 32: "8h", 64: "16h", 96: "1d"}
CLAIM = dict(n=1620, mean_bp=4.8, t=2.91, tc=2.16, capture=6.7)
N_TRIALS = {"N1": 1, "N348_study": 348}
FEES = (4.1, 5.0, 8.0)


def decay(df: pd.DataFrame, W: int, Hs: list[int], lab: dict, z: float = 1.0, min_n: int = 30) -> tuple[pd.DataFrame, dict]:
    c, v, tb = df["close"].to_numpy(float), df["volume"].to_numpy(float), df["volume_buy"].to_numpy(float)
    mask, d = bv.abs_signal(c, v, tb, W, z)
    rows, full = [], {}
    for h in Hs:
        r = bv.event_study(mask, d, c, h, min_n=min_n)
        full[h] = r
        s = bv.summary(r, ("n", "mean_bp", "t", "tc", "g", "win", "capture", "n_long", "long_mean_bp", "n_short", "short_mean_bp")) or dict(n=0)
        rows.append(dict(H=h, hours=lab[h], **s, **{f"net{f:g}": (s["mean_bp"] - f if "mean_bp" in s else float("nan")) for f in FEES}))
    return pd.DataFrame(rows), full


def main() -> None:
    out = {"env": bv.env_info(), "rule": dict(W_5m=288, W_15m=96, z=1.0, rz=0.5, direction="-sign(imbalance)", claim=CLAIM),
           "n_trials": N_TRIALS}

    # ── 1. parity on their 5m cache ────────────────────────────────────────
    b5 = bv.bars_to_df(bv.scalp_pickle("spot_BTCUSDT_5m_1177d.pkl"))
    tab, full = decay(b5, 288, H5, LAB5, 1.0)
    tab15, _ = decay(b5, 288, H5, LAB5, 1.5)
    tab.to_csv(bv.RESULTS / "c3_decay_their_cache_z1.csv", index=False)
    tab15.to_csv(bv.RESULTS / "c3_decay_their_cache_z1p5.csv", index=False)
    c2h = tab[tab.H == 24].iloc[0]
    ok = c2h["n"] == CLAIM["n"] and abs(c2h["mean_bp"] - CLAIM["mean_bp"]) < 0.15 and abs(c2h["t"] - CLAIM["t"]) < 0.02
    out["parity"] = dict(data="spot_BTCUSDT_5m_1177d.pkl", span=[b5["dt"].iloc[0], b5["dt"].iloc[-1]], n_bars=len(b5),
                         got_2h=c2h.to_dict(), expected=CLAIM, pass_=bool(ok), decay_z1=tab.to_dict("records"))
    print("1. PARITY their cache, z>1.0 (their table: 1h +2.9/2.54, 2h +4.8/2.91(2.16), 3h +4.8/2.25, 4h +1.6/0.62, 8h +1.5, 16h -1.4):")
    print(tab[["hours", "n", "mean_bp", "t", "tc", "g", "win", "capture", "net4.1"]].round(2).to_string(index=False))
    print("   PASS" if ok else "   FAIL", "| z>1.5 2h:", tab15[tab15.H == 24][["n", "mean_bp", "t", "tc"]].round(2).to_dict("records"))
    # the 2h cell on their own cache, by year / era / leg (is the 3.2-year +4.8 bp spread or concentrated?)
    r2h = full[24]
    ts5 = b5["ts"].to_numpy(np.int64)
    py5 = bv.per_year(r2h, ts5)
    py5.to_csv(bv.RESULTS / "c3_per_year_their_cache_2h.csv", index=False)
    out["parity"]["cell_2h_by_year"] = py5.to_dict("records")
    out["parity"]["cell_2h_era"] = bv.era_split(r2h, ts5)
    out["parity"]["cell_2h_legs"] = dict(n_long=r2h["n_long"], long_mean_bp=r2h["long_mean_bp"], n_short=r2h["n_short"], short_mean_bp=r2h["short_mean_bp"])
    out["parity"]["cell_2h_dsr"] = {k: bv.dsr(r2h["adj"], n) for k, n in N_TRIALS.items()}
    print("   2h cell on their cache by year:\n", py5.round(2).to_string(index=False))
    print("   era:", out["parity"]["cell_2h_era"], "| legs:", out["parity"]["cell_2h_legs"],
          "| DSR:", {k: round(v["dsr"], 3) for k, v in out["parity"]["cell_2h_dsr"].items()})

    # ── 3. exact-resolution replication: cd_spot_5s -> 5m, and their cache on the same window ──
    p5 = bv.load_5m_from_5s()
    t0, t1 = int(p5["ts"].iloc[0]), int(p5["ts"].iloc[-1])
    win = b5[(b5["ts"] >= t0) & (b5["ts"] <= t1)].reset_index(drop=True)
    tp5, fp5 = decay(p5, 288, H5, LAB5, 1.0, min_n=20)
    tw5, fw5 = decay(win, 288, H5, LAB5, 1.0, min_n=20)
    # data agreement inside the window
    j = p5.set_index("ts")[["close", "volume", "volume_buy"]].join(win.set_index("ts")[["close", "volume", "volume_buy"]],
                                                                   lsuffix="_p3", rsuffix="_bs", how="inner")
    agree = {c: float(np.nanmax(np.abs(j[c + "_p3"] - j[c + "_bs"]) / np.abs(j[c + "_bs"]).replace(0, np.nan))) for c in ("close", "volume", "volume_buy")}
    out["exact_resolution"] = dict(window=[bv.iso(t0), bv.iso(t1)], n_bars_p300=len(p5), n_bars_cache=len(win), n_common=len(j),
                                   max_rel_diff=agree, p300=tp5.to_dict("records"), their_cache_same_window=tw5.to_dict("records"))
    print("3. exact-resolution window %s..%s: p300 5m-from-5s %d bars, cache %d bars, common %d, max rel diff %s"
          % (bv.iso(t0), bv.iso(t1), len(p5), len(win), len(j), {k: round(v, 5) for k, v in agree.items()}))
    print("   p300 5m:\n", tp5[["hours", "n", "mean_bp", "t", "tc", "g", "capture", "net4.1"]].round(2).to_string(index=False))
    print("   cache same window:\n", tw5[["hours", "n", "mean_bp", "t", "tc", "g", "capture", "net4.1"]].round(2).to_string(index=False))

    # ── 4. independent 7-year 15m panels ───────────────────────────────────
    panels = {}
    fulls = {}
    for name, table in (("spot_15m", "cd_spot_15m"), ("perp_15m", "cd_futures_15m"), ("eth_perp_15m", "cd_futures_eth_15m")):
        df = bv.load_15m(table)
        t, fl = decay(df, 96, H15, LAB15, 1.0)
        t15, _ = decay(df, 96, H15, LAB15, 1.5)
        t.to_csv(bv.RESULTS / f"c3_decay_{name}_z1.csv", index=False)
        panels[name] = dict(span=[bv.iso(df["ts"].iloc[0]), bv.iso(df["ts"].iloc[-1])], n_bars=len(df),
                            decay_z1=t.to_dict("records"), decay_z1p5=t15.to_dict("records"))
        fulls[name] = (df, fl)
        print(f"4. {name} ({panels[name]['span'][0]}..{panels[name]['span'][1]}, {len(df)} bars), z>1.0:")
        print(t[["hours", "n", "mean_bp", "t", "tc", "g", "win", "capture", "n_long", "long_mean_bp", "n_short", "short_mean_bp", "net4.1"]].round(2).to_string(index=False))
    out["panels_15m"] = panels

    # ── 5. the primary 15m spot 2h cell: per-year, era, truncation, DSR, bootstrap ──
    df, fl = fulls["spot_15m"]
    r = fl[8]
    ts = df["ts"].to_numpy(np.int64)
    py = bv.per_year(r, ts)
    py.to_csv(bv.RESULTS / "c3_per_year_spot15m.csv", index=False)
    pre_df = df.iloc[:120_000].reset_index(drop=True)
    c, v, tb = pre_df["close"].to_numpy(float), pre_df["volume"].to_numpy(float), pre_df["volume_buy"].to_numpy(float)
    m_pre, d_pre = bv.abs_signal(c, v, tb, 96, 1.0)
    r_pre = bv.event_study(m_pre, d_pre, c, 8, min_n=30)
    trunc = set(int(i) for i in r["idx"] if i < 120_000 - 8) == set(int(i) for i in r_pre["idx"] if i < 120_000 - 8)
    out["primary_spot15m_2h"] = dict(summary=bv.summary(r), per_year=py.to_dict("records"), era=bv.era_split(r, ts),
                                     truncation_test_pass=bool(trunc),
                                     dsr={k: bv.dsr(r["adj"], n) for k, n in N_TRIALS.items()},
                                     boot=bv.boot_mean_ci(r["adj"], block=1),
                                     net={f"{f:g}bp": r["mean_bp"] - f for f in FEES})
    print("5. primary spot 15m 2h:", bv.summary(r), "| truncation", trunc)
    print(py.round(2).to_string(index=False))
    print("   era:", out["primary_spot15m_2h"]["era"], "| DSR:", {k: round(v["dsr"], 3) for k, v in out["primary_spot15m_2h"]["dsr"].items()},
          "| boot CI90 bp", [round(x * 1e4, 2) for x in out["primary_spot15m_2h"]["boot"]["ci"]])

    # figure: decay curves
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot([h * 5 / 60 for h in H5], tab["mean_bp"], "o-", label="their 5m cache (3.2y)")
    for name, (dfp, flp) in fulls.items():
        rows = panels[name]["decay_z1"]
        ax.plot([h / 4 for h in H15], [x["mean_bp"] for x in rows], "s--", label=f"p300 {name} (7y)")
    ax.axhline(0, color="k", lw=0.5)
    ax.axhline(4.1, color="grey", lw=0.5, ls=":", label="4.1 bp maker-maker")
    ax.set_xscale("log")
    ax.set_xlabel("holding horizon, hours")
    ax.set_ylabel("drift-adjusted edge, bp")
    ax.set_title("C3 absorption decay curves")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(bv.RESULTS / "c3_decay.png", dpi=110)
    plt.close(fig)

    # ── decision ───────────────────────────────────────────────────────────
    perp = next(x for x in panels["perp_15m"]["decay_z1"] if x["H"] == 8)
    years_pos = int((py["mean_bp"] > 0).sum())
    sig = bool(r["mean_bp"] > 0 and r["t"] >= 2.0 and years_pos >= 5 and perp.get("mean_bp", -1) > 0)
    net_ok = bool(r["mean_bp"] - 4.1 > 0 and out["primary_spot15m_2h"]["dsr"]["N348_study"]["dsr"] >= 0.95)
    kill = bool(r["t"] < 2.0 or r["mean_bp"] <= 0)
    verdict = "KILL" if kill else ("SIGNAL-CONFIRMED" + (" + TRADABLE" if net_ok else " (not tradable at 4.1bp)") if sig else "INCONCLUSIVE")
    out["decision"] = dict(spot15m_t=r["t"], spot15m_mean_bp=r["mean_bp"], years_positive=years_pos, years=int(len(py)),
                           perp_same_sign=bool(perp.get("mean_bp", -1) > 0), signal_confirmed=sig, tradable=net_ok, verdict=verdict)
    print("6. DECISION:", out["decision"])
    bv.jdump(out, "c3_summary.json")


if __name__ == "__main__":
    main()

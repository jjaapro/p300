"""E2 — passive (limit) entry vs market entry, fill-weighted, no survivorship (README §E2)."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import exec_lib as ex

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")

RULES = ("touch", "through")
FALLBACKS = ("skip", "market")


def run_sleeve(sleeve: str, res: str) -> tuple[pd.DataFrame, dict]:
    events = ex.load_events(sleeve)
    path = ex.path_for(events[0].asset, res)
    rows = []
    for k, ev in enumerate(events):
        cx = ex.context(ev, path)
        if cx is None:
            continue
        i0, i1, paper, d = cx["i0"], cx["i1"], cx["paper"], ev.direction
        base = ex.market_trade(ev, path, cx, fill=float(path.o[i0]))   # achievable market: next bar's open
        rows.append(dict(event=k, ts=ev.signal_ts, cell="market", T=0, rule="-", fallback="-", filled=True,
                         fill=base["fill"], impr_bp=d * (paper - base["fill"]) / paper * 1e4, r=base["r"], kind=base["kind"],
                         fill_delay_s=0))
        for T in ex.PATIENCE_S:
            ip = min(path.idx_ge(ev.signal_ts + T), i1)
            for rule in RULES:
                j, f = ex.limit_fill(path, i0, ip, d, paper, rule)
                for fb in FALLBACKS:
                    if j >= 0:
                        t = ex.market_trade(ev, path, cx, fill=f, start=j)
                        rows.append(dict(event=k, ts=ev.signal_ts, cell=f"T{T}_{rule}_{fb}", T=T, rule=rule, fallback=fb,
                                         filled=True, fill=f, impr_bp=d * (paper - f) / paper * 1e4, r=t["r"], kind=t["kind"],
                                         fill_delay_s=int(path.ts[j] - ev.signal_ts)))
                    elif fb == "market" and ip < i1:
                        f2 = float(path.o[ip])
                        t = ex.market_trade(ev, path, cx, fill=f2, start=ip)
                        rows.append(dict(event=k, ts=ev.signal_ts, cell=f"T{T}_{rule}_{fb}", T=T, rule=rule, fallback=fb,
                                         filled=False, fill=f2, impr_bp=d * (paper - f2) / paper * 1e4, r=t["r"], kind="late_" + t["kind"],
                                         fill_delay_s=T))
                    else:
                        rows.append(dict(event=k, ts=ev.signal_ts, cell=f"T{T}_{rule}_{fb}", T=T, rule=rule, fallback=fb,
                                         filled=False, fill=np.nan, impr_bp=np.nan, r=0.0, kind="skipped", fill_delay_s=np.nan))
    df = pd.DataFrame(rows)
    df = df[np.isfinite(df.r)]
    summ = {}
    mkt = df[df.cell == "market"]
    base = ex.summarize_r(mkt.r.to_numpy(), mkt.ts.to_numpy())
    summ["market"] = dict(**base, mean_impr_bp=float(mkt.impr_bp.mean()))
    for cell, g in df[df.cell != "market"].groupby("cell"):
        s = ex.summarize_r(g.r.to_numpy(), g.ts.to_numpy())
        f = g[g.filled]
        summ[cell] = dict(**s, T=int(g["T"].iloc[0]), rule=g.rule.iloc[0], fallback=g.fallback.iloc[0],
                          fill_rate=float(g.filled.mean()), mean_impr_bp_filled=float(f.impr_bp.mean()) if len(f) else float("nan"),
                          median_fill_delay_s=float(f.fill_delay_s.median()) if len(f) else float("nan"),
                          delta_vs_market=s["mean_r"] - base["mean_r"],
                          delta_first_half=s["first_half_mean"] - base["first_half_mean"],
                          delta_second_half=s["second_half_mean"] - base["second_half_mean"])
    return df, summ


def decide(summ: dict) -> dict:
    """README rule 2: cancel->skip, some T <= 15 min, touch rule, delta >= +0.02 R in both halves, fill >= 70 %,
    and the through rule must not flip the sign of the improvement."""
    best = None
    for cell, s in summ.items():
        if cell == "market" or s["fallback"] != "skip" or s["rule"] != "touch" or s["T"] > 900:
            continue
        thr = summ.get(cell.replace("touch", "through"), {})
        ok = (s["delta_first_half"] >= 0.02 and s["delta_second_half"] >= 0.02 and s["fill_rate"] >= 0.70
              and thr.get("delta_vs_market", -1) > 0)
        if ok and (best is None or s["delta_vs_market"] > best[1]["delta_vs_market"]):
            best = (cell, s)
    return dict(maker_entry=bool(best), cell=best[0] if best else None,
                detail={k: best[1][k] for k in ("delta_vs_market", "delta_first_half", "delta_second_half", "fill_rate")} if best else None)


def adx_entry_improvement() -> dict:
    """ADX: limit at the daily close, patience up to 60 min; improvement only (exits are signal-driven)."""
    p1 = ex.load_path("btc_1m")
    rows = []
    for a in ex.load_adx():
        i0 = p1.idx_ge(a["signal_ts"])
        if i0 <= 0 or p1.ts[i0] != a["signal_ts"]:
            continue
        paper = float(p1.c[i0 - 1]); d = a["direction"]
        rec = dict(ts=a["signal_ts"], market_cost_bp=d * (p1.o[i0] - paper) / paper * 1e4)
        for T in ex.PATIENCE_S:
            j, f = ex.limit_fill(p1, i0, p1.idx_ge(a["signal_ts"] + T), d, paper, "touch")
            rec[f"T{T}_filled"] = j >= 0
            rec[f"T{T}_impr_bp"] = d * (paper - f) / paper * 1e4 if j >= 0 else np.nan
        rows.append(rec)
    df = pd.DataFrame(rows)
    out = dict(n=int(len(df)), market_cost_bp_mean=float(df.market_cost_bp.mean()))
    for T in ex.PATIENCE_S:
        out[f"T{T}"] = dict(fill_rate=float(df[f"T{T}_filled"].mean()), mean_impr_bp_filled=float(df[f"T{T}_impr_bp"].mean()))
    return out


def main() -> None:
    out = {"env": ex.env_info(), "sleeves": {}, "decision": {}, "adx": adx_entry_improvement()}
    print("ADX entry:", out["adx"])
    frames = []
    for sl in ex.WALK_SLEEVES:
        df, summ = run_sleeve(sl, "1m")
        df["sleeve"], df["res"] = sl, "1m"
        frames.append(df)
        out["sleeves"][sl] = {"1m": summ}
        dec = decide(summ)
        if df.iloc[0].ts and ex.load_events(sl)[0].asset == "BTC":
            df5, summ5 = run_sleeve(sl, "5s")
            df5["sleeve"], df5["res"] = sl, "5s"
            frames.append(df5)
            out["sleeves"][sl]["5s"] = summ5
            # resolution check on the common events: fill rates at the touch rule, T = 5 min
            common = set(df5.event)
            sub1 = df[(df.event.isin(common)) & (df.cell == "T300_touch_skip")]
            sub5 = df5[df5.cell == "T300_touch_skip"]
            fr1, fr5 = float(sub1.filled.mean()) if len(sub1) else float("nan"), float(sub5.filled.mean()) if len(sub5) else float("nan")
            dec["fill_rate_T300_1m_on_5s_events"], dec["fill_rate_T300_5s"] = fr1, fr5
            dec["resolution_check_pass"] = bool(abs(fr1 - fr5) <= 0.15) if np.isfinite(fr1) and np.isfinite(fr5) else None
            if dec.get("resolution_check_pass") is False:
                dec["maker_entry"] = "INCONCLUSIVE"
        out["decision"][sl] = dec
        m = summ["market"]
        print(f"\n{sl}: market n={m['n']} mean {m['mean_r']:+.3f} R (halves {m['first_half_mean']:+.3f}/{m['second_half_mean']:+.3f})")
        tab = pd.DataFrame({c: dict(T=s["T"], rule=s["rule"], fb=s["fallback"], fill=round(s["fill_rate"], 3), impr_bp=round(s["mean_impr_bp_filled"], 2),
                                    mean_r=round(s["mean_r"], 3), d_vs_mkt=round(s["delta_vs_market"], 3), d_h1=round(s["delta_first_half"], 3),
                                    d_h2=round(s["delta_second_half"], 3), delay_s=s["median_fill_delay_s"])
                            for c, s in summ.items() if c != "market"}).T
        print(tab.sort_values(["fb", "rule", "T"]).to_string())
        print("decision:", dec)
    pd.concat(frames).to_csv(ex.RESULTS / "e2_cells.csv", index=False)
    ex.jdump(out, "e2_passive_entry.json")


if __name__ == "__main__":
    main()

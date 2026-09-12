"""E5 — conditional adverse selection: passive-touch markouts at sleeve signal times vs random times (README §E5)."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import exec_lib as ex

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")

FILL_WINDOW_S = 60
N_RANDOM = 20
SIGNAL_SETS = {"CHENTO_BTC_all": None, "SHORT_SQUEEZE": None, "SQUEEZE_BULL_ALL": None}


def signal_times(name: str) -> list[tuple[int, int]]:
    if name == "CHENTO_BTC_all":
        t = pd.read_csv(ex.ROOT / "studies" / "notebooks" / "overlay_study" / "results_backonly" / "trades_BTC.csv")
        return [(int(pd.Timestamp(r.ts).timestamp()) + 900, 1 if r.direction == "long" else -1) for r in t.itertuples()]
    return [(e.signal_ts, e.direction) for e in ex.load_events(name)]


def markouts(path: ex.PricePath, times: list[tuple[int, int]]) -> pd.DataFrame:
    rows = []
    for ts, d in times:
        i0 = path.idx_ge(ts)
        if i0 <= 0 or i0 >= len(path.ts) or path.ts[i0] != ts:
            continue
        limit = float(path.c[i0 - 1])
        j, f = ex.limit_fill(path, i0, path.idx_ge(ts + FILL_WINDOW_S), d, limit, "touch")
        rec = dict(ts=ts, d=d, filled=j >= 0)
        if j >= 0:
            for k in ex.MARKOUT_S:
                jj = j + k // path.step
                rec[f"mo_{k}"] = d * (float(path.c[jj]) - f) / f * 1e4 if jj < len(path.ts) else np.nan
        rows.append(rec)
    return pd.DataFrame(rows)


def main() -> None:
    p5 = ex.load_path("spot_5s")
    t_lo, t_hi = int(p5.ts[0]) + 86400, int(p5.ts[-1]) - 2 * 3600
    rng = np.random.default_rng(ex.SEED)
    days = np.arange(t_lo // 86400, t_hi // 86400)
    out = {"env": ex.env_info(), "window": [ex.iso(t_lo), ex.iso(t_hi)], "fill_window_s": FILL_WINDOW_S, "sets": {}}
    for name in SIGNAL_SETS:
        times = [(t, d) for t, d in signal_times(name) if t_lo <= t <= t_hi]
        if not times:
            out["sets"][name] = dict(n_signals=0); continue
        cond = markouts(p5, times)
        rand_times = []
        for t, d in times:
            for day in rng.choice(days, N_RANDOM, replace=True):
                rand_times.append((int(day) * 86400 + t % 86400, d))
        unc = markouts(p5, rand_times)
        res = dict(n_signals=int(len(times)), n_cond_filled=int(cond.filled.sum()), n_rand=int(len(unc)),
                   fill_rate_cond=float(cond.filled.mean()), fill_rate_rand=float(unc.filled.mean()))
        for k in ex.MARKOUT_S:
            a = cond.loc[cond.filled, f"mo_{k}"].dropna(); b = unc.loc[unc.filled, f"mo_{k}"].dropna()
            ba = ex.block_boot_mean(a.to_numpy(), ex.day_groups(cond.loc[a.index, "ts"].to_numpy()))
            bb = ex.block_boot_mean(b.to_numpy(), ex.day_groups(unc.loc[b.index, "ts"].to_numpy()))
            # difference CI: independent day-block bootstraps combined (conservative, no pairing)
            res[f"mo_{k}"] = dict(cond_mean=ba["mean"], cond_ci90=ba["ci90"], rand_mean=bb["mean"], rand_ci90=bb["ci90"],
                                  diff=ba["mean"] - bb["mean"],
                                  diff_ci90=[ba["ci90"][0] - bb["ci90"][1], ba["ci90"][1] - bb["ci90"][0]])
        out["sets"][name] = res
        print(name, {k: v for k, v in res.items() if not isinstance(v, dict)})
        for k in ex.MARKOUT_S:
            m = res[f"mo_{k}"]
            print(f"   markout +{k}s: cond {m['cond_mean']:+.2f} bp {[round(x, 2) for x in m['cond_ci90']]} | random {m['rand_mean']:+.2f} {[round(x, 2) for x in m['rand_ci90']]} | diff {m['diff']:+.2f} {[round(x, 2) for x in m['diff_ci90']]}")
    ex.jdump(out, "e5_conditional_as.json")


if __name__ == "__main__":
    main()

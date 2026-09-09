"""Two figures for the S-Hawkes note. Reads only the JSON artefacts already written.

Run: python studies/notebooks/hawkes_note_results/make_figs.py
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

OUT = Path(__file__).resolve().parent
main = json.loads((OUT / "hawkes_note_results.json").read_text(encoding="utf-8"))
add = json.loads((OUT / "addendum_slow_rate.json").read_text(encoding="utf-8"))
W = [1, 4, 12, 24, 72]
keys = [f"{w}h" for w in W]

fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))

# --- panel 1: Fano ladder, BTC 1m stream ----------------------------------
s4 = main["S4_BTC_1M"]["ladder"]
ax[0].plot(W, [s4[k]["fano"] for k in keys], "o-", lw=2, color="black", label="observed")
ax[0].plot(W, [s4[k]["null_shuffle_mean"] for k in keys], "s--", color="tab:orange",
           label="N1 inter-arrival shuffle (renewal)")
ax[0].plot(W, [s4[k]["null_poisson_mean"] for k in keys], "^--", color="tab:green",
           label="N2 homogeneous Poisson")
n3 = add["S4_BTC_1M"]["null_N3"]
ax[0].plot(W, [n3["smooth_24h"][k]["mean"] for k in keys], "v:", color="tab:blue",
           label="N3 Poisson, rate smooth on 24h")
ax[0].plot(W, [n3["smooth_168h"][k]["mean"] for k in keys], "d:", color="tab:purple",
           label="N3 Poisson, rate smooth on 168h")
s2 = main["S2_OI_FLUSH"]["ladder"]
ax[0].plot(W, [s2[k]["fano"] for k in keys], "o-", lw=1.5, color="tab:red",
           label="observed: OI flush stream")
ax[0].set_xscale("log"); ax[0].set_yscale("log")
ax[0].set_xticks(W); ax[0].set_xticklabels([f"{w}h" for w in W])
ax[0].set_xlabel("bin width"); ax[0].set_ylabel("Fano factor  Var/Mean")
ax[0].set_title("Index of dispersion vs bin width\n(no plateau = slow rate drift, not a Hawkes kernel)",
                fontsize=9)
ax[0].legend(fontsize=6.5, loc="upper left")
ax[0].grid(alpha=0.3, which="both")

# --- panel 2: ACF of hourly counts ----------------------------------------
a = main["S4_BTC_1M"]["acf_hourly"]
lags = np.arange(1, 25)
obs = [a[f"lag{i}"]["acf"] for i in lags]
lo = [a[f"lag{i}"]["null_lo"] for i in lags]
hi = [a[f"lag{i}"]["null_hi"] for i in lags]
ax[1].fill_between(lags, lo, hi, color="tab:orange", alpha=0.3,
                   label="N1 shuffle 95% band")
ax[1].plot(lags, obs, "o-", color="black", label="BTC 1m |r|>=50bp")
a2 = main["S2_OI_FLUSH"]["acf_hourly"]
ax[1].plot(lags, [a2[f"lag{i}"]["acf"] for i in lags], "o-", color="tab:red",
           label="OI flush (rising edge)")
pl = add["S4_BTC_1M"]["acf_powerlaw"]
ax[1].plot(lags, np.exp(np.log(lags) * pl["slope"] + np.log(obs[0])), "--",
           color="tab:blue", lw=1,
           label=f"power law slope {pl['slope']:.2f} (R2 {pl['r2']:.2f})")
ax[1].axhline(0, color="grey", lw=0.8)
ax[1].set_xlabel("lag (hours)"); ax[1].set_ylabel("autocorrelation of hourly counts")
ax[1].set_title("ACF of hourly event counts, lags 1-24", fontsize=9)
ax[1].legend(fontsize=7)
ax[1].grid(alpha=0.3)

fig.tight_layout()
fig.savefig(OUT / "hawkes_note_fig.png", dpi=140)
print("wrote", OUT / "hawkes_note_fig.png")

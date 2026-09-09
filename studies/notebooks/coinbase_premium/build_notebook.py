"""Build the viewer notebook for the S-Coinbase-premium study (Run B).

Run with the SYSTEM python (the repo venv has no nbformat):

    C:/Python/Python313/python.exe studies/notebooks/coinbase_premium/build_notebook.py

Writes coinbase_premium.ipynb next to this file. The notebook only READS the
artefacts already in results/ -- it recomputes nothing, so it cannot disagree
with findings.md.
"""
from __future__ import annotations

import json
from pathlib import Path

import nbformat as nbf

HERE = Path(__file__).resolve().parent
OUT = HERE / "coinbase_premium.ipynb"


def md(text: str) -> nbf.NotebookNode:
    return nbf.v4.new_markdown_cell(text)


def code(src: str) -> nbf.NotebookNode:
    return nbf.v4.new_code_cell(src)


SETUP = '''\
import json, pandas as pd
from pathlib import Path
pd.set_option("display.width", 190)
pd.set_option("display.max_columns", 60)
pd.set_option("display.float_format", lambda v: f"{v:,.4f}")

R = Path.cwd() / "results"
if not R.exists():                       # notebook opened from the repo root
    R = Path("studies/notebooks/coinbase_premium/results")

report = json.loads((R / "report.json").read_text(encoding="utf-8"))
trades = pd.read_csv(R / "trades.csv")
print("verdict:", report["verdict"])
print("cost:", report["cost_rt_bp"], "bp round trip")
print("rule:", report["rule"])
'''

CLAUSES = '''\
c = report["clauses"]
rows = [
    dict(clause="C1  ETH bootstrap CI on mean R",
         rule=c["C1_eth_boot_ci_mean_R"]["rule"],
         measured=f'n={c["C1_eth_boot_ci_mean_R"]["n"]}  '
                  f'mean R={c["C1_eth_boot_ci_mean_R"]["point"]:+.4f}  '
                  f'CI95=[{c["C1_eth_boot_ci_mean_R"]["ci95_lo"]:+.4f}, '
                  f'{c["C1_eth_boot_ci_mean_R"]["ci95_hi"]:+.4f}]',
         evaluable=c["C1_eth_boot_ci_mean_R"]["evaluable"],
         fired=c["C1_eth_boot_ci_mean_R"]["fires"]),
    dict(clause="C2  BTC 2025+ mean R",
         rule=c["C2_btc_2025plus_mean_R"]["rule"],
         measured=f'n={c["C2_btc_2025plus_mean_R"]["n"]}  '
                  f'mean R={c["C2_btc_2025plus_mean_R"]["mean_R"]:+.4f}',
         evaluable=c["C2_btc_2025plus_mean_R"]["evaluable"],
         fired=c["C2_btc_2025plus_mean_R"]["fires"]),
    dict(clause="support  ETH DSR at N_TRIALS=1",
         rule="BUILD needs DSR > 0.95",
         measured=f'DSR={c["support_eth_dsr"]["dsr"]:.4f}',
         evaluable=True, fired=not c["support_eth_dsr"]["passes"]),
]
display(pd.DataFrame(rows))
print("\\nVERDICT:", report["verdict"])
'''

HEADLINE = '''\
keys = ["n_trades","mean_R","sum_R","win_rate","pf","sr_per_trade","max_dd_R",
        "sl_rate","mean_pct","trades_per_year","n_trials"]
h = pd.DataFrame({a: {k: report[a][k] for k in keys} for a in ("BTC","ETH")}).T
h["dsr"] = [report[a]["dsr"]["dsr"] for a in ("BTC","ETH")]
h["boot_ci95_lo"] = [report[a]["boot_mean_R"]["ci95_lo"] for a in ("BTC","ETH")]
h["boot_ci95_hi"] = [report[a]["boot_mean_R"]["ci95_hi"] for a in ("BTC","ETH")]
h["p_mean_R_gt_0"] = [report[a]["boot_mean_R"]["p_positive"] for a in ("BTC","ETH")]
display(h)
'''

ERA = '''\
print("Per-trade R by era (split at the BTC spot-ETF approval, 2024-01-11)\\n")
display(pd.read_csv(R / "trade_era_split.csv"))
print("\\nPer-trade R by calendar year\\n")
display(pd.read_csv(R / "per_year.csv"))
'''

EQUITY = '''\
eq = pd.read_csv(R / "equity_curve.csv", parse_dates=["entry_dt"])
try:
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 4.5))
    for a, g in eq.groupby("asset"):
        ax.plot(g["entry_dt"], g["equity_R"], label=a, lw=1.4)
    ax.axvline(pd.Timestamp("2024-01-11"), color="k", ls="--", lw=1,
               label="BTC spot ETF")
    ax.axhline(0, color="grey", lw=0.8)
    ax.set_ylabel("cumulative R"); ax.set_xlabel("entry")
    ax.set_title("Coinbase premium z>=+2, long 48h, 2xATR stop, 18bp — cumulative R")
    ax.legend(); fig.tight_layout(); plt.show()
except ImportError:
    print("matplotlib not installed — table instead")
    display(eq.groupby("asset")["equity_R"].agg(["first", "last", "min", "max"]))
'''

PREMIUM = '''\
print("Premium by year, basis points of the Binance price\\n")
display(pd.read_csv(R / "premium_by_year.csv"))
print("\\nPremium either side of the BTC spot-ETF approval\\n")
display(pd.read_csv(R / "premium_era_decay.csv"))
'''

MONO = '''\
print("Forward 48h return by z-decile — every bar, NO threshold, no stop, no cost\\n")
dec = pd.read_csv(R / "z_decile_forward48h.csv")
display(dec.pivot(index="decile", columns="asset", values="mean_fwd48_bp"))
print("\\nCorrelations and the z>=2 excess over unconditional drift, by era\\n")
m = pd.DataFrame(report["monotonicity"]).set_index("asset")
display(m[["pearson","spearman","pearson_nonoverlap","spearman_nonoverlap",
           "n","n_nonoverlap"]])
display(m[["uncond_fwd48_pre_bp","z_ge_2_fwd48_pre_bp","excess_pre_bp",
           "uncond_fwd48_post_bp","z_ge_2_fwd48_post_bp","excess_post_bp"]])
'''

DIAG = '''\
print("DIAGNOSTIC ONLY — same fires, stop removed. NOT the pre-registered rule,")
print("NOT a decision input, NOT a recommendation.\\n")
display(pd.DataFrame({a: report[a]["diag_nostop"] for a in ("BTC","ETH")}).T)
print("\\nExit-reason mix of the actual (stopped) rule:\\n")
display(trades.groupby(["asset","exit_reason"]).size().unstack(fill_value=0))
'''

TRADES = '''\
print("Ten largest and ten worst trades by R\\n")
cols = ["asset","entry_dt","exit_dt","z","premium_bp","bars_held",
        "exit_reason","trade_R","ret_net"]
display(trades.nlargest(10, "trade_R")[cols])
display(trades.nsmallest(10, "trade_R")[cols])
'''


def build() -> None:
    nb = nbf.v4.new_notebook()
    nb.cells = [
        md("# S-Coinbase-premium — viewer (Run B, 2026-09-09)\n\n"
           "**STATUS: CONCLUDED KILL per pre-registration.**\n\n"
           "Frozen rule: premium = (Coinbase close − Binance close) / Binance close on "
           "the hourly grid, spot against spot; 14-day (336h) trailing z-score; fire at "
           "**z ≥ +2.0**; long at the firing bar's Binance close; stop at **2 × ATR14**; "
           "time exit at **+48h**; one fire per 48h; **18 bp** round trip. "
           "`R` = the stop distance (2 × ATR14).\n\n"
           "This notebook only reads `results/` — it recomputes nothing. Re-generate "
           "those artefacts with `python analysis.py` (repo venv).\n\n"
           "> **Two things to read before any number here:**\n"
           "> 1. **ETH is the genuine out-of-sample test.** BTC is a carry-over: this "
           "exact cell `(2.0, 48h, 336h)` was the training-set argmax of a 24-config "
           "grid in the predecessor repo, so BTC is deflated at N_TRIALS = 24 and "
           "carries no independent evidence.\n"
           "> 2. **A prior independent run of this same rule (Run A, 2026-09-08) "
           "already existed in this folder.** It agrees with Run B bit-for-bit on BTC "
           "and on the deciding clause C2, and differs on ETH by 8 bars out of 58,579 "
           "— enough to flip clause C1. See §2 of `findings.md`."),
        code(SETUP),
        md("## 1. Clause table — the decision\n\n"
           "Either KILL clause firing kills the rule. **C2 fires.**"),
        code(CLAUSES),
        md("## 2. Headline numbers\n\n"
           "BTC's DSR is computed at N_TRIALS = 24 (the predecessor's declared budget), "
           "ETH's at N_TRIALS = 1 (never tested on this rule anywhere)."),
        code(HEADLINE),
        md("## 3. The edge is entirely pre-2024, on both assets\n\n"
           "ETH — never fitted, no threshold ever chosen on it — is positive 2020–2023 "
           "and dead from 2024, the same year BTC dies."),
        code(ERA),
        code(EQUITY),
        md("## 4.1 The premium itself decayed, twice\n\n"
           "Dispersion collapsed ~3.5× (16.6 bp sd in 2020 → 4.7 bp in 2025) *and* the "
           "level flipped negative (+2.90 bp pre-ETF → −1.64 bp post; −6.0 bp in 2026 "
           "with only 17.8% of hours positive). A z-score is scale-free, so the rule "
           "keeps firing at the same rate on progressively smaller real dislocations "
           "while the 18 bp cost does not shrink."),
        code(PREMIUM),
        md("## 4.2 Monotonicity — the pre-registered red flag is ABSENT\n\n"
           "The pre-registration warned that a signal working only at the +2 cut with no "
           "monotone relationship would be a red flag. It is monotone across essentially "
           "the whole z range on both assets, and the sign survives on the honest "
           "non-overlapping sample. So the premium *does* carry weak real information — "
           "and the frozen rule still loses money post-ETF. The excess over drift fell "
           "4.9× on BTC, to +27 bp against an 18 bp round trip."),
        code(MONO),
        md("## 5. Why the rule loses even though the signal informs\n\n"
           "The 2 × ATR14 stop sits inside the 48-hour noise band: a majority of trades "
           "hit it, costing ~60% of gross expectancy. **Recorded as attribution only.** "
           "Removing the stop is an unregistered post-hoc parameter change to a rule "
           "already through a 24-cell selection; it would need its own pre-registration "
           "and its own untouched asset — and BTC and ETH are both spent."),
        code(DIAG),
        md("## 6. Trade tape"),
        code(TRADES),
        md("---\n\n### Verdict\n\n"
           "**CONCLUDED KILL per pre-registration.** C2 fires: BTC entries from "
           "2025-01-01 number 68 (≥ 20) with mean R = −0.3191 (≤ 0). C1 does not fire "
           "in Run B but fires in Run A on an 8-bar aggregation difference, so it is "
           "not informative in either direction.\n\n"
           "**Recommendation: do not build.** Recommendation only — the user decides. "
           "No sleeve or bot was created; nothing under `strategies/**` or `bots/**` "
           "was touched; `prod.db` was read-only throughout.\n\n"
           "Full discussion, deviations and limitations: `findings.md`."),
    ]
    nb.metadata["kernelspec"] = {"display_name": "Python 3", "language": "python",
                                 "name": "python3"}
    nbf.write(nb, str(OUT))
    print(f"wrote {OUT} ({len(nb.cells)} cells)")


if __name__ == "__main__":
    build()

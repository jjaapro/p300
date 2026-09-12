STATUS: CONCLUDED — KILL as a standalone sleeve candidate (per-trade t 1.40 < 1.5; Sharpe ≥ 0.5 in 79 % of phases < 80 %); a positive but noisy edge whose pair with BTC ADX adds no MAR

# ADX Tier-2 machine on ETH — N1

**Tag: new-sleeve candidate test, N_TRIALS = 1. Pre-registration: [README.md](README.md), frozen before any
run.** Written 2026-09-12 from `results/log_n1.txt` and `results/n1_eth.json`. The shipped BTC configuration
applied to ETH unchanged, live semantics (`adx_robustness_2026_09/adx_lib.live_walk`), `eth_1m` 2020-01 →,
ETHUSDT settlement funding, 15 bp.

## Result

| | live phase 0 | 24 phases [min, median, max] |
|---|---|---|
| closed trades | 19 | 18 – 23 |
| mean net trade | +14.6 % (13 longs +19.4 %, 6 shorts +4.3 %) | +3.8 – +17.8 % |
| per-trade t | **1.40** | 1.07 – 2.40 (median 1.47) |
| MTM CAGR | 25.2 % | 7.2 – 40.1 % (median 18.5 %) |
| MTM Sharpe | **0.72** | 0.38 – 0.90 (median 0.60); ≥ 0.5 in **79 %** of phases |
| MTM maxDD | −54.3 % | −65.7 – −44.8 % |
| MAR | 0.46 | 0.14 – 0.74 |
| halves (net per trade) | +23.4 % / +4.9 % | — |
| funding paid | −41.1 pp over the ledger | — |
| DSR at N = 1 (daily curve) | 0.96 | — |

Correlation of daily MTM returns with the BTC machine (phase 0): **0.47** on the 1,118 days either is in the
market; both in the market on 18 % of days. Equal-weight BTC+ETH pair: CAGR 33.0 %, MTM maxDD −43.7 %, Sharpe
0.97, MAR 0.75 — against BTC alone 36.0 % / −48.1 % / 1.02 / 0.75. The pair trims the drawdown by 4 pp and
gives back 3 pp of CAGR; MAR is unchanged.

## Decision rule applied

Sharpe ≥ 0.7 at the live phase ✓ (0.72); Sharpe ≥ 0.5 in ≥ 80 % of phases ✗ (79 %, one phase short); MTM
maxDD ≥ −55 % ✓ (−54.3 %); per-trade t ≥ 1.5 ✗ (1.40); correlation < 0.7 ✓ (0.47). Two clauses fail →
**KILL** as a standalone sleeve. Two of the three passes are at the edge of their thresholds, and the phase
spread (Sharpe 0.38–0.90) is wider than BTC's (0.63–1.12): the machine works on ETH in the same way it works
on BTC, only with a weaker and less stable trend regime, and 2020 → 2026 is the sample on which the BTC version
was itself calibrated.

## What this changes

Nothing. The C4 side result (ETH shorts +8.7 %) does not survive the shipped configuration and live pricing
(shorts +4.3 % on 6 trades). The dormant JPLUS_ETH_DAILY sleeve remains the ETH trend expression on file; the
diversification an ETH ADX would add to BTC ADX is measured at zero MAR. As with BTC, funding is a large part
of the ETH ledger's drag (−41 pp) and the same netting/venue argument applies if the idea is ever revisited.

## Reproduce

`run_n1_eth.py`, then `C:/Python/Python313/python.exe build_notebook.py` → `adx_eth.ipynb`. Reads the
execution study's `eth_1m` cache and the ADX robustness pack's BTC daily returns. Runtime ≈ 5 minutes.

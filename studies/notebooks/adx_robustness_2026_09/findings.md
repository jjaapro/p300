STATUS: CONCLUDED — (a) live semantics reproduce the harness once funding is excluded; the whole live-vs-research gap is perp funding on the longs (−7 pp CAGR, −10 pp MTM drawdown); (b) at shipped sizing (≈ 0.2× notional) the account MTM drawdown is ≈ −10 % in the worst of 24 phases; (c) phase ensembles: NO CHANGE; (d) late entries cost ≤ 0.5 pp per trade, no grace window needed; (e) ADX longs paid 24.7 %/yr of long exposure in funding, netted by CARRY's perp short on 97 % of long-days

OUTCOME (2026-09-12, user go-ahead): no ADX parameter, sizing, ensemble or grace-window change (all NO CHANGE by the pre-registered rules); (e) became pool-plan decision D8 — ADX and CARRY in one cross-margin account, or ADX longs on spot; `adx_study/harness.run` gained `with_funding=True` so no harness-vs-live comparison is quoted without funding again; the close now books the measured 10 + 1 bp (docs/calibration/adx.md).

# ADX robustness pack 2026-09

**Tag: AUDIT + one policy decision (c). Pre-registration: [README.md](README.md), frozen before any run.**
Written 2026-09-12 from `results/log_p1*.txt` and `results/*.json`. Shipped Tier-2 parameters unchanged (the
funding-crowding veto is not modelled, as in the adx_study parity table). One deviation from the README: the
common window is **2020-01-01 → 2026-09-12** (2,400 days), because `btc_1m` — the live stop-check table —
begins 2020-01-01; the parity gate still runs the harness on its own 2018 → 2026-06-25 window.

## (a) Harness fill model vs live semantics

Parity gate: the harness reproduces the adx_study Tier-2 table exactly (n 27, +2483 %, −15.1 %, MAR 3.09).
Port check: every harness entry on the common window is an entry signal of the ported state machine (22 / 22).

| fill model (2020-01 →, 21 trades each) | cost | mean trade | CAGR | MTM Sharpe | MTM maxDD | MAR | exits |
|---|---|---|---|---|---|---|---|
| harness: daily bars, stop at level, same-bar trail, entry at daily close | 10 bp, no funding | +16.1 % | 43.0 % | 1.19 | −38.0 % | 1.13 | 12 ADX / 5 trail / 4 SL |
| live: 1 m stop_path, next-day trail, entry at first minute | 15 bp, no funding | — | **44.2 %** | **1.20** | **−38.4 %** | **1.15** | — |
| live, with Binance settlement funding | 15 bp + funding | **+14.5 %** | **36.0 %** | **1.02** | **−48.1 %** | **0.75** | 12 ADX / 6 trail / 3 SL |

The fill semantics are not the gap: minute-resolution stops, the anti-look-ahead trail and the 5 bp extra cost
net to +1 pp of CAGR. **Funding is the gap**: −7 pp of CAGR, −10 pp of drawdown, −0.38 of MAR. The research
harness is funding-blind and the sleeve pays it; the validation audit's "optimistic by 5 bp/trade plus the
funding stream" is 2.7 pp per long trade in practice (see (e)).

## (b) Mark-to-market drawdown across the 24 day boundaries → sizing

Live semantics with funding, 2020-01 →: CAGR 18.2–44.0 % (median 28.3 %), MTM Sharpe 0.63–1.12 (median
0.84), **MTM maxDD −36.0 to −49.9 % (median −41.6 %)**, MAR 0.38–1.13, 19–25 trades. The live phase 0 sits
near the top on CAGR (36.0 %) and near the bottom on drawdown (−48.1 %).

Sizing: the bot risks 2 % over the initial stop, and the initial stop is the 10 % SL in every trade (the
4×ATR seed is wider), so the shipped notional is **0.2× capital**. The −48 % curve drawdown is therefore a
**−9.6 % account drawdown** at the live phase and −10.0 % in the worst phase — inside a 25 % budget with 2.5×
headroom (the notional that would spend the budget in the worst phase is 0.50×, i.e. `RISK_PCT` ≈ 5 %). The
MTM-vs-trade-close distinction (−48 % vs −15 %) matters for how the curve is described, not for the bot's
account risk as sized today. No sizing change is implied.

## (c) Phase ensembles — NO CHANGE

| ensemble | CAGR | MTM maxDD | Sharpe | MAR | ΔSharpe vs phase 0 (30-day block CI90) | ΔMAR |
|---|---|---|---|---|---|---|
| k = 2 {0, 12} | 32.2 % | −38.3 % | 1.03 | 0.84 | +0.01 [−0.21, +0.25] | +0.09 [−0.46, +0.53] |
| k = 3 {0, 8, 16} | 29.1 % | −36.8 % | 0.93 | 0.79 | −0.09 [−0.34, +0.17] | +0.04 [−0.66, +0.35] |
| k = 24 (limit) | 27.9 % | −34.5 % | 0.93 | 0.81 | −0.09 [−0.40, +0.22] | +0.06 [−0.72, +0.44] |

Averaging phases removes the phase lottery (drawdown −34.5 % at the limit vs −41.6 % median, −49.9 % worst) but
no k ≤ 3 ensemble beats the median single-phase drawdown by the pre-set 5 pp (k = 3 misses by 0.2 pp), and
the paired bootstraps of Sharpe and MAR are centred on zero. Rule (c): NO CHANGE; the operational cost of k
sub-positions is not justified by these numbers.

## (d) Late entry — current behaviour is fine

Same trades with the entry taken h hours after the day boundary (exits unchanged): −0.24 pp (1 h), −0.28
(2 h), **−0.48 (6 h)**, −0.72 (12 h), −0.61 pp (23 h) per trade; no trade would have been stopped before a late
entry. Re-running the whole machine with a delayed entry tick: CAGR 36.0 → 34.3 % (6 h) → 33.9 % (23 h), MTM
drawdown unchanged. Within the ±0.5 pp clause at 6 h: keep the sleeve's enter-any-time-that-day behaviour and
state in the calibration note that the backtest assumes entry at the boundary. No grace window.

## (e) Funding and venue — the finding that matters

14 long trades paid **37.4 pp** of funding in total, **2.67 pp per trade** (max 9.3 pp), every long paid;
that is **24.7 %/yr of long exposure** (554 long-days, mostly 2021 and 2024-25 bull stretches). The 7 shorts
received 2.6 pp. On **96.6 % of ADX long-days S-078 CARRY's perp short was on** (CARRY is on 90 % of all
days), so in a shared account the two perp legs net and the pair is a spot long paying no funding: the
sleeve's funding drag is a bookkeeping artefact of separate $10 k bots, not a portfolio cost — *if* the legs
sit in one account. Expressing the long leg on spot instead costs 10 bp more per round trip against 267 bp of
funding per trade: spot was cheaper in 12 of 14 longs.

## What this changes (for the user's decision; nothing implemented)

1. **Treat funding, not fill semantics, as the ADX sleeve's execution problem.** In one cross-margin account
   with CARRY, the ADX long's funding nets against CARRY's short; as separate paper bots each books its own
   funding and the ADX ledger shows −7 pp CAGR that the portfolio would not pay. The pool design (Continuous
   pool holds CARRY, Standard pool holds ADX) currently separates them — that separation costs the funding.
   Alternatives: express ADX longs on spot (10 bp extra, 0 funding) or account for the netting explicitly.
2. **Sizing**: no change; the account drawdown at 0.2× notional is ≈ −10 % across phases.
3. **Phase ensemble**: no change.
4. **Late entry**: no grace window; document the boundary-entry assumption.
5. **Harness**: the adx_study harness should charge funding (it has the settlement rows available) before its
   numbers are compared with live again; `bv_lib.ledger_to_daily_returns` accepts a funding map already.

## Reproduce

`run_p1a_live_semantics.py` → `run_p1bc_phases.py` → `run_p1d_late_entry.py` → `run_p1e_funding.py`, then
`C:/Python/Python313/python.exe build_notebook.py` → `adx_robustness.ipynb`. Reads the execution study's
1 m cache and the brainstorm study's cached Binance funding. Runtime ≈ 6 minutes (24 phases).

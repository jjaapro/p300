# ADX Tier-2 machine on ETH — pre-registration (frozen before any result)

Written 2026-09-12 before `run_n1_eth.py` produced a number. Track N1 of the approved brainstorm follow-up
plan. Tag: new-sleeve candidate test, **N_TRIALS = 1**: the shipped BTC Tier-2 configuration is applied to ETH
with no parameter changed (ADX(14) 20/25, EMA(50) direction, symmetric EMA(150) gate, 10 % SL, ATR(14)×4
ratcheting trail, 15 bp, Binance ETHUSDT settlement funding), priced with the live semantics of the ADX
robustness pack (`adx_robustness_2026_09/adx_lib.live_walk`).

## Why

The only positive out-of-sample transfer in the brainstorm validation was the ADX machine on ETH daily (C4
side run, baseline machine: 11 longs +18.9 %, 7 shorts +8.7 %). A trend-regime machine is asset-generic; ETH
data is on disk (`eth_1m` 2020-01 →). The question is whether the *shipped* machine, unchanged, is a sleeve
candidate on ETH and whether it adds anything beyond BTC ADX.

## Data

`eth_1m` (Binance spot 1 m, 2020-01-01 →) aggregated to daily bars at each of the 24 day boundaries
(`bv_lib.daily_from_1m_sql`), the same 1 m table for stops and marks, `fetch_binance_funding("ETHUSDT")`.
Trading starts when the 150-day EMA exists (mid-2020); the BTC machine's live-semantics daily returns from
the robustness pack on the same days for the correlation.

## Statistics

Per phase: closed trades, mean net %, per-trade t, MTM CAGR, MTM Sharpe, MTM maxDD, MAR (2020 → now). At the
live phase (0): the ledger, both halves (split at the median entry time), Pearson correlation of daily MTM
returns with the BTC machine (phase 0), and the BTC+ETH equal-weight curve. Deflated Sharpe at N = 1 on the
daily curve (`studies/lib/validation.dsr_pbo`).

## Decision rule (fixed now)

**BUILD-CANDIDATE** only if ALL of: live-phase MTM Sharpe ≥ 0.7; MTM Sharpe ≥ 0.5 in ≥ 80 % of the 24 phases;
live-phase MTM maxDD ≥ −55 %; per-trade t ≥ 1.5 at the live phase; daily-return correlation with BTC ADX
< 0.7 (else it is a BTC clone). Otherwise **KILL**. Stated up front: the dormant JPLUS_ETH_DAILY sleeve
overlaps this idea, and a new sleeve costs a pool slot.

## Run order

`run_n1_eth.py`, then `C:/Python/Python313/python.exe build_notebook.py` → `adx_eth.ipynb`.

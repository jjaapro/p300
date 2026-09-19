# SQUEEZE_BULL on ETH — findings

**CONCLUDED 2026-09-19. By the letter of the frozen rule: DESCRIPTIVE. By every clause that would have decided:
KILL.** The shipped rule — the frozen June detector and replay run unchanged on ETH hourly bars and close-of-hour
open interest — fires 174 times in ETH's bull regime over 2022-02 → 2026-09 and loses **−0.163 R per fire** at the
June 18 bp (−0.123 R at 10 bp): win rate 38.5 %, cumulative −28.4 R, both halves negative (−0.12 / −0.21), every
calendar year negative (2022 −0.11, 2023 −0.12, 2024 −0.27, 2025 −0.09, 2026 −0.31), every regime negative (bear
−0.156, flat −0.138, bull −0.163 — the bull gate that carries BTC does nothing on ETH), OOS (2026-04-14 →) n 12 at
−0.089 R, DSR 0.04. Pre-registered in [README.md](README.md), frozen before any ETH number
(`results/freeze_F0.json`), one outcome run (`results/report.json`), reviewed in
[squeeze_bull_eth.ipynb](squeeze_bull_eth.ipynb). Nothing in production changes; no ETH twin, no ETH open-interest
feed.

## Why the verdict reads DESCRIPTIVE, and why that does not rescue ETH

The fidelity gate had two conditions: the panel-built BTC run must reproduce the reference ledger's bull-gated
fires (**Jaccard ≥ 0.80 — measured 0.871**, 115 of 126 / 121 fires shared; the non-shared ones are the archive
open-interest series sitting on the other side of the −2 % threshold from CoinDesk's, as expected), and the shared
fires must replay to **1e-9** — which failed on **one fire of 115** (2024-02-14 18:00, a time-stop exit, 0.034 R
apart; the other 114 agree exactly). The cause is a data hole, not the builder: the perpetual 1-minute panel has no
present minute for seven hours on 2024-02-16 13:00–19:00 UTC, prod's hourly table carries CoinDesk rows there, so
the 48-bar window of that fire ends seven calendar hours later in the panel run. The 1e-9 tolerance was written on
the assumption that the hourly sources are byte-identical; they are not on every hour (the opens also differ by
one tick, harmlessly). The decision code applies the gate as frozen, so the report says DESCRIPTIVE. Read the
substance: on 114 of 115 shared fires the replay is exact, and the ETH ledger was built by the same code.

What the deciding clauses read on that ledger: KILL by the full-sample clause (mean R −0.163 ≤ 0 at n 174) and by
the OOS clause (n 12, mean R −0.089 ≤ 0); (b) MAR negative; (c) both halves negative. There is no reading of the
numbers under which ETH is a candidate. No re-run was made and the gate is not amended after the fact; the honest
verdict is recorded here beside the formal one.

## The three ledgers

| run | bull-gated fires | mean R (18 bp) | mean R (10 bp) | win | cum R | max DD | MAR | halves | OOS n / mean R |
|---|---|---|---|---|---|---|---|---|---|
| BTC reference (prod tables, corrected OI) | 122 | **+0.215** | — | 57 % | +26.2 | −4.2 | 1.09 | +0.14 / +0.29 | 10 / +0.23 |
| BTC panel-built (this builder) | 126 | +0.191 | +0.231 | 56 % | +24.1 | −4.5 | 0.99 | +0.14 / +0.24 | 11 / +0.19 |
| **ETH panel-built** | **174** | **−0.163** | −0.123 | 38.5 % | **−28.4** | −35.8 | −0.17 | **−0.12 / −0.21** | 12 / −0.09 |

(The MAR here is annual R over max drawdown on the bull-gated resolved fires from 2022-01-30, the shipped causal
regime; the revalidation's clause (b) MAR of 1.59 was on its combined OI + fCVD portfolio and is not this number.)
ETH exits: 105 stops, 58 targets, 11 time stops — the stop is hit 60 % of the time; on BTC the same rule stops out
about 40 %. Pooled across regimes ETH loses on 509 fires (−0.152 R); the flush signal fires more often on ETH than
on BTC (509 vs 426 in the same years on a similar frame) and pays nowhere.

## Reading

The mechanism the BTC sleeve buys — a leveraged-long flush in a bull regime that bounces because forced sellers are
done — does not produce a bounce on ETH at this resolution: ETH's flushes are as frequent as BTC's and their
open-interest signature is the same (the rule triggers on the same shape), but the next 48 hours on ETH continue
down often enough that the −2 % stop wins 60 : 40 against the +3 % target. The regime gate that separates BTC's
bull-tape bounces from its bear-tape continuation has no such separation on ETH: bear −0.156, flat −0.138, bull
−0.163. Whether ETH's forced flow is thinner, its leverage differently distributed, or its price simply less
mean-reverting after a flush, the study cannot say; the rule as shipped does not transfer.

## What this closes

SQUEEZE_BULL on ETH at the shipped rule — the last second-asset study on the roadmap. Not re-proposed without a
different mechanism statement (an ETH-specific flush definition would be a new study, not a twin). No ETH
open-interest feed is needed for anything now running. The panel-built builder stands as the route for any future
hourly open-interest study on ETH (`ss_eth_lib.oi_hourly`, `sqb_eth_lib.hourly_frame`); its known limit is the
1-minute panel's missing hours, which prod's hourly table fills.

# ETH/BTC regime spread and hedged expressions — findings

**CONCLUDED 2026-09-19. Part A: KILL. Part B: KEEP UNHEDGED.** Pre-registered in [README.md](README.md), frozen
before any number (`results/freeze_F0.json`), one outcome run (`results/report.json`), reviewed in
[eth_btc_spread.ipynb](eth_btc_spread.ipynb). Nothing in production changes; roadmap §3 research item 2 is closed.

## Part A — the strong_bull long-ETH / short-BTC spread is not a sleeve

2020-01-01 → 2026-09-17, 2,452 days; the production classifier gives 242 `strong_bull` days (9.9 %) in 48 episodes
(median 2 days), 36 `mild_bull`, 699 `bear`, 1,474 `uncertain`; no strong_bull day in 2022, 2023 or 2026.

| arm (one unit per leg, additive) | episodes | net % | net %/yr | max DD % | MAR | DSR | halves % | per year % |
|---|---|---|---|---|---|---|---|---|
| **spread, strong_bull (candidate)** | 48 | **+88.0** (gross +103.6, funding −6.0, cost −9.6) | +13.1 | **63.1** | **0.21** | **0.923** | **−0.4 / +88.4** | 2020 −1.2 · 2021 +3.7 · 2024 +7.9 · **2025 +77.6** |
| long ETH only, strong_bull | 48 | +190.7 (funding −31.7) | +28.4 | 38.0 | 0.75 | 0.997 | +111.6 / +79.1 | 2020 +112 · 2021 −5.5 · 2024 +2.6 · 2025 +81.4 |
| long BTC only, strong_bull | 48 | +93.1 (funding −25.7) | +13.9 | 52.2 | 0.27 | 0.945 | +107.2 / −14.1 | 2020 +110 · 2021 −11.6 · 2024 −7.2 · 2025 +1.8 |
| spread, mild_bull | 22 | −22.5 | −3.3 | 37.4 | −0.09 | 0.12 | −14.4 / −8.1 | — |
| spread, all bull days | 45 | +67.9 | +10.1 | 68.1 | 0.15 | 0.84 | −8.1 / +76.0 | 2025 +69.5 |

The decision rule needed five things and the candidate passed one: (a) net +13.1 %/yr with an episode-bootstrap
CI90 of +0.6 to +26.4 — passed, barely. (b) Both halves: the first 24 episodes net **−0.4 %**, the second **+88.4 %**;
before 2023-06-09 (28 episodes) +2.5 %, after (20) +85.5 %. (c) DSR **0.923** on the 242 held days (Sharpe 1.63
annualised, but skew 1.9 and kurtosis 13.4: a few days carry it). (d) The placebo — the same episode lengths in the
same years at random days, 1,000 draws — earns +19 % on average and beats the candidate in **7 %** of draws
(q95 +94.7 %): the classifier's timing is not distinguishable from "be long the 2025 ETH days" at the 5 % bar.
(e) MAR 0.21 against **0.75** for simply being long ETH on the same days: the short BTC leg removes 103 points of
net and doubles the drawdown, because what it hedges away — ETH's beta on strong-bull days — is where the return is.

Read plainly: 77.6 of the 88 net points are 2025. The 2026-09-12 re-run's t of 1.76 was this, uncosted and unframed;
with 10 bp per leg, both legs' funding (the long ETH leg alone paid 31.7 % over its 242 days) and a placebo that
keeps the years, it is one good year. The pool study's 2023-26 window (t 2.42) was the good year plus its run-up.

## Part B — hedging chento's trades with the other asset removes the trade

Each of stage 1's 392 gate-off chento trades (208 BTC, 184 ETH; the shipped walk, stop 1 R, target 6 R, 72 h) was
paired with an equal-notional opposite position in the other asset from the entry minute to the exit minute, at 10 bp
round trip on the hedge leg, price only.

| | n | mean R | annual R | max DD (R) | MAR | first-half MAR | second-half MAR |
|---|---|---|---|---|---|---|---|
| chento BTC, unhedged | 208 | +0.788 | +30.6 | 12.6 | **2.43** | 7.67 | 1.30 |
| chento BTC, hedged with ETH | 208 | **−0.253** | −9.8 | 53.9 | −0.18 | −0.11 | −0.33 |
| chento ETH, unhedged | 184 | +0.584 | +21.3 | 12.0 | **1.77** | 2.60 | 0.91 |
| chento ETH, hedged with BTC | 184 | **−0.008** | −0.3 | 24.8 | −0.01 | −0.13 | 0.36 |

Paired difference hedged − unhedged: BTC **−1.04 R** per trade (95 % −1.62 to −0.47; halves −1.24 / −0.84), ETH
**−0.59 R** (−1.08 to −0.13; −0.97 / −0.21). Every condition of the rule fails on both assets.

Why, in one number each: the slope of a trade's R on the other asset's move over the same minutes (in the trade's
direction, in R) is **0.61** for BTC trades (correlation 0.83) and **0.93** for ETH trades (0.86). Chento's R is the
market's move over the trade. That is what a directional timing signal is — it enters before a move the whole
market makes — and a hedge in a 0.8-correlated asset subtracts the move it predicted. This does not contradict the
attribution layer's "timing alpha": timing alpha is alpha in *when* the market moves, and it is paid in the market's
move. A beta-scaled hedge (0.6–0.9 instead of 1.0) would remove proportionally less and pay proportionally less; it
was not run because the sign is settled by the slope.

## What this closes

- The ETH/BTC spread on any bull state as a sleeve (strong_bull, mild_bull, both). Not re-proposed without a new
  mechanism and out-of-sample years that are not 2025.
- Cross-asset dollar-neutral (or beta) hedging of chento's trades as a variant. "Hedging" in chento's own sense — a
  counter-position at a level on an open winner, same asset, after profit — is a different question and stays where
  the roadmap parked it (§6, the counter-short comparator, trigger to be defined).
- The ETH_REGIME thesis's honest form: on strong_bull days long ETH beat long BTC over 2020-26 (+190.7 vs +93.1 net),
  but every route to trading that as a *relative* position gives back more than it protects. If ETH's bull-day beta
  is wanted, it is wanted long and unhedged, and the 2021 / 2024 years say even that is a 2020-and-2025 story.

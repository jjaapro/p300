STATUS: CONCLUDED 2026-09-19 — **RECOMMEND an ETH paper twin of CARRY** by the frozen rule. No bot changed; the
twin is a bot change that needs the operator's go-ahead (BACKLOG §2.4 / §3.1).

# CARRY on ETH 2026-09 — findings

**Tag: AUDIT of the shipped rule on a second asset + one recommendation. Pre-registration: [README.md](README.md),
frozen before any run. Trial ledger: one candidate.** Written from `results/carry_eth.json` and
`results/per_year.csv`; the executed notebook is `carry_eth.ipynb`; re-run with `run_eth.py`.

## Gates

- **C0 — prints equal prod.db.** 7,463 Binance ETHUSDT settlement prints fetched; the 7,359 that overlap
  `cd_funding_rate_eth` match it timestamp for timestamp with max |Δrate| = 0.0; nothing in the table is missing
  from the fetch. (The 104 extra prints are 2019-11-27 → 2019-12-31, before the table starts.) ETH's table is
  settlement rows for its whole history, so the BTC study's predicted-vs-settled provenance caveat does not apply.
- **Parity with the BTC study, from this code path:** CUM30D 11.568 %/yr net, 0.740 round trips/yr (expected
  11.58 / 0.74); ALWAYS-ON 11.679 (11.69); LIVE 10.834 / 4.737 (10.85 / 4.75). All inside 0.02 %/yr and 0.1 RT/yr.

## The rule on ETH (2020-03-06 → 2026-09-18, 6.54 y, where a causal one-year median exists)

| rule | held | round trips / yr | gross %/yr | **net %/yr** |
|---|---|---|---|---|
| **CUM30D (shipped)** | 99.0 % | 1.38 | 13.11 | **12.83** |
| ALWAYS-ON | 100 % | 0.15 | 12.93 | 12.90 |
| LIVE (streak) | 91.3 % | 3.82 | 13.26 | 12.50 |

BTC, same window and code: CUM30D 11.57, ALWAYS-ON 11.68, LIVE 10.83.

Per calendar year, CUM30D net %/yr on ETH: 2020 22.8 · 2021 37.5 · **2022 0.36** · 2023 8.3 · 2024 13.0 ·
2025 4.9 · 2026 1.6 (to 09-18). Era split at the BTC ETF cutoff (2024-01-11): **pre 17.0 %/yr, post 6.9 %/yr**
— against BTC's 14.7 / 6.9. ETH's advantage over BTC is a pre-2024 fact; since the ETFs the two earn the same.

Bootstrap of the net %/yr against zero (90-print blocks, 5,000 draws): **+12.8, CI90 [+9.5, +16.6], P(>0) = 1.00**.

**Tail stress, the honest part.** In ETH's worst funding stretches the shipped exit did *not* protect:

| window | cumulative funding | ALWAYS-ON | **CUM30D** | LIVE (streak) |
|---|---|---|---|---|
| worst 30 d, 2022-08-27 → 09-25 | −1.78 % | −1.78 | **−0.67** | 0.00 |
| worst 90 d, 2022-08-27 → 11-24 | −1.67 % | −1.67 | **−2.10** | −0.04 |
| worst 180 d, 2022-06-11 → 12-07 | −1.24 % | −1.24 | **−1.67** | −0.69 |

Over 90 and 180 days CUM30D lost more than never exiting — it left after the damage and paid to come back — the
same pattern the BTC study found for the streak exit. On ETH the streak rule was the better tail insurance in every
window. None of this changes the year-level result (worst year +0.36), and the pre-registered rule does not read
these windows; they are reported for the operator's judgment, as the BTC study reported its premium.

## The combined book — the breadth test (common days 2020-03-06 → 2026-09-18, 2,388 days)

| book | net %/yr | max drawdown of cumulative net funding | net ÷ DD |
|---|---|---|---|
| BTC CUM30D alone | 10.94 | −2.24 % | 4.9 |
| ETH CUM30D alone | 12.84 | −2.14 % | 6.0 |
| **equal weight, half in each** | **11.89** | **−0.79 %** | **15.1** |

Correlation of daily funding sums BTC vs ETH: **0.87**; of the two rules' daily net: 0.87. The diversification is
not low correlation — it is that the two assets' worst negative-funding stretches do not coincide, so half-and-half
cuts the book's drawdown by two thirds at roughly the average return. That is a timing fact about 2020–2026, not a
law; it is the reason clause (c) passes by a wide margin and the reason to state it plainly.

## Decision (rule fixed in README.md before the run)

| clause | required | measured | pass |
|---|---|---|---|
| (a) ETH CUM30D net, CI90 excludes 0 | ≥ +5.0 %/yr | +12.83, CI90 low +9.48 | yes |
| (b) worst calendar year | ≥ −2.0 %/yr | +0.36 (2022) | yes |
| (c) equal-weight net ÷ DD not below BTC alone | ≥ 4.88 | 15.13 | yes |

**Verdict: RECOMMEND an ETH paper twin.** The mechanism is the shipped one — longs pay shorts to hold perpetual
exposure in a positive-funding regime, collected delta-neutral — on a second asset whose funding series is the
cleanest in the database. Nothing here is a new rule; it is the same rule on a second stream.

## What this does not establish, and what a twin would need

- **Costs are assumed, not measured.** 0.24 % per toggle is the BTC sleeve's constant; ETH spot and perp fees are
  the same schedule, spreads of the same order. With 1.4 toggles a year the sensitivity is small (each toggle is
  0.24 % of one year's ~12.8 %), but the ETH legs have never been measured the way the execution study measured
  BTC's.
- **Equal weight halves the BTC leg.** Clause (c) compares per unit of capital; a fleet that keeps BTC at full size
  and adds ETH at full size is a different (levered) book and was not the test.
- **The carry bot is BTC-only in code**, not by parameter: `asset="BTC"`, `daily_sums_pct("BTC", …)`, `btc_1m` in
  `MGMT_TABLES`, and the one-open guard is per variant. A twin needs an asset parameter, `eth_1m` and
  `cd_funding_rate_eth` in its tables, its own freshness expectations, and its own paper variant — a bot change
  under the standing go-ahead rule, with its parity test pinned the way `tests/test_carry_exit_rule.py` pins BTC's.
- **The tail behaviour above** is a reason to keep the exit-policy question open for carry too (BACKLOG header,
  "Exits"): on ETH the shipped exit bought nothing in the long stretches and the streak exit bought something.
  Not a change; a fact for the next exit study.

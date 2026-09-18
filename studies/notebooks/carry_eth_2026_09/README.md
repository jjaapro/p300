# CARRY on ETH 2026-09 — pre-registration (frozen before any result)

Written 2026-09-19 00:xx UTC, before `run_eth.py` produced a number. Road step 3, "second assets"
(BACKLOG §3.1): the validation audit's one-line conclusion was that the constraint on this project is breadth,
not edge, and CARRY is the sleeve whose second-asset data is complete. Tag: AUDIT of a shipped rule on a new
asset + one recommendation. Nothing under `bots/` or `strategies/` changes; an ETH paper twin, if recommended,
is a bot change that needs the operator's go-ahead.

## Question

Does the shipped CARRY rule — S-078 with the CUM-30D exit that replaced the streak exit on 2026-09-12 — earn on
Binance ETHUSDT perpetual funding net of its own toggles, and does adding it to the BTC carry improve the fleet's
carry rather than repeat it?

## Data and machinery

- Binance ETHUSDT settlement prints, the full history, via `bv_lib.fetch_binance_funding("ETHUSDT")` (the same
  fetcher and cache the BTC study used). **C0 gate:** they must equal prod.db's `cd_funding_rate_eth` rows over
  the overlap — timestamps and rates — which is expected, because that table is 8-hourly settlement rows for
  its whole history (6,881 + 476 rows at 28,800 s spacing across the 2026-04-13 cutover). Any disagreement is
  reported and the run stops there.
- BTCUSDT prints from the same fetcher, for the combined book and the parity gate.
- The C2 rule simulator `run_c2_carry_timing.run_rule` and `s078_hold`, imported unchanged. The three state-
  machine helpers `daily_frame`, `hold_from_states`, `rule_states` are copied verbatim from
  `carry_exit_rule_2026_09/run_p2_exit_rules.py` (that module is a script; importing it is not safe).
- Window: prints where a causal one-year median exists (`c2.TRAIL` / `c2.MIN_TRAIL`), as in C2 and the BTC
  study. The combined book uses the days both series cover from the later of the two starts.
- Toggle cost 0.24 % per round trip (the sleeve's 0.20 + 0.04 %), the same assumption on ETH: Binance's fee
  schedule does not depend on the symbol, and ETHUSDT spot and perp spreads are of the same order as BTC's.
  Declared as an assumption, not measured.

## Rules (fixed now; decided at the end of a UTC day, effective from the next print)

| rule | role | enter | exit |
|---|---|---|---|
| **CUM30D** | **the candidate — the shipped rule** | 7-day average daily funding > 0 | trailing 30-day cumulative funding < −0.5 % |
| ALWAYS-ON | reference | day 1 | never |
| LIVE (S-078 streak) | reference | 7-day average > 0 | 3 consecutive negative days |

Trial ledger: **one** candidate. The two references are the BTC study's anchors and carry no decision weight.

## Statistics

Per rule: share of time held, round trips per year, gross and net %/yr; per calendar year; era split at the
BTC ETF cutoff (2024-01-11, `bv.ETF_CUTOFF`, kept for comparability with the BTC study — ETH's own spot ETFs
began trading 2024-07-23 and are noted, not used); tail stress — the worst 30 / 90 / 180-day cumulative funding
windows in ETH's history and each rule's net through them; a 90-print circular block bootstrap (5,000 draws) of
the candidate's net %/yr **against zero** (the question is whether it earns, not whether it beats a reference).

**The combined book.** Daily net funding of CUM30D on BTC and on ETH, aligned on common days: an equal-weight
book (half the capital in each) against BTC alone — net %/yr, the maximum drawdown of cumulative net funding
(in % of capital), and their ratio (a MAR analogue on additive funding, as the BTC study used); plus the
correlation of daily funding sums BTC vs ETH. This is the breadth test: a second asset whose carry is the first
one again adds nothing (cf. ADX on ETH, killed at correlation 0.47 for adding no MAR).

**Parity gate.** From this code path, the BTC rows must reproduce the BTC study: CUM30D 11.58 %/yr net and
0.74 round trips/yr, ALWAYS-ON 11.69, LIVE 10.85 — each to 0.02 %/yr and 0.1 round trips/yr. If parity fails,
no ETH number is decision-bearing.

## Decision rule (fixed now)

RECOMMEND an ETH paper twin of CARRY only if **all** of:

- **(a)** ETH CUM30D net ≥ **+5.0 %/yr** over the window, with the bootstrap CI90 of the net excluding 0.
  The bar is roughly half of what the shipped BTC rule earns (11.58 %/yr); a second asset below that at a
  similar drawdown would add little breadth. Chosen before the run, for that reason.
- **(b)** ETH CUM30D's worst calendar year net ≥ **−2.0 %/yr**.
- **(c)** the equal-weight BTC + ETH CUM30D book's net ÷ max drawdown is **not below** BTC alone's.

If (a) and (b) pass and (c) fails: **NO BUILD — ETH carry is BTC carry again**; record the correlation.
If (a) or (b) fails: **KILL for ETH.** Otherwise: **RECOMMEND**, which is a paper twin, not live capital.

## Run order

`python run_eth.py`, then `C:/Python/Python313/python.exe build_notebook.py` → `carry_eth.ipynb`. Findings in
`findings.md`, written after the run.

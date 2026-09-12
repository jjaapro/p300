# CARRY exit rule 2026-09 — pre-registration (frozen before any result)

Written 2026-09-12 before `run_p2_exit_rules.py` produced a number. Track P2 of the approved brainstorm
follow-up plan. Tag: AUDIT + one policy decision. Nothing under `strategies/` changes; the S-078 sleeve's
constants are used as shipped (`FR_WINDOW_DAYS` 7, `FR_ENTRY_THRESHOLD` 0, `EXIT_NEG_DAYS` 3,
`ENTRY_EXIT_COST_PCT` 0.20 + `CARRY_SLIPPAGE_PCT` 0.04 per toggle).

## Question

The brainstorm validation found (side finding, C2) that S-078's three-negative-day exit cost 0.85 %/yr against
never exiting over 2019-12 → 2026-09, below always-on in every calendar year. Is there an exit rule that beats
the shipped one net of its own toggles, and what is the tail-insurance premium the shipped exit buys?

## Data and machinery

Binance BTCUSDT settlement funding, all prints 2019-09 → now (`bv_lib.fetch_binance_funding`, cached; equal to
`cd_funding_rate` settlement rows over the full history per the C0 gate). The C2 rule simulator
(`run_c2_carry_timing.run_rule`, `s078_hold`) is imported unchanged; the window is the one where a causal
7-day average exists (2019-12-19 →). Toggle cost 0.24 % per round trip (0.12 % per leg-pair).

## Rules (fixed now; all decided at the end of a UTC day, effective from the next print)

| rule | enter | exit |
|---|---|---|
| LIVE (S-078) | 7-day average daily funding > 0 | 3 consecutive negative days |
| ALWAYS-ON | day 1 | never |
| SYMMETRIC-7D | 7-day average > 0 | 7-day average < 0 |
| LIVE + 21-day minimum hold | as LIVE | as LIVE, but not before 21 days held |
| CUM-30D | 7-day average > 0 | trailing 30-day cumulative funding < −0.5 % |

## Statistics

Per rule: share of time held, round trips per year, gross and net %/yr; per calendar year; era split at the
ETF cutoff (2024-01-11); 90-print circular block bootstrap (5,000 draws) of the net %/yr difference vs LIVE.
Tail stress: the worst 30 / 90 / 180-day cumulative funding in the history (what ALWAYS-ON pays in the worst
stretch) and how each rule fared through it. Parity gate: reproduce C2's LIVE row (10.85 %/yr net,
4.8 round trips/yr) and ALWAYS-ON row (11.69 %/yr) to 0.02 %/yr.

## Decision rule (fixed now)

Recommend replacing the shipped exit only if a candidate beats LIVE by ≥ 0.5 %/yr net with the bootstrap
CI90 of the difference excluding 0 AND the candidate's worst calendar year is not worse than LIVE's worst
year by more than 1 pp. Otherwise NO CHANGE; the tail-insurance premium of the shipped exit is reported for
the user's judgment. Trial ledger: 5 rules, declared.

## Run order

`run_p2_exit_rules.py`, then `C:/Python/Python313/python.exe build_notebook.py` → `carry_exit_rule.ipynb`.

STATUS: CONCLUDED — RECOMMEND replacing S-078's three-negative-day exit: ALWAYS-ON beats it by +0.85 %/yr (CI90 [+0.60, +1.11]) and a 30-day cumulative-funding exit by +0.74 %/yr (CI90 [+0.44, +1.04]), both with a better worst calendar year; the shipped exit bought no tail protection in any of the worst funding stretches

OUTCOME (2026-09-12, user go-ahead): the CUM-30D rule shipped — `strategies/sleeves/carry/config.py` `EXIT_CUM_DAYS = 30`, `EXIT_CUM_THRESHOLD_PCT = -0.5`, streak exit removed; chosen over always-on to keep a defined tail exit; per-day parity with `rule_states("CUM30D")` pinned by tests/test_carry_exit_rule.py (the sleeve additionally does not enter while the exit is active). Takes effect on the carry bot's next restart.

# CARRY (S-078) exit rule 2026-09

**Tag: AUDIT + one policy decision. Pre-registration: [README.md](README.md), frozen before any run.**
Written 2026-09-12 from `results/log_p2.txt` and `results/p2_exit_rules.json`. Data: 7,676 Binance BTCUSDT
settlement prints 2019-09 → 2026-09-11 (equal to `cd_funding_rate` settlement rows, C0 gate); scoring window
2019-12-19 → 2026-09-11 (6.74 years, where a causal one-year median exists, as in C2); toggle cost 0.24 %
(the sleeve's 0.20 + 0.04 %); the C2 rule simulator imported unchanged. Parity gate passed: LIVE 10.846 %/yr
net at 4.75 round trips/yr (C2: 10.85 / 4.8), ALWAYS-ON 11.693 %/yr (11.69).

## Rules, net of their own toggles

| rule | held | round trips / yr | gross %/yr | **net %/yr** | worst year (net) | diff vs LIVE, CI90 |
|---|---|---|---|---|---|---|
| LIVE: 7-day avg > 0 in, 3 negative days out | 90.6 % | 4.75 | 11.80 | 10.85 | 1.56 (2026) | — |
| **ALWAYS-ON** | 100 % | 0.15 | 11.72 | **11.69** | **2.61** | **+0.85 [+0.60, +1.11]** |
| SYMMETRIC-7D: 7-day avg < 0 out | 89.8 % | 4.75 | 11.88 | 10.92 | 1.33 | +0.08 [−0.11, +0.27] |
| LIVE + 21-day minimum hold | 93.6 % | 3.71 | 11.76 | 11.02 | 1.71 | +0.17 [+0.06, +0.31] |
| **CUM-30D: 30-day cumulative < −0.5 % out** | 99.6 % | 0.74 | 11.73 | **11.58** | **2.61** | **+0.74 [+0.44, +1.04]** |

Per year (net %/yr): ALWAYS-ON 17.2 / 30.6 / 4.2 / 7.9 / 11.9 / 5.1 / 2.6 for 2020 → 2026; LIVE 16.3 / 30.1 /
2.5 / 7.2 / 11.5 / 4.5 / 1.6 — below ALWAYS-ON in every year, including 2022. CUM-30D equals ALWAYS-ON in every
year but 2020 (16.5). Pre-ETF / post-ETF: LIVE 12.6 / 6.2, ALWAYS-ON 13.5 / 6.9 %/yr.

## Tail stress — the insurance never paid

| worst stretch | cumulative funding | LIVE net | ALWAYS-ON net | CUM-30D net |
|---|---|---|---|---|
| worst 30 days: 2020-03-12 → 04-10 | −1.31 % | −1.30 % | −1.31 % | −1.55 % |
| worst 90 days: 2026-02-06 → 05-06 | −0.39 % | **−0.77 %** | −0.39 % | −0.39 % |
| worst 180 days: 2025-12-31 → 06-28 | +0.53 % | **−0.20 %** | +0.53 % | +0.53 % |

Negative-funding stretches in this history are short: the worst 30-day cumulative is −1.3 % and the worst
180-day window is still positive. The three-day exit reacts to those stretches after most of the damage,
pays 0.24 % to leave and 0.24 % to return, and in the two longest bad stretches it lost *more* than never
exiting. The premium it has cost is 0.85 %/yr, and the event it insures against — a sustained negative
regime — has not occurred in seven years of data. CUM-30D keeps a genuine tail exit (5 toggles in 6.7 years)
at a cost of 0.11 %/yr against always-on.

## Decision rule applied

Candidates had to beat LIVE by ≥ 0.5 %/yr with the bootstrap CI90 excluding zero and a worst year not more
than 1 pp worse. **ALWAYS-ON and CUM-30D both pass**; SYMMETRIC-7D and the 21-day minimum hold do not (small
gains, the first insignificant). Verdict: **RECOMMEND** replacing the exit; between the two passing rules,
ALWAYS-ON is the simpler (the entry condition is already true 100 % of the scoring window) and CUM-30D is the
one that keeps a defined exit for a regime the data has not seen.

## What this changes (for the user's decision; nothing implemented)

`strategies/sleeves/carry/config.py` `EXIT_NEG_DAYS = 3` → either disable the streak exit (always-on while
the 7-day average entry condition holds) or replace it with "exit when the trailing 30-day cumulative funding
< −0.5 %" (`CUM30_EXIT`), expected +0.74–0.85 %/yr on the sleeve's notional with the same or a better worst
year. The bot and calibration log (`docs/calibration/carry.md`) would change with it. Caveat: seven years,
one venue, and the tail the shipped exit was written for is absent from the sample — if the user values the
insurance regardless, the price is now known: 0.85 %/yr.

## Reproduce

`run_p2_exit_rules.py`, then `C:/Python/Python313/python.exe build_notebook.py` → `carry_exit_rule.ipynb`.
Reads the brainstorm study's cached Binance funding (`brainstorm_validation_2026_09/cache/`). Runtime < 1 min.

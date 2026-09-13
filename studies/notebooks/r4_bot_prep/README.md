# R4 bot prep — pre-registered design (2026-09-06, before any data)

Purpose: fix two bot-level constants for `bots/r4/` from data instead of guesses, per the
per-sleeve stop-loss policy (memory `feedback_allocation_and_sl_policy`) and the 2026-05-13
cold-fill incident (both V2 losers entered 127 min after the window opened).

Scope is deliberately narrow: the four shipped windows are NOT re-optimised here. Windows,
weekday and day-of-month rules are the ones in
`bots/r4/strategy/config.py` and `math.py`:

| strategy | days | entry → exit (UTC) | hold |
|---|---|---|---|
| JPLUS_R4_BTC | Mon, day ≤ 14 | 06:00 → 18:00 | 12h |
| JPLUS_R4_ETH | Tue, next day ≤ 14 | 20:00 → Wed 20:00 | 24h |
| JPLUS_R4_BTC_V2 | Wed+Fri, day ≤ 14 | 04:00 → 14:00 | 10h |
| JPLUS_R4_ETH_V2 | Wed+Fri, day ≤ 14 | 04:00 → 14:00 | 10h |

Data: `btc_1m`, `eth_1m` in `data/databases/prod.db` (2020-01-01 → present), read-only.
Fills: entry = open of the 1m bar at the entry minute; exit = open of the 1m bar at the exit
minute (same convention as `r4/math.py`); stop = filled AT the stop level on the first 1m bar
whose low breaches it (gap risk ignored, so the stop is optimistic — a real stop can only be
worse, which biases the test toward adopting a stop, not against it).
Costs: 15 bp round trip (p300 `trades.py` defaults: 10 bp fee + 5 bp slippage) applied to
every fire, stopped or not. Eras: pre-ETF = 2020-01-01 → 2024-01-10, post-ETF = 2024-01-11 →.

## Test 1 — intraday stop-loss

Grid (fixed, no additions after seeing results): stop ∈ {2%, 3%, 5%, none} below entry.

Priors (stated before running): the windows are 10–24h holds on assets with ~2.5–3.5%
median daily range, so a 2% stop will trigger on ordinary noise (stop-hit rate 15–30%) and
cut expectancy; 5% should rarely trigger; the trader repo's PDO sweep found every stop
level degraded that intraday sleeve. Expected verdict: `STOP_LOSS_PCT = None`.

Decision rule (fixed): adopt a stop level only if, for that level, in BOTH eras
per-fire expectancy (net) drops by ≤ 5 bp relative to no stop AND the worst single-fire loss
shrinks by ≥ 30%. If several levels pass, take the widest. Otherwise `STOP_LOSS_PCT = None`
and the deviation from the SL policy is recorded in `docs/calibration/r4.md` with these
numbers (the scheduled exit remains the only exit, as today).

## Test 2 — late-entry cost curve

Lateness ∈ {0, 5, 10, 15, 30, 60} minutes after the window opens; exit unchanged. Reported
per window and era: mean net return per fire, and the loss versus lateness 0.

Prior: the R4 mechanism is a session drift, so the first minutes carry a proportional share
of the move and the curve should be roughly linear; the 2026-05-13 fills (+127 min) were
losers but n=2 says nothing.

Decision rule (fixed): `LATE_ENTRY_MAX_S` = the largest grid lateness whose post-ETF mean
loss versus lateness 0 is ≤ 5 bp on all four windows, floored at 5 minutes and capped at
15 minutes (operational bound: a bot that is more than 15 minutes late has a problem worth
alerting on regardless of the curve). Provisional value if the test is inconclusive: 600 s.

## KILL / stop conditions

- If fewer than 30 post-ETF fires exist for a window, that window's era result is
  informational only and the pre-ETF result decides.
- No parameter changes after seeing results. Any follow-up idea goes to a new
  pre-registered study.

Tag: AUDIT-leaning calibration (fixed grid, N_TRIALS = 4 stop levels + 6 lateness points;
recorded in the DSR ledger as 10 trials against the R4 family for completeness).

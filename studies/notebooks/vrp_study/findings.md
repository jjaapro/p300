# VRP harvest (short ±10% BTC strangle, 7-day delta hedge) — findings

STATUS: CONCLUDED — DO NOT ADVANCE (2026-09-07). The pre-registered seeded-sample clauses are
formally not hit (parity PASS; DSR@168 = 0.902 against the 0.90 cut, 0.890 on the trader's own
strike picks), but the parity step reproduced two data artefacts in the trader repo's Phase-3
mechanics, and a post-hoc sensitivity — declared as such, no parameters changed — shows they
account for the whole result. Under spec-faithful mechanics the test half is negative and every
deflated test fails (DSR@168 = 0.19). No paper options-sleeve design document is written. OOS
accrual continues on the live Deribit feed, which cannot reproduce either artefact; re-run both
modes when OOS n ≥ 6 (≈ 2026-12 if weekly expiries carry 21-day-ahead listings, else ≈ 2027-03).

Engine: `studies/lib/options/{bs,chain,pnl_engine}.py` (port of the trader's
`probe_vrp_straddle{,_v2}.py`). Runner: `run_vrp.py` (`--terminal mark` = pre-registered,
`--terminal intrinsic` = sensitivity). Results in `results/`; the trader's own per-expiry output
on its database is kept as `results/trader_canonical_expiries.csv`.

## Step 1 — parity gate (pre-registered): PASS

| | trader (its DB, its code) | p300 port (seeded tables) | tolerance |
|---|---|---|---|
| n (train / test) | 66 (43 / 23) | 66 (43 / 23) | ±3 |
| mean per expiry | +1.53 % | +1.59 % | ±0.20 pp |
| worst expiry | −7.08 % | −7.08 % | ±0.50 pp |
| annualised Sharpe (√12) | 1.88 | 1.91 | ±0.20 |
| win rate | 81.8 % | 81.8 % | — |
| DSR @ 168 trials | 0.890 | 0.902 | (clause: ≥ 0.90) |

The TRAIN half reproduces to the last digit (mean +1.493 %, sd 2.665 %, Sharpe 1.94, worst
−7.08 %, best +4.56 %). Three TEST expiries differ (2025-12-26, 2026-02-27, 2026-03-27): the
trader's picker mixed Deribit's USDC-settled linear contracts (`BTC_USDC-…`) with the BTC-settled
ones, and p300's instrument parser keeps only the latter. Those three picks move the combined
mean by +0.06 pp and the DSR@168 from 0.890 to 0.902 — the DSR clause sits at the noise floor.
The toolkit's `dsr_pbo.dsr_from_returns` agrees with the trader formula to four decimals.

## Step 2 — what the parity run actually traded

`chain.find_strangle_pair` (trader convention) takes the strike nearest the ±10 % target **that
has both an entry-day and an expiry-day mark row**. In the CoinDesk snapshot expiry-day rows exist
for 100 % of instruments that finish in the money and only 54 % (calls) / 69 % (puts) of those
that finish out of the money; by moneyness at settlement: 95 % within 5 %, 71 % at 5–10 %, 61 % at
10–20 %. Two consequences:

1. **Strike drift.** Picked offsets average 6.3 % (call) and 7.1 % (put) instead of 10 %, with a
   4–5 pp spread, and go as far as an 8 % *in-the-money* call (2025-11-28: call K = 95 000 at spot
   103 305). 38 of the 66 expiries traded a different structure from the spec, collecting 2.8–10.7 %
   of spot in premium instead of 0.9–4.1 %. The strategy that produced +1.53 % per expiry is a
   ≈ ±6.5 % strangle-to-straddle, not the registered ±10 % strangle.
2. **Missing crash weeks.** Seven expiries were silently skipped: 2025-11-21, 2026-02-06, 02-13,
   02-20, 04-10, 04-17, 04-24 — all in the TEST half, and the November-2025 and February-2026 ones
   are exactly the crash weeks (terminal payoffs of 8–20 k USD against 1–2 % premium). Mean of the
   seven under spec strikes: −3.66 % (2 winners).

   *Corrected 2026-09-09 after verification — the original text said all seven had "no expiry-day
   row for any instrument", which is wrong for four of them, and the true mechanism is worse.*
   **Two distinct mechanisms:**

   | expiry | spot at entry | expiry-day rows (C / P) | legs with BOTH entry + expiry marks (C / P) |
   |---|---|---|---|
   | 2025-11-21 | 109,557 | 6 / 22 | **0** / 5 |
   | 2026-02-06 | 95,504 | 16 / 29 | **0** / 4 |
   | 2026-02-13 | 89,559 | 6 / 21 | **0** / 4 |
   | 2026-02-20 | 84,211 | 6 / 14 | **0** / 5 |
   | 2026-04-10 / 17 / 24 | 70,473 / 66,364 / 66,930 | 0 / 0 | 0 / 0 |

   (a) The four crash-week expiries **do** have expiry-day rows, but **not one call** carries both
   an entry-day and an expiry-day mark, while several puts do. The call leg is therefore
   unfillable and the expiry drops out. That is a *systematic post-crash selection* — as spot
   falls 109 k → 84 k, out-of-the-money calls stop trading and lose their marks — which is a
   stronger indictment of the picker than the random-gap story it replaces.
   (b) The three April-2026 expiries genuinely have no expiry-day rows at all, because the seeded
   snapshot ends 2026-04-24. That is truncation, not a market phenomenon, and it would not recur
   on the live feed.

Decomposition on the 66 common expiries: spec-faithful strikes lower the mean from +1.59 % to
+1.03 % (−0.56 pp); adding the seven excluded expiries takes it to +0.58 % (−0.45 pp).

## Sensitivity (post hoc, labelled): spec strikes, settlement at 08:00 UTC, all 73 expiries

Strikes need only an entry-day mark; the terminal payoff is intrinsic value at the 08:00 UTC
price from `cd_futures_ohlcv` (Deribit's settlement time; the hedge closes there too).

| | TRAIN (43) | TEST (30) | combined (73) |
|---|---|---|---|
| mean per expiry | +1.05 % | **−0.10 %** | +0.58 % |
| sd | 2.59 % | 3.13 % | 2.86 % |
| annualised Sharpe | 1.40 | −0.11 | 0.70 |
| win rate | 76.7 % | 63.3 % | 71.2 % |
| worst | −7.68 % | −7.83 % | −7.83 % |

Deflated Sharpe on the combined sample: N = 1 → 0.934, N = 42 → 0.330, N = 168 → 0.189,
N = 672 → 0.104. Per expiry-year: 2024 +0.08 % (n = 8), 2025 +0.86 % (n = 48), 2026 +0.02 %
(n = 17). The five worst expiries are the February-2026 and November-2024 moves (−6.0 to −7.8 %).

## Decision-rule mapping (pre-registered clauses, mark mode as written)

| clause | measured | outcome |
|---|---|---|
| parity: n 66 ± 3, mean ± 0.20 pp, worst ± 0.50 pp, Sharpe ± 0.20 | 66 / +0.06 pp / 0.00 pp / +0.03 | PASS |
| OOS mean ≤ 0 with n ≥ 6 | OOS n = 0 (no marks 2026-04-25 → 2026-09-05) | not evaluable |
| any expiry < −10 % | worst −7.08 % (mark) / −7.83 % (intrinsic) | not hit |
| DSR@168 < 0.90 | 0.902 (mark, p300 picks); 0.890 (trader picks); 0.189 (spec mechanics) | not hit as written; hit under corrected mechanics |

Verdict: the rule as written does not kill; the item is stopped anyway because the mechanics
behind the passing numbers are defective, not because a rule was re-tuned. Recording this as a
deviation from the pre-registration.

## Is the premium still there? (DVOL − forward 30-day realised vol, vol points)

| year | mean | share positive | n |
|---|---|---|---|
| 2022 (from Sep) | +21.0 | 75 % | 116 |
| 2023 | +6.5 | 73 % | 365 |
| 2024 | +7.1 | 73 % | 366 |
| 2025 | +6.4 | 71 % | 365 |
| 2026 (to Sep 6) | **+0.2** | 63 % | 220 |

Latest (2026-09-06): DVOL 38.95 against trailing 30-day realised 45.8 — the seller is paid below
realised right now. The structural premium of 2023–2025 (≈ +6–7 points) has collapsed in 2026,
consistent with the trader's own April note (−14.7 % YTD then).

## OOS accrual

No option marks exist between 2026-04-25 and 2026-09-05 (Deribit's public API serves no history
for expired instruments). Live snapshots (00:05 / 08:05 UTC, liquid subset ≤ 90 d, |ln K/S| ≤ 0.30)
started 2026-09-06; nine future expiries are listed; the first with a T−21 entry inside the live
window is 2026-10-30. On the live feed every liquid strike has a 00:05 row on its expiry day, so
strike drift cannot occur, and the settlement price is computable from the index — both modes
should then agree. Re-run:

```
python studies/notebooks/vrp_study/run_vrp.py
python studies/notebooks/vrp_study/run_vrp.py --terminal intrinsic
```

## Lessons for the ledger

- "Data availability as a selection criterion" silently changed the traded structure and dropped
  the crash weeks; the trader repo's Phase-3 VRP result (its memory `vrp/phase3_summary.md`) should
  be treated as falsified. Added to the trader-survey caveats.
- Parity gates reproduce bugs as faithfully as results; a mechanics sensitivity belongs in every
  port's pre-registration, not after it.
- The options engine (BS, chain access on `deribit_*`, hedged P&L) is reusable for any later
  options study; nothing in `strategies/**` or `bots/**` references it.

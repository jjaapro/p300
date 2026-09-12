STATUS: CONCLUDED — 4 sleeves re-costed; rule 1 fires on all four (coded lumps overstate execution cost 2–3×); rule 4: CHENTO_BTC/ETH and SQUEEZE_BULL VIABLE, SHORT_SQUEEZE "retire pending user decision"; no prod change made

OUTCOME (2026-09-12, user go-ahead): cost constants adopted — chento 18 → 10 bp, SHORT_SQUEEZE 25 → 10, SQUEEZE_BULL 15 (booked default) → 7, ADX 15 → 11; SHORT_SQUEEZE kept running at the corrected cost with a no-stop paper twin instead of being retired (docs/calibration/*.md); execution-layer requirements recorded in the pool plan (F-EXEC) and BACKLOG; E7 probe not run.

# Execution study 2026-09 — what execution actually costs each running sleeve

**Tag: AUDIT + execution-policy decision. Pre-registration: [README.md](README.md), frozen before any run.**
Written 2026-09-12 from `results/log_e*.txt` and `results/*.json`. Nothing under `strategies/`, `bots/`, `data/`
was modified; prod.db was opened read-only. Every number below is from the sleeves' own validated trade lists
walked on Binance spot 1-minute bars (2020 → 2026-09, 7 years) and, where the events fall inside it, on the
5-second table (2025-06-08 → 2026-06-07).

**One-line answer.** For p300's sizes execution cost is the exchange fee and almost nothing else: the measured
taker round trip is 7–10 bp against the 18–25 bp the sleeves charge themselves, the realised half-spread is
below 1 bp by every estimator, decision-to-fill drift over the bot's 60-second tick is within ±2 bp of zero,
stop-market fills never gapped through a stop in seven years of 1-minute bars, and take-profit touches always
traded through by at least a tick. Passive entries at the last price fill 89–100 % of the time within a minute
but improve nothing except the fee. The execution finding that changes a decision is not a policy but a
constant: SHORT_SQUEEZE, with a 35 bp-wide stop, pays 0.80 R per trade under its own coded 25 bp lump on a
+0.48 R gross edge, so its paper record is negative by construction; at the measured 9 bp it is +0.19 R and
execution still eats 47–60 % of the edge, which the pre-registered rule calls "retire pending user decision".

## Verdicts by pre-registered rule

| sleeve | n | gross R (1 m) | coded RT bp | measured taker RT bp (CI90) | rule 1 constant | rule 2 maker entry | rule 3 resting TP | rule 4 viability (M1 / M2 cost share) |
|---|---|---|---|---|---|---|---|---|
| CHENTO_BTC | 101 | +0.800 | 18 | 9.6 (8.4–10.8) | **CHANGE** | stay taker | **resting** | **VIABLE** (7.6 % / 5.8 %) |
| CHENTO_ETH | 77 | +0.653 | 18 | 10.0 (8.0–11.9) | **CHANGE** | stay taker | **resting** | **VIABLE** (7.4 % / 6.0 %) |
| SHORT_SQUEEZE | 71 | +0.481 | 25 (sleeve) / 4 (notebook) | 9.3 (7.0–11.6) | **CHANGE** | stay taker | **resting** | **RETIRE PENDING USER DECISION** (60 % / 47 %) |
| SQUEEZE_BULL | 122 | +0.334 | 18 | 6.7 (4.5–8.8) | **CHANGE** | stay taker | **resting** | **VIABLE** (10.0 % / 10.1 %) |
| ADX | 34 | +15.2 % per trade | 15 (sleeve) / 10 (harness) | 10.5 | (informational: 0.7 % of a mean trade) | nothing to gain | n/a | fee-insensitive |
| CARRY | — | funding only | 24 bp per toggle | 24 (taker, BNB) / 20.6 (maker perp legs) | matches | — | — | fee-insensitive (4.8 toggles/yr) |

"Measured taker" = 2 × 5.0 bp fee + 2 × 0.3 bp half-spread + the sleeve's measured decision-to-fill drift +
its measured stop slippage (0.0). M2 = maker entry (2.0 bp + 1.0 bp adverse selection, fill-weighted with
cancel-then-skip at 15 min) + resting-limit TP (2.0 bp) + taker stop/time exits.

## E0 — facts and parity (all gates passed)

- `btc_1m` / `eth_1m` are **Binance spot**: the minute closing each hour equals the spot hourly close in
  99.98 % of 58,671 hours (median difference 0.0 bp; vs perp 4.6 bp).
- **Spot-vs-perp basis** on 245,637 common 15 m closes: mean −1.3 bp, sd 6.3 bp, p95 |basis| 10.7 bp; inside
  the 5 s year mean −4.5 bp, sd 1.2 bp. The 15-minute *change* of the basis has sd 2.5 bp (p95 3.2 bp), which
  is the error a venue level carries when tested against the spot path — small against chento's 160–240 bp
  stops and squeeze_bull's 200 bp, material against SHORT_SQUEEZE's 35 bp (see caveats).
- **Parity**: CHENTO_BTC reproduces the audit's 72 h replay to four decimals (+0.8001 R pre-cost, +0.6853 R at
  18 bp; ETH +0.7120 / +0.6219); the SHORT_SQUEEZE port lands n = 70, +0.392 R, PF 1.64 (quoted 70 / +0.40 /
  1.65; win rate 45.7 % vs quoted 44.3 %); SQUEEZE_BULL reproduces all 122 bull-gated ledger rows to 2e-16.
  The 1 m walk of the same events (the study's own engine) gives +0.800 / +0.653 / +0.481 / +0.334 R gross.

## E1 — a market order costs the fee, not the spread

- **Half-spread**: Roll on 5 s closes is defined (negative autocovariance) in only 6.8 % of hours and is
  0.26 bp there (0.14–0.54 bp by hour of day); Corwin–Schultz on 5 s bars 0.02 bp; on 1 m bars 0.86 bp BTC
  (1.19 bp in 2020 → 0.54 bp in 2026) and 1.14 bp ETH, which is an upper bound because it counts intra-minute
  noise. The study uses 0.3 bp per side. Impact at $10–30 k notional is taken as zero.
- **Decision-to-fill drift** (the paper bot books the 1 m close; a 60 s tick acts up to a minute later), 1 m
  panel, adverse-signed, mean and day-block CI90: CHENTO_BTC −0.55 bp [−1.8, +0.7]; CHENTO_ETH −0.24
  [−2.3, +1.7]; SHORT_SQUEEZE −0.31 [−2.6, +2.0]; SQUEEZE_BULL **−2.6 bp [−4.7, −0.4]** (price keeps falling
  for a minute after a flush bar, so a late taker fill is *cheaper*); ADX entries −1.7 bp, signal exits +1.6 bp.
  The gap at the bar boundary is 0.0 bp everywhere. On the 5 s panel the +60 s drift is +1.5 [−0.1, +3.2] bp
  for chento (n 21) and inside ±2 bp for the others, on 12–17 events. Time-stop exits: 0.0 bp.
- Stressed entries (top 5 % 1 m range): drift is negative (favourable) in every sleeve but on n = 4–7 events.

## E2 — a limit at the last price fills; it just does not improve anything

| sleeve | fill ≤ 60 s touch / through | fill ≤ 15 min touch / through | improvement at fill | Δ gross R vs market (T ≤ 15 min, skip) | 5 s check |
|---|---|---|---|---|---|
| CHENTO_BTC | 98 % / 89 % | 99 % / 94 % | 0.02 bp | −0.01 … +0.03 | fill rates equal |
| CHENTO_ETH | 99 % / 94 % | 100 % / 99 % | 0.02 bp | −0.07 … 0.00 | — |
| SHORT_SQUEEZE | 100 % / 96 % | 100 % / 99 % | 0.03 bp | −0.04 … 0.00 | equal |
| SQUEEZE_BULL | 100 % / 96 % | 100 % / 99 % | 0.04 bp | 0.00 … +0.02 | equal |

The order is marketable at the next print, so the fill is immediate and the price is the same as a market order.
Rule 2 (≥ +0.02 R gross in both halves) fails for all four: the only gain from a maker entry is the 3 bp fee
difference, which E6 prices at +0.01 R (chento), +0.06 R (SHORT_SQUEEZE) and −0.001 R (SQUEEZE_BULL, whose
late taker fill is cheaper than a limit at the close). ADX: a limit at the daily close fills 100 % of the time
within a minute for 0.08 bp of improvement.

## E3 — take-profit touches trade through; resting TP limits fill

Every target hit on 1 m bars (14 / 8 / 21 / 49 per sleeve) exceeded the target by ≥ 1 tick, and by ≥ 1 bp in
88–95 %; the same on 5 s (100 % ≥ 1 tick). Rule 3 passes for all four: a resting reduce-only limit at the
target would have filled, saving 3 bp per target exit. The polling exit the bots use today (close at the
price seen on the next tick) is on average *better* than the target because price runs through it — median
−6 bp (chento BTC), −2 bp (SHORT_SQUEEZE), −0.1 bp (SQUEEZE_BULL) — with a wide dispersion; a resting limit
gives up that run-through for a certain fee saving.

## E4 — stop-market fills do not gap at 1-minute resolution

Across 176 stop exits on 1 m bars (54 / 41 / 39 / 42) and 27 on 5 s, **no bar opened beyond the stop before
touching it**, so the stop_path fill rule (wick → stop price, gap → open) gives 0.0 bp slippage in every case,
calm or stressed. This also says the daily harness's fill-at-stop assumption is exact at minute resolution:
the 8 pp CAGR gap the brainstorm validation found between fill models is a daily-bar artefact. The polling
fill (a bot that closes at the price it sees after the breach) is on average slightly favourable (mean −0.7 to
−3.7 bp; SHORT_SQUEEZE −0.09 R) with an adverse tail: p90 +8.5 bp (SHORT_SQUEEZE, a quarter of its stop
distance), +13–17 bp (chento, squeeze_bull). The nine ADX 10 % stops since 2020 all located on the ledger's
day, no gap-through, polling fills −74 to +38 bp around the stop. What 1 m and 5 s bars cannot see is a
sub-bar cascade; that is the live probe's job.

## E5 — no adverse selection at signal times beyond SHORT_SQUEEZE's first minute

Passive touch-fills at the last close on the 5 s panel, markout vs 20 hour-of-day-matched random times per
signal (day-block CI90):

| signal set | n | +60 s | +15 min | +60 min |
|---|---|---|---|---|
| chento BTC (all 41 fires) | 40 filled | +0.0 vs −0.2 → diff +0.2 [−2.7, +2.8] | **+11.5 vs −0.7 → diff +12.1 [4.6, 19.8]** | +5.1 vs −1.5 |
| SHORT_SQUEEZE (17) | 17 | **−2.3 vs +0.6 → diff −2.9 [−6.2, −0.2]** | +2.4 vs +1.0 | +6.9 vs +2.6 |
| SQUEEZE_BULL (all 61 fires) | 61 | −0.2 vs −0.2 | +4.4 vs +0.7 (CI incl. 0) | +4.7 vs −1.1 |

A passive fill at a chento signal is followed by a favourable 12 bp move within 15 minutes — the signal's own
edge, not adverse selection. SHORT_SQUEEZE shows a one-minute adverse move of about 3 bp after the sweep bar
that recovers by 15 minutes; on a 35 bp stop that is 0.08 R of transient pain, not a fill-quality problem.
Fill rates within 60 s are 96–100 % conditional and 96–98 % unconditional.

## E6 — re-costing (1 m panel, day-block CI90 in the JSON)

| sleeve | gross R | M0 sleeve-coded → net R | M1 measured taker → net R (bp) | M2 hybrid (touch / through) → net R | M3 all-maker → net R | net MAR M0 → M1 |
|---|---|---|---|---|---|---|
| CHENTO_BTC | +0.800 | 18 bp → +0.685 | 9.6 bp → **+0.739** | +0.761 / +0.744 | +0.765 / +0.748 | 0.93 → 1.08 |
| CHENTO_ETH | +0.653 | 18 → +0.563 | 10.0 → **+0.605** | +0.615 / +0.596 | +0.618 / +0.599 | 0.81 → 0.90 |
| SHORT_SQUEEZE | +0.481 | **25 → −0.316** (4 → +0.353) | 9.3 → **+0.192** | +0.256 / +0.247 | +0.264 / +0.255 | −0.21 → 0.40 |
| SQUEEZE_BULL | +0.334 | 18 → +0.244 | 6.7 → **+0.301** | +0.299 / +0.308 | +0.302 / +0.311 | 1.24 → 1.66 |

Halves (first / second) under M1: chento BTC +1.25 / +0.22, ETH +0.92 / +0.28, SHORT_SQUEEZE +0.14 / +0.25,
SQUEEZE_BULL +0.22 / +0.38. ADX: measured 10.5 bp vs coded 15 bp = 0.7 % of a +15.2 % mean trade at 3.9
trades a year. CARRY: 24 bp per toggle at VIP0 taker with BNB (exactly the coded 0.20 + 0.04 %), 20.6 bp with
maker perp legs, at 4.8 toggles a year — 0.16 %/yr either way.

**Reading.** (1) The coded lumps were guesses (AUDIT_2026_05_13 estimated "5–10 bp" of slippage; SHORT_SQUEEZE
was given 15 bp "because the stop is bp-wide", FOMC 10 bp) and every one of them is 2–3× the measured cost;
rule 1 fires on all four with the day-block CI90 excluding the coded value. (2) For chento and squeeze_bull the
correction is worth +0.04 to +0.06 R per trade and 10–35 % of MAR — real, but it changes no verdict. (3) For
SHORT_SQUEEZE it changes the sign of the paper record: with 25 bp the sleeve books −0.32 R per trade even when
the signal performs exactly as researched, so `bot_short_squeeze_v1` cannot validate anything until the
constant is fixed; with the measured 9.3 bp it is +0.19 R (MAR 0.40, both halves positive) and execution still
takes 60 % (47 % with maker legs) of the gross edge. The pre-registered rule 4 verdict is RETIRE PENDING USER
DECISION; the honest description is "marginal, alive, and mis-costed", not "dead".

## What this changes (for the user's decision; nothing implemented)

1. **Cost constants** (`strategies/trades.py`, sleeve configs): chento 18 → ~10 bp round trip; SHORT_SQUEEZE
   10 + 15 → ~10 bp; SQUEEZE_BULL 18 → ~7 bp; ADX 10 + 5 → ~11 bp; CARRY unchanged. Keep them fee-dominated:
   2 × taker fee + 1 bp, and re-verify the fee tier from the account before going live.
2. **Order types when execution exists**: taker entries (rule 2), **resting reduce-only take-profit limits**
   (rule 3; 100 % trade-through, 3 bp per exit), stop-market stops (0 bp slippage at bar resolution; the polling
   bot's adverse tail of 8–17 bp at p90 disappears with an exchange-resident stop), taker time exits. Maker
   entries are optional fee savings of 0.01–0.06 R per trade at a 1–11 % skip rate (through rule); not required.
3. **SHORT_SQUEEZE**: user decision — retire, or keep it in paper with the corrected constant and let the
   pre-registered re-cut points decide; at +0.19 R and 16 trades a year it contributes ≈ +3 R/yr at MAR 0.4.
4. **Paper bots**: the cost model should be per leg (fee by order type + measured drift), TP exits should be
   emulated as resting limits filled on the 1 m trade-through, every sleeve's stop should use stop_path
   semantics (today only ADX/THU_BEAR do), and a `fills` record (intended vs realised price, latency) should
   exist before any live connection so this study can be repeated on real fills.

## E7 — the live quoting probe (design only; needs an API key, ≤ $50, explicit go-ahead)

Binance USDT-M BTCUSDT mainnet (testnet queues are not real), 0.001 BTC post-only limits at the touch,
alternating sides, at random times across all hours plus every live sleeve fire, held ≤ 60 s then cancelled;
on a fill, flatten by market. Log per post: mid and top-of-book size at post (queue-ahead estimate), fill or
not, latency, mid at +1 s / +1 min / +15 min / +1 h. Target 300–500 posts over two weeks (≈ $0.05 per filled
probe). It settles the two things this study cannot: queue position (the brainstorm's factor-of-six lever) and
sub-second markouts on real fills. Given E2 (the maker benefit here is 3 bp of fee at a near-certain fill) the
probe is worth running only if maker entries are actually wanted; nothing above depends on it. No script was
written: half-built live-order code should not sit in the repo before that decision.

## Caveats stated in advance and how they landed

- The 1 m / 5 s paths are spot; the sleeves' levels are perp. Chento and squeeze_bull walk essentially the
  same as on perp bars (parity to four decimals on 15 m; the 1 m walk gives +0.800 R vs +0.800). For
  SHORT_SQUEEZE the basis (sd 2.5 bp per 15 min, up to 10 bp historically) is 7–30 % of the stop distance;
  its numbers are what the live bot would experience (it tests the same perp levels against `btc_1m`), not what
  a perp-managed version would.
- Bars cannot show sub-bar gap-throughs, queue position or true tape markouts; the 5 s year is one regime;
  impact is assumed zero at these sizes. The stressed-bar subsets have n = 4–7.
- The fee assumptions (perp 2.0 / 5.0 bp, spot 10 / 10 bp; 1.8 / 4.5 and 7.5 with BNB) are the published VIP0
  schedule and were not verified against an account.
- CHENTO_ETH's 1 m gross (+0.653 R) is below its 15 m engine number (+0.712) because the finer path resolves
  stop-first ties differently; the study's relative comparisons are unaffected.

## Reproduce

From this directory with `venv\Scripts\python`: `run_e0_facts.py` → `run_e1_market_cost.py` →
`run_e2_passive_entry.py` → `run_e3_tp_fills.py` → `run_e4_stop_slippage.py` → `run_e5_conditional_as.py` →
`run_e6_recost.py` (logs in `results/log_e*.txt`), then `C:/Python/Python313/python.exe build_notebook.py` →
`execution_study.ipynb`. The 1 m and 5 s paths are cached under `cache/` (gitignored, ≈ 700 MB) by
`exec_lib.build_cache()`. Runtime ≈ 6 minutes. Environment: Python 3.13.11, numpy 2.2.6, pandas 3.0.0.

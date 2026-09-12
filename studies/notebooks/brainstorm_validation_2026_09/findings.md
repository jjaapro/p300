STATUS: CONCLUDED — 5 claims validated, 0 survive as stated; 2 engine claims (C4) confirmed; 4 side findings for p300

# Brainstorm session 2026-09-11 — independent validation

**Tag: AUDIT / replication. N_TRIALS = 1 on every decision-bearing test.** Rules, windows and
decision clauses were frozen in [README.md](README.md) before any number was computed. Written
2026-09-12 from the runs of 2026-09-11/12 (`results/log_*.txt`, `results/*.json`).

**One-line answer.** Every positive claim reproduces to the printed digit on the brainstorm's own
cached data — the arithmetic was right — and every one of them fails when the data the claim never
saw is added: other years, other assets, or the cost of the trade it implies. The two things that do
hold are the *engine* claims about the ADX machine (the consensus veto is a stop-fill artefact, and an
arbitrary daily bar boundary moves the Sharpe by ±0.15), and those transfer to p300's live ADX sleeve
with an even wider noise floor.

| id | claim | parity | independent / unseen data | verdict |
|---|---|---|---|---|
| C1 | big-bar daily momentum +193.5 bp, t 3.04; beats buy-and-hold | **exact** | BTC 2017-26 +149 bp t 2.15; Bitstamp 2012-17 +40 bp t 0.30; ETH +102 bp t 1.09; 64 alts +68 bp, week-clustered t 0.98; DSR(348) 0.22 | **KILL** (0 of 3 unseen samples; the "beats hold" is a start date + 1.56× leverage) |
| C2 | funding timing doubles the carry (+7.7 vs +3.4 %/yr) | exact on its 500-print window | 7,676 prints: timing rule earns **−6.1 %/yr net vs +11.7 % always-on**, loses in 7 of 7 years, CI90 [−20, −15] | **KILL** (conditional rate ≠ capital yield; 72 toggles/yr) |
| C3 | absorption 5 m / 2 h +4.8 bp, t 2.91 | **exact**, and p300's 5 s data is byte-identical to the cache | 15 m spot 7 y: +0.1 bp t 0.04; ETH perp −9.8 bp; on its own cache one year (2025) carries it; DSR(348) 0.49 | **KILL** |
| C4 | veto = stop-fill artefact; ±0.15 Sharpe bar-phase noise; no short edge | **4/4 cells exact**, veto inversion reproduced, 6-phase range reproduced | p300 live machine, 24 phases: Sharpe 0.70–1.08, maxDD −19 to −37 %, MAR 0.83–2.54; short leg +2.7 %/trade, CI90 [−2.8, +9.0] | **CONFIRMED** (noise floor) / **NO SHORT EDGE DEMONSTRATED** |
| C5 | Bollinger squeeze-down short; RSI(14)>75 long | squeeze H4/H12 exact, **H24 not reproducible**; RSI t 3.76 vs 3.78 | ETH and 64 alts: squeeze dead; RSI cell fails the pre-registered bar (ETH t 1.6 at the primary horizon; alts week-clustered t 1.35) | **KILL / KILL** |

Nothing was built or changed in `strategies/`, `bots/` or `data/`. prod.db was opened read-only
throughout; the `ai_trading` caches were read as pickles and never rewritten.

---

## C0 — data parity gate (passed)

The brainstorm ran on Binance-REST caches; p300 holds the same markets in its own tables. On the
overlap they agree to rounding: BTC daily close max relative difference 7e-4 (the cache's partial
last day), the 5 m cache aggregated to 15 m vs `cd_spot_15m` differs on **4 of 112,991 bars** (p300's
own known-outage rows, e.g. 2026-06-02 14:15), and `cd_spot_5s` aggregated to 5 m is **byte-identical**
to the cache over the whole common year (max relative difference 0.0 on close, volume and taker
volume). The Bitstamp pickle equals `studies/material/bitstamp_btcusd_daily.json` except the json's
partial last day.

Funding: a fresh fetch of `fapi.binance.com/fapi/v1/fundingRate` (7,676 settlements, 2019-09-10 →
2026-09-11) equals the brainstorm's `funding_BTCUSDT_full.pkl` on all 7,673 common prints (max
difference 0.0). **Side finding:** the `cd_funding_rate` rows stamped at settlement hours
(`timestamp % 28800 = 0`) equal the Binance settlements exactly over the whole pre-cutover history
(share equal 1.000, corr 1.00), so the filter in `strategies/support/funding.py` is sound; the memory
note that pre-2026-04-13 rows are "predicted rates" is true of the *between-settlement* rows only.
Bybit settlements correlate 0.67 with Binance — a different venue, not a data fault.

One gate criterion was relaxed relative to the README's wording: "disagree by more than rounding"
was applied as *fewer than 0.1 % of bars differ by more than 0.1 %* rather than as a maximum, because
the maxima are single outage rows and partial days. No replication is INCONCLUSIVE on data grounds.

---

## C1 — big-bar daily momentum → KILL

**Claim.** Daily bar with range z-score (60-bar window) > 1 → trade the bar's direction for 7 days:
+193.5 bp drift-adjusted, t = +3.04, clustered t = +3.99, n = 189; "the only cell in study 25 that
clears its Bonferroni bar"; at 100 %-of-equity sizing it "beats buy-and-hold on both axes".

**Parity.** Exact: n = 189, +193.5 bp, t = 3.043, tc = 3.99, win 55.6 %, capture 21.4 %. The
brainstorm's arithmetic is reproduced. Note the doc's own wording: the cell "clears the Bonferroni bar
of 3.13–3.44" only on the *clustered* t (3.99); the naive t (3.04) does not.

**Same rule, p300 data.** `cd_spot_binance` → daily, 2017-08-17 → 2026-09-10: n = 197,
**+149 bp, t 2.15, tc 2.68**, win 56 %. The gap to +193.5 is the four extra months of 2017 (six trades
averaging −857 bp). Per year: 2021 +523 bp (t 2.6), 2022 +315 (t 2.0), 2023 +196 (t 1.4) carry it;
2024 +60 (t 0.3), 2025 −94 (t −1.0), 2026 +395 on 12 trades. Post-ETF: +90 bp, t 0.89, win 47 %.
Causal trailing drift instead of full-sample drift: +171 bp, t 2.5. Raw (no drift adjustment):
long leg +314 bp, short leg −6 bp — the raw short leg makes nothing.

**Unseen data, all three fail the t ≥ 2 clause.**

| sample | n | mean bp | t | note |
|---|---|---|---|---|
| Bitstamp 2012-01 → 2017-08 (pre-Binance) | 105 | +40 | 0.30 | long leg +213, short leg −137 |
| ETH spot daily 2020 → | 146 | +102 | 1.09 | perp panel +136, t 1.55 |
| 64 alts 2023-05 → 2026-04, pooled | 3,503 | +68 | naive 2.38, **week-clustered 0.98**, asset-clustered 2.25 | 66 % of assets positive; 2 with t > 2, 2 with t < −2 |

Bitstamp by era: 2012-14 +14 bp (t 0.05), 2014-17 +42 (0.31), 2017-20 +119 (0.78), **2020-23 +384
(2.91)**, 2023-26 +89 (1.01). The whole result is one era.

**Plateau or spike.** The 60-cell grid (z × H × W) on p300 BTC: 95 % of cells positive, 22 % with
t > 2, the brainstorm's cell ranks 10th of 60 by t. This is not a spike — it is a broad, weak
"follow the daily bar" tilt across every parameter, the same daily trend-state tilt TA_SWEEP §5
labelled "real but unattributable" and said S-003/S-005 already harvest.

**Deflation.** DSR on the p300 series: N = 1 → 0.98, N = 24 → 0.56, N = 96 → 0.36, **N = 348 → 0.22**.
The iid bootstrap CI90 of the mean is [+35, +262] bp — positive on BTC 2017-26, but only there.

**The sizing claim is three artefacts.** The exact port of `run_leverage_sim2.simulate` reproduces
$50,499 / 20.3 % CAGR / −63.0 % maxDD (maker) and $41,128 / 17.5 % (taker) on the brainstorm's cache.
Then:

1. **Start date.** Their buy-and-hold benchmark starts 2017-12-08 — nine days before the 2017 cycle
   top — so it compounds from $15.8k. On p300's panel (2017-08-17, $4.3k) the *same strategy* returns
   $24,107 / **10.2 % CAGR** against buy-and-hold $186,373 / **38.1 %**. Trap #7 (drift / start date),
   applied to its own author.
2. **"Comparable sizing" is not comparable.** 100 % of equity per entry with up to three concurrent
   holds is **1.56× gross exposure while in the market** (mean 0.75× over all days, peak 4.6× because
   notional is fixed at entry while equity falls). Buy-and-hold is 1×. At 50 % per entry the run is
   $28,148 / 12.5 %.
3. **Funding is under-charged 3×.** The simulator charges 0.01 % once per *daily bar*; a perp charges
   it every 8 h. Corrected, the maker run falls to $33,528 / **14.8 %** — below the 19.9 % benchmark
   even on the flattering window.

The paired block bootstrap of the CAGR difference on their own window is +0.4 pp with CI90
[−59, +51] pp; the maxDD advantage is +20 pp with CI90 [−8, +44]. A random-direction,
frequency-matched control ends below the strategy in 98 % of seeds, so the direction tilt is not noise
*on this sample* — it is simply not an edge over holding, and not there elsewhere.

**Verdict: KILL** under the frozen rule (0 of 3 unseen samples, DSR 0.22). What survives is a
statement p300 already has: on BTC daily bars, following beats fading.

---

## C2 — funding-carry quartile timing → KILL

**Claim.** Funding lag-1 autocorrelation +0.75; next-print yield by current-print quartile −2.2 /
+2.5 / +5.5 / +7.7 %/yr; "holding only the upper half roughly doubles the yield versus holding
continuously" (+7.7 vs +3.4 %/yr). Reusable piece #1 in STATUS.md.

**Parity.** The numbers come from the last **500** prints (167 days), not the 400 days the script
requests: lag-1 0.745 (quoted 0.75), untimed 3.54 %/yr (3.40), positive 77.8 % (76.6 %), quartiles
[−2.0, +2.9, +5.4, +7.7] (quoted [−2.2, +2.5, +5.5, +7.7]). Reproduced.

**Persistence is real and stronger than claimed.** On 7,676 prints lag-1/2/3/6 autocorrelation is
0.80 / 0.74 / 0.69 / 0.65, every year between 0.55 and 0.82. With *causal* trailing-1-year quartiles
the next-print yield is 3.2 / 7.2 / 10.8 / **44.6 %/yr** from Q1 to Q4. Funding is positive 85.7 % of
the time and the untimed yield over the full history is **11.6 %/yr** (2021: 30.6 %; 2026 to date:
2.8 %). The brainstorm's +3.4 % is a low-funding window.

**The trade, net of its own turnover (the question CARRY_FINDINGS left open).** Decide at each
settlement from the just-settled print whether to hold the spot-long / perp-short pair through the next
interval; 0.20 % per round trip (S-078's `ENTRY_EXIT_COST_PCT`); 2019-12-19 → 2026-09-11 (6.74 y,
the span where a causal median exists):

| rule | share held | round trips / yr | gross %/yr | **net %/yr** |
|---|---|---|---|---|
| ALWAYS-ON | 100 % | 0.1 | 11.72 | **11.69** |
| UPPER-HALF (their rule, causal median) | 34 % | 72.1 | 8.37 | **−6.06** |
| TOP-QUARTILE | 14 % | 34.9 | 6.22 | −0.75 |
| hysteresis enter > p60 / exit < p40 | 41 % | 28.2 | 9.48 | 3.84 |
| UPPER-HALF with 21-day minimum hold | 65 % | 9.9 | 10.12 | 8.13 |
| **S-078 live rule** (7-day avg > 0 in, 3 negative days out) | 91 % | 4.8 | 11.80 | **10.85** |

UPPER-HALF loses to ALWAYS-ON in **7 of 7** calendar years and the block-bootstrap (90-print blocks)
difference is **−17.8 %/yr, CI90 [−20.1, −15.4]**. Even *gross* of costs it earns less (8.37 vs 11.72)
because the 66 % of the time it stands aside forgoes funding that is positive 86 % of the time.

**Why the claim was wrong.** "+7.7 % vs +3.4 %" compares the *annualised rate while held* (a
conditional mean) with the unconditional rate. A rate per unit of time held is not a yield on capital
unless the idle capital earns the same elsewhere. Timing raises the rate per held-interval and lowers
the total collected; each toggle then costs 21 days of carry (their own arithmetic). The persistence
finding is a genuine data fact; the trading conclusion drawn from it is not.

**Side finding for p300 (not decision-bearing here, pre-registered as a comparison).** The live
S-078 rule collects slightly *more* funding than never exiting (11.80 vs 11.72 %/yr gross) and then
pays 4.8 round trips a year for it, ending **0.85 %/yr below ALWAYS-ON, CI90 [−1.11, −0.60]**, and
below it in every year including 2022 (2.49 vs 4.16). Over 2019-2026 the three-negative-day exit
avoided nothing that its own toggle cost did not exceed. Post-ETF the gap is 6.23 vs 6.88 %/yr.
Whether the exit is worth keeping as tail insurance against a negative-funding regime that has not
occurred in this sample is a risk judgment, not something this data settles.

**Verdict: KILL** as a net claim.

---

## C3 — absorption, 5 m bars, 2 h hold → KILL

**Claim.** Volume z > 1, |imbalance| z > 1, |return| z < 0.5 on a 288-bar window → fade the aggressor,
hold 24 bars: +4.8 bp, t 2.91, episode-clustered t 2.16, n 1,620; the 2 h peak of a measured decay
curve; net +0.7 bp at 4.1 bp; "the best-behaved short-horizon signal in the repo".

**Parity.** Exact on every row of the dense decay table (1 h +2.88 / t 2.54; **2 h +4.79 / 2.91 /
tc 2.16, n 1,620**; 3 h +4.79 / 2.25; 4 h +1.58 / 0.62; 8 h +1.50; 16 h −1.41). z > 1.5 at 2 h:
+5.5 bp, t 2.06.

**On its own cache the cell is one year.** 2023 +3.2 bp (t 0.75, n 145), 2024 +3.4 (0.92, 356),
**2025 +7.9 (2.92, 572)**, 2026 +2.9 (0.96, 547). Pre-ETF +2.6 bp (t 0.58); post-ETF +5.0 (t 2.85).
Short leg +7.5 bp (n 744) vs long leg +2.5 (n 876). DSR: N = 1 → 0.998, **N = 348 → 0.49**.

**Exact-resolution replication.** `cd_spot_5s` aggregated to 5 m over 2025-06-08 → 2026-06-07 is
byte-identical to the Binance 5 m klines in the cache (105,120 bars, max relative difference 0.0 on
close, volume and taker volume). Same data, same rule, same numbers: 2 h +4.29 bp, t 1.56, n 665.
This validates p300's 5 s table and the port; it cannot validate the edge, because there is no
independent 5 m data with taker splits older than one year.

**Independent 7-year panels at 15 m** (W = 96 bars = 1 day, H = 8 = 2 h):

| panel | n | 2 h mean bp | t | clustered t | 1 h |
|---|---|---|---|---|---|
| `cd_spot_15m` 2019-09 → 2026-09 | 1,274 | **+0.11** | **0.04** | −0.49 | −4.65 bp, t −2.08 |
| `cd_futures_15m` (perp, where it would trade) | 366 | +4.17 | 0.90 | 0.66 | +1.98, t 0.63 |
| `cd_futures_eth_15m` | 269 | −9.76 | −1.36 | −1.28 | −3.88, t −0.75 |

Every longer horizon on the spot panel is negative; per year 3 of 8 positive (2022 +17 bp is the only
year above t 1). Truncation test passes (no lookahead). DSR(N = 1) 0.52; bootstrap CI90 [−4.8, +4.9] bp.

**Reading.** The 5 m number is reproducible and real *on 2025*; at the 15 m bar over seven years it is
zero, and on ETH it is negative. Since a 15 m bar is a coarser filter of the same flow, the honest
statement is: an effect that exists only at one resolution, mostly in one year, at +5 bp against a
4.1 bp round trip whose own uncertainty is ±1–3 bp per leg, has not been shown to exist outside its
discovery sample. The brainstorm already called it not tradable; it is also not confirmed as a signal.

**Verdict: KILL** (15 m spot t 0.04).

---

## C4 — the ADX regime machine → engine claims CONFIRMED, no short edge demonstrated

**Parity (their `scalp_lab.s005` engine, Bitstamp daily, 2014-01-01 → 2026-08-10).** All four cells
reproduce to the printed digit: study fill S-003 1.02 / 40.9 % / −58.8 %, S-005 1.05 / 41.4 % / −41.2 %;
live fill S-003 **1.08 / 43.6 % / −37.5 %**, S-005 1.01 / 39.4 % / −41.4 %. Buy-and-hold 42.3 % / Sh 0.86 /
−83.4 %. **The veto's benefit inverts under the live stop fill — confirmed.** Their 6-phase bar test
reproduces exactly (Sharpe 0.853–1.163).

**Bar-phase noise floor on p300's live machine** (`adx_study/harness.run`, `cd_spot_binance` → daily,
24 day boundaries, 2018-01-01 →):

| statistic | phase 0 (00:00 UTC, the live one) | range over 24 phases |
|---|---|---|
| closed trades | 35 | 32 – 40 |
| total return | +2,537 % | +886 % – +3,561 % |
| harness maxDD (trade-close equity) | −27.3 % | −19.2 % – −37.0 % |
| harness MAR | 1.67 | 0.83 – 2.54 |
| per-trade t | 2.03 | 1.28 – 2.15 |
| annualised Sharpe of the daily mark-to-market curve | 0.97 | **0.70 – 1.08** |
| mark-to-market maxDD | −50.9 % | −41.6 % – −61.9 % |

The Sharpe spread is 0.38, twice the brainstorm's ±0.15, so the **noise floor is confirmed and is
wider on p300's machine**. Two consequences for the ADX study (`adx_study/findings.md`):

- Its "maxDD −27.3 %" is the drawdown of the trade-close equity. Marked to market daily the same
  ledger draws down **−51 %** at the live phase (−42 to −62 % across phases), because a long that runs
  +60 % and exits +20 % after ADX fades is a −25 % drawdown the account experiences even though the
  ledger never shows it. Position sizing off the −27 % figure understates the path risk by half.
- The stop-fill model matters as much on p300's machine as on theirs: repricing SL exits at the
  breach bar's close (the brainstorm's "study fill") takes the daily curve from Sharpe 0.97 / CAGR
  39.7 % / MTM maxDD −51 % to 0.82 / 31.3 % / −61 %. The harness's fill-at-stop is the right live
  semantics, and its result is *better*, not worse.

Against holding: buy-and-hold over the same span is 22.2 % CAGR / −81 % maxDD / Sh 0.64; scaled to the
strategy's 48 % time in market, 16.1 % / −51 % / MAR 0.31 vs the machine's 39.7 % / −51 % / 0.78.
DSR of the daily curve: N = 1 → 0.998, N = 17 (the ADX study's documented sweep) → **0.85**.

**Does the ADX study's recommendation survive the noise floor?** Paired on the same bars, per phase:

| variant | improves harness maxDD | improves MAR | improves MTM maxDD | improves daily Sharpe | median Δ maxDD | median Δ MAR |
|---|---|---|---|---|---|---|
| Tier-1: ATR×4 catastrophe stop | 79 % of phases | **62 %** | — | — | +3.7 pp | **+0.04** |
| Tier-2: + short only below EMA150 | **92 %** | **79 %** | **96 %** | **92 %** | +8.0 pp | +0.42 |

Tier-2's drawdown reduction is robust to the bar boundary; Tier-1's MAR gain is inside the noise.
This is the first test of those two recommendations that does not depend on the one 00:00 UTC
sample they were fitted on. (It does not address the funding-carry argument for keeping counter-trend
shorts, which is a separate portfolio judgment — see the short leg below.)

**Short leg (their "single cheapest next test", run as written).** p300 machine, phase 0, 19 closed
shorts: price **+2.31 %** per trade, realised Binance funding +0.40 %, total **+2.71 %, CI90 [−2.84,
+8.98]**; 15 closed longs +31.5 %. Short-weight sweep (longs at 1×, shorts carry funding):

| short weight | CAGR | MTM maxDD | MAR | Sharpe |
|---|---|---|---|---|
| 0 | 41.8 % | −31.0 % | **1.35** | 1.245 |
| 0.25 | 42.9 % | −32.0 % | 1.34 | 1.242 |
| 0.5 | 43.2 % | −36.0 % | 1.20 | 1.18 |
| 0.75 | 42.5 % | −42.8 % | 0.99 | 1.09 |
| 1.0 (live) | 41.0 % | −49.6 % | 0.83 | 0.99 |

Drift controls cut the other way: the six shorts entered in the 2018 and 2022 bear years average
**+10.8 %**, and the same machine on ETH daily (2021 →) has 7 shorts averaging **+8.7 %** (sum +61 %)
against 11 longs at +18.9 %. So "no short edge" is the BTC-2018-26 average of trend-aligned shorts
that pay and counter-trend shorts that lose — which is the ADX study's own diagnosis, and why its
Tier-2 (short only below EMA150) beats "no shorts" in spirit even though w = 0 maximises MAR on this
sample. **NO-SHORT-EDGE-DEMONSTRATED** under the frozen clause; the sample is 19 trades and the
sizing decision stays with the user.

---

## C5 — residual TA cells → KILL / KILL

**Bollinger(20,2) squeeze released downward, short.** Parity on Bitstamp: H = 4 n 142 / +240.9 bp and
H = 12 n 90 / +526.4 bp reproduce exactly (my naive t 4.50 / 4.60 against the quoted cluster-robust
4.77 / 4.77). **The H = 24 row (n 46, +1,008 bp, t 4.19) does not reproduce on any panel**: Bitstamp
full n 70 / +829 / t 3.83; the two Binance daily caches n 43–45 / +405–455 / t 1.5–1.8. Recorded as a
parity gap. Unseen assets: ETH H4 +147 bp t 1.70, H12 t 1.18, H24 t 1.10; 64 alts pooled H4 −22 bp,
H12 +42, H24 −31 (week-clustered t −0.27 / +0.28 / −0.11), 52–58 % of assets positive. DSR at the
7,908-cell search: 0.77 / 0.75 / 0.50. KILL.

**RSI(14) crossing up through 75, long.** Parity needs the oscillator sweep's own panel (Bitstamp
from 2014-01-01): H = 4 n 57, +372 bp, **t 3.76** (quoted 3.78). On p300 BTC H4 +339 bp t 3.02. Unseen:
ETH H4 +325 bp **t 1.60** (fails the clause), H12 +1,063 bp t 3.97 on 15 trades (100 % win), H24
+1,882 bp t 3.48 on 12; alts pooled H4 +240 bp, naive t 2.57, **week-clustered 1.35**, 54 % of assets
positive; H24 +1,077 bp, naive 3.12, clustered 1.43, 64 %. Every RSI plateau cell on the alt panel is
positive with naive t 2–4.5 — the alts 2023-26 carry a broad follow-the-momentum tilt, the same
generic daily continuation C1's grid shows, not a property of RSI 75. DSR(7,908) at H4: 0.47. KILL by
the frozen rule; the honest footnote is that this is the one cell with any life on unseen assets, at
horizons ≥ 12 days, on samples of 12–15 trades.

---

## What the session got right, and what it got wrong

**Right.** Every number checked reproduces exactly from its own data — the code is clean, the
non-overlap / drift / clustering conventions are implemented as described, and the "methodological
traps" list is sound (this study reused it). The S-005 engine analysis is correct in every cell tested.
The persistence of funding is real and larger than stated.

**Wrong, in five recurring ways.**

1. **Selecting inside the discovery sample.** C1 and C3 are the best cells of 348 configurations and
   are reported with the deflation of one. At the study's own count both fall to DSR 0.2–0.5.
2. **Benchmark start date.** A buy-and-hold benchmark that starts nine days before the 2017 top is
   not a benchmark. The momentum sizing claim inverts (10 % vs 38 % CAGR) on a start four months
   earlier.
3. **Leverage hidden in "100 % of equity".** Three concurrent full-equity positions is 1.56× in the
   market and 4.6× at the worst moment. "Comparable sizing" was not.
4. **Conditional rates sold as capital yields.** The carry timing claim compares a rate-while-held to
   an unconditional rate and never charges the toggles it implies.
5. **Resolution- and year-specific effects generalised.** Absorption exists at 5 m in 2025; the
   15 m seven-year panel is flat and ETH is negative. The squeeze H = 24 cell could not be located.

**Side findings for p300** (none require action; all are recorded for the next decision on each
sleeve): the S-078 exit rule has cost 0.85 %/yr against never exiting over 2019-26 (C2); the ADX
sleeve's mark-to-market drawdown is ≈ −51 %, not −27 % (C4); Tier-2 is robust across bar phases,
Tier-1 alone is not (C4); `cd_funding_rate` settlement-hour rows equal Binance settlements over the
full history (C0).

---

## Reproduce

From the repo root with `venv\Scripts\python`: `run_c0_data_parity.py` → `run_c1_momentum.py` →
`run_c2_carry_timing.py` → `run_c3_absorption.py` → `run_c4_adx_engine.py` → `run_c5_ta_residuals.py`
(each writes `results/`; logs in `results/log_*.txt`), then
`C:\Python\Python313\python.exe build_notebook.py` → `brainstorm_validation.ipynb`. Total runtime
about ten minutes. The funding fetch and the 5 s → 5 m aggregation are cached under `cache/`
(gitignored); delete them to refetch. Environment: Python 3.13.11, numpy 2.2.6, pandas 3.0.0.

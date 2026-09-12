STATUS: CONCLUDED — SQUEEZE_BULL without its −2 % stop is a BUILD-CANDIDATE on every pre-registered clause (both no-stop variants); SHORT_SQUEEZE without its stop more than doubles expectancy but fails the safety clause by 0.007 under the research pool's overlap (passes under the bot's single-open semantics, post-hoc); chento and ADX keep their stops; fixed-R sizing beats fixed notional everywhere; no liquidation anywhere at shipped sizing

OUTCOME (2026-09-12, user go-ahead): SQUEEZE_BULL policy P1b (no stop, +3 % target, 48 h, 0.5× fixed notional) and SHORT_SQUEEZE policy P1 (no stop, no target, 6 h, fixed 1× notional) shipped as second paper variants `bot_squeeze_bull_nostop_v1` / `bot_short_squeeze_nostop_v1` in the same processes as the incumbents, with paired re-cut rules at 20 / 30 fires fixed in docs/calibration/squeeze_bull.md and short_squeeze.md; chento and ADX keep their stops; fixed-R sizing kept.

# Sizing style 2026-09 — account-level risk vs per-trade stops

**Tag: AUDIT + exit/sizing-policy decision. Pre-registration: [README.md](README.md), frozen before any run.**
Written 2026-09-12 from `results/log_s*.txt` and `results/*.json`. Same events, 1 m spot paths, venue-level
convention and measured taker costs as the execution study; sizing as shipped (fixed-R 1–2 % over the shipped
stop, 3× cap, $10,000 per bot). Nothing under `strategies/`, `bots/`, `data/` was modified.

**One-line answer.** The brainstorm's "no stop, time exit, account-level risk" style is not a general truth
and not a general error: it loses for chento (a 5×ATR stop already sits outside the noise and the tail it cuts
is real) and for ADX (signal-only exits add 8 pp of CAGR and 17 pp of drawdown), and it wins decisively where the
shipped stop sits inside the noise — SQUEEZE_BULL's −2 % stop and SHORT_SQUEEZE's 10 bp-below-the-swept-low
stop are hit 34 % and 55 % of the time and are the main reason those two sleeves earn little. Removing them
raises expectancy in both halves of the sample with no liquidation risk at shipped sizing. Leverage itself
does nothing here, exactly as the brainstorm said: fixed-R on a fixed capital is the best sizing rule on MAR
for every sleeve, compounding buys CAGR with drawdown, and the brainstorm's fixed notional is the worst.

## S1 — exit policy on identical entries (net of measured taker cost, shipped sizing)

R is in shipped-R units (the notional is the same under every policy, so a no-stop loss can exceed −1 R).
MTM maxDD is mark-to-market at UTC day ends in % of the $10,000 capital; MAR = %/yr ÷ |maxDD|.

| sleeve | policy | mean R | halves | win | worst R | %/yr | MTM maxDD | MAR | verdict |
|---|---|---|---|---|---|---|---|---|---|
| CHENTO_BTC (101) | **P0 shipped** | **+0.800** | +1.30 / +0.29 | 41 % | −1.0 | 27.0 | **−9.9 %** | **2.72** | — |
| | P1 time-only | +0.779 | +1.58 / −0.04 | 53 % | −5.8 | 26.0 | −14.9 % | 1.75 | KEEP THE STOP |
| | P1b target-only | +0.768 | +1.39 / +0.14 | 54 % | −5.8 | 26.0 | −15.9 % | 1.63 | KEEP THE STOP |
| | P2 catastrophe 3× | +0.714 | +1.35 / +0.07 | 53 % | −3.0 | 24.1 | −15.9 % | 1.52 | KEEP THE STOP |
| CHENTO_ETH (77) | **P0 shipped** | **+0.653** | +0.98 / +0.32 | 43 % | −1.0 | 16.5 | **−10.7 %** | **1.54** | — |
| | P1 time-only | +0.595 | +1.23 / −0.06 | 49 % | −3.4 | 13.4 | −20.4 % | 0.66 | KEEP THE STOP |
| | P1b target-only | +0.466 | +1.01 / −0.09 | 51 % | −3.4 | 11.0 | −21.7 % | 0.51 | KEEP THE STOP |
| | P2 catastrophe 3× | +0.328 | +0.76 / −0.12 | 51 % | −3.0 | 7.2 | −25.3 % | 0.29 | KEEP THE STOP |
| SHORT_SQUEEZE (71) | P0 shipped | +0.481 | +0.39 / +0.58 | 45 % | −1.0 | 1.5 | −7.9 % | 0.19 | — |
| | **P1 time-only** | **+1.164** | **+0.50 / +1.84** | 63 % | −7.8 | 8.8 | −12.6 % | **0.69** | KEEP THE STOP (safety clause, see below) |
| | P1b target-only | +0.665 | +0.37 / +0.97 | 66 % | −7.8 | 4.1 | −12.4 % | 0.33 | KEEP THE STOP |
| | P2 catastrophe 3× | +0.481 | +0.48 / +0.48 | 61 % | −3.0 | 1.8 | −12.9 % | 0.14 | KEEP THE STOP |
| SQUEEZE_BULL (122) | P0 shipped | +0.334 | +0.26 / +0.41 | 58 % | −1.0 | 8.2 | −4.4 % | 1.86 | — |
| | **P1 time-only** | **+0.566** | **+0.56 / +0.58** | 65 % | −4.2 | 14.7 | −4.0 % | **3.69** | **BUILD-CANDIDATE** |
| | **P1b target-only** | **+0.526** | **+0.54 / +0.51** | 69 % | −4.2 | 13.6 | **−3.3 %** | **4.09** | **BUILD-CANDIDATE** |
| | P2 catastrophe 3× | +0.422 | +0.49 / +0.36 | 67 % | −3.0 | 10.7 | −5.3 % | 2.03 | KEEP THE STOP |

Paired deltas vs P0 (day-block CI90 in `s1_exit_policies.json`): SHORT_SQUEEZE P1 +0.68 R, SQUEEZE_BULL P1
+0.23 R and P1b +0.19 R; chento −0.02 to −0.33 R.

**ADX** (daily harness, shipped Tier-2 parameters, 15 bp, 27 closed trades since 2018): shipped 10 % SL +
ATR×4 trail → mean trade +17.2 %, CAGR 40.3 %, MTM maxDD −38.1 %, MAR 1.06; signal-only exits → +21.3 %,
47.9 %, **−55.1 %**, MAR 0.87 (worst trade −28.9 %); 30 % catastrophe stop → +20.7 %, 44.7 %, −48.2 %, MAR
0.93. Both halves are higher without the stop, but MAR and drawdown fail: KEEP THE STOP. This is the same
engine fact the brainstorm validation found from the other side — the stops are load-bearing for the ADX machine.

## S2 — sizing rules on the shipped trades

| sleeve | fixed-R on fixed capital (today) | fixed-R on current equity (compounding) | fixed notional (brainstorm) |
|---|---|---|---|
| CHENTO_BTC | 18.0 % CAGR, −9.1 % maxDD, **MAR 1.98**, worst trade −2.0 % | 28.1 %, −22.2 %, 1.26, −2.3 % | 16.1 %, −11.9 %, 1.35, **worst −11.9 %** |
| CHENTO_ETH | 12.3 %, −10.2 %, **1.21**, −2.1 % | 15.9 %, −17.5 %, 0.91 | 11.3 %, −12.2 %, 0.93, worst −4.5 % |
| SHORT_SQUEEZE | 1.5 %, −7.2 %, **0.20**, −1.3 % | 1.3 %, −7.5 %, 0.18 | 0.1 %, −15.1 %, 0.01, worst −4.7 % |
| SQUEEZE_BULL | 7.3 %, −4.2 %, 1.73, −1.0 % | 8.4 %, −4.8 %, **1.75** | 7.3 %, −4.2 %, 1.73 (cap never binds; identical) |

Compounding raises CAGR by 1.1–1.6× and drawdown by 1.1–2.4×; fixed notional (the brainstorm's rule) is the
worst on MAR for every sleeve and produces the single worst trade (−11.9 % of equity on chento BTC, because a
fixed dollar size ignores the stop distance). Fixed-R is the right rule; whether it is applied to fixed or
current capital is a live-design choice (paper stays fixed-capital by design).

**Ruin table.** Under 0.5 % maintenance, the adverse move that liquidates a book at gross exposure g is
(1 − 0.005 g)/g: 99.5 % at 1×, 49.5 % at 2×, **32.8 % at 3×**, 19.5 % at 5×, 9.5 % at 10×. Worst observed
adverse moves since 2020: BTC −24.8 % in 1 h and −36.9 % in 6 h (2020-03-12/13), −51.3 % in 24 h, −53.6 % in
72 h; rises of +39.5 % (1 h) to +57.5 % (24 h); ETH −31.0 % in 1 h (2021-05-19), −54.2 % in 24 h. So a 3× book
held through a 6-hour window of that kind is liquidated; the 3× cap is a cap on *ordinary* risk, not on ruin,
and the pool design's 50 % collateral rule is what has to carry the tail.

## S3 — minute-by-minute liquidation walk

No liquidation episode for any bot under any policy, and none for a pooled $50,000 account carrying all four
sleeves (max gross 1.06× shipped, 1.24× without stops). Per-bot minimum distance to liquidation (the adverse
move that would have liquidated at the worst minute) and maximum gross exposure:

| bot | P0 shipped | P1 time-only |
|---|---|---|
| CHENTO_BTC | 0.29 at 3.3× (two overlapping trades) | 0.28 at 3.5× |
| CHENTO_ETH | 0.31 at 3.1× | 0.25 at 3.9× |
| SHORT_SQUEEZE | 0.33 at 3.0× | **0.16 at 6.1×** (two overlapping trades at the cap) |
| SQUEEZE_BULL | 1.05 at 0.95× | 0.96 at 1.03× |
| pooled $50 k | 0.94 at 1.06× | 0.81 at 1.22× |

**Decision rule 1 applied.** SQUEEZE_BULL P1 and P1b pass all five clauses (both halves higher, MAR higher,
maxDD not worse by > 5 pp, no liquidation, minimum distance ≥ 2× the worst in-hold excursion) →
**BUILD-CANDIDATE**. SHORT_SQUEEZE P1 passes four and fails the safety clause by 0.007 (distance 0.160 vs
2 × 0.083 = 0.167), because the research pool lets a second trigger open while the first is held (4 h cooldown,
6 h TIF) and the walk counts that at 6.1× gross. **Pre-registered verdict: KEEP THE STOP.**

**S3b, post-hoc and not pre-registered:** with the live bot's single-open semantics (one position per variant;
12 of 71 triggers dropped) SHORT_SQUEEZE P1 gives n 59, +1.13 R, halves +0.53 / +1.75, MAR 0.63, maxDD
−10.8 % (−2.9 pp vs P0), no liquidation, minimum distance 0.29 ≥ 2 × 0.048 — every clause would pass. This
is a sensitivity, not a verdict; it says the failed clause is an artefact of the pool, and that a properly
pre-registered no-stop SHORT_SQUEEZE is the study worth running next.

## What this changes (for the user's decision; nothing implemented)

1. **SQUEEZE_BULL**: drop the −2 % stop (keep the +3 % target and the 48 h time stop — P1b, the higher-MAR,
   lower-drawdown variant; P1 pure time exit earns more per year at slightly higher drawdown). Expected effect
   on the shipped sleeve: +0.19 to +0.23 R per fire, MAR 1.9 → 3.7–4.1, worst single trade −4.2 R (−2.1 % of
   capital at 0.5× notional). The sleeve was deployed 2026-09-09 with pre-registered re-cut points at n = 20 /
   30 fires; the clean way to act on this without tuning after ten fires is to run the no-stop policy as a
   second paper variant on the same signals and let the out-of-sample record decide.
2. **SHORT_SQUEEZE**: the stop is the problem, not the signal — with it the sleeve earns +0.48 R gross and
   1.5 %/yr at MAR 0.19; without it +1.13–1.16 R and 7–9 %/yr at MAR 0.6–0.7. Combined with the execution
   study (its coded 25 bp lump makes the paper record negative by construction) the sleeve as shipped cannot
   succeed; a pre-registered no-stop, corrected-cost variant is the decision to take, or retire it.
3. **CHENTO, ADX**: keep the stops; the brainstorm style loses on both.
4. **Sizing**: keep fixed-R; do not adopt fixed notional; compounding is a live-design choice that trades MAR
   for CAGR. The 3× cap does not protect against a 2020-03-12-class move (liquidation at −32.8 %); the pool
   design's 50 % collateral buffer is the layer that must.

## Caveats

Tail risk moves from the trade to the account under the no-stop policies: worst trades of −4.2 R (SQUEEZE_BULL)
and −7.8 R (SHORT_SQUEEZE) are −2.1 % and −6.8 % of capital at shipped sizing, and a stop-less 3× SHORT_SQUEEZE
position through a 2020-03-12 move (−37 % in 6 h) is a liquidation; none occurred in the 2022 → 2026 event
window, and the safety clause used the observed excursions, not that tail. The event sets are 71 and 122
trades; four policies were compared (declared); the SQUEEZE_BULL result rests on 122 bull-gated fires of which
the 2026 out-of-sample set is ten. Spot 1 m paths stand in for perp fills (execution study E0: basis sd 2.5 bp
per 15 min), which matters most for SHORT_SQUEEZE's 35 bp stop.

## Reproduce

From this directory with `venv\Scripts\python`: `run_s1_exit_policies.py` → `run_s2_sizing_rules.py` →
`run_s3_liquidation.py` → `run_s3b_single_open.py` (post-hoc), then `C:/Python/Python313/python.exe
build_notebook.py` → `sizing_style.ipynb`. Reads the execution study's `cache/` and `results/e6_recost.json`.
Runtime ≈ 4 minutes.

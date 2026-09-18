STATUS: CONCLUDED 2026-09-15 — squeeze_bull arm, REPORT-ONLY. Nothing is decided and nothing may change before the
n = 20 / 30 re-cuts (`docs/calibration/squeeze_bull.md`). No production change.

# Exit-policy study, squeeze_bull arm — findings (report-only)

**One-line answer.** On the stop variant the 48 h time stop does nothing either way: the 30 historical trades it
closed split exactly 15 targets and 15 stops when held, for the same average result. On the no-stop twin it is the
exit that limits the damage: without it, 15 of 122 trades end on a −10 % catastrophe stop and drawdown more than
doubles. Bounces often run past +3 %, but no wider target, no-target or "flush resumed" exit beats the shipped exit
by more than noise.

Pre-registration: [PREREGISTRATION_SQUEEZE_BULL.md](PREREGISTRATION_SQUEEZE_BULL.md), frozen in
`results/squeeze_bull/freeze_F0.json` (2026-09-15 13:26:44 UTC) before any arm outcome on this path; report written
13:26:49 UTC.

## 1. What was measured

- **Fires:** the 122 bull-regime OI-flush fires of the revalidation ledger (causal regime gate), 2022-03 → 2026-09.
- **Path:** Binance BTCUSDT perpetual 1-minute bars (the traded venue; the earlier sizing and re-cut replays used
  spot). The ledger's entry price equals the perp close at the end of every trigger hour, and the sleeve's own hourly
  replay reproduces all 122 ledger outcomes (preconditions Q2, Q3).
- **Accounting:** R = 2 % of entry for every arm; the booked 7 bp; actual funding. Every fire in every arm (paired);
  the live one-position-at-a-time guard in a separate sequence.

## 2. Results (net R per trade, all 122 fires)

| Arm | Mean net R | Win | Worst | Exit mix | Paired vs its baseline (95% interval) | 2022-03 → 2024-06 / 2024-06 → 2026-09 |
|---|---:|---:|---:|---|---|---|
| **S0** shipped (−2 %, +3 %, 48 h) | **+0.259** | 57 % | −1.07 | target 49, stop 43, time 30 | — | — |
| 12 h | +0.123 | 57 % | −1.04 | time 92 | **−0.136** (−0.265, −0.008) | −0.09 / −0.23 |
| 24 h | +0.227 | 60 % | −1.05 | time 56 | −0.032 (−0.098, +0.037) | −0.03 / −0.03 |
| 72 h | +0.257 | 55 % | −1.09 | time 18 | −0.002 (−0.070, +0.061) | −0.00 / −0.01 |
| **no time stop** | +0.259 | 52 % | −1.16 | target 64, stop 58 | **+0.000** (−0.121, +0.111) | +0.06 / −0.13 |
| target +4 % | +0.250 | 52 % | −1.07 | | −0.009 (−0.089, +0.062) | +0.01 / −0.05 |
| target +5 % | +0.310 | 52 % | −1.07 | | +0.051 (−0.041, +0.136) | +0.07 / +0.02 |
| target +6 % | +0.267 | 52 % | −1.07 | | +0.007 (−0.109, +0.114) | +0.00 / +0.01 |
| no target (stop + 48 h) | +0.261 | 52 % | −1.07 | | +0.002 (−0.135, +0.151) | −0.02 / +0.04 |
| flush resumed (no time stop) | +0.238 | 52 % | −1.09 | event 25 | −0.021 (−0.126, +0.074) | +0.06 / −0.17 |
| **N0** shipped twin (no stop, +3 %, 48 h) | **+0.468** | 68 % | −4.23 | time 67, target 55 | — | — |
| twin, no time stop, −10 % catastrophe | +0.509 | 85 % | −5.24 | target 104, catastrophe 15 | +0.040 (−0.491, +0.460) | +0.27 / **−0.40** |
| twin, no target (48 h only) | +0.512 | 65 % | −4.23 | time 122 | +0.043 (−0.079, +0.181) | +0.04 / +0.05 |
| **twin minus incumbent (N0 − S0)** | | | | | **+0.209 (+0.071, +0.337)** | +0.30 / +0.03 |

In the bot's own sequence (one position at a time, $10,000, 1 % risk): shipped incumbent 118 trades, +31 %, maximum
drawdown 4.6 %; no time stop 111, +31 %, 6.6 %; shipped twin 103, +43 %, 4.2 %; twin without its time stop 76, +30 %,
**11.0 %**; twin without a target 93, +46 %, 4.7 %.

## 3. What this says

- **The incumbent's 48 h time stop is not the problem, and not a help.** The 30 trades it closed were +0.17 R on average
  at 48 h; held to their stop or target they finished +0.17 R, 15 on each side, a median 79 h later. Removing it changes
  nothing on average and adds drawdown (4.6 % → 6.6 %). Twelve hours is clearly too short; 24–72 h are the same.
  SJ-4250 is one trade from that coin flip.
- **For the no-stop twin the time stop is its risk control.** Without it the twin wins more often (85 %) but 15 trades
  run to a −10 % catastrophe stop, 2025–2026 lose 0.8–1.2 R per trade on it, and drawdown rises from 4.2 % to 11.0 %. The
  live twin has no catastrophe stop at all, so its 48 h exit is the only thing bounding a loss.
- **How far bounces run.** Within 48 h the best price is a median +2.7 % above entry (reached a median 28 h in); within
  7 days a median +5.4 %. Of all fires, 52 % reach +3 % before −2 % within 7 days, 43 % +4 %, 36 % +5 %, 30 % +6 %. Wider
  targets trade more winners for fewer hits and land within ±0.05 R of +3 %. The average move keeps rising after 48 h
  (+1.15 % at 48 h, +2.12 % at 7 days), but these fires occur only in a bull regime. An exploratory control added after
  the report (`results/squeeze_bull/exploratory_regime_drift.json`, not pre-registered) measures every hour of the same
  causal bull regime: +0.41 % at 48 h and +1.20 % at 7 days. The fires' excess over that drift is +0.52 points at 24 h,
  +0.74 at 48 h and +0.92 at 7 days, so the flush-specific bounce is largely done by 48 h; from 48 h to 7 days the fires
  gain 0.97 points against 0.79 for any bull-regime hour. Holding past 48 h mostly buys market drift.
- **"Flush resumed" is not an early warning.** It fired on 25 trades and made the result slightly worse.
- **The twin's evidence, re-tested cleanly.** The sizing study's S1 reported gross R on spot prices. On the perp path,
  net of cost and funding and paired on the same fires, the twin beats the incumbent by +0.21 R per trade and the
  interval excludes zero, but the gain is +0.30 R in the first half and +0.03 R in the second. The direction of the
  2026-09-12 decision holds historically; its size since mid-2024 does not. The n = 20 / 30 paired re-cut on live fires
  remains the decision point.

## 4. What it permits

Nothing, by design. The findings are inputs for the pre-registered re-cuts: the incumbent's time stop is not a lever
worth changing; the twin's time stop should not be removed without a catastrophe stop and an exposure check; and the
twin's historical paired advantage has faded. No threshold of the re-cut rules is touched.

## 5. Limitations

- 122 fires, clustered in episodes (48 in 2023, 41 in 2024), and every year already used by earlier squeeze_bull work.
- 1-minute bars: stop before target within a minute; targets fill at the level (execution study: touches trade through).
- The live paper bot books a perp entry against a spot exit price; this study uses the perp path for both.
- Forward moves include the bull-regime drift; no regime-matched control was pre-registered for them.

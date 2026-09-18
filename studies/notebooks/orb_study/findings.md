STATUS: CONCLUDED 2026-09-15 — pre-registered confirmatory campaign ended at validation (both candidates failed every
performance clause); frozen verdict INCONCLUSIVE + PRICE_SIGNAL_ONLY for P0 and EXT_WIDTH, with negative point
estimates; lockbox not opened for confirmation. No production change, no bot, no paper trading.

# Opening-range breakout (ORB) on BTC/ETH perpetuals — findings

**One-line answer.** A New York opening-range breakout on Binance BTCUSDT/ETHUSDT perpetuals does not pay its costs.
BTC P0 earned +12.1 bp per trade gross in 2020–22 but only +0.6 bp after an 11.6 bp round trip, and −6.8 bp net per
trade in the 2023–24 validation block. Most of the gross that does exist comes from the shape of the trade (a stop at
the range edge held into the US session), not from the direction of the break.

Pre-registration: [PREREGISTRATION.md](PREREGISTRATION.md) (v1.0, with Amendment A1 to the data gate, made before any
outcome). Plan and literature: [TEST_PLAN.md](TEST_PLAN.md), [RESEARCH.md](RESEARCH.md). Notebooks 01–05 recompute every
number below from the frozen panels.

## 1. What was tested

| Item | Choice |
|---|---|
| Data | Binance USD-M perpetual 1-minute archive, BTCUSDT and ETHUSDT, 2020-01-01 → 2026-09-13, every zip checksum-verified; actual funding settlements |
| P0 | 09:30–09:45 New York range on full NYSE days; first 1-minute close beyond it; fill at the next open; stop at the opposite boundary; exit 16:00 New York; one trade per session; 1x notional |
| Family (46) | 3 anchors (NY, London 08:00, UTC 00:00) × 4 range lengths × 2 entries (close, resting stop); 14 one-change variants (buffers, direction gate, relative volume, width band, deadlines, midpoint stop, 1R/2R/3R targets, 60-minute exit); 2 interactions; **6 exit-event arms** added at the user's request (close back inside the range on 1m or 15m, VWAP cross, trailing stop, no time exit, trailing + no time exit) |
| Controls | opening momentum, clock-only long and short, break-fade, four placebo anchor shifts, 200 random-direction and 200 random-time seeds, buy-and-hold |
| Costs | `db` = 5.0 bp taker fee + 0.3 bp half-spread + 0.5 bp slippage per leg (11.6 bp round trip) plus actual funding; sensitivities 5–30 bp |
| Chronology | development 2020–22 (BTC, selection), validation 2023–24 (BTC decides, ETH transfer), lockbox 2025-01-01 → 2026-09-13 |
| Freezes | data gate 07:36:56Z → **F0** 07:49:10Z (rules, code, calendars, data; policy registry `10936b4b…`) → development → **F1** 07:50:31Z → validation → **F2** 07:52:05Z → **verdict** 07:52:23Z, all 2026-09-15, each written before the next block's outcome existed |

Engine checks before F0: 50 synthetic fixture and accounting tests; exact field-by-field parity between the reference
loop and an independent vectorized implementation on every BTC development trade of the 42 policies it covers; the
randomized-control walker against the reference walk; decisions unchanged when future minutes are deleted; two
end-to-end smoke runs on a synthetic random walk (P0 gross +2.7 bp, standard error 2.4 bp: no built-in bias).

## 2. Verdict

| Candidate | Block | Trades | Net bp/trade (95% CI) | Half-years positive | +5 bp stress | Continuation rule |
|---|---|---:|---|---|---:|---|
| **P0** | BTC validation | 490 | **−6.8** (−15.9, +2.8) | 0 of 4 | −11.8 | fail |
| **EXT_WIDTH** (challenger) | BTC validation | 275 | **−10.8** (−25.1, +4.6) | 1 of 4 | −15.8 | fail |
| P0 | ETH validation | 486 | −7.2 (−21.0, +7.8) | — | — | (transfer, reported) |
| EXT_WIDTH | ETH validation | 276 | −8.2 (−26.7, +12.1) | — | — | (transfer, reported) |

No candidate passed, so the lockbox stayed closed and the registered verdict came from validation:
**INCONCLUSIVE + PRICE_SIGNAL_ONLY** for both. The label is "inconclusive" only because the upper end of each 95%
interval still reaches the +2 bp hurdle; the point estimates are clearly negative. Under the pre-registration a
validation failure ends this campaign; a revised idea needs a new ID and data not yet seen.

EXT_WIDTH (trade only when the range's width sits between the prior 60 sessions' 20th and 80th percentiles) was chosen
by the frozen rule: CORE_NY_R60_STOP had the highest development Sharpe (0.565), EXT_WIDTH (0.557) tied within 0.02 and
changes one component instead of two.

## 3. Development (BTC 2020–22), in numbers

- P0: 735 trades, gross +12.1 bp, net +0.6 bp (95% CI −10.3 to +11.9, p = 0.45), daily Sharpe 0.06, max drawdown −35%.
- Anchors: NY policies +11 to +19 bp gross; London −4.5 to +4.6; UTC midnight −5.0 to +8.2.
- Placebo shifts of P0: −120 min −8.9 bp, −60 min −4.1, 0 min +12.1, +60 min +7.2, +120 min −1.0.
- Opening momentum (no break needed): gross +10.6, net −0.9. P0 minus momentum: +1.0 bp per day (CI −4.4 to +7.2, p = 0.35).
- Random direction on P0's own entries and stop distance: median gross +6.6 bp; P0 at the 88th percentile. Random time and
  direction: median +1.7 bp; P0 at the 97th.
- Nothing survives multiple testing: StepM over 46 policies rejects none (best t = 0.98, critical value 2.61); deflated
  Sharpe at 46 trials 0.000 (P0) and 0.004 (EXT_WIDTH).
- Power: the minimum detectable effect at 80% power is 17 bp per trade for the validation block, eight times the +2 bp
  hurdle. Two years of one trade a day cannot confirm a few-bp edge; they can only reject large ones.

## 4. Why it fails (exploratory, after the verdict)

All 46 policies and the controls were then run on every block for both assets (PREREGISTRATION.md section 10). These
are descriptions, not findings.

1. **Costs.** Over 2020–26 BTC P0's break-even round trip is 7.5 bp, below the 10 bp taker-fee floor; ETH's is 11.5 bp,
   the cost model itself. By year, BTC P0's gross was +8.8, +3.5, +24.2, +3.5, +5.8, −1.5, +7.8 bp (2020 → 2026): above
   the 11.6 bp round trip only in 2022. Before the US spot-ETF launch (2024-01-11) +10.0 bp, after +3.6 bp. ETH cleared
   the line in 2022, 2025 and 2026 (+24.1, +17.9, +19.6 bp) and did not decay.
2. **Most of the gross is the trade's shape, not the break's direction.** Coin-flip-direction versions of P0's own trades,
   with the same stop distance and exit, earned median gross of +6.6 / +10.7 / +5.7 bp (BTC, three blocks) and +8.0 /
   +9.9 / +11.4 bp (ETH). On a synthetic random walk the same construction earns about zero, so this is a property of the
   market: positions held from the US open into the US afternoon with a stop at the range edge profit from intraday
   momentum whichever way they face. The breakout's own contribution (P0 minus that median) was +5.5 bp in 2020–22,
   −6.0 bp in 2023–24 and −3.3 bp in 2025–26 on BTC; +4.2, −5.5 and +7.2 bp on ETH.
3. **The US session matters, the breakout rule does not.** The NY anchor had the highest mean gross in five of six
   asset-blocks; midnight UTC was negative in every block after 2022; the placebo two hours earlier (07:30 New York) was
   the lowest or second-lowest of five shifts in every block. Opening momentum beat P0 on both assets in 2023–24.
4. **Tail dependence.** The best 20 of 1,641 BTC P0 trades carry 106% of all gross; the rest net to slightly negative.
   Relative opening volume did not separate BTC trades (terciles +7.9, +7.4, +8.0 bp); narrow ranges did better than wide
   (+13.7 vs +0.1 bp), on both assets.
5. **Not beta.** Correlation with holding the perpetual is about zero on both assets.

## 5. Exit events

The user asked what exit events could work for each strategy, and distrusts time stops. For ORB the answer is clear
and consistent with the repository's earlier lessons ("wider targets beat tight ones on the same stop", "tight trailing
stops underperform"). Across the six asset-blocks (BTC and ETH × three periods):

| Exit arm (each changes only P0's exit) | Blocks with gross above P0 | Blocks net positive | Mean gross − P0 |
|---|---:|---:|---:|
| No time exit: hold to the opposite-boundary stop (7-day cap) | **4 of 6** | 3 of 6 | **+10.8 bp** |
| 3R target | 0 | 1 | −3.7 |
| 2R target | 0 | 0 | −5.6 |
| 1R target | 0 | 0 | −10.0 |
| Exit on VWAP cross | 1 | 0 | −6.9 |
| Exit on a 15-minute close back inside the range | 2 | 0 | −3.9 |
| Exit on a 1-minute close back inside the range | 2 | 0 | −4.7 |
| Trailing stop one range width behind the best price | 0 | 0 | −6.4 |
| Trailing stop, no time exit | 1 | 0 | −6.5 |
| Time exit 60 minutes after entry | 0 | 0 | −8.2 |
| Stop at the range midpoint | 2 | 1 | −2.0 |

What this says:
- **Invalidation exits cut the trades that pay.** "Exit when the trade is shown wrong", implemented as the first close
  back inside the range, was below P0 in four of six blocks and never net positive. Breakouts routinely retest the range
  before they run; a quarter of P0's stopped trades had been at least 1R in profit first, and trades that reached 16:00
  peaked a median three hours after entry. Only the structural stop (the opposite boundary) does not destroy the payoff.
- **The time exit caps winners.** Removing it was the only change that raised gross, in all three BTC blocks and ETH
  2025–26. But the result is a different strategy: a trend-following position with a win rate near 10%, a −72%
  development drawdown, the best ten BTC trades carrying 130% of its gross, and a strong long bias (long +39 bp vs short
  +6 bp). It is not a fix for ORB.
- **Scope.** These are momentum-type payoffs. They support designing the exit-policy study (BACKLOG item 11) with
  "no time stop, structural stop only" arms, but they say nothing directly about mean-reversion trades such as
  squeeze_bull, where a fixed target is part of the thesis. One hindsight trade (SJ-4250) is not evidence either way.

## 6. What it leaves (hypotheses, not findings)

- **US-session intraday momentum** as its own hypothesis: the random controls, anchors and placebo shifts all point at
  it. It would need a mechanism statement (the US cash session bringing directional flow that persists for hours),
  maker execution or a much larger gross per trade, and a pre-registration tested on data after 2026-09-13; every
  historical year is now seen.
- **Do not retest** without a materially new ingredient: ORB entries, filters or exits on BTC/ETH perpetuals at the
  NY, London or UTC opens with taker execution.

## 7. Data facts worth keeping

- Binance's public archive has continuous **perpetual** 1-minute bars and funding for BTCUSDT and ETHUSDT from 2020-01;
  the earlier "BTC perp 1m history is short" limitation applied to the local tables, not to the exchange.
- Zero-volume bars are outage filler (369 BTC, 302 ETH minutes; runs up to 99 minutes, e.g. 2023-11-10 15:07Z,
  2024-10-28 20:00Z) and must be treated as missing.
- Binance sets some 1-minute bar opens to the previous close rather than the first trade, and assigns a few boundary
  trades to the adjacent minute (Amendment A1). Archive and REST bars agree exactly.
- BTCUSDT's tick changed from 0.01 to 0.10 on 2022-02-15.

## 8. Limitations

- Execution costs are modelled, not measured for ORB entries; the fee is today's 5.0 bp applied to every year (conservative
  for part of the sample). One-minute bars cannot order events inside a minute; ambiguous bars are booked pessimistically
  and flagged. No P0 trade had an ambiguous bar; two ETH P0 trades (2020-02-28, 2025-01-14) held through tradeless
  minutes and resumed at the next traded open.
- The freezes are recorded as SHA-256 manifests with UTC timestamps, not git commits, because commits are made only on the
  user's request.
- The exploratory tables span 54 rules × 2 assets × 3 blocks. Individual cells that look good (for example ETH NY 30- or
  60-minute ranges) are expected by chance and are not findings.
- The lockbox block was used only by the post-verdict exploratory run. It is no longer unseen data for this idea family.

## 9. Reproduce

From the repository root: `venv\Scripts\python.exe studies\notebooks\orb_study\orb_data.py download`, then `... orb_data.py build`
(inputs), `... orb_checks.py` and `... orb_parity.py` (gate and engine checks). `orb_run.py` refuses to redo a stage whose
freeze exists. `... orb_explore.py` regenerates the exploratory tables, and
`C:/Python/Python313/python.exe studies\notebooks\orb_study\build_notebooks.py` re-executes notebooks 01–05, which
recompute from the panels and compare with the frozen results. Trial ledger: `trial_ledger.csv`.

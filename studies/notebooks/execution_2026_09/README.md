# Execution study 2026-09 — pre-registration (frozen before any result)

Written 2026-09-12 before any `run_e*.py` produced a number. Follow-up to
`studies/notebooks/brainstorm_validation_2026_09/` (Track E of the approved plan). Tag: AUDIT +
execution-policy decision. Nothing under `strategies/`, `bots/`, `data/` is modified; prod.db is opened
read-only; the sleeves' own historical trade lists are the inputs.

## Question

What does execution actually cost each running sleeve, which order type should each leg use, and how much
expectancy is at stake? p300 has no live execution: paper fills are the last CLOSED 1-minute close and the
cost is a lump `fee + slippage` at close (`strategies/trades.py:40-60`). None of those constants were measured.

## Data (all read-only, all on disk)

| table | use |
|---|---|
| `btc_1m`, `eth_1m` (2020-01 → now, `open_time` ms, Binance spot 1 m) | 7-year fill paths, stop and TP walks |
| `cd_spot_5s` (2025-06-08 → 2026-06-07, Binance spot 5 s with taker split) | fine-resolution fills, spread, markouts |
| `cd_spot_15m`, `cd_futures_15m`, `cd_futures_eth_15m`, `cd_futures_ohlcv` | parity replays, spot-vs-perp basis |
| `cd_open_interest`, `cd_funding_rate` | short_squeeze macro gate |

Cached as `cache/*.npz` (gitignored) by `exec_lib.build_cache()`; the cache is a verbatim copy of the tables.

## Event sets (the sleeves' own validated trade lists; nothing re-tuned)

| sleeve | source | entry time | levels | TIF |
|---|---|---|---|---|
| CHENTO_BTC, CHENTO_ETH | `overlay_study/results_backonly/trades_{BTC,ETH}.csv`, OKX-aligned (long: `okx_delta_z >= 0`, short: `<= 0`), no tilt | `ts` + 15 min (the trigger bar's close) | `stop`, `target` (5×ATR, 6 R) from the file; for a different fill price the stop DISTANCE and the 6 R target are kept relative to the fill | bars opening ≤ `ts` + 72 h |
| SHORT_SQUEEZE | port of `short_squeeze_sessions/strategy_backtest.ipynb` long trigger (percentile params 0.15 / 0.70 / 0.10, sweep of the prior 24-bar low, London/NY, short-macro day, 16-bar cooldown), 2022-01-30 → now | trigger bar + 15 min | stop = trigger low × 0.999 (a LEVEL, kept fixed); target = fill + 3 × (fill − stop) | 6 h from entry |
| SQUEEZE_BULL | `squeeze_bull_revalidation/results/full_oi_flush_ledger.csv`, `regime_backonly == bull_30d` (the shipped causal gate) | `ts` + 60 min (trigger bar's close) | stop = fill × 0.98, target = fill × 1.03 | 48 hourly bars after the trigger bar |
| ADX | `brainstorm_validation_2026_09/results/c4_ledger_p300.csv` closed trades (shipped machine, phase 0) | `entry_dt` + 1 day 00:00 UTC | ledger exits; SL exits re-walked on 1 m for E4 | n/a |
| CARRY | n/a (four fee-only fills per toggle, ~4.8 toggles/yr, price P&L zero) | — | — | E6 reports cost per toggle only |

R4 is HELD and its regime gate is not reproduced here; it is out of scope. The 1 m / 5 s paths are SPOT; the
chento and squeeze_bull levels are perp prices, so they are re-based by the spot/perp close ratio at the
signal (E0 measures the basis this introduces).

## Fee and adverse-selection assumptions (to be verified against the account API before any prod change)

Binance USDT-M perp: maker 2.0 bp / taker 5.0 bp per side (1.8 / 4.5 with BNB). Spot: 10 / 10 bp (7.5 with BNB).
Adverse selection on a maker leg: 1.0 bp baseline, 2.7 bp in stressed minutes, sensitivity 0.2–3.0 bp (the
brainstorm's measured range on 90 days of aggTrades). Tick size 0.1 USDT (perp) / 0.01 USDT (spot).

## Experiments

- **E0 facts and parity.** (a) Is `btc_1m` spot or perp: median |1 m close − hourly close| vs `cd_spot_binance`
  and `cd_futures_ohlcv`. (b) Spot-vs-perp basis on overlapping 15 m closes: mean, sd, p95 of |basis| in bp,
  full history and the 5 s year — the error bar on every 5 s spot-as-perp fill below. (c) Parity gates:
  CHENTO_BTC/ETH reproduce the audit's 72 h base replay on 15 m bars (BTC +0.800 R pre-cost / +0.685 R at 18 bp
  scaled by entry/risk; ETH +0.712 / +0.622) to 0.001 R; the SHORT_SQUEEZE port reproduces n = 70, WR 44.3 %,
  mean +0.40 R, PF 1.65 at 2 bp/leg on 2022-01 → 2026-05-18 (n exact, mean within 0.01 R); SQUEEZE_BULL
  reproduces the ledger's `r_outcome` per row on hourly bars (max |diff| < 1e-6). A failed gate makes that
  sleeve's rows INCONCLUSIVE, not "fixed".
- **E1 market-order cost.** Realised half-spread: Roll estimator on 5 s closes per hour (spread = 2·sqrt(−cov)
  of consecutive 5 s returns, cov < 0 else NaN) and Corwin–Schultz on 1 m high/low per day over 2020 → now.
  Decision-to-fill drift: paper books the last CLOSED 1 m close at the decision tick; the achievable market fill
  is the next bar's open (1 m, 7 y) or the 5 s open at 0 / 30 / 60 s after the boundary (5 s year, the bot's
  60 s tick), signed adverse in the trade direction. Reported per sleeve at its entry and exit times, in bp,
  with day-block CI90; impact is taken as 0 at $10–30 k notional and stated as such.
- **E2 passive entry.** Limit at the signal price (the close the paper bot books), patience T ∈ {1, 5, 15, 60}
  min, fill rule touch (low ≤ limit) and through (low < limit), fallback cancel→market (fill at the bar open
  after T) or cancel→skip. Fill rate, price improvement at fill, and the fill-weighted expectancy: the trade
  outcome recomputed from the fill price with the sleeve's own levels/TIF on the 1 m path (7 y) and on the 5 s
  path (1 y); an unfilled signal under cancel→skip contributes 0 R. Compared with the market-entry expectancy
  of the same signals.
- **E3 take-profit fills.** Over the market-entry walk's target hits: share where the extreme traded through the
  target by ≥ 1 tick and by ≥ 1 bp (1 m and 5 s); for the touch-only cases, the outcome if the resting TP had
  not filled (walk continues to stop/TIF). Output: the fill rule a resting TP should be simulated with.
- **E4 stop slippage.** For every stop exit: fill under stop_path semantics on 1 m (first bar with low ≤ stop:
  fill = min(open, stop)) and on 5 s; slippage = (stop − fill) / entry in bp, signed adverse; stressed subset
  = bars whose 1 m range is in the top 5 % of its year. SHORT_SQUEEZE's bp-wide stop reported separately.
- **E5 conditional adverse selection.** On 5 s bars: markout of a passive touch at the previous close, at
  1 / 5 / 15 / 60 min, at sleeve signal times (all fires, incl. non-gated, to gain n) vs 20 random times per
  signal matched by hour-of-day. Statistic: conditional minus unconditional markout with day-block CI90. This is
  an approximation of the tape (5 s bars, spot); a null result here is "not detectable at 5 s", not "absent".
- **E6 re-cost and decide.** Each sleeve's trade list under M0 = the coded lump (research-coded and
  sleeve-coded where they differ), M1 = measured taker (2 × 5.0 bp fee + 2 × half-spread + decision-to-fill
  drift + E4 stop slippage on stop exits), M2 = hybrid (maker entry at 2.0 bp + 1.0 bp AS, fill-weighted from E2
  with cancel→skip at the best T ≤ 15 min; resting TP at 2.0 bp with the E3 fill rule; stop and time exits
  taker as M1), M3 = M2 with time exits also maker (stops always taker). Per sleeve: gross mean R, cost in R,
  net mean R, cost share of gross, MAR, both halves (split at the median event time).
- **E7 live quoting probe.** Design only (spec in findings.md); not run.

## Decision rules (fixed now)

1. **Cost constants.** Recommend changing a sleeve's coded lump only if the measured M1 all-in round trip differs
   from the coded constant by > 3 bp AND the day-block CI90 of the measured cost excludes the coded value.
   Otherwise NO CHANGE.
2. **Entry policy.** Recommend maker entries only if the cancel→skip fill-weighted expectancy at some
   T ≤ 15 min is ≥ the market-entry expectancy + 0.02 R in BOTH halves AND the fill rate at that T is ≥ 70 %
   (touch rule; the through rule must not flip the sign of the improvement). Otherwise stay taker.
3. **TP policy.** Resting-limit TP only if the ≥ 1 tick trade-through share is ≥ 90 % on 1 m; otherwise
   market-at-touch with the measured cost.
4. **Viability.** M1 cost > 50 % of gross mean R → NOT VIABLE AS TAKER; if M2 brings it under 33 % →
   MAKER-POLICY CANDIDATE; else RETIRE PENDING USER DECISION. Sleeves with M1 cost ≤ 50 % → VIABLE.
5. Every rule is evaluated on the 7-year 1 m panel; the 5 s year is the resolution check and can only demote
   a verdict (if the 5 s and 1 m fill rates differ by > 15 pp the 1 m verdict is downgraded to INCONCLUSIVE).

## Trial ledger

E2: 4 patience × 2 fallbacks × 2 fill rules = 16 cells per sleeve; E3: 3 fill rules; E6: 4 cost models. All
declared here; the rules above pick by pre-stated criteria, not by best cell.

## Controls and limits stated in advance

5 s SPOT paths stand in for PERP fills (E0b gives the basis error); touch optimism is bracketed by the through
rule; skipped signals earn 0 (no survivorship); the paper fill is a quote up to 60 s stale (E1 measures it);
events cluster in time (day-block bootstrap everywhere); the 5 s year is one regime; queue position, live exit
urgency and true tape markouts cannot be measured from disk — that is E7's job. Impact at $10–30 k notional
is assumed 0.

## Run order

`run_e0_facts.py` → `run_e1_market_cost.py` → `run_e2_passive_entry.py` → `run_e3_tp_fills.py` →
`run_e4_stop_slippage.py` → `run_e5_conditional_as.py` → `run_e6_recost.py`, then
`C:/Python/Python313/python.exe build_notebook.py` → `execution_study.ipynb`. Each script writes `results/`
and is deterministic (seed 42).

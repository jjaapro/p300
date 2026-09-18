# ETH/BTC regime spread and hedged expressions 2026-09 — pre-registration (frozen before any result)

Written 2026-09-19 before `run_spread.py` produced a number; frozen by `run_spread.py freeze0` (sha256 of this file,
the library, the run script and the fixtures in `results/freeze_F0.json`). Roadmap §3 research item 2: *"ETH/BTC
regime spread (strong-bull days only) and hedged expressions of existing signals."* Tag: a NEW-STRATEGY test with one
fixed rule (Part A) and an AUDIT of a hedge on shipped signals (Part B). Nothing under `bots/` or `strategies/`
changes; a BUILD is a proposal for a paper sleeve, which is a bot change that needs the operator's go-ahead.

## What is already known (disclosure, before any number here)

- `studies/notebooks/pool_study/eth_vs_btc_in_regime.py` (2026-06-07, 3-year window): on strong_bull days ETH beat
  BTC (long-ETH/short-BTC +98.5 % gross, t 2.42); mild_bull days were slightly negative; the regime is a poor BTC
  timing signal. The 2026-09-12 full-history re-run (2020 → 2026-09, scratch script, uncosted): strong_bull n 242,
  spread mean +39.6 bp/day, **t 1.76**, per year 2020 **−24.6 %** (n 88), 2021 +35.6 %, 2024 +9.0 %, 2025
  **+102.6 %** (t 3.44); pre-2023-06 strong_bull days alone +2.2 %, t 0.27. ETH-on-BTC beta 1.10 unconditional,
  0.78 on strong_bull days, 1.18 on bear days.
- The attribution layer (2026-08-23) reads chento's R as timing alpha, not regime beta; B13 (same-asset hedge mode)
  was decisively negative; ADX on ETH was killed for adding no MAR at correlation 0.47.
- The bar the validation audit set for anything new: DSR ≥ 0.95 and both halves, deflated for the trials run.

Those numbers say the spread's edge is lumpy and era-concentrated. This study fixes the rule, charges costs and
funding, and asks the pre-registered question once. No number below was chosen after seeing a result.

## Part A — the strong_bull spread as a sleeve

**Data.** Daily UTC bars from prod.db's `btc_1m` and `eth_1m` (Binance spot minute bars): a day's open is the open
of its first present minute, its close the close of its last present minute; window 2020-01-01 → the last complete
UTC day at the run. The regime is the production classifier `strategies.support.regime_jplus.classify_series` on the
BTC daily closes with `ca_long_short_ratio` (asset BTC, `long_pct` by UTC day) as its LS input — the exact code the
pool study and the 2026-09-12 re-run used; it classifies day D from data through D−1, so the mode of day D is known
at D 00:00 UTC. Before 2021-01-01 the LS series is absent and its circuit breaker cannot fire (as in the re-run).
Funding: Binance USDT-M settlement prints for BTCUSDT and ETHUSDT from the `brainstorm_validation_2026_09` cache
(`cache/binance_funding_<SYMBOL>.json`, written 2026-09-19 01:04 by the CARRY-on-ETH study; read as files, never
re-fetched; hashed into the freeze).

**Rule (fixed).** An *episode* is a maximal run of consecutive UTC days classified `strong_bull`. At the open of
its first day: long ETHUSDT perpetual and short BTCUSDT perpetual, **equal notional, one unit of capital per leg**
(gross 2×). Held without rebalancing; closed at the open of the first day that is not `strong_bull`. An episode
still open at the end of the sample is closed at the last day's close and counted as censored.

**P&L per episode**, in % of capital, additive (no compounding):
`gross = (E_exit / E_entry − 1) − (B_exit / B_entry − 1)`; `funding = Σ_settlements (f_BTC − f_ETH)` over the
settlements at or after entry and before exit (the long ETH leg pays the ETH rate, the short BTC leg receives the
BTC rate; notional held at one unit, mark-to-market drift of the notional ignored — stated, not modelled);
`cost = 0.20 %` per episode (10 bp per leg per round trip, the execution study's measured taker round trip on
chento BTC and ETH); `net = gross + funding − cost`. The daily equity path marks open episodes to each day's close,
charges the cost on the entry day and funding on its settlement days.

**Statistics.** Episodes, days held, share of time held; gross, funding, cost and net in total and per year (sample
years = calendar days / 365.25); per calendar year; the maximum drawdown of the additive daily equity (% of
capital); **MAR** = net %/yr ÷ max drawdown; daily Sharpe on held days (annualised √365); the t-statistic of
episode net returns; **DSR** (`studies.lib.validation.dsr_pbo.dsr_from_returns`, n_trials = 1, on the daily net
returns of held days); a 5,000-draw bootstrap over episodes (seed 42) for the CI90 of net %/yr; **halves** by
episode order (earlier half, the extra episode when odd); the calendar split at 2023-06-09 (the pool study's window
start) reported.

**Placebo (decides).** The same exposure profile with the timing removed: for each calendar year, the year's episode
lengths are kept and placed at random non-overlapping day positions inside that year (1,000 draws, seed 42), and the
same P&L is computed. `p_placebo` = share of draws whose net total ≥ the actual. Random days of the same year carry
the year's ETH/BTC drift and the same number of round trips, so what is tested is the classifier's timing, not the
year.

**Reported arms (no decision):** the spread on `mild_bull` days; the spread on all bull days (`strong_bull` +
`mild_bull`); long ETH only and long BTC only on `strong_bull` days (funding on the one leg, 10 bp round trip) — the
ETH_REGIME sleeve's original expression and the regime as a BTC timing signal.

**Decision rule (fixed).** BUILD (propose a paper sleeve) only if **all** of:

- **(a)** net ≥ **+5.0 %/yr** over the window with the bootstrap CI90 of net %/yr excluding 0 — the CARRY-on-ETH
  study's bar for a second sleeve, kept for comparability;
- **(b)** net > 0 in **both halves** by episode order;
- **(c)** **DSR ≥ 0.95** on the daily held-day net returns (one trial);
- **(d)** **p_placebo < 0.05**;
- **(e)** the spread's MAR ≥ long-ETH-only's MAR (the short BTC leg must earn its cost and funding).

Otherwise **KILL**, with the failing conditions named. (a)–(d) failing on the era split alone is still a KILL: an
edge that lives in two of six years is not a sleeve.

## Part B — the same hedge on chento's own trades

**Question.** Does hedging each chento trade with an equal-notional opposite position in the other asset (a chento
ETH long hedged with a BTC short, a chento BTC short hedged with an ETH long) raise MAR — that is, is chento's R
timing alpha that survives the removal of market beta, and is the hedge worth its cost?

**Population and path.** The 392 gate-off chento entries stage 1 of the exit-policy study walked
(`exit_policy_2026_09/results/microstructure/trades.csv.gz`: 208 BTC, 184 ETH; `i0`, `x`, `entry`, `risk`, `s`,
`exit_price` from the shipped walk: stop 1 R, target 6 R, 72 h) and the ORB perpetual 1-minute panels
(`orb_study/cache/<SYMBOL>_perp_1m.npz`) for the other asset's price at the same minutes.

**Hedge (fixed).** At the trade's entry minute, the other asset `H` is sold (for a long) or bought (for a short)
at its close of minute `i0 − 1` (the same convention as the trade's own entry, stage 1's P3), with notional equal
to the trade's; closed at the trade's exit minute `x` at `H`'s close of minute `x`. In the trade's R:
`hedge_R = −s · (H_x / H_entry − 1) · entry / risk`, `hedge_cost = 0.0010 · entry / risk` (10 bp round trip on the
hedge leg), `R_hedged = R_trade + hedge_R − hedge_cost`. Price only, as stage 1's R; funding on neither leg (they
partly cancel and the unhedged R carries none either).

**Statistics** per asset: mean R, per-trade Sharpe, annual R (sum ÷ years), max drawdown of cumulative R, **MAR** =
annual R ÷ max drawdown, unhedged versus hedged; the paired difference `R_hedged − R_trade` with a 30-day-block
bootstrap CI95 (`micro_lib.block_indices`, 10,000 draws, seed 42) and halves by entry order; the OLS slope of
`R_trade` on `−hedge_R` (how much of chento's R is the market move) and the correlation of the two.

**Decision rule (fixed).** The hedged form is worth a paper-variant proposal only if, **on both assets**, MAR
(hedged) > MAR (unhedged) in the whole sample **and in both halves**, and the paired mean difference is ≥ **−0.05 R**
(the hedge's cost budget). Otherwise **KEEP UNHEDGED**.

## Trial ledger

Part A: one candidate rule (the spread on `strong_bull`), four reported arms. Part B: one hedge form, two assets.
Nothing is swept. n_trials = 1 in every DSR.

## Run order

`python run_spread.py freeze0` (refuses to overwrite), `python run_spread.py outcomes` (refuses if a frozen file
changed or a result exists), then `C:/Python/Python313/python.exe build_notebook.py` → `eth_btc_spread.ipynb`.
Findings in `findings.md`, written after the run. Fixtures: `tests/test_spread.py`.

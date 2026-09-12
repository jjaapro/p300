# Sizing style 2026-09 — pre-registration (frozen before any result)

Written 2026-09-12 before any `run_s*.py` produced a number. Track S of the approved brainstorm follow-up
plan. Tag: AUDIT + exit/sizing-policy decision. Nothing under `strategies/`, `bots/`, `data/` is modified;
prod.db is opened read-only through `studies/notebooks/execution_2026_09/exec_lib.py` (same events, same
1 m spot paths, same venue-level convention as the execution study).

## Question

The brainstorm's leverage experiment (SIM_TRADING_STYLE §1–4) does not show that leverage creates profit — a
$5,000 position earns the same at 1× or 50×, and its own summary says "leverage does almost nothing at this
size". What it does show is a *style*: no per-trade stop, time exits, fixed notional, risk carried at the
account level in cross margin, with concurrency as the ruin mechanism. p300 runs per-trade stops on every
sleeve except R4 and CARRY, fixed-R sizing on a fixed $10,000 per bot, and a 3× notional cap. Does the
brainstorm style beat the shipped stops for any running sleeve, and what does account-level risk look like
at the fleet's maximum concurrency?

## Events, paths, costs

Events: the execution study's sets (CHENTO_BTC 101, CHENTO_ETH 77, SHORT_SQUEEZE 71, SQUEEZE_BULL 122; ADX
via `adx_study/harness.run` on the shipped-config parameters, 2018 →). Paths: `btc_1m` / `eth_1m`. Costs:
the execution study's measured taker round trip per sleeve (E6 M1: 9.6 / 10.0 / 9.3 / 6.7 bp; ADX 15 bp as
coded, funding excluded). Sizing per bot as shipped: fixed-R `RISK_PCT` (chento 2 %, SHORT_SQUEEZE 1 %,
SQUEEZE_BULL 1 %, ADX 2 %) over the shipped initial stop, `NOTIONAL_MAX_X` 3×, capital $10,000.

## Experiments

- **S1 exit policy on identical entries.** Same signals, same notional (sized over the *shipped* stop, so R
  stays comparable and a no-stop loss can exceed −1 R), four exit policies: P0 shipped (stop + target + TIF);
  P1 time-only (no stop, no target — the brainstorm style); P1b target-only (no stop); P2 catastrophe stop at
  3× the shipped stop distance (+ target + TIF). Per sleeve and policy: n, net mean R with day-block CI90,
  win rate, worst trade, sum R, mark-to-market daily maxDD in % of capital, MAR (sum of % per year / maxDD),
  both halves; paired delta vs P0 with day-block CI90. ADX: shipped Tier-2 (10 % SL + ATR×4 trail + symmetric
  filter) vs signal-exit-only (no SL, no trail) vs catastrophe 30 % SL, on the daily harness with
  `bv_lib.ledger_to_daily_returns` for the MTM curve.
- **S2 sizing rule on the P0 trades.** (a) fixed-R on fixed capital (today), (b) fixed-R on current equity
  (compounding), (c) fixed notional (the median of (a), the brainstorm's rule) — final equity, CAGR, maxDD,
  MAR, worst trade in % of equity, per sleeve. Ruin table: the adverse move that liquidates a book at gross
  exposure g under 0.5 % maintenance, m* = (1 − 0.005 g) / g, for g ∈ {0.5, 1, 2, 3, 5, 10}, against the worst
  observed BTC/ETH adverse moves over 1 h, 6 h, 24 h and 72 h windows since 2020.
- **S3 liquidation walk.** Minute-by-minute account walk with the sleeves' historical positions at shipped
  sizing: equity = capital + realised P&L + unrealised P&L at the bar's adverse extreme, maintenance = 0.5 % of
  open notional (`strategies/support/margin_sim.py` parameters: `mm_pct` 0.005, liquidation fee 0.5 %; the
  module itself has no exit times, so its rule is re-implemented read-only), per bot ($10,000, own trades) and
  pooled (one $50,000 account carrying all four directional sleeves at the same notionals; CARRY excluded and
  noted). Under P0 and under P1 (no stop). Outputs: liquidation episodes, minimum distance to liquidation
  (the adverse move that would have liquidated at the worst minute), maximum gross exposure.

## Decision rules (fixed now)

1. A no-stop or catastrophe-only policy (P1, P1b, P2) is a **BUILD-candidate** for a sleeve only if, versus
   P0: net mean R is higher in BOTH halves, MAR is higher, MTM maxDD (% capital) is not worse by more than
   5 pp, AND S3 shows zero liquidation episodes for that sleeve's bot under the policy with a 2× safety factor
   (minimum distance to liquidation ≥ 2× the worst observed 24 h move at that exposure). Otherwise KEEP THE
   STOP. ADX is judged on the same clauses with the harness MTM curve.
2. Sizing: no paper change (paper variants are fixed-capital by design); the S2 table is a live-design
   recommendation only, and any comparison with buy-and-hold uses an exposure-matched benchmark.
3. Trial ledger: 4 exit policies × 4 sleeves (+ 3 ADX configs) and 3 sizing rules; declared here, rules above
   pick by pre-stated criteria.

## Controls and limits stated in advance

Overlapping trades of one sleeve are each sized from capital as shipped (the bots hold one open position per
variant; the research pools allow overlap — S3 counts the overlap explicitly). MTM uses 1 m closes at UTC day
ends and adverse extremes only inside S3. The measured taker cost is applied uniformly to all policies (stop
slippage is 0 at minute resolution per E4). Spot paths for perp levels: same caveat as the execution study.

## Run order

`run_s1_exit_policies.py` → `run_s2_sizing_rules.py` → `run_s3_liquidation.py`, then
`C:/Python/Python313/python.exe build_notebook.py` → `sizing_style.ipynb`. Deterministic (seed 42).

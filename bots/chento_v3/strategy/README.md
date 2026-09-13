# CHENTO_TRIPLE_V3

Mean-reversion-into-extreme strategy on perp 15m, with four filter gates and an
asymmetric regime filter. Third-generation strategy from the chento
reverse-engineering work — superseded both v1 (swing-base limit-bid, never
shipped live) and v2 (same architecture, research-only).

This is the strategy package of the `chento_v3` bot. It serves **two** bot
processes: [bots/chento_v3](../) (BTC) and `bots/chento_v3_eth` (ETH), which is
a thin wrapper that sets `CHENTO_V3_ASSET=ETH` before importing this package
and then reuses [../runner.py](../runner.py). One process = one asset, resolved
at import time.

*Moved here from `strategies/sleeves/chento_v3/` on 2026-09-13 ("bot =
directory = strategy"). No signal change; the orchestrator that used to
dispatch it is gone.*

> **What is calibrated right now** lives in
> [docs/calibration/chento_triple_v3.md](../../../docs/calibration/chento_triple_v3.md),
> not in this file. That is the single source of truth for parameter values and
> the change history; this README explains the shape of the thing.

## Strategy summary

| Layer | What |
|---|---|
| **Trigger** | Triple composite: B1 money-flow CVD divergence ∩ B5 LSR extremes ∩ B7 multi-TF CVD alignment, B1-anchored (`b1_now & b5_w & b7_w`, 24h window) |
| **Math** | atr5_t6R (5×ATR stop, 6R fixed target), TIF=72h, 10bp RT cost scaled by stop distance (measured 2026-09-12; research replays used 18bp) |
| **Filter 1** | no_tilt — skip if recent loss in this sleeve. **BTC only** (`FILTER_NO_TILT = ASSET == "BTC"`); ETH instead halves risk after a loss at the bot layer |
| **Filter 2** | no_resist_OB_within_2R — skip if fresh opposite-direction Order Block within 2R of entry (5-bar pivot OB) |
| **Filter 3** | okx_aligned — OKX-Binance perp delta z-score (rolling 7d window) must sign-match trade direction |
| **Filter 4** | skip_up_30d_shorts (asymmetric) — skip ONLY shorts when 30d return > +10% |
| **Execution** | Single entry, no adds. The A4 ladder (add at −0.3R adverse, adaptive H_B sizing, combined stop −1.5R) is coded but **`LADDER_ENABLED = False` since 2026-06-05** — H_B failed the backward-only Pareto test |

## Performance numbers (BTC, 5.4y backtest, funding cost included)

**Research figures, booked at the June 18bp cost convention** — kept as the
record of what was validated, not as a live expectation. The shipped config
costs 10bp and runs with the ladder OFF; the honest post-cost expectancy on
the same fires is +0.739R BTC / +0.605R ETH (calibration log, 2026-09-12 row).

| | Naive baseline | This strategy (v3) |
|---|---|---|
| Trades | 681 (127/yr) | 106 (20.2/yr) |
| Mean R | +0.65 | **+4.13** |
| WR | 57% | **82%** |
| Max DD | −9.45R | **−4.52R** |
| IS / OOS | +0.63 / +0.69 | +3.86 / **+3.81** |
| MAR ratio | 8.7 | **18.4** |

The IS/OOS gap of 0.05R is extraordinarily stable — OOS slightly underperforms
IS but well within sampling noise. Live results should be discounted ~15-30%
for slippage/execution effects; the 2026-09 validation audit is the sober read.

## Why this differs from v1/v2

v1 / v2 = swing-base limit-bid (long-only, swing-low approach with MTF bias
+ confluence score). That approach was inspired by chento's stated workflow
but **did not survive validation**: most stated rules (Rule 1 whale flow,
liquidation-cluster TPs, RSI exhaustion exits, tight TIF, high leverage) were
empirically anti-edge. v2 produced +0.30R per trade — matching chento's own
recovered edge but not enough margin to ship. `chento_limit_bid` was archived
on 2026-09-13 (`studies/material/archive/chento_limit_bid/`).

v3 instead uses the **Triple composite** of three signals that actually
intersect with positive expectancy after exhaustive Group A/B/C testing.
See [studies/material/chento/validation/findings_decisions.md](../../../studies/material/chento/validation/findings_decisions.md)
for the per-rule audit.

## Data dependencies

Runtime queries against `prod.db`, resolved per asset at import time:

| Table (BTC / ETH) | Fields | For |
|---|---|---|
| `cd_futures_15m` / `cd_futures_eth_15m` | OHLCV + taker buy/sell quote volumes | OHLC, B1 (CVD), B7 (multi-TF CVD), ATR, 30d return |
| `ca_long_short_ratio` (asset='BTC' / 'ETH') | long_pct + ratio | B5 |
| `okx_perp_1h` / `okx_perp_eth_1h` | close | OKX delta gate (filter 3) |

All six tables have multi-year history. No external API calls at tick time.
The runner refuses entries loudly when an entry table is stale.

## Tick model

Same pattern as `short_squeeze`:

- `_sweep_open_positions()` — runs every minute. Manages stop / target / TIF
  for any open trades via `math.evaluate_position_step()`.
- `_evaluate_trigger()` — runs at 15m boundary only, on the JUST-CLOSED bar
  (the forming bar is never evaluated — the 2026-07-22 P0 fix). Checks Triple
  intersection + the filter gates. If all pass, opens a new trade.

The daily feature cache (`_rebuild_daily_cache()`) is built once per UTC day
and stores all rolling-window features (CVD z, MTF z, LSR percentiles, ATR,
OKX delta z, 30d return, SMC OBs). Per-tick cost is just a dataframe
lookup at the current bar index.

## Cooldown

Minimum `COOLDOWN_HOURS` (6, raised from 4 with the B1-anchored trigger)
between triggers. The Triple composite naturally fires sparsely (~20 times per
year per asset) so the cooldown is rarely active; it's there to prevent
burst-fires when multiple TF alignments resolve simultaneously.

## Files

- `signal.py` — data loaders, tick model, and the bot entry points
  `decide()` / `execute()`
- `math.py` — pure stateless detectors and the position state machine
- `config.py` — all tunable parameters; do NOT tune without re-validation
- `__init__.py` — re-exports `decide` / `execute` plus the three submodules

## Validation provenance

Every parameter in `config.py` traces back to a specific validation script.
The full audit is in [studies/material/chento/validation/findings_decisions.md](../../../studies/material/chento/validation/findings_decisions.md)
and the per-finding memories in `C:/Users/TJ5/.claude/projects/c--Source-Repos-p300/memory/`:

- `project_chento_triple_optimized_config.md` — the consolidated stack
- `project_chento_a4_ladder_finding.md` — A4 ladder tiers (as researched)
- `project_chento_v3_p1_ladder_verdict.md` — why the ladder ships OFF
- `project_cross_exchange_okx_gate.md` — OKX delta gate (filter 3)
- `project_chento_adaptive_hybrid.md` — H_B inside-VA classifier
- `project_chento_regime_filter.md` — asymmetric skip_up_30d_shorts (filter 4)
- `project_chento_multi_asset_validation.md` — ETH cross-validation
- `project_tif_72h_optimal.md` — TIF=72h finding
- `project_wider_tp_same_stop_is_better.md` — TP=6R finding
- `project_chento_rule1_empirically_dead.md` — what we tested and dropped
- `project_b13_hedge_negative.md` — why hedge mode isn't here
- `project_confidence_scaling_negative.md` — why per-trade leverage isn't here

## Status

**Live in paper.** BTC shipped as a standalone bot on **2026-07-21** (bot
extraction plan, M1); the ETH leg was added on **2026-08-23** (multi-asset plan,
Phase B). Not the five-phase route this section used to describe. Phases 2–5
of that plan (orchestrator wiring, backtest validation, PORTFOLIO update,
paper enable) are moot: *retired 2026-09-13*, the orchestrator and its
`STRATEGY_DISPATCH` registry no longer exist, and a strategy reaches paper by
having a `bots/<name>/` of its own.

Current state, monitoring and the change history:
[docs/calibration/chento_triple_v3.md](../../../docs/calibration/chento_triple_v3.md).

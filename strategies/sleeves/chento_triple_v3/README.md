# CHENTO_TRIPLE_V3

Mean-reversion-into-extreme sleeve on BTC perp 15m, with adaptive A4 ladder
sizing, four filter gates, and an asymmetric regime filter. Third-generation
strategy from the chento reverse-engineering work — superseded both v1
(swing-base limit-bid, never shipped live) and v2 (same architecture,
research-only).

## Strategy summary

| Layer | What |
|---|---|
| **Trigger** | Triple composite: B1 money-flow CVD divergence ∩ B5 LSR extremes ∩ B7 multi-TF CVD alignment |
| **Math** | atr5_t6R (5×ATR stop, 6R fixed target), TIF=72h, 18bp RT cost scaled by stop distance |
| **Filter 1** | no_tilt — skip if recent loss in this sleeve |
| **Filter 2** | no_resist_OB_within_2R — skip if fresh opposite-direction Order Block within 2R of entry (5-bar pivot OB) |
| **Filter 3** | okx_aligned — OKX-Binance perp delta z-score (rolling 7d window) must sign-match trade direction |
| **Filter 4** | skip_up_30d_shorts (asymmetric) — skip ONLY shorts when BTC 30d return > +10% |
| **Execution** | A4 ladder add at −0.3R adverse with adaptive H_B sizing (T3=150% inside 7d VA, T1=50% outside), combined stop −1.5R from original entry |

## Performance numbers (BTC, 5.4y backtest, funding cost included)

| | Naive baseline | This sleeve (v3) |
|---|---|---|
| Trades | 681 (127/yr) | 106 (20.2/yr) |
| Mean R | +0.65 | **+4.13** |
| WR | 57% | **82%** |
| Max DD | −9.45R | **−4.52R** |
| IS / OOS | +0.63 / +0.69 | +3.86 / **+3.81** |
| MAR ratio | 8.7 | **18.4** |

The IS/OOS gap of 0.05R is extraordinarily stable — OOS slightly underperforms
IS but well within sampling noise. The strategy is research-complete; live
results should be discounted ~15-30% for slippage/execution effects.

## Why this differs from v1/v2

v1 / v2 = swing-base limit-bid (long-only, swing-low approach with MTF bias
+ confluence score). That approach was inspired by chento's stated workflow
but **did not survive validation**: most stated rules (Rule 1 whale flow,
liquidation-cluster TPs, RSI exhaustion exits, tight TIF, high leverage) were
empirically anti-edge. v2 produced +0.30R per trade — matching chento's own
recovered edge but not enough margin to ship.

v3 instead uses the **Triple composite** of three signals that actually
intersect with positive expectancy after exhaustive Group A/B/C testing.
See [studies/material/chento/validation/findings_decisions.md](../../../studies/material/chento/validation/findings_decisions.md)
for the per-rule audit.

## Data dependencies

Runtime queries against `prod.db`:

| Table | Fields | For |
|---|---|---|
| `cd_futures_15m` | OHLCV + taker buy/sell quote volumes | OHLC, B1 (CVD), B7 (multi-TF CVD), ATR, 30d return |
| `ca_long_short_ratio` (asset='BTC') | long_pct + ratio | B5 |
| `okx_perp_1h` | close | OKX delta gate (filter 3) |

All three tables have multi-year history. No external API calls at tick time.

## Tick model

Same pattern as `chento_limit_bid` and `short_squeeze`:

- `_sweep_open_positions()` — runs every minute. Manages stop / target / A4
  ladder / TIF for any open trades via `math.evaluate_position_step()`.
- `_evaluate_trigger()` — runs at 15m boundary only. Checks Triple
  intersection + 4 filter gates. If all pass, opens a new trade with
  adaptive ladder sizing.

The daily feature cache (`_rebuild_daily_cache()`) is built once per UTC day
and stores all rolling-window features (CVD z, MTF z, LSR percentiles, ATR,
OKX delta z, 30d return, SMC OBs). Per-tick cost is just a dataframe
lookup at the current bar index.

## Cooldown

Minimum 4h between triggers. The Triple composite naturally fires sparsely
(~20 times per year on BTC) so the cooldown is rarely active; it's there to
prevent burst-fires when multiple TF alignments resolve simultaneously.

## Files

- `signal.py` — orchestrator entry points, data loaders, tick model
- `math.py` — pure stateless detectors and the position state machine
- `config.py` — all tunable parameters; do NOT tune without re-validation
- `__init__.py` — re-exports `try_decide_for_variant` / `execute_for_variant` /
  `try_fire_for_variant` for orchestrator dispatch

## Validation provenance

Every parameter in `config.py` traces back to a specific validation script.
The full audit is in [studies/material/chento/validation/findings_decisions.md](../../../studies/material/chento/validation/findings_decisions.md)
and the per-finding memories in `C:/Users/TJ5/.claude/projects/c--Source-Repos-p300/memory/`:

- `project_chento_triple_optimized_config.md` — the consolidated stack
- `project_chento_a4_ladder_finding.md` — A4 ladder tiers
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

**Phase 1: code written.** Phase 2 (orchestrator wiring), Phase 3 (backtest
validation), Phase 4 (PORTFOLIO.md update), Phase 5 (paper-trade enable)
pending.

This sleeve is **not yet registered** in `strategies/orchestrator.py`
STRATEGY_DISPATCH and not in the `p300_aggressive_v2` variant composition.
See the parent project's PORTFOLIO.md after Phase 2/4 lands.

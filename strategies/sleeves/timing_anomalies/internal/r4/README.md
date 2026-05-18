# S-099 R4 — Calendar-driven intraday LONG, 4 variants

Four calendar-trigger sleeves that share signal math ([math.py](math.py))
but fire on different days, assets, and windows. Each variant has its own
allocation, leverage, and regime weight — the orchestrator dispatches
them as separate entries in the strategy registry.

## Variants

| Dispatch key | Asset | Day | Window | Hold |
|---|---|---|---|---|
| `JPLUS_R4_BTC` | BTC | Mon wk1-2 | 06:00 → 18:00 UTC | 12h |
| `JPLUS_R4_ETH` | ETH | Tue → Wed wk1-2 | 20:00 → 20:00 UTC | 24h |
| `JPLUS_R4_BTC_V2` | BTC | Wed + Fri wk1-2 | 04:00 → 14:00 UTC | 10h |
| `JPLUS_R4_ETH_V2` | ETH | Wed + Fri wk1-2 | 04:00 → 14:00 UTC | 10h |

"wk1-2" means the date is ≤ 14 of the month. All variants are LONG-only.

## Sizing

`notional = capital × regime_weight[variant] × inner_lev × vol_target_lev`

- **regime_weight**: from `strategies.support.jplus_inputs.today_inputs()` per regime — see
  [PORTFOLIO.md §4.2](../../../../../PORTFOLIO.md) for the capped weight matrix.
- **inner_lev**: `R4_INNER_LEV_UNGATED` (2.5×) when the vol-percentile gate
  has NOT fired, `R4_INNER_LEV_GATED` (1.0×) when it has. The gate fires
  on ~30% of days (top 25% of 365d realized vol); see `jplus/gate.py`.
- **vol_target_lev**: 30d realized vol → per-day leverage, regime-capped
  1.5×–3.0× and floored at 0.5×; see `jplus/voltarget.py`.

The variants ARE de-levered together when the gate fires — that's the
point of the inner-lev multiplier sharing across all four.

## Entry / exit

- **Entry**: at the variant's `ENTRY_HOUR` UTC on a calendar-qualifying day.
  Idempotent per (variant, asset, UTC day) via the trades table.
- **Exit**: at `EXIT_HOUR` UTC on the same day (V1 BTC, V2) or the next
  day (V1 ETH). Scheduled via `scheduled_exit_dt`; orchestrator's
  close-due loop handles it.
- Cold-start: on a day the bot is offline during the entry window, the
  trade is missed permanently (no retroactive emitter since 2026-05-10).

## Edge thesis

- **R4 BTC** (Mon): post-Binance-perp / post-ETF emergent flow effect.
  −0.76%/trade pre-Binance vs +0.83%/trade post-ETF.
- **R4 ETH** (Tue→Wed): the highest-alpha sub-sleeve in J+ history
  (+0.088%/day mean over 48 fires; +116.5% compounded standalone). Thin
  sample — 48 events in 2.6 years.
- **R4 V2** (Wed+Fri, 04→14): era-stable BTC alpha cell (positive in
  pre-Binance-perp, Binance-perp, post-ETF eras). Likely captures
  NFP-anticipation (Friday wk1) plus early-month Wed flow.

## Caveats

- **In-sample selection**: V1 R4_BTC config (Mon 06→18 since 2026-05-08)
  and V2 (Wed+Fri 04→14) were chosen from a 7,500-config grid search
  using "57 configs that were positive in every backtest year". Post-ETF
  era (2024-01 onward) is too short for OOS walk-forward to be conclusive.
- Per-sleeve expectancy is monitored by `services/strategy_health.py`;
  the live config is auto-disabled if expectancy decays. See memory
  `feedback_r4_post_etf_ride_with_monitor.md`.

## Files

- [signal.py](signal.py) — 4 `try_fire_for_variant` handlers + shared idempotency helper
- [config.py](config.py) — strategy keys, inner-lev multipliers, entry/exit hours
- `__init__.py` — package marker

- [math.py](math.py) — windowed-return computation (used by signal.py for
  scheduling and by `studies/jplus_analytic/simulate.py` for analytic
  backtests).

Inputs builder (`jplus/simulate.today_inputs`) and shared support modules
(`regime`, `voltarget`, `gate`) are still in `jplus/` — they move to
`strategies/support/` in restructure step 6b/6c.

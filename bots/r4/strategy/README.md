# S-099 R4 — Calendar-driven intraday LONG, 4 variants

Four calendar-trigger windows that share one decide helper (`_r4_decide` in
[signal.py](signal.py)) but fire on different days, assets, and hours. This is
the strategy package of the `r4` bot: it lives under `bots/r4/` and
[bots/r4/runner.py](../runner.py) is its only caller. Bot-level choices — which
variants are enabled, how a fire is sized, the late-entry grace — are in
[bots/r4/config.py](../config.py), not here.

*Moved here from `strategies/sleeves/r4/` on 2026-09-13 ("bot = directory =
strategy"). No signal change; the orchestrator that used to dispatch it is gone.*

## Variants

| Dispatch key | Asset | Day | Window | Hold | Enabled |
|---|---|---|---|---|---|
| `JPLUS_R4_BTC` | BTC | Mon wk1-2 | 06:00 → 18:00 UTC | 12h | no (since 2026-09-12) |
| `JPLUS_R4_ETH` | ETH | Tue → Wed wk1-2 | 20:00 → 20:00 UTC | 24h | **yes** |
| `JPLUS_R4_BTC_V2` | BTC | Wed + Fri wk1-2 | 04:00 → 14:00 UTC | 10h | no (since 2026-09-12) |
| `JPLUS_R4_ETH_V2` | ETH | Wed + Fri wk1-2 | 04:00 → 14:00 UTC | 10h | **yes** |

"wk1-2" means the date is ≤ 14 of the month. All variants are LONG-only.
The two BTC windows stay wired and tested but carry `ENABLED[...] = False` —
they are post-Binance-perp *emergent*, where the ETH pair is era-stable. The
reasoning and the re-enable bar are in
[docs/calibration/r4.md](../../../docs/calibration/r4.md).

## Sizing

The strategy proposes, the bot disposes. `_r4_decide` emits an Intent sized

`notional = capital × regime_weight[variant] × inner_lev × vol_target_lev`

and the runner then **replaces** the weight with its own flat
`VARIANT_WEIGHT` (0.20 per variant) and caps the leverage at `LEV_CAP` (7.5×).

- **regime_weight**: from `strategies.support.jplus_inputs.today_inputs()`
  per regime. The live path passes no `weight_pct`, so this fallback is what
  actually runs — and it is load-bearing even though the runner overrides the
  magnitude, because **bear regime scores 0 and a zero weight means no trade**.
  That is the regime kill switch. See
  [PORTFOLIO.md §4.2](../../../PORTFOLIO.md) for the capped weight matrix.
- **inner_lev**: `R4_INNER_LEV_UNGATED` (2.5×) when the vol-percentile gate
  has NOT fired, `R4_INNER_LEV_GATED` (1.0×) when it has. The gate fires
  on ~30% of days (top 25% of 365d realized vol); see
  `strategies/support/gate.py`.
- **vol_target_lev**: 30d realized vol → per-day leverage, regime-capped
  1.5×–3.0× and floored at 0.5×; see `strategies/support/voltarget.py`.

The variants ARE de-levered together when the gate fires — that's the
point of the inner-lev multiplier sharing across all four.

## Entry / exit

- **Entry**: at the variant's `ENTRY_HOUR` UTC on a calendar-qualifying day.
  Idempotent per (variant, window, asset, UTC day) via the trades table.
  The runner refuses a fire more than `LATE_ENTRY_MAX_S` (300 s) after the
  window opened and logs `missed_window`.
- **Exit**: at `EXIT_HOUR` UTC on the same day (V1 BTC, V2) or the next
  day (V1 ETH). Scheduled via `scheduled_exit_dt`; `botlib.close_due_trades`
  closes it on the first tick after the exit time. There is no stop-loss —
  every level was rejected by the pre-registered sweep
  (`studies/notebooks/r4_bot_prep/findings.md`).
- Cold-start: on a day the bot is offline during the entry window, the
  trade is missed permanently (no retroactive emitter since 2026-05-10).

## Edge thesis

- **R4 BTC** (Mon): post-Binance-perp / post-ETF emergent flow effect.
  −0.76%/trade pre-Binance vs +0.83%/trade post-ETF. *This is why it is
  disabled:* an effect absent before the perp era cannot be told apart from
  a regime artefact.
- **R4 ETH** (Tue→Wed): the era-stable window — positive in both eras and
  ranked first in both (+1.82%/fire post-ETF, n=55, t=3.1).
- **R4 V2** (Wed+Fri, 04→14): era-stable alpha cell (positive in
  pre-Binance-perp, Binance-perp, post-ETF eras). Likely captures
  NFP-anticipation (Friday wk1) plus early-month Wed flow. Only the ETH leg
  runs.

A 2026-09-12 exploratory check found these are a **market-wide calendar
drift**, not an asset effect: BTC and ETH move the same way on 82–85% of
fires, and no conditioner tried separates good fires from bad. The levers
that matter are the cell, the asset and the entry latency.

## Caveats

- **In-sample selection**: V1 R4_BTC config (Mon 06→18 since 2026-05-08)
  and V2 (Wed+Fri 04→14) were chosen from a 7,500-config grid search
  using "57 configs that were positive in every backtest year". Post-ETF
  era (2024-01 onward) is too short for OOS walk-forward to be conclusive.
- **No mechanism.** Nobody can yet say why this works or when it would
  decay, which is why only the era-stable ETH pair gets paper evidence.
- **Known gap**: `decide_eth` has no after-window check of its own; the
  runner's late-entry guard is what covers it. A strategy-side fix needs a
  go-ahead.
- Per-variant expectancy is monitored weekly by
  `python -m strategies.support.strategy_health --variant bot_r4_v1 --capital 10000`;
  disable a window (`ENABLED[...] = False` + a row in the calibration log)
  when its 90-day expectancy is < 0 with n ≥ 8 for three consecutive reviews.

## Files

- [signal.py](signal.py) — `decide_btc` / `decide_eth` / `decide_btc_v2` /
  `decide_eth_v2`, the shared `_r4_decide` helper, and the per-day
  idempotency check. The runner's `deciders()` map dispatches these four.
- [config.py](config.py) — strategy keys, inner-lev multipliers, entry/exit hours
- `__init__.py` — package marker

Two things deliberately live **outside** this package:

- `strategies/support/r4_windows.py` — the windowed-return arithmetic
  (`r4_btc_returns`, `r4_eth_returns`, …). It sits at the support layer
  because `jplus_inputs` imports it at module scope to build `today_inputs()`,
  and the strategy reads that result back; keeping it here would make
  `strategies/support/` import `bots/`.
- [bots/r4/windows.py](../windows.py) — pure window helpers for the *bot*:
  `window_open_for()` for the late-entry guard and `next_windows()` for the
  dashboard, both built from the same predicates this package uses
  (`tests/test_r4_bot.py` pins them together).

Inputs, regime, gate and vol-target all come from `strategies/support/`
(`jplus_inputs`, `regime_jplus`, `gate`, `voltarget`, `ema_position`).

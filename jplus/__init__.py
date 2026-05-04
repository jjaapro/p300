"""Core J+ portfolio port (regime-gated, no ML).

This package ports the J+ simulator logic from the upstream `trader` research
repo into p300's standalone codebase, with two substantive changes:

  1. The ML-based R4 gate is REPLACED by a rule-based gate using strictly
     T-1 data. The upstream ML pipeline was found to have within-day look-
     ahead (end-of-day 1m features predicting same-day R4 leverage). Our
     gate uses yesterday's BTC realized vol + drawdown only — no ML, no
     opaque model, no look-ahead possible by construction.

  2. The GOLD overlay is dropped. No `macro_daily` table in p300 and the
     crisis-hedge value-add is not the core thesis. The crypto portion is
     scaled up from (1 - w_gold) to 1.0.

Variant id when composed into P-300: `p100_jplus_regimegate_v1_0`  —
explicitly NOT `_mlgate_` to avoid misrepresenting what's running.

Modules:
  data.py         Data loaders (BTC/ETH hourly + daily, LS ratio), all
                  bounded by services.clock for look-ahead safety.
  regime.py       4-state regime classifier (strong_bull / mild_bull /
                  uncertain / bear) with LS circuit breaker + peak-DD
                  override. All inputs strictly T-1.
  r4.py           R4 BTC (Mon+Wed wk1-2, 06→18 UTC) and R4 ETH (Tue 20 →
                  Wed 20 UTC, wk1-2) hourly-window return computations.
  ema_sleeve.py   EMA(5/21) weekly crossover on BTC → long/short/flat
                  position map.
  voltarget.py    30d realized vol → per-day leverage cap, scaled so the
                  strategy targets 50% annualised vol, floored at 0.5x.
  gate.py         Rule-based R4 de-lever trigger from strictly T-1 data.
  simulate.py     Daily orchestrator: composes all sleeves + gate + vol-
                  target into a {date: daily_return_pct} map.
"""

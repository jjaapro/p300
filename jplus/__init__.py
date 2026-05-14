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
  simulate.py     Daily orchestrator: composes all sleeves + gate + vol-
                  target into a {date: daily_return_pct} map.

Migration log (restructure 2026-05-14):
  r4.py        -> strategies/sleeves/r4/math.py        (step 6a)
  ema_sleeve.py -> strategies/sleeves/ema/math.py      (step 6a)
  regime.py    -> strategies/support/regime_jplus.py   (step 6b)
  voltarget.py -> strategies/support/voltarget.py      (step 6b)
  gate.py      -> strategies/support/gate.py           (step 6b)
  simulate.py  -> split in step 6c: today_inputs() to
                  strategies/support/jplus_inputs.py; the analytic
                  simulate() function to studies/jplus_analytic/.
  data.py      -> studies/jplus_analytic/data.py       (step 6c)
"""

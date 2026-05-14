"""J+ analytic simulator — research-only re-use of the live decision engine.

simulate.py reuses ``strategies.support.jplus_inputs._run_decision_loop``
to produce the same per-day series the live bot consumes, then exposes
``simulate(start_date=..., end_date=...)`` for backtests and
``apply_r4_fees(series)`` for legacy fee-net analytic series.

No runtime path imports this — live and sim fees come from the trade
ledger. This package exists only for offline research / parameter
sweeps / comparisons against legacy backtest numbers.
"""

"""J+ analytic daily-return simulator (research only).

Re-uses ``strategies.support.jplus_inputs._run_decision_loop`` (which
the live bot already calls from ``today_inputs()``) to produce a
per-day return series across the available history. Identical decision
engine; this module only adds the clock cutoff + date filters + the
optional fee-net helper.

Look-ahead safety inherits from the decision loop: all inputs for date
T are derived strictly from data available at yesterday's UTC close.
"""
from __future__ import annotations

from strategies.support import clock
from strategies.support.jplus_inputs import (
    REGIME_WEIGHTS_FULL,
    _cap_core_weights,
    _run_decision_loop,
)


def simulate(start_date: str | None = None, end_date: str | None = None) -> dict[str, dict]:
    """Run the J+ regime-gate simulator over the available history.

    Args:
      start_date: ISO string; only dates ≥ this are emitted. Default: no floor.
      end_date:   ISO string; only dates ≤ this are emitted. Default: no ceiling
                  (uses data up to the current clock).

    Returns:
      {date_iso: {
          "return_pct": float,   net daily return in percent
          "mode": str,           regime classification
          "lev": float,          vol-target leverage applied today
          "r1x_pct": float,      pre-leverage 1x return in percent
          "gated": bool,         whether the rule-based gate fired today
          "ema_p": int,          EMA sleeve position (+1/-1/0)
      }}
    """
    out, _state = _run_decision_loop()

    # Cap at strictly BEFORE the clock's UTC date. A daily close on the clock
    # date itself would mix fully-formed bars from before the clock with the
    # single hourly bar that sits AT the clock, giving a non-reproducible
    # "partial daily close." Excluding the clock date keeps the return series
    # look-ahead-safe AND deterministic across clock positions.
    clock_date = clock.now_utc().date().isoformat()
    out = {k: v for k, v in out.items() if k < clock_date}

    if start_date or end_date:
        keys = sorted(out.keys())
        if start_date:
            keys = [k for k in keys if k >= start_date]
        if end_date:
            keys = [k for k in keys if k <= end_date]
        out = {k: out[k] for k in keys}
    return out


def apply_r4_fees(series: dict[str, dict], fee_bp_rt: float = 10.0) -> None:
    """Subtract R4 round-trip fees from each day's ``return_pct``, in place.

    The simulator emits GROSS R4 window returns; trade-event ledger
    paths charge the round-trip fee at trade close. Offline-research
    consumers of ``return_pct`` that want fee-net analytic series
    (e.g. parameter sweeps that compare against legacy backtest
    numbers) re-apply the same fee model via this helper.

    The fee in % of capital terms for one R4 fire =
        capped_regime_weight × r4_inner_lev × vol_target_lev × (fee_bp/10000) × 100

    where ``r4_inner_lev`` is 1.0 if the gate fired today, else 2.5.
    Weights use ``_cap_core_weights(REGIME_WEIGHTS_FULL[mode])`` — the
    same capped values the simulator loop and ``today_inputs()`` use,
    NOT raw REGIME_WEIGHTS_FULL. Pre-2026-05-13 a separate inline
    duplicate of the raw weight table was used here, charging fees on
    the raw 40%-of-capital R4_ETH position while the simulator's gross
    P&L was sized at the capped 14.8% — the helper subtracted 2-3× too
    much in fees. See AUDIT_2026_05_13.

    No runtime path calls this. Live (and sim) fees come from the
    trade-event ledger, which is the canonical P&L source.
    """
    fee_frac = fee_bp_rt / 10000.0
    _capped: dict[str, dict[str, float]] = {
        mode: _cap_core_weights(REGIME_WEIGHTS_FULL[mode])
        for mode in REGIME_WEIGHTS_FULL
    }
    for rec in series.values():
        weights = _capped.get(rec.get("mode", ""), {})
        gated = bool(rec.get("gated", False))
        r4_lev = 1.0 if gated else 2.5
        lev = float(rec.get("lev", 1.0))
        fee_pct = 0.0
        if rec.get("r4_btc_fired"):
            fee_pct += weights.get("r4_btc", 0.0) * r4_lev * lev * fee_frac * 100.0
        if rec.get("r4_eth_fired"):
            fee_pct += weights.get("r4_eth", 0.0) * r4_lev * lev * fee_frac * 100.0
        if rec.get("r4_btc_v2_fired"):
            fee_pct += weights.get("r4_btc_v2", 0.0) * r4_lev * lev * fee_frac * 100.0
        if rec.get("r4_eth_v2_fired"):
            fee_pct += weights.get("r4_eth_v2", 0.0) * r4_lev * lev * fee_frac * 100.0
        rec["return_pct"] = float(rec["return_pct"]) - fee_pct
        rec["r4_fees_pct"] = fee_pct

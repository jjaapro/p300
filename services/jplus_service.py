"""Core J+ regime-gated — live dispatcher.

Each tick computes the Core's daily return and emits one row of
``variant_daily_returns`` per UTC day per variant.

As of the trade-emitter migration (plan: how-do-we-modular-curry.md),
the Core also writes discrete trade rows to the same ``trades`` table
the tactical sleeves use. Each sub-sleeve (EMA_BTC, ETH_DAILY, R4_BTC,
R4_ETH) maps to a strategy named ``JPLUS_<sleeve>``; entries, exits,
scales, and leverage adjustments land on ``trade_adjustments``. See
``services.jplus_trade_emitter``.

Daily-return derivation under the migration:
  - The simulator (``jplus.simulate.simulate``) emits a GROSS daily
    return — sub-sleeve fees were removed in Step 5/7 (r4.py
    COST_BP_RT = 0.0; ema_sleeve.py _COMMISSION = 0.0).
  - The trade-emitter records the same window of activity as discrete
    events; CLOSE events on R4 trades carry the 10bp round-trip fee.
  - This service computes the net daily return as ``sim_gross
    − (sum of fee_usdt across today's JPLUS_* events) / capital × 100``
    so that ``variant_daily_returns.return_1x_pct`` remains directly
    comparable to historical rows where fees were baked into the sim.

Service contract follows the same ``try_fire_for_variant(variant,
sleeve_cfg)`` signature as the tactical sleeves. Emitter failures are
logged but do not block the daily-return write — the simulator remains
the fallback source of truth if the emitter raises.

Idempotent per UTC day: a second tick on the same day is a no-op. When
invoked before the current UTC day's Core return is computable (i.e.,
the simulator won't emit a value for "today" — by design, see
``jplus/simulate.py``), the service records nothing. Catches up at the
next UTC day boundary.

Backtest replay bypasses this service — ``backtest_runner.py`` drives
``jplus.simulate()`` directly for a chosen window and writes returns
via ``combine_replay.py``. This service is for the LIVE loop
(``run.py``).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import timedelta
from pathlib import Path

from services import clock
from services import db

log = logging.getLogger("dashboard.jplus_service")

# Magic strategy_id the variant_engine uses to route dispatch here.
# Matches the composition entry injected by register_p300.py.
STRATEGY_ID = "JPLUS-CORE"


def _already_computed_today(variant_id: str, date_iso: str) -> bool:
    """Has Core produced a return for this (variant, date) already?"""
    con = sqlite3.connect(str(db.DASH_DB))
    row = con.execute(
        "SELECT 1 FROM variant_daily_returns "
        "WHERE variant_id = ? AND date = ? AND source = 'live_computed' LIMIT 1",
        (variant_id, date_iso),
    ).fetchone()
    con.close()
    return row is not None


def _write_daily_return(variant_id: str, date_iso: str, return_pct: float,
                        regime: str) -> None:
    con = sqlite3.connect(str(db.DASH_DB))
    now_iso = clock.now_iso()
    con.execute(
        "INSERT OR REPLACE INTO variant_daily_returns "
        "(variant_id, date, return_1x_pct, source, regime, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (variant_id, date_iso, return_pct, "live_computed", regime, now_iso),
    )
    con.commit()
    con.close()


def try_fire_for_variant(variant: dict, sleeve_cfg: dict) -> dict:
    """Compute yesterday's Core J+ daily return (the latest date the
    simulator will emit) and persist it as variant_daily_returns. Returns
    a status dict.

    We compute YESTERDAY's return because the simulator refuses to emit a
    return for the clock date itself (see jplus/simulate.py — avoids
    partial-daily-close look-ahead).
    """
    from jplus import simulate as core_sim

    now = clock.now_utc()
    target_date = (now - timedelta(days=1)).date().isoformat()

    # Run the simulator unconditionally so the emitter has data to work
    # with even when yesterday's VDR row already exists. The bug this
    # fixes: previously the function returned early on _already_computed
    # _today, which meant trade-emit never ran for historical dates that
    # had VDR rows from the pre-Step-6 dispatcher. After this change the
    # emitter runs every tick (idempotent via UNIQUE event-keys) so the
    # JPLUS_* trade ledger stays in sync regardless of VDR state.
    start = (now - timedelta(days=60)).date().isoformat()
    series = core_sim.simulate(start_date=start, end_date=target_date)
    if target_date not in series:
        return {"status": "not_ready",
                "reason": f"simulator has no value for {target_date} yet",
                "date": target_date}

    rec = series[target_date]

    # Trade-emitter side — runs every tick, idempotent by design. Failures
    # must NOT block the daily-return write below.
    try:
        from services import jplus_trade_emitter as emitter
        emitter.emit_for_date(variant, target_date, rec)
    except Exception as e:
        log.error(f"[jplus {variant['id']}] trade-emitter failed for "
                  f"{target_date}: {e!r}", exc_info=True)

    if _already_computed_today(variant["id"], target_date):
        return {"status": "already_recorded_emit_refreshed",
                "date": target_date}

    # Net daily return = simulator gross − trade-event fees for the day.
    # Falls back to sim's gross if the fee aggregator raises (defensive).
    sim_gross_pct = float(rec["return_pct"])
    fees_pct = 0.0
    try:
        from services import trade_adjustments, trade_db
        capital = float(variant.get("capital_usdt") or
                         trade_db.get_config("paper_account_usdt") or 10000)
        summary = trade_adjustments.daily_pnl_from_adjustments(
            target_date, variant["id"], strategy_prefix="JPLUS_")
        fees_pct = (float(summary["fees_usdt"]) / capital) * 100.0
    except Exception as e:
        log.warning(f"[jplus {variant['id']}] fee aggregation failed for "
                    f"{target_date}: {e!r}; writing gross")
    net_return_pct = sim_gross_pct - fees_pct

    _write_daily_return(variant["id"], target_date,
                         net_return_pct, str(rec.get("mode", "")))
    # Headline line — same shape as before so existing log filters work.
    log.info(f"[jplus {variant['id']}] recorded {target_date} "
             f"return={net_return_pct:+.3f}% (gross={sim_gross_pct:+.3f}%, "
             f"fees={fees_pct:+.3f}%) mode={rec['mode']} "
             f"lev={rec['lev']:.2f} gate={rec['gated']} ema_p={rec['ema_p']:+d}")
    # Sub-sleeve attribution receipt. Each contribution is the 1x term;
    # multiply by `lev` to get the final daily-return share.
    lev = float(rec.get("lev", 1.0))
    c_ema = float(rec.get("ema_contrib_1x_pct", 0.0))
    c_eth = float(rec.get("eth_daily_contrib_1x_pct", 0.0))
    c_r4b = float(rec.get("r4_btc_contrib_1x_pct", 0.0))
    c_r4e = float(rec.get("r4_eth_contrib_1x_pct", 0.0))
    btc_d = float(rec.get("btc_daily_pct", 0.0))
    eth_d = float(rec.get("eth_daily_pct", 0.0))
    r4b_p = float(rec.get("r4_btc_pct", 0.0))
    r4e_p = float(rec.get("r4_eth_pct", 0.0))
    r4b_fired = "YES" if rec.get("r4_btc_fired") else "no"
    r4e_fired = "YES" if rec.get("r4_eth_fired") else "no"
    log.info(f"[jplus {variant['id']}]   sub-sleeve attribution for {target_date}:")
    log.info(f"[jplus {variant['id']}]     EMA(BTC):    sign={rec['ema_p']:+d}  "
             f"btc_daily={btc_d:+.2f}%  contrib_1x={c_ema:+.3f}%  "
             f"final={c_ema*lev:+.3f}%")
    log.info(f"[jplus {variant['id']}]     ETH daily:   eth_daily={eth_d:+.2f}%  "
             f"contrib_1x={c_eth:+.3f}%  final={c_eth*lev:+.3f}%")
    log.info(f"[jplus {variant['id']}]     R4 BTC:      fired={r4b_fired}  "
             f"trade_ret={r4b_p:+.2f}%  contrib_1x={c_r4b:+.3f}%  "
             f"final={c_r4b*lev:+.3f}%")
    log.info(f"[jplus {variant['id']}]     R4 ETH:      fired={r4e_fired}  "
             f"trade_ret={r4e_p:+.2f}%  contrib_1x={c_r4e:+.3f}%  "
             f"final={c_r4e*lev:+.3f}%")
    return {
        "status": "recorded",
        "date": target_date,
        "return_pct": net_return_pct,
        "gross_return_pct": sim_gross_pct,
        "fees_pct": fees_pct,
        "mode": rec["mode"],
        "lev": rec["lev"],
        "gated": rec["gated"],
        "ema_p": rec["ema_p"],
    }

"""P-300 variant spec + idempotent registration.

The single source of truth for the P-300 Aggressive 2.0 composition.
Replaces the standalone ``register_p300.py`` script (deleted 2026-05-16
as part of the orchestrator-owns-everything restructure):
  - The composition (sleeves, params, leverages, allocator_notes,
    caveats) lives in :func:`build_spec`.
  - :func:`register` performs the idempotent ``variants`` row insert
    and is called by :mod:`bot` and :mod:`studies.simulation.sim` on
    startup, so the operator never has to run a separate script.
  - The ``weight_pct`` field in each composition entry is retained
    here for the parity tests + operator docs, but the live allocator
    is :mod:`strategies.support.allocation` (P2.4a). Sleeve sizing is
    injected as ``_effective_weight_pct`` per tick; this static field
    is informational.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

VARIANT_ID = "p300_aggressive_v2_v1_0"
DEFAULT_CAPITAL_USDT = 10_000.0


def build_spec() -> dict:
    """Composition spec consumed by :mod:`strategies.orchestrator`.

    Every sleeve in the composition has a live dispatch entry registered
    via ``orchestrator._load_dispatch``. All 13 sleeves are migrated to
    two-phase dispatch as of 2026-05-16; the reconcile pipeline owns
    cross-sleeve coordination (priority / conviction / signal pooling /
    margin headroom).
    """
    return {
        "equity_source": "trades",  # realized PnL from the trade ledger
        "composition": [
            {"strategy_id": "S-003", "weight_pct": 15.0,
             "params": {"stop_loss_pct": 10.0, "leverage": 5.0},
             "note": "ADX at k=5x (Aggressive 2.0 diversifier leverage)"},
            {"strategy_id": "S-078", "weight_pct": 8.0,
             "params": {"leverage": 5.0},
             "note": "Carry at k=5x"},
            {"strategy_id": "S-096", "weight_pct": 6.0,
             "params": {"version": "V4_event_conditioned", "assets": ["BTC", "ETH"],
                        "stop_loss_pct": 5.0, "leverage": 5.0},
             "note": "Thu Bear V4 at k=5x"},
            {"strategy_id": "PDO-L-RF", "weight_pct": 9.0,
             "params": {"assets": ["BTC", "ETH"], "leverage": 1.0,
                        "gap_pct": 2.0, "regime_threshold_pct": -10.0},
             "note": "PDO Retouch Long BTC+ETH at k=1x. Trimmed from 11% "
                     "to 9% on 2026-05-12 to bring tactical total to the "
                     "50% cap (Core/Tactical 50/50 policy)."},
            {"strategy_id": "CPR", "weight_pct": 5.0,
             "params": {"assets": ["BTC", "ETH"], "leverage": 1.0},
             "note": "Contrarian Positioning Reversal BTC+ETH at k=1x (experimental)"},
            {"strategy_id": "FOMC", "weight_pct": 5.0,
             "params": {"leverage": 10.0},
             "note": "FOMC long T-10h to T+0.5h at k=10x — regime+sentiment "
                     "filtered. Time-disjoint from THU_BEAR (FOMC always Tue/Wed) "
                     "so concurrent notional remains within the 2.25x gross target."},
            {"strategy_id": "JPLUS_R4_BTC", "weight_pct": 0.0,
             "params": {"asset": "BTC"},
             "note": "Core J+ R4 BTC: Mon/Wed wk1-2 06:00→18:00 UTC. Sized "
                     "live from today_inputs(). Replaces retrospective emit."},
            {"strategy_id": "JPLUS_R4_ETH", "weight_pct": 0.0,
             "params": {"asset": "ETH"},
             "note": "Core J+ R4 ETH: Tue 20:00 → Wed 20:00 UTC (Wed day≤14)."},
            {"strategy_id": "JPLUS_R4_BTC_V2", "weight_pct": 0.0,
             "params": {"asset": "BTC"},
             "note": "Core J+ R4 BTC V2: Wed/Fri wk1-2 04:00→14:00 UTC "
                     "(added 2026-05-08; era-stable BTC alpha cell)."},
            {"strategy_id": "JPLUS_R4_ETH_V2", "weight_pct": 0.0,
             "params": {"asset": "ETH"},
             "note": "Core J+ R4 ETH V2: Wed/Fri wk1-2 04:00→14:00 UTC "
                     "(added 2026-05-08; cross-asset application of V2)."},
            {"strategy_id": "JPLUS_EMA_BTC", "weight_pct": 0.0,
             "params": {"asset": "BTC"},
             "note": "Core J+ EMA(BTC) continuous; daily SCALE/LEV_ADJ + FLIP "
                     "on weekly EMA cross."},
            {"strategy_id": "JPLUS_ETH_DAILY", "weight_pct": 0.0,
             "params": {"asset": "ETH"},
             "note": "Core J+ ETH daily continuous in bull regimes only."},
            {"strategy_id": "AI_QUANT", "weight_pct": 2.0,
             "params": {"asset": "BTC", "leverage": 3.0,
                        "stop_loss_pct": 10.0, "deterministic": False},
             "note": "AI quant trader (Anthropic Opus 4.7) — daily LLM "
                     "decision at 00:05–00:15 UTC. Default-disabled via "
                     "AI_QUANT_ENABLED env. Phase-1 experiment weight 2%."},
        ],
        "sleeve_leverages": {
            "core": 2.5,
            "s003": 5.0, "s078": 5.0, "s096": 5.0,
            "pdo": 1.0, "cpr": 1.0,
            "fomc": 10.0,
            "r4_btc": 1.0, "r4_eth": 1.0,
            "r4_btc_v2": 1.0, "r4_eth_v2": 1.0,
            "ema_btc": 1.0, "eth_daily": 1.0,
            "ai_quant": 3.0,
        },
        "sleeves_live": ["S-003", "S-078", "S-096", "PDO-L-RF",
                          "CPR", "FOMC",
                          "JPLUS_R4_BTC", "JPLUS_R4_ETH",
                          "JPLUS_R4_BTC_V2", "JPLUS_R4_ETH_V2",
                          "JPLUS_EMA_BTC", "JPLUS_ETH_DAILY",
                          "AI_QUANT"],
        "architecture": "tactical_stack_plus_core_jplus_regimegate",
        "allocator_notes": {
            "core_pct": 50.0,
            "tactical_pct": 50.0,
            "reserve_pct": 0.0,
            "max_net_btc_pdo_cpr_pct": 15.0,
            "btc_cap_policy": "skip_if_over (live) — simulator uses proportional "
                              "scale-down; enforced in strategies/support/risk_caps.py",
            "gross_notional_target_x": 2.25,
        },
    }


def register(dash_db: Optional[str] = None,
             capital_usdt: float = DEFAULT_CAPITAL_USDT,
             quiet: bool = False) -> bool:
    """Ensure the P-300 variant exists in ``dash_db``. Idempotent: returns
    True on a fresh insert, False if the row already existed.

    Path resolution: ``dash_db`` arg → ``P300_DASHBOARD_DB`` env →
    default ``data/prod.db``.

    Called by :mod:`bot` and :mod:`studies.simulation.sim` on startup —
    the operator never runs this directly.
    """
    if dash_db is None:
        dash_db = os.environ.get("P300_DASHBOARD_DB")
    if dash_db is None:
        from strategies.support import db as _db
        dash_db = str(_db.DASH_DB)

    con = sqlite3.connect(dash_db)
    cur = con.cursor()
    try:
        existing = cur.execute(
            "SELECT id FROM variants WHERE id = ?", (VARIANT_ID,),
        ).fetchone()
        if existing is not None:
            return False

        spec_json = json.dumps(build_spec(), indent=2)
        now = datetime.now(timezone.utc).isoformat()
        notes = (
            "P-300 Aggressive 2.0 — full port. 13 live sleeves (6 tactical "
            "+ 6 Core J+ sub-sleeves + AI_QUANT, default-OFF). All sleeves "
            "emit real-time trades; realized PnL from the trade ledger is "
            "the only PnL. As of 2026-05-16, all 13 sleeves are on two-"
            "phase dispatch — the orchestrator's reconcile pass coordinates "
            "cross-sleeve conflict resolution, signal pooling, and margin "
            "headroom."
        )
        cur.execute("""
            INSERT INTO variants (
                id, short_name, long_name, kind, parent_variant_id, version,
                status, is_primary, capital_usdt, color, spec_json, notes,
                superseded_by, reconcile_against, enabled, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            VARIANT_ID,
            "P-300 Aggressive 2.0 1.0",
            ("P-300 full port: Core J+ regime-gated (50%) + 6 tactical sleeves "
             "(S-003/S-078/S-096 V4/PDO-L-RF/CPR/FOMC, 50%)"),
            "full_portfolio",
            None, "1.0", "paper", 0,
            capital_usdt,
            "#7c2d12",
            spec_json,
            notes,
            None, None, 1, now,
        ))
        cur.execute("""
            INSERT INTO variant_events (
                timestamp, variant_id, event_type, actor, details_json, summary
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            now, VARIANT_ID, "registered", "p300_spec.register",
            json.dumps({"scope": "full_port",
                        "live_sleeves": build_spec()["sleeves_live"],
                        "capital_usdt": capital_usdt}),
            "Auto-registered P-300 Aggressive 2.0 1.0 (paper) on startup.",
        ))
        con.commit()
        if not quiet:
            print(f"Registered {VARIANT_ID} in {dash_db}")
        return True
    finally:
        con.close()

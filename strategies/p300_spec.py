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
            # TIMING_ANOMALIES — meta-sleeve consolidating the 8 calendar/
            # clock-driven sub-strategies (was 8 flat composition entries
            # pre-2026-05-18). weight_pct is informational sum-of-children;
            # actual sizing is per-substrategy via the allocator. See
            # strategies/sleeves/timing_anomalies/README.md.
            {"strategy_id": "TIMING_ANOMALIES", "weight_pct": 25.0,
             "params": {
                 "substrategies": {
                     "THU_BEAR":  {"enabled": True, "weight_pct": 6.0, "leverage": 5.0,
                                   "params": {"version": "V4_event_conditioned",
                                              "assets": ["BTC", "ETH"],
                                              "stop_loss_pct": 5.0, "leverage": 5.0},
                                   "note": "Thu Bear V4 at k=5x"},
                     "PDO_L_RF":  {"enabled": True, "weight_pct": 9.0, "leverage": 1.0,
                                   "params": {"assets": ["BTC", "ETH"], "leverage": 1.0,
                                              "gap_pct": 2.0, "regime_threshold_pct": -10.0},
                                   "note": "PDO Retouch Long BTC+ETH at k=1x"},
                     "CPR":       {"enabled": True, "weight_pct": 5.0, "leverage": 1.0,
                                   "params": {"assets": ["BTC", "ETH"], "leverage": 1.0},
                                   "note": "Contrarian Positioning Reversal BTC+ETH at k=1x"},
                     "FOMC":      {"enabled": True, "weight_pct": 5.0, "leverage": 10.0,
                                   "params": {"leverage": 10.0},
                                   "note": "FOMC long T-10h to T+0.5h at k=10x"},
                     "R4_BTC":    {"enabled": True, "weight_pct": 0.0, "leverage": 1.0,
                                   "params": {"asset": "BTC"},
                                   "note": "Core J+ R4 BTC: Mon/Wed wk1-2 06:00→18:00 UTC"},
                     "R4_ETH":    {"enabled": True, "weight_pct": 0.0, "leverage": 1.0,
                                   "params": {"asset": "ETH"},
                                   "note": "Core J+ R4 ETH: Tue 20:00 → Wed 20:00 UTC"},
                     "R4_BTC_V2": {"enabled": True, "weight_pct": 0.0, "leverage": 1.0,
                                   "params": {"asset": "BTC"},
                                   "note": "Core J+ R4 BTC V2: Wed/Fri wk1-2 04:00→14:00 UTC"},
                     "R4_ETH_V2": {"enabled": True, "weight_pct": 0.0, "leverage": 1.0,
                                   "params": {"asset": "ETH"},
                                   "note": "Core J+ R4 ETH V2: Wed/Fri wk1-2 04:00→14:00 UTC"},
                 },
             },
             "note": "Meta-sleeve consolidating all calendar/clock-driven "
                     "sub-strategies into one allocation budget. Per-substrategy "
                     "regime-adaptive weights flow through "
                     "strategies.support.allocation via the substrategy-name -> "
                     "legacy-id mapping in timing_anomalies/internal/."},
            {"strategy_id": "JPLUS_EMA_BTC", "weight_pct": 0.0,
             "params": {"asset": "BTC"},
             "note": "Core J+ EMA(BTC) continuous; daily SCALE/LEV_ADJ + FLIP "
                     "on weekly EMA cross. Kept separate from TIMING_ANOMALIES "
                     "because it doubles as a regime gate for microstructure "
                     "sleeves; merging it would couple the gate to the bucket."},
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
        "sleeves_live": ["S-003", "S-078", "TIMING_ANOMALIES",
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
    default ``data/databases/prod.db``.

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
            "P-300 Aggressive 2.0 — full port. 6 orchestrator-level sleeves "
            "(S-003, S-078, TIMING_ANOMALIES, JPLUS_EMA_BTC, JPLUS_ETH_DAILY, "
            "AI_QUANT) on two-phase dispatch. TIMING_ANOMALIES is a meta-"
            "sleeve consolidating the 8 calendar/clock-driven sub-strategies "
            "(FOMC, THU_BEAR, PDO_L_RF, CPR, R4_BTC, R4_ETH, R4_BTC_V2, "
            "R4_ETH_V2) — consolidation done 2026-05-18. Realized PnL from "
            "the trade ledger is the only PnL. Cross-sleeve conflict "
            "resolution, signal pooling, and margin headroom live in the "
            "orchestrator's reconcile pass."
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


def migrate_to_meta_sleeve_composition(
    dash_db: Optional[str] = None, dry_run: bool = True
) -> dict:
    """One-shot migration of an existing P-300 variant's ``spec_json`` to
    the TIMING_ANOMALIES consolidated composition (2026-05-18 cutover).

    ``register()`` is INSERT-only (idempotent on existence) and won't
    update an existing row. This helper handles the spec rewrite for
    a variant that was registered before the cutover.

    Args:
        dash_db: Path to the dashboard DB. Default is the same resolution
            as :func:`register`.
        dry_run: If True (default), only reports what would change without
            writing. Set False to commit.

    Returns:
        Dict with ``status`` (``"unchanged"|"would_update"|"updated"|"missing"``),
        the old composition's strategy_ids list, and the new ones.

    Operator usage::

        from strategies.p300_spec import migrate_to_meta_sleeve_composition
        migrate_to_meta_sleeve_composition(dry_run=True)   # inspect
        migrate_to_meta_sleeve_composition(dry_run=False)  # commit

    Safe to re-run: idempotent on the new composition shape (no-op if
    already migrated).
    """
    if dash_db is None:
        dash_db = os.environ.get("P300_DASHBOARD_DB")
    if dash_db is None:
        from strategies.support import db as _db
        dash_db = str(_db.DASH_DB)

    con = sqlite3.connect(dash_db)
    cur = con.cursor()
    try:
        row = cur.execute(
            "SELECT spec_json FROM variants WHERE id = ?", (VARIANT_ID,)
        ).fetchone()
        if row is None:
            return {"status": "missing", "variant_id": VARIANT_ID}
        old_spec = json.loads(row[0])
        old_ids = [e.get("strategy_id") for e in old_spec.get("composition", [])]
        if "TIMING_ANOMALIES" in old_ids:
            return {"status": "unchanged", "variant_id": VARIANT_ID,
                     "reason": "already on the meta-sleeve composition",
                     "composition_ids": old_ids}

        new_spec = build_spec()
        # Preserve any operator-set fields that aren't part of build_spec().
        for k, v in old_spec.items():
            if k not in new_spec:
                new_spec[k] = v
        new_ids = [e.get("strategy_id") for e in new_spec.get("composition", [])]

        if dry_run:
            return {"status": "would_update", "variant_id": VARIANT_ID,
                     "old_ids": old_ids, "new_ids": new_ids}

        cur.execute("UPDATE variants SET spec_json = ? WHERE id = ?",
                    (json.dumps(new_spec, indent=2), VARIANT_ID))
        cur.execute("""
            INSERT INTO variant_events
              (timestamp, variant_id, event_type, actor, details_json, summary)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            VARIANT_ID,
            "spec_migrated",
            "p300_spec.migrate_to_meta_sleeve_composition",
            json.dumps({"old_ids": old_ids, "new_ids": new_ids}),
            "Migrated to TIMING_ANOMALIES meta-sleeve composition.",
        ))
        con.commit()
        return {"status": "updated", "variant_id": VARIANT_ID,
                 "old_ids": old_ids, "new_ids": new_ids}
    finally:
        con.close()

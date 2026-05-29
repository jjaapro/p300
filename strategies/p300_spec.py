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

    Every top-level sleeve in the composition has a live dispatch entry
    registered via ``orchestrator._load_dispatch`` and is on the two-phase
    decide/execute path. The 8 calendar/clock substrategies under
    ``TIMING_ANOMALIES`` (FOMC, THU_BEAR, PDO_L_RF, CPR, R4_BTC/ETH/V2)
    flow through the meta-sleeve's two-phase contract. The reconcile
    pipeline owns cross-sleeve coordination (priority / conviction /
    signal pooling / margin headroom).
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
            # CHENTO_TRIPLE_V3 — mean-reversion-into-extreme swing sleeve on
            # BTC perp 15m. Triple composite (B1 money-flow ∩ B5 LSR
            # extremes ∩ B7 multi-TF CVD alignment) + 4 filter gates
            # (no_tilt, no_resist_OB, okx_aligned, skip_up_30d_shorts) + A4
            # ladder with adaptive H_B sizing. 5.4y backtest: 20 trades/yr,
            # mean R +4.13, WR 82%, max-DD −4.52R, MAR 18.4 — see
            # strategies/sleeves/chento_triple_v3/README.md for full
            # provenance and `studies/material/chento/validation/
            # findings_decisions.md` for the per-rule audit.
            {"strategy_id": "CHENTO_TRIPLE_V3", "weight_pct": 10.0,
             "params": {"asset": "BTC", "leverage": 5.0},
             "note": "Triple composite swing on BTC perp 15m. At weight_pct=10 "
                     "× leverage=5, each trade risks ~1.25% NAV worst-case "
                     "(pre-ladder). Ladder T1 (50% add) doubles to ~2.5% NAV "
                     "max loss; T3 (150% add, inside-VA) hits ~4.4% NAV max "
                     "loss. Combined stop −1.5R from original entry caps blast."},
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
            "chento_triple_v3": 5.0,
        },
        "sleeves_live": ["S-003", "S-078", "TIMING_ANOMALIES",
                          "JPLUS_EMA_BTC", "JPLUS_ETH_DAILY",
                          "AI_QUANT", "CHENTO_TRIPLE_V3"],
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
            ("P-300 full port: 7 top-level sleeves "
             "(S-003, S-078, JPLUS_EMA_BTC, JPLUS_ETH_DAILY, AI_QUANT, "
             "SHORT_SQUEEZE, TIMING_ANOMALIES); TIMING_ANOMALIES dispatches "
             "8 calendar/clock substrategies internally"),
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


_LEGACY_TO_SUBSTRATEGY = {
    "S-096":           "THU_BEAR",
    "PDO-L-RF":        "PDO_L_RF",
    "CPR":             "CPR",
    "FOMC":            "FOMC",
    "JPLUS_R4_BTC":    "R4_BTC",
    "JPLUS_R4_ETH":    "R4_ETH",
    "JPLUS_R4_BTC_V2": "R4_BTC_V2",
    "JPLUS_R4_ETH_V2": "R4_ETH_V2",
}


def consolidate_timing_substrategies(spec: dict) -> dict:
    """Rewrite a flat composition to use the TIMING_ANOMALIES meta-sleeve.

    Preserves per-substrategy ``weight_pct``, ``leverage``, ``params``, and
    ``enabled`` from the original flat entries. Non-timing entries (S-003,
    S-078, JPLUS_EMA_BTC, JPLUS_ETH_DAILY, AI_QUANT, JPLUS-CORE,
    SHORT_SQUEEZE, etc.) are left untouched.

    Idempotent: returns the spec unchanged if any composition entry already
    has ``strategy_id == "TIMING_ANOMALIES"`` or if the composition has no
    timing sub-sleeves to consolidate.
    """
    composition = spec.get("composition") or []
    if not composition:
        return spec
    if any(e.get("strategy_id") == "TIMING_ANOMALIES" for e in composition):
        return spec

    substrategies: dict[str, dict] = {}
    new_composition: list[dict] = []
    sum_weight = 0.0
    for entry in composition:
        sid = entry.get("strategy_id")
        sub_name = _LEGACY_TO_SUBSTRATEGY.get(sid)
        if sub_name is None:
            new_composition.append(entry)
            continue
        substrategies[sub_name] = {
            "enabled":    entry.get("enabled", True),
            "weight_pct": entry.get("weight_pct", 0.0),
            "leverage":   (entry.get("params") or {}).get("leverage",
                            entry.get("leverage", 1.0)),
            "params":     {k: v for k, v in (entry.get("params") or {}).items()
                            if k != "leverage"},
        }
        sum_weight += float(entry.get("weight_pct", 0.0) or 0.0)

    if not substrategies:
        return spec

    new_composition.append({
        "strategy_id": "TIMING_ANOMALIES",
        "weight_pct":  sum_weight,
        "params":      {"substrategies": substrategies},
        "note":        "Auto-consolidated from flat composition.",
    })

    new_spec = dict(spec)
    new_spec["composition"] = new_composition
    return new_spec


def migrate_all_variants_to_meta_sleeve(
    dash_db: Optional[str] = None, dry_run: bool = True
) -> list[dict]:
    """Walk every row in ``variants`` and consolidate any flat-composition
    timing sub-sleeves into TIMING_ANOMALIES via
    :func:`consolidate_timing_substrategies`.

    Skips rows whose ``spec_json`` has no ``composition`` field (aggregate
    rollups created by ``combine_replay.py``). Idempotent at the per-row
    level — variants already on the meta-sleeve are reported as unchanged.

    When ``dry_run=False``, writes the new ``spec_json`` and records a
    ``spec_migrated`` event in ``variant_events``.

    Returns a list of per-variant result dicts:
      ``{"variant_id", "status", "old_ids", "new_ids"}``
    where ``status`` is one of ``"updated"``, ``"would_update"``,
    ``"unchanged"``, ``"skipped_no_composition"``.
    """
    if dash_db is None:
        dash_db = os.environ.get("P300_DASHBOARD_DB")
    if dash_db is None:
        from strategies.support import db as _db
        dash_db = str(_db.DASH_DB)

    con = sqlite3.connect(dash_db)
    cur = con.cursor()
    results: list[dict] = []
    try:
        rows = cur.execute("SELECT id, spec_json FROM variants").fetchall()
        for vid, spec_json in rows:
            spec = json.loads(spec_json)
            composition = spec.get("composition")
            if composition is None:
                results.append({"variant_id": vid,
                                 "status": "skipped_no_composition"})
                continue
            new_spec = consolidate_timing_substrategies(spec)
            old_ids = [e.get("strategy_id") for e in composition]
            new_ids = [e.get("strategy_id")
                        for e in new_spec.get("composition", [])]
            if new_ids == old_ids:
                results.append({"variant_id": vid, "status": "unchanged",
                                 "old_ids": old_ids, "new_ids": new_ids})
                continue
            if dry_run:
                results.append({"variant_id": vid, "status": "would_update",
                                 "old_ids": old_ids, "new_ids": new_ids})
                continue
            cur.execute("UPDATE variants SET spec_json = ? WHERE id = ?",
                        (json.dumps(new_spec, indent=2), vid))
            cur.execute("""
                INSERT INTO variant_events
                  (timestamp, variant_id, event_type, actor, details_json, summary)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                datetime.now(timezone.utc).isoformat(),
                vid,
                "spec_migrated",
                "p300_spec.migrate_all_variants_to_meta_sleeve",
                json.dumps({"old_ids": old_ids, "new_ids": new_ids}),
                "Migrated to TIMING_ANOMALIES meta-sleeve composition.",
            ))
            results.append({"variant_id": vid, "status": "updated",
                             "old_ids": old_ids, "new_ids": new_ids})
        if not dry_run:
            con.commit()
        return results
    finally:
        con.close()

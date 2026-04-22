# Register P-300 Aggressive 2.0 1.0 as SHADOW in dashboard variant registry
#
# Per user decision (2026-04-22): ship ONLY v1.3 Aggressive 2.0, not all four
# tiers. Validate realism of per-sleeve 5x diversifier leverage in paper before
# registering intermediate tiers (v1.0 Conservative, v1.1 Regime-dynamic,
# v1.2 Kelly-leveraged). If v1.3 paper Sharpe collapses to <40% of backtest,
# we learn the backtest is over-optimistic; if it holds to 50%+, populate tiers.
#
# Variant ID: p300_aggressive_v2_v1_0
# Config: AV2-2 (diversifiers k=5x, PDO/CPR at 1x)
# Backtest: Sh 7.21, CAGR +211.8%, MDD 18.8%, Calmar 11.26
# Honest-live: Sh 4.0-5.0, CAGR 120-180%, MDD 25-30%

import copy
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# Ensure local services are importable so variant_registry (schema) + trade_db
# (config) get initialised before we start inserting rows.
sys.path.insert(0, os.path.dirname(__file__))
from services import trade_db, variant_registry  # noqa: E402,F401

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
REGISTRY_CSV = os.path.join(DATA_DIR, "p300_registry.csv")
PNL_CSV = os.path.join(DATA_DIR, "p300_daily_pnl.csv")
AGGRESSIVE_CSV = os.path.join(DATA_DIR, "jplus_static25x_mlgate022_daily.csv")
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "dashboard.db")

VARIANT_ID = "p300_aggressive_v2_v1_0"


# ─── Load helpers (copy of experiment_p300_aggressive_v2.py simulator) ────────

def load_registry():
    reg = {}
    with open(REGISTRY_CSV, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            reg[row['sleeve_key']] = {
                'id': row['id'], 'bucket': row['bucket'],
                'weight_target_pct': float(row['weight_target_pct']) / 100,
            }
    return reg


def load_pnl_panel():
    dates = []; panel = {}; btc_bh = []
    with open(PNL_CSV, 'r') as f:
        reader = csv.reader(f)
        header = next(reader)
        sleeves = header[1:-1]
        for s in sleeves: panel[s] = []
        for row in reader:
            dates.append(row[0])
            for i, s in enumerate(sleeves):
                panel[s].append(float(row[i + 1]))
            btc_bh.append(float(row[-1]))
    for s in sleeves: panel[s] = np.array(panel[s])
    return dates, sleeves, panel, np.array(btc_bh)


def load_aggressive_core():
    rows = {}
    with open(AGGRESSIVE_CSV, 'r') as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows[r['date']] = {
                'gated_ret': (float(r['gated_ret']) / 100.0) if r['gated_ret'] else 0.0,
                'mode': r['mode'],
            }
    return rows


# ─── AV2-2 allocator (P-300 Aggressive 2.0 config) ────────────────────────────

CONFIG = {
    'anchor_core_weight': 0.50,
    'anchor_stable': 0.05,
    'bucket_b_target': 0.40,
    'max_net_btc': 0.15,
    'bear_scale': 0.7,
    'tactical_caps': {
        's003_adx_ls': 0.15, 's078_carry': 0.08, 's096_v4_thu_bear': 0.06,
        'pdo_retouch_btc': 0.06, 'pdo_retouch_eth': 0.05,
        'cpr_btc': 0.03, 'cpr_eth': 0.02,
    },
    # Aggressive 2.0 mechanism: diversifiers at 5x, PDO/CPR at 1x
    'sleeve_leverage': {'s003_adx_ls': 5.0, 's096_v4_thu_bear': 5.0, 's078_carry': 5.0},
}


def simulate_daily(dates, panel, core_rets, cfg):
    """Simulate P-300 Aggressive 2.0 and return date -> daily_return."""
    registry = load_registry()
    n = len(dates)
    daily_ret = np.zeros(n)

    for i, d in enumerate(dates):
        core_info = core_rets.get(d, {'gated_ret': 0.0, 'mode': 'uncertain'})
        is_bear = core_info['mode'] == 'bear'
        weights = {'core_jplus_mlgate': cfg['anchor_core_weight'],
                   'stable_yield': cfg['anchor_stable']}

        active_caps = {}
        scale = cfg['bear_scale'] if is_bear else 1.0
        for s, cap in cfg['tactical_caps'].items():
            if abs(panel[s][i]) > 1e-8:
                active_caps[s] = cap * scale
        tactical_sum = sum(active_caps.values())

        if tactical_sum < cfg['bucket_b_target']:
            weights['core_jplus_mlgate'] += cfg['bucket_b_target'] - tactical_sum
        elif tactical_sum > cfg['bucket_b_target']:
            k = cfg['bucket_b_target'] / tactical_sum
            for s in active_caps: active_caps[s] *= k
        for s, w in active_caps.items(): weights[s] = w

        for s, lev in cfg['sleeve_leverage'].items():
            if s in weights: weights[s] *= lev

        long_btc = weights.get('pdo_retouch_btc', 0) + weights.get('cpr_btc', 0)
        if long_btc > cfg['max_net_btc']:
            k = cfg['max_net_btc'] / long_btc
            for kk in ('pdo_retouch_btc', 'cpr_btc'):
                if kk in weights: weights[kk] *= k

        r = 0.0
        for s, w in weights.items():
            if s == 'core_jplus_mlgate':
                r += w * core_info['gated_ret']
            elif s in panel:
                r += w * panel[s][i]
        daily_ret[i] = r

    return dict(zip(dates, daily_ret))


# ─── Variant spec ─────────────────────────────────────────────────────────────

def build_spec():
    """Standard composition-format spec compatible with variant_engine dispatch.
    Core J+ MLgate runs as its own independent variant (seeded via portfolio_id).
    Tactical sleeves dispatch live via STRATEGY_DISPATCH (S-003, S-078, S-096,
    PDO-L-RF, CPR)."""
    return {
        "equity_source": "daily_returns",
        "composition": [
            # Anchor — Core J+ MLgate runs independently, attributed via seed
            {"portfolio_id": "p100_jplus_mlgate_v1_0", "weight_pct": 50.0,
             "note": "Core = mlgate 2.5x (runs as own shadow); outer k=1.0x"},
            # Tactical — live dispatch via STRATEGY_DISPATCH
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
            {"strategy_id": "PDO-L-RF", "weight_pct": 11.0,
             "params": {"assets": ["BTC", "ETH"], "leverage": 1.0,
                        "gap_pct": 2.0, "regime_threshold_pct": -10.0},
             "note": "PDO Retouch Long BTC+ETH at k=1x (newer, less validated)"},
            {"strategy_id": "CPR", "weight_pct": 5.0,
             "params": {"assets": ["BTC", "ETH"], "leverage": 1.0},
             "note": "Contrarian Positioning Reversal BTC+ETH at k=1x (experimental)"},
            # Stable/reserve 5% implicit — composition sums to 95%, remainder
            # treated as cash reserve. Yield accrual (4% APY) tracked in
            # allocator_notes but not dispatched as a strategy (no signals).
        ],
        # Top-level leverage map — per-sleeve dispatch applies these via
        # variant_engine._resolve_sleeve_leverage. sleeve['params']['leverage']
        # on composition entries takes precedence for idiomatic per-entry specs.
        "sleeve_leverages": {
            "core": 1.0,
            "s003": 5.0, "s078": 5.0, "s096": 5.0,
            "pdo": 1.0, "cpr": 1.0,
        },
        "sleeves_live": ["S-003", "S-078", "S-096", "PDO-L-RF", "CPR"],
        "sleeves_seeded_only": ["p100_jplus_mlgate_v1_0"],
        "architecture": "tiered_allocator_composition",
        "allocator_notes": {
            "buckets": {"anchor_pct": 55.0, "tactical_pct": 40.0, "reserve_pct": 5.0},
            "anchor_composition": {"core": 50.0, "stable": 5.0},
            "overflow_rule": "composition is static; overflow logic is a simulator-"
                             "only construct — live dispatch uses fixed weights × leverage",
            "max_net_btc_non_core_pct": 15.0,
            "gross_notional_target_x": 2.8,
        },
        "backtest_metrics": {
            "sharpe": 7.21, "cagr_pct": 211.8, "mdd_pct": 18.8, "calmar": 11.26,
            "total_return_pct": 127356.4, "sample_days": 2295,
            "window": "2020-01-01 to 2026-04-13",
            "source_probe": "experiment_p300_aggressive_v2.py (AV2-2)",
            "panel_source": "p300_registry_v2.py (high-fidelity, real sleeves)",
        },
        "honest_live_expectation": {
            "sharpe_range": "4.0-5.0",
            "cagr_range_pct": "120-180",
            "mdd_range_pct": "25-30",
            "rationale": ("0.55-0.65 discount factor applied to backtest Sharpe; "
                          "accounts for sleeve sim smoothing, leveraged execution "
                          "frictions, funding drag, correlation tightening in tail."),
        },
        "memos": [
            "memory/production/p300_spec.md",
            "memory/checkpoints/session_2026-04-22_p300.md",
            "memory/production/p200_aggressive_2026_04_19.md",
            "memory/production/per_sleeve_kelly_2026_04_19.md",
        ],
    }


# ─── Registration ─────────────────────────────────────────────────────────────

def register():
    print(f"Registering {VARIANT_ID} in {DB_PATH}")
    # 1. Check if already exists
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    existing = cur.execute("SELECT id FROM variants WHERE id = ?", (VARIANT_ID,)).fetchone()
    if existing:
        print(f"  Already registered. Deleting existing entry for clean re-insert.")
        cur.execute("DELETE FROM variant_daily_returns WHERE variant_id = ?", (VARIANT_ID,))
        cur.execute("DELETE FROM variant_events WHERE variant_id = ?", (VARIANT_ID,))
        cur.execute("DELETE FROM variants WHERE id = ?", (VARIANT_ID,))
        con.commit()

    # 2. Insert variant row
    spec_json = json.dumps(build_spec(), indent=2)
    now = datetime.now(timezone.utc).isoformat()
    notes = (
        "ALPHA-SEARCH candidate 2026-04-22. P-300 three-bucket tiered architecture "
        "(Anchor/Tactical/Reserve) with P-200 Aggressive 2.0's per-sleeve leverage "
        "mechanism: diversifiers k=5x (S-003, S-078, S-096 V4), PDO/CPR at 1x, "
        "Core J+ MLgate as Anchor at 50%. Backtest on high-fidelity panel: "
        "Sh 7.21, CAGR +211.8%, MDD 18.8%, Calmar 11.26. Honest-live 0.55-0.65 "
        "discount: Sh 4.0-5.0, CAGR 120-180%, MDD 25-30%. User decision 2026-04-22: "
        "register as sole shadow to validate realism of aggressive per-sleeve "
        "leverage before populating 4-tier slate (Conservative/Regime/Kelly/Aggressive). "
        "If paper live Sh drops to <40% of backtest (<2.9), learn backtest is "
        "over-optimistic. If holds to >50% (>3.6), populate intermediate tiers."
    )
    cur.execute("""
        INSERT INTO variants (
            id, short_name, long_name, kind, parent_variant_id, version, status,
            is_primary, capital_usdt, color, spec_json, notes, superseded_by,
            reconcile_against, enabled, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        VARIANT_ID,
        "P-300 Aggressive 2.0 1.0",
        ("P-300 three-bucket tiered (Anchor/Tactical/Reserve) + per-sleeve "
         "leverage k=5x on diversifiers (S-003, S-078, S-096 V4)"),
        "full_portfolio",
        None,
        "1.0",
        "SHADOW",
        0,
        10000.0,
        "#7c2d12",  # very dark red/brown — distinct from P-200 reds
        spec_json,
        notes,
        None,
        "backtest",
        1,  # enabled=True
        now,
    ))

    # 3. Log registration event
    cur.execute("""
        INSERT INTO variant_events (
            timestamp, variant_id, event_type, actor, details_json, summary
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, (
        now, VARIANT_ID, "registered", "p300_register_script",
        json.dumps({"session": "2026-04-22", "mechanism": "aggressive_2.0_per_sleeve_5x",
                    "backtest_sharpe": 7.21, "backtest_cagr_pct": 211.8}),
        "Registered P-300 Aggressive 2.0 1.0 (full_portfolio, status=SHADOW)",
    ))
    con.commit()

    # 4. Simulate and seed daily returns
    print("  Simulating AV2-2 daily returns on high-fidelity panel...")
    dates, sleeves, panel, btc_bh = load_pnl_panel()
    core_rets = load_aggressive_core()
    daily_ret_map = simulate_daily(dates, panel, core_rets, CONFIG)

    print(f"  Seeding {len(daily_ret_map)} daily returns to variant_daily_returns...")
    rows = [(VARIANT_ID, d, r * 100.0, "backtest_seed", None, now)
            for d, r in daily_ret_map.items()]
    cur.executemany("""
        INSERT INTO variant_daily_returns (variant_id, date, return_1x_pct, source, regime, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, rows)
    con.commit()

    # 5. Verify
    cnt = cur.execute("SELECT COUNT(*) FROM variant_daily_returns WHERE variant_id = ?",
                      (VARIANT_ID,)).fetchone()[0]
    nav_arr = np.array([r * 100.0 for r in daily_ret_map.values()]) / 100.0  # back to decimal
    nav = np.cumprod(1 + nav_arr)
    peak = np.maximum.accumulate(nav)
    mdd = abs(((nav - peak) / peak).min())
    print(f"\n  SEEDED: {cnt} daily returns")
    print(f"  Equity path: {nav[0]:.4f} -> {nav[-1]:.4f} ({(nav[-1]-1)*100:+.1f}% total)")
    print(f"  MDD in seed: {mdd*100:.2f}%")
    print(f"\n  Variant {VARIANT_ID} registered as SHADOW. Visible on dashboard.")
    con.close()


if __name__ == '__main__':
    register()

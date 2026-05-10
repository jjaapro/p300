# Register P-300 Aggressive 2.0 1.0 — full port (Path A Phase 3/4).
#
# The upstream `trader` research repo's backtest results are considered
# compromised; this registration does NOT seed any daily returns and does
# NOT claim any Sharpe / CAGR / MDD figures. Performance is measured by
# the replay + live-NAV the bot itself produces.
#
# What this variant runs live (SHADOW mode only):
#   JPLUS-CORE        — 50% of capital, 2.5x inner leverage, regime-gated
#                         (port of upstream Core J+ minus ML gate minus GOLD
#                          — see jplus/ package; uses daily-return accrual,
#                          not discrete trades)
#   S-003 ADX         — 15% of capital, k=5x
#   S-078 Carry       —  8%, k=5x
#   S-096 V4 Thu Bear —  6%, k=5x
#   PDO-L-RF          — 11% (BTC+ETH), k=1x
#   CPR               —  5% (BTC+ETH), k=1x
#   FOMC              —  5%, k=10x (BTC long T-10h to T+0.5h, regime+
#                                      sentiment filtered)
#
# Live dispatch: JPLUS-CORE contributes to variant_daily_returns (source=
# 'live_computed'). The 6 tactical sleeves emit phantom trades to `trades`.
# Combined NAV = 0.50 × core + 0.50 × tactical (no cash reserve).
#
# Variant ID: p300_aggressive_v2_v1_0

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from services import trade_db, variant_registry  # noqa: E402,F401

VARIANT_ID = "p300_aggressive_v2_v1_0"


def build_spec() -> dict:
    """Composition-format spec consumed by services/variant_engine.py.

    Composition lists all sleeves with live dispatch code in this repo,
    including the ported Core J+ regime-gated portfolio. Daily Core returns
    feed into `variant_daily_returns` (source='live_computed'); the 5 tactical
    sleeves continue writing phantom trades to `trades`. Combined NAV comes
    from weighting both streams.
    """
    return {
        "equity_source": "trades",  # all sleeves emit to trades; PnL is the ledger
        "composition": [
            # Core J+ no longer has a top-level dispatch entry — the
            # six sub-sleeves below (R4_BTC/V2, R4_ETH/V2, EMA_BTC,
            # ETH_DAILY) own real-time trade emission directly. The
            # umbrella JPLUS-CORE that previously wrote a theoretical
            # daily-return row to variant_daily_returns was removed
            # 2026-05-10 (live/sim refactor Phase 3): realized PnL
            # from the trade ledger is the only PnL.
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
             "note": "PDO Retouch Long BTC+ETH at k=1x"},
            {"strategy_id": "CPR", "weight_pct": 5.0,
             "params": {"assets": ["BTC", "ETH"], "leverage": 1.0},
             "note": "Contrarian Positioning Reversal BTC+ETH at k=1x (experimental)"},
            # 5% — FOMC long sleeve, regime + Polymarket + F&G filtered.
            # Replaces the prior 5% stable_yield placeholder. Trades long
            # BTC from T-10h to T+0.5h on FOMC days that pass the rule
            # (skip mid_hold phase, skip cut_25 expectations, skip extreme
            # greed; trade peak_hold/hiking/zirp_hold + extreme_fear unlocks
            # mid_hold). Backtest 2023-09 to 2026-04: 11/11 wins, +1.21%/trade
            # avg, contributes +0.80% CAGR to the tactical stack at k=10x.
            {"strategy_id": "FOMC", "weight_pct": 5.0,
             "params": {"leverage": 10.0},
             "note": "FOMC long T-10h to T+0.5h at k=10x — regime+sentiment "
                     "filtered. Time-disjoint from THU_BEAR (FOMC always Tue/Wed) "
                     "so concurrent notional remains within the 2.25x gross target."},
            # Core J+ sub-sleeves dispatched as live tactical-style entries.
            # weight_pct=0 here is a placeholder — actual sizing comes from
            # jplus.simulate.today_inputs() at trade-open time (regime
            # weight × inner R4 lev × vol-target lev). The four entries
            # exist purely so variant_engine dispatches them per tick.
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
            # AI_QUANT — discretionary LLM trader. Off-by-default via the
            # AI_QUANT_ENABLED env var (services/ai_quant_service.py
            # checks it and short-circuits to status='disabled' otherwise).
            # Set to 2.0% as a phase-1 experiment cap; raise to 5.0% only
            # after 60+ days of forward shadow PnL net of API cost. The
            # `deterministic: False` flag is consumed by backtest_runner
            # to skip this sleeve on historical replay (the LLM is non-
            # deterministic — replay would produce different decisions
            # each run).
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
            # Core sub-sleeves: live handlers compute stacked leverage
            # internally from today_inputs(). 1.0 placeholder.
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
        "missing_sleeves": [
            {"intended_id": "gold_overlay",
             "intended_weight_pct": "dynamic (15-55%)",
             "reason": "PAXG / GOLD crisis hedge — no macro_daily table in p300. "
                       "Originally in upstream P-100 J+ MLgate; dropped in this port. "
                       "If added, would reduce Core MDD (upstream claimed ~50-100bp "
                       "Sharpe contribution; our replay shows tactical partially fills "
                       "the diversifier role)."},
        ],
        "architecture": "tactical_stack_plus_core_jplus_regimegate",
        "allocator_notes": {
            "core_pct": 50.0,
            "tactical_pct": 50.0,
            "reserve_pct": 0.0,
            "max_net_btc_non_core_pct": 15.0,
            "btc_cap_policy": "skip_if_over (live) — simulator uses proportional "
                              "scale-down; enforced in services/risk_caps.py",
            "gross_notional_target_x": 2.25,
            "fomc_concurrent_exposure": "FOMC sleeve runs at k=10x but holds for "
                                        "~11h on 8 calendar dates per year (~0.5%% "
                                        "time-occupancy). Pairwise overlap with "
                                        "every other sleeve is < 0.5%% in 2.6yr "
                                        "backtest, so adding FOMC raises mean "
                                        "concurrent notional by < 0.3%% of capital.",
        },
        "known_methodology_caveats": [
            "S-096 V4 event filter (CPI/NFP-adjacent, ex-OPEX) was derived "
            "post-hoc from V3's Thursday attribution. Any backtest that reuses "
            "the same CPI/NFP/OPEX series V4 was filtered on will outperform "
            "V3 by construction. Live paper is the first genuine OOS record.",
            "CPR has an extremely thin historical sample (n=12 BTC + n=9 ETH "
            "from the upstream research). Neither the prior backtest nor our "
            "future replay will have statistical power; live accumulation is "
            "the only real validation.",
            "PDO regime threshold (-10%) and gap/tolerance params were selected "
            "via parameter sweeps in the upstream repo without visible walk-"
            "forward CV — data-snooping exposure carries over to any backtest.",
            "Any future Sharpe/MDD on the tactical stack is NOT deflated for "
            "multiple-testing. No bootstrap CI, no Monte Carlo, no White's "
            "reality check. Treat point estimates as suggestive only.",
            "Daily-NAV MDD understates intraday DD at 5x leverage in stress "
            "regimes; factor this into any risk claim.",
            "Aggressive 2.0 was chosen from a 4-tier family (Conservative / "
            "Regime-dynamic / Kelly / Aggressive) selected BY backtest "
            "performance — family-level selection bias.",
            "btc_1m in the currently-seeded trader.db is 15-minute granularity "
            "(upstream btc_15m renamed). PDO hourly touch detection and CPR "
            "intraday aggregation are coarser than a true 1m backtest until "
            "binance_feed.py has backfilled real 1m bars.",
            "Live BTC-long cap (skip-if-over) vs simulator cap (proportional "
            "down-scale) diverge by construction — live NAV ≠ sim NAV even "
            "with identical signals.",
            "FOMC sleeve added 2026-04-30 based on a 11-event in-sample "
            "backtest (2023-09 to 2026-04) with 100% win rate. Sample is "
            "thin and the regime filter (peak_hold/hiking/cutting + "
            "F&G + Polymarket cut-prob) was tuned on the same 52-event "
            "historical cohort that informs the live decision rule — "
            "in-sample selection bias applies. The k=10x sizing was "
            "chosen so a single -2% spot move (worst loss in 6.5yr) "
            "produces -1% portfolio impact, well within budget. Live "
            "edge will be re-evaluated after 6+ FOMC events.",
        ],
    }


def register(dash_db: str | None = None) -> None:
    """Register the live variant in ``dash_db``.

    Path resolution order:
      1. ``dash_db`` argument (passed by ``main()`` / ``--dash-db``)
      2. ``P300_DASHBOARD_DB`` env var (kept for back-compat with the
         pre-CLI workflow; emit a deprecation note)
      3. ``data/dashboard.db`` (default)
    """
    db_path = dash_db or os.environ.get("P300_DASHBOARD_DB") or os.path.join(
        os.path.dirname(__file__), "data", "dashboard.db"
    )
    if dash_db is None and os.environ.get("P300_DASHBOARD_DB"):
        print("note: using P300_DASHBOARD_DB env var; "
              "--dash-db flag is preferred")
    print(f"Registering {VARIANT_ID} in {db_path}")
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    existing = cur.execute(
        "SELECT id FROM variants WHERE id = ?", (VARIANT_ID,)
    ).fetchone()
    if existing:
        print("  Already registered. Clearing existing rows for clean re-insert.")
        # variant_daily_returns is no longer auto-created (Phase 7 removed
        # it from init_schema); legacy DBs still have the table with
        # historical rows, so the DELETE only runs when the table exists.
        try:
            cur.execute("DELETE FROM variant_daily_returns WHERE variant_id = ?",
                        (VARIANT_ID,))
        except sqlite3.OperationalError as e:
            if "no such table" not in str(e).lower():
                raise
        cur.execute("DELETE FROM variant_events WHERE variant_id = ?",
                    (VARIANT_ID,))
        cur.execute("DELETE FROM variants WHERE id = ?", (VARIANT_ID,))
        con.commit()

    spec_json = json.dumps(build_spec(), indent=2)
    now = datetime.now(timezone.utc).isoformat()
    notes = (
        "P-300 Aggressive 2.0 1.0 — full port. Upstream backtest results "
        "are treated as compromised and are NOT seeded here; no daily-"
        "returns rows, no Sharpe/CAGR/MDD claims. Live dispatch (since "
        "2026-05-10): 6 tactical sleeves (S-003/S-078/S-096 V4/PDO-L-RF/"
        "CPR/FOMC) + 6 Core J+ sub-sleeves (R4_BTC/V2, R4_ETH/V2, "
        "EMA_BTC, ETH_DAILY) + AI_QUANT (default-OFF). All sleeves emit "
        "real-time trades; realized PnL from the trade ledger is the "
        "only PnL. FOMC sleeve added 2026-04-30 (k=10x, regime + "
        "Polymarket + F&G filtered, 100% in-sample win rate). The "
        "umbrella JPLUS-CORE composition entry was removed 2026-05-10 "
        "along with the simulator-driven daily-return accrual; the "
        "analytic simulator (jplus.simulate.simulate) remains as a "
        "research-only tool, not on any runtime path."
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
        ("P-300 full port: Core J+ regime-gated (50%) + 6 tactical sleeves "
         "(S-003/S-078/S-096 V4/PDO-L-RF/CPR/FOMC, 50%)"),
        "full_portfolio",
        None,
        "1.0",
        "SHADOW",
        0,
        10000.0,
        "#7c2d12",
        spec_json,
        notes,
        None,
        None,  # reconcile_against: nothing to reconcile to — no baseline
        1,
        now,
    ))

    cur.execute("""
        INSERT INTO variant_events (
            timestamp, variant_id, event_type, actor, details_json, summary
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, (
        now, VARIANT_ID, "registered", "p300_register_script",
        json.dumps({"scope": "full_port",
                    "live_sleeves": ["S-003", "S-078", "S-096",
                                      "PDO-L-RF", "CPR", "FOMC",
                                      "JPLUS_R4_BTC", "JPLUS_R4_ETH",
                                      "JPLUS_R4_BTC_V2", "JPLUS_R4_ETH_V2",
                                      "JPLUS_EMA_BTC", "JPLUS_ETH_DAILY",
                                      "AI_QUANT"],
                    "tactical_pct": 50.0,
                    "core_gate": "regime_volpct_t_minus_one",
                    "gold_included": False,
                    "fomc_added_at": "2026-04-30",
                    "core_umbrella_removed_at": "2026-05-10"}),
        "Registered P-300 Aggressive 2.0 1.0 (SHADOW) — 13 live sleeves "
        "(6 tactical + 6 J+ sub-sleeves + AI_QUANT). Realized-PnL only; "
        "JPLUS-CORE umbrella + simulator daily-return accrual removed.",
    ))
    con.commit()

    print(f"  Variant {VARIANT_ID} registered as SHADOW.")
    # Legacy DBs may still have variant_daily_returns rows from before
    # Phase 3 deleted the daily-return accrual; print the count only if
    # the table is present.
    try:
        cnt = cur.execute(
            "SELECT COUNT(*) FROM variant_daily_returns WHERE variant_id = ?",
            (VARIANT_ID,),
        ).fetchone()[0]
        if cnt:
            print(f"  variant_daily_returns rows: {cnt} (legacy artifact, "
                  f"no longer read by any code path).")
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e).lower():
            raise
    con.close()


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Register the P-300 variant in a dashboard.db.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python register_p300.py\n"
            "      register in data/dashboard.db (default)\n"
            "  python register_p300.py --dash-db /tmp/sim_dash.db\n"
            "      register in a sim ledger DB for run.py --mode sim\n"
        ),
    )
    ap.add_argument("--dash-db", default=None,
                    help="Path to dashboard.db. Defaults to "
                         "$P300_DASHBOARD_DB or data/dashboard.db.")
    args = ap.parse_args(argv)
    register(dash_db=args.dash_db)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

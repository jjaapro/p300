"""Track V4, PART A — audit of p300's PAPER ledgers.

Read-only on prod.db. N_TRIALS = 1 for every series (nothing here was selected
by searching over live paper data). No KILL rules; nothing is switched off.

Run:  python studies/notebooks/validation_audit_2026_09/audit_paper_track.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from audit_common import (  # noqa: E402
    TRUST_CUTOFF,
    assign_verdict,
    audit_series,
    btc_daily_returns,
    ro_connect,
    write_csv,
    write_json,
    zero_fill,
)

N_TRIALS_PAPER = 1  # pre-registered, README §0

# ── Series definitions (README §1) ───────────────────────────────────────────
# (key, label, variant_id, strategy SQL predicate or None, note)
SERIES = [
    ("A1", "bot_adx_v1", "bot_adx_v1", None,
     "single-strategy bot; ADX S-003 T2+veto"),
    ("A2", "bot_carry_v1", "bot_carry_v1", None,
     "single-strategy bot; S-078 delta-neutral carry"),
    ("A3", "bot_chento_v3_v1", "bot_chento_v3_v1", None,
     "single-strategy bot; CHENTO_TRIPLE_V3 BTC"),
    ("A4", "bot_chento_v3_eth", "bot_chento_v3_eth", None,
     "single-strategy bot; CHENTO_TRIPLE_V3 ETH"),
    ("A5", "bot_short_squeeze_v1", "bot_short_squeeze_v1", None,
     "single-strategy bot; S-105 short squeeze"),
    ("A6", "bot_r4_v1 (unregistered)", "bot_r4_v1", None,
     "declared in bots/r4/config.py; NOT in the variants table — operator has "
     "not started the runner, so it has never registered"),
    ("A7", "legacy R4 paper (JPLUS_R4_*)", "p300_aggressive_v2_v1_0",
     "strategy LIKE 'JPLUS_R4_%'",
     "the only R4 paper record that exists (pre-July legacy variant)"),
    ("A8", "legacy variant, all sleeves", "p300_aggressive_v2_v1_0", None,
     "context row: mixed-sleeve aggregate, not a strategy"),
]


def load_variants() -> dict[str, dict]:
    con = ro_connect()
    try:
        rows = con.execute(
            "SELECT id, enabled, capital_usdt, status FROM variants"
        ).fetchall()
    finally:
        con.close()
    return {r[0]: {"id": r[0], "enabled": r[1], "capital_usdt": r[2],
                   "status": r[3]} for r in rows}


def load_closed(variant_id: str, strategy_pred: str | None,
                since: str | None = None) -> list[tuple[str, str, float, float, float]]:
    """(entry_iso, exit_iso, pnl_usdt, pnl_pct, size_usdt) for closed trades.

    Same predicates as strategies.support.strategy_health._load_closed_trades
    (status='closed', ordered by actual_exit_time), transcribed here so the
    connection can be opened READ-ONLY — see README §2.1.
    """
    sql = ("SELECT actual_entry_time, actual_exit_time, pnl_usdt, pnl_pct, "
           "       size_usdt "
           "FROM trades WHERE strategy_variant=? AND status='closed' "
           "  AND actual_exit_time IS NOT NULL AND pnl_usdt IS NOT NULL")
    args: list = [variant_id]
    if strategy_pred:
        sql += f" AND {strategy_pred}"
    if since:
        sql += " AND date(actual_exit_time) >= ?"
        args.append(since)
    sql += " ORDER BY actual_exit_time"
    con = ro_connect()
    try:
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    return [(r[0], r[1], float(r[2]), float(r[3] or 0.0), float(r[4] or 0.0))
            for r in rows]


def build_series(key: str, label: str, variant_id: str,
                 strategy_pred: str | None, note: str,
                 capital: float, btc: dict[str, float],
                 since: str | None = None) -> dict:
    trades = load_closed(variant_id, strategy_pred, since)
    trade_dates = [t[1][:10] for t in trades]
    # per-trade return in % OF CAPITAL (README §2.2; the trades table has no
    # risk unit, so mean R is n/a)
    rets_cap = [t[2] / capital * 100.0 for t in trades]
    rets_notional = [t[3] for t in trades]

    if trades:
        sparse: dict[str, float] = {}
        for d, r in zip(trade_dates, rets_cap):
            sparse[d] = sparse.get(d, 0.0) + r
        daily = zero_fill(sparse, min(trade_dates), max(trade_dates))
    else:
        daily = []

    row = audit_series(label, trade_dates, rets_cap, daily,
                       N_TRIALS_PAPER, btc,
                       unit="% of capital", note=note)
    row["key"] = key
    row["variant_id"] = variant_id
    row["capital_usdt"] = capital
    row["strategy_pred"] = strategy_pred or "(all sleeves)"
    row["window"] = "full span" if since is None else f">= {since}"
    row["mean_pct_of_notional"] = (
        sum(rets_notional) / len(rets_notional) if rets_notional else None)
    return assign_verdict(row)


def main() -> int:
    print("=" * 78)
    print("TRACK V4 — PART A: audit of p300 paper ledgers  (N_TRIALS = 1)")
    print("=" * 78)

    variants = load_variants()
    registered_bots = sorted(k for k in variants if k.startswith("bot_"))
    print(f"\nvariants LIKE 'bot_%' registered in prod.db: {len(registered_bots)}")
    for b in registered_bots:
        v = variants[b]
        print(f"  {b:<24} enabled={v['enabled']}  capital={v['capital_usdt']}"
              f"  status={v['status']}")
    print("  bot_r4_v1                UNREGISTERED — declared in "
          "bots/r4/config.py only (see README §1)")

    btc = btc_daily_returns()
    print(f"\nBTC daily benchmark: {len(btc)} days "
          f"{min(btc)} -> {max(btc)}  (cd_futures_ohlcv, read-only)")

    rows: list[dict] = []
    for key, label, vid, pred, note in SERIES:
        cap = variants.get(vid, {}).get("capital_usdt") or 10000.0
        row = build_series(key, label, vid, pred, note, cap, btc)
        rows.append(row)
        print(f"\n--- {key} {label} ---")
        print(f"    n_trades={row['n_trades']}  daily_rows={row['n_daily_rows']}"
              f"  span={row.get('first_trade')}..{row.get('last_trade')}")
        if row["n_trades"]:
            print(f"    mean={row['mean_per_trade']:+.4f}% of capital  "
                  f"WR={row['win_rate_pct']:.0f}%  "
                  f"total={row['total']:+.3f}%")
            print(f"    Sharpe per-trade={row['sharpe_per_trade']:+.3f}  "
                  f"daily(ann)={row['sharpe_daily_ann']:+.3f}  "
                  f"maxDD={row['max_drawdown_units']:.3f}%")
            print(f"    boot per-trade SR 5/50/95 = "
                  f"{row.get('boot_trade_sr_p05'):+.3f} / "
                  f"{row.get('boot_trade_sr_p50'):+.3f} / "
                  f"{row.get('boot_trade_sr_p95'):+.3f}")
            print(f"    DSR(trade,N=1)={row.get('dsr_trade')}  "
                  f"DSR(daily,N=1)={row.get('dsr_daily')}")
            print(f"    CPCV: {row.get('cpcv_decay_ratio')}")
            print(f"    alpha: {row.get('alpha_status')} "
                  f"t={row.get('alpha_t_alpha_nw')} "
                  f"R2={row.get('alpha_r_squared')}")
        print(f"    VERDICT: {row['verdict']}  [{row['clauses_fired']}]")

    # Pre-2026-05-16 split for straddling series (README §2.4)
    print("\n" + "=" * 78)
    print(f"Trust-cutoff split — paper trades before {TRUST_CUTOFF} are not "
          "fully trustworthy")
    print("=" * 78)
    split_rows: list[dict] = []
    for key, label, vid, pred, note in SERIES:
        full = next(r for r in rows if r["key"] == key)
        if not full["n_trades"]:
            continue
        if full["first_trade"] >= TRUST_CUTOFF:
            print(f"  {key} {label}: does not straddle {TRUST_CUTOFF} "
                  f"(first trade {full['first_trade']}) — no split needed")
            continue
        cap = variants.get(vid, {}).get("capital_usdt") or 10000.0
        row = build_series(key + "-post", label + f" (>= {TRUST_CUTOFF})",
                           vid, pred, note + " | trustworthy-window only",
                           cap, btc, since=TRUST_CUTOFF)
        split_rows.append(row)
        print(f"  {key} {label}: {full['n_trades']} trades full span -> "
              f"{row['n_trades']} on/after {TRUST_CUTOFF}")
        if row["n_trades"]:
            print(f"      mean={row['mean_per_trade']:+.4f}%  "
                  f"total={row['total']:+.3f}%  "
                  f"VERDICT: {row['verdict']} [{row['clauses_fired']}]")
        else:
            print(f"      VERDICT: {row['verdict']} [{row['clauses_fired']}]")

    all_rows = rows + split_rows

    # C0 — the audit-level clause
    max_n = max((r["n_trades"] for r in rows), default=0)
    c0_fired = max_n < 20
    print("\n" + "=" * 78)
    print(f"C0: largest Part-A closed-trade count = {max_n} "
          f"(threshold 20) -> C0 {'FIRES' if c0_fired else 'does not fire'}")
    if c0_fired:
        print("    => p300's forward paper record cannot support any "
              "statistical claim as of 2026-09-08.")
    print("=" * 78)

    write_json("part_a_series.json", all_rows)
    write_json("part_a_c0.json", {
        "clause": "C0",
        "rule": "is any paper series' closed-trade count >= 20?",
        "max_closed_trades": max_n,
        "threshold": 20,
        "fired": c0_fired,
        "registered_bot_variants": registered_bots,
        "unregistered_bot_variants": ["bot_r4_v1"],
    })
    cols = ["key", "series", "window", "variant_id", "strategy_pred",
            "n_trades", "n_daily_rows", "first_trade", "last_trade",
            "span_days", "bets_per_year", "unit",
            "mean_per_trade", "mean_pct_of_notional", "median_per_trade",
            "sd_per_trade", "total", "win_rate_pct",
            "sharpe_per_trade", "sharpe_per_trade_ann", "sharpe_daily_ann",
            "max_drawdown_units", "dd_n", "dd_max_duration_days",
            "dd_median_duration_days", "dd_max_depth_pct", "dd_open_at_end",
            "boot_trade_sr_p05", "boot_trade_sr_p50", "boot_trade_sr_p95",
            "block_daily_sr_p05", "block_daily_sr_p50", "block_daily_sr_p95",
            "block_daily_p_sr_gt_0",
            "n_trials", "dsr_trade", "dsr_trade_z", "dsr_daily", "dsr_daily_z",
            "haircut_bonferroni_sharpe", "haircut_holm_sharpe",
            "haircut_bhy_sharpe", "haircut_holm_pct",
            "fl_ic_required", "fl_verdict", "fl_memo_verdict",
            "cpcv_n_splits", "cpcv_train_mean", "cpcv_test_mean",
            "cpcv_decay_ratio",
            "alpha_status", "alpha_n_obs", "alpha_alpha_annual_pct",
            "alpha_beta", "alpha_r_squared", "alpha_t_alpha_nw",
            "alpha_p_alpha_nw",
            "verdict", "clauses_fired", "flags", "note"]
    p = write_csv("part_a_paper_audit.csv", all_rows, cols)
    print(f"\nwrote {p}")
    print(f"wrote {write_json('part_a_series.json', all_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

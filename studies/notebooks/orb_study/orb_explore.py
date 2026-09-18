"""Post-verdict exploratory run (PREREGISTRATION.md section 10). Never changes a verdict.

Refuses to run before results/freeze_verdict.json exists. Everything written here is labelled
exploratory: patterns selected from these tables are hypotheses for a new pre-registration with
data not yet seen, not findings.

    venv\\Scripts\\python.exe studies\\notebooks\\orb_study\\orb_explore.py

Outputs under results/run_v1/exploratory/:
  all_policies.csv        46 family policies + controls, every block and asset, gross and db
  by_year.csv             gross and db expectancy per calendar year, 2020-2026
  excursions.csv          P0 trades: MFE/MAE, where the 16:00 exit left the trade, what came after
  mechanism.json          random-direction/time controls per block, placebo shifts, random-walk check
  diagnostics.json        long/short, ETF era, weekday, DST mismatch, width/relvol terciles, costs, beta
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import orb_calendars as cal
import orb_controls as ctl
import orb_engine as eng
import orb_metrics as met
import orb_policies as pol
from orb_checks import RESULTS, load_market

STUDY = Path(__file__).resolve().parent
OUT = RESULTS / "run_v1" / "exploratory"
BLOCKS = {"development": ("2020-01-01", "2022-12-31"), "validation": ("2023-01-01", "2024-12-31"),
          "lockbox": ("2025-01-01", "2026-09-13")}
ALL = ("2020-01-01", "2026-09-13")


def run(mkt: eng.Market, p: eng.Policy, start: str, end: str) -> pd.DataFrame:
    led = eng.run_policy(mkt, cal.sessions(p.anchor, "2020-01-01", end, shift_min=p.shift_min), p, start, end)
    if "exit_reason" in led:
        led.loc[(led["status"] == "trade") & (led["exit_reason"] == "censored_block_end"), "status"] = "censored"
    return led


def all_policies() -> tuple[pd.DataFrame, dict]:
    rows, full = [], {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        mkt = load_market(sym)
        for p in (*pol.FAMILY, *pol.CONTROLS):
            led = run(mkt, p, *ALL)                       # one continuous run; blocks sliced below
            full[(sym, p.id)] = led
            for block, (a, b) in BLOCKS.items():
                part = led[(led["date"] >= a) & (led["date"] <= b)]
                for sc in ("gross", "db"):
                    s = met.summarize(part, mkt, a, b, sc)
                    rows.append({"symbol": sym, "policy": p.id, "block": block, "scenario": sc,
                                 **{k: v for k, v in s.items() if k != "exit_reasons"},
                                 "exit_reasons": json.dumps(s.get("exit_reasons", {}))})
    return pd.DataFrame(rows), full


def by_year(full: dict) -> pd.DataFrame:
    rows = []
    for (sym, pid), led in full.items():
        tr = met.trades(led)
        if not len(tr):
            continue
        year = tr["date"].str[:4]
        for sc in ("gross", "db"):
            g = met.net_bp(tr, sc).groupby(year.to_numpy())
            for y, m in g.mean().items():
                rows.append({"symbol": sym, "policy": pid, "scenario": sc, "year": y, "mean_bp": m, "trades": int(g.size()[y])})
    return pd.DataFrame(rows)


def excursions(mkt: eng.Market, led: pd.DataFrame) -> pd.DataFrame:
    """For P0 trades: best/worst move while held, the time of the best move, and the move after 16:00."""
    tr = met.trades(led)
    rows = []
    for r in tr.itertuples(index=False):
        f, e, side = int(r.fill_idx), int(r.exit_idx), int(r.side)
        path_h, path_l = mkt.high[f:e + 1], mkt.low[f:e + 1]
        fav = side * ((path_h if side > 0 else path_l) - r.fill_px) / r.fill_px * 1e4
        best = int(np.nanargmax(fav)) if np.isfinite(fav).any() else 0
        after = {}
        if r.exit_reason == "time":
            for hours in (4, 12, 24):
                j = e + hours * 60
                while j < len(mkt) and not mkt.ok(j):
                    j += 1
                after[f"move_after_{hours}h_bp"] = side * (mkt.open[j] - r.exit_px) / r.fill_px * 1e4 if j < len(mkt) else math.nan
        rows.append({"date": r.date, "side": side, "exit_reason": r.exit_reason, "risk_bp": r.risk_bp, "gross_bp": r.gross_bp,
                     "mfe_bp": r.mfe_bp, "mae_bp": r.mae_bp, "minutes_to_mfe": best, "minutes_held": e - f,
                     "gave_back_bp": r.mfe_bp - r.gross_bp, **after})
    return pd.DataFrame(rows)


def random_walk_clock_check() -> dict:
    """Engine sanity: on a random walk, clock long + clock short with range stops must have ~0 gross."""
    import sys
    sys.path.insert(0, str(STUDY / "tests"))
    from test_run_smoke import synthetic_market
    mkt = synthetic_market()
    out = {}
    for pid in ("CTL_CLOCK_LONG", "CTL_CLOCK_SHORT", "P0"):
        tr = met.trades(run(mkt, pol.BY_ID[pid], *ALL))
        g = tr["gross_bp"]
        out[pid] = {"trades": int(len(g)), "gross_mean_bp": float(g.mean()), "se_bp": float(g.std(ddof=1) / math.sqrt(len(g)))}
    return out


def tercile_table(tr: pd.DataFrame, col: str, scenario: str = "db") -> dict:
    x = tr[col].astype(float)
    q = x.quantile([1 / 3, 2 / 3]).to_numpy()
    label = np.where(x <= q[0], "low", np.where(x <= q[1], "mid", "high"))
    net = met.net_bp(tr, scenario)
    gross = tr["gross_bp"]
    return {k: {"trades": int((label == k).sum()), "gross_bp": float(gross[label == k].mean()), "db_bp": float(net[label == k].mean())}
            for k in ("low", "mid", "high")}


def diagnostics(full: dict) -> dict:
    out = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        mkt = load_market(sym)
        hold = ctl.calendar_hold(mkt, *ALL)
        for pid in ("P0", "EXT_WIDTH", "X_NOTIME"):
            led = full[(sym, pid)]
            tr = met.trades(led).copy()
            tr["w_bp"] = tr["W"] / tr["range_open"] * 1e4
            ny = cal.sessions("NY", "2020-01-01", "2026-09-13").set_index("date")
            tr["dst_mismatch"] = tr["date"].map(ny["ny_minus_ldn_hours"]).eq(-4.0)
            net = met.net_bp(tr, "db")
            d = {
                "long": {"trades": int((tr.side > 0).sum()), "gross_bp": float(tr.gross_bp[tr.side > 0].mean()), "db_bp": float(net[tr.side > 0].mean())},
                "short": {"trades": int((tr.side < 0).sum()), "gross_bp": float(tr.gross_bp[tr.side < 0].mean()), "db_bp": float(net[tr.side < 0].mean())},
                "pre_etf": {"trades": int((tr.date < "2024-01-11").sum()), "gross_bp": float(tr.gross_bp[tr.date < "2024-01-11"].mean()), "db_bp": float(net[tr.date < "2024-01-11"].mean())},
                "post_etf": {"trades": int((tr.date >= "2024-01-11").sum()), "gross_bp": float(tr.gross_bp[tr.date >= "2024-01-11"].mean()), "db_bp": float(net[tr.date >= "2024-01-11"].mean())},
                "weekday_gross_bp": tr.groupby(pd.to_datetime(tr.date).dt.day_name())["gross_bp"].mean().round(2).to_dict(),
                "dst_mismatch_weeks": {"trades": int(tr.dst_mismatch.sum()), "gross_bp": float(tr.gross_bp[tr.dst_mismatch].mean())},
                "width_terciles": tercile_table(tr, "w_bp"),
                "relvol_terciles": tercile_table(tr.dropna(subset=["relvol"]), "relvol"),
                "exit_reasons": tr.exit_reason.value_counts().to_dict(),
                "best_trade_share_of_gross": {k: float(tr.gross_bp.nlargest(k).sum() / tr.gross_bp.sum()) for k in (1, 5, 10, 20)},
                "cost_curve_mean_bp": {sc: float(met.net_bp(tr, sc).mean()) for sc in met.COSTS},
                "break_even_rt_bp": float(2 * (tr.gross_bp + tr.funding_bp).mean() / (1 + tr.exit_px / tr.fill_px).mean()),
                "flagged_trades": int((tr["flags"].fillna("") != "").sum()),
            }
            daily = met.daily_returns(led, mkt, *ALL, "db")
            j = pd.concat([daily, hold], axis=1, keys=["s", "b"]).dropna()
            beta = float(np.cov(j.s, j.b, ddof=1)[0, 1] / j.b.var(ddof=1))
            d["beta_vs_calendar_hold"] = beta
            d["corr_vs_calendar_hold"] = float(j.s.corr(j.b))
            d["alpha_daily_bp"] = float((j.s - beta * j.b).mean() * 1e4)
            out[f"{sym}_{pid}"] = d
        s_all = cal.sessions("NY", "2020-01-01", "2026-09-13")
        led_ec = eng.run_policy(mkt, s_all, pol.P0, *ALL, include_early_close=True)
        ec = met.trades(led_ec[led_ec["early_close"]])
        out[f"{sym}_P0_early_close_sessions"] = {"trades": int(len(ec)), "gross_bp": float(ec.gross_bp.mean()) if len(ec) else None}
    return out


def mechanism(full: dict) -> dict:
    out = {"random_walk_engine_check": random_walk_clock_check()}
    for sym in ("BTCUSDT", "ETHUSDT"):
        mkt = load_market(sym)
        p0 = full[(sym, "P0")]
        for block, (a, b) in BLOCKS.items():
            tr = met.trades(p0[(p0.date >= a) & (p0.date <= b)])
            rd = ctl.random_direction(mkt, tr, 200, 42)
            rt = ctl.random_time(mkt, tr, 200, 42)
            g = float(tr.gross_bp.mean())
            out[f"{sym}_{block}"] = {
                "p0_gross_bp": g,
                "random_direction_gross_median_bp": float(rd.mean_bp_gross.median()),
                "random_direction_share_ge_p0": float((rd.mean_bp_gross >= g).mean()),
                "random_time_gross_median_bp": float(rt.mean_bp_gross.median()),
                "random_time_share_ge_p0": float((rt.mean_bp_gross >= g).mean()),
            }
    return out


def diagnostics_only() -> None:
    """Rerun only the diagnostics (their first run stopped on a pandas attribute clash)."""
    full = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        mkt = load_market(sym)
        for pid in ("P0", "EXT_WIDTH", "X_NOTIME"):
            full[(sym, pid)] = run(mkt, pol.BY_ID[pid], *ALL)
    (OUT / "diagnostics.json").write_text(json.dumps(diagnostics(full), indent=1, default=str), encoding="utf-8")


def main() -> None:
    if not (RESULTS / "freeze_verdict.json").exists():
        raise SystemExit("freeze_verdict.json missing: the exploratory run is only allowed after the verdict")
    OUT.mkdir(parents=True, exist_ok=True)
    table, full = all_policies()
    table.to_csv(OUT / "all_policies.csv", index=False)
    by_year(full).to_csv(OUT / "by_year.csv", index=False)
    excursions(load_market("BTCUSDT"), full[("BTCUSDT", "P0")]).to_csv(OUT / "excursions_BTCUSDT_P0.csv", index=False)
    (OUT / "mechanism.json").write_text(json.dumps(mechanism(full), indent=1), encoding="utf-8")
    (OUT / "diagnostics.json").write_text(json.dumps(diagnostics(full), indent=1, default=str), encoding="utf-8")
    trials = [{"run_id": "run_v1", "policy_id": p.id, "config_sha256": "", "symbol": s, "block": "all (exploratory)",
               "cost_model": "gross + db", "selection_use": "exploratory_post_verdict",
               "outcome_access_utc": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")}
              for s in ("BTCUSDT", "ETHUSDT") for p in (*pol.FAMILY, *pol.CONTROLS)]
    pd.DataFrame(trials).to_csv(STUDY / "trial_ledger.csv", mode="a", header=False, index=False)
    print("exploratory outputs:", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    import sys
    diagnostics_only() if sys.argv[1:] == ["diagnostics"] else main()

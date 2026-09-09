"""Track V4, PART B — audit of the two big backtests.

  chento Triple v3, backward-only pool (the honest one):
      studies/notebooks/overlay_study/results_backonly/trades_{BTC,ETH}.csv
  ADX S-003:
      studies/notebooks/adx_study/harness.py :: run()   (called, not modified)

Read-only on prod.db throughout: the harness's own loader opens PROD_DB
read-WRITE, so a read-only transcription of `load_btc_daily` is used here and
the candles are passed into `harness.run()`.

No parameter sweep is re-run. The only re-evaluations are (a) the ATR-multiplier
axis for ADX flat-max, which is an already-documented lever, and (b) pure
post-processing of the on-disk chento trade list (tilt policies, OKX-z
threshold). Nothing is changed as a result.

Run:  python studies/notebooks/validation_audit_2026_09/audit_backtests.py
"""
from __future__ import annotations

import csv
import importlib.util
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from audit_common import (  # noqa: E402
    assign_verdict,
    audit_series,
    alpha_vs_btc,
    btc_daily_returns,
    ro_connect,
    write_csv,
    write_json,
    zero_fill,
)
from studies.lib.validation import bootstrap, dsr_pbo, flat_max, haircut  # noqa: E402

# ── Pre-registered trial counts (README §3) ──────────────────────────────────
CHENTO_N_CONSERVATIVE = 40    # overlay grid: 5 exit x 4 tilt x 2 H, documented
CHENTO_N_AGGRESSIVE = 120     # + upstream searches; STATED ASSUMPTION
ADX_N_CONSERVATIVE = 17       # experiments.py variants on disk
ADX_N_AGGRESSIVE = 40         # + veto sweep + earlier calibration; ASSUMPTION

OVERLAY = _REPO / "studies" / "notebooks" / "overlay_study" / "results_backonly"
ADX_DIR = _REPO / "studies" / "notebooks" / "adx_study"
CHENTO_TIF_DAYS = 3           # TIF 72h — used only to approximate in-position days


# ══ chento ═══════════════════════════════════════════════════════════════════

def load_chento(asset: str, r_col: str = "r72_cost") -> list[dict]:
    """Backward-only OKX-aligned trades with the chosen R column.

    Source: results/chento_r72_{asset}.csv, written by
    audit_chento_cost_check.py, which reproduces the overlay engine's BASE
    replay (TIF 72h) exactly — verified against overlay_summary.csv to
    +0.0000 on BTC mean R — and adds the source pool's own 18bp cost model.

      r72_nocost   TIF 72h, no cost      <- what the study publishes
      r72_cost     TIF 72h, 18bp charged <- PRIMARY here (production-faithful)
      r24_cost_csv the raw trades_*.csv r_outcome column (TIF 24h, cost in)
    """
    src = _HERE / "results" / f"chento_r72_{asset}.csv"
    if not src.exists():
        raise SystemExit(
            f"missing {src} — run audit_chento_cost_check.py first")
    rows = []
    with src.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                v = float(r[r_col])
            except (TypeError, ValueError, KeyError):
                continue
            if not math.isfinite(v):
                continue
            rows.append({"date": r["date"], "ts": r["ts"],
                         "direction": r["direction"], "r": v,
                         "z": float(r["okx_delta_z"]), "asset": asset})
    rows.sort(key=lambda x: x["ts"])
    return rows


def tilt_sizes(rs: list[float], policy: str) -> list[float]:
    """Post-loss size multiplier — the same rule as
    overlay_study/run_overlays.py::tilt_sizes for the two shipped policies.
    Pure post-processing of the outcome sequence; no replay."""
    size = [1.0] * len(rs)
    for i in range(len(rs)):
        if policy == "none" or i == 0:
            continue
        if rs[i - 1] < 0:
            size[i] = 0.0 if policy == "skip_after_loss" else (
                0.5 if policy == "half_after_loss" else 1.0)
    return size


def chento_daily(trades: list[dict], sizes: list[float]) -> list[tuple[str, float]]:
    """Daily R series. The backward-only CSV has no exit timestamp, so R is
    booked on the ENTRY date; TIF is 72h so the attribution error is <= 3 days
    (declared in findings.md)."""
    sparse: dict[str, float] = defaultdict(float)
    for t, s in zip(trades, sizes):
        sparse[t["date"]] += t["r"] * s
    ds = [t["date"] for t in trades]
    return zero_fill(dict(sparse), min(ds), max(ds))


def chento_in_position_dates(trades: list[dict], sizes: list[float]) -> set[str]:
    """Approximate in-position calendar days: entry .. entry + TIF."""
    out: set[str] = set()
    for t, s in zip(trades, sizes):
        if s <= 0:
            continue
        d0 = datetime.strptime(t["date"], "%Y-%m-%d")
        for k in range(CHENTO_TIF_DAYS + 1):
            out.add((d0 + timedelta(days=k)).strftime("%Y-%m-%d"))
    return out


# ══ ADX ══════════════════════════════════════════════════════════════════════

def load_btc_daily_ro() -> list[dict]:
    """Read-only transcription of adx_study/harness.load_btc_daily.

    Identical logic (hourly cd_spot_binance -> daily UTC, drop the still-forming
    current day); the only change is `file:...?mode=ro` instead of a read-write
    connect."""
    con = ro_connect()
    try:
        rows = con.execute(
            "SELECT timestamp, open, high, low, close FROM cd_spot_binance "
            "ORDER BY timestamp"
        ).fetchall()
    finally:
        con.close()
    days = defaultdict(list)
    for ts, o, h, l, c in rows:
        if o is None or c is None or o <= 0 or c <= 0:
            continue
        d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        days[d].append((ts, o, h, l, c))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = []
    for d in sorted(days):
        if d == today:
            continue
        bars = days[d]
        out.append({"ts": bars[0][0], "dt": d, "open": bars[0][1],
                    "high": max(b[2] for b in bars),
                    "low": min(b[3] for b in bars),
                    "close": bars[-1][4]})
    return out


def import_harness():
    spec = importlib.util.spec_from_file_location(
        "adx_harness", ADX_DIR / "harness.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["adx_harness"] = mod
    spec.loader.exec_module(mod)
    return mod


def adx_short_filter_e150(e150):
    def gate(ctx):
        if ctx["new_dir"] == "short":
            return ctx["close"] < e150[ctx["i"]]
        return True
    return gate


def adx_mtm_daily(trades: list[dict], candles: list[dict],
                  cost_bp_rt: float) -> list[tuple[str, float]]:
    """Mark-to-market daily % series for the ADX book.

    For every calendar day inside a trade the day's return is
    ``direction * (close_t / close_{t-1} - 1) * 100``; the round-trip cost is
    booked on the exit day. Needed because an 8.5-year daily series with 27
    exit-date spikes cannot support a beta regression at all (see findings.md).
    """
    closes = {c["dt"]: c["close"] for c in candles}
    dts = [c["dt"] for c in candles]
    idx = {d: i for i, d in enumerate(dts)}
    daily: dict[str, float] = defaultdict(float)
    for t in trades:
        e = t["entry_dt"][:10]
        x = str(t["exit_dt"])[:10]
        if e not in idx or x not in idx:
            continue
        sgn = 1.0 if t["dir"] == "long" else -1.0
        for i in range(idx[e] + 1, idx[x] + 1):
            prev, cur = closes[dts[i - 1]], closes[dts[i]]
            if prev > 0:
                daily[dts[i]] += sgn * (cur / prev - 1.0) * 100.0
        daily[x] -= cost_bp_rt / 100.0
    if not daily:
        return []
    lo, hi = min(daily), max(daily)
    return zero_fill(dict(daily), lo, hi)


def adx_exit_daily(trades: list[dict]) -> list[tuple[str, float]]:
    """Exit-date attribution — the same convention Part A uses."""
    sparse: dict[str, float] = defaultdict(float)
    for t in trades:
        sparse[str(t["exit_dt"])[:10]] += t["net_pct"]
    ds = sorted(sparse)
    return zero_fill(dict(sparse), min(ds), max(ds))


# ══ shared reporting ═════════════════════════════════════════════════════════

def haircut_both(sr_ann: float, n_obs: int, n_cons: int, n_aggr: int) -> dict:
    out = {}
    for tag, n in (("cons", n_cons), ("aggr", n_aggr)):
        hc = haircut.haircut_triple(sr_ann, n_obs, n, freq_per_year=365.0)
        for m in ("bonferroni", "holm", "bhy"):
            out[f"hc_{tag}_{m}_sharpe"] = hc[m]["sharpe_adj"]
            out[f"hc_{tag}_{m}_pct"] = hc[m]["haircut_pct"]
        out[f"hc_{tag}_n_trials"] = n
    return out


def dsr_both(rets, n_cons: int, n_aggr: int, ppy: float) -> dict:
    out = {}
    for tag, n in (("cons", n_cons), ("aggr", n_aggr)):
        d = dsr_pbo.dsr_from_returns(list(rets), n, periods_per_year=ppy)
        out[f"dsr_{tag}"] = d["dsr"] if d else None
        out[f"dsr_{tag}_z"] = d["dsr_z"] if d else None
        out[f"dsr_{tag}_sr_expected"] = d["sr_expected"] if d else None
        out[f"dsr_{tag}_n_trials"] = n
    return out


def main() -> int:  # noqa: C901 - a report, linear on purpose
    print("=" * 78)
    print("TRACK V4 — PART B: audit of the two big backtests")
    print("=" * 78)
    btc = btc_daily_returns()
    rows: list[dict] = []
    extra: dict = {}

    # ── chento ───────────────────────────────────────────────────────────────
    print("\n" + "-" * 78)
    print("chento Triple v3 — backward-only pool (results_backonly/)")
    print(f"  trial counts: conservative N={CHENTO_N_CONSERVATIVE} (overlay grid, "
          f"run_overlays.py:183-201) | aggressive N={CHENTO_N_AGGRESSIVE} (STATED "
          "ASSUMPTION, README §3)")
    print("  PRIMARY series: overlay BASE replay (TIF 72h) with the source")
    print("  pool's own 18bp-scaled cost charged. The study publishes the same")
    print("  replay with NO cost — see audit_chento_cost_check.py / findings.md.")
    print("-" * 78)

    pools = {}
    pools_nocost = {}
    pools_24h = {}
    for asset in ("BTC", "ETH"):
        tr = load_chento(asset, "r72_cost")
        pools[asset] = tr
        pools_nocost[asset] = load_chento(asset, "r72_nocost")
        pools_24h[asset] = load_chento(asset, "r24_cost_csv")
        print(f"  {asset}: {len(tr)} OKX-aligned trades "
              f"{tr[0]['date']} -> {tr[-1]['date']}")

    shipped_tilt = {"BTC": "skip_after_loss", "ETH": "half_after_loss"}
    chento_specs = []
    for asset in ("BTC", "ETH"):
        for tilt in ("none", shipped_tilt[asset]):
            tag = "base/no-tilt" if tilt == "none" else f"SHIPPED ({tilt})"
            chento_specs.append((asset, tilt, tag))

    for asset, tilt, tag in chento_specs:
        tr = pools[asset]
        sizes = tilt_sizes([t["r"] for t in tr], tilt)
        eff = [t["r"] * s for t, s in zip(tr, sizes)]
        traded = [(t, e) for t, e, s in zip(tr, eff, sizes) if s > 0]
        t_dates = [t["date"] for t, _ in traded]
        t_rets = [e for _, e in traded]
        daily = chento_daily(tr, sizes)
        key = f"B_chento_{asset}_{tilt}"
        row = audit_series(f"chento {asset} — {tag}", t_dates, t_rets, daily,
                           CHENTO_N_CONSERVATIVE, btc, unit="R",
                           note=f"backward-only pool, OKX-aligned, base exits "
                                f"TIF 72h WITH 18bp cost, tilt={tilt}; R booked "
                                f"on entry date (no exit ts on disk, TIF<=72h)")
        row["key"] = key
        row["asset"] = asset
        row["tilt"] = tilt
        row.update(dsr_both(t_rets, CHENTO_N_CONSERVATIVE, CHENTO_N_AGGRESSIVE,
                            row.get("bets_per_year") or 365.0))
        row.update(haircut_both(row["sharpe_daily_ann"], row["n_daily_rows"],
                                CHENTO_N_CONSERVATIVE, CHENTO_N_AGGRESSIVE))
        # in-position-only alpha (sparsity control)
        inpos = chento_in_position_dates(tr, sizes)
        d_in = [(d, v) for d, v in daily if d in inpos]
        av = alpha_vs_btc([d for d, _ in d_in], [v for _, v in d_in], btc)
        for k, v in av.items():
            row[f"inpos_alpha_{k}"] = v
        rows.append(assign_verdict(row))
        print(f"\n  [{key}] n={row['n_trades']} traded  mean={row['mean_per_trade']:+.3f}R"
              f"  total={row['total']:+.1f}R  WR={row['win_rate_pct']:.0f}%")
        print(f"      per-trade SR={row['sharpe_per_trade']:+.3f}  "
              f"daily SR(ann)={row['sharpe_daily_ann']:+.3f}  "
              f"maxDD={row['max_drawdown_units']:.1f}R")
        print(f"      boot per-trade SR 5/95 = {row['boot_trade_sr_p05']:+.3f} / "
              f"{row['boot_trade_sr_p95']:+.3f}   block daily SR 5/95 = "
              f"{row['block_daily_sr_p05']:+.3f} / {row['block_daily_sr_p95']:+.3f}")
        print(f"      DSR N={CHENTO_N_CONSERVATIVE}: {row['dsr_cons']:.4f}   "
              f"DSR N={CHENTO_N_AGGRESSIVE}: {row['dsr_aggr']:.4f}")
        print(f"      Holm haircut Sharpe: N={CHENTO_N_CONSERVATIVE} "
              f"{row['hc_cons_holm_sharpe']:.3f}  N={CHENTO_N_AGGRESSIVE} "
              f"{row['hc_aggr_holm_sharpe']:.3f}  (raw {row['sharpe_daily_ann']:.3f})")
        print(f"      CPCV decay={row.get('cpcv_decay_ratio')}  "
              f"alpha t={row.get('alpha_t_alpha_nw')} R2={row.get('alpha_r_squared')}")
        print(f"      in-position-only alpha t={row.get('inpos_alpha_t_alpha_nw')} "
              f"R2={row.get('inpos_alpha_r_squared')} n={row.get('inpos_alpha_n_obs')}")
        print(f"      VERDICT: {row['verdict']}  [{row['clauses_fired']}]")

    # combined BTC+ETH on the common window, shipped per-asset tilt
    end = min(pools["BTC"][-1]["ts"], pools["ETH"][-1]["ts"])
    comb = []
    for asset in ("BTC", "ETH"):
        tr = [t for t in pools[asset] if t["ts"] <= end]
        sizes = tilt_sizes([t["r"] for t in tr], shipped_tilt[asset])
        for t, s in zip(tr, sizes):
            if s > 0:
                comb.append({**t, "eff": t["r"] * s})
    comb.sort(key=lambda x: x["ts"])
    c_dates = [t["date"] for t in comb]
    c_rets = [t["eff"] for t in comb]
    sparse: dict[str, float] = defaultdict(float)
    for t in comb:
        sparse[t["date"]] += t["eff"]
    c_daily = zero_fill(dict(sparse), min(c_dates), max(c_dates))
    row = audit_series("chento COMBINED BTC+ETH — SHIPPED per-asset tilt",
                       c_dates, c_rets, c_daily, CHENTO_N_CONSERVATIVE, btc,
                       unit="R",
                       note="common window; BTC skip-after-loss, ETH "
                            "half-after-loss (docs/calibration/chento_triple_v3.md)")
    row["key"] = "B_chento_COMBINED"
    row["asset"] = "COMBINED"
    row["tilt"] = "BTC skip / ETH half"
    row.update(dsr_both(c_rets, CHENTO_N_CONSERVATIVE, CHENTO_N_AGGRESSIVE,
                        row.get("bets_per_year") or 365.0))
    row.update(haircut_both(row["sharpe_daily_ann"], row["n_daily_rows"],
                            CHENTO_N_CONSERVATIVE, CHENTO_N_AGGRESSIVE))
    rows.append(assign_verdict(row))
    print(f"\n  [B_chento_COMBINED] n={row['n_trades']} "
          f"mean={row['mean_per_trade']:+.3f}R total={row['total']:+.1f}R")
    print(f"      daily SR(ann)={row['sharpe_daily_ann']:+.3f}  "
          f"DSR N={CHENTO_N_CONSERVATIVE}: {row['dsr_cons']:.4f}  "
          f"DSR N={CHENTO_N_AGGRESSIVE}: {row['dsr_aggr']:.4f}")
    print(f"      VERDICT: {row['verdict']}  [{row['clauses_fired']}]")

    # ── chento cost/TIF sensitivity of the DEFLATED number ──────────────────
    print("\n  DSR sensitivity to the cost/TIF choice (shipped tilt per asset)")
    sens = {}
    for asset in ("BTC", "ETH"):
        pol = shipped_tilt[asset]
        for tag, pool in (("r72_nocost (published)", pools_nocost[asset]),
                          ("r72_cost (audit primary)", pools[asset]),
                          ("r24_cost (CSV column)", pools_24h[asset])):
            rs = [t["r"] for t in pool]
            sz = tilt_sizes(rs, pol)
            eff = [r * s for r, s in zip(rs, sz) if s > 0]
            dts = [t["date"] for t, s in zip(pool, sz) if s > 0]
            span = ((datetime.strptime(max(dts), "%Y-%m-%d")
                     - datetime.strptime(min(dts), "%Y-%m-%d")).days + 1)
            bpy = len(eff) * 365.0 / span
            d = dsr_both(eff, CHENTO_N_CONSERVATIVE, CHENTO_N_AGGRESSIVE, bpy)
            k = f"{asset} | {pol} | {tag}"
            sens[k] = {"n": len(eff), "mean_r": float(np.mean(eff)),
                       "sr_per_trade": float(np.mean(eff) / np.std(eff, ddof=1)),
                       **{kk: vv for kk, vv in d.items()}}
            print(f"    {k:<52} n={len(eff):>3} meanR={np.mean(eff):+.3f} "
                  f"SR/trade={sens[k]['sr_per_trade']:+.3f} "
                  f"DSR@40={d['dsr_cons']:.3f} DSR@120={d['dsr_aggr']:.3f}")
    extra["chento_cost_tif_dsr_sensitivity"] = sens

    # ── chento flat-max (post-loss multiplier axis + OKX-z axis) ─────────────
    print("\n  flat-max — post-loss size multiplier (0=skip, 0.5=half, 1=none)")
    fm_out = {}
    for asset in ("BTC", "ETH"):
        tr = pools[asset]
        rs = [t["r"] for t in tr]

        def score(mult: float, rs=rs) -> float:
            pol = ("skip_after_loss" if mult == 0.0
                   else "half_after_loss" if mult == 0.5 else "none")
            sz = tilt_sizes(rs, pol)
            eff = np.array([r * s for r, s in zip(rs, sz)])
            cum = np.cumsum(eff)
            dd = float((cum - np.maximum.accumulate(cum)).min())
            return float(eff.sum() / abs(dd)) if dd < 0 else float("inf")

        chosen = 0.0 if asset == "BTC" else 0.5
        r_fm = flat_max.flat_max_1d([0.0, 0.5, 1.0], chosen, score,
                                    neighborhood_size=2)
        fm_out[f"chento_{asset}_postloss_mult"] = {
            "param_values": r_fm["param_values"],
            "scores": {str(k): v for k, v in r_fm["scores"].items()},
            "chosen": r_fm["chosen"], "chosen_score": r_fm["chosen_score"],
            "peak_param": r_fm["peak_param"], "peak_score": r_fm["peak_score"],
            "chosen_is_peak": r_fm["chosen_is_peak"],
            "flat_max_score": r_fm["flat_max_score"],
            "relative_drop": r_fm["relative_drop"], "verdict": r_fm["verdict"],
            "metric": "MAR-like (total R / |maxDD R|)",
        }
        print(f"    {asset}: chosen={chosen} score={r_fm['chosen_score']:.2f} "
              f"peak={r_fm['peak_param']} ({r_fm['peak_score']:.2f})  "
              f"verdict={r_fm['verdict']}")

    print("\n  flat-max — OKX delta-z gate threshold (chosen 0.0), pure re-filter")
    for asset in ("BTC", "ETH"):
        all_tr = []
        with (OVERLAY / f"trades_{asset}.csv").open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    all_tr.append((r["ts"][:10], r["direction"],
                                   float(r["okx_delta_z"]), float(r["r_outcome"])))
                except (TypeError, ValueError):
                    continue

        def score_z(thr: float, all_tr=all_tr, asset=asset) -> float:
            sel = [(d, ro) for d, dr, z, ro in all_tr
                   if math.isfinite(ro) and
                   ((dr == "long" and z >= thr) or (dr == "short" and z <= -thr))]
            if len(sel) < 5:
                return float("nan")
            rs = [x[1] for x in sel]
            pol = "skip_after_loss" if asset == "BTC" else "half_after_loss"
            sz = tilt_sizes(rs, pol)
            eff = np.array([r * s for r, s in zip(rs, sz)])
            cum = np.cumsum(eff)
            dd = float((cum - np.maximum.accumulate(cum)).min())
            return float(eff.sum() / abs(dd)) if dd < 0 else float("inf")

        vals = [-0.5, -0.25, 0.0, 0.25, 0.5, 1.0]
        r_fm = flat_max.flat_max_1d(vals, 0.0, score_z, neighborhood_size=2)
        fm_out[f"chento_{asset}_okx_z_threshold"] = {
            "param_values": r_fm["param_values"],
            "scores": {str(k): v for k, v in r_fm["scores"].items()},
            "chosen": r_fm["chosen"], "chosen_score": r_fm["chosen_score"],
            "peak_param": r_fm["peak_param"], "peak_score": r_fm["peak_score"],
            "chosen_is_peak": r_fm["chosen_is_peak"],
            "flat_max_score": r_fm["flat_max_score"],
            "relative_drop": r_fm["relative_drop"], "verdict": r_fm["verdict"],
            "metric": "MAR-like (total R / |maxDD R|), shipped tilt applied",
        }
        print(f"    {asset}: chosen=0.0 score={r_fm['chosen_score']:.2f}  "
              f"peak={r_fm['peak_param']} ({r_fm['peak_score']:.2f})  "
              f"verdict={r_fm['verdict']}")
        print(f"       surface: " + "  ".join(
            f"{k:+.2f}:{v:.1f}" for k, v in sorted(r_fm["scores"].items())))

    # ── chento partial PBO (tilt sub-family only) ────────────────────────────
    print("\n  PBO via CSCV — PARTIAL: the 4 tilt variants only.")
    print("    The 5 wick-exit variants need a bar-by-bar replay and the H-tag")
    print("    needs the events table; neither per-variant series is on disk, so")
    print("    the full 40-variant PBO is SKIPPED (README §2.6).")
    pbo_out = {}
    for asset in ("BTC", "ETH"):
        rs = [t["r"] for t in pools[asset]]
        cols = []
        for pol in ("none", "skip_after_loss", "half_after_loss"):
            sz = tilt_sizes(rs, pol)
            cols.append([r * s for r, s in zip(rs, sz)])
        # 4th column: half_after_2stops_7d needs timestamps; approximate with a
        # constant-half policy so the matrix has 4 columns as the grid did
        cols.append([r * 0.5 for r in rs])
        m = [[c[i] for c in cols] for i in range(len(rs))]
        pbo, logits = dsr_pbo.cscv_pbo(m, s=10)
        pbo_out[asset] = {
            "pbo": pbo, "n_variants": len(cols), "n_rows": len(m),
            "s": 10, "n_combos": len(logits),
            "median_logit": float(np.median(logits)) if logits else None,
            "variants": ["tilt=none", "tilt=skip_after_loss",
                         "tilt=half_after_loss", "constant-half (stand-in for "
                         "half_after_2stops_7d, whose timestamps-based rule was "
                         "not re-run)"],
            "caveat": "PARTIAL — 4 of the grid's 40 variants; CSCV at N=4 is "
                      "weak and this number should not be read as the study's PBO.",
        }
        print(f"    {asset}: PBO={pbo:.3f} over {len(cols)} variants, "
              f"{len(m)} rows, {len(logits)} CSCV combos")

    extra["chento_flat_max"] = fm_out
    extra["chento_pbo_partial"] = pbo_out

    # ── ADX ──────────────────────────────────────────────────────────────────
    print("\n" + "-" * 78)
    print("ADX S-003 — harness.run() called directly (harness NOT modified)")
    print(f"  trial counts: conservative N={ADX_N_CONSERVATIVE} (experiments.py "
          f"variants on disk) | aggressive N={ADX_N_AGGRESSIVE} (STATED ASSUMPTION)")
    print("  cost model: harness COST_BP_RT = 10.0 (5bp/leg, spot-only, NO funding)")
    print("-" * 78)
    h = import_harness()
    candles = load_btc_daily_ro()
    print(f"  candles: {len(candles)} daily bars {candles[0]['dt']} -> "
          f"{candles[-1]['dt']}  (cd_spot_binance, read-only)")

    # Reproduction check: the study ran to 2026-06-25. Re-running to that date
    # verifies the read-only candle loader against adx_study/findings.md.
    STUDY_END = "2026-06-25"
    c_study = [c for c in candles if c["dt"] <= STUDY_END]
    e150_study = h.ema([c["close"] for c in c_study], 150)
    m_base_s = h.run(c_study, "2018-01-01")
    m_t2_s = h.run(c_study, "2018-01-01",
                   entry_gate=adx_short_filter_e150(e150_study),
                   exit_mode="adx_or_atr", atr_mult=4.0)
    repro = {
        "study_end_date": STUDY_END,
        "published_baseline": {"n": 34, "ret_pct": 2769, "max_dd_pct": -27.3,
                               "mar": 1.78,
                               "source": "adx_study/findings.md 2026-06-26"},
        "reproduced_baseline": {"n": m_base_s["n"], "ret_pct": m_base_s["ret_pct"],
                                "max_dd_pct": m_base_s["max_dd"],
                                "mar": m_base_s["mar"]},
        "published_tier2": {"n": 27, "ret_pct": 2483, "max_dd_pct": -15.1,
                            "mar": 3.09,
                            "source": "adx_study/findings.md + docs/calibration/adx.md"},
        "reproduced_tier2": {"n": m_t2_s["n"], "ret_pct": m_t2_s["ret_pct"],
                             "max_dd_pct": m_t2_s["max_dd"], "mar": m_t2_s["mar"]},
    }
    extra["adx_reproduction_check"] = repro
    print(f"  reproduction @ {STUDY_END}: baseline n={m_base_s['n']} "
          f"ret={m_base_s['ret_pct']:+.0f}% DD={m_base_s['max_dd']:.1f}% "
          f"MAR={m_base_s['mar']:.2f}  (published 34 / +2769% / -27.3% / 1.78)")
    print(f"                      Tier-2 n={m_t2_s['n']} "
          f"ret={m_t2_s['ret_pct']:+.0f}% DD={m_t2_s['max_dd']:.1f}% "
          f"MAR={m_t2_s['mar']:.2f}  (published 27 / +2483% / -15.1% / 3.09)")
    print("  NOTE: the audit rows below use data through "
          f"{candles[-1]['dt']}, not {STUDY_END}.")
    e50_all = h.ema([c["close"] for c in candles], 50)
    e150 = h.ema([c["close"] for c in candles], 150)

    adx_specs = [
        ("B_adx_baseline", "ADX baseline (live-2026-05 semantics)", {}),
        ("B_adx_tier2", "ADX Tier-2 SHIPPED (sym short<EMA150 + ADX-or-ATRx4)",
         dict(entry_gate=adx_short_filter_e150(e150),
              exit_mode="adx_or_atr", atr_mult=4.0)),
    ]
    for key, label, kw in adx_specs:
        m = h.run(candles, "2018-01-01", **kw)
        trades = m["trades"]
        t_dates = [str(t["exit_dt"])[:10] for t in trades]
        t_rets = [t["net_pct"] for t in trades]
        d_mtm = adx_mtm_daily(trades, candles, h.COST_BP_RT)
        d_exit = adx_exit_daily(trades)
        row = audit_series(label, t_dates, t_rets, d_mtm,
                           ADX_N_CONSERVATIVE, btc, unit="% price (unlevered)",
                           note="daily series = MARK-TO-MARKET (dir x BTC daily "
                                "return while in position, cost booked at exit); "
                                "see findings.md for why exit-date attribution "
                                "cannot support a beta regression here")
        row["key"] = key
        row["harness_n"] = m["n"]
        row["harness_ret_pct"] = m["ret_pct"]
        row["harness_max_dd_pct"] = m["max_dd"]
        row["harness_mar"] = m["mar"]
        row["harness_sharpe_field"] = m["sharpe"]
        row.update(dsr_both(t_rets, ADX_N_CONSERVATIVE, ADX_N_AGGRESSIVE,
                            row.get("bets_per_year") or 365.0))
        row.update(haircut_both(row["sharpe_daily_ann"], row["n_daily_rows"],
                                ADX_N_CONSERVATIVE, ADX_N_AGGRESSIVE))
        # exit-date-attributed comparison series
        from studies.lib.validation import metrics as _mx
        row["exitattr_daily_sharpe_ann"] = _mx.daily_sharpe(
            [v for _, v in d_exit], 365.0)
        av2 = alpha_vs_btc([d for d, _ in d_exit], [v for _, v in d_exit], btc)
        for k, v in av2.items():
            row[f"exitattr_alpha_{k}"] = v
        rows.append(assign_verdict(row))
        print(f"\n  [{key}] harness: n={m['n']} ret={m['ret_pct']:+.0f}% "
              f"maxDD={m['max_dd']:.1f}% MAR={m['mar']:.2f} "
              f"'sharpe' field={m['sharpe']:.2f}")
        print(f"      per-trade: mean={row['mean_per_trade']:+.2f}% "
              f"WR={row['win_rate_pct']:.0f}%  per-trade SR={row['sharpe_per_trade']:+.3f}")
        print(f"      MTM daily rows={row['n_daily_rows']}  "
              f"daily SR(ann)={row['sharpe_daily_ann']:+.3f}  "
              f"maxDD(additive)={row['max_drawdown_units']:.1f}%")
        print(f"      exit-attributed daily SR(ann)="
              f"{row['exitattr_daily_sharpe_ann']:+.3f}  "
              f"(alpha t={row.get('exitattr_alpha_t_alpha_nw')}, "
              f"R2={row.get('exitattr_alpha_r_squared')})")
        print(f"      boot per-trade SR 5/95 = {row['boot_trade_sr_p05']:+.3f} / "
              f"{row['boot_trade_sr_p95']:+.3f}   block daily SR 5/95 = "
              f"{row['block_daily_sr_p05']:+.3f} / {row['block_daily_sr_p95']:+.3f}")
        print(f"      DSR N={ADX_N_CONSERVATIVE}: {row['dsr_cons']:.4f}   "
              f"DSR N={ADX_N_AGGRESSIVE}: {row['dsr_aggr']:.4f}")
        print(f"      Holm haircut Sharpe: N={ADX_N_CONSERVATIVE} "
              f"{row['hc_cons_holm_sharpe']:.3f}  N={ADX_N_AGGRESSIVE} "
              f"{row['hc_aggr_holm_sharpe']:.3f}  (raw {row['sharpe_daily_ann']:.3f})")
        print(f"      CPCV decay={row.get('cpcv_decay_ratio')}")
        print(f"      MTM alpha: annual={row.get('alpha_alpha_annual_pct')} "
              f"beta={row.get('alpha_beta')} R2={row.get('alpha_r_squared')} "
              f"t={row.get('alpha_t_alpha_nw')}")
        print(f"      VERDICT: {row['verdict']}  [{row['clauses_fired']}]")

    # ── ADX exposure decomposition ───────────────────────────────────────────
    # Unconditional daily beta is a WEAK beta test for a strategy that is flat
    # half the time and sometimes short: it can be ~0 while the whole P&L is
    # still "long BTC in a bull market". The decision-relevant comparison is
    # against buy-and-hold over the identical window, plus time-in-market.
    print("\n  ADX exposure decomposition (added while running — see findings.md)")
    from studies.lib.validation import metrics as _mx2
    closes = {c["dt"]: c["close"] for c in candles}
    dts = [c["dt"] for c in candles]
    idx = {d: i for i, d in enumerate(dts)}
    expo = {}
    for key, label, kw in adx_specs:
        m = h.run(candles, "2018-01-01", **kw)
        long_d: set[str] = set()
        short_d: set[str] = set()
        for t in m["trades"]:
            e, x = t["entry_dt"][:10], str(t["exit_dt"])[:10]
            if e not in idx or x not in idx:
                continue
            tgt = long_d if t["dir"] == "long" else short_d
            for i in range(idx[e] + 1, idx[x] + 1):
                tgt.add(dts[i])
        d_mtm = dict(adx_mtm_daily(m["trades"], candles, h.COST_BP_RT))
        window = sorted(d_mtm)
        bh = []
        for i in range(1, len(window)):
            a, b = window[i - 1], window[i]
            if a in closes and b in closes and closes[a] > 0:
                bh.append((b, (closes[b] / closes[a] - 1.0) * 100.0))
        bh_vals = [v for _, v in bh]
        n_days = len(window)
        in_days = long_d | short_d
        # alpha restricted to days the book was actually open
        d_in = [(d, v) for d, v in sorted(d_mtm.items()) if d in in_days]
        av_in = alpha_vs_btc([d for d, _ in d_in], [v for _, v in d_in], btc)
        # alpha restricted to LONG days only (the beta-heavy subset)
        d_lo = [(d, v) for d, v in sorted(d_mtm.items()) if d in long_d]
        av_lo = alpha_vs_btc([d for d, _ in d_lo], [v for _, v in d_lo], btc)
        bh_sr = _mx2.daily_sharpe(bh_vals, 365.0)
        strat_sr = _mx2.daily_sharpe([d_mtm[d] for d in window], 365.0)
        d_bh = dsr_pbo.dsr_from_returns(bh_vals, 1, periods_per_year=365.0)
        # Is the sleeve better than simply holding BTC?
        #
        # The RAW excess (strategy - buy&hold) is apples-to-oranges: the
        # strategy is invested ~36% of days, buy&hold 100%, so raw excess
        # punishes it for being flat and says nothing about risk-adjusted
        # skill. Both are reported; the VOL-MATCHED excess (strategy scaled to
        # buy&hold's daily vol, i.e. the leverage an investor would actually
        # apply) is the decision-relevant one, and the paired block bootstrap
        # on the SHARPE DIFFERENCE is the significance test.
        bh_map = dict(bh)
        common = [d for d in window if d in bh_map]
        s_v = np.array([d_mtm[d] for d in common])
        b_v = np.array([bh_map[d] for d in common])
        exc_raw = list(s_v - b_v)
        scale = (b_v.std(ddof=1) / s_v.std(ddof=1)) if s_v.std(ddof=1) > 0 else 1.0
        exc_vm = list(s_v * scale - b_v)
        exc_sr = _mx2.daily_sharpe(exc_raw, 365.0)
        exc_vm_sr = _mx2.daily_sharpe(exc_vm, 365.0)
        d_exc = dsr_both(exc_vm, ADX_N_CONSERVATIVE, ADX_N_AGGRESSIVE, 365.0)
        b_exc = bootstrap.block_bootstrap_sharpe(exc_vm, block=20, n_iter=5000,
                                                 seed=42, periods_per_year=365.0)
        # paired circular-block bootstrap on SR(strategy) - SR(buy&hold)
        rng = np.random.default_rng(42)
        T = len(common)
        diffs = np.empty(2000)
        for bi in range(2000):
            ix = bootstrap.circular_block_indices(T, T, 20, rng)
            diffs[bi] = (_mx2.daily_sharpe(s_v[ix], 365.0)
                         - _mx2.daily_sharpe(b_v[ix], 365.0))
        expo[key] = {
            "excess_raw_daily_sharpe_ann": exc_sr,
            "excess_raw_caveat": "unequal exposure — not decision-relevant",
            "vol_match_scale": float(scale),
            "excess_volmatched_daily_sharpe_ann": exc_vm_sr,
            "excess_dsr_cons": d_exc["dsr_cons"],
            "excess_dsr_aggr": d_exc["dsr_aggr"],
            "excess_block_sr_p05": b_exc["sr_p05"] if b_exc else None,
            "excess_block_sr_p95": b_exc["sr_p95"] if b_exc else None,
            "excess_n_days": len(exc_vm),
            "sharpe_diff_point": float(np.mean(diffs)),
            "sharpe_diff_p05": float(np.quantile(diffs, 0.05)),
            "sharpe_diff_p95": float(np.quantile(diffs, 0.95)),
            "sharpe_diff_p_gt_0": float((diffs > 0).mean()),
            "n_days": n_days,
            "days_long": len(long_d), "days_short": len(short_d),
            "time_in_market_pct": 100.0 * len(in_days) / n_days,
            "time_long_pct": 100.0 * len(long_d) / n_days,
            "time_short_pct": 100.0 * len(short_d) / n_days,
            "strategy_daily_sharpe_ann": strat_sr,
            "btc_buy_hold_daily_sharpe_ann": bh_sr,
            "btc_buy_hold_dsr_n1": d_bh["dsr"] if d_bh else None,
            "sharpe_ratio_vs_buy_hold": (strat_sr / bh_sr) if bh_sr else None,
            "alpha_in_position_days": av_in,
            "alpha_long_days_only": av_lo,
        }
        e = expo[key]
        print(f"    [{key}] in market {e['time_in_market_pct']:.1f}% of "
              f"{n_days} days (long {e['time_long_pct']:.1f}%, short "
              f"{e['time_short_pct']:.1f}%)")
        print(f"        strategy daily SR {strat_sr:+.3f}  vs  BTC buy-and-hold "
              f"{bh_sr:+.3f}  (ratio {e['sharpe_ratio_vs_buy_hold']:.2f}); "
              f"B&H DSR@N=1 {e['btc_buy_hold_dsr_n1']:.3f}")
        print(f"        alpha on in-position days: t="
              f"{av_in.get('t_alpha_nw')} R2={av_in.get('r_squared')}")
        print(f"        alpha on LONG days only:   t="
              f"{av_lo.get('t_alpha_nw')} R2={av_lo.get('r_squared')} "
              f"beta={av_lo.get('beta')}  <- construction identity, see findings")
        print(f"        excess vs B&H, RAW (unequal exposure, not "
              f"decision-relevant): SR {e['excess_raw_daily_sharpe_ann']:+.3f}")
        print(f"        excess vs B&H, VOL-MATCHED (x{e['vol_match_scale']:.2f}): "
              f"SR {e['excess_volmatched_daily_sharpe_ann']:+.3f}  block boot 5/95 "
              f"{e['excess_block_sr_p05']:+.3f}/{e['excess_block_sr_p95']:+.3f}  "
              f"DSR@{ADX_N_CONSERVATIVE}={e['excess_dsr_cons']:.3f}  "
              f"DSR@{ADX_N_AGGRESSIVE}={e['excess_dsr_aggr']:.3f}")
        print(f"        SR(strategy) - SR(buy&hold) = "
              f"{e['sharpe_diff_point']:+.3f}  5/95 "
              f"{e['sharpe_diff_p05']:+.3f}/{e['sharpe_diff_p95']:+.3f}  "
              f"P(diff>0)={e['sharpe_diff_p_gt_0']:.3f}")
    extra["adx_exposure_decomposition"] = expo

    # ADX flat-max on the ATR-multiplier axis (documented lever, re-evaluated)
    print("\n  flat-max — ADX ATR-trail multiplier (chosen 4.0), MAR score")
    gate = adx_short_filter_e150(e150)

    def score_atr(mult: float) -> float:
        m = h.run(candles, "2018-01-01", entry_gate=gate,
                  exit_mode="adx_or_atr", atr_mult=mult)
        return float(m["mar"]) if math.isfinite(m["mar"]) else float("nan")

    vals = [2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
    r_fm = flat_max.flat_max_1d(vals, 4.0, score_atr, neighborhood_size=2)
    extra["adx_flat_max_atr_mult"] = {
        "param_values": r_fm["param_values"],
        "scores": {str(k): v for k, v in r_fm["scores"].items()},
        "chosen": r_fm["chosen"], "chosen_score": r_fm["chosen_score"],
        "peak_param": r_fm["peak_param"], "peak_score": r_fm["peak_score"],
        "chosen_is_peak": r_fm["chosen_is_peak"],
        "flat_max_score": r_fm["flat_max_score"],
        "relative_drop": r_fm["relative_drop"], "verdict": r_fm["verdict"],
        "metric": "harness MAR (CAGR / |maxDD|)",
    }
    print("    surface: " + "  ".join(f"{k}:{v:.2f}"
                                      for k, v in sorted(r_fm["scores"].items())))
    print(f"    chosen=4.0 score={r_fm['chosen_score']:.2f}  "
          f"peak={r_fm['peak_param']} ({r_fm['peak_score']:.2f})  "
          f"verdict={r_fm['verdict']}")

    extra["adx_pbo"] = {
        "status": "SKIPPED",
        "reason": "no per-variant return series exist on disk for the ADX "
                  "experiment grid (experiments.py prints to stdout and saves "
                  "nothing); reconstructing 17 variants would be re-running the "
                  "sweep, which this study forbids.",
    }
    extra["trial_counts"] = {
        "chento_conservative": CHENTO_N_CONSERVATIVE,
        "chento_conservative_source": "run_overlays.py:183-201 grid = 5 exit x 4 "
                                      "tilt x 2 H-tag; confirmed by 120 data rows "
                                      "in results_backonly/overlay_summary.csv "
                                      "(40 x 3 scopes)",
        "chento_aggressive": CHENTO_N_AGGRESSIVE,
        "chento_aggressive_source": "STATED ASSUMPTION: 40 overlay + 30 stop/target "
                                    "grid (chento_journal/validation_target_sweep_5y."
                                    "py:64-65) + ~25 B-block feature screen "
                                    "(validation_B*.py files) + 7 ladder tiers "
                                    "(memory/project_chento_triple_optimized_config.md)"
                                    " + ~18 TIF/OKX-z/regime/filter variants across "
                                    "the same memory, project_tif_72h_optimal.md, "
                                    "project_chento_regime_filter.md, "
                                    "project_cross_exchange_okx_gate.md. Sum, not "
                                    "product: the searches were sequential.",
        "adx_conservative": ADX_N_CONSERVATIVE,
        "adx_conservative_source": "adx_study/experiments.py:64-91 — 17 labelled "
                                   "variants on disk",
        "adx_aggressive": ADX_N_AGGRESSIVE,
        "adx_aggressive_source": "STATED ASSUMPTION: 17 + the funding-veto z-threshold "
                                 "sweep described in adx_study/findings.md "
                                 "(2026-06-27 addendum; script not on disk) + the "
                                 "2026-05-04 asymmetric-filter and 2026-05-01 "
                                 "signal-source rounds in docs/calibration/adx.md "
                                 "and memory/project_s003_calibration.md",
    }

    cols = ["key", "series", "asset", "tilt", "unit", "n_trades", "n_daily_rows",
            "first_trade", "last_trade", "span_days", "bets_per_year",
            "mean_per_trade", "median_per_trade", "sd_per_trade", "total",
            "win_rate_pct", "sharpe_per_trade", "sharpe_per_trade_ann",
            "sharpe_daily_ann", "exitattr_daily_sharpe_ann",
            "max_drawdown_units", "dd_n", "dd_max_duration_days",
            "dd_median_duration_days", "dd_max_depth_pct", "dd_open_at_end",
            "boot_trade_sr_p05", "boot_trade_sr_p50", "boot_trade_sr_p95",
            "block_daily_sr_p05", "block_daily_sr_p50", "block_daily_sr_p95",
            "block_daily_p_sr_gt_0",
            "dsr_cons_n_trials", "dsr_cons", "dsr_cons_z",
            "dsr_aggr_n_trials", "dsr_aggr", "dsr_aggr_z",
            "hc_cons_bonferroni_sharpe", "hc_cons_holm_sharpe", "hc_cons_bhy_sharpe",
            "hc_aggr_bonferroni_sharpe", "hc_aggr_holm_sharpe", "hc_aggr_bhy_sharpe",
            "hc_aggr_holm_pct",
            "fl_ic_required", "fl_verdict", "fl_memo_verdict",
            "cpcv_n_splits", "cpcv_train_mean", "cpcv_test_mean",
            "cpcv_decay_ratio",
            "alpha_status", "alpha_n_obs", "alpha_alpha_annual_pct", "alpha_beta",
            "alpha_r_squared", "alpha_t_alpha_nw", "alpha_p_alpha_nw",
            "inpos_alpha_n_obs", "inpos_alpha_beta", "inpos_alpha_r_squared",
            "inpos_alpha_t_alpha_nw",
            "exitattr_alpha_r_squared", "exitattr_alpha_t_alpha_nw",
            "harness_n", "harness_ret_pct", "harness_max_dd_pct", "harness_mar",
            "harness_sharpe_field",
            "verdict", "clauses_fired", "flags", "note"]
    print(f"\nwrote {write_csv('part_b_backtest_audit.csv', rows, cols)}")
    print(f"wrote {write_json('part_b_series.json', rows)}")
    print(f"wrote {write_json('part_b_extras.json', extra)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

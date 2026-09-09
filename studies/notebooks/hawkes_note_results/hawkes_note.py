"""S-Hawkes note: descriptive self-excitation measurement on p300 event streams.

Read-only on prod.db. Deterministic (fixed RNG seed). Implements exactly the
pre-registration in studies/notebooks/hawkes_note.md sections 1-4.

Run:  python studies/notebooks/hawkes_note_results/hawkes_note.py
"""
from __future__ import annotations

import json
import sqlite3
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from strategies.support import db

OUT = Path(__file__).resolve().parent
RNG = np.random.default_rng(20260908)
N_REP = 200
BIN_WIDTHS_H = (1, 4, 12, 24, 72)
YEAR_S = 365.25 * 24 * 3600
FUND_CUTOVER = int(dt.datetime(2026, 4, 13, tzinfo=dt.timezone.utc).timestamp())

MIN_YEARS = 2.0
MIN_EVENTS = 500


def conn():
    return sqlite3.connect("file:" + str(db.PROD_DB) + "?mode=ro", uri=True)


def iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------
# stream construction
# --------------------------------------------------------------------------

def rising_edge(cond: np.ndarray) -> np.ndarray:
    """First bar of each new True run."""
    prev = np.concatenate(([False], cond[:-1]))
    return cond & ~prev


def build_streams() -> dict:
    c = conn()
    streams = {}

    # --- S1 LIQ ------------------------------------------------------------
    liq = pd.read_sql(
        "select timestamp, long_quote_quantity, short_quote_quantity "
        "from cd_liquidations order by timestamp", c)
    liq["notional"] = liq.long_quote_quantity.fillna(0) + liq.short_quote_quantity.fillna(0)
    thr = float(np.percentile(liq.notional.values, 95))
    ev = liq.timestamp.values[liq.notional.values >= thr]
    streams["S1_LIQ"] = dict(
        name="LIQ (cd_liquidations, hourly notional >= p95)",
        source="cd_liquidations", source_rows=len(liq),
        t0=int(liq.timestamp.min()), t1=int(liq.timestamp.max()),
        threshold=thr, events=np.asarray(ev, dtype=float),
        edge_rule="flow variable, every crossing",
        coverage=len(liq) / (1 + (liq.timestamp.max() - liq.timestamp.min()) / 3600.0),
    )

    # --- S2 OI_FLUSH -------------------------------------------------------
    oi = pd.read_sql("select timestamp, oi_close from cd_open_interest order by timestamp", c)
    ts = oi.timestamp.values.astype(float)
    v = oi.oi_close.values.astype(float)
    chg = np.full(len(v), np.nan)
    contiguous = np.full(len(v), False)
    contiguous[4:] = (ts[4:] - ts[:-4]) == 4 * 3600
    chg[4:] = v[4:] / v[:-4] - 1.0
    cond = contiguous & (chg <= -0.02)
    ev = ts[rising_edge(cond)]
    # secondary: study's 24h cooldown applied to the raw condition
    raw_idx = np.flatnonzero(cond)
    cd_ev, last = [], -np.inf
    for i in raw_idx:
        if ts[i] - last >= 24 * 3600:
            cd_ev.append(ts[i])
            last = ts[i]
    streams["S2_OI_FLUSH"] = dict(
        name="OI_FLUSH (cd_open_interest, oi_chg_4h <= -2%, rising edge)",
        source="cd_open_interest", source_rows=len(oi),
        t0=int(ts[0]), t1=int(ts[-1]), threshold=-0.02,
        events=ev, edge_rule="overlapping window, rising edge",
        coverage=len(oi) / (1 + (ts[-1] - ts[0]) / 3600.0),
        n_raw_bars=int(cond.sum()), n_cooldown=len(cd_ev),
        events_cooldown=np.asarray(cd_ev, dtype=float),
    )

    # --- S3 FUND_EXT -------------------------------------------------------
    fr = pd.read_sql(
        f"select timestamp, fr_close from cd_funding_rate "
        f"where timestamp < {FUND_CUTOVER} order by timestamp", c)
    ts = fr.timestamp.values.astype(float)
    a = np.abs(fr.fr_close.values.astype(float))
    thr = float(np.percentile(a[~np.isnan(a)], 95))
    cond = a >= thr
    ev = ts[rising_edge(cond)]
    streams["S3_FUND_EXT"] = dict(
        name="FUND_EXT (cd_funding_rate pre-2026-04-13, |fr| >= p95, rising edge)",
        source="cd_funding_rate", source_rows=len(fr),
        t0=int(ts[0]), t1=int(ts[-1]), threshold=thr,
        events=ev, edge_rule="persistent state, rising edge",
        coverage=len(fr) / (1 + (ts[-1] - ts[0]) / 3600.0),
        n_raw_bars=int(cond.sum()),
    )

    # --- S4 BTC_1M ---------------------------------------------------------
    b = pd.read_sql("select open_time, close from btc_1m order by open_time", c)
    ts = b.open_time.values.astype(float) / 1000.0
    px = b.close.values.astype(float)
    r = np.full(len(px), np.nan)
    r[1:] = np.log(px[1:] / px[:-1])
    ok = np.full(len(px), False)
    ok[1:] = (ts[1:] - ts[:-1]) == 60.0
    ar = np.abs(r)
    cond = ok & (ar >= 0.005)
    streams["S4_BTC_1M"] = dict(
        name="BTC_1M (btc_1m, |1m log return| >= 50bp)",
        source="btc_1m", source_rows=len(b),
        t0=int(ts[0]), t1=int(ts[-1]), threshold=0.005,
        events=ts[cond], edge_rule="flow variable, every crossing",
        coverage=len(b) / (1 + (ts[-1] - ts[0]) / 60.0),
    )
    thr999 = float(np.percentile(ar[ok], 99.9))
    streams["S4b_BTC_1M_p999"] = dict(
        name="BTC_1M robustness (|1m log return| >= p99.9)",
        source="btc_1m", source_rows=len(b),
        t0=int(ts[0]), t1=int(ts[-1]), threshold=thr999,
        events=ts[ok & (ar >= thr999)], edge_rule="flow variable, every crossing",
        coverage=len(b) / (1 + (ts[-1] - ts[0]) / 60.0),
    )
    c.close()
    return streams


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------

def counts(ev: np.ndarray, t0: float, t1: float, w_s: float) -> np.ndarray:
    nb = int(np.floor((t1 - t0) / w_s))
    if nb < 2:
        return np.zeros(0)
    edges = t0 + w_s * np.arange(nb + 1)
    return np.histogram(ev, bins=edges)[0].astype(float)


def fano(x: np.ndarray) -> float:
    m = x.mean()
    return float(np.nan) if m <= 0 else float(x.var(ddof=1) / m)


def n_hat(F: float) -> float:
    if not np.isfinite(F) or F < 1.0:
        return 0.0
    return float(1.0 - 1.0 / np.sqrt(F))


def shuffle_iat(ev: np.ndarray, rng) -> np.ndarray:
    d = np.diff(ev)
    rng.shuffle(d)
    return ev[0] + np.concatenate(([0.0], np.cumsum(d)))


def poisson_times(n: int, t0: float, t1: float, rng) -> np.ndarray:
    return np.sort(rng.uniform(t0, t1, size=n))


def acf(x: np.ndarray, maxlag: int) -> np.ndarray:
    x = x - x.mean()
    d = np.dot(x, x)
    return np.array([np.dot(x[:-k], x[k:]) / d for k in range(1, maxlag + 1)])


def analyse(s: dict) -> dict:
    ev = np.sort(s["events"])
    t0, t1 = float(s["t0"]), float(s["t1"])
    span_y = (t1 - t0) / YEAR_S
    n = len(ev)
    res = dict(
        name=s["name"], source=s["source"], source_rows=int(s["source_rows"]),
        window_start=iso(t0), window_end=iso(t1),
        span_years=round(span_y, 3), n_events=int(n),
        threshold=s["threshold"], edge_rule=s["edge_rule"],
        source_coverage=round(float(s["coverage"]), 4),
        gate_span_ok=bool(span_y >= MIN_YEARS),
        gate_events_ok=bool(n >= MIN_EVENTS),
    )
    res["gate_pass"] = bool(res["gate_span_ok"] and res["gate_events_ok"])
    for k in ("n_raw_bars", "n_cooldown"):
        if k in s:
            res[k] = int(s[k])
    if not res["gate_pass"] or n < 3:
        return res

    rng = np.random.default_rng(20260908)
    ladder = {}
    for wh in BIN_WIDTHS_H:
        w = wh * 3600.0
        obs = counts(ev, t0, t1, w)
        F = fano(obs)
        f1 = np.array([fano(counts(shuffle_iat(ev, rng), t0, t1, w)) for _ in range(N_REP)])
        f2 = np.array([fano(counts(poisson_times(n, t0, t1, rng), t0, t1, w)) for _ in range(N_REP)])
        f1, f2 = f1[np.isfinite(f1)], f2[np.isfinite(f2)]
        ladder[f"{wh}h"] = dict(
            bin_hours=wh, n_bins=int(len(obs)), mean_count=round(float(obs.mean()), 5),
            fano=round(F, 4), n_hat=round(n_hat(F), 4),
            null_shuffle_mean=round(float(f1.mean()), 4),
            null_shuffle_p99=round(float(np.percentile(f1, 99)), 4),
            null_shuffle_nhat_mean=round(n_hat(float(f1.mean())), 4),
            obs_pctile_in_shuffle=round(float((f1 < F).mean() * 100), 2),
            null_poisson_mean=round(float(f2.mean()), 4),
            null_poisson_p99=round(float(np.percentile(f2, 99)), 4),
            obs_pctile_in_poisson=round(float((f2 < F).mean() * 100), 2),
        )
    res["ladder"] = ladder
    F24, F72 = ladder["24h"]["fano"], ladder["72h"]["fano"]
    res["plateau_ratio_72_over_24"] = round(F72 / F24, 4) if F24 > 0 else None
    res["C4_plateau"] = bool(res["plateau_ratio_72_over_24"] is not None
                             and res["plateau_ratio_72_over_24"] < 1.25)
    res["C2_beats_both_nulls_1h"] = bool(
        ladder["1h"]["fano"] > ladder["1h"]["null_shuffle_p99"]
        and ladder["1h"]["fano"] > ladder["1h"]["null_poisson_p99"])
    res["C3_nhat_max_ladder"] = round(max(v["n_hat"] for v in ladder.values()), 4)
    res["C3_nhat_ge_0.5"] = bool(res["C3_nhat_max_ladder"] >= 0.5)
    res["FLAG_worth_hawkes"] = bool(res["gate_pass"] and res["C2_beats_both_nulls_1h"]
                                    and res["C3_nhat_ge_0.5"] and res["C4_plateau"])

    # ACF of hourly counts, with shuffle band
    obs1 = counts(ev, t0, t1, 3600.0)
    a_obs = acf(obs1, 24)
    band = np.array([acf(counts(shuffle_iat(ev, rng), t0, t1, 3600.0), 24)
                     for _ in range(N_REP)])
    res["acf_hourly"] = {
        f"lag{k+1}": dict(acf=round(float(a_obs[k]), 5),
                          null_lo=round(float(np.percentile(band[:, k], 2.5)), 5),
                          null_hi=round(float(np.percentile(band[:, k], 97.5)), 5))
        for k in range(24)}
    res["acf_sum_1_24"] = round(float(a_obs.sum()), 4)
    res["acf_ratio_lag24_over_lag1"] = (round(float(a_obs[23] / a_obs[0]), 4)
                                        if a_obs[0] != 0 else None)
    return res


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    streams = build_streams()
    out = {}
    for sid, s in streams.items():
        print(f"--- {sid}: {s['name']}")
        r = analyse(s)
        out[sid] = r
        print(f"    span {r['span_years']}y  events {r['n_events']}  gate={r['gate_pass']}")
        if r.get("ladder"):
            for k, v in r["ladder"].items():
                print(f"    F({k:>3s})={v['fano']:8.3f}  n_hat={v['n_hat']:.3f}  "
                      f"shuffle_p99={v['null_shuffle_p99']:8.3f}  "
                      f"pois_p99={v['null_poisson_p99']:6.3f}")
            print(f"    plateau72/24={r['plateau_ratio_72_over_24']}  "
                  f"C2={r['C2_beats_both_nulls_1h']} C3={r['C3_nhat_ge_0.5']} "
                  f"C4={r['C4_plateau']} FLAG={r['FLAG_worth_hawkes']}")

    # S2 secondary: cooldown version, reported for reference only
    s2 = streams["S2_OI_FLUSH"]
    s2cd = dict(s2)
    s2cd["events"] = s2["events_cooldown"]
    s2cd["name"] = "OI_FLUSH secondary: same rule WITH the study's 24h cooldown"
    out["S2b_OI_FLUSH_cooldown"] = analyse(s2cd)
    print(f"--- S2b cooldown: events {out['S2b_OI_FLUSH_cooldown']['n_events']} "
          f"F(1h)={out['S2b_OI_FLUSH_cooldown'].get('ladder', {}).get('1h', {}).get('fano')}")

    (OUT / "hawkes_note_results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    rows = []
    for sid, r in out.items():
        base = dict(stream=sid, name=r["name"], span_years=r["span_years"],
                    n_events=r["n_events"], gate_pass=r["gate_pass"])
        if r.get("ladder"):
            for k, v in r["ladder"].items():
                rows.append({**base, "bin": k, **{kk: vv for kk, vv in v.items()
                                                  if kk != "bin_hours"}})
        else:
            rows.append({**base, "bin": None})
    pd.DataFrame(rows).to_csv(OUT / "fano_ladder.csv", index=False)

    arows = []
    for sid, r in out.items():
        if "acf_hourly" not in r:
            continue
        for lag, v in r["acf_hourly"].items():
            arows.append(dict(stream=sid, lag=int(lag[3:]), **v))
    pd.DataFrame(arows).to_csv(OUT / "acf_hourly.csv", index=False)
    print("wrote", OUT)


if __name__ == "__main__":
    main()

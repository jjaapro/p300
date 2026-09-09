#!/usr/bin/env python3
"""A1 anchor-allocator study — Step 1: daily unit-exposure return panel.

Writes  results/daily_panel.csv   (date, one column per sleeve in DECIMAL
                                   daily return on 1x notional, btc_bh, and
                                   aux_* columns the simulator needs)
        results/panel_notes.md    (every approximation, with the numbers
                                   that justify or bound it)

Read-only against prod.db. Nothing outside this study directory is written.
Run from the repo root:  venv\\Scripts\\python studies/notebooks/anchor_allocator_study/build_panel.py

Sources (verified in code before use — see README.md "Sources"):
  J+ loop     strategies.support.jplus_inputs._run_decision_loop()
  R4 math     strategies.sleeves.timing_anomalies.internal.r4.math
  ADX         studies.notebooks.adx_study.harness (T2 production config)
  chento      studies/notebooks/overlay_study/results_backonly/trades_{BTC,ETH}.csv
  carry       strategies.support.funding.daily_sums_pct + carry config
  GOLD        paxg_spot_1h (from 2020-08-28), macro_daily GOLD before that
"""
from __future__ import annotations

import math
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):          # plain console only; ipykernel streams lack it
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from strategies.support import db as _db                                   # noqa: E402
from strategies.support import funding as _funding                          # noqa: E402
from strategies.support import jplus_inputs                                 # noqa: E402
from strategies.sleeves.timing_anomalies.internal.r4 import math as r4math  # noqa: E402
from strategies.sleeves.carry.config import (                               # noqa: E402
    ENTRY_EXIT_COST_PCT, EXIT_NEG_DAYS, FR_ENTRY_THRESHOLD, FR_WINDOW_DAYS,
)
from studies.notebooks.adx_study import harness as adx_harness              # noqa: E402

RESULTS = HERE / "results"
START = "2020-01-01"
BP = 1e-4
FLIP_COST = 15 * BP            # EMA flip, eth_daily regime entry/exit, R4 per fire (x inner lev)
ADX_COST = adx_harness.COST_BP_RT * BP   # 10 bp RT, charged on the exit day's mark
CASH_YIELD_DAILY = 0.04 / 365  # assumption, see README interpretation 6
CHENTO_TIF_H = 72
CHENTO_UP30_SKIP = 0.10        # skip SHORTS when BTC 30d > +10 % (production filter 4)
CHENTO_TILT = {"BTC": "skip_after_loss", "ETH": "half_after_loss"}
PAXG_START = "2020-08-28"      # first paxg_spot_1h bar
R4_LEV_UNGATED = jplus_inputs.R4_EXTRA_LEV_UNGATED
R4_LEV_GATED = jplus_inputs.R4_EXTRA_LEV_GATED
BULL_MODES = ("strong_bull", "mild_bull")

TACTICAL = ["r4_btc", "r4_eth", "r4_btc_v2", "r4_eth_v2", "adx", "carry",
            "chento_btc", "chento_eth", "eth_daily"]
ANCHOR = ["ema_1w_btc", "gold", "cash"]

NOTES: list[str] = []


def note(s: str = "") -> None:
    NOTES.append(s)
    print(s)


def ro_connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{_db.PROD_DB}?mode=ro", uri=True)


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ─── J+ loop ─────────────────────────────────────────────────────────────────

def jplus_frame() -> pd.DataFrame:
    t0 = time.time()
    rows, _ = jplus_inputs._run_decision_loop()
    lp = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    lp.index.name = "date"
    note(f"- J+ loop: {len(lp)} days {lp.index[0]} → {lp.index[-1]} in {time.time() - t0:.1f}s "
         f"(`_run_decision_loop`, calendar = cd_spot_binance daily).")
    return lp


def full_day_end(con: sqlite3.Connection) -> tuple[str, list[tuple[str, int]]]:
    """Last date with all 24 hourly spot bars that is strictly before today UTC."""
    rows = con.execute(
        "SELECT date(timestamp,'unixepoch') d, COUNT(*) n FROM cd_spot_binance "
        "WHERE timestamp >= strftime('%s', ?) GROUP BY 1 ORDER BY 1", (START,)).fetchall()
    today = today_utc()
    short = [(d, n) for d, n in rows if n < 24 and d < today]
    full = [d for d, n in rows if n >= 24 and d < today]
    return full[-1], short


# ─── ETH full period (the loop's ETH inputs are bounded to 3y by data/loaders) ─

def eth_maps(con: sqlite3.Connection, start_iso: str):
    """Same aggregation as data.loaders.load_eth_hourly / load_eth_daily
    (first 1m open, last 1m close per bucket) but over the full period."""
    start_ms = int(datetime.fromisoformat(start_iso).replace(tzinfo=timezone.utc).timestamp() * 1000)
    cur = con.execute("SELECT open_time, open, close FROM eth_1m WHERE open_time >= ? ORDER BY open_time",
                      (start_ms,))
    hourly: dict[tuple[str, int], list[float]] = {}
    daily: dict[str, list[float]] = {}
    day_iso: dict[int, str] = {}
    n = 0
    for ot, o, c in cur:
        if o is None or c is None:
            continue
        n += 1
        ot = int(ot)
        dnum = ot // 86_400_000
        d = day_iso.get(dnum)
        if d is None:
            d = datetime.fromtimestamp(dnum * 86_400, tz=timezone.utc).date().isoformat()
            day_iso[dnum] = d
        h = (ot // 3_600_000) % 24
        k = (d, h)
        hb = hourly.get(k)
        if hb is None:
            hourly[k] = [float(o), float(c)]
        else:
            hb[1] = float(c)
        db_ = daily.get(d)
        if db_ is None:
            daily[d] = [float(o), float(c)]
        else:
            db_[1] = float(c)
    note(f"- ETH: {n:,} eth_1m rows streamed from {start_iso} → {len(hourly):,} hourly buckets, "
         f"{len(daily):,} days (same first-open/last-close aggregation as `data/loaders.py`).")
    return {k: (v[0], v[1]) for k, v in hourly.items()}, {d: (v[0], v[1]) for d, v in daily.items()}


# ─── ADX (T2 production config, as tests/test_adx_parity.py calls the harness) ─

def _short_filter_e150(ctx):
    if ctx["new_dir"] == "short":
        te = ctx["trend_ema"]
        return te is not None and not math.isnan(te) and ctx["close"] < te
    return True


def adx_marks(dates: list[str]) -> tuple[pd.Series, pd.Series, dict]:
    candles = adx_harness.load_btc_daily()
    res = adx_harness.run(candles, "2018-01-01", entry_gate=_short_filter_e150,
                          exit_mode="adx_or_atr", atr_mult=4.0)
    trades = res["trades"]
    dts = [c["dt"] for c in candles]
    idx = {d: i for i, d in enumerate(dts)}
    marks = pd.Series(0.0, index=dates)
    dirs = pd.Series(0, index=dates, dtype=int)
    long_dev, short_dev, n_long, n_short, still_open = 0.0, 0.0, 0, 0, 0
    for t in trades:
        e = idx[t["entry_dt"]]
        x = idx[t["exit_dt"].replace(" (open)", "")]
        sign = 1 if t["dir"] == "long" else -1
        prod = 1.0
        for j in range(e + 1, x + 1):
            prev_c = candles[j - 1]["close"]
            px = t["exit_price"] if j == x else candles[j]["close"]
            m = sign * (px / prev_c - 1.0)
            prod *= 1.0 + m
            if j == x and not t.get("still_open"):
                m -= ADX_COST
            d = dts[j]
            if d in marks.index:
                marks[d] += m
                dirs[d] = sign
        dev = abs((prod - 1.0) - t["gross_pct"] / 100.0)
        if sign > 0:
            long_dev = max(long_dev, dev); n_long += 1
        else:
            short_dev = max(short_dev, dev); n_short += 1
        still_open += int(bool(t.get("still_open")))
    info = dict(n=len(trades), n_long=n_long, n_short=n_short, still_open=still_open,
                long_dev=long_dev, short_dev=short_dev, first=trades[0]["entry_dt"],
                last=trades[-1]["entry_dt"], candles_end=dts[-1],
                harness_ret_pct=res["ret_pct"], harness_maxdd=res["max_dd"], harness_wr=res["wr"])
    return marks, dirs, info


# ─── chento (backward-only pool + production filter stack) ───────────────────

def tilt_sizes(rs: np.ndarray, policy: str) -> np.ndarray:
    """Verbatim semantics of overlay_study/run_overlays.tilt_sizes for the two
    production policies (skip / half after a losing trade)."""
    size = np.ones(len(rs))
    for i in range(1, len(rs)):
        if policy in ("skip_after_loss", "half_after_loss") and rs[i - 1] < 0:
            size[i] = 0.0 if policy == "skip_after_loss" else 0.5
    return size


def chento_marks(asset: str, dates: list[str], btc30: pd.Series):
    path = ROOT / "studies" / "notebooks" / "overlay_study" / "results_backonly" / f"trades_{asset}.csv"
    t = pd.read_csv(path)
    t["ts"] = pd.to_datetime(t["ts"], utc=True)
    n_raw = len(t)
    t = t[np.isfinite(t["r_outcome"])].reset_index(drop=True)
    n_nan = n_raw - len(t)
    aligned = (((t.direction == "long") & (t.okx_delta_z >= 0))
               | ((t.direction == "short") & (t.okx_delta_z <= 0)))
    t = t[aligned].reset_index(drop=True)
    n_okx = int((~aligned).sum())
    r30 = np.array([btc30.get(ts.date().isoformat(), np.nan) for ts in t["ts"]])
    skip = (t.direction.values == "short") & (r30 > CHENTO_UP30_SKIP)
    t = t[~skip].reset_index(drop=True)
    n_up30 = int(skip.sum())
    sizes = tilt_sizes(t["r_outcome"].values.astype(float), CHENTO_TILT[asset])
    t["size"] = sizes
    t["ret_notional"] = t["size"] * t["r_outcome"] * (t["risk"] / t["entry"])
    t["dir"] = np.where(t.direction == "long", 1, -1)

    marks = pd.Series(0.0, index=dates)
    dirsum = pd.Series(0.0, index=dates)
    conc = pd.Series(0, index=dates, dtype=int)
    tif = timedelta(hours=CHENTO_TIF_H)
    for _, tr in t.iterrows():
        if tr["size"] == 0.0:
            continue
        t0 = tr["ts"].to_pydatetime()
        t1 = t0 + tif
        cur = t0
        while cur < t1:
            day_end = datetime.combine(cur.date(), datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
            seg_end = min(day_end, t1)
            frac = (seg_end - cur).total_seconds() / (CHENTO_TIF_H * 3600.0)
            d = cur.date().isoformat()
            if d in marks.index:
                marks[d] += tr["ret_notional"] * frac
                dirsum[d] += tr["dir"] * tr["size"]
                conc[d] += 1
            cur = seg_end
    traded = t[t["size"] > 0]
    info = dict(n_raw=n_raw, n_nan=n_nan, n_okx_dropped=n_okx, n_up30_skipped=n_up30,
                n_after_filters=len(t), n_traded=len(traded), n_half=int((t["size"] == 0.5).sum()),
                first=str(t["ts"].min())[:10], last=str(t["ts"].max())[:10],
                sum_ret_notional=float(traded["ret_notional"].sum()),
                mean_ret_notional=float(traded["ret_notional"].mean()),
                mean_risk_frac=float((traded["risk"] / traded["entry"]).mean()),
                sum_eff_r=float((traded["size"] * traded["r_outcome"]).sum()),
                max_conc=int(conc.max()), mean_conc_active=float(conc[conc > 0].mean()),
                days_conc_gt1=int((conc > 1).sum()))
    return marks, np.sign(dirsum).astype(int), info


# ─── carry (S-078 rule on daily funding sums) ────────────────────────────────

def carry_marks(dates: list[str]):
    since = int(datetime(2019, 9, 1, tzinfo=timezone.utc).timestamp())
    until = int(datetime.now(timezone.utc).timestamp())
    fsum = _funding.daily_sums_pct("BTC", since, until, complete_only=True)   # percent, unsigned
    fdays = sorted(fsum)
    # decision for day t uses complete funding days strictly before t (the live
    # sleeve excludes today); held on t → collects day t's funding sum.
    held = pd.Series(False, index=dates)
    open_ = False
    hist_vals: list[float] = []
    fi = 0
    entries = exits = 0
    for d in dates:
        while fi < len(fdays) and fdays[fi] < d:
            hist_vals.append(fsum[fdays[fi]]); fi += 1
        if len(hist_vals) < FR_WINDOW_DAYS:
            continue
        avg = sum(hist_vals[-FR_WINDOW_DAYS:]) / FR_WINDOW_DAYS
        streak = 0
        for v in reversed(hist_vals):
            if v < 0: streak += 1
            else: break
        entry_ok = avg > FR_ENTRY_THRESHOLD
        exit_trigger = streak >= EXIT_NEG_DAYS
        if open_ and exit_trigger:
            open_ = False; exits += 1
        elif (not open_) and entry_ok and not exit_trigger:
            open_ = True; entries += 1
        held[d] = open_
    ret = pd.Series(0.0, index=dates)
    for d in dates:
        if held[d]:
            ret[d] = fsum.get(d, 0.0) / 100.0
    # costs: half the round trip on the entry day, half on the last held day
    h = held.values
    half = ENTRY_EXIT_COST_PCT / 100.0 / 2.0
    n_seg = 0
    for i in range(len(dates)):
        if h[i] and (i == 0 or not h[i - 1]):
            ret.iloc[i] -= half; n_seg += 1
        if h[i] and (i == len(dates) - 1 or not h[i + 1]):
            ret.iloc[i] -= half
    missing_held = int(sum(1 for d in dates if held[d] and d not in fsum))
    zero_held = int(sum(1 for d in dates if held[d] and fsum.get(d, 0.0) == 0.0))
    info = dict(n_funding_days=len(fdays), first=fdays[0], last=fdays[-1], segments=n_seg,
                entries=entries, exits=exits, held_days=int(held.sum()), missing_held=missing_held,
                zero_held=zero_held, sum_pct=float(ret.sum() * 100))
    return ret, held, info


# ─── GOLD ────────────────────────────────────────────────────────────────────

def gold_series(con: sqlite3.Connection, dates: list[str]):
    rows = con.execute("SELECT timestamp, close FROM paxg_spot_1h WHERE close IS NOT NULL AND close > 0 "
                       "ORDER BY timestamp").fetchall()
    paxg_close: dict[str, float] = {}
    for ts, c in rows:
        paxg_close[datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()] = float(c)
    px = pd.Series(paxg_close).sort_index()
    px = px.reindex(pd.Index(sorted(set(px.index) | set(d for d in dates if d >= PAXG_START)))).ffill()
    paxg_ret = px.pct_change()

    mrows = con.execute("SELECT date, close FROM macro_daily WHERE symbol='GOLD' AND close IS NOT NULL "
                        "AND date >= '2019-11-01' ORDER BY date").fetchall()
    gc = pd.Series({d: float(c) for d, c in mrows}).sort_index()
    gc_ret = gc.pct_change()

    gold = pd.Series(0.0, index=dates)
    src = pd.Series(0, index=dates, dtype=int)   # 1 = GC=F proxy day
    for d in dates:
        if d > PAXG_START and d in paxg_ret.index and np.isfinite(paxg_ret[d]):
            gold[d] = paxg_ret[d]
        else:
            r = gc_ret.get(d, np.nan)
            gold[d] = r if np.isfinite(r) else 0.0
            src[d] = 1
    big = [(d, float(gold[d]), float(gc_ret.get(d, np.nan))) for d in dates
           if src[d] == 0 and abs(gold[d]) > 0.04]
    info = dict(paxg_days=len(paxg_close), paxg_first=min(paxg_close), paxg_last=max(paxg_close),
                proxy_days=int(src.sum()), proxy_last=[d for d in dates if src[d] == 1][-1],
                big_moves=big, gc_days=len(gc),
                paxg_total=float(np.prod(1 + gold[src == 0]) - 1),
                proxy_total=float(np.prod(1 + gold[src == 1]) - 1),
                paxg_vol=float(gold[src == 0].std() * math.sqrt(365)))
    return gold, src, info


# ─── main ────────────────────────────────────────────────────────────────────

def main() -> int:
    RESULTS.mkdir(exist_ok=True)
    t_all = time.time()
    note("# Panel notes — A1 anchor allocator study")
    note("")
    note(f"*Generated by `build_panel.py` on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
         "Every approximation in the panel is listed here with the number that bounds it.*")
    note("")
    note("## Build log")
    note("")

    con = ro_connect()
    lp = jplus_frame()
    end, short_days = full_day_end(con)
    note(f"- Period: {START} → {end} (latest full day = last date before today UTC with 24 spot bars). "
         f"In-period dates with < 24 spot bars: {short_days if short_days else 'none'}.")
    full_dates = [d for d in lp.index if d <= end]          # loop calendar incl. 2019 warmup
    dates = [d for d in full_dates if d >= START]
    cal = pd.date_range(START, end)
    missing_cal = [x.strftime("%Y-%m-%d") for x in cal if x.strftime("%Y-%m-%d") not in set(dates)]
    note(f"- Panel rows: {len(dates)} calendar days; days missing from the J+ calendar inside the period: "
         f"{len(missing_cal)} {missing_cal[:10]}.")

    # BTC B&H and BTC 30d (T-1) on the full loop calendar
    btc = lp.loc[full_dates, "btc_daily_pct"] / 100.0
    nav = (1.0 + btc).cumprod()
    btc30_full = (nav.shift(1) / nav.shift(31) - 1.0)
    btc30 = btc30_full.reindex(dates)
    assert btc30.notna().all(), "btc_30d has NaN inside the period"
    note(f"- `aux_btc_30d` = compounded trailing 30-day spot return through T−1 (no same-day look-ahead). "
         f"Share of days > +5 %: {(btc30 > 0.05).mean() * 100:.1f} %, < −10 %: {(btc30 < -0.10).mean() * 100:.1f} %, "
         f"> +10 %: {(btc30 > 0.10).mean() * 100:.1f} %.")

    panel = pd.DataFrame(index=pd.Index(dates, name="date"))
    panel["btc_bh"] = btc.reindex(dates).values

    # ── EMA 1W BTC ──
    ema_p = lp.loc[dates, "ema_p"].astype(int)
    ema = ema_p * panel["btc_bh"]
    flips = (ema_p != ema_p.shift(1).fillna(ema_p.iloc[0]).astype(int))
    ema = ema - flips.astype(float) * FLIP_COST
    panel["ema_1w_btc"] = ema.values
    panel["aux_ema_dir"] = ema_p.values
    note(f"- EMA 1W BTC: `ema_p × btc_daily`; {int(flips.sum())} position changes in-period, each charged "
         f"{FLIP_COST * 1e4:.0f} bp (a change 0→±1, ±1→∓1 or ±1→0 counts once). Days with ema_p = 0 (flat): "
         f"{int((ema_p == 0).sum())}; long {int((ema_p == 1).sum())}, short {int((ema_p == -1).sum())}.")

    # ── ETH full period ──
    eth_h, eth_d = eth_maps(con, "2019-12-31")
    eth_ret = pd.Series(0.0, index=dates)
    prev_map = {full_dates[i]: full_dates[i - 1] for i in range(1, len(full_dates))}
    n_eth_missing = 0
    for d in dates:
        p = prev_map.get(d)
        if p in eth_d and d in eth_d:
            eth_ret[d] = (eth_d[d][1] - eth_d[p][1]) / eth_d[p][1]
        else:
            n_eth_missing += 1
    # parity with the loop on its 3y window (skip its partial first days)
    loop_eth = lp.loc[dates, "eth_daily_pct"] / 100.0
    ok = [d for d in dates if d >= "2023-09-15" and loop_eth[d] != 0.0]
    eth_dev = float((eth_ret[ok] - loop_eth[ok]).abs().max()) if ok else float("nan")
    note(f"- ETH daily: rebuilt full-period from `eth_1m` ({n_eth_missing} in-period days without an ETH return, "
         f"set to 0). Parity vs the loop's `eth_daily_pct` on {len(ok)} overlapping days from 2023-09-15: "
         f"max |diff| = {eth_dev:.2e}.")

    # ── eth_daily sleeve ──
    mode = lp.loc[dates, "mode"]
    bull = mode.isin(BULL_MODES)
    eth_daily = eth_ret.where(bull, 0.0)
    enter = bull & ~bull.shift(1, fill_value=False).astype(bool)
    leave = bull & ~bull.shift(-1, fill_value=False).astype(bool)
    eth_daily = eth_daily - enter.astype(float) * FLIP_COST - leave.astype(float) * FLIP_COST
    panel["eth_daily"] = eth_daily.values
    note(f"- eth_daily: 1× ETH on days with mode ∈ {BULL_MODES} ({int(bull.sum())} days, {int(enter.sum())} regime "
         f"entries); 15 bp on the entry day and 15 bp on the last day of each bull segment (the exit fill happens at "
         f"the next open, booked on the last held day so inactive days stay exactly 0). Mode counts in-period: "
         f"{mode.value_counts().to_dict()}.")

    # ── R4 family ──
    gated = lp.loc[dates, "gated"].astype(bool)
    r4_lev = np.where(gated, R4_LEV_GATED, R4_LEV_UNGATED)
    panel["aux_r4_lev"] = r4_lev
    r4e_map = r4math.r4_eth_returns(eth_h)
    r4e2_map = r4math.r4_eth_v2_returns(eth_h)
    fired = {k: lp.loc[dates, f"{k}_fired"].astype(bool) for k in ("r4_btc", "r4_eth", "r4_btc_v2", "r4_eth_v2")}
    r4 = {}
    r4["r4_btc"] = lp.loc[dates, "r4_btc_pct"] / 100.0
    r4["r4_btc_v2"] = lp.loc[dates, "r4_btc_v2_pct"] / 100.0
    r4["r4_eth"] = pd.Series([r4e_map.get(d, 0.0) if fired["r4_eth"][d] else 0.0 for d in dates], index=dates) * r4_lev
    r4["r4_eth_v2"] = pd.Series([r4e2_map.get(d, 0.0) if fired["r4_eth_v2"][d] else 0.0 for d in dates], index=dates) * r4_lev
    # parity of the rebuilt ETH windows vs the loop on its window
    for k, col in (("r4_eth", "r4_eth_pct"), ("r4_eth_v2", "r4_eth_v2_pct")):
        lo = lp.loc[dates, col] / 100.0
        okd = [d for d in dates if d >= "2023-09-15" and lo[d] != 0.0]
        dev = float((r4[k][okd] - lo[okd]).abs().max()) if okd else float("nan")
        note(f"- {k}: rebuilt from full-period `eth_1m` with `r4math.{ 'r4_eth_returns' if k == 'r4_eth' else 'r4_eth_v2_returns'}`; "
             f"parity vs the loop's `{col}` on {len(okd)} fire days from 2023-09-15: max |diff| = {dev:.2e}.")
    r4_lines = []
    for k in ("r4_btc", "r4_eth", "r4_btc_v2", "r4_eth_v2"):
        s = r4[k].copy()
        traded = fired[k] & (s != 0.0)
        s = s - traded.astype(float) * FLIP_COST * r4_lev
        panel[k] = s.values
        r4_lines.append(f"{k}: {int(fired[k].sum())} calendar fires, {int(traded.sum())} with data "
                        f"(gross sum {r4[k].sum() * 100:+.1f} %, net {s.sum() * 100:+.1f} %)")
    note(f"- R4: loop values are GROSS window returns × inner leverage ({R4_LEV_UNGATED}× ungated / {R4_LEV_GATED}× gated; "
         f"gated on {int(gated.sum())} of {len(dates)} days); 15 bp × inner leverage charged per fire with data. "
         + "; ".join(r4_lines) + ".")

    # ── ADX ──
    adx, adx_dir, ai = adx_marks(dates)
    panel["adx"] = adx.values
    panel["aux_adx_dir"] = adx_dir.values
    note(f"- ADX (harness T2 = production config; candles end {ai['candles_end']}): {ai['n']} trades "
         f"({ai['n_long']} long / {ai['n_short']} short, {ai['still_open']} still open) {ai['first']} → {ai['last']}; "
         f"harness ret {ai['harness_ret_pct']:+.0f} %, maxDD {ai['harness_maxdd']:.1f} %, WR {ai['harness_wr']:.0f} %. "
         f"Daily marks = dir × close-to-close (exit day uses the harness exit price, {ADX_COST * 1e4:.0f} bp charged). "
         f"Parity: compounded marks vs harness gross return — longs max |dev| {ai['long_dev']:.2e} (exact); "
         f"shorts {ai['short_dev']:.2e} (daily-rebalanced short ≠ fixed-notional short; expected, second order). "
         f"In-period active days: {int((adx != 0).sum())}. Funding-crowding LONG veto (config FUNDING_VETO_Z) is not in "
         f"the harness — affects one known trade (2025-10-05).")

    # ── chento ──
    for asset, col in (("BTC", "chento_btc"), ("ETH", "chento_eth")):
        m, dsign, ci = chento_marks(asset, dates, btc30)
        panel[col] = m.values
        panel[f"aux_{col}_dir"] = dsign.values
        note(f"- {col}: backward-only pool {ci['n_raw']} rows ({ci['n_nan']} NaN r_outcome dropped) → OKX-alignment "
             f"dropped {ci['n_okx_dropped']}, BTC-30d>+10 % short-skip dropped {ci['n_up30_skipped']} → {ci['n_after_filters']} "
             f"trades {ci['first']} → {ci['last']}; tilt {CHENTO_TILT[asset]}: {ci['n_traded']} traded"
             f"{', ' + str(ci['n_half']) + ' at half size' if ci['n_half'] else ''}; Σ effective R {ci['sum_eff_r']:+.1f}. "
             f"Return on notional per trade = size × r_outcome × risk/entry (mean risk/entry {ci['mean_risk_frac'] * 100:.2f} %, "
             f"mean {ci['mean_ret_notional'] * 100:+.2f} %, Σ {ci['sum_ret_notional'] * 100:+.1f} %), spread over the UTC days "
             f"covered by [ts, ts+{CHENTO_TIF_H}h) in proportion to hours. Overlap: max {ci['max_conc']} concurrent, mean "
             f"{ci['mean_conc_active']:.2f} on active days, {ci['days_conc_gt1']} days with > 1 (stacked notional). "
             f"Active days: {int((m != 0).sum())}. The BTC-30d filter uses the daily T−1 value (production uses the 15m "
             f"series at trade time).")

    # ── carry ──
    cr, held, ki = carry_marks(dates)
    panel["carry"] = cr.values
    note(f"- carry: `daily_sums_pct('BTC')` {ki['n_funding_days']} complete funding days {ki['first']} → {ki['last']}; "
         f"rule: enter when the 7-day mean of daily funding > {FR_ENTRY_THRESHOLD} and no {EXIT_NEG_DAYS}-day negative streak, "
         f"exit after {EXIT_NEG_DAYS} consecutive negative days, decided each day on complete days before it. "
         f"{ki['segments']} holding segments, {ki['held_days']} held days ({ki['held_days'] / len(dates) * 100:.0f} %), "
         f"{ENTRY_EXIT_COST_PCT} % round trip split entry/last-held day; Σ net {ki['sum_pct']:+.1f} %. Held days with no funding "
         f"row: {ki['missing_held']}; held days with an exactly-zero sum (would read as inactive): {ki['zero_held']}. "
         f"Funding cadence change 2026-04-13 handled by the settlement filter in `daily_sums_pct`.")

    # ── GOLD / cash ──
    gold, gsrc, gi = gold_series(con, dates)
    panel["gold"] = gold.values
    panel["aux_gold_proxy"] = gsrc.values
    panel["cash"] = CASH_YIELD_DAILY
    note(f"- GOLD: PAXG daily close (last hourly bar of the UTC day, {gi['paxg_days']} days {gi['paxg_first']} → {gi['paxg_last']}) "
         f"close-to-close from {PAXG_START}; before that GC=F (`macro_daily` GOLD, {gi['proxy_days']} in-period proxy days "
         f"through {gi['proxy_last']}, 0 on non-trading days). PAXG total {gi['paxg_total'] * 100:+.0f} %, ann. vol "
         f"{gi['paxg_vol'] * 100:.1f} %; proxy stretch total {gi['proxy_total'] * 100:+.1f} %. PAXG days with |r| > 4 %: "
         f"{[(d, round(r * 100, 1), (round(g * 100, 1) if np.isfinite(g) else None)) for d, r, g in gi['big_moves']]} "
         f"(PAXG %, GC=F % same day).")
    note(f"- cash: {CASH_YIELD_DAILY * 365 * 100:.0f} %/365 per day, flat — an assumption (too high for 2020-21, "
         f"about right 2023-26).")
    note("- aux_mode / aux_gated come from the J+ loop (regime classifier and R4 vol gate, both T−1).")
    panel["aux_mode"] = mode.values
    panel["aux_gated"] = gated.astype(int).values
    panel["aux_btc_30d"] = btc30.values

    # ── short_squeeze: omitted ──
    note("- short_squeeze: OMITTED. `short_squeeze_sessions/strategy_backtest.ipynb` is a 51-cell pipeline (15m "
         "percentile signals on cd_futures_15m/cd_spot_15m, btc_1m stop walking, cooldown selection, walk-forward "
         "parameter choice) with no self-contained trade-list function; lifting it exceeds the ~1 h budget fixed in "
         "the README. It is active on ≤ 4 % of days (6 h holds), so its idle-capital footprint is negligible for "
         "this question.")

    # ── checks + write ──
    cols = ["btc_bh"] + TACTICAL + ANCHOR
    num = panel[cols + ["aux_btc_30d", "aux_r4_lev"]]
    assert not num.isna().any().any(), f"NaN in panel: {num.isna().sum()[num.isna().sum() > 0].to_dict()}"
    assert len(panel) >= 2400, len(panel)
    order = ["btc_bh"] + TACTICAL + ANCHOR + ["aux_btc_30d", "aux_mode", "aux_gated", "aux_r4_lev", "aux_ema_dir",
                                             "aux_adx_dir", "aux_chento_btc_dir", "aux_chento_eth_dir", "aux_gold_proxy"]
    panel = panel[order]
    panel.to_csv(RESULTS / "daily_panel.csv", float_format="%.10g")

    note("")
    note("## Panel summary (unit exposure, in-period)")
    note("")
    note("| sleeve | active days | active % | mean/active day | Σ return | ann. vol on active days |")
    note("|---|---|---|---|---|---|")
    act_any = pd.Series(False, index=panel.index)
    for s in TACTICAL + ANCHOR + ["btc_bh"]:
        x = panel[s]
        a = x != 0.0
        if s in TACTICAL:
            act_any |= a
        note(f"| {s} | {int(a.sum())} | {a.mean() * 100:.1f} | {x[a].mean() * 100:+.3f} % | {x.sum() * 100:+.1f} % | "
             f"{x[a].std() * math.sqrt(365) * 100:.1f} % |")
    caps = {"r4_btc": 6, "r4_eth": 6, "r4_btc_v2": 4, "r4_eth_v2": 4, "adx": 10, "carry": 8,
            "chento_btc": 8, "chento_eth": 6, "eth_daily": 6}
    active_caps = sum((panel[s] != 0.0).astype(float) * caps[s] for s in TACTICAL)
    n_active = sum((panel[s] != 0.0).astype(int) for s in TACTICAL)
    last90 = panel.index[-90:]
    note("")
    note(f"- Days with NO active tactical sleeve: {(n_active == 0).mean() * 100:.1f} % full period, "
         f"{(n_active.loc[last90] == 0).mean() * 100:.1f} % over the last 90 panel days (the fleet figure quoted in the "
         f"README was 46 % — the fleet runs a different sleeve set, so this is a cross-check, not a match).")
    note(f"- Σ active caps: mean {active_caps.mean():.1f} % of NAV, > 47 % budget on {(active_caps > 47).mean() * 100:.1f} % "
         f"of days (proportional scaling exercised), = 0 on {(active_caps == 0).mean() * 100:.1f} %. Idle before overflow "
         f"= max(0, 47 − Σ caps): mean {np.maximum(0, 47 - active_caps).mean():.1f} % of NAV.")
    note(f"- Active-count distribution: {n_active.value_counts().sort_index().to_dict()}.")
    note("")
    note("## Conventions")
    note("")
    note("- Unit return = daily P&L per unit of that day's notional (daily-reset convention, matching the allocator's "
         "daily weight reset). chento spreads a fixed-notional trade return over its 72 h window; carry is funding on "
         "notional; both are additive per day and agree with the daily-reset convention to second order.")
    note("- 'Active' = nonzero unit return that day (README interpretation 2). Exit-day costs are booked on the last "
         "held day so that inactive days are exactly 0.")
    note("- Directions (`aux_*_dir`) are the actual position sign for the net-BTC cap; carry is delta-neutral (0).")
    note(f"- prod.db opened `mode=ro` for every query in this file; the reused loaders (J+ loop, ADX harness, "
         f"`funding.daily_sums_pct`) open it with plain SELECT-only connections.")
    note("")
    note(f"Build time {time.time() - t_all:.0f}s. Panel: {len(panel)} rows × {len(panel.columns)} columns → "
         f"`results/daily_panel.csv`.")
    (RESULTS / "panel_notes.md").write_text("\n".join(NOTES) + "\n", encoding="utf-8")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

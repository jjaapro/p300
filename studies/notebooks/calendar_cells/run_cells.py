"""S-calendar-cells — run the five pre-registered calendar cells exactly once.

Read-only on prod.db. Deterministic. See README.md for the frozen
pre-registration; nothing here may be changed to chase a result.

Usage:
    python studies/notebooks/calendar_cells/run_cells.py
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from strategies.support import db                                  # noqa: E402
from strategies.support.indicators import ema as ema_calc          # noqa: E402
from strategies.support.ema_position import aggregate_weekly      # noqa: E402
from studies.lib.validation import bootstrap, dsr_pbo, metrics     # noqa: E402

# --- frozen constants (README section 2) -------------------------------------
COST = 0.0018                 # 18 bp round trip, research convention
N_TRIALS = 5
ERA_CUT = "2024-01-11"        # BTC spot ETF
SAMPLE_START = "2020-01-01"
SAMPLE_END = "2026-09-07"     # last complete UTC day
HIGH52_SIGNAL_START = "2021-01-01"
BOOT_ITER = 10000
BOOT_SEED = 42

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(exist_ok=True)

MS = 1000
MIN_MS = 60 * MS
HOUR_MS = 3600 * MS
DAY_MS = 86400 * MS


def con_ro() -> sqlite3.Connection:
    return sqlite3.connect("file:" + str(db.PROD_DB) + "?mode=ro", uri=True)


def ts_ms(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp()) * MS


def dt_ms(d: date, hh: int = 0, mm: int = 0) -> int:
    return ts_ms(d.year, d.month, d.day, hh, mm)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / MS, tz=timezone.utc).isoformat()


# --- price access -------------------------------------------------------------

def minute_opens(con: sqlite3.Connection, table: str, stamps) -> dict[int, float]:
    """{open_time_ms: open} for the requested exact minute bars. Missing bars
    are simply absent from the dict (README: skip, never fill)."""
    want = sorted(set(int(s) for s in stamps))
    out: dict[int, float] = {}
    CH = 400
    for i in range(0, len(want), CH):
        chunk = want[i:i + CH]
        q = ("SELECT open_time, open FROM %s WHERE open_time IN (%s)"
             % (table, ",".join("?" * len(chunk))))
        for ot, o in con.execute(q, chunk):
            if o is not None and float(o) > 0:
                out[int(ot)] = float(o)
    return out


def daily_closes(con: sqlite3.Connection, table: str) -> dict[str, float]:
    """{date_iso: close of that UTC day's last available minute bar}."""
    q = ("SELECT open_time / ? AS d, open_time, close FROM %s "
         "GROUP BY d HAVING open_time = MAX(open_time)" % table)
    out: dict[str, float] = {}
    for d, ot, c in con.execute(q, (DAY_MS,)):
        if c is None or float(c) <= 0:
            continue
        out[datetime.fromtimestamp(int(ot) / MS, tz=timezone.utc).date().isoformat()] = float(c)
    return out


def hourly_bars(con: sqlite3.Connection, table: str) -> list[tuple[int, float, float, float, float, float]]:
    """[(epoch_seconds, o, h, l, c, v)] hourly, aggregated from a 1m table.

    Hour open = first available minute's open, hour close = last available
    minute's close, high/low/volume over the hour. Feeds aggregate_weekly().
    """
    opens: dict[int, tuple[int, float]] = {}
    q1 = ("SELECT open_time / ? AS hb, open_time, open FROM %s "
          "GROUP BY hb HAVING open_time = MIN(open_time)" % table)
    for hb, ot, o in con.execute(q1, (HOUR_MS,)):
        if o is not None and float(o) > 0:
            opens[int(hb)] = (int(ot), float(o))

    closes: dict[int, float] = {}
    q2 = ("SELECT open_time / ? AS hb, open_time, close FROM %s "
          "GROUP BY hb HAVING open_time = MAX(open_time)" % table)
    for hb, ot, c in con.execute(q2, (HOUR_MS,)):
        if c is not None and float(c) > 0:
            closes[int(hb)] = float(c)

    hl: dict[int, tuple[float, float, float]] = {}
    q3 = ("SELECT open_time / ? AS hb, MAX(high), MIN(low), SUM(volume) FROM %s "
          "GROUP BY hb" % table)
    for hb, hi, lo, vol in con.execute(q3, (HOUR_MS,)):
        hl[int(hb)] = (float(hi or 0), float(lo or 0), float(vol or 0))

    bars = []
    for hb in sorted(opens):
        if hb not in closes or hb not in hl:
            continue
        hi, lo, vol = hl[hb]
        bars.append((hb * 3600, opens[hb][1], hi, lo, closes[hb], vol))
    return bars


# --- statistics ---------------------------------------------------------------

def basic(rs: list[float]) -> dict:
    n = len(rs)
    if n == 0:
        return dict(n=0, mean=None, sd=None, t=None, sharpe=None, wr=None, cum=None)
    mean = sum(rs) / n
    if n < 2:
        return dict(n=n, mean=mean, sd=None, t=None, sharpe=None,
                    wr=100.0 * (rs[0] > 0), cum=sum(rs))
    sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1))
    t = mean / (sd / math.sqrt(n)) if sd > 0 else None
    sr = mean / sd if sd > 0 else None
    return dict(n=n, mean=mean, sd=sd, t=t, sharpe=sr,
                wr=100.0 * sum(1 for r in rs if r > 0) / n, cum=sum(rs))


def evaluate(name: str, trades: list[dict], n_trials: int = N_TRIALS) -> dict:
    """Full-sample + era stats, DSR, bootstrap CI. Frozen decision clauses."""
    rs = [t["net"] for t in trades]
    dates = [t["entry_date"] for t in trades]
    full = basic(rs)

    eras = metrics.era_split(dates, rs, cutoff=ERA_CUT)
    pre, post = basic(eras["pre_etf"]), basic(eras["post_etf"])

    d = dsr_pbo.dsr_from_returns(rs, n_trials=n_trials) if len(rs) >= 2 else None
    b = bootstrap.bootstrap_sharpe(rs, n_iter=BOOT_ITER, seed=BOOT_SEED) if len(rs) >= 2 else None

    c1 = pre["t"] is not None and pre["t"] >= 2.5
    c2 = post["t"] is not None and post["t"] >= 2.5
    c3 = d is not None and d["dsr"] >= 0.95

    # Power context: what the cell would have had to deliver to clear each
    # clause at its own realised n and dispersion. Descriptive, not a clause.
    need_sr = bootstrap.dsr_required_sr(full["n"], n_trials) if full["n"] >= 2 else None
    need_mean_pre = (2.5 * pre["sd"] / math.sqrt(pre["n"])
                     if pre["n"] >= 2 and pre["sd"] else None)
    need_mean_post = (2.5 * post["sd"] / math.sqrt(post["n"])
                      if post["n"] >= 2 and post["sd"] else None)
    gross = basic([t["gross"] for t in trades])
    return dict(
        cell=name, n=full["n"], mean=full["mean"], mean_gross=gross["mean"],
        sd=full["sd"], t=full["t"],
        sharpe=full["sharpe"], wr=full["wr"], cum=full["cum"],
        n_pre=pre["n"], mean_pre=pre["mean"], t_pre=pre["t"], sharpe_pre=pre["sharpe"],
        n_post=post["n"], mean_post=post["mean"], t_post=post["t"], sharpe_post=post["sharpe"],
        dsr=(d["dsr"] if d else None), dsr_z=(d["dsr_z"] if d else None),
        dsr_n_trials=n_trials,
        skew=(d["skew"] if d else None), kurt=(d["kurt"] if d else None),
        boot_sr_p05=(b["sr_p05"] if b else None), boot_sr_p50=(b["sr_p50"] if b else None),
        boot_sr_p95=(b["sr_p95"] if b else None),
        sr_needed_for_dsr95=need_sr,
        mean_needed_pre_for_t2p5=need_mean_pre,
        mean_needed_post_for_t2p5=need_mean_post,
        C1_t_pre_ge_2p5=c1, C2_t_post_ge_2p5=c2, C3_dsr_ge_0p95=c3,
        PASS=bool(c1 and c2 and c3),
    )


def mk_trade(cell: str, entry_ms: int, exit_ms: int, side: int,
             p_in: float, p_out: float, note: str = "") -> dict:
    gross = side * (p_out / p_in - 1.0)
    return dict(cell=cell,
                entry_date=datetime.fromtimestamp(entry_ms / MS, tz=timezone.utc).date().isoformat(),
                entry_ts=iso(entry_ms), exit_ts=iso(exit_ms), side=side,
                p_entry=p_in, p_exit=p_out, gross=gross, net=gross - COST, note=note)


# --- CELL 1: turn-of-quarter, BTC long, last day of Q 00:00 -> Q+1 day 3 00:00 -

def cell_qtr_end(con) -> tuple[list[dict], int]:
    ends: list[date] = []
    for y in range(2020, 2027):
        for m in (3, 6, 9, 12):
            last = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
            if SAMPLE_START <= last.isoformat() <= SAMPLE_END:
                ends.append(last)
    stamps = []
    for d in ends:
        stamps.append(dt_ms(d, 0, 0))
        stamps.append(dt_ms(d + timedelta(days=3), 0, 0))
    px = minute_opens(con, "btc_1m", stamps)
    trades, skipped = [], 0
    for d in ends:
        a, b = dt_ms(d, 0, 0), dt_ms(d + timedelta(days=3), 0, 0)
        if a not in px or b not in px:
            skipped += 1
            continue
        trades.append(mk_trade("QTR_END", a, b, +1, px[a], px[b]))
    return trades, skipped


# --- CELL 2: NFP, BTC long ----------------------------------------------------

def nfp_dates(con) -> list[date]:
    rows = con.execute(
        "SELECT date FROM scheduled_events WHERE event_type='NFP' ORDER BY date"
    ).fetchall()
    out = []
    for (d,) in rows:
        if SAMPLE_START <= d <= SAMPLE_END:
            out.append(date.fromisoformat(d))
    return out


def cell_nfp(con, dates: list[date], h_in: int, m_in: int, h_out: int, m_out: int,
             name: str) -> tuple[list[dict], int]:
    stamps = []
    for d in dates:
        stamps.append(dt_ms(d, h_in, m_in))
        stamps.append(dt_ms(d, h_out, m_out))
    px = minute_opens(con, "btc_1m", stamps)
    trades, skipped = [], 0
    for d in dates:
        a, b = dt_ms(d, h_in, m_in), dt_ms(d, h_out, m_out)
        if a not in px or b not in px:
            skipped += 1
            continue
        trades.append(mk_trade(name, a, b, +1, px[a], px[b]))
    return trades, skipped


# --- CELL 3: weekend gap fade, BTC -------------------------------------------

def cell_wknd_fade(con) -> tuple[list[dict], int]:
    d = date.fromisoformat(SAMPLE_START)
    end = date.fromisoformat(SAMPLE_END)
    fridays = []
    while d <= end:
        if d.weekday() == 4:            # Friday
            fridays.append(d)
        d += timedelta(days=1)
    stamps = []
    for f in fridays:
        stamps += [dt_ms(f, 21), dt_ms(f + timedelta(days=2), 22),
                   dt_ms(f + timedelta(days=3), 22)]
    px = minute_opens(con, "btc_1m", stamps)
    trades, skipped = [], 0
    for f in fridays:
        a = dt_ms(f, 21)                              # Fri 21:00 UTC
        b = dt_ms(f + timedelta(days=2), 22)          # Sun 22:00 UTC = entry
        c = dt_ms(f + timedelta(days=3), 22)          # Mon 22:00 UTC = exit
        if a not in px or b not in px or c not in px:
            skipped += 1
            continue
        gap = px[b] / px[a] - 1.0
        if gap == 0.0:
            skipped += 1
            continue
        side = -1 if gap > 0 else +1
        trades.append(mk_trade("WKND_FADE", b, c, side, px[b], px[c],
                               note="gap=%.6f" % gap))
    return trades, skipped


# --- CELL 4: new 52-week high overlay, BTC long ------------------------------

def cell_high52(con) -> tuple[list[dict], int, dict]:
    closes = daily_closes(con, "btc_1m")
    days = sorted(d for d in closes if SAMPLE_START <= d <= SAMPLE_END)
    idx = {d: i for i, d in enumerate(days)}
    signals = []
    for i, d in enumerate(days):
        if d < HIGH52_SIGNAL_START:
            continue
        lo = i - 364
        if lo < 0:
            continue
        prior = [closes[x] for x in days[lo:i]]
        if len(prior) < 364:
            continue
        if closes[d] > max(prior):
            signals.append(d)

    # non-overlap: entry D+1 00:00, exit D+6 00:00
    stamps = []
    for d in signals:
        D = date.fromisoformat(d)
        stamps += [dt_ms(D + timedelta(days=1)), dt_ms(D + timedelta(days=6))]
    px = minute_opens(con, "btc_1m", stamps)

    trades, skipped, blocked = [], 0, 0
    open_until: date | None = None
    for d in signals:
        D = date.fromisoformat(d)
        e_in, e_out = D + timedelta(days=1), D + timedelta(days=6)
        if open_until is not None and e_in < open_until:
            blocked += 1
            continue
        a, b = dt_ms(e_in), dt_ms(e_out)
        if a not in px or b not in px:
            skipped += 1
            continue
        trades.append(mk_trade("HIGH52", a, b, +1, px[a], px[b]))
        open_until = e_out
    info = dict(n_signals=len(signals), n_blocked_by_overlap=blocked,
                first_signal=(signals[0] if signals else None),
                last_signal=(signals[-1] if signals else None),
                n_daily_closes=len(days))
    return trades, skipped, info


# --- CELL 5: weekly EMA(5/21) cross on ETH, long/short -----------------------

def cell_ema_eth(con, fast: int = 5, slow: int = 21) -> tuple[list[dict], int, dict]:
    bars = hourly_bars(con, "eth_1m")
    weekly = aggregate_weekly(bars)          # [(dt_iso, o, h, l, c)]
    closes = [w[4] for w in weekly]
    fe, se = ema_calc(closes, fast), ema_calc(closes, slow)

    trades: list[dict] = []
    position: str | None = None
    entry_idx: int | None = None
    warmup = slow + 1
    open_trade = None

    def close_trade(side: int, i_entry: int, i_exit: int) -> dict:
        p_in, p_out = weekly[i_entry][1], weekly[i_exit][1]
        a = int(datetime.strptime(weekly[i_entry][0], "%Y-%m-%d")
                .replace(tzinfo=timezone.utc).timestamp()) * MS
        b = int(datetime.strptime(weekly[i_exit][0], "%Y-%m-%d")
                .replace(tzinfo=timezone.utc).timestamp()) * MS
        return mk_trade("EMA_ETH", a, b, side, p_in, p_out,
                        note="wk %d->%d" % (i_entry, i_exit))

    for i in range(warmup, len(weekly) - 1):
        if math.isnan(fe[i]) or math.isnan(se[i]):
            continue
        if math.isnan(fe[i - 1]) or math.isnan(se[i - 1]):
            continue
        fa, fa_prev = fe[i] > se[i], fe[i - 1] > se[i - 1]
        cross_up, cross_down = fa and not fa_prev, (not fa) and fa_prev

        if position == "long" and cross_down and entry_idx is not None:
            trades.append(close_trade(+1, entry_idx, i + 1))
            position, entry_idx = None, None
        elif position == "short" and cross_up and entry_idx is not None:
            trades.append(close_trade(-1, entry_idx, i + 1))
            position, entry_idx = None, None

        if cross_up and position is None:
            position, entry_idx = "long", i + 1
        elif cross_down and position is None:
            position, entry_idx = "short", i + 1

    if position is not None and entry_idx is not None:
        open_trade = dict(side=position, entry_week=weekly[entry_idx][0])

    info = dict(n_weekly_bars=len(weekly), n_hourly_bars=len(bars),
                first_week=weekly[0][0] if weekly else None,
                last_week=weekly[-1][0] if weekly else None,
                excluded_open_trade=open_trade)
    return trades, 0, info


# --- monthly grid + PBO -------------------------------------------------------

def monthly_grid(cells: dict[str, list[dict]], start="2021-01", end="2026-08"):
    months = []
    y, m = int(start[:4]), int(start[5:7])
    while "%04d-%02d" % (y, m) <= end:
        months.append("%04d-%02d" % (y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    names = list(cells)
    grid = []
    for mo in months:
        grid.append([sum(t["net"] for t in cells[c] if t["entry_date"][:7] == mo)
                     for c in names])
    return months, names, grid


# --- main ---------------------------------------------------------------------

def main() -> None:
    con = con_ro()
    try:
        print("prod.db:", db.PROD_DB)

        qtr, sk_qtr = cell_qtr_end(con)

        nfps = nfp_dates(con)
        n_fri = sum(1 for d in nfps if d.weekday() == 4)
        n_dom14 = sum(1 for d in nfps if d.day <= 14)
        nfp_std, sk_std = cell_nfp(con, nfps, 12, 30, 16, 0, "NFP_STANDALONE")
        nfp_inc, sk_inc = cell_nfp(con, nfps, 14, 0, 16, 0, "NFP_INCREMENTAL")
        # descriptive decomposition (NOT pre-registered, NOT decision-bearing)
        nfp_r4, sk_r4 = cell_nfp(con, nfps, 12, 30, 14, 0, "NFP_R4_COVERED")

        wknd, sk_wk = cell_wknd_fade(con)
        h52, sk_h52, h52_info = cell_high52(con)
        ema, sk_ema, ema_info = cell_ema_eth(con)

        cells = {"QTR_END": qtr, "NFP_INCREMENTAL": nfp_inc,
                 "WKND_FADE": wknd, "HIGH52": h52, "EMA_ETH": ema}
        skipped = {"QTR_END": sk_qtr, "NFP_INCREMENTAL": sk_inc,
                   "WKND_FADE": sk_wk, "HIGH52": sk_h52, "EMA_ETH": sk_ema,
                   "NFP_STANDALONE": sk_std, "NFP_R4_COVERED": sk_r4}

        rows = [evaluate(k, v) for k, v in cells.items()]
        ctx = [evaluate("NFP_STANDALONE", nfp_std),
               evaluate("NFP_R4_COVERED", nfp_r4)]

        # --- NFP sensitivities ------------------------------------------------
        SHUTDOWN = {"2025-10-03", "2025-11-07", "2025-12-05"}
        nfp_inc_clean = [t for t in nfp_inc if t["entry_date"] not in SHUTDOWN]
        nfp_std_clean = [t for t in nfp_std if t["entry_date"] not in SHUTDOWN]
        sens = dict(
            dropped_dates=sorted(SHUTDOWN),
            n_dropped_incremental=len(nfp_inc) - len(nfp_inc_clean),
            incremental_shutdown_excluded=evaluate("NFP_INCREMENTAL_ex_shutdown",
                                                   nfp_inc_clean),
            standalone_shutdown_excluded=evaluate("NFP_STANDALONE_ex_shutdown",
                                                  nfp_std_clean),
            incremental_dsr_n_trials_6=evaluate("NFP_INCREMENTAL", nfp_inc, n_trials=6),
            standalone_dsr_n_trials_6=evaluate("NFP_STANDALONE", nfp_std, n_trials=6),
            nfp_dates_in_sample=len(nfps),
            nfp_all_fridays=(n_fri == len(nfps)),
            nfp_all_dom_le_14=(n_dom14 == len(nfps)),
            nfp_all_covered_by_r4_v2_friday=(n_fri == len(nfps) and n_dom14 == len(nfps)),
        )

        months, names, grid = monthly_grid(cells)
        pbo, logits = dsr_pbo.cscv_pbo(grid, s=10)

        # --- POST-HOC ADDENDUM (NOT pre-registered, NOT decision-bearing) ------
        # Every cell had a frozen direction. Where a frozen direction produced a
        # significantly NEGATIVE t, the mechanically-implied opposite direction
        # is reported here purely so the reader can see the magnitude. Flipping a
        # direction after seeing the sign is exactly the error this study exists
        # to avoid: these rows are observations, not candidates, and they change
        # no verdict. Note the flip is NOT a sign change of the net return -- the
        # cost is paid either way: net_flip = -gross - COST.
        post_hoc = {}
        for nm, trs in cells.items():
            flipped = [dict(tr, side=-tr["side"], gross=-tr["gross"],
                            net=-tr["gross"] - COST) for tr in trs]
            post_hoc[nm + "_direction_flipped"] = evaluate(nm + "_FLIP", flipped)

        # --- write artefacts ---------------------------------------------------
        for name, trs in list(cells.items()) + [("NFP_STANDALONE", nfp_std),
                                                ("NFP_R4_COVERED", nfp_r4)]:
            p = OUT / ("trades_%s.csv" % name.lower())
            with p.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(trs[0].keys()) if trs else
                                   ["cell", "entry_date", "entry_ts", "exit_ts", "side",
                                    "p_entry", "p_exit", "gross", "net", "note"])
                w.writeheader()
                w.writerows(trs)

        fields = list(rows[0].keys())
        with (OUT / "cells_summary.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows + ctx)

        with (OUT / "cells_summary.json").open("w", encoding="utf-8") as fh:
            json.dump(dict(
                generated=datetime.now(timezone.utc).isoformat(),
                frozen=dict(cost=COST, n_trials=N_TRIALS, era_cut=ERA_CUT,
                            sample_start=SAMPLE_START, sample_end=SAMPLE_END,
                            high52_signal_start=HIGH52_SIGNAL_START,
                            boot_iter=BOOT_ITER, boot_seed=BOOT_SEED),
                decision_cells=rows, context_cells=ctx,
                skipped_observations=skipped,
                high52_info=h52_info, ema_eth_info=ema_info,
            ), fh, indent=2, default=str)

        with (OUT / "nfp_sensitivity.json").open("w", encoding="utf-8") as fh:
            json.dump(sens, fh, indent=2, default=str)

        with (OUT / "post_hoc_notes.json").open("w", encoding="utf-8") as fh:
            json.dump(dict(
                warning=("POST-HOC. Not pre-registered. Not decision-bearing. "
                         "Directions were frozen in README before running; these "
                         "flipped-direction rows exist only to show magnitude and "
                         "cannot rescue or create a PASS."),
                flipped=post_hoc), fh, indent=2, default=str)

        with (OUT / "pbo.json").open("w", encoding="utf-8") as fh:
            json.dump(dict(pbo=pbo, n_combos=len(logits), s=10,
                           months=months, cells=names,
                           note="monthly net-return grid, flat months = 0.0"),
                      fh, indent=2)

        with (OUT / "monthly_grid.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["month"] + names)
            for mo, row in zip(months, grid):
                w.writerow([mo] + ["%.6f" % v for v in row])

        # --- console ----------------------------------------------------------
        def f(x, p=4):
            return "n/a" if x is None else ("%.*f" % (p, x))

        print("\n=== DECISION CELLS (18 bp round trip, N_TRIALS=%d) ===" % N_TRIALS)
        hdr = ("%-16s %5s %9s %7s %8s %7s | %5s %8s %7s | %5s %8s %7s | %6s %s"
               % ("cell", "n", "mean%", "t", "sharpe", "DSR",
                  "npre", "mean%", "t_pre", "npost", "mean%", "t_pst", "PASS", "clauses"))
        print(hdr)
        print("-" * len(hdr))
        for r in rows + ctx:
            print("%-16s %5d %9s %7s %8s %7s | %5d %8s %7s | %5d %8s %7s | %6s %s"
                  % (r["cell"], r["n"],
                     f(None if r["mean"] is None else r["mean"] * 100, 3), f(r["t"], 2),
                     f(r["sharpe"], 3), f(r["dsr"], 3),
                     r["n_pre"], f(None if r["mean_pre"] is None else r["mean_pre"] * 100, 3),
                     f(r["t_pre"], 2),
                     r["n_post"], f(None if r["mean_post"] is None else r["mean_post"] * 100, 3),
                     f(r["t_post"], 2),
                     "YES" if r["PASS"] else "no",
                     "C1=%s C2=%s C3=%s" % (int(r["C1_t_pre_ge_2p5"]),
                                            int(r["C2_t_post_ge_2p5"]),
                                            int(r["C3_dsr_ge_0p95"]))))
        print("\nskipped observations:", skipped)
        print("HIGH52:", h52_info)
        print("EMA_ETH:", ema_info)
        print("NFP dates in sample: %d  all Fridays=%s  all dom<=14=%s  => all inside R4 V2 Fri leg=%s"
              % (sens["nfp_dates_in_sample"], sens["nfp_all_fridays"],
                 sens["nfp_all_dom_le_14"], sens["nfp_all_covered_by_r4_v2_friday"]))
        print("PBO (CSCV, monthly grid, 5 cells): %.3f over %d combos" % (pbo, len(logits)))
        for r in rows + ctx:
            print("  boot sr 90%% CI %-16s [%s, %s] median %s"
                  % (r["cell"], f(r["boot_sr_p05"], 3), f(r["boot_sr_p95"], 3),
                     f(r["boot_sr_p50"], 3)))
        print("\nNFP sensitivity (context only):")
        for k in ("incremental_shutdown_excluded", "standalone_shutdown_excluded",
                  "incremental_dsr_n_trials_6", "standalone_dsr_n_trials_6"):
            r = sens[k]
            print("  %-34s n=%d mean%%=%s t=%s DSR=%s"
                  % (k, r["n"], f(None if r["mean"] is None else r["mean"] * 100, 3),
                     f(r["t"], 2), f(r["dsr"], 3)))
        print("\nPOWER CONTEXT — what each cell needed (descriptive, not a clause):")
        for r in rows:
            print("  %-16s realised SR %s vs SR needed for DSR>=0.95 %s | "
                  "mean/trade needed for t>=2.5: pre %s%% (got %s%%), post %s%% (got %s%%)"
                  % (r["cell"], f(r["sharpe"], 3), f(r["sr_needed_for_dsr95"], 3),
                     f(None if r["mean_needed_pre_for_t2p5"] is None
                       else r["mean_needed_pre_for_t2p5"] * 100, 3),
                     f(None if r["mean_pre"] is None else r["mean_pre"] * 100, 3),
                     f(None if r["mean_needed_post_for_t2p5"] is None
                       else r["mean_needed_post_for_t2p5"] * 100, 3),
                     f(None if r["mean_post"] is None else r["mean_post"] * 100, 3)))

        print("\nPOST-HOC (not pre-registered, not decision-bearing):")
        for k, r in post_hoc.items():
            print("  %-32s n=%d mean%%=%s t=%s t_pre=%s t_post=%s DSR=%s"
                  % (k, r["n"], f(None if r["mean"] is None else r["mean"] * 100, 3),
                     f(r["t"], 2), f(r["t_pre"], 2), f(r["t_post"], 2), f(r["dsr"], 3)))
        print("\nartefacts ->", OUT)
    finally:
        con.close()


if __name__ == "__main__":
    main()

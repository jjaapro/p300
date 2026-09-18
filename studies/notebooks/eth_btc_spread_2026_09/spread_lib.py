"""ETH/BTC regime spread (Part A) and the same hedge on chento's trades (Part B). README.md is the pre-registration.

Reads prod.db read-only (btc_1m, eth_1m, ca_long_short_ratio), the cached Binance settlement prints, the exit-policy
study's stage 1 walks and the ORB perpetual panels. Nothing under bots/ or strategies/ is touched; the production
regime classifier is imported unchanged.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in (ROOT, ROOT / "studies" / "notebooks" / "exit_policy_2026_09"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from strategies.support import regime_jplus as regime  # noqa: E402
from studies.lib.validation.dsr_pbo import dsr_from_returns  # noqa: E402
import micro_lib as M  # noqa: E402

RESULTS = HERE / "results"
PROD_DB = ROOT / "data" / "databases" / "prod.db"
FUNDING_CACHE = ROOT / "studies" / "notebooks" / "brainstorm_validation_2026_09" / "cache"
STAGE1_TRADES = ROOT / "studies" / "notebooks" / "exit_policy_2026_09" / "results" / "microstructure" / "trades.csv.gz"
SYMBOL = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
OTHER = {"BTC": "ETH", "ETH": "BTC"}

# --- frozen numbers (README) --------------------------------------------------------------------------
START_DAY = "2020-01-01"
LEG_RT_COST = 0.0010                 # 10 bp per leg per round trip, the execution study's measured taker RT
PAIR_COST = 2 * LEG_RT_COST          # both legs of the spread
MIN_NET_ANN_PCT = 5.0                # decision (a)
DSR_BAR = 0.95                       # decision (c)
PLACEBO_ALPHA = 0.05                 # decision (d)
HEDGE_COST_BUDGET_R = -0.05          # Part B: paired mean difference must be >= this
N_BOOT_EPISODES, N_PLACEBO, SEED = 5_000, 1_000, 42
SPLIT_DAY = "2023-06-09"             # the pool study's window start, reported
DAYS_PER_YEAR = 365.25


# --- data -----------------------------------------------------------------------------------------------

def load_daily(asset: str, end_day: str | None = None) -> pd.DataFrame:
    """UTC daily open (first present minute's open) and close (last present minute's close) from the spot minute
    table, from START_DAY to end_day inclusive (default: the last complete UTC day in the table)."""
    table = f"{asset.lower()}_1m"
    con = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            f"SELECT open_time, open, close FROM {table} WHERE open_time >= ? AND "
            f"((open_time / 60000) % 1440 <= 9 OR (open_time / 60000) % 1440 >= 1430) ORDER BY open_time",
            (int(datetime.fromisoformat(START_DAY).replace(tzinfo=timezone.utc).timestamp() * 1000),)).fetchall()
    finally:
        con.close()
    df = pd.DataFrame(rows, columns=["open_time", "open", "close"])
    df["day"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    df = df[np.isfinite(df["close"]) & np.isfinite(df["open"])]
    g = df.groupby("day")
    out = pd.DataFrame({"open": g["open"].first(), "close": g["close"].last(), "last_minute": g["open_time"].last()})
    if end_day is None:
        today = datetime.now(timezone.utc).date().isoformat()
        out = out[out.index < today]
    else:
        out = out[out.index <= end_day]
    return out


def load_ls_ratio() -> dict[str, float]:
    con = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT timestamp, long_pct FROM ca_long_short_ratio WHERE asset='BTC' "
                           "ORDER BY timestamp").fetchall()
    finally:
        con.close()
    return {datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat(): float(v)
            for ts, v in rows if v is not None}


def load_funding(symbol: str) -> pd.Series:
    """Settlement prints [(ms, rate)] from the cache file, as a Series indexed by UTC timestamp (seconds)."""
    d = json.loads((FUNDING_CACHE / f"binance_funding_{symbol}.json").read_text(encoding="utf-8"))
    ts = np.array([int(t) // 1000 for t, _ in d["rows"]], dtype=np.int64)
    rate = np.array([float(r) for _, r in d["rows"]], dtype=float)
    return pd.Series(rate, index=ts).sort_index()


def classify(days: list[str], btc_close: list[float], ls_d: dict[str, float]) -> dict[str, str]:
    """The production classifier, batch mode: {day: mode} from days[1:]."""
    return regime.classify_series(days, btc_close, ls_d)


# --- Part A: episodes and P&L -----------------------------------------------------------------------------

def episodes_from_modes(days: list[str], modes: dict[str, str], target: tuple[str, ...]) -> list[tuple[int, int]]:
    """Maximal runs of consecutive days whose mode is in `target`, as (first_index, last_index) inclusive."""
    out, start = [], None
    for i, d in enumerate(days):
        on = modes.get(d) in target
        if on and start is None:
            start = i
        if not on and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(days) - 1))
    return out


def day_ts(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


def _settlements(fund: pd.Series, day_starts: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(timestamps, rates, day index of each settlement on the study's day grid; -1 outside it)."""
    if len(fund) == 0:
        return np.zeros(0, np.int64), np.zeros(0), np.zeros(0, np.int64)
    ts = fund.index.to_numpy(np.int64)
    di = np.searchsorted(day_starts, ts, side="right") - 1
    di = np.where((di >= 0) & (ts < day_starts[-1] + 86400), di, -1)
    return ts, fund.to_numpy(float), di


def run_episodes(days: list[str], eth: pd.DataFrame, btc: pd.DataFrame, eps: list[tuple[int, int]],
                 f_btc: pd.Series, f_eth: pd.Series, legs: tuple[float, float] = (1.0, -1.0),
                 cost: float = PAIR_COST) -> tuple[pd.DataFrame, pd.Series]:
    """Per-episode P&L (fractions of capital) and the additive daily equity path over `days`.

    legs = (w_eth, w_btc): +1 long, -1 short, 0 absent. Entry at the open of the first day, exit at the open of the
    day after the last (or the last day's close when censored). Funding: each settlement at or after entry and
    before exit pays w · rate per unit notional (a long pays a positive rate). Daily marks are price changes in
    units of the entry price, so the daily path sums exactly to the episode's simple return."""
    w_eth, w_btc = legs
    n = len(days)
    eo, ec = eth["open"].to_numpy(float), eth["close"].to_numpy(float)
    bo, bc = btc["open"].to_numpy(float), btc["close"].to_numpy(float)
    day_starts = np.array([day_ts(d) for d in days], dtype=np.int64)
    fe_ts, fe_r, fe_di = _settlements(f_eth, day_starts)
    fb_ts, fb_r, fb_di = _settlements(f_btc, day_starts)
    daily = np.zeros(n)
    rows = []
    for a, b in eps:
        censored = b == n - 1
        e0, b0 = eo[a], bo[a]
        if censored:
            e1, b1 = ec[b], bc[b]
            t_exit = day_starts[b] + 86400
            exit_day = days[b]
        else:
            e1, b1 = eo[b + 1], bo[b + 1]
            t_exit = day_starts[b + 1]
            exit_day = days[b + 1]
        t_entry = day_starts[a]
        gross = w_eth * (e1 / e0 - 1) + w_btc * (b1 / b0 - 1)
        funding = 0.0
        for ts, r, di, w in ((fe_ts, fe_r, fe_di, -w_eth), (fb_ts, fb_r, fb_di, -w_btc)):
            if w == 0 or len(ts) == 0:
                continue
            lo, hi = np.searchsorted(ts, t_entry, side="left"), np.searchsorted(ts, t_exit, side="left")
            funding += w * float(r[lo:hi].sum())
            for k in range(lo, hi):
                if di[k] >= 0:
                    daily[di[k]] += w * r[k]
        net = gross + funding - cost
        rows.append({"entry_day": days[a], "exit_day": exit_day, "days": b - a + 1, "censored": censored,
                     "gross": gross, "funding": funding, "cost": cost, "net": net,
                     "eth_move": e1 / e0 - 1, "btc_move": b1 / b0 - 1})
        prev_e, prev_b = e0, b0
        for i in range(a, b + 1):
            daily[i] += w_eth * (ec[i] - prev_e) / e0 + w_btc * (bc[i] - prev_b) / b0
            prev_e, prev_b = ec[i], bc[i]
        if not censored:
            daily[b + 1] += w_eth * (e1 - prev_e) / e0 + w_btc * (b1 - prev_b) / b0
        daily[a] -= cost
    cols = ["entry_day", "exit_day", "days", "censored", "gross", "funding", "cost", "net", "eth_move", "btc_move"]
    return pd.DataFrame(rows, columns=cols), pd.Series(daily, index=days)


def max_drawdown(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(np.concatenate([[0.0], equity]))
    return float((peak[1:] - equity).max()) if len(equity) else 0.0


def _held_mask(ep: pd.DataFrame, days: list[str]) -> np.ndarray:
    pos = {d: i for i, d in enumerate(days)}
    held = np.zeros(len(days), dtype=bool)
    for r in ep.itertuples(index=False):
        a = pos[r.entry_day]
        held[a:a + int(r.days)] = True
    return held


def summarize(ep: pd.DataFrame, daily: pd.Series, years: float) -> dict:
    days = list(daily.index)
    held = _held_mask(ep, days)
    eq = daily.cumsum().to_numpy()
    out = {"episodes": int(len(ep)), "days_held": int(ep["days"].sum()) if len(ep) else 0,
           "share_held": float(ep["days"].sum() / len(daily)) if len(ep) else 0.0,
           "censored": int(ep["censored"].sum()) if len(ep) else 0,
           "gross_total_pct": float(ep["gross"].sum() * 100) if len(ep) else 0.0,
           "funding_total_pct": float(ep["funding"].sum() * 100) if len(ep) else 0.0,
           "cost_total_pct": float(ep["cost"].sum() * 100) if len(ep) else 0.0,
           "net_total_pct": float(ep["net"].sum() * 100) if len(ep) else 0.0,
           "net_ann_pct": float(ep["net"].sum() * 100 / years) if len(ep) else 0.0,
           "max_dd_pct": max_drawdown(eq) * 100, "years": years}
    out["mar"] = (out["net_ann_pct"] / out["max_dd_pct"]) if out["max_dd_pct"] > 0 else None
    if len(ep) > 1:
        v = ep["net"].to_numpy(float)
        sd = v.std(ddof=1)
        out["t_episodes"] = float(v.mean() / (sd / math.sqrt(len(v)))) if sd > 0 else None
        out["win_rate"] = float((v > 0).mean())
        out["mean_episode_net_pct"] = float(v.mean() * 100)
        out["median_days"] = float(ep["days"].median())
    hd = daily.to_numpy(float)[held]
    if len(hd) > 2 and hd.std(ddof=1) > 0:
        out["sharpe_held_days_ann"] = float(hd.mean() / hd.std(ddof=1) * math.sqrt(365))
        d = dsr_from_returns(hd, n_trials=1, periods_per_year=365)
        out["dsr"] = None if d is None else float(d["dsr"])
        out["dsr_detail"] = d
    else:
        out["sharpe_held_days_ann"] = None
        out["dsr"] = None
    return out


def per_year(ep: pd.DataFrame) -> dict:
    if not len(ep):
        return {}
    y = ep["entry_day"].str[:4]
    return {k: {"episodes": int(len(g)), "net_pct": float(g["net"].sum() * 100), "gross_pct": float(g["gross"].sum() * 100),
                "days": int(g["days"].sum())} for k, g in ep.groupby(y)}


def halves(ep: pd.DataFrame) -> dict:
    n = len(ep)
    if n < 2:
        return {"first": None, "second": None, "first_n": n, "second_n": 0}
    h = -(-n // 2)
    return {"first": float(ep["net"].iloc[:h].sum() * 100), "second": float(ep["net"].iloc[h:].sum() * 100),
            "first_n": h, "second_n": n - h}


def split_at(ep: pd.DataFrame, day: str = SPLIT_DAY) -> dict:
    before, after = ep[ep["entry_day"] < day], ep[ep["entry_day"] >= day]
    return {"before": float(before["net"].sum() * 100), "before_n": int(len(before)),
            "after": float(after["net"].sum() * 100), "after_n": int(len(after))}


def bootstrap_net_ann(ep: pd.DataFrame, years: float, n_boot: int = N_BOOT_EPISODES, seed: int = SEED) -> dict:
    """Resample episodes with replacement: CI90 of net %/yr."""
    if len(ep) < 2:
        return {"ci90_ann_pct": [None, None]}
    rng = np.random.default_rng(seed)
    v = ep["net"].to_numpy(float)
    draws = rng.choice(v, size=(n_boot, len(v)), replace=True).sum(axis=1) * 100 / years
    return {"ci90_ann_pct": [float(np.quantile(draws, 0.05)), float(np.quantile(draws, 0.95))],
            "mean_ann_pct": float(draws.mean())}


def placebo_episodes(days: list[str], eps: list[tuple[int, int]], rng: np.random.Generator) -> list[tuple[int, int]]:
    """The same episode lengths per calendar year at random non-overlapping positions inside that year."""
    year_of = np.array([d[:4] for d in days])
    out = []
    by_year: dict[str, list[int]] = {}
    for a, b in eps:
        by_year.setdefault(days[a][:4], []).append(b - a + 1)
    for y, lengths in by_year.items():
        idx = np.flatnonzero(year_of == y)
        lo, hi = int(idx[0]), int(idx[-1])
        placed: list[tuple[int, int]] = []
        for L in sorted(lengths, reverse=True):
            for _ in range(10_000):
                s = int(rng.integers(lo, hi - L + 2))
                e = s + L - 1
                if all(e < pa - 1 or s > pb + 1 for pa, pb in placed):
                    placed.append((s, e))
                    break
            else:
                raise RuntimeError(f"could not place an episode of {L} days in {y}")
        out.extend(placed)
    return sorted(out)


def placebo_test(days, eth, btc, eps, f_btc, f_eth, actual_net_total: float, legs=(1.0, -1.0), cost=PAIR_COST,
                 n_draws: int = N_PLACEBO, seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed)
    nets = []
    for _ in range(n_draws):
        pe = placebo_episodes(days, eps, rng)
        ep, _ = run_episodes(days, eth, btc, pe, f_btc, f_eth, legs=legs, cost=cost)
        nets.append(float(ep["net"].sum()))
    nets = np.array(nets)
    return {"draws": n_draws, "actual_net_total_pct": actual_net_total * 100,
            "placebo_mean_pct": float(nets.mean() * 100), "placebo_q95_pct": float(np.quantile(nets, 0.95) * 100),
            "p_placebo": float((nets >= actual_net_total).mean())}


# --- Part B: the hedge on chento's trades ------------------------------------------------------------------

def load_chento_trades() -> pd.DataFrame:
    t = pd.read_csv(STAGE1_TRADES)
    t = t[t["pop"] == "chento"].reset_index(drop=True)
    t["s"] = np.where(t["direction"] == "long", 1, -1)
    t["R"] = t["s"] * (t["exit_price"] - t["entry"]) / t["risk"]
    return t


def load_panel_close(asset: str) -> tuple[np.ndarray, int]:
    with np.load(ROOT / "studies" / "notebooks" / "orb_study" / "cache" / f"{SYMBOL[asset]}_perp_1m.npz") as z:
        close = z["close"].astype(float)
        vol = z["volume"].astype(float)
        t0 = int(z["t0_ms"][0]) // 1000
    return np.where(vol > 0, close, np.nan), t0


def _last_finite_at_or_before(close: np.ndarray, i: int) -> float:
    while i >= 0 and not np.isfinite(close[i]):
        i -= 1
    return float(close[i]) if i >= 0 else float("nan")


def hedge_trades(trades: pd.DataFrame, closes: dict[str, np.ndarray]) -> pd.DataFrame:
    """Per trade: the hedge leg's R (the other asset, opposite direction, equal notional), its cost, R_hedged."""
    out = trades.copy()
    h_entry, h_exit = [], []
    for r in trades.itertuples(index=False):
        H = closes[OTHER[r.asset]]
        h_entry.append(_last_finite_at_or_before(H, int(r.i0) - 1))
        h_exit.append(_last_finite_at_or_before(H, int(r.x)))
    out["h_entry"], out["h_exit"] = h_entry, h_exit
    out["hedge_R"] = -out["s"] * (out["h_exit"] / out["h_entry"] - 1) * out["entry"] / out["risk"]
    out["hedge_cost_R"] = LEG_RT_COST * out["entry"] / out["risk"]
    out["R_hedged"] = out["R"] + out["hedge_R"] - out["hedge_cost_R"]
    out["diff"] = out["R_hedged"] - out["R"]
    return out


def r_metrics(R: np.ndarray, years: float) -> dict:
    cum = np.cumsum(R)
    dd = max_drawdown(cum)
    ann = float(R.sum() / years)
    sd = float(R.std(ddof=1)) if len(R) > 1 else None
    return {"n": int(len(R)), "mean_R": float(R.mean()), "sd_R": sd,
            "sharpe_per_trade": float(R.mean() / sd) if sd else None,
            "annual_R": ann, "max_dd_R": float(dd), "mar": (ann / dd) if dd > 0 else None}


def hedge_summary(h: pd.DataFrame) -> dict:
    out = {}
    for asset, g in h.groupby("asset"):
        g = g.sort_values("entry_ts", kind="mergesort").reset_index(drop=True)
        years = (g["entry_ts"].iloc[-1] - g["entry_ts"].iloc[0]) / (DAYS_PER_YEAR * 86400)
        n = len(g)
        half = -(-n // 2)
        rec = {"years": float(years), "unhedged": r_metrics(g["R"].to_numpy(float), years),
               "hedged": r_metrics(g["R_hedged"].to_numpy(float), years), "halves": {}}
        for name, sl in (("first", slice(0, half)), ("second", slice(half, n))):
            gg = g.iloc[sl]
            yy = max((gg["entry_ts"].iloc[-1] - gg["entry_ts"].iloc[0]) / (DAYS_PER_YEAR * 86400), 1e-9)
            rec["halves"][name] = {"unhedged": r_metrics(gg["R"].to_numpy(float), yy),
                                   "hedged": r_metrics(gg["R_hedged"].to_numpy(float), yy)}
        d = g["diff"].to_numpy(float)
        axis = M.day_axis(g["entry_day"])
        idx = M.block_indices(len(axis))
        boot = M.boot_means(g["entry_day"], d, axis, idx)
        rec["paired_diff"] = {"mean": float(d.mean()),
                              "ci95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))],
                              "first_half": float(d[:half].mean()), "second_half": float(d[half:].mean())}
        x = -g["hedge_R"].to_numpy(float)                       # the market move in the trade's direction, in R
        y = g["R"].to_numpy(float)
        if x.std() > 0:
            rec["beta_of_R_on_market_move"] = float(np.cov(x, y, ddof=1)[0, 1] / x.var(ddof=1))
            rec["corr_R_market_move"] = float(np.corrcoef(x, y)[0, 1])
        out[asset] = rec
    return out


def hedge_decision(summary: dict) -> dict:
    conds = {}
    for asset, rec in summary.items():
        m_u, m_h = rec["unhedged"]["mar"], rec["hedged"]["mar"]
        whole = m_u is not None and m_h is not None and m_h > m_u
        hv = rec["halves"]
        both = all(hv[k]["hedged"]["mar"] is not None and hv[k]["unhedged"]["mar"] is not None
                   and hv[k]["hedged"]["mar"] > hv[k]["unhedged"]["mar"] for k in ("first", "second"))
        budget = rec["paired_diff"]["mean"] >= HEDGE_COST_BUDGET_R
        conds[asset] = {"mar_whole": whole, "mar_both_halves": both, "within_cost_budget": budget}
    ok = all(v["mar_whole"] and v["mar_both_halves"] and v["within_cost_budget"] for v in conds.values())
    return {"conditions": conds, "verdict": "PROPOSE a hedged paper variant" if ok else "KEEP UNHEDGED"}

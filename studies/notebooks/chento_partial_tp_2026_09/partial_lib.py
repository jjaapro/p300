"""Partial take-profit engine on chento entries (README.md §3), built on the exit-policy study's walker.

A ladder never changes the remainder's stop, target or time exit, so a trade reduces to its A0 exit plus, per
bar, the running maximum favourable excursion in R and the funding accrued to that bar. Every ladder — and every
placebo draw — is then a few array lookups per trade.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EXIT_DIR = ROOT / "studies" / "notebooks" / "exit_policy_2026_09"
OKX_DIR = ROOT / "studies" / "notebooks" / "okx_gate_revalidation"
for p in (str(ROOT), str(EXIT_DIR), str(OKX_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)
import chento_lib as C  # noqa: E402  — the exit-policy study's walker, inputs, bootstrap
import okxlib as L  # noqa: E402

RESULTS = HERE / "results"
A0_REFERENCE = EXIT_DIR / "results" / "chento" / "walks.csv.gz"

# README §4. (level in R, fraction of the position still running), ascending.
LADDERS: dict[str, tuple[tuple[float, float], ...]] = {
    "P1": ((1.0, 0.25), (2.0, 0.20), (3.0, 0.10), (4.0, 0.20)),
    "P2": ((3.0, 0.50),),
    "P3": ((1.0, 0.50),),
}
CANDIDATES = ("P1", "P2", "P3")
PREFERENCE = ("P1", "P2", "P3")
PLACEBO_DRAWS = 500
PLACEBO_SEED = 7
PLACEBO_RANGE = (0.5, 5.5)
# README §6.
MAR_GAIN = 1.10
MEAN_D_FLOOR = -0.10
CI_LOW_FLOOR = -0.30
RISK_PCT = 0.02          # the bot's fixed-R sizing, for the %-of-capital view


@dataclass
class TradePath:
    asset: str
    t: int
    direction: str
    entry_day: str
    exit_kind: str
    exit_pos: int            # index into mfe / f_cum of A0's exit bar
    r_price_a0: float        # A0's R_price (cost already netted)
    cost_R: float
    f_exit: float            # A0's funding_R (per unit position)
    mfe: np.ndarray          # running max favourable excursion in R, bars entry+15m .. exit bar
    f_cum: np.ndarray        # funding_R to each bar's open, inclusive (per unit position)
    hours: np.ndarray        # hours from entry to each bar's open


def light_market(con, asset: str) -> C.Market:
    """Bars and funding only; the flow features the exit-policy arms needed are not read here."""
    bars = L.load_bars(con, asset)
    with np.load(C.ORB_CACHE / f"{C.FUNDING_SYMBOL[asset]}_funding.npz") as z:
        fs = (z["time_ms"] // 1000).astype(np.int64)
        fr = z["rate"].astype(float)
    nan = np.full(len(bars.ts), np.nan)
    return C.Market(asset, bars, nan, nan, {"long": np.array([], np.int64), "short": np.array([], np.int64)}, fs, fr)


def a0_walks(ctm, markets: dict, trades: pd.DataFrame) -> pd.DataFrame:
    return C.walk_all(ctm, markets, trades, k=3.0, cost_bp=C.COST_BP, with_funding=True, arms=(C.BY_ID["A0"],))


def check_a0_reference(walks: pd.DataFrame) -> dict:
    """The reproduced A0 must equal the committed exit-policy record trade for trade."""
    ref = pd.read_csv(A0_REFERENCE)
    ref = ref[ref["arm"] == "A0"].set_index(["asset", "t", "direction"])
    mine = walks.set_index(["asset", "t", "direction"])
    common = mine.index.intersection(ref.index)
    d = (mine.loc[common, "net_R"] - ref.loc[common, "net_R"]).abs()
    return {"reference": str(A0_REFERENCE.relative_to(ROOT)), "reference_sha256": C.sha256(A0_REFERENCE),
            "trades": int(len(mine)), "matched": int(len(common)), "max_abs_diff_net_R": float(d.max()) if len(d) else None,
            "pass_": bool(len(common) == len(mine) == len(ref) and (d.max() < 1e-9))}


def build_paths(markets: dict, trades: pd.DataFrame, walks: pd.DataFrame) -> list[TradePath]:
    w = walks.set_index(["asset", "t", "direction"])
    paths = []
    for tr in trades.itertuples(index=False):
        mkt = markets[tr.asset]
        bars = mkt.bars
        row = w.loc[(tr.asset, int(tr.t), tr.direction)]
        assert row["kind"] in ("stop", "target", "time"), f"unexpected A0 exit {row['kind']} at {tr.asset} {tr.t}"
        entry_ts = int(tr.t) + C.BAR_S
        lo = bars.first_ge(entry_ts)
        hi = bars.pos(int(row["exit_bar_ts"]))
        assert lo >= 0 and hi >= lo, "exit bar not on the path"
        sign = 1.0 if tr.direction == "long" else -1.0
        entry, risk = float(tr.entry), float(tr.risk)
        fav = (bars.high[lo:hi + 1] - entry) / risk if sign > 0 else (entry - bars.low[lo:hi + 1]) / risk
        mfe = np.maximum.accumulate(np.where(np.isfinite(fav), fav, -np.inf))
        f_cum = np.array([C.funding_R(mkt, tr.direction, risk, entry_ts, int(b), inclusive=True)
                          for b in bars.ts[lo:hi + 1]])
        hours = (bars.ts[lo:hi + 1] - entry_ts) / 3600.0
        paths.append(TradePath(tr.asset, int(tr.t), tr.direction, tr.entry_day, row["kind"], hi - lo,
                               float(row["R_price"]), float(row["cost_R"]), float(row["funding_R"]),
                               mfe, f_cum, hours))
    return paths


def apply_ladder(p: TradePath, ladder) -> dict:
    """README §3 steps 2-5. Returns the trade's R_price, funding_R, net_R and fill facts."""
    rem, r_legs, f_legs, fills = 1.0, 0.0, 0.0, []
    for level, frac in ladder:
        hit = np.flatnonzero(p.mfe >= level)
        if not len(hit):
            break
        j = int(hit[0])
        if j == p.exit_pos and p.exit_kind == "stop":
            break                                   # stop before target within the exit bar
        leg = rem * frac
        r_legs += leg * level
        f_legs += leg * p.f_cum[j]
        rem -= leg
        fills.append({"level": level, "fraction": leg, "hours": float(p.hours[j])})
    gross_a0 = p.r_price_a0 + p.cost_R
    r_price = r_legs + rem * gross_a0 - p.cost_R
    funding = f_legs + rem * p.f_exit
    return {"R_price": r_price, "funding_R": funding, "net_R": r_price + funding,
            "remainder": rem, "n_fills": len(fills), "first_fill_h": fills[0]["hours"] if fills else None,
            "closed_early": 1.0 - rem}


def net_for(paths: list[TradePath], ladder) -> np.ndarray:
    return np.array([apply_ladder(p, ladder)["net_R"] for p in paths])


# --- sequences and MAR (README §5) ----------------------------------------------------------------

def sequence_stats(net: np.ndarray, entry_ts: np.ndarray, entry_day: np.ndarray, mask=None) -> dict:
    if mask is None:
        mask = np.ones(len(net), bool)
    order = np.argsort(entry_ts[mask], kind="mergesort")
    r = net[mask][order]
    if len(r) == 0:
        return {"trades": 0}
    cum = np.cumsum(r)
    dd = cum - np.maximum.accumulate(np.r_[0.0, cum])[1:]
    max_dd = float(dd.min()) if len(dd) else 0.0
    days = pd.to_datetime(entry_day[mask][order])
    years = max((days.max() - days.min()).days / 365.25, 1e-9)
    ann = float(cum[-1] / years)
    return {"trades": int(len(r)), "mean_net_R": float(r.mean()), "cum_R": float(cum[-1]),
            "win_rate": float((r > 0).mean()), "years": float(years), "annual_R": ann,
            "max_dd_R": max_dd, "MAR_R": (ann / abs(max_dd)) if max_dd < 0 else None,
            "cum_pct_at_2pct_risk": float(cum[-1] * RISK_PCT * 100), "max_dd_pct_at_2pct_risk": float(max_dd * RISK_PCT * 100)}


def all_sequences(net: np.ndarray, trades: pd.DataFrame) -> dict:
    ts = trades["entry_ts"].to_numpy(np.int64)
    day = trades["entry_day"].to_numpy()
    first = (trades["entry_day"] < C.HALF_SPLIT_DAY).to_numpy()
    out = {"pooled": sequence_stats(net, ts, day)}
    for a in C.ASSETS:
        out[a] = sequence_stats(net, ts, day, (trades["asset"] == a).to_numpy())
    out["first_half"] = sequence_stats(net, ts, day, first)
    out["second_half"] = sequence_stats(net, ts, day, ~first)
    return out


def paired(net_p: np.ndarray, net_a0: np.ndarray, trades: pd.DataFrame, idx: np.ndarray) -> dict:
    d = net_p - net_a0
    sums, counts = C.day_sums(trades["entry_day"], d)
    boot = C.boot_mean(sums, counts, idx)
    first = (trades["entry_day"] < C.HALF_SPLIT_DAY).to_numpy()
    return {"mean_d": float(d.mean()),
            "ci95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.5)), float(np.quantile(boot, 0.975))],
            "mean_d_BTC": float(d[(trades["asset"] == "BTC").to_numpy()].mean()),
            "mean_d_ETH": float(d[(trades["asset"] == "ETH").to_numpy()].mean()),
            "mean_d_first_half": float(d[first].mean()), "mean_d_second_half": float(d[~first].mean()),
            "trades_changed": int((np.abs(d) > 1e-12).sum())}


def fill_stats(paths: list[TradePath], ladder) -> dict:
    res = [apply_ladder(p, ladder) for p in paths]
    n = len(res)
    by_level = {}
    for k, (level, frac) in enumerate(ladder):
        by_level[str(level)] = float(np.mean([r["n_fills"] > k for r in res]))
    fh = [r["first_fill_h"] for r in res if r["first_fill_h"] is not None]
    return {"share_with_any_fill": float(np.mean([r["n_fills"] > 0 for r in res])),
            "share_filled_by_level": by_level, "mean_closed_early": float(np.mean([r["closed_early"] for r in res])),
            "median_hours_to_first_fill": float(np.median(fh)) if fh else None, "trades": n}


def placebo(paths: list[TradePath], trades: pd.DataFrame, fractions: tuple[float, ...],
            draws: int = PLACEBO_DRAWS, seed: int = PLACEBO_SEED) -> dict:
    rng = np.random.default_rng(seed)
    ts = trades["entry_ts"].to_numpy(np.int64)
    day = trades["entry_day"].to_numpy()
    mars, means, ladders = [], [], []
    for _ in range(draws):
        levels = np.sort(rng.uniform(*PLACEBO_RANGE, size=len(fractions)))
        ladder = tuple(zip(levels.tolist(), fractions))
        net = net_for(paths, ladder)
        s = sequence_stats(net, ts, day)
        mars.append(s["MAR_R"] if s["MAR_R"] is not None else np.nan)
        means.append(s["mean_net_R"])
        ladders.append([round(x, 3) for x in levels])
    mars = np.array(mars, float)
    return {"draws": draws, "fractions": list(fractions), "level_range": list(PLACEBO_RANGE), "seed": seed,
            "MAR_R_p05": float(np.nanquantile(mars, 0.05)), "MAR_R_p50": float(np.nanquantile(mars, 0.50)),
            "MAR_R_p95": float(np.nanquantile(mars, 0.95)), "mean_net_R_p50": float(np.median(means)),
            "_mars": mars.tolist()}


def decide(seqs: dict, pairs: dict, placebos: dict) -> dict:
    a0 = seqs["A0"]
    clauses, passing = {}, []
    for c in CANDIDATES:
        s, p = seqs[c], pairs[c]
        def mar(x):
            return x["MAR_R"] if x.get("MAR_R") is not None else -np.inf
        cl = {"D1_mar_gain_both_assets": all(mar(s[a]) >= MAR_GAIN * mar(a0[a]) for a in C.ASSETS),
              "D2_mar_both_halves": all(mar(s[h]) >= mar(a0[h]) for h in ("first_half", "second_half")),
              "D3_bounded_cost": (p["mean_d"] >= MEAN_D_FLOOR) and (p["ci95"][0] >= CI_LOW_FLOOR)}
        clauses[c] = {**cl, "passes": all(cl.values()),
                      "placebo_p95_MAR_R": placebos[c]["MAR_R_p95"],
                      "levels_beat_placebo_p95": bool(mar(s["pooled"]) >= placebos[c]["MAR_R_p95"])}
        if clauses[c]["passes"]:
            passing.append(c)
    if passing:
        chosen = min(passing, key=PREFERENCE.index)
        label = "levels" if clauses[chosen]["levels_beat_placebo_p95"] else "trimming"
        verdict = f"PROMOTE({chosen}, {label})"
    elif not any(clauses[c]["D1_mar_gain_both_assets"] for c in CANDIDATES):
        chosen, verdict = None, "KEEP_6R"
    else:
        chosen, verdict = None, "INCONCLUSIVE"
    return {"verdict": verdict, "chosen": chosen, "passing": passing, "clauses": clauses}

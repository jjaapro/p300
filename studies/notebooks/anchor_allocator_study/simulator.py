"""A1 anchor-allocator study — Step 2: allocator simulator.

Port of C:\\Source\\Repos\\trader\\p300_simulator.py (`allocate_day`, `simulate`,
`metrics`, `per_year_returns`) adapted to:

  * the p300 panel produced by build_panel.py (results/daily_panel.csv),
  * an in-code registry (bucket / cap / asset / direction per sleeve),
  * the decomposition variants fixed in README.md,
  * an idle-capital metric per day (tactical budget − Σ active caps, before
    overflow), a turnover-based rebalancing cost on GOLD / EMA notional,
  * BTC 30d taken through T−1 (the predecessor indexed it through T),
  * a net-BTC cap on actual position direction (not the PnL-sign proxy).

Everything here is a pure function of the panel; nothing is read from disk
or the DB. `metrics` and `per_year_returns` are verbatim ports.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# ─── Fixed allocator rules (README "Fixed allocator rules") ──────────────────
BUCKET_A_TARGET = 0.48   # Anchor
BUCKET_B_TARGET = 0.47   # Tactical
BUCKET_C_TARGET = 0.05   # Reserve — always cash, earns 0 in every variant

GOLD_BULL_THRESHOLD = 0.05   # BTC 30d > +5 % → GOLD 15 %, else 55 %
GOLD_W_BULL = 0.15
GOLD_W_ELSE = 0.55
EMA_TARGET = 0.18
CASH_FLOOR = 0.08
BASELINE_CASH = 0.30         # baseline anchor = EMA 18 % + cash 30 %

SPLIT_BULL = (0.40, 0.60)        # (GOLD, EMA) when BTC 30d ≥ −10 %
SPLIT_DRAWDOWN = (0.80, 0.20)    # when BTC 30d < −10 %
DRAWDOWN_THRESHOLD = -0.10

MAX_NET_BTC = 0.50
TURNOVER_COST = 15e-4            # on |Δ notional| of GOLD and EMA per day

TACTICAL = ["r4_btc", "r4_eth", "r4_btc_v2", "r4_eth_v2", "adx", "carry",
            "chento_btc", "chento_eth", "eth_daily"]
ANCHOR = ["gold", "ema_1w_btc", "cash"]

# bucket A = anchor, B = tactical. `lev_col` = per-day notional multiplier
# column (R4 inner leverage), `dir_col` = per-day position sign column.
REGISTRY: dict[str, dict] = {
    "r4_btc":     dict(bucket="B", cap=0.06, asset="BTC", direction="long", lev_col="aux_r4_lev"),
    "r4_eth":     dict(bucket="B", cap=0.06, asset="ETH", direction="long", lev_col="aux_r4_lev"),
    "r4_btc_v2":  dict(bucket="B", cap=0.04, asset="BTC", direction="long", lev_col="aux_r4_lev"),
    "r4_eth_v2":  dict(bucket="B", cap=0.04, asset="ETH", direction="long", lev_col="aux_r4_lev"),
    "adx":        dict(bucket="B", cap=0.10, asset="BTC", direction="dynamic", dir_col="aux_adx_dir"),
    "carry":      dict(bucket="B", cap=0.08, asset="BTC", direction="neutral"),
    "chento_btc": dict(bucket="B", cap=0.08, asset="BTC", direction="dynamic", dir_col="aux_chento_btc_dir"),
    "chento_eth": dict(bucket="B", cap=0.06, asset="ETH", direction="dynamic", dir_col="aux_chento_eth_dir"),
    "eth_daily":  dict(bucket="B", cap=0.06, asset="ETH", direction="long"),
    "ema_1w_btc": dict(bucket="A", cap=None, asset="BTC", direction="dynamic", dir_col="aux_ema_dir"),
    "gold":       dict(bucket="A", cap=None, asset="GOLD", direction="long"),
    "cash":       dict(bucket="A", cap=None, asset="CASH", direction="none"),
}
assert abs(sum(REGISTRY[s]["cap"] for s in TACTICAL) - 0.58) < 1e-12


@dataclass(frozen=True)
class Variant:
    name: str
    gold_in_anchor: bool          # dynamic GOLD rule vs baseline anchor (EMA 18 + cash 30)
    overflow: str                 # "none" | "anchor" (GOLD/EMA split) | "cash"
    gold_as_cash: bool = False    # weights unchanged, GOLD dollars earn the cash yield
    split: str = "regime"         # "regime" | "fixed_40_60" | "fixed_80_20"
    gross_cap: float = 2.0
    net_btc_cap: float = MAX_NET_BTC
    claim_lag: int = 0            # diagnostic only: overflow uses the previous day's idle
    group: str = "core"


CORE_VARIANTS = [
    Variant("baseline", gold_in_anchor=False, overflow="none"),
    Variant("baseline_gold", gold_in_anchor=True, overflow="none"),
    Variant("overflow", gold_in_anchor=True, overflow="anchor"),
    Variant("overflow_no_gold", gold_in_anchor=True, overflow="anchor", gold_as_cash=True),
    Variant("overflow_cash_only", gold_in_anchor=False, overflow="cash"),
]

SENSITIVITY_VARIANTS = [
    Variant(f"overflow_split{sp}_gross{g:g}x", gold_in_anchor=True, overflow="anchor",
            split=f"fixed_{sp}", gross_cap=g, group="sensitivity")
    for sp in ("40_60", "80_20") for g in (1.5, 2.0)
]

DIAGNOSTIC_VARIANTS = [
    Variant("overflow_claimlag1", gold_in_anchor=True, overflow="anchor", claim_lag=1, group="diagnostic"),
    Variant("overflow_no_gold_claimlag1", gold_in_anchor=True, overflow="anchor", gold_as_cash=True,
            claim_lag=1, group="diagnostic"),
]


# ─── Panel access ────────────────────────────────────────────────────────────

def load_panel(path) -> pd.DataFrame:
    p = pd.read_csv(path, index_col="date")
    p.index = p.index.astype(str)
    return p


def panel_arrays(panel: pd.DataFrame) -> dict[str, np.ndarray]:
    P = {c: panel[c].to_numpy(dtype=float) for c in panel.columns if c != "aux_mode"}
    P["_dates"] = np.array(panel.index.astype(str))
    return P


# ─── Allocation for one day ──────────────────────────────────────────────────

def anchor_targets(btc30: float, gold_in_anchor: bool) -> tuple[float, float, float]:
    """(gold, ema, cash) weights of the 48 % anchor before overflow."""
    if not gold_in_anchor:
        return 0.0, EMA_TARGET, BASELINE_CASH
    gold = GOLD_W_BULL if btc30 > GOLD_BULL_THRESHOLD else GOLD_W_ELSE
    ema, cash = EMA_TARGET, CASH_FLOOR
    raw = gold + ema + cash
    if raw > BUCKET_A_TARGET:
        k = BUCKET_A_TARGET / raw
        gold, ema, cash = gold * k, ema * k, cash * k
    else:
        cash += BUCKET_A_TARGET - raw      # residual → cash (README interpretation 4)
    return gold, ema, cash


def _exposure(s: str, i: int, P: dict) -> tuple[float, float]:
    """(signed BTC notional per unit weight, gross notional per unit weight)."""
    m = REGISTRY[s]
    if s == "cash":
        return 0.0, 0.0
    mult = P[m["lev_col"]][i] if m.get("lev_col") else 1.0
    d = P[m["dir_col"]][i] if m.get("dir_col") else 1.0
    gross = mult * abs(d)
    if m["asset"] != "BTC" or m["direction"] == "neutral":
        return 0.0, gross
    return mult * d, gross


def allocate_day(i: int, P: dict, v: Variant, idle_prev: float | None = None) -> dict:
    """Target weights (fraction of NAV) for day i and the day's bookkeeping."""
    btc30 = P["aux_btc_30d"][i]
    gold, ema, cash = anchor_targets(btc30, v.gold_in_anchor)

    active = {s: REGISTRY[s]["cap"] for s in TACTICAL if P[s][i] != 0.0}
    tot = sum(active.values())
    if tot > BUCKET_B_TARGET:
        k = BUCKET_B_TARGET / tot
        active = {s: c * k for s, c in active.items()}
        idle = 0.0
    else:
        idle = BUCKET_B_TARGET - tot          # idle-capital metric, before overflow

    ov = idle if (v.claim_lag == 0 or idle_prev is None) else idle_prev
    routed = 0.0
    if v.overflow == "anchor" and ov > 0:
        if v.split == "regime":
            g, e = SPLIT_DRAWDOWN if btc30 < DRAWDOWN_THRESHOLD else SPLIT_BULL
        elif v.split == "fixed_40_60":
            g, e = SPLIT_BULL
        elif v.split == "fixed_80_20":
            g, e = SPLIT_DRAWDOWN
        else:
            raise ValueError(v.split)
        gold += ov * g
        ema += ov * e
        routed = ov
    elif v.overflow == "cash" and ov > 0:
        cash += ov
        routed = ov

    w = {"gold": gold, "ema_1w_btc": ema, "cash": cash, **active}

    # Max net long-BTC exposure: scale the BTC-long sleeves so net = cap.
    long_btc, short_btc = 0.0, 0.0
    exp = {}
    for s in w:
        e_btc, e_gross = _exposure(s, i, P)
        exp[s] = (e_btc, e_gross)
        if e_btc > 0:
            long_btc += w[s] * e_btc
        elif e_btc < 0:
            short_btc += -w[s] * e_btc
    net_btc = long_btc - short_btc
    freed_net = 0.0
    net_bound = False
    if net_btc > v.net_btc_cap and long_btc > 0:
        k = (v.net_btc_cap + short_btc) / long_btc
        for s in w:
            if exp[s][0] > 0:
                freed_net += w[s] * (1 - k)
                w[s] *= k
        net_btc = v.net_btc_cap
        net_bound = True

    # Gross cap: scale every invested (non-cash) sleeve.
    gross = sum(w[s] * exp[s][1] for s in w)
    freed_gross = 0.0
    gross_bound = False
    if gross > v.gross_cap:
        k = v.gross_cap / gross
        for s in w:
            if s != "cash":
                freed_gross += w[s] * (1 - k)
                w[s] *= k
        gross = v.gross_cap
        gross_bound = True
        net_btc = sum(w[s] * exp[s][0] for s in w)

    return dict(weights=w, idle=idle, routed=routed, gross=gross, net_btc=net_btc,
                n_active=len(active), freed_net=freed_net, freed_gross=freed_gross,
                net_bound=net_bound, gross_bound=gross_bound)


# ─── Full simulation ─────────────────────────────────────────────────────────

def simulate(panel: pd.DataFrame, v: Variant) -> dict:
    P = panel_arrays(panel)
    dates = list(P["_dates"])
    n = len(dates)
    daily_ret = np.zeros(n)
    turnover_cost = np.zeros(n)
    idle = np.zeros(n); routed = np.zeros(n); gross = np.zeros(n); net_btc = np.zeros(n)
    w_gold = np.zeros(n); w_ema = np.zeros(n); w_cash = np.zeros(n); w_tac = np.zeros(n)
    n_active = np.zeros(n, dtype=int)
    freed_net = np.zeros(n); freed_gross = np.zeros(n)
    net_bound = np.zeros(n, dtype=bool); gross_bound = np.zeros(n, dtype=bool)
    contrib = {s: 0.0 for s in TACTICAL + ANCHOR}
    wsum = {s: 0.0 for s in TACTICAL + ANCHOR}
    held = {s: 0 for s in TACTICAL + ANCHOR}
    prev_gold, prev_ema = 0.0, 0.0
    idle_prev = None
    for i in range(n):
        a = allocate_day(i, P, v, idle_prev)
        w = a["weights"]
        r = 0.0
        for s, ws in w.items():
            rs = P["cash"][i] if (s == "gold" and v.gold_as_cash) else P[s][i]
            c = ws * rs
            r += c
            contrib[s] += c
            wsum[s] += ws
            held[s] += 1
        # rebalancing cost on |Δ notional| of GOLD (when real) and EMA
        d_gold = abs(w["gold"] - prev_gold) if not v.gold_as_cash else 0.0
        d_ema = abs(w["ema_1w_btc"] - prev_ema)
        tc = TURNOVER_COST * (d_gold + d_ema)
        turnover_cost[i] = tc
        daily_ret[i] = r - tc
        prev_gold, prev_ema = w["gold"], w["ema_1w_btc"]
        idle_prev = a["idle"]
        idle[i] = a["idle"]; routed[i] = a["routed"]; gross[i] = a["gross"]; net_btc[i] = a["net_btc"]
        w_gold[i] = w["gold"]; w_ema[i] = w["ema_1w_btc"]; w_cash[i] = w["cash"]
        w_tac[i] = sum(w[s] for s in TACTICAL if s in w)
        n_active[i] = a["n_active"]
        freed_net[i] = a["freed_net"]; freed_gross[i] = a["freed_gross"]
        net_bound[i] = a["net_bound"]; gross_bound[i] = a["gross_bound"]
    nav = np.cumprod(1.0 + daily_ret)
    avg_w = {s: (wsum[s] / held[s] if held[s] else 0.0) for s in wsum}
    return dict(variant=v, dates=dates, daily_ret=daily_ret, nav=nav, turnover_cost=turnover_cost,
                idle=idle, routed=routed, gross=gross, net_btc=net_btc, w_gold=w_gold, w_ema=w_ema,
                w_cash=w_cash, w_tactical=w_tac, n_active=n_active, freed_net=freed_net,
                freed_gross=freed_gross, net_bound=net_bound, gross_bound=gross_bound,
                sleeve_contrib=contrib, sleeve_avg_weight=avg_w, sleeve_days=held)


# ─── Metrics (verbatim ports) ────────────────────────────────────────────────

def metrics(daily_ret, name=""):
    """Verbatim port of p300_simulator.metrics: Sharpe = CAGR / annualised
    daily vol (365-day year, population std, no risk-free rate); MDD on the
    compounded NAV."""
    daily_ret = np.asarray(daily_ret, dtype=float)
    n = len(daily_ret)
    total_ret = np.prod(1 + daily_ret) - 1
    ann_ret = (1 + total_ret) ** (365 / n) - 1
    ann_vol = daily_ret.std() * np.sqrt(365)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0
    nav = np.cumprod(1 + daily_ret)
    peak = np.maximum.accumulate(nav)
    dd = (nav - peak) / peak
    mdd = float(abs(dd.min()))
    calmar = ann_ret / mdd if mdd > 0 else 0.0
    return {
        'name': name, 'n_days': n, 'total_ret': float(total_ret), 'ann_ret': float(ann_ret),
        'ann_vol': float(ann_vol), 'sharpe': float(sharpe), 'mdd': mdd, 'calmar': float(calmar),
    }


def per_year_returns(dates, daily_ret):
    """Verbatim port of p300_simulator.per_year_returns."""
    years = {}
    nav = np.cumprod(1 + daily_ret)
    for i, d in enumerate(dates):
        yr = int(d.split('-')[0])
        years.setdefault(yr, []).append((i, daily_ret[i]))
    out = {}
    for yr in sorted(years.keys()):
        indices = [ix for ix, _ in years[yr]]
        i0, iN = indices[0], indices[-1]
        if i0 == 0:
            yr_ret = nav[iN] - 1
        else:
            yr_ret = nav[iN] / nav[i0 - 1] - 1
        out[yr] = float(yr_ret)
    return out


def slice_metrics(dates, daily_ret, start: str, end: str | None = None, name: str = ""):
    """metrics() on the sub-period [start, end] (inclusive, ISO dates)."""
    d = np.asarray(dates)
    m = d >= start
    if end is not None:
        m &= d <= end
    return metrics(np.asarray(daily_ret)[m], name)

"""S2 — sizing rules on the shipped-policy trades, and the ruin table."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import sizing_lib as sl

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")

ex = sl.ex
WINDOWS = {"1h": 60, "6h": 360, "24h": 1440, "72h": 4320}


def sizing_rules(df: pd.DataFrame, sleeve: str) -> dict:
    t = df[(df.sleeve == sleeve) & (df.policy == "P0_shipped")].sort_values("ts").reset_index(drop=True)
    years = max((t.ts.max() - t.ts.min()) / (365.25 * 86400), 0.05)
    med_notional = float(t.notional_usd.median())
    out = {}
    for rule in ("fixed_R_fixed_capital", "fixed_R_on_equity", "fixed_notional"):
        eq, peak, mdd, worst = sl.CAPITAL, sl.CAPITAL, 0.0, 0.0
        for r in t.itertuples():
            if rule == "fixed_R_fixed_capital":
                notional = r.notional_x * sl.CAPITAL
            elif rule == "fixed_R_on_equity":
                notional = r.notional_x * eq
            else:
                notional = med_notional
            pnl = notional * r.pnl_frac - notional * sl.cost_bp(sleeve) / 1e4
            worst = min(worst, pnl / eq)
            eq += pnl; peak = max(peak, eq); mdd = min(mdd, (eq - peak) / peak)
        cagr = (eq / sl.CAPITAL) ** (1 / years) - 1
        out[rule] = dict(final_equity=eq, cagr_pct=cagr * 100, maxdd_pct=mdd * 100, mar=float(cagr / abs(mdd)) if mdd < 0 else float("inf"),
                         worst_trade_pct_equity=worst * 100, n=len(t), years=years)
    return out


def worst_moves(path: ex.PricePath) -> dict:
    h = pd.Series(path.h); lo = pd.Series(path.l)
    out = {}
    for name, w in WINDOWS.items():
        drop = (lo / h.rolling(w, min_periods=1).max() - 1.0)
        rise = (h / lo.rolling(w, min_periods=1).min() - 1.0)
        i, j = int(drop.idxmin()), int(rise.idxmax())
        out[name] = dict(worst_drop_pct=float(drop.min() * 100), drop_at=ex.iso(path.ts[i]),
                         worst_rise_pct=float(rise.max() * 100), rise_at=ex.iso(path.ts[j]))
    return out


def main() -> None:
    df = pd.read_csv(sl.RESULTS / "s1_trades.csv")
    out = {"env": ex.env_info(), "sleeves": {}, "ruin": {}}
    for sleeve in ex.WALK_SLEEVES:
        out["sleeves"][sleeve] = sizing_rules(df, sleeve)
        print(f"\n{sleeve}")
        print(pd.DataFrame(out["sleeves"][sleeve]).T.round(2).to_string())
    g = np.array([0.5, 1, 2, 3, 5, 10])
    out["ruin"]["liquidating_move_pct_by_gross"] = {f"{x:g}x": float((1 - sl.MM_PCT * x) / x * 100) for x in g}
    out["ruin"]["worst_moves"] = {"BTC": worst_moves(ex.load_path("btc_1m")), "ETH": worst_moves(ex.load_path("eth_1m"))}
    caps = {"CHENTO_BTC": 3.0, "CHENTO_ETH": 3.0, "SHORT_SQUEEZE": 3.0, "SQUEEZE_BULL": 0.5, "ADX": 0.5}
    out["ruin"]["fleet_caps_x"] = caps
    out["ruin"]["pooled_max_gross_x_over_5_bots"] = float(sum(caps.values()) / 5)
    print("\nliquidating adverse move by gross exposure:", out["ruin"]["liquidating_move_pct_by_gross"])
    print("worst moves:", out["ruin"]["worst_moves"])
    sl.jdump(out, "s2_sizing_rules.json")


if __name__ == "__main__":
    main()

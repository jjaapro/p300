"""P1(e) — funding paid by ADX longs, and how often CARRY's perp short was on at the same time."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import adx_lib as al

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")

ex, bv = al.ex, al.bv


def carry_state() -> pd.Series:
    """S-078: enter when the 7-day average daily funding > 0; exit after 3 consecutive negative days.
    Daily funding = sum of the three settlements (Binance settlement rows)."""
    rows = al.funding_rows()
    f = pd.Series({int(t // 1000): r for t, r in rows}).sort_index()
    day = pd.Series(f.values, index=pd.to_datetime(f.index, unit="s").normalize()).groupby(level=0).sum() * 100
    avg7 = day.rolling(7).mean()
    on, streak, state = [], 0, False
    for d, v, a in zip(day.index, day.values, avg7.values):
        streak = streak + 1 if v < 0 else 0
        if state and streak >= 3:
            state = False
        elif not state and not np.isnan(a) and a > 0 and streak < 3:
            state = True
        on.append(state)
    return pd.Series(on, index=day.index)


def main() -> None:
    led = pd.read_csv(al.RESULTS / "p1a_live_ledger.csv")
    longs = led[led.dir == "long"]; shorts = led[led.dir == "short"]
    hold_days = (longs.exit_ts - longs.entry_ts) / 86400
    paid = -longs.funding_pct          # funding_pct is signed P&L; longs pay when positive rates
    out = {"env": ex.env_info(),
           "longs": dict(n=int(len(longs)), funding_paid_pct_sum=float(paid.sum()), per_trade_mean_pct=float(paid.mean()),
                         long_days=float(hold_days.sum()), pct_per_year_of_long_exposure=float(paid.sum() / hold_days.sum() * 365.25),
                         share_trades_paying=float((paid > 0).mean()), max_paid_pct=float(paid.max())),
           "shorts": dict(n=int(len(shorts)), funding_received_pct_sum=float(shorts.funding_pct.sum()))}
    cs = carry_state()
    days = []
    for t in longs.itertuples():
        for d in pd.date_range(pd.Timestamp(t.entry_ts, unit="s").normalize(), pd.Timestamp(t.exit_ts, unit="s").normalize(), freq="D"):
            days.append((d, bool(cs.get(d, False))))
    dd = pd.DataFrame(days, columns=["day", "carry_on"])
    out["netting"] = dict(long_days=int(len(dd)), share_carry_on=float(dd.carry_on.mean()), carry_on_share_all_days=float(cs.mean()),
                          note="when CARRY's perp short is on, the pair's perp legs net and the ADX long is economically a spot long")
    # spot-vs-perp arithmetic for the long leg: extra spot fee vs funding paid
    out["venue_arith"] = dict(spot_extra_fee_bp_rt=2 * (10.0 - 5.0), funding_paid_bp_per_trade_mean=float(paid.mean() * 100),
                              longs_where_spot_cheaper=int((paid * 100 > 10).sum()))
    print(out["longs"]); print(out["shorts"]); print(out["netting"]); print(out["venue_arith"])
    al.jdump(out, "p1e_funding.json")


if __name__ == "__main__":
    main()

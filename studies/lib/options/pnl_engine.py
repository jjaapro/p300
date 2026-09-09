"""Delta-hedged short strangle/straddle P&L — port of `hedged_pnl` in trader
research/probe_vrp_straddle_v2.py (2026-04), generalised only by a Costs
dataclass. Numbers are in USD per one strangle on one unit of underlying.

Mechanics (per expiry):
  entry (T-N): receive (call_mark + put_mark) × (1 − bid_haircut), pay
      option_fee_rate × spot per leg; hedge to delta-flat with a perp,
      paying hedge_fee_rate × |Δhedge| × spot
  each day: hedge P&L accrues on the held perp size; every hedge_freq_days
      re-solve per-leg IV from the day's USD marks (BS, r = 0), recompute the
      strangle delta and rebalance (fee on the change); days without marks
      are skipped (counted in n_skip)
  expiry: pay the terminal payoff (USD mark on expiry day), close the hedge
P&L = received premium − terminal payoff + hedge P&L − hedge fees − entry fees.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import bs

YEAR_S = 365.25 * 86400


@dataclass(frozen=True)
class Costs:
    bid_haircut: float = 0.03          # mid-to-bid on each leg at entry
    option_fee_rate: float = 0.0003    # Deribit 0.03% of underlying per leg, entry only
    hedge_fee_rate: float = 0.0005     # perp taker 5 bp on |Δhedge| × spot


def hedged_short_pnl(spot: dict[str, float], expiry_ts: int, entry_date_iso: str,
                     strike_call: float, strike_put: float,
                     call_marks: dict[str, float], put_marks: dict[str, float],
                     call_mark_entry: float, put_mark_entry: float,
                     terminal_payoff: float, *, hedge_freq_days: int = 7,
                     costs: Costs = Costs()) -> dict | None:
    """Returns the P&L decomposition dict (see module docstring) or None when
    the entry IV cannot be solved."""
    spot_entry = spot[entry_date_iso]
    raw_premium = call_mark_entry + put_mark_entry
    received = raw_premium * (1.0 - costs.bid_haircut)
    entry_fees = 2.0 * costs.option_fee_rate * spot_entry

    entry_dt = datetime.fromisoformat(entry_date_iso + "T00:00:00+00:00")
    expiry_date = datetime.fromtimestamp(expiry_ts, tz=timezone.utc).date().isoformat()
    days = []
    cur = entry_dt + timedelta(days=1)
    while cur.date().isoformat() <= expiry_date:
        days.append(cur.date().isoformat())
        cur += timedelta(days=1)

    T0 = (expiry_ts - entry_dt.timestamp()) / YEAR_S
    iv_c = bs.implied_vol(call_mark_entry, spot_entry, strike_call, T0, True)
    iv_p = bs.implied_vol(put_mark_entry, spot_entry, strike_put, T0, False)
    if iv_c is None or iv_p is None:
        return None
    hedge = bs.call_delta(spot_entry, strike_call, T0, iv_c) + bs.put_delta(spot_entry, strike_put, T0, iv_p)
    # short both legs ⇒ position delta = −(Δc + Δp); hedge size offsets it
    hedge_costs = abs(hedge) * spot_entry * costs.hedge_fee_rate
    hedge_pnl = 0.0
    n_rebal, n_skip = 1, 0
    prev_spot = spot_entry
    since = 0

    for d in days:
        s = spot.get(d)
        if s is None:
            dd = datetime.fromisoformat(d)
            for off in (1, -1, 2, -2):
                alt = (dd + timedelta(days=off)).date().isoformat()
                if alt in spot:
                    s = spot[alt]
                    break
            if s is None:
                continue
        hedge_pnl += hedge * (s - prev_spot)
        since += 1
        if d == expiry_date:
            hedge_costs += abs(hedge) * s * costs.hedge_fee_rate
            hedge = 0.0
            prev_spot = s
            break
        if since < hedge_freq_days:
            prev_spot = s
            continue
        cm, pm = call_marks.get(d), put_marks.get(d)
        if cm is None or pm is None:
            n_skip += 1
            prev_spot = s
            continue
        d_ts = int(datetime.fromisoformat(d + "T00:00:00+00:00").timestamp())
        T = (expiry_ts - d_ts) / YEAR_S
        if T <= 0:
            prev_spot = s
            continue
        ivc = bs.implied_vol(cm, s, strike_call, T, True)
        ivp = bs.implied_vol(pm, s, strike_put, T, False)
        if ivc is None or ivp is None:
            n_skip += 1
            prev_spot = s
            continue
        new_hedge = bs.call_delta(s, strike_call, T, ivc) + bs.put_delta(s, strike_put, T, ivp)
        hedge_costs += abs(new_hedge - hedge) * s * costs.hedge_fee_rate
        hedge = new_hedge
        n_rebal += 1
        since = 0
        prev_spot = s

    pnl = received - terminal_payoff + hedge_pnl - hedge_costs - entry_fees
    return {"pnl_usd": pnl, "naked_pnl_usd": raw_premium - terminal_payoff,
            "hedge_pnl_usd": hedge_pnl, "hedge_costs_usd": hedge_costs,
            "entry_fees_usd": entry_fees, "haircut_usd": raw_premium * costs.bid_haircut,
            "n_rebalances": n_rebal, "n_skip": n_skip,
            "iv_call_entry": iv_c, "iv_put_entry": iv_p, "spot_entry": spot_entry}

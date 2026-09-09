"""studies/lib/options — Black-Scholes sanity, IV round trip, and the hedged
short-strangle engine on synthetic paths (no database)."""
import math

import pytest

from studies.lib.options import bs, chain, pnl_engine


@pytest.mark.parametrize("S,K,T,sigma", [(100, 100, 0.25, 0.6), (60000, 66000, 21 / 365.25, 0.55), (60000, 54000, 0.05, 1.2)])
def test_put_call_parity_r0(S, K, T, sigma):
    assert bs.call_price(S, K, T, sigma) - bs.put_price(S, K, T, sigma) == pytest.approx(S - K, abs=1e-6)
    assert bs.put_delta(S, K, T, sigma) == pytest.approx(bs.call_delta(S, K, T, sigma) - 1.0)
    assert 0.0 <= bs.call_delta(S, K, T, sigma) <= 1.0
    assert bs.vega(S, K, T, sigma) > 0


def test_implied_vol_round_trip():
    S, K, T = 70000.0, 77000.0, 21 / 365.25
    for sigma in (0.35, 0.6, 1.1):
        assert bs.implied_vol(bs.call_price(S, K, T, sigma), S, K, T, True) == pytest.approx(sigma, abs=2e-3)
        assert bs.implied_vol(bs.put_price(S, K * 0.8, T, sigma), S, K * 0.8, T, False) == pytest.approx(sigma, abs=2e-3)
    assert bs.implied_vol(0.0, S, K, T, True) is None
    assert bs.implied_vol(1.0, S, K, 0.0, True) is None
    assert bs.implied_vol(S - K * 0.8 + 0.001, S, K * 0.8, T, True) == bs.IV_MIN  # at intrinsic


def _path(entry_iso: str, expiry_ts: int, start: float, end: float) -> dict[str, float]:
    from datetime import datetime, timedelta, timezone
    d0 = datetime.fromisoformat(entry_iso + "T00:00:00+00:00")
    n = (datetime.fromtimestamp(expiry_ts, tz=timezone.utc).date() - d0.date()).days
    return {(d0 + timedelta(days=i)).date().isoformat(): start + (end - start) * i / n for i in range(n + 1)}


EXPIRY_TS = 1767052800 + 8 * 3600      # 2025-12-30 08:00 UTC (00:00 = 1767052800)
ENTRY = "2025-12-09"                    # T-21


def test_flat_path_pnl_is_premium_minus_costs():
    spot = _path(ENTRY, EXPIRY_TS, 70000.0, 70000.0)
    kc, kp, cm, pm = 77000.0, 63000.0, 900.0, 1100.0
    costs = pnl_engine.Costs()
    h = pnl_engine.hedged_short_pnl(spot, EXPIRY_TS, ENTRY, kc, kp, {}, {}, cm, pm, 0.0, hedge_freq_days=7, costs=costs)
    assert h is not None
    T0 = (EXPIRY_TS - 1765238400) / (365.25 * 86400)
    hedge = bs.call_delta(70000, kc, T0, h["iv_call_entry"]) + bs.put_delta(70000, kp, T0, h["iv_put_entry"])
    expected_costs = 2 * abs(hedge) * 70000 * costs.hedge_fee_rate          # open + close, no rebalances (no marks)
    assert h["hedge_costs_usd"] == pytest.approx(expected_costs, rel=1e-9)
    assert h["hedge_pnl_usd"] == pytest.approx(0.0, abs=1e-9)
    # trader convention: the day counter is not reset on a skipped rebalance, so every day from
    # day 7 to the day before expiry is a skipped attempt (14 of them)
    assert h["n_rebalances"] == 1 and h["n_skip"] == 14
    assert h["pnl_usd"] == pytest.approx((cm + pm) * (1 - costs.bid_haircut) - expected_costs - 2 * costs.option_fee_rate * 70000)
    assert h["naked_pnl_usd"] == pytest.approx(cm + pm)


def test_trending_path_hedge_offsets_call_loss():
    spot = _path(ENTRY, EXPIRY_TS, 70000.0, 84000.0)
    kc, kp, cm, pm = 77000.0, 63000.0, 900.0, 1100.0
    terminal = 84000.0 - kc
    h = pnl_engine.hedged_short_pnl(spot, EXPIRY_TS, ENTRY, kc, kp, {}, {}, cm, pm, terminal, hedge_freq_days=7)
    assert h is not None
    # no rebalances (no marks): hedge P&L is the constant entry hedge × the total move
    T0 = (EXPIRY_TS - 1765238400) / (365.25 * 86400)
    hedge = bs.call_delta(70000, kc, T0, h["iv_call_entry"]) + bs.put_delta(70000, kp, T0, h["iv_put_entry"])
    assert h["hedge_pnl_usd"] == pytest.approx(hedge * 14000.0, rel=1e-9)
    assert h["hedge_pnl_usd"] > 0                                          # net long perp against a short strangle in a rally
    assert h["pnl_usd"] < h["naked_pnl_usd"] + h["hedge_pnl_usd"]           # costs are positive
    naked = pnl_engine.hedged_short_pnl(spot, EXPIRY_TS, ENTRY, kc, kp, {}, {}, cm, pm, terminal, hedge_freq_days=7,
                                        costs=pnl_engine.Costs(0.0, 0.0, 0.0))
    assert naked["pnl_usd"] == pytest.approx(cm + pm - terminal + h["hedge_pnl_usd"])


def test_rebalance_uses_marks_and_charges_delta_change():
    spot = _path(ENTRY, EXPIRY_TS, 70000.0, 70000.0)
    kc, kp = 77000.0, 63000.0
    # constant marks with shrinking time-to-expiry ⇒ higher IV each rebalance ⇒ delta moves ⇒ fees
    marks_c = {d: 900.0 for d in spot}
    marks_p = {d: 1100.0 for d in spot}
    h = pnl_engine.hedged_short_pnl(spot, EXPIRY_TS, ENTRY, kc, kp, marks_c, marks_p, 900.0, 1100.0, 0.0, hedge_freq_days=7)
    assert h["n_rebalances"] == 3 and h["n_skip"] == 0
    flat = pnl_engine.hedged_short_pnl(spot, EXPIRY_TS, ENTRY, kc, kp, {}, {}, 900.0, 1100.0, 0.0, hedge_freq_days=7)
    assert h["hedge_costs_usd"] > flat["hedge_costs_usd"]


def test_spot_near_fallback_order():
    spot = {"2026-01-10": 1.0, "2026-01-13": 2.0}
    assert chain.spot_near(spot, "2026-01-10") == ("2026-01-10", 1.0)
    assert chain.spot_near(spot, "2026-01-11") == ("2026-01-10", 1.0)      # −1 beats +2
    assert chain.spot_near(spot, "2026-01-12") == ("2026-01-13", 2.0)      # +1 first
    assert chain.spot_near(spot, "2026-01-20") is None

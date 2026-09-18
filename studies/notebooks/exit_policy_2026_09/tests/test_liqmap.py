"""Synthetic fixtures for the liquidation-map study, half A (PREREGISTRATION_LIQMAP.md sections 3, 4; L3).

Every fixture is hand-built and its expected value is stated in the test. The market is five identical 1-minute bars
a bin, each with volume 1 and quote volume equal to the price asked for, so VWAP_b is exactly that price and the
aggressor share is exactly `taker_share`. No real array is read and nothing is written.

Prices are 1,000, so the four long tiers sit at 904 / 964 / 984 / 994 with weights 0.40 / 0.30 / 0.20 / 0.10 and the
four short tiers at 1,096 / 1,036 / 1,016 / 1,006 with the same weights.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import liqmap_lib as LM  # noqa: E402

T0 = int(datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp())      # a UTC midnight, on the 300 s grid
P0 = 1_000.0
BINS_PER_DAY = LM.DAY_S // LM.BIN_S                                  # 288
FIELDS = ("e_long", "e_short", "clamp_surplus", "total_mass")
QFIELDS = ("d_up", "share_up", "mass_up", "d_dn", "share_dn", "mass_dn")


def minutes(vwap, high=None, low=None, taker_share=0.5, dead=()) -> dict:
    """Per-bin values -> the 1-minute panel: five identical bars a bin, volume 1, quote volume = the bin's price."""
    v = np.asarray(vwap, dtype=float)
    n = v.size
    h = v if high is None else np.asarray(high, dtype=float)
    lo = v if low is None else np.asarray(low, dtype=float)
    s = np.asarray(taker_share, dtype=float)
    s = np.full(n, float(s)) if s.ndim == 0 else s
    vol = np.ones(n * LM.BIN_MINUTES)
    for b in dead:
        vol[b * LM.BIN_MINUTES:(b + 1) * LM.BIN_MINUTES] = 0.0
    rep = lambda a: np.repeat(a, LM.BIN_MINUTES)                     # noqa: E731
    return {"open": rep(v), "high": rep(h), "low": rep(lo), "close": rep(v), "volume": vol,
            "quote_volume": rep(v) * vol, "taker_buy_volume": rep(s) * vol}


def panel(vwap, oi=None, **kw) -> LM.BinPanel:
    v = np.asarray(vwap, dtype=float)
    oi = np.full(v.size, 1_000.0) if oi is None else np.asarray(oi, dtype=float)
    return LM.build_bin_panel("TEST", oi, T0, minutes(v, **kw), T0)


def flat(n: int, **kw) -> LM.BinPanel:
    return panel(np.full(n, P0), **kw)


NOQ = (np.array([], dtype=np.int64), np.array([], dtype=float))


# --- frozen constants and the grid (3.3) -----------------------------------------------------------

def test_frozen_constants_match_the_preregistration_sections_3_and_4():
    assert (LM.BIN_S, LM.MIN_S, LM.RING_DAYS, LM.WARMUP_DAYS, LM.BAND) == (300, 60, 30, 30, 0.05)
    assert (LM.TIERS, LM.TIER_W, LM.MMR) == ((10.0, 25.0, 50.0, 100.0), (0.40, 0.30, 0.20, 0.10), 0.004)
    assert LM.LONG_MULT == pytest.approx((0.904, 0.964, 0.984, 0.994))
    assert LM.SHORT_MULT == pytest.approx((1.096, 1.036, 1.016, 1.006))
    assert (LM.BURST_LAG, LM.BURST_WINDOW, LM.BURST_MIN_DEFINED, LM.BURST_PCT) == (3, 8_640, 4_320, 99.0)
    assert (LM.TERCILE_DAYS, LM.TERCILE_Q) == (30, 2.0 / 3.0)
    assert (LM.BUCKET, LM.K_MIN, LM.K_MAX, LM.N_BUCKETS) == (1.001, 4_607, 13_822, 9_216)


def test_bucket_arithmetic_at_the_grid_edges():
    assert LM.bucket_of(100.0) == LM.K_MIN and LM.bucket_of(1_000_000.0) == LM.K_MAX
    assert 100.0 < LM.bucket_level(LM.K_MIN) < 100.0 * LM.BUCKET            # every level lies inside the range
    assert 1_000_000.0 < LM.bucket_level(LM.K_MAX) < 1_000_000.0 * LM.BUCKET   # the top bucket straddles the edge
    for k in (LM.K_MIN, 6_911, LM.K_MAX - 1):
        assert LM.bucket_of(LM.bucket_level(k)) == k
        assert LM.bucket_level(k + 1) / LM.bucket_level(k) == pytest.approx(LM.BUCKET)
    np.testing.assert_array_equal(LM.bucket_of(np.array([100.0, 1_000.0, 1_000_000.0])),
                                  np.array([LM.K_MIN, 6_911, LM.K_MAX]))
    np.testing.assert_allclose(LM.bucket_level(np.array([LM.K_MIN, LM.K_MAX])),
                               [LM.bucket_level(LM.K_MIN), LM.bucket_level(LM.K_MAX)])
    for bad in (99.999, 1_000_000.001, float("nan")):                       # an error, never a clip (3.3)
        with pytest.raises(ValueError):
            LM.bucket_of(bad)
    with pytest.raises(ValueError):
        LM.bucket_of(np.array([1_000.0, 99.0]))


def test_traversal_of_a_price_below_the_grid_is_an_error_not_a_clip():
    # the 10x long tier of a 105 VWAP would sit at 94.9: outside [100, 1e6], so the addition raises (3.3)
    bp = panel(np.full(2, 105.0), oi=np.array([1_000.0, 1_100.0]))
    with pytest.raises(ValueError):
        LM.run_map(bp, *NOQ)


# --- bins, bars and the state minute (sections 2, 3) -----------------------------------------------

def test_a_bin_holds_exactly_the_five_bars_opening_at_its_stamp():
    raw = minutes(np.full(4, P0))
    raw["high"][5] = 1_100.0                                   # the first bar of bin 1
    raw["low"][4] = 900.0                                      # the last bar of bin 0
    bp = LM.build_bin_panel("TEST", np.full(4, 1_000.0), T0, raw, T0)
    assert (bp.symbol, bp.t0_s, bp.n) == ("TEST", T0, 4)
    assert bp.high[0] == P0 and bp.high[1] == 1_100.0
    assert bp.low[0] == 900.0 and bp.low[1] == P0
    assert bp.vol[0] == 5.0 and bp.qv[0] == 5 * P0 and bp.vwap[0] == P0 and bp.tb[0] == 2.5


def test_the_map_as_of_a_minute_is_the_last_bin_stamped_five_minutes_earlier():
    for b in (0, 7, 288):
        stamp = T0 + LM.BIN_S * b
        assert LM.map_state_bin(T0, stamp + 300) == b          # as of T + 5 the bin is in
        assert LM.map_state_bin(T0, stamp + 299) == b - 1      # as of T + 4 (and any second before) it is not
        assert LM.map_state_bin(T0, stamp + 240) == b - 1
    assert LM.map_state_bin(T0, T0) == -1
    i0_ts = T0 + 3_600                                         # section 5 entry state: the map as of i0 - 1
    assert LM.map_state_bin(T0, i0_ts - LM.MIN_S) == 10        # bin 10 is stamped i0 - 10 min, bin 11 i0 - 5 min
    assert T0 + LM.BIN_S * 10 <= i0_ts - 360 < T0 + LM.BIN_S * 11


def test_build_bin_panel_offsets_the_panel_and_refuses_a_ragged_start():
    raw = minutes(np.full(6, P0))                              # 30 panel minutes from T0
    bp = LM.build_bin_panel("TEST", np.full(5, 1_000.0), T0 + 600, raw, T0)   # the archive starts two bins later
    assert bp.t0_s == T0 + 600 and bp.n == 5
    assert bp.present[:4].all() and not bp.present[4]          # bin 4 would need minutes 30 ... 34
    assert np.isnan(bp.high[4])
    with pytest.raises(AssertionError):
        LM.build_bin_panel("TEST", np.full(4, 1_000.0), T0 + 60, raw, T0)


def test_a_bin_with_no_present_minute_adds_and_removes_nothing():
    bp = flat(4, oi=np.array([1_000.0, 1_100.0, 1_200.0, 1_300.0]), low=np.full(4, 900.0), dead=(2,))
    assert not bp.present[2]
    for a in (bp.vol, bp.tb, bp.qv, bp.high, bp.low, bp.vwap):
        assert np.isnan(a[2])
    r = LM.run_map(bp, *NOQ)
    assert r.e_long[2] == 0.0 and r.e_short[2] == 0.0
    assert r.total_mass[1] == pytest.approx(100.0)
    assert r.total_mass[2] == pytest.approx(100.0)              # dOI is +100 there, but there is no VWAP to add at
    assert r.e_long[3] == pytest.approx(50.0)                   # bin 3's low still sweeps the long side


# --- additions (3.2) and traversal (3.4) -----------------------------------------------------------

def tier_panel(**kw) -> LM.BinPanel:
    """One addition of dOI = +100 at VWAP 1,000 with aggressor share 0.6, then four bins that sweep it away."""
    oi = np.full(6, 1_100.0)
    oi[0] = 1_000.0
    low = np.array([P0, P0, 970.0, 950.0, 900.0, 900.0])
    high = np.array([P0, P0, P0, P0, P0, 1_050.0])
    return flat(6, oi=oi, low=low, high=high, taker_share=0.6, **kw)


def test_additions_split_over_the_tiers_at_the_bars_vwap():
    r = LM.run_map(tier_panel(), *NOQ)
    assert r.total_mass[1] == pytest.approx(100.0)              # exactly dOI enters, 60 long and 40 short
    assert r.e_long[1] == 0.0 and r.e_short[1] == 0.0
    assert r.e_long[2] == pytest.approx(60 * (0.20 + 0.10))     # low 970 takes the 50x and 100x long tiers
    assert r.e_long[3] == pytest.approx(60 * 0.30)              # low 950 takes the 25x tier
    assert r.e_long[4] == pytest.approx(60 * 0.40)              # low 900 takes the 10x tier
    assert r.e_long[5] == 0.0
    assert r.e_short[4] == 0.0
    assert r.e_short[5] == pytest.approx(40 * (0.10 + 0.20 + 0.30))   # high 1,050 takes all but the 10x short tier
    assert r.total_mass[5] == pytest.approx(40 * 0.40)


def test_mass_added_in_a_bin_is_exposed_to_traversal_only_from_the_next_bin():
    bp = flat(3, oi=np.array([1_000.0, 1_100.0, 1_100.0]), low=np.array([P0, 900.0, 900.0]),
              high=np.array([P0, 1_100.0, 1_100.0]))
    r = LM.run_map(bp, *NOQ)
    assert r.e_long[1] == 0.0 and r.e_short[1] == 0.0           # the bin's own range never reaches its own additions
    assert r.total_mass[1] == pytest.approx(100.0)
    assert r.e_long[2] == pytest.approx(50.0) and r.e_short[2] == pytest.approx(50.0)
    assert r.total_mass[2] == 0.0


def test_traversal_uses_the_exact_level_rule_on_both_sides():
    def e_at(bar_price: float) -> LM.MapRun:
        oi = np.array([1_000.0, 1_100.0, 1_100.0])              # bin 2 has dOI = 0: traversal only
        v = np.array([P0, P0, bar_price])
        return LM.run_map(panel(v, oi=oi, high=v, low=v), *NOQ)

    lvl = LM.bucket_level(LM.bucket_of(P0 * LM.LONG_MULT[3]))    # the 100x long tier's bucket level
    assert e_at(lvl).e_long[2] == pytest.approx(50 * 0.10)       # level >= low_b is removed
    assert e_at(np.nextafter(lvl, np.inf)).e_long[2] == 0.0      # one ulp above the level it is not
    lvl = LM.bucket_level(LM.bucket_of(P0 * LM.SHORT_MULT[3]))
    assert e_at(lvl).e_short[2] == pytest.approx(50 * 0.10)      # level <= high_b is removed
    assert e_at(np.nextafter(lvl, 0.0)).e_short[2] == 0.0


def test_an_up_bin_removes_e_then_adds_exactly_the_open_interest_increase():
    bp = flat(3, oi=np.array([1_000.0, 1_100.0, 1_250.0]), low=np.array([P0, P0, 900.0]))
    r = LM.run_map(bp, *NOQ)
    assert r.total_mass[1] == pytest.approx(100.0)
    assert r.e_long[2] == pytest.approx(50.0)                    # E is removed first
    assert r.total_mass[2] == pytest.approx(100.0 - 50.0 + 150.0)
    assert r.clamp_surplus[2] == 0.0


def test_a_down_bin_with_e_above_the_decrease_removes_e_and_adds_nothing():
    bp = flat(3, oi=np.array([1_000.0, 1_100.0, 1_090.0]), low=np.array([P0, P0, 900.0]))
    r = LM.run_map(bp, *NOQ)
    assert r.e_long[2] == pytest.approx(50.0)                    # D = 10 < E = 50, so R = 0
    assert r.total_mass[2] == pytest.approx(50.0)                # the short side is untouched and nothing is added
    assert r.clamp_surplus[2] == 0.0


def test_the_clamp_records_its_surplus_and_never_touches_e():
    empty = LM.run_map(flat(3, oi=np.array([1_000.0, 990.0, 990.0])), *NOQ)
    assert empty.e_long[1] == 0.0 and empty.total_mass[1] == 0.0
    assert empty.clamp_surplus[1] == pytest.approx(10.0)         # an empty map: the whole decrease is surplus
    bp = flat(3, oi=np.array([1_000.0, 1_100.0, 900.0]), low=np.array([P0, P0, 900.0]))
    r = LM.run_map(bp, *NOQ)
    assert r.e_long[2] == pytest.approx(50.0)                    # D = 200, E = 50 -> R0 = 150, mass left 50
    assert r.clamp_surplus[2] == pytest.approx(100.0)
    assert r.total_mass[2] == 0.0


def test_the_open_interest_decrease_is_removed_in_proportion_to_the_mass_present():
    # bin 1 adds 100 (60 long, 40 short); bin 2 is a 20 decrease with no traversal -> every bucket loses a fifth
    bp = flat(3, oi=np.array([1_000.0, 1_100.0, 1_080.0]), taker_share=0.6)
    r = LM.run_map(bp, np.array([1, 2]), np.array([P0, P0]))
    assert r.e_long[2] == 0.0 and r.e_short[2] == 0.0 and r.clamp_surplus[2] == 0.0
    assert r.total_mass[2] == pytest.approx(80.0)
    assert r.mass_dn[1] == pytest.approx(0.8 * r.mass_dn[0])     # the same bucket, four fifths of the mass
    assert r.share_dn[1] == pytest.approx(r.share_dn[0])         # a proportional removal leaves every share alone
    assert r.share_up[1] == pytest.approx(r.share_up[0])
    assert r.d_dn[1] == pytest.approx(r.d_dn[0])


def test_zero_and_nan_snapshots_are_missing_and_a_gap_is_never_spanned():
    oi = np.array([1_000.0, 1_100.0, 0.0, 1_200.0, 1_300.0, np.nan, 1_000.0])
    bp = flat(7, oi=oi, low=np.array([P0, P0, 900.0, P0, P0, P0, P0]))
    assert np.isnan(bp.oi[2]) and np.isnan(bp.oi[5])             # 3.1: a zero row is missing, not a close
    r = LM.run_map(bp, *NOQ)
    assert r.total_mass[1] == pytest.approx(100.0)
    assert r.e_long[2] == pytest.approx(50.0)                    # traversal still runs on a bin that has price
    assert r.clamp_surplus[2] == 0.0 and r.total_mass[2] == pytest.approx(50.0)
    assert r.total_mass[3] == pytest.approx(50.0)                # bin 3 has no dOI either: the gap is dropped
    assert r.total_mass[4] == pytest.approx(150.0)               # 1,300 - 1,200, not 1,300 - 1,100
    assert r.clamp_surplus[5] == 0.0 and r.clamp_surplus[6] == 0.0
    assert r.total_mass[6] == pytest.approx(150.0)               # the 300 decrease across the nan is dropped


# --- the ring (3.3) --------------------------------------------------------------------------------

def ring_panel(n: int, add_bins=(1,)) -> LM.BinPanel:
    oi = np.full(n, 1_000.0)
    for b in add_bins:
        oi[b:] += 100.0
    return flat(n, oi=oi)


def test_the_ring_clears_the_new_days_layer_before_that_bins_removals_and_additions():
    n = LM.RING_DAYS * BINS_PER_DAY + 1
    oi = np.full(n, 1_100.0)
    oi[0] = 1_000.0                                              # +100 at bin 1 (UTC day 0)
    oi[-1] = 1_000.0                                             # -100 at the first bin of UTC day 30
    last, first = LM.RING_DAYS * BINS_PER_DAY - 1, LM.RING_DAYS * BINS_PER_DAY
    r = LM.run_map(flat(n, oi=oi), np.array([1, last, first]), np.full(3, P0))
    assert r.total_mass[1] == pytest.approx(100.0)
    assert r.total_mass[last] == pytest.approx(100.0)             # the last bin of day 29 still holds it
    assert r.total_mass[first] == 0.0                            # day 30's first bin clears day 0's layer
    assert r.share_dn[1] == pytest.approx(0.5) and np.isnan(r.share_dn[2])
    assert np.isnan(r.share_up[2]) and np.isnan(r.d_up[2]) and np.isnan(r.mass_up[2])
    assert r.e_long[first] == 0.0                                # the clamp meets an empty map after the rollover
    assert r.clamp_surplus[first] == pytest.approx(100.0)


def test_a_cluster_sums_the_ring_layers_and_a_rollover_takes_one_layer_away():
    n = LM.RING_DAYS * BINS_PER_DAY + 1
    oi = np.full(n, 1_200.0)
    oi[0] = 1_000.0                                              # +100 at bin 1 (day 0)
    oi[1:BINS_PER_DAY] = 1_100.0                                 # +100 at bin 288 (day 1)
    last, first = LM.RING_DAYS * BINS_PER_DAY - 1, LM.RING_DAYS * BINS_PER_DAY
    r = LM.run_map(flat(n, oi=oi), np.array([BINS_PER_DAY, last, first]), np.full(3, P0))
    assert r.total_mass[BINS_PER_DAY] == pytest.approx(200.0)
    assert r.mass_dn[0] == pytest.approx(2 * 50 * 0.30)          # the 25x bucket holds both days
    assert r.mass_dn[1] == pytest.approx(2 * 50 * 0.30)
    assert r.mass_dn[2] == pytest.approx(50 * 0.30)              # day 0's layer left the ring, day 1's stayed
    assert r.total_mass[first] == pytest.approx(100.0)


def test_a_cluster_tie_takes_the_bucket_nearest_the_price():
    # long side: 30 units land at 964 (25x of a 1,000 VWAP) and 30 at 958.24 (10x of a 1,060 VWAP)
    oi = np.array([1_000.0, 1_100.0, 1_175.0])
    v = np.array([P0, P0, 1_060.0])
    r = LM.run_map(panel(v, oi=oi, high=v, low=v, taker_share=1.0), np.array([2]), np.array([P0]))
    near, far = LM.bucket_of(P0 * 0.964), LM.bucket_of(1_060.0 * 0.904)
    assert near - far == 6                                        # two distinct buckets, six apart
    assert r.mass_dn[0] == pytest.approx(30.0) and r.share_dn[0] == pytest.approx(30.0 / 90.0)
    assert r.d_dn[0] == pytest.approx(LM.bucket_level(near) / P0 - 1.0)
    assert r.d_dn[0] != pytest.approx(LM.bucket_level(far) / P0 - 1.0)
    # short side: 30 at 1,036 (25x of a 1,000 VWAP) and 30 at 1,030.24 (10x of a 940 VWAP); the lower one is nearer
    v = np.array([P0, P0, 940.0])
    r = LM.run_map(panel(v, oi=oi, high=v, low=v, taker_share=0.0), np.array([2]), np.array([P0]))
    near, far = LM.bucket_of(940.0 * 1.096), LM.bucket_of(P0 * 1.036)
    assert far - near == 5
    assert r.mass_up[0] == pytest.approx(30.0) and r.share_up[0] == pytest.approx(30.0 / 90.0)
    assert r.d_up[0] == pytest.approx(LM.bucket_level(near) / P0 - 1.0)


# --- the control map (3.7) -------------------------------------------------------------------------

def wavy(n: int, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    i = np.arange(n, dtype=float)
    vwap = P0 * (1.0 + 0.010 * np.sin(i / 13.0) + 0.002 * np.cos(i / 3.0))
    return vwap, vwap * 1.001, vwap * 0.999


def test_the_control_map_is_the_same_code_path_with_the_information_replaced():
    n = 300
    vwap, high, low = wavy(n, 0)
    bp = panel(vwap, oi=1_000.0 + np.arange(n), high=high, low=low, taker_share=0.5)
    q_bin = np.arange(5, n, 7)
    q_price = vwap[q_bin]
    actual = LM.run_map(bp, q_bin, q_price)
    control = LM.run_map(bp, q_bin, q_price, control=True)
    for name in FIELDS + QFIELDS:                                 # dOI = +1 a bin and tb = vol / 2: identical maps
        np.testing.assert_array_equal(getattr(actual, name), getattr(control, name), err_msg=name)
    assert np.nanmax(actual.total_mass) > 0.0


def test_the_control_map_ignores_scrambled_open_interest_and_taker_flow():
    n = 300
    rng = np.random.default_rng(5)
    vwap, high, low = wavy(n, 0)
    base = panel(vwap, oi=1_000.0 + np.arange(n), high=high, low=low, taker_share=0.5)
    other = panel(vwap, oi=1_000.0 + 7.0 * rng.permutation(n), high=high, low=low,
                  taker_share=rng.uniform(0.05, 0.95, n))
    q_bin = np.arange(5, n, 7)
    q_price = vwap[q_bin]
    a = LM.run_map(base, q_bin, q_price, control=True)
    b = LM.run_map(other, q_bin, q_price, control=True)
    for name in FIELDS + QFIELDS:
        np.testing.assert_array_equal(getattr(a, name), getattr(b, name), err_msg=name)
    assert not np.array_equal(LM.run_map(base, q_bin, q_price).total_mass,
                              LM.run_map(other, q_bin, q_price).total_mass)    # the actual maps do differ


# --- the cluster search, the density cut and the undefined cases (section 4) ------------------------

def test_the_cluster_search_reports_the_densest_bucket_in_each_band():
    r = LM.run_map(tier_panel(), np.array([1, 1]), np.array([P0, 2_000.0]))
    assert r.mass_dn[0] == pytest.approx(60 * 0.30)               # 964 holds 18 of the 36 in [950, 1,000)
    assert r.share_dn[0] == pytest.approx(0.5)
    assert r.d_dn[0] == pytest.approx(LM.bucket_level(LM.bucket_of(P0 * 0.964)) / P0 - 1.0)
    assert r.mass_up[0] == pytest.approx(40 * 0.30)               # 1,036 holds 12 of the 24 in (1,000, 1,050]
    assert r.share_up[0] == pytest.approx(0.5)
    assert r.d_up[0] == pytest.approx(LM.bucket_level(LM.bucket_of(P0 * 1.036)) / P0 - 1.0)
    assert r.d_dn[0] < 0.0 < r.d_up[0]                            # d is level / P - 1 on both sides
    for name in QFIELDS:                                          # a band with no mass on that side is undefined
        assert np.isnan(getattr(r, name)[1])


def test_a_query_answers_nan_before_the_warm_up_and_past_a_causality_cut():
    held = ring_panel(6)                                          # one addition at bin 1, never swept away
    q_bin, q_price = np.array([1, 2, 3, 4, 5]), np.full(5, P0)
    r = LM.run_map(held, q_bin, q_price, warmup_bin=3)
    assert np.isnan(r.share_dn[0]) and np.isnan(r.share_dn[1])    # 3.6: nothing is read before the warm-up
    assert r.share_dn[2] == pytest.approx(0.5) and r.share_dn[4] == pytest.approx(0.5)
    r = LM.run_map(held, q_bin, q_price, n_bins=4)
    assert np.isfinite(r.share_dn[2]) and np.isnan(r.share_dn[3]) and np.isnan(r.share_dn[4])
    assert np.isfinite(r.e_long[3]) and np.isnan(r.e_long[4]) and np.isnan(r.total_mass[5])
    assert np.isnan(LM.run_map(held, np.array([1]), np.array([np.nan])).share_dn[0])
    with pytest.raises(ValueError):
        LM.run_map(held, np.array([3, 1]), np.full(2, P0))


def test_cluster_defined_applies_the_trailing_density_cut():
    share = np.array([0.50, 0.45, 0.40, np.nan, 0.60])
    cut = np.array([0.45, 0.45, 0.45, 0.45, np.nan])
    np.testing.assert_array_equal(LM.cluster_defined(share, cut), [True, True, False, False, False])


def test_the_tercile_cut_reads_the_trailing_thirty_days_of_states_with_mass():
    hours = 24 * 45
    ts = T0 + 3_600 * np.arange(hours)
    share = np.arange(hours) / hours                              # strictly increasing, so the window edges show
    share[5] = np.nan                                             # a state with no mass in the band is not counted
    win = LM.TERCILE_DAYS * LM.DAY_S
    at = np.array([ts[0] + win - 3_600, ts[0] + win, ts[0] + win + 3_600, ts[0]])
    cut = LM.tercile_cut(ts, share, at)
    assert np.isnan(cut[0]) and np.isnan(cut[3])                  # the window precedes the first hourly state
    for i in (1, 2):
        keep = (ts >= at[i] - win) & (ts < at[i]) & np.isfinite(share)
        assert keep.sum() == 24 * LM.TERCILE_DAYS - 1             # half open on the right, closed on the left
        assert cut[i] == pytest.approx(float(np.quantile(share[keep], 2.0 / 3.0)))
    assert cut[2] != cut[1]
    assert np.isnan(LM.tercile_cut(ts, np.full(hours, np.nan), at)[1])    # a window with no state that has mass


# --- the burst (section 4) -------------------------------------------------------------------------

def burst_inputs(n: int = 12_000, seed: int = 3) -> tuple[np.ndarray, np.ndarray]:
    return np.random.default_rng(seed).random(n), np.ones(n, dtype=bool)


def test_the_burst_sum_needs_all_three_bins_to_have_price():
    e, has_price = burst_inputs(n=20)
    has_price[10] = False
    S, _, _ = LM.burst_series(e, has_price, 0)
    assert np.isnan(S[0]) and np.isnan(S[1])
    assert S[9] == pytest.approx(e[7:10].sum())
    assert np.isnan(S[10]) and np.isnan(S[11]) and np.isnan(S[12])
    assert S[13] == pytest.approx(e[11:14].sum())


def test_the_burst_threshold_uses_only_its_lagged_window():
    e, has_price = burst_inputs()
    _, theta, _ = LM.burst_series(e, has_price, 0)
    b = 6_000
    spiked = e.copy()
    spiked[b] += 1_000.0                                          # S_b, S_{b+1} and S_{b+2} all change
    _, theta_s, _ = LM.burst_series(spiked, has_price, 0)
    assert theta_s[b] == theta[b] and theta_s[b + 1] == theta[b + 1] and theta_s[b + 2] == theta[b + 2]
    assert theta_s[b + 3] != theta[b + 3]                         # S_b first enters its reference at b + 3
    before = e.copy()
    before[9_000 - LM.BURST_WINDOW - 6] += 1_000.0                # its three sums all fall before the window
    _, theta_b, _ = LM.burst_series(before, has_price, 0)
    assert theta_b[9_000] == theta[9_000]


def test_the_burst_threshold_is_missing_below_the_defined_minimum():
    e, has_price = burst_inputs()
    _, theta, flag = LM.burst_series(e, has_price, 0)
    assert np.isnan(theta[LM.BURST_MIN_DEFINED + 3]) and np.isfinite(theta[LM.BURST_MIN_DEFINED + 4])
    _, warm, warm_flag = LM.burst_series(e, has_price, 8_000)     # only bins at or after the warm-up count
    assert np.all(np.isnan(warm)) and not warm_flag.any()
    hp = has_price.copy()
    hp[:9_000] = False                                            # fewer than 4,320 defined sums anywhere
    _, sparse, _ = LM.burst_series(e, hp, 0)
    assert np.all(np.isnan(sparse))


def test_the_burst_flag_fires_when_the_sum_reaches_the_threshold():
    n = 12_000
    e, has_price = np.ones(n), np.ones(n, dtype=bool)
    S, theta, flag = LM.burst_series(e, has_price, 0)
    assert theta[6_000] == pytest.approx(3.0) and S[6_000] == pytest.approx(3.0)
    assert flag[6_000]                                            # S >= theta, so equality counts
    assert not flag[:LM.BURST_MIN_DEFINED + 4].any()              # missing threshold, no burst
    quiet = e.copy()
    quiet[6_000] = 0.5
    S2, theta2, flag2 = LM.burst_series(quiet, has_price, 0)
    assert theta2[6_000] == pytest.approx(3.0) and not flag2[6_000] and not flag2[6_002]
    assert flag2[6_003]


# --- causality (L4's cut, on a synthetic panel) ----------------------------------------------------

def test_truncating_the_pass_leaves_every_earlier_bin_query_and_threshold_unchanged():
    n, cut = 9_000, 6_000
    rng = np.random.default_rng(11)
    vwap, high, low = wavy(n, 0)
    oi = np.maximum(5_000.0 + np.cumsum(rng.normal(0.0, 3.0, n)), 100.0)
    oi[[1_000, 3_000, 7_000]] = 0.0                               # unusable snapshots on both sides of the cut
    bp = panel(vwap, oi=oi, high=high, low=low, taker_share=rng.uniform(0.2, 0.8, n))
    q_bin = np.arange(50, n, 37)
    q_price = vwap[q_bin]
    full = LM.run_map(bp, q_bin, q_price)
    part = LM.run_map(bp, q_bin, q_price, n_bins=cut)
    assert np.nanmax(full.e_long) > 0.0 and np.nanmax(full.e_short) > 0.0 and np.nanmax(full.clamp_surplus) >= 0.0
    for name in FIELDS:
        np.testing.assert_array_equal(getattr(full, name)[:cut], getattr(part, name)[:cut], err_msg=name)
        assert np.all(np.isnan(getattr(part, name)[cut:])), name
    keep = q_bin < cut
    for name in QFIELDS:
        np.testing.assert_array_equal(getattr(full, name)[keep], getattr(part, name)[keep], err_msg=name)
        assert np.all(np.isnan(getattr(part, name)[~keep])), name
    hp_cut = bp.present.copy()
    hp_cut[cut:] = False
    _, theta_full, _ = LM.burst_series(full.e_short, bp.present, 0)
    _, theta_part, _ = LM.burst_series(part.e_short, hp_cut, 0)
    assert np.isfinite(theta_full[:cut]).any()
    np.testing.assert_array_equal(theta_full[:cut], theta_part[:cut])


# --- the whole pass against a brute-force reference ------------------------------------------------

def naive_run(bp: LM.BinPanel, q_bin, q_price, control: bool = False) -> dict:
    """Section 3 written without any index bookkeeping: whole-array masks, the ring summed every time."""
    levels = LM.bucket_level(np.arange(LM.K_MIN, LM.K_MAX + 1))
    ring = np.zeros((2, LM.RING_DAYS, LM.N_BUCKETS))
    out = {k: np.zeros(bp.n) for k in FIELDS} | {k: np.full(len(q_bin), np.nan) for k in QFIELDS}
    prev_day, layer = None, 0
    for b in range(bp.n):
        day = (bp.t0_s + LM.BIN_S * b) // LM.DAY_S
        if day != prev_day:
            layer = int(day % LM.RING_DAYS)
            ring[:, layer, :] = 0.0
            prev_day = day
        el = es = 0.0
        if bp.present[b]:
            m = levels >= bp.low[b]
            el = float(ring[0][:, m].sum())
            ring[0][:, m] = 0.0
            m = levels <= bp.high[b]
            es = float(ring[1][:, m].sum())
            ring[1][:, m] = 0.0
        out["e_long"][b], out["e_short"][b] = el, es
        d = bp.oi[b] - bp.oi[b - 1] if b > 0 else np.nan
        if not control and np.isfinite(d) and d < 0.0:
            r0 = max(0.0, -d - (el + es))
            total = float(ring.sum())
            r = min(r0, total)
            out["clamp_surplus"][b] = r0 - r
            if r > 0.0:
                ring *= 1.0 - r / total
        if bp.present[b] and np.isfinite(d):
            share = bp.tb[b] / bp.vol[b]
            add_l, add_s = (0.5, 0.5) if control else ((d * share, d * (1 - share)) if d > 0 else (0.0, 0.0))
            for mult, w in zip(LM.LONG_MULT, LM.TIER_W):
                ring[0, layer, LM.bucket_of(bp.vwap[b] * mult) - LM.K_MIN] += add_l * w
            for mult, w in zip(LM.SHORT_MULT, LM.TIER_W):
                ring[1, layer, LM.bucket_of(bp.vwap[b] * mult) - LM.K_MIN] += add_s * w
        out["total_mass"][b] = float(ring.sum())
        for i in np.flatnonzero(np.asarray(q_bin) == b):
            p = float(np.asarray(q_price)[i])
            for side, (m, up) in enumerate((((levels > p) & (levels <= p * 1.05), True),
                                            ((levels >= p * 0.95) & (levels < p), False))):
                tot = ring[1 - side].sum(axis=0)[m]
                if tot.sum() <= 0.0:
                    continue
                j = int(np.argmax(tot)) if up else len(tot) - 1 - int(np.argmax(tot[::-1]))
                sfx = "up" if up else "dn"
                out["mass_" + sfx][i] = tot[j]
                out["share_" + sfx][i] = tot[j] / tot.sum()
                out["d_" + sfx][i] = levels[m][j] / p - 1.0
    return out


def test_the_pass_matches_a_brute_force_reference_on_a_busy_panel():
    n = 500
    rng = np.random.default_rng(23)
    i = np.arange(n, dtype=float)
    vwap = P0 * (1.0 + 0.02 * np.sin(i / 31.0) + 0.004 * np.cos(i / 2.0))
    high, low = vwap * (1 + 0.002 * rng.random(n)), vwap * (1 - 0.002 * rng.random(n))
    oi = np.maximum(3_000.0 + np.cumsum(rng.normal(0.0, 40.0, n)), 50.0)
    oi[[70, 71, 300]] = 0.0                                       # unusable snapshots
    oi[400] = 1.0                                                 # a decrease far larger than the map: the clamp binds
    bp = panel(vwap, oi=oi, high=high, low=low, taker_share=rng.uniform(0.1, 0.9, n), dead=(120, 121))
    q_bin = np.arange(4, n, 3)
    q_price = vwap[q_bin] * (1.0 + 0.001 * rng.normal(size=q_bin.size))
    for control in (False, True):
        got = LM.run_map(bp, q_bin, q_price, control=control)
        ref = naive_run(bp, q_bin, q_price, control=control)
        for name in FIELDS + QFIELDS:
            np.testing.assert_allclose(getattr(got, name), ref[name], rtol=1e-11, atol=1e-9,
                                       err_msg=f"{name} (control={control})")
        assert np.nanmax(ref["e_long"]) > 0.0 and np.nanmax(ref["e_short"]) > 0.0
        assert np.isfinite(ref["share_up"]).sum() > 50 and np.isfinite(ref["share_dn"]).sum() > 50
        assert (np.nanmax(ref["clamp_surplus"]) > 0.0) is not control   # 3.5 runs only on the actual map
        if not control:
            assert (np.diff(ref["total_mass"]) < 0.0).any()       # proportional removals do fire


# === section 5: outcomes, matching, classification (half B) ===

'''Synthetic fixtures for the liquidation-map study's outcomes, matching and statistics
(PREREGISTRATION_LIQMAP.md section 5, precondition L3; the map's own fixtures are tests/test_liqmap_map.py).

A flat_bars 1-minute market at 100.00 +- 0.05 with every minute present; each test changes only the minutes that matter.
The cluster above sits at 100.50 (1 % through at 101.505, 1 % back at 99.495) and the cluster below at 99.50 (1 %
through at 98.505, 1 % back at 100.495), so the flat_bars baseline neither touches a level nor resolves a turn. Every
boundary asserts both the included and the excluded twin. No real series, no map and no continuation value on real
data is touched here: the study is pre-registered.
'''

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import chento_lib as CL  # noqa: E402
import liqmap_lib as S  # noqa: E402
import micro_lib as M  # noqa: E402
import test_micro_events as TME  # noqa: E402  (the frozen microstructure fixtures, re-run through match_liq)

N = 3_000
M0 = 500                      # the hourly state under test
UP = 100.5                    # the cluster above:  1 % through 101.505, 1 % back 99.495
DN = 99.5                     # the cluster below:  1 % through 98.505,  1 % back 100.495
T0_STATS = TME.T0                          # 1_700_006_400, a whole multiple of 300 s


def flat_bars(n: int = N):
    """high, low, present of the flat_bars market: no level touched, no 1 % band crossed."""
    return np.full(n, 100.05), np.full(n, 99.95), np.ones(n, dtype=bool)


def bar(high, low, i: int, h: float, lo: float) -> None:
    high[i], low[i] = h, lo


# --- frozen numbers --------------------------------------------------------------------------------

def test_frozen_constants_match_the_preregistration_section_5():
    assert (S.TOUCH_MIN, S.TURN_MIN, S.TURN_BAND, S.STATE_WINDOW) == (1_440, 240, 0.01, 1_680)
    assert S.STATE_WINDOW == S.TOUCH_MIN + S.TURN_MIN
    assert (S.BIN_S, S.MIN_S) == (300, 60)
    assert (S.BIN_R, S.WINDOW_SHARE, S.MIN_CONTROLS, S.ERA_DAYS) == (0.25, 0.05, 3, 365)
    assert (S.MIN_EVENT_TRADES, S.SIGN_CONTROL_MIN, S.EQUIVALENCE_R, S.ALPHA, S.POWER_Z) == (30, 10, 0.10, 0.05, 2.49)
    assert S.BANDS == {"touch": (-0.02, 0.02), "turn": (-0.05, 0.05), "R": (-0.10, 0.10)}
    assert S.CLAIMED_SIGN == {"Q1": +1, "Q2": -1} and S.NO_EVENT == np.iinfo(np.int64).max
    # section 5: the statistic's numbers are inherited unchanged from the microstructure stage
    assert (S.BIN_R, S.WINDOW_SHARE, S.MIN_CONTROLS) == (M.BIN_R, M.WINDOW_SHARE, M.MIN_CONTROLS)
    assert (S.MIN_EVENT_TRADES, S.SIGN_CONTROL_MIN) == (M.MIN_EVENT_TRADES, M.SIGN_CONTROL_MIN)
    assert (S.EQUIVALENCE_R, S.ALPHA, S.POWER_Z, S.NO_EVENT) == (M.EQUIVALENCE_R, M.ALPHA, M.POWER_Z, M.NO_EVENT)
    assert [M.window_minutes(p) for p in ("chento", "squeeze_bull")] == [216, 144]        # W = 5 % of the horizon


# --- Q1: touch ---------------------------------------------------------------------------------------

def test_the_state_minute_never_counts_and_the_touch_is_the_first_qualifying_minute():
    h, l, p = flat_bars()
    bar(h, l, M0, 200.0, 99.95)                          # the state's own bar is already above the level
    assert S.touch_turn(h, l, p, M0, UP, +1) == (0, -1, -1)
    bar(h, l, M0 + 3, 100.49, 99.95)                     # below the level: not a touch
    bar(h, l, M0 + 7, 100.50, 99.95)                     # exactly at the level: the touch (>=)
    bar(h, l, M0 + 9, 100.80, 99.95)
    assert S.touch_turn(h, l, p, M0, UP, +1) == (1, 0, M0 + 7)


def test_the_touch_window_is_1440_minutes_and_the_state_window_is_1680():
    h, l, p = flat_bars()
    bar(h, l, M0 + 1_440, 100.6, 99.95)
    assert S.touch_turn(h, l, p, M0, UP, +1) == (1, 0, M0 + 1_440)
    h, l, p = flat_bars()
    bar(h, l, M0 + 1_441, 100.6, 99.95)                  # one minute past the window
    assert S.touch_turn(h, l, p, M0, UP, +1) == (0, -1, -1)
    assert bool(S.state_window_ok(N - 1 - 1_680, N)) and not bool(S.state_window_ok(N - 1_680, N))
    assert S.state_window_ok(np.array([1_319, 1_320]), 3_000).tolist() == [True, False]


def test_a_missing_minute_is_skipped_but_still_consumes_the_window():
    h, l, p = flat_bars()
    bar(h, l, M0 + 5, 100.6, 99.95)
    p[M0 + 5] = False                                    # would have touched, but the minute is missing
    bar(h, l, M0 + 9, 100.6, 99.95)
    assert S.touch_turn(h, l, p, M0, UP, +1) == (1, 0, M0 + 9)
    bar(h, l, M0 + 9 + 240, 100.05, 99.40)               # the last minute of the turn scan: a turn
    assert S.touch_turn(h, l, p, M0, UP, +1) == (1, 1, M0 + 9)
    p[M0 + 9 + 240] = False                              # missing: skipped, and the clock is not rewound
    assert S.touch_turn(h, l, p, M0, UP, +1) == (1, 0, M0 + 9)


# --- Q1: turn ----------------------------------------------------------------------------------------

def test_through_inside_the_touching_minute_is_not_a_turn():
    h, l, p = flat_bars()
    bar(h, l, M0 + 10, 101.6, 99.95)                     # >= 1.01 x level in the touching minute
    bar(h, l, M0 + 30, 100.05, 99.40)                    # a later move back cannot rescue it
    assert S.touch_turn(h, l, p, M0, UP, +1) == (1, 0, M0 + 10)
    h, l, p = flat_bars()
    bar(h, l, M0 + 10, 101.50, 99.95)                    # just short of 101.505: only a touch
    bar(h, l, M0 + 30, 100.05, 99.40)
    assert S.touch_turn(h, l, p, M0, UP, +1) == (1, 1, M0 + 10)


def test_back_before_through_is_a_turn_and_through_before_back_is_not():
    h, l, p = flat_bars()
    bar(h, l, M0 + 10, 100.6, 99.95)
    bar(h, l, M0 + 20, 100.05, 99.40)                    # back 1 % first
    bar(h, l, M0 + 30, 101.6, 99.95)
    assert S.touch_turn(h, l, p, M0, UP, +1) == (1, 1, M0 + 10)
    h, l, p = flat_bars()
    bar(h, l, M0 + 10, 100.6, 99.95)
    bar(h, l, M0 + 20, 101.6, 99.95)                     # through 1 % first
    bar(h, l, M0 + 30, 100.05, 99.40)
    assert S.touch_turn(h, l, p, M0, UP, +1) == (1, 0, M0 + 10)


def test_a_minute_meeting_both_is_scored_through():
    h, l, p = flat_bars()
    bar(h, l, M0 + 10, 100.6, 99.95)
    bar(h, l, M0 + 20, 101.6, 99.40)                     # 1 % through and 1 % back in the same minute
    assert S.touch_turn(h, l, p, M0, UP, +1) == (1, 0, M0 + 10)


def test_no_resolution_within_240_minutes_is_not_a_turn():
    h, l, p = flat_bars()
    bar(h, l, M0 + 10, 100.6, 99.95)
    bar(h, l, M0 + 10 + 240, 100.05, 99.40)              # the 240th minute after the touch: inside the scan
    assert S.touch_turn(h, l, p, M0, UP, +1) == (1, 1, M0 + 10)
    h, l, p = flat_bars()
    bar(h, l, M0 + 10, 100.6, 99.95)
    bar(h, l, M0 + 10 + 241, 100.05, 99.40)              # one minute later: a stall is not a turn
    assert S.touch_turn(h, l, p, M0, UP, +1) == (1, 0, M0 + 10)


# --- Q1: the mirrors on the cluster below --------------------------------------------------------------

def test_the_cluster_below_mirrors_every_clause():
    h, l, p = flat_bars()
    bar(h, l, M0, 100.05, 1.0)                           # the state's own bar is already below the level
    assert S.touch_turn(h, l, p, M0, DN, -1) == (0, -1, -1)
    bar(h, l, M0 + 6, 99.51, 99.51)                      # above the level: not a touch
    bar(h, l, M0 + 10, 100.05, 99.50)                    # exactly at the level: the touch (<=)
    assert S.touch_turn(h, l, p, M0, DN, -1) == (1, 0, M0 + 10)
    bar(h, l, M0 + 30, 100.60, 99.95)                    # back 1 % above the level: the turn
    assert S.touch_turn(h, l, p, M0, DN, -1) == (1, 1, M0 + 10)
    h, l, p = flat_bars()
    bar(h, l, M0 + 10, 100.05, 98.40)                    # 1 % through inside the touching minute
    bar(h, l, M0 + 30, 100.60, 99.95)
    assert S.touch_turn(h, l, p, M0, DN, -1) == (1, 0, M0 + 10)
    h, l, p = flat_bars()
    bar(h, l, M0 + 10, 100.05, 99.50)
    bar(h, l, M0 + 20, 100.05, 98.40)                    # through before back
    bar(h, l, M0 + 30, 100.60, 99.95)
    assert S.touch_turn(h, l, p, M0, DN, -1) == (1, 0, M0 + 10)
    h, l, p = flat_bars()
    bar(h, l, M0 + 10, 100.05, 99.50)
    bar(h, l, M0 + 20, 100.60, 98.40)                    # both in one minute
    assert S.touch_turn(h, l, p, M0, DN, -1) == (1, 0, M0 + 10)


# --- Q1: the paired turn -------------------------------------------------------------------------------

def test_the_paired_turn_needs_both_touched_and_is_zero_at_equal_levels():
    """Section 5 Q1: delta = turn_A - turn_B on states where both the map's and the control map's level was touched."""
    h, l, p = flat_bars()
    bar(h, l, M0 + 10, 100.60, 99.95)                    # touches A = 100.5 only
    bar(h, l, M0 + 30, 100.05, 99.40)                    # A comes back 1 %: turn_A = 1
    bar(h, l, M0 + 50, 100.80, 99.95)                    # touches B = 100.7
    bar(h, l, M0 + 60, 101.90, 99.95)                    # B goes 1 % through: turn_B = 0
    touch_a, turn_a, _ = S.touch_turn(h, l, p, M0, 100.5, +1)
    touch_b, turn_b, _ = S.touch_turn(h, l, p, M0, 100.7, +1)
    assert (touch_a, turn_a) == (1, 1) and (touch_b, turn_b) == (1, 0)
    assert touch_a and touch_b and turn_a - turn_b == 1
    same = S.touch_turn(h, l, p, M0, 100.5, +1)          # equal distance: the same label, delta = 0
    assert same[1] - turn_a == 0
    touch_c, turn_c, t_c = S.touch_turn(h, l, p, M0, 105.0, +1)
    assert (touch_c, turn_c, t_c) == (0, -1, -1)         # never touched: the state leaves the paired turn


# --- Q2: the EV1 event minute --------------------------------------------------------------------------

def test_first_level_minute_allows_the_entry_minute_and_stops_before_the_exit():
    h, l, p = flat_bars()
    i0, x = 1_000, 1_100
    bar(h, l, i0, 100.70, 99.95)
    assert S.first_level_minute(h, l, p, i0, x, 100.6, +1) == i0          # b = i0 is allowed
    h, l, p = flat_bars()
    bar(h, l, i0 + 20, 100.70, 99.95)
    assert S.first_level_minute(h, l, p, i0, x, 100.6, +1) == i0 + 20
    p[i0 + 20] = False                                                   # missing: skipped
    bar(h, l, i0 + 30, 100.70, 99.95)
    assert S.first_level_minute(h, l, p, i0, x, 100.6, +1) == i0 + 30
    h, l, p = flat_bars()
    bar(h, l, x, 100.70, 99.95)                                          # at the exit minute: outside [i0, x)
    assert S.first_level_minute(h, l, p, i0, x, 100.6, +1) == S.NO_EVENT
    assert S.first_level_minute(h, l, p, i0, x, float("nan"), +1) == S.NO_EVENT
    h, l, p = flat_bars()
    bar(h, l, i0 + 20, 100.05, 99.40)                                    # the short mirror: low <= level
    assert S.first_level_minute(h, l, p, i0, x, 99.4, -1) == i0 + 20
    assert S.first_level_minute(h, l, p, i0, x, 99.4, +1) == i0          # a long is already above 99.4 at entry


def test_the_entry_state_level_is_an_argument_and_nothing_before_the_entry_is_read():
    """Section 5 Q2 / L3: the EV1 level comes from the map as of i0 - 1 and is passed in, so masking the trade's own
    minutes cannot change it; the search itself reads only [i0, x)."""
    h, l, p = flat_bars()
    i0, x, level = 1_000, 1_100, 100.6
    bar(h, l, i0 + 20, 100.70, 99.95)
    before = S.first_level_minute(h, l, p, i0, x, level, +1)
    h[:i0], l[:i0], p[:i0] = 200.0, 1.0, False           # every minute before the entry: scrambled and masked
    assert S.first_level_minute(h, l, p, i0, x, level, +1) == before == i0 + 20
    h[i0:], l[i0:] = np.nan, np.nan                      # masking the trade's minutes removes the event, not the level
    assert S.first_level_minute(h, l, p, i0, x, level, +1) == S.NO_EVENT


# --- Q2: the EV2 event minute --------------------------------------------------------------------------

def test_a_burst_whose_bins_straddle_the_entry_does_not_fire_and_the_next_one_does():
    """Bin b is stamped t0_arch + 300 b and covers the bars opening at that stamp ... + 4; minute m reads the last
    bin stamped <= m - 5 min, and the sum spans bins b - 2 ... b (section 4)."""
    i0, x = 100, 400
    flag, open_ = np.zeros(200), np.ones(400, dtype=bool)
    flag[21] = 1.0                                       # bin 21's sum starts at bin 19 = minute 95, before the entry
    assert S.first_burst_minute(flag, open_, T0_STATS, T0_STATS, i0, x) == S.NO_EVENT
    flag[22] = 1.0                                       # bin 22's sum starts at bin 20 = minute 100 = the entry
    assert S.first_burst_minute(flag, open_, T0_STATS, T0_STATS, i0, x) == 115          # the first minute whose map is bin 22
    assert S.first_burst_minute(flag.astype(bool), open_, T0_STATS, T0_STATS, i0, x) == 115    # the flag may arrive as bool
    assert S.first_burst_minute(flag, open_, T0_STATS, T0_STATS, i0, 115) == S.NO_EVENT  # [i0, x) is half open
    assert S.first_burst_minute(flag, open_, T0_STATS, T0_STATS, i0, 116) == 115
    assert S.state_bin_of(T0_STATS, T0_STATS + 60 * np.array([110, 114, 115])).tolist() == [21, 21, 22]


def test_a_missing_flag_is_no_event_and_the_archive_offset_shifts_the_bins():
    i0, x = 100, 400
    flag, open_ = np.zeros(200), np.ones(400, dtype=bool)
    flag[22] = np.nan                                    # a missing flag: no event, the trade stays at risk
    assert S.first_burst_minute(flag, open_, T0_STATS, T0_STATS, i0, x) == S.NO_EVENT
    flag[30] = 1.0
    assert S.first_burst_minute(flag, open_, T0_STATS, T0_STATS, i0, x) == 155          # floor((m - 5) / 5) = 30 at m = 155
    t0_arch = T0_STATS + 300 * 10                              # the archive starts 10 bins into the panel
    flag = np.zeros(200)
    flag[11] = 1.0                                       # bin 9 opens at minute 95: still straddles the entry
    assert S.first_burst_minute(flag, open_, t0_arch, T0_STATS, i0, x) == S.NO_EVENT
    flag[12] = 1.0                                       # bin 10 opens at minute 100
    assert S.first_burst_minute(flag, open_, t0_arch, T0_STATS, i0, x) == 115


def test_a_burst_on_a_minute_without_a_close_is_not_an_event():
    """Section 5 Q2's open minute is the walker's open set (`micro_lib.Grids.open_`: open after the minute's close
    and that close present), so a flagged minute with no close carries no event and the trade stays at risk on it."""
    i0, x = 100, 400
    flag, open_ = np.zeros(200), np.ones(400, dtype=bool)
    flag[22] = 1.0
    assert S.first_burst_minute(flag, open_, T0_STATS, T0_STATS, i0, x) == 115
    open_[115] = False                                   # no close at 115: the next open minute of that bin fires
    assert S.first_burst_minute(flag, open_, T0_STATS, T0_STATS, i0, x) == 116
    open_[115:120] = False                               # every minute reading bin 22 is missing
    assert S.first_burst_minute(flag, open_, T0_STATS, T0_STATS, i0, x) == S.NO_EVENT
    flag[30] = 1.0                                       # the next flagged present minute inside the trade fires
    assert S.first_burst_minute(flag, open_, T0_STATS, T0_STATS, i0, x) == 155


# --- Q2: match_liq reproduces the frozen matching ------------------------------------------------------

def with_eligibility(trades: pd.DataFrame, kind: str = "E1_against") -> pd.DataFrame:
    """The frozen fixtures' frames plus the per-kind column `eligible_liq` reads, filled by the frozen rule."""
    out = trades.copy()
    out[f"{kind}_eligible"] = M.eligible(trades, kind)
    return out


def test_match_liq_reproduces_test_placebo_exclusions_and_per_control_averaging():
    """The microstructure fixture of that name, re-run through match_liq with an era window that excludes nothing."""
    L = 40
    specs = [{"i0": 0, "len": L, "first": 0 + 10},        # event trade: elapsed 10
             {"i0": 100, "len": L},                       # good control
             {"i0": 200, "len": L},                       # good control, two matching minutes
             {"i0": 300, "len": L},                       # good control
             {"i0": 20, "len": L},                        # overlaps the event trade in calendar time
             {"i0": 400, "len": L, "s": -1},              # other direction
             {"i0": 500, "len": L, "first": 500 + 9},     # its own event at elapsed 9: at risk only before it
             {"i0": 600, "len": L}]                       # every mark in another bin
    tr = TME.make_trades(specs)
    base = np.full(L, 0.1)
    marks = [base.copy() for _ in specs]
    cvs = [np.zeros(L) for _ in specs]
    marks[0][10], cvs[0][10] = 0.1, -0.5
    for r in (1, 2, 3, 4, 5, 6):
        marks[r][:] = 5.0
    marks[1][10], cvs[1][10] = 0.2, 1.0
    marks[2][9], cvs[2][9] = 0.15, 0.0
    marks[2][11], cvs[2][11] = 0.2, 2.0
    marks[3][12], cvs[3][12] = 0.0, 3.0
    marks[3][13], cvs[3][13] = 0.1, 3.0
    marks[4][10], cvs[4][10] = 0.1, 99.0
    marks[5][10], cvs[5][10] = 0.1, 99.0
    marks[6][9], cvs[6][9] = 0.1, 99.0
    marks[7][:] = 5.0
    g = TME.grids_from(marks, cvs)
    frozen = M.match(tr, g, "E1_against", window=2)
    ours = S.match_liq(with_eligibility(tr), g, "E1_against", window=2, era_days=10 ** 6)
    pd.testing.assert_frame_equal(frozen, ours)
    row = ours.set_index("tid").loc["t0"]
    assert row["controls"] == 3 and row["bin"] == 0 and row["elapsed_min"] == 10 and row["cv"] == -0.5
    assert row["placebo"] == pytest.approx((1.0 + (0.0 + 2.0) / 2 + 3.0) / 3)
    assert row["controls_time_only"] == 5


def test_match_liq_reproduces_test_placebo_needs_three_controls():
    L = 20
    tr = TME.make_trades([{"i0": 0, "len": L, "first": 5}, {"i0": 100, "len": L}, {"i0": 200, "len": L}])
    g = TME.grids_from([np.full(L, 0.1)] * 3, [np.zeros(L)] * 3)
    frozen = M.match(tr, g, "E1_against", window=3)
    ours = S.match_liq(with_eligibility(tr), g, "E1_against", window=3, era_days=10 ** 6)
    pd.testing.assert_frame_equal(frozen, ours)
    assert ours["controls"].iat[0] == 2 and np.isnan(ours["placebo"].iat[0])


# --- Q2: the era window, per-kind eligibility and per-kind "no event yet" -------------------------------

def liq_trades(specs: list[dict], kinds: dict[str, list]) -> pd.DataFrame:
    """One row per spec (i0, len, optional s); `kinds` gives each kind one (first elapsed or None, eligible) pair."""
    rows = []
    for k, sp in enumerate(specs):
        i0 = sp["i0"]
        row = {"tid": f"t{k}", "asset": "BTC", "direction": "long" if sp.get("s", 1) > 0 else "short",
               "s": sp.get("s", 1), "i0": i0, "x": i0 + sp["len"], "entry_ts": T0_STATS + 60 * i0,
               "entry_day": M.utc_day(T0_STATS + 60 * i0)}
        for kind, per in kinds.items():
            first, el = per[k]
            row[f"{kind}_first"] = -1 if first is None else i0 + first
            row[f"{kind}_eligible"] = el
        rows.append(row)
    return pd.DataFrame(rows)


def test_the_era_window_keeps_365_days_and_drops_one_minute_more():
    L, DAY = 40, 1_440
    specs = [{"i0": 600_000, "len": L},                       # the event trade
             {"i0": 600_000 - 365 * DAY, "len": L},           # exactly 365 days earlier: kept
             {"i0": 600_000 - 365 * DAY - 1, "len": L},       # 365 days and one minute earlier: dropped
             {"i0": 600_000 + 365 * DAY, "len": L},           # exactly 365 days later: kept
             {"i0": 700_000, "len": L}]                       # 69 days later: kept
    tr = liq_trades(specs, {"EV1": [(10, True)] + [(None, True)] * 4})
    marks = [np.full(L, 0.1) for _ in specs]
    cvs = [np.zeros(L)] + [np.full(L, v) for v in (1.0, 2.0, 3.0, 4.0)]
    cvs[0][10] = -0.5
    g = TME.grids_from(marks, cvs)
    era = S.match_liq(tr, g, "EV1", window=2).iloc[0]
    assert era["controls"] == 3 and era["placebo"] == pytest.approx((1.0 + 3.0 + 4.0) / 3)
    allyears = S.match_liq(tr, g, "EV1", window=2, era_days=10 ** 6).iloc[0]
    assert allyears["controls"] == 4 and allyears["placebo"] == pytest.approx((1.0 + 2.0 + 3.0 + 4.0) / 4)
    narrow = S.match_liq(tr, g, "EV1", window=2, era_days=364).iloc[0]
    assert narrow["controls"] == 1 and np.isnan(narrow["placebo"])       # below the three contributing controls


def test_eligibility_and_no_event_yet_are_read_per_kind():
    L = 40
    specs = [{"i0": 0, "len": L}, {"i0": 100, "len": L}, {"i0": 200, "len": L}, {"i0": 300, "len": L},
             {"i0": 400, "len": L}]
    tr = liq_trades(specs, {                       # t0 is the event trade in both kinds
        "EV1": [(10, True), (None, True), (None, True), (9, True), (None, True)],
        "EV2": [(10, True), (None, True), (None, False), (None, True), (None, True)]})
    marks = [np.full(L, 0.1) for _ in specs]
    marks[3][:] = 5.0
    marks[3][9] = 0.1                              # t3's only matching minute is the one its EV1 event closes
    cvs = [np.zeros(L)] + [np.full(L, v) for v in (1.0, 2.0, 3.0, 4.0)]
    cvs[0][10] = -0.5
    g = TME.grids_from(marks, cvs)
    ev1 = S.match_liq(tr, g, "EV1", window=2).iloc[0]
    assert ev1["controls"] == 3                    # t1, t2, t4; t3 has its own EV1 event at elapsed 9
    assert ev1["placebo"] == pytest.approx((1.0 + 2.0 + 4.0) / 3)
    ev2 = S.match_liq(tr, g, "EV2", window=2).iloc[0]
    assert ev2["controls"] == 3                    # t2 is not eligible for EV2; t3 has no EV2 event
    assert ev2["placebo"] == pytest.approx((1.0 + 3.0 + 4.0) / 3)
    out = S.match_liq(tr.assign(EV1_eligible=[False, True, True, True, True]), g, "EV1", window=2)
    assert out["tid"].tolist() == ["t3"]            # t0 is no longer an event trade of the kind, t3 still is
    with pytest.raises(KeyError):
        S.eligible_liq(tr, "EV2_ctrl")             # the column the map stage writes is missing


def test_a_no_event_column_may_be_minus_one_or_the_sentinel_and_counts_need_no_continuation_value():
    """`first_level_minute` / `first_burst_minute` return NO_EVENT where the frozen columns carry -1; both mean no
    event. The control counts are computable without any continuation value, which is what L6 counts before F0."""
    L = 40
    specs = [{"i0": 0, "len": L}, {"i0": 100, "len": L}, {"i0": 200, "len": L}, {"i0": 300, "len": L}]
    tr = liq_trades(specs, {"EV1": [(10, True), (None, True), (None, True), (None, True)]})
    g = TME.grids_from([np.full(L, 0.1) for _ in specs], [np.zeros(L)] + [np.full(L, v) for v in (1.0, 2.0, 3.0)])
    row = S.match_liq(tr, g, "EV1", window=2).iloc[0]
    sentinel = S.match_liq(tr.assign(EV1_first=[10, S.NO_EVENT, S.NO_EVENT, S.NO_EVENT]), g, "EV1", window=2).iloc[0]
    assert row["controls"] == sentinel["controls"] == 3
    assert row["placebo"] == pytest.approx(2.0) and sentinel["placebo"] == pytest.approx(2.0)
    counts = S.match_liq(tr, M.Grids(g.open_, g.bin_, g.mark, None, None), "EV1", window=2)
    assert list(counts.columns) == ["tid", "entry_ts", "entry_day", "asset", "direction", "elapsed_min", "bin",
                                    "mark_R", "controls", "controls_time_only"]
    assert counts["controls"].iat[0] == 3


# --- statistics under a claimed sign -------------------------------------------------------------------

def test_the_one_sided_p_dispatches_on_the_claimed_sign():
    boot = np.array([-0.4, -0.3, -0.2, -0.2, -0.1])
    assert S.p_claimed(-0.24, boot, -1) == pytest.approx(1 / 6) == M.p_less(-0.24, boot)
    assert S.p_claimed(0.24, -boot, +1) == pytest.approx(1 / 6) == CL.p_one_sided(0.24, -boot)
    assert S.p_claimed(-0.24, boot, +1) == pytest.approx(M.p_less(0.24, -boot)) == 1.0    # the wrong side
    with pytest.raises(ValueError):
        S.p_claimed(0.1, boot, 0)


def days_from(n: int, start: date = date(2024, 1, 1)) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def test_the_paired_difference_bootstrap_and_its_one_sided_p():
    """Alternating -0.2 / -0.4 over 60 days: every 30-day block holds 15 of each, so every draw is exactly -0.3."""
    days = days_from(60)
    values = np.where(np.arange(60) % 2 == 0, -0.2, -0.4)
    axis = M.day_axis(pd.Series(days))
    idx = M.block_indices(len(axis), n_boot=100)
    r = S.paired_stats(days, values, axis, idx, -1)
    assert r["n"] == 60 and r["mean"] == pytest.approx(-0.3) and r["claimed_sign"] == -1
    assert r["ci95"] == pytest.approx([-0.3, -0.3])
    assert r["p_one_sided"] == pytest.approx(1 / 101)
    assert r["first_half"] == pytest.approx(-0.3) and r["second_half"] == pytest.approx(-0.3)
    assert r["by_year"] == {"2024": {"n": 60, "mean": pytest.approx(-0.3)}}
    # under the wrong claim every centred draw sits on the claimed side: p = (1 + 100) / 101
    assert S.paired_stats(days, values, axis, idx, +1)["p_one_sided"] == pytest.approx(1.0)


def test_the_earlier_half_takes_the_extra_observation_and_years_split_by_day():
    days = ["2023-12-31", "2024-01-01", "2024-01-02"]
    values = np.array([1.0, 2.0, 3.0])
    axis = M.day_axis(pd.Series(days))
    idx = M.block_indices(len(axis), n_boot=50)
    r = S.paired_stats(days, values, axis, idx, +1)
    assert (r["n"], r["first_half_n"], r["second_half_n"]) == (3, 2, 1)
    assert r["first_half"] == pytest.approx(1.5) and r["second_half"] == pytest.approx(3.0)
    assert r["by_year"] == {"2023": {"n": 1, "mean": 1.0}, "2024": {"n": 2, "mean": 2.5}}
    with_nan = S.paired_stats(days + ["2024-01-03"], np.append(values, np.nan), axis + ["2024-01-03"],
                              M.block_indices(4, n_boot=50), +1)
    assert with_nan["n"] == 3                                        # an observation without a placebo is dropped
    assert S.paired_stats([], [], axis, idx, -1) == {"n": 0, "claimed_sign": -1}
    with pytest.raises(ValueError):
        S.paired_stats(["2024-01-02", "2024-01-01"], np.array([1.0, 2.0]), axis, idx, +1)


def test_a_positive_delta_is_contrary_only_under_the_negative_claim():
    days = days_from(40)
    axis = M.day_axis(pd.Series(days))
    idx = M.block_indices(len(axis), n_boot=200)
    up = S.paired_stats(days, np.full(40, 0.25), axis, idx, +1)
    assert up["p_one_sided"] == pytest.approx(1 / 201) and up["ci95"] == pytest.approx([0.25, 0.25])
    assert S.classify_liq(up, True, up["p_one_sided"], None, +1, S.BANDS["R"]) == "INFORMATIVE"
    down = S.paired_stats(days, np.full(40, 0.25), axis, idx, -1)
    assert down["p_one_sided"] == pytest.approx(1.0)
    assert S.classify_liq(down, True, down["p_one_sided"], None, -1, S.BANDS["R"]) == "CONTRARY"
    q2 = S.paired_stats(days, np.full(40, -0.25), axis, idx, -1)     # the Q2 mirror
    assert q2["p_one_sided"] == pytest.approx(1 / 201)
    assert S.classify_liq(q2, True, q2["p_one_sided"], None, -1, S.BANDS["R"]) == "INFORMATIVE"
    assert S.classify_liq({**q2, "claimed_sign": +1}, True, 0.9, None, +1, S.BANDS["R"]) == "CONTRARY"


# --- the classification table ---------------------------------------------------------------------------

EV1 = {"n": 42, "mean": -0.30, "ci95": [-0.50, -0.12], "first_half": -0.25, "second_half": -0.35}
Q1_TOUCH = {"n": 1_200, "mean": 0.032, "ci95": [0.024, 0.040], "first_half": 0.030, "second_half": 0.034}


def test_the_classification_is_evaluated_in_the_preregistrations_order():
    R, TOUCH = S.BANDS["R"], S.BANDS["touch"]
    assert S.classify_liq(Q1_TOUCH, True, 0.01, None, +1, TOUCH) == "INFORMATIVE"       # (+2.4, +4.0) pp
    inside = {**Q1_TOUCH, "mean": 0.005, "ci95": [-0.004, 0.012]}                       # halves still positive
    assert S.classify_liq(inside, True, 0.001, None, +1, TOUCH) == "NO INFORMATION"     # rule 2 before rule 3
    assert S.classify_liq({**Q1_TOUCH, "second_half": -0.01}, True, 0.01, None, +1, TOUCH) == "UNDETERMINED"
    assert S.classify_liq(Q1_TOUCH, True, 0.20, None, +1, TOUCH) == "UNDETERMINED"
    assert S.classify_liq(EV1, False, 0.01, None, -1, R) == "DESCRIPTIVE"               # rule 1: below the count
    assert S.classify_liq({"n": 0}, True, 0.01, None, -1, R) == "DESCRIPTIVE"
    contrary = {**EV1, "mean": 0.30, "ci95": [0.12, 0.50], "first_half": 0.25, "second_half": 0.35}
    assert S.classify_liq(contrary, True, 0.90, None, -1, R) == "CONTRARY"
    assert S.classify_liq({**contrary, "ci95": [0.0, 0.50]}, True, 0.90, None, -1, R) == "UNDETERMINED"
    assert S.classify_liq({**Q1_TOUCH, "ci95": [-0.040, -0.024], "first_half": -0.03, "second_half": -0.034},
                          True, 0.90, None, +1, TOUCH) == "CONTRARY"


def test_a_comparator_below_ten_trades_blocks_informative():
    R = S.BANDS["R"]
    ok = {"placebo": {"n": 40, "p_one_sided": 0.01}}
    assert S.classify_liq(EV1, True, 0.01, ok, -1, R) == "INFORMATIVE"
    assert S.classify_liq(EV1, True, 0.01, {"placebo": {"n": 40, "p_one_sided": 0.20}}, -1, R) == "UNDETERMINED"
    assert S.classify_liq(EV1, True, 0.01, {"placebo": {"n": 9, "p_one_sided": 0.01}}, -1,
                          R) == "UNDETERMINED (comparator unavailable)"
    ev2 = {**ok, "sign": {"n": 30, "mean": 0.05}}                        # EV2 also needs Delta(EV2) < Delta(against)
    assert S.classify_liq(EV1, True, 0.01, ev2, -1, R) == "INFORMATIVE"
    assert S.classify_liq(EV1, True, 0.01, {**ev2, "sign": {"n": 30, "mean": -0.60}}, -1, R) == "UNDETERMINED"
    assert S.classify_liq(EV1, True, 0.01, {**ev2, "sign": {"n": 9, "mean": 0.05}}, -1,
                          R) == "UNDETERMINED (comparator unavailable)"


def test_classify_liq_and_the_frozen_twin_differ_on_order_and_on_direction():
    """`micro_lib.classify` evaluates INFORMATIVE first and fixes the claimed sign at -1; section 5 does neither."""
    R = S.BANDS["R"]
    inside = {"n": 40, "mean": -0.05, "ci95": [-0.09, -0.01], "first_half": -0.04, "second_half": -0.06}
    assert M.classify(inside, True, 0.01, None, "E4_at_level") == "INFORMATIVE"
    assert S.classify_liq(inside, True, 0.01, None, -1, R) == "NO INFORMATION"
    up = {"n": 40, "mean": 0.30, "ci95": [0.20, 0.50], "first_half": 0.25, "second_half": 0.35}
    assert M.classify(up, True, 0.01, None, "E4_at_level") == "CONTRARY"
    assert S.classify_liq(up, True, 0.01, None, +1, R) == "INFORMATIVE"
    assert S.classify_liq(up, True, 0.01, None, -1, R) == "CONTRARY"


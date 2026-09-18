"""Synthetic fixtures for the squeeze_bull top anatomy (PROTOCOL_TOP_ANATOMY.md sections 2-4)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import anatomy_lib as A  # noqa: E402

E = 100.0


def bars(levels: dict, n=400, base=100.0, half_range=0.05):
    """Minute bars around a price level: `levels` maps a start minute to the level held from there on."""
    level = np.full(n, base)
    for start in sorted(levels):
        level[start:] = levels[start]
    return level + half_range, level - half_range


def test_frozen_constants():
    assert (A.SPIKE, A.REVERSAL, A.HORIZON_MIN, A.PAUSE_MIN, A.PROFILE_MIN) == (0.02, 0.02, 10_080, 30, 720)
    assert (A.METRIC_LAG_S, A.METRIC_MAX_SLOTS_BACK, A.FLOW_MIN, A.VOL_BASE_MIN, A.FORWARD_MIN) == (300, 6, 60, 10_080, 1440)


def test_spike_top_and_reversal():
    h, l = bars({10: 102.1, 20: 103.0, 30: 100.9})     # 103.05 x 0.98 = 100.989 > low 100.85
    assert A.bounce_shape(h, l, E) == {"shape": "spike_reversal", "S": 10, "T": 20, "C": 30}


def test_a_higher_high_moves_the_top_and_drops_before_the_spike_do_not_count():
    h, l = bars({5: 97.0, 6: 100.0, 10: 102.1, 20: 103.0, 25: 101.5, 40: 104.0, 60: 101.9})
    assert A.bounce_shape(h, l, E) == {"shape": "spike_reversal", "S": 10, "T": 40, "C": 60}


def test_no_spike_and_spike_without_reversal():
    h, l = bars({50: 101.9})
    assert A.bounce_shape(h, l, E)["shape"] == "no_spike"
    h, l = bars({60: 102.5, 90: 103.5})
    assert A.bounce_shape(h, l, E) == {"shape": "spike_no_reversal", "S": 60, "T": 90, "C": -1}


def test_same_minute_peak_and_reversal_and_equal_highs():
    h, l = bars({})
    h[10], l[10] = 105.0, 102.0                        # one wide minute: 105 x 0.98 = 102.9
    assert A.bounce_shape(h, l, E) == {"shape": "spike_reversal", "S": 10, "T": 10, "C": 10}
    h, l = bars({10: 103.0, 20: 102.0, 30: 103.0, 50: 100.9})
    assert A.bounce_shape(h, l, E) == {"shape": "spike_reversal", "S": 10, "T": 10, "C": 50}


def test_false_tops_need_a_new_high_and_a_thirty_minute_pause():
    h, _ = bars({}, n=200)
    h[10], h[20], h[60] = 102.0, 103.0, 104.0      # 10 is exceeded within 30 minutes; 20 pauses 40 minutes
    h[70] = 103.5                                   # not a new high
    assert A.false_tops(h, 10, 60).tolist() == [20]
    h[45] = 103.2                                   # 25 minutes after 20, and itself exceeded 15 minutes later
    assert A.false_tops(h, 10, 60).tolist() == []
    h[60], h[70], h[90] = 100.05, 100.05, 104.0     # now 45 pauses 45 minutes before the top at 90
    assert A.false_tops(h, 10, 90).tolist() == [45]


def test_stall_minutes_count_from_the_last_new_running_high():
    assert A.stall_minutes(np.array([1.0, 2.0, 2.0, 1.5, 3.0])).tolist() == [0, 0, 1, 2, 0]
    assert A.stall_minutes(np.array([np.nan, 1.0, 0.5])).tolist()[1:] == [0, 1]


def test_taker_share_and_volume_ratio_windows():
    v = np.ones(200)
    tb = np.where(np.arange(200) < 100, 0.5, 1.0)
    s = A.taker_share(v, tb, window=60, min_present=50)
    assert s[159] == pytest.approx(1.0) and s[129] == pytest.approx(0.75)
    v2 = v.copy()
    v2[100:115] = 0.0                               # 15 dead minutes: 45 present < 50
    assert np.isnan(A.taker_share(v2, tb, window=60, min_present=50)[114])
    vol = np.concatenate([np.ones(100), np.full(10, 2.0)])
    r = A.volume_ratio(vol, window=10, base=50, base_min_present=50, min_present=10)
    assert r[109] == pytest.approx(2.0) and np.isnan(r[40])


def market(n=3000, t0=1_700_000_000 - 1_700_000_000 % 300, metrics=None):
    z = np.zeros(n)
    return A.Market(t0, np.full(n, 100.0), np.full(n, 100.0), np.full(n, 100.0), np.full(n, 100.0), z, z, z, z, t0,
                    metrics or {"oi": np.full(n // 5 + 10, np.nan), "top_position_lsr": np.full(n // 5 + 10, np.nan),
                                "account_lsr": np.full(n // 5 + 10, np.nan)})


def test_metric_snapshot_used_five_minutes_after_its_stamp_and_not_when_stale():
    mk = market()
    mk.metrics["oi"][0], mk.metrics["oi"][1] = 10.0, 11.0          # stamps t0 and t0 + 300
    got = mk.metric_minutes("oi", 0, 12)
    assert np.isnan(got[3])                                        # minute 3 closes at 240 s: nothing usable yet
    assert got[4] == 10.0 and got[8] == 10.0                       # closes 300 ... 540 s: stamp 0
    assert got[9] == 11.0                                          # closes 600 s: stamp 300
    late = mk.metric_minutes("oi", 40, 41)[0]                      # close 2,460 s: slot 7, six back is slot 1
    assert late == 11.0
    assert np.isnan(mk.metric_minutes("oi", 45, 46)[0])            # close 2,760 s: slot 8, six back is slot 2: stale
    assert mk.metric_stamped("oi", mk.t0_s + 300) == 11.0 and mk.metric_stamped("oi", mk.t0_s + 900) == 11.0


def test_repair_features_and_time_from_entry():
    mk = market()
    i0 = 1000
    mk.close[i0 + 10] = 101.0
    mk.high[i0 + 5] = 101.5
    mk.metrics["oi"][:] = 95.0
    mk.metrics["top_position_lsr"][:] = 2.0
    mk.metrics["account_lsr"][:] = 1.5
    mk.metrics["oi"][(i0 * 60 + 600) // 300:] = 97.5                # OI back to half of the flushed amount
    fire = SimpleNamespace(i0=i0, entry=100.0, P0=102.0, OI_E=95.0, OI_0=100.0)
    f = A.fire_features(mk, fire, -5, 30).set_index("rel_min")
    assert f.at[10, "PR"] == pytest.approx(0.5) and f.at[0, "PR"] == pytest.approx(0.0)
    assert f.at[20, "OR"] == pytest.approx(0.5) and f.at[0, "OR"] == pytest.approx(0.0)
    assert np.isnan(f.at[-5, "stall_min"]) and np.isnan(f.at[-1, "hours_since_entry"])
    assert f.at[5, "stall_min"] == 0 and f.at[8, "stall_min"] == 3 and f.at[30 - 1, "hours_since_entry"] == pytest.approx(29 / 60)
    assert f.at[10, "top_position_lsr_chg_pct"] == pytest.approx(0.0)

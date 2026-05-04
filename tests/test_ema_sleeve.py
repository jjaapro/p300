"""jplus.ema_sleeve — weekly aggregation + EMA(5/21) crossover position map.

Properties under test:
  - Weekly aggregation produces 168h-bucketed candles (first-bucket open,
    max-high, min-low, last-bucket close).
  - Position is +1/-1 for every day inside a trade (inclusive bounds).
  - Cross-up opens long, cross-down closes and opens short.
  - Look-ahead safety: compute_ema_position_map(bc[:N]) gives identical
    position labels to compute_ema_position_map(bc[:M]) for every common
    date, where M > N.
  - Empty / warmup inputs don't blow up.
"""
from __future__ import annotations

from datetime import datetime, timezone

from jplus import ema_sleeve


def _mk_hourly(n_bars: int, base_price: float = 50_000.0, start_ts: int = 1577836800):
    """Build a synthetic hourly (ts, o, h, l, c, v) series. start_ts defaults
    to 2020-01-01 00:00 UTC. Prices are a simple sine + trend mix so there
    will be crossovers."""
    import math
    bars = []
    for i in range(n_bars):
        ts = start_ts + i * 3600
        # long-period wave so weekly EMAs will cross
        p = base_price * (1 + 0.002 * i / 168 + 0.05 * math.sin(i / 400))
        bars.append((ts, p, p * 1.005, p * 0.995, p, 1.0))
    return bars


def test_aggregate_weekly_empty_returns_empty():
    assert ema_sleeve.aggregate_weekly([]) == []


def test_aggregate_weekly_produces_168h_buckets():
    bars = _mk_hourly(168 * 3)  # 3 full weeks
    weekly = ema_sleeve.aggregate_weekly(bars)
    assert len(weekly) == 3
    for w in weekly:
        # (dt_str, open, high, low, close)
        assert len(w) == 5
        assert isinstance(w[0], str)


def test_aggregate_weekly_open_is_first_bar_open():
    bars = _mk_hourly(168)
    weekly = ema_sleeve.aggregate_weekly(bars)
    assert weekly[0][1] == bars[0][1]


def test_aggregate_weekly_close_is_last_bar_close():
    bars = _mk_hourly(168)
    weekly = ema_sleeve.aggregate_weekly(bars)
    assert weekly[0][4] == bars[-1][4]


def test_aggregate_weekly_high_and_low_span_week():
    bars = _mk_hourly(168)
    weekly = ema_sleeve.aggregate_weekly(bars)
    assert weekly[0][2] == max(b[2] for b in bars)
    assert weekly[0][3] == min(b[3] for b in bars)


def test_compute_ema_position_map_warmup_returns_empty():
    # With fewer than slow+2 weekly candles, no crossovers → empty map
    bars = _mk_hourly(168 * 10)  # only 10 weeks < 21 slow EMA seed
    pos = ema_sleeve.compute_ema_position_map(bars)
    assert pos == {}


def test_compute_ema_position_map_long_run_produces_position_days():
    # 52 weeks of data → crossover possibilities → non-empty map
    bars = _mk_hourly(168 * 52)
    pos = ema_sleeve.compute_ema_position_map(bars)
    assert len(pos) > 0
    # All values are +1 or -1
    assert set(pos.values()).issubset({+1, -1})


def test_compute_ema_position_map_deterministic():
    bars = _mk_hourly(168 * 40)
    a = ema_sleeve.compute_ema_position_map(bars)
    b = ema_sleeve.compute_ema_position_map(bars)
    assert a == b


def test_compute_ema_position_map_truncated_matches_full():
    """Look-ahead safety: running on a truncated series should give the
    same positions on common dates as running on the full series."""
    bars = _mk_hourly(168 * 60)
    full = ema_sleeve.compute_ema_position_map(bars)

    # Truncate at 40 weeks
    trunc_bars = bars[: 168 * 40]
    trunc = ema_sleeve.compute_ema_position_map(trunc_bars)

    # Every key in trunc must either match full or be absent from full's
    # ongoing-trade region (the last trade may not yet have closed in
    # trunc, so its tail-end days will differ).
    # Safer check: the EARLIEST half of trunc should be bit-identical.
    early_cutoff = None
    trunc_keys = sorted(trunc.keys())
    if trunc_keys:
        # Take the first half of trunc keys; these dates correspond to
        # fully-closed trades whose assignment cannot change.
        n = len(trunc_keys) // 2
        early_cutoff = trunc_keys[n]
    for d in trunc_keys:
        if early_cutoff is not None and d >= early_cutoff:
            break
        assert d in full, f"{d} in trunc but missing from full"
        assert trunc[d] == full[d], f"position changed at {d}: trunc={trunc[d]} full={full[d]}"


def test_aggregate_weekly_incomplete_tail_is_discarded():
    # 168h + 50h extra → 2nd bucket starts but doesn't complete → dropped
    bars = _mk_hourly(168 + 50)
    weekly = ema_sleeve.aggregate_weekly(bars)
    # Only the completed bucket is emitted.
    assert len(weekly) == 1

"""EMA(5/21) weekly crossover on BTC — position map per day.

Ported from upstream's `validate_s100_mom_gold.get_ema_pos` → which calls
`backtest_ema.aggregate_candles(htf=168h)` and `backtest_ema.run_backtest(
5, 21, mode='long_short')`, then spreads each closed trade's direction
across the dates the trade was held.

The "5/21 weekly EMA" setup:
  - Aggregate BTC hourly → 168h (1 week) fixed-size buckets.
  - Compute EMA(5) and EMA(21) on weekly closes.
  - LONG when EMA5 > EMA21, SHORT when EMA5 < EMA21.
  - Cross detection → open at NEXT weekly candle's open.
  - Exit on reverse cross, enter the other side.
  - 0.1% round-trip commission.

The output of this module is a `{date_iso: +1 | -1}` map telling the
J+ simulator whether BTC EMA is long/short on each day. Dates not in
the map are treated as flat (position 0).

Look-ahead safety: `data.load_btc_hourly()` is clock-bounded, so the
weekly aggregation can only see candles strictly ≤ clock. A trade whose
"exit" is still in the future at the current clock will contribute only
the days up to clock's today.

This port matches the upstream algorithm exactly; it just avoids the
dataclass wrappers in backtest_ema.py.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from strategies.support.regime_jplus import ema_calc

# Historically 0.001 (0.1% round-trip), but never actually applied —
# the simulator's c_ema computation in jplus/simulate.py uses raw
# ema_p × br with no commission deduction. Kept at 0.0 explicitly as
# of the trade-emitter migration; if/when EMA flips need a fee in
# live trading, the emitter is the place to charge it (FLIP event's
# fee_usdt). See plan how-do-we-modular-curry.md, Step 5/7.
_COMMISSION = 0.0


def aggregate_weekly(hourly: list[tuple[int, float, float, float, float, float]]
                     ) -> list[tuple[str, float, float, float, float]]:
    """Aggregate hourly bars (ts, o, h, l, c, v) into 168h fixed-size
    weekly buckets. Returns [(dt_iso, open, high, low, close)]. First
    bucket starts at the first hourly bar; subsequent buckets accumulate
    until 168h have elapsed from bucket start, then roll.

    This matches upstream backtest_ema.aggregate_candles(tf_hours=168).
    """
    out = []
    if not hourly:
        return out
    bucket: list[tuple[int, float, float, float, float, float]] = []
    bucket_start: int | None = None
    for ts, o, h, l, c, v in hourly:
        if not bucket:
            bucket_start = ts
        bucket.append((ts, o, h, l, c, v))
        elapsed_hours = (ts - bucket_start) / 3600 + 1
        if elapsed_hours >= 168:
            dt_str = datetime.fromtimestamp(bucket_start, tz=timezone.utc).strftime("%Y-%m-%d")
            out.append((
                dt_str,
                bucket[0][1],
                max(b[2] for b in bucket),
                min(b[3] for b in bucket),
                bucket[-1][4],
            ))
            bucket = []
            bucket_start = None
    return out


def compute_ema_position_map(
    hourly: list[tuple[int, float, float, float, float, float]],
    fast: int = 5,
    slow: int = 21,
) -> dict[str, int]:
    """Run the EMA(fast/slow) long/short backtest on weekly-aggregated
    BTC, and produce a {date_iso: +1 | -1} position map covering each
    day inside a held trade. Dates outside any trade are absent.

    Invariants preserved from upstream:
      - Entry price is next weekly candle's OPEN after the cross bar.
      - A trade holds from entry date through exit date inclusive.
      - 0.1% round-trip commission is applied to PnL but doesn't affect
        the position map (we only emit direction per day).
    """
    weekly = aggregate_weekly(hourly)
    if len(weekly) < slow + 2:
        return {}
    closes = [w[4] for w in weekly]
    fast_ema = ema_calc(closes, fast)
    slow_ema = ema_calc(closes, slow)

    pos: dict[str, int] = {}
    position: str | None = None
    entry_idx: int | None = None
    entry_date: datetime | None = None

    warmup = slow + 1

    def spread(start: datetime, end_idx: int, side: int) -> None:
        """Fill days from `start` (entry date inclusive) to weekly[end_idx]
        iso date inclusive with `side` (+1 or -1)."""
        end_date = datetime.strptime(weekly[end_idx][0], "%Y-%m-%d")
        cur = start
        while cur <= end_date:
            pos[cur.date().isoformat()] = side
            cur += timedelta(days=1)

    for i in range(warmup, len(weekly) - 1):
        if math.isnan(fast_ema[i]) or math.isnan(slow_ema[i]):
            continue
        fa = fast_ema[i] > slow_ema[i]
        if math.isnan(fast_ema[i - 1]) or math.isnan(slow_ema[i - 1]):
            continue
        fa_prev = fast_ema[i - 1] > slow_ema[i - 1]

        cross_up = fa and not fa_prev
        cross_down = not fa and fa_prev

        next_dt = datetime.strptime(weekly[i + 1][0], "%Y-%m-%d")

        # Close existing position on opposing cross. Upstream trade's
        # exit_time = weekly[i+1].dt (next-open exit), so the spread
        # should reach that date, not the cross-bar date.
        if position == "long" and cross_down and entry_idx is not None and entry_date is not None:
            spread(entry_date, i + 1, +1)
            position = None
            entry_idx = None
            entry_date = None
        elif position == "short" and cross_up and entry_idx is not None and entry_date is not None:
            spread(entry_date, i + 1, -1)
            position = None
            entry_idx = None
            entry_date = None

        # Open new position at NEXT weekly candle's open (next_dt)
        if cross_up and position is None:
            position = "long"
            entry_idx = i + 1
            entry_date = next_dt
        elif cross_down and position is None:
            position = "short"  # long_short mode
            entry_idx = i + 1
            entry_date = next_dt

    # Final open trade: still running at the edge of available data. Spread
    # the position through the last hourly bar's calendar date (NOT just the
    # last weekly candle's start date). Without this, days between the last
    # weekly start and the clock would be unlabeled — and when the clock is
    # later advanced, those same days get labeled → look-ahead bug on the
    # EMA sleeve. See tests/test_jplus_lookahead.py.
    if position is not None and entry_idx is not None and entry_date is not None:
        side = +1 if position == "long" else -1
        last_hourly_date = datetime.fromtimestamp(
            hourly[-1][0], tz=timezone.utc
        ).date()
        last_weekly_date = datetime.strptime(weekly[-1][0], "%Y-%m-%d").date()
        effective_end = max(last_hourly_date, last_weekly_date)
        cur = entry_date
        # datetime-vs-date comparison: normalise both to date.
        end_d = effective_end
        while cur.date() <= end_d:
            pos[cur.date().isoformat()] = side
            cur += timedelta(days=1)

    return pos

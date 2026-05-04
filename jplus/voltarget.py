"""Vol-target leverage cap — 50% annualised-vol target with a per-regime
hard cap. Ported from upstream simulate_full_jplus.

Inputs per day:
  - recent_1x_returns: list of the strategy's 1x (pre-leverage) daily
    returns in PERCENT, accumulated as the simulator marches forward.
    ONLY returns from days STRICTLY BEFORE today may be included —
    this is what upstream does (recent_1x is appended AFTER the day's
    leverage is chosen).

  - regime_mode: today's regime label; maps to a hard cap via H_CAPS.

  - vol_window: rolling window for realised vol (default 30 days).

  - target_vol: annualised target in percent (default 50.0).

Output: today's leverage multiplier, clamped to [0.5, H_CAPS[mode]].

The 0.5 floor and per-regime cap are load-bearing: without them, a very
low recent vol (typical at the peak before a crash) would permit
ludicrous leverage. The cap dampens the vol-target when markets are
sleepy.
"""
from __future__ import annotations

import math


H_CAPS: dict[str, float] = {
    "strong_bull": 3.0,
    "mild_bull": 2.5,
    "uncertain": 2.0,
    "bear": 1.5,
}

LEV_FLOOR = 0.5
TARGET_VOL_DEFAULT = 50.0
VOL_WINDOW_DEFAULT = 30


def leverage_for_day(
    recent_1x_returns: list[float],
    regime_mode: str,
    target_vol: float = TARGET_VOL_DEFAULT,
    vol_window: int = VOL_WINDOW_DEFAULT,
) -> float:
    """Return today's leverage multiplier. `recent_1x_returns` is in percent
    (e.g. 1.23 means +1.23% — NOT 0.0123). These are the pre-leverage daily
    returns from prior days."""
    cap = H_CAPS.get(regime_mode, 2.0)
    if len(recent_1x_returns) < vol_window:
        # Warmup: don't leverage yet.
        return min(1.0, cap)
    last = recent_1x_returns[-vol_window:]
    m = sum(last) / len(last)
    s = math.sqrt(sum((r - m) ** 2 for r in last) / (len(last) - 1))
    realized_ann = s * math.sqrt(365)
    if realized_ann <= 1e-6:
        return cap
    return min(cap, max(LEV_FLOOR, target_vol / realized_ann))

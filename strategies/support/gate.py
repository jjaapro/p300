"""Rule-based R4 de-lever gate — replaces upstream's ML gate.

WHY RULE-BASED, NOT ML:
  The upstream ML gate (ml_mdd_regime_predictions.csv) was found to have
  within-day look-ahead: per-date feature vectors include end-of-day 1m
  stats (parkinson_1min_sum, rv_1min, intraday_range_pct over ALL 1,440
  minute bars of the same day). Those features can only be known at
  23:59 UTC of date T. But the R4 BTC sleeve trades 06:00 → 18:00 UTC
  on date T — so the "gate decision for today" implicitly peeks at
  end-of-day info from a window that ends AFTER the R4 trade has already
  closed. That bakes look-ahead into every gate application.

  This port uses a rule derived strictly from data available as of
  yesterday's close (T-1). No ML. No CSV. No training pipeline to trust.

RULE — R4 leverage de-levers 2.5× → 1× when:
  Trailing 30-day realised vol of BTC daily returns as of T-1 is at or
  above the 75th percentile of the trailing 365-day distribution of 30d
  realised vols (also as of T-1).

One trigger. Uses only bc[:T], so no look-ahead by construction.

Rationale: "high realised vol regime" is a standard risk-manager signal
requiring no forecasting. A 30d window and 365d reference distribution
are conventional defaults (not tuned on this dataset). The 75th percentile
threshold produces a ~25% fire rate — R4 runs at 1× on the top-quarter
most volatile days, at 2.5× on the rest.

Trade-off vs a drawdown trigger: a DD-based rule fires REACTIVELY (after
a crash already started). The vol rule fires DURING high-vol regimes,
which is a broader and more proactive category — but also less binary.
We chose the vol rule because its fire rate is stable-by-construction
rather than regime-dependent, which makes its behaviour easier to audit.

Output: boolean per date; the simulator multiplies r4b_r and r4e_r by
1.0 when the gate fires, or by r4_extra_lev (2.5) when it doesn't.
"""
from __future__ import annotations

import math


VOL_WINDOW = 30                 # realised vol window
VOL_RANK_WINDOW = 365           # trailing distribution length
VOL_RANK_THRESHOLD = 0.75       # top 25% = "high vol regime"


def _rolling_vol_annualized(returns: list[float], window: int) -> list[float]:
    """Annualised realised vol (std × sqrt(365)) over rolling window.
    Returns a list the same length as `returns`; indices < window-1 are NaN.
    `returns` is expected in DECIMAL form (e.g. 0.012 for +1.2%)."""
    out = [float("nan")] * len(returns)
    if len(returns) < window:
        return out
    for i in range(window - 1, len(returns)):
        chunk = returns[i - window + 1: i + 1]
        m = sum(chunk) / window
        var = sum((r - m) ** 2 for r in chunk) / (window - 1)
        out[i] = math.sqrt(var) * math.sqrt(365)
    return out


def compute_gate_map(
    dates: list[str],
    bc: list[float],
) -> dict[str, bool]:
    """Return {date_iso: gate_fired} — True when R4 should be de-levered.

    Gate for day T uses only bc[:T] (indices ≤ T-1). Implementation:
      - Compute daily log returns from bc.
      - Compute rolling 30d realised vol series.
      - For each date T (i.e. index i in dates), evaluate both triggers
        using indices through i-1 only.
    """
    # Daily log returns (we use log to keep vol computation stable; magnitude
    # of diff vs simple return is negligible at daily frequency).
    rets = [0.0]
    for i in range(1, len(bc)):
        if bc[i - 1] > 0 and bc[i] > 0:
            rets.append(math.log(bc[i] / bc[i - 1]))
        else:
            rets.append(0.0)
    vol_series = _rolling_vol_annualized(rets, VOL_WINDOW)

    out: dict[str, bool] = {}
    for i, d in enumerate(dates):
        if i < 1:
            out[d] = False
            continue
        prev_i = i - 1  # T-1 index

        fired = False
        if prev_i >= VOL_WINDOW - 1:
            cur_vol = vol_series[prev_i]
            if not math.isnan(cur_vol):
                rank_start = max(VOL_WINDOW - 1, prev_i - VOL_RANK_WINDOW + 1)
                hist = [v for v in vol_series[rank_start:prev_i + 1]
                        if not math.isnan(v)]
                if len(hist) >= 30:
                    hist_sorted = sorted(hist)
                    threshold = hist_sorted[int(len(hist_sorted) * VOL_RANK_THRESHOLD)]
                    fired = cur_vol >= threshold
        out[d] = bool(fired)
    return out

# S-107 SQUEEZE_BULL — buy the forced-deleveraging flush, bull regime only

Long BTC perp when open interest falls >= 2% in 4 hours while price falls
>= 0.5%, and the causal 30-day return is above +10%. Stop -2%, target +3%,
time stop 48h — and since 2026-09-12 a second paper variant on the same
signals without the stop (`sleeve_cfg["use_stop"] = False`; see
`docs/calibration/squeeze_bull.md`).

**Mechanism.** A 2% fall in open interest inside four hours is forced closure,
not repositioning: liquidation engines closing longs. Forced supply is
price-insensitive, so it overshoots; in an uptrend the bid returns once it is
exhausted. The bull gate separates "forced sellers in an uptrend" from "the
trend is over", and it is doing nearly all of the work — the ungated pool has
a profit factor of exactly 1.00 over 423 fires against the bull-gated 1.74.

**The regime gate is causal and is NOT the researched one.** The June study
read the current day's daily close, up to 21 hours after the fire. Production
shifts the daily series one day (`REGIME_SHIFT_DAYS = 1`). See
`docs/calibration/squeeze_bull.md`; the paper record measures the shipped
gate, not the study's published +0.202 R.

**The cooldown runs over flush EVENTS, not trades.** A bear-regime flush that
is never traded still silences the next 24 bars, matching
`identify_long_flush_events`. `tests/test_squeeze_bull_parity.py` enforces it.

Provenance: `studies/notebooks/oi_flush/` (June 2026) frozen, re-validated in
`studies/notebooks/squeeze_bull_revalidation/` (2026-09-08, BUILD on n=10 with
every margin one observation wide). Deployed to accrue evidence, not because
the evidence is settled. Re-cut points at n=20 and n=30 are fixed in advance
in the calibration log.

Data: `cd_futures_ohlcv` inner-joined to `cd_open_interest` on `timestamp`
(hourly). Both are live-read; the bot refuses entries when either is stale.

# CHENTO_TRIPLE_V3 — calibration log

Single source of truth for "what is calibrated right now" and the provenance
of each change. Update this file in the same commit as any config/sizing
change (definition-of-done rule from the pool plan's A4, carried into the
bot-extraction plan).

## Current state (2026-07-21)

**Strategy params** (`strategies/sleeves/chento_triple_v3/config.py`) —
unchanged from the 2026-06-05 calibration: 5×ATR(14) stop, 6R target, 72h
TIF, B1-anchored trigger (`b1_now & b5_w & b7_w`, 24h window), 4 filters ON
(no_tilt, no_resist_OB_2R, okx_aligned, skip_up_30d_shorts),
LADDER_ENABLED=False (P1 backward-only verdict), 6h cooldown.

**Bot-level** (`bots/chento_v3/config.py`, standalone bot since 2026-07-21):
- Variant `bot_chento_v3_v1`, capital $10,000 paper.
- Sizing: fixed-R **2%**/trade over the sleeve's own 5×ATR stop
  (notional = capital × 2% / stop_pct), notional cap 3× capital,
  alloc/leverage expressed via Intent replace — sleeve code untouched.
- Diagnostics permanently ON (`CHENTO_V3_DIAG=1`,
  `bots/chento_v3/logs/diag.jsonl`).
- Stale-input policy: mgmt tables (cd_futures_15m, btc_1m) stale → skip
  tick; entry tables (okx_perp_1h, ca_long_short_ratio) stale → sweep runs,
  entries refused loudly.

## Change history

| Date | Change | Why / provenance |
|---|---|---|
| 2026-07-21 | B5 `compute_lsr_extremes` min_periods `max(8, w//4)` → `w//4` | Byte-equivalence violation vs `validation_B5_lsr_extremes` caught by new `tests/test_chento_parity.py` (NaN-mask diff in warmup rows 7-8; zero live impact). Research semantics are the validated ones. |
| 2026-07-21 | Extracted to standalone bot (`bots/chento_v3/`), fixed-R 2% sizing, diag on | Bot-extraction plan M1. Previous life inside P-300: **zero trades ever — OKX gate was stale-locked since 2026-05-27** (okx_perp_1h had no live writer). Feed now refreshes OKX hourly; runner refuses stale inputs loudly. |
| 2026-06-05 | atr5_t6R + no_tilt + no_resist_OB_2R confirmed; ladder disabled | Triple composite optimization + P1 backward-only Pareto test (memories: chento-triple-optimized-config, chento-v3-p1-ladder-verdict) |
| 2026-06-04 | Fix A intra-bar walking; B1-anchored trigger | chento-v3-b1-anchored memory |
| 2026-05-30 | B7 resample-bucket → rolling-sum + median-z (byte-equivalence fix) | chento-v3-b7-bug memory |

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

## Replay baseline (shipped config: ladder OFF)

`__replay_p0gate` (2026-07-22, window 2025-06-06 → 2025-12-06, 15m ticks,
chento-only): **8 trades, +$1,043.37, WR 62.5%**. Supersedes
`__replay__rgap_fix_a` (+$962.28), which pre-dated the 2026-06-05
LADDER_ENABLED=False ship — all 8 entries byte-identical between the two;
the 3 exit diffs are exactly the baseline's ladder-widened (1.5R) stops
firing where the shipped 1R stop exits earlier. Use p0gate numbers for any
future replay-equivalence gate.

## Change history

| Date | Change | Why / provenance |
|---|---|---|
| 2026-07-22 | **P0 live boundary-eval fix**: live entry path anchors on wall-clock 15m boundaries, evaluates the JUST-CLOSED bar with final values (intraday cache refresh; forming bar never evaluated); `_just_closed_15m_ts` → last fully-closed bar (walker partial-bar protection). Replay path untouched (`clock.is_simulated()` branch). | Day-1 telemetry: 850/850 live evals `boundary_skipped` — entry path was dead (2nd live lockout after OKX). Gate: replay entries 8/8 byte-identical. |
| 2026-07-21 | B5 `compute_lsr_extremes` min_periods `max(8, w//4)` → `w//4` | Byte-equivalence violation vs `validation_B5_lsr_extremes` caught by new `tests/test_chento_parity.py` (NaN-mask diff in warmup rows 7-8; zero live impact). Research semantics are the validated ones. |
| 2026-07-21 | Extracted to standalone bot (`bots/chento_v3/`), fixed-R 2% sizing, diag on | Bot-extraction plan M1. Previous life inside P-300: **zero trades ever — OKX gate was stale-locked since 2026-05-27** (okx_perp_1h had no live writer). Feed now refreshes OKX hourly; runner refuses stale inputs loudly. |
| 2026-06-05 | atr5_t6R + no_tilt + no_resist_OB_2R confirmed; ladder disabled | Triple composite optimization + P1 backward-only Pareto test (memories: chento-triple-optimized-config, chento-v3-p1-ladder-verdict) |
| 2026-06-04 | Fix A intra-bar walking; B1-anchored trigger | chento-v3-b1-anchored memory |
| 2026-05-30 | B7 resample-bucket → rolling-sum + median-z (byte-equivalence fix) | chento-v3-b7-bug memory |

## 2026-08-23 — ETH leg (multi-asset plan Phase B)

- Sleeve asset-parameterized via `CHENTO_V3_ASSET` env (config resolves
  `cd_futures_eth_15m` / `okx_perp_eth_1h` / LSR asset='ETH'); BTC leg
  byte-identical (test_chento_parity.py green through the refactor).
- New bot `bots/chento_v3_eth` (variant `bot_chento_v3_eth`, $10k paper,
  2%/trade, 3x cap) — thin wrapper over the shared runner.
- Per-asset tilt policy per the overlay study + backward-only confirmation:
  BTC keeps FILTER_NO_TILT (skip-after-loss); ETH disables the skip and
  halves risk after a loss at the bot layer (TILT_HALF_AFTER_LOSS).
- Go/no-go basis: backward-only research pool ETH +0.70R mean / 44% WR /
  n=73 (2021→2026-05), attribution timing +0.70R vs regime −0.10R.
  Underwrite expectancy: ~+0.7R region, NOT the +1.28R research figure.
- Paper gate: ≥2 months or ≥10 trades; kill at < +0.3R/trade after 15
  trades (plan doc: studies/material/plans/multi_asset_chento_plan.md).

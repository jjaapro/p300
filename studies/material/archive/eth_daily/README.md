# ETH_DAILY — Passive ETH LONG, regime-gated

Continuous ETH LONG position that exists only while the regime is
`strong_bull` or `mild_bull`. Captures ETH's bull-market beta directly,
sized by regime weight × vol-target leverage.

## Signal

There isn't one in the traditional sense — this is a "long ETH at
regime-weighted size" position. The trigger is the regime classifier
flipping in or out of bull mode.

- **Open**: on the day the regime classifier flips into `strong_bull` or
  `mild_bull` (regime weight goes from 0 → positive).
- **Close**: on the day the regime classifier flips out of bull
  (regime weight goes back to 0).
- Per-day rebalancing while open: SCALE if today's `weight × lev` notional
  diverges from the open position; LEVERAGE_ADJUST if vol-target lev changed.

## Per-tick state machine

| Open trade | desired_open | Action |
|---|---|---|
| none | False | noop |
| none | True (fresh bull entry) | OPEN at current price |
| none | True (already-bull when bot started) | `awaiting_fresh_bull_entry` (cold-start guard) |
| open | False | CLOSE (regime exited bull) |
| open | True | SCALE / LEVERAGE_ADJUST if size or lev drifted today |

## Sizing

`notional = capital × regime_weight['eth_daily'] × vol_target_lev`

No R4 inner multiplier (this is not an R4 sleeve). Vol-target outer
leverage only (1.5×–3.0× per regime, floored 0.5×).

## Cold-start guard

If yesterday already had the bull regime, the regime entry happened
before this variant was emitting trades — the handler reports
`awaiting_fresh_bull_entry` rather than chasing into mid-trend. See
memory `feedback_missed_entry_is_missed_entry.md`.

## Edge thesis

Pure long-ETH-beta exposure during bull regimes. ETH outperforms BTC on
the way up; this gives the portfolio that exposure when conditions are
constructive.

## Costs

Funding + fee + slippage charged on close (since 2026-05-13). Multi-week
ETH-LONG perp at 8h funding ~0.005% accrues to ~4.5%/yr — previously
invisible. See [AUDIT_2026_05_13.md](../../../AUDIT_2026_05_13.md).

## Files

- [signal.py](signal.py) — `eth_daily_try_fire` handler with the full state machine
- [config.py](config.py) — strategy key
- `__init__.py` — package marker

Inputs builder (`jplus/simulate.today_inputs`) and regime/vol-target
math stay in `jplus/` until restructure step 6.

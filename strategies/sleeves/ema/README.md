# EMA(BTC) — Weekly EMA-cross continuous BTC position

Continuous BTC position whose direction follows a weekly EMA(5/21)
crossover. Active in every regime (positive weight in all four).

## Signal

- **EMA(5) vs EMA(21) on weekly BTC closes** (BTC hourly aggregated to 168h
  buckets). `ema_p = +1` while LONG, `-1` while SHORT, `0` during warmup.
- Position state lives in [jplus/ema_sleeve.py](../../../jplus/ema_sleeve.py)
  and is read each tick via [jplus.simulate.today_inputs()](../../../jplus/simulate.py).
- **Entry**: at the **next weekly candle's open** after a cross is detected
  (strict T+1, no same-bar entry).
- **Exit**: at the **next weekly candle's open** after the reverse cross.
- Effectively always in one of LONG / SHORT / flat — once weekly EMAs have
  crossed, there is no idle-to-cash state.

## Per-tick state machine

The live handler reconciles the open trade against `today_inputs.ema_p`:

| Open trade | desired ema_p | Action |
|---|---|---|
| none | 0 | noop |
| none | +1 / −1 | OPEN at current price (cold-start guard: only on fresh cross) |
| open, same direction | same | SCALE / LEVERAGE_ADJUST if weight or vol-lev changed today |
| open, opposite direction | flip | FLIP at current price |
| open, any | 0 | CLOSE (defensive, rare) |

SCALE and LEVERAGE_ADJUST events are idempotent per UTC day via the
`trade_adjustments` table.

## Sizing

`notional = capital × regime_weight['ema_btc'] × vol_target_lev`

EMA_BTC has no R4 inner multiplier — the leverage stack is just
vol-target outer leverage (1.5×–3.0× per regime, floored 0.5×).

## Cold-start guard

If yesterday's `ema_p` already had today's value, the cross fired before
this variant was emitting trades. Rather than enter offside at today's
price, the handler reports `awaiting_fresh_cross` and waits for the next
genuine cross. See memory `feedback_missed_entry_is_missed_entry.md`.

## Edge thesis

Medium-term trend follower on BTC. Captures multi-week directional
moves; pays the spread/fee on whipsaws.

## Costs

Funding + fee + slippage charged on close (since 2026-05-13). Pre-fix
this sleeve was zero-fee + zero-funding — structurally wrong for the
perp it actually trades. See [AUDIT_2026_05_13.md](../../../AUDIT_2026_05_13.md).

## Files

- [signal.py](signal.py) — `ema_btc_try_fire` handler with the full state machine
- [config.py](config.py) — strategy key
- `__init__.py` — package marker

Signal math (`jplus/ema_sleeve.py`) and inputs builder
(`jplus/simulate.today_inputs`) stay in `jplus/` until restructure step 6.

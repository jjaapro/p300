# CARRY (S-078) — calibration log

## Current state (2026-07-22 — standalone bot)

**Strategy** (`strategies/sleeves/carry/config.py`, unchanged): enter
delta-neutral (spot-long + perp-short, equal notional) when 7d average BTC
perp funding > 0; exit on a 3-day negative-funding streak (sleeve
side-effect sweep). P&L = accrued funding − fees/slippage on 4 fills;
price P&L ≈ 0 by construction.

**Bot** (`bots/carry/config.py`): variant `bot_carry_v1`, $10,000 paper,
**fixed-notional 1× capital** (no stop exists → fixed-R doesn't apply;
risk is basis/funding, not price), 60s ticks, daily-idempotent decisions.
Mgmt tables: btc_1m + cd_funding_rate. Monitor eval limit 26h.

Replaces the stranded legacy CARRY (SJ-3452, closed 2026-07-22
`legacy_shutdown` at +$24.69 after 69 days of accrual). Note the funding
cadence cutover 2026-04-13 (1h predicted → 8h settlement) — the sleeve's
7d window is fully post-cutover in live operation.

## Change history

| Date | Change | Why / provenance |
|---|---|---|
| 2026-07-22 | Extracted to standalone bot; fixed-notional 1× | Bot-extraction plan P4; market-neutral diversifier for the long-heavy fleet |

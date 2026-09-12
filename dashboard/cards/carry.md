# How Carry S-078 works

**One line:** hold spot BTC long and an equal perp short at the same time — price
risk cancels out, and the position simply collects the funding payments shorts make
to longs while the funding rate is positive.

## The position
**Delta-neutral**: long spot + short perp, equal notional (1× capital, fixed).
Price moves change both legs equally and oppositely, so the price path is *not* the
P&L — **funding is the P&L**. That is why the dashboard shows no stop, no target,
no timed stop, and no "unrealized" number for this bot: none of those concepts
apply to a neutral structure.

## Entry
When the **7-day rolling average daily funding turns positive** (> 0). Positive
funding = longs paying shorts = the perp-short leg is being paid to exist.

## Exit
When the **trailing 30 days of funding add up to less than −0.5%** of notional —
a sustained negative regime, where the position pays instead of collecting. A
full round trip costs ~0.24% of notional, so single bad days (or three of them)
are not worth leaving over: the old three-negative-day exit paid that toll about
five times a year and never left before the damage. The rule changed on
2026-09-12 (`docs/calibration/carry.md`).

## Character
Positions are held for **weeks** (the current one entered in July). Earnings are a
steady drip, not trade-shaped wins. CARRY earns exactly in the regimes where
SHORT_SQUEEZE sleeps and vice versa — they are designed complements.

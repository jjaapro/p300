# How Carry S-078 (ETH) works

**One line:** the BTC carry bot's rule on ETHUSDT — long spot ETH and an equal perp
short, collecting the funding shorts are paid while the 7-day average is positive.

## Why a second asset
The pre-registered study (`studies/notebooks/carry_eth_2026_09/`, 2026-09-19)
found the shipped rule nets about 12.8 %/yr on ETH settlement prints, and that
the two assets' worst funding stretches do not coincide: an equal-weight BTC + ETH
book earned 15.1 units of net per unit of drawdown against 4.9 for BTC alone. It
is breadth, not a new edge: same entry, same CUM-30D exit, its own $10,000.

## What differs from the BTC bot
Funding comes from `cd_funding_rate_eth` (8-hour settlement rows), the entry and
exit price from ETH spot (`eth_1m`). Costs are the BTC assumption (0.20 % per
round trip), declared, not measured on ETH. In ETH's worst 90 / 180-day windows
the CUM-30D exit lost more than never exiting; that is in the study and is the
risk this twin runs.

## Character
Held for weeks or months; the research history has one completed episode in
seven years. The dashboard's per-trade research warning is not meaningful for
this bot (n = 1), as for BTC carry.

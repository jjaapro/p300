# How Chento Triple v3 ETH works

**One line:** the same Triple composite as the BTC leg, pointed at ETH — validated
multi-asset in the 2026-08 overlay study (+75% portfolio R at BTC-alone drawdown,
measured on the OKX-gated trade set).

## Same engine, different asset
Identical trigger and filter stack to [the BTC leg](#) — B1 money-flow divergence ∩
B5 LSR extreme ∩ B7 multi-TF alignment, with the order-block veto and the
asymmetric up-30d short skip. The OKX-delta alignment filter (against
`okx_perp_eth_1h`) was retired on both legs 2026-09-13. Reads
`cd_futures_eth_15m` / `eth_1m` and the ETH rows of the long/short-ratio table.

## The one deliberate difference: tilt policy
The BTC leg **skips** the next trade after a loss. On ETH the backward-only rerun
showed skipping and half-sizing have the same MAR — but half-sizing keeps ~64% more
income (both on the OKX-gated trade set). So the ETH leg **halves risk on the trade
after a loss** instead of skipping it (bot-level `TILT_HALF_AFTER_LOSS`).

## Exits & sizing
Same as BTC: 5×ATR(14) stop, 6R fixed target, 72h TIF, fixed-R 2% per trade,
3× notional cap. Positions stack the same way (up to 6 open at once in the ungated
study).

## Status
Live-paper since 2026-08-23 (the ETH data tables were revived from freeze the same
day). It did not fire while OKX-gated (through 2026-09-14; ~15 a year in the study,
slightly less frequent than BTC); with the gate off since 2026-09-14 the study rate is
~34 a year. The diag counters below show the gate-by-gate story each day.

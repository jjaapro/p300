# Cross-study comparison — R4, SqueezeBull, PDO (2026-09-09)

Descriptive only. This folder proves no hypothesis and has no decision rule; it
re-reads the per-trade ledgers three already-concluded studies left on disk and
puts them on one axis. The verdicts live in those studies, not here:

- [`r4_bot_prep`](../r4_bot_prep/findings.md) — R4 stop and late-entry calibration
- [`squeeze_bull_revalidation`](../squeeze_bull_revalidation/findings.md) — BUILD, thin
- [`pdo_adjacents`](../pdo_adjacents/findings.md) — keep −10%, CDO retouch KILL

Run: `python studies/notebooks/strategy_comparison_2026_09/compare.py`

## The common basis, and what it is not

Each study books outcomes in its own unit. R4 and PDO use basis points of the
position notional; SqueezeBull uses R multiples against a 2 % stop; the CDO
retouch variant uses R against a 1 % stop. Everything is converted to **percent
of position notional**:

| source | conversion |
|---|---|
| R4, PDO question (a) | `net_bp / 100` |
| SqueezeBull | `r_outcome × 2.0` (1 R = the 2 % stop distance) |
| PDO CDO retouch | `R × 1.0` (1 R = the 1 % stop distance) |

The equity curve is then **one unit of notional per trade, unlevered, trades in
entry order**, identically for all three, so max drawdown is in percent of
capital under one sizing assumption.

Three things this deliberately is not:

1. **Not any strategy's shipped sizing.** R4 fires at up to 1.5× capital per leg
   (`VARIANT_WEIGHT` 0.20 × `LEV_CAP` 7.5) and holds up to three legs at once, so
   its real per-trade P&L is a multiple of the numbers here. SqueezeBull has no
   shipped sizing at all — it is not built.
2. **Not a portfolio drawdown for R4.** R4's windows overlap by design, and a
   sequential cumulative curve flattens concurrent legs into a queue. The
   measured worst *simultaneous* cluster at shipped sizing is −21.0 % of variant
   capital pre-ETF and −15.2 % post-ETF; see `docs/calibration/r4.md`.
3. **Not risk-adjusted for frequency.** R4 fires 144 times a year against PDO's
   34, so R4's larger cumulative total partly reflects more deployments of the
   same unit, not a larger edge per trade. Compare `mean_pct` and
   `profit_factor` for that, and the deflated Sharpe in each study for whether
   the edge survives its own search.

Costs are each study's own: 15 bp round trip for R4 (production `trades.py`
defaults), 18 bp for SqueezeBull and PDO (research convention).

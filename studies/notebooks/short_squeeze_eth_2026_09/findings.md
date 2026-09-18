# SHORT_SQUEEZE on ETH — findings

**CONCLUDED 2026-09-19. Verdict: KILL for ETH.** The shipped rule fires 70 times on ETH over 2022-03 → 2026-09 and
nets **+0.14 R** per trade at the measured 10 bp round trip, with the **second half of the triggers negative**
(+0.37 / −0.09 R), DSR 0.75, and the whole edge in 2022 (38 of 70 triggers, +13.3 R net; the 32 trades since net
−3.7 R). Three of the four decision conditions fail. Pre-registered in [README.md](README.md), frozen before any ETH
number (`results/freeze_F0.json`), one outcome run (`results/report.json`), reviewed in
[short_squeeze_eth.ipynb](short_squeeze_eth.ipynb). Nothing in production changes.

## The three runs

The engine is the execution study's port of the sleeve's notebook, verbatim, with its five table loads injected.
`BTC_prod` is the engine on prod's tables (the anchor); `BTC_panel` the same engine on tables the new builder makes
from the on-disk panels (the fidelity run); `ETH` the same builder and engine on ETH. Gross is 0 bp per leg; net
charges the 10 bp round trip against each trade's own risk distance (median 0.34 % on BTC, 0.54 % on ETH).

| run | span | n | gross R | net R | net CI90 (30-day blocks) | win | PF net | halves net | DSR net | MAR net | cost ÷ gross | exits stop / target / time |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC_prod | 2022-01-30 → 2026-09-18 | 71 | +0.498 | **+0.179** | −0.14, +0.50 | 45.1 % | 1.25 | +0.15 / +0.21 | 0.81 | 0.37 | 64 % | 39 / 21 / 11 |
| BTC_panel | 2020-09-01 → 2026-09-13 | 81 | +0.412 | +0.107 | −0.22, +0.44 | 42.0 % | 1.14 | +0.07 / +0.14 | 0.71 | 0.21 | 74 % | 47 / 23 / 11 |
| **ETH** | 2021-12-01 → 2026-09-13 | **70** | +0.344 | **+0.138** | **−0.21, +0.48** | 38.6 % | 1.19 | **+0.37 / −0.09** | **0.75** | 0.22 | 60 % | 43 / 18 / 9 |

ETH net per year: 2022 **+0.35 R × 38** (+13.3 R), 2023 −0.01 × 12, 2024 none, 2025 −0.23 × 11, 2026 −0.12 × 9.
The gross CI90 on ETH touches zero at its low end (+0.000 to +0.70): even before cost the ETH edge is not
distinguishable from nothing at 90 %.

## Decision, condition by condition

(a) n 70 ≥ 30 — passes. (b) net +0.138 R < the +0.20 R bar, and its CI90 includes 0 — fails. (c) second half −0.09 R
— fails. (d) DSR 0.747 < 0.95 — fails. (e) fidelity — passes (below). **KILL for ETH.**

The same bar applied to the BTC sleeve itself: net +0.18 R (CI90 includes 0), DSR 0.81 — it would not pass either,
which is the execution study's "retire pending user decision" seen again from the other asset. The cost is the
whole story on both: 60–64 % of gross at 10 bp, because the stop sits 0.3–0.5 % away and 10 bp is a fifth of it.

## Preconditions

- **P0, the engine.** On prod's BTC tables the injected-table copy produces the port's trigger set exactly. The
  anchor to 2026-05-18: n 70 (expected 70), mean +0.392 R (0.40), PF 1.644 (1.65) — within tolerance; win rate
  0.457 against the quoted 0.443, which is **one trade of seventy on the other side of zero** since the port's
  numbers were taken on 2026-09-12 (prod's tables have moved: item 30 re-stamped the open-interest table on
  2026-09-18, and the 1-minute spot path is refreshed daily). The code's win-rate tolerance (0.005) was tighter than
  one flipped trade (0.014), so the run records P0 as not passed on that number alone. The decision rule does not
  read P0; it is reported, not hidden.
- **P1, the builder.** Over the common span the panel-built BTC run reproduces 71 of the prod-table run's 71
  triggers and adds one (2022-02-02 15:15, the archive-versus-CoinDesk open-interest difference at the macro gate's
  0.5 % Asia threshold): Jaccard **0.986**, and the 71 shared trades' P&L agree to **0.000 R** (same path, same
  fills). The builder is faithful; the ETH numbers are decision-bearing. Its extra 2021 triggers (the BTC archive
  starts 2020-09, prod's open interest 2022-01) are outside the anchor's span.

## What was built, and what it is for

`ss_eth_lib.tables_from_panels(asset)` makes the five tables the sleeve reads — `cd_futures_15m` and `cd_spot_15m`
from the 1-minute perpetual and spot panels (bar for bar what prod holds for BTC, verified to the cent), hourly
`cd_futures_ohlcv`, and `cd_open_interest` with the close-of-hour convention item 30 restored (the 5-minute archive's
snapshot at H + 1 h) — for any asset the panels cover. That is the twin-table route the roadmap said SQUEEZE_BULL on
ETH would need too; it exists now, and the sleeve's own engine ran on it unchanged.

## What this closes

SHORT_SQUEEZE on ETH at the shipped rule. Not re-proposed without a rule change that survives on BTC first — the
sleeve's own re-cut at n = 20 / 30 decides whether there is a rule to twin. Alts stay blocked (no screener feed
since 2026-05-23).

# Vendored front-end libraries

Committed to the repo so the dashboard works fully offline (no CDN).
Upgrading either file = re-test the chart hover/click mechanics and the
markdown rendering in the bot info tabs.

| File | Package | Version | License | Source |
|---|---|---|---|---|
| `lightweight-charts.standalone.production.js` | lightweight-charts (TradingView) | 5.2.1 | Apache-2.0 | https://unpkg.com/lightweight-charts@5.2.1/dist/lightweight-charts.standalone.production.js |
| `marked.min.js` | marked | 16.4.2 | MIT | https://unpkg.com/marked@16.4.2/lib/marked.umd.js (UMD build; local name kept for stable script tags) |

Downloaded 2026-08-24. Licenses:
- Lightweight Charts™: Apache License 2.0, © TradingView, Inc.
  (license header retained at the top of the file). The Lightweight
  Charts™ attribution notice: this dashboard uses TradingView's
  Lightweight Charts™ library.
- marked: MIT, © 2011-2025 Christopher Jeffrey / MarkedJS
  (header retained).

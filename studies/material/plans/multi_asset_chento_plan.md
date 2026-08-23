# Multi-asset Chento (BTC+ETH) — implementation plan

*2026-08-23. Basis: overlay study + backward-only confirmation
(studies/notebooks/overlay_study/findings.md). Combined BTC+ETH on the
production-faithful pool: +72% total R at lower drawdown than BTC alone, MAR-like
11.8 vs 6.0. Honest expectancy to underwrite: ~+0.8R/trade, 42% WR, ~30 trades/yr/asset.
OP excluded (no LSR → degraded composite ≠ same strategy).*

## Decisions locked by the studies

- ETH runs the **full** Triple composite (B1∩B5∩B7, ETH LSR live since 2021).
- Exits unchanged: 5×ATR stop / 6R fixed target / TIF 72h. No wick exit (rejected),
  no half-risk tag (rejected as specified), LADDER stays off.
- Tilt policy per asset: **BTC keeps `no_tilt` (skip-after-loss)**; ETH ships
  **half-after-loss** (beat/tied skip on ETH in both pools while keeping ~64% more
  income). Config-driven so it can be flattened later.
- Orchestrator treatment: chento composite remains **one orchestrator unit**
  (feedback memory: composite sleeves = one unit); BTC and ETH legs share its
  allocation under the existing shared-pool/fixed-priority policy. Concurrent
  BTC+ETH signals are correlated — cap combined open risk at the unit level,
  BLOCK (never resize) on cap, per the allocation policy.

## Phase A — revive the two frozen ETH feeds (prod, feed.py)

1. `data/sources/binance.py`: add ETH to the 15m futures fetcher path writing
   `cd_futures_eth_15m` (same schema incl. taker split — table exists, PK verify via
   `PRAGMA index_list` per the day-1-PK rule) and extend `fix_all_gaps()` to heal it.
   Backfill the 2026-05-26 → now hole first via the existing
   `pipelines/backfill_eth_op.py` machinery (Binance history covers it — ~90 days).
2. `data/sources/okx_perp.py`: `refresh_latest('ETH-USDT-SWAP', table='okx_perp_eth_1h')`
   wired into the hourly OKX throttle alongside BTC; backfill the hole
   (OKX history-candles reach it; verify — if not, ETH OKX gate starts fresh and the
   ETH leg waits out its 7-day z-warmup).
3. **Same commit** (table-freshness contract rule): move both tables from
   `FROZEN_TABLES` to `FRESHNESS_CONTRACTS` in botlib (`45*60` / `3*3600` like their BTC
   twins) — the completeness monitor enforces the bookkeeping automatically.
4. Operator: restart feed.py; `python monitor.py --deep` next day to confirm the holes
   healed and no interior gaps.

## Phase B — ETH bot variant (no new bot process)

5. Parameterize `bots/chento_v3` by asset: `ASSETS = [{'BTC': tables...}, {'ETH':
   cd_futures_eth_15m / eth_1m / okx_perp_eth_1h / LSR asset='ETH'}]`; one runner, one
   heartbeat, per-asset variant ids (`bot_chento_v3_eth`), per-asset tilt policy
   (BTC skip / ETH half-after-loss). `eth_1m` already live for mgmt sweeps.
6. **Byte-equivalence gate** (verify-byte-equivalence memory): assert the ETH feature
   pipeline (B1/B5/B7 values) matches `validation_multi_asset`'s research output at
   ≥5 known timestamps before any paper trade; add to `tests/test_chento_parity.py`.
7. Backward-only 6-month ETH replay through `backtest_runner`-equivalent path (or the
   research backward-only pool restricted to the window) as the go/no-go: expectancy
   within family of the study's ETH backward numbers (+0.7R region, not the +1.65
   research figure).

## Phase C — paper rollout + monitoring (day-1 requirements)

8. `monitor.py BOT_EXPECTATIONS`: chento eval cadence already covered by the shared
   heartbeat; add ETH-leg silence check if the ETH variant gets its own last_eval field.
9. Paper-trade ≥2 months or ≥10 ETH trades before any allocation weight; compare
   realized vs the backward-only expectancy band (strategy_health-style check).
10. Calibration log: `docs/calibration/chento_triple_v3.md` gets the ETH section +
    per-asset tilt policy rationale; PORTFOLIO.md composition note.

## Explicitly out of scope

- OP (needs an LSR source first — Coinalyze backfill + live fetcher would be its own
  small project with a 30d-retention clock).
- Any exit-model change; any ladder revival; scanner-universe expansion of chento.

## Kill criteria

ETH leg paper expectancy < +0.3R/trade after 15 trades, or parity assertions drift,
or the ETH feeds breach freshness contracts chronically → disable the ETH variant
(config flag), keep the feeds (they serve research regardless).

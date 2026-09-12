# astro_validation

Turns the trading framework extracted in
`telegram_export/out/astro_s_order_flow_institutional_framework/ANALYSIS.md`
into executable tests against 5.7 years of market data, and reports what survives.

**Answer: none of the nine claims survived.** Full write-up in [RESULTS.md](ASTRO_FINDINGS.md).

## Layout

```
src/
  config.py     FROZEN parameter grids — the pre-registration (read this first)
  data.py       Binance Vision bulk archives -> cached parquet
  features.py   ATR, swing pivots, TPO/volume profiles, OI states
  stats.py      block bootstrap, matched controls, multiple-testing, cost model
  run_all.py    orchestrator
  tests/
    c1_ny_times.py         the four "institutional" times vs real anchors
    c2_squeeze.py          Onset of the Squeeze — 3,888-cell sweep
    c2_pooled.py           pooled + canonical + matched control
    c3_cvd_divergence.py   4 divergence classes + the persistence trap
    c4_c7_profile.py       IB width, poor highs, pdVA, POC migration
    c9_robustness.py       exit regimes, ETH instrument-OOS, year by year
data/           cached parquet + raw archive zips (gitignored)
out/            every result table as CSV
```

## Data

All public, no API key.

| series | source | span |
|---|---|---|
| futures + spot OHLCV, 5m | `data.binance.vision` monthly klines | 2021-01 → 2026-08 |
| open interest, 5m | `data.binance.vision` **daily metrics** | 2021-01 → 2026-08 |
| funding, 8h | `fapi.binance.com/fapi/v1/fundingRate` | full history |

The OI series is the reason this is possible at all: the REST endpoint
`/futures/data/openInterestHist` caps at **30 days**, far too short for a backtest,
while the daily `metrics` archive carries 5-minute OI back to 2021.

Delta/CVD is exact rather than estimated — Binance klines carry `taker_buy_base`
(volume where the buyer was the aggressor), so `delta = 2*taker_buy - volume`.

First run downloads ~2.5 GB and takes ~20 minutes; everything is cached as parquet
afterwards and the suite then runs in ~15 minutes.

## Method commitments

From `ANALYSIS.md` §C11, all enforced in code:

1. **Grids frozen before execution** — `config.py`, one documented amendment
2. **Full parameter surfaces reported**, never the best cell — `out/c2_surface.csv` has all 3,888
3. **30% holdout** plus a second instrument (ETH) never used in any design decision
4. **Matched controls** — random entries, same count, same stop-distance distribution
5. **Costs on every leg** — 13 bps round trip, plus realised funding
6. **Negative results reported with equal prominence** — which is nearly all of them

Two look-ahead bugs were found and fixed during development; both are documented in
the code and in RESULTS.md rather than quietly corrected, because both initially
produced *positive* results (a 76% win rate that was entirely artefact).

## Usage

```bash
python src/run_all.py                  # everything
python src/run_all.py --only c2 c2p    # one test
python src/data.py --symbols BTCUSDT   # just prefetch
```

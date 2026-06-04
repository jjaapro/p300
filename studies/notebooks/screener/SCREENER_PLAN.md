# Top-N-coin setup screener — plan

## Context

Chento-style discretionary mimicry (see [../chento_journal/VALIDATION_PLAN.md](../chento_journal/VALIDATION_PLAN.md)) is one bet. This is the other: instead of reverse-engineering one trader on one asset, **scan a wide universe of coins for setups whose forward-return distribution is statistically edged**, and trade only the high-confidence triggers as they appear.

The two efforts are complementary:
- **Chento sleeve** = bet that one human's pattern-matching transfers to code, on BTC/ETH/OP
- **Screener** = bet that *some* setup × *some* coin has stable expectancy, found by exhaustive search across a wider asset set

The screener is *quantifiable from day 1* (clear setup → clear backtest → clear pass/fail) where chento required 3 weeks of qualitative reverse-engineering to get to the same place. Lower analytical risk, but bigger ingestion + compute cost.

## Hypothesis

> Across a wide universe of liquid crypto perps, there exist **simple, codifiable price-action setups** (e.g. oversold-bounce, breakout-of-multi-day-flag, round-number-rejection) that have **stable forward-return expectancy** when filtered by liquidity + volatility + regime. The screener finds them, ranks by edge stability, and either (a) feeds a new sleeve, (b) augments the chento sleeve as a candidate gate, or (c) becomes a daily signal feed for discretionary review.

## What "edge" means for a setup

For each candidate setup (defined as a deterministic trigger function `f(history) → bool`):

| Metric | Target |
|---|---|
| Forward N-bar return (N ∈ {1d, 3d, 7d, 14d}) — mean | ≥ baseline asset mean + 1σ |
| Per-trigger Sharpe (mean / stdev of forward returns) | ≥ 0.30 |
| Frequency | ≥ 50 triggers/year across universe |
| Stability — top-half vs bottom-half of date range, both positive expectancy | yes |
| Robustness — small parameter perturbations don't flip sign | yes |
| Coin diversity — edge doesn't concentrate in one coin (≤30% of triggers from one asset) | yes |

All gates must pass for SHIP. Most candidate setups will fail. That's fine — the screener is a sieve.

## Universe construction

**Tier 1 universe** — start here:
- Binance USDT-perp pairs
- Filter: 24h quote volume > $10M (median over last 30 days)
- Result: ≈100-150 coins
- Source: `GET /fapi/v1/ticker/24hr` then filter

**Tier 2 universe** — expand if Tier 1 yields good setups:
- Top 200 by market cap (CoinGecko `markets` endpoint)
- Cross-reference with Binance perp availability
- Drop wrapped / stablecoin / non-USDT-quoted

**Data needed per coin:**
- Daily OHLCV — 3+ years history (~1,100 bars/coin × 200 coins = 220k rows; trivial)
- 1h OHLCV — 1+ year history (~9k bars/coin × 200 coins = 1.8M rows; small)
- 15m OHLCV — only for coins with shipped setups (don't pre-ingest; pull on demand)
- Funding rate — daily mean is enough (per-coin × per-day; 200 × 1100 = 220k rows)
- Open interest — daily snapshot
- Optional: long/short ratio (Binance `/futures/data/topLongShortAccountRatio` — has 30d cap per call; backfill via Coinalyze)

## Candidate setups to backtest

These are starter hypotheses; the screener will likely test a dozen and find that 1–2 carry edge. Each is a deterministic trigger function:

### S1 — Oversold bounce (mean-reversion)
- 7d return < −20% AND RSI(14) < 30 AND today's close > today's open (reversal candle) AND today's volume > 1.5× 20d mean
- Entry: close. Stop: today's low − 1 ATR. Target: 7d-VWAP or +3R, whichever first.

### S2 — Bull flag breakout (momentum continuation)
- Prior 20d trend: +30% (slope on log close)
- Last 5–10d: consolidation (range < 8%, no new highs)
- Trigger: close breaks above 10d high on volume > 1.5× consolidation mean
- Entry: trigger close. Stop: flag low. Target: prior leg height projected from breakout.

### S3 — Round-number support hold
- Price approaches major round number (5%, 10%, 25%, 50%, 100% rounds) from above
- Within 1% of round AND prior 2 candles printed lower closes AND current candle prints higher low than prior
- Entry: round + 0.5%. Stop: round − 1%. Target: 2R or 7d-VWAP.

### S4 — Funding-flush long
- Funding rate flips negative (was positive 24h ago)
- Open interest drops > 5% in 24h
- Price held within −5% of prior close
- Entry: 24h after the flush completes. Stop: flush low. Target: pre-flush level.

### S5 — Post-listing IPO bounce
- New Binance perp listing within last 30d
- Price down > 40% from listing-day high
- 3d consolidation at lows
- Entry: break above 3d high. Stop: 3d low.

### S6 — Gap fill
- Daily gap (close → next open) > 5%
- Gap unfilled for ≥ 3 days
- Trigger: price returns within 2% of gap target on declining ADX
- Entry: gap midpoint. Stop: 1 ATR beyond.

### S7 — Cross-exchange perp deviation (requires C4 ingestion from chento plan)
- Binance perp diverges from OKX perp by > X bps
- Reverts within Y hours historically
- Entry: short the premium / long the discount

### S8 — Spot CVD divergence
- 7d CVD positive (spot accumulation)
- Price flat or down over same window
- Trigger: first up-day with above-avg volume

### S9 — Whale-cluster long (requires C2 ingestion)
- Whale buy CVD ($10k+ trades) > 2σ of 30d mean for 3 consecutive 1h bars
- Mid+retail CVD neutral or negative (divergence)
- Trigger: close above 4h pivot

### S10 — Trend-aligned pullback (chento-style on wide universe)
- Daily ADX(14) > 25 (trending) AND close > EMA(50) (uptrend)
- 4h pullback to 21-EMA or prior demand zone
- Entry: 4h reversal candle at zone. Stop: zone low.

Many of these overlap with chento findings — the screener is essentially "apply chento-style setup logic to 200 coins instead of 3."

## Architecture

```
┌──────────────────────┐    ┌──────────────────────┐    ┌──────────────────────┐
│ data/sources/        │    │ studies/lib/         │    │ studies/notebooks/   │
│   binance_universe.py│ →  │   screener_setups.py │ →  │   screener/          │
│   binance_klines_    │    │   screener_runner.py │    │   S1_oversold.ipynb  │
│     bulk.py          │    │                      │    │   S2_bull_flag.ipynb │
│                      │    │                      │    │   ...                │
└──────────────────────┘    └──────────────────────┘    └──────────────────────┘
   ingests universe              defines setups as            per-setup backtest
   + per-coin OHLCV              functions + edge gate        + universe-level
                                                              expectancy
```

**Key components:**

1. **`data/sources/binance_universe.py`** — daily refresh of top-N USDT-perp universe + per-coin metadata (volume, market cap proxy, listing date)
2. **`data/sources/binance_klines_bulk.py`** — bulk ingestion of daily + 1h klines for the universe; appends to `screener_klines_daily`, `screener_klines_1h` tables with `asset` column from day 1
3. **`studies/lib/screener_setups.py`** — each setup is a function returning a DataFrame of triggers (asset, ts, entry, stop, target, setup_id, params)
4. **`studies/lib/screener_runner.py`** — given a setup function + universe + date range, computes forward returns per trigger and emits per-trigger ledger + summary stats
5. **One notebook per setup** under `studies/notebooks/screener/` — runs setup_runner, applies edge-gate criteria, decides SHIP/DROP

**Database tables (new):**

```sql
CREATE TABLE screener_universe (
    asset TEXT PRIMARY KEY,
    base TEXT, quote TEXT,
    listing_ts INTEGER,
    median_quote_volume_30d REAL,
    last_refreshed_ts INTEGER
);

CREATE TABLE screener_klines_daily (
    asset TEXT, ts INTEGER,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, quote_volume REAL,
    PRIMARY KEY (asset, ts)
);

CREATE TABLE screener_klines_1h (
    asset TEXT, ts INTEGER,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, quote_volume REAL,
    PRIMARY KEY (asset, ts)
);

CREATE TABLE screener_funding_daily (
    asset TEXT, ts INTEGER, funding_mean REAL,
    PRIMARY KEY (asset, ts)
);
```

Multi-asset from day 1 (asset column in PK) — no migration debt.

## Execution order

**Phase 0 — Universe + ingestion (1 session):**
- `binance_universe.py` (refresh top-N list, write to DB)
- `binance_klines_bulk.py` (backfill 3y daily + 1y hourly for top 150 coins)

**Phase 1 — Setup library + harness (1 session):**
- `screener_setups.py` with first 4 setups (S1, S2, S4, S10)
- `screener_runner.py` with edge-gate evaluator
- Smoke test on BTC alone

**Phase 2 — Per-setup notebooks (1 session per 2–3 setups):**
- Run each setup across universe, write per-setup JSON output
- Aggregate winners into `studies/notebooks/screener/results_summary.md`

**Phase 3 — Productization (only after ≥1 setup passes):**
- New sleeve `strategies/sleeves/screener/` that subscribes to live trigger feed
- Live ingestion job for universe OHLCV
- Trade-emission gated by same edge criteria as backtest

## How this fits with chento work

| Aspect | Chento plan | Screener plan |
|---|---|---|
| Hypothesis | One trader's pattern mimicked | Statistical edge in defined patterns |
| Data | BTC/ETH/OP + chento corpus | Top 150 USDT perps |
| Analytical risk | High (discretionary reverse-engineering) | Low (deterministic setup definitions) |
| Ingestion cost | Medium (alts, agg trades, cross-exchange) | High upfront (200-coin OHLCV) but bounded |
| Time to first ship | Notebook 1 + 2 + 3 (3-5 sessions) | Phase 0 + 1 + 1 setup (2-3 sessions) |
| Edge ceiling | If chento has real edge → unknown but potentially large | Likely small per-setup but additive across setups |
| Validation cleanliness | Lots of survivorship/selection bias to control for | Cleaner — define setup, walk forward, measure |

**Recommendation:** Run **chento Notebook 1 completion** in parallel with **screener Phase 0** (universe ingestion). They share zero code and zero data dependency. Once chento Notebook 1 is done and screener Phase 0 is done, we have two independent workstreams that can be evaluated on their own merits.

The screener may end up being the bigger value-driver if chento turns out to be more vibe than edge. But we won't know until both have produced their first results.

## Open questions (resolve before Phase 0)

1. **Universe size:** 100 (top by volume) vs 200 (top by mcap)? Volume-filter is cleaner for backtest but smaller pool.
2. **Backfill depth:** 3 years vs 5 years? Many altcoins didn't exist 5 years ago.
3. **Walk-forward vs random split:** Use 70/30 chronological? Or k-fold time-series?
4. **Lookahead-bias controls:** Each setup function gets only history up to its trigger ts — enforced by harness, not setup author.
5. **Transaction cost model:** Apply same 18 bps RT as chento sleeve? Or scale to per-coin liquidity?
6. **Edge gate stringency:** The 0.30 Sharpe target is borrowed from quant equity research. Crypto's vol structure may warrant tighter (or looser) gates — calibrate after first 3 setups produce a baseline.

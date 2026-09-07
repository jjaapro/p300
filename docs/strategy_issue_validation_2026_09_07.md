# Strategy issue validation — 7 September 2026

Initial read-only audit: working tree at HEAD `e5e1c10cbfe43852be470e32a365f297b9a6116c` and local `data/databases/prod.db`. The initial audit preserved existing changes and used read-only connections or synthetic inputs. The subsequently requested fixes and production repair are recorded below. This checks current source, not the version loaded by any running process.

## Follow-up fixes

### Minute-candle repair

**Applied to local prod.db: 218,908 incorrect rows replaced** using 75 checksum-verified Binance monthly spot 1m archives. Original rows, replacements, archive URLs and SHA-256 hashes are retained in `data/backups/minute-repair-20260907.db`. Production row count was preserved within the atomic repair transaction.

**Correction to the initial count:** all 218,919 exact cross-table matches reproduced, but comparing with genuine minute archives established that **11 are legitimate coincidences**, not incorrect candles: five flat zero-volume candles on 2021-02-11, five on 2023-03-24, and the 2021-04-25 04:00 UTC halt candle. All eleven exactly match Binance's minute data and were left intact. The confirmed incorrect-candle count is therefore **218,908**.

Post-repair checks find **zero proven fifteen-minute clones**. The broad equality check retains only those eleven verified genuine candles. At 2024-01-15 00:01 UTC the actual price reader now returns **41,755.38**, replacing the erroneous future close of 42,045.30. Summed minute volume for 00:00–00:15 is now **677.90385**, exactly matching the fifteen-minute reference.

The new `data.repair_minute_candles` command defaults to a read-only audit. Preparation validates selected archive rows, timestamps, OHLCV and checksums before any source mutation; application refuses changed originals and applies all updates transactionally. It retains recovery data and is idempotent. `health.py` now checks resolution across tables as well as cadence, requiring evidence of later trading to avoid halt-related false positives.

Validation: **45 tests passed** across minute repair, price feed and existing funding tests. The repair tests cover milliseconds/microseconds, early halt closes, invalid inputs, incomplete downloads, checksum errors, rollback, concurrent edits, preserved original/unrelated rows and idempotence. Existing research outputs and historical trade ledgers have not been regenerated; rerun affected studies against the corrected data.

### ADX and Thursday stop paths

Implemented chronological completed-minute OHLC checks in `strategies/support/stop_path.py` and integrated them with ADX, enhanced Thursday and the shared close pipeline. Recovered wick breaches now close at the stop; gap-through opens retain the worse price. Stops use their entry-recorded threshold, and ADX daily trailing levels become available only after that day's candle completes. Progress and missing ranges survive restarts.

The shared close pipeline also checks the path before scheduled exits, generic live backstops and end-of-window closes. Overdue Thursday exits are bounded at their scheduled time. A provisional newest live candle cannot be recorded as fully processed or used to finalize a recovered scheduled winner. Historical event times govern exit accounting and funding; closes across later recorded adjustments are refused. Position state and close persistence share a write transaction to prevent a concurrent resize changing the booked basis.

Validation: **186 targeted tests passed**, plus an independent **86-test** stop/trade/concurrency/equity review. Coverage includes recovered breaches, gap fills, coarse/minute replay parity, entry/finalization boundaries, restarts, delayed data, trailing-level chronology, frozen thresholds, scheduled and end-window exits, real generic live backstops, and concurrent resizing.

Remaining market-data granularity is explicit: OHLC cannot identify the exact time of a wick or whether a partially held entry-minute wick happened before entry. The partially held minute is excluded from path extremes; normal wick fills use the completed minute's time and configured transaction costs. Missing historical minutes are retried when supplied, rather than invented.

## Initial audit findings

| Reported issue | Verdict in this checkout |
|---|---|
| 218,919 fifteen-minute candles stored as minute rows | **218,919 matches reproduced; archive validation refined incorrect rows to 218,908, now repaired above.** |
| ADX / enhanced Thursday stop implemented by clipping completed-trade P&L | **Reported implementation not found in current code.** Current stops execute during management; synthetic breaches close before recovery. |
| ADX final P&L spread backward with inclusive-date overcount | **Reported implementation not found in current reporting.** Current realized returns book P&L once on exit. |
| Carry daily funding calculated as hourly average × 24 | **Historical bug confirmed, already fixed in current code on 4 May 2026.** |

## 1. Fifteen-minute data in `btc_1m`: confirmed

Joining `btc_1m` with `cd_spot_15m` on the same opening timestamp produced **218,919 exact matches across open, high, low, close, volume, and trade count**, from **2020-01-01 00:00 UTC through 2026-03-31 23:45 UTC**. This is every overlapping quarter-hour opening in that period, out of 3,283,733 stored minute rows before April 2026.

The same period has **zero basic OHLC range / negative-volume violations** under the query below. Those checks cannot distinguish a valid fifteen-minute candle from a valid minute candle. No exact matches across all six fields were found after March through the available September data; this is a specific cross-resolution check, not a certification that all other data is correct.

```sql
SELECT COUNT(*) AS exact_matches,
       datetime(MIN(m.open_time)/1000, 'unixepoch') AS first_utc,
       datetime(MAX(m.open_time)/1000, 'unixepoch') AS last_utc
FROM btc_1m AS m
JOIN cd_spot_15m AS s ON m.open_time = s.timestamp * 1000
WHERE m.open_time < 1775001600000 -- 2026-04-01 00:00 UTC
  AND m.open = s.open AND m.high = s.high
  AND m.low = s.low AND m.close = s.close
  AND m.volume = s.volume AND m.num_trades = s.total_trades;
-- 218919 | 2020-01-01 00:00:00 | 2026-03-31 23:45:00

SELECT COUNT(*) AS basic_violations
FROM btc_1m
WHERE open_time < 1775001600000
  AND (low > high OR open < low OR open > high
       OR close < low OR close > high OR volume < 0);
-- 0
```

**Actual runtime consequence:** calling `price_feed.get_current_price("BTC")` at simulated **2024-01-15 00:01 UTC** against the read-only production database returns **42,045.30**. That is the final close of the **00:00–00:15** candle, exposed **14 minutes early**. The next minute row's open is 41,755.39. The current [price reader](../strategies/support/price_feed.py#L82) correctly enforces a one-minute completion delay, but trusts the table's stated resolution.

**Volume consequence:** for the same first quarter-hour, stored fifteen-minute volume is **677.90385 BTC**, while summing all 15 purported minute rows produces **1,275.64309 BTC**. The first row already contains the whole interval's volume. Consumers that resample minute data with summed volume inherit this overcount, including [Chento Limit Bid](../strategies/sleeves/chento_limit_bid/signal.py#L123).

**Historical cause supported by repository code:** initial commit `d434a32d7f9430ead03f6fea052b2440498989a1` contains `seed_data.py`. Its lines 10–13 explicitly describe renaming upstream fifteen-minute data to `btc_1m`; line 27 maps `("btc_15m", "btc_1m")`; lines 65–83 copy the values unchanged. Inspect with:

```powershell
git show d434a32:seed_data.py
```

The seeder was deleted in `dc694ea` on 4 May 2026. This is a concrete mechanism matching the observed corruption; an ingestion audit trail proving when this particular database was seeded was not available.

**Why it persists:** the [current feed](../data/sources/binance.py#L86) requests genuine `1m` candles starting at the newest stored timestamp. Historical [gap filling](../data/sources/binance.py#L514) fetches missing timestamps, leaving existing bad quarter-hour rows intact. [Continuity checks](../health.py#L354) inspect cadence and gaps, not candle content across resolutions.

Repair requires replacing affected rows from genuine minute history, verifying cross-resolution consistency, and rerunning results that consumed the affected data. Ordinary gap filling is insufficient. The contamination's effect on each strategy's performance was not quantified here.

## 2. ADX / Thursday stops and daily returns: quoted implementations absent

The current ADX and Thursday paths do not use the reported completed-trade clipping adapter:

- [ADX management](../strategies/sleeves/adx/signal.py#L387) checks the current price against the stop on each evaluation and closes at the observed price.
- [Enhanced Thursday management](../strategies/sleeves/timing_anomalies/internal/thu_bear/signal.py#L242) does the same.
- [Trade close accounting](../strategies/trades.py#L317) computes P&L from the actual supplied exit price with costs/funding, without a desired-loss floor.
- The current [ADX research harness](../studies/notebooks/adx_study/harness.py#L190) checks each bar's low/high against the hard stop before processing later decisions.

Synthetic probes called the actual functions with isolated inputs:

| Probe | Observed result |
|---|---|
| ADX long: entry 100, 10% stop, observed prices 88 then 112 | Closed once at 88, **−12% gross**, before recovery. |
| Enhanced Thursday short: entry 100, 5% stop, observed prices 108 then 88 | Closed once at 108, **−8% gross**, before recovery. |
| ADX research: entry 100, later bar low 85 and recovered close 112 | Exited at hard stop 90, **−10% gross / −10.1% net** under that harness. |

For daily returns, [the canonical implementation](../strategies/support/strategy_health.py#L171) sums closed-trade dollar P&L by `actual_exit_time` date. [The backtest report](../backtest_runner.py#L278) uses this implementation. It does not divide ADX P&L by `bars_held` or spread terminal outcomes across earlier dates.

An in-memory ledger with +12% realized P&L relative to portfolio capital, entry January 1 and exit January 4, produced:

```text
January 1: 0%; January 2: 0%; January 3: 0%; January 4: +12%
Sum: +12%, not +16%.
```

**Related limitations remain.** Runtime stops use sampled minute closes, not a full intraminute trade path; a breach and recovery between observations can still be missed. Standalone ADX defaults to [60-second ticks](../bots/adx/config.py#L24); `backtest_runner.py` defaults to hourly ticks, while the separate simulation CLI defaults to 60 seconds. Results therefore depend on replay cadence. The research harness's assumed stop fill is also not proof of executable intrabar fills.

Current daily reporting is explicitly **realized P&L only** and [excludes open positions](../strategies/support/strategy_health.py#L185). Its equity, drawdown, and volatility measures do not capture unrealized intratrade risk. This differs from the reported backward spreading and arithmetic overcount, which were not found.

**Validation:** 74 existing tests passed across `tests/test_sleeves.py`, `tests/test_trades.py`, `tests/test_adx_bot.py`, and `tests/test_strategy_health.py`. These supplement the synthetic probes above. Tests used a fresh temporary directory, `python -B`, and `-p no:cacheprovider`; a first attempt encountered a protected shared pytest temporary directory, then the rerun with explicit writable temporary storage passed.

## 3. Carry funding: old arithmetic reproduced, current code fixed

Read-only production checks and calls to the actual current functions returned:

| UTC day | Stored rows | 00/08/16 rows | Old average × 24 × 100 | Current daily result |
|---|---:|---:|---:|---:|
| 2024-01-15 | 24 | 3 | 0.240000% | **0.030000%** |
| 2026-09-06 | 3 | 3 | 0.069576% | **0.008697%** |

Every stored hourly rate on 15 January 2024 is `0.0001`. The claimed **8×** discrepancy reproduces exactly when applying the old formula:

```sql
SELECT COUNT(*) AS stored_rows,
       AVG(fr_close) * 24 * 100 AS old_daily_pct,
       SUM(CASE WHEN timestamp % 28800 = 0 THEN 1 ELSE 0 END)
         AS boundary_rows,
       SUM(CASE WHEN timestamp % 28800 = 0 THEN fr_close ELSE 0 END)
         * 100 AS boundary_daily_pct
FROM cd_funding_rate
WHERE timestamp >= 1705276800 AND timestamp < 1705363200;
-- 24 | 0.24 | 3 | 0.03
```

Commit `2ca7cdc7f61d3a04fc49de745834175ceec93b71`, dated **4 May 2026**, removed the faulty formula from the former `services/carry_service.py`. Current [carry loading](../strategies/sleeves/carry/signal.py#L59) delegates to [funding.daily_sums_pct](../strategies/support/funding.py#L161), which sums boundary-hour values and requires three samples for a complete day. Trade closing uses the similarly filtered `accrued_pct`.

Executing current `daily_sums_pct`, `accrued_pct("SHORT")`, and carry `_load_recent_daily_funding` for the January example returned **0.03%**. Carry also excludes the current incomplete day. **All 20 existing funding tests passed**, including hourly-data and non-boundary-sentinel regressions, using temporary databases and disabled pytest cache.

**Evidence boundary:** 0.03% is the sum of stored boundary-hour values, not an independent reconciliation to an exchange funding ledger. [Current data notes](../data/check_gaps.py#L53) describe the legacy hourly feed as CoinDesk-predicted. The eight-hour assumption matches the sampled recent BTC/ETH data; Binance documents 00/08/16 UTC as its default but allows interval changes. [Binance funding documentation](https://www.binance.com/en/support/faq/detail/360033525031).

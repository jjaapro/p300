# Test 0 — LSR stamp semantics (2026-09-01 07:52)

Binance 1d stamps sit at offset [0] s into the UTC day.

## Binance 1d vs its own 1h series

| day | v1d | a start | b last hour | c mean | d next open | best |
|---|---|---|---|---|---|---|
| 2026-08-12 | 1.8043 | 0.0 | 0.0415 | 0.06691 | 0.0488 | a_start |
| 2026-08-13 | 1.8531 | 0.0 | 0.0025 | 0.0488 | 0.0155 | a_start |
| 2026-08-14 | 1.8686 | 0.0 | 0.2409 | 0.15442 | 0.214 | a_start |
| 2026-08-15 | 2.0826 | 0.0 | 0.0569 | 0.01224 | 0.0375 | a_start |
| 2026-08-16 | 2.0451 | 0.0 | 0.1765 | 0.03654 | 0.1765 | a_start |
| 2026-08-17 | 2.2216 | 0.0 | 0.7033 | 0.33723 | 0.7371 | a_start |
| 2026-08-18 | 1.4845 | 0.0 | 0.0927 | 0.02496 | 0.1069 | a_start |
| 2026-08-19 | 1.3776 | 0.0 | 0.2785 | 0.01114 | 0.3003 | a_start |
| 2026-08-20 | 1.0773 | 0.0 | 0.1333 | 0.05925 | 0.1219 | a_start |
| 2026-08-21 | 0.9554 | 0.0 | 0.098 | 0.07363 | 0.0833 | a_start |
| 2026-08-22 | 1.0387 | 0.0 | 0.0331 | 0.02273 | 0.0315 | a_start |
| 2026-08-23 | 1.0072 | 0.0 | 0.0671 | 0.01973 | 0.0628 | a_start |
| 2026-08-24 | 1.0700 | 0.0 | 0.1283 | 0.06735 | 0.1252 | a_start |
| 2026-08-25 | 0.9448 | 0.0 | 0.064 | 0.02102 | 0.056 | a_start |
| 2026-08-26 | 1.0008 | 0.0 | 0.0709 | 0.03397 | 0.0851 | a_start |
| 2026-08-27 | 1.0859 | 0.0 | 0.1621 | 0.06447 | 0.1658 | a_start |
| 2026-08-28 | 0.9201 | 0.0 | 0.2619 | 0.09457 | 0.2676 | a_start |
| 2026-08-29 | 1.1877 | 0.0 | 0.0029 | 0.00444 | 0.0 | a_start |
| 2026-08-30 | 1.1877 | 0.0 | 0.1343 | 0.07658 | 0.1065 | a_start |
| 2026-08-31 | 1.0812 | 0.0 | 0.1018 | 0.01886 | 0.0864 | a_start |

**Verdict: PERIOD_START** (share a-start = 1.0, rule ≥ 0.8) → SHIFT_DAYS = 0

## Coinalyze daily vs Binance 1h (the backfilled history's convention)

verdict on the same rule: **PERIOD_START** over 20 days

## Offset fits (mean |diff| / share equal / corr by day offset k)

- stored_vs_binance_1d: `{"-1": {"n": 29, "mean_abs_diff": 0.15528, "share_equal": 0.034, "corr": 0.8373}, "0": {"n": 30, "mean_abs_diff": 0.0, "share_equal": 1.0, "corr": 1.0}, "1": {"n": 30, "mean_abs_diff": 0.15856, "share_equal": 0.033, "corr": 0.8512}}`
- coinalyze_vs_stored: `{"-1": {"n": 45, "mean_abs_diff": 0.17873, "share_equal": 0.022, "corr": 0.7796}, "0": {"n": 46, "mean_abs_diff": 0.0, "share_equal": 1.0, "corr": 1.0}, "1": {"n": 45, "mean_abs_diff": 0.17873, "share_equal": 0.022, "corr": 0.7796}}`
- coinalyze_vs_binance_1d: `{"-1": {"n": 29, "mean_abs_diff": 0.15528, "share_equal": 0.034, "corr": 0.8373}, "0": {"n": 30, "mean_abs_diff": 0.0, "share_equal": 1.0, "corr": 1.0}, "1": {"n": 30, "mean_abs_diff": 0.15856, "share_equal": 0.033, "corr": 0.8512}}`

## Forming-row check

`{"today_1d": 0.9948, "m5_at_00": 0.9948, "m5_latest": 1.0117, "m5_latest_ts": "2026-09-01 07:50", "stored_today": 0.9948, "closer_to": "00:00 value"}`

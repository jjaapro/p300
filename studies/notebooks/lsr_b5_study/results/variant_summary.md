# Test 1 — B5 variant ablation (backward-only Triple pool)

IS ≤ 2024-12-31 · OOS after · production tilt = BTC skip-after-loss, ETH half-after-loss · filters: OKX-aligned, shorts skipped when BTC 30d > +10 %

## tilt = prod

| asset | variant | n | L/S | mean R | total R | maxDD R | MAR | WR % | IS mean R (n) | OOS mean R (n) | short: OOS n / mean R / total R |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | V0_sym30 | 40 | 27/13 | +0.535 | +21.4 | -4.6 | +4.65 | 48 | +0.447 (27) | +0.717 (13) | 3 / +0.442 / +1.3 |
| BTC | V1_long30 | 62 | 26/36 | +0.421 | +26.1 | -4.4 | +5.87 | 48 | +0.436 (41) | +0.392 (21) | 11 / +0.019 / +0.2 |
| BTC | V2_noB5 | 89 | 55/34 | +0.235 | +20.9 | -6.7 | +3.13 | 47 | +0.248 (61) | +0.208 (28) | 11 / +0.019 / +0.2 |
| BTC | V3_sym90 | 38 | 29/9 | +0.474 | +18.0 | -4.5 | +4.02 | 50 | +0.466 (27) | +0.492 (11) | 3 / -0.521 / -1.6 |
| BTC | V4_sym365 | 38 | 30/8 | +0.708 | +26.9 | -3.0 | +9.10 | 58 | +0.611 (30) | +1.075 (8) | 2 / +0.306 / +0.6 |
| BTC | V5_long365 | 63 | 25/38 | +0.397 | +25.0 | -3.7 | +6.70 | 54 | +0.299 (48) | +0.713 (15) | 11 / +0.211 / +2.3 |
| ETH | V0_sym30 | 64 | 35/29 | +0.178 | +11.4 | -6.3 | +1.80 | 50 | +0.116 (35) | +0.252 (29) | 12 / +0.258 / +3.1 |
| ETH | V1_long30 | 124 | 35/89 | +0.225 | +27.9 | -6.4 | +4.35 | 49 | +0.095 (66) | +0.374 (58) | 41 / +0.481 / +19.7 |
| ETH | V2_noB5 | 174 | 85/89 | +0.266 | +46.2 | -6.2 | +7.50 | 48 | +0.232 (96) | +0.307 (78) | 41 / +0.547 / +22.4 |
| ETH | V3_sym90 | 62 | 34/28 | +0.616 | +38.2 | -3.5 | +10.85 | 55 | +0.832 (34) | +0.354 (28) | 10 / +0.352 / +3.5 |
| ETH | V4_sym365 | 64 | 38/26 | +0.519 | +33.2 | -3.6 | +9.30 | 53 | +0.644 (41) | +0.297 (23) | 6 / +0.271 / +1.6 |
| ETH | V5_long365 | 127 | 38/89 | +0.365 | +46.3 | -4.3 | +10.79 | 50 | +0.339 (69) | +0.395 (58) | 41 / +0.495 / +20.3 |

## tilt = none

| asset | variant | n | L/S | mean R | total R | maxDD R | MAR | WR % | IS mean R (n) | OOS mean R (n) | short: OOS n / mean R / total R |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC | V0_sym30 | 90 | 54/36 | +0.293 | +26.4 | -11.1 | +2.38 | 43 | +0.425 (58) | +0.054 (32) | 10 / +0.042 / +0.4 |
| BTC | V1_long30 | 133 | 54/79 | +0.283 | +37.6 | -10.1 | +3.73 | 46 | +0.441 (84) | +0.011 (49) | 27 / -0.029 / -0.8 |
| BTC | V2_noB5 | 199 | 120/79 | +0.116 | +23.0 | -12.0 | +1.91 | 44 | +0.188 (134) | -0.033 (65) | 27 / -0.029 / -0.8 |
| BTC | V3_sym90 | 82 | 54/28 | +0.335 | +27.5 | -8.4 | +3.29 | 45 | +0.490 (53) | +0.051 (29) | 11 / +0.057 / +0.6 |
| BTC | V4_sym365 | 69 | 46/23 | +0.475 | +32.8 | -5.1 | +6.41 | 54 | +0.509 (51) | +0.379 (18) | 7 / +0.317 / +2.2 |
| BTC | V5_long365 | 125 | 46/79 | +0.364 | +45.5 | -8.1 | +5.65 | 50 | +0.480 (87) | +0.100 (38) | 27 / -0.029 / -0.8 |
| ETH | V0_sym30 | 64 | 35/29 | +0.244 | +15.6 | -7.7 | +2.03 | 50 | +0.206 (35) | +0.289 (29) | 12 / +0.371 / +4.5 |
| ETH | V1_long30 | 124 | 35/89 | +0.286 | +35.5 | -7.5 | +4.75 | 49 | +0.120 (66) | +0.476 (58) | 41 / +0.577 / +23.7 |
| ETH | V2_noB5 | 174 | 85/89 | +0.296 | +51.5 | -8.7 | +5.90 | 48 | +0.248 (96) | +0.354 (78) | 41 / +0.577 / +23.7 |
| ETH | V3_sym90 | 62 | 34/28 | +0.646 | +40.0 | -4.5 | +8.94 | 55 | +0.903 (34) | +0.333 (28) | 10 / +0.519 / +5.2 |
| ETH | V4_sym365 | 64 | 38/26 | +0.584 | +37.4 | -6.1 | +6.14 | 53 | +0.799 (41) | +0.202 (23) | 6 / +0.349 / +2.1 |
| ETH | V5_long365 | 127 | 38/89 | +0.416 | +52.8 | -7.7 | +6.82 | 50 | +0.385 (69) | +0.452 (58) | 41 / +0.577 / +23.7 |

## Pre-registered adoption rule (production tilt, both assets)

OOS mean R ≥ V0 + 0.1R AND MAR ≥ 1.1×V0 AND OOS n ≥ 20 AND IS mean R ≥ V0 − 0.05R

- V1_long30: **KILL** — fails BTC:oos_mean_r
- V2_noB5: **KILL** — fails BTC:oos_mean_r,mar,is_mean_r; ETH:oos_mean_r
- V3_sym90: **KILL** — fails BTC:oos_mean_r,mar,oos_n
- V4_sym365: **KILL** — fails BTC:oos_n; ETH:oos_mean_r
- V5_long365: **KILL** — fails BTC:oos_mean_r,oos_n,is_mean_r

## Short-leg statement (V0, production tilt)

- BTC: IS n 10 total -0.0R · OOS n 3 mean +0.442R total +1.3R → **insufficient evidence (OOS n < 10) — no production change may be proposed from this study**
- ETH: IS n 17 total +4.3R · OOS n 12 mean +0.258R total +3.1R → **evaluable**

## Filter counts

```
{
 "BTC/V0_sym30": {
  "okx_dropped": 110,
  "up30_short_skipped": 11,
  "after_filters": 90
 },
 "BTC/V1_long30": {
  "okx_dropped": 250,
  "up30_short_skipped": 30,
  "after_filters": 133
 },
 "BTC/V2_noB5": {
  "okx_dropped": 391,
  "up30_short_skipped": 30,
  "after_filters": 199
 },
 "BTC/V3_sym90": {
  "okx_dropped": 88,
  "up30_short_skipped": 2,
  "after_filters": 82
 },
 "BTC/V4_sym365": {
  "okx_dropped": 69,
  "up30_short_skipped": 0,
  "after_filters": 69
 },
 "BTC/V5_long365": {
  "okx_dropped": 242,
  "up30_short_skipped": 30,
  "after_filters": 125
 },
 "ETH/V0_sym30": {
  "okx_dropped": 114,
  "up30_short_skipped": 13,
  "after_filters": 64
 },
 "ETH/V1_long30": {
  "okx_dropped": 203,
  "up30_short_skipped": 56,
  "after_filters": 124
 },
 "ETH/V2_noB5": {
  "okx_dropped": 263,
  "up30_short_skipped": 56,
  "after_filters": 174
 },
 "ETH/V3_sym90": {
  "okx_dropped": 101,
  "up30_short_skipped": 9,
  "after_filters": 62
 },
 "ETH/V4_sym365": {
  "okx_dropped": 83,
  "up30_short_skipped": 6,
  "after_filters": 64
 },
 "ETH/V5_long365": {
  "okx_dropped": 198,
  "up30_short_skipped": 56,
  "after_filters": 127
 }
}
```

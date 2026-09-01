# Parity gate — ETH discrepancy explained (2026-09-01)

`gen_variant_pools.py` reproduces `overlay_study/results_backonly/trades_BTC.csv`
exactly on `ts ≤ 2026-07-20` (204 / 204 trades, max |ΔR| 4.4e-16). For ETH every
one of the 182 reference trades is reproduced exactly (max |ΔR| 4.4e-16) but the
new V0 pool has 7 additional trades in the window:

```
2026-06-03T14:45|short  2026-06-15T17:00|long  2026-06-16T13:00|long  2026-06-16T19:45|long
2026-06-17T07:45|long   2026-07-15T05:00|long  2026-07-16T06:30|long
```

## Cause: ETH data backfilled after the reference pool was cut

Evidence (all read-only, `data/backups/prod-20260722.db` vs today's prod.db):

| table (2026-05-25 → 07-21 window) | rows in 07-22 snapshot | rows today | change |
|---|---|---|---|
| `cd_futures_eth_15m` | 97 (table ended **2026-05-26 00:00**) | 5,473 | +5,376 added, 0 values changed |
| `okx_perp_eth_1h` | 25 | 1,369 | +1,344 added, 0 changed |
| `ca_long_short_ratio` (ETH) | 58 | 58 | unchanged |
| `cd_futures_15m` (BTC, control) | 5,473 | 5,473 | unchanged |

The ETH perp feed had been dead since 2026-05-26 and was revived on 2026-08-23
(commit `5d9c4b3` "multi-asset chento Phase A: ETH feeds revived"); the reference
pool was written 2026-08-23 11:22 against the not-yet-backfilled table. Rerunning
the identical ETH V0 pipeline **against the 07-22 snapshot** yields 182 trades
whose keys equal the reference set exactly (only_snap = only_ref = ∅) and contain
none of the 7 extra trades. Every extra trade sits inside the backfilled
2026-05-26 → 07-21 span.

## Verdict

The pipeline is byte-equivalent to the reference on unchanged data; the extra
trades are new information (the restored ETH history), not a code divergence.
Per the pre-registration the parity gate is **passed with explanation** and the
study proceeds on today's tables. Note for the ETH leg: the reference
`results_backonly/trades_ETH.csv` (and any conclusion drawn from it) is missing
2026-05-26 → 08-23 of ETH bars.

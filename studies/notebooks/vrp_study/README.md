# VRP harvest (short ±10% strangle, 7-day delta hedge) — pre-registered design (2026-09-06, before running)

The trader repo's most economically validated unshipped strategy (its memory
`vrp/phase3_summary.md`): sell a BTC monthly strangle at strikes ≈ spot ±10% at T−21 days,
delta-hedge with the perp every 7 days, hold to expiry. On 66 expiries (2024-11 → 2026-04)
it showed mean +1.53% of spot per expiry, worst −7.08%, annualised Sharpe 1.88 (√12 × per-
expiry Sharpe), 82% win rate; passed five economic gates and two stress tests; failed only
the deflated Sharpe at its realistic 168-trial count (DSR 0.890, gap 0.035 in per-trade SR ≈
24 more expiries).

## Spec (the ONLY spec — nothing is tuned here)

| item | value |
|---|---|
| asset | BTC (ETH failed the same spec upstream; not tested) |
| structure | short call at K ≈ spot × 1.10 and short put at K ≈ spot × 0.90 (nearest strikes with entry-day and expiry-day marks), one per expiry that has ≥ 5 calls and ≥ 5 puts |
| entry | T−21 calendar days before expiry (spot from the nearest of ±2 days if missing) |
| hedge | Black-Scholes delta (per-leg IV from USD marks, r = 0), rebalanced every 7 days, closed at expiry |
| costs | 3% bid haircut on the premium, 0.03% × spot per leg option fee, 5 bp × \|Δhedge\| × spot per rebalance |
| P&L unit | % of spot at entry per expiry (one strangle on one BTC) |
| sizing (for any later sleeve) | ≤ 20% of NAV per strangle (worst case −10% ⇒ −2% NAV) |

Engine: `studies/lib/options/{bs,chain,pnl_engine}.py`, a verbatim port of the trader's
`probe_vrp_straddle{,_v2}.py`. Data: `deribit_options_daily` (USD marks; seeded from the
trader CoinDesk snapshot 2023-12 → 2026-04-24, live Deribit snapshots from 2026-09-06),
`deribit_options_instruments`, spot from `cd_futures_ohlcv`.

## Step 1 — parity gate (mandatory before any new number)

Re-run the trader's Phase-3 canonical cell on the seeded data with its train/test split
(expiries 2024-11-01 → 2025-09-30 / 2025-10-01 → 2026-04-30). PASS if n = 66 ± 3, combined
mean within ±0.20 pp of +1.53%, worst within ±0.50 pp of −7.08%, combined annualised Sharpe
within ±0.20 of 1.88. FAIL ⇒ stop and debug the port; do not proceed to Step 2.

## Step 2 — deflated Sharpe and OOS

- Report DSR at N_TRIALS ∈ {1, 42, 168, 672} on the combined seeded sample (trader's own
  search-space audit: 6 strikes × 7 hedge cadences × 4 entry timings = 168 realistic).
- OOS = expiries after 2026-04-30 with live marks. **Known gap:** Deribit's public API serves
  no history for expired instruments, so no option marks exist between 2026-04-24 and
  2026-09-06; OOS expiries accrue only from the live snapshots (first usable entry
  2026-09-06 ⇒ first full monthly expiry 2026-10-30). Report OOS n and results whenever
  ≥ 1 expiry exists; the OOS clause below cannot be evaluated before ~2027-03.
- Report the pre-ETF / post-ETF split (all seeded expiries are post-ETF: note it), per-year
  table, and the descriptive DVOL − realised-vol premium on `deribit_dvol_daily` since
  2022-09 as the "is the premium still there" monitor (2026 reversal check).

## Decision rule (fixed)

KILL if any of: OOS mean per expiry ≤ 0 with n ≥ 6; any expiry < −10%; full-sample
DSR at 168 trials < 0.90 (it was 0.890 upstream — the port must not lose ground).
Otherwise the item advances to a paper options-sleeve design document (go-ahead required;
the bot has no options venue adapter, so this remains paper-only research).

Priors: parity should reproduce; DSR@168 stays ≈ 0.89 (no new data yet); the 2026 DVOL−RV
premium is expected negative (the trader recorded −14.7% YTD in April 2026).

Tag: AUDIT/re-validation now (N_TRIALS = 1 on new data; the 168-trial correction is applied
to the seeded sample as upstream did).

# LSR B5 study — findings (2026-09-01)

**Status: CONCLUDED — no production change. All five pre-registered variants
KILL under the adoption rule; the current B5 (symmetric, 30 daily rows) stays.
Test 0 cleared every LSR backtest of the suspected 24h peek. The only
production deliverable was documentation (commit `c582e33`, before the study).**

Pre-registration: [README.md](README.md) (committed `aa9bd87` before any script ran).

## 1 What was asked

chento_triple_v3's B5 gate marks a LONG window when `long_pct` (Binance global
long/short *account* ratio, daily) is below its rolling 30-row p10 and a SHORT
window above p90. A 2026-09-01 quick check found the ratio's 1-year percentile
carries a contrarian tilt on 20-day returns whose crowd-LONG side ("fade
euphoria") is unreliable after 2024, and live had never fired the short
branch. Three questions: (0) are the daily LSR stamps causal, (1) does the
short leg / the window earn its place at chento's 72h horizon, (2) if the short
leg matters, is it timing or regime.

## 2 Data

prod.db read-only: `cd_futures_15m` / `cd_futures_eth_15m` (taker splits),
`ca_long_short_ratio` (BTC + ETH, daily), `okx_perp_1h` / `okx_perp_eth_1h`.
Pools from the research pipeline of `overlay_study/gen_trades.py` with
`intersect_backward` ([−24h, 0]), 5×ATR / 6R / 72h replay, no_resist_OB;
scoring applies the production stack (OKX alignment, shorts skipped when BTC
30d > +10 %, BTC skip-after-loss / ETH half-after-loss). IS ≤ 2024-12-31.

## 3 Test 0 — stamp semantics: PERIOD_START, no peek

Binance `period=1d` stamped D equals the 1h/5m value at **D 00:00** on every
day with full coverage (20/20; today's stored 0.9948 = the 00:00 snapshot while
the live 5m value was 1.0117). Coinalyze's daily history equals Binance 1d
exactly at offset 0 (share equal 1.0, n=30) and passes the same rule. The
stamp-D value is known at D 00:00, so the sleeve's forward-fill from the stamp
is causal; **SHIFT_DAYS = 0** for the study and no correction is owed by CPR or
the regime circuit breaker. Figure: `results/fig_test0_stamp.png`; detail
`results/test0.md`.

## 4 Parity gate

BTC: 204 / 204 reference trades reproduced, max |ΔR| 4.4e-16. ETH: all 182
reference trades reproduced exactly plus 7 new trades in June–July 2026 —
explained and accepted: the ETH perp table ended 2026-05-26 when the reference
was cut (feed dead until revived 2026-08-23, `5d9c4b3`) and 5,376 15m + 1,344
OKX rows were backfilled since; rerunning against the 07-22 snapshot
reproduces the reference set exactly and none of the 7. `results/parity_explained.md`.

## 5 Test 1 — six-variant ablation (production tilt; full tables in `results/variant_summary.md`)

| asset | variant | n | L/S | mean R | total R | maxDD | MAR | IS mean R (n) | OOS mean R (n) | shorts OOS n / mean / total |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC | **V0 sym30 (current)** | 40 | 27/13 | +0.535 | +21.4 | −4.6 | 4.65 | +0.447 (27) | +0.717 (13) | 3 / +0.44 / +1.3 |
| BTC | V1 long30 | 62 | 26/36 | +0.421 | +26.1 | −4.4 | 5.87 | +0.436 (41) | +0.392 (21) | 11 / +0.02 / +0.2 |
| BTC | V2 noB5 | 89 | 55/34 | +0.235 | +20.9 | −6.7 | 3.13 | +0.248 (61) | +0.208 (28) | 11 / +0.02 / +0.2 |
| BTC | V3 sym90 | 38 | 29/9 | +0.474 | +18.0 | −4.5 | 4.02 | +0.466 (27) | +0.492 (11) | 3 / −0.52 / −1.6 |
| BTC | V4 sym365 | 38 | 30/8 | +0.708 | +26.9 | −3.0 | 9.10 | +0.611 (30) | +1.075 (8) | 2 / +0.31 / +0.6 |
| BTC | V5 long365 | 63 | 25/38 | +0.397 | +25.0 | −3.7 | 6.70 | +0.299 (48) | +0.713 (15) | 11 / +0.21 / +2.3 |
| ETH | **V0 sym30 (current)** | 64 | 35/29 | +0.178 | +11.4 | −6.3 | 1.80 | +0.116 (35) | +0.252 (29) | 12 / +0.26 / +3.1 |
| ETH | V1 long30 | 124 | 35/89 | +0.225 | +27.9 | −6.4 | 4.35 | +0.095 (66) | +0.374 (58) | 41 / +0.48 / +19.7 |
| ETH | V2 noB5 | 174 | 85/89 | +0.266 | +46.2 | −6.2 | 7.50 | +0.232 (96) | +0.307 (78) | 41 / +0.55 / +22.4 |
| ETH | V3 sym90 | 62 | 34/28 | +0.616 | +38.2 | −3.5 | 10.85 | +0.832 (34) | +0.354 (28) | 10 / +0.35 / +3.5 |
| ETH | V4 sym365 | 64 | 38/26 | +0.519 | +33.2 | −3.6 | 9.30 | +0.644 (41) | +0.297 (23) | 6 / +0.27 / +1.6 |
| ETH | V5 long365 | 127 | 38/89 | +0.365 | +46.3 | −4.3 | 10.79 | +0.339 (69) | +0.395 (58) | 41 / +0.50 / +20.3 |

**Adoption rule** (both assets: OOS mean R ≥ V0 + 0.10R, MAR ≥ 1.1×V0, OOS n ≥ 20,
IS mean R ≥ V0 − 0.05R):

- V1 long30 — KILL (BTC OOS mean R)
- V2 noB5 — KILL (BTC OOS mean R, MAR, IS mean R; ETH OOS mean R)
- V3 sym90 — KILL (BTC OOS mean R, MAR, OOS n = 11)
- V4 sym365 — KILL (BTC OOS n = 8; ETH OOS lift +0.045 < +0.10)
- V5 long365 — KILL (BTC OOS mean R, OOS n, IS mean R)

**Short-leg statement (V0):** BTC OOS n = 3 → *insufficient evidence*; no
production change may be proposed. ETH: IS 17 shorts +4.3R, OOS 12 shorts
mean +0.258R / total +3.1R → evaluable and **positive**.

Figures: `results/fig_oos_mean_r.png`, `results/fig_cum_r.png`, `results/fig_short_leg.png`.

## 6 Test 2 — attribution of the short leg (gated in: |Δ total R| BTC 6.4R, ETH 17.8R)

Gross, 72h stop-first engine, production-tilt pools, IS+OOS (`attribution.attribute`):

| pool | n | actual R | = regime | + timing | + exit |
|---|---|---|---|---|---|
| BTC V0 shorts (B5-gated) | 13 | +1.01 | −0.42 | +0.47 | +0.97 |
| BTC V1 shorts (B1∩B7 only) | 36 | +1.06 | −0.23 | +1.26 | +0.03 |
| BTC V0 longs | 27 | +0.94 | +0.19 | +1.16 | −0.41 |
| ETH V0 shorts (B5-gated) | 29 | +1.30 | −0.13 | **+1.55** | −0.12 |
| ETH V1 shorts (B1∩B7 only) | 89 | +0.84 | −0.27 | +0.65 | +0.46 |
| ETH V0 longs | 35 | +0.48 | +0.15 | +0.14 | +0.18 |

The short leg is not a loser: gated shorts are positive on both assets and on
ETH the B5 gate selects shorts with 2.4× the timing alpha per trade of the
ungated set (which wins on *total* R only by volume). On BTC the gate does not
improve timing (n = 13, the smallest pool here). Shorts earn their R through
timing/exit while fighting the regime term, as expected for a fade.

## 7 Priors scorecard

| prior (README) | outcome |
|---|---|
| removing B5 from shorts adds lower-quality shorts | BTC yes (OOS mean +0.02, n 11); ETH **no** (OOS mean +0.48, n 41) |
| longer windows do not help at 72h | contradicted in-sample on both assets (V4: MAR 9.1 / 9.3 vs 4.7 / 1.8) — but OOS fails the sample floor (BTC n 8) and the lift (ETH +0.045) |
| short leg ≈ 0R, small n | small n confirmed; ≈ 0R **not** — positive on both assets |
| live evidence too thin | confirmed (3 fires, 0 shorts) |

The 20-day "fade euphoria is unreliable" finding does not transfer to
chento's 72h timing trades — the horizon-mismatch prior held, in the direction
of *not* removing the short leg.

## 8 Conclusions

1. No production change. B5 stays symmetric on 30 rows; comments/docs were the
   only fix. Nothing in `strategies/` or `bots/` was touched by this study.
2. LSR stamps are causal (PERIOD_START); the lookahead audit's "B5 robust"
   entry stands, now with a backward-only re-score behind it.
3. Two signals worth a **fresh pre-registration when OOS data allows — not a
   parameter change now**: (a) the 365-row window (V4) — higher mean R and MAR
   on both assets in-sample, OOS n 8 / 23; needs BTC OOS n ≥ 20 (≈ a year at
   the current fire rate); (b) ETH B1∩B7 shorts that B5 currently blocks —
   +19.7R over 41 OOS trades — but BTC does not confirm and the rule requires
   both assets.

## 9 Caveats

- Relative comparisons are the deliverable; absolute R inherits the research
  engine (replay_one) and the gross attribution walk — see the overlay study's
  caveat. Costs and slippage are not in the attribution numbers.
- Small pools: BTC V0 has 13 shorts all-time under the production tilt. The
  short-leg verdict on BTC is "insufficient evidence", and V4's OOS numbers are
  8 and 23 trades.
- The ETH reference pool used by the overlay study (`results_backonly/trades_ETH.csv`)
  is missing 2026-05-26 → 08-23 of ETH bars; any conclusion drawn from it should
  be re-checked on today's tables (this study's ETH pools are complete).
- The regime skip uses BTC's 30d return for both assets (the overlay study's
  definition); the ETH bot's own implementation may differ.

## 10 File index / re-run

`test0_stamp_semantics.py` → `results/test0_stamp_semantics.json`, `test0.md` ·
`gen_variant_pools.py` → `results/pools/trades_{BTC,ETH}_{V0..V5}.csv`,
`results/parity_{asset}.json` (+ `parity_explained.md`) · `score_variants.py` →
`results/variant_summary.{csv,md}`, `results/scored/*.csv` · `attribution_shorts.py`
→ `results/attribution.csv` · `figures.py` → `results/fig_*.png`.
Run in that order; ~10 min total, read-only against prod.db.

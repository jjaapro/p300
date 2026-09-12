# ADX (S-003) — calibration log

Single source of truth for "what is calibrated right now" + provenance.
Update in the same commit as any config/sizing change.

## Current state (2026-07-22 — Tier-2 + veto, standalone bot)

**Strategy** (`strategies/sleeves/adx/config.py`): ADX(14) cross <20→≥25
(was_low machine), direction close-vs-EMA(50), **symmetric** EMA(150) trend
filter (T2a — SHORTs now require close < EMA150; asymmetric design retired),
ADX<20 exit **or ATR×4 trailing exit** (T2b, Wilder ATR(14) daily, ratchets
on closes), 10% fixed SL, **funding-crowding LONG veto** (skip LONG when 30d
funding z > 1.5; z = (today − mean)/pstdev over daily-mean fr_close, current
day included, ≥10 samples, fail-open). Study numbers (2018 → 2026-06-25):
n=27, WR 56%, PF 6.89, ret +2483%, maxDD −15.1%, MAR 3.09 (OOS MAR 3.16)
vs baseline −27.3% / MAR 1.78.

**Bot** (`bots/adx/config.py`): variant `bot_adx_v1`, $10,000 paper,
fixed-R 2% over the effective initial stop (min of 10% SL and 4×ATR seed —
typically 4-10% → notional ~0.2-0.5× capital), 3× cap, 60s ticks (daily
entry decision + continuous SL/trail sweep vs live btc_1m). Stale policy:
mgmt = btc_1m + cd_spot_binance; entry = cd_funding_rate. No per-day diag
JSONL (deviation from the plan): at one decision/day with ~6 statuses, INFO
logs + the monitor's 26h eval limit cover visibility; add counters if a
silent failure mode ever shows up here.

**Port semantics note**: research's trail updates with the same bar's close
before checking that bar's low; live equivalently includes all CLOSED daily
bars in the ratchet and monitors breaches continuously on minute prices
(today's bar joins the ratchet once closed). Trail breaches book at current
price (conservative vs research's at-trail fill assumption).

**Parity** (tests/test_adx_parity.py, 5 tests): indicators.atr byte-equal to
harness.atr_series; harness T2 reproduces the findings table on the study
window; sleeve state machine fires the identical 27 entries and blocks the
forfeited counter-trend shorts; veto z matches the documented 2025-10-05
(1.53, vetoed) and 2024-11-09 (0.41, kept).

## 2026-09-12 — measured cost, funding is the live-vs-research gap

- **Cost**: the close now books 10 + 1 bp (`SLIPPAGE_BP_RT = 1.0`; it
  booked the 5 bp default, 15 bp total, until 2026-09-12).
  `studies/notebooks/execution_2026_09/` E6 measured 10.5 bp all-in on the
  sleeve's own entries and stops over 2020–2026 1 m bars — 0.7 % of the
  mean trade, so this changes nothing about the sleeve's economics.
- **Funding**: `studies/notebooks/adx_robustness_2026_09/` reproduced the
  harness under live fill semantics (44.2 vs 43.0 % CAGR, −38.4 vs −38.0 %
  MTM drawdown, 2020 →) and found that **funding on the longs is the whole
  remaining gap**: 36.0 % CAGR, −48.1 % MTM drawdown, MAR 0.75 once
  charged; 14 longs paid 37.4 pp, 24.7 %/yr of long exposure. CARRY's perp
  short was on during 96.6 % of those long-days, so in one cross-margin
  account the perp legs net and the pair is "ADX long on spot" → pool plan
  decision D8 (same account, or ADX longs on spot). The study numbers in
  "Current state" above are the harness's no-funding, fill-model figures;
  the harness now takes `with_funding=True` and any future comparison with
  the live sleeve must use it.
- **No other change**, each by its pre-registered rule: phase ensembles
  NO CHANGE (k = 2/3/24 daily boundaries), no late-entry grace window
  (≤ 0.5 pp per trade at 6 h), stops and trail kept (the no-stop policy
  loses for this machine), fixed-R sizing kept. At the shipped ≈ 0.2×
  notional the account MTM drawdown is ≈ −10 % in the worst of the 24 day
  boundaries.

## Change history

| Date | Change | Why / provenance |
|---|---|---|
| 2026-09-12 | Close books `SLIPPAGE_BP_RT = 1.0` explicitly (15 → 11 bp round trip) | `execution_2026_09` E6 measured 10.5 bp all-in; user go-ahead 2026-09-12. Account design note above (D8). |
| 2026-07-22 | Tier-2 + veto shipped (symmetric filter, ATR×4 trail, funding veto — all config-flagged, ON); extracted to standalone bot `bots/adx/` | User decision 2026-07-22 (T2+veto option); studies/notebooks/adx_study/findings.md (2026-06-26). Carry cost: forfeits counter-trend-short funding harvest — S-078 CARRY owns that stream delta-neutrally. |
| 2026-05-04 | Asymmetric trend filter (LONG-only EMA150) | funding-aware replay 2023-09→2026-05 (superseded by T2a) |
| 2026-05-01 | Signal source cd_futures_ohlcv → cd_spot_binance | TV parity (spot vs perp ADX delta flipped entries) |

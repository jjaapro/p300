# SHORT_SQUEEZE (S-105) — calibration log

Single source of truth for "what is calibrated right now" + provenance.
Update in the same commit as any config/sizing change.

## Current state (2026-07-21 — first-ever paper deployment)

**Strategy params** (`strategies/sleeves/short_squeeze/config.py`) — frozen
from the 2026-05-18 notebook sweep, unchanged: perp_cvd pct < 0.15,
divergence pct > 0.70 (90d session-filtered distributions), close-in-range
≥ 0.10, sweep of prior 24-bar (6h) low, London/NY sessions only, Asia
short-macro gate (close<open + OI ≥ +0.5% + funding < 0), 4h cooldown.
Exits: stop 10bp below swept low, target 3R, **6h time-stop** (code truth;
PORTFOLIO.md's "session-end" wording is stale). Costs 10bp + 15bp slippage,
funding applied.

**Bot-level** (`bots/short_squeeze/config.py`, standalone bot):
- Variant `bot_short_squeeze_v1`, capital $10,000 paper.
- Sizing: fixed-R **1%**/trade over the swept-low stop, notional hard-capped
  at 3× capital — **the cap binds often by design** (stop is frequently
  only bp from entry; this replaces the README's "leverage 20-100×"
  suggestion). `sized_at_cap` visible in runner logs.
- Diagnostics permanently ON (`SSQ_DIAG=1`,
  `bots/short_squeeze/logs/diag.jsonl` — per-day gate-kill counters).
- Stale-input policy: btc_1m stale → skip tick; signal tables
  (cd_futures_15m/cd_spot_15m/cd_futures_ohlcv/cd_open_interest/
  cd_funding_rate) stale → sweep runs, entries refused loudly.

**Expected cadence** (notebook results table, 2022-01→2026-05): n=70
triggers ≈ 16/yr ≈ one per ~3 weeks; WR 44.3%, avg +0.40R. Operator rule of
thumb: investigate if no *trigger* for >12 weeks with green diag counters;
monitor.py already alerts if no *evaluation* for >14h.

**Parity status** (tests/test_short_squeeze_parity.py, 2026-07-21):
- Raw features (perp_cvd, divergence) — sleeve loader EXACTLY matches the
  notebook formulas per timestamp.
- rolling_percentile / percentile_rank — byte-identical to the notebook
  definitions.
- Live path's daily-frozen 90d snapshot vs research per-bar trailing window
  (a deliberate structural approximation): measured p99 |Δpct| = 0.0026,
  max 0.0030, **gate flip rate 0.000%** over 1,069 session bars — the
  approximation is empirically negligible.

## Macro-gate base rates (computed 2026-08-15 via the sleeve's own functions)

Short-macro days (all three Asia conditions aligned) are **3.7% of all days**
(62 of 1,657 since 2022-02) and heavily clustered in risk-off periods: the
longest historical drought is **186 days** (ending 2024-04-22, post-ETF
bull). The 2026 drought reached 89 days by Aug 15 with funding the binding
constraint (Asia funding positive every day since May). Historically the 62
macro days produced ~70 triggers (~1.1/day) — when the regime flips, action
follows quickly. Long silences in positive-funding regimes are the designed
behavior (SS is the regime-complement to CARRY, which harvests exactly then).

**Funding-cadence fidelity note**: since the 2026-04-13 cadence cutover
(1h predicted → 8h settlement), the Asia funding join yields **1 row per
session (the 00:00 UTC settlement) vs 7 hourly rows before** — `fund_mean`
is now the sign of a single settlement, not a 7-hour mean. Same for any
post-April backtest day, so live matches the backtest's post-cutover
behavior; but the validated trigger history (2022→2026-04) used the 7-row
mean. Slightly noisier gate timing; NOT recalibrated ad hoc — any change
(e.g. averaging the prior 24h's three settlements) needs notebook
validation first per the research-workflow rule.

## Change history

| Date | Change | Why / provenance |
|---|---|---|
| 2026-07-21 | Deployed as standalone bot (first paper deployment ever); fixed-R 1% + 3× cap; SSQ_DIAG counters added to sleeve (env-gated, additive); README CVD-availability claim corrected | Bot-extraction plan M2. The sleeve was dispatch-registered 2026-05-18 but never composed — two months validated-but-silent (fact-sheet finding #11). |
| 2026-05-18 | Signal thresholds frozen from percentile sweep + walk-forward | strategy_backtest.ipynb |

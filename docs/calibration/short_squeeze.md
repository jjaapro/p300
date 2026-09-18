# SHORT_SQUEEZE (S-105) — calibration log

Single source of truth for "what is calibrated right now" + provenance.
Update in the same commit as any config/sizing change.

## Shipped configuration (2026-07-21 — first paper deployment; the file of record is `bots/short_squeeze/strategy/config.py`)

**Strategy params** (`bots/short_squeeze/strategy/config.py`) — frozen
from the 2026-05-18 notebook sweep, unchanged: perp_cvd pct < 0.15,
divergence pct > 0.70 (90d session-filtered distributions), close-in-range
≥ 0.10, sweep of prior 24-bar (6h) low, London/NY sessions only, Asia
short-macro gate (close<open + OI ≥ +0.5% + funding < 0), 4h cooldown.
Exits: stop 10bp below swept low, target 3R, **6h time-stop** (code truth;
PORTFOLIO.md's "session-end" wording is stale). Costs **10 bp, measured**
(was 10 bp + 15 bp slippage until 2026-09-12 — see the 2026-09-12 section),
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

## 2026-09-12 — measured cost (25 → 10 bp) and a no-stop, 1× paper variant

**Cost.** `SLIPPAGE_BP_RT` 15 → 0; the booked round trip is the 10 bp fee
line. Execution study `studies/notebooks/execution_2026_09/` (E1/E4/E6, the
sleeve's 71 historical fires 2022-01 → 2026-05 on 1 m bars): half-spread
< 1 bp per leg, decision-to-fill drift −0.3 bp, zero gap-throughs in 39
stops, all-in taker round trip 9.3 bp, CI90 [7.0, 11.6]. Under its own
25 bp the sleeve's net expectancy was **−0.316 R per trade on a +0.481 R
gross edge** (25 bp is 0.80 R on a bp-wide stop), so the paper record was
negative by construction; under the measured cost it is +0.192 R with a
60 % cost share, which the study's pre-registered viability rule labels
"retire pending user decision". The user's 2026-09-12 decision: keep the
stop variant running at the corrected cost and add the no-stop variant
below; the n = 30 rule decides. Trades closed before 2026-09-12 carry
25 bp — a methodology change, not a regime change.

**Second variant `bot_short_squeeze_nostop_v1`.** Same process, same
signals; **no stop, no target, 6 h time stop only; fixed 1× notional**
(`NOSTOP_NOTIONAL_X = 1.0`). The 3× cap was only ever justified by a
bp-wide stop this variant does not have, and at 1× the tail that moves from
the stop to the account is ~1/2.5 of what the stop variant's average
notional would carry. Sizing study `studies/notebooks/sizing_style_2026_09/`
(S1 policy P1, measured cost, shipped 3×-capped sizing; S3b = the bot's
single-open semantics, post-hoc):

| policy | n | mean R | win | worst trade | MTM maxDD | MAR | halves |
|---|---|---|---|---|---|---|---|
| stop + 3R target + 6 h (shipped) | 71 | +0.481 | 45 % | −1.0 R | −7.9 % | 0.19 | +0.388 / +0.576 |
| **no stop, 6 h only (new variant)** | 71 | **+1.164** | 63 % | −7.8 R | −12.6 % | 0.69 | +0.504 / +1.843 |
| no stop, 6 h only, single-open (S3b) | 59 | +1.131 | — | — | −10.8 % | 0.63 | +0.531 / +1.751 |
| no stop, 3R target, 6 h | 71 | +0.665 | 66 % | −7.8 R | −12.4 % | 0.33 | +0.372 / +0.967 |

Under the research pool's overlapping fires at 3× the no-stop policy failed
the pre-registered safety clause by 0.007 (liquidation distance 0.160 vs
0.167 required at 6.06× peak gross); under the bot's single-open guard it
passes (S3b), and at 1× notional gross exposure cannot exceed 1×. Both
halves are better on every other clause. The worst-trade tail is −7.8 R
against the stop variant's −1.0 R by construction; at the historical stop
widths (0.1–0.7 % of entry) and 1× notional that is roughly −1 to −5 % of
capital on one trade. R for both variants is measured against the swept-low
reference distance (`_reference_stop_price` in the trade notes).

**Pre-registered re-cut for the pair (written 2026-09-12, before any fire):**

- At **n = 20 fires taken by both variants on the same bar**: replay both
  policies over the union of live fires with the sleeve's own walk. DISABLE
  the no-stop variant if its live mean R ≤ 0, or its paired mean R is more
  than 0.10 R below the stop variant's, or any single live trade prints
  below −10 R (the replay's worst is −7.8 R). Otherwise CONTINUE.
- At **n = 30**: the same, plus a deflated Sharpe ≥ 0.5 at a trial count of
  at least 30. If BOTH variants have mean R ≤ 0 at n = 30, retire the
  sleeve — the execution study's verdict stands. Make the no-stop variant
  the fleet default only if its paired mean R is ≥ the stop variant's
  + 0.10 R AND its MTM drawdown is not worse by > 5 pp.
- Any time: DISABLE a variant whose live record diverges from the sleeve's
  own replay of the same fires by > 0.05 R on any trade. Disable via
  `enabled = 0`; do not edit thresholds.

**The re-cut is executable**, written 2026-09-12 while `n_paired = 0` so the
code predates the data it judges: `studies/notebooks/squeeze_recut/`
(`python studies/notebooks/squeeze_recut/run_recut.py --sleeve short_squeeze`).
Its README quotes the block above verbatim; `tests/test_squeeze_recut.py` pins
each clause, this sleeve's −10 R floor and its D7 retire-the-sleeve rule, which
SQUEEZE_BULL does not have. Sleeve-specific caveats recorded there: the
reconstructed fires are labelled approximate because the live gate reads the
newest funding settlement where the validated history used a 7-row mean (see
above), which affects only union bars neither variant took — never the paired
set, which comes from the live ledger. `enabled = 0` alone does not stop a
runner; the variant must also leave `bots/short_squeeze/config.py: VARIANTS`.

## Change history

| Date | Change | Why / provenance |
|---|---|---|
| 2026-09-14 | **The scheduled-exit backstop books this sleeve's own cost; the re-cut nets booked cost on every closed trade.** No parameter changed. `botlib.close_due_trades` now closes through `signal._close_paper` (`COST_BP_RT` 10 bp, `SLIPPAGE_BP_RT` 0, funding) instead of the `trades.py` defaults (10 bp fee + 5 bp slippage + funding): 5 bp less, on both variants. On this sleeve's swept-low stops 5 bp is 0.03–0.33 R per trade (median 0.15 R; E6 per-trade `bp_to_r`). BACKLOG 4.4 applied SQUEEZE_BULL's 8 bp gap to this sleeve, hence its 0.07–0.5 R; the real gap here is 5 bp. **No backstop close has ever happened on this sleeve**, so no ledger row changes. The backstop can only beat the sweep's time stop inside one tick; when it does, the label is still `scheduled_exit`. Also: a tick whose `decide()` raises now still runs the backstop and reports heartbeat `error`, and a due trade whose strategy has no closer is left open and named in the heartbeat without stopping the twin's tick. Any other error in one variant's tick (for example `execute()` refused while the process stands down as a duplicate instance) no longer skips the twin's tick either, though it still skips that variant's own backstop for the tick. **Re-cut (D4):** live books 10 bp and the replay nets the E6 measured 9.32 bp. That 0.68 bp gap is 0.004–0.045 R per trade (median 0.020 R), up to 90 % of D4's 0.05 R. It is now netted on every closed trade as `booked_cost_R`, with booked bp taken from the CLOSE adjustment's `fee_usdt`. Before, only `scheduled_exit` closes got a term, at a hard-coded 15 bp. The same change fixes `funding_R`, which came out NaN on every hold that crossed a funding settlement (the lookup passed `+1` for the direction), leaving that funding in the residual. Every recorded re-cut run had D4 = n/a. Takes effect when short_squeeze restarts. Restarted 2026-09-14 19:06:27Z stop / 19:06:59Z start (commits 1c201c5, 5485899, 5ce3e7e); backstop closes before that booked the old defaults. | BACKLOG 4.4 and 18; operator go-ahead 2026-09-14. Tests: `test_short_squeeze_bot.py` backstop cost per variant (80.00 on +$100 of price P&L; the old backstop booked 75.00), raising decide, twin still swept after a refusal and after an entry error; `test_squeeze_recut.py` booked-cost tests. |
| 2026-09-12 | `SLIPPAGE_BP_RT` 15 → 0 (booked round trip 25 → 10 bp); second paper variant `bot_short_squeeze_nostop_v1` (no stop, no target, 6 h, fixed 1× notional) in the same process; diag counters counted once per tick; re-cut rule for the pair fixed above | `execution_2026_09` E6 (measured 9.3 bp [7.0, 11.6]; sleeve −0.316 R under 25 bp); `sizing_style_2026_09` S1/S3b. User go-ahead 2026-09-12. |
| 2026-07-21 | Deployed as standalone bot (first paper deployment ever); fixed-R 1% + 3× cap; SSQ_DIAG counters added to sleeve (env-gated, additive); README CVD-availability claim corrected | Bot-extraction plan M2. The sleeve was dispatch-registered 2026-05-18 but never composed — two months validated-but-silent (fact-sheet finding #11). |
| 2026-05-18 | Signal thresholds frozen from percentile sweep + walk-forward | strategy_backtest.ipynb |

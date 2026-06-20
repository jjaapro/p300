# Trade audit — behavior compliance + paper vs sim (2026-06-07)

Scripts: `explore*.py`, `audit*.py` (read-only against `data/databases/prod.db`).

## Data model
- **One `trades` table, all `execution_mode='paper'`** (4094 rows). Live vs sim is
  separated by `strategy_variant` → resolved via `variants.enabled`:
  - **LIVE = `p300_aggressive_v2_v1_0`** (the only `enabled=1` variant). ~25 trades,
    entries 2026-04-06 → 2026-06-01.
  - **SIM = `__replay_full_v9_consume_open`** (canonical full replay, 203 closed
    trades, 2023-09 → 2026-04). 40+ other `__replay*`/`__core*`/smoke variants are
    `enabled=0` research runs.
- **PnL fields differ:** live trades have `realized_pnl_usdt`; replay trades have
  `pnl_pct` only (USD = 0). Score sim by `pnl_pct`. `pnl_pct` base isn't normalized
  across sleeves → use WR + sign for cross-sleeve comparison, not magnitude.

## Behavior compliance (asset / direction / timing-window / hold)
- **SIM v9: 203/203 clean.** ADX, CARRY, CPR, FOMC, PDO, THU_BEAR all obey
  documented asset/direction/timing. The strategy *logic* behaves to spec in sim.
- **LIVE: clean except two explained cases:**
  - R4_BTC 2 "non-Monday" entries (2026-04-08, 2026-05-06 = Wednesdays) — **not a bug**:
    R4_BTC ran Mon+Wed before the 2026-05-08 Mon-only change; both Wed trades pre-date
    the cutover; all post-cutover entries are Monday. Audit correctly reflects the config history.
  - **⚠️ Real anomaly:** on **2026-05-13**, R4_BTC_V2 and R4_ETH_V2 both opened at
    **06:07 UTC** (window opens 04:00) and held 7.9h. Off-schedule cold-fill ~2h late
    (a deploy/restart artifact that day). Both off-schedule trades **lost** (−1.87% / −1.84%)
    and were the only V2 losers. Contradicts the "missed entry is missed entry" no-cold-fill rule.

## Coverage gaps (the biggest finding)
| Sleeve | live | sim_v9 | note |
|---|---|---|---|
| JPLUS_EMA_BTC | 0 | 0 | **emits ZERO live trades — by design, not a bug.** Last weekly cross 2025-11-09→SHORT predates live paper (2026-04); cold-start guard correctly refuses to cold-fill the offside short. Enters only on the next fresh cross (→LONG). |
| SHORT_SQUEEZE | 0 | 0 | never fired (15m sleeve; not in 1h replay; no live fills). Unvalidated. |
| CHENTO_TRIPLE_V3 | 0 | 0 | only in its own `__replay_chento_v3*` runs, not the full replay or live. |
| ADX / CARRY / FOMC / THU_BEAR | 0 | 12/11/11/68 | sim-validated, **never fired live yet** (ADX+CARRY have 1 open each). |
| JPLUS_ETH_DAILY | 0 | 0 | correct — live period is non-bull (bear today), sleeve stays flat. |
| JPLUS_R4_* | 6/2/3/2 | 0 | R4 is daily-return-modeled in v9, discrete-trade in live → not directly comparable. |

## Results
**SIM v9 (mean pnl% / WR):** all positive — FOMC +1.22%/100%, THU_BEAR +1.56%/69%,
ADX +3.62%/50%, CARRY +14.9%/55% (long-hold basis), CPR +0.39%/78%, PDO +0.30%/55%.

**LIVE (mean pnl% / WR, tiny n):**
- R4_BTC +0.65% / **100% (n=6)** ✅, R4_ETH +0.68% / 33% (n=3, one +4.5% winner)
- PDO +0.09% / 50% (n=2), AI_QUANT −0.09% / 50% (n=4)
- CPR −0.47% / 50% (n=4), R4_BTC_V2 −0.94% / 0% (n=2), R4_ETH_V2 −1.02% / 0% (n=2)

## Paper vs sim
- Only **CPR** and **PDO** overlap (both live & sim). **CPR diverges**: sim +0.39%/78% WR
  vs live −0.47%/50% WR (n=4) — early sign of live underperforming sim, but n=4.
  PDO consistent (sim +0.30% vs live +0.09%).
- Everything else can't be compared: no live trades (ADX/CARRY/FOMC/THU_BEAR), or no v9
  representation (R4 family), or no trades at all (EMA/Short Squeeze/Chento).
- A portfolio-level **`variant_daily_returns` `source='live_computed'`** series exists
  (196 days) — a possible cleaner portfolio paper-vs-sim baseline (not pursued here).

## Bottom line
1. **Live paper is far too thin to validate the portfolio** — ~25 trades, most sleeves
   zero. Confirms "bot not close to live." Only R4 has any live signal.
2. **R4_BTC is the live standout** (+0.65%/trade, 6/6 wins) — matches the user's read;
   R4_ETH also net-positive. V2 variants net-negative (but their losers are the 05-13
   off-schedule fills). All n≤6 → directional, not conclusive.
3. **Behavior compliance is good** in sim (100%) and live (except the 2026-05-13 R4_V2
   off-schedule cold-fill — worth a fix/root-cause).
4. **EMA emits no live trades — confirmed correct** (not an emitter bug): last weekly cross 2025-11-09→SHORT predates live paper start, so the cold-start guard holds it flat rather than cold-filling a 7-month-old offside short. EMA enters on the next genuine cross. A continuous sleeve can therefore sit flat for months.

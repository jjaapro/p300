# CARRY (S-078) — calibration log

## Current state (2026-09-12 — cumulative-funding exit)

**Strategy** (`bots/carry/strategy/config.py`): enter delta-neutral
(spot-long + perp-short, equal notional) when 7d average BTC perp funding
> 0; **exit when the trailing 30-day cumulative daily funding is below
−0.5 % of notional** (`EXIT_CUM_DAYS = 30`, `EXIT_CUM_THRESHOLD_PCT = -0.5`,
sleeve side-effect sweep; the sleeve does not enter while the exit
condition is active). P&L = accrued funding − fees/slippage on 4 fills;
price P&L ≈ 0 by construction.

The three-consecutive-negative-days exit ran from the sleeve's creation to
2026-09-12. Pre-registered study
`studies/notebooks/carry_exit_rule_2026_09/` (7,676 Binance BTCUSDT
settlements 2019-09 → 2026-09-11, every toggle charged 0.24 %, scoring
window 2019-12 → 2026-09):

| rule | net %/yr | worst year | vs streak exit, CI90 |
|---|---|---|---|
| streak exit (old) | 10.85 | 1.56 | — |
| always-on | 11.69 | 2.61 | +0.85 [+0.60, +1.11] |
| **30-day cumulative < −0.5 % (shipped)** | **11.58** | **2.61** | **+0.74 [+0.44, +1.04]** |

The streak exit never protected in the worst 30/90/180-day funding
stretches and lost *more* than never exiting in the two longest ones (it
reacted after the damage and paid 0.24 % to leave and 0.24 % to return).
The cumulative rule keeps a defined exit for a sustained negative regime
the data has not yet shown (5 toggles in 6.7 years) at 0.11 %/yr against
always-on. Chosen over always-on by the user on 2026-09-12 for that reason.

Port note: `tests/test_carry_exit_rule.py` pins the sleeve's per-day entry
and exit signals to the study rule over the production funding table. The
one deliberate difference is the entry guard: the study's state machine
enters whenever the 7-day mean is positive, even on a day the cumulative
exit is active, and exits again the next day; the sleeve waits. Trades
closed before 2026-09-12 were closed by the streak rule — a methodology
change, not a regime change.

**Bot** (`bots/carry/config.py`): variant `bot_carry_v1`, $10,000 paper,
**fixed-notional 1× capital** (no stop exists → fixed-R doesn't apply;
risk is basis/funding, not price), 60s ticks, daily-idempotent decisions.
Mgmt tables: btc_1m + cd_funding_rate. Monitor eval limit 26h. The new
exit takes effect on the bot's next restart; an open position simply
switches rule.

**Account design (pool restructure, decided 2026-09-12):** CARRY's perp
short was on during 96.6 % of ADX's long-days
(`studies/notebooks/adx_robustness_2026_09/`, P1e), so in one cross-margin
account the two perp legs net and the pair is economically "ADX long on
spot". Funding on ADX's longs is the whole ADX live-vs-research gap; keep
the two sleeves in the same account (pool plan D8).

Replaces the stranded legacy CARRY (SJ-3452, closed 2026-07-22
`legacy_shutdown` at +$24.69 after 69 days of accrual). Note the funding
cadence cutover 2026-04-13 (1h predicted → 8h settlement) — the sleeve's
7d window is fully post-cutover in live operation.

## ETH twin — `bots/carry_eth`, variant `bot_carry_eth_v1` (2026-09-19)

The same sleeve module with `CARRY_ASSET=ETH` (set by the wrapper before the
sleeve is imported, as chento's ETH leg does): the same entry (7-day mean of
daily ETHUSDT funding > 0), the same CUM-30D exit, fixed-notional 1× of its own
$10,000, `ENTRY_EXIT_COST_PCT` 0.20 % (declared, not measured on ETH). Data:
funding from `cd_funding_rate_eth` (8-hour settlement rows), the spot close
from the last minute of the UTC day in `eth_1m`, the perp close from the last
`cd_futures_eth_15m` bar; mgmt tables `eth_1m`, `cd_futures_eth_15m`,
`cd_funding_rate_eth`. The carry close books the trade's own asset's funding
(`strategies.trades.close_carry_trade`, changed the same day). Pre-registered
and concluded RECOMMEND in `studies/notebooks/carry_eth_2026_09/` (12.8 %/yr
net, CI90 9.5–16.6, worst year +0.36; equal-weight BTC + ETH book net ÷ DD 15.1
against BTC alone 4.9). Research baseline: one completed episode
(`bots/carry_eth/research_baseline.json`, +67.05 % of capital 2019-12-03 →
2022-09-12, left-censored), so the per-trade AMBER test is as unusable as on
BTC. Operator go-ahead 2026-09-19.

## Change history

| Date | Change | Why / provenance |
|---|---|---|
| 2026-09-19 | **ETH paper twin** `bots/carry_eth` (`bot_carry_eth_v1`); the sleeve gained `config.ASSET` / `PRICE_SOURCES`; `close_carry_trade` reads the trade's asset. No change to the BTC bot's rule or numbers. | `studies/notebooks/carry_eth_2026_09/findings.md` RECOMMEND; operator go-ahead 2026-09-19. `tests/test_carry_eth.py`. |
| 2026-09-14 | **No calibration change.** The scheduled-exit backstop now closes an overdue CARRY trade through `signal._close_carry_paper`: delta-neutral, short-leg funding minus 0.20 % fees and 0.04 % slippage, no price P&L. Before, it used the generic perp close, which would have booked a directional long at 15 bp with long-side funding, silently wrong. The path is still unreachable: CARRY trades carry the 2099 no-exit placeholder (open SJ-4242 does). The backstop now shares the sleeve close's known gap: `close_carry_trade` takes no write lock (BACKLOG 4.5). Also: a tick whose `decide()` raises now still runs the backstop and reports heartbeat `error`. Takes effect when carry restarts. Restarted 2026-09-14 19:06:27Z stop / 19:06:59Z start (commits 1c201c5, 5485899, 5ce3e7e); backstop closes before that booked the old defaults. | BACKLOG 4.4 and 18; operator go-ahead 2026-09-14. Kept as defence in depth; `tests/test_carry_bot.py` forces a real exit time (−14.00 on $10,000, where the old backstop booked +75.00). |
| 2026-09-12 | Exit rule: 3-consecutive-negative-days streak → trailing 30-day cumulative funding < −0.5 % (`EXIT_NEG_DAYS` removed; `EXIT_CUM_DAYS`, `EXIT_CUM_THRESHOLD_PCT` added) | `studies/notebooks/carry_exit_rule_2026_09/findings.md` — pre-registered RECOMMEND (+0.74 %/yr, CI90 excludes 0, better worst year); brainstorm validation C2 first measured the streak exit at −0.85 %/yr vs always-on. User go-ahead 2026-09-12. |
| 2026-07-22 | Extracted to standalone bot; fixed-notional 1× | Bot-extraction plan P4; market-neutral diversifier for the long-heavy fleet |

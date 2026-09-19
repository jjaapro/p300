# Roadmap — open work

**Objective.** Profitable trading strategies: improve the ones running, research new ones. Paper trading and the
dashboard are enough for the operator to track progress for now, so everything on the path to real capital is parked
(§6) and ranks below any strategy work.

**One topic at a time.** §0 is the order. Finish a topic — its study, its notebook, its decision — before opening the
next; do not do a little of everything. When a strategy is improved and studied again, that is when its missing or
unexecuted notebook gets written (decided 2026-09-18: do not backfill notebooks for their own sake).

**Exits.** Every time-based exit in the fleet — chento 72 h, squeeze_bull 48 h, short_squeeze 6 h — is a placeholder,
not policy. The aim is to replace each with an exit that knows the trade is wrong (an invalidation level, the way
chento trades: "lose 80.4 and we see 77"), and to test partial take-profits alongside. Nothing changes until something
measurably better exists: the exit-policy study has so far found nothing that beats the placeholders (chento arm
INCONCLUSIVE; microstructure, spot/perp, the implied range and the rejection wick NONE PROMOTED; partial
take-profits KEEP_6R), and that is the bar any replacement must clear. The exhaustion brainstorm is exhausted.

**Rules of this file.** Open work only; done and obsolete items are deleted in the commit that finishes them, and the
history lives in git (last long-form version: `git show 5c72c8c:BACKLOG.md`). **Numbered items keep their numbers**
because code, tests and docs cite them ("BACKLOG 15", "item 19"); a number that is not here is closed. Study results
live in each study's findings file, not here. Data measurements that constrain future studies live in §5.

---

## 0. The road

In order. Each step names its section.

1. **Squeeze pair** (§2.1) — decision 8 and the D4 trip are the operator's; then the short_squeeze exit arm and
   stage B if wanted. Its n = 20 / 30 gates are the nearest real verdict in the fleet.
2. **Second assets** (§3.1) — CARRY's ETH paper twin shipped 2026-09-19 (§2.4); SHORT_SQUEEZE on ETH is killed;
   SQUEEZE_BULL on ETH is the one left, and its data route now exists. The validation audit's one-line
   conclusion was that the constraint is breadth, not edge.
3. **Chento** (§2.2) — close decisions 14 and 15, then the replay baseline and the time-stop twin.
4. **R4** (§2.3) — after its first windows have traded; nothing to do before 2026-10-02 but watch.
5. Then §3.2 onward, in the order listed there.

## 1. Now

- **Watch r4's first enabled window**, Fri 2026-10-02 04:00 UTC (R4_ETH V1: Tue 2026-10-06 20:00). r4 has never
  traded; confirm the first open and its scheduled close.
- **Start `carry_eth`** (`.\start_fleet.ps1 -Units carry_eth`): the ETH paper twin of CARRY, shipped 2026-09-19 on
  the operator's go-ahead; nothing starts it until then. Its first decision comes at the next UTC day boundary.

## 2. Existing strategies

One strategy at a time. Each carries its evidence gate (the rule was fixed before the data) and its open work.

### 2.1 Squeeze pair — SQUEEZE_BULL and SHORT_SQUEEZE

| Gate | Rule | Where |
|---|---|---|
| Paired re-cuts, stop vs no-stop | at 20 and 30 paired fires; SHORT_SQUEEZE retires if both variants ≤ 0 at 30 | `studies/notebooks/squeeze_recut/run_recut.py`; `docs/calibration/squeeze_bull.md`, `short_squeeze.md` |
| SQUEEZE_BULL incumbent | DISABLE if mean R ≤ 0 at n = 20; at n = 30 also if DSR < 0.50 | `docs/calibration/squeeze_bull.md` |
| SQUEEZE_BULL Rule B (funding + CVD) | revisit at ≥ 10 of its own OOS fires; nothing counts them live, so recount by hand | `docs/calibration/squeeze_bull.md` |

Cadence: squeeze_bull ~25 fires/yr in bull tape, none in bear; short_squeeze droughts up to 186 days.

- **Decision 8.** Does SJ-4250 count toward the n = 20 / 30 re-cut? On the corrected open-interest table its bar
  (2026-09-11 17:00) does not fire, in the revalidation study's own harness (`squeeze_bull_revalidation/findings.md`,
  addendum 2026-09-19: BUILD holds, OOS mean R +0.202 → +0.190, MAR 1.60 → 1.59, margins one fire wide as before).
  [no — void it as an artefact of the defect]
- **Decision: the any-time divergence clause D4 has tripped on SJ-4250** (`squeeze_recut/results/`, 2026-09-18
  21:55Z: residual +0.0555 R against 0.05; the harness says DISABLE and exits 1). Its first evaluation, not a change —
  the trade closed 09-13, and the 09-12 runs had nothing closed to compare. The residual is the bot's 60 s tick quotes
  at entry and exit against the replay's bar closes, ~7.6 bp on a 2 % stop; the clause nets funding and booked cost
  but not tick drift, so its 0.05 R budget on this sleeve is ten basis points wide. The harness labels every D4 trip
  DISABLE_NOSTOP (pinned by `tests/test_squeeze_recut.py`) though the diverging trade is the stop variant's.
  [void with decision 8, no disable; then fix the clause's netting and its label, with tests, before a genuine fire
  trips it for the same reason]
- **11. Exit-policy, short_squeeze arm** — report-only until its re-cut. A catastrophe stop for the no-stop twin, whose
  only loss exit is the 6 h time stop. Stage 1 showed microstructure events almost never occur inside its trades.
- **11. Top-anatomy stage B** [user's call]. "Exit after 24 h without a new high" instead of the fixed 48 h,
  pre-registered on ETH long flushes and BTC flat/bear flushes (both untouched so far). Expect exposure cut at
  drift-level value, not losses avoided. Note ETH flushes are also §3.1's holdout — decide which uses them first.
- Parked behind the n = 30 re-cut: alternative trend gates for squeeze_bull (ADX state, weekly EMA); funding-cadence
  fidelity and a symmetric `is_long_macro` detector for short_squeeze.

### 2.2 Chento — chento_v3 (BTC) and chento_v3_eth

| Gate | Rule | Where |
|---|---|---|
| Gate-off paper track | dashboard results warnings (item 16); decision 14 decides whether a hard rule remains | `docs/calibration/chento_triple_v3.md`, from 2026-09-14 08:45 UTC |

- **Decision 14. Figures measured on OKX-gated pools** describe a configuration that no longer runs: the ETH kill rule
  (< +0.3 R after 15 trades), the +0.739 / +0.605 R expectancies, the overlay tilt ranking, the attribution split, the
  LSR B5 scores, the audit DSRs. [retire them all; item 16's AMBER warning covers the ETH rule; re-cut only what a
  named decision needs]
- **Decision 15. Post-loss rules.** The bots' rules are not the overlay study's rules: BTC's `FILTER_NO_TILT` skips 48 h
  after a *closed* stop loss, the study skipped after any losing predecessor closed or not (94 vs 198 of 208 BTC
  triggers); ETH's half-after-loss disagrees on 54 of 184 trades. The 48 h was never fitted and the research rule was
  in-sample; on the honest pool the effect is noise-sized (MAR none 7.20 / skip 8.16 / half 8.34; skip lowers DSR
  0.726 → 0.461). A study is designed, not written (`studies/notebooks/post_loss_rules/`). [retire: take the default,
  no rule on either asset, and close it — more data sharpens an estimate nobody should size on]
- **A new replay baseline** (bounded loaders, 10 bp, gate off), replacing the superseded `__replay_p0gate`. This is
  also where chento's core calibration gets its executed notebook. **Scoped 2026-09-19: an engineering task, not a
  research one.** The runner has no replay mode (`--once`, `--interval`, `--verbose` only); the tooling that
  produced `__replay_p0gate` is gone; the only walking replay left is inside `tests/test_chento_clock_bound.py`, and
  the calibration log says walking replays are ~15× slower since the clock bound. It needs a replay harness first —
  the same harness the time-stop twin below needs to be validated — so build them together, with the operator.
- **What would settle the 72 h time stop.** Data after 2026-09-11: a paper twin without the time stop, under a per-bot
  open-risk budget (the research half of item 13 — r4's `GROSS_MAX_X` is the precedent; RISK_PCT 1.5 % is the
  alternative, drawdown BTC 25 % / ETH 16 %). The go-live half of 13 is in §6.
- **Partial take-profits: DONE, KEEP_6R** (`studies/notebooks/chento_partial_tp_2026_09/`, pre-registered and
  frozen before the run, 2026-09-19). Chento's own ladder, his 3R default, his 1R first take-profit and 500
  random-level ladders of the same fractions all lower MAR on both assets and in both halves (A0 2.54 vs the ladder
  2.09 pooled; paired −0.20 R per trade, CI95 −0.33 to −0.08). Trimming is wrong on this signal, not the levels;
  58 % of trades touch +1R and holding still wins. The squeeze pair does not inherit it. Do not re-propose fixed-level
  trims on chento; the Exits constraint stands unmet on this arm.
- **24. About 1 in 8 time-stop exits go through the backstop** — the sleeve's `not walked_any` misses under the 90 s
  settle margin, so those exits are labelled `scheduled_exit` and close at the tick's quote. A sleeve and golden change;
  do it with the replay baseline.

### 2.3 R4

| Gate | Rule | Where |
|---|---|---|
| R4 ETH paper acceptance | ≥ 20 fires, latency and exit fidelity; never live capital without a mechanism | `docs/calibration/r4.md`, first window 2026-10-02 |

- **Decision 9. R4_ETH research weights.** The J+ simulator books the Tue 20:00 → Wed 20:00 trade on Wednesday's row
  with Wednesday's gate and leverage, which a Tuesday entry cannot know: research 39.985 vs 37.447 pct-pts causal, 86 %
  of the gap from 2025-07-09. Live is causal. Options: A record and park; B lag the gate only; C full fix with a
  separate R4_ETH leverage; D fix `simulate()` only. [A; C only if the simulator is used for a decision again]
- **25. R4's cost has never been measured.** It books the `trades.py` defaults (10 bp + 5 bp + funding). Measure after
  the first windows, the way the execution study did for the others.
- **27. r4's stale-management check is not per asset** — a stale `btc_1m` alone holds back the ETH window close.
- Low: a target-exit sweep (it cannot lift R4's no-live-capital status); `decide_eth` has no after-window guard
  (covered by the 300 s grace).

### 2.4 ADX and CARRY

ADX on ETH was killed (Sharpe 0.72, corr 0.47 with BTC ADX). CARRY's 30-day cumulative exit shipped 2026-09-12.

- **Decision: an ETH paper twin of CARRY** (`studies/notebooks/carry_eth_2026_09/`, pre-registered, CONCLUDED
  2026-09-19, verdict RECOMMEND). The shipped CUM-30D rule on ETHUSDT settlement prints nets 12.8 %/yr (CI90 9.5 →
  16.6, worst year +0.36); the equal-weight BTC + ETH book's net ÷ drawdown is 15.1 against BTC alone's 4.9, because
  the two assets' worst funding stretches do not coincide (daily correlation 0.87). Reported and not hidden: in ETH's
  worst 90 / 180-day windows the shipped exit lost more than never exiting; the old streak exit was the better
  insurance there. Costs are BTC's constant, unmeasured on ETH. **Building it is a bot change:** the carry bot is
  BTC-only in code (`asset="BTC"`, `daily_sums_pct("BTC", …)`, `btc_1m`), so a twin needs an asset parameter,
  `eth_1m` and `cd_funding_rate_eth`, its own paper variant and a parity test like `tests/test_carry_exit_rule.py`.
  [yes]

## 3. New strategies

Pre-registered notebooks; every replay charges measured per-leg costs and funding. Order:

1. **Second assets.** CARRY's ETH paper twin shipped 2026-09-19 (§2.4). **SHORT_SQUEEZE on ETH:
   KILL** (`studies/notebooks/short_squeeze_eth_2026_09/`, 2026-09-19, pre-registered: 70 triggers, net +0.14 R at
   10 bp with the second half −0.09 R, DSR 0.75; the edge is 2022, and BTC itself would fail the same bar at net
   +0.18 R). The five tables the sleeve reads are now built from on-disk panels for any asset the panels cover
   (`ss_eth_lib.tables_from_panels`: 15-minute perp and spot bar for bar what prod holds, hourly bars, close-of-hour
   open interest from the 5-minute archive), faithful to prod for BTC (Jaccard 0.986, fills identical), and the
   execution study's port of the sleeve ran on them unchanged. **SQUEEZE_BULL on ETH** is the one second-asset study
   left: the same open-interest route, the revalidation study's hourly engine, and no live ETH feed. Alts are blocked
   (the screener feed stopped 2026-05-23).
2. **Shelf re-cost** under the no-stop style at measured costs (R4 windows, PDO), then fleet compounding through the
   liquidation walk. Bar: net ≥ 2× the round trip in both halves.
3. **Hawkes / liquidation cascades.** The hourly feed it needs is live since 2026-09-18 (it works on hourly counts, not
   prints), so its clock runs without the collector. Stored hourly history is 2026-02-25 → now, less the lost
   2026-05-24 → 06-21. Re-dated 2026-09-19 against the coverage plan's gates (≥ 2 years and ≥ 500 events): the two
   years land 2028-03-24 counting through the hole, 2028-06-22 contiguous from its end; the event gate is met earlier
   at the measured rate (~440 a year). The daily series since 2021 cannot see a cascade. Not before 2028-03.

Evidence-dated, not effort-dated:

| What | Rule | When |
|---|---|---|
| VRP options study | re-run both modes at OOS n ≥ 6 (`studies/notebooks/vrp_study/findings.md`) | ≈ 2026-12, else 2027-03 |
| LSR B5 V4 (365-row window) | fresh pre-registration at BTC OOS n ≥ 20 (`studies/notebooks/lsr_b5_study/findings.md`) | ≈ 2027-09 |

## 4. Paper-ledger fidelity

Paper trading is the progress measure, so the ledger has to be true. This is where the defects that would make it
lie go, batched, each with its own go-ahead as a prod change. Nothing open. Fires are counted in `bot_tick_daily`
(OPERATIONS §11) from the bots' next restart on.

## 5. Data

**The data coverage plan** (`studies/material/data_coverage_2026_09/DATA_COVERAGE_PLAN.md`, 2026-09-18) is the
reference for what each venue publishes, what is forward-only and what an archive still sells. Its measurements hold.
Its *ordering* does not: it was written under "data itself has value for future research", and this roadmap puts
strategy work first. So an item is pulled from it only when a scheduled study needs it. Pulled so far: the 5-minute
`metrics` archive (item 30's verification, done), the bot tick log (its item 3, done 2026-09-19), and **the
feed-vintage table** (its item 5),
still open — it is what would have caught the open-interest shift on 2026-06-10 instead of 2026-09-15. Its item 2 is
done.

**What was established on 2026-09-18, because it constrains any study that reads liquidations:**
- `ca_liquidations` (hourly, BTC + ETH, rolling ~89-day source) and `ca_liquidations_daily` (2021-01-01 →) have a live
  Coinalyze writer (`data/sources/coinalyze.py`) and freshness contracts. Nothing on this front is expiring.
- **CoinDesk is closed** (401 unauthenticated; keys are paid). `cd_liquidations` is complete 2026-02-25 → 2026-09-18
  from three hand-pulled pages (`data/archive/17*.json`, replayed by `data/import_coindesk_export.py`) and will not
  advance. **Its 2026-04-23 18:00 → 2026-06-01 15:00 hours are 934 zeros, not data** — a CoinDesk-side outage.
  `check_gaps` cannot see this because the rows exist; a study reading that window gets 39 days of "no liquidations"
  that are really "no data". Coinalyze covers it at daily resolution; the hourly detail is gone.
- Hourly 2026-05-24 → 2026-06-21 aged out of Coinalyze while nothing was fetching (`known_unfillable.json`).
- The two sources are interchangeable for `BTCUSDT_PERP.A`: on 1,368 shared hours, long ρ 0.9999 (totals within
  0.1 %), short ρ 0.9911 (within 2.9 %), 98.2 % of hours equal to 1e-6. Treat them as one series.
- **Gamma levels are blocked on data.** No per-strike option OI history exists anywhere; prod.db
  `deribit_options_daily` carries OI and IV only since 2026-09-06. Testable in a year or two. `trader.db
  cd_options_oi` is misnamed (mark-price OHLC, no OI).

- **Open interest is stamped at the close of its hour again** (item 30, 2026-09-18 21:49 UTC, `0adf114`). The
  Binance-era rows were moved back one hour and verified against the 5-minute archive by which snapshot each row is
  *closer* to — 88.6 % start-of-hour before, 85.9 % close-of-hour after; the two Binance series never agree exactly,
  so no tolerance is honest — and the monitor's daily deep run now scores it (`OI_SEMANTICS`). **One island remains:**
  18 rows the June migration back-filled inside the CoinDesk era, 2026-06-02 13:00 → 06-03 06:00, still hold the
  start-of-hour snapshot; the revalidation study reconstructs around them and its bull-gated set is unaffected. The
  moved block's backup, `cd_open_interest_bak_20260919`, can be dropped when convenient.

**9. The collector** (`collector.py`, written, tested, dry-run clean, not running) — decision. It records what no
archive sells: liquidation prints (Binance largest-per-second only; Bybit complete for 12 symbols; OKX one per
contract per second), 1-second depth buckets for BTC/ETH, and Hyperliquid contexts, leaderboard and per-account
positions with liquidation prices. **No study shows this data is valuable**; the brainstorm that produced it calls
every entry a hypothesis, and the closest tests are negative (fine-grained flow 0-for-3, coarse book 0-for-2, the
liquidation map killed 2026-09-18). The three streams have different cases: prints are small and the mechanism is
validated coarsely (squeeze_bull); Hyperliquid positions are the only per-account data any venue publishes;
`depth_1s` is a third of the disk, has the weakest prior, and its 1-second schema cannot see the wall refill it would
be collected for. Disk: ~170–230 MB/day, not backed up; C:'s runway is not a floor (§1). [start prints and
Hyperliquid positions only, on D: via `--db` (631 GB free); hold `depth_1s` and `hl_asset_ctx` until a pre-registered
question needs them]

## 6. Parked

Each with what un-parks it.

**Path to real capital** — un-parked when the operator says paper tracking is no longer enough. Ordered gates, none
met: **G0** operations you can trust (monitor, deep scan, backups seen working for weeks; alerts that reach the
operator; a monitor run log — coverage plan §1 item 10); **G1** each bot passes its §2 gate on deflated statistics
with a retirement rule written before the data; **G2** risk — item 13's exposure budget as a hard cap, fleet-level
margin arithmetic and a joint-path margin simulation (pool plan S2, S4) re-scoped to bots, live liquidation modelling,
margin mode per bot; **G3** accounts — decisions 5 (one sub-account per bot, or the pool-plan D8/D9 groupings) and 6
(every go-live document assumes Binance, `.env` holds only MEXC keys); **G4** execution — taker entries, resting
reduce-only take-profits, exchange-resident stops instead of 60 s polling, a fills record, an exchange adapter
(`studies/notebooks/execution_2026_09/findings.md`); decision 7, the E7 quoting probe, only if maker entries are
wanted [no — E2 bounds the whole maker gain at 0.01–0.06 R]; **G5** an end-to-end audit of the live path, then
smallest-first sizing with a circuit breaker.

**Research, with triggers.**
- Archived strategies (CPR / PDO / THU_BEAR): only through a study and a bot of their own.
- US-session intraday momentum (left by the ORB study): a mechanism statement, maker execution — which E2 already
  measured as worthless on our entries — and data after 2026-09-13. Do not retest ORB entries, filters or exits with
  taker execution.
- Flow detail: footprint C3 on alts; dwell-block as a filter. Both cost-killed on BTC; alt costs are worse.
- OI short-flush continuation as a trade (+1.85 % at 168 h, descriptive only); a causal pivot + CVD divergence as an
  ADX veto (never backtested; the original pivot had look-ahead).
- Spot-lagging highs as an *entry* on chento (the one thing the spot-vs-perp study left: flow leg +1.046 vs −1.064;
  unprotected by the family correction, absent on squeeze_bull). Needs its own pre-registration.
- The counter-short comparator from chento's hedging (§2.2's partial-TP study, stage 2): at a causal resistance
  trigger on an open winner, do nothing vs counter-short. With the partial-close leg dead it is a two-arm test, and
  the counter-short carries the regime table's prior (shorts against an up_30d regime +0.67 R / 55 %, removed from
  the sleeve; B13's opposite leg 5 % WR; the one scoreable chento instance lost). Needs the trigger defined first.
- Institutional trader ("Astronomer") material, when the rules are shared (likely needs L2).
- Tooling for an honest go/no-go: SPA / White's reality check and full-grid PBO.
- Study validation reviews (`studies/notebooks/study_validation_audit_2026_09/`, 51 plans): folded into §2 — each
  strategy's P0 corrections happen when that strategy is studied again, not as a standalone pass.
- Coverage plan items with no study attached: Deribit trade tape, Gate.io `contract_stats`, the USDC option chain,
  `fundingInfo`/`constituents`, perp contract specs, DVOL 1-minute, the one-off COIN-M and `EOHSummary` downloads. The
  two Deribit `is_liquid()` constants (its item 1) are a two-line deletion that stops discarding 49 % / 61 % of option
  OI at every snapshot — do it when anything touches `deribit.py`.
- Do not re-propose: the estimated liquidation map, spot- vs perp-led highs as an exit, absorption in any form, FVG /
  LVN magnets, the OKX gate, calendar cells, ORB, Coinbase premium, delta-neutral, basis carry, the VRP strangle,
  the implied-range exit (any multiplier, scale or anchor) and the rejection wick at levels (any bar size or level
  set) — exit-policy stage R, 2026-09-19, the exhaustion brainstorm's last pair; the ETH/BTC spread on any bull
  state as a sleeve and cross-asset hedging of chento's trades (`studies/notebooks/eth_btc_spread_2026_09/`,
  2026-09-19: the spread is one year, 2025, at a 63 % drawdown; a hedge in the other asset removes the market move
  chento's timing is paid in, −1.04 R and −0.59 R per trade); SHORT_SQUEEZE on ETH at the shipped rule
  (`short_squeeze_eth_2026_09/`, 2026-09-19: net +0.14 R, second half negative, the edge is 2022).
- The ETH/BTC regime thesis, for the record: on strong_bull days long ETH beat long BTC over 2020-26 (+190.7 % vs
  +93.1 % net of 10 bp and funding), but as a relative position it gives back more than it protects, and even the
  long is a 2020-and-2025 story (2021 −5.5 %, 2024 +2.6 %).

**Operations hygiene** — batch when convenient; none affects a strategy result.
19. Live schema drift from the 2026-05-18 PK rebuild: `trades` lost NOT NULL and DEFAULTs; `variants` has no PRIMARY
    KEY (two simultaneous starts of one bot can register a duplicate); `trade_adjustments` and `ai_quant_decisions`
    lost DEFAULTs. `tests/test_live_schema_constraints.py` pins each; a fix updates its pin.
21. 288 orphan `trade_adjustments` rows (ids 69–356). Archive, delete or leave, and record it.
- Tests still reach prod.db read-write through library helpers (`botlib`, `monitor`); the test-side opens are
  read-only since 2026-09-19, and a `conftest` connect guard like `okx_gate_revalidation`'s would settle it; the dry-run probe covers only `botlib.point_at_db_copy`; the chento
  fixture hash alarm fires on B-tree page layout; `strategy_health`'s 2.5× gross-headroom default reads negative for
  chento; cadences with no owner (weekly `strategy_health`, monthly attribution, periodic memory audit); MFE/MAE per
  closed trade (confirm `btc_1m` retention first); `.env` still names the archived AI_QUANT variables.
- Docs: rewrite GATE_VALIDATION.md (dead paths; its §5 promotion rule is not the one the OKX re-test used).

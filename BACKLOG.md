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
INCONCLUSIVE, microstructure and spot/perp NONE PROMOTED), and that is the bar any replacement must clear.

**Rules of this file.** Open work only; done and obsolete items are deleted in the commit that finishes them, and the
history lives in git (last long-form version: `git show 5c72c8c:BACKLOG.md`). **Numbered items keep their numbers**
because code, tests and docs cite them ("BACKLOG 15", "item 19"); a number that is not here is closed. Study results
live in each study's findings file, not here. Data measurements that constrain future studies live in §5.

---

## 0. The road

In order. Each step names its section.

1. **Fix the evidence the squeeze bots stand on** — item 30 (§1), then decision 8. Everything downstream of the
   open-interest table is wrong by one bar until this lands.
2. **Squeeze pair** (§2.1) — the post-June re-cut, the short_squeeze exit arm, stage B if wanted. Its n = 20 / 30 gates
   are the nearest real verdict in the fleet.
3. **Second assets** (§3.1) — SHORT_SQUEEZE and CARRY on ETH. Data exists, nothing to collect, and the validation audit's
   one-line conclusion was that the constraint is breadth, not edge.
4. **Chento** (§2.2) — close decisions 14 and 15, then the replay baseline and the time-stop twin.
5. **Paper-ledger fidelity** (§4) — the defects that would make paper results lie, batched once, before the paper tracks
   are old enough to be read.
6. **Move-vs-implied-range and the rejection-wick exit** (§3.2) — the two free on-disk tests the exhaustion brainstorm
   left; the last of that family worth running.
7. **R4** (§2.3) — after its first windows have traded; nothing to do before 2026-10-02 but watch.
8. Then §3.3 onward, in the order listed there.

## 1. Now

- **30. The live open-interest feed is one bar stale since 2026-06-10, and SJ-4250 fired because of it.** Production
  fix; needs a go-ahead.
  - **The defect.** `cd_open_interest.oi_close` stamped at an hour is the open interest at the *start* of that hour since
    the Binance fetcher replaced CoinDesk. Before that it was the *end* of the hour, which is what squeeze_bull was
    researched and validated on. Measured against Binance's 5-minute archive, every month matches exactly at one lag.
  - **SJ-4250 is an artefact.** Its trigger bar (2026-09-11 17:00) had −2.48 % on the stored values but −1.79 % at bar
    closes, short of the −2 % trigger. Since 06-10 the bull fires are 09-04 14:00 and 09-11 17:00 on stored values,
    versus a single 09-04 13:00 fire at bar closes.
  - **Also affected.** The revalidation ledger's post-June rows, and short_squeeze's Asia open-interest change.
  - **Fix.** In `data/sources/binance.py::fetch_open_interest()`, store the end-of-hour value under each bar's stamp.
    Backfill from 06-10 (the Binance Vision 5-minute `metrics` archive is the truth series: BTCUSDT from 2020-09,
    ETHUSDT from 2021-12, ~90 MB, one download), add a monitor check against the 5-minute series, then re-cut the
    post-June ledger rows. A feed-vintage table (coverage plan §1 item 5) is the follow-on that would have caught this
    on 2026-06-10 instead of 2026-09-15.
  - Evidence: `studies/notebooks/exit_policy_2026_09/findings_top_anatomy.md` §7.
- **Watch r4's first enabled window**, Fri 2026-10-02 04:00 UTC (R4_ETH V1: Tue 2026-10-06 20:00). r4 has never
  traded; confirm the first open and its scheduled close.
- **Disk.** C: hit 0 bytes free on 2026-09-18 with the fleet live; ~20 GB returned when Firefox closed (deleted-but-open
  handles, invisible to any directory scan). `backup.py` refuses to snapshot below 2 × prod.db (~3.3 GB), so a repeat
  silently skips the nightly backup that protects the paper record, and nothing alerts on free space. Smallest fix: a
  free-space check in `monitor.py` at the backup floor.

## 2. Existing strategies

One strategy at a time. Each carries its evidence gate (the rule was fixed before the data) and its open work.

### 2.1 Squeeze pair — SQUEEZE_BULL and SHORT_SQUEEZE

| Gate | Rule | Where |
|---|---|---|
| Paired re-cuts, stop vs no-stop | at 20 and 30 paired fires; SHORT_SQUEEZE retires if both variants ≤ 0 at 30 | `studies/notebooks/squeeze_recut/run_recut.py`; `docs/calibration/squeeze_bull.md`, `short_squeeze.md` |
| SQUEEZE_BULL incumbent | DISABLE if mean R ≤ 0 at n = 20; at n = 30 also if DSR < 0.50 | `docs/calibration/squeeze_bull.md` |
| SQUEEZE_BULL Rule B (funding + CVD) | revisit at ≥ 10 of its own OOS fires; nothing counts them live, so recount by hand | `docs/calibration/squeeze_bull.md` |

Cadence: squeeze_bull ~25 fires/yr in bull tape, none in bear; short_squeeze droughts up to 186 days.

- **Decision 8.** Does SJ-4250 count toward the n = 20 / 30 re-cut, given item 30? [no — it did not fire at bar closes]
- **Re-cut the post-June ledger rows** once item 30 lands; the paired re-cut reads the corrected table.
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
  also where chento's core calibration gets its executed notebook.
- **What would settle the 72 h time stop.** Data after 2026-09-11: a paper twin without the time stop, under a per-bot
  open-risk budget (the research half of item 13 — r4's `GROSS_MAX_X` is the precedent; RISK_PCT 1.5 % is the
  alternative, drawdown BTC 25 % / ETH 16 %). The go-live half of 13 is in §6.
- **Partial take-profits, as a pre-registered arm of the replay baseline.** Chento's own rule: first TP at 1R, partials
  after, tiered (`studies/notebooks/chento_journal/strategy_spec.md`). Never tested on our sleeves. The prior is
  against it on cumulative R — wider fixed targets up to ~8R beat tighter ones on this signal, and a fixed TP beats
  every trailing variant — so the arm is judged on MAR and drawdown, not cum R, and its placebo is the same trim at a
  random fraction of the target. If it holds, the squeeze pair inherits the design.
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

Nothing open beyond their paper tracks. ADX on ETH was killed (Sharpe 0.72, corr 0.47 with BTC ADX). CARRY's 30-day
cumulative exit shipped 2026-09-12. The one defect: **26. `close_carry_trade` takes no write lock** — the losing caller
of a race logs a close that did not happen (§4).

## 3. New strategies

Pre-registered notebooks; every replay charges measured per-leg costs and funding. Order:

1. **Second assets.** SHORT_SQUEEZE and CARRY on ETH — data exists. SQUEEZE_BULL on ETH has archive open interest for
   research (ETHUSDT 5-minute from 2021-12) but no live feed. Alts are blocked (the screener feed stopped 2026-05-23).
   The most direct lever on breadth.
2. **The two free tests the exhaustion brainstorm left**, as exit-information tests with placebos:
   **move vs implied range** (`deribit_dvol_daily`: clean daily OHLC BTC + ETH 2022-09-07 →, no gaps, stamped at the
   UTC day open with the current day partial; a magnitude test has no level-distance confound, and its placebo is the
   same rule with the implied range replaced by trailing realised volatility) and the **rejection-wick exit**
   (Paladin's, +0.12 R on his entries, never tried on our sleeves; its levels must be causal). Everything else in that
   brainstorm is dead or blocked — see §5 and §6.
3. **ETH/BTC regime spread** (strong-bull days only) and hedged expressions of existing signals.
4. **Shelf re-cost** under the no-stop style at measured costs (R4 windows, PDO), then fleet compounding through the
   liquidation walk. Bar: net ≥ 2× the round trip in both halves.
5. **Hawkes / liquidation cascades.** The hourly feed it needs is live since 2026-09-18 (it works on hourly counts, not
   prints), so its clock runs without the collector. Stored hourly history is 2026-02-25 → now, less the lost
   2026-05-24 → 06-21. Re-date its estimate against that before picking it up; the old "≥ 2028-06" assumed no feed.

Evidence-dated, not effort-dated:

| What | Rule | When |
|---|---|---|
| VRP options study | re-run both modes at OOS n ≥ 6 (`studies/notebooks/vrp_study/findings.md`) | ≈ 2026-12, else 2027-03 |
| LSR B5 V4 (365-row window) | fresh pre-registration at BTC OOS n ≥ 20 (`studies/notebooks/lsr_b5_study/findings.md`) | ≈ 2027-09 |

## 4. Paper-ledger fidelity

Paper trading is the progress measure, so the ledger has to be true. These are the defects that would make it lie.
Batch them; each needs its own go-ahead as a prod change.

20. **`created_at` is NULL on every trade and variant since 2026-05-18**, so `ledger_coherence`'s OPEN/CLOSE/seq checks
    have audited no bot-era trade. Options: write it in `open_paper_trade`; `COALESCE(created_at, actual_entry_time)`
    in the checks; backfill. Remove the strict xfail in `tests/test_ledger_coherence.py` with the fix.
23. **An exception after `decide()` still skips that tick's exits**, and in the squeeze bots the second variant. Where:
    the entry-table check, sizing, `execute()`, `open_gross_usdt`, `DuplicateInstanceError`, r4's `_sleeve()` /
    `deciders()`. Fix: wrap the entry path in the same try as `decide()`.
26. **`close_carry_trade` takes no write lock.** The losing caller of a race logs a close that did not happen.
28. **A double close across 00:00 UTC is not caught** — every duplicate check groups by `event_date`. Candidate: a
    date-independent "more than one OPEN or CLOSE per trade" count.
- **Count fires.** Every "revisit at n OOS fires" rule in §2 and §3 counts fires, and nothing counts them:
  `bot_heartbeats` is overwritten every 60 s. An append-only `bot_ticks` log (coverage plan §1 item 3) is the smallest
  thing that makes those gates checkable without a hand recount.
- The dashboard shows no R multiple for squeeze_bull trades (no `_risk` key in the notes).

## 5. Data

**The data coverage plan** (`studies/material/data_coverage_2026_09/DATA_COVERAGE_PLAN.md`, 2026-09-18) is the
reference for what each venue publishes, what is forward-only and what an archive still sells. Its measurements hold.
Its *ordering* does not: it was written under "data itself has value for future research", and this roadmap puts
strategy work first. So an item is pulled from it only when a scheduled study needs it. Pulled so far: the 5-minute
`metrics` archive and the feed-vintage table (both into item 30), the bot tick log (§4). Its item 2 is done.

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
- Institutional trader ("Astronomer") material, when the rules are shared (likely needs L2).
- Tooling for an honest go/no-go: SPA / White's reality check and full-grid PBO.
- Study validation reviews (`studies/notebooks/study_validation_audit_2026_09/`, 51 plans): folded into §2 — each
  strategy's P0 corrections happen when that strategy is studied again, not as a standalone pass.
- Coverage plan items with no study attached: Deribit trade tape, Gate.io `contract_stats`, the USDC option chain,
  `fundingInfo`/`constituents`, perp contract specs, DVOL 1-minute, the one-off COIN-M and `EOHSummary` downloads. The
  two Deribit `is_liquid()` constants (its item 1) are a two-line deletion that stops discarding 49 % / 61 % of option
  OI at every snapshot — do it when anything touches `deribit.py`.
- Do not re-propose: the estimated liquidation map, spot- vs perp-led highs as an exit, absorption in any form, FVG /
  LVN magnets, the OKX gate, calendar cells, ORB, Coinbase premium, delta-neutral, basis carry, the VRP strangle.

**Operations hygiene** — batch when convenient; none affects a strategy result.
19. Live schema drift from the 2026-05-18 PK rebuild: `trades` lost NOT NULL and DEFAULTs; `variants` has no PRIMARY
    KEY (two simultaneous starts of one bot can register a duplicate); `trade_adjustments` and `ai_quant_decisions`
    lost DEFAULTs. `tests/test_live_schema_constraints.py` pins each; a fix updates its pin.
21. 288 orphan `trade_adjustments` rows (ids 69–356). Archive, delete or leave, and record it.
22. `health.py`'s single-open check only sees legacy `p300_%` variants; a repoint must exempt chento's and r4's stacking.
- 27 tests open prod.db read-write for SELECTs; the dry-run probe covers only `botlib.point_at_db_copy`; the chento
  fixture hash alarm fires on B-tree page layout; `strategy_health`'s 2.5× gross-headroom default reads negative for
  chento; cadences with no owner (weekly `strategy_health`, monthly attribution, periodic memory audit); MFE/MAE per
  closed trade (confirm `btc_1m` retention first); `.env` still names the archived AI_QUANT variables.
- Docs: OPERATIONS §7.2 calls the look-ahead port open and has old test counts; `portfolio_with_pools.md` calls chento
  BTC-only; README status date; "Current state" headers in `docs/calibration/adx.md` and `short_squeeze.md`;
  `squeeze_bull.md` dates the sizing pool "2022-01 to 2026-06" (fires run 2022-03-25 → 2026-09-04); archive or rewrite
  MANUAL.md; rewrite GATE_VALIDATION.md (dead paths; its §5 promotion rule is not the one the OKX re-test used).

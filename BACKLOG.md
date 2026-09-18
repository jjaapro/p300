# Backlog — open work

Only work that still needs doing. Done, decided and obsolete items were removed on 2026-09-16 at the user's request
("None of that is needed as it live in git history"). The last full version is `git show 5c72c8c:BACKLOG.md`. The
2026-09-15 study results are in their folders' findings files: `studies/notebooks/orb_study/` and
`studies/notebooks/exit_policy_2026_09/`.

**Numbered items keep their numbers**, because code, tests and docs cite them ("BACKLOG 15", "item 19"). A number that
is not here is closed; look it up in git history. When an item is finished, delete it in the same commit.

---

## 1. Operator, now

- **30. The live open-interest feed has been one bar stale since 2026-06-10, and SJ-4250 fired because of it.**
  Production fix, so it needs a go-ahead.
  - **The defect.** `cd_open_interest.oi_close` stamped at an hour is the open interest at the *start* of that hour since
    the Binance fetcher replaced CoinDesk. Before that it was the *end* of the hour, which is what squeeze_bull was
    researched and validated on. Measured against Binance's 5-minute archive, every month matches exactly at one lag.
  - **SJ-4250 is an artefact.** Its trigger bar (2026-09-11 17:00) had −2.48 % on the stored values but −1.79 % at bar
    closes, short of the −2 % trigger.
  - **Fires differ.** Since 06-10 the bull fires are 09-04 14:00 and 09-11 17:00 on the stored values, versus a single
    09-04 13:00 fire at bar closes.
  - **Also affected.** The revalidation ledger's post-June rows carry the shift, and short_squeeze's Asia open-interest
    change reads the same table.
  - **Fix.** In `data/sources/binance.py::fetch_open_interest()`, store the end-of-hour value under each bar's stamp.
    Backfill from 06-10, add a monitor check against the 5-minute series, then re-cut the post-June ledger rows.
  - Evidence: `studies/notebooks/exit_policy_2026_09/findings_top_anatomy.md` §7.
- **Watch r4's first enabled window**, Fri 2026-10-02 04:00 UTC (R4_ETH V1: Tue 2026-10-06 20:00). r4 has never
  traded; confirm the first open and its scheduled close.
- **Free disk, and decide what guards it.** C: hit **0 bytes free** on 2026-09-18 with the fleet live; ~20 GB came
  back the moment Firefox was closed, which means it was holding that much in deleted-but-still-open handles —
  invisible to any directory scan, so nothing that walks the filesystem can see this coming. Two things ride on it:
  `backup.py` refuses to snapshot below 2 × prod.db (~3.3 GB), so a repeat skips the nightly backup without failing
  loudly, and item 9's collector has no runway at all until there is slack. Nothing currently alerts on free space.

## 2. Decisions waiting on the operator

Recommendation in brackets.

1. **15. Post-loss rules.** The bots' rules are not the overlay study's rules.
   - **The mismatch.** BTC's `FILTER_NO_TILT` skips 48 h after a *closed* stop loss; the study skipped after any losing
     predecessor in trigger order, closed or not (94 vs 198 of 208 BTC triggers taken). ETH's half-after-loss disagrees on
     54 of 184 trades.
   - **The evidence is weak.** The 48 h was never fitted, and the research rule was picked in-sample. On the honest pool the
     effect is noise-sized (BTC MAR none 7.20, skip 8.16, half 8.34; skip lowers DSR 0.726 → 0.461).
   - **Designed, not written:** `studies/notebooks/post_loss_rules/`, with arms no rule / coded BTC / coded ETH / causal
     research rule / same-direction only, placebos, and N_TRIALS 87.
   - **Needs** (a) the objective, drawdown/MAR or total return, and (b) the default if the study is inconclusive [no rule
     on either asset].
2. **14. Chento figures measured on OKX-gated pools** describe a configuration that no longer runs. Per figure, re-cut on
   the gate-off arm or retire:
   - the ETH kill rule (< +0.3 R after 15 trades) [retire; item 16's AMBER warning covers it];
   - the +0.739 / +0.605 R expectancies, the overlay tilt ranking, the attribution split, the LSR B5 scores and the audit
     DSRs [retire; re-cut only what a decision needs].
3. **9. R4_ETH research weights.** The J+ simulator books the Tue 20:00 → Wed 20:00 trade on Wednesday's row with
   Wednesday's gate and leverage, which a Tuesday entry cannot know.
   - **Size.** R4_ETH's research total is 39.985 vs 37.447 pct-pts causal, 86 % of the gap from 2025-07-09. Live is causal.
   - **Options.** A record and park; B lag the gate only; C full fix with a separate R4_ETH leverage (a live no-op, but an
     edit on r4's sizing path); D fix `simulate()` only.
   - [A; C only if the J+ simulator is used for a decision again.]
4. **13. A per-bot exposure budget for chento before real capital.** Nothing caps a chento bot's total exposure: the
   structural maximum is 12 open positions and 24 % at risk, and history peaks at BTC 5 open / 8.5× gross and ETH 6 open.
   - **Options.** A per-bot open-risk or gross budget (r4's `GROSS_MAX_X` is the precedent), or RISK_PCT 1.5 %
     (drawdown BTC 25 %, ETH 16 %), as its own pre-registered test.
   - Chento as one runner carrying both assets under a cross-asset cap belongs to the same design.
5. **Account architecture.** One exchange sub-account per bot, or the pool-plan D8/D9 groupings. Before any live wiring.
6. **Live venue.** Every go-live document assumes Binance, but `.env` holds only MEXC keys.
7. **E7 live quoting probe** (API key, ≤ $50, two weeks) [no, unless maker entries are wanted].
8. **Does SJ-4250 count** toward squeeze_bull's n = 20 / 30 re-cut, given item 30?
9. **microstructure.db runway.** The collector (`collector.py`, 2026-09-17/18) records ~170–230 MB/day into a database
   that is not backed up, on the prod.db drive. It pauses only its depth_1s rows below 5 GB free (resumes above 8 GB)
   and keeps everything else recording; the 1-second depth cadence and the full hl_asset_ctx / hl_positions sets were
   kept on purpose (extensive dataset).
   - **The runway assumption is gone.** The ~3 months in the original sizing came from 24 GB free on 2026-09-18. C:
     hit 0 that evening and sits near 20 GB after closing Firefox, so the figure is not a floor — it moves by tens of
     GB for reasons outside this repo (§1). Starting the collector against that is how the depth pause becomes the
     normal state rather than the emergency one.
   - **Options:** move the database off C: via `--db` — **D: has 631 GB free and X: 900 GB**, which makes this the
     cheapest fix by far and removes the coupling to prod.db's drive entirely; free the Claude VM bundle
     (OPERATIONS §11 says 8.6 GB, measured 10.76 GB on 2026-09-18); or thin depth_1s to 2 s / float16
     (−37 / −28 MB/day) [**`--db` onto D:**; keep the cadence].

## 3. Defects and code (each needs its own go-ahead)

19. **Live schema drift from the 2026-05-18 PK rebuild and the P2.6 consolidation.**
    - `trades` lost its NOT NULL constraints and DEFAULTs.
    - `variants` has no PRIMARY KEY, NOT NULL, CHECK or DEFAULT, so two simultaneous starts of one bot can register a
      duplicate variant.
    - `trade_adjustments` and `ai_quant_decisions` lost DEFAULTs.
    - `tests/test_live_schema_constraints.py` pins each drift; a fix updates its pin in the same commit.
20. **`created_at` is NULL on every trade and variant since 2026-05-18**, so `ledger_coherence`'s OPEN/CLOSE/seq checks
    have audited no bot-era trade.
    - **Options.** Write it in `open_paper_trade`; use `COALESCE(created_at, actual_entry_time)` in the checks; backfill.
    - Remove the strict xfail in `tests/test_ledger_coherence.py` with the fix.
21. **288 orphan `trade_adjustments` rows** (ids 69–356, SJ-3156 … SJ-3299). Archive, delete or leave, and record it.
22. **`health.py`'s single-open check only sees legacy `p300_%` variants.** A repoint must exempt chento's and r4's
    stacking.
23. **An exception after `decide()` still skips that tick's exits**, and in the squeeze bots the second variant.
    - **Where.** The entry-table check, sizing, `execute()`, `open_gross_usdt`, `DuplicateInstanceError`, and r4's
      `_sleeve()` / `deciders()`.
    - **Fix.** Wrap the entry path in the same try as `decide()`.
24. **Chento sends about 1 in 8 time-stop exits through the backstop.** The sleeve's `not walked_any` misses under the
    90 s settle margin, so those exits are labelled `scheduled_exit` and close at the tick's quote. A sleeve and golden
    change.
25. **R4's cost has never been measured.** It books the `trades.py` defaults (10 bp + 5 bp + funding).
26. **`close_carry_trade` takes no write lock.** The losing caller of a race logs a close that did not happen.
27. **r4's stale-management check is not per asset.** A stale `btc_1m` alone holds back the ETH window close.
28. **A double close across 00:00 UTC is not caught.** Every duplicate check groups by `event_date`. Candidate: a
    date-independent "more than one OPEN or CLOSE per trade" count.

**Smaller, batchable:**

Tests and tooling
- Item 10 follow-ups: 27 tests open prod.db read-write for SELECTs, and the dry-run probe covers only
  `botlib.point_at_db_copy`.
- The chento fixture hash alarm fires on B-tree page layout, not data.
- `strategy_health`'s 2.5× gross-headroom default reads negative for the chento bots.
- r4 `decide_eth` has no after-window guard (covered today by the 300 s grace).

Operations
- Cadences with no owner: weekly `strategy_health` per bot, a monthly attribution re-run, and a periodic memory/system
  audit.
- Record each closed trade's maximum favourable and adverse excursion (from `btc_1m` or in the CLOSE notes). Confirm
  `btc_1m` retention first.
- The dashboard shows no R multiple for squeeze_bull trades (no `_risk` key in the notes).
- `.env` still names the archived AI_QUANT variables.

Documentation
- OPERATIONS §7.2 still calls the look-ahead port open and has old test counts.
- `portfolio_with_pools.md` calls chento BTC-only.
- The README status date is stale.
- The "Current state" headers in `docs/calibration/adx.md` and `short_squeeze.md` are stale.
- `docs/calibration/squeeze_bull.md` dates the sizing pool "2022-01 to 2026-06"; its fires run 2022-03-25 → 2026-09-04.
- Archive or rewrite MANUAL.md.
- Rewrite GATE_VALIDATION.md: its paths are dead, and its §5 promotion rule is not the rule the OKX re-test used.

## 4. Waiting on evidence (rules fixed in advance)

| What | Rule | Where | When |
|---|---|---|---|
| Squeeze paired re-cuts, stop vs no-stop | at 20 and 30 paired fires; SHORT_SQUEEZE retires if both variants ≤ 0 at 30. Read SJ-4250 with its 47.0 h booking and item 30 | `studies/notebooks/squeeze_recut/run_recut.py`; `docs/calibration/squeeze_bull.md`, `short_squeeze.md` | squeeze_bull ~25 fires/yr in bull tape, none in bear; short_squeeze droughts up to 186 days |
| SQUEEZE_BULL incumbent | DISABLE if mean R ≤ 0 at n = 20; at n = 30 also if DSR < 0.50 | `docs/calibration/squeeze_bull.md` | as above |
| R4 ETH paper acceptance | ≥ 20 fires, latency and exit fidelity; never live capital without a mechanism | `docs/calibration/r4.md` | first window Fri 2026-10-02 |
| Chento gate-off paper track | dashboard results warnings (item 16); item 14 decides whether a hard rule remains | `docs/calibration/chento_triple_v3.md` | from 2026-09-14 08:45 UTC |
| VRP options study | re-run both modes at OOS n ≥ 6 | `studies/notebooks/vrp_study/findings.md` | ≈ 2026-12, else 2027-03 |
| LSR B5 V4 (365-row window) | fresh pre-registration at BTC OOS n ≥ 20 | `studies/notebooks/lsr_b5_study/findings.md` | ≈ 2027-09 |
| SQUEEZE_BULL Rule B (funding + CVD) | revisit at ≥ 10 of its own OOS fires; nothing counts them live, so recount periodically | `docs/calibration/squeeze_bull.md` | unknown |
| Hawkes / liquidation cascades | the hourly feed it needs is live since 2026-09-18, so the clock now runs without the collector — the note works on hourly counts, not prints. Stored hourly history is 2026-02-25 → now (~7 months, less the lost 2026-05-24 → 06-21). Re-date the estimate against that before picking it up | `studies/notebooks/hawkes_note.md` | was "≥ 2028-06 without backfill", premised on no feed |

## 5. Research queue

Pre-registered notebooks. Every replay charges measured per-leg costs and funding.

1. **11. Exit-policy study** (`studies/notebooks/exit_policy_2026_09/`). The chento arm, the squeeze_bull arm,
   microstructure stage 1 and top-anatomy stage A are concluded. Still open:
   - **short_squeeze arm, report-only until its re-cut.** A catastrophe stop for the no-stop twin, whose only loss exit
     is the 6 h time stop. Stage 1 showed microstructure events almost never occur inside its trades.
   - **Top-anatomy stage B** [user's call]. "Exit after 24 h without a new high" instead of the fixed 48 h, pre-registered
     on ETH long flushes and BTC flat/bear flushes (both untouched so far). Expect exposure cut at drift-level value, not
     losses avoided.
   - **What would settle chento's 72 h time stop.** Data after 2026-09-11: a chento paper twin without the time stop,
     plus item 13's budget.
2. **Exhaustion and reversal signals we do not monitor** (`studies/material/exhaustion_signals_2026_09/BRAINSTORM.md`,
   2026-09-15).
   - **First, ask** which tools the trader the user saw was using.
   - **Collect** what cannot be bought back: `collector.py` is written, tested and dry-run clean (liquidation prints
     from Binance, Bybit and OKX; 1-second L2 depth buckets; Hyperliquid asset contexts, leaderboard and positions).
     **It needs the operator's go-ahead to start as a fleet unit**; see item 9 for its disk runway. Note what it is
     and is not: *prints*, per event, which no venue keeps and no archive sells. The aggregate hourly series below is
     now safe without it.
   - **Aggregate liquidations are covered again as of 2026-09-18**, so nothing further is expiring on that front.
     `ca_liquidations` (hourly, BTC + ETH) and `ca_liquidations_daily` (2021-01-01 →) have a live Coinalyze writer in
     `data/sources/coinalyze.py` and freshness contracts. What that leaves, and why:
     - **CoinDesk is closed.** `/futures/v1/historical/liquidation/hours` returns 401 unauthenticated and a key is
       paid-only. `cd_liquidations` is complete 2026-02-25 → 2026-09-18 from three hand-pulled pages
       (`data/archive/1788192000.json`, `1780992000.json`, `1789759539.json`, replayed by
       `data/import_coindesk_export.py`) and will not advance again. Do not plan work that needs it live.
     - **Its 2026-04-23 18:00 → 2026-06-01 15:00 hours are zeros, not data** — 934 of them, a CoinDesk-side outage
       (a re-pull returns the same zeros with `CLOSE_LONG_PRICE` frozen). `check_gaps` cannot see this: the rows are
       present, so the series looks unbroken, and a study reading that window gets 39 days of "no liquidations" that
       are really "no data". Coinalyze covers it at **daily** resolution (41/41 days real, 15,013 BTC long / 6,756
       short); the hourly detail is unrecoverable.
     - **Hourly 2026-05-24 → 2026-06-21 is gone for good** — aged out of Coinalyze's ~89-day window while nothing was
       fetching. Recorded in `known_unfillable.json`.
     - **The two sources are interchangeable**, contrary to what `fetch_coinalyze.py` used to claim: on the 1,368
       hours where both hold real data, long ρ 0.9999 (totals within 0.1 %), short ρ 0.9911 (within 2.9 %), 98.2 % of
       hours equal to 1e-6 on both sides. A study may treat them as one series for `BTCUSDT_PERP.A`.
   - **Gamma levels are BLOCKED on data, checked 2026-09-18.** No per-strike open interest history exists anywhere.
     `trader.db cd_options_oi` is misnamed: it holds mark-price OHLC and an update *count*, no OI and no IV, and its
     settlement columns are zero. prod.db `deribit_options_daily` does carry `open_interest` and `mark_iv`, but only
     on rows the live feed wrote — 14,254 of 514,433, i.e. **since 2026-09-06**; the 2023-12 → 2026-04 backfill is
     mark prices only. So dealer gamma accrues forward at ~1,400 instruments a day and is testable in a year or two,
     or from paid history. Do not plan it as a near-term study.
   - **Test on data we have**, as exit-information tests with placebos: **move vs implied range** (the next one to
     pre-register: `deribit_dvol_daily` is clean daily OHLC for BTC and ETH, 2022-09-07 → today, 1,473 rows each, no
     gaps, stamped at the UTC day open with the current day partial), a forced-flow proxy from OI and taker flow, and
     the rejection-wick exit. A magnitude test has no level-distance confound, so the liquidation map's trap does not
     apply; its placebo is the same rule with the implied range replaced by a trailing realised-volatility estimate.
   - **The estimated liquidation map is DONE and dead, 2026-09-18**
     (`studies/notebooks/exit_policy_2026_09/findings_liqmap.md`). The pre-registered verdict reads
     `REPLICATED: up_touch, down_touch`, and §7's distance diagnostic dissolves it: the map's cluster sits nearer price
     than the price-path-only control map's in 64.8 % of states, and matching the two levels on distance takes the
     touch difference from +2.5 pp to +0.10 pp (−0.09, +0.30) on BTC and +0.09 pp on ETH. No turn at all; the burst
     exit (`chento:EV2`) is +0.04 R against its control-map twin (p 0.65). Do not re-propose either arm. **Two things
     it leaves:** the estimated liquidation *amount* series is genuinely informative (range-controlled ρ 0.57 / 0.56
     against measured liquidations, vs 0.36 / 0.28 for a naive proxy) while this model's *side split* loses to
     `(open − low)` vs `(high − open)`; and **a level study must match on distance in its primary test**, not in a
     post-verdict secondary — pairing within a state was not enough.
   - **Spot- vs perp-led highs (row F) is DONE and dead: NONE PROMOTED, 2026-09-18**
     (`studies/notebooks/exit_policy_2026_09/findings_spot_perp.md`). A new high carried by perpetual takers and a
     rising premium while Binance spot lags carries no exit information: chento BTC +1.068 R (95 % +0.100, +2.034, the
     *wrong* sign for an exit), squeeze_bull −0.036, short_squeeze untestable. Do not re-propose it. The one thing it
     leaves is a hypothesis pointing the other way, and only on chento: spot-lagging highs continued (flow leg +1.046)
     while spot-confirmed highs did not (−1.064). That is an entry-side question, unprotected by the family
     correction, absent on squeeze_bull, and it needs its own pre-registration if anyone wants it.
3. **Study validation reviews** (`studies/notebooks/study_validation_audit_2026_09/`, 51 plans; P0 = affected evidence
   needs correction before reuse).
   - **Order** (from its REVIEW_FINDINGS): freeze the shared data/return contracts; correct the shared marked-P&L
     function, the ADX first-day walk and the decision-time feature paths where those results are reused; reproduce the
     affected baselines; then the decision-bearing studies.
   - **P0 behind running bots first:** adx_study, adx_robustness, carry_exit_rule, overlay_study, r4_study,
     short_squeeze_sessions, sizing_style, squeeze_bull_revalidation, squeeze_recut.
4. **Post-loss rules** (item 15), after decision 1.
5. **Second assets.** SHORT_SQUEEZE and CARRY on ETH (data exists). SQUEEZE_BULL on ETH has archive open interest for
   research (ETHUSDT 5-minute from 2021-12) but no live feed. ETH flushes are also stage B's holdout, so decide which
   uses them first. Alts are blocked (the screener feed stopped 2026-05-23). This is the most direct lever on breadth.
6. **Shelf re-cost** under the no-stop style at measured costs (R4 windows, PDO), then fleet compounding through the
   liquidation walk. Bar: net ≥ 2× the round trip in both halves.
7. **ETH/BTC regime spread** (strong-bull days only) and hedged expressions of existing signals.
8. **A new chento replay baseline** (bounded loaders, 10 bp, gate off), replacing the superseded `__replay_p0gate`.
9. **R4 target-exit sweep** (low: it cannot lift R4's no-live-capital status).

**Parked, each with its trigger:**
- **Short_squeeze.** Funding-cadence fidelity (`docs/calibration/short_squeeze.md`); a symmetric `is_long_macro`
  detector.
- **Archived strategies.** The CPR / PDO / THU_BEAR questions, re-entered only through a study and a bot of their own.
- **Flow detail.** Footprint C3 on alts; dwell-block as a filter.
- **US-session intraday momentum** (left by the ORB study). Needs a mechanism statement, maker execution, and data after
  2026-09-13.
- **Trend gates and trend trades.** Alternative trend gates for squeeze_bull (ADX state, weekly EMA), after its n = 30
  re-cut. OI short-flush continuation as a trade (+1.85 % at 168 h, descriptive only). A causal pivot + CVD divergence as
  an ADX veto (never backtested; the original pivot had look-ahead).
- **Institutional trader ("Astronomer") material**, when the rules are shared (likely needs L2 data).
- **Tooling** for any honest go/no-go: SPA / White's reality check and full-grid PBO.

## 6. Path to real capital

Ordered gates; none is met.

- **G0 — operations you can trust.** Monitor, deep scan and backups seen working for weeks; alerts that reach the
  operator (Telegram push was deferred on 2026-09-14); disk healthy.
- **G1 — evidence.** Each bot passes its own pre-registered gate (section 4), on deflated statistics, with a retirement
  rule written before the data.
- **G2 — risk.** Item 13's exposure budget; fleet-level return and margin arithmetic and a joint-path margin simulation
  (pool plan S2, S4) re-scoped to bots; live liquidation modelling re-added; margin mode chosen per bot.
- **G3 — accounts.** Decisions 5 and 6: sub-account per bot or groups, the venue, sub-account availability and terms,
  hedge mode.
- **G4 — execution.** Taker entries, resting reduce-only take-profits, exchange-resident stop orders instead of 60 s
  polling, a fills record (intended vs realised price per leg), an exchange adapter
  (`studies/notebooks/execution_2026_09/findings.md`). E7 only if maker entries are wanted.
- **G5 — re-audit, then start small.** An end-to-end audit of the live path, then smallest-first sizing with an automatic
  circuit breaker.

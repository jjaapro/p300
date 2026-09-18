# Data coverage plan — what to record before it is gone

Written 2026-09-18; revised the same day after three adversarial reviews (§8 lists every finding and what
happened to it). Every number below was measured on this machine or fetched from the venue today; the appendix
says which. Read-only survey: nothing was started, changed or written except this file.

The directive this serves: *"data limitations should not limit our research coverage. Data feeds can always be
added, and data itself has value for future research."* So the question is not which study to run next. It is
which series, if we are not recording it today, cannot be recovered at any price.

---

## 0. Four corrections before anything else

These change the premises the rest of the document rests on, so they come first.

**The collector is not running.** There is no `collector` row in `bot_heartbeats` (the 8 rows are adx, carry,
chento_v3, chento_v3_eth, feed, r4, short_squeeze, squeeze_bull); `data/databases/microstructure.db` does not
exist anywhere on disk; no collector process is alive (22 python processes: 7 bots x 2 under the venv shim,
dashboard x 2, feed x 2). `data/diagnostics/collector_last.json` is a 45-second smoke test from 13:29 UTC today.
So the 170-230 MB/day is a **projection, not a current spend**, the three-month runway in BACKLOG item 9 has not
started counting, and the go-ahead in BACKLOG §5 item 2 is still outstanding. Every disk figure below is computed
from that starting point.

**Every prod.db row costs three times its own size.** `backup.py` takes a whole-file `VACUUM INTO` snapshot and
the scheduled task runs `--keep-daily 2 --keep-weekly 0`, so steady state is the live file plus two rolling
copies (measured today: `prod-20260917.db` 1,604,517,888 B and `prod-20260918.db` 1,605,025,792 B, plus
`prod-20260722.db` held permanently by `PRUNE_FROM = "2026-09-14"`). **A feed item that writes X MB/day of rows
spends 3X MB/day of disk.** The first draft of this plan costed §5.2 at 1x and was wrong by a factor of three;
every figure below is now stated as rows *and* disk. It also lengthens the nightly backup window and raises
`backup.py`'s own 2 x prod.db free-space floor as prod.db grows.

**We have less free space than the first draft assumed, and a 10.8 GB lever.** `shutil.disk_usage('C:/')` reports
**24.17 GB free** at 14:40 UTC (510.8 total). The first draft's 24.6 GB was measured a few hours earlier the same
day; a few hundred MB of intraday churn is normal, which is why every date below is given to the nearest week,
not the day. The Claude desktop VM bundle under `%LOCALAPPDATA%\Packages\Claude_*` measures **10.76 GB** today,
not the 8.6 GB OPERATIONS §11 records. There is also a stray 58.3 MB `b.csv.gz` in the repo root — a Bybit tick
file a survey agent downloaded, untracked, safe to delete.

**`/fapi/v1/leverageBracket` is not public.** The survey called a daily bracket snapshot "the cheapest
forward-only item in this entire sweep". Called unauthenticated today it returns
`401 {"code":-2014,"msg":"API-key format invalid."}`. It needs a signed request with a Binance API key, and
`.env` holds MEXC keys only. It is still worth doing — a read-only Binance key is free to create — but it is a
decision with a credential attached, not a free `curl`. This is exactly the class of claim the brief warned
about, so it is flagged rather than planned around. (The margin *schedule* — `maintMarginPercent`,
`requiredMarginPercent`, `liquidationFee` — is in the unauthenticated `exchangeInfo` and is covered by item 13
below; only the per-notional bracket ladder needs the key.)

---

## 1. The one-page answer

### Start recording this week (in priority order)

| # | Action | Where | MB/day rows | MB/day disk | Why now |
|---|---|---|---|---|---|
| 1 | **Delete two constants in `data/sources/deribit.py`** (`LIQUID_MAX_DAYS = 90`, `LIQUID_MAX_LOG_MONEYNESS = 0.30`) so the snapshot keeps the whole BTC and ETH chain | feed | +0.29 | +0.9 | We are discarding 48.7 % of BTC and 60.6 % of ETH option open interest on every snapshot, *today*, at the strikes dealer gamma actually sits at. Four of the twelve largest BTC OI strikes are dropped — all 25DEC26, 98 days out, missing the cut by 8 days. |
| 2 | **Recover and then keep Coinalyze hourly liquidations and open interest** (`fetch_coinalyze.py --liquidations --interval 1hour`, then wire into `feed.py`) | prod.db | +0.03 | +0.1 | **The fleet has had no liquidation series of any kind since 2026-06-10** and nobody noticed. Measured today with the key in `.env`: 1h liquidation history still returns rows 89 days back and nothing at 90; 1h open interest returns rows at 80 days and nothing at 85. So ~2026-05-24 → ~2026-06-21 is already gone forever and ~88 days are still recoverable **today**, one more day converting from recoverable to lost every day. Working client, live key, 16-venue aggregate. |
| 3 | **Add an append-only bot tick log** (`bot_ticks`: bot, variant, tick, status, note, gate values, plus a daily per-status counter row) | prod.db | +0.05 | +0.15 | Every "revisit at n OOS fires" rule in BACKLOG §4 counts fires, and nothing counts them. `bot_heartbeats` is overwritten every 60 s per unit. |
| 4 | **Poll the Deribit option trade tape hourly**, resuming from the last stored trade | feed | +2.0 | +6.0 | Measured today: the hour ending 22 h back returns 273 trades; the hour ending 24 h back returns **zero**, as do 26/28/30/36/48 h. A hard ~24 h window and the hardest deadline in this document. |
| 5 | **Add a feed vintage table** (`feed_vintages`: table, bucket_ts, fetched_ts, value, checked_ts) over the windows the feed rewrites | prod.db | +0.10 | +0.3 | Would have caught BACKLOG item 30 on 2026-06-10 instead of 2026-09-15. It is the only thing that separates "the golden is wrong" from "the feed changed". |
| 6 | **Poll Gate.io `contract_stats`** for BTC_USDT and ETH_USDT at 5 min | feed | +0.14 | +0.4 | Hard 180-day rolling window (verified: `from` beyond 180 d returns `INVALID_PARAM_VALUE: from time exceeds 180-day limit`). 25 fields nobody else publishes, including `long_users`/`short_users` — account *counts*, not ratios — and per-side liquidation USD. |
| 7 | **Capture the Deribit USDC-settled linear chain** (3,292 instruments today, 7 underlyings) — **with the three fixes in §5.2 item 2** | feed | +1.15 | +3.5 | `_NAME_RE` in `deribit.py` drops every `BTC_USDC-...` row today. It is the only route to SOL, XRP, HYPE, AVAX and TRX option surfaces. Do **not** ship it without those fixes: as specified in the first draft it would store a mark price wrong by a factor of the underlying, an open interest wrong by 10x-10,000x, and would silently disable the catch-up path for the new leg. |
| 8 | **Snapshot `/fapi/v1/fundingInfo` and `/fapi/v1/constituents` daily**, one row per symbol per observed day | feed | +0.08 | +0.24 | Funding interval is no longer uniform: of 789 symbols, 467 are on 4 h, 318 on 8 h, 4 on 1 h. Nothing records what a symbol's interval was on a past date, and the 2026-04-13 cadence change already broke signals crossing it. |
| 9 | **Record the perp contract specs from the `exchangeInfo` call we already make daily** (`binance_perp_contracts`: status, onboardDate, tick/step/minNotional, margin and liquidation-fee schedule, first/last seen) | feed | <0.05 | +0.15 | Forward-only, free, and **the precondition for the breadth lever §7 calls the binding constraint**: per-symbol cost modelling needs each symbol's tick size, step size and min notional *as of the modelled date*. Binance Vision publishes no equivalent (verified: `um/daily/` holds aggTrades, bookDepth, bookTicker, indexPriceKlines, klines, markPriceKlines, metrics, premiumIndexKlines, trades — no contract metadata). Live today: 905 symbols, 773 TRADING / 130 SETTLING / 2 PENDING_TRADING — 130 transitional states that are invisible tomorrow. |
| 10 | **Append each monitor run to `monitor_runs`** instead of overwriting `monitor_last.json` | prod.db | +0.02 | +0.06 | G0 ("monitor, deep scan and backups seen working **for weeks**") is the first gate on the path to real capital and **nothing accumulates its evidence**. `monitor.py:141` overwrites one JSON; the only history is a 6 MB rotating text log that rolls off after ~167 days. The clock on "for weeks" starts when something records it. |
| | **Subtotal** | | **3.9** | **11.7** | |

Then, as a separate decision: **start the collector — staged.** §5.1 A recommends starting the three liquidation
streams this week and holding the depth and Hyperliquid tasks as a later decision with a disk plan attached. The
arithmetic is in §6.3: staged buys **10-23 months** of runway with nothing deleted; all-at-once buys **~3 months**
to a degraded state and ~3.5 to the backups stopping.

### Queue as one-off downloads (cheap, finite, not competing for disk)

- **Binance 5-minute `metrics` archive**, BTCUSDT from 2020-09-01 and ETHUSDT from 2021-12-01 (~90 MB stored for
  both). This single download is the truth series for the item-30 OI defect, the ETH open-interest research
  history SQUEEZE_BULL's ETH leg needs, and the top-trader/taker positioning ratios we have never held.
- **Coinalyze *daily* liquidations and LSR**, to close the `trader.db` gap (its `ca_liquidations` runs 2022-02-28
  → 2026-04-24, 3,034 rows, and that daily series is the ground truth behind the only surviving liqmap result).
  Verified today: daily liquidation history still returns rows 1,000 days back, so this leg is **backfillable at
  leisure** — it is the hourly granularity in item 2 that is forward-only.
- **Binance COIN-M `liquidationSnapshot`**, BTCUSD_PERP, 472 daily files 2023-06-25 → 2024-10-14, a few hundred
  MB. The only historical liquidation-*print* panel obtainable free anywhere. Binance stopped publishing it, so
  it can be retired the way `bookTicker` was.
- **Binance option `EOHSummary`**, 147 daily files 2023-05-18 → 2023-10-23 (~59 MB for BTCUSDT). Per-strike,
  per-hour `gamma`, `mark_iv`, `openinterest_contracts` and `openinterest_usdt`. It is a real dealer-gamma panel,
  it is free, and it is also a dead dataset.
- **Deribit DVOL hourly back to 2021-03-24** (4.3 MB) plus the 532 missing daily rows per asset that our
  `backfill_dvol(days=365*4)` cap cut off.

### Stop caring about these

Retire them on purpose, so nobody re-discovers them:

- **Post-loss rules (item 15).** Even with perfect data the arms sit inside each other's noise (MAR none 7.20,
  skip 8.16, half 8.34; skip lowers DSR 0.726 → 0.461). Answer the objective question, take the bracketed
  default [no rule on either asset], close it. More assets sharpen an estimate nobody should size on.
- **Re-cutting the OKX-gated chento figures (item 14).** Retire the numbers; re-cut only what a named decision needs.
- **ADX and CARRY per-tick diagnostics.** Both read permanently-stored, never-revised inputs, so every evaluation
  since 2019 is recomputable offline. Add a diag file for operator legibility if wanted, not for research.
- **The E7 maker probe.** E2 already bounds the whole maker gain at 0.01-0.06 R per trade.
- **Footprint C3 on alts.** C3 could not pay 18 bp on BTC; alt costs are worse, not better.
- **Buying option-chain history now** (Tardis ~$2,100 for a year of Deribit `options_chain`). Item 1 above plus
  the free `EOHSummary` window plus forward accrual covers the next year; revisit only if a pre-registered gamma
  study is actually scheduled.
- **A second venue's option panel now** (Binance `eapi`, the first draft's rank 9, 5.6 MB/day of rows =
  **16.8 MB/day of disk**, more than the entire rest of the feed programme). It is a cross-check on a study §7
  concedes is a year away. **Park it behind the same condition as the Tardis pull: a scheduled, pre-registered
  gamma study.** Spending operator attention on it now is the same bet as spending $2,100, without the money.
- **dYdX.** $4.0 M/day of BTC volume, 0.04 % of Binance. Recording it costs attention, not bytes.
- **Full-tick `bookTicker` capture** (4.4 GB/day/symbol as packed rows) and **a second venue's L2 depth**
  (Bybit `orderbook.50` measured 2.39 GB/day raw for one symbol). Neither fits, and book imbalance is 0-for-2 here.
- **DVOL at `resolution=1`** (8.6 MB/day, 3.1 GB/yr) — the 1-minute series carries the same information at 1/60th the cost.
- **CME BVX, CoinAPI, Coinalyze-for-options.** BVX needs a licence and is backtested before 2024-04-09, while our
  free Deribit DVOL is live from 2021-03-24. Neither CoinAPI nor Coinalyze publishes any options, IV or option-OI
  endpoint — stated here so nobody checks again. (Coinalyze's *futures* endpoints are a different matter and are
  week-one item 2.)

---

## 2. Forward-only losses, ranked

A day not recorded is a day lost forever. Ranked by what it unblocks x how likely we are to want it. MB/day is
**rows**; multiply by 3 for prod.db disk (§0), by 1 for the collector (microstructure.db is not backed up). The
running total is against **24.17 GB free** (measured today), with the 10.76 GB VM bundle as the available lever.

### 2.1 The ranking

| Rank | Series | MB/day rows | Running disk total | What it unblocks | Cost of waiting |
|---|---|---|---|---|---|
| 1 | **Deribit full option chain** (drop the two filters) | 0.29 | 0.9 | Dealer gamma, pin/expiry effects, the whole vol-surface family | Total, and being paid now. Deribit serves no expired-instrument book: `get_instruments?expired=true` returned 58 instruments, all expiring 2026-09-18 08:00 — today's batch only. |
| 2 | **Coinalyze 1h liquidations + open interest** (16-venue aggregate, `BTCUSDT_PERP.A` today, widenable) | 0.03 | 1.0 | The only cross-venue liquidation series we can have for the past; re-running the liqmap amount validation at better than daily resolution; a live liquidation input at all | **~88 days and counting down one per day** for liquidations, ~80 for OI. Measured today: 1h liq rows at 89 d back, none at 90; 1h OI rows at 80 d, none at 85. Daily is fully backfillable (rows at 1,000 d), so the loss is specifically the intraday granularity. ~28 days are already gone. |
| 3 | **Binance perp contract specs** from the daily `exchangeInfo` pull already made | <0.05 | 1.2 | Per-symbol cost modelling for the alt panel; survivorship-correct universe history; the margin/liquidation-fee schedule as of a date | Total, and accruing. `screener_universe` is `PRIMARY KEY (asset)` with no as-of column and its feed stopped 2026-05-23, so every status transition and filter change since May is already lost. Prices backfill (delisted `FTTUSDT`/`SRMUSDT`/`TOMOUSDT` klines all still list); specs do not. |
| 4 | **A fills record and an income poller**, written from the first live order | 0 today | 1.2 | G4; whether the measured 7-10 bp round trip survives contact; whether CARRY's coupon is real | Absolute but not yet accruing. Binance `/fapi/v1/income` retains three months, so a poller built after go-live loses the first quarter permanently. |
| 5 | **Liquidation prints** (Binance `!forceOrder@arr`, Bybit `allLiquidation`, OKX `liquidation-orders`) — the collector | 10-40 | 41 | Real liquidation maps, Hawkes cascade work, cross-sectional flush asymmetry | Total. `data/futures/um/daily/liquidationSnapshot/` returns **zero keys** and the dataset is absent from the um daily listing entirely. Hyperliquid publishes no liquidations either (its S3 archive is `market_data`, `asset_ctxs`, node fills/trades/events — verified against its own docs). |
| 6 | **Deribit option trade tape** | 2.0 | 47 | Signed option flow by strike with per-trade IV — dealer positioning as it is built, not inferred | **Hard ~24 h**, with **no REST repair path at all** — the tightest deadline here. Measured today: 273 trades in the hour ending 22 h back, 0 in the hour ending 24 h back and at every longer offset. |
| 7 | **Bot tick log** (per-tick status, gate values, whether an Intent was produced, plus a daily status counter) | 0.05 | 47 | Every BACKLOG §4 "revisit at n fires" rule; SQUEEZE_BULL Rule B's OOS count; whether freshness contracts are costing trades | Total. A skipped signal leaves no trace, and replay is unreliable for exactly item 30's reason. |
| 8 | **Feed vintage table** over the re-fetched windows | 0.10 | 48 | Reproducing any study as of its run date; distinguishing a code defect from a silent revision | Total. Live tables are destructive upserts with no as-of column; past vintages are already gone. |
| 9 | **Monitor run log** | 0.02 | 48 | G0's only evidence stream: how many consecutive weeks the fleet, the deep scan and the backups were healthy | Total. `monitor_last.json` is overwritten every run; the rotating text log keeps ~167 days and is not queryable. |
| 10 | **Gate.io `contract_stats`** (BTC+ETH, 5 min) | 0.14 | 48 | Account-count positioning (`long_users`/`short_users`), per-side liquidation USD, OI and three LSR flavours on a $5 B/day venue we hold nothing from | 180 days, then gone. |
| 11 | **Deribit USDC linear chain** | 1.15 | 52 | Option surfaces for SOL, XRP, HYPE, AVAX, TRX — underlyings with no base-settled option market | Total; nothing has ever been recorded. |
| 12 | **Hyperliquid `predictedFundings`** (hourly) | 1.14 | 55 | Cross-venue funding dispersion in one call — HL, Binance and Bybit predicted rates side by side, with each venue's interval | **The HL leg and the per-venue interval metadata only.** The Binance leg backfills to 2019-12-23 from `premiumIndexKlines`, and the Bybit leg to at least 2021-10 (verified today: `premium-index-price-kline` returns rows 1,800 days back). The first draft called this "total" and was wrong. |
| 13 | **Deribit DVOL at 1-minute** | 0.14 | 55 | Intraday move-vs-implied-range (the next study to pre-register) at the cadence a 28-hour bounce actually needs | ~187 days. Measured: `resolution=60` returns rows at 186 d back, empty at 190 d. |
| 14 | **`fundingInfo` + `constituents`, one row per symbol per observed day** | 0.08 | 55 | Structural breaks in any funding or basis series; which spot venues compose the index (MEXC is in it at 6.5 %) | Total; current state only. |
| 15 | **Hyperliquid leaderboard and positions** — the collector | 49-74 | 129 | **Aggregate forced-flow amount and per-account positioning *change*** — the half of the liquidation-map family that survived. The only per-account position data any venue publishes. | The *ranking snapshot* and the per-account positions are total loss; Hyperliquid's S3 archive publishes `market_data` and `asset_ctxs` only. See §2.2 for why the level half of this is **not** the justification. |
| 16 | **Hyperliquid `hl_asset_ctx`** (`metaAndAssetCtxs` every 60 s, whole universe) — the collector | ~39 | 168 | HL open interest, mark, oracle, premium and daily volume per coin | **Recoverable.** Hyperliquid publishes `s3://hyperliquid-archive/asset_ctxs/[date].csv.lz4` (requester-pays, "no guarantee of timely updates"). This is the most backfillable line in the collector and the third largest. See §2.2. |
| 17 | **1-second depth buckets** — the collector | ~75 | 243 | Wall refill/pull at levels where trades turn — the one book question no test has reached | Partial. `bookDepth` archive (+/-1 %, 30 s, from 2023-01-01, 561 KB/day) survives forever; only the second-level resolution and the level structure are lost. |
| 18 | **Binance options panel** (`/eapi/v1/mark` + `/eapi/v1/openInterest`, hourly, BTC+ETH) — **parked** | 5.6 | *(16.8 if taken)* | A second venue's gamma surface, and an independent check on our own Black-76 inversion of Deribit's `mark_iv` | Total. `EOHSummary` died 2023-10-23 and expired instruments vanish from `exchangeInfo`. But it is a cross-check on a study a year away, at 16.8 MB/day of disk — see §2.2. |
| 19 | **MEXC trades and top-of-book** | ~30 | 273 | Whether the fleet's cost model transfers to the venue we may actually trade | Total if the venue decision lands on MEXC — no free archive exists. Gated on decision 6. |
| 20 | **OKX funding** (already running since 2026-06-08) | 0 | 273 | Three-venue dispersion | Already forward-only: OKX's own OI history returned rows at 30 days back and none at 60. `bybit_funding` looks like six years and backfills forever; `okx_funding` is a rolling window we were lucky to catch. |

### 2.2 The five items that deserve argument, not just a rank

**Rank 1 is the only place we are actively destroying data we could have.** `data/sources/deribit.py` keeps
instruments with expiry <= 90 days and |ln(K/S)| <= 0.30. Re-implementing that predicate against the live chain
today: of 944 BTC instruments we keep 524, holding **51.3 %** of BTC open interest; of 856 ETH instruments we
keep 468, holding **39.4 %**. The discarded half is not noise. Sorting BTC instruments by open interest, four of
the top twelve are `BTC-25DEC26-...` at 98 days — dropped for being eight days past the cut:

```
BTC-25SEP26-70000-C   OI 10,942    7d  kept
BTC-25DEC26-80000-C   OI  9,014   98d  DROPPED
BTC-25DEC26-60000-P   OI  6,249   98d  DROPPED
BTC-25DEC26-100000-C  OI  5,916   98d  DROPPED
BTC-25DEC26-120000-C  OI  5,779   98d  DROPPED
```

That is the quarterly-expiry concentration the gamma hypothesis is *about*. BACKLOG §5 item 2 records the
historical loss correctly and then says gamma is "testable in a year or two" — but under the current filter it is
not testable in two years either, because the ladder being accrued has its wings cut off. The cost of the fix is
286 KB/day of rows, 0.9 MB/day of disk. Nothing else in this document has that ratio.

One thing it does **not** need: Binance's greek fields. Deribit's `get_book_summary_by_currency` returns
`mark_iv`, `underlying_price` and `open_interest` per instrument (verified today), and strike and expiry parse
out of the instrument name, so gamma is a Black-76 inversion on data we would already be storing. Rank 18 is a
cross-check and a second venue, not the primary route — which is why it is parked (below).

**Rank 2 is the loss we were taking without knowing it.** The first draft mentioned Coinalyze only to dismiss it
for options and never noticed that **the fleet has had no liquidation series at all since 2026-06-10**:
`cd_liquidations` stops there (CoinDesk 401, §3.3) and `ca_liquidations` was frozen as a "one-shot (30d-retention
source)" on 2026-05-24 — a retention figure that is wrong by a factor of three. Measured today with the key
already in `.env`: 1h liquidation history returns rows 89 days back and none at 90; 1h open interest returns rows
at 80 days and none at 85; daily returns rows 1,000 days back. `GET /v1/future-markets` returns 5,429 markets,
26 BTC perps across 16 exchange codes, so this is a genuine cross-venue aggregate, not a Binance duplicate.
`fetch_coinalyze.py` already implements the client (`liquidation-history`, `INSERT OR REPLACE`,
`_record_unfillable`). The whole thing is one command today and a `feed.py` catch-up thereafter, for 2 KB/day.
The stake beyond the blackout: `studies/notebooks/exit_policy_2026_09/liqmap_run.py::read_ca_liquidations` is the
ground truth behind the only surviving result of the just-concluded liqmap study (range-controlled rho 0.572 /
0.555 for the estimated amount series against measured, versus 0.363 / 0.283 for a naive proxy). For any month we
do not record, that validation can only ever be re-run at daily resolution.

**Rank 15's value is the amount, not the level.** The first draft's "what it unblocks" column read "whale
liquidation levels as magnets or exits" — the exact question
`studies/notebooks/exit_policy_2026_09/findings_liqmap.md` dissolved three days earlier. Its §4: the actual map's
cluster sits **nearer price than the control map's in 64.8 % of states**, and matching on distance takes the
touch effect from +2.54 pp (+1.78, +3.33) to **+0.10 pp (−0.09, +0.30)** on BTC and +0.09 pp on ETH; the turn is
null on BTC and wholly negative on ETH (−6.84 pp, CI −12.90, −1.01); §6 says "do not re-propose". Real whale
liquidation levels carry that confound **harder**, not less: a level is a mechanical function of leverage, so the
largest positions produce the nearest levels — precisely the geometry that produced the false positive. What
survived liqmap was the *amount* series, and that is what rank 15 is for: aggregate forced flow and per-account
positioning change. liqmap §6 does name "venue-published liquidation levels" as a legitimately different input,
so the family is not dead — but any level use must carry a **distance-matched primary test**, which is liqmap's
own closing instruction ("matching on distance belongs in the primary test, not in §7").

**Rank 16 is 39 MB/day hiding inside rank 15's label, and it is the one line I would cut.** The first draft
folded `hl_asset_ctx` into "Hyperliquid leaderboard and positions" (39 + 40-65 + 9 = 88-113 MB/day), so a third
of that budget was spent under a name that did not describe it. It is also the **most recoverable** item in the
collector: Hyperliquid publishes `asset_ctxs` as daily S3 files (requester-pays), while it publishes no
liquidations and no per-account positions at all. So the document's own headline distinction points the other way
for this line. Meanwhile rank 12 spends a full argument and a DDL block on the same venue's `predictedFundings`
at 1/34th the cost. **Priced option for the operator: 60 s → 300 s cadence saves ~31 MB/day, about 15 % of the
whole collector, and no study named anywhere in this document needs HL open interest or mark at 60-second
resolution.** BACKLOG item 9 records a preference to keep the full sets, but it was formed against "roughly three
months" of runway; §6.3 shows that is three months to a *degraded* state and ~3.5 to the backups stopping, so the
premise has changed and the option should be re-put rather than assumed.

**Rank 17 is the largest line item and has the weakest prior.** depth_1s is ~75 MB/day — a third of the
collector — and coarse book data is 0-for-2 here: imbalance failed as a predictor (|t| < 2) and as exit
information (E3, Holm p 0.83, with the sign pointing the wrong way at +0.22 R). The genuinely new quantity is
refill/pull **at a wall**, which the current 1-second bucket schema cannot express: a wall hit and refilled inside
one second is invisible, and refill is the whole signal. If depth_1s is worth 75 MB/day, it is worth the extra
per-bucket counters that let it answer the question it is being collected for — but they must be **scoped to the
wall**, not per-side totals, and the resolution they buy is **100 ms, not per-event** (§5.1 item C).

**Rank 18 is parked, not scheduled.** 5.6 MB/day of rows is 16.8 MB/day of disk — more than the rest of the feed
programme put together — plus 528 `eapi` calls/day, a new table, a contract, a `check_gaps` Spec and a monitor
alert path, against a constraint stated as "23 GB of free disk and one person's attention". This document already
parks the $2,100 Tardis pull behind "a pre-registered gamma study is actually scheduled". Rank 18 is the same bet
paid in attention instead of money, so it gets the same condition.

### 2.3 Running total and what it costs

The ten week-one items total **3.9 MB/day of rows = 11.7 MB/day of disk**. The full §5.2 programme (adding DVOL
1-minute, the ETH OI writer and the expiry-day poll, with rank 18 parked) is **4.4 MB/day of rows = 13.2 MB/day
of disk**; with prod.db's existing 0.51 MB/day of growth the feed side spends **~14.7 MB/day of disk = 5.4
GB/year**. That is not nothing — it is roughly four years of headroom on its own — but **the entire disk question
is still the collector**, at 170-230 MB/day. §6.3 does that arithmetic with dates.

Note what the 3x multiplier changes: rank 18 alone was 55 % of the feed programme's disk. Parking it roughly
doubles the feeds-only runway. The first draft's "at that rate the drive lasts years — not a disk question at
all" was right in conclusion and wrong in arithmetic; the corrected version is in §6.3.

---

## 3. Backfillable, so not urgent

A public archive or a vendor will still sell these in a year. **They should not compete for disk or effort with
§2.** Listed so the distinction is on the record and nobody treats a download as a loss.

### 3.1 Free, still served, verified today

| Source | Span | Size | Note |
|---|---|---|---|
| Binance Vision `um/daily/metrics/<SYM>` | BTCUSDT 2020-09-01 → 2026-09-17 (2,208 files); ETHUSDT 2021-12-01 → (1,752) | 11.4 KB/day/symbol zipped; ~51 MB stored per symbol-history | 288 rows/day: `sum_open_interest`, `sum_open_interest_value`, `count_toptrader_long_short_ratio`, `sum_toptrader_long_short_ratio`, `count_long_short_ratio`, `sum_taker_long_short_vol_ratio`. The REST siblings serve ~30 days; the archive keeps the same series forever at 5 minutes. **The highest-value backfill on the list.** |
| Binance Vision `um/daily/bookDepth/<SYM>` | 2023-01-01 → 2026-09-17 (1,353 files) | 561 KB/day | +/-1 % cumulative depth, 30 s snapshots. Coarser than depth_1s in both dimensions but permanent. |
| Binance Vision `um/{daily,monthly}/aggTrades`, `trades`, `klines`, `markPriceKlines`, `indexPriceKlines`, `premiumIndexKlines` | trades from 2019-09-08, klines 2019-12-31, mark/index/premium 2019-12-23 | aggTrades 12.2 MB/day/symbol zipped | Stream-and-discard: keep the aggregates, never the raw zips. `premiumIndexKlines` reconstructs the continuous predicted-funding series short_squeeze's parked cadence-fidelity question needs, **and it is the Binance leg of rank 12**. Delisted symbols survive: `FTTUSDT`, `SRMUSDT`, `TOMOUSDT`, `ANTUSDT`, `BTSUSDT`, `SCUSDT`, `RAYUSDT` all still list 1h klines, so an alt panel is survivorship-correct on *prices*. It is not survivorship-correct on *specs* — see §2.1 rank 3. |
| Bybit `v5/market/premium-index-price-kline` | rows at 1,800 days back (2021-10) | trivial | The Bybit leg of rank 12's predicted-funding spread. Verified today at 30/365/1,000/1,800 days, `retMsg` OK. |
| Binance Vision `cm/daily/liquidationSnapshot/BTCUSD_PERP` | 2023-06-25 → 2024-10-14, 472 files | last file 1.3 KB | Per-print liquidations with ms stamps, order side, fill quantity. Inverse BTC, but correlated with USD-M flow and it covers the 2024 high and the August 2024 unwind. **Dead dataset — pull it once.** |
| Binance Vision `option/daily/EOHSummary/<UND>` | 2023-05-18 → 2023-10-23, 147 files | 402 KB/day | Per-strike, per-hour: strike, `mark_iv`, `delta`, `gamma`, `vega`, `theta`, `openinterest_contracts`, `openinterest_usdt`. 5,498 rows in the 2023-10-23 BTCUSDT file. **Dead dataset — pull it once.** |
| Binance Vision `option/daily/BVOLIndex` | 2023-06-20 → 2026-09-17, 1,160 files each for BTC and ETH | 434 KB/day/asset | 1-second implied-vol index. Downsampled to 1 minute the full history is ~137 MB. A second venue's IV against Deribit DVOL. |
| Coinalyze **daily** liquidations, OI and LSR | rows at 1,000 days back (2023-12) for liquidations, 1,500 for LSR | trivial | Fully backfillable, unlike the intraday series in rank 2. Closes `trader.db ca_liquidations`' 2026-04-24 → today gap at leisure. |
| Bybit `v5/market/open-interest` | rows at 2020-09-09, empty at 2300 days back | 13 KB/day/symbol | 5-minute OI, six years, free, unauthenticated. The independent second-venue OI series `cd_open_interest` never had. |
| Bybit `public.bybit.com/trading/<SYM>/` | BTCUSDT 2020-03-25 → 2026-09-17 | 58 MB/day/symbol gzipped | Tick trades. Ingest as 1-second aggregates (~5 MB/day/symbol), never raw. |
| OKX `static.okx.com` trade records | BTC-USDT-SWAP 2021-10-01 → | 21 MB/day zipped | Same treatment. |
| Hyperliquid `s3://hyperliquid-archive/asset_ctxs/[date].csv.lz4`, `market_data/`, and `hl-mainnet-node-data` fills/trades/events | requester-pays | unpriced | Verified against Hyperliquid's own historical-data page. **No liquidations and no per-account positions are published** — which is exactly why rank 15 is forward-only and rank 16 is not. |
| Deribit DVOL at `1D`/`43200`/`3600` | rows at 2000 days back (2021-03-28), empty at 2100 | 2.6 KB/day ongoing | Hourly is fully backfillable to the index origin; only the 1-minute and 1-second series are rolling. |
| Deribit `get_tradingview_chart_data` on expired instruments | works back to at least 2019-12 | — | **Mark-price OHLCV only.** Verified key set: `['close','cost','high','low','open','status','ticks','volume']` — no open interest, no IV. This is why `trader.db cd_options_oi` is misnamed, and why no free route to historical per-strike OI exists. |
| Kraken Futures `history/v3/market/<SYM>/executions` | >= 1,000 days | 14 MB/day | Publishes maker *and* taker order type per execution — unique. But Kraken is $0.59 B/day: a detail source. Its `historicalfundingrates` is a rolling 12 months, so that leg is forward-only. |
| Coinbase INTX REST | BTC-PERP daily candles from 2023-08-30; hourly funding pages to 2023-11 | 115 KB/day | Verified unauthenticated today (`/instruments/BTC-PERP/quote` returns best bid/ask, index, mark, settlement). $4.9 B/day — Bybit-sized and we hold nothing. Its *websocket* requires a CDP key. |
| CME `BTC=F` / `ETH=F` dailies | BTC=F from 2017-12-18, ETH=F from 2021-02-05 | 180 B/day | Two entries in `macro_yahoo.SYMBOLS`; the whole backfill is ~325 KB. |
| `^VVIX`, `^SKEW`, `^VIX9D` | 2015 and earlier | 270 B/day | Same code path. Note the survey's negative: `^MOVE` is mis-mapped on Yahoo (returns a Northern Trust bond fund) — do not ingest it from there. |
| CoinMetrics community API | BTC/ETH daily from 2011-04-24, no key | 250 B/day | `cm_daily_metrics` is frozen at 2026-05-24 because `refresh_all` never calls it. Free to restart, low prior — the Q2-2026 memo steers away from slow daily aggregates. |
| CFTC Commitments of Traders, CME Bitcoin futures | weekly from Dec 2017 | trivial | ~450 observations of a slow state. A regime or sizing input beside the retail ratio, never a trigger. |

**Coinbase spot tick tape is not on this list, and the first draft wrongly implied it was.** Coinbase publishes no
free bulk trade archive. The only free route is cursor-paging `api.exchange.coinbase.com/products/BTC-USD/trades`,
which returns 1,000 rows a page against a newest `trade_id` of 1,094,694,878 today — ~1.09 **million** sequential
requests for one product. The vendor routes (Coinbase Data Marketplace / Coinbase Institutional, Amberdata) are
paid. We already hold `coinbase_spot_1h` (117,694 rows, 2020-01-01 → live), so the hourly leg is covered; at tick
resolution Coinbase is a **forward-only capture decision like MEXC**, not a download. Nobody should schedule it as
one.

### 3.2 Paid, priced

- **Tardis.dev Deribit `options_chain`** — per-strike OI, IV and greeks, tick granularity, from 2020-03-01.
  Options plan $350/mo Academic, $700 Solo, $1,000 Pro, $3,000 Business; history depth is set by billing
  interval (quarterly = 12 months, yearly = 4 years). Practical entry: Solo quarterly, ~$2,100 for 12 months.
  **Free tier: the first day of every month downloads with no key** — roughly 80 monthly cross-sections back to
  2020-03, enough to pre-register and pilot a gamma study before paying anything. That is the right first step
  if gamma is ever scheduled.
- **Amberdata, Kaiko, Laevitas** — contact-sales, no public figures. Amberdata claims Deribit options from
  2018-08-13, deeper than Tardis. Recorded as unpriced, not estimated.

### 3.3 Not obtainable, stated so nobody re-checks

- **Binance USD-M `liquidationSnapshot`**: zero keys, dataset absent from the um daily listing.
- **Binance perp contract metadata of any kind from the archive**: `data/futures/um/daily/` lists exactly
  aggTrades, bookDepth, bookTicker, indexPriceKlines, klines, markPriceKlines, metrics, premiumIndexKlines,
  trades. No exchangeInfo, no filters, no margin schedule, at any date. This is why rank 3 exists.
- **Binance spot book history of any kind**: `data/spot/daily/` contains exactly `aggTrades`, `klines`, `trades`.
  No spot bookTicker, bookDepth, metrics or liquidations, at any date.
- **Binance USD-M `bookTicker`**: published 2023-05-16 → 2024-03-30 only (320 files, 87.8 MB/day), then killed.
  Everything since 2024-05-01 is unrecoverable from Binance.
- **Deribit expired-instrument OI and IV**: none, confirmed twice today.
- **Deribit option trades older than ~24 h**: none, confirmed today at 24/26/28/30/36/48 h. There is no repair
  path of any kind for this tape.
- **Hyperliquid liquidations and per-account positions**: not published in either S3 bucket, per its own docs.
- **Coinbase free bulk tick archive**: does not exist (see the note above §3.2).
- **CoinDesk `data-api`**: HTTP 401 "API key required" on both `/futures/v1/historical/liquidation/hours` and
  `/index/cc/v1/historical/days`. `cd_liquidations` and `cd_dvol` cannot be re-enabled or backfilled as the code
  stands, and the 2026-04-13 → 2026-09-06 hole in `deribit_options_daily` cannot be closed by re-seeding.

---

## 4. Blocked questions, mapped

Bucket key: **FO** forward-only (§2), **BF** backfillable (§3), **NB** not blocked on data at all,
**HARD** no source at any price.

### 4.1 Covered by this plan

| Question | Unblocking data | Bucket | Covered |
|---|---|---|---|
| Do large-gamma strikes act as pin/reversal levels? | Full-chain OI + IV per strike, forward | **FO** | Yes — §1 item 1. Testable in ~1 year, or this quarter via the Tardis free monthly cross-sections. |
| Does price pin toward max-gamma on expiry days and release at 08:00 UTC? | Full chain at hourly cadence on Thu/Fri | **FO** | Partly — §5.2 adds the expiry-day extra poll. |
| Is SJ-4250 an artefact of the one-bar-stale OI stamp (item 30)? | Binance 5-min `metrics` archive as the truth series | **BF** | Yes — §1 queue. Also settles whether SJ-4250 counts toward the n = 20/30 re-cut (decision 8). |
| Does SQUEEZE_BULL replicate on ETH (also top-anatomy stage B's holdout)? | ETHUSDT 5-min OI archive from 2021-12 + a live ETH OI writer | **BF** + **FO** (writer) | Yes — same download; the writer is ~5 KB/day. |
| Does a 5-minute flush detector change SQUEEZE_BULL's fire set (BACKLOG 30)? | Same archive | **BF** | Yes. |
| Retail accounts vs top-trader positions as an exhaustion divergence | `count_toptrader_long_short_ratio`, `sum_toptrader_long_short_ratio`, `count_long_short_ratio`, `sum_taker_long_short_vol_ratio` at 5 min | **BF** | Yes — same files. `anatomy_data.py` already parses these four columns. |
| Is the B5 LSR gate better at 5 min than at the daily stamp? | Same | **BF** | Yes. |
| Has SQUEEZE_BULL Rule B produced its 10 OOS fires? | Per-tick rule and gate log | **FO** | Yes — §1 item 3. |
| What fraction of ticks end in a non-trading status, and do declined signals have the same expectancy? | Same, **plus a daily per-(bot, variant, status) counter row** | **FO** | Yes — the sparse event rows alone have no denominator (absence is ambiguous between "ok", "runner down" and "crashed before the log call"), and `bot_heartbeats` cannot supply one because it is overwritten. §5.2 item 8 adds the counter. |
| Which closed trades were booked while the fleet was healthy? *And: how many consecutive weeks has the fleet been healthy (G0)?* | Append-only monitor-run rows | **FO** | Yes — §1 item 10, promoted to week one. |
| Was the value the same when the bot read it? | Vintage table | **FO** | Yes — §1 item 5. |
| Move vs implied range, measured intraday | DVOL at 1-minute | **FO** (rolling 187 d) + **BF** (hourly to 2021-03) | Yes — §5.2 item 4, and the hourly backfill. |
| Cross-venue funding dispersion including a thin venue | HL `predictedFundings`; Gate `contract_stats.last_funding_rate` | **FO** (HL leg + intervals) / **BF** (Binance and Bybit legs) | Yes — ranks 10 and 12. |
| Was there any liquidation activity at all between 2026-06-10 and today? | Coinalyze 1h liquidation history, run **now** | **FO** (~88-day window) | Yes — §1 item 2, and ~29 days of it are already unrecoverable. |
| Per-symbol cost model as of a past date; a survivorship-correct alt universe | `exchangeInfo` contract specs, accumulated forward | **FO** | Yes — §1 item 9. Without it, an alt panel built next year is uncorrectably biased on the symbols that delist between now and then. |
| Does aggregated cross-venue CVD separate real tops? | Bybit + OKX tick archives, bucketed to 1 min | **BF** (Bybit, OKX) / **FO** (Coinbase tick) | Data yes for Bybit and OKX, work no — queue behind the §1 items. The Coinbase leg is **not** a download: ~1.09 M paged REST requests per product or a paid vendor (§3.1 note). |
| Does absorbing-order *size* separate absorption that stops a move? | Binance aggTrades archive from 2019-12-31 | **BF** | Data yes. Prior is poor (0-for-3); frame it as a well-powered burial. |
| Do stops gap through inside a bar during a cascade? | Raw `trades` archive around the 176 historical stop exits | **BF** | Data yes, and it is nearly free — a few hundred daily zips. |
| Short_squeeze funding-cadence fidelity | `premiumIndexKlines` 1m from 2019-12; **and `trader.db fr_minutes`, 2,022,485 rows 2022-06 → 2026-04, already on this machine** | **BF** | Data yes. The stated blocker ("no continuous funding series") is simply not true. |
| Do per-alt liquidation cascades show the BTC asymmetry? | Bybit `allLiquidation` widened to the linear universe; OKX already all-SWAP; Coinalyze 1h as the aggregate cross-check | **FO** | Yes — §5.1 item B and §1 item 2. |
| Can alt liquidations be normalised at all? | 1h klines + funding + hourly OI for the collector's symbols | **BF** | Data yes (~120 KB/day). Without it the collector produces a pile, not a dataset. |
| Aggregate forced-flow amount and whale positioning *change* as exit information | HL leaderboard + positions | **FO** | Collector, if started. **Not** "whale levels as magnets" — see §2.2 rank 15. |
| What are each closed trade's MFE and MAE? | **Nothing.** `btc_1m`/`eth_1m` run 2020-01-01 → now, 3,529,374 rows | **NB** | Not blocked. The BACKLOG's "confirm retention first" caveat is answered — retire it from the blocked pile. |
| Which hourly BTC perp close series is correct (`cd_futures_ohlcv` vs the 15m reconstruction)? | Binance 1h kline archive for the 733 disputed hours | **BF**/**NB** | Data yes, one day of work. High value per cost: `cd_futures_ohlcv` is a live-read table with a 3600 s contract, so an error in it reaches decisions. |
| Do the OKX-gated chento figures survive re-cutting? | Nothing — both pools reconstruct | **NB** | Retire the figures (decision 2). |

### 4.2 Stays blocked, and that is the right answer

| Question | Why | Verdict |
|---|---|---|
| VRP strangle at OOS n >= 6 | OOS accrues only from 2026-09-06; the 2026-04-13 → 09-06 hole cannot be closed | Wait. DVOL-minus-realised went +6.4 vol points in 2025 to +0.2 in 2026 — the edge source has gone. Close it at the pre-registered point; do not pay for history. |
| Hawkes self-excitation in liquidations | S1 LIQ = 0.286 years, 125 events against gates of >= 2 years and >= 500 events | Collector, then >= 2028-06. Its honest use is slippage-burst timing for G4, not a sleeve — a fitted intensity is direction-free by construction. **It is also the clearest case for §5.1's `stream_coverage` table**: an hour with no rows and an hour with no events are the same row count, and a fitted intensity cannot tell them apart. |
| Whale liquidation *levels* as magnets | Dissolved by `findings_liqmap.md` §4 on estimated levels; real levels carry the leverage-to-distance confound harder | Do not re-propose without a distance-matched primary test. |
| Chento's 72-hour time stop | Every historical year is spent; walk-forward picked a different arm in each of four folds | Only a forward paper twin settles it, at 60-80 paired fires = 2-4 years. It costs exposure while it runs (peak concurrency BTC 5→6, ETH 6→8; ETH drawdown 21 %→43 %), so it needs item 13's budget first. |
| Squeeze paired re-cuts at n = 20/30 | Fires cannot be manufactured | 6-18 months. Settle item 30 first, or the count mixes two signal definitions. |
| Queue position and true maker fill probability | No public dataset contains our own orders | Stays blocked; see the "stop caring" list. |
| Realised funding vs modelled | No account | Build the income poller on day 1 of live; Binance retains three months. |
| MEXC cost model | No free archive comparable to Binance Vision | Gated on decision 6. Recording forward costs ~30 MB/day and makes the decision reversible. |
| Reproducing every past study's input state | Vintages are already gone | The vintage table stops the loss continuing; it cannot undo it. The audit's "reproduce the affected baselines" step reproduces today's data, and a match proves less than it appears to. |

### 4.3 Not worth unblocking

Post-loss rules · OKX-gate re-cuts · ADX/CARRY tick diagnostics · E7 · footprint C3 on alts · the calendar cells
(QTR_END 26 observations in 6.7 years, HIGH52 38, EMA_ETH 11 — the double-era t >= 2.5 test was close to
unpassable before the first price was read) · the Bybit perp price gap (repairs a diagnostic in a study already
concluded KILL — worth mentioning only as the cleanest example of a live table that stopped with no alert) ·
whale absorption revival (six phase-1 files, all `gate_pass=false`, zero candidates).

---

## 5. What to add

Most of the value is **not** in the collector. Eight of the ten week-one items are `feed.py` writers into prod.db
with freshness contracts. The collector gets a staged start and six changes. Both sections match the shapes
already in the repo.

### 5.1 Collector: start it staged, and fix five things first

The collector is a fleet unit with a `collector` heartbeat, a single-instance guard keyed on that row, a 60 s
status file, a batched `store.Writer` with one `INSERT OR IGNORE` per table, and a disk pause that drops only
`store.DROP_FIRST_TABLE = "depth_1s"` below 5 GB free (resume above 8 GB). Every addition below keeps that shape:
a table in `store.DDL` with an explicit primary key and a `received_ms` column, a matching entry in
`store.INSERT_SQL`, a coroutine in the `tasks` dict in `collector.run()`, and — for a stream — an `ALLOWANCE_S`
entry so silence degrades the heartbeat.

**A. Start it staged: the three liquidation streams this week, the rest as a later decision.**
The first draft said "start it, before any extension". That inverts this document's own ranking: liquidation
prints are rank 5 at 10-40 MB/day, while the Hyperliquid set (ranks 15-16) and depth_1s (rank 17) are ranks
15-17 at 163-188 MB/day — i.e. starting everything spends 80-90 % of the bytes on the three items ranked last.
The arithmetic nobody had computed (full working in §6.3, against 24.17 GB free and `backup.py`'s 3.30 GB floor):

| Start | Disk rate | Backups stop |
|---|---|---|
| `binance_forceorder` + `bybit_liq` + `okx_liq` + `okx_instruments`, plus the whole feed programme | 30-70 MB/day | **10-23 months** |
| Everything | 185-245 MB/day | **~3.5 months** (depth pause at ~3 months) |

So the single irreplaceable series in this entire document — the one §7 concedes may well be null but cannot be
bought back — buys **over a year** of runway with no VM-bundle deletion, no retention policy and no disk decision
attached. Bundling it with ranks 15-17 cuts that to three months and forces the disk decision, which is plausibly
what has stalled the go-ahead since 2026-09-17.

`collector.py:403-414` has only `--once-seconds`, `--db`, `--no-heartbeat`, `--force-start` and `--verbose`, so
staging needs a `--sources` / `--skip` filter over the `tasks` dict in `collector.run()` — about ten lines and one
test, not a redesign. Recommended sequence: land items A1-A5 below, add the filter, delete `b.csv.gz`, start the
liquidation streams. Free the 10.76 GB VM bundle whenever convenient; it is a lever, not a precondition, once the
start is staged.

**A1. Land every schema change before the first start.** `store.ensure_schema()` executes only the
`CREATE TABLE IF NOT EXISTS` statements in `DDL`, and there is no `ALTER TABLE` anywhere in `data/sources/micro/`
(`grep` finds it only in `botlib.py:209`, `strategies/support/trade_db.py:123` and the 2026-05-18 migration). So
adding a column to `DDL` **after** the database exists is a silent no-op on the file while `INSERT_SQL` names the
new column — raising `sqlite3.OperationalError: table depth_1s has no column named ...`. That is not a busy error
(`_is_busy` matches only "locked"/"busy"), so `Writer._write` takes the generic branch, rolls back the **whole**
transaction and counts every row in it as lost. A depth row is enqueued every second per symbol, so essentially
every batch contains one — and 100 % of the liquidations, `hl_positions` and `hl_asset_ctx` rows in those batches
would be discarded permanently. The heartbeat would say `degraded`, not `error` (`derive_status`: only a dead task
yields `error`), and `monitor.py:497` would surface it as a dashboard line reading "writer: +N flush failures".
**Either land item C's columns before the first start, or ship a guarded migration in `store.ensure_schema`
(`PRAGMA table_info` + `ALTER TABLE ... ADD COLUMN`, the `botlib.py:209` pattern).**

**A2. Make one table's failure survivable.** Independently of A1: commit per table in `Writer._write` (or retry
the batch minus the failing table) so a schema or constraint error in one table can never destroy the
irreplaceable one, and raise a per-table flush failure to heartbeat **`error`**, not `degraded`.

**A3. Record the collector's own coverage.** `store.py`'s DDL has exactly one operational table,
`collector_runs(started_ms PK, pid, version, note)` — process starts only. Every coverage counter is in-memory
and process-scoped (`Writer.dropped`, `Writer.dropped_disk`, `flush_failures`, `wsclient.WsStatus.reconnects`),
surfaced only through the 60 s status file, which is overwritten. For a dataset whose headline uses are all event
*rates* — Hawkes intensity, liquidation climax, wall refill counts — **an hour with no rows is indistinguishable
from an hour with no events, and that ambiguity is itself forward-only.** §6.3 plans for a multi-week silent
thinning of depth_1s with no row anywhere saying which seconds were dropped. The repo already holds the opposite
doctrine for prod.db (`data/known_unfillable.json` + `health._gap_is_unfillable()`), so:

```sql
CREATE TABLE IF NOT EXISTS stream_coverage (
    venue TEXT NOT NULL, stream TEXT NOT NULL, bucket_start_ms INTEGER NOT NULL,
    connected_s REAL, msgs INTEGER, reconnects INTEGER, dropped_rows INTEGER,
    depth_paused INTEGER, received_ms INTEGER,
    PRIMARY KEY (venue, stream, bucket_start_ms));
```

Written once a minute from the same `Writer.stats()` / `WsStatus` values that already feed the status file, reset
per bucket. *Cost: ~8 tasks x 1,440 x ~60 B = 0.7 MB/day, 0.35 % of the collector; 0.14 MB/day at 5-minute
buckets.* **Decide this before the collector starts: every day it runs without it is a day whose quiet minutes can
never be labelled.**

**A4. Add two lower disk thresholds, and stop calling the depth pause a floor.** `DROP_FIRST_TABLE` pauses
depth_1s only; store.py's own docstring says "every other table keeps recording", at 98-153 MB/day. From the 5 GB
pause line to `backup.py`'s 3.296 GB refusal threshold is 1.704 GB — **10 to 16 days**, after which prod.db stops
being backed up. And `should_pause_depth` with `RESUME_FREE_BYTES = 8e9` means the pause never lifts while free
space is falling, so its 75 MB/day saving is one-shot while the other 125 MB/day runs on. Add a second threshold
at ~3.5 GB that stops **everything except `liquidations`** (the only truly unrecoverable table) and a hard stop
for all tables at ~2.5 GB, both reported as heartbeat **`error`**.

**A5. Fix the `liquidations` primary key before it holds a year of rows.**
`PRIMARY KEY (venue, symbol, ts_ms, pos_side, price, qty)` with `INSERT OR IGNORE`. Bybit's `allLiquidation`
frames carry no sequence or id and `liquidation_row()` writes `extra = None`, so two accounts liquidated in the
same millisecond at the same bankruptcy price with the same size collapse into one row. On BTC/ETH sizes are
near-continuous and this is rare; on the ~390 alts item B adds, minimum order sizes are integral and bankruptcy
prices cluster — precisely the cascade case the widening is for. The loss leaves no trace (`INSERT OR IGNORE`
reports nothing; `_on_written` counts only `con.total_changes`). Either add a per-frame ordinal to the PK
(`seq INTEGER`, assigned by the parser within a frame) or move the count into an `n_prints` column incremented on
conflict. **A later migration means rebuilding the one table no vendor can re-sell.**

**B. Widen the Bybit liquidation symbol list — as a change to the collector, not a constant edit.**
`bybit_ws.DEFAULT_SYMBOLS` is a hardcoded 12-tuple; Bybit lists ~839 linear perps and `allLiquidation` is sparse
on alts. Topic sizing is verified: 400 topics = 10,434 chars and all 883 linear instruments = 23,346 chars
against Bybit's documented 21,000-character `args` limit, so "top ~400" fits on one connection and
`subscribe_messages()` already batches at 10 per frame. But "take the top 400 by turnover" is four pieces of
machinery, three of whose failure modes are silent:

- There is **no turnover source in the repo**. A top-400 list needs a tickers poller with persistence and a
  readiness gate — the `_okx_instruments_loop` + `okx_instruments` apparatus, mirrored as `bybit_instruments`.
- `collector.py` evaluates `bybit_ws.subscribe_messages()` **once at startup** and `wsclient._session` re-sends
  exactly that list on every reconnect. A refreshed universe takes effect only on process restart. Either add a
  resubscribe-on-list-change path or accept restart-only **and say so**.
- A rejected batch is a log line only: `parse_message` logs `bybit subscribe rejected` when `success is False`
  and returns `[]`; nothing touches `StreamStatus.errors` or the heartbeat. Ten of 400 symbols disappearing is
  invisible, and routine once the list churns with delistings. **Count it into `StreamStatus.errors` and the
  heartbeat note.**
- `ALLOWANCE_S["bybit_liq"] = 60 * 60` was calibrated for 12 sparse symbols. At the measured ~1.3 rows/s across
  400, an hour of tolerated silence hides a half-open socket for an hour. **Drop it to minutes in the same
  commit.**

*Cost: the survey measured 115 rows in 90 s across 400 symbols, ~110k rows/day. At ~145 B/row including the
composite-PK autoindex and `ix_liquidations_ts` that is ~16 MB/day for the whole linear market — an increment of
roughly +5 to +15 MB/day over the current 12 symbols, not the +3 the first draft stated. The 90-second
measurement was not re-verified here.*

**C. Add replenishment counters to `depth_1s` — scoped to the wall, at 100 ms resolution.**
The first draft proposed `bid_refill_qty`, `ask_refill_qty`, `bid_events`, `ask_events`. Those are **per-side
totals**, in which a refill at the wall and a refill 3 % away are the same number — so they cannot answer the wall
question §2.2 says they are being bought for. `book.py` already localises the wall (`find_wall()`: largest level
within +/-1 % of mid, stored as `wall_bid_px`/`wall_bid_qty`), so scope the counters to it:
`wall_pull_qty`, `wall_refill_qty` keyed to the sampled `wall_*_px`, plus one near-mid bucket pair. Second and
unavoidable: the stream is `<sym>@depth@100ms` (`binance_ws.DEPTH_URL_BASE`), whose events carry the **net** change
per 100 ms window, so a pull-and-replace inside one window nets to zero. **This buys 100 ms resolution, not
per-event resolution** — the hypothesis is tested at that granularity or not at all. *Cost: ~6 REALs on an
existing row, ~+48 B x 172,800 rows/day = +8 MB/day (+11 % of depth_1s).* Subject to the disk pause like the rest
of the row, and **it must land before the first start** (A1).

**D. Hyperliquid `predictedFundings`, hourly.**

```sql
CREATE TABLE IF NOT EXISTS hl_predicted_funding (
    ts_ms INTEGER NOT NULL, coin TEXT NOT NULL, venue TEXT NOT NULL,
    funding_rate REAL, next_funding_ms INTEGER, interval_hours INTEGER,
    received_ms INTEGER,
    PRIMARY KEY (ts_ms, coin, venue));
```

One `POST /info {"type":"predictedFundings"}` per hour (63.8 KB, weight 20 against a 1,200/min budget), keyed to
the hour grid by `hyperliquid._bucket_ms` like the existing pollers, with
`ALLOWANCE_S["hl_predicted_funding"] = 2 * 3600`.
*Measured today: 234 coins, 702 legs, of which **633 are non-null** (BinPerp / HlPerp / BybitPerp). 633 x 24 =
15,192 rows/day x ~75 B including the PK autoindex = **1.14 MB/day**, not the 0.84 first stated.*

**E. Keep a bounded slice of the leaderboard — and drop the stated rationale.**
`hyperliquid.LEADERBOARD_TOP = 300` discards 45,648 of 45,948 rows from a 38 MB download that already happens
daily. Two corrections. *Cost:* a row is snapshot_day TEXT(10) + address TEXT(42) + rank + five REALs +
received_ms ~115 B, **plus** the `sqlite_autoindex` that `PRIMARY KEY (snapshot_day, address)` creates on a
rowid table (~65 B) = ~180 B; 45,648 extra rows/day = **~8.2 MB/day = 3.0 GB/year**, not 4.1, in the database
`backup.py` does not copy. *Rationale:* "we track the largest accounts and a cascade is driven by the largest
positions" does not survive contact with the rate limit. Ranking by position size needs `clearinghouseState` for
the whole set: 45,948 x weight 2 = 91,896 against a documented **1,200 weight/min per IP** (verified against
Hyperliquid's own rate-limit page: weight 2 for `clearinghouseState`, 20 for other `info` requests) — 77 minutes
of the entire IP budget per pass against a 300 s cadence, and 6.4 hours at the collector's own `CONCURRENCY = 4` /
`GAP_S = 2.0` pacing. **Re-scope E to a bounded depth (top 2,000-5,000 rows, ~0.4-0.9 MB/day) chosen to cover the
account-value range the tracker might ever reach, or drop it. Delete the "largest positions" argument either
way.** Lower priority than A-D, and part of the held-back stage.

**F. Use OKX's 24-hour liquidation REST window as a gap repair — a paced poller, not a one-line call.**
`GET /api/v5/public/liquidation-orders?instType=SWAP&state=filled` **fails** without a family:
`{"code":"50015","msg":"Either parameter uly or instFamily is required"}` (verified today, HTTP 400). And
`GET /api/v5/public/instruments?instType=SWAP` lists **482 instruments in 482 distinct families**, while the
collector subscribes to all of them. Walking BTC-USDT with `after=<oldest ts>` took 29 pages x limit 100 = 2,832
rows to reach 1,438.9 minutes back (23.98 h) in 17 s, so the "exactly 24 hours" claim **is** verified; OKX's
public-data limit is 20 req/2 s. A universe-wide 24 h repair is therefore ~500-3,000 requests with its own rate
limiter, driven off the instrument-family list already held in `okx_instruments` (derive families from `instId`).
On the good side the REST `details` objects carry the same keys as the WS ones (`bkLoss`, `bkPx`, `posSide`,
`side`, `sz`, `ts`), so it reuses `okx_ws.liquidation_row` verbatim and dedupes on the existing primary key.
Costs nothing standing; recovers rows that are otherwise gone.

### 5.2 Feed extensions (prod.db, where most of the value is)

Each needs a `botlib.FRESHNESS_CONTRACTS` entry in the same commit — the repo's standing rule for a live-read
table — and, where the cadence is uniform, a `data/check_gaps.py` `Spec`.

**And one standing rule for change-detected tables.** A table written "only on change" cannot carry a row-age
contract: the contract either alerts on a quiet market or is set so loose it certifies nothing, and **a stopped
writer looks identical to a quiet market** — which is exactly how `ca_liquidations`, `screener_*` and the Bybit
perp tables died silently in May and June. (Measured today for the concrete case: `fundingInfo`'s newest
`updateTime` is today, but only 22 of 789 symbols changed in the last 7 days and 85 in 30 — so a 2-day contract
on `MAX(update_time)` would breach on quiet weeks and certify nothing on busy ones.) The repo already carries the
right pattern: `deribit_options_instruments` and `binance_quarterly_contracts` both contract on
`("last_seen_ts", 1.0, 2 * 86400)` — a column bumped on **every successful poll**, change or not. So: **every
change-detected table gets a `last_seen_ts` bumped on each poll and contracts on that column; value-change rows
stay append-only in a second column set.** Where a daily row per key is cheap (items 7 and 13), write it instead
— it is simpler and gives the contract a uniform cadence.

| # | Item | Endpoint / change | Cadence | Table shape | Contract | MB/day rows |
|---|---|---|---|---|---|---|
| 1 | **Full Deribit chain** | delete `LIQUID_MAX_DAYS` and `LIQUID_MAX_LOG_MONEYNESS` from `deribit.py::is_liquid()` | existing 00:05 / 08:05 | `deribit_options_daily` unchanged | unchanged (18 h) | +0.29 |
| 2 | **Deribit USDC linear** | extend `_NAME_RE` to `ASSET_USDC-DDMMMYY-STRIKE-C\|P` (strikes carry `d` as the decimal point, e.g. `AVAX_USDC-19SEP26-6d5-C`); iterate the USDC chain **per underlying**, not per currency. **Three mandatory fixes — see below.** | same | same table + `contract_size` and `settlement_currency` on `deribit_options_instruments` | same, scoped per underlying | +1.15 |
| 3 | **Option trade tape** | `get_last_trades_by_currency_and_time?currency=BTC\|ETH&kind=option&sorting=asc`, paging on `has_more` | hourly, window = `max(last stored ts, now − 20 h)` → now | `deribit_option_trades(trade_id PK, ts_ms, instrument, price, amount, direction, iv, index_price, mark_price)` | 3 h | +2.0 |
| 4 | **DVOL 1-minute** | `get_volatility_index_data?resolution=60`, continuation-paged | hourly catch-up | `deribit_dvol_1m(asset, timestamp)` PK | 3 h | +0.14 |
| 5 | **Gate `contract_stats`** | `api/v4/futures/usdt/contract_stats?contract=BTC_USDT&interval=5m` | hourly catch-up | `gate_contract_stats(contract, timestamp)` PK, 25 numeric columns | 3 h | +0.14 |
| 6 | **Coinalyze 1h liquidations + open interest** | `fetch_coinalyze.py --liquidations --interval 1hour`, plus `open-interest-history` on the same call | one recovery run **now**, then daily catch-up in `feed.py` | `ca_liquidations` (unfreeze) + `ca_open_interest(asset, timestamp)` PK | 26 h, matching `ca_long_short_ratio`; `check_gaps` Spec at 3600 s | +0.03 |
| 7 | **`fundingInfo` / `constituents`** | `/fapi/v1/fundingInfo` (789 rows) and `/fapi/v1/constituents?symbol=...` | daily | `binance_funding_info(symbol, observed_day)` PK; `binance_index_constituents(symbol, exchange, observed_day)` PK | 2 d on the daily row | +0.08 |
| 8 | **Bot tick log** | `botlib` helper called by every runner tick | 60 s | `bot_ticks(bot, variant, tick_utc)` PK + status, note, rule_id, gate values JSON, intent_produced; **plus one `bot_tick_daily(day, bot, variant, status)` counter row** | state table, no age contract | +0.05 (non-`ok` and status changes, plus the counters; +1.3 if every tick) |
| 9 | **Feed vintages** | in `binance.py`, before each `INSERT OR REPLACE` over a re-fetched window | per feed cycle | `feed_vintages(table_name, bucket_ts, fetched_ts)` PK + value; write a value row only when the value changed, **and a `checked_ts` row every cycle regardless** | contract on `checked_ts` | +0.10 |
| 10 | **Monitor run log** | append each `monitor.py` run's result beside `monitor_last.json` | hourly | `monitor_runs(run_utc PK, status, alert_codes, per-table age ratios, per-bot heartbeat age)` | 3 h on `run_utc` | +0.02 |
| 11 | **ETH open interest** | add an ETHUSDT arm to `binance.py::fetch_open_interest`, into an **asset-keyed** table | hourly | `binance_open_interest(asset, timestamp)` PK — do not extend `cd_open_interest`, which has no asset column and carries the item-30 stamp defect | 3 h | +0.005 |
| 12 | **Expiry-day option poll** | reuse item 1's snapshot on the hour, Thursdays and Fridays only | hourly on 2 days | existing table | existing | +0.35 amortised |
| 13 | **Perp contract specs** | the **same daily `exchangeInfo` pull `binance_quarterly.py::fetch_contracts` already makes** — keep all 905 symbols instead of 4 | daily, no extra request | `binance_perp_contracts(symbol, observed_day)` PK: contract_type, status, onboard_ts, delivery_ts, tick_size, step_size, min_notional, maint_margin_pct, required_margin_pct, liquidation_fee, first_seen_ts, last_seen_ts | 2 d on `last_seen_ts` | ~0.4 first snapshot, then <0.05 |
| ~~14~~ | ~~**Binance options panel**~~ | ~~`/eapi/v1/mark` + `/eapi/v1/openInterest`~~ | — | — | — | **parked** (§2.2) |

**Item 2's three mandatory fixes.** As specified in the first draft this item would have written bad data, and
was verified wrong against the live chain today:

1. **Mark price.** `snapshot_options()` computes `(mark * underlying)`, correct only because coin-settled marks
   are quoted in the base currency. Live today: coin-settled `BTC-25DEC26-104000-C` mark 0.01280805 with
   underlying 81,634 → $1,046, right; USDC-linear `BTC_USDC-25DEC26-125000-C` mark **285.58** with underlying
   81,628 and `quote_currency: 'USDC'` → the code would store **$23.3 M** for a $286 option. **Branch the USD
   conversion on the payload's `quote_currency`** (USDC/USDT → already USD-denominated; base currency →
   multiply by `underlying_price`). `quote_currency` is in the `get_book_summary_by_currency` reply, verified.
2. **Contract size.** `get_instruments?currency=USDC&kind=option` returns contract_size BTC_USDC 1.0, ETH_USDC
   1.0, SOL_USDC 10, HYPE_USDC 10, AVAX_USDC 100, XRP_USDC 1,000, TRX_USDC 10,000 (verified today). Neither
   `deribit_options_daily` nor `deribit_options_instruments` has a `contract_size` column, so `open_interest` for
   SOL/XRP/HYPE/AVAX/TRX — **the stated reason for the item** — would be off by 10x to 10,000x with nothing
   recorded to undo it. **Add `contract_size` and `settlement_currency` to `deribit_options_instruments` in the
   same commit.**
3. **Catch-up scope and the `asset` label.** `_snapshot_age_s(now, asset)` scopes with
   `WHERE instrument LIKE 'BTC-%'`. USDC names are `BTC_USDC-25DEC26-...`, so a `currency='USDC'` pass matches
   nothing, `age` is `None`, and the function's own docstring says "None means 'cannot tell', and the caller must
   not treat that as due" — so the catch-up path can never fire for the USDC leg. That is the exact defect the
   scoping was added to fix on 2026-09-10 ("BTC wrote 496 rows and ETH silently did not run"), and the
   `deribit_options_daily` freshness contract cannot catch it because the BTC/ETH legs keep `MAX(timestamp)`
   fresh. **Key the scope on a prefix that matches (`LIKE 'BTC\_USDC-%'` per underlying) and iterate per
   underlying.** Separately, `fetch_instruments()` stores the loop's `currency` argument verbatim into
   `deribit_options_instruments.asset`, so a USDC pass would label all seven underlyings `asset='USDC'`. **Set
   `asset` from `parse_instrument`'s parsed prefix instead.** (`deribit_options_daily` has no `asset` column at
   all; the first draft's "`asset` = underlying prefix" named a column that does not exist.)

**Item 3's repair path.** The option tape is the only item here with a hard deadline (~24 h, measured) and **no
REST repair of any kind** — a >24 h feed outage loses that window permanently. A fixed 70-minute poll window
would lose rows on any outage longer than 70 minutes, which is the exact failure the item exists to prevent. The
endpoint takes arbitrary `start_timestamp`/`end_timestamp` with `sorting=asc` and returns `has_more` (verified),
and the repo shipped this pattern at HEAD three days ago (`5c72c8c fix(feeds): coinbase and quarterly refresh
resume from the last stored bar`). So: **resume from `max(last stored ts, now − 20 h)`, paged on `has_more`.**
The 3 h contract is fine — but it must be an alert the operator *acts on* inside the remaining ~21 hours, not a
dashboard colour.

Ordering note: items 1, 8, 3, 9, 5, 2, 7, 6, 13, 10 are the week-one ten. Item 11 should land with the 5-minute
metrics backfill so the ETH research history and the live writer arrive together — the repo's own
research-versus-live parity rule.

---

## 6. Costs and limits

### 6.1 Rate limits that actually bind

- **Deribit**: 20 req/s sustained, 100 burst for non-matching-engine requests. `get_instruments` is the one
  expensive method (10,000 credits, ~1 req/s). Our whole plan is a handful of calls per hour — no pressure.
- **Binance `fapi` IP budget is shared with `feed.py` and, once started, the collector's depth snapshots.**
  `binance_ws.py` already enforces a 2 s per-symbol snapshot gap, exponential backoff, and an IP-wide hold of
  `max(Retry-After, 60 s)` on 429/418. §5.2 item 13 adds **zero** requests — it keeps rows from a call already
  made daily.
- **Coinalyze**: free tier 40 req/min (`fetch_coinalyze.py` sleeps 1.6 s between calls). The recovery run in item
  6 is ~2 chunks x 2 assets; the daily catch-up is one call per asset.
- **Gate.io**: hard 180-day `from` limit, enforced with an explicit error. Nothing else bit in testing.
- **OKX**: 3 connections/s per IP; 480 subscribe/unsubscribe/login requests per hour per connection; public-data
  REST 20 req/2 s — which is what bounds §5.1 item F's 482-family repair.
- **Bybit**: public `args` array <= 21,000 characters per connection (measured: 400 topics = 10,434 chars, all
  883 linear instruments = 23,346); "do not build over 500 connections in 5 minutes" per IP.
- **Hyperliquid**: **1,200 weight/min per IP**, `clearinghouseState` weight 2, all other documented `info`
  requests weight 20 (verified against Hyperliquid's rate-limit page today). `predictedFundings` is weight 20
  hourly; the existing 200-address pass is 400 weight per 300 s. Ample headroom — but a whole-leaderboard
  position poll is not (§5.1 item E).
- **Binance `/fapi/v1/income`** (once live): retains **three months**. The poller must exist on day 1.

### 6.2 Vendor prices

Only Tardis publishes figures: **$350 / $700 / $1,000 / $3,000 per month** (Academic / Solo / Pro / Business) for
the options plan, $300 minimum order, history depth set by billing interval. Amberdata, Kaiko and Laevitas are
contact-sales with no public number. **Recommendation: pay nothing now.** Use Tardis's free
first-day-of-each-month `options_chain` files (~80 monthly cross-sections back to 2020-03, no key) to
pre-register and pilot; that is enough to decide whether the paid pull is worth $2,100, and §5.2 item 1 makes the
forward series complete meanwhile.

### 6.3 Disk arithmetic, with the dates

Measured today at 14:40 UTC: **24.17 GB free**; prod.db **1,647,812,608 B = 1.648 GB**, growing **0.51 MB/day**
of rows (backups `prod-20260917.db` 1,604,517,888 B → `prod-20260918.db` 1,605,025,792 B); `data/backups` holds
4.6 GB; `backup.py:163-166` refuses to run below `2 x prod.db` = **3.296 GB free**; `store.py` pauses depth_1s
below **5 GB** and resumes above 8 GB.

Two headrooms matter: **19.17 GB** from today to the depth pause, and **20.88 GB** from today to the backup floor.

Rates, with §0's 3x multiplier applied to every prod.db row:

| Component | Rows MB/day | Disk MB/day |
|---|---|---|
| prod.db existing growth | 0.51 | 1.5 |
| §5.2 programme (rank 18 parked) | 4.4 | 13.2 |
| **Feed side total** | **4.9** | **14.7** |
| Collector, liquidation streams only (incl. item B's unverified widening) | — | 15-55 |
| Collector, everything | — | 170-230 |
| §5.1 A3 `stream_coverage` | — | 0.14-0.7 |

| Scenario | Disk rate | Depth pause (5 GB) | Backups stop (3.30 GB) | Drive full |
|---|---|---|---|---|
| Feeds only, no collector | 14.7 MB/day | n/a | **~2030-08** (1,420 days) | later |
| Feeds + **staged** collector (liquidations only) | 30-70 MB/day | n/a | **10-23 months** (~2027-07 to ~2028-08) | +4-9 months |
| Feeds + full collector, 170-230 MB/day | 185-245 MB/day | **2026-12-05 to 2026-12-31** (78-104 days; ~2026-12-16 at 200) | **~2026-12-28** (~12 days after the pause) | **~2027-01-22** |
| Feeds + full collector, VM bundle freed (+10.76 GB) | 185-245 MB/day | **2027-01-18 to 2027-02-27** (~2027-02-04 at 200) | **~2027-02-16** | **~2027-03-13** |

**The depth pause is not a floor; it is a 10-to-16-day warning.** `DROP_FIRST_TABLE` stops depth_1s only — the other
95-155 MB/day keep running, plus the feed side's 14.7 — so the 1.704 GB between the pause line and the backup
floor is crossed in **10 to 16 days** (12 at the 200 MB/day midpoint), and from there the remaining
3.30 GB in a further 21-33 days, at which point prod.db writes fail and the fleet stops. The first draft concluded "the pause ordering is safe … the
failure mode is 'the book series thins out', not 'the backups silently stop'." That is false: on the full
collector the backups **do** stop, 10-16 days after the pause. The three dates above are what the operator
needs, and the December note is a **scheduled action with a named remedy**, not a diary entry.

So the collector go-ahead should be conditional on one of: (a) the **staged start** of §5.1 A, which removes the
question for 10-23 months; (b) a second drive via `collector.py --db`; (c) a retention/VACUUM policy on
`depth_1s` and `hl_asset_ctx`; or (d) the 300 s `hl_asset_ctx` cadence in §2.2, which alone is ~15 % of the
collector. Levers that cost nothing: the **10.76 GB** VM bundle and the **58 MB** `b.csv.gz`. Per the standing
"keep everything" rule, `data/backups` and the two 2026-05 `prod.db.bak` files (954 MB) are **not** levers.

BACKLOG item 9 orders the options as *free space first; keep the cadence*. On this arithmetic that ordering is
right for the first move and the staged start makes it unnecessary for now — but the preference was recorded
against "roughly three months", and three months is now the date the **degradation** starts, not the date the
drive fills. If a second move is ever needed, thinning depth_1s saves the most bytes and loses the least
irreplaceable data, because `bookDepth` survives in the archive forever while a liquidation print does not
survive anywhere.

### 6.4 The unbacked-up database

microstructure.db is not in `backup.py` and never will be — it is far too large to copy daily onto this drive.
But the liquidation table is **10-40 MB/day, the small part of the total, and the only part that is truly
unrecoverable**. A weekly `VACUUM INTO` of `liquidations` alone (plus `stream_coverage`, which is what makes it
interpretable), to any off-machine target, is a few hundred MB a month and protects the thing no vendor will sell
back. Depth is recoverable at 30 s from the archive and `hl_asset_ctx` is recoverable from Hyperliquid's own S3.
This is worth deciding when the collector starts, not after a disk failure — and under the staged start it is the
*only* table to decide about.

---

## 7. What this does not solve

**Vintages already lost.** The vintage table stops the bleeding; it does not reconstruct what a 2026-05 study
read. The validation audit's "reproduce the affected baselines" step will reproduce *today's* data, and a match
proves less than it looks like it does. Item 30 is the proof that the same rows can mean two different things.

**Gamma is still a year away**, even after §1 item 1. Widening the filter does not make the study testable
sooner — it decides whether it is testable *at all* when the clock runs out. The only thing that answers it this
quarter is Tardis's free monthly cross-sections, and those are 80 single days, not a series. This is also why
the Binance options panel is parked rather than scheduled.

**Twenty-eight days of hourly liquidations are already gone**, and were gone before this document was written.
Item 2 recovers ~88 days and stops the loss; it cannot undo 2026-05-24 17:00 → ~2026-06-21. That gap is the cleanest
illustration of this document's whole thesis: the series was in the repo, the client was written, the key was in
`.env`, and it stopped because a table was marked FROZEN with a retention figure that was wrong by a factor of
three and nothing contracted on it.

**Breadth stays the binding constraint.** The validation audit's headline — not one row reaches DSR >= 0.95, and
"more years, or more assets, move this; more tuning does not" — is untouched by anything here. The alt panel is a
download (per-symbol `metrics` from 2021-12, `fundingRate` full history, klines), but it is a download plus weeks
of panel-building and per-symbol cost modelling, and this plan deliberately does not schedule it against the
forward-only items. **The one part of it that is not recoverable later is the per-symbol spec history, and §1
item 9 costs nothing and fixes exactly that.** Everything else about the alt panel will still be recoverable next
quarter. That is the whole point of the distinction.

**Three flow studies came back null, and this plan does not explain why.** Absorption, footprint C3 and
spot-vs-perp all used coarse, public, inferred inputs, and the honest possibility is that the null is real rather
than instrumental. Nothing here guarantees that liquidation prints at millisecond resolution behave differently.
The case for recording them is that they cannot be bought later, not that they will work.

**Unverified or unresolved, carried forward as such:**

- **Farside ETF flows** — the survey asserts a full daily per-issuer history from January 2024 at
  `farside.co.uk`. Not checked here. Verify before building; `trader.db etf_flows_btc` (580 rows, 2024-01-11 →
  2026-04-13, per-issuer) is on this machine either way and is the cheapest start.
- **Bybit `allLiquidation` at 400 symbols** — 110k rows/day is the survey's 90-second measurement, not
  re-verified. The byte figure derived from it (~16 MB/day with indexes) inherits that uncertainty.
- **Hyperliquid S3 requester-pays cost** — both buckets exist (anonymous probes return the requester-pays
  AccessDenied XML), and the docs confirm `asset_ctxs/[date].csv.lz4`, but no per-GB figure was obtainable. Size
  it after a credentialed listing, not before. This matters for §2.2 rank 16: the substitute is *known to exist*
  but *not priced*.
- **OKX L2 archive** — OKX's own historical-data page advertises L2 from March 2023, but 11 CDN URL patterns all
  404'd. The real path is not public; capture it from a browser session if anyone wants it.
- **Volmex BVIV/EVIV** — no working endpoint found (`api.volmex.finance` returns structured 404s;
  `rest-v1.volmex.finance/public/iv/history` returns 500, which hints the path exists with different
  parameters). The cheap way to settle it is the TradingView MCP against `VOLMEX:BVIV`.
- **`fstream` `aggTrade` / `kline` / `markPrice` streams returned zero messages from this host** on 2026-09-18
  while `trade`, `bookTicker` and `depth@100ms` streamed normally in the same script. Recorded as a host
  observation, not a venue fact. It changes no priority — both series are fully archived — but re-check before
  anyone builds against `fstream aggTrade`.
- **OKX open-interest history depth** — the survey said ~60 days; probing today returned rows at 30 days back and
  none at 60. The honest statement is "between 30 and 60 days", and either way it is forward-only.

---

## 8. Review record

Three adversarial reviews (completeness, feasibility, priority) returned 26 findings. Each was re-verified on
this machine or against the venue before being acted on; three were adopted with their arithmetic or reasoning
corrected, and none was rejected outright.

| # | Finding | Verdict | Reason |
|---|---|---|---|
| 1 | Coinalyze intraday liquidations/OI omitted; fleet has had no liquidation series since 2026-06-10 | **Adopted** | Re-measured: 1h liq rows at 89 d back, none at 90; 1h OI rows at 80 d, none at 85; daily rows at 1,000 d. `ca_liquidations` 2092 rows ending 2026-05-24 17:00; `cd_liquidations` ends 2026-06-10 08:00; 5,429 markets / 26 BTC perps / 16 venues. Now §1 item 2, rank 2, §5.2 item 6. |
| 2 | No durable record of the collector's own coverage | **Adopted** | `store.py` DDL has only `collector_runs`; all counters in-memory. `known_unfillable.json` + `health._gap_is_unfillable()` is the repo's opposite doctrine. Now §5.1 A3. |
| 3 | Reference/instrument layer (`exchangeInfo`) missing | **Adopted** | Live: 905 symbols, 773/130/2, `onboardDate`, `liquidationFee`, margin percents, 7 filter types. Vision `um/daily/` has no contract metadata. `fetch_contracts` already makes the call and keeps 4 rows. Now §1 item 9, rank 3, §5.2 item 13. |
| 4 | Change-detected tables cannot carry row-age contracts | **Adopted** | `deribit_options_instruments` / `binance_quarterly_contracts` already use `last_seen_ts`. Stated once as a rule in §5.2's preamble and applied to items 7, 9, 13. |
| 5 | Rank 10's "no historical form on any venue" is wrong for 2 of 3 legs | **Adopted** | Verified: Bybit `premium-index-price-kline` returns rows 1,800 days back; Binance `premiumIndexKlines` from 2019-12-23 is in the plan's own §3.1. Rank 12's cost-of-waiting rewritten. |
| 6 | §4.1 claims a Coinbase tick archive that does not exist | **Adopted** | Verified: `/products/BTC-USD/trades` pages 1,000 at a time against trade_id 1,094,694,878 = ~1.09 M requests. Re-labelled in §4.1 and §3.1; Coinbase tick is now a forward-only capture decision, not a download. |
| 7 | §5.1's "start, then extend" sequence makes the new depth columns a silent no-op that destroys whole batches | **Adopted** | Verified: no `ALTER TABLE` in `data/sources/micro/`; `_is_busy` matches only locked/busy; `_write` rolls back the whole transaction; `derive_status` yields `degraded`. Now §5.1 A1 + A2. |
| 8 | "The pause ordering is safe" is false | **Adopted** (merged with #19) | 1.704 GB between the 5 GB pause and the 3.296 GB floor at 109-169 MB/day = 9-13 days. §6.3 rewritten with three dates. |
| 9 | §6.3 counts prod.db rows once; every row is stored three times | **Adopted** | `--keep-daily 2 --keep-weekly 0`, two rolling copies measured in `data/backups/`. Stated in §0 and applied to every figure. |
| 10 | Item 2 would store `mark_price_usd` wrong by the underlying, and OI with no contract multiplier | **Adopted** | Verified live: `BTC_USDC-25DEC26-125000-C` mark 285.58 vs coin-settled 0.0128; `contract_size` 1/1/10/10/100/1,000/10,000; `quote_currency` present in the book summary. Now §5.2 item 2 fixes 1 and 2. |
| 11 | Item 2 also disables the per-asset catch-up and mislabels `asset` | **Adopted** | Verified: `_snapshot_age_s` scopes `LIKE 'BTC-%'`; `fetch_instruments` stores the loop argument verbatim; `deribit_options_daily` has no `asset` column. Now §5.2 item 2 fix 3. |
| 12 | Item B is a collector change with three silent failure modes | **Adopted** | Verified: hardcoded 12-tuple, no turnover source, subscribe evaluated once and replayed verbatim on reconnect, `success:false` logged only, `ALLOWANCE_S["bybit_liq"] = 3600`. §5.1 B respecified. |
| 13 | The `liquidations` PK merges distinct prints | **Adopted** | Verified: PK on (venue, symbol, ts_ms, pos_side, price, qty), `INSERT OR IGNORE`, `extra = None`, Bybit frames carry no id. Now §5.1 A5 — must be decided before the table holds a year. |
| 14 | Item C's per-side totals cannot answer the wall question; 100 ms netting bounds it anyway | **Adopted** | Verified: `book.py::find_wall` already localises the wall; stream is `@depth@100ms`. §5.1 C rescoped and the resolution stated. |
| 15 | Item E costs ~2x the stated bytes and its rationale is unreachable | **Adopted** | Verified: rate-limit page gives weight 2 / 1,200 per min, so 45,948 accounts = 77 min of the whole IP budget per pass; `CONCURRENCY=4`, `GAP_S=2.0`. §5.1 E rescoped, rationale deleted, B/D/E bytes restated with index overhead. |
| 16 | Item F is 482 paged chains, not one call | **Adopted** | Verified: no-`uly` call returns HTTP 400 / code 50015; `instruments?instType=SWAP` = 482 in 482 families. §5.1 F respecified. |
| 17 | Deribit tape retention is a hard ~24 h with no repair path | **Adopted** | Re-measured today: 273 trades in the hour ending 22 h back, **0** at 24/26/28/30/36/48. §1, rank 6, §3.3 and §5.2 item 3 all say "hard ~24 h". |
| 18 | `fundingInfo` + 2-day contract can never be satisfied | **Adopted, reasoning corrected** | The fix is right and taken (daily `observed_day` rows). The stated reason is not: `MAX(updateTime)` is **today** — but only 22 of 789 symbols changed in 7 days, so the contract is unreliable in both directions rather than permanently breached. §5.2's preamble states it that way. |
| 19 | Item 8's non-`ok`-only logging removes the denominator | **Adopted** | A daily per-(bot, variant, status) counter row added to §5.2 item 8 and to the §4.1 row. |
| 20 | (duplicate of #8, stated with full arithmetic) | **Adopted, merged** | Its numbers were re-derived independently; §6.3's table now carries them. |
| 21 | "Start it, before any extension" inverts the document's own ranking; staging should be the recommendation | **Adopted, arithmetic corrected** | The recommendation is right and is now §5.1 A. Its runway figure was not: applying #9's 3x backup multiplier to the feed programme gives **10-23 months** staged, not 13-28. Both still dwarf ~3 months. |
| 22 | Rank 13 is justified by the question liqmap dissolved | **Adopted** | Verified against `findings_liqmap.md` §4/§6: +2.54 pp → +0.10 pp under distance matching; ETH turn −6.84 pp; "do not re-propose". Rank 15 rewritten to the amount half, with the distance-matching instruction quoted. |
| 23 | `hl_asset_ctx` is 39 MB/day, unranked, and the most recoverable item in the collector | **Adopted** | Verified against Hyperliquid's docs: `asset_ctxs/[date].csv.lz4` is published (requester-pays); liquidations and positions are not. Now rank 16 with its own row, byte cost, S3 substitute and a priced cadence option. |
| 24 | Item 3 has no catch-up path; retention is 24-30 h | **Adopted, figure corrected** | The fix is right and taken (resume from `max(last stored, now−20 h)`, paged on `has_more`, matching `5c72c8c`). The retention figure is not: my own measurement puts the boundary at ~23-24 h, tighter than 24-30, and agrees with #17. |
| 25 | The perp symbol universe is forward-only, free, and unranked | **Adopted, merged with #3** | Verified: `screener_universe` is `PRIMARY KEY (asset)` with no as-of column, feed stopped 2026-05-23; delisted symbols' klines survive in the archive, so prices backfill and specs do not. One new §5.2 item 13 covers both findings. |
| 26 | Rank 9 (Binance options panel) is a cross-check scheduled ahead of G0's only evidence stream | **Adopted** | Verified: `monitor.py` overwrites one JSON and rotates 6 MB of text; prod.db has no `monitor_runs` (58 tables). Rank 18 parked behind a scheduled gamma study; the monitor run log promoted to week-one item 10 and rank 9. |

---

### Appendix: how the load-bearing numbers were checked

| Claim | Method |
|---|---|
| Collector not running | `bot_heartbeats` SELECT (prod.db `mode=ro`); `Get-CimInstance Win32_Process`; `find` for microstructure.db |
| 24.17 GB free; prod.db 1,647,812,608 B; VM bundle 10.76 GB | `shutil.disk_usage('C:/')` and `os.path.getsize` at 14:40 UTC; `Get-ChildItem -Recurse \| Measure-Object Length` |
| prod.db growth 0.51 MB/day; backups are two rolling copies | byte sizes of `data/backups/prod-20260917.db` and `prod-20260918.db`; `backup.py` docstring and `PRUNE_FROM`; `ls data/backups/` |
| Deribit filter drops 48.7 % / 60.6 % of OI | live `get_book_summary_by_currency` for BTC and ETH, re-implementing `deribit.py::is_liquid()` and summing `open_interest` kept vs all |
| USDC mark price is USD-denominated; contract sizes 1-10,000 | live `get_book_summary_by_currency?currency=USDC` and `get_instruments?currency=USDC&kind=option`, compared against the coin-settled chain |
| Option tape retention hard ~24 h | `get_last_trades_by_currency_and_time`, 1-hour windows at 1/6/12/18/22/24/26/28/30/36/48 h back |
| DVOL retention by resolution | same endpoint at `1D`/`3600`/`60`/`1`, stepped 0.02 → 2,100 days back |
| Coinalyze retention: liq 1h ~89 d, OI 1h ~80 d, daily >= 1,000 d; 5,429 markets | live `liquidation-history`, `open-interest-history`, `future-markets` with the `.env` key, 6-hour probe windows stepped 30 → 95 days |
| `exchangeInfo` 905 symbols / 773-130-2 / filters / margin schedule | live `fapi/v1/exchangeInfo` |
| `fundingInfo` 789 rows, 467/318/4, 22 changes in 7 d | live `fapi/v1/fundingInfo`, `updateTime` histogram |
| Bybit premium-index klines to 1,800 d; OKX 482 families; Coinbase trade_id 1.09 B | live calls to each endpoint today |
| Hyperliquid `predictedFundings` 234 coins / 633 non-null legs; rate limits; S3 archive contents | live `POST /info`; Hyperliquid's own rate-limit and historical-data documentation pages |
| Vision archive spans, sizes, and the absence of contract metadata | S3 list API with `delimiter`/`prefix`/`marker` pagination, `.CHECKSUM` keys excluded; `um/daily/` prefix listing; delisted-symbol kline listings (FTTUSDT, SRMUSDT, TOMOUSDT) |
| `leverageBracket` needs a key | live unauthenticated call → 401 `-2014` |
| Gate 180-day limit; Bybit OI to 2020-09-09; OKX OI 30-60 d | retention ladders against each endpoint |
| CoinDesk 401 | live calls to both documented endpoints |
| prod.db and trader.db table spans | read-only SELECTs (`deribit_options_daily` 514,433 rows, 19,014 carrying OI and IV, all `source='deribit'` from 2026-09-06 20:00; `ca_liquidations` 2,092 rows to 2026-05-24; `cd_liquidations` 2,494 rows to 2026-06-10; trader.db `ca_liquidations` 3,034 daily rows to 2026-04-24) |
| Collector code claims (no ALTER path, batch rollback, PK shapes, allowances, hardcoded symbol list) | direct reads of `collector.py`, `data/sources/micro/{store,bybit_ws,binance_ws,book,hyperliquid,wsclient}.py`, `botlib.py`, `backup.py`, `monitor.py` |

# chento-findings validation plan

> **Structural caveat (added 2026-05-23):** Live observation of chento doubling a position from ~$1M to ~$2M while underwater so a small bounce scratches the trade at 25% TP. This pattern is **not in our 890-record scan** (zero lifecycles with margin_growth ≥ 1.5x and worst_pnl ≤ -10%). If frequent, each "scratched loss" appears in our data as a small *win*, inflating per-fill WR; the Feb 2025 RUNE blowup (-$28.5k, 100% of extracted losses on one day) is then better read as "the day the bounce didn't come on a doubled position" than as random tail risk. **Bot implication:** we cannot codify the unbounded version. A4 ladder-add must stay bounded (2 rungs at -0.75R / -1.0R, hard combined-stop at -1.5R). Realistic codifiable expectancy ceiling may be lower than the +0.30R the v2 sleeve currently produces. See [project_chento_size_doubling_observation.md](../../../../../Users/TJ5/.claude/projects/c--Source-Repos-p300/memory/project_chento_size_doubling_observation.md). **Do not assume the edge is fake** — he has built $100k → $1M+ multiple times; size-doubling may be one component of a larger edge stack we haven't fully captured.

## Context

Over the last 3 weeks we reverse-engineered a discretionary crypto trader's playbook from 1,989 Discord messages and 890 image extractions, plus 4 codifiable rules he articulated on a YouTube live stream (`notes.md` v28 + live commentary). The current `chento_limit_bid` v2 sleeve captures ~5% of his observed behavior (a single long-side swing-base entry + 33/50/trail exit ladder).

Before shipping any further bot work, we need to **validate every codifiable finding against historical data** so we ship what carries edge and discard what's marketing or survivorship. The first pass at a "findings" list missed the entire signal-stream layer (money flow, liquidation cascades, long:short ratio, Leviathan TP placement). This plan corrects that — it enumerates every implementable finding, groups them by what data they need, and proposes a notebook-per-group structure with explicit pass/fail gates.

Premise: if we knew the recipe to millions we'd already have it. So the gates are calibrated for incremental edge per finding, not magic-bullet expectations.

## Findings inventory (39 items)

Source: `studies/material/chento/notes.md` (v1–v28 + live commentary), `studies/material/chento/scan_full.jsonl` (890 records), `studies/material/chento/scan_aggregated/{trades.jsonl, realized_pnl_events.jsonl, signal_taxonomy.json}` (176 lifecycles, 49 realized fills).

Tagging: **SIG** = entry/exit trigger · **FLT** = regime gate · **SZ** = sizing rule · **RSK** = risk/exec rule · **AE** = anti-edge (skip or invert) · ev = evidence count.

### Group A — Sleeve-config tuning (no new data, just config sweeps on v2 backtest)

| ID | Finding | Type | ev |
|---|---|---|---|
| A1 | Short-side mirror (`chento_limit_offer`) — 60/40 short bias | SIG | 224 vs 150 |
| A2 | TIF tighten 21d → 1–3d (median holding 2.1h, p75=6.8h, only 1/90 >7d) | RSK | 90 |
| A3 | SL → BE on T1 hit ("Taking 50% rest is for 90% or BE") | RSK | 7+ |
| A4 | 2-rung ladder-add at –1R (ladder-shorts-higher / DCA-longs-lower) | SZ | 12+ |
| A5 | Hour-of-day / session cell map | FLT | 174 |
| A6 | Hard leverage cap: alts ≤20x, majors ≤30x (RUNE blowup = 100% of losses) | RSK | 1 catastrophe + 15+ leverage tags |
| A7 | 25% trim variant alongside 50% template | SZ | 3+ |
| A8 | Trim→DCA→Trim cycling (rotate exposure inside ladder) | SZ | 2+ |
| A9 | Dynamic TP adjust mid-trade (TP not fixed commitment) | RSK | 4+ |
| A10 | Leverage taxonomy: 5-7x HTF / 20x intraday / 50x+ scalp | SZ | 15+ |

### Group B — Signal filter / new feature on **existing prod.db data**

| ID | Finding | Data source available NOW | Type | ev |
|---|---|---|---|---|
| B1 | Rule 1: money-flow positive + weak price → bearish | `cd_futures_15m.volume_buy/sell` (taker CVD) vs price velocity | SIG | live verbatim |
| B2 | Rule 2: time-in-range strengthens resistance | OHLC + `detect_active_base()` extended | FLT | live verbatim + 8 |
| B3 | Rule 3: don't trade midrange — only outer-25% of range | OHLC + range detector | FLT | live verbatim + 6 |
| B4 | Trade-with-the-squeeze (CoinGlass squeeze direction) | `cd_liquidations.long_quantity / short_quantity` (already hourly) | SIG | 5+ |
| B5 | Long/short ratio extremes as contrarian/momentum filter | `ca_long_short_ratio` (Binance global account ratio) | FLT | 3+ |
| B6 | Move-vs-liq ratio anomaly ("TAME for 2.5% move") | price-move-magnitude / `cd_liquidations.long+short_quantity` | SIG | 6+ |
| B7 | Multi-TF CVD divergence grid (proxy for flowx multi-TF money-flow grid) | extend `_refresh_mtf_bias_cache()` to add CVD per TF | SIG | 12+ |
| B8 | Pre-NY session profit-take + 1h-before-NY pattern (Mon/Tue/Wed cluster) | hour-of-day + day-of-week from OHLC | RSK | 3+ |
| B9 | OI-flush + funding-flip combo (already 2 of 4 in v2 confluence — measure standalone) | `cd_open_interest` + `cd_funding_rate` | FLT | 8+ |
| B10 | Spot-driven move detection ("Most mental spot only driven move") = realized-range / OI-delta | `btc_1m` + `cd_open_interest` | SIG | 4+ |
| B11 | Volume profile / POC stickiness | volume distribution over price from `btc_1m` | FLT | 6+ |
| B12 | DVOL regime gate (when implied vol is high, mean-reversion edges weaken) | `cd_dvol` | FLT | implicit in his hedge era |
| B13 | Hedge-mode-as-range-profiting (both legs green when in range) | needs hedge sleeve architecture, but range gating uses existing data | SIG/RSK | 8+ |

### Group C — Requires new ingestion or compute, then test (all in scope per user)

| ID | Finding | New data needed | Effort | Type | ev |
|---|---|---|---|---|---|
| C1 | Liquidation **price-level cluster** (Leviathan/Coinglass heatmap) for TP placement | proxy: bucket `cd_liquidations.vwap_long_price/vwap_short_price` by price-band over rolling window; no external API needed | low | RSK | 4+ |
| C2 | Whale / mid / retail trade-size cohort flow (Rule 1 stronger form) | ingest Binance `aggTrades` with $-bucket segregation → new table `binance_agg_trades_by_size` | medium | SIG | 5 + verbatim |
| C3 | CHENTO ON TOP composite indicator components | `skyfield` for planetary aspects (Sun/Moon/Mars/Jupiter angles, Full Moon, retrograde) + Vol Spike + RSI Neutral + Session — all computed from existing OHLC + ephemeris library | low–medium | FLT | 8+ |
| C4 | Cross-exchange perp delta (flowx Exchange Deviation) | ingest OKX + Bybit perp 1h OHLC alongside Binance; compute cross-exchange delta z-scores | medium | SIG | 4+ |
| C5 | SMC structure features (OB / CHoCH / FVG / BOS) | pure compute from `btc_1m` — codify the rules | medium | SIG | 15+ |
| C6 | Auction Market Theory acceptance/rejection | pure compute from volume profile | low | FLT | 1 (weak ev) — bundled into C5 |

### Group D — Apply directly, no test needed

| ID | Finding | Why no test |
|---|---|---|
| D1 | Rule 4: marathon / compounding mindset | risk-management invariant; equity-curve-level, not signal-level |
| D2 | DCA-fill triggers immediate trim (rotate exposure) | mechanical execution coupled to A4 |
| D3 | Multi-account challenge architecture | exchange-layer not sleeve-layer; defer to live infra |
| D4 | Explicit "TEST" track-record exclusion (data hygiene) | journalistic discipline; mirror in our backtest tagging |

### Group E — Confirmed not edge / non-replicable

| ID | Finding | Why skip |
|---|---|---|
| E1 | No-stop-loss "Chen Tzu" mystique | per v15 + Feb 2025 -$7,742 stop-hunt loss; marketing, not edge |
| E2 | Anti-signal pattern (tells followers to close, holds personally) | we're not his audience; not actionable for our bot |
| E3 | Network sourcing (Muppet calls, killa fractals, John flow) | non-replicable for an autonomous bot |
| E4 | Affiliate / flowx product ecosystem | not a trading edge |
| E5 | Audience-stream-time micro-moves | not actionable |

## Test plan by group

Each group is **one notebook in `studies/notebooks/chento_journal/`**, except Group C which is split per-data-stream because each requires different upstream build work.

### Notebook 1 — `validation_A_sleeve_tuning.ipynb`

Backtests v2 sleeve with each Group-A knob flipped, one at a time. Reuses existing `backtest_runner.py` plumbing and the per-sleeve override pattern from `discovery.ipynb` / `chento_limit_bid_v3_backtest.ipynb`. **NO sleeve-code edits in this notebook** — short side (A1) is tested in-notebook only; the architecture decision (config-flag vs new sleeve folder) is deferred to after the validation_summary review.

Sections:
- **A1**: invert direction in-notebook (mirror v2's math: `MTF_NET_ACCEPT=(-1, 3)`, capitulation sig `++---`, swing-peak instead of swing-base detection, SHORT entry above peak). Backtest on full multi-asset set 2023–2026. No sleeve code touched.
- **A2**: TIF sweep ∈ {1, 2, 3, 5, 7, 14, 21}d. Output: R-per-trade × WR × frequency curve.
- **A3**: toggle `STOP_TO_BE_ON_T1=True`. Compare worst-trade-R distribution vs v2.
- **A4**: 2-rung ladder add at –0.75R / –1.0R with 0.5× size at each rung. Hard-stop at –1.5R below original entry.
- **A5**: per-hour-UTC and per-weekday WR + mean-R heatmap from existing v2 ledger (no re-backtest needed — postprocess).
- **A6**: policy change; verify alts-leverage cap by inspecting config, no backtest.
- **A7**: 25% trim at T1 (vs current 33%), keep rest at T2/trail.
- **A8**: re-add same notional after trim if price reverts within band (cycling).
- **A9**: TP-adjust ladder ("after T1 hit, move T2 from 3R to 2R if trail-watermark > 1.5R").
- **A10**: leverage taxonomy enforcement (HTF bias score → leverage class).

**Gate (per knob):** ship if net R/trade ≥ v2 baseline AND frequency preserved (within ±20%) AND max-DD-per-trade not worse by >0.2R.

### Notebook 2 — `validation_B_signal_filters.ipynb`

Each section adds ONE Group-B signal/filter to v2 and measures (a) standalone signal expectancy and (b) uplift vs v2 baseline when used as filter.

Sections:
- **B1 Money-flow divergence (taker-CVD proxy)**: define `mf_velocity = cvd_z(15m) / price_change_pct(15m)`. Test as bearish-bias filter when mf_velocity > threshold AND price-change-pct < threshold over rolling 1h window.
- **B2 Time-in-range resistance**: extend `detect_active_base()` to also detect a range bracket (high-low pair persisting >N candles). Test `time_at_range_top` weighting as conviction scalar for shorts.
- **B3 Midrange avoidance**: gate entries on `price_percentile_in_range ∉ (0.25, 0.75)` — only top-25%/bottom-25% entries.
- **B4 Trade-with-the-squeeze**: define squeeze direction = sign(`cd_liquidations.long_quantity - short_quantity`) z-scored over rolling 24h. Test as direction-bias for next 4–24h window.
- **B5 Long/short ratio extremes**: when `ca_long_short_ratio` > p90 or < p10 of trailing 30d, test counter-trend vs continuation in next N hours.
- **B6 Move-vs-liq anomaly**: define `move_vs_liq = abs(price_change_15m) / (long_qty_15m + short_qty_15m)`. Test as "spot-driven" continuation signal when ratio is high.
- **B7 Multi-TF CVD grid**: compute CVD direction on 5m/15m/1h/4h/1d. Test alignment (all green or all red) as conviction multiplier.
- **B8 Pre-NY session / day-of-week pattern**: from B5 of A5 heatmap, identify any Mon/Tue/Wed × 14:30 UTC clusters; backtest as time-gated entry.
- **B9 OI-flush + funding-flip standalone**: factor existing v2 confluence into its 2 standalone components; measure each.
- **B10 Spot-driven move detection**: define `spot_drive = abs(price_change) / abs(oi_change)`; high → spot-driven (continuation); low → futures-driven (mean-reversion).
- **B11 Volume profile POC**: compute rolling 7-day POC; test "entry rejected at POC" vs "breakout past POC".
- **B12 DVOL regime gate**: split backtest by DVOL quartile; measure v2 expectancy per quartile.
- **B13 Hedge-mode-as-range-profiting**: needs separate notebook (becomes part of Notebook C-4) because it requires hedge-sleeve architecture.

**Gate (per filter):** ship if (a) ≥+0.10 R/trade when used as gate on v2, OR (b) standalone signal ≥+0.20 R/trade at ≥30 trades/yr, OR (c) cuts tail loss by ≥30%. Stat-significance check: rerun with random-permuted feature, p<0.05 vs that null.

### Notebook 3 — `validation_C1_liq_cluster_tp.ipynb`

Build proxy liq-cluster from `cd_liquidations.vwap_long_price/vwap_short_price` bucketed by price-bands over rolling 7d window. Test TP placement at "heaviest cluster above (for longs) / below (for shorts)" vs current fixed-R targets.

**Gate:** TP-at-liq-cluster captures ≥85% of fixed-3R TP capture AND fills earlier (median time-to-TP shorter by ≥20%).

### Notebook 4 — `validation_C2_whale_flow.ipynb` (requires ingestion build)

Step 1: build `pipelines/binance_agg_trades.py` to ingest Binance `aggTrades` endpoint, bucket by USD-notional ($10k+ = whale, $1k-$10k = mid, <$1k = retail), write to `binance_agg_trades_by_size` table. Backfill 1 year for BTC + ETH.

Step 2: in the notebook, compute per-15m whale-CVD / mid-CVD / retail-CVD. Test Rule 1 strict form: `whale_cvd > 0 AND price_velocity < threshold` → bearish next-1h.

**Gate:** Rule 1 trigger has ≥55% next-1h directional accuracy at ≥100 instances/yr. Compare to taker-CVD-only baseline from B1.

### Notebook 5 — `validation_C3_chento_indicator.ipynb`

Build the CHENTO ON TOP composite as a library function:
- `skyfield` planetary aspects (Sun-Moon angle, Mars-Saturn, retrograde flags, Full Moon)
- Vol Spike (ATR % spike z-score)
- RSI Neutral (RSI ∈ [45, 55])
- Session label (Asia / London / NY / overlap)
- Multi-TF EMA confluence (already in v2 as MTF bias)
- Composite scoring (weighted, ≥70% threshold both per-component-Quality and overall)

Test each component standalone + composite as a filter on v2.

**Gate:** any component with ≥+0.10R uplift on v2 is shipped; planetary aspects without uplift are documented and dropped.

### Notebook 6 — `validation_C4_hedge_range.ipynb`

Implement a hedge-mode sub-sleeve that runs simultaneous long + short within a confirmed range bracket (uses B2 + B3 range detector). Both legs scaled. Profits from oscillation.

**Gate:** Sharpe ≥ 1.5 over 2y, max-DD < 15%, edge robust to ±20% range-bracket parameter sweep.

### Notebook 7 — `validation_C5_smc_features.ipynb` (optional, later)

Codify Order Block / FVG / BOS / CHoCH detection. Test each as filter on v2.

### Notebook 8 — `validation_summary.ipynb`

One-pager. Loads outputs from notebooks 1–7. For each finding A1–C6:
- result (per the gate above)
- recommended action: SHIP / DROP / DEFER / NEEDS-DATA
- proposed config diff (for SHIPs)

This is the source-of-truth document for the eventual v3 sleeve PR.

## Acceptance gates (recap)

| Group | Gate |
|---|---|
| A | Net R ≥ v2 baseline AND freq ±20% AND max-DD-per-trade not worse by >0.2R |
| B | ≥+0.10R as filter OR ≥+0.20R standalone @ n≥30/yr OR tail-loss cut ≥30% |
| C1 (liq TP) | ≥85% of fixed-3R capture AND median time-to-TP shorter ≥20% |
| C2 (whale flow) | Rule 1 ≥55% directional accuracy @ n≥100/yr; beats B1 proxy |
| C3 (chento ind.) | per-component ≥+0.10R uplift |
| C4 (hedge) | Sharpe ≥1.5, max-DD <15%, robust ±20% sweep |
| C5 (SMC) | per-feature ≥+0.10R uplift |

## Multi-asset scope (user choice: BTC + ETH + OP + more alts)

Every notebook section runs across the full asset set. Existing tables cover BTC + ETH + OP. New ingestion required for additional alts:

**Step 0 — `pipelines/backfill_alts.py`** (must complete before any notebook can be considered multi-asset-complete):
- Add SOL, AVAX, SUI to all existing Binance/CoinDesk ingestion paths (perp 1h + 15m, spot 1h + 15m, funding 8h, OI hourly, liquidations hourly, long/short ratio daily)
- SOL is in chento corpus (1 SOLUSDT lifecycle in scan); RUNE is the blowup case (-$28k single day) — backfill RUNE separately so we can validate the A6 leverage-cap finding against the actual loss event
- Asset list: **BTC, ETH, OP, SOL, AVAX, SUI, RUNE** (RUNE for forensic validation only)
- Backfill range: 2023-01-01 → present
- Add `cd_open_interest`, `cd_liquidations`, `ca_long_short_ratio` schema columns for `asset` so the same table holds multi-asset rows (currently BTC-only by convention)

Each validation notebook iterates the asset set and emits per-asset + portfolio-aggregated R distributions, WR, frequency. Multi-asset summary section per finding shows where the edge concentrates (e.g., "B4 squeeze-direction signal only works on BTC + ETH, not on alts").

**Cross-asset gates layered on top of per-asset gates:**
- A finding is SHIP-eligible if it passes the per-finding gate on ≥2 of the major assets (BTC, ETH).
- Alt-only edges are SHIP-eligible only if expectancy is ≥0.5R/trade and frequency ≥50/yr.

## Critical files

**Read (reuse, don't rewrite):**
- `strategies/sleeves/chento_limit_bid/signal.py` — entry/exit logic, `_sweep_open_positions`, `evaluate_tier_transitions`
- `strategies/sleeves/chento_limit_bid/math.py` — `detect_active_base`, `_resample_ohlcv`, `compute_tf_bias_series`, `score_base_window`
- `strategies/sleeves/chento_limit_bid/config.py` — sleeve config schema
- `strategies/sleeves/ai_quant/cvd.py` — `load_hourly_cvd`, `daily_cvd_series` (canonical CVD)
- `backtest_runner.py` — replay harness
- `data/sources/binance.py` — `fetch_klines_interval`, `fetch_long_short_ratio`
- `data/sources/coindesk.py` — `fetch_liquidations`, `fetch_oi`, `fetch_dvol`
- `studies/notebooks/swing_base_limit_bid/discovery.ipynb` — backtest notebook template

**Write (new):**
- `pipelines/backfill_alts.py` — STEP 0 multi-asset backfill (SOL/AVAX/SUI/RUNE) + schema extension for `asset` column on cd_liquidations / cd_open_interest / ca_long_short_ratio
- `pipelines/binance_agg_trades.py` — Binance aggTrades whale-bucket ingestion (for C2)
- `pipelines/okx_perp.py` + `pipelines/bybit_perp.py` — cross-exchange perp 1h OHLC (for C4)
- `studies/lib/range_detector.py` — extracted from notebook B2/B3 (reusable range + midrange + time-in-range)
- `studies/lib/chento_indicator.py` — extracted from notebook C3 (reusable composite indicator)
- `studies/lib/smc_features.py` — extracted from notebook C5 (OB/FVG/BOS/CHoCH)
- `studies/lib/liq_cluster.py` — extracted from notebook C1 (price-level liq density from VWAP buckets)
- `studies/notebooks/chento_journal/validation_A_sleeve_tuning.ipynb`
- `studies/notebooks/chento_journal/validation_B_signal_filters.ipynb`
- `studies/notebooks/chento_journal/validation_C1_liq_cluster_tp.ipynb`
- `studies/notebooks/chento_journal/validation_C2_whale_flow.ipynb`
- `studies/notebooks/chento_journal/validation_C3_chento_indicator.ipynb`
- `studies/notebooks/chento_journal/validation_C4_cross_exchange_delta.ipynb`
- `studies/notebooks/chento_journal/validation_C5_smc_features.ipynb`
- `studies/notebooks/chento_journal/validation_C6_hedge_range.ipynb` (was C4 in earlier draft; renumbered)
- `studies/notebooks/chento_journal/validation_summary.ipynb`

## Verification

Per notebook:
1. Notebook runs top-to-bottom on a fresh kernel without errors
2. All sections produce an artifact: trade-list + R-distribution + WR + freq + a one-line PASS/FAIL vs the gate
3. Outputs are written to `studies/material/chento/validation/{group}_{finding_id}.json` for the summary notebook to ingest

Global:
1. `validation_summary.ipynb` ingests all per-finding JSON and emits a single `findings_decisions.md` markdown table
2. Hand-spot-check 3 random SHIP-recommended findings by re-running their notebook section and confirming the gate is met
3. For any SHIP that touches the live sleeve, confirm `chento_limit_bid` config diff is minimal and reviewable

## Execution order

0. **`pipelines/backfill_alts.py`** — backfill SOL/AVAX/SUI/RUNE for all 5 data tables. Schema migration to add `asset` column where missing. Multi-asset is gating — must finish before A/B can be considered complete (Notebook 1 + 2 can start on BTC/ETH/OP in parallel while backfill runs).
1. **Notebook 1 (Group A)** — cheapest, mostly mechanical config sweeps on v2.
2. **Notebook 2 (Group B)** — high EV; tests trader's own verbalized rules on data we already have.
3. **Notebook 3 (C1 liq cluster TP)** — no new ingestion; pure compute on existing `cd_liquidations`.
4. **Notebook 5 (C3 chento indicator)** — `skyfield` is a pip install; cheap to try. Run in parallel with C1.
5. **`pipelines/binance_agg_trades.py` then Notebook 4 (C2 whale flow)** — ingestion + test. Larger build.
6. **`pipelines/okx_perp.py` + `pipelines/bybit_perp.py` then Notebook 6 (C4 cross-exchange delta)** — ingestion + test.
7. **Notebook 7 (C5 SMC features)** — pure compute, biggest implementation surface. Bundle C6 AMT into this.
8. **Notebook 8 (C6 hedge-mode range-profiting)** — depends on B2/B3 range detector passing; uses C5 structure features if available.
9. **Notebook 9 (summary)** — rolling-update as findings land; final pass once all are complete.

Hard dependency chain: 0 → {1, 2} → {3, 4, 5, 6, 7} → {8} → {9}.

Soft preference: complete groups A and B fully before any Group-C work — if A/B reveals that v2's core thesis is wrong, several Group-C builds become moot.

---

## Live progress log

### 2026-05-22 — Notebook 1 (Group A) first pass

Harness shipped: [validation_A_sleeve_tuning.py](validation_A_sleeve_tuning.py).
Outputs: [studies/material/chento/validation/A_results.json](../../material/chento/validation/A_results.json), [studies/material/chento/validation/A_baseline_ledger.jsonl](../../material/chento/validation/A_baseline_ledger.jsonl).

**Baseline (v2 default config):** BTC+ETH+OP · 155 trades / 6.1y · 25.4/yr · mean R **+0.104** · WR 39% · T1 50% · T2 25% · max-DD-p10 **−1.36R** · hold-p75 **94h** · implied annual **+5%** @ 2% NAV risk.

| Section | Verdict | Result |
|---|---|---|
| **A2 TIF sweep** | KEEP 21d | Tightening hurts (1d=−0.007, 3d=+0.034, 7d=+0.088). 14d=+0.108 marginally beats 21d=+0.104; not enough to ship. Earlier "median holding 2.1h → tighten TIF" reasoning was about *his* trades, not our sleeve. |
| **A3 BE-on-T1** | DROP | R drops to +0.033 (−0.071). WR up 39%→50% but kills the runner (T2 25%→18%). |
| **A7 25% trim at T1** | SHIP | R=+0.126 (+0.022 over baseline). Closing 25% vs 33% leaves more runner. |
| **A5 hour/weekday** | INSUFFICIENT | Time-gate (06–22 UTC) concentrates fires too narrowly; only 5 cells reach n≥5. Needs wider time gate to be useful. |
| **A6 leverage cap** | SHIP (policy) | Alts ≤20x, majors ≤30x. |

**Critical structural finding:** Only 22/155 entries are BTC (4/yr). ETH (107) and OP (26) bypass confluence scoring (no 15m perp/OI/CVD in DB). **86% of v2 expectancy rests on a code path that skips the gates v2 was built around.** Notebook 2 section B9 (OI-flush + funding-flip standalone) becomes a structural check on whether confluence is real edge or theatrical.

**Stubbed → next session (Notebook 1 NOT yet complete):**

- **A1 — regime-gated direction (corrected design).** Not blind direction-mirror. Use S-003 ADX (`strategies/sleeves/adx/signal.py:99-200`) as regime classifier: `ADX(14)≥25 ∧ close>EMA(50) → 'long'`; `≥25 ∧ close<EMA(50) → 'short'`; `<20 → None`. Fire long-side swing-base entries only when regime='long'; short-side swing-peak entries only when regime='short'. Three variants: A1a long-gated, A1b short-only, A1c combined.
- **A4 — 2-rung ladder-add** at −0.75R / −1.0R, 0.5× size per rung. Combined hard-stop at −1.5R.
- **A8 — trim/DCA cycling** — re-entry within +0.25R of entry after T1, bounded one cycle.
- **A9 — dynamic TP adjust** — if trail-watermark > 1.5R when T1 fires, move T2 from 3R → 2R.
- **A10 — leverage taxonomy** — MTF bias score → leverage class (HTF 5-7x / intraday 20x / `--+++` 50x), tested as scaled-R.

**Foundation work also pending:**

- Entry cache to disk (`entries_v2_{asset}.parquet`) — eliminates 78-min per-run bottleneck for future sweeps.
- ADX regime classifier helper — wrap S-003 `_current_signal()` (or expose +DI/-DI) as per-(asset, timestamp) callable. Needed for A1.

### Next session order

1. Entry cache (foundation)
2. ADX regime helper (foundation)
3. A1 three variants
4. A4, A8, A9, A10
5. Final A_results.json with all 10 sections + verdicts
6. → THEN Notebook 2 (Group B)

---

### 2026-05-24 — Notebook 2 (Group B) COMPLETE — all 13 sections

Full summary: [NOTEBOOK_2_SUMMARY.md](NOTEBOOK_2_SUMMARY.md). Headlines:

**Final composite (BTC only, 6+ years, honest cost):**
- Best per-trade quality: **Triple + B3 + B8 + B11 = +0.604R per trade, 62% WR, IS=OOS=+0.60R** (89 trades, 14/yr)
- Best annual return: **Triple + B3 + B12 = +0.290R per trade, 84/yr, +65% annual** (matches chento's trade frequency)
- Triple base = B1 (money-flow div) ∩ B5 (LSR extremes) ∩ B7-align (multi-TF CVD)

**Structural findings:**
1. **Signal-stream confluence > swing-base detection** for matching chento's setups
2. **B9: v2's OI+funding confluence components are NEGATIVE standalone** — the existing sleeve's edge comes from swing-base+MTF, not the confluence. Worth testing v2 *without* OI/funding.
3. **B11 POC alignment is the biggest single filter** (+0.428R uplift on the composite)
4. **Composite works in trending markets, decays in chop** — ADX-regime gate is the obvious next experiment
5. **B4 squeeze is potentially the strongest single signal** (+0.87R per trade in 88d sample) but BOTH CoinDesk and Coinalyze APIs source-limit liquidations to ~3 months — paid data needed

**Negative or rare findings:**
- B6 (move-vs-liq) and B10 (spot-driven) negative both directions — signals catch chento timestamps but directional inference doesn't work
- B2 time-in-range too restrictive alone
- B8 MTW 13:30 UTC pattern doesn't generalize (3 specific days only)
- B13 hedge-mode naive proxy negative (-0.20R per hedge) — likely needs sophisticated range identification + scaling

**Coverage truth:** Final composites catch <25% of chento's actual trade timestamps same-direction. We've found OUR signal-stream edge, loosely inspired by his playbook but mechanically different.

### Next session priorities

1. **Test v2 without OI+funding confluence** — directly addresses B9's structural finding; quick win if R improves
2. **ADX-regime gate on candidates** — finding 4 says composite works trending only; adding gate should restore OOS expectancy
3. **Find paid liquidation data source** — user is investigating; would unlock B4 multi-year validation
4. **Group C work** — CHENTO ON TOP indicator (planetary aspects + Vol Spike + RSI Neutral via skyfield), SMC features (OB/FVG/BOS), liq-cluster TP placement
5. **Bootstrap CIs on the +0.60R OOS finding** — confirm the signal is statistically robust on small OOS sample

Soft preference: complete groups A and B fully before any Group-C work — if A/B reveals that v2's core thesis is wrong, several Group-C builds become moot.

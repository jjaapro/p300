# Exit-policy study (2026-09)

BACKLOG item 11 (are the time stops worth anything?) merged with research queue 2 (exits that fire when a trade is
shown wrong). Started at the user's request on 2026-09-15: *"study what exit events there could be for each strategy."*
The user's hypothesis, recorded before any number: *"time stop overall feels a bad idea, it just means that we do not
actually know if the trade was valid or not."*

Each strategy is its own phase with its own frozen pre-registration, because each exit is part of a different thesis.
Nothing in this folder changes a bot; any change a verdict permits is proposed to the user separately.

| Phase | Strategy | Status | Result |
|---|---|---|---|
| 1 | **chento** (BTC + ETH, 392 identical gate-off entries) | **CONCLUDED 2026-09-15** | **INCONCLUSIVE — keep 72 h.** Shorter time stops are significantly worse; no time stop is +0.18 R per trade on average but only in 2021–2023, and doubles ETH's drawdown; flow-reversal and opposite-signal exits do not help. [findings_chento.md](findings_chento.md) |
| 2 | squeeze_bull (and its no-stop twin) | **CONCLUDED 2026-09-15, report-only** | **The incumbent's 48 h time stop does nothing either way** (its 30 time-stopped trades split 15 targets / 15 stops when held); for the no-stop twin it is the risk control (without it 15 trades hit a −10 % catastrophe stop, drawdown 4 % → 11 %). Wider targets, no target and "flush resumed" are within noise. Twin − incumbent +0.21 R (95% CI +0.07, +0.34), almost all before mid-2024. [findings_squeeze_bull.md](findings_squeeze_bull.md) |
| 3 | short_squeeze (and its no-stop twin) | planned | Report-only until its re-cut. Stage 1 removed the microstructure part: its trades (median 65 min) almost never meet absorption, rejection or book events. What remains is risk: a catastrophe stop for the no-stop twin, whose only loss exit is the 6 h time stop |
| S1 | **microstructure stage 1** (chento, squeeze_bull, short_squeeze) | **CONCLUDED 2026-09-15** | **NONE PROMOTED.** An information test, not an exit test: at the first absorption, 24 h-extreme rejection or order-book tilt against the position, is the rest of the trade worth less than at matched moments? On chento (the only population with enough events) no: Holm p 0.69–0.83, sign controls the same, and holding after each event still paid +0.67 to +0.78 R. short_squeeze meets these events on 0–2 of 71 trades; squeeze_bull too rarely to test. No stage 2. [findings_microstructure.md](findings_microstructure.md) |
| A | **squeeze_bull top anatomy** (exploratory stage A) | **DONE 2026-09-15** | What happens just before a bounce reverses looks the same at the top as at earlier highs that kept going; nothing real-time marks the top. The bounce's extra return fades after 24 h without a new high (next 24 h +0.12 % vs +0.51 % while fresh) or once price is past 1.5 flush-sizes. Found on the way: the live OI feed has carried each hour's opening value since 2026-06-10, and **SJ-4250 fired only because of it** (−2.48 % stored vs −1.79 % at bar closes). Stage B candidate: exit after 24 h without a new high. [findings_top_anatomy.md](findings_top_anatomy.md) |
| F | **spot-led versus perp-led extremes** (brainstorm item F; chento BTC + ETH, squeeze_bull, short_squeeze) | **CONCLUDED 2026-09-18** | **NONE PROMOTED.** A new high carried by perpetual takers and a rising premium while Binance spot lags carries no exit information. On chento BTC it points the other way (Δ +1.068 R, 95 % +0.100 to +2.034: holding after one of those highs beat holding at matched highs), on squeeze_bull it is flat (−0.036), and short_squeeze cannot be asked (5 event trades). The venue leg is what separates and its sign is the opposite of the hypothesis. [findings_spot_perp.md](findings_spot_perp.md) |
| B | **liquidation map** (brainstorm candidate B; chento BTC + ETH, squeeze_bull) | **CONCLUDED 2026-09-18** | **`REPLICATED: up_touch, down_touch` — and it is worth nothing.** The map's cluster is touched inside 24 h more often than the level a control map built by the same code with the information removed puts out (BTC +2.87 and +2.20 pp, ETH +4.10 and +2.80 pp), but the turn is absent and the burst exit is null (chento BTC EV2 −0.26 R, placebo comparison +0.04 R). The §7 diagnostic settles the touch result: the actual cluster sits nearer price in 64.8 % of states, and matching the two levels on distance takes the difference to zero (≤ 20 bp: +0.10 pp BTC, +0.09 pp ETH). The map tells you to put your level nearer, not where price will go. Its estimated **amount** series is worth keeping (range-controlled ρ ≈ 0.56 against measured liquidations); its side split is not. No production change, no stage B. [findings_liqmap.md](findings_liqmap.md) |
| R | **move vs implied range and the rejection-wick exit** (brainstorm rows I and J; chento BTC + ETH, squeeze_bull, short_squeeze) | **CONCLUDED 2026-09-19** | **NONE PROMOTED.** Neither the first minute at which a trade's favourable move exceeds one option-implied daily move (DVOL / √365) nor the first 15-minute bar that rejects from a causal level while ≥ 0.3 R in profit (Paladin's exit, his best variant) carries exit information: chento BTC Δ +0.26 R and +0.37 R, squeeze_bull +0.08 R, all UNDETERMINED with holding after the event paying *more* than at matched moments. The legs are empty: the implied scale scores above its realised-vol control (+0.22 R, 95 % +0.00 to +0.46, the wrong way) and the level adds nothing to the shape (−0.03 R). As exit arms both lose on every population (chento wick exit −0.55 R per trade; Paladin's +0.12 R does not transfer). One ETH implied-range line is negative (−0.92 R, p 0.048) against a positive BTC line on the same rule — recorded, not a result. Closes the brainstorm's last pair. [findings_range_wick.md](findings_range_wick.md) |
| — | ADX, CARRY, R4 | out of scope | Their exits define the strategy (regime exits, the funding exit, the calendar window), per the item-11 census |

Related evidence from outside this folder: the ORB study's exit arms (`studies/notebooks/orb_study/findings.md` §5) — on
breakout payoffs early and invalidation exits lowered gross and dropping the time exit raised it.

## Phase 1 files (chento)

| File | Role |
|---|---|
| [PREREGISTRATION_CHENTO.md](PREREGISTRATION_CHENTO.md) | Frozen arms, walker, statistics, decision rule, disclosure |
| `chento_lib.py` | Inputs (OKX-study snapshot, pool and features; ORB-study funding), the 10-arm walker, bootstrap and Holm |
| `chento_checks.py` | Preconditions P1–P7 and step 0 |
| `chento_run.py` | `freeze0`, then `outcomes` (verdict written last; neither stage reruns) |
| `chento_explore.py` | Post-verdict tables: drawdown against fixed capital, where the no-time-stop difference comes from |
| `tests/` | 20 walker fixtures; opt-in end-to-end smoke run (`EXIT_SMOKE=1`) |
| `results/chento/` | `freeze_F0.json`, `preconditions.json`, `step0.json`, `walks.csv.gz`, `report.json`, `verdict.json`, `exploratory.json` |
| [01_chento_exit_policy.ipynb](01_chento_exit_policy.ipynb) | Executed review: recomputes every walk, checks it against the saved files, shows the evidence |
| [findings_chento.md](findings_chento.md) | Verdict and what it permits |

## Phase 2 files (squeeze_bull, report-only)

| File | Role |
|---|---|
| [PREREGISTRATION_SQUEEZE_BULL.md](PREREGISTRATION_SQUEEZE_BULL.md) | Frozen arms, 1-minute perp walker, what is reported; nothing decided |
| `sqb_lib.py`, `sqb_run.py` | Inputs, walker, excursions, sequence; `checks`, `freeze0`, `outcomes` |
| `sqb_explore.py` | Post-report exploratory control: the bull regime's own forward drift |
| `tests/test_sqb_walk.py` | 12 walker fixtures |
| `results/squeeze_bull/` | `hourly_snapshot.json`, `preconditions.json`, `freeze_F0.json`, `walks.csv.gz`, `report.json`, `exploratory_regime_drift.json` |
| [02_squeeze_bull_exit_policy.ipynb](02_squeeze_bull_exit_policy.ipynb) | Executed review, recomputes every walk |
| [findings_squeeze_bull.md](findings_squeeze_bull.md) | What the report shows and why it permits nothing yet |

Phase 2 also reads the ORB study's Binance perpetual 1-minute panel and funding, and a study snapshot of the hourly price
and open-interest tables (`p300-study-snapshots/exit_policy_2026_09/squeeze_bull_hourly.npz`).

## Microstructure stage 1 files

| File | Role |
|---|---|
| [PREREGISTRATION_MICROSTRUCTURE.md](PREREGISTRATION_MICROSTRUCTURE.md) | Events, the matched-placebo statistic, the decision rule and disclosure, frozen before any count |
| `micro_data.py` | bookDepth download (checksum-verified, `data/raw/bookDepth/`, gitignored) and the per-minute ±1 % book arrays (`cache/`, gitignored) |
| `micro_lib.py` | Populations, the one 1-minute walker, flow / level / book events, matched placebos, bootstrap and classification |
| `micro_run.py` | `checks` (M1–M8), `freeze0`, `outcomes` (verdict written last; neither stage reruns) |
| `micro_explore.py` | Post-verdict exploratory control: placebo controls restricted to ±365 days of the event trade |
| `tests/test_micro_events.py` | 23 fixtures: features, walker, every event rule, placebo exclusions, statistics |
| `results/microstructure/` | `preconditions.json`, `freeze_F0.json`, `events.csv.gz`, `trades.csv.gz`, `report.json`, `verdict.json`, `exploratory_era_matched.json` |
| [03_microstructure_information.ipynb](03_microstructure_information.ipynb) | Executed review: recomputes every event and statistic, checks them against the saved run |
| [findings_microstructure.md](findings_microstructure.md) | Verdict and what it permits |

Stage 1 needs the ORB study's BTCUSDT and ETHUSDT 1-minute panels and the bookDepth archive (about 1.24 GB of zips for
2023-01-01 → 2026-09-13). Rebuild with `micro_data.py download` and `build`.

## Top anatomy files (exploratory stage A)

| File | Role |
|---|---|
| [PROTOCOL_TOP_ANATOMY.md](PROTOCOL_TOP_ANATOMY.md) | Definitions, features, what stage A may and may not do, the pre-committed stage B holdouts; Amendment A1 |
| `anatomy_data.py` | BTCUSDT 5-minute open interest and long/short ratios, 1-minute premium index (archive, checksum-verified, 71 MB), and the SJ-4250 case panel (archive + REST to 2026-09-15 16:26 UTC) |
| `anatomy_lib.py` | Bounce shapes, causal features, false tops, repair, state map, data checks |
| `anatomy_run.py`, `anatomy_amend_a1.py` | The stage A run (manifest first) and the same-fires overlay of Amendment A1 |
| `tests/test_anatomy.py` | 10 fixtures: shapes, false tops, stall, flow windows, the metrics lag, repair features |
| `results/top_anatomy/` | manifests, `summary.json`, `shapes.csv`, `top_vs_false*.{json,csv}`, `profiles*.json`, `repair*`, `state_map.json`, `states.csv.gz`, `gallery.*`, `case_SJ-4250*`, `data_checks.json` |
| [04_squeeze_bull_top_anatomy.ipynb](04_squeeze_bull_top_anatomy.ipynb) | Executed review: the case, every top aligned, top against earlier highs, repair, state map, the OI timestamp issue |
| [findings_top_anatomy.md](findings_top_anatomy.md) | What stage A shows and the stage B candidate |

Inputs outside the repository: the OKX re-validation's read-only snapshot
(`C:\Source\Repos\p300-study-snapshots\okx_gate_revalidation\snapshot.db`, sha256 `f3decffe…`). Rebuild the notebook with
`C:/Python/Python313/python.exe studies/notebooks/exit_policy_2026_09/build_notebook.py`.

## Phase F files (spot-led versus perp-led extremes)

| File | Role |
|---|---|
| [PREREGISTRATION_SPOT_PERP.md](PREREGISTRATION_SPOT_PERP.md) | Frozen events, controls, placebo rungs, decision rules, preconditions, disclosure; two review rounds and amendment A26 |
| `spotperp_download.py` | The checksum-verified archive download (422 files: spot 1 m for BTCUSDT, ETHUSDT, BTCFDUSD; premium index for BTCUSDT, ETHUSDT) |
| `spotperp_data.py` | The panel build onto the perpetual grid, with per-file facts and sidecars (`cache/<SYMBOL>_{spot,premium}_1m_panel.npz`) |
| `spotperp_lib.py` | Per-minute inputs, the event kinds and their controls, the matched placebos and the labels |
| `spotperp_run.py` | `smoke`, `checks` (P1–P11), `freeze0`, `outcomes` (neither of the last two reruns) |
| `spotperp_explore.py` | The target-minute channel and the covariate balance, computed after the verdict; exploratory |
| `tests/test_spotperp_data.py`, `tests/test_spotperp_events.py` | 57 fixtures, one twin per gate |
| `results/spot_perp/` | `smoke_counts.json`, `preconditions.json` (+ `_pre_A26`), `freeze_F0.json`, `events.csv.gz`, `trades.csv.gz`, `report.json`, `verdict.json`, `exploratory_reported_tables.json` |
| [05_spot_perp_information.ipynb](05_spot_perp_information.ipynb) | Executed review: recomputes every decision line from the saved events and checks it against the saved run |
| [findings_spot_perp.md](findings_spot_perp.md) | Verdict and what it permits |

Phase F needs the ORB study's 1-minute perpetual panels and about 600 MB of Binance archive zips (gitignored under
`data/raw/spotperp/`). Rebuild with `spotperp_download.py`, then `spotperp_data.py build`, then `spotperp_run.py checks`.
Rebuild the notebook with `build_notebook_spotperp.py`.

## Stage R files (move vs implied range, rejection-wick exit)

| File | Role |
|---|---|
| [PREREGISTRATION_RANGE_WICK.md](PREREGISTRATION_RANGE_WICK.md) | Events, the matched-placebo statistic, the decision rule and disclosure, frozen before any continuation value; amendment A1 |
| `rangewick_lib.py` | DVOL and realised-vol scales, 15-minute bars, causal levels, the kinds, grids, the matched placebo, the exit-arm overlay |
| `rangewick_run.py` | `checks` (P1–P8, the DVOL export), `freeze0`, `outcomes` (verdict written last; neither stage reruns) |
| `tests/test_rangewick_events.py` | 24 fixtures: the scales at their day boundaries, every event threshold at both twins, bars, levels, the placebo rungs and exclusions, the overlay, the verdict refusals |
| `results/range_wick/` | `dvol_daily.csv`, `preconditions.json`, `freeze_F0.json`, `events.csv.gz`, `trades.csv.gz`, `report.json`, `verdict.json` |
| [07_range_wick_information.ipynb](07_range_wick_information.ipynb) | Executed review: recomputes every decision line from the saved events and checks it against the report |
| [findings_range_wick.md](findings_range_wick.md) | Verdict and what it closes |

Stage R reads the ORB perpetual panels, stage 1's populations and walks, and `deribit_dvol_daily` from prod.db
(read-only, exported once at `checks`).

## Liquidation-map files (brainstorm candidate B)

| File | Role |
|---|---|
| [PREREGISTRATION_LIQMAP.md](PREREGISTRATION_LIQMAP.md) | Frozen map model, the control map, the four Q1 level tests and the Q2 event tests, preconditions L1–L8, the classification order and the ETH replication rule; v1.0 → v1.1 after a four-lens adversarial review |
| `liqmap_data.py` | The Binance USD-M `metrics` daily archive download (3,954 checksum-verified zips) and the 5-minute open-interest arrays (`cache/<SYMBOL>_metrics_5m_full.npz`, gitignored) |
| `liqmap_lib.py` | The map and its control (bins, tiers, traversal removal, the 30-day ring), cluster and burst features, touch and turn, the era-matched placebo twins, the paired bootstrap and `classify_liq` |
| `liqmap_run.py` | `checks` (L1–L8), `freeze0`, `outcomes` (BTC, verdict written last), `holdout` (the only stage that builds an ETH map); neither of the last two reruns |
| `liqmap_explore.py` | Section 7 after the verdict: Δ by distance bin and by the sign and size of `d_A − d_B`, plus the ETH trade table the replication's bootstrap axis needs |
| `tests/test_liqmap.py` | 57 fixtures: bin-to-bar mapping, additions and both removals, the ring, cluster and burst definitions, touch and turn, the placebo twins, the claimed sign and the classification order |
| `results/liqmap/` | `preconditions.json`, `freeze_F0.json`, `q1_states*.csv.gz`, `q2_events*.csv.gz`, `trades*.csv.gz`, `report.json`, `verdict.json`, `holdout_eth.json`, `secondary_section7.json` |
| [06_liqmap_levels.ipynb](06_liqmap_levels.ipynb) | Executed review: re-derives the four Q1 tests on both assets, every Q2 test and placebo comparison, the Holm family and section 7's distance ladder from the saved tables, and asserts each against the saved run |
| [findings_liqmap.md](findings_liqmap.md) | The verdict, and why §4 has to be quoted with it |

Candidate B reads the metrics archive (3,954 zips, about 53 MB, gitignored under `data/raw/metrics/`), the ORB study's
1-minute perpetual panels, and — read-only, for L5 validation only — `C:/Source/Repos/trader/data/trader.db`'s
`ca_liquidations`. Rebuild with `liqmap_data.py`, then `liqmap_run.py checks`. Rebuild the notebook with
`build_notebook_liqmap.py`.

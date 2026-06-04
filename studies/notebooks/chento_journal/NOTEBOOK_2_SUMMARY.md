# Notebook 2 (Group B) — complete summary

> **Mode: research / experiment, NOT ship.** Findings here inform what to study next. All numbers use honest cost model (18bp RT scaled by stop distance).

## All 13 Group B sections tested

| ID | Topic | Type | Standalone result | Verdict |
|---|---|---|---|---|
| B1 | Money-flow divergence (Rule 1) | trigger | -0.28R alone; **100% chento coverage** | Essential composite component |
| B2 | Time-in-range strengthens resistance (Rule 2) | filter | Only 9-18 trades after filter | Too restrictive alone |
| B3 | Don't trade midrange (Rule 3) | filter | **+0.054R uplift** as filter | SHIP — drop bottom-edge longs + midrange shorts |
| B4 | Trade-with-the-squeeze | trigger | **+0.87R in 88d sample** | Strong but unvalidatable (cd_liquidations source-limited) |
| B5 | LSR extremes | trigger | -0.20R alone | Essential composite component |
| B6 | Move-vs-liq anomaly | trigger | Negative both modes | Context signal, not directional |
| B7 align | Multi-TF CVD alignment | trigger | -0.00R alone | Essential composite component |
| B7 div | Multi-TF CVD divergence | trigger | +0.21R standalone but only 19/yr | Promising but rare |
| B8 | Session timing + MTW pattern | filter | MTW doesn't generalize; Sat + LDN-NY overlap negative | SHIP — drop Sat + drop 12-14 UTC |
| B9 | OI flush + funding flip standalone | structural | **NEGATIVE all variants** | **MAJOR FINDING: v2 confluence's OI/funding doesn't carry standalone edge** |
| B10 | Spot-driven move | trigger | Negative both modes | Same pattern as B6 (signal real, direction wrong) |
| B11 | Volume profile POC stickiness | filter | At-POC +0.40R; **+0.428R uplift** on composite | SHIP — POC alignment filter |
| B12 | DVOL regime gate | filter | High-vol +0.24R, low-vol -0.08R | SHIP — drop low DVOL |
| B13 | Hedge-mode-as-range-profiting | architecture | Naive proxy -0.20R per hedge | Needs sophisticated impl; not a fail |

## Composite progression (all on BTC, 6+ years, honest cost)

| Composite | n | trades/yr | mean R | WR | IS R | OOS R | annual |
|---|---|---|---|---|---|---|---|
| B1 alone | 8,427 | 1260 | -0.278 | 35% | — | — | -100% |
| B5 alone | 605 | 113 | -0.204 | 36% | — | — | -37% |
| B7-align alone | 833 | 126 | -0.004 | 40% | — | — | -1% |
| B1 ∩ B5 | 1,504 | 280 | -0.033 | 43% | +0.001 | -0.120 | -17% |
| **B1 ∩ B5 ∩ B7-align (triple)** | **681** | **127** | **+0.176** | **49%** | **+0.223** | **+0.055** | **+57%** |
| Triple + B3 | 582 | 109 | +0.230 | 50% | +0.276 | +0.107 | +65% |
| Triple + B3 + B8 | 470 | 88 | +0.296 | 52% | +0.349 | +0.161 | +68% |
| **Triple + B3 + B8 + B11 (POC)** | **89** | **14** | **+0.604** | **62%** | **+0.604** | **+0.603** | **+22%** |
| Triple + B3 + B8 + B11 + B12 | 73 | 11 | +0.590 | 60% | +0.569 | +0.740 | +18% |
| ALT: Triple + B3 + B12 only | 453 | 84 | +0.290 | 52% | +0.314 | +0.185 | +65% |

## Two viable shipping candidates (subject to further work)

### Candidate A — HIGH-QUALITY (Triple + B3 + B8 + B11)
- 89 trades over 6.4y = 14 trades/year on BTC
- **+0.604R per trade**, 62% WR
- **IS / OOS identical at +0.60R** — most robust composite found
- ~3× chento's per-trade R estimate (+0.33R) at ~18% of his trade frequency
- Implied annual: +22% at 2% NAV risk
- Better for risk-adjusted equity-curve; survives chop better

### Candidate B — HIGH-FREQUENCY (Triple + B3 + B12)
- 453 trades over 5.4y = 84 trades/year on BTC (matches chento's ~80/yr!)
- +0.290R per trade, 52% WR
- IS +0.314 / OOS +0.185 (some decay)
- Implied annual: **+65%** at 2% NAV risk
- Matches chento's trade frequency while keeping per-trade R near his level

### Combined annualized R per year (rough)
- Candidate A: 0.604R × 14 = +8.5R/yr
- Candidate B: 0.290R × 84 = +24R/yr
- Chento estimate: 0.33R × 80 = +26R/yr

**Candidate B is closer to chento's annualized R**. Candidate A is more selective and likely more robust to regime change.

## Structural findings (not just trigger results)

### Finding 1: signal-stream confluence > swing-base detection
The original v2 chento_limit_bid sleeve catches 6% of chento's BTC trades same-direction. Triple composite catches 18-25%. The signal-stream layer (money flow + LSR + multi-TF CVD) is structurally better at finding his setups than swing-base detection.

### Finding 2: v2 confluence's OI+funding components are net-negative standalone (B9)
The v2 sleeve uses OI flush + funding flip as 2 of 4 confluence components. Standalone tests show they're **negative R** in all variants. The sleeve's edge must come from swing-base + MTF bias, not the confluence layer. Worth a follow-up: **try removing OI+funding from v2 and see if R improves**.

### Finding 3: Direction-context matters more than expected (B11)
"Short above POC" has +0.485R per trade (n=69, 58% WR). "Short below POC" has +0.100R. The same trigger fired in different price-context produces dramatically different results. Volume profile context is one of the strongest filters tested.

### Finding 4: Composite works in trending markets, decays in chop
"Only no-range entries" gives the best OOS performance (+0.168R vs full composite +0.055R OOS). The 2026 chop has weakened the composite. An ADX-regime overlay (only fire in trending markets) might restore OOS performance.

### Finding 5: B4 squeeze is potentially the strongest single signal but unvalidatable
B4 produced +0.87R per trade at 63% WR in the 88-day window available — by far the highest standalone R of any signal tested. Both CoinDesk and Coinalyze liquidation APIs only serve ~3 months of history, blocking multi-year validation. **A paid data source or live-capture-forward strategy is needed to truly validate B4**.

## Coverage of chento's trades

| Composite | n triggers | Same-direction loose chento coverage |
|---|---|---|
| Triple | 681 | 18% |
| Triple + B3 | 582 | similar |
| Triple + B3 + B8 + B11 | 89 | likely <10% |

**We are NOT replicating chento's specific trades.** Even with the best signal-stream composite, we catch <25% of his timestamps same-direction. What we've found is our own signal-stream edge, loosely inspired by his playbook but mechanically different.

## Caveats

1. **Cost model assumed retail-Binance 18bp RT.** Institutional/prop execution could improve by 8-10bp.
2. **All in-sample for filter selection.** B3/B8/B11/B12 filters were SELECTED by looking at the data — selection bias inflates the composite. The IS=OOS holding on STEP 3 is encouraging but not bulletproof.
3. **OOS sample for STEP 3-4 is small** (17 / 9 trades). The +0.6R OOS holding is suggestive, not definitive.
4. **Hedge mode (B13) needs a better implementation** — naive proxy doesn't capture chento's actual range-profiting setup.
5. **Backtest assumes market-on-bar-close entry.** Chento uses limit ladders into pullbacks, getting better fills. Our entry execution is conservative.

## Recommended next directions

1. **Backfill cd_liquidations from a paid source** — the highest-priority data work. B4 (+0.87R in 88d) would dramatically improve composites if validated multi-year.
2. **Build the v2-without-OI/funding-confluence test** — confirms B9's finding; if R improves, we have a quick win on the existing sleeve.
3. **ADX-regime gate on Candidate A/B** — only fire in trending regimes, see if OOS expands again.
4. **Group C work** (CHENTO ON TOP indicator, SMC features) — explore the remaining structural signals chento uses.
5. **Out-of-sample CIs** — bootstrap-resample the OOS sets to get realistic confidence intervals on the +0.60R OOS finding.

## Output artifacts (this session)

- All per-section results: `studies/material/chento/validation/B{1-13}_*.json`
- Final composite: `studies/material/chento/validation/B_final_composite_results.json`
- Triggers ledgers: `studies/material/chento/validation/B{1,4,5,6,7,8,9,10,11,12}_*.jsonl`
- Code: `studies/notebooks/chento_journal/validation_B{1,4,5,6,7,8,9,10,11,12,13,_final}_*.py`
- Reusable libs: `studies/lib/range_detector.py`, `studies/lib/regime_adx.py`
- Pipelines: `data/sources/binance_universe.py`, `data/sources/binance_klines_bulk.py`, `fetch_coinalyze.py` (liquidations extension)

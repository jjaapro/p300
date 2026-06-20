# Dwell-block research — findings & record

Reverse-engineering chento's "dwellblock" entry concept and testing whether it
is a deployable edge. Self-contained record so this can be picked up later.
See also the memory note `project_dwell_block_5s_study`.

Status: **CONCLUDED — no deployable ENTRY edge for this sleeve.** A shallow ~20%
pullback looked strong in the controlled experiment, but the prod-faithful test
(§4e) shows it does NOT survive the shipped 5×ATR/6R exit model — neutral return,
WORSE max-DD/MAR. Deep bidding fails too. The dwellblock is real and detectable,
but as an entry overlay it is not a systematic edge here. **Do NOT integrate (2b).**
The controlled "edge" was an artifact of that experiment's tight-stop model.

---

## 1. What a "dwellblock" is (reconstructed)

He never defines it in text (only 3 of 1,989 messages use the word, all
2026; the real explanation is verbal in his livestreams which we don't have).
From those 3 chart-annotated messages + his vocabulary, a **dwellblock = a
horizontal price zone where price spent significant TIME consolidating** — a
Market-Profile / volume-profile high-time-at-price node, NOT a candle pattern.
He distinguishes it from an "orderblock" (SMC). Three uses, all chart-confirmed:

| Use | His words | TF |
|---|---|---|
| entry staging | "one dwell block too early, got frontran + 3% dump" | 5m |
| support that holds | "dwellblock held, refusal to go down" | ~4h |
| breakout trigger | "acceptance above this dwellblock → 84 fast" | 1D |

Charts: `studies/material/chento/images/{1490965328480698479,1496892993863553216,1500871150429929633}_*`

Dwellblocks are drawn on **5m–1D**, not 5s. So 5s is the execution lens; zone
detection runs on a higher TF.

---

## 2. Data foundation

- **`cd_spot_5s`** (prod.db) — BTC spot 5s OHLCV, built `data/sources/binance_klines_5s.py`
  from Binance Vision 1s klines (futures has NO 1s klines). 12 months, ~6.3M bars,
  same 14-col schema as `cd_futures_15m`. Used for execution-fill simulation.
- **`cd_futures_15m`** (perp, 2019→now) — used for dwellblock detection at his
  trade times (covers his full extracted history; matches his perp charts).

---

## 3. The detector — `dwellblock.py`

Time-at-price (TPO) histogram over a causal lookback; a dwellblock = a
contiguous run of price bins whose normalised time-at-price clears a threshold;
POC = peak bin. Validated against his 3 marked charts (`dwellblock_validation.png`):
for L1781 the detected POCs (69,754 / 68,663) land on the 69,737 / 68,662 lines
he had drawn — independent method, same levels.

---

## 4. Experiments

### 4a. Depth sweep — `experiment_abc.py` (→ `depth_curve.png`)
Hold the signal constant (TRIPLE_V3 = B1∩B5∩B7, the validated edge composite),
vary entry = pullback depth toward the nearest dwellblock POC. Common structural
stop beyond the zone, 3R target, 24h fill-TIF / 72h trade-TIF, fills from the 5s tape.

- On **raw B1 (noise anchor)**: ALL depths negative → an edge signal is mandatory.
- On the **edge signal**: exp/signal peaks at a **shallow ~20% pullback (+0.72R)**,
  ~2× market (+0.38R), and **declines to the full dwell-zone bid (+0.19R, 58% fill)**.
  Fill% falls faster than fill-quality rises → bidding the literal zone is too passive.

### 4b. His actual bid depth — `analyze_chento_bids.py` (→ `chento_bid_depth.png`)
For 140 extracted BTC trades, where does his real entry sit vs the detected zone?
Detector found a zone near 100% of entries. **He bids DEEP**: 45% below the zone /
36% in / 19% above; median entry ~0.9 ATR BELOW the POC; bid_depth median 1.07.
The OPPOSITE side from the sweep's shallow optimum. (Era: 2025 he bid right at the
POC; 2024/2026 deeper.) Caveat: `entry_first` is his FIRST bid only — he ladders,
so effective entry is deeper; his log is survivorship-skewed (posts wins).

### 4c. Does a ladder rescue deep bidding? — `experiment_ladder.py` (→ `ladder_compare.png`)
Test the reconciliation: bid deep + bounded DCA across the zone + structural stop;
and the stop-and-re-enter-next-support cascade (3-strike cap). On the edge signal:

| arm | fill% | meanR | exp/signal |
|---|---|---|---|
| market | 100% | +0.384 | +0.384 |
| **shallow 0.2** | 98% | +0.737 | **+0.720** |
| deep_single | 58% | +0.329 | +0.190 |
| deep_ladder | 58% | +0.215 | +0.124 |
| cascade | 58% | +0.181 | +0.104 |

**The ladder makes deep bidding worse, not better** (the DCA add lands ~0.25 ATR
above the stop → adds size right before getting stopped). The cascade barely
triggered (6/48 multi-attempt) and didn't help. `deep_single` reproduces the
sweep's +0.19 (sanity ✓).

### 4d. Equity / max-DD comparison — `equity_compare.py` (→ `equity_curves.png`)
Fixed-1R-risk equity curves per entry arm (experiment exit model):

| entry | n | cumR | maxDD_R | ret/DD |
|---|---|---|---|---|
| market (≈ current prod entry) | 83 | +31.9 | −8.6 | 3.68 |
| **shallow 0.2** | 81 | **+59.7** | **−7.9** | **7.52** |
| deep_single | 48 | +15.8 | −10.2 | 1.55 |
| deep_ladder | 48 | +10.3 | −19.7 | 0.52 |
| cascade | 48 | +8.7 | −24.7 | 0.35 |

Shallow nearly doubles return AND slightly REDUCES max drawdown (ret/DD 3.7→7.5).
Deep+ladder/cascade BLOW UP drawdown 2–3× (−8.6 → −19.7 → −24.7) — a drawdown lens
that hardens the "deep bidding is bad" verdict. NOTE: experiment exit model
(structural stop / 3R / 72h), NOT the prod 5×ATR/6R model.

### 4e. Prod-faithful comparison — `prod_compare.py` (→ `prod_compare_equity.png`)
The decisive test: replay the SHIPPED sleeve exactly (`replay_with_mae(enable_ladder=False)`,
5×ATR stop / 6R target / 72h, + no-tilt/resist-OB/OKX filters) and swap ONLY the entry.

| arm | n | meanR | WR | cumR | maxDD_R | MAR |
|---|---|---|---|---|---|---|
| prod_market (current) | 103 | +2.160 | 72% | +222.5 | **−2.4** | **92.6** |
| shallow_fallback | 105 | +2.202 | 73% | +231.3 | −3.3 | 71.0 |
| shallow_skip | 91 | +1.949 | 68% | +177.4 | −3.3 | 53.0 |

**The shallow edge does NOT survive.** Neutral return (+2.16→+2.20), but WORSE
max-DD (−2.4→−3.3 R) and MAR (93→71). Why: the prod 5×ATR stop is so wide that a
20% pullback is negligible relative to risk, so the entry improvement vanishes
while the fill-timing adds drawdown variance. The controlled experiment's tight
structural stop / 3R target is what manufactured the apparent edge.

---

## 5. Conclusions

1. **An edge signal is mandatory** — no entry trick rescues a noise signal.
2. **No dwellblock ENTRY overlay is a deployable edge for this sleeve.** The shallow
   ~20% pullback looked like ~2× edge in the controlled (tight-stop/3R) experiment, but
   under the SHIPPED 5×ATR/6R model it is neutral-to-worse (worse max-DD/MAR, §4e). The
   "edge" was exit-model-dependent — a key methodological caution.
3. **Deep "bid-the-zone" bidding fails too** — depth sweep + ladder/cascade (which worsens
   DD 2–3×) + the prior backward-only ladder verdict (`project_chento_v3_p1_ladder_verdict`).
   His deep-bid success is most likely discretionary setup-selection + survivorship + conviction adds.
4. **What IS real and reusable:** the dwellblock detector (validated vs his charts) and the
   methodology. The concept is detectable; it just isn't a mechanical entry edge here.

---

## 6. Caveats

- n=83 edge signals in the 5s window (deep arms n=48). Thin.
- 33% of signals skipped as "too far from any zone" → zone selection is weak.
- Experiments use a controlled exit model (structural stop / 3R / 72h), NOT the
  chento_triple_v3 production exit model — so 'market' approximates the current prod
  ENTRY, and market→shallow is the RELATIVE entry effect. A faithful prod comparison
  needs the prod exit model replayed.
- One ladder geometry tested (add at zone edge, tight stop) — not "no ladder works".
- TRIPLE_V3 triggers ≠ his discretionary entries.

---

## 7. Status of next steps

- **(2a) prod-faithful comparison — DONE (§4e), NEGATIVE.** Shallow entry doesn't survive
  the prod exit model → drawdown/MAR worse. **(2b) integration is NOT recommended** —
  don't add entry-overlay complexity to a working sleeve for no edge.
- The entry-mechanic line of the dwellblock study is **concluded.**
- Still-open / optional, only if revisited later:
  - The dwellblock as a **signal/filter** (not an entry overlay) — e.g. gating or sizing on
    proximity to a held zone — is untested and a different question.
  - The breakout/"acceptance-above" use (L1918) is untested.
  - His edge is most likely discretionary **setup/zone selection** — would need a different
    (selection-focused) study, not an entry-mechanic one.

---

## 8. File index (`studies/notebooks/dwell_block/`)

| file | what |
|---|---|
| `dwellblock.py` | detector + validation render |
| `experiment_abc.py` | depth sweep (signal switch: `triple`/`b1`) |
| `analyze_chento_bids.py` | his actual bid depth vs zones |
| `experiment_ladder.py` | ladder + cascade test |
| `equity_compare.py` | equity curves + max-DD per arm |
| `*.png` | validation / depth_curve / bid_depth / ladder_compare / equity_curves |
| `*_results.csv`, `equity_stats.csv` | per-trade outputs |

Re-run order: `dwellblock.py` → `experiment_abc.py triple` → `analyze_chento_bids.py`
→ `experiment_ladder.py` → `equity_compare.py`. All read prod.db; no external deps.

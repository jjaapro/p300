# S-003 ADX sleeve — accuracy & drawdown study (2026-06-26)

**Question (user):** the ADX sleeve is promising but (1) misses many entries,
(2) isn't as exact as hoped, (3) has incorrect entries that lose money — what
from our research arsenal makes it more accurate with smaller DD? Also produce
a Pine script.

**Data:** BTC daily from `cd_spot_binance` (the live signal source), 2017-08 →
2026-06, strategy window 2018-01-01. Indicator math = `strategies.support.indicators`
(same ema/adx the live service uses → signal parity by construction). IS = 2018–2022,
OOS = 2023–2026. ~34 trades total, so OOS ≈ 17 trades — **small sample; we lean on
IS/OOS sign-consistency + causal stories, not last-bp optimisation.**

Files: `harness.py` (engine), `analyze.py` (problem characterisation),
`experiments.py` (lever sweep), `funding_overlay.py` (short funding), and
`s003_adx_enhanced.pine` (deliverable).

---

## Baseline (live S-003 semantics, reproduced exactly)

`ema50` direction, asymmetric LONG-only EMA150 filter, ADX<20 exit, 10% SL.

| n | WR | PF | return | CAGR | maxDD | MAR | Sharpe |
|---|----|----|--------|------|-------|-----|--------|
| 34 | 50% | 5.20 | +2769% | +48.6% | −27.3% | 1.78 | 2.09 |

**TradingView cross-validation** (BINANCE:BTCUSDT 1D, toggles off): return +2761%,
closed maxDD 27.33%, 33 closed + 1 open trade, open +18.76%. Near-exact parity →
the Pine and the live signal are the same machine.

---

## Problem 1 — "incorrect entries that lose money" → it's the SHORTS

Direction split over 9y:

| side | n | WR | cum return | SL hits | sum price PnL |
|------|---|----|-----------|---------|---------------|
| LONG | 15 | 67% | +2035% | 3 | +473% |
| SHORT | 19 | 37% | **+34%** | 8 | +51% |

**Longs are the entire engine; shorts barely break even on price and cause most
of the −10% stop-outs.** And the losing shorts cluster ABOVE the 200D/EMA150
(fighting the primary trend): 6 of 7 *winning* shorts are below the 200D.

### …but funding rescues them (the original design was right)

The live sleeve trades perps; the Pine/TV tester is price-only. Overlaying real
funding (`cd_funding_rate`):

| short group | price PnL | funding | **net** |
|-------------|-----------|---------|---------|
| all 19 shorts | +51% | +50% | **+101%** |
| kept (< EMA150), n=12 | +82% | +17% | +99% |
| **filtered (> EMA150), n=7** | **−31%** | **+34%** | **+2%** |

So the 7 "ugly" counter-trend shorts are **funding-harvest trades, net ~flat,
not broken entries.** Part of complaint (3) is a measurement artifact: on the TV
strategy tester these show as −10% losers because Pine can't see funding income.
The enhanced Pine now draws them in **dim red** and documents this.

The real lever: those shorts still create the *price* drawdown (funding accrues
slowly; a −10% SL is sudden). So filtering them is a genuine **risk/reward trade,
not a free lunch.**

## Problem 2 — "misses many entries" → mostly structural & era-specific

Only **2 of 36** ADX≥25 events were blocked by the trend filter. The real misses
are trend *continuations* where ADX never re-compressed below 20 to re-arm
`was_low` — e.g. the entire **Sept–Nov 2021 ADX rally to the ATH** generated no
long. A shallower re-arm (ADX<22/<23) catches these **but only in-sample**: every
re-arm variant produced a byte-identical 2023–2026 trade list. The miss is
concentrated in the wild 2020–2021 tape and **does not generalise** → rejected.

## Problem 3 — "not as exact" → direction & SL are already well-tuned

- **DI/-DI direction is WORSE** than close-vs-EMA50 (return +2207 vs +2769; it
  flips the +22% Jan-2026 short into a missed long). Keep EMA50 direction.
- **Tighter SL (8%) raises DD**, wider (12%) raises DD. 10% is near-optimal.

---

## What actually works (OOS-confirmed)

| variant | n | WR | PF | return | maxDD | MAR | note |
|---------|---|----|----|--------|-------|-----|------|
| baseline | 34 | 50% | 5.20 | +2769% | −27.3% | 1.78 | live |
| **+ ATR×4 catastrophe stop** | 34 | 50% | 5.32 | +2483% | **−23.7%** | 1.97 | keeps funding shorts |
| **+ short<EMA150 filter** | 27 | 59% | 7.95 | +3945% | **−19.3%** | 2.83 | drops funding shorts |
| **+ both (filter + ATR×4)** | 27 | 56% | 6.89 | +2483% | **−15.1%** | **3.09** | best risk-adj |

TV confirms the combo: 26 closed trades, headline maxDD 17.61% (from 29.48%),
PF 4.32 (from 2.96), WR 53.9%. Both IS and OOS improve (OOS MAR 3.16).

**Rejected:** DI direction, tighter/wider SL, shallow re-arm (IS-only).

---

## Recommendation (two tiers — a portfolio judgment for the user)

- **Tier 1 — ATR×4 catastrophe stop (safe, keeps the funding carry).**
  maxDD −27→−24%, return-neutral. Doesn't touch the deliberate short-funding
  decision. Pure risk improvement.

- **Tier 2 — symmetric short trend filter + ATR×4 (bigger DD cut).**
  maxDD −27→−15%, MAR 1.78→3.09. Cost: forfeits the counter-trend-short
  funding-carry stream (~net-flat P&L, but a real diversifying carry the
  asymmetric filter was designed to keep). Choose only if DD reduction outranks
  the carry overlay.

  *Middle path worth considering:* keep the funding shorts but **half-size**
  the counter-trend (above-EMA150) ones — captures half the carry for half the
  DD contribution. Sizing lives in the orchestrator, not the signal.

**Pine:** `s003_adx_enhanced.pine` — faithful to live by default; both DD
reducers are toggles (default OFF); funding-harvest shorts drawn dim red.
Validated against live TradingView.

**No production sleeve code was changed** (per research-workflow rule). Awaiting
go-ahead on Tier 1 / Tier 2 / half-size before touching `strategies/sleeves/adx/`.

---

## Addendum (2026-06-27) — the LONG stop-outs in bull runs

User asked what's meaningful about the longs that hit SL. There are only **3 long
SL hits in 9y** (`long_context.py`):

| entry | held | what happened | MAE | tell? |
|-------|------|---------------|-----|-------|
| 2020-01-12 | ~5wk then crash | **COVID** crash | −46% (SL cut it at −10%) | none — held fine 5wk |
| 2021-03-13 | 2 days | entered on a +6.9% blow-off spike at **+82% above EMA150**, instant reversal | −11% | extreme extension (but see below) |
| 2025-10-05 | 5 days | rose +1.9%, then **Oct-10-2025 liquidation cascade** (−7.3% day, low $102k from $122k) | −17% | none — kept rising 2 days post-entry |

**Conclusion: 2 of 3 are macro crash/liquidation interruptions of valid uptrends,
not bad entries.** The entries were reasonable (price kept rising after entry); a
market-wide shock hit while long. The 10% SL did its job (COVID capped at −10% vs
a −50% unhedged path). This is the unavoidable tail cost of trend-long-with-a-stop.

**Filtering by overextension is confirmed DEAD** — winners are MORE extended than
losers: WIN ret30 +18% / xEMA50 +15.8% / RSI 74 vs LOSS +14% / +11.8% / 68. The
super-extended 2021-03-13 (+82% > EMA150) looks catchable, but 2021-02-09 was
+91% > EMA150 and WON — a parabola filter nets out useless.

**One curiosity (not a rule, n=5 post-2023):** losing longs entered at LOWER DVOL
(~38, complacency) than winners (~48). "Vol is cheap right before it isn't." Worth
watching as a possible portfolio-level low-vol-before-shock signal, not an ADX filter.

**Only real lever = a market-wide crash/liquidation circuit-breaker** (portfolio
level — `strategy_health` / `margin_sim` / liquidations feed), which would protect
ALL sleeves, not just ADX. The ATR×4 stop does NOT help these gap-crashes (Oct-10
gapped through both the 10% SL and any ATR trail on the same bar).

---

## Addendum 2 (2026-06-27) — can other sleeves' indicators give ADX HTF top/bottom sense?

User goal: stop ADX longing HTF tops (Oct-5-2025 ATH long → crash) and ideally
flag where to short. Mapped the indicators in chento_triple_v3 / chento_limit_bid /
short_squeeze (`case_study.py`, agent inventories).

### Cross-sleeve exhaustion-primitive map (reversal/top-bottom signals only)

| primitive | sleeve(s) | measures | data / history | backtestable for a daily ADX veto? |
|-----------|-----------|----------|----------------|-----------------------------------|
| CVD divergence (B1, perp-CVD, spot−perp) | triple_v3, short_squeeze | aggressive flow w/ no price follow-through = absorption/exhaustion | split-volume **only from 2026-05-18** | **NO — forward-only** (blind at Oct-2025) |
| LSR positioning extreme (B5) | triple_v3 | crowd euphoric/flushed (30d %ile) | ca_long_short_ratio 2021+ | yes, but didn't separate (Oct-2025 LSR only 10th-%ile) |
| Funding extreme | limit_bid, short_squeeze | crowded longs(+)/shorts(−) | cd_funding_rate 2019+ | **yes — the one useful lever (below)** |
| OI build / flush | limit_bid, short_squeeze | leverage parabola / capitulation | cd_open_interest 2022+ | didn't separate (winners had OIΔ +30–49%) |
| Sweep of prior-N-day extreme | short_squeeze (#5) | liquidity grab / swing failure | any OHLC | plausible, untested at daily |
| Close-in-range rejection | short_squeeze (#8) | daily candle closes weak off the high | any OHLC | suggestive (Oct-2025 CIR 0.38 vs Nov-2024 0.81) |
| Swing-base cluster + expansion | limit_bid (#1,#2) | defended low (bottom); mirror = distributed high | spot 15m, history-rich | bottom-only as built; mirror unbuilt |
| B7 multi-TF CVD; 30d-return; OKX delta; OBs/VA | triple_v3 | trend / regime / barriers | — | NOT reversal — do not use as a top veto (B7 confirms trend INTO a top) |

### Two structural reasons a backtested daily top-catcher is hard
1. **The best exhaustion signals are forward-only.** CVD divergence (the core of B1
   and short_squeeze) needs split-volume data that only exists from 2026-05-18 — it
   was blind at Oct-2025 and any pre-2026 extreme. Can confirm tops *live*, can't
   be validated historically.
2. **History-rich crowding doesn't separate a blow-off from a healthy breakout.**
   The naive "near-high + OI-building + hot-funding" composite flagged 3 ADX longs:
   2024-02-14 (+31% WIN), 2024-11-09 (+28% WIN), 2025-10-05 (SL). 2 of 3 winners.
   Early-trend and late-trend look identical on the surface.

### The one usable lever — funding-crowding LONG veto
`skip ADX long when 30d funding z-score > 1.5`. Removes 2020-07 (breakeven),
2021-02 (+5%), 2025-07 (−3%), **2025-10 (−10% SL, the target case)**.
Full +2759→+3020%, **OOS MAR 2.36→2.66**, but **headline DD unchanged −27.3%**
(driven by the short book, not these longs). Mild net-positive, clean causal story
("don't long over-crowded leverage"), avoids the Oct-2025 mistake — but it AVOIDS the
top, it doesn't SHORT it. The Oct-2025 vs Nov-2024 tell: funding z **1.53 vs 0.41**,
close-in-range **0.38 vs 0.81** (blow-off closed weak; breakout closed strong).

### Architectural conclusion
ADX is trend-CONTINUATION; at an HTF top every trend feature (incl. B7) says "long."
You cannot reliably flip it to short a top with backtested confidence on this data.
The honest path to "match tops/bottoms":
- **Now (ship-able):** add the funding-crowding long veto to ADX — small, validated,
  avoids the worst crowded-long tops.
- **Real top/bottom catching = a SEPARATE forward-validated reversal sleeve**, using
  short_squeeze's already-symmetric machinery (`is_long_macro` is computed but unused;
  sweep + close-in-range + funding/OI mirror cleanly to a swing-HIGH detector). It is
  CVD-forward-only so it must be paper-validated going forward, not backtested.
  Caveat: short_squeeze README says the *intraday* short-mirror "doesn't work" — the
  *daily* version is untested, so treat as fresh research. This matches
  [[project_portfolio_direction_2026Q2]] (microstructure setups, causal stories).
- ADX already catches downtrends well once established (the "two good shorts after",
  2026-01 & 2026-05). The gap is specifically TOPS — and that's reversal-sleeve work,
  not an ADX trend-filter tweak.

---

## Addendum 3 (2026-06-27) — TradingView top/bottom detector (no prod code)

User: experiment on TV only, no code changes. Built `htf_top_bottom_detector.pine`,
live on BINANCE:BTCUSDT 1D. Design (after iterating on the chart):
- **Mandatory price-geometry anchor** (full history): near an N-bar(180) high/low +
  rejection close-in-range + stretch from EMA150 → localizes the swing.
- **+ ≥ minConfirm microstructure confirms** from {funding z-extreme (perp
  `cryptoDerivativeMetric 'Funding Rate'`, ~238d hist), CVD divergence
  (`tvta.requestVolumeDelta`, ~69d hist), failed-breakout sweep (full history)}.

**Live iteration lessons:** binary geometry = 74 noisy tops; score≥1-on-any-single-
component = 505 labels (funding-cold alone fired bottoms everywhere). Fix = geometry
MANDATORY + ≥1 confirm → **23 labels over 9y**, at the real swings.

**Result:** Oct-5-2025 ATH fired **score-3 TOP** (`T3 fz=1.8 @ $123,218`,
`T3 fz=0.9 @ $125,708`); a **score-3 BOTTOM caught the Nov low** (`B3 @ $88,608`) —
the two swings ADX got wrong. Screenshot `adx_topbottom_final.png`.

**Caveats:** TV funding/CVD history (~238d/~69d) → pre-~Oct-2025 fires lean on
geometry+sweep only (same backtest-blindness as the Python finding) = forward/visual
tool, not a historical backtest. Tops fire well in uptrends; BOTTOMS sparse (geometry
rarely sees a 180-bar low + strong close). Pine **v6 required** (tvta/req libs).
NEXT when ready: backtest this as an ADX long-veto / standalone reversal entry — needs
Python (a code change), deferred per the TV-only instruction.

---

## Addendum 4 (2026-06-27) — the 180-day-extreme anchor was top-only; rebuilt on PIVOTS

Exact validation (`pivot_detector.py`) confirmed the Addendum-3 detector was badly
lopsided: **26 tops (2020-2025) vs only 2 bottoms (both Nov-2025 / Jun-2026)**. Root
cause = the "within 4% of the 180-DAY high/low" anchor only catches the single ABSOLUTE
cycle high; blind to (a) the lower-highs inside a downtrend that the ADX shorts fade,
(b) real bottoms (capitulation closes weak → fails strong-close). It missed the 2020
COVID and 2022 $15.5k cycle bottoms entirely, and flagged NO top before either winning
ADX short (2026-01-12, 2026-05-31).

**Rebuilt on SWING PIVOTS** (`pivot_top_bottom_detector.pine`, on chart as "ADX Pivot
Top/Bottom Detector"): confirmed local pivot high/low (`ta.pivothigh/low`, L=8/R=8) +
≥10% prominence; funding-z + rejection-close only SCORE the pivot (NOT required — the
+22% Jan-2026 short's preceding top 2025-12-09 had zero confirms). Validated:

| event | 180-day version | pivot version |
|-------|-----------------|---------------|
| SHORT 2025-02-09 (+14%) | top 2024-12-17 | tops 2025-01-07/20 ok |
| SHORT 2026-01-12 (+22%) | NONE | **top 2025-12-09** ok |
| SHORT 2026-05-31 (+18%) | NONE | **top 2026-05-06** ok |
| 2020 COVID bottom | missed | **2020-03-13** ok |
| 2022 $15.5k cycle bottom | missed | **2022-11-09/21** ok |

Now symmetric (~13 tops + ~13 bottoms/yr = real swings, ~monthly). Caveats: pivots
confirm R=8 bars LATE (markers drawn back at the pivot bar) → a visual swing MAP, not
a same-bar trigger; a tradeable ADX overlay must handle the lag (act on the pivot
break, or use a smaller R). Tunable live via L/R/prominence/minConfirm. The funding
"fz" + T1/T2/T3 score rank conviction (Feb-2026 capitulation `B3 fz=-3.5`, Oct-2025
ATH `T3 fz=1.8`).

---

## Addendum 5 (2026-06-27) — which confirms make pivots fewer + accurate (ADX? CVD?)

Forward-accuracy test (`pivot_accuracy.py`, `cvd_divergence_test.py`): for every
pivot, fwd-30-bar move; TOP good if price LOWER after, BOTTOM good if HIGHER.

**Real CVD IS backtestable** — `cd_futures_15m` has true taker volume_buy/sell 100%
since 2019-09 (the "~69d" limit is only TV-Pine's `requestVolumeDelta` proxy; chento
B1 already uses this DB column; short_squeeze README's "added 2026-05-18" is wrong).
CVD divergence tested on 6.5y:

| confirm | TOPS (hit / reversal) | BOTTOMS (hit / reversal) |
|---------|----------------------|--------------------------|
| all pivots (baseline) | 66% / +4.5% | 72% / +11.5% |
| **CVD divergence** | **75% / +5.7%** (vs 59% without) | 78% / +15.9% (rare n=9) |
| **ADX rolling over** | does NOT help tops | **74% / +14.8%** (n=43) |
| funding extreme | 67% / +6.1% | 67% / +11.7% |
| CVD div + funding/ADX | 67% / **+11.1%** (n=9) | 80% / +17.6% (n=5) |

**Answers:** ADX was NOT in the detector; validated as a BOTTOM confirm only (74%;
no top edge). CVD divergence = best TOP confirm (75% vs 59%), rescues tops with no
other tell (Jan-26 short's top 2025-12-09 now `T1 cvd`). Dropped near-neutral
close-in-range (64%).

**Detector v2** (`pivot_top_bottom_detector.pine`): pivots scored by {CVD div,
funding, EMA-stretch}=tops / {ADX-roll, CVD div, funding}=bottoms; minConfirm=1 =>
confirmed = big triangles (the ~74-75% subset), unconfirmed = small dots. Raise to 2
for the rarest/strongest (CVD+funding tops +11%, CVD+ADX bottoms +18%). CHART caveat:
CVD uses TV's ~69d proxy so that confirm only lights recent bars; the 75% is the
real-CVD Python number a live/Python overlay would use.

# Chento material, filed 2026-09-19 — a community comment and his TradingView screen

Two sources the user brought on 2026-09-18/19, decoded against what `strategy_spec.md` already holds. Nothing here
is a test; §4 says what each source changes and what it does not.

## 1. Source: his comment in a trading community (undated, 2026-09)

Fifteen numbered points, paraphrased closely (his numbering; there is no 11):

1. "I will almost always full margin in."
2. "Risk what you can lose … this is a challenge, meaning it is hard, fast and risky. Risk is not a factor for me in
   the first 1 or 2 trades, I will HAMMER them till liquidation if needed. Why? I can easily lose this amount and make
   double or more back trade after, because that is my style."
3. "SL is mostly a placeholder UNLESS defined otherwise."
4. "My closing is an advice … I will sometimes hold or have side trades; when that is the case I will credit balance."
5. (community: he chooses the challenge amounts.)
6. "I'm not always able to update — InnerCircle, X, VIP, YouTube. **I stream DAILY.**"
7. Partial closes are **on the running amount, not on the starting amount**: 100 → close 25 % → 75 running → close
   20 % → 60 → close 10 % → 54 → close 20 % → 43.2 running.
8. (nothing is financial advice.)  9. (don't ask for money.)
10. "If the entry is close, chances are I will market in, not letting stuff go on pennies — within 0.1–0.2 % ish. If
    a limit, it will be clearly visible and defined."
12. "I will define if not full margin, by leave room for DCA; assume 50 % max goes in then."
13. (exchange screenshots show leverage and margin.)
14. "Assume leverage is 20 or 25×, mostly 20× unless defined. I manage multiple positions from a central account."
15. "Trading is no exact science, I trade on experience and vibe, combined with my system — 300 days a year, 14 h a
    day for 8+ years. Hard to copy."

## 2. Source: his TradingView, mobile, BTCUSDT perpetual 5-minute

**When.** Header 76,291.0 (+121.1, +0.16 %), phone clock 12:33, axis date "17". Pinned on `btc_1m`: 2026-09-17,
~11:30 UTC (the 11:33 UTC bar closed 76,310; his 12:33 is therefore UTC+1 or +2). 2026-04-17 also prints 76,29x but
is ruled out: that morning had already traded 74,529, which the chart would show and does not (its visible day range
is 76,055–76,774, matching 09-17).

**What is on the screen.** Nine indicators loaded (the "∨ 9" chip); one panel open, tab "CUSTOM":

| Readout | Value | Meaning |
|---|---|---|
| 10× / 25× / 50× / 100× | 0 / 20 / 20 / 12 | liquidation levels drawn per leverage tier; 10× off |
| Active, Long / Short | 52, 29 / 23 | the 52 dotted horizontal segments across the chart |
| HTF Levels | OFF | |
| Nearest Cluster / Cluster Density | NONE / — | no cluster inside the zone width of current price |
| Liq Imbalance | BALANCED | 29 vs 23 |
| CVD Bias | SHORT | a CVD readout, direction only |
| Bounce Rate | 60.0 % (50 events) | the indicator scores its own level sweeps |
| **Data / Regime** | **PROXY \| NORMAL** | **the liquidation levels are estimated, not measured** |
| Zones / Piv | ATR×0.3 / 5 | zone half-width; pivot lookback the levels are projected from |

A **Sweep Log** table: SHORT 2 BOUNCE, SHORT 1 BOUNCE, SHORT 1 CASCADE, LONG 2 BOUNCE, SHORT 4 BOUNCE, LONG 1 BOUNCE —
each sweep of a level cluster classified afterwards as reclaimed (bounce) or continued (cascade).

Cyan price labels at 79,856.5 / 79,055.1 / 78,386.8 / 77,478.7 / 76,522.6 / 75,733.6 / 73,213.3 (pivot levels); a
shaded box 78,400–79,000; a white line at 74,486.6.

**His drawing.** A hand-drawn white path: up from ~75,900 into the 78,400–79,900 box, several swings there, then
straight down to the white line at 74,486.6. The classic "take the liquidity above, then dump" plan: target the far
cluster, invalidation beyond the near one.

**Scored against `btc_1m` from 11:30 UTC 2026-09-17:**

| Leg of the drawing | What happened |
|---|---|
| reach 78,386.8 | 2026-09-18 09:03 UTC (+21.5 h) |
| reach 79,055.1 / 79,856.5 | 09-18 13:40 / 13:47 UTC — the whole box swept inside 5 hours |
| reverse to 74,486.6 | **never** (through 2026-09-19); 48 h high 81,400, 48 h low 76,260 |

The grab happened as drawn; the dump did not — price went through his box to 81,400, a cascade in his own
indicator's terms. One call, no timeframe attached, so it is a data point and not a verdict. What it demonstrates is
that a dated screenshot with a drawing on it is scoreable in one query, which the brainstorm asked for: "a dated list
of his calls, scored against price, is evidence."

## 3. What the user reports about the streams

Most of each stream is talk with a co-host and one or two trades. He never shows the inputs; it is a chart, and the
decision is made in his head before the order. Reviewing them properly would be weeks.

## 4. What this changes

**New, and worth acting on.**
- **The partial-close arithmetic (point 7) is a spec.** Multiplicative trims: retained fraction after trims f₁…fₙ is
  ∏(1 − fᵢ); his example 25 / 20 / 10 / 20 % leaves 43.2 %. This is the mechanic the partial-TP arm in BACKLOG §2.2
  needs. **Missing: the triggers** — which R-multiple or which event fires each trim. `strategy_spec.md` has "first
  TP at 1R, partials after, 10–30 % when conviction wavers"; the trigger is discretionary as far as anything shows.
- **Points 2, 3 and 14 are the return profile, in his words.** Full margin at 20× with the stop as a placeholder and
  liquidation as the real stop, restarted from a bankroll outside the challenge. A 5 % move against is the account.
  Challenges liquidated on trade one are not posted as "10k → 100k in two weeks"; the survivors are. This closes the
  question of how the posted runs happen: it is the sizing policy, and it is survivorship by his own design.
- **Point 6, he streams daily** — the only route to the trim triggers and to the eight remaining indicators. Not
  worth a broad review (§3); worth a targeted one (§5).

**Confirmed.**
- Point 12 → our A4 **Tier 2**: initial 50 % of margin, DCA the other 50 %, so the add equals the initial position
  (100 % add, the Balanced tier we chose). His base mode (point 1) is full margin with no add; the ladder is his
  exception mode.
- Point 10 → our taker execution model: he markets in within 0.1–0.2 % of the level. Consistent with E2 (a limit at
  the last price fills but improves nothing).
- The TV liquidation tool is the **estimated map** — pivots projected to 25/50/100× — and its own panel says
  **PROXY**. That is the object the liqmap study built and dissolved on 2026-09-18 (touch rates explained by distance
  once the control level is matched on distance). His **60 % bounce rate** is the number a distance-matched placebo
  produces on a 5-minute chart with ATR×0.3 zones; it is not evidence of a level effect.
- The projection is the "target = next cluster, invalidation = beyond the near cluster" rule already in
  `strategy_spec.md`.

**A caveat introduced.** Point 4: posted closes are not his closes. The +0.33 R the sleeve was matched against comes
from communicated trades; his actual P&L includes unposted holds and side trades. The comparison stands as
"sleeve ≈ his communicated per-trade edge".

**Consequences elsewhere.**
- The collector's liquidation *prints* are not what is on his screen; his screen runs on a proxy. That removes
  "replicate chento" from the collector's justifications (BACKLOG §5 item 9).
- The Sweep Log's bounce/cascade classification is the union of things already tested: E2 rejection (no exit
  information), liqmap touch (distance), and Paladin's short fresh-high sweep-fade (real, small). Not a new object.
- Point 15 is him agreeing with the v3 lookahead conclusion: the discretionary part is not copyable.

## 5. What to extract from the streams, and only that

1. **Trim moments.** For each trade he trims on stream: entry price and time, the R at each trim, the fraction, and
   what he says as he does it. Ten to twenty trades would let the partial-TP arm use his triggers instead of a guess.
2. **The drawing moments.** When he draws a projection: which levels he picks as the box and the target, and what
   is on the other eight indicators at that moment if any is visible.

Bounded to the one or two trades per stream he actually makes, that is days, not weeks. Anything beyond it is the
"experience and vibe" he says himself cannot be copied.

## 6. Source: his exchange positions screen, 2026-09-18 ~06:20 UTC

Perp app, USDT-M, one cross-margin account, hedge mode ("Cross Merge" = merged fills). Phone clock 08:22; the long's
open time (2026-09-16 10:47:07) pins his clock to UTC+2 (the 08:47 UTC bar closed 75,553 against his 75,523 entry).

| | Long | Short |
|---|---|---|
| Opened (his clock) | 2026-09-16 10:47 | 2026-09-18 05:13 |
| Effective leverage | 45× | 40× |
| Notional | 751,835 USDT | 1,275,577 USDT |
| Entry | 75,523.30 | 77,443.80 |
| Margin | 16,707 | 31,889 |
| Unrealized at screenshot | +19,577 (+117 % ROE) | −758 (−2.4 %) |
| TP / SL | — / 74,522.1 | (cut off) |

Mark 77,482.60. Both legs show the same estimated liquidation, **86,423.7 — above the market**, because liquidation
is at account level and the account is net **short** 523,742 USDT. Gross 2.03 M on 48.6 k of margin, margin ratio
13.88 %. Effective leverage 40–45×, above the "20×, sometimes 25×" of his comment (§1 point 14).

**Scored on `btc_1m` (Binance) from the screenshot forward.** The long's SL was never hit (lowest since: 77,402).

| At | Price | Long | Short | Pair | Long alone |
|---|---|---|---|---|---|
| screenshot | 77,483 | +19,505 | −639 | **+18,866** (app: +18,870) | +19,505 |
| 09-18 high | 81,400 | +58,503 | −65,163 | **−6,660** | +58,503 |
| last bar, 09-18 21:08 UTC | 81,245 | +56,959 | −62,609 | **−5,650** | +56,959 |

If he held the pair, the "hedge" turned +19 k into −6 k while the unhedged long went to +57 k. If he cut the short on
the way up, he lost less; one screenshot cannot say which. The account survives either way (liquidation 86,424).

**What this is, mechanically.** The user's reading: "when there is uncertainty he hedges; we do not do this at all."
Two corrections, one of them to the premise:

1. *This is not a hedge.* A hedge is sized at or below the position it protects and caps profit; this short is 1.7×
   the long. At the screenshot the pair is economically a net short of 524 k with the long left on as an option to
   un-hedge if the pullback fails. "Uncertainty" resolved upward, and a net short in a +5 % day costs what it costs.
   The only thing the hedge structure buys over "close the long, open a short" is the un-hedge decision — which is
   the discretionary part again — at the price of funding on both legs and two extra fee cycles.
2. *We do not hedge, but not for lack of testing.* B13 (2026-05-29): the opposite-direction leg at chento's triggers
   wins 5 % of the time at −0.98 R; every hedge variant lower annual R, deeper drawdown, lower MAR (single direction
   MAR 17.5, best hedge 6.0). Reactive hedge (2026-06-09: open the opposite leg instead of stopping, unwind on a
   bounce): neutral at best, most cells deepen drawdown, because the sleeve strings almost no losses so there is no
   tail to insure. Range-formation hedge: −1.34 R, 9 % WR, 1 of 380 events both legs win. All three are on record in
   `validation_B13_hedge.py` and `studies/notebooks/hedge_tests/`. The 2026-09-14 review rates them as negative
   *screens* with accounting gaps (no joint collateral account, R-sums not NAV), not proof — so a specific hedge
   question could be reopened; a general one should not.

**What is untested, and where it fits.** Our tests hedged at the trigger (B13) or when the trade went *against*
(reactive). His screenshot is the third case: hedging a *winner* on a counter-signal, instead of taking partial
profit. On a fixed-target sleeve, hedge-and-hold on a winner is a partial close minus costs; hedge-and-unwind is a
partial close plus a re-entry, and its value lives entirely in the unwind rule. That makes it a variant of the
partial-TP arm already in BACKLOG §2.2 — "partial hedge vs partial close, same fraction, same trigger, scored on the
unwind rule" — not a new study. B13's line already says it in one sentence: hedge-and-hold ≡ a tighter stop,
hedge-and-unwind = the opposite leg.

## 7. The user's proposal (2026-09-19): HTF direction, LTF trades against it

"Consider the overall bullish or bearish regime, take a higher-timeframe long or short, then trade against it on
the lower timeframe to minimise risk — e.g. ADX long from the 77k area, and lower-timeframe shorts at each
resistance. I think this is what he usually does."

**The live instance, scored.** ADX *is* long: SJ-4247, since 2026-08-22 at 78,328 (stop 70,495), +3.7 % at 81,200.
Chento's resistance box (78,387 / 79,055 / 79,857) was swept on 09-18 and price ran to 81,400. Every LTF short at
those levels is under water now: −3.46 % / −2.64 % / −1.65 % at 81,200, and the best any of them ever saw was +0.19 %
on the topmost one at the 79,708 pullback. The one chento short we can score (§6) is the same trade and lost the
same way.

**What the repo has already measured on this structure.** The chento regime filter's per-direction table
(2026-05-29, 92 H_B trades, 5.4 y) is the closest direct test of "LTF shorts during an HTF uptrend":

| 30-day regime | LTF longs | LTF shorts |
|---|---|---|
| up_30d (> +10 %) | +3.14 R / 84 % WR | **+0.67 R / 55 % WR** — removed from the sleeve |
| flat | +3.90 R / 83 % | +3.50 R / 67 % |
| down_30d (< −10 %) | n = 4 | **+6.02 R / 92 % WR, DD −0.06 R** |

Shorting the LTF extreme *against* a strong up-regime is the worst cell in the table and was cut from production
("skip only shorts in up_30d", MAR +53 %); shorting it *with* a down-regime is the best. The stated mechanism:
after a strong rally, shorts get squeezed. Three more results say the same: Paladin's fresh-high sweep-fade — the
one automatable counter-trend short found anywhere — carries the note "his shorts lost when fighting trend" and a
regime gate in its candidate spec; the OI study found a short flush is *continuation*, so a short into a squeeze
fades the wrong way; B13's opposite leg at chento triggers wins 5 % of the time.

**So the data inverts the proposal.** The money in the regime table is in trading the LTF *in* the HTF direction at
its pullback extremes — HTF long + LTF longs on dips, HTF short + LTF shorts on bounces — which is roughly how the
fleet's gates already work (squeeze_bull bull-gated, short_squeeze macro-gated). The against-trend leg is the losing
leg, and it is the leg his screenshot shows losing.

**On "to minimise risk".** Reducing exposure at resistance is right; a counter-short is the expensive way to do it.
A partial close at the same level reduces the same exposure with zero expected loss and one fee cycle; the
counter-short reduces it at the expectancy of shorts-in-up-regime (+0.67 R here, negative in his instance) and two
fee cycles. The only thing the short adds is the un-hedge decision, which is discretion. This makes the proposal a
comparator inside the §2.2 partial-TP arm — at a resistance trigger on an open winner: do nothing / partial close /
counter-short of the same fraction — not a new study, and the prior says the partial close wins.

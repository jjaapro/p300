# What passive execution costs — and whether MEXC maker fees reopen intraday

Study 21. 2026-09-10. Triggered by a fee schedule, not a signal: MEXC quotes
futures maker 0.010% / taker 0.040% and spot maker 0.000% / taker 0.050%,
against the 0.05%/side taker assumed everywhere else in this repo.

On fee arithmetic alone that appeared to reopen the 1-4 hour band. It does not,
and this document is the measurement that settles it.

**The prior this replaces.** [LIQUIDATION_FINDINGS](LIQUIDATION_FINDINGS.md)
measured resting bids at **-10bp** and that number has been quoted since as the
cost of passive execution. It is not an adverse-selection number: it is a
strategy return, entry to exit, net of fees, over 2-4h holds, at levels chosen
for liquidation memory. It conflates adverse selection with the strategy's own
directional result. The general-context number is **~1.0bp per leg**, an order
of magnitude smaller — but the fee saving it unlocks is smaller still.

**Method note.** `bookTicker` was discontinued ~2024-03, so the touch cannot be
observed and must be reconstructed from the tape. All three implementations do
this differently and bound the error explicitly; the reconstruction over-states
the quoted spread by roughly 2-3x, which is why the distance sweep is safe and
the 0bp point is not.

---

## What passive execution actually costs

### 1. The headline number, and what the reviewers did to it

**Adverse selection on a passive BTC fill at the touch is ~1.0 bp per leg under a realistic queue assumption, one-sided, and it is a one-off entry tax rather than a bleed.** The defensible range is 0.2 bp (front of queue) to 3.0 bp (behind 20 BTC), and essentially the entire range is queue position — nothing else moves it.

Three independent implementations were run against the same 89-90 days of Binance BTCUSDT aggTrades (2026-06-10 to 2026-09-07). Net markout per passive fill at the touch, in bp, one-sided:

| queue ahead | event study (`run_advsel.py`) | flow study (`run_advsel_flow.py`) | third implementation (written from spec) |
|---|---|---|---|
| front of queue | −0.42 / −0.49 | −0.34 | −0.22 @1m, −0.16 @60m |
| ~1 BTC | −1.03 / −1.06 | −1.05 | −0.95 @1m, −0.71 @60m |
| 5 BTC | −1.64 | −2.02 | −1.64 @1m, −1.51 @60m |
| 20 BTC | −2.93 | −3.73 | −3.04 @1m, −2.77 @60m |
| last in queue ("through") | −1.06 | −1.34 | −1.16 @1m, −0.90 @60m |

**The measurement is not in dispute.** The third implementation reproduces the event study to within 0.1 bp at every queue rung; the flow study runs ~1.3x hot but stays well inside a factor of two. What looked at first like a large disagreement between the two original studies — event study "−1.18 bp at the touch", flow study "+0.27 bp at the touch" — was entirely a mismatch of queue assumption at the same distance rung, not a measurement conflict. Matched on queue, they agree.

**What was refuted.** All three adversarial reviewers independently found the same fatal arithmetic error in the flow study's decision layer: its required-capture figures (12.5% @1h, 6.3% @4h central; 19.8% @1h, 10.0% @4h pessimistic) are each **exactly 2x too generous**. It scaled the brief's 8.3%/4.2% baseline by cost/4bp instead of recomputing 2×cost/σ, silently dropping the factor of 2 already embedded in the repo's "edge > 2× round trip" screen — and in doing so shifted one horizon doubling, so its quoted @1h numbers are in fact the correct @4h numbers. **Its headline conclusions "the 4h horizon stays inside the 3-13% band under every queue assumption" and "at 1h it is a queue-quality question, not a microstructure question" are both false and are struck.** The event study's required-capture table is arithmetically correct as printed but carries a mislabel — it reports "σ = 24.1 bp @1h / 47.6 bp @4h", which is half the true σ (48.1 / 96.1 bp under the repo's own σ(T)=2.355%·√T) because it absorbed the 2x screen factor into σ. That mislabel is harmless inside its own table and **must never be quoted onward as σ.** Everything below uses σ(1h)=48.1 bp, σ(4h)=96.1 bp (in-window realised: 41.1 / 81.8 bp).

**Two things must not be quoted as achievable.** (a) The front-of-queue cells (−0.22 to −0.49 bp). 37.5% of those fills occur inside 200 ms and are stale-quote crossings that would be taker executions in reality. (b) The 0 bp / at-touch rung carries ±0.1 bp of definitional ambiguity: bookTicker ended ~2024-03, so the touch is a tape proxy whose implied spread (0.235 bp median day) over-states the true quoted spread by 2-3x against a Roll estimate of 0.079 bp.

**Structural results both studies establish and no reviewer challenged:**
- **Adverse selection does not compound with holding time.** 73% of it lands within 1 second of the fill, 97% within 10 seconds, and the markout curve is then flat from 1 minute to 4 hours (at touch: 0.27 → 0.30 → 0.33 → 0.37 bp front-of-queue; −1.18 → −1.13 → −1.18 → −1.13 bp under "through"). It is also permanent — no reversion, so holding longer never earns it back. **This is the entire mechanical reason 4h fares better than 1h: the same fixed ~1 bp per leg amortised against twice the sigma.**
- **Resting longer does not cost more.** Flat at the touch across 1m-60m lives; away from the touch a longer life *reduces* it (2.11 → 0.71 bp at 5 bp out) by diluting fast toxic fills with slow benign ones.
- **Posting away from the touch never helps.** The event study finds gross drift flat at −1.2 to −1.4 bp at every distance; the flow study finds it 4-6x *worse* away from the touch at matched front-of-queue. Either reading gives the same instruction: distance buys nothing and costs fill rate (88% → 23% at 300 s).
- **96% of the price improvement a passive backtest books does not exist.** At a 10 bp limit the assumed improvement vs the mid at post time is +10.03 bp; the improvement that actually exists at fill time is +0.44 bp. This is a *larger* error than adverse selection itself, and it silently inflates every passive backtest in this repo — including the `run_restbid` family, which rests 50-200 bp below spot and credits itself the full distance.

**A correction the repo owes itself.** `run_final.py` line 36 (`r = side*(ex/e-1)*100 - side*UNC[hold] - 0.10`) subtracts a 10 bp round-trip fee *before* reporting. The "−10 bp maker execution penalty" quoted at STATUS.md lines 43 and 331 is therefore the fee, not adverse selection: gross of fees those results are −0.2 bp and +0.1 bp. Comparing that −10 bp against a fee saving double-counts the fee. Maker execution is currently marked DEAD on that arithmetic and should not be.

### 2. Net edge after BOTH fees and adverse selection

Adverse-selection charge applied: **1.0 bp per maker leg on futures** (the converged "through / ~1 BTC ahead" case; a taker leg pays 0). MEXC spot at 0% maker is charged **1.4 bp per leg** — the measured Binance-spot number (1.38-1.46 bp), which is a *floor* for MEXC's thinner book, not an estimate of it. All-in round trips: **spot mk/mk 2.8 bp** (0 fee + 2×1.4), **fut mk/mk 4.0 bp** (2 + 2×1.0), **fut mk/tk 6.0 bp** (5 + 1×1.0), **fut tk/tk 8.0 bp** (8 + 0).

| effect (gross, horizon) | capture % of σ | spot mk/mk **2.8bp** | fut mk/mk **4.0bp** | fut mk/tk **6.0bp** | fut tk/tk **8.0bp** | clears 2× screen? |
|---|---|---|---|---|---|---|
| ofi5, 0.5 bp @5m | 3.6% | **−2.3** | **−3.5** | **−5.5** | **−7.5** | no, at any tier |
| tint5, 1.8 bp @15m | — | **−1.0** | **−2.2** | **−4.2** | **−6.2** | no, at any tier |
| best 3-signal combo, 2.5 bp @1h | 5.2% | **−0.3** | **−1.5** | **−3.5** | **−5.5** | no, at any tier |
| 1h contrarian, 1.7 bp @2h | — | **−1.1** | **−2.3** | **−4.3** | **−6.3** | no, at any tier |
| post-cascade reversion, 10.9 bp @4h | 11.3% | +8.1 | +6.9 | +4.9 | +2.9 | spot & fut mk/mk only — **but see note** |
| mean reversion, 21 bp @2d | — | +18.2 | +17.0 | +15.0 | +13.0 | all four |
| %R swing, 23.8 bp drift-adj @63h | — | +21.0 | +19.8 | +17.8 | +15.8 | all four |

"2× screen" is the repo's own gate (edge ≥ 2× round-trip cost): needs 5.6 / 8.0 / 12.0 / 16.0 bp gross respectively.

**Three caveats that do most of the work in this table:**

1. **The post-cascade row is the one where the 1.0 bp charge is wrong.** Resting into a cascade means resting in the stressed bucket, where measured adverse selection at 4h is **−2.66 bp on futures (t −2.7) and −4.91 bp on spot (t −3.5)** — 2.4x and 3.5x the general case. Recharged honestly: spot mk/mk 9.8 bp all-in → **net +1.1**; fut mk/mk 7.3 bp → **net +3.6**; mk/tk 7.7 bp → **net +3.2**; tk/tk 8.0 bp → **net +2.9**. **It then fails the 2× screen at every tier** (needs 14.6-19.6 bp). The only effect in the repo big enough to survive the fee arithmetic in the 1-4h band is precisely the one whose adverse selection is worst, and passive execution buys it only ~0.7 bp over paying taker. Note also that the stressed bucket is 1% of fills, n=1,079, t=−2.7 — read it as "meaningfully worse, magnitude uncertain".

2. **The two rows that pass comfortably pass on the wrong axis.** The 21 bp @2d is the repo's *intraday reversion budget* — the total excess mean reversion that **exists** over 48 bars, an upper bound, not an edge anyone captured; the oracle test (bucket by actual future VR, fade extremes) loses −0.158% in the most mean-reverting quartile, t=−3.78. The 23.8 bp %R swing is drift-adjusted at t=+1.65 full sample, TRAIN +2.36 → **TEST −0.43**, with live TradingView confirmation on the 15m variant at PF 0.818. **Cheaper execution multiplies a positive out-of-sample edge; it does nothing to a negative one.** No fee tier and no fill model rescues either row.

3. **Queue sensitivity, applied to any maker/maker cell:** add 2×(AS − 1.0) bp. Behind 5 BTC that is +1.2 to +2.0 bp of extra round trip; behind 20 BTC, +3.8 to +5.4 bp. On spot the queue penalty is 2-3x worse than futures (front −0.35 → q5 −4.92 → q20 −9.63 one-sided). **MEXC's 0% spot maker tier is worth nothing unless you are near the front: at 5 BTC ahead the spot round trip is 9.8 bp, a wash with paying Binance taker, and at 20 BTC (19.3 bp) it is worse.**

### 3. Avoidable, or a fixed tax? — a fixed tax

This is the practically decisive question and the flow-conditioned analysis answers it cleanly: **you cannot see the toxic fill coming from the trade tape.**

Out-of-sample ex-ante toxicity quintiles (rank-average of 10 s pre-fill pressure share, pressure notional, trade count, mid range; marginals and cut points fit on days 1-45, applied to days 46-90):

| quintile | AS @15m | AS @60m |
|---|---|---|
| Q1 calmest | +0.57 (t 2.2) | +0.07 (t 0.1) |
| Q5 most one-sided | +0.58 (t 1.7) | +2.42 (t 1.9) |
| **Q5 − Q1** | **+0.01 (t 0.02)** | **+2.35 (t 1.57)** |
| top 1% tail | −4.91 (t −2.2) | +7.82 (t 1.2) |

Non-monotone at 15m, monotone-ish but insignificant at 60m, and the extreme tail flips sign between horizons. Pulling quotes on observable pre-trade flow would forfeit fills without materially improving markouts.

The mixture decomposition settles it: at 15m, **6.3% of fills are top-5% sweeps costing +1.70 bp and 93.7% are ordinary fills costing +0.75 bp, blended +0.81 bp** (spot: 6.4% at +1.94, 93.6% at +0.99, blended +1.05). The bill is carried by the ordinary 94%, not by a small toxic minority you could design around. That is the definition of a fixed tax.

Counter-intuitively, the *biggest* sweeps are not the toxic ones — the >p99 contiguous same-side run (>$2.5M in ≤3 s) shows AS of −0.12 bp at 15m and −4.39 bp at 60m, i.e. the mid comes back. Toxicity peaks in the p50-p99 range (+1.4 to +2.0 bp). n=50-167, |t|<1 — suggestive only. If true, resting into cascades is a directional bet on reversion, not an adverse-selection problem, which is consistent with the repo's own +10.9 bp post-cascade reversion.

**The only levers that move the number, in order:** (1) **queue position — a factor of 6**, and it is unobservable from the tape; (2) **venue** — spot is 35-50% worse at the touch than perp and 2.6x more queue-sensitive; (3) **the stressed-volatility tail at long horizons** (−2.66 bp at 4h vs −1.13 calm). Everything else tested — mid proxy, touch definition, resting time, distance, sample construction, fill-detection rule beyond the touch — moves it by less than 0.1 bp.

### 4. Verdict on the 1h-4h band

Required capture = 2 × round trip / σ(T), the repo's own screen (STATUS.md lines 128-129/213), which reproduces "11.1 h minimum viable hold at 4 bp, 5% capture" to 3 s.f. σ(1h) = 48.1 bp, σ(4h) = 96.1 bp; in-window realised σ in brackets (41.1 / 81.8 bp).

| queue ahead | MEXC fut mk/mk round trip | required capture @1h | @4h |
|---|---|---|---|
| front of queue *(not achievable)* | 2.4-3.0 bp | 10.0-12.5% [11.7-14.6%] | 5.0-6.2% [5.9-7.3%] |
| ~1 BTC ahead | 3.9-4.2 bp | 16.2-17.5% [19.0-20.4%] | 8.1-8.7% [9.5-10.3%] |
| 5 BTC ahead | 5.3-6.0 bp | 22.1-25.0% [25.8-29.2%] | 11.0-12.5% [13.0-14.7%] |
| 20 BTC ahead | 7.9-9.5 bp | 32.9-39.5% [38.4-46.2%] | 16.4-19.8% [19.3-23.2%] |

Against the repo's measured price-prediction capture band of **3-13% of horizon sigma, clustering near 5%**:

**1 HOUR IS CLOSED.** Under every queue assumption a real order can obtain. It needs 16-20% behind 1 BTC and 22-29% behind 5 BTC, against a band whose *ceiling* is 13%. Even the unobtainable front-of-queue case needs 10.0-14.6% — the top of the entire measured range. The repo's actual 1h candidate, the best 3-signal combo, captures 5.2%. Not close.

**4 HOURS IS CONDITIONAL, on two things simultaneously.** (a) **Queue position of ~1 BTC or better**: 5.0-7.3% at the front, 8.1-10.3% behind 1 BTC (inside the band), 11.0-14.7% behind 5 BTC (sitting on or over the ceiling), 16.4-23.2% behind 20 BTC (closed). (b) **An edge in the top half of the 3-13% band**: a 12-13% capture survives at any queue better than 20 BTC; a 5% capture — the cluster, where most of this repo's measured effects actually live — survives only at front-of-queue, which is not achievable.

So the honest statement is: **the fee arithmetic moves 4h from "closed" to "conditionally open for the top of the measured range", and moves 1h not at all.** The binding constraint is queue position, which is unobservable and which nothing in this data can settle. Note what this does *not* rescue: STATUS.md line 31's conclusion holds unchanged for the 0.5-2.5 bp microstructure edges — the effective MEXC maker round trip is 2.9-5.3 bp of real cost, not 4 bp of fee plus 10 bp of selection, and 2.9 bp still exceeds 2.5 bp before the 2× screen is even applied.

### 5. What is still unmeasured, and would change the answer

1. **Queue position on MEXC.** The single largest gap. It moves the round-trip answer from 2.7 to 9.5 bp on futures and 0.7 to 19.3 bp on spot — a wider range than the effect being measured — and no amount of aggTrades analysis closes it. **The two-week live minimum-size quoting experiment on MEXC (post real orders, record your own fill latencies against the tape) is the only thing that settles the deliverable**, and it is the one action item every study and every reviewer converged on. It is already STATUS.md line 335's cheapest test.
2. **Binance data, MEXC execution.** Every number here is Binance. MEXC's book is thinner: wider spreads, shorter queues (good), more toxic flow per unit of resting size (bad). **Treat 1.0-1.4 bp as a floor for MEXC, and plan against the q5-to-q20 column, not the front-of-queue column.**
3. **Conditional adverse selection.** Everything measured is *unconditional*. A maker who posts only when an S-003/S-005 signal is live is posting at systematically different times. The whole premise of a passive entry on a 1-4h signal is that the signal is *anti-correlated* with the flow that fills you — the optimistic case, and the one nobody has measured. This is the measurement that actually decides whether the shipped strategies can go passive, and it is the natural next step after the live queue test.
4. **The real exit leg.** Both sides were measured and are symmetric to within 0.1 bp at short horizons, so a maker-maker round trip is charged at 2×. But no exit was simulated — an exit has a position to close, a time constraint and a different fill urgency, and if it degrades to taker the whole thing dies (1 bp maker in + 4 bp taker out + 1× AS ≈ 6 bp reopens nothing at either horizon).
5. **One regime, 90 days, and a trending one.** BTC +28.2% over 2026-06-10 to 2026-09-07. Buy/sell averaging and per-record signed drift subtraction handle the trend structurally, and the 1s-15m rows are load-bearing (both sides independently negative, |t|>3). But the 1h and 4h rows are differences of two large per-side numbers (+4.24 / −6.51 bp at 4h) and depend on clean cancellation. Defend "it has not decayed by 4h", not the third decimal. Ninety days also cannot see a 2022-style downtrend or a liquidity crisis — exactly the conditions in which adverse selection blows out.
6. **The extreme tail is unresolved.** The top-1% toxicity bucket swings from −4.91 bp (t −2.19) at 15m to +7.82 bp (t 1.15) at 60m on n=60-158. That is where `run_restbid`'s −10 bp lives. The tail is fat and roughly ±5-10 bp; its sign is not established.
7. **No repricing, no cancels, no latency, no size.** Every simulated order is infinitesimal, posted once, never partially filled, never moves the book, never causes anyone to pull a quote. A real maker re-quoting continuously has a different fill mix — probably worse at the touch, since chasing the bid up means filling more often on the tick that turns.
8. **Two loose ends worth closing before anything here is quoted elsewhere.** Re-run `run_restbid2` crediting `mid_at_fill − P` rather than `spot_at_post − P`; at only 10 bp out, 96% of booked improvement already evaporates, and at 50-200 bp out most of the restbid family's −10 bp is likely this mirage rather than toxicity. And reconcile the aggressive-sell share discrepancy (49.8% futures / 48.0% spot by trade count here vs 48.6% / 43.8% in the brief) — nothing above conditions on it, but the two figures should not both be in circulation.

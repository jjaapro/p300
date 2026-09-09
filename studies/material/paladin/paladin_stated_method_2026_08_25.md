# Paladin — stated method, in his own words (Discord, ~2026-08-24 night)

Primary source: Paladin directly answering questions about coin selection, entries, and stops.
Captured by user (Nics asking), pasted 2026-08-25. This is the only first-person description of
his mechanism we have; everything else in this folder is reverse-engineered from trade cards.

## Transcript

**[Nics]** How does @Paladin choose what coins to trade and what is proper Entry, TP and SL?

**[Paladin]** Trendy coins usually I trade like big caps or total shitcoins I have a group with
my buddies we find, shitcoins. SL and TP easiest way is you put SL slightly down below support
on the time frame you're trading and TP before the resistance. For example I'm trading BTC on
4h and 62k is support and 65k resistance. I would set SL at 61.5-61.7k, TP at 64.7k.

**[Nics]** So how about your latest trade with UNI; it was sliding down, why this point in
time, why did you expect it to test resistance again?

*(Context: latest trade idea was Short UNI, Entry 4.404 CMP, DCA 4.44 (30%), TP 4.28, SL 4.546)*

**[Paladin]** Big caps usually move with BTC right? BTC was pumping at almost 80k, ETH pumping
too like all the following ALTs pumping but UNI was weak. 80k was expected to reject so if BTC
came down the weak ALTs will just dump hard. UNI couldn't even pump on lower time frame when
everything was pumping so if BTC dumped it would dump easy. Same with other ALTs like ENA, DOGE
or SUI. In most cases when coins aren't able to pump when BTC or majority of ALTs are pumping
when BTC dumps they dump harder. And SL was last daily high, if you don't know how to use SL
easiest way is use last daily low or high, 24 high or low whatever your exchange shows a bit
lower. And vice versa, rn PENGU is showing strength if BTC pumps from here you can expect a
strong pump on it too because BTC is dumping and it's holding. BTC.D pumping is weakness in
ALTs. And when I shorted BTC.D was pumping.

**[Kaykoo]** What do you use for the resistance levels?

**[Paladin]** I open 4h on which I usually trade and just see the resistance visually also I
use emas so ema's do act as resistance or an easy prediction where the candles will reject.

## Mapping to study findings (paladin_study + scanner_study, both concluded)

| His statement | Our measurement | Verdict |
|---|---|---|
| SL = last daily high/low, a bit beyond | Stops are a volatility multiple (Spearman 0.75 to 1h ATR); daily extreme IS a vol multiple | Confirmed, already modeled |
| TP before resistance on 4h, visual + EMAs | H0: his levels traded mechanically = +0.00–0.07R | Confirmed noise; edge was exits/ledger |
| Short the laggard when BTC rejects (UNI/ENA/DOGE/SUI) | Cross-sectional RS predicted his SELECTION, never outcome (scanner B); sweep-fade short below cost floor (scanner A) | Selection story confirmed; outcome edge already falsified via H0 on his own picks |
| Long the RS-holder when BTC bounces (PENGU) | Scanner B RS-dump long: gross −0.14 to −0.26R everywhere | Tested directly, dead |
| BTC.D pumping = alt weakness gate | Same RS-vs-BTC family; subsumed by cross-sectional ranks | No new information channel |
| Coin universe = "trendy" big caps + buddy-group shitcoins | Social/attention selection, not automatable | Out of scope by construction |

## Delta vs. what we tested

The only combination not run verbatim: **laggard-weakness SHORT conditional on a BTC rejection
trigger** (scanner B tested the long mirror; scanner A shorted fresh-high pumpers, not laggards).
However H0 already scores his actual laggard shorts (UNI-type included) at ~0R mechanical, and
the RS metric was shown to predict selection-not-outcome, so the prior is poor. Do not reopen
without a materially new ingredient (per scanner_study conclusion).

His UNI card arithmetic: entry 4.404, TP +2.8%, SL −3.2% — sub-1:1 R:R before DCA, ~1:1 after
the 30% DCA at 4.44. Consistent with the finding that reported win rate is manufactured at the
exit and ledger, not by entry quality.

## Outcome (2026-08-24)

BTC rejected 80k and fell to 78459 (−1.9%); UNI fell through the 4.28 TP (~3× downside beta)
and beyond. Paladin booked **+214.38%** (per card timestamped 2026-08-24 22:06). Arithmetic:
214.38% ÷ 75x = 2.86% price move = entry 4.404 → ~4.28, i.e. the published TP at his standard
75x. In R terms this is ~+0.87R (2.8% gain vs 3.2% risk). He runs CROSS margin (per user),
so no liquidation cliff — the 75x only inflates the card's ROI denominator; account risk is
his stated 2% on alts. In account terms (user's calc): SL −2.00%; TP +1.75% without DCA
(0.87R), +2.05% if the 30% DCA fills (1.03R). Break-even WR: ~53.5% without DCA, ~49.3% with.
User reconstructed the ROI card image trivially from public info — such cards carry zero
evidentiary value on their own (cf. measured accounting bias: 35 vanished trades, mark-price
cards read as closes).

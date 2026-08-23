# Does the material show how Paladin chooses his entries?

Short version: **it shows *where* he enters with high precision, and *why* only about
40% of the time — and almost never *what triggered it at that moment*.** For an
automation project that distinction is the whole problem, so this note quantifies it
and corrects one earlier claim that was overstated.

Measured over the 181 messages that carry a trade call, the 214 call/entry events, and
the 217 reconstructed positions.

---

## 1. What is fully recorded

**The order, always.** Direction, entry price, stop, and usually a target and a DCA
level. 180 of 200 taken positions carry a published stop, 118 a published target,
57 a published DCA. This part is complete and unambiguous.

**The size grading, usually.** 156 of 217 positions carry an explicit risk note —
56 "half risk", 22 flagged as risky or a gamble, 16 small size. He grades conviction
*before* the outcome, in his own words, on roughly 3 of every 4 trades. This is a real,
recorded decision variable and it is machine-usable as a confidence weight.

**The entry mechanism.** 168 of 217 positions say CMP or current market. He almost
never rests a limit order. **The price is therefore not the decision — the moment is.**

## 2. What is only partly recorded

**The reason.** Stripping the order block (Long X / Entry / TP / SL / DCA / notify)
out of each call message and looking at what remains:

| what the call message contains | count | share |
|---|---|---|
| nothing but the order block | 59 | 33% |
| a risk grade ("half risk", "gambling here") | 54 | 30% |
| an instruction to followers ("you can enter at DCA") | 40 | 22% |
| **anything reason-like about the market** | **51** | **28%** |

Only 39 of 182 calls are preceded by his own view or plan on the same asset within
12 hours. Combining both routes, **roughly 40% of entries have any recoverable "why"
anywhere near them; about 60% are an order and nothing else.**

**And when he does give a reason it is directional bias, not a trigger.**
Representative of the whole set:

> "Risky trades because BTC could retrace more but I'm expecting 80s to hold" (msg 7)
> "I'm bullish on BTC until the weekend now let's stay till 24-25th." (msg 176)
> "SOL looks alot better to long from CMP but we did ETH so be it." (msg 93)

That tells you which way he is leaning over days. It does not tell you what made
14:32 the moment rather than 11:00.

**The exception — 50 conditional statements.** Scattered through the log he does
occasionally publish a real trigger before the fact, and these are the highest-value
rows in the corpus for reverse-engineering:

> "4h close above 80.5k and I would…" (msg 33)
> "If BTC doesn't hold here we go 76k, the idea for long would be Limit Long BTC: 76850 / TP: 80-82k / SL: 75k" (msg 131)
> "LTF looks bearish so waiting for entry, I would like to use the same setup as before, 79k entry, 78k DCA, 77k SL" (msg 43)

Filter `events` for `event_type == 'plan'` — 96 rows, of which 50 contain an if/when/
wait/close condition. Those 50 are the only places he states a rule in advance.

## 3. What is not recorded at all

No chart markup (23 of 320 images are charts, and most carry no user drawings), no
indicator values at the moment of entry, no watchlist, no screen recording of the
decision, and no statement of what he was looking at in the minutes before he posted.
The trigger is simply absent from the text.

## 4. Correction: the round-number finding was overstated

I earlier reported that **97% of his entries sit within 0.25% of a round level**. That
threshold was too loose — at BTC's scale it admits anything within $190 of a $1,000
level, which is 38% of the number line. Tested properly against a uniform null:

| granularity | share in the nearest quarter | null | verdict |
|---|---|---|---|
| thousands (BTC 76,000) | 27% | 25% | **no effect** |
| hundreds (BTC 76,100) | 51% | 25% | real but modest, ~2× |
| tens (BTC 76,110) | 73% | 25% | strong — but this is quoting precision |

The explanation is visible in the digits. His **quoted** entries are almost all
3-significant-figure numbers (114 of 194); his **actual fills** carry 4 to 7
significant digits. He rounds when he writes, and fills wherever the market is —
median gap between quoted and filled price is 0.13%.

**Use it as a weak prior, not a rule.** There is a genuine ~2× pull toward the finer
round levels, which is worth one feature. There is no evidence he waits for the big
round thousands.

## 5. What this means for the automation work

The recorded decision is *direction + risk grade + stop distance*. The unrecorded
decision is *timing*. So the productive question is not "which of his stated signals
predicts a trade" — he barely states any — but:

> **What did the chart look like in the 1–4 hours before he posted, compared with a
> random hour on the same symbol?**

That is a supervised classification problem you already have the labels for: 173
backtestable `signal_time_utc` stamps as positives, and matched random timestamps on
the same symbols, same hour-of-day distribution, as negatives. Fit something
interpretable — logistic regression or a shallow tree on ATR-normalised features —
and the coefficients are the trigger he never wrote down. Anything the model finds
can then be checked against the 50 conditional statements above as an independent
holdout: if the model says "recent sweep of a local low" and msg 43 says "LTF looks
bearish so waiting", that agreement is worth more than either alone.

Two cheap sanity checks before modelling: his signals cluster 08:00–15:00 UTC and only
20% land on a weekend, so any feature correlated with time of day will look predictive
for the wrong reason — match the control set on hour and weekday. And 62 of 173
backtestable positions are BTC, so pool with care or the result is a BTC model wearing
a general-purpose label.

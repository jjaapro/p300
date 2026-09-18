STATUS: STAGE A DONE 2026-09-15 — EXPLORATORY (describes, decides nothing). Stage B not started. No production change.

# squeeze_bull top anatomy — findings (stage A)

**One-line answer.** What happens just before a squeeze_bull bounce reverses is a quick push to a new high on a
premium pop and a burst of taker buying. It looks the same at the high that turns out to be the top and at the earlier
highs that kept going, so nothing seen in real time marks the top. What does change is the state: after a day without
a new high, or once price has run well past where the flush started, the rest of the trade is worth about the bull
regime's normal drift. And **SJ-4250 should not have been a trade**: it fired only because the live open-interest feed
has carried each hour's *opening* value since 2026-06-10.

Protocol: [PROTOCOL_TOP_ANATOMY.md](PROTOCOL_TOP_ANATOMY.md) (manifest `results/top_anatomy/manifest_A.json`,
2026-09-15 16:31:29 UTC; Amendment A1 manifest 16:34:59 UTC). Review notebook:
[04_squeeze_bull_top_anatomy.ipynb](04_squeeze_bull_top_anatomy.ipynb), which recomputes the tables and matches the
saved run. All numbers come from one discovery set of 122 fires and are hypotheses, not results.

## 1. What was measured

- **Fires.** The 122 bull fires, followed on the Binance BTCUSDT perp 1-minute path for 7 days after entry,
  regardless of stop or time stop.
- **Shape.** A spike is +2 % from entry. The top is the highest point before the first 2 % fall from the peak. A false
  top is an earlier new high followed by 30 minutes without a higher one.
- **Features, each known at its minute.**
  - Price repair: flush-sizes above entry, where 1 = back at the price the flush started from.
  - Open-interest repair: 1 = the flushed open interest is back, from the Binance 5-minute archive.
  - Premium index; top-trader and all-account long/short ratios.
  - 60-minute taker buy share and volume ratio; order-book ±1 % imbalance.
  - Minutes since the last new high; hours since entry.
- **Case.** SJ-4250, followed through 2026-09-15 16:25 UTC using public REST data for the last day.

## 2. How the bounces went

| | |
|---|---|
| Spiked +2 % and then fell 2 % from the peak | **103** of 122 |
| Never reached +2 % (7-day low median −6.8 %) | 16 |
| Reached +2 % and did not fall 2 % | 3 |
| Hours from entry to +2 % | median 18 (quartiles 6 – 44) |
| Hours from +2 % to the top | **median 2.0** (a quarter within 12 minutes; quartiles 0.2 – 11) |
| Top | median **+3.2 %** above entry (quartiles +2.5 – +4.9 %), 28 h after entry |
| Tops after 48 h, where the time stop had already closed the trade | **35 of 103** |
| Fall in the 24 h after the top | median −3.2 % |
| Highest price within 7 days | median +5.9 %: after the first 2 % fall, price often came back higher |

## 3. Just before the top

Compared with the same fire's earlier paused highs, on the 56 fires that have both:

| At the high | Final top − earlier highs (median, 95 % interval) |
|---|---|
| premium index | +0.7 bp (−0.9, +2.5) |
| taker buy share, 60 min | +0.003 (−0.005, +0.012) |
| volume ÷ 7-day normal, 60 min | −0.21 (−0.51, +0.20) |
| order-book ±1 % imbalance z (48 fires) | −0.10 (−0.33, +0.09) |
| top-trader position long/short, % since entry (51) | −0.2 (−0.6, +0.5) |
| **all-account long/short, % since entry** | **−5.0 (−6.9, −3.9)** |
| **open-interest repair** | **+0.35 (+0.13, +0.61)** |
| **price repair** | **+0.91 (+0.78, +1.25)** |
| **hours since entry** | **+6.2 (+4.7, +8.7)** |

The only differences are in quantities that grow as a bounce ages and rises: the top comes later and higher, with more
open interest rebuilt and more accounts having sold into the rise. Flow, volume, book and premium do not differ. The
same-fires overlay (Amendment A1) looked for a build-up in the hours before: volume into the final top runs slightly
lower, but no interval from 15 to 240 minutes before excludes zero. About 60 comparisons were made, so one marginal
interval (premium at −120 minutes) is expected by chance.

## 4. Is the top where the flush is repaired?

No.
- **Price went past the repair level.** 87 % of tops were beyond the price the flush started from, and the median top
  was 2.3 flush-sizes above entry.
- **Bounce size barely depends on flush size** (rank correlation 0.19). Bounces run to about +3 % whether the flush
  was 0.9 % or 2 %.
- **Reaching the repair level was not the end.** In 91 % of bounces price got back to the flush start before the top.
  The next 24 h from that moment still averaged +0.80 % (median +0.40 %), against the bull regime's normal +0.21 %.
- **Open-interest repair does not mark the top.** At the top, a median of half the flushed open interest was back,
  with quartiles from −0.3 to 1.7.

## 5. "Something else that tells it's time"

At every hour for 7 days after each fire (20,496 states), the move over the next 24 h, by state. The bull-regime
drift is +0.21 %.

| State | Next 24 h, mean % (95 % interval) |
|---|---|
| last new high less than 1 h ago | **+0.51** (+0.22, +0.79) |
| last new high 1 – 6 h ago | +0.39 (+0.16, +0.61) |
| last new high 6 – 24 h ago | +0.36 (+0.14, +0.57) |
| **no new high for over 24 h** | **+0.12** (−0.09, +0.33) |
| price repair 0.5 – 1 | +0.48 (+0.18, +0.81) |
| price repair 1 – 1.5 | +0.39 (+0.03, +0.74) |
| **price repair above 1.5** | **+0.13** (−0.12, +0.38) |
| open-interest repair, any bin | +0.19 to +0.42, no pattern |

The bounce's extra return lives in its fresh phase. Once it stalls for a day, or has run well past the flush, holding
is worth about what any bull-regime hour is worth. That is lower, but not negative, so an exit there would cut
exposure rather than avoid losses.

## 6. The case, SJ-4250

- **Entry.** 2026-09-11 18:00 at 77,493.9, after price fell 2.2 % from 79,208 in four hours.
- **Before the time stop.** Price sagged for almost three days, and the entry minute stayed its high. Open interest was
  fully back by 09-13 13:34, before the time stop closed the trade at 17:00.
- **The spike.** Price crossed +2 % on 09-14 18:19, reached the flush start at 18:46, paused at 18:48, and topped at
  20:23 at +2.68 % (repair 1.14). It fell 2 % by 00:20 and reached 75,697 (−2.3 %) on 09-15.
- **Positioning into the spike.** Open interest fell from 2.0× the flushed amount at 06:15 to 0.5× at the top, and the
  all-account long/short ratio went from +13 % to −20 %: the rise was sold into and covered, not bought with new longs.
- **No exit here would have caught it.** It went 56 h without a new high before the spike, so "exit after 24 h without
  a new high" would have left on 09-12 at 18:00, a day before the time stop. Only holding with no time exit at all
  would have caught the spike, and then it gave the gain back within hours.

## 7. A production data issue found on the way

The hourly open interest the live bots read (`cd_open_interest.oi_close`) changed meaning on **2026-06-10**, the day
CoinDesk's feed died and the Binance fetcher replaced it. Until 06-09 the value stamped at an hour matched the archive's
open interest at the end of that hour, as it had since 2022-06. From 06-10 it matches the start of the hour: one bar
staler than the price bar it is joined to. The migration check ("values match within 0.1 % at the seam") could not see
a one-hour shift.

What it did:
- **SJ-4250 is an artefact of the shift.** Its trigger bar's 4-hour open-interest change was −2.48 % on the stored
  values but −1.79 % at bar closes, short of the −2 % trigger. On the convention the strategy was researched and
  validated on, it would not have fired.
- **The fires differ.** Since 06-10, the sleeve's own code gives bull fires on 2026-09-04 14:00 and 09-11 17:00 with
  the stored values, and one fire on 09-04 13:00 with bar-close values.
- **The research ledger is affected too.** Its post-June rows were built from the same table.
- **short_squeeze** uses the same table for its Asia open-interest change.
- **Two archive months** (2022-03 and 2022-04) match the archive at no lag.

Nothing was changed. The fix is a production change (store the bar-close value under each bar's stamp, then backfill
from 06-10) and needs the user's go-ahead.

## 8. What it permits, and stage B

Nothing in production. By the protocol's section 6, one candidate meets the bar:

- **"Exit after 24 h without a new high"**, in place of the fixed 48 h time stop. Its mechanism reading is that the
  bounce's momentum is gone. Its one number (24 h) is taken from the state map above and counts as fitted. It would fire
  on every fire.
- **"Price more than 1.5 flush-sizes above entry" is not forwarded.** Its mechanism reading, that the bounce repairs the
  flush, failed here: bounce size does not scale with the flush.
- **The parameter-free repair exits are not forwarded either.** Both come too early.

Stage B would test the stall exit on ETH long flushes and BTC flat/bear flushes, none of which stage A touched, paired
against the shipped exits and the no-stop twin with the squeeze_bull arm's accounting. Its pre-registration would
be written first. Expectations should be modest: the state map says the stall exit cuts exposure at drift-level value;
it does not dodge losses.

## 9. Limitations

- **Sample.** One discovery set of 122 fires, clustered in 2023–2024. Hourly states overlap within and across fires,
  and the bootstrap resamples fires, so intervals are optimistic.
- **Definitions.** The spike (+2 %), reversal (2 %) and pause (30 minutes) are definitions chosen before any number;
  other choices would shift the timing statistics.
- **Metrics lag.** Archive metrics are used from their stamp + 5 minutes, which equals the Binance REST stamp; the real
  publication delay is not measured.
- **Book coverage.** The order book exists from 2023 only (48 of 56 fires in the comparison).

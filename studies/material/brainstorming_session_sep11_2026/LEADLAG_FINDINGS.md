# Do equity indices lead Bitcoin?

Tested 2026-09-09. BTCUSDT (Binance) against NQ=F, YM=F, ES=F (Yahoo), plus
cash ^NDX / ^DJI / ^GSPC on the daily horizon. Futures used for intraday
because they trade ~23h and so avoid a session-overlap artifact.

## Answer: no. If anything the causality runs the other way.

### 1. Co-movement is real and large

| Pair | 5m contemporaneous | Daily same-day |
|---|---|---|
| NQ / BTC | **+0.375** | +0.443 |
| ES / BTC | **+0.369** | +0.479 |
| YM / BTC | +0.255 | +0.392 |

Similar in RTH (+0.387) and outside RTH (+0.372), so it is genuine
co-movement, not an artifact of when both markets happen to be open.

**But contemporaneous correlation carries no information about the future.**
It says BTC and equities react to the same things at the same time.

### 2. There is no equity -> BTC lead at any granularity tested

Granger F, equity predicting BTC beyond BTC's own lags:

| | L=1 | L=3 | L=5/6 | R2 gain |
|---|---|---|---|---|
| NQ -> BTC (5m) | 0.46 | 1.43 | 1.59 | +0.084pp |
| ES -> BTC (5m) | 0.34 | 1.90 | 1.85 | +0.097pp |
| NQ -> BTC (1m) | **0.00** | 0.06 | 0.23 | +0.022pp |
| ES -> BTC (1m) | 0.25 | 0.30 | 0.30 | +0.029pp |

Critical values are 6.63 / 3.78 / 2.80. Nothing comes close. Lagged
cross-correlations sit inside +/-0.03 at every lag from 5 to 60 minutes.

### 3. BTC leads equity futures -- significantly, but trivially

At 1-minute bars, Granger BTC -> equity:

| | L=1 | L=3 | L=5 |
|---|---|---|---|
| BTC -> NQ | **14.62 SIG** | **5.59 SIG** | **6.86 SIG** |
| BTC -> ES | **12.01 SIG** | **4.70 SIG** | **5.74 SIG** |

Cross-correlation at k=-1 (BTC leading by one minute): +0.054 NQ, +0.040 ES.

This is the opposite of the hypothesis, and it is consistent across symbols
and lag lengths. Plausible reason: BTC never closes and has no circuit
breakers, so it prices shared macro news marginally first.

**Economically it is nothing.** The R2 gain is +0.285pp. Predicting 0.3% of
the variance of the next minute does not pay a spread.

### 4. Conditional test: does BTC follow LARGE equity moves?

The only thread with any promise. Measured as excess over the unconditional
BTC drift (essential -- BTC trended up hard over the window), with the
tradable quantity being the up-minus-down spread.

27 cells tested (3 symbols x 3 horizons x 3 thresholds). **One cleared t=2:**

```
Dow fut, >1.5sd move, 30min hold:  spread +0.0673%  t=+2.56
```

At 27 tests, one t=2.56 is what chance produces. Everything else came in at
t = 0.15 to 1.94.

### 5. Even the best cell does not survive costs

Long-after-up / short-after-down, 2.5sd trigger:

| Config | gross/trade | t | net @0.05% | net @0.10% | trades/yr |
|---|---|---|---|---|---|
| Dow, 30min | +0.0550% | +1.89 | **+0.0050%** | −0.0450% | ~1,780 |
| NQ, 60min | +0.0593% | +1.49 | **+0.0093%** | −0.0407% | ~1,920 |
| Dow, 15min | +0.0201% | +0.94 | −0.0299% | −0.0799% | ~1,780 |
| NQ, 15min | +0.0153% | +0.90 | −0.0347% | −0.0847% | ~1,920 |

The best case is +0.005% to +0.009% per trade at maker fees, on a t-stat
under 2, requiring ~1,800 trades a year. That is not an edge; it is a
rounding error with a fee bill attached.

### 6. Daily horizon: mild mean reversion, not momentum

| | eq[t-1] -> btc[t] | Granger F | crit |
|---|---|---|---|
| Nasdaq-100 | −0.087 | 1.39 | 4.61 |
| Dow Jones | −0.133 | 3.48 | 4.61 |
| S&P 500 | −0.102 | — | — |

All three negative -- a strong equity day is followed by a mildly *weaker*
BTC day. None significant. The sign is at least consistent across indices,
which makes it worth re-testing on a longer sample, but it is contrarian to
the hypothesis, not supportive.

## What to actually take from this

**For prediction: nothing.** There is no future information in equity prices
about BTC at any horizon from 1 minute to 1 day.

**For risk: quite a lot.** A contemporaneous correlation of 0.37 intraday and
0.44-0.48 daily means equities are a genuine shared risk factor. Practical
consequences:

- A macro event that moves equities will move BTC *at the same time*. There
  is no window to react in between. Position before, not during.
- BTC exposure is not diversified against an equity book. On the day it
  matters, they move together.
- FOMC on 2026-09-16 will hit both simultaneously. Any plan that assumes
  equities telegraph the move to crypto is assuming something the data says
  does not exist.

## Reproduce

```bash
python -m scalp_lab.run_leadlag    # cross-correlation + Granger, 5m
python -m scalp_lab.run_leadlag2   # 1m granularity, conditional, daily
python -m scalp_lab.run_leadlag3   # baseline-controlled conditional + costs
```

# scalp_lab

A backtest harness for short-horizon BTC setups, built to be hard to fool.

## Why it exists

An earlier ad-hoc test of sweep-and-reclaim showed +0.22R on 15m bars with
hindsight-labelled regimes. Re-run on 5m bars with a causal regime filter it
became −0.02R. Re-run again with a proper train/test split, it went negative
out of sample. Three passes, three different answers — all from the same idea.

This package exists so that does not happen again.

## The four rules it enforces

1. **Causality.** Any value at bar `i` depends only on bars `0..i`. Fractal
   levels return the index at which they became *knowable*, not where the
   extreme printed.
2. **Chronological train/test.** First 60% train, last 40% test. Never random —
   random splits leak, because adjacent trades share market conditions.
3. **Search correction.** The sweep prints a Bonferroni t-threshold for the
   number of combinations searched. Clearing t=2 after 54 tests means nothing;
   the bar is ~3.3.
4. **Costs are first-class.** `fee_curve()` shows edge as a function of
   round-trip cost, because on a ~0.4% median stop, 0.10% of fees is 0.25R.

## Layout

```
data.py       kline fetch + on-disk cache, spot-delta helper
signals.py    causal regimes (EMA/vol), causal levels (fractal/session), setups
backtest.py   path simulation, management schemes, per-trade stats
study.py      parameter sweep, out-of-sample confirm, fee curve, bucket breakdown
run_study.py  end-to-end study runner
```

## Management schemes

`simulate()` takes a dict:

```python
{"target": 1.5,            # full exit at 1.5R
 "first_target": 1.0,      # scale 50% at 1R ...
 "first_frac": 0.5,
 "breakeven_after_first": True,   # ... then stop to breakeven
 "adverse_bars": 6,        # cut if not in profit after 6 bars
 "trail": 1.0,             # trail by 1R from the best price
 "horizon": 48}            # hard time stop, in bars
```

## Reading the output

`amb %` is the share of trades where a single bar's range spanned both stop
and target — the engine resolves those stop-first. **If `amb` is near zero,
finer bars would not change the result.** In the 5m BTC study it came out at
0%, which retires the "we need 5-second candles" question for backtesting at
these stop distances. Finer data still helps *execution* (maker fills), which
is a separate matter, and matters a lot given the fee sensitivity.

`***` means |t| > Bonferroni threshold. `*` means |t| > 2 — nominally
significant, but not after correcting for the search.

## Results, 365 days (BTCUSDT, 5m, 2025-09-09 -> 2026-09-09)

**This is the definitive run. 1,156 long and 1,102 short setups.**

| Setup | Regime | n | Expectancy | t |
|---|---|---|---|---|
| long sweep-reclaim | **BEAR** | 796 | **−0.095R** | **−2.54 \*** |
| long sweep-reclaim | BULL | 108 | +0.117R | +1.42 |
| long sweep-reclaim | CHOP | 252 | **−0.130R** | −1.97 |
| short sweep-reject | BEAR | 93 | −0.010R | −0.11 |
| short sweep-reject | **BULL** | 782 | **−0.102R** | **−2.75 \*** |
| short sweep-reject | CHOP | 226 | −0.069R | −0.97 |

**The CHOP edge does not exist.** On 90 days it read +0.163R (long) and
+0.025R (short). On 365 days both are *negative*: −0.130R and −0.069R. A
result that changes sign when you quadruple the sample was noise, and the
90-day number was the noise, not the signal.

Out of sample, nothing worked. Best long config: TEST −0.174R (t=−2.67).
Best short config: TEST −0.025R. One config printed +0.013R on test —
indistinguishable from zero, and expected from a 54-way search.

**Conclusion: sweep-and-reclaim has no tradable edge on BTC over a year,
in any regime, at any of the management schemes tested.**

## Results, 90 days — kept only to show the trap

| Setup | Regime | n | Expectancy | t |
|---|---|---|---|---|
| long sweep-reclaim | all | 280 | −0.099R | −1.70 |
| long sweep-reclaim | **BEAR** | 186 | **−0.209R** | **−2.92 \*\*\*** |
| long sweep-reclaim | BULL | 34 | +0.014R | +0.12 |
| long sweep-reclaim | CHOP | 58 | +0.163R | +1.20 |
| short sweep-reject | all | 282 | −0.116R | −2.00 \* |
| short sweep-reject | BULL | 197 | −0.149R | −2.15 \* |
| short sweep-reject | CHOP | 65 | +0.025R | +0.21 |

**Out of sample, every tuned configuration failed.**

```
LONG   best train +0.211R  ->  TEST −0.246R
SHORT  best train +0.240R  ->  TEST −0.158R
```

### What actually survives, on both samples

Only the negative findings replicate, and they are the useful ones:

- **Do not buy sweep-reclaims in a BEAR regime.** 90d: −0.209R (t=−2.92).
  365d: −0.095R (t=−2.54). Same sign, significant in both, n=796 on the year.
- **Do not sell sweep-rejects in a BULL regime.** 90d: −0.149R (t=−2.15).
  365d: −0.102R (t=−2.75). Same sign, significant in both, n=782.

That is 1,578 of 2,258 setups covered by two rules that both say *don't*.
Fading a trend loses money reliably. Everything else is noise. A harness that
only ever produces negative results is still doing its job — this one saved a
live account from a strategy that read +0.22R on the first, worst test.

### The cost wall

Both sides die between 0.05% and 0.10% round-trip:

```
CHOP long   fee 0.00% +0.170R | 0.05% +0.022R | 0.10% −0.127R
CHOP short  fee 0.00% +0.184R | 0.05% +0.081R | 0.10% −0.023R
```

Any version of this that pays taker fees on both legs is not a strategy.

## Usage

```bash
python -m scalp_lab.run_study                 # 90d of 5m
DAYS=365 INTERVAL=5m python -m scalp_lab.run_study
```

## Known limits

- Single symbol, one venue, ~90 days by default. CHOP subsets are n≈60.
- No slippage model beyond a flat `slip_pct`; no funding cost on perps.
- Regime gate (EMA200 + slope) was chosen, not searched. Some of the CHOP
  result may be that choice fitting this window.
- Levels are 1H fractals only. Session levels and round numbers are
  implemented but not yet swept.

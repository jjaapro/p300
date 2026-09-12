# The combination matrix (backlog #2)

**Result: combinations do not stack. Best is 53.8% win rate / 2.5bp edge
against a 5-10bp fee.** The target from RATIO_FINDINGS was ~57%.

## Why it was worth running

Everything univariate failed on PROFITABILITY, not learnability. Combining
weak signals raises the win rate, which raises edge while leaving per-trade
noise roughly unchanged -- improving both screening axes at once. Three
INDEPENDENT 52% signals agreeing would give ~56%, i.e. 8.4bp on a 0.7% move.

The whole argument rests on independence. It does not hold.

## Step 1: the correlation matrix

| | ofi_fast | ofi_slow | ofi_vslow | mom_rev | mom_rev_slow | intensity | liq_dens | casc_recent | regime_up |
|---|---|---|---|---|---|---|---|---|---|
| ofi_fast | 1.00 | **0.53** | 0.30 | 0.33 | 0.18 | -0.02 | -0.01 | -0.00 | -0.16 |
| ofi_slow | 0.53 | 1.00 | **0.57** | **0.50** | 0.32 | 0.04 | -0.01 | 0.02 | -0.25 |
| ofi_vslow | 0.30 | 0.57 | 1.00 | 0.21 | 0.46 | 0.01 | -0.01 | 0.01 | -0.32 |
| mom_rev | 0.33 | 0.50 | 0.21 | 1.00 | **0.50** | 0.03 | 0.00 | 0.04 | -0.29 |
| mom_rev_slow | 0.18 | 0.32 | 0.46 | 0.50 | 1.00 | 0.04 | 0.01 | 0.04 | **-0.51** |
| intensity | -0.02 | 0.04 | 0.01 | 0.03 | 0.04 | 1.00 | 0.02 | 0.27 | -0.01 |
| **liq_dens** | -0.01 | -0.01 | -0.01 | 0.00 | 0.01 | 0.02 | 1.00 | 0.03 | 0.02 |
| **casc_recent** | -0.00 | 0.02 | 0.01 | 0.04 | 0.04 | 0.27 | 0.03 | 1.00 | -0.02 |

Nothing exceeded the 0.70 drop threshold, so nothing was collapsed -- but the
predictive cluster sits at 0.5-0.57, nowhere near independent.

## Step 2: singles

| feature | edge% | t | win% |
|---|---|---|---|
| ofi_slow | +0.0073 | **+2.05** | 51.5% |
| ofi_fast | +0.0064 | +1.79 | 51.6% |
| mom_rev | +0.0021 | +0.57 | 51.7% |
| ofi_vslow | +0.0012 | +0.33 | 50.9% |
| casc_recent | -0.0020 | -0.55 | 49.7% |
| liq_dens | -0.0048 | -1.33 | 49.5% |
| intensity | -0.0055 | -1.54 | 49.2% |
| regime_up | -0.0009 | -0.26 | 48.3% |

## Step 3: combinations underperform independence, every one

| combination | n | actual win% | independence predicts |
|---|---|---|---|
| ofi_fast+ofi_slow+mom_rev | 6157 | 52.7% | 54.7% |
| ofi_fast+ofi_vslow+mom_rev | 4736 | 52.8% | 54.2% |
| ofi_fast+ofi_slow+ofi_vslow | 5586 | 52.2% | 53.9% |
| ofi_slow+ofi_vslow+mom_rev | 5716 | 52.3% | 54.1% |

Filtering to agreement cuts n without raising edge proportionally, so the
t-statistics collapse: best combination t=+0.77 in train against t=+2.05 for
the best single feature.

## Step 4: out of sample

| | train | TEST |
|---|---|---|
| ofi_fast+ofi_slow+mom_rev | +0.0044% (t=0.66) | +0.0248% (t=3.08) |
| ofi_fast+ofi_slow+ofi_vslow | +0.0041% (t=0.59) | +0.0166% (t=2.00) |

Test BEATS train, which argues period-dependence rather than overfitting.
But t=3.08 is under the Bonferroni bar of 3.2 for 120 combinations, and
2.5bp of edge does not clear a 5-10bp fee at any significance.

## THE STRUCTURAL FINDING

**You can have independence or predictive power, but not both.**

- The features that PREDICT (the ofi family) are variants of one signal,
  correlated 0.53-0.57. Combining them is counting one signal three times.
- The features that are genuinely INDEPENDENT -- liq_dens (max |corr| 0.02)
  and casc_recent (max |corr| 0.27), both OI-derived -- have no predictive
  power (-0.0048%, -0.0020%) and appear in zero top combinations.

This is a deeper obstacle than "needs better multiple-comparison correction".
The independence that makes stacking work is precisely what the market does
not offer: anything that predicts the next hour of BTC is a rearrangement of
recent order flow, and there is only one order flow.

It also retires the §7.4 hypothesis from LIQUIDATION_FINDINGS. The cascade
features ARE independent as predicted, and that turned out not to be enough.

```bash
python -m scalp_lab.run_combo
```

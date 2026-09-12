# Edge-to-noise: the screening tool (backlog #3)

Absolute edge is the wrong screen. What decides whether a candidate is
LEARNABLE in a human timeframe is edge / per-trade noise, because it fixes the
sample needed to tell it from zero:  **n for t=2 = (2*noise/edge)^2**

## Ranked

| candidate | edge% | noise% | ratio | n for t=2 | trades/yr | years |
|---|---|---|---|---|---|---|
| **funding carry, 7d hold** | +0.0644 | 0.0826 | **+0.780** | **7** | 52 | **0.1** |
| **funding carry, 1d hold** | +0.0093 | 0.0129 | **+0.717** | 8 | 365 | 0.0 |
| buy&hold 90d | +12.39 | 36.59 | +0.339 | 35 | 4 | 8.7 |
| short straddle 1d | +0.514 | 2.010 | +0.256 | 61 | 365 | 0.2 |
| short straddle 7d | +1.264 | 5.478 | +0.231 | 75 | 52 | 1.4 |
| buy&hold 30d | +4.155 | 18.03 | +0.230 | 75 | 12 | 6.3 |
| short straddle 14d | +1.623 | 7.370 | +0.220 | 83 | 26 | 3.2 |
| short straddle 30d | +1.036 | 12.93 | +0.080 | 623 | 12 | 51.9 |
| 1h contrarian, 2h hold | +0.0171 | 0.6691 | +0.026 | 6,092 | 4,380 | 1.4 |
| 1h contrarian, 1h hold | +0.0051 | 0.4796 | +0.011 | 35,730 | 8,760 | 4.1 |
| **RANDOM (null), 4h** | +0.0041 | 0.9184 | **+0.004** | 204,793 | 2,190 | 93.5 |

All defined-risk variants came out NEGATIVE (-0.257 to -0.385) once the 43%
wing cost is applied -- consistent with the short-vol spec.

**Funding carry has 3x the ratio of anything else** and validates in 7 trades.
It is the lowest-RETURN thing in this repo and by far the most LEARNABLE,
because its noise is tiny. Ranking by return hides this completely.

## The structural law

```
buy&hold (drift)     +0.044  +0.113  +0.230  +0.339     IMPROVES with hold
funding (carry)      +0.717  +0.780                     improves
short vol            +0.256  +0.231  +0.220  +0.080     degrades
1h contrarian        +0.011  +0.026  +0.021  -0.015     peaks at 2h, dies
                     (shortest hold -> longest)
```

Theory confirmed:

- A **DRIFT/CARRY** edge accumulates: edge ~ T, noise ~ sqrt(T) -> **ratio ~ sqrt(T)**.
  Hold longer.
- A **ONE-SHOT** edge does not: edge ~ const, noise ~ sqrt(T) -> **ratio ~ 1/sqrt(T)**.
  Hold as briefly as the edge allows.

Short vol degrades despite the premium accruing linearly, because its TAIL
grows faster than sqrt(T). That is the real reason 1d and 7d tenors beat 30d
-- better than the "the tail eats it" hand-wave in the earlier note.

## The screen: TWO tests, not one

Candidates fail on different axes and both must pass.

**Learnability** = ratio and frequency together -> time to validate
**Profitability** = edge vs round-trip fee

| candidate | learnable? | profitable? | verdict |
|---|---|---|---|
| 1h contrarian, 2h | YES (1.4 yr, 4380/yr) | NO (+0.017% vs 5-10bp) | reject |
| short straddle 30d | NO (51.9 yr) | YES (+1.04%) | reject |
| short straddle 7d | YES (1.4 yr) | YES | pursue |
| funding carry 7d | YES (0.1 yr) | marginal, but real | pursue |

### The rule adopted

```
REJECT if  ratio < 0.05 AND trades/yr < 1000     (unlearnable)
REJECT if  edge < 2x round-trip fee              (unprofitable)
```

Applied retrospectively this rejects six of the ten studies in this repo
before any compute is spent: order flow (0.5bp vs 5-10bp fee), book imbalance,
mean reversion, value-area boundary, post-cascade reversion (10.9bp vs 10bp),
and the daily consolidation breakout (18.5 trades/yr at high noise).

### The corollary that matters for backlog #2

Frequency substitutes for ratio only up to a point, and it does nothing for
profitability. Combining weak signals raises the WIN RATE, which raises edge
but leaves per-trade noise roughly unchanged -- so it improves BOTH axes at
once. That is the specific reason the combination matrix is worth running
despite everything univariate having failed: three independent 52% signals
agreeing give ~56%, which on a 0.7% move is 8.4bp of edge against the same
noise. That clears the fee where 52% does not.

The catch: it requires INDEPENDENCE. ofi/tint/big are all tick-derived and
almost certainly correlated. The cascade feature (OI-derived) and the regime
filter (price-derived) are the two plausibly independent additions.

```bash
python -m scalp_lab.run_ratio
```

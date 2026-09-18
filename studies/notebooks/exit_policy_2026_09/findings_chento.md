STATUS: CONCLUDED 2026-09-15 — chento arm. Frozen verdict **INCONCLUSIVE**; the user's hypothesis that the time
stop is harmful is **not settled**. No candidate exit passed; keep the shipped 72 h exit. No production change.

# Exit-policy study, chento arm — findings

**One-line answer.** Shorter time stops than 72 h are clearly worse. Removing the time stop raised chento's net
expectancy by +0.18 R per trade on average, but the whole gain came from 2021–2023 (+1.00 R on each trade the
time stop closed) and none since 2024 (−0.05 R), while drawdown rose from 33% to 49% (BTC) and from 21% to 43%
(ETH). Exits that fire when the trade is contradicted did not help: a flow reversal cuts winners, and the opposite
chento signal almost never fires while a trade is open.

Pre-registration: [PREREGISTRATION_CHENTO.md](PREREGISTRATION_CHENTO.md), frozen in `results/chento/freeze_F0.json`
(2026-09-15 13:10:55 UTC) before any arm outcome; outcome run 13:11 UTC; verdict `results/chento/verdict.json`.

## 1. What was tested

- **Entries:** the OKX re-validation's gate-off chento pool, identical for every arm: 208 BTC and 184 ETH trades,
  2021-04 → 2026-09, every trade taken, 10 bp, with **actual Binance funding** (new; the bot books none).
- **Arms** (each changes only the exit): A0 shipped (1R stop, 6R target, 72 h time stop); the historical time-stop
  grid 6 / 12 / 24 / 48 / 168 h; A1 no time stop (30-day censoring); X1 = A1 + exit on a flow reversal (the bot's
  own B1 absorption rule against the position, threshold k = 3 chosen by step 0 before any outcome); X2 = the same
  only while the trade is losing; X3 = A1 + exit on the opposite chento Triple signal.
- **Checks before the freeze:** A0 reproduced the OKX study's 392 trades exactly; the flow features matched the
  bot's own rebuilt frames (max difference 9e-14); funding complete; no missing bars; 20 walker fixtures; a
  truncation test on 1,360 exits; an end-to-end smoke run on synthetic markets.
- **Step 0** (feature timing only): the flow-reversal signal fires a median 4.3 h after entry at k = 1 (96% of
  trades within a day — a hidden short time stop), 31 h at k = 2, 140 h at k = 3. The frozen rule chose k = 3.

## 2. Results (net R per trade: 10 bp and actual funding; paired against A0 on identical entries)

| Arm | Mean net R | Mean vs A0 (95% CI) | BTC / ETH | 2021-04 → 2023-12 / 2023-12 → 2026-09 | Exit mix |
|---|---:|---|---|---|---|
| **A0** shipped 72 h | **+0.634** | — | +0.725 / +0.530 | — | stop 194, time 159, target 39 |
| 6 h | +0.068 | **−0.565** (−0.892, −0.256) | −0.65 / −0.47 | −0.88 / −0.30 | time 357 |
| 12 h | +0.116 | **−0.518** (−0.820, −0.232) | −0.61 / −0.41 | −0.85 / −0.24 | time 319 |
| 24 h | +0.256 | **−0.377** (−0.621, −0.145) | −0.40 / −0.35 | −0.61 / −0.18 | time 263 |
| 48 h | +0.506 | **−0.127** (−0.222, −0.035) | −0.14 / −0.11 | −0.18 / −0.08 | time 194 |
| 168 h | +0.721 | +0.088 (−0.110, +0.292) | +0.09 / +0.08 | +0.18 / +0.01 | stop 242, time 84, target 66 |
| **A1** no time stop | **+0.808** | **+0.175** (−0.091, +0.459) | +0.19 / +0.16 | **+0.41 / −0.02** | stop 271, target 96, censored 25 |
| X1 flow reversal, any time | +0.568 | −0.065 (−0.267, +0.128) | −0.08 / −0.05 | −0.15 / +0.01 | stop 205, event 141 |
| X2 flow reversal, losing only | +0.761 | +0.127 (−0.135, +0.410) | +0.15 / +0.10 | +0.38 / −0.09 | stop 258, target 92, event 21 |
| X3 opposite chento signal | +0.812 | +0.179 (−0.086, +0.464) | +0.18 / +0.18 | +0.41 / −0.02 | stop 266, target 95, event 14 |

**Verdict: INCONCLUSIVE.** No candidate passed the frozen rule. A1, X2 and X3 cleared the +0.10 R effect size on the
point estimate but failed the rest: Holm-adjusted p 0.94, negative in the second half, and walk-forward
re-selection picked a different arm in each of four folds (A1, X3, A0, X1) with a stitched out-of-sample
difference of −0.05 R. Their 95% intervals still reach +0.10 R, which is why the verdict is not KEEP_72H.
**The hypothesis "the time stop is harmful" is not settled:** A1 is ahead on average, but its interval includes zero
and the gain is concentrated in one period.

## 3. What the numbers say about the time stop

- **72 h is not too long.** Every shorter time stop in the historical grid is significantly worse: 48 h already costs
  0.13 R per trade, 24 h 0.38 R, with both halves and both assets agreeing. Mean-reversion that is cut early loses
  its payoff.
- **Where the no-time-stop gain comes from** (post-verdict, `results/chento/exploratory.json`). Of the 159 trades A0
  closes on its time stop (on average +1.40 R at that moment), held to their stop or target: 57 went on to the 6R target
  (+2.24 R at 72 h → +5.90 R, +208 R in total), 77 turned into full stop-outs (+0.77 R at 72 h → −1.05 R, −141 R), and
  25 were still open after 30 days. **By period: +1.00 R per such trade in 2021–2023, −0.05 R in 2024–2026.** Holding
  past 72 h paid in the high-volatility early years and has been worth nothing since.
- **The mechanism is slow, not absent.** With no exit at all, the average move in the trade's favour builds to
  +0.74 R at 72 h, +1.29 R at 7 days and then stops growing (+1.30 R at 14 days, +1.35 R at 30 days, with very wide
  intervals). Half of the eventual move is in place by 72 h. A time stop around 3–7 days sits on the part of the
  curve where the edge is still accruing but most of it has arrived.
- **Longer holds stack risk.** In the bots' own sequence (6 h cooldown, BTC 48 h skip after a losing stop, ETH half
  risk after a loss, 2% risk, 3× cap; drawdown against the fixed $10,000, reproducing the 2026-09-14 sizing review's
  33.4% / 20.9% for A0):

| Arm | BTC return / max drawdown | ETH return / max drawdown | Max open (BTC / ETH) |
|---|---|---|---|
| A0 72 h | +288% / 33% | +128% / 21% | 5 / 6 |
| 48 h | +235% / 24% | +90% / 22% | 5 / 6 |
| 168 h | +322% / 45% | +136% / 33% | 6 / 7 |
| A1 no time stop | +353% / 49% | +146% / **43%** | 6 / 8 |
| X2 | +339% / 49% | +138% / 42% | 6 / 8 |
| X3 | +349% / 56% | +149% / 41% | 6 / 8 |

  Without the time stop, ETH's drawdown doubles for 18 points more return. (The frozen report divided drawdown by
  running peak equity, which understates it as equity compounds; `exploratory.json` corrects this report-only figure.
  The verdict does not use it.)

## 4. What the numbers say about "exit when the trade is shown wrong"

- **Flow reversal at any time (X1) hurts** (−0.07 R). Even at the strictest threshold it fires on 141 of 392 trades
  before their stop or target, and cuts too many that would have worked.
- **Flow reversal only while losing (X2)** is A1 minus a little: it fires on 21 trades and lowers A1's result by 0.05 R.
  Cutting losers on an absorption signal does not add information beyond the stop.
- **The opposite chento signal (X3)** is the literal "the reason for the trade reversed" exit, and it fired on only
  14 trades. It is essentially A1.
- As on the ORB study, the only invalidation that does not damage the payoff is the structural stop. Chento's losing
  trades are not identified early by the flows tested here.

## 5. Decision and what it permits

The pre-registration permits no change: **keep `TIF_HOURS = 72`** on both chento bots. What would change the answer:
a no-time-stop result that holds in data after 2026-09-11 (every historical year has now been used), together with an
exposure budget for the extra stacking (BACKLOG 13). A prospective comparison could run A1 as a paper twin of chento
beside A0, as was done for the squeeze no-stop twins. That is a proposal for the user, not part of this verdict.

DSR of A0's daily net R: 0.993 at N = 21, 0.980 at N = 40 (per-observation Sharpe; ignores concurrency and the
strategy's own entry search, so it does not validate chento). Cost sensitivity: at 18 bp every arm drops by about
0.04 R and the ordering is unchanged; funding costs 0.008 R per trade at 72 h and 0.018 R with no time stop.

## 6. Limitations

- One historical sample, already used to pick 72 h in-sample. No clean holdout exists for this dial; the walk-forward
  is the only out-of-sample check, and it did not favour any change.
- 15-minute bars, stop before target within a bar (the bot's convention), exits at bar closes.
- Paired design on every trade; the bots' tilt and cooldown rules are in the report-only sequence only.
- 25 A1 trades were still open at the 30-day censoring horizon or the data end and were marked there.

Reproduce: `chento_checks.py all`, `chento_run.py freeze0` (refuses to overwrite), `chento_run.py outcomes` (refuses
to rerun once the verdict exists), `chento_explore.py`; notebook `01_chento_exit_policy.ipynb`.

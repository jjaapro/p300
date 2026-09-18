# Chento partial take-profits 2026-09 — PRE-REGISTRATION v1.0

**Written 2026-09-19, before any partial-close outcome was computed. Frozen in `results/freeze_F0.json` before
the outcome stage runs.** BACKLOG §2.2, the partial-TP arm, and the roadmap's standing constraint on exits
("nothing changes until something measurably better exists"). The user's request on record, 2026-09-18:
*"consider partial TPs with our strategies. But of course first something better would need to be found."*

Nothing under `bots/`, `strategies/` or `data/` changes inside this study. The only database read is the frozen
OKX-study snapshot, opened read-only through the exit-policy study's process guard.

## 1. Question

On identical chento entries, does taking partial profits — closing fractions of the running position at fixed
R-levels and letting the remainder run to the shipped exit — improve the strategy's **risk-adjusted** record over
the shipped single 6R target, net of costs and funding?

**Prior, stated before the run.** Cumulative R is expected to *fall*: on this signal wider fixed targets beat
tighter ones up to ~8R and a fixed target beats every trailing variant (memories `wider-tp-same-stop-is-better`,
`tight-tsl-underperforms-mean-reversion`). Drawdown is expected to fall too, mechanically, because exposure is cut
after +1R. Whether the ratio improves is the open question, and whether chento's particular levels matter — as
opposed to any early trim — is the second.

The specification comes from chento himself (journal `material_2026_09_19_comment_and_tv_chart.md`, comment point 7):
partial closes are taken **on the running amount, not on the starting amount**, in his example 25 / 20 / 10 / 20 %,
leaving 43.2 % running; his stated first take-profit is 1R and his default is 3R. The levels for his trims are not
on record; here they are assumed at 1R, 2R, 3R and 4R, in that order — a declared assumption, not his rule.

## 2. Inputs (frozen)

| Item | Frozen value |
|---|---|
| Entries, walker, snapshot | Exactly the exit-policy study's (`exit_policy_2026_09/PREREGISTRATION_CHENTO.md` §2–3): 392 trades (BTC 208, ETH 184), 2021-04 → 2026-09, entry = trigger bar close, risk = 5 × ATR(14, 15m), 10 bp, actual Binance funding, snapshot sha256 `f3decffe…` opened `mode=ro`; `chento_lib.verify_inputs()` must pass |
| The shipped exit (A0) | Reproduced by `chento_lib.walk` with arm A0 and checked against the committed `exit_policy_2026_09/results/chento/walks.csv.gz`: every trade's `net_R` within 1e-9, or the study stops |
| Engine | Partial closes do not change the remainder's stop, target or 72 h time exit, so every ladder reduces to A0's exit plus, per trade, the first bar at which the running maximum favourable excursion crosses each level (§3) |

## 3. Engine (every arm)

For a trade with A0 exit at bar `e` (kind stop / target / time), walk bars `b = t + 15m … e` in order:

1. **MFE.** `mfe_b` = long: `(high_b − entry) / risk`; short: `(entry − low_b) / risk`; running maximum `M_b`.
2. **Fills.** For each level `L_k` of the ladder, ascending, with fraction `f_k` of the position *still running*:
   the leg fills at the first bar `j` with `M_j ≥ L_k`, **at the level price** (the same touch convention as the
   shipped 6R target), provided `j < e`, or `j = e` and A0's exit at `e` is the target or the time exit. **If A0's
   exit at `e` is the stop, no level fills on bar `e`** — stop before target within a bar, as the bot walks it;
   the conservative ordering. A level that never fills ends the ladder (higher levels cannot fill either).
3. **Remainder.** Whatever is still running exits exactly as A0 did, at A0's price and time.
4. **Costs.** The bot charges one round trip as basis points of notional, `cost_R = 10 / 10000 × entry / risk`,
   scaled by stop distance. Partial closes leave the total traded notional unchanged — one entry, exits summing
   to one — so the total cost is unchanged; each leg carries `cost_R × its fraction`. Declared: extra order
   count and any adverse fill at a resting level are not charged.
5. **Funding.** Each leg accrues `chento_lib.funding_R` from entry to its own fill bar (inclusive, as for a fill
   inside a bar), times its fraction; the remainder carries A0's funding times its fraction.

`R_price = Σ_legs fraction × L_k + remainder × A0's gross R − cost_R`; **net R = R_price + funding** (decision-bearing).
**Parity gate:** the empty ladder must reproduce A0's `net_R` on all 392 trades within 1e-9.

## 4. Arms

| ID | Ladder (level R → fraction of the running position) | Rides to A0's exit |
|---|---|---|
| **A0** | none — the shipped exit | 100 % |
| **P1** | chento's arithmetic: 1R → 25 %, 2R → 20 %, 3R → 10 %, 4R → 20 % | 43.2 % |
| **P2** | his default: 3R → 50 % | 50 % |
| **P3** | his first take-profit: 1R → 50 % | 50 % |

Candidate family: **M = 3**. Tie order P1, P2, P3 (his full ladder first). No break-even stop moves, no
re-entries, no trailing — each is another dial and none is on record as his rule.

**Placebo.** For P1: 500 ladders with the same four fractions at four levels drawn uniformly from (0.5R, 5.5R),
sorted, one ladder per draw applied to all trades (seed 7). For P2 and P3: 500 single trims of the same fraction
at one uniform level. The placebo answers whether the *levels* carry information or *any* early trim does the
same; it is a label on a winning candidate, not a gate (§6).

## 5. Statistics

- Per arm, per asset and pooled: mean net R, cumulative R, win rate, the maximum peak-to-trough drawdown of
  cumulative net R in trade order (**DD_R**), and **MAR_R = (cumulative R ÷ years) ÷ |DD_R|**; the same on each
  half (entry day before / from 2023-12-20). Also at 2 % risk per trade, additive, as % of capital.
- Paired per-trade difference `d_i(P) = netR_i(P) − netR_i(A0)`, with the exit-policy study's bootstrap unchanged
  (`chento_lib.block_indices`: calendar-day axis, 30-day circular blocks, 10,000 draws, seed 42): 95 % interval.
- Fill statistics per arm: share of trades with each leg filled, mean fraction closed early, mean hours to the
  first fill.
- Placebo: the distribution of pooled MAR_R and mean net R over the 500 draws; the candidate's percentile.

## 6. Decision rule (fixed now)

A candidate P **passes** when all hold:

- **D1** risk-adjusted gain: `MAR_R(P) ≥ 1.10 × MAR_R(A0)` on BTC **and** on ETH, each on its own sequence.
- **D2** stability: `MAR_R(P) ≥ MAR_R(A0)` on the pooled sequence in **both** halves.
- **D3** bounded cost in expectancy: `d̄ ≥ −0.10 R` and the 95 % lower bound of `d̄` ≥ −0.30 R.

| Verdict | Rule |
|---|---|
| `PROMOTE(P*)` | at least one candidate passes; P* is the first passing candidate in tie order. Labelled **levels** if P*'s pooled MAR_R is at or above the 95th percentile of its placebo, else **trimming** (any early trim does it). Promotion means a pre-registered paper arm on a chento twin — a bot change for the operator, not a live change |
| `KEEP_6R` | no candidate passes D1 |
| `INCONCLUSIVE` | otherwise |

## 7. Out of scope, declared

The counter-short comparator in BACKLOG §2.2 (do nothing / partial close / counter-short at a resistance trigger)
needs a resistance trigger defined causally and is a stage 2. Hedge-and-hold on the same instrument is a partial
close minus costs and is not run separately.

## 8. Run order

`python partial_run.py freeze0` (hashes this file, the inputs and the A0 reference) → `python partial_run.py
outcomes` → `C:/Python/Python313/python.exe build_notebook.py` → `findings.md`.

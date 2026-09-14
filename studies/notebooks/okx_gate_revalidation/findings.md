# OKX cross-exchange gate — causal re-validation — FINDINGS

**Verdict: RETIRE.** Final (§4.4: once `verdict.json` is written the verdict is final).

| | |
|---|---|
| Pre-registration | `README.md`, frozen `19cd10a`; Addendum 1 `d1d9808` |
| Run commit (A2) | `2406b6a` — the study run is the first `outcomes.py` phase B at this commit, run 2026-09-13 |
| Snapshot (A6) | taken once, sha256 `f3decffe…b75c81`, rows `< 2026-09-12 00:00 UTC`, outside the repo at `C:\Source\Repos\p300-study-snapshots\okx_gate_revalidation\snapshot.db` |
| Outputs | `results/verdict.json`, `report_pre.json` (step 5), `report_post.json` (step 6), `trades_{BTC,ETH}.csv`, `n_trials.json` (family `chento_okx_gate`, n = 1) |

## What the verdict permits (§6)

RETIRE leads to **`FILTER_OKX_ALIGNED = False` on both chento bots.** The direction is decided;
it is one commit made after this file, containing the flag change
(`bots/chento_v3/strategy/config.py:89`, which the ETH leg resolves too), a calibration-log row
naming this verdict, the golden re-baseline (`chento_btc_okx_blocked` loses its meaning), a
roadmap update and a restart of both bots.

- **Timing is the operator's; the direction is not.** The go-ahead sets the date only. If the
  commit has not landed by **2026-10-13** (30 days from this file) the roadmap records it overdue.
- **A sizing and concurrency review is a precondition of that timing** (§6). Measured here: the
  OFF arm takes **392** trades against the gated arm's **174** (2.25× the fire rate), and its
  mark-to-market drawdown is 36.1 % against 23.5 % at 2 % risk per trade with every trade taken
  (report-only, below).
- The memory label becomes **"RETIRED (causal re-test)"**; the −25 % drawdown / +34 % OOS figures
  may no longer be cited.
- Every downstream chento study scored on the same-hour `okx_delta_z` (§0.2: overlay study,
  attribution layer, LSR B5 study, validation audit threshold re-filter, the C4-copied attaches)
  needs its own decision; none is re-cut here.
- It permits **no replacement gate** — no tighter z, other window, Bybit, deliberately lagged z or
  size modulator — without its own pre-registration at `N_TRIALS ≥ 54`.

## Required statement (§4.5)

The same-hour control arm meets the full RETIRE clause (R-a, R-b and D, with POWER met), so:

> *"The same-hour control also fails to discriminate, so this verdict does not bear on the
> look-ahead premise: the gate does not discriminate on this pool and engine even with C4's
> information set."*

The §8 red flag (causal gap larger than the same-hour gap) is **not** raised: +0.301 R causal
against +0.403 R same-hour.

## Clause by clause (FULL, R1, 10 bp)

Evaluation order INVALID → KEEP → RETIRE → INCONCLUSIVE.

| Clause | Rule | Measured | Holds |
|---|---|---|---|
| INVALID checks | P0–P3, POWER, A5, degenerate `gate_metrics`, finite bootstrap quantiles | none failed | no |
| **K1** | `promotion_verdict(grid 53, binary)` | stitched-OOS blocked expectancy **+59.6 bp** (needs ≤ −5); Sharpe uplift −0.118 raw, **−0.016** deflated (needs ≥ 0.2); sign stability **2 of 4** folds (needs ≥ 2/3) | **no** |
| K2 | Δ_BTC > 0 and Δ_ETH > 0 | +0.118 R, +0.505 R | yes |
| **K3** | mean_R(B_R2) ≤ −0.025 and K_R2 > B_R2 | B_R2 **+0.542 R** (n 220), K_R2 +0.769 R (n 172) | **no** |
| K4 | Δ_pool > 0 and D | +0.301 R | yes |
| **R-a** | boot 5th pct of mean_R(B) > 0 | **+0.181 R** [0.181, 0.495, 0.843] | **yes** |
| **R-b** | 90 % CI of K − B includes 0 | **[−0.042, +0.304, +0.662]** | **yes** |
| **D** | per-asset signs match pooled | +, +, + | **yes** |

KEEP fails on K1 and K3 → RETIRE holds → **RETIRE**. The per-fold Sharpe uplifts were −0.29,
−1.60, +0.32, +0.85 (folds 2023-04 → 2027-04 OOS starts; `n_folds` 4).

## Preconditions (development run, re-evaluated in the study run)

| Check | Result |
|---|---|
| P0 | all six live `okx_delta_z` values reproduced (max \|diff\| 4.4e-16), decisions identical; P0c exact on all six |
| P1 | overlay backward-only pool reproduced exactly: BTC 204 / ETH 189 rows, max relative diff 0.0 |
| P2 | fidelity 0.983 BTC (351/357), 0.991 ETH (334/337), **0.987 pooled** |
| P3 (A1) | six ledger trades: kinds exact; TIF −25.3 / −57.9 / −12.2 / −45.2 s against `_time_stop_iso`; stops −17.9 / −17.9 s and prices exact |
| POWER | K 93 / B 115 BTC, K 81 / B 103 ETH, **K 174 / B 218 pooled** |
| Data | 0 missing bars, 0 NaN prices on any walk path, 0 NaN z (R1 and R2), 0 ATR drops |

## Report-only (cannot change the verdict)

**The arms.** R per trade at 10 bp.

| scope | OFF n / mean | K n / mean / win | B n / mean / win | Δ K − B |
|---|---|---|---|---|
| pooled | 392 / +0.641 | 174 / **+0.809** / 47 % | 218 / **+0.508** / 38 % | **+0.301** |
| BTC | 208 / +0.731 | 93 / +0.796 / 46 % | 115 / +0.678 / 44 % | +0.118 |
| ETH | 184 / +0.540 | 81 / +0.823 / 48 % | 103 / +0.318 / 31 % | +0.505 |

**The discrimination is positive but not demonstrable at this sample.** Δ is positive pooled,
on both assets, at 18 bp (+0.293), under R2 (+0.227) and in all three periods (C4-IS +0.318,
C4-OOS +0.580, HOLDOUT +0.203 on 14 / 11 trades). 92.6 % of bootstrap draws put K above B. But
the 90 % interval reaches −0.042 R, and the realised minimum detectable effect at the
Bonferroni level is **0.93 R** against a measured 0.30 R (CI half-width 0.35 R; §4.6 predicted
≈ 0.33 R before the widening from day blocks). §4.6 and §7.3 declared this in advance: "R-b is
expected to hold even if C4's effect were fully causal", and under decision 1 a gate whose
discrimination cannot be distinguished from zero while it blocks profitable trades is retired.
**RETIRE here means "not shown to discriminate while blocking a profitable set" — not "shown
to be useless".** The blocked set earned +110.7 R over 218 trades.

Other items:
- **Mark-to-market drawdown** (§4.5, every trade taken, overlapping, 2 % risk): **OFF 36.1 %,
  gated 23.5 %**; trade-close 18.3 R vs 12.2 R. **This gate cuts drawdown while blocking
  profitable trades — exactly the case decision 4 said this study may retire.** Drawdown was
  report-only by that decision.
- **Production-sequence approximation** (tilt after the gate; BTC skip-after-loss, ETH
  half-after-loss): OFF total 206.3 R, trade-close maxDD 5.9 R, MTM 15.1 %; gated 102.2 R,
  5.3 R, 12.1 %. MAR-like 34.9 vs 19.2.
- Long / short, pooled: longs Δ +0.437 (95 / 131), shorts Δ +0.055 (79 / 87). BTC shorts Δ −0.435.
- Exit mix, pooled: K stop 84 / target 19 / TIF 71; B 110 / 20 / 88.
- HOLDOUT per asset is noise-sized: BTC Δ −0.95 (8 / 7), ETH +1.98 (6 / 4).
- DSR of the K arm on per-trade R: 0.996 at N = 53. This counts only the gate family's trials
  and ignores concurrency and the strategy's own search, so it does **not** validate chento
  (memory `project_validation_audit_2026_09` stands: nothing clears an honest DSR).
- Cap-binding trades: 8 of 392. Walker: 0 missing bars.
- Precision (§2.2): 1.0 on both assets (every end-of-day anchor bar, after the 6 h cooldown, is a
  pool trigger); 0 pool triggers whose end-of-day anchor differs from the per-trigger one.
- Feature-level sign disagreement, R1 vs same-hour: 30.8 % BTC, 28.8 % ETH; R1 vs R2: 30.8 %,
  29.4 % (BTC's two rates are equal by coincidence — only 42 of the 110 disagreements are shared).
- A1's report-only P3 TIF exit prices, walker 15m close vs live tick: −0.103 %, −0.001 %,
  −0.053 %, −0.028 % (SJ-4243, 4244, 4245, 4246).

**Same-hour control** (C4's information set, same trades, same R): Δ **+0.403** (BTC +0.359,
ETH +0.430), CI **[−0.006, +0.412, +0.830]**, boot_B 5th percentile +0.102, POWER K 168 / B 224,
K1 fails (blocked +37.8 bp). R1 and same-hour membership agree on 69.9 % of OFF trades. The
look-ahead's measurable contribution is the ~0.10 R larger point gap; both intervals include 0.

## Priors (§8) against the result

P(P0 passes) 0.95 — passed. P(INVALID first run) 0.20 — did not occur. P(RETIRE | valid) 0.45 —
occurred. P(same-hour control also meets RETIRE) 0.6 — occurred. Gate-off was declared the most
likely end state; it is the outcome. Nobody should read it as news, and nobody should read the
positive Δ as a reprieve: the rule was fixed before either number existed.

## Record of the run and every look before it

1. Planning looks are disclosed in Addendum 1 (probe snapshot, P0 z values, walker on the six
   ledger trades, pool sizes before filters, exit-reason strings).
2. While implementing, a `head` of `overlay_study/results_backonly/trades_BTC.csv` cut at 60
   characters showed the header and two 2021-01 rows (before the window): `ts`, `direction`,
   `entry`, `stop` and the first digits of `target`. No outcome column was visible.
3. Development runs, all before the run commit, none failing a precondition: step 1 once;
   steps 2–4; phase A; a determinism rerun of steps 3–4 into a temporary directory
   (byte-identical); after the review fixes, steps 2–4 again with provenance (data outputs
   byte-identical to the first run) and phase A again.
4. `shadow_run.py` ran phase B, the report and the control on real features and membership with
   **synthetic** seeded prices, twice, into temporary directories that were deleted. No real R
   existed before the study run.
5. An independent pre-run code review (read-only; it read one `pool_BTC.csv` row — a trigger
   time and direction) found no blocker. Its should-fix items were fixed before the run commit:
   provenance on every step output, a run-commit check over all imported code, a
   phase-B-started marker, refusal on NaN prices, and seven tests that had passed without
   testing their clause (then 19 / 19 mutations of the decision code caught).
6. The study run printed only `VERDICT: RETIRE` before `verdict.json` existed.

## Threats and notes found after the verdict (§4.4: recorded, cannot void it)

- **Line endings.** Provenance hashes are over working-copy bytes, and the repository converts
  LF to CRLF on checkout (`core.autocrlf`). A fresh checkout of `results/` will fail the hash
  checks — a refusal, never a silent pass — so the §6 re-cut machinery, if ever used, needs
  hashing on normalised line endings or a `.gitattributes` rule first. (Not needed: RETIRE
  schedules no re-cut.)
- Mark-to-market days are dated by bar open (a mark at the 23:45 bar's close belongs to that
  day); report-only.
- Funding is not modelled (§7.8), and the OFF arm is 58 % long.
- §7.6 still applies to live: until the OKX refresh is hour-aligned, live sees the R2 set on part
  of its decisions. It no longer matters for a switched-off gate, but it matters for any future
  use of `okx_delta_z`.
- **The production-sequence approximation (§4.5) is further from the bot than it looked**
  (found by the §6 sizing review, 2026-09-14). `run_overlays.tilt_sizes` zeroes a BTC trade after
  ANY losing predecessor in trigger order, closed or not; the bot skips only for 48 h after a
  CLOSED stop loss. On the OFF arm the approximation takes 94 of 208 BTC triggers where the coded
  rule takes 198, with 36 look-ahead cases. So its OFF-arm figures above (total 206.3 R, MTM
  15.1 %) do not describe the bot, and must not be carried into the calibration log. The
  §4.5 text already labelled it an approximation; it could not move the verdict. The review's
  per-bot numbers in the bots' own sequence are in `docs/calibration/chento_triple_v3.md`
  (2026-09-14 row). ETH's half-after-loss has the same trigger-order look-ahead (54 of 184
  sizing disagreements with the bot's last-actual-close rule; 49 look-ahead halvings). The same
  rule mismatch sits under the overlay study's tilt ranking on both assets, which predates this
  study — roadmap item 15.

## OPS follow-up (§6, separate go-ahead)

Align the OKX refresh to HH:01 (the elapsed-time check in `_refresh_hourly_okx`, `data/sources/binance.py`; frozen README cites :1096-1098) so any future
`okx_delta_z` consumer sees the R1 information set on every bar.

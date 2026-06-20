# Pool restructure — implementation plan

> **Source:** [portfolio_with_pools.md](portfolio_with_pools.md) (the fact sheet / vision).
> **Created:** 2026-06-11, after a code-verification pass of the fact sheet's claims.
> **Posture:** stabilization-first — per fact-sheet findings #21/#22, production absorbs the validated backlog before new research. This plan IS that absorption, sequenced.
> **No time estimates** (design principle): phases are ordered by dependency and closed by explicit gates, not dates.

---

## 0. Verified current state (code, 2026-06-11)

Everything below was checked against the working tree and `prod.db` — this is the ground the plan stands on. Where the fact sheet disagreed, the fact sheet has been corrected (see §1).

| # | Fact | Evidence |
|---|---|---|
| V1 | Sizing is `size_usdt = capital × (allocation_pct/100) × leverage` — no cash buffer, no `deploy_fraction` anywhere in code | `strategies/trades.py:174-176` |
| V2 | `variant.spec.allocator_notes` exists and already carries `gross_notional_target_x: 2.25` plus **dead legacy fields** `core_pct: 50 / tactical_pct: 50 / reserve_pct: 0` from the retired Core/Tactical design | `strategies/p300_spec.py:138-146` |
| V3 | **Two parallel weight systems.** `_resolve_sleeve_weight` consults the regime allocation table FIRST (`allocation.get_weight_pct`), falling back to the composition's static `weight_pct` only when regime/table is unavailable | `strategies/orchestrator.py:430-451`, `strategies/support/allocation.py:52-83` |
| V4 | The allocation table gives **non-zero** weights to the zero-static sleeves: `JPLUS_EMA_BTC` 0.50/0.30/0.30/0.30 across regimes, `JPLUS_ETH_DAILY` 0.20/0.10/0/0, R4 family non-zero outside bear, all under `CORE_ALLOC_CAP = 0.50`. This is how R4_BTC traded live despite a static 0%. **Fact-sheet finding #12 was wrong** — EMA/ETH would NOT open at zero size on the next cross | `allocation.py:77-82` |
| V5 | Live composition (`p300_aggressive_v2_v1_0`, the only enabled variant): S-003 15%@5x · S-078 8%@5x · TIMING 25% (THU_BEAR 6@5, PDO 9@1, CPR 5@1, FOMC 5@10, R4s 0@1) · AI_QUANT 2%@3x · CHENTO 10%@5x · EMA/ETH 0%. Σ = 60%. 50 disabled `__replay*` variants clutter the table | `prod.db` variants |
| V6 | Dispatch: all 8 sleeves registered in legacy `STRATEGY_DISPATCH`; all 8 conditionally registered two-phase; the tick **prefers two-phase** when present. Legacy path is live fallback code | `orchestrator.py:323-392, 524-526` |
| V7 | Vol-target code exists but differs from the vision draft: `voltarget.leverage_for_day` uses **target_vol = 50%** (not 35%), regime **caps** (option A: 3.0/2.5/2.0/1.5) not target-modulation (option B), floor 0.5, J+ family only | `strategies/support/voltarget.py:29-61`, `jplus_inputs.py:232-233` |
| V8 | Margin sim models CROSS + ISOLATED; **FOMC is already isolated** via `_ISOLATED_STRATEGIES = {"FOMC"}`. Always-on in backtest; NOT wired into the live/paper open path | `margin_sim.py:42-44`, `margin_check.py:64-76` |
| V9 | Same-direction cap fully wired through reconcile but **dormant** — no variant sets `same_direction_target_x` | `margin_headroom.py:140-155`, `orchestrator.py:580-589` |
| V10 | TIMING_ANOMALIES subs size independently via per-sub `_effective_weight_pct`; **no enforced Σ-budget, no arbitration** (fact sheet's gap description is accurate; the meta README's "shares a single budget" is aspirational prose) | `timing_anomalies/signal.py:86-114` |
| V11 | `strategy_health.KNOWN_SLEEVES` is **missing CHENTO_TRIPLE_V3 and SHORT_SQUEEZE**; auto-disable on expectancy decay is not implemented (read-only metrics today) | `strategy_health.py:311-317` |
| V12 | Pool / sub-account concept has **zero code presence**. Chento `LADDER_ENABLED = False` confirmed (fact sheet accurate) | grep; `chento_triple_v3/config.py:90` |

## 1. Corrections applied to the fact sheet (2026-06-11)

1. **ADX placement contradiction resolved → Standard.** Main table and the 4-candidates-for-3-slots note already said Standard; the per-pool parameter table still said "Premier — Chento, ADX" (stale). Fixed; the Premier mention is now explicitly the *rejected* alternative.
2. **Finding #12 rewritten** per V3/V4: the issue is not "weight=0 → can't fire"; it is *two parallel weight systems with the regime table silently winning*. The decision it poses changed accordingly (see D2).
3. **Vol-target section** now notes current code = 50% target + option-A caps (V7), so the 35% + option-B draft is a proposed *change*, resolved by study S1.
4. **Stray allocator bullet** that had drifted below the glossary moved back into the TIMING_ANOMALIES open questions.
5. **Numbering gap closed** — Paladin items 29–33 renumbered 23–27.
6. Short Squeeze hold corrected "minutes" → "minutes–hours" (sleeve README: 3R target / 6h time-stop). Remaining fact-sheet-vs-code drift on its exit description ("session-end" vs "6h") is tracked in A5, not guessed at.

## 2. Critical evaluation → consequences

Issues found evaluating the vision itself (beyond the factual fixes above). Each maps to a plan item.

- **C1 — No end-to-end deployment arithmetic.** The stacked haircuts compound: NAV × pool% × ½ buffer × slot split × risk% per trade. At draft params, Chento — the most-validated edge — risks 30% × ½ × 1% = **0.15% of NAV per trade** (~8 trades/yr × ≈+1.3R ⇒ ≈ +1.5% NAV/yr from the flagship sleeve); a Standard slot risks ~0.017% NAV per trade. The whole portfolio plausibly returns low single digits at backtested edge while carrying 4 sub-accounts of operational load. Maybe that's the intended capital-preservation posture — but nobody has computed it. → **Study S2 is now a gate**: no pool variant goes live-paper with frozen params until the portfolio-level expected return / margin-utilization table exists and is explicitly accepted.
- **C2 — Vol-target on a mostly-flat pool is a latent bug.** `pool_realized_vol` from *realized pool PnL* goes ≈0 while EMA waits months for a cross and ETH sits non-bull (fact-sheet #9 says exactly this happens). First entry then sizes at the **cap** — maximum leverage precisely when the pool has been blind. The blend must be computed from **underlying asset returns weighted by current + incoming target exposure**, never from the account PnL series. → Baked into B3 as a hard requirement.
- **C3 — Calendar pool cross-margin contradicts the standing FOMC-isolated decision** (margin-mode memory; implemented in code, V8). FOMC at fixed 10× inside a cross pool means one event gap drains the shared buffer of all 8 subs. Either keep FOMC isolated *inside* the Calendar sub-account (Binance supports per-position isolation) or justify cross quantitatively. → Study S4; default = keep isolated.
- **C4 — Four overlapping risk guards, no binding analysis.** 50% cash buffer, per-trade risk%, slot caps, same-direction cap, gross_notional cap — which binds when? Over-constrained systems fail silent (the audit's own theme: most-validated sleeves were the quiet ones). → S2 produces a "which guard binds in which scenario" table.
- **C5 — The fact sheet never mentions the regime allocation table** (V3/V4) — the layer stack (`NAV × pool × deploy × sleeve weight × risk-lev × regime`) has no slot for it, yet it currently *overrides* static weights. The pool design must either absorb it (per-pool regime modifier, options A/B/C) or retire it. → D2 + B2.
- **C6 — Carry's two-wallet reality is unmodeled.** Spot-long + perp-short in one sub-account: spot gains don't auto-collateralize the futures wallet on Binance unless multi-asset/portfolio margin is used. A pump stresses the perp leg's margin while the hedge PnL sits in the spot wallet. "~5× legs" needs a transfer/top-up mechanic or lower leverage. → S4 scope.
- **C7 — Retirement principle has no mechanism.** The fact sheet's per-sleeve retire-on-evidence rule needs `strategy_health` to (a) cover all sleeves (V11 gap), (b) compute per-sleeve post-cost expectancy vs the N-trade thresholds. Auto-disable can stay future; the *report* must exist or the principle is prose. → A6.
- **C8 — Squeeze Bull's composite numbers lack provenance.** "+9.4R/yr / MAR 1.77" appears only in the fact sheet; the memories record OI-flush alone (MAR 2.18, +8.1R/yr) and funding+CVD alone (+1.09R/trade, n=21). If a combined backtest exists, link it; if not, the composite backtest is part of E1's acceptance, not a prior. → E1.
- **C9 — Premier 1%/trade vs Chento's backtest sizing.** The R-ledger validation (MAR ≈ 3 at T0) is sizing-independent in R-space, but the fixed-R example in the fact sheet (2%) and the per-pool draft (1%) disagree, and chento-source practice was 2–4%. Pick via S2's portfolio arithmetic, not per-sleeve habit.

**Verdict on the fact sheet itself:** keep it — the content is mostly sound and now corrected — but it is doing three jobs at once (vision, dated audit log, implementation backlog). The audit/process findings #11–22 are *plan workstreams*, absorbed into this document; recommend a later split into `vision` (stable fact sheet) + this plan + an append-only audit log. That split is parked in P3 (it touches the broader doc-cleanup effort already deferred by [[doc-cleanup-planned]]).

## 3. Decisions needed (Phase 0 — nothing below proceeds past its gate without these)

| ID | Decision | Recommendation |
|---|---|---|
| D1 | ADX home: Standard (4-for-3 slots) or Premier (halves Chento)? | **Standard** — already applied to the fact sheet; Premier stays 1-slot Chento |
| D2 | Weight source of truth for pool variants: composition-owned weights with the global regime table made per-variant opt-in, or keep the global table override? | **Composition-owned per pool variant**; regime influence re-enters only as the pool's regime modifier (option A/B/C). Legacy variant keeps current behavior until retired |
| D3 | SHORT_SQUEEZE composition (fact-sheet #11): compose into the legacy variant now, or wait for the Standard pool variant? | **Wait for Standard pool variant** (keeps the control baseline clean), and record it in an "intentionally uncomposed" list in the orchestrator docstring so it isn't a silent gap |
| D4 | EMA/ETH static 0% (fact-sheet #12 as corrected): leave (table drives them) or set statics as documentation? | **Leave for the legacy variant**; pool variants get explicit non-zero composition weights under D2 |
| D5 | Calendar fate criteria (#8): pre-commit to 3 sub-accounts unless evidence clears? | **Yes** — commit now: Calendar earns a sub-account only if ≥2 subs besides R4_BTC clear post-cost expectancy in S5; otherwise timing stays paper-only and R4_BTC folds into an existing pool later |
| D6 | Replay-variant retention (#16) | **Archive-then-delete** maintenance script: export `__replay*` rows >30d closed to an archive file, delete from live table |
| D7 | Fact-sheet split (vision / plan / audit log) | **Yes, later** — parked P3; do it after Phase B lands so the vision doc describes something real |

---

## Phase A — Stabilization (finish absorbing; no new strategy code)

*Closes fact-sheet findings #16, #17, #19 and the V11 gap. Squeeze Bull explicitly waits for Phase E.*

- **A1. Two-phase migration close-out.** Verify at runtime that all 8 sleeves register two-phase (V6 says registration is conditional on method presence); migrate any stragglers; **delete the legacy `STRATEGY_DISPATCH` path**; write the one-place dispatch-loop description (tick order, reconcile, caps) as the orchestrator module docstring. *Gate G-A1: one dispatch path; docstring exists.*
- **A2. Replay-variant cleanup** per D6. *Gate: variants table contains only live + deliberately-kept rows.*
- **A3. Same-direction cap pilot.** Set `same_direction_target_x` on the legacy variant at a permissive value (e.g. 2.0× capital — it should bind rarely; the point is to exercise the wired-but-dormant path, V9) and watch reconcile logs for BLOCK events. *Gate: at least one tick observed evaluating the cap; no false blocks.*
- **A4. Per-sleeve calibration logs** (#19). One `docs/calibration/<sleeve>.md` per sleeve: current params + provenance of last change. Backfill CHENTO_TRIPLE_V3 first (5+ changes in 2 weeks); add "update the log" to the definition-of-done for any config edit. *Gate: all 8 production sleeves have a log.*
- **A5. Fact-sheet-vs-code drift sweep.** Reconcile small discrepancies found during verification: Short Squeeze exit wording (session-end vs 6h time-stop — read the sleeve code, fix whichever is wrong), `PDO_L_RF` vs `PDO_RETOUCH` naming hops, dead `core_pct/tactical_pct` allocator_notes fields (delete in the same PR as B2 to avoid double churn). *Gate: fact sheet strategy table matches sleeve code on entry/exit/hold for all rows.*
- **A6. strategy_health coverage** (C7/V11): add CHENTO_TRIPLE_V3 + SHORT_SQUEEZE to `KNOWN_SLEEVES`; add per-sleeve post-cost expectancy + trade-count vs the retirement thresholds (N=15 swing / N=30 daily cadence) to the report. Auto-disable stays future. *Gate: health report shows every composed sleeve with its N-progress.*
- **A7. chento_limit_bid archival execution** (decision already recorded in the fact sheet): remove `strategies/sleeves/chento_limit_bid/`, archive its notebooks, supersede [[chento-v1-sleeve-status]]. *Gate: no dead sleeve code in tree.*

## Phase B — Pool primitives (paper-only, single physical account)

*The pool model in code, exercised by paper variants long before any sub-account exists.*

- **B1. Pools = variants.** One enabled paper variant per pool (`pool_continuous_v1`, `pool_premier_v1`, `pool_standard_v1`, `pool_calendar_v1` only if D5 evidence clears). Rationale: a variant already has capital, its own ledger, allocator_notes, margin caps — exactly the sub-account boundary, 1:1 mappable at go-live. **No new "pool" entity inside one mega-variant.** The legacy variant keeps running unchanged as the control during validation.
- **B2. Pool-variant spec fields** in `allocator_notes`: `deploy_fraction` (0.5), `risk_model` (`vol_target | fixed_r | fixed_notional` + param block), `pool_name`. Composition-owned weights per D2 (regime table opt-out for these variants). Delete dead core/tactical fields (A5). Consumers: `open_paper_trade` (capital × deploy_fraction), `gross_cap_usdt` (decide and document: cap applies to deployed capital, not raw), `_resolve_sleeve_weight` (D2 branch).
- **B3. `leverage_for_pool()`** — generalize `voltarget.py` to: blend = Σ member target-exposure × **underlying asset daily returns** (C2 requirement — never account PnL; flat sleeve contributes only when entering, at which point the candidate exposure is included), Carry excluded from the blend, params from the pool spec (target, window, floor/cap, regime option A/B/C). One function serves Continuous now and any future pool.
- **B4. Fixed-R sizing path.** Extend `Intent` with optional `stop_pct`; reconcile/sizing computes `notional = (collateral × risk%) / stop_pct` when the pool's risk_model is fixed_r. Sleeves that know their stop at decide time (Chento 5×ATR; Short Squeeze fixed-R; ADX −2%) populate it; fixed-notional pools ignore it.
- **B5. TIMING internal allocator** (the fact sheet's recursion design, V10 gap): implement ONE pure function — `allocate(budget, committed, candidates[{want, priority}]) → open/BLOCK decisions` — used by both the pool layer and inside TIMING_ANOMALIES (answers the fact sheet's "reuse vs reimplement": **reuse**). Enforced budget = pool collateral; priority = historical MAR (S3); BLOCK + log, never resize open positions. *Gate G-B: pool variants run in paper with enforced budgets; a deliberate over-subscription test produces a logged BLOCK, not a silent overdraw.*
- **B6. Per-pool margin checks in paper.** Wire `margin_check.can_open` (V8: currently backtest-only) into the pool variants' open path; FOMC stays isolated per C3 default pending S4.

## Phase C — Calibration studies (notebooks; can start parallel to Phase B)

*Every `<study>` marker, made concrete. All in `studies/notebooks/pool_study/` (exists) — research-workflow rules apply: notebook first, production untouched until go-ahead.*

| ID | Study | Settles | Acceptance |
|---|---|---|---|
| S1 | Continuous vol-target backtest on the EMA+ETH proxy blend: target 35 vs 50, window 30–45d, floor/cap, regime option A vs B | Fact-sheet open #1, #2, #5; V7 delta | Param set chosen on MAR/maxDD with IS/OOS split; documented in the pool spec |
| S2 | **End-to-end portfolio arithmetic** (C1/C4/C9): expected annual R/$ per pool from existing backtest ledgers under candidate risk%/targets; margin-utilization; binding-guard table | Premier/Standard risk%; "is the stack worth running" | Explicit accept/reject of projected portfolio return; binding-guard table written into the fact sheet |
| S3 | TIMING priority ranking + budget over-subscription level from historical calendar overlap simulation (CPR-persistent + R4 pairs + FOMC/PDO coincidence) | Fact-sheet open #6 + both TIMING open questions | Priority list + budget level in the TIMING spec |
| S4 | Per-pool worst-case margin sim (extends existing `margin_sim`): FOMC 10× cross-vs-isolated in Calendar; Carry two-wallet/transfer mechanics and leg leverage (C6); crash-gap test of the 50% buffer claim | C3, C6; fact-sheet "structural liquidation insurance" claim | Margin mode per sleeve per pool decided with numbers; buffer claim quantified |
| S5 | Calendar fate evidence (#8/#14 corrected method): per-sub deployment-window-normalized expectancy (expected fire-rate × window vs actual); fold in the still-pending CPR re-validation ([[pdo-cpr-tv-revalidation]]) | D5 execution | Per-sub verdict table; 3-vs-4 sub-account decision recorded |
| S6 | Standard slot priority ranking (4 candidates / 3 slots) by validated MAR + correlation | Slot policy | Ordered list in the pool spec |

## Phase D — Pool paper validation

- **D-1.** Enable the pool variants (B1) alongside the legacy control. Sleeve membership per the fact-sheet table (ADX → Standard per D1).
- **D-2.** Run until evidence, not time: each pool variant accumulates its sleeves' expected fire counts (from S5-style normalization); ledger_coherence stays clean; BLOCK logs match S3 predictions; margin checks (B6) report no violations.
- **D-3.** *Gate G-D: pool variants' behavior matches the spec'd budgets/risk models on real paper data; S2's projection vs actual reviewed once per pool has ≥1 full cycle of its slowest sleeve.* Only then: retire the legacy variant's enabled status (it remains as history).

## Phase E — Squeeze Bull build (the one new sleeve in the vision)

*Production absorption of validated research — allowed once Phase A gates close; lands in the Standard pool variant.*

- **E1.** Composite sleeve per the fact-sheet spec (Rule A OI-flush + Rule B funding+CVD, shared bull gate + 24h cross-rule cooldown). First deliverable is the **combined backtest** (C8) — if it can't reproduce ≈ the claimed +9.4R/yr / MAR 1.77, recalibrate or descope to Rule A only.
- **E2.** Byte-equivalence harness per [[verify-byte-equivalence-when-porting]]: produced feature values asserted against the research notebooks at known timestamps before any paper trade.
- **E3.** Day-1 requirements (lessons #11/V11): composition entry in the Standard pool variant, strategy_health coverage, calibration log. *Gate: first paper trades observed with correct gating.*

## Phase F — Sub-account go-live wiring (far gate)

*Blocked by [[bot-not-close-to-live]] until an explicit end-to-end re-audit passes (G-D plus exchange-side work that is out of this plan's scope). Designed seam: each pool variant maps 1:1 to one exchange sub-account; margin modes per S4; transfers/rebalancing between sub-accounts is new work to be specced only after G-D.*

---

## Dependency graph

```
Phase 0 (D1–D7 decisions)
   │
   ├─► Phase A (A1–A7 stabilization) ──► gate G-A
   │        │                              │
   │        │            ┌─────────────────┤
   ▼        ▼            ▼                 ▼
Phase C (S1–S6 studies, parallel)      Phase B (B1–B6 primitives) ──► gate G-B
   │                                        │
   └────────────► params frozen ◄───────────┘
                       │
                       ▼
              Phase D (paper validation) ──► gate G-D
                       │                        │
                       ▼                        ▼
              Phase E (Squeeze Bull)      Phase F (sub-accounts, far)
```
*(E needs only G-A strictly; slotting it after G-B keeps the Standard pool variant as its landing zone.)*

## Parking lot

- **P1. Paladin trader analysis** (fact-sheet items 23–27 after renumber) — explicitly deferred until the analysis prerequisite is done; no scanner code before it.
- **P2. Monthly memory + system audits** (#18, #20) — process cadence; start after G-A so the first audit measures a stabilized system.
- **P3. Fact-sheet split** (D7) + the broader PORTFOLIO/README/MANUAL/OPERATIONS cleanup ([[doc-cleanup-planned]]).
- **P4. Continuous-sleeve SL / lower-TF entry study** (fact-sheet #10) — deferred by its own text; revisit after S1 lands.
- **P5. strategy_health auto-disable circuit breaker** — after A6's metrics have accumulated trust.

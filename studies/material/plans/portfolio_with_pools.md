> **Implementation plan:** [pool_restructure_implementation_plan.md](pool_restructure_implementation_plan.md) *(2026-06-11)* — created after a code-verification pass of this fact sheet; that pass's corrections are applied inline below (marked where substantive).

## Full pool overview

Each pool is sub-account under main account to minimize liquidation risk and easier position management with cross margin accounts.
**Each pool = one real exchange sub-account, so keep the count minimal** — a pool earns a sub-account only once it has enough validated edge to justify the operational overhead. Prefer consolidating sleeves into existing pools over spawning new ones.
Each pool can allocate maximum of 50% of available funds for the sleeves in the pool. 50% of the funds marked for the pool are always in cash to handle possible draw down and to minimize liquidation risk.


| Pool | Pool allocation | Sleeve | Collateral (margin) | Leverage | Asset | Direction | Hold | Type |
|---|---|---|---|---|---|---|---|---|
| **Continuous** | 50% |||||||
| | | BTC EMA | 50% × ½ (cash) × ⅓ (3 sleeves) | vol-target · `<study needed>` | BTC | LONG or SHORT | weeks | Swing |
| | | ETH Bull Regime | 50% × ½ (cash) × ⅓ (3 sleeves) | vol-target · `<study needed>` | ETH | LONG | weeks | Swing |
| | | S-078 Carry | 50% × ½ (cash) × ⅓ (3 sleeves) | delta-neutral · ~5× legs | BTC (delta-neutral) | market-neutral | days–weeks (while funding +) | Carry |
| **Premier** | 30% |||||||
| | | Chento Triple v3 | 30% × ½ (cash) × 1 (pool only has 1 slot available all times, other sleeves blocked when reserved) | fixed-R · `<to be verified>` | **BTC only** *(locked — trader prefers BTC; multi-asset is a separate sleeve family, not a Chento retrofit)* | LONG or SHORT | hours–weeks (TIF 72h) | Swing |
| **Standard** | 20% |||||||
| | | Short Squeeze | 20% × ½ (cash) ÷ 3 slots (4 candidates, shared) | fixed-R · `<study needed>` | BTC | LONG | minutes–hours | Scalp |
| | | AI_QUANT *(experimental — live in paper)* | up to 1 of the 3 slots, conviction-scaled | fixed-R · 3× now | BTC | LONG/SHORT/FLAT | variable (AI-decided, can run long) | Discretionary |
| | | S-003 ADX | 20% × ½ (cash) ÷ 3 slots (4 candidates, shared) | fixed-R · `<study needed>` | BTC | LONG or SHORT | days–weeks | Swing |
| | | Squeeze Bull *(to be built — OI flush + funding+CVD composite)* | 20% × ½ (cash) ÷ 3 slots (4 candidates, shared) | fixed-R · `<calibrate>` | BTC (ETH pending) | LONG | 48–72h | Swing |
| **Calendar** | to be decided |||||||
| | | Timing Anomalies - FOMC | `<calibrate>` | fixed-lev · 10× now | BTC | LONG | ~10.5h | Event |
| | | Timing Anomalies - THU_BEAR | `<calibrate>` | fixed-R · 5× now | BTC + ETH | SHORT | 24h | Calendar |
| | | Timing Anomalies - PDO_L_RF | `<calibrate>` | fixed-lev · 1× now | BTC + ETH | LONG | 24h | Calendar |
| | | Timing Anomalies - CPR | `<calibrate>` | fixed-lev · 1× now | BTC + ETH | LONG | ≤15 days | Calendar |
| | | Timing Anomalies - R4_BTC | `<calibrate>` | 2.5× inner × vol-lev | BTC | LONG | 12h (Mon) | Calendar/intraday |
| | | Timing Anomalies - R4_ETH | `<calibrate>` | 2.5× inner × vol-lev | ETH | LONG | 24h (Tue) | Calendar/intraday |
| | | Timing Anomalies - R4_BTC_V2 | `<calibrate>` | 2.5× inner × vol-lev | BTC | LONG | 10h (Wed/Fri) | Calendar/intraday |
| | | Timing Anomalies - R4_ETH_V2 | `<calibrate>` | 2.5× inner × vol-lev | ETH | LONG | 10h (Wed/Fri) | Calendar/intraday |


> **Allocations pending:** 4 pools must re-sum to 100% once Calendar takes its slice. Carry now sits **inside Continuous** (3-way split). **AI_QUANT** is live in paper in **Standard**, sharing its 3 slots with Short Squeeze + S-003 ADX + **Squeeze Bull** *(to be built)* — **4 candidates for 3 slots, fixed-priority decides**. *(Considered and rejected: adding S-003 ADX to Premier would **halve** each Premier sleeve's collateral — Chento 15% → 7.5% — so ADX sits in Standard.)*
>
> **Risk models:** Continuous = vol-target (Carry is the delta-neutral exception → fixed-notional), Premier/Standard = fixed-R, Calendar = fixed-notional. See the 3-model table below.
>
> **Calendar under review:** paper is weak across the timing subs — only **R4_BTC** looks profitable so far. Since each pool is a live sub-account, Calendar may not earn one — see Open decision #8.


## Strategy overview

Independent top-level sleeves first, then the **TIMING_ANOMALIES** meta-sleeve — one orchestrator unit that fans out to 8 calendar/clock sub-strategies (≈ the Calendar pool). Rows are ordered by pool to mirror the table above.

| Sleeve | Signal(s) | Entry | Exit | Edge | Interval |
|---|---|---|---|---|---|
| BTC EMA | LONG when EMA5 > EMA21, SHORT when EMA5 < EMA21 | at the next weekly candle's open | at the next weekly candle's open | trend following, captures multi-week directional moves, pays the spread/fee on whipsaws | 1W |
| ETH Bull Regime | Regime classifier reads bull (strong_bull) | at the next daily candle's open if regime has flipped into bull | at the next daily candle's open if regime has flipped out of bull | outperforms BTC on the way up (validated: strong_bull only — see analysis) | 1D |
| Chento Triple v3 | B1 money-flow CVD-divergence AND B5 LSR extreme (p10/p90) AND B7 multi-TF CVD aligned (1h/4h/1d/3d, \|z\|≥2) — all same direction; + 4 filter gates (no_tilt, no_resist_OB_2R, okx_aligned, skip_up_30d_shorts) | 15m bar close; uniform sizing T0 *(H_B + A4 ladder DISABLED in production per backward-only Pareto test — see [[chento-v3-p1-ladder-verdict]])* | stop = entry ±5×ATR(14), target ±6R, TIF 72h | mean-reversion at confluence of 3 extremes; asymmetric filter avoids the bull+short failure regime | 15m |
| ADX | ADX(14) crosses 25 from compression (<20 in last 20 bars); LONG if close>EMA50 (& >EMA150), SHORT if close<EMA50 | the crossover bar | opposite ADX flip, SL −2% spot, or trend exhaustion | catches medium-term BTC trends; takes the loss on reversal | 1D |
| Short Squeeze | 15m: London/NY session (07–21 UTC) AND Asia-grind macro AND sweep of prior session low AND perp/spot CVD divergence | sweep bar close | fixed-R target, fixed-R stop, or session-end time-stop (no trailing) | forced short-covers at swept lows squeeze when perp/spot order-flow disagrees | 15m |
| AI_QUANT *(experimental — live in paper, needs refinement)* | Opus 4.8 tool-use loop once/day (00:05–00:15 UTC) → {LONG/SHORT/FLAT, conviction}; conviction<30 → FLAT | daily decision; size = weight × conviction/100 | next day's decision flips/FLAT, or 10% price-move stop | broad-context discretionary trader catches regime shifts rules miss | 1D |
| Squeeze Bull *(to be built — composite, two OR'd entry rules under shared bull gate)* | Bull regime gate: BTC ret_30d > +10%; **Rule A (OI flush):** −2% OI drop in 4h AND price drop ≥ −0.5%; **Rule B (funding+CVD):** funding_z < −2.0 AND cvd_z > +0.5 with 4-bar CVD sustain; 24h shared cooldown across both rules | bar close after either rule fires | fixed-R: 2% stop / 3% target / TIF 48h (Rule A); 1% stop / 1.5% target / TIF 72h (Rule B) — calibration TBD per validation | bull-regime crypto-squeeze asymmetry — long capitulation bounce (Rule A) + positioning-extreme squeeze (Rule B); validated standalone +9.4R/yr / MAR 1.77; ~−0.08 monthly Pearson r vs Chento (diversifying) | 1h (Rule A) / 15m (Rule B) |
| **TIMING_ANOMALIES** *(meta — fans out to the 8 sub-strategies below)* | calendar/clock dispatcher; one orchestrator unit, per-sub signals | per-sub | per-sub | calendar & event anomalies, each regime/sentiment-filtered | per-sub |
| &nbsp;&nbsp;&nbsp;↳ FOMC | FOMC dates only (8/yr); filter on Fed phase + F&G + Polymarket cut-odds | T−10h before announcement (08:00 UTC) | T+0.5h after announcement | short-window event: drift up into announcement, fade after | event (8/yr) |
| &nbsp;&nbsp;&nbsp;↳ THU_BEAR (S-096 V4) | Thursdays within ±1d of CPI/NFP, not ±1d OPEX, prior-day regime non-bull → SHORT BTC+ETH | Thursday 00:00 UTC | Friday 01:00 UTC, or SL −1% spot | weekly Thursday selling pressure in macro-event-adjacent non-bull periods | weekly (Thu) |
| &nbsp;&nbsp;&nbsp;↳ PDO_L_RF (S-102) | daily gap-down ≥2%, then price retouches the prior daily open; regime not deeply bearish | the PDO retouch | scheduled time-stop, or SL | gap-fill mean-reversion long | 1D |
| &nbsp;&nbsp;&nbsp;↳ CPR (S-101) | 3d funding < p20 AND LSR < p20 AND close>EMA20 AND EMA20>EMA50 | next 1m bar after trigger | target +2.93% (BB upper), stop −5%, or 15-day time-stop | contrarian-position-with-trend → short squeeze | 1D |
| &nbsp;&nbsp;&nbsp;↳ R4_BTC | calendar window: Mon (wk1-2 of month); vol-gate de-levers 2.5×→1.0× in top-25% vol | 06:00 UTC | 18:00 UTC (12h), LONG | calendar-anchored intraday window, empirically positive | intraday (Mon) |
| &nbsp;&nbsp;&nbsp;↳ R4_ETH | calendar window: Tue (wk1-2) | Tue 20:00 UTC | Wed 20:00 UTC (24h), LONG | calendar-anchored intraday window | intraday (Tue) |
| &nbsp;&nbsp;&nbsp;↳ R4_BTC_V2 | calendar window: Wed+Fri (wk1-2) | 04:00 UTC | 14:00 UTC (10h), LONG | Wed/Fri (NFP-anticipation) intraday window | intraday (Wed/Fri) |
| &nbsp;&nbsp;&nbsp;↳ R4_ETH_V2 | calendar window: Wed+Fri (wk1-2) | 04:00 UTC | 14:00 UTC (10h), LONG | Wed/Fri intraday window on ETH | intraday (Wed/Fri) |
| S-078 Carry | 7-day avg BTC perp funding > 0% → spot-long + perp-short, equal notional (delta-neutral) | when 7d funding turns positive | 3 consecutive negative-funding days (no time-stop; held while funding stays positive) | harvest structurally-positive funding; PnL ≈ funding, not price | 1D (funding settles 3×/day) |


## Leverage & risk model

> **Status: planning** · `<study>` = needs a backtest before committing · fact sheet for design/refactor work.

### Core principle

- **Leverage is an output, not an input** — pick a *risk budget*; leverage = budget ÷ current market conditions.
- Keeps risk ~constant across calm and violent markets (a fixed multiple does the opposite).
- **One risk model per sleeve**, chosen by its structure:

| Model | Sleeves | Risk = | Formula | Example |
|---|---|---|---|---|
| **Vol-target** | Continuous directional — BTC EMA, ETH Regime | annualized volatility | `lev = clamp(floor, cap, target_vol / realized_vol)` | 35% ÷ 50% realized → **0.70×** |
| **Fixed-R** | SL'd discrete — Chento, ADX, Short Squeeze, AI_QUANT | stop distance × size | `notional = risk$ / stop%` (leverage emerges) | $200 risk ÷ 4% stop → **$5k notional** |
| **Fixed-notional** | Calendar (events/windows) + Carry (delta-neutral) | capital deployed | `notional = collateral × fixed_lev` | $3k × 5× → **$15k notional** |

- **Don't double-count vol** — an ATR stop already shrinks size when vol rises; don't vol-target it too.
- **Carry exception** — sits in the Continuous *pool* but is delta-neutral → uses **fixed-notional** and is **excluded from the pool's vol blend** (its ≈0 price-vol would otherwise inflate everyone's leverage).

### Layer stack — how one position's size is built

```
NAV
 └─ × pool allocation            (Continuous 50 / Premier 30 / Standard 20 / Calendar tbd)
     └─ × deploy fraction        (≤ 0.5 — the 50% cash buffer; structural liquidation insurance)
         └─ × sleeve weight      (1 / n strategies in the pool)        ……… = COLLATERAL
             └─ × risk-target lev (vol-lev / fixed-R / fixed-notional)
                 └─ × regime modifier (top-down macro tilt — see below)  … = NOTIONAL
```

- Cash buffer = **static** ceiling (gap protection); leverage = **dynamic** normalizer — complementary, not redundant.

### Vol-target — detail (Continuous pool)

```
pool_realized_vol = stdev( pool_daily_returns[-W:] ) × √365
pool_vol_lev      = clamp( floor, regime_cap, pool_target_vol / pool_realized_vol )
sleeve_notional   = collateral × pool_vol_lev
```

- Feed the **pool-blended** return (not per-sleeve, not whole-portfolio): captures EMA/ETH correlation for free — hedged → lever up, stacked → de-lever. *Carry is excluded from this blend.*
- **Direction-agnostic** — a short has the same vol as a long.
- ⚠️ **Blend source matters** *(added 2026-06-11)*: compute the blend from **underlying asset returns of the target exposures**, never from realized pool PnL — a mostly-flat pool (EMA between crosses, ETH non-bull) otherwise shows ≈0 vol and sizes its first entry at the **cap**, i.e. max leverage exactly when the pool has been blind.
- ⚠️ **Current code differs from this draft** *(verified 2026-06-11)*: `voltarget.leverage_for_day` runs target_vol **50%** with per-regime **caps** (option A: 3.0/2.5/2.0/1.5, floor 0.5) and serves the J+ family only. The 35% + option-B numbers here are a proposed *change*, settled by study S1 in the implementation plan.

**Example** — NAV $100k · target 35% · floor 0.5× · cap 2.0×. BTC EMA collateral = 50% × ½ × ⅓ = **$8,333**:

| Market | realized vol | target ÷ vol | after clamp | notional |
|---|---|---|---|---|
| Calm | 25% | 1.40× | 1.40× | $11.7k |
| Normal | 35% | 1.00× | 1.00× | $8.3k |
| Elevated | 50% | 0.70× | 0.70× | $5.8k |
| Crash | 90% | 0.39× | **0.50×** (floor) | $4.2k |

→ auto-cuts exposure into crashes — vs a flat 5× that holds full size into the vol spike.

### Fixed-R — detail (Premier, Standard)

```
risk$    = collateral × risk%_per_trade
notional = risk$ / stop_distance%      # leverage = notional / margin → emerges ~3–5×
```

- **Example** — $10k slice, risk 2% ($200), stop 4% → notional **$5,000**; posted at 5× = $1k margin. If stopped (−4%) you lose exactly $200 = 2% of the slice.
- Chento's "5×" is this byproduct, not a dial. Standard's **3 slots** cap concurrent R-at-risk.

### Regime modifier — separate layer, composes on top

Vol-target is bottom-up (reacts to *realized vol*); regime is top-down (a *macro state*). Pick one per pool:

| Option | Mechanism | Effect |
|---|---|---|
| **A. Cap** (current code) | regime sets max leverage | clips the top in risk-off |
| **B. Target-modulation** | regime scales `target_vol` (35% bull → 18% bear) | shifts the whole curve down |
| **C. Weight-only** | regime sets allocation; leverage stays pure vol-target | regime out of the risk dial |

- Draft: **Continuous → B** (no-SL; want bear to shrink the whole curve). **Premier / Standard → A or skip** (SL already bounds per-trade risk).

### Per-pool parameters (draft — all `<study>`)

| Pool | Model | Target / risk% | Window W | Regime | Floor / Cap |
|---|---|---|---|---|---|
| **Continuous** — EMA, ETH | vol-target | 35% ann | 30–45 d | B: target ×{bull 1.0, unc 0.6, bear 0.3} | 0.5× / 2.0× |
| &nbsp;&nbsp;↳ Carry (delta-neutral) | fixed-notional | notional cap | n/a | — | ~5× legs |
| **Premier** — Chento (1 slot) | fixed-R | ~1% / trade | n/a | A or skip | emerges ~5× |
| **Standard** — Short Squeeze, AI_QUANT, S-003 ADX, Squeeze Bull (4 candidates / 3 slots) | fixed-R | ~0.5% / trade·slot | n/a | A or skip | emerges |
| **Calendar** — 8 timing subs | fixed-notional | per-sub historical | n/a | per-sub | per-sub (10× / 5× / 1× / 2.5×) |

### Open decisions & topics to revisit

1. **Continuous target vol** — 35% on the deployed half (≈8% NAV/sleeve after the buffer + ⅓ split). Higher / lower? → backtest the EMA+ETH blend.
2. **Regime composition** — confirm B for Continuous, A / skip for the SL'd pools.
3. **Carry in Continuous** — ✅ **resolved: stays in Continuous** (mixed-model: 2 vol-targeted + Carry delta-neutral, excluded from the blend). Each pool is a live sub-account, so don't spawn one just for Carry.
4. **Idle-slot capital** — when ETH Regime is flat (non-bull), reallocate its slice to EMA or hold as cash? (Draft: cash.)
5. **Floor** — with the 50% cash buffer already in place, keep the 0.5× floor or allow lower in extreme vol?
6. **Calendar model + internal split** *(only if Calendar survives #8)* — fixed-notional per sub; budget distribution designed below in **TIMING_ANOMALIES — internal allocation**.
7. **Shared allocator** — one `leverage_for_pool(pool_returns, pool_cfg)` serving all pools (design only, not building yet).
8. **Calendar fate & sub-account count** ⚠️ — paper is weak across the timing subs; only **R4_BTC** looks profitable. Pools = live sub-accounts, so: (a) don't give Calendar a sub-account — keep the timing-anomalies in paper and fold R4_BTC into an existing pool only once it earns it; or (b) a minimal Calendar = R4_BTC only. Likely target: **3 sub-accounts** (Continuous / Premier / Standard) until Calendar validates. Merging Premier+Standard → 2 is possible but mixes validated Chento with experimental AI_QUANT — not recommended. → confirm with a per-sub paper-expectancy check.
9. **EMA flat until the next crossover — accept it** *(from 2026-06-07 trade audit)* — JPLUS_EMA_BTC has emitted **zero** live trades. Reason: its last weekly EMA(5/21) cross was **2025-11-09 → SHORT** and live paper started *after* it (2026-04-06), so the cold-start guard correctly refuses to cold-fill a ~7-month-old offside SHORT (entering short now is dangerous — we don't know the bottom). **This is correct behavior, not a broken emitter.** EMA enters only on the *next* genuine cross (→ LONG). Implication to accept: a "continuous" sleeve can sit flat for months and contribute nothing to the Continuous pool for long stretches — keep the no-cold-fill rule; revisit only if a flat continuous anchor is unacceptable.
10. **SL + lower-TF entry for continuous sleeves** *(deferred — revisit; not testing yet)* — continuous sleeves (EMA, ETH) have no SL and enter at the blunt weekly/daily open → high single-position variance. Two **complementary** refinements: (a) **lower-TF entry** — after the weekly trigger, drop to daily/4h and enter on a pullback/retest → better location + a logical stop site; (b) **structural SL + re-entry** at that LTF level. SL *alone* needs a wide weekly stop (BTC pulls back 20–30% inside uptrends → whipsaw), so pair it with (a). Both add dials to deliberately-simple sleeves → must earn it on a backtest. **Test when revisited:** 3 EMA variants over the same history — (1) current weekly-open/no-stop, (2) +daily entry, (3) +daily entry + structural stop + re-entry — compare MAR / maxDD / mean-R / fill-rate. Caveat: weigh effort here vs leaning on already-validated sleeves (only R4_BTC has live signal so far).

### Current-state audit findings *(2026-06-07 — gaps surfaced while drafting this plan)*

11. **SHORT_SQUEEZE registered but not in P-300 composition** — sleeve code exists ([strategies/sleeves/short_squeeze/](strategies/sleeves/short_squeeze/)), dispatcher registered in orchestrator's `STRATEGY_DISPATCH`, but has **zero composition entry** → zero trades possible. Plan places it in Standard pool. Decide: (a) add to current P-300 composition now with calibrated weight, (b) defer until tier migration creates the Standard pool variant, (c) retire if not worth the slot. Don't leave a dispatcher-registered sleeve uncomposed — silent failures are the worst kind.

12. **JPLUS_EMA_BTC + JPLUS_ETH_DAILY static weight=0% — but the regime allocation table overrides it** *(rewritten 2026-06-11 after code verification; the original claim "they'd open at zero size" was wrong)*. `_resolve_sleeve_weight` (orchestrator.py:430-451) consults `allocation.get_weight_pct(strategy_id, regime)` FIRST and falls back to the composition's static `weight_pct` only when the regime/table is unavailable. The table (allocation.py:77-82) gives EMA 0.50/0.30/0.30/0.30 and ETH_DAILY 0.20/0.10/0/0 across regimes under `CORE_ALLOC_CAP = 0.50` — so both WOULD open at table-resolved size on the next genuine cross (this is also how R4_BTC traded live despite its static 0%). The real finding: **two parallel weight systems exist and the global regime table silently wins**, yet this fact sheet's layer stack has no slot for it. Decide (= D2 in the implementation plan): pool variants own their weights in composition with the regime table made per-variant opt-in (recommended; regime influence re-enters as the pool's regime modifier), or the global table stays authoritative.

13. **CHENTO_TRIPLE_V3: zero live trades since paper deployment** *(2026-06-08 correction: this is NOT 90 days; the SQL query I ran used a 90-day WINDOW, but the sleeve has only been in paper trading ~1–2 weeks).* — at 10% weight in P-300, sleeve registered/dispatched. Natural fire rate is ~8/yr ≈ one trade per ~46 days on average. 0 trades in 1–2 weeks is consistent with normal cadence, NOT a red flag. Action: track that the next trigger fires correctly when it arrives; don't diagnose as a regression unless 2+ months pass without a single trade.

14. **TIMING_ANOMALIES subs dominate the live ledger — but check deployment-duration confound first** *(2026-06-08 correction: I conflated a 90-day SQL query window with sleeve deployment durations; some sleeves have been deployed weeks, others much longer. The raw 19-of-25 split is suggestive but not definitive without normalizing by per-sleeve deployment window).* — the qualitative pattern (most validated sleeves silent or uncomposed; most-active sleeves are the paper-weakest subs) likely still holds, but the evidence is weaker than the raw counts implied. Action: pull per-sleeve deployment date from the variant edit history, compute expected fire rate × deployment window, compare to actual count. Then revisit whether this is "wrong sleeves running" or "right sleeves with too-short observation window."

15. **50% cash buffer doesn't exist in current code** — `deploy_fraction` is new architecture. Present `open_paper_trade` computes `size = capital × allocation_pct/100 × leverage` with no buffer; current allocations sum to 60% (15+8+25+2+10) with no cash reserve. New code: `variant.spec.allocator_notes.deploy_fraction` (default 1.0 = no buffer, backward-compatible); existing variants stay unchanged, new pool variants opt in. Touches `strategies.trades.open_paper_trade`, `orchestrator._resolve_sleeve_weight`, possibly `margin_headroom.gross_cap_usdt`.

16. **DB hygiene: 60 disabled replay variants** — backtest artifacts (`p300_aggressive_v2_v1_0__replay_*`) accumulated in the variants table. Not actively dangerous, but: (a) make variant queries noisy, (b) clutter operator visibility into "what's actually composed and enabled," (c) may collide with future tier-migration scripts that filter by variant ID prefix. Decide retention policy: delete after N days closed, archive separately, or leave indefinitely.

### Process & posture findings *(2026-06-07 — root-cause patterns identified during the same audit)*

17. **Architecture migration mid-flight (legacy + two-phase dispatch coexist)** — orchestrator routes some sleeves through `STRATEGY_DISPATCH` (one-shot `try_fire_for_variant`) and others through `STRATEGY_TWO_PHASE_DISPATCH` (`try_decide_for_variant` → reconcile → `execute_for_variant`). P2.4d/e/f rollout has been incremental over the last 3 weeks. Operator cannot easily answer "what runs in what order each tick" without reading orchestrator source. Plan: finish migration to two-phase for all sleeves before tier work, then remove the legacy path and document the dispatch loop in one place.

18. **Memory drift / no consolidation process** — 60+ memory files exist in `.claude/projects/.../memory/`; some have stale labels (this session: "bear-favored" was wrong in 2 memories — [[oi-flush-findings]] and [[funding-cvd-divergence-findings]] — until fixed today). No periodic prune-or-supersede process. Older memories can mislead future sessions before conflicts are detected. Plan: monthly memory audit — mark superseded entries explicitly, consolidate per-topic, delete obsolete.

19. **No single-source-of-truth calibration log per sleeve** — CHENTO_TRIPLE_V3 alone has had 5+ config changes in 2 weeks (B7 implementation bug fix May 30, B1-anchor switch May 31, intra-bar walking Jun 4, regime filter re-enable Jun 3, ladder disable Jun 5, atr5/target6 threshold confirmed Jun 5). Each change justified individually; "what's actually calibrated right now" requires reading config.py + several memories + recent commit messages. Plan: per-sleeve calibration log file (one MD per sleeve, documenting current params + provenance of last change), updated as part of each config edit.

20. **No scheduled system-level audit** — per-sleeve and per-component audits happen reactively (Chento lookahead Jun 5, paper-trade leak Jun 5, this session's portfolio reality check). No periodic "is the whole portfolio doing what we say it does" pass. Drift accumulates silently between audits. Plan: monthly system audit memo — composition state vs plan, recent firings vs expected frequencies, memory-vs-code drift check, DB hygiene, explicit re-verification of [[bot-not-close-to-live]] claims (which may now be partly resolved but haven't been re-checked end-to-end).

21. **Research velocity > production absorption velocity** ⚠️ *(root cause behind #11–14)* — research has shipped many validated artifacts (Chento v1→v3 + multiple optimization passes, regime filter, OKX gate, OI flush + ablation, funding+CVD divergence, short_squeeze, 8 TIMING_ANOMALIES subs, FVG/LVN closures, threshold-normalization studies). Production has wired fewer (CHENTO_TRIPLE_V3 sleeve, two-phase dispatch, idempotency, ledger coherence, same-direction cap). Running paper portfolio is largely the *complement* of validated edges — least-validated subs fire, most-validated anchors are silent. Plan: research/feature moratorium 1-2 weeks while production catches up on the backlog. Reassess research pace after the backlog is cleared.

22. **Project posture not explicitly tracked per cycle** — each session has tended to mix feature-building with discovered gaps; features ship while gaps accumulate. This very session shipped same-direction cap + orchestrator wiring while surfacing #11-21. Plan: per-planning-cycle posture decision recorded at the top of the relevant plan/memory — explicit "this is a stabilization cycle" vs "this is a feature/research cycle" — and treat that label as binding for the cycle's duration.

### Archival decisions *(2026-06-07 onwards — sleeves explicitly retired from the vision)*

These sleeves are formally archived. Code may still exist transiently while a cleanup pass removes it; the vision portfolio does NOT include them.

- **CHENTO_LIMIT_BID (S-106)** — sibling Chento sleeve (long-side swing-base limit-bid entries + T1/T2/runner exits). Mechanism validated (mean R +0.30 v2) but marginal vs the +0.5R deployment threshold, and the portfolio already has enough long-only exposure. Retire: (a) remove sleeve code at [strategies/sleeves/chento_limit_bid/](strategies/sleeves/chento_limit_bid/), (b) archive backtest notebook + discovery notebook, (c) supersede [[chento-v1-sleeve-status]] memory with an archival note, (d) keep [[chento-strategy]] + [[chento-execution-rules]] memories for context but mark v3 candidate improvements as not pursued. *(Decision 2026-06-07.)*

### Design principles *(vision-level — apply to all new sleeve work)*

- **Long-only proliferation is a constraint, not a path of least resistance** — the portfolio already skews long (Chento takes both directions but is long-favored in our regime; Squeeze Bull is long-only; most TIMING_ANOMALIES subs are long; only THU_BEAR is reliably short). New sleeve proposals should justify their direction profile against existing book exposure. Prefer mechanisms with genuine short-side edge or short-side regime asymmetry over yet-another-long.

- **Sleeves are subject to retirement based on paper-trade evidence** — keeping a poorly performing sleeve "just in case" is anti-value. Once a sleeve has accumulated enough paper-trade data to make a statistically meaningful judgement, it must either earn its spot (post-cost expectancy + meaningful contribution to portfolio MAR or diversification) or be retired. Applies equally to top-level sleeves and TIMING_ANOMALIES sub-strategies. Concrete: retire when paper post-cost mean R is non-positive after N trades where N is calibrated per sleeve frequency (e.g. N=15 for swing-cadence, N=30 for daily-cadence). Retirement isn't a verdict on the underlying research — it's a verdict on whether the sleeve adds *portfolio* value at its current calibration.

- **Don't estimate time** — implementation/research durations are routinely wrong (e.g. Chento research was ~1 week, not "weeks"; the previous draft of this doc inflated estimates without basis). Plan by milestones and dependencies, not by week counts. When a calendar reference is genuinely useful (e.g. "let it run 30 days before judging"), state the criterion explicitly.

---

## Asset-agnostic scanner sleeve *(vision — new sleeve family, modeled on Paladin trader)*

A target-architecture pattern for a **new** sleeve family — NOT a retrofit of existing sleeves.

### The pattern (modeled on the Paladin trader being studied)

Paladin (TBD analysis) reportedly trades by scanning a multi-asset universe continuously and entering setups wherever they appear — different setup, different asset, different timing per opportunity. Reported success rate is high. The setup criteria, hold-time, risk model, and tooling are **not yet analyzed** — that analysis is prerequisite work before any implementation, mirroring the Chento journal-mining work that preceded CHENTO_TRIPLE_V3.

This pattern is **distinct from the existing portfolio sleeves**, not a way to amplify them. Existing sleeves (Chento, Short Squeeze, S-003, BTC EMA, ETH Regime, Carry, TIMING_ANOMALIES) stay locked to their validated asset(s). Chento in particular is BTC-locked because the source trader prefers BTC and per-asset edge validation is already heavy work; multi-asset Chento is explicitly out of scope.

### Conceptual shape (subject to Paladin analysis findings)

```
Scanner sleeve (asset-agnostic by design — Paladin-style)
├── Universe: configurable per scanner sleeve (BTC + ETH + top liquid perps)
├── Per tick, for each asset in the universe:
│   Evaluate THIS sleeve's setup criteria on the asset's recent data
│   If match → emit Intent(asset, direction, signal_payload)
├── Per-asset cooldown (firing on BTC doesn't block firing on ETH same tick)
├── Position management same as other sleeves (reconcile pipeline + per-asset trade row)
└── Sized per pool tier (likely Standard initially given experimental status)
```

The existing dispatch + reconcile + Intent infrastructure already supports per-asset dispatch — `Intent.asset` is plumbed, trades are keyed by asset, conflict resolver is per-asset. The new code is the scanner-style sleeve that emits multiple intents per tick (one per matching asset) rather than zero-or-one.

### Why this isn't a retrofit of existing sleeves

The earlier framing in this doc suggested making Chento/Squeeze Bull/Short Squeeze scanner-compatible. That framing was wrong. Reasons:

- Chento's source trader is BTC-specialised; multi-asset would need fresh per-asset edge validation that we don't have bandwidth for
- Squeeze Bull's bull-regime gate is BTC-cycle-keyed; alt regime isn't trivially the same
- Short Squeeze (S-105) is calibrated on BTC Asia/London/NY session microstructure; alt session dynamics differ
- Each existing sleeve already has open audit items (#11–14); adding multi-asset surface area before those are resolved compounds debt
- A purpose-built Paladin sleeve, designed multi-asset from the start, is cleaner than retrofitting BTC-validated sleeves and hoping edge transfers

### Prerequisite research (must precede any implementation)

Before building any scanner sleeve, complete a Paladin-trader analysis comparable in depth to the Chento journal work:

23. **Paladin trader style analysis** — what's his actual setup criteria? hold time? risk model? asset universe? tools/data sources he uses? direction profile? Until this is documented, the scanner sleeve is unspecified — we'd be building infrastructure with no signal. Concrete deliverables: a `studies/notebooks/paladin_journal/` folder mirroring `chento_journal/` with extracted trades + setup taxonomy + tooling map + initial backtest of inferred mechanism. Memory entry on completion.

### Open architecture questions *(deferred until #23 is done)*

These shape the scanner build but only matter after the sleeve's setup criteria are known:

24. **Where does the scanner live?** — (A) inside orchestrator's `_tick_composition` (simplest), (B) separate background service that emits to a signal queue, (C) per-sleeve internal iteration. Default: **A** until scale demands B.

25. **Per-asset cooldown vs sleeve-global cooldown** — natural read of the scanner pattern is per-asset cooldown; confirm with Paladin's observed behaviour.

26. **Universe scope first cut** — BTC + ETH (data exists, infra trivial) vs BTC + ETH + top-5 alts (new data ingestion + alt slippage/cost models). Default: start with BTC + ETH; expand only once edge transfer is paper-validated per asset.

27. **Tier placement** — Standard initially (experimental, slot-limited), with promotion to Premier on its own if MAR + correlation evidence warrants. NOT in a Premier slot from day one — the validated anchor sleeve there is Chento.

### What stays true regardless of Paladin's specific style

These risks apply to any multi-asset scanner sleeve, independent of the setup criteria:

- **Crypto correlation during stress** — 3 concurrent same-direction positions across major coins are largely one crypto-beta bet; same-direction concentration cap must scale with active universe
- **Cost modeling per asset** — 18bp on BTC perps doesn't transfer to alts (25–40bp typical with worse slippage); per-asset cost overrides required before going past BTC + ETH
- **Operational surface grows with universe size** — data freshness, per-asset feed reliability, per-asset sub-account margin if isolation matters; ingestion bandwidth is real work

### Composition with the tier architecture

A multi-asset scanner sleeve interacts with the pool/slot model:

- **Slot semantics** — one scanner sleeve with positions on BTC + ETH + SOL is genuinely one sleeve firing three trades, not three sleeves. Use `max_concurrent_positions = N` where N is the active universe size, with the per-direction cap as the over-aggregation backstop
- **Pool placement** — Standard at launch; the experimental flag stays until paper-trade data accrues per asset
- **Same-direction cap interaction** — if Paladin-style is long-biased AND existing portfolio is already long-biased (Design Principle), the cap binds quickly; consider a per-scanner-sleeve direction quota separate from the global cap

---

## TIMING_ANOMALIES — internal allocation

The Calendar pool hands **one** budget to TIMING_ANOMALIES; the meta-sleeve must split it across 8 sub-strategies that fire on independent calendar/clock triggers and **frequently overlap**. This is a second allocation layer *inside* the pool.

### Current behavior (the gap)

Today the meta-sleeve dispatches each sub-strategy **independently** ([timing_anomalies/signal.py](strategies/sleeves/timing_anomalies/signal.py)): each takes its own regime-adaptive `_effective_weight_pct` (% of NAV) and fires at that size. The top-level `weight_pct` is **informational only** ("sum-of-children") — there is **no enforced budget and no arbitration** between sub-strategies. If several fire at once, each draws its full weight and the total is whatever it sums to (bounded only by the orchestrator's global margin-headroom + same-direction cap, not by a Calendar-pool budget).

### Proposed: recurse the orchestrator's allocation policy one level down

Use the **same primitive** the orchestrator already uses — shared budget + fixed-priority + BLOCK-on-cap — applied *inside* the meta-sleeve. TIMING_ANOMALIES becomes a mini-orchestrator over its 8 children. Budget is in **collateral**; each sub applies its own fixed leverage on top (`notional = sub_collateral × sub_leverage`, per the Calendar risk model — Open decision #7).

```
budget    = Calendar-pool collateral (pool_alloc × ½ cash buffer)   # ENFORCED, not informational
committed = Σ collateral of currently-open sub-positions
for each sub firing this tick, in PRIORITY order (highest historical MAR first):
    want = sub.calibrated_size            # its regime-adaptive slice (today's _effective_weight_pct)
    if committed + want ≤ budget:  open at `want`; committed += want
    else:                          BLOCK (skip + log the drop — no silent cap)
never resize an already-open sub-position; it frees its slice only when it closes (time-stop / SL / target)
```

- **Each sub keeps its calibrated size** — we do *not* divide-by-N among the active set, which would make a sub's size depend on who else happens to be firing (non-deterministic, breaks per-sub calibration).
- **Priority** = historical edge/MAR. R4 family is the most validated (t=+4.6 IS / +2.5 OOS); the event/calendar subs carry in-sample caveats → lower priority. `<study>`
- **BLOCK, never resize** — mirrors the orchestrator allocation policy: a fresh high-priority fire can't shrink an open position, and a fire that doesn't fit is skipped and **logged** (never silently dropped).
- The existing global **same-direction concentration cap** still applies on top as a guardrail; this internal budget is the new layer between pool and subs.

### Concurrency patterns to size the budget against

| Pattern | Notes |
|---|---|
| **R4_BTC_V2 + R4_ETH_V2** | **always co-fire** (Wed/Fri 04–14 UTC) — different assets (diversifying), but the budget floor must fit both at once |
| **CPR** | ≤15-day hold → a near-permanent budget consumer that overlaps almost everything |
| **FOMC / PDO** | land on any weekday → can coincide with an R4 window |
| **THU_BEAR** | Thu only; rarely overlaps the R4 windows; BTC+ETH SHORT |

Realistic peak concurrency ≈ CPR (persistent) + an R4 pair + the occasional FOMC/PDO — **not** all 8 at once. So size the budget to that peak, **over-subscribed** vs the naive Σ(all 8), with BLOCK as the safety valve. Sizing to Σ(all 8) would leave the budget mostly idle (calendar strategies fire sparsely).

### Open questions

- **Priority ranking** of the 8 (by live/historical MAR) — R4 family looks highest; event/calendar subs are thinner/in-sample. `<study>`
- **Over-subscription level** — budget = Σ(all 8) (never blocks, low utilization) vs budget = realistic-peak (efficient, occasional BLOCK)? `<study>`
- **Same-asset netting** — when an R4 long-BTC and (rarely) a THU_BEAR short-BTC overlap: size **gross** (cross-margin pool + cash buffer absorbs the net) or **net** them? Draft: gross, keep v1 simple.
- **Reuse vs reimplement** — can the meta-sleeve literally call the pool/orchestrator allocator with `budget = Calendar pool` and `candidates = its 8 subs`? If so, **one** allocation function serves all three layers (pool → meta → sub) — the fractal/simplicity win. *(Implementation plan B5 answers: yes, reuse — one pure `allocate(budget, committed, candidates)` function at both layers.)*

---

## Glossary

Brief definitions of acronyms, sleeve-specific terms, and risk/research terminology used throughout this document. Standard trading vocabulary is defined briefly; project-specific terms get full provenance pointers.

### Trading terms (general)

| Term | Definition |
|---|---|
| **CVD** | Cumulative Volume Delta. Running sum of (buy volume − sell volume) over a window; reveals net taker flow independent of price. Used to detect order-flow divergence vs price. |
| **OI** | Open Interest. Total notional of unsettled perpetual contracts on an exchange. Rapid drops = liquidation cascades; rapid builds = positioning accumulation. |
| **LSR** | Long/Short Ratio. Long-side account count divided by short-side account count on a given exchange. Extremes (p10/p90) flag crowded positioning. |
| **Funding rate** | Periodic perp payment between long and short holders. Positive = longs pay shorts (longs crowded); negative = shorts pay longs (shorts crowded). |
| **Basis bp** | Basis-points spread between perp and spot price (positive = perp premium). |
| **ATR(N)** | Average True Range over N bars (typically 14). Vol-scaled bar range used as stop-distance multiplier. |
| **EMA(N)** | Exponential moving average over N bars. |
| **ADX(N)** | Average Directional Index. Trend-strength oscillator (0–100). ADX > 25 typically = trending; < 20 = ranging. |
| **F&G** | Crypto Fear & Greed index (0–100). |
| **OPEX** | Quarterly options expiry (front-month BTC/ETH options). |
| **Bollinger Band (BB upper)** | Mean ± Nσ envelope; "BB upper" = upper-band price level. |
| **Polymarket cut-odds** | Implied probability of a Fed rate cut from Polymarket prediction-market pricing. |

### Risk / research terms

| Term | Definition |
|---|---|
| **R** | Risk-multiple. 1R = distance from entry to stop. "6R target" = profit-direction level at 6× the stop distance. |
| **MAR** | Mean-annual-return ÷ max-drawdown. Risk-adjusted return; higher = better. |
| **MFE** | Maximum Favorable Excursion. Best price the position reached during its life. |
| **TIF** | Time-In-Force. Hard time-stop on a position. |
| **WR** | Win rate. |
| **IS / OOS** | In-sample / Out-of-sample (cutoff typically IS_END = 2024-12-31). |
| **Bootstrap CI** | Confidence interval estimated by resampling-with-replacement the trade ledger. |
| **Pearson r** | Linear correlation coefficient between two return series. |
| **z-score** | `(x − μ) / σ` over a rolling window. `\|z\| ≥ 2` ≈ ≥2σ from the rolling mean. |
| **p10 / p90 / p20** | 10th / 90th / 20th percentile of a rolling distribution. |
| **NAV** | Net Asset Value (variant capital + unrealized PnL). |

### Chento Triple v3 building blocks

| Term | Definition |
|---|---|
| **B1 — money-flow CVD-divergence** | Fires when 30d CVD z-score has opposite sign to short-term price velocity z-score (CVD says one thing, price hasn't moved). Source: [validation_B1_moneyflow_divergence.py](studies/notebooks/chento_journal/validation_B1_moneyflow_divergence.py). |
| **B5 — LSR extreme** | Fires at LSR p10 (oversold longs → SHORT) or p90 (euphoric longs → LONG) over 30d rolling. Source: validation_B5_lsr_extremes.py. |
| **B7 — multi-TF CVD aligned** | CVD z-score \|z\| ≥ 2 AND aligns same-sign across 1h/4h/1d/3d timeframes. Source: validation_B7_multitf_cvd.py. |
| **Triple intersection** | Production trigger: B1 ∩ B5 ∩ B7 same-direction within the trailing 24h (backward-only window). |
| **H_B (Hybrid adaptive sizing)** | Research design: T3 (150%) inside Value Area, T1 (50%) outside. **DISABLED in production** per [[chento-v3-p1-ladder-verdict]] — failed the backward-only Pareto test. Current sleeve uses T0 (uniform sizing). |
| **A4 ladder** | Research design: add margin at −0.3R adverse excursion. **DISABLED in production** alongside H_B. |
| **T0 / T1 / T2 / T3** | Sizing tiers: T0 = uniform/no ladder (current production), T1 = 50% add, T2 = 100% add, T3 = 150% add. |
| **VA / Value Area** | 7-day rolling volume profile bins covering 70% of volume. Used by H_B classifier (when enabled). |
| **Filter: no_tilt** | Skip if prior trade was a loser (`consec_losses_before == 0`). |
| **Filter: no_resist_OB_2R** | Skip if opposite-direction Order Block within 2R of entry (SMC-style structural filter). |
| **Filter: okx_aligned** | Require OKX-Binance perp delta z-score (rolling 7d) to align with trade direction. |
| **Filter: skip_up_30d_shorts** | Asymmetric regime gate: skip ONLY shorts when BTC ret_30d > +10%. See [[chento-regime-filter]]. |

### Squeeze Bull rules *(to be built)*

| Term | Definition |
|---|---|
| **Bull regime gate** | BTC ret_30d > +10% — precondition for both rules. |
| **Rule A — OI flush** | −2% OI drop in 4h AND price drop ≥ −0.5%. Captures long-flush capitulation. See [[oi-flush-findings]]. |
| **Rule B — funding+CVD** | funding_z < −2.0 AND cvd_z > +0.5 (with 4-bar CVD sustain). Captures positioning-extreme squeeze. See [[funding-cvd-divergence-findings]]. |

### Short Squeeze (S-105) terms

| Term | Definition |
|---|---|
| **Asia-grind macro** | Asia session: OI rose ≥ 0.5%, mean funding < 0, close below open. Signals shorts piled up overnight. |
| **Sweep** | Bar's low pierces the lowest low of the prior 6h (24 × 15m bars). |
| **perp_cvd_pct** | Rolling-90-day percentile of perp_cvd (low percentile = strong perp selling intensity). |
| **divergence_pct** | Rolling-90-day percentile of (spot_cvd − perp_cvd) (high = perp selling harder than spot — bullish divergence). |

### AI_QUANT terms

| Term | Definition |
|---|---|
| **Conviction** | 0–100 score returned by the daily LLM tool-use loop. Position size scales with conviction; < 30 → FLAT. |

### TIMING_ANOMALIES sub-strategy shorthand

| Term | Definition |
|---|---|
| **R4 family** | Calendar windows in weeks 1–2 of each month: R4_BTC (Mon), R4_ETH (Tue), R4_BTC_V2 / R4_ETH_V2 (Wed/Fri). |
| **THU_BEAR (S-096 V4)** | Thursdays near CPI/NFP, not near OPEX, prior-day regime non-bull → SHORT BTC+ETH. |
| **PDO_L_RF (S-102)** | Daily gap-down ≥ 2% + retouch of the prior daily open (gap-fill long). |
| **CPR (S-101)** | 3d funding < p20 AND LSR < p20 AND close > EMA20 (contrarian-with-trend → squeeze). |
| **FOMC** | Entry T−10h before Fed announcement (08:00 UTC); exit T+0.5h after. |

### Architectural / project terms

| Term | Definition |
|---|---|
| **Sleeve** | A single strategy module with its own signal + sizing + exit logic. |
| **Variant** | A portfolio container with capital + composition + spec. One paper-trade ledger per variant. |
| **Composition** | List of sleeves enabled within a variant, each with weight_pct, leverage, and (optionally) priority. |
| **Pool / Tier** | Continuous / Premier / Standard / Calendar. In the vision architecture each maps 1:1 to an exchange sub-account. |
| **Reconcile pass** | Orchestrator step that collects intents from all sleeves on a tick and applies priority, conflict resolution, and concentration caps. |
| **Intent** | A sleeve's "what I'd open if approved" payload (asset, direction, allocation_pct, leverage, conviction, priority). Frozen dataclass in `strategies.support.dispatch`. |
| **deploy_fraction** | Vision concept: per-variant multiplier limiting how much of a pool's allocation is deployed (50% cash buffer = 0.5). Not yet implemented. |
| **Two-phase dispatch** | Sleeve protocol: `try_decide_for_variant` emits Intent → reconcile → `execute_for_variant`. Some sleeves still use the legacy one-shot `try_fire_for_variant`. |
| **Same-direction cap** | Per-variant notional cap per direction. Implemented `reconcile_intents.same_direction_cap_usdt` but not yet activated on any variant. |
| **Scanner sleeve** | Vision concept: asset-agnostic sleeve that evaluates its setup criteria across a universe per tick and emits one Intent per matching asset. To be modelled on [[paladin-trader-research]]. |
| **MTF cells** | Multi-Timeframe signature cells from the [swing-base-mtf-findings] research. Used by the now-archived `chento_limit_bid` sleeve. |
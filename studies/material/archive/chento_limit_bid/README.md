# S-106 CHENTO_LIMIT_BID — long-side swing-base entries

Production sleeve adapted from the chento Discord-journal reverse-engineering
work ([studies/notebooks/chento_journal/](../../../studies/notebooks/chento_journal/)).

This sleeve approximates the trader's *limit-bid at the swing low* pattern,
with **v2 staged scale-out exits** (T1 / T2 / runner-trail) replicating his
documented exit playbook.

## Signal

A LONG trigger fires on a 15m boundary minute when ALL conditions hold:

1. **Cooldown** — at least 24h since the last trigger on this variant.
2. **Time window** — UTC hour ∈ [12:00, 17:00] (the NY-overlap session
   where his "1h before NY open" clockwork lives).
3. **Day of week** — Mon / Tue / Wed only.
4. **Active swing base** — a confirmed base exists in the last ~7 days
   where:
   - The 36h window low has ≥ 40% of bars within 1.2% of it (cluster).
   - Price has since expanded up by ≥ 4% within 3 days (confirmation).
5. **Approach** — current price within 1.2% above the base low.
6. **Confluence score ≥ 3 / 4** on the base window:
   - mean basis_bp ≤ −2 bp (futures discount)
   - mean funding < 0 (shorts paying)
   - OI dropped by ≥ 1.5% during the base window (capitulation flush)
   - spot CVD > 0 (absorption)
7. **MTF bias gate** — either:
   - `mtf_net ∈ {+1, +2}` (moderate-up-stack — the +1.53R sweet spot), OR
   - `mtf_sig == "--+++"` (the capitulation-bounce signature)

Per the discovery notebook
([swing_base_limit_bid](../../../studies/notebooks/swing_base_limit_bid/discovery.ipynb)),
the `mtf_net=+1 + conf_score=3` cell delivered **+1.53R per signal (n=9)** in
2022-2026 historicals. The `--+++` exact signature delivered +0.53R (n=9).

## Execution (v2 staged scale-out)

| Element | Value |
|---|---|
| Entry | Market on the trigger bar's close |
| Initial stop | `base_low × (1 − 0.020)` (2% below structural low) |
| **Tier 1** | Close **33%** at `entry + 1R` (matches chento's "partials only after 1R" rule) |
| **Tier 2** | Close **50% of remaining** at `entry + 3R` (~33% of original) |
| **Runner** | Remaining ~34% trails at **5% under high-water mark** (armed after T1) |
| Time stop | 21 days from entry (was 14 in v1 — chento holds longer) |
| SL behaviour | Only tightens — runner trail can ratchet stop UP, never widens |

The tier state machine lives in [math.py:evaluate_tier_transitions](math.py)
and is exercised by the sweep loop in [signal.py](signal.py) and the
backtest notebook
([studies/notebooks/chento_journal/chento_limit_bid_v1_backtest.ipynb](../../../studies/notebooks/chento_journal/chento_limit_bid_v1_backtest.ipynb))
using identical logic so the live behavior matches the backtest.

## Mapping to chento's actual playbook

The trader's full playbook in
[strategy_spec.md](../../../studies/notebooks/chento_journal/strategy_spec.md):

| Element | His playbook | v1 sleeve |
|---|---|---|
| Entry style | Limit-bid ladder, 4–5 tiers | Single market entry on approach |
| TP horizon | Variable (2.4% to 25%) at structural target | Fixed 3R |
| Stop | Tight (0.4–0.8% on scalps, 2–4% on swings) | 2% below base low |
| Scale-in | Yes, multi-tier | No (v1 limitation) |
| Scale-out | T1/T2/runner partials | No (v1 limitation) |
| TIF | 30–60 days | 14d |
| Asset | BTC / ETH / top-10 alts | BTC only |
| Direction | Both | LONG only (mirror short = v2) |

So v1 captures the **structural setup** but uses **simplified execution**.
The setup detection is the high-conviction part; the execution is the
operational part we can iteratively improve.

## Calibration

Defaults in `config.py` were taken from the discovery notebook's findings
on the +1/conf=3 (mean +1.53R, n=9) and --+++ (mean +0.53R, n=9) cells.

### v1 → v2 progression

| Metric | v1 (single 3R) | **v2 (T1/T2/runner)** |
|---|---|---|
| Signals | 22 | 22 (same setup) |
| Net mean R | +0.15 | **+0.30** (2x) |
| Win rate | 36% | **55%** |
| T1 (1R) hit rate | n/a | 59% |
| T2 (3R) hit rate | n/a | 23% |
| Median MFE captured | n/a | 1.98R |
| Median hold | 55h | 45h |

**Per MTF cell (v2):**
- `net_-3` (capitulation): n=10, mean R **+0.39**
- `net_+1` (moderate-up): n=12, mean R **+0.23**

**Per year (v2):** 2022 (-0.33, n=3), 2023 (+0.22, n=10), 2024 (-0.15, n=3),
**2025 (+1.27, n=3), 2026 (+0.70, n=3)**. Recent years strongly positive.

### Verdict — still MARGINAL but improved

v2 doubled v1's expectancy by capturing the asymmetric upside chento's
multi-tier exits target. Still under the +0.5R promotion threshold so
**weight_pct should remain 0** for now.

The remaining gap is closable via several v3 angles documented below.
Mechanism is sound; need a calibration push.

### Identified next-step improvements (v3 candidates)

1. **Limit-bid entry** (vs market): chento places limits 0.5-1.5% below
   current price. Better fills = +50-100bp on entry = +0.1-0.2R per trade.
2. **Wider initial stop** (vs 2%): currently 45% of trades stop out at
   initial SL with MFE never reaching +1R. Some of these would have hit
   T1 with a ~3% stop. Cost: SL hits become -1R drag of 3% (vs 2%) but
   T1 hit rate climbs.
3. **OP / ETH variants**: same MTF cells apply but on higher-vol assets
   ⇒ same R earned at faster compounding. Most promising single change.
4. **Closer T1**: T1 at 0.7R hits more often. Trade-off: smaller per-T1
   gain. Worth a sweep.
5. **Drop net_+1 cell**: net_+1 is only +0.23R. If we go net_-3 only,
   sample shrinks to 10 but mean climbs to +0.39R. May not pass n criterion.

### Deployment status

| Field | Value |
|---|---|
| `weight_pct` (spec_json) | 0 (defined but disabled) |
| `weight_pct` recommended once v3 reaches +0.5R | 2.0 |
| `leverage` | 5 (modest pending edge confirmation) |
| Status | **Mechanism validated. Edge marginal. Hold for calibration.** |

## Sleeve config (proposed for `spec_json`)

| Field | Recommendation | Why |
|---|---|---|
| `weight_pct` | 2.0 | Conservative initial allocation pending full backtest |
| `leverage` | 5 | Modest; the 2% SL = 10% margin risk at 5x |
| `priority` | 150 | Higher than EMA (200), lower than FOMC/CARRY |
| `params.cooldown_min` | 1440 | 24h — matches signal config |

Per-trade risk math:
- Stop is ~2.8-3.5% from entry (1.2% approach + 2% stop offset)
- At 5x leverage with 2% NAV allocation → ~0.07% NAV per stop
- Target +0.20% NAV per win (3R risk)
- Expected positive expectancy if mean-R ≥ +0.5

## What's NOT covered yet (deferred to later versions)

These are documented in
[strategy_spec.md](../../../studies/notebooks/chento_journal/strategy_spec.md)
and tracked as future sleeve versions:

- ✅ **v2 (shipped 2026-05-20)**: Staged partial scale-outs (T1 at 1R 33% / T2 at 3R 50% of remaining / runner trail 5%).
- **v3**: Multi-tier limit-bid ladder ENTRIES (3–5 tiers across the approach band) — chento's actual entry mechanism rather than the current market-on-touch.
- **v4**: Short-side mirror (detect swing-high tops, mirror confluence scoring, same MTF cells inverted: net_+3 / +5 / `++---`).
- **v5**: OP / ETH variants — same MTF framework on higher-volatility assets.
- **v6**: Liquidation-cluster-aware entry (replaces swing-low base with
  Leviathan-style liquidation map; requires `cd_liquidations` data ingest).
- **v7**: Probability indicator (lunar + calendar + MTF + session).

Each addition requires new infrastructure documented in `strategy_spec.md`.

## Data dependencies

- `cd_futures_15m` and `cd_spot_15m` in prod.db (taker buy/sell split, hourly
  cadence maintained by `data/sources/binance.py`).
- `cd_open_interest` (hourly).
- `cd_funding_rate` (8h).
- `btc_1m` (for MTF bias resampling at M/W/D/4h/1h).

All present in `data/databases/prod.db` after the 2026-05-18 PK migration.

## Related artifacts

- **Research notebook**: [discovery.ipynb](../../../studies/notebooks/swing_base_limit_bid/discovery.ipynb)
  — the original MTF cell map study that informs the gate selection.
- **Strategy doc**: [strategy_spec.md](../../../studies/notebooks/chento_journal/strategy_spec.md)
  — the full discretionary playbook this sleeve approximates.
- **Findings log**: [findings.md](../../../studies/notebooks/chento_journal/findings.md)
  — running notes from the journal-mining work.
- **Memory**: [`project_chento_strategy`, `project_chento_tool_stack`,
  `project_chento_execution_rules`] in `/memory/` index.

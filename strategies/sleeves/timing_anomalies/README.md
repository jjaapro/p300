# TIMING_ANOMALIES — meta-sleeve for calendar/clock edges

Single orchestrator-level sleeve that consolidates the date/time-driven
sub-strategies into one unit. Internally dispatches to multiple
sub-strategies on every tick; each one's intents flow into the
orchestrator's reconcile pass alongside intents from other top-level
sleeves (S-003 ADX, S-078 CARRY, SHORT_SQUEEZE, AI_QUANT, EMA, etc.).

## Why this exists

The user-driven framing (2026-05-18): the existing sleeves divide cleanly
into two categories:

- **Statistical timing edges** — positive expectancy in specific calendar
  / clock windows with no microstructure mechanism (FOMC, R4 family,
  THU_BEAR, PDO, CPR, day-of-week effects).
- **Microstructure setups** — causal stories grounded in order flow,
  positioning, liquidation cascades (SHORT_SQUEEZE; future funding /
  liquidation / ETF-flow sleeves).

Two-category portfolio composition is cleaner than 8+ flat sleeves: each
category gets its own allocation budget, and the timing-anomaly bucket
shares a single budget across its sub-strategies instead of each one
competing for capital independently.

See `project_portfolio_direction_2026Q2` in Claude Code's auto-memory for
the framing, and [BACKLOG.md](../../../BACKLOG.md) "Consolidate
timing-anomaly sleeves under a single bucket" for the deferred-scope
checklist.

## Sub-strategies in this bucket

| Name | Origin sleeve | Trigger |
|---|---|---|
| `FOMC` | strategies/sleeves/fomc | T-10h before FOMC announcement; phase + F&G filtered |
| `THU_BEAR` | strategies/sleeves/thu_bear | Thursday 00:00 UTC short, Friday 01:00 UTC close; regime-gated |
| `PDO_L_RF` | strategies/sleeves/pdo | Daily PDO retouch (long); BTC + ETH |
| `CPR` | strategies/sleeves/cpr | Contrarian positioning reversal; BTC + ETH |
| `R4_BTC` | strategies/sleeves/r4 | Mon/Wed wk1-2 06:00→18:00 UTC |
| `R4_ETH` | strategies/sleeves/r4 | Tue 20:00 → Wed 20:00 UTC (Wed day≤14) |
| `R4_BTC_V2` | strategies/sleeves/r4 | Wed/Fri wk1-2 04:00→14:00 UTC |
| `R4_ETH_V2` | strategies/sleeves/r4 | Wed/Fri wk1-2 04:00→14:00 UTC (cross-asset application) |

**Not in this bucket** (explicitly): AI_QUANT (news-reactive), S-003 ADX
(price-action), S-078 CARRY (funding state), JPLUS_EMA_BTC (trend regime
— kept as a separate gate variable), JPLUS_ETH_DAILY (continuous, not
date-driven), SHORT_SQUEEZE (microstructure).

## Architecture: delegation, not code movement (this commit)

This first commit takes a **delegation** approach: each substrategy's
logic stays in its original location (`strategies/sleeves/fomc/`,
`strategies/sleeves/thu_bear/`, etc.). The meta-sleeve calls into them
via the registry in `internal/__init__.py`.

Why: zero refactor risk to working sleeve code, no test breakage, no
variant-spec migration required to get the new dispatch path running.

A follow-up commit can physically move each substrategy's code into
`internal/{fomc,thu_bear,pdo,cpr,r4}/` with the original sleeve dirs
becoming thin re-export shims, if desired. The dispatch contract here
won't change.

## Composition contract

A variant opts into the meta-sleeve by adding this to its `composition`:

```python
{
  "strategy_id": "TIMING_ANOMALIES",
  "weight_pct": 35.0,                  # informational sum-of-children
  "params": {
    "substrategies": {
      "FOMC": {
        "enabled": True, "weight_pct": 5.0, "leverage": 10.0,
        "params": {"stop_loss_pct": 5.0},
      },
      "THU_BEAR": {
        "enabled": True, "weight_pct": 6.0, "leverage": 5.0,
        "params": {"version": "V4_event_conditioned",
                    "assets": ["BTC", "ETH"], "stop_loss_pct": 5.0},
      },
      "PDO_L_RF": {"enabled": True, "weight_pct": 9.0, ...},
      "CPR":      {"enabled": True, "weight_pct": 5.0, ...},
      "R4_BTC":   {"enabled": True, "weight_pct": 0.0, ...},
      "R4_ETH":   {"enabled": True, "weight_pct": 0.0, ...},
      "R4_BTC_V2":{"enabled": True, "weight_pct": 0.0, ...},
      "R4_ETH_V2":{"enabled": True, "weight_pct": 0.0, ...},
    },
  },
}
```

Each substrategy's `weight_pct`, `leverage`, and `params` shape mirrors
what it consumed as a standalone sleeve_cfg, so migration from the old
flat composition is straightforward.

The trader-relevant invariant: **a single sleeve can open multiple
trades on the same tick**. PDO can emit BTC + ETH legs; THU_BEAR can
emit BTC + ETH shorts; FOMC can fire on its hour. The dispatcher
returns all intents in one list; the orchestrator's reconcile pass
handles them downstream.

## Backward compat

The legacy flat-composition entries (FOMC, S-096, PDO-L-RF, CPR,
JPLUS_R4_BTC, JPLUS_R4_ETH, etc.) remain registered in the orchestrator.
Existing variants continue to dispatch via the old path until their
`spec_json` is migrated to the consolidated TIMING_ANOMALIES form. New
variants should use the consolidated form going forward.

Tests for individual sub-sleeves stay where they are, importing from the
original sleeve modules — those modules are still the source of truth
for substrategy logic.

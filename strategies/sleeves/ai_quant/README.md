# AI_QUANT — Discretionary LLM trader (experimental, default-OFF)

A Claude Opus tool-use loop runs once per UTC day in a 10-minute window
(00:05–00:15 UTC) and emits a LONG / SHORT / FLAT decision on BTC perp.
Phase-1 experiment; off by default behind `AI_QUANT_ENABLED=true`.

## Signal

Daily fire on the first minute that passes all four gates (cheap-first
order, evaluated each tick):

1. Kill-switch: `AI_QUANT_ENABLED` env must be `true` (case-insensitive).
2. Time-window: 00:05–00:15 UTC.
3. Per-day already fired (idempotent against `ai_quant_decisions`).
4. Daily API cost cap (default $5/day; see `DEFAULT_DAILY_COST_CAP_USD`).

On the qualifying tick, [context.py](context.py) builds the context bundle
(regime, F&G, funding, recent vol, open positions, etc.), [chart.py](chart.py)
renders a 90-bar daily chart, and [decision.py](decision.py) runs the
Anthropic tool-use loop.

## Output schema

Model returns `direction ∈ {LONG, SHORT, FLAT}`, `conviction_0_100`,
`time_horizon_days`, `key_drivers[]`, plus structured `exit_conditions`
and `confidence_caveats`. Conviction floor: anything `< MIN_CONVICTION_FOR_TRADE`
(30) is forced to FLAT regardless of stated direction.

## Sizing

`allocation_pct = weight_pct × (conviction / 100)`, capped at the
configured sleeve weight (2% default). A conviction-50 LONG sizes to 1% of
capital at 3× leverage; a conviction-100 LONG sizes to the full 2%.

## Reconciliation

Each day's decision is reconciled against any open AI_QUANT position:
open / hold / close / flip. No mid-day scaling in v1. Exit is only via the
next day's flip/FLAT or the configured `stop_loss_pct=10.0` price-move
stop (no fixed time-stop).

## Backtest behavior

`params.deterministic=False` consumed by [backtest_runner.py](../../../backtest_runner.py)
to **skip** AI_QUANT on historical replay — LLM outputs are
non-deterministic and replay would produce different decisions each run.

## Audit trail

Every fire writes a row to `ai_quant_decisions` via [journal.py](journal.py)
(decision payload, tool calls, token usage, cost, resulting trade action).
[archive.py](archive.py) mirrors each row to
`data/ai_quant_archive/{date}_{variant}_{asset}_{decided}_id{N}.md` for
human review. Regenerable from the DB via [archive_rebuild.py](archive_rebuild.py).

## Edge thesis

A discretionary trader with broad context (macro, sentiment,
microstructure, chart) may catch regime shifts that the rule-based
sleeves are structurally blind to. Whether the model beats its own API
cost net of slippage is the open question this experiment exists to
answer.

## Files

- [signal.py](signal.py) — four-gate short-circuit + daily decision/reconciliation
- [config.py](config.py) — entry window, cost cap, conviction floor, defer limits
- [context.py](context.py) — context bundle builder
- [chart.py](chart.py) — chart rendering
- [prompt.py](prompt.py) — system prompt + tool schemas
- [tools.py](tools.py) — server-tool implementations
- [decision.py](decision.py) — Anthropic tool-use loop
- [journal.py](journal.py) — `ai_quant_decisions` writer
- [archive.py](archive.py) — markdown mirror writer
- [cvd.py](cvd.py) — CVD bucket aggregation helper
- [chart_cli.py](chart_cli.py) — operator CLI for chart rendering (was `tools/render_ai_chart.py`)
- [archive_rebuild.py](archive_rebuild.py) — operator CLI to regenerate the markdown archive (was `tools/ai_quant_archive_rebuild.py`)

Outstanding work tracked in [BACKLOG.md](../../../BACKLOG.md): decision-history
carryover commitments + post-hoc P&L self-calibration.

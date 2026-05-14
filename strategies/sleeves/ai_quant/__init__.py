"""AI_QUANT sleeve — discretionary LLM trader (experimental, default-OFF).

Entrypoint: signal.try_fire_for_variant. The module hosts the four-gate
short-circuit and the daily decision/reconciliation loop. Heavy lifting
lives in sibling modules:

  context.py   — build the LLM context bundle (regime, funding, F&G, etc.)
  chart.py     — render the daily decision chart sent to the model
  prompt.py    — system prompt + tool schemas
  tools.py     — server-tool implementations (chart fetch, deeper context)
  decision.py  — Anthropic tool-use loop, parses model output
  journal.py   — DB writer for ai_quant_decisions rows
  archive.py   — markdown mirror writer (one .md per decision)
  cvd.py       — CVD bucket aggregation used in the context bundle

Operator scripts (CLIs, not part of bot.py runtime):
  chart_cli.py        — render a sample chart PNG for visual sanity-check
  archive_rebuild.py  — regenerate the markdown archive from DB rows
"""

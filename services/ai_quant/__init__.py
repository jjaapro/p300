"""services.ai_quant — AI quant trader sleeve internals.

The sleeve entrypoint is services.ai_quant_service.try_fire_for_variant; this
package holds the heavy lifting (chart rendering, context bundling, prompt,
Anthropic tool-use loop). See PORTFOLIO.md and the plan in
~/.claude/plans/how-could-we-implement-gentle-quail.md for the contract.
"""

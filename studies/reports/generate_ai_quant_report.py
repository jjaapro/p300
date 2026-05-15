"""Preview an AI_QUANT decision against the current data -- no side effects.

Runs the same context-build -> chart-render -> Anthropic-tool-loop pipeline
the live AI_QUANT sleeve uses, then renders the result with the same
markdown template the live archive uses. The .md is written under
``data/ai_quant_preview/`` so it sits next to (but separate from) the
real archive at ``data/ai_quant_archive/``.

Crucially this script is **side-effect free** with respect to the trading
system:

  * No row written to ``ai_quant_decisions``.
  * No paper trade opened or closed.
  * No effect on the once-per-day idempotency gate or the daily cost cap
    (those gates read the journal table, which we don't touch).
  * ``AI_QUANT_ENABLED`` does not need to be set; gates are bypassed.

What it DOES do: makes one real Anthropic API call against your
``ANTHROPIC_API_KEY`` (typically a few cents per run; subject to model
and tool usage). The cost is printed at the end.

Usage:
    python studies/reports/generate_ai_quant_report.py
    python studies/reports/generate_ai_quant_report.py --asset BTC
    python studies/reports/generate_ai_quant_report.py --no-web         # disable web_search/web_fetch
    python studies/reports/generate_ai_quant_report.py --print-context  # dump context bundle, no API call
    python studies/reports/generate_ai_quant_report.py --out path.md    # override output path
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from strategies.support import clock, db  # noqa: E402
from strategies.sleeves.ai_quant import archive, chart, context as ctx_mod, decision as decision_mod  # noqa: E402
from strategies.support.env import load_env_file  # noqa: E402

DEFAULT_VARIANT = "p300_aggressive_v2_v1_0"
DEFAULT_ASSET = "BTC"
PREVIEW_DIRNAME = "ai_quant_preview"
PREVIEW_TRADE_ACTION = "PREVIEW (no DB row, no trade emitted)"


def _preview_dir() -> Path:
    return Path(db.DASH_DB).parent / PREVIEW_DIRNAME


def _preview_filename(*, decision_date: str, variant_id: str, asset: str,
                      decided: str, hhmmss: str) -> str:
    safe = archive._safe_token
    return (
        f"{safe(decision_date, 'NA')}_"
        f"{safe(variant_id, 'variant')}_"
        f"{safe(asset.upper(), 'ASSET')}_"
        f"{safe((decided or 'ERROR').upper(), 'ERROR')}_"
        f"preview_{hhmmss}.md"
    )


def _result_to_row(*, result, variant_id: str, asset: str,
                   decision_ts: int, decision_date: str) -> dict:
    """Map DecisionResult to the dict shape archive._render_markdown expects.
    Mirrors journal.save_decision's archive call (strategies/sleeves/ai_quant/journal.py:150)
    but stamps id='PREVIEW' and trade_action with the preview marker."""
    payload = result.decision or {}
    usage = result.usage or {}
    return {
        "id": "PREVIEW",
        "decision_utc": decision_ts,
        "decision_date": decision_date,
        "variant_id": variant_id,
        "asset": asset.upper(),
        "decided": payload.get("direction") or "ERROR",
        "conviction": payload.get("conviction_0_100"),
        "time_horizon_days": payload.get("time_horizon_days"),
        "key_drivers_json": json.dumps(
            payload.get("key_drivers") or [], default=str),
        "exit_conditions": payload.get("exit_conditions"),
        "confidence_caveats": payload.get("confidence_caveats"),
        "rationale_md": payload.get("rationale_md"),
        "tool_calls_json": json.dumps(result.tool_calls, default=str),
        "model_id": result.model_id,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "cache_write_tokens": usage.get("cache_creation_input_tokens"),
        "cost_usd": result.cost_usd,
        "turns": result.turns,
        "trade_action": PREVIEW_TRADE_ACTION,
        "error": result.error,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--variant", default=DEFAULT_VARIANT,
                    help=f"variant_id used for the context lookup "
                         f"(default: {DEFAULT_VARIANT})")
    p.add_argument("--asset", default=DEFAULT_ASSET,
                    help="asset (default: BTC; only BTC supported in v1)")
    p.add_argument("--no-web", action="store_true",
                    help="disable web_search / web_fetch server tools")
    p.add_argument("--print-context", action="store_true",
                    help="build and print the context bundle, do NOT call the API")
    p.add_argument("--max-turns", type=int,
                    default=decision_mod.DEFAULT_MAX_TURNS,
                    help="max tool-use turns (default: %(default)s)")
    p.add_argument("--out", default=None,
                    help="explicit output path; default is "
                         "data/ai_quant_preview/{auto-named}.md")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("ai_quant_preview")

    load_env_file()  # picks up ANTHROPIC_API_KEY / AI_QUANT_MODEL / etc.

    if args.print_context:
        ctx = ctx_mod.build_context(args.variant, args.asset)
        sys.stdout.write(json.dumps(ctx, indent=2, default=str))
        sys.stdout.write("\n")
        return 0

    log.info("building context for variant=%s asset=%s",
             args.variant, args.asset)
    context_bundle = ctx_mod.build_context(args.variant, args.asset)

    log.info("rendering baseline chart")
    baseline_png = chart.render_chart(
        asset=args.asset, timeframe="1d", lookback_bars=90,
        indicators=None, open_positions=None,
    )

    log.info("calling Anthropic — this incurs real API cost")
    result = decision_mod.run_decision(
        variant_id=args.variant,
        asset=args.asset,
        open_positions=None,
        include_server_tools=not args.no_web,
        max_turns=args.max_turns,
        context_bundle=context_bundle,
        baseline_chart_png=baseline_png,
    )

    now_utc = clock.now_utc()
    decision_ts = int(now_utc.timestamp())
    decision_date = now_utc.date().isoformat()
    hhmmss = now_utc.strftime("%H%M%S")
    row = _result_to_row(
        result=result, variant_id=args.variant, asset=args.asset,
        decision_ts=decision_ts, decision_date=decision_date,
    )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        d = _preview_dir()
        d.mkdir(parents=True, exist_ok=True)
        out_path = d / _preview_filename(
            decision_date=decision_date, variant_id=args.variant,
            asset=args.asset, decided=row["decided"], hhmmss=hhmmss,
        )

    md = archive._render_markdown(row)
    out_path.write_text(md, encoding="utf-8")

    if result.decision is None:
        log.error("decision call did not produce a payload: %s", result.error)
    else:
        log.info("decision: %s @ conviction %s (turns=%s, cost=$%.4f)",
                 result.decision.get("direction"),
                 result.decision.get("conviction_0_100"),
                 result.turns, result.cost_usd)
    log.info("preview written: %s", out_path)
    sys.stdout.write(str(out_path) + "\n")
    return 0 if result.decision else 1


if __name__ == "__main__":
    raise SystemExit(main())

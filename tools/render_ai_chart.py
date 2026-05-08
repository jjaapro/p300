"""CLI front-end to services.ai_quant.chart.render_chart for visual sanity-checking.

Examples:
    python tools/render_ai_chart.py --out c:/tmp/sample.png
    python tools/render_ai_chart.py --timeframe 4h --lookback 120 --out c:/tmp/4h.png
    python tools/render_ai_chart.py --no-funding --no-lsr --out c:/tmp/price_only.png

The CLI takes no DB-mutating actions; it only reads from data/trader.db.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from repo root without installing the package.
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from services.ai_quant.chart import render_chart


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render the AI_QUANT decision chart as a PNG.")
    p.add_argument("--asset", default="BTC", help="asset (only BTC in v1)")
    p.add_argument("--timeframe", default="1d", choices=["1h", "4h", "1d"])
    p.add_argument("--lookback", type=int, default=90, help="number of bars to display")
    p.add_argument("--out", required=True, help="output PNG path")
    p.add_argument("--no-ema50", action="store_true")
    p.add_argument("--no-ema150", action="store_true")
    p.add_argument("--no-funding", action="store_true")
    p.add_argument("--no-lsr", action="store_true")
    args = p.parse_args(argv)

    indicators = []
    if not args.no_ema50:
        indicators.append("ema50")
    if not args.no_ema150:
        indicators.append("ema150")
    if not args.no_funding:
        indicators.append("funding")
    if not args.no_lsr:
        indicators.append("lsr")
    indicators.append("open_positions")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    png = render_chart(
        asset=args.asset,
        timeframe=args.timeframe,
        lookback_bars=args.lookback,
        indicators=indicators,
        open_positions=None,
        out_path=out_path,
    )
    print(f"wrote {out_path}  ({len(png):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

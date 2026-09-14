"""Build the Markdown-only ORB plan notebook; no data access or backtest execution."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def build() -> Path:
    folder = Path(__file__).resolve().parent
    plan = (folder / "TEST_PLAN.md").read_text(encoding="utf-8")
    research = (folder / "RESEARCH.md").read_text(encoding="utf-8")
    introduction = """# ORB research and testing plan

**Planning only — 2026-09-14. No strategy has been executed in this notebook.**

Scope: BTC/ETH first, with equities and index futures as optional extensions.

Read the [research review](RESEARCH.md) for source-by-source evidence, exact data
inventory, existing project studies and validation-helper limitations. This notebook
contains the full [testing plan](TEST_PLAN.md), divided into reviewable sections.
The Markdown documents are the source of truth; regenerate this file after editing them.
"""
    overview = """## Research findings that shape the plan

- Distinguish a genuine completed-range breakout, opening-candle momentum and a
  range-break reversal; they require separate tests and controls.
- Literature review covers original equities, Nasdaq ETF and futures research,
  implementation recreations, limited crypto evidence and data-quality investigations.
- The local minute histories are principally spot. Long-history perpetual minute
  paths and verified settlement funding need preparation before execution claims.
- Preserve historical holdout boundaries during cost calibration as well as signal research.
- Use complete ledgers, causal timestamps, bounded trial families and joint date-block
  inference. Keep economic magnitude and execution confidence visible in each verdict.

Sources and qualifications are in [RESEARCH.md](RESEARCH.md); proposed parameters
below are study-design choices, not empirically selected winners.
"""
    sections = [introduction, overview, *re.split(r"(?m)(?=^## )", plan)]
    cells = []
    for index, section in enumerate(sections):
        if not section.strip():
            continue
        cell_id = hashlib.sha256(f"{index}:{section}".encode("utf-8")).hexdigest()[:12]
        cells.append({"cell_type": "markdown", "id": cell_id, "metadata": {},
                      "source": section.splitlines(keepends=True)})
    notebook = {
        "cells": cells,
        "metadata": {
            "language_info": {"name": "python"},
            "orb_study": {
                "status": "DRAFT_PLAN_NO_OUTCOMES",
                "date": "2026-09-14",
                "source_sha256": {
                    "TEST_PLAN.md": hashlib.sha256(plan.encode("utf-8")).hexdigest(),
                    "RESEARCH.md": hashlib.sha256(research.encode("utf-8")).hexdigest(),
                },
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    destination = folder / "00_research_and_plan.ipynb"
    destination.write_text(json.dumps(notebook, indent=1, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    return destination


if __name__ == "__main__":
    print(build())

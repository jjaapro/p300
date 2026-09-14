"""Assemble coverage and a Markdown-only audit notebook; never execute studies."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import hashlib
import json
import re
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
INDEX_FILES = [
    HERE / "root_review_index.json",
    BASE / "adx_eth_2026_09" / "validation_review_index.json",
    BASE / "chento_journal" / "validation_review_index.json",
    BASE / "basis_carry" / "validation_review_index.json",
]


def main():
    inventory = json.loads((HERE / "notebook_inventory.json").read_text(encoding="utf-8"))
    entries = []
    for path in INDEX_FILES:
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries.extend(payload["studies"] if isinstance(payload, dict) else payload)
    entries.sort(key=lambda x: (x["priority"], x["study"]))
    assert len({x["study"] for x in entries}) == len(entries), "Duplicate study review"
    expected = {n["notebook"] for n in inventory["notebooks"] if n["scope"] == "review"}
    coverage = {}
    for entry in entries:
        assert (BASE / entry["review_path"]).is_file(), entry["review_path"]
        assert entry["priority"] in ("P0", "P1", "P2"), entry
        for notebook in entry["notebooks_reviewed"]:
            assert notebook in expected, f"Unexpected original notebook: {notebook}"
            assert notebook not in coverage, f"Duplicate coverage: {notebook}"
            coverage[notebook] = entry["review_path"]
    assert set(coverage) == expected, f"Missing notebook coverage: {expected-set(coverage)}"
    original_dirs = set(inventory["directories"]) - {"orb_study"}
    reviewed_dirs = {x["study"] for x in entries} & original_dirs
    assert reviewed_dirs == original_dirs, f"Missing directory coverage: {original_dirs-reviewed_dirs}"
    unchanged = []
    for original in inventory["notebooks"]:
        path = BASE / original["notebook"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == original["sha256"], f"Original notebook changed: {path}"
        unchanged.append(original["notebook"])
    counts = dict(sorted(Counter(x["priority"] for x in entries).items()))
    zero_counts = sum(n["code_cells"] > 0 and n["code_cells_with_execution_count"] == 0
                      for n in inventory["notebooks"] if n["scope"] == "review")
    result = {
        "date": "2026-09-14", "method": inventory["method"],
        "scope": {"original_notebooks_reviewed": len(expected), "original_directories_reviewed": len(original_dirs),
                  "review_plans": len(entries), "priorities": counts,
                  "notebooks_without_saved_execution_counts": zero_counts,
                  "orb": "Existing research/test plan preserved; excluded from this new audit"},
        "review_plans": entries, "notebook_coverage": dict(sorted(coverage.items())),
    }
    (HERE / "audit_index.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["# Study validation audit and follow-up plans", "",
             "**2026-09-14 — static review and planning only.** Testing quality is uneven: there are sound rejection/parity studies, incomplete evidence, and specific causality/accounting defects. This review does not certify the strategies or claim that every possible issue has been found.", "",
             f"Reviewed **{len(expected)} existing non-ORB notebooks** and supporting study artifacts across **{len(original_dirs)} existing study directories**, including script-only directories and the top-level notebook families. Added **{len(entries)} tailored review plans**. The [existing ORB plan](../orb_study/TEST_PLAN.md) remains unchanged.", "",
             "## Start here", "",
             "- [Cross-study findings and suggested work order](REVIEW_FINDINGS.md)",
             "- [Shared validation protocol](VALIDATION_PROTOCOL.md)",
             "- [Notebook version of this overview, findings and protocol](00_review_and_plan.ipynb)",
             "- [Machine-readable coverage and findings](audit_index.json)",
             "- [Original notebook inventory, hashes and execution metadata](notebook_inventory.json)", "",
             "## Reading priorities", "",
             "P0 means affected evidence needs correction or explicit bounds before reuse. P1 means decision-bearing follow-up is needed if pursued. P2 means conditional/descriptive/archive follow-up. These labels do not direct production changes and are not profitability verdicts.", "",
             "The plans preserve old preregistrations and decisions. They document what was tested well, exact defects or missing evidence, proposed notebook stages, relevant controls and stopping rules. They are retrospective addenda; any future run must freeze its exact configuration and decision criteria before seeing new results.", "",
             "## Complete study coverage", "",
             "Paths in the notebook column are relative to `studies/notebooks/`. Script-only studies receive a notebook-based follow-up plan where applicable.", "",
             "| Study / report family | Priority | Existing notebooks | Review and plan |",
             "|---|---|---|---|"]
    for entry in sorted(entries, key=lambda x: x["study"]):
        rel = Path(entry["review_path"])
        link = rel.relative_to(HERE.name).as_posix() if rel.parts[0] == HERE.name else "../" + rel.as_posix()
        notebooks = "<br>".join("`" + n + "`" for n in entry["notebooks_reviewed"]) or "Script/report study"
        lines.append(f"| {entry['study']} | {entry['priority']} | {notebooks} | [Review]({link}) |")
    lines += ["", "## Scope and verification limits", "",
              f"All {len(expected)} original notebooks have exactly one review mapping; all {len(original_dirs)} existing non-ORB directories have a plan. All {len(unchanged)} original notebook files, including ORB, match the pre-review byte hashes. No historical notebooks or study code were executed, no databases were queried, and no original source/results were edited for this audit.", "",
              f"The review inspected source, Markdown and relevant saved text/result artifacts. It did not render every embedded chart, independently reproduce numeric performance, verify an external snapshot, or run every code path. {zero_counts} original notebooks with code have no saved execution counts; accompanying script runs may still be documented. These limits prevent a blanket claim that testing was done correctly or that everything has been considered.", "",
              "## Rebuild this documentation", "",
              "From the repository root, run the repository Python on `studies/notebooks/study_validation_audit_2026_09/write_root_plans.py`, then `build_audit_index.py`. These documentation scripts do not execute a strategy, import study libraries or open databases. The original inventory is a review snapshot; do not regenerate it to conceal changes. A new review should version its inventory separately.", "",
              "Future empirical tests belong under each plan's stated `studies/notebooks/` path. The overview notebook contains Markdown only; it is a readable plan, not an executed backtest.", ""]
    readme = "\n".join(lines)
    (HERE / "README.md").write_text(readme, encoding="utf-8")
    # Split on second-level headings for a compact, reviewable notebook structure.
    cells = []
    for name, body in [("overview", readme), ("findings", (HERE / "REVIEW_FINDINGS.md").read_text(encoding="utf-8")),
                       ("protocol", (HERE / "VALIDATION_PROTOCOL.md").read_text(encoding="utf-8"))]:
        for i, section in enumerate(re.split(r"(?=^## )", body, flags=re.M)):
            if section.strip():
                cells.append({"cell_type": "markdown", "id": f"{name}-{i:02d}", "metadata": {},
                              "source": section.splitlines(keepends=True)})
    notebook = {"cells": cells, "metadata": {"language_info": {"name": "python"},
                 "audit": {"mode": "markdown_only_static_review", "date": "2026-09-14"}},
                "nbformat": 4, "nbformat_minor": 5}
    (HERE / "00_review_and_plan.ipynb").write_text(json.dumps(notebook, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    assert len({c["id"] for c in cells}) == len(cells)
    assert all(c["cell_type"] == "markdown" and isinstance(c["source"], list) for c in cells)
    checked_links = 0
    markdown_files = {BASE / x["review_path"] for x in entries} | set(HERE.glob("*.md"))
    for path in markdown_files:
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            target = unquote(target.split("#", 1)[0].strip("<>"))
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            assert (path.parent / target).exists(), f"Missing local link: {path}: {target}"
            checked_links += 1
    verification = {"original_notebooks_unchanged": len(unchanged), "reviewed_notebooks_mapped_once": len(coverage),
                    "original_directories_covered": len(reviewed_dirs), "review_plan_files_present": len(entries),
                    "overview_notebook_markdown_cells": len(cells), "empirical_studies_executed": 0,
                    "databases_queried": 0, "local_markdown_links_checked": checked_links, "counts": counts}
    (HERE / "documentation_checks.json").write_text(json.dumps(verification, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(verification))


if __name__ == "__main__":
    main()

"""Static notebook inventory only. Never imports or executes notebook code."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def inventory() -> dict:
    folder = Path(__file__).resolve().parent
    base = folder.parent
    rows = []
    for path in sorted(base.rglob("*.ipynb")):
        if folder in path.parents or ".ipynb_checkpoints" in path.parts:
            continue
        raw = path.read_bytes()
        nb = json.loads(raw.decode("utf-8-sig"))
        code = [(i, c) for i, c in enumerate(nb.get("cells", []))
                if c.get("cell_type") == "code"]
        sources = ["".join(c.get("source", [])) for c in nb.get("cells", [])]
        flags = []
        if any("__file__" in "".join(c.get("source", [])) for _, c in code):
            flags.append("uses___file___requires_notebook_entrypoint_review")
        if code and all(c.get("execution_count") is None for _, c in code):
            flags.append("no_saved_execution_counts_not_proof_of_no_external_run")
        if any("sqlite3.connect(" in s and "mode=ro" not in s for s in sources):
            flags.append("connection_cell_without_explicit_mode_ro_review_required")
        errors = [{"cell_index": i, "ename": o.get("ename", "unknown")}
                  for i, c in code for o in c.get("outputs", [])
                  if o.get("output_type") == "error"]
        rows.append({
            "notebook": path.relative_to(base).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "source_sha256": hashlib.sha256("\n".join(sources).encode("utf-8")).hexdigest(),
            "cells": len(nb.get("cells", [])),
            "code_cells": len(code),
            "code_cells_with_execution_count": sum(c.get("execution_count") is not None for _, c in code),
            "code_cells_with_saved_outputs": sum(bool(c.get("outputs")) for _, c in code),
            "saved_errors": errors,
            "static_review_flags": flags,
            "scope": "existing_plan_excluded" if "orb_study" in path.parts else "review",
        })
    directories = sorted(p.name for p in base.iterdir() if p.is_dir()
                         and p != folder and not p.name.startswith("."))
    return {
        "date": "2026-09-14",
        "method": "Static JSON/source inspection; no notebook, strategy, database or network execution",
        "cell_index_convention": "zero-based in original .ipynb",
        "directories": directories,
        "notebooks": rows,
    }


if __name__ == "__main__":
    result = inventory()
    out = Path(__file__).resolve().parent / "notebook_inventory.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    selected = [r for r in result["notebooks"] if r["scope"] == "review"]
    print(f"Inventoried {len(selected)} existing notebooks plus ORB plan; "
          f"{len(result['directories'])} existing directories")

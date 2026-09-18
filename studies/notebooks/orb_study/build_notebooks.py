#!/usr/bin/env python3
"""Assemble and execute the ORB review notebooks 01-05.

Run with the system Python (it has nbformat/nbclient; the repo venv does not). Cells execute in the
repo venv through a temporary kernelspec, from this directory, so every number is the venv's:

    C:/Python/Python313/python.exe studies/notebooks/orb_study/build_notebooks.py [01 02 ...]

The notebooks recompute from the frozen panels and compare with the saved, hashed results; they never
write a freeze or a result file.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import nbformat
from nbclient import NotebookClient

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"
sys.path.insert(0, str(HERE))
from notebook_cells import NOTEBOOKS  # noqa: E402


def register_temp_kernel() -> str:
    tmp = tempfile.mkdtemp(prefix="orb_kernel_")
    kd = Path(tmp) / "kernels" / "p300venv"
    kd.mkdir(parents=True)
    (kd / "kernel.json").write_text(json.dumps({
        "argv": [str(VENV_PY), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": "p300 venv", "language": "python"}), encoding="utf-8")
    prev = os.environ.get("JUPYTER_PATH")
    os.environ["JUPYTER_PATH"] = tmp + (os.pathsep + prev if prev else "")
    return "p300venv"


def build(name: str, cells: list[tuple[str, str]], kernel: str) -> Path:
    nb = nbformat.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": kernel, "display_name": "p300 venv", "language": "python"}
    nb.metadata["language_info"] = {"name": "python"}
    for kind, text in cells:
        text = text.strip("\n")
        nb.cells.append(nbformat.v4.new_markdown_cell(text) if kind == "md" else nbformat.v4.new_code_cell(text))
    NotebookClient(nb, timeout=3600, kernel_name=kernel, resources={"metadata": {"path": str(HERE)}}).execute()
    path = HERE / name
    nbformat.write(nb, path)
    return path


if __name__ == "__main__":
    wanted = sys.argv[1:]
    kernel = register_temp_kernel()
    for name, cells in NOTEBOOKS.items():
        if wanted and not any(name.startswith(w) for w in wanted):
            continue
        print("executing", name, flush=True)
        print("  wrote", build(name, cells, kernel), flush=True)

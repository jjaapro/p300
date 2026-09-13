"""Standalone pytest setup for the archived sleeves.

tests/conftest.py does not apply here — it only covers `tests/` — and
pytest.ini's `testpaths = tests` means nothing under this directory is
collected by a bare `pytest`. That is deliberate: the archive is unsupported,
so it must never be able to turn the main suite red.

Run on demand:  venv/Scripts/python.exe -m pytest studies/material/archive/tests
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

from strategies.support import clock  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_clock():
    """Every test starts in live mode and restores on exit — the same autouse
    fixture tests/conftest.py gives the main suite."""
    before = clock._simulated_now
    clock.set_simulated_now(None)
    yield
    clock.set_simulated_now(before)

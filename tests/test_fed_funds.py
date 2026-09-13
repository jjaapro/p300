"""data.sources.fed_funds — the Fed Funds target-rate phase classifier.

This lived in tests/test_fomc_service.py until 2026-09-13, which made it look
like sleeve coverage. It is not: the FOMC sleeve was archived that day and
`data/sources/fed_funds.py` stayed, because it is a live feed — `refresh_xml()`
is called by bootstrap and the running binance_feed loop, and monitor.py ages
the JSON it writes. Deleting the sleeve's test file wholesale would have taken
the only assertion that `classify_phase` reads that live artefact correctly
with it.

`classify_phase` has no other coverage anywhere in the suite.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def test_phase_classifier_known_dates():
    """Smoke test against the live fed_funds_target_upper.json. If this
    file isn't present the test skips rather than fails."""
    from data.sources import fed_funds as fed_funds_service

    json_path = (Path(__file__).resolve().parent.parent
                  / "data" / "archive" / "fed_funds_target_upper.json")
    if not json_path.exists():
        pytest.skip("fed_funds_target_upper.json not present")

    fed_funds_service.invalidate_cache()
    # 2024-07-31: rate held at 5.50% since Jul 2023 — peak_hold
    assert fed_funds_service.classify_phase("2024-07-31") == "peak_hold"
    # 2022-09-21: aggressive hike cycle
    assert fed_funds_service.classify_phase("2022-09-21") == "hiking"
    # 2025-12-10: r_90d_ago=4.5%, r_now=4.0% (after Sep+Oct cuts) -> cutting
    assert fed_funds_service.classify_phase("2025-12-10") == "cutting"
    # 2025-09-17: rate still 4.5% AT trade-decision time (cut effective T+1).
    # Phase is mid_hold even though the meeting itself produced a cut. This
    # reflected what the sleeve saw when deciding to enter at T-10h.
    assert fed_funds_service.classify_phase("2025-09-17") == "mid_hold"
    # 2026-04-29: rate stuck at 3.75% since Dec 2025 — mid_hold
    assert fed_funds_service.classify_phase("2026-04-29") == "mid_hold"

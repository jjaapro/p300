"""Sim mode must not hit the network.

Phase 7 added in-function ``if clock.is_simulated(): return ...`` guards
to every external-API refresh function. This test verifies the guards
are wired correctly by:

1. Setting a simulated clock.
2. Patching ``urllib.request.urlopen`` and ``socket.socket.connect`` to
   raise loudly if called.
3. Calling each refresh function.
4. Asserting it returns its no-op value AND no network primitive was
   invoked.

If a future change adds a new external-fetch path that bypasses the
guard, this test fails — surfacing the leak before it lands in a sim
run.
"""
from __future__ import annotations

import socket
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from services import clock


@pytest.fixture
def simulated_clock():
    """Pin the clock to a deterministic sim time inside the test."""
    clock.set_simulated_now(datetime(2024, 6, 3, 6, 0, 0, tzinfo=timezone.utc))
    yield
    clock.set_simulated_now(None)


@pytest.fixture
def network_tripwire():
    """Patch every primitive that could open a socket; any call fails the
    test loudly. Yields the patch objects so individual tests can
    inspect them if needed."""
    def _fail_urlopen(*args, **kwargs):
        raise AssertionError(
            f"urlopen called in sim mode: args={args}, kwargs={kwargs}"
        )

    def _fail_connect(self, *args, **kwargs):
        raise AssertionError(
            f"socket.connect called in sim mode: args={args}"
        )

    with patch("urllib.request.urlopen", side_effect=_fail_urlopen) as a, \
         patch.object(socket.socket, "connect", _fail_connect) as b:
        yield (a, b)


# ─── Per-service guards ────────────────────────────────────────────────────

def test_fed_funds_refresh_xml_is_no_op_in_sim(
    simulated_clock, network_tripwire,
):
    from services import fed_funds_service
    assert fed_funds_service.refresh_xml() is False


def test_polymarket_refresh_is_no_op_in_sim(
    simulated_clock, network_tripwire,
):
    from services import polymarket_service
    assert polymarket_service.refresh() is False


def test_sentiment_refresh_is_no_op_in_sim(
    simulated_clock, network_tripwire,
):
    from services import sentiment_index_service
    assert sentiment_index_service.refresh() is False


def test_news_refresh_is_no_op_in_sim(simulated_clock, network_tripwire):
    from services import news_fetcher
    # force=True bypasses the throttle but should still be blocked by
    # the sim guard, so this is the harder version of the test.
    assert news_fetcher.refresh(force=True) == 0


def test_coindesk_refresh_is_no_op_in_sim(simulated_clock, network_tripwire):
    from services import coindesk_fetcher
    assert coindesk_fetcher.refresh(force=True) == {}


# ─── Negative control: live mode actually attempts the call ────────────────

def test_guards_only_block_under_simulated_clock(network_tripwire):
    """Sanity check that the guards aren't blanket-disabling the
    refreshes. Without the simulated clock, refresh_xml's network call
    fires (hits our tripwire and raises) — which proves the no-op in
    sim mode comes from the is_simulated() check, not from some other
    path turning all fetchers off."""
    from services import fed_funds_service
    assert not clock.is_simulated()
    with pytest.raises(AssertionError, match="urlopen called"):
        fed_funds_service.refresh_xml()

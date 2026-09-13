"""chento_v3 must not read past its own clock. BACKLOG 7b, 2026-09-13.

Until that date chento's three feature loaders had no upper bound
(`WHERE timestamp >= ?` only). Harmless in live trading, where the clock tracks
the DB tail. Fatal to every frozen-clock run — the goldens, `--sim-now` runner
replays, parity re-cuts — which all read the database's future: 5,751 bars past
a 2026-07-15 clock. The committed goldens recorded okx_delta_z -0.73 for a bar
the live bot actually traded at +1.46.

Each arm here has a named mutation it is proven to catch, registered in
tests/fixtures/mutation_drill.py. They run against the hash-pinned chento
fixture, which carries rows to 2026-08-22 06:15 — so a clock of 2026-07-16 has
37 days of future data sitting in the file, ready to leak.

chento is also now covered by the look-ahead contract (BACKLOG step 6), which
could not be ported while these loaders were unbounded: the correct boundary
assertion would have been red.
"""
from __future__ import annotations

from tests import _golden_guard  # noqa: F401  — MUST be first (CHENTO_V3_DIAG)

import shutil
from datetime import datetime, timezone

import pandas as pd
import pytest

from strategies.support import clock
from tests._golden_fixture import fixture_db

#: 37 days before the fixture's tail — plenty of future to leak.
EARLY = datetime(2026, 7, 16, 6, 30, 5, tzinfo=timezone.utc)
#: A bar live traded (SJ-4245), with the forming 15m bar and a mid-hour clock.
FIRE_A = datetime(2026, 8, 21, 19, 30, 5, tzinfo=timezone.utc)


@pytest.fixture
def chento(tmp_path, monkeypatch):
    """The chento signal module pointed at a COPY of the fixture, with the
    ledger schema added. Never prod.db: decide() and the cooldown seeding read
    the trades table, and _golden_guard exists because a sleeve pointed at the
    live ledger can close real open positions."""
    from bots.chento_v3.strategy import config as ch_cfg
    if ch_cfg.ASSET != "BTC":
        pytest.skip(f"chento imported as {ch_cfg.ASSET}; these arms need BTC")
    dest = (tmp_path / "prod.db").resolve()
    shutil.copy(fixture_db("chento"), dest)

    from strategies.support import db as dbm, trade_db, variant_registry
    for attr in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(dbm, attr, dest)
    monkeypatch.setattr(trade_db, "DB_PATH", dest)
    trade_db.init_db()
    variant_registry.init_schema()
    _golden_guard.assert_fixture_db()

    from bots.chento_v3.strategy import signal as sg
    sg._cache_date = None
    sg._cached_features.clear() if isinstance(sg._cached_features, dict) else None
    sg._last_trigger_ts.clear()
    yield sg
    clock.set_simulated_now(None)
    sg._cache_date = None


def _ts(clk) -> pd.Timestamp:
    return pd.Timestamp(clk)


def test_chento_loaders_are_clock_bounded(chento):
    """The boundary half, on all three loaders, at a clock with 37 days of
    future rows in the file.

    The OKX bound is `now - 1h`, not `now`: a row stamped T is the bar
    [T, T+1h), OKX stores closed hours only, and `<= now` would pair a complete
    OKX hour with a truncated Binance hour. That artifact alone flips the OKX
    gate on ~36% of bars.

    Proven to catch: the upper bound deleted from any one of the three loaders.
    """
    sg = chento
    clock.set_simulated_now(EARLY)
    df15 = sg._load_15m_btc(EARLY, 90)
    lsr = sg._load_lsr_btc(EARLY, 90)
    okx = sg._load_okx_1h(EARLY, 30)

    assert len(df15) > 1000 and len(lsr) > 10 and len(okx) > 100, (
        "loaders returned too little — the arm would be vacuous")
    assert df15.index.max() <= _ts(EARLY), (
        f"_load_15m_btc: LOOK-AHEAD — newest bar {df15.index.max()} is after "
        f"the clock {EARLY}")
    assert lsr.index.max() <= _ts(EARLY), (
        f"_load_lsr_btc: LOOK-AHEAD — newest row {lsr.index.max()} is after "
        f"the clock {EARLY}")
    assert okx.index.max() <= _ts(EARLY) - pd.Timedelta(hours=1), (
        f"_load_okx_1h: newest bar {okx.index.max()} is not a CLOSED hour at "
        f"{EARLY} — the -3600 bound is missing")


def test_chento_feature_frame_ends_at_the_clock(chento):
    """Everything the decision reads is built here, so this is the bound that
    actually matters to a golden. Proven to catch: the same deletion."""
    sg = chento
    clock.set_simulated_now(EARLY)
    sg._rebuild_daily_cache(EARLY, force=True)
    df = sg._cached_features["df"]
    after = int((df.index > _ts(EARLY)).sum())
    assert after == 0, (
        f"chento feature frame: LOOK-AHEAD — {after} bars after the clock "
        f"{EARLY}; the frame runs to {df.index.max()}")


def test_chento_bound_keeps_the_forming_bar_and_the_last_closed_okx_hour(chento):
    """The other direction — the bound must not be TIGHTENED into a live
    behaviour change.

    At 19:30:05 the 15m bar that opened at 19:30:00 is the forming bar. It
    must be IN the frame: the replay entry path selects the bar whose open ==
    now, so `< floor(now, 15m)` kills replay entries outright. And the newest
    OKX row must be the 18:00 hour — not 19:00 (still forming at 19:30, which
    would mean the -3600 is gone) and not 17:00 (which would mean the bound was
    over-tightened and live would drop a row it has).

    Proven to catch: the 15m bound tightened by one bar, and the OKX bound
    widened back to `now`.
    """
    sg = chento
    clock.set_simulated_now(FIRE_A)
    df15 = sg._load_15m_btc(FIRE_A, 90)
    okx = sg._load_okx_1h(FIRE_A, 30)
    assert df15.index.max() == pd.Timestamp("2026-08-21 19:30:00", tz="UTC"), (
        f"forming 15m bar missing: newest is {df15.index.max()}")
    assert okx.index.max() == pd.Timestamp("2026-08-21 18:00:00", tz="UTC"), (
        f"newest OKX hour is {okx.index.max()}, expected the last CLOSED hour "
        f"18:00 at a 19:30 clock")


def test_chento_walking_replay_reaches_every_boundary(chento):
    """A replay walks the clock forward WITHOUT resetting module state, which
    is the case the goldens never exercise — each golden is one decide() on a
    cold cache.

    _rebuild_daily_cache is keyed by UTC date, so the day's first tick builds
    the frame and later ticks reuse it. With bounded loaders that frame ends
    at the first tick; without the replay-only stale-frame rebuild, every later
    boundary that day finds no bar and returns not_at_15m_boundary. Measured
    on a 121-tick walk: 2 decided became 0. The goldens stayed green through
    that, which is why this arm exists.

    Proven to catch: the stale-frame rebuild removed.
    """
    sg = chento
    variant = {"id": "walk"}
    walk = [datetime(2026, 8, 21, 19, 0, 5, tzinfo=timezone.utc),
            datetime(2026, 8, 21, 19, 15, 5, tzinfo=timezone.utc),
            FIRE_A]
    statuses = []
    for t in walk:
        clock.set_simulated_now(t)
        intents, st = sg._evaluate_trigger(t, variant, 100.0, 1.0, 100.0)
        statuses.append(st.get("status"))

    assert "not_at_15m_boundary" not in statuses, (
        f"walking replay stalled on a 15m boundary: {statuses} — the day's "
        f"first frame is being reused past its own end")
    assert statuses[-1] == "decided", (
        f"FIRE_A (a bar live traded, SJ-4245) did not decide when reached by "
        f"walking: {statuses}")

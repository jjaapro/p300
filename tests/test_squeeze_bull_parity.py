"""S-107 SQUEEZE_BULL — parity of the production sleeve against the research.

The repo's standing rule for ports: assert the production feature values match
the research at known timestamps, rather than trusting that two
implementations of the same formula agree.

The research ledger is
studies/notebooks/squeeze_bull_revalidation/results/full_oi_flush_ledger.csv,
produced by the frozen June code path. These tests drive
bots.squeeze_bull.strategy.math over the same prod.db frame and require:

  1. the kept-flush event set is exactly the ledger's fire set;
  2. the backward-only regime label matches the ledger's `regime_backonly`
     on every fire;
  3. the bracket and replay reproduce the ledger's r_outcome and exit_kind
     for every backward-only bull fire.

These are the fires the live bot would have taken, so a failure here means the
bot would trade a different book from the one that was validated.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bots.squeeze_bull.strategy import math as sb_math
from bots.squeeze_bull.strategy.config import COST_BP_RT, TIF_HOURS
from strategies.support import db

LEDGER = (Path(__file__).resolve().parents[1] / "studies" / "notebooks" /
          "squeeze_bull_revalidation" / "results" / "full_oi_flush_ledger.csv")
FIRST_TS = 1643500800  # 2022-01-30 00:00 UTC, the ledger's own start


def _load_frame():
    """The research loader's frame: price inner-joined to OI on timestamp."""
    con = sqlite3.connect(f"file:{db.PROD_DB}?mode=ro", uri=True)
    try:
        rows = con.execute("""
            SELECT p.timestamp, p.high, p.low, p.close, o.oi_close
            FROM cd_futures_ohlcv p
            JOIN cd_open_interest o ON o.timestamp = p.timestamp
            WHERE p.timestamp >= ?
            ORDER BY p.timestamp
        """, (FIRST_TS,)).fetchall()
    finally:
        con.close()
    rows = [r for r in rows if r[3] is not None and r[4] is not None]
    return ([int(r[0]) for r in rows], [r[1] for r in rows],
            [r[2] for r in rows], [r[3] for r in rows], [r[4] for r in rows])


def _ledger():
    pd = pytest.importorskip("pandas")
    if not LEDGER.is_file():
        pytest.skip("research ledger not on disk")
    d = pd.read_csv(LEDGER)
    # NOT .astype("int64") // 10**9: under pandas 3.0 these parse to
    # datetime64[us], so that yields microseconds and every timestamp comes
    # out 1000x too small. Cast the unit explicitly.
    d["ts_epoch"] = (pd.to_datetime(d.ts, utc=True).dt.tz_convert("UTC")
                     .dt.tz_localize(None).astype("datetime64[s]").astype("int64"))
    return d


@pytest.fixture(scope="module")
def frame():
    if not Path(db.PROD_DB).exists():
        pytest.skip("requires prod.db")
    return _load_frame()


def test_kept_flush_set_matches_research_fires(frame):
    """The trigger plus the 24-bar cooldown reproduce the ledger's fire set."""
    ts, _, _, closes, ois = frame
    d = _ledger()
    kept = sb_math.kept_flush_indices(ois, closes)
    ours = {ts[i] for i in kept}
    theirs = set(int(x) for x in d.ts_epoch)
    # The ledger stops at its own run time; compare only the overlap.
    end = max(theirs)
    ours = {t for t in ours if t <= end}
    assert ours == theirs, (
        f"fire set differs: only-production={sorted(ours - theirs)[:5]}, "
        f"only-research={sorted(theirs - ours)[:5]}")


def test_backward_only_regime_matches_research(frame):
    """`regime_backonly` in the ledger is reproduced bar for bar."""
    ts, _, _, closes, _ = frame
    d = _ledger()
    daily = {}
    for t, c in zip(ts, closes):
        daily[datetime.fromtimestamp(t, tz=timezone.utc).date()] = c
    by_ts = {int(r.ts_epoch): r for r in d.itertuples()}

    mismatches = []
    for t, row in by_ts.items():
        date = datetime.fromtimestamp(t, tz=timezone.utc).date()
        ours = sb_math.classify_regime(sb_math.backward_only_ret_30d(daily, date))
        if ours != row.regime_backonly:
            mismatches.append((datetime.fromtimestamp(t, tz=timezone.utc).isoformat(),
                               ours, row.regime_backonly))
    assert not mismatches, f"{len(mismatches)} regime mismatches, first: {mismatches[:3]}"


def test_replay_reproduces_research_outcomes_on_bull_fires(frame):
    """Every backward-only bull fire replays to the ledger's R and exit kind.

    This is the decision-bearing set: the fires the live bot would take.
    """
    ts, highs, lows, closes, ois = frame
    d = _ledger()
    idx_of = {t: i for i, t in enumerate(ts)}
    bulls = d[d.regime_backonly == "bull_30d"]
    assert len(bulls) >= 100, f"expected the full-sample bull set, got {len(bulls)}"

    checked, bad = 0, []
    for row in bulls.itertuples():
        i = idx_of.get(int(row.ts_epoch))
        if i is None:
            continue
        rep = sb_math.replay_bracket(highs, lows, closes, entry_idx=i,
                                     tif_bars=TIF_HOURS, cost_bp=COST_BP_RT)
        if rep is None:
            bad.append((row.ts, "no replay"))
            continue
        checked += 1
        if abs(rep["r_outcome"] - row.r_outcome) > 1e-9 or rep["exit_kind"] != row.exit_kind:
            bad.append((row.ts, rep["r_outcome"], row.r_outcome,
                        rep["exit_kind"], row.exit_kind))
    assert checked >= 100, f"only replayed {checked} fires"
    assert not bad, f"{len(bad)} outcome mismatches, first: {bad[:3]}"


def test_bracket_is_2pct_stop_3pct_target():
    stop, target, risk = sb_math.bracket(100.0)
    assert stop == pytest.approx(98.0)
    assert target == pytest.approx(103.0)
    assert risk == pytest.approx(2.0)
    # 1R is the 2% stop distance, so the target is 1.5R gross.
    assert (target - 100.0) / risk == pytest.approx(1.5)


def test_regime_shift_is_what_makes_the_gate_causal():
    """A shift of 1 day must read a strictly earlier close. This is the whole
    reason the production gate differs from the June one."""
    from datetime import date, timedelta
    from bots.squeeze_bull.strategy import config as cfg
    assert cfg.REGIME_SHIFT_DAYS == 1, "the causal guarantee was changed"

    d0 = date(2026, 3, 1)
    daily = {d0 - timedelta(days=k): 100.0 for k in range(0, 40)}
    daily[d0] = 999_999.0          # today's close: must NOT be readable
    daily[d0 - timedelta(days=1)] = 130.0
    daily[d0 - timedelta(days=31)] = 100.0
    r = sb_math.backward_only_ret_30d(daily, d0)
    assert r == pytest.approx(0.30), "gate read the wrong day"
    assert sb_math.classify_regime(r) == "bull_30d"


def test_nan_regime_is_flat_never_bull():
    """A missing 30-day return must never open a trade."""
    assert sb_math.classify_regime(None) == "flat_30d"
    assert sb_math.backward_only_ret_30d({}, datetime(2026, 1, 1).date()) is None

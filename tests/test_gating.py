"""P2.4b — gating framework contract + R4 vol-gate parity.

Anchors:
  1. The GateDecision dataclass is frozen, defaults are pass-through.
  2. Unregistered strategy_ids get the default (fire=True, mult=1.0).
  3. Registered R4 sleeves return leverage_mult ∈ {0.4, 1.0} keyed by
     today_inputs()["gated"] — same value the legacy
     R4_INNER_LEV_GATED/_UNGATED branch computes.
  4. Orchestrator's _tick_composition injects an _effective_gate into
     every dispatched sleeve_cfg (smoke-tested via the resolver call
     chain; we don't ride the live tick here).
"""
from __future__ import annotations

import pytest

from strategies.support import gating


def test_gate_decision_defaults_are_pass_through():
    d = gating.GateDecision()
    assert d.fire is True
    assert d.leverage_mult == 1.0
    assert d.reason == ""
    assert d.metadata is None


def test_gate_decision_frozen():
    d = gating.GateDecision()
    with pytest.raises(Exception):  # FrozenInstanceError
        d.fire = False  # type: ignore[misc]


def test_default_decision_constant_is_pass_through():
    assert gating.DEFAULT_DECISION.fire is True
    assert gating.DEFAULT_DECISION.leverage_mult == 1.0


@pytest.mark.parametrize("sid", [
    "S-003", "S-078", "S-096", "PDO-L-RF", "CPR", "FOMC", "AI_QUANT",
    "JPLUS_EMA_BTC", "JPLUS_ETH_DAILY",  # no gates registered yet
    "NOT_A_SLEEVE",
])
def test_unregistered_sleeve_returns_default(sid):
    out = gating.get_decision(sid, "strong_bull", None)
    assert out is gating.DEFAULT_DECISION


@pytest.mark.parametrize("sid", [
    "JPLUS_R4_BTC", "JPLUS_R4_ETH", "JPLUS_R4_BTC_V2", "JPLUS_R4_ETH_V2",
])
def test_r4_sleeves_have_a_gate_registered(sid):
    assert sid in gating.GATE_REGISTRY


@pytest.mark.parametrize("sid", [
    "JPLUS_R4_BTC", "JPLUS_R4_ETH", "JPLUS_R4_BTC_V2", "JPLUS_R4_ETH_V2",
])
def test_r4_gate_matches_today_inputs_gated(monkeypatch, sid):
    """The gate's leverage_mult must equal R4_INNER_LEV_GATED /
    R4_INNER_LEV_UNGATED (=0.4) when ti['gated'] is True, and 1.0
    when False. Same numbers the legacy branch produces."""
    from strategies.support import jplus_inputs as ji

    fake = {"gated": True, "mode": "strong_bull"}
    monkeypatch.setattr(ji, "today_inputs", lambda: fake)
    d = gating.get_decision(sid, "strong_bull", None)
    assert d.fire is True
    assert d.leverage_mult == pytest.approx(0.4)
    assert d.reason == "r4_vol_gated"

    fake["gated"] = False
    d = gating.get_decision(sid, "strong_bull", None)
    assert d.leverage_mult == pytest.approx(1.0)
    assert d.reason == "r4_vol_ungated"


def test_r4_gate_cold_boot_returns_default(monkeypatch):
    """When today_inputs() is None (insufficient warmup), the R4 gate
    returns the pass-through default so the sleeve's own no_inputs
    short-circuit takes over."""
    from strategies.support import jplus_inputs as ji
    monkeypatch.setattr(ji, "today_inputs", lambda: None)
    d = gating.get_decision("JPLUS_R4_BTC", None, None)
    assert d is gating.DEFAULT_DECISION


def test_r4_inner_lev_equivalence(monkeypatch):
    """Verify the leverage_mult arithmetic against the legacy constants:

      inner_lev (legacy)        == inner_lev (gate-based)
      R4_INNER_LEV_GATED        == R4_INNER_LEV_UNGATED * 0.4
      R4_INNER_LEV_UNGATED      == R4_INNER_LEV_UNGATED * 1.0
    """
    from strategies.sleeves.r4.signal import R4_INNER_LEV_GATED, R4_INNER_LEV_UNGATED
    assert R4_INNER_LEV_UNGATED * 0.4 == pytest.approx(R4_INNER_LEV_GATED)
    assert R4_INNER_LEV_UNGATED * 1.0 == pytest.approx(R4_INNER_LEV_UNGATED)


# ─── THU_BEAR V4 event filter ────────────────────────────────────────────────

def test_v4_gate_registered_for_s096():
    assert "S-096" in gating.GATE_REGISTRY


def test_v4_gate_returns_default_on_non_thursday():
    """V4 only matters on Thursdays. Per-tick orchestrator calls on
    Mon-Wed/Fri-Sun must be cheap no-ops returning DEFAULT_DECISION."""
    from datetime import datetime, timezone
    # 2026-05-13 is Wednesday
    not_thursday = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)
    d = gating.get_decision("S-096", "strong_bull", not_thursday)
    assert d is gating.DEFAULT_DECISION


def test_v4_gate_returns_default_when_now_utc_missing():
    d = gating.get_decision("S-096", "strong_bull", None)
    assert d is gating.DEFAULT_DECISION


def test_v4_gate_passes_when_event_window_includes_today(monkeypatch):
    """Thursday + today in include set + not in exclude set -> fire=True."""
    from datetime import datetime, timezone
    from strategies.sleeves.thu_bear import signal as thu_bear
    monkeypatch.setattr(thu_bear, "_event_cache", {
        "include": {"2026-05-14"},
        "exclude": set(),
    })
    # 2026-05-14 is Thursday
    thursday = datetime(2026, 5, 14, 0, 5, tzinfo=timezone.utc)
    d = gating.get_decision("S-096", "strong_bull", thursday)
    assert d.fire is True
    assert d.reason == "v4_event_adjacent"
    assert d.metadata == {"today_iso": "2026-05-14"}


def test_v4_gate_blocks_when_opex_adjacent(monkeypatch):
    """Thursday + today in exclude (OPEX-adjacent) -> fire=False."""
    from datetime import datetime, timezone
    from strategies.sleeves.thu_bear import signal as thu_bear
    monkeypatch.setattr(thu_bear, "_event_cache", {
        "include": {"2026-05-14"},
        "exclude": {"2026-05-14"},
    })
    thursday = datetime(2026, 5, 14, 0, 5, tzinfo=timezone.utc)
    d = gating.get_decision("S-096", "strong_bull", thursday)
    assert d.fire is False
    assert d.reason == "v4_opex_adjacent"


def test_v4_gate_blocks_when_not_event_adjacent(monkeypatch):
    """Thursday + today not in include -> fire=False (no_cpi_nfp)."""
    from datetime import datetime, timezone
    from strategies.sleeves.thu_bear import signal as thu_bear
    monkeypatch.setattr(thu_bear, "_event_cache", {
        "include": {"2026-05-21"},  # next Thursday, not this one
        "exclude": set(),
    })
    thursday = datetime(2026, 5, 14, 0, 5, tzinfo=timezone.utc)
    d = gating.get_decision("S-096", "strong_bull", thursday)
    assert d.fire is False
    assert d.reason == "v4_no_cpi_nfp_adjacency"


def test_v4_gate_fails_closed_when_calendar_missing(monkeypatch):
    """Empty include set (calendar unavailable) -> fire=False, never silently
    degrades to V3 unconditional shorts."""
    from datetime import datetime, timezone
    from strategies.sleeves.thu_bear import signal as thu_bear
    monkeypatch.setattr(thu_bear, "_event_cache", {
        "include": set(),
        "exclude": set(),
    })
    thursday = datetime(2026, 5, 14, 0, 5, tzinfo=timezone.utc)
    d = gating.get_decision("S-096", "strong_bull", thursday)
    assert d.fire is False
    assert d.reason == "v4_event_calendar_unavailable_fail_closed"


# ─── FOMC composite filter ───────────────────────────────────────────────────

def test_fomc_gate_registered():
    assert "FOMC" in gating.GATE_REGISTRY


def test_fomc_gate_returns_default_when_no_fomc_due(monkeypatch):
    """If next_fomc_date returns None (no FOMC within 2 days), gate is a
    no-op. Most ticks take this path."""
    from datetime import datetime, timezone
    import strategies.sleeves.fomc.signal as fomc_signal
    monkeypatch.setattr(fomc_signal, "next_fomc_date", lambda now, lookahead_days=2: None)
    now = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
    assert gating.get_decision("FOMC", "uncertain", now) is gating.DEFAULT_DECISION


def test_fomc_gate_returns_default_when_now_utc_missing():
    assert gating.get_decision("FOMC", "uncertain", None) is gating.DEFAULT_DECISION


def test_fomc_gate_returns_default_when_no_cached_eval(monkeypatch, tmp_path):
    """FOMC is due but Phase 1 hasn't pre-decided yet — observer row is
    missing. Gate must not block; the sleeve will run Phase 1 and
    populate the row, after which subsequent ticks see a non-default
    decision."""
    from datetime import datetime, timezone
    import sqlite3
    import strategies.sleeves.fomc.signal as fomc_signal
    monkeypatch.setattr(fomc_signal, "next_fomc_date", lambda now, lookahead_days=2: "2026-06-18")
    db_path = tmp_path / "prod.db"
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE fomc_observer ("
                 "  fomc_date TEXT PRIMARY KEY, decision TEXT, reason TEXT, "
                 "  phase TEXT, fear_greed_bucket TEXT, expected_action TEXT)")
    con.commit(); con.close()
    monkeypatch.setattr("strategies.support.db.TRADER_DB", db_path)
    monkeypatch.setattr("strategies.support.db.PROD_DB", db_path)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    assert gating.get_decision("FOMC", "uncertain", now) is gating.DEFAULT_DECISION


def test_fomc_gate_fires_when_cached_decision_is_trade(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    import sqlite3
    import strategies.sleeves.fomc.signal as fomc_signal
    monkeypatch.setattr(fomc_signal, "next_fomc_date", lambda now, lookahead_days=2: "2026-06-18")
    db_path = tmp_path / "prod.db"
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE fomc_observer ("
                 "  fomc_date TEXT PRIMARY KEY, decision TEXT, reason TEXT, "
                 "  phase TEXT, fear_greed_bucket TEXT, expected_action TEXT)")
    con.execute(
        "INSERT INTO fomc_observer VALUES (?, ?, ?, ?, ?, ?)",
        ("2026-06-18", "trade", "phase=peak_hold; fg=neutral",
         "peak_hold", "neutral", "hold"),
    )
    con.commit(); con.close()
    monkeypatch.setattr("strategies.support.db.TRADER_DB", db_path)
    monkeypatch.setattr("strategies.support.db.PROD_DB", db_path)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    d = gating.get_decision("FOMC", "uncertain", now)
    assert d.fire is True
    assert "phase=peak_hold" in d.reason
    assert d.metadata["fomc_date"] == "2026-06-18"
    assert d.metadata["phase"] == "peak_hold"


def test_fomc_gate_blocks_when_cached_decision_is_skip(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    import sqlite3
    import strategies.sleeves.fomc.signal as fomc_signal
    monkeypatch.setattr(fomc_signal, "next_fomc_date", lambda now, lookahead_days=2: "2026-06-18")
    db_path = tmp_path / "prod.db"
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE fomc_observer ("
                 "  fomc_date TEXT PRIMARY KEY, decision TEXT, reason TEXT, "
                 "  phase TEXT, fear_greed_bucket TEXT, expected_action TEXT)")
    con.execute(
        "INSERT INTO fomc_observer VALUES (?, ?, ?, ?, ?, ?)",
        ("2026-06-18", "skip", "phase=mid_hold (25% win rate)",
         "mid_hold", "neutral", "hold"),
    )
    con.commit(); con.close()
    monkeypatch.setattr("strategies.support.db.TRADER_DB", db_path)
    monkeypatch.setattr("strategies.support.db.PROD_DB", db_path)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    d = gating.get_decision("FOMC", "uncertain", now)
    assert d.fire is False
    assert "mid_hold" in d.reason


def test_fomc_gate_handles_missing_table(monkeypatch, tmp_path):
    """A bot booting on a fresh DB before fomc_observer is created sees
    sqlite3.OperationalError. Gate must swallow it and return DEFAULT."""
    from datetime import datetime, timezone
    import strategies.sleeves.fomc.signal as fomc_signal
    monkeypatch.setattr(fomc_signal, "next_fomc_date", lambda now, lookahead_days=2: "2026-06-18")
    db_path = tmp_path / "prod.db"
    # Empty DB — no fomc_observer table.
    import sqlite3
    sqlite3.connect(str(db_path)).close()
    monkeypatch.setattr("strategies.support.db.TRADER_DB", db_path)
    monkeypatch.setattr("strategies.support.db.PROD_DB", db_path)
    now = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    assert gating.get_decision("FOMC", "uncertain", now) is gating.DEFAULT_DECISION


# ─── Orchestrator injection ──────────────────────────────────────────────────

def test_orchestrator_injects_effective_gate(monkeypatch):
    """_tick_composition runs gating.get_decision per sleeve and stores
    it on sleeve_cfg before dispatch. Smoke test via direct call to
    get_decision (the live tick path is exercised by integration tests)."""
    from strategies.support import jplus_inputs as ji
    monkeypatch.setattr(ji, "today_inputs", lambda: {"gated": True, "mode": "uncertain"})

    d = gating.get_decision("JPLUS_R4_BTC", "uncertain", None)
    assert isinstance(d, gating.GateDecision)
    assert d.fire is True
    assert d.leverage_mult == pytest.approx(0.4)

    # Composition path (manually mimicking _tick_composition):
    sleeve_cfg = {"strategy_id": "JPLUS_R4_BTC"}
    sleeve_cfg["_effective_gate"] = gating.get_decision(
        sleeve_cfg["strategy_id"], "uncertain", None)
    assert sleeve_cfg["_effective_gate"].leverage_mult == pytest.approx(0.4)

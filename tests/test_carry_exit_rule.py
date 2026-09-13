"""CARRY (S-078) exit rule — the trailing 30-day cumulative-funding exit that
replaced the three-negative-day streak on 2026-09-12.

Synthetic checks on the sleeve's daily evaluator and on the decide path, and
two parity tests that re-derive the pre-registered study rule
(studies/notebooks/carry_exit_rule_2026_09/run_p2_exit_rules.py,
`rule_states(kind="CUM30D")`: entry when the 7-day mean is positive, exit when
the 30-day rolling sum is below -0.5 %) over the production funding table and
require the sleeve's per-day signals to match it on every day.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bots.carry.strategy import config as cfg
from bots.carry.strategy import signal as carry

CFG = {"weight_pct": 100.0, "_effective_leverage": 1.0, "priority": 100}
#: What CFG translated to before the orchestrator was retired. Inlined rather
#: than computed, because nothing in production translates a cfg dict now.
_CFG_KW = {"weight_pct": 100.0, "leverage": 1.0, "priority": 100.0}


def _records(values, start="2026-01-01"):
    d0 = datetime.fromisoformat(start)
    return [{"date": (d0 + timedelta(days=i)).strftime("%Y-%m-%d"),
             "daily_funding_pct": float(v),
             "spot_close": 100.0, "perp_close": 100.1}
            for i, v in enumerate(values)]


# ─── the rule itself ─────────────────────────────────────────────────────────

def test_constants_are_the_study_rule():
    assert cfg.EXIT_CUM_DAYS == 30
    assert cfg.EXIT_CUM_THRESHOLD_PCT == -0.5
    assert not hasattr(cfg, "EXIT_NEG_DAYS"), "the streak exit is gone, not parked"


def test_exit_needs_a_full_window():
    """29 days summing to -0.9 %: no complete window, so no exit yet."""
    sig = carry._evaluate_today(_records([-0.031] * 29))
    assert sig["cum_funding_pct"] is None
    assert sig["exit_trigger"] is False


def test_exit_triggers_below_threshold_over_thirty_days():
    sig = carry._evaluate_today(_records([0.03] * 10 + [-0.02] * 30))
    assert sig["cum_funding_pct"] == pytest.approx(-0.6)
    assert sig["exit_trigger"] is True
    assert sig["entry_ok"] is False


def test_window_is_the_last_thirty_days_only():
    """A bad month that has rolled off the window no longer counts."""
    sig = carry._evaluate_today(_records([-0.05] * 30 + [0.01] * 30))
    assert sig["cum_funding_pct"] == pytest.approx(0.3)
    assert sig["exit_trigger"] is False
    assert sig["entry_ok"] is True


def test_three_negative_days_alone_do_not_exit():
    """The retired streak rule would have exited here; the cumulative rule
    needs -0.5 % over the month, and the month is still +0.51 %."""
    sig = carry._evaluate_today(_records([0.02] * 27 + [-0.01] * 3))
    assert sig["cum_funding_pct"] == pytest.approx(0.51)
    assert sig["exit_trigger"] is False


def test_threshold_is_strict():
    sig = carry._evaluate_today(_records([0.0] * 29 + [-0.5]))
    assert sig["cum_funding_pct"] == pytest.approx(-0.5)
    assert sig["exit_trigger"] is False


# ─── decide path ─────────────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path, monkeypatch):
    import botlib
    from strategies.support import clock
    from strategies.support import db as _db_mod
    from strategies.support import trade_db, variant_registry
    db_path = (tmp_path / "prod.db").resolve()
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, db_path)
    monkeypatch.setattr(trade_db, "DB_PATH", db_path)
    trade_db.init_db()
    variant_registry.init_schema()
    clock.set_simulated_now(datetime(2026, 3, 10, 0, 5, tzinfo=timezone.utc))
    variant = botlib.ensure_bot_variant(
        "bot_carry_v1", short_name="t", capital_usdt=10_000.0, bot_name="carry")
    yield variant
    clock.set_simulated_now(None)


def _open(variant):
    from strategies.support.dispatch import Intent
    intent = Intent(asset="BTC", direction="LONG", allocation_pct=100.0,
                    leverage=1.0, conviction=100, priority=100,
                    reason={"trigger": "t", "_entry_price": 100.0,
                            "_fr_7d_avg_pct": 0.03},
                    scheduled_exit_dt=None)
    return carry.execute(variant, intent)["trade_id"]


def test_decide_closes_an_open_trade_when_the_month_breaks(env, monkeypatch):
    seen = {}

    def fake_load(days=30):
        seen["days"] = days
        return _records([0.03] * 10 + [-0.02] * 30, start="2026-01-29")
    monkeypatch.setattr(carry, "_load_recent_daily_funding", fake_load)
    _open(env)
    assert len(carry._get_open_carry_trades(env["id"])) == 1

    intents, status = carry.decide(env, **_CFG_KW)
    assert seen["days"] >= cfg.EXIT_CUM_DAYS, "the loader window must cover the exit window"
    assert status["status"] == "closed" and not intents
    assert carry._get_open_carry_trades(env["id"]) == []


def test_decide_does_not_enter_while_the_exit_is_active(env, monkeypatch):
    """The corner the study's state machine handles differently: 7-day mean
    positive but the month still below -0.5 %. The sleeve waits (the study
    would enter and exit again the next day, paying two toggles)."""
    monkeypatch.setattr(carry, "_load_recent_daily_funding",
                        lambda days=30: _records([-0.05] * 23 + [0.02] * 7,
                                                 start="2026-02-08"))
    intents, status = carry.decide(env, **_CFG_KW)
    assert not intents
    assert status["status"] == "no_action"
    assert status["cum_funding_pct"] == pytest.approx(-1.01)
    assert status["fr_7d_avg_pct"] == pytest.approx(0.02)


def test_decide_enters_when_the_mean_is_positive_and_no_exit(env, monkeypatch):
    monkeypatch.setattr(carry, "_load_recent_daily_funding",
                        lambda days=30: _records([0.01] * 37, start="2026-02-01"))
    intents, status = carry.decide(env, **_CFG_KW)
    assert len(intents) == 1 and status["status"] == "decided"


# ─── parity with the pre-registered study rule on the production table ─────

STUDY_MIN_DAYS = 2000


@pytest.fixture(scope="module")
def daily_series():
    from strategies.support import db, funding
    if not Path(db.PROD_DB).exists():
        pytest.skip("requires prod.db")
    now_ts = int(datetime.now(timezone.utc).timestamp())
    sums = funding.daily_sums_pct("BTC", 0, now_ts, complete_only=True)
    if len(sums) < STUDY_MIN_DAYS:
        pytest.skip("funding history too short for the parity check")
    dates = sorted(sums)
    return dates, [sums[d] for d in dates]


def _study_signals(dates, vals):
    pd = pytest.importorskip("pandas")
    s = pd.Series(vals, index=dates, dtype=float)
    avg7 = s.rolling(cfg.FR_WINDOW_DAYS).mean()
    cum = s.rolling(cfg.EXIT_CUM_DAYS).sum()
    return avg7, cum


def _sleeve_window(records, i):
    """What decide() hands the evaluator on day i."""
    window = cfg.EXIT_CUM_DAYS + 7
    return records[max(0, i - window + 1): i + 1]


def test_sleeve_signals_match_the_study_rule_every_day(daily_series):
    pd = pytest.importorskip("pandas")
    dates, vals = daily_series
    avg7, cum = _study_signals(dates, vals)
    records = [{"date": d, "daily_funding_pct": v, "spot_close": 1.0, "perp_close": 1.0}
               for d, v in zip(dates, vals)]
    checked = 0
    for i in range(cfg.FR_WINDOW_DAYS, len(records)):
        sig = carry._evaluate_today(_sleeve_window(records, i))
        assert sig is not None, dates[i]
        a, c = avg7.iloc[i], cum.iloc[i]
        if pd.notna(a) and abs(a - cfg.FR_ENTRY_THRESHOLD) > 1e-12:
            assert sig["entry_ok"] == bool(a > cfg.FR_ENTRY_THRESHOLD), dates[i]
        study_exit = bool(pd.notna(c) and c < cfg.EXIT_CUM_THRESHOLD_PCT)
        if pd.notna(c) and abs(c - cfg.EXIT_CUM_THRESHOLD_PCT) < 1e-9:
            continue                        # float tie at the threshold
        assert sig["exit_trigger"] == study_exit, dates[i]
        if pd.notna(c):
            assert sig["cum_funding_pct"] == pytest.approx(c, abs=1e-9), dates[i]
        checked += 1
    assert checked > STUDY_MIN_DAYS - 100


def test_hold_states_differ_from_the_study_only_in_the_entry_guard_corner(daily_series):
    """The study's state machine enters whenever the 7-day mean is positive,
    even on a day the cumulative exit is active, and exits again the next
    day; the sleeve does not enter while the exit is active (the guard the
    streak exit already had). Every divergence between the two hold
    sequences must therefore start on such a corner day, and the sleeve must
    still be in the market almost all of the time, as the study's CUM-30D
    row was (99.6 % of the scored window)."""
    dates, vals = daily_series
    records = [{"date": d, "daily_funding_pct": v, "spot_close": 1.0, "perp_close": 1.0}
               for d, v in zip(dates, vals)]
    study, sleeve, corner = [], [], []
    hs = hv = False
    for i in range(len(records)):
        sig = carry._evaluate_today(_sleeve_window(records, i))
        entry = bool(sig and sig["entry_ok"])
        exit_ = bool(sig and sig["exit_trigger"])
        if hs and exit_:
            hs = False
        elif not hs and entry:
            hs = True
        if hv and exit_:
            hv = False
        elif not hv and entry and not exit_:
            hv = True
        study.append(hs)
        sleeve.append(hv)
        corner.append(entry and exit_)
    diff = [i for i in range(len(records)) if study[i] != sleeve[i]]
    for i in diff:
        j = i
        while j > 0 and study[j - 1] != sleeve[j - 1]:
            j -= 1
        assert corner[j], (dates[j], "a divergence must start on a corner day")
    share = sum(sleeve) / len(sleeve)
    assert share >= 0.98, f"sleeve held {share:.1%} of days"
    assert len(diff) <= 0.02 * len(records), f"{len(diff)} differing days"

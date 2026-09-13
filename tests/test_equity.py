"""Risk reconstruction regressions: observed path, timing, and accounting."""
import sqlite3
from datetime import datetime, timezone

import pytest

from strategies.support import db, equity, trade_db, strategy_health


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "equity.db"
    monkeypatch.setattr(db, "DASH_DB", path)
    monkeypatch.setattr(db, "TRADER_DB", path)
    monkeypatch.setattr(trade_db, "DB_PATH", path)
    trade_db.init_db()
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE btc_1m (open_time INTEGER PRIMARY KEY, close REAL)")
        con.execute("CREATE TABLE cd_funding_rate (timestamp INTEGER PRIMARY KEY, fr_close REAL)")
    return path


def trade(path, *, tid="T", entry="2024-01-01T10:00:00+00:00", exit="2024-01-04T12:00:00+00:00",
          pnl=12, qty=1, strategy="PDO_RETOUCH", direction="LONG"):
    with sqlite3.connect(path) as con:
        con.execute("INSERT INTO trades (id,series,asset,direction,strategy,strategy_variant,"
            "actual_entry_time,actual_exit_time,status,entry_price,qty,size_usdt,pnl_usdt) "
            "VALUES (?,'SJ','BTC',?,?,'V',?,?,?,100,?,?,?)",
            (tid,direction,strategy,entry,exit,"closed" if exit else "open",qty,qty*100,pnl))


def mark(path, day, price):
    ts = int(datetime.fromisoformat(f"2024-01-{day:02}T23:59:00+00:00").timestamp()*1000)
    with sqlite3.connect(path) as con:
        con.execute("INSERT INTO btc_1m VALUES (?,?)",(ts,price))
        # Feed finalizes this row together with its successor.
        con.execute("INSERT OR IGNORE INTO btc_1m VALUES (?,?)",(ts+60000,price))


def event(path, seq, kind, day, qty, price, size, realized=0, fee=0, tid="T"):
    with sqlite3.connect(path) as con:
        con.execute("INSERT INTO trade_adjustments (trade_id,seq,event_type,event_time,event_date,"
            "qty_after,price,size_usdt_after,realized_pnl_delta_usdt,fee_usdt) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tid,seq,kind,f"2024-01-{day:02}T12:00:00+00:00",f"2024-01-{day:02}",qty,price,size,realized,fee))


def test_recovered_winner_keeps_daily_drawdown_and_conserves_terminal_pnl(ledger):
    trade(ledger)
    for day, price in [(1,80),(2,110),(3,112)]:
        mark(ledger,day,price)
    rows = equity.daily_equity("V","2024-01-01","2024-01-04",100)
    assert [r["daily_pnl"] for r in rows] == pytest.approx([-20,30,2,0])
    assert rows[-1]["equity_usdt"] == pytest.approx(112)
    metrics = strategy_health.portfolio_metrics("V", strategy_health.Window("ALL","2024-01-01","2024-01-04"), capital_usdt=100)
    assert metrics.max_drawdown_pct == pytest.approx(-20)
    assert metrics.total_return_pct == pytest.approx(12)
    assert strategy_health.trades_daily_returns("V","2024-01-01","2024-01-04",100) == [("2024-01-04",12)]
    nav = equity.build_daily_nav("V",100,datetime(2024,1,1,tzinfo=timezone.utc),datetime(2024,1,4,12,tzinfo=timezone.utc))
    assert equity.compute_metrics(nav,100)["mdd_pct"] == pytest.approx(-20)


def test_future_terminal_result_cannot_change_earlier_equity(ledger):
    trade(ledger)
    for day, price in [(1,80),(2,110),(3,112)]:
        mark(ledger,day,price)
    earlier = equity.daily_equity("V","2024-01-01","2024-01-03",100)
    with sqlite3.connect(ledger) as con:
        con.execute("UPDATE trades SET pnl_usdt=999,exit_price=1099")
    assert equity.daily_equity("V","2024-01-01","2024-01-03",100) == earlier


def test_open_short_and_before_window_position_use_previous_mark(ledger):
    trade(ledger,exit=None,pnl=None,direction="SHORT")
    mark(ledger,1,80)
    mark(ledger,2,90)
    row = equity.daily_equity("V","2024-01-02","2024-01-02",100)[0]
    assert row["opening_equity_usdt"] == pytest.approx(120)
    assert row["equity_usdt"] == pytest.approx(110)
    assert row["daily_pnl"] == pytest.approx(-10)
    assert row["nav_return_pct"] == pytest.approx(-100/12)


def test_scaling_replays_quantity_basis_and_each_fee_once(ledger):
    trade(ledger,qty=10,pnl=193)
    event(ledger,0,"OPEN",1,10,100,1000)
    event(ledger,1,"SCALE_UP",2,20,120,2400,fee=2)
    event(ledger,2,"SCALE_DOWN",3,10,130,1300,realized=197,fee=3)
    event(ledger,3,"CLOSE",4,0,110,0,realized=-4,fee=4)
    for day, price in [(1,110),(2,115),(3,125)]:
        mark(ledger,day,price)
    rows = equity.daily_equity("V","2024-01-01","2024-01-04",1000)
    assert [r["equity_usdt"] for r in rows] == pytest.approx([1100,1098,1345,1191])
    assert sum(r["daily_pnl"] for r in rows) == pytest.approx(191)


def test_funding_accrues_on_settlement_and_close_net_is_not_double_counted(ledger):
    trade(ledger,entry="2024-01-01T01:00:00+00:00",exit="2024-01-02T12:00:00+00:00",pnl=-1.4,strategy="ADX")
    mark(ledger,1,100)
    with sqlite3.connect(ledger) as con:
        for day,hour in [(1,8),(1,16),(2,0),(2,8)]:
            ts=int(datetime(2024,1,day,hour,tzinfo=timezone.utc).timestamp())
            con.execute("INSERT INTO cd_funding_rate VALUES (?,.001)",(ts,))
    rows=equity.daily_equity("V","2024-01-01","2024-01-02",100)
    assert [r["daily_pnl"] for r in rows] == pytest.approx([-.2,-1.2])
    assert rows[-1]["equity_usdt"] == pytest.approx(98.6)


def test_missing_mark_is_explicit_and_vol_sizing_uses_floor(ledger, monkeypatch, caplog):
    trade(ledger,exit=None,pnl=None)
    # A next-day observation may never be substituted for a missing mark.
    mark(ledger,2,999)
    with pytest.raises(equity.EquityDataError,match="Missing completed BTC mark"):
        equity.daily_equity("V","2024-01-01","2024-01-01",100)
    from strategies.support import clock
    monkeypatch.setattr(clock,"now_utc",lambda:datetime(2024,1,2,12,tzinfo=timezone.utc))
    # (The portfolio-vol scalar assertion went with strategies/support/
    # portfolio_vol.py, retired 2026-09-13 with the orchestrator that was its
    # only consumer — no bot ever read it; every runner passes leverage
    # explicitly.)
    metrics = strategy_health.portfolio_metrics("V", strategy_health.Window("D","2024-01-01","2024-01-01"),capital_usdt=100)
    assert metrics.max_drawdown_pct is None and metrics.total_return_pct is None
    assert "Missing completed BTC mark" in metrics.data_error


def test_partial_day_never_reads_later_close(ledger):
    trade(ledger,exit=None,pnl=None)
    with sqlite3.connect(ledger) as con:
        for hour,minute,price in [(11,59,90),(12,0,999)]:
            ts=int(datetime(2024,1,1,hour,minute,tzinfo=timezone.utc).timestamp()*1000)
            con.execute("INSERT INTO btc_1m VALUES (?,?)",(ts,price))
    rows=equity.daily_equity("V","2024-01-01","2024-01-01",100,as_of=datetime(2024,1,1,12,tzinfo=timezone.utc))
    assert rows[0]["equity_usdt"] == pytest.approx(90)


def test_missing_funding_is_not_silently_zero(ledger):
    trade(ledger,exit=None,pnl=None,strategy="ADX")
    mark(ledger,1,100)
    with pytest.raises(equity.EquityDataError,match="Missing BTC funding settlement"):
        equity.daily_equity("V","2024-01-01","2024-01-01",100)


def test_flip_closes_old_direction_and_opens_new_position_with_separate_fee(ledger):
    trade(ledger,qty=2,exit="2024-01-02T12:00:00+00:00",pnl=38)
    event(ledger,0,"OPEN",1,2,100,200)
    event(ledger,1,"FLIP",2,0,120,0,realized=38,fee=2)
    trade(ledger,tid="NEW",qty=2,entry="2024-01-02T12:00:00+00:00",
          exit="2024-01-03T12:00:00+00:00",pnl=18,direction="SHORT")
    event(ledger,0,"OPEN",2,2,120,240,fee=2,tid="NEW")
    event(ledger,1,"CLOSE",3,0,110,0,realized=18,fee=2,tid="NEW")
    mark(ledger,1,110)
    mark(ledger,2,115)
    rows=equity.daily_equity("V","2024-01-01","2024-01-03",1000)
    assert [row["daily_pnl"] for row in rows] == pytest.approx([20,26,8])


def test_full_portfolio_notebook_uses_marked_risk_series(ledger,monkeypatch):
    import json
    import math
    from pathlib import Path
    from datetime import timedelta
    from strategies.support import clock
    trade(ledger)
    for day, price in [(1,80),(2,110),(3,112)]:
        mark(ledger,day,price)
    monkeypatch.setattr(clock,"now_utc",lambda:datetime(2024,1,5,12,tzinfo=timezone.utc))
    notebook=Path(__file__).resolve().parents[1]/"studies/notebooks/full_portfolio_report.ipynb"
    cells=json.loads(notebook.read_text(encoding="utf-8"))["cells"]
    scope=dict(sqlite3=sqlite3,db=db,equity=equity,clock=clock,timedelta=timedelta,math=math)
    for cell in cells:
        source="".join(cell["source"])
        if cell["cell_type"] == "code":
            compile(source,str(notebook),"exec")
        if source.startswith(("def load_daily_returns(","def accumulate_equity(","def equity_metrics(")):
            exec(source,scope)
    daily=scope["load_daily_returns"]("V",100)
    metrics=scope["equity_metrics"](100,daily)
    assert metrics["final_equity"] == pytest.approx(112)
    assert metrics["mdd_pct"] == pytest.approx(-20)


def test_leverage_adjustment_reprices_funding_notional_without_changing_basis(ledger):
    trade(ledger,entry="2024-01-01T12:00:00+00:00",exit=None,pnl=None,strategy="ADX")
    event(ledger,0,"OPEN",1,1,100,100)
    event(ledger,1,"LEVERAGE_ADJUST",2,1,120,120)
    mark(ledger,1,100)
    mark(ledger,2,120)
    with sqlite3.connect(ledger) as con:
        for day,hour,rate in [(1,16,0),(2,0,0),(2,8,0),(2,16,.01)]:
            ts=int(datetime(2024,1,day,hour,tzinfo=timezone.utc).timestamp())
            con.execute("INSERT INTO cd_funding_rate VALUES (?,?)",(ts,rate))
    rows=equity.daily_equity("V","2024-01-01","2024-01-02",100)
    # Price P&L remains 1*(120-100); funding uses repriced $120 notional.
    assert rows[-1]["equity_usdt"] == pytest.approx(118.8)


def test_live_latest_row_requires_final_refresh_even_after_clock_boundary(ledger,monkeypatch):
    trade(ledger,exit=None,pnl=None)
    mark(ledger,1,80)
    with sqlite3.connect(ledger) as con:
        con.execute("DELETE FROM btc_1m WHERE open_time=(SELECT MAX(open_time) FROM btc_1m)")
    with pytest.raises(equity.EquityDataError,match="Unfinalized latest BTC mark"):
        equity.daily_equity("V","2024-01-01","2024-01-01",100)
    # A historical replay explicitly treats its completed source bars as final.
    from strategies.support import clock
    monkeypatch.setattr(clock,"_simulated_now",datetime(2024,1,2,tzinfo=timezone.utc))
    assert equity.daily_equity("V","2024-01-01","2024-01-01",100)[0]["equity_usdt"] == 80

"""Daily marked equity from execution events and contemporaneous prices.

This is the risk series; strategy_health.trades_daily_returns remains the
explicitly realized accounting series. Final trade P&L is first observable
on its close date. Earlier equity uses held quantity, its running cost basis,
and completed market bars, never a portion of the eventual trade result.

Recorded execution costs retain their ledger booking dates. SCALE_DOWN and
CLOSE realization is already net of costs; OPEN/SCALE_UP/LEVERAGE_ADJUST fees
are separate cashflows. Funding is accrued at settlement timestamps while a
trade remains open, then reconciled to the booked net realization on close.
The ledger's fixed-notional funding convention is used between resize events.
CARRY retains the ledger's synthetic delta-neutral, zero-basis price model.
"""
from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from strategies.support import clock, db

UTC = timezone.utc
_NO_FUNDING = {"PDO_RETOUCH", "CHENTO_TRIPLE_V3"}


class EquityDataError(ValueError):
    """A risk curve cannot be reconstructed from the available observations."""


def _dt(value: str | datetime) -> datetime:
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError as exc:
        raise EquityDataError(f"Invalid execution timestamp: {value!r}") from exc
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("BEGIN")
    return con


def _number(value, label: str, *, positive: bool = False) -> float:
    if value is None:
        raise EquityDataError(f"Missing {label}")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise EquityDataError(f"Invalid {label}: {value!r}") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise EquityDataError(f"Invalid {label}: {value!r}")
    return result


class _Market:
    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self.marks: dict[tuple[str, datetime], float] = {}
        self.rates: dict[tuple[str, int], float] = {}
        self.latest: dict[str, int | None] = {}

    def mark(self, asset: str, cutoff: datetime) -> float:
        key = (asset, cutoff)
        if key in self.marks:
            return self.marks[key]
        table = {"BTC": "btc_1m", "ETH": "eth_1m"}.get(asset)
        if table is None:
            raise EquityDataError(f"No historical mark source for {asset}")
        cutoff_ms = int(cutoff.timestamp() * 1000)
        try:
            if not clock.is_simulated() and table not in self.latest:
                self.latest[table] = self.con.execute(f"SELECT MAX(open_time) FROM {table}").fetchone()[0]
            row = self.con.execute(
                f"SELECT open_time, close FROM {table} WHERE open_time <= ? "
                "ORDER BY open_time DESC LIMIT 1", (cutoff_ms - 60_000,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise EquityDataError(f"Cannot read {asset} marks: {exc}") from exc
        if row is None or cutoff_ms - row["open_time"] > 600_000:
            raise EquityDataError(f"Missing completed {asset} mark at {cutoff.isoformat()} (max age 10m)")
        # The live feed updates its provisional last row with its successor.
        # A wall-clock boundary alone cannot prove that refresh happened.
        if not clock.is_simulated() and row["open_time"] == self.latest[table]:
            raise EquityDataError(f"Unfinalized latest {asset} mark at {cutoff.isoformat()}; waiting for successor row")
        value = _number(row["close"], f"{asset} mark at {cutoff.isoformat()}", positive=True)
        self.marks[key] = value
        return value

    def funding(self, asset: str, start: datetime, end: datetime,
                notional: float, sign: float) -> float:
        """Settlements in (start, end), with end an exclusive observation limit."""
        table = {"BTC": "cd_funding_rate", "ETH": "cd_funding_rate_eth"}.get(asset)
        if table is None:
            raise EquityDataError(f"No funding source for {asset}")
        ts = (int(start.timestamp()) // 28800 + 1) * 28800
        total = 0.0
        while ts < end.timestamp():
            key = (asset, ts)
            if key not in self.rates:
                try:
                    row = self.con.execute(f"SELECT fr_close FROM {table} WHERE timestamp=?", (ts,)).fetchone()
                except sqlite3.Error as exc:
                    raise EquityDataError(f"Cannot read {asset} funding: {exc}") from exc
                if row is None:
                    raise EquityDataError(f"Missing {asset} funding settlement at {datetime.fromtimestamp(ts, UTC).isoformat()}")
                self.rates[key] = _number(row[0], f"{asset} funding at {ts}")
            total += sign * notional * self.rates[key]
            ts += 28800
        return total


@dataclass
class _Position:
    trade: dict
    events: list[dict]
    index: int = 0
    qty: float = 0.0
    basis: float = 0.0
    realized: float = 0.0
    separate_fees: float = 0.0
    # Funding notional segments: entry/resize timestamp and new notional.
    segments: list[tuple[datetime, float]] = field(default_factory=list)

    def advance(self, cutoff: datetime) -> None:
        while self.index < len(self.events) and self.events[self.index]["dt"] < cutoff:
            event = self.events[self.index]
            self.index += 1
            kind = event["event_type"]
            after = _number(event.get("qty_after"), f"{self.trade['id']} {kind} quantity")
            if after < 0:
                raise EquityDataError(f"Negative quantity for {self.trade['id']}")
            fee = _number(event.get("fee_usdt", 0) or 0, "event fee")
            if kind in {"OPEN", "SCALE_UP"}:
                price = _number(event.get("price"), "execution price", positive=True)
                if after <= self.qty:
                    raise EquityDataError(f"Invalid {kind} quantity for {self.trade['id']}")
                self.basis = (self.qty * self.basis + (after - self.qty) * price) / after
                self.separate_fees += fee
            elif kind in {"SCALE_DOWN", "CLOSE", "FLIP"}:
                self.realized += _number(event.get("realized_pnl_delta_usdt"), "realized event P&L")
                if after > self.qty or (kind in {"CLOSE", "FLIP"} and after != 0):
                    raise EquityDataError(f"Invalid {kind} quantity for {self.trade['id']}")
            elif kind == "LEVERAGE_ADJUST":
                if after != self.qty:
                    raise EquityDataError(f"Leverage event changes quantity for {self.trade['id']}")
                self.separate_fees += fee
            else:
                raise EquityDataError(f"Unknown adjustment {kind!r}")
            self.qty = after
            # Leverage adjustments preserve qty/basis but the writer also
            # reprices current_size_usdt. Funding must follow that notional.
            notional = _number(event.get("size_usdt_after"), "event notional", positive=True) if after else 0.0
            self.segments.append((event["dt"], notional))

    def value(self, cutoff: datetime, market: _Market) -> float:
        self.advance(cutoff)
        total = self.realized - self.separate_fees
        if self.qty == 0:
            return total
        strategy = self.trade["strategy"].upper()
        asset = self.trade["asset"].upper()
        carry = strategy == "CARRY"
        sign = 1.0 if self.trade["direction"].upper() == "LONG" else -1.0
        if not carry:
            total += sign * self.qty * (market.mark(asset, cutoff) - self.basis)
        if strategy not in _NO_FUNDING:
            for i, (start, notional) in enumerate(self.segments):
                end = self.segments[i + 1][0] if i + 1 < len(self.segments) else cutoff
                # A resize at a settlement timestamp settles the old position.
                if i + 1 < len(self.segments):
                    end += timedelta(microseconds=1)
                total += market.funding(asset, start, end, notional, 1.0 if carry else -sign)
        return total


def _position(trade: dict, adjustments: list[dict]) -> _Position:
    tid = trade["id"]
    entry = _dt(trade["actual_entry_time"])
    if trade["direction"].upper() not in {"LONG", "SHORT"}:
        raise EquityDataError(f"Unsupported direction for {tid}")
    if not adjustments or adjustments[0]["event_type"] != "OPEN":
        qty = _number(trade.get("qty"), f"{tid} entry quantity", positive=True)
        adjustments.insert(0, dict(event_type="OPEN", event_time=entry.isoformat(),
            qty_after=qty, price=trade.get("entry_price"), fee_usdt=0,
            size_usdt_after=trade.get("size_usdt")))
    if trade["status"] == "closed" and not any(a["event_type"] in {"CLOSE", "FLIP"} for a in adjustments):
        if not trade.get("actual_exit_time"):
            raise EquityDataError(f"Missing exit timestamp for {tid}")
        prior = sum(float(a.get("realized_pnl_delta_usdt") or 0) for a in adjustments)
        adjustments.append(dict(event_type="CLOSE", event_time=trade["actual_exit_time"],
            qty_after=0, fee_usdt=0, size_usdt_after=0,
            realized_pnl_delta_usdt=_number(trade.get("pnl_usdt"), f"{tid} final P&L") - prior))
    previous = entry
    for event in adjustments:
        event["dt"] = _dt(event["event_time"])
        if event["dt"] < previous:
            raise EquityDataError(f"Out-of-order adjustments for {tid}")
        previous = event["dt"]
    return _Position(trade, adjustments)


def daily_equity(variant_id: str, start: str, end: str, capital_usdt: float,
                 *, as_of: datetime | None = None) -> list[dict]:
    """Calendar-complete equity for inclusive UTC dates, capped at ``as_of``.

    ``capital_usdt`` is the original fixed sizing capital. Prior-window P&L
    forms opening equity; an existing position is marked at the window's
    opening boundary so its earlier gains/losses are not counted again.
    ``return_pct`` = daily P&L / original capital; ``nav_return_pct`` = daily
    P&L / opening daily equity, suitable for volatility and daily Sharpe.
    No quote after the observation boundary or stale quote is substituted.
    """
    _number(capital_usdt, "capital", positive=True)
    start_dt = datetime.combine(date.fromisoformat(start), time(), UTC)
    end_dt = datetime.combine(date.fromisoformat(end) + timedelta(days=1), time(), UTC)
    limit = min(end_dt, _dt(as_of or clock.now_utc()) + timedelta(microseconds=1))
    if limit <= start_dt:
        return []
    try:
        with closing(_connect(db.DASH_DB)) as ledger:
            invalid = ledger.execute(
                "SELECT id FROM trades WHERE strategy_variant=? AND status IN ('open','closed') "
                "AND julianday(actual_entry_time) IS NULL LIMIT 1", (variant_id,),
            ).fetchone()
            if invalid:
                raise EquityDataError(f"Missing/invalid entry timestamp for {invalid['id']}")
            trades = [dict(row) for row in ledger.execute(
                "SELECT * FROM trades WHERE strategy_variant=? AND status IN ('open','closed') "
                # SQLite dates round sub-millisecond cutoffs; admit the
                # boundary here and apply the precise cutoff in advance().
                "AND julianday(actual_entry_time) <= julianday(?)", (variant_id, limit.isoformat()))]
            has_adjustments = ledger.execute("SELECT 1 FROM sqlite_master WHERE name='trade_adjustments'").fetchone()
            positions = []
            for trade in trades:
                events = [dict(row) for row in ledger.execute(
                    "SELECT * FROM trade_adjustments WHERE trade_id=? ORDER BY seq", (trade["id"],)
                )] if has_adjustments else []
                positions.append(_position(trade, events))
        with closing(_connect(db.TRADER_DB)) as market_con:
            market = _Market(market_con)
            previous = capital_usdt + sum(p.value(start_dt, market) for p in positions)
            rows = []
            current = start_dt
            while current < limit:
                cutoff = min(current + timedelta(days=1), limit)
                value = capital_usdt + sum(p.value(cutoff, market) for p in positions)
                pnl = value - previous
                rows.append(dict(date=current.date().isoformat(), equity_usdt=value,
                    opening_equity_usdt=previous, daily_pnl=pnl,
                    return_pct=pnl / capital_usdt * 100,
                    nav_return_pct=pnl / previous * 100 if previous > 0 else None))
                previous = value
                current += timedelta(days=1)
            return rows
    except (sqlite3.Error, TypeError, KeyError) as exc:
        raise EquityDataError(f"Cannot reconstruct {variant_id} equity: {exc}") from exc


def marked_daily_returns(variant_id: str, start: str, end: str,
                         capital_usdt: float, *, fixed_capital: bool = False
                         ) -> list[tuple[str, float]]:
    rows = daily_equity(variant_id, start, end, capital_usdt)
    key = "return_pct" if fixed_capital else "nav_return_pct"
    if any(row[key] is None for row in rows):
        raise EquityDataError("Daily NAV returns undefined after non-positive equity")
    return [(row["date"], row[key]) for row in rows]

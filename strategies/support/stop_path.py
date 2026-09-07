"""Replay completed minute candles for paper stops before checking the quote.

Only the first observed crossing can close a trade. A gap through a stop fills
at the worse opening price and time; an ordinary wick touch fills at the stop.
Wicks use the minute's close boundary as their recorded event time because
OHLC does not tell us the time of the touch within that minute.

Progress and missing ranges live in the trade's notes, so restarting does not
forget a missed minute or re-scan the healthy history on every tick. Missing
rows are retried if the feed/backfill supplies them later. In live mode the
newest stored row is withheld: the Binance feed re-fetches that provisional
row together with its successor, in one transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import sqlite3
from typing import Callable, Sequence

from strategies.support import clock, db

_MINUTE_MS = 60_000
_STATE_KEY = "_stop_path_v1"
_TABLES = {"BTC": "btc_1m", "ETH": "eth_1m"}
# Each level is (reason, stop price), valid at the supplied UTC time.
StopLevels = Callable[[datetime], Sequence[tuple[str, float]]]


@dataclass(frozen=True)
class StopExit:
    price: float
    at: datetime
    reason: str


def _utc(value: datetime | str) -> datetime:
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None
            else dt.astimezone(timezone.utc))


def _notes(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
        if isinstance(value, dict):
            return value
    except (ValueError, TypeError):
        pass
    # Preserve legacy free text instead of dropping it on the first checkpoint.
    return {"_legacy_notes": raw}


def entry_stop_pct(trade: dict, default_pct: float) -> float:
    """Use the threshold captured at entry, not today's config or leverage.

    Legacy rows without the effective threshold use their recorded configured
    stop and immutable entry leverage. Only rows without either stop field use
    the sleeve default; the historical stop semantic is unknowable there.
    """
    from strategies.support.risk_config import effective_price_move_sl_pct
    blob = _notes(trade.get("notes"))
    recorded = blob.get("sl_semantic_price_thresh_pct")
    if recorded is None:
        configured = float(blob.get("stop_loss_pct", default_pct))
        recorded = effective_price_move_sl_pct(configured, float(trade.get("leverage") or 1))
    value = float(recorded)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid entry stop threshold for {trade['id']}")
    return value


def scheduled_close_bound(trade: dict, now: datetime) -> datetime:
    """Do not walk stops after a trade's already-due scheduled exit."""
    raw = trade.get("exit_time")
    if not raw:
        return now
    try:
        due = _utc(raw)
    except (TypeError, ValueError):
        return now
    return due if _utc(trade["actual_entry_time"]) <= due <= now else now


def completed_price_at(asset: str, at: datetime) -> float:
    """Historical scheduled fill; never pair a later quote with an earlier time."""
    table = _TABLES[asset.upper()]
    cutoff_ms = int(at.timestamp() * 1000)
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        row = con.execute(
            f"SELECT open_time, close FROM {table} WHERE open_time <= ? "
            "ORDER BY open_time DESC LIMIT 1", (cutoff_ms - _MINUTE_MS,),
        ).fetchone()
        if row is not None and not clock.is_simulated():
            latest = con.execute(f"SELECT MAX(open_time) FROM {table}").fetchone()[0]
            if int(latest) <= row[0]:
                raise ValueError(f"Unfinalized {asset} exit minute at {at.isoformat()}")
    finally:
        con.close()
    if (row is None or cutoff_ms - row[0] > 600_000 or row[1] is None
            or not math.isfinite(row[1]) or row[1] <= 0):
        raise ValueError(f"Missing completed {asset} scheduled fill at {at.isoformat()}")
    return float(row[1])


def resolve_sleeve_close(trade: dict, price: float, now: datetime) -> StopExit | None:
    """Resolve stops for every ADX/Thursday close caller, including backstops.

    Called by the central close pipeline before it computes or persists P&L.
    Returning a fill leaves the caller's configured fee/slippage model intact.
    Other strategies retain their own execution logic.
    """
    strategy = trade["strategy"].upper()
    if strategy == "ADX":
        from strategies.sleeves.adx.signal import _load_btc_daily_candles, _stop_levels_at
        hit = check_stop_path(
            trade, trade["asset"],
            _stop_levels_at(_load_btc_daily_candles(), trade, entry_stop_pct(trade, 10.0)),
            now=now, current_price=price, save_progress=False)
        if hit is None and not clock.is_simulated():
            # Do not book a recovered scheduled/signal winner before the
            # delayed final refresh can reveal the exit minute's stop wick.
            completed_price_at(trade["asset"], now)
        return hit
    if strategy == "THU_BEAR":
        bound = scheduled_close_bound(trade, now)
        pct = entry_stop_pct(trade, 5.0)
        stop = float(trade["entry_price"]) * (
            1 - pct / 100 if trade["direction"].upper() == "LONG" else 1 + pct / 100)
        hit = check_stop_path(trade, trade["asset"], lambda _: [("stop_loss", stop)],
                              now=bound, current_price=price if bound == now else None,
                              save_progress=False)
        if hit is not None:
            return hit
        if bound < now:
            return StopExit(completed_price_at(trade["asset"], bound), bound, "scheduled_exit")
        if not clock.is_simulated():
            completed_price_at(trade["asset"], bound)
    return None


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if start > end:
            continue
        if out and start <= out[-1][1] + _MINUTE_MS:
            out[-1] = (out[-1][0], max(end, out[-1][1]))
        else:
            out.append((start, end))
    return out


def _save_progress(trade: dict, blob: dict, through_ms: int,
                   gaps: list[tuple[int, int]]) -> None:
    state = {"through_ms": through_ms, "gaps": [list(gap) for gap in gaps]}
    if blob.get(_STATE_KEY) == state:
        return
    blob = {**blob, _STATE_KEY: state}
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        # Compare-and-swap preserves concurrent note edits and cannot move a
        # different worker's checkpoint backwards. A conflict is retried from
        # freshly loaded notes on the next sweep. Closed trades are untouched.
        con.execute(
            "UPDATE trades SET notes=? WHERE id=? AND status='open' AND notes IS ?",
            (json.dumps(blob), trade["id"], trade.get("notes")),
        )
        con.commit()
    finally:
        con.close()


def _hit(direction: str, levels: Sequence[tuple[str, float]],
         open_price: float, high: float, low: float,
         at: datetime, opened_at: datetime | None = None) -> StopExit | None:
    valid = [(reason, float(price)) for reason, price in levels
             if math.isfinite(price) and price > 0]
    if not valid:
        return None
    # If both fixed and trailing stops touch in one bar, the tighter stop
    # would execute first. This also handles opens beyond both levels.
    reason, stop = (max(valid, key=lambda item: item[1]) if direction == "LONG"
                    else min(valid, key=lambda item: item[1]))
    if direction == "LONG" and low <= stop:
        event_at = opened_at if open_price <= stop and opened_at is not None else at
        return StopExit(min(open_price, stop), event_at, reason)
    if direction == "SHORT" and high >= stop:
        event_at = opened_at if open_price >= stop and opened_at is not None else at
        return StopExit(max(open_price, stop), event_at, reason)
    return None


def check_stop_path(trade: dict, asset: str, levels_at: StopLevels, *,
                    now: datetime, current_price: float | None = None,
                    save_progress: bool = True
                    ) -> StopExit | None:
    """Find a stop crossing since entry/checkpoint, then check the quote.

    ``levels_at(t)`` must only return levels known at t. Full OHLC begins at
    the first minute opened at or after entry; an entry mid-minute cannot use
    that minute's earlier high/low. Quote fallback remains available when
    history is missing or the newest row has not yet been finalized. No path
    checkpoint is written until the entire available range has no crossing.
    The central close pipeline uses ``save_progress=False`` while holding its
    ledger write lock: it will close the trade in that same transaction.
    """
    now = _utc(now)
    entry = _utc(trade["actual_entry_time"])
    direction = trade["direction"].upper()
    if direction not in ("LONG", "SHORT"):
        raise ValueError(f"Unsupported stop direction: {direction!r}")
    if entry > now:
        return None
    table = _TABLES[asset.upper()]
    entry_ms = math.ceil(entry.timestamp() * 1000)
    first_ms = ((entry_ms + _MINUTE_MS - 1) // _MINUTE_MS) * _MINUTE_MS
    upper = (int(now.timestamp() * 1000) // _MINUTE_MS - 1) * _MINUTE_MS
    blob = _notes(trade.get("notes"))
    state = blob.get(_STATE_KEY) or {}
    through = int(state.get("through_ms", first_ms - _MINUTE_MS))
    # A rewound replay/new ledger cannot inherit progress from its future.
    if through > upper:
        through = first_ms - _MINUTE_MS
        state = {}
    pending = [(max(first_ms, int(a)), min(upper, int(b)))
               for a, b in state.get("gaps", [])]

    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        if not clock.is_simulated():
            latest = con.execute(f"SELECT MAX(open_time) FROM {table}").fetchone()[0]
            if latest is None:
                upper = first_ms - _MINUTE_MS
            else:
                upper = min(upper, int(latest) - _MINUTE_MS)
        ranges = _merge_ranges(
            [(a, min(b, upper)) for a, b in pending]
            + [(max(first_ms, through + _MINUTE_MS), upper)])
        gaps: list[tuple[int, int]] = []
        for start, end in ranges:
            expected = start
            rows = con.execute(
                f"SELECT open_time, open, high, low, close FROM {table} "
                "WHERE open_time >= ? AND open_time <= ? ORDER BY open_time",
                (start, end),
            )
            for ts, o, h, l, c in rows:
                ts = int(ts)
                if ts > expected:
                    gaps.append((expected, ts - _MINUTE_MS))
                expected = ts + _MINUTE_MS
                try:
                    o, h, l, c = map(float, (o, h, l, c))
                except (TypeError, ValueError):
                    gaps.append((ts, ts))
                    continue
                if (ts % _MINUTE_MS or any(not math.isfinite(v) or v <= 0
                                           for v in (o, h, l, c))
                        or l > min(o, c) or h < max(o, c) or l > h):
                    gaps.append((ts, ts))
                    continue
                opened_at = datetime.fromtimestamp(ts / 1000, timezone.utc)
                closed_at = datetime.fromtimestamp((ts + _MINUTE_MS) / 1000,
                                                   timezone.utc)
                result = _hit(direction, levels_at(opened_at), o, h, l,
                              closed_at, opened_at)
                if result is not None:
                    return result
            if expected <= end:
                gaps.append((expected, end))
        # Preserve gaps outside the current live finality boundary, if any.
        gaps.extend((max(a, upper + _MINUTE_MS), b)
                    for a, b in pending if b > upper)
    finally:
        con.close()

    if current_price is not None and math.isfinite(current_price) and current_price > 0:
        result = _hit(direction, levels_at(now), current_price, current_price,
                      current_price, now)
        if result is not None:
            return result
    if ranges and save_progress:
        _save_progress(trade, blob, max(through, upper), _merge_ranges(gaps))
    return None

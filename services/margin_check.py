"""Adapter between backtest_runner.py and services.margin_sim.

Builds margin-simulator inputs from the SHADOW trade record + trader.db
price/funding data, runs the simulator, returns liquidation events.

Phase 2 scope:
  - Per-trade isolated check for all leveraged sleeves (leverage > 1).
  - Cross-margin special case for CARRY (long spot + short perp hedge pair).
  - Pre-fetch + cache hourly bars and funding settlements per (asset, window)
    to avoid re-querying trader.db on every tick.

Skipped for v1 (Phase 2.5+ if needed):
  - Cross margin across multiple simultaneous trades from different sleeves.
    Each trade is checked in isolation; the first sleeve to liquidate doesn't
    affect the others' margin pools.
  - Notional-tiered MM (uses flat 0.5%).
  - Basis-blowout modeling.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from services import db
from services.margin_sim import (
    FundingSettlement,
    LiquidationEvent,
    MarginMode,
    PerpPosition,
    PriceBar,
    Side,
    SimParams,
    SpotLeg,
    simulate_position_path,
)


# CARRY's perp leg is SHORT delta-neutral; the trade record's direction='LONG'
# refers to the spot side. Other sleeves: trade direction is the perp direction.
def _perp_side_for_trade(strategy: str, trade_direction: str) -> Side:
    if strategy == "CARRY":
        return Side.SHORT
    return Side.LONG if trade_direction == "LONG" else Side.SHORT


def _is_carry_pair(strategy: str) -> bool:
    return strategy == "CARRY"


# ─── trader.db data fetchers ──────────────────────────────────────────────────

def _aggregate_minute_to_hourly(rows: list[tuple]) -> list[PriceBar]:
    """Group 1m bars into hourly buckets keyed by floor(open_time / 3600).
    Each hourly bar's high = max(highs), low = min(lows), close = last close."""
    buckets: dict[int, dict] = {}
    for open_time, op, hi, lo, cl in rows:
        if cl is None:
            continue
        h = (open_time // 3600) * 3600
        b = buckets.setdefault(h, {"high": float(hi or cl), "low": float(lo or cl),
                                    "close": float(cl), "last_ts": open_time})
        b["high"] = max(b["high"], float(hi or cl))
        b["low"] = min(b["low"], float(lo or cl))
        if open_time >= b["last_ts"]:
            b["close"] = float(cl)
            b["last_ts"] = open_time
    return [PriceBar(
        ts=datetime.fromtimestamp(h, tz=timezone.utc),
        high=b["high"], low=b["low"], close=b["close"],
    ) for h, b in sorted(buckets.items())]


def _load_perp_bars(asset: str, start_ts: int, end_ts: int) -> list[PriceBar]:
    """Hourly perp OHLC bars. BTC uses cd_futures_ohlcv (1h native); ETH
    aggregates eth_1m → 1h. Falls back to spot if perp data unavailable
    (basis is ~10bp for backtest purposes)."""
    con = sqlite3.connect(str(db.TRADER_DB))
    if asset.upper() == "BTC":
        rows = con.execute(
            "SELECT timestamp, open, high, low, close FROM cd_futures_ohlcv "
            "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (start_ts, end_ts),
        ).fetchall()
        con.close()
        if rows:
            return [PriceBar(
                ts=datetime.fromtimestamp(ts, tz=timezone.utc),
                high=float(hi or cl), low=float(lo or cl), close=float(cl),
            ) for ts, op, hi, lo, cl in rows if cl is not None]
        # Fall back to spot
        return _load_spot_bars(asset, start_ts, end_ts)
    # ETH: aggregate 1m → hourly
    rows = con.execute(
        f"SELECT open_time, open, high, low, close FROM {asset.lower()}_1m "
        "WHERE open_time >= ? AND open_time <= ? ORDER BY open_time",
        (start_ts, end_ts),
    ).fetchall()
    con.close()
    return _aggregate_minute_to_hourly(rows)


def _load_spot_bars(asset: str, start_ts: int, end_ts: int) -> list[PriceBar]:
    """Hourly spot bars. BTC uses cd_spot_binance (1h native); ETH aggregates
    eth_1m → 1h."""
    con = sqlite3.connect(str(db.TRADER_DB))
    if asset.upper() == "BTC":
        rows = con.execute(
            "SELECT timestamp, open, high, low, close FROM cd_spot_binance "
            "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (start_ts, end_ts),
        ).fetchall()
        con.close()
        return [PriceBar(
            ts=datetime.fromtimestamp(ts, tz=timezone.utc),
            high=float(hi or cl), low=float(lo or cl), close=float(cl),
        ) for ts, op, hi, lo, cl in rows if cl is not None]
    rows = con.execute(
        f"SELECT open_time, open, high, low, close FROM {asset.lower()}_1m "
        "WHERE open_time >= ? AND open_time <= ? ORDER BY open_time",
        (start_ts, end_ts),
    ).fetchall()
    con.close()
    return _aggregate_minute_to_hourly(rows)


def _load_funding(asset: str, start_ts: int, end_ts: int) -> list[FundingSettlement]:
    """Per-settlement funding rates from cd_funding_rate (BTC) or
    cd_funding_rate_eth (ETH). Each row is one settlement; we use fr_close
    as the per-period rate (decimal, e.g. 0.0001 = 1bp)."""
    table = "cd_funding_rate" if asset.upper() == "BTC" else "cd_funding_rate_eth"
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        rows = con.execute(
            f"SELECT timestamp, fr_close FROM {table} "
            "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (start_ts, end_ts),
        ).fetchall()
    except sqlite3.OperationalError:
        # Table missing for asset — return empty (simulator skips funding step).
        rows = []
    con.close()
    return [FundingSettlement(
        ts=datetime.fromtimestamp(ts, tz=timezone.utc),
        asset=asset.upper(), rate=float(fr) if fr is not None else 0.0,
    ) for ts, fr in rows]


# ─── Top-level check ──────────────────────────────────────────────────────────

def check_trade_for_liquidation(
    trade: dict,
    sim_now: datetime,
    params: Optional[SimParams] = None,
) -> Optional[LiquidationEvent]:
    """Run the margin simulator from trade entry → sim_now.

    Returns the first LiquidationEvent if the position would have been
    liquidated mid-hold, else None.

    Trades with leverage <= 1 are skipped (no liquidation possible).
    Trades with no leverage field are also skipped.
    """
    leverage = float(trade.get("leverage") or 0)
    if leverage <= 1.0:
        return None

    size_usdt = float(trade.get("size_usdt") or 0)
    entry_price = float(trade.get("entry_price") or 0)
    if size_usdt <= 0 or entry_price <= 0:
        return None

    asset = (trade.get("asset") or "BTC").upper()
    strategy = trade.get("strategy") or ""
    entry_time_str = trade.get("actual_entry_time") or trade.get("entry_time")
    if not entry_time_str:
        return None
    entry_time = datetime.fromisoformat(entry_time_str)
    if entry_time.tzinfo is None:
        entry_time = entry_time.replace(tzinfo=timezone.utc)

    # No work if sim_now is at or before entry.
    if sim_now <= entry_time:
        return None

    perp_side = _perp_side_for_trade(strategy, trade.get("direction") or "LONG")
    perp = PerpPosition(
        id=str(trade.get("id")),
        asset=asset, side=perp_side,
        size_usdt=size_usdt, leverage=leverage,
        entry_price=entry_price, entry_time=entry_time,
    )

    spot_legs: list[SpotLeg] = []
    margin_mode = MarginMode.ISOLATED  # default for non-hedge perps

    if _is_carry_pair(strategy):
        # CARRY's hedge: matching long spot. qty matches notional / spot_price.
        # Spot price ~= perp price at entry (basis is small).
        spot_qty = size_usdt / entry_price
        spot_legs.append(SpotLeg(
            asset=asset, qty=spot_qty,
            entry_price=entry_price, entry_time=entry_time,
        ))
        margin_mode = MarginMode.CROSS

    # Fetch market data over [entry, sim_now] window.
    start_ts = int(entry_time.timestamp())
    end_ts = int(sim_now.timestamp())

    perp_bars = _load_perp_bars(asset, start_ts, end_ts)
    spot_bars = _load_spot_bars(asset, start_ts, end_ts) if spot_legs else []
    funding = _load_funding(asset, start_ts, end_ts)

    if not perp_bars:
        return None  # No data to check against.

    # Initial USDT cash for the simulator.
    # ISOLATED mode: just the position's IM (per-trade isolated check).
    # CROSS mode (CARRY): start with 0 USDT cash; rely on spot collateral.
    if margin_mode == MarginMode.ISOLATED:
        initial_usdt = perp.initial_margin
    else:
        initial_usdt = 0.0

    sim_params = params or SimParams(margin_mode=margin_mode)
    if params is None:
        sim_params = SimParams(
            margin_mode=margin_mode,
            mm_pct=0.005,
            spot_haircut=0.95,
            liquidation_fee_pct=0.005,
        )

    result = simulate_position_path(
        initial_usdt=initial_usdt,
        perp_positions=[perp], spot_legs=spot_legs,
        perp_price_bars={asset: perp_bars},
        spot_price_bars={asset: spot_bars} if spot_bars else {},
        funding_settlements=funding,
        params=sim_params,
    )
    if result.liquidations:
        return result.liquidations[0]
    return None


def check_liquidations_for_variant(
    variant_id: str,
    sim_now: datetime,
    params: Optional[SimParams] = None,
) -> list[tuple[dict, LiquidationEvent]]:
    """Walk open shadow trades for the variant; return list of
    (trade_dict, liq_event) pairs for any that would have been liquidated
    by sim_now. Caller is responsible for force-closing them via the
    sleeve's close function at liq_event.liq_price / liq_event.liq_time.
    """
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    opens = con.execute("""
        SELECT * FROM trades
        WHERE strategy_variant = ? AND execution_mode = 'SHADOW'
          AND status = 'open'
    """, (variant_id,)).fetchall()
    con.close()

    out: list[tuple[dict, LiquidationEvent]] = []
    for row in opens:
        t = dict(row)
        liq = check_trade_for_liquidation(t, sim_now, params)
        if liq is not None:
            out.append((t, liq))
    return out

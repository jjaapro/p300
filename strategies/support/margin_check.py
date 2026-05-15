"""Margin / liquidation check and force-close orchestration.

Two responsibilities live here:

1. **Math** — :func:`check_liquidations_for_variant` builds margin-simulator
   inputs from the paper trade record + trader.db price/funding data, runs
   the simulator, returns liquidation events. Pure read-only.

2. **Orchestration** — :func:`force_close_liquidations` walks every open
   paper trade for a variant, calls the math, then per-event invokes the
   sleeve's close function at the liquidation price/time. This is what the
   live tick (``orchestrator._check_liquidations_all_variants``) and the
   backtest tick (``backtest_runner.run._tick``) both call.

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

import logging
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from strategies.support import clock, db
from strategies.support.margin_sim import (
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

log = logging.getLogger("p300.margin_check")


# CARRY's perp leg is SHORT delta-neutral; the trade record's direction='LONG'
# refers to the spot side. Other sleeves: trade direction is the perp direction.
def _perp_side_for_trade(strategy: str, trade_direction: str) -> Side:
    if strategy == "CARRY":
        return Side.SHORT
    return Side.LONG if trade_direction == "LONG" else Side.SHORT


def _is_carry_pair(strategy: str) -> bool:
    return strategy == "CARRY"


# Per-sleeve margin-mode mapping for the go-live plan
# (memory/project_margin_mode_design.md): cross for every sleeve except
# FOMC (isolated, because FOMC's 10× leverage at the announcement bar is
# the one place we explicitly do NOT want netting with other positions).
# CARRY is also cross but handled separately because of its spot leg.
_ISOLATED_STRATEGIES = frozenset({"FOMC"})


def _margin_mode_for_strategy(strategy: str) -> MarginMode:
    """Return the configured margin mode for a sleeve. Default is CROSS;
    FOMC is the sole opt-out (ISOLATED). See _ISOLATED_STRATEGIES."""
    return (MarginMode.ISOLATED if strategy in _ISOLATED_STRATEGIES
            else MarginMode.CROSS)


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
    margin_mode = _margin_mode_for_strategy(strategy)

    if _is_carry_pair(strategy):
        # CARRY's hedge: matching long spot. qty matches notional / spot_price.
        # Spot price ~= perp price at entry (basis is small).
        spot_qty = size_usdt / entry_price
        spot_legs.append(SpotLeg(
            asset=asset, qty=spot_qty,
            entry_price=entry_price, entry_time=entry_time,
        ))
        # CARRY's mode is already CROSS via _margin_mode_for_strategy.

    # Fetch market data over [entry, sim_now] window.
    start_ts = int(entry_time.timestamp())
    end_ts = int(sim_now.timestamp())

    perp_bars = _load_perp_bars(asset, start_ts, end_ts)
    spot_bars = _load_spot_bars(asset, start_ts, end_ts) if spot_legs else []
    funding = _load_funding(asset, start_ts, end_ts)

    if not perp_bars:
        return None  # No data to check against.

    # Initial USDT cash for the simulator.
    # - ISOLATED (FOMC): the position's IM is its only margin pool.
    # - CROSS with spot collateral (CARRY): seed with 0 cash and let the
    #   spot leg's haircut value carry the pool.
    # - CROSS without spot (every other directional sleeve): seed with the
    #   position's IM. This is a single-trade check that doesn't model the
    #   variant-wide margin pool (would require summing all open trades'
    #   IM + variant free cash); in practice the simulator's cross/isolated
    #   liquidation breach is then identical for these sleeves until the
    #   variant-pool model lands. Direction is correct (no over-strict
    #   isolated walls); magnitude undercounts the cushion. Tracked in
    #   memory/project_margin_mode_design.md.
    if margin_mode == MarginMode.ISOLATED or not spot_legs:
        initial_usdt = perp.initial_margin
    else:
        initial_usdt = 0.0

    if params is None:
        sim_params = SimParams(margin_mode=margin_mode)
    else:
        sim_params = params

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
    """Walk open paper trades for the variant; return list of
    (trade_dict, liq_event) pairs for any that would have been liquidated
    by sim_now. Caller is responsible for force-closing them via the
    sleeve's close function at liq_event.liq_price / liq_event.liq_time.
    """
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    opens = con.execute("""
        SELECT * FROM trades
        WHERE strategy_variant = ? AND execution_mode = 'paper'
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


# ─── Force-close orchestration ────────────────────────────────────────────────

def _load_close_fn(strategy: str):
    """Return the sleeve-specific close function (which applies fees/funding).
    Falls back to None for unknown strategies — caller logs and skips."""
    if strategy == "ADX":
        from strategies.sleeves.adx.signal import _close_adx_paper
        return _close_adx_paper
    if strategy == "CARRY":
        from strategies.sleeves.carry.signal import _close_carry_paper
        return _close_carry_paper
    if strategy == "THU_BEAR":
        from strategies.sleeves.thu_bear.signal import _close_thu_bear_paper
        return _close_thu_bear_paper
    if strategy == "PDO_RETOUCH":
        from strategies.sleeves.pdo.signal import _close_pdo_paper
        return _close_pdo_paper
    if strategy == "CPR":
        from strategies.sleeves.cpr.signal import _close_cpr_paper
        return _close_cpr_paper
    if strategy == "FOMC":
        from strategies.sleeves.fomc.signal import _close_fomc_paper
        return _close_fomc_paper
    return None


def force_close_liquidations(variant_id: str, now_utc: datetime) -> int:
    """Walk open paper trades for this variant; for any leveraged trade
    whose margin trajectory would have breached maintenance margin between
    its entry and now_utc, force-close it via the sleeve's close function
    at the liquidation price/time.

    Tags forced-closes via the sleeve's close ``reason`` argument with
    ``'forced_exit:liquidation'`` so they're identifiable in the trades table.

    Always-on in backtest mode (per project decision). Trades at leverage
    <= 1 are skipped inside :func:`check_liquidations_for_variant` (no
    liquidation possible). Returns count force-closed.
    """
    events = check_liquidations_for_variant(variant_id, now_utc)
    if not events:
        return 0
    n = 0
    for trade, liq in events:
        close_fn = _load_close_fn(trade["strategy"])
        if close_fn is None:
            log.warning(f"[liq] no close_fn for {trade['strategy']!r} "
                        f"({trade['id']}) — liquidation event ignored")
            continue
        # Set the simulated clock to the liquidation time so the close
        # function records actual_exit_time correctly.
        prior = clock._simulated_now if hasattr(clock, '_simulated_now') else None
        clock.set_simulated_now(liq.liq_time)
        try:
            close_fn(trade["id"], liq.liq_price, "forced_exit:liquidation")
            n += 1
            log.warning(f"[liq] {trade['id']} {trade['strategy']} {trade['asset']} "
                        f"force-closed at {liq.liq_time.isoformat()} "
                        f"px={liq.liq_price:.2f} ({liq.reason})")
        except Exception:
            log.exception(f"[liq] close_fn raised for {trade['id']} during "
                          f"liquidation force-close — trade may be inconsistent")
        finally:
            # Restore prior clock; the main loop will set it back per-tick anyway.
            clock.set_simulated_now(prior)
    return n

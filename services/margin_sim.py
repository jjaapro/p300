"""Margin and liquidation simulator.

Pure module — no DB or I/O. Walks an hourly price path forward from a
position's open time, tracks cross/isolated margin, applies 8h funding
settlements, and detects liquidation events when account or per-position
collateral falls below maintenance margin.

Used by the backtest runner to detect mid-hold liquidations that the
strategy-level close logic alone misses (e.g. a CARRY hold survives 78
days at 5x but the perp leg would have been liquidated at 100x mid-pump).

Phase 1 scope: standalone module + unit tests. No integration yet —
``backtest_runner.py`` integration lands in Phase 2.

Key conventions:
  - Hourly bar resolution. Funding settlements happen at 00/08/16 UTC.
  - Position size in USDT notional (size_usdt = capital * alloc * leverage).
  - LONG perp: position_sign = +1; pays positive funding, receives negative.
  - SHORT perp: position_sign = -1; opposite.
  - Cross margin pool = USDT cash + spot_qty * spot_mark * haircut +
    Σ perp unrealized PnL. Liquidation when pool < Σ MM-required.
  - Isolated margin: each perp has its own pool = own initial margin +
    own unrealized PnL. Spot legs do not collateralize isolated perps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Sequence


SECONDS_PER_HOUR = 3600
FUNDING_HOURS_UTC = (0, 8, 16)


class Side(Enum):
    LONG = 1
    SHORT = -1


class MarginMode(Enum):
    ISOLATED = "isolated"
    CROSS = "cross"


@dataclass
class PerpPosition:
    """A single perp leg. ``id`` is just a label used to tag liquidation events."""
    id: str
    asset: str
    side: Side
    size_usdt: float          # notional at entry (= entry_qty * entry_price)
    leverage: float
    entry_price: float
    entry_time: datetime

    @property
    def initial_margin(self) -> float:
        return self.size_usdt / self.leverage

    @property
    def qty(self) -> float:
        return self.size_usdt / self.entry_price

    @property
    def position_sign(self) -> int:
        return self.side.value


@dataclass
class SpotLeg:
    """A long-spot position. Acts as cross-margin collateral when paired
    with a perp under MarginMode.CROSS. No effect under ISOLATED."""
    asset: str
    qty: float
    entry_price: float
    entry_time: datetime


@dataclass
class PriceBar:
    ts: datetime              # bar open time
    high: float
    low: float
    close: float


@dataclass
class FundingSettlement:
    ts: datetime              # 00/08/16 UTC settlement boundary
    asset: str
    rate: float               # per-settlement rate as decimal (e.g. 0.0001 = 1bp)


@dataclass
class LiquidationEvent:
    position_id: str
    liq_time: datetime
    liq_price: float
    margin_lost: float        # USDT debited from cash (initial margin + adverse pnl + fee)
    fee: float                # USDT
    reason: str               # human-readable why


@dataclass
class SimulationResult:
    final_usdt: float                      # cash balance at end (or at liquidation)
    final_perp_pnl: dict[str, float]       # per-position realized PnL at end
    funding_paid: float                    # total funding paid (negative = received)
    liquidations: list[LiquidationEvent]
    survived: dict[str, bool]              # per-position survival flag
    bars_processed: int


# ─── Mark-to-mark helpers ─────────────────────────────────────────────────────

def perp_unrealized_pnl(pos: PerpPosition, mark_price: float) -> float:
    """USDT PnL on the perp at given mark. LONG gains as mark rises;
    SHORT gains as mark falls. Linear (no funding included here)."""
    return pos.qty * (mark_price - pos.entry_price) * pos.position_sign


def adverse_mark(pos: PerpPosition, bar: PriceBar) -> float:
    """The within-bar mark price that maximizes the position's drawdown.
    SHORT is hurt by high; LONG is hurt by low. Used for liquidation checks
    (we want to know if the position would have been liquidated *intra-bar*,
    not just at the close)."""
    return bar.high if pos.side == Side.SHORT else bar.low


def spot_collateral_value(spot: SpotLeg, mark_price: float, haircut: float) -> float:
    """Cross-margin collateral value of a long spot position at given mark."""
    return spot.qty * mark_price * haircut


# ─── Funding settlement ───────────────────────────────────────────────────────

def funding_payment(pos: PerpPosition, rate: float) -> float:
    """Funding paid by this position at one settlement.
    Convention: positive rate means longs pay shorts.
    Returns positive = position paid out (expense), negative = position received."""
    return pos.position_sign * pos.size_usdt * rate


def is_funding_boundary(ts: datetime) -> bool:
    return ts.hour in FUNDING_HOURS_UTC and ts.minute == 0 and ts.second == 0


# ─── Margin pool computation ──────────────────────────────────────────────────

def cross_margin_pool(
    usdt_cash: float,
    perp_positions: Sequence[PerpPosition],
    perp_marks: dict[str, float],          # {position_id: mark_price}
    spot_legs: Sequence[SpotLeg],
    spot_marks: dict[str, float],          # {asset: mark_price}
    spot_haircut: float,
) -> float:
    """Available cross-margin pool in USDT.

    pool = cash
         + Σ(spot_qty * spot_mark * haircut)
         + Σ(perp_unrealized_pnl_at_mark)
    """
    pool = usdt_cash
    for spot in spot_legs:
        mark = spot_marks.get(spot.asset, spot.entry_price)
        pool += spot_collateral_value(spot, mark, spot_haircut)
    for pos in perp_positions:
        mark = perp_marks.get(pos.id, pos.entry_price)
        pool += perp_unrealized_pnl(pos, mark)
    return pool


def required_maintenance_margin(
    perp_positions: Sequence[PerpPosition],
    perp_marks: dict[str, float],
    mm_pct: float,
) -> float:
    """Total MM required for all open perps. Notional-based (mark * qty)."""
    total = 0.0
    for pos in perp_positions:
        mark = perp_marks.get(pos.id, pos.entry_price)
        total += abs(pos.qty) * mark * mm_pct
    return total


def isolated_position_health(
    pos: PerpPosition,
    mark: float,
    mm_pct: float,
) -> tuple[float, float]:
    """Returns (available_margin, mm_required) for a single isolated position.

    Available margin = initial_margin + unrealized_pnl. (No funding here —
    funding is debited/credited from a separate cash pool even in isolated
    mode on most exchanges; we conservatively keep funding out of the isolated
    pool, matching Binance's behavior.)

    NOTE: real exchanges allow adding more margin to isolated positions; this
    simulator assumes static initial margin (the standard CARRY/FOMC paper-
    trading assumption — no margin top-ups)."""
    available = pos.initial_margin + perp_unrealized_pnl(pos, mark)
    required = abs(pos.qty) * mark * mm_pct
    return available, required


# ─── The core simulator ──────────────────────────────────────────────────────

@dataclass
class SimParams:
    """All tunables in one place."""
    margin_mode: MarginMode = MarginMode.CROSS
    mm_pct: float = 0.005                  # 0.5% maintenance margin
    spot_haircut: float = 0.50             # Binance USDM cross-margin collateral
                                            # ratio for BTC spot is 0.50 (0.70 at
                                            # higher VIP tier). Pre-2026-05-13
                                            # this defaulted to 0.95, which
                                            # overstated CARRY's cross-margin
                                            # pool by ~80% and under-reported
                                            # liquidation risk on hedge
                                            # positions. See AUDIT_2026_05_13.
    liquidation_fee_pct: float = 0.005     # 0.5% of notional on liquidation


def simulate_position_path(
    initial_usdt: float,
    perp_positions: list[PerpPosition],
    spot_legs: list[SpotLeg],
    perp_price_bars: dict[str, list[PriceBar]],   # {asset: bars sorted by ts}
    spot_price_bars: dict[str, list[PriceBar]],   # {asset: bars sorted by ts}
    funding_settlements: list[FundingSettlement],  # any asset, ordered by ts
    params: SimParams = SimParams(),
) -> SimulationResult:
    """Walk all assets' price bars from earliest entry to latest bar,
    apply funding at settlement boundaries, mark-to-mark each tick, detect
    liquidations.

    Liquidation handling:
      - On each bar, compute the within-bar adverse mark for each open perp.
      - Recompute pool (cross) or per-position health (isolated).
      - If breached, emit LiquidationEvent at this bar's adverse mark, debit
        cash by initial_margin + remaining unrealized loss + liquidation fee,
        and remove the position from open set.
      - Position is closed at the breaching bar; subsequent bars don't see it.

    Bar synchronization:
      - All assets must have aligned hourly bars (no gap-filling here).
      - We iterate over the union of bar timestamps, picking each asset's
        closest bar at that ts.
    """
    cash = initial_usdt
    open_perps: list[PerpPosition] = list(perp_positions)
    closed_pnl: dict[str, float] = {p.id: 0.0 for p in perp_positions}
    survived: dict[str, bool] = {p.id: True for p in perp_positions}
    liquidations: list[LiquidationEvent] = []
    funding_paid_total = 0.0

    # Build a unified timeline of (ts, kind, payload) events, sorted ascending.
    # kind ∈ {'bar', 'funding'}.
    timeline: list[tuple[datetime, str, object]] = []
    for asset, bars in perp_price_bars.items():
        for b in bars:
            timeline.append((b.ts, 'perp_bar', (asset, b)))
    for asset, bars in spot_price_bars.items():
        for b in bars:
            timeline.append((b.ts, 'spot_bar', (asset, b)))
    for f in funding_settlements:
        timeline.append((f.ts, 'funding', f))
    timeline.sort(key=lambda x: (x[0], 0 if x[1] == 'funding' else 1))
    # Funding before bars at same ts: settlement debits, then we evaluate margin.

    # Track the latest mark for each asset (perp + spot), keyed by asset.
    perp_marks: dict[str, float] = {p.id: p.entry_price for p in perp_positions}
    spot_marks: dict[str, float] = {s.asset: s.entry_price for s in spot_legs}
    perp_pos_by_asset: dict[str, list[PerpPosition]] = {}
    for p in perp_positions:
        perp_pos_by_asset.setdefault(p.asset, []).append(p)

    bars_processed = 0
    for ts, kind, payload in timeline:
        if not open_perps:
            break

        if kind == 'funding':
            f: FundingSettlement = payload  # type: ignore
            for pos in list(open_perps):
                if pos.asset != f.asset:
                    continue
                pay = funding_payment(pos, f.rate)
                cash -= pay
                funding_paid_total += pay
            # Re-check margin after funding (settlements can deplete cash to liq).
            _check_and_liquidate(
                ts, ts_price_source=None,  # funding-only ts has no bar mark; use last marks
                cash_ref=lambda v=None: cash if v is None else None,
                open_perps=open_perps, perp_marks=perp_marks,
                spot_legs=spot_legs, spot_marks=spot_marks,
                params=params, liquidations=liquidations,
                closed_pnl=closed_pnl, survived=survived,
                cash_setter=lambda v: None,  # placeholder; we update cash below
            )
            # Above helper does NOT mutate cash; do liquidation detection inline:
            cash = _liquidate_breaches(
                ts=ts, open_perps=open_perps, perp_marks=perp_marks,
                spot_legs=spot_legs, spot_marks=spot_marks,
                cash=cash, params=params, liquidations=liquidations,
                closed_pnl=closed_pnl, survived=survived,
                use_adverse=False,
            )
            continue

        if kind == 'perp_bar':
            asset, bar = payload  # type: ignore
            bars_processed += 1
            # Update mark for all perps on this asset to bar.close (used between bars).
            # But for the in-bar liquidation check, we use the adverse mark.
            for pos in [p for p in open_perps if p.asset == asset]:
                # Run liquidation check at adverse intra-bar mark first.
                cash = _liquidate_one_if_breached(
                    pos=pos, ts=bar.ts, mark=adverse_mark(pos, bar),
                    open_perps=open_perps, perp_marks=perp_marks,
                    spot_legs=spot_legs, spot_marks=spot_marks,
                    cash=cash, params=params, liquidations=liquidations,
                    closed_pnl=closed_pnl, survived=survived,
                )
                # If still alive, set mark to close for next round.
                if pos in open_perps:
                    perp_marks[pos.id] = bar.close
            continue

        if kind == 'spot_bar':
            asset, bar = payload  # type: ignore
            spot_marks[asset] = bar.close
            continue

    # End of timeline: realize unrealized PnL on surviving positions at last marks.
    for pos in open_perps:
        closed_pnl[pos.id] = perp_unrealized_pnl(pos, perp_marks.get(pos.id, pos.entry_price))

    # Final cash includes all funding flows but not the unrealized PnL of survivors
    # (caller can add closed_pnl to cash if they want a final total equity view).
    return SimulationResult(
        final_usdt=cash,
        final_perp_pnl=closed_pnl,
        funding_paid=funding_paid_total,
        liquidations=liquidations,
        survived=survived,
        bars_processed=bars_processed,
    )


# ─── Liquidation detection helpers ────────────────────────────────────────────

def _liquidate_one_if_breached(
    pos: PerpPosition,
    ts: datetime,
    mark: float,
    open_perps: list[PerpPosition],
    perp_marks: dict[str, float],
    spot_legs: list[SpotLeg],
    spot_marks: dict[str, float],
    cash: float,
    params: SimParams,
    liquidations: list[LiquidationEvent],
    closed_pnl: dict[str, float],
    survived: dict[str, bool],
) -> float:
    """Check one position at given mark; if breached, liquidate and return new cash."""
    if pos not in open_perps:
        return cash
    if params.margin_mode == MarginMode.ISOLATED:
        available, required = isolated_position_health(pos, mark, params.mm_pct)
        if available < required:
            return _do_liquidate(pos, ts, mark, cash, params,
                                  open_perps, perp_marks, liquidations,
                                  closed_pnl, survived,
                                  reason=f"iso MM breach (avail {available:.2f} < req {required:.2f})")
        return cash
    # CROSS: compute pool with this position at adverse mark, others at last marks
    test_marks = dict(perp_marks)
    test_marks[pos.id] = mark
    pool = cross_margin_pool(cash, open_perps, test_marks, spot_legs, spot_marks,
                              params.spot_haircut)
    required = required_maintenance_margin(open_perps, test_marks, params.mm_pct)
    if pool < required:
        return _do_liquidate(pos, ts, mark, cash, params,
                              open_perps, perp_marks, liquidations,
                              closed_pnl, survived,
                              reason=f"cross pool breach (pool {pool:.2f} < req {required:.2f})")
    return cash


def _liquidate_breaches(
    ts: datetime,
    open_perps: list[PerpPosition],
    perp_marks: dict[str, float],
    spot_legs: list[SpotLeg],
    spot_marks: dict[str, float],
    cash: float,
    params: SimParams,
    liquidations: list[LiquidationEvent],
    closed_pnl: dict[str, float],
    survived: dict[str, bool],
    use_adverse: bool,
) -> float:
    """Sweep all open perps at last-known marks and liquidate any that breach."""
    for pos in list(open_perps):
        cash = _liquidate_one_if_breached(
            pos=pos, ts=ts,
            mark=perp_marks.get(pos.id, pos.entry_price),
            open_perps=open_perps, perp_marks=perp_marks,
            spot_legs=spot_legs, spot_marks=spot_marks,
            cash=cash, params=params, liquidations=liquidations,
            closed_pnl=closed_pnl, survived=survived,
        )
    return cash


def _do_liquidate(
    pos: PerpPosition,
    ts: datetime,
    mark: float,
    cash: float,
    params: SimParams,
    open_perps: list[PerpPosition],
    perp_marks: dict[str, float],
    liquidations: list[LiquidationEvent],
    closed_pnl: dict[str, float],
    survived: dict[str, bool],
    reason: str,
) -> float:
    """Realize the loss: debit cash by (initial_margin + adverse_pnl + fee).
    Marks position closed and records the event."""
    pnl = perp_unrealized_pnl(pos, mark)   # negative for adverse
    fee = abs(pos.qty) * mark * params.liquidation_fee_pct
    # Cash flow:
    # - The position's initial_margin was already "in" the account at entry
    #   (cross) or set aside (isolated). For cross, we model cash as the total
    #   USDT pool, so initial_margin is already in cash.
    # - On liquidation we realize pnl into cash (negative for adverse) and
    #   debit the liquidation fee.
    cash += pnl - fee
    closed_pnl[pos.id] = pnl - fee
    survived[pos.id] = False
    open_perps.remove(pos)
    perp_marks[pos.id] = mark
    liquidations.append(LiquidationEvent(
        position_id=pos.id, liq_time=ts, liq_price=mark,
        margin_lost=abs(pnl) + fee, fee=fee, reason=reason,
    ))
    return cash


# Funding-only ts liquidation helper (used by funding step). Implemented as
# `_liquidate_breaches` with use_adverse=False — adverse marks aren't relevant
# at a settlement-only event since no new bar arrived. Kept inline above.
# The placeholder helper below is unused; left as documentation.
def _check_and_liquidate(*args, **kwargs):  # pragma: no cover
    """Placeholder — see inline call to _liquidate_breaches in funding branch."""
    return None

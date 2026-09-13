"""S-107 SQUEEZE_BULL — sleeve dispatcher.

Long BTC perp after a forced-deleveraging flush, in a bull regime only:
open interest drops >= 2% over 4 hours while price drops >= 0.5%, and the
causal 30-day return is above +10%. Fixed 2% stop, 3% target, 48h time stop.

Mechanism (why this is a trade and not a calendar): a 2% fall in open
interest inside four hours is forced closure, not repositioning — long
liquidations cascading through the book. The flush overshoots because
liquidation engines are price-insensitive, and in an uptrend the bid returns
once the forced supply is exhausted. The bull gate is doing the work of
distinguishing "forced sellers in an uptrend" from "the trend is over": the
ungated pool has a profit factor of exactly 1.00 over 423 fires, the
bull-gated pool 1.74.

Provenance: frozen rule from studies/notebooks/oi_flush/ (June 2026),
re-validated out-of-sample in studies/notebooks/squeeze_bull_revalidation/
(2026-09-08, verdict BUILD on n=10 with every margin one observation wide).
Read docs/calibration/squeeze_bull.md before trusting any number here.

Tick model:
  - The bot ticks every 60s. On every tick: sweep open positions against the
    live price for stop / target / time-stop.
  - Entry is evaluated only once per closed hourly bar, because every input
    is hourly. A second tick inside the same hour is a no-op.

Look-ahead: the regime uses `REGIME_SHIFT_DAYS = 1`, so a fire reads a daily
close at least three hours old. The June construction read the current day's
close and is not implementable; see config.py.

Exit policy (2026-09-12): the `use_stop` keyword (default True) is the one
thing the bot's two paper variants differ on. The shipped variant keeps the
-2% stop; the no-stop variant exits on the +3% target or the 48h time stop
only (studies/notebooks/sizing_style_2026_09/, policy P1b). The 2% distance
stays in the trade notes as `_reference_stop_price` for both, so sizing and
R accounting are identical and only the exit differs.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from strategies.support import clock, db
from strategies.support.dispatch import Intent

from . import math as sb_math
from .config import (
    ASSET, PAPER_COST_BP_RT, PAPER_SLIPPAGE_BP_RT, SLEEVE_NAME, TIF_HOURS,
)

log = logging.getLogger("p300.squeeze_bull")

# Last hour we evaluated, per variant, so a 60s tick loop evaluates the
# signal once per closed hourly bar rather than sixty times.
_last_eval_hour: dict[str, datetime] = {}


# ─── Loaders (read-only) ─────────────────────────────────────────────────

def _con():
    return sqlite3.connect(f"file:{db.PROD_DB}?mode=ro", uri=True)


def _load_hourly(now: datetime, lookback_days: int = 45) -> list[dict]:
    """Closed hourly bars joined on timestamp, oldest first.

    The inner join on `timestamp` is the research loader's join: a bar exists
    only when BOTH open interest and price exist for that hour. `now` bounds
    the frame to bars that have closed, so the forming hour is never read.
    """
    end = int(now.replace(minute=0, second=0, microsecond=0).timestamp())
    start = end - lookback_days * 86400
    con = _con()
    try:
        rows = con.execute("""
            SELECT p.timestamp, p.open, p.high, p.low, p.close, o.oi_close
            FROM cd_futures_ohlcv p
            JOIN cd_open_interest o ON o.timestamp = p.timestamp
            WHERE p.timestamp >= ? AND p.timestamp < ?
            ORDER BY p.timestamp
        """, (start, end)).fetchall()
    finally:
        con.close()
    return [{"ts": int(r[0]), "open": r[1], "high": r[2], "low": r[3],
             "close": r[4], "oi_close": r[5]} for r in rows
            if r[4] is not None and r[5] is not None]


def _daily_closes(bars: list[dict]) -> dict:
    """date -> last hourly close of that UTC day. Equivalent to the research
    loader's `df['close'].resample('1D').last()`."""
    out = {}
    for b in bars:
        out[datetime.fromtimestamp(b["ts"], tz=timezone.utc).date()] = b["close"]
    return out


# ─── Exits ───────────────────────────────────────────────────────────────

def _close_paper(trade_id: str, exit_price: float, reason: str) -> None:
    from strategies.trades import close_perp_trade
    close_perp_trade(trade_id, exit_price, reason, sleeve_name=SLEEVE_NAME,
                     cost_bp_rt=PAPER_COST_BP_RT,
                     slippage_bp_rt=PAPER_SLIPPAGE_BP_RT,
                     apply_funding=True)


def _open_trades(variant_id: str) -> list[dict]:
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("""
            SELECT id, asset, direction, entry_price, entry_time, notes
            FROM trades
            WHERE strategy_variant = ? AND strategy = ?
              AND status = 'open' AND execution_mode = 'paper'
        """, (variant_id, SLEEVE_NAME)).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def _sweep_open_positions(variant_id: str) -> int:
    """Close any open position whose stop, target or time stop has triggered
    against the live price. Runs on every tick, before any entry logic."""
    from strategies.support import price_feed

    open_rows = _open_trades(variant_id)
    if not open_rows:
        return 0
    closed = 0
    now = clock.now_utc()
    for tr in open_rows:
        try:
            blob = json.loads(tr["notes"]) if tr["notes"] else {}
        except Exception:  # noqa: BLE001
            blob = {}
        stop, target = blob.get("_stop_price"), blob.get("_target_price")
        tstop = blob.get("_time_stop_iso")
        price = price_feed.get_current_price(tr["asset"])
        if price is None:
            log.warning(f"[squeeze_bull {variant_id}] no price for {tr['asset']}; "
                        f"cannot sweep {tr['id']}")
            continue
        # Stop before target, matching the research replay.
        if stop is not None and price <= float(stop):
            _close_paper(tr["id"], price, "stop_loss")
            log.info(f"[squeeze_bull {variant_id}] STOP {tr['id']} @ {price:.2f}")
            closed += 1
        elif target is not None and price >= float(target):
            _close_paper(tr["id"], price, "take_profit")
            log.info(f"[squeeze_bull {variant_id}] TARGET {tr['id']} @ {price:.2f}")
            closed += 1
        elif tstop and now >= datetime.fromisoformat(tstop):
            _close_paper(tr["id"], price, "time_stop")
            log.info(f"[squeeze_bull {variant_id}] TIME STOP {tr['id']} @ {price:.2f}")
            closed += 1
    return closed


# ─── Signal ──────────────────────────────────────────────────────────────

def evaluate(bars: list[dict]) -> tuple[bool, dict]:
    """Evaluate the newest closed bar. Returns (fires, diag).

    Order matters and matches the research: flush -> cooldown (over flush
    EVENTS, regardless of regime) -> regime gate.
    """
    if len(bars) < 5:
        return False, {"status": "insufficient_bars", "n_bars": len(bars)}

    i = len(bars) - 1
    bar = bars[i]
    closes = [b["close"] for b in bars]
    ois = [b["oi_close"] for b in bars]

    oi_chg = sb_math.pct_change_n(ois, 4, i)
    px_chg = sb_math.pct_change_n(closes, 4, i)
    diag = {"bar_ts": bar["ts"],
            "bar_iso": datetime.fromtimestamp(bar["ts"], tz=timezone.utc).isoformat(),
            "close": bar["close"], "oi_chg_4h": oi_chg, "px_chg_4h": px_chg}

    if not sb_math.is_flush(oi_chg, px_chg):
        return False, {**diag, "status": "no_flush"}

    # The cooldown runs over flush events, so an untraded bear-regime flush
    # still silences this bar. Recomputed from the window every time, which
    # makes it restart-safe: no in-process state decides whether we fire.
    if i not in set(sb_math.kept_flush_indices(ois, closes)):
        prev = [k for k in sb_math.kept_flush_indices(ois, closes) if k < i]
        if prev:
            diag["hours_since_kept_flush"] = i - prev[-1]
        return False, {**diag, "status": "cooldown"}

    bar_date = datetime.fromtimestamp(bar["ts"], tz=timezone.utc).date()
    ret30 = sb_math.backward_only_ret_30d(_daily_closes(bars), bar_date)
    regime = sb_math.classify_regime(ret30)
    diag.update(ret_30d_backonly=ret30, regime=regime)
    if regime != "bull_30d":
        return False, {**diag, "status": "regime_not_bull"}

    return True, {**diag, "status": "fires"}


# ─── Dispatch contract ───────────────────────────────────────────────────

def decide(variant: dict, *, weight_pct: float = 0.0, leverage: float = 1.0,
           priority: float = 100.0, use_stop: bool = True):
    """Decide whether to open. Returns (list[Intent], status_dict).

    Side effect on every call: sweep open positions for stop / target /
    time-stop. Entry is evaluated at most once per closed hourly bar.

    ``use_stop`` selects the exit policy and is what separates the two live
    paper variants — `bot_squeeze_bull_v1` (-2 % stop) from
    `bot_squeeze_bull_nostop_v1` (target + 48 h only). They run on the same
    signals in one process with a pre-registered paired re-cut at n = 20 / 30,
    so this flag must stay per-variant and explicit.
    """
    variant_id = variant["id"]
    swept = _sweep_open_positions(variant_id)
    now = clock.now_utc()
    hour = now.replace(minute=0, second=0, microsecond=0)

    if _last_eval_hour.get(variant_id) == hour:
        return [], {"status": "already_evaluated_this_hour", "swept": swept}

    bars = _load_hourly(now)
    if not bars:
        return [], {"status": "no_bars", "swept": swept}
    _last_eval_hour[variant_id] = hour

    if _open_trades(variant_id):
        return [], {"status": "position_open", "swept": swept}

    fires, diag = evaluate(bars)
    if not fires:
        return [], {**diag, "swept": swept}

    entry_price = float(diag["close"])
    stop, target, risk = sb_math.bracket(entry_price)
    if risk <= 0:
        return [], {"status": "invalid_risk", "swept": swept}
    # The time stop is TIF_HOURS after ENTRY, and entry is the trigger bar's
    # CLOSE — one hour after its `bar_ts` open. Until 2026-09-13 this added
    # TIF_HOURS to bar_ts itself, so every live hold was 47h while the research
    # walker (math.replay_bracket) and the re-cut replay (recut_lib) held 48h,
    # and the pre-registered live-vs-replay divergence rule compared two
    # different exit times on every time-stop trade. BACKLOG 12a.
    entry_dt = datetime.fromtimestamp(diag["bar_ts"], tz=timezone.utc) + \
        timedelta(hours=1)
    time_stop_dt = entry_dt + timedelta(hours=TIF_HOURS)

    reason = {
        "trigger": "squeeze_bull_oi_flush",
        "variant_id": variant_id,
        "sleeve": SLEEVE_NAME,
        "bar_ts": diag["bar_ts"],
        "bar_iso": diag["bar_iso"],
        "oi_chg_4h": diag["oi_chg_4h"],
        "px_chg_4h": diag["px_chg_4h"],
        "ret_30d_backonly": diag["ret_30d_backonly"],
        "regime": diag["regime"],
        "exit_policy": "stop_target_time" if use_stop else "target_time",
        "_stop_price": stop if use_stop else None,
        "_reference_stop_price": stop,
        "_target_price": target,
        "_time_stop_iso": time_stop_dt.isoformat(),
        "_entry_price": entry_price,
    }
    intent = Intent(
        asset=ASSET, direction="LONG",
        allocation_pct=float(weight_pct),
        leverage=float(leverage),
        conviction=100,
        priority=float(priority),
        reason=reason, scheduled_exit_dt=time_stop_dt,
    )
    # diag first: a trailing **diag would overwrite "status" with "fires".
    return [intent], {**diag, "status": "decided", "swept": swept}


def execute(variant: dict, intent: Intent) -> dict:
    """Phase 2: open the LONG described by `intent`."""
    from strategies.trades import open_paper_trade
    reason = dict(intent.reason or {})
    entry_price = float(reason.pop("_entry_price"))
    tid = open_paper_trade(
        variant=variant, sleeve_name=SLEEVE_NAME,
        asset=intent.asset, direction="LONG",
        entry_price=entry_price,
        allocation_pct=intent.allocation_pct, leverage=intent.leverage,
        reason=reason,
        scheduled_exit_dt=intent.scheduled_exit_dt,
        regime_value=reason.get("regime", "bull_30d"),
        # Epoch seconds of the trigger bar, as a string. Guarded: an absent
        # bar_ts would otherwise key every open as the literal "None", and
        # the partial UNIQUE index would then block this variant's SECOND
        # open forever. None falls back to the fill instant instead.
        signal_time_iso=(str(reason["bar_ts"])
                         if reason.get("bar_ts") is not None else None),
    )
    stop_txt = (f"{reason['_stop_price']:.2f}" if reason.get("_stop_price") is not None
                else "none")
    log.info(f"[squeeze_bull {variant['id']}] opened {tid} BTC LONG @ "
             f"{entry_price:.2f}  stop={stop_txt}  "
             f"target={reason['_target_price']:.2f}  "
             f"alloc={intent.allocation_pct}%  k={intent.leverage}x")
    return {"status": "opened", "trade_id": tid, "entry_price": entry_price,
            "stop_price": reason["_stop_price"],
            "target_price": reason["_target_price"]}

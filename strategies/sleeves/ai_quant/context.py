"""Daily context bundle for the AI_QUANT sleeve's decision call.

`build_context(variant_id)` reads from trader.db / dashboard.db / the
cached JSON files written by sentiment_index_service / fed_funds_service /
polymarket_service and returns a single JSON-serializable dict the LLM
will see. Pure read-only and replay-safe — every clock read goes through
`strategies.support.clock.now_utc()`.

Each section is wrapped so a missing or stale data source produces
``{"error": "..."}`` rather than blowing up the whole bundle. The sleeve
must keep working even when one feed is briefly broken (e.g. Polymarket
returns 502, F&G hasn't been refreshed yet) — the LLM gets a smaller
bundle and can flag the gap in its rationale.
"""
from __future__ import annotations

import logging
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Callable

from data.sources import (
    coindesk as coindesk_fetcher,
    fed_funds as fed_funds_service,
    news as news_fetcher,
    polymarket as polymarket_service,
    sentiment as sentiment_index_service,
)
from strategies.support import clock, db, price_feed
from . import cvd as ai_cvd
from strategies.support.indicators import adx, ema

log = logging.getLogger("p300.ai_quant.context")

# Number of daily candles loaded for indicator/return computations.
_DAILY_LOOKBACK_DAYS = 220
# News window the LLM gets handed in the prompt.
_NEWS_WINDOW_HOURS = 30
_NEWS_LIMIT = 25


def _safe(name: str, fn: Callable[[], dict]) -> dict:
    """Wrap a section so a single source's failure doesn't sink the bundle."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — we genuinely want to swallow anything
        log.warning(f"context section {name!r} failed: {e}")
        return {"error": f"{type(e).__name__}: {e}"}


def _safe_live_price(asset: str) -> float | None:
    """price_feed.get_current_price wrapped so an unseeded test DB (no
    btc_1m table) doesn't bubble an OperationalError up through every
    section that reports live price."""
    try:
        return price_feed.get_current_price(asset)
    except sqlite3.OperationalError:
        return None


# ─── Daily candle loader ─────────────────────────────────────────────────────

def _load_btc_daily_candles(n_days: int = _DAILY_LOOKBACK_DAYS) -> list[dict]:
    """BTC daily candles aggregated from cd_spot_binance 1h, newest last.

    Drops the still-forming bucket so all returned candles are CLOSED
    (matches the convention used by the ADX sleeve)."""
    upper = clock.now_ts()
    since = upper - n_days * 86400
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        rows = con.execute(
            "SELECT timestamp, open, high, low, close FROM cd_spot_binance "
            "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (since, upper),
        ).fetchall()
    finally:
        con.close()
    days: dict[str, list] = defaultdict(list)
    for ts, o, h, l, c in rows:
        if o is None or c is None or o <= 0 or c <= 0:
            continue
        d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        days[d].append((ts, o, h, l, c))
    today = clock.now_utc().strftime("%Y-%m-%d")
    out: list[dict] = []
    for d in sorted(days):
        if d == today:
            continue
        bars = days[d]
        out.append({
            "ts": bars[0][0], "dt": d,
            "open": bars[0][1],
            "high": max(b[2] for b in bars),
            "low": min(b[3] for b in bars),
            "close": bars[-1][4],
        })
    return out


# ─── Section: market ─────────────────────────────────────────────────────────

def _market_section(asset: str) -> dict:
    candles = _load_btc_daily_candles()
    if len(candles) < 30:
        return {"error": f"only {len(candles)} daily candles available — need ≥30"}
    closes = [c["close"] for c in candles]
    last_close = closes[-1]
    last_dt = candles[-1]["dt"]

    def _pct_change(n: int) -> float | None:
        if len(closes) <= n:
            return None
        prev = closes[-1 - n]
        return round((last_close / prev - 1.0) * 100.0, 2) if prev > 0 else None

    log_returns = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            log_returns.append(math.log(closes[i] / closes[i - 1]))
    last_30 = log_returns[-30:] if len(log_returns) >= 30 else log_returns
    if last_30:
        mean = sum(last_30) / len(last_30)
        var = sum((r - mean) ** 2 for r in last_30) / len(last_30)
        realized_vol_30d_ann = round(math.sqrt(var) * math.sqrt(365) * 100.0, 1)
    else:
        realized_vol_30d_ann = None

    ema50 = ema(closes, 50)
    ema150 = ema(closes, 150)
    adx14 = adx(candles, 14)

    last_ema50 = ema50[-1] if not math.isnan(ema50[-1]) else None
    last_ema150 = ema150[-1] if not math.isnan(ema150[-1]) else None
    last_adx = adx14[-1] if not math.isnan(adx14[-1]) else None

    live_price = _safe_live_price(asset)

    return {
        "live_price": live_price,
        "last_daily_close": round(last_close, 2),
        "last_daily_close_date": last_dt,
        "pct_change_1d": _pct_change(1),
        "pct_change_7d": _pct_change(7),
        "pct_change_30d": _pct_change(30),
        "realized_vol_30d_pct_annualized": realized_vol_30d_ann,
        "ema50": round(last_ema50, 2) if last_ema50 is not None else None,
        "ema150": round(last_ema150, 2) if last_ema150 is not None else None,
        "close_vs_ema50_pct": round((last_close / last_ema50 - 1) * 100, 2)
            if last_ema50 else None,
        "close_vs_ema150_pct": round((last_close / last_ema150 - 1) * 100, 2)
            if last_ema150 else None,
        "adx14": round(last_adx, 1) if last_adx is not None else None,
    }


# ─── Section: funding ────────────────────────────────────────────────────────

def _funding_section(asset: str) -> dict:
    table = "cd_funding_rate" if asset.upper() == "BTC" else "cd_funding_rate_eth"
    upper = clock.now_ts()
    since = upper - 8 * 86400  # 8d covers ~24 8h-settlements
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        try:
            rows = con.execute(
                f"SELECT timestamp, fr_close FROM {table} "
                "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
                (since, upper),
            ).fetchall()
        except sqlite3.OperationalError as e:
            return {"error": f"funding table {table} missing or unreadable: {e}"}
    finally:
        con.close()
    if not rows:
        return {"error": f"no funding rows in {table} within 8d"}
    rates = [float(r[1]) for r in rows if r[1] is not None]
    last_ts = rows[-1][0]
    last_rate = rates[-1]
    last_24 = rates[-3:]  # 8h x 3 = 24h
    last_7d = rates[-21:]  # 8h x 21 = 7d
    flips_7d = sum(1 for i in range(1, len(last_7d))
                   if (last_7d[i] >= 0) != (last_7d[i - 1] >= 0))
    return {
        "latest_8h_rate_pct": round(last_rate * 100, 4),
        "latest_settlement_utc": datetime.fromtimestamp(
            last_ts, tz=timezone.utc).isoformat(),
        "mean_24h_rate_pct": round(sum(last_24) / len(last_24) * 100, 4)
            if last_24 else None,
        "mean_7d_rate_pct": round(sum(last_7d) / len(last_7d) * 100, 4)
            if last_7d else None,
        "n_sign_flips_7d": flips_7d,
    }


# ─── Section: long-short ratio ──────────────────────────────────────────────

def _lsr_section(asset: str) -> dict:
    upper = clock.now_ts()
    since = upper - 14 * 86400
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        rows = con.execute(
            "SELECT timestamp, ratio FROM ca_long_short_ratio "
            "WHERE asset = ? AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (asset.upper(), since, upper),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return {"error": "no L/S ratio rows in last 14d"}
    vals = [float(r[1]) for r in rows if r[1] is not None]
    last_ts = rows[-1][0]
    return {
        "latest_ratio": round(vals[-1], 3),
        "latest_date_utc": datetime.fromtimestamp(
            last_ts, tz=timezone.utc).strftime("%Y-%m-%d"),
        "mean_7d": round(sum(vals[-7:]) / min(7, len(vals)), 3),
        "mean_14d": round(sum(vals) / len(vals), 3),
    }


# ─── Section: calendar ──────────────────────────────────────────────────────

_EVENT_TYPES = ("FOMC", "CPI", "NFP", "OPEX_MONTHLY", "OPEX_QUARTERLY")


def _calendar_section() -> dict:
    today = clock.now_utc().date().isoformat()
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        rows = con.execute(
            "SELECT date, event_type, description FROM scheduled_events "
            "WHERE date >= ? AND event_type IN (?,?,?,?,?) "
            "ORDER BY date ASC LIMIT 12",
            (today, *_EVENT_TYPES),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return {"error": "no upcoming scheduled events"}
    today_dt = datetime.fromisoformat(today).date()
    events = []
    next_by_type: dict[str, dict] = {}
    for d, etype, desc in rows:
        try:
            days_to = (datetime.fromisoformat(d).date() - today_dt).days
        except ValueError:
            continue
        e = {"date": d, "type": etype, "days_to": days_to, "description": desc}
        events.append(e)
        if etype not in next_by_type:
            next_by_type[etype] = e
    return {
        "next_by_type": next_by_type,
        "upcoming_12": events,
    }


# ─── Section: sentiment (Fear & Greed) ──────────────────────────────────────

def _sentiment_section() -> dict:
    latest = sentiment_index_service.get_latest()
    if not latest:
        return {"error": "fear_greed cache empty — run sentiment_index_service.refresh()"}
    latest_date, latest_value = latest
    today_dt = clock.now_utc().date()
    days_ago_7 = (today_dt - timedelta(days=7)).isoformat()
    days_ago_30 = (today_dt - timedelta(days=30)).isoformat()
    val_7d = sentiment_index_service.get_value(days_ago_7)
    val_30d = sentiment_index_service.get_value(days_ago_30)
    return {
        "latest_value": latest_value,
        "latest_date": latest_date,
        "latest_bucket": sentiment_index_service.bucket(latest_value),
        "value_7d_ago": val_7d,
        "value_30d_ago": val_30d,
        "delta_7d": (latest_value - val_7d) if val_7d is not None else None,
    }


# ─── Section: macro (Fed funds + Polymarket) ───────────────────────────────

def _macro_section() -> dict:
    today = clock.now_utc().date().isoformat()
    out: dict = {}
    try:
        out["fed_target_upper_pct"] = fed_funds_service.get_target_rate(today)
    except Exception as e:  # noqa: BLE001
        out["fed_target_upper_pct"] = None
        out["fed_target_error"] = str(e)
    try:
        out["fed_phase"] = fed_funds_service.classify_phase(today)
    except Exception as e:  # noqa: BLE001
        out["fed_phase"] = None
        out["fed_phase_error"] = str(e)
    try:
        out["polymarket_expected_cuts_2026"] = polymarket_service.expected_cuts_2026()
    except Exception as e:  # noqa: BLE001
        out["polymarket_expected_cuts_2026"] = None
        out["polymarket_error"] = str(e)
    return out


# ─── Section: news ──────────────────────────────────────────────────────────

def _news_section(asset: str) -> dict:
    headlines = news_fetcher.query(
        asset=asset, hours=_NEWS_WINDOW_HOURS, limit=_NEWS_LIMIT,
    )
    macro_headlines = news_fetcher.query(
        asset=None, hours=_NEWS_WINDOW_HOURS, limit=_NEWS_LIMIT,
    )
    # `query(asset=None)` returns everything including BTC-tagged; filter
    # macro to "no asset_tag" so it doesn't duplicate the asset list.
    macro_only = [h for h in macro_headlines if not h.get("asset_tag")]
    n_hot = sum(1 for h in headlines if h.get("importance"))

    def _slim(h: dict) -> dict:
        return {
            "ts_utc": datetime.fromtimestamp(
                h["published_utc"], tz=timezone.utc).isoformat(),
            "title": h["title"],
            "source": h["source"],
            "hot": bool(h["importance"]),
        }

    return {
        "asset_tagged": [_slim(h) for h in headlines],
        "macro_untagged": [_slim(h) for h in macro_only[:10]],
        "n_total": len(headlines) + len(macro_only),
        "n_hot": n_hot,
        "window_hours": _NEWS_WINDOW_HOURS,
    }


# ─── Section: open interest ─────────────────────────────────────────────────

def _open_interest_section(asset: str) -> dict:
    """Aggregated OI for the BTC perp from CoinDesk Data (Binance feed).
    Returns latest USD-quote OI plus 24h/7d % change and recent peak.
    Currently BTC-only; ETH would require a parallel cd_open_interest_eth
    table which we don't yet populate."""
    if asset.upper() != "BTC":
        return {"error": f"OI section: only BTC supported (got {asset})"}
    rows = coindesk_fetcher.latest_oi(hours_back=8 * 24)  # need 7d for delta
    if len(rows) < 2:
        return {"error": "no recent open-interest rows in cd_open_interest"}
    latest = rows[-1]
    latest_value = float(latest["oi_value_close"] or 0.0)

    def _pct_change(hours_ago: int) -> float | None:
        target_ts = latest["ts"] - hours_ago * 3600
        # Pick the row closest to target_ts
        prev = min(rows, key=lambda r: abs(r["ts"] - target_ts))
        prev_value = float(prev["oi_value_close"] or 0.0)
        if prev_value <= 0:
            return None
        return round((latest_value / prev_value - 1) * 100.0, 2)

    peak_7d = max((r["oi_value_close"] or 0.0) for r in rows)
    return {
        "latest_btc_perp_usd": round(latest_value, 0),
        "latest_oi_contracts": round(float(latest["oi_close"] or 0.0), 1),
        "as_of_utc": datetime.fromtimestamp(
            latest["ts"], tz=timezone.utc).isoformat(),
        "pct_change_24h": _pct_change(24),
        "pct_change_7d": _pct_change(24 * 7),
        "peak_7d_usd": round(peak_7d, 0),
        "distance_from_7d_peak_pct": round(
            (latest_value / peak_7d - 1) * 100.0, 2) if peak_7d > 0 else None,
    }


# ─── Section: CVD (Cumulative Volume Delta) ─────────────────────────────────

def _cvd_section(asset: str) -> dict:
    """Taker-buy minus taker-sell volume on the BTC perp from Binance klines.

    Surfaces the latest day's CVD with a 30d rolling z-score (so the LLM
    can see when today's net flow is unusual) plus 24h and 7d net sums.
    The buy-pressure ratio (0-100) makes the direction unambiguous: 50 is
    neutral, >50 means buyers were aggressors on net, <50 means sellers.
    """
    if asset.upper() != "BTC":
        return {"error": f"CVD section: only BTC supported (got {asset})"}
    return ai_cvd.cvd_summary()


# ─── Section: liquidations ──────────────────────────────────────────────────

def _liquidations_section(asset: str) -> dict:
    """Aggregated liquidations for the BTC perp from CoinDesk Data (Binance).
    Returns 24h totals (long vs short USD), 7d totals, ratio, and a
    spike flag computed against the 7d hourly median.
    Currently BTC-only."""
    if asset.upper() != "BTC":
        return {"error": f"liquidations section: only BTC supported (got {asset})"}
    rows = coindesk_fetcher.latest_liquidations(hours_back=8 * 24)  # 8d for trend
    if not rows:
        return {"error": "no recent liquidation rows in cd_liquidations"}
    now_ts = clock.now_ts()
    rows_24h = [r for r in rows if r["ts"] >= now_ts - 24 * 3600]
    rows_7d = [r for r in rows if r["ts"] >= now_ts - 7 * 24 * 3600]
    longs_24h = sum(r["long_quote_quantity"] for r in rows_24h)
    shorts_24h = sum(r["short_quote_quantity"] for r in rows_24h)
    longs_7d = sum(r["long_quote_quantity"] for r in rows_7d)
    shorts_7d = sum(r["short_quote_quantity"] for r in rows_7d)
    # Data-quality flag: CoinDesk's Binance liquidation feed sometimes
    # returns zero rows even when liquidations occurred (the upstream is
    # still gathering history). When every value in the 7d window is
    # zero, surface a flag so the LLM doesn't read "$0 in 24h" as
    # "absolute calm" when it actually means "data not yet flowing".
    all_zero_7d = (longs_7d == 0 and shorts_7d == 0 and rows_7d)
    # Spike flag: was the 24h total > 2× the trailing-7d hourly median × 24?
    if rows_7d:
        sorted_hourly = sorted(r["long_quote_quantity"] + r["short_quote_quantity"]
                                 for r in rows_7d)
        median_hourly = sorted_hourly[len(sorted_hourly) // 2]
        spike = (longs_24h + shorts_24h) > (2 * median_hourly * 24)
    else:
        spike = False
    # Biggest single-hour cluster in last 24h
    biggest_hour = (max(rows_24h,
                          key=lambda r: r["long_quote_quantity"] + r["short_quote_quantity"])
                     if rows_24h else None)
    out = {
        "longs_24h_usd": round(longs_24h, 0),
        "shorts_24h_usd": round(shorts_24h, 0),
        "ratio_long_short_24h": round(longs_24h / shorts_24h, 2)
            if shorts_24h > 0 else None,
        "longs_7d_usd": round(longs_7d, 0),
        "shorts_7d_usd": round(shorts_7d, 0),
        "spike_24h": bool(spike),
        "biggest_hour_24h": (
            {"ts_utc": datetime.fromtimestamp(biggest_hour["ts"],
                                                tz=timezone.utc).isoformat(),
             "long_usd": round(biggest_hour["long_quote_quantity"], 0),
             "short_usd": round(biggest_hour["short_quote_quantity"], 0)}
            if biggest_hour else None),
        "n_hours_with_data_24h": len(rows_24h),
    }
    if all_zero_7d:
        out["data_quality_warning"] = (
            "All values are zero across the 7d window. CoinDesk's Binance "
            "liquidation feed is sparse for newer data; treat this as "
            "missing data rather than 'no liquidations'."
        )
    return out


# ─── Section: DVOL (Deribit implied vol) ────────────────────────────────────

def _dvol_section(asset: str) -> dict:
    """Implied-volatility index for `asset` (BTC or ETH) from Deribit DVOL
    via CoinDesk Data. Daily resolution. Returns latest value + 7d delta
    + 30d range so the LLM can place today's IV in regime context.
    """
    asset_u = asset.upper()
    if asset_u not in ("BTC", "ETH"):
        return {"error": f"DVOL section: only BTC/ETH supported (got {asset})"}
    rows = coindesk_fetcher.latest_dvol(asset_u, days_back=30)
    if len(rows) < 2:
        return {"error": f"no recent DVOL rows for {asset_u} in cd_dvol"}
    latest = rows[-1]
    latest_close = float(latest["close"])
    closes = [float(r["close"]) for r in rows]
    # 7d-ago: try to find a row ~7 days back, else use the oldest in window
    target_ts = latest["ts"] - 7 * 86400
    seven_d_ago_row = min(rows, key=lambda r: abs(r["ts"] - target_ts))
    seven_d_close = float(seven_d_ago_row["close"])
    delta_7d = round(((latest_close / seven_d_close) - 1) * 100.0, 2) \
        if seven_d_close > 0 else None
    return {
        "latest": round(latest_close, 2),
        "latest_date_utc": datetime.fromtimestamp(
            latest["ts"], tz=timezone.utc).strftime("%Y-%m-%d"),
        "delta_7d_pct": delta_7d,
        "min_30d": round(min(closes), 2),
        "max_30d": round(max(closes), 2),
        "percentile_30d": (
            round(sum(1 for c in closes if c <= latest_close) / len(closes) * 100.0)
            if closes else None
        ),
    }


# ─── Section: portfolio ─────────────────────────────────────────────────────

def _portfolio_section(variant_id: str, asset: str) -> dict:
    # Local import to keep this module importable in test contexts that
    # don't seed dashboard.db.
    from strategies import trades

    own = trades.get_open_trades(variant_id, "AI_QUANT", asset)
    own_pos: dict | None = None
    if own:
        t = own[0]
        entry = float(t.get("avg_entry_price") or t.get("entry_price") or 0) or None
        entry_iso = t.get("actual_entry_time") or t.get("entry_time")
        try:
            entry_dt = datetime.fromisoformat(entry_iso) if entry_iso else None
        except ValueError:
            entry_dt = None
        age_hours = None
        if entry_dt is not None:
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=timezone.utc)
            age_hours = round(
                (clock.now_utc() - entry_dt).total_seconds() / 3600.0, 1
            )
        live_price = _safe_live_price(asset)
        live_pnl_pct = None
        if entry and live_price:
            from strategies.support.sleeves import live_pnl_pct as _live_pnl
            live_pnl_pct = round(_live_pnl(t["direction"], entry, live_price), 2)
        own_pos = {
            "trade_id": t["id"],
            "direction": t["direction"],
            "entry_price": entry,
            "entry_time_utc": entry_iso,
            "age_hours": age_hours,
            "leverage": float(t.get("leverage") or 1.0),
            "allocation_pct": float(t.get("allocation_pct") or 0.0),
            "live_pnl_pct": live_pnl_pct,
        }

    # Other open trades across all sleeves on the same variant — visibility
    # only, the LLM cannot override them.
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        others = con.execute(
            "SELECT id, strategy, asset, direction, leverage, allocation_pct, "
            "       actual_entry_time, entry_price "
            "FROM trades WHERE strategy_variant=? AND status='open' "
            "  AND strategy != 'AI_QUANT' "
            "ORDER BY actual_entry_time DESC",
            (variant_id,),
        ).fetchall()
    finally:
        con.close()
    other_summary = [
        {
            "id": r["id"],
            "sleeve": r["strategy"],
            "asset": r["asset"],
            "direction": r["direction"],
            "leverage": r["leverage"],
            "allocation_pct": r["allocation_pct"],
            "entry_time_utc": r["actual_entry_time"],
            "entry_price": r["entry_price"],
        }
        for r in others
    ]
    return {
        "ai_quant_open_position": own_pos,
        "other_open_positions": other_summary,
        "n_other_open": len(other_summary),
    }


# ─── Section: decision history (M1 — carryover commitments) ────────────────

def _decision_history_section(variant_id: str, asset: str) -> dict:
    """Surface the recent AI_QUANT decision history so today's call can
    evaluate the ``exit_conditions`` and ``time_horizon`` it committed
    to on prior open positions. Excludes ``rationale_md`` (anchoring
    risk — the model should re-derive the WHY from current data).

    For each retained decision, computes a ``status_now``:

    - ``open``              — the trade row for this decision is still open.
    - ``closed``            — the trade row is closed (any reason).
    - ``expired_horizon``   — decision_utc + time_horizon_days × 86400 < now
                              and there's no trade row to mark closed.
    - ``superseded``        — a later decision on a later UTC day (or same
                              day, later utc) overrode this one — keeps
                              the surfaced rows focused on what's still
                              relevant. The latest row is never marked
                              superseded.
    - ``deferred_active``   — ``decided=DEFER`` and ``defer_until_utc`` is
                              in the future.
    - ``deferred_expired``  — ``decided=DEFER`` and ``defer_until_utc`` is
                              in the past (re-fire window has passed
                              without a successor row).

    Window: last 7 days OR while any decision's
    ``decision_utc + time_horizon_days × 86400 >= now`` is still in play
    (so a 21-day horizon decision stays visible until day 22). Cap 7
    entries total; ERROR rows excluded.
    """
    from . import journal

    now_ts = int(clock.now_utc().timestamp())
    # Pull a wide window (30d) and let the horizon / cap filter narrow
    # it. 30d is enough to catch most realistic time horizons.
    raw = journal.get_recent_decisions(variant_id, days=30)
    raw = [r for r in raw if (r.get("decided") or "").upper() != "ERROR"
            and not (r.get("error"))]

    if not raw:
        return {"n_rows": 0, "rows": []}

    # Compute open trade IDs by AI_QUANT for this variant for quick
    # status-now lookup. We use the DB directly rather than reach into
    # trades.get_open_trades to keep this section read-only.
    open_trade_ids: set[str] = set()
    closed_trade_ids: set[str] = set()
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        rows = con.execute(
            "SELECT id, status FROM trades "
            "WHERE strategy_variant=? AND strategy='AI_QUANT'",
            (variant_id,),
        ).fetchall()
    finally:
        con.close()
    for tid, status in rows:
        if status == "open":
            open_trade_ids.add(tid)
        else:
            closed_trade_ids.add(tid)

    # Newest first from journal; tag supersession on the older rows.
    latest_decision_utc = raw[0]["decision_utc"]
    horizon_cutoff_secs = 7 * 86400

    out_rows: list[dict] = []
    for i, r in enumerate(raw):
        decided = (r.get("decided") or "").upper()
        horizon_days = int(r.get("time_horizon_days") or 0)
        decision_utc = int(r.get("decision_utc") or 0)
        horizon_expires_at = decision_utc + horizon_days * 86400
        age_secs = now_ts - decision_utc

        # Keep within 7d unless still within its declared horizon.
        if age_secs > horizon_cutoff_secs and horizon_expires_at < now_ts:
            continue

        # Trade-action format from journal.save_decision is
        # "opened:SJ-..." or "flipped:SJ-old->SJ-new" or "closed:SJ-..." or
        # "noop"/"held"/etc. Extract the latest trade id if present.
        trade_action = r.get("trade_action") or ""
        trade_id = None
        if ":" in trade_action:
            tail = trade_action.split(":", 1)[1]
            trade_id = tail.split("->")[-1].strip()

        if decided == "DEFER":
            defer_until = r.get("defer_until_utc")
            if defer_until is None:
                status_now = "deferred_expired"
            elif now_ts < int(defer_until):
                status_now = "deferred_active"
            else:
                status_now = "deferred_expired"
        elif trade_id and trade_id in open_trade_ids:
            status_now = "open"
        elif trade_id and trade_id in closed_trade_ids:
            status_now = "closed"
        elif decision_utc < latest_decision_utc and decided in ("LONG", "SHORT", "FLAT"):
            # A later decision exists and this one didn't open a trade —
            # the later one supersedes it.
            status_now = "superseded"
        elif horizon_expires_at < now_ts and horizon_days > 0:
            status_now = "expired_horizon"
        elif trade_id is None and decided in ("LONG", "SHORT", "FLAT"):
            # Decided but no trade — typically FLAT-on-flat (noop) or
            # skipped:no_price. Treat as superseded once a later row
            # exists, else closed (no live commitment).
            status_now = "closed"
        else:
            status_now = "open" if i == 0 else "superseded"

        out_rows.append({
            "date": r.get("decision_date"),
            "decision_utc": decision_utc,
            "decided": decided,
            "conviction": r.get("conviction"),
            "time_horizon_days": horizon_days,
            "trade_action": trade_action,
            "exit_conditions": r.get("exit_conditions"),
            "confidence_caveats": r.get("confidence_caveats"),
            "status_now": status_now,
        })
        if len(out_rows) >= 7:
            break

    return {"n_rows": len(out_rows), "rows": out_rows}


# ─── Section: data freshness ────────────────────────────────────────────────

def _freshness_section() -> dict:
    """Tell the LLM which inputs may be stale so it can downweight them."""
    now = clock.now_ts()

    def _staleness(ts: int | None) -> dict | None:
        if not ts:
            return None
        age_h = round((now - int(ts)) / 3600.0, 1)
        return {
            "as_of_utc": datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(),
            "age_hours": age_h,
        }

    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        latest = lambda tbl, col="timestamp": con.execute(
            f"SELECT MAX({col}) FROM {tbl}"
        ).fetchone()[0]
        out = {
            "btc_1h_spot": _staleness(latest("cd_spot_binance")),
            "btc_funding_8h": _staleness(latest("cd_funding_rate")),
            "lsr_btc": _staleness(latest("ca_long_short_ratio")),
        }
        # The cd_open_interest / cd_liquidations / cd_dvol / news_headlines
        # tables may not exist yet on legacy installs without the AI sleeve.
        # Each lookup is wrapped so a missing table doesn't sink the whole
        # freshness section.
        for label, table, col in [
            ("news_latest",          "news_headlines",   "fetched_utc"),
            ("open_interest_latest", "cd_open_interest", "timestamp"),
            ("liquidations_latest",  "cd_liquidations",  "timestamp"),
            ("dvol_btc_latest",      "cd_dvol",          "timestamp"),
        ]:
            try:
                if table == "cd_dvol":
                    ts = con.execute(
                        "SELECT MAX(timestamp) FROM cd_dvol WHERE asset='BTC'"
                    ).fetchone()[0]
                else:
                    ts = latest(table, col)
                out[label] = _staleness(ts)
            except sqlite3.OperationalError:
                out[label] = None
    finally:
        con.close()
    return out


# ─── Top-level ──────────────────────────────────────────────────────────────

def build_context(variant_id: str, asset: str = "BTC") -> dict:
    """Assemble the daily-decision context bundle.

    Each section is independently fault-tolerant: if a data source is
    missing or broken, that section becomes ``{"error": "..."}`` and the
    rest of the bundle still ships. Returns a single JSON-serializable
    dict; the caller dumps it into the LLM prompt and persists it to
    ``ai_quant_decisions.context_json``.
    """
    return {
        "as_of_utc": clock.now_iso(),
        "variant_id": variant_id,
        "asset": asset.upper(),
        "market": _safe("market", lambda: _market_section(asset)),
        "funding": _safe("funding", lambda: _funding_section(asset)),
        "lsr": _safe("lsr", lambda: _lsr_section(asset)),
        "open_interest": _safe("open_interest",
                                 lambda: _open_interest_section(asset)),
        "cvd": _safe("cvd", lambda: _cvd_section(asset)),
        "liquidations": _safe("liquidations",
                                lambda: _liquidations_section(asset)),
        "dvol": _safe("dvol", lambda: {
            "btc": _dvol_section("BTC"),
            "eth": _dvol_section("ETH"),
        }),
        "calendar": _safe("calendar", _calendar_section),
        "sentiment": _safe("sentiment", _sentiment_section),
        "macro": _safe("macro", _macro_section),
        "news": _safe("news", lambda: _news_section(asset)),
        "portfolio": _safe("portfolio",
                            lambda: _portfolio_section(variant_id, asset)),
        "decision_history": _safe("decision_history",
                                    lambda: _decision_history_section(
                                        variant_id, asset)),
        "data_freshness": _safe("data_freshness", _freshness_section),
    }

"""Daily context bundle for the AI_QUANT sleeve's decision call.

`build_context(variant_id)` reads from trader.db / dashboard.db / the
cached JSON files written by sentiment_index_service / fed_funds_service /
polymarket_service and returns a single JSON-serializable dict the LLM
will see. Pure read-only and replay-safe — every clock read goes through
`services.clock.now_utc()`.

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

from services import (
    clock,
    db,
    fed_funds_service,
    news_fetcher,
    polymarket_service,
    price_feed,
    sentiment_index_service,
)
from services.indicators import adx, ema

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
    (matches the convention used by adx_service)."""
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


# ─── Section: portfolio ─────────────────────────────────────────────────────

def _portfolio_section(variant_id: str, asset: str) -> dict:
    # Local import to keep this module importable in test contexts that
    # don't seed dashboard.db.
    from services import trades

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
            from services.sleeves import live_pnl_pct as _live_pnl
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
        # news_headlines may not exist yet on installs without the AI sleeve
        try:
            out["news_latest"] = _staleness(latest("news_headlines", "fetched_utc"))
        except sqlite3.OperationalError:
            out["news_latest"] = None
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
        "calendar": _safe("calendar", _calendar_section),
        "sentiment": _safe("sentiment", _sentiment_section),
        "macro": _safe("macro", _macro_section),
        "news": _safe("news", lambda: _news_section(asset)),
        "portfolio": _safe("portfolio",
                            lambda: _portfolio_section(variant_id, asset)),
        "data_freshness": _safe("data_freshness", _freshness_section),
    }

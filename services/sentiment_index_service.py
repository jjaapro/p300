"""Sentiment index — Crypto Fear & Greed (alternative.me).

Daily index, 0-100, classifies into Extreme Fear / Fear / Neutral / Greed /
Extreme Greed. Free public API at https://api.alternative.me/fng/, no auth.
History reaches back to 2018-02-01.

Empirical findings on F&G as a signal (2018-2026, 8y daily data):

  Standalone (buy F&G<25, sell F&G>55) is NEGATIVE: 9 signals over 8y,
    cumulative -22.8%. F&G is correlated with regime, not predictive.

  As an event-window FILTER, it has clear edge:
    F&G at FOMC -> T-10h..T+0.5h BTC return:
      Extreme Fear (0-25)   8/8   wins  (+2.40%)  <- best
      Fear (26-45)         13     events (+0.75%, 62%)
      Neutral (46-55)       9     events (+0.09%, 67%)
      Greed (56-75)        17     events (+1.27%, 82%)
      Extreme Greed (76+)   2/5   wins  (+1.18%)  <- worst

  This is the OPPOSITE of buy-and-hold contrarian intuition: at short
  event windows, fear bottoms get relief rallies, greed peaks dump.

The FOMC sleeve uses F&G as one of its skip/trade gates. Other sleeves
may consume it for sizing or veto.

Refresh cadence: once per day is sufficient (index updates daily 00:00 UTC).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parent.parent
JSON_PATH = REPO / "data" / "fear_greed.json"
API_URL = "https://api.alternative.me/fng/?limit=0&format=json"

log = logging.getLogger("p300.sentiment")


# ─── Fetch + cache ───────────────────────────────────────────────────────────

def refresh() -> bool:
    """Download full F&G history (~3000 days) and write to JSON_PATH.
    Cheap (<200KB). Idempotent. Returns True on success.
    No-op (returns False) in sim mode — sim must not hit the network."""
    from strategies.support import clock
    if clock.is_simulated():
        return False
    req = Request(API_URL, headers={"User-Agent": "p300/1.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            data = resp.read()
        # Validate parse before writing (avoids corrupted cache on bad responses)
        parsed = json.loads(data)
        if not isinstance(parsed.get("data"), list):
            log.warning("F&G response missing 'data' array")
            return False
        JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        JSON_PATH.write_bytes(data)
        invalidate_cache()
        return True
    except (URLError, OSError, json.JSONDecodeError) as e:
        log.warning(f"F&G refresh failed: {e}")
        return False


# ─── Read ────────────────────────────────────────────────────────────────────

_by_date_cache: dict[str, int] | None = None


def _by_date() -> dict[str, int]:
    """ISO date -> integer F&G value, 0-100. Loaded lazily, cached."""
    global _by_date_cache
    if _by_date_cache is None:
        if not JSON_PATH.exists():
            log.warning(f"{JSON_PATH} missing; call sentiment_index_service.refresh()")
            _by_date_cache = {}
        else:
            payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
            _by_date_cache = {
                datetime.fromtimestamp(int(r["timestamp"]),
                                        tz=timezone.utc).date().isoformat():
                int(r["value"])
                for r in payload.get("data", [])
            }
    return _by_date_cache


def invalidate_cache() -> None:
    global _by_date_cache
    _by_date_cache = None


def get_value(date_str: str) -> int | None:
    """F&G on `date_str` (YYYY-MM-DD). Returns None if not in cache."""
    return _by_date().get(date_str)


def get_latest() -> tuple[str, int] | None:
    """Most recent (date, value) tuple, or None if cache empty."""
    bd = _by_date()
    if not bd:
        return None
    d = max(bd.keys())
    return (d, bd[d])


# ─── Bucketing ───────────────────────────────────────────────────────────────

def bucket(value: int | None) -> str:
    """Map F&G value to one of the standard 5 buckets.

    Boundaries match alternative.me's own classification with one
    refinement: we treat value<=25 as Extreme Fear (vs their <=24) so the
    boundary aligns with the FOMC backtest's binning.
    """
    if value is None:
        return "unknown"
    if value <= 25:
        return "extreme_fear"
    if value <= 45:
        return "fear"
    if value <= 55:
        return "neutral"
    if value <= 75:
        return "greed"
    return "extreme_greed"

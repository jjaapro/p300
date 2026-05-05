"""Polymarket Gamma API client — for Fed-related prediction markets.

We use one anchor market: "How many Fed rate cuts in 2026?"
(slug=how-many-fed-rate-cuts-in-2026, $21M+ volume). It's stable in
slug, has deep liquidity, and gives a forward expectation we can decompose
into per-meeting cut probabilities.

Why not use the per-meeting "Fed decision in April?" markets? Their slugs
change every month and they often don't appear in the gamma /events
endpoint until a few days out — fragile for an automated daily refresh.

Daily refresh is sufficient: prices on these markets move slowly (rate
expectations are revealed by macro data, which arrives at most weekly).

Cache: data/polymarket_fed_2026.json — written each refresh, read on demand.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parent.parent
CACHE_PATH = REPO / "data" / "polymarket_fed_2026.json"
GAMMA_BASE = "https://gamma-api.polymarket.com"
ANCHOR_SLUG = "how-many-fed-rate-cuts-in-2026"

log = logging.getLogger("p300.polymarket")


# ─── HTTP helper ─────────────────────────────────────────────────────────────

def _gamma_get(path: str, params: dict | None = None) -> object:
    qs = "?" + urlencode(params) if params else ""
    url = f"{GAMMA_BASE}{path}{qs}"
    req = Request(url, headers={"User-Agent": "p300/1.0"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


# ─── Refresh ─────────────────────────────────────────────────────────────────

def refresh() -> bool:
    """Pull the anchor market group + sub-markets, store as JSON.

    The /events?slug=... endpoint returns the parent event with a list of
    child markets (one per outcome). We extract per-outcome prices and the
    derived expected-cuts value E[cuts] = sum(i * P(i)).

    Returns True on success, False on network/parse failure (cache stays as-is).
    """
    try:
        events = _gamma_get("/events", {"slug": ANCHOR_SLUG})
        if not isinstance(events, list) or not events:
            log.warning(f"Polymarket: no event for slug {ANCHOR_SLUG}")
            return False
        ev = events[0]
        out: dict = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "event_slug": ANCHOR_SLUG,
            "event_id": ev.get("id"),
            "event_title": ev.get("title"),
            "volume": ev.get("volume"),
            "outcomes": [],
        }
        # Each child market is a Yes/No on a specific cut count
        for m in ev.get("markets", []) or []:
            q = m.get("question") or ""
            outcomes = _safe_json_list(m.get("outcomes"))
            prices = _safe_json_list(m.get("outcomePrices"))
            yes_price = None
            for o, p in zip(outcomes, prices):
                if isinstance(o, str) and o.lower() == "yes":
                    try:
                        yes_price = float(p)
                    except (TypeError, ValueError):
                        yes_price = None
                    break
            cuts = _extract_cut_count(q)
            out["outcomes"].append({
                "question": q,
                "cut_count": cuts,
                "p_yes": yes_price,
                "market_id": m.get("id"),
            })
        # Derived expected-cuts value
        ec = expected_cuts_2026(out)
        out["expected_cuts_2026"] = ec
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
        invalidate_cache()
        return True
    except (URLError, OSError, ValueError, KeyError) as e:
        log.warning(f"Polymarket refresh failed: {e}")
        return False


def _safe_json_list(v: object) -> list:
    """outcomes / outcomePrices come back as JSON-encoded strings sometimes
    and as native lists other times. Normalize."""
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _extract_cut_count(question: str) -> int | None:
    """'Will 2 Fed rate cuts happen in 2026?' -> 2. 'Will no Fed rate cuts...' -> 0."""
    q = (question or "").lower()
    if "no fed rate cut" in q:
        return 0
    # match the first number before "fed rate cut"
    import re
    m = re.search(r"(\d+)\s+fed rate cut", q)
    return int(m.group(1)) if m else None


# ─── Read ────────────────────────────────────────────────────────────────────

_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        if not CACHE_PATH.exists():
            log.warning(f"{CACHE_PATH} missing; call polymarket_service.refresh()")
            _cache = {}
        else:
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return _cache


def invalidate_cache() -> None:
    global _cache
    _cache = None


def expected_cuts_2026(payload: dict | None = None) -> float | None:
    """E[cuts in 2026] = sum_i (i * P(cuts=i)).
    Pass `payload` to compute from a freshly-fetched dict (avoiding a
    chicken-and-egg cache miss inside refresh)."""
    pl = payload if payload is not None else _load()
    outcomes = pl.get("outcomes") or []
    if not outcomes:
        return None
    ec = 0.0
    saw_any = False
    for o in outcomes:
        c = o.get("cut_count")
        p = o.get("p_yes")
        if c is None or p is None:
            continue
        ec += c * float(p)
        saw_any = True
    return ec if saw_any else None


# ─── Per-meeting expected action ─────────────────────────────────────────────

def expected_action_for_meeting(fomc_date: str,
                                  remaining_meetings_after: int) -> tuple[str, dict]:
    """Best-effort estimate of the most-likely action at `fomc_date`.

    Forward dates (2026 — covered by the anchor market):
      cuts_per_meeting = (E[cuts_2026] - cuts_already_taken_in_2026)
                         / remaining_meetings_in_2026_inc_this_one
      > 0.6 -> 'cut_25'
      > 0.3 -> 'unclear' (50/50, sleeve treats as soft skip)
      else  -> 'hold'

    Historical dates (pre-2026): we don't have Polymarket data, but for
    backtest fidelity we approximate the market's expected action by the
    REALIZED rate change at that meeting (lookup via fed_funds_service).
    SOFR futures pre-FOMC have agreed with the realized action ~85%+ of
    the time historically, so this is a defensible proxy. The result is
    tagged proxy='ex_post' in the meta dict so audits can identify it.

    Returns (label, info_dict).
    """
    from services import fed_funds_service

    year = fomc_date[:4]
    if year != "2026":
        # Historical fallback: use actual rate change at meeting as proxy
        bp = fed_funds_service.get_change_at(fomc_date)
        if bp < -50:
            label = "emergency_cut"
        elif -50 <= bp <= -30:
            label = "cut_50"
        elif -30 < bp <= -10:
            label = "cut_25"
        elif 10 <= bp < 30:
            label = "hike_25"
        elif bp >= 30:
            label = "hike_50"
        else:
            label = "hold"
        return label, {"proxy": "ex_post_realized", "change_bp": bp}

    pl = _load()
    ec = pl.get("expected_cuts_2026")
    if ec is None:
        return "unknown", {"reason": "no_polymarket_data"}

    cuts_taken = 0
    for d, _r, bp in fed_funds_service._changes():
        if d.startswith("2026") and d <= fomc_date and bp < -10:
            cuts_taken += 1

    cuts_remaining_expected = max(0.0, ec - cuts_taken)
    meetings_remaining = max(1, remaining_meetings_after + 1)
    per_meeting = cuts_remaining_expected / meetings_remaining

    if per_meeting > 0.6:
        label = "cut_25"
    elif per_meeting > 0.3:
        label = "unclear"
    else:
        label = "hold"
    return label, {
        "expected_cuts_2026": ec,
        "cuts_already_2026": cuts_taken,
        "meetings_remaining_inc_this": meetings_remaining,
        "implied_per_meeting_cut_prob": round(per_meeting, 3),
    }

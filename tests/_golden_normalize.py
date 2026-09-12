"""Turn a decide()/execute() outcome into a comparable golden document.

The per-field policy is declared HERE, once, before any strip happens, so that
the refactor's intended deletions are expressible without quietly weakening
anything:

**PINNED-EXACT** — asset, direction, allocation_pct, leverage,
scheduled_exit_dt, every key and value of ``reason``, and every key of the
status dict. ``reason`` is the blob that lands in ``trades.notes`` and lives in
the ledger forever; the status dict's key SET is behaviour the dashboard reads
(its order is not).

**PINNED-OR-ABSENT** — ``conviction`` and ``priority`` only. Read with a
default, so a strip that deletes them passes and a strip that CHANGES them
fails. Both are provably unobservable on the bot path: no runner, no ledger
writer and ``open_paper_trade`` read either, and Intent's dataclass defaults
already equal the values every bot passes. Pinning them as must-exist would
force the refactor to rewrite the goldens, which is the exact failure this
exercise exists to avoid; pinning them as must-be-absent would fail today.

**NORMALIZED-AWAY** — the SJ-id and ``actual_entry_time``: mint-order and clock
artefacts. Their SHAPE is pinned (one row, id matching ``SJ-<digits>``), not
their value.

Floats are rounded to 10 significant digits. That is far tighter than any
behaviour change a refactor could introduce and loose enough to survive
platform FP noise.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

MISSING = "<absent>"
SJ_RE = re.compile(r"^SJ-\d+$")

# Keys whose values are clock/mint artefacts, replaced by a shape assertion.
_VOLATILE_TRADE_COLS = ("id", "actual_entry_time", "actual_exit_time",
                        "created_at", "unique_key", "order_ids")


def _round(v):
    # numpy scalars first: chento builds its status from a pandas frame, so
    # `inside_va` arrives as numpy.bool_ and the z-scores as numpy.float64.
    # Both compare equal to their Python twins but neither is JSON
    # serializable, and a golden that cannot round-trip through JSON is not a
    # golden. `.item()` is the documented way to get the Python scalar.
    if hasattr(v, "item") and hasattr(v, "dtype") and getattr(v, "ndim", 1) == 0:
        v = v.item()
    if isinstance(v, bool):
        return v                      # before the float branch: bool is an int
    if isinstance(v, float):
        return float(f"{v:.10g}")
    if isinstance(v, dict):
        return {k: _round(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_round(x) for x in v]
    return v


def normalize_intent(intent) -> dict:
    """PINNED-EXACT fields by name; conviction/priority via getattr so a strip
    that removes them degrades to a policy decision rather than an
    AttributeError."""
    out = {
        "asset": getattr(intent, "asset", MISSING),
        "direction": getattr(intent, "direction", MISSING),
        "allocation_pct": _round(getattr(intent, "allocation_pct", MISSING)),
        "leverage": _round(getattr(intent, "leverage", MISSING)),
        "conviction": getattr(intent, "conviction", 100),
        "priority": _round(getattr(intent, "priority", 100.0)),
        "reason": _round(dict(getattr(intent, "reason", None) or {})),
    }
    sched = getattr(intent, "scheduled_exit_dt", None)
    out["scheduled_exit_dt"] = sched.isoformat() if sched is not None else None
    return out


def normalize_status(status) -> dict:
    return _round(dict(status or {}))


def normalize_db(db_path) -> dict:
    """The ledger delta. decide() is not a pure function for any sleeve — it
    closes trades before it returns — so a golden that records only the return
    value is blind to half of what the sleeve did."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, strategy, strategy_variant, asset, direction, status, "
            "entry_price, exit_price, size_usdt, qty, leverage, "
            "allocation_pct, exit_time, pnl_usdt, notes, unique_key "
            "FROM trades ORDER BY id").fetchall()
    finally:
        con.close()

    trades = []
    for r in rows:
        d = dict(r)
        assert SJ_RE.match(d["id"]), f"unexpected trade id shape {d['id']!r}"
        notes = d.pop("notes")
        try:
            head, _, tail = (notes or "").partition("\n")
            d["notes"] = _round(json.loads(head)) if head else None
            d["notes_tail"] = tail.strip() or None
        except (ValueError, TypeError):
            d["notes"], d["notes_tail"] = None, (notes or "").strip() or None
        # unique_key embeds the signal stamp, which IS behaviour (it is what
        # stops a doubled process double-booking), but it also embeds the
        # variant id. Keep its suffix, drop the volatile id prefix.
        uk = d.pop("unique_key")
        d["unique_key_suffix"] = uk.split("|", 1)[1] if uk and "|" in uk else uk
        for c in _VOLATILE_TRADE_COLS:
            d.pop(c, None)
        trades.append(_round(d))
    return {"trades": trades, "n_trades": len(trades)}


def golden_document(*, intents, status, db_path) -> dict:
    return {
        "status": normalize_status(status),
        "intents": [normalize_intent(i) for i in (intents or [])],
        "db_after": normalize_db(db_path),
    }


def load_or_write(path: Path, doc: dict) -> dict:
    """Read the committed golden, or write it on first run.

    A missing golden is WRITTEN, not failed — that is how these are seeded.
    A golden that exists is never rewritten by the test: regenerating is a
    deliberate act with its own commit and its own justification, because a
    golden silently re-baselined after a behaviour change proves nothing.
    """
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    return doc

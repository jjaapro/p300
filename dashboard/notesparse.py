"""Parse trades.notes into structured plan/decision data. Pure, never raises.

The ledger convention (strategies/trades.py): `notes` starts with one line
of JSON written at open — the sleeve's reason blob — and close appends
plain-text lines after "\\n" (e.g. "CHENTO_TRIPLE_V3_EXIT: stop_hit; ...").

Reason-blob conventions across sleeves:
  - underscore keys are execution plan written by the sizing/exit layer
    (`_stop_price`, `_target_price`, `_time_stop_iso`, `_risk`, ...);
    `_state` is the sleeve's mutable position-walking state (redundant with
    the columns — dropped here); `_filter_diag` is the per-entry filter
    record.
  - non-underscore scalar keys are the sleeve's decision inputs (chento:
    okx_delta_z...; adx: adx/ema50/funding_z; carry: fr_7d_avg_pct;
    short_squeeze: perp_cvd_pct/divergence_pct/...). Captured generically so
    a new sleeve needs no parser change.
"""
from __future__ import annotations

import json

_PLAN_KEYS = {
    "_stop_price": "stop_price",
    "_target_price": "target_price",
    "_time_stop_iso": "time_stop",
    "_entry_price": "entry_price_planned",
    "_risk": "risk_price",
    "_atr_at_entry": "atr_at_entry",
    "_inside_va": "inside_va",
    "_ladder_size_frac": "ladder_size_frac",
}
_META_KEYS = {"trigger", "variant_id", "sleeve", "bar_ts"}
_DROP_KEYS = {"_state", "_filter_diag"}
_SCALARS = (str, int, float, bool)


def parse(notes: str | None) -> dict:
    """-> {plan, decision, exit_lines, error}; every field present, error is
    None unless the first line failed to parse as JSON."""
    out = {"plan": None, "decision": None, "exit_lines": [], "error": None}
    if not notes:
        return out
    first, _, rest = notes.partition("\n")
    out["exit_lines"] = [ln.strip() for ln in rest.split("\n") if ln.strip()]
    try:
        blob = json.loads(first)
        if not isinstance(blob, dict):
            raise ValueError(f"reason blob is {type(blob).__name__}, not dict")
    except (ValueError, TypeError) as e:
        out["error"] = f"{e} — raw: {first[:200]}"
        return out

    plan = {}
    other = {}
    inputs = {}
    for k, v in blob.items():
        if k in _DROP_KEYS:
            continue
        if k in _PLAN_KEYS:
            plan[_PLAN_KEYS[k]] = v
        elif k.startswith("_"):
            if v is None or isinstance(v, _SCALARS):
                other[k.lstrip("_")] = v
        elif k not in _META_KEYS:
            if v is None or isinstance(v, _SCALARS):
                inputs[k] = v
    if other:
        plan["other"] = other

    filters = blob.get("_filter_diag")
    out["plan"] = plan or None
    out["decision"] = {
        "trigger": blob.get("trigger"),
        "sleeve": blob.get("sleeve"),
        "bar_ts": blob.get("bar_ts"),
        "filters": filters if isinstance(filters, dict) else None,
        "inputs": inputs,
    }
    return out

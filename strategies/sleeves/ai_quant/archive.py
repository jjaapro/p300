"""Per-decision markdown archive for AI_QUANT.

Every successful call to journal.save_decision writes one .md file under
``data/ai_quant_archive/`` mirroring the row that just landed in
``ai_quant_decisions``. The DB row remains the source of truth — these
files are a human-browsable mirror so the operator can scan recent
decisions, spot-check rationale quality, and grep across history without
touching SQLite.

Layout: ``data/ai_quant_archive/{YYYY-MM-DD}_{variant}_{asset}_{decided}_id{row_id}.md``

Design notes:
- ``_archive_dir()`` resolves the path off ``db.DASH_DB.parent`` so tests
  that monkeypatch ``strategies.support.db.DASH_DB`` redirect the archive too — no
  separate fixture needed.
- ``write_archive_md`` is best-effort: any exception is logged and
  swallowed. The DB row is durable; the file is regenerable. See
  ``tools/ai_quant_archive_rebuild.py`` for backfill / re-render.
- Row ids appear in the filename so a same-day retry (ERROR row followed
  by a successful row on a later tick) doesn't collide.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from strategies.support import db

log = logging.getLogger("p300.ai_quant.archive")

ARCHIVE_DIRNAME = "ai_quant_archive"


def _archive_dir() -> Path:
    """``data/ai_quant_archive/`` next to ``dashboard.db``. Resolved
    dynamically so test monkeypatches of ``strategies.support.db.DASH_DB`` flow
    through here too."""
    return Path(db.DASH_DB).parent / ARCHIVE_DIRNAME


def _safe_token(s: str | None, fallback: str = "UNKNOWN") -> str:
    """Filename-safe single token. Strips path separators and whitespace,
    coerces to ASCII-ish, caps length."""
    if not s:
        return fallback
    out = "".join(ch for ch in str(s) if ch.isalnum() or ch in ("-", "_"))
    return (out or fallback)[:48]


def _filename_for(
    *, row_id: int, decision_date: str, variant_id: str,
    asset: str, decided: str,
) -> str:
    return (
        f"{_safe_token(decision_date, 'NA')}_"
        f"{_safe_token(variant_id, 'variant')}_"
        f"{_safe_token(asset.upper(), 'ASSET')}_"
        f"{_safe_token((decided or 'ERROR').upper(), 'ERROR')}_"
        f"id{int(row_id)}.md"
    )


def write_archive_md(*, row_id: int, row: dict[str, Any]) -> Path | None:
    """Render `row` as markdown and write it under the archive dir. Returns
    the path on success, ``None`` on failure (failures are logged, never
    raised — the journal's DB row stays durable either way)."""
    try:
        d = _archive_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / _filename_for(
            row_id=row_id,
            decision_date=row.get("decision_date") or "NA",
            variant_id=row.get("variant_id") or "variant",
            asset=row.get("asset") or "ASSET",
            decided=row.get("decided") or "ERROR",
        )
        path.write_text(_render_markdown(row), encoding="utf-8")
        return path
    except Exception:
        log.exception("AI_QUANT archive write failed for row %s", row_id)
        return None


# ─── Rendering ──────────────────────────────────────────────────────────────

def _coerce_int(v: Any) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _parse_json_list(raw: Any) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _format_decision_time(ts: Any) -> str | None:
    if ts is None:
        return None
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return None


def _render_markdown(row: dict[str, Any]) -> str:
    direction = (row.get("decided") or "ERROR").upper()
    conviction = row.get("conviction")
    conv_str = f"{conviction} / 100" if conviction is not None else "—"
    decision_date = row.get("decision_date") or "NA"
    asset = (row.get("asset") or "?").upper()

    if direction != "ERROR" and conviction is not None:
        title = (f"AI_QUANT decision — {decision_date} {asset} "
                 f"{direction} (conviction {conviction})")
    else:
        title = f"AI_QUANT decision — {decision_date} {asset} {direction}"

    in_t = _coerce_int(row.get("input_tokens"))
    out_t = _coerce_int(row.get("output_tokens"))
    cw_t = _coerce_int(row.get("cache_write_tokens"))
    cr_t = _coerce_int(row.get("cache_read_tokens"))
    cost = row.get("cost_usd") or 0.0
    decision_time = _format_decision_time(row.get("decision_utc"))

    drivers = _parse_json_list(row.get("key_drivers_json"))
    tool_calls = _parse_json_list(row.get("tool_calls_json"))

    parts: list[str] = [f"# {title}", ""]

    parts.append("| Field | Value |")
    parts.append("|---|---|")
    parts.append(f"| Decision date (UTC) | {decision_date} |")
    if decision_time:
        parts.append(f"| Decision time (UTC) | {decision_time} |")
    parts.append(f"| Variant | {row.get('variant_id') or '—'} |")
    parts.append(f"| Asset | {asset} |")
    parts.append(f"| **Direction** | **{direction}** |")
    parts.append(f"| **Conviction** | **{conv_str}** |")
    horizon = row.get("time_horizon_days")
    parts.append(
        f"| Time horizon | {horizon} days |" if horizon is not None
        else "| Time horizon | — |"
    )
    parts.append(f"| Trade action | `{row.get('trade_action') or '—'}` |")
    parts.append(f"| Model | {row.get('model_id') or '—'} |")
    parts.append(f"| Cost (USD) | ${float(cost):.4f} |")
    parts.append(f"| Turns | {row.get('turns') if row.get('turns') is not None else '—'} |")
    parts.append(
        f"| Tokens (in / out / cache-write / cache-read) | "
        f"{in_t} / {out_t} / {cw_t} / {cr_t} |"
    )
    parts.append(f"| Journal row id | {row.get('id', '?')} |")
    parts.append("")

    if drivers:
        parts.append("## Key drivers")
        for d in drivers:
            parts.append(f"- {d}")
        parts.append("")

    if row.get("exit_conditions"):
        parts.append("## Exit conditions")
        parts.append(str(row["exit_conditions"]).rstrip())
        parts.append("")

    if row.get("confidence_caveats"):
        parts.append("## Confidence caveats")
        parts.append(str(row["confidence_caveats"]).rstrip())
        parts.append("")

    if row.get("rationale_md"):
        parts.append("## Rationale")
        parts.append(str(row["rationale_md"]).rstrip())
        parts.append("")

    if tool_calls:
        parts.append("## Tool calls")
        for i, tc in enumerate(tool_calls, 1):
            name = tc.get("name", "<unknown>") if isinstance(tc, dict) else str(tc)
            inp = tc.get("input", {}) if isinstance(tc, dict) else {}
            try:
                inp_str = json.dumps(inp, default=str, ensure_ascii=False)
            except (TypeError, ValueError):
                inp_str = str(inp)
            if len(inp_str) > 400:
                inp_str = inp_str[:397] + "..."
            parts.append(f"{i}. `{name}` — `{inp_str}`")
        parts.append("")

    if row.get("error"):
        parts.append("## Error")
        parts.append("```")
        parts.append(str(row["error"]).rstrip())
        parts.append("```")
        parts.append("")

    return "\n".join(parts)

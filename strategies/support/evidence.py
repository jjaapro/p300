"""Results warnings for the paper fleet — DISPLAY ONLY (BACKLOG item 16).

Two warnings per live variant, from its closed trades in prod.db:

  LOSING_MONEY (red)      cumulative net pnl_usdt over every closed trade of
                          the variant is below zero, at any n (n=1 counts).
                          Clears when the total is back at or above zero.
  BELOW_RESEARCH (amber)  the mean result per trade since the variant's
                          `comparable_from` is below the 10th percentile of
                          the means of n trades resampled with replacement
                          from its research values. Needs live n >= 5 and
                          research n >= 10; a smaller research sample is an
                          info line instead.

Nothing here disables, sizes or touches anything: the operator decides.
Disabling a variant is removing it from its bot config and restarting that
unit (docs/calibration/squeeze_bull.md), and the dashboard unit too: it keeps
the bot configs it imported, so it shows the variant until it restarts.
Research is in-sample, so an amber line is a prompt to look, not a verdict.

Scope is every variant a bot config trades — derived exactly as health.py's
check_bot_variants_registered does (health.BOT_CONFIGS; VARIANTS ids, else
VARIANT_ID) — so the legacy p300_* variant is out, and both squeeze variants
are judged separately (they trade the same signals with different exits).
A config that fails to import becomes a visible 'check unavailable' line.

Result unit: pnl_usdt / the config's CAPITAL_USDT * 100 (% of capital per
trade), the unit of bots/<bot>/research_baseline.json.

De-duplication: rows of one variant, strategy and direction whose entry falls
in the same UTC minute count once (lowest id kept). That is the signature of
the 2026-08-15..24 doubled fleet (SJ-4243/4244, 4245/4246, 4248/4249 on
bot_chento_v3_v1): two processes booked every signal twice.

Pure read side: no DB writes, no studies/ imports, no import-time work. The
loader takes an injected (read-only) connection. monitor.py and
dashboard/queries.py both call evaluate(), so their wording cannot drift.
"""
from __future__ import annotations

import functools
import hashlib
import importlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np

REPO = Path(__file__).resolve().parents[2]
BASELINE_FILE = "research_baseline.json"

MIN_LIVE_N = 5              # below this: "too few trades"; no amber test
MIN_RESEARCH_N = 10         # below this the research spread means nothing
BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_PERCENTILE = 10
LAST_CLOSES = 3             # closes listed on the dashboard tile

_BIG_ID = 10 ** 18
_SEVERITY_ORDER = {"red": 0, "amber": 1, "info": 2}


def _warning(severity: str, code: str, text: str, bot: str | None = None,
             variant: str | None = None) -> dict:
    return {"severity": severity, "code": code, "text": text,
            "bot": bot, "variant": variant}


def unavailable(what: str, err: BaseException | str) -> dict:
    """The one 'check unavailable' line both surfaces show."""
    detail = err if isinstance(err, str) else repr(err)
    return _warning("amber", "EVIDENCE_UNAVAILABLE",
                    f"RESULTS CHECK UNAVAILABLE {what}: {detail}")


def _parse_utc(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _id_key(trade_id: str) -> tuple[int, str]:
    """Numeric order for SJ-<n> ids ('SJ-999' < 'SJ-1000'), text otherwise."""
    m = re.search(r"(\d+)$", trade_id or "")
    return (int(m.group(1)) if m else _BIG_ID, trade_id or "")


# ─── Scope ────────────────────────────────────────────────────────────────────

def live_variants(bots: Sequence[str] | None = None) -> tuple[list[dict], list[dict]]:
    """([{bot, variant, capital_usdt}], [unavailable warnings]) for every
    variant the bot configs trade. Defaults to health.BOT_CONFIGS."""
    if bots is None:
        import health          # lazy: a top-level script, not a support module
        bots = health.BOT_CONFIGS
    scope: list[dict] = []
    failures: list[dict] = []
    for bot in bots:
        try:
            cfg = importlib.import_module(f"bots.{bot}.config")
            ids = [v["id"] for v in getattr(cfg, "VARIANTS", [])] \
                or [getattr(cfg, "VARIANT_ID")]
            capital = float(getattr(cfg, "CAPITAL_USDT"))
        except Exception as e:  # noqa: BLE001 — surfaced, never swallowed
            failures.append(unavailable(f"{bot} (bots/{bot}/config.py)", e))
            continue
        scope += [{"bot": bot, "variant": vid, "capital_usdt": capital}
                  for vid in ids]
    return scope, failures


def load_baseline(bot: str) -> dict[str, dict]:
    """variant_id -> entry of bots/<bot>/research_baseline.json; {} when the
    file does not exist. Raises ValueError on a malformed or unreadable file,
    so one bad file costs only its own bot's line."""
    path = REPO / "bots" / bot / BASELINE_FILE
    if not path.exists():
        return {}
    try:
        variants = json.loads(path.read_text(encoding="utf-8"))["variants"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ValueError(f"{path.name}: {e!r}") from e
    if not isinstance(variants, dict):
        raise ValueError(f"{path.name}: 'variants' must be an object")
    for vid, entry in variants.items():
        _research_values(entry, f"{path.name} {vid}")
    return variants


def _research_values(entry: dict, where: str) -> tuple[float, ...]:
    values = entry.get("values") if isinstance(entry, dict) else None
    if (not isinstance(values, list) or not values
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       for v in values)):
        raise ValueError(f"{where}: 'values' must be a non-empty list of numbers")
    if _parse_utc(entry.get("comparable_from")) is None:
        raise ValueError(f"{where}: 'comparable_from' is not an ISO timestamp")
    return tuple(float(v) for v in values)


# ─── Ledger ───────────────────────────────────────────────────────────────────

def closed_trades(con: sqlite3.Connection,
                  variant_ids: Sequence[str]) -> dict[str, list[dict]]:
    """Closed trades per variant. Entry and exit fall back to the scheduled
    times when the actual ones are NULL. Index access only, so it works with
    or without a row_factory."""
    out: dict[str, list[dict]] = {vid: [] for vid in variant_ids}
    if not variant_ids:
        return out
    ph = ",".join("?" * len(variant_ids))
    rows = con.execute(
        "SELECT id, strategy_variant, strategy, direction, "
        "COALESCE(actual_entry_time, entry_time), "
        "COALESCE(actual_exit_time, exit_time), pnl_usdt "
        f"FROM trades WHERE status = 'closed' AND strategy_variant IN ({ph})",
        list(variant_ids)).fetchall()
    for r in rows:
        out[r[1]].append({
            "id": r[0], "variant": r[1], "strategy": r[2], "direction": r[3],
            "entry_time": r[4], "exit_time": r[5], "pnl_usdt": r[6],
            "entry_dt": _parse_utc(r[4]), "exit_dt": _parse_utc(r[5]),
        })
    return out


def dedupe(trades: Sequence[dict]) -> tuple[list[dict], int]:
    """(kept, n_dropped): one row per (variant, strategy, direction, UTC entry
    minute), keeping the lowest id. A row without a readable entry time is
    never merged."""
    kept: list[dict] = []
    seen: set[tuple] = set()
    for t in sorted(trades, key=lambda t: _id_key(t["id"])):
        if t["entry_dt"] is None:
            key: tuple = ("unreadable entry", t["id"])
        else:
            key = (t["variant"], t["strategy"], t["direction"],
                   t["entry_dt"].replace(second=0, microsecond=0))
        if key in seen:
            continue
        seen.add(key)
        kept.append(t)
    return kept, len(trades) - len(kept)


def _chronological(trades: Sequence[dict]) -> list[dict]:
    far = datetime.max.replace(tzinfo=timezone.utc)
    return sorted(trades, key=lambda t: (t["exit_dt"] or far, _id_key(t["id"])))


# ─── The amber threshold ──────────────────────────────────────────────────────

@functools.lru_cache(maxsize=512)
def research_threshold(variant_id: str, n: int,
                       values: tuple[float, ...]) -> float:
    """10th percentile of BOOTSTRAP_DRAWS means of n research values drawn
    with replacement. Seeded from sha256(variant_id, n) — not Python's salted
    hash() — so every process, the monitor and the dashboard agree. Cached:
    the dashboard asks every 5 s and the answer only changes with n."""
    seed = int.from_bytes(
        hashlib.sha256(f"{variant_id}:{n}".encode("utf-8")).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    pool = np.asarray(values, dtype=float)
    means = np.empty(BOOTSTRAP_DRAWS)
    step = max(1, 2_000_000 // n)          # bound one batch to ~2M draws
    for start in range(0, BOOTSTRAP_DRAWS, step):
        k = min(step, BOOTSTRAP_DRAWS - start)
        means[start:start + k] = pool[
            rng.integers(0, len(pool), size=(k, n))].mean(axis=1)
    return float(np.percentile(means, BOOTSTRAP_PERCENTILE))


# ─── Assessment ───────────────────────────────────────────────────────────────

def _win_rate(xs: Sequence[float]) -> float:
    return sum(1 for x in xs if x > 0) / len(xs)


def assess(scope: dict, trades: Sequence[dict],
           baseline: dict | None) -> tuple[dict, list[dict]]:
    """(row, warnings) for one variant. `trades` are its closed rows as
    closed_trades() returns them; `baseline` is its research entry or None."""
    bot, vid, capital = scope["bot"], scope["variant"], scope["capital_usdt"]
    label = f"{bot}/{vid}"
    no_pnl = sum(1 for t in trades if t["pnl_usdt"] is None)
    closed, dups = dedupe([t for t in trades if t["pnl_usdt"] is not None])
    closed = _chronological(closed)
    n = len(closed)
    total = sum((t["pnl_usdt"] for t in closed), 0.0)

    ignored = []
    if dups:
        ignored.append(f"{dups} duplicate row{'s' if dups != 1 else ''} ignored")
    if no_pnl:
        ignored.append(f"{no_pnl} closed row{'s' if no_pnl != 1 else ''} "
                       f"without pnl_usdt ignored")
    ignored_txt = "; ".join(ignored)

    warnings: list[dict] = []
    if total < 0:
        few = " — too few trades to conclude anything" if n < MIN_LIVE_N else ""
        warnings.append(_warning(
            "red", "LOSING_MONEY",
            f"LOSING MONEY {label}: net {total:+.2f} USDT over {n} closed "
            f"trade{'s' if n != 1 else ''}{few}"
            + (f" ({ignored_txt})" if ignored_txt else "")
            + " — display only; the operator decides", bot, vid))
    if dups:
        warnings.append(_warning(
            "info", "DUPLICATES_IGNORED",
            f"RESULTS {label}: {dups} duplicate row{'s' if dups != 1 else ''} "
            f"ignored (same strategy, direction and entry minute; the lowest "
            f"id is kept)", bot, vid))

    research = comparable = None
    note = ""
    if baseline is None:
        note = "no research baseline"
        warnings.append(_warning("info", "NO_RESEARCH_BASELINE",
                                 f"RESULTS {label}: no research baseline",
                                 bot, vid))
    else:
        values = _research_values(baseline, vid)
        research = {"n": len(values),
                    "mean_pct": round(sum(values) / len(values), 4),
                    "win_rate": round(_win_rate(values), 4)}
        since = _parse_utc(baseline["comparable_from"])
        comp = [t for t in closed if t["entry_dt"] is not None
                and t["entry_dt"] >= since]
        pcts = [t["pnl_usdt"] / capital * 100 for t in comp]
        comparable = {"since": since.isoformat(), "n": len(comp),
                      "mean_pct": None, "win_rate": None,
                      "threshold_pct": None}
        if len(values) < MIN_RESEARCH_N:
            note = f"research sample too small (n={len(values)})"
            warnings.append(_warning(
                "info", "RESEARCH_SAMPLE_SMALL",
                f"RESULTS {label}: research sample too small for a "
                f"below-research test (n={len(values)})", bot, vid))
        elif len(comp) < MIN_LIVE_N:
            note = (f"below-research test from n={MIN_LIVE_N} "
                    f"(now {len(comp)} since {since:%Y-%m-%d %H:%MZ})")
        else:
            thr = research_threshold(vid, len(comp), values)
            mean = sum(pcts) / len(pcts)
            wr = _win_rate(pcts)
            comparable.update(mean_pct=round(mean, 4), win_rate=round(wr, 4),
                              threshold_pct=round(thr, 4))
            note = (f"live {mean:+.3f}%/trade vs research "
                    f"{research['mean_pct']:+.3f}% (threshold {thr:+.3f}%)")
            if mean < thr:
                warnings.append(_warning(
                    "amber", "BELOW_RESEARCH",
                    f"BELOW RESEARCH {label}: live mean {mean:+.3f}% vs "
                    f"research {research['mean_pct']:+.3f}% of capital per "
                    f"trade, under the threshold {thr:+.3f}% (10th percentile "
                    f"of {BOOTSTRAP_DRAWS:,} research means resampled at "
                    f"n={len(comp)}); win rate {wr:.0%} vs "
                    f"{research['win_rate']:.0%}; n={len(comp)} since "
                    f"{since:%Y-%m-%d %H:%MZ} — research is in-sample; this is "
                    f"a prompt to look, not a verdict", bot, vid))

    row = {
        "bot": bot,
        "variant": vid,
        "capital_usdt": capital,
        "n": n,
        "total_usdt": round(total, 2),
        "duplicates_ignored": dups,
        "no_pnl_ignored": no_pnl,
        "last_closes": [
            {"id": t["id"], "exit_time": t["exit_time"],
             "pnl_usdt": round(t["pnl_usdt"], 2),
             "pct": round(t["pnl_usdt"] / capital * 100, 3)}
            for t in closed[-LAST_CLOSES:]],
        "research": research,
        "comparable": comparable,
        "flags": [w["code"] for w in warnings if w["severity"] != "info"],
        "note": note,
    }
    return row, warnings


def evaluate(con: sqlite3.Connection, bots: Sequence[str] | None = None,
             baselines: dict[str, dict] | None = None) -> dict:
    """{"variants": [row per live variant], "warnings": [...]}. Rows are in
    bot-config order; warnings red, amber, then info. `baselines`
    (variant_id -> research entry) replaces the
    bots/<bot>/research_baseline.json files — for tests."""
    scope, warnings = live_variants(bots)
    if baselines is None:
        baselines = {}
        for bot in dict.fromkeys(s["bot"] for s in scope):
            try:
                baselines.update(load_baseline(bot))
            except ValueError as e:
                warnings.append(unavailable(f"{bot} research baseline", e))
    try:
        trades = closed_trades(con, [s["variant"] for s in scope])
    except sqlite3.Error as e:
        warnings.append(unavailable("trades", e))
        return {"variants": [], "warnings": warnings}
    rows = []
    for s in scope:
        row, w = assess(s, trades[s["variant"]], baselines.get(s["variant"]))
        rows.append(row)
        warnings += w
    warnings.sort(key=lambda w: _SEVERITY_ORDER[w["severity"]])
    return {"variants": rows, "warnings": warnings}

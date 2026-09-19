"""Read-only portfolio monitor — observes, alerts, never trades.

Part of the 2026-07 bot-extraction architecture. Scheduled by
ops/register_tasks.ps1 (\\p300\\monitor-hourly, \\p300\\monitor-daily-deep;
OPERATIONS.md §11), or run ad hoc:

  python monitor.py            # report; exit 0 = green, 1 = alerts, 2 = crashed
  python monitor.py --quiet    # alerts and warnings only (task-friendly)
  python monitor.py --deep     # plus the interior-gap scan (daily)

Every run records itself in data/diagnostics/monitor_last.json (a --deep run
also in monitor_last_deep.json) and appends to data/diagnostics/monitor.log.
The dashboard reads those files, so a monitor that stops running, or crashes
(result "error", exit 2 — distinct from 1 = alerts), turns it red.

prod.db is opened READ-ONLY for every check: a wrong or missing path raises
PROD_DB_UNREADABLE instead of silently creating an empty database.

Checks, in order of the incidents that motivated them:
  1. Table freshness vs botlib.FRESHNESS_CONTRACTS
       (2026-06-28 feed death ran 23 days unnoticed; okx_perp_1h staleness
        silently gate-locked CHENTO_TRIPLE_V3 for its entire paper life)
  2. Heartbeat staleness — any bot/feed row older than 3x its interval
  3. Per-bot silence — last signal evaluation older than its expected
     cadence limit (a bot that runs but never evaluates is the invisible
     failure mode)
  4. Overdue open trades — open paper trades past exit_time + grace
     (backstop-of-the-backstop)
  5. Classification completeness — every prod.db table is contracted or
     declared frozen/gated/state/static in botlib; unknown tables alert
       (the screener klines died silently 2026-05-24 because nothing owned them)
  6. Retention burn — LSR/OI upstreams serve only ~30d; staleness is scored
     against the burn-down clock, not just cadence
  7. scheduled_events runway (<60d of future events) and archive-JSON mtime
  8. --deep: interior-gap scan of contracted tables via data/check_gaps.py
     (daily; MAX(ts) freshness cannot see holes in the middle)
  9. DISK_LOW — free space on prod.db's drive (2026-09-09: a full C: broke
     the feed's writes and left the gaps the deep scan still finds)
 10. BACKUP_STALE — age of the newest good prod.db backup
 11. DEEP_SCAN_STALE (hourly runs) — the daily deep scan stopped recording

Results warnings (strategies/support/evidence.py: LOSING_MONEY red,
BELOW_RESEARCH amber, info lines) are a separate `warnings` tier: printed
every run and recorded in the status file, never pushed on their own and
never part of the exit code. They are display-only; the operator decides.

Expected-cadence limits live in BOT_EXPECTATIONS below — extend when a new
bot ships (part of its day-1 requirements).

Telegram alerts (2026-07-22): set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in
.env (create the bot via @BotFather; chat id via @userinfobot or the
getUpdates API). Alerts are pushed on every non-green run; `--summary`
additionally pushes an all-green daily status so silence itself signals
breakage. `--test-alert` verifies the wiring. Send failures are logged,
never fatal.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

try:
    import botlib  # noqa: E402
    # not evidence: display-only results are imported where they are used
    # (results_evidence), so a broken evidence.py cannot stop the monitor
    from strategies.support import db  # noqa: E402
    _IMPORT_ERROR: str | None = None
except Exception:  # noqa: BLE001
    if __name__ != "__main__":
        raise                    # importers (dashboard, tests) see the real error
    _IMPORT_ERROR = traceback.format_exc()   # recorded as a crash at the bottom

log = logging.getLogger("monitor")

# Units that are built and wired but DELIBERATELY not running. A held unit is
# not a fault: it must not raise DEAD or MISSING, and the dashboard renders it
# as HELD. The moment a process for it IS seen, every normal check applies
# again — holding suppresses "it should be running", never "it is misbehaving".
#
# Keep the reason here rather than in a comment: it is what the dashboard shows,
# e.g. {"r4": "held 2026-09-09 pending a mechanism ..."}. Empty since 2026-09-12:
# r4 rejoined the fleet defaults with only its ETH windows enabled
# (bots/r4/config.py ENABLED; docs/calibration/r4.md).
HELD_UNITS: dict[str, str] = {}

# bot name -> max seconds since last_eval_utc before it counts as silent.
# Chento evaluates every 15m bar; Short Squeeze evaluates 15m bars inside
# London/NY sessions (07-21 UTC), so its longest legitimate eval gap is the
# ~10h overnight window; both get slack on top.
BOT_EXPECTATIONS: dict[str, int] = {
    "chento_v3": 2 * 3600,
    "chento_v3_eth": 2 * 3600,
    "short_squeeze": 14 * 3600,
    "adx": 26 * 3600,          # daily entry decision + continuous sweep
    "carry": 26 * 3600,        # daily funding decision
    "carry_eth": 26 * 3600,    # the ETH twin, same cadence (2026-09-19)
    "r4": 2 * 3600,
    # Hourly inputs, so one evaluation per closed hour; 2h tolerates a
    # missed hour before it counts as silent.
    "squeeze_bull": 2 * 3600,
}

OVERDUE_GRACE_S = 2 * 3600

# Upstream APIs for these tables only serve a trailing window; feed downtime
# beyond it is PERMANENT history loss (binance.py:264-268, 316-320). Escalate
# on a burn-down clock, not just cadence staleness.
RETENTION_LIMITS: dict[str, int] = {
    "ca_long_short_ratio": 30 * 86400,
    "cd_open_interest":    30 * 86400,
}
RETENTION_WARN_S = 3 * 86400
RETENTION_CRIT_S = 7 * 86400

# Daily-refreshed archive files (rewritten on every successful refresh, so
# mtime is a valid freshness signal). Path resolved under db.DATA_DIR/archive.
ARCHIVE_FILES: dict[str, int] = {
    "fed_funds_target_upper.json": 3 * 86400,
    "polymarket_fed_2026.json":    3 * 86400,
}

EVENT_RUNWAY_DAYS = 60

# Job status files under db.DATA_DIR / STATUS_DIRNAME, resolved at call time.
# monitor.py writes the first two, backup.py the third; the dashboard reads
# all three, so a task that stops running shows up there.
STATUS_DIRNAME = "diagnostics"
MONITOR_STATUS = "monitor_last.json"
DEEP_STATUS = "monitor_last_deep.json"
BACKUP_STATUS = "backup_last.json"
MONITOR_LOG = "monitor.log"
STATUS_SCHEMA = 1

MONITOR_STALE_S = 2 * 3600 + 900     # hourly task: one missed run plus slack
DEEP_STALE_S = 30 * 3600             # daily task plus slack
BACKUP_WARN_S = 30 * 3600
BACKUP_CRIT_S = 72 * 3600
DISK_WARN_BYTES = 5_000_000_000      # free on prod.db's drive
DISK_CRIT_BYTES = 2_000_000_000

EXIT_GREEN, EXIT_ALERTS, EXIT_CRASH = 0, 1, 2


def _age_s(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).total_seconds()
    except (TypeError, ValueError):   # TypeError: a status file's non-string field
        return None


def _notify(text: str) -> bool:
    """Push `text` to Telegram. Returns True on success; failures are
    printed and swallowed — alerting must never break monitoring."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("    (telegram not configured — set TELEGRAM_BOT_TOKEN + "
              "TELEGRAM_CHAT_ID in .env)")
        return False
    body = json.dumps({"chat_id": chat_id,
                       "text": text[:3900]}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = json.loads(resp.read()).get("ok", False)
        if not ok:
            print("    telegram send rejected")
        return bool(ok)
    except Exception as e:
        print(f"    telegram send failed: {e!r}")
        return False


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "never/unreadable"
    if seconds < 120:
        return f"{seconds:.0f}s"
    if seconds < 7200:
        return f"{seconds / 60:.0f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _alert(code: str, text: str) -> dict:
    return {"code": code, "text": text}


# ─── Status files ─────────────────────────────────────────────────────────────

def status_path(name: str) -> Path:
    return db.DATA_DIR / STATUS_DIRNAME / name


def read_status(name: str) -> tuple[dict | None, str | None]:
    """(payload, error) of a job status file: (None, None) when the file does
    not exist, (None, reason) when it cannot be read or is not a JSON object."""
    path = status_path(name)
    if not path.exists():
        return None, None
    for attempt in range(3):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            break
        except PermissionError as e:      # mid-replace on Windows: retry
            if attempt == 2:
                return None, f"{name} unreadable: {e!r}"
            time.sleep(0.05)
        except (OSError, ValueError) as e:
            return None, f"{name} unreadable: {e!r}"
    if not isinstance(payload, dict):
        return None, f"{name} is not a JSON object"
    return payload, None


def write_status(name: str, payload: dict, directory: Path | None = None) -> bool:
    """Replace a status file (in `directory`, default status_path's) atomically
    (tmp + os.replace). On Windows a reader holding the file open makes the
    replace raise PermissionError, so retry briefly. Never raises: recording a
    run must not fail the run."""
    path = status_path(name) if directory is None else directory / name
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2)
    except Exception as e:  # noqa: BLE001
        log.warning(f"status file {path} not written: {e!r}")
        print(f"    (status file {path} not written: {e!r})")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return False


# ─── Checks that need no database ─────────────────────────────────────────────

def disk_low() -> tuple[bool, str] | None:
    """(critical, text) when free space on prod.db's drive is below
    DISK_WARN_BYTES, else None. 2026-09-09: a full C: broke the feed's
    writes."""
    drive = Path(db.PROD_DB).anchor or "."
    free = shutil.disk_usage(drive).free
    if free >= DISK_WARN_BYTES:
        return None
    critical = free < DISK_CRIT_BYTES
    return critical, (
        f"DISK LOW ({'CRITICAL' if critical else 'warn'}) {drive}: "
        f"{free / 1e9:.1f} GB free (warn below {DISK_WARN_BYTES / 1e9:.0f} GB, "
        f"critical below {DISK_CRIT_BYTES / 1e9:.0f} GB)")


def backup_stale(now: datetime) -> tuple[bool, str] | None:
    """(critical, text) when the newest good backup is older than
    BACKUP_WARN_S, else None. Its age comes from backup_last.json when that
    run's result is ok, otherwise from the newest data/backups/prod-*.db."""
    payload, _ = read_status(BACKUP_STATUS)
    age = source = None
    if payload is not None and payload.get("result") == "ok":
        age = _age_s(payload.get("finished_utc"), now)
        source = BACKUP_STATUS
    if age is None:
        copies = []
        for p in (db.DATA_DIR / "backups").glob("prod-*.db"):
            try:
                copies.append((p.stat().st_mtime, p.name))
            except OSError:           # pruned between the glob and the stat
                continue
        if copies:
            mtime, source = max(copies)
            age = now.timestamp() - mtime
    if age is not None and age <= BACKUP_WARN_S:
        return None
    critical = age is None or age > BACKUP_CRIT_S
    what = ("no backup found" if age is None
            else f"newest good backup is {_fmt_age(age)} old ({source})")
    return critical, (
        f"BACKUP STALE ({'CRITICAL' if critical else 'warn'}): {what} — "
        f"check the \\p300\\backup-daily task, or run python backup.py")


def deep_scan_stale(now: datetime) -> str | None:
    """Alert text when the daily deep scan has not recorded a finished run
    within DEEP_STALE_S, else None."""
    payload, err = read_status(DEEP_STATUS)
    if err:
        return f"DEEP SCAN STALE: {err}"
    if payload is None:
        return ("DEEP SCAN STALE: no deep gap scan recorded — check the "
                "\\p300\\monitor-daily-deep task, or run python monitor.py --deep")
    finished = payload.get("finished_utc")
    if payload.get("result") == "error":
        return (f"DEEP SCAN STALE: the last deep scan ({finished}) crashed — "
                f"see data/diagnostics/{MONITOR_LOG}")
    age = _age_s(finished, now)
    if age is None or age > DEEP_STALE_S:
        return (f"DEEP SCAN STALE: last deep gap scan {_fmt_age(age)} ago "
                f"(limit {_fmt_age(DEEP_STALE_S)})")
    return None


def _file_checks(now: datetime, deep: bool, alerts: list[dict]) -> None:
    # 1e. archive JSONs — feed-cycle outputs with no table; mtime is the signal.
    for name, limit in ARCHIVE_FILES.items():
        path = db.DATA_DIR / "archive" / name
        if not path.exists():
            alerts.append(_alert("ARCHIVE_STALE",
                                 f"ARCHIVE STALE {name}: file missing"))
            continue
        age = now.timestamp() - path.stat().st_mtime
        if age > limit:
            alerts.append(_alert(
                "ARCHIVE_STALE", f"ARCHIVE STALE {name}: last written "
                f"{_fmt_age(age)} ago (limit {_fmt_age(limit)})"))

    # 9-11. disk, backups, and the daily deep scan itself.
    low = disk_low()
    if low:
        alerts.append(_alert("DISK_LOW", low[1]))
    stale_backup = backup_stale(now)
    if stale_backup:
        alerts.append(_alert("BACKUP_STALE", stale_backup[1]))
    if not deep:
        stale_deep = deep_scan_stale(now)
        if stale_deep:
            alerts.append(_alert("DEEP_SCAN_STALE", stale_deep))


# ─── Checks against prod.db (read-only) ───────────────────────────────────────

def _ro_con() -> sqlite3.Connection:
    """Read-only prod.db connection. mode=ro never creates the file, so a
    wrong or missing path raises here instead of leaving an empty prod.db."""
    con = sqlite3.connect(f"file:{db.PROD_DB}?mode=ro", uri=True, timeout=5.0)
    try:
        con.execute("PRAGMA query_only=1")
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
    except sqlite3.Error:
        con.close()
        raise
    return con


def _heartbeats(con: sqlite3.Connection) -> list[dict]:
    try:
        cur = con.execute("SELECT * FROM bot_heartbeats ORDER BY name")
    except sqlite3.OperationalError:
        return []
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _db_checks(con: sqlite3.Connection, now: datetime, deep: bool,
               alerts: list[dict], info: list[str]) -> None:
    # 1. table freshness
    stale = {}
    for table in botlib.FRESHNESS_CONTRACTS:
        age = botlib.latest_age_s(table, con)
        if age is None or age > botlib.FRESHNESS_CONTRACTS[table][2]:
            stale[table] = age
    for table, age in sorted(stale.items()):
        limit = botlib.FRESHNESS_CONTRACTS[table][2]
        alerts.append(_alert("STALE_TABLE",
                             f"STALE TABLE  {table}: age {_fmt_age(age)} "
                             f"(contract {_fmt_age(limit)})"))
    if not stale:
        info.append(f"tables: all {len(botlib.FRESHNESS_CONTRACTS)} fresh")

    # 1b. classification completeness — every table must be contracted or
    # declared; a new table with no classification alerts within the hour.
    db_tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    classified = (set(botlib.FRESHNESS_CONTRACTS) | set(botlib.FROZEN_TABLES)
                  | botlib.GATED_TABLES | botlib.STATE_TABLES
                  | botlib.STATIC_TABLES)
    for t in sorted(db_tables - classified):
        alerts.append(_alert("UNCLASSIFIED_TABLE",
                             f"UNCLASSIFIED TABLE {t}: contract it or declare "
                             f"it frozen/gated/state in botlib.py"))
    for t in sorted(classified - db_tables):
        alerts.append(_alert("GHOST_REGISTRY",
                             f"GHOST REGISTRY ENTRY {t}: classified in "
                             f"botlib.py but no such table in prod.db"))

    # 1c. retention burn — staleness scored against the upstream window.
    for table, window_s in RETENTION_LIMITS.items():
        age = botlib.latest_age_s(table, con)
        if age is None or age <= RETENTION_WARN_S:
            continue
        left_d = max(0.0, (window_s - age) / 86400)
        sev = "CRITICAL" if age > RETENTION_CRIT_S else "warn"
        alerts.append(_alert(
            "HISTORY_BURN", f"HISTORY BURN ({sev}) {table}: stale "
            f"{_fmt_age(age)}, {left_d:.0f}d of upstream retention left — "
            f"restart feed before this history is gone for good"))

    # 1d. scheduled_events runway — static calendar, expires silently otherwise.
    try:
        row = con.execute("SELECT MAX(date) FROM scheduled_events").fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is None or row[0] is None:
        alerts.append(_alert("EVENT_RUNWAY",
                             "EVENT RUNWAY scheduled_events: table "
                             "missing/empty — run fetch_events.py"))
    else:
        end = datetime.fromisoformat(str(row[0])).replace(tzinfo=timezone.utc)
        runway_d = (end - now).days
        if runway_d < EVENT_RUNWAY_DAYS:
            alerts.append(_alert(
                "EVENT_RUNWAY", f"EVENT RUNWAY scheduled_events ends {row[0]} "
                f"({runway_d}d) — extend fetch_events.py lists and re-run it"))

    # 1f. interior gaps (--deep, daily) — MAX(ts) freshness can't see holes.
    if deep:
        from data import check_gaps
        for spec in check_gaps.SPECS:
            if spec.table not in botlib.FRESHNESS_CONTRACTS:
                continue  # gated/frozen tables don't gap-alert
            if spec.cadence_seconds is None:
                continue  # cd_funding_rate: 2026-04-13 cadence cutover
            if spec.table not in db_tables:
                continue  # table missing: already a STALE TABLE alert above
            try:
                gaps = check_gaps.collect_gaps(con, spec)
            except sqlite3.Error as e:
                # locked, I/O error, schema drift: say the table went unscanned
                # (same code, so the dashboard replays it with the real gaps)
                alerts.append(_alert("INTERIOR_GAPS", f"INTERIOR GAPS {spec.table}: "
                                     f"scan failed: {e!r}"))
                continue
            if gaps:
                n_rows = sum(g[3] for g in gaps)
                oldest = min(g[1] for g in gaps)
                oldest_iso = datetime.fromtimestamp(
                    oldest, tz=timezone.utc).isoformat()[:16]
                alerts.append(_alert(
                    "INTERIOR_GAPS", f"INTERIOR GAPS {spec.table}: "
                    f"{len(gaps)} gap(s), {n_rows} rows missing "
                    f"(oldest {oldest_iso}Z)"))
        # 1g. open-interest stamp convention (--deep, daily). The row stamped
        # H must hold the snapshot at H+1h, the close of hour H — what the
        # squeeze bots were researched on. From 2026-06-10 to 2026-09-19 it
        # held the snapshot at H, one bar stale, and SJ-4250 fired on it
        # (BACKLOG 30). Freshness cannot see a shift; this can.
        try:
            from data.sources import binance as _binance
            oi = _binance.check_oi_semantics()
        except Exception as e:  # noqa: BLE001 — network/schema: say it went unverified
            alerts.append(_alert("OI_SEMANTICS",
                                 f"OI SEMANTICS cd_open_interest: check could "
                                 f"not run: {e!r}"))
        else:
            if oi["verdict"] != "ok":
                alerts.append(_alert(
                    "OI_SEMANTICS", f"OI SEMANTICS cd_open_interest: "
                    f"{oi['verdict']} — of {oi['scored']} stamps, "
                    f"{oi['end_of_hour']} sit closer to the close-of-hour "
                    f"snapshot and {oi['start_of_hour']} to the start-of-hour one"))
            else:
                info.append(f"oi semantics: {oi['end_of_hour']}/{oi['scored']} "
                            f"closer to close-of-hour")
        info.append("deep gap scan: done")

    # 2 + 3. heartbeats
    beats = _heartbeats(con)
    if not beats:
        alerts.append(_alert("NO_HEARTBEATS", "NO HEARTBEATS — feed/bots not "
                             "running (or schema absent)"))
    seen = {b["name"] for b in beats}
    for name in sorted(set(BOT_EXPECTATIONS) - seen):
        if name in HELD_UNITS:
            info.append(f"{name}: HELD — {HELD_UNITS[name]}")
            continue
        alerts.append(_alert("MISSING_BOT", f"MISSING BOT  {name}: no "
                             f"heartbeat row — never started"))
    for b in beats:
        name = b["name"]
        tick_age = _age_s(b.get("last_tick_utc"), now)
        interval = b.get("interval_s") or 60
        if tick_age is None or tick_age > 3 * interval:
            if name in HELD_UNITS:
                info.append(f"{name}: HELD (stale heartbeat from its last run, "
                            f"{_fmt_age(tick_age)} ago) — {HELD_UNITS[name]}")
                continue
            alerts.append(_alert("DEAD_PROCESS", f"DEAD PROCESS {name}: last "
                                 f"tick {_fmt_age(tick_age)} "
                                 f"(interval {interval}s)"))
        elif b.get("status") != "ok":
            alerts.append(_alert("DEGRADED", f"DEGRADED     {name}: "
                                 f"{b.get('status')} — {b.get('note') or ''}"))
        elif "DUPLICATE INSTANCE" in (b.get("note") or ""):
            # botlib.heartbeat writes this note when two pids share one row,
            # but the runner's status stays "ok" — without this check the
            # warning is invisible (2026-08-24 incident: four bots + feed ran
            # doubled for 9 days, chento double-sized every signal).
            alerts.append(_alert("DUPLICATE",
                                 f"DUPLICATE    {name}: {b.get('note')}"))
        else:
            info.append(f"{name}: tick {_fmt_age(tick_age)} ago, ok")
        limit = BOT_EXPECTATIONS.get(name)
        if limit is not None and tick_age is not None and tick_age <= 3 * interval:
            eval_age = _age_s(b.get("last_eval_utc"), now)
            if eval_age is None or eval_age > limit:
                alerts.append(_alert(
                    "SILENT_BOT", f"SILENT BOT   {name}: last signal eval "
                    f"{_fmt_age(eval_age)} (limit {_fmt_age(limit)})"))
        sig_age = _age_s(b.get("last_signal_utc"), now)
        if sig_age is not None:
            info.append(f"{name}: last signal {_fmt_age(sig_age)} ago, "
                        f"open trades: {b.get('open_trades')}")

    # 4. overdue open trades (all enabled variants)
    try:
        rows = con.execute(
            "SELECT t.id, t.strategy, t.strategy_variant, t.exit_time "
            "FROM trades t JOIN variants v ON t.strategy_variant = v.id "
            "WHERE t.execution_mode='paper' AND t.status='open' AND COALESCE(v.enabled,1)=1"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for tid, strat, variant, exit_iso in rows:
        age = _age_s(exit_iso, now)
        if age is not None and age > OVERDUE_GRACE_S:
            alerts.append(_alert("OVERDUE_TRADE",
                                 f"OVERDUE TRADE {tid} ({strat}/{variant}): "
                                 f"exit_time passed {_fmt_age(age)} ago"))


def results_evidence(con: sqlite3.Connection) -> dict:
    """evidence.evaluate(con), or one EVIDENCE_UNAVAILABLE line when it raises
    or evidence.py does not import. Imported here, not at the top: results are
    display only, and must cost neither a monitor run its operational alerts
    nor the dashboard (which calls this too) its fleet panel."""
    try:
        from strategies.support import evidence
        return evidence.evaluate(con)
    except Exception as e:  # noqa: BLE001 — a visible line, not a crash
        # evidence.unavailable()'s line, spelled out: evidence may be what failed
        return {"variants": [], "warnings": [{
            "severity": "amber", "code": "EVIDENCE_UNAVAILABLE",
            "text": f"RESULTS CHECK UNAVAILABLE evaluate: {e!r}",
            "bot": None, "variant": None}]}


def _checks(now: datetime, deep: bool) -> tuple[list[dict], list[dict], list[str]]:
    """(alerts, warnings, info). Alerts set the exit code; warnings (results
    evidence) never do."""
    alerts: list[dict] = []
    warnings: list[dict] = []
    info: list[str] = []
    try:
        con = _ro_con()
    except sqlite3.Error as e:
        con = None
        alerts.append(_alert(
            "PROD_DB_UNREADABLE", f"PROD DB UNREADABLE {db.PROD_DB}: {e} — "
            f"table, heartbeat, trade and results checks skipped"))
    if con is not None:
        try:
            _db_checks(con, now, deep, alerts, info)
            warnings = results_evidence(con)["warnings"]
        finally:
            con.close()
    _file_checks(now, deep, alerts)
    return alerts, warnings, info


# ─── Output ───────────────────────────────────────────────────────────────────

def _report(now: datetime, alerts: list[dict], warnings: list[dict],
            info: list[str], quiet: bool, summary: bool) -> None:
    """Print (alerts and warnings always, the rest unless quiet), log every
    line, and push alerts to Telegram when it is configured."""
    stamp = now.isoformat()[:16] + "Z"

    def out(line: str, show: bool = True) -> None:
        log.info(line)
        if show:
            print(line)

    if alerts:
        out(f"=== monitor {stamp} — {len(alerts)} ALERT(S) ===")
        for a in alerts:
            out(f"  !! {a['text']}")
    else:
        out(f"=== monitor {stamp} — all green ===", show=not quiet)
    for w in warnings:
        out(f"  ~~ [{w['severity']}] {w['text']}")
    if alerts:
        _notify(f"p300 monitor — {len(alerts)} ALERT(S) @ {stamp}\n"
                + "\n".join(f"!! {a['text']}" for a in alerts))
    elif summary:
        _notify(f"p300 monitor — all green @ {stamp}\n"
                + "\n".join(info[:12]))
    for i in info:
        out(f"    {i}", show=not quiet)


def _record(deep: bool, started: datetime, result: str, exit_code: int,
            alerts: list[dict], warnings: list[dict], info: list[str],
            argv: list[str] | None, error: str | None = None,
            directory: Path | None = None) -> None:
    finished = datetime.now(timezone.utc)
    payload = {
        "schema": STATUS_SCHEMA,
        "job": "monitor",
        "mode": "deep" if deep else "hourly",
        "started_utc": started.isoformat(timespec="seconds"),
        "finished_utc": finished.isoformat(timespec="seconds"),
        "duration_s": round((finished - started).total_seconds(), 2),
        "result": result,                    # green | alerts | error
        "exit_code": exit_code,
        "alerts": alerts,
        "warnings": [{"code": w["code"], "text": w["text"],
                      "severity": w["severity"]} for w in warnings],
        "info": info,
        "error": error,
        "argv": argv,
        "pid": os.getpid(),
    }
    write_status(MONITOR_STATUS, payload, directory)
    if deep:
        write_status(DEEP_STATUS, payload, directory)


def run(quiet: bool = False, summary: bool = False, deep: bool = False,
        argv: list[str] | None = None) -> int:
    started = datetime.now(timezone.utc)
    alerts, warnings, info = _checks(started, deep)
    _report(started, alerts, warnings, info, quiet=quiet, summary=summary)
    code = EXIT_ALERTS if alerts else EXIT_GREEN
    _record(deep, started, "alerts" if alerts else "green", code,
            alerts, warnings, info, argv)
    return code


def _open_log(directory: Path | None = None) -> logging.Handler | None:
    """Attach monitor.log (1 MB x 5) in `directory`, default data/diagnostics
    resolved at call time. A log that cannot be opened is reported, never
    fatal."""
    path = status_path(MONITOR_LOG) if directory is None else directory / MONITOR_LOG
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5,
                                      encoding="utf-8")
    except OSError as e:
        print(f"    ({MONITOR_LOG} unavailable: {e!r})")
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only bot/feed monitor")
    ap.add_argument("--quiet", action="store_true",
                    help="Print only alerts and warnings (task-friendly).")
    ap.add_argument("--summary", action="store_true",
                    help="Also push an all-green status to Telegram (use on "
                         "a daily schedule so silence itself is a signal).")
    ap.add_argument("--test-alert", action="store_true",
                    help="Send a test Telegram message and exit.")
    ap.add_argument("--deep", action="store_true",
                    help="Also scan contracted tables for interior gaps "
                         "(heavier; run daily, not hourly).")
    args = ap.parse_args(argv)
    arglist = list(sys.argv[1:] if argv is None else argv)

    if args.test_alert:
        from strategies.support.env import load_env_file
        load_env_file()
        ok = _notify("p300 monitor — test alert: wiring works.")
        print("test alert sent" if ok else "test alert FAILED")
        return 0 if ok else 1

    handler = _open_log()
    started = datetime.now(timezone.utc)
    try:
        from strategies.support.env import load_env_file
        load_env_file()
        return run(quiet=args.quiet, summary=args.summary, deep=args.deep,
                   argv=arglist)
    except Exception:
        # Exit 2, not 1: a monitor broken by a refactor must not look like
        # one that is reporting alerts.
        tb = traceback.format_exc()
        log.error(f"monitor CRASHED\n{tb}")
        print(tb, file=sys.stderr)
        _record(args.deep, started, "error", EXIT_CRASH, [], [], [], arglist,
                error=tb)
        return EXIT_CRASH
    finally:
        if handler is not None:
            log.removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    if _IMPORT_ERROR is not None:
        # botlib or strategies.support failed to import (a broken refactor).
        # Python's own exit would be 1, which reads as "alerts", and under the
        # task's pythonw the traceback would go nowhere: record it where the
        # dashboard looks (db.DATA_DIR's default, REPO/data) and in monitor.log,
        # where it and OPERATIONS §11 send the operator, and exit 2.
        print(_IMPORT_ERROR, file=sys.stderr)
        diag = REPO / "data" / STATUS_DIRNAME
        crash_log = _open_log(diag)
        log.error(f"monitor CRASHED at import\n{_IMPORT_ERROR}")
        if crash_log is not None:
            log.removeHandler(crash_log)
            crash_log.close()
        _record("--deep" in sys.argv[1:], datetime.now(timezone.utc), "error",
                EXIT_CRASH, [], [], [], sys.argv[1:], error=_IMPORT_ERROR,
                directory=diag)
        sys.exit(EXIT_CRASH)
    sys.exit(main())

"""Read-only portfolio monitor — observes, alerts, never trades.

Part of the 2026-07 bot-extraction architecture. Run it ad hoc or hourly
via Task Scheduler:

  python monitor.py            # report; exit 0 = green, 1 = alerts
  python monitor.py --quiet    # alerts only (cron-friendly)

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
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

import botlib  # noqa: E402
from strategies.support import db  # noqa: E402

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


def _age_s(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).total_seconds()
    except ValueError:
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


def run(quiet: bool = False, summary: bool = False, deep: bool = False) -> int:
    now = datetime.now(timezone.utc)
    alerts: list[str] = []
    info: list[str] = []

    # 1. table freshness
    stale = botlib.stale_tables()
    for table, age in sorted(stale.items()):
        limit = botlib.FRESHNESS_CONTRACTS[table][2]
        alerts.append(f"STALE TABLE  {table}: age {_fmt_age(age)} "
                      f"(contract {_fmt_age(limit)})")
    if not stale:
        info.append(f"tables: all {len(botlib.FRESHNESS_CONTRACTS)} fresh")

    # 1b. classification completeness — every table must be contracted or
    # declared; a new table with no classification alerts within the hour.
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        db_tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    classified = (set(botlib.FRESHNESS_CONTRACTS) | set(botlib.FROZEN_TABLES)
                  | botlib.GATED_TABLES | botlib.STATE_TABLES
                  | botlib.STATIC_TABLES)
    for t in sorted(db_tables - classified):
        alerts.append(f"UNCLASSIFIED TABLE {t}: contract it or declare it "
                      f"frozen/gated/state in botlib.py")
    for t in sorted(classified - db_tables):
        alerts.append(f"GHOST REGISTRY ENTRY {t}: classified in botlib.py "
                      f"but no such table in prod.db")

    # 1c. retention burn — staleness scored against the upstream window.
    for table, window_s in RETENTION_LIMITS.items():
        age = botlib.latest_age_s(table)
        if age is None or age <= RETENTION_WARN_S:
            continue
        left_d = max(0.0, (window_s - age) / 86400)
        sev = "CRITICAL" if age > RETENTION_CRIT_S else "warn"
        alerts.append(f"HISTORY BURN ({sev}) {table}: stale {_fmt_age(age)}, "
                      f"{left_d:.0f}d of upstream retention left — "
                      f"restart feed before this history is gone for good")

    # 1d. scheduled_events runway — static calendar, expires silently otherwise.
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        row = con.execute("SELECT MAX(date) FROM scheduled_events").fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        con.close()
    if row is None or row[0] is None:
        alerts.append("EVENT RUNWAY scheduled_events: table missing/empty — "
                      "run fetch_events.py")
    else:
        end = datetime.fromisoformat(str(row[0])).replace(tzinfo=timezone.utc)
        runway_d = (end - now).days
        if runway_d < EVENT_RUNWAY_DAYS:
            alerts.append(f"EVENT RUNWAY scheduled_events ends {row[0]} "
                          f"({runway_d}d) — extend fetch_events.py lists and "
                          f"re-run it")

    # 1e. archive JSONs — feed-cycle outputs with no table; mtime is the signal.
    for name, limit in ARCHIVE_FILES.items():
        path = db.DATA_DIR / "archive" / name
        if not path.exists():
            alerts.append(f"ARCHIVE STALE {name}: file missing")
            continue
        age = now.timestamp() - path.stat().st_mtime
        if age > limit:
            alerts.append(f"ARCHIVE STALE {name}: last written "
                          f"{_fmt_age(age)} ago (limit {_fmt_age(limit)})")

    # 1f. interior gaps (--deep, daily) — MAX(ts) freshness can't see holes.
    if deep:
        from data import check_gaps
        for spec in check_gaps.SPECS:
            if spec.table not in botlib.FRESHNESS_CONTRACTS:
                continue  # gated/frozen tables don't gap-alert
            if spec.cadence_seconds is None:
                continue  # cd_funding_rate: 2026-04-13 cadence cutover
            gcon = sqlite3.connect(str(db.PROD_DB))
            try:
                gaps = check_gaps.collect_gaps(gcon, spec)
            finally:
                gcon.close()
            if gaps:
                n_rows = sum(g[3] for g in gaps)
                oldest = min(g[1] for g in gaps)
                oldest_iso = datetime.fromtimestamp(
                    oldest, tz=timezone.utc).isoformat()[:16]
                alerts.append(f"INTERIOR GAPS {spec.table}: {len(gaps)} gap(s), "
                              f"{n_rows} rows missing (oldest {oldest_iso}Z)")
        info.append("deep gap scan: done")

    # 2 + 3. heartbeats
    beats = botlib.get_heartbeats()
    if not beats:
        alerts.append("NO HEARTBEATS — feed/bots not running (or schema absent)")
    seen = {b["name"] for b in beats}
    for name in sorted(set(BOT_EXPECTATIONS) - seen):
        alerts.append(f"MISSING BOT  {name}: no heartbeat row — never started")
    for b in beats:
        name = b["name"]
        tick_age = _age_s(b.get("last_tick_utc"), now)
        interval = b.get("interval_s") or 60
        if tick_age is None or tick_age > 3 * interval:
            alerts.append(f"DEAD PROCESS {name}: last tick {_fmt_age(tick_age)} "
                          f"(interval {interval}s)")
        elif b.get("status") != "ok":
            alerts.append(f"DEGRADED     {name}: {b.get('status')} — "
                          f"{b.get('note') or ''}")
        elif "DUPLICATE INSTANCE" in (b.get("note") or ""):
            # botlib.heartbeat writes this note when two pids share one row,
            # but the runner's status stays "ok" — without this check the
            # warning is invisible (2026-08-24 incident: four bots + feed ran
            # doubled for 9 days, chento double-sized every signal).
            alerts.append(f"DUPLICATE    {name}: {b.get('note')}")
        else:
            info.append(f"{name}: tick {_fmt_age(tick_age)} ago, ok")
        limit = BOT_EXPECTATIONS.get(name)
        if limit is not None and tick_age is not None and tick_age <= 3 * interval:
            eval_age = _age_s(b.get("last_eval_utc"), now)
            if eval_age is None or eval_age > limit:
                alerts.append(f"SILENT BOT   {name}: last signal eval "
                              f"{_fmt_age(eval_age)} (limit {_fmt_age(limit)})")
        sig_age = _age_s(b.get("last_signal_utc"), now)
        if sig_age is not None:
            info.append(f"{name}: last signal {_fmt_age(sig_age)} ago, "
                        f"open trades: {b.get('open_trades')}")

    # 4. overdue open trades (all enabled variants)
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        rows = con.execute(
            "SELECT t.id, t.strategy, t.strategy_variant, t.exit_time "
            "FROM trades t JOIN variants v ON t.strategy_variant = v.id "
            "WHERE t.execution_mode='paper' AND t.status='open' AND v.enabled=1"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    for tid, strat, variant, exit_iso in rows:
        age = _age_s(exit_iso, now)
        if age is not None and age > OVERDUE_GRACE_S:
            alerts.append(f"OVERDUE TRADE {tid} ({strat}/{variant}): "
                          f"exit_time passed {_fmt_age(age)} ago")

    stamp = now.isoformat()[:16] + "Z"
    if alerts:
        print(f"=== monitor {stamp} — {len(alerts)} ALERT(S) ===")
        for a in alerts:
            print("  !!", a)
        _notify(f"p300 monitor — {len(alerts)} ALERT(S) @ {stamp}\n"
                + "\n".join(f"!! {a}" for a in alerts))
    elif not quiet:
        print(f"=== monitor {stamp} — all green ===")
    if not alerts and summary:
        _notify(f"p300 monitor — all green @ {stamp}\n"
                + "\n".join(info[:12]))
    if not quiet:
        for i in info:
            print("   ", i)
    return 1 if alerts else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only bot/feed monitor")
    ap.add_argument("--quiet", action="store_true",
                    help="Print only alerts (cron-friendly).")
    ap.add_argument("--summary", action="store_true",
                    help="Also push an all-green status to Telegram (use on "
                         "a daily schedule so silence itself is a signal).")
    ap.add_argument("--test-alert", action="store_true",
                    help="Send a test Telegram message and exit.")
    ap.add_argument("--deep", action="store_true",
                    help="Also scan contracted tables for interior gaps "
                         "(heavier; run daily, not hourly).")
    args = ap.parse_args(argv)

    from strategies.support.env import load_env_file
    load_env_file()

    if args.test_alert:
        ok = _notify("p300 monitor — test alert: wiring works.")
        print("test alert sent" if ok else "test alert FAILED")
        return 0 if ok else 1
    return run(quiet=args.quiet, summary=args.summary, deep=args.deep)


if __name__ == "__main__":
    sys.exit(main())

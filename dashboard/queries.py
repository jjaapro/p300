"""All prod.db reads and derived state for the dashboard. STRICTLY READ-ONLY.

Every connection here is opened `mode=ro` + `PRAGMA query_only=1` — SQLite
itself refuses writes, so a bug in this module cannot touch the ledger. That
is also why the botlib conveniences (`get_heartbeats`, `stale_tables`,
`ensure_wal`) are deliberately NOT called: they open write-capable
connections and would create the file if missing. botlib is imported for
its registries only, plus `latest_age_s(table, con=...)` which accepts an
injected connection. monitor.py is imported for its threshold constants so
the dashboard and the hourly monitor can never disagree on what "stale" or
"silent" means; `monitor.run()` is never called (it opens RW connections
and pushes Telegram).

`db.PROD_DB` is read at call time, never cached at import — tests
monkeypatch `strategies.support.db.PROD_DB` (house convention).

Fleet-state precedence (the reason this dashboard exists — the
2026-08-15→24 doubling ran 9 days unnoticed):
  DUPLICATE (procscan instance_count > 1, or botlib's note)
  > MISSING (no heartbeat row) > DEAD (tick older than 3x interval)
  > WRITER_UNSEEN (fresh heartbeat, no scanned process — psutil blind spot)
  > DEGRADED (status != ok) > SILENT (eval older than BOT_EXPECTATIONS)
  > OK
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import botlib
import monitor
from strategies.support import db
from dashboard import notesparse, procscan

# feed first (everything depends on it), then bots alphabetically.
UNITS: tuple[str, ...] = ("feed",) + tuple(sorted(monitor.BOT_EXPECTATIONS))

# feed.py heartbeat notes name refresh_all() result keys, not always table
# names — map the aliases so the feeds grid can attribute failures.
_FETCH_ALIASES: dict[str, str] = {
    "ls_btc": "ca_long_short_ratio",
    "ls_eth": "ca_long_short_ratio",
    "fear_greed": "fear_greed_index",
}

_SENTINEL_YEAR = 2099          # trades.exit_time "no scheduled exit"


def _ro_con() -> sqlite3.Connection:
    """Read-only prod.db connection (WAL: readers never block writers)."""
    con = sqlite3.connect(f"file:{db.PROD_DB}?mode=ro", uri=True, timeout=2.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=1")
    con.execute("PRAGMA busy_timeout=2000")
    return con


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _alert(severity: str, code: str, text: str) -> dict:
    return {"severity": severity, "code": code, "text": text}


def _heartbeats(con: sqlite3.Connection) -> dict[str, dict]:
    try:
        rows = con.execute("SELECT * FROM bot_heartbeats").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r["name"]: dict(r) for r in rows}


# ─── Fleet state ──────────────────────────────────────────────────────────────

def _fleet(scanres: procscan.ScanResult, beats: dict[str, dict],
           now: datetime) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    alerts: list[dict] = []
    for unit in UNITS:
        hb = beats.get(unit)
        insts = scanres.instances.get(unit, [])
        n = len(insts)
        pids_scanned = scanres.all_pids(unit)

        def _age(iso):
            # clamp: writer clocks can be sub-second ahead of ours
            a = monitor._age_s(iso, now)
            return None if a is None else max(0.0, a)

        tick_age = _age(hb.get("last_tick_utc")) if hb else None
        interval = (hb.get("interval_s") or 60) if hb else 60
        fresh = tick_age is not None and tick_age <= 3 * interval
        eval_age = _age(hb.get("last_eval_utc")) if hb else None
        sig_age = _age(hb.get("last_signal_utc")) if hb else None
        note = (hb.get("note") or "") if hb else ""
        status = hb.get("status") if hb else None
        expectation = monitor.BOT_EXPECTATIONS.get(unit)

        if n > 1:
            state = "DUPLICATE"
            pid_desc = "; ".join(
                f"pids {'->'.join(str(p) for p in i.pids)} "
                f"(up {monitor._fmt_age(i.age_s)})" for i in insts)
            force = " — a cmdline carries --force-start (guard bypassed)" \
                if any("--force-start" in i.cmdline for i in insts) else ""
            alerts.append(_alert(
                "red", "DUPLICATE",
                f"DUPLICATE {unit}: {n} instances running [{pid_desc}] — "
                f"kill all but one (oldest listed first){force}"))
        elif hb is None:
            state = "MISSING"
            extra = f" ({n} process(es) visible, none has written yet)" if n else ""
            alerts.append(_alert("red", "MISSING",
                                 f"MISSING {unit}: no heartbeat row{extra}"))
        elif not fresh:
            state = "DEAD"
            extra = " — a process exists but is not ticking (hung?)" if n else ""
            alerts.append(_alert(
                "red", "DEAD",
                f"DEAD PROCESS {unit}: last tick {monitor._fmt_age(tick_age)} "
                f"(interval {interval}s){extra}"))
        elif "DUPLICATE INSTANCE" in note:
            # fresh row still carrying botlib's own detection — checked before
            # DEGRADED because the detector forces status='error' with this
            # note, and DUPLICATE is the more specific truth.
            state = "DUPLICATE"
            alerts.append(_alert("red", "DUPLICATE", f"DUPLICATE {unit}: {note}"))
        elif n == 0:
            state = "WRITER_UNSEEN"
            alerts.append(_alert(
                "amber", "WRITER_UNSEEN",
                f"WRITER UNSEEN {unit}: heartbeat is fresh but no matching "
                f"process found — other user / access denied / other machine?"))
        elif status != "ok":
            state = "DEGRADED"
            alerts.append(_alert("amber", "DEGRADED",
                                 f"DEGRADED {unit}: {status} — {note}"))
        elif expectation is not None and (eval_age is None
                                          or eval_age > expectation):
            state = "SILENT"
            alerts.append(_alert(
                "amber", "SILENT",
                f"SILENT BOT {unit}: last signal eval "
                f"{monitor._fmt_age(eval_age)} "
                f"(limit {monitor._fmt_age(expectation)})"))
        else:
            state = "OK"

        hb_pid = hb.get("pid") if hb else None
        hb_pid_seen = hb_pid is not None and hb_pid in pids_scanned
        if hb_pid is not None and n > 0 and not hb_pid_seen:
            alerts.append(_alert(
                "amber", "PID_MISMATCH",
                f"PID MISMATCH {unit}: heartbeat pid {hb_pid} not among "
                f"scanned pids {sorted(pids_scanned)} — possible unscannable "
                f"second instance"))

        rows.append({
            "unit": unit,
            "state": state,
            "instance_count": n,
            "instances": [dataclasses.asdict(i) for i in insts],
            "hb_pid_seen": hb_pid_seen,
            "expectation_s": expectation,
            "heartbeat": None if hb is None else {
                "last_tick_utc": hb.get("last_tick_utc"),
                "tick_age_s": None if tick_age is None else round(tick_age, 1),
                "interval_s": hb.get("interval_s"),
                "status": status,
                "note": note,
                "pid": hb_pid,
                "pid_known": hb_pid is not None,   # pre-migration rows: False
                "open_trades": hb.get("open_trades"),
                "last_eval_utc": hb.get("last_eval_utc"),
                "eval_age_s": None if eval_age is None else round(eval_age, 1),
                "last_signal_utc": hb.get("last_signal_utc"),
                "signal_age_s": None if sig_age is None else round(sig_age, 1),
            },
        })

    if scanres.legacy_bot_py:
        alerts.append(_alert(
            "red", "LEGACY_BOT_PY",
            f"LEGACY bot.py RUNNING (pids {scanres.legacy_bot_py}) — the "
            f"monolith embeds its own feed thread; double-fetch risk"))
    if scanres.access_denied:
        alerts.append(_alert(
            "amber", "SCAN_BLIND",
            f"process scan partially blind: {scanres.access_denied} python "
            f"process(es) unreadable — run the dashboard as the bots' user"))
    return rows, alerts


# ─── Data-health checks (mirrors monitor.py's cheap checks, same constants) ───

def _data_alerts(con: sqlite3.Connection, now: datetime) -> list[dict]:
    alerts: list[dict] = []

    for t in sorted(botlib.FRESHNESS_CONTRACTS):
        age = botlib.latest_age_s(t, con)
        limit = botlib.FRESHNESS_CONTRACTS[t][2]
        if age is None or age > limit:
            alerts.append(_alert(
                "red", "STALE_TABLE",
                f"STALE TABLE {t}: age {monitor._fmt_age(age)} "
                f"(contract {monitor._fmt_age(limit)})"))

    db_tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    classified = (set(botlib.FRESHNESS_CONTRACTS) | set(botlib.FROZEN_TABLES)
                  | botlib.GATED_TABLES | botlib.STATE_TABLES
                  | botlib.STATIC_TABLES)
    for t in sorted(db_tables - classified):
        alerts.append(_alert("red", "UNCLASSIFIED_TABLE",
                             f"UNCLASSIFIED TABLE {t}: contract it or declare "
                             f"it frozen/gated/state in botlib.py"))
    for t in sorted(classified - db_tables):
        alerts.append(_alert("amber", "GHOST_REGISTRY",
                             f"GHOST REGISTRY ENTRY {t}: classified in "
                             f"botlib.py but no such table in prod.db"))

    for table, window_s in monitor.RETENTION_LIMITS.items():
        age = botlib.latest_age_s(table, con)
        if age is None or age <= monitor.RETENTION_WARN_S:
            continue
        left_d = max(0.0, (window_s - age) / 86400)
        crit = age > monitor.RETENTION_CRIT_S
        alerts.append(_alert(
            "red" if crit else "amber", "HISTORY_BURN",
            f"HISTORY BURN ({'CRITICAL' if crit else 'warn'}) {table}: stale "
            f"{monitor._fmt_age(age)}, {left_d:.0f}d of upstream retention "
            f"left"))

    try:
        row = con.execute("SELECT MAX(date) FROM scheduled_events").fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is None or row[0] is None:
        alerts.append(_alert("amber", "EVENT_RUNWAY",
                             "EVENT RUNWAY scheduled_events: table "
                             "missing/empty — run fetch_events.py"))
    else:
        end = datetime.fromisoformat(str(row[0])).replace(tzinfo=timezone.utc)
        runway_d = (end - now).days
        if runway_d < monitor.EVENT_RUNWAY_DAYS:
            alerts.append(_alert(
                "amber", "EVENT_RUNWAY",
                f"EVENT RUNWAY scheduled_events ends {row[0]} ({runway_d}d)"))

    for name, limit in monitor.ARCHIVE_FILES.items():
        path = db.DATA_DIR / "archive" / name
        if not path.exists():
            alerts.append(_alert("amber", "ARCHIVE_STALE",
                                 f"ARCHIVE STALE {name}: file missing"))
            continue
        age = now.timestamp() - path.stat().st_mtime
        if age > limit:
            alerts.append(_alert(
                "amber", "ARCHIVE_STALE",
                f"ARCHIVE STALE {name}: last written "
                f"{monitor._fmt_age(age)} ago"))

    # Overdue open trades. Unlike monitor.py, no `variants.enabled=1` join —
    # enabled is NULL for every bot variant, which silently blinds monitor's
    # version of this check to the whole fleet.
    try:
        rows = con.execute(
            "SELECT id, strategy, strategy_variant, exit_time FROM trades "
            "WHERE execution_mode='paper' AND status='open'").fetchall()
    except sqlite3.OperationalError:
        rows = []
    for r in rows:
        exit_iso = r["exit_time"]
        if not exit_iso:
            continue
        try:
            if datetime.fromisoformat(exit_iso).year >= _SENTINEL_YEAR:
                continue                      # "no scheduled exit" sentinel
        except ValueError:
            continue
        age = monitor._age_s(exit_iso, now)
        if age is not None and age > monitor.OVERDUE_GRACE_S:
            alerts.append(_alert(
                "red", "OVERDUE_TRADE",
                f"OVERDUE TRADE {r['id']} ({r['strategy']}/"
                f"{r['strategy_variant']}): exit_time passed "
                f"{monitor._fmt_age(age)} ago"))
    return alerts


# ─── API payloads ─────────────────────────────────────────────────────────────

def overview(scanres: procscan.ScanResult | None = None) -> dict:
    now = _now()
    scanres = procscan.scan() if scanres is None else scanres
    con = _ro_con()
    try:
        beats = _heartbeats(con)
        data_alerts = _data_alerts(con, now)
    finally:
        con.close()
    fleet_rows, fleet_alerts = _fleet(scanres, beats, now)
    alerts = fleet_alerts + data_alerts
    return {
        "generated_utc": now.isoformat(timespec="seconds"),
        "alerts": alerts,
        "fleet": fleet_rows,
        "scan": {"scanned_python": scanres.scanned_python,
                 "access_denied": scanres.access_denied,
                 "legacy_bot_py": scanres.legacy_bot_py},
        "all_green": not any(a["severity"] == "red" for a in alerts),
        "all_clear": not alerts,
    }


def feeds() -> dict:
    now = _now()
    con = _ro_con()
    try:
        beats = _heartbeats(con)
        feed_hb = beats.get("feed") or {}
        note = feed_hb.get("note") or ""
        failing_raw = [s.strip() for s in note.split(":", 1)[1].split(",")] \
            if note.startswith("failed:") else []
        failing = {_FETCH_ALIASES.get(f.lower(), f) for f in failing_raw if f}

        tables = []
        for t in sorted(botlib.FRESHNESS_CONTRACTS):
            age = botlib.latest_age_s(t, con)
            limit = botlib.FRESHNESS_CONTRACTS[t][2]
            state = ("missing" if age is None
                     else "stale" if age > limit else "fresh")
            retention = None
            window_s = monitor.RETENTION_LIMITS.get(t)
            if window_s is not None:
                r_state = ("crit" if age is None or age > monitor.RETENTION_CRIT_S
                           else "warn" if age > monitor.RETENTION_WARN_S
                           else "ok")
                retention = {
                    "window_s": window_s,
                    "days_left": None if age is None
                    else round(max(0.0, (window_s - age) / 86400), 1),
                    "state": r_state,
                }
            tables.append({
                "table": t,
                "age_s": None if age is None else round(age, 1),
                "limit_s": limit,
                "ratio": None if age is None else round(age / limit, 3),
                "state": state,
                "failing_fetch": t in failing,
                "retention": retention,
            })

        try:
            row = con.execute(
                "SELECT MAX(date) FROM scheduled_events").fetchone()
            runway_end = None if row is None else row[0]
        except sqlite3.OperationalError:
            runway_end = None
    finally:
        con.close()

    archive = []
    for name, limit in monitor.ARCHIVE_FILES.items():
        path = db.DATA_DIR / "archive" / name
        age = (now.timestamp() - path.stat().st_mtime) if path.exists() else None
        archive.append({
            "file": name,
            "age_s": None if age is None else round(age, 1),
            "limit_s": limit,
            "state": ("missing" if age is None
                      else "stale" if age > limit else "ok"),
        })

    runway_days = None
    if runway_end is not None:
        end = datetime.fromisoformat(str(runway_end)).replace(
            tzinfo=timezone.utc)
        runway_days = (end - now).days
    return {
        "generated_utc": now.isoformat(timespec="seconds"),
        "tables": tables,
        "feed_note": note,
        "feed_note_failures": failing_raw,
        "gated": sorted(botlib.GATED_TABLES),
        "frozen_count": len(botlib.FROZEN_TABLES),
        "archive": archive,
        "event_runway": {"end_date": runway_end, "days": runway_days,
                         "state": ("ok" if runway_days is not None
                                   and runway_days >= monitor.EVENT_RUNWAY_DAYS
                                   else "warn")},
    }


# ─── Trades + candles (chart data) ────────────────────────────────────────────

_TF_SECONDS = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
_DEFAULT_BARS = {"15m": 3000, "1h": 2000, "4h": 1500, "1d": 3000}
# (asset, tf) -> source table + its native bar seconds; coarser tfs are
# bucketed in Python ((ts // secs) * secs, in-progress bucket dropped —
# same approach as ai_quant/chart.py::_aggregate). ETH stays on its Binance
# perp table for every tf to keep the venue consistent.
_CANDLE_SOURCES: dict[tuple[str, str], tuple[str, int]] = {
    ("BTC", "15m"): ("cd_futures_15m", 900),
    ("BTC", "1h"):  ("cd_futures_ohlcv", 3600),
    ("BTC", "4h"):  ("cd_futures_ohlcv", 3600),
    ("BTC", "1d"):  ("cd_futures_ohlcv", 3600),
    ("ETH", "15m"): ("cd_futures_eth_15m", 900),
    ("ETH", "1h"):  ("cd_futures_eth_15m", 900),
    ("ETH", "4h"):  ("cd_futures_eth_15m", 900),
    ("ETH", "1d"):  ("cd_futures_eth_15m", 900),
}


def _ts(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _bot_variants(con: sqlite3.Connection) -> dict[str, str]:
    """variant_id -> bot name, from variants.spec_json (written by
    botlib.ensure_bot_variant). LIKE 'bot_%' so new bots appear without a
    dashboard change."""
    try:
        rows = con.execute("SELECT id, spec_json FROM variants "
                           "WHERE id LIKE 'bot_%'").fetchall()
    except sqlite3.OperationalError:
        return {}
    out = {}
    for r in rows:
        bot = None
        try:
            bot = json.loads(r["spec_json"] or "{}").get("bot")
        except ValueError:
            pass
        out[r["id"]] = bot or r["id"]
    return out


def _last_prices(con: sqlite3.Connection) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for asset, table in (("BTC", "btc_1m"), ("ETH", "eth_1m")):
        try:
            row = con.execute(f"SELECT close FROM {table} "
                              f"ORDER BY open_time DESC LIMIT 1").fetchone()
            out[asset] = None if row is None else float(row[0])
        except sqlite3.OperationalError:
            out[asset] = None
    return out


_TRADE_COLS = ("id, asset, direction, strategy, strategy_variant, "
               "entry_time, exit_time, actual_exit_time, entry_price, "
               "exit_price, size_usdt, qty, leverage, pnl_usdt, pnl_pct, "
               "status, notes")


def _trade_dict(r: sqlite3.Row, variants: dict[str, str],
                last: dict[str, float | None]) -> dict:
    parsed = notesparse.parse(r["notes"])
    plan = parsed["plan"] or {}
    is_open = r["status"] == "open"

    timed_stop = plan.get("time_stop") or r["exit_time"]
    ts_ts = _ts(timed_stop)
    if timed_stop and datetime.fromtimestamp(
            ts_ts or 0, tz=timezone.utc).year >= _SENTINEL_YEAR:
        timed_stop = None

    r_multiple = None
    risk = plan.get("risk_price")
    if not is_open and r["pnl_usdt"] is not None and risk and r["qty"]:
        r_multiple = round(r["pnl_usdt"] / (r["qty"] * risk), 2)

    unrealized = None
    lp = last.get(r["asset"])
    if (is_open and lp is not None and r["strategy"] != "CARRY"
            and r["entry_price"] and r["qty"]):
        sign = 1 if r["direction"] == "LONG" else -1
        unrealized = round((lp - r["entry_price"]) * sign * r["qty"], 2)

    return {
        "id": r["id"],
        "bot": variants.get(r["strategy_variant"], r["strategy_variant"]),
        "variant": r["strategy_variant"],
        "asset": r["asset"],
        "direction": r["direction"],
        "strategy": r["strategy"],
        "status": r["status"],
        "entry_time": r["entry_time"],
        "entry_ts": _ts(r["entry_time"]),
        "entry_price": r["entry_price"],
        "qty": r["qty"],
        "size_usdt": r["size_usdt"],
        "leverage": r["leverage"],
        "timed_stop": timed_stop,
        "actual_exit_time": r["actual_exit_time"],
        "exit_ts": _ts(r["actual_exit_time"]),
        "exit_price": r["exit_price"],
        "pnl_usdt": r["pnl_usdt"],
        "pnl_pct": r["pnl_pct"],
        "r_multiple": r_multiple,
        "unrealized_usdt": unrealized,
        "plan": parsed["plan"],
        "decision": parsed["decision"],
        "exit_lines": parsed["exit_lines"],
        "notes_error": parsed["error"],
    }


def trades(scope: str = "recent") -> dict:
    """Bot-variant trades with parsed notes. scope: open | recent (open +
    last 50 closed) | all. The whole bot ledger is a few dozen rows, so it
    is fetched once and filtered in Python."""
    if scope not in ("open", "recent", "all"):
        raise ValueError(f"bad scope: {scope}")
    now = _now()
    con = _ro_con()
    try:
        variants = _bot_variants(con)
        rows = []
        if variants:
            ph = ",".join("?" * len(variants))
            rows = con.execute(
                f"SELECT {_TRADE_COLS} FROM trades "
                f"WHERE strategy_variant IN ({ph}) ORDER BY entry_time",
                list(variants)).fetchall()
        last = _last_prices(con)
    finally:
        con.close()

    open_t: list[dict] = []
    closed_t: list[dict] = []
    for r in rows:
        t = _trade_dict(r, variants, last)
        (open_t if t["status"] == "open" else closed_t).append(t)

    if scope == "open":
        sel = open_t
    elif scope == "recent":
        sel = open_t + closed_t[-50:]
    else:
        sel = open_t + closed_t
    sel.sort(key=lambda t: t["entry_ts"] or 0, reverse=True)
    return {"generated_utc": now.isoformat(timespec="seconds"),
            "trades": sel, "last_price": last}


def trade_by_id(trade_id: str) -> dict | None:
    con = _ro_con()
    try:
        variants = _bot_variants(con)
        row = con.execute(f"SELECT {_TRADE_COLS} FROM trades WHERE id=?",
                          (trade_id,)).fetchone()
        if row is None or row["strategy_variant"] not in variants:
            return None
        return _trade_dict(row, variants, _last_prices(con))
    finally:
        con.close()


def latest_entry(variant_id: str) -> dict | None:
    con = _ro_con()
    try:
        variants = _bot_variants(con)
        row = con.execute(
            f"SELECT {_TRADE_COLS} FROM trades WHERE strategy_variant=? "
            f"ORDER BY entry_time DESC LIMIT 1", (variant_id,)).fetchone()
        if row is None:
            return None
        return _trade_dict(row, variants, _last_prices(con))
    finally:
        con.close()


def candles(asset: str = "BTC", tf: str = "1h", bars: int = 0,
            after: int = 0) -> dict:
    asset = (asset or "BTC").upper()
    if (asset, tf) not in _CANDLE_SOURCES:
        raise ValueError(f"unsupported asset/tf: {asset}/{tf}")
    table, native = _CANDLE_SOURCES[(asset, tf)]
    secs = _TF_SECONDS[tf]
    bars = bars or _DEFAULT_BARS[tf]
    now_s = int(_now().timestamp())

    con = _ro_con()
    try:
        if after:
            start = int(after)          # re-fetch from the last known bar
        else:
            start = now_s - bars * secs
            # every open-trade entry dot must be on-chart (carry's entry is
            # weeks old) — extend the window past the oldest open entry.
            try:
                row = con.execute(
                    "SELECT MIN(entry_time) FROM trades WHERE status='open' "
                    "AND asset=? AND strategy_variant LIKE 'bot_%'",
                    (asset,)).fetchone()
            except sqlite3.OperationalError:
                row = None
            oldest = _ts(row[0]) if row and row[0] else None
            if oldest:
                start = min(start, oldest - 2 * 86400)
        rows = con.execute(
            f"SELECT timestamp, open, high, low, close FROM {table} "
            f"WHERE timestamp >= ? ORDER BY timestamp", (start,)).fetchall()
    finally:
        con.close()

    out: list[dict] = []
    if secs == native:
        out = [{"time": int(r["timestamp"]), "open": r["open"],
                "high": r["high"], "low": r["low"], "close": r["close"]}
               for r in rows]
    else:
        cur: dict | None = None
        for r in rows:
            b = (int(r["timestamp"]) // secs) * secs
            if cur is None or cur["time"] != b:
                if cur is not None:
                    out.append(cur)
                cur = {"time": b, "open": r["open"], "high": r["high"],
                       "low": r["low"], "close": r["close"]}
            else:
                cur["high"] = max(cur["high"], r["high"])
                cur["low"] = min(cur["low"], r["low"])
                cur["close"] = r["close"]
        if cur is not None:
            out.append(cur)
        while out and out[-1]["time"] + secs > now_s:   # in-progress bucket
            out.pop()

    return {"asset": asset, "tf": tf, "source": table, "bars": out,
            "last_time": out[-1]["time"] if out else None,
            "server_time": now_s}

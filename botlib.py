"""Shared plumbing for single-strategy bots, the feed daemon, and monitor.py.

Part of the 2026-07 bot-extraction architecture
(studies/material/plans/bot_extraction_plan.md): each strategy runs as its
own process against the shared market-data platform (prod.db). This module
is the whole "platform library" — deliberately one flat file:

  - freshness contracts + checks   every table a bot reads has a max age;
                                   bots refuse to evaluate on stale inputs
                                   (loud degradation, never a silent NaN
                                   gate-block — the CHENTO/okx lesson)
  - bot_heartbeats table           every process upserts a row per tick;
                                   monitor.py alerts on stale rows
  - WAL mode                       multi-process safety (feed + N bots +
                                   monitor all touch prod.db)
  - ensure_bot_variant             one variants row per bot = its ledger
                                   scope now, its sub-account at go-live
  - close_due_trades               scheduled-exit backstop (defense in
                                   depth behind each sleeve's own sweep)
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from strategies.support import clock, db, instance_guard

log = logging.getLogger("botlib")

# ─── Freshness contracts ──────────────────────────────────────────────────────
# table -> (timestamp column, multiplier to seconds, max age in seconds).
# Ages are measured latest-row vs wall clock. Limits leave headroom over the
# natural cadence (a 15m table is stale at 45m = 3 missed bars).

FRESHNESS_CONTRACTS: dict[str, tuple[str, float, int]] = {
    "cd_futures_15m":      ("timestamp", 1.0,   45 * 60),
    "cd_spot_15m":         ("timestamp", 1.0,   45 * 60),
    "cd_futures_ohlcv":    ("timestamp", 1.0,   2 * 3600 + 900),
    "cd_spot_binance":     ("timestamp", 1.0,   2 * 3600 + 900),
    "btc_1m":              ("open_time", 0.001, 10 * 60),
    "eth_1m":              ("open_time", 0.001, 10 * 60),
    "okx_perp_1h":         ("timestamp", 1.0,   3 * 3600),
    # The row stamped H is written when the H+1h snapshot exists (item 30), so
    # the newest stamp is 1-2 h old by design; 4 h leaves one missed poll.
    "cd_open_interest":    ("timestamp", 1.0,   4 * 3600),
    "ca_long_short_ratio": ("timestamp", 1.0,   26 * 3600),
    "cd_funding_rate":     ("timestamp", 1.0,   9 * 3600),
    "cd_funding_rate_eth": ("timestamp", 1.0,   9 * 3600),
    "fear_greed_index":    ("date",      1.0,   3 * 86400),
    # multi-asset chento ETH leg (revived from freeze 2026-08-23; same
    # cadence/limits as their BTC twins)
    "cd_futures_eth_15m":  ("timestamp", 1.0,   45 * 60),
    "okx_perp_eth_1h":     ("timestamp", 1.0,   3 * 3600),
    # Track D feeds (2026-09-06): gold leg, macro context, Deribit options.
    "paxg_spot_1h":        ("timestamp", 1.0,   2 * 3600 + 900),
    # Weekday cadence, and `date` is parsed as UTC midnight of the bar's own
    # day, so a row is already ~24h old when written. Fri bar + holiday Monday
    # reaches 120h, so 5 days left zero headroom for one missed poll: 6.
    "macro_daily":         ("date",      1.0,   6 * 86400),
    "deribit_dvol_daily":  ("timestamp", 1.0,   2 * 86400 + 3600),
    # Snapshots run at 00:05 and 08:05 UTC but rows are stamped at the floored
    # hour, so the long leg of the cycle (08:00 row -> next 00:05 write) is
    # 16h05m. 14h could never be satisfied; 18h clears it with poll slack.
    "deribit_options_daily": ("timestamp", 1.0, 18 * 3600),
    "deribit_options_instruments": ("last_seen_ts", 1.0, 2 * 86400),
    # Track D4 (2026-09-08): Coinbase US-venue spot, the premium study's leg.
    "coinbase_spot_1h":    ("timestamp", 1.0,   2 * 3600 + 900),
    # Track D6 (2026-09-08): OKX + Bybit funding settlements, the
    # funding-dispersion study's cross-venue legs. Settlements are 8-hourly
    # and the feed polls hourly, so the newest row is at most 8h + 1h old;
    # 10h leaves room for one missed poll before the alert fires.
    "okx_funding":         ("timestamp", 1.0,   10 * 3600),
    "bybit_funding":       ("timestamp", 1.0,   10 * 3600),
    # Track D5 (2026-09-08): Binance USDⓈ-M quarterly futures, the
    # perp-vs-quarterly basis study's leg. Klines are hourly (same limit as
    # the other 1h kline tables); the contracts table is refreshed once per
    # UTC day from exchangeInfo, so 2 days leaves room for one missed pull.
    "binance_quarterly_1h":        ("timestamp",    1.0, 2 * 3600 + 900),
    "binance_quarterly_contracts": ("last_seen_ts", 1.0, 2 * 86400),
    # Coinalyze liquidations (2026-09-18), moved out of FROZEN_TABLES when
    # data/sources/coinalyze.py gave them a live writer. The hourly table is
    # the one that must not be allowed to go stale: its source window is a
    # rolling ~89 days, so an hour not collected inside that window is gone
    # for good — which is exactly how 2026-05-24 → 2026-06-21 was lost. The
    # feed pulls hourly, so 3h leaves room for one missed poll. The daily
    # table has no such deadline (history reaches 2021) but is written once
    # per UTC day; its newest row is the still-forming day, rewritten each
    # pull, so 2 days plus an hour covers a missed run.
    "ca_liquidations":       ("timestamp", 1.0, 3 * 3600),
    "ca_liquidations_daily": ("timestamp", 1.0, 2 * 86400 + 3600),
}

# ─── Table classification ─────────────────────────────────────────────────────
# Every prod.db table is either contracted above or declared in exactly one of
# these registries; monitor.py alerts on any table in neither (and on registry
# entries whose table has vanished). Add new tables here in the same commit
# that creates them.

# Research snapshots with no live writer. Frozen deliberately — a stale age on
# these is not a failure. Value = freeze date + why.
FROZEN_TABLES: dict[str, str] = {
    "binance_agg_trades_1m":  "2026-05-23 — aggTrades research (chento Rule 1, closed)",
    "binance_agg_trades_5m":  "2026-05-23 — aggTrades research (chento Rule 1, closed)",
    "binance_agg_trades_15m": "2026-05-23 — aggTrades research (chento Rule 1, closed)",
    "bybit_perp_1h":          "2026-05-26 — cross-exchange study backfill",
    "bybit_perp_eth_1h":      "2026-05-26 — cross-exchange study backfill",
    "bybit_perp_op_1h":       "2026-05-26 — cross-exchange study backfill",
    "cd_futures_op_15m":      "2026-05-26 — multi-asset validation backfill",
    "cd_spot_5s":             "2026-06-07 — dwell-block study (Binance Vision bulk)",
    "cm_daily_metrics":       "2026-05-24 — CoinMetrics community one-shot",
    "cm_reference_rate_1h":   "2026-05-25 — CoinMetrics community one-shot",
    "okx_perp_op_1h":         "2026-05-26 — multi-asset validation backfill",
    "op_perp_1m":             "2026-05-19 — chento journal research; writer never committed",
    "screener_klines_daily":  "2026-05-23 — screener research one-shot, never a live feed",
    "screener_klines_1h":     "2026-05-23 — screener research one-shot, never a live feed",
    "screener_klines_5m":     "2026-06-05 — whale-absorption Phase 1 one-shot (1 asset)",
    "screener_klines_1m":     "2026-06-05 — whale-absorption Phase 1 one-shot (1 asset)",
    "screener_universe":      "2026-05-23 — screener research one-shot, never a live feed",
    "tv_btc_perp_15m":        "2026-05-25 — manual TradingView CSV import",
    "tv_btc_perp_1h":         "2026-05-25 — manual TradingView CSV import",
}

# Writers exist but are switched off (AI_QUANT_ENABLED=false since 2026-06).
# Move back to FRESHNESS_CONTRACTS in the same commit that re-enables the gate.
GATED_TABLES: set[str] = {"news_headlines", "cd_liquidations", "cd_dvol"}

# Bot/ledger state — covered by heartbeat, ledger-coherence and trade checks,
# not by row-age freshness.
STATE_TABLES: set[str] = {
    "ai_quant_decisions", "bot_heartbeats", "config", "fomc_observer",
    "sqlite_sequence", "trade_adjustments", "trades",
    "variant_daily_returns", "variant_events", "variants",
}

# Static reference data — has its own runway check in monitor.py instead of a
# row-age contract (built manually by fetch_events.py, populated years ahead).
STATIC_TABLES: set[str] = {"scheduled_events"}


def latest_age_s(table: str, con: sqlite3.Connection | None = None) -> float | None:
    """Age in seconds of the newest row in `table`, or None if the table is
    missing/empty. Uses the contract's column/unit spec."""
    ts_col, mult, _ = FRESHNESS_CONTRACTS[table]
    own = con is None
    if own:
        con = sqlite3.connect(str(db.PROD_DB))
    try:
        try:
            row = con.execute(f"SELECT MAX({ts_col}) FROM {table}").fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None or row[0] is None:
            return None
        if isinstance(row[0], str):
            # TEXT ISO column (fear_greed_index.date). Naive = UTC midnight.
            dt = datetime.fromisoformat(row[0])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            latest_s = dt.timestamp()
        else:
            latest_s = float(row[0]) * mult
        return clock.now_utc().timestamp() - latest_s
    finally:
        if own:
            con.close()


def stale_tables(tables: list[str] | None = None) -> dict[str, float | None]:
    """Contract breaches among `tables` (default: every contracted table).
    Returns {table: age_seconds_or_None}; empty dict means all fresh."""
    tables = list(FRESHNESS_CONTRACTS) if tables is None else tables
    out: dict[str, float | None] = {}
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        for t in tables:
            age = latest_age_s(t, con)
            limit = FRESHNESS_CONTRACTS[t][2]
            if age is None or age > limit:
                out[t] = age
    finally:
        con.close()
    return out


# ─── Multi-process safety ─────────────────────────────────────────────────────

def ensure_wal() -> None:
    """Switch prod.db to WAL journal mode (persistent, idempotent). Required
    now that feed daemon + bots + monitor are separate processes sharing the
    file; WAL lets readers proceed during writes."""
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        mode = con.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if mode.lower() != "wal":
            log.warning(f"journal_mode is {mode!r}, expected WAL")
    finally:
        con.close()


# ─── Heartbeats ───────────────────────────────────────────────────────────────

def init_heartbeat_schema() -> None:
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS bot_heartbeats (
                name            TEXT PRIMARY KEY,
                last_tick_utc   TEXT NOT NULL,
                last_eval_utc   TEXT,
                last_signal_utc TEXT,
                open_trades     INTEGER,
                interval_s      INTEGER,
                status          TEXT NOT NULL DEFAULT 'ok',
                note            TEXT
            )
        """)
        cols = {r[1] for r in con.execute(
            "PRAGMA table_info(bot_heartbeats)").fetchall()}
        if "pid" not in cols:
            con.execute("ALTER TABLE bot_heartbeats ADD COLUMN pid INTEGER")
        con.commit()
    finally:
        con.close()


def _pid() -> int:
    import os
    return os.getpid()


# Per-process memory of our own last heartbeat write, keyed by name — the
# basis of duplicate-instance detection in heartbeat().
_last_hb_write: dict[str, str] = {}


def heartbeat(name: str, *, status: str = "ok", note: str = "",
              interval_s: int | None = None,
              last_eval_utc: str | None = None,
              last_signal_utc: str | None = None,
              open_trades: int | None = None) -> bool:
    """Upsert this process's heartbeat row. `last_tick_utc` is always set to
    now; the optional fields keep their previous value when passed None.

    Duplicate-instance detection (2026-08-15 incident: two copies of every
    bot ran for 5 days, invisible because rows are name-keyed and the
    monitor saw one fresh row): if a DIFFERENT pid wrote this row since our
    own previous write, another live instance shares our name. We then
    force status='error' with a loud note — the monitor's existing
    status!=ok check surfaces it with no monitor changes — and return
    False. First write after startup never triggers (taking over a stale
    row is a normal restart).
    """
    now_iso = clock.now_utc().isoformat()
    my_pid = _pid()
    duplicate = False
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        try:
            row = con.execute(
                "SELECT pid, last_tick_utc FROM bot_heartbeats WHERE name=?",
                (name,)).fetchone()
        except sqlite3.OperationalError:      # pre-migration schema
            row = None
        prev_own = _last_hb_write.get(name)
        if (row is not None and prev_own is not None
                and row[0] is not None and row[0] != my_pid
                and (row[1] or "") > prev_own):
            duplicate = True
            status = "error"
            note = (f"DUPLICATE INSTANCE: pid {row[0]} also writing "
                    f"'{name}' (I am {my_pid}). Kill one. " + (note or ""))
            log.error(note.strip())
        con.execute("""
            INSERT INTO bot_heartbeats
                (name, last_tick_utc, last_eval_utc, last_signal_utc,
                 open_trades, interval_s, status, note, pid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                last_tick_utc   = excluded.last_tick_utc,
                last_eval_utc   = COALESCE(excluded.last_eval_utc,   bot_heartbeats.last_eval_utc),
                last_signal_utc = COALESCE(excluded.last_signal_utc, bot_heartbeats.last_signal_utc),
                open_trades     = COALESCE(excluded.open_trades,     bot_heartbeats.open_trades),
                interval_s      = COALESCE(excluded.interval_s,      bot_heartbeats.interval_s),
                status          = excluded.status,
                note            = excluded.note,
                pid             = excluded.pid
        """, (name, now_iso, last_eval_utc, last_signal_utc,
              open_trades, interval_s, status, note, my_pid))
        con.commit()
        _last_hb_write[name] = now_iso
    finally:
        con.close()
    # Turn the detection into a refusal rather than a note. Every runner
    # discarded this return value until 2026-09-12, which is why the August
    # doubling ran for nine days with the alarm already firing.
    if duplicate:
        instance_guard.stand_down(
            f"another process is writing the '{name}' heartbeat")
    else:
        instance_guard.resume()
    return not duplicate


def get_heartbeats() -> list[dict]:
    con = sqlite3.connect(str(db.PROD_DB))
    con.row_factory = sqlite3.Row
    try:
        try:
            rows = con.execute(
                "SELECT * FROM bot_heartbeats ORDER BY name").fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]
    finally:
        con.close()


# ─── Bot variant registration ─────────────────────────────────────────────────

def ensure_bot_variant(variant_id: str, *, short_name: str,
                       capital_usdt: float, bot_name: str,
                       notes: str = "") -> dict:
    """Idempotently register the bot's variants row and return it as a dict.

    One variant per bot = the bot's ledger scope in the shared trades table
    today, and its 1:1 exchange sub-account at go-live. `composition` is
    explicitly empty so the legacy orchestrator (if ever started) no-ops on
    this variant instead of dispatching sleeves into it.

    `kind` must satisfy the variants CHECK constraint
    ('full_portfolio'|'signal_overlay') — a single-strategy bot IS a full
    portfolio of one strategy, so 'full_portfolio' with spec.bot set.
    """
    from strategies.support import variant_registry
    variant_registry.init_schema()
    v = variant_registry.get_variant(variant_id)
    if v is None:
        variant_registry.register_variant(
            variant_id=variant_id,
            short_name=short_name,
            kind="full_portfolio",
            version="1.0",
            spec={"bot": bot_name, "composition": [],
                  "architecture": "single_strategy_bot"},
            status="paper",
            capital_usdt=capital_usdt,
            notes=notes or f"Single-strategy bot ({bot_name}). "
                            f"Extraction plan 2026-07: one bot = one variant "
                            f"= one future sub-account.",
            actor="bot",
        )
        v = variant_registry.get_variant(variant_id)
        log.info(f"registered bot variant {variant_id}")
    # Rows registered before `enabled` got its DEFAULT carry NULL, which the
    # monitor's overdue-trade join (v.enabled=1) silently excludes. A bot
    # that is running is enabled by definition — backfill on every start.
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        cur = con.execute(
            "UPDATE variants SET enabled=1 WHERE id=? AND enabled IS NULL",
            (variant_id,))
        con.commit()
        if cur.rowcount:
            log.info(f"variant {variant_id}: enabled NULL -> 1")
        # The config's SHORT_NAME is the bot's label in every variant list;
        # follow it when it changes (r4 -> "ETH windows", 2026-09-12) and
        # leave the same audit trail register_variant does.
        if v is not None and short_name and v.get("short_name") != short_name:
            con.execute("UPDATE variants SET short_name=? WHERE id=?",
                        (short_name, variant_id))
            con.commit()
            variant_registry._record_event(
                variant_id, "renamed", "bot",
                summary=f"short_name {v.get('short_name')!r} -> {short_name!r}")
            log.info(f"variant {variant_id}: short_name -> {short_name!r}")
        v = variant_registry.get_variant(variant_id)
    finally:
        con.close()
    return v


# ─── Scheduled-exit backstop ──────────────────────────────────────────────────

class BackstopRefused(Exception):
    """close_due_trades could not close every due trade.

    Raised only AFTER every due trade has been tried, so one bad trade never
    strands the others. `closed` holds the ids that did close; `refused`
    (no closer for the strategy) and `errors` (its price read or closer
    raised) hold (trade_id, strategy, reason) tuples. Runners catch it, keep
    the closed ids and mark the heartbeat 'error' rather than aborting the
    tick.
    """

    def __init__(self, closed: list[str], refused: list[tuple[str, str, str]],
                 errors: list[tuple[str, str, str]], *, variant_id: str = ""):
        self.closed = list(closed)
        self.refused = list(refused)
        self.errors = list(errors)
        self.variant_id = variant_id
        failed = "; ".join(f"{tid} {strategy}: {why}"
                           for tid, strategy, why in self.refused + self.errors)
        super().__init__(f"backstop left {len(self.refused) + len(self.errors)} "
                         f"due trade(s) open in {variant_id}: {failed}")


def close_due_trades(variant_id: str, now_utc: datetime | None = None, *,
                     closers: dict) -> list[str]:
    """Close this variant's open paper trades whose exit_time has passed.

    Defense in depth behind the sleeve's own sweep, which normally closes
    stop / target / time stop first. `closers` maps strategy name to that
    sleeve's OWN close function, called as closer(trade_id, price,
    'scheduled_exit'). So a backstop close books exactly what the sleeve's
    close books — cost, slippage, funding, ADX's stop resolver, CARRY's
    delta-neutral P&L. What it does not match is the price source and the
    label: it hands the closer get_current_price and reason
    'scheduled_exit'; a stop-path sleeve's close (ADX) may still re-price
    that to an earlier stop or to the due minute.

    Until 2026-09-14 this called trades.close_perp_trade with no overrides,
    so every strategy was booked at the trades.py defaults, 10 bp fee + 5 bp
    slippage + funding (BACKLOG 4.4). A due trade whose strategy has no
    closer is now REFUSED rather than booked at those defaults. A refusal, or
    a price read or closer that raises, never stops the next trade: every
    due trade is tried, then BackstopRefused names what stayed open and
    carries the ids that closed.
    """
    from strategies.support.price_feed import get_current_price

    now_utc = now_utc or clock.now_utc()
    con = sqlite3.connect(str(db.PROD_DB))
    con.row_factory = sqlite3.Row
    try:
        opens = con.execute(
            "SELECT id, asset, exit_time, strategy FROM trades "
            "WHERE strategy_variant = ? AND execution_mode = 'paper' "
            "  AND status = 'open'", (variant_id,)
        ).fetchall()
    finally:
        con.close()

    closed: list[str] = []
    refused: list[tuple[str, str, str]] = []
    errors: list[tuple[str, str, str]] = []
    for t in opens:
        exit_time = t["exit_time"]
        if not exit_time:
            continue
        try:
            exit_dt = datetime.fromisoformat(exit_time)
            if exit_dt.tzinfo is None:
                exit_dt = exit_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if now_utc < exit_dt:
            continue
        close = closers.get(t["strategy"])
        if close is None:
            refused.append((t["id"], t["strategy"],
                            "no closer for this strategy, refusing to book "
                            "it at the trades.py defaults"))
            continue
        try:
            # The price read sits inside the try too: a locked or broken 1m
            # table must not strand the other due trades either.
            price = get_current_price(t["asset"])
            if price is None:
                log.warning(f"[backstop] no price for {t['id']} {t['asset']} — skip")
                continue
            close(t["id"], price, "scheduled_exit")
        except Exception as e:  # noqa: BLE001 — the other due trades still close
            log.exception(f"[backstop] close failed for {t['id']} ({t['strategy']})")
            errors.append((t["id"], t["strategy"], repr(e)))
            continue
        closed.append(t["id"])
        log.info(f"[backstop] closed overdue {t['id']} ({t['strategy']})")
    if refused or errors:
        refusal = BackstopRefused(closed, refused, errors, variant_id=variant_id)
        log.error(f"[backstop] {refusal}")
        raise refusal
    return closed


def point_at_db_copy(path, *, diag_dir=None, botcfg=None) -> None:
    """Dry-run only: redirect every DB constant at a COPY of prod.db, refusing
    the live file, and redirect the sleeves' diagnostic JSONL alongside it.

    Shared by every bot runner so there is one definition of "dry run" rather
    than one per bot. Two things it must get right:

    * **Refuse prod.db.** A dry run writes trades; pointed at the live file it
      would inject rows into the paper ledger the fleet is accruing.
    * **Redirect the diagnostics.** Redirecting the DB alone is not isolation:
      the chento and short_squeeze sleeves append a JSONL that the dashboard
      reads, and the paths come from env vars the sleeve modules resolve at
      IMPORT. This is called from ``main()``, before the first tick imports a
      sleeve, which is the only window where setting them still takes effect.
      chento_v3_eth assigns ``CHENTO_V3_DIAG_PATH`` unconditionally at module
      import, so this must run after that — it does.

    Copy prod.db with ``sqlite3 .backup``, never ``shutil.copy``: eight
    processes write it every 60 s and it is in WAL, so a byte copy taken
    without its ``-wal`` is not a guaranteed-consistent snapshot.
    """
    import os
    from pathlib import Path

    from strategies.support import db
    target = Path(path).resolve()
    if target == Path(db.PROD_DB).resolve():
        raise SystemExit(f"--db {path} is the live prod.db; dry runs need a copy")
    if not target.exists():
        raise SystemExit(f"--db {path} does not exist")
    db.PROD_DB = db.DASH_DB = db.TRADER_DB = target
    # Imported only AFTER the repoint. Until 2026-09-13 trade_db was imported at
    # the top of this function and ran its DDL on import, so every "isolated"
    # dry run wrote to the live prod.db before reaching this line — including
    # tests/fixtures/repoint_baseline.py's refactor gate. trade_db no longer
    # writes on import, but the ordering stays so it never depends on that.
    from strategies.support import trade_db
    trade_db.DB_PATH = target

    diag = Path(diag_dir) if diag_dir else target.parent / "diagnostics"
    diag.mkdir(parents=True, exist_ok=True)
    # Sleeve-level diagnostics, resolved from env at sleeve import.
    os.environ["CHENTO_V3_DIAG_PATH"] = str(diag / "chento_v3_diag.jsonl")
    os.environ["SSQ_DIAG_PATH"] = str(diag / "short_squeeze_diag.jsonl")
    # Runner-level diagnostics. squeeze_bull and r4 append to
    # botcfg.DIAG_PATH directly — a module constant, not an env var — so the
    # env redirect above does not reach them. Without this a dry run appends
    # to the live JSONL the dashboard reads, which the step-3 gate caught.
    if botcfg is not None:
        orig = getattr(botcfg, "DIAG_PATH", None)
        if orig is not None:
            dest = diag / Path(str(orig)).name
            botcfg.DIAG_PATH = dest if isinstance(orig, Path) else str(dest)
        if getattr(botcfg, "LOGS_DIR", None) is not None:
            botcfg.LOGS_DIR = diag
    log.warning(f"DRY RUN against {target} (diagnostics -> {diag})")


def add_dry_run_flags(ap) -> None:
    """The --db / --sim-now pair, identical on every bot."""
    from pathlib import Path
    ap.add_argument("--db", type=Path, default=None,
                    help="Dry run against a COPY of prod.db (requires --once).")
    ap.add_argument("--sim-now", default=None,
                    help="ISO UTC timestamp to simulate (requires --once).")


def apply_dry_run_flags(ap, args, botcfg=None) -> None:
    """Validate and apply --db / --sim-now. Call from main() BEFORE the first
    tick, because the sleeve modules read their diagnostics env at import.
    Pass the bot's config module so runner-level diagnostics are redirected
    too."""
    from datetime import datetime, timezone
    if (args.db or args.sim_now) and not args.once:
        ap.error("--db / --sim-now are only valid with --once")
    if args.db:
        point_at_db_copy(args.db, botcfg=botcfg)
    if args.sim_now:
        from strategies.support import clock
        dt = datetime.fromisoformat(args.sim_now)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        clock.set_simulated_now(dt)
        log.warning(f"SIMULATED CLOCK {dt.isoformat()}")


def size_intent_fixed_r(intent, capital: float, *, risk_pct: float,
                        notional_max_x: float):
    """Fixed-R sizing shared by all bots: risk `risk_pct`% of capital over
    the stop distance the sleeve itself computed into intent.reason
    (`_entry_price` / `_stop_price`).

        notional = capital × risk_pct% / stop_pct   (≤ notional_max_x cap)

    open_paper_trade sizes as capital × alloc% × leverage, so alloc is
    pinned to 100% and the notional expressed through leverage. R-space
    results are unchanged by this; only $ per R changes. Returns
    (resized_intent, {stop_pct, notional, at_cap}).
    """
    import dataclasses
    reason = intent.reason or {}
    entry = float(reason["_entry_price"])
    stop = float(reason["_stop_price"])
    stop_pct = abs(entry - stop) / entry
    if stop_pct <= 0:
        raise ValueError(
            f"non-positive stop distance (entry={entry}, stop={stop})")
    notional = capital * (risk_pct / 100.0) / stop_pct
    capped = min(notional, notional_max_x * capital)
    resized = dataclasses.replace(
        intent, allocation_pct=100.0, leverage=capped / capital)
    return resized, {"stop_pct": stop_pct, "notional": capped,
                     "at_cap": capped < notional}


def count_open_trades(variant_id: str) -> int:
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        return con.execute(
            "SELECT COUNT(*) FROM trades WHERE strategy_variant = ? "
            "AND execution_mode = 'paper' AND status = 'open'",
            (variant_id,),
        ).fetchone()[0]
    finally:
        con.close()


def open_gross_usdt(variant_id: str) -> float:
    """Sum of open paper notional for the variant (current size after any
    partial adjustments, else the entry size). Bots with several windows use
    it as the co-fire budget input."""
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        row = con.execute(
            "SELECT COALESCE(SUM(COALESCE(current_size_usdt, size_usdt)), 0) "
            "FROM trades WHERE strategy_variant = ? "
            "AND execution_mode = 'paper' AND status = 'open'",
            (variant_id,),
        ).fetchone()
        return float(row[0] or 0.0)
    finally:
        con.close()

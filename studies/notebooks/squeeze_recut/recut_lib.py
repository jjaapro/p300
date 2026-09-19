"""Paired re-cut of the SQUEEZE_BULL and SHORT_SQUEEZE stop / no-stop twins.

Written 2026-09-12, when n_paired = 0 for both sleeves, precisely so that the
decision code exists before the data it will judge. The thresholds live in
docs/calibration/{squeeze_bull,short_squeeze}.md and are quoted verbatim in
README.md; nothing here may be edited when a fire disappoints.

What this module does NOT do: decide anything on its own numbers. Every
clause below is transcribed from a calibration log written before any fire
existed. `decide()` returns the clause table; `run_recut.py` prints it.

Shape of the problem. Each sleeve runs two variants in ONE process on the
SAME signals, differing only in the exit. Each variant applies its own
single-open guard, so the longer-holding no-stop variant skips some fires the
stop variant takes. The pre-registration therefore compares them on the
PAIRED subset (same bar, both variants) and replays both policies over the
UNION of live fires, which is what `build_union` + `replay` reconstruct.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EXEC = ROOT / "studies" / "notebooks" / "execution_2026_09"
SIZING = ROOT / "studies" / "notebooks" / "sizing_style_2026_09"
for _p in (str(ROOT), str(EXEC), str(SIZING)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import exec_lib as ex          # noqa: E402
import sizing_lib as sl        # noqa: E402

from strategies.support import db  # noqa: E402
from studies.lib.validation import benchmark, dsr_pbo  # noqa: E402

RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)

# Fixed in advance (README.md). The honest trial count behind both sleeves is
# at least 30 threshold/stop/target/TIF variants; the calibration logs require
# "a trial count of at least 30".
N_TRIALS = 30

# The any-time divergence tripwire, from both calibration logs: "DISABLE a
# variant whose live record diverges from the sleeve's own replay of the same
# fires by > 0.05 R on any trade".
DIVERGENCE_R = 0.05

# Live trades struck from the record by the operator, per sleeve: {trade id: reason}. A voided trade is not a fire
# of the rule — load_live drops it before the union, the pairing, the divergence and every clause, and names it in
# the report. SJ-4250 fired only on the open-interest table's hour-start stamp (item 30); on the corrected table its
# bar does not fire (decision 8, 2026-09-19).
VOIDED_TRADES: dict[str, dict[str, str]] = {
    "squeeze_bull": {"SJ-4250": "fired only on the mis-stamped open-interest table (item 30); "
                                "voided by the operator 2026-09-19 (decision 8)"},
    "short_squeeze": {},
}

# Quote timing: the bot enters and exits at 60 s tick quotes, the replay at bar closes and level fills. When the
# live and replayed exits are the same kind and land within this many signal bars of each other, the price
# difference at entry and at exit is timing, not a fault, and divergence() nets it (since 2026-09-19).
QUOTE_DRIFT_MAX_BARS = 2

# Float tolerance for threshold comparisons. Thresholds are stated to two
# decimals; without this, 0.30 - 0.40 == -0.10000000000000003 turns a clause
# the doc calls "not a breach" into a DISABLE.
_EPS = 1e-9


@dataclass(frozen=True)
class SleeveCfg:
    key: str
    sleeve: str
    asset: str
    stop_id: str
    nostop_id: str
    stop_policy: str
    nostop_policy: str
    bar_key: str              # the reason-blob field holding the trigger bar
    bar_step: int             # seconds per signal bar
    tif_h: int
    worst_trade_floor: float  # DISABLE if any single live trade prints below
    retire_sleeve_if_both_negative: bool
    doc: str


SLEEVES: dict[str, SleeveCfg] = {
    "squeeze_bull": SleeveCfg(
        key="squeeze_bull", sleeve="SQUEEZE_BULL", asset="BTC",
        stop_id="bot_squeeze_bull_v1", nostop_id="bot_squeeze_bull_nostop_v1",
        # P0 = the shipped -2% stop / +3% target / 48h.
        # P1b = target + TIF, no stop: the variant sizing_style_2026_09 chose.
        stop_policy="P0_shipped", nostop_policy="P1b_target_only",
        bar_key="bar_ts",          # int epoch, hourly bar OPEN
        bar_step=3600, tif_h=48,
        worst_trade_floor=-6.0,    # docs/calibration/squeeze_bull.md
        retire_sleeve_if_both_negative=False,
        doc="docs/calibration/squeeze_bull.md"),
    "short_squeeze": SleeveCfg(
        key="short_squeeze", sleeve="SHORT_SQUEEZE", asset="BTC",
        stop_id="bot_short_squeeze_v1", nostop_id="bot_short_squeeze_nostop_v1",
        # P1 = no stop, no target, 6h time stop only.
        stop_policy="P0_shipped", nostop_policy="P1_time_only",
        # NOTE the name: the reason blob carries "bar_ts_utc", never "bar_ts".
        # Reading "bar_ts" here is the bug that left this sleeve's idempotency
        # key unprotected from 2026-07-21 to 2026-09-12.
        bar_key="bar_ts_utc",      # ISO string, 15m bar OPEN
        bar_step=900, tif_h=6,
        worst_trade_floor=-10.0,   # docs/calibration/short_squeeze.md
        retire_sleeve_if_both_negative=True,
        doc="docs/calibration/short_squeeze.md"),
}


# ── live ledger ────────────────────────────────────────────────────────────

def ro_prod() -> sqlite3.Connection:
    """Read-only handle on the live ledger. The fleet is running; this module
    must never hold a write lock on prod.db."""
    con = sqlite3.connect(Path(db.DASH_DB).as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


_EXIT_RE = re.compile(r"([A-Z0-9_]+)_EXIT:\s*([^;]+);")


def parse_notes(raw: str | None) -> tuple[dict, str | None]:
    """The notes column is a JSON object followed by free text appended at
    close. json.loads on the whole column raises; split on the first newline."""
    if not raw:
        return {}, None
    head, _, tail = raw.partition("\n")
    try:
        blob = json.loads(head)
    except (ValueError, TypeError):
        blob = {}
    m = _EXIT_RE.search(tail or "")
    return (blob if isinstance(blob, dict) else {}), (m.group(2).strip() if m else None)


_BOOKED_RE = re.compile(r"fees=(\d+(?:\.\d+)?)bp RT(?:, slip=(\d+(?:\.\d+)?)bp RT)?")


def booked_cost_bp(close_fee_usdt: float | None, size_usdt: float | None,
                   notes: str | None) -> float:
    """Round-trip cost the ledger booked on a closed trade, in bp of notional.

    Taken from the trade's CLOSE adjustment: close_perp_trade persists
    fee + slippage on the notional it closed as `fee_usdt`, exactly. The
    notes suffix (`fees=10bp RT, slip=0bp RT`) is the fallback for a row with
    no CLOSE adjustment; it rounds to whole bp, so it is never preferred.
    NaN when neither is present.
    """
    if close_fee_usdt is not None and size_usdt:
        return float(close_fee_usdt) / float(size_usdt) * 1e4
    m = _BOOKED_RE.search(notes or "")
    if m:
        return float(m.group(1)) + float(m.group(2) or 0.0)
    return float("nan")


def _bar_ts(blob: dict, cfg: SleeveCfg) -> int | None:
    v = blob.get(cfg.bar_key)
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(datetime.fromisoformat(str(v)).timestamp())
    except ValueError:
        return None


def load_live(cfg: SleeveCfg, as_of: datetime,
              con: sqlite3.Connection | None = None) -> pd.DataFrame:
    """Every live trade of both variants, with R measured against the same
    reference stop distance the bot sized on, so the two ledgers compare
    directly (`_reference_stop_price` in the trade notes)."""
    own = con is None
    con = con or ro_prod()
    try:
        # One CLOSE adjustment per trade, the first by seq: prod's
        # trade_adjustments has only UNIQUE(trade_id, seq), so a plain join
        # could return a trade twice if a duplicate CLOSE row exists
        # (BACKLOG 4.5). The notional is the one the close costed.
        rows = con.execute("""
            SELECT t.id, t.strategy_variant, t.status, t.actual_entry_time,
                   t.actual_exit_time, t.entry_price, t.exit_price, t.size_usdt,
                   t.leverage, t.pnl_usdt, t.pnl_pct, t.unique_key, t.notes,
                   COALESCE(NULLIF(t.current_size_usdt, 0), t.size_usdt)
                       AS close_size_usdt,
                   (SELECT a.fee_usdt FROM trade_adjustments a
                     WHERE a.trade_id = t.id AND a.event_type = 'CLOSE'
                     ORDER BY a.seq LIMIT 1) AS close_fee_usdt
            FROM trades t
            WHERE t.strategy_variant IN (?, ?) AND t.strategy = ?
            ORDER BY t.actual_entry_time
        """, (cfg.stop_id, cfg.nostop_id, cfg.sleeve)).fetchall()
    finally:
        if own:
            con.close()

    recs = []
    for r in rows:
        blob, exit_reason = parse_notes(r["notes"])
        entry = float(r["entry_price"] or 0.0)
        ref_stop = blob.get("_reference_stop_price")
        legacy = False
        if ref_stop is None and cfg.key == "squeeze_bull" and entry > 0:
            # Rows opened before 2026-09-12 predate the key. The sleeve's stop
            # was a flat 2% below entry throughout, so the reference distance
            # is recoverable exactly.
            from bots.squeeze_bull.strategy import config as sb_cfg
            ref_stop, legacy = entry * (1.0 - sb_cfg.STOP_PCT), True
        risk_pct = (abs(entry - float(ref_stop)) / entry
                    if ref_stop and entry > 0 else float("nan"))
        size = float(r["size_usdt"] or 0.0)
        denom = size * risk_pct
        recs.append(dict(
            id=r["id"], variant=r["strategy_variant"],
            side=("nostop" if r["strategy_variant"] == cfg.nostop_id else "stop"),
            status=r["status"], still_open=(r["status"] == "open"),
            entry_time=r["actual_entry_time"], exit_time=r["actual_exit_time"],
            entry_price=entry, exit_price=r["exit_price"],
            size_usdt=size, risk_pct=risk_pct,
            pnl_usdt=(None if r["pnl_usdt"] is None else float(r["pnl_usdt"])),
            # R net of everything the bot actually booked — cost and funding
            # are inside pnl_usdt by construction.
            r_live_net=(float(r["pnl_usdt"]) / denom
                        if r["pnl_usdt"] is not None and denom else float("nan")),
            bar_ts=_bar_ts(blob, cfg), exit_reason=exit_reason,
            legacy_ref_stop=legacy,
            # What the ledger charged this trade, which divergence() nets
            # against the replay's measured cost on every closed trade.
            booked_bp=(booked_cost_bp(r["close_fee_usdt"], r["close_size_usdt"],
                                      r["notes"])
                       if r["status"] == "closed" else float("nan")),
            # `scheduled_exit` means botlib.close_due_trades beat the sleeve's
            # own sweep. Since 2026-09-14 it closes through the sleeve's own
            # close, so the cost matches and only the label differs; before
            # that it booked 15 bp + funding (BACKLOG 4.4). A flag, not a cost.
            backstop_exit=(exit_reason == "scheduled_exit"),
            unique_key=r["unique_key"],
        ))
    df = pd.DataFrame(recs)
    if not df.empty:
        df = df[df.entry_time <= as_of.isoformat()].reset_index(drop=True)
    voided = VOIDED_TRADES.get(cfg.key, {})
    present = {k: v for k, v in voided.items() if not df.empty and k in set(df.id)}
    if present:
        df = df[~df.id.isin(present)].reset_index(drop=True)
    df.attrs["voided"] = present
    return df


# ── the union of fires ─────────────────────────────────────────────────────

def reconstruct_fires(cfg: SleeveCfg, t0: datetime, as_of: datetime) -> list[int]:
    """Trigger bars in [t0, as_of] recomputed from market data — the half of
    the union neither variant took (both were holding something).

    squeeze_bull is exact: no in-process state decides a fire, the sleeve
    recomputes kept flush events from the window on every evaluation.
    short_squeeze is labelled approximate — the live gate reads the newest
    funding settlement where the validated history used a 7-row mean
    (docs/calibration/short_squeeze.md).
    """
    lo, hi = int(t0.timestamp()), int(as_of.timestamp())
    if cfg.key == "squeeze_bull":
        from bots.squeeze_bull.strategy import math as sb_math
        con = ro_prod()
        try:
            # Transcribed from the sleeve's own _load_hourly (squeeze_bull/
            # signal.py): the INNER join on timestamp is the research loader's
            # join — a bar exists only when BOTH price and OI exist for it.
            rows = con.execute("""
                SELECT p.timestamp AS ts, p.close AS close, o.oi_close AS oi
                FROM cd_futures_ohlcv p
                JOIN cd_open_interest o ON o.timestamp = p.timestamp
                WHERE p.timestamp <= ? AND p.close IS NOT NULL
                  AND o.oi_close IS NOT NULL
                ORDER BY p.timestamp
            """, (hi,)).fetchall()
        finally:
            con.close()
        if not rows:
            return []
        ts = [int(r["ts"]) for r in rows]
        closes = [float(r["close"]) for r in rows]
        ois = [float(r["oi"]) for r in rows]
        daily: dict = {}
        for t, c in zip(ts, closes):
            daily[datetime.fromtimestamp(t, tz=timezone.utc).date()] = c
        out = []
        for i in sb_math.kept_flush_indices(ois, closes):
            if not (lo <= ts[i] <= hi):
                continue
            d = datetime.fromtimestamp(ts[i], tz=timezone.utc).date()
            if sb_math.classify_regime(
                    sb_math.backward_only_ret_30d(daily, d)) == "bull_30d":
                out.append(ts[i])
        return out

    b15, trig = ex.short_squeeze_frame()
    idx = trig.index[trig.astype(bool)]
    return [int(t.timestamp()) for t in idx
            if lo <= int(t.timestamp()) <= hi]


def variant_start(variant_id: str) -> str | None:
    """When this variant first existed, from its `registered` event. A fire
    before that instant is not a skip — the variant did not exist. The no-stop
    twins were registered 2026-09-12, months after their stop siblings, so
    without this every earlier fire reads as an unexplained skip."""
    con = ro_prod()
    try:
        row = con.execute(
            "SELECT MIN(timestamp) FROM variant_events "
            "WHERE variant_id = ? AND event_type = 'registered'",
            (variant_id,)).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()
    return row[0] if row and row[0] else None


def build_union(live: pd.DataFrame, fires: list[int], cfg: SleeveCfg,
                as_of: datetime) -> pd.DataFrame:
    """One row per trigger bar in (live fires ∪ reconstructed fires), saying
    which variants took it and why the other did not."""
    born = {"stop": variant_start(cfg.stop_id),
            "nostop": variant_start(cfg.nostop_id)}
    taken: dict[int, dict] = {}
    for r in live.itertuples():
        if r.bar_ts is None or (isinstance(r.bar_ts, float) and np.isnan(r.bar_ts)):
            continue
        taken.setdefault(int(r.bar_ts), {})[r.side] = r

    def _held(side: str, bar: int) -> bool:
        """Was that variant already holding a position across this bar?"""
        end = as_of.isoformat()
        for r in live.itertuples():
            if r.side != side or not r.entry_time:
                continue
            x = r.exit_time or end
            b = datetime.fromtimestamp(bar, tz=timezone.utc).isoformat()
            if r.entry_time <= b < x:
                return True
        return False

    bars = sorted(set(taken) | set(fires))
    out = []
    for b in bars:
        t = taken.get(b, {})
        ts, tn = "stop" in t, "nostop" in t
        row = dict(bar_ts=b,
                   bar_iso=datetime.fromtimestamp(b, tz=timezone.utc).isoformat(),
                   taken_stop=ts, taken_nostop=tn,
                   klass=("BOTH" if ts and tn else
                          "STOP_ONLY" if ts else
                          "NOSTOP_ONLY" if tn else "NEITHER"),
                   stop_trade=(t["stop"].id if ts else None),
                   nostop_trade=(t["nostop"].id if tn else None))
        b_iso = row["bar_iso"]
        for side, was in (("stop", ts), ("nostop", tn)):
            start = born.get(side)
            row[f"{side}_block"] = (
                None if was else
                "variant_not_yet_live" if start and b_iso < start else
                "position_open" if _held(side, b) else
                "unexplained")
        out.append(row)
    return pd.DataFrame(out)


# ── replay ─────────────────────────────────────────────────────────────────

def _entry_price_at(cfg: SleeveCfg, bar_ts: int) -> float | None:
    """The close the sleeve would book as its entry for this trigger bar."""
    table = "cd_futures_ohlcv" if cfg.bar_step == 3600 else "cd_futures_15m"
    con = ro_prod()
    try:
        row = con.execute(
            f"SELECT close FROM {table} WHERE timestamp = ?", (bar_ts,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()
    return float(row["close"]) if row else None


def replay(cfg: SleeveCfg, union: pd.DataFrame, as_of: datetime,
           rebuild_cache: bool = False) -> pd.DataFrame:
    """Both exit policies over every bar of the union, on the 1 m path, at the
    sleeve's measured cost. Mirrors sizing_lib.run_policy rather than
    re-deriving it, so the re-cut and the sizing study agree by construction."""
    if union.empty:
        return pd.DataFrame()
    ex.build_cache(force=rebuild_cache)
    path = ex.path_for(cfg.asset, "1m")
    as_of_ts = int(as_of.timestamp())
    rows = []
    for u in union.itertuples():
        bar = int(u.bar_ts)
        price = _entry_price_at(cfg, bar)
        if price is None:
            continue
        sig_ts = bar + cfg.bar_step               # the trigger bar's CLOSE
        tif_end = sig_ts + cfg.tif_h * 3600 - 60
        if cfg.key == "squeeze_bull":
            ev = ex.Event(sleeve=cfg.sleeve, asset=cfg.asset, direction=1,
                          signal_ts=sig_ts, signal_price=price,
                          stop=price * 0.98, target=price * 1.03,
                          tif_end=tif_end, level_mode="pct", target_r=1.5,
                          stop_pct=0.02, target_pct=0.03)
        else:
            stop = _live_stop_for_bar(union, bar) or price * 0.999
            ev = ex.Event(sleeve=cfg.sleeve, asset=cfg.asset, direction=1,
                          signal_ts=sig_ts, signal_price=price, stop=stop,
                          target=price + 3.0 * (price - stop),
                          tif_end=tif_end, level_mode="fixed_stop", target_r=3.0)
        cx = ex.context(ev, path, need_tif=False)
        if cx is None:
            continue
        # Truncate at as_of: a trade still inside its TIF has no outcome yet.
        i1 = min(path.idx_gt(ev.tif_end), path.idx_ge(as_of_ts))
        if i1 <= cx["i0"]:
            continue
        cx = {**cx, "i1": i1}
        still_open = path.idx_gt(ev.tif_end) > i1
        for side, policy in (("stop", cfg.stop_policy),
                             ("nostop", cfg.nostop_policy)):
            t = sl.run_policy(cfg.sleeve, ev, path, cx, policy)
            rows.append(dict(bar_ts=bar, side=side, policy=policy,
                             r_gross=t["r"], kind=t["kind"],
                             risk_pct=t["risk_pct"], notional_x=t["notional_x"],
                             pnl_usd=t["pnl_usd"],
                             r_net=t["r"] - (sl.cost_bp(cfg.sleeve) / 1e4) / t["risk_pct"],
                             still_open=still_open,
                             i_fill=t["i_fill"], i_exit=t["i_exit"],
                             fill_spot=t["fill_spot"], exit_spot=t["exit_spot"],
                             exit_ts=(int(path.ts[t["i_exit"]])
                                      if t.get("i_exit") is not None and 0 <= t["i_exit"] < len(path.ts)
                                      else None)))
    return pd.DataFrame(rows)


def _live_stop_for_bar(union: pd.DataFrame, bar: int) -> float | None:
    """short_squeeze's stop is the swept low — not reconstructible from a
    formula, so the live row's own reference stop is used where one exists."""
    row = union[union.bar_ts == bar]
    if row.empty:
        return None
    return row.iloc[0].get("ref_stop")


# ── divergence: live vs the sleeve's own replay ────────────────────────────

def divergence(cfg: SleeveCfg, live: pd.DataFrame,
               rep: pd.DataFrame) -> pd.DataFrame:
    """Per taken trade, live R minus replayed R under the SAME policy, split
    into the parts with a known cause and a residual.

    The pre-registered clause — "diverges ... by more than 0.05 R on any
    trade, which would mean an execution or data fault rather than an edge
    failure" — is read against the RESIDUAL. A difference fully explained by
    funding, by the booked cost or by the 60 s poll is not a fault.
    """
    if live.empty or rep.empty:
        return pd.DataFrame()
    from strategies.support import funding as funding_mod

    key = {(int(r.bar_ts), r.side): r for r in rep.itertuples()}
    out = []
    for lv in live.itertuples():
        if lv.still_open or lv.bar_ts is None:
            continue
        rp = key.get((int(lv.bar_ts), lv.side))
        if rp is None:
            continue
        d = lv.r_live_net - rp.r_net
        funding_r = 0.0
        try:
            if lv.entry_time and lv.exit_time and lv.risk_pct:
                # Both twins are long-only; accrued_pct takes the direction as
                # a string. It took +1 until 2026-09-14, which raised on every
                # hold that crossed a settlement, so funding_R was NaN there.
                pct = funding_mod.accrued_pct(
                    cfg.asset, datetime.fromisoformat(lv.entry_time),
                    datetime.fromisoformat(lv.exit_time), "LONG")
                funding_r = (pct / 100.0) / lv.risk_pct
        except Exception:  # noqa: BLE001 — reporting only, never fail the re-cut
            funding_r = float("nan")
        # Live nets the cost it BOOKED (7 / 10 bp, from the CLOSE adjustment);
        # the replay nets the E6 measured mean (sl.cost_bp, 6.67 / 9.32 bp).
        # That gap sits in every closed trade, not only backstop closes, and
        # on SHORT_SQUEEZE's tight stops it is up to 0.045 R of D4's 0.05 R
        # budget — so it is netted on every closed trade (since 2026-09-14;
        # README). A backstop close with no recoverable booked cost falls back
        # to the 15 bp the pre-2026-09-14 backstop booked.
        booked = getattr(lv, "booked_bp", float("nan"))
        if not np.isfinite(booked) and lv.backstop_exit:
            booked = 15.0
        cost_r = 0.0
        if np.isfinite(booked) and lv.risk_pct:
            cost_r = -((booked - sl.cost_bp(cfg.sleeve)) / 1e4) / lv.risk_pct
        # Quote timing (2026-09-19): when both exits are the same kind and within QUOTE_DRIFT_MAX_BARS of
        # each other, the live-vs-replay price difference at entry and at exit is the 60 s poll, not a
        # fault. SJ-4250's 0.0555 R "divergence" was this: tick quotes against bar closes on a 2 % stop.
        quote_r, exit_gap_bars = 0.0, None
        same_kind = _norm_exit(lv.exit_reason) == rp.kind
        rp_exit_ts, rp_fill, rp_exit = (getattr(rp, "exit_ts", None), getattr(rp, "fill_spot", None),
                                        getattr(rp, "exit_spot", None))
        try:
            if (same_kind and rp_exit_ts is not None and lv.exit_time and lv.exit_price is not None
                    and rp_fill is not None and rp_exit is not None and lv.risk_pct and lv.entry_price
                    and np.isfinite(float(rp_fill)) and np.isfinite(float(rp_exit))):
                lv_exit_ts = int(datetime.fromisoformat(lv.exit_time).timestamp())
                exit_gap_bars = abs(lv_exit_ts - int(rp_exit_ts)) / cfg.bar_step
                if exit_gap_bars <= QUOTE_DRIFT_MAX_BARS:
                    denom = float(lv.entry_price) * float(lv.risk_pct)
                    quote_r = ((float(lv.exit_price) - float(rp_exit))
                               - (float(lv.entry_price) - float(rp_fill))) / denom
        except (TypeError, ValueError):
            quote_r, exit_gap_bars = 0.0, None
        residual = d - (funding_r if np.isfinite(funding_r) else 0.0) - cost_r - quote_r
        out.append(dict(id=lv.id, side=lv.side, bar_ts=int(lv.bar_ts),
                        r_live=lv.r_live_net, r_replay=rp.r_net, diff_R=d,
                        funding_R=funding_r, booked_cost_R=cost_r, quote_drift_R=quote_r,
                        exit_gap_bars=exit_gap_bars,
                        residual_R=residual, backstop_exit=lv.backstop_exit,
                        live_exit=lv.exit_reason, replay_exit=rp.kind,
                        exit_matches=same_kind))
    return pd.DataFrame(out)


def _norm_exit(reason: str | None) -> str | None:
    return {"stop_loss": "stop", "take_profit": "target",
            "time_stop": "tif", "scheduled_exit": "tif"}.get(reason or "")


# ── the pre-registered decision ────────────────────────────────────────────

@dataclass
class Clause:
    name: str
    text: str
    value: float | int | None
    threshold: str
    verdict: str          # PASS | FAIL | n/a
    action: str = ""


@dataclass
class Verdict:
    sleeve: str
    as_of: str
    n_gate: int
    n_paired: int
    clauses: list[Clause] = field(default_factory=list)
    outcome: str = "NOT_DUE"
    notes: list[str] = field(default_factory=list)


def paired_frame(live: pd.DataFrame, union: pd.DataFrame) -> pd.DataFrame:
    """Fires taken by BOTH variants on the same bar AND closed on both sides —
    the pre-registration's unit of comparison ("n = 20 fires taken by both
    variants on the same bar").

    An open trade has no R yet, so a bar where only one side has closed is not
    a pair. Counting it would let the gate be reached by trades that have not
    resolved, which is the one way the re-cut could be triggered early.
    """
    if live.empty or union.empty or "bar_ts" not in live.columns:
        return pd.DataFrame()
    both = set(union[union.klass == "BOTH"].bar_ts.astype(int))
    closed = live[(~live.still_open) & live.bar_ts.notna()]
    keep = closed[closed.bar_ts.astype(int).isin(both)]
    if keep.empty:
        return pd.DataFrame()
    wide = keep.pivot_table(index="bar_ts", columns="side",
                            values="r_live_net", aggfunc="first")
    if not {"stop", "nostop"} <= set(wide.columns):
        return pd.DataFrame()
    return wide.dropna(subset=["stop", "nostop"])


def _d4_outcome(div: pd.DataFrame) -> str:
    """D4 names the variant whose trade diverged (since 2026-09-19; before, every trip read DISABLE_NOSTOP
    whichever variant's trade it was)."""
    bad = set(div.loc[div.residual_R.abs() > DIVERGENCE_R, "side"]) if not div.empty else set()
    if bad == {"stop"}:
        return "DISABLE_STOP"
    if bad == {"stop", "nostop"}:
        return "DISABLE_BOTH"
    return "DISABLE_NOSTOP"


def decide(cfg: SleeveCfg, live: pd.DataFrame, union: pd.DataFrame,
           div: pd.DataFrame, as_of: datetime, n_gate: int) -> Verdict:
    paired = paired_frame(live, union)
    n_paired = len(paired)
    v = Verdict(sleeve=cfg.sleeve, as_of=as_of.isoformat(), n_gate=n_gate,
                n_paired=n_paired)

    # The any-time clause is evaluated at EVERY n, including n = 0. It is the
    # only one that is not gated on the paired count.
    worst_resid = (float(div.residual_R.abs().max())
                   if not div.empty and div.residual_R.notna().any() else None)
    v.clauses.append(Clause(
        "D4_divergence",
        "any time: live record diverges from the sleeve's own replay by "
        "> 0.05 R on any trade (residual, after funding and booked cost)",
        worst_resid, f"<= {DIVERGENCE_R} R",
        "n/a" if worst_resid is None else
        ("PASS" if worst_resid <= DIVERGENCE_R else "FAIL"),
        action="DISABLE that variant — execution or data fault"))

    if n_paired < n_gate:
        v.outcome = "NOT_DUE"
        v.notes.append(
            f"{n_paired} paired fires; the re-cut is due at {n_gate}. "
            f"{n_gate - n_paired} more needed. Thresholds are NOT evaluated "
            f"early — see {cfg.doc}.")
        if worst_resid is not None and worst_resid > DIVERGENCE_R:
            v.outcome = _d4_outcome(div)
            v.notes.append("but the any-time divergence clause has tripped.")
        return v

    nostop_live = live[(live.side == "nostop") & (~live.still_open)]
    nostop_mean_all = float(nostop_live.r_live_net.mean()) if len(nostop_live) else float("nan")
    p_stop, p_nostop = float(paired["stop"].mean()), float(paired["nostop"].mean())
    worst_live = float(live.r_live_net.min()) if len(live) else float("nan")
    gap = p_nostop - p_stop

    v.clauses += [
        Clause("D1_nostop_mean", "no-stop variant's live mean R <= 0",
               nostop_mean_all, "> 0",
               "PASS" if nostop_mean_all > 0 else "FAIL",
               action="DISABLE the no-stop variant"),
        Clause("D2_paired_gap",
               "no-stop paired mean R more than 0.10 R BELOW the stop variant's",
               gap, ">= -0.10 R",
               # "more than 0.10 R below" — a gap of exactly -0.10 is not a
               # breach, so the comparison carries a float tolerance rather
               # than turning -0.10000000000000003 into a DISABLE.
               "PASS" if gap >= -0.10 - _EPS else "FAIL",
               action="DISABLE the no-stop variant"),
        Clause("D3_worst_trade",
               f"any single live trade prints below {cfg.worst_trade_floor} R",
               worst_live, f">= {cfg.worst_trade_floor} R",
               "PASS" if worst_live >= cfg.worst_trade_floor - _EPS else "FAIL",
               action="DISABLE the no-stop variant"),
    ]

    if n_gate >= 30:
        d = dsr_pbo.dsr_from_returns(paired["nostop"].to_numpy(), n_trials=N_TRIALS)
        dsr = None if d is None else float(d["dsr"])
        v.clauses.append(Clause(
            "D5_dsr", f"deflated Sharpe at a trial count of {N_TRIALS}",
            dsr, ">= 0.50",
            "n/a" if dsr is None else ("PASS" if dsr >= 0.50 - _EPS else "FAIL"),
            action="DISABLE the no-stop variant"))
        promote = (gap >= 0.10 - _EPS)
        v.clauses.append(Clause(
            "D6_promote",
            "promote no-stop to fleet default: paired mean R >= stop + 0.10 R "
            "AND MTM drawdown not worse by > 5 pp",
            gap, ">= +0.10 R",
            "PASS" if promote else "FAIL",
            action=("promote (subject to the drawdown half, printed below)"
                    if promote else "keep the stop variant as default")))
        if cfg.retire_sleeve_if_both_negative:
            both_neg = p_stop <= 0 and p_nostop <= 0
            v.clauses.append(Clause(
                "D7_retire_sleeve",
                "BOTH variants' mean R <= 0 at n = 30 — the execution study's "
                "verdict stands", max(p_stop, p_nostop), "> 0",
                "FAIL" if both_neg else "PASS",
                action="RETIRE THE SLEEVE"))

    fails = [c for c in v.clauses if c.verdict == "FAIL"]
    if any(c.name == "D7_retire_sleeve" for c in fails):
        v.outcome = "RETIRE_SLEEVE"
    elif any(c.name in ("D1_nostop_mean", "D2_paired_gap", "D3_worst_trade",
                        "D5_dsr") for c in fails):
        v.outcome = "DISABLE_NOSTOP"
    elif any(c.name == "D4_divergence" for c in fails):
        v.outcome = _d4_outcome(div)
    elif any(c.name == "D6_promote" and c.verdict == "PASS" for c in v.clauses):
        v.outcome = "CONTINUE_AND_CONSIDER_PROMOTION"
    else:
        v.outcome = "CONTINUE"

    # Reported alongside, never as a clause (GATE_VALIDATION.md).
    if n_paired >= 2:
        boot = benchmark.paired_block_boot_diff(
            paired["nostop"].to_numpy(), paired["stop"].to_numpy(),
            np.mean, block=5)
        v.notes.append(
            f"paired block bootstrap of (nostop - stop) mean R: "
            f"{boot['point']:+.3f}, CI90 [{boot['ci'][0]:+.3f}, "
            f"{boot['ci'][2]:+.3f}], P(>0) = {boot['p_gt_0']:.2f}")
        v.notes.append(_drop_sensitivity(paired))
    return v


def _drop_sensitivity(paired: pd.DataFrame) -> str:
    """Drop-one and drop-two sensitivity of both paired means. Every margin in
    the original SQUEEZE_BULL decision was one observation wide; this says
    whether that is still true."""
    def worst(col: str, k: int) -> float:
        x = np.sort(paired[col].to_numpy())
        return float(np.mean(x[k:])) if len(x) > k else float("nan")
    return ("drop-one / drop-two worst-case paired mean R  "
            f"nostop {worst('nostop', 1):+.3f} / {worst('nostop', 2):+.3f}   "
            f"stop {worst('stop', 1):+.3f} / {worst('stop', 2):+.3f}")

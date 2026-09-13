"""Per-bot info for the dashboard: registry, live config values, curated
cards, calibration docs, gate diagnostics.

Hybrid content model (user decision, 2026-08-24): the NUMBERS are imported
live from each bot's config modules so they can never drift from what runs;
the MECHANISM prose is a curated card in dashboard/cards/<bot>.md; the
authoritative change history is docs/calibration/<x>.md rendered inline.
Param selection is curated by hand (named constants, grouped) — never a
dir() dump; forensic leftovers like the disabled LADDER_* block stay out.

All imported config modules are pure constants (verified 2026-08-24). The
chento sleeve config resolves its asset from CHENTO_V3_ASSET at import
(default BTC) — imported once for the BTC view; the ETH view derives from
its _ASSET_TABLES map plus the eth bot config. Never mutate env or reload.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from dashboard import queries

REPO = Path(__file__).resolve().parents[1]
CARDS_DIR = Path(__file__).resolve().parent / "cards"
CALIB_DIR = REPO / "docs" / "calibration"

BOTS: dict[str, dict] = {
    "chento_v3": {
        "display": "Chento Triple v3 (BTC)",
        "variant_id": "bot_chento_v3_v1",
        "asset": "BTC",
        "card": "chento_v3.md",
        "calibration": "chento_triple_v3.md",
        "diag": REPO / "bots" / "chento_v3" / "logs" / "diag.jsonl",
        "cadence_note": "evaluates every 15m bar; ~10–20 triples/yr",
    },
    "chento_v3_eth": {
        "display": "Chento Triple v3 (ETH)",
        "variant_id": "bot_chento_v3_eth",
        "asset": "ETH",
        "card": "chento_v3_eth.md",
        "calibration": "chento_triple_v3.md",
        "diag": REPO / "bots" / "chento_v3_eth" / "logs" / "diag.jsonl",
        "cadence_note": "evaluates every 15m bar; live since 2026-08-23",
    },
    "short_squeeze": {
        "display": "Short Squeeze (BTC)",
        "variant_id": "bot_short_squeeze_v1",
        # Second paper variant on the same signals, no stop (2026-09-12).
        "variant_ids": ["bot_short_squeeze_v1", "bot_short_squeeze_nostop_v1"],
        "asset": "BTC",
        "card": "short_squeeze.md",
        "calibration": "short_squeeze.md",
        "diag": REPO / "bots" / "short_squeeze" / "logs" / "diag.jsonl",
        "cadence_note": ("macro-gated: setup days are ~3.7% of all days; "
                         "multi-month droughts are designed behavior "
                         "(regime-complement to CARRY)"),
    },
    "squeeze_bull": {
        "display": "Squeeze Bull (BTC OI flush)",
        "variant_id": "bot_squeeze_bull_v1",
        # Second paper variant on the same signals, no stop (2026-09-12).
        "variant_ids": ["bot_squeeze_bull_v1", "bot_squeeze_bull_nostop_v1"],
        "asset": "BTC",
        "card": "squeeze_bull.md",
        "calibration": "squeeze_bull.md",
        "diag": REPO / "bots" / "squeeze_bull" / "logs" / "diag.jsonl",
        "cadence_note": ("bull-regime only: ~25 fires/yr on average, and ZERO "
                         "outside a bull regime (28% of recent days). Silence "
                         "in a flat or bear tape is correct behaviour"),
    },
    "adx": {
        "display": "ADX S-003 T2 (BTC)",
        "variant_id": "bot_adx_v1",
        "asset": "BTC",
        "card": "adx.md",
        "calibration": "adx.md",
        "diag": None,
        "cadence_note": "daily entry decision; continuous trail sweep",
    },
    "carry": {
        "display": "Carry S-078 (BTC)",
        "variant_id": "bot_carry_v1",
        "asset": "BTC",
        "card": "carry.md",
        "calibration": "carry.md",
        "diag": None,
        "cadence_note": "daily funding decision; positions held for weeks",
    },
    "r4": {
        "display": "R4 calendar (ETH windows)",
        "variant_id": "bot_r4_v1",
        "asset": "ETH",
        "card": "r4.md",
        "calibration": "r4.md",
        "diag": REPO / "bots" / "r4" / "logs" / "diag.jsonl",
        "cadence_note": ("fires only inside the Tue/Wed/Fri ETH windows of "
                         "days 1-14 (BTC windows disabled 2026-09-12); 0-2 "
                         "positions/day, ~6 fires/month max, none in bear "
                         "regimes; silence on other days is designed behavior"),
    },
}


def _p(group: str, name: str, value, unit: str = "",
       source: str = "") -> dict:
    return {"group": group, "name": name, "value": value, "unit": unit,
            "source": source}


def params(bot: str) -> list[dict]:
    """Curated live parameter table for one bot. Values are read from the
    imported config modules at call time — they cannot drift from the code
    that runs (imports are cached; configs are constants)."""
    if bot in ("chento_v3", "chento_v3_eth"):
        from bots.chento_v3.strategy import config as s
        bsrc = f"bots/{bot}/config.py"
        ssrc = "bots/chento_v3/strategy/config.py"
        if bot == "chento_v3":
            from bots.chento_v3 import config as b
            tables = (s.PERP_15M_TABLE, s.OKX_1H_TABLE)
            tilt = ("skip next trade after a loss"
                    if s.FILTER_NO_TILT else "off")
        else:
            from bots.chento_v3_eth import config as b
            eth = s._ASSET_TABLES["ETH"]
            tables = (eth["perp_15m"], eth["okx_1h"])
            tilt = ("half risk on trade after a loss"
                    if getattr(b, "TILT_HALF_AFTER_LOSS", False)
                    else "off")
        return [
            _p("Sizing", "risk per trade", b.RISK_PCT, "% of capital", bsrc),
            _p("Sizing", "notional cap", b.NOTIONAL_MAX_X, "× capital", bsrc),
            _p("Sizing", "paper capital", b.CAPITAL_USDT, "USDT", bsrc),
            _p("Cadence", "tick", b.TICK_SECONDS, "s", bsrc),
            _p("Cadence", "trigger cooldown", s.COOLDOWN_HOURS, "h", ssrc),
            _p("Signal", "B1 CVD z threshold", s.B1_CVD_Z_THRESHOLD, "", ssrc),
            _p("Signal", "B1 velocity z max", s.B1_VEL_Z_MAX, "", ssrc),
            _p("Signal", "B5 LSR percentiles",
               f"<{s.B5_LO_PCTILE} / >{s.B5_HI_PCTILE}", "30d window", ssrc),
            _p("Signal", "B7 median |z| threshold", s.B7_Z_THRESHOLD,
               f"across {'/'.join(s.B7_TIMEFRAMES)}", ssrc),
            _p("Signal", "triple window", s.TRIPLE_WINDOW_HOURS,
               "h trailing, B1-anchored", ssrc),
            _p("Filters", "tilt policy", tilt, "", ssrc),
            _p("Filters", "opposite order-block veto",
               f"within {s.SMC_OB_WITHIN_R}R"
               if s.FILTER_NO_RESIST_OB else "off", "", ssrc),
            _p("Filters", "OKX delta alignment",
               "on" if s.FILTER_OKX_ALIGNED else "off",
               f"z vs {tables[1]}", ssrc),
            _p("Filters", "skip shorts in up-30d regime",
               f"on (>{s.UP_30D_THRESHOLD:.0%})"
               if s.FILTER_SKIP_UP_30D_SHORTS else "off", "", ssrc),
            _p("Exits", "initial stop", f"{s.ATR_STOP_MULT}×ATR({s.ATR_PERIOD})",
               "15m bars", ssrc),
            _p("Exits", "target", s.TARGET_R, "R fixed", ssrc),
            _p("Exits", "time in force", s.TIF_HOURS, "h", ssrc),
            _p("Exits", "ladder adds",
               "DISABLED (P1 audit)" if not s.LADDER_ENABLED else "on",
               "", ssrc),
            _p("Costs", "round trip", s.COST_BP_RT, "bp", ssrc),
            _p("Data", "candles / OKX", " + ".join(tables), "", ssrc),
        ]

    if bot == "short_squeeze":
        from bots.short_squeeze import config as b
        from bots.short_squeeze.strategy import config as s
        bsrc, ssrc = ("bots/short_squeeze/config.py",
                      "bots/short_squeeze/strategy/config.py")
        lon, ny = s.SESSIONS["london"], s.SESSIONS["ny"]
        return [
            _p("Variants", "stop variant", b.VARIANTS[0]["id"],
               "swept-low stop, 3R target, 6h", bsrc),
            _p("Variants", "no-stop variant", b.VARIANTS[1]["id"],
               "6h time stop only (2026-09-12)", bsrc),
            _p("Sizing", "risk per trade", b.RISK_PCT, "% of capital", bsrc),
            _p("Sizing", "notional cap", b.NOTIONAL_MAX_X,
               "× capital (binds by design)", bsrc),
            _p("Sizing", "no-stop variant notional", b.NOSTOP_NOTIONAL_X,
               "× capital (fixed)", bsrc),
            _p("Cadence", "tick", b.TICK_SECONDS, "s", bsrc),
            _p("Cadence", "sessions", f"{lon[0]:02d}–{ny[1]:02d} UTC",
               "London+NY", ssrc),
            _p("Cadence", "cooldown", s.COOLDOWN_BARS * 15 // 60, "h", ssrc),
            _p("Signal", "perp CVD percentile", f"< {s.PERP_CVD_PCT_MAX}",
               f"{s.WINDOW_DAYS}d session dist.", ssrc),
            _p("Signal", "spot-perp divergence pct",
               f"> {s.DIVERGENCE_PCT_MIN}", "", ssrc),
            _p("Signal", "sweep lookback", s.LOOKBACK_BARS,
               "×15m prior low", ssrc),
            _p("Signal", "close-in-range", f"≥ {s.CLOSE_IN_RANGE_MIN}", "", ssrc),
            _p("Signal", "Asia macro gate",
               f"OI ≥ +{s.ASIA_OI_PCT_MIN:.1%}, funding < 0, "
               f"asia close < open", "", ssrc),
            _p("Exits", "stop", f"swept low × (1 − {s.STOP_BUFFER:.1%})",
               "", ssrc),
            _p("Exits", "target", s.TP_R, "R fixed", ssrc),
            _p("Exits", "time stop", s.TIME_STOP_HOURS, "h", ssrc),
            _p("Costs", "round trip + slippage",
               f"{s.COST_BP_RT} + {s.SLIPPAGE_BP_RT}", "bp", ssrc),
        ]

    if bot == "adx":
        from bots.adx import config as b
        from bots.adx.strategy import config as s
        bsrc, ssrc = "bots/adx/config.py", "bots/adx/strategy/config.py"
        return [
            _p("Sizing", "risk per trade", b.RISK_PCT, "% of capital", bsrc),
            _p("Sizing", "notional cap", b.NOTIONAL_MAX_X, "× capital", bsrc),
            _p("Cadence", "tick", b.TICK_SECONDS,
               "s (daily entry decision)", bsrc),
            _p("Signal", "ADX period", s.ADX_PERIOD, "d", ssrc),
            _p("Signal", "entry cross", f"< {s.ADX_LOW_THRESH:.0f} → "
               f"≥ {s.ADX_HIGH_THRESH:.0f}", "", ssrc),
            _p("Signal", "direction", f"close vs EMA({s.EMA_LEN})", "", ssrc),
            _p("Filters", "trend filter",
               f"EMA({s.TREND_EMA_LEN}) "
               f"{'symmetric' if s.SYMMETRIC_TREND_FILTER else 'LONG-only'}",
               "", ssrc),
            _p("Filters", "funding-crowding LONG veto",
               f"30d funding z > {s.FUNDING_VETO_Z}"
               if s.FUNDING_VETO_Z is not None else "off", "", ssrc),
            _p("Exits", "trail", f"{s.ATR_TRAIL_MULT}×ATR"
               f"({s.ATR_TRAIL_PERIOD})", "", ssrc),
            _p("Exits", "regime exit", f"ADX < {s.ADX_LOW_THRESH:.0f}",
               "", ssrc),
            _p("Exits", "fixed stop-loss", b.STOP_LOSS_PCT, "%", bsrc),
            _p("Costs", "round trip + slippage",
               f"{s.COST_BP_RT:g} + {s.SLIPPAGE_BP_RT:g}", "bp (measured 2026-09-12)",
               ssrc),
        ]

    if bot == "carry":
        from bots.carry import config as b
        from bots.carry.strategy import config as s
        bsrc, ssrc = ("bots/carry/config.py",
                      "bots/carry/strategy/config.py")
        return [
            _p("Sizing", "notional", b.CARRY_NOTIONAL_X,
               "× capital (fixed, no stop — delta-neutral)", bsrc),
            _p("Cadence", "tick", b.TICK_SECONDS,
               "s (daily funding decision)", bsrc),
            _p("Signal", "entry", f"{s.FR_WINDOW_DAYS}d avg funding "
               f"> {s.FR_ENTRY_THRESHOLD}", "", ssrc),
            _p("Exits", "exit", f"trailing {s.EXIT_CUM_DAYS}d cumulative "
               f"funding < {s.EXIT_CUM_THRESHOLD_PCT}%",
               "of notional (since 2026-09-12)", ssrc),
            _p("Costs", "round trip", s.ENTRY_EXIT_COST_PCT, "% notional",
               ssrc),
        ]

    if bot == "squeeze_bull":
        from bots.squeeze_bull import config as b
        from bots.squeeze_bull.strategy import config as s
        bsrc = "bots/squeeze_bull/config.py"
        ssrc = "bots/squeeze_bull/strategy/config.py"
        return [
            _p("Variants", "stop variant", b.VARIANTS[0]["id"],
               "-2% stop, +3% target, 48h", bsrc),
            _p("Variants", "no-stop variant", b.VARIANTS[1]["id"],
               "+3% target, 48h, no stop (2026-09-12)", bsrc),
            _p("Sizing", "risk per trade", b.RISK_PCT, "% of capital over the stop", bsrc),
            _p("Sizing", "notional cap", b.NOTIONAL_MAX_X, "x capital (inert at a 2% stop)", bsrc),
            _p("Sizing", "no-stop variant notional", b.NOSTOP_NOTIONAL_X,
               "x capital (fixed; = the stop variant's 1%/2%)", bsrc),
            _p("Sizing", "paper capital", b.CAPITAL_USDT, "USDT", bsrc),
            _p("Cadence", "tick", b.TICK_SECONDS, "s", bsrc),
            _p("Cadence", "entry evaluation", "once per closed hourly bar", "", ssrc),
            _p("Cadence", "cooldown", s.COOLDOWN_HOURS, "h after any kept flush", ssrc),
            _p("Signal", "OI change 4h", f"<= {s.FLUSH_THRESHOLD:.0%}", "forced deleveraging", ssrc),
            _p("Signal", "price change 4h", f"<= {s.PRICE_DIR_THRESHOLD:.1%}", "long flush", ssrc),
            _p("Signal", "regime gate", f"30d return > +{s.BULL_THRESHOLD:.0%}",
               f"backward-only, shifted {s.REGIME_SHIFT_DAYS}d (causal)", ssrc),
            _p("Exits", "stop / target", f"-{s.STOP_PCT:.0%} / +{s.TARGET_PCT:.0%}",
               "1.5 R gross, stop checked first", ssrc),
            _p("Exits", "time stop", s.TIF_HOURS, "h", ssrc),
            _p("Costs", "round trip + slippage",
               f"{s.PAPER_COST_BP_RT:g} + {s.PAPER_SLIPPAGE_BP_RT:g}",
               "bp (measured 2026-09-12; research replay 18)", ssrc),
            _p("Data", "mgmt / entry tables",
               f"{', '.join(b.MGMT_TABLES)} / {', '.join(b.ENTRY_TABLES)}", "", bsrc),
        ]

    if bot == "r4":
        from bots.r4 import config as b
        from strategies import trades as t
        from bots.r4.strategy import config as s
        bsrc = "bots/r4/config.py"
        ssrc = "bots/r4/strategy/config.py"
        weight = next(iter(b.VARIANT_WEIGHT.values()))
        enabled = ", ".join(k.replace("JPLUS_", "") for k, v in b.ENABLED.items() if v)
        return [
            _p("Sizing", "weight per variant", weight, "x capital x sleeve lev", bsrc),
            _p("Sizing", "sleeve leverage cap", b.LEV_CAP, "x (inner 2.5 x vol-target)", bsrc),
            _p("Sizing", "co-fire gross cap", b.GROSS_MAX_X, "x capital", bsrc),
            _p("Sizing", "min notional", b.MIN_NOTIONAL_USDT, "USDT", bsrc),
            _p("Sizing", "paper capital", b.CAPITAL_USDT, "USDT", bsrc),
            _p("Cadence", "tick", b.TICK_SECONDS, "s", bsrc),
            _p("Cadence", "late-entry grace", b.LATE_ENTRY_MAX_S, "s after open", bsrc),
            _p("Cadence", "enabled variants", enabled, "", bsrc),
            _p("Windows", "R4_BTC", f"Mon {s.R4_BTC_ENTRY_HOUR:02d}-{s.R4_BTC_EXIT_HOUR:02d} UTC",
               "day <= 14", ssrc),
            _p("Windows", "R4_ETH", f"Tue {s.R4_ETH_ENTRY_HOUR:02d} -> Wed {s.R4_ETH_EXIT_HOUR:02d} UTC",
               "Wed day <= 14", ssrc),
            _p("Windows", "R4_BTC_V2 / R4_ETH_V2",
               f"Wed+Fri {s.R4_V2_ENTRY_HOUR:02d}-{s.R4_V2_EXIT_HOUR:02d} UTC", "day <= 14", ssrc),
            _p("Signal", "inner leverage", f"{s.R4_INNER_LEV_UNGATED} / {s.R4_INNER_LEV_GATED}",
               "ungated / vol-gated", ssrc),
            _p("Exits", "exit", "scheduled window close", "no target", ssrc),
            _p("Exits", "stop-loss", b.STOP_LOSS_PCT if b.STOP_LOSS_PCT is not None
               else "none (r4_bot_prep sweep)", "", bsrc),
            _p("Costs", "round trip + slippage",
               f"{t.DEFAULT_COST_BP_RT:g} + {t.DEFAULT_SLIPPAGE_BP_RT:g}", "bp", "strategies/trades.py"),
            _p("Data", "mgmt / entry tables",
               " + ".join(b.MGMT_TABLES) + " / " + " + ".join(b.ENTRY_TABLES), "", bsrc),
        ]

    raise ValueError(f"unknown bot: {bot}")


def _read_md(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return f"*({path.name} not found)*"


def diag_today(bot: str) -> dict | None:
    """Sum today's diag.jsonl gate counters (multiple fragments per UTC
    day — always sum grouped by utc_date) + the last few near-misses."""
    path = BOTS[bot]["diag"]
    if path is None or not Path(path).exists():
        return None
    try:
        raw = Path(path).read_bytes()[-262144:]           # tail 256KB
        lines = raw.decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    by_date: dict[str, dict] = {}
    misses: dict[str, list] = {}
    b5_last: dict[str, dict] = {}       # date -> B5 state at the day's last bar
    for ln in lines:
        ln = ln.strip()
        if not ln or not ln.startswith("{"):
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        d = rec.get("utc_date")
        if not d:
            continue
        agg = by_date.setdefault(d, {})
        for k, v in (rec.get("counters") or {}).items():
            if isinstance(v, (int, float)):
                agg[k] = agg.get(k, 0) + v
        if rec.get("near_misses"):
            misses.setdefault(d, []).extend(rec["near_misses"])
        if rec.get("b5_last"):
            b5_last[d] = rec["b5_last"]
    if not by_date:
        return None
    today = datetime.now(timezone.utc).date().isoformat()
    date = today if today in by_date else max(by_date)
    return {"date": date, "is_today": date == today,
            "counters": dict(sorted(by_date[date].items())),
            "near_misses": (misses.get(date) or [])[-5:],
            "b5_last": b5_last.get(date)}


def summary() -> dict:
    con = queries._ro_con()
    try:
        counts: dict[str, dict] = {}
        try:
            for r in con.execute(
                    "SELECT strategy_variant v, status, COUNT(*) n "
                    "FROM trades WHERE strategy_variant LIKE 'bot_%' "
                    "GROUP BY strategy_variant, status"):
                c = counts.setdefault(r["v"], {"open": 0, "total": 0})
                c["total"] += r["n"]
                if r["status"] == "open":
                    c["open"] += r["n"]
        except Exception:
            pass
        caps: dict[str, float] = {}
        try:
            for r in con.execute("SELECT id, capital_usdt FROM variants "
                                 "WHERE id LIKE 'bot_%'"):
                caps[r["id"]] = r["capital_usdt"]
        except Exception:
            pass
    finally:
        con.close()
    bots = []
    for name, meta in BOTS.items():
        vid = meta["variant_id"]
        vids = meta.get("variant_ids", [vid])
        # A bot with several paper variants (squeeze bots since 2026-09-12)
        # reports the counts over all of them; capital is per variant.
        n_open = sum(counts.get(v, {"open": 0})["open"] for v in vids)
        n_total = sum(counts.get(v, {"total": 0})["total"] for v in vids)
        bots.append({"name": name, "display": meta["display"],
                     "variant_id": vid, "variant_ids": vids,
                     "asset": meta["asset"],
                     "capital_usdt": caps.get(vid),
                     "open_trades": n_open, "trades_total": n_total})
    return {"bots": bots}


def detail(bot: str) -> dict:
    if bot not in BOTS:
        raise KeyError(bot)
    meta = BOTS[bot]
    latest = queries.latest_entry(meta["variant_id"])
    return {
        "name": bot,
        "display": meta["display"],
        "variant_id": meta["variant_id"],
        "variant_ids": meta.get("variant_ids", [meta["variant_id"]]),
        "asset": meta["asset"],
        "cadence_note": meta["cadence_note"],
        "params": params(bot),
        "card_md": _read_md(CARDS_DIR / meta["card"]),
        "calibration_md": _read_md(CALIB_DIR / meta["calibration"]),
        "calibration_path": f"docs/calibration/{meta['calibration']}",
        "latest_entry": latest,
        "entry_chart_url": (f"/api/entry_chart/{latest['id']}.png"
                            if latest else None),
        "diag_today": diag_today(bot),
        "no_trades_note": (None if latest else
                           f"never traded — {meta['cadence_note']}"),
        "upcoming_windows": _upcoming_windows(bot),
    }


def _upcoming_windows(bot: str) -> list[dict] | None:
    """Read-only calendar for the R4 bot: the next windows from the same
    predicates the sleeve uses (bots/r4/windows.py), plus whether each
    variant already fired today. Other bots return None."""
    if bot != "r4":
        return None
    import sqlite3
    from bots.r4 import config as r4cfg
    from bots.r4 import windows as r4cal
    from strategies.support import clock, db
    now = clock.now_utc()
    enabled = [k for k, v in r4cfg.ENABLED.items() if v]
    fired: set[str] = set()
    try:
        con = sqlite3.connect(str(db.PROD_DB))
        try:
            fired = {r[0] for r in con.execute(
                "SELECT DISTINCT strategy FROM trades WHERE strategy_variant=? "
                "AND date(actual_entry_time)=date(?)",
                (BOTS["r4"]["variant_id"], now.isoformat())).fetchall()}
        finally:
            con.close()
    except sqlite3.OperationalError:
        pass
    return [{"strategy": w["strategy"], "asset": w["asset"],
             "open_utc": w["open_utc"].isoformat(),
             "close_utc": w["close_utc"].isoformat(),
             "fired_today": (w["strategy"] in fired
                             and w["open_utc"].date() == now.date())}
            for w in r4cal.next_windows(now, 8, strategies=enabled)]

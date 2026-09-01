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
        "asset": "BTC",
        "card": "short_squeeze.md",
        "calibration": "short_squeeze.md",
        "diag": REPO / "bots" / "short_squeeze" / "logs" / "diag.jsonl",
        "cadence_note": ("macro-gated: setup days are ~3.7% of all days; "
                         "multi-month droughts are designed behavior "
                         "(regime-complement to CARRY)"),
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
        from strategies.sleeves.chento_triple_v3 import config as s
        bsrc = f"bots/{bot}/config.py"
        ssrc = "strategies/sleeves/chento_triple_v3/config.py"
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
        from strategies.sleeves.short_squeeze import config as s
        bsrc, ssrc = ("bots/short_squeeze/config.py",
                      "strategies/sleeves/short_squeeze/config.py")
        lon, ny = s.SESSIONS["london"], s.SESSIONS["ny"]
        return [
            _p("Sizing", "risk per trade", b.RISK_PCT, "% of capital", bsrc),
            _p("Sizing", "notional cap", b.NOTIONAL_MAX_X,
               "× capital (binds by design)", bsrc),
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
        from strategies.sleeves.adx import config as s
        bsrc, ssrc = "bots/adx/config.py", "strategies/sleeves/adx/config.py"
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
            _p("Costs", "round trip", s.COST_BP_RT, "bp", ssrc),
        ]

    if bot == "carry":
        from bots.carry import config as b
        from strategies.sleeves.carry import config as s
        bsrc, ssrc = ("bots/carry/config.py",
                      "strategies/sleeves/carry/config.py")
        return [
            _p("Sizing", "notional", b.CARRY_NOTIONAL_X,
               "× capital (fixed, no stop — delta-neutral)", bsrc),
            _p("Cadence", "tick", b.TICK_SECONDS,
               "s (daily funding decision)", bsrc),
            _p("Signal", "entry", f"{s.FR_WINDOW_DAYS}d avg funding "
               f"> {s.FR_ENTRY_THRESHOLD}", "", ssrc),
            _p("Exits", "exit", f"{s.EXIT_NEG_DAYS} consecutive "
               f"negative-funding days", "", ssrc),
            _p("Costs", "round trip", s.ENTRY_EXIT_COST_PCT, "% notional",
               ssrc),
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
        c = counts.get(vid, {"open": 0, "total": 0})
        bots.append({"name": name, "display": meta["display"],
                     "variant_id": vid, "asset": meta["asset"],
                     "capital_usdt": caps.get(vid),
                     "open_trades": c["open"], "trades_total": c["total"]})
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
    }

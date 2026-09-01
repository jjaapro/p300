#!/usr/bin/env python3
"""Test 0 — LSR stamp semantics (pre-registered in README.md; run first).

Which moment does the value stored under a daily stamp D describe — D 00:00
(period start, causal to forward-fill from) or the end of day D (known only
at D+1 00:00, a 24h peek in any backtest that uses it from the stamp)?

Read-only: GETs to Binance (globalLongShortAccountRatio 1d / 1h / 5m) and
Coinalyze (daily history), a mode=ro read of prod.db. Writes only
results/test0_stamp_semantics.json and results/test0.md.

Decision rule (from the README, fixed before running): per day D with
full hourly coverage, a=|v1d−v1h(D 00:00)|, b=|v1d−v1h(D 23:00)|,
c=|v1d−mean(v1h over D)|, d=|v1d−v1h(D+1 00:00)|. `a` smallest on ≥80 % of
days → PERIOD-START (SHIFT_DAYS=0); b/c/d smallest on ≥80 % → PERIOD-END
(SHIFT_DAYS=+1); otherwise AMBIGUOUS (run both, +1 primary).
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from data.sources.binance import FAPI_DATA, _get  # noqa: E402  (GET helper only)
import fetch_coinalyze as cz  # noqa: E402  (_api_get is GET-only; loads .env)
from strategies.support import db  # noqa: E402

OUT = HERE / "results"
OUT.mkdir(exist_ok=True)
SYMBOL = "BTCUSDT"
DAY = 86400
SHARE = 0.80


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def binance(period: str, limit: int) -> dict[int, float]:
    rows = _get(f"{FAPI_DATA}/globalLongShortAccountRatio",
                {"symbol": SYMBOL, "period": period, "limit": limit})
    return {int(r["timestamp"]) // 1000: float(r["longShortRatio"]) for r in rows}


def stored(since: int) -> dict[int, float]:
    con = sqlite3.connect(f"file:{db.PROD_DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT timestamp, ratio FROM ca_long_short_ratio "
            "WHERE asset='BTC' AND timestamp >= ? ORDER BY timestamp", (since,)).fetchall()
    finally:
        con.close()
    return {int(t): float(r) for t, r in rows}


def coinalyze(frm: int, to: int) -> dict[int, float]:
    data = cz._api_get("long-short-ratio-history", {
        "symbols": cz.SYMBOLS["BTC"], "interval": "daily", "from": frm, "to": to})
    hist = data[0]["history"] if data and data[0].get("history") else []
    return {int(r["t"]): float(r["r"]) for r in hist}


def compare(daily: dict[int, float], hourly: dict[int, float]) -> list[dict]:
    """Per day D of `daily` with full hourly coverage: the four distances."""
    out = []
    for t, v in sorted(daily.items()):
        d0 = t - t % DAY
        hs = [hourly.get(d0 + k * 3600) for k in range(24)]
        if any(x is None for x in hs):
            continue
        nxt = hourly.get(d0 + DAY)
        c = {"a_start": abs(v - hs[0]), "b_last_hour": abs(v - hs[23]),
             "c_mean": abs(v - statistics.mean(hs))}
        if nxt is not None:
            c["d_next_open"] = abs(v - nxt)
        out.append({"day": iso(d0)[:10], "stamp_offset_s": t % DAY, "v1d": v,
                    **{k: round(x, 5) for k, x in c.items()},
                    "best": min(c, key=c.get)})
    return out


def offset_fit(a: dict[int, float], b: dict[int, float], offsets=(-1, 0, 1)) -> dict:
    """mean |a(D) − b(D + k days)| and correlation for each offset k."""
    res = {}
    for k in offsets:
        pairs = [(a[t], b[t + k * DAY]) for t in a if (t + k * DAY) in b]
        if len(pairs) < 5:
            res[str(k)] = {"n": len(pairs)}
            continue
        xs, ys = zip(*pairs)
        corr = statistics.correlation(xs, ys) if len(set(xs)) > 1 and len(set(ys)) > 1 else None
        res[str(k)] = {"n": len(pairs),
                       "mean_abs_diff": round(statistics.mean(abs(x - y) for x, y in pairs), 5),
                       "share_equal": round(sum(abs(x - y) < 1e-6 for x, y in pairs) / len(pairs), 3),
                       "corr": None if corr is None else round(corr, 4)}
    return res


def verdict(rows: list[dict]) -> tuple[str, int | None]:
    if not rows:
        return "NO_DATA", None
    n = len(rows)
    start = sum(r["best"] == "a_start" for r in rows) / n
    end = 1 - start
    if start >= SHARE:
        return "PERIOD_START", 0
    if end >= SHARE:
        return "PERIOD_END", 1
    return "AMBIGUOUS", None


def main() -> int:
    now_s = int(datetime.now(timezone.utc).timestamp())
    day0 = now_s - now_s % DAY
    print("fetching Binance 1d / 1h / 5m ...")
    d1, h1, m5 = binance("1d", 30), binance("1h", 500), binance("5m", 500)
    st = stored(day0 - 45 * DAY)
    print("fetching Coinalyze daily ...")
    czd = coinalyze(day0 - 45 * DAY, now_s)

    rows = compare(d1, h1)
    verd, shift = verdict(rows)
    cz_rows = compare(czd, h1)
    cz_verd, _ = verdict(cz_rows)

    # forming-row check: does today's row track the live 5m value?
    forming = None
    if day0 in d1:
        latest_m5_ts = max(m5) if m5 else None
        forming = {
            "today_1d": d1[day0],
            "m5_at_00": m5.get(day0),
            "m5_latest": m5.get(latest_m5_ts) if latest_m5_ts else None,
            "m5_latest_ts": iso(latest_m5_ts) if latest_m5_ts else None,
            "stored_today": st.get(day0),
        }
        if forming["m5_at_00"] is not None and forming["m5_latest"] is not None:
            forming["closer_to"] = ("00:00 value" if abs(d1[day0] - forming["m5_at_00"])
                                    <= abs(d1[day0] - forming["m5_latest"]) else "latest 5m value")

    result = {
        "generated_utc": iso(now_s),
        "stamp_offsets_1d": sorted({t % DAY for t in d1}),
        "binance_1d_vs_1h": {"rows": rows, "verdict": verd, "shift_days": shift,
                             "share_start": round(sum(r["best"] == "a_start" for r in rows) / len(rows), 3) if rows else None},
        "coinalyze_vs_binance_1h": {"rows": cz_rows, "verdict": cz_verd},
        "stored_vs_binance_1d": offset_fit(st, d1),
        "coinalyze_vs_stored": offset_fit(czd, st),
        "coinalyze_vs_binance_1d": offset_fit(czd, d1),
        "forming_row": forming,
        "decision": {
            "verdict": verd,
            "shift_days_primary": 1 if verd != "PERIOD_START" else 0,
            "run_both_shifts": verd == "AMBIGUOUS",
        },
    }
    (OUT / "test0_stamp_semantics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    md = [f"# Test 0 — LSR stamp semantics ({iso(now_s)})", "",
          f"Binance 1d stamps sit at offset {result['stamp_offsets_1d']} s into the UTC day.", "",
          "## Binance 1d vs its own 1h series", "",
          "| day | v1d | a start | b last hour | c mean | d next open | best |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['day']} | {r['v1d']:.4f} | {r['a_start']} | {r['b_last_hour']} | {r['c_mean']} | "
                  f"{r.get('d_next_open', '—')} | {r['best']} |")
    md += ["", f"**Verdict: {verd}** (share a-start = {result['binance_1d_vs_1h']['share_start']}, "
           f"rule ≥ {SHARE}) → SHIFT_DAYS = {result['decision']['shift_days_primary']}"
           f"{' (AMBIGUOUS: Test 1 runs both shifts, +1 primary)' if verd == 'AMBIGUOUS' else ''}", "",
           "## Coinalyze daily vs Binance 1h (the backfilled history's convention)", "",
           f"verdict on the same rule: **{cz_verd}** over {len(cz_rows)} days", "",
           "## Offset fits (mean |diff| / share equal / corr by day offset k)", ""]
    for name in ("stored_vs_binance_1d", "coinalyze_vs_stored", "coinalyze_vs_binance_1d"):
        md.append(f"- {name}: `{json.dumps(result[name])}`")
    md += ["", "## Forming-row check", "", f"`{json.dumps(forming)}`", ""]
    (OUT / "test0.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md[-12:]))
    print(f"\nVERDICT {verd} → shift_days_primary={result['decision']['shift_days_primary']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

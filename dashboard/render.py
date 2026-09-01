"""Annotated entry-context PNG for the bot info window.

Renders 15m candles around a trade's entry with the planned levels (entry /
SL / TP / timed stop) and an annotation box listing the decision inputs
recorded in trades.notes — "how this entry was decided", drawn from data,
not prose. Patterns cribbed from strategies/sleeves/ai_quant/chart.py
(headless Agg before pyplot, mpf returnfig, right-edge boxed level labels);
that module is deliberately left untouched — it has no 15m path.

Rendering is serialized with a module lock (pyplot is not thread-safe under
ThreadingHTTPServer) and cached to dashboard/cache/<id>.png: a closed
trade's picture is final; an open trade re-renders when the cached file is
older than 15 minutes.
"""
from __future__ import annotations

import io
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
# Force the headless Agg backend before pyplot/mplfinance import a GUI one
# (same constraint as ai_quant/chart.py: TclError on hosts without Tcl/Tk).
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402

from dashboard import queries  # noqa: E402

_LOCK = threading.Lock()
CACHE_DIR = Path(__file__).resolve().parent / "cache"
OPEN_RERENDER_S = 15 * 60

BAR_S = 900
BARS_BEFORE = 96          # 24h of 15m context before the entry
BARS_AFTER = 48           # up to 12h after (or "now" for a fresh entry)

_TABLES = {"BTC": "cd_futures_15m", "ETH": "cd_futures_eth_15m"}

_ENTRY = "#3b82f6"
_SL = "#d62728"
_TP = "#2ca02c"
_TIME = "#ffb000"

# Per-theme figure styling, matching the page tokens in static/style.css.
# dark keeps the original nightclouds look byte-for-byte; light gets the
# page's candle pair explicitly (mplfinance's default style has its own).
_STYLES = {
    "dark": dict(base="nightclouds", face="#14161a", fig="#14161a",
                 edge="#2e3340", grid="#22252e", tick="#8a8f9c",
                 text="#d7dae0", box="#1c1f26", candles=None),
    "light": dict(base="default", face="#ffffff", fig="#f4f4f2",
                  edge="#d3d5da", grid="#e4e5e8", tick="#5b606c",
                  text="#1b1d22", box="#ffffff",
                  candles=("#26a69a", "#ef5350")),
}


def _load_frame(asset: str, start_s: int, end_s: int) -> pd.DataFrame:
    table = _TABLES.get(asset)
    if table is None:
        raise ValueError(f"no candle table for asset {asset}")
    con = queries._ro_con()
    try:
        rows = con.execute(
            f"SELECT timestamp, open, high, low, close, volume FROM {table} "
            f"WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (start_s, end_s)).fetchall()
    finally:
        con.close()
    if not rows:
        raise ValueError(f"no {asset} 15m candles in the entry window")
    df = pd.DataFrame(
        [dict(r) for r in rows]).set_index("timestamp")
    df.index = pd.to_datetime(df.index, unit="s", utc=True)
    df.columns = [c.capitalize() for c in df.columns]
    return df


def entry_chart_png(trade: dict, theme: str = "dark") -> bytes:
    """Render the annotated entry-context chart for one trade dict (the
    shape queries._trade_dict returns)."""
    entry_ts = trade.get("entry_ts")
    if not entry_ts:
        raise ValueError(f"trade {trade.get('id')} has no entry timestamp")
    now_s = int(datetime.now(timezone.utc).timestamp())
    start = entry_ts - BARS_BEFORE * BAR_S
    end = min(now_s, entry_ts + BARS_AFTER * BAR_S)
    df = _load_frame(trade["asset"], start, end)

    plan = trade.get("plan") or {}
    dec = trade.get("decision") or {}
    entry_price = trade.get("entry_price")

    # planned levels: (price, color, style, label)
    levels = [(entry_price, _ENTRY, "-", f"entry {entry_price:,.1f}")]
    if plan.get("stop_price"):
        levels.append((plan["stop_price"], _SL, "--",
                       f"SL {plan['stop_price']:,.1f}"))
    if plan.get("target_price"):
        levels.append((plan["target_price"], _TP, "--",
                       f"TP {plan['target_price']:,.1f}"))

    entry_dt = pd.Timestamp(entry_ts, unit="s", tz="UTC")
    vlines = [entry_dt]
    vcolors = [_ENTRY]
    time_stop_ts = queries._ts(trade.get("timed_stop"))
    if time_stop_ts and start <= time_stop_ts <= end:
        vlines.append(pd.Timestamp(time_stop_ts, unit="s", tz="UTC"))
        vcolors.append(_TIME)

    # entry dot: NaN series with one value at the entry bar
    dot = pd.Series(float("nan"), index=df.index)
    entry_bar = pd.Timestamp((entry_ts // BAR_S) * BAR_S, unit="s", tz="UTC")
    addplots = []
    if entry_bar in dot.index and entry_price:
        dot.loc[entry_bar] = entry_price
        addplots.append(mpf.make_addplot(
            dot, type="scatter", markersize=90, marker="o", color=_ENTRY))

    title = (f"{trade['id']}  {trade.get('bot', '')}  {trade['asset']} "
             f"{trade['direction']}  ·  entered "
             f"{(trade.get('entry_time') or '')[:16]}Z  ·  {trade['status']}")

    st = _STYLES[theme]
    with _LOCK:
        style_kw: dict = dict(
            base_mpf_style=st["base"],
            facecolor=st["face"], figcolor=st["fig"], edgecolor=st["edge"],
            gridcolor=st["grid"], gridstyle=":",
            rc={"axes.labelcolor": st["tick"], "xtick.color": st["tick"],
                "ytick.color": st["tick"], "text.color": st["text"]})
        if st["candles"]:
            style_kw["marketcolors"] = mpf.make_marketcolors(
                up=st["candles"][0], down=st["candles"][1],
                edge="inherit", wick="inherit")
        style = mpf.make_mpf_style(**style_kw)
        kwargs: dict = dict(
            type="candle", style=style, returnfig=True, volume=False,
            figsize=(12, 6), title=dict(title=title, size=10),
            hlines=dict(hlines=[lv[0] for lv in levels],
                        colors=[lv[1] for lv in levels],
                        linestyle=[lv[2] for lv in levels],
                        linewidths=[0.9] * len(levels), alpha=0.8),
            vlines=dict(vlines=vlines, colors=vcolors,
                        linestyle=":", linewidths=[0.8] * len(vlines),
                        alpha=0.6),
        )
        if addplots:
            kwargs["addplot"] = addplots
        fig, axes = mpf.plot(df, **kwargs)
        ax = axes[0]

        # right-edge boxed labels per level (ai_quant chart.py pattern)
        for price, color, _, label in levels:
            ax.text(1.005, price, f" {label} ",
                    transform=ax.get_yaxis_transform(), color="white",
                    fontsize=7, fontweight="bold", va="center", ha="left",
                    bbox=dict(facecolor=color, edgecolor="none",
                              boxstyle="square,pad=0.15"), clip_on=False)

        # decision annotation box — how the entry was decided, from notes
        def _fmt(v):
            if isinstance(v, float):
                return f"{v:,.1f}" if abs(v) >= 1000 else f"{v:.4g}"
            return v

        lines: list[str] = []
        if dec.get("trigger"):
            lines.append(f"trigger: {dec['trigger']}")
        for k, v in (dec.get("filters") or {}).items():
            lines.append(f"filter {k}: {_fmt(v)}")
        for k, v in (dec.get("inputs") or {}).items():
            lines.append(f"{k}: {_fmt(v)}")
        for k, v in (plan.get("other") or {}).items():
            lines.append(f"{k}: {_fmt(v)}")
        if plan.get("risk_price"):
            lines.append(f"stop dist: {plan['risk_price']:,.1f}")
        if trade.get("size_usdt"):
            lines.append(f"size: ${trade['size_usdt']:,.0f} "
                         f"(qty {_fmt(trade.get('qty'))})")
        if time_stop_ts:
            lines.append(f"timed stop: "
                         f"{trade['timed_stop'][:16].replace('T', ' ')}Z")
        if trade["strategy"] == "CARRY":
            lines.append("delta-neutral: spot long + perp short —")
            lines.append("price path is not the P&L; funding is")
        if lines:
            ax.text(0.008, 0.985, "\n".join(lines[:14]),
                    transform=ax.transAxes, fontsize=7.5,
                    fontfamily="monospace", va="top", ha="left",
                    color=st["text"],
                    bbox=dict(facecolor=st["box"], edgecolor=st["edge"],
                              alpha=0.92, boxstyle="round,pad=0.4"))

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=115, bbox_inches="tight")
        plt.close(fig)
    return buf.getvalue()


def cached_entry_chart(trade_id: str, theme: str = "dark") -> bytes | None:
    """PNG for the trade, from cache when valid. None = unknown trade.
    Dark keeps the historical `SJ-n.png` cache name; other themes get
    `SJ-n-<theme>.png` so a theme switch never serves the wrong picture."""
    if theme not in _STYLES:
        raise ValueError(f"unknown chart theme: {theme}")
    trade = queries.trade_by_id(trade_id)
    if trade is None:
        return None
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / (f"{trade_id}.png" if theme == "dark"
                        else f"{trade_id}-{theme}.png")
    if path.exists():
        if trade["status"] != "open":          # closed = final picture
            return path.read_bytes()
        if time.time() - path.stat().st_mtime < OPEN_RERENDER_S:
            return path.read_bytes()
    png = entry_chart_png(trade, theme)
    try:
        path.write_bytes(png)
    except OSError:
        pass                                    # cache is best-effort
    return png

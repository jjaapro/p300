"""AI-purpose chart renderer for the AI_QUANT sleeve.

Renders a single PNG that the multimodal LLM looks at as part of its daily
decision. Reads from trader.db only (cd_spot_binance for BTC OHLC,
cd_funding_rate for funding, ca_long_short_ratio for L/S). All times use
clock.now_utc() so this is replay-safe — the renderer never peeks past the
simulated clock.

The chart is intentionally simple and AI-readable: candles + EMA50/EMA150
on the main panel, funding rate (in %) and long/short ratio as separate
sub-panels. Open AI_QUANT positions appear as dashed horizontal lines.

Usage:
    png = render_chart(asset="BTC", timeframe="1d", lookback_bars=90)
    Path("out.png").write_bytes(png)
"""
from __future__ import annotations

import io
import sqlite3
from collections import defaultdict
from pathlib import Path

import matplotlib

# Force the headless Agg backend before pyplot/mplfinance import a GUI one.
# We never want a Tk window from this server-side renderer; the failure mode
# on a Windows host without Tcl/Tk is a TclError on the single-panel path.
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402

from services import clock, db
from services.indicators import ema

_TF_SECONDS = {"1h": 3600, "4h": 4 * 3600, "1d": 86400}
_ALLOWED_INDICATORS = {"ema50", "ema150", "funding", "lsr", "open_positions"}
_DEFAULT_INDICATORS: tuple[str, ...] = ("ema50", "ema150", "funding", "lsr", "open_positions")
# Bars of warmup beyond the requested lookback so EMA(150) is fully seeded
# before the displayed window.
_WARMUP_BARS = 160


def _load_btc_1h(lookback_seconds: int) -> list[tuple]:
    upper_ts = clock.now_ts()
    since_ts = upper_ts - lookback_seconds
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        return con.execute(
            "SELECT timestamp, open, high, low, close, volume FROM cd_spot_binance "
            "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (since_ts, upper_ts),
        ).fetchall()
    finally:
        con.close()


def _aggregate(rows_1h: list, timeframe: str) -> pd.DataFrame:
    """Bucket 1h rows into the requested timeframe. The bucket containing
    clock.now_ts() is dropped so every displayed bar is CLOSED."""
    if not rows_1h:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    secs = _TF_SECONDS[timeframe]
    buckets: dict[int, list[tuple]] = defaultdict(list)
    for ts, o, h, l, c, v in rows_1h:
        if o is None or c is None or o <= 0 or c <= 0:
            continue
        buckets[(ts // secs) * secs].append((ts, o, h, l, c, v))
    now_bucket = (clock.now_ts() // secs) * secs
    rows: list[dict] = []
    for b_ts in sorted(buckets):
        if b_ts == now_bucket:
            continue
        bars = buckets[b_ts]
        rows.append({
            "ts": b_ts,
            "Open": bars[0][1],
            "High": max(b[2] for b in bars),
            "Low": min(b[3] for b in bars),
            "Close": bars[-1][4],
            "Volume": sum((b[5] or 0) for b in bars),
        })
    if not rows:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    df = pd.DataFrame(rows)
    df["dt"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    return df.set_index("dt")[["Open", "High", "Low", "Close", "Volume"]]


def _load_funding(lookback_seconds: int) -> pd.DataFrame:
    upper_ts = clock.now_ts()
    since_ts = upper_ts - lookback_seconds
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        rows = con.execute(
            "SELECT timestamp, fr_close FROM cd_funding_rate "
            "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (since_ts, upper_ts),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return pd.DataFrame(columns=["funding"])
    df = pd.DataFrame(rows, columns=["ts", "funding"])
    df["dt"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    return df.set_index("dt")[["funding"]]


def _load_lsr(asset: str, lookback_seconds: int) -> pd.DataFrame:
    upper_ts = clock.now_ts()
    since_ts = upper_ts - lookback_seconds
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        rows = con.execute(
            "SELECT timestamp, ratio FROM ca_long_short_ratio "
            "WHERE asset = ? AND timestamp >= ? AND timestamp <= ? "
            "ORDER BY timestamp",
            (asset.upper(), since_ts, upper_ts),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return pd.DataFrame(columns=["lsr"])
    df = pd.DataFrame(rows, columns=["ts", "lsr"])
    df["dt"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    return df.set_index("dt")[["lsr"]]


def render_chart(
    asset: str = "BTC",
    timeframe: str = "1d",
    lookback_bars: int = 90,
    indicators: list[str] | None = None,
    open_positions: list[dict] | None = None,
    out_path: str | Path | None = None,
) -> bytes:
    """Render a chart PNG. Returns the PNG bytes; if ``out_path`` is given,
    also writes the bytes to that path.

    Args:
        asset: only "BTC" supported in v1.
        timeframe: "1d" | "4h" | "1h".
        lookback_bars: number of bars to display.
        indicators: subset of {"ema50","ema150","funding","lsr","open_positions"}.
            Defaults to all five.
        open_positions: list of {direction, entry_price, entry_dt} dicts to
            mark as dashed horizontal lines on the price panel.
    """
    if asset.upper() != "BTC":
        raise ValueError(f"AI_QUANT v1 supports BTC only, got {asset!r}")
    if timeframe not in _TF_SECONDS:
        raise ValueError(f"timeframe must be one of {sorted(_TF_SECONDS)}, got {timeframe!r}")
    if lookback_bars < 5:
        raise ValueError(f"lookback_bars must be >= 5, got {lookback_bars}")
    inds = list(indicators) if indicators is not None else list(_DEFAULT_INDICATORS)
    bad = set(inds) - _ALLOWED_INDICATORS
    if bad:
        raise ValueError(f"unknown indicators: {sorted(bad)}")

    secs = _TF_SECONDS[timeframe]
    lookback_secs = (lookback_bars + _WARMUP_BARS) * secs

    df = _aggregate(_load_btc_1h(lookback_secs), timeframe)
    if df.empty:
        raise RuntimeError(f"no candles found in trader.db for {asset} {timeframe}")

    closes = df["Close"].tolist()
    ema50_full = ema(closes, 50)
    ema150_full = ema(closes, 150)

    if len(df) <= lookback_bars:
        df_show = df
        show_start = 0
    else:
        df_show = df.tail(lookback_bars)
        show_start = len(df) - lookback_bars
    ema50_show = ema50_full[show_start:]
    ema150_show = ema150_full[show_start:]

    addplots = []
    if "ema50" in inds:
        addplots.append(mpf.make_addplot(ema50_show, panel=0, color="#1f77b4", width=1.1))
    if "ema150" in inds:
        addplots.append(mpf.make_addplot(ema150_show, panel=0, color="#d62728", width=1.1))

    next_panel = 1
    if "funding" in inds:
        f_aligned = _load_funding(lookback_secs).reindex(df_show.index, method="ffill")
        if not f_aligned["funding"].isna().all():
            addplots.append(mpf.make_addplot(
                (f_aligned["funding"] * 100.0).tolist(),
                panel=next_panel, color="#9467bd", width=1.0,
                ylabel="Funding %", type="line",
            ))
            next_panel += 1
    if "lsr" in inds:
        l_aligned = _load_lsr(asset, lookback_secs).reindex(df_show.index, method="ffill")
        if not l_aligned["lsr"].isna().all():
            addplots.append(mpf.make_addplot(
                l_aligned["lsr"].tolist(),
                panel=next_panel, color="#2ca02c", width=1.0,
                ylabel="L/S ratio", type="line",
            ))
            next_panel += 1

    panel_count = next_panel
    panel_ratios = (3,) + (1,) * (panel_count - 1) if panel_count > 1 else (1,)

    title = (
        f"{asset.upper()} {timeframe}  •  {len(df_show)} bars  •  "
        f"clock={clock.now_utc().strftime('%Y-%m-%d %H:%M UTC')}"
    )
    style = mpf.make_mpf_style(base_mpf_style="charles", gridstyle=":")
    fig, axes = mpf.plot(
        df_show, type="candle", style=style, title=title,
        addplot=addplots if addplots else None,
        volume=False, returnfig=True,
        figsize=(13, 5 + 1.5 * (panel_count - 1)),
        panel_ratios=panel_ratios,
    )

    # mplfinance returns axes as a flat list [price_main, price_twin, panel1_main, panel1_twin, ...].
    if "open_positions" in inds and open_positions:
        ax_price = axes[0]
        for pos in open_positions:
            ep = pos.get("entry_price")
            try:
                ep = float(ep) if ep is not None else None
            except (TypeError, ValueError):
                ep = None
            if not ep:
                continue
            direction = str(pos.get("direction", "")).upper()
            color = "#2ca02c" if direction == "LONG" else "#d62728"
            ax_price.axhline(ep, color=color, linewidth=1.0, linestyle="--", alpha=0.7)
            ax_price.text(
                0.005, ep, f" {direction} @ {ep:,.0f}",
                transform=ax_price.get_yaxis_transform(),
                color=color, fontsize=8, va="bottom", ha="left",
            )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    png_bytes = buf.getvalue()
    if out_path:
        Path(out_path).write_bytes(png_bytes)
    return png_bytes

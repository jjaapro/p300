"""AI-purpose chart renderer for the AI_QUANT sleeve.

Renders a single PNG that the multimodal LLM looks at as part of its daily
decision. Reads from trader.db only (cd_spot_binance for BTC OHLC,
cd_futures_ohlcv for perp CVD, cd_funding_rate for funding,
ca_long_short_ratio for L/S). All times use clock.now_utc() so this is
replay-safe — the renderer never peeks past the simulated clock.

The chart is intentionally simple and AI-readable: candles + EMA20/50/150
on the main panel, plus volume, RSI(14), cumulative CVD on the perp,
funding rate (in %), and long/short ratio as separate sub-panels. Open
AI_QUANT positions appear as dashed horizontal lines on the price panel.

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
from matplotlib.ticker import MaxNLocator  # noqa: E402

from strategies.support import clock, db
from strategies.support.indicators import ema

_TF_SECONDS = {"1h": 3600, "4h": 4 * 3600, "1d": 86400}
_ALLOWED_INDICATORS = {
    "ema20", "ema50", "ema150",
    "volume", "rsi14",
    "cvd",
    "funding", "lsr",
    "open_positions",
}
_DEFAULT_INDICATORS: tuple[str, ...] = (
    "ema20", "ema50", "ema150",
    "volume", "rsi14",
    "cvd",
    "funding", "lsr",
    "open_positions",
)
# Bars of warmup beyond the requested lookback so EMA(150) is fully seeded
# before the displayed window. RSI(14) seeds in 14 bars, well within this.
_WARMUP_BARS = 160


def _rsi(values: list[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI. Output is aligned with `values`; the first `period`
    entries are None (no data yet). The displayed window is well past the
    warmup, so callers see only valid values.
    """
    n = len(values)
    if n <= period:
        return [None] * n
    out: list[float | None] = [None] * n
    gains = [0.0] * period
    losses = [0.0] * period
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains[i - 1] = max(d, 0.0)
        losses[i - 1] = max(-d, 0.0)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    for i in range(period + 1, n):
        d = values[i] - values[i - 1]
        gain = max(d, 0.0)
        loss = max(-d, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    return out


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


def _load_futures_cvd_1h(lookback_seconds: int) -> list[tuple]:
    """Pull hourly (timestamp, volume_buy, volume_sell) from cd_futures_ohlcv.
    The chart's OHLC comes from spot but CVD is most meaningful on the perp
    (~5-10× the spot volume), so this dips into a separate table. Rows
    where volume_buy IS NULL are filtered — they were partial-bar writes
    before the kline fetcher learned to populate taker columns. If the
    table is missing (e.g. unseeded test fixture) the panel is dropped
    rather than the chart crashing."""
    upper_ts = clock.now_ts()
    since_ts = upper_ts - lookback_seconds
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        try:
            return con.execute(
                "SELECT timestamp, volume_buy, volume_sell FROM cd_futures_ohlcv "
                "WHERE timestamp >= ? AND timestamp <= ? "
                "  AND volume_buy IS NOT NULL ORDER BY timestamp",
                (since_ts, upper_ts),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        con.close()


def _aggregate_cvd(rows_1h: list, timeframe: str,
                    target_index: pd.DatetimeIndex) -> pd.Series:
    """Bucket 1h CVD rows into the chart timeframe and align to the price
    panel's index. Returns a per-bar delta Series (buy − sell) reindexed
    to ``target_index`` with missing buckets filled with 0 — calling
    ``.cumsum()`` then gives the displayed cumulative line."""
    if not rows_1h or len(target_index) == 0:
        return pd.Series(0.0, index=target_index, dtype="float64")
    secs = _TF_SECONDS[timeframe]
    buckets: dict[int, float] = defaultdict(float)
    for ts, buy, sell in rows_1h:
        if buy is None or sell is None:
            continue
        buckets[(ts // secs) * secs] += float(buy) - float(sell)
    if not buckets:
        return pd.Series(0.0, index=target_index, dtype="float64")
    s = pd.Series(buckets)
    s.index = pd.to_datetime(s.index, unit="s", utc=True)
    return s.reindex(target_index).fillna(0.0)


def _load_funding(lookback_seconds: int) -> pd.DataFrame:
    upper_ts = clock.now_ts()
    since_ts = upper_ts - lookback_seconds
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        try:
            rows = con.execute(
                "SELECT timestamp, fr_close FROM cd_funding_rate "
                "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
                (since_ts, upper_ts),
            ).fetchall()
        except sqlite3.OperationalError:
            # Table missing (e.g. test fixture without funding seeded) →
            # render the chart without the funding panel rather than crash.
            rows = []
    finally:
        con.close()
    if not rows:
        return pd.DataFrame(columns=["funding"],
                            index=pd.DatetimeIndex([], tz="UTC"))
    df = pd.DataFrame(rows, columns=["ts", "funding"])
    df["dt"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    out = df.set_index("dt")[["funding"]]
    # cd_funding_rate has no PRIMARY KEY, so repeated backfills can leave
    # duplicate timestamps. reindex() on a duplicated source raises, so
    # collapse to one row per timestamp here.
    return out[~out.index.duplicated(keep="last")]


def _load_lsr(asset: str, lookback_seconds: int) -> pd.DataFrame:
    upper_ts = clock.now_ts()
    since_ts = upper_ts - lookback_seconds
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        try:
            rows = con.execute(
                "SELECT timestamp, ratio FROM ca_long_short_ratio "
                "WHERE asset = ? AND timestamp >= ? AND timestamp <= ? "
                "ORDER BY timestamp",
                (asset.upper(), since_ts, upper_ts),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
    finally:
        con.close()
    if not rows:
        return pd.DataFrame(columns=["lsr"],
                            index=pd.DatetimeIndex([], tz="UTC"))
    df = pd.DataFrame(rows, columns=["ts", "lsr"])
    df["dt"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    out = df.set_index("dt")[["lsr"]]
    return out[~out.index.duplicated(keep="last")]


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
    ema20_full = ema(closes, 20)
    ema50_full = ema(closes, 50)
    ema150_full = ema(closes, 150)
    rsi_full = _rsi(closes, 14) if "rsi14" in inds else None

    if len(df) <= lookback_bars:
        df_show = df
        show_start = 0
    else:
        df_show = df.tail(lookback_bars)
        show_start = len(df) - lookback_bars
    ema20_show = ema20_full[show_start:]
    ema50_show = ema50_full[show_start:]
    ema150_show = ema150_full[show_start:]

    addplots = []
    if "ema20" in inds:
        addplots.append(mpf.make_addplot(ema20_show, panel=0, color="#ff7f0e", width=1.0))
    if "ema50" in inds:
        addplots.append(mpf.make_addplot(ema50_show, panel=0, color="#1f77b4", width=1.1))
    if "ema150" in inds:
        addplots.append(mpf.make_addplot(ema150_show, panel=0, color="#d62728", width=1.1))

    show_volume = "volume" in inds
    next_panel = 2 if show_volume else 1

    # Track (panel_index, latest_value, format_string, line_color) for each
    # sub-panel so we can post-decorate after mpf.plot returns: tick locator
    # + a right-edge color-boxed label showing the current value.
    sub_panels: list[tuple[int, float | None, str, str]] = []

    volume_panel_idx: int | None = None
    if show_volume:
        volume_panel_idx = 1
        latest_vol = float(df_show["Volume"].iloc[-1]) if len(df_show) else None
        sub_panels.append((1, latest_vol, "{:,.0f}", "#555"))

    rsi_panel: int | None = None
    if "rsi14" in inds and rsi_full is not None:
        rsi_show = rsi_full[show_start:]
        addplots.append(mpf.make_addplot(
            rsi_show,
            panel=next_panel, color="#8c564b", width=1.0,
            ylabel="", type="line",
        ))
        rsi_panel = next_panel
        latest_rsi = next((v for v in reversed(rsi_show) if v is not None), None)
        sub_panels.append((next_panel, latest_rsi, "{:.0f}", "#8c564b"))
        next_panel += 1

    cvd_panel: int | None = None
    cum_cvd_latest: float | None = None
    if "cvd" in inds:
        cvd_rows = _load_futures_cvd_1h(lookback_secs)
        if cvd_rows:
            cvd_per_bar = _aggregate_cvd(cvd_rows, timeframe, df_show.index)
            cum_cvd = cvd_per_bar.cumsum()
            if not cum_cvd.empty:
                addplots.append(mpf.make_addplot(
                    cum_cvd.tolist(),
                    panel=next_panel, color="#17becf", width=1.0,
                    ylabel="", type="line",
                ))
                cvd_panel = next_panel
                cum_cvd_latest = float(cum_cvd.iloc[-1])
                sub_panels.append((next_panel, cum_cvd_latest, "{:+,.0f}",
                                    "#17becf"))
                next_panel += 1

    if "funding" in inds:
        f_aligned = _load_funding(lookback_secs).reindex(df_show.index, method="ffill")
        if not f_aligned["funding"].isna().all():
            f_pct = (f_aligned["funding"] * 100.0).tolist()
            addplots.append(mpf.make_addplot(
                f_pct,
                panel=next_panel, color="#9467bd", width=1.0,
                ylabel="", type="line",
            ))
            latest_funding = next((v for v in reversed(f_pct) if pd.notna(v)), None)
            sub_panels.append((next_panel, latest_funding, "{:+.4f}%", "#9467bd"))
            next_panel += 1
    if "lsr" in inds:
        l_aligned = _load_lsr(asset, lookback_secs).reindex(df_show.index, method="ffill")
        if not l_aligned["lsr"].isna().all():
            l_vals = l_aligned["lsr"].tolist()
            addplots.append(mpf.make_addplot(
                l_vals,
                panel=next_panel, color="#2ca02c", width=1.0,
                ylabel="", type="line",
            ))
            latest_lsr = next((v for v in reversed(l_vals) if pd.notna(v)), None)
            sub_panels.append((next_panel, latest_lsr, "{:.2f}", "#2ca02c"))
            next_panel += 1

    panel_count = next_panel
    panel_ratios = (3,) + (1,) * (panel_count - 1) if panel_count > 1 else (1,)

    title = (
        f"{asset.upper()} {timeframe}  •  {len(df_show)} bars  •  "
        f"clock={clock.now_utc().strftime('%Y-%m-%d %H:%M UTC')}"
    )
    # Style overrides: larger fonts, light gridlines — pushes the look closer
    # to a TradingView-style chart where ticks/labels are easy to read at a
    # glance rather than matplotlib's tight defaults.
    style = mpf.make_mpf_style(
        base_mpf_style="charles",
        gridstyle=":",
        rc={
            "font.size": 11,
            "axes.labelsize": 11,
            "axes.titlesize": 14,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
        },
    )
    plot_kwargs: dict = dict(
        type="candle", style=style, title=title,
        volume=show_volume, returnfig=True,
        figsize=(17, 6 + 1.9 * (panel_count - 1)),
        panel_ratios=panel_ratios,
        # Suppress mplfinance's default right-edge y-axis titles ("Price",
        # "Volume") — the inside-panel headers carry that info now.
        ylabel="",
        ylabel_lower="",
    )
    if addplots:
        plot_kwargs["addplot"] = addplots
    if show_volume:
        plot_kwargs["volume_panel"] = 1
    fig, axes = mpf.plot(df_show, **plot_kwargs)

    # Per-panel header text (TV-style legend inside the panel) so the reader
    # immediately sees what each panel is and what its current value is,
    # without cross-referencing the y-axis label.
    def _panel_header(panel_idx: int, text: str) -> None:
        try:
            ax = axes[2 * panel_idx]
        except (IndexError, AttributeError):
            return
        ax.text(
            0.005, 0.97, text,
            transform=ax.transAxes,
            fontsize=10, fontweight="bold", color="#222",
            va="top", ha="left",
            bbox=dict(boxstyle="square,pad=0.3",
                      facecolor="white", edgecolor="#ccc", alpha=0.92),
        )

    price_header_parts = [f"{asset.upper()} {timeframe}"]
    if "ema20" in inds and ema20_show:
        price_header_parts.append(f"EMA20 {ema20_show[-1]:,.0f}")
    if "ema50" in inds and ema50_show:
        price_header_parts.append(f"EMA50 {ema50_show[-1]:,.0f}")
    if "ema150" in inds and ema150_show:
        price_header_parts.append(f"EMA150 {ema150_show[-1]:,.0f}")
    _panel_header(0, "  •  ".join(price_header_parts))

    if show_volume:
        latest_vol_for_header = (float(df_show["Volume"].iloc[-1])
                                  if len(df_show) else None)
        if latest_vol_for_header is not None:
            _panel_header(1, f"Volume  {latest_vol_for_header:,.0f}")
    if rsi_panel is not None:
        latest_rsi_h = next((v for v in reversed(rsi_full[show_start:])
                              if v is not None), None) if rsi_full else None
        if latest_rsi_h is not None:
            _panel_header(rsi_panel, f"RSI 14 close  {latest_rsi_h:.2f}")
    if cvd_panel is not None and cum_cvd_latest is not None:
        _panel_header(cvd_panel,
                       f"CVD Perp cum  {cum_cvd_latest:+,.0f}")
    # Funding & L/S headers reuse the latest values stored on sub_panels —
    # find them by panel index.
    for panel_idx, latest, fmt, _color in sub_panels:
        if panel_idx in (volume_panel_idx, rsi_panel, cvd_panel):
            continue  # already headered above
        if latest is None or not pd.notna(latest):
            continue
        if fmt.endswith("%"):
            _panel_header(panel_idx, f"Funding %  {fmt.format(latest)}")
        else:
            _panel_header(panel_idx, f"L/S ratio  {fmt.format(latest)}")

    # Sub-panel decoration: denser y-tick grid + a right-edge color-boxed
    # label showing the latest value. The colored box (matching the line)
    # makes the current value pop and avoids visual collision with the
    # plain-text y-tick labels nearby. Best-effort: if the axes layout
    # changes between mplfinance versions and indexing fails, the chart
    # still renders without these decorations.
    for panel_idx, latest, fmt, line_color in sub_panels:
        try:
            ax = axes[2 * panel_idx]
        except (IndexError, AttributeError):
            continue
        if panel_idx == rsi_panel:
            ax.set_ylim(0, 100)
            ax.set_yticks([30, 50, 70])
            ax.axhline(70, color="#888", linewidth=0.6, linestyle=":", alpha=0.7)
            ax.axhline(30, color="#888", linewidth=0.6, linestyle=":", alpha=0.7)
        else:
            ax.yaxis.set_major_locator(MaxNLocator(nbins=5, prune=None))
        if latest is not None and pd.notna(latest):
            ax.axhline(latest, color=line_color, linewidth=0.6, alpha=0.5)
            ax.text(
                1.005, latest, f" {fmt.format(latest)} ",
                transform=ax.get_yaxis_transform(),
                color="white", fontsize=7, fontweight="bold",
                va="center", ha="left",
                bbox=dict(boxstyle="round,pad=0.18",
                          facecolor=line_color, edgecolor="none"),
            )

    # Price-panel current-close tag (right edge), matching the boxed style
    # used on sub-panels so the latest price stands out the same way.
    ax_price = axes[0]
    latest_close = float(df_show["Close"].iloc[-1]) if len(df_show) else None
    if latest_close is not None:
        prev_close = (float(df_show["Close"].iloc[-2])
                       if len(df_show) > 1 else latest_close)
        close_color = "#2ca02c" if latest_close >= prev_close else "#d62728"
        ax_price.axhline(latest_close, color=close_color, linewidth=0.6, alpha=0.5)
        ax_price.text(
            1.005, latest_close, f" {latest_close:,.2f} ",
            transform=ax_price.get_yaxis_transform(),
            color="white", fontsize=9, fontweight="bold",
            va="center", ha="left",
            bbox=dict(boxstyle="round,pad=0.22",
                      facecolor=close_color, edgecolor="none"),
        )

    # mplfinance returns axes as a flat list [price_main, price_twin, panel1_main, panel1_twin, ...].
    if "open_positions" in inds and open_positions:
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
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    png_bytes = buf.getvalue()
    if out_path:
        Path(out_path).write_bytes(png_bytes)
    return png_bytes

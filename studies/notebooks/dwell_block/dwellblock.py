"""Dwellblock detection — the operationalization of chento's 'dwellblock'.

Concept (reconstructed; see memory project_dwell_block_5s_study): a dwellblock is
a horizontal price zone where price spent significant TIME consolidating — a
high-time-at-price node (Market-Profile TPO / volume-profile value area), NOT a
candle pattern. He uses it three ways: stage limits INTO it (entry), it acting
as support that "holds", and "acceptance above" it as a breakout trigger.

This module detects those zones causally (as-of time T it uses only bars in
(T - lookback, T]) so it can drive a no-lookahead backtest. Detection runs on a
higher TF (5m/15m/1h — the TFs he draws them on); the 5s data is reserved for
the entry-execution simulation that comes later.

Method: build a TIME-at-price histogram over the lookback (each bar contributes
to every price bin its [low, high] spans — the TPO definition of "time at
price"), then a dwellblock = a contiguous run of bins whose normalised
time-at-price clears a threshold. The peak bin of a run is its POC.

Run `python studies/notebooks/dwell_block/dwellblock.py` to regenerate the
validation figure that overlays detected zones on his 3 actual marked charts.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve()
while not (ROOT / "data" / "databases" / "prod.db").exists():
    if ROOT == ROOT.parent:
        raise RuntimeError("could not locate data/databases/prod.db")
    ROOT = ROOT.parent
DB = ROOT / "data" / "databases" / "prod.db"


# ─── data ────────────────────────────────────────────────────────────────────

def load_btc_5s(start_ts: int | None = None, end_ts: int | None = None) -> pd.DataFrame:
    """Load cd_spot_5s into a UTC-indexed OHLCV frame. start/end are unix sec."""
    q = ("SELECT timestamp, open, high, low, close, volume, quote_volume, "
         "volume_buy, volume_sell, total_trades FROM cd_spot_5s")
    conds = []
    if start_ts is not None:
        conds.append(f"timestamp >= {int(start_ts)}")
    if end_ts is not None:
        conds.append(f"timestamp <= {int(end_ts)}")
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY timestamp"
    con = sqlite3.connect(str(DB))
    df = pd.read_sql(q, con)
    con.close()
    df["dt"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    return df.set_index("dt")


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample a 5s OHLCV frame to a coarser bar (e.g. '5min', '15min', '1h')."""
    agg = df.resample(rule).agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum"))
    return agg.dropna(subset=["open"])


# ─── time-at-price ───────────────────────────────────────────────────────────

@dataclass
class Dwellblock:
    lo: float          # zone lower edge
    hi: float          # zone upper edge
    poc: float         # price of the peak time-at-price bin
    tpo: float         # total time (bar-touches) inside the zone
    strength: float    # mean normalised time-at-price across the zone [0,1]

    @property
    def mid(self) -> float:
        return 0.5 * (self.lo + self.hi)


def time_at_price(bars: pd.DataFrame, n_bins: int = 120):
    """Return (centers, tpo, vol, edges) for the price range spanned by `bars`.

    tpo[i] = number of bars whose [low, high] covers bin i (time at price).
    vol[i] = volume distributed evenly across the bins each bar spans."""
    lo = float(bars["low"].min())
    hi = float(bars["high"].max())
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return None
    edges = np.linspace(lo, hi, n_bins + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    tpo = np.zeros(n_bins)
    vol = np.zeros(n_bins)
    lows = bars["low"].to_numpy()
    highs = bars["high"].to_numpy()
    vols = bars["volume"].to_numpy()
    for bl, bh, bv in zip(lows, highs, vols):
        i0 = max(0, np.searchsorted(edges, bl, side="right") - 1)
        i1 = min(n_bins - 1, np.searchsorted(edges, bh, side="right") - 1)
        tpo[i0:i1 + 1] += 1.0
        vol[i0:i1 + 1] += bv / (i1 - i0 + 1)
    return centers, tpo, vol, edges


def detect_dwellblocks(bars: pd.DataFrame, *, n_bins: int = 120,
                       thresh: float = 0.55, min_bins: int = 2,
                       top_k: int | None = None) -> list[Dwellblock]:
    """Detect dwellblocks over `bars` (one lookback window).

    A zone = a contiguous run of >= `min_bins` price bins whose time-at-price,
    normalised to the window peak, is >= `thresh`. Returned sorted by total
    time (strongest first); `top_k` caps the count."""
    tap = time_at_price(bars, n_bins)
    if tap is None:
        return []
    centers, tpo, _vol, edges = tap
    peak = tpo.max()
    if peak <= 0:
        return []
    norm = tpo / peak
    above = norm >= thresh

    zones: list[Dwellblock] = []
    i = 0
    while i < len(above):
        if not above[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(above) and above[j + 1]:
            j += 1
        if (j - i + 1) >= min_bins:
            seg = slice(i, j + 1)
            poc_idx = i + int(np.argmax(tpo[seg]))
            zones.append(Dwellblock(
                lo=float(edges[i]), hi=float(edges[j + 1]),
                poc=float(centers[poc_idx]),
                tpo=float(tpo[seg].sum()),
                strength=float(norm[seg].mean())))
        i = j + 2

    zones.sort(key=lambda z: -z.tpo)
    return zones[:top_k] if top_k else zones


def dwellblocks_asof(df5s: pd.DataFrame, asof: pd.Timestamp, *, tf: str,
                     lookback_bars: int, **kw) -> list[Dwellblock]:
    """Causal detector: resample to `tf`, take the `lookback_bars` bars ending
    at-or-before `asof`, detect. Uses only data <= asof (no lookahead)."""
    window = df5s.loc[:asof]
    bars = resample(window, tf).iloc[-lookback_bars:]
    if len(bars) < lookback_bars // 2:
        return []
    return detect_dwellblocks(bars, **kw)


# ─── validation render ───────────────────────────────────────────────────────

# His 3 actual 'dwellblock' messages (ts, the TF the screenshot was on, his words)
KNOWN = [
    ("2026-04-07 09:43", "5min", 288, 144,
     "L1781  \"one dwell block too early, got frontran + 3% dump\""),
    ("2026-04-23 18:18", "15min", 192, 96,
     "L1889  \"dwellblock held, refusal to go down\""),
    ("2026-05-04 17:46", "1h", 240, 120,
     "L1918  \"acceptance above this dwellblock -> 84 fast\""),
]


def _render():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    full = load_btc_5s()
    print(f"cd_spot_5s loaded: {len(full):,} bars  {full.index.min()} -> {full.index.max()}")

    fig, axes = plt.subplots(len(KNOWN), 2, figsize=(13, 4.2 * len(KNOWN)),
                             gridspec_kw={"width_ratios": [4, 1], "wspace": 0.02,
                                          "hspace": 0.28})

    for row, (asof_str, tf, lookback, forward, title) in enumerate(KNOWN):
        asof = pd.Timestamp(asof_str, tz="UTC")
        zones = dwellblocks_asof(full, asof, tf=tf, lookback_bars=lookback, top_k=4)

        # price window: lookback before -> forward after, at the detection TF
        win = resample(full.loc[asof - pd.Timedelta(tf) * lookback:
                                asof + pd.Timedelta(tf) * forward], tf)
        lb = resample(full.loc[:asof], tf).iloc[-lookback:]  # bars actually used

        axp, axh = axes[row]
        axp.plot(win.index, win["close"], lw=0.8, color="#111", zorder=3)
        axp.fill_between(win.index, win["low"], win["high"], color="#bbb",
                         alpha=0.35, lw=0, zorder=1)
        axp.axvline(asof, color="crimson", lw=1.1, ls="--", zorder=4)
        for z in zones:
            axp.axhspan(z.lo, z.hi, color="#1f77b4",
                        alpha=0.13 + 0.17 * z.strength, lw=0, zorder=2)
            axp.axhline(z.poc, color="#1f77b4", lw=0.6, alpha=0.5, zorder=2)
        axp.set_title(f"{title}\n[{tf} detect, {lookback}-bar lookback]  "
                      f"{len(zones)} dwellblocks; red = his post time",
                      fontsize=9, loc="left")
        axp.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M"))
        axp.tick_params(labelsize=7)
        axp.grid(alpha=0.15)

        # right: time-at-price histogram over the lookback window
        tap = time_at_price(lb, 120)
        if tap is not None:
            centers, tpo, _v, _e = tap
            axh.barh(centers, tpo, height=(centers[1] - centers[0]),
                     color="#1f77b4", alpha=0.6)
            for z in zones:
                axh.axhspan(z.lo, z.hi, color="#1f77b4", alpha=0.18, lw=0)
            axh.set_ylim(axp.get_ylim())
            axh.set_xticks([])
            axh.tick_params(labelleft=False, labelsize=7)
            axh.set_title("time@price", fontsize=7)

        print(f"\n{title}")
        for z in zones:
            print(f"   zone {z.lo:,.0f}-{z.hi:,.0f}  poc={z.poc:,.0f}  "
                  f"strength={z.strength:.2f}  tpo={z.tpo:.0f}")

    out = ROOT / "studies" / "notebooks" / "dwell_block" / "dwellblock_validation.png"
    fig.savefig(out, dpi=110, bbox_inches="tight")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    _render()

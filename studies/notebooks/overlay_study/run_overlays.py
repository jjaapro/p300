#!/usr/bin/env python3
"""Overlay study on the chento Triple composite (BTC + ETH, OKX-gated):

  W  rejection-wick exit — book at bar close when price wicks off a round
     level / the target while >= P_min R in profit (validated on Paladin's
     positions; here armed higher because the composite targets 6R)
  H  binary half-risk tag — half size when the entry is on a weekend, within
     24h before CPI/FOMC/NFP, or against the 30d BTC trend (>|10%|)
  T  post-loss policies on the trade sequence: full (none) / skip-after-loss
     (the shipped no_tilt) / half-after-loss / half-after-2-stops-in-7d

All exit variants are re-replayed bar-by-bar on the 15m futures tables with
identical mechanics (stop wins a spanning bar; TIF 72h) so baseline and
overlays differ ONLY in the policy under test. Sizing overlays scale each
trade's R contribution; metrics are portfolio-level (total R, maxDD, MAR).

Caveat carried from the source: the Triple trigger pool inherits the research
intersect lookahead — absolute Rs are optimistic, but every policy shares the
same entries, so RELATIVE comparisons are valid. IS/OOS split 2024-12-31.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DB = ROOT / 'data' / 'databases' / 'prod.db'
OUT = HERE / 'results'
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

TABLES = {'BTC': 'cd_futures_15m', 'ETH': 'cd_futures_eth_15m'}
IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')
TIF_H = 72
TOL = 0.0015
WICK_FRAC = 0.4


def load_bars(asset: str) -> pd.DataFrame:
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    try:
        df = pd.read_sql(f'SELECT timestamp, open, high, low, close FROM {TABLES[asset]} '
                         'ORDER BY timestamp', con)
    finally:
        con.close()
    df.index = pd.to_datetime(df.pop('timestamp'), unit='s', utc=True)
    return df[~df.index.duplicated(keep='last')]


def load_events() -> pd.DatetimeIndex:
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    try:
        rows = con.execute("SELECT date FROM scheduled_events "
                           "WHERE event_type IN ('CPI','FOMC','NFP')").fetchall()
    finally:
        con.close()
    return pd.DatetimeIndex([pd.Timestamp(r[0], tz='UTC') for r in rows]).sort_values()


def round_levels(lo: float, hi: float) -> np.ndarray:
    step = 10 ** (np.floor(np.log10(max(hi, 1e-12))) - 2)
    return np.arange(np.floor(lo / step) * step, hi + 2 * step, step)


def replay(bars: pd.DataFrame, t: pd.Series, wick_pmin: float | None) -> float:
    """R outcome of one trade under baseline or wick-exit policy."""
    sign = 1 if t.direction == 'long' else -1
    entry, stop, target, risk = t.entry, t.stop, t.target, abs(t.entry - t.stop)
    w = bars[(bars.index > t.ts) & (bars.index <= t.ts + pd.Timedelta(hours=TIF_H))]
    if not len(w):
        return np.nan
    levels = None
    if wick_pmin is not None:
        levels = round_levels(w['low'].min(), w['high'].max())
        levels = np.append(levels, target)
        levels = levels[(levels - entry) * sign >= -TOL * entry]
    for _, b in w.iterrows():
        if (b['low'] <= stop) if sign > 0 else (b['high'] >= stop):
            return -1.0
        if wick_pmin is not None:
            ext = b['high'] if sign > 0 else b['low']
            rng = b['high'] - b['low']
            if rng > 0 and (ext - entry) * sign / risk >= wick_pmin:
                shadow = (b['high'] - max(b['open'], b['close']) if sign > 0
                          else min(b['open'], b['close']) - b['low'])
                near = np.abs(ext - levels) <= TOL * entry
                through = ((ext - levels) * sign >= 0) & ((b['close'] - levels) * sign < 0)
                if (near.any() or through.any()) and shadow >= WICK_FRAC * rng:
                    return (b['close'] - entry) * sign / risk
        if (b['high'] >= target) if sign > 0 else (b['low'] <= target):
            return (target - entry) * sign / risk
    return (w.iloc[-1]['close'] - entry) * sign / risk


def half_risk_tag(t: pd.Series, events: pd.DatetimeIndex, btc30: pd.Series) -> bool:
    ts = t.ts
    if ts.dayofweek >= 5:
        return True
    nxt = events.searchsorted(ts)
    if nxt < len(events) and (events[nxt] - ts) <= pd.Timedelta('24h'):
        return True
    i = btc30.index.searchsorted(ts) - 1
    if i >= 0:
        r30 = btc30.iloc[i]
        if np.isfinite(r30) and abs(r30) > 0.10:
            against = (r30 > 0 and t.direction == 'short') or (r30 < 0 and t.direction == 'long')
            if against:
                return True
    return False


def tilt_sizes(rs: np.ndarray, ts: pd.DatetimeIndex, policy: str) -> np.ndarray:
    """Per-trade size multiplier from the sequence of prior outcomes."""
    n = len(rs)
    size = np.ones(n)
    for i in range(n):
        if policy == 'none':
            continue
        prev = rs[:i]
        if policy in ('skip_after_loss', 'half_after_loss'):
            if i > 0 and prev[-1] < 0:
                size[i] = 0.0 if policy == 'skip_after_loss' else 0.5
        elif policy == 'half_after_2stops_7d':
            recent = [(j, r) for j, r in enumerate(prev)
                      if ts[i] - ts[j] <= pd.Timedelta(days=7)]
            stops = [r for _, r in recent if r <= -0.99]
            # throttle lifts on the first winner after the stops
            lifted = any(r > 0 and j > max((jj for jj, rr in recent if rr <= -0.99),
                                           default=-1)
                         for j, r in recent)
            if len(stops) >= 2 and not lifted:
                size[i] = 0.5
    return size


def metrics(rs: np.ndarray, sizes: np.ndarray, ts: pd.DatetimeIndex, label: str) -> dict:
    eff = rs * sizes
    cum = np.cumsum(eff)
    dd = float((cum - np.maximum.accumulate(cum)).min()) if len(cum) else 0.0
    is_m = ts <= IS_END
    return {'variant': label, 'n': len(eff), 'traded_n': int((sizes > 0).sum()),
            'mean_r': float(np.mean(eff[sizes > 0])) if (sizes > 0).any() else np.nan,
            'total_r': float(eff.sum()), 'max_dd_r': dd,
            'mar_like': float(eff.sum() / abs(dd)) if dd < 0 else np.inf,
            'wr_pct': float((eff[sizes > 0] > 0).mean() * 100) if (sizes > 0).any() else np.nan,
            'is_total_r': float(eff[is_m].sum()), 'oos_total_r': float(eff[~is_m].sum())}


def main() -> int:
    events = load_events()
    btc_bars = load_bars('BTC')
    btc30 = btc_bars['close'].pct_change(30 * 96)

    frames = {}
    for asset in ('BTC', 'ETH'):
        t = pd.read_csv(OUT / f'trades_{asset}.csv', parse_dates=['ts'])
        # validated gate: OKX delta-z aligned with direction (z >= 0)
        aligned = (((t.direction == 'long') & (t.okx_delta_z >= 0))
                   | ((t.direction == 'short') & (t.okx_delta_z <= 0)))
        t = t[aligned].reset_index(drop=True)
        t['asset'] = asset
        frames[asset] = t
        print(f'{asset}: {len(t)} OKX-aligned trades '
              f'({t.ts.min():%Y-%m}->{t.ts.max():%Y-%m})')

    # exit-policy replays (per asset, then combined on the common window)
    results = []
    for scope in ('BTC', 'ETH', 'COMBINED'):
        if scope == 'COMBINED':
            end = min(f.ts.max() for f in frames.values())
            t = (pd.concat([f[f.ts <= end] for f in frames.values()])
                 .sort_values('ts').reset_index(drop=True))
        else:
            t = frames[scope]
        bars = {a: load_bars(a) for a in t.asset.unique()}

        exit_variants = {'base': None, 'wick_p0.5': 0.5, 'wick_p1': 1.0,
                         'wick_p2': 2.0, 'wick_p3': 3.0}
        routcomes = {}
        for name, pmin in exit_variants.items():
            rs = np.array([replay(bars[row.asset], row, pmin)
                           for row in t.itertuples()])
            routcomes[name] = rs

        ts_idx = pd.DatetimeIndex(t.ts)
        tags = np.array([half_risk_tag(row, events, btc30) for row in t.itertuples()])
        print(f'{scope}: half-risk tag rate {tags.mean():.0%}')

        for ename, rs in routcomes.items():
            keep = np.isfinite(rs)
            rs_k, ts_k, tags_k = rs[keep], ts_idx[keep], tags[keep]
            for tilt in ('none', 'skip_after_loss', 'half_after_loss',
                         'half_after_2stops_7d'):
                sizes = tilt_sizes(rs_k, ts_k, tilt)
                for htag in (False, True):
                    sz = sizes * np.where(tags_k & (sizes > 0), 0.5, 1.0) if htag else sizes
                    label = f'{ename}|tilt={tilt}|H={"on" if htag else "off"}'
                    m = metrics(rs_k, sz, ts_k, label)
                    m['scope'] = scope
                    results.append(m)

    res = pd.DataFrame(results)
    res.to_csv(OUT / 'overlay_summary.csv', index=False)
    cols = ['variant', 'n', 'traded_n', 'mean_r', 'total_r', 'max_dd_r',
            'mar_like', 'wr_pct', 'is_total_r', 'oos_total_r']
    for scope in ('BTC', 'ETH', 'COMBINED'):
        sub = res[res.scope == scope].sort_values('mar_like', ascending=False)
        print(f'\n=== {scope}: top 12 by MAR ===')
        print(sub[cols].head(12).round(2).to_string(index=False))
        base = sub[sub.variant == 'base|tilt=none|H=off']
        print('--- baseline ---')
        print(base[cols].round(2).to_string(index=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())

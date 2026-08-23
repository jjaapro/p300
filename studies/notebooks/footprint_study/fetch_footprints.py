#!/usr/bin/env python3
"""Per-event footprints from Binance Vision daily aggTrades.

For every unique (symbol, day) in results/events.csv: stream the daily zip,
keep only trades inside each event bar and the bar before it, bucket into
N_BINS price bins with signed delta (is_buyer_maker: True = taker SELL),
append to results/footprints.parquet, delete the zip. Nothing heavy persists.

Resumable: (symbol, event_ts) pairs already in the parquet are skipped.
"""
from __future__ import annotations

import io
import os
import sys
import time
import zipfile

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'results')
FP_PATH = os.path.join(OUT, 'footprints.parquet')
BASE = 'https://data.binance.vision/data/futures/um/daily/aggTrades'
N_BINS = 20
BAR = pd.Timedelta('15min')


def bucket(trades: pd.DataFrame, t0: pd.Timestamp) -> dict | None:
    """One bar's footprint: per-bin volume/delta + top-zone aggregates."""
    w = trades[(trades.ts >= t0) & (trades.ts < t0 + BAR)]
    if len(w) < 50:
        return None
    lo, hi = w.price.min(), w.price.max()
    if hi <= lo:
        return None
    bins = np.linspace(lo, hi, N_BINS + 1)
    idx = np.clip(np.digitize(w.price, bins) - 1, 0, N_BINS - 1)
    buy = np.bincount(idx, weights=np.where(w.is_buyer_maker, 0, w.quantity),
                      minlength=N_BINS)
    sell = np.bincount(idx, weights=np.where(w.is_buyer_maker, w.quantity, 0),
                       minlength=N_BINS)
    top = slice(int(N_BINS * 0.75), N_BINS)          # top 25% of the bar's range
    top_buy, top_sell = float(buy[top].sum()), float(sell[top].sum())
    return {'bar_lo': float(lo), 'bar_hi': float(hi),
            'delta_total': float(buy.sum() - sell.sum()),
            'vol_total': float(buy.sum() + sell.sum()),
            'top_delta': top_buy - top_sell,
            'top_vol': top_buy + top_sell,
            'top_sell_share': top_sell / (top_buy + top_sell)
                              if top_buy + top_sell > 0 else np.nan,
            'n_trades': int(len(w))}


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    ev = pd.read_csv(os.path.join(OUT, 'events.csv'), parse_dates=['event_ts'])
    ev = ev[ev.target_r == 1.0]                      # footprint keyed by event bar only
    done: set[tuple] = set()
    records: list[dict] = []
    if os.path.exists(FP_PATH):
        prev = pd.read_parquet(FP_PATH)
        records = prev.to_dict('records')
        done = {(r['symbol'], r['event_ts']) for r in records}
        print(f'resuming: {len(done)} events already done')

    by_day: dict[tuple, list] = {}
    for _, r in ev.iterrows():
        if (r.symbol, r.event_ts) in done:
            continue
        for day in {r.event_ts.date(), (r.event_ts - BAR).date()}:
            by_day.setdefault((r.symbol, day), []).append(r.event_ts)

    print(f'{len(ev)} events -> {len(by_day)} (symbol, day) files to fetch')
    day_cache: dict[tuple, pd.DataFrame] = {}
    n_files = 0
    for (sym, day), _ in sorted(by_day.items()):
        url = f'{BASE}/{sym}/{sym}-aggTrades-{day}.zip'
        try:
            resp = requests.get(url, timeout=300)
            if resp.status_code == 404:
                print(f'  404 {sym} {day}')
                continue
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                name = z.namelist()[0]
                df = pd.read_csv(z.open(name),
                                 usecols=['price', 'quantity', 'transact_time',
                                          'is_buyer_maker'])
        except Exception as e:  # noqa: BLE001
            print(f'  FAIL {sym} {day}: {e}')
            time.sleep(5)
            continue
        df['ts'] = pd.to_datetime(df.pop('transact_time'), unit='ms', utc=True)
        day_cache[(sym, day)] = df
        n_files += 1
        if n_files % 25 == 0:
            print(f'  [{n_files}/{len(by_day)}] {sym} {day}', flush=True)

        # bucket every event whose bars are fully coverable by cached days
        for _, r in ev.iterrows():
            key = (r.symbol, r.event_ts)
            if key in done or r.symbol != sym:
                continue
            need = {(r.symbol, r.event_ts.date()),
                    (r.symbol, (r.event_ts - BAR).date())}
            if not need <= set(day_cache):
                continue
            frames = pd.concat([day_cache[k] for k in need]).sort_values('ts')
            fe = bucket(frames, r.event_ts)
            fp = bucket(frames, r.event_ts - BAR)
            if fe is None:
                done.add(key)
                continue
            rec = {'symbol': r.symbol, 'event_ts': r.event_ts}
            rec.update({f'e_{k}': v for k, v in fe.items()})
            if fp:
                rec.update({f'p_{k}': v for k, v in fp.items()})
            records.append(rec)
            done.add(key)
        # keep the cache small: days arrive sorted, so 4 recent files suffice
        while len(day_cache) > 4:
            day_cache.pop(next(iter(day_cache)))
        if n_files % 50 == 0 and records:
            pd.DataFrame(records).to_parquet(FP_PATH, index=False)

    pd.DataFrame(records).to_parquet(FP_PATH, index=False)
    print(f'done: {len(records)} event footprints -> footprints.parquet')
    return 0


if __name__ == '__main__':
    sys.exit(main())

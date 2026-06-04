"""validation_C1_liq_cluster_tp: TP placement at liquidation-density clusters
instead of a fixed R-multiple.

Chento's edge (per the journal + tool stack memory) is heavily Leviathan-/
Coinglass-driven: he reads liquidation heatmaps to time exits at levels where
"the herd" gets liquidated. Price tends to magnet to those levels, hit them
hard, then reverse. The hypothesis is that the +0.5R MFE -> stop give-backs
we documented in the exhaustion analysis are happening because our fixed 3R
TP is BEYOND a real liq cluster — the cluster acted as the reversal trigger
and price never reached our target.

We don't have a true heatmap (which would show projected liquidation prices
of OPEN positions, sourced from exchange position data). What we have is
TV's per-bar realized `long_liq` and `short_liq` USD-notional, going back to
2024-01-01 on the 1H timeframe. We use those as a proxy: where price has
PRINTED large liquidations historically tends to mark levels of significance
(range edges, capitulation lows, blow-off highs). The density of recent
liquidations by price level approximates a "significance heatmap".

Approach:
  1. Build a rolling 30-day liquidation-density histogram from
     `tv_btc_perp_1h` (price bucket -> total notional liquidated).
  2. For each Triple trigger (entries are still from cd_futures_15m so the
     trigger set matches the prior studies):
       - long:  search ABOVE entry for the densest cluster within [+0.5R, +5R]
       - short: search BELOW entry within [-0.5R, -5R]
       - TP = cluster center (capped at +/- 5R if no cluster qualifies, fall
         back to baseline 3R)
  3. Replay with the cluster TP, ATR4 stop. Compare vs fixed-3R baseline.
  4. Test which side's liquidations to use:
       - 'opposite_side': use short_liq above for longs / long_liq below
         for shorts (theory: hunt the opposite side's stops)
       - 'same_side': inverse (theory: levels where same-side liquidations
         clustered are significant tops/bottoms — magnets in either direction)
       - 'combined':    total long_liq + short_liq density by price bucket
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve()
while not (ROOT / 'data' / 'databases' / 'prod.db').exists():
    if ROOT == ROOT.parent:
        raise RuntimeError('locate prod.db')
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))
DB = ROOT / 'data' / 'databases' / 'prod.db'
OUT_DIR = ROOT / 'studies' / 'material' / 'chento' / 'validation'
OUT_DIR.mkdir(parents=True, exist_ok=True)

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
    load_btc_15m, compute_moneyflow_signal, b1_triggers, compute_atr,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    load_lsr, compute_lsr_extremes, b5_triggers,
)
from studies.notebooks.chento_journal.validation_B7_multitf_cvd import (
    compute_multitf_cvd, b7_alignment_triggers,
)
from studies.notebooks.chento_journal.validation_B_composite import intersect_triggers

COST_BP = 18.0
TIF_BARS = 4 * 24    # 24h TIF at 15m
IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')


# === Load TV 1H liquidations ===============================================

def load_tv_1h() -> pd.DataFrame:
    """Load TV BTC perp 1H frame: timestamp, close, long_liq, short_liq."""
    con = sqlite3.connect(str(DB))
    df = pd.read_sql("""
        SELECT timestamp, open, high, low, close, long_liq, short_liq
        FROM tv_btc_perp_1h
        ORDER BY timestamp
    """, con)
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    df = df.set_index('ts').drop(columns='timestamp')
    df['long_liq'] = df['long_liq'].fillna(0)
    df['short_liq'] = df['short_liq'].fillna(0)
    df['tot_liq'] = df['long_liq'] + df['short_liq']
    return df


# === Cluster detection =====================================================

def find_cluster_tp(
    ts: pd.Timestamp,
    entry: float,
    direction: str,
    risk: float,
    tv_1h: pd.DataFrame,
    *,
    lookback_days: int = 30,
    side: str = 'opposite',     # 'opposite' | 'same' | 'combined'
    bin_pct: float = 0.0025,    # 0.25% price bin (~$200 at $80k BTC)
    search_min_R: float = 0.5,
    search_max_R: float = 5.0,
    cluster_min_pct: float = 0.20,   # cluster must hold >=X% of window's total
    fallback_R: float = 3.0,
) -> tuple[float, str]:
    """Return (TP price, source) where source ∈ {'cluster', 'fallback'}.

    Build a price-bin histogram of liquidation notional over the lookback,
    using the chosen side (long_liq / short_liq / combined). For a long
    trade, scan bins above entry within [+search_min_R, +search_max_R] in
    R units; pick the densest. Mirror for shorts. If no bin reaches the
    minimum density threshold, fall back to fixed `fallback_R`.
    """
    if entry <= 0 or risk <= 0:
        return entry, 'fallback'

    cutoff = ts - pd.Timedelta(days=lookback_days)
    window = tv_1h.loc[(tv_1h.index >= cutoff) & (tv_1h.index < ts)]
    if len(window) < lookback_days * 4:    # need at least 4 bars per day
        # not enough TV history yet; fall back
        if direction == 'long':
            return entry + fallback_R * risk, 'fallback'
        return entry - fallback_R * risk, 'fallback'

    # Pick the liquidation series
    if side == 'opposite':
        # longs use short_liq (where shorts got liquidated above)
        # shorts use long_liq (where longs got liquidated below)
        liq = window['short_liq'] if direction == 'long' else window['long_liq']
    elif side == 'same':
        liq = window['long_liq'] if direction == 'long' else window['short_liq']
    else:    # combined
        liq = window['tot_liq']

    if liq.sum() <= 0:
        if direction == 'long':
            return entry + fallback_R * risk, 'fallback'
        return entry - fallback_R * risk, 'fallback'

    # Build price-bin histogram. Each bar attributes its liq to its `close`.
    prices = window['close'].values
    weights = liq.values
    # Restrict to bars where there was meaningful liquidation
    msk = weights > 0
    prices = prices[msk]; weights = weights[msk]
    if len(prices) == 0:
        if direction == 'long':
            return entry + fallback_R * risk, 'fallback'
        return entry - fallback_R * risk, 'fallback'

    # Bin prices in log space: each bin spans bin_pct of price
    # Use multiplicative grid: bin k spans [base * (1+bin_pct)^k, base * (1+bin_pct)^(k+1))
    base = float(np.min(prices))
    bins_k = np.floor(np.log(prices / base) / np.log(1 + bin_pct)).astype(int)

    # Aggregate weight per bin_k
    df_bins = pd.DataFrame({'bin': bins_k, 'w': weights})
    grouped = df_bins.groupby('bin')['w'].sum().reset_index()
    grouped['price'] = base * (1 + bin_pct) ** (grouped['bin'] + 0.5)   # bin center

    total_w = float(grouped['w'].sum())
    if total_w <= 0:
        if direction == 'long':
            return entry + fallback_R * risk, 'fallback'
        return entry - fallback_R * risk, 'fallback'
    grouped['frac'] = grouped['w'] / total_w

    # Search range in price
    if direction == 'long':
        lo = entry + search_min_R * risk
        hi = entry + search_max_R * risk
        scan = grouped[(grouped['price'] >= lo) & (grouped['price'] <= hi)]
    else:
        lo = entry - search_max_R * risk
        hi = entry - search_min_R * risk
        scan = grouped[(grouped['price'] >= lo) & (grouped['price'] <= hi)]

    if scan.empty:
        if direction == 'long':
            return entry + fallback_R * risk, 'fallback'
        return entry - fallback_R * risk, 'fallback'

    # Pick the densest bin in the scan range
    best = scan.loc[scan['frac'].idxmax()]
    if best['frac'] < cluster_min_pct / max(len(scan), 1):
        # No dominant cluster — fall back
        if direction == 'long':
            return entry + fallback_R * risk, 'fallback'
        return entry - fallback_R * risk, 'fallback'

    return float(best['price']), 'cluster'


# === Replay ================================================================

def replay_one(trig, df_15m: pd.DataFrame, tv_1h: pd.DataFrame, *,
                atr_mult: float = 4.0, target_r: float = 3.0,
                tif_bars: int = TIF_BARS,
                tp_mode: str = 'cluster',
                cluster_kwargs: dict | None = None) -> dict | None:
    """Replay one trigger. tp_mode ∈ {'fixed', 'cluster'}.
    'fixed' uses fixed R-multiple TP (baseline).
    'cluster' uses find_cluster_tp(...).
    """
    direction = trig['direction']
    ts = trig['ts']
    idx = df_15m.index.searchsorted(ts, side='right') - 1
    if idx < 0 or idx >= len(df_15m) or df_15m.index[idx] != ts:
        idx = df_15m.index.searchsorted(ts, side='right') - 1
        if idx < 0:
            return None
    atr = float(df_15m['atr'].iloc[idx])
    if pd.isna(atr) or atr <= 0:
        return None
    entry = float(df_15m['close'].iloc[idx])
    risk = atr * atr_mult
    if risk <= 0:
        return None

    if direction == 'long':
        stop = entry - risk
    else:
        stop = entry + risk

    # TP placement
    tp_source = 'fixed'
    if tp_mode == 'cluster':
        target, tp_source = find_cluster_tp(
            ts, entry, direction, risk, tv_1h, **(cluster_kwargs or {}))
    else:
        if direction == 'long':
            target = entry + risk * target_r
        else:
            target = entry - risk * target_r

    cost_R = (COST_BP / 10000.0) * (entry / risk)

    start = idx + 1
    end = min(start + tif_bars, len(df_15m))
    if end <= start:
        return None
    outcome = None
    exit_kind = None
    exit_ts = None
    max_fav_R = 0.0
    if direction == 'long':
        for j in range(start, end):
            bh = float(df_15m['high'].iloc[j]); bl = float(df_15m['low'].iloc[j])
            max_fav_R = max(max_fav_R, (bh - entry) / risk)
            if bl <= stop:
                outcome = (stop - entry) / risk - cost_R
                exit_ts = df_15m.index[j]; exit_kind = 'stop'; break
            if bh >= target:
                outcome = (target - entry) / risk - cost_R
                exit_ts = df_15m.index[j]; exit_kind = 'target'; break
    else:
        for j in range(start, end):
            bh = float(df_15m['high'].iloc[j]); bl = float(df_15m['low'].iloc[j])
            max_fav_R = max(max_fav_R, (entry - bl) / risk)
            if bh >= stop:
                outcome = (entry - stop) / risk - cost_R
                exit_ts = df_15m.index[j]; exit_kind = 'stop'; break
            if bl <= target:
                outcome = (entry - target) / risk - cost_R
                exit_ts = df_15m.index[j]; exit_kind = 'target'; break
    if outcome is None:
        last_close = float(df_15m['close'].iloc[end - 1])
        if direction == 'long':
            outcome = (last_close - entry) / risk - cost_R
        else:
            outcome = (entry - last_close) / risk - cost_R
        exit_kind = 'tif'
        exit_ts = df_15m.index[end - 1]

    target_R = (target - entry) / risk if direction == 'long' else (entry - target) / risk
    return {
        'ts': ts, 'direction': direction, 'entry': entry,
        'stop': stop, 'target': target, 'target_R': round(target_R, 3),
        'risk': risk,
        'r_outcome': outcome, 'exit_kind': exit_kind,
        'tp_source': tp_source,
        'max_fav_R': round(max_fav_R, 3),
    }


def replay_all(triggers, df_15m, tv_1h, **kw) -> pd.DataFrame:
    rows = []
    for _, row in triggers.iterrows():
        r = replay_one(row, df_15m, tv_1h, **kw)
        if r is not None:
            rows.append(r)
    return pd.DataFrame(rows)


def summarize(t: pd.DataFrame, label: str) -> dict:
    if t.empty:
        return {'label': label, 'n': 0}
    t = t.sort_values('ts').reset_index(drop=True)
    cum = t['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    is_set = t[t['ts'] <= IS_END]
    oos_set = t[t['ts'] > IS_END]
    return {
        'label': label,
        'n': int(len(t)),
        'mean_R': round(float(t['r_outcome'].mean()), 3),
        'median_R': round(float(t['r_outcome'].median()), 3),
        'wr': round(float((t['r_outcome'] > 0).mean()), 3),
        'cum_R': round(float(cum[-1]), 2),
        'max_dd_R': round(float(dd.min()), 2),
        'targets': int((t['exit_kind'] == 'target').sum()),
        'stops': int((t['exit_kind'] == 'stop').sum()),
        'tifs': int((t['exit_kind'] == 'tif').sum()),
        'cluster_pct': round(float((t['tp_source'] == 'cluster').mean()) * 100, 1)
                        if 'tp_source' in t.columns else 0,
        'mean_target_R': round(float(t['target_R'].mean()), 3) if 'target_R' in t.columns else 0,
        'IS_meanR': round(float(is_set['r_outcome'].mean()) if len(is_set) else 0, 3),
        'IS_n': int(len(is_set)),
        'OOS_meanR': round(float(oos_set['r_outcome'].mean()) if len(oos_set) else 0, 3),
        'OOS_n': int(len(oos_set)),
    }


def main():
    print('Loading TV 1H + computing Triple composite...')
    tv_1h = load_tv_1h()
    print(f'  TV 1H: {len(tv_1h):,} bars  {tv_1h.index.min()} -> {tv_1h.index.max()}')

    df_15m = load_btc_15m()
    df_b1 = compute_moneyflow_signal(df_15m)
    df_b1['atr'] = compute_atr(df_b1, period=14)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df_15m, compute_lsr_extremes(load_lsr('BTC')))
    b7 = b7_alignment_triggers(compute_multitf_cvd(df_15m), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)

    # Restrict to triggers where we have at least 30d of TV history
    tv_start = tv_1h.index.min() + pd.Timedelta(days=30)
    triple_tv = triple[triple['ts'] >= tv_start].copy()
    print(f'  Triple triggers (full): {len(triple):,}')
    print(f'  Triple triggers (TV-covered, >= {tv_start.date()}): {len(triple_tv):,}')

    # === Baseline: fixed 3R on TV-covered set ===
    print('\n--- Baseline atr4_t3R on TV-covered set ---')
    base = replay_all(triple_tv, df_b1, tv_1h, tp_mode='fixed', target_r=3.0)
    sb = summarize(base, 'baseline_fixed_3R')
    print(f'  n={sb["n"]}  meanR={sb["mean_R"]}  WR={sb["wr"]}  '
           f'cumR={sb["cum_R"]}  maxDD={sb["max_dd_R"]}  '
           f'tgt={sb["targets"]}  stp={sb["stops"]}  tif={sb["tifs"]}')

    # Also fixed 2R and 5R as benchmarks
    for fixed_r in (2.0, 4.0, 5.0):
        rep = replay_all(triple_tv, df_b1, tv_1h, tp_mode='fixed', target_r=fixed_r)
        s = summarize(rep, f'baseline_fixed_{fixed_r}R')
        print(f'  fixed {fixed_r}R: n={s["n"]} meanR={s["mean_R"]:+.3f} '
               f'WR={s["wr"]:.0%} cumR={s["cum_R"]:+.1f} maxDD={s["max_dd_R"]:+.1f} '
               f'tgt={s["targets"]} tif={s["tifs"]}')

    # === Cluster variants ===
    print('\n--- Cluster-TP variants on TV-covered set ---')
    print(f'{"label":<55s} {"n":>4s} {"meanR":>7s} {"WR":>5s} {"cumR":>7s} {"maxDD":>7s} {"tgt":>4s} {"stp":>4s} {"tif":>4s} {"clust%":>6s} {"mTgtR":>6s} {"IS":>9s} {"OOS":>9s}')
    print(f'{sb["label"]:<55s} {sb["n"]:>4d} {sb["mean_R"]:>+7.3f} '
           f'{sb["wr"]:>5.2%} {sb["cum_R"]:>+7.1f} {sb["max_dd_R"]:>+7.1f} '
           f'{sb["targets"]:>4d} {sb["stops"]:>4d} {sb["tifs"]:>4d} '
           f'{sb.get("cluster_pct",0):>6.1f} '
           f'{sb.get("mean_target_R",3.0):>6.2f} '
           f'{sb["IS_meanR"]:>+9.3f} {sb["OOS_meanR"]:>+9.3f}')

    all_results = {'baseline_fixed_3R': sb}
    variants = []
    # Vary lookback × side × max_R
    for lookback_d in (14, 30, 60):
        for side in ('opposite', 'same', 'combined'):
            for max_R in (3.0, 5.0):
                variants.append({
                    'label': f'cluster_lb{lookback_d}d_{side}_maxR{max_R}',
                    'lookback_days': lookback_d,
                    'side': side,
                    'search_min_R': 0.5,
                    'search_max_R': max_R,
                    'bin_pct': 0.0025,
                    'cluster_min_pct': 0.20,
                    'fallback_R': 3.0,
                })

    for v in variants:
        label = v.pop('label')
        rep = replay_all(triple_tv, df_b1, tv_1h, tp_mode='cluster',
                          cluster_kwargs=v)
        s = summarize(rep, label)
        all_results[label] = s
        print(f'{label:<55s} {s["n"]:>4d} {s["mean_R"]:>+7.3f} '
               f'{s["wr"]:>5.2%} {s["cum_R"]:>+7.1f} {s["max_dd_R"]:>+7.1f} '
               f'{s["targets"]:>4d} {s["stops"]:>4d} {s["tifs"]:>4d} '
               f'{s.get("cluster_pct",0):>6.1f} '
               f'{s.get("mean_target_R",3.0):>6.2f} '
               f'{s["IS_meanR"]:>+9.3f} {s["OOS_meanR"]:>+9.3f}')

    # === Top variants ===
    print('\n=== Top by mean R (n>=50) ===')
    ranked = sorted(
        [(k, v) for k, v in all_results.items()
         if v.get('n', 0) >= 50 and k != 'baseline_fixed_3R'],
        key=lambda kv: kv[1].get('mean_R', 0), reverse=True)[:6]
    for k, v in ranked:
        print(f'  {k:<55s}  meanR={v["mean_R"]:+.3f}  cumR={v["cum_R"]:+7.1f}  '
               f'maxDD={v["max_dd_R"]:+5.1f}  IS={v["IS_meanR"]:+.3f}  '
               f'OOS={v["OOS_meanR"]:+.3f}')

    print('\n=== Top by max-DD (n>=50) ===')
    ranked_dd = sorted(
        [(k, v) for k, v in all_results.items()
         if v.get('n', 0) >= 50],
        key=lambda kv: kv[1].get('max_dd_R', -99), reverse=True)[:6]
    for k, v in ranked_dd:
        print(f'  {k:<55s}  maxDD={v["max_dd_R"]:+5.1f}  meanR={v["mean_R"]:+.3f}  '
               f'cumR={v["cum_R"]:+7.1f}')

    out_path = OUT_DIR / 'C1_liq_cluster_tp_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('C1 liquidation-cluster TP placement on Triple + atr4 stop. '
                      'TV 1H data 2024-01-01 to 2026-05-25; rolling 14/30/60d '
                      'liq-density window; bin_pct=0.25%; min cluster fraction '
                      'within scan range = 20%. Compared to fixed 2/3/4/5R.'),
            'variants': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

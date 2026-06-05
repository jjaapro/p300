"""Phase 4 of the LVN study: backtest the LVN-fade sleeve.

Setup (single best-guess config from Phase 3 / 3b):
  - Entry: first bar closes inside an LVN, prev bar was outside
  - Direction: AGAINST entry (fade back to the HVN price came from)
  - Width filter: 1% <= zone_width / price <= 2% (sweet spot from Phase 3b)
  - Stop: OPPOSITE LVN edge + 5% width buffer (allows full traverse)
  - Target: ENTRY edge (the side price came through) — back-to-where-it-came
  - TIF: 96 bars (24h)
  - Cost: 18bp round-trip (production default)

Output:
  - Per-trade ledger with regime + direction tags
  - Aggregate: n, mean R, WR, maxDD, MAR, annual R
  - Breakdown by regime + by entry direction

Decision:
  - mean R > +0.3R AND MAR > 2 AND OOS positive: PASS — proceed to sleeve design
  - mean R > 0 but MAR < 2: marginal — consider regime gating
  - mean R <= 0: FAIL — fade economic story doesn't survive cost
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

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

from studies.notebooks.fvg_magnet.lvn_phase3_directionality import (
    load_btc_15m, compute_vp_at, identify_lvn_zones,
    VP_WINDOW_BARS, REBALANCE_BARS,
)

# Config — wider LVNs (Phase 3b showed 75-89% reversal at 2-5% width;
# wider stops also reduce cost-as-fraction-of-risk dramatically).
WIDTH_MIN = 0.02
WIDTH_MAX = 0.05
STOP_BUFFER_FRAC = 0.05
TARGET_PENETRATION_FRAC = 0.25
MIN_ENTRY_PENETRATION_FRAC = 0.30
COOLDOWN_BARS = 4
TIF_BARS = 288                     # 72h — wider LVNs take longer to traverse
COST_BP = 18.0

IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')


def replay_trade(highs, lows, closes, *,
                  entry_idx: int, direction: str,
                  zone_low: float, zone_high: float, width: float,
                  tif_bars: int = TIF_BARS) -> dict | None:
    """Simulate the fade trade. direction='short' = fade bottom-entry;
    direction='long' = fade top-entry."""
    entry = closes[entry_idx]
    if direction == 'short':
        # Came up into LVN from below; fade DOWN.
        # Target pushes PAST entry edge (zone_low) by penetration*width
        # so reward is meaningful relative to cost.
        stop = zone_high + STOP_BUFFER_FRAC * width        # above LVN
        target = zone_low - TARGET_PENETRATION_FRAC * width  # below LVN
        risk = stop - entry
        reward = entry - target
    else:
        # Came down into LVN from above; fade UP.
        stop = zone_low - STOP_BUFFER_FRAC * width
        target = zone_high + TARGET_PENETRATION_FRAC * width
        risk = entry - stop
        reward = target - entry
    if risk <= 0 or reward <= 0:
        return None
    cost_R = (COST_BP / 10000.0) * (entry / risk)

    n = len(highs)
    end = min(entry_idx + tif_bars + 1, n)
    exit_kind = None
    last_close = entry
    r_out = None
    for j in range(entry_idx + 1, end):
        bh = float(highs[j]); bl = float(lows[j]); bc = float(closes[j])
        last_close = bc
        if direction == 'short':
            if bh >= stop:
                r_out = (entry - stop) / risk - cost_R
                exit_kind = 'stop'; break
            if bl <= target:
                r_out = (entry - target) / risk - cost_R
                exit_kind = 'target'; break
        else:
            if bl <= stop:
                r_out = (stop - entry) / risk - cost_R
                exit_kind = 'stop'; break
            if bh >= target:
                r_out = (target - entry) / risk - cost_R
                exit_kind = 'target'; break
    if r_out is None:
        r_out = ((entry - last_close) / risk - cost_R if direction == 'short'
                  else (last_close - entry) / risk - cost_R)
        exit_kind = 'tif'
    return {
        'entry': entry, 'stop': stop, 'target': target,
        'risk': risk, 'reward': reward,
        'r_outcome': r_out, 'exit_kind': exit_kind,
    }


def summarize(rep: pd.DataFrame, label: str) -> dict:
    if rep.empty:
        return {'label': label, 'n': 0}
    rep = rep.sort_values('ts').reset_index(drop=True)
    cum = rep['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    is_set = rep[rep['ts'] <= IS_END]
    oos_set = rep[rep['ts'] > IS_END]
    span_y = ((rep['ts'].max() - rep['ts'].min()).total_seconds()
                / (365.25 * 86400))
    annual_R = float(rep['r_outcome'].mean()) * len(rep) / max(span_y, 0.05)
    return {
        'label': label, 'n': int(len(rep)),
        'n_per_yr': round(len(rep) / max(span_y, 0.05), 1),
        'mean_R': round(float(rep['r_outcome'].mean()), 3),
        'WR': round(float((rep['r_outcome'] > 0).mean()), 3),
        'cum_R': round(float(cum[-1]), 2),
        'maxDD': round(float(dd.min()), 2),
        'annual_R': round(annual_R, 1),
        'MAR': round(annual_R / abs(float(dd.min())), 2)
                if dd.min() < 0 else 0,
        'IS_meanR': round(float(is_set['r_outcome'].mean()) if len(is_set) else 0, 3),
        'IS_n': int(len(is_set)),
        'OOS_meanR': round(float(oos_set['r_outcome'].mean()) if len(oos_set) else 0, 3),
        'OOS_n': int(len(oos_set)),
        'stops_pct': round(float((rep['exit_kind'] == 'stop').mean()), 2),
        'tgts_pct': round(float((rep['exit_kind'] == 'target').mean()), 2),
        'tif_pct': round(float((rep['exit_kind'] == 'tif').mean()), 2),
    }


def show(s):
    if s.get('n', 0) == 0:
        print(f'  {s["label"]:<35s} empty'); return
    print(f'  {s["label"]:<35s} n={s["n"]:>4d} ({s["n_per_yr"]:>5.1f}/yr)  '
          f'meanR={s["mean_R"]:+.3f}  WR={s["WR"]:.0%}  '
          f'cumR={s["cum_R"]:>+7.1f}  maxDD={s["maxDD"]:+5.2f}  '
          f'annR={s["annual_R"]:>+6.1f}  MAR={s["MAR"]:>5.2f}  '
          f'OOS={s["OOS_meanR"]:+.3f}({s["OOS_n"]})  '
          f'st/tg/tif={s["stops_pct"]:.0%}/{s["tgts_pct"]:.0%}/{s["tif_pct"]:.0%}')


def main():
    print('Loading BTC 15m...')
    df = load_btc_15m()
    print(f'  {len(df)} bars')

    typ = ((df['high'] + df['low']) / 2).values
    vol = df['volume'].values
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    n_bars = len(df)
    ts_arr = df.index

    # BTC 30d return for regime
    daily_close = df['close'].resample('1D').last()
    ret30 = daily_close.pct_change(30).reindex(df.index, method='ffill').values

    print(f'Detecting LVN entries with width filter [{WIDTH_MIN*100}%, '
          f'{WIDTH_MAX*100}%]...')
    rebalance_zones = []
    for idx_end in range(VP_WINDOW_BARS, n_bars, REBALANCE_BARS):
        vp = compute_vp_at(typ, vol, idx_end)
        if vp is None:
            continue
        bin_vols, bin_edges = vp
        zones = identify_lvn_zones(bin_vols, bin_edges)
        rebalance_zones.append((idx_end, zones))

    trades = []
    last_trade_idx_per_zone: dict[tuple[float, float], int] = {}
    for ri, (idx_R, zones) in enumerate(rebalance_zones):
        next_R = (rebalance_zones[ri + 1][0]
                   if ri + 1 < len(rebalance_zones) else n_bars)
        end_walk = min(idx_R + REBALANCE_BARS, next_R, n_bars - 1)
        for zone_low, zone_high in zones:
            if zone_high <= zone_low:
                continue
            close_at_R = closes[idx_R]
            width_pct = (zone_high - zone_low) / close_at_R
            if not (WIDTH_MIN <= width_pct <= WIDTH_MAX):
                continue
            zone_key = (round(zone_low, 2), round(zone_high, 2))
            last_trade = last_trade_idx_per_zone.get(zone_key, -10**9)
            for i in range(idx_R + 1, end_walk):
                if i - last_trade < COOLDOWN_BARS:
                    continue
                prev_close = closes[i - 1]
                curr_close = closes[i]
                if zone_low <= prev_close <= zone_high:
                    continue
                if not (zone_low <= curr_close <= zone_high):
                    continue
                # Entry event
                if prev_close < zone_low:
                    entry_dir = 'short'  # came up into LVN, fade DOWN
                    entry_edge = zone_low
                    penetration = (curr_close - zone_low) / (zone_high - zone_low)
                else:
                    entry_dir = 'long'   # came down into LVN, fade UP
                    entry_edge = zone_high
                    penetration = (zone_high - curr_close) / (zone_high - zone_low)
                # Penetration filter: require minimum depth into the LVN
                # before opening, so the reward distance is meaningful.
                if penetration < MIN_ENTRY_PENETRATION_FRAC:
                    continue
                width = zone_high - zone_low
                result = replay_trade(
                    highs, lows, closes,
                    entry_idx=i, direction=entry_dir,
                    zone_low=zone_low, zone_high=zone_high, width=width)
                if result is None:
                    continue
                regime = ('bear_30d' if ret30[i] < -0.10
                            else 'bull_30d' if ret30[i] > 0.10
                            else 'flat_30d')
                trades.append({
                    'ts': ts_arr[i],
                    'idx': i,
                    'direction': entry_dir,
                    'regime': regime,
                    'ret_30d': float(ret30[i]) if not np.isnan(ret30[i]) else None,
                    'zone_low': zone_low, 'zone_high': zone_high,
                    'width_pct': width_pct,
                    **result,
                })
                last_trade_idx_per_zone[zone_key] = i

    rep = pd.DataFrame(trades)
    print(f'  Total trades: {len(rep)}')

    # ─── Headline ─────────────────────────────────────────────────────────
    print('\n' + '=' * 110)
    print('=== HEADLINE ===')
    print('=' * 110)
    show(summarize(rep, 'ALL TRADES (1-2% LVN width)'))

    # ─── By direction ─────────────────────────────────────────────────────
    print('\n=== BY ENTRY DIRECTION ===')
    show(summarize(rep[rep['direction'] == 'short'], 'short (fade bottom-entry)'))
    show(summarize(rep[rep['direction'] == 'long'], 'long (fade top-entry)'))

    # ─── By regime ────────────────────────────────────────────────────────
    print('\n=== BY REGIME ===')
    for regime in ('bear_30d', 'flat_30d', 'bull_30d'):
        show(summarize(rep[rep['regime'] == regime], regime))

    # ─── Regime × direction cross-tab ─────────────────────────────────────
    print('\n=== REGIME × DIRECTION ===')
    for regime in ('bear_30d', 'flat_30d', 'bull_30d'):
        for direction in ('short', 'long'):
            sub = rep[(rep['regime'] == regime) & (rep['direction'] == direction)]
            label = f'{regime}_{direction}'
            show(summarize(sub, label))

    # ─── Bootstrap ────────────────────────────────────────────────────────
    print('\n=== BOOTSTRAP (1000 resamples of mean R) ===')
    r = rep['r_outcome'].values
    rng = np.random.default_rng(42)
    means = []
    maxDDs = []
    for _ in range(1000):
        s = rng.choice(r, len(r), replace=True)
        c = np.cumsum(s); p = np.maximum.accumulate(c)
        means.append(s.mean()); maxDDs.append((c-p).min())
    p05_m = np.percentile(means, 5)
    p50_m = np.percentile(means, 50)
    p95_m = np.percentile(means, 95)
    pos_frac = (np.array(means) > 0).mean()
    p05_dd = np.percentile(maxDDs, 5)
    p95_dd = np.percentile(maxDDs, 95)
    print(f'  meanR  p05={p05_m:+.3f}  p50={p50_m:+.3f}  p95={p95_m:+.3f}')
    print(f'  maxDD  p05={p05_dd:+.2f}  p95={p95_dd:+.2f}')
    print(f'  P(meanR > 0): {pos_frac:.1%}')
    print(f'  P(meanR > +0.1): {(np.array(means) > 0.1).mean():.1%}')

    # ─── Verdict ─────────────────────────────────────────────────────────
    headline = summarize(rep, 'ALL')
    print('\n' + '=' * 110)
    print('=== VERDICT ===')
    print('=' * 110)
    print(f'\n  mean R: {headline["mean_R"]:+.3f}')
    print(f'  WR:     {headline["WR"]:.0%}')
    print(f'  annual: {headline["annual_R"]:+.1f}R / yr')
    print(f'  MAR:    {headline["MAR"]:.2f}')
    print(f'  OOS R:  {headline["OOS_meanR"]:+.3f}')
    print(f'  Boot P(meanR > 0): {pos_frac:.1%}')
    if (headline['mean_R'] > 0.3 and headline['MAR'] > 2.0
            and headline['OOS_meanR'] > 0):
        print(f'\n  PASS: design a sleeve. Strong edge survives cost.')
    elif headline['mean_R'] > 0 and headline['OOS_meanR'] > 0:
        print(f'\n  MARGINAL: edge is real but small. Consider regime gating, '
              f'wider-width-only, or different stop/target.')
    else:
        print(f'\n  FAIL: structure B doesn\'t survive cost. Try different '
              f'stop/target or close hypothesis.')

    out_path = OUT_DIR / 'lvn_phase4_backtest_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'config': {
                'width_min': WIDTH_MIN, 'width_max': WIDTH_MAX,
                'stop_buffer_frac': STOP_BUFFER_FRAC,
                'cooldown_bars': COOLDOWN_BARS,
                'tif_bars': TIF_BARS, 'cost_bp': COST_BP,
            },
            'headline': headline,
            'bootstrap': {
                'meanR_p05': float(p05_m), 'meanR_p50': float(p50_m),
                'meanR_p95': float(p95_m), 'pos_frac': float(pos_frac),
                'maxDD_p05': float(p05_dd), 'maxDD_p95': float(p95_dd),
            },
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

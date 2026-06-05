"""Phase 3b of the LVN study: regime sensitivity of the LVN-fade signal.

Phase 3 found 60.57% reversal rate (FADE) when price enters an LVN — but
that's pooled across all market regimes. Phase 3b splits by BTC's 30d
return regime to check whether the fade is universal or bull/bear-specific.

Regime classification (same convention as TRIPLE_V3's [[chento-regime-filter]]):
  bear_30d:  BTC 30d return < -10%
  flat_30d:  -10% <= BTC 30d return <= +10%
  bull_30d:  BTC 30d return > +10%

For each regime, report:
  - continuation rate
  - reversal rate (the signal)
  - asymmetry (top vs bottom entries)
  - rate at wide-LVN bucket (2-5%, where Phase 3 showed peak edge)

Decision: if reversal rate >= 55% in all 3 regimes, signal is universal —
proceed to Phase 4 unconditionally. If one regime shows 50/50 or reversal,
design entries to skip that regime.
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
    VP_WINDOW_BARS, REBALANCE_BARS, MAX_LOOKAHEAD_BARS,
)


def main():
    print('Loading BTC 15m OHLC...')
    df = load_btc_15m()
    print(f'  {len(df)} bars')

    typ = ((df['high'] + df['low']) / 2).values
    vol = df['volume'].values
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    n_bars = len(df)

    # Compute BTC 30d return at each bar (forward-fill from daily resample)
    print('Computing BTC 30d return for regime classification...')
    daily_close = df['close'].resample('1D').last()
    daily_ret30 = daily_close.pct_change(30)
    ret30_15m = daily_ret30.reindex(df.index, method='ffill').values

    print('Identifying LVN zones + walking forward for entry events...')
    events = []
    rebalance_zones = []
    for idx_end in range(VP_WINDOW_BARS, n_bars, REBALANCE_BARS):
        vp = compute_vp_at(typ, vol, idx_end)
        if vp is None:
            continue
        bin_vols, bin_edges = vp
        zones = identify_lvn_zones(bin_vols, bin_edges)
        rebalance_zones.append((idx_end, zones))

    for ri, (idx_R, zones) in enumerate(rebalance_zones):
        next_R = (rebalance_zones[ri + 1][0]
                   if ri + 1 < len(rebalance_zones) else n_bars)
        end_walk = min(idx_R + REBALANCE_BARS, next_R, n_bars - 1)
        for zone_low, zone_high in zones:
            if zone_high <= zone_low:
                continue
            for i in range(idx_R + 1, end_walk):
                prev_close = closes[i - 1]
                curr_close = closes[i]
                if zone_low <= prev_close <= zone_high:
                    continue
                if not (zone_low <= curr_close <= zone_high):
                    continue
                entry_edge = 'bottom' if prev_close < zone_low else 'top'

                exit_edge = None
                exit_bars = None
                for j in range(i + 1, min(i + MAX_LOOKAHEAD_BARS + 1, n_bars)):
                    if lows[j] > zone_high:
                        exit_edge = 'top'; exit_bars = j - i; break
                    if highs[j] < zone_low:
                        exit_edge = 'bottom'; exit_bars = j - i; break
                if exit_edge is None:
                    continue

                # Regime at entry time
                ret30 = ret30_15m[i]
                if np.isnan(ret30):
                    regime = 'unknown'
                elif ret30 < -0.10:
                    regime = 'bear_30d'
                elif ret30 > 0.10:
                    regime = 'bull_30d'
                else:
                    regime = 'flat_30d'

                events.append({
                    'idx_entry': i,
                    'regime': regime,
                    'ret_30d': float(ret30) if not np.isnan(ret30) else None,
                    'zone_width_pct': (zone_high - zone_low) / closes[i],
                    'entry_edge': entry_edge,
                    'exit_edge': exit_edge,
                    'exit_bars': exit_bars,
                    'continuation': entry_edge != exit_edge,
                })

    rep = pd.DataFrame(events)
    print(f'  Total events: {len(rep)}')

    # ─── Headline by regime ────────────────────────────────────────────
    print('\n' + '=' * 95)
    print('=== REVERSAL RATE BY REGIME (the LVN-fade signal strength) ===')
    print('=' * 95)
    print(f'\n  {"regime":<12s} {"n":>6s} {"cont%":>7s} {"rev%":>7s} '
          f'{"bottom_rev%":>13s} {"top_rev%":>10s}')
    by_regime = {}
    for regime in ('bear_30d', 'flat_30d', 'bull_30d'):
        sub = rep[rep['regime'] == regime]
        if len(sub) == 0:
            continue
        cont = sub['continuation'].mean()
        rev = 1 - cont
        bot = sub[sub['entry_edge'] == 'bottom']
        top = sub[sub['entry_edge'] == 'top']
        bot_rev = (1 - bot['continuation'].mean()) if len(bot) > 0 else None
        top_rev = (1 - top['continuation'].mean()) if len(top) > 0 else None
        print(f'  {regime:<12s} {len(sub):>6d} {cont*100:>6.2f}% '
              f'{rev*100:>6.2f}% '
              f'{bot_rev*100 if bot_rev is not None else 0:>12.2f}% '
              f'{top_rev*100 if top_rev is not None else 0:>9.2f}%')
        by_regime[regime] = {
            'n': int(len(sub)),
            'cont_pct': round(float(cont)*100, 2),
            'rev_pct': round(float(rev)*100, 2),
            'bottom_rev_pct': round(float(bot_rev)*100, 2) if bot_rev is not None else None,
            'top_rev_pct': round(float(top_rev)*100, 2) if top_rev is not None else None,
            'n_bottom': int(len(bot)),
            'n_top': int(len(top)),
        }

    # ─── Width bucket × regime cross-tab ───────────────────────────────
    print('\n=== REVERSAL RATE: WIDTH BUCKET × REGIME ===')
    width_buckets = [
        ('<0.5%', 0, 0.005),
        ('0.5-1%', 0.005, 0.01),
        ('1-2%', 0.01, 0.02),
        ('2-5%', 0.02, 0.05),
        ('5%+', 0.05, np.inf),
    ]
    print(f'\n  {"width":<10s} {"bear_n":>8s} {"bear_rev%":>10s}    '
          f'{"flat_n":>8s} {"flat_rev%":>10s}    '
          f'{"bull_n":>8s} {"bull_rev%":>10s}')
    width_x_regime = {}
    for label, lo, hi in width_buckets:
        sub = rep[(rep['zone_width_pct'] >= lo) & (rep['zone_width_pct'] < hi)]
        row = {'label': label}
        cells = [label]
        for regime in ('bear_30d', 'flat_30d', 'bull_30d'):
            ss = sub[sub['regime'] == regime]
            if len(ss) > 0:
                rev = (1 - ss['continuation'].mean()) * 100
                cells.append(f'{len(ss):>8d}  {rev:>8.2f}%')
                row[regime + '_n'] = int(len(ss))
                row[regime + '_rev_pct'] = round(float(rev), 2)
            else:
                cells.append(f'{0:>8d}  {0:>8s}')
        width_x_regime[label] = row
        print(f'  {cells[0]:<10s} {cells[1]:<20s} {cells[2]:<20s} {cells[3]:<20s}')

    # ─── Verdict ─────────────────────────────────────────────────────────
    print('\n' + '=' * 95)
    print('=== VERDICT ===')
    print('=' * 95)
    rev_rates = [by_regime[r]['rev_pct'] for r in ('bear_30d', 'flat_30d', 'bull_30d')
                  if r in by_regime]
    min_rev = min(rev_rates)
    print(f'\n  Reversal rates across regimes: '
           f'{[f"{r:.1f}%" for r in rev_rates]}')
    print(f'  Worst regime reversal rate: {min_rev:.2f}%')
    if min_rev >= 55:
        print(f'\n  CONCLUSION: LVN-fade is UNIVERSAL across regimes — proceed to '
              f'Phase 4 without regime filter.')
    elif min_rev >= 50:
        print(f'\n  CONCLUSION: LVN-fade is WEAK in worst regime ({min_rev:.1f}%). '
              f'Consider regime-conditional entries.')
    else:
        weakest = min(by_regime, key=lambda r: by_regime[r]['rev_pct'])
        print(f'\n  CONCLUSION: LVN-fade FAILS in {weakest} regime '
              f'({min_rev:.1f}% reversal). Design entries that SKIP this regime.')

    out_path = OUT_DIR / 'lvn_phase3b_regime_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'n_events': int(len(rep)),
            'by_regime': by_regime,
            'width_x_regime': width_x_regime,
            'min_regime_rev_pct': float(min_rev),
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

"""Phase 1 of the whale-absorption scalp sleeve research (plan
joyful-singing-leaf.md, 2026-06-05).

Tests two signatures at parameterizable cadence (1m, 5m, 15m) on BTCUSDT:
  absorb_short_z = whale_buy_usd_z  -  ret_z    (heavy whale buy, price NOT up)
  absorb_long_z  = whale_sell_usd_z +  ret_z    (heavy whale sell, price NOT down)

Both polarities tested (fade vs follow):
  fade   = trade against whale direction (original absorption thesis)
  follow = trade with whale direction (accumulation/distribution thesis)

All z-scoring is BACKWARD-ONLY rolling (no shift, no centered) — same rule
the lookahead audit just enforced. Sweep grid uses TIME-scaled windows so
the same minutes-of-history are used across cadences.

Decision gate for P1 -> P2:
  At least one (direction, polarity, combo) with n >= 100 triggers
  AND mean R > +0.3 AND both 50/50 IS+OOS halves positive.

Usage:
  python -m studies.notebooks.whale_absorption.research [--cadence 1m|5m|15m]
"""
from __future__ import annotations

import argparse
import itertools
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

COST_BP_DEFAULT = 18.0
COST_BP = COST_BP_DEFAULT  # overridden in main via --cost-bp

# Cadence definitions: (table-suffix, minutes-per-bar, ohlc-table)
CADENCE_CONFIG = {
    '1m':  {'minutes': 1,  'ohlc_table': 'screener_klines_1m',
             'agg_table': 'binance_agg_trades_1m'},
    '5m':  {'minutes': 5,  'ohlc_table': 'screener_klines_5m',
             'agg_table': 'binance_agg_trades_5m'},
    '15m': {'minutes': 15, 'ohlc_table': 'cd_futures_15m',
             'agg_table': 'binance_agg_trades_15m'},
}

# Sweep grid is TIME-based; per-cadence bars derived in main()
Z_WINDOWS_MINUTES = (60, 240, 480)     # 1h, 4h, 8h
Z_THRESHOLDS = (1.5, 2.0, 2.5)
COOLDOWN_MINUTES = (15, 30)             # 15m, 30m
ATR_MULTS = (1.5, 2.5)
TARGET_RS = (1.5, 2.5)
TIF_MINUTES = (60, 240)                 # 1h, 4h


# ─── Data loaders ──────────────────────────────────────────────────────────

def load_ohlc(cadence: str, start_ts: int, end_ts: int) -> pd.DataFrame:
    cfg = CADENCE_CONFIG[cadence]
    table = cfg['ohlc_table']
    con = sqlite3.connect(str(DB))
    # cd_futures_15m has no asset column (BTC-only); screener_klines_* do
    if table.startswith('cd_futures'):
        df = pd.read_sql(f"""
            SELECT timestamp, open, high, low, close
            FROM {table}
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp
        """, con, params=(start_ts, end_ts))
    else:
        df = pd.read_sql(f"""
            SELECT ts AS timestamp, open, high, low, close
            FROM {table}
            WHERE asset = 'BTCUSDT' AND ts >= ? AND ts <= ?
            ORDER BY ts
        """, con, params=(start_ts, end_ts))
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True).dt.as_unit('ns')
    return df.set_index('ts').drop(columns='timestamp')


def load_agg(cadence: str, start_ts: int, end_ts: int) -> pd.DataFrame:
    cfg = CADENCE_CONFIG[cadence]
    table = cfg['agg_table']
    con = sqlite3.connect(str(DB))
    df = pd.read_sql(f"""
        SELECT timestamp, whale_buy_usd, whale_sell_usd,
               mid_buy_usd, mid_sell_usd, retail_buy_usd, retail_sell_usd,
               n_trades
        FROM {table}
        WHERE asset = 'BTCUSDT' AND timestamp >= ? AND timestamp <= ?
        ORDER BY timestamp
    """, con, params=(start_ts, end_ts))
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True).dt.as_unit('ns')
    return df.set_index('ts').drop(columns='timestamp')


def agg_trades_range(cadence: str) -> tuple[int, int]:
    table = CADENCE_CONFIG[cadence]['agg_table']
    con = sqlite3.connect(str(DB))
    rng = con.execute(
        f"SELECT MIN(timestamp), MAX(timestamp) FROM {table} "
        "WHERE asset = 'BTCUSDT'"
    ).fetchone()
    con.close()
    return rng[0], rng[1]


# ─── Features ──────────────────────────────────────────────────────────────

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df['high'], df['low'], df['close']
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_zscores(df: pd.DataFrame, window: int) -> pd.DataFrame:
    """Add whale_buy_z / whale_sell_z / ret_z for given trailing window.
    All rolling windows BACKWARD-ONLY (no shift, no centered)."""
    out = df.copy()
    minp = max(2, min(window, window // 4))

    wb = out['whale_buy_usd'].fillna(0)
    ws = out['whale_sell_usd'].fillna(0)
    wb_mu = wb.rolling(window, min_periods=minp).mean()
    wb_sd = wb.rolling(window, min_periods=minp).std()
    ws_mu = ws.rolling(window, min_periods=minp).mean()
    ws_sd = ws.rolling(window, min_periods=minp).std()
    out['whale_buy_z'] = (wb - wb_mu) / wb_sd
    out['whale_sell_z'] = (ws - ws_mu) / ws_sd

    ret = np.log(out['close'] / out['close'].shift(1))
    ret_mu = ret.rolling(window, min_periods=minp).mean()
    ret_sd = ret.rolling(window, min_periods=minp).std()
    out['ret_z'] = (ret - ret_mu) / ret_sd
    return out


# ─── Trigger generation ────────────────────────────────────────────────────

def generate_triggers(df: pd.DataFrame, *,
                      entry_direction: str,
                      polarity: str,
                      z_threshold: float,
                      cooldown_bars: int) -> list[int]:
    """Return list of integer indices where trigger fires.
    polarity='fade': original absorption thesis (heavy whale buy -> price NOT up -> SHORT)
    polarity='follow': accumulation thesis (heavy whale buy -> price NOT up YET -> LONG)
    """
    # The two signature flavors compute the SAME indicator;
    # whether we enter LONG or SHORT is determined by polarity x signature pairing.
    abs_short_sig = df['whale_buy_z'] - df['ret_z']    # high = heavy buy, weak return
    abs_long_sig = df['whale_sell_z'] + df['ret_z']    # high = heavy sell, strong return
    if polarity == 'fade':
        sig = abs_short_sig if entry_direction == 'short' else abs_long_sig
    else:  # follow
        # heavy buy & weak return -> follow LONG (accumulation): use abs_short_sig
        # heavy sell & strong return -> follow SHORT (distribution): use abs_long_sig
        sig = abs_short_sig if entry_direction == 'long' else abs_long_sig
    fires = (sig > z_threshold) & df['atr'].notna() & df['close'].notna()
    idx_list = []
    last_fire = -10**9
    for i in np.flatnonzero(fires.values):
        if i - last_fire < cooldown_bars:
            continue
        idx_list.append(i)
        last_fire = i
    return idx_list


# ─── Replay (intra-bar walk, backward-only — matches Fix A pattern) ───────

def replay_trigger(df: pd.DataFrame, idx: int, *,
                   direction: str,
                   atr_mult: float, target_R: float,
                   tif_bars: int) -> dict | None:
    atr = float(df['atr'].iloc[idx])
    if not np.isfinite(atr) or atr <= 0:
        return None
    entry = float(df['close'].iloc[idx])
    risk = atr * atr_mult
    if risk <= 0:
        return None
    if direction == 'long':
        stop = entry - risk
        target = entry + risk * target_R
    else:
        stop = entry + risk
        target = entry - risk * target_R
    cost_R = (COST_BP / 10000.0) * (entry / risk)

    start = idx + 1
    end = min(start + tif_bars, len(df))
    exit_kind = None
    last_close = entry
    r_out = None
    for j in range(start, end):
        bh = float(df['high'].iloc[j])
        bl = float(df['low'].iloc[j])
        bc = float(df['close'].iloc[j])
        last_close = bc
        if direction == 'long':
            if bl <= stop:
                r_out = (stop - entry) / risk - cost_R
                exit_kind = 'stop'; break
            if bh >= target:
                r_out = (target - entry) / risk - cost_R
                exit_kind = 'target'; break
        else:
            if bh >= stop:
                r_out = (entry - stop) / risk - cost_R
                exit_kind = 'stop'; break
            if bl <= target:
                r_out = (entry - target) / risk - cost_R
                exit_kind = 'target'; break
    if r_out is None:
        r_out = ((last_close - entry) / risk - cost_R if direction == 'long'
                  else (entry - last_close) / risk - cost_R)
        exit_kind = 'tif'
    return {
        'ts': df.index[idx], 'direction': direction,
        'entry': entry, 'atr': atr, 'risk': risk,
        'r_outcome': r_out, 'exit_kind': exit_kind,
    }


# ─── Summary stats ─────────────────────────────────────────────────────────

def summarize(rep: pd.DataFrame, label: str, split_ts: pd.Timestamp) -> dict:
    if rep.empty:
        return {'label': label, 'n': 0}
    rep = rep.sort_values('ts').reset_index(drop=True)
    cum = rep['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    is_set = rep[rep['ts'] <= split_ts]
    oos_set = rep[rep['ts'] > split_ts]
    span_s = (rep['ts'].max() - rep['ts'].min()).total_seconds()
    span_w = span_s / (7 * 86400) if span_s > 0 else 0.0
    n_per_wk = len(rep) / span_w if span_w > 0 else 0
    annual_R = float(rep['r_outcome'].mean()) * len(rep) / max(span_s / (365.25 * 86400), 0.05)
    s = {
        'label': label, 'n': int(len(rep)),
        'n_per_wk': round(n_per_wk, 2),
        'mean_R': round(float(rep['r_outcome'].mean()), 3),
        'wr': round(float((rep['r_outcome'] > 0).mean()), 3),
        'cum_R': round(float(cum[-1]), 2),
        'max_dd_R': round(float(dd.min()), 2),
        'annual_R': round(annual_R, 1),
        'IS_meanR': round(float(is_set['r_outcome'].mean()) if len(is_set) else 0, 3),
        'IS_n': int(len(is_set)),
        'OOS_meanR': round(float(oos_set['r_outcome'].mean()) if len(oos_set) else 0, 3),
        'OOS_n': int(len(oos_set)),
        'stops_pct': round(float((rep['exit_kind'] == 'stop').mean()), 2),
        'tgts_pct': round(float((rep['exit_kind'] == 'target').mean()), 2),
        'tif_pct': round(float((rep['exit_kind'] == 'tif').mean()), 2),
    }
    s['MAR'] = round(annual_R / abs(s['max_dd_R']), 2) if s['max_dd_R'] != 0 else 0
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cadence', choices=list(CADENCE_CONFIG.keys()), default='15m')
    ap.add_argument('--cost-bp', type=float, default=COST_BP_DEFAULT,
                     help='Round-trip cost in basis points. 0=raw signal test.')
    args = ap.parse_args()

    global COST_BP
    COST_BP = args.cost_bp
    cadence = args.cadence
    mpb = CADENCE_CONFIG[cadence]['minutes']
    z_windows = tuple(w // mpb for w in Z_WINDOWS_MINUTES)
    cooldowns = tuple(max(1, m // mpb) for m in COOLDOWN_MINUTES)
    tifs = tuple(m // mpb for m in TIF_MINUTES)
    print(f'Cadence: {cadence} ({mpb}m/bar). z_windows={z_windows}, '
           f'cooldowns={cooldowns}, tifs={tifs}')

    # Overlap of OHLC and agg-trades ranges
    agg_start, agg_end = agg_trades_range(cadence)
    print(f'AggTrades range: {datetime.fromtimestamp(agg_start, tz=timezone.utc)} '
           f'-> {datetime.fromtimestamp(agg_end, tz=timezone.utc)}')

    df_ohlc = load_ohlc(cadence, agg_start, agg_end)
    df_agg = load_agg(cadence, agg_start, agg_end)
    print(f'OHLC bars: {len(df_ohlc)}, agg_trades bars: {len(df_agg)}')

    df = df_ohlc.join(df_agg, how='inner')
    df = df[~df.index.duplicated(keep='last')]
    df = df.sort_index()
    df['atr'] = compute_atr(df, period=14)
    print(f'Merged bars: {len(df)}')

    # 50/50 IS/OOS split
    split_ts = df.index[len(df) // 2]
    print(f'IS/OOS split at: {split_ts}')

    # Precompute z-scores once per z_window
    df_zcache = {}
    for w in z_windows:
        print(f'  computing z-scores at window={w} bars...')
        df_zcache[w] = compute_zscores(df, w)

    results = []
    print(f'\n{"label":<48s} {"n":>4s} {"/wk":>5s} '
           f'{"meanR":>7s} {"WR":>4s} {"maxDD":>7s} {"annR":>6s} {"MAR":>5s} '
           f'{"IS_meanR":>9s} {"OOS_meanR":>10s} {"stp/tgt/tif":>13s}')

    for polarity in ('fade', 'follow'):
      for direction in ('long', 'short'):
        for z_w, z_t, cd, am, tr, tif in itertools.product(
                z_windows, Z_THRESHOLDS, cooldowns,
                ATR_MULTS, TARGET_RS, tifs):
            dfz = df_zcache[z_w]
            idxs = generate_triggers(dfz, entry_direction=direction,
                                       polarity=polarity,
                                       z_threshold=z_t, cooldown_bars=cd)
            if not idxs:
                continue
            rows = []
            for i in idxs:
                r = replay_trigger(dfz, i, direction=direction,
                                     atr_mult=am, target_R=tr,
                                     tif_bars=tif)
                if r is not None:
                    rows.append(r)
            if not rows:
                continue
            rep = pd.DataFrame(rows)
            label = (f'{polarity[:3]}_{direction}_zw{z_w}_zt{z_t:.1f}_cd{cd}_'
                      f'am{am:.1f}_tr{tr:.1f}_tif{tif}')
            s = summarize(rep, label, split_ts)
            s['polarity'] = polarity
            s['direction'] = direction
            s['z_window'] = z_w
            s['z_threshold'] = z_t
            s['cooldown_bars'] = cd
            s['atr_mult'] = am
            s['target_R'] = tr
            s['tif_bars'] = tif
            results.append(s)
            print(f'{label:<48s} {s["n"]:>4d} {s["n_per_wk"]:>5.1f} '
                   f'{s["mean_R"]:>+7.3f} {s["wr"]:>4.0%} '
                   f'{s["max_dd_R"]:>+7.2f} {s["annual_R"]:>+6.1f} '
                   f'{s["MAR"]:>5.2f} {s["IS_meanR"]:>+9.3f} '
                   f'{s["OOS_meanR"]:>+10.3f}({s["OOS_n"]:>2d}) '
                   f'{s["stops_pct"]:>3.0%}/{s["tgts_pct"]:>3.0%}/{s["tif_pct"]:>3.0%}')

    print('\n' + '=' * 110)
    print('=== P1 GATE evaluation ===')
    print('=' * 110)
    print('  Gate: at least one combo with n >= 100 AND mean_R > +0.3 AND both IS+OOS halves positive')
    candidates = [r for r in results
                  if r['n'] >= 100
                  and r['mean_R'] > 0.3
                  and r['IS_meanR'] > 0
                  and r['OOS_meanR'] > 0]
    if candidates:
        candidates.sort(key=lambda r: r['mean_R'] * np.sqrt(r['n']), reverse=True)
        print(f'\n  PASS — {len(candidates)} combos clear the gate. Top 10 by meanR * sqrt(n):')
        for r in candidates[:10]:
            print(f'    {r["label"]:<48s} n={r["n"]:>3d} meanR={r["mean_R"]:+.3f} '
                   f'IS={r["IS_meanR"]:+.3f}({r["IS_n"]}) '
                   f'OOS={r["OOS_meanR"]:+.3f}({r["OOS_n"]}) '
                   f'MAR={r["MAR"]:.2f}')
        gate_pass = True
    else:
        print('\n  FAIL — no combo clears the gate. Print top-5 by mean_R for inspection:')
        results.sort(key=lambda r: r.get('mean_R', -99), reverse=True)
        for r in results[:5]:
            print(f'    {r["label"]:<48s} n={r["n"]:>3d} meanR={r["mean_R"]:+.3f} '
                   f'IS={r["IS_meanR"]:+.3f}({r["IS_n"]}) '
                   f'OOS={r["OOS_meanR"]:+.3f}({r["OOS_n"]})')
        gate_pass = False

    cost_tag = f'_cost{int(COST_BP):d}bp'
    out_path = OUT_DIR / f'whale_absorption_phase1_results_{cadence}{cost_tag}.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'asset': 'BTCUSDT',
            'cadence': cadence,
            'data_range_utc': [
                datetime.fromtimestamp(agg_start, tz=timezone.utc).isoformat(),
                datetime.fromtimestamp(agg_end, tz=timezone.utc).isoformat(),
            ],
            'split_ts_utc': split_ts.isoformat(),
            'cost_bp': COST_BP,
            'sweep_grid': {
                'z_windows_bars': list(z_windows),
                'z_windows_minutes': list(Z_WINDOWS_MINUTES),
                'z_thresholds': list(Z_THRESHOLDS),
                'cooldown_bars': list(cooldowns),
                'cooldown_minutes': list(COOLDOWN_MINUTES),
                'atr_mults': list(ATR_MULTS),
                'target_Rs': list(TARGET_RS),
                'tif_bars': list(tifs),
                'tif_minutes': list(TIF_MINUTES),
            },
            'gate_pass': gate_pass,
            'n_results': len(results),
            'n_candidates': len(candidates) if gate_pass else 0,
            'all_results': results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

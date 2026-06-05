"""Phase 1 of the funding+CVD divergence scalp/swing research
(plan joyful-singing-leaf.md, 2026-06-05; second scalp candidate after
whale-absorption closed in commit bf3140a).

Thesis (causal microstructure story):
  When funding is deeply NEGATIVE (shorts paying longs heavily → shorts
  positioning-crowded) AND spot CVD is POSITIVE (real buyers absorbing
  despite the bearish positioning), the squeeze-long setup is real:
  shorts are over-extended and a liquidation cascade is mechanically
  likely.

  Symmetric for shorts: funding POSITIVE (longs crowded) + spot CVD
  NEGATIVE (real sellers absorbing) → distribute-short.

Data:
  cd_funding_rate    — hourly OHLC of fr_close from 2019-09 to 2026-06
                        (cadence changed 2026-04-13 per memory; use
                        pre-cutover stable period only)
  cd_futures_15m     — spot CVD via quote_volume_buy - quote_volume_sell,
                        15m bars from 2019-09 onwards

Both have 5+ years of history, vastly better than aggTrades' 13 weeks.

Decision gate for P1 -> P2 (unchanged from whale_absorption):
  At least one (direction, combo) with n >= 100 triggers AND mean R > +0.3
  AND both 50/50 IS+OOS halves positive.

Outputs printed grid + JSON at studies/material/chento/validation/.

Usage:
  python -m studies.notebooks.funding_cvd_divergence.research
        [--cost-bp 18] [--start 2020-01-01] [--end 2026-04-13]
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
COST_BP = COST_BP_DEFAULT

# Sweep grids (lean: signal params first, risk fixed at CHENTO_TRIPLE_V3 defaults)
FUNDING_Z_THRESHOLDS = (1.5, 2.0, 2.5)    # one-sided; symmetric for long/short
CVD_Z_THRESHOLDS = (0.5, 1.0, 1.5)
Z_WINDOW_DAYS = (14, 30, 60)
CVD_SUSTAIN_BARS = (4, 12, 24)             # last 1h, 3h, 6h must show CVD divergence
COOLDOWN_HOURS = (24, 48)

# Risk params fixed (chento_triple_v3 defaults)
ATR_MULT = 5.0
TARGET_R = 6.0
TIF_HOURS = 72


# ─── Data loaders ──────────────────────────────────────────────────────────

def load_btc_15m(start_ts: int, end_ts: int) -> pd.DataFrame:
    con = sqlite3.connect(str(DB))
    df = pd.read_sql("""
        SELECT timestamp, open, high, low, close,
               quote_volume_buy, quote_volume_sell
        FROM cd_futures_15m
        WHERE timestamp >= ? AND timestamp <= ?
        ORDER BY timestamp
    """, con, params=(start_ts, end_ts))
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True).dt.as_unit('ns')
    return df.set_index('ts').drop(columns='timestamp')


def load_funding(start_ts: int, end_ts: int) -> pd.Series:
    """Load hourly funding rate fr_close, indexed by ts."""
    con = sqlite3.connect(str(DB))
    df = pd.read_sql("""
        SELECT timestamp, fr_close
        FROM cd_funding_rate
        WHERE timestamp >= ? AND timestamp <= ?
        ORDER BY timestamp
    """, con, params=(start_ts, end_ts))
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True).dt.as_unit('ns')
    return df.set_index('ts')['fr_close']


# ─── Features ──────────────────────────────────────────────────────────────

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df['high'], df['low'], df['close']
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def attach_features(df_15m: pd.DataFrame, funding_h: pd.Series,
                     window_bars: int) -> pd.DataFrame:
    """Add funding_z, cvd_z, atr to the 15m frame using backward-only
    rolling windows of `window_bars` bars."""
    out = df_15m.copy()
    out['atr'] = compute_atr(out, period=14)

    # CVD per 15m bar
    cvd = out['quote_volume_buy'] - out['quote_volume_sell']
    minp = max(2, window_bars // 4)
    cvd_mu = cvd.rolling(window_bars, min_periods=minp).mean()
    cvd_sd = cvd.rolling(window_bars, min_periods=minp).std()
    out['cvd_z'] = (cvd - cvd_mu) / cvd_sd

    # Forward-fill funding onto the 15m grid (funding fires hourly, 15m bars
    # use the most recent funding value — purely backward).
    funding_15m = funding_h.reindex(out.index, method='ffill')
    # Funding window: same bars as CVD (so z_window_days drives both).
    f_mu = funding_15m.rolling(window_bars, min_periods=minp).mean()
    f_sd = funding_15m.rolling(window_bars, min_periods=minp).std()
    out['funding'] = funding_15m
    out['funding_z'] = (funding_15m - f_mu) / f_sd
    return out


# ─── Trigger generation ────────────────────────────────────────────────────

def generate_triggers(df: pd.DataFrame, *,
                      direction: str,
                      funding_z_threshold: float,
                      cvd_z_threshold: float,
                      cvd_sustain_bars: int,
                      cooldown_bars: int) -> list[int]:
    """LONG: funding_z < -F_THR (shorts crowded) AND last cvd_sustain bars all cvd_z > +C_THR (buyers absorbing)
    SHORT: funding_z > +F_THR AND last cvd_sustain bars all cvd_z < -C_THR
    Backward-only: uses rolling().min() / max() on cvd_z to check sustained agreement."""
    f_z = df['funding_z']
    c_z = df['cvd_z']
    if direction == 'long':
        funding_cond = f_z < -funding_z_threshold
        # All cvd_z in last `cvd_sustain_bars` must be > cvd_z_threshold
        cvd_min_recent = c_z.rolling(cvd_sustain_bars, min_periods=cvd_sustain_bars).min()
        cvd_cond = cvd_min_recent > cvd_z_threshold
    else:
        funding_cond = f_z > funding_z_threshold
        cvd_max_recent = c_z.rolling(cvd_sustain_bars, min_periods=cvd_sustain_bars).max()
        cvd_cond = cvd_max_recent < -cvd_z_threshold
    fires = funding_cond & cvd_cond & df['atr'].notna() & df['close'].notna()
    fires_idx = np.flatnonzero(fires.values)
    idx_list = []
    last_fire = -10**9
    for i in fires_idx:
        if i - last_fire < cooldown_bars:
            continue
        idx_list.append(i)
        last_fire = i
    return idx_list


# ─── Replay (intra-bar walking — same pattern as whale_absorption) ────────

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
    last_close = entry
    r_out = None
    exit_kind = None
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
        'funding_z': float(df['funding_z'].iloc[idx]),
        'cvd_z': float(df['cvd_z'].iloc[idx]),
        'r_outcome': r_out, 'exit_kind': exit_kind,
    }


# ─── Summary ───────────────────────────────────────────────────────────────

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
    span_y = span_s / (365.25 * 86400) if span_s > 0 else 0
    n_per_yr = len(rep) / span_y if span_y > 0 else 0
    annual_R = float(rep['r_outcome'].mean()) * len(rep) / max(span_y, 0.05)
    s = {
        'label': label, 'n': int(len(rep)),
        'n_per_yr': round(n_per_yr, 1),
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
    ap.add_argument('--cost-bp', type=float, default=COST_BP_DEFAULT)
    ap.add_argument('--start', type=str, default='2020-01-01')
    ap.add_argument('--end', type=str, default='2026-04-13')  # pre-cutover
    args = ap.parse_args()

    global COST_BP
    COST_BP = args.cost_bp
    start_ts = int(pd.Timestamp(args.start, tz='UTC').timestamp())
    end_ts = int(pd.Timestamp(args.end, tz='UTC').timestamp())
    print(f'Window: {args.start} -> {args.end} (cost={COST_BP}bp)')

    print('Loading 15m + funding...')
    df_15m = load_btc_15m(start_ts, end_ts)
    funding_h = load_funding(start_ts, end_ts)
    print(f'  15m bars: {len(df_15m)}, funding hourly: {len(funding_h)}')

    if df_15m.empty or funding_h.empty:
        print('  no data; aborting'); return

    # 50/50 IS/OOS split
    split_ts = df_15m.index[len(df_15m) // 2]
    print(f'IS/OOS split at: {split_ts}')

    # Precompute features per z_window (rolling z on cvd + funding at the same window)
    feat_cache = {}
    for w_days in Z_WINDOW_DAYS:
        bars = w_days * 96  # 96 bars/day at 15m
        print(f'  computing features at z_window={w_days}d ({bars} bars)...')
        feat_cache[w_days] = attach_features(df_15m, funding_h, bars)

    results = []
    print(f'\n{"label":<58s} {"n":>4s} {"/yr":>5s} '
           f'{"meanR":>7s} {"WR":>4s} {"maxDD":>7s} {"annR":>7s} {"MAR":>5s} '
           f'{"IS_meanR":>10s} {"OOS_meanR":>11s} {"stp/tgt/tif":>13s}')

    for direction in ('long', 'short'):
        for wd, fz, cz, sus, cd in itertools.product(
                Z_WINDOW_DAYS, FUNDING_Z_THRESHOLDS, CVD_Z_THRESHOLDS,
                CVD_SUSTAIN_BARS, COOLDOWN_HOURS):
            dff = feat_cache[wd]
            cooldown_bars = cd * 4  # 4 bars/hour
            tif_bars = TIF_HOURS * 4
            idxs = generate_triggers(dff, direction=direction,
                                       funding_z_threshold=fz,
                                       cvd_z_threshold=cz,
                                       cvd_sustain_bars=sus,
                                       cooldown_bars=cooldown_bars)
            if not idxs:
                continue
            rows = []
            for i in idxs:
                r = replay_trigger(dff, i, direction=direction,
                                     atr_mult=ATR_MULT, target_R=TARGET_R,
                                     tif_bars=tif_bars)
                if r is not None:
                    rows.append(r)
            if not rows:
                continue
            rep = pd.DataFrame(rows)
            label = (f'{direction}_wd{wd}_fz{fz:.1f}_cz{cz:.1f}_'
                      f'sus{sus}_cd{cd}h')
            s = summarize(rep, label, split_ts)
            s.update({'direction': direction, 'z_window_days': wd,
                       'funding_z_threshold': fz, 'cvd_z_threshold': cz,
                       'cvd_sustain_bars': sus, 'cooldown_hours': cd})
            results.append(s)
            print(f'{label:<58s} {s["n"]:>4d} {s["n_per_yr"]:>5.1f} '
                   f'{s["mean_R"]:>+7.3f} {s["wr"]:>4.0%} '
                   f'{s["max_dd_R"]:>+7.2f} {s["annual_R"]:>+7.1f} '
                   f'{s["MAR"]:>5.2f} {s["IS_meanR"]:>+9.3f}({s["IS_n"]:>3d}) '
                   f'{s["OOS_meanR"]:>+9.3f}({s["OOS_n"]:>3d}) '
                   f'{s["stops_pct"]:>3.0%}/{s["tgts_pct"]:>3.0%}/{s["tif_pct"]:>3.0%}')

    print('\n' + '=' * 120)
    print('=== P1 GATE evaluation ===')
    print('=' * 120)
    print('  Gate: at least one combo with n >= 100 AND mean_R > +0.3 AND both IS+OOS halves positive')
    cands = [r for r in results
              if r['n'] >= 100 and r['mean_R'] > 0.3
              and r['IS_meanR'] > 0 and r['OOS_meanR'] > 0]
    if cands:
        cands.sort(key=lambda r: r['mean_R'] * np.sqrt(r['n']), reverse=True)
        print(f'\n  PASS — {len(cands)} combos clear the gate. Top 10 by meanR * sqrt(n):')
        for r in cands[:10]:
            print(f'    {r["label"]:<58s} n={r["n"]:>3d} meanR={r["mean_R"]:+.3f} '
                   f'IS={r["IS_meanR"]:+.3f}({r["IS_n"]}) '
                   f'OOS={r["OOS_meanR"]:+.3f}({r["OOS_n"]}) '
                   f'MAR={r["MAR"]:.2f} annual={r["annual_R"]:+.1f}R')
        gate_pass = True
    else:
        print('\n  FAIL — no combo clears the gate. Top-10 by mean_R:')
        results.sort(key=lambda r: r.get('mean_R', -99), reverse=True)
        for r in results[:10]:
            print(f'    {r["label"]:<58s} n={r["n"]:>3d} meanR={r["mean_R"]:+.3f} '
                   f'IS={r["IS_meanR"]:+.3f}({r["IS_n"]}) '
                   f'OOS={r["OOS_meanR"]:+.3f}({r["OOS_n"]})')
        gate_pass = False

    cost_tag = f'_cost{int(COST_BP):d}bp'
    out_path = OUT_DIR / f'funding_cvd_phase1_results{cost_tag}.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'asset': 'BTC', 'cadence': '15m',
            'window_utc': [args.start, args.end],
            'split_ts_utc': split_ts.isoformat(),
            'cost_bp': COST_BP,
            'fixed_risk_params': {
                'atr_mult': ATR_MULT, 'target_R': TARGET_R,
                'tif_hours': TIF_HOURS,
            },
            'sweep_grid': {
                'funding_z_thresholds': list(FUNDING_Z_THRESHOLDS),
                'cvd_z_thresholds': list(CVD_Z_THRESHOLDS),
                'z_window_days': list(Z_WINDOW_DAYS),
                'cvd_sustain_bars': list(CVD_SUSTAIN_BARS),
                'cooldown_hours': list(COOLDOWN_HOURS),
            },
            'gate_pass': gate_pass,
            'n_candidates': len(cands) if gate_pass else 0,
            'all_results': results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

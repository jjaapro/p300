"""validation_exhaustion_scale_out: detect price-action exhaustion at MFE
and use it to scale out + move SL to BE on the remainder.

Two-part analysis.

Part A (diagnostic): For each completed trade, find the bar at which MFE
peaked. At that bar compute candidate exhaustion features:
  - RSI(14) on 15m close
  - Bar's velocity proxy: (close - open) / atr           (how strong the
    favorable-side bar was)
  - Up-bar / down-bar density in trailing N bars
  - Wick rejection ratio:   (close-low)/range for longs, etc.
  - CVD-vs-price divergence over trailing 4 bars
  - ATR z-score on bar (extreme vol = blow-off)
Then compare distributions for:
  - "kept_mfe":   trades that closed within 20% of MFE (good follow-through)
  - "gave_back":  trades that gave back ≥50% of MFE (eventual stop or scratch)

Part B (action): backtest a rule on Triple + atr4_t3R:
  - While in favorable territory AND MFE ≥ +0.5R:
    - If exhaustion fires intrabar (close-based), scale out X% at this close
      AND move stop to BE on remainder.
    - Continue trade with reduced size to target.
Variants:
  - exhaustion definition: RSI > {65,70,75} for longs / < {35,30,25} for shorts
  - scale-out fraction: 25/33/50/75
  - minimum MFE threshold: 0.5 / 0.75 / 1.0 R
"""
from __future__ import annotations

import json
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
TIF_BARS = 4 * 24
IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')


# === Indicators ============================================================

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Standard RSI Wilder."""
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    # Wilder smoothing = EMA with alpha=1/period
    avg_up = up.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_dn = down.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_up / avg_dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if 'atr' not in out.columns:
        out['atr'] = compute_atr(out, period=14)
    out['rsi14'] = rsi(out['close'], 14)
    out['rng'] = (out['high'] - out['low']).replace(0, np.nan)
    out['body_pct'] = (out['close'] - out['open']) / out['rng']
    out['upper_wick'] = (out['high'] - out[['close', 'open']].max(axis=1)) / out['rng']
    out['lower_wick'] = (out[['close', 'open']].min(axis=1) - out['low']) / out['rng']
    # ATR z-score 7d
    out['atr_z_7d'] = (out['atr']/out['close'] -
                       (out['atr']/out['close']).rolling(672, min_periods=200).mean()) / \
                      (out['atr']/out['close']).rolling(672, min_periods=200).std()
    # CVD bar: quote_buy - quote_sell
    out['mf_bar'] = out['quote_volume_buy'] - out['quote_volume_sell']
    return out


# === Replay (baseline + diagnostic) ========================================

def replay_baseline(trig, df: pd.DataFrame, *,
                     atr_mult: float = 4.0, target_r: float = 3.0,
                     tif_bars: int = TIF_BARS) -> dict | None:
    """Replay one trigger with baseline (no scale-out, no BE). Also records
    bar-of-MFE so we can study exhaustion features there."""
    direction = trig['direction']
    ts = trig['ts']
    idx = df.index.searchsorted(ts, side='right') - 1
    if idx < 0 or idx >= len(df) or df.index[idx] != ts:
        idx = df.index.searchsorted(ts, side='right') - 1
        if idx < 0:
            return None
    atr = float(df['atr'].iloc[idx])
    if pd.isna(atr) or atr <= 0:
        return None
    entry = float(df['close'].iloc[idx])
    risk = atr * atr_mult
    if risk <= 0:
        return None
    if direction == 'long':
        stop = entry - risk
        target = entry + risk * target_r
    else:
        stop = entry + risk
        target = entry - risk * target_r
    cost_R = (COST_BP / 10000.0) * (entry / risk)

    start = idx + 1
    end = min(start + tif_bars, len(df))
    if end <= start:
        return None

    max_fav_R = 0.0
    mfe_idx = None     # bar index where MFE peaked
    outcome = None
    exit_kind = None
    exit_ts = None
    for j in range(start, end):
        bh = float(df['high'].iloc[j])
        bl = float(df['low'].iloc[j])
        if direction == 'long':
            fav_r = (bh - entry) / risk
        else:
            fav_r = (entry - bl) / risk
        if fav_r > max_fav_R:
            max_fav_R = fav_r
            mfe_idx = j
        # check stop/target
        if direction == 'long':
            if bl <= stop:
                outcome = (stop - entry) / risk - cost_R
                exit_ts = df.index[j]; exit_kind = 'stop'; break
            if bh >= target:
                outcome = (target - entry) / risk - cost_R
                exit_ts = df.index[j]; exit_kind = 'target'; break
        else:
            if bh >= stop:
                outcome = (entry - stop) / risk - cost_R
                exit_ts = df.index[j]; exit_kind = 'stop'; break
            if bl <= target:
                outcome = (entry - target) / risk - cost_R
                exit_ts = df.index[j]; exit_kind = 'target'; break
    if outcome is None:
        last_close = float(df['close'].iloc[end - 1])
        if direction == 'long':
            outcome = (last_close - entry) / risk - cost_R
        else:
            outcome = (entry - last_close) / risk - cost_R
        exit_kind = 'tif'
        exit_ts = df.index[end - 1]

    # MFE features
    mfe_features = {}
    if mfe_idx is not None:
        # Use indicators at MFE bar
        for col in ('rsi14', 'body_pct', 'upper_wick', 'lower_wick',
                     'atr_z_7d', 'mf_bar'):
            if col in df.columns:
                mfe_features[col] = float(df[col].iloc[mfe_idx])
        # MFE bar's velocity vs prior 4 bars: fav-direction close-pct
        if mfe_idx >= 4:
            if direction == 'long':
                vel_now = (df['close'].iloc[mfe_idx] - df['close'].iloc[mfe_idx-1]) / df['close'].iloc[mfe_idx-1]
                vel_recent = (df['close'].iloc[mfe_idx] - df['close'].iloc[mfe_idx-4]) / df['close'].iloc[mfe_idx-4]
            else:
                vel_now = -(df['close'].iloc[mfe_idx] - df['close'].iloc[mfe_idx-1]) / df['close'].iloc[mfe_idx-1]
                vel_recent = -(df['close'].iloc[mfe_idx] - df['close'].iloc[mfe_idx-4]) / df['close'].iloc[mfe_idx-4]
            mfe_features['vel_now'] = float(vel_now)
            mfe_features['vel_recent'] = float(vel_recent)
            mfe_features['vel_decel'] = float(vel_now - vel_recent / 4)  # decel proxy
        # Bars since entry to MFE
        mfe_features['bars_to_mfe'] = int(mfe_idx - idx)

    return {
        'ts': ts, 'direction': direction, 'entry': entry,
        'stop': stop, 'target': target, 'risk': risk,
        'r_outcome': outcome, 'exit_kind': exit_kind,
        'max_fav_R': round(max_fav_R, 3),
        'idx': idx, 'mfe_idx': mfe_idx, 'end_idx': end,
        **{f'mfe_{k}': v for k, v in mfe_features.items()},
    }


def replay_exhaustion_rule(trig, df: pd.DataFrame, *,
                             atr_mult: float = 4.0, target_r: float = 3.0,
                             tif_bars: int = TIF_BARS,
                             min_mfe_R: float = 0.5,
                             rsi_long: float = 70.0,
                             rsi_short: float = 30.0,
                             require_decel: bool = False,
                             scale_out_frac: float = 0.50,
                             ) -> dict | None:
    """Replay one trigger with the exhaustion scale-out rule:

    While MFE >= min_mfe_R, on every bar close check:
      long:  rsi14 >= rsi_long  (optionally AND vel_now <= vel_recent/4)
      short: rsi14 <= rsi_short ...

    On first such bar close, exit `scale_out_frac` at this close and move
    stop to entry on remainder. Continue to target/stop/TIF.

    Final R outcome = scale_out_frac * r_at_partial + (1 - scale_out_frac) * r_at_final
    """
    direction = trig['direction']
    ts = trig['ts']
    idx = df.index.searchsorted(ts, side='right') - 1
    if idx < 0 or idx >= len(df) or df.index[idx] != ts:
        idx = df.index.searchsorted(ts, side='right') - 1
        if idx < 0:
            return None
    atr = float(df['atr'].iloc[idx])
    if pd.isna(atr) or atr <= 0:
        return None
    entry = float(df['close'].iloc[idx])
    risk = atr * atr_mult
    if risk <= 0:
        return None
    if direction == 'long':
        stop = entry - risk
        target = entry + risk * target_r
    else:
        stop = entry + risk
        target = entry - risk * target_r
    cost_R = (COST_BP / 10000.0) * (entry / risk)

    start = idx + 1
    end = min(start + tif_bars, len(df))
    if end <= start:
        return None

    partial_taken = False
    partial_r = 0.0       # R captured on the scale-out portion
    fav_R_now = 0.0       # most-recent favorable excursion
    outcome_remainder = None
    exit_ts = None
    exit_kind = None
    armed_be = False

    for j in range(start, end):
        bh = float(df['high'].iloc[j])
        bl = float(df['low'].iloc[j])
        bc = float(df['close'].iloc[j])

        # Update fav_R_now
        if direction == 'long':
            fav_R_now = max(fav_R_now, (bh - entry) / risk)
        else:
            fav_R_now = max(fav_R_now, (entry - bl) / risk)

        # 1. Stop/target on whole (or remaining) position
        if direction == 'long':
            if bl <= stop:
                r = (stop - entry) / risk - cost_R
                outcome_remainder = r
                exit_ts = df.index[j]
                exit_kind = 'stop_be' if armed_be and abs(stop - entry) < 1e-6 else 'stop'
                break
            if bh >= target:
                outcome_remainder = (target - entry) / risk - cost_R
                exit_ts = df.index[j]; exit_kind = 'target'; break
        else:
            if bh >= stop:
                r = (entry - stop) / risk - cost_R
                outcome_remainder = r
                exit_ts = df.index[j]
                exit_kind = 'stop_be' if armed_be and abs(stop - entry) < 1e-6 else 'stop'
                break
            if bl <= target:
                outcome_remainder = (entry - target) / risk - cost_R
                exit_ts = df.index[j]; exit_kind = 'target'; break

        # 2. Apply exhaustion scale-out rule at close
        if not partial_taken and fav_R_now >= min_mfe_R:
            rsi_val = df['rsi14'].iloc[j]
            if pd.isna(rsi_val):
                continue
            if direction == 'long':
                fires = rsi_val >= rsi_long
            else:
                fires = rsi_val <= rsi_short
            if require_decel and fires:
                if j >= 4:
                    if direction == 'long':
                        vel_now = (bc - df['close'].iloc[j-1]) / df['close'].iloc[j-1]
                        vel_rec = (bc - df['close'].iloc[j-4]) / df['close'].iloc[j-4] / 4
                    else:
                        vel_now = -(bc - df['close'].iloc[j-1]) / df['close'].iloc[j-1]
                        vel_rec = -(bc - df['close'].iloc[j-4]) / df['close'].iloc[j-4] / 4
                    fires = fires and (vel_now <= vel_rec)
            if fires:
                # Take scale_out_frac at bc, move stop to entry on remainder
                if direction == 'long':
                    partial_r = (bc - entry) / risk - cost_R
                else:
                    partial_r = (entry - bc) / risk - cost_R
                partial_taken = True
                armed_be = True
                stop = entry

    if outcome_remainder is None:
        last_close = float(df['close'].iloc[end - 1])
        if direction == 'long':
            outcome_remainder = (last_close - entry) / risk - cost_R
        else:
            outcome_remainder = (entry - last_close) / risk - cost_R
        exit_kind = 'tif'
        exit_ts = df.index[end - 1]

    if partial_taken:
        r_total = scale_out_frac * partial_r + (1 - scale_out_frac) * outcome_remainder
    else:
        r_total = outcome_remainder

    return {
        'ts': ts, 'direction': direction, 'entry': entry,
        'r_outcome': round(r_total, 4),
        'exit_kind': exit_kind,
        'partial_taken': partial_taken,
        'partial_r': round(partial_r, 4),
        'remainder_r': round(outcome_remainder, 4),
        'max_fav_R': round(fav_R_now, 3),
    }


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
        'IS_meanR': round(float(is_set['r_outcome'].mean()) if len(is_set) else 0, 3),
        'IS_n': int(len(is_set)),
        'OOS_meanR': round(float(oos_set['r_outcome'].mean()) if len(oos_set) else 0, 3),
        'OOS_n': int(len(oos_set)),
        'partial_pct': round(float(t['partial_taken'].mean())*100, 1) if 'partial_taken' in t.columns else 0,
    }


def main():
    print('Building Triple composite + indicators...')
    df = load_btc_15m()
    df_b1 = compute_moneyflow_signal(df)
    df_b1 = add_indicators(df_b1)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df, compute_lsr_extremes(load_lsr('BTC')))
    b7 = b7_alignment_triggers(compute_multitf_cvd(df), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    print(f'  Triple triggers: {len(triple)}')

    # === Part A: diagnostic ===
    print('\n--- Part A: feature distributions at MFE bar ---')
    rows = []
    for _, t in triple.iterrows():
        r = replay_baseline(t, df_b1)
        if r is not None:
            rows.append(r)
    diag = pd.DataFrame(rows)
    print(f'  Replayed: {len(diag)} trades')

    # Categorize: trades that gave back >=50% of MFE vs kept within 20% of MFE
    diag = diag[diag['max_fav_R'] >= 0.25].copy()  # only trades that got into +0.25R+
    diag['final_pct_of_mfe'] = diag.apply(
        lambda r: (r['r_outcome'] / r['max_fav_R']) if r['max_fav_R'] > 0 else 0, axis=1)
    # Buckets
    gave_back = diag[diag['final_pct_of_mfe'] < 0.3]      # gave back >70%
    kept_mfe = diag[diag['final_pct_of_mfe'] >= 0.7]      # kept >=70%
    print(f'  gave_back (kept <30% of MFE): n={len(gave_back)}')
    print(f'  kept_mfe (kept >=70% of MFE): n={len(kept_mfe)}')

    # Compare features at MFE bar
    features_check = ['mfe_rsi14', 'mfe_body_pct', 'mfe_upper_wick',
                       'mfe_lower_wick', 'mfe_atr_z_7d', 'mfe_vel_now',
                       'mfe_vel_recent', 'mfe_vel_decel', 'mfe_bars_to_mfe']
    print('\n  Feature comparison at MFE bar:')
    print(f'    {"feature":<25s} {"gave_back_median":>18s} {"kept_mfe_median":>18s} {"gave_back_n":>14s}')
    diff_summary = {}
    for f in features_check:
        if f not in diag.columns: continue
        gb = gave_back[f].dropna()
        km = kept_mfe[f].dropna()
        if len(gb) < 10 or len(km) < 10: continue
        gb_med = float(gb.median())
        km_med = float(km.median())
        print(f'    {f:<25s} {gb_med:>+18.3f} {km_med:>+18.3f} {len(gb):>14d}')
        diff_summary[f] = {'gave_back_median': round(gb_med, 4),
                            'kept_mfe_median': round(km_med, 4)}

    # Split by direction for RSI (sign-dependent feature)
    print('\n  RSI at MFE — long-only:')
    long_gb = gave_back[gave_back['direction'] == 'long']
    long_km = kept_mfe[kept_mfe['direction'] == 'long']
    print(f'    gave_back longs RSI median = {long_gb["mfe_rsi14"].median():.1f}  (n={len(long_gb)})')
    print(f'    kept_mfe longs RSI median  = {long_km["mfe_rsi14"].median():.1f}  (n={len(long_km)})')
    print(f'    RSI distribution (gave_back longs):')
    print(f'      p25={long_gb["mfe_rsi14"].quantile(.25):.1f}  '
           f'p50={long_gb["mfe_rsi14"].quantile(.50):.1f}  '
           f'p75={long_gb["mfe_rsi14"].quantile(.75):.1f}  '
           f'p90={long_gb["mfe_rsi14"].quantile(.90):.1f}')
    print(f'    pct of gave_back longs with RSI > 70: '
           f'{(long_gb["mfe_rsi14"] > 70).mean()*100:.0f}%')
    print(f'    pct of gave_back longs with RSI > 65: '
           f'{(long_gb["mfe_rsi14"] > 65).mean()*100:.0f}%')

    print('\n  RSI at MFE — short-only:')
    short_gb = gave_back[gave_back['direction'] == 'short']
    short_km = kept_mfe[kept_mfe['direction'] == 'short']
    print(f'    gave_back shorts RSI median = {short_gb["mfe_rsi14"].median():.1f}  (n={len(short_gb)})')
    print(f'    kept_mfe shorts RSI median  = {short_km["mfe_rsi14"].median():.1f}  (n={len(short_km)})')
    print(f'    pct of gave_back shorts with RSI < 30: '
           f'{(short_gb["mfe_rsi14"] < 30).mean()*100:.0f}%')
    print(f'    pct of gave_back shorts with RSI < 35: '
           f'{(short_gb["mfe_rsi14"] < 35).mean()*100:.0f}%')

    # === Part B: backtest the scale-out rule ===
    print('\n--- Part B: scale-out + BE on exhaustion ---')
    print(f'{"label":<48s} {"n":>4s} {"meanR":>7s} {"WR":>5s} {"cumR":>7s} {"maxDD":>7s} {"part%":>6s} {"IS":>9s} {"OOS":>9s}')
    base_rep = [replay_baseline(t, df_b1) for _, t in triple.iterrows()]
    base_df = pd.DataFrame([r for r in base_rep if r is not None])
    base_df['partial_taken'] = False
    sb = summarize(base_df, 'baseline_atr4_t3R')
    print(f'{sb["label"]:<48s} {sb["n"]:>4d} {sb["mean_R"]:>+7.3f} '
           f'{sb["wr"]:>5.2%} {sb["cum_R"]:>+7.1f} {sb["max_dd_R"]:>+7.1f} '
           f'{sb.get("partial_pct",0):>6.1f} '
           f'{sb["IS_meanR"]:>+9.3f} {sb["OOS_meanR"]:>+9.3f}')

    all_results = {'baseline': sb}
    variants = []
    for min_mfe in (0.5, 0.75, 1.0):
        for rsi_l in (65, 70, 75):
            for frac in (0.25, 0.50, 0.75):
                variants.append({
                    'label': f'mfe>={min_mfe}_RSI{rsi_l}_take{int(frac*100)}',
                    'min_mfe_R': min_mfe,
                    'rsi_long': rsi_l,
                    'rsi_short': 100 - rsi_l,
                    'scale_out_frac': frac,
                    'require_decel': False,
                })
    # A few decel-required variants
    for min_mfe in (0.5, 1.0):
        variants.append({
            'label': f'mfe>={min_mfe}_RSI70_take50_decel',
            'min_mfe_R': min_mfe,
            'rsi_long': 70, 'rsi_short': 30,
            'scale_out_frac': 0.5, 'require_decel': True,
        })

    for v in variants:
        label = v.pop('label')
        rows = []
        for _, t in triple.iterrows():
            r = replay_exhaustion_rule(t, df_b1, **v)
            if r is not None:
                rows.append(r)
        rep = pd.DataFrame(rows)
        s = summarize(rep, label)
        all_results[label] = s
        print(f'{s["label"]:<48s} {s["n"]:>4d} {s["mean_R"]:>+7.3f} '
               f'{s["wr"]:>5.2%} {s["cum_R"]:>+7.1f} {s["max_dd_R"]:>+7.1f} '
               f'{s.get("partial_pct",0):>6.1f} '
               f'{s["IS_meanR"]:>+9.3f} {s["OOS_meanR"]:>+9.3f}')

    # === Ranking ===
    ranked = sorted(
        [(k, v) for k, v in all_results.items()
         if v.get('n', 0) >= 100 and k != 'baseline'],
        key=lambda kv: kv[1].get('mean_R', 0), reverse=True)[:8]
    print('\n=== Top by mean R (n>=100) ===')
    for k, v in ranked:
        print(f'  {k:<48s}  meanR={v["mean_R"]:+.3f}  cumR={v["cum_R"]:+7.1f}  '
               f'maxDD={v["max_dd_R"]:+5.1f}  IS={v["IS_meanR"]:+.3f}  '
               f'OOS={v["OOS_meanR"]:+.3f}')

    ranked_dd = sorted(
        [(k, v) for k, v in all_results.items()
         if v.get('n', 0) >= 100 and k != 'baseline'],
        key=lambda kv: kv[1].get('max_dd_R', -99), reverse=True)[:8]
    print('\n=== Top by max-DD (less negative is better) ===')
    for k, v in ranked_dd:
        print(f'  {k:<48s}  maxDD={v["max_dd_R"]:+5.1f}  meanR={v["mean_R"]:+.3f}  '
               f'cumR={v["cum_R"]:+7.1f}')

    out_path = OUT_DIR / 'exhaustion_scale_out_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('Diagnose exhaustion at MFE + test scale-out/BE rules. '
                      'Triple + atr4_t3R baseline; RSI/velocity at MFE-bar.'),
            'diagnostic': {
                'gave_back_n': int(len(gave_back)),
                'kept_mfe_n': int(len(kept_mfe)),
                'feature_medians': diff_summary,
                'long_gave_back_rsi_p50': float(long_gb['mfe_rsi14'].median()),
                'long_kept_rsi_p50': float(long_km['mfe_rsi14'].median()),
                'short_gave_back_rsi_p50': float(short_gb['mfe_rsi14'].median()),
                'short_kept_rsi_p50': float(short_km['mfe_rsi14'].median()),
            },
            'variants': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

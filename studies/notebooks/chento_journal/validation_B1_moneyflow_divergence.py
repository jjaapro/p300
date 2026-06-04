"""validation_B1: chento's Rule 1 as a STANDALONE entry trigger.

EXPERIMENT, not for ship.

Chento Rule 1 (verbatim, from 2026-05-22 live):
  "While the money flow is positive, the price action is reacting weakly.
   It is going up very slowly and barely moving percentage-wise, so it
   will probably dump."

Proxy:
  money_flow_proxy[t] = z-score over rolling 30-day window of
                         taker_buy_quote_volume - taker_sell_quote_volume
                         on the 15m BTC perp bar
  price_velocity[t]   = z-score of pct_change over `velocity_window` 15m bars

Trigger SHORT  when:  money_flow z > +THRESH  AND  price_velocity z < +VELOCITY_MAX
Trigger LONG   when:  money_flow z < -THRESH  AND  price_velocity z > -VELOCITY_MAX

The bot is told: "whales are accumulating/distributing, but price isn't reacting
the way it should — fade the apparent momentum."

For each trigger:
  - entry = next 15m close
  - stop  = entry ± ATR(14, 15m bars) * 2
  - target = entry ± ATR * 2 * 2  (2R fixed)
  - TIF   = 24h

Output:
  - per-trigger ledger (ts, direction, entry, stop, target, R outcome)
  - aggregate stats (n, /yr, mean R, WR, T1 hit)
  - chento coverage delta vs trigger timestamps
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

CHENTO_TRADES = ROOT / 'studies' / 'material' / 'chento' / 'scan_aggregated' / 'trades.jsonl'


# === Data loaders ==========================================================

def load_btc_15m() -> pd.DataFrame:
    """Load BTC 15m perp OHLC + taker-buy/sell quote volumes."""
    con = sqlite3.connect(str(DB))
    df = pd.read_sql("""
        SELECT timestamp, open, high, low, close, volume, quote_volume,
               volume_buy, quote_volume_buy, volume_sell, quote_volume_sell
        FROM cd_futures_15m
        ORDER BY timestamp
    """, con)
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    df = df.set_index('ts').drop(columns='timestamp')
    df = df[~df.index.duplicated(keep='last')]
    return df


# === Signal computation ====================================================

def compute_moneyflow_signal(df: pd.DataFrame, *,
                              cvd_window_bars: int = 4 * 24 * 30,  # 30 days of 15m
                              velocity_window_bars: int = 4,         # 1 hour
                              ) -> pd.DataFrame:
    """Compute money-flow z-score and price-velocity z-score on the 15m frame.

    money_flow[t]   = (quote_buy - quote_sell) at bar t
    cvd_z[t]        = z-score of money_flow over trailing cvd_window_bars
    vel_pct[t]      = pct_change over trailing velocity_window_bars
    vel_z[t]        = z-score of vel_pct over trailing cvd_window_bars

    Returns df with added cols: cvd_z, vel_z.
    """
    out = df.copy()
    mf = out['quote_volume_buy'] - out['quote_volume_sell']
    cvd_mean = mf.rolling(cvd_window_bars, min_periods=cvd_window_bars // 4).mean()
    cvd_std = mf.rolling(cvd_window_bars, min_periods=cvd_window_bars // 4).std()
    out['cvd_z'] = (mf - cvd_mean) / cvd_std

    vel = out['close'].pct_change(velocity_window_bars)
    vel_mean = vel.rolling(cvd_window_bars, min_periods=cvd_window_bars // 4).mean()
    vel_std = vel.rolling(cvd_window_bars, min_periods=cvd_window_bars // 4).std()
    out['vel_z'] = (vel - vel_mean) / vel_std
    return out


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR on 15m bars."""
    h, l, c = df['high'], df['low'], df['close']
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


# === Trigger generation =====================================================

def b1_triggers(df_enriched: pd.DataFrame, *,
                cvd_threshold: float = 1.0,
                velocity_max: float = 0.5,
                cooldown_bars: int = 4 * 6,  # 6h between fires
                atr_mult_stop: float = 2.0,
                target_r: float = 2.0,
                ) -> pd.DataFrame:
    """Generate B1 triggers.

    SHORT: cvd_z > +cvd_threshold AND |vel_z| < velocity_max (positive flow, weak price)
           => bearish bias
    LONG:  cvd_z < -cvd_threshold AND |vel_z| < velocity_max (negative flow, weak price)
           => bullish reversal (selling exhausted)
    """
    df = df_enriched.copy()
    df['atr'] = compute_atr(df, period=14)

    short_mask = (df['cvd_z'] > cvd_threshold) & (df['vel_z'].abs() < velocity_max)
    long_mask = (df['cvd_z'] < -cvd_threshold) & (df['vel_z'].abs() < velocity_max)

    rows = []
    last_trigger_idx = -10**9
    for i in range(len(df)):
        if i - last_trigger_idx < cooldown_bars:
            continue
        if pd.isna(df['cvd_z'].iloc[i]) or pd.isna(df['vel_z'].iloc[i]) \
                or pd.isna(df['atr'].iloc[i]):
            continue
        entry_price = float(df['close'].iloc[i])
        atr_val = float(df['atr'].iloc[i])
        risk = atr_val * atr_mult_stop
        if risk <= 0:
            continue
        ts = df.index[i]
        if short_mask.iloc[i]:
            rows.append({
                'ts': ts, 'direction': 'short',
                'entry': entry_price,
                'stop': entry_price + risk,
                'target': entry_price - risk * target_r,
                'cvd_z': float(df['cvd_z'].iloc[i]),
                'vel_z': float(df['vel_z'].iloc[i]),
                'atr': atr_val,
            })
            last_trigger_idx = i
        elif long_mask.iloc[i]:
            rows.append({
                'ts': ts, 'direction': 'long',
                'entry': entry_price,
                'stop': entry_price - risk,
                'target': entry_price + risk * target_r,
                'cvd_z': float(df['cvd_z'].iloc[i]),
                'vel_z': float(df['vel_z'].iloc[i]),
                'atr': atr_val,
            })
            last_trigger_idx = i
    return pd.DataFrame(rows)


# === R-outcome replay ======================================================

def measure_r_outcomes(triggers: pd.DataFrame, df_15m: pd.DataFrame, *,
                       tif_bars: int = 4 * 24,  # 24h
                       cost_bp: float = 18.0,
                       ) -> pd.DataFrame:
    """For each trigger, walk forward bars to find stop-hit, target-hit, or TIF.

    Cost model (corrected 2026-05-24):
        For 1 unit of size = 1R risk, notional = R / stop_distance_pct.
        Round-trip fee + slippage = cost_bp/10000 of notional.
        Cost in R-units = cost_bp/10000 * entry / risk.
        For BTC at $60k with 2*ATR stop ~ 1% = $600 risk, notional = $60k,
        cost = 0.0018 * 60000/600 = 0.18 R. Realistic.

    Earlier B1 versions used cost_R = cost_bp/10000 = 0.0018R flat which
    was 50-100x too low. The composite-OOS finding of +0.252R is overstated
    by ~0.10-0.15R per trade. This fix produces honest numbers.
    """
    if triggers.empty:
        return triggers
    out = triggers.copy()
    r_outcomes = []
    exit_kinds = []
    hold_hours = []

    for _, row in out.iterrows():
        ts = pd.Timestamp(row['ts'])
        entry = float(row['entry'])
        stop = float(row['stop'])
        target = float(row['target'])
        direction = row['direction']
        risk = abs(entry - stop)
        if risk == 0:
            r_outcomes.append(np.nan); exit_kinds.append('na'); hold_hours.append(np.nan)
            continue

        # Correct cost: round-trip of cost_bp on notional, expressed in R units
        cost_R = (cost_bp / 10000.0) * (entry / risk)

        # Walk forward bars
        start_pos = df_15m.index.searchsorted(ts, side='right')
        end_pos = min(start_pos + tif_bars, len(df_15m))
        fwd = df_15m.iloc[start_pos:end_pos]

        outcome = None; exit_kind = None; exit_ts = None
        if direction == 'long':
            for bts, bar in fwd.iterrows():
                bh = float(bar['high']); bl = float(bar['low'])
                if bl <= stop:
                    outcome = -1.0 - cost_R; exit_kind = 'stop'; exit_ts = bts; break
                if bh >= target:
                    outcome = (target - entry) / risk - cost_R; exit_kind = 'target'; exit_ts = bts; break
        else:  # short
            for bts, bar in fwd.iterrows():
                bh = float(bar['high']); bl = float(bar['low'])
                if bh >= stop:
                    outcome = -1.0 - cost_R; exit_kind = 'stop'; exit_ts = bts; break
                if bl <= target:
                    outcome = (entry - target) / risk - cost_R; exit_kind = 'target'; exit_ts = bts; break

        if outcome is None:
            # TIF: close at last bar's close
            close = float(fwd['close'].iloc[-1]) if len(fwd) > 0 else entry
            if direction == 'long':
                outcome = (close - entry) / risk - cost_R
            else:
                outcome = (entry - close) / risk - cost_R
            exit_kind = 'tif'
            exit_ts = fwd.index[-1] if len(fwd) > 0 else ts
        r_outcomes.append(outcome)
        exit_kinds.append(exit_kind)
        hold_hours.append((exit_ts - ts).total_seconds() / 3600.0 if exit_ts else np.nan)

    out['r_outcome'] = r_outcomes
    out['exit_kind'] = exit_kinds
    out['hold_hours'] = hold_hours
    return out


# === Chento coverage check =================================================

def chento_coverage(triggers: pd.DataFrame, asset: str = 'BTCUSDT',
                    window_hours_tight: float = 24,
                    window_hours_loose: float = 72) -> dict:
    """For each trigger, was there a chento trade in same asset+direction within
    the window? Returns dict with tight/loose coverage stats."""
    if triggers.empty:
        return {}
    rows = [json.loads(l) for l in CHENTO_TRADES.read_text(encoding='utf-8').splitlines() if l.strip()]
    chento = pd.DataFrame(rows)
    chento['ts'] = pd.to_datetime(chento['first_ts'], utc=True, errors='coerce')
    chento = chento.dropna(subset=['ts'])
    chento = chento[chento['asset'] == asset].copy()
    if chento.empty:
        return {'note': f'no chento trades on {asset}'}

    # Trigger -> chento same-dir within window
    trig_match_tight = 0
    trig_match_loose = 0
    trig_match_any_dir_loose = 0
    for _, t in triggers.iterrows():
        ts = pd.Timestamp(t['ts'])
        same_dir = chento[chento['direction'] == t['direction']]
        any_dir = chento
        delta_h_same = (same_dir['ts'] - ts).dt.total_seconds() / 3600.0
        delta_h_any = (any_dir['ts'] - ts).dt.total_seconds() / 3600.0
        if (delta_h_same.abs() <= window_hours_tight).any():
            trig_match_tight += 1
        if (delta_h_same.abs() <= window_hours_loose).any():
            trig_match_loose += 1
        if (delta_h_any.abs() <= window_hours_loose).any():
            trig_match_any_dir_loose += 1

    # Chento -> trigger same-dir within window (other direction of coverage)
    chento_in_trigger_window = chento[(chento['ts'] >= triggers['ts'].min())
                                       & (chento['ts'] <= triggers['ts'].max())]
    n_chento_window = len(chento_in_trigger_window)
    chento_match_tight = 0
    chento_match_loose = 0
    for _, c in chento_in_trigger_window.iterrows():
        same_dir = triggers[triggers['direction'] == c['direction']]
        if same_dir.empty:
            continue
        delta_h = (same_dir['ts'] - c['ts']).dt.total_seconds() / 3600.0
        if (delta_h.abs() <= window_hours_tight).any():
            chento_match_tight += 1
        if (delta_h.abs() <= window_hours_loose).any():
            chento_match_loose += 1

    return {
        'n_triggers': len(triggers),
        'n_chento_in_trigger_window': n_chento_window,
        'trigger_to_chento_tight': trig_match_tight,
        'trigger_to_chento_loose': trig_match_loose,
        'trigger_to_chento_any_dir_loose': trig_match_any_dir_loose,
        'trigger_to_chento_tight_rate': round(trig_match_tight / len(triggers), 3),
        'trigger_to_chento_loose_rate': round(trig_match_loose / len(triggers), 3),
        'chento_to_trigger_tight': chento_match_tight,
        'chento_to_trigger_loose': chento_match_loose,
        'chento_to_trigger_loose_rate': round(chento_match_loose / max(n_chento_window, 1), 3),
    }


# === Summary helpers =======================================================

def summarize_triggers(df: pd.DataFrame, label: str) -> dict:
    if df.empty:
        return {'label': label, 'n': 0}
    span_y = (df['ts'].max() - df['ts'].min()).total_seconds() / (365.25 * 86400)
    per_y = len(df) / max(span_y, 0.1)
    r_vals = df['r_outcome'].dropna()
    mean_r = float(r_vals.mean()) if len(r_vals) else 0
    median_r = float(r_vals.median()) if len(r_vals) else 0
    wr = float((r_vals > 0).mean()) if len(r_vals) else 0
    exit_counts = df['exit_kind'].value_counts().to_dict() if 'exit_kind' in df else {}
    direction_split = df['direction'].value_counts().to_dict() if 'direction' in df else {}
    # Annual return @ 2% NAV risk
    ann = ((1 + 0.02 * mean_r) ** per_y - 1) if per_y > 0 else 0.0
    return {
        'label': label, 'n': int(len(df)),
        'span_years': round(span_y, 2),
        'trades_per_year': round(per_y, 1),
        'mean_R': round(mean_r, 3),
        'median_R': round(median_r, 3),
        'win_rate': round(wr, 3),
        'exit_kinds': exit_counts,
        'direction_split': direction_split,
        'implied_annual_pct': round(ann * 100, 1),
    }


# === Main ==================================================================

def main():
    print(f'DB: {DB}')
    print('Loading BTC 15m perp...')
    df = load_btc_15m()
    print(f'  {len(df):,} bars  {df.index.min()} -> {df.index.max()}')

    print('\nComputing money-flow + price-velocity signals...')
    df_enr = compute_moneyflow_signal(df)
    valid = df_enr['cvd_z'].dropna()
    print(f'  cvd_z range: [{valid.min():.2f}, {valid.max():.2f}]  '
          f'mean={valid.mean():.2f}  std={valid.std():.2f}')

    # Param sweep on cvd_threshold to see signal frequency
    print('\nParameter sweep (cvd_threshold, velocity_max -> trigger count):')
    for cvd_t in (0.5, 1.0, 1.5, 2.0):
        for vel_max in (0.3, 0.5, 1.0):
            trigs = b1_triggers(df_enr, cvd_threshold=cvd_t, velocity_max=vel_max)
            print(f'  cvd_z>±{cvd_t}, |vel_z|<{vel_max}: {len(trigs)} triggers  '
                   f'({(trigs["direction"]=="short").sum()} short / '
                   f'{(trigs["direction"]=="long").sum()} long)')

    # === Run two reasonable variants ===
    all_results = {}
    for label, params in (
        ('B1_baseline_cvd1.0_vel0.5', {'cvd_threshold': 1.0, 'velocity_max': 0.5}),
        ('B1_loose_cvd0.5_vel1.0', {'cvd_threshold': 0.5, 'velocity_max': 1.0}),
        ('B1_strict_cvd1.5_vel0.3', {'cvd_threshold': 1.5, 'velocity_max': 0.3}),
    ):
        print(f'\n=== {label} ===')
        trigs = b1_triggers(df_enr, **params)
        if trigs.empty:
            print('  no triggers')
            continue
        print(f'  {len(trigs)} triggers, measuring R outcomes...')
        trigs_with_r = measure_r_outcomes(trigs, df_enr)
        summary = summarize_triggers(trigs_with_r, label=label)
        print(f'  n={summary["n"]}  /yr={summary["trades_per_year"]}  '
               f'mean R={summary["mean_R"]:+.3f}  WR={summary["win_rate"]:.0%}  '
               f'annual={summary["implied_annual_pct"]:+.1f}%')
        print(f'  exit kinds: {summary["exit_kinds"]}')
        print(f'  direction split: {summary["direction_split"]}')

        cov = chento_coverage(trigs_with_r, asset='BTCUSDT')
        print(f'  chento coverage:')
        print(f'    trigger->chento same-dir tight ({24}h): '
               f'{cov.get("trigger_to_chento_tight", 0)}/{cov.get("n_triggers", 0)} = '
               f'{cov.get("trigger_to_chento_tight_rate", 0):.1%}')
        print(f'    trigger->chento same-dir loose ({72}h): '
               f'{cov.get("trigger_to_chento_loose", 0)}/{cov.get("n_triggers", 0)} = '
               f'{cov.get("trigger_to_chento_loose_rate", 0):.1%}')
        print(f'    chento (in window={cov.get("n_chento_in_trigger_window",0)}) -> trigger same-dir loose: '
               f'{cov.get("chento_to_trigger_loose", 0)} = '
               f'{cov.get("chento_to_trigger_loose_rate", 0):.1%}')
        all_results[label] = {**summary, 'coverage': cov, 'params': params}

    out_path = OUT_DIR / 'B1_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'study_note': (
                "EXPERIMENT. Chento's Rule 1 (verbatim 2026-05-22 YT live): "
                "'money flow positive, price reacting weakly -> will dump'. "
                "Tested as STANDALONE entry trigger on BTC 15m perp. "
                "Proxy: taker-quote-volume buy-sell z-score over 30d window. "
                "Stop = 2 ATR, target = 2 R, TIF = 24h, cost = 18 bp RT."
            ),
            'variants': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

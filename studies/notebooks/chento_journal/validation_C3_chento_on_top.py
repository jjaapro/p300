"""validation_C3_chento_on_top: codify the CHENTO ON TOP composite indicator.

Per chento's stream and journal, the "CHENTO ON TOP" indicator combines several
contextual factors and produces a weighted composite quality score. Components
(per project_chento_tool_stack memory + chento corpus):

  1. Vol Spike  — ATR z-score on bar (extreme vol = blow-off setup)
  2. RSI Neutral — RSI ∈ [45, 55] AT ENTRY (RSI not extreme yet =
     "fresh setup"; extreme already-pushed price is "late")
  3. Session label — Asia / London / NY / overlap (already known: Asia wins)
  4. Multi-TF EMA confluence — already captured by B7 multi-TF CVD; using
     B7's z-score alignment as a proxy
  5. Planetary aspects — Sun-Moon separation (full/new moon), Mars-Saturn
     conjunction proximity, retrograde flags. Compute via skyfield DE421
     ephemeris.

Composite quality:
  Each component scored 0..1 (Quality). Composite = weighted mean.
  Threshold: trade only if composite_quality >= 0.7 AND all components >= 0.5
  (the "70% per-component AND overall" rule per the plan).

Tests:
  - Per-component lift on Triple composite (already-optimized config:
    atr5_t6R + no_tilt + no_resist_OB_2R)
  - Composite as additional filter on top of optimized config
  - Pure-planetary as standalone filter (does it have ANY independent edge?)
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
from studies.notebooks.chento_journal.validation_C5_smc_features import (
    compute_pivots, compute_smc_state, compute_order_blocks, compute_fvgs,
    replay_one,
)
from studies.notebooks.chento_journal.validation_exhaustion_scale_out import rsi

IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')


# === Planetary features ====================================================

def compute_planetary(ts_index: pd.DatetimeIndex) -> pd.DataFrame:
    """Compute planetary aspects for each timestamp.

    Returns DataFrame with columns:
      sun_moon_sep_deg     — Sun-Moon separation (0=new, 180=full)
      moon_phase_quality   — 1.0 at new/full moon, 0.0 at quarters
      mars_saturn_deg      — separation
      sun_jupiter_deg      — separation
      mercury_retrograde   — 1.0 if mercury apparent motion is retrograde
    """
    from skyfield.api import load
    ts_scale = load.timescale()
    eph = load('de421.bsp')
    sun = eph['sun']
    earth = eph['earth']
    moon = eph['moon']
    mars = eph['mars']
    saturn = eph['saturn barycenter']
    jupiter = eph['jupiter barycenter']
    mercury = eph['mercury']

    # Convert ts to JD efficiently — batch
    times = ts_scale.from_datetimes([t.to_pydatetime() for t in ts_index])
    e_at = earth.at(times)
    s_app = e_at.observe(sun).apparent()
    m_app = e_at.observe(moon).apparent()
    mars_app = e_at.observe(mars).apparent()
    sat_app = e_at.observe(saturn).apparent()
    jup_app = e_at.observe(jupiter).apparent()
    merc_app = e_at.observe(mercury).apparent()

    sun_moon_deg = s_app.separation_from(m_app).degrees
    mars_sat_deg = mars_app.separation_from(sat_app).degrees
    sun_jup_deg = s_app.separation_from(jup_app).degrees

    # Moon phase quality: 1.0 at 0 or 180 deg, 0.0 at 90 or 270
    # use cos(2*x) which is 1 at 0/180, -1 at 90/270. Map to [0,1]
    phase_q = (np.cos(np.radians(sun_moon_deg) * 2) + 1) / 2

    # Mercury retrograde: compute apparent ecliptic longitude at t and t+1d,
    # retrograde if longitude is decreasing.
    times_next = ts_scale.from_datetimes(
        [(t + pd.Timedelta(days=1)).to_pydatetime() for t in ts_index])
    e_at_next = earth.at(times_next)
    merc_app_next = e_at_next.observe(mercury).apparent()
    # ecliptic_latlon returns (lat, lon, distance) — use lon (second arg)
    _, lon_now, _ = merc_app.ecliptic_latlon()
    _, lon_next, _ = merc_app_next.ecliptic_latlon()
    # Compute dlon mod 360
    dlon = (lon_next.degrees - lon_now.degrees + 540) % 360 - 180
    merc_retro = (dlon < 0).astype(float)

    return pd.DataFrame({
        'sun_moon_sep_deg': sun_moon_deg,
        'moon_phase_quality': phase_q,
        'mars_saturn_deg': mars_sat_deg,
        'sun_jupiter_deg': sun_jup_deg,
        'mercury_retrograde': merc_retro,
    }, index=ts_index)


# === Component scores (each 0..1) ==========================================

def session_quality(hour_utc: int) -> float:
    """Asia best, NY worst (per loser-profile findings)."""
    # Map 0-23 to quality based on our loser_profile categorical data
    # Top hours by mean R: 1,3,5 -> q=1.0; worst (6,11,15,20) -> q=0.0; rest interp
    table = {
        0: 0.85, 1: 1.00, 2: 0.65, 3: 1.00, 4: 0.75, 5: 1.00,
        6: 0.00, 7: 0.40, 8: 0.80, 9: 0.85, 10: 0.45, 11: 0.30,
        12: 0.80, 13: 0.65, 14: 0.50, 15: 0.00, 16: 0.60, 17: 0.50,
        18: 0.70, 19: 0.55, 20: 0.20, 21: 0.75, 22: 0.55, 23: 1.00,
    }
    return table.get(hour_utc, 0.5)


def vol_spike_quality(atr_z_7d: float) -> float:
    """Vol spike — chento likes some elevated vol (signals capitulation)
    but not extreme vol (chop regime). Sweet spot ~ +0.5 to +1.5 z."""
    if pd.isna(atr_z_7d):
        return 0.5
    if -0.5 <= atr_z_7d <= 1.5:
        return 1.0 - abs(atr_z_7d - 0.5) / 2.0
    if atr_z_7d > 1.5:
        return max(0.0, 1.0 - (atr_z_7d - 1.5) / 1.5)   # 0 above z+3
    return max(0.0, 0.5 + atr_z_7d / 1.0)


def rsi_neutral_quality(rsi_val: float) -> float:
    """RSI Neutral: 1.0 at RSI 50, 0.0 at RSI 30 or 70."""
    if pd.isna(rsi_val):
        return 0.5
    dist = abs(rsi_val - 50)
    return max(0.0, 1.0 - dist / 20.0)


def planetary_quality(row) -> float:
    """High score at full/new moon AND when mercury is direct."""
    # Components
    phase = row['moon_phase_quality']      # 1.0 at full/new
    merc = 1.0 - row['mercury_retrograde'] * 0.5    # penalize retrograde by half
    return float((phase + merc) / 2)


def b7_alignment_quality(b7_z: float) -> float:
    """B7 multi-TF CVD alignment z-score. Already part of the Triple trigger,
    so we use its STRENGTH as quality."""
    if pd.isna(b7_z):
        return 0.5
    return min(1.0, abs(b7_z) / 3.0)


# === Attach features and compute composite =================================

def attach_chento_features(trades: pd.DataFrame, df_b1: pd.DataFrame,
                            planet_df: pd.DataFrame, multitf_df: pd.DataFrame
                            ) -> pd.DataFrame:
    out = trades.sort_values('ts').reset_index(drop=True).copy()
    ts_idx = pd.DatetimeIndex(out['ts'])

    # Hour-of-day session score
    out['hour_utc'] = ts_idx.hour
    out['session_q'] = out['hour_utc'].map(session_quality)

    # ATR z-score 7d
    df_b1 = df_b1.copy()
    if 'atr' not in df_b1.columns:
        df_b1['atr'] = compute_atr(df_b1, period=14)
    df_b1['atr_pct'] = df_b1['atr'] / df_b1['close']
    df_b1['atr_z_7d'] = (df_b1['atr_pct'] -
                          df_b1['atr_pct'].rolling(672, min_periods=200).mean()) / \
                        df_b1['atr_pct'].rolling(672, min_periods=200).std()
    # RSI on 15m close
    df_b1['rsi14'] = rsi(df_b1['close'], 14)

    # Reindex per-trade
    ix = df_b1.index.searchsorted(ts_idx, side='right') - 1
    out['atr_z_7d'] = [float(df_b1['atr_z_7d'].iloc[i]) if 0 <= i < len(df_b1) else np.nan
                       for i in ix]
    out['rsi14'] = [float(df_b1['rsi14'].iloc[i]) if 0 <= i < len(df_b1) else np.nan
                     for i in ix]

    # Multi-TF CVD alignment z (b7_z): use the mean absolute cvd z across
    # the 4 timeframes (1h, 4h, 1d, 3d) — proxy for "strength of alignment".
    if multitf_df is not None and 'cvd_1h_z' in multitf_df.columns:
        ix2 = multitf_df.index.searchsorted(ts_idx, side='right') - 1
        cols = ['cvd_1h_z', 'cvd_4h_z', 'cvd_1d_z', 'cvd_3d_z']
        b7z_vals = []
        for i in ix2:
            if 0 <= i < len(multitf_df):
                vs = [multitf_df[c].iloc[i] for c in cols]
                vs = [abs(v) for v in vs if not pd.isna(v)]
                b7z_vals.append(float(np.mean(vs)) if vs else np.nan)
            else:
                b7z_vals.append(np.nan)
        out['b7_z'] = b7z_vals
    else:
        out['b7_z'] = np.nan

    # Planetary join (snap each trigger to the planet_df ts most recent <= ts)
    # planet_df is computed at trigger timestamps directly, so just merge
    plan_at_trig = planet_df.loc[ts_idx].reset_index(drop=True)
    for col in plan_at_trig.columns:
        out[col] = plan_at_trig[col].values

    # Component quality
    out['vol_spike_q'] = out['atr_z_7d'].apply(vol_spike_quality)
    out['rsi_neutral_q'] = out['rsi14'].apply(rsi_neutral_quality)
    out['b7_q'] = out['b7_z'].apply(b7_alignment_quality)
    out['planet_q'] = out.apply(planetary_quality, axis=1)

    # Composite: weighted mean. Equal weights to start.
    out['composite_q'] = (
        out['session_q'] * 0.20 +
        out['vol_spike_q'] * 0.20 +
        out['rsi_neutral_q'] * 0.20 +
        out['b7_q'] * 0.20 +
        out['planet_q'] * 0.20
    )

    # "70% per-component AND overall" rule
    out['passes_strict'] = (
        (out['session_q'] >= 0.5) &
        (out['vol_spike_q'] >= 0.5) &
        (out['rsi_neutral_q'] >= 0.5) &
        (out['b7_q'] >= 0.5) &
        (out['planet_q'] >= 0.5) &
        (out['composite_q'] >= 0.7)
    )
    return out


def summary(t: pd.DataFrame, label: str) -> dict:
    if t.empty:
        return {'label': label, 'n': 0}
    t = t.sort_values('ts').reset_index(drop=True)
    cum = t['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    is_set = t[t['ts'] <= IS_END]; oos_set = t[t['ts'] > IS_END]
    return {
        'label': label, 'n': int(len(t)),
        'mean_R': round(float(t['r_outcome'].mean()), 3),
        'wr': round(float((t['r_outcome'] > 0).mean()), 3),
        'cum_R': round(float(cum[-1]), 2),
        'max_dd_R': round(float(dd.min()), 2),
        'IS_meanR': round(float(is_set['r_outcome'].mean()) if len(is_set) else 0, 3),
        'IS_n': int(len(is_set)),
        'OOS_meanR': round(float(oos_set['r_outcome'].mean()) if len(oos_set) else 0, 3),
        'OOS_n': int(len(oos_set)),
    }


def show(s):
    print(f'  {s["label"]:<55s} n={s["n"]:>3d}  meanR={s["mean_R"]:+.3f}  '
           f'WR={s["wr"]:.0%}  cumR={s["cum_R"]:>+7.1f}  '
           f'maxDD={s["max_dd_R"]:+5.2f}  IS={s["IS_meanR"]:+.3f}  '
           f'OOS={s["OOS_meanR"]:+.3f}')


def main():
    print('Building Triple composite with optimized config (atr5_t6R + no_tilt + no_resist_OB_2R)...')
    df = load_btc_15m()
    df_p = compute_pivots(df, n=5)
    df_smc = compute_smc_state(df_p, n=5)
    obs = compute_order_blocks(df_smc)
    fvgs = compute_fvgs(df_smc)
    df_b1 = compute_moneyflow_signal(df)
    df_atr = df_smc.copy()
    df_atr['atr'] = compute_atr(df_atr, period=14)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df, compute_lsr_extremes(load_lsr('BTC')))
    multitf = compute_multitf_cvd(df)
    b7 = b7_alignment_triggers(multitf, z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    print(f'  Triple triggers: {len(triple):,}')

    # Replay with optimized config: atr5_t6R
    rows = []
    for _, t in triple.iterrows():
        r = replay_one(t, df_smc, df_atr, fvgs, obs,
                        atr_mult=5.0, target_r=6.0, tp_mode='fixed')
        if r is not None:
            rows.append(r)
    rep = pd.DataFrame(rows)

    # Apply no_tilt
    rep = rep.sort_values('ts').reset_index(drop=True)
    cur = 0; lb = []
    for r in rep['r_outcome'].shift(1).fillna(0):
        if r < 0: cur += 1
        else: cur = 0
        lb.append(cur)
    rep['consec_losses_before'] = lb
    # Apply no_resist_OB_2R
    mask = (rep['consec_losses_before'] == 0) & (
        (rep['dist_resist_OB_R'] > 2.0) | rep['dist_resist_OB_R'].isna())
    optimized = rep[mask].copy()
    print(f'  Optimized config trades: {len(optimized):,}')
    show(summary(optimized, 'optimized (atr5_t6R + no_tilt + no_resist_OB_2R)'))

    print('\nComputing planetary aspects at trigger timestamps...')
    planet_df = compute_planetary(pd.DatetimeIndex(optimized['ts']))
    print(f'  planet feature head:')
    print(planet_df.head(3).to_string())

    # Attach features
    feat = attach_chento_features(optimized, df_b1, planet_df, multitf)

    # === Diagnostic: feature vs outcome correlation ===
    print('\n--- Component diagnostic (mean R conditioned on each component) ---')
    for comp in ('session_q', 'vol_spike_q', 'rsi_neutral_q', 'b7_q', 'planet_q'):
        print(f'  {comp}:')
        for q_threshold in (0.0, 0.3, 0.5, 0.7, 0.85):
            sub = feat[feat[comp] >= q_threshold]
            if len(sub) >= 30:
                print(f'    >= {q_threshold:.2f}:  n={len(sub):>3d}  '
                       f'meanR={sub["r_outcome"].mean():+.3f}  '
                       f'WR={(sub["r_outcome"] > 0).mean():.0%}')

    # === Composite filter tests ===
    print('\n--- Composite filter applied to optimized config ---')
    base = summary(feat, 'optimized (no chento filter)')
    show(base)
    for ct in (0.4, 0.5, 0.6, 0.7, 0.8):
        sub = feat[feat['composite_q'] >= ct]
        show(summary(sub, f'composite_q >= {ct}'))

    print('\n--- Strict rule: all components >=0.5 AND composite >=0.7 ---')
    sub = feat[feat['passes_strict']]
    show(summary(sub, 'strict (each>=0.5 AND comp>=0.7)'))

    # === Planetary-only standalone ===
    print('\n--- Planetary aspects standalone (independent edge check) ---')
    for q in (0.4, 0.5, 0.6, 0.7, 0.85):
        sub = feat[feat['planet_q'] >= q]
        if len(sub) >= 20:
            show(summary(sub, f'planet_q >= {q}'))

    # === Moon-phase narrow tests ===
    print('\n--- Specific moon-phase windows ---')
    near_full = feat[(feat['sun_moon_sep_deg'] > 165) & (feat['sun_moon_sep_deg'] < 195)]
    near_new = feat[(feat['sun_moon_sep_deg'] < 15) | (feat['sun_moon_sep_deg'] > 345)]
    away = feat[(feat['sun_moon_sep_deg'] > 75) & (feat['sun_moon_sep_deg'] < 105) |
                  ((feat['sun_moon_sep_deg'] > 255) & (feat['sun_moon_sep_deg'] < 285))]
    show(summary(near_full, 'near full moon (sep 165-195 deg)'))
    show(summary(near_new, 'near new moon (sep <15 or >345 deg)'))
    show(summary(away, 'near quarter (sep 75-105 or 255-285)'))

    # === Mercury retrograde ===
    print('\n--- Mercury retrograde ---')
    show(summary(feat[feat['mercury_retrograde'] == 1], 'mercury retrograde'))
    show(summary(feat[feat['mercury_retrograde'] == 0], 'mercury direct'))

    # === Sun-Jupiter conjunction (close angle) ===
    print('\n--- Sun-Jupiter conjunction proximity ---')
    show(summary(feat[feat['sun_jupiter_deg'] < 30], 'Sun-Jupiter < 30 deg'))
    show(summary(feat[feat['sun_jupiter_deg'] > 150], 'Sun-Jupiter > 150 deg (opp)'))

    # Save
    all_results = {
        'optimized_baseline': base,
        'strict_filter': summary(feat[feat['passes_strict']], 'strict'),
    }
    out_path = OUT_DIR / 'C3_chento_on_top_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('CHENTO ON TOP composite indicator on optimized Triple+atr5_t6R '
                      '+no_tilt+no_resist_OB_2R. Tests each component + the composite + '
                      'standalone planetary aspects.'),
            'variants': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

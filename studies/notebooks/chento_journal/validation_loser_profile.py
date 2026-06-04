"""validation_loser_profile: characterize losing trades on the Triple +
atr4_t3R baseline and test whether a single feature (or small combination)
can carve them off without sacrificing too much expectancy.

Approach:
  1. Rebuild Triple composite + atr4_t3R trades (n=681).
  2. For every trade attach context features computed AT the entry bar
     (no look-ahead): time-of-day, weekday, direction, vol regime,
     range position, signal strength, signal clustering, prior-loss
     pressure, funding sign.
  3. For each feature: median/quartile split of losers vs winners
     + Mann-Whitney U-test, then a candidate cutoff.
  4. Stack-filter sweep: apply the most-promising single filter, then
     2-feature combinations. Report n, mean R, max-DD, profit kept.

Outcome to look for:
  - features where losers concentrate sharply in one tail
  - filter that cuts >=50% of losers while keeping >=70% of winners
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sps

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
from studies.notebooks.chento_journal.validation_structural_stops import (
    replay_all, stop_atr,
)


# === Feature attachment =====================================================

def attach_features(trades: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-trade context features at the entry bar."""
    out = trades.sort_values('ts').reset_index(drop=True).copy()

    # Time features
    ts = pd.DatetimeIndex(out['ts'])
    out['hour_utc'] = ts.hour
    out['weekday'] = ts.weekday          # 0=Mon
    out['session'] = pd.cut(
        ts.hour, bins=[-1, 7, 11, 16, 22, 24],
        labels=['asia', 'asia_london', 'london', 'london_ny', 'asia2'],
    ).astype(str)

    # Pre-compute helper columns on df (15m frame)
    df = df.copy()
    if 'atr' not in df.columns:
        df['atr'] = compute_atr(df, period=14)
    # ATR % of price
    df['atr_pct'] = df['atr'] / df['close']
    # ATR z-score over trailing 7d (= 4*24*7 = 672 bars)
    df['atr_z_7d'] = (df['atr_pct'] - df['atr_pct'].rolling(672, min_periods=200).mean()) / \
                     df['atr_pct'].rolling(672, min_periods=200).std()
    # Volume z over trailing 7d (15m bar volume)
    df['vol_z_7d'] = (df['quote_volume'] - df['quote_volume'].rolling(672, min_periods=200).mean()) / \
                      df['quote_volume'].rolling(672, min_periods=200).std()
    # 24h price-range position (where in the rolling 24h range)
    win_24h = 4 * 24
    h24 = df['high'].rolling(win_24h, min_periods=win_24h // 4).max()
    l24 = df['low'].rolling(win_24h, min_periods=win_24h // 4).min()
    df['pctile_24h'] = (df['close'] - l24) / (h24 - l24).replace(0, np.nan)
    # 7d price-range position
    win_7d = 4 * 24 * 7
    h7 = df['high'].rolling(win_7d, min_periods=win_7d // 4).max()
    l7 = df['low'].rolling(win_7d, min_periods=win_7d // 4).min()
    df['pctile_7d'] = (df['close'] - l7) / (h7 - l7).replace(0, np.nan)

    # Reindex per-trade lookups
    idx_lookup = df.index.searchsorted(ts, side='right') - 1
    for col in ['atr_pct', 'atr_z_7d', 'vol_z_7d', 'pctile_24h', 'pctile_7d',
                'cvd_z', 'vel_z']:
        if col in df.columns:
            out[col] = [float(df[col].iloc[i]) if 0 <= i < len(df) else np.nan
                        for i in idx_lookup]

    # LSR z if available (B5 computed earlier)
    # We don't have df['lsr_z'] here — skip; can be added later

    # Signal-density: hours since prior trigger, signals in last 24h
    out['hours_since_prior'] = (
        pd.to_datetime(out['ts']).diff().dt.total_seconds() / 3600.0
    )
    # Signals in last 24h (count of trades in trailing 24h window)
    last24 = []
    for i, t in enumerate(ts):
        cutoff = t - pd.Timedelta(hours=24)
        last24.append(int(((ts >= cutoff) & (ts < t)).sum()))
    out['n_signals_24h'] = last24

    # Prior-loss streak: how many consecutive losing trades immediately before
    losses_before = []
    cur = 0
    for r in out['r_outcome'].shift(1).fillna(0):
        if r < 0:
            cur += 1
        else:
            cur = 0
        losses_before.append(cur)
    out['consec_losses_before'] = losses_before

    # Funding sign from cd_funding_rate
    try:
        con = sqlite3.connect(str(DB))
        fd = pd.read_sql(
            "SELECT timestamp, funding_rate FROM cd_funding_rate WHERE asset='BTC' "
            "ORDER BY timestamp", con)
        con.close()
        if not fd.empty:
            fd['ts'] = pd.to_datetime(fd['timestamp'], unit='s', utc=True)
            fd = fd.set_index('ts').drop(columns='timestamp')
            ix = fd.index.searchsorted(ts, side='right') - 1
            out['funding_rate'] = [float(fd['funding_rate'].iloc[i])
                                     if 0 <= i < len(fd) else np.nan for i in ix]
    except Exception as e:
        print(f'  (funding skipped: {e})')

    return out


# === Loser-vs-winner analysis ==============================================

def loser_vs_winner_table(feat: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    """For each feature: median/IQR/n in losers vs winners + MW-U p-value."""
    losers = feat[feat['r_outcome'] < 0]
    winners = feat[feat['r_outcome'] > 0]
    rows = []
    for col in features:
        L = losers[col].dropna()
        W = winners[col].dropna()
        if len(L) < 20 or len(W) < 20:
            continue
        # Mann-Whitney U two-sided
        try:
            stat, p = sps.mannwhitneyu(L, W, alternative='two-sided')
        except Exception:
            stat, p = (np.nan, np.nan)
        rows.append({
            'feature': col,
            'n_losers': int(len(L)),
            'n_winners': int(len(W)),
            'loser_median': round(float(L.median()), 4),
            'winner_median': round(float(W.median()), 4),
            'loser_q1': round(float(L.quantile(.25)), 4),
            'loser_q3': round(float(L.quantile(.75)), 4),
            'winner_q1': round(float(W.quantile(.25)), 4),
            'winner_q3': round(float(W.quantile(.75)), 4),
            'mw_p': round(float(p), 5) if not np.isnan(p) else None,
        })
    return pd.DataFrame(rows)


def categorical_breakdown(feat: pd.DataFrame, col: str) -> pd.DataFrame:
    """R per category for a discrete column (e.g. hour_utc)."""
    g = feat.groupby(col)['r_outcome'].agg(
        n='count', mean_R='mean', median_R='median',
        wr=lambda x: (x > 0).mean(),
    ).reset_index()
    g['mean_R'] = g['mean_R'].round(3)
    g['median_R'] = g['median_R'].round(3)
    g['wr'] = g['wr'].round(2)
    return g.sort_values('mean_R')


# === Filter test ============================================================

def equity_metrics(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {'n': 0}
    df = trades.sort_values('ts').reset_index(drop=True)
    cum = df['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    return {
        'n': int(len(df)),
        'mean_R': round(float(df['r_outcome'].mean()), 3),
        'wr': round(float((df['r_outcome'] > 0).mean()), 3),
        'cum_R': round(float(cum[-1]), 2),
        'max_dd_R': round(float(dd.min()), 2),
    }


def apply_filter(trades: pd.DataFrame, mask: pd.Series, label: str) -> dict:
    kept = trades[mask].copy()
    return {**equity_metrics(kept), 'label': label,
            'cut_count': int((~mask).sum())}


def main():
    print('Building Triple composite + atr4_t3R trades...')
    df = load_btc_15m()
    df_b1 = compute_moneyflow_signal(df)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df, compute_lsr_extremes(load_lsr('BTC')))
    b7 = b7_alignment_triggers(compute_multitf_cvd(df), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    df_with_sig = df_b1   # so we have cvd_z / vel_z in df_with_sig
    df_with_sig['atr'] = compute_atr(df_with_sig, period=14)
    trades = replay_all(triple, df_with_sig, stop_fn=stop_atr,
                         stop_kwargs={'atr_mult': 4.0}, target_r=3.0)
    base = equity_metrics(trades)
    print(f'  Baseline: n={base["n"]} meanR={base["mean_R"]} '
           f'WR={base["wr"]} cumR={base["cum_R"]} maxDD={base["max_dd_R"]}')

    print('\nAttaching per-trade features...')
    feat = attach_features(trades, df_with_sig)

    # === Loser vs winner separability ===
    numeric_features = [
        'atr_pct', 'atr_z_7d', 'vol_z_7d', 'pctile_24h', 'pctile_7d',
        'cvd_z', 'vel_z', 'hours_since_prior', 'n_signals_24h',
        'consec_losses_before', 'funding_rate',
    ]
    numeric_features = [c for c in numeric_features if c in feat.columns]

    print('\n=== Loser vs Winner: feature distribution + MW p-value ===')
    tab = loser_vs_winner_table(feat, numeric_features)
    tab = tab.sort_values('mw_p')
    print(tab.to_string(index=False))

    # === Categorical breakdowns ===
    print('\n=== R by hour-of-day (UTC) ===')
    print(categorical_breakdown(feat, 'hour_utc').to_string(index=False))
    print('\n=== R by weekday (0=Mon) ===')
    print(categorical_breakdown(feat, 'weekday').to_string(index=False))
    print('\n=== R by direction ===')
    print(categorical_breakdown(feat, 'direction').to_string(index=False))
    print('\n=== R by session ===')
    print(categorical_breakdown(feat, 'session').to_string(index=False))

    # === Threshold-based filters: test the most promising features ===
    print('\n=== Single-feature filter experiments ===')
    print(f'{"label":<40s}  {"n":>4s} {"meanR":>7s} {"WR":>5s} {"cumR":>7s} {"maxDD":>7s} {"cut":>4s}')
    print(f'{"BASELINE":<40s}  {base["n"]:>4d} {base["mean_R"]:>+7.3f} '
           f'{base["wr"]:>5.2%} {base["cum_R"]:>+7.1f} {base["max_dd_R"]:>+7.1f}    -')
    filter_results = {'baseline': base}

    def report(label, mask):
        r = apply_filter(feat, mask, label)
        filter_results[label] = r
        if r['n'] == 0:
            print(f'{label:<40s}  {0:>4d}')
            return
        print(f'{label:<40s}  {r["n"]:>4d} {r["mean_R"]:>+7.3f} '
               f'{r["wr"]:>5.2%} {r["cum_R"]:>+7.1f} {r["max_dd_R"]:>+7.1f} {r["cut_count"]:>4d}')

    # Vol-regime gates (atr_pct / atr_z_7d)
    if 'atr_z_7d' in feat.columns:
        for q in (-1.0, -0.5, 0.0, 0.5, 1.0, 1.5):
            report(f'atr_z_7d <= {q:+.1f} (calm)', feat['atr_z_7d'] <= q)
            report(f'atr_z_7d >  {q:+.1f} (vol)', feat['atr_z_7d'] > q)

    # 24h range position (B3 — midrange-avoidance hypothesis revisited)
    if 'pctile_24h' in feat.columns:
        report('pctile_24h outer 25% (B3-like)',
                (feat['pctile_24h'] <= 0.25) | (feat['pctile_24h'] >= 0.75))
        report('pctile_24h outer 30%',
                (feat['pctile_24h'] <= 0.30) | (feat['pctile_24h'] >= 0.70))
        # long-only-in-lower-quartile / short-only-in-upper
        long_in_low = (feat['direction'] == 'long') & (feat['pctile_24h'] <= 0.30)
        short_in_high = (feat['direction'] == 'short') & (feat['pctile_24h'] >= 0.70)
        report('long@bottom25 + short@top25', long_in_low | short_in_high)

    # 7d range position
    if 'pctile_7d' in feat.columns:
        report('pctile_7d outer 25%',
                (feat['pctile_7d'] <= 0.25) | (feat['pctile_7d'] >= 0.75))

    # Signal density
    report('n_signals_24h == 0 (clean)', feat['n_signals_24h'] == 0)
    report('n_signals_24h <= 1', feat['n_signals_24h'] <= 1)
    report('n_signals_24h <= 2', feat['n_signals_24h'] <= 2)

    # Consec loss filter
    report('consec_losses_before <= 2', feat['consec_losses_before'] <= 2)
    report('consec_losses_before <= 1', feat['consec_losses_before'] <= 1)
    report('consec_losses_before == 0', feat['consec_losses_before'] == 0)

    # Funding gate (longs only when funding negative, shorts only when positive)
    if 'funding_rate' in feat.columns:
        align = (((feat['direction'] == 'long') & (feat['funding_rate'] <= 0)) |
                 ((feat['direction'] == 'short') & (feat['funding_rate'] >= 0)))
        report('funding-aligned (long@neg, short@pos)', align)

    # Hour-of-day worst-cell removal
    hr_means = feat.groupby('hour_utc')['r_outcome'].mean()
    worst3_hours = hr_means.sort_values().head(3).index.tolist()
    best3_hours = hr_means.sort_values().tail(3).index.tolist()
    report(f'skip worst-3 hours {sorted(worst3_hours)}',
            ~feat['hour_utc'].isin(worst3_hours))
    report(f'only best-3 hours {sorted(best3_hours)}',
            feat['hour_utc'].isin(best3_hours))

    # Weekday worst removal
    wd_means = feat.groupby('weekday')['r_outcome'].mean()
    worst_wd = wd_means.sort_values().head(2).index.tolist()
    report(f'skip worst-2 weekdays {sorted(worst_wd)}',
            ~feat['weekday'].isin(worst_wd))

    # CVD signal strength: stronger divergence -> higher conviction?
    if 'cvd_z' in feat.columns:
        # Use absolute cvd_z (we know direction is consistent with sign)
        feat_abs_cvd = feat['cvd_z'].abs()
        for q in (0.5, 1.0, 1.5, 2.0):
            report(f'|cvd_z| >= {q:.1f} (high conviction)', feat_abs_cvd >= q)

    # Vol_z (entry volume): spike vs calm
    if 'vol_z_7d' in feat.columns:
        for q in (0.0, 0.5, 1.0, 1.5):
            report(f'vol_z_7d >= {q:+.1f} (spike)', feat['vol_z_7d'] >= q)
            report(f'vol_z_7d <  {q:+.1f} (calm)', feat['vol_z_7d'] < q)

    # === Best stacked combinations (top single filters ANDed) ===
    print('\n=== Stacking the best single filters ===')
    # Pick top single filters by max_dd improvement subject to cumR >= 80% base
    single_keys = [k for k, v in filter_results.items()
                    if k != 'baseline' and v.get('n', 0) >= 100
                    and v.get('cum_R', 0) >= 0.50 * base['cum_R']]
    # Score: weight 0.7*max_dd_improvement + 0.3*meanR_improvement
    scored = []
    for k in single_keys:
        v = filter_results[k]
        dd_gain = base['max_dd_R'] - v['max_dd_R']  # less negative is better
        r_gain = v['mean_R'] - base['mean_R']
        scored.append((k, dd_gain, r_gain, v['cum_R']))
    scored.sort(key=lambda x: (x[1] * 0.7 + x[2] * 0.3), reverse=True)
    print('\nTop single filters (by DD gain + meanR gain):')
    for k, dd, r, c in scored[:8]:
        print(f'  {k:<40s} dd_gain={dd:+.2f}  meanR_gain={r:+.3f}  cumR={c:+.1f}')

    out_path = OUT_DIR / 'loser_profile_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'baseline': base,
            'feature_separability': tab.to_dict(orient='records'),
            'filters': filter_results,
            'top_stack_candidates': [
                {'label': k, 'dd_gain': dd, 'meanR_gain': r, 'cum_R': c}
                for k, dd, r, c in scored[:10]
            ],
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

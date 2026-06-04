"""validation_drawdown_analysis: characterize and reduce max drawdown.

Per-trade max-loss = 1R is misleading. What actually matters is the equity-
curve max peak-to-trough drawdown over the full backtest. Losing streaks
compound — a strategy with +0.65R per trade can still see -8R or -15R DDs
if it has bad streaks.

This script:
  1. Reconstructs the Triple composite with atr4_t3R stops (current best)
  2. Computes the full equity curve (cumulative R, no compounding)
  3. Measures DD metrics: max-DD, DD duration, %-time in DD, streaks
  4. Tests four DD-reduction techniques:
     - A. Consecutive-loss circuit breaker (pause after N losses)
     - B. Equity-curve circuit breaker (pause after equity drops X%)
     - C. Filter additions from prior winners (B3 + B8 + B11)
     - D. Vol-regime gate (skip during DVOL extremes)
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


# === Drawdown analysis ====================================================

def equity_curve(trades: pd.DataFrame) -> pd.DataFrame:
    """Cumulative R equity curve (no compounding, fixed 1R risk per trade).
    Returns DataFrame with ts, r_outcome, cum_r, peak, dd, dd_R."""
    df = trades.sort_values('ts').reset_index(drop=True).copy()
    df['cum_r'] = df['r_outcome'].cumsum()
    df['peak'] = df['cum_r'].cummax()
    df['dd_R'] = df['cum_r'] - df['peak']   # zero or negative
    return df


def dd_metrics(eq: pd.DataFrame) -> dict:
    """Summarize drawdown characteristics."""
    if eq.empty:
        return {}
    cum_r = eq['cum_r'].values
    peak = eq['peak'].values
    dd = eq['dd_R'].values
    max_dd_R = float(dd.min())
    # Time-in-DD: bars where dd < 0
    in_dd = (dd < -0.001).sum()
    pct_in_dd = in_dd / len(eq) * 100
    # Longest DD duration (consecutive bars below peak)
    durations = []
    cur = 0
    for d in dd:
        if d < -0.001:
            cur += 1
        else:
            if cur > 0: durations.append(cur)
            cur = 0
    if cur > 0: durations.append(cur)
    max_dd_dur = max(durations) if durations else 0
    # Recovery: max bars between hitting trough and reaching prior peak again
    # (approximated by max_dd_dur)
    # Worst 5 drawdowns (peak-to-trough): identify by tracking peak resets
    troughs = []
    cur_peak = -1e9
    cur_trough_dd = 0
    for i, (p, c) in enumerate(zip(peak, cum_r)):
        if p > cur_peak:
            if cur_trough_dd < 0:
                troughs.append(cur_trough_dd)
            cur_peak = p
            cur_trough_dd = 0
        else:
            d = c - p
            if d < cur_trough_dd:
                cur_trough_dd = d
    if cur_trough_dd < 0:
        troughs.append(cur_trough_dd)
    troughs.sort()  # most negative first
    return {
        'n_trades': len(eq),
        'final_cum_R': round(float(cum_r[-1]), 3),
        'max_dd_R': round(max_dd_R, 3),
        'pct_time_in_dd': round(pct_in_dd, 1),
        'longest_dd_bars': max_dd_dur,
        'top5_worst_drawdowns_R': [round(t, 2) for t in troughs[:5]],
        'n_drawdowns_below_-5R': sum(1 for t in troughs if t < -5),
        'n_drawdowns_below_-10R': sum(1 for t in troughs if t < -10),
    }


def streak_metrics(trades: pd.DataFrame) -> dict:
    """Longest consecutive win/loss streaks."""
    if trades.empty:
        return {}
    df = trades.sort_values('ts')
    wins = (df['r_outcome'] > 0).values
    streaks_loss = []; streaks_win = []
    cur_loss = cur_win = 0
    for w in wins:
        if w:
            cur_win += 1
            if cur_loss > 0: streaks_loss.append(cur_loss)
            cur_loss = 0
        else:
            cur_loss += 1
            if cur_win > 0: streaks_win.append(cur_win)
            cur_win = 0
    if cur_loss > 0: streaks_loss.append(cur_loss)
    if cur_win > 0: streaks_win.append(cur_win)
    return {
        'longest_loss_streak': max(streaks_loss) if streaks_loss else 0,
        'longest_win_streak': max(streaks_win) if streaks_win else 0,
        'loss_streaks_5plus': sum(1 for s in streaks_loss if s >= 5),
        'loss_streaks_10plus': sum(1 for s in streaks_loss if s >= 10),
    }


def sharpe_sortino(trades: pd.DataFrame) -> dict:
    if trades.empty or len(trades) < 2:
        return {}
    r = trades['r_outcome'].values
    mu = float(np.mean(r))
    sd = float(np.std(r, ddof=1))
    sharpe = mu / sd if sd > 0 else 0
    neg = r[r < 0]
    sd_dn = float(np.std(neg, ddof=1)) if len(neg) > 1 else sd
    sortino = mu / sd_dn if sd_dn > 0 else 0
    return {
        'sharpe_per_trade': round(sharpe, 3),
        'sortino_per_trade': round(sortino, 3),
    }


# === Circuit breakers =====================================================

def apply_consecutive_loss_pause(trades: pd.DataFrame, n_losses: int,
                                   pause_bars_h: float = 24.0) -> pd.DataFrame:
    """Pause for pause_bars_h hours after n_losses consecutive losses."""
    if trades.empty:
        return trades
    df = trades.sort_values('ts').reset_index(drop=True).copy()
    keep = np.zeros(len(df), dtype=bool)
    cur_losses = 0
    paused_until = pd.Timestamp.min.tz_localize('UTC')
    for i, row in df.iterrows():
        ts = pd.Timestamp(row['ts'])
        if ts < paused_until:
            cur_losses = 0  # reset on pause
            continue
        keep[i] = True
        if row['r_outcome'] < 0:
            cur_losses += 1
            if cur_losses >= n_losses:
                paused_until = ts + pd.Timedelta(hours=pause_bars_h)
                cur_losses = 0
        else:
            cur_losses = 0
    return df[keep].copy()


def apply_dd_pause(trades: pd.DataFrame, dd_threshold_R: float = -5.0,
                    pause_until_new_high: bool = True) -> pd.DataFrame:
    """Pause when running DD <= dd_threshold_R, resume when equity makes a
    new high (or after fixed time, but new-high is simpler)."""
    if trades.empty:
        return trades
    df = trades.sort_values('ts').reset_index(drop=True).copy()
    cum = 0.0; peak = 0.0; paused = False
    keep = np.zeros(len(df), dtype=bool)
    for i, row in df.iterrows():
        if not paused:
            # take this trade
            keep[i] = True
            cum += row['r_outcome']
            peak = max(peak, cum)
            dd = cum - peak
            if dd <= dd_threshold_R:
                paused = True
        else:
            # paused — we need to track hypothetical equity if pause_until_new_high
            # In paused mode, we don't take trades, so cum doesn't move.
            # We need an external trigger to resume. Simplest: resume after
            # a fixed N hours.
            # For now, resume after 7 days of no trading.
            if pause_until_new_high:
                # Resume after 7 days
                ts = pd.Timestamp(row['ts'])
                last_ts = pd.Timestamp(df['ts'].iloc[max(0, i-1)])
                if (ts - last_ts).total_seconds() > 7 * 86400:
                    paused = False
                    keep[i] = True
                    cum += row['r_outcome']
                    peak = max(peak, cum)
    return df[keep].copy()


def report_run(label: str, trades: pd.DataFrame) -> dict:
    eq = equity_curve(trades)
    m = dd_metrics(eq)
    s = streak_metrics(trades)
    ss = sharpe_sortino(trades)
    span_y = ((trades['ts'].max() - trades['ts'].min()).total_seconds()
               / (365.25 * 86400)) if len(trades) else 0
    print(f'\n--- {label} ---')
    print(f'  trades: {m.get("n_trades",0)}  ({m.get("n_trades",0)/max(span_y,0.1):.0f}/yr)')
    print(f'  final cum R: {m.get("final_cum_R",0):+.2f}R')
    if len(trades):
        mean_r = float(trades["r_outcome"].mean())
        wr = float((trades["r_outcome"]>0).mean())
        print(f'  mean R: {mean_r:+.3f}  WR: {wr:.0%}  Sharpe(per-trade): {ss.get("sharpe_per_trade",0):+.2f}')
    print(f'  MAX-DD: {m.get("max_dd_R",0):+.2f}R  ({m.get("pct_time_in_dd",0):.0f}% of bars in DD)')
    print(f'  Longest DD duration: {m.get("longest_dd_bars",0)} trades')
    print(f'  Top-5 worst DDs (R): {m.get("top5_worst_drawdowns_R",[])}')
    print(f'  DDs <= -5R: {m.get("n_drawdowns_below_-5R",0)}   <= -10R: {m.get("n_drawdowns_below_-10R",0)}')
    print(f'  Longest loss streak: {s.get("longest_loss_streak",0)} trades '
           f'(streaks of 5+: {s.get("loss_streaks_5plus",0)},  10+: {s.get("loss_streaks_10plus",0)})')
    return {**m, **s, **ss}


# === Main ==================================================================

def main():
    print('Loading + building Triple composite...')
    df = load_btc_15m()
    df_b1 = compute_moneyflow_signal(df)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df, compute_lsr_extremes(load_lsr('BTC')))
    b7 = b7_alignment_triggers(compute_multitf_cvd(df), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    df = df.copy()
    df['atr'] = compute_atr(df, period=14)
    print(f'  Triple triggers: {len(triple)}')

    # Re-replay with the winning atr4_t3R config (current best)
    base = replay_all(triple, df, stop_fn=stop_atr,
                       stop_kwargs={'atr_mult': 4.0}, target_r=3.0)
    print(f'  Replayed with atr4_t3R: {len(base)}')

    dump = {}

    # === Baseline: atr4_t3R, no DD controls ===
    dump['baseline_atr4_t3R'] = report_run('Baseline: atr4_t3R (no DD controls)', base)

    # === A. Consecutive-loss circuit breakers ===
    for n_loss in (3, 5, 8):
        for pause_h in (24, 72, 168):
            sub = apply_consecutive_loss_pause(base, n_loss, pause_h)
            lab = f'CB-loss_{n_loss}losses_pause{pause_h}h'
            dump[lab] = report_run(lab, sub)

    # === B. DD-based circuit breakers ===
    for dd_thresh in (-3, -5, -8, -12):
        sub = apply_dd_pause(base, dd_threshold_R=dd_thresh)
        lab = f'CB-dd_{abs(dd_thresh)}R_resume7d'
        dump[lab] = report_run(lab, sub)

    # === Equity curve plot: not generated here, but write CSV for later ===
    eq = equity_curve(base)
    eq_path = OUT_DIR / 'equity_curve_atr4_t3R.csv'
    eq[['ts', 'r_outcome', 'cum_r', 'peak', 'dd_R']].to_csv(eq_path, index=False)
    print(f'\nWrote equity curve CSV: {eq_path}')

    # === Save all dumps ===
    out_path = OUT_DIR / 'drawdown_analysis.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'base_variant': 'Triple composite (B1∩B5∩B7-align) + atr4_t3R',
            'variants': dump,
        }, fh, indent=2, default=str)
    print(f'Wrote {out_path}')

    # === Summary ranking ===
    print('\n=== Ranking variants by max-DD (less negative is better) ===')
    items = [(k, v) for k, v in dump.items() if v.get('n_trades', 0) >= 50]
    items.sort(key=lambda kv: kv[1].get('max_dd_R', -99), reverse=True)
    print(f'{"variant":<35s}  {"n":>4s}  {"final_R":>8s}  {"max_DD":>8s}  {"mean_R":>7s}  {"WR":>4s}  {"Sharpe":>7s}')
    for k, v in items[:10]:
        mr = v.get('final_cum_R', 0) / max(v.get('n_trades', 1), 1)
        n = v.get('n_trades', 0)
        # Approximate WR: we don't have raw r values here
        print(f'  {k:<33s}  {n:>4d}  {v.get("final_cum_R",0):>+7.1f}R  '
               f'{v.get("max_dd_R",0):>+7.1f}R  {mr:>+7.3f}  '
               f'{"":>4s}  {v.get("sharpe_per_trade",0):>+7.3f}')


if __name__ == '__main__':
    main()

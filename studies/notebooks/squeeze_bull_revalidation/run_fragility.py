#!/usr/bin/env python3
"""Step 5 — fragility of the BUILD verdict (audit of the audit).

The pre-registered rule (README.md) returns BUILD on numbers that clear both
thresholds by a hair: n = 10 against a floor of 10, mean R +0.2021 against
+0.10, combined MAR 1.60 against 1.50.  Nothing here changes the verdict —
the decision rule was frozen before the run and stays frozen.  This script
only measures HOW THIN the margin is, so findings.md can say so with numbers:

  F1  leave-k-out on the OOS bull-gated set (drop the best fire, the best two,
      full jackknife) and re-apply the frozen decision rule to each subset;
  F2  look-ahead audit of the regime gate — for every OOS fire, which daily
      close the June `ret_30d` ffill actually used, how many hours of it lie
      at or after the fire, and whether the classification survives the
      backward-only construction;
  F3  the OI source seam (CoinDesk -> native Binance, 2026-06-10 08:00/09:00):
      bull-gated fires each side, and the verdict on the post-seam subset;
  F4  bootstrap CI on the OOS mean R and the deflated Sharpe at N_TRIALS = 1
      (studies.lib.validation), plus the clustering of the fires in time;
  F5  what the funding+CVD leg contributes to clause (b), and clause (b)
      measured without it and without the OOS window.

Read-only: reads results/*.csv + results/*.json written by the three run
scripts and prod.db in mode=ro (for the daily closes in F2).  Writes
results/fragility.json (+ results/fragility_regime_audit.csv).
"""
from __future__ import annotations

import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from squeeze_bull_lib import (  # noqa: E402
    ROOT, RESULTS, OOS_START, FULL_SAMPLE_START, BULL_THRESHOLD,
    read_ledger, load_json, dump_json, basic, window_metrics, load_px_hourly,
)

sys.path.insert(0, str(ROOT))
from studies.lib.validation import bootstrap as vboot  # noqa: E402
from studies.lib.validation import dsr_pbo as vdsr  # noqa: E402

BUILD_MIN_N = 10
BUILD_MIN_MEAN_R = 0.10
BUILD_MIN_MAR = 1.5
OI_SEAM_LAST_COINDESK = pd.Timestamp('2026-06-10 08:00:00', tz='UTC')
SEED = 20260908


def verdict_of(n: int, mean_r: float | None, mar: float, parity_pass: bool = True) -> tuple[str, str]:
    """The pre-registered decision rule, applied verbatim to any (n, meanR, MAR)."""
    if not parity_pass:
        return 'INCONCLUSIVE', 'parity failed'
    if n < BUILD_MIN_N:
        return 'INCONCLUSIVE', f'n = {n} < {BUILD_MIN_N}'
    if mean_r is None:
        return 'INCONCLUSIVE', 'mean R undefined'
    if mean_r <= 0:
        return 'KILL', f'mean R {mean_r:+.4f} <= 0 at n = {n}'
    if mean_r >= BUILD_MIN_MEAN_R and mar >= BUILD_MIN_MAR:
        return 'BUILD', f'n = {n}, mean R {mean_r:+.4f}, MAR {mar:.2f}'
    if mean_r >= BUILD_MIN_MEAN_R:
        return 'INCONCLUSIVE', f'(a) met, (b) failed: MAR {mar:.2f} < {BUILD_MIN_MAR}'
    return 'INCONCLUSIVE', f'n = {n} >= {BUILD_MIN_N} but 0 < mean R {mean_r:+.4f} < {BUILD_MIN_MEAN_R:+.2f}'


def subset_stats(r: np.ndarray) -> dict:
    if len(r) == 0:
        return {'n': 0, 'mean_R': None, 'sum_R': 0.0}
    return {'n': int(len(r)), 'mean_R': round(float(r.mean()), 4),
            'sum_R': round(float(r.sum()), 3),
            'WR': round(float((r > 0).mean()), 3)}


# ── F1 leave-k-out ──────────────────────────────────────────────────────────

def leave_k_out(led: pd.DataFrame, mar: float) -> dict:
    r = led['r_outcome'].values.astype(float)
    ts = led['ts'].astype(str).tolist()
    order = np.argsort(-r)                      # best first
    out: dict = {'fires_sorted_desc': [{'ts': ts[i], 'R': round(float(r[i]), 4)} for i in order]}

    for k in (1, 2):
        keep = np.ones(len(r), dtype=bool)
        keep[order[:k]] = False
        sub = r[keep]
        s = subset_stats(sub)
        v, why = verdict_of(s['n'], s['mean_R'], mar)
        # second framing: what does the MEAN clause alone say if the n floor is
        # ignored (i.e. is the +0.10 threshold cleared by the remaining fires)?
        out[f'drop_best_{k}'] = {
            'dropped': [{'ts': ts[i], 'R': round(float(r[i]), 4)} for i in order[:k]],
            **s, 'verdict': v, 'why': why,
            'mean_clause_a_met_ignoring_n_floor': bool(s['mean_R'] is not None
                                                       and s['mean_R'] >= BUILD_MIN_MEAN_R),
        }

    # full jackknife (leave-one-out over every fire), n held at 9
    jk = np.array([np.delete(r, i).mean() for i in range(len(r))])
    out['jackknife_leave_one_out'] = {
        'n_each': int(len(r) - 1),
        'min_mean_R': round(float(jk.min()), 4), 'max_mean_R': round(float(jk.max()), 4),
        'median_mean_R': round(float(np.median(jk)), 4),
        'n_subsets_mean_ge_0.10': int((jk >= BUILD_MIN_MEAN_R).sum()),
        'n_subsets': int(len(jk)),
    }
    # leave-two-out over ALL pairs (not just the best two)
    pairs = np.array([np.delete(r, list(c)).mean() for c in combinations(range(len(r)), 2)])
    out['leave_two_out_all_pairs'] = {
        'n_each': int(len(r) - 2), 'n_pairs': int(len(pairs)),
        'min_mean_R': round(float(pairs.min()), 4), 'max_mean_R': round(float(pairs.max()), 4),
        'median_mean_R': round(float(np.median(pairs)), 4),
        'share_pairs_mean_ge_0.10': round(float((pairs >= BUILD_MIN_MEAN_R).mean()), 3),
        'share_pairs_mean_le_0': round(float((pairs <= 0).mean()), 3),
    }
    # how much of sum R comes from the target exits
    tgt = led[led['exit_kind'] == 'target']['r_outcome'].sum()
    out['concentration'] = {
        'sum_R': round(float(r.sum()), 3),
        'sum_R_from_target_exits': round(float(tgt), 3),
        'share_of_sum_R_from_targets': round(float(tgt / r.sum()), 3) if r.sum() else None,
        'n_targets': int((led['exit_kind'] == 'target').sum()),
        'note': 'every target exit pays exactly +1.410 R (3 % move / 2 % stop, minus 18 bp)',
    }
    return out


# ── F2 regime-gate look-ahead audit ─────────────────────────────────────────

def regime_audit(led_oos: pd.DataFrame, px: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """For each OOS fire: which daily bar the June ffill picked, at what wall
    clock that daily bar's last hourly close occurred, and how many hours of
    that lie at or after the fire (= look-ahead)."""
    close = px['close']
    daily_last_ts = close.groupby(close.index.floor('D')).apply(lambda s: s.index.max())
    rows = []
    for _, f in led_oos.iterrows():
        t = f['ts']
        day = t.floor('D')
        src_ts = daily_last_ts.get(day, pd.NaT)          # the bar whose close feeds ret_30d[day]
        src_ts_bo = daily_last_ts.get(day - pd.Timedelta(days=1), pd.NaT)  # backward-only source
        la_h = (src_ts - t).total_seconds() / 3600.0 if pd.notna(src_ts) else np.nan
        la_h_bo = (src_ts_bo - t).total_seconds() / 3600.0 if pd.notna(src_ts_bo) else np.nan
        rows.append({
            'ts': t, 'regime_june': f['regime'], 'ret_30d_june': f['ret_30d'],
            'regime_backonly': f['regime_backonly'], 'ret_30d_backonly': f['ret_30d_backonly'],
            'r_outcome': f['r_outcome'],
            'june_gate_source_close_ts': src_ts,
            'june_lookahead_hours': round(la_h, 2),
            'backonly_gate_source_close_ts': src_ts_bo,
            'backonly_lookahead_hours': round(la_h_bo, 2),
            'gate_flips': f['regime'] != f['regime_backonly'],
        })
    aud = pd.DataFrame(rows)
    june_bull = led_oos[led_oos['regime'] == 'bull_30d']
    bo_bull = led_oos[led_oos['regime_backonly'] == 'bull_30d']
    a_bull = aud[aud['regime_june'] == 'bull_30d']
    summ = {
        'construction_june': 'daily close resampled 1D (stamped at 00:00, value = last close of that day), '
                             'pct_change(30), reindexed onto the hourly grid with method="ffill"',
        'consequence': 'an hourly bar at HH:00 of day D reads a 30d return whose numerator is the LAST close '
                       'of day D — i.e. up to 23 h of the same day that has not happened yet at the fire',
        'oos_fires': int(len(aud)),
        'fires_whose_gate_source_close_is_at_or_after_the_fire': int((aud['june_lookahead_hours'] > 0).sum()),
        'max_lookahead_hours': round(float(aud['june_lookahead_hours'].max()), 2),
        'min_lookahead_hours': round(float(aud['june_lookahead_hours'].min()), 2),
        'mean_lookahead_hours': round(float(aud['june_lookahead_hours'].mean()), 2),
        'bull_gated_fires_with_lookahead': int((a_bull['june_lookahead_hours'] > 0).sum()),
        'bull_gated_max_lookahead_hours': round(float(a_bull['june_lookahead_hours'].max()), 2),
        'backonly_max_lookahead_hours': round(float(aud['backonly_lookahead_hours'].max()), 2),
        'backonly_is_implementable': bool((aud['backonly_lookahead_hours'] < 0).all()),
        'gate_flips': aud[aud['gate_flips']][['ts', 'regime_june', 'ret_30d_june', 'regime_backonly',
                                              'ret_30d_backonly', 'r_outcome']]
            .assign(ts=lambda d: d['ts'].astype(str)).to_dict('records'),
        'june_bull_set': {**subset_stats(june_bull['r_outcome'].values.astype(float)),
                          'ts': june_bull['ts'].astype(str).tolist()},
        'backonly_bull_set': {**subset_stats(bo_bull['r_outcome'].values.astype(float)),
                              'ts': bo_bull['ts'].astype(str).tolist()},
        'set_symmetric_difference': sorted(set(june_bull['ts'].astype(str))
                                           ^ set(bo_bull['ts'].astype(str))),
    }
    jb = summ['june_bull_set']
    bb = summ['backonly_bull_set']
    summ['verdict_june_construction'] = verdict_of(jb['n'], jb['mean_R'], 1.60)
    summ['verdict_backonly_construction'] = verdict_of(bb['n'], bb['mean_R'], 1.60)
    return aud, summ


# ── F3 OI source seam ───────────────────────────────────────────────────────

def seam_split(led_oos: pd.DataFrame, parity: dict, mar: float) -> dict:
    bull = led_oos[led_oos['regime'] == 'bull_30d']
    pre = bull[bull['ts'] <= OI_SEAM_LAST_COINDESK]
    post = bull[bull['ts'] > OI_SEAM_LAST_COINDESK]
    pool_pre = led_oos[led_oos['ts'] <= OI_SEAM_LAST_COINDESK]
    pool_post = led_oos[led_oos['ts'] > OI_SEAM_LAST_COINDESK]
    s = parity['data_quality']['oi_seam']
    v_post, why_post = verdict_of(len(post), subset_stats(post['r_outcome'].values.astype(float))['mean_R'], mar)
    return {
        'seam': {'last_coindesk_ohlc_row': s['last_ohlc_row_utc'],
                 'first_binance_snapshot_row': s['first_snapshot_row_utc'],
                 'seam_1h_oi_change_pct': s['seam_1h_change_pct'],
                 'median_abs_1h_change_pct_2026': s['median_abs_1h_change_pct_2026'],
                 'p99_abs_1h_change_pct_2026': s['p99_abs_1h_change_pct_2026'],
                 'seam_move_vs_median': round(s['seam_1h_change_pct'] / s['median_abs_1h_change_pct_2026'], 2)},
        'bull_gated_pre_seam': {**basic(pre), 'ts': pre['ts'].astype(str).tolist()},
        'bull_gated_post_seam': {**basic(post), 'ts': post['ts'].astype(str).tolist()},
        'pooled_pre_seam': basic(pool_pre), 'pooled_post_seam': basic(pool_post),
        'post_seam_verdict_if_it_were_the_whole_sample': [v_post, why_post],
        'oos_days_pre_seam': round((OI_SEAM_LAST_COINDESK - OOS_START).total_seconds() / 86400, 1),
        'oos_days_post_seam': round((pd.Timestamp(led_oos['ts'].max()) - OI_SEAM_LAST_COINDESK)
                                    .total_seconds() / 86400, 1),
    }


# ── F4 bootstrap + DSR + clustering ─────────────────────────────────────────

def resample_stats(r: np.ndarray, n_iter: int = 20000, seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(r), size=(n_iter, len(r)))
    means = r[idx].mean(axis=1)
    return {
        'n': int(len(r)), 'point_mean_R': round(float(r.mean()), 4), 'n_iter': n_iter,
        'p02.5': round(float(np.percentile(means, 2.5)), 4),
        'p05': round(float(np.percentile(means, 5)), 4),
        'p50': round(float(np.percentile(means, 50)), 4),
        'p95': round(float(np.percentile(means, 95)), 4),
        'p97.5': round(float(np.percentile(means, 97.5)), 4),
        'P_mean_gt_0': round(float((means > 0).mean()), 4),
        'P_mean_ge_0.10': round(float((means >= BUILD_MIN_MEAN_R).mean()), 4),
    }


def block_resample_stats(r: np.ndarray, block: int = 3, n_iter: int = 20000,
                         seed: int = SEED) -> dict:
    """Circular-block bootstrap of the MEAN, blocks of `block` consecutive
    fires — the fires are clustered in time and overlap (48 h TIF, 24 h
    cooldown), so the iid bootstrap overstates the effective sample."""
    rng = np.random.default_rng(seed)
    T = len(r)
    means = np.empty(n_iter)
    for i in range(n_iter):
        idx = vboot.circular_block_indices(T, T, block, rng)
        means[i] = r[idx].mean()
    return {'block': block, 'n_iter': n_iter,
            'p05': round(float(np.percentile(means, 5)), 4),
            'p50': round(float(np.percentile(means, 50)), 4),
            'p95': round(float(np.percentile(means, 95)), 4),
            'P_mean_gt_0': round(float((means > 0).mean()), 4),
            'P_mean_ge_0.10': round(float((means >= BUILD_MIN_MEAN_R).mean()), 4)}


def episode_resample_stats(led: pd.DataFrame, episodes: list[list], n_iter: int = 20000,
                           seed: int = SEED) -> dict:
    """Cluster bootstrap: resample whole firing EPISODES with replacement.

    8 of the 10 fires sit inside one 19-day bull episode; treating them as 10
    independent observations overstates the sample. Resampling episodes (and
    keeping every fire inside a drawn episode) is the honest version of the
    question "how much does this result depend on that one episode?"."""
    groups = [led.set_index('ts').loc[e, 'r_outcome'].values.astype(float) for e in episodes]
    rng = np.random.default_rng(seed)
    k = len(groups)
    means = np.empty(n_iter)
    for i in range(n_iter):
        pick = rng.integers(0, k, size=k)
        means[i] = np.concatenate([groups[j] for j in pick]).mean()
    return {'n_episodes': k, 'n_iter': n_iter,
            'episode_sizes': [int(len(g)) for g in groups],
            'p05': round(float(np.percentile(means, 5)), 4),
            'p50': round(float(np.percentile(means, 50)), 4),
            'p95': round(float(np.percentile(means, 95)), 4),
            'P_mean_gt_0': round(float((means > 0).mean()), 4),
            'P_mean_ge_0.10': round(float((means >= BUILD_MIN_MEAN_R).mean()), 4)}


def clustering(led: pd.DataFrame, tif_h: int = 48) -> dict:
    t = pd.DatetimeIndex(led['ts']).sort_values()
    # NOT t.view('int64') / 3.6e12: under pandas 3.0 these stamps are
    # datetime64[us], so that yields microseconds and reports every gap as
    # ~0.05h. Go through timedelta64 so the unit is explicit. (Fixed
    # 2026-09-09 after verification; it had inverted the overlap conclusion.)
    gaps_h = (np.diff(t.values).astype('timedelta64[s]').astype(float) / 3600.0
              if len(t) > 1 else np.array([]))
    # worst-case overlap: a fire's trade can still be open (TIF) when the next fires
    overlaps = int((gaps_h < tif_h).sum())
    # episodes = runs of fires separated by <= 7 days
    eps, cur = [], [t[0]]
    for a, b in zip(t[:-1], t[1:]):
        if (b - a) <= pd.Timedelta(days=7):
            cur.append(b)
        else:
            eps.append(cur)
            cur = [b]
    eps.append(cur)
    return eps, {
        'n_fires': int(len(t)), 'first': str(t.min()), 'last': str(t.max()),
        'median_gap_hours': round(float(np.median(gaps_h)), 1) if len(gaps_h) else None,
        'min_gap_hours': round(float(gaps_h.min()), 1) if len(gaps_h) else None,
        'consecutive_pairs_closer_than_TIF_48h': overlaps,
        'n_episodes_7d_linkage': len(eps),
        'episodes': [{'start': str(e[0]), 'end': str(e[-1]), 'n': len(e),
                      'span_days': round((e[-1] - e[0]).total_seconds() / 86400, 1),
                      'sum_R': round(float(led.set_index('ts').loc[e, 'r_outcome'].sum()), 3)}
                     for e in eps],
    }


def n_trials_breakeven(r: np.ndarray, max_n: int = 500) -> dict:
    """Smallest n_trials at which the DSR of this series drops below 0.95.

    N_TRIALS = 1 is right for THIS study (nothing was searched here), but the
    June studies that chose the -2 % threshold, the 2 %/3 % stop-target and the
    +10 % bull gate did search. This answers "how big would June's effective
    search have to have been to wipe out the full-sample significance?"."""
    for n in range(1, max_n + 1):
        d = vdsr.dsr_from_returns(r, n_trials=n)
        if d is None:
            return {'n_trials_where_dsr_falls_below_0.95': None, 'note': 'DSR undefined'}
        if d['dsr'] <= 0.95:
            return {'n_trials_where_dsr_falls_below_0.95': n,
                    'dsr_at_that_n': round(d['dsr'], 4),
                    'dsr_at_n_minus_1': round(vdsr.dsr_from_returns(r, n_trials=max(n - 1, 1))['dsr'], 4)}
    return {'n_trials_where_dsr_falls_below_0.95': f'> {max_n}'}


def dsr_block(r: np.ndarray, label: str, trades_per_year: float) -> dict:
    d = vdsr.dsr_from_returns(r, n_trials=1, periods_per_year=trades_per_year)
    b = vboot.bootstrap_sharpe(list(map(float, r)), n_iter=20000, threshold=0.0,
                               seed=SEED, n_per_year=trades_per_year)
    req = vboot.dsr_required_sr(len(r), n_trials=1, target_p=0.95)
    req5 = vboot.dsr_required_sr(len(r), n_trials=5, target_p=0.95)
    return {
        'label': label, 'n': int(len(r)), 'trades_per_year': round(trades_per_year, 2),
        'sr_per_trade': round(d['sr_per_obs'], 4), 'sr_ann': round(d['sr_ann'], 3),
        'skew': round(d['skew'], 3), 'kurtosis_full': round(d['kurt'], 3),
        'n_trials': 1, 'sr_expected_under_H0': round(d['sr_expected'], 4),
        'dsr': round(d['dsr'], 4), 'dsr_z': round(d['dsr_z'], 3),
        'dsr_rejects_null_at_0.95': bool(d['dsr'] > 0.95),
        'sr_per_trade_required_for_dsr_0.95_at_N1': round(req, 4),
        'sr_per_trade_required_for_dsr_0.95_at_N5': round(req5, 4),
        'bootstrap_sharpe_p05': round(b['sr_p05'], 4), 'bootstrap_sharpe_p50': round(b['sr_p50'], 4),
        'bootstrap_sharpe_p95': round(b['sr_p95'], 4), 'P_sharpe_gt_0': round(b['p_at_or_above'], 4),
    }


# ── F5 clause (b) decomposition ─────────────────────────────────────────────

def clause_b_decomposition(led_oi: pd.DataFrame, led_fcd: pd.DataFrame,
                           w_end: pd.Timestamp, summary: dict) -> dict:
    oi_bull = led_oi[(led_oi['regime'] == 'bull_30d') & led_oi['resolved']
                     & (led_oi['ts'] >= FULL_SAMPLE_START) & (led_oi['ts'] <= w_end)].copy()
    fcd = led_fcd[(led_fcd['handling'] == 'A') & led_fcd['resolved']
                  & (led_fcd['ts'] >= FULL_SAMPLE_START) & (led_fcd['ts'] <= w_end)].copy()
    cols = ['ts', 'r_outcome']
    comb = pd.concat([oi_bull[cols], fcd[cols]], ignore_index=True).sort_values('ts')

    def mar_of(d: pd.DataFrame, end: pd.Timestamp) -> dict:
        m = window_metrics(d, 'x', end)
        return {k: m.get(k) for k in ('n', 'meanR', 'sumR', 'maxDD', 'annual_R', 'MAR', 'span_y')}

    pre_oos_end = OOS_START - pd.Timedelta(seconds=1)
    comb_pre = comb[comb['ts'] <= pre_oos_end]
    oi_pre = oi_bull[oi_bull['ts'] <= pre_oos_end]
    fcd_pre = fcd[fcd['ts'] <= pre_oos_end]
    return {
        'combined_full_sample': mar_of(comb, w_end),
        'oi_bull_only_full_sample': mar_of(oi_bull[cols], w_end),
        'fcd_only_full_sample': mar_of(fcd[cols], w_end),
        'fcd_share_of_combined_sum_R': round(float(fcd['r_outcome'].sum() / comb['r_outcome'].sum()), 3),
        'combined_pre_OOS_only': mar_of(comb_pre, pre_oos_end),
        'oi_bull_pre_OOS_only': mar_of(oi_pre[cols], pre_oos_end),
        'fcd_pre_OOS_only': mar_of(fcd_pre[cols], pre_oos_end),
        'oos_contribution_to_combined_sum_R': round(float(comb[comb['ts'] >= OOS_START]['r_outcome'].sum()), 3),
        'clause_b_is_mostly_in_sample_note': (
            'clause (b) is a FULL-SAMPLE statistic: only the OOS window is unseen, '
            'and it contributes the sum R above out of the total; the rest of MAR was '
            'already known in June'),
        'fcd_oos': summary['oos_funding_cvd'],
    }


def main() -> None:
    print('=' * 100)
    print('S-SqueezeBull re-validation — step 5: fragility of the BUILD verdict')
    print('=' * 100)
    summary = load_json(RESULTS / 'summary.json')
    parity = load_json(RESULTS / 'parity.json')
    oos = load_json(RESULTS / 'oos.json')
    led_oos = read_ledger(RESULTS / 'oos_oi_flush_ledger.csv')
    led_oi = read_ledger(RESULTS / 'full_oi_flush_ledger.csv')
    led_fcd = read_ledger(RESULTS / 'full_funding_cvd_ledger.csv')
    w_end = pd.Timestamp(oos['oos_window'][1])
    mar_b = summary['clause_map']['b_full_sample_combined_MAR']['measured']
    bull = led_oos[(led_oos['regime'] == 'bull_30d') & led_oos['resolved']].sort_values('ts').reset_index(drop=True)
    r = bull['r_outcome'].values.astype(float)
    print(f"headline: verdict {summary['verdict']}; OOS bull-gated n={len(r)} meanR={r.mean():+.4f}; "
          f"clause-(b) MAR {mar_b}; window {oos['oos_window'][0][:10]} -> {oos['oos_window'][1][:10]}")

    out: dict = {
        'generated_at_utc': str(pd.Timestamp.now('UTC')),
        'headline_verdict_unchanged': summary['verdict'],
        'thresholds': {'n_min': BUILD_MIN_N, 'mean_R_min': BUILD_MIN_MEAN_R, 'MAR_min': BUILD_MIN_MAR},
        'margins': {
            'n_margin': int(len(r) - BUILD_MIN_N),
            'mean_R_margin': round(float(r.mean() - BUILD_MIN_MEAN_R), 4),
            'MAR_margin': round(float(mar_b - BUILD_MIN_MAR), 4),
        },
    }

    # F1
    out['F1_leave_k_out'] = leave_k_out(bull, mar_b)
    f1 = out['F1_leave_k_out']
    print('\n--- F1 leave-k-out on the OOS bull-gated set')
    for k in (1, 2):
        d = f1[f'drop_best_{k}']
        print(f"  drop best {k}: n={d['n']} meanR={d['mean_R']:+.4f} sumR={d['sum_R']:+.3f} -> "
              f"{d['verdict']} ({d['why']}); mean clause alone met: {d['mean_clause_a_met_ignoring_n_floor']}")
    jk = f1['jackknife_leave_one_out']
    print(f"  jackknife (n=9 each): meanR range [{jk['min_mean_R']:+.4f}, {jk['max_mean_R']:+.4f}]; "
          f"{jk['n_subsets_mean_ge_0.10']}/{jk['n_subsets']} subsets still >= +0.10")
    lt = f1['leave_two_out_all_pairs']
    print(f"  all leave-two-out pairs (n=8): meanR range [{lt['min_mean_R']:+.4f}, {lt['max_mean_R']:+.4f}]; "
          f"share >= +0.10 = {lt['share_pairs_mean_ge_0.10']:.0%}; share <= 0 = {lt['share_pairs_mean_le_0']:.0%}")
    c = f1['concentration']
    print(f"  concentration: {c['n_targets']} target exits carry {c['sum_R_from_target_exits']:+.3f} of "
          f"{c['sum_R']:+.3f} sum R ({c['share_of_sum_R_from_targets']:.0%})")

    # F2
    px = load_px_hourly()
    aud, reg = regime_audit(led_oos, px)
    aud.to_csv(RESULTS / 'fragility_regime_audit.csv', index=False)
    out['F2_regime_gate_lookahead'] = reg
    print('\n--- F2 regime-gate look-ahead audit')
    print(f"  June construction: {reg['fires_whose_gate_source_close_is_at_or_after_the_fire']}/{reg['oos_fires']} "
          f"OOS fires read a daily close stamped at or after the fire; look-ahead "
          f"[{reg['min_lookahead_hours']}, {reg['max_lookahead_hours']}] h (mean {reg['mean_lookahead_hours']} h)")
    print(f"  backward-only construction is strictly causal: {reg['backonly_is_implementable']} "
          f"(max source-close offset {reg['backonly_max_lookahead_hours']} h)")
    print(f"  gate flips between the two constructions: {len(reg['gate_flips'])}")
    for g in reg['gate_flips']:
        print(f"    {g['ts']}  June={g['regime_june']} ({g['ret_30d_june']:+.4f}) -> "
              f"backonly={g['regime_backonly']} ({g['ret_30d_backonly']:+.4f})  R={g['r_outcome']:+.3f}")
    print(f"  June bull set     n={reg['june_bull_set']['n']} meanR={reg['june_bull_set']['mean_R']:+.4f} "
          f"-> {reg['verdict_june_construction'][0]}")
    print(f"  backonly bull set n={reg['backonly_bull_set']['n']} meanR={reg['backonly_bull_set']['mean_R']:+.4f} "
          f"-> {reg['verdict_backonly_construction'][0]}")

    # F3
    out['F3_oi_seam'] = seam_split(led_oos, parity, mar_b)
    f3 = out['F3_oi_seam']
    print('\n--- F3 OI source seam')
    print(f"  seam: last CoinDesk OHLC row {f3['seam']['last_coindesk_ohlc_row']}, first Binance snapshot "
          f"{f3['seam']['first_binance_snapshot_row']}; 1 h change across it {f3['seam']['seam_1h_oi_change_pct']}% "
          f"({f3['seam']['seam_move_vs_median']}x the 2026 median |1 h| move)")
    print(f"  bull-gated fires pre-seam  n={f3['bull_gated_pre_seam']['n']} meanR={f3['bull_gated_pre_seam']['mean_R']} "
          f"sumR={f3['bull_gated_pre_seam']['sum_R']} ({f3['oos_days_pre_seam']} days of window)")
    print(f"  bull-gated fires post-seam n={f3['bull_gated_post_seam']['n']} meanR={f3['bull_gated_post_seam']['mean_R']} "
          f"sumR={f3['bull_gated_post_seam']['sum_R']} ({f3['oos_days_post_seam']} days of window)")
    print(f"  post-seam subset alone -> {f3['post_seam_verdict_if_it_were_the_whole_sample']}")

    # F4
    span_y = (w_end - OOS_START).total_seconds() / (365.25 * 86400)
    tpy = len(r) / span_y
    eps, cl_summary = clustering(bull)
    out['F4_uncertainty'] = {
        'iid_bootstrap_mean_R': resample_stats(r),
        'block_bootstrap_mean_R_block3': block_resample_stats(r, block=3),
        'episode_cluster_bootstrap_mean_R': episode_resample_stats(bull, eps),
        'dsr_oos_bull_gated': dsr_block(r, 'OOS bull-gated OI flush', tpy),
        'clustering': cl_summary,
        'n_trials_justification': (
            'N_TRIALS = 1: the README froze both rules, every threshold and the decision rule '
            'before any number was computed, and this study fits nothing — it re-runs the June '
            'code path on a later window. The multiple-testing penalty for THIS study is therefore '
            'zero. It is NOT zero for the June studies that selected the -2 % threshold, the 2 %/3 % '
            'stop/target and the +10 % bull gate out of a grid; the June ablation swept at least '
            '3 thresholds x 3 regimes, and the DSR here does not deflate for that prior search.'),
    }
    # context: the same statistics on the full-sample legs
    full_bull = led_oi[(led_oi['regime'] == 'bull_30d') & led_oi['resolved']
                       & (led_oi['ts'] >= FULL_SAMPLE_START) & (led_oi['ts'] <= w_end)]
    fspan = (w_end - full_bull['ts'].min()).total_seconds() / (365.25 * 86400)
    fb = full_bull['r_outcome'].values.astype(float)
    out['F4_uncertainty']['dsr_full_sample_bull_gated_context'] = dsr_block(
        fb, 'full-sample bull-gated OI flush', len(full_bull) / fspan)
    out['F4_uncertainty']['n_trials_breakeven_full_sample'] = n_trials_breakeven(fb)
    out['F4_uncertainty']['n_trials_breakeven_oos'] = n_trials_breakeven(r)
    f4 = out['F4_uncertainty']
    print('\n--- F4 uncertainty on the OOS mean R')
    ib = f4['iid_bootstrap_mean_R']
    print(f"  iid bootstrap (n={ib['n']}, {ib['n_iter']} resamples): mean R {ib['point_mean_R']:+.4f}, "
          f"90% CI [{ib['p05']:+.4f}, {ib['p95']:+.4f}], 95% CI [{ib['p02.5']:+.4f}, {ib['p97.5']:+.4f}]; "
          f"P(mean>0)={ib['P_mean_gt_0']:.1%}; P(mean>=+0.10)={ib['P_mean_ge_0.10']:.1%}")
    bb = f4['block_bootstrap_mean_R_block3']
    print(f"  circular-block bootstrap (block={bb['block']}): 90% CI [{bb['p05']:+.4f}, {bb['p95']:+.4f}]; "
          f"P(mean>0)={bb['P_mean_gt_0']:.1%}; P(mean>=+0.10)={bb['P_mean_ge_0.10']:.1%}")
    eb = f4['episode_cluster_bootstrap_mean_R']
    print(f"  episode cluster bootstrap ({eb['n_episodes']} episodes, sizes {eb['episode_sizes']}): "
          f"90% CI [{eb['p05']:+.4f}, {eb['p95']:+.4f}]; P(mean>0)={eb['P_mean_gt_0']:.1%}; "
          f"P(mean>=+0.10)={eb['P_mean_ge_0.10']:.1%}")
    d = f4['dsr_oos_bull_gated']
    print(f"  DSR at N_TRIALS=1: per-trade SR {d['sr_per_trade']:+.4f} (ann {d['sr_ann']:+.2f}, {d['trades_per_year']}/yr), "
          f"skew {d['skew']}, kurt {d['kurtosis_full']} -> DSR {d['dsr']:.4f} (z={d['dsr_z']}), "
          f"rejects null at 0.95: {d['dsr_rejects_null_at_0.95']}")
    print(f"     per-trade SR required for DSR>0.95 at n={d['n']}: {d['sr_per_trade_required_for_dsr_0.95_at_N1']} "
          f"(N=1) / {d['sr_per_trade_required_for_dsr_0.95_at_N5']} (N=5)")
    dc = f4['dsr_full_sample_bull_gated_context']
    print(f"  context, full-sample bull-gated (n={dc['n']}): SR {dc['sr_per_trade']:+.4f} -> DSR {dc['dsr']:.4f} "
          f"(rejects at 0.95: {dc['dsr_rejects_null_at_0.95']})")
    print(f"     full-sample DSR falls below 0.95 once n_trials >= "
          f"{f4['n_trials_breakeven_full_sample']['n_trials_where_dsr_falls_below_0.95']}; "
          f"OOS-only already below at n_trials = "
          f"{f4['n_trials_breakeven_oos']['n_trials_where_dsr_falls_below_0.95']}")
    cl = f4['clustering']
    print(f"  clustering: {cl['n_fires']} fires in {cl['n_episodes_7d_linkage']} episodes (7-day linkage); "
          f"{cl['consecutive_pairs_closer_than_TIF_48h']} consecutive pairs closer than the 48 h TIF")
    for e in cl['episodes']:
        print(f"    episode {e['start'][:10]} -> {e['end'][:10]}  n={e['n']} span={e['span_days']}d sumR={e['sum_R']:+.3f}")

    # F5
    out['F5_clause_b'] = clause_b_decomposition(led_oi, led_fcd, w_end, summary)
    f5 = out['F5_clause_b']
    print('\n--- F5 clause (b) decomposition (handling A, strict span)')
    for k in ('combined_full_sample', 'oi_bull_only_full_sample', 'fcd_only_full_sample',
              'combined_pre_OOS_only', 'oi_bull_pre_OOS_only', 'fcd_pre_OOS_only'):
        m = f5[k]
        print(f"  {k:<30s} n={m['n']:>3d} sumR={m['sumR']:+7.2f} maxDD={m['maxDD']:+6.2f} "
              f"annR={m['annual_R']:+6.2f} MAR={m['MAR']:5.2f}")
    print(f"  fCVD contributes {f5['fcd_share_of_combined_sum_R']:.0%} of combined sum R; the OOS window "
          f"contributes {f5['oos_contribution_to_combined_sum_R']:+.2f} R of it")

    dump_json(out, RESULTS / 'fragility.json')
    print(f"\nwrote {RESULTS / 'fragility.json'} and {RESULTS / 'fragility_regime_audit.csv'}")


if __name__ == '__main__':
    main()

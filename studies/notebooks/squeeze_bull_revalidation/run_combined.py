#!/usr/bin/env python3
"""Step 4 — full-sample combined portfolio (clause b), the verdict, and
results/summary.json (README.md decision rule). Reads the ledgers written by
run_oos.py and the parity flags from run_parity.py; touches no database.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from squeeze_bull_lib import (  # noqa: E402
    RESULTS, FULL_SAMPLE_START, JUNE_REF, read_ledger, load_json, dump_json,
    june_metrics, window_metrics, monthly_pearson,
)

BUILD_MIN_N = 10
BUILD_MIN_MEAN_R = 0.10
BUILD_MIN_MAR = 1.5


def combined_portfolio(led_oi: pd.DataFrame, led_fcd: pd.DataFrame, handling: str,
                       w_start: pd.Timestamp, w_end: pd.Timestamp) -> dict:
    oi_bull = led_oi[(led_oi['regime'] == 'bull_30d') & led_oi['resolved']
                     & (led_oi['ts'] >= w_start) & (led_oi['ts'] <= w_end)].copy()
    fcd = led_fcd[(led_fcd['handling'] == handling) & led_fcd['resolved']
                  & (led_fcd['ts'] >= w_start) & (led_fcd['ts'] <= w_end)].copy()
    oi_bull['sleeve'] = 'OI_FLUSH_bull'
    fcd['sleeve'] = 'FUNDING_CVD'
    cols = ['ts', 'sleeve', 'r_outcome', 'exit_kind']
    comb = pd.concat([oi_bull[cols], fcd[cols]], ignore_index=True).sort_values('ts').reset_index(drop=True)
    comb['cum_R'] = comb['r_outcome'].cumsum()

    # June (phase3a) formula: window cut at max(first fires) / min(last fires), span first->last fire
    js = max(oi_bull['ts'].min(), fcd['ts'].min())
    je = min(oi_bull['ts'].max(), fcd['ts'].max())
    j_oi = oi_bull[(oi_bull['ts'] >= js) & (oi_bull['ts'] <= je)]
    j_fcd = fcd[(fcd['ts'] >= js) & (fcd['ts'] <= je)]
    j_comb = pd.concat([j_oi[cols], j_fcd[cols]], ignore_index=True).sort_values('ts')
    r, n_act = monthly_pearson(oi_bull, fcd)
    return {
        'handling': handling,
        'window': [str(w_start), str(w_end)],
        'strict': {
            'OI_bull': window_metrics(oi_bull, 'OI_bull', w_end),
            'fCVD': window_metrics(fcd, f'fCVD_{handling}', w_end),
            'combined': window_metrics(comb, 'combined', w_end),
        },
        'june_formula': {
            'window': [str(js), str(je)],
            'OI_bull': june_metrics(j_oi, 'OI_bull'),
            'fCVD': june_metrics(j_fcd, f'fCVD_{handling}'),
            'combined': june_metrics(j_comb, 'combined'),
        },
        'monthly_pearson': r, 'active_months': n_act,
        'maxDD_additive_worst': round(float(window_metrics(oi_bull, 'a', w_end).get('maxDD', 0))
                                      + float(window_metrics(fcd, 'b', w_end).get('maxDD', 0)), 2),
        'ledger': comb,
    }


def show(tag: str, m: dict) -> None:
    if m.get('n', 0) == 0:
        print(f'  {tag:<34s} empty')
        return
    print(f"  {tag:<34s} n={m['n']:>3d} meanR={m['meanR']:+.3f} sumR={m['sumR']:+7.2f} WR={m['WR']:.0%} "
          f"maxDD={m['maxDD']:+6.2f} annR={m['annual_R']:+6.2f} MAR={m['MAR']:5.2f} span={m['span_y']:.2f}y")


def main() -> None:
    print('=' * 100)
    print('S-SqueezeBull re-validation — step 4: full-sample combined portfolio + verdict')
    print('=' * 100)
    parity = load_json(RESULTS / 'parity.json')
    oos = load_json(RESULTS / 'oos.json')
    led_oi = read_ledger(RESULTS / 'full_oi_flush_ledger.csv')
    led_fcd = read_ledger(RESULTS / 'full_funding_cvd_ledger.csv')
    w_end = pd.Timestamp(oos['oos_window'][1])
    w_start = FULL_SAMPLE_START
    print(f'full-sample window [{w_start}, {w_end}]; parity all_pass={parity["all_pass"]}')

    ports = {}
    for h in ('A', 'B'):
        p = combined_portfolio(led_oi, led_fcd, h, w_start, w_end)
        ports[h] = p
        print(f'\n--- combined portfolio, fCVD handling {h} (strict span: first fire -> window end)')
        show('OI flush bull-gated', p['strict']['OI_bull'])
        show(f'funding+CVD ({h})', p['strict']['fCVD'])
        show('COMBINED', p['strict']['combined'])
        print(f"  June formula (window {p['june_formula']['window'][0][:10]} -> {p['june_formula']['window'][1][:10]}):")
        show('  OI flush bull-gated', p['june_formula']['OI_bull'])
        show(f'  funding+CVD ({h})', p['june_formula']['fCVD'])
        show('  COMBINED', p['june_formula']['combined'])
        print(f"  monthly Pearson r = {p['monthly_pearson']} over {p['active_months']} active months; "
              f"additive-worst maxDD {p['maxDD_additive_worst']:+.2f}")
    ports['A']['ledger'].to_csv(RESULTS / 'combined_ledger.csv', index=False)
    ports['B']['ledger'].to_csv(RESULTS / 'combined_ledger_handlingB.csv', index=False)

    # cumulative-R figure (venv has matplotlib; Agg backend)
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 4))
        c = ports['A']['ledger']
        for sl, color in (('OI_FLUSH_bull', '#4878cf'), ('FUNDING_CVD', '#e1812c')):
            s = c[c['sleeve'] == sl]
            ax.plot(s['ts'], s['r_outcome'].cumsum(), label=f'{sl} (n={len(s)})', color=color, lw=1.2)
        ax.plot(c['ts'], c['cum_R'], label=f'combined (n={len(c)})', color='#333', lw=1.8)
        ax.axvline(pd.Timestamp(oos['oos_window'][0]), color='red', ls='--', lw=1, label='OOS start 2026-04-14')
        ax.set_ylabel('cumulative R (18 bp cost)')
        ax.set_title('Squeeze Bull ingredients, full sample 2022-01-30 -> last full day (frozen June rules)')
        ax.legend(loc='upper left')
        fig.tight_layout()
        fig.savefig(RESULTS / 'fig_cum_r.png', dpi=110)
        plt.close(fig)
        print(f"\nwrote {RESULTS / 'fig_cum_r.png'}")
    except Exception as e:  # pragma: no cover
        print(f'figure skipped: {e}')

    # ── verdict per the pre-registered rule ──
    bull = oos['oi_flush']['bull_gated_resolved']
    n_oos = int(bull['n'])
    mean_oos = bull['mean_R']
    mar_b = ports['A']['strict']['combined'].get('MAR', 0)
    mar_b_june_formula = ports['A']['june_formula']['combined'].get('MAR', 0)
    mar_b_handling_B = ports['B']['strict']['combined'].get('MAR', 0)
    clause_a = bool(n_oos >= BUILD_MIN_N and mean_oos is not None and mean_oos >= BUILD_MIN_MEAN_R)
    clause_kill = bool(n_oos >= BUILD_MIN_N and mean_oos is not None and mean_oos <= 0)
    clause_b = bool(mar_b >= BUILD_MIN_MAR)
    if not parity['all_pass']:
        verdict, reason = 'INCONCLUSIVE', 'parity check failed (see results/parity.json); no OOS number is decision-bearing'
    elif n_oos < BUILD_MIN_N:
        verdict, reason = 'INCONCLUSIVE', f'OOS bull-gated OI-flush n = {n_oos} < {BUILD_MIN_N} resolved fires'
    elif clause_kill:
        verdict, reason = 'KILL', f'OOS bull-gated OI-flush mean R {mean_oos:+.3f} <= 0 at n = {n_oos}'
    elif clause_a and clause_b:
        verdict, reason = 'BUILD', f'(a) n={n_oos} mean R {mean_oos:+.3f} >= +0.10 and (b) combined MAR {mar_b:.2f} >= 1.5'
    elif clause_a and not clause_b:
        verdict, reason = 'INCONCLUSIVE', f'(a) met but (b) failed: combined MAR {mar_b:.2f} < 1.5'
    else:
        verdict, reason = 'INCONCLUSIVE', f'n = {n_oos} >= 10 but 0 < mean R {mean_oos:+.3f} < +0.10'

    # projection for the INCONCLUSIVE (n < 10) case
    rates = oos['rates']
    lfd = pd.Timestamp(oos['last_full_day'], tz='UTC')
    need = max(0, BUILD_MIN_N - n_oos)
    proj = {'fires_still_needed': need}
    if need > 0:
        days_uncond = need / rates['june_bull_fires_per_year'] * 365.25
        proj['unconditional'] = {
            'rate_per_year': rates['june_bull_fires_per_year'],
            'days_needed': round(days_uncond, 0),
            'expected_date': str((lfd + pd.Timedelta(days=days_uncond)).date()),
            'note': 'assumes bull-regime days recur at the June-window frequency '
                    f"({rates['june_share_bull_hours']:.0%} of hours)",
        }
        bull_days_needed = need / rates['june_bull_fires_per_bull_day']
        proj['per_bull_day'] = {
            'rate_per_bull_day': rates['june_bull_fires_per_bull_day'],
            'bull_days_needed': round(bull_days_needed, 1),
            'earliest_date_if_bull_from_tomorrow': str((lfd + pd.Timedelta(days=1 + bull_days_needed)).date()),
            'note': 'the honest projection: fires need BTC 30d > +10%; the date is undefined until that regime returns',
        }
        if rates['oos_bull_days'] and rates['oos_bull_fires_resolved']:
            r_oos = rates['oos_bull_fires_resolved'] / rates['oos_bull_days']
            proj['oos_own_rate'] = {'fires_per_bull_day': round(r_oos, 4),
                                    'bull_days_needed': round(need / r_oos, 1)}

    # informational (no decision weight): how fragile is the OOS bull-gated mean at n = 10?
    oos_led = read_ledger(RESULTS / 'oos_oi_flush_ledger.csv')
    rb = oos_led[(oos_led['regime'] == 'bull_30d') & oos_led['resolved']]['r_outcome'].values
    boot: dict = {'n': int(len(rb))}
    if len(rb) >= 2:
        rng = np.random.default_rng(42)
        means = np.array([rng.choice(rb, len(rb), replace=True).mean() for _ in range(10000)])
        srt = np.sort(rb)
        boot.update({
            'p05': round(float(np.percentile(means, 5)), 3), 'p50': round(float(np.percentile(means, 50)), 3),
            'p95': round(float(np.percentile(means, 95)), 3),
            'P_mean_gt_0': round(float((means > 0).mean()), 3),
            'P_mean_ge_0.10': round(float((means >= BUILD_MIN_MEAN_R).mean()), 3),
            'mean_without_best_fire': round(float(srt[:-1].mean()), 3),
            'mean_without_worst_fire': round(float(srt[1:].mean()), 3),
            'fires_sorted_R': [round(float(x), 3) for x in srt],
        })

    clause_map = {
        'parity_all_pass': {'required': True, 'measured': parity['all_pass'],
                            'detail': {k: parity[k]['pass'] for k in ('P1', 'P2', 'P3', 'P4')}},
        'a_oos_bull_gated_n': {'required': f'>= {BUILD_MIN_N}', 'measured': n_oos},
        'a_oos_bull_gated_mean_R': {'required': f'>= {BUILD_MIN_MEAN_R:+.2f} (BUILD) / <= 0 (KILL)', 'measured': mean_oos},
        'b_full_sample_combined_MAR': {'required': f'>= {BUILD_MIN_MAR}', 'measured': mar_b,
                                       'june_formula_MAR': mar_b_june_formula,
                                       'handling_B_MAR': mar_b_handling_B, 'june_reference_MAR': JUNE_REF['P1_phase3a_combined']['MAR']},
        'clause_a': clause_a, 'clause_b': clause_b, 'clause_kill': clause_kill,
    }
    summary = {
        'study': 'S-SqueezeBull OOS re-validation', 'tag': 'AUDIT/re-validation, N_TRIALS=1 (frozen rules)',
        'generated_at_utc': str(pd.Timestamp.now('UTC')),
        'oos_window': oos['oos_window'], 'last_full_day': oos['last_full_day'],
        'verdict': verdict, 'reason': reason, 'clause_map': clause_map, 'projection': proj,
        'oos_bull_gated_bootstrap_informational': boot,
        'parity': {k: parity[k]['pass'] for k in ('P1', 'P2', 'P3', 'P4')} | {'all_pass': parity['all_pass']},
        'oos_oi_flush_bull_gated': bull,
        'oos_oi_flush_pooled': oos['oi_flush']['pooled_resolved'],
        'oos_oi_flush_by_regime': oos['oi_flush']['by_regime_resolved'],
        'oos_oi_flush_bull_gated_backonly_regime': oos['oi_flush']['bull_gated_backonly_regime_resolved'],
        'oos_funding_cvd': {h: {'n_fires': len(oos['funding_cvd'][h]['oos_fires']),
                                'resolved': oos['funding_cvd'][h]['oos_resolved'],
                                'full_n': oos['funding_cvd'][h]['full_n'],
                                'full_meanR': oos['funding_cvd'][h]['full_meanR']} for h in ('A', 'B')},
        'oos_regime': oos['regime']['june_construction'] | {'bull_gate_never_open': oos['regime']['bull_gate_never_open']},
        'combined': {h: {'strict': ports[h]['strict'], 'june_formula': ports[h]['june_formula'],
                         'monthly_pearson': ports[h]['monthly_pearson'], 'active_months': ports[h]['active_months'],
                         'maxDD_additive_worst': ports[h]['maxDD_additive_worst']} for h in ('A', 'B')},
        'rates': rates,
    }
    dump_json(summary, RESULTS / 'summary.json')

    print('\n' + '=' * 100)
    print(f'VERDICT: {verdict} — {reason}')
    print('=' * 100)
    print(f"  parity: {summary['parity']}")
    print(f"  (a) OOS bull-gated OI flush: n={n_oos} (need >= {BUILD_MIN_N}), mean R={mean_oos} (BUILD >= +0.10, KILL <= 0)")
    print(f"  (b) full-sample combined MAR (handling A, strict span) = {mar_b}  [June formula {mar_b_june_formula}; "
          f"handling B {mar_b_handling_B}; June reference 1.77]  need >= {BUILD_MIN_MAR}")
    if need > 0:
        print(f"  projection: {need} more bull-gated fires needed; unconditional {proj['unconditional']['expected_date']} "
              f"({proj['unconditional']['rate_per_year']}/yr); per-bull-day: {proj['per_bull_day']['bull_days_needed']} bull days "
              f"(earliest {proj['per_bull_day']['earliest_date_if_bull_from_tomorrow']} if bull from tomorrow)")
    if boot.get('p05') is not None:
        print(f"  informational bootstrap of the OOS bull-gated mean (n={boot['n']}, 10k resamples): "
              f"p05={boot['p05']:+.3f} p50={boot['p50']:+.3f} p95={boot['p95']:+.3f}; P(mean>0)={boot['P_mean_gt_0']:.0%}; "
              f"P(mean>=+0.10)={boot['P_mean_ge_0.10']:.0%}; without best fire {boot['mean_without_best_fire']:+.3f}, "
              f"without worst {boot['mean_without_worst_fire']:+.3f}")
    print(f"wrote {RESULTS / 'summary.json'}")


if __name__ == '__main__':
    main()

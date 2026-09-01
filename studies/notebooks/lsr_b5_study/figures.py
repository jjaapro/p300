#!/usr/bin/env python3
"""Figures for findings.md (run after score_variants.py). Reads
results/variant_summary.csv, results/scored/*.csv and
results/test0_stamp_semantics.json; writes results/fig_*.png.

Palette: the dataviz reference categorical order (validated, light
surface) in fixed variant order — never cycled or re-ranked.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / 'results'
VARIANTS = ['V0_sym30', 'V1_long30', 'V2_noB5', 'V3_sym90', 'V4_sym365', 'V5_long365']
COLORS = dict(zip(VARIANTS, ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300']))
IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')
INK, MUTED, GRID = '#0b0b0b', '#52514e', '#e6e5e1'

plt.rcParams.update({'font.size': 9, 'axes.edgecolor': GRID, 'axes.labelcolor': MUTED,
                     'xtick.color': MUTED, 'ytick.color': MUTED, 'axes.spines.top': False,
                     'axes.spines.right': False, 'figure.facecolor': 'white'})


def fig_oos_mean_r(df: pd.DataFrame) -> None:
    prod = df[df.tilt == 'prod']
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4), sharey=True)
    for ax, asset in zip(axes, ('BTC', 'ETH')):
        sub = prod[prod.asset == asset].set_index('variant').loc[VARIANTS]
        y = sub.oos_mean_r.values
        bars = ax.bar(range(len(VARIANTS)), y, color=[COLORS[v] for v in VARIANTS], width=0.6)
        for b, n in zip(bars, sub.oos_n.values):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + (0.01 if b.get_height() >= 0 else -0.05),
                    f'n={n}', ha='center', va='bottom', fontsize=8, color=MUTED)
        ax.axhline(0, color=GRID, lw=1)
        v0 = sub.loc['V0_sym30', 'oos_mean_r']
        ax.axhline(v0 + 0.10, color=MUTED, lw=1, ls=':')
        ax.set_xticks(range(len(VARIANTS)))
        ax.set_xticklabels([v.split('_', 1)[1] for v in VARIANTS], rotation=30, ha='right')
        ax.set_title(f'{asset} — OOS mean R per trade (production tilt)', loc='left', color=INK)
        ax.grid(axis='y', color=GRID, lw=0.6)
    axes[0].set_ylabel('mean R (OOS, 2025→)')
    fig.text(0.01, 0.01, 'dotted: adoption bar = V0 + 0.10R', color=MUTED, fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / 'fig_oos_mean_r.png', dpi=150)
    plt.close(fig)


def fig_cum_r() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8))
    for ax, asset in zip(axes, ('BTC', 'ETH')):
        for v in VARIANTS:
            p = OUT / 'scored' / f'{asset}_{v}_prod.csv'
            if not p.exists():
                continue
            t = pd.read_csv(p, parse_dates=['ts'])
            ax.plot(t.ts, t.eff_r.cumsum(), color=COLORS[v], lw=1.6 if v == 'V0_sym30' else 1.2,
                    label=v.split('_', 1)[1])
        ax.axvline(IS_END, color=MUTED, lw=1, ls=':')
        ax.text(IS_END, ax.get_ylim()[1], ' OOS →', color=MUTED, fontsize=8, va='top')
        ax.set_title(f'{asset} — cumulative R (production tilt)', loc='left', color=INK)
        ax.grid(color=GRID, lw=0.6)
        ax.legend(frameon=False, fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(OUT / 'fig_cum_r.png', dpi=150)
    plt.close(fig)


def fig_short_leg() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.4))
    for ax, asset in zip(axes, ('BTC', 'ETH')):
        for v in ('V0_sym30', 'V1_long30', 'V2_noB5'):
            p = OUT / 'scored' / f'{asset}_{v}_prod.csv'
            if not p.exists():
                continue
            t = pd.read_csv(p, parse_dates=['ts'])
            s = t[t.direction == 'short']
            ax.plot(s.ts, s.eff_r.cumsum(), color=COLORS[v], lw=1.4,
                    label=f'{v.split("_", 1)[1]} shorts (n={int((s["size"] > 0).sum())})')
        ax.axvline(IS_END, color=MUTED, lw=1, ls=':')
        ax.axhline(0, color=GRID, lw=1)
        ax.set_title(f'{asset} — short leg, cumulative R', loc='left', color=INK)
        ax.grid(color=GRID, lw=0.6)
        ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / 'fig_short_leg.png', dpi=150)
    plt.close(fig)


def fig_test0() -> None:
    p = OUT / 'test0_stamp_semantics.json'
    if not p.exists():
        return
    rows = json.loads(p.read_text(encoding='utf-8'))['binance_1d_vs_1h']['rows']
    if not rows:
        return
    days = [r['day'][5:] for r in rows]
    keys = [('a_start', 'a: 00:00 of D', '#2a78d6'), ('b_last_hour', 'b: 23:00 of D', '#eb6834'),
            ('c_mean', 'c: mean over D', '#1baf7a'), ('d_next_open', 'd: 00:00 of D+1', '#eda100')]
    fig, ax = plt.subplots(figsize=(10, 3.2))
    x = np.arange(len(rows))
    w = 0.2
    for i, (k, label, c) in enumerate(keys):
        ax.bar(x + (i - 1.5) * w, [r.get(k, np.nan) for r in rows], width=w, color=c, label=label)
    ax.set_xticks(x)
    ax.set_xticklabels(days, rotation=45, ha='right', fontsize=7)
    ax.set_ylabel('|1d value − 1h value|')
    ax.set_title('Test 0 — which hourly value does the daily stamp equal? (Binance BTCUSDT)',
                 loc='left', color=INK)
    ax.grid(axis='y', color=GRID, lw=0.6)
    ax.legend(frameon=False, fontsize=8, ncol=4)
    fig.tight_layout()
    fig.savefig(OUT / 'fig_test0_stamp.png', dpi=150)
    plt.close(fig)


def main() -> int:
    df = pd.read_csv(OUT / 'variant_summary.csv')
    fig_oos_mean_r(df)
    fig_cum_r()
    fig_short_leg()
    fig_test0()
    print('figures written:', sorted(p.name for p in OUT.glob('fig_*.png')))
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Assemble + execute paladin_study.ipynb from the results the scripts wrote.

Pipeline order: fetch_ohlcv.py -> venue_offset.py -> run_h0.py ->
entry_context.py -> resolve_and_exits.py -> this.
"""
from __future__ import annotations

import os

import nbformat as nbf
from nbclient import NotebookClient

HERE = os.path.dirname(os.path.abspath(__file__))


def md(src): return nbf.v4.new_markdown_cell(src)
def code(src): return nbf.v4.new_code_cell(src)


SETUP = """\
import os, sys, sqlite3
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('../../material/paladin/analysis'))
from paladin_data import load_ohlcv, load_pack
pd.set_option('display.width', 160)

d = load_pack()
pos = d['positions']
bt = pos[pos.is_backtestable]
print(f"positions {len(pos)}, backtestable {len(bt)}, "
      f"window {pos.signal_time_utc.min():%Y-%m-%d} -> {pos.signal_time_utc.max():%Y-%m-%d}")
print(f"symbols with data: ", end='')
con = sqlite3.connect('paladin_ohlcv.db')
have = {r[0] for r in con.execute('SELECT DISTINCT symbol FROM klines_15m')}
con.close()
missing = sorted(set(bt.symbol) - have)
print(f"{len(set(bt.symbol) & have)}/{bt.symbol.nunique()}  (missing: {missing})")
"""

OFFSET = """\
off = pd.read_csv('results/venue_offset.csv')
print(f"{len(off)} of his quoted prices joined to Binance 15m bars")
print(f"inside-bar share {off.inside_bar.mean()*100:.1f}%   "
      f"median dist-to-range {off.dist_to_range_pct.median():.3f}%   "
      f"p90 {off.dist_to_range_pct.quantile(.9):.3f}%")
fig, ax = plt.subplots(figsize=(8,3))
ax.hist(off.dist_to_range_pct.clip(0, 2), bins=80, color='#4878cf')
ax.set_xlabel('distance of his quoted price to our bar range (%)'); ax.set_ylabel('n')
ax.set_title('Venue offset: his prints vs Binance futures'); plt.tight_layout()
"""

H0 = """\
s = pd.read_csv('results/h0_summary.csv')
cols = ['variant','n','expectancy_r','median_r','hit_rate_pct','total_r',
        'his_expectancy_r','his_hit_rate_pct']
display(s[cols].round(3))
rep = pd.read_csv('results/h0_replay_168h.csv').dropna(subset=['r'])
both = rep.dropna(subset=['his_r'])
fig, ax = plt.subplots(figsize=(7,3.2))
ax.hist([both.r, both.his_r], bins=25, label=['mechanical plan','his execution'],
        color=['#4878cf','#e1812c'])
ax.legend(); ax.set_xlabel('R'); ax.set_title('H0: plan traded mechanically vs what he did (168h cap)')
plt.tight_layout()
print(f"same {len(both)} positions:  mechanical {both.r.mean():+.3f}R/trade  "
      f"vs his {both.his_r.mean():+.3f}R/trade")
"""

ENTRY = """\
h = pd.read_csv('results/entry_features.csv'); c = pd.read_csv('results/control_features.csv')
hl, cl = h[h.side_sign>0], c[c.side_sign>0]
hs, cs = h[h.side_sign<0], c[c.side_sign<0]
rows = []
for n in (1,3,7):
    rows.append([f'long after new {n}d low (H2)', hl[f'new_low_{n}d'].mean(), cl[f'new_low_{n}d'].mean()])
    rows.append([f'short after new {n}d high (H2)', hs[f'new_high_{n}d'].mean(), cs[f'new_high_{n}d'].mean()])
rows.append(['range position, longs (H10, median)', hl.range_position.median(), cl.range_position.median()])
rows.append(['entry vs 4h EMA200 in ATRs, longs (H4, median)',
             hl.entry_vs_ema200_atr.median(), cl.entry_vs_ema200_atr.median()])
rows.append(['signal in first 30min of 4h candle (H3)',
             (h.min_since_4h_close<=30).mean(), (c.min_since_4h_close<=30).mean()])
t = pd.DataFrame(rows, columns=['feature','his trades','matched controls']).round(3)
display(t)
m = h.dropna(subset=['risk_dist_pct','atr14_1h_pct'])
print(f"H5 stop%% vs ATR%%: pearson {m.risk_dist_pct.corr(m.atr14_1h_pct):.2f}, "
      f"spearman {m.risk_dist_pct.corr(m.atr14_1h_pct, method='spearman'):.2f} (n={len(m)})")
fig, axes = plt.subplots(1, 2, figsize=(11,3.2))
axes[0].hist([hl.range_position.dropna(), cl.range_position.dropna()], bins=20, density=True,
             label=['his longs','controls'], color=['#4878cf','#bbb'])
axes[0].legend(); axes[0].set_title('H10: where in the day range he buys')
axes[1].scatter(m.atr14_1h_pct, m.risk_dist_pct, s=14, alpha=.6, color='#4878cf')
axes[1].set_xlabel('1h ATR14 %'); axes[1].set_ylabel('published stop %')
axes[1].set_title('H5: stop distance vs volatility'); plt.tight_layout()
"""

EXITS = """\
un = pd.read_csv('results/unresolved_resolved.csv')
res = un[un.resolution.isin(['stop','target','timeout'])]
print('H12 - the 35 vanished positions:')
print(un.resolution.value_counts().to_string())
if len(res):
    print(f"  vanished trades that hit stop first: {(res.resolution=='stop').sum()}, "
          f"target first: {(res.resolution=='target').sum()}, mean R {res.r.mean():+.2f}")

mfe = pd.read_csv('results/manual_exit_mfe.csv')
print(f"\\nH11 - {len(mfe)} manual closes:")
print(f"  median extra favourable move within 72h after his exit: "
      f"{mfe.mfe_after_exit_r_72h.median():+.2f}R")
print(f"  median extra move before his published stop would have hit: "
      f"{mfe.mfe_after_exit_r_before_stop.median():+.2f}R")
print(f"  share leaving >0.5R pre-stop: {(mfe.mfe_after_exit_r_before_stop>0.5).mean():.0%}")

wick = pd.read_csv('results/loss_wick_analysis.csv')
t = wick[wick.wicked_through_stop]
print(f"\\nH3b - of {len(t)} losses that touched the stop: "
      f"{t.closed_4h_beyond_stop.sum()} also closed a 4h beyond it, "
      f"{(~t.closed_4h_beyond_stop).sum()} were wick-only")
"""

WINRATE = """\
# corrected scoreboard: reconstructed outcomes + the resolved vanished trades
res = pd.read_csv('results/unresolved_resolved.csv')
res = res[res.resolution.isin(['stop','target','timeout'])]
w0 = (pos.outcome=='win').sum(); l0 = (pos.outcome=='loss').sum(); b0 = (pos.outcome=='breakeven').sum()
wv = ((res.resolution=='target') | ((res.resolution=='timeout') & (res.r>0))).sum()
lv = ((res.resolution=='stop') | ((res.resolution=='timeout') & (res.r<=0))).sum()
print(f"reconstructed: {w0}W/{l0}L/{b0}BE = {w0/(w0+l0)*100:.0f}% ex-BE")
print(f"vanished resolved: +{wv}W/+{lv}L")
print(f"measured:      {w0+wv}W/{l0+lv}L/{b0}BE = {(w0+wv)/(w0+wv+l0+lv)*100:.0f}% ex-BE   "
      f"(his claims: 85% May, 89% July)")
"""


def main() -> None:
    nb = nbf.v4.new_notebook()
    nb.cells = [
        md('# Paladin study — what he sees, and whether it survives contact with data\n\n'
           'Source: `studies/material/paladin/analysis/` (UTC pack, 217 positions, 173 backtestable).\n'
           'OHLCV: Binance USDT-M futures 15m in study-local `paladin_ohlcv.db` (prod untouched), '
           'BTC/ETH 1m from prod read-only.\n\n'
           'Pipeline: `fetch_ohlcv.py → venue_offset.py → run_h0.py → entry_context.py → resolve_and_exits.py`. '
           'This notebook presents their `results/`. Findings prose: `findings.md`.'),
        code(SETUP),
        md('## 1. Can we trust the price join? (venue offset)\n'
           'He traded Blofin/MEXC/Bybit/Yubit/Binance; we join 981 prices he actually typed to our bars.'),
        code(OFFSET),
        md('## 2. H0 — trade his published plan mechanically\n'
           'Entry at his ref price on signal, exit at published stop or TP1, conservative ambiguity '
           '(stop wins a spanning bar). Variants: time cap 24/72/168/700h × (plain | stop-to-BE at +1R).'),
        code(H0),
        md('## 3. What the market looks like when he enters\n'
           'Each feature vs matched controls (same symbol, same time-of-day, random dates, no lookahead).'),
        code(ENTRY),
        md('## 4. Exits, vanished trades, and the real stop\n'
           '- **H12**: scan the 35 trades that vanish from the channel forward to stop/TP.\n'
           '- **H11**: after each manual close, how much was still on the table?\n'
           '- **H3b**: did losing stops see a 4h close beyond them, or only a wick?'),
        code(EXITS),
        md('## 5. The corrected scoreboard'),
        code(WINRATE),
    ]
    path = os.path.join(HERE, 'paladin_study.ipynb')
    nbf.write(nb, path)
    client = NotebookClient(nb, timeout=1800, kernel_name='python3',
                            resources={'metadata': {'path': HERE}})
    client.execute()
    nbf.write(nb, path)
    print('executed and wrote', path)


if __name__ == '__main__':
    main()

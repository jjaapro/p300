# Paladin study — findings

*2026-08-22. Data: `studies/material/paladin/analysis/` (UTC pack, 217 positions, 173 backtestable)
joined to Binance USDT-M futures 15m (study-local `paladin_ohlcv.db`, 68 symbols, 1.1M bars;
prod untouched) and prod `btc_1m`/`eth_1m` read-only. Pipeline: `fetch_ohlcv.py → venue_offset.py →
run_h0.py → entry_context.py → resolve_and_exits.py → build_notebook.py`. Presentation:
`paladin_study.ipynb`; raw outputs in `results/`.*

## Verdict in one paragraph

His published setups carry **no automatable edge**: traded mechanically exactly as posted
(entry, stop, TP1), 173 positions return **+0.00 to +0.07R per trade** at realistic horizons,
and **−0.05 to −0.06R with follower fills** at the next bar open. His recorded execution on the
same positions shows +1.64R at a 90% hit rate — the entire gap between his scoreboard and
breakeven lives in (a) discretionary exits and (b) accounting. The exits are real but not
copyable-by-signal; the accounting is measurably generous: the 35 trades that silently vanish
from the channel resolve to **17 losses, 1 win** against OHLCV, which moves the honest win rate
from his claimed 85–89% and the reconstructed 77% down to **~70%**. There is one genuinely
automatable signal in his behaviour (short the fresh-high sweep — see below), and it is the one
thing he does that his own narration doesn't emphasise.

## Data-trust gates (passed)

- **Venue offset**: 965 of his 981 quoted prices joined to our bars. Tight-timestamped card
  prices sit inside our 15m bar 86% of the time, p90 offset 0.05% → the join is sound.
- His **stated entry prices** are inside the bar only 34% of the time (median 0.34% adrift):
  the price he books is often minutes-to-hours stale by the time he posts. This is why every
  headline number above is also run with market fills at the next bar open.
- Four tail symbols (BONK, GPS, RE, PARTI) have venue/denomination mismatches vs Binance —
  they carry **zero** backtestable positions, so nothing downstream is affected.

## H0 — the decider (edge is not in the setup)

| variant (fill, cap) | exp R/trade | hit % |
|---|---|---|
| plan fill, 24h | +0.07 | 56 |
| plan fill, 72h | +0.00 | 53 |
| plan fill, 168h | +0.02 | 48 |
| plan fill, 700h | +0.22 | 44 |
| market fill, 72h | **−0.06** | 51 |
| market fill, 168h | **−0.05** | 48 |
| stop-to-BE at +1R, 168h | +0.05 | 42 |
| his recorded exits (n=107) | **+1.64** | **90** |

The 700h number (+0.22R) is drift capture in a market that recovered, not setup quality —
44% of trades end by timeout or run for weeks. His stated rule (stop to BE at +1R) does not
rescue the plan either.

## What he actually sees at entry (173 positions vs 6,920 matched controls)

Controls: same symbol, same time-of-day, random dates in the same window, features computed
only from bars closed before the timestamp.

| feature | his trades | control | read |
|---|---|---|---|
| short within 6h of a new 1-day high | **57%** | 23% | **real signal** |
| short within 6h of a new 3-day high | **48%** | 14% | **real signal** |
| short within 6h of a new 7-day high | **36%** | 9% | **real signal** |
| long within 6h of a new 1-day low | 31% | 25% | weak — the "buy the flush" story barely exists on longs |
| long's position in the day's range (median) | 0.41 | 0.50 | mild buy-low habit |
| long entry vs 4h EMA200 (ATRs, median) | −0.62 | −1.48 | he enters when price is *near* the 200 EMA rather than deep below it; regime-confounded |
| signal in first 30 min of a 4h candle | 14% | 14% | **null** — he talks 4h closes but does not time entries to them |
| published stop % vs 1h ATR % | Spearman **0.75** | — | his stops are a volatility multiple, trivially automatable |

His entries are also grid-anchored: 97% sit within 0.25% of a round level (pack finding, H1).

## Exits and accounting

- **H12 (vanished trades)**: of 35 positions that stop being mentioned, 19 resolve against
  OHLCV → 17 hit the stop first, 1 genuine win (SOL +5.4R still running), 1 wrong-sided-stop
  typo. Silent attrition is almost pure losses. Corrected scoreboard ≈ **121W / 52L / 10BE ≈ 70%**.
- **H11 (manual closes, n=53)**: median **+0.69R** more was available within 72h after his
  exit before his own stop would have hit; 58% of manual closes leave >0.5R behind. But 30% of
  the time the published stop *is* hit within 72h of his exit — the early booking also dodges
  real reversals. His exit style trades tail R for hit rate, exactly as he says ("my R is small,
  0.5–0.8R on avg").
- **H3b (are the stops real?)**: of 31 losses that touched the stop, 22 also printed a 4h close
  beyond it; only 29% were wick-only. The published stop is, mostly, the traded stop.

## What this means for us

1. **Do not build a "paladin sleeve" from his setups.** H0 is a clean negative: the posted
   plans are breakeven-at-best noise around a long bias in a recovering market (H14 didn't
   need a separate run — the 700h/timeout pattern already shows drift capture).
2. **The one idea worth taking forward: short-side fresh-high fade.** His shorts concentrate
   2.5–4× base rate after fresh 1/3/7-day highs and won 74% as executed. That is structurally
   the mirror of our validated long-side [short_squeeze sleeve](../../..//bots/short_squeeze)
   (sweep + reversal, long side only). A "sweep-fade short" candidate would be:
   fresh N-day high within 6h + rejection + ATR-multiple stop (his own stops are ~0.75
   Spearman to ATR) + small fixed target (~1R). Needs its own study with our standard
   walk-forward before anything more — and note his shorts were his worst stretch when he
   fought trend, so a trend/regime gate (cf. `chento_regime_filter`, asymmetric skip) is the
   first thing to test.
3. **Exit-style experiment, cheap and general**: his hit-rate engine is "book ~0.5–1R fast,
   never let a winner become a loser". H11 quantifies the cost (+0.69R median left behind) and
   the benefit (30% stop-dodge). Worth a one-notebook test on our own sleeves' MFE curves
   (chento_v3, short_squeeze) to see whether a "take 1R when touched, else original plan"
   overlay changes MAR — his data suggests it buys drawdown reduction with expectancy cost.
4. **Nothing else in his toolkit is load-bearing.** 4h-close timing: null. Long-side sweep:
   near-null. EMA200/round-levels: confounded with "price is always near some level"; the
   grid anchoring is real but is a *description* of where he clicks, not a demonstrated edge.

## Files

- `paladin_study.ipynb` — executed notebook, all tables/plots
- `results/h0_summary.csv`, `results/h0_replay_*.csv` — replay grid
- `results/entry_features.csv`, `results/control_features.csv` — 173 + 6,920 feature rows
- `results/unresolved_resolved.csv`, `results/manual_exit_mfe.csv`, `results/loss_wick_analysis.csv`
- `results/venue_offset.csv` — join-quality evidence
- `paladin_ohlcv.db` — 68 symbols × 15m, 2026-03-01→08-22 (regenerate with `fetch_ohlcv.py`)

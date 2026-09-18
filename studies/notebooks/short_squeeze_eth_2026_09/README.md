# SHORT_SQUEEZE on ETH 2026-09 — pre-registration (frozen before any result)

Written 2026-09-19 before `run_ss_eth.py` produced a number; frozen by `run_ss_eth.py freeze0` (sha256 of this file,
the library, the run script, the fixtures and every input file in `results/freeze_F0.json`). Roadmap §3 research
item 1, second assets: *"SHORT_SQUEEZE on ETH is not 'data exists': the sleeve reads `cd_spot_15m` and
`cd_open_interest`, and neither has an ETH twin. It needs Binance Vision spot ETHUSDT 15-minute klines and the
5-minute `metrics` archive rolled to close-of-hour open interest, built as a study-local twin … and its research
engine exists only inside two never-executed notebooks, so that study is an engine build first."* Tag: AUDIT of a
shipped rule on a new asset + one recommendation. Nothing under `bots/` or `strategies/` changes; an ETH paper twin,
if recommended, is a bot change that needs the operator's go-ahead.

## What is already known (disclosure)

- The BTC sleeve (S-005 sweep + CVD divergence, long only, London/NY, short-macro days) replays at n 70, win
  44.3 %, mean +0.40 R gross, PF 1.65 to 2026-05-18 (`execution_2026_09` E0 port of
  `short_squeeze_sessions/strategy_backtest.ipynb`, 2 bp per leg). The execution study measured its taker round trip
  at 10 bp and found the cost 47–60 % of gross ("retire pending user decision"); the roadmap's re-cut retires it if
  both variants ≤ 0 at 30 fires. The exit-policy stages found its trades (median 65 min) meet no microstructure
  event.
- The engine: `execution_2026_09/exec_lib.short_squeeze_frame` builds the notebook's frame from five prod.db tables
  (`cd_futures_15m`, `cd_spot_15m`, `cd_futures_ohlcv`, `cd_open_interest`, `cd_funding_rate`) and
  `short_squeeze_notebook_simulate` walks each trigger on the 1-minute spot path. Verified while scoping (three
  timestamps, 2023–2026): prod's `cd_futures_15m` and `cd_spot_15m` are Binance USDT-M perpetual and Binance spot
  bar for bar (OHLC, volume and taker buy volume equal the ORB perp panel and the spot-vs-perp stage's spot panel
  to the cent); prod's `cd_open_interest` close-of-hour is the 5-minute archive's snapshot at H + 1 h within about
  0.05 % (a different source, not identical). No ETH number of any kind has been computed.

## Data and the twin tables

For an asset, the five tables the engine reads are built from files already on disk, in the engine's own shape
(indexed by bar open time, the same columns):

| table | ETH source | BTC (fidelity run) source |
|---|---|---|
| `cd_futures_15m` | `orb_study/cache/ETHUSDT_perp_1m.npz` aggregated to 15 min (open of the first present minute, max, min, close of the last; `volume_buy` = taker buy volume, `volume_sell` = volume − taker buy) | the BTCUSDT perp panel, the same way |
| `cd_spot_15m` | `exit_policy_2026_09/cache/ETHUSDT_spot_1m_panel.npz`, the same aggregation | the BTCUSDT spot panel |
| `cd_futures_ohlcv` (hourly) | the perp panel aggregated to 60 min | the same |
| `cd_open_interest` (hourly) | `exit_policy_2026_09/cache/ETHUSDT_metrics_5m_full.npz` (2021-12-01 →): `oi_close` of hour H = the snapshot stamped H + 1 h, the close-of-hour convention item 30 restored | `BTCUSDT_metrics_5m_full.npz` (2020-09-01 →), the same |
| `cd_funding_rate` (hourly) | prod.db `cd_funding_rate_eth` (8-hour settlement rows; the engine forward-fills up to 8 hours inside the Asia window, so the Asia mean is the 00:00 settlement rate — a coarser reading than BTC's hourly predicted rate, disclosed) | prod.db `cd_funding_rate` (hourly to 2026-04-13, 8-hourly after) |

A bar with no present minute is absent from its table (the engine's `dropna` handles it). The spot 1-minute panel
ends 2026-09-14 00:00 UTC and the perp panel the same; the ETH sample is bounded by them. Simulation walks the
1-minute **spot** path (`execution_2026_09/cache/eth_1m.npz`, prod's `eth_1m`), the path the BTC port and the live
sleeve's management use.

## Engine (verbatim, not re-tuned)

`ss_eth_lib.frame(tables)` is `exec_lib.short_squeeze_frame` with the five loads replaced by arguments and nothing
else changed; **P0** runs both on prod's BTC tables and requires the same trigger set. Every parameter is the port's
(`SS_PARAMS` 0.15 / 0.70 / 0.10, lookback 24 bars, cooldown 16, window 90 × 56 bars, London/NY sessions, the
short-macro day: Asia close below open, Asia open interest up more than 0.5 %, Asia funding mean below 0); the
simulation is the port's (entry at the trigger bar's close, stop = trigger low × 0.999, target entry + 3 R, 6 h
time stop, walked on 1-minute spot, stop before target inside a minute), with the per-leg slippage made a parameter
so gross (0 bp) and the port's parity form (2 bp) are both computed.

**Costs.** `net_R = gross_R − 10 bp / risk_pct` per trade: the sleeve's `COST_BP_RT` and the execution study's
measured taker round trip on BTC and ETH, charged as a fraction of the trade's own risk distance (median risk about
0.5 % on BTC, so about 0.2 R). No funding: trades last hours.

## Fidelity gate (P1, before any ETH number is decision-bearing)

The same builder on the BTC panels must reproduce the prod-table BTC run over their common span: the share of
prod-table triggers also found by the panel run (and the reverse) — the only input that differs is the open
interest source (archive vs CoinDesk), which enters the macro gate's 0.5 % Asia threshold. **Jaccard ≥ 0.90** on the
trigger sets is required; below it the ETH numbers are DESCRIPTIVE and the builder is at fault, not the asset. The
per-trade P&L of the shared triggers must agree to 0.001 R (same path, same fills).

## Statistics (ETH)

n triggers; gross and net mean R with a 30-day-block bootstrap CI90 (`micro_lib.block_indices`, 5,000 draws,
seed 42, trades assigned to trigger days); win rate, profit factor, exit-kind mix (stop / target / time), median
risk %, the cost's share of gross; per calendar year; **halves** by trigger order; MAR = annual net R ÷ max drawdown
of cumulative net R; **DSR** (`dsr_from_returns`, n_trials 1) on net per-trade R. The BTC panel run's numbers are
shown beside them for scale, and the BTC prod-table run's as the anchor (which must match the E0 port's n 70 /
win 0.443 / mean 0.40 / PF 1.65 to 2026-05-18, P0).

## Decision rule (fixed now)

RECOMMEND an ETH paper twin only if **all** of:

- **(a)** n ≥ **30** triggers;
- **(b)** net mean R ≥ **+0.20 R** with the block-bootstrap CI90 excluding 0 — about what the BTC sleeve's own
  replay nets at 10 bp (+0.40 gross, 2 bp slip refunded, 0.2 R cost), so "at least as good as the sleeve it twins";
- **(c)** net mean R > 0 in **both halves** by trigger order;
- **(d)** **DSR ≥ 0.95** (one trial) on net per-trade R;
- **(e)** P1 passed (Jaccard ≥ 0.90).

If (e) fails: **DESCRIPTIVE** (fix the builder first). If (a)–(d) fail: **KILL for ETH**. Otherwise **RECOMMEND**,
which is a paper twin — and the BTC sleeve's own retirement question stands regardless: a recommended ETH twin joins
the re-cut at n = 20 / 30 on the same terms.

## Trial ledger

One rule, one asset, no sweep. n_trials = 1.

## Run order

`python run_ss_eth.py freeze0`, `python run_ss_eth.py outcomes`, then `C:/Python/Python313/python.exe
build_notebook.py` → `short_squeeze_eth.ipynb`. Findings in `findings.md`, written after the run. Fixtures:
`tests/test_ss_eth.py`.

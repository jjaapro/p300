#!/usr/bin/env python3
"""S-SqueezeBull OOS re-validation — shared code. README.md is the pre-registration.

The two June-2026 rules are IMPORTED from the June modules and never
re-implemented here:

  studies/notebooks/oi_flush/phase2_backtest.py      identify_long_flush_events, replay, stats
  studies/notebooks/funding_cvd_divergence/research.py attach_features, generate_triggers,
                                                       replay_trigger, summarize
  studies/notebooks/funding_cvd_divergence/phase2_robustness.py  WINNING (the frozen combo)

This file adds only: read-only DB loaders (the June SQL, `mode=ro`), the
optional "table as of <date>" cut used by the parity check, the backward-only
regime column (sensitivity), the 8 h-consistent funding series (handling B),
ledger builders that call the June functions, and metrics/report helpers.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RESULTS = HERE / 'results'
RESULTS.mkdir(exist_ok=True)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The June modules call sys.stdout.reconfigure(...) at import time on win32.
# ipykernel's OutStream has no reconfigure(); give it a no-op so `%run` works
# inside the notebook. Outside a kernel, do what the June modules do.
if not hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure = lambda **kw: None  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        pass
elif sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

from strategies.support.db import PROD_DB  # noqa: E402  (a Path constant; no side effects)

# ─── Frozen June rule functions ─────────────────────────────────────────────
from studies.notebooks.oi_flush.phase2_backtest import (  # noqa: E402
    COOLDOWN_HOURS, FLUSH_THRESHOLD, PRICE_DIR_THRESHOLD,
    COST_BP as OI_COST_BP, IS_END as JUNE_IS_END,
    identify_long_flush_events, replay as oi_replay, stats as oi_stats,
)
from studies.notebooks.funding_cvd_divergence.research import (  # noqa: E402
    ATR_MULT, TARGET_R, TIF_HOURS, COST_BP as FCD_COST_BP,
    attach_features, generate_triggers, replay_trigger, summarize as fcd_summarize,
)
from studies.notebooks.funding_cvd_divergence.phase2_robustness import WINNING  # noqa: E402

# ─── Frozen parameters (Phase 2 best combo, ablation threshold) ─────────────
OI_STOP_PCT = 0.02
OI_TARGET_PCT = 0.03
OI_TIF_H = 48
BULL_THRESHOLD = 0.10
FCD_TIF_BARS = TIF_HOURS * 4                      # 288
FCD_WINDOW_BARS = WINNING['z_window_days'] * 96   # 1344

# ─── Windows (UTC) ──────────────────────────────────────────────────────────
OOS_START = pd.Timestamp('2026-04-14 00:00:00', tz='UTC')
FUNDING_CUTOVER = pd.Timestamp('2026-04-13 00:00:00', tz='UTC')
CUTOVER_MIXED_END = FUNDING_CUTOVER + pd.Timedelta(days=WINNING['z_window_days'])
ETF_SPLIT = pd.Timestamp('2024-01-11 00:00:00', tz='UTC')
FULL_SAMPLE_START = pd.Timestamp('2022-01-30 00:00:00', tz='UTC')
JUNE_FCD_START = '2020-01-01'
JUNE_FCD_END = '2026-04-13'                        # the June --end (00:00 UTC, inclusive)
JUNE_OI_TABLE_END = pd.Timestamp('2026-06-05 19:01:47', tz='UTC')   # ablation generated_at
JUNE_P3_END = pd.Timestamp('2026-04-13 23:59:59', tz='UTC')
JUNE_FCD_LAST_FIRE = pd.Timestamp('2026-02-06 17:30:00', tz='UTC')  # phase3a window end
OI_SEAM_NOTE = 'CoinDesk rows through 2026-06-10 08:00 UTC; native Binance rows since (migration 2026-06-19)'

# ─── June reference numbers (from the June JSON outputs, copied 2026-09-06) ─
JUNE_REF = {
    'P1_phase3a_bull': {'n': 104, 'meanR': 0.280, 'MAR': 2.18, 'annual_R': 8.10},  # compute_metrics keys
    'P1_phase3a_combined': {'n': 118, 'MAR': 1.77, 'annual_R': 9.39, 'maxDD': -5.31,
                            'pearson_bull': 0.007},
    'P2_ablation_fixed_020_bull': {'n': 112, 'mean_R': 0.285, 'MAR': 2.05, 'maxDD': -3.72,
                                   'annual_R': 7.6, 'OOS_n': 17, 'OOS_meanR': 0.562,
                                   'cum_R': 31.92, 'WR': 0.607, 'n_per_yr': 26.7},
    'P2_ablation_fixed_020_pool': {'n': 416, 'mean_R': 0.009, 'MAR': 0.05, 'maxDD': -19.84,
                                   'OOS_n': 100, 'OOS_meanR': -0.028},
    'P3_caller': {'n': 104, 'n_tol': 3, 'mean_R': 0.28, 'mean_R_tol': 0.05},
    'P4_fcd': {'n': 21, 'mean_R': 1.0897659407605873},
}
# funding_cvd_phase2_robustness.json ledger (ts, r_outcome), n = 21
JUNE_FCD_LEDGER = [
    ('2020-09-16 03:45:00+00:00', 0.9761991344604137),
    ('2020-10-21 01:15:00+00:00', 5.881868095442362),
    ('2020-10-29 14:00:00+00:00', 1.5830103370013011),
    ('2021-06-09 11:45:00+00:00', 0.37384285286602087),
    ('2021-07-26 00:00:00+00:00', 5.940348949090778),
    ('2021-09-24 14:00:00+00:00', 0.5018589643966461),
    ('2021-12-23 18:00:00+00:00', -0.2205640596716889),
    ('2022-03-11 12:00:00+00:00', -1.0487872824345794),
    ('2022-04-12 15:15:00+00:00', -1.0685560271722274),
    ('2022-05-12 11:00:00+00:00', 0.3682248351947375),
    ('2022-06-07 20:15:00+00:00', -1.0516114255657045),
    ('2022-08-29 04:00:00+00:00', 0.2180327613278503),
    ('2022-10-21 18:00:00+00:00', 0.3035427297530237),
    ('2023-06-06 14:00:00+00:00', 1.04844792047381),
    ('2023-06-20 17:00:00+00:00', 5.910112753358139),
    ('2023-10-20 02:45:00+00:00', 2.921791483280264),
    ('2024-02-09 03:00:00+00:00', 1.8322477807830921),
    ('2025-02-11 20:30:00+00:00', 1.5715679780635432),
    ('2025-10-09 17:45:00+00:00', -1.0805808724006871),
    ('2026-01-31 21:30:00+00:00', -1.0383169252287607),
    ('2026-02-06 17:30:00+00:00', -1.037595227046005),
]
JUNE_JSON_DIR = ROOT / 'studies' / 'material' / 'chento' / 'validation'


# ─── Read-only loaders (June SQL) ───────────────────────────────────────────

def ro_connect() -> sqlite3.Connection:
    return sqlite3.connect(f'file:{PROD_DB.as_posix()}?mode=ro', uri=True)


def to_ts(col: pd.Series) -> pd.Series:
    return pd.to_datetime(col, unit='s', utc=True).dt.as_unit('ns')


OI_SEAM_FIRST_SNAPSHOT = pd.Timestamp('2026-06-10 09:00:00', tz='UTC')
OI_BACKFILL_REACH_START = pd.Timestamp('2026-05-20 00:00:00', tz='UTC')   # ~30d before the 2026-06-19 migration


def june_backfill_rows() -> pd.DatetimeIndex:
    """Hourly OI rows inside the CoinDesk era that the 2026-06-19 Binance
    backfill added (INSERT OR IGNORE reaches ~20-30 days back): point-snapshot
    rows (oi_open = oi_high = oi_low = oi_close) stamped between the backfill
    reach and the seam. These rows did not exist when the June studies ran
    (phase-1 JSON: 38,080 joined rows through 2026-06-05 15:00)."""
    con = ro_connect()
    oi = pd.read_sql('SELECT timestamp, oi_open, oi_high, oi_low, oi_close '
                     'FROM cd_open_interest ORDER BY timestamp', con)
    con.close()
    oi['ts'] = to_ts(oi['timestamp'])
    flat = ((oi['oi_open'] == oi['oi_close']) & (oi['oi_high'] == oi['oi_close'])
            & (oi['oi_low'] == oi['oi_close']))
    sel = flat & (oi['ts'] >= OI_BACKFILL_REACH_START) & (oi['ts'] < OI_SEAM_FIRST_SNAPSHOT)
    return pd.DatetimeIndex(oi.loc[sel, 'ts'])


def load_oi_frame(table_end: pd.Timestamp | None = None,
                  drop_rows: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    """phase2_backtest.load_data() byte-for-byte (read-only connection), plus
    an optional cut of the raw joined rows at `table_end` and an optional
    removal of rows that did not exist at the time (both replay "the table as
    of then" for the parity check), and a backward-only regime column for the
    sensitivity. Features are computed after the cut, as June did."""
    con = ro_connect()
    oi = pd.read_sql('SELECT timestamp, oi_close FROM cd_open_interest '
                     'ORDER BY timestamp', con)
    px = pd.read_sql('SELECT timestamp, open, high, low, close '
                     'FROM cd_futures_ohlcv ORDER BY timestamp', con)
    con.close()
    oi['ts'] = to_ts(oi['timestamp'])
    px['ts'] = to_ts(px['timestamp'])
    df = px.set_index('ts').drop(columns='timestamp').join(
        oi.set_index('ts').drop(columns='timestamp'), how='inner')
    if table_end is not None:
        df = df[df.index <= table_end].copy()
    if drop_rows is not None and len(drop_rows):
        df = df.drop(index=drop_rows, errors='ignore').copy()
    df['oi_chg_4h'] = df['oi_close'].pct_change(4)
    df['px_chg_4h'] = df['close'].pct_change(4)
    daily = df['close'].resample('1D').last()
    r30 = daily.pct_change(30)
    df['ret_30d'] = r30.reindex(df.index, method='ffill')                 # June
    df['ret_30d_backonly'] = r30.shift(1).reindex(df.index, method='ffill')  # sensitivity
    return df


def load_px_hourly() -> pd.DataFrame:
    """Price-only hourly frame (no OI join) with the June ret_30d construction —
    used for the OOS regime distribution and the regime of fCVD fires."""
    con = ro_connect()
    px = pd.read_sql('SELECT timestamp, open, high, low, close '
                     'FROM cd_futures_ohlcv ORDER BY timestamp', con)
    con.close()
    px['ts'] = to_ts(px['timestamp'])
    px = px.set_index('ts').drop(columns='timestamp')
    daily = px['close'].resample('1D').last()
    r30 = daily.pct_change(30)
    px['ret_30d'] = r30.reindex(px.index, method='ffill')
    px['ret_30d_backonly'] = r30.shift(1).reindex(px.index, method='ffill')
    return px


def last_full_day(px: pd.DataFrame) -> pd.Timestamp:
    """00:00 of the last UTC day with all 24 hourly rows in cd_futures_ohlcv."""
    counts = px['close'].groupby(px.index.floor('D')).count()
    return counts[counts >= 24].index.max()


def load_15m(start_ts: int, end_ts: int) -> pd.DataFrame:
    """research.load_btc_15m() byte-for-byte, read-only connection."""
    con = ro_connect()
    df = pd.read_sql("""
        SELECT timestamp, open, high, low, close,
               quote_volume_buy, quote_volume_sell
        FROM cd_futures_15m
        WHERE timestamp >= ? AND timestamp <= ?
        ORDER BY timestamp
    """, con, params=(start_ts, end_ts))
    con.close()
    df['ts'] = to_ts(df['timestamp'])
    return df.set_index('ts').drop(columns='timestamp')


def load_funding(start_ts: int, end_ts: int) -> pd.Series:
    """research.load_funding() byte-for-byte, read-only connection (handling A:
    the table as stored — hourly predicted before 2026-04-13, 8 h settlements after)."""
    con = ro_connect()
    df = pd.read_sql("""
        SELECT timestamp, fr_close
        FROM cd_funding_rate
        WHERE timestamp >= ? AND timestamp <= ?
        ORDER BY timestamp
    """, con, params=(start_ts, end_ts))
    con.close()
    df['ts'] = to_ts(df['timestamp'])
    return df.set_index('ts')['fr_close']


def funding_8h_grid(funding_h: pd.Series) -> pd.Series:
    """Handling B: the whole history on the settlement grid — keep only rows
    stamped 00:00 / 08:00 / 16:00 UTC (pre-cutover: the hourly predicted row at
    the settlement stamp; post-cutover: the stored settlements)."""
    idx = funding_h.index
    keep = (idx.minute == 0) & (idx.second == 0) & np.isin(idx.hour, [0, 8, 16])
    return funding_h[keep]


# ─── Ledgers (call the June functions) ──────────────────────────────────────

def classify(r: float) -> str:
    """June regime classification (NaN falls into flat_30d, as in June)."""
    return ('bear_30d' if r < -BULL_THRESHOLD
            else 'bull_30d' if r > BULL_THRESHOLD
            else 'flat_30d')


def oi_ledger(df: pd.DataFrame) -> pd.DataFrame:
    """Rule 1 fires + replay (stop 2 % / target 3 % / TIF 48 bars / 18 bp),
    pooled across regimes; the regime columns let callers gate."""
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    ret30 = df['ret_30d'].values
    ret30b = df['ret_30d_backonly'].values
    oi4 = df['oi_chg_4h'].values
    px4 = df['px_chg_4h'].values
    ts = df.index
    n = len(df)
    rows = []
    for i in identify_long_flush_events(df):
        r = oi_replay(highs, lows, closes, entry_idx=i,
                      stop_pct=OI_STOP_PCT, target_pct=OI_TARGET_PCT,
                      tif_bars=OI_TIF_H)
        if r is None:
            continue
        rows.append({
            'ts': ts[i], 'idx': int(i), 'entry': float(closes[i]),
            'oi_chg_4h': float(oi4[i]), 'px_chg_4h': float(px4[i]),
            'ret_30d': float(ret30[i]),
            'regime': classify(ret30[i]),
            'ret_30d_backonly': float(ret30b[i]),
            'regime_backonly': classify(ret30b[i]),
            'r_outcome': float(r['r_outcome']), 'exit_kind': r['exit_kind'],
            'window_span_h': ((ts[i] - ts[i - 4]).total_seconds() / 3600.0
                              if i >= 4 else np.nan),
            'resolved': bool(i + OI_TIF_H <= n - 1),
        })
    cols = ['ts', 'idx', 'entry', 'oi_chg_4h', 'px_chg_4h', 'ret_30d', 'regime',
            'ret_30d_backonly', 'regime_backonly', 'r_outcome', 'exit_kind',
            'window_span_h', 'resolved']
    return pd.DataFrame(rows, columns=cols)


def fcd_ledger(df_15m: pd.DataFrame, funding_h: pd.Series, handling: str) -> pd.DataFrame:
    """Rule 2 fires + replay with the frozen WINNING combo (long only)."""
    dff = attach_features(df_15m, funding_h, FCD_WINDOW_BARS)
    idxs = generate_triggers(dff, direction='long',
                             funding_z_threshold=WINNING['funding_z_threshold'],
                             cvd_z_threshold=WINNING['cvd_z_threshold'],
                             cvd_sustain_bars=WINNING['cvd_sustain_bars'],
                             cooldown_bars=WINNING['cooldown_bars'])
    n = len(dff)
    rows = []
    for i in idxs:
        r = replay_trigger(dff, i, direction='long', atr_mult=ATR_MULT,
                           target_R=TARGET_R, tif_bars=FCD_TIF_BARS)
        if r is None:
            continue
        r['resolved'] = bool(i + FCD_TIF_BARS <= n - 1)
        r['cutover_mixed'] = bool(FUNDING_CUTOVER <= r['ts'] < CUTOVER_MIXED_END)
        r['handling'] = handling
        rows.append(r)
    cols = ['ts', 'direction', 'entry', 'atr', 'risk', 'funding_z', 'cvd_z',
            'r_outcome', 'exit_kind', 'resolved', 'cutover_mixed', 'handling']
    rep = pd.DataFrame(rows, columns=cols)
    return rep.sort_values('ts').reset_index(drop=True)


def regime_of(px: pd.DataFrame, ts: pd.Series, col: str = 'ret_30d') -> pd.Series:
    """ret_30d at each fire timestamp (as-of the hourly grid)."""
    vals = [float(px[col].asof(t)) for t in pd.DatetimeIndex(ts)]
    return pd.Series(vals, index=ts.index)


# ─── Metrics ────────────────────────────────────────────────────────────────

def june_metrics(rep: pd.DataFrame, label: str) -> dict:
    """phase3a_correlation_with_funding_cvd.compute_metrics() verbatim (span =
    first fire -> last fire)."""
    if rep.empty:
        return {'label': label, 'n': 0}
    rep = rep.sort_values('ts')
    cum = rep['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    span_y = ((rep['ts'].max() - rep['ts'].min()).total_seconds()
              / (365.25 * 86400))
    annual_R = float(rep['r_outcome'].mean()) * len(rep) / max(span_y, 0.05)
    return {
        'label': label, 'n': int(len(rep)),
        'meanR': round(float(rep['r_outcome'].mean()), 3),
        'sumR': round(float(rep['r_outcome'].sum()), 2),
        'WR': round(float((rep['r_outcome'] > 0).mean()), 3),
        'maxDD': round(float(dd.min()), 2),
        'annual_R': round(annual_R, 2),
        'MAR': round(annual_R / abs(float(dd.min())), 2) if dd.min() < 0 else 0,
        'span_y': round(span_y, 3),
    }


def window_metrics(rep: pd.DataFrame, label: str, window_end: pd.Timestamp) -> dict:
    """The pre-registered stricter variant: span = first fire -> window end."""
    if rep.empty:
        return {'label': label, 'n': 0}
    rep = rep.sort_values('ts')
    cum = rep['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    span_y = (window_end - rep['ts'].min()).total_seconds() / (365.25 * 86400)
    annual_R = float(rep['r_outcome'].sum()) / max(span_y, 0.05)
    return {
        'label': label, 'n': int(len(rep)),
        'meanR': round(float(rep['r_outcome'].mean()), 3),
        'sumR': round(float(rep['r_outcome'].sum()), 2),
        'WR': round(float((rep['r_outcome'] > 0).mean()), 3),
        'maxDD': round(float(dd.min()), 2),
        'annual_R': round(annual_R, 2),
        'MAR': round(annual_R / abs(float(dd.min())), 2) if dd.min() < 0 else 0,
        'span_y': round(span_y, 3),
        'first_fire': str(rep['ts'].min()), 'window_end': str(window_end),
    }


def basic(rep: pd.DataFrame) -> dict:
    if rep.empty:
        return {'n': 0, 'mean_R': None, 'sum_R': None, 'WR': None, 'maxDD': None,
                'stops': 0, 'targets': 0, 'tifs': 0}
    rep = rep.sort_values('ts')
    cum = rep['r_outcome'].cumsum().values
    dd = cum - np.maximum.accumulate(cum)
    ek = rep['exit_kind']
    return {
        'n': int(len(rep)),
        'mean_R': round(float(rep['r_outcome'].mean()), 4),
        'median_R': round(float(rep['r_outcome'].median()), 4),
        'sum_R': round(float(rep['r_outcome'].sum()), 3),
        'WR': round(float((rep['r_outcome'] > 0).mean()), 3),
        'maxDD': round(float(dd.min()), 3),
        'stops': int((ek == 'stop').sum()),
        'targets': int((ek == 'target').sum()),
        'tifs': int((ek == 'tif').sum()),
        'first': str(rep['ts'].min()), 'last': str(rep['ts'].max()),
    }


def monthly_pearson(a: pd.DataFrame, b: pd.DataFrame) -> tuple[float | None, int]:
    """phase3a monthly P&L correlation on months where at least one sleeve fired."""
    if a.empty or b.empty:
        return None, 0
    am = a.set_index('ts')['r_outcome'].resample('1ME').sum()
    bm = b.set_index('ts')['r_outcome'].resample('1ME').sum()
    al = pd.concat([am.rename('a'), bm.rename('b')], axis=1).fillna(0)
    act = al[(al['a'] != 0) | (al['b'] != 0)]
    if len(act) < 5:
        return None, int(len(act))
    return float(act['a'].corr(act['b'])), int(len(act))


# ─── I/O ────────────────────────────────────────────────────────────────────

def dump_json(obj, path: Path) -> None:
    with Path(path).open('w', encoding='utf-8') as fh:
        json.dump(obj, fh, indent=2, default=str)


def load_json(path: Path):
    with Path(path).open('r', encoding='utf-8') as fh:
        return json.load(fh)


def read_ledger(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if 'ts' in df.columns:
        df['ts'] = pd.to_datetime(df['ts'], utc=True)
    return df


def fmt(x, nd=3) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return 'n/a'
    return f'{x:+.{nd}f}' if isinstance(x, float) else str(x)

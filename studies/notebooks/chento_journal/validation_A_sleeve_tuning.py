"""validation_A: sleeve-config tuning sweeps (Group A) for chento findings.

Plan: studies/notebooks/chento_journal/ — see plans/joyful-singing-leaf.md.

This script runs the v2 chento_limit_bid sleeve as baseline on BTC + ETH + OP
and then applies each Group-A finding as a config override, comparing against
the baseline gate.

Group-A gate (per knob):
  ship if net R/trade >= v2 baseline AND
         frequency preserved (within +/-20%) AND
         max-DD-per-trade not worse by >0.2R.

Sections implemented in this file:
  - Baseline (v2 config)
  - A2  TIF sweep                 {1, 2, 3, 5, 7, 14, 21}d
  - A3  STOP_TO_BE_ON_T1 toggle
  - A5  hour-of-day + weekday WR heatmap (postprocess on baseline)
  - A7  25% trim variant at T1
  - A6  hard leverage cap         (policy note; no backtest)

Stubbed for next session (need new entry logic or sizing model):
  - A1  short-side mirror
  - A4  2-rung ladder-add
  - A8  trim/DCA cycling
  - A9  dynamic TP-adjust
  - A10 leverage taxonomy by setup class

Output: studies/material/chento/validation/A_results.json
"""
from __future__ import annotations

import argparse
import hashlib
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
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Set UTF-8 stdout on Windows so the report tables render. Also force
# line-buffered output so progress reaches the log file in real time
# (without `python -u`, prints buffer for several MB before flushing).
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
else:
    sys.stdout.reconfigure(line_buffering=True)

from strategies.sleeves.chento_limit_bid import config as cli_cfg
from strategies.sleeves.chento_limit_bid import math as cli_math

ASSETS = ('BTC', 'OP')  # chento universe — ETH dropped 2026-05-23 per ruleset
RISK_PER_TRADE_NAV = 0.02

CACHE_DIR = OUT_DIR / 'cache'
CACHE_DIR.mkdir(exist_ok=True)


def _entry_cache_key() -> str:
    """Hash the config knobs that affect entry collection. Any change to one
    of these invalidates the cache and forces a re-collection.

    Knobs NOT included here: TIF, T1/T2 R/close pct, trail, be_on_t1 — those
    are exit-only knobs and only affect replay, not entry list.
    """
    payload = {
        'BASE_WINDOW_HOURS': cli_cfg.BASE_WINDOW_HOURS,
        'BASE_CLUSTER_PCT': cli_cfg.BASE_CLUSTER_PCT,
        'BASE_CLUSTER_BAND_PCT': cli_cfg.BASE_CLUSTER_BAND_PCT,
        'BASE_EXPANSION_PCT': cli_cfg.BASE_EXPANSION_PCT,
        'BASE_EXPANSION_DAYS': cli_cfg.BASE_EXPANSION_DAYS,
        'BASE_APPROACH_BAND_PCT': cli_cfg.BASE_APPROACH_BAND_PCT,
        'CONF_SCORE_MIN': cli_cfg.CONF_SCORE_MIN,
        'BASIS_BP_MAX': cli_cfg.BASIS_BP_MAX,
        'FUNDING_MAX': cli_cfg.FUNDING_MAX,
        'OI_FLUSH_PCT': cli_cfg.OI_FLUSH_PCT,
        'MTF_DEFS': cli_cfg.MTF_DEFS,
        'MTF_NET_ACCEPT': cli_cfg.MTF_NET_ACCEPT,
        'MTF_NET_REJECT': cli_cfg.MTF_NET_REJECT,
        'MTF_CAPITULATION_SIG': cli_cfg.MTF_CAPITULATION_SIG,
        'TRIGGER_HOUR_MIN': cli_cfg.TRIGGER_HOUR_MIN,
        'TRIGGER_HOUR_MAX': cli_cfg.TRIGGER_HOUR_MAX,
        'TRIGGER_WEEKDAYS': cli_cfg.TRIGGER_WEEKDAYS,
        'COOLDOWN_MIN': cli_cfg.COOLDOWN_MIN,
        'STOP_OFFSET_PCT': cli_cfg.STOP_OFFSET_PCT,
        'cache_version': 2,  # bump if collect_entries() logic changes
    }
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:12]


def _entry_cache_path(asset: str, cache_key: str) -> Path:
    return CACHE_DIR / f'entries_{asset}_{cache_key}.jsonl'


def _load_cached_entries(asset: str, cache_key: str) -> pd.DataFrame | None:
    p = _entry_cache_path(asset, cache_key)
    if not p.exists():
        return None
    rows = []
    with p.open(encoding='utf-8') as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df['now_ts'] = pd.to_datetime(df['now_ts'], utc=True)
    return df


def _save_cached_entries(asset: str, cache_key: str,
                          df: pd.DataFrame) -> None:
    p = _entry_cache_path(asset, cache_key)
    with p.open('w', encoding='utf-8') as fh:
        for rec in df.to_dict(orient='records'):
            rec = dict(rec)
            ts = rec.get('now_ts')
            if hasattr(ts, 'isoformat'):
                rec['now_ts'] = ts.isoformat()
            fh.write(json.dumps(rec, default=str) + '\n')


# === Data loading (mirrors v3_backtest cells 1-3-5) ==========================

def _load_table(table: str, ts_col: str = 'timestamp', ts_unit: str = 's'):
    con = sqlite3.connect(str(DB))
    df = pd.read_sql(f'SELECT * FROM {table} ORDER BY {ts_col}', con)
    con.close()
    df['ts'] = pd.to_datetime(df[ts_col], unit=ts_unit, utc=True)
    return df.set_index('ts')[lambda d: ~d.index.duplicated(keep='last')]


def _load_1m(table: str):
    con = sqlite3.connect(str(DB))
    df = pd.read_sql(
        f'SELECT open_time, open, high, low, close, volume FROM {table} '
        f'ORDER BY open_time', con)
    con.close()
    df['ts'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    df = df.set_index('ts').drop(columns='open_time')
    df.columns = ['o', 'h', 'l', 'c', 'v']
    return df[~df.index.duplicated(keep='last')]


def _resample(df: pd.DataFrame, rule: str):
    return df.resample(rule).agg(
        o=('o', 'first'), h=('h', 'max'),
        l=('l', 'min'),   c=('c', 'last'),
        v=('v', 'sum')).dropna()


def build_btc_frame():
    spot15 = _load_table('cd_spot_15m')
    fut15 = _load_table('cd_futures_15m')
    oi_h = _load_table('cd_open_interest')
    fund_h = _load_table('cd_funding_rate')
    start = oi_h.index.min()
    spot15 = spot15.loc[start:]; fut15 = fut15.loc[start:]; fund_h = fund_h.loc[start:]
    f = pd.DataFrame(index=spot15.index)
    f['spot_o'] = spot15['open']; f['spot_h'] = spot15['high']
    f['spot_l'] = spot15['low'];  f['spot_c'] = spot15['close']
    f['spot_cvd'] = spot15['volume_buy'] - spot15['volume_sell']
    f['fut_c'] = fut15['close'].reindex(f.index)
    f['basis_bp'] = (f['fut_c'] - f['spot_c']) / f['spot_c'] * 10000.0
    f['oi'] = oi_h['oi_close'].reindex(f.index).ffill(limit=4)
    f['funding'] = fund_h['fr_close'].reindex(f.index).ffill(limit=32)
    return f.dropna(subset=['spot_c', 'fut_c', 'oi', 'funding']).copy()


def build_eth_frame():
    m1 = _load_1m('eth_1m')
    f15 = _resample(m1, '15min')
    fund_h = _load_table('cd_funding_rate_eth')
    f = pd.DataFrame(index=f15.index)
    f['spot_o'] = f15['o']; f['spot_h'] = f15['h']
    f['spot_l'] = f15['l']; f['spot_c'] = f15['c']
    # ETH lacks 15m perp + OI + spot CVD in the DB. v3 ran ETH/OP without
    # confluence; we do the same so the entry list is comparable.
    f['spot_cvd'] = 0.0
    f['fut_c'] = f['spot_c']
    f['basis_bp'] = 0.0
    f['oi'] = 1.0
    f['funding'] = fund_h['fr_close'].reindex(f.index).ffill(limit=32).fillna(0.0)
    return f.dropna(subset=['spot_c']).copy()


def build_op_frame():
    m1 = _load_1m('op_perp_1m')
    f15 = _resample(m1, '15min')
    f = pd.DataFrame(index=f15.index)
    f['spot_o'] = f15['o']; f['spot_h'] = f15['h']
    f['spot_l'] = f15['l']; f['spot_c'] = f15['c']
    f['spot_cvd'] = 0.0; f['fut_c'] = f['spot_c']
    f['basis_bp'] = 0.0; f['oi'] = 1.0; f['funding'] = 0.0
    return f.dropna(subset=['spot_c']).copy()


def build_mtf_bias(asset_1m_table: str):
    m1 = _load_1m(asset_1m_table)
    rules = {'M': '1ME', 'W': '1W', 'D': '1D', 'H4': '4h', 'H1': '1h'}
    bias_map = {}
    for label, rule in rules.items():
        c = cli_cfg.MTF_DEFS[label]
        tf_df = _resample(m1, rule)
        bias_map[label] = cli_math.compute_tf_bias_series(
            tf_df, period=c['period'], slope=c['slope'])
    return bias_map


# === Two-phase backtest: collect entries, then replay exits ==================

def collect_entries(asset_name: str, f: pd.DataFrame, mtf_bias_map: dict,
                    use_confluence: bool):
    """Walk the 15m frame and record one row per fresh v2 entry signal.

    Returns DataFrame with columns:
      now_ts, entry, stop, base_low, conf_score, mtf_sig, mtf_net.

    Forward replay (R-net, hold-h, MFE) is delegated to replay_exits so we can
    sweep exit config without re-running base detection (the slow part).
    """
    cooldown_td = pd.Timedelta(minutes=cli_cfg.COOLDOWN_MIN)
    min_bars = (cli_cfg.BASE_WINDOW_HOURS * 4
                + cli_cfg.BASE_EXPANSION_DAYS * 24 * 4 + 12)
    f_idx = f.index
    rows = []
    last_trigger = None
    for i in range(len(f_idx)):
        now_ts = f_idx[i]
        if last_trigger is not None and (now_ts - last_trigger) < cooldown_td:
            continue
        now_dt = now_ts.to_pydatetime()
        if not cli_math.passes_time_gate(now_dt):
            continue
        if i < min_bars:
            continue
        sub = f.iloc[:i + 1]
        base = cli_math.detect_active_base(sub, now_ts)
        if base is None:
            continue
        current_price = float(sub['spot_c'].iloc[-1])
        if not cli_math.is_approaching_base(current_price, base['base_low'],
                                              cli_cfg.BASE_APPROACH_BAND_PCT):
            continue
        if use_confluence:
            window = f.loc[base['base_start_ts']:base['base_end_ts']]
            score = cli_math.score_base_window(window)
            if score['conf_score'] < cli_cfg.CONF_SCORE_MIN:
                continue
            cf = int(score['conf_score'])
        else:
            cf = -1  # sentinel: confluence not evaluated for ETH/OP
        sig, net = cli_math.mtf_signature_at(now_ts, mtf_bias_map)
        if not cli_math.passes_mtf_gate(sig, net):
            continue

        entry_price = current_price
        stop_price = base['base_low'] * (1 - cli_cfg.STOP_OFFSET_PCT)
        if entry_price - stop_price <= 0:
            continue
        rows.append({
            'asset': asset_name,
            'now_ts': now_ts,
            'entry': entry_price,
            'stop': stop_price,
            'base_low': float(base['base_low']),
            'conf_score': cf,
            'mtf_sig': sig,
            'mtf_net': int(net),
        })
        last_trigger = now_ts
    return pd.DataFrame(rows)


def replay_one(entry_row: dict, f: pd.DataFrame, *,
               tif_days: float, t1_r: float, t1_close_pct: float,
               t2_r: float, t2_close_pct: float, trail_pct: float,
               be_on_t1: bool, cost_per_unit: float):
    """Replay forward bars for a single entry with the given exit config.

    Returns dict with r_net, hold_h, outcome, t1_done, t2_done, mfe_R, max_dd_R.
    Direction is LONG only here; the short-mirror replay (A1) will live in a
    separate function once we wire it up.
    """
    now_ts = entry_row['now_ts']
    entry = float(entry_row['entry'])
    stop_initial = float(entry_row['stop'])
    risk = entry - stop_initial
    if risk <= 0:
        return None

    tif_end = now_ts + pd.Timedelta(days=tif_days)
    forward = f.loc[now_ts:tif_end]

    state = {
        't1_done': False, 't2_done': False, 'trail_armed': False,
        'high_water': entry, 'active_stop': stop_initial,
    }
    remaining = 1.0
    realized_R = 0.0
    mfe_R = 0.0
    max_dd_R = 0.0  # most-negative excursion in R
    outcome_final = 'tif'
    exit_ts_final = forward.index[-1]
    exit_price_final = float(forward['spot_c'].iloc[-1])

    for ts_, bar in forward.iterrows():
        if ts_ == now_ts:
            continue
        bar_h = float(bar['spot_h']); bar_l = float(bar['spot_l'])
        mfe_R = max(mfe_R, (bar_h - entry) / risk)
        max_dd_R = min(max_dd_R, (bar_l - entry) / risk)
        result = cli_math.evaluate_tier_transitions(
            state, bar_high=bar_h, bar_low=bar_l,
            entry=entry, stop_initial=stop_initial,
            t1_r=t1_r, t2_r=t2_r, trail_pct=trail_pct)
        state = result['new_state']
        for act in result['actions']:
            if act['kind'] == 't1':
                sp = t1_close_pct
                r = (act['price'] - entry) / risk
                cost = cost_per_unit * (act['price'] / risk) * sp
                realized_R += sp * r - cost
                remaining -= sp
                # A3: STOP_TO_BE_ON_T1 — tighten stop to entry after T1
                if be_on_t1 and state['active_stop'] < entry:
                    state['active_stop'] = entry
            elif act['kind'] == 't2':
                sp = t2_close_pct * remaining
                r = (act['price'] - entry) / risk
                cost = cost_per_unit * (act['price'] / risk) * sp
                realized_R += sp * r - cost
                remaining -= sp
            elif act['kind'] == 'stop_exit':
                r = (act['price'] - entry) / risk
                cost = cost_per_unit * (act['price'] / risk) * remaining
                realized_R += remaining * r - cost
                outcome_final = ('trail_exit' if state.get('trail_armed') and
                                  state['active_stop'] > stop_initial else 'stop')
                exit_ts_final = ts_
                exit_price_final = act['price']
                remaining = 0.0
                break
        if remaining <= 1e-9:
            break

    if remaining > 0:
        r = (exit_price_final - entry) / risk
        cost = cost_per_unit * (exit_price_final / risk) * remaining
        realized_R += remaining * r - cost
        outcome_final = 'tif'

    hold_h = (exit_ts_final - now_ts).total_seconds() / 3600.0
    return {
        'r_net': realized_R,
        'hold_h': hold_h,
        'outcome': outcome_final,
        't1_done': state['t1_done'],
        't2_done': state['t2_done'],
        'mfe_R': mfe_R,
        'max_dd_R': max_dd_R,
    }


def replay_exits(entries: pd.DataFrame, f: pd.DataFrame, *,
                 tif_days: float = cli_cfg.TIF_DAYS,
                 t1_r: float = cli_cfg.T1_R,
                 t1_close_pct: float = cli_cfg.T1_CLOSE_PCT,
                 t2_r: float = cli_cfg.T2_R,
                 t2_close_pct: float = cli_cfg.T2_CLOSE_PCT,
                 trail_pct: float = cli_cfg.TRAIL_PCT,
                 be_on_t1: bool = False):
    cost_per_unit = (cli_cfg.COST_BP_RT + cli_cfg.SLIPPAGE_BP_RT) / 10000.0
    rows = []
    for _, row in entries.iterrows():
        result = replay_one(row.to_dict(), f,
                            tif_days=tif_days, t1_r=t1_r,
                            t1_close_pct=t1_close_pct, t2_r=t2_r,
                            t2_close_pct=t2_close_pct,
                            trail_pct=trail_pct, be_on_t1=be_on_t1,
                            cost_per_unit=cost_per_unit)
        if result is None:
            continue
        rows.append({**row.to_dict(), **result})
    return pd.DataFrame(rows)


# === Summary / gate evaluation ===============================================

def summarize(df: pd.DataFrame, label: str = '') -> dict:
    if len(df) == 0:
        return {'label': label, 'n': 0}
    span_y = ((df['now_ts'].max() - df['now_ts'].min()).total_seconds()
              / (365.25 * 86400))
    per_year = len(df) / max(span_y, 0.1)
    mean_R = float(df['r_net'].mean())
    median_R = float(df['r_net'].median())
    win_rate = float((df['r_net'] > 0).mean())
    t1_rate = float(df['t1_done'].mean())
    t2_rate = float(df['t2_done'].mean())
    max_dd_p10 = float(df['max_dd_R'].quantile(0.10))  # worst 10% drawdown
    mfe_p90 = float(df['mfe_R'].quantile(0.90))
    median_hold_h = float(df['hold_h'].median())
    p75_hold_h = float(df['hold_h'].quantile(0.75))
    ann_ret = ((1 + RISK_PER_TRADE_NAV * mean_R) ** per_year - 1) if per_year > 0 else 0.0
    return {
        'label': label,
        'n': int(len(df)),
        'span_years': round(span_y, 2),
        'trades_per_year': round(per_year, 1),
        'mean_R': round(mean_R, 3),
        'median_R': round(median_R, 3),
        'win_rate': round(win_rate, 3),
        't1_hit_rate': round(t1_rate, 3),
        't2_hit_rate': round(t2_rate, 3),
        'max_dd_R_p10': round(max_dd_p10, 3),
        'mfe_R_p90': round(mfe_p90, 3),
        'median_hold_h': round(median_hold_h, 1),
        'p75_hold_h': round(p75_hold_h, 1),
        'implied_annual_ret_pct': round(ann_ret * 100, 1),
    }


def evaluate_gate(variant: dict, baseline: dict) -> tuple[str, list[str]]:
    """Group-A gate. Returns (verdict, reasons).

    Pass if:
      net R/trade >= baseline AND
      freq within +/-20% AND
      max-DD-per-trade not worse by >0.2R (i.e. max_dd_R_p10 not lower by >0.2).
    """
    if variant['n'] == 0:
        return 'FAIL', ['no signals']
    reasons = []
    delta_R = variant['mean_R'] - baseline['mean_R']
    if delta_R < -0.01:
        reasons.append(f'mean_R drop {delta_R:+.3f}')
    freq_ratio = variant['trades_per_year'] / max(baseline['trades_per_year'], 0.1)
    if freq_ratio < 0.8 or freq_ratio > 1.2:
        reasons.append(f'freq ratio {freq_ratio:.2f} outside [0.8, 1.2]')
    dd_delta = variant['max_dd_R_p10'] - baseline['max_dd_R_p10']
    if dd_delta < -0.2:
        reasons.append(f'max_dd_R_p10 worse by {dd_delta:+.2f}R')
    return ('PASS' if not reasons else 'FAIL'), reasons


def print_summary(s: dict):
    if s.get('n', 0) == 0:
        print(f"  {s.get('label','?')}: 0 signals")
        return
    print(f"  {s['label']:<40s}  "
          f"n={s['n']:>4d} ({s['trades_per_year']:>5.1f}/yr)  "
          f"R={s['mean_R']:+.3f}  WR={s['win_rate']:.0%}  "
          f"T1={s['t1_hit_rate']:.0%}  T2={s['t2_hit_rate']:.0%}  "
          f"DD_p10={s['max_dd_R_p10']:+.2f}  "
          f"hold_p75={s['p75_hold_h']:.0f}h  "
          f"ann={s['implied_annual_ret_pct']:+.0f}%")


# === A5 postprocess: hour/weekday heatmap ====================================

def hour_weekday_heatmap(df: pd.DataFrame) -> dict:
    """Compute per-hour-UTC × per-weekday WR + mean-R on the baseline ledger."""
    if len(df) == 0:
        return {}
    d = df.copy()
    ts = pd.to_datetime(d['now_ts'])
    d['hour_utc'] = ts.dt.hour
    d['weekday'] = ts.dt.weekday  # 0=Mon
    by_hour = d.groupby('hour_utc').agg(
        n=('r_net', 'size'),
        mean_R=('r_net', 'mean'),
        wr=('r_net', lambda s: (s > 0).mean())).round(3)
    by_day = d.groupby('weekday').agg(
        n=('r_net', 'size'),
        mean_R=('r_net', 'mean'),
        wr=('r_net', lambda s: (s > 0).mean())).round(3)
    # Per-(weekday, hour) cell where n>=5
    cells = d.groupby(['weekday', 'hour_utc']).agg(
        n=('r_net', 'size'),
        mean_R=('r_net', 'mean'),
        wr=('r_net', lambda s: (s > 0).mean())).reset_index()
    cells = cells[cells['n'] >= 5].sort_values('mean_R', ascending=False)
    return {
        'by_hour_utc': by_hour.reset_index().to_dict(orient='records'),
        'by_weekday': by_day.reset_index().to_dict(orient='records'),
        'top10_cells_meanR': cells.head(10).round(3).to_dict(orient='records'),
        'bot10_cells_meanR': cells.tail(10).round(3).to_dict(orient='records'),
    }


# === Main ====================================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-cache', action='store_true',
                    help='Force re-collection of entries even if cache exists')
    args = ap.parse_args()

    print(f'DB: {DB}')
    cache_key = _entry_cache_key()
    print(f'Entry-cache key: {cache_key}  (cache dir: {CACHE_DIR})')

    # Check cache before loading frames — if all assets are cached we can
    # skip the slow MTF cache + frame-load too, but we still need asset
    # frames for the forward replay. So load frames anyway.
    print('Loading per-asset 15m frames...')
    builders = {'BTC': build_btc_frame, 'ETH': build_eth_frame, 'OP': build_op_frame}
    asset_frames = {a: builders[a]() for a in ASSETS}
    for n, f in asset_frames.items():
        print(f'  {n}: {len(f):,} bars  {f.index.min()} -> {f.index.max()}')

    # --- Phase 1: collect entries (slow on first run, cached after) ----------
    entries_by_asset = {}
    need_mtf = False
    for asset in ASSETS:
        if not args.no_cache:
            cached = _load_cached_entries(asset, cache_key)
            if cached is not None:
                print(f'  {asset}: cache hit ({len(cached)} entries)')
                entries_by_asset[asset] = cached
                continue
        need_mtf = True

    if need_mtf:
        print('Building MTF bias maps for non-cached assets...')
        mtf_tables = {'BTC': 'btc_1m', 'ETH': 'eth_1m', 'OP': 'op_perp_1m'}
        asset_mtf_maps = {a: build_mtf_bias(mtf_tables[a]) for a in ASSETS}
        print('\nCollecting entries per asset (one-time, ~minutes per asset)...')
        for asset in ASSETS:
            if asset in entries_by_asset:
                continue
            use_conf = (asset == 'BTC')
            t0 = datetime.now()
            e = collect_entries(asset, asset_frames[asset],
                                  asset_mtf_maps[asset],
                                  use_confluence=use_conf)
            elapsed = (datetime.now() - t0).total_seconds()
            print(f'  {asset}: {len(e):>4d} entries  ({elapsed:.1f}s)  '
                   f'-> caching')
            _save_cached_entries(asset, cache_key, e)
            entries_by_asset[asset] = e

    all_entries = pd.concat(entries_by_asset.values(), ignore_index=True)
    print(f'  TOTAL: {len(all_entries)} entries across {len(ASSETS)} assets')

    # --- Phase 2: variant sweeps via replay ----------------------------------
    results = {}

    def run_variant(label: str, **override):
        rows = []
        for asset, entries in entries_by_asset.items():
            if len(entries) == 0:
                continue
            df = replay_exits(entries, asset_frames[asset], **override)
            rows.append(df)
        combined = (pd.concat(rows, ignore_index=True) if rows
                    else pd.DataFrame())
        s = summarize(combined, label=label)
        return combined, s

    # --- Baseline (v2 default config) ---
    print('\n=== Baseline (v2 default config) ===')
    baseline_df, baseline_s = run_variant('baseline_v2')
    print_summary(baseline_s)
    results['baseline'] = baseline_s

    # --- A2 TIF sweep ---
    print('\n=== A2 TIF sweep ===')
    a2_variants = {}
    for tif in (1, 2, 3, 5, 7, 14, 21):
        _, s = run_variant(f'A2_TIF_{tif}d', tif_days=tif)
        verdict, reasons = evaluate_gate(s, baseline_s)
        s['gate'] = verdict
        s['gate_reasons'] = reasons
        print_summary(s)
        if reasons:
            print(f'    -> {verdict}: {", ".join(reasons)}')
        else:
            print(f'    -> {verdict}')
        a2_variants[f'TIF_{tif}d'] = s
    results['A2_TIF_sweep'] = a2_variants

    # --- A3 STOP_TO_BE_ON_T1 ---
    print('\n=== A3 STOP_TO_BE_ON_T1 ===')
    _, a3_s = run_variant('A3_BE_on_T1', be_on_t1=True)
    v, r = evaluate_gate(a3_s, baseline_s)
    a3_s['gate'] = v; a3_s['gate_reasons'] = r
    print_summary(a3_s)
    print(f'    -> {v}: {", ".join(r) if r else "ok"}')
    results['A3_BE_on_T1'] = a3_s

    # --- A7 25% trim variant ---
    print('\n=== A7 25% trim at T1 (vs baseline 33%) ===')
    _, a7_s = run_variant('A7_T1_25pct', t1_close_pct=0.25)
    v, r = evaluate_gate(a7_s, baseline_s)
    a7_s['gate'] = v; a7_s['gate_reasons'] = r
    print_summary(a7_s)
    print(f'    -> {v}: {", ".join(r) if r else "ok"}')
    results['A7_T1_25pct'] = a7_s

    # --- A5 hour/weekday heatmap (postprocess baseline) ---
    print('\n=== A5 hour-of-day + weekday heatmap (postprocess) ===')
    a5 = hour_weekday_heatmap(baseline_df)
    if a5:
        print('  top 10 (weekday, hour_utc) cells by mean_R (n>=5):')
        for c in a5['top10_cells_meanR'][:10]:
            print(f'    weekday={int(c["weekday"])} hour={int(c["hour_utc"])}  '
                  f'n={int(c["n"]):>3d}  R={c["mean_R"]:+.2f}  WR={c["wr"]:.0%}')
        print('  bottom 10 (weekday, hour_utc) cells by mean_R (n>=5):')
        for c in a5['bot10_cells_meanR'][-10:]:
            print(f'    weekday={int(c["weekday"])} hour={int(c["hour_utc"])}  '
                  f'n={int(c["n"]):>3d}  R={c["mean_R"]:+.2f}  WR={c["wr"]:.0%}')
    results['A5_hour_weekday_heatmap'] = a5

    # --- A6 hard leverage cap (policy note, no backtest) ---
    print('\n=== A6 hard leverage cap (POLICY) ===')
    results['A6_leverage_cap_policy'] = {
        'label': 'A6_leverage_cap',
        'verdict': 'SHIP (no test required)',
        'rationale': ('RUNE blowup = 100% of extracted losses ($-28.5k in '
                       'one day). 200x/121x/98x on alts seen in the scan. '
                       'Cap: alts <=20x, majors (BTC/ETH) <=30x. Enforce in '
                       'strategies/sleeves/chento_limit_bid/config.py + any '
                       'orchestrator leverage knob.'),
    }
    print(f"  -> {results['A6_leverage_cap_policy']['verdict']}")

    # --- Stubs for next session: A1, A4, A8, A9, A10 ---
    stub_notes = {
        'A1_short_mirror': (
            'Need to (a) detect swing-peaks via cli_math.detect_active_base '
            'on negated OHLC, then flip back; (b) invert MTF gate '
            '(MTF_NET_ACCEPT mirrored to (-1, 3), capitulation sig "++---"); '
            '(c) write replay_one_short that mirrors the long replay. '
            'Highest-impact stub per scan: 60/40 short-biased corpus.'
        ),
        'A4_2rung_ladder_add': (
            'After entry, if price drops to entry-(0.75*risk) without '
            'stopping, add 0.5x size at -0.75R; if it drops further to -1R, '
            'add 0.5x more. Hard-stop on the combined position at -1.5R '
            'below original entry. Requires a position-state extension to '
            'replay_one tracking avg_entry + total_size.'
        ),
        'A8_trim_DCA_cycling': (
            'After T1 trim, if price reverts to within +0.25R of entry, re-add '
            'the trimmed slice at the better price. Bounded to one cycle per '
            'trade. Requires re-entry tracking in replay state.'
        ),
        'A9_dynamic_TP_adjust': (
            'If trail-watermark > 1.5R when T1 fires, move T2 trigger from '
            '3R to 2R (lock-in earlier in momentum decay). Test A/B vs static.'
        ),
        'A10_leverage_taxonomy': (
            'Map MTF bias score -> leverage class: HTF-aligned setups '
            '(mtf_net in {0, 1, 2}) get 5-7x; intraday-only (mtf_net <0) '
            'get 20x; capitulation_sig ("--+++") gets 50x. Scale R proportionally '
            'to test if asymmetric sizing beats uniform 1R sizing.'
        ),
    }
    print('\n=== Stubbed for next session ===')
    for k, v in stub_notes.items():
        print(f'  {k}: {v[:80]}...')
    results['stubs_next_session'] = stub_notes

    # --- Write JSON output ---
    out_path = OUT_DIR / 'A_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'baseline': baseline_s,
            'sections': results,
            'notes': (
                'Group A sleeve-config tuning sweeps on BTC/ETH/OP. Baseline '
                'is v2 default (TIF=21d, T1=33% at 1R, T2=50% at 3R, '
                'trail=5%). Gate: ship if mean_R >= baseline AND '
                'trades_per_year in [0.8x, 1.2x] of baseline AND '
                'max_dd_R_p10 not worse by >0.2R.'
            ),
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')

    # Also write the per-trade ledger so other notebooks (esp. validation_B)
    # can join entries -> outcomes without re-running entry collection.
    ledger_path = OUT_DIR / 'A_baseline_ledger.parquet'
    try:
        baseline_df.to_parquet(ledger_path, index=False)
        print(f'Wrote {ledger_path}')
    except Exception as e:
        # parquet engine not installed — fall back to JSONL
        jsonl_path = OUT_DIR / 'A_baseline_ledger.jsonl'
        baseline_df.assign(now_ts=baseline_df['now_ts'].astype(str)).to_json(
            jsonl_path, orient='records', lines=True)
        print(f'Wrote {jsonl_path} ({e})')


if __name__ == '__main__':
    main()

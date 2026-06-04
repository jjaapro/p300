"""validation_A1: regime-gated direction variants for chento sleeve.

Three sub-variants tested against the v2 baseline (from validation_A_sleeve_tuning):
  A1a — existing long-side sleeve gated by ADX regime ('long' only)
  A1b — new short-side mirror gated by ADX regime ('short' only)
  A1c — combined long+short with per-asset, per-timestamp ADX regime gating

ADX regime is per-asset, computed on daily candles via studies/lib/regime_adx.py,
then forward-filled onto the 15m grid for entry-time lookup.

Why regime-gated, not blind mirror: chento's direction is not always-short, it's
regime-conditional. We don't know his actual regime detector; our closest proxy
is S-003 ADX (close vs EMA(50) at ADX >= 25). This is the simplest principled
gate we have.

Output: studies/material/chento/validation/A1_results.json
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
OUT_DIR.mkdir(parents=True, exist_ok=True)

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
else:
    sys.stdout.reconfigure(line_buffering=True)

# Reuse the validation_A harness for data loaders + replay + cache helpers.
from studies.notebooks.chento_journal.validation_A_sleeve_tuning import (
    ASSETS, RISK_PER_TRADE_NAV,
    build_btc_frame, build_eth_frame, build_op_frame, build_mtf_bias,
    collect_entries, replay_exits, replay_one,
    summarize, evaluate_gate, print_summary,
    _entry_cache_key, _load_cached_entries, _save_cached_entries,
)
from strategies.sleeves.chento_limit_bid import config as cli_cfg
from strategies.sleeves.chento_limit_bid import math as cli_math
from studies.lib.regime_adx import classify_regime


# === Swing-peak detection (mirror of detect_active_base) ====================

def detect_active_peak(df_15m: pd.DataFrame, now_ts: pd.Timestamp,
                       window_hours: int = cli_cfg.BASE_WINDOW_HOURS,
                       cluster_pct: float = cli_cfg.BASE_CLUSTER_PCT,
                       cluster_band_pct: float = cli_cfg.BASE_CLUSTER_BAND_PCT,
                       expansion_pct: float = cli_cfg.BASE_EXPANSION_PCT,
                       expansion_days: int = cli_cfg.BASE_EXPANSION_DAYS,
                       ) -> dict | None:
    """Mirror of detect_active_base for swing-peak (resistance) detection.

    A peak is a window where the rolling max-of-highs is closely clustered
    (≥ cluster_pct of bars have high ≥ max_high * (1 - cluster_band_pct))
    AND a forward downward expansion has occurred (forward low drops by
    ≥ expansion_pct below the peak high).

    Returns dict like detect_active_base but with 'peak_high', 'fwd_low'.
    """
    bars_per_hr = 4
    W = window_hours * bars_per_hr
    K_bars = expansion_days * 24 * bars_per_hr

    if now_ts not in df_15m.index:
        idx_pos = df_15m.index.searchsorted(now_ts, side='right') - 1
    else:
        idx_pos = df_15m.index.get_loc(now_ts)
    if idx_pos < W:
        return None

    max_lookback = min(idx_pos, 7 * 24 * bars_per_hr)
    for k_off in range(0, max_lookback, bars_per_hr):
        confirm_pos = idx_pos - k_off
        if confirm_pos - W - K_bars < 0:
            break
        base_end_pos = confirm_pos - K_bars
        base_start_pos = base_end_pos - W
        if base_start_pos < 0:
            continue
        win_hi = df_15m['spot_h'].iloc[base_start_pos:base_end_pos].values
        p_high = float(win_hi.max())
        within = float((win_hi >= p_high * (1 - cluster_band_pct)).mean())
        if within < cluster_pct:
            continue
        fwd_lo_window = df_15m['spot_l'].iloc[base_end_pos:base_end_pos + K_bars]
        if len(fwd_lo_window) == 0:
            continue
        fwd_low = float(fwd_lo_window.min())
        if fwd_low > p_high * (1 - expansion_pct):
            continue
        hit_mask = fwd_lo_window <= p_high * (1 - expansion_pct)
        if not hit_mask.any():
            continue
        confirm_pos_actual = base_end_pos + int(hit_mask.values.argmax())
        if confirm_pos_actual > idx_pos:
            continue
        return {
            'peak_high': p_high,
            'base_start_ts': df_15m.index[base_start_pos],
            'base_end_ts': df_15m.index[base_end_pos - 1],
            'confirm_ts': df_15m.index[confirm_pos_actual],
            'cluster_frac': within,
            'fwd_low': fwd_low,
        }
    return None


def is_approaching_peak(current_price: float, peak_high: float,
                        approach_band_pct: float) -> bool:
    """Symmetric to is_approaching_base: price within band BELOW the peak."""
    return peak_high * (1 - approach_band_pct) <= current_price <= peak_high


# === Mirrored MTF gate for shorts ==========================================

MTF_NET_ACCEPT_SHORT = (-1, 3)     # mirror of long (-3, 1) — i.e. positive net
MTF_NET_REJECT_SHORT = (-5, -4, -3)  # mirror of long (3, 4, 5)
MTF_CAPITULATION_SIG_SHORT = '++---'  # mirror of '--+++'


def passes_mtf_gate_short(sig: str, net: int) -> bool:
    if sig == MTF_CAPITULATION_SIG_SHORT:
        return True
    if net in MTF_NET_REJECT_SHORT:
        return False
    lo, hi = MTF_NET_ACCEPT_SHORT
    return lo <= net <= hi


# === Per-asset ADX regime classifier =======================================

def _load_daily_for_asset(asset: str) -> pd.DataFrame:
    """Load daily OHLC for asset. BTC/ETH from {asset}_1m resampled.
    OP from op_perp_1m. Returns df with high/low/close columns."""
    src_table = {'BTC': 'btc_1m', 'ETH': 'eth_1m', 'OP': 'op_perp_1m'}[asset]
    con = sqlite3.connect(str(DB))
    df = pd.read_sql(
        f'SELECT open_time, open, high, low, close, volume FROM {src_table} '
        f'ORDER BY open_time', con)
    con.close()
    df['ts'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    df = df.set_index('ts').drop(columns='open_time')
    df.columns = ['open', 'high', 'low', 'close', 'volume']
    daily = df.resample('1D').agg(
        open=('open', 'first'), high=('high', 'max'),
        low=('low', 'min'),     close=('close', 'last'),
        volume=('volume', 'sum')).dropna()
    return daily


def build_regime_series(asset: str) -> pd.Series:
    """Daily ADX regime for asset, indexed by UTC day. Returns ts -> regime."""
    daily = _load_daily_for_asset(asset)
    return classify_regime(daily)


def regime_at(regime_series: pd.Series, ts: pd.Timestamp) -> str | None:
    """ffill lookup of regime at or before ts."""
    if regime_series.empty:
        return None
    pos = regime_series.index.searchsorted(ts, side='right') - 1
    if pos < 0:
        return None
    val = regime_series.iloc[pos]
    return val if isinstance(val, str) else None


# === Short-side entry collection (mirrors collect_entries) =================

def collect_short_entries(asset_name: str, f: pd.DataFrame, mtf_bias_map: dict,
                          use_confluence: bool):
    """Find swing-peak short entries using mirrored gates."""
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
        peak = detect_active_peak(sub, now_ts)
        if peak is None:
            continue
        current_price = float(sub['spot_c'].iloc[-1])
        if not is_approaching_peak(current_price, peak['peak_high'],
                                     cli_cfg.BASE_APPROACH_BAND_PCT):
            continue
        if use_confluence:
            # Mirrored confluence: shorts want POSITIVE basis_bp (premium),
            # POSITIVE funding, OI BUILD (not flush), negative spot CVD.
            window = f.loc[peak['base_start_ts']:peak['base_end_ts']]
            basis_mean = float(window['basis_bp'].mean())
            funding_mean = float(window['funding'].mean())
            oi_start, oi_end = float(window['oi'].iloc[0]), float(window['oi'].iloc[-1])
            oi_build_pct = (oi_end - oi_start) / oi_start if oi_start > 0 else 0.0
            spot_cvd_sum = float(window['spot_cvd'].sum())
            conf_score = 0
            if basis_mean >= -cli_cfg.BASIS_BP_MAX: conf_score += 1  # positive premium
            if funding_mean >= -cli_cfg.FUNDING_MAX: conf_score += 1  # positive funding
            if oi_build_pct >= cli_cfg.OI_FLUSH_PCT: conf_score += 1  # OI build
            if spot_cvd_sum < 0: conf_score += 1                       # spot selling
            if conf_score < cli_cfg.CONF_SCORE_MIN:
                continue
            cf = conf_score
        else:
            cf = -1
        sig, net = cli_math.mtf_signature_at(now_ts, mtf_bias_map)
        if not passes_mtf_gate_short(sig, net):
            continue

        entry_price = current_price
        stop_price = peak['peak_high'] * (1 + cli_cfg.STOP_OFFSET_PCT)
        if stop_price - entry_price <= 0:
            continue
        rows.append({
            'asset': asset_name,
            'now_ts': now_ts,
            'direction': 'short',
            'entry': entry_price,
            'stop': stop_price,
            'peak_high': float(peak['peak_high']),
            'conf_score': cf,
            'mtf_sig': sig,
            'mtf_net': int(net),
        })
        last_trigger = now_ts
    return pd.DataFrame(rows)


# === Short-side forward replay (mirrors replay_one) ========================

def replay_one_short(entry_row: dict, f: pd.DataFrame, *,
                     tif_days: float, t1_r: float, t1_close_pct: float,
                     t2_r: float, t2_close_pct: float, trail_pct: float,
                     be_on_t1: bool, cost_per_unit: float):
    """Forward replay for a SHORT entry. PnL inverted: price decline = profit."""
    now_ts = entry_row['now_ts']
    entry = float(entry_row['entry'])
    stop_initial = float(entry_row['stop'])
    risk = stop_initial - entry  # positive: distance to upside stop
    if risk <= 0:
        return None

    tif_end = now_ts + pd.Timedelta(days=tif_days)
    forward = f.loc[now_ts:tif_end]

    # State machine for shorts: low_water tracks best (most profitable) price
    # encountered; active_stop trails DOWN from initial as we profit
    state = {
        't1_done': False, 't2_done': False, 'trail_armed': False,
        'low_water': entry, 'active_stop': stop_initial,
    }
    remaining = 1.0
    realized_R = 0.0
    mfe_R = 0.0
    max_dd_R = 0.0
    outcome_final = 'tif'
    exit_ts_final = forward.index[-1]
    exit_price_final = float(forward['spot_c'].iloc[-1])

    t1_price = entry - t1_r * risk  # target below entry
    t2_price = entry - t2_r * risk

    for ts_, bar in forward.iterrows():
        if ts_ == now_ts:
            continue
        bar_h = float(bar['spot_h']); bar_l = float(bar['spot_l'])
        # For shorts, MFE = how far price fell below entry (in R units)
        mfe_R = max(mfe_R, (entry - bar_l) / risk)
        max_dd_R = min(max_dd_R, (entry - bar_h) / risk)

        # Track low water
        if bar_l < state['low_water']:
            state['low_water'] = bar_l

        # T1: low touches/breaks t1_price (price fell to target)
        if not state['t1_done'] and bar_l <= t1_price:
            state['t1_done'] = True
            state['trail_armed'] = True
            sp = t1_close_pct
            r = (entry - t1_price) / risk
            cost = cost_per_unit * (t1_price / risk) * sp
            realized_R += sp * r - cost
            remaining -= sp
            if be_on_t1 and state['active_stop'] > entry:
                state['active_stop'] = entry

        # T2: low touches/breaks t2_price
        if state['t1_done'] and not state['t2_done'] and bar_l <= t2_price:
            state['t2_done'] = True
            sp = t2_close_pct * remaining
            r = (entry - t2_price) / risk
            cost = cost_per_unit * (t2_price / risk) * sp
            realized_R += sp * r - cost
            remaining -= sp

        # Trail (after T1): active_stop = max(active_stop, low_water * (1+trail_pct))
        # For shorts trail moves DOWN: protect profit by lowering stop above low_water
        if state['trail_armed']:
            new_trail = state['low_water'] * (1 + trail_pct)
            if new_trail < state['active_stop']:
                state['active_stop'] = new_trail

        # Stop exit: high breaks active_stop (price rose to stop)
        if bar_h >= state['active_stop']:
            stop_px = state['active_stop']
            r = (entry - stop_px) / risk
            cost = cost_per_unit * (stop_px / risk) * remaining
            realized_R += remaining * r - cost
            outcome_final = ('trail_exit' if state.get('trail_armed') and
                              state['active_stop'] < stop_initial else 'stop')
            exit_ts_final = ts_
            exit_price_final = stop_px
            remaining = 0.0
            break

        if remaining <= 1e-9:
            break

    if remaining > 0:
        r = (entry - exit_price_final) / risk
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


def replay_exits_short(entries: pd.DataFrame, f: pd.DataFrame, **kwargs):
    """Replay shorts. Same default knobs as replay_exits."""
    cost_per_unit = (cli_cfg.COST_BP_RT + cli_cfg.SLIPPAGE_BP_RT) / 10000.0
    defaults = {
        'tif_days': cli_cfg.TIF_DAYS, 't1_r': cli_cfg.T1_R,
        't1_close_pct': cli_cfg.T1_CLOSE_PCT, 't2_r': cli_cfg.T2_R,
        't2_close_pct': cli_cfg.T2_CLOSE_PCT,
        'trail_pct': cli_cfg.TRAIL_PCT, 'be_on_t1': False,
    }
    defaults.update(kwargs)
    rows = []
    for _, row in entries.iterrows():
        result = replay_one_short(row.to_dict(), f,
                                    cost_per_unit=cost_per_unit, **defaults)
        if result is None:
            continue
        rows.append({**row.to_dict(), **result})
    return pd.DataFrame(rows)


# === Regime filtering ======================================================

def apply_regime_gate(entries: pd.DataFrame, regime_by_asset: dict,
                      direction: str) -> pd.DataFrame:
    """Drop rows whose asset regime at entry-ts does not match direction.
    direction='long' keeps only entries where regime=='long'.
    """
    if entries.empty:
        return entries
    keep = []
    for idx, row in entries.iterrows():
        rs = regime_by_asset.get(row['asset'])
        if rs is None:
            continue
        regime = regime_at(rs, row['now_ts'])
        if regime == direction:
            keep.append(idx)
    return entries.loc[keep].copy()


# === Main ==================================================================

def main():
    print(f'DB: {DB}')
    cache_key = _entry_cache_key()
    print(f'Entry-cache key: {cache_key}')

    # Load frames (ASSETS is now ('BTC', 'OP') — ETH dropped per chento universe)
    print('Loading per-asset 15m frames...')
    builders = {'BTC': build_btc_frame, 'ETH': build_eth_frame, 'OP': build_op_frame}
    asset_frames = {a: builders[a]() for a in ASSETS}
    for n, f in asset_frames.items():
        print(f'  {n}: {len(f):,} bars  {f.index.min()} -> {f.index.max()}')

    # === Phase 1a: load cached LONG entries (from baseline ledger) ==========
    print('\nLoading cached LONG entries...')
    long_entries = {}
    for asset in ASSETS:
        cached = _load_cached_entries(asset, cache_key)
        if cached is None:
            print(f'  {asset}: NO CACHE — run validation_A first to seed.')
            return
        long_entries[asset] = cached
        print(f'  {asset}: {len(cached):>4d} long entries cached')

    # === Phase 1b: collect SHORT entries (slow, then cache) ================
    short_cache_key = f'short_{cache_key}'
    print('\nCollecting SHORT entries per asset...')
    short_entries = {}
    need_mtf = False
    for asset in ASSETS:
        cached = _load_cached_entries(f'{asset}_SHORT', cache_key)
        if cached is not None:
            print(f'  {asset}: cache hit ({len(cached)} short entries)')
            short_entries[asset] = cached
            continue
        need_mtf = True

    if need_mtf:
        print('Building MTF bias maps...')
        asset_mtf_maps = {
            'BTC': build_mtf_bias('btc_1m'),
            'ETH': build_mtf_bias('eth_1m'),
            'OP':  build_mtf_bias('op_perp_1m'),
        }
        for asset in ASSETS:
            if asset in short_entries:
                continue
            use_conf = (asset == 'BTC')
            t0 = datetime.now()
            e = collect_short_entries(asset, asset_frames[asset],
                                        asset_mtf_maps[asset],
                                        use_confluence=use_conf)
            elapsed = (datetime.now() - t0).total_seconds()
            print(f'  {asset}: {len(e):>4d} short entries  ({elapsed:.1f}s)  -> caching')
            _save_cached_entries(f'{asset}_SHORT', cache_key, e)
            short_entries[asset] = e

    # === Phase 2: build per-asset ADX regime series ========================
    print('\nBuilding per-asset ADX regime series (daily)...')
    regime_by_asset = {}
    for asset in ASSETS:
        rs = build_regime_series(asset)
        n_long = (rs == 'long').sum()
        n_short = (rs == 'short').sum()
        n_range = (rs == 'range').sum()
        n_none = rs.isna().sum() + (rs.apply(lambda v: v is None)).sum()
        print(f'  {asset}: {len(rs)} days  long={n_long}  short={n_short}  '
               f'range={n_range}  gap={n_none}')
        regime_by_asset[asset] = rs

    # === Phase 3: baseline + three A1 variants ============================
    cost_per_unit = (cli_cfg.COST_BP_RT + cli_cfg.SLIPPAGE_BP_RT) / 10000.0
    results = {}

    def run_long_variant(label: str, entries_by_asset: dict):
        rows = []
        for asset, entries in entries_by_asset.items():
            if len(entries) == 0:
                continue
            df = replay_exits(entries, asset_frames[asset])
            rows.append(df)
        combined = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
        return combined, summarize(combined, label=label)

    def run_short_variant(label: str, entries_by_asset: dict):
        rows = []
        for asset, entries in entries_by_asset.items():
            if len(entries) == 0:
                continue
            df = replay_exits_short(entries, asset_frames[asset])
            rows.append(df)
        combined = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
        return combined, summarize(combined, label=label)

    # Baseline (long, no regime gate) — for reference
    print('\n=== Baseline v2 (long, no regime gate) ===')
    base_df, base_s = run_long_variant('baseline_v2_long', long_entries)
    print_summary(base_s)
    results['baseline_long'] = base_s

    # Baseline (short, no regime gate) — for reference
    print('\n=== Baseline short (no regime gate) ===')
    base_short_df, base_short_s = run_short_variant('baseline_short', short_entries)
    print_summary(base_short_s)
    results['baseline_short'] = base_short_s

    # A1a: long + regime gate
    print('\n=== A1a: LONG entries gated by ADX regime=\'long\' ===')
    long_gated = {a: apply_regime_gate(e, regime_by_asset, 'long')
                   for a, e in long_entries.items()}
    for a, e in long_gated.items():
        kept = len(e); total = len(long_entries[a])
        print(f'  {a}: kept {kept}/{total} entries')
    a1a_df, a1a_s = run_long_variant('A1a_long_regime_gated', long_gated)
    print_summary(a1a_s)
    v, r = evaluate_gate(a1a_s, base_s)
    a1a_s['gate'] = v; a1a_s['gate_reasons'] = r
    print(f'  -> {v}: {", ".join(r) if r else "ok"}')
    results['A1a_long_regime_gated'] = a1a_s

    # A1b: short + regime gate
    print('\n=== A1b: SHORT entries gated by ADX regime=\'short\' ===')
    short_gated = {a: apply_regime_gate(e, regime_by_asset, 'short')
                    for a, e in short_entries.items()}
    for a, e in short_gated.items():
        kept = len(e); total = len(short_entries[a])
        print(f'  {a}: kept {kept}/{total} entries')
    a1b_df, a1b_s = run_short_variant('A1b_short_regime_gated', short_gated)
    print_summary(a1b_s)
    v, r = evaluate_gate(a1b_s, base_s)
    a1b_s['gate'] = v; a1b_s['gate_reasons'] = r
    print(f'  -> {v}: {", ".join(r) if r else "ok"}')
    results['A1b_short_regime_gated'] = a1b_s

    # A1c: combined long + short (both regime-gated)
    print('\n=== A1c: combined long+short, regime-gated ===')
    a1c_combined = pd.concat([a1a_df.assign(direction='long'),
                                a1b_df.assign(direction='short')],
                               ignore_index=True) if (len(a1a_df) or len(a1b_df)) else pd.DataFrame()
    a1c_s = summarize(a1c_combined, label='A1c_combined_regime_gated')
    print_summary(a1c_s)
    v, r = evaluate_gate(a1c_s, base_s)
    a1c_s['gate'] = v; a1c_s['gate_reasons'] = r
    print(f'  -> {v}: {", ".join(r) if r else "ok"}')
    results['A1c_combined_regime_gated'] = a1c_s

    # === Write output ======================================================
    out_path = OUT_DIR / 'A1_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'baseline_long': base_s,
            'baseline_short': base_short_s,
            'A1a_long_regime_gated': results['A1a_long_regime_gated'],
            'A1b_short_regime_gated': results['A1b_short_regime_gated'],
            'A1c_combined_regime_gated': results['A1c_combined_regime_gated'],
            'regime_params': {
                'adx_period': 14, 'adx_high': 25, 'adx_low': 20,
                'ema_period': 50, 'tf': 'daily',
                'source': 'studies/lib/regime_adx.py',
            },
            'notes': (
                'A1 tests regime-conditional direction selection. Long entries '
                'fire only when daily ADX regime is "long" (ADX>=25, close>EMA50); '
                'short entries fire only when "short". Range/gap-zone bars produce '
                'no entries. Gate vs baseline v2 long-only.'
            ),
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()

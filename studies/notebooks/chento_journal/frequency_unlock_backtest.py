"""Frequency-unlock experiment: sweep across gate relaxations to find
the maximum-frequency variant that preserves edge.

Goal: turn the 5/year bot into the 100/year bot, see if per-trade R holds.
Tests BTC alone (we have full confluence data).
"""
import sys, sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

sys.path.insert(0, 'c:/Source/Repos/p300')
sys.stdout.reconfigure(encoding='utf-8')

DB = Path('c:/Source/Repos/p300/data/databases/prod.db')
from strategies.sleeves.chento_limit_bid import math as cli_math
from strategies.sleeves.chento_limit_bid import config as cli_cfg


def _load_table(table, ts_col='timestamp', ts_unit='s'):
    con = sqlite3.connect(str(DB))
    df = pd.read_sql(f'SELECT * FROM {table} ORDER BY {ts_col}', con)
    con.close()
    df['ts'] = pd.to_datetime(df[ts_col], unit=ts_unit, utc=True)
    return df.set_index('ts')[lambda d: ~d.index.duplicated(keep='last')]


def _load_1m(table):
    con = sqlite3.connect(str(DB))
    df = pd.read_sql(f'SELECT open_time, open, high, low, close, volume FROM {table} ORDER BY open_time', con)
    con.close()
    df['ts'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    df = df.set_index('ts').drop(columns='open_time')
    df.columns = ['o','h','l','c','v']
    return df[~df.index.duplicated(keep='last')]


def _resample(df, rule):
    return df.resample(rule).agg(o=('o','first'), h=('h','max'),
                                  l=('l','min'),   c=('c','last'),
                                  v=('v','sum')).dropna()


print('Loading BTC enriched 15m frame...')
spot15 = _load_table('cd_spot_15m')
fut15  = _load_table('cd_futures_15m')
oi_h   = _load_table('cd_open_interest')
fund_h = _load_table('cd_funding_rate')
start = oi_h.index.min()
spot15 = spot15.loc[start:]; fut15 = fut15.loc[start:]; fund_h = fund_h.loc[start:]
f = pd.DataFrame(index=spot15.index)
f['spot_o']=spot15['open']; f['spot_h']=spot15['high']
f['spot_l']=spot15['low']; f['spot_c']=spot15['close']
f['spot_cvd']=spot15['volume_buy']-spot15['volume_sell']
f['fut_c']=fut15['close'].reindex(f.index)
f['basis_bp']=(f['fut_c']-f['spot_c'])/f['spot_c']*10000.0
f['oi']=oi_h['oi_close'].reindex(f.index).ffill(limit=4)
f['funding']=fund_h['fr_close'].reindex(f.index).ffill(limit=32)
f = f.dropna(subset=['spot_c','fut_c','oi','funding']).copy()
print(f'  {len(f):,} 15m bars')

print('Building MTF bias map...')
m1 = _load_1m('btc_1m')
mtf = {}
for label, rule in {'M':'1ME','W':'1W','D':'1D','H4':'4h','H1':'1h'}.items():
    cfg = cli_cfg.MTF_DEFS[label]
    tf = _resample(m1, rule)
    mtf[label] = cli_math.compute_tf_bias_series(tf, period=cfg['period'], slope=cfg['slope'])


def backtest(name, *, cooldown_min, hour_min, hour_max, weekdays,
              max_concurrent=1):
    """Run backtest with specified gate relaxations."""
    cooldown_td = pd.Timedelta(minutes=cooldown_min)
    cost_per_unit = (cli_cfg.COST_BP_RT + cli_cfg.SLIPPAGE_BP_RT) / 10000.0
    min_bars = cli_cfg.BASE_WINDOW_HOURS * 4 + cli_cfg.BASE_EXPANSION_DAYS * 24 * 4 + 12

    signals = []
    open_trades = []  # list of dicts: {entry_ts, exit_ts, ...}
    f_idx = f.index
    last_trigger = None

    for i in range(len(f_idx)):
        now_ts = f_idx[i]
        now_dt = now_ts.to_pydatetime()
        # Drop closed trades from open list
        open_trades = [t for t in open_trades if now_ts < t['tif_end']]
        # Cooldown (only if we have a recent trigger)
        if cooldown_min > 0 and last_trigger is not None and (now_ts - last_trigger) < cooldown_td:
            continue
        # Time-of-day
        if hour_min is not None and not (hour_min <= now_dt.hour <= hour_max):
            continue
        # Day-of-week
        if weekdays is not None and now_dt.weekday() not in weekdays:
            continue
        # Concurrency limit
        if len(open_trades) >= max_concurrent:
            continue
        if i < min_bars: continue
        sub = f.iloc[:i+1]
        base = cli_math.detect_active_base(sub, now_ts)
        if base is None: continue
        current_price = float(sub['spot_c'].iloc[-1])
        if not cli_math.is_approaching_base(current_price, base['base_low'],
                                              cli_cfg.BASE_APPROACH_BAND_PCT): continue
        window = f.loc[base['base_start_ts']:base['base_end_ts']]
        score = cli_math.score_base_window(window)
        if score['conf_score'] < cli_cfg.CONF_SCORE_MIN: continue
        sig, net = cli_math.mtf_signature_at(now_ts, mtf)
        if not cli_math.passes_mtf_gate(sig, net): continue

        entry_price = current_price
        stop_price = base['base_low'] * (1 - cli_cfg.STOP_OFFSET_PCT)
        risk = entry_price - stop_price
        if risk <= 0: continue
        tif_end = now_ts + pd.Timedelta(days=cli_cfg.TIF_DAYS)
        forward = f.loc[now_ts:tif_end]
        # v2 tier state machine
        state = {"t1_done":False, "t2_done":False, "trail_armed":False,
                 "high_water":entry_price, "active_stop":stop_price}
        remaining = 1.0; realized_R = 0.0; mfe_R = 0.0
        exit_ts_final = forward.index[-1]
        exit_price_final = float(forward['spot_c'].iloc[-1])
        outcome_final = 'tif'
        for ts_, bar in forward.iterrows():
            if ts_ == now_ts: continue
            bh,bl = float(bar['spot_h']), float(bar['spot_l'])
            mfe_R = max(mfe_R, (bh - entry_price) / risk)
            result = cli_math.evaluate_tier_transitions(
                state, bar_high=bh, bar_low=bl, entry=entry_price, stop_initial=stop_price,
                t1_r=cli_cfg.T1_R, t2_r=cli_cfg.T2_R, trail_pct=cli_cfg.TRAIL_PCT)
            state = result['new_state']
            for act in result['actions']:
                if act['kind']=='t1':
                    sp=cli_cfg.T1_CLOSE_PCT
                    r=(act['price']-entry_price)/risk
                    cost=cost_per_unit*(act['price']/risk)*sp
                    realized_R += sp*r - cost; remaining -= sp
                elif act['kind']=='t2':
                    sp=cli_cfg.T2_CLOSE_PCT*remaining
                    r=(act['price']-entry_price)/risk
                    cost=cost_per_unit*(act['price']/risk)*sp
                    realized_R += sp*r - cost; remaining -= sp
                elif act['kind']=='stop_exit':
                    r=(act['price']-entry_price)/risk
                    cost=cost_per_unit*(act['price']/risk)*remaining
                    realized_R += remaining*r - cost
                    outcome_final = ('trail_exit' if state.get('trail_armed') and
                                       state['active_stop']>stop_price else 'stop')
                    exit_ts_final=ts_; exit_price_final=act['price']
                    remaining=0.0; break
            if remaining<=1e-9: break
        if remaining>0:
            r=(exit_price_final-entry_price)/risk
            cost=cost_per_unit*(exit_price_final/risk)*remaining
            realized_R += remaining*r - cost
            outcome_final='tif'
        open_trades.append({'tif_end': exit_ts_final})
        signals.append({
            'now_ts': now_ts, 'r_net': realized_R, 'mfe_R': mfe_R,
            'outcome': outcome_final, 't1_done': state['t1_done'],
            't2_done': state['t2_done'], 'mtf_net': net,
        })
        last_trigger = now_ts

    df_sig = pd.DataFrame(signals)
    n = len(df_sig)
    if n == 0:
        return {'name': name, 'n': 0, 'per_year': 0, 'mean_R': 0,
                'win_rate': 0, 'implied_ann': 0}
    span_y = (df_sig['now_ts'].max() - df_sig['now_ts'].min()).total_seconds() / (365.25*86400)
    span_y = max(span_y, 0.1)
    per_year = n / span_y
    mean_R = df_sig['r_net'].mean()
    win_rate = (df_sig['r_net']>0).mean()
    per_trade_ret = 0.02 * mean_R
    implied_ann = (1+per_trade_ret)**per_year - 1
    return {
        'name': name, 'n': n, 'per_year': per_year,
        'mean_R': mean_R, 'win_rate': win_rate,
        't1_rate': df_sig['t1_done'].mean(), 't2_rate': df_sig['t2_done'].mean(),
        'implied_ann': implied_ann,
    }


# Run experiments
print('\n=== Gate sensitivity experiments ===')
exps = [
    dict(name='v2_baseline', cooldown_min=1440, hour_min=6, hour_max=22, weekdays={0,1,2,3,4,6}),
    dict(name='drop_time', cooldown_min=1440, hour_min=None, hour_max=None, weekdays={0,1,2,3,4,6}),
    dict(name='drop_day', cooldown_min=1440, hour_min=6, hour_max=22, weekdays=None),
    dict(name='cool_6h', cooldown_min=360, hour_min=6, hour_max=22, weekdays={0,1,2,3,4,6}),
    dict(name='cool_0', cooldown_min=0, hour_min=6, hour_max=22, weekdays={0,1,2,3,4,6}),
    dict(name='2_concurrent', cooldown_min=360, hour_min=6, hour_max=22, weekdays={0,1,2,3,4,6}, max_concurrent=2),
    dict(name='unlocked', cooldown_min=0, hour_min=None, hour_max=None, weekdays=None, max_concurrent=3),
]
results = []
for e in exps:
    r = backtest(**e)
    results.append(r)
    print(f'\n  {r["name"]:>15s}: n={r["n"]:>3d} ({r["per_year"]:>5.1f}/yr)  '
          f'mean R {r["mean_R"]:+.3f}  WR {r["win_rate"]:.1%}  '
          f'implied {r["implied_ann"]*100:+5.1f}%/yr')

print('\n=== Summary ===')
print(f'{"variant":>15s} {"n":>5s} {"/yr":>7s} {"R":>7s} {"WR":>6s} {"ann":>8s}')
for r in results:
    print(f'{r["name"]:>15s} {r["n"]:>5d} {r["per_year"]:>7.1f} {r["mean_R"]:>+7.3f} '
          f'{r["win_rate"]:>5.0%} {r["implied_ann"]*100:>+7.1f}%')

"""Cost-sensitivity re-run of the LVN Phase 4 fade (NOT a new study): same trade generation as
studies/notebooks/fvg_magnet/lvn_phase4_backtest.py, gross R kept separate from cost so several
cost models can be evaluated on one trade list."""
import sys, numpy as np, pandas as pd
from pathlib import Path as _P; _R=_P(__file__).resolve().parents[3]; sys.path.insert(0, str(_R))
import studies.notebooks.fvg_magnet.lvn_phase4_backtest as P
from studies.notebooks.fvg_magnet.lvn_phase3_directionality import load_btc_15m, compute_vp_at, identify_lvn_zones, VP_WINDOW_BARS, REBALANCE_BARS

df = load_btc_15m()
typ = ((df['high']+df['low'])/2).values; vol = df['volume'].values
highs=df['high'].values; lows=df['low'].values; closes=df['close'].values; n=len(df); ts=df.index
daily_close = df['close'].resample('1D').last()
ret30 = daily_close.pct_change(30).reindex(df.index, method='ffill').values
zones_by_R=[]
for idx_end in range(VP_WINDOW_BARS, n, REBALANCE_BARS):
    vp=compute_vp_at(typ, vol, idx_end)
    if vp is None: continue
    zones_by_R.append((idx_end, identify_lvn_zones(*vp)))

def gen(wmin, wmax, pen_min, tgt_pen, tif):
    P.COST_BP = 0.0; P.TARGET_PENETRATION_FRAC = tgt_pen; P.MIN_ENTRY_PENETRATION_FRAC = pen_min
    trades=[]; last={}
    for ri,(idx_R,zones) in enumerate(zones_by_R):
        next_R = zones_by_R[ri+1][0] if ri+1<len(zones_by_R) else n
        end_walk=min(idx_R+REBALANCE_BARS, next_R, n-1)
        for zl,zh in zones:
            if zh<=zl: continue
            w=(zh-zl)/closes[idx_R]
            if not (wmin<=w<=wmax): continue
            key=(round(zl,2),round(zh,2)); lt=last.get(key,-10**9)
            for i in range(idx_R+1,end_walk):
                if i-lt<P.COOLDOWN_BARS: continue
                pc,cc=closes[i-1],closes[i]
                if zl<=pc<=zh or not (zl<=cc<=zh): continue
                if pc<zl: d='short'; pen=(cc-zl)/(zh-zl)
                else: d='long'; pen=(zh-cc)/(zh-zl)
                if pen<pen_min: continue
                r=P.replay_trade(highs,lows,closes,entry_idx=i,direction=d,zone_low=zl,zone_high=zh,width=zh-zl,tif_bars=tif)
                if r is None: continue
                reg='bear_30d' if ret30[i]<-0.10 else 'bull_30d' if ret30[i]>0.10 else 'flat_30d'
                trades.append(dict(ts=ts[i],dir=d,reg=reg,grossR=r['r_outcome'],exit=r['exit_kind'],stop_bp=r['risk']/r['entry']*1e4,rr=r['reward']/r['risk']))
                last[key]=i
    return pd.DataFrame(trades)

def evaluate(t, label):
    if t.empty: print(label,'empty'); return
    out=[]
    for cm in ('taker18','maker_blend','zero'):
        if cm=='taker18': cost=18.0
        elif cm=='zero': cost=0.0
        else: cost=np.where(t.exit=='stop', 2+9, np.where(t.exit=='target', 2+2, 2+9))  # entry maker 2bp; TP maker 2bp; stop/TIF taker 9bp
        net=t.grossR - cost/t.stop_bp
        cum=net.cumsum(); dd=(cum-cum.cummax()).min(); yrs=(t.ts.max()-t.ts.min()).days/365.25
        oos=net[t.ts>P.IS_END]
        out.append(f"{cm}: meanR={net.mean():+.3f} annR={net.sum()/yrs:+.1f} maxDD={dd:+.1f} MAR={(net.sum()/yrs)/abs(dd) if dd<0 else 0:.2f} OOS={oos.mean():+.3f}(n={len(oos)})")
    print(f"{label}: n={len(t)} ({len(t)/((t.ts.max()-t.ts.min()).days/365.25):.0f}/yr) WR_gross={(t.grossR>0).mean():.0%} tgt={ (t.exit=='target').mean():.0%} stop={(t.exit=='stop').mean():.0%} med_stop_bp={t.stop_bp.median():.0f} med_RR={t.rr.median():.2f} grossR={t.grossR.mean():+.3f}")
    for o in out: print("     ",o)

for label,args in (("1-2% width, pen>=30%, target +25%w, TIF 24h",(0.01,0.02,0.30,0.25,96)),
                   ("1-2% width, pen>=30%, target +50%w, TIF 24h",(0.01,0.02,0.30,0.50,96)),
                   ("1-2% width, pen>=50%, target +25%w, TIF 24h",(0.01,0.02,0.50,0.25,96)),
                   ("2-5% width, pen>=30%, target +25%w, TIF 72h",(0.02,0.05,0.30,0.25,288)),
                   ("0.5-1% width, pen>=30%, target +25%w, TIF 24h",(0.005,0.01,0.30,0.25,96))):
    t=gen(*args); evaluate(t,label)
    if not t.empty:
        for reg in ('bear_30d','flat_30d','bull_30d'):
            s=t[t.reg==reg]
            if len(s)>20:
                mb=(s.grossR - np.where(s.exit=='target',4,11)/s.stop_bp).mean()
                print(f"        {reg}: n={len(s)} grossR={s.grossR.mean():+.3f} maker_blend={mb:+.3f}")

"""ADX improvement experiments — each lever isolated, then combined.

Reports full-period + in-sample (2018-2022) + out-of-sample (2023-2026) so we
can tell a real edge from an overfit. ~34 trades total, so OOS is ~17 trades —
small. We lean on (a) consistent IS/OOS sign and (b) causal stories, not on
squeezing the last basis point.

Every row also prints the LONG/SHORT cum split and SL-hit count, because the
problem characterization showed losers concentrate in shorts.
"""
from __future__ import annotations
from harness import run, load_btc_daily, fmt, ema, di_series, ADX_PERIOD

c = load_btc_daily()
closes = [x["close"] for x in c]
e150 = ema(closes, 150)
e200 = ema(closes, 200)
dt_idx = {x["dt"]: i for i, x in enumerate(c)}
pdi, mdi = di_series(c, ADX_PERIOD)

IS_START, OOS_SPLIT, END = "2018-01-01", "2023-01-01", None

def splitline(label, **kw):
    full = run(c, IS_START, **kw)
    is_ = run(c, IS_START, **kw); is_ = _clip(is_, IS_START, OOS_SPLIT)
    oos = run(c, IS_START, **kw); oos = _clip(oos, OOS_SPLIT, "2027")
    # direction split on full
    L = [t for t in full["trades"] if t["dir"]=="long"]
    S = [t for t in full["trades"] if t["dir"]=="short"]
    def cum(ts):
        x=1.0
        for t in ts: x*=(1+t["net_pct"]/100)
        return (x-1)*100
    sl = sum(1 for t in full["trades"] if t["reason"]=="SL")
    print(fmt(full, label))
    print(f"      IS:  {fmt(is_,'')[28:]}")
    print(f"      OOS: {fmt(oos,'')[28:]}")
    print(f"      Lcum={cum(L):+.0f}% (n={len(L)})  Scum={cum(S):+.0f}% (n={len(S)})  SLhits={sl}")

def _clip(m, start, end):
    # recompute metrics on the subset of trades whose entry is in [start,end)
    from harness import _metrics
    ts=[t for t in m["trades"] if start <= t["entry_dt"][:10] < end]
    return _metrics(ts, c, start)

# ── gates ─────────────────────────────────────────────────────────────────────
def short_filter_e150(ctx):
    # block shorts when price is above EMA150 (i.e. only short confirmed downtrends)
    if ctx["new_dir"]=="short":
        i=ctx["i"]; return ctx["close"] < e150[i]
    return True
def short_filter_e200(ctx):
    if ctx["new_dir"]=="short":
        i=ctx["i"]; return ctx["close"] < e200[i]
    return True
def both_filter_e150(ctx):
    # symmetric: long>EMA150 already handled by trend_ema_len; this only adds short<EMA150
    return short_filter_e150(ctx)

print("="*100)
print("BASELINE = live S-003 (ema50 dir, LONG-only EMA150 filter, ADX<20 exit, 10% SL)")
print("="*100)
splitline("baseline")

print("\n--- LEVER 1: add SHORT trend filter (price-only; funding overlay separate) ---")
splitline("+short<EMA150",  entry_gate=short_filter_e150)
splitline("+short<EMA200",  entry_gate=short_filter_e200)

print("\n--- LEVER 2: direction rule (confirm DI is not better) ---")
splitline("dir=DI",          direction="di")
splitline("dir=DI&EMA50",    direction="di_and_ema50")

print("\n--- LEVER 3: exit method for smaller DD ---")
for m in (2.5, 3.0, 4.0):
    splitline(f"ATR_trail x{m}",  exit_mode="atr_trail", atr_mult=m)
splitline("ADX-or-ATRx3",        exit_mode="adx_or_atr", atr_mult=3.0)
splitline("ADX-or-ATRx4",        exit_mode="adx_or_atr", atr_mult=4.0)

print("\n--- LEVER 4: re-arm threshold (catch trend continuations -> fewer missed) ---")
splitline("rearm<22",   rearm_thresh=22.0)
splitline("rearm<23",   rearm_thresh=23.0)

print("\n--- LEVER 5: tighter SL ---")
splitline("SL=8%",   sl_pct=8.0)
splitline("SL=12%",  sl_pct=12.0)

print("\n--- COMBOS ---")
splitline("short<E150 + ATRx4",     entry_gate=short_filter_e150, exit_mode="adx_or_atr", atr_mult=4.0)
splitline("short<E150 + rearm<22",  entry_gate=short_filter_e150, rearm_thresh=22.0)
splitline("short<E150 + rearm22 + ATRx4",
          entry_gate=short_filter_e150, rearm_thresh=22.0, exit_mode="adx_or_atr", atr_mult=4.0)

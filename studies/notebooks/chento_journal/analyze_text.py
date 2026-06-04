"""Cheap text-only analysis of chento's journal.

Answers without any image reads:
1. Time-of-day distribution (UTC hour of posts)
2. Day-of-week distribution
3. Asset mentions in text captions
4. Posting cadence (gaps, streaks)
5. SMC/ICT terminology evolution by quarter
6. Cluster (=trade lifecycle) duration distribution
"""
import json, re, sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

MSGS = Path('c:/Source/Repos/p300/studies/material/chento/messages.jsonl')
records = [json.loads(l) for l in MSGS.read_text(encoding='utf-8').splitlines() if l.strip()]
chento = [r for r in records if r['author_id'] == '978925049945919499']
print(f'chento messages: {len(chento):,}')

# === 1. Time-of-day ===
print('\n' + '='*70)
print('1. POSTING TIME-OF-DAY (UTC hour)')
print('='*70)
hours = Counter()
for r in chento:
    dt = datetime.fromisoformat(r['ts_utc'])
    hours[dt.hour] += 1
print(f'{"hour":>4s} {"count":>6s} bar')
for h in range(24):
    n = hours[h]
    bar = '#' * (n // 5)
    note = ''
    if h == 7: note = '  ← London open (08:00 BST)'
    if h == 13: note = '  ← NY open (09:30 EDT)'
    if h == 14: note = '  ← NY open hour'
    if h == 20: note = '  ← NY close'
    print(f'{h:>3}h {n:>6d} {bar}{note}')

# === 2. Day-of-week ===
print('\n' + '='*70)
print('2. DAY-OF-WEEK')
print('='*70)
dow = Counter()
for r in chento:
    dt = datetime.fromisoformat(r['ts_utc'])
    dow[dt.weekday()] += 1
names = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
for i, name in enumerate(names):
    print(f'  {name}: {dow[i]:>4}  {"#"*(dow[i]//10)}')

# === 3. Asset mentions in text ===
print('\n' + '='*70)
print('3. ASSET MENTIONS IN TEXT (case-insensitive)')
print('='*70)
patterns = {
    'BTC':    re.compile(r'\b(btc|bitcoin)\b', re.I),
    'ETH':    re.compile(r'\b(eth|ethereum|ether)\b', re.I),
    'SOL':    re.compile(r'\b(sol|solana)\b', re.I),
    'OP':     re.compile(r'\b(op|optimism|opusdt)\b', re.I),
    'AVAX':   re.compile(r'\b(avax|avalanche)\b', re.I),
    'DOGE':   re.compile(r'\b(doge|dogecoin)\b', re.I),
    'ARB':    re.compile(r'\b(arb|arbitrum)\b', re.I),
    'WIF':    re.compile(r'\b(wif)\b', re.I),
    'INJ':    re.compile(r'\b(inj|injective)\b', re.I),
    'XRP':    re.compile(r'\b(xrp|ripple)\b', re.I),
    'PEPE':   re.compile(r'\b(pepe)\b', re.I),
    'memecoin':re.compile(r'\b(memecoin|meme coin|memes?)\b', re.I),
}
for name, pat in patterns.items():
    n = sum(1 for r in chento if pat.search(r['text']))
    print(f'  {name:>8s}: {n:>4d} mentions')

# === 4. SMC/ICT terminology by quarter ===
print('\n' + '='*70)
print('4. SMC/ICT VOCABULARY FREQUENCY BY QUARTER')
print('='*70)
smc_terms = {
    'orderblock/OB':    re.compile(r'\b(ob|orderblock|order block|order-block)\b', re.I),
    'FVG':              re.compile(r'\b(fvg|fair value gap|imbalance)\b', re.I),
    'liquidity':        re.compile(r'\b(liquidity|liqui)\b', re.I),
    'sweep':            re.compile(r'\b(sweep|swept|grab|grabbed)\b', re.I),
    'CHOCH/BOS':        re.compile(r'\b(choch|bos|break of structure|change of character)\b', re.I),
    'premium/discount': re.compile(r'\b(premium|discount)\b', re.I),
    'CVD':              re.compile(r'\b(cvd|cum delta|delta)\b', re.I),
    'OI':               re.compile(r'\b(oi|open interest)\b', re.I),
    'liquidation':      re.compile(r'\b(liq|liquidation|liquidat)\b', re.I),
    'funding':          re.compile(r'\b(funding|fr)\b', re.I),
    'DCA':              re.compile(r'\b(dca|dcaed|dca\'d|dollar cost)\b', re.I),
    'leverage':         re.compile(r'\b(lev|leverage|\d+x)\b', re.I),
    'short':            re.compile(r'\b(short|shorting|shorted)\b', re.I),
    'long':             re.compile(r'\b(long|longing|longed)\b', re.I),
    'TP':               re.compile(r'\b(tp|take profit|target)\b', re.I),
    'SL':               re.compile(r'\b(sl|stop loss|stoploss|stop)\b', re.I),
    'breakeven':        re.compile(r'\b(be|breakeven|break even|break-even)\b', re.I),
    'range':            re.compile(r'\b(range|ranging)\b', re.I),
    'trend':            re.compile(r'\b(trend|trending)\b', re.I),
    'NY open':          re.compile(r'\b(ny open|new york open|13:30|14:30 cet)\b', re.I),
    'CPI/news':         re.compile(r'\b(cpi|fomc|nfp|news|fed)\b', re.I),
    'whale/retail':     re.compile(r'\b(whale|retail|smart money)\b', re.I),
    'conviction':       re.compile(r'\b(conviction|convicted)\b', re.I),
}
by_q = defaultdict(Counter)
for r in chento:
    dt = datetime.fromisoformat(r['ts_utc'])
    q = f'{dt.year}Q{(dt.month-1)//3+1}'
    for term, pat in smc_terms.items():
        if pat.search(r['text']):
            by_q[q][term] += 1
quarters = sorted(by_q.keys())
print(f'{"term":>18s}', end='')
for q in quarters:
    print(f' {q[2:]:>5s}', end='')
print()
for term in smc_terms:
    print(f'{term:>18s}', end='')
    for q in quarters:
        n = by_q[q][term]
        print(f' {n if n else "·":>5}', end='')
    print()

# === 5. Cluster duration distribution (proxy for trade hold time) ===
print('\n' + '='*70)
print('5. CLUSTER (≤6h gap) DURATION — trade lifecycle proxy')
print('='*70)
imgs = [r for r in chento if r['attachment_urls']]
imgs.sort(key=lambda r: r['ts_utc'])
clusters = []; cur = []; prev = None
for r in imgs:
    ts = datetime.fromisoformat(r['ts_utc'])
    if prev is None or (ts - prev).total_seconds()/3600 <= 6:
        cur.append(r)
    else:
        clusters.append(cur); cur = [r]
    prev = ts
if cur: clusters.append(cur)

durations = []
for cl in clusters:
    if len(cl) < 2: continue
    dur_h = (datetime.fromisoformat(cl[-1]['ts_utc']) - datetime.fromisoformat(cl[0]['ts_utc'])).total_seconds()/3600
    durations.append(dur_h)
print(f'clusters total: {len(clusters):,}  (≥2-image clusters: {len(durations):,})')
buckets = [(0,1,'<1h'), (1,4,'1-4h'), (4,12,'4-12h'), (12,24,'12-24h'),
           (24,72,'1-3d'), (72,168,'3-7d'), (168,720,'1-4w'), (720,99999,'>4w')]
for lo, hi, lbl in buckets:
    n = sum(1 for d in durations if lo <= d < hi)
    print(f'  {lbl:>6s}: {n:>4d} {"#"*n}')

# === 6. Posting cadence ===
print('\n' + '='*70)
print('6. POSTS PER QUARTER + DENSITY')
print('='*70)
by_q_count = Counter()
for r in chento:
    dt = datetime.fromisoformat(r['ts_utc'])
    q = f'{dt.year}Q{(dt.month-1)//3+1}'
    by_q_count[q] += 1
total_days = (datetime.fromisoformat(chento[-1]['ts_utc']) - datetime.fromisoformat(chento[0]['ts_utc'])).days
for q in sorted(by_q_count.keys()):
    n = by_q_count[q]
    print(f'  {q}: {n:>4} posts  ({n/90:.1f}/day avg)  {"#"*(n//10)}')

# === 7. R-mentions (text TP "+3R" etc) — captures explicit risk-talk ===
print('\n' + '='*70)
print('7. R-LANGUAGE INCIDENCE (does he talk in R?)')
print('='*70)
r_terms = {
    '"X R" e.g. 3R':   re.compile(r'(?<![a-z])\d+(?:\.\d+)?\s?R\b', re.I),
    '"RR" or "R:R"':   re.compile(r'\brr|r:r\b', re.I),
    '"risk reward"':   re.compile(r'risk\s?reward|risk\s?to\s?reward', re.I),
    '"%" sign':        re.compile(r'\d+\s?%'),
    '"$" sign':        re.compile(r'\$\d+'),
}
for name, pat in r_terms.items():
    n = sum(1 for r in chento if pat.search(r['text']))
    print(f'  {name:>16s}: {n:>4d} posts')

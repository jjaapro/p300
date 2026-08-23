#!/usr/bin/env python3
"""Build the Paladin trade timeline page.

Two outputs from one template:
  out/paladin_timeline.html          local — shows the screenshot thumbnails from images/
  out/paladin_timeline_artifact.html hosted — identical minus the local image references
"""
import json
import html
import statistics as st
from collections import Counter, defaultdict

POS = json.load(open('/tmp/paladin/extract/timeline_payload.json', encoding='utf-8'))
PB = json.load(open('/tmp/paladin/extract/playbook_final.json', encoding='utf-8'))

taken = [p for p in POS if p['outcome'] not in ('not_taken', 'never_filled')]
resolved = [p for p in taken if p['outcome'] in ('win', 'loss', 'breakeven')]
W = sum(1 for p in resolved if p['outcome'] == 'win')
L = sum(1 for p in resolved if p['outcome'] == 'loss')
B = sum(1 for p in resolved if p['outcome'] == 'breakeven')

months = defaultdict(Counter)
for p in resolved:
    months[p['month']][p['outcome']] += 1
MONTH_STATS = {m: {'w': c['win'], 'l': c['loss'], 'b': c['breakeven'],
                   'wr': round(c['win'] / (c['win'] + c['loss']) * 100) if (c['win'] + c['loss']) else None}
               for m, c in sorted(months.items())}

durs = [p['duration_hours'] for p in taken if p.get('duration_hours')]
levs = [p['leverage'] for p in taken if p.get('leverage')]
exit_types = Counter(p['exit_type'] for p in taken if p.get('exit_type'))

STATS = {
    'positions': len(POS), 'taken': len(taken), 'resolved': len(resolved),
    'w': W, 'l': L, 'b': B,
    'wr_excl': round(W / (W + L) * 100, 1), 'wr_incl': round(W / (W + L + B) * 100, 1),
    'months': MONTH_STATS,
    'median_hold': round(st.median(durs), 1),
    'symbols': len({p['symbol'] for p in POS}),
    'longs': sum(1 for p in taken if p['side'] == 'long'),
    'shorts': sum(1 for p in taken if p['side'] == 'short'),
    'manual': exit_types.get('manual_close', 0), 'tp': exit_types.get('take_profit', 0),
    'stop': exit_types.get('stop_loss', 0), 'unknown_exit': exit_types.get('unknown', 0),
    'top_lev': Counter(levs).most_common(1)[0][0] if levs else None,
    'with_stop': sum(1 for p in taken if p.get('planned_stop') is not None),
    'with_tp': sum(1 for p in taken if p.get('planned_take_profits')),
    'with_dca': sum(1 for p in taken if p.get('planned_dca')),
    'dca_filled': sum(1 for p in taken if p.get('dca_fills')),
}

RECON = PB['reconciliation']
WINMECH = PB['how_he_wins']
SIGNALS = PB['signals']
RULES = PB['risk_rules']
MGMT = PB['trade_management']
ENTRY = PB['entry_style']

PAGE_DATA = {'positions': POS, 'stats': STATS, 'recon': RECON, 'mech': WINMECH,
             'signals': SIGNALS, 'rules': RULES, 'mgmt': MGMT, 'entry': ENTRY}

CSS = r"""
:root{
  --ground:#F6F4EF; --surface:#FFFFFF; --surface-2:#F0EDE5; --ink:#191C24; --ink-2:#3E434F;
  --muted:#6A6E7A; --line:#E3DDD2; --line-2:#D3CBBC; --accent:#8A6212; --accent-soft:#F3E7CE;
  --win:#1C7048; --loss:#A93127; --flat:#8A6212; --unk:#787C88;
  --win-bg:#E4F1E9; --loss-bg:#F8E5E2; --flat-bg:#F5EBD6; --unk-bg:#ECEAE4;
  --shadow:0 1px 2px rgba(25,28,36,.06), 0 8px 24px -16px rgba(25,28,36,.25);
}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
  --ground:#13151B; --surface:#1A1D25; --surface-2:#20242E; --ink:#E9E7E2; --ink-2:#C3C1BC;
  --muted:#8B909D; --line:#282C36; --line-2:#39404E; --accent:#D9A441; --accent-soft:#2E2718;
  --win:#4FC489; --loss:#F07A6E; --flat:#D9A441; --unk:#8B909D;
  --win-bg:#16291F; --loss-bg:#2C1917; --flat-bg:#2A2213; --unk-bg:#22252D;
  --shadow:0 1px 2px rgba(0,0,0,.3), 0 8px 24px -16px rgba(0,0,0,.7);
}}
:root[data-theme="dark"]{
  --ground:#13151B; --surface:#1A1D25; --surface-2:#20242E; --ink:#E9E7E2; --ink-2:#C3C1BC;
  --muted:#8B909D; --line:#282C36; --line-2:#39404E; --accent:#D9A441; --accent-soft:#2E2718;
  --win:#4FC489; --loss:#F07A6E; --flat:#D9A441; --unk:#8B909D;
  --win-bg:#16291F; --loss-bg:#2C1917; --flat-bg:#2A2213; --unk-bg:#22252D;
  --shadow:0 1px 2px rgba(0,0,0,.3), 0 8px 24px -16px rgba(0,0,0,.7);
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);
  font-family:Karla,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4{font-family:Bitter,Georgia,"Times New Roman",serif;font-weight:600;
  text-wrap:balance;margin:0;letter-spacing:-.01em}
.mono{font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace;font-variant-numeric:tabular-nums}
a{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px}
.wrap{max-width:1180px;margin:0 auto;padding:0 24px}

/* ---------- masthead ---------- */
.mast{border-bottom:1px solid var(--line);background:var(--surface)}
.mast .wrap{padding-top:34px;padding-bottom:26px}
.kicker{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--accent);margin-bottom:12px}
.mast h1{font-size:clamp(28px,4.2vw,44px);line-height:1.1;max-width:20ch}
.mast p.lede{margin:14px 0 0;max-width:66ch;color:var(--ink-2);font-size:16.5px}

/* ---------- headline numbers ---------- */
.band{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:1px;background:var(--line);border-block:1px solid var(--line);margin-top:30px}
.band .cell{background:var(--surface);padding:16px 18px}
.band .n{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;
  font-size:26px;font-weight:500;line-height:1.15;letter-spacing:-.02em}
.band .k{font-size:11.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-top:5px}
.band .sub{font-size:12px;color:var(--muted);margin-top:3px}

/* ---------- sections ---------- */
section{padding:44px 0}
section+section{border-top:1px solid var(--line)}
.sec-head{display:flex;align-items:baseline;gap:14px;margin-bottom:8px;flex-wrap:wrap}
.sec-head h2{font-size:23px}
.sec-head .note{color:var(--muted);font-size:13px}
.sec-intro{color:var(--ink-2);max-width:70ch;margin:0 0 24px}

/* ---------- mechanics ---------- */
.mechs{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
.mech{background:var(--surface);border:1px solid var(--line);border-radius:4px;padding:16px 18px}
.mech h3{font-size:16px;margin-bottom:6px}
.strength{display:inline-block;font-family:"IBM Plex Mono",monospace;font-size:10.5px;
  letter-spacing:.07em;text-transform:uppercase;padding:2px 7px;border-radius:2px;margin-bottom:9px}
.s-strong{background:var(--win-bg);color:var(--win)}
.s-sup{background:var(--accent-soft);color:var(--accent)}
.s-weak{background:var(--unk-bg);color:var(--unk)}
.mech p{margin:0 0 9px;font-size:14px;color:var(--ink-2)}
.mech .nums{font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--muted);
  border-top:1px dashed var(--line-2);padding-top:9px;line-height:1.5}

/* ---------- reconciliation ---------- */
.recon-table{width:100%;border-collapse:collapse;font-size:14px;margin-top:6px}
.recon-table th{text-align:left;font-family:"IBM Plex Mono",monospace;font-size:10.5px;
  letter-spacing:.09em;text-transform:uppercase;color:var(--muted);
  border-bottom:1px solid var(--line-2);padding:0 14px 7px 0;font-weight:500}
.recon-table td{border-bottom:1px solid var(--line);padding:12px 14px 12px 0;
  vertical-align:top;color:var(--ink-2)}
.recon-table td:first-child{font-family:"IBM Plex Mono",monospace;color:var(--ink);white-space:nowrap}
.scroll{overflow-x:auto}

/* ---------- signals ---------- */
.sigs{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.sig{background:var(--surface);border:1px solid var(--line);border-left:2px solid var(--accent);
  border-radius:3px;padding:12px 14px}
.sig .top{display:flex;justify-content:space-between;gap:10px;align-items:baseline}
.sig h4{font-size:14px;line-height:1.3}
.sig .ct{font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--muted);white-space:nowrap}
.sig p{margin:6px 0 0;font-size:13px;color:var(--ink-2)}
.sig .cat{font-family:"IBM Plex Mono",monospace;font-size:10px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--muted);margin-top:8px}

.rules{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:22px}
.rules ul{margin:8px 0 0;padding-left:0;list-style:none}
.rules li{border-top:1px solid var(--line);padding:9px 0;font-size:14px;color:var(--ink-2)}
.rules li b{color:var(--ink);font-weight:600}
.rules h3{font-size:13px;font-family:"IBM Plex Mono",monospace;letter-spacing:.08em;
  text-transform:uppercase;color:var(--muted);font-weight:500}

/* ---------- controls ---------- */
.controls{position:sticky;top:0;z-index:20;background:var(--surface);
  border-block:1px solid var(--line);padding:11px 0}
.controls .wrap{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input[type=search],select{font-family:inherit;font-size:13.5px;color:var(--ink);
  background:var(--ground);border:1px solid var(--line-2);border-radius:3px;padding:7px 10px}
input[type=search]{flex:1;min-width:200px}
.chips{display:flex;gap:5px;flex-wrap:wrap}
.chip{font-family:"IBM Plex Mono",monospace;font-size:11.5px;letter-spacing:.04em;
  padding:6px 11px;border:1px solid var(--line-2);border-radius:2px;background:var(--ground);
  color:var(--muted);cursor:pointer}
.chip[aria-pressed=true]{background:var(--ink);color:var(--ground);border-color:var(--ink)}
.tally{margin-left:auto;font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--muted)}

/* ---------- month rule ---------- */
.month{position:sticky;top:53px;z-index:10;display:flex;align-items:baseline;gap:14px;
  background:var(--ground);padding:22px 0 10px;border-bottom:1px solid var(--line-2);margin-bottom:16px}
.month h3{font-size:19px}
.month .mstat{font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--muted)}

/* ---------- position card ---------- */
.cards{display:flex;flex-direction:column;gap:12px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:4px;
  box-shadow:var(--shadow);overflow:hidden}
.card>summary{list-style:none;cursor:pointer;padding:13px 16px;display:grid;
  grid-template-columns:96px 118px 1fr auto;gap:14px;align-items:center}
.card>summary::-webkit-details-marker{display:none}
.card>summary:hover{background:var(--surface-2)}
.when{font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--muted);line-height:1.35}
.tick{display:flex;align-items:baseline;gap:7px;min-width:0}
.tick .sym{font-family:"IBM Plex Mono",monospace;font-size:14.5px;font-weight:500;letter-spacing:-.02em}
.tick .dir{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.06em;text-transform:uppercase}
.dir.long{color:var(--win)} .dir.short{color:var(--loss)}
.gist{font-size:13.5px;color:var(--ink-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.meta{display:flex;align-items:center;gap:9px}
.pill{font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.07em;
  text-transform:uppercase;padding:3px 9px;border-radius:2px;white-space:nowrap}
.o-win{background:var(--win-bg);color:var(--win)}
.o-loss{background:var(--loss-bg);color:var(--loss)}
.o-breakeven{background:var(--flat-bg);color:var(--flat)}
.o-unknown,.o-not_taken,.o-never_filled{background:var(--unk-bg);color:var(--unk)}
.lev{font-family:"IBM Plex Mono",monospace;font-size:11.5px;color:var(--muted)}
.conf-medium,.conf-low{width:7px;height:7px;border-radius:50%;background:var(--flat);flex:none}
.conf-low{background:var(--loss)}

.body{border-top:1px solid var(--line);padding:18px 16px 16px;display:flex;flex-direction:column;gap:16px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:start}
@media (max-width:820px){.two{grid-template-columns:1fr} .card>summary{grid-template-columns:1fr auto;row-gap:6px} .gist{display:none}}
.panel{background:var(--ground);border:1px solid var(--line);border-radius:3px;padding:12px 14px}
.panel h4{font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);font-weight:500;margin-bottom:9px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;font-size:13.5px}
.kv dt{color:var(--muted);white-space:nowrap}
.kv dd{margin:0;font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.kv dd.plain{font-family:Karla,sans-serif}
.none{color:var(--muted);font-style:italic;font-size:13px}

/* R ladder */
.ladder{padding:14px 14px 8px}
.ladder .track{position:relative;height:34px;margin:34px 0 6px}
.ladder .zero{position:absolute;top:0;bottom:0;width:1px;background:var(--line-2)}
.ladder .bar{position:absolute;top:15px;height:3px;border-radius:2px}
.ladder .bar.risk{background:var(--loss);opacity:.35}
.ladder .bar.reward{background:var(--win);opacity:.3}
.ladder .mk{position:absolute;top:9px;width:2px;height:15px;transform:translateX(-1px)}
.ladder .mk.tp{background:var(--win)}
.ladder .mk.dca{background:var(--accent)}
.ladder .mk.part{background:var(--accent)}
.ladder .mk.ex{width:0;height:0;top:4px;border-left:5px solid transparent;
  border-right:5px solid transparent;border-top:8px solid var(--ink);transform:translateX(-5px)}
.ladder .lab{position:absolute;font-family:"IBM Plex Mono",monospace;font-size:10px;
  color:var(--muted);white-space:nowrap;transform:translateX(-50%)}
.ladder .lab.up{top:-6px} .ladder .lab.up2{top:-21px} .ladder .lab.dn{top:26px} .ladder .lab.dn2{top:41px}
.ladder .legend{font-family:"IBM Plex Mono",monospace;font-size:10.5px;color:var(--muted);
  display:flex;gap:14px;flex-wrap:wrap;border-top:1px dashed var(--line-2);padding-top:8px;margin-top:14px}

.story{font-size:14.5px;color:var(--ink-2);max-width:78ch}
.story b{color:var(--ink)}
.thesis{font-size:14px;color:var(--ink-2);border-left:2px solid var(--accent);padding-left:12px;max-width:78ch}
.notes{font-size:13px;color:var(--flat);background:var(--flat-bg);border-radius:3px;padding:9px 12px}

.steps{display:flex;flex-direction:column;gap:0}
.step{display:grid;grid-template-columns:118px 92px 1fr;gap:12px;padding:7px 0;
  border-top:1px solid var(--line);font-size:13.5px}
.step .st{font-family:"IBM Plex Mono",monospace;font-size:11.5px;color:var(--muted)}
.step .sy{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.05em;
  text-transform:uppercase;color:var(--accent)}
.step .sq{color:var(--ink-2);white-space:pre-wrap;overflow-wrap:anywhere}
.step .sw{color:var(--muted);font-size:12.5px;margin-top:2px}
@media (max-width:820px){.step{grid-template-columns:1fr}}

.shots{display:flex;gap:8px;flex-wrap:wrap}
.shots figure{margin:0;width:150px}
.shots img{width:100%;border:1px solid var(--line);border-radius:3px;background:var(--surface-2);display:block}
.shots figcaption{font-family:"IBM Plex Mono",monospace;font-size:9.5px;color:var(--muted);
  margin-top:4px;line-height:1.4;overflow:hidden;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical}
.msglinks{font-family:"IBM Plex Mono",monospace;font-size:11.5px;color:var(--muted);
  display:flex;gap:8px;flex-wrap:wrap;align-items:baseline}
.msglinks a{text-decoration:none}
.msglinks a:hover{text-decoration:underline}

footer{border-top:1px solid var(--line);padding:30px 0 60px;color:var(--muted);font-size:13px}
footer p{max-width:74ch}
.hidden{display:none !important}
@media (prefers-reduced-motion:no-preference){.card{transition:box-shadow .15s ease}}
"""

JS = r"""
const D = window.__DATA__;
const $ = s => document.querySelector(s);
const esc = s => (s==null?'':String(s)).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const GUILD='1042855255089623100', CH='1501271173588193290';
const SHOW_IMAGES = window.__SHOW_IMAGES__;

const fmt = v => {
  if (v === null || v === undefined || v === '') return null;
  if (typeof v !== 'number') return String(v);
  const a = Math.abs(v);
  const dp = a >= 1000 ? 1 : a >= 1 ? 3 : a >= 0.01 ? 5 : 8;
  return v.toLocaleString('en-US', {maximumFractionDigits: dp});
};
const money = v => { const s = fmt(v); return s === null ? '—' : s; };
const rTxt = r => (r===null||r===undefined) ? '' : (r>0?'+':'') + r.toFixed(2) + 'R';

function ladderHTML(p){
  const L = p.ladder; if(!L) return '';
  const pts = [-1, 0].concat(L.tps||[], L.dcas||[], (L.parts||[]).map(x=>x.r), L.exit==null?[]:[L.exit])
                     .filter(v => typeof v === 'number' && isFinite(v));
  let lo = Math.min(-1.25, ...pts) - 0.15, hi = Math.max(1.4, ...pts) + 0.25;
  const x = r => ((r - lo) / (hi - lo) * 100).toFixed(2) + '%';
  let m = '';
  const up = [], dn = [];
  m += `<div class="zero" style="left:${x(0)}"></div>`;
  m += `<div class="bar risk" style="left:${x(-1)};width:calc(${x(0)} - ${x(-1)})"></div>`;
  const top = Math.max(...(L.tps||[0]), L.exit||0, 0);
  if (top > 0) m += `<div class="bar reward" style="left:${x(0)};width:calc(${x(top)} - ${x(0)})"></div>`;
  m += `<div class="mk" style="left:${x(-1)};background:var(--loss)"></div>`; dn.push([-1,'stop']);
  m += `<div class="mk" style="left:${x(0)};background:var(--ink)"></div>`; dn.push([0,'entry']);
  (L.tps||[]).forEach((t,i) => { if(t==null) return;
    m += `<div class="mk tp" style="left:${x(t)}"></div>`; up.push([t, `TP${i+1} ${rTxt(t)}`]); });
  (L.dcas||[]).forEach(d => { if(d==null) return;
    m += `<div class="mk dca" style="left:${x(d)}"></div>`; dn.push([d,'DCA']); });
  (L.parts||[]).forEach(pt => { if(pt.r==null) return;
    m += `<div class="mk part" style="left:${x(pt.r)}"></div>`; dn.push([pt.r, pt.pct?pt.pct+'%':'part']); });
  if (L.exit != null){
    m += `<div class="mk ex" style="left:${x(L.exit)}"></div>`; up.push([L.exit, `exit ${rTxt(L.exit)}`]); }
  // stagger labels that would collide, measuring in % of track width
  const place = (arr, cls, cls2) => {
    arr.sort((a,b) => a[0]-b[0]);
    let prev = -Infinity, row = 0;
    arr.forEach(([r, txt]) => {
      const pct = (r - lo) / (hi - lo) * 100;
      const w = txt.length * 1.15;
      row = (pct - prev < w) ? (row === 0 ? 1 : 0) : 0;
      if (row === 0) prev = pct;
      m += `<div class="lab ${row ? cls2 : cls}" style="left:${pct.toFixed(2)}%">${txt}</div>`;
    });
  };
  place(up, 'up', 'up2'); place(dn, 'dn', 'dn2');
  return `<div class="panel ladder"><h4>Plan vs outcome, measured in R</h4><div class="track">${m}</div>
    <div class="legend"><span>1R = ${money(L.risk)} of price</span>${L.exit!=null?`<span>exit landed at ${rTxt(L.exit)}</span>`:'<span>no exit price posted</span>'}</div></div>`;
}

function kv(rows){
  const live = rows.filter(r => r[1] !== null && r[1] !== undefined && r[1] !== '' &&
                                !(Array.isArray(r[1]) && !r[1].length));
  if (!live.length) return '<div class="none">nothing published</div>';
  return '<dl class="kv">' + live.map(r =>
    `<dt>${esc(r[0])}</dt><dd${r[2]?' class="plain"':''}>${esc(Array.isArray(r[1])?r[1].map(fmt).join('  ·  '):(typeof r[1]==='number'?fmt(r[1]):r[1]))}</dd>`).join('') + '</dl>';
}

function cardHTML(p, i){
  const d = p.first_seen.slice(0,10), t = p.first_seen.slice(11,16);
  const gist = p.thesis || p.management_story || '';
  const conf = p.confidence !== 'high' ? `<span class="conf-${p.confidence}" title="${p.confidence} confidence — see notes"></span>` : '';
  const lev = p.leverage ? `<span class="lev">${p.leverage}×</span>` : '';

  const plan = kv([
    ['Entry', p.planned_entry], ['', p.planned_entry_note ? p.planned_entry_note : null, 1],
    ['DCA at', p.planned_dca], ['Stop', p.planned_stop],
    ['Targets', p.planned_take_profits], ['Risk', p.risk_note, 1],
  ]);
  const did = kv([
    ['Avg entry', p.avg_entry],
    ['DCA filled', (p.dca_fills||[]).map(f => fmt(f.price) + (f.note ? ' ('+f.note+')' : '')).join(' · ') || null, 1],
    ['Stop moved', (p.stop_moves||[]).map(s => fmt(s.to) + (s.type ? ' ('+s.type.replace(/_/g,' ')+')' : '')).join(' → ') || null, 1],
    ['Target moved', (p.tp_changes||[]).map(c => (c.to||[]).map(fmt).join('/')).join(' → ') || null, 1],
    ['Took off', (p.partials||[]).map(x => (x.portion_pct?x.portion_pct+'% at ':'') + fmt(x.price)).join(' · ') || null, 1],
    ['Exit', p.exit_price], ['Exit was', p.exit_type ? p.exit_type.replace(/_/g,' ') : null, 1],
    ['Result', p.r_multiple != null ? rTxt(p.r_multiple) : null],
    ['Card ROI', p.roi_pct != null ? p.roi_pct + '%' : null],
    ['Held', p.duration_hours != null ? (p.duration_hours >= 48 ? (p.duration_hours/24).toFixed(1)+' days' : p.duration_hours+' h') : null, 1],
  ]);

  const steps = (p.timeline||[]).map(s => `<div class="step">
      <div class="st">${esc(s.ts.slice(5,16))}</div>
      <div class="sy">${esc(s.ty.replace(/_/g,' '))}</div>
      <div><div class="sq">${esc(s.q)}</div>${s.why?`<div class="sw">${esc(s.why)}</div>`:''}</div></div>`).join('');

  const shots = (SHOW_IMAGES && p.images && p.images.length) ? `<div class="panel"><h4>What he posted</h4><div class="shots">` +
    p.images.map(im => `<figure><img loading="lazy" src="images/${esc(im.f)}" alt="${esc(im.t||'screenshot')}"
      onerror="this.closest('figure').remove()"><figcaption>${esc(im.d||im.t||'')}</figcaption></figure>`).join('') +
    `</div></div>` : '';

  const links = (p.msgs||[]).map(m => `<a href="https://discord.com/channels/${GUILD}/${CH}/${D.msgIds[m]||''}" target="_blank" rel="noopener">#${m}</a>`).join(' ');

  return `<details class="card" data-i="${i}">
    <summary>
      <span class="when">${d}<br>${t}</span>
      <span class="tick"><span class="sym">${esc(p.symbol||'—')}</span><span class="dir ${p.side||''}">${esc(p.side||'')}</span></span>
      <span class="gist">${esc(gist)}</span>
      <span class="meta">${lev}${conf}<span class="pill o-${p.outcome}">${esc(p.outcome.replace(/_/g,' '))}</span></span>
    </summary>
    <div class="body">
      ${p.thesis ? `<div class="thesis">${esc(p.thesis)}</div>` : ''}
      <div class="two">
        <div class="panel"><h4>What he published</h4>${plan}</div>
        <div class="panel"><h4>What actually happened</h4>${did}</div>
      </div>
      ${ladderHTML(p)}
      ${p.management_story ? `<p class="story">${esc(p.management_story)}</p>` : ''}
      ${steps ? `<div class="panel"><h4>Every message on this trade</h4><div class="steps">${steps}</div></div>` : ''}
      ${shots}
      ${p.notes ? `<div class="notes"><b>Trust note.</b> ${esc(p.notes)}</div>` : ''}
      <div class="msglinks"><span>Open in Discord:</span>${links}</div>
    </div>
  </details>`;
}

function render(){
  const q = $('#q').value.toLowerCase().trim();
  const sym = $('#sym').value, out = $('#out').value;
  const side = document.querySelector('.chip[data-g="side"][aria-pressed="true"]').dataset.v;
  const mo = document.querySelector('.chip[data-g="mo"][aria-pressed="true"]').dataset.v;
  let n = 0, html = '', lastMonth = '';
  D.positions.forEach((p, i) => {
    if (sym && p.symbol !== sym) return;
    if (out && p.outcome !== out) return;
    if (side && p.side !== side) return;
    if (mo && p.month !== mo) return;
    if (q && !p._s.includes(q)) return;
    if (p.month !== lastMonth){
      if (lastMonth) html += '</div>';
      const ms = D.stats.months[p.month];
      const label = new Date(p.month + '-02').toLocaleDateString('en-US',{month:'long',year:'numeric'});
      html += `<div class="month"><h3>${label}</h3><span class="mstat">${ms ? `${ms.w}W · ${ms.l}L · ${ms.b}BE — ${ms.wr}% win rate` : ''}</span></div><div class="cards">`;
      lastMonth = p.month;
    }
    html += cardHTML(p, i); n++;
  });
  if (lastMonth) html += '</div>';
  $('#list').innerHTML = html || '<p class="none" style="padding:40px 0">Nothing matches those filters.</p>';
  $('#tally').textContent = n + ' of ' + D.positions.length + ' positions';
}

function boot(){
  D.positions.forEach(p => {
    p._s = [p.symbol, p.side, p.outcome, p.exit_type, p.thesis, p.management_story, p.notes,
            p.market_context, (p.indicators||[]).join(' '), p.risk_note,
            (p.timeline||[]).map(s => s.q + ' ' + s.why).join(' ')].join(' ').toLowerCase();
  });
  const syms = [...new Set(D.positions.map(p => p.symbol))].sort();
  $('#sym').innerHTML = '<option value="">Every asset</option>' +
    syms.map(s => `<option>${s}</option>`).join('');
  const mos = [...new Set(D.positions.map(p => p.month))].sort();
  $('#mos').innerHTML = `<button class="chip" data-g="mo" data-v="" aria-pressed="true">All months</button>` +
    mos.map(m => `<button class="chip" data-g="mo" data-v="${m}" aria-pressed="false">${new Date(m+'-02').toLocaleDateString('en-US',{month:'short'})}</button>`).join('');
  document.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => {
    document.querySelectorAll(`.chip[data-g="${c.dataset.g}"]`).forEach(o => o.setAttribute('aria-pressed','false'));
    c.setAttribute('aria-pressed','true'); render();
  }));
  ['q','sym','out'].forEach(id => $('#'+id).addEventListener('input', render));
  render();
}
document.addEventListener('DOMContentLoaded', boot);
"""


def esc(s):
    return html.escape(str(s)) if s is not None else ''


def build(show_images: bool) -> str:
    s = STATS
    mech = ''.join(
        f'''<article class="mech"><span class="strength {"s-strong" if m["strength"].startswith("strongly") else ("s-weak" if m["strength"]=="weak" else "s-sup")}">{esc(m["strength"])}</span>
        <h3>{esc(m["mechanic"])}</h3><p>{esc(m["explanation"])}</p><div class="nums">{esc(m["numbers"])}</div></article>'''
        for m in WINMECH)

    recon_rows = ''.join(
        f'<tr><td>{esc(r["period"])}</td><td>{esc(r["he_claimed"])}</td><td>{esc(r["reconstructed"])}</td><td>{esc(r["gap"])}</td></tr>'
        for r in RECON['claimed_vs_reconstructed'])

    sigs = ''.join(
        f'''<article class="sig"><div class="top"><h4>{esc(x["name"])}</h4><span class="ct">{x.get("frequency","")}×</span></div>
        <p>{esc(x["how_he_uses_it"])}</p><div class="cat">{esc(x.get("category",""))}</div></article>'''
        for x in SIGNALS)

    def rule_list(items, key, detail):
        return ''.join(f'<li><b>{esc(i[key])}.</b> {esc(i.get(detail,""))}</li>' for i in items)

    payload = json.dumps(PAGE_DATA, ensure_ascii=False, separators=(',', ':'))
    msgs = sorted(json.load(open('/tmp/paladin/scalpx_paladin.json', encoding='utf-8'))['messages'],
                  key=lambda m: m['timestamp'])
    ids = json.dumps({i + 1: m['id'] for i, m in enumerate(msgs)}, separators=(',', ':'))

    return f"""<title>Paladin Trade Timeline</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bitter:wght@500;600&family=Karla:wght@400;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{CSS}</style>

<header class="mast"><div class="wrap">
  <div class="kicker">Reconstructed from 1,005 Discord messages and 320 screenshots · 6 May – 21 Aug 2026</div>
  <h1>Four months of Paladin's trades, rebuilt trade by trade</h1>
  <p class="lede">Every setup he published, what he planned, what he actually did with it, and how it ended —
  assembled from his own words and his own screenshots. Where the record is silent, it says so.</p>
</div></header>

<div class="band">
  <div class="cell"><div class="n">{s['positions']}</div><div class="k">Positions found</div><div class="sub">{s['taken']} actually taken</div></div>
  <div class="cell"><div class="n">{s['w']}<span style="color:var(--muted);font-size:16px"> / {s['l']} / {s['b']}</span></div><div class="k">Win · loss · flat</div><div class="sub">of {s['resolved']} with a known result</div></div>
  <div class="cell"><div class="n">{s['wr_excl']}%</div><div class="k">Reconstructed win rate</div><div class="sub">he claimed 85% and 89%</div></div>
  <div class="cell"><div class="n">{s['manual']} / {s['tp']}</div><div class="k">Hand exits vs targets hit</div><div class="sub">all {s['stop']} losses came via a stop</div></div>
  <div class="cell"><div class="n">{s['longs']}:{s['shorts']}</div><div class="k">Longs to shorts</div><div class="sub">across {s['symbols']} assets</div></div>
  <div class="cell"><div class="n">{s['median_hold']}h</div><div class="k">Median hold</div><div class="sub">{s['top_lev']}× the commonest leverage</div></div>
</div>

<section><div class="wrap">
  <div class="sec-head"><h2>How the win rate is actually produced</h2><span class="note">graded by how strongly the record supports each one</span></div>
  <p class="sec-intro">He is a competent trader with a real process. The mechanics below are ordered by how much of the
  headline number each one explains — and two of them are weaker than they look from the outside.</p>
  <div class="mechs">{mech}</div>
</div></section>

<section><div class="wrap">
  <div class="sec-head"><h2>His scoreboard against the record</h2></div>
  <p class="sec-intro">{esc(RECON['summary'])}</p>
  <div class="scroll"><table class="recon-table">
    <thead><tr><th>Period</th><th>What he posted</th><th>What the messages show</th><th>The gap</th></tr></thead>
    <tbody>{recon_rows}</tbody></table></div>
</div></section>

<section><div class="wrap">
  <div class="sec-head"><h2>What he actually watches</h2><span class="note">count = messages that mention it</span></div>
  <p class="sec-intro">The toolkit is thin on purpose. No RSI, no fibonacci, no order flow, no open interest —
  those words never appear in four months of messages. Levels, candle closes, a 200 EMA and the calendar do nearly all the work.</p>
  <div class="sigs">{sigs}</div>
</div></section>

<section><div class="wrap">
  <div class="sec-head"><h2>The rules he trades by</h2></div>
  <div class="rules">
    <div><h3>Risk</h3><ul>{rule_list(RULES,'rule','detail')}</ul></div>
    <div><h3>Entry</h3><ul>{rule_list(ENTRY,'pattern','detail')}</ul></div>
    <div><h3>Management</h3><ul>{rule_list(MGMT,'tactic','detail')}</ul></div>
  </div>
</div></section>

<div class="controls"><div class="wrap">
  <input type="search" id="q" placeholder="Search a level, a phrase, a reason — &ldquo;daily low&rdquo;, &ldquo;CPI&rdquo;, &ldquo;64.5k&rdquo;">
  <select id="sym"></select>
  <select id="out">
    <option value="">Any result</option><option value="win">Win</option><option value="loss">Loss</option>
    <option value="breakeven">Breakeven</option><option value="unknown">Never resolved</option>
    <option value="not_taken">Called, not taken</option>
  </select>
  <div class="chips">
    <button class="chip" data-g="side" data-v="" aria-pressed="true">Both</button>
    <button class="chip" data-g="side" data-v="long" aria-pressed="false">Long</button>
    <button class="chip" data-g="side" data-v="short" aria-pressed="false">Short</button>
  </div>
  <div class="chips" id="mos"></div>
  <span class="tally" id="tally"></span>
</div></div>

<main class="wrap" id="list" style="padding-bottom:50px"></main>

<footer><div class="wrap">
  <p><b>How to read a card.</b> Left panel is what he published before the trade; right panel is what the messages
  and screenshots show he actually did. The R strip plots both on one scale where his own stop distance equals 1R,
  so you can see at a glance whether an exit reached the target he had named. An amber dot next to the result means
  parts of the record had to be inferred — the trust note at the bottom of the card says which parts.</p>
  <p style="margin-top:12px">Blank means he never posted the number; nothing here is calculated or assumed on his behalf.
  {s['with_stop']} of {s['taken']} taken positions carried a published stop, {s['with_tp']} a published target,
  {s['with_dca']} a published DCA level (only {s['dca_filled']} of those ever filled). {s['unknown_exit']} positions
  simply stop being mentioned — they are marked never resolved rather than guessed.</p>
</div></footer>

<script>window.__SHOW_IMAGES__={'true' if show_images else 'false'};
window.__DATA__={payload};window.__DATA__.msgIds={ids};</script>
<script>{JS}</script>
"""


open('/tmp/paladin/out/paladin_timeline.html', 'w', encoding='utf-8').write(build(True))
open('/tmp/paladin/out/paladin_timeline_artifact.html', 'w', encoding='utf-8').write(build(False))
import os
for f in ['paladin_timeline.html', 'paladin_timeline_artifact.html']:
    print(f, round(os.path.getsize('/tmp/paladin/out/' + f) / 1024, 1), 'KB')

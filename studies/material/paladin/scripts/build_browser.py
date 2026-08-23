#!/usr/bin/env python3
"""Local HTML browser: every image beside its extracted trading data.

Written to the paladin folder; image src paths are relative to images/, so it
only works next to that folder (which is the point — the images stay local).
"""
import html
import json

merged = json.load(open('/tmp/paladin/extract/merged.json', encoding='utf-8'))
OUT = '/tmp/paladin/out/paladin_image_browser.html'


def esc(v):
    return html.escape(str(v)) if v is not None else ''


def num(v):
    if v is None:
        return '—'
    if isinstance(v, float):
        s = f'{v:,.8f}'.rstrip('0').rstrip('.')
        return s or '0'
    return f'{v:,}' if isinstance(v, int) else esc(v)


cards = []
for m in merged:
    ch = m.get('chart') or {}
    trades = m['trades'] or []
    tr_html = ''
    has_numbers = any(t.get(k) is not None for t in trades
                      for k in ('entry_price', 'exit_price', 'roi_pct', 'leverage', 'pnl_usd',
                                'stop_loss', 'liquidation_price'))
    if trades and not has_numbers and len(trades) > 4:
        # a recap listing many tickers with no numbers — keep it compact
        listed = ', '.join((t.get('symbol_norm') or t.get('symbol') or '?') for t in trades)
        tr_html = (f'<div class="chartbox"><div class="boxlabel">Trades mentioned ({len(trades)})</div>'
                   f'{esc(listed)}</div>')
    elif trades:
        rows = ''
        for t in trades:
            tp = '; '.join(str(x) for x in (t.get('take_profits') or [])) or '—'
            side = (t.get('side') or '').lower()
            side_html = f'<span class="side {side}">{esc(t.get("side") or "—")}</span>'
            rows += (
                f'<tr><td class="sym">{esc(t.get("symbol_norm") or t.get("symbol") or "—")}</td>'
                f'<td>{side_html}</td><td>{num(t.get("leverage")) if t.get("leverage") else "—"}{"x" if t.get("leverage") else ""}</td>'
                f'<td>{num(t.get("entry_price"))}</td><td>{num(t.get("exit_price"))}'
                f'<span class="tag">{esc(t.get("exit_price_type") or "")}</span></td>'
                f'<td class="{"pos" if (t.get("roi_pct") or 0) > 0 else ("neg" if (t.get("roi_pct") or 0) < 0 else "")}">'
                f'{(str(t["roi_pct"]) + "%") if t.get("roi_pct") is not None else "—"}</td>'
                f'<td>{num(t.get("stop_loss"))}</td><td>{esc(tp)}</td>'
                f'<td>{num(t.get("liquidation_price"))}</td>'
                f'<td><span class="status {esc(t.get("status"))}">{esc(t.get("status"))}</span></td></tr>')
        tr_html = (
            '<table class="trades"><thead><tr><th>Symbol</th><th>Side</th><th>Lev</th><th>Entry</th>'
            '<th>Exit / mark</th><th>ROI</th><th>SL</th><th>TP</th><th>Liq</th><th>Status</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>')

    ch_html = ''
    if ch:
        bits = []
        if ch.get('timeframe'):
            bits.append(f'<b>Timeframe:</b> {esc(ch["timeframe"])}')
        if ch.get('price_shown'):
            bits.append(f'<b>Price shown:</b> {esc(ch["price_shown"])}')
        if ch.get('indicators'):
            bits.append(f'<b>Indicators:</b> {esc("; ".join(ch["indicators"]))}')
        if ch.get('drawings_and_levels'):
            bits.append(f'<b>Levels / drawings:</b> {esc("; ".join(ch["drawings_and_levels"]))}')
        if ch.get('annotations_text'):
            bits.append(f'<b>Annotations:</b> {esc(ch["annotations_text"])}')
        if ch.get('what_it_shows'):
            bits.append(f'<b>Setup:</b> {esc(ch["what_it_shows"])}')
        ch_html = '<div class="chartbox"><div class="boxlabel">Chart</div>' + '<br>'.join(bits) + '</div>'

    media = (f'<video src="images/{esc(m["filename"])}" controls preload="none"></video>'
             if m['filename'].endswith('.mp4')
             else f'<img loading="lazy" src="images/{esc(m["filename"])}" alt="{esc(m["filename"])}">')

    syms = sorted({(t.get('symbol_norm') or t.get('symbol') or '') for t in trades} - {''})
    search = ' '.join([m['filename'], m['date'], m['image_type'], m['platform'], ' '.join(syms),
                       m.get('message') or '', m.get('visible_text') or '', m.get('description') or '',
                       ch.get('what_it_shows') or '', ' '.join(ch.get('indicators') or [])]).lower()

    cards.append(f'''
<article class="card" data-search="{esc(search)}" data-type="{esc(m['image_type'])}" data-platform="{esc(m['platform'])}" data-conf="{esc(m['confidence'])}" id="img{m['index']}">
  <div class="media">{media}</div>
  <div class="info">
    <div class="head">
      <span class="idx">#{m['index']}</span>
      <span class="date">{esc(m['date'])} {esc(m['time'])}</span>
      <span class="chip">{esc(m['image_type'])}</span>
      <span class="chip alt">{esc(m['platform'])}</span>
      {'<span class="chip warn">medium confidence</span>' if m['confidence'] == 'medium' else ''}
      {'<span class="chip warn">no trading info</span>' if not m['trading_related'] else ''}
      <a class="link" href="{esc(m['message_link'])}" target="_blank" rel="noopener">open in Discord ↗</a>
    </div>
    {f'<div class="msg"><div class="boxlabel">He wrote</div>{esc(m["message"])}</div>' if m.get('message') else ''}
    {tr_html}
    {ch_html}
    {f'<div class="desc">{esc(m["description"])}</div>' if m.get('description') else ''}
    {f'<details class="vt"><summary>Text visible in the image</summary><pre>{esc(m["visible_text"])}</pre></details>' if m.get('visible_text') else ''}
    {f'<div class="issues"><b>Note:</b> {esc(m["issues"])}</div>' if m.get('issues') else ''}
    <div class="fname">{esc(m['filename'])}</div>
  </div>
</article>''')

types = sorted({m['image_type'] for m in merged})
plats = sorted({m['platform'] for m in merged})

doc = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Paladin image browser</title>
<style>
  :root {{
    --bg:#0e1014; --panel:#171a21; --panel2:#1e222b; --line:#2a2f3a;
    --text:#e6e8ec; --muted:#9aa3b2; --accent:#4f8cff; --pos:#2ecc8f; --neg:#ff5c6c; --warn:#f0b429;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif; }}
  header {{ position:sticky; top:0; z-index:10; background:rgba(14,16,20,.95); backdrop-filter:blur(6px);
           border-bottom:1px solid var(--line); padding:14px 20px; }}
  h1 {{ margin:0 0 4px; font-size:17px; letter-spacing:.2px; }}
  .sub {{ color:var(--muted); font-size:12px; margin-bottom:10px; }}
  .controls {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; }}
  input,select {{ background:var(--panel2); color:var(--text); border:1px solid var(--line);
                 border-radius:8px; padding:8px 10px; font-size:13px; }}
  input[type=search] {{ min-width:280px; flex:1; }}
  .count {{ color:var(--muted); font-size:12px; margin-left:auto; }}
  main {{ padding:18px 20px 60px; display:flex; flex-direction:column; gap:16px; max-width:1500px; margin:0 auto; }}
  .card {{ display:grid; grid-template-columns:minmax(240px,380px) 1fr; gap:18px; background:var(--panel);
          border:1px solid var(--line); border-radius:12px; padding:14px; }}
  @media (max-width:900px) {{ .card {{ grid-template-columns:1fr; }} }}
  .media img, .media video {{ width:100%; border-radius:8px; background:#000; display:block; }}
  .head {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:10px; }}
  .idx {{ color:var(--muted); font-weight:700; }}
  .date {{ color:var(--muted); }}
  .chip {{ background:var(--panel2); border:1px solid var(--line); border-radius:999px; padding:2px 9px; font-size:11.5px; color:var(--muted); }}
  .chip.alt {{ color:var(--accent); border-color:#2b3d5e; }}
  .chip.warn {{ color:var(--warn); border-color:#4a3c14; }}
  .link {{ margin-left:auto; color:var(--accent); text-decoration:none; font-size:12px; }}
  .msg {{ background:var(--panel2); border-left:3px solid var(--accent); border-radius:6px; padding:8px 10px; margin-bottom:10px; white-space:pre-wrap; }}
  .boxlabel {{ font-size:10.5px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin-bottom:3px; }}
  table.trades {{ width:100%; border-collapse:collapse; margin-bottom:10px; font-size:13px; }}
  table.trades th {{ text-align:left; font-weight:600; color:var(--muted); font-size:11px; text-transform:uppercase;
                    letter-spacing:.05em; border-bottom:1px solid var(--line); padding:4px 8px 4px 0; }}
  table.trades td {{ padding:5px 8px 5px 0; border-bottom:1px solid #21252e; vertical-align:top; }}
  .sym {{ font-weight:600; }}
  .side.long {{ color:var(--pos); }} .side.short {{ color:var(--neg); }}
  .pos {{ color:var(--pos); font-weight:600; }} .neg {{ color:var(--neg); font-weight:600; }}
  .tag {{ color:var(--muted); font-size:10.5px; margin-left:5px; }}
  .status {{ font-size:11px; padding:1px 7px; border-radius:999px; border:1px solid var(--line); color:var(--muted); }}
  .status.closed {{ color:var(--pos); border-color:#1f4d3a; }}
  .status.open {{ color:var(--accent); border-color:#2b3d5e; }}
  .chartbox {{ background:var(--panel2); border-radius:8px; padding:9px 11px; margin-bottom:10px; }}
  .desc {{ color:var(--muted); margin-bottom:8px; }}
  .vt summary {{ cursor:pointer; color:var(--accent); font-size:12.5px; }}
  .vt pre {{ white-space:pre-wrap; background:var(--panel2); border-radius:8px; padding:10px; font-size:12px;
            color:var(--text); max-height:340px; overflow:auto; }}
  .issues {{ color:var(--warn); font-size:12px; margin-top:6px; }}
  .fname {{ color:#5b6472; font-size:11px; margin-top:8px; font-family:ui-monospace,Consolas,monospace; }}
  .hidden {{ display:none; }}
</style></head><body>
<header>
  <h1>Paladin — every posted image and what it shows</h1>
  <div class="sub">{len(merged)} images from #🔮｜paladin, 2026-05-06 → 2026-08-21. Numbers are read off the images; blank means it was not visible.</div>
  <div class="controls">
    <input type="search" id="q" placeholder="Search symbol, text in image, his message, chart notes…">
    <select id="type"><option value="">All image types</option>{''.join(f'<option>{esc(t)}</option>' for t in types)}</select>
    <select id="plat"><option value="">All platforms</option>{''.join(f'<option>{esc(p)}</option>' for p in plats)}</select>
    <label style="color:var(--muted);font-size:12.5px"><input type="checkbox" id="onlytrades" style="vertical-align:-2px"> only images with trades</label>
    <span class="count" id="count"></span>
  </div>
</header>
<main id="list">
{''.join(cards)}
</main>
<script>
  const cards=[...document.querySelectorAll('.card')];
  const q=document.getElementById('q'), ty=document.getElementById('type'),
        pl=document.getElementById('plat'), ot=document.getElementById('onlytrades'),
        cnt=document.getElementById('count');
  function apply(){{
    const s=q.value.toLowerCase().trim(), t=ty.value, p=pl.value, o=ot.checked;
    let n=0;
    for(const c of cards){{
      const ok=(!s||c.dataset.search.includes(s))
             &&(!t||c.dataset.type===t)&&(!p||c.dataset.platform===p)
             &&(!o||c.querySelector('table.trades'));
      c.classList.toggle('hidden',!ok); if(ok)n++;
    }}
    cnt.textContent=n+' / '+cards.length+' images';
  }}
  [q,ty,pl,ot].forEach(e=>e.addEventListener('input',apply)); apply();
</script>
</body></html>'''

open(OUT, 'w', encoding='utf-8').write(doc)
import os
print('saved', OUT, round(os.path.getsize(OUT) / 1024, 1), 'KB')

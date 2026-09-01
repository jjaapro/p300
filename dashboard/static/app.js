"use strict";
/* p300 dashboard front-end. No framework. All dynamic values inserted via
   textContent — API data never becomes HTML (marked-rendered bot cards are
   the one exception and render repo-owned files only).

   Chart: TradingView Lightweight Charts v5 (vendored). Trade entries are
   circle markers — LONG below the bar, SHORT above (position encodes
   direction; color encodes open #3b82f6 / closed muted). Hover a marker
   bar for the plan (TP / SL / timed stop); click to pin price lines and
   the full decision panel. Palette validated (dataviz six checks) against
   the dark surface. */

const HEALTH_MS = 5000;
const CHART_MS = 30000;
const TF_S = { "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400 };
const OPEN_MARKER = "#3b82f6";
const CLOSED_MARKER = "#5c6270";

const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

/* Age formatting mirrors monitor.py's vocabulary. */
function fmtAge(s) {
  if (s === null || s === undefined) return "never";
  if (s < 120) return `${Math.round(s)}s`;
  if (s < 7200) return `${Math.round(s / 60)}m`;
  if (s < 172800) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(1)}d`;
}

function fmtPrice(p) {
  if (p === null || p === undefined) return "—";
  return p >= 1000 ? p.toLocaleString("en-US", { maximumFractionDigits: 1 })
                   : p.toLocaleString("en-US", { maximumFractionDigits: 4 });
}

function fmtIso(iso) {
  return iso ? iso.slice(0, 16).replace("T", " ") + "Z" : "—";
}

function fmtNum(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") {
    return Math.abs(v) >= 1000 ? v.toLocaleString("en-US",
      { maximumFractionDigits: 1 }) : String(Math.round(v * 10000) / 10000);
  }
  return String(v);
}

/* ── health strip ─────────────────────────────────────────────────────── */

let lastOk = null;

function renderBanner(ov) {
  const b = $("banner");
  b.classList.remove("banner-wait", "banner-green", "banner-amber",
                     "banner-red", "banner-stale");
  const reds = ov.alerts.filter((a) => a.severity === "red").length;
  const ambers = ov.alerts.length - reds;
  if (reds) {
    b.classList.add("banner-red");
    b.textContent = `${reds} ALERT${reds > 1 ? "S" : ""}` +
      (ambers ? ` + ${ambers} warning${ambers > 1 ? "s" : ""}` : "");
  } else if (ambers) {
    b.classList.add("banner-amber");
    b.textContent = `${ambers} warning${ambers > 1 ? "s" : ""}`;
  } else {
    b.classList.add("banner-green");
    const n = ov.fleet.length;
    b.textContent = `ALL SYSTEMS GO — ${n} units running single, tables fresh`;
  }

  const list = $("alerts");
  list.replaceChildren();
  for (const a of ov.alerts) {
    list.appendChild(el("div", `alert alert-${a.severity}`, a.text));
  }
}

function renderFleet(ov) {
  const wrap = $("fleet");
  wrap.replaceChildren();
  for (const u of ov.fleet) {
    const tile = el("div", `tile state-${u.state.toLowerCase()}`);
    const head = el("div", "tile-head");
    head.appendChild(el("span", "tile-name", u.unit));
    head.appendChild(el("span", "tile-state", u.state));
    tile.appendChild(head);

    const hb = u.heartbeat;
    tile.appendChild(el("div", "tile-row",
      hb ? `tick ${fmtAge(hb.tick_age_s)} ago` : "no heartbeat"));
    if (hb && hb.open_trades !== null && hb.open_trades !== undefined) {
      tile.appendChild(el("div", "tile-row",
        `open trades: ${hb.open_trades}`));
    }
    if (u.expectation_s !== null && hb) {
      tile.appendChild(el("div", "tile-row dim",
        `eval ${fmtAge(hb.eval_age_s)} ago (limit ${fmtAge(u.expectation_s)})`));
    }
    for (const inst of u.instances) {
      tile.appendChild(el("div", "tile-row mono",
        `${inst.pids.join("→")} up ${fmtAge(inst.age_s)}`));
    }
    if (u.instance_count === 0) {
      tile.appendChild(el("div", "tile-row dim", "no process visible"));
    }
    if (hb && !hb.pid_known) {
      tile.appendChild(el("div", "tile-row dim",
        "pid unknown (pre-migration process)"));
    }
    if (u.state === "DUPLICATE" && u.instance_count > 1) {
      tile.appendChild(el("div", "tile-row",
        "kill all but one (oldest listed first)"));
    }
    wrap.appendChild(tile);
  }
}

/* ── feeds grid ───────────────────────────────────────────────────────── */

function renderFeeds(fd) {
  const wrap = $("feeds");
  wrap.replaceChildren();
  const table = el("table", "grid");
  const head = el("tr");
  for (const h of ["table", "age", "limit", "", "state"]) {
    head.appendChild(el("th", null, h));
  }
  table.appendChild(head);
  for (const t of fd.tables) {
    const tr = el("tr", `feed-${t.state}`);
    tr.appendChild(el("td", "mono", t.table));
    tr.appendChild(el("td", null, fmtAge(t.age_s)));
    tr.appendChild(el("td", "dim", fmtAge(t.limit_s)));
    const barCell = el("td", "barcell");
    const bar = el("div", "bar");
    const fill = el("div", "bar-fill");
    const ratio = t.ratio === null ? 1 : Math.min(1, t.ratio);
    fill.style.width = `${Math.max(2, ratio * 100)}%`;
    if (t.state !== "fresh") fill.classList.add("bar-bad");
    bar.appendChild(fill);
    barCell.appendChild(bar);
    tr.appendChild(barCell);
    let stateTxt = t.state;
    if (t.failing_fetch) stateTxt += " · fetch failing";
    if (t.retention && t.retention.state !== "ok") {
      stateTxt += ` · ${t.retention.days_left}d retention left`;
    }
    tr.appendChild(el("td", null, stateTxt));
    table.appendChild(tr);
  }
  wrap.appendChild(table);

  $("feeds-note").textContent =
    fd.feed_note && fd.feed_note.startsWith("failed:")
      ? `(feed reports: ${fd.feed_note})` : "";
  const parts = [];
  parts.push(`${fd.frozen_count} frozen tables (research snapshots)`);
  if (fd.gated.length) parts.push(`gated off: ${fd.gated.join(", ")}`);
  for (const a of fd.archive) {
    parts.push(`${a.file}: ${a.state === "ok" ? fmtAge(a.age_s) + " ago" : a.state}`);
  }
  if (fd.event_runway.days !== null) {
    parts.push(`event runway ${fd.event_runway.days}d`);
  }
  $("feeds-footer").textContent = parts.join(" · ");
}

/* ── chart ────────────────────────────────────────────────────────────── */

let chart = null;
let series = null;
let markersPrim = null;
let curAsset = "BTC";
let curTf = "1h";
let showClosed = false;
let candleLast = null;
let tradesCache = [];
let markersByTime = new Map();   // barTime -> [{t, kind}]
let priceLines = [];
let selectedId = null;

function initChart() {
  if (typeof LightweightCharts === "undefined") {
    $("chart").textContent = "chart library missing (static/vendor/)";
    return false;
  }
  chart = LightweightCharts.createChart($("chart"), {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: "#8a8f9c" },
    grid: { vertLines: { color: "#22252e" }, horzLines: { color: "#22252e" } },
    rightPriceScale: { borderColor: "#2e3340" },
    timeScale: { borderColor: "#2e3340", timeVisible: true,
                 secondsVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });
  series = chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: "#26a69a", downColor: "#ef5350", borderVisible: false,
    wickUpColor: "#26a69a", wickDownColor: "#ef5350",
  });
  markersPrim = LightweightCharts.createSeriesMarkers(series, []);
  chart.subscribeCrosshairMove(onCrosshair);
  chart.subscribeClick(onChartClick);
  buildChartControls();
  return true;
}

function buildChartControls() {
  const c = $("chart-controls");
  c.replaceChildren();
  for (const a of ["BTC", "ETH"]) {
    const b = el("button", a === curAsset ? "active" : "", a);
    b.dataset.asset = a;
    b.onclick = () => { curAsset = a; markControls(); loadChart(); };
    c.appendChild(b);
  }
  c.appendChild(el("span", "dim", " "));
  for (const tf of Object.keys(TF_S)) {
    const b = el("button", tf === curTf ? "active" : "", tf);
    b.dataset.tf = tf;
    b.onclick = () => { curTf = tf; markControls(); loadChart(); };
    c.appendChild(b);
  }
  const tog = el("button", showClosed ? "active" : "", "show closed");
  tog.dataset.toggle = "closed";
  tog.onclick = () => { showClosed = !showClosed; markControls();
                        rebuildMarkers(); };
  c.appendChild(tog);
}

function markControls() {
  for (const b of $("chart-controls").querySelectorAll("button")) {
    b.classList.toggle("active",
      b.dataset.asset === curAsset || b.dataset.tf === curTf ||
      (b.dataset.toggle === "closed" && showClosed));
  }
}

async function loadChart() {
  if (!chart) return;
  clearSelection();
  const [cd, td] = await Promise.all([
    getJSON(`/api/candles?asset=${curAsset}&tf=${curTf}`),
    getJSON("/api/trades?scope=recent"),
  ]);
  tradesCache = td.trades;
  series.setData(cd.bars);
  candleLast = cd.last_time;
  if (cd.bars.length > 160) {
    chart.timeScale().setVisibleLogicalRange(
      { from: cd.bars.length - 150, to: cd.bars.length + 5 });
  } else {
    chart.timeScale().fitContent();
  }
  rebuildMarkers();
}

async function refreshChart() {
  if (!chart || document.hidden) return;
  try {
    const [cd, td] = await Promise.all([
      getJSON(`/api/candles?asset=${curAsset}&tf=${curTf}` +
              (candleLast ? `&after=${candleLast}` : "")),
      getJSON("/api/trades?scope=recent"),
    ]);
    for (const b of cd.bars) series.update(b);
    if (cd.last_time) candleLast = cd.last_time;
    tradesCache = td.trades;
    rebuildMarkers();
  } catch (e) { /* transient — health poll shows the stale banner */ }
}

function shownTrades() {
  return tradesCache.filter((t) =>
    t.asset === curAsset && t.entry_ts &&
    (t.status === "open" || showClosed));
}

function rebuildMarkers() {
  if (!markersPrim) return;
  const secs = TF_S[curTf];
  markersByTime = new Map();
  const markers = [];
  const add = (time, t, kind, m) => {
    if (!markersByTime.has(time)) markersByTime.set(time, []);
    markersByTime.get(time).push({ t, kind });
    markers.push(m);
  };
  for (const t of shownTrades()) {
    const isOpen = t.status === "open";
    const bt = Math.floor(t.entry_ts / secs) * secs;
    add(bt, t, "entry", {
      time: bt,
      position: t.direction === "LONG" ? "belowBar" : "aboveBar",
      shape: "circle",
      color: isOpen ? OPEN_MARKER : CLOSED_MARKER,
      size: isOpen ? 2 : 1,
      id: `${t.id}-entry`,
    });
    if (!isOpen && t.exit_ts) {
      const xt = Math.floor(t.exit_ts / secs) * secs;
      add(xt, t, "exit", {
        time: xt,
        position: t.direction === "LONG" ? "aboveBar" : "belowBar",
        shape: "square",
        color: CLOSED_MARKER,
        size: 1,
        id: `${t.id}-exit`,
      });
    }
  }
  markers.sort((a, b) => a.time - b.time);
  markersPrim.setMarkers(markers);
}

function hitsAt(time) {
  if (time === undefined || time === null) return [];
  const secs = TF_S[curTf];
  const out = [];
  for (const dt of [0, -secs, secs]) {
    for (const h of markersByTime.get(time + dt) || []) {
      if (!out.includes(h)) out.push(h);
    }
    if (out.length) break;      // exact bar wins; neighbors only as fallback
  }
  return out;
}

function kvRow(grid, k, v) {
  grid.appendChild(el("span", null, k));
  grid.appendChild(el("span", null, v));
}

function tooltipContent(hits) {
  const box = el("div");
  for (const { t, kind } of hits) {
    const h = el("h4", null,
      `${t.id} · ${t.bot} · ${t.direction} · ` +
      (t.status === "open" ? "OPEN" :
        `closed${t.r_multiple !== null ? ` ${t.r_multiple > 0 ? "+" : ""}${t.r_multiple}R` : ""}`) +
      (kind === "exit" ? " (exit)" : ""));
    box.appendChild(h);
    const kv = el("div", "kv");
    kvRow(kv, "entry", `${fmtPrice(t.entry_price)} @ ${fmtIso(t.entry_time)}`);
    const plan = t.plan || {};
    kvRow(kv, "stop (SL)", fmtPrice(plan.stop_price));
    kvRow(kv, "target (TP)", plan.target_price ? fmtPrice(plan.target_price)
                                               : "— (trailing/none)");
    kvRow(kv, "timed stop", t.timed_stop ? fmtIso(t.timed_stop)
                                         : "none (trailing/streak exit)");
    if (plan.risk_price) kvRow(kv, "stop dist", fmtPrice(plan.risk_price));
    kvRow(kv, "size", `$${fmtNum(t.size_usdt)} · qty ${fmtNum(t.qty)}` +
      (t.leverage ? ` · ${fmtNum(t.leverage)}x` : ""));
    if (t.status === "open" && t.unrealized_usdt !== null) {
      kvRow(kv, "unrealized", `$${fmtNum(t.unrealized_usdt)} (approx)`);
    }
    if (t.status !== "open" && t.pnl_usdt !== null) {
      kvRow(kv, "pnl", `$${fmtNum(t.pnl_usdt)} (${fmtNum(t.pnl_pct)}%)`);
    }
    const filt = (t.decision && t.decision.filters) || null;
    if (filt) {
      kvRow(kv, "filters", Object.entries(filt)
        .map(([k, v]) => `${k}=${fmtNum(v)}`).join("  "));
    }
    box.appendChild(kv);
  }
  box.appendChild(el("div", "dim", "click marker to pin levels + full decision"));
  return box;
}

function onCrosshair(param) {
  const tip = $("tooltip");
  const hits = param.point ? hitsAt(param.time) : [];
  if (!hits.length) { tip.classList.add("hidden"); return; }
  tip.replaceChildren(tooltipContent(hits));
  tip.classList.remove("hidden");
  const wrap = $("chart-wrap");
  const x = Math.min(param.point.x + 14, wrap.clientWidth - tip.offsetWidth - 8);
  const y = Math.min(param.point.y + 14, wrap.clientHeight - tip.offsetHeight - 8);
  tip.style.left = `${Math.max(0, x)}px`;
  tip.style.top = `${Math.max(0, y)}px`;
}

function clearSelection() {
  for (const pl of priceLines) series.removePriceLine(pl);
  priceLines = [];
  selectedId = null;
  $("trade-detail").classList.add("hidden");
}

function selectTrade(t) {
  clearSelection();
  selectedId = t.id;
  const mk = (price, color, style, title) => {
    if (price === null || price === undefined) return;
    priceLines.push(series.createPriceLine({
      price, color, lineWidth: 1, lineStyle: style, title,
      axisLabelVisible: true,
    }));
  };
  const dash = LightweightCharts.LineStyle.Dashed;
  const solid = LightweightCharts.LineStyle.Solid;
  const plan = t.plan || {};
  mk(t.entry_price, OPEN_MARKER, solid, `${t.id} entry`);
  mk(plan.stop_price, "#d62728", dash, "SL");
  mk(plan.target_price, "#2ca02c", dash, "TP");

  const d = $("trade-detail");
  d.replaceChildren();
  d.classList.remove("hidden");
  d.appendChild(el("h4", null,
    `${t.id} · ${t.bot} · ${t.asset} ${t.direction} · ${t.status}` +
    ` · entered ${fmtIso(t.entry_time)}`));
  const kv = el("div", "kv");
  const dec = t.decision || {};
  if (dec.trigger) kvRow(kv, "trigger", dec.trigger);
  if (dec.bar_ts) kvRow(kv, "signal bar", fmtIso(dec.bar_ts));
  for (const [k, v] of Object.entries(dec.filters || {})) {
    kvRow(kv, `filter ${k}`, fmtNum(v));
  }
  for (const [k, v] of Object.entries(dec.inputs || {})) {
    kvRow(kv, k, fmtNum(v));
  }
  for (const [k, v] of Object.entries((t.plan && t.plan.other) || {})) {
    kvRow(kv, k, fmtNum(v));
  }
  d.appendChild(kv);
  for (const line of t.exit_lines || []) {
    d.appendChild(el("div", "dim mono", line));
  }
  if (t.notes_error) {
    d.appendChild(el("div", "dim", `notes unparsed: ${t.notes_error}`));
  }
}

function onChartClick(param) {
  const hits = param.point ? hitsAt(param.time) : [];
  if (!hits.length) { clearSelection(); return; }
  const idx = hits.findIndex((h) => h.t.id === selectedId);
  const next = hits[(idx + 1) % hits.length].t;   // click again cycles
  selectTrade(next);
}

/* ── bot info tabs ────────────────────────────────────────────────────── */

let curBot = null;

function mdDiv(md) {
  const d = el("div", "md");
  /* innerHTML is acceptable here only because the markdown comes from
     repo-owned files (dashboard/cards/, docs/calibration/) served by our
     own read-only server — never from API/user data. */
  if (typeof marked !== "undefined") d.innerHTML = marked.parse(md);
  else d.textContent = md;
  return d;
}

async function initBots() {
  let bl;
  try { bl = await getJSON("/api/bots"); } catch (e) { return; }
  const tabs = $("bot-tabs");
  tabs.replaceChildren();
  for (const b of bl.bots) {
    const btn = el("button", null,
      `${b.name} (${b.open_trades} open / ${b.trades_total})`);
    btn.dataset.bot = b.name;
    btn.onclick = () => showBot(b.name);
    tabs.appendChild(btn);
  }
  if (bl.bots.length) showBot(bl.bots[0].name);
}

async function showBot(name) {
  curBot = name;
  for (const b of $("bot-tabs").querySelectorAll("button")) {
    b.classList.toggle("active", b.dataset.bot === name);
  }
  const info = $("bot-info");
  info.replaceChildren(el("div", "dim", "loading…"));
  let d;
  try { d = await getJSON(`/api/bots/${name}`); }
  catch (e) { info.replaceChildren(el("div", "dim", e.message)); return; }
  if (curBot !== name) return;               // user clicked away meanwhile
  info.replaceChildren();

  info.appendChild(el("h3", null, d.display));
  info.appendChild(el("div", "dim", d.cadence_note));

  // live params (imported from the running configs — cannot drift)
  const pt = el("table", "params");
  let lastGroup = null;
  for (const p of d.params) {
    const tr = el("tr");
    tr.appendChild(el("td", "dim", p.group === lastGroup ? "" : p.group));
    lastGroup = p.group;
    tr.appendChild(el("td", null, p.name));
    tr.appendChild(el("td", "mono", `${p.value}${p.unit ? " " + p.unit : ""}`));
    pt.appendChild(tr);
  }
  info.appendChild(pt);

  info.appendChild(mdDiv(d.card_md));

  const le = el("h3", null, "Latest entry");
  info.appendChild(le);
  if (d.latest_entry) {
    const t = d.latest_entry;
    info.appendChild(el("div", null,
      `${t.id} · ${t.asset} ${t.direction} · ${t.status} · ` +
      `entered ${fmtIso(t.entry_time)} @ ${fmtPrice(t.entry_price)}` +
      (t.r_multiple !== null && t.status !== "open"
        ? ` · ${t.r_multiple > 0 ? "+" : ""}${t.r_multiple}R` : "")));
    if (d.entry_chart_url) {
      const img = document.createElement("img");
      img.src = d.entry_chart_url;
      img.alt = `entry context chart for ${t.id}`;
      img.onclick = () => window.open(d.entry_chart_url, "_blank");
      info.appendChild(img);
    }
  } else {
    info.appendChild(el("div", "dim", d.no_trades_note || "no trades yet"));
  }

  if (d.diag_today) {
    const dg = d.diag_today;
    info.appendChild(el("h3", null,
      `Gate diagnostics — ${dg.date}${dg.is_today ? " (today)" : " (latest)"}`));
    const counters = el("div", "mono dim",
      Object.entries(dg.counters).map(([k, v]) => `${k}=${v}`).join("  "));
    info.appendChild(counters);
    for (const nm of dg.near_misses) {
      info.appendChild(el("div", "dim",
        `near miss: ${JSON.stringify(nm)}`));
    }
  }

  const det = document.createElement("details");
  const sum = document.createElement("summary");
  sum.textContent = `calibration log (${d.calibration_path})`;
  det.appendChild(sum);
  det.appendChild(mdDiv(d.calibration_md));
  info.appendChild(det);
}

/* ── polling ──────────────────────────────────────────────────────────── */

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

async function pollHealth() {
  if (document.hidden) return;
  try {
    const [ov, fd] = await Promise.all([
      getJSON("/api/overview"), getJSON("/api/feeds"),
    ]);
    renderBanner(ov);
    renderFleet(ov);
    renderFeeds(fd);
    lastOk = new Date();
    $("foot").textContent =
      `updated ${lastOk.toLocaleTimeString()} · read-only · ` +
      `${ov.scan.scanned_python} python processes scanned · ` +
      `chart: TradingView Lightweight Charts™`;
  } catch (e) {
    const b = $("banner");
    b.classList.remove("banner-wait", "banner-green", "banner-amber",
                       "banner-red");
    b.classList.add("banner-stale");
    b.textContent = `dashboard stale — server unreachable since ` +
      `${lastOk ? lastOk.toLocaleTimeString() : "start"} (${e.message})`;
  }
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) { pollHealth(); refreshChart(); }
});

pollHealth();
setInterval(pollHealth, HEALTH_MS);
if (initChart()) {
  loadChart().catch((e) => { $("chart").textContent = `chart: ${e.message}`; });
  setInterval(refreshChart, CHART_MS);
}
initBots();

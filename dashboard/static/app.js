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

/* Flow panes (dashboard/market.py) — descriptive context under the
   candles: what the bots see, not a signal. One measure family per pane,
   never two scales on one axis: pane 1 CVD in base units (perp line, spot
   line, spot−perp histogram); pane 2 ΔOI% histogram — bar direction is the
   OI sign, bar color the CVD sign, so the quadrant reads from geometry (OI
   level lives in the legend); pane 3 bp (funding per 8h as a histogram,
   basis as a line). Palettes validated with the dataviz validator on their
   own surfaces — dark #14161a: #3987e5 / #d95926; light #f4f4f2: #2a78d6 /
   #eb6834 — plus the #26a69a / #ef5350 polarity pair (the candle colors),
   which passes on both and so never needs re-coloring on a theme switch. */
const THEMES = {
  dark:  { primary: "#3987e5", secondary: "#d95926", dim: "#8a8f9c",
           text: "#8a8f9c", grid: "#22252e", border: "#2e3340" },
  light: { primary: "#2a78d6", secondary: "#eb6834", dim: "#9aa0ab",
           text: "#5b606c", grid: "#e4e5e8", border: "#d3d5da" },
};
const UP = "#26a69a";
const DOWN = "#ef5350";
const THEME_KEY = "p300.theme";
let themeMode = "auto";            // auto (light 07–19 local clock) | light | dark
function TH() { return THEMES[currentTheme()]; }
const PANE_H = 110;
const QUADRANT = { longs_opening: "longs opening", short_covering: "short covering",
                   shorts_opening: "shorts opening", longs_closing: "longs closing" };

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
// deep link: /#ETH or /#BTC-15m selects asset (and optionally timeframe)
const _hash = /^#(BTC|ETH)(?:-(15m|1h|4h|1d))?$/.exec(location.hash) || [];
let curAsset = _hash[1] || "BTC";
let curTf = _hash[2] || "1h";
let showClosed = false;
let candleLast = null;
let tradesCache = [];
let markersByTime = new Map();   // barTime -> [{t, kind}]
let priceLines = [];
let selectedId = null;
let loadToken = 0;
let flowSeries = {};            // series name -> Lightweight Charts series
let flowBars = [];
let flowByTime = new Map();
let flowLast = null;
let flowMeta = null;            // last /api/flow payload (bars excluded)

function chartOptions() {
  const t = TH();
  return {
    layout: { background: { color: "transparent" }, textColor: t.text },
    grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
    rightPriceScale: { borderColor: t.border },
    timeScale: { borderColor: t.border, timeVisible: true, secondsVisible: false },
  };
}

function initChart() {
  if (typeof LightweightCharts === "undefined") {
    $("chart").textContent = "chart library missing (static/vendor/)";
    return false;
  }
  chart = LightweightCharts.createChart($("chart"), {
    autoSize: true, ...chartOptions(),
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
  const token = ++loadToken;
  const [cd, td, fd] = await Promise.all([
    getJSON(`/api/candles?asset=${curAsset}&tf=${curTf}`),
    getJSON("/api/trades?scope=recent"),
    getJSON(`/api/flow?asset=${curAsset}&tf=${curTf}`)
      .catch((e) => ({ error: e.message })),
  ]);
  if (token !== loadToken) return;            // user switched meanwhile
  tradesCache = td.trades;
  series.setData(cd.bars);
  candleLast = cd.last_time;
  if (fd.error) {
    $("flow-legend").replaceChildren(
      el("span", "dim", `flow panes unavailable: ${fd.error}`));
  } else {
    rebuildPanes(fd);
    setFlowData(fd);
  }
  loadProfile();
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
  const asset = curAsset, tf = curTf, token = loadToken;
  try {
    const [cd, td, fd] = await Promise.all([
      getJSON(`/api/candles?asset=${asset}&tf=${tf}` +
              (candleLast ? `&after=${candleLast}` : "")),
      getJSON("/api/trades?scope=recent"),
      Object.keys(flowSeries).length
        ? getJSON(`/api/flow?asset=${asset}&tf=${tf}` +
                  (flowLast ? `&after=${flowLast}` : ""))
        : Promise.resolve(null),
    ]);
    if (token !== loadToken || asset !== curAsset || tf !== curTf) return;
    for (const b of cd.bars) series.update(b);
    if (cd.last_time) candleLast = cd.last_time;
    tradesCache = td.trades;
    if (fd) updateFlowData(fd);
    rebuildMarkers();
  } catch (e) { /* transient — health poll shows the stale banner */ }
}

/* ── flow panes ───────────────────────────────────────────────────────── */

function flowPoint(name, b) {
  const t = b.time;
  switch (name) {
    case "perp_cvd":
      return b.perp_cvd === null ? { time: t } : { time: t, value: b.perp_cvd };
    case "spot_cvd":
      return b.spot_cvd === null ? { time: t } : { time: t, value: b.spot_cvd };
    case "divergence":
      return b.divergence === null ? { time: t }
        : { time: t, value: b.divergence, color: b.divergence >= 0 ? UP : DOWN };
    case "oi_delta_pct":
      if (b.oi_delta_pct === null) return { time: t };
      return { time: t, value: b.oi_delta_pct,
               color: b.label === null ? TH().dim : (b.perp_cvd > 0 ? UP : DOWN) };
    case "funding":
      return b.funding === null ? { time: t }
        : { time: t, value: b.funding * 1e4, color: b.funding >= 0 ? UP : DOWN };
    case "basis_bp":
      return b.basis_bp === null ? { time: t } : { time: t, value: b.basis_bp };
  }
  return { time: t };
}

function rebuildPanes(fd) {
  for (const s of Object.values(flowSeries)) chart.removeSeries(s);
  flowSeries = {};
  if (chart.removePane) {
    for (let i = chart.panes().length - 1; i >= 1; i--) chart.removePane(i);
  }
  const line = (color, pane) => chart.addSeries(LightweightCharts.LineSeries, {
    color, lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
    crosshairMarkerRadius: 3,
  }, pane);
  const hist = (pane) => chart.addSeries(LightweightCharts.HistogramSeries, {
    priceLineVisible: false, lastValueVisible: false, base: 0,
  }, pane);
  // histograms first so the lines draw on top of them
  let p = 1;
  if (fd.spot_source) flowSeries.divergence = hist(p);
  flowSeries.perp_cvd = line(TH().primary, p);
  if (fd.spot_source) flowSeries.spot_cvd = line(TH().secondary, p);
  p++;
  if (fd.oi_source) { flowSeries.oi_delta_pct = hist(p); p++; }
  flowSeries.funding = hist(p);
  if (fd.spot_source) flowSeries.basis_bp = line(TH().secondary, p);
  $("chart").style.height = `${460 + PANE_H * p}px`;
  const panes = chart.panes();
  if (panes[0] && panes[0].setStretchFactor) {
    panes[0].setStretchFactor(3.5);
    for (let i = 1; i < panes.length; i++) panes[i].setStretchFactor(1);
  }
}

function setFlowData(fd) {
  flowBars = fd.bars;
  flowByTime = new Map(fd.bars.map((b) => [b.time, b]));
  flowLast = fd.last_time;
  flowMeta = fd;
  for (const [name, s] of Object.entries(flowSeries)) {
    s.setData(fd.bars.map((b) => flowPoint(name, b)));
  }
  updateLegend(null);
  renderTape(fd);
}

function updateFlowData(fd) {
  for (const b of fd.bars) {
    for (const [name, s] of Object.entries(flowSeries)) s.update(flowPoint(name, b));
    const last = flowBars[flowBars.length - 1];
    if (last && last.time === b.time) flowBars[flowBars.length - 1] = b;
    else if (!last || b.time > last.time) flowBars.push(b);
    flowByTime.set(b.time, b);
  }
  if (fd.last_time) flowLast = fd.last_time;
  flowMeta = fd;
  updateLegend(null);
  renderTape(fd);
}

function fmtSigned(v, nd) {
  if (v === null || v === undefined) return "—";
  return (v > 0 ? "+" : "") + v.toFixed(nd);
}

/* Legend: one line per pane, values follow the crosshair (default: last
   bar). Text wears text tokens; the swatch carries the series identity. */
function updateLegend(time) {
  const box = $("flow-legend");
  if (!flowMeta) return;
  const b = (time !== null && time !== undefined && flowByTime.get(time)) ||
            flowBars[flowBars.length - 1];
  box.replaceChildren();
  if (!b) { box.appendChild(el("span", "dim", "no flow data")); return; }
  const sw = (c) => { const s = el("span", "sw"); s.style.background = c; return s; };
  const txt = (t) => el("span", null, t);
  const val = (t) => el("span", "val", t);
  const row = (parts) => {
    const r = el("span", "lg");
    for (const x of parts) r.appendChild(x);
    box.appendChild(r);
  };
  const p1 = [txt("CVD "), sw(TH().primary), txt("perp "), val(fmtSigned(b.perp_cvd, 1))];
  if (flowMeta.spot_source) {
    p1.push(txt(" · "), sw(TH().secondary), txt("spot "), val(fmtSigned(b.spot_cvd, 1)),
            txt(" · spot−perp "), val(fmtSigned(b.divergence, 1)));
  }
  row(p1);
  if (flowMeta.oi_source) {
    row([txt("ΔOI "), val(b.oi_delta_pct === null ? "—" : fmtSigned(b.oi_delta_pct, 3) + "%"),
         txt(" · OI "), val(b.oi_close === null ? "—" : fmtNum(b.oi_close)),
         txt(" · quadrant "), val(b.label ? QUADRANT[b.label] : "—")]);
  }
  const p3 = [txt("funding "),
              val(b.funding === null ? "—" : fmtSigned(b.funding * 1e4, 2) + " bp/8h")];
  if (flowMeta.spot_source) {
    p3.push(txt(" · "), sw(TH().secondary), txt("basis "),
            val(b.basis_bp === null ? "—" : fmtSigned(b.basis_bp, 1) + " bp"));
  }
  row(p3);
  box.appendChild(el("span", "dim",
    `bar ${fmtIso(new Date(b.time * 1000).toISOString())} · CVD in ${flowMeta.asset}` +
    ` · quadrant needs |CVD| and |ΔOI| above their ${flowMeta.thresholds.pool_days}d p60`));
}

function renderTape(fd) {
  const t = $("tape");
  t.replaceChildren();
  for (const w of ["4h", "24h"]) t.appendChild(el("div", null, fd.tape[w].text));
  const s = $("ssq");
  if (!fd.ssq) {
    s.textContent = fd.asset === "BTC" ? "short_squeeze view: no closed 15m bar"
                                       : "short_squeeze view: BTC only";
    return;
  }
  const g = fd.ssq;
  const pct = (x) => `${Math.round(x * 100)}th pct`;
  s.textContent =
    `short_squeeze sees (15m bar ${fmtIso(new Date(g.bar_ts * 1000).toISOString())}): ` +
    `perp CVD ${pct(g.perp_cvd_pct)} (needs < ${Math.round(g.perp_cvd_pct_max * 100)}th) ` +
    `${g.perp_ok ? "pass" : "fail"} · spot−perp divergence ${pct(g.divergence_pct)} ` +
    `(needs > ${Math.round(g.divergence_pct_min * 100)}th) ${g.div_ok ? "pass" : "fail"}` +
    ` · pool ${g.pool_n} London/NY bars over ${g.pool_days}d`;
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
  updateLegend(param.time);
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

/* ── positioning tiles (dashboard/market.py::positioning) ─────────────── */

const POS_MS = 60000;

function renderPositioning(list) {
  const wrap = $("positioning");
  wrap.replaceChildren();
  for (const pz of list) {
    const L = pz.latest;
    const cb = pz.regime_cb;
    const state = !L ? "missing" : L.stale ? "missing" : (cb && cb.active) ? "degraded" : "info";
    const tile = el("div", `tile state-${state}`);
    const head = el("div", "tile-head");
    head.appendChild(el("span", "tile-name", `${pz.asset} long/short`));
    head.appendChild(el("span", "tile-state",
      !L ? "NO DATA" : L.stale ? "STALE" : (cb && cb.active) ? "CB ACTIVE"
                                                             : (pz.decile_label || "—")));
    tile.appendChild(head);
    if (!L) {
      tile.appendChild(el("div", "tile-row dim", "no rows in ca_long_short_ratio"));
      wrap.appendChild(tile);
      continue;
    }
    tile.appendChild(el("div", "tile-row mono",
      `ratio ${L.ratio} · ${L.long_pct}% of accounts long · ${L.date}` +
      (L.stale ? ` (${fmtAge(L.age_s)} old)` : "")));
    const meter = el("div", "tile-row meter");
    const bar = el("div", "bar");
    const fill = el("div", "bar-fill bar-neutral");
    const rk = pz.pct_rank_365;
    fill.style.width = `${Math.max(2, (rk || 0) * 100)}%`;
    bar.appendChild(fill);
    meter.appendChild(bar);
    meter.appendChild(el("span", null, rk === null ? "warming up (needs 365 rows)"
      : `${Math.round(rk * 100)}th pct of its year — crowd ${rk < 0.5 ? "short" : "long"} vs usual`));
    tile.appendChild(meter);
    const ds = pz.decile_stats.find((d) => d.decile === pz.decile);
    if (ds && ds.n && pz.uncond) {
      tile.appendChild(el("div", "tile-row",
        `20d fwd from ${pz.decile_label} (n=${ds.n}): mean ${fmtSigned(ds.mean_ret_pct, 1)}%` +
        ` · hit ${ds.hit_pct}% · unconditional ${fmtSigned(pz.uncond.mean_ret_pct, 1)}%` +
        ` / ${pz.uncond.hit_pct}%`));
    }
    const c = pz.cpr;
    if (c.reason) {
      tile.appendChild(el("div", "tile-row dim", `CPR gate (${c.date}): ${c.reason}`));
    } else {
      tile.appendChild(el("div", "tile-row",
        `CPR gate (${c.date}): L/S ≤ p20 ${c.ls_ok ? "pass" : "fail"} (${c.ls_ratio} vs ${c.ls_p20})` +
        ` · funding 3d ≤ p20 ${c.fund_ok ? "pass" : "fail"}` +
        ` (${(c.fund_3d * 1e4).toFixed(2)} vs ${(c.fund_p20 * 1e4).toFixed(2)} bp, BTC funding)`));
    }
    if (cb) {
      tile.appendChild(el("div", `tile-row${cb.active ? " warn" : " dim"}`,
        cb.active
          ? `J+ LS circuit breaker ACTIVE until ${cb.until} — forces 'uncertain' ` +
            `(long% 7d shift ${fmtSigned(cb.shift, 1)}, arms below ${cb.threshold})`
          : `J+ LS circuit breaker off (long% 7d shift ${fmtSigned(cb.shift, 1)}, arms below ${cb.threshold})`));
    } else {
      tile.appendChild(el("div", "tile-row dim", "J+ circuit breaker: BTC only"));
    }
    wrap.appendChild(tile);
  }
}

async function pollPositioning() {
  if (document.hidden) return;
  try {
    const list = await Promise.all([
      getJSON("/api/positioning?asset=BTC"), getJSON("/api/positioning?asset=ETH"),
    ]);
    renderPositioning(list);
  } catch (e) {
    $("positioning").replaceChildren(
      el("div", "dim", `positioning unavailable: ${e.message}`));
  }
  loadProfile();
}

/* ── delta by price (dashboard/market.py::profile) ────────────────────── */

function renderProfile(pf) {
  const wrap = $("profile");
  wrap.replaceChildren();
  wrap.appendChild(el("div", "dim",
    `${pf.hours}h taker delta by price · ${pf.asset} · ${pf.buckets.length} buckets of ` +
    `${fmtNum(pf.bucket_width)} · total ${fmtSigned(pf.total_delta, 0)} · bar-range ` +
    `attribution from 15m bars (no live footprint table)`));
  if (!pf.buckets.length) { wrap.appendChild(el("div", "dim", "no bars")); return; }
  const table = el("table", "grid profile");
  let marked = false;
  for (const b of pf.buckets) {                       // high -> low
    const isLast = !marked && pf.last_price !== null &&
      (pf.last_price >= b.lo && (pf.last_price < b.hi || b === pf.buckets[0]));
    if (isLast) marked = true;
    const tr = el("tr", isLast ? "row-last" : "");
    tr.appendChild(el("td", "mono", `${fmtPrice(b.lo)} – ${fmtPrice(b.hi)}`));
    const cell = el("td", "barcell wide");
    const bar = el("div", "bar bar-center");
    const fill = el("div", `bar-fill ${b.delta >= 0 ? "bar-buy" : "bar-sell"}`);
    const w = pf.max_abs ? Math.abs(b.delta) / pf.max_abs * 50 : 0;
    fill.style.width = `${w}%`;
    fill.style.marginLeft = b.delta >= 0 ? "50%" : `${50 - w}%`;
    bar.appendChild(fill);
    cell.appendChild(bar);
    tr.appendChild(cell);
    tr.appendChild(el("td", "mono", fmtSigned(b.delta, 1)));
    tr.appendChild(el("td", "dim", isLast ? "◀ last" : ""));
    table.appendChild(tr);
  }
  wrap.appendChild(table);
}

async function loadProfile() {
  try {
    renderProfile(await getJSON(`/api/profile?asset=${curAsset}`));
  } catch (e) {
    $("profile").replaceChildren(el("div", "dim", `profile unavailable: ${e.message}`));
  }
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
      img.dataset.base = d.entry_chart_url;          // re-themed by applyTheme
      img.src = `${d.entry_chart_url}?theme=${currentTheme()}`;
      img.alt = `entry context chart for ${t.id}`;
      img.onclick = () => window.open(img.src, "_blank");
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
    if (dg.b5_last && dg.b5_last.long_pct !== undefined) {
      const b = dg.b5_last;
      const n = (v, nd) => (v === null || v === undefined) ? "—" : Number(v).toFixed(nd);
      info.appendChild(el("div", "dim",
        `B5 at ${fmtIso(b.bar_ts)}: long% ${n(b.long_pct, 2)} vs p10 ${n(b.lp_p10, 2)} / ` +
        `p90 ${n(b.lp_p90, 2)} → same-bar ${b.b5_same_bar || "none"} · ` +
        `windows long ${b.b5_long_w ? "armed" : "off"} / short ${b.b5_short_w ? "armed" : "off"}`));
    }
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

/* ── theme ────────────────────────────────────────────────────────────── */

function currentTheme() {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

function resolveTheme(mode) {
  if (mode === "light" || mode === "dark") return mode;
  const h = new Date().getHours();
  return (h >= 7 && h < 19) ? "light" : "dark";   // auto: light in the daytime
}

function applyTheme() {
  const t = resolveTheme(themeMode);
  const changed = currentTheme() !== t;
  document.documentElement.dataset.theme = t;
  const btn = $("theme-btn");
  if (btn) {
    btn.textContent = themeMode === "auto"
      ? `theme: auto (${t} now · light 07–19)` : `theme: ${themeMode}`;
  }
  if (!changed) return;
  if (chart) {
    chart.applyOptions(chartOptions());
    const th = TH();
    if (flowSeries.perp_cvd) flowSeries.perp_cvd.applyOptions({ color: th.primary });
    if (flowSeries.spot_cvd) flowSeries.spot_cvd.applyOptions({ color: th.secondary });
    if (flowSeries.basis_bp) flowSeries.basis_bp.applyOptions({ color: th.secondary });
    if (flowSeries.oi_delta_pct) {              // unlabeled bars carry the dim color
      flowSeries.oi_delta_pct.setData(flowBars.map((b) => flowPoint("oi_delta_pct", b)));
    }
    updateLegend(null);
  }
  for (const img of document.querySelectorAll("#bot-info img[data-base]")) {
    img.src = `${img.dataset.base}?theme=${t}`;
  }
}

function initTheme() {
  try { themeMode = localStorage.getItem(THEME_KEY) || "auto"; }
  catch (e) { /* storage blocked: stay on auto */ }
  const q = new URLSearchParams(location.search).get("theme");
  if (q === "light" || q === "dark" || q === "auto") themeMode = q;   // one-off
  const btn = $("theme-btn");
  if (btn) {
    btn.onclick = () => {
      themeMode = { auto: "light", light: "dark", dark: "auto" }[themeMode] || "auto";
      try { localStorage.setItem(THEME_KEY, themeMode); } catch (e) { /* ignore */ }
      applyTheme();
    };
  }
  applyTheme();
  setInterval(applyTheme, 60000);               // auto follows the clock
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
  if (!document.hidden) { pollHealth(); refreshChart(); pollPositioning(); }
});

initTheme();                                    // before the chart reads TH()
pollHealth();
setInterval(pollHealth, HEALTH_MS);
pollPositioning();
setInterval(pollPositioning, POS_MS);
if (initChart()) {
  loadChart().catch((e) => { $("chart").textContent = `chart: ${e.message}`; });
  setInterval(refreshChart, CHART_MS);
}
initBots();

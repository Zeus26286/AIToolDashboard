"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const state = { range: "30", account: "", metric: "cost", data: null };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const usd = (v) => {
  const digits = Math.abs(v) >= 1000 ? 0 : 2;
  return "$" + v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
};
const compact = (v) => {
  if (v >= 1e9) return (v / 1e9).toFixed(v >= 1e10 ? 0 : 1).replace(/\.0$/, "") + "B";
  if (v >= 1e6) return (v / 1e6).toFixed(v >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(v >= 1e4 ? 0 : 1).replace(/\.0$/, "") + "K";
  return String(Math.round(v));
};
const fullNum = (v) => v.toLocaleString();
const multiple = (v) => (v == null ? "–" : v.toFixed(v >= 10 ? 0 : 1) + "×");
const fmtMetric = (v) => (state.metric === "cost" ? usd(v) : compact(v));
const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const shortDay = (s) => new Date(s + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });

// Color follows the account's position in config, never its rank in the current view.
let accountOrder = [];
const colorOf = (id) => `var(--series-${(accountOrder.indexOf(id) % 8) + 1})`;
const accountName = (id) => (state.data.accounts.find((a) => a.id === id) || { name: id }).name;

function rangeParams() {
  const today = new Date();
  const p = new URLSearchParams();
  if (state.range === "month") {
    p.set("start", isoDay(new Date(today.getFullYear(), today.getMonth(), 1)));
  } else if (state.range !== "all") {
    const start = new Date(today);
    start.setDate(start.getDate() - (Number(state.range) - 1));
    p.set("start", isoDay(start));
  }
  p.set("end", isoDay(today));
  if (state.account) p.set("account", state.account);
  return p;
}

async function load() {
  const res = await fetch("/api/summary?" + rangeParams());
  state.data = await res.json();
  if (!accountOrder.length) {
    accountOrder = state.data.accounts.map((a) => a.id);
    const sel = $("account-filter");
    for (const a of state.data.accounts) sel.add(new Option(a.name, a.id));
  }
  render();
}

function render() {
  const d = state.data;
  const t = d.totals;
  $("kpi-cost").textContent = usd(t.cost);
  $("kpi-cost-sub").textContent = `${shortDay(d.range.start)} – ${shortDay(d.range.end)}`;
  $("kpi-fees").textContent = usd(t.fees);
  $("kpi-multiple").textContent = multiple(t.value_multiple);
  $("kpi-tokens").textContent = compact(t.total_tokens);
  $("kpi-tokens-sub").textContent = `${fullNum(t.requests)} requests · ${fullNum(t.sessions)} sessions`;

  const synced = d.last_sync ? new Date(d.last_sync).toLocaleString() : "never";
  $("sync-status").textContent = `Last synced ${synced}`;

  const notices = [];
  if (d.unpriced_models.length) {
    notices.push(`<strong>No API price found</strong> for ${d.unpriced_models.map((m) => esc(m.model)).join(", ")}. ` +
      `Their tokens count toward totals but add $0. Add them under <code>price_overrides</code> in config.json.`);
  }
  if (!d.range.data_first) {
    notices.push("<strong>No usage found yet.</strong> Check that the log paths in config.json exist on this computer, then press Sync now.");
  }
  $("notices").innerHTML = notices.map((n) => `<div class="notice">${n}</div>`).join("");

  $("daily-title").textContent = state.metric === "cost" ? "Daily API-equivalent cost" : "Daily tokens";
  renderLegend();
  renderDaily();
  renderDailyTable();
  renderMonthly();
  renderModelChart();
  renderProjects();
  renderModelTable();
  $("footnote").textContent =
    `Costs are estimates at public API list prices (price table: ${d.pricing_source}). ` +
    "Subscription usage is not billed per token. The API-equivalent shows what the same work would have cost on pay-as-you-go.";
}

function activeAccounts() {
  const ids = new Set();
  for (const day of state.data.daily) for (const id of Object.keys(day.accounts)) ids.add(id);
  return accountOrder.filter((id) => ids.has(id));
}

function renderLegend() {
  const ids = activeAccounts();
  // A single series needs no legend; the chart title names it.
  $("daily-legend").innerHTML = ids.length < 2 ? "" :
    ids.map((id) => `<span><i style="background:${colorOf(id)}"></i>${esc(accountName(id))}</span>`).join("");
}

// A round tick step (1, 2 or 5 × 10^n) so that `ticks` steps cover v.
function niceStep(v, ticks) {
  if (v <= 0) return 1;
  const raw = v / ticks;
  const exp = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 5, 10]) if (m * exp >= raw) return m * exp;
  return 10 * exp;
}

function el(name, attrs, parent) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

// Rounded data-end on top, square at the baseline.
function columnPath(x, y, w, h, r) {
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

function renderDaily() {
  const box = $("daily-chart");
  box.innerHTML = "";
  const days = state.data.daily;
  const ids = activeAccounts();
  const key = state.metric;
  const totals = days.map((d) => ids.reduce((s, id) => s + (d.accounts[id]?.[key] || 0), 0));
  if (!totals.some((v) => v > 0)) {
    box.innerHTML = '<div class="empty">No usage in this range</div>';
    return;
  }

  const width = box.clientWidth || 600;
  const height = 240;
  const m = { top: 12, right: 8, bottom: 26, left: 52 };
  const iw = width - m.left - m.right;
  const ih = height - m.top - m.bottom;
  const step = niceStep(Math.max(...totals), 4);
  const ticks = Math.ceil(Math.max(...totals) / step);
  const max = step * ticks;
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img",
    "aria-label": `${$("daily-title").textContent}, ${days.length} days` }, box);

  const decimals = step < 1 ? Math.min(2, Math.ceil(-Math.log10(step))) : 0;
  for (let i = 0; i <= ticks; i++) {
    const v = step * i;
    const y = m.top + ih - (v / max) * ih;
    if (i > 0) el("line", { x1: m.left, x2: width - m.right, y1: y, y2: y, class: "grid" }, svg);
    const label = el("text", { x: m.left - 8, y: y + 4, "text-anchor": "end" }, svg);
    label.textContent = key === "cost" ? "$" + (max < 1000 ? v.toFixed(decimals) : compact(v)) : compact(v);
  }

  const band = iw / days.length;
  const barW = Math.max(2, Math.min(24, band * 0.7));
  const gap = 2;
  const every = Math.ceil(days.length / Math.max(1, Math.floor(iw / 64)));

  days.forEach((d, i) => {
    const x = m.left + i * band + (band - barW) / 2;
    let y = m.top + ih;
    const segs = ids.filter((id) => (d.accounts[id]?.[key] || 0) > 0);
    segs.forEach((id, j) => {
      const h = ((d.accounts[id][key]) / max) * ih;
      const top = j === segs.length - 1;
      const drawH = Math.max(0, h - (j > 0 ? gap : 0));
      y -= h;
      const yTop = y;
      if (drawH <= 0) return;
      if (top) el("path", { d: columnPath(x, yTop, barW, drawH, 4), fill: colorOf(id) }, svg);
      else el("rect", { x, y: yTop, width: barW, height: drawH, fill: colorOf(id) }, svg);
    });
    if (i % every === 0) {
      const t = el("text", { x: x + barW / 2, y: height - 8, "text-anchor": "middle" }, svg);
      t.textContent = shortDay(d.day);
    }
    const hit = el("rect", { x: m.left + i * band, y: m.top, width: band, height: ih, class: "hit" }, svg);
    hit.addEventListener("mousemove", (ev) => showTip(ev, dayTip(d, ids, totals[i])));
    hit.addEventListener("mouseleave", hideTip);
  });
  el("line", { x1: m.left, x2: width - m.right, y1: m.top + ih, y2: m.top + ih, class: "baseline" }, svg);
}

function dayTip(d, ids, total) {
  const rows = ids.filter((id) => d.accounts[id])
    .map((id) => `<div class="t-row"><span><i style="background:${colorOf(id)}"></i>${esc(accountName(id))}</span><span>${fmtMetric(d.accounts[id][state.metric])}</span></div>`);
  const date = new Date(d.day + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  return `<div class="t-head">${date}</div>${rows.join("") || '<div class="tag">No usage</div>'}` +
    (rows.length > 1 ? `<div class="t-row t-total"><span>Total</span><span>${fmtMetric(total)}</span></div>` : "");
}

function showTip(ev, html) {
  const tip = $("tooltip");
  tip.innerHTML = html;
  tip.hidden = false;
  const pad = 14;
  const r = tip.getBoundingClientRect();
  let x = ev.clientX + pad;
  let y = ev.clientY + pad;
  if (x + r.width > window.innerWidth - 8) x = ev.clientX - r.width - pad;
  if (y + r.height > window.innerHeight - 8) y = ev.clientY - r.height - pad;
  tip.style.left = Math.max(8, x) + "px";
  tip.style.top = Math.max(8, y) + "px";
}
function hideTip() { $("tooltip").hidden = true; }

// leftCols: indexes of text columns; numbers stay right-aligned.
function table(headers, rows, leftCols = [0]) {
  if (!rows.length) return '<div class="empty">Nothing to show</div>';
  const head = headers.map((h, i) => `<th class="${leftCols.includes(i) ? "left" : ""}">${esc(h)}</th>`).join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${rows.map((r) => `<tr>${r.join("")}</tr>`).join("")}</tbody></table>`;
}
const td = (v, cls = "") => `<td class="${cls}">${v}</td>`;
const accountCell = (id) => td(`<span class="swatch" style="background:${colorOf(id)}"></span>${esc(accountName(id))}`, "left");

function renderDailyTable() {
  const ids = activeAccounts();
  const rows = state.data.daily.filter((d) => Object.keys(d.accounts).length).reverse().map((d) => [
    td(esc(d.day), "left"),
    ...ids.map((id) => td(d.accounts[id] ? `${usd(d.accounts[id].cost)} <span class="tag">${compact(d.accounts[id].tokens)}</span>` : "–")),
  ]);
  $("daily-table").innerHTML = table(["Day", ...ids.map(accountName)], rows);
}

function renderMonthly() {
  const rows = state.data.monthly.slice().reverse().map((m) => [
    td(esc(m.month) + (m.partial ? ' <span class="tag">so far</span>' : ""), "left"),
    accountCell(m.account_id),
    td(compact(m.tokens)),
    td(usd(m.fee)),
    td(usd(m.value)),
    td(multiple(m.value_multiple), m.value_multiple >= 1 ? "good" : ""),
  ]);
  $("monthly-table").innerHTML = rows.length ? table(["Month", "Account", "Tokens", "Fee", "API-equivalent", "Value"], rows, [0, 1])
    : '<div class="empty">No subscription accounts in this view</div>';
}

function renderModelChart() {
  const box = $("model-chart");
  box.innerHTML = "";
  const models = state.data.by_model.filter((m) => m.cost > 0).slice(0, 10);
  if (!models.length) {
    box.innerHTML = '<div class="empty">No priced usage in this range</div>';
    return;
  }
  const width = box.clientWidth || 400;
  const rowH = 30;
  const labelW = Math.min(170, width * 0.42);
  const valueW = 64;
  const height = models.length * rowH + 4;
  const max = Math.max(...models.map((m) => m.cost));
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img", "aria-label": "Cost by model" }, box);
  const barMax = width - labelW - valueW - 12;
  models.forEach((m, i) => {
    const y = i * rowH;
    const label = el("text", { x: 0, y: y + rowH / 2 + 4, class: "label" }, svg);
    label.textContent = m.model.length > 26 ? m.model.slice(0, 25) + "…" : m.model;
    const w = Math.max(2, (m.cost / max) * barMax);
    const bh = 14;
    const by = y + (rowH - bh) / 2;
    const r = Math.min(4, w / 2);
    el("path", { d: `M${labelW},${by}H${labelW + w - r}Q${labelW + w},${by} ${labelW + w},${by + r}V${by + bh - r}Q${labelW + w},${by + bh} ${labelW + w - r},${by + bh}H${labelW}Z`, fill: colorOf(m.account_id) }, svg);
    const v = el("text", { x: labelW + w + 6, y: y + rowH / 2 + 4, class: "value" }, svg);
    v.textContent = usd(m.cost);
    const hit = el("rect", { x: 0, y, width, height: rowH, class: "hit" }, svg);
    hit.addEventListener("mousemove", (ev) => showTip(ev,
      `<div class="t-head">${esc(m.model)}</div>` +
      `<div class="t-row"><span><i style="background:${colorOf(m.account_id)}"></i>${esc(accountName(m.account_id))}</span><span>${usd(m.cost)}</span></div>` +
      `<div class="t-row"><span>Tokens</span><span>${compact(m.total_tokens)}</span></div>` +
      `<div class="t-row"><span>Requests</span><span>${fullNum(m.requests)}</span></div>`));
    hit.addEventListener("mouseleave", hideTip);
  });
}

function renderProjects() {
  const rows = state.data.by_project.map((p) => [
    td(esc(p.project), "left"),
    td(compact(p.tokens)),
    td(usd(p.cost)),
  ]);
  $("project-table").innerHTML = table(["Project", "Tokens", "API-equivalent"], rows);
}

function renderModelTable() {
  const rows = state.data.by_model.map((m) => [
    accountCell(m.account_id),
    td(esc(m.model) + (m.priced ? "" : ' <span class="tag">no price</span>'), "left"),
    td(compact(m.tokens.input_tokens)),
    td(compact(m.tokens.output_tokens)),
    td(compact(m.tokens.cache_read_tokens)),
    td(compact(m.tokens.cache_write_tokens + m.tokens.cache_write_1h_tokens)),
    td(compact(m.total_tokens)),
    td(usd(m.cost)),
  ]);
  $("model-table").innerHTML = table(["Account", "Model", "Input", "Output", "Cache read", "Cache write", "Total", "API-equivalent"], rows, [0, 1]);
}

function setPressed(group, attr, value) {
  for (const b of group.querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset[attr] === value));
}

function init() {
  try { state.range = localStorage.getItem("aitd.range") || state.range; } catch (_) {}
  setPressed($("range-presets"), "range", state.range);
  $("range-presets").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    state.range = b.dataset.range;
    try { localStorage.setItem("aitd.range", state.range); } catch (_) {}
    setPressed($("range-presets"), "range", state.range);
    load();
  });
  $("metric-toggle").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || !state.data) return;
    state.metric = b.dataset.metric;
    setPressed($("metric-toggle"), "metric", state.metric);
    render();
  });
  $("account-filter").addEventListener("change", (e) => { state.account = e.target.value; load(); });
  $("sync-btn").addEventListener("click", async () => {
    const btn = $("sync-btn");
    btn.disabled = true;
    btn.textContent = "Syncing…";
    try {
      await fetch("/api/sync", { method: "POST" });
      await load();
    } finally {
      btn.disabled = false;
      btn.textContent = "Sync now";
    }
  });
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => state.data && render(), 150);
  });
  setInterval(() => { if (document.visibilityState === "visible") load(); }, 5 * 60 * 1000);
  load().catch((err) => { $("sync-status").textContent = "Could not reach the server: " + err.message; });
}

init();

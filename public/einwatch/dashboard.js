"use strict";

const el = (id) => document.getElementById(id);
const setText = (id, text) => { el(id).textContent = text; };
const setVisible = (id, visible) => { el(id).hidden = !visible; };
const pageSize = 12;
const sourceLabel = {
  simulated: "Synthetic GPU",
  local_psutil: "Local host",
  inventory_only: "Inventory only",
};
const statusLabel = {
  unmonitored: "Unmonitored", stale: "Stale", critical: "Critical",
  warning: "Warning", healthy: "Healthy", met: "Met", breached: "Breached", pending: "Pending",
};
let currentAsset = "";
let pageCursors = [null];
let pageIndex = 0;
let nextCursor = null;
let historyRequest;
let chartSamples = [];

const utcFormat = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium", timeStyle: "medium", timeZone: "UTC",
});
const utc = (value) => value ? utcFormat.format(new Date(value)) : "—";
const measure = (value, unit) => Number.isFinite(value) ? `${value.toFixed(1)} ${unit}` : "—";

async function read(path, signal) {
  const response = await fetch(path, { headers: { Accept: "application/json" }, signal });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function pill(state) {
  const span = document.createElement("span");
  span.className = "pill";
  span.dataset.state = state;
  span.textContent = statusLabel[state] ?? state;
  return span;
}

function renderRows(id, records, fields) {
  const rows = records.map((record) => {
    const row = document.createElement("tr");
    for (const value of fields(record)) {
      const cell = row.insertCell();
      if (value instanceof Node) cell.append(value);
      else cell.textContent = value;
    }
    return row;
  });
  el(id).replaceChildren(...rows);
}

function renderOverview(health, nodes, alerts, sla) {
  const snapshot = health.mode === "synthetic-demo";
  setText("mode", snapshot
    ? `Synthetic demo · Read-only snapshot as of ${utc(health.snapshot_as_of)} UTC`
    : "Local monitoring · Host measurements and synthetic GPU telemetry");
  setText("asset-count", nodes.length);
  setText("monitored-count", `${nodes.filter((node) => node.telemetry).length} with telemetry`);
  setText("alert-count", alerts.length);
  setText("critical-count", `${alerts.filter((alert) => alert.severity === "critical").length} critical`);
  setText("compliance-rate", sla.compliance_percent === null ? "—" : `${sla.compliance_percent}%`);
  setText("sla-breakdown", `${sla.pending} pending · ${sla.breached} breached`);

  renderRows("inventory-rows", nodes, ({ asset, status }) => [
    `${asset.vendor} ${asset.model}`, pill(status), sourceLabel[asset.telemetry_source],
    asset.warranty_end, asset.eol_date, asset.eosl_date,
  ]);
  setVisible("inventory-table", nodes.length > 0);
  setText("inventory-message", "No assets are available.");
  setVisible("inventory-message", nodes.length === 0);

  const slaByTicket = new Map(sla.tickets.map((ticket) => [ticket.ticket_id, ticket.status]));
  renderRows("alert-rows", alerts, (alert) => [
    alert.summary, alert.asset_id, pill(alert.severity), alert.occurrences,
    utc(alert.due_at), slaByTicket.has(alert.id) ? pill(slaByTicket.get(alert.id)) : "—",
  ]);
  setVisible("alerts-table", alerts.length > 0);
  setText("alerts-message", "No active alerts.");
  setVisible("alerts-message", alerts.length === 0);

  const select = el("asset-select");
  select.replaceChildren(...nodes.map(({ asset }) => new Option(asset.id, asset.id)));
  select.disabled = !nodes.length;
  if (!nodes.length) {
    setText("history-message", "No assets are available.");
    return;
  }
  select.value = nodes.find(({ telemetry }) => telemetry)?.asset.id ?? nodes[0].asset.id;
  selectAsset(select.value);
}

async function loadOverview() {
  setVisible("overview-error", false);
  setText("mode", "Loading monitoring source…");
  try {
    const [health, nodes, alerts, sla] = await Promise.all([
      read("/health"), read("/nodes/health"), read("/alerts"), read("/sla/compliance"),
    ]);
    renderOverview(health, nodes, alerts, sla);
  } catch {
    setText("mode", "Monitoring source unavailable");
    setText("overview-message", "The monitoring API could not be reached.");
    setVisible("overview-error", true);
    for (const id of ["inventory-message", "alerts-message", "history-message"]) {
      setText(id, "Overview data unavailable.");
    }
  }
}

function drawChart() {
  if (el("chart-block").hidden) return;

  const canvas = el("chart");
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (!width || !height) return;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.scale(ratio, ratio);
  const values = chartSamples.map((sample) => sample.max_temperature_c);
  const temperatures = values.filter(Number.isFinite);
  const min = Math.floor(Math.min(...temperatures) / 10) * 10 - 5;
  const max = Math.ceil(Math.max(...temperatures) / 10) * 10 + 5;
  const left = 34;
  const right = width - 10;
  const top = 14;
  const bottom = height - 16;
  const x = (index) => values.length === 1 ? (right + left) / 2 : left + index * (right - left) / (values.length - 1);
  const y = (value) => bottom - (value - min) * (bottom - top) / (max - min);

  ctx.font = "12px system-ui";
  ctx.textBaseline = "middle";
  ctx.fillStyle = "#a6b3cc";
  ctx.strokeStyle = "#2b3956";
  for (const value of [min, Math.round((min + max) / 2), max]) {
    ctx.fillText(String(value), 3, y(value));
    ctx.beginPath();
    ctx.moveTo(left, y(value));
    ctx.lineTo(right, y(value));
    ctx.stroke();
  }
  ctx.strokeStyle = "#91c9ff";
  ctx.fillStyle = "#91c9ff";
  ctx.lineWidth = 2;
  for (const [index, value] of values.entries()) {
    if (!Number.isFinite(value)) continue;
    const pointX = x(index);
    const pointY = y(value);
    if (Number.isFinite(values[index - 1])) {
      ctx.beginPath();
      ctx.moveTo(x(index - 1), y(values[index - 1]));
      ctx.lineTo(pointX, pointY);
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.arc(pointX, pointY, 3, 0, Math.PI * 2);
    ctx.fill();
  }
}

function updatePagination(loading = false) {
  el("newer").disabled = loading || pageIndex === 0;
  el("older").disabled = loading || nextCursor === null;
  setText("page-number", `Page ${pageIndex + 1}`);
}

async function loadHistory(index) {
  historyRequest?.abort();
  const request = new AbortController();
  historyRequest = request;
  updatePagination(true);
  setText("history-message", "Loading telemetry…");
  setVisible("retry-history", false);
  const params = new URLSearchParams({ limit: pageSize });
  const cursor = pageCursors[index];
  if (cursor !== null) params.set("before_id", cursor);

  try {
    const page = await read(`/nodes/${encodeURIComponent(currentAsset)}/telemetry?${params}`, request.signal);
    if (request.signal.aborted) return;
    pageIndex = index;
    nextCursor = page.next_before_id;
    if (nextCursor !== null) pageCursors[index + 1] = nextCursor;

    renderRows("sample-rows", page.samples, (sample) => [
      utc(sample.observed_at), sourceLabel[sample.source] ?? sample.source,
      measure(sample.max_temperature_c, "°C"), measure(sample.node_power_kw, "kW"),
      measure(sample.cpu_percent, "%"),
    ]);
    chartSamples = page.samples.slice().reverse();
    setVisible("chart-block", chartSamples.some((sample) => Number.isFinite(sample.max_temperature_c)));
    drawChart();
    setVisible("history-table", page.samples.length > 0);
    setText("history-message", page.samples.length
      ? `${page.samples.length} samples · latest recorded first`
      : "No telemetry has been recorded for this asset.");
  } catch {
    if (request.signal.aborted) return;
    setText("history-message", "Telemetry could not be loaded.");
    setVisible("retry-history", true);
    el("retry-history").onclick = () => loadHistory(index);
  } finally {
    if (!request.signal.aborted) updatePagination();
  }
}

function selectAsset(assetId) {
  currentAsset = assetId;
  pageCursors = [null];
  pageIndex = 0;
  nextCursor = null;
  setVisible("history-table", false);
  setVisible("chart-block", false);
  loadHistory(0);
}

el("asset-select").addEventListener("change", (event) => selectAsset(event.target.value));
el("newer").addEventListener("click", () => loadHistory(pageIndex - 1));
el("older").addEventListener("click", () => loadHistory(pageIndex + 1));
el("retry-overview").addEventListener("click", loadOverview);
new ResizeObserver(drawChart).observe(el("chart"));
loadOverview();

const API_BASE = `${window.location.origin}`;

const SEGMENT_ROUTE_LOOKUP = {
  "SUB->Quad": "S1",
  "Quad->CAB": "S2",
  "CAB->Library": "S3",
  "SUB->Library": "S4",
  "Library->HUB": "S5",
  "CAB->HUB": "S6",
};

const LOOP_SEQUENCE = ["prediction", "routing", "reporting", "treatment", "recalculation"];
const SHARED_HORIZON_KEY = "frostflow:shared-horizon";
const SHARED_SYNC_KEY = "frostflow:sync-event";
const SHARED_OVERLAY_KEY = "frostflow:overlay-options";
const syncChannel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("frostflow-sync") : null;

const COLOR_BY_STATUS = {
  red: "#e14f56",
  yellow: "#f2bb39",
  blue: "#4fb6ff",
  teal: "#2fb8af",
  green: "#2fbf7e",
};

let safestMode = true;
let selectedSegmentId = null;
let currentRouteNodes = [];
let currentSegments = [];
let refreshTimer = null;
let refreshToken = 0;
const controlProgress = [];
let pollingHandle = null;

function selectedHorizon() {
  return Number(document.getElementById("timeline").value);
}

function markLoopStep(step) {
  if (!LOOP_SEQUENCE.includes(step)) return;
  if (!controlProgress.includes(step)) controlProgress.push(step);
  renderLoopSteps();
}

function renderLoopSteps() {
  const firstIncomplete = LOOP_SEQUENCE.find((step) => !controlProgress.includes(step));
  LOOP_SEQUENCE.forEach((step) => {
    const el = document.getElementById(`loop-${step}`);
    if (!el) return;
    el.classList.remove("completed", "active");
    if (controlProgress.includes(step)) el.classList.add("completed");
    else if (step === firstIncomplete) el.classList.add("active");
  });
}

function readableStatus(status) {
  const key = String(status || "clear");
  const friendly = {
    confirmed_hazard: "Confirmed Hazard",
    caution: "Caution",
    treated_monitor: "Preferred Route",
    treated_stable: "Treated Stable",
    clear: "Clear",
  };
  return friendly[key] || key.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function riskColor(segment) {
  if (segment?.display_color && COLOR_BY_STATUS[segment.display_color]) return COLOR_BY_STATUS[segment.display_color];
  if ((segment?.risk_score || 0) >= 0.8) return COLOR_BY_STATUS.red;
  if ((segment?.risk_score || 0) >= 0.55) return COLOR_BY_STATUS.yellow;
  return COLOR_BY_STATUS.green;
}

function segmentName(segmentId) {
  return currentSegments.find((seg) => seg.segment_id === segmentId)?.name || segmentId;
}

function publishSync(type, payload = {}) {
  const event = { type, payload, ts: Date.now() };
  localStorage.setItem(SHARED_SYNC_KEY, JSON.stringify(event));
  if (syncChannel) syncChannel.postMessage(event);
}

function applySharedHorizon() {
  const shared = Number(localStorage.getItem(SHARED_HORIZON_KEY));
  if (!Number.isNaN(shared) && shared >= 0 && shared <= 24) {
    document.getElementById("timeline").value = String(shared);
    document.getElementById("timelineLabel").textContent = String(shared);
  }
}

function currentOverlayOptions() {
  const defaults = { hazard: true, treated: true, drainage: true, shading: true };
  const raw = localStorage.getItem(SHARED_OVERLAY_KEY);
  if (!raw) return defaults;
  try {
    const parsed = JSON.parse(raw);
    return {
      hazard: typeof parsed.hazard === "boolean" ? parsed.hazard : defaults.hazard,
      treated: typeof parsed.treated === "boolean" ? parsed.treated : defaults.treated,
      drainage: typeof parsed.drainage === "boolean" ? parsed.drainage : defaults.drainage,
      shading: typeof parsed.shading === "boolean" ? parsed.shading : defaults.shading,
    };
  } catch {
    return defaults;
  }
}

function renderOverlayHint() {
  const overlay = currentOverlayOptions();
  const text = `Overlay profile from Facilities: hazard ${overlay.hazard ? "on" : "off"}, treated ${overlay.treated ? "on" : "off"}, drainage ${overlay.drainage ? "on" : "off"}, shading ${overlay.shading ? "on" : "off"}.`;
  const routeText = document.getElementById("routeText");
  if (routeText.textContent.startsWith("Plan a route")) {
    routeText.textContent = text;
  }
}

function currentOverlayParams() {
  const overlay = currentOverlayOptions();
  return {
    hazard_layer: String(overlay.hazard),
    treated_layer: String(overlay.treated),
    drainage_layer: String(overlay.drainage),
    shading_layer: String(overlay.shading),
  };
}

function renderRouteOutput(body) {
  const headline = document.getElementById("routeHeadline");
  const text = document.getElementById("routeText");
  const chips = document.getElementById("routePathChips");

  headline.textContent = safestMode ? "Safest route locked" : "Shortest route locked";
  text.textContent = `${body.explanation} Weighted cost: ${body.weighted_cost}`;

  chips.innerHTML = "";
  (body.nodes || []).forEach((node, idx) => {
    const chip = document.createElement("span");
    chip.className = "route-chip";
    chip.textContent = node;
    chips.appendChild(chip);
    if (idx < body.nodes.length - 1) {
      const arrow = document.createElement("span");
      arrow.className = "route-arrow";
      arrow.textContent = ">";
      chips.appendChild(arrow);
    }
  });
}

async function fetchRiskMap() {
  const response = await fetch(`${API_BASE}/risk-map?horizon_hours=${selectedHorizon()}`);
  if (!response.ok) throw new Error("Could not fetch /risk-map");
  return response.json();
}

function renderNodes(nodes) {
  const start = document.getElementById("startSelect");
  const end = document.getElementById("endSelect");
  const currentStart = start.value;
  const currentEnd = end.value;

  start.innerHTML = "";
  end.innerHTML = "";

  nodes.forEach((node) => {
    const startOpt = document.createElement("option");
    startOpt.value = node;
    startOpt.textContent = node;
    const endOpt = document.createElement("option");
    endOpt.value = node;
    endOpt.textContent = node;
    start.appendChild(startOpt);
    end.appendChild(endOpt);
  });

  if (nodes.length) {
    start.value = nodes.includes(currentStart) ? currentStart : nodes[0];
    end.value = nodes.includes(currentEnd) ? currentEnd : nodes[nodes.length - 1];
  }
}

function clearRouteStyles() {
  document.querySelectorAll(".segment").forEach((line) => line.classList.remove("route"));
}

function drawRoute(path) {
  clearRouteStyles();
  currentRouteNodes = Array.isArray(path) ? path : [];
  for (let i = 0; i < currentRouteNodes.length - 1; i += 1) {
    const a = currentRouteNodes[i];
    const b = currentRouteNodes[i + 1];
    const segId = SEGMENT_ROUTE_LOOKUP[`${a}->${b}`] || SEGMENT_ROUTE_LOOKUP[`${b}->${a}`];
    if (segId) document.getElementById(segId)?.classList.add("route");
  }
}

function renderSegmentDetails(segment) {
  const roles = [];
  if (segment.emergency_route) roles.push("emergency");
  if (segment.accessible_route) roles.push("accessible");
  if (segment.main_corridor) roles.push("corridor");

  document.getElementById("segmentDetails").textContent =
    `${segment.name} | ${readableStatus(segment.status)} | risk ${segment.risk_score} `
    + `| surface ${segment.surface_type}, slope ${segment.slope_pct}% `
    + `| drainage ${segment.drainage_quality}, shading ${segment.shading_exposure} `
    + `| roles: ${roles.join(", ") || "none"} | ${segment.reason}`;
}

function renderRiskSegments(segments) {
  currentSegments = segments;
  document.querySelectorAll(".segment").forEach((line) => line.classList.remove("selected"));

  segments.forEach((segment) => {
    const line = document.getElementById(segment.segment_id);
    if (!line) return;
    line.style.stroke = riskColor(segment);
    line.style.strokeOpacity = "0.9";
    line.style.strokeWidth = "10";
    line.style.cursor = "pointer";
    line.onclick = () => {
      selectedSegmentId = segment.segment_id;
      renderSegmentDetails(segment);
      document.querySelectorAll(".segment").forEach((l) => l.classList.remove("selected"));
      line.classList.add("selected");
    };
  });

  if (selectedSegmentId) {
    const selected = segments.find((seg) => seg.segment_id === selectedSegmentId);
    if (selected) {
      renderSegmentDetails(selected);
      document.getElementById(selected.segment_id)?.classList.add("selected");
    }
  }
}

function renderTimeline(segments) {
  const body = document.getElementById("timelineBody");
  body.innerHTML = "";
  [...segments]
    .sort((a, b) => b.risk_peak_score - a.risk_peak_score)
    .forEach((segment) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${segment.name}</td>
        <td><span class="badge ${segment.display_color || "green"}">${readableStatus(segment.status)}</span></td>
        <td>+${segment.risk_peak_hour}h (${segment.risk_peak_score})</td>
        <td>+${segment.recommended_pretreat_hour}h</td>
      `;
      body.appendChild(tr);
    });
}

async function planRoute() {
  const overlayParams = currentOverlayParams();
  const params = new URLSearchParams({
    start: document.getElementById("startSelect").value,
    end: document.getElementById("endSelect").value,
    safest: String(safestMode),
    avoid_steep: "false",
    prefer_cleared: String(document.getElementById("preferClearedToggle").checked),
    horizon_hours: String(selectedHorizon()),
    ...overlayParams,
  });
  const response = await fetch(`${API_BASE}/route?${params.toString()}`);
  const body = await response.json();
  if (!response.ok) {
    document.getElementById("routeText").textContent = `Route error: ${body.detail || "unknown"}`;
    return;
  }
  markLoopStep("routing");
  drawRoute(body.nodes);
  renderRouteOutput(body);
  publishSync("route_planned", { nodes: body.nodes });
}

async function submitReport(reportType) {
  if (!selectedSegmentId) {
    document.getElementById("reportStatus").textContent = "Select a segment first.";
    return;
  }
  const response = await fetch(`${API_BASE}/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segment_id: selectedSegmentId, report_type: reportType }),
  });
  if (!response.ok) {
    document.getElementById("reportStatus").textContent = `Could not save report (${response.status}).`;
    return;
  }
  markLoopStep("reporting");
  document.getElementById("reportStatus").textContent = `${reportType} report submitted for ${segmentName(selectedSegmentId)}.`;
  publishSync("report_submitted", { segment_id: selectedSegmentId, report_type: reportType });
  await refreshAll({ fromFeedback: true });
}

async function refreshAll({ fromFeedback = false } = {}) {
  const token = ++refreshToken;
  document.getElementById("timelineLabel").textContent = String(selectedHorizon());
  const risk = await fetchRiskMap();
  if (token !== refreshToken) return;

  markLoopStep("prediction");
  if (fromFeedback) markLoopStep("recalculation");

  document.getElementById("warningBanner").textContent = risk.active_warning;
  renderNodes(risk.nodes || []);
  renderRiskSegments(risk.segments || []);
  renderTimeline(risk.segments || []);
  if (currentRouteNodes.length > 1) drawRoute(currentRouteNodes);
}

function attachActions() {
  document.getElementById("safestBtn").onclick = () => {
    safestMode = true;
    document.getElementById("safestBtn").classList.add("active");
    document.getElementById("shortestBtn").classList.remove("active");
  };
  document.getElementById("shortestBtn").onclick = () => {
    safestMode = false;
    document.getElementById("shortestBtn").classList.add("active");
    document.getElementById("safestBtn").classList.remove("active");
  };
  document.getElementById("timeline").oninput = () => {
    localStorage.setItem(SHARED_HORIZON_KEY, document.getElementById("timeline").value);
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => refreshAll(), 150);
  };
  document.getElementById("routeBtn").onclick = () => planRoute();
  document.querySelectorAll("[data-report]").forEach((button) => {
    button.onclick = () => submitReport(button.getAttribute("data-report"));
  });

  window.addEventListener("storage", (event) => {
    if (event.key === SHARED_HORIZON_KEY && event.newValue) {
      document.getElementById("timeline").value = event.newValue;
      refreshAll();
    }
    if (event.key === SHARED_SYNC_KEY && event.newValue) {
      refreshAll();
    }
    if (event.key === SHARED_OVERLAY_KEY && event.newValue) {
      renderOverlayHint();
    }
  });

  if (syncChannel) {
    syncChannel.onmessage = () => {
      renderOverlayHint();
      refreshAll();
    };
  }
}

async function boot() {
  applySharedHorizon();
  renderOverlayHint();
  attachActions();
  renderLoopSteps();
  try {
    await refreshAll();
    pollingHandle = setInterval(() => refreshAll(), 15000);
  } catch (error) {
    document.getElementById("warningBanner").textContent = "Failed to connect to API.";
  }
}

boot();

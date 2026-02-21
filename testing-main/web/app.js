const API_BASE = `${window.location.origin}`;

const SEGMENT_ROUTE_LOOKUP = {
  "SUB->Quad": "S1",
  "Quad->CAB": "S2",
  "CAB->Library": "S3",
  "SUB->Library": "S4",
  "Library->HUB": "S5",
  "CAB->HUB": "S6",
};

const COLOR_BY_STATUS = {
  red: "#c44536",
  yellow: "#d59a12",
  green: "#2f8f5b",
  blue: "#2d79b7",
};

const LOOP_SEQUENCE = ["prediction", "routing", "reporting", "treatment", "recalculation"];

let safestMode = true;
let currentRiskSegments = [];
let selectedSegmentId = null;
let currentRouteNodes = [];
let refreshToken = 0;
let refreshTimer = null;
let controlProgress = [];
let facilitiesEnabled = true;
let currentMaintenance = null;

function selectedHorizon() {
  return Number(document.getElementById("timeline").value);
}

function readableStatus(status) {
  return String(status || "clear")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function riskToColor(segment) {
  if (segment?.display_color && COLOR_BY_STATUS[segment.display_color]) {
    return COLOR_BY_STATUS[segment.display_color];
  }
  if ((segment?.risk_score || 0) >= 0.8) return COLOR_BY_STATUS.red;
  if ((segment?.risk_score || 0) >= 0.55) return COLOR_BY_STATUS.yellow;
  return COLOR_BY_STATUS.green;
}

function segmentName(segmentId) {
  return currentRiskSegments.find((item) => item.segment_id === segmentId)?.name || segmentId;
}

function markLoopStep(step) {
  if (!LOOP_SEQUENCE.includes(step)) return;
  if (!controlProgress.includes(step)) {
    controlProgress.push(step);
  }
  renderLoopSteps();
}

function renderLoopSteps() {
  const firstIncomplete = LOOP_SEQUENCE.find((step) => !controlProgress.includes(step));
  LOOP_SEQUENCE.forEach((step) => {
    const el = document.getElementById(`loop-${step}`);
    if (!el) return;
    el.classList.remove("completed", "active");
    if (controlProgress.includes(step)) {
      el.classList.add("completed");
    } else if (step === firstIncomplete) {
      el.classList.add("active");
    }
  });
}

function clearRouteStyles() {
  document.querySelectorAll(".segment").forEach((el) => el.classList.remove("route"));
}

function drawRoute(path) {
  clearRouteStyles();
  currentRouteNodes = Array.isArray(path) ? path : [];
  for (let i = 0; i < currentRouteNodes.length - 1; i += 1) {
    const a = currentRouteNodes[i];
    const b = currentRouteNodes[i + 1];
    const key = `${a}->${b}`;
    const reverseKey = `${b}->${a}`;
    const segId = SEGMENT_ROUTE_LOOKUP[key] || SEGMENT_ROUTE_LOOKUP[reverseKey];
    if (segId) document.getElementById(segId)?.classList.add("route");
  }
}

async function fetchRiskMap() {
  const res = await fetch(`${API_BASE}/risk-map?horizon_hours=${selectedHorizon()}`);
  if (!res.ok) throw new Error("Could not fetch /risk-map");
  return res.json();
}

async function fetchMaintenance() {
  const res = await fetch(`${API_BASE}/maintenance-plan?horizon_hours=${selectedHorizon()}`);
  if (!res.ok) throw new Error("Could not fetch /maintenance-plan");
  return res.json();
}

function renderNodes(nodes) {
  const start = document.getElementById("startSelect");
  const end = document.getElementById("endSelect");
  const keepStart = start.value;
  const keepEnd = end.value;

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

  start.value = nodes.includes(keepStart) ? keepStart : nodes[0];
  end.value = nodes.includes(keepEnd) ? keepEnd : nodes[nodes.length - 1];
}

function renderSegmentDetails(segment) {
  const roles = [];
  if (segment.emergency_route) roles.push("emergency");
  if (segment.accessible_route) roles.push("accessible");
  if (segment.main_corridor) roles.push("corridor");
  const roleText = roles.length ? roles.join(", ") : "none";

  document.getElementById("segmentDetails").textContent =
    `${segment.name} | status ${readableStatus(segment.status)} | risk ${segment.risk_score} `
    + `(weather ${segment.weather_risk}, structural ${segment.structural_risk}, reports ${segment.reports_risk}) `
    + `| surface ${segment.surface_type}, slope ${segment.slope_pct}% | drainage ${segment.drainage_quality}, `
    + `shading ${segment.shading_exposure}, foot traffic ${segment.foot_traffic_importance}/5 | critical ${roleText} `
    + `| peak +${segment.risk_peak_hour}h, pre-treat +${segment.recommended_pretreat_hour}h | ${segment.reason}`;
}

function renderRiskSegments(segments, criticalRoutes) {
  currentRiskSegments = segments;
  const emergency = new Set(criticalRoutes?.emergency || []);
  const accessible = new Set(criticalRoutes?.accessible || []);
  const corridors = new Set(criticalRoutes?.main_corridors || []);

  document.querySelectorAll(".segment").forEach((line) => {
    line.classList.remove("route", "selected", "emergency-route", "accessible-route", "corridor-route");
  });

  segments.forEach((segment) => {
    const line = document.getElementById(segment.segment_id);
    if (!line) return;
    line.style.stroke = riskToColor(segment);
    line.style.pointerEvents = "stroke";
    line.style.cursor = "pointer";

    line.classList.toggle("emergency-route", emergency.has(segment.segment_id));
    line.classList.toggle("accessible-route", accessible.has(segment.segment_id));
    line.classList.toggle("corridor-route", corridors.has(segment.segment_id));

    line.onclick = () => {
      selectedSegmentId = segment.segment_id;
      renderSegmentDetails(segment);
      document.querySelectorAll(".segment").forEach((el) => el.classList.remove("selected"));
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

function renderTimelinePreview(segments) {
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

function renderCriticalRoutes(criticalRoutes) {
  const mappings = [
    { id: "emergencyRouteList", items: criticalRoutes?.emergency || [] },
    { id: "accessibleRouteList", items: criticalRoutes?.accessible || [] },
    { id: "corridorRouteList", items: criticalRoutes?.main_corridors || [] },
  ];

  mappings.forEach(({ id, items }) => {
    const list = document.getElementById(id);
    list.innerHTML = "";
    items.forEach((segmentId) => {
      const li = document.createElement("li");
      li.textContent = segmentName(segmentId);
      list.appendChild(li);
    });
  });
}

function renderMaintenance(maintenance) {
  currentMaintenance = maintenance;
  const hazardList = document.getElementById("hazardList");
  hazardList.innerHTML = "";
  document.getElementById("maintenanceLoop").textContent =
    `Maintenance loop route: ${(maintenance.treatment_route_nodes || []).join(" -> ")}`;

  maintenance.ranked_segments.forEach((item) => {
    const li = document.createElement("li");
    li.className = "plan-item";
    li.innerHTML = `
      <div class="row split">
        <strong>${item.name}</strong>
        <span class="badge ${item.display_color || "green"}">${readableStatus(item.status)}</span>
      </div>
      <p class="small">Priority ${item.priority_index} | risk ${item.risk_score} | confidence ${Math.round(item.confidence * 100)}%</p>
      <p class="small">Roles: ${(item.critical_roles || []).join(", ") || "none"} | peak +${item.risk_peak_hour}h | pre-treat +${item.recommended_pretreat_hour}h</p>
      <p class="small">Treatment: ${item.recommended_treatment.toUpperCase()} | required ${item.treatment_required_kg} kg | blanket ${item.blanket_treatment_kg} kg | saved ${item.kg_saved_vs_blanket} kg</p>
      <p class="small">Surface ${item.surface_type}, slope ${item.slope_pct}%, drainage ${item.drainage_quality}, shade ${item.shading_exposure}</p>
    `;

    const button = document.createElement("button");
    button.textContent = item.treated ? "Mark untreated" : "Mark treated";
    button.disabled = !facilitiesEnabled;
    button.onclick = async () => {
      if (!facilitiesEnabled) return;
      await fetch(`${API_BASE}/mark-treated`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ segment_id: item.segment_id, treated: !item.treated }),
      });
      markLoopStep("treatment");
      await refreshAll({ fromFeedback: true });
    };

    li.appendChild(button);
    hazardList.appendChild(li);
  });

  const env = maintenance.environmental_metrics;
  document.getElementById("optimizedMass").textContent = `${env.optimized_treatment_mass_kg} kg`;
  document.getElementById("blanketMass").textContent = `${env.blanket_treatment_mass_kg} kg`;
  document.getElementById("materialSaved").textContent = `${env.treatment_mass_saved_kg} kg`;
  document.getElementById("runoffReduction").textContent = `${env.chloride_runoff_reduction_kg} kg (${env.chloride_reduction_pct}%)`;
  document.getElementById("pollutionAvoided").textContent = `${env.pollution_avoided_kg} kg chloride`;
  document.getElementById("sustainabilityIndex").textContent = `${env.sustainability_index}/100`;
}

async function planRoute() {
  const start = document.getElementById("startSelect").value;
  const end = document.getElementById("endSelect").value;
  const avoidSteep = document.getElementById("avoidSteepToggle").checked;
  const preferCleared = document.getElementById("preferClearedToggle").checked;

  const q = new URLSearchParams({
    start,
    end,
    safest: String(safestMode),
    avoid_steep: String(avoidSteep),
    prefer_cleared: String(preferCleared),
    horizon_hours: String(selectedHorizon()),
  });
  const res = await fetch(`${API_BASE}/route?${q.toString()}`);
  const body = await res.json();

  if (!res.ok) {
    document.getElementById("routeText").textContent = `Route error: ${body.detail || "unknown"}`;
    return;
  }

  drawRoute(body.nodes);
  markLoopStep("routing");
  document.getElementById("routeText").textContent = `${body.explanation} Path: ${body.nodes.join(" -> ")} | weighted cost ${body.weighted_cost}`;
}

async function submitReport(type) {
  if (!selectedSegmentId) {
    document.getElementById("reportStatus").textContent = "Select a segment on the map first.";
    return;
  }
  const res = await fetch(`${API_BASE}/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segment_id: selectedSegmentId, report_type: type }),
  });
  if (!res.ok) {
    document.getElementById("reportStatus").textContent = `Could not save report (${res.status}).`;
    return;
  }

  markLoopStep("reporting");
  document.getElementById("reportStatus").textContent = `${type} report saved for ${segmentName(selectedSegmentId)}. Recalculating...`;
  await refreshAll({ fromFeedback: true });
}

function setMaintenanceMode(enabled) {
  facilitiesEnabled = enabled;
  document.getElementById("hazardList").classList.toggle("disabled", !enabled);
  if (currentMaintenance) renderMaintenance(currentMaintenance);
}

async function refreshAll({ fromFeedback = false } = {}) {
  const token = ++refreshToken;
  document.getElementById("timelineLabel").textContent = String(selectedHorizon());
  const risk = await fetchRiskMap();
  if (token !== refreshToken) return;

  markLoopStep("prediction");
  if (fromFeedback) markLoopStep("recalculation");

  document.getElementById("warningBanner").textContent = risk.active_warning;
  renderNodes(risk.nodes);
  renderRiskSegments(risk.segments, risk.critical_routes);
  renderCriticalRoutes(risk.critical_routes);
  renderTimelinePreview(risk.segments);
  if (currentRouteNodes.length > 1) drawRoute(currentRouteNodes);

  const maintenance = await fetchMaintenance();
  if (token !== refreshToken) return;
  renderMaintenance(maintenance);
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
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => refreshAll(), 140);
  };
  document.getElementById("routeBtn").onclick = planRoute;
  document.querySelectorAll("[data-report]").forEach((button) => {
    button.onclick = () => submitReport(button.getAttribute("data-report"));
  });
  document.getElementById("maintenanceToggle").onchange = (event) => {
    setMaintenanceMode(event.target.checked);
  };
}

async function boot() {
  attachActions();
  renderLoopSteps();
  try {
    await refreshAll();
  } catch (err) {
    document.getElementById("warningBanner").textContent = "Failed to connect to API. Start FastAPI and open http://127.0.0.1:8000/web/.";
  }
}

boot();

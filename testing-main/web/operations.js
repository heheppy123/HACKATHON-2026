const API_BASE = `${window.location.origin}`;

const COLOR_BY_STATUS = {
  red: "#e14f56",
  yellow: "#f2bb39",
  blue: "#4fb6ff",
  teal: "#2fb8af",
  green: "#2fbf7e",
};

let selectedSegmentId = null;
let currentSegments = [];
let currentMaintenance = null;
let refreshToken = 0;
let refreshTimer = null;

function selectedHorizon() {
  return Number(document.getElementById("timeline").value);
}

function layerState() {
  return {
    hazard: document.getElementById("layerHazard").checked,
    treated: document.getElementById("layerTreated").checked,
    emergency: document.getElementById("layerEmergency").checked,
    accessible: document.getElementById("layerAccessible").checked,
    corridor: document.getElementById("layerCorridor").checked,
    drainage: document.getElementById("layerDrainage").checked,
    shading: document.getElementById("layerShading").checked,
  };
}

function readableStatus(status) {
  return String(status || "clear")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
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

async function fetchRiskMap() {
  const response = await fetch(`${API_BASE}/risk-map?horizon_hours=${selectedHorizon()}`);
  if (!response.ok) throw new Error("Could not fetch /risk-map");
  return response.json();
}

async function fetchMaintenance() {
  const storm = document.getElementById("stormModeToggle").checked;
  const response = await fetch(`${API_BASE}/maintenance-plan?horizon_hours=${selectedHorizon()}&storm_mode=${storm}`);
  if (!response.ok) throw new Error("Could not fetch /maintenance-plan");
  return response.json();
}

async function markTreated(segmentId, treated) {
  const response = await fetch(`${API_BASE}/mark-treated`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segment_id: segmentId, treated }),
  });
  if (!response.ok) throw new Error("Could not update treatment");
}

function renderSegmentDetails(segment) {
  const flags = [];
  if (segment.emergency_route) flags.push("emergency");
  if (segment.accessible_route) flags.push("accessible");
  if (segment.main_corridor) flags.push("corridor");
  if (segment.drainage_quality === "poor") flags.push("drainage watch");
  if (segment.shading_exposure >= 0.65) flags.push("high shading");
  document.getElementById("segmentDetails").textContent =
    `${segment.name} | ${readableStatus(segment.status)} | risk ${segment.risk_score} | `
    + `surface ${segment.surface_type}, slope ${segment.slope_pct}% | `
    + `drainage ${segment.drainage_quality}, shade ${segment.shading_exposure} | `
    + `flags: ${flags.join(", ") || "none"} | ${segment.reason}`;
}

function applyLayerStyles(segment, line, layers) {
  line.classList.remove("selected", "layer-emergency", "layer-accessible", "layer-corridor", "layer-drainage", "layer-shading", "layer-treated");

  const isHazard = segment.status === "confirmed_hazard" || segment.status === "caution";
  let color = "#7ea8d2";
  let width = 8;
  let opacity = 0.45;

  if (layers.hazard && isHazard) {
    color = riskColor(segment);
    width = 12;
    opacity = 0.96;
  } else if (layers.treated && segment.treated) {
    color = "#4fb6ff";
    width = 11;
    opacity = 0.95;
    line.classList.add("layer-treated");
  } else if (!layers.hazard && !layers.treated) {
    color = riskColor(segment);
    opacity = 0.78;
  } else {
    color = riskColor(segment);
    opacity = 0.72;
  }

  if (layers.emergency && segment.emergency_route) line.classList.add("layer-emergency");
  if (layers.accessible && segment.accessible_route) line.classList.add("layer-accessible");
  if (layers.corridor && segment.main_corridor) line.classList.add("layer-corridor");
  if (layers.drainage && segment.drainage_quality === "poor") line.classList.add("layer-drainage");
  if (layers.shading && Number(segment.shading_exposure) >= 0.65) line.classList.add("layer-shading");

  line.style.stroke = color;
  line.style.strokeWidth = String(width);
  line.style.strokeOpacity = String(opacity);
  line.style.cursor = "pointer";
}

function renderRiskSegments(segments) {
  currentSegments = segments;
  const layers = layerState();

  segments.forEach((segment) => {
    const line = document.getElementById(segment.segment_id);
    if (!line) return;
    applyLayerStyles(segment, line, layers);
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

function renderCriticalRoutes(criticalRoutes) {
  const listDefs = [
    { id: "emergencyRouteList", items: criticalRoutes?.emergency || [] },
    { id: "accessibleRouteList", items: criticalRoutes?.accessible || [] },
    { id: "corridorRouteList", items: criticalRoutes?.main_corridors || [] },
  ];
  listDefs.forEach(({ id, items }) => {
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
  const list = document.getElementById("hazardList");
  list.innerHTML = "";

  maintenance.ranked_segments.forEach((item) => {
    const li = document.createElement("li");
    li.className = "plan-item";
    li.innerHTML = `
      <div class="row split">
        <strong>${item.name}</strong>
        <span class="badge ${item.display_color || "green"}">${readableStatus(item.status)}</span>
      </div>
      <p class="small">Priority ${item.priority_index} | risk ${item.risk_score} | confidence ${Math.round(item.confidence * 100)}%</p>
      <p class="small">Treatment ${String(item.recommended_treatment || "").toUpperCase()} | required ${item.treatment_required_kg} kg | saved ${item.kg_saved_vs_blanket} kg</p>
      <p class="small">Surface ${item.surface_type}, slope ${item.slope_pct}%, drainage ${item.drainage_quality}, shade ${item.shading_exposure}</p>
    `;
    const button = document.createElement("button");
    button.textContent = item.treated ? "Mark Untreated" : "Mark Treated";
    button.onclick = async () => {
      await markTreated(item.segment_id, !item.treated);
      await refreshAll();
    };
    li.appendChild(button);
    list.appendChild(li);
  });

  document.getElementById("maintenanceLoop").textContent =
    `Maintenance loop route: ${(maintenance.treatment_route_nodes || []).join(" -> ")}`;

  const env = maintenance.environmental_metrics || {};
  const sustainability = env.sustainability_score ?? env.sustainability_index ?? 0;

  document.getElementById("optimizedMass").textContent = `${env.optimized_treatment_mass_kg ?? 0} kg`;
  document.getElementById("blanketMass").textContent = `${env.blanket_treatment_mass_kg ?? 0} kg`;
  document.getElementById("materialSaved").textContent = `${env.treatment_mass_saved_kg ?? 0} kg`;
  document.getElementById("runoffReduction").textContent = `${env.chloride_runoff_reduction_kg ?? 0} kg`;
  document.getElementById("pollutionAvoided").textContent = `${env.pollution_avoided_kg ?? 0} kg`;
  document.getElementById("sustainabilityIndex").textContent = `${sustainability}/100`;
}

function applyStormModeLabel() {
  const stormOn = document.getElementById("stormModeToggle").checked;
  document.getElementById("stormInfo").textContent = stormOn
    ? "Storm mode on. Emergency corridors should be prioritized first."
    : "Storm mode off. Standard optimization applied.";
}

async function refreshAll() {
  const token = ++refreshToken;
  document.getElementById("timelineLabel").textContent = String(selectedHorizon());
  applyStormModeLabel();

  const [risk, maintenance] = await Promise.all([fetchRiskMap(), fetchMaintenance()]);
  if (token !== refreshToken) return;

  document.getElementById("warningBanner").textContent = risk.active_warning;
  renderRiskSegments(risk.segments || []);
  renderCriticalRoutes(risk.critical_routes || {});
  renderMaintenance(maintenance);
}

function attachActions() {
  document.getElementById("timeline").oninput = () => {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => refreshAll(), 160);
  };
  document.getElementById("stormModeToggle").onchange = () => refreshAll();

  [
    "layerHazard",
    "layerTreated",
    "layerEmergency",
    "layerAccessible",
    "layerCorridor",
    "layerDrainage",
    "layerShading",
  ].forEach((id) => {
    document.getElementById(id).onchange = () => renderRiskSegments(currentSegments);
  });
}

async function boot() {
  attachActions();
  try {
    await refreshAll();
  } catch (error) {
    document.getElementById("warningBanner").textContent = "Failed to connect to API.";
  }
}

boot();

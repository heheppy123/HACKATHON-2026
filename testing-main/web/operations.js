const API_BASE = `${window.location.origin}`;

const COLOR_BY_STATUS = {
  red: "#e14f56",
  yellow: "#f2bb39",
  blue: "#4fb6ff",
  teal: "#2fb8af",
  green: "#2fbf7e",
};
const SHARED_HORIZON_KEY = "frostflow:shared-horizon";
const SHARED_SYNC_KEY = "frostflow:sync-event";
const SHARED_OVERLAY_KEY = "frostflow:overlay-options";
const syncChannel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("frostflow-sync") : null;

let selectedSegmentId = null;
let currentSegments = [];
let currentMaintenance = null;
let refreshToken = 0;
let refreshTimer = null;
let pollingHandle = null;

function selectedHorizon() {
  return Number(document.getElementById("timeline").value);
}

function layerState() {
  return {
    hazard: document.getElementById("layerHazard").checked,
    treated: document.getElementById("layerTreated").checked,
    drainage: document.getElementById("layerDrainage").checked,
    shading: document.getElementById("layerShading").checked,
  };
}

function applySharedOverlays() {
  const raw = localStorage.getItem(SHARED_OVERLAY_KEY);
  if (!raw) return;
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed.hazard === "boolean") document.getElementById("layerHazard").checked = parsed.hazard;
    if (typeof parsed.treated === "boolean") document.getElementById("layerTreated").checked = parsed.treated;
    if (typeof parsed.drainage === "boolean") document.getElementById("layerDrainage").checked = parsed.drainage;
    if (typeof parsed.shading === "boolean") document.getElementById("layerShading").checked = parsed.shading;
  } catch {
    // Ignore malformed storage
  }
}

function persistOverlays() {
  localStorage.setItem(SHARED_OVERLAY_KEY, JSON.stringify(layerState()));
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

async function fetchRiskMap() {
  const response = await fetch(`${API_BASE}/risk-map?horizon_hours=${selectedHorizon()}`);
  if (!response.ok) throw new Error("Could not fetch /risk-map");
  return response.json();
}

async function fetchMaintenance() {
  const storm = document.getElementById("stormModeToggle").checked;
  const layers = layerState();
  const params = new URLSearchParams({
    horizon_hours: String(selectedHorizon()),
    storm_mode: String(storm),
    hazard_layer: String(layers.hazard),
    treated_layer: String(layers.treated),
    drainage_layer: String(layers.drainage),
    shading_layer: String(layers.shading),
  });
  const response = await fetch(`${API_BASE}/maintenance-plan?${params.toString()}`);
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
  document.getElementById("segmentDetails").textContent =
    `${segment.name} | ${readableStatus(segment.status)} | risk ${segment.risk_score} | surface ${segment.surface_type}`;
}

function applyLayerStyles(segment, line, layers) {
  line.classList.remove("selected", "layer-drainage", "layer-shading", "layer-treated");

  const statusHazard = segment.status === "confirmed_hazard" || segment.status === "caution";
  const isHazard = statusHazard || segment.hazard_zone;
  const isTreated = segment.status === "treated_monitor" || segment.status === "treated_stable";
  let color = riskColor(segment);
  let width = 9;
  let opacity = 0.45;

  if (layers.treated && isTreated) {
    color = riskColor(segment);
    width = 11;
    opacity = 0.95;
    line.classList.add("layer-treated");
  } else if (layers.hazard && isHazard) {
    color = statusHazard ? riskColor(segment) : "#f0b04c";
    width = 12;
    opacity = 0.96;
  } else {
    opacity = 0.76;
  }

  if (layers.drainage && segment.drainage_zone) line.classList.add("layer-drainage");
  if (layers.shading && segment.shading_zone) line.classList.add("layer-shading");

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
      <p class="small">
        Treatment ${String(item.recommended_treatment || "").toUpperCase()} @ ${item.treatment_rate_value} ${item.treatment_rate_unit}
        on ${item.treated_area_m2} m2
      </p>
      <p class="small">Required ${item.treatment_required_kg} kg | Blanket ${item.blanket_treatment_kg} kg | Saved ${item.kg_saved_vs_blanket} kg</p>
      <p class="small">Material cost $${item.estimated_material_cost} | ${item.engineering_basis}</p>
      <p class="small">Surface ${item.surface_type}, slope ${item.slope_pct}%, drainage ${item.drainage_quality}, shade ${item.shading_exposure}</p>
    `;
    const button = document.createElement("button");
    button.textContent = item.treated ? "Mark Untreated" : "Mark Treated";
    button.onclick = async () => {
      await markTreated(item.segment_id, !item.treated);
      publishSync("treatment_updated", { segment_id: item.segment_id, treated: !item.treated });
      await refreshAll();
    };
    li.appendChild(button);
    list.appendChild(li);
  });

  document.getElementById("maintenanceLoop").textContent =
    `Maintenance loop route: ${(maintenance.treatment_route_nodes || []).join(" -> ")}`;

  const env = maintenance.environmental_metrics || {};
  const sustainability = env.sustainability_score ?? env.sustainability_index ?? 0;
  const treatedCount = env.treated_segments_count ?? 0;

  document.getElementById("optimizedMass").textContent = `${env.optimized_treatment_mass_kg ?? 0} kg`;
  document.getElementById("blanketMass").textContent = `${env.blanket_treatment_mass_kg ?? 0} kg`;
  document.getElementById("materialSaved").textContent = `${env.treatment_mass_saved_kg ?? 0} kg`;
  document.getElementById("runoffReduction").textContent = `${env.chloride_runoff_reduction_kg ?? 0} kg`;
  document.getElementById("pollutionAvoided").textContent = `${env.pollution_avoided_kg ?? 0} kg`;
  document.getElementById("sustainabilityIndex").textContent = `${sustainability}/100`;

  if (!treatedCount) {
    document.getElementById("maintenanceLoop").textContent += " | No treated segments yet; impact metrics remain 0.";
  }
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
  renderMaintenance(maintenance);
}

function attachActions() {
  document.getElementById("timeline").oninput = () => {
    localStorage.setItem(SHARED_HORIZON_KEY, document.getElementById("timeline").value);
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => refreshAll(), 160);
  };
  document.getElementById("stormModeToggle").onchange = () => refreshAll();

  [
    "layerHazard",
    "layerTreated",
    "layerDrainage",
    "layerShading",
  ].forEach((id) => {
    document.getElementById(id).onchange = () => {
      persistOverlays();
      publishSync("overlay_updated", layerState());
      refreshAll();
    };
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
      applySharedOverlays();
      refreshAll();
    }
  });
  if (syncChannel) {
    syncChannel.onmessage = () => {
      applySharedOverlays();
      refreshAll();
    };
  }
}

async function boot() {
  applySharedHorizon();
  applySharedOverlays();
  attachActions();
  try {
    await refreshAll();
    pollingHandle = setInterval(() => refreshAll(), 15000);
  } catch (error) {
    document.getElementById("warningBanner").textContent = "Failed to connect to API.";
  }
}

boot();

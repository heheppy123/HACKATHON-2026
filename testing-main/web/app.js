const API_BASE = `${window.location.origin}`;

const SEGMENT_ROUTE_LOOKUP = {
  "SUB->Quad": "S1",
  "Quad->CAB": "S2",
  "CAB->Library": "S3",
  "SUB->Library": "S4",
  "Library->HUB": "S5",
  "CAB->HUB": "S6",
};

let safestMode = true;
let currentRiskSegments = [];
let selectedSegmentId = null;
let currentRouteNodes = [];
let refreshToken = 0;
let refreshTimer = null;

function riskToColor(risk) {
  if (risk >= 0.8) return "#ff5f7d";
  if (risk >= 0.55) return "#ffcc4d";
  return "#34e7a4";
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
    if (segId) {
      document.getElementById(segId)?.classList.add("route");
    }
  }
}

function selectedHorizon() {
  return Number(document.getElementById("timeline").value);
}

async function fetchRiskMap() {
  const res = await fetch(`${API_BASE}/risk-map?horizon_hours=${selectedHorizon()}`);
  if (!res.ok) throw new Error("Could not fetch /risk-map");
  return res.json();
}

async function fetchMaintenance() {
  const res = await fetch(`${API_BASE}/maintenance-plan?horizon_hours=${selectedHorizon() || 6}`);
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
  nodes.forEach((n) => {
    const s = document.createElement("option");
    s.value = n; s.textContent = n;
    const e = document.createElement("option");
    e.value = n; e.textContent = n;
    start.appendChild(s);
    end.appendChild(e);
  });

  start.value = nodes.includes(keepStart) ? keepStart : nodes[0];
  end.value = nodes.includes(keepEnd) ? keepEnd : nodes[nodes.length - 1];
}

function renderRiskSegments(segments) {
  currentRiskSegments = segments;
  segments.forEach((seg) => {
    const line = document.getElementById(seg.segment_id);
    if (!line) return;
    line.classList.remove("route");
    line.style.stroke = riskToColor(seg.risk_score);
    line.style.pointerEvents = "stroke";
    line.style.cursor = "pointer";
    line.onclick = () => {
      selectedSegmentId = seg.segment_id;
      document.getElementById("segmentDetails").textContent =
        `${seg.name} | risk ${seg.risk_score} | confidence ${Math.round(seg.confidence * 100)}% | reports ${seg.reports_count} | treated ${seg.treated ? "yes" : "no"} | ${seg.reason}`;
    };
  });
}

function renderMaintenance(maintenance) {
  const hazardList = document.getElementById("hazardList");
  hazardList.innerHTML = "";
  maintenance.ranked_segments.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = `${item.name} — risk ${item.risk_score} (confidence ${Math.round(item.confidence * 100)}%)`;

    const btn = document.createElement("button");
    btn.textContent = item.treated ? "Mark Untreated" : "Mark Treated";
    btn.onclick = async () => {
      await fetch(`${API_BASE}/mark-treated`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ segment_id: item.segment_id, treated: !item.treated }),
      });
      await refreshAll();
    };
    li.appendChild(btn);
    hazardList.appendChild(li);
  });

  document.getElementById("rockSalt").textContent = `${maintenance.environmental_metrics.estimated_salt_use_kg} kg`;
  document.getElementById("brineSalt").textContent = `${maintenance.environmental_metrics.brine_equivalent_kg} kg`;
  document.getElementById("reduction").textContent = `${maintenance.environmental_metrics.chloride_reduction_pct}%`;
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
  document.getElementById("routeText").textContent = `${body.explanation} Path: ${body.nodes.join(" → ")} | cost ${body.weighted_cost}`;
}

async function submitReport(type) {
  if (!selectedSegmentId) {
    document.getElementById("reportStatus").textContent = "Select a segment on map first.";
    return;
  }
  const res = await fetch(`${API_BASE}/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segment_id: selectedSegmentId, report_type: type }),
  });
  if (res.ok) {
    document.getElementById("reportStatus").textContent = `${type} report saved for ${selectedSegmentId}.`;
    await refreshAll();
  } else {
    document.getElementById("reportStatus").textContent = `Could not save report (${res.status}).`;
  }
}

function setMaintenanceMode(enabled) {
  document.getElementById("hazardList").style.opacity = enabled ? "1" : "0.35";
}

async function refreshAll() {
  const token = ++refreshToken;
  document.getElementById("timelineLabel").textContent = String(selectedHorizon());
  const risk = await fetchRiskMap();
  if (token !== refreshToken) return;
  document.getElementById("warningBanner").textContent = risk.active_warning;
  renderNodes(risk.nodes);
  renderRiskSegments(risk.segments);
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
    refreshTimer = setTimeout(refreshAll, 120);
  };
  document.getElementById("routeBtn").onclick = planRoute;

  document.querySelectorAll("[data-report]").forEach((btn) => {
    btn.onclick = () => submitReport(btn.getAttribute("data-report"));
  });

  document.getElementById("maintenanceToggle").onchange = (e) => setMaintenanceMode(e.target.checked);
}

async function boot() {
  attachActions();
  try {
    await refreshAll();
  } catch (err) {
    document.getElementById("warningBanner").textContent = "Failed to connect to API. Start FastAPI and open http://127.0.0.1:8000/web/.";
  }
}

boot();

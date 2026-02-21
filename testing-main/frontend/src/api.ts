import type { MaintenanceResponse, RiskMapResponse, RouteResponse } from './types';

const API = 'http://127.0.0.1:8000';

export async function getRiskMap(horizonHours: number): Promise<RiskMapResponse> {
  const r = await fetch(`${API}/risk-map?horizon_hours=${horizonHours}`);
  return r.json();
}

export async function getRoute(params: {
  start: string;
  end: string;
  safest: boolean;
  avoidSteep: boolean;
  preferCleared: boolean;
  horizonHours: number;
}): Promise<RouteResponse> {
  const q = new URLSearchParams({
    start: params.start,
    end: params.end,
    safest: String(params.safest),
    avoid_steep: String(params.avoidSteep),
    prefer_cleared: String(params.preferCleared),
    horizon_hours: String(params.horizonHours),
  });
  const r = await fetch(`${API}/route?${q.toString()}`);
  return r.json();
}

export async function submitReport(segmentId: string, reportType: string): Promise<void> {
  await fetch(`${API}/report`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ segment_id: segmentId, report_type: reportType }),
  });
}

export async function getMaintenancePlan(horizonHours: number): Promise<MaintenanceResponse> {
  const r = await fetch(`${API}/maintenance-plan?horizon_hours=${horizonHours}`);
  return r.json();
}

export async function markTreated(segmentId: string, treated: boolean): Promise<void> {
  await fetch(`${API}/mark-treated`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ segment_id: segmentId, treated }),
  });
}

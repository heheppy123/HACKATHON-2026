import { useEffect, useMemo, useState } from 'react';
import { MapContainer, Marker, Polyline, Popup, TileLayer } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import { getMaintenancePlan, getRiskMap, getRoute, markTreated, submitReport } from './api';
import type { MaintenanceResponse, Segment } from './types';
import './styles.css';

function colorByRisk(risk: number) {
  if (risk >= 0.8) return '#ff4d6d';
  if (risk >= 0.55) return '#ffd166';
  return '#34e7a4';
}

function nodePoint(name: string, segments: Segment[]): [number, number] | null {
  for (const seg of segments) {
    if (seg.start === name) return seg.coords[0];
    if (seg.end === name) return seg.coords[1];
  }
  return null;
}

export default function App() {
  const [segments, setSegments] = useState<Segment[]>([]);
  const [nodes, setNodes] = useState<string[]>([]);
  const [warning, setWarning] = useState('Loading warning...');
  const [horizon, setHorizon] = useState(0);
  const [start, setStart] = useState('SUB');
  const [end, setEnd] = useState('HUB');
  const [safest, setSafest] = useState(true);
  const [avoidSteep, setAvoidSteep] = useState(false);
  const [preferCleared, setPreferCleared] = useState(true);
  const [routeSegments, setRouteSegments] = useState<string[]>([]);
  const [routeText, setRouteText] = useState('Plan a route to see explanation.');
  const [facilitiesMode, setFacilitiesMode] = useState(false);
  const [maintenance, setMaintenance] = useState<MaintenanceResponse | null>(null);

  async function refreshAll(nextHorizon = horizon) {
    const risk = await getRiskMap(nextHorizon);
    setSegments(risk.segments);
    setNodes(risk.nodes);
    setWarning(risk.active_warning);
    const maint = await getMaintenancePlan(nextHorizon || 6);
    setMaintenance(maint);
  }

  useEffect(() => {
    refreshAll(0);
  }, []);

  async function planRoute() {
    const result = await getRoute({ start, end, safest, avoidSteep, preferCleared, horizonHours: horizon });
    setRouteSegments(result.segments);
    setRouteText(`${result.explanation} Path: ${result.nodes.join(' → ')} | Cost ${result.weighted_cost}`);
  }

  async function report(segmentId: string, type: string) {
    await submitReport(segmentId, type);
    await refreshAll();
  }

  const mapCenter = useMemo<[number, number]>(() => [53.5234, -113.5238], []);

  return (
    <div className="app">
      <header className="topbar glass">
        <div>
          <h1>❄️ FrostFlow</h1>
          <p>Predictive Winter Safety Digital Twin</p>
        </div>
        <div className="banner">{warning}</div>
      </header>

      <main className="layout">
        <aside className="panel glass">
          <h2>Student Mode</h2>
          <label>Start</label>
          <select value={start} onChange={(e) => setStart(e.target.value)}>{nodes.map((n) => <option key={n}>{n}</option>)}</select>
          <label>Destination</label>
          <select value={end} onChange={(e) => setEnd(e.target.value)}>{nodes.map((n) => <option key={n}>{n}</option>)}</select>
          <div className="row">
            <button className={safest ? 'active' : ''} onClick={() => setSafest(true)}>Safest</button>
            <button className={!safest ? 'active' : ''} onClick={() => setSafest(false)}>Shortest</button>
          </div>
          <label><input type="checkbox" checked={avoidSteep} onChange={(e) => setAvoidSteep(e.target.checked)} /> Avoid steep paths</label>
          <label><input type="checkbox" checked={preferCleared} onChange={(e) => setPreferCleared(e.target.checked)} /> Prefer cleared paths</label>
          <button onClick={planRoute}>Plan Safe Route</button>
          <p className="muted">{routeText}</p>

          <label>Timeline preview: +{horizon}h</label>
          <input type="range" min={0} max={24} step={6} value={horizon} onChange={async (e) => {
            const val = Number(e.target.value);
            setHorizon(val);
            await refreshAll(val);
          }} />
          <div className="chips"><span>Now</span><span>+6h</span><span>+12h</span><span>+24h</span></div>
        </aside>

        <section className="mapWrap glass">
          <MapContainer center={mapCenter} zoom={17} style={{ height: '70vh', width: '100%' }}>
            <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
            {segments.map((s) => (
              <Polyline key={s.segment_id} positions={s.coords} color={routeSegments.includes(s.segment_id) ? '#5bc0ff' : colorByRisk(s.risk_score)} weight={routeSegments.includes(s.segment_id) ? 10 : 7}>
                <Popup>
                  <strong>{s.name}</strong><br />
                  Risk: {s.risk_score}<br />
                  Reason: {s.reason}<br />
                  Confidence: {Math.round(s.confidence * 100)}%<br />
                  Reports: {s.reports_count}<br />
                  Treatment: {s.treated ? 'Treated' : 'Untreated'}<br />
                  <div className="reportBtns">
                    {['Icy', 'Slushy', 'Clear', 'Salted'].map((t) => <button key={t} onClick={() => report(s.segment_id, t)}>{t}</button>)}
                  </div>
                </Popup>
              </Polyline>
            ))}
            {maintenance?.treatment_route_nodes.map((node, idx) => {
              const p = nodePoint(node, segments);
              if (!p) return null;
              return <Marker key={`${node}-${idx}`} position={p}><Popup>Maintenance loop: {node}</Popup></Marker>;
            })}
          </MapContainer>
        </section>

        <aside className="panel glass">
          <div className="row between">
            <h2>Facilities Mode</h2>
            <label><input type="checkbox" checked={facilitiesMode} onChange={(e) => setFacilitiesMode(e.target.checked)} /> ON</label>
          </div>
          {facilitiesMode && maintenance && (
            <>
              <h3>Ranked Hazards</h3>
              <ol>
                {maintenance.ranked_segments.map((s) => (
                  <li key={s.segment_id}>
                    <b>{s.name}</b> (risk {s.risk_score}, conf {Math.round(s.confidence * 100)}%)
                    <div className="row">
                      <span>ETA ice: {s.eta_to_ice_minutes} min</span>
                      <button onClick={async () => { await markTreated(s.segment_id, !s.treated); await refreshAll(); }}>
                        {s.treated ? 'Mark Untreated' : 'Mark Treated'}
                      </button>
                    </div>
                  </li>
                ))}
              </ol>
              <h3>Environmental Metrics</h3>
              <p>Estimated salt use: {maintenance.environmental_metrics.estimated_salt_use_kg} kg</p>
              <p>Brine equivalent: {maintenance.environmental_metrics.brine_equivalent_kg} kg</p>
              <p>Chloride reduction: {maintenance.environmental_metrics.chloride_reduction_pct}%</p>
            </>
          )}
        </aside>
      </main>
    </div>
  );
}

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .db import execute, init_db
from .engine import FrostFlowEngine, SEGMENT_COORDS

app = FastAPI(title="FrostFlow API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = FrostFlowEngine()
init_db()


class ReportIn(BaseModel):
    segment_id: str
    report_type: str
    lat: float | None = None
    lon: float | None = None


class TreatIn(BaseModel):
    segment_id: str
    treated: bool = True


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/risk-map")
def risk_map(horizon_hours: int = Query(default=0, ge=0, le=24)) -> dict:
    risk = engine.compute_risk_map(horizon_hours)
    segments = []
    for seg in engine.load_segments():
        cond = risk[seg.segment_id]
        segments.append(
            {
                "segment_id": seg.segment_id,
                "name": seg.name,
                "start": seg.start,
                "end": seg.end,
                "coords": SEGMENT_COORDS[seg.segment_id],
                "surface_type": seg.surface_type,
                "slope_pct": seg.slope_pct,
                "drainage_quality": seg.drainage_quality,
                "shading_exposure": seg.shading_exposure,
                "foot_traffic_importance": seg.foot_traffic_importance,
                "emergency_route": seg.emergency_route,
                "accessible_route": seg.accessible_route,
                "main_corridor": seg.main_corridor,
                "wind_corridor": seg.wind_corridor,
                "risk_score": cond.risk_score,
                "weather_risk": cond.weather_risk,
                "structural_risk": cond.structural_risk,
                "reports_risk": cond.reports_risk,
                "treatment_adjustment": cond.treatment_adjustment,
                "confidence": cond.confidence,
                "reason": cond.reason,
                "reports_count": cond.reports_count,
                "treated": cond.treated,
                "status": cond.status,
                "display_color": cond.display_color,
                "risk_peak_hour": cond.risk_peak_hour,
                "risk_peak_score": cond.risk_peak_score,
                "recommended_pretreat_hour": cond.recommended_pretreat_hour,
                "refreeze_likelihood": cond.refreeze_likelihood,
            }
        )

    critical_routes = {
        "emergency": [s["segment_id"] for s in segments if s["emergency_route"]],
        "accessible": [s["segment_id"] for s in segments if s["accessible_route"]],
        "main_corridors": [s["segment_id"] for s in segments if s["main_corridor"]],
    }

    return {
        "horizon_hours": horizon_hours,
        "active_warning": engine.warning_banner(horizon_hours),
        "segments": segments,
        "nodes": sorted({s["start"] for s in segments} | {s["end"] for s in segments}),
        "critical_routes": critical_routes,
        "control_loop": [
            "prediction",
            "routing",
            "reporting",
            "treatment",
            "recalculation",
        ],
    }


@app.get("/route")
def route(
    start: str,
    end: str,
    safest: bool = True,
    avoid_steep: bool = False,
    prefer_cleared: bool = False,
    horizon_hours: int = 0,
) -> dict:
    try:
        out = engine.compute_route(start, end, safest, avoid_steep, prefer_cleared, horizon_hours)
        return out.__dict__
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/report")
def report(payload: ReportIn) -> dict:
    if payload.report_type not in {"Icy", "Slushy", "Clear", "Salted"}:
        raise HTTPException(status_code=400, detail="Invalid report_type")

    execute(
        "INSERT INTO Reports(segment_id, report_type, timestamp, lat, lon) VALUES (?,?,?,?,?)",
        (
            payload.segment_id,
            payload.report_type,
            datetime.now(timezone.utc).isoformat(),
            payload.lat,
            payload.lon,
        ),
    )
    return {"ok": True}


@app.get("/maintenance-plan")
def maintenance_plan(
    horizon_hours: int = Query(default=6, ge=0, le=24),
    storm_mode: bool = Query(default=False),
) -> dict:
    return engine.maintenance_plan(horizon_hours, storm_mode=storm_mode)


@app.get("/timeline-preview")
def timeline_preview() -> dict:
    segments = engine.load_segments()
    now_risk = engine.compute_risk_map(0)
    timeline = engine.timeline_insights(segments=segments, reports=engine.recent_reports())
    payload = []
    for seg in segments:
        current = now_risk[seg.segment_id]
        insight = timeline[seg.segment_id]
        payload.append(
            {
                "segment_id": seg.segment_id,
                "name": seg.name,
                "status": current.status,
                "display_color": current.display_color,
                "current_risk": current.risk_score,
                "peak_hour": insight["peak_hour"],
                "peak_risk": insight["peak_risk"],
                "recommended_pretreat_hour": insight["recommended_pretreat_hour"],
            }
        )
    return {"segments": payload, "hours_considered": [0, 6, 12, 18, 24]}


@app.post("/mark-treated")
def mark_treated(payload: TreatIn) -> dict:
    execute(
        "UPDATE WalkwaySegments SET treatment_status = ? WHERE id = ?",
        (1 if payload.treated else 0, payload.segment_id),
    )
    return {"ok": True, "segment_id": payload.segment_id, "treated": payload.treated}


app.mount("/web", StaticFiles(directory="web", html=True), name="web")


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/web/")


@app.get("/debug/status")
def debug_status() -> dict:
    from .db import fetch_rows

    counts = {
        "segments": fetch_rows("SELECT COUNT(*) AS c FROM WalkwaySegments")[0]["c"],
        "reports": fetch_rows("SELECT COUNT(*) AS c FROM Reports")[0]["c"],
        "weather_rows": fetch_rows("SELECT COUNT(*) AS c FROM WeatherData")[0]["c"],
    }
    return {
        "backend": "running",
        "db": counts,
        "tip": "Open http://127.0.0.1:8000/web/ and avoid file:// web/index.html",
    }

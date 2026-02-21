# FrostFlow: Predictive Winter Safety Digital Twin

Complete full-stack app with dynamic map, live risk updates, route planning, hazard reports, and facilities treatment workflow.

## IMPORTANT: Why it looked non-interactive for you
If you open `web/index.html` directly in Edge using `file:///...`, the app is **not connected to backend APIs**.
That makes it look broken/static.

✅ Correct way: run the backend server, then open `http://127.0.0.1:8000/web/`.

---

## Quick run (copy-paste)

```bash
cd /workspace/testing
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open:
- `http://127.0.0.1:8000/web/` (live UI)
- `http://127.0.0.1:8000/docs` (API docs)

---

## If you still see “Failed to connect to API”

In a second terminal, run:

```bash
source .venv/bin/activate
python -m app.scripts.doctor
```

Expected:
- `✅ Backend reachable`
- `✅ /risk-map...`
- `✅ /maintenance-plan...`
- `✅ /debug/status...`

If doctor says backend is down, restart:

```bash
uvicorn app.main:app --reload
```

---

## What makes it a closed-loop system (real purpose)

1. Model predicts risk (`/risk-map`) from weather + freeze-thaw + reports.
2. User plans safer route (`/route`) using those risks.
3. User submits hazard report (`/report`) on a selected segment.
4. Risk map updates immediately after report.
5. Facilities see ranked hazards + salt/brine impact (`/maintenance-plan`).
6. Facilities mark treated (`/mark-treated`), which lowers future risk.

That feedback cycle is the core innovation.

---

## Stack
- Backend: FastAPI + SQLite + NetworkX
- Frontend options:
  - `web/` vanilla JS live UI served by FastAPI (easy start)
  - `frontend/` React + TypeScript + Leaflet (dev build flow)

## API endpoints
- `GET /risk-map?horizon_hours=0|6|12|24`
- `GET /route?start=SUB&end=HUB&safest=true`
- `POST /report`
- `GET /maintenance-plan`
- `POST /mark-treated`
- `GET /debug/status`

## SQLite tables
- `WalkwaySegments`
- `Reports`
- `WeatherData`

Database file is created as `frostflow.db` automatically at backend startup.

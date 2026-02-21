# FrostFlow 2.0: Campus Winter Operations Decision-Support

FrostFlow is a full-stack winter safety platform for campus infrastructure teams.
This version upgrades the project into a civil and environmental engineering dashboard with a closed-loop control workflow:

`prediction -> routing -> reporting -> treatment -> recalculation`

## What is new

### Civil engineering segment model
- Surface type: `concrete | asphalt | brick | bridge`
- Slope percentage
- Drainage quality: `poor | fair | good`
- Shading exposure (0-1)
- Foot-traffic importance (1-5)
- Critical route flags: emergency, accessible, and main corridor

### Engineering risk model
- Risk components are combined from:
  - weather conditions
  - structural / surface factors
  - crowd reports
  - treatment adjustment
- Report handling:
  - one active icy/slushy report -> `yellow` (caution)
  - two or more active icy/slushy reports -> `red` (confirmed hazard)
  - treated segments are forced into `green` or `blue` states

### Treatment optimization
- Treatment mass is computed from segment length, risk, slope, drainage, and surface behavior.
- Treatment selection:
  - `brine` for moderate risk
  - `salt` for high risk
  - `sand` for extreme cold conditions
- Every segment includes:
  - required treatment mass (kg)
  - blanket-treatment baseline (kg)
  - kg saved vs blanket treatment

### Environmental impact metrics
- Chloride reduction (%)
- Chloride runoff reduction (kg)
- Pollution avoided (kg chloride equivalent)
- Sustainability index

### Timeline and pre-treatment planning
- Segment-level peak risk timing across +0h/+6h/+12h/+18h/+24h
- Recommended pre-treatment hour for each segment

### Engineering dashboard UI (`/web`)
- Risk-state map coloring with critical-route overlays
- Priority maintenance list with treatment actions
- Timeline preview table
- Control-loop tracker with live progression

## API endpoints

- `GET /risk-map?horizon_hours=0..24`
- `GET /route?start=SUB&end=HUB&safest=true...`
- `POST /report`
- `POST /mark-treated`
- `GET /maintenance-plan?horizon_hours=0..24`
- `GET /timeline-preview`
- `GET /debug/status`

## Run locally

```bash
cd testing-main
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open:
- `http://127.0.0.1:8000/web/`
- `http://127.0.0.1:8000/docs`

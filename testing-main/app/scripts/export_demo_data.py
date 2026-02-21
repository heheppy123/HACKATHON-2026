from __future__ import annotations

import json
from pathlib import Path

from app.db import init_db
from app.engine import FrostFlowEngine


def export_demo_data(output_path: str = "web/demo-data.json") -> Path:
    init_db()
    engine = FrostFlowEngine()
    payload = {
        "risk_map": [
            {
                "segment_id": s.segment_id,
                "risk_score": c.risk_score,
                "confidence": c.confidence,
                "reason": c.reason,
            }
            for s, c in [
                (seg, engine.compute_risk_map(0)[seg.segment_id])
                for seg in engine.load_segments()
            ]
        ],
        "route": engine.compute_route("SUB", "HUB", True, True, True).__dict__,
        "maintenance": engine.maintenance_plan(6),
    }
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    path = export_demo_data()
    print(f"Wrote demo data to {path}")

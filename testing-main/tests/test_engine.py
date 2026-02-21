from app.db import execute, init_db
from app.engine import FrostFlowEngine


def setup_function():
    init_db()
    execute("DELETE FROM Reports")


def test_risk_map_shape():
    engine = FrostFlowEngine()
    risk = engine.compute_risk_map(0)
    assert "S1" in risk
    assert 0 <= risk["S1"].risk_score <= 1


def test_report_changes_risk():
    engine = FrostFlowEngine()
    before = engine.compute_risk_map(0)["S1"].risk_score
    execute("INSERT INTO Reports(segment_id, report_type, timestamp) VALUES ('S1','Icy', datetime('now'))")
    after = engine.compute_risk_map(0)["S1"].risk_score
    assert after >= before


def test_route_and_maintenance():
    engine = FrostFlowEngine()
    route = engine.compute_route("SUB", "HUB", safest=True, avoid_steep=True, prefer_cleared=True)
    assert route.nodes[0] == "SUB"
    assert route.nodes[-1] == "HUB"

    plan = engine.maintenance_plan(6)
    assert len(plan["ranked_segments"]) > 0
    assert "chloride_reduction_pct" in plan["environmental_metrics"]

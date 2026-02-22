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
    assert risk["S1"].status in {"clear", "caution", "confirmed_hazard", "treated_stable", "treated_monitor"}
    assert risk["S1"].risk_peak_hour in {0, 6, 12, 18, 24}


def test_startup_defaults_to_clear():
    engine = FrostFlowEngine()
    risk = engine.compute_risk_map(0)
    assert all(condition.status == "clear" for condition in risk.values())


def test_report_feedback_levels():
    engine = FrostFlowEngine()
    baseline = engine.compute_risk_map(0)["S1"]
    execute("INSERT INTO Reports(segment_id, report_type, timestamp) VALUES ('S1','Icy', datetime('now'))")
    after_single = engine.compute_risk_map(0)["S1"]
    execute("INSERT INTO Reports(segment_id, report_type, timestamp) VALUES ('S1','Icy', datetime('now'))")
    after_multiple = engine.compute_risk_map(0)["S1"]

    assert after_single.risk_score >= baseline.risk_score
    assert after_single.status == "caution"
    assert after_multiple.status == "confirmed_hazard"


def test_route_and_maintenance():
    engine = FrostFlowEngine()
    route = engine.compute_route("SUB", "HUB", safest=True, avoid_steep=True, prefer_cleared=True)
    assert route.nodes[0] == "SUB"
    assert route.nodes[-1] == "HUB"

    plan = engine.maintenance_plan(6)
    assert len(plan["ranked_segments"]) > 0
    assert "chloride_reduction_pct" in plan["environmental_metrics"]
    assert "chloride_runoff_reduction_kg" in plan["environmental_metrics"]
    assert plan["ranked_segments"][0]["recommended_treatment"] in {"brine", "salt", "sand"}
    assert "kg_saved_vs_blanket" in plan["ranked_segments"][0]
    assert plan["ranked_segments"][0]["treated_area_m2"] > 0
    assert plan["ranked_segments"][0]["treatment_rate_unit"] in {"g/m2", "mL/m2"}
    assert "material_cost_saved" in plan["environmental_metrics"]

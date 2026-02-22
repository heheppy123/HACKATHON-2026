from __future__ import annotations

from datetime import datetime, timedelta, timezone
from heapq import heappop, heappush
from math import exp
from typing import Dict, List, Tuple

from .db import fetch_rows
from .models import ReportType, RouteResult, Segment, SegmentCondition, WeatherSnapshot

REPORT_IMPACT = {
    ReportType.icy.value: 0.42,
    ReportType.slushy.value: 0.14,
    ReportType.clear.value: -0.18,
    ReportType.salted.value: -0.24,
}

SURFACE_RISK_FACTOR = {
    "bridge": 0.18,
    "brick": 0.12,
    "asphalt": 0.08,
    "concrete": 0.06,
}

DRAINAGE_RISK_FACTOR = {
    "poor": 0.16,
    "fair": 0.08,
    "good": 0.03,
}

STATUS_COLOR = {
    "confirmed_hazard": "red",
    "caution": "yellow",
    "treated_monitor": "blue",
    "treated_stable": "green",
    "clear": "green",
}

CHLORIDE_FACTOR = {
    "salt": 0.61,
    "brine": 0.14,
    "sand": 0.02,
}

BRINE_DENSITY_KG_PER_L = 1.2
BLANKET_SALT_G_PER_M2 = 35.0
RUNOFF_RATIO = 0.42
TIMELINE_STEPS = [0, 6, 12, 18, 24]

TREATMENT_UNIT_COST = {
    "salt": 0.42,
    "brine": 0.18,
    "sand": 0.16,
}

SEGMENT_WIDTH_M = {
    "S1": 5.0,
    "S2": 6.0,
    "S3": 5.5,
    "S4": 7.0,
    "S5": 6.5,
    "S6": 6.0,
}

SEGMENT_COORDS = {
    "S1": [(53.5230, -113.5260), (53.5236, -113.5247)],
    "S2": [(53.5236, -113.5247), (53.5238, -113.5231)],
    "S3": [(53.5238, -113.5231), (53.5232, -113.5218)],
    "S4": [(53.5230, -113.5260), (53.5232, -113.5218)],
    "S5": [(53.5232, -113.5218), (53.5241, -113.5205)],
    "S6": [(53.5238, -113.5231), (53.5241, -113.5205)],
}

SURFACE_TREATMENT_FACTOR = {
    "asphalt": 0.92,
    "concrete": 1.0,
    "brick": 1.12,
    "bridge": 1.18,
}

DRAINAGE_TREATMENT_FACTOR = {
    "good": 0.94,
    "fair": 1.04,
    "poor": 1.16,
}

OVERLAY_HAZARD_SEGMENTS = {"S2", "S3", "S5"}
DEFAULT_OVERLAY_OPTIONS = {
    "hazard": True,
    "treated": True,
    "drainage": True,
    "shading": True,
}


class FrostFlowEngine:
    def __init__(self) -> None:
        self.session_started_at = datetime.now(timezone.utc) - timedelta(seconds=5)

    def load_segments(self) -> List[Segment]:
        rows = fetch_rows("SELECT * FROM WalkwaySegments")
        return [
            Segment(
                segment_id=r["id"],
                name=r["name"],
                start=r["start_node"],
                end=r["end_node"],
                distance_m=r["distance_m"],
                surface_type=r["surface_type"],
                slope_pct=r["slope_pct"],
                drainage_quality=r["drainage_quality"],
                shading_exposure=r["shading_exposure"],
                foot_traffic_importance=r["foot_traffic_importance"],
                shaded=bool(r["shaded"]),
                treated=bool(r["treatment_status"]),
                emergency_route=bool(r["emergency_route"]),
                accessible_route=bool(r["accessible_route"]),
                main_corridor=bool(r["main_corridor"]),
                wind_corridor=bool(r["wind_corridor"]) if "wind_corridor" in r.keys() else False,
            )
            for r in rows
        ]

    def weather_for_horizon(self, horizon_hours: int) -> WeatherSnapshot:
        target = datetime.now(timezone.utc) + timedelta(hours=horizon_hours)
        rows = fetch_rows(
            "SELECT timestamp,temp_c,precip_mm FROM WeatherData ORDER BY ABS(strftime('%s', timestamp) - strftime('%s', ?)) LIMIT 1",
            (target.isoformat(),),
        )
        if not rows:
            return WeatherSnapshot(timestamp=target, temp_c=-1.0, precip_mm=0.0)
        row = rows[0]
        return WeatherSnapshot(self._as_utc(datetime.fromisoformat(row["timestamp"])), row["temp_c"], row["precip_mm"])

    def previous_weather(self) -> WeatherSnapshot:
        rows = fetch_rows("SELECT timestamp,temp_c,precip_mm FROM WeatherData ORDER BY timestamp DESC LIMIT 2")
        if not rows:
            now = datetime.now(timezone.utc)
            return WeatherSnapshot(timestamp=now - timedelta(hours=6), temp_c=0.0, precip_mm=0.0)
        row = rows[1] if len(rows) > 1 else rows[0]
        return WeatherSnapshot(self._as_utc(datetime.fromisoformat(row["timestamp"])), row["temp_c"], row["precip_mm"])

    def recent_reports(self) -> Dict[str, list[dict]]:
        rows = fetch_rows(
            "SELECT segment_id, report_type, timestamp FROM Reports "
            "WHERE strftime('%s', timestamp) >= strftime('%s', 'now', '-24 hours')"
        )
        bucket: Dict[str, list[dict]] = {}
        for row in rows:
            report_ts = self._as_utc(datetime.fromisoformat(row["timestamp"]))
            if report_ts < self.session_started_at:
                continue
            bucket.setdefault(row["segment_id"], []).append(dict(row))
        return bucket

    def compute_risk_map(self, horizon_hours: int = 0) -> Dict[str, SegmentCondition]:
        segments = self.load_segments()
        reports = self.recent_reports()
        weather = self.weather_for_horizon(horizon_hours)
        prev = self.weather_for_horizon(max(horizon_hours - 6, 0)) if horizon_hours >= 6 else self.previous_weather()
        timeline = self.timeline_insights(segments=segments, reports=reports)

        risk_map: Dict[str, SegmentCondition] = {}
        now = datetime.now(timezone.utc)
        for seg in segments:
            risk_map[seg.segment_id] = self._evaluate_segment(
                seg=seg,
                weather=weather,
                previous_weather=prev,
                segment_reports=reports.get(seg.segment_id, []),
                now=now,
                timeline_meta=timeline.get(seg.segment_id),
            )
        return risk_map

    def timeline_insights(
        self,
        segments: List[Segment] | None = None,
        reports: Dict[str, list[dict]] | None = None,
    ) -> Dict[str, dict]:
        segments = segments or self.load_segments()
        reports = reports or self.recent_reports()
        now = datetime.now(timezone.utc)

        weather_steps = {hour: self.weather_for_horizon(hour) for hour in TIMELINE_STEPS}
        prev_steps = {
            0: self.previous_weather(),
            6: weather_steps[0],
            12: weather_steps[6],
            18: weather_steps[12],
            24: weather_steps[18],
        }

        insights: Dict[str, dict] = {}
        for seg in segments:
            peak_hour = 0
            peak_risk = 0.0
            for hour in TIMELINE_STEPS:
                condition = self._evaluate_segment(
                    seg=seg,
                    weather=weather_steps[hour],
                    previous_weather=prev_steps[hour],
                    segment_reports=reports.get(seg.segment_id, []),
                    now=now,
                    timeline_meta=None,
                )
                if condition.risk_score >= peak_risk:
                    peak_risk = condition.risk_score
                    peak_hour = hour

            lead_hours = 3 if peak_risk >= 0.75 else 2 if peak_risk >= 0.55 else 1
            insights[seg.segment_id] = {
                "peak_hour": peak_hour,
                "peak_risk": round(peak_risk, 3),
                "recommended_pretreat_hour": max(0, peak_hour - lead_hours),
            }
        return insights

    def graph(self) -> Dict[str, list[Tuple[str, Segment]]]:
        adj: Dict[str, list[Tuple[str, Segment]]] = {}
        for seg in self.load_segments():
            adj.setdefault(seg.start, []).append((seg.end, seg))
            adj.setdefault(seg.end, []).append((seg.start, seg))
        return adj

    def compute_route(
        self,
        start: str,
        end: str,
        safest: bool,
        avoid_steep: bool,
        prefer_cleared: bool,
        horizon_hours: int = 0,
        overlay_options: dict | None = None,
    ) -> RouteResult:
        adj = self.graph()
        risk_map = self.compute_risk_map(horizon_hours)
        overlay = self._normalize_overlay_options(overlay_options)
        weather = self.weather_for_horizon(horizon_hours)
        if start not in adj or end not in adj:
            raise ValueError("Unknown start/end")

        pq = [(0.0, start)]
        dist = {n: float("inf") for n in adj}
        prev: Dict[str, tuple[str, str] | None] = {n: None for n in adj}
        dist[start] = 0.0

        status_factor = {
            "confirmed_hazard": 1.9,
            "caution": 1.35,
            "treated_monitor": 0.9,
            "treated_stable": 0.74,
            "clear": 1.0,
        }

        while pq:
            cur, node = heappop(pq)
            if cur > dist[node]:
                continue
            if node == end:
                break

            for nxt, seg in adj[node]:
                condition = risk_map[seg.segment_id]
                overlay_modifier = self._overlay_modifier(seg, condition, overlay, weather.temp_c)
                final_risk = self._clamp(condition.risk_score + overlay_modifier)
                base = seg.distance_m
                criticality = self._criticality(seg)

                if safest:
                    base *= 1 + (final_risk * 1.55)
                    base *= status_factor.get(condition.status, 1.0)
                    base *= 1 - (criticality * 0.08)

                    if overlay["treated"] and condition.treated and condition.status in {"treated_monitor", "treated_stable"}:
                        base *= 0.78
                    if seg.accessible_route and seg.slope_pct <= 2.2:
                        base *= 0.9
                    if seg.slope_pct > 4:
                        base *= 1.18
                else:
                    base *= 1 + (final_risk * 0.2)

                if avoid_steep and seg.slope_pct > 3:
                    base *= 1.28

                if prefer_cleared:
                    if condition.status in {"clear", "treated_monitor", "treated_stable"}:
                        base *= 0.9
                    if condition.status in {"caution", "confirmed_hazard"}:
                        base *= 1.12

                flags = self._overlay_flags(seg)
                if overlay["hazard"] and flags["hazard"]:
                    base *= 1.08
                if overlay["drainage"] and flags["drainage"] and condition.refreeze_likelihood >= 0.45:
                    base *= 1.12
                if overlay["shading"] and flags["shading"] and -2 <= weather.temp_c <= 1:
                    base *= 1.08

                new_cost = cur + base
                if new_cost < dist[nxt]:
                    dist[nxt] = new_cost
                    prev[nxt] = (node, seg.segment_id)
                    heappush(pq, (new_cost, nxt))

        if dist[end] == float("inf"):
            raise ValueError("No route")

        nodes: list[str] = []
        segs: list[str] = []
        cursor: str | None = end
        while cursor is not None:
            nodes.append(cursor)
            link = prev[cursor]
            if link is None:
                break
            cursor, seg_id = link
            segs.append(seg_id)
        nodes.reverse()
        segs.reverse()

        explanation = (
            "Safest route uses winter risk, treated-state bonuses, slope/accessibility limits, and corridor criticality."
            if safest
            else "Shortest route prioritizes distance with a light winter-risk penalty."
        )
        return RouteResult(nodes=nodes, segments=segs, weighted_cost=round(dist[end], 2), explanation=explanation)

    def maintenance_plan(
        self,
        horizon_hours: int = 6,
        storm_mode: bool = False,
        overlay_options: dict | None = None,
    ) -> dict:
        segments = self.load_segments()
        risk_map = self.compute_risk_map(horizon_hours)
        weather = self.weather_for_horizon(horizon_hours)
        overlay = self._normalize_overlay_options(overlay_options)

        def segment_priority(seg: Segment) -> float:
            condition = risk_map[seg.segment_id]
            final_risk = self._clamp(
                condition.risk_score + self._overlay_modifier(seg, condition, overlay, weather.temp_c)
            )
            return self._priority_index(seg, condition, storm_mode=storm_mode, risk_score=final_risk)

        ranked = sorted(segments, key=segment_priority, reverse=True)

        ranked_payload = []
        optimized_mass_kg = 0.0
        blanket_mass_kg = 0.0
        total_saved_kg = 0.0
        runoff_reduction_kg = 0.0
        chloride_avoided_kg = 0.0
        chloride_blanket_kg = 0.0
        optimized_cost = 0.0
        blanket_cost = 0.0
        oversalt_penalty_kg = 0.0
        treated_segments_count = 0
        high_risk_total = 0
        high_risk_treated = 0

        for seg in ranked:
            condition = risk_map[seg.segment_id]
            flags = self._overlay_flags(seg)
            overlay_modifier = self._overlay_modifier(seg, condition, overlay, weather.temp_c)
            final_risk = self._clamp(condition.risk_score + overlay_modifier)
            if final_risk > 0.65:
                high_risk_total += 1

            needs_treatment = final_risk > 0.65 or (
                final_risk > 0.45 and overlay["hazard"] and flags["hazard"]
            )

            if overlay["drainage"] and flags["drainage"] and -4 <= weather.temp_c <= 1 and final_risk >= 0.45:
                treatment = "brine"
            else:
                treatment = self._recommended_treatment(final_risk, weather.temp_c)

            treatment_calc = self._treatment_requirement(
                seg,
                condition,
                treatment,
                weather.temp_c,
                risk_score=final_risk,
            )

            dose_multiplier = 1.0
            if overlay["hazard"] and flags["hazard"]:
                dose_multiplier += 0.20
            if overlay["drainage"] and flags["drainage"]:
                dose_multiplier += 0.15
            if overlay["shading"] and flags["shading"]:
                dose_multiplier += 0.10

            planned_required_kg = treatment_calc["required_kg"] * dose_multiplier
            treatment_calc["engineering_basis"].append(
                f"overlay dose multiplier {dose_multiplier:.2f} from active layers"
            )

            if not needs_treatment:
                planned_required_kg = 0.0

            recommended_treatment = treatment if needs_treatment else "none"
            required_kg = round(planned_required_kg, 1)
            blanket_kg = round(self._blanket_salt_mass_kg(seg), 1)
            saved_kg = round(max(0.0, blanket_kg - required_kg), 1)

            applied_kg = 0.0
            applied_treatment = "none"
            if overlay["treated"] and seg.treated:
                treated_segments_count += 1
                high_risk_treated += 1 if final_risk > 0.65 else 0
                applied_treatment = treatment
                applied_kg = round(treatment_calc["required_kg"] * dose_multiplier, 1)

                blanket_mass_kg += blanket_kg
                optimized_mass_kg += applied_kg
                blanket_cost += blanket_kg * TREATMENT_UNIT_COST["salt"]
                optimized_cost += applied_kg * TREATMENT_UNIT_COST.get(applied_treatment, 0.0)

                runoff_fraction = self._segment_runoff_fraction(flags)
                blanket_runoff_mass = runoff_fraction * blanket_kg
                optimized_runoff_mass = runoff_fraction * applied_kg
                blanket_chloride = blanket_runoff_mass * 0.606
                optimized_chloride = optimized_runoff_mass * CHLORIDE_FACTOR.get(applied_treatment, 0.0)
                chloride_reduction = max(0.0, blanket_chloride - optimized_chloride)

                runoff_reduction_kg += chloride_reduction
                chloride_avoided_kg += chloride_reduction
                chloride_blanket_kg += blanket_chloride

                if applied_treatment == "salt" and treatment_calc["adjusted_rate"] > BLANKET_SALT_G_PER_M2:
                    excess_g_m2 = treatment_calc["adjusted_rate"] - BLANKET_SALT_G_PER_M2
                    oversalt_penalty_kg += (treatment_calc["area_m2"] * excess_g_m2) / 1000

            priority = round(self._priority_index(seg, condition, storm_mode=storm_mode, risk_score=final_risk), 3)
            peak_hour = condition.risk_peak_hour
            pretreat_hour = condition.recommended_pretreat_hour
            eta_minutes = 30 if peak_hour <= horizon_hours else int((peak_hour - horizon_hours) * 60)

            roles = []
            if seg.emergency_route:
                roles.append("emergency")
            if seg.accessible_route:
                roles.append("accessible")
            if seg.main_corridor:
                roles.append("main_corridor")

            item_cost = required_kg * TREATMENT_UNIT_COST.get(treatment, 0.0)

            ranked_payload.append(
                {
                    "segment_id": seg.segment_id,
                    "name": seg.name,
                    "risk_score": round(final_risk, 3),
                    "base_risk_score": condition.risk_score,
                    "overlay_modifier": round(overlay_modifier, 3),
                    "confidence": condition.confidence,
                    "status": condition.status,
                    "display_color": condition.display_color,
                    "treated": condition.treated,
                    "priority_index": priority,
                    "critical_roles": roles,
                    "hazard_zone": flags["hazard"],
                    "drainage_zone": flags["drainage"],
                    "shaded_zone": flags["shading"],
                    "surface_type": seg.surface_type,
                    "slope_pct": seg.slope_pct,
                    "drainage_quality": seg.drainage_quality,
                    "shading_exposure": seg.shading_exposure,
                    "foot_traffic_importance": seg.foot_traffic_importance,
                    "risk_peak_hour": peak_hour,
                    "recommended_pretreat_hour": pretreat_hour,
                    "eta_to_ice_minutes": eta_minutes,
                    "recommended_treatment": recommended_treatment,
                    "treated_area_m2": round(treatment_calc["area_m2"], 1),
                    "treatment_rate_value": round(treatment_calc["adjusted_rate"], 1),
                    "treatment_rate_unit": treatment_calc["rate_unit"],
                    "treatment_required_kg": required_kg,
                    "blanket_treatment_kg": blanket_kg,
                    "kg_saved_vs_blanket": saved_kg,
                    "applied_treatment": applied_treatment,
                    "applied_treatment_kg": round(applied_kg, 1),
                    "temperature_factor": round(treatment_calc["temperature_factor"], 3),
                    "surface_factor": round(treatment_calc["surface_factor"], 3),
                    "drainage_factor": round(treatment_calc["drainage_factor"], 3),
                    "slope_safety_factor": round(treatment_calc["slope_factor"], 3),
                    "combined_factor": round(treatment_calc["combined_factor"], 3),
                    "estimated_material_cost": round(item_cost, 2),
                    "engineering_basis": "; ".join(treatment_calc["engineering_basis"]),
                }
            )

        total_saved_kg = max(0.0, blanket_mass_kg - optimized_mass_kg)
        optimized_ratio_pct = (optimized_mass_kg / blanket_mass_kg) * 100 if blanket_mass_kg else 0.0
        saved_ratio_pct = (total_saved_kg / blanket_mass_kg) * 100 if blanket_mass_kg else 0.0
        chloride_reduction_pct = (chloride_avoided_kg / chloride_blanket_kg) * 100 if chloride_blanket_kg else 0.0
        oversalt_penalty_pct = (oversalt_penalty_kg / blanket_mass_kg) * 100 if blanket_mass_kg else 0.0

        if treated_segments_count == 0:
            runoff_reduction_kg = 0.0
            chloride_avoided_kg = 0.0
            chloride_reduction_pct = 0.0
            sustainability_index = 0.0
        else:
            s1 = saved_ratio_pct
            s2 = chloride_reduction_pct
            s3 = (high_risk_treated / high_risk_total) * 100 if high_risk_total else 100.0
            sustainability_index = max(0.0, min(100.0, (0.4 * s1) + (0.4 * s2) + (0.2 * s3) - (0.2 * oversalt_penalty_pct)))

        emergency_segments = [s.segment_id for s in segments if s.emergency_route]
        accessible_segments = [s.segment_id for s in segments if s.accessible_route]
        corridor_segments = [s.segment_id for s in segments if s.main_corridor]
        route_nodes = self._maintenance_route_nodes(ranked)

        return {
            "storm_mode": storm_mode,
            "overlay_options": overlay,
            "ranked_segments": ranked_payload[:6],
            "treatment_route_nodes": route_nodes,
            "critical_routes": {
                "emergency": emergency_segments,
                "accessible": accessible_segments,
                "main_corridors": corridor_segments,
            },
            "environmental_metrics": {
                "optimized_treatment_mass_kg": round(optimized_mass_kg, 1),
                "blanket_treatment_mass_kg": round(blanket_mass_kg, 1),
                "treatment_mass_saved_kg": round(total_saved_kg, 1),
                "chloride_reduction_pct": round(chloride_reduction_pct, 1),
                "chloride_runoff_reduction_kg": round(runoff_reduction_kg, 1),
                "pollution_avoided_kg": round(chloride_avoided_kg, 1),
                "sustainability_index": round(sustainability_index, 1),
                "optimized_to_blanket_ratio_pct": round(optimized_ratio_pct, 1),
                "saved_mass_ratio_pct": round(saved_ratio_pct, 1),
                "estimated_salt_use_kg": round(blanket_mass_kg, 1),
                "brine_equivalent_kg": round(optimized_mass_kg, 1),
                "optimized_material_cost": round(optimized_cost, 2),
                "blanket_material_cost": round(blanket_cost, 2),
                "material_cost_saved": round(max(0.0, blanket_cost - optimized_cost), 2),
                "oversalting_penalty_kg": round(oversalt_penalty_kg, 2),
                "treated_segments_count": treated_segments_count,
            },
        }

    def warning_banner(self, horizon_hours: int) -> str:
        weather = self.weather_for_horizon(horizon_hours)
        prev = self.weather_for_horizon(max(horizon_hours - 6, 0)) if horizon_hours >= 6 else self.previous_weather()

        messages: list[str] = []
        if weather.temp_c <= -12:
            messages.append("Extreme cold: sand recommended")
        if -2 <= weather.temp_c <= 1:
            messages.append("Near freezing: elevated icing risk")
        if weather.precip_mm > 0.3:
            messages.append("Precipitation loading: pretreatment advised")
        if prev.temp_c > 0 and weather.temp_c < 0:
            messages.append("Freeze-thaw refreeze warning")
        if not messages:
            messages.append("Conditions stable")

        return (
            f"Real-Time Winter Risk Conditions | Temp {weather.temp_c:.1f}C | "
            + " | ".join(messages)
        )

    def _evaluate_segment(
        self,
        seg: Segment,
        weather: WeatherSnapshot,
        previous_weather: WeatherSnapshot,
        segment_reports: list[dict],
        now: datetime,
        timeline_meta: dict | None,
    ) -> SegmentCondition:
        weather_risk, weather_reasons = self._weather_component(weather, previous_weather)
        structural_risk, structural_reasons = self._structural_component(seg)
        reports_risk, hazard_reports, icy_reports, slushy_reports, report_reasons = self._report_component(segment_reports, now)
        treatment_adjustment, treatment_reasons = self._treatment_adjustment(seg, weather)

        risk = 0.04 + (0.49 * weather_risk) + (0.31 * structural_risk) + reports_risk + treatment_adjustment
        risk = max(0.0, min(1.0, risk))

        confidence = 0.48 + min(hazard_reports * 0.11, 0.33)
        if abs(weather.temp_c) <= 2:
            confidence += 0.08
        if weather.precip_mm > 0.15:
            confidence += 0.07
        if seg.treated:
            confidence += 0.02
        confidence = max(0.2, min(0.97, confidence))

        status, display_color = self._classify_status(risk, icy_reports, slushy_reports, seg.treated)
        reasons = (weather_reasons + structural_reasons + report_reasons + treatment_reasons)[:6]

        peak_hour = int((timeline_meta or {}).get("peak_hour", 0))
        peak_score = float((timeline_meta or {}).get("peak_risk", risk))
        pretreat_hour = int((timeline_meta or {}).get("recommended_pretreat_hour", 0))

        refreeze_likelihood = 0.16
        if previous_weather.temp_c > 0 and weather.temp_c < 0:
            refreeze_likelihood += 0.4
        if seg.shading_exposure >= 0.6:
            refreeze_likelihood += 0.12
        if seg.drainage_quality == "poor":
            refreeze_likelihood += 0.1
        if seg.wind_corridor:
            refreeze_likelihood += 0.08
        refreeze_likelihood = max(0.0, min(1.0, refreeze_likelihood))

        return SegmentCondition(
            segment_id=seg.segment_id,
            risk_score=round(risk, 3),
            weather_risk=round(weather_risk, 3),
            structural_risk=round(structural_risk, 3),
            reports_risk=round(reports_risk, 3),
            treatment_adjustment=round(treatment_adjustment, 3),
            confidence=round(confidence, 3),
            reason=", ".join(reasons) if reasons else "Baseline winter condition",
            reports_count=hazard_reports,
            treated=seg.treated,
            status=status,
            display_color=display_color,
            risk_peak_hour=peak_hour,
            risk_peak_score=round(peak_score, 3),
            recommended_pretreat_hour=pretreat_hour,
            refreeze_likelihood=round(refreeze_likelihood, 3),
        )

    def _weather_component(self, weather: WeatherSnapshot, prev: WeatherSnapshot) -> tuple[float, list[str]]:
        score = 0.06
        reasons: list[str] = []

        if -4 <= weather.temp_c <= 1:
            score += 0.29
            reasons.append("near-freezing pavement")
        elif weather.temp_c < -4:
            score += 0.14
            reasons.append("sub-freezing surface regime")

        if weather.precip_mm >= 0.1:
            precip_risk = min(0.26, 0.08 + (weather.precip_mm * 0.22))
            score += precip_risk
            reasons.append("active precipitation loading")

        if prev.temp_c > 0 and weather.temp_c < 0:
            score += 0.2
            reasons.append("freeze-thaw refreeze window")

        if prev.temp_c - weather.temp_c >= 4:
            score += 0.06
            reasons.append("rapid temperature drop")

        return min(0.85, score), reasons

    def _structural_component(self, seg: Segment) -> tuple[float, list[str]]:
        surface = SURFACE_RISK_FACTOR.get(seg.surface_type, 0.1)
        slope = min(0.22, max(seg.slope_pct, 0.0) * 0.03)
        drainage = DRAINAGE_RISK_FACTOR.get(seg.drainage_quality, 0.08)
        shade = min(0.18, max(seg.shading_exposure, 0.0) * 0.16)
        traffic = (max(1, seg.foot_traffic_importance) / 5.0) * 0.07

        score = surface + slope + drainage + shade + traffic
        reasons: list[str] = [
            f"{seg.surface_type} surface behavior",
            f"{seg.slope_pct:.1f}% slope loading",
            f"{seg.drainage_quality} drainage",
        ]

        if seg.wind_corridor:
            score += 0.07
            reasons.append("wind corridor refreeze")
        if seg.shading_exposure >= 0.6:
            reasons.append("limited solar melting")
        if seg.accessible_route and seg.slope_pct > 2:
            score += 0.04
            reasons.append("accessibility sensitivity")

        return min(0.82, score), reasons

    def _report_component(self, reports: list[dict], now: datetime) -> tuple[float, int, int, int, list[str]]:
        report_score = 0.0
        hazard_reports = 0
        icy_reports = 0
        slushy_reports = 0
        reasons: list[str] = []

        for report in reports:
            report_time = self._as_utc(datetime.fromisoformat(report["timestamp"]))
            age_hours = (now - report_time).total_seconds() / 3600
            decay = exp(-max(age_hours, 0) / 8)
            report_type = report["report_type"]
            report_score += REPORT_IMPACT.get(report_type, 0.0) * decay

            if age_hours <= 12:
                if report_type == ReportType.icy.value:
                    icy_reports += 1
                elif report_type == ReportType.slushy.value:
                    slushy_reports += 1

            if report_type == ReportType.icy.value:
                reasons.append("user icy report")
            elif report_type == ReportType.slushy.value:
                reasons.append("user slush report")
            elif report_type == ReportType.clear.value:
                reasons.append("clear-condition report")
            elif report_type == ReportType.salted.value:
                reasons.append("salted-condition report")

        hazard_reports = icy_reports + slushy_reports
        report_score = max(-0.25, min(0.45, report_score))
        return report_score, hazard_reports, icy_reports, slushy_reports, reasons

    def _treatment_adjustment(self, seg: Segment, weather: WeatherSnapshot) -> tuple[float, list[str]]:
        if not seg.treated:
            return 0.0, []

        effectiveness = -0.24
        reasons = ["recent treatment effect"]

        if weather.temp_c <= -12:
            effectiveness = -0.09
            reasons.append("de-icer effectiveness reduced in extreme cold")
        elif weather.temp_c <= -8:
            effectiveness = -0.14
            reasons.append("reduced treatment effectiveness")

        if weather.precip_mm > 0.5:
            effectiveness += 0.05
            reasons.append("fresh snowfall dilutes treatment")
        if seg.surface_type == "bridge":
            effectiveness += 0.04
            reasons.append("bridge deck cools quickly")
        if seg.drainage_quality == "poor":
            effectiveness += 0.03
            reasons.append("drainage limits treatment retention")

        return effectiveness, reasons

    def _classify_status(self, risk: float, icy_reports: int, slushy_reports: int, treated: bool) -> tuple[str, str]:
        if icy_reports >= 1:
            return "confirmed_hazard", STATUS_COLOR["confirmed_hazard"]
        if slushy_reports >= 1:
            return "caution", STATUS_COLOR["caution"]

        if treated:
            return "treated_stable", STATUS_COLOR["treated_stable"]

        if risk >= 0.92:
            return "confirmed_hazard", STATUS_COLOR["confirmed_hazard"]
        if risk >= 0.78:
            return "caution", STATUS_COLOR["caution"]
        return "clear", STATUS_COLOR["clear"]

    def _priority_index(
        self,
        seg: Segment,
        condition: SegmentCondition,
        storm_mode: bool = False,
        risk_score: float | None = None,
    ) -> float:
        effective_risk = condition.risk_score if risk_score is None else risk_score
        score = (
            (effective_risk * 0.55)
            + (self._criticality(seg) * 0.28)
            + ((seg.foot_traffic_importance / 5) * 0.17)
        )

        if condition.status == "confirmed_hazard":
            score += 0.12
        elif condition.status == "caution":
            score += 0.05

        if storm_mode:
            if seg.emergency_route:
                score += 0.12
            if seg.accessible_route:
                score += 0.08
            if seg.main_corridor:
                score += 0.06

        if seg.treated and condition.status == "treated_stable":
            score -= 0.08

        return max(0.0, min(1.5, score))

    def _criticality(self, seg: Segment) -> float:
        criticality = 0.0
        if seg.emergency_route:
            criticality += 0.42
        if seg.accessible_route:
            criticality += 0.28
        if seg.main_corridor:
            criticality += 0.24
        criticality += (seg.foot_traffic_importance / 5.0) * 0.12
        return min(1.0, criticality)

    def _recommended_treatment(self, risk_score: float, temp_c: float) -> str:
        if temp_c <= -12:
            return "sand"
        if temp_c <= -6:
            return "salt"
        if risk_score >= 0.78:
            return "salt"
        return "brine"

    def _base_application_rate(self, treatment: str, risk_score: float) -> tuple[float, str]:
        if treatment == "salt":
            return 20 + (20 * max(0.0, min(1.0, risk_score))), "g/m2"
        if treatment == "brine":
            return 10 + (20 * max(0.0, min(1.0, risk_score))), "mL/m2"

        return 20 + (15 * max(0.0, min(1.0, risk_score))), "g/m2"

    def _temperature_factor(self, temp_c: float, treatment: str) -> float:
        if treatment == "sand":
            if temp_c <= -18:
                return 1.05
            if temp_c <= -12:
                return 1.0
            return 0.9

        if treatment == "salt":
            if temp_c <= -12:
                return 1.18
            if temp_c <= -8:
                return 1.1
            if temp_c <= -2:
                return 1.0
            return 0.94

        if temp_c <= -8:
            return 1.15
        if temp_c <= -4:
            return 1.05
        if temp_c <= 0:
            return 1.0
        return 0.92

    def _treatment_requirement(
        self,
        seg: Segment,
        condition: SegmentCondition,
        treatment: str,
        temp_c: float,
        risk_score: float | None = None,
    ) -> dict:
        width_m = SEGMENT_WIDTH_M.get(seg.segment_id, 5.0)
        area_m2 = seg.distance_m * width_m

        effective_risk = condition.risk_score if risk_score is None else risk_score
        base_rate, rate_unit = self._base_application_rate(treatment, effective_risk)
        temperature_factor = self._temperature_factor(temp_c, treatment)
        surface_factor = SURFACE_TREATMENT_FACTOR.get(seg.surface_type, 1.0)
        drainage_factor = DRAINAGE_TREATMENT_FACTOR.get(seg.drainage_quality, 1.0)
        slope_factor = 1.0 + (min(max(seg.slope_pct, 0.0), 8.0) * 0.03)

        combined_factor = temperature_factor * surface_factor * drainage_factor * slope_factor
        adjusted_rate = base_rate * combined_factor

        if treatment == "brine":
            liters = (area_m2 * adjusted_rate) / 1000
            required_kg = liters * BRINE_DENSITY_KG_PER_L
        else:
            required_kg = (area_m2 * adjusted_rate) / 1000

        basis = [
            f"area={area_m2:.1f}m2 from length {seg.distance_m:.1f}m x width {width_m:.1f}m",
            f"base rate {base_rate:.1f} {rate_unit}",
            f"temp factor {temperature_factor:.2f}",
            f"surface factor {surface_factor:.2f}",
            f"drainage factor {drainage_factor:.2f}",
            f"slope factor {slope_factor:.2f}",
        ]

        return {
            "area_m2": area_m2,
            "rate_unit": rate_unit,
            "base_rate": base_rate,
            "adjusted_rate": adjusted_rate,
            "temperature_factor": temperature_factor,
            "surface_factor": surface_factor,
            "drainage_factor": drainage_factor,
            "slope_factor": slope_factor,
            "combined_factor": combined_factor,
            "required_kg": required_kg,
            "engineering_basis": basis,
        }

    def _blanket_salt_mass_kg(self, seg: Segment) -> float:
        width_m = SEGMENT_WIDTH_M.get(seg.segment_id, 5.0)
        area_m2 = seg.distance_m * width_m
        return (area_m2 * BLANKET_SALT_G_PER_M2) / 1000

    def _maintenance_route_nodes(self, ranked: List[Segment]) -> list[str]:
        if not ranked:
            return []
        selected = ranked[:4]
        nodes: list[str] = [selected[0].start]
        for seg in selected:
            if not nodes or nodes[-1] != seg.start:
                nodes.append(seg.start)
            nodes.append(seg.end)
        if nodes[0] != nodes[-1]:
            nodes.append(nodes[0])
        return nodes

    def _normalize_overlay_options(self, overlay_options: dict | None) -> dict:
        merged = dict(DEFAULT_OVERLAY_OPTIONS)
        if overlay_options:
            for key in merged:
                if key in overlay_options:
                    merged[key] = bool(overlay_options[key])
        return merged

    def _overlay_flags(self, seg: Segment) -> dict:
        return {
            "hazard": seg.segment_id in OVERLAY_HAZARD_SEGMENTS or seg.surface_type == "bridge" or seg.slope_pct >= 3.5,
            "drainage": seg.drainage_quality == "poor",
            "shading": seg.shading_exposure >= 0.65,
        }

    def _overlay_modifier(self, seg: Segment, condition: SegmentCondition, overlay: dict, temp_c: float) -> float:
        flags = self._overlay_flags(seg)
        modifier = 0.0

        if overlay["hazard"] and flags["hazard"]:
            modifier += 0.18
        if overlay["drainage"] and flags["drainage"]:
            modifier += 0.1
            if condition.refreeze_likelihood >= 0.45:
                modifier += 0.12
        if overlay["shading"] and flags["shading"]:
            modifier += 0.08
            if -2 <= temp_c <= 1:
                modifier += 0.05
        if condition.treated:
            if overlay["treated"]:
                modifier -= 0.28 if condition.refreeze_likelihood < 0.75 else 0.14
            else:
                modifier += abs(condition.treatment_adjustment)

        return max(-0.45, min(0.45, modifier))

    def _segment_runoff_fraction(self, flags: dict) -> float:
        runoff = 0.45
        if flags.get("drainage"):
            runoff += 0.15
        if flags.get("shading"):
            runoff += 0.05
        return max(0.2, min(0.85, runoff))

    def _clamp(self, value: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, value))

    def _as_utc(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

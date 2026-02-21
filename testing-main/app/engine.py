from __future__ import annotations

from datetime import datetime, timedelta, timezone
from heapq import heappop, heappush
from math import exp
from typing import Dict, List, Tuple

from .db import fetch_rows
from .models import ReportType, RouteResult, Segment, SegmentCondition, WeatherSnapshot

REPORT_IMPACT = {
    ReportType.icy.value: 0.28,
    ReportType.slushy.value: 0.16,
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
    "clear": "green",
    "treated_stable": "blue",
    "treated_monitor": "green",
}

TREATMENT_BASE_RATE_KG_PER_M = {
    "brine": 0.09,
    "salt": 0.16,
    "sand": 0.13,
}

CHLORIDE_FACTOR = {
    "salt": 0.61,
    "brine": 0.14,
    "sand": 0.02,
}

BLANKET_SALT_RATE_KG_PER_M = 0.18
RUNOFF_RATIO = 0.42
TIMELINE_STEPS = [0, 6, 12, 18, 24]

SEGMENT_COORDS = {
    "S1": [(53.5230, -113.5260), (53.5236, -113.5247)],
    "S2": [(53.5236, -113.5247), (53.5238, -113.5231)],
    "S3": [(53.5238, -113.5231), (53.5232, -113.5218)],
    "S4": [(53.5230, -113.5260), (53.5232, -113.5218)],
    "S5": [(53.5232, -113.5218), (53.5241, -113.5205)],
    "S6": [(53.5238, -113.5231), (53.5241, -113.5205)],
}


class FrostFlowEngine:
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
    ) -> RouteResult:
        adj = self.graph()
        risk_map = self.compute_risk_map(horizon_hours)
        if start not in adj or end not in adj:
            raise ValueError("Unknown start/end")

        pq = [(0.0, start)]
        dist = {n: float("inf") for n in adj}
        prev: Dict[str, tuple[str, str] | None] = {n: None for n in adj}
        dist[start] = 0.0

        while pq:
            cur, node = heappop(pq)
            if cur > dist[node]:
                continue
            if node == end:
                break

            for nxt, seg in adj[node]:
                segment_condition = risk_map[seg.segment_id]
                base = seg.distance_m
                criticality = self._criticality(seg)

                if safest:
                    base *= 1 + (segment_condition.risk_score * 1.35)
                    base *= 1 - (criticality * 0.1)
                    if segment_condition.status == "confirmed_hazard":
                        base *= 1.25
                if avoid_steep and seg.slope_pct > 3:
                    base *= 1.32
                if prefer_cleared and seg.treated:
                    base *= 0.82
                if avoid_steep and seg.accessible_route and seg.slope_pct <= 2:
                    base *= 0.92

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
            "Safest route uses engineering risk, treatment state, and priority corridors."
            if safest
            else "Shortest route prioritizes distance while keeping critical corridors available."
        )
        return RouteResult(nodes=nodes, segments=segs, weighted_cost=round(dist[end], 2), explanation=explanation)

    def maintenance_plan(self, horizon_hours: int = 6) -> dict:
        segments = self.load_segments()
        risk_map = self.compute_risk_map(horizon_hours)
        weather = self.weather_for_horizon(horizon_hours)

        ranked = sorted(
            segments,
            key=lambda s: self._priority_index(s, risk_map[s.segment_id]),
            reverse=True,
        )

        ranked_payload = []
        optimized_mass_kg = 0.0
        blanket_mass_kg = 0.0
        optimized_chloride_kg = 0.0
        blanket_chloride_kg = 0.0
        total_saved_kg = 0.0

        for seg in ranked:
            condition = risk_map[seg.segment_id]
            treatment = self._recommended_treatment(condition.risk_score, weather.temp_c)
            required_kg = self._treatment_mass_kg(seg, condition.risk_score, treatment)
            if seg.treated:
                required_kg *= 0.4
            required_kg = round(required_kg, 1)
            blanket_kg = round(seg.distance_m * BLANKET_SALT_RATE_KG_PER_M, 1)
            saved_kg = round(max(0.0, blanket_kg - required_kg), 1)

            priority = round(self._priority_index(seg, condition), 3)
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

            ranked_payload.append(
                {
                    "segment_id": seg.segment_id,
                    "name": seg.name,
                    "risk_score": condition.risk_score,
                    "confidence": condition.confidence,
                    "status": condition.status,
                    "display_color": condition.display_color,
                    "treated": condition.treated,
                    "priority_index": priority,
                    "critical_roles": roles,
                    "surface_type": seg.surface_type,
                    "slope_pct": seg.slope_pct,
                    "drainage_quality": seg.drainage_quality,
                    "shading_exposure": seg.shading_exposure,
                    "foot_traffic_importance": seg.foot_traffic_importance,
                    "risk_peak_hour": peak_hour,
                    "recommended_pretreat_hour": pretreat_hour,
                    "eta_to_ice_minutes": eta_minutes,
                    "recommended_treatment": treatment,
                    "treatment_required_kg": required_kg,
                    "blanket_treatment_kg": blanket_kg,
                    "kg_saved_vs_blanket": saved_kg,
                    "salt_kg_if_rock_salt": blanket_kg,
                    "salt_kg_if_brine": round(self._treatment_mass_kg(seg, condition.risk_score, "brine"), 1),
                }
            )

            optimized_mass_kg += required_kg
            blanket_mass_kg += blanket_kg
            optimized_chloride_kg += required_kg * CHLORIDE_FACTOR[treatment]
            blanket_chloride_kg += blanket_kg * CHLORIDE_FACTOR["salt"]
            total_saved_kg += saved_kg

        chloride_reduction_kg = max(0.0, blanket_chloride_kg - optimized_chloride_kg)
        chloride_reduction_pct = (chloride_reduction_kg / blanket_chloride_kg) * 100 if blanket_chloride_kg else 0.0
        runoff_reduction_kg = chloride_reduction_kg * RUNOFF_RATIO
        optimized_ratio_pct = (optimized_mass_kg / blanket_mass_kg) * 100 if blanket_mass_kg else 0.0
        saved_ratio_pct = (total_saved_kg / blanket_mass_kg) * 100 if blanket_mass_kg else 0.0
        sustainability_index = min(100.0, max(0.0, 35 + (saved_ratio_pct * 0.4) + (chloride_reduction_pct * 0.45)))

        emergency_segments = [s.segment_id for s in segments if s.emergency_route]
        accessible_segments = [s.segment_id for s in segments if s.accessible_route]
        corridor_segments = [s.segment_id for s in segments if s.main_corridor]

        route_nodes = self._maintenance_route_nodes(ranked)

        return {
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
                "pollution_avoided_kg": round(chloride_reduction_kg, 1),
                "sustainability_index": round(sustainability_index, 1),
                "optimized_to_blanket_ratio_pct": round(optimized_ratio_pct, 1),
                "saved_mass_ratio_pct": round(saved_ratio_pct, 1),
                "estimated_salt_use_kg": round(blanket_mass_kg, 1),
                "brine_equivalent_kg": round(optimized_mass_kg, 1),
            },
        }

    def warning_banner(self, horizon_hours: int) -> str:
        weather = self.weather_for_horizon(horizon_hours)
        prev = self.weather_for_horizon(max(horizon_hours - 6, 0)) if horizon_hours >= 6 else self.previous_weather()
        if prev.temp_c > 0 and weather.temp_c < 0:
            return "Refreeze risk: temperatures crossing below 0C."
        if weather.temp_c <= -12:
            return "Extreme cold regime: sand mix may be required over salt."
        if weather.precip_mm > 0.5:
            return "Precipitation expected: slip risk likely across exposed segments."
        return "Conditions stable; continue engineering monitoring cycle."

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
        reports_risk, hazard_reports, report_reasons = self._report_component(segment_reports, now)
        treatment_adjustment, treatment_reasons = self._treatment_adjustment(seg, weather)

        risk = 0.05 + (0.52 * weather_risk) + (0.33 * structural_risk) + reports_risk + treatment_adjustment
        if seg.emergency_route and risk >= 0.4:
            risk += 0.04
        risk = max(0.0, min(1.0, risk))

        confidence = 0.48 + min(hazard_reports * 0.11, 0.33)
        if abs(weather.temp_c) <= 2:
            confidence += 0.08
        if weather.precip_mm > 0.15:
            confidence += 0.07
        if seg.treated:
            confidence += 0.02
        confidence = max(0.2, min(0.97, confidence))

        status, display_color = self._classify_status(risk, hazard_reports, seg.treated)
        reasons = (weather_reasons + structural_reasons + report_reasons + treatment_reasons)[:5]

        peak_hour = int((timeline_meta or {}).get("peak_hour", 0))
        peak_score = float((timeline_meta or {}).get("peak_risk", risk))
        pretreat_hour = int((timeline_meta or {}).get("recommended_pretreat_hour", 0))

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
            score += 0.20
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
        reasons: list[str] = []
        reasons.append(f"{seg.surface_type} surface behavior")
        reasons.append(f"{seg.slope_pct:.1f}% slope loading")
        reasons.append(f"{seg.drainage_quality} drainage")
        if seg.shading_exposure >= 0.6:
            reasons.append("limited solar melting")
        if seg.accessible_route and seg.slope_pct > 2:
            score += 0.04
            reasons.append("accessibility sensitivity")

        return min(0.82, score), reasons

    def _report_component(self, reports: list[dict], now: datetime) -> tuple[float, int, list[str]]:
        report_score = 0.0
        hazard_reports = 0
        reasons: list[str] = []

        for report in reports:
            report_time = self._as_utc(datetime.fromisoformat(report["timestamp"]))
            age_hours = (now - report_time).total_seconds() / 3600
            decay = exp(-max(age_hours, 0) / 8)
            report_type = report["report_type"]
            report_score += REPORT_IMPACT.get(report_type, 0.0) * decay

            if report_type in {ReportType.icy.value, ReportType.slushy.value} and age_hours <= 12:
                hazard_reports += 1
            if report_type == ReportType.icy.value:
                reasons.append("user icy report")
            elif report_type == ReportType.slushy.value:
                reasons.append("user slush report")
            elif report_type == ReportType.clear.value:
                reasons.append("clear-condition report")
            elif report_type == ReportType.salted.value:
                reasons.append("salted-condition report")

        report_score = max(-0.25, min(0.45, report_score))
        return report_score, hazard_reports, reasons

    def _treatment_adjustment(self, seg: Segment, weather: WeatherSnapshot) -> tuple[float, list[str]]:
        if not seg.treated:
            return 0.0, []

        effectiveness = -0.22
        reasons = ["recent treatment effect"]
        if weather.temp_c <= -12:
            effectiveness = -0.10
            reasons.append("de-icer effectiveness reduced in extreme cold")
        elif weather.temp_c <= -8:
            effectiveness = -0.14
            reasons.append("reduced treatment effectiveness")

        if seg.surface_type == "bridge":
            effectiveness += 0.04
            reasons.append("bridge deck cools quickly")
        if seg.drainage_quality == "poor":
            effectiveness += 0.03
            reasons.append("drainage limits treatment retention")

        return effectiveness, reasons

    def _classify_status(self, risk: float, hazard_reports: int, treated: bool) -> tuple[str, str]:
        if treated and risk <= 0.35:
            return "treated_stable", STATUS_COLOR["treated_stable"]
        if treated:
            return "treated_monitor", STATUS_COLOR["treated_monitor"]
        if hazard_reports >= 2:
            return "confirmed_hazard", STATUS_COLOR["confirmed_hazard"]
        if hazard_reports == 1:
            return "caution", STATUS_COLOR["caution"]
        if risk >= 0.75:
            return "confirmed_hazard", STATUS_COLOR["confirmed_hazard"]
        if risk >= 0.5:
            return "caution", STATUS_COLOR["caution"]
        return "clear", STATUS_COLOR["clear"]

    def _priority_index(self, seg: Segment, condition: SegmentCondition) -> float:
        return (condition.risk_score * 0.58) + (self._criticality(seg) * 0.27) + ((seg.foot_traffic_importance / 5) * 0.15)

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
        if risk_score >= 0.72:
            return "salt"
        return "brine"

    def _treatment_mass_kg(self, seg: Segment, risk_score: float, treatment: str) -> float:
        base_rate = TREATMENT_BASE_RATE_KG_PER_M[treatment]
        risk_multiplier = 0.75 + (risk_score * 0.9)
        slope_multiplier = 1.0 + min(seg.slope_pct, 6) * 0.025
        drainage_multiplier = {"poor": 1.18, "fair": 1.08, "good": 0.95}.get(seg.drainage_quality, 1.0)
        surface_multiplier = {"bridge": 1.12, "brick": 1.06, "concrete": 1.0, "asphalt": 0.98}.get(seg.surface_type, 1.0)
        corridor_multiplier = 1.03 if seg.main_corridor else 1.0
        return seg.distance_m * base_rate * risk_multiplier * slope_multiplier * drainage_multiplier * surface_multiplier * corridor_multiplier

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

    def _as_utc(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

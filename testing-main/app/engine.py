from __future__ import annotations

from datetime import datetime, timedelta, timezone
from heapq import heappop, heappush
from math import exp
from typing import Dict, List, Tuple

from .db import fetch_rows
from .models import ReportType, RouteResult, Segment, SegmentCondition, WeatherSnapshot

REPORT_IMPACT = {
    ReportType.icy.value: 0.25,
    ReportType.slushy.value: 0.12,
    ReportType.clear.value: -0.18,
    ReportType.salted.value: -0.22,
}

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
                slope_pct=r["slope_pct"],
                shaded=bool(r["shaded"]),
                treated=bool(r["treatment_status"]),
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
        return WeatherSnapshot(datetime.fromisoformat(row["timestamp"]), row["temp_c"], row["precip_mm"])

    def previous_weather(self) -> WeatherSnapshot:
        rows = fetch_rows("SELECT timestamp,temp_c,precip_mm FROM WeatherData ORDER BY timestamp DESC LIMIT 2")
        row = rows[1] if len(rows) > 1 else rows[0]
        return WeatherSnapshot(datetime.fromisoformat(row["timestamp"]), row["temp_c"], row["precip_mm"])

    def recent_reports(self) -> Dict[str, list[dict]]:
        rows = fetch_rows("SELECT segment_id, report_type, timestamp FROM Reports WHERE timestamp >= datetime('now', '-24 hours')")
        bucket: Dict[str, list[dict]] = {}
        for row in rows:
            bucket.setdefault(row["segment_id"], []).append(dict(row))
        return bucket

    def compute_risk_map(self, horizon_hours: int = 0) -> Dict[str, SegmentCondition]:
        segments = self.load_segments()
        weather = self.weather_for_horizon(horizon_hours)
        prev = self.previous_weather()
        reports = self.recent_reports()

        near_freezing = -2.0 <= weather.temp_c <= 2.0
        freeze_thaw = prev.temp_c > 0.0 and weather.temp_c < 0.0

        risk_map: Dict[str, SegmentCondition] = {}
        now = datetime.now(timezone.utc)
        for seg in segments:
            risk = 0.12
            reasons: List[str] = []

            if near_freezing:
                risk += 0.24
                reasons.append("Temp near freezing")
            if weather.precip_mm > 0.2:
                risk += 0.2
                reasons.append("Recent precipitation")
            if freeze_thaw:
                risk += 0.2
                reasons.append("Freeze-thaw refreeze")
            if seg.shaded:
                risk += 0.08
                reasons.append("Shaded segment")
            if seg.treated:
                risk -= 0.2
                reasons.append("Segment treated")

            report_count = 0
            for rep in reports.get(seg.segment_id, []):
                age = (now - datetime.fromisoformat(rep["timestamp"]).astimezone(timezone.utc)).total_seconds() / 3600
                decay = exp(-max(age, 0) / 6)
                risk += REPORT_IMPACT.get(rep["report_type"], 0) * decay
                report_count += 1

            risk = max(0.0, min(1.0, risk))
            confidence = 0.45 + min(report_count * 0.08, 0.3)
            if abs(weather.temp_c) < 1:
                confidence += 0.1
            confidence = max(0.1, min(0.95, confidence))

            risk_map[seg.segment_id] = SegmentCondition(
                segment_id=seg.segment_id,
                risk_score=round(risk, 3),
                confidence=round(confidence, 3),
                reason=", ".join(reasons) if reasons else "Baseline winter risk",
                reports_count=report_count,
                treated=seg.treated,
            )
        return risk_map

    def graph(self):
        adj: Dict[str, list[Tuple[str, Segment]]] = {}
        for seg in self.load_segments():
            adj.setdefault(seg.start, []).append((seg.end, seg))
            adj.setdefault(seg.end, []).append((seg.start, seg))
        return adj

    def compute_route(self, start: str, end: str, safest: bool, avoid_steep: bool, prefer_cleared: bool, horizon_hours: int = 0) -> RouteResult:
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
                base = seg.distance_m
                if safest:
                    base *= 1 + risk_map[seg.segment_id].risk_score
                if avoid_steep and seg.slope_pct > 3:
                    base *= 1.25
                if prefer_cleared and seg.treated:
                    base *= 0.8
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

        explanation = "Safest route prioritized low-risk segments." if safest else "Shortest route prioritized distance."
        return RouteResult(nodes=nodes, segments=segs, weighted_cost=round(dist[end], 2), explanation=explanation)

    def maintenance_plan(self, horizon_hours: int = 6) -> dict:
        risk_map = self.compute_risk_map(horizon_hours)
        segments = self.load_segments()
        ranked = sorted(segments, key=lambda s: risk_map[s.segment_id].risk_score, reverse=True)[:5]

        loop_nodes = ["SUB", "Quad", "CAB", "Library", "HUB", "SUB"]
        ranked_payload = []
        rock_total = 0.0
        brine_total = 0.0
        for seg in ranked:
            risk = risk_map[seg.segment_id]
            rock = round(seg.distance_m * 0.12, 1)
            brine = round(rock * 0.75, 1)
            rock_total += rock
            brine_total += brine
            ranked_payload.append(
                {
                    "segment_id": seg.segment_id,
                    "name": seg.name,
                    "risk_score": risk.risk_score,
                    "confidence": risk.confidence,
                    "treated": risk.treated,
                    "eta_to_ice_minutes": int(max(15, 180 * (1 - risk.risk_score))),
                    "salt_kg_if_rock_salt": rock,
                    "salt_kg_if_brine": brine,
                }
            )

        return {
            "ranked_segments": ranked_payload,
            "treatment_route_nodes": loop_nodes,
            "environmental_metrics": {
                "estimated_salt_use_kg": round(rock_total, 1),
                "brine_equivalent_kg": round(brine_total, 1),
                "chloride_reduction_pct": round((1 - (brine_total / rock_total)) * 100, 1) if rock_total else 0.0,
            },
        }

    def warning_banner(self, horizon_hours: int) -> str:
        weather = self.weather_for_horizon(horizon_hours)
        prev = self.previous_weather()
        if prev.temp_c > 0 and weather.temp_c < 0:
            return "⚠️ Refreeze risk: temperatures dropping below 0°C."
        if weather.precip_mm > 0.5:
            return "⚠️ Snow/rain expected, slippery conditions possible."
        return "✅ Conditions stable; continue monitoring."

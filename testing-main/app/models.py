from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List


class ReportType(str, Enum):
    icy = "Icy"
    slushy = "Slushy"
    clear = "Clear"
    salted = "Salted"


@dataclass
class Segment:
    segment_id: str
    name: str
    start: str
    end: str
    distance_m: float
    slope_pct: float
    shaded: bool
    treated: bool = False


@dataclass
class SegmentCondition:
    segment_id: str
    risk_score: float
    confidence: float
    reason: str
    reports_count: int
    treated: bool


@dataclass
class WeatherSnapshot:
    timestamp: datetime
    temp_c: float
    precip_mm: float


RiskMap = Dict[str, SegmentCondition]


@dataclass
class RouteResult:
    nodes: List[str]
    segments: List[str]
    weighted_cost: float
    explanation: str

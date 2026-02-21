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
    surface_type: str  # concrete, asphalt, brick, bridge
    slope_pct: float
    drainage_quality: str  # poor, fair, good
    shading_exposure: float  # 0-1 scale
    foot_traffic_importance: int  # 1-5 scale
    shaded: bool
    treated: bool = False
    emergency_route: bool = False
    accessible_route: bool = False
    main_corridor: bool = False
    wind_corridor: bool = False


@dataclass
class SegmentCondition:
    segment_id: str
    risk_score: float
    weather_risk: float
    structural_risk: float
    reports_risk: float
    treatment_adjustment: float
    confidence: float
    reason: str
    reports_count: int
    treated: bool
    status: str
    display_color: str
    risk_peak_hour: int
    risk_peak_score: float
    recommended_pretreat_hour: int
    refreeze_likelihood: float


@dataclass
class LiveWeather:
    source: str
    location: str
    observed_at: datetime
    temperature_c: float
    feels_like_c: float
    precipitation_mm: float
    snowfall_mm: float
    freeze_thaw_warning: bool
    surface_icing_risk: str
    classification: str
    risk_multiplier: float
    treatment_recommendation: str
    summary: str


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

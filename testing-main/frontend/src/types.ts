export type Segment = {
  segment_id: string;
  name: string;
  start: string;
  end: string;
  coords: [number, number][];
  risk_score: number;
  confidence: number;
  reason: string;
  reports_count: number;
  treated: boolean;
};

export type RiskMapResponse = {
  horizon_hours: number;
  active_warning: string;
  segments: Segment[];
  nodes: string[];
};

export type RouteResponse = {
  nodes: string[];
  segments: string[];
  weighted_cost: number;
  explanation: string;
};

export type MaintenanceResponse = {
  ranked_segments: Array<{
    segment_id: string;
    name: string;
    risk_score: number;
    confidence: number;
    treated: boolean;
    eta_to_ice_minutes: number;
    salt_kg_if_rock_salt: number;
    salt_kg_if_brine: number;
  }>;
  treatment_route_nodes: string[];
  environmental_metrics: {
    estimated_salt_use_kg: number;
    brine_equivalent_kg: number;
    chloride_reduction_pct: number;
  };
};

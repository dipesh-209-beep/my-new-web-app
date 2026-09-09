// Mirrors backend/app/schemas.py exactly. Keep in sync if the backend changes.

export interface Stop {
  stop_id: string;
  stop_name: string;
  lat: number;
  lng: number;
  zone?: string | null;
  district?: string | null;
  aliases?: string | null;
  ward?: number | null;
  landmark?: string | null;
  is_major_stop: boolean;
  has_shelter?: boolean;
  has_ticket_counter?: boolean;
  is_interchange: boolean;
  wheelchair_access?: boolean;
  audio_support?: boolean;
  status: string;
}

export interface RoadGeometry {
  // GeoJSON LineString: coordinates are [lng, lat] pairs, per GeoJSON spec.
  // Convert to [lat, lng] before handing to Leaflet.
  geometry: {
    type: "LineString";
    coordinates: [number, number][];
  };
  distance_m: number;
  duration_s: number;
}

export interface RouteLeg {
  route_id: string;
  route_name: string;
  board_stop: Stop;
  alight_stop: Stop;
  num_ride_segments: number; // count of merged ride segments (hops), not physical stops
  stops: Stop[]; // every physical stop on this leg, in order, board→alight inclusive
  road_geometry?: RoadGeometry | null;
}

export interface FareOut {
  fare_id: string;
  min_distance_km: number;
  max_distance_km: number;
  fare_npr_min: number;
  fare_npr_max: number;
  student_discount_pct: number | null;
}

export interface RouteAlternative {
  label: "alternate_direct_route" | "shortest_distance" | "fastest_estimated";
  total_cost: number;
  transfer_count: number;
  legs: RouteLeg[];
}

export interface RouteFinderResult {
  origin_stop_id: string;
  destination_stop_id: string;
  // Intermediate stop_ids the search was asked to pass through, in
  // order. Empty/absent for a plain origin->destination search. Optional
  // here (even though the backend always sends it) so existing fixtures
  // and hand-built results elsewhere in the frontend don't all need
  // updating just to satisfy this new field.
  via_stop_ids?: string[];
  total_cost: number;
  transfer_count: number;
  legs: RouteLeg[];
  fare: FareOut | null;
  alternatives: RouteAlternative[];
}

// Frontend-only wrapper: the backend signals "not found" via HTTP 404,
// not a `found` field, so we add it client-side after the fetch.
export type RouteSearchResult =
  | ({ found: true } & RouteFinderResult)
  | { found: false };

// Which field a map click should fill in. Null means clicking a stop on
// the map does nothing (the default, idle state).
export type StopPickTarget = "origin" | "destination" | null;

export interface LatLng {
  lat: number;
  lng: number;
}

// GET /walking-route response -- same shape as RoadGeometry, kept separate
// so call sites don't imply it came from a bus leg.
export type WalkingRoute = RoadGeometry;

// GET /routes/{route_id}/geometry response -- road-following OSRM geometry
// through a route's full stop sequence. Same shape as RoadGeometry, kept
// separate so call sites don't imply it came from a route-finder leg.
export type RouteGeometry = RoadGeometry;

// Mirrors CongestionLevel / CongestionSegmentOut / CongestionResponse in
// backend/app/schemas.py.
export type CongestionLevel = "free_flow" | "moderate" | "heavy" | "unknown";

export interface CongestionSegment {
  route_id: string | null;
  from_stop_id: string;
  to_stop_id: string;
  avg_duration_s: number;
  avg_distance_m: number;
  free_flow_duration_s: number;
  congestion_ratio: number;
  congestion_level: CongestionLevel;
  sample_count: number;
  is_seeded: boolean;
}

export interface CongestionResponse {
  day_of_week: number;
  hour_bucket: number;
  segments: CongestionSegment[];
}

// Mirrors RouteOut / RouteStopOut / RouteListOut in backend/app/schemas.py.
export interface RouteOperator {
  operator_id: string;
  name: string;
  service_type: string | null;
}

export interface RouteSummary {
  route_id: string;
  route_name: string;
  short_name: string | null;
  vehicle_type: string;
  route_type?: string | null;
  start_stop_id: string;
  end_stop_id: string;
  total_stops: number;
  /** Whether a return leg exists as a distinct travel direction -- see
   * GET /routes/{route_id}/stops|geometry's `direction` query param.
   * Frontend uses this to decide whether to show a forward/reverse toggle. */
  is_bidirectional: boolean;
  approx_distance_km: number | null;
  /** Real OSRM road distance -- prefer this over approx_distance_km
   * (source-data-supplied, not reliably accurate) whenever it's set. */
  osrm_distance_km: number | null;
  has_ac?: boolean;
  is_express?: boolean;
  status: string;
  operator: RouteOperator | null;
}

export interface RouteListResponse {
  total: number;
  limit: number;
  offset: number;
  items: RouteSummary[];
}

// GET /routes/{route_id}/stops -- a route's stops, in ride order.
export interface RouteStopEntry {
  sequence_no: number;
  stop: Stop;
  // Route + direction-specific display position (see GET
  // /routes/{route_id}/stops's `direction` query param). null/absent
  // means no adjustment was computed (OSRM unreachable, etc) -- fall
  // back to stop.lat/lng, the canonical coordinate. Never persisted,
  // never used for search/details, only for map display.
  display_lat?: number | null;
  display_lng?: number | null;
}

// Which travel direction to request from GET /routes/{route_id}/stops
// and .../geometry -- see those endpoints' `direction` query param.
// "reverse" is only valid when the route is bidirectional.
export type RouteDirection = "forward" | "reverse";

// GET /stops -- mirrors StopListOut in backend/app/schemas.py.
export interface StopListOut {
  total: number;
  limit: number;
  offset: number;
  items: Stop[];
}

// GET /routes/{route_id} -- same shape as RouteSummary (RouteOut in the
// backend), aliased separately so call sites reading a single route don't
// have to import "RouteSummary" for a non-list context.
export type RouteOut = RouteSummary;

// ---------------------------------------------------------------------------
// Admin data-entry (mirrors backend/app/schemas.py + app/api/admin.py)
// ---------------------------------------------------------------------------

export interface AdminTokenResponse {
  access_token: string;
  token_type: string;
}

// POST /stops request body -- server generates stop_id.
export interface StopCreatePayload {
  stop_name: string;
  lat: number;
  lng: number;
  aliases?: string | null;
  zone?: string | null;
  district?: string | null;
  ward?: number | null;
  landmark?: string | null;
  is_major_stop?: boolean;
  has_shelter?: boolean;
  has_ticket_counter?: boolean;
  is_interchange?: boolean;
  wheelchair_access?: boolean;
  audio_support?: boolean;
}

// POST /routes request body -- server generates route_id.
export interface RouteCreatePayload {
  route_name: string;
  short_name?: string | null;
  vehicle_type: string;
  route_type?: string | null;
  operator?: string | null;
  operator_id?: string | null;
  start_stop_id: string;
  end_stop_id: string;
  /** Required by the backend (initial value only -- add-route-stop syncs it). */
  total_stops: number;
  is_bidirectional?: boolean;
  has_ac?: boolean;
  is_express?: boolean;
}

// POST/PATCH response from the route-stop write endpoints (add/remove/
// reorder) -- one row of route_stops.
export interface RouteStopRef {
  route_id: string;
  stop_id: string;
  sequence_no: number;
}

// Route statuses allowed by PATCH /routes/{route_id}/status.
export type RouteStatus = "active" | "pending_release";

// ---------------------------------------------------------------------------
// Public-user auth + crowd-sourced suggestions (mirrors backend/app/schemas.py
// app/api/auth.py + app/api/suggestions.py)
// ---------------------------------------------------------------------------

// POST /auth/register + /auth/login response.
export interface UserTokenResponse {
  access_token: string;
  token_type: string;
}

// Which kinds of change a logged-in user can propose. Mirrors the backend's
// SuggestionCreate.suggestion_type Literal exactly.
export type SuggestionType =
  | "stop_name_change"
  | "route_name_change"
  | "stop_sequence_change";

export type SuggestionStatus = "pending" | "approved" | "rejected" | "auto_applied";

// Mirrors SuggestionOut in backend/app/schemas.py.
export interface Suggestion {
  suggestion_id: number;
  target_type: "stop" | "route";
  target_id: string;
  suggestion_type: SuggestionType;
  payload: Record<string, unknown>;
  status: SuggestionStatus;
  vote_count: number;
  created_at: string;
  submitted_by: string | null;
  reviewed_by: string | null;
  /** null when the listing request carried no user token. */
  voted_by_me: boolean | null;
}

// POST /suggestions request body -- mirrors backend SuggestionCreate.
export interface SuggestionCreatePayload {
  target_type: "stop" | "route";
  target_id: string;
  suggestion_type: SuggestionType;
  payload: Record<string, unknown>;
}
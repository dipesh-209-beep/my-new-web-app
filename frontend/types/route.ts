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
  /** Operator running this route (from the linked operator or free-text
   * column). Null for walking transfers and routes with no recorded
   * operator. */
  operator_name?: string | null;
  /** Terminus this leg travels toward, derived from board/alight positions
   * in the route's stop sequence. Null when undeterminable. A direction
   * hint, not a guarantee the vehicle terminates exactly there. */
  direction_towards?: string | null;
  /** Backend-computed estimated travel time for this leg at fixed assumed
   * speeds (bus ~12 km/h, walk ~4.7 km/h) over the leg's distance on the
   * path's single distance basis (see DistanceSource). Null only when the
   * backend couldn't compute it. NOT the OSRM driving-profile duration --
   * that's how long a private car would take at free-flow speed. Always
   * display as an estimate, never an ETA. */
  estimated_duration_s?: number | null;
  /** The distance-banded fare estimate for THIS ride leg (its own ridden
   * distance looked up in fare_rules), or null for a walking-transfer leg
   * or a ride leg no band covers. The journey total is the sum of the ride
   * legs' fares -- see RouteFinderResult.fare / JourneyFareOut. */
  fare?: FareOut | null;
}

/** The min/max amount pair shared by a single fare band (FareOut) and a
 * journey total (JourneyFareOut), so one formatter serves both. */
export interface FareAmounts {
  fare_npr_min: number;
  fare_npr_max: number;
}

/** Which basis a result's distance-derived figures are on. Chosen once per
 * path (all legs have real OSRM geometry, or the whole path falls back to
 * the routing graph's straight-line sum), so `total_distance_source`
 * describes the total, the fare distance, and the estimated durations
 * alike. Mirrors DistanceSource in backend/app/schemas.py. */
export type DistanceSource = "osrm" | "graph";

export interface FareOut extends FareAmounts {
  fare_id: string;
  min_distance_km: number;
  max_distance_km: number;
  student_discount_pct: number | null;
  /** Caveat on how solid this fare figure is ("PENDING confirmation
   * against the official gazette", for now). Frontend shows it under the
   * fare so "Estimated fare" never reads as fact. */
  verification_note?: string | null;
}

/** A journey's estimated total fare: the sum of its ride legs' own fare
 * bands. Mirrors JourneyFareOut in backend/app/schemas.py. `complete` is
 * true only when every ride leg matched a fare band; when false the min/max
 * cover only the matched legs and must be labelled incomplete. A journey
 * with no priced ride leg has `fare: null`, never a zero. */
export interface JourneyFareOut extends FareAmounts {
  complete: boolean;
  ride_leg_count: number;
  priced_leg_count: number;
  /** Distinct verification notes of the contributing bands, joined. */
  verification_note?: string | null;
}

export interface RouteAlternative {
  label: "alternate_direct_route" | "shortest_distance" | "fastest_estimated";
  total_cost: number;
  /** The path's single distance basis (see DistanceSource), covering this
   * option's total, fare distance, and estimated durations. Null only for
   * hand-built fixtures or older payloads; the API always sets it. */
  total_distance_source?: DistanceSource | null;
  transfer_count: number;
  legs: RouteLeg[];
  /** This option's own fare: the sum of its ride legs' bands (on the same
   * basis as total_cost), so the selected alternative's card never has to
   * borrow the primary result's fare. `complete` is false when any ride leg
   * had no matching band; null when there's no ride leg or no leg could be
   * priced. Each leg's own band is on RouteLeg.fare. */
  fare?: JourneyFareOut | null;
  /** Sum of every leg's estimate plus assumed per-ride boarding wait.
   * Labeled approximation, same distance basis as the primary result. */
  total_estimated_duration_s?: number | null;
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
  /** The path's single distance basis (see DistanceSource), covering this
   * result's total, fare distance, and estimated durations. Optional here
   * (like via_stop_ids) so hand-built fixtures don't all need updating;
   * the API always sets it. */
  total_distance_source?: DistanceSource | null;
  transfer_count: number;
  legs: RouteLeg[];
  fare: JourneyFareOut | null;
  alternatives: RouteAlternative[];
  /** Whole-trip estimate: ride/walk time at assumed speeds (over each
   * leg's distance on the same basis as total_cost) plus per-ride boarding
   * wait, computed by the backend. Optional here (like via_stop_ids) so
   * hand-built fixtures don't all need updating; the display code falls
   * back to summing leg estimates. Always shown as an estimate, never as a
   * definitive ETA, and never as an OSRM driving-profile time. */
  total_estimated_duration_s?: number | null;
  /** Machine-readable reason this result was chosen as the primary; the UI
   * should translate it into a concise, truthful explanation. Optional so
   * existing fixtures and responses without this field still work. */
  rank_reason?: "only_option" | "fewest_transfers" | "shortest_distance" | "avoids_congestion" | "best_overall" | "via_stops" | null;
}

// Frontend-only wrapper: the backend signals "not found" via HTTP 404,
// not a `found` field, so we add it client-side after the fetch.
export type RouteSearchResult =
  | ({ found: true } & RouteFinderResult)
  | { found: false };

// Which field a map click should fill in. Null means clicking a stop on
// the map does nothing (the default, idle state).
export type StopPickTarget = "origin" | "destination" | null;

// Progress stage for a route search, driving the result panel's loading
// copy ("Finding the best routes…" → "Calculating alternative routes…").
export type LoadingStage = "idle" | "searching" | "calculating_alternatives" | "done";

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

/** Mirrors CongestionProvenance in backend/app/schemas.py. Everything the
 * congestion feature serves is derived, never observed: `is_live` and
 * `is_measured` are false by construction, and the backing severity table's
 * origin is not recorded (see backend/app/routing/congestion_provenance.py).
 * The UI must render `note` (or its own constant fallback) so congestion is
 * never presented as live/measured. */
export interface CongestionProvenance {
  is_live: boolean;
  is_measured: boolean;
  note: string;
}

export interface CongestionResponse {
  day_of_week: number;
  hour_bucket: number;
  segments: CongestionSegment[];
  /** Optional: a cache entry written before this field existed omits it, so
   * the UI falls back to a constant honest note. */
  provenance?: CongestionProvenance | null;
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
  /** Whole-route estimated running time in minutes, from the route record
   * (mirrors RouteOut.estimated_duration_min in backend/app/schemas.py).
   * Source-supplied and often absent -- null when there's no estimate.
   * A planning estimate for the full route, never a live ETA. */
  estimated_duration_min: number | null;
  /** Scheduled headway in whole minutes between departures (mirrors
   * RouteOut.frequency_min in backend/app/schemas.py). A scheduled/estimated
   * figure from route data, NOT a live arrival countdown. The backend
   * normalizes unusable values (missing/zero/negative/malformed) to null, so
   * a number here is always positive; absent means unknown, not "no service". */
  frequency_min?: number | null;
  /** Scheduled service window as "HH:MM" (mirrors
   * RouteOut.service_start_time / service_end_time). The backend drops a
   * one-sided or reversed window to null on BOTH ends, so they are present
   * together or absent together. Typical hours for the route, not real-time
   * status -- a bus isn't necessarily running just because the clock is
   * inside the window. */
  service_start_time?: string | null;
  service_end_time?: string | null;
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
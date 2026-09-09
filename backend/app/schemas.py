from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator
from pydantic import ValidationError


class StopOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stop_id: str
    stop_name: str
    lat: float
    lng: float
    zone: Optional[str] = None
    district: Optional[str] = None
    is_major_stop: bool
    is_interchange: bool
    status: str


class StopWithDistance(StopOut):
    distance_m: Optional[float] = None


class OperatorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    operator_id: str
    name: str
    service_type: Optional[str] = None


class RouteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    route_id: str
    route_name: str
    short_name: Optional[str] = None
    vehicle_type: str
    start_stop_id: str
    end_stop_id: str
    total_stops: int
    approx_distance_km: Optional[float] = None
    # Real road distance from OSRM (see Route.osrm_distance_km) -- prefer
    # this over approx_distance_km wherever it's available, since
    # approx_distance_km is source-data-supplied and not reliably
    # accurate. Null until backend/scripts/compute_osrm_route_distances.py
    # has been run for a given route.
    osrm_distance_km: Optional[float] = None
    status: str
    # Whether the return leg exists as a distinct travel direction (see
    # Route.is_bidirectional) -- the frontend uses this to decide whether
    # to offer a forward/reverse toggle on the route detail map (only
    # meaningful together with GET /routes/{route_id}/stops|geometry's
    # `direction` query param).
    is_bidirectional: bool
    # Route.operator (the column) is the free-text name as originally
    # recorded in the source data; the *linked* operator row lives on the
    # relationship Route.operator_ref, so pull from there instead.
    operator: Optional[OperatorOut] = Field(
        default=None, validation_alias="operator_ref", serialization_alias="operator"
    )


class RouteStopOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence_no: int
    stop: StopOut
    # Route + direction-specific display position, distinct from
    # stop.lat/lng (the canonical, never-adjusted coordinate -- see
    # models/stop.py). Set by app/api/routes.py::read_route_stops via
    # app/routing/stop_positioning.py when the route's road geometry is
    # available; None whenever it isn't (OSRM unreachable, fewer than 2
    # stops, etc), in which case callers should fall back to stop.lat/lng.
    # Search/detail endpoints (app/api/stops.py) never set these -- only
    # the route-stops listing used for map display does.
    display_lat: Optional[float] = None
    display_lng: Optional[float] = None


class StopListOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[StopOut]


class RouteListOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[RouteOut]


class FareOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fare_id: str
    min_distance_km: float
    max_distance_km: float
    fare_npr_min: float
    fare_npr_max: float
    student_discount_pct: Optional[float] = None


class RouteLeg(BaseModel):
    """One uninterrupted ride on a single route, part of a route-finder result."""
    route_id: str
    route_name: str
    board_stop: StopOut
    alight_stop: StopOut
    # Count of merged ride segments (hops) on this leg, NOT physical stops.
    # len(stops) == num_ride_segments + 1 always; use len(stops) for the
    # physical stop count.
    num_ride_segments: int
    stops: list[StopOut] = []  # every physical stop on this leg, in order, board→alight inclusive
    road_geometry: Optional[dict] = None


class RouteAlternative(BaseModel):
    """A secondary option alongside the primary route-finder result.
    Deliberately flat (no nested `alternatives` of its own, no `fare` --
    callers show fare/details for the primary result and let the person
    switch to an alternative if they prefer it). Each alternative is a
    genuinely different physical path -- deduplicated by stop sequence,
    not just by route_id, so two route numbers covering the identical
    stop-for-stop corridor collapse into a single option rather than
    showing up as two -- and carries real OSRM road_geometry per leg,
    same as the primary result.

    label meanings:
      - "alternate_direct_route": a different real bus route that also
        connects these two stops directly, second-shortest by distance.
        Exact, not estimated.
      - "shortest_distance": minimum total distance, ignoring the small
        per-transfer weighting the primary/recommended result applies.
        Only appears when no direct route exists. total_cost here is
        still the routing graph's straight-line (haversine) edge-weight
        sum, not an OSRM road distance -- prefer summing each leg's own
        road_geometry.distance_m for the real travel distance.
      - "fastest_estimated": minimum estimated travel time, using fixed
        assumed speeds per edge kind (~12 km/h riding, ~4.7 km/h
        walking, since no per-edge duration data exists pre-search --
        OSRM duration is only computed after a path is already chosen).
        This is a labeled approximation, not a live ETA. Only appears
        when no direct route exists.
    """

    label: Literal["alternate_direct_route", "shortest_distance", "fastest_estimated"]
    total_cost: float
    transfer_count: int
    legs: list[RouteLeg]


class RouteFinderResult(BaseModel):
    origin_stop_id: str
    destination_stop_id: str
    # Intermediate stop_ids the request asked to pass through, in order
    # (empty for a plain origin->destination search). Echoed back so the
    # frontend can label the trip without re-deriving it from `legs`.
    via_stop_ids: list[str] = []
    total_cost: float
    transfer_count: int
    legs: list[RouteLeg]
    fare: Optional[FareOut] = None
    alternatives: list[RouteAlternative] = []


# ---------------------------------------------------------------------------
# Admin write endpoints (app/api/admin.py) -- request bodies for creating
# stops/routes/route_stops. Server-generated fields (stop_id, route_id,
# geom, timestamps) are intentionally absent here.
# ---------------------------------------------------------------------------

class StopCreate(BaseModel):
    stop_name: str = Field(min_length=1, max_length=150)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    aliases: str | None = None
    zone: str | None = None
    district: str | None = None
    ward: int | None = None
    landmark: str | None = None
    is_major_stop: bool = False
    has_shelter: bool = False
    has_ticket_counter: bool = False
    is_interchange: bool = False
    wheelchair_access: bool = False
    audio_support: bool = False

    @field_validator("stop_name")
    @classmethod
    def strip_stop_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("stop_name must not be blank")
        return v

class RouteStatusUpdate(BaseModel):
    status: Literal["active", "pending_release"]


class RouteCreate(BaseModel):
    route_name: str = Field(min_length=1, max_length=150)
    short_name: str | None = Field(default=None, max_length=50)
    vehicle_type: str = Field(min_length=1, max_length=50)
    route_type: str | None = Field(default=None, max_length=50)
    operator: str | None = Field(default=None, max_length=100)
    operator_id: str | None = None
    start_stop_id: str
    end_stop_id: str
    total_stops: int = Field(ge=0)
    is_bidirectional: bool = False
    has_ac: bool = False
    is_express: bool = False


class RouteStopCreate(BaseModel):
    stop_id: str
    sequence_no: int = Field(ge=1)


class RouteStopRef(BaseModel):
    """One row of route_stops, as returned by the route-stop write
    endpoints (add/remove/reorder)."""
    route_id: str
    stop_id: str
    sequence_no: int


class StopNameUpdate(BaseModel):
    """Body for PATCH /stops/{stop_id} -- a direct (editorial) stop-name
    correction. This is the admin path for what users can suggest via
    POST /suggestions (stop_name_change)."""
    stop_name: str = Field(min_length=1, max_length=150)

    @field_validator("stop_name")
    @classmethod
    def strip_stop_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("stop_name must not be blank")
        return v


class RouteNameUpdate(BaseModel):
    """Body for PATCH /routes/{route_id} -- a direct (editorial) route-name
    correction. The admin path for what users can suggest via
    POST /suggestions (route_name_change)."""
    route_name: str = Field(min_length=1, max_length=150)

    @field_validator("route_name")
    @classmethod
    def strip_route_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("route_name must not be blank")
        return v


class RouteStopReorder(BaseModel):
    # Desired order of a route's existing stop rows, expressed as their
    # *current* sequence_no values -- a permutation of 1..N where N is the
    # route's current stop count. Expressing the order as sequence numbers
    # rather than stop_ids keeps loop routes with legitimately repeated
    # stops unambiguous (two occurrences of the same stop are distinct
    # rows: e.g. [1, 3, 1] can't distinguish which "1" moves where, but
    # [2, 1, 3] can).
    sequence: list[int] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Admin login (AdminUser)
# ---------------------------------------------------------------------------

class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1)


class AdminTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------------------------------------------------------------------------
# Public-user auth (app/api/auth.py, backed by models/user.py) -- same
# JWT shape as the admin login so the frontend can treat both the same way;
# the tokens themselves are scoped by a "type" claim instead (see
# app/core/security.py).
# ---------------------------------------------------------------------------

class UserRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("username must not be blank")
        return v


class UserLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1)


class UserTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------------------------------------------------------------------------
# Historical congestion (app/api/congestion.py, backed by
# segment_congestion_stats -- see that model's docstring for the schema
# rationale).
# ---------------------------------------------------------------------------

CongestionLevel = Literal["free_flow", "moderate", "heavy", "unknown"]


class CongestionSegmentOut(BaseModel):
    route_id: Optional[str] = None
    from_stop_id: str
    to_stop_id: str
    avg_duration_s: float
    avg_distance_m: float
    free_flow_duration_s: float
    # avg_duration_s / free_flow_duration_s. 1.0 = as fast as this segment's
    # best-observed bucket; higher = slower than usual for this segment.
    congestion_ratio: float
    congestion_level: CongestionLevel
    sample_count: int
    is_seeded: bool


class CongestionResponse(BaseModel):
    day_of_week: int
    hour_bucket: int
    segments: list[CongestionSegmentOut]


# ---------------------------------------------------------------------------
# Crowd-sourced suggestions (app/api/suggestions.py)
#
# POST /suggestions takes `suggestion_type` at the TOP LEVEL plus the change
# body in `payload` WITHOUT the type tag. SuggestionCreate.model_validator
# injects the tag into payload and re-validates it as the discriminated
# union below, so a mismatch (e.g. sequence fields on a stop_name_change)
# fails 422 with the union's own error detail. payload is stored raw (no
# injected tag) so its hash matches what the client sent.
# ---------------------------------------------------------------------------

class _SuggestionPayload:
    """Marker base for the suggestion payload union -- each member pins
    suggestion_type with a Literal, which is what Field(discriminator=...)
    dispatches on."""


class StopNameChangePayload(_SuggestionPayload, BaseModel):
    model_config = ConfigDict(extra="forbid")
    suggestion_type: Literal["stop_name_change"]
    stop_name: str = Field(min_length=1, max_length=150)

    @field_validator("stop_name")
    @classmethod
    def strip_stop_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("stop_name must not be blank")
        return v


class RouteNameChangePayload(_SuggestionPayload, BaseModel):
    model_config = ConfigDict(extra="forbid")
    suggestion_type: Literal["route_name_change"]
    route_name: str = Field(min_length=1, max_length=150)

    @field_validator("route_name")
    @classmethod
    def strip_route_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("route_name must not be blank")
        return v


class StopSequenceChangePayload(_SuggestionPayload, BaseModel):
    model_config = ConfigDict(extra="forbid")
    suggestion_type: Literal["stop_sequence_change"]
    # The desired new order, expressed as the route's CURRENT sequence_no
    # values -- the same convention as admin RouteStopReorder. Validated as
    # a permutation against the route at apply time (the route can change
    # between suggestion and application), here just non-empty + positive.
    sequence: list[int] = Field(min_length=1)


SuggestionPayload = Annotated[
    Union[
        StopNameChangePayload,
        RouteNameChangePayload,
        StopSequenceChangePayload,
    ],
    Field(discriminator="suggestion_type"),
]

# A bare typing.Union isn't a Pydantic model (no .model_validate); the
# TypeAdapter is what actually parses a payload dict against the
# discriminated union.
_payload_validator = TypeAdapter(SuggestionPayload)


class SuggestionCreate(BaseModel):
    """Request body for POST /suggestions -- see the module comment for the
    suggestion_type/payload split."""
    target_type: Literal["stop", "route"]
    target_id: str = Field(min_length=1, max_length=50)
    suggestion_type: Literal["stop_name_change", "route_name_change", "stop_sequence_change"]
    payload: dict

    @model_validator(mode="after")
    def validate_payload(self) -> "SuggestionCreate":
        tagged = {**self.payload, "suggestion_type": self.suggestion_type}
        try:
            _payload_validator.validate_python(tagged)
        except ValidationError as exc:
            raise ValueError(
                f"payload does not match suggestion_type '{self.suggestion_type}': {exc.errors()}"
            ) from exc
        return self


class SuggestionOut(BaseModel):
    suggestion_id: int
    target_type: str
    target_id: str
    suggestion_type: str
    payload: dict
    status: str
    vote_count: int
    created_at: str
    # Optional enrichment fields -- set by the endpoints, not the ORM.
    submitted_by: Optional[str] = None
    reviewed_by: Optional[str] = None
    # Only meaningful on the public GET /suggestions (which accepts an
    # optional user token): did the requesting user already back this?
    voted_by_me: Optional[bool] = None


class SuggestionAction(BaseModel):
    """Body for the admin review endpoint PATCH /admin/suggestions/{id}."""
    action: Literal["approve", "reject"]

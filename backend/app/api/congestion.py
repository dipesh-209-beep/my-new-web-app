"""
api/congestion.py

GET /congestion — historical congestion stats for a given day-of-week /
hour-bucket, defaulting to "right now" in Nepal time.

Data comes from segment_congestion_stats, an aggregate table upserted by
api/routing.py with OSRM-derived durations (see _record_leg_congestion) and
optionally pre-populated by scripts/seed_congestion_stats.py. These are
routing-engine estimates, not measured traffic -- see
app/routing/congestion_provenance.py. This endpoint is pure read: it
classifies each row's congestion_ratio into a CongestionLevel and returns
it, plus a provenance block describing what the numbers are not, no writes.
"""

from fastapi import APIRouter, Query, Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.response_cache import cached_response
from app.db import queries
from app.db.session import get_db
from app.routing.congestion_provenance import PROVENANCE_NOTE
from app.routing.time_buckets import HOUR_BUCKETS, day_and_bucket_for, hour_bucket_start, now_in_nepal
from app.schemas import (
    CongestionLevel,
    CongestionProvenance,
    CongestionResponse,
    CongestionSegmentOut,
)

router = APIRouter(tags=["congestion"])
settings = get_settings()

# Ratio thresholds for avg_duration_s / free_flow_duration_s. Tuned to be
# forgiving of GPS/OSRM noise on short segments (a 90s hop naturally has
# more relative jitter than a 10-minute one) -- adjust here only; every
# consumer reads the classification off the API, not the raw ratio.
_MODERATE_RATIO = 1.15
_HEAVY_RATIO = 1.5

# The disclosure served with every response. The note text lives in
# app/routing/congestion_provenance.py so the API and its tests share one
# string. The flags are hardcoded False because no producer in this
# repository emits a live or measured source (see PRODUCER_SOURCES there);
# if one ever does, it must change both places together.
# See CongestionProvenance in app/schemas.py for the response-shape rationale.
_PROVENANCE = CongestionProvenance(
    is_live=False,
    is_measured=False,
    note=PROVENANCE_NOTE,
)


def _classify(ratio: float) -> CongestionLevel:
    if ratio >= _HEAVY_RATIO:
        return "heavy"
    if ratio >= _MODERATE_RATIO:
        return "moderate"
    return "free_flow"


@router.get("/congestion", response_model=CongestionResponse)
@cached_response(
    "congestion", ttl_seconds=settings.CONGESTION_CACHE_TTL_S, key_params=("day_of_week", "hour")
)
def get_congestion(
    day_of_week: int | None = Query(
        None, ge=0, le=6, description="0=Monday..6=Sunday. Defaults to today (Nepal time)."
    ),
    hour: int | None = Query(
        None,
        ge=0,
        le=23,
        description="Hour of day (0-23), rounded down to its 3-hour bucket. Defaults to now.",
    ),
    db: Session = Depends(get_db),
):
    if day_of_week is None or hour is None:
        default_dow, default_bucket = day_and_bucket_for(now_in_nepal())
        if day_of_week is None:
            day_of_week = default_dow
        hour_bucket = default_bucket if hour is None else hour_bucket_start(hour)
    else:
        hour_bucket = hour_bucket_start(hour)

    rows = queries.get_congestion_stats(db, day_of_week=day_of_week, hour_bucket=hour_bucket)

    segments = []
    for stat, free_flow_duration_s in rows:
        ratio = stat.avg_duration_s / free_flow_duration_s if free_flow_duration_s > 0 else 1.0
        segments.append(
            CongestionSegmentOut(
                route_id=stat.route_id,
                from_stop_id=stat.from_stop_id,
                to_stop_id=stat.to_stop_id,
                avg_duration_s=stat.avg_duration_s,
                avg_distance_m=stat.avg_distance_m,
                free_flow_duration_s=free_flow_duration_s,
                congestion_ratio=ratio,
                congestion_level=_classify(ratio),
                sample_count=stat.sample_count,
                is_seeded=stat.is_seeded,
            )
        )

    return CongestionResponse(
        day_of_week=day_of_week,
        hour_bucket=hour_bucket,
        segments=segments,
        provenance=_PROVENANCE,
    )


@router.get("/congestion/buckets")
def list_buckets():
    """The fixed set of valid hour buckets, so the frontend's time picker
    doesn't have to hardcode it separately."""
    return {"hour_buckets": list(HOUR_BUCKETS)}

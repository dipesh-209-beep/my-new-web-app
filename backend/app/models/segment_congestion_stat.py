"""ORM model for `segment_congestion_stats`.

One row per (route_id, from_stop_id, to_stop_id, day_of_week, hour_bucket) --
an *aggregate*, not a raw log. Rows are upserted (see
db/queries.record_congestion_sample), so the table stays bounded by the real
segment/time-bucket space instead of growing per request:

    max rows ~= (sum of ride-hops across all active routes) * 7 * 8

with hour_bucket in {0,3,6,9,12,15,18,21} (3-hour windows) rather than 24
one-hour buckets, trading a bit of time-of-day resolution for buckets that
fill up with enough samples to be statistically meaningful sooner -- the
whole point of "historical stats" over a cold, sparse table.

Populated two ways:
  1. Organically, via a FastAPI BackgroundTask on every /route-finder
     request that got real OSRM road_geometry for a leg (see
     app/api/routing.py::_record_leg_congestion). Doesn't add latency to
     the user-facing response.
  2. Proactively, via scripts/seed_congestion_stats.py, which walks every
     active route's consecutive stop pairs once and seeds all 8 buckets
     with a baseline OSRM duration (is_seeded=True, sample_count=1) so the
     map isn't blank before real traffic accumulates. The first *real*
     sample for a seeded row overwrites the baseline outright instead of
     averaging into it (see record_congestion_sample) so one synthetic
     guess doesn't permanently bias the average.
"""
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    TIMESTAMP,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship

from .base import Base


class SegmentCongestionStat(Base):
    __tablename__ = "segment_congestion_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # route_id is nullable to leave room for a future non-route-specific
    # (pure road-segment) key; every row written today always sets it.
    route_id = Column(Text, ForeignKey("routes.route_id", ondelete="CASCADE"), nullable=True)
    from_stop_id = Column(Text, ForeignKey("stops.stop_id", ondelete="CASCADE"), nullable=False)
    to_stop_id = Column(Text, ForeignKey("stops.stop_id", ondelete="CASCADE"), nullable=False)

    # 0=Monday .. 6=Sunday (Python's date.weekday()).
    day_of_week = Column(SmallInteger, nullable=False)
    # Start hour of a 3-hour window: 0, 3, 6, ..., 21.
    hour_bucket = Column(SmallInteger, nullable=False)

    avg_duration_s = Column(Float, nullable=False)
    avg_distance_m = Column(Float, nullable=False)
    sample_count = Column(Integer, nullable=False, server_default=text("0"))

    # The OSRM duration recorded once at seed time -- a genuine
    # free-driving estimate, anchored independent of avg_duration_s so a
    # segment that's congested in every bucket still has a real baseline
    # to compare against. Nullable: rows from before this column existed
    # fall back to the old min(avg_duration_s)-across-buckets estimate,
    # see queries.get_congestion_stats.
    free_flow_duration_s = Column(Float, nullable=True)

    # True until the first organic sample overwrites it -- lets
    # record_congestion_sample distinguish "one synthetic guess" from
    # "one real observation" when deciding whether to average or replace.
    is_seeded = Column(Boolean, nullable=False, server_default=text("false"))

    updated_at = Column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint(
            "route_id",
            "from_stop_id",
            "to_stop_id",
            "day_of_week",
            "hour_bucket",
            name="uq_segment_congestion_key",
        ),
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_congestion_day_of_week"),
        CheckConstraint(
            "hour_bucket IN (0,3,6,9,12,15,18,21)", name="ck_congestion_hour_bucket"
        ),
        CheckConstraint("sample_count >= 0", name="ck_congestion_sample_count"),
        # The hot-path query is always "give me every segment for this
        # (day_of_week, hour_bucket)", so that pair is the lead index --
        # not route_id/from_stop_id, which only matter for the upsert.
        Index("idx_congestion_day_hour", "day_of_week", "hour_bucket"),
        # Backs the get_congestion_stats fallback subquery's GROUP BY for
        # rows still missing free_flow_duration_s (see queries.py). Added
        # by migration e5f6a7b8c9d0 alongside a NULL-free flow backfill.
        Index(
            "ix_segment_congestion_stats_segment",
            "route_id",
            "from_stop_id",
            "to_stop_id",
        ),
    )

    route = relationship("Route")
    from_stop = relationship("Stop", foreign_keys=[from_stop_id])
    to_stop = relationship("Stop", foreign_keys=[to_stop_id])

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SegmentCongestionStat route={self.route_id} "
            f"{self.from_stop_id}->{self.to_stop_id} "
            f"dow={self.day_of_week} hr={self.hour_bucket}>"
        )

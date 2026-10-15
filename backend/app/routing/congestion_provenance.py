"""Provenance registry for the congestion feature.

An audit of this project's congestion data found that **every value it
serves is derived**, and that the origin of the static severity table
behind the geographic zones is not recorded anywhere in the repository:

- `segment_congestion_stats` rows written by the seed script are OSRM
  free-flow durations (`is_seeded=True`).
- Rows written organically from a `/route-finder` response are OSRM
  `road_geometry` durations (`is_seeded=False`).
- Rows written by the demo seeder are **fabricated**: a distance /
  assumed-speed free-flow guess scaled by a severity score. These are
  also stored `is_seeded=False`, so at the row level they are
  indistinguishable from the OSRM-derived rows above. `is_seeded` is a
  seed-vs-upsert flag, **not** a provenance field.
- The geographic zones' scores come from `data/congestion_loss_table.csv`,
  a static per-intersection severity table with no recorded author, date,
  method or license in this repository.

There is no live or field-measured traffic feed. This module is the single,
testable source of truth for those labels so the API, the seeding scripts,
the zones loader and the documentation agree, and so a test can assert
that nothing is ever presented as measured.

Labels are conservative on purpose. `MEASURED` is a category that no
producer in this repository emits, and `is_measured()` returns `False` for
every source a producer can currently emit. A genuine measurement source
must be introduced here, with its evidence, rather than inferred from a
stored flag such as `is_seeded == False`.
"""
from __future__ import annotations

from enum import Enum


class CongestionSource(str, Enum):
    """Where a congestion value came from. None of these is a measurement."""

    # OSRM free-flow duration, written to every time bucket by
    # scripts/seed_congestion_stats.py (stored is_seeded=True).
    OSRM_FREE_FLOW = "osrm_free_flow"

    # OSRM road_geometry duration recorded from a /route-finder response by
    # app/api/routing.py::_record_leg_congestion (stored is_seeded=False).
    OSRM_ROUTE_GEOMETRY = "osrm_route_geometry"

    # Durations fabricated by scripts/seed_demo_congestion.py -- a distance /
    # assumed-speed free-flow guess scaled by a severity score. Stored through
    # the normal upsert with is_seeded=False, so it is not distinguishable
    # from OSRM_ROUTE_GEOMETRY at the row level.
    SYNTHETIC_DEMO = "synthetic_demo"

    # A static per-intersection severity table (data/congestion_loss_table.csv)
    # backing the geographic zones and the demo seed's ratios. Its origin is
    # not recorded in this repository; treat the scores as unverified.
    SEVERITY_TABLE_UNVERIFIED = "severity_table_unverified"

    # Provenance was not recorded for this value.
    UNKNOWN = "unknown"

    # An actual field measurement of traffic. No producer in this repository
    # emits this; it is defined so a future measured source is represented
    # explicitly rather than being implied by another flag.
    MEASURED = "measured"


# The single source that denotes a real measurement. Everything else is
# derived, and must never be described as "live" or "measured".
_MEASURED_SOURCES = frozenset({CongestionSource.MEASURED})

# Sources a producer in this repository can actually write today. A test
# asserts MEASURED is not among them, so "is this measured?" can never be
# answered True by accident.
PRODUCER_SOURCES = frozenset(
    {
        CongestionSource.OSRM_FREE_FLOW,
        CongestionSource.OSRM_ROUTE_GEOMETRY,
        CongestionSource.SYNTHETIC_DEMO,
        CongestionSource.SEVERITY_TABLE_UNVERIFIED,
    }
)


def is_measured(source: "CongestionSource | str") -> bool:
    """True only for an actual field-measured source. Every current producer
    returns False here, by construction."""
    return CongestionSource(source) in _MEASURED_SOURCES


def is_derived(source: "CongestionSource | str") -> bool:
    return not is_measured(source)


# The human-readable disclosure served with GET /congestion and rendered by
# the frontend. Kept here so the API and its tests share one string; the
# frontend keeps a matching fallback constant (CongestionPanel.tsx) for the
# case where an older cached response omits provenance entirely.
PROVENANCE_NOTE = (
    "Estimated, not measured. Congestion values are derived from OSRM "
    "free-flow durations and a static severity table whose origin is not "
    "recorded in this repository -- there is no live or real-time traffic "
    "feed. Segments may be incomplete, and a green segment does not "
    "guarantee a clear road."
)

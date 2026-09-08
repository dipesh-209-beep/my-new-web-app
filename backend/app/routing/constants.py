"""Small set of tunable constants (meters)."""
EARTH_RADIUS: int = 6_371_000
INTERCHANGE_DISTANCE: int = 200
TRANSFER_PENALTY: int = 3000

# How strongly the Dijkstra transfer-search fallback (find_shortest_path
# with avoid_congestion=True) penalizes a "ride" edge in proportion to
# its recorded congestion_ratio. A ride edge's weight becomes
#     distance_m * (1 + CONGESTION_LAMBDA * max(congestion_ratio - 1, 0))
# so free-flow edges (ratio ~= 1) are untouched and only genuinely
# congested edges get inflated. Starting value, tune by hand against a
# few known-congested corridors (Koteshwor, Kalanki, Thapathali) before
# trusting it in production.
CONGESTION_LAMBDA: float = 0.75

# Used ONLY to rank the "fastest_estimated" route-finder alternative --
# rough constant assumptions, not measured or real-time data. No per-edge
# duration exists in the graph itself (OSRM duration is computed after a
# path is already chosen, per full leg polyline -- see
# api/routing.py:_attach_road_geometry), so this is the only way to rank
# by estimated time before that point. Kathmandu Valley in-city bus
# speeds are commonly cited in the 10-15 km/h range during regular
# traffic; walking pace is the standard ~4.7 km/h.
BUS_AVG_SPEED_MPS: float = 3.3  # ~12 km/h
WALK_SPEED_MPS: float = 1.3  # ~4.7 km/h
BOARD_WAIT_S: float = 180.0  # assumed average wait for a bus to arrive

"""
test_clean_data.py — regression tests for clean_data.py, using tiny in-memory
fixtures instead of the full dataset. Run with: pytest scripts/test_clean_data.py

These pin the specific behaviors report.md documents, so a future refactor
can't silently change orphan-removal, resequencing, or operator_id-nulling
logic without a test failing.
"""

import pandas as pd
import pytest

from pathlib import Path

from clean_data import CleaningStats, clean_route_stops, clean_routes, clean_stops, dedup_stops, dedup_routes, _stop_set_similarity, overrides_path
from validate_clean import consecutive_duplicate_stops, route_service_checks


@pytest.fixture
def stops_raw():
    return pd.DataFrame([
        {"stop_id": "S001", "stop_name": "A", "lat": "27.70", "lng": "85.30",
         "aliases": "", "zone": "Z", "district": "KATHMANDU", "ward": "1",
         "is_major_stop": "False", "landmark": "", "has_shelter": "False",
         "has_ticket_counter": "False", "is_interchange": "False",
         "wheelchair_access": "False", "audio_support": "False", "status": "active"},
        {"stop_id": "S002", "stop_name": "B", "lat": "27.71", "lng": "85.31",
         "aliases": "", "zone": "Z", "district": "KATHMANDU", "ward": "2",
         "is_major_stop": "False", "landmark": "", "has_shelter": "False",
         "has_ticket_counter": "False", "is_interchange": "False",
         "wheelchair_access": "False", "audio_support": "False", "status": "active"},
        {"stop_id": "S003", "stop_name": "C", "lat": "99.00", "lng": "85.32",
         "aliases": "", "zone": "Z", "district": "KATHMANDU", "ward": "3",
         "is_major_stop": "False", "landmark": "", "has_shelter": "False",
         "has_ticket_counter": "False", "is_interchange": "False",
         "wheelchair_access": "False", "audio_support": "False", "status": "active"},
    ])


def test_clean_stops_flags_out_of_bounds(stops_raw):
    stops = clean_stops(stops_raw)
    assert stops.loc[stops["stop_id"] == "S003", "geo_out_of_bounds"].iloc[0]
    assert not stops.loc[stops["stop_id"] == "S001", "geo_out_of_bounds"].iloc[0]


def test_clean_route_stops_removes_orphans_and_resequences(stops_raw):
    stops = clean_stops(stops_raw)
    route_stops_raw = pd.DataFrame([
        {"route_id": "R1", "stop_id": "S001", "sequence_no": "1"},
        {"route_id": "R1", "stop_id": "S999", "sequence_no": "2"},  # orphan: no such stop
        {"route_id": "R1", "stop_id": "S002", "sequence_no": "3"},
    ])
    stats = CleaningStats()
    cleaned = clean_route_stops(route_stops_raw, stops, stats)

    assert stats.orphan_route_stops_removed == 1
    assert "S999" in stats.phantom_stop_ids
    assert list(cleaned["stop_id"]) == ["S001", "S002"]
    assert list(cleaned["sequence_no"]) == [1, 2]  # re-numbered, no gap


def test_clean_route_stops_flags_revisits_without_removing(stops_raw):
    stops = clean_stops(stops_raw)
    route_stops_raw = pd.DataFrame([
        {"route_id": "R1", "stop_id": "S001", "sequence_no": "1"},
        {"route_id": "R1", "stop_id": "S002", "sequence_no": "2"},
        {"route_id": "R1", "stop_id": "S001", "sequence_no": "3"},  # loop back to S001
    ])
    stats = CleaningStats()
    cleaned = clean_route_stops(route_stops_raw, stops, stats)

    assert len(cleaned) == 3  # nothing removed — revisits are legitimate
    assert stats.revisit_rows == 1
    assert stats.revisit_routes == 1


def test_clean_routes_nulls_unrecoverable_operator_id(stops_raw):
    stops = clean_stops(stops_raw)
    route_stops_raw = pd.DataFrame([
        {"route_id": "R1", "stop_id": "S001", "sequence_no": "1"},
        {"route_id": "R1", "stop_id": "S002", "sequence_no": "2"},
    ])
    stats = CleaningStats()
    route_stops = clean_route_stops(route_stops_raw, stops, stats)

    operators = pd.DataFrame([{"operator_id": "OP001", "name": "Real Operator"}])
    route_operators = pd.DataFrame(columns=["route_id", "operator_id"])

    routes_raw = pd.DataFrame([{
        "route_id": "R1", "route_name": "Test Route", "vehicle_type": "bus",
        "operator": "Unregistered Micro", "operator_id": "OP999",  # invalid
        "operator_id_raw": "OP999", "start_stop_id": "S001", "end_stop_id": "S002",
        "total_stops": "2", "approx_distance_km": "1.0",
        "approx_distance_km_original": "1.0",
    }])

    routes = clean_routes(routes_raw, route_stops, stops, operators, route_operators, stats)

    assert pd.isna(routes.loc[0, "operator_id"])
    assert "OP999" in stats.invalid_operator_ids
    assert "R1" in stats.operator_id_nulled_routes


def test_clean_routes_recomputes_total_stops_after_orphan_removal(stops_raw):
    stops = clean_stops(stops_raw)
    route_stops_raw = pd.DataFrame([
        {"route_id": "R1", "stop_id": "S001", "sequence_no": "1"},
        {"route_id": "R1", "stop_id": "S999", "sequence_no": "2"},  # orphan
        {"route_id": "R1", "stop_id": "S002", "sequence_no": "3"},
    ])
    stats = CleaningStats()
    route_stops = clean_route_stops(route_stops_raw, stops, stats)

    operators = pd.DataFrame([{"operator_id": "OP001", "name": "Real Operator"}])
    route_operators = pd.DataFrame(columns=["route_id", "operator_id"])
    routes_raw = pd.DataFrame([{
        "route_id": "R1", "route_name": "Test Route", "vehicle_type": "bus",
        "operator": "Real Operator", "operator_id": "OP001", "operator_id_raw": "OP001",
        "start_stop_id": "S001", "end_stop_id": "S001",  # wrong on purpose
        "total_stops": "3",  # wrong on purpose (was 3 before orphan removal)
        "approx_distance_km": "1.0", "approx_distance_km_original": "1.0",
    }])

    routes = clean_routes(routes_raw, route_stops, stops, operators, route_operators, stats)

    assert routes.loc[0, "end_stop_id"] == "S002"
    assert int(routes.loc[0, "total_stops"]) == 2
    assert "R1" in stats.end_stop_corrected
    assert "R1" in stats.total_stops_corrected


def test_stop_set_similarity_basic():
    a = frozenset({"S1", "S2", "S3"})
    b = frozenset({"S2", "S3", "S4"})
    assert _stop_set_similarity(a, b) == pytest.approx(2 / 4)
    assert _stop_set_similarity(frozenset(), frozenset()) == 1.0
    assert _stop_set_similarity(a, a) == 1.0


def test_consecutive_duplicate_stops_flags_adjacent_repeats():
    # AUD-007: adjacent identical stops used to slip past the validator.
    rs = pd.DataFrame([
        {"route_id": "R1", "stop_id": "S1", "sequence_no": "1"},
        {"route_id": "R1", "stop_id": "S2", "sequence_no": "2"},
        {"route_id": "R1", "stop_id": "S2", "sequence_no": "3"},  # adjacent dup
        {"route_id": "R1", "stop_id": "S1", "sequence_no": "4"},
        {"route_id": "R2", "stop_id": "S9", "sequence_no": "1"},
        {"route_id": "R2", "stop_id": "S8", "sequence_no": "2"},
    ])

    assert consecutive_duplicate_stops(rs) == {"R1": ["seq 2/3 S2"]}


def test_consecutive_duplicate_stops_orders_sequence_numerically():
    # sequence_no is str in the processed CSVs; a lexicographic sort would
    # put "10" before "2" and miss the 9/10 pair.
    rs = pd.DataFrame([
        {"route_id": "R1", "stop_id": "A", "sequence_no": "9"},
        {"route_id": "R1", "stop_id": "A", "sequence_no": "10"},
        {"route_id": "R1", "stop_id": "B", "sequence_no": "2"},
    ])

    assert consecutive_duplicate_stops(rs) == {"R1": ["seq 9/10 A"]}


def test_consecutive_duplicate_stops_handles_missing_columns():
    assert consecutive_duplicate_stops(pd.DataFrame({"route_id": ["R1"]})) == {}


def test_overrides_path_resolves_next_to_script_not_cwd(monkeypatch, tmp_path):
    # AUD-016: CI runs the pipeline from `data/`, so a cwd-relative
    # Path("data/scripts/...") became data/data/scripts/... and every
    # override was silently skipped. Resolution must follow the script.
    import clean_data

    monkeypatch.chdir(tmp_path)
    resolved = overrides_path("stop_dedup_overrides.yaml")

    assert resolved.is_absolute()
    assert resolved.parent == Path(clean_data.__file__).resolve().parent
    assert resolved.exists()


def test_missing_override_file_warns_instead_of_silently_skipping(tmp_path, caplog):
    stops = pd.DataFrame([
        {"stop_id": "S001", "stop_name": "A", "lat": 27.7000, "lng": 85.3000},
        {"stop_id": "S002", "stop_name": "A dup", "lat": 27.70010, "lng": 85.3000},
    ])
    missing = tmp_path / "does_not_exist.yaml"

    with caplog.at_level("WARNING", logger="clean_data"):
        dedup_stops(stops, CleaningStats(), overrides_path=missing)

    assert "does_not_exist.yaml" in caplog.text


def test_dedup_stops_proposes_but_does_not_merge_without_overrides():
    # Two stops ~11m apart (well within 250m radius) — should be proposed as a
    # candidate cluster but NOT merged, since no overrides_path is given.
    stops = pd.DataFrame([
        {"stop_id": "S001", "stop_name": "A", "lat": 27.7000, "lng": 85.3000},
        {"stop_id": "S002", "stop_name": "A dup", "lat": 27.70010, "lng": 85.3000},
        {"stop_id": "S003", "stop_name": "Far away", "lat": 27.9000, "lng": 85.5000},
    ])
    stats = CleaningStats()
    deduped, remap = dedup_stops(stops, stats, overrides_path=None)

    assert remap == {}
    assert len(deduped) == 3  # nothing dropped
    assert any(set(c) == {"S001", "S002"} for c in stats.stop_dedup_candidates)
    assert any(set(c) == {"S001", "S002"} for c in stats.stop_dedup_pending_review)


def test_dedup_stops_merges_when_confirmed(tmp_path):
    stops = pd.DataFrame([
        {"stop_id": "S001", "stop_name": "A", "lat": 27.7000, "lng": 85.3000},
        {"stop_id": "S002", "stop_name": "A dup", "lat": 27.70010, "lng": 85.3000},
    ])
    overrides = tmp_path / "stop_dedup_overrides.yaml"
    overrides.write_text("confirmed_merges:\n  - keep: S001\n    drop: [S002]\n")

    stats = CleaningStats()
    deduped, remap = dedup_stops(stops, stats, overrides_path=overrides)

    assert remap == {"S002": "S001"}
    assert list(deduped["stop_id"]) == ["S001"]
    assert stats.stop_dedup_dropped == 1
    assert ["S001", "S002"] in stats.stop_dedup_groups


def test_dedup_routes_merges_and_sets_bidirectional(tmp_path):
    routes = pd.DataFrame([
        {"route_id": "R1", "route_name": "A-B", "operator_id": "OP1"},
        {"route_id": "R2", "route_name": "B-A", "operator_id": "OP1"},
    ])
    route_stops = pd.DataFrame([
        {"route_id": "R1", "stop_id": "S1", "sequence_no": 1},
        {"route_id": "R1", "stop_id": "S2", "sequence_no": 2},
        {"route_id": "R2", "stop_id": "S2", "sequence_no": 1},
        {"route_id": "R2", "stop_id": "S1", "sequence_no": 2},
    ])
    route_operators = pd.DataFrame([
        {"route_id": "R1", "operator_id": "OP1"},
        {"route_id": "R2", "operator_id": "OP1"},
    ])
    overrides = tmp_path / "route_dedup_overrides.yaml"
    overrides.write_text(
        "confirmed_merges:\n  - keep: R1\n    drop: [R2]\n    bidirectional: true\n"
    )

    stats = CleaningStats()
    new_routes, new_route_stops, new_route_operators, dropped_route_ids = dedup_routes(
        routes, route_stops, route_operators, stats, overrides_path=overrides
    )

    assert list(new_routes["route_id"]) == ["R1"]
    assert new_routes.loc[new_routes["route_id"] == "R1", "is_bidirectional"].iloc[0] == True
    assert "R2" not in set(new_route_stops["route_id"])
    assert ("R1", "R2") in stats.route_dedup_merged
    assert dropped_route_ids == {"R2"}


def test_dedup_routes_warns_and_skips_unknown_route_id(tmp_path, caplog):
    routes = pd.DataFrame([
        {"route_id": "R1", "route_name": "A-B", "operator_id": "OP1"},
    ])
    route_stops = pd.DataFrame([
        {"route_id": "R1", "stop_id": "S1", "sequence_no": 1},
    ])
    route_operators = pd.DataFrame([{"route_id": "R1", "operator_id": "OP1"}])
    overrides = tmp_path / "route_dedup_overrides.yaml"
    overrides.write_text(
        "confirmed_merges:\n  - keep: R1\n    drop: [R999]\n"  # R999 doesn't exist
    )

    stats = CleaningStats()
    new_routes, _, _, dropped_route_ids = dedup_routes(
        routes, route_stops, route_operators, stats, overrides_path=overrides
    )

    assert list(new_routes["route_id"]) == ["R1"]  # nothing dropped, R1 untouched
    assert stats.route_dedup_merged == []
    assert dropped_route_ids == set()


# ---------------------------------------------------------------------------
# route_service_checks (validate_clean.py) — the R4 frequency/service-hours
# integrity checks that guard what RouteOut will expose to the API/UI.
# ---------------------------------------------------------------------------

def _routes_with_service(rows):
    return pd.DataFrame(rows)


def test_route_service_checks_flags_unusable_frequency():
    routes = _routes_with_service([
        {"frequency_min": "15"},    # ok
        {"frequency_min": "30"},    # ok
        {"frequency_min": ""},      # unknown, not a failure
        {"frequency_min": "0"},     # non-positive
        {"frequency_min": "-5"},    # negative
        {"frequency_min": "12.5"},  # fractional
        {"frequency_min": "often"}, # non-numeric
    ])
    checks = route_service_checks(routes)
    assert checks["routes.frequency_min non-positive or non-integer"] == 4


def test_route_service_checks_flags_malformed_times():
    routes = _routes_with_service([
        {"service_start_time": "06:00:00", "service_end_time": "20:00:00"},  # ok
        {"service_start_time": "6:00",     "service_end_time": "20:00:00"},  # unpadded
        {"service_start_time": "06:60:00", "service_end_time": "20:00:00"},  # minute range
        {"service_start_time": "24:00:00", "service_end_time": "20:00:00"},  # hour range
        {"service_start_time": "",         "service_end_time": ""},          # empty, ok
    ])
    checks = route_service_checks(routes)
    assert checks["routes.service_start_time invalid"] == 3
    assert checks["routes.service_end_time invalid"] == 0


def test_route_service_checks_flags_reversed_or_equal_window():
    routes = _routes_with_service([
        {"service_start_time": "06:00:00", "service_end_time": "20:00:00"},  # ok
        {"service_start_time": "20:00:00", "service_end_time": "06:00:00"},  # reversed
        {"service_start_time": "06:00:00", "service_end_time": "06:00:00"},  # equal
        {"service_start_time": "06:00:00", "service_end_time": ""},          # one-sided
    ])
    checks = route_service_checks(routes)
    # One-sided windows are handled at read time (dropped to unknown), not
    # flagged here — only complete-but-unordered windows fail this check.
    assert checks["routes.service window start >= end"] == 2


def test_route_service_checks_passes_on_clean_data():
    routes = _routes_with_service([
        {"frequency_min": "15", "service_start_time": "06:00:00", "service_end_time": "20:00:00"},
        {"frequency_min": "30", "service_start_time": "06:00:00", "service_end_time": "19:00:00"},
        {"frequency_min": "",   "service_start_time": "",         "service_end_time": ""},
    ])
    checks = route_service_checks(routes)
    assert all(count == 0 for count in checks.values())


def test_route_service_checks_skips_absent_columns():
    # A fixture/frame without the service columns must not raise.
    assert route_service_checks(pd.DataFrame([{"route_id": "R1"}])) == {}

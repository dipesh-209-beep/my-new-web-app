"""
tests/test_congestion_provenance.py

Unit tests for app.routing.congestion_provenance: the single registry of
where a congestion value came from.

The point of these tests is that "is this measured?" can never be answered
True by accident. Every producer in this repository emits a derived value,
so is_measured() must be False for all of them; only an explicitly-defined
MEASURED source is True, and nothing can emit it today. No DB needed.
"""
import csv
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.routing import congestion_provenance as cp


def test_every_producer_source_is_derived():
    """No source a producer can write is measured. This is the core
    guarantee: the app has no measured input, so nothing it serves may be
    labelled measured."""
    assert cp.PRODUCER_SOURCES, "there must be at least one producer source"
    for source in cp.PRODUCER_SOURCES:
        assert cp.is_measured(source) is False, source
        assert cp.is_derived(source) is True, source


def test_measured_source_is_not_a_producer_source():
    """MEASURED exists as a category (so a real feed can be represented
    explicitly later) but no producer emits it yet. If this starts failing
    because a measured feed was genuinely added, the API's
    is_live/is_measured flags and the disclosure note must change with it."""
    assert cp.CongestionSource.MEASURED not in cp.PRODUCER_SOURCES


def test_is_measured_true_only_for_measured():
    assert cp.is_measured(cp.CongestionSource.MEASURED) is True
    assert cp.is_measured(cp.CongestionSource.MEASURED.value) is True


@pytest.mark.parametrize(
    "source",
    [
        cp.CongestionSource.OSRM_FREE_FLOW,
        cp.CongestionSource.OSRM_ROUTE_GEOMETRY,
        cp.CongestionSource.SYNTHETIC_DEMO,
        cp.CongestionSource.SEVERITY_TABLE_UNVERIFIED,
        cp.CongestionSource.UNKNOWN,
    ],
)
def test_is_measured_false_for_each_derived_source(source):
    assert cp.is_measured(source) is False
    assert cp.is_derived(source) is True


def test_is_measured_rejects_unknown_strings():
    """A typo'd or foreign label must not be treated as measured (nor
    silently accepted) -- it raises, so a bad source is caught rather than
    mislabelled."""
    with pytest.raises(ValueError):
        cp.is_measured("definitely-a-real-measurement-honest")


def test_provenance_note_denies_live_and_measured():
    note = cp.PROVENANCE_NOTE.lower()
    assert "estimated" in note
    assert "not measured" in note
    assert "no live" in note
    # Names the real, weaker origin rather than implying a study.
    assert "severity table" in note
    assert "not recorded" in note
    # Holds even if the note is read without the API's boolean flags.
    assert "measured" in note
    assert note.count("measured") == note.count("not measured")


def test_shipped_scores_csv_has_no_measured_source():
    """The demo seeder's manifest must not label its rows measured either:
    its scores come from the same unrecorded-origin table as the zones, and
    the durations it writes are synthetic. Located beside congestion_zones.csv
    because both are derived from congestion_loss_table.csv and are mounted
    into the backend's data directory."""
    zones_path = Path(get_settings().CONGESTION_ZONES_PATH)
    scores_path = zones_path.parent / "stop_congestion_scores.csv"
    assert scores_path.is_file(), f"expected {scores_path}"
    with scores_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 0
    sources = {r["source"] for r in rows}
    assert "measured" not in sources, f"a scores row is still labelled measured: {scores_path}"
    assert sources <= {"severity_table_unverified", "fallback_average"}, (
        f"unexpected scores source values: {sources}"
    )

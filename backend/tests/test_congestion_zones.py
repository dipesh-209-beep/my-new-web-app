"""
tests/test_congestion_zones.py

Unit tests for app.routing.congestion_zones: the CSV-driven, static
geographic-congestion mechanism (distinct from app/db/queries.py's
organic, per-route-segment congestion -- see this module's own
docstring for how the two combine in pathfinder.py). No DB or graph
needed; everything here is pure functions plus a CSV fixture.

Two tests here exist specifically to pin down how the CSV is *located*,
not just how it is parsed -- see test_default_zones_path_points_at_a_real_file.
Every other test monkeypatches ZONES_PATH at a fixture path, so the suite
once confirmed load_zones() coped with a missing file while never checking
that the real default resolved to a real file anywhere.
"""
import csv
import logging
from pathlib import Path

import pytest

from app.routing import congestion_zones as cz


@pytest.fixture(autouse=True)
def _reset_cache():
    cz.reset_zones_cache()
    yield
    cz.reset_zones_cache()


def test_score_to_ratio_endpoints():
    """0 -> ~free-flow, 10 -> the documented 4.2 ceiling."""
    assert cz.score_to_ratio(0) == pytest.approx(1.05)
    assert cz.score_to_ratio(10) == pytest.approx(4.2)


def test_radius_for_score_endpoints():
    """200m for negligible, 500m for the worst measured score."""
    assert cz.radius_for_score(0) == pytest.approx(200)
    assert cz.radius_for_score(10) == pytest.approx(500)


def test_load_zones_reads_csv(tmp_path, monkeypatch):
    csv_path = tmp_path / "congestion_zones.csv"
    csv_path.write_text(
        "stop_id,stop_name,lat,lng,score,radius_m,source,real_location_name\n"
        "S0056,Tripureshwor,27.694418,85.314128,10.0,500,measured,Tripureshwor\n"
    )
    monkeypatch.setattr(cz, "ZONES_PATH", csv_path)

    zones = cz.load_zones()
    assert len(zones) == 1
    zone = zones[0]
    assert zone.stop_id == "S0056"
    assert zone.radius_m == pytest.approx(500)
    assert zone.ratio == pytest.approx(cz.score_to_ratio(10.0))


def test_load_zones_missing_file_returns_empty(tmp_path, monkeypatch, caplog):
    """No CSV yet (e.g. a fresh checkout before the data file is added)
    should degrade to "no zones" rather than raising -- but it must SAY so.

    The silent half of this behaviour is what hid the file being absent
    from the container image for so long: every ratio came back 1.0 and
    the geographic half of the congestion model was simply gone, with
    nothing in the logs. The warning naming the resolved path is the
    regression guard."""
    monkeypatch.setattr(cz, "ZONES_PATH", tmp_path / "does_not_exist.csv")
    with caplog.at_level(logging.WARNING, logger="app.routing.congestion_zones"):
        assert cz.load_zones() == []
    assert any(
        "does_not_exist.csv" in record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ), "a missing zones CSV must log a warning naming the path it looked for"


def test_load_zones_empty_file_warns(tmp_path, monkeypatch, caplog):
    """A present-but-empty CSV is a different fault (truncated or
    header-only file) and is also silent unless called out, so it gets its
    own warning rather than being folded into the missing-file branch."""
    csv_path = tmp_path / "congestion_zones.csv"
    csv_path.write_text("stop_id,stop_name,lat,lng,score,radius_m,source,real_location_name\n")
    monkeypatch.setattr(cz, "ZONES_PATH", csv_path)
    with caplog.at_level(logging.WARNING, logger="app.routing.congestion_zones"):
        assert cz.load_zones() == []
    assert any(
        "no usable rows" in record.getMessage() for record in caplog.records
    ), "a zones CSV that parses to nothing must be reported"


def test_load_zones_path_pointing_at_a_directory_degrades(tmp_path, monkeypatch, caplog):
    """A CONGESTION_ZONES_PATH that resolves to a directory must not raise.

    Path("") collapses to ".", so a blank value in a .env -- the natural way
    to write "use the default" -- would otherwise reach open() and raise
    IsADirectoryError from a function whose contract is to degrade rather
    than raise. The Settings validator catches the blank case, but a
    deployment can still point the setting at a directory by hand, and this
    function must hold its contract either way."""
    monkeypatch.setattr(cz, "ZONES_PATH", tmp_path)  # the directory itself
    with caplog.at_level(logging.WARNING, logger="app.routing.congestion_zones"):
        assert cz.load_zones() == []
    assert any("not a file" in record.getMessage() for record in caplog.records)


def test_blank_zones_path_setting_falls_back_to_the_default():
    """A blank CONGESTION_ZONES_PATH means "unset", not "the current
    directory".

    This is not hypothetical: scripts/gen_backend_env.py copies
    backend/.env.example to backend/.env verbatim, so writing
    `CONGESTION_ZONES_PATH=` (uncommented) in the template would ship that
    blank to every developer. Pydantic would take "" as an explicit value
    and Path("") would resolve to "." -- a directory."""
    from app.core.config import Settings

    blank = Settings(CONGESTION_ZONES_PATH="")
    whitespace = Settings(CONGESTION_ZONES_PATH="   ")
    default = Settings()
    assert blank.CONGESTION_ZONES_PATH == default.CONGESTION_ZONES_PATH
    assert whitespace.CONGESTION_ZONES_PATH == default.CONGESTION_ZONES_PATH
    assert Path(blank.CONGESTION_ZONES_PATH).is_file(), "the fallback must still be a real file"


def test_default_zones_path_points_at_a_real_file():
    """The DEFAULT path (no CONGESTION_ZONES_PATH set) must resolve to a
    file that exists and parses.

    This is the test that would have caught the container bug. Every other
    test in this file monkeypatches ZONES_PATH to a tmp_path fixture, so
    the suite confirmed load_zones() handles absence correctly while never
    checking that the *real* default resolves to anything at all. It did
    not: `parents[3]` from backend/app/routing/ lands on the repo root on
    a host run and on `/` inside the image, where data/ does not exist.

    Docker handles that case properly now -- docker-compose.yml
    bind-mounts the CSV and sets CONGESTION_ZONES_PATH -- so this asserts
    the host default, which is what CI and a bare pytest run exercise."""
    from app.core.config import get_settings

    default_path = Path(get_settings().CONGESTION_ZONES_PATH)
    assert default_path.is_absolute(), f"default ZONES_PATH must be absolute, got {default_path}"
    assert default_path.is_file(), (
        f"default congestion zones path does not exist: {default_path}. "
        "On a host run this should be <repo>/data/congestion_zones.csv; "
        "in Docker CONGESTION_ZONES_PATH is set by docker-compose.yml to "
        "the bind-mounted path."
    )
    # And it must actually load, not merely exist -- a zero-byte or
    # header-only file would satisfy is_file() and still disable the
    # feature. Deliberately does not go through the module-level cache.
    with default_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 0, f"{default_path} parsed to zero rows"
    assert all(r["score"] and r["radius_m"] for r in rows), "every shipped row needs score and radius_m"


def test_zones_path_is_env_overridable(monkeypatch, tmp_path):
    """CONGESTION_ZONES_PATH is a real setting, not a hardcoded path, so
    a deployment can point the app at its own copy of the CSV.

    ZONES_PATH is deliberately a module-level constant bound at import
    time rather than re-read per call: it is what every other test here
    monkeypatches, and re-reading Settings on each load would make that
    patching a lie. The env var is set before the process starts (it comes
    from docker-compose.yml), so import-time binding is the right moment --
    hence the reload, which is what a fresh process would do."""
    import importlib

    from app.core import config

    csv_path = tmp_path / "elsewhere.csv"
    csv_path.write_text(
        "stop_id,stop_name,lat,lng,score,radius_m,source,real_location_name\n"
        "S1,A,27.7,85.3,5.0,300,measured,A\n"
    )
    monkeypatch.setenv("CONGESTION_ZONES_PATH", str(csv_path))

    # Settings reads it from the environment...
    reloaded = config.Settings()
    assert reloaded.CONGESTION_ZONES_PATH == str(csv_path)

    # ...and a module imported under that environment derives ZONES_PATH
    # from it, then loads the file that is actually there.
    #
    # get_settings() is lru_cached, so the cache is cleared here to stand
    # in for a fresh process. That is not a workaround for the test's
    # benefit: in a real deployment the variable is in the environment
    # before the process starts (docker-compose.yml `environment:`), so
    # the first -- and only -- call sees it. The cache is what stops
    # Settings being re-read per request.
    config.get_settings.cache_clear()
    try:
        reimported = importlib.reload(cz)
        assert Path(reimported.ZONES_PATH) == csv_path
        zones = reimported.load_zones()
        assert len(zones) == 1
        assert zones[0].stop_id == "S1"
    finally:
        # Put both the module and the settings cache back on the repo
        # default for whatever runs next in this session.
        monkeypatch.delenv("CONGESTION_ZONES_PATH", raising=False)
        config.get_settings.cache_clear()
        importlib.reload(cz)
        cz.reset_zones_cache()


def test_load_zones_is_cached_until_reset(tmp_path, monkeypatch):
    csv_path = tmp_path / "congestion_zones.csv"
    csv_path.write_text(
        "stop_id,stop_name,lat,lng,score,radius_m,source,real_location_name\n"
        "S1,A,27.7,85.3,5.0,300,measured,A\n"
    )
    monkeypatch.setattr(cz, "ZONES_PATH", csv_path)

    first = cz.load_zones()
    assert len(first) == 1

    # Rewrite the CSV without invalidating the cache -- load_zones()
    # should keep returning the stale, already-cached result.
    csv_path.write_text(
        "stop_id,stop_name,lat,lng,score,radius_m,source,real_location_name\n"
        "S1,A,27.7,85.3,5.0,300,measured,A\n"
        "S2,B,27.8,85.4,9.0,450,measured,B\n"
    )
    assert cz.load_zones() is first
    assert len(cz.load_zones()) == 1

    cz.reset_zones_cache()
    assert len(cz.load_zones()) == 2


def test_ratio_for_point_inside_and_outside_zone():
    zone = cz.CongestionZone(
        stop_id="S1", name="A", lat=27.7000, lng=85.3100, radius_m=300, ratio=3.0
    )
    assert cz.ratio_for_point(27.7000, 85.3100, zones=[zone]) == pytest.approx(3.0)
    # ~11km away -- well outside a 300m radius.
    assert cz.ratio_for_point(27.8000, 85.3100, zones=[zone]) == pytest.approx(1.0)


def test_ratio_for_point_takes_max_not_sum_of_overlapping_zones():
    """Two overlapping zones shouldn't stack additively -- the worse one
    wins, matching how a single physical traffic jam behaves."""
    weak = cz.CongestionZone(stop_id="S1", name="A", lat=27.7, lng=85.31, radius_m=1000, ratio=1.5)
    strong = cz.CongestionZone(stop_id="S2", name="B", lat=27.7, lng=85.31, radius_m=1000, ratio=4.0)
    assert cz.ratio_for_point(27.7, 85.31, zones=[weak, strong]) == pytest.approx(4.0)


def test_ratio_for_point_no_zones_is_free_flow():
    assert cz.ratio_for_point(27.7, 85.31, zones=[]) == pytest.approx(1.0)


def test_ratio_for_segment_triggers_from_either_endpoint():
    zone = cz.CongestionZone(
        stop_id="S1", name="A", lat=27.7000, lng=85.3100, radius_m=200, ratio=2.5
    )
    # "from" endpoint inside the zone, "to" endpoint far outside.
    assert cz.ratio_for_segment(27.7000, 85.3100, 27.9000, 85.5000, zones=[zone]) == pytest.approx(2.5)
    # "to" endpoint inside the zone, "from" endpoint far outside.
    assert cz.ratio_for_segment(27.9000, 85.5000, 27.7000, 85.3100, zones=[zone]) == pytest.approx(2.5)


def test_ratio_for_segment_neither_endpoint_in_zone():
    zone = cz.CongestionZone(
        stop_id="S1", name="A", lat=27.7000, lng=85.3100, radius_m=200, ratio=2.5
    )
    ratio = cz.ratio_for_segment(27.9000, 85.5000, 28.0000, 85.6000, zones=[zone])
    assert ratio == pytest.approx(1.0)

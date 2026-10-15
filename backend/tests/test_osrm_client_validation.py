"""
tests/test_osrm_client_validation.py

Regression tests for app/routing/osrm_client.py::_parse_route_response and
its use inside get_route_geometry.

An HTTP 200 from OSRM proves the request succeeded, not that the body is
usable: the response can be truncated, the wrong shape, missing fields, or
carry a non-finite/negative measurement. Before this, the client reached
straight into data["routes"][0]["geometry"]/["distance"]/["duration"], so a
malformed payload raised KeyError/IndexError/TypeError (or a JSONDecodeError
for a non-JSON body) instead of the OSRMError the callers already catch --
and an invalid number would flow into a total, fare or duration. These tests
pin down that every malformed case becomes a controlled OSRMError, while a
valid payload is still returned unchanged.

No live OSRM, DB or network: httpx.Client.get is replaced with a fake that
returns a caller-supplied decoded body (or raises as if the body were not
JSON). The module-global route cache and circuit breaker are reset per test
so neither leaks across cases.
"""

import json
import os

# app.routing.osrm_client imports app.core.config at import time; set
# harmless values so this file runs without a .env, like the other
# DB-less test modules (see test_attach_road_geometry.py).
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")

import httpx
import pytest

from app.routing import osrm_client
from app.routing.osrm_client import OSRMError

_COORDS = [(27.70, 85.30), (27.71, 85.31)]


def _valid_payload(distance=1234.5, duration=210.0, coordinates=None):
    return {
        "code": "Ok",
        "routes": [
            {
                "geometry": {
                    "type": "LineString",
                    "coordinates": (
                        coordinates
                        if coordinates is not None
                        else [[85.30, 27.70], [85.31, 27.71]]
                    ),
                },
                "distance": distance,
                "duration": duration,
            }
        ],
    }


class _FakeResponse:
    """Minimal stand-in for httpx.Response -- only the two methods
    get_route_geometry touches, over a body we control."""

    def __init__(self, payload=None, *, bad_json=False, status_code=200):
        self._payload = payload
        self._bad_json = bad_json
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("GET", "http://osrm.test/route"),
                response=self,
            )

    def json(self):
        if self._bad_json:
            raise json.JSONDecodeError("Expecting value", "", 0)
        return self._payload


@pytest.fixture(autouse=True)
def _isolate_client_state(monkeypatch):
    """The route cache and circuit breaker are module globals shared by
    every call; give each test its own so one case can't satisfy or trip
    another."""
    monkeypatch.setattr(osrm_client, "_route_cache", {})
    monkeypatch.setattr(osrm_client, "_circuit_breaker", osrm_client.CircuitBreaker())


def _call(monkeypatch, payload=None, *, bad_json=False):
    monkeypatch.setattr(
        httpx.Client,
        "get",
        lambda self, *a, **k: _FakeResponse(payload, bad_json=bad_json),
    )
    return osrm_client.get_route_geometry(_COORDS)


# --- valid payload control ------------------------------------------------


def test_valid_payload_is_returned_unchanged(monkeypatch):
    result = _call(monkeypatch, _valid_payload())

    assert result["distance_m"] == 1234.5
    assert result["duration_s"] == 210.0
    assert result["geometry"] == {
        "type": "LineString",
        "coordinates": [[85.30, 27.70], [85.31, 27.71]],
    }


def test_valid_payload_is_cached(monkeypatch):
    _call(monkeypatch, _valid_payload())
    assert len(osrm_client._route_cache) == 1


# --- malformed body / top level -------------------------------------------


def test_non_json_body_raises_osrm_error(monkeypatch):
    with pytest.raises(OSRMError):
        _call(monkeypatch, bad_json=True)


@pytest.mark.parametrize("payload", [[], ["Ok"], "Ok", 5, None, True])
def test_non_object_top_level_raises(monkeypatch, payload):
    with pytest.raises(OSRMError):
        _call(monkeypatch, payload)


@pytest.mark.parametrize("code", ["NoRoute", "NoSegment", "InvalidQuery", "", None, 0])
def test_non_ok_code_raises(monkeypatch, code):
    payload = _valid_payload()
    payload["code"] = code
    with pytest.raises(OSRMError):
        _call(monkeypatch, payload)


# --- routes list ----------------------------------------------------------


@pytest.mark.parametrize("routes", [None, {}, "not-a-list", 5, []])
def test_missing_or_malformed_routes_raises(monkeypatch, routes):
    payload = {"code": "Ok"}
    if routes is not None:
        payload["routes"] = routes
    with pytest.raises(OSRMError):
        _call(monkeypatch, payload)


@pytest.mark.parametrize("route", [None, 5, "route", [], ["nested"]])
def test_malformed_route_object_raises(monkeypatch, route):
    with pytest.raises(OSRMError):
        _call(monkeypatch, {"code": "Ok", "routes": [route]})


# --- geometry -------------------------------------------------------------


def test_missing_geometry_raises(monkeypatch):
    payload = _valid_payload()
    del payload["routes"][0]["geometry"]
    with pytest.raises(OSRMError):
        _call(monkeypatch, payload)


@pytest.mark.parametrize(
    "geometry",
    [
        "not-an-object",
        5,
        {"type": "Point", "coordinates": [85.30, 27.70]},
        {"type": "LineString"},  # no coordinates
        {"coordinates": [[85.30, 27.70]]},  # no type
        {"type": "LineString", "coordinates": "nope"},
        {"type": "LineString", "coordinates": 5},
    ],
)
def test_invalid_geometry_raises(monkeypatch, geometry):
    payload = _valid_payload()
    payload["routes"][0]["geometry"] = geometry
    with pytest.raises(OSRMError):
        _call(monkeypatch, payload)


# --- distance / duration --------------------------------------------------


def test_missing_distance_raises(monkeypatch):
    payload = _valid_payload()
    del payload["routes"][0]["distance"]
    with pytest.raises(OSRMError):
        _call(monkeypatch, payload)


def test_missing_duration_raises(monkeypatch):
    payload = _valid_payload()
    del payload["routes"][0]["duration"]
    with pytest.raises(OSRMError):
        _call(monkeypatch, payload)


@pytest.mark.parametrize(
    "bad",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        0,
        -1,
        -0.0,
        None,
        "1234.5",
        True,
        False,
    ],
)
def test_invalid_distance_raises(monkeypatch, bad):
    with pytest.raises(OSRMError):
        _call(monkeypatch, _valid_payload(distance=bad))


@pytest.mark.parametrize(
    "bad",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        0,
        -1,
        -0.0,
        None,
        "210",
        True,
        False,
    ],
)
def test_invalid_duration_raises(monkeypatch, bad):
    with pytest.raises(OSRMError):
        _call(monkeypatch, _valid_payload(duration=bad))


# --- operational guarantees ----------------------------------------------


def test_malformed_payload_is_not_cached(monkeypatch):
    with pytest.raises(OSRMError):
        _call(monkeypatch, _valid_payload(distance=-1))
    assert osrm_client._route_cache == {}


def test_error_message_leaks_neither_url_nor_body(monkeypatch):
    # A body value that must never surface in the error text.
    secret = "SUPER-SECRET-BODY-VALUE"
    with pytest.raises(OSRMError) as excinfo:
        _call(monkeypatch, _valid_payload(distance=secret))

    message = str(excinfo.value)
    assert secret not in message
    assert "http://" not in message and "https://" not in message
    assert "/route/" not in message

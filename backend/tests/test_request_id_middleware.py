"""Regression tests for the request-context middleware ordering (AUD-022).

X-Request-ID is meant to be present on *every* response, including ones
produced by middleware that can reject a request before it reaches a route
(TrustedHostMiddleware's 400, the custom CORS middleware's 403). That is only
true if the request-context middleware is registered so it is the outermost
user middleware; registering it first actually made it innermost, and these
tests pin the correct behaviour so a future reorder cannot regress it.

No database is needed: these hit /health and middleware-level rejections.
"""
from fastapi.testclient import TestClient

from app.core.request_context import REQUEST_ID_HEADER
from app.main import app

client = TestClient(app)


def test_request_id_on_normal_response():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get(REQUEST_ID_HEADER)


def test_request_id_on_trusted_host_rejection():
    """The AUD-022 case: a Host-header 400 is produced by middleware outside
    the request-context middleware unless it is registered outermost."""
    response = client.get("/health", headers={"host": "evil.example.com"})
    assert response.status_code == 400
    assert response.headers.get(REQUEST_ID_HEADER)


def test_request_id_on_cors_rejection():
    """A disallowed preflight is answered by the CORS middleware, which also
    sits outside the request-context middleware in the naive ordering."""
    response = client.options(
        "/health",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 403
    assert response.headers.get(REQUEST_ID_HEADER)


def test_inbound_request_id_is_echoed():
    response = client.get("/health", headers={REQUEST_ID_HEADER: "trace-abc-123"})
    assert response.status_code == 200
    assert response.headers.get(REQUEST_ID_HEADER) == "trace-abc-123"


def test_overlong_inbound_request_id_is_truncated_not_rejected():
    response = client.get("/health", headers={REQUEST_ID_HEADER: "x" * 100})
    assert response.status_code == 200
    request_id = response.headers.get(REQUEST_ID_HEADER)
    assert request_id is not None
    assert len(request_id) == 64

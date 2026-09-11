"""
tests/test_osrm_circuit_breaker.py

Unit tests for app/routing/osrm_client.py's CircuitBreaker and the
_should_retry decision logic. Both were previously untested despite being
the resilience mechanism that keeps routing working when OSRM misbehaves
-- is the retry/backoff logic sound, and does the circuit actually open,
recuperate, and close again? No live OSRM or DB required.

The production thresholds (_CIRCUIT_FAILURE_THRESHOLD=5,
_CIRCUIT_RECOVERY_TIME=30s, _CIRCUIT_HALF_OPEN_MAX=3) are monkeypatched
down per-test so state transitions are exercised without 5 failures or a
30-second wait. time.monotonic is stubbed to a mutable "clock" so the
OPEN -> HALF_OPEN recovery-time check is deterministic.
"""
import time
from unittest.mock import Mock

import pytest

from app.routing import osrm_client


@pytest.fixture(autouse=True)
def _small_thresholds(monkeypatch):
    monkeypatch.setattr(osrm_client, "_CIRCUIT_FAILURE_THRESHOLD", 3)
    monkeypatch.setattr(osrm_client, "_CIRCUIT_RECOVERY_TIME", 60.0)
    monkeypatch.setattr(osrm_client, "_CIRCUIT_HALF_OPEN_MAX", 2)


@pytest.fixture
def clock(monkeypatch):
    """A mutable monotonic clock; tests advance `now` to simulate time."""
    ns = {"now": 0.0}
    monkeypatch.setattr(osrm_client.time, "monotonic", lambda: ns["now"])
    return ns


def test_starts_closed_and_allows_execution():
    cb = osrm_client.CircuitBreaker()
    assert cb.can_execute("driving") is True
    assert cb._get("driving")["state"] == "CLOSED"


def test_success_resets_failure_count():
    cb = osrm_client.CircuitBreaker()
    cb.record_failure("foot")
    cb.record_success("foot")
    assert cb._get("foot")["failures"] == 0
    assert cb._get("foot")["state"] == "CLOSED"


def test_opens_after_threshold_failures(clock):
    cb = osrm_client.CircuitBreaker()
    for _ in range(osrm_client._CIRCUIT_FAILURE_THRESHOLD):
        cb.record_failure("driving")
    # OPEN fails fast -- no execution allowed.
    assert cb._get("driving")["state"] == "OPEN"
    assert cb.can_execute("driving") is False


def test_not_quite_threshold_stays_closed(clock):
    cb = osrm_client.CircuitBreaker()
    for _ in range(osrm_client._CIRCUIT_FAILURE_THRESHOLD - 1):
        cb.record_failure("driving")
    assert cb._get("driving")["state"] == "CLOSED"
    assert cb.can_execute("driving") is True


def test_profile_state_is_isolated(clock):
    """The circuit for one profile must not leak into another -- a dead
    driving profile shouldn't stop walking routes from working."""
    cb = osrm_client.CircuitBreaker()
    for _ in range(osrm_client._CIRCUIT_FAILURE_THRESHOLD):
        cb.record_failure("driving")
    assert cb.can_execute("driving") is False
    assert cb.can_execute("foot") is True  # untouched


def test_open_transitions_to_half_open_after_recovery_window(clock):
    cb = osrm_client.CircuitBreaker()
    for _ in range(osrm_client._CIRCUIT_FAILURE_THRESHOLD):
        cb.record_failure("driving")

    # Before recovery time elapses: still OPEN.
    clock["now"] += osrm_client._CIRCUIT_RECOVERY_TIME - 1
    assert cb.can_execute("driving") is False

    # After recovery time elapses: probe allowed, state flips to HALF_OPEN.
    clock["now"] += 2
    assert cb.can_execute("driving") is True
    assert cb._get("driving")["state"] == "HALF_OPEN"


def test_half_open_closes_after_required_successes(clock):
    cb = osrm_client.CircuitBreaker()
    for _ in range(osrm_client._CIRCUIT_FAILURE_THRESHOLD):
        cb.record_failure("driving")
    clock["now"] += osrm_client._CIRCUIT_RECOVERY_TIME + 1
    assert cb.can_execute("driving") is True  # -> HALF_OPEN

    # One success isn't enough to trust it again.
    cb.record_success("driving")
    assert cb._get("driving")["state"] == "HALF_OPEN"
    assert cb.can_execute("driving") is True  # HALF_OPEN still probes

    # Second success reaches the half-open max -> CLOSED.
    cb.record_success("driving")
    assert cb._get("driving")["state"] == "CLOSED"
    assert cb._get("driving")["failures"] == 0


def test_half_open_failure_reopens_immediately(clock):
    cb = osrm_client.CircuitBreaker()
    for _ in range(osrm_client._CIRCUIT_FAILURE_THRESHOLD):
        cb.record_failure("driving")
    clock["now"] += osrm_client._CIRCUIT_RECOVERY_TIME + 1
    assert cb.can_execute("driving") is True  # -> HALF_OPEN

    # A single failure while probing throws it straight back to OPEN,
    # requiring a fresh recovery window -- no endless half-open thrash.
    cb.record_failure("driving")
    assert cb._get("driving")["state"] == "OPEN"
    assert cb.can_execute("driving") is False
    # And it stays open until the recovery window has really elapsed.
    clock["now"] += osrm_client._CIRCUIT_RECOVERY_TIME - 1
    assert cb.can_execute("driving") is False


def test_record_failure_timestamps_last_failure(clock):
    cb = osrm_client.CircuitBreaker()
    clock["now"] = 42.0
    cb.record_failure("driving")
    assert cb._get("driving")["last_failure"] == 42.0


# --- _should_retry ---


def test_retries_connection_errors():
    assert osrm_client._should_retry(osrm_client.httpx.ConnectError("boom")) is True
    assert osrm_client._should_retry(osrm_client.httpx.ConnectTimeout("boom")) is True
    assert osrm_client._should_retry(osrm_client.httpx.ReadTimeout("boom")) is True


def test_retries_transient_server_errors():
    for status in (502, 503, 504):
        resp = Mock(status_code=status)
        assert osrm_client._should_retry(None, resp) is True, f"status {status} should retry"


def test_does_not_retry_client_errors():
    for status in (400, 401, 404, 422):
        resp = Mock(status_code=status)
        assert osrm_client._should_retry(None, resp) is False, f"status {status} must not retry"


def test_does_not_retry_non_retryable_exceptions(clock):
    # A 500 (not in the retry list) and a generic exception must not retry.
    resp = Mock(status_code=500)
    assert osrm_client._should_retry(None, resp) is False
    assert osrm_client._should_retry(Exception("boom")) is False
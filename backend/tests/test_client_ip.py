"""Regression tests for client-IP resolution (AUD-001).

The rate limiter and the admin audit trail both take "who is this
client?" from app/core/request_context.py::get_client_ip, so a client
that can influence that value can choose its own rate-limit bucket and
falsify audit rows. These tests pin the rule: a forwarded address is
used only when TRUST_PROXY_HEADERS is on AND the header holds exactly
one valid IP (the address the trusted edge overwrote it with); every
other shape falls back to the socket peer.

No database or running stack is needed -- the requests are built by
hand.
"""
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.core import request_context
from app.core.rate_limit import _get_client_ip
from app.core.request_context import get_client_ip

PEER = "203.0.113.9"


def _request(headers: dict[str, str] | None = None, client: tuple[str, int] | None = (PEER, 44321)) -> Request:
    raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/route-finder",
        "raw_path": b"/route-finder",
        "query_string": b"",
        "headers": raw_headers,
        "client": client,
        "server": ("testserver", 80),
    }
    return Request(scope)


def _set_trust(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    monkeypatch.setattr(
        request_context,
        "get_settings",
        lambda: SimpleNamespace(TRUST_PROXY_HEADERS=value),
    )


def test_headers_ignored_when_trust_disabled(monkeypatch):
    """Default (dev) posture: a forged header cannot move the bucket."""
    _set_trust(monkeypatch, False)
    request = _request({"X-Forwarded-For": "1.2.3.4"})
    assert get_client_ip(request) == PEER


def test_single_forwarded_ip_is_used_when_trusted(monkeypatch):
    _set_trust(monkeypatch, True)
    request = _request({"X-Forwarded-For": "198.51.100.7"})
    assert get_client_ip(request) == "198.51.100.7"


def test_forwarded_chain_is_refused_when_trusted(monkeypatch):
    """A comma-separated chain is never picked from, even the leftmost
    entry. With the edge overwriting the header this can only be a
    misconfiguration or an injection, and neither may choose the bucket."""
    _set_trust(monkeypatch, True)
    request = _request({"X-Forwarded-For": "1.2.3.4, 198.51.100.7"})
    assert get_client_ip(request) == PEER


def test_forged_leftmost_entry_is_not_selected(monkeypatch):
    """The precise AUD-001 attack: client prepends a fresh address to a
    legitimately-appended chain. The forged value must not be returned."""
    _set_trust(monkeypatch, True)
    request = _request({"X-Forwarded-For": "9.9.9.9, 198.51.100.7"})
    assert get_client_ip(request) == PEER


def test_garbage_forwarded_value_is_refused(monkeypatch):
    _set_trust(monkeypatch, True)
    request = _request({"X-Forwarded-For": "not-an-ip"})
    assert get_client_ip(request) == PEER


def test_x_real_ip_backs_up_a_chained_xff(monkeypatch):
    """nginx sets X-Real-IP independently, so a chained XFF still resolves
    to the true client via the single-value header."""
    _set_trust(monkeypatch, True)
    request = _request({"X-Forwarded-For": "1.2.3.4, 5.6.7.8", "X-Real-IP": "198.51.100.7"})
    assert get_client_ip(request) == "198.51.100.7"


def test_ipv6_is_accepted(monkeypatch):
    _set_trust(monkeypatch, True)
    request = _request({"X-Forwarded-For": "2001:db8::1"})
    assert get_client_ip(request) == "2001:db8::1"


def test_no_headers_and_no_peer_is_unknown(monkeypatch):
    _set_trust(monkeypatch, False)
    request = _request(client=None)
    assert get_client_ip(request) == "unknown"


def test_rate_limit_key_function_uses_the_same_resolution(monkeypatch):
    """The limiter must key on the same value the audit trail records --
    otherwise a client could be limited under one identity and audited
    under another."""
    _set_trust(monkeypatch, True)
    request = _request({"X-Forwarded-For": "1.2.3.4, 198.51.100.7"})
    assert _get_client_ip(request) == PEER

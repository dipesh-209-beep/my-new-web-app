"""Per-request correlation id and client-IP resolution.

Two small pieces of plumbing that the audit trail and the rate limiter
both need, kept together so there's exactly one answer to "what IP was
this request from" in the whole codebase.

request_id
    Generated once per request (or taken from an inbound X-Request-ID,
    bounded and sanitised first), exposed on request.state, echoed back
    in the X-Request-ID response header, and copied into every
    admin_audit_log row for the request. This is what lets an operator
    go from "someone says a route status changed at 14:32" to the
    exact set of log lines and audit rows for that request.

client_ip
    request.client.host -- the socket peer -- *unless*
    TRUST_PROXY_HEADERS is on, in which case X-Forwarded-For is
    consulted first. The default is not to trust it: the development
    stack publishes the backend's port directly, so a forged
    X-Forwarded-For there would hand every request a fresh rate-limit
    bucket. Production sets TRUST_PROXY_HEADERS together with uvicorn's
    --proxy-headers/--forwarded-allow-ips, so the peer address has
    already been rewritten to the real client by the time it gets
    here.
"""

import uuid

from fastapi import Request

from .config import get_settings

REQUEST_ID_HEADER = "X-Request-ID"
# Inbound ids longer than this are truncated rather than rejected: this
# is a correlation label, not a security control, and an over-long one
# shouldn't be able to fail a request.
_MAX_INBOUND_REQUEST_ID = 64


def _sanitise_inbound_request_id(raw: str | None) -> str | None:
    """Accept an inbound X-Request-ID only if it looks like a label.

    Anything with control characters, whitespace, or non-printable
    bytes is dropped in favour of a fresh id -- otherwise a client
    could inject arbitrary text into the audit table and into whatever
    logs echo this value back.
    """
    if not raw:
        return None
    cleaned = raw.strip()[:_MAX_INBOUND_REQUEST_ID]
    if not cleaned:
        return None
    if not all(32 <= ord(char) < 127 for char in cleaned):
        return None
    return cleaned


def get_client_ip(request: Request) -> str:
    """Best available client address for rate limiting and audit rows."""
    if get_settings().TRUST_PROXY_HEADERS:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            # Leftmost entry is the original client; everything after
            # it was appended by intermediate proxies.
            first = forwarded_for.split(",")[0].strip()
            if first:
                return first
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


def get_request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


async def request_context_middleware(request: Request, call_next):
    """Attach request.state.request_id / .client_ip and echo the id back."""
    request_id = _sanitise_inbound_request_id(request.headers.get(REQUEST_ID_HEADER)) or uuid.uuid4().hex
    request.state.request_id = request_id
    request.state.client_ip = get_client_ip(request)

    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response

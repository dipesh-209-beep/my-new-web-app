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
    TRUST_PROXY_HEADERS is on and the request carries a single,
    well-formed forwarded address, in which case that address is used.
    The default is not to trust the headers: the development stack
    publishes the backend's port directly, so a forged X-Forwarded-For
    there would hand every request a fresh rate-limit bucket.

    When TRUST_PROXY_HEADERS is on (production), the trusted edge is
    nginx and it OVERWRITES both X-Forwarded-For and X-Real-IP with
    $remote_addr (see deploy/proxy_params.conf), so a well-formed header
    holds exactly one value: the address nginx accepted the connection
    from. This function therefore accepts a forwarded value only when it
    is a single valid IP address; anything else -- a comma-separated
    chain, junk, a hostname -- is refused and the socket peer is used
    instead. That refusal is what makes a client-injected X-Forwarded-For
    useless even if the edge is ever misconfigured to append rather than
    overwrite: the attacker's value would arrive as part of a chain and
    be discarded, not selected.
"""

import ipaddress
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


def _single_forwarded_ip(raw: str | None) -> str | None:
    """The one client address in a forwarded header, or None.

    Returns a value only when the header holds exactly one entry and that
    entry parses as an IP address. A chain (more than one comma-separated
    entry) is deliberately refused rather than picked from: with the
    trusted edge overwriting the header (deploy/proxy_params.conf) a
    chain can only mean the edge is misconfigured or the value was
    injected, and neither case should influence the rate-limit bucket.
    """
    if not raw:
        return None
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    if len(entries) != 1:
        return None
    try:
        return str(ipaddress.ip_address(entries[0]))
    except ValueError:
        return None


def get_client_ip(request: Request) -> str:
    """Best available client address for rate limiting and audit rows."""
    if get_settings().TRUST_PROXY_HEADERS:
        # X-Forwarded-For first; X-Real-IP is an independent single-value
        # header nginx (and most proxies) set, so it still yields the true
        # client if the XFF copy is ever a chain.
        for header in ("x-forwarded-for", "x-real-ip"):
            client = _single_forwarded_ip(request.headers.get(header))
            if client is not None:
                return client
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

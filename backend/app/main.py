import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import admin, admin_auth, auth, congestion, fare, routes, routing, service_credentials, stops, suggestions
from app.core.config import get_settings, validate_production_settings
from app.core.rate_limit import limiter
from app.core.request_context import request_context_middleware
from app.db.session import SessionLocal
from app.routing import graph_builder

logger = logging.getLogger("uvicorn.error")

# Fail before the app object is even constructed, so an unsafe
# production configuration is a hard startup error rather than
# something a request has to trip over later. A no-op unless
# ENVIRONMENT=production -- see validate_production_settings.
validate_production_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info(
        "Starting API: environment=%s, trusted_hosts=%s, rate_limit_store=%s",
        settings.ENVIRONMENT,
        ",".join(settings.trusted_hosts_list),
        settings.rate_limit_redis_url or "in-memory (per-worker)",
    )
    if settings.is_production and settings.ALLOW_LEGACY_SHARED_ADMIN_KEY:  # pragma: no cover
        # Unreachable: validate_production_settings() refuses to start
        # with this on. Kept as a belt-and-braces assertion in case
        # that check is ever weakened.
        raise RuntimeError("Legacy shared admin key must never be enabled in production.")

    # Warm the graph cache at startup instead of on the first request.
    db = SessionLocal()
    try:
        graph = graph_builder.get_cached_graph(db)
        logger.info(
            "Routing graph built: %d stops, %d edges",
            graph.number_of_nodes(),
            graph.number_of_edges(),
        )
    except Exception:
        logger.exception("Failed to build routing graph at startup")
    finally:
        db.close()

    yield

app = FastAPI(
    title="Kathmandu Bus Route Finder API",
    description="Origin-destination bus route search for the Kathmandu Valley.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if get_settings().is_production else "/docs",
    redoc_url=None if get_settings().is_production else "/redoc",
    openapi_url=None if get_settings().is_production else "/openapi.json",
)

# Rate limiting: applied to /admin/login (app/api/admin_auth.py) and
# /auth/register + /auth/login (app/api/auth.py) -- these are the
# intentionally unprotected endpoints that would otherwise be open doors
# for password brute-forcing / account creation. Keyed by client IP,
# counted in Redis when one is configured so the limit is global across
# workers and replicas rather than per-process; see app/core/rate_limit.py
# for the storage decision and app/core/config.py for the production
# guard that makes a missing shared store a startup error.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# X-Request-ID generation/echo and single-source client-IP resolution.
# Registered first so it wraps everything below it, including CORS and
# the error handlers, so even a rejected request has an id.
app.middleware("http")(request_context_middleware)

# Host-header validation, always on. Starlette's TrustedHostMiddleware
# rejects any request whose Host header isn't in the configured list,
# which is the direct mitigation for the request.url reconstruction
# class of advisories (PYSEC-2026-161, PYSEC-2026-248) and for cache
# poisoning attempts against any host-derived response. The default
# list covers local development; production must set TRUSTED_HOSTS
# explicitly and validate_production_settings() refuses the dev
# defaults.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=get_settings().trusted_hosts_list)


# ---------------------------------------------------------------------------
# CORS middleware
#
# Dynamic origin reflection: the incoming Origin header is compared against
# the configured CORS_ORIGINS list (exact match) AND, *in development
# only*, a localhost/LAN pattern. The LAN pattern exists so a device on
# the same network can reach the API during development without
# hard-coding every possible address; it is disabled entirely when
# ENVIRONMENT=production, where CORS_ORIGINS must list the exact
# frontend origin(s) (validate_production_settings rejects '*' and
# http:// origins there).
# ---------------------------------------------------------------------------
_CORS_ALLOW_METHODS = "GET, POST, PATCH, DELETE, OPTIONS"
_CORS_ALLOW_HEADERS = "Content-Type, Authorization, X-Admin-Api-Key"

# Patterns accepted in addition to explicit CORS_ORIGINS, but only in
# development. Covers localhost, 127.0.0.1, and any RFC 1918 /
# link-local LAN IP accessed over HTTP (the common dev scenario).
_LAN_ORIGIN_RE = re.compile(
    r"^https?://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+|169\.254\.\d+\.\d+)(:\d+)?$"
)


@app.middleware("http")
async def cors_middleware(request: Request, call_next):
    # Only process CORS on actual requests (not OpenAPI schema etc.)
    origin = request.headers.get("origin")
    if request.method == "OPTIONS" and origin:
        # Preflight: determine if the origin is allowed
        allowed_origin = _resolve_cors_origin(origin)
        if allowed_origin is None:
            return Response(status_code=403, content=b'{"detail":"Origin not allowed"}', media_type="application/json")
        response = Response(status_code=204)
        response.headers["Access-Control-Allow-Origin"] = allowed_origin
        response.headers["Access-Control-Allow-Methods"] = _CORS_ALLOW_METHODS
        response.headers["Access-Control-Allow-Headers"] = _CORS_ALLOW_HEADERS
        response.headers["Access-Control-Max-Age"] = "86400"
        response.headers["Vary"] = "Origin"
        return response

    response = await call_next(request)

    if origin:
        allowed_origin = _resolve_cors_origin(origin)
        if allowed_origin:
            response.headers["Access-Control-Allow-Origin"] = allowed_origin
            response.headers["Vary"] = "Origin"

    return response


def _resolve_cors_origin(origin: str) -> str | None:
    """Return the origin string to echo back in Access-Control-Allow-Origin,
    or None if the origin is not allowed."""
    settings = get_settings()
    # Exact match against configured origins
    if origin in settings.cors_origins_list:
        return origin
    # LAN / localhost pattern match, development only. In production a
    # private-network origin on CORS_ORIGINS would have been rejected at
    # startup by validate_production_settings, and this branch is the
    # belt-and-braces half: even a mis-set CORS_ORIGINS can't get a
    # wildcard-ish LAN pattern accepted here.
    if not settings.is_production and _LAN_ORIGIN_RE.match(origin):
        return origin
    return None


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Conservative response headers on every API response.

    These matter at the API boundary because the API's own responses
    are JSON documents a browser will parse on behalf of the frontend
    origin, and because a plain-HTTP deployment should not be silently
    cacheable or sniffable. They are deliberately NOT a substitute for
    the real ones: a browser only honours HSTS/CSP/Permissions-Policy
    when they arrive from the HTTPS origin itself, which in a real
    deployment is nginx (deploy/nginx.prod.conf) -- the app is behind
    it, not in front of TLS. No-store on API responses is the notable
    one here: it stops a shared cache or the browser's bfcache from
    holding on to a personalized or admin response.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    # The API serves JSON and no user content of its own, so it does not
    # need to load or execute anything; a strict default-src costs
    # nothing here and limits the blast radius if a response is ever
    # rendered in a browser context.
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    )
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Last-resort handler: log the detail server-side, return nothing
    useful to the client.

    Without this, a Starlette ServerErrorMiddleware re-raises into the
    test client / ASGI server, and depending on deployment the traceback
    can reach the client as a 500 body. That body can contain SQL text
    (constraint names, column values from a failed statement), internal
    filesystem paths (template/config file locations), or connection
    details -- all of which belong in the server log and none of which
    belong in an HTTP response.

    The handler itself deliberately does not echo `str(exc)`, because
    for a SQLAlchemy error that string *is* the SQL. It returns a
    generic message plus the X-Request-ID already set by
    request_context_middleware, so a user reporting a problem can hand
    over an id that maps to a full traceback in the server log.
    """
    logger.error(
        "Unhandled error on %s %s (request_id=%s): %s",
        request.method,
        request.url.path,
        getattr(request.state, "request_id", None),
        exc,
        exc_info=True,
    )
    return Response(
        status_code=500,
        content=b'{"detail":"Internal server error."}',
        media_type="application/json",
    )


@app.get("/health")
def health_check():
    return {"status": "ok"}


# Graph-rebuild is exposed once, as POST /graph/reload (app/api/admin.py) --
# this used to be duplicated here as /admin/rebuild-graph, doing the exact
# same get_cached_graph(refresh=True) call. Removed rather than kept as an
# alias: two endpoints for one action is one more thing to keep in sync and
# one more place require_admin's auth logic has to be gotten right.
app.include_router(stops.router)
app.include_router(routes.router)
app.include_router(routing.router)
app.include_router(fare.router)
app.include_router(congestion.router)
app.include_router(admin.router)
app.include_router(admin_auth.router)
app.include_router(auth.router)
app.include_router(suggestions.router)
app.include_router(service_credentials.router)

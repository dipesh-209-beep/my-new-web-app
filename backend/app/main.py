import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api import admin, admin_auth, congestion, fare, routes, routing, stops
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.db.session import SessionLocal
from app.routing import graph_builder

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
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
)

# Rate limiting: currently only applied to /admin/login (see
# app/api/admin_auth.py) -- that endpoint is intentionally unprotected by
# require_admin, which makes it the one open door for password
# brute-forcing. Keyed by client IP; fine for a small internal tool,
# revisit if this ever sits behind a proxy that doesn't forward the real
# client IP, or runs with multiple worker processes (the in-memory
# storage backend below doesn't share state across workers).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ---------------------------------------------------------------------------
# CORS middleware
#
# Dynamic origin reflection: the incoming Origin header is compared against
# the configured CORS_ORIGINS list (exact match) AND a localhost/LAN
# pattern.  For development, this allows any device on the same network to
# access the API without hard-coding every possible LAN IP.  For production,
# set CORS_ORIGINS to the exact domain(s) and remove the localhost patterns
# from _LAN_ORIGIN_RE.
# ---------------------------------------------------------------------------
_CORS_ALLOW_METHODS = "GET, POST, PATCH, DELETE, OPTIONS"
_CORS_ALLOW_HEADERS = "Content-Type, Authorization, X-Admin-Api-Key"

# Patterns that are always accepted in addition to explicit CORS_ORIGINS.
# Covers localhost, 127.0.0.1, and any RFC 1918 / link-local LAN IP when
# accessed over HTTP (the common dev scenario).
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
            return Response(status_code=403, detail="Origin not allowed")
        response = Response(status_code=204)
        response.headers["Access-Control-Allow-Origin"] = allowed_origin
        response.headers["Access-Control-Allow-Methods"] = _CORS_ALLOW_METHODS
        response.headers["Access-Control-Allow-Headers"] = _CORS_ALLOW_HEADERS
        response.headers["Access-Control-Max-Age"] = "86400"
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
    # LAN / localhost pattern match for development
    if _LAN_ORIGIN_RE.match(origin):
        return origin
    return None

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
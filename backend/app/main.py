import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import admin, admin_auth, auth, congestion, fare, routes, routing, service_credentials, stops, suggestions
from app.core.config import get_settings, validate_production_settings
from app.core.rate_limit import limiter
from app.core.redis_client import get_redis
from app.core.request_context import request_context_middleware
from app.db.session import SessionLocal, engine
from app.routing import congestion_zones, graph_builder

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

    # Load the geographic congestion zones at startup too, for the same
    # reason as the graph: it is a file read that should either work or be
    # reported now, not silently degrade on a user's first search.
    #
    # A missing file is a warning, not a startup failure -- the organic
    # segment_congestion_stats signal still applies, and refusing to serve
    # routes because one static CSV is absent would be a worse outcome
    # than degraded congestion weighting. But it must never be silent:
    # this exact degradation (the file simply not being in the image) went
    # unnoticed for the lifetime of the containerised setup, because
    # load_zones() returned an empty list and every ratio came back 1.0.
    # The positive log line matters just as much -- "0 zones" is
    # indistinguishable from "file loaded but empty" unless stated.
    zones = congestion_zones.load_zones()
    logger.info("Congestion zones loaded: %d", len(zones))

    # try/finally around the yield so the shutdown path actually runs.
    # The startup `db.close()` above returns a single session to the pool;
    # it does not dispose the pool. Without this, the pool is only reclaimed
    # when the OS reclaims the process's sockets -- which happens to be
    # equivalent for a container that is exiting, and is not equivalent for
    # an in-process lifespan restart (tests, or a future embedder).
    try:
        yield
    finally:
        engine.dispose()
        logger.info("Database connection pool disposed")

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
    """Liveness only: the process is up and serving.

    Deliberately touches no dependency. A liveness probe that reached for
    Postgres would report the *database* as unhealthy, and an orchestrator
    acting on that would restart this container -- which cannot fix a
    database that is down, and would turn one outage into a restart loop
    across every replica. Dependency state belongs in /health/ready.
    """
    return {"status": "ok"}


def _run_with_deadline(fn, timeout: float):
    """Run fn() in a worker thread and return its result within `timeout`.

    Returns the elapsed milliseconds on success, raises TimeoutError if the
    deadline passes, and re-raises whatever fn raised otherwise. See
    readiness_check for why the deadline lives here and not in SQL.
    """
    box: dict = {}

    def target() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - forwarded to the caller
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True, name="readiness-probe")
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"probe exceeded {timeout}s")
    if "error" in box:
        raise box["error"]
    return box["result"]


def _timed_redis_ping(client) -> int:
    """PING the shared Redis client and return the elapsed milliseconds.

    The timeout is applied by the caller's thread deadline, NOT by
    per-call kwargs. An earlier version of this passed
    socket_connect_timeout=2, socket_timeout=2 to ping() on the theory
    that redis-py honours them per call. It does not, on two counts
    (verified against redis-py 8.1.0):

      * get_connection() takes no options at all -- it is decorated
        @deprecated_args(args_to_warn=["*"]) -- and the RESP parser reads
        connection.socket_timeout, an attribute fixed when the connection
        object is built. Against a blackholed server, ping() with those
        kwargs blocked for 5.01s, which is AbstractConnection's own
        socket_timeout=5 default, not 2.

      * Worse, the kwargs are forwarded to the PING response callback,
        which is <lambda>(r) and accepts no keyword arguments. So a
        *healthy* Redis raised TypeError and readiness reported 503 --
        a false "unhealthy" on a perfectly good deployment.

    Passing no kwargs is deliberate: the thread deadline in
    _run_with_deadline is the only bound that is actually load-bearing,
    and it is the one that works.
    """
    started = time.monotonic()
    client.ping()
    return round((time.monotonic() - started) * 1000)


@app.get("/health/ready")
def readiness_check():
    """Readiness: can this process actually serve a request right now?

    Every endpoint that matters reads Postgres, so a DB that is unreachable
    means the process is up but useless and should report 503. Redis is
    treated differently, and the distinction is the whole point of this
    handler:

      * Configured and unreachable -> 503. In production a missing shared
        rate-limit store is refused at startup precisely because the
        per-worker fallback multiplies an attacker's login guesses by the
        worker count (see validate_production_settings and
        app/core/rate_limit.py). A configured-but-unreachable Redis is
        that same degradation appearing at runtime, so it is not cosmetic.

      * Not configured (get_redis() is None) -> reported, not failed.
        Development legitimately runs without it, and marking a working
        dev backend permanently unready would make the status lie.

    OSRM is deliberately not checked. It is unconditionally optional --
    the backend returns road_geometry: null without it -- so failing
    readiness on it would take the whole API down over a missing optional
    geometry service.

    Each probe is bounded at ~2s so a hung dependency cannot make the
    readiness check itself hang. The bound is applied with a thread and an
    explicit deadline rather than a SQL statement_timeout, because the
    failure mode that matters most here is not a slow query -- it is an
    *exhausted pool*, where the request spends its whole time waiting on
    pool checkout (app/db/session.py sets pool_timeout=10). A server-side
    timeout does not bound that; it fires after the connection is acquired.
    Without the deadline this endpoint blocks for 10s under exactly the
    incident it exists to report.

    A probe that loses the race is abandoned, not cancelled. That is
    deliberate: the thread is blocked in libpq/redis-py and cannot be
    interrupted safely, and it will finish on its own and return its
    connection to the pool. Readiness is a diagnostic surface polled
    infrequently, so a transient leaked thread is a better trade than a
    probe that can hang.

    This is a diagnostic and orchestration surface; it is not wired to the
    Compose healthcheck, which stays on /health (see docker-compose.yml),
    because the only thing that healthcheck gates is the proxy's startup
    ordering.
    """
    checks: dict[str, dict] = {}

    def _probe(name: str, fn) -> None:
        """Run one bounded probe and record the outcome. Thread-safe: the
        per-probe dicts are written before the future is read, so a
        completed probe's result is visible to get() once it returns."""
        try:
            result = _run_with_deadline(fn, timeout=2.0)
        except TimeoutError:
            checks[name] = {"ok": False, "error": "timeout"}
            logger.warning("Readiness check: %s probe exceeded 2s", name)
        except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
            checks[name] = {"ok": False, "error": type(exc).__name__}
            logger.warning("Readiness check: %s not ready: %s", name, exc)
        else:
            checks[name] = {"ok": True, "ms": result}

    def _db_probe() -> int:
        started = time.monotonic()
        # Its own connection, NOT the request-scoped `db` session. The
        # deadline can abandon this thread while it is still blocked, and
        # a request Session is owned by get_db's `finally: db.close()` --
        # sharing one makes the two race, which SQLAlchemy rejects with
        # IllegalStateChangeError. A dedicated connection is abandoned
        # safely: the abandoned thread closes it when it unblocks, and
        # closing returns it to the pool.
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return round((time.monotonic() - started) * 1000)

    client = get_redis()

    # Probes run concurrently, not in sequence. Each has its own 2s
    # deadline; running them one after another made the total worst case
    # 2s + 2s = 4.01s when both dependencies hung, which is long enough
    # for a load balancer to have already given up on the check.
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="readiness") as pool:
        futures = {"db": pool.submit(_probe, "db", _db_probe)}
        if client is None:
            checks["redis"] = {"ok": None, "skipped": "not configured"}
        else:
            futures["redis"] = pool.submit(
                _probe, "redis", lambda: _timed_redis_ping(client)
            )
        # Context manager __exit__ waits for both, but each is already
        # bounded by its own deadline, so this cannot hang the response.
        for future in futures.values():
            future.result()

    ready = all(c["ok"] is not False for c in checks.values())
    payload = {"status": "ok" if ready else "unhealthy", "checks": checks}
    if ready:
        return payload
    # 503, not 500: this is a dependency being unavailable, which is
    # exactly what a load balancer or orchestrator watches for.
    return JSONResponse(status_code=503, content=payload)


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

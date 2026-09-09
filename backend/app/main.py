import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins_list,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Admin-Api-Key"],
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
"""
db/session.py

Engine + session factory for the app. Everything that talks to Postgres
(queries.py, API dependency injection, routing graph loader) should get
its Session from `get_db`, not create engines/sessions of its own.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# Pool sizing. The arithmetic that matters, because it is the thing that
# gets blindly multiplied later:
#
#     workers x (pool_size + max_overflow) <= Postgres max_connections
#
# Note that create_engine() itself has a max_overflow default of 10, so
# the effective ceiling is pool_size + 10, NOT pool_size. With the values
# below that is 20 connections per process. As deployed this is a single
# uvicorn worker (no --workers flag anywhere), so the real cost is 20 of
# the 100 max_connections the DB allows -- comfortable. If workers are
# ever added for CPU reasons, four workers would want 80 of 100, which
# leaves almost no room for psql, a migration, or the pg_bouncer that
# would be the better answer at that point. Check this comment before
# adding --workers; do not raise pool_size to compensate.
#
# pool_recycle is the highest-value setting here: Postgres (and managed
# Postgres in particular) drops idle connections, and recycling retires
# them before that happens. It composes with -- does not replace --
# pool_pre_ping below, which is what makes a dropped connection recoverable
# at request time.
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,  # avoids stale-connection errors after DB restarts
    pool_size=10,
    max_overflow=10,  # per-process ceiling: 20
    pool_timeout=10,  # fail fast instead of queueing behind an exhausted pool
    pool_recycle=1800,  # retire connections older than 30 min
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    future=True,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a Session, always closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
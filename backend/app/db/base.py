"""Single import point that pulls in `Base` plus the core ORM models.

Alembic's `migrations/env.py` imports `Base` for autogenerate support
(`alembic revision --autogenerate`). That only picks up models that
have actually been imported somewhere by the time `Base.metadata` is
read, so this module exists purely for its import side-effects --
importing it registers Stop/Route/RouteStop on `Base.metadata`.

Note: `migrations/env.py` also imports `Base` from `app.models` (which
imports ALL models) so autogenerate sees the full schema regardless of
which models are imported here.  Keep this file in sync if new core
models are added that Alembic should track.
"""

from app.models import Base, Route, RouteStop, Stop  # noqa: F401

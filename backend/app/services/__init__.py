"""Service layer for the Kathmandu Bus Route Finder backend.

Anything that coordinates across multiple tables/units of work (rather
than a single DB read/write) but isn't a pure API concern lives here --
currently just suggestion application (app/services/suggestions.py)."""
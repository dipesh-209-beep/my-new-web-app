"""Generates new primary-key IDs for stops/routes created via the admin
API. Existing IDs come from an external pipeline (stops: S#### convention;
routes: OSM-derived R-numbers, not sequential/owned by this app) -- see
migrations/versions/0002_replace_with_full_schema.py.

Stops: next sequential number in the existing S#### convention.
Routes: a separate M###### prefix, reserved for admin-created routes only,
so a manually created route can never collide with a future OSM-sourced
R-numbered import.

Uses PostgreSQL sequences for race-safe generation.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session


def next_stop_id(db: Session) -> str:
    next_n = db.execute(text("SELECT nextval('stop_id_seq')")).scalar_one()
    return f"S{next_n:04d}"


def next_route_id(db: Session) -> str:
    next_n = db.execute(text("SELECT nextval('route_id_seq')")).scalar_one()
    return f"M{next_n:06d}"

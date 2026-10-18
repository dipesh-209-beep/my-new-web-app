"""Initial schema: stops, routes, route_stops

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-30

"""

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geometry


# revision identifiers, used by Alembic.
revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:

    # PostGIS must exist before the first Geometry column and its GiST
    # index below. Historically this ran only in 0002, so the chain applied
    # on the postgis/postgis image (which pre-enables the extension in
    # template1) and in CI, but failed at this migration on any other
    # PostgreSQL where PostGIS is installed but not yet created. Idempotent,
    # so databases that already have it -- including via 0002 -- are
    # unaffected.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")


    # -------------------------
    # Stops table
    # -------------------------
    op.create_table(
        "stops",
        sa.Column(
            "stop_id",
            sa.Integer,
            primary_key=True,
            autoincrement=True
        ),

        sa.Column(
            "name",
            sa.String(150),
            nullable=False
        ),

        sa.Column(
            "name_normalized",
            sa.String(150),
            nullable=False
        ),

        sa.Column(
            "is_interchange",
            sa.Boolean,
            nullable=False,
            server_default=sa.false()
        ),

        sa.Column(
            "geom",
            Geometry(
                geometry_type="POINT",
                srid=4326,
                spatial_index=False
            ),
            nullable=False
        ),

        sa.Column(
            "verified",
            sa.Boolean,
            nullable=False,
            server_default=sa.false()
        ),

        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()")
        ),

        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()")
        ),
    )


    # Spatial index for nearby stop search
    op.create_index(
        "idx_stops_geom",
        "stops",
        ["geom"],
        postgresql_using="gist"
    )

    op.create_index(
        "idx_stops_name_normalized",
        "stops",
        ["name_normalized"]
    )


    # -------------------------
    # Routes table
    # -------------------------
    op.create_table(
        "routes",

        sa.Column(
            "route_id",
            sa.Integer,
            primary_key=True,
            autoincrement=True
        ),

        sa.Column(
            "route_number",
            sa.String(20),
            nullable=False
        ),

        sa.Column(
            "route_name",
            sa.String(150),
            nullable=False
        ),

        sa.Column(
            "operator",
            sa.String(100),
            nullable=True
        ),

        sa.Column(
            "tier",
            sa.SmallInteger,
            nullable=True
        ),

        sa.Column(
            "verified",
            sa.Boolean,
            nullable=False,
            server_default=sa.false()
        ),

        sa.Column(
            "source",
            sa.String(50),
            nullable=True
        ),

        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()")
        ),

        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()")
        ),

        sa.CheckConstraint(
            "tier IN (1,2,3)",
            name="ck_routes_tier"
        ),
    )


    op.create_index(
        "idx_routes_tier",
        "routes",
        ["tier"]
    )

    op.create_index(
        "idx_routes_verified",
        "routes",
        ["verified"]
    )


    # -------------------------
    # Route stops mapping table
    # -------------------------
    op.create_table(
        "route_stops",

        sa.Column(
            "route_id",
            sa.Integer,
            sa.ForeignKey(
                "routes.route_id",
                ondelete="CASCADE"
            ),
            nullable=False
        ),

        sa.Column(
            "stop_id",
            sa.Integer,
            sa.ForeignKey(
                "stops.stop_id",
                ondelete="CASCADE"
            ),
            nullable=False
        ),

        sa.Column(
            "sequence_order",
            sa.Integer,
            nullable=False
        ),

        sa.PrimaryKeyConstraint(
            "route_id",
            "sequence_order"
        ),
    )


    op.create_index(
        "idx_route_stops_route_id",
        "route_stops",
        ["route_id"]
    )

    op.create_index(
        "idx_route_stops_stop_id",
        "route_stops",
        ["stop_id"]
    )

    op.create_index(
        "uq_route_stop_position",
        "route_stops",
        [
            "route_id",
            "stop_id",
            "sequence_order"
        ],
        unique=True
    )


def downgrade() -> None:

    op.drop_index(
        "uq_route_stop_position",
        table_name="route_stops"
    )

    op.drop_index(
        "idx_route_stops_stop_id",
        table_name="route_stops"
    )

    op.drop_index(
        "idx_route_stops_route_id",
        table_name="route_stops"
    )

    op.drop_table("route_stops")


    op.drop_index(
        "idx_routes_verified",
        table_name="routes"
    )

    op.drop_index(
        "idx_routes_tier",
        table_name="routes"
    )

    op.drop_table("routes")


    op.drop_index(
        "idx_stops_name_normalized",
        table_name="stops"
    )

    op.drop_index(
        "idx_stops_geom",
        table_name="stops"
    )

    op.drop_table("stops")

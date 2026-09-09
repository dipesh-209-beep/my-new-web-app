"""init sequences from existing data

Revision ID: a1b2c3d4e5f6
Revises: 2667dc7a2839
Create Date: 2026-09-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '2667dc7a2839'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Initialize stop_id_seq to max numeric part of existing stop IDs + 1
    # to prevent collisions with imported data (e.g. S0198 or S_391 ->
    # next val = max+1). The trailing-digit regex handles both the S####
    # and S_#### forms that exist in the imported data; rows with no
    # trailing digits just contribute NULL (ignored by MAX/COALESCE).
    op.execute("""
        SELECT setval('stop_id_seq',
            COALESCE(
                (SELECT MAX(CAST(SUBSTRING(stop_id FROM '([0-9]+)$') AS int)) + 1 FROM stops),
                1
            )
        )
    """)

    # Initialize route_id_seq similarly for R-prefixed imported routes.
    op.execute("""
        SELECT setval('route_id_seq',
            COALESCE(
                (SELECT MAX(CAST(SUBSTRING(route_id FROM '([0-9]+)$') AS int)) + 1 FROM routes),
                1
            )
        )
    """)


def downgrade() -> None:
    # Reset sequences to default starting value (1).  This loses the
    # "next after existing data" guarantee but is reversible.
    op.execute("SELECT setval('stop_id_seq', 1, false)")
    op.execute("SELECT setval('route_id_seq', 1, false)")

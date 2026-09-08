"""add_id_sequences

Revision ID: 2667dc7a2839
Revises: 9486a80c9dd1
Create Date: 2026-09-08 12:58:40.763546

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2667dc7a2839'
down_revision: Union[str, None] = '9486a80c9dd1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE stop_id_seq")
    op.execute("CREATE SEQUENCE route_id_seq")


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS stop_id_seq")
    op.execute("DROP SEQUENCE IF EXISTS route_id_seq")

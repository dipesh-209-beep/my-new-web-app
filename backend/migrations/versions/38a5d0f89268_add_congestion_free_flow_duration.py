"""add free_flow_duration_s to segment_congestion_stats

The free-flow baseline was previously computed on every read as
min(avg_duration_s) across a segment's own 8 buckets -- self-defeating
for a segment that's never actually free-flowing in any bucket, since the
ratio then compresses toward 1.0 exactly where "heavy" should be easiest
to trigger. This anchors it instead to the OSRM duration recorded once at
seed time (a genuine free-driving estimate), stored per segment rather
than recomputed from potentially-congested historical data.

Nullable + backfilled from each seeded row's avg_duration_s -- which for
seeded rows *is* the original OSRM free-driving estimate recorded at seed
time, not an average of potentially-congested buckets -- so rows seeded
before this migration (or never re-seeded) still resolve via the
COALESCE fallback in queries.get_congestion_stats, rather than needing
every row rewritten immediately.

Revision ID: 38a5d0f89268
Revises: c1a2b3d4e5f6
Create Date: 2026-08-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '38a5d0f89268'
down_revision: Union[str, None] = 'c1a2b3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'segment_congestion_stats',
        sa.Column('free_flow_duration_s', sa.Float(), nullable=True),
    )
    # Backfill from every row currently seeded (is_seeded=True) --  their
    # avg_duration_s *is* the original OSRM free-driving estimate for
    # that segment, un-averaged, exactly what this column is meant to
    # anchor to.
    op.execute(
        """
        UPDATE segment_congestion_stats
        SET free_flow_duration_s = avg_duration_s
        WHERE is_seeded = true
        """
    )


def downgrade() -> None:
    op.drop_column('segment_congestion_stats', 'free_flow_duration_s')

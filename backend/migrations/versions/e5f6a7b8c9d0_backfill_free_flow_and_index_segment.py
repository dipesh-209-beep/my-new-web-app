"""backfill free_flow_duration_s and index segment key

The get_congestion_stats fallback (db/queries.py) computes
min(avg_duration_s) grouped by (route_id, from_stop_id, to_stop_id) for
any row still missing a free-flow anchor. Before this migration every
congestion read paid for that GROUP BY across the whole table; after it:

  1. free_flow_duration_s is backfilled from avg_duration_s for every row
     still NULL -- the same anchor 38a5d0f89268 used for seeded rows
     (their avg_duration_s *is* the OSRM free-driving estimate at seed
     time), just extended to organic rows written before that column
     existed. The earlier migration message called this "the existing
     min()-based value", which is actually this same assignment.
  2. A covering index on (route_id, from_stop_id, to_stop_id) is added so
     the fallback -- still reachable for any future row written without a
     free-flow anchor -- can group by that key cheaply instead of a full
     scan.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE segment_congestion_stats
        SET free_flow_duration_s = avg_duration_s
        WHERE free_flow_duration_s IS NULL
        """
    )
    op.create_index(
        'ix_segment_congestion_stats_segment',
        'segment_congestion_stats',
        ['route_id', 'from_stop_id', 'to_stop_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_segment_congestion_stats_segment', table_name='segment_congestion_stats')
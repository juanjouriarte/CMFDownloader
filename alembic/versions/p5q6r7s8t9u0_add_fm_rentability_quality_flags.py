"""add FM rentability quality flags

Revision ID: p5q6r7s8t9u0
Revises: o4p5q6r7s8t9
Branch Labels: None
Depends On: None

Creates a lightweight view over mv_rentabilidad_fm that flags implausible
returns without altering or hiding the source values.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "p5q6r7s8t9u0"
down_revision: Union[str, None] = "o4p5q6r7s8t9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_VIEW = "v_rentabilidad_fm_quality"

_SQL = """
CREATE VIEW v_rentabilidad_fm_quality AS
SELECT
    r.*,
    (
        COALESCE(ABS(r.r_1d)  > 50, false)
        OR COALESCE(ABS(r.r_1w)  > 100, false)
        OR COALESCE(ABS(r.r_1m)  > 200, false)
        OR COALESCE(ABS(r.r_1y)  > 1000, false)
        OR COALESCE(ABS(r.r_5y)  > 10000, false)
        OR COALESCE(ABS(r.r_ytd) > 1000, false)
    ) AS is_data_suspicious,
    ARRAY_REMOVE(ARRAY[
        CASE WHEN ABS(r.r_1d)  > 50    THEN 'r_1d' END,
        CASE WHEN ABS(r.r_1w)  > 100   THEN 'r_1w' END,
        CASE WHEN ABS(r.r_1m)  > 200   THEN 'r_1m' END,
        CASE WHEN ABS(r.r_1y)  > 1000  THEN 'r_1y' END,
        CASE WHEN ABS(r.r_5y)  > 10000 THEN 'r_5y' END,
        CASE WHEN ABS(r.r_ytd) > 1000  THEN 'r_ytd' END
    ], NULL) AS suspicious_periods
FROM mv_rentabilidad_fm r
"""


def upgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {_VIEW}")
    op.execute(_SQL)


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {_VIEW}")

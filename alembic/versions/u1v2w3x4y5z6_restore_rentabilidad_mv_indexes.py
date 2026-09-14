"""restore unique indexes required by concurrent MV refreshes

Revision ID: u1v2w3x4y5z6
Revises: 1f541724fcee
Create Date: 2026-09-14
"""
from typing import Sequence, Union

from alembic import op


revision: str = "u1v2w3x4y5z6"
down_revision: Union[str, None] = "1f541724fcee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Migration n3o4p5q6r7s8 rebuilds both materialized views. PostgreSQL drops
    # their indexes with the old views, so recreate the unique indexes needed
    # by REFRESH MATERIALIZED VIEW CONCURRENTLY.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_mv_rentabilidad_fm_pk
        ON mv_rentabilidad_fm (run_fondo, serie)
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_mv_rentabilidad_fi_pk
        ON mv_rentabilidad_fi (run_fondo, serie)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_mv_rentabilidad_fm_r1m
        ON mv_rentabilidad_fm (r_1m) WHERE r_1m IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_mv_rentabilidad_fm_r1y
        ON mv_rentabilidad_fm (r_1y) WHERE r_1y IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_mv_rentabilidad_fi_r1m
        ON mv_rentabilidad_fi (r_1m) WHERE r_1m IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_mv_rentabilidad_fi_r1y
        ON mv_rentabilidad_fi (r_1y) WHERE r_1y IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_mv_rentabilidad_fi_r1y")
    op.execute("DROP INDEX IF EXISTS ix_mv_rentabilidad_fi_r1m")
    op.execute("DROP INDEX IF EXISTS ix_mv_rentabilidad_fm_r1y")
    op.execute("DROP INDEX IF EXISTS ix_mv_rentabilidad_fm_r1m")
    op.execute("DROP INDEX IF EXISTS ix_mv_rentabilidad_fi_pk")
    op.execute("DROP INDEX IF EXISTS ix_mv_rentabilidad_fm_pk")

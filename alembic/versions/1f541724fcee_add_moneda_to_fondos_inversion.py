"""add moneda to fondos_inversion

Revision ID: 1f541724fcee
Revises: s8t9u0v1w2x3
Create Date: 2026-06-15 20:33:18.427121

"""
from typing import Sequence, Union

from alembic import op


revision: str = '1f541724fcee'
down_revision: Union[str, None] = 's8t9u0v1w2x3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE fondos_inversion
        ADD COLUMN IF NOT EXISTS moneda VARCHAR(10)
    """)
    # Backfill: dominant non-null moneda per fund from valores_cuota_fi.
    # NULL and '0' are data gaps — MODE() over valid values gives the right answer.
    op.execute("""
        UPDATE fondos_inversion fi
        SET moneda = (
            SELECT MODE() WITHIN GROUP (ORDER BY v.moneda)
            FROM valores_cuota_fi v
            WHERE v.run_fondo = fi.run_fondo
              AND v.moneda IS NOT NULL
              AND v.moneda != '0'
        )
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE fondos_inversion
        DROP COLUMN IF EXISTS moneda
    """)
